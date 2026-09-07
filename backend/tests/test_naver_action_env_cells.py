# test_naver_action_env_cells.py — 조치 × 환경 채점 1라운드 (D-NAO-299 · 계약 D-NAO-266 T5)
#
# 원칙22: SA 단위테스트는 라우터를 안 거치므로 라우터 레이어 500을 못 잡는다 — **HTTP 왕복**으로 쓴다.
#
# ★이 파일이 지키는 것은 「값이 잘 계산되나」가 아니라 **「그 값이 응답까지 도달하나」**다.
#   이 저장소가 반복해 밟은 병이 정확히 그것이고(교훈 #346 계열 · n=95 P1-1 「수정이 한 층
#   늦었다」), 직전 세션의 적대 리뷰가 남긴 SURVIVED 변이 3종이 전부 «라우터 아래를 아무도
#   안 보고 있었다»는 자리였다. 그래서 여기서는 손으로 만든 dict를 서비스에 넣지 않는다 —
#   전부 DB에 심고 HTTP로 꺼낸다.
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    NaverAdDaily,
    NaverAgencyOp,
    NaverProductBep,
    NaverSearchTermExclusion,
)
from app.services.naver_ad import action_env_cells
from app.utils.kst import kst_today

CAMPAIGN = "cmp-t5"
URL = "/api/naver/ad/bm/action-env-cells"

# BEP=2로 심는다 — ad_profit = conv_amt/2 − cost 가 암산으로 검산되는 값이라
# 「수축식이 정말 그 식인가」를 사람이 눈으로 확인할 수 있다.
BEP = Decimal("2")


def _mature_end() -> date:
    return kst_today() - timedelta(days=action_env_cells.MATURITY_CUT_DAYS)


def _pick(*, env: str, clean: bool, before: date | None = None) -> date:
    """창 안에서 조건을 만족하는 날짜 하나를 «고른다»(하드코딩 금지 — CI가 어느 요일에 돌든
    같은 것을 검사해야 한다). clean=True면 미확정 환경 라벨이 0건인 날짜."""
    d = before or _mature_end()
    for _ in range(400):
        if action_env_cells.env_layer_of_date(d) == env:
            has_label = bool(action_env_cells.unverified_env_labels(d))
            if has_label != clean:
                return d
        d -= timedelta(days=1)
    raise AssertionError(f"조건을 만족하는 날짜를 못 찾았다: env={env} clean={clean}")


def _vacation_day() -> date:
    """가장 최근의 «성숙한» 휴가창 날짜(ref 63 F7 창 = build_panel.py:27-28의 7/20~8/15)."""
    end = _mature_end()
    for y in (end.year, end.year - 1):
        d = date(y, 8, 1)
        if d <= end:
            assert "vacation_window" in action_env_cells.unverified_env_labels(d)
            return d
    raise AssertionError("성숙한 휴가창 날짜를 못 찾았다")


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


def _seed_bep(db, *, bep_roas: Decimal | None = BEP):
    if bep_roas is None:
        return
    db.add(NaverProductBep(
        channel_id=6, channel_product_id="pid-t5", product_name="테스트 필름",
        selling_price=Decimal("15900"), cost_price=Decimal("3000"),
        commission_rate=Decimal("0.0780"), logistics_cost=Decimal("1900"),
        contribution_margin=Decimal("5000"), bep_roas=bep_roas, has_cost=True,
    ))


def _seed_perf(db, d: date, *, cost: int, conv_amt: int, campaign_id: str = CAMPAIGN):
    db.add(NaverAdDaily(
        ad_date=d, campaign_id=campaign_id, campaign_type="SHOPPING",
        adgroup_id="grp-1", keyword_id="", imp=100, clk=10, cost=cost,
        conv_direct_cnt=1, conv_indirect_cnt=0,
        conv_direct_amt=conv_amt, conv_indirect_amt=0,
    ))


def _seed_op(db, d: date, op_type: str, *, n: int = 1, campaign_id: str = CAMPAIGN,
             feed_verdict: str | None = None, entity_type: str = "adgroup"):
    for i in range(n):
        db.add(NaverAgencyOp(
            op_date=d, detected_at=datetime(d.year, d.month, d.day, 9, 0),
            entity_type=entity_type, entity_id=f"e-{op_type}-{d}-{i}",
            campaign_id=campaign_id, optimizer="none", op_type=op_type,
            feed_verdict=feed_verdict,
        ))


def _cell(payload, action, env):
    return next(c for c in payload["cells"] if c["action_type"] == action and c["env"] == env)


