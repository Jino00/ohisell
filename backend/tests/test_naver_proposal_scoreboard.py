# test_naver_proposal_scoreboard.py — 듀얼모드 스프린트 Phase 6 proposal_scoreboard 단위테스트
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverChangeLog, NaverLearningState
from app.services.naver_ad import proposal_scoreboard as sb

TODAY = date(2026, 7, 8)
EXECUTED = TODAY - timedelta(days=15)
VERIFY = EXECUTED + timedelta(days=14)  # == TODAY - 1


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


def _ad_rows(keyword_id, date_from, date_to, daily_clk, daily_conv_amt, daily_cost=1000):
    d = date_from
    rows = []
    while d <= date_to:
        rows.append(NaverAdDaily(
            ad_date=d, campaign_id="cmp1", campaign_type="WEB_SITE",
            adgroup_id="grp1", keyword_id=keyword_id, imp=100, clk=daily_clk, cost=daily_cost,
            conv_direct_amt=daily_conv_amt, conv_indirect_amt=0,
        ))
        d += timedelta(days=1)
    return rows


def _change(entity_type="keyword", entity_id="nkw-1", campaign_id="cmp1", action="update_bid",
            dry_run=True, verify_date=VERIFY, outcome=None):
    return NaverChangeLog(
        entity_type=entity_type, entity_id=entity_id, campaign_id=campaign_id, action=action,
        executed_at=datetime.combine(EXECUTED, datetime.min.time()), verify_date=verify_date,
        dry_run=dry_run, outcome=outcome, proposal_id=1,
    )


# ── evaluate_change ──
def test_evaluate_change_detects_improved(db):
    for r in _ad_rows("nkw-1", EXECUTED - timedelta(days=14), EXECUTED - timedelta(days=1), daily_clk=1, daily_conv_amt=1000):
        db.add(r)  # before: RPC=1000
    for r in _ad_rows("nkw-1", EXECUTED, VERIFY, daily_clk=1, daily_conv_amt=1500):
        db.add(r)  # after: RPC=1500 (1.5배 개선)
    db.commit()

    change = _change()
    result = sb.evaluate_change(db, change, today=TODAY)
    assert result["outcome"] == "improved"


def test_evaluate_change_detects_declined(db):
    for r in _ad_rows("nkw-1", EXECUTED - timedelta(days=14), EXECUTED - timedelta(days=1), daily_clk=1, daily_conv_amt=1000):
        db.add(r)
    for r in _ad_rows("nkw-1", EXECUTED, VERIFY, daily_clk=1, daily_conv_amt=500):
        db.add(r)  # after: RPC=500 (절반)
    db.commit()

    result = sb.evaluate_change(db, _change(), today=TODAY)
    assert result["outcome"] == "declined"


def test_evaluate_change_neutral_within_band(db):
    for r in _ad_rows("nkw-1", EXECUTED - timedelta(days=14), EXECUTED - timedelta(days=1), daily_clk=1, daily_conv_amt=1000):
        db.add(r)
    for r in _ad_rows("nkw-1", EXECUTED, VERIFY, daily_clk=1, daily_conv_amt=1050):
        db.add(r)  # 5% 변화 — neutral 밴드
    db.commit()

    result = sb.evaluate_change(db, _change(), today=TODAY)
    assert result["outcome"] == "neutral"


def test_evaluate_change_none_when_thin_sample(db):
    # 클릭 자체가 LOW_CLICK_THRESHOLD 미만
    for r in _ad_rows("nkw-1", EXECUTED - timedelta(days=1), EXECUTED - timedelta(days=1), daily_clk=1, daily_conv_amt=100):
        db.add(r)
    db.commit()
    result = sb.evaluate_change(db, _change(), today=TODAY)
    assert result["outcome"] is None
    assert result["actual_json"] is not None  # 판정은 못 해도 실측치는 기록


