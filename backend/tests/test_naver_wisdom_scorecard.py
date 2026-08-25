# test_naver_wisdom_scorecard.py — M3-a 지혜 성적표 조인 배선 단위테스트
# 계약 docs/PLAN_naver-m3-wisdom-scorecard.md §4-A① · §4-B⑥ · §8-Q5(델타 크기)
from __future__ import annotations

from datetime import datetime, timedelta

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
    OpsDiaryEntry,
    OpsWisdomCandidate,
    OpsWisdomEntry,
)
from app.services.naver_ad import wisdom_apply
from app.services.naver_ad import wisdom_candidates
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


# ── ★적대 리뷰 3R P2 회귀 ───────────────────────────────────────────────────
def test_profit_buckets_cover_every_row_exactly_once(db):
    """★3R P2-1: 「판정X·금액X」 행이 어느 버킷에도 안 들어가 조용히 사라졌다.
    세 버킷의 합이 changes_total과 같아야 «분모가 어디로 갔는지»를 화면이 설명할 수 있다."""
    ok = _actual_json(before={"clk": 50, "conv_amt": 400_000, "cost": 100_000},
                      after={"clk": 50, "conv_amt": 600_000, "cost": 100_000}, bep=2.0, cf=1.0)
    _change(db, cid=90, proposal_id=900, outcome_profit="improved", actual_json=ok)   # 판정O·금액O
    _change(db, cid=91, proposal_id=900, outcome_profit="declined", actual_json=None) # 판정O·금액X
    _change(db, cid=92, proposal_id=900, outcome_profit=None, actual_json=ok)         # 판정X·금액O
    _change(db, cid=93, proposal_id=900, outcome_profit=None, actual_json=None)       # 판정X·금액X
    _proposal(db, pid=900, change_log_id=90)
    _wisdom(db, wid=1, proposal_id=900)

    row = ws.build(db)["wisdom"][0]
    assert row["changes_total"] == 4
    assert row["profit_pairs"] + row["profit_unavailable"] + row["profit_unjudged"] == 4
    assert row["profit_pairs"] == 1
    assert row["profit_unavailable"] == 2
    assert row["profit_unjudged"] == 1


# ── ★D-NAO-248 §4-A2 회귀: 후보 현황 블록 ────────────────────────────────────
def _candidate(db, *, cid, signature, grain=None, campaign_type=None, experiment_batch=None,
               campaign_id="", by_campaign=None, status="pending", occurrences=1,
               good_count=1, bad_count=0):
    import json as _json
    c = OpsWisdomCandidate(
        id=cid, signature=signature, campaign_id=campaign_id, action="bid_up",
        env_bucket_json="{}", observation="관찰 요약", occurrences=occurrences,
        good_count=good_count, bad_count=bad_count, status=status,
        importance=5, strength=7.0, grain=grain, campaign_type=campaign_type,
        experiment_batch=experiment_batch,
        by_campaign_json=_json.dumps(by_campaign, ensure_ascii=False) if by_campaign else None,
    )
    db.add(c)
    db.commit()
    return c


def test_candidate_status_distinguishes_legacy_from_global(db):
    """레거시(grain=NULL, D-NAO-248 이전) 27행류와 신형 전역 후보가 화면에서 갈라져야 한다."""
    _candidate(db, cid=1, signature="cmp1|bid_up|weekday|summer|normal", grain=None,
               campaign_id="cmp1")
    _candidate(db, cid=2, signature="g|WEB_SITE|bid_up|weekday|summer|normal|",
               grain="global", campaign_type="WEB_SITE",
               by_campaign={"cmp1": {"good": 3, "bad": 1}, "cmp2": {"good": 2, "bad": 0}})

    out = ws.build(db)["candidate_status"]
    assert out["candidates_total"] == 2
    by_id = {r["candidate_id"]: r for r in out["candidates"]}
    assert by_id[1]["grain"] is None
    assert by_id[1]["bucket"] == "legacy"
    assert by_id[1]["campaign_count"] == 1  # 레거시는 항상 캠페인 1개(파생값, by_campaign 없음)
    assert by_id[2]["grain"] == "global"
    assert by_id[2]["bucket"] == "global_pool"
    # 캠페인별 분해가 그대로 실려야 한다(부록 Q2: 합치되 이질성은 판사에게 보인다)
    assert by_id[2]["campaign_count"] == 2
    assert by_id[2]["by_campaign"] == {"cmp1": {"good": 3, "bad": 1}, "cmp2": {"good": 2, "bad": 0}}
    assert out["bucket_counts"]["legacy"] == 1
    assert out["bucket_counts"]["global_pool"] == 1


