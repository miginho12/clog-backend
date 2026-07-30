"""Climbing 도메인 모델.

클라이밍 기록(ClimbingLog) — 한 문제(루트)에 대한 기록.

설계 근거:
- ADR-022 (그레이드 짐별 명시): grade_raw + grade_system + gym_name 분해 저장
  → 사용자가 본 그대로("V3", "보라") 표시하되, V2 calibration 을 위해
    표기 체계와 짐 정보를 분리. 데이터 누적 시 정규화 그레이드 산출 가능.
- 카테고리 다중 태그: PostgreSQL text[] 배열. 한 문제에 여러 유형 중첩 허용
  (예: [오버행, 다이나믹, 파워]). && / @> 연산자로 필터링.
- 공개 범위: public(누구나) / private(본인만). friends 는 친구 시스템 후 추가.
- soft delete (deleted_at), created_at/updated_at 자동 관리.

Spring/JPA 비교: User 모델과 동일 패턴.
"""

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Literal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.users.models import User
from app.infra.db.base import Base

if TYPE_CHECKING:
    from app.domain.climbing.schemas import CommentPreview


class ClimbingLog(Base):
    """클라이밍 기록 (한 문제 단위)."""

    __tablename__ = "climbing_logs"
    # 아래 like_count 등은 DB 컬럼이 아니라 ClimbingService._attach_likes/
    # _attach_comments 가 응답 조립 시 동적으로 주입하는 뷰 전용 필드라,
    # Mapped[] 매핑 없이 타입만 선언한다.
    __allow_unmapped__ = True
    __table_args__ = (
        # ADR-038: categories text[] 태그 검색용 GIN 인덱스
        Index(
            "ix_climbing_logs_categories",
            "categories",
            postgresql_using="gin",
        ),
    )

    # ── 식별자 ──
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    # ── 작성자 (FK) ──
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="작성자 user id",
    )

    # 작성자 relationship (피드 author 표시용 — selectinload 로 eager load)
    user: Mapped[User] = relationship(lazy="raise")

    # ── 그레이드 (ADR-022: 짐별 명시) ──
    grade_raw: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="사용자가 본 그대로의 등급 (V3, 보라 등)",
    )
    grade_system: Mapped[Literal["v_scale", "color"]] = mapped_column(
        String(20),
        nullable=False,
        default="v_scale",
        comment="등급 표기 체계 (v_scale | color)",
    )
    gym_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment="짐(암장) 이름 — 짐별 난이도 정규화 기반",
    )

    # ── 카테고리 (다중 태그) ──
    categories: Mapped[list[str]] = mapped_column(
        ARRAY(String(30)),
        nullable=False,
        server_default=text("'{}'::varchar[]"),
        comment="문제 유형 다중 태그 (다이나믹, 슬랩 등)",
    )

    # ── 기록 내용 ──
    comment: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="코멘트 / 베타 메모",
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
        comment="시도 횟수",
    )
    is_success: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="완등(send) 성공 여부",
    )
    climbed_at: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        server_default=func.current_date(),
        comment="클라이밍 날짜 (기본 오늘)",
    )

    # ── 미디어 (이번엔 URL 만, 업로드는 추후) ──
    media_type: Mapped[Literal["video", "image"] | None] = mapped_column(
        String(10),
        nullable=True,
        comment="미디어 유형 (video | image)",
    )
    media_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="미디어 URL (영상은 압축 완료 후 압축본, 이미지는 원본)",
    )
    # ── 미디어 처리 상태 (비동기 트랜스코딩 파이프라인) ──
    # null: 미디어 없음 or 이미지(압축 불필요, 즉시 공개)
    # processing: 원본 업로드됨, 압축 대기/진행 중 (피드에서 숨김)
    # done: 압축 완료, media_url=압축본 (공개)
    # failed: 압축 실패 (숨김 유지)
    media_status: Mapped[Literal["processing", "done", "failed"] | None] = (
        mapped_column(
            String(20),
            nullable=True,
            index=True,
            comment="미디어 처리 상태 (processing | done | failed, null=이미지/없음)",
        )
    )
    # 원본 영상 URL (압축 소스 — 압축 완료 후 정리 대상)
    original_media_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="원본 영상 URL (트랜스코딩 소스, 압축 후 삭제)",
    )
    # 영상 정지 썸네일 (ffmpeg thumbnail 필터로 압축 시 함께 생성).
    # 그리드 등에서 <video> 태그 없이 <img> 로만 보여주기 위함 — Android
    # WebView 가 src 없는/미재생 <video> 를 깨진 미디어 아이콘으로 렌더링하는
    # 문제를 근본적으로 우회 (2026-07-30). null 이면 image 게시물이거나
    # 구버전 영상(백필 전).
    thumbnail_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="영상 정지 썸네일 URL (video 전용, null=image 또는 백필 전)",
    )

    # ── 공개 범위 ──
    visibility: Mapped[Literal["public", "private"]] = mapped_column(
        String(10),
        nullable=False,
        default="public",
        server_default=text("'public'"),
        index=True,
        comment="공개 범위 (public | private)",
    )

    # ── 메타 ──
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ── 뷰 전용 동적 필드 (DB 컬럼 아님, ClimbingService 가 응답 조립 시 주입) ──
    like_count: int
    liked_by_me: bool
    comment_count: int
    top_comment: "CommentPreview | None"

    def __repr__(self) -> str:
        return (
            f"<ClimbingLog id={self.id} grade={self.grade_raw!r} "
            f"success={self.is_success}>"
        )
