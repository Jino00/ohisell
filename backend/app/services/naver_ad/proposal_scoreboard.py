# proposal_scoreboard.py — proposal_scoreboard SA (듀얼모드 스프린트 Phase 6, D-NAO-14 학습루프1)
# 역할(SA): naver_change_log에서 verify_date가 지난(D+14) 실행 건을 찾아 실측 성과(전/후 RPC
#   트렌드)로 outcome(improved/declined/neutral)을 판정하고, 제안유형별 정확도를
#   NaverLearningState(metric=proposal_accuracy)에 롤업한다. 콘솔 "성적표"(D-NAO-14 "정확도
#   상시 공개")의 데이터 소스.
#
#   ★D-NAO-223(M3-b, 2026-08-22): 전/후 RPC 배율은 **분모가 클릭**이라 클릭이 줄면 오른다 —
#   「클릭·매출이 함께 줄어도 매출이 덜 줄었으면 개선」이 되고, 라이브 improved 전건 4/4가
#   실제로 매출 감소였다(ref 90 §2, id 761은 클릭 −68.5%·매출 −48.3%인데 「개선」). 트랙
#   목표(D-NAO-59)는 총이익 **절대액**이므로 목적함수 정합 축을 **나란히** 붙인다:
#   outcome_profit = **총이익 델타**((cf보정매출/BEP) − 비용의 전/후 비교, D-NAO-225).
#   GAVE 점수는 «크기» 축으로 gave_before/gave_after에 계속 저장하되 **판정에는 쓰지
#   않는다** — GAVE엔 비용을 빼는 항이 없어 적자 대상의 지출을 줄인 조치(총이익 증가)를
#   「매출이 줄었다」고 악화로 읽는다(실측: ref 90 정본 4건 전부 총이익 증가인데 GAVE
#   배율은 3건을 declined로 찍었다). 기존 outcome은
#   **불변**이다(§8-Q1 — 「교정 전 채점기가 무엇을 찍었나」가 증거로 남아야 한다, 교훈 #274).
#   ★이 모듈이 gave_score를 쓰는 것은 새 발명이 아니라 **원래 설계**다 — gave_score.py 모듈
#   docstring이 처음부터 "제안 성적표(proposal_scoreboard)·flight_loop 목적함수로 채택"이라
#   적어 두고 배선만 안 되어 있었다.
#
#   ⚠️ dry_run=True(Phase 5 골격, 이번 스프린트는 항상 이 값) 건은 검증 대상에서 제외한다 —
#   실제 입찰이 안 바뀌었으니 "전/후 성과"를 비교해도 우리 판단과 무관한 자연 변동만 보게
#   되고, 그걸 "제안 정확도"로 오귀속하면 거짓 신호가 된다(추정 금지). 실제 집행(dry_run=
#   False)이 열리기 전까지 이 SA는 자연히 대상이 0건 — 계획서 §4-Phase6 "관찰모드에선 데이터
#   없음(정상)"과 일치. predicted_json은 구조화 수치가 아니라 서술 텍스트(expected_effect,
#   P2-S3 plan-eng-review 결정)라 "예측값 대 실측값" 수치 비교는 하지 않는다 — 대신 전/후
#   RPC 트렌드 자체로 outcome을 판정한다(코드가 알 수 있는 유일한 객관적 신호).
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverCampaignSettings, NaverChangeLog, NaverLearningState
from app.services.naver_ad import campaign_target_resolver, diagnosis, gave_score
from app.services.naver_ad.account_diagnosis import LOW_CLICK_THRESHOLD
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

_Q4 = Decimal("0.0001")
METRIC = "proposal_accuracy"
IMPROVED_RATIO = Decimal("1.1")  # 전/후 RPC 비율 ≥10% 개선 — improved (상식적 배수, anomaly_feed 전례 수준)
DECLINED_RATIO = Decimal("0.9")  # ≤10% 악화 — declined. 사이는 neutral.


