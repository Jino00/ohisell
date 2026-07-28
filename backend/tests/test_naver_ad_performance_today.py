# test_naver_ad_performance_today.py — D-NAO-104 Phase 1: GET /api/naver/ad/performance/today
# + SA 단위(today_proxy_revenue 분모/파워링크 null · change_log_narrator 문장 조립 · 로스터 확장).
# 원칙22: SA 단위테스트만으로는 라우터 레이어 500을 못 잡는다 → HTTP 왕복도 함께 건다.
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    NaverAdgroupProduct,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
    NaverHourlySnapshot,
    NaverProductBep,
    Order,
)
from app.services.naver_ad import (
    campaign_roster,
    change_log_narrator,
    naver_execution_harness,
    perf_today_harness,
    today_proxy_revenue,
)
from app.utils.kst import kst_now, kst_today

SHOPPING_CAMPAIGN = "cmp-shopping-1"
POWERLINK_CAMPAIGN = "cmp-powerlink-1"
PRODUCT_ID = "11730763642"


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
    seed = TestingSession()
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()


@pytest.fixture
def client(client_and_session):
    return client_and_session[0]


@pytest.fixture
def db(client_and_session):
    return client_and_session[1]


def _seed_baseline(db):
    """쇼핑 캠페인(상품 매핑 있음) + 파워링크 캠페인(매핑 없음) 최소 셋."""
    db.add_all([
        NaverEntity(entity_type="campaign", entity_id=SHOPPING_CAMPAIGN, campaign_id=SHOPPING_CAMPAIGN,
                    campaign_type="SHOPPING", name="02. 아이폰_강화유리", status="on", status_reason="ELIGIBLE"),
        NaverEntity(entity_type="campaign", entity_id=POWERLINK_CAMPAIGN, campaign_id=POWERLINK_CAMPAIGN,
                    campaign_type="WEB_SITE", name="P. 아이패드 파워링크", status="on", status_reason="ELIGIBLE"),
        NaverEntity(entity_type="adgroup", entity_id="grp-1", campaign_id=SHOPPING_CAMPAIGN,
                    parent_id=SHOPPING_CAMPAIGN, campaign_type="SHOPPING", name="17프로", status="on"),
    ])
    db.add(NaverCampaignSettings(campaign_id=SHOPPING_CAMPAIGN, optimizer="ours", auto_operate=True))
    db.add(NaverAdgroupProduct(adgroup_id="grp-1", campaign_id=SHOPPING_CAMPAIGN,
                               mall_product_id=PRODUCT_ID, product_name="아이폰 17 Pro 강화유리",
                               ad_id="nad-1"))
    db.add(NaverProductBep(channel_id=6, channel_product_id=PRODUCT_ID, product_name="강화유리",
                           selling_price=Decimal("12900"), cost_price=Decimal("3100"),
                           contribution_margin=Decimal("8740"), bep_roas=Decimal("1.4758"),
                           target_roas=Decimal("1.7200"), has_cost=True))
    now = kst_now()
    db.add_all([
        NaverHourlySnapshot(snapshot_at=now, ad_date=kst_today(), snapshot_hour=now.hour,
                            campaign_id=SHOPPING_CAMPAIGN, campaign_type="SHOPPING",
                            cost=41300, clk=20, imp=900, daily_budget=50000),
        NaverHourlySnapshot(snapshot_at=now, ad_date=kst_today(), snapshot_hour=now.hour,
                            campaign_id=POWERLINK_CAMPAIGN, campaign_type="WEB_SITE",
                            cost=5000, clk=3, imp=100, daily_budget=None),
    ])
    db.commit()


def _seed_order(db, *, amount: int, product_id: str = PRODUCT_ID, status: str = "delivered"):
    db.add(Order(
        channel_id=6, order_number=f"ord-{amount}-{product_id}", platform_product_id=product_id,
        quantity=1, selling_price=Decimal(amount), order_date=kst_now(), status=status,
    ))
    db.commit()


