# test_naver_pooled_estimate.py — M2-a [9] 계층 EB 풀링 배선 (D-NAO-214 · ref 65 S1-ⓑ/ⓓ)
#
# 무엇을 지키는가:
#   ①**합격기준 ②가 원리적으로 관측 가능해야 한다** — 저장된 (n, raw, prior, K)로 수기 공식
#     `(n·raw+K·prior)/(n+K)`을 계산하면 pooled_* 와 일치한다. 이 넷 중 하나만 빠져도 합격기준이
#     «검산 불가»가 되므로 컬럼 존재가 아니라 **재계산 일치**로 잡는다.
#   ②**3지점 교체가 값을 안 바꿨다** — pool_metric(...,"rpc") == bid_simulator.pooled_rpc(...).
#     「회귀 0」(합격 ⑥)의 본체가 이것이다. 상수가 갈라지면(K 불일치) 여기서 죽는다.
#   ③**ctr·cvr이 조용히 0이 되지 않는다** — 분모(imp·conv_cnt)를 집계에 안 넣으면 pool_all은
#     예외 없이 0을 돌려주고, 0은 「신호 없음」과 화면상 구분되지 않는다.
#   ④같은 회차 중복 keyword_id가 **두 번째 값을 잃지 않는다**(query-then-add 이중 INSERT의
#     사촌 — 이 저장소에서 5회 재발한 모양, 교훈 #292).
#   ⑤미완주는 **complete=False로 표면화**되고 잡이 raise 한다 — 부분 산출이 `last_status='ok'`로
#     굳는 것이 교훈 #319·#321·D-NAO-212 1R P1의 같은 모양이다.
#   ⑥keyword_id='' sentinel(쇼핑·브랜드검색 그룹 행)은 키워드가 아니다 — 섞으면 scope_key가
#     빈 문자열인 행들이 서로를 덮는다.
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverCampaignSettings, NaverPooledEstimateDaily
from app.services.naver_ad import bid_simulator, hierarchical_pooling, proposal_pipeline
from app.services.naver_ad import pooled_estimate_writer as writer


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    # ★prod와 같은 autoflush=False (교훈 #292 — 관대한 픽스처는 결함을 원리적으로 못 잡는다)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _row(d: date, *, kw: str, camp="cmp-1", grp="grp-1", imp=0, clk=0,
         cnt=0, amt=0, ctype="WEB_SITE") -> NaverAdDaily:
    return NaverAdDaily(
        ad_date=d, campaign_id=camp, campaign_type=ctype, adgroup_id=grp, keyword_id=kw,
        imp=imp, clk=clk, cost=0, rank_sum=0,
        conv_direct_cnt=cnt, conv_indirect_cnt=0,
        conv_direct_amt=amt, conv_indirect_amt=0,
    )


def _yesterday_window(as_of: date) -> date:
    return as_of - timedelta(days=1)


# ─────────────────────────────────────────────────────────────────────────
# ② 3지점 교체 동치 — 「회귀 0」의 본체
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kw,grp,camp,acct", [
    ({"clk": 0, "conv_amt": 0}, {"clk": 0, "conv_amt": 0}, {"clk": 0, "conv_amt": 0}, {"clk": 0, "conv_amt": 0}),
    ({"clk": 3, "conv_amt": 30000}, {"clk": 50, "conv_amt": 400000}, {"clk": 500, "conv_amt": 3000000}, {"clk": 9000, "conv_amt": 50000000}),
    ({"clk": 1000, "conv_amt": 1}, {"clk": 2, "conv_amt": 999999}, {"clk": 0, "conv_amt": 0}, {"clk": 7, "conv_amt": 70000}),
    ({"clk": 10, "conv_amt": 0}, {"clk": 10, "conv_amt": 0}, {"clk": 10, "conv_amt": 0}, {"clk": 10, "conv_amt": 100}),
])
def test_pool_metric_rpc_equals_legacy_pooled_rpc(kw, grp, camp, acct):
    """교체한 3지점의 값이 구버전과 **같아야** 한다 — 다르면 그건 배선이 아니라 동작 변경이다."""
    legacy = bid_simulator.pooled_rpc(kw, grp, camp, acct)
    new = hierarchical_pooling.pool_metric(kw, grp, camp, acct, "rpc")
    assert new == legacy


