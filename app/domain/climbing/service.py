"""Climbing Service.

비즈니스 로직:
- 작성 (create)
- 단건 조회 (공개 범위 권한 검사)
- 피드 조회 (필터)
- 수정/삭제 (본인만)
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.domain.climbing.exceptions import (
    ClimbingLogForbidden,
    ClimbingLogNotFound,
)
from app.domain.climbing.models import ClimbingLog
from app.domain.climbing.repository import ClimbingRepository
from app.domain.likes.repository import LikeRepository

logger = get_logger(__name__)


class ClimbingService:
    def __init__(
        self,
        session: AsyncSession,
        repository: ClimbingRepository,
        like_repo: LikeRepository,
    ):
        self.session = session
        self.repo = repository
        self.like_repo = like_repo

    async def _attach_likes(
        self, logs: list[ClimbingLog], viewer_id: UUID | None
    ) -> None:
        """log 객체들에 like_count/liked_by_me 를 동적 주입 (배치, N+1 방지)."""
        if not logs:
            return
        ids = [log.id for log in logs]
        counts = await self.like_repo.count_by_logs(ids)
        liked = (
            await self.like_repo.liked_log_ids(user_id=viewer_id, log_ids=ids)
            if viewer_id is not None
            else set()
        )
        for log in logs:
            log.like_count = counts.get(log.id, 0)
            log.liked_by_me = log.id in liked

    # ── 작성 ──

    async def create_log(self, *, user_id: UUID, data: dict) -> ClimbingLog:
        log = await self.repo.create(user_id=user_id, **data)
        await self.session.commit()
        # author 응답 매핑을 위해 user 관계까지 eager load (lazy=raise 대응)
        await self.session.refresh(log, ["user"])
        # 방금 생성 → 좋아요 0, 본인이 누른 것도 없음
        log.like_count = 0
        log.liked_by_me = False
        logger.info(
            "climbing_log_created", log_id=str(log.id), user_id=str(user_id)
        )
        return log

    # ── 단건 조회 (공개 범위 권한) ──

    async def get_log(
        self, *, log_id: UUID, viewer_id: UUID | None
    ) -> ClimbingLog:
        """단건 조회.

        공개 범위:
        - public: 누구나
        - private: 본인만

        권한 없으면 NotFound 로 응답 (존재 여부 숨김).
        """
        log = await self.repo.get_by_id(log_id)
        if log is None:
            raise ClimbingLogNotFound(str(log_id))

        if log.visibility == "private" and log.user_id != viewer_id:
            # 비공개 + 본인 아님 → 존재 자체를 숨김 (NotFound)
            raise ClimbingLogNotFound(str(log_id))

        await self._attach_likes([log], viewer_id)
        return log

    # ── 피드 조회 ──

    async def list_feed(self, **kwargs) -> tuple[list[ClimbingLog], bool]:
        viewer_id = kwargs.get("viewer_id")
        logs, has_next = await self.repo.list_feed(**kwargs)
        await self._attach_likes(logs, viewer_id)
        return logs, has_next

    # ── 수정 (본인만) ──

    async def update_log(
        self, *, log_id: UUID, user_id: UUID, data: dict
    ) -> ClimbingLog:
        log = await self.repo.get_by_id(log_id)
        if log is None:
            raise ClimbingLogNotFound(str(log_id))
        if log.user_id != user_id:
            raise ClimbingLogForbidden(str(log_id))

        # 라우터에서 exclude_unset=True 로 dump 하므로 data 에는
        # "프론트가 명시적으로 보낸 필드"만 존재. null 도 의도된 값
        # (예: 미디어 제거) 이므로 None 필터링하지 않고 그대로 반영.
        log = await self.repo.update(log, **data)
        await self.session.commit()
        await self.session.refresh(log, ["user"])
        await self._attach_likes([log], user_id)
        logger.info("climbing_log_updated", log_id=str(log_id))
        return log

    # ── 삭제 (본인만) ──

    async def delete_log(self, *, log_id: UUID, user_id: UUID) -> None:
        log = await self.repo.get_by_id(log_id)
        if log is None:
            raise ClimbingLogNotFound(str(log_id))
        if log.user_id != user_id:
            raise ClimbingLogForbidden(str(log_id))

        await self.repo.soft_delete(log)
        await self.session.commit()
        logger.info("climbing_log_deleted", log_id=str(log_id))
