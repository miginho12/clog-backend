"""Climbing 라우트.

엔드포인트:
- POST   /climbing-logs           작성 (인증 필수)
- GET    /climbing-logs           피드 (비로그인 허용, 공개글만)
- GET    /climbing-logs/{id}      상세 (비로그인 허용, 공개글만)
- PATCH  /climbing-logs/{id}      수정 (본인만)
- DELETE /climbing-logs/{id}      삭제 (본인만)
- GET    /climbing-logs/meta/categories  추천 카테고리 목록

선택적 인증(get_optional_user): 토큰 있으면 viewer_id, 없으면 None.
→ 비로그인도 공개글 조회 가능 (ADR-033).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.dependencies import CurrentUserDep
from app.core.security import (
    InvalidToken,
    TokenExpired,
    WrongTokenType,
    decode_access_token,
)
from app.domain.climbing.dependencies import ClimbingServiceDep
from app.domain.climbing.schemas import (
    SUGGESTED_CATEGORIES,
    ClimbingLogCreate,
    ClimbingLogListResponse,
    ClimbingLogResponse,
    ClimbingLogUpdate,
)

router = APIRouter(prefix="/climbing-logs", tags=["climbing"])

# 선택적 인증용 bearer (토큰 없어도 OK)
_optional_bearer = HTTPBearer(auto_error=False)


async def get_optional_user_id(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_optional_bearer)
    ],
) -> UUID | None:
    """선택적 인증: 유효한 토큰이면 user_id, 아니면 None.

    피드/상세 조회에서 사용 — 비로그인도 허용하되, 로그인하면
    본인의 private 글까지 볼 수 있도록 viewer_id 를 넘긴다.
    """
    if credentials is None:
        return None
    try:
        payload = decode_access_token(credentials.credentials)
        return UUID(payload.sub)
    except (TokenExpired, InvalidToken, WrongTokenType, ValueError):
        # 토큰이 있지만 유효하지 않으면 그냥 비로그인 취급
        return None


OptionalUserId = Annotated[UUID | None, Depends(get_optional_user_id)]


# ─────────────────────────────────────────
#  작성
# ─────────────────────────────────────────
@router.post(
    "",
    response_model=ClimbingLogResponse,
    status_code=status.HTTP_201_CREATED,
    summary="클라이밍 기록 작성",
)
async def create_climbing_log(
    payload: ClimbingLogCreate,
    user: CurrentUserDep,
    service: ClimbingServiceDep,
) -> ClimbingLogResponse:
    log = await service.create_log(
        user_id=user.id, data=payload.model_dump()
    )
    return ClimbingLogResponse.model_validate(log)


# ─────────────────────────────────────────
#  피드 (비로그인 허용)
# ─────────────────────────────────────────
@router.get(
    "",
    response_model=ClimbingLogListResponse,
    summary="클라이밍 기록 피드",
    description=(
        "공개 기록 피드. 비로그인도 조회 가능(공개글만). "
        "로그인 시 본인의 비공개 글도 포함. "
        "category/gym/grade_system/success/author 필터 지원."
    ),
)
async def list_climbing_logs(
    service: ClimbingServiceDep,
    viewer_id: OptionalUserId,
    author_id: UUID | None = Query(None, description="특정 작성자 글만"),
    category: str | None = Query(None, description="카테고리 태그 필터"),
    gym_name: str | None = Query(None, description="짐 이름 필터"),
    grade_system: str | None = Query(None, description="v_scale | color"),
    only_success: bool | None = Query(None, description="완등만 보기"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
) -> ClimbingLogListResponse:
    items, has_next = await service.list_feed(
        viewer_id=viewer_id,
        author_id=author_id,
        category=category,
        gym_name=gym_name,
        grade_system=grade_system,
        only_success=only_success,
        page=page,
        page_size=page_size,
    )
    return ClimbingLogListResponse(
        items=[ClimbingLogResponse.model_validate(i) for i in items],
        page=page,
        page_size=page_size,
        has_next=has_next,
    )


# ─────────────────────────────────────────
#  추천 카테고리 목록
# ─────────────────────────────────────────
@router.get(
    "/meta/categories",
    response_model=list[str],
    summary="추천 카테고리 태그 목록",
)
async def get_suggested_categories() -> list[str]:
    return SUGGESTED_CATEGORIES


# ─────────────────────────────────────────
#  상세 (비로그인 허용)
# ─────────────────────────────────────────
@router.get(
    "/{log_id}",
    response_model=ClimbingLogResponse,
    summary="클라이밍 기록 상세",
)
async def get_climbing_log(
    log_id: UUID,
    service: ClimbingServiceDep,
    viewer_id: OptionalUserId,
) -> ClimbingLogResponse:
    log = await service.get_log(log_id=log_id, viewer_id=viewer_id)
    return ClimbingLogResponse.model_validate(log)


# ─────────────────────────────────────────
#  수정 (본인만)
# ─────────────────────────────────────────
@router.patch(
    "/{log_id}",
    response_model=ClimbingLogResponse,
    summary="클라이밍 기록 수정",
)
async def update_climbing_log(
    log_id: UUID,
    payload: ClimbingLogUpdate,
    user: CurrentUserDep,
    service: ClimbingServiceDep,
) -> ClimbingLogResponse:
    log = await service.update_log(
        log_id=log_id,
        user_id=user.id,
        data=payload.model_dump(exclude_unset=True),
    )
    return ClimbingLogResponse.model_validate(log)


# ─────────────────────────────────────────
#  삭제 (본인만)
# ─────────────────────────────────────────
@router.delete(
    "/{log_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="클라이밍 기록 삭제",
)
async def delete_climbing_log(
    log_id: UUID,
    user: CurrentUserDep,
    service: ClimbingServiceDep,
) -> None:
    await service.delete_log(log_id=log_id, user_id=user.id)