def _aggregate_entity_metrics(
    db: Session, entity_type: str, entity_id: str, campaign_id: str, date_from: date, date_to: date,
) -> dict:
    """entity_type/entity_id 기준 naver_ad_daily 클릭·전환매출 합계(campaign 레벨은 entity_id
    무시하고 campaign_id로 집계)."""
    q = db.query(
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
        # D-NAO-223(M3-b): cost는 GAVE(=ROAS 페널티×매출)의 원료다. **같은 쿼리에 컬럼
        #   하나를 더하는 것**이라 추가 조회 0회 — 계약 §8-Q3 확정 각주 근거 3.
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
    ).filter(
        NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    )
    if entity_type == "keyword":
        q = q.filter(NaverAdDaily.keyword_id == entity_id)
    elif entity_type == "adgroup":
        q = q.filter(NaverAdDaily.adgroup_id == entity_id)
    else:  # campaign
        q = q.filter(NaverAdDaily.campaign_id == campaign_id)
    clk, direct, indirect, cost = q.first()
    clk = int(clk)
    conv_amt = int(direct) + int(indirect)
    rpc = (Decimal(conv_amt) / Decimal(clk)) if clk > 0 else None
    return {"clk": clk, "conv_amt": conv_amt, "rpc": rpc, "cost": int(cost)}


def _gamma_for(db: Session, campaign_id: str, cache: dict[str, Decimal]) -> Decimal:
    """캠페인 공격성 다이얼 γ(naver_campaign_settings.gamma, D-NAO-2) — 없거나 NULL이면
    DEFAULT_GAMMA=1. **retro_scorer._gamma_for와 같은 패턴·같은 폴백**을 쓴다(같은 식을
    쓰는 두 채점기가 서로 다른 γ를 보면 점수를 비교할 수 없다)."""
    if campaign_id in cache:
        return cache[campaign_id]
    row = db.query(NaverCampaignSettings.gamma).filter(
        NaverCampaignSettings.campaign_id == campaign_id
    ).first()
    gamma = row[0] if row is not None and row[0] is not None else gave_score.DEFAULT_GAMMA
    cache[campaign_id] = gamma
    return gamma


def _bep_for(db: Session, campaign_id: str, cache: dict[str, tuple]) -> tuple:
    """캠페인 손익분기 ROAS와 **그 출처**. 반환 (bep|None, source).

    사다리는 `campaign_target_resolver.resolve_bep_roas`가 단일 정본이다 —
      ① 상품 파생: 그룹 매핑 상품의 bep_roas 매출가중평균(source='product_bep')
      ② 계정 블렌디드 기본값(source='account_default')
      ③ 둘 다 없음(source='unavailable') → 이 행은 새 식으로 판정하지 않는다.
    ★`target_roas`가 아니라 `bep_roas`를 쓴다: models.py의 정의가
      `target_roas = bep_roas × 공격성 배수`라, target을 기준자로 쓰면 **BEP를 넘겨 실제로
      총이익을 낸 구간**(bep ≤ roas < target)이 통째로 「나쁨」으로 떨어진다 — 그 구간이
      정확히 D-NAO-59가 잡으라고 한 자리다.
    ★source를 함께 돌려주는 이유: 계정 블렌디드는 «근사»다(상품BEP 미확보 그룹).
      근사값을 확정값처럼 합산하면 「돈이 됐다」 숫자 자체가 오염된다(계약 §4-B ⑥) —
      그래서 판정과 함께 «어느 렌즈로 쟀는지»를 행에 남긴다.
    """
    if campaign_id in cache:
        return cache[campaign_id]
    try:
        resolved = campaign_target_resolver.resolve_bep_roas(db, campaign_id)
        bep, source = resolved["bep_roas"], resolved["source"]
    except Exception as e:  # noqa: BLE001 — 렌즈 해석 실패는 «판정 보류»지 오판이 아니다
        log.warning("proposal_scoreboard: BEP 해석 실패(새 식 판정 보류): campaign=%s: %s", campaign_id, e)
        bep, source = None, "unavailable"
    cache[campaign_id] = (bep, source)
    return cache[campaign_id]


