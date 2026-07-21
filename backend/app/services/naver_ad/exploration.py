# exploration.py — 저볼륨 그룹 탐색 UP 순수 SA (스프린트 B-X BX1, D-NAO-70·71)
# 역할(SA·순수함수): 핫셋 게이트(정착 7일 클릭≥10) 밖 = "죽는 캠페인" 경로에 놓인 저볼륨
#   SHOPPING 광고그룹을 능동 탐색 대상으로 골라내고(후보), 탐색을 발동할지 판정하며(트리거),
#   전일 탐색분 D+1 결과로 래더 다음 수(시작/한 단/중단/상한)를 정한다(래더 판정). 이 파일은
#   BX1 범위 = 순수 선정/판정만: 실쓰기(update_ad_bid UP·explore_op 승인원)는 BX2, 레인 배선·
#   dedup·다운스트림은 BX3 몫. ★행위 불변 — 어떤 레인/실행 경로도 이 모듈을 아직 호출하지 않는다.
#   ★import 정책: models + campaign_backfill(sentinel 상수·최말단)만 import한다. auto_operator를
#   import하지 않는다 — BX3에서 auto_operator가 이 모듈을 import(레인 배선)하므로 역방향 import는
#   순환을 만든다(bid_step_types R0와 동일 철학). 핫셋 클릭 게이트 상수는 로컬로 복제하고 출처를
#   명시하며(아래 _MIN_CLICK_FOR_EXPLORATION), 정착창 clk 집계는 auto_operator._settlement_agg의
#   adgroup grain 관례를 그대로 복제한다(테스트가 두 상수/집계의 정합을 별도로 고정한다).
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverEntity
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

# ── 핫셋 여집합 게이트(출처: auto_operator._MIN_CLICK_FOR_APPROVAL=10, D-NAO-48 조건②/§4-1) ──
# 상호배타 재현: 핫셋 = 정착 clk ≥ 이 값 / 탐색 후보 = 정착 clk < 이 값. auto_operator를 import하면
# BX3 배선에서 순환이 생기므로 값만 로컬 복제한다. test_exploration.py가
# exploration._MIN_CLICK_FOR_EXPLORATION == auto_operator._MIN_CLICK_FOR_APPROVAL 정합을 고정해
# 두 상수가 조용히 갈라지는 것을 차단한다.
_MIN_CLICK_FOR_EXPLORATION = 10

# 탐색 대상 캠페인 유형 — 쇼핑검색(CPC 과금)만. WEB_SITE(파워링크 — 만성 0.1% CTR 병리, PLAN §0.5)·
# BRAND_SEARCH 제외. 호출측(BX3 레인)이 1차로 거르지만 여기서도 방어적으로 재확인(fail-closed).
_EXPLORATION_CAMPAIGN_TYPE = "SHOPPING"

# ── 안전 봉투 상수(PLAN §1 D-NAO-71 개정판) — BX2/BX3가 소비, BX1은 정의만(값 고정) ──
# ★D-NAO-71(2026-07-21 16:25): 초안의 수량·빈도 캡 4종(±15% 스텝·그룹당 하루 1회·런당 캡·
#   일일 계정 상한)을 제거. "수량·빈도 상한은 이익/손실 방향 모두 기회비용" — 브레이크는 경제성·
#   관측 간격·손실 백스톱·킬스위치로만. 따라서 _EXPLORATION_RUN_CAP(런당 캡)은 삭제됐다.
# 가드1: 탐색 스텝 = +30%. guardrail_gate._MAX_CHANGE_PCT(0.15)보다 크다("15%는 순위 실이동에
#   너무 작음" — 목적은 순위가 실제로 오르내리는 크기). 30%>15%이므로 이 UP은 ±15% 변경폭 면제
#   (CHANGE_PCT_EXEMPT_TYPES) 대상 타입으로 발사돼야 한다(그 배선·타입 등록은 BX2).
_EXPLORATION_STEP_PCT = Decimal("0.30")
# 가드2: 경제성 상한 = **주(유일) 가격 브레이크**(D-NAO-71로 수량 캡이 사라져 이 상한이 유일한
#   가격 브레이크가 됨). 정식 상한 = product_bep(공헌이익×기대 전환가치) 연동이며 BX2에서 구현한다.
#   아래 배수(현 입찰×2)는 **임시 병행 휴리스틱 캡**일 뿐 — product_bep 연동으로 대체·병행된다.
_EXPLORATION_CEILING_MULT = Decimal("2.0")
# 가드3: 쿨다운 2h = 탐색 사이클(같은 그룹 재조정 최소 간격 — "하루 1회" 대체). 값 출처=
#   guardrail_gate._COOLDOWN_HOURS(D-NAO-19). exploration_trigger가 last_step_at과 대조해 게이트.
_EXPLORATION_COOLDOWN_HOURS = 2


