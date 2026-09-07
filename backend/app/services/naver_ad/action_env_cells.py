# action_env_cells.py — 조치 × 환경 채점 1라운드 (D-NAO-299 · 계약 D-NAO-266 §4-A T5 = ref 65 S2-ⓔ)
#
# 역할(SA): 「어떤 환경에서 어떤 조치가 흑자였는가」(Jino 교정 2026-08-17 21:24)에 답하는
#   **기술통계 셀 표**를 읽기 시점에 만든다. 조치 유형 5종 × 환경 2층으로 `ad_profit`을 모으고
#   계층 수축(hierarchical_pooling.shrink, K=10)을 태워 (n, raw, shrunk, 확정도)를 병기한다.
#   읽기 전용 — 테이블 신설 0 · 마이그 0 · 네이버 API 0콜 · 광고계정 쓰기 0.
#
# ★★이 표는 «기술»이지 «인과»가 아니다. 대조군 없는 전후 비교는 인과가 아니고(ref 63 §12-3:
#   조치는 나쁠 때 발동돼 평균 회귀가 낀다), 인과 판정의 정본 설계는 매칭 DiD다
#   (ref 59 ⑦-3 · 설계 문서 `docs/references/142_did_matching_design_20260907.md`).
#   그래서 산출물의 모든 셀에 `causal: false`를 실어 보낸다 — 경고를 문서에만 두면
#   응답을 소비하는 쪽은 그 경고를 못 본다.
#
# 왜 저장 표면이 «없나»(계약 §4-B ⑥의 택일 — 구현 «전»에 정한 것을 여기 옮겨 적는다):
#   ⓐ같은 트랙에 무저장 선례가 둘이다 — `probe_cell_aggregate`가 환경 셀×버킷 수축을 저장 없이
#     하고, `semantic_units`도 전용 테이블이 0개다.
#   ⓑ계약이 요구한 것은 「셀 표가 산출되고 (n, raw, shrunk, 확정도)가 병기」이지 영속이 아니다.
#   ⓒ마이그가 0이면 배포 순서 위험이 0이다.
#   ⓓ원료(naver_agency_op·naver_search_term_exclusion·naver_ad_daily)가 전부 이미 영속이라
#     이 표는 언제든 재생성된다 — 저장하면 «재생성 가능한 파생»과 «원장»이 섞인다.
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverAgencyOp, NaverSearchTermExclusion
from app.services.naver_ad import campaign_target_resolver, probe_cell_aggregate
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_ad.diary import _iphone_offset_days
from app.services.naver_ad.hierarchical_pooling import SHRINK_K, shrink
from app.services.naver_ad.wisdom_candidates import _iphone_window
from app.utils.kst import kst_today

_Q4 = Decimal("0.0001")
_ZERO = Decimal("0")

# ── 계약 §4-B ⑤가 확정한 값들 — 전부 기존 숫자의 재사용, 발명 0 ──────────────────────
# 조치 유형 5종(ref 65 §5-c-1 표 그대로). 마지막 'exclusion'만 원장이 다르다
# (`naver_search_term_exclusion.console_excluded_at` — ref 65가 「+ 제외」로 센 그 축).
ACTION_TYPES: tuple[str, ...] = (
    "bid_change", "extended_toggle", "status_flip", "budget_change", "exclusion",
)
_AGENCY_OP_TYPES: tuple[str, ...] = ACTION_TYPES[:-1]

# 환경 2층 — 확정 축 둘뿐(ref 63 W9: 홀드아웃+민감도 동시 통과). 그 이상 쪼개지 않는다
# (ref 65 §5-c-2: 「첫 라운드 환경 층은 확정 2축만」).
ENV_LAYERS: tuple[str, ...] = ("weekday", "weekend_holiday")

# 성숙 컷 D−8 — ref 63 §1-1 / ref 59 ⑦-5 「모든 롤링 창의 종점 ≤ D0−8」. 새 창이 아니다.
MATURITY_CUT_DAYS = 8
DEFAULT_WINDOW_DAYS = 90