def _cf_for(db: Session, date_to: date, cache: dict[date, Decimal]) -> Decimal:
    """보정계수 cf(D-NAO-21) — 네이버 convAmt를 실주문매출로 환산. retro_snapshotter가
    `cf_asof`를 얻는 바로 그 경로(diagnosis.correction_factor)를 재사용한다.
    산출 실패는 1(무보정) 폴백 — 그 함수 자체의 계약이다."""
    if date_to in cache:
        return cache[date_to]
    try:
        cf = Decimal(str(diagnosis.correction_factor(db, date_to)["factor"]))
    except Exception as e:  # noqa: BLE001
        log.warning("proposal_scoreboard: 보정계수 산출 실패(1.0 폴백): date_to=%s: %s", date_to, e)
        cf = Decimal("1")
    cache[date_to] = cf
    return cf


def _gross_profit(conv_amt: int, cost: int, *, bep: Decimal, cf: Decimal) -> Decimal:
    """총이익(절대액) = (cf 보정 매출 / BEP) − 비용.

    `bep_roas`는 «본전 ROAS»(공헌이익률의 역수)이므로 `매출/BEP`가 그 매출이 낳은 공헌이익이고,
    거기서 광고비를 빼면 이 창의 총이익이다. **D-NAO-59가 최대화하라고 한 그 양 자체**다.
    """
    return (Decimal(conv_amt) * cf / bep - Decimal(cost)).quantize(_Q4)


def _profit_verdict(before: dict, after: dict, *, bep, gamma: Decimal, cf: Decimal) -> dict:
    """★D-NAO-223 + **D-NAO-225** — 목적함수(총이익 절대액) 정합 판정.

    기존 `outcome`은 전/후 **RPC(매출/클릭) 배율**을 재는데, 분모가 클릭이라 **클릭이 줄면
    RPC는 오른다** — 「클릭·매출이 함께 줄어도 매출이 덜 줄었으면 개선」이 된다(ref 90 §2:
    improved 전건 4/4가 매출 감소, id 761은 클릭 −68.5%·매출 −48.3%인데 「개선」).

    ★★**D-NAO-225(2026-08-22, Jino 확정) — 판정식은 «총이익 델타»다.**
      계약 §8-Q5의 초기 확정값은 «GAVE 배율»(S = min{(roas/bep)^γ,1} × 매출)이었으나, 구현
      실측이 그 재사용을 반증했다: **GAVE에는 비용을 빼는 항이 없다.** 그래서 적자 대상의
      지출을 줄인 조치(= 총이익 증가)를 「매출이 줄었다」는 이유로 악화로 읽는다. ref 90 §2-2의
      정본 4건을 재계산하니 **4건 전부 총이익은 증가**했는데 GAVE 배율은 그중 **3건을
      declined**로 찍었다(BEP 2·3·5 전 구간 동일). Q5 본문이 예고한 *"재사용 불가가 나오면
      멈추고 §8 경로로 올린다"*가 발동한 자리다.
      ⇒ 판정은 `_gross_profit`의 전/후 비교로 하고, **GAVE 점수는 «크기» 축으로 계속 저장한다**
      (gave_before/gave_after) — Q5의 재사용 지시는 그 형태로 살아 있다.

    ★**새 문턱을 만들지 않았다**(계약 §3): 기존 IMPROVED_RATIO/DECLINED_RATIO(±10% 배율 밴드)는
      **부호 있는 양에 옮길 수 없다** — 총이익은 음수가 될 수 있어 「−70,827 → −130」의 배율은
      의미가 없다(0.002배지만 실제로는 큰 개선이다). 그래서 밴드를 억지로 이식하는 대신
      **부호 비교**만 한다: 늘었으면 improved, 줄었으면 declined, 같으면 neutral.
      노이즈 방어는 이미 있는 모수게이트(양쪽 창 clk >= LOW_CLICK_THRESHOLD)가 맡는다.
      ⚠️알려진 한계: 아주 작은 델타도 판정이 된다. 이 축은 «표시 + 자기 롤업»에만 흐르고
      조작 경로가 없어(ref 90 §2-3) 지금은 무해하지만, M3-a 성적표가 이 값을 롤업할 때
      델타 크기를 함께 봐야 한다(그 크기가 gave_before/gave_after와 actual_json의 lens다).

    ★전 창의 BEP 렌즈가 없으면(bep None) 판정하지 않는다 — 억지로 찍지 않는다.
    """
    if bep is None:
        return {"outcome_profit": None, "gave_before": None, "gave_after": None}
    bep = Decimal(str(bep))
    if bep <= 0:
        return {"outcome_profit": None, "gave_before": None, "gave_after": None}

    # 크기 축(Q5 재사용) — 판정에는 쓰지 않지만 「얼마나 큰 건이었나」를 행에 남긴다.
    scored_before = gave_score.compute_gave_score(
        revenue=(Decimal(before["conv_amt"]) * cf), cost=before["cost"], bep_roas=bep, gamma=gamma,
    )
    scored_after = gave_score.compute_gave_score(
        revenue=(Decimal(after["conv_amt"]) * cf), cost=after["cost"], bep_roas=bep, gamma=gamma,
    )

    profit_before = _gross_profit(before["conv_amt"], before["cost"], bep=bep, cf=cf)
    profit_after = _gross_profit(after["conv_amt"], after["cost"], bep=bep, cf=cf)
    if profit_after > profit_before:
        outcome_profit = "improved"
    elif profit_after < profit_before:
        outcome_profit = "declined"
    else:
        outcome_profit = "neutral"

    return {
        "outcome_profit": outcome_profit,
        "gave_before": float(scored_before["score"]),
        "gave_after": float(scored_after["score"]),
    }


