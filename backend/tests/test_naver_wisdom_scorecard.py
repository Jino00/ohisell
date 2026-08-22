# test_naver_wisdom_scorecard.py — M3-a 지혜 성적표 조인 배선 단위테스트
# 계약 docs/PLAN_naver-m3-wisdom-scorecard.md §4-A① · §4-B⑥ · §8-Q5(델타 크기)
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdgroupProduct,
    NaverAdgroupTargetCurrent,
    NaverChangeLog,
    NaverProductBep,
    NaverProposal,
    OpsWisdomEntry,
)
from app.services.naver_ad import wisdom_scorecard as ws


@pytest.fixture
def db():
    # ★StaticPool + check_same_thread=False: TestClient는 다른 스레드에서 도므로 기본 풀이면
    #   「테이블이 없다」로 깨진다 — HTTP 경계 테스트를 «쓸 수 있는 모양»이어야 한다(교훈 #321).
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    # ★autoflush=False — prod 세션과 같은 모양(교훈 #292: 픽스처가 prod보다 관대하면
    #   query-then-add 이중 INSERT류 결함을 테스트가 못 잡는다).
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _wisdom(db, *, wid=1, proposal_id=None, text="주말엔 bid_up을 차단한다."):
    e = OpsWisdomEntry(
        id=wid, wisdom_text=text, source_candidate_id=wid, status="active",
        promoted_at=datetime(2026, 7, 27, 8, 45), param_proposal_id=proposal_id,
    )
    db.add(e)
    db.commit()
    return e


def _proposal(db, *, pid, status="approved", change_log_id=None, campaign_id="cmp1"):
    p = NaverProposal(
        id=pid, proposal_type="param_change", target_type="campaign", target_id=campaign_id,
        campaign_id=campaign_id, status=status, executed_change_log_id=change_log_id,
    )
    db.add(p)
    db.commit()
    return p


def _actual_json(*, before, after, bep=2.0, cf=1.0, bep_source="product_bep"):
    """proposal_scoreboard가 실제로 적는 모양(:296-309)을 그대로 흉내낸다."""
    import json
    return json.dumps({
        "before": before, "after": after,
        "lens": {"bep": bep, "bep_source": bep_source, "gamma": 1.0, "cf": cf},
    }, ensure_ascii=False)


def _change(db, *, cid, proposal_id=None, outcome_profit=None, gave_before=None,
            gave_after=None, bep_source=None, outcome=None, action="update_bid",
            actual_json=None, dry_run=False):
    c = NaverChangeLog(
        id=cid, changed_at=datetime(2026, 7, 28, 9, 0), entity_type="campaign",
        entity_id="cmp1", campaign_id="cmp1", action=action, dry_run=dry_run,
        proposal_id=proposal_id, outcome=outcome, outcome_profit=outcome_profit,
        gave_before=gave_before, gave_after=gave_after, bep_source=bep_source,
        actual_json=actual_json,
    )
    db.add(c)
    db.commit()
    return c


# ── ① 조인이 실제로 이어지는가 (계약 §4-A①) ────────────────────────────────
def test_wisdom_joins_to_change_log_through_proposal(db):
    _change(db, cid=10, proposal_id=100, outcome_profit="improved",
            gave_before=1.0, gave_after=3.5, bep_source="product_bep")
    _proposal(db, pid=100, change_log_id=10)
    _wisdom(db, wid=1, proposal_id=100)

    out = ws.build(db)
    assert out["wisdom_total"] == 1
    row = out["wisdom"][0]
    assert row["wisdom_id"] == 1
    assert row["linked_proposal_count"] == 1
    assert row["changes_total"] == 1
    assert row["changes_scored_profit"] == 1
    assert row["verdicts"] == {"improved": 1}
    assert row["has_evidence"] is True
    assert row["evidence_gap"] is None


