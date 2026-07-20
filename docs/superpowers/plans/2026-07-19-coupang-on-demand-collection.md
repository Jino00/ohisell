# 쿠팡 On-Demand 수집 + 전역 신선도 배너 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 쿠팡 4개 브라우저 수집 스트림의 자동 트리거를 제거해 창이 버튼 누를 때만 뜨게 하고, 낡음(24h🟡/48h🔴)·실패(로그인 필요🔴)를 전역 배너로 상시 가시화한다.

**Architecture:** 기존 버튼→플래그→데몬 모델은 그대로 두고, ①창을 스스로 띄우는 자동 트리거 3개(ofix 광고비 크론 + rocket/ohitech 23h 안전망)를 제거하고, ②4스트림 상태를 한 번에 주는 집계 엔드포인트 + 전역 배너 빌더를 신설한다. 기존 `buildPipelineHealthBanner` 패턴(순수 빌더 + `.ts` 테스트)을 그대로 미러링한다.

**Tech Stack:** FastAPI(Python 3.11) / SQLAlchemy / Alembic / React 19 + Vite + TS / vitest / pytest

**설계 문서:** `docs/superpowers/specs/2026-07-19-coupang-on-demand-collection-design.md`

**확정 파라미터:** WARN=24h, CRIT=48h. 배너=전역(Layout). 창 깜빡임 acceptable. state 우선순위: in_flight > failed > critical > warn > fresh.

---

## File Structure

**Backend**
- Create: `backend/app/services/coupang/collection_status.py` — 4스트림 상태 집계 SA(단일 책임). 순수 `compute_stream_state()` + `collection_status(db)`.
- Create: `backend/tests/test_collection_status.py`
- Modify: `backend/app/routers/coupang_ops.py` — `GET /collection-status` 라우트 추가.
- Modify: `backend/app/services/scheduler_service.py` — `request_ad_cost_refresh` 크론/잡맵/함수 제거.
- Create: `backend/alembic/versions/<rev>_drop_request_ad_cost_refresh_job.py` — prod `SchedulerState` 행 삭제(멱등).
- Modify: `backend/tests/test_scheduler_*` (신규 파일) — 크론 테이블에서 잡 부재 회귀.

**Daemons (tools/)**
- Modify: `tools/rocket_supplier_fetcher.py` — `cmd_poll` "일별 정기 실행(23h)" 블록 제거.
- Modify: `tools/ohitech_ad_fetcher.py` — `cmd_poll` "daily" 트리거 제거(버튼만).

**Frontend**
- Modify: `frontend/src/lib/api.ts` — `CollectionStatus` 타입 + `getCollectionStatus()`.
- Create: `frontend/src/components/collectionFreshnessBanner.ts` — 순수 빌더 `buildCollectionFreshnessBanner()`.
- Create: `frontend/src/components/collectionFreshnessBanner.test.ts`
- Modify: `frontend/src/components/Layout.tsx` — 배너 렌더 + 60s 폴.

---

## Task 1: 백엔드 — 순수 상태 계산 `compute_stream_state`

**Files:**
- Create: `backend/app/services/coupang/collection_status.py`
- Test: `backend/tests/test_collection_status.py`

- [ ] **Step 1: 실패 테스트 작성**