# ── run_daily ──
def test_run_daily_ignores_dry_run_changes(db):
    db.add(_change(dry_run=True))
    db.commit()
    result = sb.run_daily(db, today=TODAY)
    assert result["pending"] == 0  # dry_run=True는 대상 자체가 아님


def test_run_daily_ignores_changes_not_yet_due(db):
    db.add(_change(dry_run=False, verify_date=TODAY + timedelta(days=5)))
    db.commit()
    result = sb.run_daily(db, today=TODAY)
    assert result["pending"] == 0


def test_run_daily_ignores_already_verified(db):
    db.add(_change(dry_run=False, outcome="improved"))
    db.commit()
    result = sb.run_daily(db, today=TODAY)
    assert result["pending"] == 0


def test_run_daily_verifies_and_rolls_up_accuracy(db):
    for r in _ad_rows("nkw-1", EXECUTED - timedelta(days=14), EXECUTED - timedelta(days=1), daily_clk=1, daily_conv_amt=1000):
        db.add(r)
    for r in _ad_rows("nkw-1", EXECUTED, VERIFY, daily_clk=1, daily_conv_amt=1500):
        db.add(r)
    db.add(_change(dry_run=False))
    db.commit()

    result = sb.run_daily(db, today=TODAY)
    assert result["pending"] == 1
    assert result["verified"] == 1
    assert result["accuracy_by_action"]["update_bid"]["improved_ratio"] == 1.0

    change = db.query(NaverChangeLog).first()
    assert change.outcome == "improved"
    assert change.actual_json is not None

    learning_row = db.query(NaverLearningState).filter(
        NaverLearningState.scope_key == "update_bid", NaverLearningState.metric == "proposal_accuracy",
    ).first()
    assert learning_row is not None
    assert float(learning_row.current_value) == 1.0
    assert learning_row.sample_n == 1


def test_rollup_accuracy_excludes_dry_run_rows(db):
    """codex 지적: dry_run=True 건에 outcome이 채워져 있어도(수동/과거 경로) 정확도 통계에
    섞이면 안 된다 — dry_run=False 실집행 실적만 반영해야 한다."""
    db.add(_change(dry_run=True, outcome="improved"))  # 오염 시도 — 집계에서 빠져야 함
    db.add(_change(dry_run=False, outcome="declined"))
    db.commit()
    accuracy = sb._rollup_accuracy(db)
    assert accuracy["update_bid"]["n"] == 1  # dry_run=True 건은 제외
    assert accuracy["update_bid"]["improved_ratio"] == 0.0


def test_run_daily_leaves_undecided_for_thin_sample_for_retry(db):
    db.add(_change(dry_run=False))  # naver_ad_daily 데이터 없음 → 모수 미달
    db.commit()
    result = sb.run_daily(db, today=TODAY)
    assert result["pending"] == 1
    assert result["verified"] == 0

    change = db.query(NaverChangeLog).first()
    assert change.outcome is None  # 다음 회차 재시도 대상으로 남음


# ══════════════════════════════════════════════════════════════════════════
# D-NAO-223 (M3-b) — 목적함수 정합 축 `outcome_profit`
# 계약 `docs/PLAN_naver-m3-wisdom-scorecard.md` §4-B ④ · §8-Q1/Q5/Q6
#
# ★이 블록의 핵심 주장은 하나다: **두 자가 서로 «반대»를 가리키는 구간이 실재하고,
#   그 구간이 정확히 트랙 목표(D-NAO-59)가 잡으라고 한 자리다.**
#   그래서 아래 두 테스트는 legacy `outcome`과 새 `outcome_profit`을 **같은 행에서 동시에**
#   단언한다 — 새 축만 단언하면 「옛 자가 여전히 틀리게 찍는다」는 사실이 테스트에서 사라진다.
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def bep_lens(monkeypatch):
    """BEP·보정계수 렌즈를 결정적으로 고정한다.

    ⚠️정직 경계: 이 픽스처는 «판정 로직»을 재는 것이지 «렌즈 조달»을 재지 않는다.
    렌즈 조달의 실경로(상품BEP 가중평균 → 계정 기본값 → 없음)는 아래
    `test_profit_axis_holds_when_bep_unavailable`가 **패치 없이** 밟고, 최종 확인은
    prod 라이브 관측이 한다(교훈: C10에서 테스트·변이 전부가 가짜 주입이라
    `client=None` 경로를 원리적으로 못 밟았다).
    """
    def _install(bep, *, source="product_bep", cf="1"):
        from decimal import Decimal as D
        from app.services.naver_ad import campaign_target_resolver as ctr
        from app.services.naver_ad import diagnosis as diag
        if source == "product_bep":
            monkeypatch.setattr(ctr, "weighted_product_value_for_campaign", lambda db, cid, col: D(str(bep)))
        else:
            monkeypatch.setattr(ctr, "weighted_product_value_for_campaign", lambda db, cid, col: None)
            monkeypatch.setattr(ctr, "account_default_bep_roas", lambda db: D(str(bep)))
        monkeypatch.setattr(diag, "correction_factor", lambda db, date_to: {"factor": D(cf)})
    return _install


