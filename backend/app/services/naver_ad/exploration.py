# exploration.py — 저볼륨 그룹 탐색 UP 순수 SA (스프린트 B-X BX1-rev, D-NAO-70·71)
# 역할(SA·순수함수): 핫셋 게이트(정착 7일 클릭≥10) 밖 = "죽는 캠페인" 경로에 놓인 저볼륨
#   SHOPPING 광고그룹을 능동 탐색 대상으로 골라내고(후보), 탐색을 발동할지 판정하며(트리거),
#   **순위 피드백 상태 기계**(ladder_judgment)로 사이클마다 다음 수(시작/적응 스텝/재가동/상향
#   정지/진단 종료/상한)를 정하고, **적응 스텝 크기**(adaptive_step)를 산정한다(D-NAO-71 19:01
#   교정: 눈먼 % 상향이 아니라 최저 CPC로 밴드 진입). BX1-rev = 순수 선정/판정/스텝 산정만:
#   실쓰기(update_ad_bid UP·explore_op 승인원)는 BX2, 레인 배선·관측 조립·다운스트림은 BX3 몫.
#   ★import 정책: models + campaign_backfill(sentinel 상수·최말단) + 최말단 순수 SA
#   (bid_simulator·campaign_target_resolver — 둘 다 models만 의존, auto_operator/exploration을
#   import하지 않아 순환 없음)만 import한다. auto_operator는 import하지 않는다 — BX3에서
#   auto_operator가 이 모듈을 import(레인 배선)하므로 역방향 import는 순환을 만든다(bid_step_types
#   R0와 동일 철학). 핫셋 클릭 게이트 상수는 로컬로 복제하고 출처를 명시하며(아래
#   _MIN_CLICK_FOR_EXPLORATION), 정착창 clk 집계는 auto_operator._settlement_agg의 adgroup grain
#   관례를 그대로 복제한다(테스트가 두 상수/집계의 정합을 별도로 고정한다). BX2: explore_op 승인원
#   상수 + 경제성 상한 SA(exploration_ceiling)를 추가한다(손·게이트 — 레인 배선은 BX3).
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverEntity, NaverProductBep
from app.services.naver_ad import bid_simulator, campaign_target_resolver
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

# ── BX2: explore_op 자동 실쓰기 승인원(D-NAO-70③ "자동 개방") ──
# probe_op/revert_op 관례(auto_operator.APPROVAL_SOURCE_PROBE/REVERT)와 동형 — 탐색 UP 실쓰기의
# approval_source. naver_proposals.approval_source는 String(12)이라 "explore_op"(10자) 적합.
# 이 승인원만 소재(ad) UP 실쓰기 게이트(harness _execute_update_bid ad-guard)를 뚫고, 킬스위치
# 화이트리스트(harness)에 등록돼 auto_operate OFF에서 차단된다. 다른 승인원(콘솔 수동 NULL·
# auto_op·delegation)의 소재 UP은 여전히 봉쇄(B3 Confirm 경계 유지). ★상수 정의처가 auto_operator가
# 아니라 여기인 이유: BX2에서 auto_operator.py 수정 금지(병행 세션·레인 배선은 BX3) — exploration이
# 자기 승인원을 소유하고 harness가 이 상수를 참조한다(BX3에서 레인이 이 값을 approval_source로 심는다).
APPROVAL_SOURCE_EXPLORE = "explore_op"  # 10자 — String(12) 적합

# 경제성 상한 fail-closed 폴백의 최소 하한(원). rpc·bep·현재입찰이 전부 부재해 상한을 못 세우면
# 탐색을 발동하지 않는 게 안전하므로 0을 반환한다(ladder_judgment가 current_bid<ceiling=0을
# 만족 못 해 step_up 안 함 → capped). 0은 "상한 근거 없음"의 표준 신호(affordable_ceiling 관례 동형).
_CEILING_UNAVAILABLE = 0

# ── BX1-rev: 순위 피드백 상태 기계 상수(PLAN §1 가드1·4, D-NAO-71 2026-07-21 19:01 Jino 교정) ──
# 이익 스팟 밴드 = 클릭이 관측되는 순위대(rank 2.5~4). 목표 = 핫셋 미달 그룹을 이 밴드에 **최소
# 입찰로** 진입시킨다("아무 목적없이 30%씩 가격만 올리는건 의미가 없다 — 최저의 CPC로 순위를
# 조절하고 싶다", Jino 원문). 순위 숫자가 작을수록 상단(더 좋은 위치):
#   · rank ≤ _EXPLORATION_BAND_LOW(2.5)   = 과열밴드(진입 금지). 클릭0이면 순위 병리 아님 진단 종료.
#   · 2.5 < rank ≤ _EXPLORATION_BAND_HIGH(4.0) = 밴드 안(도달 — 상향 정지·관측, 과열밴드 진입 금지).
#   · rank > 4.0 = 밴드 밖(climbing — 적응 스텝 상향).
_EXPLORATION_BAND_LOW = Decimal("2.5")   # 밴드 상단(과열 경계, PLAN §1 가드4 "과열밴드 rank<2.5 진입 금지")
_EXPLORATION_BAND_HIGH = Decimal("4.0")  # 밴드 하단(진입 목표 = 최소 입찰의 이익 스팟 밴드 진입점)
_EXPLORATION_TARGET_BAND = (_EXPLORATION_BAND_LOW, _EXPLORATION_BAND_HIGH)

