# exclusion_return_score.py — 복귀(재개방) 후 성적 채점기
#   (계약 docs/contracts/CONTRACT_ignition_readiness.md §4-C **S3-b**, 결손 #12)
#
# ── S3-b의 `[미상]` 해소 (착수 실측 2026-08-27, 이 모듈이 그 산출물) ──────────────
# 계약 §4-A는 "복귀 후 성적 채점기(실측 후 신설/재사용 판단)"라 적었다. 전수 조사 결과:
#
#   · `retro_scorer.py`      — grain이 `adgroup`/`keyword`뿐(:34). search_term 미지원이고
#                              `search_term_ss_lane`에서 참조 0건. **재사용 불가**.
#   · `exclusion_survival.py`— «제외가 아직 걸려 있나»를 라이브 재조회로 대조하는 **감시**다.
#                              MONITORED_STATUS='excluded' 하나뿐이고 probation·restored는
#                              «라이브에 없는 것이 정상»이라 의도적으로 대상 밖. **성적이 아니다**.
#   · `diary_outcome._st_window` (d1_st, D-NAO-178)
#                            — 검색어 grain을 **정확히 우리가 필요한 원료**로 집계한다.
#                              그러나 자(尺)가 반대다(아래). **집계는 재사용·판정은 신설**.
#
# ⇒ 판정: **신설**. 단 집계 프리미티브는 `diary_outcome`의 한 벌을 되읽는다(복제 금지 —
#    D-NAO-259가 재심사 백오프 세 벌을 한 벌로 모은 것과 같은 규율).
#
# ── ★왜 d1_st의 자를 그대로 쓰면 안 되나 (이 모듈의 존재 이유) ────────────────────
# `_st_window`의 status는 **비용 정지가 성공**인 자다: `cost_total == 0 → "stopped"`.
# 제외(브레이크)에는 옳다 — 목적함수가 「손실 검색어 절단」이니까.
#
# 복귀(액셀)는 목적함수가 **정반대**다. 복귀의 목적은 「제외가 틀렸는지 확인하는 실험」이고,
# 그 실험이 성립하려면 **돈이 다시 나가야** 한다. 그런데 같은 자로 재면:
#
#     복귀했는데 아무도 그 검색어를 안 찾음(cost=0) → status="stopped" → **성공으로 기록**
#
# 실제로는 «실험이 정보를 하나도 못 냈다»(silent)이고, 성공이 아니다. 부호가 뒤집힌 채로
# 학습 사슬에 들어가면 사슬은 「복귀하면 좋다」를 «비용이 안 나서» 배운다 — 북극성 §7이
# 이 트랙의 상습 실패 모드로 지목한 **「브레이크 어휘로 액셀을 채점」**이 정확히 이 모양이고,
# D-NAO-85(ROAS +7% · 매출 −52%)가 그 실측 전례다.
#
# 그래서 자를 가른다. 이 모듈의 판정은 **총이익 방향**(D-NAO-59)이고, 어휘가 다르다.
#
# ── 쓰기 범위 · 금지선 ───────────────────────────────────────────────────────
# 읽기(diary·naver_search_term_daily·BEP) + **diary.outcome_json 쓰기만**. 제외 원장의
# status/cycle/next_review_at·네이버 광고계정·제안 어디에도 쓰지 않는다.
# ★북극성 §6-b M3: *"성적표는 «재는 자»이지 «돌리는 손»이 아니다."* 이 모듈은 값을 바꾸는
#   경로를 갖지 않는다 — 재판정을 하는 것은 `_run_reexamination`이고 그 입력은 §1 후보 목록이지
#   이 점수가 아니다. 점수를 판정에 먹이는 것은 이 계약 밖이다(Jino 승인 필요 — §6-b M3 금지선).
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import NaverSearchTermDaily, OpsDiaryEntry
from app.services.naver_ad import campaign_target_resolver, diary_outcome, exclusion_lifecycle

log = logging.getLogger(__name__)

# outcome_json에 쓰는 키 — d1/d7/d1_st/retro와 공존하는 additive 키. 기존 키는 건드리지 않는다.
PROBATION_KEY = "probation"

# 관찰창 길이 — `search_term_ss_lane._PROBATION_DAYS`의 **되읽기**(새 상수 발명 금지, 계약 §2-3).
# 지연 import: search_term_ss_lane이 이 모듈을 import하지 않으므로 순환은 없으나, 레인 모듈은
# 무거워서(judge·harness 지연 import 체인) 채점 경로가 그걸 끌고 들어오지 않게 함수 안에서 읽는다.


