"""SQLAlchemy 2.0 모델의 베이스 클래스.

모든 ORM 모델이 상속받을 Base 클래스.
다음 세션에 User, Climb 같은 모델들이 여기를 상속받음.

Spring/JPA 의 @MappedSuperclass 또는 BaseEntity 와 비슷.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """모든 ORM 모델의 부모 클래스.

    SQLAlchemy 2.0 에서 권장되는 방식 (Mapped[] 타입 힌트 사용 가능).

    사용 예 (다음 세션):
        from app.infra.db.base import Base
        from sqlalchemy.orm import Mapped, mapped_column

        class User(Base):
            __tablename__ = "users"
            id: Mapped[int] = mapped_column(primary_key=True)
            email: Mapped[str] = mapped_column(unique=True)
    """

    pass