def test_candidate_status_separates_experiment_and_unknown_buckets(db):
    """경계 분리 건수(부록 Q3) — 실험배치 분리와 fail-closed 미상분리가 전역 풀과 각각 다른
    버킷 카운터에 잡혀야 한다."""
    _candidate(db, cid=10, signature="g|SHOPPING|bid_up|weekday|summer|normal|",
               grain="global", campaign_type="SHOPPING", experiment_batch=None,
               by_campaign={"cmp1": {"good": 1, "bad": 0}})
    _candidate(db, cid=11, signature="g|SHOPPING|bid_up|weekday|summer|normal|MOP열",
               grain="global", campaign_type="SHOPPING", experiment_batch="MOP열",
               by_campaign={"cmp2": {"good": 1, "bad": 0}})
    _candidate(db, cid=12, signature="g?|cmp3|bid_up|weekday|summer|normal",
               grain="global", campaign_id="cmp3",
               by_campaign={"cmp3": {"good": 1, "bad": 0}})

    out = ws.build(db)["candidate_status"]
    assert out["bucket_counts"] == {
        "legacy": 0, "global_pool": 1, "separated_experiment": 1, "separated_unknown": 1,
    }
    by_id = {r["candidate_id"]: r for r in out["candidates"]}
    assert by_id[11]["bucket"] == "separated_experiment"
    assert by_id[12]["bucket"] == "separated_unknown"
    # 「기존 재료 재집계」 라벨이 wisdom_candidates의 상수와 문자 그대로 같아야 한다
    assert out["retro_harvest_label"] == wisdom_candidates.RETRO_HARVEST_LABEL
    assert "재집계" in out["retro_harvest_label"]


def test_candidate_status_is_unfiltered_by_wisdom_id(db):
    """후보는 특정 지혜 1건에 속하지 않는다(승격 전) — wisdom_id로 걸러도 전체가 나와야 한다."""
    _wisdom(db, wid=1)
    _wisdom(db, wid=2, text="다른 지혜")
    _candidate(db, cid=1, signature="cmp1|bid_up|weekday|summer|normal", grain=None,
               campaign_id="cmp1")

    out = ws.build(db, wisdom_id=2)
    assert out["wisdom_total"] == 1  # wisdom_id 필터는 여전히 먹는다
    assert out["candidate_status"]["candidates_total"] == 1  # 후보는 안 걸린다


def test_candidate_status_present_even_with_zero_candidates(db):
    """후보 0건이어도 블록 자체는 항상 나와야 한다(침묵 방지)."""
    out = ws.build(db)["candidate_status"]
    assert out["candidates_total"] == 0
    assert out["candidates"] == []
    assert out["retro_harvest_label"]


# ── ★D-NAO-248 §4-A7 회귀: 지혜별 소비 현황(결정 메타·브리핑 주입) ──────────────
def test_proposal_decision_meta_is_recorded_when_present(db):
    _proposal(db, pid=100, status="approved", change_log_id=None)
    p = db.get(NaverProposal, 100)
    p.decided_at = datetime(2026, 8, 25, 9, 0)
    p.decided_by = "console:jino"
    p.decision_note = "승인 — 근거 충분"
    db.commit()
    _wisdom(db, wid=1, proposal_id=100)

    row = ws.build(db)["wisdom"][0]
    lp = row["linked_proposals"][0]
    assert lp["decided_at"] == "2026-08-25 09:00:00"
    assert lp["decided_by"] == "console:jino"
    assert lp["decision_note"] == "승인 — 근거 충분"


