# search_term_judge.py — 검색어 ROAS 판단 SA (SS2, docs/PLAN_naver-ad-searchterm-ss.md §3)
# 역할(순수 SA·read-only): naver_search_term_daily의 rolling N일 검색어 grain 누적 집계 →
#   안전 봉투 §1 게이트(전환 보호·표본 게이트·화이트리스트)를 순차 적용해 "클릭당 확정 손해"
#   검색어 제외 후보(source별)와 승격 후보(SS4 원료)를 산출한다. DB 쓰기 0·네이버 API 호출 0.
#   목적함수(D-NAO-1/59 총이익 극대화): 제외 = 낭비 비용 회수(손실 검색어 절단), ROAS 최대화 아님.
#
#   ★import 정책(exploration.py 관례): models + 최말단 순수 SA(campaign_target_resolver·
#     intraday_roas — 둘 다 models만 의존)만 import한다. auto_operator/harness는 import하지
#     않는다(SS3 harness가 이 모듈의 상수를 참조하므로 역방향 import는 순환).
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import (
    NaverAdDaily,
    NaverAdgroupProduct,
    NaverCampaignSettings,
    NaverEntity,
    NaverProductBep,
    NaverSearchTermDaily,
)
from app.services.naver_ad import campaign_target_resolver, intraday_roas, semantic_units
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.utils.kst import kst_now

# ── 제안 유형·승인원 상수(SA 소유 — exploration.py의 APPROVAL_SOURCE_EXPLORE 관례) ──
# proposal_type: SS3 브리핑/실행 배선이 생성·디스패치에 쓴다. String(24) 적합(19자).
SEARCH_TERM_EXCLUDE_TYPE = "search_term_exclude"
# 자동 실쓰기 승인원 — **코드에 정의하되 이 스프린트에선 비활성**(PLAN §3 SS3-A·§실측 5).
# 어디에서도 자동 승인(status='approved' + approval_source=이 값)을 배선하지 않는다. 활성화는
# 라이브 실측 후 Jino 승인 대상. harness 킬스위치 화이트리스트에는 미리 등록해(explore_op 관례)
# 미래 활성화 시점에 킬스위치 존중이 이미 보장되게 한다. String(12) 적합(10자).
APPROVAL_SOURCE_SS_EXCLUDE = "ss_exclude"

# SS4(PLAN §3 SS4, 전략 v2 로드맵 3번) — 전환 검색어 승격 제안(정식 키워드 등록 후보). 값의
# 단일 진실은 여기(search_term_ss_lane이 참조). **제안만·영구 Confirm** — 등록 쓰기 손 자체는
# L3 스코프라 실행자를 만들지 않는다. naver_execution_harness._ACTION_BY_PROPOSAL_TYPE에
# 매핑이 없어 실행 요청되면 ActionNotExecutableError로 자연히 fail-closed 거부된다(실행자
# 미구현이 안전장치 그 자체 — SS3처럼 별도 SHOPPING 명시 거부 코드가 필요 없음).
# String(24) 적합(20자).
SEARCH_TERM_PROMOTE_TYPE = "search_term_promote"

# M2-c ⓒ/ⓔ(D-NAO-191·219) — 의미 단위(사전 최장일치로 조립된 합성 구) 제외 후보의 target_type.
# 실제 관측된 검색어(target_type="search_term")와 다른 값을 쓰는 이유: harness의 구조 가드
# (_execute_search_term_exclude·_execution_blocker 둘 다 "target_type != 'search_term'"이면
# fail-closed 거부/정보성 표시)가 이 값을 만나면 **콘솔 Confirm으로도 실행되지 않는다.** 의미
# 단위는 사전 최장일치로 조립된 합성 구라 실제 검색어 텍스트가 아니고, SHOPPING API가 그걸 어떤
# type(exact=1 추정/phrase=2 추정, naver_sa_writer._SHOPPING_RESTRICT_TYPE 참조)으로 받아야
# 의도대로 동작하는지 아직 실증되지 않았다(NGRAM_GRAIN_MEASUREMENT_20260818.md §6). 그래서 이
# target_type은 "쓸 수 있게 태어난 제안"이 아니라 "사람이 검토 후 콘솔 밖에서 수동 등록하는
# 배출구"로 의도적으로 비실행 상태에 둔다 — 등록 표현이 검증되면 그때 별도 슬라이스가 연다.
SEARCH_TERM_EXCLUDE_SEMANTIC_TARGET_TYPE = "search_term_semantic"

# ── 안전 봉투 §1 상수(PLAN §1) — 초기값은 가설, SS1 실분포로 캘리브레이션(§실측 2) ──
_SS_WINDOW_DAYS = 14   # rolling 창(§난제 2 — 저볼륨 롱테일 표본 누적, 보존 16일 내)
_SS_MIN_CLICK = 10     # 최소 클릭 표본 게이트(§1 2, D-NAO-70 핫셋 게이트와 동일 값)

