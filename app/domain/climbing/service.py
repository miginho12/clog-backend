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
from app.domain.climbing.schemas import CommentPreview
from app.domain.comment_likes.repository import CommentLikeRepository
from app.domain.comments.repository import CommentRepository
from app.domain.likes.repository import LikeRepository

logger = get_logger(__name__)


class ClimbingService:
    def __init__(
        self,
        session: AsyncSession,
        repository: ClimbingRepository,
        like_repo: LikeRepository,
        comment_repo: CommentRepository,
        comment_like_repo: CommentLikeRepository,
    ):
        self.session = session
        self.repo = repository
        self.like_repo = like_repo
        self.comment_repo = comment_repo
        self.comment_like_repo = comment_like_repo

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

    async def _attach_comments(
        self, logs: list[ClimbingLog], viewer_id: UUID | None
    ) -> None:
        """log 객체들에 comment_count + top_comment(좋아요 1등) 주입.

        배치: ① 게시물별 댓글수 ② 최상위 댓글 후보 ③ 후보 좋아요수
        ④ 게시물별 max 선정. (viewer_id 는 미리보기엔 미사용 — 수/내용만)
        """
        if not logs:
            return
        ids = [log.id for log in logs]
        counts = await self.comment_repo.count_by_logs(ids)
        candidates = await self.comment_repo.top_level_by_logs(ids)

        # 후보 댓글들의 좋아요 수 배치
        cand_ids = [c.id for c in candidates]
        like_counts = await self.comment_like_repo.count_by_comments(cand_ids)

        # 게시물별 최고 좋아요 댓글 선정 (동점이면 최신 created_at)
        best: dict = {}  # log_id -> (comment, like_count)
        for c in candidates:
            lc = like_counts.get(c.id, 0)
            cur = best.get(c.climbing_log_id)
            if (
                cur is None
                or lc > cur[1]
                or (lc == cur[1] and c.created_at > cur[0].created_at)
            ):
                best[c.climbing_log_id] = (c, lc)

        # 선정된 top 댓글들의 대댓글 수 배치 집계
        top_ids = [c.id for (c, _) in best.values()]
        reply_counts = await self.comment_repo.reply_counts_by_parents(top_ids)

        for log in logs:
            log.comment_count = counts.get(log.id, 0)
            top = best.get(log.id)
            if top is not None:
                comment, lc = top
                # CommentPreview 로 변환 (author 매핑은 validator 가)
                preview = CommentPreview.model_validate(comment)
                preview.like_count = lc
                preview.reply_count = reply_counts.get(comment.id, 0)
                log.top_comment = preview
            else:
                log.top_comment = None

    # ── 작성 ──

    async def create_log(self, *, user_id: UUID, data: dict) -> ClimbingLog:
        # 영상이면 트랜스코딩 파이프라인 진입:
        #   원본을 original_media_url 로 보관, media_url 은 압축 완료까지 None,
        #   media_status=processing (피드에서 숨김). 워커가 압축 후 done 전환.
        if data.get("media_type") == "video" and data.get("media_url"):
            data = {
                **data,
                "original_media_url": data["media_url"],
                "media_url": None,
                "media_status": "processing",
            }
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
        await self._attach_comments([log], viewer_id)
        return log

    # ── 피드 조회 ──

    async def list_feed(self, **kwargs) -> tuple[list[ClimbingLog], bool]:
        viewer_id = kwargs.get("viewer_id")
        logs, has_next = await self.repo.list_feed(**kwargs)
        await self._attach_likes(logs, viewer_id)
        await self._attach_comments(logs, viewer_id)
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

    # ── 삭제 (본인 또는 admin) ──

    async def delete_log(
        self, *, log_id: UUID, user_id: UUID, is_admin: bool = False
    ) -> None:
        log = await self.repo.get_by_id(log_id)
        if log is None:
            raise ClimbingLogNotFound(str(log_id))
        # 본인 글이 아니면 admin 만 삭제 가능 (신고 처리 등)
        if log.user_id != user_id and not is_admin:
            raise ClimbingLogForbidden(str(log_id))

        await self.repo.soft_delete(log)
        await self.session.commit()
        logger.info(
            "climbing_log_deleted",
            log_id=str(log_id),
            actor_id=str(user_id),
            admin_override=is_admin and log.user_id != user_id,
        )