def test_legacy_decision_without_new_columns_shows_record_missing_not_undecided(db):
    """★기존 제안(예: 2314, 07-26 rejected)은 decided_at 컬럼 신설 «전»에 결정났다.
    NULL을 «아직 결정 안 됨»으로 읽으면 거짓말이 된다(그 제안은 이미 rejected다) — 코드에
    id를 박지 않는 일반 규칙(decided_at IS NULL → 「기록 없음」)으로 이 케이스가 저절로
    맞게 뜨는지를 검사한다."""
    _proposal(db, pid=2314, status="rejected", change_log_id=None)
    _wisdom(db, wid=1, proposal_id=2314)

    row = ws.build(db)["wisdom"][0]
    lp = row["linked_proposals"][0]
    assert lp["proposal_id"] == 2314
    assert lp["status"] == "rejected"
    assert lp["decided_at"] is None
    assert lp["decided_by"] is None
    assert lp["decision_note"] == "기록 없음(컬럼 신설 전)"


def test_briefing_injected_flag_reflects_active_wisdom_prefix_query(db):
    """브리핑 주입 여부는 wisdom_apply.active_wisdom_prefix()와 같은 질의(active·promoted_at
    desc·limit 10)를 재현한 것이어야 한다 — retired는 주입되지 않는다."""
    _wisdom(db, wid=1, text="주입되는 지혜")
    e2 = _wisdom(db, wid=2, text="철회된 지혜")
    e2.status = "retired"
    db.commit()

    out = ws.build(db)["wisdom"]
    by_id = {r["wisdom_id"]: r for r in out}
    assert by_id[1]["briefing_injected"] is True
    assert by_id[2]["briefing_injected"] is False
    assert "성과" in by_id[1]["briefing_injection_note"] or "결과" in by_id[1]["briefing_injection_note"]


def test_briefing_injected_flag_respects_limit(db):
    """limit(10) 밖의 오래된 지혜는 주입되지 않는다고 표시돼야 한다."""
    for i in range(1, 13):
        _wisdom(db, wid=i, text=f"지혜{i}")
        e = db.get(OpsWisdomEntry, i)
        e.promoted_at = datetime(2026, 8, i, 8, 45)
        db.commit()

    out = {r["wisdom_id"]: r for r in ws.build(db)["wisdom"]}
    # promoted_at 내림차순 상위 10건(3~12)만 주입, 1~2는 밖
    injected = {wid for wid, r in out.items() if r["briefing_injected"]}
    assert injected == set(range(3, 13))


def test_unjudged_count_reaches_the_gap_text_when_no_verdict_row_exists(db):
    """★3R P2-2: 판정 행이 0이면 배지 줄이 안 뜬다 — 그때 보류 «건수»가 사라지면 안 된다."""
    thin = _actual_json(before={"clk": 2, "conv_amt": 200_000, "cost": 0},
                        after={"clk": 1, "conv_amt": 100_000, "cost": 0}, bep=2.0, cf=1.0)
    _change(db, cid=94, proposal_id=901, outcome_profit=None, actual_json=thin)
    _change(db, cid=95, proposal_id=901, outcome_profit=None, actual_json=thin)
    _proposal(db, pid=901, change_log_id=94)
    _wisdom(db, wid=1, proposal_id=901)

    row = ws.build(db)["wisdom"][0]
    assert row["has_evidence"] is False
    assert "2건은 표본 미달로 판정 보류" in row["evidence_gap"]


