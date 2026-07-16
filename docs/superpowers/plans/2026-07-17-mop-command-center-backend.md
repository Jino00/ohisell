# MOP 커맨드 센터 — 백엔드 구현 계획 (D-NAO-47 Phase 1/2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 우리 MOP가 "무엇을 왜 바꿨는지"를 기록·조회할 수 있게 만든다 — 지금은 `entity_sync.py:182`가 입찰가를 덮어써서 변경 이력이 **아예 존재하지 않는다**(prod `naver_change_log` 전체 17행, 우리 자동 입찰변경 0건).

**Architecture:** SA(단일 책임) → Harness 없이 라우터 단순 read 3개 추가 + `entity_sync` SA에 diff 밸브 1개. 기존 `_log_external_status_change`(status 전용)의 **대칭 함수**로 `_log_external_bid_change`를 만들어 같은 호출 지점에 배선한다. 쓰기 경로는 건드리지 않는다(전부 읽기 전용 + 로깅 1개).

**Tech Stack:** Python 3.11 · FastAPI · SQLAlchemy 2.x(Mapped) · SQLite(prod `/home/ubuntu/ohisell/backend/ohisell.db`) · pytest

**선행 문서:** `docs/superpowers/specs/2026-07-17-mop-command-center-design.md` (§8 디자인 시스템 · §9 라이브 확인). 트랙: `docs/tracks/active/track_naver-ad-optimization.md` D-NAO-47.

**Phase 2(프론트)는 별도 계획서**: `docs/superpowers/plans/2026-07-17-mop-command-center-frontend.md` — 이 계획이 만드는 API를 소비한다. **이 계획이 codex PASS + 배포된 뒤에 착수한다.**

---

## ⚠️ 착수 전 필독 — 이 계획의 유일한 진짜 위험

**`entity_sync.sync_entities()`는 매일 07:35 도는 프로덕션 크론이고, `naver_entity`는 91,005행(키워드)이다.**

diff 로깅을 순진하게 달면 **매일 91,005행이 `naver_change_log`에 쌓인다**(현재 전체 17행인 테이블에). 이건 DB를 죽인다.

가드는 **"입찰가가 실제로 바뀐 행만 로깅"** 하나이고, 그게 무너지는 경로는 **타입 불일치**다:

- `NaverEntity.bid_amt` = `Integer, nullable=True` (SQLAlchemy 선언)
- fetcher는 `k.get("bidAmt")`를 **네이버 API 응답 그대로** 넘긴다(`naver_sa_ad_fetcher.py:505`) — 파싱·캐스팅 없음
- **SQLite는 동적 타입**이라 `Integer` 컬럼에 `"700"`(str)이 들어가도 조용히 저장된다

→ DB에서 읽은 값이 `700`(int)이고 API가 준 값이 `"700"`(str)이면 `700 != "700"` → **매 행이 "변경됨"으로 판정 → 91,005행/일.**

**그래서 비교는 반드시 `_norm_bid()`로 정규화한 뒤에 한다(Task 1).** 이 함수가 이 계획서에서 가장 중요한 10줄이다.

---

## File Structure

| 파일 | 책임 | 신규/수정 |
|---|---|---|
| `backend/app/services/naver_ad/entity_sync.py` | 엔티티 동기화 SA. **`_norm_bid` + `_log_external_bid_change` 추가**(`_log_external_status_change`의 대칭) | 수정 |
| `backend/app/services/naver_ad/proposal_writer.py` | 제안 생성 SA. **`ALL_PROPOSAL_TYPES` 상수 추가**(드리프트 방지 단일 진실) | 수정 |
| `backend/app/routers/naver_ad.py` | 라우터. **`GET /change-log` + `GET /raw/{keywords,search-terms,hourly}` 추가 · `_serialize_proposal` 보강** | 수정 |
| `backend/tests/test_naver_ad_entity_bid_valve.py` | 밸브 단위 테스트(★핵심 — 쓰기 폭증 회귀 방지) | 신규 |
| `backend/tests/test_naver_ad_change_log_router.py` | change-log API HTTP 왕복 | 신규 |
| `backend/tests/test_naver_ad_raw_router.py` | 원자료 API HTTP 왕복(페이지네이션 상한 포함) | 신규 |
| `backend/tests/test_naver_ad_proposals_router.py` | `_serialize_proposal` 보강 검증 **추가**(기존 파일) | 수정 |
| `backend/tests/test_naver_ad_p2s1.py` | 기존 entity_sync 테스트 — 회귀 확인용, 수정 없음 | 불변 |

**규약(기존 코드베이스 관례 — 따를 것):**
- `tests/conftest.py`는 **없다.** 각 테스트 파일이 자기 `db` fixture를 정의한다(in-memory SQLite + `StaticPool`).
- 라우터 테스트는 `TestClient` + `app.dependency_overrides[get_db]` 패턴(`test_naver_ad_proposals_router.py:18-34`).
- 시각은 `app.utils.kst.kst_now()` / `kst_today()`. **`datetime.now()` 금지** — `server_default=func.now()`는 UTC라 KST와 9시간 어긋난다(메모리: `sqlite-server-default-now-is-utc`).

**테스트 실행:** `cd backend && pytest tests/<file> -v`. 전체 회귀: `cd backend && pytest -q` (현재 902 pass 기준 — 이 계획 후 증가만 허용, 감소 0).

---

## Task 1: 입찰가 정규화 `_norm_bid` (밸브의 안전장치)

**Files:**
- Modify: `backend/app/services/naver_ad/entity_sync.py` (`_status` 함수 아래, 21-27행 근처)
- Test: `backend/tests/test_naver_ad_entity_bid_valve.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_naver_ad_entity_bid_valve.py` 신규 생성:

```python
# test_naver_ad_entity_bid_valve.py — D-NAO-47 Task1~2: entity_sync 입찰가 diff 밸브
# ★이 파일의 존재 이유: sync_entities는 매일 07:35 도는 크론이고 naver_entity는 91,005행이다.
#   "무변동 행 미로깅" 가드가 무너지면 매일 91,005행이 naver_change_log에 쌓여 DB가 죽는다.
#   타입 불일치(API가 "700"(str), DB가 700(int))가 그 가드를 무너뜨리는 유일한 경로라
#   _norm_bid 정규화를 반드시 거친다.
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverChangeLog, NaverEntity
from app.services.naver_ad import entity_sync
from app.utils.kst import kst_now


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


# ── Task 1: _norm_bid ──
def test_norm_bid_normalizes_str_and_int_to_same_value():
    """★API가 "700"(str), DB가 700(int)이어도 같은 값으로 봐야 한다.
    이게 깨지면 91,005행이 매일 '변경됨'으로 오판정된다."""
    assert entity_sync._norm_bid(700) == entity_sync._norm_bid("700") == 700


def test_norm_bid_handles_none_and_empty():
    assert entity_sync._norm_bid(None) is None
    assert entity_sync._norm_bid("") is None


def test_norm_bid_handles_float_and_decimal_string():
    """네이버가 700.0 같은 실수를 줘도 int로 접는다."""
    assert entity_sync._norm_bid(700.0) == 700
    assert entity_sync._norm_bid("700.0") == 700


def test_norm_bid_returns_none_on_garbage_rather_than_raising():
    """파싱 불가 값은 예외 대신 None — 크론이 죽으면 안 된다(fail-safe)."""
    assert entity_sync._norm_bid("N/A") is None
    assert entity_sync._norm_bid({}) is None
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && pytest tests/test_naver_ad_entity_bid_valve.py -v`
Expected: FAIL — `AttributeError: module 'app.services.naver_ad.entity_sync' has no attribute '_norm_bid'`

- [ ] **Step 3: 최소 구현**

`backend/app/services/naver_ad/entity_sync.py` — `_status()` 함수 **바로 아래**(27행 다음)에 추가:

```python
def _norm_bid(v) -> int | None:
    """입찰가를 int|None으로 정규화 — diff 비교 전 **반드시** 통과시킨다.

    ★왜 필요한가(원칙22, 실측): NaverEntity.bid_amt는 Integer 선언이지만 SQLite는 동적
    타입이라 fetcher가 네이버 API 응답을 그대로 넘긴 값(str일 수 있음 — naver_sa_ad_fetcher
    :505는 k.get("bidAmt")를 캐스팅 없이 전달)이 그대로 저장된다. 정규화 없이 비교하면
    700(DB, int) != "700"(API, str)이 되어 **매일 91,005개 키워드 전부가 '입찰 변경'으로
    오판정**되고 naver_change_log에 91,005행/일이 쌓인다(현재 전체 17행).

    파싱 불가 값은 예외 대신 None을 반환한다 — 이 함수는 매일 07:35 크론 경로에서 91,005번
    호출되므로 쓰레기 값 하나가 동기화 전체를 죽이면 안 된다(fail-safe). None은 호출부에서
    '비교 불가 → 로깅 안 함'으로 처리된다.
    """
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && pytest tests/test_naver_ad_entity_bid_valve.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
cd backend && git add app/services/naver_ad/entity_sync.py tests/test_naver_ad_entity_bid_valve.py
git commit -m "feat(naver-ad): D-NAO-47 T1 _norm_bid — 입찰가 diff 비교용 타입 정규화 (쓰기폭증 방지)"
```