def _frozen_lens(change: NaverChangeLog):
    """이 행에 이미 적힌 렌즈를 되살린다. 없거나 불완전하면 None(새로 해석하라는 뜻).

    반환 (bep, bep_source, gamma, cf) — 전부 Decimal/str.
    `actual_json.lens`는 이전 회차가 적어 둔 채점 렌즈다(§8-Q3 각주). 이걸 되살려야
    재시도 회차에서 판정이 흔들리지 않는다(적대 리뷰 1R P1-1).
    """
    raw = getattr(change, "actual_json", None)
    if not raw:
        return None
    try:
        lens = (json.loads(raw) or {}).get("lens") or {}
    except (ValueError, TypeError):  # 손상된 JSON은 없는 것으로 친다(다음 회차가 다시 적는다)
        return None
    bep = lens.get("bep")
    if bep is None:  # 해석 실패로 비어 있던 렌즈는 얼리지 않는다 — 다시 시도한다
        return None
    try:
        return (
            Decimal(str(bep)),
            lens.get("bep_source") or "unavailable",
            Decimal(str(lens.get("gamma", gave_score.DEFAULT_GAMMA))),
            Decimal(str(lens.get("cf", 1))),
        )
    except (ArithmeticError, ValueError, TypeError):
        return None


def evaluate_change(
    db: Session, change: NaverChangeLog, *, today: date | None = None,
    caches: dict | None = None,
) -> dict:
    """change_log 1건의 전/후 성과로 판정. 반환:
    {"outcome", "actual_json", "outcome_profit", "gave_before", "gave_after", "bep_source"}.

    ★두 자를 «나란히» 낸다 — `outcome`은 기존 RPC 배율(불변, §8-Q1), `outcome_profit`은
    목적함수 정합 GAVE 배율(D-NAO-223). 옛 자를 갈아치우지 않는 이유는 「교정 전
    채점기가 무엇을 찍었나」가 증거로 남아야 하기 때문이다(교훈 #274).
    `caches`는 회차 내 렌즈 캐시(γ·BEP·cf) — 없으면 이 호출 한정으로 만든다.

    executed_at 이전 동일 길이 창(before)과 executed_at부터 동일 길이 창(after)을 비교
    (codex 지적 — verify_date를 그대로 after_to로 쓰면 before는 window_days일인데 after는
    window_days+1일이 되어(양끝 포함) 창 길이가 달라 RPC 비교가 편향된다. after도 정확히
    window_days일로 맞춘다 — verify_date 자체를 반드시 마지막 날로 쓸 필요는 없다, 검증
    "기한"일 뿐 관측 창의 끝이어야 하는 건 아니다).
    모수게이트(양쪽 창 모두 clk>=LOW_CLICK_THRESHOLD) 미달이면 outcome=None(판정 보류 —
    억지로 improved/declined를 매기지 않는다).
    """
    today = today or kst_today()
    executed_date = change.executed_at.date() if isinstance(change.executed_at, datetime) else change.executed_at
    verify_date = change.verify_date or today
    window_days = max((verify_date - executed_date).days, 1)

    after_from = executed_date
    after_to = executed_date + timedelta(days=window_days - 1)
    before_from = executed_date - timedelta(days=window_days)
    before_to = executed_date - timedelta(days=1)

    after = _aggregate_entity_metrics(db, change.entity_type, change.entity_id, change.campaign_id, after_from, after_to)
    before = _aggregate_entity_metrics(db, change.entity_type, change.entity_id, change.campaign_id, before_from, before_to)

    caches = caches if caches is not None else {}
    gamma_cache = caches.setdefault("gamma", {})
    bep_cache = caches.setdefault("bep", {})
    cf_cache = caches.setdefault("cf", {})

    # ★★렌즈는 «한 번 정해지면 얼린다»(적대 리뷰 1R P1-1).
    #   왜: `run_daily`의 대상 필터가 `outcome IS NULL`이라, 레거시 판정이 영영 보류되는 행
    #   (예: before 창 conv_amt=0)은 **매일 재시도**된다. 렌즈를 매번 라이브로 다시 뽑으면
    #   `naver_product_bep`가 매일 재산출되는 스냅샷이라 **같은 광고 실적에 대해 판정이
    #   improved↔declined로 뒤집힌다**(재현: bep 0.5 → 3으로 바뀌자 같은 행이 뒤집혔다).
    #   그러면 이 축이 세운 목적("옛 자와 새 자가 나란히 «영구 보존»된다")이 깨진다.
    #   자매 채점기 `retro_scorer`가 `cf_asof`/`bep_asof`를 스냅샷 시점에 얼려 두는 것과 같은
    #   이유이고 같은 처방이다 — 다만 change_log엔 렌즈 «컬럼»이 없으므로 `actual_json.lens`를
    #   그 자리로 쓴다(§8-Q3 각주가 렌즈를 거기 적기로 한 이유가 여기서 값을 한다).
    #   ★단 «해석 실패로 비어 있는» 렌즈는 얼리지 않는다 — 일시적 실패가 unavailable을 영구화
    #   하면 안 되므로, bep이 None인 렌즈는 다음 회차에 다시 시도한다.
    frozen = _frozen_lens(change)
    if frozen is not None:
        bep, bep_source, gamma, cf = frozen
    else:
        bep, bep_source = _bep_for(db, change.campaign_id, bep_cache)
        gamma = _gamma_for(db, change.campaign_id, gamma_cache)
        cf = _cf_for(db, after_to, cf_cache)

    # 모수게이트는 두 자가 «같은» 문턱을 쓴다 — 새 문턱을 만들지 않는다(계약 §3).
    thin = before["clk"] < LOW_CLICK_THRESHOLD or after["clk"] < LOW_CLICK_THRESHOLD
    profit = (
        {"outcome_profit": None, "gave_before": None, "gave_after": None}
        if thin else _profit_verdict(before, after, bep=bep, gamma=gamma, cf=cf)
    )

    actual_json = json.dumps({
        "before": {"clk": before["clk"], "conv_amt": before["conv_amt"], "cost": before["cost"]},
        "after": {"clk": after["clk"], "conv_amt": after["conv_amt"], "cost": after["cost"]},
        # 렌즈를 함께 적는다 — `naver_change_log`엔 retro의 `cf_asof`/`bep_asof` 같은 렌즈
        #   컬럼이 없어서, 이게 없으면 gave 점수를 나중에 되짚을 수 없다(채점 재현성).
        #   컬럼을 4개 더 늘리는 대신 이미 있는 관측 기록 JSON에 additive 키로 남긴다(§8-Q3).
        "lens": {
            "bep": (float(bep) if bep is not None else None),
            "bep_source": bep_source,
            "gamma": float(gamma),
            "cf": float(cf),
        },
    }, ensure_ascii=False)

    base = {"actual_json": actual_json, "bep_source": bep_source, **profit}

    if thin:
        return {"outcome": None, **base}
    if before["rpc"] is None or after["rpc"] is None or before["rpc"] <= 0:
        return {"outcome": None, **base}

    ratio = (after["rpc"] / before["rpc"]).quantize(_Q4)
    if ratio >= IMPROVED_RATIO:
        outcome = "improved"
    elif ratio <= DECLINED_RATIO:
        outcome = "declined"
    else:
        outcome = "neutral"
    return {"outcome": outcome, **base}