# ──────────────────────────────────────────────────────────────────────────
# ★표면 절단 변이 — 셀 표가 «API 응답까지» 도달하는가
# ──────────────────────────────────────────────────────────────────────────

def test_surface_cells_reach_the_api_response(client_and_session):
    """★계약 §4-C S2-⑤가 요구한 넷 (n, raw, shrunk, 확정도)이 **HTTP 응답 본문**에 실린다.

    라우터 직렬화에서 이 키를 지우거나 하니스 호출을 끊으면 여기서 죽는다 — 서비스층만
    보는 테스트는 그 절단을 통과시킨다(n=95 P1-1이 정확히 그 자리였다)."""
    client, db = client_and_session
    _seed_bep(db)
    d = _pick(env="weekday", clean=True)
    _seed_perf(db, d, cost=10_000, conv_amt=60_000)
    _seed_op(db, d, "bid_change", n=3)
    db.commit()

    r = client.get(URL)
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "ok"

    # 셀은 5종 × 2층 = 10칸이 «항상» 다 나온다(0건 셀도 숨기지 않는다).
    assert len(payload["cells"]) == len(action_env_cells.ACTION_TYPES) * len(action_env_cells.ENV_LAYERS)
    for c in payload["cells"]:
        for key in ("n", "raw", "shrunk", "certainty"):
            assert key in c, f"응답에 {key}가 없다 — 셀 표의 계약이 깨졌다"

    cell = _cell(payload, "bid_change", "weekday")
    assert cell["n"] == 1 and cell["ops"] == 3
    assert cell["raw"] == pytest.approx(20_000.0)   # 60,000/2 − 10,000


def test_surface_response_says_it_is_not_causal(client_and_session):
    """★인과 오독 방지 키가 응답에 실린다 — 경고를 문서에만 두면 소비처는 못 본다."""
    client, db = client_and_session
    _seed_bep(db)
    db.commit()
    payload = client.get(URL).json()
    assert payload["causal"] is False
    assert "DiD" in payload["causal_note"]


# ──────────────────────────────────────────────────────────────────────────
# grain — 조치 «건수»가 아니라 조치-일
# ──────────────────────────────────────────────────────────────────────────

def test_same_campaign_day_counts_once_but_keeps_op_density(client_and_session):
    """같은 캠페인·같은 날 30번 만져도 그날의 ad_profit은 하나뿐이다. n=1 / ops=30."""
    client, db = client_and_session
    _seed_bep(db)
    d = _pick(env="weekday", clean=True)
    _seed_perf(db, d, cost=10_000, conv_amt=60_000)
    _seed_op(db, d, "bid_change", n=30)
    db.commit()

    cell = _cell(client.get(URL).json(), "bid_change", "weekday")
    assert cell["n"] == 1
    assert cell["ops"] == 30
    assert cell["ad_profit_sum"] == 20_000


# ──────────────────────────────────────────────────────────────────────────
# 환경 2층 · 수축 체인
# ──────────────────────────────────────────────────────────────────────────