def test_shrink_constants_are_the_same_number():
    """동치의 근거가 되는 상수 자체를 못박는다 — 한쪽만 바뀌면 위 동치가 조용히 깨진다."""
    assert Decimal(bid_simulator._SHRINK_K) == hierarchical_pooling.SHRINK_K == Decimal("10")


# ─────────────────────────────────────────────────────────────────────────
# ③ 집계 확장이 additive이고, 분모가 실제로 채워진다
# ─────────────────────────────────────────────────────────────────────────
def test_precompute_aggregates_adds_denominators_without_changing_old_keys(db):
    d = date(2026, 8, 1)
    db.add_all([
        _row(d, kw="nkw-1", imp=1000, clk=100, cnt=5, amt=500000),
        _row(d, kw="nkw-2", imp=500, clk=20, cnt=1, amt=30000),
    ])
    db.commit()
    agg = proposal_pipeline._precompute_aggregates(db, d, d)
    acct = agg["account"]
    # 기존 소비자가 읽던 키는 그대로 (회귀 0)
    assert acct["clk"] == 120
    assert acct["conv_amt"] == 530000
    # 새 분모가 실제로 채워진다 (조용한 0 방지)
    assert acct["imp"] == 1500
    assert acct["conv_cnt"] == 6
    assert agg["group"]["grp-1"]["imp"] == 1500
    assert agg["campaign"]["cmp-1"]["conv_cnt"] == 6


def test_pool_all_ctr_cvr_are_not_silently_zero_when_denominators_exist():
    """분모가 있으면 ctr·cvr이 0이 아니어야 한다 — 이 테스트가 죽으면 배선이 반쪽이다."""
    kw = {"imp": 200, "clk": 20, "conv_cnt": 2, "conv_amt": 60000}
    grp = {"imp": 2000, "clk": 150, "conv_cnt": 10, "conv_amt": 300000}
    camp = {"imp": 20000, "clk": 1200, "conv_cnt": 60, "conv_amt": 2400000}
    acct = {"imp": 90000, "clk": 5000, "conv_cnt": 200, "conv_amt": 9000000}
    out = hierarchical_pooling.pool_all(kw, grp, camp, acct)
    assert out["ctr"] > 0
    assert out["cvr"] > 0
    assert out["rpc"] > 0


# ─────────────────────────────────────────────────────────────────────────
# ① 합격기준 ②의 수기 검산 — 저장된 원료로 공식을 재현할 수 있는가
# ─────────────────────────────────────────────────────────────────────────
def test_stored_columns_reproduce_the_shrink_formula(db):
    as_of = date(2026, 8, 20)
    wt = _yesterday_window(as_of)
    db.add_all([
        _row(wt, kw="nkw-A", imp=400, clk=40, cnt=4, amt=200000),
        _row(wt, kw="nkw-B", grp="grp-2", imp=8000, clk=300, cnt=20, amt=1500000),
    ])
    db.commit()

    result = writer.write_pooled_estimates(db, as_of=as_of)
    assert result["complete"] is True
    assert result["written"] == 2

    row = db.query(NaverPooledEstimateDaily).filter_by(scope_key="nkw-A").one()
    k = Decimal(row.shrink_k)
    # 지표마다 분모 n이 다르다 — CTR은 imp, CVR·RPC는 clk (METRICS 정의 그대로)
    for metric, n, raw, prior, pooled in (
        ("ctr", row.n_imp, row.raw_ctr, row.prior_ctr, row.pooled_ctr),
        ("cvr", row.n_clk, row.raw_cvr, row.prior_cvr, row.pooled_cvr),
        ("rpc", row.n_clk, row.raw_rpc, row.prior_rpc, row.pooled_rpc),
    ):
        manual = (Decimal(n) * Decimal(raw) + k * Decimal(prior)) / (Decimal(n) + k)
        # ★허용오차 = **양자화 반폭(5e-5)**이지 부동소수 오차가 아니다. pool_metric이 세 지표를
        #   모두 _Q4(소수 4자리)로 quantize 하기 때문이다 — RPC는 원 단위 금액이라 4자리로 충분하고
        #   `bid_simulator.pooled_rpc`도 같은 폭이라 동치가 성립하지만, **CTR·CVR은 [0,1] 비율이라
        #   4자리면 해상도가 0.01%p**다. 이 사실을 허용오차를 늘려 «숨기지» 않고 여기에 적어 둔다.
        #   (양자화 폭을 바꾸면 pooled_rpc 동치가 깨지므로 이 슬라이스에서 바꾸지 않는다 —
        #    저CTR 키워드의 유효숫자 손실은 M2-d 소비 시점의 이월 사안이다.)
        assert abs(manual - Decimal(pooled)) <= Decimal("0.00005"), metric