def run_daily(db: Session, *, today: date | None = None) -> dict:
    """매일 실행 — verify_date 지난 미검증 실집행(dry_run=False) 건을 판정 + 제안유형별
    정확도(improved 비율) 롤업. dry_run=True 건은 대상 아님(모듈 docstring 참조).
    """
    today = today or kst_today()
    pending = db.query(NaverChangeLog).filter(
        NaverChangeLog.dry_run.is_(False),
        NaverChangeLog.verify_date.isnot(None), NaverChangeLog.verify_date <= today,
        NaverChangeLog.outcome.is_(None),
    ).all()

    verified = 0
    verified_profit = 0
    caches: dict = {}
    for change in pending:
        result = evaluate_change(db, change, today=today, caches=caches)
        change.actual_json = result["actual_json"]
        # ★새 축은 «별도 컬럼»에만 쓴다 — 기존 outcome을 덮지 않는다(§8-Q1).
        #   두 축은 서로 독립이다: 한쪽이 판정 보류여도 다른 쪽은 찍힐 수 있다.
        change.gave_before = result["gave_before"]
        change.gave_after = result["gave_after"]
        change.bep_source = result["bep_source"]
        if result["outcome_profit"] is not None:
            # ★«처음» 찍힐 때만 센다 — 레거시 outcome이 영영 None인 행은 매일 재시도되므로
            #   무조건 세면 같은 물리적 행이 날마다 중복 계상된다(적대 리뷰 1R P2).
            if change.outcome_profit is None:
                verified_profit += 1
            change.outcome_profit = result["outcome_profit"]
        if result["outcome"] is not None:
            change.outcome = result["outcome"]
            verified += 1
        # outcome=None(모수 미달)은 actual_json만 채우고 다음 회차 재시도 대상으로 남김
    db.commit()

    accuracy_by_action = _rollup_accuracy(db)
    for action, stats in accuracy_by_action.items():
        confidence = min(Decimal(1), Decimal(stats["n"]) / Decimal(10))
        row = db.query(NaverLearningState).filter(
            NaverLearningState.scope == "action_type", NaverLearningState.scope_key == action,
            NaverLearningState.metric == METRIC,
        ).first()
        if row is None:
            row = NaverLearningState(scope="action_type", scope_key=action, metric=METRIC)
            db.add(row)
        row.current_value = stats["improved_ratio"]
        row.sample_n = stats["n"]
        row.confidence = confidence.quantize(_Q4)
    db.commit()

    log.info(
        "proposal_scoreboard: pending=%d verified=%d verified_profit=%d",
        len(pending), verified, verified_profit,
    )
    return {
        "pending": len(pending), "verified": verified,
        # D-NAO-223: 새 축이 실제로 몇 건을 찍었나. 0이면 「배선은 됐는데 아무것도 안 재고
        #   있다」는 뜻이라 크론 로그에서 바로 보인다(교훈 #318 — 카운터가 있어야 침묵을 본다).
        "verified_profit": verified_profit,
        "accuracy_by_action": accuracy_by_action,
    }


