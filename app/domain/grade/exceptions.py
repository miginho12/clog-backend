"""Grade 도메인 예외."""


class GradeDomainError(Exception):
    """Grade 도메인 기본 예외."""

    pass


class GymGradeSystemNotFound(GradeDomainError):
    """짐 색 순서 정의를 찾을 수 없음."""

    def __init__(self, gym_name: str):
        self.gym_name = gym_name
        super().__init__(f"gym grade system not found: {gym_name}")


class ColorNotInGymSystem(GradeDomainError):
    """해당 짐의 색 순서에 그 색이 없음."""

    def __init__(self, gym_name: str, color: str):
        self.gym_name = gym_name
        self.color = color
        super().__init__(f"color {color!r} not in gym {gym_name!r} system")