def _seed_change(db, *, action="update_bid", entity_type="adgroup", entity_id="grp-1",
                 campaign_id=SHOPPING_CAMPAIGN, before=None, after=None, rationale="",
                 outcome=None, dry_run=False, hours_ago=1):
    row = NaverChangeLog(
        entity_type=entity_type, entity_id=entity_id, campaign_id=campaign_id, action=action,
        before_value=json.dumps(before) if before is not None else None,
        after_value=json.dumps(after) if after is not None else None,
        rationale=rationale, outcome=outcome, dry_run=dry_run,
        changed_at=kst_now() - timedelta(hours=hours_ago),
    )
    db.add(row)
    db.commit()
    return row


# ─────────────────────────── SA: campaign_roster 확장(P1-1) ───────────────────────────

def test_roster_exposes_auto_operate_and_status_reason(db):
    _seed_baseline(db)
    rows = {r["campaign_id"]: r for r in campaign_roster.build(db)}
    assert rows[SHOPPING_CAMPAIGN]["auto_operate"] is True
    assert rows[SHOPPING_CAMPAIGN]["status_reason"] == "ELIGIBLE"
    # 설정 행이 없는 캠페인은 auto_operate=False(모델 기본값과 동일 시맨틱)
    assert rows[POWERLINK_CAMPAIGN]["auto_operate"] is False
    # 기존 키는 그대로(additive — 커맨드 센터 회귀 방지)
    for key in ("campaign_id", "name", "campaign_type", "status", "cost", "clk", "conv_amt",
                "roas_naver", "optimizer", "loss_policy", "window_days"):
        assert key in rows[SHOPPING_CAMPAIGN]


# ─────────────────────────── SA: today_proxy_revenue ───────────────────────────

def test_proxy_revenue_none_without_product_mapping(db):
    """파워링크는 상품 매핑이 원리적으로 없다 → 0이 아니라 None(=알 수 없음) + 사유."""
    _seed_baseline(db)
    _seed_order(db, amount=30000)
    out = today_proxy_revenue.build(db, [SHOPPING_CAMPAIGN, POWERLINK_CAMPAIGN], kst_today())
    assert out[SHOPPING_CAMPAIGN]["revenue"] == 30000
    assert out[POWERLINK_CAMPAIGN]["revenue"] is None
    assert out[POWERLINK_CAMPAIGN]["reason"] == today_proxy_revenue.NO_MAPPING_REASON


def test_proxy_revenue_zero_is_measured_not_unknown(db):
    """매핑은 있는데 주문이 없으면 0 — '모름'과 구분한다."""
    _seed_baseline(db)
    out = today_proxy_revenue.build(db, [SHOPPING_CAMPAIGN], kst_today())
    assert out[SHOPPING_CAMPAIGN]["revenue"] == 0
    assert out[SHOPPING_CAMPAIGN]["reason"] is None


def test_proxy_revenue_excludes_cancelled_and_other_days(db):
    _seed_baseline(db)
    _seed_order(db, amount=30000)
    _seed_order(db, amount=99999, status="cancelled")
    db.add(Order(channel_id=6, order_number="ord-yesterday", platform_product_id=PRODUCT_ID,
                 quantity=1, selling_price=Decimal(50000), status="delivered",
                 order_date=kst_now() - timedelta(days=1)))
    db.commit()
    out = today_proxy_revenue.build(db, [SHOPPING_CAMPAIGN], kst_today())
    assert out[SHOPPING_CAMPAIGN]["revenue"] == 30000


def test_proxy_revenue_splits_shared_product(db):
    """한 상품이 두 캠페인에 매핑되면 균등 분할 — 합쳐서 실제 매출을 넘지 않는다."""
    _seed_baseline(db)
    db.add(NaverEntity(entity_type="campaign", entity_id="cmp-shopping-2", campaign_id="cmp-shopping-2",
                       campaign_type="SHOPPING", name="03. 같은상품", status="on"))
    db.add(NaverAdgroupProduct(adgroup_id="grp-2", campaign_id="cmp-shopping-2",
                               mall_product_id=PRODUCT_ID, product_name="같은 상품"))
    db.commit()
    _seed_order(db, amount=30000)
    out = today_proxy_revenue.build(db, [SHOPPING_CAMPAIGN, "cmp-shopping-2"], kst_today())
    assert out[SHOPPING_CAMPAIGN]["revenue"] == 15000
    assert out["cmp-shopping-2"]["revenue"] == 15000
    assert out[SHOPPING_CAMPAIGN]["shared_product_count"] == 1


# ─────────────────────────── SA: change_log_narrator ───────────────────────────