# ── 핵심어 화이트리스트(§1 3 오컷 방지) ──
# ★Jino 확정 대상(PLAN §1 3 "product_master 매핑 기반 + Jino 확정"): 아래 하드코딩은 상품/
#   브랜드 핵심 토큰의 **보수적 기본값**이다. 이 검색어를 포함하면 어떤 성과여도 제외 후보에서
#   빠진다(오컷 방지 우선). 실효 화이트리스트 = 이 상수 ∪ naver_product_bep 매핑 상품명 토큰.
_SS_WHITELIST_TOKENS: tuple[str, ...] = (
    "아이폰", "아이패드", "맥세이프", "강화유리", "지문방지", "보호필름",
)

# 상품명 토큰화 — 한글/영숫자 경계로 분리, 길이<2·순수숫자 토큰은 과광폭 매칭 방지로 버린다.
_TOKEN_SPLIT_RE = re.compile(r"[^0-9A-Za-z가-힣]+")

_SHOPPING_SOURCE = "shopping"
_POWERLINK_SOURCE = "expkeyword"

# ── PX1: 파워링크 전용 게이트 상수(PLAN_naver-ad-powerlink-autoexclude.md §1) ──
# 파워링크(WEB_SITE, source='expkeyword')는 전환 귀속 불가(§0.5)라 쇼핑과 다른 봉투로 판정한다.
# 쇼핑 게이트(_SS_WINDOW_DAYS 14·_SS_MIN_CLICK 10·margin fail-closed)는 그대로 두고(회귀 0),
# 파워링크만 아래 상수로 분리 — 저볼륨 롱테일 표본 누적(30일)·낮은 클릭 표본(5)·margin 폴백.
_PL_WINDOW_DAYS = 30            # rolling 창(§1 1 — 실측: 14d 최대 clk=5라 30d로 표본 확보, 보존 365일)
_PL_MIN_CLICK = 5              # 최소 클릭 표본(§1 2 — 쇼핑 10과 분리)
_PL_FALLBACK_MIN_COST = 10000  # margin 미확인 그룹의 최소 비용 하한(§1 3 — 확정원가 공헌이익 ~10,500 정렬)
# 스코프 밖(대행사) 파워링크 고비용 검색어 브리핑 게이트(§4 2·관찰 전용, 실쓰기 0).
_PL_AGENCY_MIN_COST = 30000
_PL_AGENCY_MIN_CLICK = 10

# 디바이스/브랜드 토큰 스톱리스트(§1 5) — 확장검색어는 거의 전부 디바이스명을 포함해 브랜드
# substring 보호가 사문화된다(실측 2). 이 토큰들은 파워링크 화이트리스트 보호 집합에서 뺀다
# (제품형 토큰 보호는 유지). casefold 저장(_is_whitelisted 짝 — 검색어도 casefold 비교).
_PL_DEVICE_TOKENS: frozenset[str] = frozenset(
    tok.casefold() for tok in ("아이폰", "아이패드", "갤럭시", "맥세이프", "삼성", "애플")
)


def _build_whitelist(db: Session) -> set[str]:
    """실효 화이트리스트 토큰 = 하드코딩 보수 목록 ∪ naver_product_bep(has_cost) 상품명 토큰.

    상품명은 공백/기호로 토큰화하고 길이<2·순수숫자 토큰은 버린다("15" 같은 토큰이 무관 검색어를
    광범위 보호해 후보를 과하게 지우는 것 방지). 검색어가 어떤 토큰이라도 **부분문자열로** 포함하면
    보호(한글은 띄어쓰기 없는 합성이 흔해 substring이 token-split보다 견고).

    ★대소문자 정규화(casefold): 토큰을 저장 시점에 casefold해 둔다 — 영문 브랜드 토큰
    (예: product_bep 상품명의 "iPhone")이 검색어의 표기 편차("iphone", "IPHONE")에서도
    보호되도록 `_is_whitelisted`와 짝을 이룬다(양쪽 다 casefold해야 매칭됨)."""
    tokens: set[str] = {tok.casefold() for tok in _SS_WHITELIST_TOKENS}
    rows = db.query(NaverProductBep.product_name).filter(
        NaverProductBep.has_cost.is_(True),
    ).all()
    for (name,) in rows:
        if not name:
            continue
        for tok in _TOKEN_SPLIT_RE.split(name):
            if len(tok) >= 2 and not tok.isdigit():
                tokens.add(tok.casefold())
    return tokens


