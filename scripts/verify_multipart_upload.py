"""분할(multipart) 업로드 API 검증 — 실제 MinIO 로 조각 왕복 (DB 무관).

Cloudflare 요청당 100MB 한도를 넘는 영상용 분할 업로드(init/status/complete/abort)를 검증한다.
- init: 영상만 허용, 상한 초과 거부, 조각 계획, presigned URL 이 공개 주소 기준
- 이어 올리기: 일부 조각만 올린 뒤 status 가 빠진 조각만 재발급, 그 상태의 complete 거부
- 잘린 조각: 크기가 틀린 조각은 미완료로 보고 complete 거부
- 완료: 합친 객체 크기·SHA-256 == 원본
- 권한/취소: 남의 업로드 거부, abort 후 not_found, abort 멱등
- HTTP: 실제 라우트(의존성 override)로 200/204/400+code

★ 선행: 운영과 분리된 임시 MinIO 가 127.0.0.1:19000 에 떠 있어야 한다. 예)
    kubectl run verify-minio -n default --image=minio/minio:RELEASE.2025-04-22T22-12-26Z \\
      --image-pull-policy=Never --restart=Never \\
      --env=MINIO_ROOT_USER=verifyadmin --env=MINIO_ROOT_PASSWORD=verifysecret123 -- server /data
    kubectl port-forward -n default pod/verify-minio 19000:9000
  (MinIO 커뮤니티 이미지는 Docker Hub/quay 에서 더는 받을 수 없어 노드 캐시 이미지를 쓴다.)
실행:
    uv run python -m scripts.verify_multipart_upload
"""

import hashlib
import os
import sys
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi.testclient import TestClient
from minio import Minio

from app.api.dependencies import get_current_user
from app.core.config import get_settings
from app.domain.media.dependencies import get_media_service
from app.domain.media.service import MediaError, MediaService

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

ENDPOINT = os.environ.get("VERIFY_MINIO_ENDPOINT", "127.0.0.1:19000")
ACCESS = os.environ.get("VERIFY_MINIO_ACCESS", "verifyadmin")
SECRET = os.environ.get("VERIFY_MINIO_SECRET", "verifysecret123")
BUCKET = "verify-multipart"
PART = 5 * 1024 * 1024  # MinIO(S3) 최소 조각 크기 — 작은 데이터로 3조각 시나리오 재현
MB = 1024 * 1024

_results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}" + (f"  ({detail})" if detail else ""))


def expect_error(name: str, code: str, fn) -> MediaError | None:
    try:
        fn()
        check(name, False, "예외 없음")
    except MediaError as e:
        check(name, e.code == code, f"code={e.code}")
        return e
    return None


def put(url: str, data: bytes) -> int:
    return httpx.put(url, content=data, timeout=60).status_code