# ── ★D-NAO-248 §4-B(B4) — 「지혜id → 제안id → 결정 메타 → change_log before/after」 한 줄 ──
def test_linked_proposal_shows_applied_change_before_after(db):
    """B1이 채운 executed_change_log_id를 타고 change_log의 before/after가 linked_proposals에
    그대로 보인다 — 조인 하나가 없다던 북극성 ref 82 §5-3①의 진단을 이 자리에서 닫는다."""
    c = NaverChangeLog(
        id=200, changed_at=datetime(2026, 8, 25, 9, 0), entity_type="account",
        entity_id="", campaign_id="", action="update_guardrail_params", dry_run=False,
        proposal_id=100,
        before_value='{"cooldown_hours": "2"}', after_value='{"cooldown_hours": "5"}',
    )
    db.add(c)
    db.commit()
    _proposal(db, pid=100, status="approved", change_log_id=200)
    _wisdom(db, wid=1, proposal_id=100)

    row = ws.build(db)["wisdom"][0]
    lp = row["linked_proposals"][0]
    applied = lp["applied_change"]
    assert applied is not None
    assert applied["change_log_id"] == 200
    assert applied["before_value"] == '{"cooldown_hours": "2"}'
    assert applied["after_value"] == '{"cooldown_hours": "5"}'
    assert applied["action"] == "update_guardrail_params"


def test_linked_proposal_applied_change_is_none_when_not_executed(db):
    """아직 반영되지 않은(pending/rejected) 제안은 applied_change가 None — 「반영 증거 없음」을
    정직하게 나타낸다(값을 지어내지 않는다)."""
    _proposal(db, pid=101, status="pending", change_log_id=None)
    _wisdom(db, wid=1, proposal_id=101)

    row = ws.build(db)["wisdom"][0]
    assert row["linked_proposals"][0]["applied_change"] is None


# ── ★D-NAO-248 §4-B(B7-6) — param_gate 카운터, 0이어도 침묵하지 않는다 ──
def _candidate_with_suggestion(db, *, cid, suggestion, signature):
    import json as _json
    c = OpsWisdomCandidate(
        id=cid, signature=signature, campaign_id="cmp1", action="bid_up",
        env_bucket_json="{}", observation="관찰 요약", occurrences=1,
        good_count=1, bad_count=0, status="promoted", importance=5, strength=7.0,
        judge_verdict_json=_json.dumps(
            {"verdict": "promote", "principle": "p", "rationale": "r",
             "param_suggestion": suggestion}, ensure_ascii=False),
    )
    db.add(c)
    db.commit()
    return c


def test_param_gate_present_and_zero_when_no_wisdom(db):
    """지혜가 0건이어도 param_gate 블록 자체는 항상 나온다(교훈 #318, 침묵 방지)."""
    out = ws.build(db)["candidate_status"]["param_gate"]
    assert out == {
        "unconditional_mapped": 0, "conditional_fallback": 0,
        "unmapped_param": 0, "no_suggestion": 0,
    }


def test_param_gate_counts_reflect_stored_wisdom(db):
    """B7 코드 클램프 판정이 wisdom_scorecard의 candidate_status에도 그대로 보인다 —
    wisdom_apply.gate_summary()를 read-time으로 재현한 것과 같은 값이어야 한다."""
    # _wisdom(wid=N)의 source_candidate_id 기본값이 N이므로 _candidate_with_suggestion을
    # 같은 id(cid=N)로 먼저 심으면 별도 연결 없이 조인된다(기존 헬퍼 관례 그대로).
    _candidate_with_suggestion(
        db, cid=1, signature="s1",
        suggestion={"param": "cooldown_hours", "scope": "unconditional", "direction": "up"})
    _wisdom(db, wid=1, proposal_id=None, text="지혜1")
    _candidate_with_suggestion(
        db, cid=2, signature="s2",
        suggestion={"param": "cooldown_hours", "direction": "up"})  # scope 없음 → conditional
    _wisdom(db, wid=2, proposal_id=None, text="지혜2")

    out = ws.build(db)["candidate_status"]["param_gate"]
    assert out["unconditional_mapped"] == 1
    assert out["conditional_fallback"] == 1
    assert out == wisdom_apply.gate_summary(db)


# ── ★계약 §C2 「재료 전건 왕복」 — search_term_material ─────────────────────────
def _now_utc_naive():
    """wisdom_candidates._HARVEST_LOOKBACK_DAYS 창 계산과 같은 방식(kst_now() - 9h)으로
    「지금」의 naive UTC 값을 낸다 — 테스트가 실제 실행 날짜와 무관하게 안/밖 경계를 잡게 한다."""
    from app.utils.kst import kst_now as _kst_now  # noqa: PLC0415
    return _kst_now() - timedelta(hours=9)


