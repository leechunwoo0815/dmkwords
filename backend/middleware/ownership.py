# backend/middleware/ownership.py
"""
[What] 声明式归属校验层
[Why] 消除 router 中 43 处手动 child.user_id != current_user.id 重复校验
[How] 提供 callable class 依赖工厂，FastAPI Depends 自动注入

使用方式：
    @router.get("/{child_id}")
    def get_detail(child: Child = Depends(GetOwnedChild())):
        ...

    @router.post("/enroll")
    def enroll(data: ..., child: Child = Depends(GetOwnedChildFromBody())):
        ...

    @router.put("/{enrollment_id}/cancel")
    def cancel(enrollment: ... = Depends(GetOwnedEnrollment())):
        ...
"""

import logging

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from backend.common.exceptions import BadRequestError, ForbiddenError, NotFoundError
from backend.database import get_db
from backend.domain.user.schemas import UserResponse
from backend.middleware.auth import get_current_user

logger = logging.getLogger(__name__)


def verify_child_ownership(
    child_id: int,
    current_user: UserResponse,
    db: Session,
):
    """底层校验函数：校验 child 属于当前用户，返回 Child 或 raise 403"""
    from backend.domain.child.models import Child

    child = (
        db.query(Child)
        .filter(
            Child.id == child_id,
            Child.is_deleted == 0,
        )
        .first()
    )
    if not child:
        raise NotFoundError("孩子不存在")
    if child.user_id != current_user.id:
        logger.warning(
            "Ownership violation: child_id=%d, user_id=%d, owner_id=%d",
            child_id,
            current_user.id,
            child.user_id,
        )
        raise ForbiddenError("无权操作该孩子")
    return child


