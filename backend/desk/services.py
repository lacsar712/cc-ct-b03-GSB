from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from desk.models import OffsetSubmission, ReturnHistory


class ReturnStateConflict(Exception):
    """记录当前状态不允许打回（尚未结清，或已被并发改回待复核）。"""


def with_row_lock(queryset, *, skip_locked: bool = False):
    """对支持的后端（PostgreSQL）加行锁；不支持的后端（SQLite 测试）原样返回。"""
    if connection.features.has_select_for_update:
        return queryset.select_for_update(
            skip_locked=skip_locked
            and connection.features.has_select_for_update_skip_locked
        )
    return queryset


def evaluate_verdict(offset_um: int) -> str:
    if abs(offset_um) <= settings.OFFSET_TOLERANCE_UM:
        return OffsetSubmission.Verdict.PASS
    return OffsetSubmission.Verdict.FAIL


def apply_verdict(submission: OffsetSubmission) -> None:
    """再次认领后重新计算结论；打回履历与计数不在此清除，保留历史。"""
    submission.verdict = evaluate_verdict(submission.offset_um)
    submission.status = OffsetSubmission.Status.DONE
    submission.reviewed_at = timezone.now()
    submission.save(
        update_fields=["verdict", "status", "reviewed_at"],
    )


def return_for_review(
    submission_id: int,
    reason: str,
    user,
) -> OffsetSubmission:
    """把已结清记录打回待复核。

    整段在一个数据库事务内完成：
      1. select_for_update 锁定该行（与 worker 认领互斥，防打回/认领撞车）；
      2. 锁内复核状态必须仍是「已结清」，否则抛 ReturnStateConflict；
      3. 清空结论与复核时间、状态回 pending、打回计数 +1；
      4. 写入一条打回履历，原因与本次次数一并落库。

    任一步失败整体回滚：不会出现结论已清却无履历，或履历已写却仍停在已结清。
    """
    clean_reason = (reason or "").strip()
    if not clean_reason:
        raise ValueError("打回原因不能为空")

    with transaction.atomic():
        submission = with_row_lock(OffsetSubmission.objects).get(pk=submission_id)

        # 锁内复查：并发打回或 worker 已认领时，这里看到的都是最新状态。
        if submission.status != OffsetSubmission.Status.DONE:
            raise ReturnStateConflict(
                "该记录不在「已结清」状态，不能打回（可能已被打回或正在复核）"
            )

        new_count = submission.return_count + 1
        submission.verdict = ""
        submission.status = OffsetSubmission.Status.PENDING
        submission.reviewed_at = None
        submission.return_count = new_count
        submission.save(
            update_fields=[
                "verdict",
                "status",
                "reviewed_at",
                "return_count",
            ]
        )

        # 履历写入失败会令整个 atomic 回滚，上面的清结论/回队一并撤销。
        ReturnHistory.objects.create(
            submission=submission,
            reason=clean_reason,
            return_count=new_count,
            returned_by=user if (user and user.pk) else None,
        )

    return submission
