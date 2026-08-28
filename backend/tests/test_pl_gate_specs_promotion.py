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


# ── ⑤ 인구조사: SS 2종의 «분산 없음»을 소비처 전수로 못 박는다 (D-NAO-265) ──
# ★이 블록은 원래 「SS 2종은 아직 SPECS에 없다」를 재는 보류 가드였다(D-NAO-262). 그 테스트의
#   docstring이 남긴 지시가 *"승격하려면 그 소비처를 먼저 배선하고, 이 테스트를 「배선됐음」을 재는
#   것으로 바꿔라. 이 줄을 그냥 지우는 것은 검사를 지우는 것이지 통과하는 게 아니다"*였다.
#   아래가 그 전환이다 — 검사는 사라지지 않고 **더 센 것으로** 바뀌었다(존재 → 소비처 전수 정합).
def test_SS_2종이_계약_봉투_그대로_등재됐다():
    """계약 §4-B⑤ 봉투 표 그대로. 이 세션이 발명한 수가 없다는 고정."""
    assert guardrail_params.SPECS["ss_min_click"].lo == 5
    assert guardrail_params.SPECS["ss_min_click"].hi == 21
    assert guardrail_params.SPECS["ss_window_days"].lo == 7
    assert guardrail_params.SPECS["ss_window_days"].hi == 16