---

## Task 2: `_log_external_bid_change` 밸브 + 배선

**Files:**
- Modify: `backend/app/services/naver_ad/entity_sync.py` (`_log_external_status_change` 아래 + `sync_entities` 175-182행)
- Test: `backend/tests/test_naver_ad_entity_bid_valve.py` (Task 1에서 만든 파일에 추가)

**설계 근거 (기존 코드 대칭성 — 반드시 읽을 것):**

`_log_external_status_change`(entity_sync.py:83-146)가 이미 확립한 계약을 그대로 따른다:
1. **호출 시점이 중요하다.** `e.synced_at = now`로 **덮어쓰기 전에** 호출된다 — 함수 안에서 `entity.synced_at`을 "직전 관측 시각"으로 읽기 때문. `_log_external_bid_change`도 `e.bid_amt = ...` **대입 전에** 호출해야 `entity.bid_amt`가 옛값이다.
2. **우리 쓰기 귀속 판별**: `change_log`에서 우리의 마지막 성공 쓰기를 찾아, 그게 직전 관측 이후이고 방향이 일치하면 "외부 변경 아님"으로 스킵.
3. **`after_value`의 키는 camelCase다.** writer가 네이버 API 재조회 결과(`get_keyword()`)를 그대로 `json.dumps` 하기 때문(`naver_sa_writer.py:350` → `after=after`). status는 `userLock`, 입찰가는 **`bidAmt`**. `bid_amt`(snake)가 아니다.
4. **우리 bid 쓰기의 `action`은 `"update_bid"`**(`naver_execution_harness.py:558,582`) — `update_keyword_bid`가 아니다(그건 writer 내부 WriteResult.action).

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_naver_ad_entity_bid_valve.py` **끝에 추가**:

```python
# ── Task 2: _log_external_bid_change 밸브 ──
def _seed_entity(db, *, bid_amt, status="on", synced_at=None):
    e = NaverEntity(
        entity_type="keyword", entity_id="nkw-1", parent_id="grp-1",
        campaign_id="cmp-1", campaign_type="WEB_SITE", name="필름",
        status=status, bid_amt=bid_amt, synced_at=synced_at or kst_now(),
    )
    db.add(e)
    db.commit()
    return e


def _rows(db, *, adgroup_bid=None, keyword_bid=700, status="ELIGIBLE"):
    """sync_entities에 넣을 rows — 키워드 1개짜리 최소 형태."""
    return [{
        "entity_type": "keyword", "entity_id": "nkw-1", "parent_id": "grp-1",
        "campaign_id": "cmp-1", "campaign_type": "WEB_SITE", "name": "필름",
        "status": "on", "bid_amt": keyword_bid,
    }]


def test_no_log_when_bid_unchanged(db):
    """★핵심 회귀 테스트: 무변동 = 로깅 0. 이게 깨지면 91,005행/일."""
    _seed_entity(db, bid_amt=700)
    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=700))
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").count() == 0


def test_no_log_when_bid_unchanged_across_types(db):
    """★타입만 다르고 값이 같으면 무변동이다 — DB 700(int) vs API "700"(str)."""
    _seed_entity(db, bid_amt=700)
    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid="700"))
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").count() == 0


def test_logs_when_bid_actually_changed(db):
    _seed_entity(db, bid_amt=700)
    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=900))

    logs = db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").all()
    assert len(logs) == 1
    assert json.loads(logs[0].before_value) == {"bidAmt": 700}
    assert json.loads(logs[0].after_value) == {"bidAmt": 900}
    assert logs[0].entity_id == "nkw-1"
    assert logs[0].campaign_id == "cmp-1"
    assert logs[0].dry_run is False


def test_bid_amt_is_actually_persisted_after_change(db):
    """밸브를 달아도 기존 동작(입찰가 갱신)은 그대로여야 한다."""
    _seed_entity(db, bid_amt=700)
    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=900))
    e = db.query(NaverEntity).filter(NaverEntity.entity_id == "nkw-1").one()
    assert e.bid_amt == 900


def test_no_log_when_previous_bid_is_none(db):
    """신규 관측(옛값 없음)은 '변경'이 아니다."""
    _seed_entity(db, bid_amt=None)
    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=700))
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").count() == 0


def test_no_log_when_new_bid_is_none(db):
    """수집 누락(새값 없음)도 '변경'이 아니다 — 이걸 로깅하면 API 장애 시 91k행이 쏟아진다."""
    _seed_entity(db, bid_amt=700)
    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=None))
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").count() == 0


def test_no_log_for_deleted_entity(db):
    _seed_entity(db, bid_amt=700, status="deleted")
    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=900))
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").count() == 0


def test_skips_log_when_change_is_our_own_recent_write(db):
    """우리가 방금 바꾼 건 '외부 변경'이 아니다 — _log_external_status_change와 동일 계약.
    after_value의 키는 camelCase 'bidAmt'(writer가 네이버 재조회 응답을 그대로 dumps)."""
    prev_sync = kst_now() - timedelta(hours=2)
    _seed_entity(db, bid_amt=700, synced_at=prev_sync)
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp-1",
        action="update_bid", dry_run=False,
        changed_at=kst_now() - timedelta(minutes=30),  # 직전 관측 이후
        after_value=json.dumps({"bidAmt": 900}),
    ))
    db.commit()

    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=900))
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").count() == 0


def test_logs_when_our_write_is_older_than_last_observation(db):
    """우리 쓰기가 직전 관측보다 *이전*이면, 방향이 같아도 외부 변경으로 기록한다.
    (status 쪽 원 버그의 대칭 — 우리 인상 → 외부 인하 → 외부 재인상 시퀀스 방어)"""
    prev_sync = kst_now() - timedelta(minutes=10)
    _seed_entity(db, bid_amt=700, synced_at=prev_sync)
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp-1",
        action="update_bid", dry_run=False,
        changed_at=kst_now() - timedelta(hours=5),  # 직전 관측보다 오래됨
        after_value=json.dumps({"bidAmt": 900}),
    ))
    db.commit()

    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=900))
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").count() == 1


def test_dry_run_our_write_does_not_suppress_log(db):
    """dry_run 기록은 실제 쓰기가 아니다 — 억제하면 안 된다."""
    prev_sync = kst_now() - timedelta(hours=2)
    _seed_entity(db, bid_amt=700, synced_at=prev_sync)
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp-1",
        action="update_bid", dry_run=True,  # ← dry
        changed_at=kst_now() - timedelta(minutes=30),
        after_value=json.dumps({"bidAmt": 900}),
    ))
    db.commit()

    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=900))
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").count() == 1


