"""자체 회원가입(local OAuth) Pydantic 스키마 (Day 17).

카카오 OAuth 와 별개로, 이메일+비밀번호 기반 자체 가입/로그인.
비밀번호 정책 검증은 core.password 에 위임.
"""

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.password import PasswordPolicyError, validate_password_policy

# ─────────────────────────────────────────
#  Request
# ─────────────────────────────────────────


class SignupResponse(BaseModel):
    """자체 회원가입 응답 (이메일 인증 안내)."""

    message: str = "인증 메일을 발송했어요. 메일함을 확인해 주세요."
    email: str


class SignupRequest(BaseModel):
    """자체 회원가입 요청.

    필수: email, password, nickname
    선택: profile_image_url
    """

    email: EmailStr = Field(..., description="이메일 (로그인 ID)")
    password: str = Field(
        ...,
        description="비밀번호 (최소 12자 + 영문/숫자/특수문자)",
    )
    nickname: str = Field(
        ...,
        min_length=2,
        max_length=50,
        description="닉네임 (고유, 2~50자)",
    )
    profile_image_url: str | None = Field(
        default=None,
        max_length=500,
        description="프로필 이미지 URL (선택)",
    )

    @field_validator("password")
    @classmethod
    def _validate_password(cls, v: str) -> str:
        """비밀번호 정책 검증.

        PasswordPolicyError → Pydantic ValidationError 로 변환되어
        422 응답에 사유가 포함됨.
        """
        try:
            validate_password_policy(v)
        except PasswordPolicyError as e:
            raise ValueError("; ".join(e.reasons)) from e
        return v

    @field_validator("nickname")
    @classmethod
    def _strip_nickname(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("닉네임은 공백일 수 없습니다")
        return stripped


class LocalLoginRequest(BaseModel):
    """자체 로그인 요청 (이메일 + 비밀번호)."""

    email: EmailStr = Field(..., description="가입한 이메일")
    password: str = Field(..., min_length=1, description="비밀번호")