def _is_whitelisted(search_term: str, tokens: set[str]) -> bool:
    """검색어가 화이트리스트 토큰을 하나라도 부분문자열로 포함하면 보호(True). 검색어도
    casefold해 비교한다(`_build_whitelist`가 토큰을 이미 casefold해 저장 — 영문 브랜드 토큰의
    대소문자 편차 갭 제거, 한글은 casefold가 항등이라 기존 동작 불변)."""
    if not search_term:
        return False
    normalized = search_term.casefold()
    return any(tok in normalized for tok in tokens)


def _min_cost_for_adgroup(db: Session, adgroup_id: str, cache: dict[str, Decimal | None]) -> Decimal | None:
    """표본 게이트의 최소 비용 하한(§1 2 "cost ≥ product_bep 공헌이익 × 1") = 그룹 매핑 상품의
    매출가중 공헌이익(intraday_roas.adgroup_unit_price 재사용 — 단일 진실 소스). 매핑/원가
    미확인이면 None(→ fail-closed로 후보 제외). 그룹당 1회 조회 후 캐시."""
    if adgroup_id in cache:
        return cache[adgroup_id]
    margin = intraday_roas.adgroup_unit_price(db, adgroup_id).get("margin")
    cache[adgroup_id] = margin
    return margin


def _reason(clk: int, cost: int, min_cost: Decimal, window_from: date, window_to: date) -> str:
    return (
        f"[검색어제외] 손실 검색어(전환0·낭비비용 회수) — rolling {window_from}~{window_to}: "
        f"clk={clk}(≥{_SS_MIN_CLICK}), cost={cost}원(≥공헌이익 {int(min_cost)}원), "
        f"purchase 전환=0. target ROAS 미달·매출 미증 = 클릭당 확정 손해(PLAN §1 2)."
    )