def test_narrator_bid_up_sentence_has_names_not_ids(db):
    _seed_baseline(db)
    row = _seed_change(db, before={"bidAmt": 2290}, after={"bidAmt": 3060},
                       rationale="[탐색UP] 밴드 밖·흐름0 정체 — 적응 스텝 상향")
    [item] = change_log_narrator.narrate(db, [row])
    assert item["state"] == change_log_narrator.STATE_EXECUTED
    s = item["sentence"]
    assert "17프로" in s and "02. 아이폰_강화유리" in s
    assert "2,290원에서 3,060원으로 올렸습니다" in s
    # D-NAO-103: ID·내부 용어 금지
    assert "grp-1" not in s and SHOPPING_CAMPAIGN not in s
    assert "탐색UP" not in s and "D-NAO" not in s


def test_narrator_blocked_sentence_says_it_did_not_change(db):
    _seed_baseline(db)
    row = _seed_change(
        db, outcome="failed", rationale=(
            "[탐색UP] 첫 탐색 시작 [실행 불가] 가드레일 차단 — 스톱로스 도달 — "
            "무전환 지출 11660원 ≥ 상한 600원(D-NAO-20, target_bid×10)"
        ),
    )
    [item] = change_log_narrator.narrate(db, [row])
    assert item["state"] == change_log_narrator.STATE_BLOCKED
    s = item["sentence"]
    assert "멈췄습니다" in s
    assert "아직 전환이 없는데 지출이 한도에 닿아" in s
    assert "올렸습니다" not in s  # 집행과 같은 말을 쓰면 안 된다
    assert "D-NAO" not in s and "target_bid" not in s


def test_narrator_write_failure_is_unknown_not_blocked(db):
    """쓰기 실패는 '확실히 안 바뀜'이 아니라 '모름'이다(원칙22)."""
    _seed_baseline(db)
    row = _seed_change(db, outcome="failed",
                       rationale="[탐색UP] 스텝 [실행 실패] WriteVerificationError: ...")
    [item] = change_log_narrator.narrate(db, [row])
    assert item["state"] == change_log_narrator.STATE_UNKNOWN
    assert "확인되지 않았습니다" in item["sentence"]


def test_narrator_ad_entity_uses_product_name(db):
    """소재(ad)는 naver_entity에 없다 — 상품명으로 말하고 ID를 흘리지 않는다."""
    _seed_baseline(db)
    row = _seed_change(db, entity_type="ad", entity_id="nad-1",
                       before={"adAttr": {"bidAmt": 300}}, after={"adAttr": {"bidAmt": 1900}},
                       rationale="[콜드첫입찰] 콜드 첫 입찰 1,900원")
    [item] = change_log_narrator.narrate(db, [row])
    assert "아이폰 17 Pro 강화유리" in item["sentence"]
    assert "nad-1" not in item["sentence"]
    assert "300원에서 1,900원으로 올렸습니다" in item["sentence"]


def test_narrator_unknown_ad_falls_back_to_generic_label(db):
    _seed_baseline(db)
    row = _seed_change(db, entity_type="ad", entity_id="nad-없는소재",
                       before={"adAttr": {"bidAmt": 100}}, after={"adAttr": {"bidAmt": 200}})
    [item] = change_log_narrator.narrate(db, [row])
    assert "상품 소재" in item["sentence"]
    assert "nad-없는소재" not in item["sentence"]


def test_narrator_budget_and_lock_and_search_term(db):
    _seed_baseline(db)
    rows = [
        _seed_change(db, action="budget_up_pacing", entity_type="campaign",
                     entity_id=SHOPPING_CAMPAIGN, before={"dailyBudget": 50000},
                     after={"dailyBudget": 65000}, rationale="[예산페이싱] 소진 빠름", hours_ago=3),
        _seed_change(db, action="set_user_lock", after={"userLock": True},
                     rationale="[shopping_pause_candidates] 성과 없음", hours_ago=2),
        _seed_change(db, action="exclude_search_term", entity_type="search_term",
                     entity_id="무료배송", after={"created_ids": ["rst-1"]},
                     rationale="[검색어제외] 성과 없음", hours_ago=1),
    ]
    items = change_log_narrator.narrate(db, rows)
    assert "하루 예산을 50,000원에서 65,000원으로 늘렸습니다" in items[0]["sentence"]
    assert "광고를 멈췄습니다" in items[1]["sentence"]
    assert "‘무료배송’" in items[2]["sentence"]
    # 시간 오름차순 정렬
    assert [i["time_label"] for i in items] == sorted(i["time_label"] for i in items)


