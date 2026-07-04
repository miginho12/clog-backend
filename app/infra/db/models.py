"""모든 ORM 모델을 import 하는 중앙 모듈.

⭐ 이 파일의 역할:
Alembic 의 autogenerate 가 Base.metadata 에서 테이블을 찾으려면
모든 모델 클래스가 Python 에 로드(import) 되어있어야 함.

env.py 에서 이 모듈을 import 하면 → 여기에 적힌 모든 모델이 로드됨 →
Base.metadata 에 등록됨 → autogenerate 가 감지.

새 모델 추가 시 여기에 한 줄만 추가.
"""

from app.domain.climbing.models import ClimbingLog  # noqa: F401
from app.domain.comment_likes.models import CommentLike  # noqa: F401
from app.domain.comments.models import Comment  # noqa: F401
from app.domain.grade.models import GymGradeSystem  # noqa: F401
from app.domain.likes.models import Like  # noqa: F401
from app.domain.notifications.models import Notification  # noqa: F401
from app.domain.users.models import User  # noqa: F401
from app.infra.db.base import Base

# 다음 세션에 추가될 모델들:
# from app.domain.gyms.models import Gym  # noqa: F401
# from app.domain.climbs.models import ClimbSession, ClimbRecord  # noqa: F401
# from app.domain.grades.models import Grade  # noqa: F401

__all__ = [
    "Base",
    "User",
    "ClimbingLog",
    "GymGradeSystem",
    "Like",
    "Comment",
    "CommentLike",
    "Notification",
]