def judge_search_terms(
    db: Session, *, now: datetime | None = None, window_days: int = _SS_WINDOW_DAYS,
) -> dict:
    """검색어 grain rolling 누적 집계 → 봉투 §1 게이트 → 제외/승격 후보 산출(순수·read-only).

    게이트 순서(§1, fail-closed):
      ① 전환 보호(§1 1): conv_purchase_cnt(직+간 합)≥1 검색어는 제외 후보 진입 불가(살아있는 증거).
      ② 표본 게이트(§1 2): clk≥_SS_MIN_CLICK ∧ conv_purchase_cnt==0 ∧ cost≥공헌이익. 공헌이익
         부재(원가 미확인 그룹)면 후보 제외(fail-closed — 표본 비용 하한을 못 세우면 자르지 않는다).
      ③ 화이트리스트(§1 3): 상품 핵심 토큰 포함 검색어는 보호(오컷 방지).

    산출:
      exclude_candidates.shopping   — source='shopping'(SS3-B 브리핑용, API 제외 불가 §실측-0).
      exclude_candidates.powerlink  — source='expkeyword'. PX1: _judge_powerlink(30일 전용 게이트,
        §1)가 스코프(auto_operate)·그룹 순손실·디바이스 토큰 제외 화이트리스트로 별도 판정한 자동
        발사 후보(비용 기반, 전환 귀속 불가라 전환게이트 대신 그룹 순손실 프록시로 판정).
      agency_powerlink              — 스코프 밖(비 auto_operate=대행사) 파워링크 고비용 검색어
        (30d cost≥30,000 ∧ clk≥10). 실쓰기 없음·브리핑 전용(§4 2 관찰 산출물).
      promote_candidates            — conv_direct_cnt≥1(직접전환 품질) 검색어. SS4 원료(산출까지만).

    반환 dict의 각 후보: campaign_id·adgroup_id·search_term·source·근거 수치·reason. 결정적 정렬
    (cost 내림차순, 동률 search_term 오름차순)."""
    now = now or kst_now()
    as_of = now.date()
    window_from = as_of - timedelta(days=window_days - 1)

    rows = (
        db.query(
            NaverSearchTermDaily.source,
            NaverSearchTermDaily.campaign_id,
            NaverSearchTermDaily.adgroup_id,
            NaverSearchTermDaily.search_term,
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.imp), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.clk), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.cost), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.conv_purchase_cnt), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.conv_direct_cnt), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.conv_purchase_amt), 0),
        )
        .filter(
            NaverSearchTermDaily.ad_date >= window_from,
            NaverSearchTermDaily.ad_date <= as_of,
        )
        .group_by(
            NaverSearchTermDaily.source,
            NaverSearchTermDaily.campaign_id,
            NaverSearchTermDaily.adgroup_id,
            NaverSearchTermDaily.search_term,
        )
        .all()
    )

    whitelist = _build_whitelist(db)
    min_cost_cache: dict[str, Decimal | None] = {}

    shopping: list[dict] = []
    promote: list[dict] = []

    for source, campaign_id, adgroup_id, term, imp, clk, cost, pconv, dconv, pamt in rows:
        imp, clk, cost = int(imp), int(clk), int(cost)
        pconv, dconv, pamt = int(pconv), int(dconv), int(pamt)

        # 승격 후보(SS4 원료) — 직접전환 발생 검색어. 제외 게이트와 독립(전환 있으면 살아있는 증거).
        if dconv >= 1:
            promote.append({
                "campaign_id": campaign_id, "adgroup_id": adgroup_id, "search_term": term,
                "source": source, "clk": clk, "cost": cost, "imp": imp,
                "conv_direct_cnt": dconv, "conv_purchase_cnt": pconv, "conv_purchase_amt": pamt,
                "reason": f"[검색어승격] 직접전환 {dconv}건 발생 — 정식 키워드 등록 후보(SS4, 영구 Confirm)",
            })

        # ① 전환 보호(§1 1) — purchase 전환(직+간)≥1이면 제외 후보 진입 불가(fail-closed).
        if pconv >= 1:
            continue
        # ③ 화이트리스트(§1 3) — 상품 핵심어 포함 검색어 보호(오컷 방지).
        if _is_whitelisted(term, whitelist):
            continue
        # ② 표본 게이트(§1 2) — clk·cost 하한. 공헌이익 부재면 fail-closed(자르지 않음).
        if clk < _SS_MIN_CLICK:
            continue
        min_cost = _min_cost_for_adgroup(db, adgroup_id, min_cost_cache)
        if min_cost is None or min_cost <= 0:
            continue  # fail-closed — 표본 비용 하한 근거 없음(원가 미확인 그룹)
        if cost < min_cost:
            continue

        cand = {
            "campaign_id": campaign_id, "adgroup_id": adgroup_id, "search_term": term,
            "source": source, "clk": clk, "cost": cost, "imp": imp,
            "conv_purchase_cnt": pconv, "min_cost": int(min_cost),
            "window_from": window_from.isoformat(), "window_to": as_of.isoformat(),
            "reason": _reason(clk, cost, min_cost, window_from, as_of),
        }
        # PX1: 파워링크(source='expkeyword') 제외 판정은 이 14일 루프에서 분리한다 —
        # _judge_powerlink(30일 전용 게이트, §1)가 스코프(auto_operate)·그룹 순손실·디바이스
        # 토큰 제외 화이트리스트로 별도 판정한다. 여기 도달한 파워링크 행은 버린다(쇼핑 경로
        # byte-동일 유지 — 이 continue 위 전 로직·shopping.append는 종전과 완전 동일).
        if source == _POWERLINK_SOURCE:
            continue
        shopping.append(cand)

    def _sort(xs: list[dict]) -> list[dict]:
        return sorted(xs, key=lambda c: (-c["cost"], c["search_term"]))

    powerlink = _judge_powerlink(db, now=now, as_of=as_of)

    # M2-c ⓒ-3: 의미 단위 판정은 **병렬 산출**이다 — 위 개별 grain 판정을 대체하지 않는다(기존
    # 4개 키는 이 줄 이전 로직과 byte-동일 유지). 같은 now·window_days로 같은 rolling 창을 재사용.
    semantic = judge_semantic_units(db, now=now, window_days=window_days)

    return {
        "window": {"from": window_from.isoformat(), "to": as_of.isoformat(), "days": window_days},
        "exclude_candidates": {"shopping": _sort(shopping), "powerlink": powerlink["powerlink"]},
        "agency_powerlink": powerlink["agency_powerlink"],
        "promote_candidates": _sort(promote),
        "semantic_units": semantic,
    }


# ══════════════════════════════════════════════════════════════════
# M2-c ⓒ-2 — 의미 단위(사전 최장일치) 풀링 판정. 개별 grain(GROUP BY search_term)의 클릭 희소성을
# 우회한다(D-NAO-191 실측: 검색어의 97.4%가 토큰 1개 → 단어 n-gram은 산출 0). 순수·read-only.
# ══════════════════════════════════════════════════════════════════
def _semantic_reason(
    kind: str, key: str, clk: int, cost: int, min_cost: Decimal,
    member_terms: int, members_passing: int, window_from: date, window_to: date,
) -> str:
    unlocked = members_passing == 0
    unlocked_txt = (
        "개별 grain에선 표본 게이트를 통과하는 멤버가 0건 — 풀링으로 신규 확보(unlocked_by_pooling)"
        if unlocked else
        f"개별 grain에서 이미 게이트를 통과한 멤버 {members_passing}건 포함"
    )
    return (
        f"[검색어제외 의미단위·{kind}] 「{key}」 — rolling {window_from}~{window_to}: "
        f"clk={clk}(≥{_SS_MIN_CLICK}), cost={cost}원(≥공헌이익 {int(min_cost)}원), "
        f"묶인 검색어={member_terms}건. {unlocked_txt}. target ROAS 미달·매출 미증 = 클릭당 확정 "
        "손해(PLAN §1 2, 의미단위 풀링 적용 — D-NAO-191·219)."
    )