def _settlement_clk(db: Session, adgroup_id: str, date_from, date_to) -> int:
    """정착창 [date_from, date_to] 내 광고그룹 clk 합 —
    auto_operator._settlement_agg의 adgroup grain 집계(clk 파트)를 그대로 복제한다.
    (naver_ad_daily 확정치·backfill sentinel 행 제외 관례 동일. clk 하나만 필요하므로
    cost/conv는 집계하지 않는다 — 후보 게이트는 표본(clk)만 본다.)"""
    (clk,) = (
        db.query(sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0))
        .filter(
            NaverAdDaily.ad_date >= date_from,
            NaverAdDaily.ad_date <= date_to,
            NaverAdDaily.adgroup_id == adgroup_id,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .one()
    )
    return int(clk)


def exploration_candidates(
    db: Session, campaign_id: str, window_from, window_to,
) -> list[tuple[str, str]]:
    """탐색 후보 = 핫셋(정착 clk≥10)의 여집합 광고그룹 전부(D-NAO-70① "무조건 모든 조건").

    선정 기준(핫셋 base 필터 재현 + 클릭 게이트 반전):
    - 캠페인 엔티티 행이 status='on'이고 campaign_type='SHOPPING'일 때만(부모 체인 최상위 활성 +
      쇼핑검색 grain). 캠페인 행 부재/off/타입 미확보/비SHOPPING → [] (fail-closed·방어적 타입 체크).
    - 그 캠페인 소속 adgroup 엔티티 중 status='on'·campaign_type='SHOPPING'(grain 방어) 인 것.
    - 정착창(7일) clk < _MIN_CLICK_FOR_EXPLORATION = 핫셋 미달 전부(웜존 4~9 포함·콜드 0클릭·
      노출0 imp=0 그룹 포함 — imp/rank는 후보 게이트에서 강등, PLAN §2·§실측 3).

    핫셋(_hot_set_candidates)과 정확히 상호배타: 동일 base(SHOPPING adgroup·status on·캠페인 on)
    위에서 핫셋은 clk≥10, 탐색은 clk<10을 취하므로 한 그룹이 양쪽에 동시에 들지 않는다.

    반환: [(entity_type, entity_id), ...] entity_id 오름차순(결정적). 여기서 entity_type='adgroup' 고정."""
    campaign_row = (
        db.query(NaverEntity)
        .filter(
            NaverEntity.entity_type == "campaign",
            NaverEntity.entity_id == campaign_id,
            NaverEntity.status == "on",
        )
        .first()
    )
    if campaign_row is None:
        return []  # 캠페인 엔티티 off/행 부재 — 체인 최상위 비활성(fail-closed)
    if (campaign_row.campaign_type or "") != _EXPLORATION_CAMPAIGN_TYPE:
        return []  # WEB_SITE/BRAND_SEARCH/타입 미확보 — 탐색 대상 아님(방어적 캠페인 타입 체크·fail-closed)

    adgroups = (
        db.query(NaverEntity)
        .filter(
            NaverEntity.campaign_id == campaign_id,
            NaverEntity.entity_type == "adgroup",
            NaverEntity.status == "on",
        )
        .order_by(NaverEntity.entity_id.asc())
        .all()
    )
    out: list[tuple[str, str]] = []
    for e in adgroups:
        if (e.campaign_type or "") != _EXPLORATION_CAMPAIGN_TYPE:
            continue  # grain 방어(SHOPPING adgroup만) — 동기화 미채움/혼선 행 제외
        clk = _settlement_clk(db, e.entity_id, window_from, window_to)
        if clk < _MIN_CLICK_FOR_EXPLORATION:
            out.append((e.entity_type, e.entity_id))
    return out