def test_stored_pooled_matches_pool_all_exactly(db):
    """저장값이 pool_all 산출 그 자체여야 한다(중간에 다른 계산이 끼면 검산이 거짓말이 된다)."""
    as_of = date(2026, 8, 20)
    wt = _yesterday_window(as_of)
    db.add_all([
        _row(wt, kw="nkw-A", imp=400, clk=40, cnt=4, amt=200000),
        _row(wt, kw="nkw-B", grp="grp-2", imp=8000, clk=300, cnt=20, amt=1500000),
    ])
    db.commit()
    writer.write_pooled_estimates(db, as_of=as_of)

    agg = proposal_pipeline._precompute_aggregates(db, wt - timedelta(days=writer.WINDOW_DAYS - 1), wt)
    row = db.query(NaverPooledEstimateDaily).filter_by(scope_key="nkw-A").one()
    expect = hierarchical_pooling.pool_all(
        {"imp": 400, "clk": 40, "conv_cnt": 4, "conv_amt": 200000},
        agg["group"]["grp-1"], agg["campaign"]["cmp-1"], agg["account"],
    )
    assert Decimal(row.pooled_ctr) == expect["ctr"]
    assert Decimal(row.pooled_cvr) == expect["cvr"]
    assert Decimal(row.pooled_rpc) == expect["rpc"]


# ─────────────────────────────────────────────────────────────────────────
# ④⑥ 중복·sentinel·무신호 처리
# ─────────────────────────────────────────────────────────────────────────
def test_same_keyword_in_two_groups_keeps_one_row_and_last_value(db):
    """같은 keyword_id가 두 그룹에 걸쳐 오면 행은 1개, 값은 잃지 않는다(두 번째가 UPDATE로 흐른다)."""
    as_of = date(2026, 8, 20)
    wt = _yesterday_window(as_of)
    db.add_all([
        _row(wt, kw="nkw-DUP", grp="grp-1", imp=100, clk=10, cnt=1, amt=10000),
        _row(wt, kw="nkw-DUP", grp="grp-2", imp=200, clk=20, cnt=2, amt=20000),
    ])
    db.commit()
    result = writer.write_pooled_estimates(db, as_of=as_of)
    assert result["complete"] is True

    rows = db.query(NaverPooledEstimateDaily).filter_by(scope_key="nkw-DUP").all()
    assert len(rows) == 1
    # 두 번째 grain의 값이 실제로 반영됐다 — 세션 밖 객체에 써서 조용히 버려지면 여기서 죽는다.
    assert rows[0].adgroup_id == "grp-2"
    assert rows[0].n_imp == 200
    assert result["written"] + result["updated"] == 2