def test_narrator_campaign_target_is_not_repeated(db):
    """캠페인 자신이 대상이면 이름을 두 번 읽지 않는다(라이브 실측 교정)."""
    _seed_baseline(db)
    row = _seed_change(db, action="update_budget", entity_type="campaign",
                       entity_id=SHOPPING_CAMPAIGN, before={"dailyBudget": 30000},
                       after={"dailyBudget": 50000}, rationale="[예산봉투] 램프")
    [item] = change_log_narrator.narrate(db, [row])
    assert item["sentence"].count("02. 아이폰_강화유리") == 1
    assert "하루 예산을 30,000원에서 50,000원으로 늘렸습니다" in item["sentence"]


def test_bracket_label_prefix_survives_cleaning(db):
    """prod 실측 `[P_삭제금지]03. …` — 여는 괄호만 지우면 깨진 이름이 남는다."""
    from app.services.naver_ad import alert_humanizer

    # prod 실측 원문은 `● [P_삭제금지]03. …` — 장식(●)만 지우고 라벨은 통째로 남긴다.
    assert alert_humanizer.clean_name("● [P_삭제금지]03. 아이폰_강화유리") == "[P_삭제금지]03. 아이폰_강화유리"
    assert alert_humanizer.clean_name("[P_삭제금지]04. 아이폰_지문방지") == "[P_삭제금지]04. 아이폰_지문방지"
    assert alert_humanizer.clean_name("○ P. 아이패드 파워링크") == "P. 아이패드 파워링크"
    assert alert_humanizer.clean_name("[짝없는 여는 괄호") == "짝없는 여는 괄호"


def test_marker_constants_match_execution_harness():
    """복제한 접두사 상수가 하니스와 갈라지면 차단/실패 판별이 조용히 어긋난다."""
    assert change_log_narrator.GUARD_BLOCK_MARKER == naver_execution_harness.GUARD_BLOCK_MARKER
    assert change_log_narrator.WRITE_FAILURE_MARKER == naver_execution_harness.WRITE_FAILURE_MARKER


# ─────────────────────────── Harness + 라우터 ───────────────────────────

def test_performance_today_http_roundtrip(client, db):
    _seed_baseline(db)
    _seed_order(db, amount=130000)
    _seed_change(db, before={"bidAmt": 2290}, after={"bidAmt": 3060}, rationale="[탐색UP] 상향")

    res = client.get("/api/naver/ad/performance/today")
    assert res.status_code == 200
    body = res.json()
    cards = {c["campaign_id"]: c for c in body["campaigns"]}

    shopping = cards[SHOPPING_CAMPAIGN]
    assert shopping["spend_today"] == 41300
    assert shopping["daily_budget"] == 50000
    assert shopping["spend_ratio"] == pytest.approx(0.826)
    assert shopping["revenue_today_proxy"] == 130000
    assert shopping["roas_today_proxy"] == pytest.approx(130000 / 41300, rel=1e-3)
    assert shopping["target_roas"] == pytest.approx(1.72)
    assert shopping["bep_roas"] == pytest.approx(1.4758)
    assert shopping["managed_by_label"] == "우리가 자동으로 운영"
    assert shopping["status_label"] == "정상 노출 중"
    assert "목표를 넘고 있습니다" in shopping["verdict_sentence"]

    # 파워링크는 프록시 산출 불가 → null(0.00배 금지)
    power = cards[POWERLINK_CAMPAIGN]
    assert power["roas_today_proxy"] is None
    assert power["revenue_today_proxy"] is None
    assert power["roas_unknown_reason"] == today_proxy_revenue.NO_MAPPING_REASON

    actions = body["today_actions"]
    assert actions["executed_count"] == 1
    assert actions["blocked_count"] == 0
    assert actions["quiet_reason"] is None
    assert len(actions["items"]) == 1