def test_join_works_when_only_change_log_side_is_linked(db):
    """제안 쪽 executed_change_log_id가 비어도 조치 쪽 proposal_id로 이어져야 한다.
    한 방향만 보면 이런 행을 통째로 놓친다."""
    _change(db, cid=11, proposal_id=101, outcome_profit="declined", bep_source="account_default")
    _proposal(db, pid=101, change_log_id=None)
    _wisdom(db, wid=1, proposal_id=101)

    row = ws.build(db)["wisdom"][0]
    assert row["changes_total"] == 1
    assert row["verdicts"] == {"declined": 1}


def test_join_works_when_only_proposal_side_is_linked(db):
    """반대 방향 — 조치의 proposal_id가 비어도 제안의 executed_change_log_id로 이어져야."""
    _change(db, cid=12, proposal_id=None, outcome_profit="improved", bep_source="product_bep")
    _proposal(db, pid=102, change_log_id=12)
    _wisdom(db, wid=1, proposal_id=102)

    row = ws.build(db)["wisdom"][0]
    assert row["changes_total"] == 1
    assert row["changes_scored_profit"] == 1


def test_no_double_count_when_both_sides_linked(db):
    """양방향이 다 채워져 있어도 같은 조치를 두 번 세지 않는다."""
    _change(db, cid=13, proposal_id=103, outcome_profit="improved")
    _proposal(db, pid=103, change_log_id=13)
    _wisdom(db, wid=1, proposal_id=103)

    row = ws.build(db)["wisdom"][0]
    assert row["changes_total"] == 1
    assert len(row["details"]) == 1


# ── 표본 0을 «좋은 성적»으로 렌더하지 않는다 ────────────────────────────────
def test_wisdom_without_proposal_reports_gap_not_zero_score(db):
    _wisdom(db, wid=1, proposal_id=None)
    row = ws.build(db)["wisdom"][0]
    assert row["has_evidence"] is False
    assert row["changes_scored_profit"] == 0
    assert "제안을 낳지 않았다" in row["evidence_gap"]


def test_rejected_proposal_reports_gap_with_status(db):
    """prod의 실제 형태 — 지혜 1건이 낸 제안이 반려돼 실집행이 0건인 경우."""
    _proposal(db, pid=2314, status="rejected", change_log_id=None)
    _wisdom(db, wid=1, proposal_id=2314)

    out = ws.build(db)
    row = out["wisdom"][0]
    assert row["has_evidence"] is False
    assert row["linked_proposal_count"] == 1
    assert row["changes_total"] == 0
    assert "rejected" in row["evidence_gap"]
    assert out["wisdom_with_evidence"] == 0


def test_change_exists_but_unscored_reports_distinct_gap(db):
    """조치는 있는데 새 식으로 아직 안 찍힌 경우 — 위 두 경우와 «다른» 사유여야 한다."""
    _change(db, cid=14, proposal_id=104, outcome_profit=None, outcome="improved")
    _proposal(db, pid=104, change_log_id=14)
    _wisdom(db, wid=1, proposal_id=104)

    row = ws.build(db)["wisdom"][0]
    assert row["has_evidence"] is False
    assert row["changes_total"] == 1
    assert "채점된 행이 0건" in row["evidence_gap"]


# ── ⑥ 값 정확도 라벨 (계약 §4-B⑥) ──────────────────────────────────────────
def test_bep_source_label_is_carried_per_row_and_rolled_up(db):
    _change(db, cid=20, proposal_id=200, outcome_profit="improved", bep_source="product_bep")
    _change(db, cid=21, proposal_id=200, outcome_profit="declined", bep_source="account_default")
    _proposal(db, pid=200, change_log_id=20)
    _wisdom(db, wid=1, proposal_id=200)

    row = ws.build(db)["wisdom"][0]
    assert row["bep_sources"] == {"product_bep": 1, "account_default": 1}
    assert {d["bep_source"] for d in row["details"]} == {"product_bep", "account_default"}