def test_group_grain_sentinel_rows_are_excluded(db):
    """keyword_id='' (쇼핑·브랜드검색 그룹 단위 행)은 키워드가 아니다 — 적재 대상에서 빠진다."""
    as_of = date(2026, 8, 20)
    wt = _yesterday_window(as_of)
    db.add_all([
        _row(wt, kw="", ctype="SHOPPING", imp=5000, clk=300, cnt=10, amt=900000),
        _row(wt, kw="", camp="cmp-2", ctype="BRAND_SEARCH", imp=100, clk=9, cnt=0, amt=0),
        _row(wt, kw="nkw-real", imp=100, clk=10, cnt=1, amt=10000),
    ])
    db.commit()
    writer.write_pooled_estimates(db, as_of=as_of)
    keys = {r.scope_key for r in db.query(NaverPooledEstimateDaily).all()}
    assert keys == {"nkw-real"}


def test_no_signal_keywords_are_skipped_and_counted(db):
    """노출·클릭이 둘 다 0이면 상위 prior를 베낀 행일 뿐이다 — 남기지 않되 «셌다»는 남긴다."""
    as_of = date(2026, 8, 20)
    wt = _yesterday_window(as_of)
    db.add_all([
        _row(wt, kw="nkw-dead", imp=0, clk=0),
        _row(wt, kw="nkw-live", imp=10, clk=1, cnt=0, amt=0),
    ])
    db.commit()
    result = writer.write_pooled_estimates(db, as_of=as_of)
    assert result["skipped_no_signal"] == 1
    assert {r.scope_key for r in db.query(NaverPooledEstimateDaily).all()} == {"nkw-live"}


def test_rerun_updates_in_place_without_duplicating(db):
    """같은 날 두 번 돌아도 행이 늘지 않는다(멱등) — UNIQUE 제약에 기대지 않고 경로로 보장."""
    as_of = date(2026, 8, 20)
    wt = _yesterday_window(as_of)
    db.add(_row(wt, kw="nkw-1", imp=100, clk=10, cnt=1, amt=10000))
    db.commit()
    first = writer.write_pooled_estimates(db, as_of=as_of)
    second = writer.write_pooled_estimates(db, as_of=as_of)
    assert first["written"] == 1 and first["updated"] == 0
    assert second["written"] == 0 and second["updated"] == 1
    assert db.query(func.count(NaverPooledEstimateDaily.id)).scalar() == 1


# ─────────────────────────────────────────────────────────────────────────
# ⑤ 미완주 표면화 — 성공으로 위장하지 않는다
# ─────────────────────────────────────────────────────────────────────────
def test_failure_is_surfaced_as_incomplete_not_silent_success(db, monkeypatch):
    as_of = date(2026, 8, 20)
    wt = _yesterday_window(as_of)
    db.add(_row(wt, kw="nkw-1", imp=100, clk=10, cnt=1, amt=10000))
    db.commit()

    def boom(*a, **kw):
        raise RuntimeError("계산 폭발")

    # ★리팩터(1R P2 채택) 후 writer가 부르는 것은 pool_all_with_priors다 — 옛 이름을 패치하면
    # 아무것도 가로채지 못한 채 «성공»으로 통과한다(테스트가 조용히 무력해지는 전형).
    monkeypatch.setattr(hierarchical_pooling, "pool_all_with_priors", boom)
    result = writer.write_pooled_estimates(db, as_of=as_of)
    assert result["complete"] is False
    assert "계산 폭발" in result["incomplete_reason"]
    # 부분 적재가 남지 않는다(rollback)
    assert db.query(func.count(NaverPooledEstimateDaily.id)).scalar() == 0