def test_모든_SPECS_default가_리터럴이_아니라_심볼_참조다():
    """★자기 변이 M2가 살려낸 구멍(2026-08-28) — 값 비교로는 «복사»를 못 잡는다.

    기존 가드는 `SPECS[k].default is judge._SS_MIN_CLICK`이었는데, CPython이 작은 정수를
    캐싱하므로 **리터럴 `10`을 적어도 `is`가 True**다. 즉 「두 곳에 숫자를 적지 마라」는 규칙을
    어겨도 초록이었다 — `pl` 2종도 같은 구멍이었다(n=60에서 물려받았다).

    그래서 값이 아니라 **소스**를 본다: `ParamSpec(...)`의 두 번째 인자(default)가 상수
    리터럴이면 실패. 전 키를 훑으므로 새 키가 늘어도 자동으로 지켜진다.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(guardrail_params))
    checked = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ParamSpec"):
            continue
        key = node.args[0].value          # 첫 인자 = 키 문자열
        default_arg = node.args[1]        # 둘째 인자 = default
        assert not isinstance(default_arg, ast.Constant), (
            f"SPECS[{key!r}].default가 리터럴이다 — 판정기 상수를 «참조»해야 두 곳이 안 갈린다"
        )
        checked.add(key)
    assert checked == set(guardrail_params.SPECS), (
        f"AST가 못 훑은 키가 있다: {set(guardrail_params.SPECS) - checked}"
    )


def test_보류_사유였던_소비처_2곳에_이제_상수_직접읽기가_없다():
    """★보류의 실체는 «값»이 아니라 «분산»이었다 — 그 분산이 사라졌음을 소스로 못 박는다.

    심볼 참조를 세는 이유: 이름을 다시 쓰는 순간(예: 편의를 위해 상수를 도로 읽는 리팩터)
    승인 카드는 다시 거짓말을 시작하는데, **값 테스트로는 안 잡힌다**(DB를 안 건드리는 기본
    경로에선 상수와 DB값이 같아서 초록이다). 그래서 «코드가 그 이름을 읽는가»를 직접 센다.
    """
    import ast
    import inspect
    import textwrap

    from app.services.naver_ad import naver_execution_harness, search_term_exclusion_list

    fn = naver_execution_harness._search_term_conversions_in_window
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))

    # ★텍스트 grep이 아니라 **AST 속성 접근**을 센다. 초판은 소스를 통짜로 훑었는데, 그 함수의
    #   docstring이 「예전엔 _SS_WINDOW_DAYS를 직접 읽었다」는 내력을 일부러 적어 두므로 **설명이
    #   위반으로 잡혔다**. 내력은 남기고 직접읽기만 막아야 하므로, 「코드가 그 이름을 읽는가」를
    #   구문으로 묻는 것이 옳다 — 주석·docstring은 원리적으로 안 걸린다.
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "_ss_params" in attrs, "실행 재검증이 DB 출처를 안 읽는다"
    assert "_SS_WINDOW_DAYS" not in attrs, "모듈 상수 직접읽기가 되살아났다"

    # 복제 리터럴이 있던 자리 — 이름 자체가 사라졌어야 한다(값만 같게 두면 또 갈린다).
    assert not hasattr(search_term_exclusion_list, "MIN_CLICK"), (
        "복제 리터럴 `MIN_CLICK`이 되살아났다 — 단일 출처는 `resolve_min_click(db)`다"
    )
    assert hasattr(search_term_exclusion_list, "resolve_min_click")


# ── ⑥ SS의 «닿는 층» — pl과 같은 두 축(게이트가 움직이나 / 사유가 DB값을 말하나) ──
def _ss_term(db, *, term, clk, cost=6000, ad_date=date(2026, 7, 10),
             adgroup_id="grp-web", campaign_id="cmp1"):
    """쇼핑 grain 행 — `_term`과 같되 `source="shopping"`."""
    db.add(NaverSearchTermDaily(
        ad_date=ad_date, campaign_id=campaign_id, adgroup_id=adgroup_id, search_term=term,
        source="shopping", imp=100, clk=clk, cost=cost, rank_sum=0,
        conv_purchase_cnt=0, conv_direct_cnt=0, conv_purchase_amt=0, cart_cnt=0, cart_amt=0,
    ))
    db.commit()


def _ss_cands(out):
    return {c["search_term"] for c in out["exclude_candidates"]["shopping"]}


def test_ss_db가_최소클릭을_올리면_후보에서_빠진다(db):
    _base(db)
    _params(db, ss_min_click=21)
    _ss_term(db, term="열두클릭", clk=12)  # 코드 상수 10은 통과, DB 21은 미달
    assert "열두클릭" not in _ss_cands(judge.judge_search_terms(db, now=_NOW))


def test_ss_대조군_db값이_없으면_같은_행이_후보다(db):
    """위 테스트가 «클릭이 적어서»가 아니라 «DB값 때문에» 빠진 것임을 가르는 대조군."""
    _base(db)
    _ss_term(db, term="열두클릭", clk=12)  # 코드 상수 10 → 12 >= 10 통과
    assert "열두클릭" in _ss_cands(judge.judge_search_terms(db, now=_NOW))


def test_ss_db가_창을_좁히면_창_밖_행이_빠진다(db):
    _base(db)
    _params(db, ss_window_days=7)  # _NOW=07-22 → from=2026-07-16
    _ss_term(db, term="열이틀전", clk=12, ad_date=date(2026, 7, 10))
    assert "열이틀전" not in _ss_cands(judge.judge_search_terms(db, now=_NOW))


def test_ss_대조군_기본창_14일이면_같은_행이_후보다(db):
    _base(db)
    _ss_term(db, term="열이틀전", clk=12, ad_date=date(2026, 7, 10))  # 14일 창 from=07-09
    assert "열이틀전" in _ss_cands(judge.judge_search_terms(db, now=_NOW))


def test_ss_사유_문장이_DB값을_말한다_옛_상수가_아니라(db):
    """★옛 결함의 자리 — 게이트는 DB값으로 도는데 카드의 사유는 코드 상수를 말하던 것."""
    _base(db)
    _params(db, ss_min_click=7)
    _ss_term(db, term="여덟클릭", clk=8)
    out = judge.judge_search_terms(db, now=_NOW)
    reason = next(c["reason"] for c in out["exclude_candidates"]["shopping"]
                  if c["search_term"] == "여덟클릭")
    assert "≥7" in reason, f"사유가 DB값을 안 말한다: {reason}"
    assert "≥10" not in reason, f"사유가 옛 코드 상수를 말한다: {reason}"


def test_ss_봉투_밖_값은_게이트를_못_움직인다(db):
    """봉투(5~21) 밖 값은 `_coerce`가 버린다 — 카드가 아무 값이나 받는 창구가 아니다."""
    _base(db)
    _params(db, ss_min_click=99)
    _ss_term(db, term="열두클릭", clk=12)
    # 99가 먹었다면 12 < 99라 빠졌을 것 — 코드 기본값 10으로 되돌아가 후보로 남아야 한다.
    assert "열두클릭" in _ss_cands(judge.judge_search_terms(db, now=_NOW))


def test_제외후보_리스트_API도_같은_DB값을_읽는다(db):
    """★복제 리터럴이 있던 하류 — 「승인 카드가 거짓말을 한다」의 실체였던 자리.

    판정기만 고치고 여기를 두면 카드에서 값을 내려도 이 목록만 옛 값으로 돈다.
    """
    from app.services.naver_ad import search_term_exclusion_list as sel

    _params(db, ss_min_click=21)
    assert sel.resolve_min_click(db) == 21
    out = sel.build_exclusion_list(db, now=_NOW)
    assert out["gates"]["min_click"] == 21, "하류 게이트가 DB값을 안 따라온다"


def test_명시_인자가_의미단위_판정에도_그대로_전달된다(db):
    """★자기 변이 M9가 살려낸 구멍(2026-08-28) — 「섞임」의 실제 경로.

    `judge_search_terms`가 명시 인자를 받고도 `judge_semantic_units`에 안 넘기면, 하위 판정이
    **혼자 DB를 다시 읽어** 상위와 다른 게이트로 돈다. 기본 경로에선 두 값이 같아서 초록이라
    값 테스트로는 안 잡힌다 — 그래서 **DB와 다른 명시값**으로 갈라 놓고 잰다.
    """
    _base(db)
    _params(db, ss_min_click=21)          # DB는 21
    _ss_term(db, term="아이스크림틀", clk=8)
    out = judge.judge_search_terms(db, now=_NOW, min_click=7, window_days=14)  # 명시는 7
    sem = out.get("semantic_units") or {}
    reasons = [c["reason"] for kind in ("unit", "pair", "residual")
               for c in (sem.get(kind) or [])]
    assert reasons, f"의미단위 후보가 0건 — 픽스처를 고쳐라 (keys={list(sem)})"
    assert all("≥7" in r for r in reasons), f"명시 인자가 의미단위에 안 전달됐다: {reasons[:2]}"


def test_HTTP_제외후보_API가_DB값을_실어_보낸다(db):
    """★진짜 «닿는 층» — Jino가 부르는 건 SA 함수가 아니라 이 URL이다.

    SA만 고치고 라우터의 `Query(...)` 기본값을 그대로 두면, 그 기본값이 **import 시점 상수**라
    HTTP로 들어오는 모든 조회가 옛 값으로 돈다 — SA 단위 테스트는 초록인 채로. 교훈 #362가
    말하는 「만드는 층은 고쳤는데 닿는 층은 안 고쳤다」의 정확한 형태이고, 이 파일이 n=57~n=62에서
    네 번 데인 자리다.
    """
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    _params(db, ss_min_click=21)
    app.dependency_overrides[get_db] = lambda: db
    try:
        r = TestClient(app).get("/api/naver/ad/search-term/exclusion-list")
        assert r.status_code == 200
        assert r.json()["gates"]["min_click"] == 21, "HTTP 표면이 옛 상수 10으로 돈다"
        # 명시 조회(what-if)는 그대로 존중 — DB가 명시 인자를 덮어쓰지 않는다.
        r2 = TestClient(app).get("/api/naver/ad/search-term/exclusion-list?min_click=8")
        assert r2.json()["gates"]["min_click"] == 8
    finally:
        app.dependency_overrides.clear()


def test_의미단위_사유도_DB값을_말한다(db):
    """★풀링 판정은 «다른 함수»다 — 개별 grain 사유만 고치면 여기가 옛 숫자를 말한다."""
    _base(db)
    _params(db, ss_min_click=7)
    _ss_term(db, term="아이스크림틀", clk=8)
    out = judge.judge_semantic_units(db, now=_NOW)
    reasons = [c["reason"] for kind in ("unit", "pair", "residual")
               for c in out.get(kind, [])]
    assert reasons, "의미단위 후보가 0건이면 사유를 검사할 수 없다 — 픽스처를 고쳐라"
    assert all("≥7" in r for r in reasons), f"의미단위 사유가 DB값을 안 말한다: {reasons[:2]}"
    assert not any("≥10" in r for r in reasons), f"옛 코드 상수를 말한다: {reasons[:2]}"


def test_실행_재검증_창도_같은_DB값을_읽는다(db):
    """★GATE ⑥ — 판정은 DB 창, 실행 재검증만 코드 창이면 «재사용»의 목적이 깨진다."""
    from app.services.naver_ad import naver_execution_harness as harness

    db.add(NaverSearchTermDaily(
        ad_date=date(2026, 7, 10), campaign_id="cmp1", adgroup_id="grp-web",
        search_term="창밖전환", source="shopping", imp=1, clk=1, cost=1, rank_sum=0,
        conv_purchase_cnt=5, conv_direct_cnt=0, conv_purchase_amt=0, cart_cnt=0, cart_amt=0,
    ))
    db.commit()

    # 대조군 먼저 — DB값이 없으면 코드 창 14일(from=2026-07-09)이라 07-10 행이 «안»에 든다.
    conv, window = harness._search_term_conversions_in_window(db, "grp-web", "창밖전환", _NOW)
    assert (conv, window) == (5, 14)

    # DB가 창을 7일로 좁히면(from=2026-07-16) 같은 행이 창 «밖»이 된다.
    _params(db, ss_window_days=7)
    conv, window = harness._search_term_conversions_in_window(db, "grp-web", "창밖전환", _NOW)
    assert window == 7, "실행 재검증이 코드 상수 14를 그대로 쓴다"
    assert conv == 0


def test_SPECS_인구조사_현재_7종(db):
    """키가 늘면 이 줄이 빨개진다 — 「무엇이 학습 가능한가」가 조용히 안 바뀌도록."""
    assert set(guardrail_params.SPECS) == {
        "cooldown_hours", "max_daily_auto_bid_downs", "max_auto_up_multiple",
        "pl_min_click", "pl_window_days",
        "ss_min_click", "ss_window_days",
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


def test_두_창_파라미터_모두_승격됐고_재료가_같은_응답에서_관측된다(db):
    """★D-NAO-262 시절 이 테스트는 「SS 창은 SPECS에 없다(분산 보류)」를 재고 있었다. D-NAO-265가
    소비처를 배선해 승격했으므로 **전제가 바뀌었다** — 계약 §4-B④ⓑ대로 목표(«재료가 봉투 상한을
    덮는가»)는 그대로 두고 구현만 사실에 맞춘다. 「승격 대기 중인 축이 있다」는 사실을 재던 자리가
    이제 「둘 다 승격됐다」를 잰다.

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
    assert ss[0]["param_key"] == "ss_window_days"
    assert ss[0]["promoted"] is True
    # 두 축이 같은 응답에서 나란히 관측된다 — 「재료 있음」과 「봉투 있음」이 한 필드를 다투지 않는다
    assert _cov(db, "pl_window_days")["promoted"] is True
    assert _cov(db, "ss_window_days")["ceiling_days"] == 16, "봉투 상한을 실제로 재야 한다"


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