def test_status_and_bid_change_together_produce_two_logs(db):
    """상태와 입찰가가 같이 바뀌면 각각 1행씩 — 두 밸브는 독립이다."""
    _seed_entity(db, bid_amt=700, status="on")
    rows = [{
        "entity_type": "keyword", "entity_id": "nkw-1", "parent_id": "grp-1",
        "campaign_id": "cmp-1", "campaign_type": "WEB_SITE", "name": "필름",
        "status": "off", "bid_amt": 900,
    }]
    entity_sync.sync_entities(db, rows=rows)

    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").count() == 1
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_status_change").count() == 1
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && pytest tests/test_naver_ad_entity_bid_valve.py -v`
Expected: FAIL — `test_logs_when_bid_actually_changed` 등이 `assert 0 == 1`로 실패(밸브가 아직 없어 로깅 0건)

- [ ] **Step 3: 최소 구현 (a) — 밸브 함수 추가**

`backend/app/services/naver_ad/entity_sync.py` — `_log_external_status_change` **바로 아래**(146행 다음, `sync_entities` 위)에 추가:

```python
def _log_external_bid_change(db: Session, entity: NaverEntity, new_bid_raw, now) -> None:
    """D-NAO-47: 입찰가 변경을 change_log에 기록한다 — `_log_external_status_change`의 대칭.

    ★이 함수가 없던 동안 `e.bid_amt = r.get("bid_amt")`가 매일 91,005개 키워드의 어제
    입찰가를 조용히 덮어썼다(스펙 §1-5). 그래서 "우리(또는 MOP)가 CPC를 얼마에서 얼마로
    바꿨나"를 보여줄 데이터가 **아예 존재하지 않았다**(prod change_log 전체 17행 · 우리
    자동 입찰변경 0건).

    ⚠️ 쓰기 폭증 방어(이 함수의 존재 이유의 절반):
      - 무변동 행은 로깅하지 않는다. naver_entity 91,005행 중 절대다수는 매일 그대로다.
      - 비교 전 반드시 `_norm_bid()`로 정규화한다 — 타입 불일치(DB int vs API str) 하나로
        전 행이 '변경됨'이 되어 91,005행/일이 쌓인다(_norm_bid docstring 참조).
      - old/new 어느 쪽이든 None이면 로깅하지 않는다. 신규 관측·수집 누락은 '변경'이 아니고,
        특히 API 장애로 bid_amt가 전부 None이 되면 91,005행이 쏟아진다.

    호출 계약: `e.bid_amt` 대입 **전에** 호출해야 한다(entity.bid_amt가 옛값이어야 함).
    `_log_external_status_change`가 `e.synced_at` 대입 전에 호출되는 것과 같은 이유다.

    우리 쓰기 귀속: `_log_external_status_change`와 동일 — 우리의 마지막 성공 쓰기
    (action="update_bid", dry_run=False, after_value 존재)가 직전 관측(entity.synced_at)
    **이후**이고 그 결과값이 지금 관측값과 같으면 "우리가 방금 한 것"이라 스킵한다.
    방향 일치만으로 판단하지 않는 이유는 _log_external_status_change의 ⚠️ 주석 참조.
    after_value의 키가 camelCase 'bidAmt'인 것은 writer가 네이버 재조회 응답(get_keyword)을
    그대로 json.dumps 하기 때문이다(naver_sa_writer.py:350) — 'bid_amt'(snake)가 아니다.
    """
    if entity.status == "deleted":
        return

    old_bid = _norm_bid(entity.bid_amt)
    new_bid = _norm_bid(new_bid_raw)
    if old_bid is None or new_bid is None:
        return  # 신규 관측/수집 누락/파싱 실패는 변경이 아님
    if old_bid == new_bid:
        return  # ★ 절대다수가 여기서 끊긴다 — 이 한 줄이 쓰기 폭증을 막는다

    last_our_write = (
        db.query(NaverChangeLog)
        .filter(
            NaverChangeLog.entity_type == entity.entity_type,
            NaverChangeLog.entity_id == entity.entity_id,
            NaverChangeLog.action == "update_bid",
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),
        )
        .order_by(NaverChangeLog.changed_at.desc())
        .first()
    )
    if (
        last_our_write
        and last_our_write.after_value
        and entity.synced_at is not None
        and last_our_write.changed_at > entity.synced_at
    ):
        try:
            last_after = json.loads(last_our_write.after_value)
            if isinstance(last_after, dict) and _norm_bid(last_after.get("bidAmt")) == new_bid:
                return
        except (ValueError, TypeError):
            pass

    db.add(NaverChangeLog(
        entity_type=entity.entity_type,
        entity_id=entity.entity_id,
        campaign_id=entity.campaign_id,
        action="external_bid_change",
        proposal_id=None,
        dry_run=False,
        changed_at=now,
        before_value=json.dumps({"bidAmt": old_bid}),
        after_value=json.dumps({"bidAmt": new_bid}),
        rationale="entity_sync 감지: 외부(MOP/사람) 입찰가 변경",
    ))
    log.info("external_bid_change detected: %s %s %s→%s",
             entity.entity_type, entity.entity_id, old_bid, new_bid)
```

- [ ] **Step 4: 최소 구현 (b) — `sync_entities`에 배선**

`backend/app/services/naver_ad/entity_sync.py` — `sync_entities` 안 `else:` 블록(174-183행). **`e.bid_amt = ...` 대입 전**에 호출하는 것이 핵심:

```python
        else:
            if e.status != r["status"] and e.status != "deleted":
                _log_external_status_change(db, e, r["status"], now)
            # ★ e.bid_amt 대입 *전*에 호출 — 함수가 entity.bid_amt를 옛값으로 읽는다(D-NAO-47).
            _log_external_bid_change(db, e, r.get("bid_amt"), now)
            e.parent_id = r["parent_id"]
            e.campaign_id = r["campaign_id"]
            e.campaign_type = r["campaign_type"]
            e.name = r["name"]
            e.status = r["status"]
            e.bid_amt = r.get("bid_amt")
            e.synced_at = now
```

- [ ] **Step 5: 통과 확인**

Run: `cd backend && pytest tests/test_naver_ad_entity_bid_valve.py -v`
Expected: PASS (15 passed)

- [ ] **Step 6: 기존 entity_sync 회귀 확인**

Run: `cd backend && pytest tests/test_naver_ad_p2s1.py -v`
Expected: PASS, 실패 0 (밸브는 additive라 기존 동작 불변)

- [ ] **Step 7: ★쓰기 폭증 실측 가드 — 91k 규모 스모크 테스트**

`backend/tests/test_naver_ad_entity_bid_valve.py` 끝에 추가:

```python
def test_large_sync_with_no_bid_changes_writes_zero_logs(db):
    """★prod 규모 축소판 회귀: 5,000행 무변동 sync → change_log 0행.
    (prod는 91,005행. 이 테스트가 깨지면 크론이 매일 change_log를 폭파한다.)"""
    for i in range(5000):
        db.add(NaverEntity(
            entity_type="keyword", entity_id=f"nkw-{i}", parent_id="grp-1",
            campaign_id="cmp-1", campaign_type="WEB_SITE", name=f"kw{i}",
            status="on", bid_amt=700, synced_at=kst_now(),
        ))
    db.commit()

    rows = [{
        "entity_type": "keyword", "entity_id": f"nkw-{i}", "parent_id": "grp-1",
        "campaign_id": "cmp-1", "campaign_type": "WEB_SITE", "name": f"kw{i}",
        "status": "on", "bid_amt": "700",  # ★str — 타입만 다르고 값은 같다
    } for i in range(5000)]

    entity_sync.sync_entities(db, rows=rows)
    assert db.query(NaverChangeLog).count() == 0
```

Run: `cd backend && pytest tests/test_naver_ad_entity_bid_valve.py::test_large_sync_with_no_bid_changes_writes_zero_logs -v`
Expected: PASS

- [ ] **Step 8: 커밋**

```bash
cd backend && git add app/services/naver_ad/entity_sync.py tests/test_naver_ad_entity_bid_valve.py
git commit -m "feat(naver-ad): D-NAO-47 T2 입찰가 diff 밸브 — 변경 이력 기록 시작 (무변동 미로깅 가드)"
```

---

## Task 3: `GET /api/naver/ad/change-log` — 변경 이력 조회 API

**Files:**
- Modify: `backend/app/routers/naver_ad.py` (`_serialize_proposal` 섹션 위, 진단 섹션 아래 — 파일 끝 `retro-scorecard` 근처에 새 섹션으로 추가)
- Test: `backend/tests/test_naver_ad_change_log_router.py` (신규)

**설계:** D-47-d ②. 순수 read. **1층 "우리 조작 N회" 칸의 데이터 원천**이며, Task 2의 밸브가 채우기 시작한 이력을 화면에 올린다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_naver_ad_change_log_router.py` 신규 생성:

```python
# test_naver_ad_change_log_router.py — D-NAO-47 T3: GET /api/naver/ad/change-log HTTP 왕복
# 원칙22: SA 단위테스트는 라우터를 안 거치므로 라우터 레이어 500을 못 잡는다(P2-S2 사고 전례).
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverChangeLog
from app.utils.kst import kst_now


@pytest.fixture
def client_and_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    session_for_seed = TestingSession()
    yield TestClient(app), session_for_seed
    session_for_seed.close()
    app.dependency_overrides.clear()


@pytest.fixture
def client(client_and_session):
    return client_and_session[0]


@pytest.fixture
def db(client_and_session):
    return client_and_session[1]


def _seed(db, *, action="update_bid", campaign_id="cmp-1", dry_run=False, days_ago=0, outcome=None):
    row = NaverChangeLog(
        entity_type="keyword", entity_id="nkw-1", campaign_id=campaign_id,
        action=action, dry_run=dry_run, changed_at=kst_now() - timedelta(days=days_ago),
        before_value=json.dumps({"bidAmt": 700}), after_value=json.dumps({"bidAmt": 900}),
        rationale="테스트 근거", outcome=outcome,
    )
    db.add(row)
    db.commit()
    return row


def test_change_log_returns_rows_newest_first(client, db):
    _seed(db, days_ago=3)
    _seed(db, days_ago=1)
    r = client.get("/api/naver/ad/change-log")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    assert items[0]["changed_at"] > items[1]["changed_at"]


def test_change_log_parses_before_after_json(client, db):
    _seed(db)
    items = client.get("/api/naver/ad/change-log").json()["items"]
    assert items[0]["before"] == {"bidAmt": 700}
    assert items[0]["after"] == {"bidAmt": 900}
    assert items[0]["action"] == "update_bid"
    assert items[0]["rationale"] == "테스트 근거"


def test_change_log_survives_malformed_json_without_500(client, db):
    """★before_value에 쓰레기가 들어있어도 500이 아니라 null로 흘려보낸다(fail-safe)."""
    row = _seed(db)
    row.before_value = "{not json"
    db.commit()
    r = client.get("/api/naver/ad/change-log")
    assert r.status_code == 200
    assert r.json()["items"][0]["before"] is None


def test_change_log_filters_by_campaign(client, db):
    _seed(db, campaign_id="cmp-1")
    _seed(db, campaign_id="cmp-2")
    items = client.get("/api/naver/ad/change-log?campaign_id=cmp-1").json()["items"]
    assert len(items) == 1
    assert items[0]["campaign_id"] == "cmp-1"


def test_change_log_filters_by_action(client, db):
    _seed(db, action="update_bid")
    _seed(db, action="external_bid_change")
    items = client.get("/api/naver/ad/change-log?action=external_bid_change").json()["items"]
    assert len(items) == 1
    assert items[0]["action"] == "external_bid_change"


def test_change_log_excludes_dry_run_by_default(client, db):
    """★기본값이 중요하다: 1층 '우리 조작 N회'는 실제 집행만 세야 한다.
    dry_run을 섞으면 아무것도 안 했는데 일한 것처럼 보인다(D-47-h 정직성)."""
    _seed(db, dry_run=True)
    _seed(db, dry_run=False)
    items = client.get("/api/naver/ad/change-log").json()["items"]
    assert len(items) == 1
    assert items[0]["dry_run"] is False

    all_items = client.get("/api/naver/ad/change-log?include_dry_run=true").json()["items"]
    assert len(all_items) == 2


def test_change_log_respects_days_window(client, db):
    _seed(db, days_ago=40)
    _seed(db, days_ago=2)
    items = client.get("/api/naver/ad/change-log?days=7").json()["items"]
    assert len(items) == 1


def test_change_log_limit_is_capped(client, db):
    r = client.get("/api/naver/ad/change-log?limit=99999")
    assert r.status_code == 422  # Query(le=500) 위반


def test_change_log_returns_total_for_pagination(client, db):
    for _ in range(3):
        _seed(db)
    body = client.get("/api/naver/ad/change-log?limit=2").json()
    assert body["total"] == 3
    assert len(body["items"]) == 2


def test_change_log_empty_is_200_not_404(client):
    """★빈 상태는 에러가 아니다 — 1층이 '우리 조작 0회'를 정직하게 그려야 한다(D-47-h)."""
    body = client.get("/api/naver/ad/change-log").json()
    assert body == {"items": [], "total": 0}
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && pytest tests/test_naver_ad_change_log_router.py -v`
Expected: FAIL — 404 (엔드포인트 없음)

- [ ] **Step 3: 최소 구현**

`backend/app/routers/naver_ad.py` **파일 끝**에 새 섹션으로 추가:

```python
# ══════════════════════════════════════════════════════════════════
# D-NAO-47 — 변경 이력 조회(change_log) · 커맨드 센터 1층 "우리 조작 N회"의 원천
# ══════════════════════════════════════════════════════════════════
_MAX_CHANGE_LOG_LIMIT = 500


def _loads_or_none(raw: str | None) -> dict | None:
    """change_log의 before/after_value 파싱 — 쓰레기가 들어있어도 500 대신 None.
    (이 테이블은 여러 writer가 각자 dumps 하므로 스키마 보장이 없다.)"""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


@router.get("/change-log")
def get_change_log(
    campaign_id: str | None = Query(None, description="캠페인 필터"),
    action: str | None = Query(None, description="update_bid/external_bid_change/set_user_lock 등"),
    days: int = Query(30, ge=1, le=365, description="changed_at 조회 창(KST 기준)"),
    include_dry_run: bool = Query(False, description="dry-run 기록 포함 여부(기본 제외)"),
    limit: int = Query(100, ge=1, le=_MAX_CHANGE_LOG_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """변경 이력 조회(D-NAO-47, 읽기 전용).

    ★`include_dry_run` 기본 False가 의도적이다: 1층 "우리 조작 N회"는 **실제 집행만** 세야
    한다. dry-run을 섞으면 아무것도 실행하지 않았는데 일한 것처럼 보인다(D-47-h 정직성 —
    0이면 0이라고 말하는 게 이 화면의 일).

    ⚠️ 이 API는 change_log를 **읽기만** 한다. 이력을 *채우는* 것은 entity_sync의 diff 밸브
    (D-NAO-47 T2)와 naver_execution_harness다.
    """
    since = kst_now() - timedelta(days=days)
    q = db.query(NaverChangeLog).filter(NaverChangeLog.changed_at >= since)
    if campaign_id:
        q = q.filter(NaverChangeLog.campaign_id == campaign_id)
    if action:
        q = q.filter(NaverChangeLog.action == action)
    if not include_dry_run:
        q = q.filter(NaverChangeLog.dry_run.is_(False))

    total = q.count()
    rows = q.order_by(NaverChangeLog.changed_at.desc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "items": [
            {
                "id": r.id,
                "changed_at": r.changed_at.isoformat() if r.changed_at else None,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "campaign_id": r.campaign_id,
                "action": r.action,
                "before": _loads_or_none(r.before_value),
                "after": _loads_or_none(r.after_value),
                "rationale": r.rationale,
                "outcome": r.outcome,
                "dry_run": r.dry_run,
                "proposal_id": r.proposal_id,
                "executed_at": r.executed_at.isoformat() if r.executed_at else None,
            }
            for r in rows
        ],
    }
```

**import 확인**: 파일 상단에 `json`, `timedelta`, `Query`, `NaverChangeLog`, `kst_now`가 이미 import되어 있는지 확인하고, 없으면 추가한다. (`json`·`timedelta`·`Query`·`NaverChangeLog`는 기존에 있음 — `kst_now`는 `from app.utils.kst import kst_now` 필요 여부를 grep으로 확인할 것.)

- [ ] **Step 4: 통과 확인**

Run: `cd backend && pytest tests/test_naver_ad_change_log_router.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: 커밋**

```bash
cd backend && git add app/routers/naver_ad.py tests/test_naver_ad_change_log_router.py
git commit -m "feat(naver-ad): D-NAO-47 T3 GET /change-log — 변경 이력 조회 API (dry-run 기본 제외)"
```

---

## Task 4: `_serialize_proposal` 보강 + `ALL_PROPOSAL_TYPES` 단일 진실

**Files:**
- Modify: `backend/app/routers/naver_ad.py:202-224` (`_serialize_proposal`)
- Modify: `backend/app/services/naver_ad/proposal_writer.py` (`INFORMATIONAL_PROPOSAL_TYPES` 아래)
- Test: `backend/tests/test_naver_ad_proposals_router.py` (기존 파일에 추가)

**설계:** 스펙 §1-6 — `_serialize_proposal`이 `target_bid`를 안 주는 탓에 **"입찰 인상" 카드가 얼마로 올리는지 화면에 없다.** 지금 pending인 실행 대상 5건이 전부 `bid_up`이라 이건 곧바로 체감되는 결함이다.

**제안 유형 13종(실측 확정 — prod DB + 코드 상수 대조):**

| # | type | 분류 | 라벨(프론트에서 사용) |
|---|---|---|---|
| 1 | `bid_up` | 실행형 | 입찰 인상 |
| 2 | `bid_down` | 실행형 | 입찰 인하 |
| 3 | `growth_bid_up` | 실행형 | 성장 입찰 인상 |
| 4 | `negative_keyword` | 실행형 | 제외 키워드 |
| 5 | `pause` | 실행형 | 정지 |
| 6 | `resume` | 실행형 | 재개 |
| 7 | `budget_up` | 실행형(미개방) | 예산 증액 |
| 8 | `budget_pre_exhaustion` | 실행형(미개방) | 예산 소진 임박 |
| 9 | `anomaly` | 정보성 | 이상 감지 |
| 10 | `anomaly_freshness` | 정보성 | 데이터 신선도 이상 |
| 11 | `account_brief` | 정보성 | 계정 브리핑 |
| 12 | `trigger_pacing` | 정보성 | 페이싱 경보 |
| 13 | `trigger_cpc_spike` | 정보성 | CPC 급등 경보 |

출처: `proposal_writer.py:16-24`(`_NEGATIVE`~`_ACCOUNT_BRIEF`) + `trigger_watch.PROPOSAL_TYPE_PACING/CPC` + `guardrail_gate.py:36-37`(`_BID_UP_TYPES`/`_BID_DOWN_TYPES`) + `naver_execution_harness.py:98-99`. **프론트의 `budget`·`new_setup`은 백엔드가 생성하지 않는 유령 라벨**(제거 대상 — Phase 2에서).

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_naver_ad_proposals_router.py` **끝에 추가**:

