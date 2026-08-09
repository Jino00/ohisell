# test_naver_ad_modifications_router.py — 「수정 사항」 화면 HTTP 왕복.
# 원칙22: SA 단위테스트는 라우터를 안 거치므로 라우터 레이어 500을 못 잡는다(P2-S2 사고 전례).
#
# 커버(계약 그대로):
#   · 두 원천 union이 날짜 구간으로 정확히 잘리는가
#   · 주체 판정 4규칙 각각(external_* → 대행사 / 우리 실집행 → 우리 자동화 /
#     agency_op 전량 → 대행사 / 정정이 최우선)
#   · 정정이 **원천을 안 건드리는가**(정정 후 원천 행 불변을 SELECT로 단언)
#   · occurred_at 없는 행의 날짜 귀속(감지일) vs 있는 행의 날짜 귀속(실제 발생일)
#   · 이전값 불명을 0/빈칸으로 채우지 않는가 · 소급 백필 표시
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverAgencyOp, NaverChangeActorOverride, NaverChangeLog, NaverEntity
from app.services.naver_ad import change_actor

# 라이브 실측 배경(2026-07-30): 우리 10:12 17프로 소재 입찰 인하 + 대행사 소재 조작 3건.
DAY = date(2026, 7, 30)
DETECTED_DAY = date(2026, 8, 3)  # 백필이 남긴 감지일 — 여기로 잡으면 07-30에 안 보인다


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


def _bid(amount: int) -> str:
    """소재(ad) grain 입찰 JSON — 실측 스키마는 최상위가 아니라 `adAttr.bidAmt`다."""
    return json.dumps({"nccAdId": "nad-1", "adAttr": {"bidAmt": amount}})


def _seed_change_log(db, *, action="update_bid", at=datetime(2026, 7, 30, 10, 12, 9),
                     before=_bid(2730), after=_bid(2330), campaign_id="cmp-1",
                     entity_type="ad", entity_id="nad-1", dry_run=False, rationale=None,
                     occurred_at=None):
    row = NaverChangeLog(
        entity_type=entity_type, entity_id=entity_id, campaign_id=campaign_id,
        action=action, before_value=before, after_value=after,
        rationale=rationale, dry_run=dry_run, changed_at=at, occurred_at=occurred_at,
    )
    db.add(row)
    db.commit()
    return row


def _seed_agency_op(db, *, op_date=DETECTED_DAY, occurred_at=datetime(2026, 7, 30, 11, 28, 36),
                    op_type="bid_change", before="2330",
                    after="1890 [BACKFILL 2026-08-03: 소급 백필, 정규 탐지 아님]",
                    entity_type="ad", entity_id="nad-2", campaign_id="cmp-1",
                    optimizer="none"):
    row = NaverAgencyOp(
        op_date=op_date, detected_at=datetime(2026, 8, 3, 12, 53, 57),
        occurred_at=occurred_at, entity_type=entity_type, entity_id=entity_id,
        campaign_id=campaign_id, optimizer=optimizer, op_type=op_type,
        before_value=before, after_value=after, is_exception=True,
    )
    db.add(row)
    db.commit()
    return row