# 콜드(imp=0=순위 관측 불가)·첫 스텝·기울기 부재 시 보수적 소폭 스텝(PLAN §1 가드1 "기울기 정보
# 없으면 보수적 소폭(예: +10%)"). 순위 무반응(직전 스텝에 입찰 올렸는데 순위 개선 없음)이면 직전
# Δ입찰 × 이 점증 계수로 스텝을 키운다(가드1 "순위 무반응이면 스텝 점증"). 두 산정 모두 상한
# _EXPLORATION_STEP_PCT(0.30=1스텝 상한, 기본 아님) 안에서 클램프된다.
_EXPLORATION_FIRST_STEP_PCT = Decimal("0.10")
_EXPLORATION_UNRESPONSIVE_GROWTH = Decimal("1.5")

# 흐름 정체 관측창(시간) — hold 중 이 창 안에 클릭이 0이면 정체로 보고 재가동 판단(PLAN §1 가드4
# ④ "hold 중 최근 24h 클릭 0 정체 ∧ 정착클릭<10 → 재가동"). 클릭 발생(hold 진입)과 재가동을
# 가르는 **신선** 신호 — 정착창(D-8~D-2)은 지연되므로 별개 창이 필요하다.
_EXPLORATION_FLOW_WINDOW_H = 24

# ── 유효 입찰가 하한: grain별 규격 분리(VT4 D-NAO-82① 2026-07-22 라이브 해부 발견) ──
# ★결함이었던 것: `_EXPLORATION_MIN_BID`(70)은 파워링크(WEB_SITE) **키워드 grain** 실측 역추적
#   하한(bid_simulator._MIN_BID 출처)인데, 이 탐색 래더는 SHOPPING 전용(_EXPLORATION_CAMPAIGN_TYPE)
#   이라 키워드 규격을 그대로 쓰면 안 됐다. prod 실측(2026-07-22): 쇼핑 그룹 158개가 bid=50으로
#   라이브 노출 수신 중(70원 미만 입찰은 SHOPPING adgroup grain에만 존재·키워드 grain 0건) →
#   **쇼핑검색 최소 유효입찰 = 50원**. 70원 하한을 쇼핑 래더에 적용해 50원 그룹의 스텝이 영구
#   소실됐다(03 아이폰17프로 rank 6.39·bid 50·발사 이력 0의 근본 원인). → 래더 유효 하한을
#   쇼핑 규격 50으로 교체한다. _EXPLORATION_MIN_BID(70)은 삭제하지 않고 출처 문서로 보존(키워드
#   grain 규격·bid_simulator._MIN_BID 정합) — 래더 판정에는 아래 SHOPPING 하한을 쓴다.
_EXPLORATION_MIN_BID = 70               # (보존) 파워링크 키워드 grain 유효 하한·bid_simulator 규격 출처
_EXPLORATION_MIN_BID_SHOPPING = 50      # 쇼핑검색 adgroup grain 유효 하한(prod 158그룹 bid=50 라이브 실증, 2026-07-22)
_EXPLORATION_MAX_BID = 100_000  # 유효 입찰가 상한
_EXPLORATION_BID_INCREMENT = 10  # 10원 단위(스텝 반올림 = floor //10*10, PLAN §3 BX2 GATE 이월 P2③)
# 플로어 입찰 상한(원) — VT4 수요 우선 정렬의 "신수요 개척 우선" 대상 판정 경계(D-NAO-82①).
# 이 값 이하 입찰 ∧ 판정 표본 미달(clk<10) 그룹 = 아직 밴드 진입 안 한 방치된 신수요 후보.
_EXPLORATION_FLOOR_BID_MAX = 100


def _apply_bm_anchor(target: Decimal, current_bid: int, bm_prior: int | None) -> Decimal:
    """BM P4(D-NAO-78) 초기입찰 프라이어 — 기울기 부재(콜드/첫 스텝/무근거) 구간에서만 사용.

    bm_prior = 대행사 고성과 그룹 입찰밴드 p50(bench_kind='bid_band', bm_benchmark). 눈먼 소폭
    스텝 대신 "대행사가 검증한 밴드"를 향해 시드한다(눈먼 +10% 대신 근거 있는 시작점). 현재가
    이하 앵커는 무시(하향 앵커 금지 — 탐색은 UP만). 상한 클램프는 호출측 _finalize_step(cap_bid)가
    담당하므로 여기선 max만 취한다. bm_prior=None/현재가 이하면 target 그대로(fail-open·회귀 0)."""
    if bm_prior is None or bm_prior <= current_bid:
        return target
    return max(target, Decimal(bm_prior))