def test_job_raises_when_incomplete_so_last_status_is_error(monkeypatch):
    """잡이 삼키면 `last_status='ok'`가 굳는다 — 교훈 #319·#321의 네 번째 재현 방지."""
    from app.services import scheduler_service

    class _DummyDB:
        def close(self):
            pass

    monkeypatch.setattr(scheduler_service, "_get_own_db_session", lambda: _DummyDB())
    monkeypatch.setattr(
        writer, "write_pooled_estimates",
        lambda db, **kw: {
            "window_from": "2026-07-22", "window_to": "2026-08-20", "candidates": 5,
            "written": 2, "updated": 0, "skipped_no_signal": 0,
            "complete": False, "incomplete_reason": "RuntimeError: 중단",
        },
    )
    with pytest.raises(RuntimeError, match="미완주"):
        scheduler_service.write_naver_pooled_estimates_job()


def test_job_returns_result_dict_on_success(monkeypatch):
    """수동 트리거 응답에 실릴 dict를 실제로 돌려준다 — 고정 문구만 돌려주면 누른 사람이 못 안다."""
    from app.services import scheduler_service

    class _DummyDB:
        def close(self):
            pass

    payload = {
        "window_from": "2026-07-22", "window_to": "2026-08-20", "candidates": 5,
        "written": 5, "updated": 0, "skipped_no_signal": 1,
        "complete": True, "incomplete_reason": None,
    }
    monkeypatch.setattr(scheduler_service, "_get_own_db_session", lambda: _DummyDB())
    monkeypatch.setattr(writer, "write_pooled_estimates", lambda db, **kw: payload)
    assert scheduler_service.write_naver_pooled_estimates_job() == payload


# ─────────────────────────────────────────────────────────────────────────
# S1-ⓓ BRAND_SEARCH 시드 — 스키마·의미
# ─────────────────────────────────────────────────────────────────────────
def test_brand_search_seed_rows_are_inert(db):
    """시드는 optimizer='none'·auto_operate=False라 어떤 자동 경로도 열지 않는다."""
    for cid in ("cmp-a001-04-000000005294498", "cmp-a001-04-000000009198275"):
        db.add(NaverCampaignSettings(campaign_id=cid, optimizer="none", auto_operate=False))
    db.commit()
    rows = db.query(NaverCampaignSettings).filter(
        NaverCampaignSettings.campaign_id.like("cmp-a001-04-%")
    ).all()
    assert len(rows) == 2
    assert all(r.optimizer == "none" for r in rows)
    assert all(bool(r.auto_operate) is False for r in rows)


def test_window_is_yesterday_backwards_not_including_today(db):
    """당일 행은 수집이 진행 중이라 분모가 계속 자란다 — 창에 넣으면 «미완성»을 확정치로 굳힌다."""
    as_of = date(2026, 8, 20)
    db.add_all([
        _row(as_of, kw="nkw-today", imp=999999, clk=99999, cnt=99, amt=99999999),
        _row(as_of - timedelta(days=1), kw="nkw-yday", imp=100, clk=10, cnt=1, amt=10000),
    ])
    db.commit()
    result = writer.write_pooled_estimates(db, as_of=as_of)
    assert result["window_to"] == (as_of - timedelta(days=1)).isoformat()
    assert {r.scope_key for r in db.query(NaverPooledEstimateDaily).all()} == {"nkw-yday"}


def test_quantization_width_is_pinned_so_precision_loss_stays_visible():
    """세 지표가 같은 폭(1e-4)으로 양자화된다는 사실을 못박는다.

    ①이게 `pooled_rpc` 동치(회귀 0)의 근거다 — 한쪽 폭만 바뀌면 동치가 조용히 깨진다.
    ②동시에 **CTR·CVR의 해상도 한계**를 드러낸다: 비율 지표에 1e-4는 0.01%p 격자다. CTR이
      1e-4보다 작은 롱테일 키워드는 pooled_ctr이 0 또는 1e-4로 뭉친다. 아래 assert가 그 뭉침을
      «관측»으로 고정한다 — 숫자가 작아 안 보이는 손실은 테스트가 말해 줘야 한다.
    """
    assert hierarchical_pooling._Q4 == Decimal("0.0001")
    # CTR 1e-5 수준(노출 100만·클릭 10)의 키워드: 상위 prior도 같은 수준이면 결과가 격자에 뭉친다.
    tiny = {"imp": 1_000_000, "clk": 10, "conv_cnt": 0, "conv_amt": 0}
    out = hierarchical_pooling.pool_all(tiny, tiny, tiny, tiny)
    assert out["ctr"] == Decimal("0.0000")  # 실제 CTR 1e-5가 0으로 뭉친다 — 알고 쓰는 한계다