`backend/tests/test_collection_status.py`:
```python
# test_collection_status.py — 쿠팡 4스트림 수집 신선도 집계 SA 가드.
#   ★존재 이유: 자동 트리거 제거 후 '잊어버림→조용히 낡음'을 막는 유일한 안전장치가 이 상태다.
from datetime import datetime
from app.services.coupang.collection_status import compute_stream_state, WARN_HOURS, CRIT_HOURS

NOW = datetime(2026, 7, 19, 12, 0, 0)  # naive KST

def _iso(y, mo, d, h=12, mi=0):
    return datetime(y, mo, d, h, mi, 0).isoformat()

def test_fresh_within_warn():
    s = compute_stream_state(last_success_at=_iso(2026, 7, 19, 6), last_error_at=None, requested=False, now_kst=NOW)
    assert s["state"] == "fresh"
    assert 5.9 < s["age_hours"] < 6.1

def test_warn_between_24_and_48():
    s = compute_stream_state(last_success_at=_iso(2026, 7, 18, 6), last_error_at=None, requested=False, now_kst=NOW)
    assert s["state"] == "warn"  # 30h

def test_critical_over_48():
    s = compute_stream_state(last_success_at=_iso(2026, 7, 16, 6), last_error_at=None, requested=False, now_kst=NOW)
    assert s["state"] == "critical"  # 78h

def test_never_succeeded_is_critical():
    s = compute_stream_state(last_success_at=None, last_error_at=None, requested=False, now_kst=NOW)
    assert s["state"] == "critical"
    assert s["age_hours"] is None

def test_failed_when_error_newer_than_success():
    s = compute_stream_state(last_success_at=_iso(2026, 7, 19, 6), last_error_at=_iso(2026, 7, 19, 10),
                             requested=False, now_kst=NOW)
    assert s["state"] == "failed"

def test_success_newer_than_error_not_failed():
    s = compute_stream_state(last_success_at=_iso(2026, 7, 19, 10), last_error_at=_iso(2026, 7, 19, 6),
                             requested=False, now_kst=NOW)
    assert s["state"] == "fresh"

def test_in_flight_takes_precedence():
    s = compute_stream_state(last_success_at=_iso(2026, 7, 16, 6), last_error_at=_iso(2026, 7, 19, 11),
                             requested=True, now_kst=NOW)
    assert s["state"] == "in_flight"

def test_tzaware_iso_treated_as_kst_no_9h_drift():
    # tz-aware 입력이 와도 KST로 해석 → 9시간 오차 없음(SQLite UTC 함정 방어).
    s = compute_stream_state(last_success_at="2026-07-19T06:00:00+09:00", last_error_at=None,
                             requested=False, now_kst=NOW)
    assert 5.9 < s["age_hours"] < 6.1

def test_boundary_exactly_24h_is_warn():
    s = compute_stream_state(last_success_at=_iso(2026, 7, 18, 12), last_error_at=None, requested=False, now_kst=NOW)
    assert s["state"] == "warn"  # 정확히 24h → warn(>= 경계는 warn 쪽)
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_collection_status.py -q`
Expected: FAIL — `ModuleNotFoundError: collection_status`

- [ ] **Step 3: 구현**

