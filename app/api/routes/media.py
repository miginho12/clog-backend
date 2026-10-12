"""미디어 업로드 라우터 — presigned URL 발급."""

from fastapi import APIRouter, status

from app.api.dependencies import CurrentUserDep
from app.domain.media.dependencies import MediaServiceDep
from app.domain.media.schemas import (
    MultipartCompleteResponse,
    MultipartInitRequest,
    MultipartInitResponse,
    MultipartRef,
    MultipartSizedRef,
    MultipartStatusResponse,
    PresignRequest,
    PresignResponse,
)

router = APIRouter(prefix="/media", tags=["media"])


@router.post(
    "/presign",
    response_model=PresignResponse,
    summary="미디어 업로드용 presigned URL 발급",
)
async def create_presigned_upload(
    body: PresignRequest,
    user: CurrentUserDep,
    service: MediaServiceDep,
) -> PresignResponse:
    result = service.create_presigned_upload(
        user_id=str(user.id),
        content_type=body.content_type,
    )
    return PresignResponse(
        upload_url=result["upload_url"],
        object_key=result["object_key"],
        public_url=result["public_url"],
        category=result["category"],
        expires_in=service.settings.minio_presign_expiry,
    )


# ── 분할(multipart) 업로드 — Cloudflare 요청당 100MB 한도를 넘는 영상용 ──
# 서비스가 MinIO 로 동기 네트워크 호출(생성/조각 조회/합치기/취소)을 하므로 async 가 아닌
# def 라우트로 둔다 → FastAPI 가 스레드풀에서 실행해 이벤트 루프(다른 요청)를 막지 않음.


@router.post(
    "/multipart/init",
    response_model=MultipartInitResponse,
    summary="분할 업로드 시작 (조각별 presigned URL 발급)",
)
def init_multipart_upload(
    body: MultipartInitRequest,
    user: CurrentUserDep,
    service: MediaServiceDep,
) -> MultipartInitResponse:
    result = service.init_multipart_upload(
        user_id=str(user.id), content_type=body.content_type, size=body.size
    )
    return MultipartInitResponse(**result)


@router.post(
    "/multipart/status",
    response_model=MultipartStatusResponse,
    summary="분할 업로드 이어 올리기: 올라간 조각 + 남은 조각 URL 재발급",
)
def multipart_status(
    body: MultipartSizedRef,
    user: CurrentUserDep,
    service: MediaServiceDep,
) -> MultipartStatusResponse:
    result = service.multipart_status(
        user_id=str(user.id), object_key=body.object_key, upload_id=body.upload_id, size=body.size
    )
    return MultipartStatusResponse(**result)


@router.post(
    "/multipart/complete",
    response_model=MultipartCompleteResponse,
    summary="분할 업로드 완료 (조각 검증 후 합치기)",
)
def complete_multipart_upload(
    body: MultipartSizedRef,
    user: CurrentUserDep,
    service: MediaServiceDep,
) -> MultipartCompleteResponse:
    result = service.complete_multipart_upload(
        user_id=str(user.id), object_key=body.object_key, upload_id=body.upload_id, size=body.size
    )
    return MultipartCompleteResponse(**result)


@router.post(
    "/multipart/abort",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="분할 업로드 취소 (올라간 조각 정리, 멱등)",
)
def abort_multipart_upload(
    body: MultipartRef,
    user: CurrentUserDep,
    service: MediaServiceDep,
) -> None:
    service.abort_multipart_upload(
        user_id=str(user.id), object_key=body.object_key, upload_id=body.upload_id
    )
