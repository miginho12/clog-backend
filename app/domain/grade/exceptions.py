"""Grade 도메인 예외."""


class GradeDomainError(Exception):
    """Grade 도메인 기본 예외."""

    pass


class GymGradeSystemNotFound(GradeDomainError):
    """짐 색 순서 정의를 찾을 수 없음 (gym_name 으로 조회 실패).

    주로 base_gym 입력 검증용 (구현 5, 400). id 기반 404 는 별도.
    """

    def __init__(self, gym_name: str):
        self.gym_name = gym_name
        super().__init__(f"gym grade system not found: {gym_name}")


class ColorNotInGymSystem(GradeDomainError):
    """해당 짐의 색 순서에 그 색이 없음."""

    def __init__(self, gym_name: str, color: str):
        self.gym_name = gym_name
        self.color = color
        super().__init__(f"color {color!r} not in gym {gym_name!r} system")


# ── 구현 6: 암장 색체계 등록/수정/삭제 ──


class GymGradeSystemAlreadyExists(GradeDomainError):
    """이미 등록된 gym_name 으로 신규 등록 시도 (409)."""

    def __init__(self, gym_name: str):
        self.gym_name = gym_name
        super().__init__(f"gym grade system already exists: {gym_name}")


class GymGradeSystemNotFoundById(GradeDomainError):
    """id 로 짐 색체계 조회 실패 (404). 수정/삭제/단건조회 대상 없음."""

    def __init__(self, system_id: str):
        self.system_id = system_id
        super().__init__(f"gym grade system not found by id: {system_id}")


class GymGradeSystemForbidden(GradeDomainError):
    """본인 등록분(비공식)이 아닌 색체계 수정/삭제 시도 (403).

    is_official=True(시스템 시드) 또는 타인 등록분은 변경 불가.
    """

    def __init__(self, system_id: str):
        self.system_id = system_id
        super().__init__(f"cannot modify gym grade system: {system_id}")


# ── 구현 7: 암장 랭킹 기간 필터 ──


class InvalidRankingPeriod(GradeDomainError):
    """랭킹 기간 파라미터 조합이 잘못됨 (400).

    period=week 인데 week 누락, period=month 인데 month 누락,
    ISO week 범위(1~53) 밖 등.
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"invalid ranking period: {reason}")
