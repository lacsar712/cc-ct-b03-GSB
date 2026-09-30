from django.conf import settings
from django.db import transaction
from django.utils import timezone

from desk.models import OffsetSubmission, ReturnHistory


class ReturnNotAllowedError(Exception):
    """记录当前状态不允许打回（仅「已完成」可打回）。"""


def evaluate_verdict(offset_um: int) -> str:
    if abs(offset_um) <= settings.OFFSET_TOLERANCE_UM:
        return OffsetSubmission.Verdict.PASS
    return OffsetSubmission.Verdict.FAIL


def apply_verdict(submission: OffsetSubmission) -> None:
    submission.verdict = evaluate_verdict(submission.offset_um)
    submission.status = OffsetSubmission.Status.DONE
    submission.reviewed_at = timezone.now()
    submission.save(
        update_fields=["verdict", "status", "reviewed_at"],
    )


def return_for_review(
    submission: OffsetSubmission,
    reason: str,
    user,
) -> OffsetSubmission:
    """打回一条已结清记录，全部动作必须在同一事务内完成：

    - 行锁锁定记录，与 worker 的认领互斥，防止打回/再次认领撞车；
    - 清空结论（verdict、reviewed_at），状态回待复核；
    - 打回计数加一；
    - 写入一条打回履历（含原因与当时计数）。

    任一步失败整体回滚，不会出现「结论已清却无履历」或
    「履历已写却仍停在已结清」的半截状态。
    """
    with transaction.atomic():
        locked = (
            OffsetSubmission.objects.select_for_update()
            .select_related("submitted_by")
            .get(pk=submission.pk)
        )
        if locked.status != OffsetSubmission.Status.DONE:
            raise ReturnNotAllowedError("仅「已完成」记录可以打回")

        locked.verdict = ""
        locked.status = OffsetSubmission.Status.PENDING
        locked.reviewed_at = None
        locked.return_count += 1
        locked.save(
            update_fields=[
                "verdict",
                "status",
                "reviewed_at",
                "return_count",
            ]
        )
        ReturnHistory.objects.create(
            submission=locked,
            reason=reason,
            returned_by=user,
            return_count=locked.return_count,
        )
        return locked