def test_profit_axis_declines_when_clicks_and_revenue_fall_but_rpc_rises(db, bep_lens):
    """★ref 90 §2-2 id 761의 재현 — 클릭 −68.5%·매출 −48.3%인데 옛 자는 「개선」.

    RPC = 매출/클릭이라 **분모가 줄면 오른다**. 총이익 절대액(D-NAO-59)으로 보면 이건
    명백한 악화다. 두 자가 반대를 가리키는 것이 이 테스트의 요점이다.
    """
    bep_lens(5)
    # before: clk 98 · 매출 175,000 · cost 14,000 → RPC 1,785.7 / ROAS 12.5
    for r in _ad_rows("nkw-1", EXECUTED - timedelta(days=14), EXECUTED - timedelta(days=1),
                      daily_clk=7, daily_conv_amt=12500, daily_cost=1000):
        db.add(r)
    # after: clk 28 · 매출 90,496 · cost 14,000 → RPC 3,232.0 / ROAS 6.46
    for r in _ad_rows("nkw-1", EXECUTED, VERIFY, daily_clk=2, daily_conv_amt=6464, daily_cost=1000):
        db.add(r)
    db.commit()

    result = sb.evaluate_change(db, _change(), today=TODAY)

    assert result["outcome"] == "improved"          # 옛 자 — 여전히 이렇게 찍는다(불변, §8-Q1)
    assert result["outcome_profit"] == "declined"   # 새 자 — 매출 절반이면 악화다
    # 두 창 모두 ROAS ≥ BEP라 페널티는 1 → GAVE는 매출 그 자체
    assert result["gave_before"] == pytest.approx(175000.0)
    assert result["gave_after"] == pytest.approx(90496.0)


def test_profit_axis_improves_when_roas_falls_but_revenue_grows(db, bep_lens):
    """★D-NAO-59 원문의 그 구간 — *"Roas는 떨어지지만 매출이 늘어서 총 이익이 늘어나는 경우"*.

    옛 자는 이것을 `declined`로 버린다(클릭이 10배 늘어 RPC가 1/5로 떨어지므로).
    새 자는 BEP 위에 있는 한 매출 증가를 그대로 인정한다.
    """
    bep_lens(3)
    # before: clk 14 · 매출 100,002 · cost 14,000 → ROAS 7.14
    for r in _ad_rows("nkw-1", EXECUTED - timedelta(days=14), EXECUTED - timedelta(days=1),
                      daily_clk=1, daily_conv_amt=7143, daily_cost=1000):
        db.add(r)
    # after: clk 140 · 매출 200,004 · cost 56,000 → ROAS 3.57 (BEP 3 위)
    for r in _ad_rows("nkw-1", EXECUTED, VERIFY, daily_clk=10, daily_conv_amt=14286, daily_cost=4000):
        db.add(r)
    db.commit()

    result = sb.evaluate_change(db, _change(), today=TODAY)

    assert result["outcome"] == "declined"          # 옛 자 — 목표가 잡으라는 구간을 버린다
    assert result["outcome_profit"] == "improved"   # 새 자 — 총이익이 늘었다
    assert result["gave_after"] > result["gave_before"]