# ═════════════════════════════════════════════════════════════════════════
# 적대 리뷰 1R에서 **살아남은 변이 3종**을 죽이는 테스트.
# 살아남은 변이는 그 자체가 발견이다 — 「통과하는데 아무것도 안 지키는」 자리를 가리킨다.
# 세 경우 다 구현은 옳았고(리뷰어가 직접 재현해 확인), 없던 것은 «그걸 지키는 테스트»였다.
# ═════════════════════════════════════════════════════════════════════════

def test_impressions_without_clicks_are_kept_not_skipped(db):
    """변이 `and`→`or` 사살. imp>0·clk=0은 실무에서 흔하다(노출됐는데 안 눌린 키워드).

    그 상태야말로 CTR 신호의 핵심 표본이다 — `or`로 바뀌면 조용히 통째로 버려지는데,
    산출 행이 줄어드는 것 말고는 아무 증상이 없어 눈으로는 절대 안 보인다.
    """
    as_of = date(2026, 8, 20)
    wt = _yesterday_window(as_of)
    db.add_all([
        _row(wt, kw="nkw-seen-not-clicked", imp=5000, clk=0),   # 노출만
        _row(wt, kw="nkw-clicked-no-imp", imp=0, clk=3, cnt=0, amt=0),  # 원장 이상치지만 신호는 있다
        _row(wt, kw="nkw-dead", imp=0, clk=0),                  # 진짜 무신호
    ])
    db.commit()
    result = writer.write_pooled_estimates(db, as_of=as_of)

    keys = {r.scope_key for r in db.query(NaverPooledEstimateDaily).all()}
    assert keys == {"nkw-seen-not-clicked", "nkw-clicked-no-imp"}
    assert result["skipped_no_signal"] == 1
    row = db.query(NaverPooledEstimateDaily).filter_by(scope_key="nkw-seen-not-clicked").one()
    assert row.n_imp == 5000 and row.n_clk == 0
    assert Decimal(row.raw_ctr) == Decimal("0")  # 관측 CTR은 0이 맞다(분모 있음, 분자 0)