def test_unmeasured_bep_source_is_labeled_not_silently_dropped(db):
    _change(db, cid=22, proposal_id=201, outcome_profit=None, bep_source=None)
    _proposal(db, pid=201, change_log_id=22)
    _wisdom(db, wid=1, proposal_id=201)

    row = ws.build(db)["wisdom"][0]
    assert row["bep_sources"] == {"unmeasured": 1}


def test_value_definition_is_present_in_output(db):
    _wisdom(db, wid=1)
    vd = ws.build(db)["value_definition"]
    assert vd["formula"] == ws.PROFIT_FORMULA
    assert vd["grain"] == ws.PROFIT_GRAIN
    assert "conversion_delay" in vd and "window" in vd["conversion_delay"]
    assert "bep_coverage" in vd


def test_conversion_delay_reports_correction_disabled(db):
    """정착 보정은 실제로 꺼져 있다 — 성적표가 「보정된 값」인 척하면 거짓이다."""
    _wisdom(db, wid=1)
    delay = ws.build(db)["value_definition"]["conversion_delay"]
    assert delay["correction_applied"] is False
    assert "적용되지 않는다" in delay["note"]


# ── §8-Q5 확정 각주: 델타 «크기»를 함께 본다 ───────────────────────────────
def test_gave_delta_magnitude_is_rolled_up(db):
    _change(db, cid=30, proposal_id=300, outcome_profit="improved", gave_before=2.0, gave_after=5.0)
    _change(db, cid=31, proposal_id=300, outcome_profit="improved", gave_before=1.0, gave_after=1.5)
    _proposal(db, pid=300, change_log_id=30)
    _wisdom(db, wid=1, proposal_id=300)

    row = ws.build(db)["wisdom"][0]
    assert row["gave_pairs"] == 2
    assert row["gave_delta_sum"] == pytest.approx(3.5)
    assert [d["gave_delta"] for d in row["details"]] == [pytest.approx(3.0), pytest.approx(0.5)]


def test_gave_delta_is_none_when_only_one_side_present(db):
    """한쪽만 있으면 델타를 지어내지 않는다."""
    _change(db, cid=32, proposal_id=301, outcome_profit="improved", gave_before=2.0, gave_after=None)
    _proposal(db, pid=301, change_log_id=32)
    _wisdom(db, wid=1, proposal_id=301)

    row = ws.build(db)["wisdom"][0]
    assert row["gave_pairs"] == 0
    assert row["gave_delta_sum"] is None
    assert row["details"][0]["gave_delta"] is None


# ── 귀속의 한계가 산출물에 실려 나가는가 ────────────────────────────────────
def test_attribution_limitation_is_shipped_with_output(db):
    _wisdom(db, wid=1)
    att = ws.build(db)["attribution"]
    assert "param_proposal_id" in att["path"]
    assert "하한" in att["limitation"]


def test_legacy_outcome_is_preserved_alongside_new_verdict(db):
    """§8-Q1 — 옛 자는 불변 증거로 함께 나가야 한다(새 자가 옛 값을 덮거나 가리지 않는다)."""
    _change(db, cid=40, proposal_id=400, outcome="neutral", outcome_profit="improved")
    _proposal(db, pid=400, change_log_id=40)
    _wisdom(db, wid=1, proposal_id=400)

    d = ws.build(db)["wisdom"][0]["details"][0]
    assert d["outcome_legacy"] == "neutral"
    assert d["outcome_profit"] == "improved"


def test_wisdom_id_filter_returns_only_that_entry(db):
    _wisdom(db, wid=1)
    _wisdom(db, wid=2, text="다른 지혜")
    out = ws.build(db, wisdom_id=2)
    assert out["wisdom_total"] == 1
    assert out["wisdom"][0]["wisdom_id"] == 2