def adaptive_step(
    current_bid: int, current_rank, target_band: tuple, last_step: dict | None, cap_pct: Decimal,
    *, bm_prior: int | None = None,
) -> int | None:
    """순위 목표형 적응 스텝(순수·PLAN §1 가드1, D-NAO-71 19:01 교정) — 목표 밴드까지 **필요한
    만큼만** 상향해 최소 입찰로 밴드에 진입시킨다(눈먼 % 상향 금지).

    입력:
      current_bid:  현재(스텝 기준) 입찰가(원). ≤0이면 None(근거 없음).
      current_rank: 최근 사이클 imp-가중 avg_rank(Decimal|float|None). None = imp=0(순위 관측
                    불가 = 유일한 눈먼 구간) → 보수적 소폭 스텝으로 노출 발생까지.
      target_band:  (band_low, band_high) — (2.5, 4.0). band_high(밴드 하단=진입 목표)를 노린다.
      last_step:    직전 스텝 스냅샷 {"bid": 직전(스텝 前) 입찰, "rank": 그때 관측 순위}|None.
                    Δ순위/Δ입찰 관측 기울기 산정용. None(첫 스텝)이면 보수적 소폭.
      cap_pct:      1스텝 변경폭 상한(=_EXPLORATION_STEP_PCT 0.30). **기본이 아니라 상한** —
                    적응 산정이 이보다 작으면 그만큼만 오른다.
      bm_prior:     BM P4 초기입찰 프라이어(대행사 밴드 p50, optional). **기울기가 없는 눈먼
                    구간**(콜드·첫 스텝·무근거 폴백)에서만 시드로 쓴다 — 실관측 기울기가 있으면
                    무시(폐루프가 이미 정보 보유). None(기본)이면 전 구간 기존과 byte-동일(회귀 0,
                    §0 금지선 4 fail-open). 상한(cap_bid)이 최종 클램프하므로 밴드가 멀어도 1스텝
                    상한을 넘지 않는다(안전).

    반환: target_bid(int, 10원 단위·현재 초과·50~100,000 쇼핑 유효입찰) / None(스텝 없음 = 밴드
          내·산정 소실 = 상한이 막음). VT4(D-NAO-82①): 눈먼 소폭 구간의 저입찰(≤~50원) 스텝
          소실 시 최소 증분(+10원)을 보장(50→60) — 상한 이내에서만.

    산정(PLAN §1 가드1):
      ① 상한 = floor(current×(1+cap_pct))(10원 내림 — 30% 가드 미세 초과 방지, P2③).
      ② imp=0(current_rank None) → 보수적 소폭(current×first_step_pct). 노출 살아날 때까지.
      ③ 이미 밴드 내(current_rank ≤ band_high) → None(상향 불필요 — 밴드 도달, 상향 정지).
      ④ Δrank_needed = current_rank − band_high(>0). 직전 스텝 기울기(Δrank/Δbid)가 있으면
         (last_step.bid<current_bid ∧ rank 개선>0) 그 기울기로 목표까지 필요 증분을 근접
         산정(밴드 접근할수록 delta_needed↓ → 스텝 자연 축소 = 오버슈트 과지불 방지). 순위
         무반응(rank 개선≤0)이면 직전 Δ입찰×점증 계수(스텝 점증). 기울기 부재면 보수적 소폭.
      ⑤ 산정 → 상한(cap_bid·_MAX_BID) 클램프 → 10원 내림 → 현재 초과·≥50원(쇼핑 하한)이어야 유효.
    """
    if current_bid <= 0:
        return None
    _, band_high = target_band
    cap_bid = int((Decimal(current_bid) * (Decimal(1) + Decimal(str(cap_pct)))) // _EXPLORATION_BID_INCREMENT
                  * _EXPLORATION_BID_INCREMENT)
    cap_bid = min(cap_bid, _EXPLORATION_MAX_BID)

    # ② imp=0(순위 관측 불가) — 유일한 눈먼 구간. 노출 발생까지 보수적 소폭 스텝(BM 프라이어 시드 대상).
    if current_rank is None:
        target = Decimal(current_bid) * (Decimal(1) + _EXPLORATION_FIRST_STEP_PCT)
        target = _apply_bm_anchor(target, current_bid, bm_prior)
        return _finalize_step(target, current_bid, cap_bid, allow_min_increment=True)  # 눈먼 소폭 — 플로어 스텝 보장

    cr = Decimal(str(current_rank))
    band_high_d = Decimal(str(band_high))
    # ③ 이미 밴드 내(도달) — 상향 안 함(상향 정지·관측은 ladder_judgment 몫).
    if cr <= band_high_d:
        return None

    delta_needed = cr - band_high_d  # 밴드 하단까지 필요한 순위 개선폭(>0)

    increment: Decimal | None = None
    if last_step is not None:
        prev_bid = last_step.get("bid")
        prev_rank = last_step.get("rank")
        if prev_bid is not None and prev_rank is not None and current_bid > int(prev_bid):
            rank_gain = Decimal(str(prev_rank)) - cr    # 순위 개선(숫자 감소)이면 >0
            bid_delta = Decimal(current_bid - int(prev_bid))
            if rank_gain > 0:
                # 반응함 — 입찰/순위개선 기울기 × 남은 필요 개선폭(밴드 접근 시 자연 축소).
                increment = (bid_delta / rank_gain) * delta_needed
            else:
                # 순위 무반응 — 직전 Δ입찰 × 점증 계수(스텝 점증, PLAN §1 가드1).
                increment = bid_delta * _EXPLORATION_UNRESPONSIVE_GROWTH
    if increment is None or increment <= 0:
        # 기울기 부재 — 보수적 소폭(BM 프라이어 시드 대상: 눈먼 소폭 대신 대행사 밴드로 시드).
        increment = Decimal(current_bid) * _EXPLORATION_FIRST_STEP_PCT
        target = _apply_bm_anchor(Decimal(current_bid) + increment, current_bid, bm_prior)
        return _finalize_step(target, current_bid, cap_bid, allow_min_increment=True)  # 눈먼 소폭 — 플로어 스텝 보장
    target = Decimal(current_bid) + increment  # 실관측 기울기 산정 — BM 앵커 미적용(폐루프 우선)
    # 실관측 기울기 경로는 min_increment 미적용(측정된 소폭 강제 +10원은 과열밴드 오버슈트 위험).
    return _finalize_step(target, current_bid, cap_bid)