```python
# ── D-NAO-47 T4: _serialize_proposal 보강 ──
def test_proposal_serializes_target_bid(client, db):
    """★"입찰 인상" 카드가 '얼마로' 올리는지 화면에 없던 결함(스펙 §1-6).
    현재 pending 실행대상 5건이 전부 bid_up이라 바로 체감되는 누락이다."""
    db.add(NaverProposal(
        proposal_type="bid_up", target_type="keyword", target_id="nkw-1",
        campaign_id="cmp-1", status="pending", target_bid=1450,
    ))
    db.commit()
    items = client.get("/api/naver/ad/proposals?status=pending").json()["items"]
    assert items[0]["target_bid"] == 1450


def test_proposal_serializes_target_lock_and_budget(client, db):
    db.add(NaverProposal(
        proposal_type="pause", target_type="keyword", target_id="nkw-2",
        campaign_id="cmp-1", status="pending", target_lock=True, target_budget=50000,
    ))
    db.commit()
    items = client.get("/api/naver/ad/proposals?status=pending").json()["items"]
    assert items[0]["target_lock"] is True
    assert items[0]["target_budget"] == 50000


def test_proposal_serializes_informational_flag(client, db):
    """프론트가 '나를 기다리는 것'(실행형)과 '롤업 대상'(정보성)을 가르는 기준.
    ★프론트에서 유형 문자열을 하드코딩해 재분류하면 드리프트한다 — 백엔드가 진실을 준다."""
    db.add(NaverProposal(
        proposal_type="trigger_pacing", target_type="campaign", target_id="cmp-1",
        campaign_id="cmp-1", status="pending",
    ))
    db.add(NaverProposal(
        proposal_type="bid_up", target_type="keyword", target_id="nkw-3",
        campaign_id="cmp-1", status="pending", target_bid=900,
    ))
    db.commit()
    items = client.get("/api/naver/ad/proposals?status=pending").json()["items"]
    by_type = {i["proposal_type"]: i for i in items}
    assert by_type["trigger_pacing"]["informational"] is True
    assert by_type["bid_up"]["informational"] is False


def test_all_proposal_types_constant_covers_every_emitted_type():
    """★드리프트 방지: 백엔드가 새 유형을 만들면 이 상수에 반드시 추가해야 한다.
    프론트 라벨 13종은 이 상수를 진실로 삼는다(유령 라벨 재발 방지)."""
    from app.services.naver_ad.proposal_writer import ALL_PROPOSAL_TYPES, INFORMATIONAL_PROPOSAL_TYPES
    from app.services.naver_ad.naver_execution_harness import _ACTION_BY_PROPOSAL_TYPE

    assert INFORMATIONAL_PROPOSAL_TYPES <= ALL_PROPOSAL_TYPES
    assert set(_ACTION_BY_PROPOSAL_TYPE) <= ALL_PROPOSAL_TYPES
    assert len(ALL_PROPOSAL_TYPES) == 13
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && pytest tests/test_naver_ad_proposals_router.py -v -k "target_bid or informational or all_proposal_types"`
Expected: FAIL — `KeyError: 'target_bid'` / `ImportError: cannot import name 'ALL_PROPOSAL_TYPES'`

- [ ] **Step 3: 최소 구현 (a) — `ALL_PROPOSAL_TYPES` 상수**

`backend/app/services/naver_ad/proposal_writer.py` — `INFORMATIONAL_PROPOSAL_TYPES` 정의 **바로 아래**에 추가:

```python
# D-NAO-47: 제안 유형 13종 단일 진실 — 프론트 라벨 맵이 이걸 진실로 삼는다.
# ★배경: 프론트 PROPOSAL_TYPE_LABEL이 6종만 정의해 9종이 영문 원문으로 렌더됐고, 반대로
# 백엔드가 생성하지 않는 'budget'·'new_setup' 유령 라벨을 갖고 있었다(스펙 §1-3).
# 새 유형을 추가하면 여기에도 반드시 넣는다 — test_all_proposal_types_constant_covers_
# every_emitted_type이 강제한다.
_BID_UP = "bid_up"
_BID_DOWN = "bid_down"

ALL_PROPOSAL_TYPES: frozenset[str] = frozenset({
    _BID_UP, _BID_DOWN, _GROWTH_BID_UP, _NEGATIVE, _PAUSE, _RESUME,
    _BUDGET_UP, _BUDGET_PRE_EXHAUSTION,
    _ANOMALY, _ANOMALY_FRESHNESS, _ACCOUNT_BRIEF, PROPOSAL_TYPE_PACING, PROPOSAL_TYPE_CPC,
})
```

- [ ] **Step 4: 최소 구현 (b) — `_serialize_proposal` 보강**

`backend/app/routers/naver_ad.py:202` `_serialize_proposal` — `"adgroup_id": p.adgroup_id,` 아래에 4줄 추가하고 `informational` 추가:

```python
def _serialize_proposal(p: NaverProposal, verdict: NaverExpertReview | None) -> dict:
    blocker_reason = naver_execution_harness.real_write_blocker(p)
    return {
        "id": p.id,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "proposal_type": p.proposal_type,
        "target_type": p.target_type,
        "target_id": p.target_id,
        "campaign_id": p.campaign_id,
        "adgroup_id": p.adgroup_id,
        # D-NAO-47: 실행 목표값 — 이게 없어서 "입찰 인상" 카드가 *얼마로* 올리는지
        # 화면에 안 나왔다(스펙 §1-6). pending 실행대상 5건이 전부 bid_up이라 바로 체감됨.
        "target_bid": p.target_bid,
        "target_lock": p.target_lock,
        "target_budget": p.target_budget,
        "budget_auto_eligible": p.budget_auto_eligible,
        # D-NAO-47: 정보성/실행형 구분을 백엔드가 준다 — 프론트가 유형 문자열을 하드코딩해
        # 재분류하면 백엔드에 유형이 추가될 때 조용히 드리프트한다.
        "informational": p.proposal_type in proposal_writer.INFORMATIONAL_PROPOSAL_TYPES,
        "rationale": p.rationale,
        "expected_effect": p.expected_effect,
        "status": p.status,
        "slack_ts": p.slack_ts,
        "executed_change_log_id": p.executed_change_log_id,
        "approval_source": p.approval_source,
        "expert_verdict": _serialize_expert_verdict_summary(verdict) if verdict else None,
        "executable": blocker_reason is None,
        "not_executable_reason": blocker_reason,
    }
```

**import 추가 확인**: 라우터 상단에 `proposal_writer`가 import되어 있는지 grep으로 확인하고, 없으면 기존 `from app.services.naver_ad import ...` 줄에 추가.

**✅ 필드 존재 실측 완료**(계획 작성 시 확인): `NaverProposal.target_bid`(models.py:1622) · `target_lock`(1623) · `target_budget`(1624) · `budget_auto_eligible`(1625) **전부 존재**. 스키마 변경 불필요.

- [ ] **Step 5: 통과 확인**

Run: `cd backend && pytest tests/test_naver_ad_proposals_router.py -v`
Expected: PASS (기존 + 신규 4개)

- [ ] **Step 6: 커밋**

```bash
cd backend && git add app/routers/naver_ad.py app/services/naver_ad/proposal_writer.py tests/test_naver_ad_proposals_router.py
git commit -m "feat(naver-ad): D-NAO-47 T4 제안 직렬화 보강(target_bid 등)+ALL_PROPOSAL_TYPES 13종 단일 진실"
```

---

## Task 5: `GET /api/naver/ad/raw/*` — 원자료 조회 API 3종

**Files:**
- Modify: `backend/app/routers/naver_ad.py` (Task 3의 change-log 섹션 아래)
- Test: `backend/tests/test_naver_ad_raw_router.py` (신규)

