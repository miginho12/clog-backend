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


# ── 분할(multipart) 업로드 ──
# Cloudflare 요청당 100MB 한도를 넘는 영상용 (자세한 흐름은 MediaService 주석).


class MultipartPartUrl(BaseModel):
    part_number: int  # 1부터
    url: str  # 이 조각을 PUT 할 presigned URL


class MultipartInitRequest(BaseModel):
    """분할 업로드 시작 요청 (영상 전용)."""

    content_type: str = Field(..., examples=["video/mp4"])
    filename: str | None = Field(None, max_length=255)
    size: int = Field(..., gt=0, description="원본 파일 크기(bytes) — 조각 계획의 기준")


class MultipartInitResponse(BaseModel):
    upload_id: str
    object_key: str
    public_url: str  # 합치기 완료 후 조회 URL (media_url 로 사용)
    category: str  # "video"
    part_size: int  # 마지막 조각을 뺀 조각 크기(bytes)
    part_count: int
    parts: list[MultipartPartUrl]
    expires_in: int  # 조각 URL 유효시간(초)


class MultipartRef(BaseModel):
    """진행 중인 분할 업로드 식별자 (status/complete 는 size 도 필요)."""

    object_key: str = Field(..., max_length=500)
    upload_id: str = Field(..., max_length=500)


class MultipartSizedRef(MultipartRef):
    size: int = Field(..., gt=0, description="init 때 보낸 원본 크기")


class MultipartStatusResponse(BaseModel):
    uploaded: list[int]  # 크기가 맞게 올라간 조각 번호
    parts: list[MultipartPartUrl]  # 아직 올려야 할 조각 (URL 재발급)
    expires_in: int


class MultipartCompleteResponse(BaseModel):
    object_key: str
    public_url: str
    category: str