def judge_semantic_units(
    db: Session, *, now: datetime | None = None, window_days: int = _SS_WINDOW_DAYS,
) -> dict:
    """쇼핑 검색어를 의미 단위(사전 최장일치, semantic_units.py)로 풀링해 개별 grain 클릭
    희소성을 우회한다(M2-c ⓒ, D-NAO-191 실측 이식 — NGRAM_GRAIN_MEASUREMENT_20260818.md). 개별
    grain 판정(위 judge_search_terms)의 **병렬 산출**이지 대체가 아니다. 순수·read-only.

    원료: judge_search_terms와 같은 rolling 창(window_days)·같은 테이블(NaverSearchTermDaily).
    대상: source='shopping' 且 conv_purchase_cnt(창 합산)==0 — §1-1 전환 보호를 그대로 상속한다
    (전환이 붙은 검색어는 집계 대상에도 들어가지 않는다 — 살아있는 증거는 애초에 후보가 아니다).

    검색어 정규화: casefold() 후 공백 제거(프로토타입과 동일 — 붙여 쓴 한국어 검색어 전제).

    집계 3종(각 (adgroup_id, 키) grain, 같은 검색어 안 같은 단위 중복 등장은 1회만 센다):
      ① 의미단위 단일 ② 의미단위 쌍(인접 zip, key="a+b") ③ 잔여(사전 밖, len>=2).

    게이트(기존 상수만 재사용, fail-closed — 신규 문턱 없음): 집계 clk≥_SS_MIN_CLICK 且 집계
    cost≥_min_cost_for_adgroup(그룹 공헌이익). min_cost가 None/<=0이면 제외(원가 미확인 그룹은
    자르지 않는다). ★화이트리스트는 이 grain에 아직 적용하지 않는다(NGRAM_GRAIN_MEASUREMENT
    §6-1 [미상] — 적용 방식이 결정되지 않은 채로 남아 있고, 이 함수는 그 미결정을 판단으로
    메우지 않는다. 완료 QA·다음 슬라이스가 볼 것).

    각 생존 항목: adgroup_id·campaign_id·kind(unit/pair/residual)·key(내부 집계 키)·phrase(등록
    표현용 텍스트 — 쌍은 공백 조인)·clk·cost·min_cost·window_from/to·member_terms(묶인 검색어
    수)·members_passing_individual_gate(그중 개별 grain에서 clk≥_SS_MIN_CLICK을 통과하는 멤버
    수)·unlocked_by_pooling(members_passing_individual_gate==0 — 개별 grain에선 판정 불가였는데
    풀링으로 새로 확보된 후보라는 뜻)·member_sample(예시 검색어 최대 8개, 정렬)·reason."""
    now = now or kst_now()
    as_of = now.date()
    window_from = as_of - timedelta(days=window_days - 1)

    rows = (
        db.query(
            NaverSearchTermDaily.campaign_id,
            NaverSearchTermDaily.adgroup_id,
            NaverSearchTermDaily.search_term,
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.clk), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.cost), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.conv_purchase_cnt), 0),
        )
        .filter(
            NaverSearchTermDaily.source == _SHOPPING_SOURCE,
            NaverSearchTermDaily.ad_date >= window_from,
            NaverSearchTermDaily.ad_date <= as_of,
        )
        .group_by(
            NaverSearchTermDaily.campaign_id,
            NaverSearchTermDaily.adgroup_id,
            NaverSearchTermDaily.search_term,
        )
        .all()
    )

    vocab = semantic_units.build_vocab(db)
    index = semantic_units.build_index(vocab)

    # agg[kind][(adgroup_id, key)] = {"campaign_id","clk","cost","member_terms":set,"members_passing"}
    agg: dict[str, dict[tuple[str, str], dict]] = {"unit": {}, "pair": {}, "residual": {}}

    def _bump(kind: str, adgroup_id: str, campaign_id: str, key: str, term: str,
              clk: int, cost: int, individual_pass: bool) -> None:
        bucket = agg[kind].setdefault(
            (adgroup_id, key),
            {"campaign_id": campaign_id, "clk": 0, "cost": 0, "member_terms": set(), "members_passing": 0},
        )
        # ★같은 검색어가 같은 키에 여러 번 기여해도(예: 인접 쌍이 한 검색어 안에서 반복) 1회만
        # 센다 — "묶인 검색어 수"가 실제 검색어 종류 수를 뜻해야 하기 때문(프로토타입은 unit·
        # residual만 set()을 쓰고 pair는 안 썼으나, 이 규칙을 3종에 균일 적용한다 — 판단 근거는
        # 구현 보고에 명시).
        if term in bucket["member_terms"]:
            return
        bucket["member_terms"].add(term)
        bucket["clk"] += clk
        bucket["cost"] += cost
        if individual_pass:
            bucket["members_passing"] += 1

    for campaign_id, adgroup_id, term, clk, cost, pconv in rows:
        clk, cost, pconv = int(clk), int(cost), int(pconv)
        if pconv >= 1:
            continue  # §1-1 전환 보호 상속 — 살아있는 증거는 집계 대상도 아니다
        norm = (term or "").casefold().replace(" ", "")
        if not norm:
            continue
        units, resid = semantic_units.segment_indexed(norm, index)
        individual_pass = clk >= _SS_MIN_CLICK  # 이 행 자체가 개별 grain 값(같은 창·같은 표)

        for u in set(units):
            _bump("unit", adgroup_id, campaign_id, u, term, clk, cost, individual_pass)
        for a, b in zip(units, units[1:]):
            _bump("pair", adgroup_id, campaign_id, f"{a}+{b}", term, clk, cost, individual_pass)
        for r in set(resid):
            if len(r) >= 2:
                _bump("residual", adgroup_id, campaign_id, r, term, clk, cost, individual_pass)

    min_cost_cache: dict[str, Decimal | None] = {}

    def _finalize(kind: str) -> list[dict]:
        out: list[dict] = []
        for (adgroup_id, key), v in agg[kind].items():
            clk, cost = v["clk"], v["cost"]
            if clk < _SS_MIN_CLICK:
                continue
            min_cost = _min_cost_for_adgroup(db, adgroup_id, min_cost_cache)
            if min_cost is None or min_cost <= 0:
                continue  # fail-closed — 원가 미확인 그룹은 자르지 않는다
            if cost < min_cost:
                continue
            member_terms = len(v["member_terms"])
            members_passing = v["members_passing"]
            phrase = key.replace("+", " ") if kind == "pair" else key
            out.append({
                "adgroup_id": adgroup_id, "campaign_id": v["campaign_id"],
                "kind": kind, "key": key, "phrase": phrase,
                "clk": clk, "cost": cost, "min_cost": int(min_cost),
                "window_from": window_from.isoformat(), "window_to": as_of.isoformat(),
                "member_terms": member_terms,
                "members_passing_individual_gate": members_passing,
                "unlocked_by_pooling": members_passing == 0,
                "member_sample": sorted(v["member_terms"])[:8],
                "reason": _semantic_reason(
                    kind, key, clk, cost, min_cost, member_terms, members_passing, window_from, as_of,
                ),
            })
        return sorted(out, key=lambda c: (-c["cost"], c["key"]))

    return {
        "window": {"from": window_from.isoformat(), "to": as_of.isoformat(), "days": window_days},
        "vocab_size": len(vocab),
        "units": _finalize("unit"),
        "pairs": _finalize("pair"),
        "residual": _finalize("residual"),
    }