def test_env_split_and_shrink_chain(client_and_session):
    """평시/주말+공휴일로 갈리고, 셀 수축이 «부모(조치 유형)의 수축값»을 prior로 쓴다.

    ★루트 raw와 부모 수축값이 **다른 숫자가 되도록** 심는다. 초판 픽스처는 둘이 우연히
    같아서(둘 다 10,000) 수축 체인을 「전체 → 조치유형 → 셀」에서 「전체 → 셀」로 끊는
    변이가 **살아남았다**(자기 변이검증 M6 SURVIVED). 값이 같은 픽스처는 체인을 검사하지
    못한다 — 이 테스트가 검사하는 것은 «숫자»가 아니라 «누구를 prior로 삼는가»다.

    심는 것(BEP=2): bid_change 평시 +20,000 · bid_change 주말 0 · status_flip 평시 +40,000
      루트  n=3 · sum 60,000 · raw = 20,000
      bid_change  n=2 · raw = 10,000 · shrunk = (2×10,000 + 10×20,000)/12 = 55,000/3
      bid_change×평시 n=1 · raw = 20,000 · shrunk = (20,000 + 10×55,000/3)/11 = 610,000/33
    (루트를 prior로 잘못 쓰면 20,000이 나온다 — 610,000/33 ≒ 18,484.85와 확연히 다르다.)
    """
    client, db = client_and_session
    _seed_bep(db)
    d_wd = _pick(env="weekday", clean=True)
    d_we = _pick(env="weekend_holiday", clean=True)
    d_wd2 = _pick(env="weekday", clean=True, before=min(d_wd, d_we) - timedelta(days=1))
    _seed_perf(db, d_wd, cost=10_000, conv_amt=60_000)    # +20,000
    _seed_perf(db, d_we, cost=10_000, conv_amt=20_000)    # 0
    _seed_perf(db, d_wd2, cost=10_000, conv_amt=100_000)  # +40,000
    _seed_op(db, d_wd, "bid_change")
    _seed_op(db, d_we, "bid_change")
    _seed_op(db, d_wd2, "status_flip")
    db.commit()

    payload = client.get(URL).json()
    assert payload["overall"]["n"] == 3
    assert payload["overall"]["raw"] == pytest.approx(20_000.0)

    act = payload["by_action"]["bid_change"]
    assert act["n"] == 2
    assert act["raw"] == pytest.approx(10_000.0)
    assert act["prior"] == pytest.approx(20_000.0)          # 부모의 prior = 루트 raw
    assert act["shrunk"] == pytest.approx(55_000 / 3)

    wd = _cell(payload, "bid_change", "weekday")
    we = _cell(payload, "bid_change", "weekend_holiday")
    assert wd["n"] == 1 and we["n"] == 1
    # ★셀의 prior는 «부모의 수축값»이지 루트 raw가 아니다 — 체인을 끊으면 여기서 죽는다.
    assert wd["prior"] == pytest.approx(55_000 / 3)
    assert wd["prior"] != pytest.approx(payload["overall"]["raw"])
    assert wd["prior_level"] == "action:bid_change"
    assert wd["shrunk"] == pytest.approx(610_000 / 33)
    assert we["shrunk"] == pytest.approx((0 + 10 * 55_000 / 3) / 11)


def test_empty_cell_is_marked_all_prior(client_and_session):
    """n=0 셀의 수축값은 «전부 prior»다 — 관측이 아니라는 것을 숨기지 않는다."""
    client, db = client_and_session
    _seed_bep(db)
    d = _pick(env="weekday", clean=True)
    _seed_perf(db, d, cost=10_000, conv_amt=60_000)
    _seed_op(db, d, "bid_change")
    db.commit()

    payload = client.get(URL).json()
    we = _cell(payload, "bid_change", "weekend_holiday")
    assert we["n"] == 0
    assert we["all_prior"] is True
    assert we["shrunk"] == pytest.approx(we["prior"])


# ──────────────────────────────────────────────────────────────────────────
# 확정도 — 「미확정 셀이 확정으로 표기된 사례 0건」(계약 §4-C S2-⑤ 원문)
# ──────────────────────────────────────────────────────────────────────────

def test_clean_cell_is_certain_and_vacation_cell_is_not(client_and_session):
    """미확정 환경 라벨이 «한 건이라도» 걸리면 그 셀은 미확정이다(비율 문턱 없음)."""
    client, db = client_and_session
    _seed_bep(db)
    d_clean = _pick(env="weekday", clean=True)
    _seed_perf(db, d_clean, cost=10_000, conv_amt=60_000)
    _seed_op(db, d_clean, "bid_change")

    d_vac = _vacation_day()
    _seed_perf(db, d_vac, cost=10_000, conv_amt=40_000)
    _seed_op(db, d_vac, "status_flip")
    db.commit()

    days = (_mature_end() - d_vac).days + 1
    payload = client.get(f"{URL}?days={days}").json()

    clean_cell = _cell(payload, "bid_change", action_env_cells.env_layer_of_date(d_clean))
    assert clean_cell["certainty"] == "확정"

    vac_cell = _cell(payload, "status_flip", action_env_cells.env_layer_of_date(d_vac))
    assert vac_cell["n"] == 1
    assert vac_cell["certainty"] == "미확정"
    assert vac_cell["unverified_labels"].get("vacation_window") == 1


def test_no_cell_claims_certain_while_carrying_unverified_labels(client_and_session):
    """★합격기준 원문의 «0건» 검사 그 자체 — 라벨을 든 셀이 확정으로 표기되면 실패."""
    client, db = client_and_session
    _seed_bep(db)
    d_vac = _vacation_day()
    _seed_perf(db, d_vac, cost=10_000, conv_amt=40_000)
    _seed_op(db, d_vac, "bid_change")
    db.commit()

    days = (_mature_end() - d_vac).days + 1
    payload = client.get(f"{URL}?days={days}").json()
    offenders = [c for c in payload["cells"] if c["unverified_labels"] and c["certainty"] == "확정"]
    assert offenders == []


# ──────────────────────────────────────────────────────────────────────────
# 원료 축 — 제외 원장 · 5종 밖 · 피드 잡음 · 성숙 컷 · 결측
# ──────────────────────────────────────────────────────────────────────────