_IN_WINDOW = _now_utc_naive() - timedelta(days=1)  # 창(90일) 안 — 확실히 최근
_OUT_OF_WINDOW = _now_utc_naive() - timedelta(days=200)  # 창(90일) 밖 — 여유 있게 지난 값


def _diary(db, *, did, target_type="search_term", target_id="검색어", created_at=None,
           outcome=None, event_type="execute", actor="system"):
    import json as _json
    e = OpsDiaryEntry(
        id=did, created_at=created_at if created_at is not None else _IN_WINDOW,
        event_type=event_type, campaign_id="cmp1", target_type=target_type, target_id=target_id,
        outcome_json=_json.dumps(outcome, ensure_ascii=False) if outcome is not None else None,
        actor=actor,
    )
    db.add(e)
    db.commit()
    return e


def test_search_term_material_counts_status_distribution(db):
    """계약 §C2 재료 전건 왕복 — stopped/leaking/ambiguous/no_data/absent 각 1건씩 심으면
    by_status가 그 분포를 그대로 반영해야 한다(창 안, prod 실측 예상값과 같은 모양 —
    total=일기수).

    ★2026-08-25 의미 정정: did=5(outcome_json 자체가 없음, event_type 기본값 "execute")는
    예전엔 "absent"로 셌다. 그런데 harvest_candidates()의 SQL 필터는 outcome_json IS NOT
    NULL을 요구하므로 이 행은 harvest가 **원리적으로 절대 보지 않는다** — "absent"(= harvest는
    보는데 d1_st 키만 없는 행)라고 부르면 「채워지면 처리될 행」처럼 부정직하게 읽힌다.
    그래서 이제 event_type이 HARVEST_EVENT_TYPES 밖이거나 outcome_json이 비어 있는 행은
    별도 "not_harvestable" 버킷으로 간다. "absent"는 별도 테스트
    (test_search_term_material_absent_means_seen_by_harvest_but_missing_d1_st)로 검증한다."""
    _diary(db, did=1, outcome={"d1_st": {"status": "stopped"}})
    _diary(db, did=2, outcome={"d1_st": {"status": "leaking"}})
    _diary(db, did=3, outcome={"d1_st": {"status": "ambiguous"}})
    _diary(db, did=4, outcome={"d1_st": {"status": "no_data"}})
    _diary(db, did=5, outcome=None)  # outcome_json 자체가 없음 → not_harvestable(의미 정정)
    # target_type이 search_term이 아니면 재료가 아니다 — 섞여도 새지 않는지 같이 검사.
    _diary(db, did=6, target_type="keyword", outcome={"d1_st": {"status": "stopped"}})

    out = ws.build(db)["candidate_status"]["search_term_material"]
    assert out["total"] == 5
    assert out["by_status"] == {
        "stopped": 1, "leaking": 1, "ambiguous": 1, "no_data": 1, "absent": 0, "unknown": 0,
        "not_harvestable": 1,
    }


def test_search_term_material_absent_means_seen_by_harvest_but_missing_d1_st(db):
    """absent(좁혀진 의미) — event_type이 HARVEST_EVENT_TYPES 안(execute/blocked)이고
    outcome_json도 있지만(harvest가 «보는» 행), 그 안에 d1_st 키 자체가 없는 경우."""
    _diary(db, did=1, event_type="execute", outcome={})  # outcome_json="{}" — d1_st 없음

    out = ws.build(db)["candidate_status"]["search_term_material"]
    assert out["total"] == 1
    assert out["by_status"]["absent"] == 1
    assert out["by_status"]["not_harvestable"] == 0