def probation_days() -> int:
    from app.services.naver_ad.search_term_ss_lane import _PROBATION_DAYS

    return int(_PROBATION_DAYS)


# 원료 정정 창이 닫히기를 기다리는 여유 — `diary_outcome._OUTCOME_MIN_AGE_DAYS`와 같은 이유·같은 값.
# 창 종료일(open+PROBATION_DAYS)로부터 이만큼 더 지나야 «정정이 끝난 값»을 본다.
_SETTLE_LAG_DAYS = diary_outcome._OUTCOME_MIN_AGE_DAYS

# ── 판정 어휘 (총이익 축 — ROAS 방어 어휘를 재사용하지 않는다) ──────────────────
STATUS_NO_DATA = "no_data"            # 필요 source 보고서가 창에 없다 — 「0」이 아니라 「모른다」
STATUS_AMBIGUOUS = "ambiguous"        # 50자 절단 접두 매칭이 여럿을 잡았다 — 누구 돈인지 모른다
STATUS_SILENT = "silent"              # 열었는데 노출·클릭·비용 0 — **실험이 정보를 못 냈다**(성공 아님)
STATUS_UNVERIFIED = "unverified"      # 비용은 났는데 전환 귀속이 원리적으로 불가(expkeyword 단독) 또는 BEP 부재
STATUS_PROFITABLE = "profitable"      # 창 RoAS ≥ BEP — 복귀가 옳았다(컷이 틀렸다는 실측)
STATUS_UNPROFITABLE = "unprofitable"  # 창 RoAS < BEP — 컷이 옳았다

# 전환이 기록되는 유일한 source. expkeyword(WEB_SITE 계열)는 전환 귀속이 **원리적으로 부재**라
# (ref 64 · SS0 §0.5) 0을 「전환 없었음」으로 읽으면 안 된다. 두 source의 전환·RoAS 합산은
# diary_outcome 금지선 5 — 여기서도 그대로 지킨다.
_CONV_SOURCE = "shopping"


def _window_view(db: Session, entry: OpsDiaryEntry, date_from: date, date_to: date) -> dict:
    """관찰창 전체를 source별로 집계. `present`는 **이 (campaign, adgroup, source)의 창 안 보고서
    행 유무**다 — 검색어 행이 없는 것이야말로 「비용 0」의 의미이고, 보고서 자체가 없는 것은
    「아직 모른다」이기 때문이다(`diary_outcome._st_source_view`와 같은 규율, 창만 넓혔다).

    ★일별 루프가 아니라 창 단위 1회 집계다 — 복귀는 일일 캡 10건이라 볼륨이 작고, 일별로 쪼개면
    「어느 날 보고서가 하루 빠졌다」가 창 전체를 no_data로 뒤집는다(창은 14일이라 하루 결손은 흔하다).
    """
    out: dict = {}
    for source in diary_outcome.ST_SOURCES:
        scoped = diary_outcome.st_scope(db, entry, date_from, date_to).filter(
            NaverSearchTermDaily.source == source
        )
        if scoped.with_entities(NaverSearchTermDaily.id).first() is None:
            out[source] = {"present": False}
            continue
        matched = scoped.filter(diary_outcome.st_term_clause(entry.target_id)).all()
        view: dict = {
            "present": True,
            "imp": sum(int(r.imp or 0) for r in matched),
            "clk": sum(int(r.clk or 0) for r in matched),
            "cost": sum(int(r.cost or 0) for r in matched),
            "matched_terms": len({r.search_term for r in matched}),
        }
        if source == _CONV_SOURCE:
            view["conv_amt"] = sum(int(r.conv_purchase_amt or 0) for r in matched)
        out[source] = view
    return out


def _bep_roas(db: Session) -> float | None:
    """계정 기본 BEP RoAS — `exclusion_grade.backfill`이 등급 부여에 쓰는 것과 **같은 원천**을
    되읽는다(자가 갈라지면 같은 검색어가 등급과 복귀 성적에서 다른 판정을 받는다)."""
    try:
        dec = campaign_target_resolver.account_default_bep_roas(db)
    except Exception as e:  # noqa: BLE001 — BEP 산출 실패는 판정불능이지 0이 아니다
        log.warning("exclusion_return_score: BEP 산출 실패(unverified 처리): %s", e)
        return None
    return float(dec) if dec is not None else None


