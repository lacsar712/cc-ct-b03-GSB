from datetime import datetime
from typing import Optional

from django.http import HttpRequest
from ninja import NinjaAPI, Schema
from ninja.errors import HttpError

from desk.auth_utils import bearer_auth, create_access_token, verify_password
from desk.models import OffsetSubmission, ReturnHistory, User
from desk.services import ReturnStateConflict, return_for_review

api = NinjaAPI(title="数控刀补复核台", version="1.1")


class HealthOut(Schema):
    status: str


class LoginIn(Schema):
    username: str
    password: str


class LoginOut(Schema):
    token: str
    username: str
    role: str
    can_write: bool
    can_review: bool


class SubmissionIn(Schema):
    tool_code: str
    offset_um: int


class ReturnIn(Schema):
    reason: str


class SubmissionOut(Schema):
    id: int
    tool_code: str
    offset_um: int
    status: str
    verdict: str
    return_count: int
    created_at: datetime
    reviewed_at: Optional[datetime]


class ReturnHistoryOut(Schema):
    id: int
    submission_id: int
    tool_code: str
    offset_um: int
    reason: str
    return_count: int
    returned_by: str
    current_status: str
    created_at: datetime


def _to_out(row: OffsetSubmission) -> SubmissionOut:
    return SubmissionOut(
        id=row.id,
        tool_code=row.tool_code,
        offset_um=row.offset_um,
        status=row.status,
        verdict=row.verdict or "",
        return_count=row.return_count,
        created_at=row.created_at,
        reviewed_at=row.reviewed_at,
    )


def _history_to_out(row: ReturnHistory) -> ReturnHistoryOut:
    returned_by = row.returned_by.username if row.returned_by_id else ""
    submission = row.submission
    return ReturnHistoryOut(
        id=row.id,
        submission_id=row.submission_id,
        tool_code=submission.tool_code,
        offset_um=submission.offset_um,
        reason=row.reason,
        return_count=row.return_count,
        returned_by=returned_by,
        current_status=submission.status,
        created_at=row.created_at,
    )


@api.get("/health", response=HealthOut)
def health(request: HttpRequest):
    return {"status": "ok"}


@api.post("/auth/login", response=LoginOut)
def login(request: HttpRequest, body: LoginIn):
    try:
        user = User.objects.get(username=body.username)
    except User.DoesNotExist:
        raise HttpError(401, "用户名或密码错误")
    if not verify_password(body.password, user.password):
        raise HttpError(401, "用户名或密码错误")
    token = create_access_token(user)
    return {
        "token": token,
        "username": user.username,
        "role": user.role,
        "can_write": user.can_write,
        "can_review": user.can_review,
    }


@api.get("/submissions", response=list[SubmissionOut], auth=bearer_auth)
def list_submissions(request: HttpRequest):
    rows = OffsetSubmission.objects.all()[:200]
    return [_to_out(r) for r in rows]


@api.get("/submissions/{submission_id}", response=SubmissionOut, auth=bearer_auth)
def get_submission(request: HttpRequest, submission_id: int):
    try:
        row = OffsetSubmission.objects.get(pk=submission_id)
    except OffsetSubmission.DoesNotExist:
        raise HttpError(404, "刀补记录不存在")
    return _to_out(row)


@api.post("/submissions", response=SubmissionOut, auth=bearer_auth)
def create_submission(request: HttpRequest, body: SubmissionIn):
    user: User = request.auth
    if not user.can_write:
        raise HttpError(403, "当前账号只读，不能提交刀补")
    tool_code = body.tool_code.strip()
    if not tool_code:
        raise HttpError(400, "刀具编号不能为空")
    row = OffsetSubmission.objects.create(
        tool_code=tool_code,
        offset_um=body.offset_um,
        submitted_by=user,
        status=OffsetSubmission.Status.PENDING,
    )
    return _to_out(row)


@api.post(
    "/submissions/{submission_id}/return",
    response=SubmissionOut,
    auth=bearer_auth,
)
def return_submission(request: HttpRequest, submission_id: int, body: ReturnIn):
    user: User = request.auth
    # 仅复核员可发起打回；操作员显式拒绝。
    if not user.can_review:
        raise HttpError(403, "仅复核员可发起打回")
    reason = (body.reason or "").strip()
    if not reason:
        raise HttpError(400, "打回原因不能为空")
    try:
        row = return_for_review(submission_id, reason, user)
    except OffsetSubmission.DoesNotExist:
        raise HttpError(404, "刀补记录不存在")
    except ReturnStateConflict as exc:
        # 与再次认领撞车、或已被打回过：当前已不是「已结清」。
        raise HttpError(409, str(exc))
    except ValueError as exc:
        raise HttpError(400, str(exc))
    return _to_out(row)


@api.get(
    "/submissions/{submission_id}/history",
    response=list[ReturnHistoryOut],
    auth=bearer_auth,
)
def submission_history(request: HttpRequest, submission_id: int):
    if not OffsetSubmission.objects.filter(pk=submission_id).exists():
        raise HttpError(404, "刀补记录不存在")
    rows = (
        ReturnHistory.objects.select_related("submission", "returned_by")
        .filter(submission_id=submission_id)
        .order_by("-created_at", "-id")
    )
    return [_history_to_out(r) for r in rows]


@api.get("/return-history", response=list[ReturnHistoryOut], auth=bearer_auth)
def list_return_history(request: HttpRequest):
    """打回履历流：侧栏「打回记录」展示，含每条记录的当前状态。"""
    rows = (
        ReturnHistory.objects.select_related("submission", "returned_by")
        .all()[:200]
    )
    return [_history_to_out(r) for r in rows]