def test_search_term_material_voided_event_is_not_harvestable_not_absent(db):
    """★C2 정직화 핵심 회귀 — prod 실측(2026-08-25): search_term 일기 3건 = execute 2건
    (outcome 보유) + voided 1건(outcome 없음). voided 행은 event_type이 HARVEST_EVENT_TYPES
    밖이라 d1_st가 나중에 채워지든 말든 harvest가 원리적으로 절대 안 본다 — not_harvestable로
    가야 하고, "채워지면 처리될 행"처럼 읽히는 absent로 가면 안 된다."""
    _diary(db, did=1, event_type="voided", outcome=None)
    # event_type은 HARVEST_EVENT_TYPES 안(execute)인데 outcome_json이 없는 경우도 같은 버킷.
    _diary(db, did=2, event_type="execute", outcome=None)

    out = ws.build(db)["candidate_status"]["search_term_material"]
    assert out["total"] == 2
    assert out["by_status"]["not_harvestable"] == 2
    assert out["by_status"]["absent"] == 0


def test_search_term_material_unknown_status_is_fail_closed(db):
    """status가 알려진 4값 밖이면 good/bad로 잘못 세지 않고 unknown으로 fail-closed 한다
    (harvest_candidates._D1_ST_UNKNOWN_STATUS_COUNTER와 같은 원칙)."""
    _diary(db, did=1, outcome={"d1_st": {"status": "weird"}})

    out = ws.build(db)["candidate_status"]["search_term_material"]
    assert out["total"] == 1
    assert out["by_status"]["unknown"] == 1
    assert sum(out["by_status"].values()) == 1


def test_search_term_material_present_even_with_zero_rows(db):
    """0건이어도 7개 status 키(+not_harvestable) + total + label이 침묵하지 않고 전부
    나온다(교훈 #318)."""
    out = ws.build(db)["candidate_status"]["search_term_material"]
    assert out["total"] == 0
    assert out["by_status"] == {
        "stopped": 0, "leaking": 0, "ambiguous": 0, "no_data": 0, "absent": 0, "unknown": 0,
        "not_harvestable": 0,
    }
    assert "재료 0건" in out["label"]
    assert "지혜가 났다" in out["label"]  # 「검색어 지혜가 났다」 주장 금지 취지가 라벨에 있어야 한다


def test_search_term_material_excludes_rows_outside_harvest_window(db):
    """harvest_candidates와 같은 lookback 창 밖 일기는 재료로 안 세어진다."""
    _diary(db, did=1, created_at=_OUT_OF_WINDOW, outcome={"d1_st": {"status": "stopped"}})
    _diary(db, did=2, created_at=_IN_WINDOW, outcome={"d1_st": {"status": "stopped"}})

    out = ws.build(db)["candidate_status"]["search_term_material"]
    assert out["total"] == 1
    assert out["by_status"]["stopped"] == 1


def test_search_term_material_does_not_disturb_other_candidate_status_keys(db):
    """search_term_material 추가가 candidate_status의 기존 키를 바꾸면 안 된다(회귀)."""
    _candidate(db, cid=1, signature="cmp1|bid_up|weekday|summer|normal", grain=None,
               campaign_id="cmp1")
    _diary(db, did=1, outcome={"d1_st": {"status": "stopped"}})

    out = ws.build(db)["candidate_status"]
    assert out["candidates_total"] == 1
    assert out["bucket_counts"] == {
        "legacy": 1, "global_pool": 0, "separated_experiment": 0, "separated_unknown": 0,
    }
    assert out["retro_harvest_label"] == wisdom_candidates.RETRO_HARVEST_LABEL
    assert out["param_gate"] == {
        "unconditional_mapped": 0, "conditional_fallback": 0,
        "unmapped_param": 0, "no_suggestion": 0,
    }
    assert out["search_term_material"]["total"] == 1


# ── ★B5 대칭·탐색 관측 (D-NAO-247 점화 계약 「B5. 대칭·탐색 관측」) ──────────────────
def _guardrail_change(db, *, cid, before, after, changed_at):
    """guardrail_params.apply_params가 실제로 남기는 change_log 모양(action=
    "update_guardrail_params", before_value/after_value=JSON{key: "문자열값"})을 흉내낸다."""
    import json as _json
    row = NaverChangeLog(
        id=cid, changed_at=changed_at, entity_type="account", entity_id="", campaign_id="",
        action="update_guardrail_params",
        before_value=_json.dumps(before, ensure_ascii=False),
        after_value=_json.dumps(after, ensure_ascii=False),
        dry_run=False,
    )
    db.add(row)
    db.commit()
    return row


