"""미디어 업로드 — MinIO presigned URL 발급."""

import uuid
from collections.abc import Iterator
from datetime import timedelta

from minio import Minio

from app.core.config import Settings

# 허용 content_type → 확장자 + 카테고리(image/video)
ALLOWED_CONTENT_TYPES: dict[str, tuple[str, str]] = {
    "image/jpeg": ("jpg", "image"),
    "image/png": ("png", "image"),
    "image/webp": ("webp", "image"),
    "image/gif": ("gif", "image"),
    "video/mp4": ("mp4", "video"),
    "video/quicktime": ("mov", "video"),
    "video/webm": ("webm", "video"),
}


class MediaError(Exception):
    """미디어 업로드 도메인 예외 (400 매핑)."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


class MediaService:
    def __init__(self, settings: Settings, *, internal: bool = False):
        self.settings = settings
        # presigned 발급은 외부 엔드포인트(브라우저가 닿는 주소)로 서명.
        # 단 internal=True (클러스터 내부 작업: 객체 나열/삭제)면 내부 주소 + http.
        # 내부 작업이 외부 주소로 가면 self-signed TLS 로 막히기 때문.
        if internal:
            endpoint = settings.minio_endpoint
            secure = False
        else:
            endpoint = settings.minio_public_endpoint or settings.minio_endpoint
            secure = settings.minio_secure
        self._client = Minio(
            endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=secure,
            # region 명시: presigned 발급 시 _get_region 네트워크 조회 방지
            # (외부 endpoint 로 region 조회 시 self-signed TLS 로 실패)
            region="us-east-1",
        )
        self._bucket = settings.minio_bucket

    def create_presigned_upload(
        self, *, user_id: str, content_type: str
    ) -> dict:
        """업로드용 presigned PUT URL 발급.

        반환: {upload_url, object_key, public_url, category}
        """
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise MediaError(
                "unsupported_media_type",
                "지원하지 않는 파일 형식입니다",
                {"content_type": content_type},
            )
        ext, category = ALLOWED_CONTENT_TYPES[content_type]
        # object key: {user_id}/{uuid}.{ext} — 충돌 방지 + 사용자별 격리
        object_key = f"{user_id}/{uuid.uuid4().hex}.{ext}"

        upload_url = self._client.presigned_put_object(
            self._bucket,
            object_key,
            expires=timedelta(seconds=self.settings.minio_presign_expiry),
        )

        # 공개 조회 URL (버킷 public-read 전제) — 외부 엔드포인트 기준
        host = self.settings.minio_public_endpoint or self.settings.minio_endpoint
        scheme = "https" if self.settings.minio_secure else "http"
        public_url = f"{scheme}://{host}/{self._bucket}/{object_key}"

        return {
            "upload_url": upload_url,
            "object_key": object_key,
            "public_url": public_url,
            "category": category,
        }

    def list_object_keys(self) -> Iterator[str]:
        """버킷의 모든 object_key 나열 (orphan 청소용)."""
        objects = self._client.list_objects(self._bucket, recursive=True)
        for obj in objects:
            if obj.object_name:
                yield obj.object_name

    def delete_object(self, object_key: str) -> None:
        """단일 객체 삭제 (멱등 — 없는 키여도 에러 안 냄)."""
        self._client.remove_object(self._bucket, object_key)

    def extract_object_key(self, media_url: str) -> str | None:
        """media_url 에서 object_key 추출.

        예: https://host/clog-media/{user}/{uuid}.png -> {user}/{uuid}.png
        버킷 경로(/clog-media/) 뒤 부분만 반환. 형식 안 맞으면 None.
        """
        marker = f"/{self._bucket}/"
        idx = media_url.find(marker)
        if idx == -1:
            return None
        key = media_url[idx + len(marker):]
        # 쿼리스트링(?X-Amz-...) 있으면 제거
        return key.split("?", 1)[0] or None