`backend/app/services/coupang/collection_status.py`:
```python
# collection_status.py — 쿠팡 4개 브라우저 수집 스트림의 신선도/실패 상태 집계(SA, 단일 책임).
#   자동 트리거 제거 후, '안 눌러 낡음(stale)' vs '눌렀는데 실패(failed)'를 전역 배너에 공급한다.
#   각 스트림의 기존 refresh_status(db)를 재사용(중복 구현 금지). state 판정만 여기서 한다.
from __future__ import annotations
from datetime import datetime

from sqlalchemy.orm import Session

from app.utils.kst import KST, kst_now
from app.services.coupang import (
    ad_cost_sync,
    ohitech_ad_sync,
    rocket_supplier_sync,
    vendor_summary_sync,
)

WARN_HOURS = 24
CRIT_HOURS = 48

# (key, label, refresh_status 콜러블) — 표시 순서 = 이 순서.
_STREAMS = [
    ("ofix_sales", "ofix 판매분석", lambda db: vendor_summary_sync.refresh_status(db)),
    ("ofix_ad", "ofix 광고비", lambda db: ad_cost_sync.refresh_status(db)),
    ("ohitech_ad", "ohitech 로켓광고", lambda db: ohitech_ad_sync.refresh_status(db)),
    ("supplier_hub", "로켓 발주/정산", lambda db: rocket_supplier_sync.rocket_refresh_status(db)),
]


def _parse_kst(iso: str | None) -> datetime | None:
    """iso 문자열 → naive KST datetime. tz-aware면 KST로 변환 후 naive화(UTC 함정 방어)."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(KST).replace(tzinfo=None)
    return dt


def compute_stream_state(
    last_success_at: str | None,
    last_error_at: str | None,
    requested: bool,
    now_kst: datetime,
) -> dict:
    """순수 판정. 우선순위: in_flight > failed > (never/critical/warn/fresh).

    age_hours = 마지막 성공 이후 경과(시간). last_success_at 없으면 None.
    """
    suc = _parse_kst(last_success_at)
    err = _parse_kst(last_error_at)
    age_hours = None if suc is None else (now_kst - suc).total_seconds() / 3600.0

    if requested:
        state = "in_flight"
    elif err is not None and (suc is None or err > suc):
        state = "failed"
    elif suc is None:
        state = "critical"
    elif age_hours >= CRIT_HOURS:
        state = "critical"
    elif age_hours >= WARN_HOURS:
        state = "warn"
    else:
        state = "fresh"
    return {"state": state, "age_hours": age_hours}


def collection_status(db: Session) -> dict:
    """4스트림 집계. 각 스트림 refresh_status(db) 호출 → compute_stream_state 적용."""
    now = kst_now()
    streams = []
    for key, label, getter in _STREAMS:
        st = getter(db)
        derived = compute_stream_state(
            last_success_at=st.get("last_success_at"),
            last_error_at=st.get("last_error_at"),
            requested=bool(st.get("requested")),
            now_kst=now,
        )
        streams.append({
            "key": key,
            "label": label,
            "state": derived["state"],
            "age_hours": derived["age_hours"],
            "last_success_at": st.get("last_success_at"),
            "last_error_at": st.get("last_error_at"),
            "last_error": st.get("last_error"),
        })
    return {"streams": streams, "as_of": now.isoformat()}
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_collection_status.py -q`
Expected: PASS (9 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/app/services/coupang/collection_status.py backend/tests/test_collection_status.py
git commit -m "feat(coupang): 4스트림 수집 신선도 상태 집계 SA(compute_stream_state)"
```

---

## Task 2: 백엔드 — 집계 라우트 `GET /collection-status`

**Files:**
- Modify: `backend/app/routers/coupang_ops.py` (rocket refresh-status 엔드포인트 부근에 추가)
- Test: `backend/tests/test_collection_status.py` (라우트 통합 테스트 추가)

- [ ] **Step 1: 실패 테스트 추가**

`backend/tests/test_collection_status.py` 하단에 추가:
```python
def test_collection_status_route_shape(client):
    # client = 기존 conftest TestClient 픽스처
    r = client.get("/api/coupang/ops/collection-status")
    assert r.status_code == 200
    body = r.json()
    assert "streams" in body and "as_of" in body
    keys = {s["key"] for s in body["streams"]}
    assert keys == {"ofix_sales", "ofix_ad", "ohitech_ad", "supplier_hub"}
    for s in body["streams"]:
        assert s["state"] in {"fresh", "warn", "critical", "failed", "in_flight"}
```
(주의: `client` 픽스처명은 conftest에 맞춰 조정. 없으면 `TestClient(app)` 직접 생성.)

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_collection_status.py::test_collection_status_route_shape -q`
Expected: FAIL — 404

- [ ] **Step 3: 라우트 구현**

`backend/app/routers/coupang_ops.py`의 rocket `refresh-status` 엔드포인트 근처(약 `:1462` 이후)에 추가. import는 파일 상단 기존 `from app.services.coupang import ...`에 `collection_status`가 없으면 추가:
```python
@router.get("/collection-status")
def coupang_collection_status(db: Session = Depends(get_db)):
    """쿠팡 4개 브라우저 수집 스트림(ofix 판매/광고, ohitech 광고, 로켓 발주)의
    신선도·실패 상태 집계. 전역 신선도 배너 전용(60s 폴). 자동 트리거 제거 후
    '낡음/실패'를 가시화하는 유일 경로."""
    from app.services.coupang import collection_status as _cs
    return _cs.collection_status(db)
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_collection_status.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/app/routers/coupang_ops.py backend/tests/test_collection_status.py
git commit -m "feat(coupang): GET /collection-status 집계 라우트"
```

---

## Task 3: 백엔드 — ofix 광고비 자동 갱신 크론 제거 + prod 행 삭제 마이그

**Files:**
- Modify: `backend/app/services/scheduler_service.py` (`:1160` defaults, `:1383` job map, `:934` 함수)
- Create: `backend/alembic/versions/<rev>_drop_request_ad_cost_refresh_job.py`
- Test: `backend/tests/test_no_ad_cost_refresh_cron.py`

- [ ] **Step 1: 실패 테스트 작성**

`backend/tests/test_no_ad_cost_refresh_cron.py`:
```python
# test_no_ad_cost_refresh_cron.py — 자동 트리거 제거 회귀 가드(D: 순수 on-demand).
#   request_ad_cost_refresh 크론이 defaults·job map·함수에서 완전히 빠졌는지 고정.
import inspect
from app.services import scheduler_service as ss

def test_request_ad_cost_refresh_not_in_defaults():
    src = inspect.getsource(ss._ensure_default_states)
    assert "request_ad_cost_refresh" not in src

def test_request_ad_cost_refresh_not_in_job_map():
    assert ss.job_func_for("request_ad_cost_refresh") is None

def test_request_ad_cost_refresh_func_removed():
    assert not hasattr(ss, "request_ad_cost_refresh_job")
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_no_ad_cost_refresh_cron.py -q`
Expected: FAIL (3건)

- [ ] **Step 3: 코드 제거**

`scheduler_service.py`:
1. `:1160` defaults에서 아래 라인과 바로 위 주석 3줄(`# 03:00 야간 브릿지 추가` 블록) 삭제:
   ```python
   ("request_ad_cost_refresh", "0 3,10-20 * * *"),
   ```
2. `:1383` job map에서 삭제:
   ```python
   "request_ad_cost_refresh": request_ad_cost_refresh_job,
   ```
3. `:934` `def request_ad_cost_refresh_job(): ...` 함수 전체(docstring 포함) 삭제.

- [ ] **Step 4: 마이그레이션 작성**

Run: `cd backend && .venv/bin/alembic revision -m "drop request_ad_cost_refresh job"` → 생성된 파일에:
```python
"""drop request_ad_cost_refresh job

prod SchedulerState에 이미 시드된 request_ad_cost_refresh 행을 제거(순수 on-demand 전환).
등록은 SchedulerState 행에서 이뤄지므로 defaults 제거만으론 prod에서 안 사라진다.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "<자동>"
down_revision = "<직전 head 자동>"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DELETE FROM scheduler_state WHERE job_name = 'request_ad_cost_refresh'")


def downgrade():
    op.execute(
        "INSERT INTO scheduler_state (job_name, cron_expression, is_enabled) "
        "VALUES ('request_ad_cost_refresh', '0 3,10-20 * * *', 1)"
    )
```
(테이블/컬럼명은 `SchedulerState.__tablename__` 확인 후 맞출 것 — `scheduler_state` 가정.)

- [ ] **Step 5: 통과 확인 + 마이그 적용**

Run: `cd backend && .venv/bin/pytest tests/test_no_ad_cost_refresh_cron.py tests/test_alembic_revision_integrity.py -q && .venv/bin/alembic upgrade head`
Expected: PASS + 마이그 적용(로컬 DB)

- [ ] **Step 6: 커밋**

```bash
git add backend/app/services/scheduler_service.py backend/alembic/versions/ backend/tests/test_no_ad_cost_refresh_cron.py
git commit -m "feat(scheduler): ofix 광고비 자동 갱신 크론 제거(순수 on-demand) + prod 행 삭제 마이그"
```

---

## Task 4: 데몬 — rocket 23h 자동 실행 제거

**Files:**
- Modify: `tools/rocket_supplier_fetcher.py` `cmd_poll` (`:762-777` "일별 정기 실행" 블록)

> 데몬 while-루프는 단위 테스트 대상이 아님. 검증 = ①삭제 후 grep 확인 ②버튼 경로(`st.get("requested")` → claim → run) 불변 육안 확인 ③실행 단계에서 라이브 관찰.

- [ ] **Step 1: 블록 제거**

`cmd_poll`에서 아래 블록 전체 삭제(버튼 요청 처리만 남긴다):
```python
            # 일별 정기 실행 (마지막 성공 23h 초과)
            if not needs_run:
                now = _time.time()
                suc_iso = st.get("last_success_at")
                if suc_iso:
                    import datetime as _dt
                    suc_epoch = _dt.datetime.fromisoformat(suc_iso).timestamp()
                    if (now - suc_epoch) > _DAILY_HOURS * 3600:
                        log.info("[poll] last_success_at %s → 23h 초과 → 자동 실행", suc_iso)
                        needs_run = True
                elif (now - last_run_at) > _DAILY_HOURS * 3600:
                    # 아직 성공 기록 없음 → 24h 이상 실행 안 했으면 1회
                    log.info("[poll] last_success_at 없음 → 첫 자동 실행")
                    needs_run = True
```
또한 이제 미사용이 되는 `_DAILY_HOURS`, `last_run_at`을 제거하고, docstring/로그 문구를 "버튼 요청 시에만 실행"으로 정정:
- `docstring` ②줄 "② last_success_at이 23시간 이상 오래됐으면 자동 run" 삭제.
- `log.info("[poll] 시작 — 30초마다 갱신 요청 체크 + 23시간 마다 자동 실행")` → `log.info("[poll] 시작 — 30초마다 갱신 요청(버튼)만 체크·실행")`.
- `needs_run` 이후 `last_run_at = _time.time()` 라인 삭제.

- [ ] **Step 2: 문법·잔재 확인**

Run: `cd "$(git rev-parse --show-toplevel)" && python3 -c "import ast; ast.parse(open('tools/rocket_supplier_fetcher.py').read()); print('ok')" && ! grep -n "_DAILY_HOURS\|23시간 마다\|일별 정기 실행" tools/rocket_supplier_fetcher.py && echo "clean"`
Expected: `ok` + `clean`

- [ ] **Step 3: 커밋**

```bash
git add tools/rocket_supplier_fetcher.py
git commit -m "feat(rocket-daemon): 23h 자동 실행 제거 — 버튼 요청만 처리(on-demand)"
```

---

## Task 5: 데몬 — ohitech "daily" 자동 트리거 제거

**Files:**
- Modify: `tools/ohitech_ad_fetcher.py` `cmd_poll` (`:551-573` 트리거 계산 블록)

- [ ] **Step 1: 트리거 계산 단순화**

`cmd_poll` while-루프에서 트리거 계산부를:
```python
            st = _prod_refresh_status(cfg)
            net_fails = 0
            trigger = None  # "button" | "daily"
            if st.get("requested"):
                trigger = "button"
            else:
                suc = st.get("last_success_at")
                if suc:
                    ...
                    if age_s > daily_hours * 3600:
                        trigger = "daily"
                elif (_time.time() - last_auto) > daily_hours * 3600:
                    trigger = "daily"  # 성공 기록 없음 → 첫 자동 실행
```
아래로 교체(버튼만):
```python
            st = _prod_refresh_status(cfg)
            net_fails = 0
            trigger = "button" if st.get("requested") else None  # 자동 트리거 제거(on-demand)
```
그리고 미사용이 되는 `daily_hours`, `last_auto`, 실행 블록의 `last_auto = _time.time()` 라인과 `trigger == "daily"` 관련 분기·로그 문구를 정리(버튼 전용). docstring ②줄과 시작 `log.info` 문구도 "버튼 요청만"으로 정정.

- [ ] **Step 2: 문법·잔재 확인**

Run: `cd "$(git rev-parse --show-toplevel)" && python3 -c "import ast; ast.parse(open('tools/ohitech_ad_fetcher.py').read()); print('ok')" && ! grep -n '"daily"\|daily_hours\|last_auto' tools/ohitech_ad_fetcher.py && echo "clean"`
Expected: `ok` + `clean`

- [ ] **Step 3: 커밋**

```bash
git add tools/ohitech_ad_fetcher.py
git commit -m "feat(ohitech-daemon): daily 자동 트리거 제거 — 버튼 요청만 처리(on-demand)"
```

---

## Task 6: 프론트 — api.ts 타입 + `getCollectionStatus()`

**Files:**
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: 타입 + 래퍼 추가**

`frontend/src/lib/api.ts` 하단에:
```typescript
// 쿠팡 4스트림 수집 신선도(전역 배너 전용). 자동 트리거 제거 후 '낡음/실패' 가시화 유일 경로.
export type CollectionState = "fresh" | "warn" | "critical" | "failed" | "in_flight";
export interface CollectionStreamStatus {
  key: "ofix_sales" | "ofix_ad" | "ohitech_ad" | "supplier_hub";
  label: string;
  state: CollectionState;
  age_hours: number | null;
  last_success_at: string | null;
  last_error_at: string | null;
  last_error: string | null;
}
export interface CollectionStatus {
  streams: CollectionStreamStatus[];
  as_of: string;
}
export function getCollectionStatus(): Promise<CollectionStatus> {
  return fetchApi<CollectionStatus>("/api/coupang/ops/collection-status");
}
```

- [ ] **Step 2: 타입체크**

Run: `cd frontend && npx tsc -b`
Expected: 에러 없음

- [ ] **Step 3: 커밋**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(fe): getCollectionStatus API 래퍼 + 타입"
```

---

## Task 7: 프론트 — 순수 배너 빌더 `buildCollectionFreshnessBanner`

**Files:**
- Create: `frontend/src/components/collectionFreshnessBanner.ts`
- Test: `frontend/src/components/collectionFreshnessBanner.test.ts`

- [ ] **Step 1: 실패 테스트 작성**

`frontend/src/components/collectionFreshnessBanner.test.ts`:
```typescript
// collectionFreshnessBanner.test.ts — 전역 수집 신선도 배너 빌더 가드.
//   ★존재 이유: 자동 트리거 제거 후 '잊어버림→낡음'과 '로그인 깨짐'을 유일하게 가시화.
import { describe, it, expect } from "vitest";
import { buildCollectionFreshnessBanner } from "./collectionFreshnessBanner";
import type { CollectionStatus, CollectionStreamStatus } from "../lib/api";

function s(over: Partial<CollectionStreamStatus>): CollectionStreamStatus {
  return { key: "ofix_ad", label: "ofix 광고비", state: "fresh", age_hours: 1,
    last_success_at: null, last_error_at: null, last_error: null, ...over };
}
const wrap = (streams: CollectionStreamStatus[]): CollectionStatus => ({ streams, as_of: "" });

describe("buildCollectionFreshnessBanner", () => {
  it("전부 fresh/in_flight면 null(숨김)", () => {
    expect(buildCollectionFreshnessBanner(wrap([s({ state: "fresh" }), s({ state: "in_flight" })]))).toBeNull();
  });
  it("warn만 있으면 severity yellow", () => {
    const b = buildCollectionFreshnessBanner(wrap([s({ state: "warn", age_hours: 30 })]));
    expect(b?.severity).toBe("yellow");
    expect(b?.items[0].kind).toBe("stale");
  });
  it("critical 있으면 severity red", () => {
    const b = buildCollectionFreshnessBanner(wrap([s({ state: "warn" }), s({ state: "critical", age_hours: 60 })]));
    expect(b?.severity).toBe("red");
  });
  it("failed는 red + kind failed", () => {
    const b = buildCollectionFreshnessBanner(wrap([s({ state: "failed", key: "supplier_hub", label: "로켓 발주/정산" })]));
    expect(b?.severity).toBe("red");
    expect(b?.items[0].kind).toBe("failed");
  });
  it("stale 항목 텍스트에 경과일/시간 포함", () => {
    const b = buildCollectionFreshnessBanner(wrap([s({ state: "critical", age_hours: 50, label: "ofix 광고비" })]));
    expect(b?.items[0].text).toContain("ofix 광고비");
    expect(b?.items[0].text).toContain("지남");
  });
  it("null/undefined 입력 방어 → null(크래시 금지)", () => {
    expect(buildCollectionFreshnessBanner(null as unknown as CollectionStatus)).toBeNull();
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `cd frontend && npx vitest run src/components/collectionFreshnessBanner.test.ts`
Expected: FAIL — 모듈 없음

- [ ] **Step 3: 구현**

`frontend/src/components/collectionFreshnessBanner.ts`:
```typescript
// collectionFreshnessBanner.ts — 전역 수집 신선도 배너의 순수 빌더(표시 로직만, React 무관).
//   fresh/in_flight는 숨김. warn=🟡, critical/failed=🔴. stale(안 눌러 낡음) vs failed(눌렀는데 실패) 구분.
import type { CollectionStatus, CollectionStreamStatus } from "../lib/api";

export interface FreshnessBannerItem {
  key: string;
  label: string;
  kind: "stale" | "failed";
  text: string;
}
export interface FreshnessBanner {
  severity: "yellow" | "red";
  items: FreshnessBannerItem[];
}

function ageText(hours: number | null): string {
  if (hours == null) return "수집 기록 없음";
  if (hours >= 48) return `${Math.floor(hours / 24)}일 지남`;
  if (hours >= 24) return "1일 지남";
  return `${Math.floor(hours)}시간 지남`;
}

export function buildCollectionFreshnessBanner(
  status: CollectionStatus | null | undefined,
): FreshnessBanner | null {
  if (!status || !Array.isArray(status.streams)) return null;
  const items: FreshnessBannerItem[] = [];
  for (const st of status.streams as CollectionStreamStatus[]) {
    if (st.state === "failed") {
      items.push({ key: st.key, label: st.label, kind: "failed",
        text: `${st.label} 갱신 실패 · 로그인 필요` });
    } else if (st.state === "warn" || st.state === "critical") {
      items.push({ key: st.key, label: st.label, kind: "stale",
        text: `${st.label} ${ageText(st.age_hours)}` });
    }
  }
  if (items.length === 0) return null;
  const hasRed = items.some((i) => i.kind === "failed") ||
    (status.streams.some((st) => st.state === "critical"));
  return { severity: hasRed ? "red" : "yellow", items };
}
```

- [ ] **Step 4: 통과 확인**

Run: `cd frontend && npx vitest run src/components/collectionFreshnessBanner.test.ts`
Expected: PASS (6 passed)

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/components/collectionFreshnessBanner.ts frontend/src/components/collectionFreshnessBanner.test.ts
git commit -m "feat(fe): 전역 수집 신선도 배너 순수 빌더 + 테스트"
```

---

## Task 8: 프론트 — Layout에 배너 렌더 + 60s 폴

**Files:**
- Modify: `frontend/src/components/Layout.tsx`

- [ ] **Step 1: 상태 + 폴 추가**

`Layout.tsx` 컴포넌트 안, 기존 `health` state 근처에:
```typescript
const [collection, setCollection] = useState<CollectionStatus | null>(null);
```
useEffect 폴(기존 health 폴 패턴 미러링, 60s):
```typescript
useEffect(() => {
  let cancelled = false;
  const tick = () => {
    getCollectionStatus()
      .then((c) => { if (!cancelled) setCollection(c); })
      .catch(() => { /* fail-safe: 배너만 미표시, 앱 크래시 금지 */ });
  };
  tick();
  const id = setInterval(tick, 60000);
  return () => { cancelled = true; clearInterval(id); };
}, []);
```
import에 `getCollectionStatus`, `CollectionStatus`, `buildCollectionFreshnessBanner` 추가.

- [ ] **Step 2: 배너 렌더**

기존 파이프라인 헬스 배너 렌더 근처에, 수집 신선도 배너 JSX 추가(색: yellow=amber, red=rose). 각 item 클릭 → 종합조망으로 이동:
```tsx
{(() => {
  const banner = buildCollectionFreshnessBanner(collection);
  if (!banner) return null;
  const cls = banner.severity === "red"
    ? "bg-rose-50 border-rose-300 text-rose-800"
    : "bg-amber-50 border-amber-300 text-amber-800";
  return (
    <div className={`border px-3 py-2 text-sm ${cls}`}>
      <span className="font-semibold">수집 신선도</span>{" · "}
      {banner.items.map((it, i) => (
        <span key={it.key}>
          {i > 0 && " / "}
          <Link to="/coupang/command-center" className="underline">{it.text}</Link>
        </span>
      ))}
    </div>
  );
})()}
```
(`Link`가 이미 import돼 있지 않으면 `react-router-dom`에서 추가. command-center 경로는 실제 라우트로 확인.)

- [ ] **Step 3: 타입체크 + 빌드**

Run: `cd frontend && npx tsc -b && npm run build`
Expected: 성공

- [ ] **Step 4: 커밋**

```bash
git add frontend/src/components/Layout.tsx
git commit -m "feat(fe): 전역 수집 신선도 배너 Layout 렌더 + 60s 폴(fail-safe)"
```

---

## Task 9: 전체 검증

- [ ] **Step 1: 백엔드 전체 스위트**

Run: `cd backend && .venv/bin/pytest -q`
Expected: 전부 PASS(회귀 0). 실패 시 해당 테스트 수정 후 재실행.

- [ ] **Step 2: 프론트 전체**

Run: `cd frontend && npx vitest run && npx tsc -b`
Expected: 전부 PASS.

- [ ] **Step 3: 데몬 문법 최종 확인**

Run: `cd "$(git rev-parse --show-toplevel)" && python3 -c "import ast; ast.parse(open('tools/rocket_supplier_fetcher.py').read()); ast.parse(open('tools/ohitech_ad_fetcher.py').read()); print('daemons ok')"`

- [ ] **Step 4: codex 교차 리뷰(원칙 19)**

Run: `/codex review` — 지적 사항 대화형 검증(원칙 19). pass까지 반영.

---

## Task 10: 배포 + 라이브 검증 + 핸드오프

> 배포는 반드시 `scripts/safe_deploy.sh`(D-NAO-49, 직접 scp 금지). 데몬 코드는 Mac 로컬이므로 `launchctl kickstart -k`로 재시작.

- [ ] **Step 1: PR 생성 + main 병합** (또는 프로젝트 배포 흐름)

- [ ] **Step 2: 백엔드 배포 + 마이그**

Run: `scripts/safe_deploy.sh backend/app/services/coupang/collection_status.py backend/app/routers/coupang_ops.py backend/app/services/scheduler_service.py --restart` + prod `alembic upgrade head`. (배포 스크립트가 마이그 포함 여부 확인.)

- [ ] **Step 3: prod 라이브 검증(원칙 22 — 라이브 증거)**

- `GET /api/coupang/ops/collection-status` 실제 응답에 4스트림·state 정상.
- prod `SchedulerState`에 `request_ad_cost_refresh` 행 **부재** 확인(마이그 적용됨).
- 프론트에서 전역 배너가 상태에 맞게 표시(낡은 스트림 있으면 뜸, 없으면 숨김).

- [ ] **Step 4: 데몬 재배포 + 재시작(Mac 로컬)**

`tools/rocket_supplier_fetcher.py`·`ohitech_ad_fetcher.py`는 Mac 로컬 실행 → 파일 반영 후:
```bash
launchctl kickstart -k gui/$(id -u)/com.ohisell.rocket
launchctl kickstart -k gui/$(id -u)/com.ohisell.ohitech-ad
```
로그에 시작 문구가 "버튼 요청만" 으로 바뀌었는지 확인.

- [ ] **Step 5: 자동 창 미발생 확인(핵심 목표)**

한동안(예: 23h 지난 스트림이 있는 상태에서) 데몬 로그에 **자동 run이 발생하지 않음** 확인. 버튼 클릭 시에만 창/run 발생.

- [ ] **Step 6: 핸드오프 갱신(신선도 유지)**

- `claude-progress.txt` 갱신(현재 상태·다음 액션).
- `.claude/memory/HANDOFF_*.md` 작성(archive-session).
- `docs/TRACKS.md`는 이 작업이 naver 트랙 외이므로 별도 표기 불필요(스펙/플랜 링크만 progress에).
- Failure Memory: 배포 중 이슈 있었으면 `failures.jsonl` 기록.

---

## Self-Review (작성자 체크)

- **Spec coverage:** 스펙 §3-1(제거 3) → Task 3/4/5. §3-3(집계 엔드포인트) → Task 1/2. §3-4(전역 배너+버튼 옆 상세) → Task 7/8. §4(에러/fail-safe) → Task 7(null 방어)·8(catch). §5(테스트) → 각 Task 테스트 + Task 9. §6(롤백/safe_deploy) → Task 10. ✅
  - ⚠️ 스펙 §3-4 "각 버튼 옆 N일 지남 상세"는 Task 8이 전역 배너까지만 다룸. **버튼 옆 텍스트는 배너로 충분(전역 배너가 스트림별 경과 표기)하므로 YAGNI 적용해 생략**. 필요 시 후속.
- **Placeholder scan:** 코드 스텝 모두 실제 코드 포함. 마이그 revision id는 alembic 자동 생성(플레이스홀더 아님). ✅
- **Type consistency:** `CollectionStatus`/`CollectionStreamStatus`/`CollectionState`가 api.ts(Task 6)→빌더(Task 7)→Layout(Task 8) 일관. state 리터럴 5종 백엔드(Task 1)와 프론트(Task 6) 일치. ✅