**설계:** D-47-d ③ · 스펙 3층 ⑨. 수집은 풍부한데(키워드 91,005 · 검색어 114,285 · 시간당 8,469) **API가 0건이라 볼 방법이 없다**(§1-4).

**★페이지네이션은 선택이 아니라 필수다.** §9 라이브 확인: 진단보드가 489행을 무페이징으로 그려 **스크롤 27,305px**가 나왔다. 키워드는 **91,005행**이다. 상한을 API가 강제한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_naver_ad_raw_router.py` 신규 생성:

```python
# test_naver_ad_raw_router.py — D-NAO-47 T5: GET /api/naver/ad/raw/* HTTP 왕복
# ★페이지네이션 상한이 이 API의 핵심 계약: naver_entity 키워드 91,005행 · search_term 114,285행.
#   상한 없이 열면 프론트가 죽는다(§9 라이브: 489행 무페이징 → 스크롤 27,305px).
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverEntity, NaverHourlySnapshot, NaverSearchTermDaily
from app.utils.kst import kst_now, kst_today


@pytest.fixture
def client_and_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    session_for_seed = TestingSession()
    yield TestClient(app), session_for_seed
    session_for_seed.close()
    app.dependency_overrides.clear()


@pytest.fixture
def client(client_and_session):
    return client_and_session[0]


@pytest.fixture
def db(client_and_session):
    return client_and_session[1]


def _seed_keyword(db, *, entity_id="nkw-1", name="필름", bid=700, status="on", campaign_id="cmp-1"):
    db.add(NaverEntity(
        entity_type="keyword", entity_id=entity_id, parent_id="grp-1",
        campaign_id=campaign_id, campaign_type="WEB_SITE", name=name,
        status=status, bid_amt=bid, synced_at=kst_now(),
    ))
    db.commit()


# ── raw/keywords ──
def test_raw_keywords_returns_only_keyword_rows(client, db):
    _seed_keyword(db)
    db.add(NaverEntity(
        entity_type="campaign", entity_id="cmp-1", parent_id="",
        campaign_id="cmp-1", campaign_type="WEB_SITE", name="캠페인",
        status="on", bid_amt=None, synced_at=kst_now(),
    ))
    db.commit()
    items = client.get("/api/naver/ad/raw/keywords").json()["items"]
    assert len(items) == 1
    assert items[0]["entity_id"] == "nkw-1"
    assert items[0]["bid_amt"] == 700


def test_raw_keywords_limit_is_capped_at_200(client, db):
    """★91,005행짜리 테이블이다. 상한 없이 열면 프론트가 죽는다."""
    assert client.get("/api/naver/ad/raw/keywords?limit=201").status_code == 422
    assert client.get("/api/naver/ad/raw/keywords?limit=200").status_code == 200


def test_raw_keywords_returns_total_for_pagination(client, db):
    for i in range(5):
        _seed_keyword(db, entity_id=f"nkw-{i}", name=f"kw{i}")
    body = client.get("/api/naver/ad/raw/keywords?limit=2").json()
    assert body["total"] == 5
    assert len(body["items"]) == 2


def test_raw_keywords_search_by_name(client, db):
    _seed_keyword(db, entity_id="nkw-1", name="아이폰 필름")
    _seed_keyword(db, entity_id="nkw-2", name="갤럭시 케이스")
    items = client.get("/api/naver/ad/raw/keywords?q=필름").json()["items"]
    assert len(items) == 1
    assert items[0]["name"] == "아이폰 필름"


def test_raw_keywords_filters_by_campaign_and_status(client, db):
    _seed_keyword(db, entity_id="nkw-1", campaign_id="cmp-1", status="on")
    _seed_keyword(db, entity_id="nkw-2", campaign_id="cmp-2", status="on")
    _seed_keyword(db, entity_id="nkw-3", campaign_id="cmp-1", status="off")

    assert len(client.get("/api/naver/ad/raw/keywords?campaign_id=cmp-1").json()["items"]) == 2
    assert len(client.get("/api/naver/ad/raw/keywords?campaign_id=cmp-1&status=on").json()["items"]) == 1


def test_raw_keywords_excludes_deleted_by_default(client, db):
    _seed_keyword(db, entity_id="nkw-1", status="on")
    _seed_keyword(db, entity_id="nkw-2", status="deleted")
    items = client.get("/api/naver/ad/raw/keywords").json()["items"]
    assert len(items) == 1
    assert items[0]["entity_id"] == "nkw-1"


# ── raw/search-terms ──
def test_raw_search_terms_returns_rows(client, db):
    db.add(NaverSearchTermDaily(
        ad_date=kst_today(), campaign_id="cmp-1", adgroup_id="grp-1",
        search_term="아이폰 필름", source="shopping", imp=100, clk=5, cost=1000,
    ))
    db.commit()
    items = client.get("/api/naver/ad/raw/search-terms").json()["items"]
    assert len(items) == 1
    assert items[0]["search_term"] == "아이폰 필름"


def test_raw_search_terms_limit_is_capped_at_200(client, db):
    """114,285행."""
    assert client.get("/api/naver/ad/raw/search-terms?limit=201").status_code == 422


def test_raw_search_terms_respects_days_window(client, db):
    db.add(NaverSearchTermDaily(
        ad_date=kst_today() - timedelta(days=40), campaign_id="cmp-1", adgroup_id="grp-1",
        search_term="옛날", source="shopping", imp=1, clk=0, cost=0,
    ))
    db.add(NaverSearchTermDaily(
        ad_date=kst_today() - timedelta(days=1), campaign_id="cmp-1", adgroup_id="grp-1",
        search_term="최근", source="shopping", imp=1, clk=0, cost=0,
    ))
    db.commit()
    items = client.get("/api/naver/ad/raw/search-terms?days=7").json()["items"]
    assert len(items) == 1
    assert items[0]["search_term"] == "최근"


# ── raw/hourly ──
def test_raw_hourly_returns_rows_with_budget_and_ratio(client, db):
    """★daily_budget·소진율이 화면에 없던 결함(스펙 §1-4) — 여기서 처음 노출된다.
    ⚠️ 컬럼명은 `snapshot_hour`다(`hour` 아님 — models.py:1553 실측)."""
    db.add(NaverHourlySnapshot(
        ad_date=kst_today(), snapshot_hour=14, snapshot_at=kst_now(),
        campaign_id="cmp-1", campaign_type="WEB_SITE",
        cost=25000, clk=10, imp=100, daily_budget=100000, synced_at=kst_now(),
    ))
    db.commit()
    items = client.get("/api/naver/ad/raw/hourly").json()["items"]
    assert len(items) == 1
    assert items[0]["daily_budget"] == 100000
    assert items[0]["snapshot_hour"] == 14
    assert items[0]["spend_ratio"] == pytest.approx(0.25)


def test_raw_hourly_spend_ratio_is_none_when_budget_missing(client, db):
    """★0으로 나누지 않는다. 예산 미설정은 '소진율 0%'가 아니라 '알 수 없음'이다."""
    db.add(NaverHourlySnapshot(
        ad_date=kst_today(), snapshot_hour=14, snapshot_at=kst_now(),
        campaign_id="cmp-1", campaign_type="WEB_SITE",
        cost=25000, clk=10, imp=100, daily_budget=None, synced_at=kst_now(),
    ))
    db.commit()
    items = client.get("/api/naver/ad/raw/hourly").json()["items"]
    assert items[0]["spend_ratio"] is None


def test_raw_hourly_spend_ratio_is_none_when_budget_zero(client, db):
    db.add(NaverHourlySnapshot(
        ad_date=kst_today(), snapshot_hour=14, snapshot_at=kst_now(),
        campaign_id="cmp-1", campaign_type="WEB_SITE",
        cost=25000, clk=10, imp=100, daily_budget=0, synced_at=kst_now(),
    ))
    db.commit()
    assert client.get("/api/naver/ad/raw/hourly").json()["items"][0]["spend_ratio"] is None


def test_raw_hourly_ordered_by_date_then_hour(client, db):
    for h in (9, 14, 11):
        db.add(NaverHourlySnapshot(
            ad_date=kst_today(), snapshot_hour=h, snapshot_at=kst_now(),
            campaign_id="cmp-1", campaign_type="WEB_SITE",
            cost=100, clk=1, imp=10, daily_budget=1000, synced_at=kst_now(),
        ))
    db.commit()
    items = client.get("/api/naver/ad/raw/hourly").json()["items"]
    assert [i["snapshot_hour"] for i in items] == [14, 11, 9]  # 최신 시각 먼저


