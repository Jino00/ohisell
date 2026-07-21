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

from app.models import NaverProductBep, NaverSearchTermDaily
from app.services.naver_ad import intraday_roas
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
      exclude_candidates.powerlink  — source='expkeyword'. **전환 귀속 불가(§난제 5)라 전환게이트를
        걸 수 없음** → confirm_required=True 마킹(자동 발사 대상 아님, SS3-A 초기 Confirm 전용).
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
    powerlink: list[dict] = []
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
        if source == _POWERLINK_SOURCE:
            # 파워링크는 전환 귀속 불가(§난제 5) — 전환게이트 없이 성과만으로 판정 = 위험 →
            # 자동 발사 대상 아님(Confirm 전용) 명시 마킹.
            cand["confirm_required"] = True
            powerlink.append(cand)
        else:
            shopping.append(cand)

    def _sort(xs: list[dict]) -> list[dict]:
        return sorted(xs, key=lambda c: (-c["cost"], c["search_term"]))

    return {
        "window": {"from": window_from.isoformat(), "to": as_of.isoformat(), "days": window_days},
        "exclude_candidates": {"shopping": _sort(shopping), "powerlink": _sort(powerlink)},
        "promote_candidates": _sort(promote),
    }
