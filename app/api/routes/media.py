"""미디어 업로드 라우터 — presigned URL 발급."""

from fastapi import APIRouter

from app.domain.media.dependencies import MediaServiceDep
from app.domain.media.schemas import PresignRequest, PresignResponse
from app.api.dependencies import CurrentUserDep

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
