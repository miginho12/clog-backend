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
from typing import Literal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


class ClimbingLog(Base):
    """클라이밍 기록 (한 문제 단위)."""

    __tablename__ = "climbing_logs"

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
        comment="미디어 URL",
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

    def __repr__(self) -> str:
        return (
            f"<ClimbingLog id={self.id} grade={self.grade_raw!r} "
            f"success={self.is_success}>"
        )
