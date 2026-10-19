"""미디어 업로드 — MinIO presigned URL 발급."""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

from minio import Minio
from minio.datatypes import Part
from minio.error import S3Error

from app.core.config import Settings

# 조각 수 상한 — _list_parts 한 페이지(1000) 안에 들어오게. 16MB 조각이면 ~16GB 라 충분.
MAX_MULTIPART_PARTS = 1000

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

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
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
        # 분할 업로드의 생성/조각 조회/합치기/취소는 MinIO 로 실제 요청이 가는 작업이라
        # 항상 내부 주소로 보낸다 (서명만 하는 presign 은 위 _client 의 외부 주소 기준).
        self._ops_client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=False,
            region="us-east-1",
        )
        self._bucket = settings.minio_bucket

    def create_presigned_upload(
        self, *, user_id: str, content_type: str
    ) -> dict[str, Any]:
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

    # ── 분할(multipart) 업로드 ──
    # Cloudflare 가 요청 1건당 100MB 를 넘으면 413 으로 끊어 큰 영상은 조각으로 올린다.
    # 흐름: init → 브라우저가 조각별 presigned URL 로 PUT
    #       → (끊기면 status 로 이미 올라간 조각 확인 + 빠진 조각 URL 재발급)
    #       → complete(서버가 조각을 직접 조회·검증 후 합침) / 포기 시 abort.
    # 서버는 업로드 상태를 저장하지 않는다 — 조각 계획은 클라이언트가 보내는 size 로 계산하고,
    # size 조작은 게시물 생성 시 실제 객체 크기/길이 검증(climbing service)에서 다시 걸러진다.
    # minio-py 7.2 는 multipart 저수준 API 를 언더스코어 메서드로만 노출한다
    # (_create/_complete/_abort_multipart_upload, _list_parts) → 라이브러리 업그레이드 시 확인.

    def _public_url(self, object_key: str) -> str:
        host = self.settings.minio_public_endpoint or self.settings.minio_endpoint
        scheme = "https" if self.settings.minio_secure else "http"
        return f"{scheme}://{host}/{self._bucket}/{object_key}"

    def _part_plan(self, size: int) -> list[int]:
        """size 를 part_size 로 나눈 조각별 기대 크기 (마지막 조각만 나머지)."""
        ps = self.settings.media_multipart_part_size
        n = -(-size // ps)  # 올림
        return [ps] * (n - 1) + [size - ps * (n - 1)]

    def _check_owner(self, user_id: str, object_key: str) -> None:
        # object key 는 {user_id}/{uuid}.{ext} — 남의 업로드를 조회/합치기/취소하지 못하게
        if not object_key.startswith(f"{user_id}/"):
            raise MediaError("multipart_forbidden", "본인의 업로드만 처리할 수 있습니다")

    def _presign_part(self, object_key: str, upload_id: str, part_number: int) -> str:
        return self._client.get_presigned_url(
            "PUT",
            self._bucket,
            object_key,
            expires=timedelta(seconds=self.settings.media_multipart_presign_expiry),
            extra_query_params={"partNumber": str(part_number), "uploadId": upload_id},
        )

    def _uploaded_parts(self, object_key: str, upload_id: str) -> dict[int, Part]:
        try:
            # 조각 수 상한(MAX_MULTIPART_PARTS)이 한 페이지(1000) 이내라 페이지네이션 불필요
            result = self._ops_client._list_parts(
                self._bucket, object_key, upload_id, max_parts=1000
            )
        except S3Error as e:
            if e.code == "NoSuchUpload":
                raise MediaError(
                    "multipart_not_found", "업로드를 찾을 수 없습니다 (만료되었거나 취소됨)"
                ) from e
            raise
        return {p.part_number: p for p in result.parts}

    def init_multipart_upload(
        self, *, user_id: str, content_type: str, size: int
    ) -> dict[str, Any]:
        """분할 업로드 시작: upload_id 생성 + 조각별 presigned PUT URL 발급 (영상 전용)."""
        if ALLOWED_CONTENT_TYPES.get(content_type, ("", ""))[1] != "video":
            raise MediaError(
                "unsupported_media_type",
                "분할 업로드는 영상만 지원합니다",
                {"content_type": content_type},
            )
        limit = self.settings.media_max_video_bytes
        if size > limit:
            raise MediaError(
                "media_size_limit_exceeded",
                f"영상은 {limit // (1024 * 1024)}MB 이하만 업로드할 수 있습니다",
                {"size": size, "limit": limit},
            )
        plan = self._part_plan(size)
        if len(plan) > MAX_MULTIPART_PARTS:
            raise MediaError(
                "multipart_too_many_parts",
                "조각 수가 너무 많습니다",
                {"parts": len(plan), "limit": MAX_MULTIPART_PARTS},
            )
        ext, category = ALLOWED_CONTENT_TYPES[content_type]
        object_key = f"{user_id}/{uuid.uuid4().hex}.{ext}"
        upload_id = self._ops_client._create_multipart_upload(
            self._bucket, object_key, {"Content-Type": content_type}
        )
        return {
            "upload_id": upload_id,
            "object_key": object_key,
            "public_url": self._public_url(object_key),
            "category": category,
            "part_size": self.settings.media_multipart_part_size,
            "part_count": len(plan),
            "parts": [
                {"part_number": n, "url": self._presign_part(object_key, upload_id, n)}
                for n in range(1, len(plan) + 1)
            ],
            "expires_in": self.settings.media_multipart_presign_expiry,
        }

    def multipart_status(
        self, *, user_id: str, object_key: str, upload_id: str, size: int
    ) -> dict[str, Any]:
        """이어 올리기용: 크기가 맞게 올라간 조각 목록 + 나머지 조각 URL 재발급.

        크기가 틀린 조각(중간에 끊겨 잘린 경우 등)은 미완료로 보고 다시 올리게 한다
        (같은 part_number 로 다시 PUT 하면 덮어써짐).
        """
        self._check_owner(user_id, object_key)
        plan = self._part_plan(size)
        uploaded = self._uploaded_parts(object_key, upload_id)
        done = [n for n in range(1, len(plan) + 1) if n in uploaded and uploaded[n].size == plan[n - 1]]
        missing = [n for n in range(1, len(plan) + 1) if n not in done]
        return {
            "uploaded": done,
            "parts": [
                {"part_number": n, "url": self._presign_part(object_key, upload_id, n)}
                for n in missing
            ],
            "expires_in": self.settings.media_multipart_presign_expiry,
        }

    def complete_multipart_upload(
        self, *, user_id: str, object_key: str, upload_id: str, size: int
    ) -> dict[str, Any]:
        """모든 조각이 계획대로(개수·크기) 올라왔을 때만 합친다 — 하나라도 어긋나면 거부."""
        self._check_owner(user_id, object_key)
        plan = self._part_plan(size)
        uploaded = self._uploaded_parts(object_key, upload_id)
        bad = [n for n in range(1, len(plan) + 1) if n not in uploaded or uploaded[n].size != plan[n - 1]]
        extra = sorted(n for n in uploaded if n > len(plan))
        if bad or extra:
            raise MediaError(
                "multipart_incomplete",
                "아직 올라가지 않았거나 크기가 맞지 않는 조각이 있습니다",
                {"missing_or_invalid": bad, "unexpected": extra},
            )
        self._ops_client._complete_multipart_upload(
            self._bucket,
            object_key,
            upload_id,
            [Part(n, uploaded[n].etag) for n in range(1, len(plan) + 1)],
        )
        return {
            "object_key": object_key,
            "public_url": self._public_url(object_key),
            "category": "video",
        }

    def abort_multipart_upload(self, *, user_id: str, object_key: str, upload_id: str) -> None:
        """업로드 포기 → 올라간 조각 정리. 이미 없으면(취소/완료/만료) 조용히 성공 (멱등)."""
        self._check_owner(user_id, object_key)
        try:
            self._ops_client._abort_multipart_upload(self._bucket, object_key, upload_id)
        except S3Error as e:
            if e.code != "NoSuchUpload":
                raise

    def list_objects(self) -> Iterator[tuple[str, datetime | None]]:
        """버킷의 모든 (object_key, 마지막 수정 시각) 나열 (orphan 청소용).

        수정 시각은 reconcile 이 막 올라온 객체(업로드 직후·트랜스코딩 중)를
        건너뛰는 데 쓴다.
        """
        objects = self._client.list_objects(self._bucket, recursive=True)
        for obj in objects:
            if obj.object_name:
                yield obj.object_name, obj.last_modified

    def delete_object(self, object_key: str) -> None:
        """단일 객체 삭제 (멱등 — 없는 키여도 에러 안 냄)."""
        self._client.remove_object(self._bucket, object_key)

    async def get_video_duration(self, object_key: str) -> float | None:
        """영상 길이(초)를 ffprobe 로 조회 (다운로드/디코딩 없이 메타데이터만).

        get_object_size 와 대응되는 사후 검증용 메서드. 클러스터 내부망
        URL(http, TLS 없음)을 ffprobe 에 직접 넘겨 원격으로 읽는다.
        실패(ffprobe 에러, 타임아웃, 파싱 실패)하면 None — 호출측이 검증을
        건너뛸지 결정한다(네트워크 오류로 정상 업로드를 막지 않기 위함).
        """
        url = f"http://{self.settings.minio_endpoint}/{self._bucket}/{object_key}"
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        except TimeoutError:
            proc.kill()
            return None
        if proc.returncode != 0:
            return None
        try:
            return float(stdout.decode().strip())
        except ValueError:
            return None

    def get_object_size(self, object_key: str) -> int | None:
        """객체의 실제 업로드 용량(bytes) 조회.

        presigned PUT 은 자체적으로 업로드 용량 상한을 걸 수 없어(그건
        presigned POST policy 여야 가능), 로그 생성 시점에 사후 검증하는
        용도로 쓴다. 객체가 없으면 None(호출부가 알아서 처리).
        """
        try:
            stat = self._client.stat_object(self._bucket, object_key)
        except S3Error:
            return None
        return stat.size

    def download_to_file(self, object_key: str, dest_path: str) -> None:
        """MinIO 객체를 로컬 파일로 다운로드 (트랜스코딩 소스용)."""
        self._client.fget_object(self._bucket, object_key, dest_path)

    def upload_file(
        self, object_key: str, src_path: str, content_type: str
    ) -> str:
        """로컬 파일을 MinIO 에 업로드하고 public URL 반환 (압축본용)."""
        self._client.fput_object(
            self._bucket,
            object_key,
            src_path,
            content_type=content_type,
        )
        scheme = "https" if self.settings.minio_secure else "http"
        endpoint = (
            self.settings.minio_public_endpoint or self.settings.minio_endpoint
        )
        return f"{scheme}://{endpoint}/{self._bucket}/{object_key}"

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
