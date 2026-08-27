# test_pl_gate_specs_promotion.py — D-NAO-262 (S4): 파워링크 제외 게이트 2종의 SPECS 승격
#
# ══ 무엇을 지키는 테스트인가 ══
# 계약 `CONTRACT_ignition_readiness.md` §4-C S4-a: *"승인 카드/성적표 표면(prod GET 응답)에 새
# 파라미터가 현행값·봉투와 함께 뜬다 — Jino가 그 응답에서 행을 본다"*.
#
# ★그래서 이 파일은 「SPECS에 키가 있다」에서 멈추지 않는다. 등재는 **만드는 층**이고, 합격기준이
#   지목한 건 **닿는 층**이다(교훈 #362 — n=57·n=58·n=59가 연달아 같은 자리에서 데였다):
#     ① DB 값이 «게이트»를 실제로 움직이는가 (판정 결과가 바뀌는가)
#     ② DB 값이 «사람이 읽는 사유 문장»에 반영되는가 ← 여기가 옛 결함의 자리
#   ①만 지키면 게이트는 DB값(10)으로 도는데 카드의 사유는 옛 숫자(5)를 말하는 상태가 초록으로 산다.
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAccountSettings,
    NaverAdDaily,
    NaverAdgroupProduct,
    NaverCampaignSettings,
    NaverProductBep,
    NaverSearchTermDaily,
)
from app.services.naver_ad import guardrail_params, search_term_judge as judge
from app.services.naver_ad.campaign_target_resolver import NAVER_CHANNEL_ID

_NOW = datetime(2026, 7, 22, 9, 0, 0)  # 기본 창 30일 → from=2026-06-23 / 창 14일 → from=2026-07-09


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


def _params(db, **kv):
    """`guardrail_params` KV에 값을 심는다 — 승인 카드가 저장하는 것과 같은 자리."""
    db.add(NaverAccountSettings(key=guardrail_params.SETTINGS_KEY, value_json=json.dumps(kv)))
    db.commit()


def _base(db, *, adgroup_id="grp-web", campaign_id="cmp1"):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer="ours", auto_operate=True))
    db.add(NaverProductBep(
        channel_id=NAVER_CHANNEL_ID, channel_product_id="P1", has_cost=True,
        product_name="필름 정품", selling_price=Decimal("1000"),
        contribution_margin=Decimal("500"), cost_price=0, commission_rate=0, logistics_cost=0,
        target_roas=Decimal("2.0"), bep_roas=Decimal("1.5"),
    ))
    db.add(NaverAdgroupProduct(adgroup_id=adgroup_id, mall_product_id="P1", campaign_id=campaign_id))
    # gate ④ 그룹 순손실 프록시 — 두 창(14·30) «양쪽에» 들어오는 날짜라 창 실험의 교란이 아니다.
    db.add(NaverAdDaily(
        ad_date=date(2026, 7, 10), campaign_id=campaign_id, campaign_type="WEB_SITE",
        adgroup_id=adgroup_id, keyword_id="nkw-1", imp=500, clk=50, cost=10000, rank_sum=0,
        conv_direct_cnt=0, conv_indirect_cnt=0, conv_direct_amt=1000, conv_indirect_amt=0,
    ))
    db.commit()


def _term(db, *, term, clk, cost=6000, ad_date=date(2026, 7, 10),
          adgroup_id="grp-web", campaign_id="cmp1"):
    db.add(NaverSearchTermDaily(
        ad_date=ad_date, campaign_id=campaign_id, adgroup_id=adgroup_id, search_term=term,
        source="expkeyword", imp=100, clk=clk, cost=cost, rank_sum=0,
        conv_purchase_cnt=0, conv_direct_cnt=0, conv_purchase_amt=0, cart_cnt=0, cart_amt=0,
    ))
    db.commit()


def _cands(out):
    return {c["search_term"] for c in out["exclude_candidates"]["powerlink"]}


def _reason_of(out, term):
    for c in out["exclude_candidates"]["powerlink"]:
        if c["search_term"] == term:
            return c["reason"]
    raise AssertionError(f"{term!r}이 후보에 없다 — 사유를 볼 수 없다")


# ── ① 등재: 계약 §4-B⑤ 봉투 표 «그대로»인가 (숫자를 이 세션이 발명하지 않았다는 고정) ──
def test_승격_2건이_계약_봉투_그대로_등재됐다():
    assert guardrail_params.SPECS["pl_min_click"].lo == 5
    assert guardrail_params.SPECS["pl_min_click"].hi == 10
    assert guardrail_params.SPECS["pl_window_days"].lo == 14
    assert guardrail_params.SPECS["pl_window_days"].hi == 90


def test_default는_판정기_상수를_참조한다_복사가_아니다():
    """두 곳에 숫자를 적으면 갈라진다 — SPECS의 기존 3종이 지키는 규칙과 같은 계약."""
    assert guardrail_params.SPECS["pl_min_click"].default is judge._PL_MIN_CLICK
    assert guardrail_params.SPECS["pl_window_days"].default is judge._PL_WINDOW_DAYS