def exploration_trigger(
    settle_agg: dict, last_step_at: datetime | None, now: datetime,
) -> tuple[bool, str]:
    """탐색 발동 판정(순수) — 후보 그룹 1개에 대해 지금 탐색 UP을 쏠지 결정한다.

    발동 조건(둘 다 충족):
    ① 정착창 clk < _MIN_CLICK_FOR_EXPLORATION — 클릭 표본 부족 = ROAS 판단불가 = 증거 구매 대상
       (D-NAO-70① "클릭 0 = 방치 금지"). imp=0 그룹도 대상(rank·노출은 참고 신호로 강등, PLAN §2).
    ② 쿨다운 2h 경과 — last_step_at(마지막 탐색 스텝 시각) 이후 _EXPLORATION_COOLDOWN_HOURS 경과
       (가드3, D-NAO-71 "하루 1회" 대체 = 탐색 사이클 간격). last_step_at=None(첫 탐색)이면 통과.
       실제 last_step_at 조회(change_log)는 BX3 레인 배선 몫 — 여기선 값만 받아 순수 판정한다.

    settle_agg: _settlement_agg 형태 dict({"clk", "cost", "conv_amt"}). 반환 (fire, 한국어 사유)."""
    clk = int(settle_agg.get("clk", 0))
    if clk >= _MIN_CLICK_FOR_EXPLORATION:
        return (False, f"클릭 표본 충분(clk={clk}≥{_MIN_CLICK_FOR_EXPLORATION}) — 탐색 대상 아님(핫셋/정착 ROAS 경로)")
    if last_step_at is not None:
        elapsed_hours = (now - last_step_at).total_seconds() / 3600.0
        if elapsed_hours < _EXPLORATION_COOLDOWN_HOURS:
            return (
                False,
                f"쿨다운 미경과({elapsed_hours:.1f}h<{_EXPLORATION_COOLDOWN_HOURS}h, D-NAO-19) — 사이클 대기",
            )
    return (True, f"클릭 표본 부족(clk={clk}<{_MIN_CLICK_FOR_EXPLORATION})·쿨다운 통과 — 증거 구매 탐색 UP 발동")


def ladder_judgment(
    last_probe: dict | None, since_step_stats: dict, ceiling: int, current_bid: int,
) -> tuple[str, str]:
    """사이클 래더 판정(순수·PLAN §1 가드4, D-NAO-71) — 4분기. 매 탐색 사이클(≥2h)마다
    직전 스텝 이후의 성과로 래더 다음 수를 정한다(D+1 고정이 아니라 쿨다운 사이클 단위).

    - last_probe is None → ('start', ...): 직전 탐색 스텝 없음 = 첫 탐색 시작.
    - since_step_stats clk > 0 → ('stop_observe', ...): 직전 스텝 이후 클릭 발생 → 래더 중단,
      정착창 ROAS 평가 경로로 인계(증거 확보 = 목적 달성, money-action 경로가 이어받음).
    - 무클릭·무비용 → 상한 판정: current_bid < ceiling 이면 ('step_up', ...) 한 단 추가,
      아니면 ('capped', ...) 경제성 상한 도달·무클릭 → 탐색 종료·`explored_capped` 표기
      (구조 문제=소재/관련성 신호).

    순수함수: DB 접근 없음. last_probe(직전 탐색 스텝 스냅샷)·since_step_stats(직전 스텝 이후
    성과 {"clk", "cost", ...})·ceiling(경제성 상한)·current_bid(현 입찰)는 전부 호출측(BX3)이
    조회해 넘긴다. 반환 (verdict, 한국어 사유). verdict ∈ {'start','stop_observe','step_up','capped'}."""
    if last_probe is None:
        return ("start", "직전 탐색 스텝 없음 — 첫 탐색 시작")
    clk = int(since_step_stats.get("clk", 0))
    if clk > 0:
        return ("stop_observe", f"직전 스텝 이후 클릭 발생(clk={clk}) — 래더 중단·정착 ROAS 관측 인계")
    if current_bid < ceiling:
        return ("step_up", f"무클릭(clk=0)·상한 미도달(현 {current_bid}<상한 {ceiling}) — 한 단 추가")
    return ("capped", f"무클릭(clk=0)·경제성 상한 도달(현 {current_bid}≥상한 {ceiling}) — 탐색 종료·관찰 표기")