# ── ★HTTP 경계 — 판정면이 body까지 오는가 (교훈 #321) ──────────────────────
def test_wisdom_scorecard_route_actually_returns_verdict_surface(db):
    """★서비스층 dict만 보는 테스트는 이 사고를 **원리적으로** 못 잡는다.

    FastAPI는 `response_model`에 없는 키를 직렬화에서 뺀다. 이 저장소에서 그 사고가
    이미 세 번 났다(교훈 #319·#321, schemas.py 경고 주석 4개). 이 성적표의 «판정면»은
    `wisdom[].outcome_profit`·`bep_source`·`evidence_gap`·`attribution.limitation`이고,
    그중 하나라도 body에서 사라지면 화면은 «성적이 없다»가 아니라 «문제없다»로 읽힌다.
    """
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from app.database import get_db  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    _change(db, cid=50, proposal_id=500, outcome="neutral", outcome_profit="improved",
            gave_before=1.0, gave_after=4.0, bep_source="account_default")
    _proposal(db, pid=500, change_log_id=50)
    _wisdom(db, wid=1, proposal_id=500)

    app.dependency_overrides[get_db] = lambda: db
    try:
        r = TestClient(app).get("/api/naver/ad/wisdom-scorecard")
        assert r.status_code == 200
        body = r.json()
        assert body["wisdom_total"] == 1
        row = body["wisdom"][0]
        # 판정면 4종이 전부 HTTP body까지 와야 화면이 읽는다
        assert row["details"][0]["outcome_profit"] == "improved"
        assert row["details"][0]["bep_source"] == "account_default"
        assert row["details"][0]["gave_delta"] == pytest.approx(3.0)
        assert "evidence_gap" in row and "has_evidence" in row
        assert "limitation" in body["attribution"]
        assert body["value_definition"]["formula"]
        assert body["value_definition"]["bep_coverage"] is not None


    finally:
        app.dependency_overrides.pop(get_db, None)


def test_wisdom_scorecard_route_survives_empty_ledger(db):
    """지혜가 0건이어도 500이 아니라 «비었다»를 정상 응답해야 한다 —
    라이브 첫 호출이 500으로 죽은 전례가 있다(C10 적재, 2026-08-21)."""
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from app.database import get_db  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    app.dependency_overrides[get_db] = lambda: db
    try:
        r = TestClient(app).get("/api/naver/ad/wisdom-scorecard")
        assert r.status_code == 200
        assert r.json()["wisdom_total"] == 0
        assert r.json()["wisdom"] == []
    finally:
        app.dependency_overrides.pop(get_db, None)


# ── ★적대 리뷰 1R P1-1 회귀: 총이익 «금액»이 산출물에 실리는가 ────────────────
def test_profit_amount_is_rolled_up_not_just_the_label(db):
    """★이 테스트가 없어서 초판이 나갔다.

    계약 §4-A①은 「GAVE 점수·**ad_profit** 합」을 요구한다. 라벨(improved/declined)만
    내면 「지혜가 돈을 얼마 벌었나」에 원리적으로 답할 수 없고, 화면의 유일한 크기 숫자가
    GAVE가 되는데 **GAVE엔 비용을 빼는 항이 없어** 판정과 반대 부호를 가리킬 수 있다.
    아래 수치가 정확히 그 상황이다(적대 리뷰 재현값).
    """
    # before: conv 2,000,000 / cost 566,667 → 2,000,000/2 - 566,667 = 433,333
    # after : conv 1,000,000 / cost 600,000 → 1,000,000/2 - 600,000 = -100,000
    aj = _actual_json(
        before={"clk": 100, "conv_amt": 2_000_000, "cost": 566_667},
        after={"clk": 60, "conv_amt": 1_000_000, "cost": 600_000},
        bep=2.0, cf=1.0,
    )
    _change(db, cid=60, proposal_id=600, outcome_profit="declined",
            gave_before=1_000_000.0, gave_after=1_250_000.0, actual_json=aj)
    _proposal(db, pid=600, change_log_id=60)
    _wisdom(db, wid=1, proposal_id=600)

    row = ws.build(db)["wisdom"][0]
    assert row["profit_pairs"] == 1
    assert row["profit_before_sum"] == pytest.approx(433_333.0)
    assert row["profit_after_sum"] == pytest.approx(-100_000.0)
    assert row["profit_delta_sum"] == pytest.approx(-533_333.0)
    d = row["details"][0]
    assert d["profit_delta"] == pytest.approx(-533_333.0)
    # ★핵심: GAVE 델타는 «양수»인데 총이익 델타는 «음수»다 — 둘이 반대를 가리킨다.
    assert d["gave_delta"] > 0 and d["profit_delta"] < 0