class GetOwnedChild:
    """从路径参数获取 child_id 并校验归属

    使用：Depends(GetOwnedChild())  — 默认从路径参数 child_id 取值
         Depends(GetOwnedChild(param_name="id"))  — 自定义参数名
    """

    def __init__(self, param_name: str = "child_id"):
        self.param_name = param_name

    async def __call__(
        self,
        request: Request,
        current_user: UserResponse = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        child_id = request.path_params.get(self.param_name)
        if child_id is None:
            raise BadRequestError(f"缺少路径参数 {self.param_name}")
        return verify_child_ownership(int(child_id), current_user, db)


class GetOwnedChildFromBody:
    """从请求体获取 child_id 并校验归属

    使用：Depends(GetOwnedChildFromBody())  — 默认从 body.child_id 取值
         Depends(GetOwnedChildFromBody(field_name="source_child_id"))
    """

    def __init__(self, field_name: str = "child_id"):
        self.field_name = field_name

    async def __call__(
        self,
        request: Request,
        current_user: UserResponse = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        body = await request.json()
        child_id = body.get(self.field_name)
        if child_id is None:
            raise BadRequestError(f"请求体缺少 {self.field_name}")
        return verify_child_ownership(int(child_id), current_user, db)


class GetOwnedChildFromQuery:
    """从 query param 获取 child_id 并校验归属

    使用：Depends(GetOwnedChildFromQuery())
    """

    def __init__(self, param_name: str = "child_id"):
        self.param_name = param_name

    async def __call__(
        self,
        request: Request,
        current_user: UserResponse = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        child_id = request.query_params.get(self.param_name)
        if child_id is None:
            raise BadRequestError(f"缺少查询参数 {self.param_name}")
        return verify_child_ownership(int(child_id), current_user, db)


class GetOwnedEnrollment:
    """通过 enrollment_id 反查 child 归属

    使用：Depends(GetOwnedEnrollment())
    """

    async def __call__(
        self,
        request: Request,
        current_user: UserResponse = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        enrollment_id = request.path_params.get("enrollment_id")
        if enrollment_id is None:
            raise BadRequestError("缺少路径参数 enrollment_id")
        from backend.domain.activity.models import ActivityEnrollment

        enrollment = (
            db.query(ActivityEnrollment)
            .filter(
                ActivityEnrollment.id == int(enrollment_id),
                ActivityEnrollment.is_deleted == 0,
            )
            .first()
        )
        if not enrollment:
            raise NotFoundError("报名记录不存在")
        return verify_child_ownership(enrollment.child_id, current_user, db), enrollment


class GetOwnedSession:
    """通过 session_id 反查 child 归属

    使用：Depends(GetOwnedSession())
    """

    async def __call__(
        self,
        request: Request,
        current_user: UserResponse = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        session_id = request.path_params.get("session_id")
        if session_id is None:
            raise BadRequestError("缺少路径参数 session_id")
        from backend.domain.reading.models import ReadingSession

        session = (
            db.query(ReadingSession)
            .filter(
                ReadingSession.id == int(session_id),
                ReadingSession.is_deleted == 0,
            )
            .first()
        )
        if not session:
            raise NotFoundError("阅读会话不存在")
        return verify_child_ownership(session.child_id, current_user, db), session


class GetOwnedQuiz:
    """通过 quiz_id 反查 child 归属

    使用：Depends(GetOwnedQuiz())
    """

    async def __call__(
        self,
        request: Request,
        current_user: UserResponse = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        quiz_id = request.path_params.get("quiz_id")
        if quiz_id is None:
            raise BadRequestError("缺少路径参数 quiz_id")
        from backend.domain.advancement.models import Quiz

        quiz = (
            db.query(Quiz)
            .filter(
                Quiz.id == int(quiz_id),
                Quiz.is_deleted == 0,
            )
            .first()
        )
        if not quiz:
            raise NotFoundError("测验不存在")
        return verify_child_ownership(quiz.child_id, current_user, db), quiz


class GetOwnedOrder:
    """通过 order_id 归属校验

    使用：Depends(GetOwnedOrder())
    返回 (current_user, order) 元组
    """

    async def __call__(
        self,
        request: Request,
        current_user: UserResponse = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        order_id = request.path_params.get("order_id")
        if order_id is None:
            raise BadRequestError("缺少路径参数 order_id")
        from backend.domain.order.models import Order

        order = db.query(Order).filter(Order.id == int(order_id), Order.is_deleted == 0).first()
        if not order:
            raise NotFoundError("订单不存在")
        if order.user_id != current_user.id:
            logger.warning(
                "Ownership violation: order_id=%d, user_id=%d, owner_id=%d",
                order_id,
                current_user.id,
                order.user_id,
            )
            raise ForbiddenError("无权操作该订单")
        return current_user, order


class GetOwnedRefund:
    """通过 refund_id 归属校验

    使用：Depends(GetOwnedRefund())
    返回 (current_user, refund) 元组
    """

    async def __call__(
        self,
        request: Request,
        current_user: UserResponse = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        refund_id = request.path_params.get("refund_id")
        if refund_id is None:
            raise BadRequestError("缺少路径参数 refund_id")
        from backend.domain.refund.models import RefundApplication

        refund = (
            db.query(RefundApplication)
            .filter(
                RefundApplication.id == int(refund_id),
                RefundApplication.is_deleted == 0,
            )
            .first()
        )
        if not refund:
            raise NotFoundError("退款记录不存在")
        if refund.user_id != current_user.id:
            logger.warning(
                "Ownership violation: refund_id=%d, user_id=%d, owner_id=%d",
                refund_id,
                current_user.id,
                refund.user_id,
            )
            raise ForbiddenError("无权查看该退款记录")
        return current_user, refund


class GetOwnedVocab:
    """通过 vocab_id 反查 child 归属

    使用：Depends(GetOwnedVocab())
    """

    async def __call__(
        self,
        request: Request,
        current_user: UserResponse = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        vocab_id = request.path_params.get("vocab_id")
        if vocab_id is None:
            raise BadRequestError("缺少路径参数 vocab_id")
        from backend.domain.vocabulary.models import UserVocabulary

        vocab = (
            db.query(UserVocabulary)
            .filter(
                UserVocabulary.id == int(vocab_id),
                UserVocabulary.is_deleted == 0,
            )
            .first()
        )
        if not vocab:
            raise NotFoundError("生词不存在")
        return verify_child_ownership(vocab.child_id, current_user, db), vocab