def main() -> int:
    settings = get_settings().model_copy(update={
        "minio_endpoint": ENDPOINT,
        "minio_public_endpoint": ENDPOINT,
        "minio_secure": False,
        "minio_access_key": ACCESS,
        "minio_secret_key": SECRET,
        "minio_bucket": BUCKET,
        "media_multipart_part_size": PART,
    })
    admin = Minio(ENDPOINT, access_key=ACCESS, secret_key=SECRET, secure=False)
    try:
        if not admin.bucket_exists(BUCKET):
            admin.make_bucket(BUCKET)
    except Exception as e:  # noqa: BLE001 — 선행 조건 안내
        print(f"{RED}임시 MinIO 접속 실패{RESET} ({ENDPOINT}): {e}\n docstring 의 선행 조건 참고")
        return 1
    svc = MediaService(settings)
    me, other = str(uuid.uuid4()), str(uuid.uuid4())
    size = 2 * PART + 2 * MB + 3  # 5MB, 5MB, 2MB+3B
    data = os.urandom(size)
    chunks = [data[0:PART], data[PART:2 * PART], data[2 * PART:]]

    print("\n[init 검증]")
    expect_error("이미지 → 거부", "unsupported_media_type",
                 lambda: svc.init_multipart_upload(user_id=me, content_type="image/jpeg", size=MB))
    expect_error("상한(media_max_video_bytes) 초과 → 거부", "media_size_limit_exceeded",
                 lambda: svc.init_multipart_upload(user_id=me, content_type="video/mp4",
                                                   size=settings.media_max_video_bytes + 1))
    init = svc.init_multipart_upload(user_id=me, content_type="video/mp4", size=size)
    check("조각 계획: 3조각, part_size=5MB", init["part_count"] == 3 and init["part_size"] == PART
          and [p["part_number"] for p in init["parts"]] == [1, 2, 3])
    u = urlparse(init["parts"][0]["url"])
    q = parse_qs(u.query)
    check("presigned URL = 공개 주소 + partNumber/uploadId",
          u.netloc == ENDPOINT and q.get("partNumber") == ["1"] and q.get("uploadId") == [init["upload_id"]])
    check("object_key = {user_id}/... , public_url 형식",
          init["object_key"].startswith(f"{me}/")
          and init["public_url"] == f"http://{ENDPOINT}/{BUCKET}/{init['object_key']}")
    key, uid = init["object_key"], init["upload_id"]
    urls = {p["part_number"]: p["url"] for p in init["parts"]}

    print("\n[이어 올리기 — 1·3번만 업로드한 상태]")
    check("조각 1·3 PUT 200", put(urls[1], chunks[0]) == 200 and put(urls[3], chunks[2]) == 200)
    st = svc.multipart_status(user_id=me, object_key=key, upload_id=uid, size=size)
    check("status: uploaded=[1,3], 재발급=[2]",
          st["uploaded"] == [1, 3] and [p["part_number"] for p in st["parts"]] == [2])
    e = expect_error("빠진 조각 있는 채로 complete → 거부", "multipart_incomplete",
                     lambda: svc.complete_multipart_upload(user_id=me, object_key=key, upload_id=uid, size=size))
    check("거부 상세에 빠진 조각 2", bool(e) and e.details.get("missing_or_invalid") == [2])

    print("\n[잘린 조각 — 2번을 1MB 만 올림]")
    check("잘린 조각 2 PUT 200", put(st["parts"][0]["url"], chunks[1][:MB]) == 200)
    st2 = svc.multipart_status(user_id=me, object_key=key, upload_id=uid, size=size)
    check("status: 크기 틀린 2번은 미완료", st2["uploaded"] == [1, 3]
          and [p["part_number"] for p in st2["parts"]] == [2])
    expect_error("잘린 조각 상태로 complete → 거부", "multipart_incomplete",
                 lambda: svc.complete_multipart_upload(user_id=me, object_key=key, upload_id=uid, size=size))

    print("\n[권한]")
    for name, fn in [
        ("status", lambda: svc.multipart_status(user_id=other, object_key=key, upload_id=uid, size=size)),
        ("complete", lambda: svc.complete_multipart_upload(user_id=other, object_key=key, upload_id=uid, size=size)),
        ("abort", lambda: svc.abort_multipart_upload(user_id=other, object_key=key, upload_id=uid)),
    ]:
        expect_error(f"다른 사용자 {name} → 거부", "multipart_forbidden", fn)

    print("\n[완료 — 올바른 2번 재업로드(덮어쓰기) 후 합치기]")
    check("재발급 URL 로 조각 2 PUT 200", put(st2["parts"][0]["url"], chunks[1]) == 200)
    done = svc.complete_multipart_upload(user_id=me, object_key=key, upload_id=uid, size=size)
    check("complete → public_url/category", done["public_url"] == init["public_url"] and done["category"] == "video")
    stat = admin.stat_object(BUCKET, key)
    body = admin.get_object(BUCKET, key).read()
    check("합친 객체 크기 = 원본", stat.size == size, f"{stat.size} / {size}")
    check("합친 객체 SHA-256 = 원본", hashlib.sha256(body).hexdigest() == hashlib.sha256(data).hexdigest())
    check("Content-Type = video/mp4", stat.content_type == "video/mp4", str(stat.content_type))
    expect_error("완료된 업로드 status → not_found", "multipart_not_found",
                 lambda: svc.multipart_status(user_id=me, object_key=key, upload_id=uid, size=size))

    print("\n[취소]")
    ab = svc.init_multipart_upload(user_id=me, content_type="video/quicktime", size=size)
    check("abort 대상 조각 1 PUT 200", put(ab["parts"][0]["url"], chunks[0]) == 200)
    svc.abort_multipart_upload(user_id=me, object_key=ab["object_key"], upload_id=ab["upload_id"])
    expect_error("abort 후 status → not_found", "multipart_not_found",
                 lambda: svc.multipart_status(user_id=me, object_key=ab["object_key"],
                                              upload_id=ab["upload_id"], size=size))
    try:
        svc.abort_multipart_upload(user_id=me, object_key=ab["object_key"], upload_id=ab["upload_id"])
        check("abort 재호출 → 오류 없음 (멱등)", True)
    except Exception as ex:  # noqa: BLE001
        check("abort 재호출 → 오류 없음 (멱등)", False, repr(ex))
    check("abort 된 객체는 생성되지 않음",
          not any(o.object_name == ab["object_key"] for o in admin.list_objects(BUCKET, recursive=True)))

    print("\n[HTTP 라우트 — 의존성 override]")
    from app.main import app
    app.dependency_overrides[get_media_service] = lambda: svc
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid.UUID(me))
    try:
        client = TestClient(app)
        r = client.post("/media/multipart/init", json={"content_type": "video/mp4", "size": size})
        check("POST /media/multipart/init → 200 + parts 3",
              r.status_code == 200 and len(r.json().get("parts", [])) == 3, str(r.status_code))
        j = r.json()
        r2 = client.post("/media/multipart/complete",
                         json={"object_key": j["object_key"], "upload_id": j["upload_id"], "size": size})
        check("미완료 complete → 400 + multipart_incomplete",
              r2.status_code == 400 and "multipart_incomplete" in r2.text, f"{r2.status_code} {r2.text[:80]}")
        r3 = client.post("/media/multipart/abort",
                         json={"object_key": j["object_key"], "upload_id": j["upload_id"]})
        check("POST /media/multipart/abort → 204", r3.status_code == 204, str(r3.status_code))
        r4 = client.post("/media/multipart/init", json={"content_type": "video/mp4", "size": 0})
        check("size=0 → 422 (스키마 검증)", r4.status_code == 422, str(r4.status_code))
    finally:
        app.dependency_overrides.clear()

    # 정리: 테스트 버킷 비우기 (임시 MinIO 자체도 검증 후 삭제)
    for o in admin.list_objects(BUCKET, recursive=True):
        admin.remove_object(BUCKET, o.object_name)

    total, passed = len(_results), sum(_results)
    print(f"\n{'─' * 40}")
    if passed == total:
        print(f"{GREEN}ALL PASS{RESET}  ({passed}/{total})")
        return 0
    print(f"{RED}FAILED{RESET}  ({passed}/{total})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