def test_raw_endpoints_empty_are_200(client):
    for path in ("keywords", "search-terms", "hourly"):
        body = client.get(f"/api/naver/ad/raw/{path}").json()
        assert body == {"items": [], "total": 0}
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && pytest tests/test_naver_ad_raw_router.py -v`
Expected: FAIL — 404

**✅ 스키마 실측 완료**(계획 작성 시 확인 — 위 테스트에 이미 반영됨). 특히 `NaverHourlySnapshot`은 `hour`가 아니라 **`snapshot_hour`**이고 `snapshot_at`이 nullable이 아니므로 시드에 반드시 포함한다. 모델은 고치지 말 것(스키마 변경은 범위 밖).

- [ ] **Step 3: 최소 구현**

`backend/app/routers/naver_ad.py` — Task 3의 change-log 섹션 **아래**에 추가:

```python
# ══════════════════════════════════════════════════════════════════
# D-NAO-47 — 원자료 탐색(3층 ⑨). 수집은 풍부한데 API가 0건이라 볼 방법이 없었다(스펙 §1-4).
# ★limit 상한 200 고정: 키워드 91,005행 · 검색어 114,285행. §9 라이브에서 489행 무페이징이
#   스크롤 27,305px를 만든 전례가 있어 상한을 API가 강제한다(프론트 선의에 맡기지 않는다).
# ══════════════════════════════════════════════════════════════════
_MAX_RAW_LIMIT = 200


@router.get("/raw/keywords")
def get_raw_keywords(
    q: str | None = Query(None, description="키워드 텍스트 부분일치"),
    campaign_id: str | None = Query(None),
    status: str | None = Query(None, description="on/off"),
    include_deleted: bool = Query(False),
    limit: int = Query(50, ge=1, le=_MAX_RAW_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """등록 키워드 원자료(naver_entity의 keyword 행, prod 91,005행) 조회 — 읽기 전용."""
    query = db.query(NaverEntity).filter(NaverEntity.entity_type == "keyword")
    if not include_deleted:
        query = query.filter(NaverEntity.status != "deleted")
    if q:
        query = query.filter(NaverEntity.name.contains(q))
    if campaign_id:
        query = query.filter(NaverEntity.campaign_id == campaign_id)
    if status:
        query = query.filter(NaverEntity.status == status)

    total = query.count()
    rows = query.order_by(NaverEntity.name).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "entity_id": r.entity_id,
                "name": r.name,
                "parent_id": r.parent_id,
                "campaign_id": r.campaign_id,
                "campaign_type": r.campaign_type,
                "status": r.status,
                "bid_amt": r.bid_amt,
                "monthly_volume": r.monthly_volume,
                "competition": r.competition,
                "synced_at": r.synced_at.isoformat() if r.synced_at else None,
            }
            for r in rows
        ],
    }


