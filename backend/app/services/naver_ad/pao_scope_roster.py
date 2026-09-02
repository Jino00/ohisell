# pao_scope_roster.py — PAO 스코프 대시보드 하니스 (D-NAO-244)
#
# Jino 원문 2026-08-24: *"ohisell에 PAO 메뉴를 만들어서 어떤 캠페인 - 광고그룹 을 돌릴지,
# 그 성과는 어떻게 나오는지 보여주는 대시보드를 같이 만들자"*
#
# 두 질문에 한 화면으로 답한다:
#   ①어떤 캠페인·광고그룹을 엔진이 돌리는가 — naver_adgroup_scope(역할·enabled) + 캠페인 축
#   ②그 성과는 어떻게 나오는가 — 광고비·클릭·전환·ROAS·**총이익**
#
# ★기존 부품 재조합이다(신규 산식 0):
#   · 집계        metrics_aggregator.aggregate(grain="adgroup")  — campaign_id·adgroup_id 동시 반환
#   · 이름        NaverEntity(entity_type='adgroup')             — perf_campaign_harness 관례
#   · BEP 사다리  exploration.resolve_exploration_bep_roas       — 그룹→캠페인→계정 (공개 래퍼)
#   · 보정계수    diagnosis.correction_factor                    — profit_scorecard와 같은 소스
#   · 총이익 산식 (Σconv_amt × factor) ÷ bep_roas − Σcost        — profit_scorecard._window_profit과 동일
#
# ★「BEP 해석 불가면 숫자를 만들어내지 않는다」(profit_scorecard §4-1 '숫자 조작 금지')를 그대로
#   따른다 — gross_profit=None + profit_status 사유. 0원과 «모름»은 다른 값이다.
#
# ★왜 «횡단»이 필요했나: perf_campaign_harness.build_campaign은 캠페인 «1개»만 처리한다.
#   「어떤 캠페인-광고그룹을 돌릴지」는 여러 캠페인을 나란히 놓고 고르는 질문이라 그 함수로는
#   원리적으로 답이 안 된다. 상태 배지(group_state_badge)는 캠페인 상세의 소관으로 남긴다 —
#   이 화면의 질문은 「엔진이 뭘 하고 있나」가 아니라 「무엇을 맡길까」다.
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverCampaignSettings, NaverEntity
from app.services.naver_ad import (
    adgroup_scope, exploration, metrics_aggregator, probe_cell_aggregate,
)
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_ad.diagnosis import correction_factor
from app.utils.kst import kst_today

DEFAULT_WINDOW_DAYS = 21
MAX_WINDOW_DAYS = 180

# BEP를 해석 못 한 행의 사유 — 화면이 «0원»으로 그리지 않게 하는 라벨
PROFIT_STATUS_OK = "ok"
PROFIT_STATUS_BEP_UNKNOWN = "bep_unknown"
# ★D-NAO-267 (계약 §4-A T1 = ref 65 S2-④ · 교란축 X9): 창 안에 «평시» 관측이 하나도 없는
#   그룹. 확정 밴드값 대신 이 라벨을 낸다 — 아래 _baseline_days_by_adgroup 참조.
PROFIT_STATUS_RAMP_UP = "ramp_up"

# 환경 층 2층(평시 / 주말+공휴일) — 계약 §4-B⑤ 「그 이상 쪼개기 금지(첫 라운드)」.
# 판정은 probe_cell_aggregate.env_cell_of_date가 유일 출처다(두 벌이 되면 같은 날짜가
# 표면마다 다른 칸에 들어간다). weekend·holiday를 따로 내는 건 ref 63이 둘을 «따로»
# 확정했기 때문이지 3층으로 쓰라는 뜻이 아니다.
DAY_CLASSES = ("weekday", "weekend", "holiday")

# 날짜 grain 분리 집계의 가법 키 — ROAS는 «비율»이라 여기 안 들어간다(ref 63 §1-1).
_SPLIT_KEYS = ("cost", "imp", "clk", "conv_amt")