def test_profit_amount_is_not_invented_when_lens_missing(db):
    """렌즈(bep·cf)가 없으면 금액을 지어내지 않고 «산출불가»로 센다.
    prod의 기존 actual_json 249건에는 lens가 0건이다 — 소급되지 않는다."""
    import json
    aj = json.dumps({"before": {"clk": 10, "conv_amt": 100, "cost": 50},
                     "after": {"clk": 10, "conv_amt": 200, "cost": 50}})
    _change(db, cid=61, proposal_id=601, outcome_profit="improved", actual_json=aj)
    _proposal(db, pid=601, change_log_id=61)
    _wisdom(db, wid=1, proposal_id=601)

    row = ws.build(db)["wisdom"][0]
    assert row["profit_pairs"] == 0
    assert row["profit_delta_sum"] is None
    assert row["profit_unavailable"] == 1
    assert row["details"][0]["profit_delta"] is None


def test_profit_amount_survives_broken_actual_json(db):
    _change(db, cid=62, proposal_id=602, outcome_profit="improved", actual_json="{not json")
    _proposal(db, pid=602, change_log_id=62)
    _wisdom(db, wid=1, proposal_id=602)
    row = ws.build(db)["wisdom"][0]
    assert row["profit_unavailable"] == 1
    assert row["profit_delta_sum"] is None


# ── ★적대 리뷰 BM3 회귀: GAVE 0.0을 짝에서 버리지 않는다 ─────────────────────
def test_gave_zero_is_a_real_value_not_falsy(db):
    """GAVE = min((ROAS/BEP)^gamma,1) x revenue 이므로 0.0은 정상값이다.
    `if gb and ga`로 쓰면 행에는 델타가 있는데 합계는 None이 되어 서로 다른 말을 한다."""
    _change(db, cid=63, proposal_id=603, outcome_profit="improved",
            gave_before=0.0, gave_after=4.0)
    _proposal(db, pid=603, change_log_id=63)
    _wisdom(db, wid=1, proposal_id=603)

    row = ws.build(db)["wisdom"][0]
    assert row["gave_pairs"] == 1
    assert row["gave_delta_sum"] == pytest.approx(4.0)
    assert row["details"][0]["gave_delta"] == pytest.approx(4.0)


# ── ★적대 리뷰 BM7 회귀: 커버리지 «숫자»를 검사한다 ─────────────────────────
def _coverage_fixture(db):
    db.add(NaverAdgroupTargetCurrent(adgroup_id="g1"))
    db.add(NaverAdgroupTargetCurrent(adgroup_id="g2"))
    db.add(NaverAdgroupProduct(adgroup_id="g1", campaign_id="cmp1", mall_product_id="p1"))
    db.add(NaverProductBep(channel_id=6, channel_product_id="p1", has_cost=True, bep_roas=2.0))
    db.commit()


def test_bep_coverage_counts_only_real_product_bep(db):
    """has_cost=False나 bep_roas=None을 「상품BEP 확보」로 세면 §4-B⑥이 막으려던
    오염(근사를 확정값처럼)이 그대로 들어온다."""
    _coverage_fixture(db)
    db.add(NaverAdgroupProduct(adgroup_id="g2", campaign_id="cmp1", mall_product_id="p2"))
    db.add(NaverProductBep(channel_id=6, channel_product_id="p2", has_cost=False, bep_roas=None))
    db.commit()
    _wisdom(db, wid=1)

    cov = ws.build(db)["value_definition"]["bep_coverage"]
    assert cov["groups_total"] == 2
    assert cov["groups_with_product_bep"] == 1
    assert cov["ratio"] == pytest.approx(0.5)