# 미확정 환경 라벨 — **층이 아니라 라벨**이다(ref 65 §5-c-2). 좌표를 발명하지 않았다:
#   · launch_window = `wisdom_candidates._iphone_window`(|offset| ≤ 14일)
#   · vacation_window = `scripts/analysis/63_band_decomposition/build_panel.py:27-28`
#     (VACATION_START_MD=(7,20) · VACATION_END_MD=(8,15) — ref 63 F7이 실제로 쓴 그 창)
_VACATION_START_MD = (7, 20)
_VACATION_END_MD = (8, 15)


def env_layer_of_date(d: date) -> str:
    """확정 환경 2층. `probe_cell_aggregate.env_cell_of_date`(weekday/weekend/holiday)를
    2층으로 «접기만» 한다 — 새 판정이 아니다. 접는 이유는 계약 §4-B ⑤가 환경 층을
    「평시 / 주말+공휴일」 둘로 못 박았기 때문이고, 주말과 공휴일을 가르는 것은 층 증식이다.
    """
    return "weekday" if probe_cell_aggregate.env_cell_of_date(d) == "weekday" else "weekend_holiday"


def unverified_env_labels(d: date) -> tuple[str, ...]:
    """그 날짜에 걸리는 **미확정** 환경 라벨들(없으면 빈 튜플).

    ★fail-closed: 출시 오프셋을 모르면(`_iphone_window`가 'unknown' — 출시일 config가 비었을
    때) `launch_unknown`을 단다. 「모른다」를 「없다」로 접으면 그 셀이 확정으로 굳는다 —
    이 저장소가 반복해 겪은 «0의 사유를 안 가른» 병(계약 §2-5)의 라벨판이다.
    """
    out: list[str] = []
    w = _iphone_window(_iphone_offset_days(d))
    if w == "launch_window":
        out.append("launch_window")
    elif w == "unknown":
        out.append("launch_unknown")
    if _VACATION_START_MD <= (d.month, d.day) <= _VACATION_END_MD:
        out.append("vacation_window")
    return tuple(out)


def _q4(v: Decimal | None) -> float | None:
    return None if v is None else float(v.quantize(_Q4, ROUND_HALF_UP))


