"""미디어 업로드 schemas."""

from pydantic import BaseModel, Field


class PresignRequest(BaseModel):
    """presigned 업로드 URL 요청."""

    content_type: str = Field(..., examples=["image/jpeg", "video/mp4"])
    filename: str | None = Field(None, max_length=255)


class PresignResponse(BaseModel):
    """presigned 업로드 URL 응답."""

    upload_url: str   # 브라우저가 PUT 할 presigned URL
    object_key: str   # 저장 경로 (기록 저장용)
    public_url: str   # 업로드 후 조회 URL (media_url 로 사용)
    category: str     # "image" | "video"
    expires_in: int   # presigned 유효시간(초)