def _get(client, **params):
    params.setdefault("date_from", DAY.isoformat())
    params.setdefault("date_to", DAY.isoformat())
    r = client.get("/api/naver/ad/modifications", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ── union + 날짜 구간 ────────────────────────────────────────────────


def test_union_of_two_sources_in_one_list(client, db):
    _seed_change_log(db)
    _seed_agency_op(db)
    data = _get(client)
    assert data["total"] == 2
    assert {r["source"] for r in data["rows"]} == {"change_log", "agency_op"}


def test_window_clips_both_sources(client, db):
    _seed_change_log(db, at=datetime(2026, 7, 29, 23, 59, 59))  # 구간 밖(전날)
    _seed_change_log(db, at=datetime(2026, 7, 30, 0, 0, 0))     # 구간 안(첫 순간)
    _seed_change_log(db, at=datetime(2026, 7, 31, 0, 0, 0))     # 구간 밖(다음 날 자정)
    _seed_agency_op(db, occurred_at=datetime(2026, 7, 29, 20, 0, 0))  # 구간 밖
    _seed_agency_op(db, occurred_at=datetime(2026, 7, 30, 23, 59, 59))  # 구간 안(마지막 순간)
    data = _get(client)
    assert data["total"] == 2, [r["occurred_at"] for r in data["rows"]]


def test_rows_sorted_by_occurrence_desc(client, db):
    _seed_change_log(db, at=datetime(2026, 7, 30, 10, 12, 9))
    _seed_agency_op(db, occurred_at=datetime(2026, 7, 30, 15, 39, 5))
    rows = _get(client)["rows"]
    assert [r["source"] for r in rows] == ["agency_op", "change_log"]


# ── 주체 판정 4규칙 ──────────────────────────────────────────────────


def test_rule1_external_detection_defaults_to_agency(client, db):
    """① change_log의 external_* 4종 = 외부가 바꾼 걸 우리가 관측 → 대행사."""
    _seed_change_log(db, action="external_bid_change")
    row = _get(client)["rows"][0]
    assert row["actor"] == change_actor.ACTOR_AGENCY
    assert row["actor_label"] == "대행사"


def test_rule2_our_execution_is_ours(client, db):
    """② change_log의 나머지(우리 실집행) = 우리 자동화."""
    _seed_change_log(db, action="update_bid")
    row = _get(client)["rows"][0]
    assert row["actor"] == change_actor.ACTOR_OURS
    assert row["actor_label"] == "우리 자동화"


def test_rule3_agency_op_is_always_agency_regardless_of_optimizer(client, db):
    """③ agency_op은 전량 외부다. ★optimizer 컬럼을 보면 안 된다 — 그건 '누가 만들었나'가
    아니라 '그 캠페인의 관리주체 설정'이라, ours로 설정된 캠페인을 대행사가 만진 사건이
    **우리 것**으로 뒤집힌다."""
    _seed_agency_op(db, optimizer="ours")
    row = _get(client)["rows"][0]
    assert row["actor"] == change_actor.ACTOR_AGENCY
    assert row["actor_auto"] == change_actor.ACTOR_AGENCY


def test_rule4_override_wins_over_auto(client, db):
    """④ 정정이 있으면 그것이 최우선. 자동 판정은 지워지지 않고 actor_auto로 남는다."""
    src = _seed_change_log(db, action="update_bid")
    r = client.put(
        f"/api/naver/ad/modifications/change_log/{src.id}/actor",
        json={"actor": "jino", "note": "내가 콘솔에서 직접"},
    )
    assert r.status_code == 200, r.text
    row = _get(client)["rows"][0]
    assert row["actor"] == "jino"
    assert row["actor_label"] == "Jino"
    assert row["actor_auto"] == change_actor.ACTOR_OURS  # 자동 판정은 보존
    assert row["corrected"] is True
    assert row["correction_note"] == "내가 콘솔에서 직접"


def test_override_survives_refetch_and_applies_to_agency_op_too(client, db):
    op = _seed_agency_op(db)
    client.put(f"/api/naver/ad/modifications/agency_op/{op.id}/actor", json={"actor": "jino"})
    assert _get(client)["rows"][0]["actor"] == "jino"
    assert _get(client)["rows"][0]["actor"] == "jino"  # 새로고침(재조회) 후에도 유지


def test_actor_filter_uses_final_actor_not_auto(client, db):
    """정정했는데 원래 버킷에 계속 남아 있으면 정정이 무의미하다."""
    src = _seed_change_log(db, action="update_bid")
    client.put(f"/api/naver/ad/modifications/change_log/{src.id}/actor", json={"actor": "agency"})
    assert _get(client, actor="ours")["total"] == 0
    assert _get(client, actor="agency")["total"] == 1


def test_by_actor_distribution_counts_final_actor(client, db):
    _seed_change_log(db, action="update_bid")
    _seed_change_log(db, action="external_bid_change", at=datetime(2026, 7, 30, 7, 35, 0))
    _seed_agency_op(db)
    dist = _get(client)["by_actor"]
    assert dist["ours"] == 1
    assert dist["agency"] == 2
    assert dist["jino"] == 0


# ── 정정이 원천을 오염시키지 않는다 ─────────────────────────────────


def test_override_does_not_mutate_change_log_source_row(client, db):
    """★계약: 정정은 원천을 덮어쓰지 않는다. 덮어쓰면 탐지 산출물과 사람 주석이 구분 불가."""
    src = _seed_change_log(db, action="external_bid_change")
    snapshot = (src.action, src.before_value, src.after_value, src.dry_run, src.changed_at)

    client.put(f"/api/naver/ad/modifications/change_log/{src.id}/actor", json={"actor": "jino"})

    db.expire_all()
    after = db.query(NaverChangeLog).filter(NaverChangeLog.id == src.id).one()
    assert (after.action, after.before_value, after.after_value, after.dry_run,
            after.changed_at) == snapshot
    # 정정은 전용 테이블에만 쌓인다.
    ov = db.query(NaverChangeActorOverride).one()
    assert (ov.source, ov.source_id, ov.actor) == ("change_log", src.id, "jino")


def test_override_does_not_mutate_agency_op_source_row(client, db):
    op = _seed_agency_op(db)
    snapshot = (op.op_date, op.occurred_at, op.optimizer, op.op_type,
                op.before_value, op.after_value)

    client.put(f"/api/naver/ad/modifications/agency_op/{op.id}/actor", json={"actor": "ours"})

    db.expire_all()
    after = db.query(NaverAgencyOp).filter(NaverAgencyOp.id == op.id).one()
    assert (after.op_date, after.occurred_at, after.optimizer, after.op_type,
            after.before_value, after.after_value) == snapshot


def test_override_upsert_then_clear_returns_to_auto(client, db):
    """되돌리기가 없으면 오타 정정이 영구화된다(일방통행 문 금지)."""
    src = _seed_change_log(db, action="update_bid")
    client.put(f"/api/naver/ad/modifications/change_log/{src.id}/actor", json={"actor": "agency"})
    assert _get(client)["rows"][0]["actor"] == "agency"

    r = client.put(f"/api/naver/ad/modifications/change_log/{src.id}/actor", json={"actor": None})
    assert r.status_code == 200, r.text
    row = _get(client)["rows"][0]
    assert row["actor"] == change_actor.ACTOR_OURS
    assert row["corrected"] is False
    assert db.query(NaverChangeActorOverride).count() == 0


def test_override_rejects_unknown_actor_and_source(client, db):
    src = _seed_change_log(db)
    assert client.put(
        f"/api/naver/ad/modifications/change_log/{src.id}/actor", json={"actor": "mop"}
    ).status_code == 422
    assert client.put(
        f"/api/naver/ad/modifications/entity_sync/{src.id}/actor", json={"actor": "ours"}
    ).status_code == 422


def test_override_on_missing_source_row_is_404(client):
    """존재하지 않는 행에 정정을 달면 화면에 영영 안 보이는 유령 레코드가 된다."""
    r = client.put("/api/naver/ad/modifications/change_log/9999/actor", json={"actor": "jino"})
    assert r.status_code == 404


# ── 날짜 귀속: 실제 발생 시각 우선 ───────────────────────────────────


def test_occurred_at_wins_over_detection_date(client, db):
    """★이 화면이 존재하는 이유: 백필 36건은 op_date=08-03이지만 실제로는 07-30 일이다.
    감지일로 잡으면 07-30을 골랐을 때 한 건도 안 보인다."""
    _seed_agency_op(db, op_date=DETECTED_DAY, occurred_at=datetime(2026, 7, 30, 11, 28, 36))
    on_day = _get(client)
    assert on_day["total"] == 1
    row = on_day["rows"][0]
    assert row["occurred_date"] == "2026-07-30"
    assert row["time_basis"] == "occurred"

    # 감지일(08-03)로 조회하면 **안 나온다** — 발생일 기준이 계약이기 때문.
    assert _get(client, date_from="2026-08-03", date_to="2026-08-03")["total"] == 0


def test_row_without_occurred_at_falls_back_to_detection_date_and_says_so(client, db):
    """시각 불명 행을 지어내지 않는다 — 감지일에 귀속시키고 그 사실을 표시한다."""
    _seed_agency_op(db, op_date=DAY, occurred_at=None, entity_type="adgroup", entity_id="grp-1")
    row = _get(client)["rows"][0]
    assert row["occurred_date"] == "2026-07-30"
    assert row["time_basis"] == "detected"
    assert "감지 시각" in row["time_note"]


def test_external_detection_row_time_is_marked_detected(client, db):
    """change_log의 external_* 도 '감지' 시각이다(entity_sync가 알아챈 순간)."""
    _seed_change_log(db, action="external_bid_change")
    assert _get(client)["rows"][0]["time_basis"] == "detected"


def test_our_execution_row_time_is_actual(client, db):
    _seed_change_log(db, action="update_bid")
    assert _get(client)["rows"][0]["time_basis"] == "occurred"


# ── 값 표시: 모르는 건 모른다고 ──────────────────────────────────────


def test_missing_before_is_null_with_reason_not_zero(client, db):
    """★백필 36건 중 31건은 이전값이 없다. 0이나 빈칸으로 채우면 '0원이었다'는 거짓이 된다."""
    _seed_agency_op(db, op_type="ad_edit", before="",
                    after="07-30 13:32:03 [BACKFILL 2026-08-03: 소급 백필, 정규 탐지 아님]")
    row = _get(client)["rows"][0]
    assert row["before"] is None
    assert row["before_unknown"] == "이전값 불명(관측 공백)"
    assert row["after"] is not None


def test_backfill_marker_is_flagged_and_stripped_from_value(client, db):
    """마커를 값에 붙인 채 두면 '1890 [BACKFILL…]'이 그대로 표에 찍힌다(내부 표기 노출)."""
    _seed_agency_op(db, op_type="bid_change", before="2330",
                    after="1890 [BACKFILL 2026-08-03: 소급 백필, 정규 탐지 아님]")
    row = _get(client)["rows"][0]
    assert row["backfilled"] is True
    assert "BACKFILL" not in (row["after"] or "")
    assert row["after"] == "1,890원"
    assert row["before"] == "2,330원"


def test_regular_detection_is_not_marked_backfilled(client, db):
    _seed_agency_op(db, before="2330", after="1890")
    assert _get(client)["rows"][0]["backfilled"] is False


def test_ad_level_bid_is_read_from_adattr(client, db):
    """소재 입찰은 최상위 bidAmt가 아니라 adAttr.bidAmt다(쇼핑의 실제 레버, 실측 96%)."""
    _seed_change_log(db, before=_bid(2730), after=_bid(2330))
    row = _get(client)["rows"][0]
    assert (row["before"], row["after"]) == ("2,730원", "2,330원")


def test_stop_row_shows_lock_not_unchanged_budget(client, db):
    """★라이브 실측 회귀 고정(2026-07-30 긴급 정지 5건): 캠페인 정지 payload에는 dailyBudget도
    들어 있어서 '입찰→예산→정지' 순 탐색이면 **"50,000원 → 50,000원"**이 뜬다 —
    광고를 멈춘 사건인데 화면이 "아무것도 안 바뀜"이라고 말한다."""
    payload = lambda lock: json.dumps({  # noqa: E731
        "nccCampaignId": "cmp-1", "dailyBudget": 50000, "userLock": lock,
    })
    _seed_change_log(db, action="manual_emergency_stop", entity_type="campaign",
                     entity_id="cmp-1", before=payload(False), after=payload(True))
    row = _get(client)["rows"][0]
    assert (row["before"], row["after"]) == ("가동", "정지")


def test_unknown_action_picks_the_field_that_actually_changed(client, db):
    """모르는 액션은 순서상 첫 필드가 아니라 **달라진** 필드를 고른다."""
    payload = lambda budget: json.dumps({"bidAmt": 1000, "dailyBudget": budget})  # noqa: E731
    _seed_change_log(db, action="some_new_action", entity_type="campaign", entity_id="cmp-1",
                     before=payload(30000), after=payload(50000))
    row = _get(client)["rows"][0]
    assert (row["before"], row["after"]) == ("30,000원", "50,000원")


def test_entity_name_is_resolved_for_campaign(client, db):
    db.add(NaverEntity(entity_type="campaign", entity_id="cmp-1", campaign_id="cmp-1",
                       campaign_type="SHOPPING", name="03. 아이폰_강화유리", status="on"))
    db.commit()
    _seed_change_log(db, entity_type="campaign", entity_id="cmp-1")
    row = _get(client)["rows"][0]
    assert row["campaign_name"] == "03. 아이폰_강화유리"


# ── 필터·기본값 ──────────────────────────────────────────────────────


def test_dry_run_excluded_by_default(client, db):
    _seed_change_log(db, dry_run=True)
    assert _get(client)["total"] == 0
    assert _get(client, include_dry_run=True)["total"] == 1


def test_blocked_attempt_excluded_by_default(client, db):
    """가드레일이 막힌 시도는 광고를 **안 바꿨다** — '수정 사항'에 세면 거짓이다."""
    _seed_change_log(db, before=None, after=None, rationale="[실행 불가] 가드레일 차단 — BEP 미달")
    assert _get(client)["total"] == 0
    opened = _get(client, include_blocked=True)
    assert opened["total"] == 1
    assert opened["rows"][0]["execution_state"] == "blocked"


def test_source_filter(client, db):
    _seed_change_log(db)
    _seed_agency_op(db)
    assert _get(client, source="change_log")["total"] == 1
    assert _get(client, source="agency_op")["total"] == 1


def test_campaign_filter_applies_to_both_sources(client, db):
    _seed_change_log(db, campaign_id="cmp-1")
    _seed_agency_op(db, campaign_id="cmp-2")
    assert _get(client, campaign_id="cmp-1")["total"] == 1
    assert _get(client, campaign_id="cmp-2")["total"] == 1


def test_pagination_slices_merged_list(client, db):
    for h in range(5):
        _seed_change_log(db, at=datetime(2026, 7, 30, 10 + h, 0, 0))
    first = _get(client, limit=2, offset=0)
    second = _get(client, limit=2, offset=2)
    assert first["total"] == second["total"] == 5
    assert len(first["rows"]) == len(second["rows"]) == 2
    assert {r["key"] for r in first["rows"]}.isdisjoint({r["key"] for r in second["rows"]})


# ── 날짜 검증은 /change-log와 **동일 규칙** ──────────────────────────


@pytest.mark.parametrize(
    "params",
    [
        {"date_from": "2026-07-30"},                       # 한쪽만
        {"date_from": "2026-07-31", "date_to": "2026-07-30"},  # 뒤집힘
        {"date_from": "2025-07-17", "date_to": "2026-07-17"},  # 366일
        {"date_from": "9999-12-31", "date_to": "9999-12-31"},  # OverflowError 경로
    ],
)
def test_date_validation_matches_change_log_rules(client, params):
    assert client.get("/api/naver/ad/modifications", params=params).status_code == 422


# ── D-NAO-147: 변경 이력에도 발생 시각 + 원천 간 중복 접기 ──────────────
# 배경(라이브 실측 2026-08-04): 10:49:25에 꺼진 광고그룹이 change_log에 18:33:51로 남아
#   화면이 "감지 시각 — 실제로 언제 손댔는지는 기록이 없습니다"라고 말했다. 같은 순간
#   naver_entity.edit_tm은 10:49를 갖고 있었다.
OCCURRED = datetime(2026, 7, 30, 10, 49, 25)   # 네이버 editTm이 말하는 실제 발생 시각
DETECTED_NEXT_MORNING = datetime(2026, 7, 31, 7, 35, 2)  # 우리가 알아챈 시각(다음날 아침)


def test_external_change_log_uses_occurred_at_as_time(client, db):
    """occurred_at이 있으면 그것이 귀속 시각이고 time_basis='occurred'다."""
    _seed_change_log(db, action="external_status_change", entity_type="adgroup",
                     entity_id="grp-1", before=json.dumps({"userLock": False}),
                     after=json.dumps({"userLock": True}),
                     at=DETECTED_NEXT_MORNING, occurred_at=OCCURRED)

    rows = _get(client)["rows"]
    assert len(rows) == 1
    assert rows[0]["occurred_at"].startswith("2026-07-30T10:49:25")
    assert rows[0]["time_basis"] == "occurred"


def test_external_change_log_detected_next_day_still_shows_on_occurrence_date(client, db):
    """★핵심: 발생일을 고른 사용자에게 **다음날 감지된** 행이 보여야 한다.

    changed_at으로만 거르면 07-30을 골랐을 때 이 행이 통째로 사라진다 — 정확히 이 화면이
    없애려는 문제(agency_op 쪽에서 이미 한 번 겪은 함정)."""
    _seed_change_log(db, action="external_bid_change", entity_type="adgroup",
                     entity_id="grp-1", before=json.dumps({"bidAmt": 300}),
                     after=json.dumps({"bidAmt": 600}),
                     at=DETECTED_NEXT_MORNING, occurred_at=OCCURRED)

    assert _get(client)["total"] == 1  # 조회창은 07-30 하루


def test_change_log_without_occurred_at_keeps_detected_basis(client, db):
    """occurred_at이 없으면 종전 그대로 — 감지 시각 + '모른다'는 표시(하위호환)."""
    _seed_change_log(db, action="external_bid_change", entity_type="adgroup",
                     entity_id="grp-1", before=json.dumps({"bidAmt": 300}),
                     after=json.dumps({"bidAmt": 600}),
                     at=datetime(2026, 7, 30, 7, 35, 2))

    rows = _get(client)["rows"]
    assert rows[0]["time_basis"] == "detected"


def test_same_event_in_both_sources_collapses_to_one_row(client, db):
    """★같은 사건이 두 원천에 들어와도 한 줄이다.

    양쪽이 같은 editTm을 시각으로 쓰게 되면서 '같은 초·같은 대상·같은 축'의 두 줄이
    나란히 뜨게 됐다 — 읽는 사람에겐 "10:49에 두 번 바뀜"이다. 접은 개수는 dedup이 말한다."""
    _seed_change_log(db, action="external_bid_change", entity_type="adgroup",
                     entity_id="grp-1", before=json.dumps({"bidAmt": 300}),
                     after=json.dumps({"bidAmt": 600}),
                     at=DETECTED_NEXT_MORNING, occurred_at=OCCURRED)
    _seed_agency_op(db, op_date=date(2026, 7, 31), occurred_at=OCCURRED,
                    op_type="bid_change", entity_type="adgroup", entity_id="grp-1",
                    before="300", after="600")

    body = _get(client)
    assert body["total"] == 1
    assert body["dedup"]["merged_agency_op"] == 1
    assert body["rows"][0]["source"] == "change_log"  # 남는 쪽은 관측값이 더 정확한 change_log


def test_different_second_is_not_collapsed(client, db):
    """1초라도 다르면 접지 않는다 — 다른 사건일 가능성을 지우지 않는다(오접 > 미탐)."""
    _seed_change_log(db, action="external_bid_change", entity_type="adgroup",
                     entity_id="grp-1", before=json.dumps({"bidAmt": 300}),
                     after=json.dumps({"bidAmt": 600}),
                     at=DETECTED_NEXT_MORNING, occurred_at=OCCURRED)
    _seed_agency_op(db, op_date=date(2026, 7, 31),
                    occurred_at=OCCURRED.replace(second=26),
                    op_type="bid_change", entity_type="adgroup", entity_id="grp-1",
                    before="300", after="600")

    body = _get(client)
    assert body["total"] == 2
    assert body["dedup"]["merged_agency_op"] == 0


def test_different_axis_is_not_collapsed(client, db):
    """같은 대상·같은 초라도 축이 다르면 별개 사건이다(입찰과 On/Off를 뭉치지 않는다)."""
    _seed_change_log(db, action="external_bid_change", entity_type="adgroup",
                     entity_id="grp-1", before=json.dumps({"bidAmt": 300}),
                     after=json.dumps({"bidAmt": 600}),
                     at=DETECTED_NEXT_MORNING, occurred_at=OCCURRED)
    _seed_agency_op(db, op_date=date(2026, 7, 31), occurred_at=OCCURRED,
                    op_type="status_flip", entity_type="adgroup", entity_id="grp-1",
                    before="on", after="off")

    assert _get(client)["total"] == 2


def test_agency_op_without_occurred_at_is_never_collapsed(client, db):
    """시각을 모르는 agency_op 행은 접기 대상이 아니다 — 같은 사건인지 알 수 없기 때문."""
    _seed_change_log(db, action="external_bid_change", entity_type="adgroup",
                     entity_id="grp-1", before=json.dumps({"bidAmt": 300}),
                     after=json.dumps({"bidAmt": 600}),
                     at=DETECTED_NEXT_MORNING, occurred_at=OCCURRED)
    _seed_agency_op(db, op_date=DAY, occurred_at=None, op_type="bid_change",
                    entity_type="adgroup", entity_id="grp-1", before="300", after="600")

    body = _get(client)
    assert body["total"] == 2
    assert body["dedup"]["merged_agency_op"] == 0


def test_summary_sentence_uses_occurred_time_not_detected(client, db):
    """★행의 시각과 문장의 시각이 어긋나면 안 된다.

    라이브에서 실제로 그랬다(2026-08-04): 행 머리는 10:49인데 문장은 "18:33 · …"이었다.
    occurred_at이 있으면 문장도 그 시각을 말한다."""
    _seed_change_log(db, action="external_status_change", entity_type="adgroup",
                     entity_id="grp-1", before=json.dumps({"userLock": False}),
                     after=json.dumps({"userLock": True}),
                     at=DETECTED_NEXT_MORNING, occurred_at=OCCURRED)

    row = _get(client)["rows"][0]
    assert row["summary"].startswith("10:49"), row["summary"]
    assert "07:35" not in row["summary"]


# ── 규칙 ⑤ — 우리 실집행과 대조해 되찾기 (2026-08-09 라이브 실사고) ──
#
# 실사고: 2026-07-30 10:50 우리가 Jino 지시로 캠페인 5개를 정지했는데(manual_emergency_stop),
# entity_sync가 하루 1회(07:37)만 돌아 **다음날 아침** "꺼져 있네"를 external_status_change로
# 적었고 규칙 ①이 그걸 대행사로 보냈다. prod에서 04·15·P 3줄이 "대행사가 껐다"로 서 있었다.

OURS_STOP_AT = datetime(2026, 7, 30, 10, 50, 6)        # 우리가 실제로 끈 순간
NEXT_MORNING_SYNC = datetime(2026, 7, 31, 7, 37, 30)   # 다음날 아침 탐지(20.8시간 뒤)
_LOCKED = json.dumps({"userLock": True})
_UNLOCKED = json.dumps({"userLock": False})
# 우리 실집행 행의 after는 엔티티 통짜 JSON이다(감지 행의 짧은 dict와 방언이 다르다).
_CAMPAIGN_PAUSED = json.dumps(
    {"nccCampaignId": "cmp-1", "name": "15. 갤럭시Z", "userLock": True, "status": "PAUSED"}
)


def _seed_our_stop(db, *, at=OURS_STOP_AT, action="manual_emergency_stop", dry_run=False,
                   after=_CAMPAIGN_PAUSED):
    return _seed_change_log(db, action=action, entity_type="campaign", entity_id="cmp-1",
                            campaign_id="cmp-1", before=_UNLOCKED, after=after,
                            at=at, dry_run=dry_run)


def _seed_next_morning_detection(db, *, after=_LOCKED, at=NEXT_MORNING_SYNC, occurred_at=None):
    return _seed_change_log(db, action="external_status_change", entity_type="campaign",
                            entity_id="cmp-1", campaign_id="cmp-1", before=_UNLOCKED,
                            after=after, at=at, occurred_at=occurred_at)


def test_rule5_reclaims_our_own_stop_detected_next_morning(client, db):
    """★핵심 회귀: 우리가 끈 것이 다음날 탐지에서 대행사로 찍히면 안 된다."""
    _seed_our_stop(db)
    _seed_next_morning_detection(db)

    body = _get(client, date_from="2026-07-30", date_to="2026-07-31")
    detection = next(r for r in body["rows"] if r["op_type"] == "external_status_change")
    assert detection["actor"] == change_actor.ACTOR_OURS
    assert detection["actor_label"] == "우리 자동화"
    # 근거를 같이 낸다 — 주체는 사실 주장이라 근거 없이 뒤집으면 다음 사람이 못 믿는다.
    assert "우리 실집행과 대조됨" in detection["actor_evidence"]
    assert "07-30 10:50" in detection["actor_evidence"]
    assert body["reclaimed_ours"] == 1
    assert body["by_actor"][change_actor.ACTOR_AGENCY] == 0


def test_rule5_does_not_steal_the_reverse_change(client, db):
    """우리가 껐는데 감지된 건 **재개**(값이 반대) → 그건 대행사가 한 것이다."""
    _seed_our_stop(db)
    _seed_next_morning_detection(db, after=_UNLOCKED)

    body = _get(client, date_from="2026-07-30", date_to="2026-07-31")
    detection = next(r for r in body["rows"] if r["op_type"] == "external_status_change")
    assert detection["actor"] == change_actor.ACTOR_AGENCY
    assert detection["actor_evidence"] is None
    assert body["reclaimed_ours"] == 0


def test_rule5_window_is_closed_after_36h_for_detected_rows(client, db):
    """창 밖이면 현행 유지(대행사) — fail-safe 방향은 '덜 되찾기'다."""
    _seed_our_stop(db, at=NEXT_MORNING_SYNC - timedelta(hours=36, minutes=1))
    _seed_next_morning_detection(db)

    body = _get(client, date_from="2026-07-29", date_to="2026-07-31")
    detection = next(r for r in body["rows"] if r["op_type"] == "external_status_change")
    assert detection["actor"] == change_actor.ACTOR_AGENCY
    assert body["reclaimed_ours"] == 0


def test_rule5_window_is_tight_when_real_time_is_known(client, db):
    """occurred(진짜 발생 시각)를 아는 행은 10분 창이다 — 아는 만큼만 좁힌다.

    36시간을 그대로 열어두면, 우리가 어제 넣은 값과 우연히 같은 값을 오늘 대행사가 넣었을 때
    우리 것으로 뺏는다."""
    occurred = datetime(2026, 7, 31, 10, 49, 25)
    _seed_our_stop(db, at=occurred - timedelta(hours=2))
    _seed_next_morning_detection(db, at=NEXT_MORNING_SYNC, occurred_at=occurred)

    body = _get(client, date_from="2026-07-30", date_to="2026-07-31")
    detection = next(r for r in body["rows"] if r["op_type"] == "external_status_change")
    assert detection["actor"] == change_actor.ACTOR_AGENCY, "2시간 전 집행은 occurred 창 밖이다"

    # 같은 조건에서 3분 전이면 되찾는다(창이 죽어 있는 게 아님을 고정).
    _seed_our_stop(db, at=occurred - timedelta(minutes=3))
    body = _get(client, date_from="2026-07-30", date_to="2026-07-31")
    detection = next(r for r in body["rows"] if r["op_type"] == "external_status_change")
    assert detection["actor"] == change_actor.ACTOR_OURS


def test_rule5_blocked_or_dry_run_execution_is_not_evidence(client, db):
    """가드레일이 막았거나 dry-run이면 광고를 안 바꿨다 → 외부 변경을 설명하지 못한다."""
    _seed_our_stop(db, dry_run=True)
    _seed_our_stop(db, at=OURS_STOP_AT + timedelta(seconds=1), after=None)  # 차단된 시도
    _seed_next_morning_detection(db)

    body = _get(client, date_from="2026-07-30", date_to="2026-07-31")
    detection = next(r for r in body["rows"] if r["op_type"] == "external_status_change")
    assert detection["actor"] == change_actor.ACTOR_AGENCY
    assert body["reclaimed_ours"] == 0


def test_rule5_reclaims_bid_axis_too(client, db):
    """축은 status만이 아니다 — 입찰도 같은 사고를 겪는다(우리 update_bid ↔ external_bid_change)."""
    _seed_change_log(db, action="update_bid", entity_type="adgroup", entity_id="grp-1",
                     before=json.dumps({"bidAmt": 300}), after=json.dumps({"bidAmt": 600}),
                     at=OURS_STOP_AT)
    _seed_change_log(db, action="external_bid_change", entity_type="adgroup", entity_id="grp-1",
                     before=json.dumps({"bidAmt": 300}), after=json.dumps({"bidAmt": 600}),
                     at=NEXT_MORNING_SYNC)

    body = _get(client, date_from="2026-07-30", date_to="2026-07-31")
    detection = next(r for r in body["rows"] if r["op_type"] == "external_bid_change")
    assert detection["actor"] == change_actor.ACTOR_OURS


def test_rule5_reclaims_agency_op_rows_as_well(client, db):
    """agency_op(스냅샷 diff)에도 우리 집행이 섞여 들어온다 — 규칙 ③만으론 전부 대행사가 된다."""
    occurred = datetime(2026, 7, 30, 10, 50, 6)
    _seed_change_log(db, action="set_user_lock", entity_type="adgroup", entity_id="grp-9",
                     before=_UNLOCKED, after=json.dumps({"nccAdgroupId": "grp-9", "userLock": True}),
                     at=occurred)
    _seed_agency_op(db, op_date=DAY, occurred_at=occurred + timedelta(seconds=4),
                    op_type="status_flip", entity_type="adgroup", entity_id="grp-9",
                    before="on", after="off")

    body = _get(client, date_from="2026-07-30", date_to="2026-07-30")
    row = next(r for r in body["rows"] if r["source"] == "agency_op")
    assert row["actor"] == change_actor.ACTOR_OURS
    assert body["reclaimed_ours"] == 1


def test_rule5_loses_to_human_correction(client, db):
    """④가 ⑤보다 위다 — 사람이 자동 규칙을 이긴다."""
    _seed_our_stop(db)
    detection = _seed_next_morning_detection(db)
    r = client.put(
        f"/api/naver/ad/modifications/change_log/{detection.id}/actor",
        json={"actor": change_actor.ACTOR_AGENCY, "note": "내가 확인함"},
    )
    assert r.status_code == 200, r.text

    body = _get(client, date_from="2026-07-30", date_to="2026-07-31")
    row = next(r for r in body["rows"] if r["op_type"] == "external_status_change")
    assert row["actor"] == change_actor.ACTOR_AGENCY
    assert row["corrected"] is True
    assert row["actor_auto"] == change_actor.ACTOR_OURS  # 자동 판정은 ⑤ 결과 그대로 보인다


def test_rule5_does_not_touch_source_tables(client, db):
    """금지선: 판정만 바꾼다. 원천 테이블은 이 경로에서 한 행도 안 바뀐다."""
    _seed_our_stop(db)
    det = _seed_next_morning_detection(db)
    before = (det.action, det.after_value, db.query(NaverChangeLog).count())

    _get(client, date_from="2026-07-30", date_to="2026-07-31")

    db.expire_all()
    after_row = db.query(NaverChangeLog).filter(NaverChangeLog.id == det.id).one()
    assert (after_row.action, after_row.after_value, db.query(NaverChangeLog).count()) == before


def test_evidence_window_keys_match_time_basis_codes():
    """창 사전의 키가 time_basis 코드와 어긋나면 되찾기가 **조용히 전부 꺼진다**.

    두 모듈에 나뉜 문자열이라(순환 import 회피) 여기서 고정한다."""
    from app.services.naver_ad import modification_feed

    assert set(change_actor.EVIDENCE_WINDOW) == {
        modification_feed.TIME_BASIS_DETECTED,
        modification_feed.TIME_BASIS_OCCURRED,
    }
