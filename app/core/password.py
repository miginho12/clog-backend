"""비밀번호 해싱 + 정책 검증 모듈 (Day 17).

자체 회원가입(local OAuth)을 위한 비밀번호 처리.

설계 원칙:
- 평문 저장 절대 금지 → bcrypt 해싱 (salt 자동 포함)
- 정책: 최소 12자 + 영문 + 숫자 + 특수문자
- 해싱/검증과 정책 검증을 분리 (단일 책임)

bcrypt 선택 이유:
- 적응형 해시 (work factor 조정 가능, 기본 12 rounds)
- salt 가 해시 문자열에 내장됨 (별도 컬럼 불필요)
- 같은 비밀번호도 매번 다른 해시 → rainbow table 방어

Spring Security 와 비교:
- BCryptPasswordEncoder.encode() → hash_password()
- BCryptPasswordEncoder.matches() → verify_password()
"""

import re

import bcrypt

# ─────────────────────────────────────────
#  정책 상수
# ─────────────────────────────────────────

MIN_LENGTH = 12
MAX_LENGTH = 128  # bcrypt 는 72 bytes 까지만 유효하지만, UX 상 명시적 제한

# 특수문자 집합 (OWASP 권장 범위)
_SPECIAL_CHARS = r"!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?~`"
_HAS_LETTER = re.compile(r"[A-Za-z]")
_HAS_DIGIT = re.compile(r"\d")
_HAS_SPECIAL = re.compile(f"[{_SPECIAL_CHARS}]")


# ─────────────────────────────────────────
#  예외
# ─────────────────────────────────────────


class PasswordPolicyError(Exception):
    """비밀번호 정책 위반.

    Attributes:
        reasons: 위반 사유 목록 (사용자에게 보여줄 수 있음)
    """

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons))


# ─────────────────────────────────────────
#  정책 검증
# ─────────────────────────────────────────


def validate_password_policy(password: str) -> None:
    """비밀번호 정책 검증.

    정책 (Day 17 - 강력):
    - 최소 12자, 최대 128자
    - 영문 1자 이상
    - 숫자 1자 이상
    - 특수문자 1자 이상

    Raises:
        PasswordPolicyError: 하나라도 위반 시 (모든 위반 사유 수집)
    """
    reasons: list[str] = []

    if len(password) < MIN_LENGTH:
        reasons.append(f"최소 {MIN_LENGTH}자 이상이어야 합니다")
    if len(password) > MAX_LENGTH:
        reasons.append(f"최대 {MAX_LENGTH}자 이하여야 합니다")
    if not _HAS_LETTER.search(password):
        reasons.append("영문자를 포함해야 합니다")
    if not _HAS_DIGIT.search(password):
        reasons.append("숫자를 포함해야 합니다")
    if not _HAS_SPECIAL.search(password):
        reasons.append("특수문자를 포함해야 합니다")

    if reasons:
        raise PasswordPolicyError(reasons)


# ─────────────────────────────────────────
#  해싱 / 검증
# ─────────────────────────────────────────


def hash_password(password: str) -> str:
    """비밀번호를 bcrypt 해시로 변환.

    Note:
        bcrypt 는 72 bytes 초과분을 무시함. validate_password_policy 의
        MAX_LENGTH(128) 와는 별개로, 보안상 72 bytes 까지만 의미 있음.
        UTF-8 한글은 자당 3 bytes 이므로 24자 정도부터 영향.

    Args:
        password: 평문 비밀번호 (이미 정책 검증을 통과했다고 가정)

    Returns:
        bcrypt 해시 문자열 (salt 내장, $2b$ 로 시작)
    """
    pw_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()  # 기본 work factor 12
    hashed = bcrypt.hashpw(pw_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """평문 비밀번호와 저장된 해시를 비교.

    Args:
        password: 사용자가 입력한 평문
        password_hash: DB 에 저장된 bcrypt 해시

    Returns:
        일치하면 True, 아니면 False (예외 던지지 않음)
    """
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            password_hash.encode("utf-8"),
        )
    except (ValueError, TypeError):
        # 해시 형식이 깨진 경우 등 → 인증 실패로 처리
        return False
