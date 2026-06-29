"""미디어 도메인 의존성."""

from typing import Annotated

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.domain.media.service import MediaService


def get_media_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> MediaService:
    return MediaService(settings)


MediaServiceDep = Annotated[MediaService, Depends(get_media_service)]
