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

from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from fastapi.security import HTTPBearer

from app.api.dependencies import CurrentUserDep, OptionalUserId
from app.domain.climbing.dependencies import ClimbingServiceDep
from app.domain.climbing.schemas import (
    SUGGESTED_CATEGORIES,
    CategoryCount,
    ClimbingLogCreate,
    ClimbingLogListResponse,
    ClimbingLogResponse,
    ClimbingLogUpdate,
)

router = APIRouter(prefix="/climbing-logs", tags=["climbing"])

# 선택적 인증용 bearer (토큰 없어도 OK)
_optional_bearer = HTTPBearer(auto_error=False)





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
    request: Request,
    payload: ClimbingLogCreate,
    user: CurrentUserDep,
    service: ClimbingServiceDep,
) -> ClimbingLogResponse:
    log = await service.create_log(
        user_id=user.id, data=payload.model_dump()
    )
    # 영상이면 트랜스코딩 작업을 큐에 등록 (워커가 백그라운드 처리)
    if log.media_status == "processing":
        await request.app.state.arq_pool.enqueue_job(
            "transcode_video", str(log.id)
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
#  인기 태그 집계 (검색 탭 발견용)
# ─────────────────────────────────────────
@router.get(
    "/meta/categories/popular",
    response_model=list[CategoryCount],
    summary="사용 횟수 상위 인기 태그",
    description="공개 계정의 공개 글 기준 태그 사용 횟수 집계. 비로그인도 조회 가능.",
)
async def get_popular_categories(
    service: ClimbingServiceDep,
    limit: int = Query(10, ge=1, le=30),
) -> list[CategoryCount]:
    rows = await service.get_popular_categories(limit=limit)
    return [CategoryCount(tag=tag, count=count) for tag, count in rows]


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
    await service.delete_log(
        log_id=log_id, user_id=user.id, is_admin=user.is_admin
    )