def test_campaign_layer_is_not_skipped_in_the_stored_prior(db):
    """변이 「campaign 단 건너뛰기」 사살 — **캠페인 2개 이상 + 캠페인별 CTR이 다른** 픽스처가 필요하다.

    기존 픽스처는 캠페인이 1개뿐이라 campaign_agg == account_agg가 되어 campaign_pooled와
    acct_raw가 수학적으로 같아진다 — 3단 중 한 단을 통째로 빼도 값이 안 변한다. 우연히
    통과하던 자리다. 여기서는 두 캠페인의 CTR을 크게 벌려 그 우연을 깨뜨린다.
    """
    as_of = date(2026, 8, 20)
    wt = _yesterday_window(as_of)
    # cmp-hi: CTR 10% (imp 1,000 / clk 100) · cmp-lo: CTR 0.1% (imp 100,000 / clk 100)
    db.add_all([
        _row(wt, kw="nkw-hi", camp="cmp-hi", grp="grp-hi", imp=1_000, clk=100, cnt=5, amt=250_000),
        _row(wt, kw="nkw-lo", camp="cmp-lo", grp="grp-lo", imp=100_000, clk=100, cnt=1, amt=10_000),
    ])
    db.commit()
    writer.write_pooled_estimates(db, as_of=as_of)

    agg = proposal_pipeline._precompute_aggregates(
        db, wt - timedelta(days=writer.WINDOW_DAYS - 1), wt)
    row = db.query(NaverPooledEstimateDaily).filter_by(scope_key="nkw-hi").one()

    # 저장된 prior는 «3단을 다 거친» 그룹 레벨 값이어야 한다.
    _, priors = hierarchical_pooling.pool_all_with_priors(
        {"imp": 1_000, "clk": 100, "conv_cnt": 5, "conv_amt": 250_000},
        agg["group"]["grp-hi"], agg["campaign"]["cmp-hi"], agg["account"],
    )
    # prior_ctr 컬럼은 Numeric(12,6) — 저장이 6자리로 반올림한다. 완전일치가 아니라 그 격자에
    # 맞춘 일치를 본다(격자를 넘는 차이는 «다른 계산»이라는 뜻이고, 그게 이 테스트의 표적이다).
    assert Decimal(row.prior_ctr) == priors["ctr"].quantize(Decimal("0.000001"))

    # 그리고 그 값은 «계정 raw»와 확실히 달라야 한다 — 같다면 캠페인 단이 무의미해진 것이다.
    acct = agg["account"]
    acct_raw_ctr = Decimal(acct["clk"]) / Decimal(acct["imp"])
    assert Decimal(row.prior_ctr) != acct_raw_ctr
    # 고CTR 캠페인 소속이므로 계정 평균보다 위로 당겨져 있어야 한다(방향까지 고정).
    assert Decimal(row.prior_ctr) > acct_raw_ctr


def test_partial_writes_are_rolled_back_when_a_later_keyword_explodes(db):
    """변이 「rollback 제거」 사살 — 예외가 **이미 add한 뒤에** 나야 롤백 유무가 드러난다.

    기존 테스트는 첫 키워드 처리 «전»에 터뜨려서 add된 것이 하나도 없었고, 그래서 롤백을
    지워도 통과했다. 부분 적재가 남으면 그날 행이 «일부만 있는 채» 완주처럼 보인다.
    """
    as_of = date(2026, 8, 20)
    wt = _yesterday_window(as_of)
    db.add_all([
        _row(wt, kw="nkw-1", imp=100, clk=10, cnt=1, amt=10_000),
        _row(wt, kw="nkw-2", imp=200, clk=20, cnt=2, amt=20_000),
        _row(wt, kw="nkw-3", imp=300, clk=30, cnt=3, amt=30_000),
    ])
    db.commit()

    real = hierarchical_pooling.pool_all_with_priors
    calls = {"n": 0}

    def explode_on_third(*a, **kw):
        calls["n"] += 1
        if calls["n"] >= 3:
            raise RuntimeError("세 번째에서 폭발")
        return real(*a, **kw)

    import app.services.naver_ad.pooled_estimate_writer as w
    w.hierarchical_pooling.pool_all_with_priors = explode_on_third
    try:
        result = w.write_pooled_estimates(db, as_of=as_of)
    finally:
        w.hierarchical_pooling.pool_all_with_priors = real

    assert result["complete"] is False
    assert calls["n"] >= 3  # 앞의 두 건은 실제로 add됐다 — 롤백이 없으면 세션에 남는다

    # ★`count(*)`만으로는 롤백을 못 잰다 — 픽스처가 prod와 같은 autoflush=False라 pending add가
    #   flush되지 않아, 롤백을 지워도 쿼리는 0을 본다(변이 재주입으로 실측: M2 생존).
    #   그래서 **세션의 pending 집합**을 직접 본다. 이게 「롤백했다」의 진짜 내용이다.
    assert len(db.new) == 0, "롤백 후에도 미flush add가 세션에 남아 있다"
    # 그리고 뒤이어 누가 commit 해도 부분 적재가 새어 나가면 안 된다(호출측이 세션을 공유하는 경우).
    db.commit()
    assert db.query(func.count(NaverPooledEstimateDaily.id)).scalar() == 0