def score_probation_window(
    db: Session, entry: OpsDiaryEntry, today: date, open_date: date
) -> dict | None:
    """복귀 개방 일기 1행 → `outcome_json["probation"]` dict. 아직 창이 안 닫혔으면 None
    (키를 안 쓰고 다음 스윕에서 재시도 — d1_st의 «키를 안 쓴다» 규율과 같다).

    창 = [open_date+1, open_date+PROBATION_DAYS]. 개방 당일을 빼는 이유: 개방은 하루 중
    임의 시각에 일어나 그날 실적은 제외 구간과 복귀 구간이 섞인다(d1이 action_date+1부터인 것과
    같은 이유)."""
    span = probation_days()
    date_from = open_date + timedelta(days=1)
    date_to = open_date + timedelta(days=span)
    if today < date_to + timedelta(days=_SETTLE_LAG_DAYS):
        return None  # 창이 아직 안 닫혔거나 원료 정정 창이 열려 있다 — 다음 스윕 재시도

    mode = "prefix50" if len(entry.target_id or "") >= diary_outcome.ST_TRUNC_LEN else "exact"
    base: dict = {
        "window": {"from": date_from.isoformat(), "to": date_to.isoformat(), "days": span},
        "match": {"term": entry.target_id, "mode": mode},
    }

    if not entry.adgroup_id:
        # 그룹을 모르면 범위를 좁힐 수 없다 — 캠페인으로 넓히는 것은 d1이 낸 오귀속 그 자체다.
        base["match"]["mode"] = "unresolved"
        base["by_source"] = {}
        base["status"] = STATUS_NO_DATA
        return base

    by_source = _window_view(db, entry, date_from, date_to)
    base["by_source"] = by_source
    present = [v for v in by_source.values() if v["present"]]
    if not present:
        base["status"] = STATUS_NO_DATA  # 창 전체에 보고서가 없다 — 0이 아니라 모른다
        return base

    imp_total = sum(int(v["imp"]) for v in present)
    clk_total = sum(int(v["clk"]) for v in present)
    cost_total = sum(int(v["cost"]) for v in present)
    matched_terms = sum(int(v["matched_terms"]) for v in present)
    base["match"]["matched_terms"] = matched_terms
    base["imp_total"] = imp_total
    base["clk_total"] = clk_total
    base["cost_total"] = cost_total

    if imp_total == 0 and clk_total == 0 and cost_total == 0:
        # ★여기가 자를 가른 이유의 핵심. 제외 성적표라면 이것이 «완벽한 성공(stopped)»이지만,
        #   복귀 실험에서는 «아무 정보도 못 얻었다»이다 — 컷이 옳았다는 증거도, 틀렸다는 증거도
        #   아니다. 성공으로 세면 사슬이 「복귀하면 좋다」를 비용이 안 나서 배운다.
        base["status"] = STATUS_SILENT
        return base

    if mode == "prefix50" and matched_terms > 1:
        base["status"] = STATUS_AMBIGUOUS  # 누구 돈·누구 매출인지 모른다
        return base

    # RoAS는 **shopping 안에서만** 낸다 — expkeyword는 전환 귀속이 원리적으로 부재라(ref 64)
    # 그 비용을 분모에 넣으면 RoAS가 구조적으로 과소평가된다(두 source 합산 금지, 금지선 5).
    shop = by_source.get(_CONV_SOURCE) or {"present": False}
    shop_cost = int(shop.get("cost", 0)) if shop.get("present") else 0
    base["conv_scope"] = {
        "source": _CONV_SOURCE,
        "cost": shop_cost,
        "conv_amt": int(shop.get("conv_amt", 0)) if shop.get("present") else 0,
        "excluded_cost_no_attribution": cost_total - shop_cost,
    }
    bep = _bep_roas(db)
    if shop_cost <= 0 or bep is None:
        # 비용이 전환 귀속 불가 source에서만 났거나 BEP를 모른다 → 판정 근거가 없다.
        # 「보류」가 정직하다(D-NAO-259가 id=579를 오컷의심에서 미검증으로 되돌린 것과 같은 규율).
        base["status"] = STATUS_UNVERIFIED
        base["unverified_reason"] = (
            "BEP 부재" if bep is None else "전환 귀속 가능 source(shopping)에 비용 0 — expkeyword 단독 지출"
        )
        return base

    roas = base["conv_scope"]["conv_amt"] / shop_cost
    base["roas"] = round(roas, 4)
    base["bep_roas"] = round(bep, 4)
    base["status"] = STATUS_PROFITABLE if roas >= bep else STATUS_UNPROFITABLE
    return base


def is_return_open_entry(entry: OpsDiaryEntry) -> bool:
    """이 diary 행이 «복귀 개방»인가 — 소급 채점의 게이트."""
    return (
        entry.target_type == "search_term"
        and bool(entry.target_id)
        and entry.action == exclusion_lifecycle.RETURN_OPEN_ACTION
    )