def _profit_by_campaign_day(db: Session, start: date, end: date,
                            bep_roas: Decimal, campaign_id: str | None) -> dict:
    """{(ad_date, campaign_id): ad_profit(Decimal)} — ref 63 §1-1의 자 그대로.

    `ad_profit = conv_amt / bep_roas − cost`. **분모는 계정 단일값**이다(캠페인별 BEP가 아니라):
    ref 65 §5-c-1이 성과를 「가법」이라 못 박았고, 캠페인마다 다른 분모를 쓰면 셀 합이
    상위 층 합과 안 맞아 수축 체인이 성립하지 않는다. 그 대가(상품 BEP 미확보 그룹이
    계정 블렌디드로 뭉개진다 — 북극성 §7의 「알려진 구멍」)는 응답의 `yardstick`에 적어 보낸다.

    집계 정본: sentinel 행(`adgroup_id='__backfill__'`)을 뺀다 — `metrics_aggregator`와 같은 규칙.
    """
    q = (
        db.query(
            NaverAdDaily.ad_date,
            NaverAdDaily.campaign_id,
            sqlfunc.sum(NaverAdDaily.cost).label("cost"),
            sqlfunc.sum(NaverAdDaily.conv_direct_amt + NaverAdDaily.conv_indirect_amt).label("conv_amt"),
        )
        .filter(
            NaverAdDaily.ad_date >= start,
            NaverAdDaily.ad_date <= end,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .group_by(NaverAdDaily.ad_date, NaverAdDaily.campaign_id)
    )
    if campaign_id:
        q = q.filter(NaverAdDaily.campaign_id == campaign_id)
    out: dict[tuple[date, str], Decimal] = {}
    for ad_date, cid, cost, conv_amt in q.all():
        d = date.fromisoformat(ad_date) if isinstance(ad_date, str) else ad_date
        out[(d, cid)] = Decimal(int(conv_amt or 0)) / bep_roas - Decimal(int(cost or 0))
    return out


def _agency_action_days(db: Session, start: date, end: date, campaign_id: str | None) -> tuple[dict, dict]:
    """(조치-일 집합, 5종 밖 op_type 인구조사, 조치 유형별 entity grain 인구조사).

    반환 1: {action_type: {(op_date, campaign_id): ops_count}}
    반환 2: {op_type: 건수} — **5종 밖으로 «버린» 것을 세어 함께 돌려준다.** 안 세면 「5종이
      전부」로 읽히는데, 09-07 실측에서 5종 밖이 이미 다수다(ad_edit 76·bid_mode_flip 24 등).
    반환 3: {action_type: {entity_type: 건수}} — ★**grain이 섞여 있다는 사실 자체를 센다.**
      09-07 실측(90일·성숙컷): bid_change = ad 410 / adgroup 12 · status_flip = adgroup 15 /
      campaign 4 · budget_change = campaign 2 / adgroup 1 · extended_toggle = adgroup 55.

    피드 재적용(`feed_verdict='feed'`)은 사람의 조치가 아니라 네이버가 상품 피드를 재적용한
    잡음이라 뺀다(D-NAO-139) — ref 65 §5-c-1이 「이미 걸러져 있다」고 적은 그 260건.
    """
    q = db.query(
        NaverAgencyOp.op_type, NaverAgencyOp.op_date, NaverAgencyOp.campaign_id,
        NaverAgencyOp.entity_type,
    ).filter(
        NaverAgencyOp.op_date >= start,
        NaverAgencyOp.op_date <= end,
        sqlfunc.coalesce(NaverAgencyOp.feed_verdict, "") != "feed",
    )
    if campaign_id:
        q = q.filter(NaverAgencyOp.campaign_id == campaign_id)

    days: dict[str, dict] = {a: {} for a in _AGENCY_OP_TYPES}
    entity_census: dict[str, dict] = {a: {} for a in _AGENCY_OP_TYPES}
    outside: dict[str, int] = {}
    for op_type, op_date, cid, ent in q.all():
        if op_type not in days:
            outside[op_type] = outside.get(op_type, 0) + 1
            continue
        if not cid:
            # campaign_id가 비면 성과에 붙일 자리가 없다 — 버리지 않고 인구조사로 센다.
            outside["__no_campaign_id__"] = outside.get("__no_campaign_id__", 0) + 1
            continue
        d = date.fromisoformat(op_date) if isinstance(op_date, str) else op_date
        key = (d, cid)
        days[op_type][key] = days[op_type].get(key, 0) + 1
        entity_census[op_type][ent] = entity_census[op_type].get(ent, 0) + 1
    return days, outside, entity_census


def _exclusion_action_days(db: Session, start: date, end: date, campaign_id: str | None) -> dict:
    """제외 조치의 조치-일 — {(date, campaign_id): 건수}.

    ★시각축 주의: `console_excluded_at`은 콘솔 「제외 검색어」 탭의 표기(`2026.08.11 22:26`)를
    그대로 받은 값이라 **KST 벽시계**다(D-NAO-177). `excluded_at`(우리 장부가 행을 세운 시각)로
    폴백하지 않는다 — 그건 「우리가 언제 알았나」이고, 섞으면 조치일이 편입일에 몰린다.
    """
    q = db.query(
        NaverSearchTermExclusion.console_excluded_at, NaverSearchTermExclusion.campaign_id,
    ).filter(NaverSearchTermExclusion.console_excluded_at.isnot(None))
    if campaign_id:
        q = q.filter(NaverSearchTermExclusion.campaign_id == campaign_id)
    out: dict[tuple[date, str], int] = {}
    for stamp, cid in q.all():
        if stamp is None or not cid:
            continue
        d = stamp.date() if hasattr(stamp, "date") else date.fromisoformat(str(stamp)[:10])
        if d < start or d > end:
            continue
        key = (d, cid)
        out[key] = out.get(key, 0) + 1
    return out


def _summarize(entries: list[tuple[tuple[date, str], int]], profit: dict) -> dict:
    """조치-일 목록 → (n, ops, 매칭 결측, ad_profit 합, raw 평균, 미확정 라벨 카운트).

    n은 **조치-일 수**(distinct (조치일 × 캠페인))이지 조치 «건수»가 아니다. 같은 캠페인·같은 날
    입찰을 30번 만져도 그날 그 캠페인의 ad_profit은 하나뿐이라, 건수로 세면 같은 하루가 30번
    더해진다. 건수는 `ops`로 따로 싣는다 — 둘 다 필요하다(ops는 조치 «밀도»다).
    """
    n = 0
    ops = 0
    unmatched = 0
    total = _ZERO
    labels: dict[str, int] = {}
    for key, cnt in entries:
        ops += cnt
        p = profit.get(key)
        if p is None:
            # 성과 행이 없는 조치-일(그날 그 캠페인에 노출 자체가 없었거나 수집 결측).
            # 0원으로 채우지 않는다 — 「모름」을 「본전」으로 굳히면 셀 평균이 조용히 낙관된다.
            unmatched += 1
            continue
        n += 1
        total += p
        for lab in unverified_env_labels(key[0]):
            labels[lab] = labels.get(lab, 0) + 1
    raw = (total / Decimal(n)) if n else None
    return {"n": n, "ops": ops, "unmatched_days": unmatched,
            "ad_profit_sum": total if n else _ZERO, "raw": raw, "unverified_labels": labels}


def _certainty(labels: dict) -> tuple[str, str]:
    """(확정도, 사유). 미확정 환경 라벨이 **한 건이라도** 걸리면 그 셀은 미확정이다.

    ref 65 §5-c-1 원문: *"미확정 환경으로 조치를 채점하면 그 결과도 미확정이다 — 산출물에
    라벨을 전파한다"*. 그래서 비율 문턱을 두지 않는다 — 문턱을 두면 그 문턱이 곧 새 상수이고,
    「조금 섞였으니 확정」이라는 판정이 생긴다(계약 §3: 문턱 신설 금지).
    """
    if not labels:
        return "확정", "확정 환경 축(평시/주말·공휴일)만으로 구성 — 미확정 라벨 0건"
    parts = " · ".join(f"{k} {v}조치-일" for k, v in sorted(labels.items()))
    return "미확정", f"미확정 환경 라벨 포함({parts}) — ref 63 F1a·F1b·F2·F7은 부호 미확정"


def build_cells(db: Session, *, days: int = DEFAULT_WINDOW_DAYS,
                date_to: date | None = None, campaign_id: str | None = None) -> dict:
    """조치 × 환경 셀 표를 «읽기 시점에» 만든다(저장 0 — 모듈 헤더의 택일 사유 참조).

    수축 체인은 ref 65 §5-c-2가 못 박은 그대로 **전체 → 조치 유형 → 조치 유형×환경**이고,
    각 층의 prior는 부모의 **수축값**이다(부모의 raw가 아니다 — `hierarchical_pooling.
    _pool_with_prior`와 같은 관례. raw를 prior로 쓰면 상위의 얇은 표본이 그대로 하위에 샌다).
    """
    today = kst_today()
    end_cap = today - timedelta(days=MATURITY_CUT_DAYS)
    end = min(date_to, end_cap) if date_to else end_cap
    days = max(1, int(days))
    start = end - timedelta(days=days - 1)

    bep = campaign_target_resolver.account_default_bep_roas(db)
    yardstick = {
        "formula": "ad_profit = conv_amt / bep_roas − cost",
        "bep_roas": _q4(bep) if bep else None,
        "bep_source": "account_default(매출가중)" if bep else "unavailable",
        "reference": "ref 63 §1-1 · ref 65 §5-c-1 성과 행. 계정 단일 분모라 가법이다 — "
                     "캠페인별 BEP를 쓰면 셀 합이 상위 층 합과 어긋나 수축 체인이 깨진다.",
        "known_gap": "상품 BEP 미확보 그룹은 계정 블렌디드로 뭉개진다(북극성 §7).",
    }
    window = {
        "date_from": start.isoformat(), "date_to": end.isoformat(), "days": days,
        "maturity_cut_days": MATURITY_CUT_DAYS,
        "basis": "조치일(op_date / console_excluded_at, KST) — 성과는 그 날짜의 캠페인 ad_profit",
        "asof": today.isoformat(),
    }

    if bep is None or bep <= 0:
        # 자가 없으면 표를 만들지 않는다. 0으로 채우면 「전 셀 손실」이라는 거짓 표가 나온다.
        return {"window": window, "yardstick": yardstick, "causal": False,
                "action_types": list(ACTION_TYPES), "env_layers": list(ENV_LAYERS),
                "overall": None, "by_action": {}, "cells": [],
                "status": "bep_unavailable",
                "caveats": ["계정 기본 BEP ROAS를 해석하지 못해 산출을 중단했다(0으로 채우지 않는다)."]}

    profit = _profit_by_campaign_day(db, start, end, bep, campaign_id)
    agency_days, outside, entity_census = _agency_action_days(db, start, end, campaign_id)
    action_days: dict[str, dict] = dict(agency_days)
    action_days["exclusion"] = _exclusion_action_days(db, start, end, campaign_id)

    # ── 층 1: 전체 ────────────────────────────────────────────────────────────────
    all_entries: list[tuple[tuple[date, str], int]] = []
    for a in ACTION_TYPES:
        all_entries.extend(action_days[a].items())
    overall = _summarize(all_entries, profit)
    root_prior = overall["raw"] if overall["raw"] is not None else _ZERO

    by_action: dict[str, dict] = {}
    cells: list[dict] = []
    for a in ACTION_TYPES:
        entries = list(action_days[a].items())
        s = _summarize(entries, profit)
        a_shrunk = shrink(n=s["n"], raw=s["raw"] if s["raw"] is not None else _ZERO, prior=root_prior)
        cert, cert_why = _certainty(s["unverified_labels"])
        by_action[a] = {
            "n": s["n"], "ops": s["ops"], "unmatched_days": s["unmatched_days"],
            "ad_profit_sum": int(s["ad_profit_sum"].quantize(Decimal("1"), ROUND_HALF_UP)),
            "raw": _q4(s["raw"]), "shrunk": _q4(a_shrunk), "prior": _q4(root_prior),
            "certainty": cert, "certainty_reason": cert_why,
            # ★grain 인구조사 — 이 조치들이 어느 층에서 일어났는가. 「전부 광고그룹 조치」로
            #   읽히면 소재 단위 조치(bid_change의 대다수)가 캠페인-일에 귀속됐다는 사실이
            #   숨는다. 숫자를 보여주고 판단은 읽는 쪽이 하게 둔다.
            "entity_grain": (
                {"search_term": s["ops"]} if a == "exclusion" else entity_census.get(a, {})
            ),
        }
        for env in ENV_LAYERS:
            cell_entries = [(k, c) for k, c in entries if env_layer_of_date(k[0]) == env]
            cs = _summarize(cell_entries, profit)
            c_shrunk = shrink(n=cs["n"], raw=cs["raw"] if cs["raw"] is not None else _ZERO,
                              prior=a_shrunk)
            c_cert, c_why = _certainty(cs["unverified_labels"])
            cells.append({
                "action_type": a, "env": env,
                "n": cs["n"], "ops": cs["ops"], "unmatched_days": cs["unmatched_days"],
                "ad_profit_sum": int(cs["ad_profit_sum"].quantize(Decimal("1"), ROUND_HALF_UP)),
                "raw": _q4(cs["raw"]),
                "shrunk": _q4(c_shrunk),
                "prior": _q4(a_shrunk),
                "prior_level": f"action:{a}",
                "certainty": c_cert, "certainty_reason": c_why,
                "unverified_labels": cs["unverified_labels"],
                # n=0이면 수축값은 «전부 prior»다 — 관측이 아니다. 숨기지 않는다.
                "all_prior": cs["n"] == 0,
            })

    return {
        "window": window,
        "yardstick": yardstick,
        # ★소비처가 이 표를 인과로 읽지 못하게 응답 자체에 박는다.
        "causal": False,
        "causal_note": "기술통계다. 대조군 없는 전후 비교는 인과가 아니다(평균 회귀) — "
                       "인과 판정의 정본 설계는 매칭 DiD(ref 59 ⑦-3), 설계 문서 ref 142.",
        "grain": {
            "unit": "조치-일 = (조치일 × 캠페인) distinct",
            "note": "셀 «안»에서는 중복이 없다. 셀 «사이»는 같은 캠페인-일이 여러 조치 유형에 "
                    "들어갈 수 있어 셀 합은 전체와 같지 않다 — 셀 간 합산 금지.",
            # ★★조치는 소재·광고그룹·캠페인 세 층에서 일어나는데 성과 귀속은 **캠페인-일**
            #   하나뿐이다. `naver_agency_op`에 `adgroup_id` 컬럼이 «없어서»다 —
            #   `ad_external_change.py:277`이 그 값을 계산해 놓고 `:354`의 persist에 안 싣는다
            #   (모델에 자리가 없다). 그래서 소재 조치를 광고그룹으로 내리는 경로가 원리적으로
            #   막혀 있고, 첫 라운드는 그 사실을 «희석»으로 안고 간다. 층별 건수는
            #   `by_action[*].entity_grain`이 그대로 보여준다 — 뭉갠 것을 숨기지 않는다.
            "entity_note": "조치 grain은 ad/adgroup/campaign 혼재 · 성과 귀속은 캠페인-일 단일. "
                           "naver_agency_op에 adgroup_id 컬럼이 없어 소재→광고그룹 강하가 불가.",
            "shrink_k": int(SHRINK_K),
            "chain": "전체 → 조치 유형 → 조치 유형×환경 (prior는 부모의 수축값)",
        },
        "action_types": list(ACTION_TYPES),
        "env_layers": list(ENV_LAYERS),
        "overall": {
            "n": overall["n"], "ops": overall["ops"],
            "unmatched_days": overall["unmatched_days"],
            "ad_profit_sum": int(overall["ad_profit_sum"].quantize(Decimal("1"), ROUND_HALF_UP)),
            "raw": _q4(overall["raw"]),
        },
        "by_action": by_action,
        "cells": cells,
        "op_types_outside_scope": outside,
        "status": "ok",
        "caveats": [
            "조치 유형은 계약 §4-B ⑤가 확정한 5종뿐이다 — 그 밖의 op_type은 "
            "`op_types_outside_scope`에 «버린 채로» 센다(안 세면 5종이 전부로 읽힌다).",
            "환경 층은 확정 2축(평시 / 주말+공휴일)뿐이다. 출시창·휴가창은 층이 아니라 "
            "라벨이고, 걸리면 그 셀의 확정도를 미확정으로 내린다(ref 65 §5-c-2).",
            "셀이 희소하면 문턱으로 감추지 않는다 — n을 그대로 병기한다(계약 §4-B ⑦). "
            "ref 65 §5-c-2가 「1차 병목은 성과가 아니라 조치 표본」이라 예언한 자리다.",
            "성과 행이 없는 조치-일은 0원이 아니라 `unmatched_days`로 뺀다.",
        ],
    }
