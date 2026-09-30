from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from ninja.testing import TestClient

from desk.api import api
from desk.auth_utils import create_access_token, hash_password
from desk.models import OffsetSubmission, ReturnHistory, User
from desk.services import ReturnStateConflict, return_for_review
from desk.worker import claim_one_pending


@override_settings(DEBUG=False)
class ReturnWorkflowTests(TestCase):
    def setUp(self):
        self.machinist = User.objects.create(
            username="machinist",
            role=User.Role.MACHINIST,
            password=hash_password("machine123456"),
        )
        self.auditor = User.objects.create(
            username="auditor",
            role=User.Role.AUDITOR,
            password=hash_password("audit123456"),
        )
        now = timezone.now()
        # 种子里那条合格行 T01（5µm → 合格）
        self.t01 = OffsetSubmission.objects.create(
            tool_code="T01",
            offset_um=5,
            status=OffsetSubmission.Status.DONE,
            verdict=OffsetSubmission.Verdict.PASS,
            submitted_by=self.machinist,
            reviewed_at=now,
        )
        # 超差行 T09
        self.t09 = OffsetSubmission.objects.create(
            tool_code="T09",
            offset_um=20,
            status=OffsetSubmission.Status.DONE,
            verdict=OffsetSubmission.Verdict.FAIL,
            submitted_by=self.machinist,
            reviewed_at=now,
        )
        # 一条仍在待复核的行
        self.pending = OffsetSubmission.objects.create(
            tool_code="T02",
            offset_um=3,
            status=OffsetSubmission.Status.PENDING,
        )
        self.client = TestClient(api)

    def auth(self, user):
        token = create_access_token(user)
        return {"headers": {"Authorization": f"Bearer {token}"}}

    # ---- 权限 ----

    def test_machinist_cannot_return(self):
        """操作员不能发起打回。"""
        resp = self.client.post(
            f"/submissions/{self.t01.id}/return",
            json={"reason": "怀疑量具有误"},
            **self.auth(self.machinist),
        )
        self.assertEqual(resp.status_code, 403)
        self.t01.refresh_from_db()
        self.assertEqual(self.t01.status, OffsetSubmission.Status.DONE)
        self.assertEqual(self.t01.verdict, OffsetSubmission.Verdict.PASS)
        self.assertEqual(self.t01.return_count, 0)
        self.assertEqual(ReturnHistory.objects.count(), 0)

    def test_anonymous_cannot_return(self):
        resp = self.client.post(
            f"/submissions/{self.t01.id}/return",
            json={"reason": "x"},
        )
        self.assertEqual(resp.status_code, 401)

    # ---- 参数 / 状态校验 ----

    def test_reason_required(self):
        resp = self.client.post(
            f"/submissions/{self.t01.id}/return",
            json={"reason": "   "},
            **self.auth(self.auditor),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(ReturnHistory.objects.count(), 0)

    def test_only_done_can_be_returned(self):
        """待复核/复核中的记录不能打回 → 409。"""
        resp = self.client.post(
            f"/submissions/{self.pending.id}/return",
            json={"reason": "不应能打回"},
            **self.auth(self.auditor),
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(ReturnHistory.objects.count(), 0)

    def test_return_missing_returns_404(self):
        resp = self.client.post(
            "/submissions/99999/return",
            json={"reason": "x"},
            **self.auth(self.auditor),
        )
        self.assertEqual(resp.status_code, 404)

    # ---- 主流程：原子打回 ----

    def test_auditor_return_clears_verdict_queues_counts_and_logs(self):
        resp = self.client.post(
            f"/submissions/{self.t01.id}/return",
            json={"reason": "对刀基准疑似错误，请重测"},
            **self.auth(self.auditor),
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        # 单子回待复核、结论清空、计数加一
        self.assertEqual(body["status"], "pending")
        self.assertEqual(body["verdict"], "")
        self.assertEqual(body["return_count"], 1)

        self.t01.refresh_from_db()
        self.assertEqual(self.t01.status, OffsetSubmission.Status.PENDING)
        self.assertEqual(self.t01.verdict, "")
        self.assertIsNone(self.t01.reviewed_at)
        self.assertEqual(self.t01.return_count, 1)

        # 原因与次数写入履历
        hist = ReturnHistory.objects.get()
        self.assertEqual(hist.submission_id, self.t01.id)
        self.assertEqual(hist.reason, "对刀基准疑似错误，请重测")
        self.assertEqual(hist.return_count, 1)
        self.assertEqual(hist.returned_by_id, self.auditor.id)

    # ---- 事务原子性：零半截 ----

    def test_history_write_failure_rolls_back_entire_return(self):
        """履历写入失败时，清结论/回队/计数必须一并回滚，不得留半截。"""
        with patch.object(
            ReturnHistory.objects, "create", side_effect=RuntimeError("history db down")
        ):
            with self.assertRaises(RuntimeError):
                return_for_review(self.t01.id, "原因", self.auditor)

        self.t01.refresh_from_db()
        # 仍是已结清、原结论保留、计数未增
        self.assertEqual(self.t01.status, OffsetSubmission.Status.DONE)
        self.assertEqual(self.t01.verdict, OffsetSubmission.Verdict.PASS)
        self.assertIsNotNone(self.t01.reviewed_at)
        self.assertEqual(self.t01.return_count, 0)
        self.assertEqual(ReturnHistory.objects.count(), 0)

    def test_clear_step_failure_leaves_no_history(self):
        """若清结论那步自身报错，也不得已写履历（履历在清结论之后写）。"""
        with patch.object(
            OffsetSubmission, "save", side_effect=RuntimeError("row save failed")
        ):
            with self.assertRaises(RuntimeError):
                return_for_review(self.t01.id, "原因", self.auditor)
        self.assertEqual(ReturnHistory.objects.count(), 0)
        self.t01.refresh_from_db()
        self.assertEqual(self.t01.return_count, 0)
        self.assertEqual(self.t01.status, OffsetSubmission.Status.DONE)

    # ---- 撞车：已回待复核不得残留旧结论，不可重复打回 ----

    def test_double_return_conflict_and_no_stale_verdict(self):
        """打回与再次认领撞车：已回待复核后再打回应 409，且不残留旧结论。"""
        return_for_review(self.t01.id, "第一次打回", self.auditor)
        self.t01.refresh_from_db()
        self.assertEqual(self.t01.status, OffsetSubmission.Status.PENDING)
        self.assertEqual(self.t01.verdict, "")

        with self.assertRaises(ReturnStateConflict):
            return_for_review(self.t01.id, "重复打回", self.auditor)

        self.t01.refresh_from_db()
        self.assertEqual(self.t01.status, OffsetSubmission.Status.PENDING)
        self.assertEqual(self.t01.verdict, "")  # 旧结论没有被写回
        self.assertEqual(self.t01.return_count, 1)  # 计数不重复加
        self.assertEqual(ReturnHistory.objects.count(), 1)

    def test_return_while_processing_conflicts(self):
        self.t01.status = OffsetSubmission.Status.PROCESSING
        self.t01.save(update_fields=["status"])
        with self.assertRaises(ReturnStateConflict):
            return_for_review(self.t01.id, "撞车打回", self.auditor)

    # ---- 再次认领：重新算结论，履历与次数保留 ----

    def test_reclaim_recomputes_verdict_but_keeps_history(self):
        """种子合格行打回 → worker 再次认领结清 → 历史仍能看见原因与次数。"""
        # 打回后待复核队列里还有 T02 在前（created_at 更早的优先）
        return_for_review(self.t01.id, "对刀基准疑似错误，请重测", self.auditor)

        # worker 按 created_at 顺序认领；先消费更早的 pending，直到处理 T01
        processed_ids = set()
        for _ in range(5):
            if not claim_one_pending():
                break
            # claim_one_pending 每次处理最早的一条 pending
        for sid in OffsetSubmission.objects.filter(
            status=OffsetSubmission.Status.DONE
        ).values_list("id", flat=True):
            processed_ids.add(sid)

        self.t01.refresh_from_db()
        self.assertEqual(self.t01.status, OffsetSubmission.Status.DONE)
        # 再次认领重新算结论：5µm 仍判合格
        self.assertEqual(self.t01.verdict, OffsetSubmission.Verdict.PASS)
        self.assertIsNotNone(self.t01.reviewed_at)
        # 次数与履历不被结清动作抹掉
        self.assertEqual(self.t01.return_count, 1)
        hist = ReturnHistory.objects.get(submission_id=self.t01.id)
        self.assertEqual(hist.reason, "对刀基准疑似错误，请重测")
        self.assertEqual(hist.return_count, 1)

        # 履历接口在再次结清后仍可查到原因与次数
        resp = self.client.get(
            f"/submissions/{self.t01.id}/history", **self.auth(self.auditor)
        )
        self.assertEqual(resp.status_code, 200)
        items = resp.json()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["reason"], "对刀基准疑似错误，请重测")
        self.assertEqual(items[0]["return_count"], 1)
        self.assertEqual(items[0]["current_status"], "done")

        # 汇总履历流同样可见
        resp = self.client.get("/return-history", **self.auth(self.auditor))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 1)

    def test_return_count_accumulates_across_rounds(self):
        """二次打回（中间重新结清）计数累加到 2，履历两行。"""
        return_for_review(self.t09.id, "第一次", self.auditor)
        self.t09.refresh_from_db()
        # worker 重新认领结清
        claim_one_pending()
        self.t09.refresh_from_db()
        self.assertEqual(self.t09.status, OffsetSubmission.Status.DONE)
        self.assertEqual(self.t09.verdict, OffsetSubmission.Verdict.FAIL)
        self.assertEqual(self.t09.return_count, 1)

        return_for_review(self.t09.id, "第二次", self.auditor)
        claim_one_pending()
        self.t09.refresh_from_db()
        self.assertEqual(self.t09.status, OffsetSubmission.Status.DONE)
        self.assertEqual(self.t09.return_count, 2)
        counts = list(
            ReturnHistory.objects.filter(submission_id=self.t09.id)
            .order_by("return_count")
            .values_list("return_count", "reason")
        )
        self.assertEqual(counts, [(1, "第一次"), (2, "第二次")])

    # ---- 登录返回权限位 ----

    def test_login_exposes_review_flag(self):
        resp = self.client.post(
            "/auth/login",
            json={"username": "auditor", "password": "audit123456"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["can_review"])
        self.assertFalse(body["can_write"])
