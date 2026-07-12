"""Climbing Repository.

CRUD + 피드 조회 (공개 범위 / 카테고리 / 짐 필터, 페이지네이션).
users/repository.py 패턴 동일.
"""

from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.climbing.models import ClimbingLog
from app.domain.users.models import User


class ClimbingRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── 단건 조회 ──

    async def get_by_id(self, log_id: UUID) -> ClimbingLog | None:
        result = await self.session.execute(
            select(ClimbingLog)
            .join(User, ClimbingLog.user_id == User.id)
            .options(selectinload(ClimbingLog.user))
            .where(
                ClimbingLog.id == log_id,
                ClimbingLog.deleted_at.is_(None),
                User.deleted_at.is_(None),  # 탈퇴 작성자 글 숨김
            )
        )
        return result.scalar_one_or_none()

    # ── 생성 ──

    async def create(self, *, user_id: UUID, **fields) -> ClimbingLog:
        # climbed_at 이 None 이면 컬럼에서 제거 → DB default(오늘) 적용
        if fields.get("climbed_at") is None:
            fields.pop("climbed_at", None)
        log = ClimbingLog(user_id=user_id, **fields)
        self.session.add(log)
        await self.session.flush()
        return log

    # ── 수정 ──

    async def update(self, log: ClimbingLog, **fields) -> ClimbingLog:
        for key, value in fields.items():
            setattr(log, key, value)
        await self.session.flush()
        return log

    # ── 삭제 (soft) ──

    async def soft_delete(self, log: ClimbingLog) -> None:
        from sqlalchemy import func

        log.deleted_at = func.now()
        await self.session.flush()

    # ── 피드 조회 ──

    async def list_feed(
        self,
        *,
        viewer_id: UUID | None,
        author_id: UUID | None = None,
        category: str | None = None,
        gym_name: str | None = None,
        grade_system: str | None = None,
        only_success: bool | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ClimbingLog], bool]:
        """피드 조회.

        공개 범위 규칙:
        - 비로그인(viewer_id=None): public 만
        - 로그인: public 전체 + 본인의 private
        (friends 는 친구 시스템 도입 후 확장)

        필터: author_id, category(태그 포함), gym_name, grade_system, only_success

        반환: (items, has_next)
        """
        stmt = (
            select(ClimbingLog)
            .join(User, ClimbingLog.user_id == User.id)
            .options(selectinload(ClimbingLog.user))
            .where(
                ClimbingLog.deleted_at.is_(None),
                User.deleted_at.is_(None),  # 탈퇴 작성자 글 숨김
                # 미디어 처리 완료(done) 또는 미디어없음/이미지(null)만 노출.
                # processing/failed 는 피드·프로필에서 숨김 (압축 완료 후 등장)
                or_(
                    ClimbingLog.media_status.is_(None),
                    ClimbingLog.media_status == "done",
                ),
            )
        )

        # 공개 범위
        # 글이 보이는 조건: (글이 public AND 작성자가 공개계정) OR 본인 글
        # 비공개 계정(User.is_public=False)의 글은 본인에게만 노출.
        # (승인 팔로워 공개는 팔로우 승인제 도입 후 확장 — Day 24 후속 과제)
        public_and_visible = and_(
            ClimbingLog.visibility == "public",
            User.is_public.is_(True),
        )
        if viewer_id is None:
            stmt = stmt.where(public_and_visible)
        else:
            stmt = stmt.where(
                or_(
                    public_and_visible,
                    ClimbingLog.user_id == viewer_id,
                )
            )

        # 필터
        if author_id is not None:
            stmt = stmt.where(ClimbingLog.user_id == author_id)
        if category is not None:
            # 배열에 해당 태그 포함 (@> 연산자)
            stmt = stmt.where(ClimbingLog.categories.contains([category]))
        if gym_name is not None:
            stmt = stmt.where(ClimbingLog.gym_name == gym_name)
        if grade_system is not None:
            stmt = stmt.where(ClimbingLog.grade_system == grade_system)
        if only_success is not None:
            stmt = stmt.where(ClimbingLog.is_success.is_(only_success))

        # 정렬: 최신 클라이밍 날짜 → 생성순
        stmt = stmt.order_by(
            ClimbingLog.climbed_at.desc(), ClimbingLog.created_at.desc()
        )

        # 페이지네이션 (has_next 판별 위해 +1 조회)
        offset = (page - 1) * page_size
        stmt = stmt.offset(offset).limit(page_size + 1)

        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())

        has_next = len(rows) > page_size
        return rows[:page_size], has_next