# ★적대 리뷰 생존 변이 D의 상환(2026-08-25) — 아래 4개는 «단방향 1건»만 넣는다.
#   구판은 한 테스트에 up 1건 + down 1건을 같이 넣고 합계 {brake:1, accel:1}만 확인했는데,
#   그러면 **brake/accel 라벨을 통째로 뒤집어도 합계가 그대로라 테스트가 통과한다**(리뷰어가
#   변이로 실증: 단건 up 변화 시 정상은 {brake:1,accel:0}인데 변이는 {brake:0,accel:1}을 냈고
#   기존 테스트 59건은 전부 초록이었다). 이 표면은 화면의 「⚠ 브레이크만 조여지고 액셀은 0건
#   — 표류 경보」로 직결되므로(D-NAO-85 재발 감시), 라벨이 뒤집히면 **경보 판정이 반대로 뜬다.**
#   교훈 #181의 그 자리 — 「통과하는데 아무것도 안 지키는 테스트」다.

def test_symmetry_tighten_up_increase_is_brake_only(db):
    """B5① — cooldown_hours(tighten_up: 커질수록 조임)가 «늘기만» 하면 brake 1·accel 0."""
    assert ws.guardrail_params.SPECS["cooldown_hours"].direction == "tighten_up"
    _guardrail_change(db, cid=1, before={"cooldown_hours": "2"}, after={"cooldown_hours": "4"},
                       changed_at=datetime(2026, 8, 1, 9, 0))

    gd = ws.build(db)["symmetry_report"]["guardrail_direction"]
    assert gd["brake"] == 1, "조이는 방향인데 accel로 셌다 — 표류 경보가 반대로 뜬다"
    assert gd["accel"] == 0
    assert gd["by_key"]["cooldown_hours"] == {"brake": 1, "accel": 0}
    assert gd["total_changes"] == 1


def test_symmetry_tighten_up_decrease_is_accel_only(db):
    """B5① — cooldown_hours가 «줄기만» 하면 accel 1·brake 0(푸는 방향)."""
    _guardrail_change(db, cid=2, before={"cooldown_hours": "5"}, after={"cooldown_hours": "2"},
                       changed_at=datetime(2026, 8, 2, 9, 0))

    gd = ws.build(db)["symmetry_report"]["guardrail_direction"]
    assert gd["accel"] == 1, "푸는 방향인데 brake로 셌다"
    assert gd["brake"] == 0
    assert gd["by_key"]["cooldown_hours"] == {"brake": 0, "accel": 1}


def test_symmetry_tighten_down_decrease_is_brake_only(db):
    """B5① — max_auto_up_multiple(tighten_down: 작아질수록 조임)이 «줄기만» 하면 brake 1·accel 0."""
    assert ws.guardrail_params.SPECS["max_auto_up_multiple"].direction == "tighten_down"
    _guardrail_change(db, cid=1, before={"max_auto_up_multiple": "2.0"},
                       after={"max_auto_up_multiple": "1.5"}, changed_at=datetime(2026, 8, 1, 9, 0))

    gd = ws.build(db)["symmetry_report"]["guardrail_direction"]
    assert gd["brake"] == 1, "tighten_down은 «감소»가 조이는 방향이다 — 방향을 거꾸로 읽었다"
    assert gd["accel"] == 0
    assert gd["by_key"]["max_auto_up_multiple"] == {"brake": 1, "accel": 0}


def test_symmetry_tighten_down_increase_is_accel_only(db):
    """B5① — max_auto_up_multiple이 «늘기만» 하면 accel 1·brake 0(상한을 넓히는 방향)."""
    _guardrail_change(db, cid=2, before={"max_auto_up_multiple": "1.5"},
                       after={"max_auto_up_multiple": "2.5"}, changed_at=datetime(2026, 8, 2, 9, 0))

    gd = ws.build(db)["symmetry_report"]["guardrail_direction"]
    assert gd["accel"] == 1, "tighten_down은 «증가»가 푸는 방향이다"
    assert gd["brake"] == 0
    assert gd["by_key"]["max_auto_up_multiple"] == {"brake": 0, "accel": 1}