def test_bep_coverage_numerator_cannot_exceed_denominator(db):
    """분자(누적 원장)가 분모(현재 스윕)를 넘으면 화면에 250% 같은 숫자가 뜬다."""
    _coverage_fixture(db)
    # 현재 스윕에 «없는» 그룹이 누적 원장에만 3개 더 있는 상황
    for i in range(3):
        db.add(NaverAdgroupProduct(adgroup_id=f"gone{i}", campaign_id="cmp1",
                                   mall_product_id=f"px{i}"))
        db.add(NaverProductBep(channel_id=6, channel_product_id=f"px{i}",
                               has_cost=True, bep_roas=2.0))
    db.commit()
    _wisdom(db, wid=1)

    cov = ws.build(db)["value_definition"]["bep_coverage"]
    assert cov["groups_with_product_bep"] <= cov["groups_total"]
    assert cov["ratio"] <= 1.0


# ── ★적대 리뷰 BM9 회귀: 라우트 파라미터가 실제로 먹는가 ────────────────────
def test_route_wisdom_id_query_param_actually_filters(db):
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from app.database import get_db  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    _wisdom(db, wid=1)
    _wisdom(db, wid=2, text="두 번째 지혜")

    app.dependency_overrides[get_db] = lambda: db
    try:
        body = TestClient(app).get("/api/naver/ad/wisdom-scorecard?wisdom_id=2").json()
        assert body["wisdom_total"] == 1
        assert body["wisdom"][0]["wisdom_id"] == 2
    finally:
        app.dependency_overrides.pop(get_db, None)


# ── ★적대 리뷰 P2-3·P2-4·P2-5 회귀 ─────────────────────────────────────────
def test_retired_wisdom_is_not_counted_as_active(db):
    e = _wisdom(db, wid=1)
    e.status = "retired"
    db.commit()
    out = ws.build(db)
    assert out["wisdom_total"] == 1
    assert out["wisdom_active"] == 0


def test_dry_run_only_changes_report_distinct_gap(db):
    """모의 조치는 run_daily가 «영원히» 채점하지 않는다 — 「채점 대기」는 거짓이다."""
    _change(db, cid=70, proposal_id=700, outcome_profit=None, dry_run=True)
    _proposal(db, pid=700, change_log_id=70)
    _wisdom(db, wid=1, proposal_id=700)

    row = ws.build(db)["wisdom"][0]
    assert row["changes_total"] == 1
    assert row["changes_executed"] == 0
    assert "모의(dry_run)" in row["evidence_gap"]


def test_missing_proposal_row_is_not_disguised_as_zero_execution(db):
    """링크는 있는데 제안 행이 없는 것은 «데이터 정합 문제»지 운영 상태가 아니다."""
    _wisdom(db, wid=1, proposal_id=9999)
    row = ws.build(db)["wisdom"][0]
    assert "찾지 못했다" in row["evidence_gap"]


def test_maturity_state_does_not_claim_disabled_when_flag_is_gone(db, monkeypatch):
    """★BM5: 상수가 개명·삭제돼도 「보정 미적용」을 확신하면 거짓말이 된다."""
    from app.services.naver_ad import bid_ceiling_calculator  # noqa: PLC0415

    monkeypatch.delattr(bid_ceiling_calculator, "MATURITY_CORRECTION_ENABLED", raising=True)
    _wisdom(db, wid=1)
    delay = ws.build(db)["value_definition"]["conversion_delay"]
    assert delay["correction_applied"] is None
    assert "판정불능" in delay["note"]