# ── ② 닿는 층 1: DB 값이 «게이트»를 실제로 움직이는가 ──
def test_db가_최소클릭을_올리면_후보에서_빠진다(db):
    _base(db)
    _params(db, pl_min_click=10)
    _term(db, term="여섯클릭", clk=6)
    assert "여섯클릭" not in _cands(judge.judge_search_terms(db, now=_NOW))


def test_대조군_db값이_없으면_같은_행이_후보다(db):
    """위 테스트가 «클릭이 적어서»가 아니라 «DB값 때문에» 빠진 것임을 가르는 대조군."""
    _base(db)
    _term(db, term="여섯클릭", clk=6)  # 코드 상수 5 → 6 >= 5 통과
    assert "여섯클릭" in _cands(judge.judge_search_terms(db, now=_NOW))


def test_db가_창을_좁히면_창_밖_행이_빠진다(db):
    _base(db)
    _params(db, pl_window_days=14)  # from=2026-07-09
    _term(db, term="열이레전", clk=10, ad_date=date(2026, 7, 5))
    assert "열이레전" not in _cands(judge.judge_search_terms(db, now=_NOW))


def test_대조군_기본창_30일이면_같은_행이_후보다(db):
    _base(db)
    _term(db, term="열이레전", clk=10, ad_date=date(2026, 7, 5))  # from=2026-06-23
    assert "열이레전" in _cands(judge.judge_search_terms(db, now=_NOW))


# ── ③ 닿는 층 2 (★표면): DB 값이 «사람이 읽는 사유»에 반영되는가 ──
def test_사유_문장이_DB값을_말한다_옛_상수가_아니라(db):
    """★이 테스트가 이 파일의 존재 이유다.

    게이트만 배선하고 `_pl_reason`을 안 고치면, 제외 카드에 「clk=12(≥5)」라고 찍힌다 —
    실제로 도는 게이트는 10인데. Jino가 읽는 건 그 문장이다.
    """
    _base(db)
    _params(db, pl_min_click=10)
    _term(db, term="열두클릭", clk=12)
    reason = _reason_of(judge.judge_search_terms(db, now=_NOW), "열두클릭")
    assert "(≥10)" in reason, f"사유가 DB값을 안 말한다: {reason}"
    assert "(≥5)" not in reason, f"사유가 옛 코드 상수를 말한다: {reason}"


def test_사유의_창_표기도_DB값을_따른다(db):
    _base(db)
    _params(db, pl_window_days=14)
    _term(db, term="창표기", clk=10)
    reason = _reason_of(judge.judge_search_terms(db, now=_NOW), "창표기")
    assert "2026-07-09" in reason, f"사유의 창 시작일이 14일 창을 안 따른다: {reason}"


# ── ④ 봉투 밖 값은 코드 상수로 폴백한다 (fail-to-current — 이 층의 계약) ──
def test_봉투_밖_값은_게이트를_못_움직인다(db):
    """DB가 자기 상한을 넓힐 수 없다 — 범위는 배포로만 바뀐다."""
    _base(db)
    _params(db, pl_min_click=99)  # hi=10 밖 → 코드 상수 5로 폴백
    _term(db, term="여섯클릭", clk=6)
    out = judge.judge_search_terms(db, now=_NOW)
    assert "여섯클릭" in _cands(out)
    assert "(≥5)" in _reason_of(out, "여섯클릭")


# ── ⑤ 인구조사: 분산 보류 2건이 «조용히» 승격되지 않게 못 박는다 ──
def test_분산이_남은_SS_게이트_2종은_아직_SPECS에_없다():
    """★이 가드가 잡는 것: 다음 세션이 봉투 표만 보고 SS 2종을 마저 등재하는 것.

    계약 §4-B⑤의 「승격 전 검사」가 2026-08-27에 분산을 실측했다(그래서 «기록만» 했다):
      · `_SS_WINDOW_DAYS` — `naver_execution_harness.py:1245`가 직접 읽는다(GATE ⑥ 실행
        재검증). 갈리면 판정 창과 실행 재검증 창이 어긋난다.
      · `_SS_MIN_CLICK` — `search_term_exclusion_list.py:48`에 복제 리터럴 `MIN_CLICK = 10`.
        카드에서 내려도 하류가 안 따라온다 ⇒ 승인 카드가 거짓말을 한다.

    승격하려면 **그 소비처를 먼저 배선**하고, 이 테스트를 「배선됐음」을 재는 것으로 바꿔라.
    이 줄을 그냥 지우는 것은 검사를 지우는 것이지 통과하는 게 아니다.
    """
    assert "ss_min_click" not in guardrail_params.SPECS
    assert "ss_window_days" not in guardrail_params.SPECS