# ══════════════════════════════════════════════════════════════════
# PX1 — 파워링크 전용 게이트(§1, 쇼핑과 분리, fail-closed)
# ══════════════════════════════════════════════════════════════════
def _pl_min_cost(db: Session, adgroup_id: str, cache: dict[str, Decimal | None]) -> Decimal:
    """§1 3 최소 비용 하한 = 그룹 공헌이익(margin), margin 미확인 시 폴백(_PL_FALLBACK_MIN_COST).
    쇼핑(_min_cost_for_adgroup, fail-closed)과 달리 파워링크는 폴백으로 게이트를 세운다(§실측 3
    해소 — 아이패드 상품군 원가 미확정이라 margin unavailable이 게이트를 사문화하던 문제).
    ★게이트 3이 margin 우선이므로 원가 확정 시 자동으로 실측 margin으로 전환된다(§체크리스트)."""
    margin = _min_cost_for_adgroup(db, adgroup_id, cache)  # 쇼핑과 캐시 공유(그룹당 1회)
    if margin is None or margin <= 0:
        return Decimal(_PL_FALLBACK_MIN_COST)
    return margin


def _pl_group_net_loss(
    db: Session, adgroup_id: str, window_from: date, as_of: date, cache: dict[str, bool],
) -> bool:
    """§1 4 그룹 순손실 프록시 — 그 그룹의 naver_ad_daily 30d (직+간 전환매출)/cost < 그룹 target
    ROAS ∧ cost>0이면 True(순손실 = 그 그룹 확장검색어는 집합적으로 손해). sentinel(__backfill__)·
    빈 그룹은 집계에서 제외(2배 함정 교훈). 판정 불가(cost 0·target 부재)는 fail-closed(False=
    자르지 않음 — 표본/근거 없으면 오컷 방지). 그룹당 1회 조회 후 캐시."""
    if adgroup_id in cache:
        return cache[adgroup_id]
    row = (
        db.query(
            sqlfunc.coalesce(
                sqlfunc.sum(NaverAdDaily.conv_direct_amt + NaverAdDaily.conv_indirect_amt), 0
            ),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
        )
        .filter(
            NaverAdDaily.adgroup_id == adgroup_id,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
            NaverAdDaily.adgroup_id != "",
            NaverAdDaily.ad_date >= window_from,
            NaverAdDaily.ad_date <= as_of,
        )
        .first()
    )
    conv_amt = int(row[0] or 0)
    gcost = int(row[1] or 0)
    result = False
    if gcost > 0:
        target = campaign_target_resolver.resolve_adgroup_target_roas(db, adgroup_id)["target_roas"]
        if target is not None:
            result = (Decimal(conv_amt) / Decimal(gcost)) < Decimal(str(target))
    cache[adgroup_id] = result
    return result