def test_performance_today_quiet_when_no_actions(client, db):
    """0건이면 숨기지 않고 왜 0인지 말한다(D-47-h)."""
    _seed_baseline(db)
    body = client.get("/api/naver/ad/performance/today").json()
    assert body["today_actions"]["executed_count"] == 0
    assert body["today_actions"]["items"] == []
    assert body["today_actions"]["quiet_reason"] == perf_today_harness.QUIET_REASON


def test_performance_today_excludes_dry_run_and_external_and_other_days(client, db):
    """dry-run·외부 감지·내부 설정 변경·어제 행은 '오늘 우리가 한 일'이 아니다."""
    _seed_baseline(db)
    _seed_change(db, dry_run=True, after={"bidAmt": 100}, rationale="[탐색UP] 시뮬")
    _seed_change(db, action="external_bid_change", after={"bidAmt": 100},
                 rationale="entity_sync 감지: 외부(MOP/사람) 입찰가 변경")
    _seed_change(db, action="optimizer_change", entity_type="campaign",
                 entity_id=SHOPPING_CAMPAIGN, before="none", after="ours", rationale="스위치")
    _seed_change(db, after={"bidAmt": 900}, rationale="[탐색UP] 어제 것", hours_ago=30)

    actions = client.get("/api/naver/ad/performance/today").json()["today_actions"]
    assert actions["executed_count"] == 0
    assert actions["items"] == []


def test_performance_today_counts_blocked_separately(client, db):
    _seed_baseline(db)
    _seed_change(db, before={"bidAmt": 200}, after={"bidAmt": 300}, rationale="[탐색UP] 상향")
    _seed_change(db, outcome="failed",
                 rationale="[탐색UP] 시작 [실행 불가] 가드레일 차단 — BEP 미달 증액 금지 — 보정ROAS 0")
    actions = client.get("/api/naver/ad/performance/today").json()["today_actions"]
    assert actions["executed_count"] == 1
    assert actions["blocked_count"] == 1
    assert len(actions["items"]) == 2


def test_performance_today_never_leaks_ids_in_display_text(client, db):
    """화면에 나가는 문자열 전체에 ID가 없어야 한다(§6 Phase 1 완료기준 1)."""
    _seed_baseline(db)
    _seed_change(db, before={"bidAmt": 2290}, after={"bidAmt": 3060}, rationale="[탐색UP] 상향")
    body = client.get("/api/naver/ad/performance/today").json()

    display_text = " ".join(
        [c[k] or "" for c in body["campaigns"]
         for k in ("name", "type_label", "status_label", "managed_by_label",
                   "verdict_sentence", "roas_unknown_reason")]
        + [i["sentence"] for i in body["today_actions"]["items"]]
        + [body["data_note"]]
    )
    for token in ("cmp-", "grp-", "nkw-", "nad-"):
        assert token not in display_text


def test_performance_today_zero_spend_campaign_has_no_fake_roas(client, db):
    """광고비 0이면 ROAS는 0배가 아니라 '알 수 없음'."""
    db.add(NaverEntity(entity_type="campaign", entity_id="cmp-idle", campaign_id="cmp-idle",
                       campaign_type="SHOPPING", name="04. 쉬는 광고", status="off",
                       status_reason="CAMPAIGN_PAUSED"))
    db.commit()
    [card] = client.get("/api/naver/ad/performance/today").json()["campaigns"]
    assert card["spend_today"] == 0
    assert card["roas_today_proxy"] is None
    assert card["status_label"] == "정지됨"
    # 라벨을 문장에 그대로 끼우면 "지금은 정지됨입니다"가 된다 — 문장은 문장으로 쓴다.
    assert "지금은 광고가 멈춰 있습니다." in card["verdict_sentence"]
    assert "정지됨입니다" not in card["verdict_sentence"]
    assert "오늘은 아직 집행된 광고비가 없습니다" in card["verdict_sentence"]


def test_performance_today_harness_accepts_explicit_now(db):
    """하니스는 시각 주입을 받는다(테스트·재현용). 라우터는 kst_now를 쓴다."""
    _seed_baseline(db)
    out = perf_today_harness.build(db, now=datetime(2000, 1, 1, 9, 0))
    assert out["date"] == "2000-01-01"
    # 그날 스냅샷이 없으므로 당일 지출은 0이고, 그것이 '모름'이 아니라 측정된 0이다.
    assert all(c["spend_today"] == 0 for c in out["campaigns"])