def _rollup_accuracy(db: Session) -> dict:
    """action별 outcome 분포에서 improved 비율 산출(전체 대상=improved+declined+neutral,
    outcome=None인 미판정 건은 분모에서 제외 — 정직 경계). dry_run=False만 대상(codex 지적 —
    run_daily의 pending 조회는 이미 dry_run=False로 걸렀지만, 이 롤업은 change_log 테이블
    전체를 다시 훑으므로 dry_run=True 건에 수동/과거 경로로 outcome이 채워져 있어도 정확도
    통계에 섞이지 않도록 여기서도 동일 필터를 명시한다)."""
    rows = db.query(NaverChangeLog.action, NaverChangeLog.outcome).filter(
        NaverChangeLog.outcome.isnot(None), NaverChangeLog.dry_run.is_(False),
    ).all()
    by_action: dict[str, dict[str, int]] = {}
    for action, outcome in rows:
        stats = by_action.setdefault(action, {"improved": 0, "n": 0})
        stats["n"] += 1
        if outcome == "improved":
            stats["improved"] += 1
    return {
        action: {"n": s["n"], "improved_ratio": (Decimal(s["improved"]) / Decimal(s["n"])).quantize(_Q4)}
        for action, s in by_action.items()
    }


# ── 읽기 경로 — 화면의 「결과 칸」이 쓸 값 (설계서 122 §4-3·§4-4) ────────────────────
#   ★여기 두는 이유: 산식(`_gross_profit`)과 얼린 렌즈(`_frozen_lens`)가 이 모듈에 산다.
#     화면 쪽에서 다시 계산하면 **산식이 두 벌**이 되고, 렌즈를 안 쓰면 채점 «당시»와
#     다른 숫자가 나온다(그게 `_frozen_lens`가 존재하는 이유 그대로다).
#   ★**재채점하지 않는다** — 이 함수는 DB를 안 보고 행에 이미 적힌 것만 되살린다.
_PROFIT_STATE_NOTE = {
    "dry_run": "채점 대상 아님 — 연습(dry_run)이라 계정에 안 나갔다",
    "no_lens": "판정 못 함 — 이 행엔 BEP 렌즈가 없다(총이익을 잴 자가 없다)",
    "thin": "판정 보류 — 모수 미달(전·후 창의 클릭이 문턱 미만)",
}