def test_exclusion_ledger_is_the_fifth_action_type(client_and_session):
    """제외는 `naver_agency_op`가 아니라 `console_excluded_at` 원장에서 온다."""
    client, db = client_and_session
    _seed_bep(db)
    d = _pick(env="weekday", clean=True)
    _seed_perf(db, d, cost=10_000, conv_amt=60_000)
    db.add(NaverSearchTermExclusion(
        campaign_id=CAMPAIGN, adgroup_id="grp-1", search_term="지문방지",
        excluded_at=datetime(d.year, d.month, d.day, 1, 0),
        last_transition_at=datetime(d.year, d.month, d.day, 1, 0),
        console_excluded_at=datetime(d.year, d.month, d.day, 22, 26),
    ))
    db.commit()

    cell = _cell(client.get(URL).json(), "exclusion", "weekday")
    assert cell["n"] == 1 and cell["ops"] == 1
    assert cell["raw"] == pytest.approx(20_000.0)


def test_op_types_outside_the_five_are_counted_not_silently_dropped(client_and_session):
    """5종 밖을 «버린 채로» 센다 — 안 세면 5종이 전부로 읽힌다(계약 §2-5)."""
    client, db = client_and_session
    _seed_bep(db)
    d = _pick(env="weekday", clean=True)
    _seed_perf(db, d, cost=10_000, conv_amt=60_000)
    _seed_op(db, d, "ad_edit", n=4)
    _seed_op(db, d, "keyword_add", n=2)
    db.commit()

    payload = client.get(URL).json()
    assert payload["op_types_outside_scope"] == {"ad_edit": 4, "keyword_add": 2}
    assert payload["overall"]["n"] == 0


def test_feed_reapply_noise_is_excluded(client_and_session):
    """피드 재적용은 사람의 조치가 아니다(D-NAO-139) — 셀에도 인구조사에도 안 들어간다."""
    client, db = client_and_session
    _seed_bep(db)
    d = _pick(env="weekday", clean=True)
    _seed_perf(db, d, cost=10_000, conv_amt=60_000)
    _seed_op(db, d, "bid_change", n=2, feed_verdict="feed")
    db.commit()

    payload = client.get(URL).json()
    assert payload["overall"]["ops"] == 0
    assert payload["op_types_outside_scope"] == {}


def test_maturity_cut_excludes_recent_days(client_and_session):
    """성숙 컷 D−8 — 최근 조치는 창에 안 들어온다(전환 지연이 셀을 손실로 물들이는 것 차단)."""
    client, db = client_and_session
    _seed_bep(db)
    d_recent = kst_today() - timedelta(days=1)
    _seed_perf(db, d_recent, cost=10_000, conv_amt=60_000)
    _seed_op(db, d_recent, "bid_change", n=5)
    db.commit()

    payload = client.get(URL).json()
    assert payload["window"]["date_to"] == _mature_end().isoformat()
    assert payload["overall"]["ops"] == 0


def test_action_day_without_performance_row_is_unmatched_not_zero(client_and_session):
    """성과 행이 없는 조치-일을 0원으로 채우면 셀 평균이 조용히 낙관된다 — 뺀 것을 센다."""
    client, db = client_and_session
    _seed_bep(db)
    d1 = _pick(env="weekday", clean=True)
    d2 = _pick(env="weekday", clean=True, before=d1 - timedelta(days=1))
    _seed_perf(db, d1, cost=10_000, conv_amt=60_000)   # d2에는 성과 행 없음
    _seed_op(db, d1, "bid_change")
    _seed_op(db, d2, "bid_change")
    db.commit()

    cell = _cell(client.get(URL).json(), "bid_change", "weekday")
    assert cell["n"] == 1
    assert cell["ops"] == 2
    assert cell["unmatched_days"] == 1
    assert cell["raw"] == pytest.approx(20_000.0)   # 0원이 섞였으면 10,000으로 내려간다


def test_missing_bep_stops_the_table_instead_of_filling_zeros(client_and_session):
    """자가 없으면 표를 만들지 않는다 — 0으로 채우면 「전 셀 손실」이라는 거짓 표가 나온다."""
    client, db = client_and_session
    d = _pick(env="weekday", clean=True)
    _seed_perf(db, d, cost=10_000, conv_amt=60_000)
    _seed_op(db, d, "bid_change")
    db.commit()

    payload = client.get(URL).json()
    assert payload["status"] == "bep_unavailable"
    assert payload["cells"] == []
    assert payload["yardstick"]["bep_roas"] is None