def _finalize_step(
    target: Decimal, current_bid: int, cap_bid: int, *, allow_min_increment: bool = False,
) -> int | None:
    """스텝 반올림·클램프(PLAN §3 BX2 GATE 이월 P2③): floor(//10*10) → 상한 클램프 →
    현재 초과·최소입찰가(쇼핑 50원) 이상이어야 유효(아니면 None=스텝 소실).

    allow_min_increment(VT4 D-NAO-82① 플로어 스텝 함정 수정): **눈먼 소폭 구간**(콜드 imp=0·
    첫 스텝·기울기 부재 폴백)에서만 True. 이 구간의 보수 +10%는 저입찰(≤~50원)대에서 10원
    내림하면 current와 같아져(예 50×1.1=55→floor 50=current) 스텝이 영구 소실됐다 — 프로그램이
    50원 그룹을 절대 못 올리던 결함(prod 실측). 소실 시 **최소 유효 증분(current+10원)으로 승격**
    한다. 단 cap_bid(30% 상한) 이내여야 하며 cap_bid<current+10이면 승격 불가(None 유지 =
    상한이 스텝을 막는 정상 동작). ★실관측 기울기 산정 경로(adaptive_step ④ 반응함)에서는
    False로 둔다 — 기울기가 "밴드까지 +N원만 필요"라고 말하면 강제 +10원이 과열밴드(rank<2.5)로
    오버슈트할 수 있어(test_adaptive_step_never_overshoots_below_2_5·가드4 과열밴드 진입 금지),
    측정된 소폭은 소실=hold가 옳다."""
    stepped = int((target // _EXPLORATION_BID_INCREMENT) * _EXPLORATION_BID_INCREMENT)  # 10원 내림
    stepped = min(stepped, cap_bid, _EXPLORATION_MAX_BID)
    # VT4 플로어 스텝 함정 수정: 눈먼 소폭이 내림으로 소실됐고 원 target이 상향이면 최소 증분 보장.
    if allow_min_increment and stepped <= current_bid and target > Decimal(current_bid):
        bumped = current_bid + _EXPLORATION_BID_INCREMENT
        if bumped <= cap_bid:  # 30% 상한 이내에서만 승격(상한이 막으면 None 유지)
            stepped = bumped
    if stepped <= current_bid or stepped < _EXPLORATION_MIN_BID_SHOPPING:
        return None
    return stepped


def _grain_settlement_agg(
    db: Session, *, adgroup_id: str | None, campaign_id: str | None, date_from, date_to,
) -> dict:
    """정착창 [date_from, date_to] 내 (clk, conv_amt) 합 — grain별(adgroup/campaign/account).
    auto_operator._settlement_agg·proposal_pipeline._precompute_aggregates의 집계 규칙을 그대로
    복제한다(conv_amt = conv_direct_amt + conv_indirect_amt, backfill sentinel 행 제외). 필터:
    - adgroup_id 지정 → 그 광고그룹 grain
    - campaign_id만 지정 → 그 캠페인 grain
    - 둘 다 None → 계정 grain(전 캠페인유형 — pooled_rpc 계정 prior 관례, _precompute_aggregates
      campaign_type=None 기본과 동일). cost는 상한 산식에 불필요하므로 집계하지 않는다."""
    q = db.query(
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
    ).filter(
        NaverAdDaily.ad_date >= date_from,
        NaverAdDaily.ad_date <= date_to,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    )
    if adgroup_id is not None:
        q = q.filter(NaverAdDaily.adgroup_id == adgroup_id)
    elif campaign_id is not None:
        q = q.filter(NaverAdDaily.campaign_id == campaign_id)
    clk, direct, indirect = q.one()
    return {"clk": int(clk), "conv_amt": int(direct) + int(indirect)}


def _resolve_exploration_bep_roas(db: Session, campaign_id: str, adgroup_id: str) -> Decimal | None:
    """탐색 경제성 상한의 분모 BEP ROAS 폴백 사다리(D-NAO-71 "product_bep의 BEP ROAS(상품 미연결
    시 캠페인 기본 BEP)"): ① 그룹 매핑 상품 BEP → ② 캠페인 매핑 상품 BEP → ③ 계정 기본 BEP.
    전부 부재면 None(→ 경제성 상한 계산 불가 → 휴리스틱 캡만 남음). has_cost=True 상품만 대상
    (원가 미확인 상품 추정 금지) — campaign_target_resolver 단일 소스 재사용."""
    bep = campaign_target_resolver.weighted_product_value_for_adgroup(
        db, adgroup_id, NaverProductBep.bep_roas,
    )
    if bep is not None and bep > 0:
        return bep
    bep = campaign_target_resolver.weighted_product_value_for_campaign(
        db, campaign_id, NaverProductBep.bep_roas,
    )
    if bep is not None and bep > 0:
        return bep
    bep = campaign_target_resolver.account_default_bep_roas(db)
    if bep is not None and bep > 0:
        return bep
    return None


def _heuristic_ceiling(current_bid: int) -> int:
    """휴리스틱 병행 캡 = 현재입찰 × _EXPLORATION_CEILING_MULT(2.0), 유효 입찰가로 클램프
    (10원 단위 내림·70~100,000원). current_bid가 유효하지 않으면(≤0) 0(상한 근거 없음)."""
    if current_bid <= 0:
        return _CEILING_UNAVAILABLE
    raw = int((Decimal(current_bid) * _EXPLORATION_CEILING_MULT).to_integral_value(rounding="ROUND_DOWN"))
    rounded = (raw // 10) * 10  # 10원 단위 내림(bid_simulator 유효 입찰가 규격)
    if rounded < 70:
        return _CEILING_UNAVAILABLE
    return min(rounded, 100_000)


def exploration_ceiling(
    db: Session, campaign_id: str, adgroup_id: str, current_bid: int, window_from, window_to,
) -> int:
    """탐색 UP 경제성 상한(원) — D-NAO-71로 수량 캡이 사라져 **유일한 가격 브레이크**(PLAN §1 가드2).

    산식(rank_servo._servo_economic_ceiling 선례 재사용): "클릭 가치 기반 최대 입찰가" =
      pooled_rpc(그룹→캠페인→계정 계층 수축) ÷ product_bep의 BEP ROAS. bid_simulator.pooled_rpc·
      affordable_ceiling을 그대로 태운다(수축 산식 재구현 금지 — 검증된 SA 재사용, 원칙18-4).
      SHOPPING 근사(_servo_economic_ceiling 동형): keyword_row=그룹 정착 실적, group_agg=campaign_agg
      (쇼핑은 그룹 하위 키워드 grain 부재 → group=campaign 근사).

    ★보정계수(diagnosis.correction_factor) 미적용 — **의도적 보수 선택**(PLAN §실측 2·D-NAO-71
      "보수값 채택 후 주석 명시"): 보정계수는 네이버 과소보고를 상향 보정(>1)해 상한을 **높인다**.
      탐색은 콜드 그룹(표본 없음)에서 도는 자동 실쓰기라 상한을 낮게 잡는 쪽이 안전하므로 raw rpc를
      쓴다(문서에서 "탐색용 보정" 확인 안 됨 → 추정 금지 원칙 상 보수값). BX3 라이브 실측 후 재판정.

    폴백 사다리(각 단계를 테스트로 고정, PLAN §실측 2):
      1) BEP ROAS 해석(_resolve_exploration_bep_roas): 그룹→캠페인→계정 순.
      2) pooled_rpc(정착창 grain 집계) > 0 ∧ BEP 있음 → economic = affordable_ceiling(rpc, bep).
      3) heuristic = current_bid × 2.0(병행 휴리스틱 캡, _EXPLORATION_CEILING_MULT).
      4) 결합(보수): economic 있으면 min(economic, heuristic)(둘 다 상한).
      5) **경제 증거 전무(codex P1 fail-closed)**: 그룹→캠페인→계정 전 레벨에서 경제 증거(BEP와
         RPC 둘 다)를 얻지 못하면(bep None 또는 economic≤0) → ceiling = **current_bid** 반환.
         그러면 ladder_judgment가 current_bid ≥ ceiling → 'capped'로 판정해 상향 불가(fail-closed) —
         휴리스틱(current×2)을 반환하면 current<ceiling이라 근거 없이 상향되는 fail-open이 됐다.
         ★계정 BEP(D-NAO-57 account_default_bep_roas)가 정상 존재하는 한 bep_roas는 항상 해석되므로
         이 분기는 실제로는 미발동(BEP 스냅샷이 통째로 비어야 도달) — 순수 안전망이다.

    반환: 유효 입찰가(10원 단위, 70~100,000원) / current_bid(경제 증거 전무=capped) / 0(current도
      부재). 순수 판정(부수효과 없음)."""
    heuristic = _heuristic_ceiling(current_bid)

    bep_roas = _resolve_exploration_bep_roas(db, campaign_id, adgroup_id)
    if bep_roas is None:
        return current_bid  # 경제 증거 전무(BEP 없음) — fail-closed(ladder capped, codex P1)

    grp = _grain_settlement_agg(db, adgroup_id=adgroup_id, campaign_id=None, date_from=window_from, date_to=window_to)
    camp = _grain_settlement_agg(db, adgroup_id=None, campaign_id=campaign_id, date_from=window_from, date_to=window_to)
    acct = _grain_settlement_agg(db, adgroup_id=None, campaign_id=None, date_from=window_from, date_to=window_to)

    # SHOPPING 근사(_servo_economic_ceiling 동형): keyword_row=그룹, group_agg=campaign_agg.
    rpc_raw = bid_simulator.pooled_rpc(
        {"clk": grp["clk"], "conv_amt": grp["conv_amt"]}, camp, camp, acct,
    )
    economic = bid_simulator.affordable_ceiling(rpc_raw, bep_roas)  # rpc≤0이면 0 반환(division guard)
    if economic <= 0:
        return current_bid  # 경제 증거 전무(rpc≤0, 심층 콜드) — fail-closed(ladder capped, codex P1)

    if heuristic <= 0:
        return economic  # current_bid 부재지만 경제 근거는 있음 — 경제 상한 채택
    return min(economic, heuristic)  # 둘 다 상한(보수 min 결합, D-NAO-71 병행 캡)


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


def prioritize_candidates(
    db: Session, campaign_id: str, candidates: list[tuple[str, str]], window_from, window_to,
) -> list[tuple[str, str]]:
    """탐색 후보 수요 우선 재정렬(순수·VT4 D-NAO-82① — 07-22 라이브 해부 발견).

    근거 표본: 03 캠페인 아이폰17·17프로 — 노출 수요가 캠페인의 34%인데 bid 50원으로 방치돼
    래더가 손도 못 댐(발사 이력 0). 후보를 entity_id 순으로만 순회하면 런당 관측/쿨다운 예산이
    앞 순번에 쏠려 정작 신수요(고노출·저입찰) 그룹이 뒤로 밀린다 → **수요 크기를 순회 우선순위에
    반영**한다. ★"순서만" 바꾼다 — 봉투·스텝 크기·경제성 상한·쿨다운·발동 조건 전부 불변
    (새 권한 없음, PLAN §4.2). 재정렬은 exploration_candidates 결과에만 적용되고 다운스트림
    판정(트리거·래더·상한)은 그대로다.

    정렬 규칙:
      1순위(신수요 개척): (현 그룹 입찰 bid_amt ≤ _EXPLORATION_FLOOR_BID_MAX(100원)) ∧ (정착창
        clk < _MIN_CLICK_FOR_EXPLORATION(10)) 인 adgroup — 최근 7일(정착창) 노출 **내림차순**.
        동률 노출은 entity_id 오름차순(결정성). = 아직 밴드 진입 못 한 채 방치된 고수요 플로어 그룹.
      2순위: 나머지 — 입력 순서(exploration_candidates의 entity_id 오름차순) 그대로 유지(결정성 보존).

    데이터 소스: bid_amt = naver_entity(그룹 기본가). 노출·clk = naver_ad_daily [window_from,
    window_to] 합(sentinel 제외 — _grain_settlement_agg 관례 동일). 후보 adgroup을 **일괄
    쿼리**(2회: daily 집계 GROUP BY + entity bid)로 뽑아 N+1을 피한다. bid_amt=None(미동기) 또는
    daily 행 부재(콜드 imp=0) 그룹은 판정 가능한 신호만으로 평가(None 입찰 = 플로어 판정 불가 →
    2순위 유지·보수적).

    반환: 재정렬된 [(entity_type, entity_id), ...](입력과 동일 원소·개수, 순서만 변경)."""
    if not candidates:
        return list(candidates)
    adgroup_ids = [eid for (etype, eid) in candidates if etype == "adgroup"]
    if not adgroup_ids:
        return list(candidates)

    # ① 일괄 집계(N+1 방지): 후보 adgroup들의 정착창 clk·imp 합(sentinel 제외).
    agg: dict[str, dict] = {}
    rows = (
        db.query(
            NaverAdDaily.adgroup_id,
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.imp), 0),
        )
        .filter(
            NaverAdDaily.ad_date >= window_from,
            NaverAdDaily.ad_date <= window_to,
            NaverAdDaily.adgroup_id.in_(adgroup_ids),
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .group_by(NaverAdDaily.adgroup_id)
        .all()
    )
    for aid, clk, imp in rows:
        agg[aid] = {"clk": int(clk), "imp": int(imp)}

    # ② 후보 그룹 현재 입찰가 일괄 조회(naver_entity — 그룹 기본가 bid_amt).
    bids: dict[str, int | None] = {}
    for eid, bid in (
        db.query(NaverEntity.entity_id, NaverEntity.bid_amt)
        .filter(
            NaverEntity.entity_type == "adgroup",
            NaverEntity.entity_id.in_(adgroup_ids),
        )
        .all()
    ):
        bids[eid] = bid

    tier1: list[tuple[int, str, tuple[str, str]]] = []  # (−imp, entity_id, cand) — 노출 내림·id 오름
    tier2: list[tuple[str, str]] = []
    for cand in candidates:
        etype, eid = cand
        a = agg.get(eid, {"clk": 0, "imp": 0})
        bid = bids.get(eid)
        is_floor_new_demand = (
            etype == "adgroup"
            and bid is not None and bid <= _EXPLORATION_FLOOR_BID_MAX
            and a["clk"] < _MIN_CLICK_FOR_EXPLORATION
        )
        if is_floor_new_demand:
            tier1.append((-a["imp"], eid, cand))  # −imp = 노출 내림차순 정렬키
        else:
            tier2.append(cand)  # 입력 순서 유지(entity_id 오름차순)
    tier1.sort(key=lambda t: (t[0], t[1]))  # 노출 내림(−imp)·동률 entity_id 오름(결정성)
    return [c for (_i, _e, c) in tier1] + tier2


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
    *, recent_flow_clk: int = 0, settled_clk: int = 0, flow_available: bool = True,
) -> tuple[str, str]:
    """사이클 래더 판정(순수·PLAN §1 가드4, D-NAO-71 18:56+19:01) — **순위 피드백 상태 기계**.
    매 탐색 사이클(≥2h)마다 직전 스텝 이후의 순위·클릭으로 다음 수를 정한다(D+1 고정 아님).

    verdict ∈ {'start','step_up','reactivate','stop_observe','capped','not_rank'} (BX3 카운터
    매핑: start/step_up/reactivate→explored 실쓰기 / stop_observe→explored_held /
    capped→explored_capped / not_rank→explored_not_rank).

    입력(전부 호출측 BX3이 조회해 넘김 — DB 접근 없음):
      last_probe:  직전 탐색 스텝 스냅샷 {"bid": 직전(前) 입찰, "rank": 그때 순위}|None(첫 스텝).
      since_step_stats: 직전 스텝 이후 최근 사이클 관측 {"clk","imp","cost","avg_rank"}.
                   avg_rank=None(imp=0 → 순위 관측 불가).
      ceiling:     경제성 상한(원, exploration_ceiling). 경제 증거 전무면 =current_bid(fail-closed).
      current_bid: 현(스텝 기준) 입찰가.
      recent_flow_clk: **롤링 24h**(_EXPLORATION_FLOW_WINDOW_H, 자정 경계 넘음) 클릭 합 — 흐름/정체
         신호. BX3 레인이 어제 시간별(NaverKeywordHourly)+오늘 curve로 조립(codex P1 자정 리셋 수정).
      settled_clk: 정착창(D-8~D-2) 클릭 합(과거 클릭 이력 = hold 상태 신호).
      flow_available: 롤링 24h 창을 실제로 확정했는지(어제 시간별 데이터 가용). False면 정체를
         확언할 수 없어 fail-toward-hold(스텝 대신 관측 — codex P1 안전 방향).

    상태 기계(우선순위 순):
      ⓪ last_probe None → 'start'(첫 탐색) — 단 current_bid≥ceiling이면 'capped'(발동 불가).
      ① 이번 사이클 클릭 발생(clk>0) → 'stop_observe'(상향 정지·정착 ROAS 관측 인계, hold —
         영구 정지 아님. 목적=증거 확보 달성).
      ② 무클릭 ∧ rank≤2.5(과열밴드인데 클릭0 지속) → 'not_rank'(순위 병리 아님=소재/관련성,
         진단 종료 — 상한까지 낭비 상향 차단, 파워링크 병리와 동형).
      ③ 무클릭 ∧ 2.5<rank≤4(밴드 도달) → 'stop_observe'(상향 정지·클릭 흐름 관측 — 과열밴드
         진입 금지).
      ④ 무클릭 ∧ 밴드 밖(rank>4 또는 imp=0) ∧ 최근 24h 클릭 흐름 있음(recent_flow_clk>0) →
         'stop_observe'(흐름 살아있으면 이번 사이클 무클릭이어도 상향 정지, codex P1 자정 리셋 수정).
         흐름 확정 불가(flow_available False)도 'stop_observe'(fail-toward-hold).
      ⑤ 무클릭 ∧ 밴드 밖 ∧ 흐름 0 정체 ∧ current_bid≥ceiling → 'capped'(경제성 상한 도달).
      ⑥ 무클릭 ∧ 밴드 밖 ∧ 흐름 0 정체 ∧ 상한 미도달: 과거 클릭 이력(settled_clk>0 또는
         last_probe.had_click) → 'reactivate'([탐색UP·재가동]) / 아니면 'step_up'(적응 스텝 상향).
    """
    if last_probe is None:
        if current_bid >= ceiling:
            return ("capped", f"첫 탐색이나 경제성 상한 이미 도달(현 {current_bid}≥상한 {ceiling}) — 발동 불가·관찰 표기")
        return ("start", "직전 탐색 스텝 없음 — 첫 탐색 시작(적응 스텝)")

    clk = int(since_step_stats.get("clk", 0))
    rank = since_step_stats.get("avg_rank")

    # ① 이번 사이클 클릭 발생 → 상향 정지·정착 ROAS 관측 인계(hold, 영구 아님).
    if clk > 0:
        return ("stop_observe", f"직전 스텝 이후 클릭 발생(clk={clk}) — 상향 정지·정착 ROAS 관측 인계")

    # 무클릭. 순위 관측 가능하면 밴드/병리 판정(과열밴드 진입 금지).
    if rank is not None:
        r = Decimal(str(rank))
        # ② 과열밴드(rank≤2.5)인데 클릭0 지속 → 순위 병리 아님(소재/관련성) 진단 종료.
        if r <= _EXPLORATION_BAND_LOW:
            return (
                "not_rank",
                f"순위 {float(r):.2f}≤{_EXPLORATION_BAND_LOW}인데 클릭0 지속 — 순위 병리 아님(소재/관련성) 진단 종료",
            )
        # ③ 밴드 도달(2.5<rank≤4)·무클릭 → 상향 정지·관측(과열밴드 진입 금지).
        if r <= _EXPLORATION_BAND_HIGH:
            return (
                "stop_observe",
                f"밴드 도달(순위 {float(r):.2f}≤{_EXPLORATION_BAND_HIGH})·무클릭 — 상향 정지·클릭 흐름 관측(과열밴드 진입 금지)",
            )

    # 밴드 밖(rank>4) 또는 imp=0(rank None) · 이번 사이클 무클릭 — 상향 여지 판정.
    # ④ 최근 24h 클릭 흐름 있음 → 상향 정지·관측(흐름 살아있으면 이번 사이클 무클릭이어도 hold;
    #    codex P1 자정 리셋 수정 — 어제 늦은 시각 클릭이 자정 후 사라져 오탐 step_up 되던 것 차단).
    if recent_flow_clk > 0:
        return (
            "stop_observe",
            f"최근 {_EXPLORATION_FLOW_WINDOW_H}h 클릭 흐름 있음(flow={recent_flow_clk}) — 상향 정지·관측(hold)",
        )
    # 흐름 확정 불가(어제 시간별 미가동) → fail-toward-hold(정체 확언 불가 = 안전방향 관측, codex P1).
    if not flow_available:
        return ("stop_observe", "롤링 24h 흐름 확정 불가(어제 시간별 미가동) — 관측 유지(fail-toward-hold)")

    # ⑤ 흐름 0 정체 ∧ 경제성 상한 도달 → 탐색 종료.
    if current_bid >= ceiling:
        return ("capped", f"밴드 밖·흐름0 정체·경제성 상한 도달(현 {current_bid}≥상한 {ceiling}) — 탐색 종료·관찰 표기")

    # ⑥ 흐름 0 정체 ∧ 상한 미도달: 과거 클릭 이력 → 재가동 / 아니면 정상 적응 스텝.
    had_prior_click = settled_clk > 0 or bool(last_probe.get("had_click"))
    if had_prior_click:
        return (
            "reactivate",
            f"[탐색UP·재가동] 과거 클릭 이력(정착 clk={settled_clk})·최근 {_EXPLORATION_FLOW_WINDOW_H}h 클릭0 정체 — 재가동 스텝",
        )
    return ("step_up", f"밴드 밖·흐름0 정체·상한 미도달(현 {current_bid}<상한 {ceiling}) — 적응 스텝 상향")