def test_profit_axis_penalises_growth_that_falls_below_bep(db, bep_lens):
    """★새 자가 「매출만 보는 자」가 아님을 못박는다 — BEP 미달이면 매출이 늘어도 감점된다.

    이 테스트가 없으면 위 두 테스트만으로는 「그냥 매출 배율을 재는 것 아니냐」와 구분되지
    않는다. 효율은 «페널티»로 살아 있다(S = min{(roas/bep)^γ,1} × 매출).
    """
    bep_lens(5)
    # before: 매출 100,002 · cost 14,000 → ROAS 7.14 (BEP 5 위, 페널티 1)
    for r in _ad_rows("nkw-1", EXECUTED - timedelta(days=14), EXECUTED - timedelta(days=1),
                      daily_clk=1, daily_conv_amt=7143, daily_cost=1000):
        db.add(r)
    # after: 매출 110,012(+10%) · cost 56,000 → ROAS 1.96 (BEP 5 미달 → 페널티 0.393)
    for r in _ad_rows("nkw-1", EXECUTED, VERIFY, daily_clk=10, daily_conv_amt=7858, daily_cost=4000):
        db.add(r)
    db.commit()

    result = sb.evaluate_change(db, _change(), today=TODAY)

    assert result["outcome_profit"] == "declined"          # 매출은 늘었지만 BEP를 깼다
    assert result["gave_after"] < result["gave_before"]    # 페널티가 실제로 물렸다


def test_profit_axis_holds_when_bep_unavailable(db):
    """★렌즈 조달의 «실경로»를 패치 없이 밟는다 — 상품BEP도 계정 기본값도 없으면 판정 보류.

    억지로 improved/declined를 매기지 않는다(§4-0 정직 경계). `bep_source`는 그 사실을
    행에 남겨, 나중에 「왜 이 행만 새 자가 비었나」를 되짚을 수 있게 한다.
    """
    for r in _ad_rows("nkw-1", EXECUTED - timedelta(days=14), EXECUTED - timedelta(days=1),
                      daily_clk=1, daily_conv_amt=1000):
        db.add(r)
    for r in _ad_rows("nkw-1", EXECUTED, VERIFY, daily_clk=1, daily_conv_amt=1500):
        db.add(r)
    db.commit()

    result = sb.evaluate_change(db, _change(), today=TODAY)

    assert result["outcome"] == "improved"        # 옛 자는 BEP가 없어도 찍힌다
    assert result["outcome_profit"] is None       # 새 자는 렌즈 없이 찍지 않는다
    assert result["bep_source"] == "unavailable"
    assert result["gave_before"] is None and result["gave_after"] is None


def test_bep_source_label_distinguishes_product_from_blended(db, bep_lens):
    """★§4-B ⑥의 원료 — 같은 판정이라도 «상품BEP로 쟀나 계정 블렌디드 근사로 쟀나»가 남는다.

    근사값을 확정값처럼 합산하면 「돈이 됐다」 숫자 자체가 오염된다(ref 91 §3-2 E6).
    """
    bep_lens(5, source="account_default")
    for r in _ad_rows("nkw-1", EXECUTED - timedelta(days=14), EXECUTED - timedelta(days=1),
                      daily_clk=7, daily_conv_amt=12500):
        db.add(r)
    for r in _ad_rows("nkw-1", EXECUTED, VERIFY, daily_clk=2, daily_conv_amt=6464):
        db.add(r)
    db.commit()

    result = sb.evaluate_change(db, _change(), today=TODAY)
    assert result["bep_source"] == "account_default"
    assert result["outcome_profit"] == "declined"