def _pl_protect_tokens(
    db: Session, adgroup_id: str, global_form_tokens: set[str], cache: dict[str, set[str]],
) -> set[str]:
    """§1 5 파워링크 보호 토큰 = (그룹 매핑 상품명 제품형 토큰 ∪ **그룹명 제품형 토큰** ∪ 전역
    제품형 토큰) − 디바이스 토큰. global_form_tokens는 이미 (전역 화이트리스트 − 디바이스) casefold
    집합. 그룹 매핑 상품 부재면 global_form_tokens + 그룹명 토큰(§1 5 폴백). 그룹당 1회 조회 후
    캐시(casefold — _is_whitelisted 짝).

    ★그룹명 토큰(P2-2 GATE 오컷 방지): 상품명뿐 아니라 그 adgroup의 NaverEntity.name(그룹명)도
    제품형 의도를 담는다(예: P_Test `_종이질감`·`_6H BLC` 그룹은 상품명이 아니라 그룹명에만 종이질감
    의도가 있어, "아이패드종이질감필름" 검색어가 종이질감 그룹에서 미보호로 잘릴 수 있었다 — §0.5
    경고 케이스). 상품명과 동일한 토큰화(len≥2·비숫자·casefold·디바이스 토큰 제외)로 합산한다."""
    if adgroup_id in cache:
        return cache[adgroup_id]
    tokens = set(global_form_tokens)
    rows = (
        db.query(NaverProductBep.product_name)
        .join(
            NaverAdgroupProduct,
            NaverAdgroupProduct.mall_product_id == NaverProductBep.channel_product_id,
        )
        .filter(
            NaverAdgroupProduct.adgroup_id == adgroup_id,
            NaverProductBep.has_cost.is_(True),
        )
        .all()
    )
    # 그룹명(NaverEntity, entity_type='adgroup')을 상품명 토큰과 같은 소스로 합산 — 그룹당 1회.
    grp = (
        db.query(NaverEntity.name)
        .filter(
            NaverEntity.entity_type == "adgroup",
            NaverEntity.entity_id == adgroup_id,
        )
        .first()
    )
    names = [name for (name,) in rows]
    if grp is not None and grp[0]:
        names.append(grp[0])
    for name in names:
        if not name:
            continue
        for tok in _TOKEN_SPLIT_RE.split(name):
            cf = tok.casefold()
            # 디바이스/브랜드 토큰은 보호에서 제외(§1 5 스톱리스트) — 제품형 토큰만 남긴다.
            if len(tok) >= 2 and not tok.isdigit() and cf not in _PL_DEVICE_TOKENS:
                tokens.add(cf)
    cache[adgroup_id] = tokens
    return tokens


def _pl_reason(clk: int, cost: int, min_cost: Decimal, window_from: date, window_to: date) -> str:
    return (
        f"[검색어제외] 파워링크 낭비 검색어(전환귀속 불가·비용 기반) — rolling {window_from}~{window_to}: "
        f"clk={clk}(≥{_PL_MIN_CLICK}), cost={cost}원(≥{int(min_cost)}원), 그룹 순손실(BEP 미달)·"
        f"제품형 토큰 미포함 = 낭비비용 회수(PLAN §1). 자동 제외 후 in-out 재심사 루프가 자가 교정."
    )