# ── ★적대 리뷰 2R P1 회귀: 금액 합의 «집합»이 판정 집합과 같아야 한다 ────────
def test_profit_sum_excludes_rows_the_scorer_refused_to_judge(db):
    """★`run_daily`는 모수 미달(양쪽 창 clk<10) 행에 대해 **판정은 보류하면서 actual_json은
    무조건 쓴다**(proposal_scoreboard.py:290-294 + :339). 그 행을 금액 합에 섞으면
    「채점 1/4건 · 총이익 개선 1건 · 총이익 델타 -2,000,000원(4건)」이 한 줄에 뜬다 —
    판정과 크기가 서로 반대를 가리키는, P1-1이 고치려던 바로 그 증상이다.
    """
    judged = _actual_json(before={"clk": 50, "conv_amt": 400_000, "cost": 100_000},
                          after={"clk": 50, "conv_amt": 600_000, "cost": 100_000},
                          bep=2.0, cf=1.0)  # +100,000 개선
    thin = _actual_json(before={"clk": 2, "conv_amt": 2_000_000, "cost": 0},
                        after={"clk": 1, "conv_amt": 600_000, "cost": 0},
                        bep=2.0, cf=1.0)    # -700,000 (판정 보류 행)
    _change(db, cid=80, proposal_id=800, outcome_profit="improved", actual_json=judged)
    for i, cid in enumerate((81, 82, 83)):
        _change(db, cid=cid, proposal_id=800, outcome_profit=None, actual_json=thin)
    _proposal(db, pid=800, change_log_id=80)
    _wisdom(db, wid=1, proposal_id=800)

    row = ws.build(db)["wisdom"][0]
    assert row["changes_total"] == 4
    assert row["changes_scored_profit"] == 1
    # 합은 «판정된 1건»만 — 보류 3건이 부호를 뒤집으면 안 된다
    assert row["profit_pairs"] == 1
    assert row["profit_delta_sum"] == pytest.approx(100_000.0)
    # 보류 행은 버리지 않고 따로 센다(조용히 빠지면 분모가 어디로 갔는지 모른다)
    assert row["profit_unjudged"] == 3


def test_unjudged_rows_still_show_their_own_amount_in_details(db):
    """행 단위로는 금액을 보여 준다 — 합에서 뺀 것이지 «없는 것»으로 만든 게 아니다."""
    thin = _actual_json(before={"clk": 2, "conv_amt": 200_000, "cost": 0},
                        after={"clk": 1, "conv_amt": 100_000, "cost": 0}, bep=2.0, cf=1.0)
    _change(db, cid=84, proposal_id=801, outcome_profit=None, actual_json=thin)
    _proposal(db, pid=801, change_log_id=84)
    _wisdom(db, wid=1, proposal_id=801)

    row = ws.build(db)["wisdom"][0]
    assert row["profit_delta_sum"] is None
    assert row["profit_unjudged"] == 1
    assert row["details"][0]["profit_delta"] == pytest.approx(-50_000.0)


def test_negative_bep_is_refused_like_the_canonical_verdict_function(db):
    """정본 `_profit_verdict`가 bep<=0을 거부하므로 여기도 거부해야 한다 —
    두 자가 다르면 부호가 뒤집힌 금액이 그대로 화면에 나간다(적대 리뷰 2R P2)."""
    aj = _actual_json(before={"clk": 50, "conv_amt": 1000, "cost": 100},
                      after={"clk": 50, "conv_amt": 2000, "cost": 100}, bep=-2.0, cf=1.0)
    _change(db, cid=85, proposal_id=802, outcome_profit="improved", actual_json=aj)
    _proposal(db, pid=802, change_log_id=85)
    _wisdom(db, wid=1, proposal_id=802)

    row = ws.build(db)["wisdom"][0]
    assert row["profit_pairs"] == 0
    assert row["profit_unavailable"] == 1
    assert row["details"][0]["profit_delta"] is None