def test_lens_is_recorded_in_actual_json_for_reproducibility(db, bep_lens):
    """★채점 재현성 — change_log엔 retro의 cf_asof/bep_asof 같은 렌즈 «컬럼»이 없다.
    렌즈를 안 남기면 gave 점수를 나중에 되짚을 수 없다(§8-Q3 확정 각주).
    """
    bep_lens(5, cf="2")
    for r in _ad_rows("nkw-1", EXECUTED - timedelta(days=14), EXECUTED - timedelta(days=1),
                      daily_clk=7, daily_conv_amt=12500):
        db.add(r)
    for r in _ad_rows("nkw-1", EXECUTED, VERIFY, daily_clk=2, daily_conv_amt=6464):
        db.add(r)
    db.commit()

    import json as _json
    result = sb.evaluate_change(db, _change(), today=TODAY)
    payload = _json.loads(result["actual_json"])
    assert payload["lens"] == {"bep": 5.0, "bep_source": "product_bep", "gamma": 1.0, "cf": 2.0}
    assert payload["before"]["cost"] == 14000 and payload["after"]["cost"] == 14000
    # cf=2면 매출이 2배로 환산된다 — 렌즈가 실제로 점수에 반영됐는지 확인
    assert result["gave_before"] == pytest.approx(350000.0)


def test_thin_sample_holds_both_axes(db, bep_lens):
    """모수게이트는 두 자가 «같은» 문턱을 쓴다 — 새 문턱을 만들지 않았다(계약 §3)."""
    bep_lens(5)
    for r in _ad_rows("nkw-1", EXECUTED - timedelta(days=2), EXECUTED - timedelta(days=1),
                      daily_clk=1, daily_conv_amt=1000):
        db.add(r)
    for r in _ad_rows("nkw-1", EXECUTED, EXECUTED + timedelta(days=1),
                      daily_clk=1, daily_conv_amt=1500):
        db.add(r)
    db.commit()

    result = sb.evaluate_change(db, _change(), today=TODAY)
    assert result["outcome"] is None
    assert result["outcome_profit"] is None
    assert result["actual_json"] is not None  # 판정은 못 해도 실측치는 기록


def test_run_daily_writes_profit_columns_without_touching_existing_outcome(db, bep_lens):
    """★§8-Q1 집행 확인 — 새 축은 «별도 컬럼»에만 쓰고, 이미 값이 박힌 행은 건드리지 않는다.

    `run_daily`의 대상 필터가 `outcome IS NULL`이므로 기존 150건은 애초에 pending에
    들어오지 않는다 — 그 사실 자체를 테스트로 못박는다(소급 UPDATE 0건, 교훈 #274).
    """
    bep_lens(5)
    for r in _ad_rows("nkw-1", EXECUTED - timedelta(days=14), EXECUTED - timedelta(days=1),
                      daily_clk=7, daily_conv_amt=12500):
        db.add(r)
    for r in _ad_rows("nkw-1", EXECUTED, VERIFY, daily_clk=2, daily_conv_amt=6464):
        db.add(r)
    db.add(_change(dry_run=False))                                  # 새로 채점될 행
    already = _change(dry_run=False, entity_id="nkw-2", outcome="improved")  # 이미 박힌 행
    db.add(already)
    db.commit()

    result = sb.run_daily(db, today=TODAY)

    assert result["pending"] == 1            # 이미 박힌 행은 대상 밖
    assert result["verified_profit"] == 1

    scored = db.query(NaverChangeLog).filter(NaverChangeLog.entity_id == "nkw-1").first()
    assert scored.outcome == "improved"          # 옛 자
    assert scored.outcome_profit == "declined"   # 새 자 — 나란히 남는다
    assert scored.bep_source == "product_bep"
    assert scored.gave_before is not None and scored.gave_after is not None

    untouched = db.query(NaverChangeLog).filter(NaverChangeLog.entity_id == "nkw-2").first()
    assert untouched.outcome == "improved"       # 원 값 불변
    assert untouched.outcome_profit is None      # 소급 채점 0건
    assert untouched.gave_before is None