def _round_won(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _clean(name: str | None) -> str:
    """캠페인·그룹 이름 앞머리의 표시용 기호(●○◎)와 공백을 털어낸다."""
    return (name or "").lstrip("●○◎ ").strip()


def _profit(conv_amt: int, cost: int, *, factor: Decimal, bep_roas: Decimal | None) -> tuple[int | None, str]:
    """총이익 절대액 — profit_scorecard와 같은 산식.

    bep_roas가 없으면 (None, 사유)를 돌려준다. **추정하지 않는다** — 원가 미확인 상품의 이익을
    지어내면 그 숫자가 그대로 판정에 쓰인다.
    """
    if bep_roas is None or bep_roas <= 0:
        return None, PROFIT_STATUS_BEP_UNKNOWN
    corrected = Decimal(conv_amt) * factor
    return _round_won(corrected / bep_roas - Decimal(cost)), PROFIT_STATUS_OK


def _baseline_days_by_adgroup(
    db: Session, date_from: date, date_to: date, campaign_id: str | None,
) -> dict[str, int]:
    """★그룹별 «평시» 관측일 수 — 교란축 X9(신규 그룹 램프업) 판정의 원료.

    ## 판정을 발명하지 않았다 (계약 §4-B⑤ 「없으면 ref 63 §10의 「baseline 부재」 판정을
    재사용하고 그 사실을 기록한다. 발명 금지」)

    ref 63 §10 원문의 기제: *"baseline 잔차법(§1-2)은 「그룹의 평시 체질」이 존재한다는 전제
    위에 서 있다. 신규 그룹은 ⓐ평시 표본이 0이라 b_g 자체가 정의되지 않고 ⓑ초기 구간엔
    품질지수 미성숙·소재 학습·입찰 탐색이 겹쳐 **어떤 그룹이든 체질과 무관하게 나쁠 수
    있다**."* → 그래서 「평시 관측 0일」이 곧 「밴드 확정값을 낼 수 없음」이다.

    코드·문서 어디에도 X9 판정 로직은 없었다(착수 실측: backend 전체 grep 0건). 그래서 위
    판정을 그대로 옮긴다 — `reg_tm` 기준 그룹 나이 축은 ref 63 §10이 *"다음 라운드 후보"*로
    적은 **미확정** 설계라 판정에 쓰지 않는다(§3 미확정은 층 승격 금지).

    ## ★좁게 잡았다 — 그 사실을 여기 적어 둔다

    ref 63 §1-2의 «평시»는 `평일 ∧ 단독공휴일 아님 ∧ 명절연휴 아님 ∧ 휴가창 밖 ∧
    launch_phase=none ∧ data_gap 아님 ∧ mature`다. 이 함수는 그중 **확정 축만** 쓴다
    (평일 ∧ 공휴일 아님 = env_cell_of_date == "weekday"). 휴가창·출시창은 계약 §3이
    「미확정 환경은 층 승격 금지」로 막았기 때문이다.

    대가를 정직하게 적는다: ref 63이 X9를 발견한 실제 사례(TPU3)는 **휴가창(7/20~8/15)이
    평시를 통째로 먹어서** 평시 0건이 된 경우다. 휴가창 축을 안 쓰는 이 판정은 그 사례를
    **재현하지 못한다** — 즉 우리 라벨은 ref 63보다 **적게** 붙는다. 놓치는 쪽이지 없는 걸
    지어내는 쪽은 아니다. 계약 §4-C S2-④가 *"판정 시점 X9 그룹 0개면 «해당 없음(라이브
    사례 0)»으로 적고 달성 주장하지 않는다"*로 이 결과를 미리 허용해 뒀다.
    """
    q = (
        db.query(NaverAdDaily.adgroup_id, NaverAdDaily.ad_date)
        .filter(
            NaverAdDaily.ad_date >= date_from,
            NaverAdDaily.ad_date <= date_to,
            NaverAdDaily.adgroup_id.isnot(None),
            # 집계 정본 규칙 — sentinel 행을 섞으면 같은 날이 두 번 세어진다
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .distinct()
    )
    if campaign_id:
        q = q.filter(NaverAdDaily.campaign_id == campaign_id)

    out: dict[str, int] = {}
    for gid, ad_date in q.all():
        if ad_date is None:
            continue
        if probe_cell_aggregate.env_cell_of_date(ad_date) == "weekday":
            out[gid] = out.get(gid, 0) + 1
    return out


def day_class_split(db: Session, date_from: date, date_to: date,
                    campaign_id: str | None = None, *, date_agg: dict | None = None) -> dict:
    """★평시/주말/공휴일 **날짜 grain** 분리 집계 (D-NAO-267 · 계약 §4-A T1의 「밴드 판정 표면」).

    ## 왜 여기인가 (적대 리뷰 1R P1-1의 처방)

    초판은 이 분리를 retro 성적표에만 넣었다. 그런데 retro의 축은 `asof_date` 기준
    `verdict_d{h}`이고 성과는 **사후창(asof+1..asof+h)**에서 난다 — 7일 연속 구간은 어느
    발신일이든 주말을 2일 포함하므로 **d7은 분리가 원리적으로 0**이고 d3은 부분적으로
    뒤집힌다(`retro_rollup.day_class_rollup` docstring의 표).

    ref 63의 축은 `ad_profit_{g,d}` — **날짜 d 당일**이다(§1-2). 그 질문에 답하려면 날짜
    grain이 실재하는 곳에서 갈라야 하고, 이 로스터가 그 자리다. 계약 §4-A T1이 산출물로
    「성적표·retro·**밴드 판정 표면**에 분리 집계」 셋을 나열한 이유가 이것이다 — 셋 중
    이 표면만 ref 63과 grain이 같다.

    ## 무엇을 재사용했나 (계약 §2-3 — 새 집계 0)

    `metrics_aggregator.aggregate(grain="date")`가 이미 날짜별 행을 준다. 그걸 day_class로
    접기만 한다 — 새 쿼리·새 산식·새 상수 **0**. 그래서 이 분리의 합은 같은 집계기의
    `totals`와 **정의상** 맞아야 하고, `identity.ok`가 그걸 실제로 검산한다.

    ## 무엇을 «안» 하나

    · **보정 아님, 분리 표기다.** 주말분을 빼거나 계수를 곱하지 않는다.
    · **총이익을 칸별로 내지 않는다.** BEP가 그룹마다 다르고 날짜 grain엔 그 조인이 없다 —
      지어내느니 안 낸다(§2-3 「재사용이 불가능하면 멈추고 기록한다」). 칸별 ROAS는 그
      칸의 합에서 바로 나오므로 BEP(1.711)와 비교하면 사람이 읽을 수 있다.
    · **연휴·휴가창·출시축은 칸이 없다**(§3 — 미확정 환경은 층 승격 금지).
    """
    agg = date_agg if date_agg is not None else metrics_aggregator.aggregate(
        db, date_from, date_to, grain="date", campaign_filter=campaign_id
    )

    out: dict = {dc: {"days": 0, **{k: 0 for k in _SPLIT_KEYS}} for dc in DAY_CLASSES}
    for row in agg["rows"]:
        d = row["ad_date"]
        d = date.fromisoformat(d) if isinstance(d, str) else d
        cell = out[probe_cell_aggregate.env_cell_of_date(d)]
        cell["days"] += 1
        for k in _SPLIT_KEYS:
            cell[k] += int(row.get(k) or 0)

    for dc in DAY_CLASSES:
        cell = out[dc]
        # 칸별 ROAS — 그 칸의 합에서 «직접» 낸다. 칸끼리 더하면 안 되는 값이라
        # identity 검산 대상이 아니다(ref 63 §1-1: 비율은 가법이 아니다).
        cell["roas"] = round(cell["conv_amt"] / cell["cost"], 4) if cell["cost"] else None

    totals = agg["totals"]
    summed = {k: sum(out[dc][k] for dc in DAY_CLASSES) for k in _SPLIT_KEYS}
    out["identity"] = {
        "total": {k: int(totals.get(k) or 0) for k in _SPLIT_KEYS},
        "sum_of_parts": summed,
        "ok": all(summed[k] == int(totals.get(k) or 0) for k in _SPLIT_KEYS),
        "note": "평시+주말+공휴일 = 전체 (ref 63 §1-2 검산과 같은 방식·같은 grain)",
    }
    out["basis"] = "ad_date (성과 발생일) — ref 63 §1-2와 같은 grain"
    out["reference"] = (
        "ref 63 §4-1 확정치: 주말 Σexcess −8,020,470원(30,606 group-day) · "
        "공휴일 −915,912원(4,547 group-day). 둘 다 홀드아웃 게이트 통과·부호 안정. "
        "여기 수치는 그 확정치가 아니라 이 창의 실측 분리다 — 보정하지 않았다."
    )
    return out


def _profit_band(conv_amt: int, cost: int, *, bep_roas: Decimal | None,
                 factor_low: Decimal, factor_high: Decimal) -> dict:
    """★총이익을 «하나»가 아니라 «있는 그대로 + 구간»으로 낸다 (Jino 지시 2026-08-24).

    Jino 원문: *"보정계수(1.3016)를 왜 쓰는거야? 있는 그대로를 보여줘야 하는거 아니야?"*

    ## 왜 단일값이 위험했나

    보정계수는 `실주문매출 ÷ 네이버 convAmt`인데, **분자에 광고 귀속 조인이 없다**
    (`channel_id==6` 필터뿐, `diagnosis.correction_factor` 주석이 자백). 즉 이 계수는
    **「네이버 채널 매출 100%를 광고가 견인했다」는 가정과 수학적으로 동치**다 — 광고를 안
    켰어도 팔렸을 매출까지 광고 공으로 돌린다. 그래서 D-NAO-230이 점추정을 버리고 구간으로
    바꿨고, ref 93 §1 행 9는 «표시 전용» 소비처를 **「구간 양끝 병기」 1순위**로 지정했다.

    ★초판이 `factor_high`(상한) 하나만 실었다. 상한은 「후보 선정·게이트」용 끝인데 표시에
    쓰면 총이익이 **가장 낙관적으로** 보인다 — 실제로 TPU 21일이 무보정 −864,081원인데
    상한으로는 +557,591원이 되어 **부호가 뒤집혔다**.

    ## 무엇을 내나

      profit        — ★**있는 그대로**(보정 없음). 네이버가 준 convAmt를 그대로 쓴 값.
      profit_low    — × factor_low  (D-NAO-234 하한 — inflowPath 「광고>」5종 ÷ direct 근거)
      profit_high   — × factor_high (상한 — 채널 매출 전액 귀속 «가정»)

    셋을 다 실어야 화면이 «얼마나 모르는지»를 같이 보일 수 있다. 하나만 실으면 그 하나가
    사실처럼 읽힌다.
    """
    raw, status = _profit(conv_amt, cost, factor=Decimal(1), bep_roas=bep_roas)
    low, _ = _profit(conv_amt, cost, factor=factor_low, bep_roas=bep_roas)
    high, _ = _profit(conv_amt, cost, factor=factor_high, bep_roas=bep_roas)
    return {
        "gross_profit": raw,            # ★있는 그대로가 기본값이다
        "gross_profit_low": low,
        "gross_profit_high": high,
        "profit_status": status,
    }


def build_roster(
    db: Session,
    *,
    campaign_id: str | None = None,
    days: int = DEFAULT_WINDOW_DAYS,
    date_from_in: date | None = None,
    date_to_in: date | None = None,
    today: date | None = None,
) -> dict:
    """캠페인 × 광고그룹 횡단 로스터.

    campaign_id를 주면 그 캠페인만, 안 주면 창 안에 집행이 있었던 전 캠페인.
    창은 D-0(오늘) 제외 — 당일 전환이 정착 전이라 총이익이 과소로 보인다(창 관례).

    창을 정하는 길은 둘이다:
      ① `days` — 종전 경로. 「어제로 끝나는 N일」.
      ② `date_from_in`/`date_to_in` — 화면이 날짜를 직접 고른 경우(공용 `PeriodRangeBar`).
    ★②가 필요한 이유: 화면에 날짜 입력을 주면서 서버가 `days`만 받으면 «고른 날짜»와
      «실제 조회 창»이 갈라진다 — 사용자는 자기가 고른 구간을 봤다고 믿는데 아니다.
    ★오늘(D-0)을 고르면 **자르고 그 사실을 말한다**(`window.clamped`·`window.note`).
      조용히 자르면 「내가 고른 날짜가 안 나왔다」가 되고, 안 자르면 총이익이 과소로 나온다.
    """
    today = today or kst_today()
    latest = today - timedelta(days=1)          # 창 관례상 마지막으로 쓸 수 있는 날
    clamped = False
    note: str | None = None

    if date_from_in is not None or date_to_in is not None:
        date_to = date_to_in or latest
        date_from = date_from_in or (date_to - timedelta(days=DEFAULT_WINDOW_DAYS - 1))
        if date_to > latest:
            clamped = True
            note = (
                f"{date_to.isoformat()}까지 고르셨지만 오늘({today.isoformat()})은 전환이 아직 "
                f"정착 전이라 총이익이 실제보다 적게 보입니다 — {latest.isoformat()}까지로 "
                f"보여드립니다."
            )
            date_to = latest
        if date_from > date_to:                 # 뒤집힌 입력은 지어내지 않고 바로잡고 말한다
            clamped = True
            note = (note + " " if note else "") + "시작일이 종료일보다 뒤여서 하루짜리 창으로 봅니다."
            date_from = date_to
        span = (date_to - date_from).days + 1
        if span > MAX_WINDOW_DAYS:
            clamped = True
            note = (note + " " if note else "") + f"창 상한 {MAX_WINDOW_DAYS}일까지만 봅니다."
            date_from = date_to - timedelta(days=MAX_WINDOW_DAYS - 1)
        days = (date_to - date_from).days + 1
    else:
        days = max(1, min(int(days), MAX_WINDOW_DAYS))
        date_to = latest
        date_from = date_to - timedelta(days=days - 1)

    # 보정계수 — profit_scorecard와 같은 소스(fail-open: 산출 불가면 1.0 무보정)
    # ★구간 양끝을 «둘 다» 가져온다(Jino 지시 2026-08-24 — 표시엔 단일 보정값을 쓰지 않는다).
    #   하한 = D-NAO-234(inflowPath 「광고>」5종 ÷ direct 근거) · 상한 = 채널 매출 전액 귀속 가정.
    try:
        factor_info = correction_factor(db, date_to)
        factor_low = Decimal(str(factor_info.get("factor_low") or 1))
        factor_high = Decimal(str(factor_info.get("factor_high") or 1))
        factor_source = factor_info.get("source")
    except Exception:  # noqa: BLE001 — 보정계수 실패가 로스터 전체를 죽이지 않는다
        factor_low = factor_high = Decimal(1)
        factor_source = "unavailable"

    agg = metrics_aggregator.aggregate(
        db, date_from, date_to, grain="adgroup", campaign_filter=campaign_id
    )
    rows = [r for r in agg["rows"] if r.get("adgroup_id")]

    # D-NAO-267: X9(램프업) 판정 원료. 창·필터를 집계와 «똑같이» 맞춘다 — 어긋나면
    # 「집계엔 있는데 baseline은 0」인 유령 램프업이 생긴다.
    baseline_days_map = _baseline_days_by_adgroup(db, date_from, date_to, campaign_id)

    campaign_ids = sorted({r["campaign_id"] for r in rows})
    if campaign_id and campaign_id not in campaign_ids:
        campaign_ids.append(campaign_id)  # 창 안 집행이 없어도 스코프는 보여야 한다

    # 이름 — 캠페인·광고그룹 한 번에
    entities = {
        (e.entity_type, e.entity_id): e
        for e in db.query(NaverEntity)
        .filter(NaverEntity.entity_type.in_(("campaign", "adgroup")))
        .all()
    }
    settings = {
        s.campaign_id: s
        for s in db.query(NaverCampaignSettings)
        .filter(NaverCampaignSettings.campaign_id.in_(campaign_ids))
        .all()
    } if campaign_ids else {}
    scope_map = adgroup_scope.scope_rows_for_campaigns(db, campaign_ids)

    by_campaign: dict[str, list[dict]] = {}
    for r in rows:
        by_campaign.setdefault(r["campaign_id"], []).append(r)

    # ★BEP 사다리의 «요청 단위» 메모(적대 리뷰 P2-2 상환) — 407그룹 × 3 tier 반복 조회로
    #   prod 실측 10.1초가 나왔다. 캠페인·계정 tier는 그룹마다 같은 값이라 한 번만 구하면 된다.
    #   사다리 자체는 exploration에 한 벌로 두고 여기선 메모만 넘긴다(두 벌이 되면 갈라진다).
    #   수명은 이 함수 호출 하나 — 다음 요청은 새 dict라 stale BEP를 물고 있지 않는다.
    bep_cache: dict = {}

    campaigns: list[dict] = []
    for cid in campaign_ids:
        s = settings.get(cid)
        optimizer = s.optimizer if s else "none"          # 설정 행 없음 = none(harness와 같은 시맨틱)
        auto_operate = bool(s.auto_operate) if s else False
        scope_rows = scope_map.get(cid, [])
        scope_by_group = {sr["adgroup_id"]: sr for sr in scope_rows}

        adgroups: list[dict] = []
        c_cost = c_clk = c_imp = c_conv_amt = 0
        c_profit_sum = c_profit_low_sum = c_profit_high_sum = 0
        c_profit_known = False
        c_ramp_up = 0
        for r in by_campaign.get(cid, []):
            gid = r["adgroup_id"]
            cost = int(r.get("cost") or 0)
            conv_amt = int(r.get("conv_amt") or 0)
            bep = exploration.resolve_exploration_bep_roas(db, cid, gid, cache=bep_cache)
            band = _profit_band(conv_amt, cost, bep_roas=bep,
                                factor_low=factor_low, factor_high=factor_high)
            # ★X9 램프업 — 계약 §4-C S2-④ 원문: *"신규 그룹(X9) 라벨이 붙은 그룹은 밴드
            #   확정값 «대신» 「램프업」 표기"*. 그래서 값을 **덮어써서 지운다**(옆에 붙이는
            #   게 아니다) — 확정값을 남겨 두면 화면이 그걸 집어 들고, 라벨은 장식이 된다.
            #   평시 체질이 없는 그룹의 밴드값은 «체질»이 아니라 초기 구간 잡음이다(ref 63 §10).
            #
            # ★★사유 우선순위 — bep_unknown이 ramp_up을 «이긴다».
            #   둘 다 「확정값 없음」이지만 성격이 다르다: bep_unknown은 **애초에 못 재는**
            #   것(상품 원가 미연결 — 사람이 고쳐야 풀린다)이고 ramp_up은 **재도 의미가
            #   없는** 것(시간이 지나면 저절로 풀린다). 램프업을 위에 씌우면 평일이 지나
            #   라벨이 풀린 «뒤에야» 원가 미연결을 알게 된다 — 두 번 놀란다. 그래서 더 깊은
            #   막힘을 먼저 말한다.
            baseline_days = baseline_days_map.get(gid, 0)
            if baseline_days == 0 and band["profit_status"] == PROFIT_STATUS_OK:
                band = {
                    "gross_profit": None,
                    "gross_profit_low": None,
                    "gross_profit_high": None,
                    "profit_status": PROFIT_STATUS_RAMP_UP,
                }
                c_ramp_up += 1
            sr = scope_by_group.get(gid)
            e = entities.get(("adgroup", gid))
            adgroups.append({
                "adgroup_id": gid,
                "name": _clean(e.name if e else None) or "이름 없는 그룹",
                "status": (e.status if e else None),
                # ★스코프 — 이 화면의 첫 질문("무엇을 맡길까")에 답하는 필드
                "in_scope": bool(sr and sr["enabled"]),
                "scope_role": (sr or {}).get("role"),
                "scope_enabled": (sr or {}).get("enabled"),
                "cost": cost,
                "imp": int(r.get("imp") or 0),
                "clk": int(r.get("clk") or 0),
                "conv_amt": conv_amt,
                "roas": r.get("roas_naver"),
                "bep_roas": float(bep) if bep is not None else None,
                # ★램프업 판정의 «근거 숫자»를 라벨과 함께 낸다 — 0이면 왜 확정값이 없는지가
                #   그 자리에서 읽힌다. 라벨만 있으면 「왜?」가 코드를 열어야 나온다.
                "baseline_days": baseline_days,
                **band,
            })
            c_cost += cost
            c_imp += int(r.get("imp") or 0)
            c_clk += int(r.get("clk") or 0)
            c_conv_amt += conv_amt
            if band["gross_profit"] is not None:
                c_profit_sum += band["gross_profit"]
                c_profit_low_sum += band["gross_profit_low"]
                c_profit_high_sum += band["gross_profit_high"]
                c_profit_known = True

        # 창 안 집행이 없는 스코프 그룹도 보여준다 — 0은 «없는 것»이 아니다(D-47-h).
        for sr in scope_rows:
            if sr["adgroup_id"] in {a["adgroup_id"] for a in adgroups}:
                continue
            e = entities.get(("adgroup", sr["adgroup_id"]))
            adgroups.append({
                "adgroup_id": sr["adgroup_id"],
                "name": _clean(e.name if e else None) or "이름 없는 그룹",
                "status": (e.status if e else None),
                "in_scope": bool(sr["enabled"]),
                "scope_role": sr["role"],
                "scope_enabled": sr["enabled"],
                "cost": 0, "imp": 0, "clk": 0, "conv_amt": 0, "roas": None,
                "bep_roas": None,
                # 창 안 집행이 아예 없는 스코프 그룹 — 램프업이 아니라 «관측 자체가 없음»이다.
                # baseline_days=0이지만 profit_status는 bep_unknown 그대로 둔다(둘은 다른 사유다).
                "baseline_days": 0,
                "gross_profit": None, "gross_profit_low": None, "gross_profit_high": None,
                "profit_status": PROFIT_STATUS_BEP_UNKNOWN,
            })

        adgroups.sort(key=lambda g: (-g["cost"], g["name"]))
        ce = entities.get(("campaign", cid))
        campaigns.append({
            "campaign_id": cid,
            "name": _clean(ce.name if ce else None) or "이름 없는 광고",
            "campaign_type": (ce.campaign_type if ce else None),
            "optimizer": optimizer,
            "auto_operate": auto_operate,
            # ★스코프 행이 있으면 이 캠페인은 「일부 그룹만 맡긴 상태」다 —
            #   그때 캠페인 레벨 액션(예산)은 hold된다(그룹 귀속 불가).
            "has_scope": bool(scope_rows),
            "scoped_count": sum(1 for sr in scope_rows if sr["enabled"]),
            "adgroup_count": len(adgroups),
            # ★D-NAO-267: 램프업으로 «빠진» 그룹 수. 이게 없으면 캠페인 총이익이 조용히
            #   줄어든 것처럼 보인다 — 「모름」이 「0원」으로 읽히는 그 자리다(n=62 실증).
            "ramp_up_count": c_ramp_up,
            "cost": c_cost, "imp": c_imp, "clk": c_clk, "conv_amt": c_conv_amt,
            "roas": (round(c_conv_amt / c_cost, 2) if c_cost else None),
            "gross_profit": (c_profit_sum if c_profit_known else None),
            "gross_profit_low": (c_profit_low_sum if c_profit_known else None),
            "gross_profit_high": (c_profit_high_sum if c_profit_known else None),
            "adgroups": adgroups,
        })

    campaigns.sort(key=lambda c: (not c["has_scope"], -c["cost"], c["name"]))

    return {
        "window": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "days": days,
            # ★가산 — 고른 창을 그대로 못 준 경우 «왜»가 화면에 뜬다. 조용히 자르지 않는다.
            "clamped": clamped,
            "note": note,
        },
        # ★단일 "value"를 없앴다 — 화면이 하나만 집어 들면 그게 사실처럼 읽힌다.
        "correction_factor": {
            "low": float(factor_low), "high": float(factor_high), "source": factor_source,
        },
        "totals": agg["totals"],
        # ★D-NAO-267 (계약 §4-A T1 = ref 65 S2-ⓐ): 평시/주말/공휴일 분리 집계.
        #   ref 63과 **같은 grain**(날짜)이라 항등식이 진짜로 성립하는 자리다 —
        #   retro 쪽 분리는 사후창을 못 가른다(적대 리뷰 1R P1-1).
        "weekend_holiday": day_class_split(db, date_from, date_to, campaign_id),
        "campaigns": campaigns,
    }