def test_migration_uses_boolean_not_integer_for_auto_operate():
    """적대 리뷰 1R P1 재발 방지 — PostgreSQL에서 `boolean = integer`는 연산자가 없다.

    SQLite는 타입을 강제하지 않아 **런타임 테스트로는 원리적으로 못 잡는다**(로컬·CI 전부 초록).
    그래서 소스 자체를 검사한다. 이 검사가 보는 것은 「정수 리터럴이 auto_operate에 안 닿는가」다.
    """
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / \
        "m2a1pool2eb3_add_pooled_estimate.py"
    text = src.read_text(encoding="utf-8")
    # ★**개수를 센다.** 「존재하는가」만 보면 두 자리 중 한 자리만 망가뜨렸을 때 나머지가 검사를
    #   통과시킨다(변이 재주입으로 실측: M4 생존). 발견 0건과 실행 안 됨이 같은 숫자로 보이는
    #   것과 같은 함정이다(교훈 #123). auto_operate가 걸리는 자리는 upgrade INSERT·downgrade
    #   DELETE **정확히 둘**이고, 둘 다 파이썬 bool이어야 한다.
    assert text.count('"auto_operate": False') == 2, "bool 바인딩이 두 자리 모두에 있어야 한다"
    assert "auto_operate = 0" not in text
    assert "auto_operate\": 0" not in text
    assert "'none', 0," not in text


def test_stored_prior_keeps_full_precision_not_the_display_grid(db):
    """변이 「prior도 _Q4로 quantize」 사살.

    prior는 **검산 공식의 입력**이지 표시값이 아니다. 4자리로 뭉개면 저장된 (n,raw,prior,K)로
    계산한 값이 실제 pooled와 미세하게 어긋나고, 허용오차가 그걸 덮어 «검산 통과»라고 말한다 —
    합격기준이 거짓말을 하는 정확한 경로다. 그래서 prior가 4자리 격자 «밖»의 정보를 갖는
    사례를 하나 고정한다.
    """
    as_of = date(2026, 8, 20)
    wt = _yesterday_window(as_of)
    db.add_all([
        _row(wt, kw="nkw-hi", camp="cmp-hi", grp="grp-hi", imp=1_000, clk=100, cnt=5, amt=250_000),
        _row(wt, kw="nkw-lo", camp="cmp-lo", grp="grp-lo", imp=100_000, clk=100, cnt=1, amt=10_000),
    ])
    db.commit()
    writer.write_pooled_estimates(db, as_of=as_of)

    row = db.query(NaverPooledEstimateDaily).filter_by(scope_key="nkw-hi").one()
    prior = Decimal(row.prior_ctr)
    assert prior != prior.quantize(Decimal("0.0001")), (
        f"prior_ctr={prior} 가 4자리 격자에 뭉쳐 있다 — 검산 입력이 표시값으로 대체됐다"
    )


def test_window_length_is_pinned_to_thirty_days(db):
    """창 길이는 계약 §3 금지선이다(「분석 창 길이 변경 금지」) — 아무도 안 지키면 조용히 바뀐다.

    창은 산출값의 의미를 정한다. 30일이 31일이 되면 모든 추정치가 달라지는데 화면상 증상은 없다.
    """
    as_of = date(2026, 8, 20)
    wt = _yesterday_window(as_of)
    db.add(_row(wt, kw="nkw-1", imp=100, clk=10, cnt=1, amt=10_000))
    db.commit()
    result = writer.write_pooled_estimates(db, as_of=as_of)

    assert writer.WINDOW_DAYS == 30
    w_from = date.fromisoformat(result["window_from"])
    w_to = date.fromisoformat(result["window_to"])
    assert (w_to - w_from).days == 29, "포함 창 30일 = 끝-시작 29일(off-by-one 고정)"
    assert w_to == as_of - timedelta(days=1)
    row = db.query(NaverPooledEstimateDaily).one()
    assert row.window_from == w_from and row.window_to == w_to  # 행에도 같은 창이 박힌다