def test_symmetry_guardrail_direction_all_keys_present_when_zero_changes(db):
    """change_log 0건이어도 SPECS 키 전부가 by_key에 0으로 실린다(교훈 #318 — 침묵 방지)."""
    gd = ws.build(db)["symmetry_report"]["guardrail_direction"]
    assert gd["brake"] == 0
    assert gd["accel"] == 0
    assert gd["unchanged_or_unknown"] == 0
    assert gd["total_changes"] == 0
    assert set(gd["by_key"]) == set(ws.guardrail_params.SPECS)
    for k in ws.guardrail_params.SPECS:
        assert gd["by_key"][k] == {"brake": 0, "accel": 0}


def test_symmetry_exploration_blocked_rate_computed_correctly(db):
    """B5② — actor=explore 중 event_type=blocked 비율이 맞게 계산된다.
    파라미터 변경 이력이 없으므로 whole_window에서 관측한다."""
    _diary(db, did=1, actor="explore", event_type="blocked", target_type="adgroup", outcome=None)
    _diary(db, did=2, actor="explore", event_type="blocked", target_type="adgroup", outcome=None)
    _diary(db, did=3, actor="explore", event_type="execute", target_type="adgroup", outcome=None)
    _diary(db, did=4, actor="daily", event_type="execute", target_type="adgroup", outcome=None)

    ex = ws.build(db)["symmetry_report"]["exploration"]
    assert ex["boundary_changed_at"] is None
    assert ex["before"] is None and ex["after"] is None
    ww = ex["whole_window"]
    assert ww["total"] == 4
    assert ww["by_actor"] == {"explore": 3, "daily": 1}
    assert ww["explore_total"] == 3
    assert ww["explore_blocked"] == 2
    assert ww["explore_blocked_rate"] == pytest.approx(round(2 / 3, 4))


def test_symmetry_exploration_no_param_change_reports_after_honestly(db):
    """B5② ★파라미터 변경이 0건일 때 «후» 구간이 정직하게 표시된다 — null을 지어내지 않고
    boundary·before·after가 전부 None이며, note가 그 사실을 설명한다."""
    ex = ws.build(db)["symmetry_report"]["exploration"]
    assert ex["boundary_changed_at"] is None
    assert ex["before"] is None
    assert ex["after"] is None
    assert "경계가 없다" in ex["note"]
    assert ex["window_days"] == ws._SYMMETRY_WINDOW_DAYS
    # whole_window는 창이 낭비되지 않도록 대신 낸다(0건이어도 구조는 채워진다).
    assert ex["whole_window"] == {
        "total": 0, "by_actor": {}, "explore_share": None,
        "explore_total": 0, "explore_blocked": 0, "explore_blocked_rate": None,
    }


def test_symmetry_exploration_before_after_split_by_latest_guardrail_change(db):
    """B5② — 가장 최근 update_guardrail_params 시각을 경계로 창 안 일기가 전/후로 갈린다."""
    boundary = _now_utc_naive() - timedelta(days=5)
    _guardrail_change(db, cid=1, before={"cooldown_hours": "2"}, after={"cooldown_hours": "4"},
                       changed_at=boundary)
    _diary(db, did=1, actor="explore", event_type="blocked",
           created_at=boundary - timedelta(days=1), outcome=None)  # 변경 전
    _diary(db, did=2, actor="explore", event_type="execute",
           created_at=boundary + timedelta(days=1), outcome=None)  # 변경 후
    _diary(db, did=3, actor="daily", event_type="execute",
           created_at=boundary + timedelta(days=2), outcome=None)  # 변경 후

    ex = ws.build(db)["symmetry_report"]["exploration"]
    assert ex["boundary_changed_at"] is not None
    assert ex["whole_window"] is None
    assert ex["before"]["total"] == 1
    assert ex["before"]["explore_blocked_rate"] == pytest.approx(1.0)
    assert ex["after"]["total"] == 2
    assert ex["after"]["by_actor"] == {"explore": 1, "daily": 1}
