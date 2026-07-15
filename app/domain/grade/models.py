"""Grade 도메인 모델.

GymGradeSystem — 짐별 색 난이도 순서 정의.

설계 근거 (ADR-041):
- 색의 절대값("보라")은 짐마다 의미가 다름 → 비교 불가
- 대신 "그 짐 안에서의 난이도 순서(rank)"를 척도로 사용
- color_order 배열: 쉬운 것부터 어려운 것 순으로 색 이름 나열
  예: [흰, 노, 주, 초, 파, 빨, 핑, 보, 회, 갈, 검]
- 기록의 색 → 이 배열의 인덱스 = rank
- is_official: 시스템 시드(true) / 사용자 등록(false) 구분
  → 사용자 등록 데이터가 공식 시드와 섞이지 않음

조작 방지: 색 순서는 객관적 사실(그 짐 벽에 실제로 그 순서로 붙어있음)
이라 누가 입력해도 같음. V값 주관 입력이 없어 조작 불가.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


class GymGradeSystem(Base):
    """짐별 색 난이도 순서."""

    __tablename__ = "gym_grade_systems"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    gym_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        comment="짐(암장) 이름 — climbing_logs.gym_name 과 매칭. 지점 단위(예: '피커스 종로')",
    )

    brand_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="브랜드/체인 이름 (예: '피커스') — 같은 브랜드 지점을 묶어보는 용도. "
        "지점별 color_order 는 별개로 유지됨",
    )

    color_order: Mapped[list[str]] = mapped_column(
        ARRAY(String(20)),
        nullable=False,
        comment="색 난이도 순서 (쉬운→어려운). 인덱스 = rank",
    )

    is_official: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="시스템 시드(true) / 사용자 등록(false)",
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="사용자 등록 시 작성자 user id (시드는 NULL)",
    )

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

    def __repr__(self) -> str:
        return (
            f"<GymGradeSystem gym={self.gym_name!r} "
            f"levels={len(self.color_order)} official={self.is_official}>"
        )