@router.get("/raw/search-terms")
def get_raw_search_terms(
    q: str | None = Query(None, description="검색어 부분일치"),
    campaign_id: str | None = Query(None),
    days: int = Query(14, ge=1, le=365),
    limit: int = Query(50, ge=1, le=_MAX_RAW_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """검색어 원자료(prod 114,285행, 현재 shopping 소스만) 조회 — 읽기 전용."""
    since = kst_today() - timedelta(days=days)
    query = db.query(NaverSearchTermDaily).filter(NaverSearchTermDaily.ad_date >= since)
    if q:
        query = query.filter(NaverSearchTermDaily.search_term.contains(q))
    if campaign_id:
        query = query.filter(NaverSearchTermDaily.campaign_id == campaign_id)

    total = query.count()
    rows = (
        query.order_by(NaverSearchTermDaily.ad_date.desc(), NaverSearchTermDaily.cost.desc())
        .offset(offset).limit(limit).all()
    )
    return {
        "total": total,
        "items": [
            {
                "ad_date": r.ad_date.isoformat() if r.ad_date else None,
                "campaign_id": r.campaign_id,
                "adgroup_id": r.adgroup_id,
                "search_term": r.search_term,
                "source": r.source,
                "imp": r.imp,
                "clk": r.clk,
                "cost": r.cost,
            }
            for r in rows
        ],
    }


@router.get("/raw/hourly")
def get_raw_hourly(
    campaign_id: str | None = Query(None),
    days: int = Query(3, ge=1, le=365),
    limit: int = Query(100, ge=1, le=_MAX_RAW_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """시간당 스냅샷 조회 — 읽기 전용.

    ★daily_budget·소진율(spend_ratio)이 화면에 없던 결함(스펙 §1-4)을 여기서 해소한다.
    spend_ratio는 daily_budget이 없거나 0이면 **None**이다 — '소진율 0%'가 아니라
    '알 수 없음'이다. 0으로 나누지 않는다.

    보존기간: D-NAO-46①로 7→365일 연장됨. days 상한 365는 그 상한과 맞춘 것.

    ⚠️ 컬럼명은 `snapshot_hour`다(`hour` 아님 — models.py:1553). 응답 키도 snapshot_hour로
    그대로 노출해 프론트↔DB 이름을 일치시킨다(번역 레이어를 만들지 않는다).
    """
    since = kst_today() - timedelta(days=days)
    query = db.query(NaverHourlySnapshot).filter(NaverHourlySnapshot.ad_date >= since)
    if campaign_id:
        query = query.filter(NaverHourlySnapshot.campaign_id == campaign_id)

    total = query.count()
    rows = (
        query.order_by(NaverHourlySnapshot.ad_date.desc(), NaverHourlySnapshot.snapshot_hour.desc())
        .offset(offset).limit(limit).all()
    )

    def _ratio(cost: int, budget: int | None) -> float | None:
        if not budget:  # None 또는 0
            return None
        return round(cost / budget, 4)

    return {
        "total": total,
        "items": [
            {
                "ad_date": r.ad_date.isoformat() if r.ad_date else None,
                "snapshot_hour": r.snapshot_hour,
                "snapshot_at": r.snapshot_at.isoformat() if r.snapshot_at else None,
                "campaign_id": r.campaign_id,
                "campaign_type": r.campaign_type,
                "cost": r.cost,
                "clk": r.clk,
                "imp": r.imp,
                "daily_budget": r.daily_budget,
                "spend_ratio": _ratio(r.cost, r.daily_budget),
            }
            for r in rows
        ],
    }
```

**import 확인**: `NaverEntity`, `NaverSearchTermDaily`, `NaverHourlySnapshot`, `kst_today`가 라우터 상단에 import되어 있는지 확인하고 없으면 추가.

**✅ 스키마 실측 완료(계획 작성 시 확인 — 구현자는 재확인 불필요):**
- `NaverProposal`: `target_bid`(1622) `target_lock`(1623) `target_budget`(1624) `budget_auto_eligible`(1625) **전부 존재** ✅
- `NaverSearchTermDaily`: `ad_date` `campaign_id` `adgroup_id` `search_term` `source` `imp` `clk` `cost` — 위 테스트와 **일치** ✅
- `NaverHourlySnapshot`: `snapshot_at` `ad_date` **`snapshot_hour`** `campaign_id` `campaign_type` `cost` `clk` `imp` `daily_budget` `synced_at` — **`hour`가 아니라 `snapshot_hour`** ⚠️(위 코드에 반영됨)

- [ ] **Step 4: 통과 확인**

Run: `cd backend && pytest tests/test_naver_ad_raw_router.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
cd backend && git add app/routers/naver_ad.py tests/test_naver_ad_raw_router.py
git commit -m "feat(naver-ad): D-NAO-47 T5 GET /raw/{keywords,search-terms,hourly} — 원자료 조회 API(상한 200 강제)"
```

---

## Task 6: 라우터 문서화 주석 갱신 + 전체 회귀

**Files:**
- Modify: `backend/app/routers/naver_ad.py:1-27` (파일 상단 엔드포인트 목록 주석)

- [ ] **Step 1: 상단 주석에 신규 엔드포인트 4개 추가**

`backend/app/routers/naver_ad.py` 상단 주석 블록의 `# GET /api/naver/ad/retro-scorecard ...` **아래**에 추가:

```python
# GET /api/naver/ad/change-log        — 변경 이력 조회(D-NAO-47). naver_change_log 단순 read.
#   include_dry_run 기본 False — 1층 "우리 조작 N회"는 실제 집행만 센다(D-47-h 정직성).
#   이 API는 읽기만 하고, 이력을 *채우는* 것은 entity_sync의 diff 밸브와 execution_harness다.
# GET /api/naver/ad/raw/keywords      — 등록 키워드 원자료(prod 91,005행), limit 상한 200 강제.
# GET /api/naver/ad/raw/search-terms  — 검색어 원자료(prod 114,285행), limit 상한 200 강제.
# GET /api/naver/ad/raw/hourly        — 시간당 스냅샷 + daily_budget·spend_ratio(스펙 §1-4의
#   "소진율 미노출" 해소). spend_ratio는 budget 없음/0이면 None(0 나눗셈 금지).
```

- [ ] **Step 2: 전체 회귀**

Run: `cd backend && pytest -q`
Expected: 902 이상 pass, **실패 0**. 신규 테스트로 총계는 증가만 한다.

- [ ] **Step 3: 커밋**

```bash
cd backend && git add app/routers/naver_ad.py
git commit -m "docs(naver-ad): D-NAO-47 라우터 엔드포인트 목록 주석 갱신"
```

---

## Task 7: ★ codex 교차 검증 게이트 (원칙 19 — 건너뛸 수 없음)

- [ ] **Step 1: `/codex review` 실행**

이 계획의 diff 전체를 대상으로 `/codex review` 실행.

**codex에게 반드시 명시적으로 물을 것 3가지:**
1. **쓰기 폭증**: `_log_external_bid_change`의 가드가 prod 91,005행 × 매일 07:35 크론에서 안전한가? `_norm_bid` 정규화가 타입 불일치를 실제로 다 막는가? (Decimal? bytes? 네이버가 `"1,450"`처럼 콤마를 줄 가능성은?)
2. **호출 순서**: `_log_external_bid_change`가 `e.bid_amt` 대입 **전에** 호출되는 계약이 리팩터에 취약하지 않은가? 순서가 뒤집히면 조용히 로깅 0건이 되는데(테스트가 잡는가?)
3. **귀속 로직**: `_log_external_status_change`의 ⚠️ 원 버그(방향 일치만으로 스킵 → 안전사고)의 대칭 버그가 bid 쪽에 있는가?

- [ ] **Step 2: 대화형 검증 (원칙 19 — 일방 수용/기각 금지)**

지적마다 동의/부분동의/기각 + 근거를 표시하고, 사용자에게 대화 과정을 보여준다:

```
🔍 Codex: [지적]
🤖 Claude: [동의/부분동의/기각 + 근거]
🔍 Codex: [재평가]
🤖 Claude: [최종 입장 + 코드 변경]
✅ 합의 / ❌ 미합의 → Jino 판단 요청
```

최대 3라운드. 미합의 시 Jino에게 위임(임의로 한쪽 따르지 않음).

- [ ] **Step 3: PASS 후 커밋**

```bash
cd backend && git add -A && git commit -m "fix(naver-ad): D-NAO-47 codex review 반영"
```

---

## Task 8: prod 배포 + ★라이브 검증 (원칙 22)

**⚠️ 배포는 Jino 게이트다.** 아래는 승인 후 절차.

- [ ] **Step 1: DB 백업**

```bash
ssh sellc.ohitech.co.kr 'cd /home/ubuntu/ohisell/backend && cp ohisell.db "backups/naver-d-nao-47_$(date +%Y%m%d_%H%M)_predeploy.db" && ls -lh backups/ | tail -3'
```
Expected: ~174MB 백업 파일 생성 확인

- [ ] **Step 2: 배포 + sha 대조 + 재시작**

파일 전송 후 각 파일 `sha256sum` 로컬↔prod 대조(전 파일 일치 필수), 그다음:
```bash
ssh sellc.ohitech.co.kr 'pm2 restart ohisell-backend && sleep 5 && pm2 list | grep ohisell-backend'
```
Expected: `online`, restart 카운트 증가 1, 크래시 0

- [ ] **Step 3: 신규 엔드포인트 라이브 왕복**

```bash
ssh sellc.ohitech.co.kr 'curl -s "localhost:8000/api/naver/ad/change-log?days=30" | head -c 400; echo;
  curl -s "localhost:8000/api/naver/ad/raw/keywords?limit=2" | head -c 400; echo;
  curl -s "localhost:8000/api/naver/ad/raw/hourly?days=1&limit=2" | head -c 400; echo;
  curl -s "localhost:8000/api/naver/ad/proposals?status=pending&limit=1" | head -c 600'
```
Expected: 4개 전부 200 + JSON. 특히 `raw/keywords`의 `total`이 **91,005 근처**, `proposals`에 **`target_bid` 필드 존재**.

- [ ] **Step 4: ★★ 다음날 07:35 크론 후 쓰기 폭증 실측 (이 계획의 진짜 합격 기준)**

**배포 당일에는 "됐다"고 말할 수 없다(원칙 22).** 크론이 91,005행을 실제로 훑은 뒤에만 판정 가능하다.

배포 다음날 07:40 이후:
```bash
ssh sellc.ohitech.co.kr 'cd /home/ubuntu/ohisell/backend && sqlite3 "file:ohisell.db?mode=ro" -readonly -header -column "
SELECT action, COUNT(*) n, MIN(changed_at) first, MAX(changed_at) last
FROM naver_change_log
WHERE date(changed_at) = date('"'"'now'"'"', '"'"'+9 hours'"'"')
GROUP BY action ORDER BY n DESC;"'
```

**합격 기준:**
- `external_bid_change` 행수가 **수십~수백 단위**(MOP가 03에서 실제 조정한 키워드 수 규모). 
- **91,005에 근접하면 즉시 실패** → 밸브가 무너진 것 → `_norm_bid`가 못 막은 타입이 있다는 뜻 → 롤백 후 실측값으로 재수정.
- 0건이면 그것도 조사 대상(밸브가 호출조차 안 되는지 — 호출 순서 회귀 의심).

**⚠️ 이 Step이 끝나기 전에는 "밸브가 작동한다"고 말하지 않는다.** 격리 테스트 15개 통과는 필요조건이지 충분조건이 아니다(원칙 22 · 메모리 `naver-ad-claimed-vs-wired-gaps`).

- [ ] **Step 5: 트랙·progress 갱신**

`docs/tracks/active/track_naver-ad-optimization.md` D-NAO-47에 배포 실측 기록 + `claude-progress.txt` 갱신.

---

## Self-Review (계획 작성자 자체 점검 — 완료)

**1. 스펙 커버리지:**

| 스펙 §5 포함 항목 | 태스크 |
|---|---|
| entity_sync diff 밸브 | T1·T2 ✅ |
| change_log 조회 API | T3 ✅ |
| 원자료 조회 API | T5 ✅ |
| `_serialize_proposal` 보강 | T4 ✅ |
| 제안 라벨 13종 정합 | T4(백엔드 단일 진실 `ALL_PROPOSAL_TYPES`) ✅ / 라벨 맵 자체는 **Phase 2 프론트** |
| 프론트 1~3층·디자인 시스템·recharts | **Phase 2 별도 계획서** (의도적 분리) |
| 03 `optimizer='mop'` 태깅(D-47-g) | ⚠️ **미포함** — 아래 참조 |

**2. 갭 처리:**
- **D-47-g(03 태깅)**: 이건 코드 변경이 아니라 **prod 데이터 1행 UPDATE**다(`naver_campaign_settings`에 03 행 추가, optimizer='mop'). 계획 태스크가 아니라 **Jino 승인 후 배포 시 1회 실행**할 데이터 작업이므로 T8 배포 절차에 붙이는 게 맞다. → **T8에 하위 스텝으로 추가하지 않고 Phase 2 프론트 계획서의 "3열 대조" 태스크 선행조건으로 옮긴다**(그 화면이 없으면 태깅해도 보이는 곳이 없음).
- **`gamma`/모드 다이얼**: D-47-e로 제외 확정. 계획에 없는 게 맞음.

**3. 타입 일관성 점검:**
- `_norm_bid` 반환 `int | None` → `_log_external_bid_change`의 `old_bid`/`new_bid` 비교 ✅
- `after_value` 키 `bidAmt`(camel) 일관 — 테스트·구현·docstring 전부 ✅ (`bid_amt` snake와 혼동 금지 주석 명시)
- `action="update_bid"`(harness 실제 값) — `update_keyword_bid`(writer 내부값)와 구분 ✅
- `ALL_PROPOSAL_TYPES` 13종 = prod DB 실측 8종 + 코드 상수 13종 대조 확인 ✅

**4. 플레이스홀더 스캔:** "TBD"/"적절히 처리"/"비슷하게" 0건. 모든 코드 스텝에 실제 코드 존재 ✅

**5. 발견된 리스크(계획에 명시함):**
- T4 Step 4의 `NaverProposal` 컬럼 존재 여부는 **구현자가 grep으로 먼저 확인**하도록 가드 추가(없으면 스키마 변경이라 범위 밖 → 중단·보고)
- T5 Step 2의 `NaverSearchTermDaily`/`NaverHourlySnapshot` 컬럼명도 동일 가드