def read_profit_delta(change: NaverChangeLog) -> dict:
    """채점된 행에서 **총이익 델타를 금액으로 되살린다**(읽기 전용).

    ★자 자백을 함께 낸다(D-NAO-230 — *"자의 가정·창을 성적과 반드시 병기한다"*).
      이 금액은 보정계수 **점추정**(북극성 §3 구간 [하한, 점추정]의 위쪽 끝)으로 잰 값이다.
      `actual_json.lens`엔 점추정만 얼려져 있어 **「하한으로도 흑자인가」는 이 행에서 못 묻는다**
      — 그래서 `lens.interval_low_available=False`로 «못 한다»고 말한다(지어내지 않는다).
    """
    legacy = {
        "outcome": change.outcome,
        "label": "교정 전 자 — 증거용",
        "note": "전/후 RPC(매출/클릭) 배율. 분모가 클릭이라 클릭·매출이 함께 줄어도 「개선」이 될 수 있다",
    }
    base = {
        "delta": None, "before": None, "after": None,
        "delta_high": None, "scored_by": None, "sign_flips": False,
        "verdict": None, "lens": None, "window": None, "legacy": legacy,
    }

    if change.dry_run:
        return {**base, "state": "dry_run", "note": _PROFIT_STATE_NOTE["dry_run"]}

    raw = getattr(change, "actual_json", None)
    if not raw:
        # 아직 채점기가 안 다녀갔다 — 0도 「—」도 아니고 «언제 채워지는가»를 말한다(§4-4).
        when = change.verify_date.isoformat() if change.verify_date else None
        return {
            **base, "state": "pending",
            "note": (f"채점 전 · D+14 · {when}부터" if when else "채점 전 · 검증 예정일 미정"),
            "scored_from": when,
        }
    try:
        actual = json.loads(raw) or {}
    except (ValueError, TypeError):
        return {**base, "state": "no_lens", "note": _PROFIT_STATE_NOTE["no_lens"]}

    lens = _frozen_lens(change)
    if lens is None:
        return {**base, "state": "no_lens", "note": _PROFIT_STATE_NOTE["no_lens"]}
    bep, bep_source, _gamma, cf = lens

    before, after = actual.get("before") or {}, actual.get("after") or {}
    lens_out = {
        "cf": float(cf), "bep": float(bep), "bep_source": bep_source,
        # ★기본값은 «있는 그대로»다 — 표시 전용 소비처의 관례가 그것이다(`pao_scope_roster.
        #   _profit_band`, Jino 2026-08-24 *"있는 그대로를 보여줘야 하는거 아니야?"*).
        "basis": "있는 그대로(보정 없음)",
        "high_basis": "보정계수 점추정(구간의 위쪽 끝) — 채널 매출 전액을 광고 공으로 돌리는 가정",
        "interval_low_available": False,
    }
    window = _read_window(change)

    if change.outcome_profit is None:
        # 채점기가 **일부러** 판정을 보류한 자리다. 여기에 금액을 그리면 화면이 판정을 지어낸다.
        return {**base, "state": "thin", "note": _PROFIT_STATE_NOTE["thin"],
                "lens": lens_out, "window": window}

    # ★자를 **두 개** 낸다(ref 93 §1 행 9 · D-NAO-230). 하나만 실으면 그 하나가 사실처럼
    #   읽히는데, 이 자는 끝값에 따라 **부호가 갈린다** — 실측: 계정 30일 총이익이
    #   보정 적용 +5,963,568원 ↔ 미적용 −234,545원. 상한만 쓰면 화면이 가장 낙관적으로 보인다.
    #   하한은 렌즈에 `factor_low`가 안 얼려져 있어 **못 낸다**(지어내지 않는다).
    one = Decimal(1)
    try:
        raw_before = _gross_profit(int(before["conv_amt"]), int(before["cost"]), bep=bep, cf=one)
        raw_after = _gross_profit(int(after["conv_amt"]), int(after["cost"]), bep=bep, cf=one)
        hi_before = _gross_profit(int(before["conv_amt"]), int(before["cost"]), bep=bep, cf=cf)
        hi_after = _gross_profit(int(after["conv_amt"]), int(after["cost"]), bep=bep, cf=cf)
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return {**base, "state": "no_lens", "note": _PROFIT_STATE_NOTE["no_lens"],
                "lens": lens_out, "window": window}

    raw_delta = int((raw_after - raw_before).to_integral_value())
    high_delta = int((hi_after - hi_before).to_integral_value())
    return {
        "state": "scored",
        # 기본값 = 있는 그대로. 화면의 첫 숫자가 이것이다.
        "delta": raw_delta,
        "before": int(raw_before.to_integral_value()),
        "after": int(raw_after.to_integral_value()),
        # 상한 가정으로 잰 같은 양 — 채점기가 **판정에 쓴** 자다.
        "delta_high": high_delta,
        "scored_by": "high",
        # ★자 선택이 결론을 바꾸는 행. 이게 §7이 경계하는 「부푼 자 위의 판정」이
        #   사람 눈에 처음 닿는 자리다.
        "sign_flips": (raw_delta > 0) != (high_delta > 0) and 0 not in (raw_delta, high_delta),
        "verdict": change.outcome_profit,
        "note": None,
        "lens": lens_out,
        "window": window,
        "legacy": legacy,
    }


def _read_window(change: NaverChangeLog) -> dict | None:
    """행에 적힌 날짜로 전·후 창을 되살린다 — `evaluate_change`가 쓴 그 산식 그대로.

    ★창을 화면이 따로 지어내면 「화면이 말하는 창」과 「채점이 쓴 창」이 두 벌이 된다.
    """
    if change.executed_at is None or change.verify_date is None:
        return None
    executed = change.executed_at.date() if isinstance(change.executed_at, datetime) else change.executed_at
    days = max((change.verify_date - executed).days, 1)
    return {
        "days": days,
        "before_from": (executed - timedelta(days=days)).isoformat(),
        "before_to": (executed - timedelta(days=1)).isoformat(),
        "after_from": executed.isoformat(),
        "after_to": (executed + timedelta(days=days - 1)).isoformat(),
    }