def _judge_powerlink(db: Session, *, now: datetime, as_of: date) -> dict:
    """파워링크(source='expkeyword') 제외 판정 — 30일 전용 게이트(§1, fail-closed). 순수·read-only.

    스코프(⓪ auto_operate=1) 안 후보는 자동 발사 대상(powerlink), 밖(대행사)은 고비용 브리핑
    후보(agency_powerlink)로 가른다 — 대행사 캠페인엔 절대 실쓰기 없음(§0 3). 게이트 순서(전부
    fail-closed): ①창 30일 ②clk≥5 ③cost≥margin(폴백 10,000) ④그룹 순손실 프록시 ⑤제품형
    토큰 화이트리스트(디바이스 토큰 제외). 전환 보호(§1-1)는 파워링크에서 구조적 0이나 게이트 유지."""
    window_from = as_of - timedelta(days=_PL_WINDOW_DAYS - 1)
    rows = (
        db.query(
            NaverSearchTermDaily.campaign_id,
            NaverSearchTermDaily.adgroup_id,
            NaverSearchTermDaily.search_term,
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.clk), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.cost), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.conv_purchase_cnt), 0),
        )
        .filter(
            NaverSearchTermDaily.source == _POWERLINK_SOURCE,
            NaverSearchTermDaily.ad_date >= window_from,
            NaverSearchTermDaily.ad_date <= as_of,
        )
        .group_by(
            NaverSearchTermDaily.campaign_id,
            NaverSearchTermDaily.adgroup_id,
            NaverSearchTermDaily.search_term,
        )
        .all()
    )

    # ⓪ 스코프: campaign auto_operate=1(ours)만 자동 발사 대상(§0 3). WEB_SITE 함의는
    # source='expkeyword'가 이미 보장(파워링크 확장검색 버킷). 미등록 캠페인은 auto_operate=False.
    auto_map = {
        cid: bool(auto)
        for cid, auto in db.query(
            NaverCampaignSettings.campaign_id, NaverCampaignSettings.auto_operate,
        ).all()
    }
    # 전역 제품형 토큰 = 전역 화이트리스트 − 디바이스 토큰(§1 5, 그룹 매핑 부재 시 폴백).
    global_form = {tok.casefold() for tok in _SS_WHITELIST_TOKENS} - _PL_DEVICE_TOKENS
    margin_cache: dict[str, Decimal | None] = {}
    loss_cache: dict[str, bool] = {}
    protect_cache: dict[str, set[str]] = {}

    powerlink: list[dict] = []
    agency: list[dict] = []

    for campaign_id, adgroup_id, term, clk, cost, pconv in rows:
        clk, cost, pconv = int(clk), int(cost), int(pconv)
        # sentinel/빈 그룹은 그룹 grain 판정·실쓰기 불가 — 양쪽 경로 모두 fail-closed 제외.
        if adgroup_id in ("", BACKFILL_SENTINEL_ADGROUP):
            continue

        if not auto_map.get(campaign_id, False):
            # ⓪ 스코프 밖(대행사) — 실쓰기 절대 없음(§0 3). 고비용만 브리핑(§4 2, 관찰 산출물).
            if cost >= _PL_AGENCY_MIN_COST and clk >= _PL_AGENCY_MIN_CLICK:
                agency.append({
                    "campaign_id": campaign_id, "adgroup_id": adgroup_id, "search_term": term,
                    "source": _POWERLINK_SOURCE, "clk": clk, "cost": cost,
                    "window_from": window_from.isoformat(), "window_to": as_of.isoformat(),
                    "reason": (
                        f"[파워링크 대행사 고비용] {window_from}~{as_of} cost={cost}원"
                        f"(≥{_PL_AGENCY_MIN_COST})·clk={clk}(≥{_PL_AGENCY_MIN_CLICK}) — 대행사 "
                        "캠페인이라 자동 제외 없음, Jino 전달 검토 대상(§4 2)"
                    ),
                })
            continue

        # 스코프 안(auto_operate=1) — §1 게이트 전부 fail-closed
        if pconv >= 1:
            continue  # §1-1 전환 보호(파워링크는 구조적 0이지만 게이트 유지 — 데이터 생기면 자동 보호)
        if clk < _PL_MIN_CLICK:
            continue  # ② 최소 클릭
        min_cost = _pl_min_cost(db, adgroup_id, margin_cache)  # ③ margin(폴백 10,000)
        if cost < min_cost:
            continue
        if not _pl_group_net_loss(db, adgroup_id, window_from, as_of, loss_cache):
            continue  # ④ 그룹 순손실 프록시(BEP 이상 그룹은 안 자름 — 오컷 방지 1차)
        protect = _pl_protect_tokens(db, adgroup_id, global_form, protect_cache)
        if _is_whitelisted(term, protect):
            continue  # ⑤ 제품형 토큰 보호(디바이스 토큰 제외 — 오컷 방지 2차)

        powerlink.append({
            "campaign_id": campaign_id, "adgroup_id": adgroup_id, "search_term": term,
            "source": _POWERLINK_SOURCE, "clk": clk, "cost": cost,
            "conv_purchase_cnt": pconv, "min_cost": int(min_cost),
            "window_from": window_from.isoformat(), "window_to": as_of.isoformat(),
            "reason": _pl_reason(clk, cost, min_cost, window_from, as_of),
        })

    def _sort(xs: list[dict]) -> list[dict]:
        return sorted(xs, key=lambda c: (-c["cost"], c["search_term"]))

    return {"powerlink": _sort(powerlink), "agency_powerlink": _sort(agency)}