def test_SPECS_인구조사_현재_5종(db):
    """키가 늘면 이 줄이 빨개진다 — 「무엇이 학습 가능한가」가 조용히 안 바뀌도록."""
    assert set(guardrail_params.SPECS) == {
        "cooldown_hours", "max_daily_auto_bid_downs", "max_auto_up_multiple",
        "pl_min_click", "pl_window_days",
    }


# ══ #14 창 재료 커버리지 — 「창을 끝까지 늘렸을 때 그만큼의 재료가 있나」 ══
# 계약 #14의 전제(「원본 보존 16일」)는 2026-08-27 실측으로 정정됐다: 그 16일은 **네이버 리포트
# 보관 기한**(ref 21)이지 우리 DB 보존이 아니고, 우리 쪽엔 purge가 없어 이미 상한을 넘겨 갖고
# 있다(봉투 상한 90일 창 결손 0일). ⇒ 남은 실질은 「늘리기」가 아니라 **「늘려도 되는지 상시
# 보이게 하기」** — 그 관측이 실제로 결손을 세는지를 여기서 못 박는다.
from app.routers.naver_ad import guardrail_params_window_coverage  # noqa: E402


def _cov(db, key):
    for r in guardrail_params_window_coverage(db):
        if r["param_key"] == key:
            return r
    raise AssertionError(f"{key} 행이 없다")


def test_원본이_봉투_상한을_다_덮으면_covered(db):
    _base(db)
    latest = date(2026, 7, 22)
    for i in range(guardrail_params.SPECS["pl_window_days"].hi):  # 90일 연속
        _term(db, term=f"t{i}", clk=1, ad_date=latest - timedelta(days=i))
    row = _cov(db, "pl_window_days")
    assert row["ceiling_days"] == 90
    assert row["missing_days"] == 0
    assert row["covered"] is True


def test_중간에_결손이_있으면_세어진다(db):
    """★수집이 며칠 죽으면 값은 90인데 실제로 보는 건 87일이다 — 그게 보여야 한다."""
    _base(db)
    latest = date(2026, 7, 22)
    for i in range(90):
        if i in (10, 11, 12):  # 사흘 결손(2026-08-26 크론 결손 전례의 축소판)
            continue
        _term(db, term=f"t{i}", clk=1, ad_date=latest - timedelta(days=i))
    row = _cov(db, "pl_window_days")
    assert row["missing_days"] == 3, row
    assert row["covered"] is False


def test_원본이_아예_없으면_창을_못_세운다고_말한다(db):
    _base(db)
    row = _cov(db, "pl_window_days")
    assert row["covered"] is False
    assert row["latest"] is None
    assert "0행" in row["note"]


def test_승격_보류분도_같이_관측된다_봉투가_없다는_사실과_함께(db):
    """SS 창은 SPECS에 없다(분산 보류). 그래도 재료는 재서, 승격되는 날 이미 있는지 보이게 한다.

    ★두 source «다 데이터가 있는» 상태로 잰다 — 빈 DB로만 재면 `latest is None` 분기만 밟혀
    데이터 분기의 `promoted`를 아무도 안 지킨다(변이 M9가 그 구멍으로 살아남았다).
    """
    _base(db)
    _term(db, term="pl", clk=1, ad_date=date(2026, 7, 22))
    db.add(NaverSearchTermDaily(
        ad_date=date(2026, 7, 22), campaign_id="cmp1", adgroup_id="grp-web",
        search_term="ss", source="shopping", imp=10, clk=1, cost=100, rank_sum=0,
        conv_purchase_cnt=0, conv_direct_cnt=0, conv_purchase_amt=0, cart_cnt=0, cart_amt=0,
    ))
    db.commit()
    rows = guardrail_params_window_coverage(db)
    assert all(r["latest"] is not None for r in rows), "두 분기 중 데이터 분기를 밟아야 한다"
    ss = [r for r in rows if r["source"] == "shopping"]
    assert len(ss) == 1
    assert ss[0]["param_key"] is None
    assert ss[0]["promoted"] is False, "봉투 없음이 «재료 없음»에 덮이면 안 된다"
    # 승격분은 같은 응답에서 promoted=True — 두 사실이 한 필드를 다투지 않는다
    assert _cov(db, "pl_window_days")["promoted"] is True


def test_GET_응답에_실제로_실린다(db):
    """★표면 — 계산만 되고 응답에 안 실리면 Jino는 못 본다(교훈 #362)."""
    from app.routers.naver_ad import guardrail_params_get
    body = guardrail_params_get(db=db)
    assert "window_coverage" in body
    assert {r["source"] for r in body["window_coverage"]} == {"expkeyword", "shopping"}
    # 승격 2건이 봉투와 함께 같은 응답에 뜬다 = 계약 §4-C S4-a가 요구한 그 화면
    keys = {r["key"]: r for r in body["params"]}
    assert keys["pl_min_click"]["min"] == 5 and keys["pl_min_click"]["max"] == 10
    assert keys["pl_window_days"]["min"] == 14 and keys["pl_window_days"]["max"] == 90
