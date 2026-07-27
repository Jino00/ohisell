# bm_diff.py — SA-2 조작 감지기 (BM 벤치마크 레이어, D-NAO-78)
# 역할: naver_entity_snapshot의 D-1 vs D 두 날짜 셋을 diff(DB-to-DB, 0 GET·결정적·리플레이)
#   → naver_agency_op 이벤트 생성. 계정 전체 45캠페인(대행사 포함)의 일일 조작을 관찰 전용
#   피드로 축적한다(§3). 노이즈 필터 5종(우리 변경의 지연 반영(echo) 제외·ours 자기변경
#   제외·deleted 가드·bootstrap 가드·is_exception 판정). 네이버 API 호출 0 — 실행 손
#   (naver_execution_harness/naver_sa_writer)을 import조차 안 한다(§0 금지선 1 · 원칙18-1).
#
# ★echo 필터(2026-07-27 오탐 수정): 스냅샷은 매일 07:37 KST에 naver_entity를 복사하므로
#   우리 실집행(예: 07-23 21:51 EX확장 bid 1,970→2,260, 07-24 탐색UP 90→180→270 래더)이
#   1~2일 늦게 diff에 나타난다. 구 코드는 (a) optimizer=='ours' 행만 change_log와 대조하고
#   (b) 대조해도 action 종류·시간창만 봤기 때문에, D-NAO-92로 optimizer=none이 된 03 캠페인의
#   우리 변경 7건이 전부 "대행사 조작"으로 기록됐다(실측). 이제 optimizer와 무관하게 모든 op에
#   대해 "우리 실집행 after 값과 일치하는가"를 먼저 검사한다. 시간창 앵커도 kst_now가 아니라
#   **그 날 스냅샷에 저장된 관측 시각**이다 — 과거 날짜 리플레이가 같은 결과를 내야 하고
#   (멱등·리플레이 계약), 스냅샷 이후에 일어난 우리 쓰기는 이 diff에 반영될 수 없기 때문이다.
#   억제가 과하지 않도록 세 겹의 브레이크를 둔다: 값 일치 필수 · 외부 귀속 기록
#   (external_bid_change 등) 우선 · 되돌림(우리 최종값에서 이탈) 판별.
#
# ★op별 창 앵커(D-NAO-93): 상한을 필드 출처별·**행별** 관측 시각으로 나눈다 — 입찰·상태·키워드는
#   그 행의 entity_observed_at(entity_sync 관측), 예산·확장검색은 그 행의 p3_observed_at(P3 GET).
#   synced_at 하나로 잡던 구 코드는 그 차이만큼 미탐(entity 밴드가 ~24h 과대)·오탐(p3 밴드가 몇 분
#   과소)을 남겼다. P3는 캠페인별 순차 GET이라 전역 max로 뭉치는 것도 금지(codex[P1]).
#   구 스냅샷 행(두 컬럼 NULL)은 synced_at 폴백 — 종전 동작 그대로. §_window 참조.
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Callable, NamedTuple

from sqlalchemy.orm import Session

from app.models import NaverAgencyOp, NaverChangeLog, NaverEntitySnapshot
from app.utils.kst import kst_now, kst_today

log = logging.getLogger(__name__)

# ── 노이즈·예외 임계(§3 공통 필터, §9-3 초기 캘리브레이션 대상) ──
BID_JITTER_PCT = 3.0        # |Δ%| < 3% = 입찰 반올림 지터 무시
BUDGET_JITTER_PCT = 5.0     # |Δ%| < 5% = 예산 지터 무시
BID_EXCEPTION_PCT = 20.0    # 입찰 |Δ%| ≥ 20% → is_exception
BUDGET_EXCEPTION_PCT = 30.0  # 예산 |Δ%| ≥ 30% → is_exception
OURS_MATCH_WINDOW_H = 48    # ours 자기변경 매칭 창(op_date 종료 기준 48h)
# echo(지연 반영) 조회창 — 실측 지연 1~2일을 덮는 3일.
# ★D-NAO-93 이후 의미 변화(재캘리브레이션은 후속 과제 — 값 자체는 이번 스프린트에서 안 건드림):
#   앵커가 synced_at(D 07:37)에서 **행별 관측 시각**(체이닝 전 데이터는 D-1 07:35)으로 당겨지며
#   lookback 하단도 같이 밀렸다. 원리상 필요한 구간은 (직전 관측, 이번 관측] ≈ 24h이므로 3일은
#   그만큼이 여유폭이고, 그 여유는 그대로 **미탐 표면**이다: 창 안 어딘가의 우리 옛 쓰기 값이
#   우연히 스냅샷 after와 같으면 대행사 조작이 echo로 지워질 수 있다. 특히 budget_change는
#   외부 귀속 기록(external_*)이 없어 veto 브레이크가 안 걸리고 인과 규칙(4)만 남는다.
ECHO_LOOKBACK_D = 3
_ID_CHUNK = 400             # change_log IN 절 분할(SQLite 바인딩 상한 방어)

# op_type → 우리(ours)가 API로 쓰는 change_log.action 집합(매칭 대상). 여기 없는 op_type은
# 우리가 API로 안 쓰는 종류 → ours여도 매칭 불가 = 외부 개입으로 간주(§3-1 후단).
_OURS_ACTION_MATCH = {
    "bid_change": {"update_bid"},
    "status_flip": {"set_user_lock", "external_status_change"},
    "budget_change": {"update_budget"},
    "negative_add": {"add_negative_keyword"},
}

# ★echo 근거에서 제외하는 action: entity_sync가 "외부(MOP/사람)가 바꿨다"를 관측해 남기는
# 기록이라 우리 손의 증거가 아니다. 이걸 echo로 인정하면 대행사 정지/재개가 그대로 묻힌다.
_ECHO_EXCLUDED_ACTIONS = frozenset({"external_status_change"})

# op_type → entity_sync가 남기는 **외부 귀속 증거** action(after_value 형태는 우리 쓰기와 동일:
# {"bidAmt":N} / {"userLock":bool}). 같은 값에 대해 외부 기록이 있으면 그쪽이 이긴다 —
# entity_sync는 07:35 동기화 시점에 "우리 쓰기로 설명 안 되는 변화"만 남기므로 더 시의적절하고
# 강한 근거다(codex[P1] R1). 우리 stale 로그가 외부 개입을 덮는 경로를 여기서 끊는다.
_EXTERNAL_ACTION_MATCH = {
    "bid_change": frozenset({"external_bid_change"}),
    "status_flip": frozenset({"external_status_change"}),
}


def _pct(before: int, after: int) -> float:
    """Δ% (before 기준). before=0이면 0(분모 방어 — 지터/예외 판정 모두 통과 안 됨)."""
    return (after - before) / before * 100 if before else 0.0


def _snapshot_map(db: Session, sdate: date) -> dict[tuple[str, str], NaverEntitySnapshot]:
    """(entity_type, entity_id) → 스냅샷 행. 특정 날짜 셋을 dict로 적재."""
    return {
        (r.entity_type, r.entity_id): r
        for r in db.query(NaverEntitySnapshot).filter(
            NaverEntitySnapshot.snapshot_date == sdate).all()
    }


def _op(row, op_type: str, *, before, after, magnitude, force_exc: bool = False) -> dict:
    """diff 1건 → 내부 op dict(분류/영속화 전 중간표현). before/after는 텍스트로 정규화."""
    return {
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "campaign_id": row.campaign_id or "",
        "optimizer": row.optimizer or "none",
        "op_type": op_type,
        "before_value": None if before is None else str(before),
        "after_value": None if after is None else str(after),
        "magnitude": magnitude,
        "force_exc": force_exc,
        # ★이 행의 필드 출처별 관측 시각(D-NAO-93) — 창 상한을 **행별로** 유도한다(전역 max 금지:
        # P3는 캠페인별 순차 GET이라 먼저 관측된 행에 늦은 시각을 쓰면 밴드가 되살아난다).
        "entity_observed_at": getattr(row, "entity_observed_at", None),
        "p3_observed_at": getattr(row, "p3_observed_at", None),
    }


def _detect_added(curr: dict, prev: dict) -> list[dict]:
    """새 entity_id 등장 = 대행사 구조 신설(campaign_add/adgroup_add). 항상 is_exception=True(§3).

    신규 등장인데 이미 deleted면 무시(구조로 등장하지 않은 잔여 상태)."""
    ops = []
    for key, c in curr.items():
        if key in prev or (c.status or "") == "deleted":
            continue
        op_type = "campaign_add" if c.entity_type == "campaign" else "adgroup_add"
        ops.append(_op(c, op_type, before=None, after=(c.status or "on"), magnitude=None, force_exc=True))
    return ops


def _detect_removed(curr: dict, prev: dict) -> list[dict]:
    """소실 또는 deleted 전이 = 구조 소멸(campaign_remove/adgroup_remove). 항상 is_exception=True.

    ★deleted 가드(§3-2): D-1에서 이미 deleted였던 엔티티는 재발화 금지(일 레인 deleted 404
    반복 사고 교훈). on/off였던 것이 이번에 소실·deleted로 바뀔 때 1회만 기록한다."""
    ops = []
    for key, p in prev.items():
        if (p.status or "") == "deleted":
            continue  # 이미 이전에 remove 기록됨 — 재발화 금지
        c = curr.get(key)
        became_deleted = c is not None and (c.status or "") == "deleted"
        if c is not None and not became_deleted:
            continue  # 여전히 존재(변화는 _detect_changed가 처리)
        op_type = "campaign_remove" if p.entity_type == "campaign" else "adgroup_remove"
        after = "deleted" if became_deleted else None
        ops.append(_op(p, op_type, before=(p.status or "on"), after=after, magnitude=None, force_exc=True))
    return ops


def _changed_bid(p, c) -> list[dict]:
    """그룹 기본입찰 Δ. |Δ%| < 3% 지터 무시. magnitude=Δ%(방향 포함)."""
    if c.entity_type != "adgroup" or p.bid_amt is None or c.bid_amt is None or p.bid_amt == c.bid_amt:
        return []
    pct = _pct(p.bid_amt, c.bid_amt)
    if abs(pct) < BID_JITTER_PCT:
        return []
    return [_op(c, "bid_change", before=p.bid_amt, after=c.bid_amt, magnitude=round(pct, 2))]


def _changed_status(p, c) -> list[dict]:
    """상태 on↔off 전이. deleted 전이는 _detect_removed가 처리(여기선 제외).

    ★캠페인 grain의 status_flip은 항상 is_exception=True로 승격(P2 리뷰 지적 반영, 2026-07-21
    실사례 근거): 대행사가 맥세이프쇼검·폴드8/플립8 캠페인을 통째로 잠그는 것은 그날 최대
    조작이었다 — 캠페인 정지/재개는 그룹 개별 flip과 무게가 다른 구조적 사건이라 대형변화
    임계(§3-4a) 유무와 무관하게 예외 브리핑에 올린다. 그룹 grain flip은 기존대로 비예외
    유지(대형변화·외부개입 판정에서만 예외 승격, §3 불변)."""
    ps, cs = (p.status or "on"), (c.status or "on")
    if ps == cs or "deleted" in (ps, cs):
        return []
    is_campaign = c.entity_type == "campaign"
    return [_op(c, "status_flip", before=ps, after=cs, magnitude=None, force_exc=is_campaign)]


def _changed_keyword_count(p, c) -> list[dict]:
    """그룹 활성 키워드 수 증감(집계 이벤트, entity=그룹·before/after=count). 개별 키워드
    grain은 entity_sync가 이미 로깅 — §9-1 병존. 어느 쪽이든 NULL(미집계)이면 스킵."""
    if c.entity_type != "adgroup" or p.keyword_count is None or c.keyword_count is None:
        return []
    if p.keyword_count == c.keyword_count:
        return []
    op_type = "keyword_add" if c.keyword_count > p.keyword_count else "keyword_remove"
    return [_op(c, op_type, before=p.keyword_count, after=c.keyword_count,
                magnitude=float(abs(c.keyword_count - p.keyword_count)))]


def _changed_budget(p, c) -> list[dict]:
    """일예산 Δ. |Δ%| < 5% 무시. ★어느 쪽이든 NULL이면 스킵 — P1/P2는 양쪽 NULL(자연 비활성),
    P3 최초 적재일의 NULL↔값 전이는 '미수집→수집' 아티팩트라 조작이 아니다(양쪽 값이 찬 P3
    2일차부터 실제 Δ 발화). §6 '양쪽 NULL 스킵'을 either-NULL로 확장(근거=None은 미수집 센티넬)."""
    if p.daily_budget is None or c.daily_budget is None or p.daily_budget == c.daily_budget:
        return []
    pct = _pct(p.daily_budget, c.daily_budget)
    if abs(pct) < BUDGET_JITTER_PCT:
        return []
    return [_op(c, "budget_change", before=p.daily_budget, after=c.daily_budget, magnitude=round(pct, 2))]


def _changed_extended(p, c) -> list[dict]:
    """확장검색 on↔off 토글. 어느 쪽이든 NULL(미수집)이면 스킵(_changed_budget과 같은 근거)."""
    if p.extended_search is None or c.extended_search is None or p.extended_search == c.extended_search:
        return []
    return [_op(c, "extended_toggle", before=p.extended_search, after=c.extended_search, magnitude=None)]


def _changed_negative(p, c) -> list[dict]:
    """제외키워드 수 증감(주간 grain). 어느 쪽이든 NULL(미수집)이면 스킵."""
    if p.negative_kw_count is None or c.negative_kw_count is None or p.negative_kw_count == c.negative_kw_count:
        return []
    op_type = "negative_add" if c.negative_kw_count > p.negative_kw_count else "negative_remove"
    return [_op(c, op_type, before=p.negative_kw_count, after=c.negative_kw_count,
                magnitude=float(abs(c.negative_kw_count - p.negative_kw_count)))]


def _changed_creative(p, c) -> list[dict]:
    """소재 수 증감(주간 grain). 어느 쪽이든 NULL(미수집)이면 스킵."""
    if p.ad_count is None or c.ad_count is None or p.ad_count == c.ad_count:
        return []
    return [_op(c, "creative_change", before=p.ad_count, after=c.ad_count,
                magnitude=float(abs(c.ad_count - p.ad_count)))]


def _detect_changed(curr: dict, prev: dict) -> list[dict]:
    """양쪽 스냅샷에 모두 존재하는 엔티티의 차원별 변화. deleted 관여 엔티티는 remove 담당."""
    ops: list[dict] = []
    for key, c in curr.items():
        p = prev.get(key)
        if p is None or (c.status or "") == "deleted" or (p.status or "") == "deleted":
            continue
        ops += _changed_bid(p, c)
        ops += _changed_status(p, c)
        ops += _changed_keyword_count(p, c)
        ops += _changed_budget(p, c)
        ops += _changed_extended(p, c)
        ops += _changed_negative(p, c)
        ops += _changed_creative(p, c)
    return ops


class _ChangeRec(NamedTuple):
    """change_log 1행의 매칭용 축약(entity_type/action/시각/before·after 원문 JSON)."""

    entity_type: str
    action: str
    changed_at: datetime | None
    before_value: str | None
    after_value: str | None


class _Band(NamedTuple):
    """한 관측 앵커에서 파생된 change_log 조회·매칭 창.

    until = 그 필드군의 관측 시각. echo_from = until − 3일, ours_from = until − 48h."""

    echo_from: datetime
    ours_from: datetime
    until: datetime


# op_type → 그 op의 상한으로 쓸 **행 자신의** 관측 시각 컬럼. 여기 없는 op_type(구조
# add/remove·주간 deep negative_*·creative_change)은 fallback 밴드(스냅샷 복사 시각) 유지 —
# 주간 deep은 별도 레인(일요일 GET)이라 이 수정의 대상이 아니다.
_OP_STAMP = {
    "bid_change": "entity_observed_at",
    "status_flip": "entity_observed_at",
    "keyword_add": "entity_observed_at",
    "keyword_remove": "entity_observed_at",
    "budget_change": "p3_observed_at",
    "extended_toggle": "p3_observed_at",
}


class _Window(NamedTuple):
    """change_log 적재창 + op별 밴드 해석기. 앵커는 전부 스냅샷 **저장값** — kst_now 금지
    (리플레이 멱등, §9-1).

    fallback = 스냅샷 복사 시각(synced_at) 밴드 — 구조·주간 deep op와, 관측 시각이 NULL인 구
    행의 폴백. load_from/load_until은 전 op 밴드를 덮는 최광 창(적재 1회용)."""

    fallback: _Band
    load_from: datetime
    load_until: datetime

    def band(self, op: dict) -> _Band:
        """이 op의 창 — **그 op가 나온 스냅샷 행 자신의** 관측 시각이 상한(없으면 fallback).

        ★상한은 적재창 [load_from, load_until] 안으로 클램프한다. op은 prev(D-1) 행 스탬프를
        실을 수 있는데(_detect_removed) _window는 curr만 스캔하므로, 그 스탬프가 상한이 되면
        적재되지 않은 구간을 판정하게 되어 **조용히** 틀린다(적재 안 된 로그는 판정에서 통째로
        사라짐 = 근거 없음 = echo 아님으로 흐름). 지금은 remove가 _OP_STAMP에 없어 도달하지
        않지만, 미래에 누가 넣어도 깨지지 않도록 구조로 막는다."""
        stamp = _OP_STAMP.get(op["op_type"])
        at = op.get(stamp) if stamp else None
        if at is None:
            return self.fallback
        return _band(min(max(at, self.load_from), self.load_until))


def _max_stamp(curr: dict, attr: str) -> datetime | None:
    """스냅샷 행들의 attr 최대값(전부 NULL이면 None)."""
    stamps = [s for r in curr.values() if (s := getattr(r, attr, None)) is not None]
    return max(stamps) if stamps else None


def _band(until: datetime) -> _Band:
    return _Band(
        echo_from=until - timedelta(days=ECHO_LOOKBACK_D),
        ours_from=until - timedelta(hours=OURS_MATCH_WINDOW_H),
        until=until,
    )


def _window(d: date, curr: dict) -> _Window:
    """op_date d의 change_log 적재창 + 폴백 밴드. 실제 판정 상한은 op별(_Window.band).

    "D 종료(D+1 00:00)"로 잡으면 스냅샷 이후(예: 같은 날 20시)의 우리 쓰기가 **아직 이 diff에
    반영될 수 없는데도** 매칭 후보가 되어, 값이 우연히 겹치는 대행사 조작을 소급해 지운다
    (codex[P1] R1). 앵커는 전부 스냅샷 행에 저장된 값이라 리플레이해도 같다 — 실행 시각
    의존이 없다.

    ★D-NAO-93: 상한을 필드 출처별·**행별**로 나눈다. 입찰·상태·키워드집계는 앞선 entity_sync가
    관측한 값이므로 그 행의 entity_observed_at(실측상 D-1 07:35 — bm 스냅샷 07:37보다
    entity_sync 커밋이 늦는 경합), 예산·확장검색은 스냅샷이 도는 중 P3 GET이 관측하므로 그 행의
    p3_observed_at을 쓴다. P3는 캠페인별 순차 GET이라 행마다 시각이 다르다 — 전역 max로 뭉치면
    먼저 관측된 행에 "반영 안 됐는데 창 안" 밴드가 되살아난다(codex[P1]).
    행 값이 NULL인 구 행은 synced_at 폴백 → 종전 동작 그대로. 그것도 없으면 D+1 00:00 폴백
    (curr가 빈 경우 외엔 도달하지 않고, 그때는 값 변화 op 자체가 없다).

    ★알려진 잔여 오차: ①entity_observed_at은 entity_sync **런 시작 시각**이라 그 런이 도는
    동안(분 단위)의 우리 쓰기와는 여전히 어긋날 수 있다. ②주간 deep 필드(negative_*·
    creative_change)는 일요일 별도 레인이 채우므로 관측 시각을 따로 두지 않았다 — 종전대로
    fallback 밴드. ③구 행은 NULL → synced_at 폴백이라 이 정밀화가 소급 적용되지 않는다."""
    fallback_at = _max_stamp(curr, "synced_at") or datetime.combine(d + timedelta(days=1), time.min)
    # 적재창은 **모든 행·모든 앵커**의 상한을 덮어야 한다(행별 밴드가 갈라지므로 최소~최대 전부).
    # ★deleted 행 제외: entity_sync는 deleted 전이 때 synced_at을 갱신하지 않는데 bm_snapshot은
    # deleted 행도 스냅샷하므로, entity_observed_at이 수개월 전인 좀비가 curr에 남는다(실측 82일).
    # 그 행은 remove op(=fallback 밴드)만 만들어 판정 기여가 0인데, min()이 그걸 집으면 적재창
    # 하한이 수개월로 벌어져 change_log를 통째로 스캔한다(entity_id 인덱스 없음).
    live = [r for r in curr.values() if (getattr(r, "status", "") or "") != "deleted"]
    untils = [fallback_at]
    for attr in dict.fromkeys(_OP_STAMP.values()):
        untils += [s for r in live if (s := getattr(r, attr, None)) is not None]
    return _Window(
        fallback=_band(fallback_at),
        load_from=min(untils) - timedelta(days=ECHO_LOOKBACK_D),
        load_until=max(untils),
    )


def _json_dict(raw: str | None) -> dict | None:
    """change_log.after_value(JSON 문자열) → dict. 파싱 실패/비-dict는 None(fail-safe)."""
    if not raw:
        return None
    try:
        v = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return v if isinstance(v, dict) else None


def _num_str(v) -> str | None:
    """수치 값 → 무손실 문자열. int 캐스팅으로 절삭하지 않는다(2260.9를 2260으로 만들어
    잘못 일치시키던 문제, codex[P2]). bool은 거부(True==1 오탐 방지). 수치가 아니면 None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        return str(v)
    if isinstance(v, str):
        s = v.strip()
        try:
            Decimal(s)
        except InvalidOperation:
            return None
        return s
    return None


def _after_bid(after: dict | None) -> str | None:
    """우리 update_bid의 after → 입찰가 문자열. 광고그룹({"bidAmt":N})·소재(adAttr 중첩) 둘 다
    지원(auto_operator._exploration_bid_from_change_value와 같은 규약)."""
    if after is None:
        return None
    bid = after.get("bidAmt")
    if bid is None:
        attr = after.get("adAttr")
        if isinstance(attr, str):
            attr = _json_dict(attr)
        bid = attr.get("bidAmt") if isinstance(attr, dict) else None
    return _num_str(bid)


def _after_budget(after: dict | None) -> str | None:
    """우리 update_budget의 after → 일예산 문자열({"dailyBudget":N}, writer가 재조회 응답 그대로)."""
    return _num_str(after.get("dailyBudget")) if after is not None else None


def _after_status(after: dict | None) -> str | None:
    """우리 set_user_lock의 after → 스냅샷 status 표기로 환산(userLock True=off / False=on).

    ★진짜 bool만 받는다 — 문자열 "false"는 bool()로 True가 되어 정지로 오인된다(codex[P2]).
    entity_sync._status와 같은 규약 — deleted는 여기서 나올 수 없다(status_flip은 deleted 제외)."""
    if after is None:
        return None
    lock = after.get("userLock")
    if not isinstance(lock, bool):
        return None
    return "off" if lock else "on"


# op_type → change_log.after_value에서 "스냅샷 after와 같은 축"의 값을 뽑는 함수. 여기 없는
# op_type(키워드/제외/소재 수 증감 등)은 값 비교가 불가능한 집계라 echo 판정 대상이 아니다.
_ECHO_AFTER: dict[str, Callable[[dict | None], str | None]] = {
    "bid_change": _after_bid,
    "budget_change": _after_budget,
    "status_flip": _after_status,
}


def _same_after(op_val: str | None, log_val: str | None) -> bool:
    """스냅샷 값 vs change_log 값 동치 판정. 수치는 Decimal로 **정확 비교**("1970" == 1970,
    "2260.9" != "2260" — float 절삭/오차 없음), 그 외는 문자열 비교(on/off). 어느 쪽이든
    None이면 불일치(근거 없음 = echo 아님)."""
    if op_val is None or log_val is None:
        return False
    a, b = op_val.strip(), log_val.strip()
    try:
        return Decimal(a) == Decimal(b)
    except InvalidOperation:
        return a == b


def _load_change_logs(db: Session, ops: list[dict], win: _Window) -> dict[str, list[_ChangeRec]]:
    """이번 diff에 등장한 **전체** 엔티티의 우리 실집행 change_log(dry_run=False)를 적재.

    ★optimizer='ours' 한정을 걷어낸 것이 이번 수정의 핵심(구 코드는 optimizer=none으로 바뀐
    캠페인의 우리 변경을 대조조차 못 했다). 하루 diff 건수라 소량이고, 조회창 하한(load_from)을
    SQL에서 걸어 바운드한다. entity_id는 IN 절 상한 방어로 분할 조회.
    적재는 **전 밴드를 덮는 가장 넓은 창**으로 1회 — 밴드별 좁힘은 판정부(_in)에서 한다.

    반환 키는 entity_id — change_log.entity_type은 proposal.target_type 기반이라 스냅샷 grain
    표기와 어긋날 수 있고, 네이버 id는 타입 접두사(cmp-/grp-/nkw-/nad-)로 전역 유일하다.
    entity_type이 필요한 ours 분기는 _ChangeRec.entity_type으로 따로 좁힌다.

    ★tz 계약: change_log를 쓰는 경로(naver_execution_harness·entity_sync 등 26곳)는 전부
    changed_at을 kst_now()로 명시 기록한다 — server_default(func.now()=UTC)에 기대는 행이
    없으므로 KST naive끼리 직접 비교해도 안전하다."""
    ids = sorted({o["entity_id"] for o in ops if o["entity_id"]})
    out: dict[str, list[_ChangeRec]] = defaultdict(list)
    for i in range(0, len(ids), _ID_CHUNK):
        rows = (
            db.query(NaverChangeLog)
            .filter(
                NaverChangeLog.dry_run.is_(False),
                NaverChangeLog.entity_id.in_(ids[i:i + _ID_CHUNK]),
                NaverChangeLog.changed_at >= win.load_from,
                NaverChangeLog.changed_at <= win.load_until,  # ★상한 닫힘 — 관측과 동시각인 외부 귀속 로그를 실어야 veto가 산다(codex[P1])
            )
            .all()
        )
        for r in rows:
            out[r.entity_id].append(
                _ChangeRec(r.entity_type, r.action, r.changed_at, r.before_value, r.after_value)
            )
    return out


def _in(at: datetime | None, lo: datetime, hi: datetime) -> bool:
    """창 [lo, hi) 포함 여부. changed_at NULL은 판정 불가 → False."""
    return at is not None and lo <= at < hi


def _in_closed(at: datetime | None, lo: datetime, hi: datetime) -> bool:
    """창 [lo, hi] 포함 여부(**상한 닫힘**) — 외부 귀속 증거 전용.

    ★codex[P1]: entity_sync는 external_* 로그의 changed_at과 NaverEntity.synced_at에 같은 now를
    기록한다. 상한이 그 synced_at(=entity_observed_at)인데 반개구간으로 보면, **바로 그 관측이
    남긴 외부 귀속 증거**가 경계에서 탈락한다 → veto가 사라져, 과거 우리 쓰기의 after가 우연히
    같으면 실제 대행사 변경이 echo로 오억제된다. 우리 쓰기(mine)는 반개구간 유지 — 관측과
    동시각인 우리 쓰기가 그 관측에 반영됐다는 보장이 없어 보수적으로 제외한다."""
    return at is not None and lo <= at <= hi


def _is_our_echo(op: dict, logs: dict, win: _Window) -> bool:
    """이 diff가 '우리 실집행의 지연 반영'인가 — optimizer와 무관하게 먼저 검사한다.

    인정 조건(셋 다):
      (1) 창 안 우리 실집행 로그 중 after 값이 스냅샷 after와 일치하는 것이 있다. 래더
          (90→180→270)는 중간 스텝이 diff에 걸리므로 최신 1건이 아니라 **전체 중 하나라도**.
      (2) 같은 값에 대한 외부 귀속 기록(external_bid_change 등)이 없다 — 있으면 그쪽이 이긴다.
      (3) 되돌림이 아니다: 우리 마지막 쓰기값과 스냅샷 after가 다르면서 **스냅샷 before가
          우리 마지막 쓰기값**이면, 그건 우리가 옮겨둔 상태에서 누군가 되돌린 전이다
          (우리 270 뒤 대행사 270→180). 옛 로그의 after=180이 우연히 맞아도 echo 아님
          (codex[P1] R1 — 래더 인정과 되돌림 탐지를 동시에 만족시키는 규칙).
      (4) 인과 정합: 스냅샷 before가 **우리 상태 궤적**(창 안 우리 쓰기의 before/after 값들)
          안에 있어야 한다. 예산처럼 외부 귀속 기록(external_*)이 아예 없는 차원에서, 우리가
          한 번도 쓴 적 없는 제3의 값(30,000)에서 우리 옛 값(50,000)으로 되돌아온 외부 조작이
          옛 로그 때문에 묻히던 경로를 끊는다(codex[P1] R3). ★궤적의 **시작**을 모르면
          건너뛴다(fail-open) — 창 안 첫 쓰기의 before를 파싱 못 하면 그 앞 상태를 알 수 없어
          "지나온 적 없다"를 말할 수 없다(codex[P2] R4).
          잔여: 래더의 첫 스텝이 조회창(3일)보다 오래되면 궤적이 잘려 정당한 echo를 오탐할 수
          있다. 실측 지연이 1~2일이라 3일 창이 이를 덮는다는 가정 위에 서 있다.
    """
    extract = _ECHO_AFTER.get(op["op_type"])
    if extract is None or op["after_value"] is None:
        return False  # 값 비교 불가한 집계 op(키워드/소재 수 등) → echo 판정 대상 아님
    ours_actions = _OURS_ACTION_MATCH.get(op["op_type"], set()) - _ECHO_EXCLUDED_ACTIONS
    if not ours_actions:
        return False
    ext_actions = _EXTERNAL_ACTION_MATCH.get(op["op_type"], frozenset())
    band = win.band(op)  # 이 행이 그 필드를 실제 관측한 시각 기준 창(D-NAO-93)

    mine: list[tuple[datetime, str, str | None]] = []  # (changed_at, after, before)
    external: list[tuple[datetime, str]] = []
    for rec in logs.get(op["entity_id"], ()):
        after = extract(_json_dict(rec.after_value))
        if after is None:
            continue
        if rec.action in ours_actions:
            if _in(rec.changed_at, band.echo_from, band.until):  # 우리 쓰기 = 반개구간
                mine.append((rec.changed_at, after, extract(_json_dict(rec.before_value))))
        elif rec.action in ext_actions:
            if _in_closed(rec.changed_at, band.echo_from, band.until):  # 외부 증거 = 상한 닫힘
                external.append((rec.changed_at, after))

    hits = [t for t, v, _ in mine if _same_after(op["after_value"], v)]
    if not hits:
        return False  # (1) 우리 손의 값 근거 없음
    # (2) 외부 귀속 기록은 **우리 매칭 쓰기 이후**의 것만 veto한다. 우리보다 먼저 있었던 같은
    # 값의 외부 기록까지 veto하면, 그 뒤 우리가 같은 값을 쓴 진짜 echo를 오탐한다(codex[P2] R2).
    ours_at = max(hits)
    if any(t >= ours_at and _same_after(op["after_value"], v) for t, v in external):
        return False
    mine.sort(key=lambda r: (r[0], r[1]))  # 시각·값 순 — 동시각도 결정적(리플레이 멱등)
    last_after = mine[-1][1]
    if not _same_after(op["after_value"], last_after) and _same_after(op["before_value"], last_after):
        return False  # (3) 우리 최종값에서 이탈한 전이 = 되돌림
    if mine[0][2] is not None:  # 궤적의 시작을 아는 경우에만 (4) 적용
        trail = [b for _, _, b in mine if b is not None] + [v for _, v, _ in mine]
        if not any(_same_after(op["before_value"], v) for v in trail):
            return False  # (4) 우리가 지나온 적 없는 값에서 출발한 전이 = 우리 손 아님
    return True


def _matches_our_write(op: dict, logs: dict, win: _Window) -> bool:
    """(ours 전용 분기) 이 조작이 48h 내 우리 실집행 change_log와 매칭되는가(= 우리 손).

    action 종류·시간창만 보는 느슨한 매칭이라 **값 비교가 불가능한 op_type에만** 남긴다
    (제외키워드 수 증감 등). 값 비교가 되는 op(입찰·예산·상태)에서 이 경로를 살려두면
    _is_our_echo가 세운 브레이크 3개(값 일치·외부 veto·되돌림 판별)를 ours 캠페인에서만
    통째로 우회한다 — 특히 _OURS_ACTION_MATCH에 external_status_change가 들어 있어 "외부
    기록만 있는데 우리 손으로 판정"까지 났다(codex[P1] R2). echo 창이 48h보다 넓으므로
    (3일) 값 근거가 있는 우리 변경은 이 경로 없이도 전부 걸린다."""
    actions = _OURS_ACTION_MATCH.get(op["op_type"])
    if not actions or op["op_type"] in _ECHO_AFTER:
        return False  # 우리가 API로 안 쓰는 종류·값 비교 가능한 종류 → 이 경로 사용 안 함
    band = win.band(op)
    for rec in logs.get(op["entity_id"], ()):
        if rec.entity_type != op["entity_type"]:
            continue
        if rec.action in actions and _in(rec.changed_at, band.ours_from, band.until):
            return True
    return False


def _magnitude_exception(op: dict) -> bool:
    """대형 변화 예외 판정(§3-4a): 입찰 |Δ%|≥20% · 예산 |Δ%|≥30%."""
    m = op["magnitude"]
    if m is None:
        return False
    if op["op_type"] == "bid_change":
        return abs(m) >= BID_EXCEPTION_PCT
    if op["op_type"] == "budget_change":
        return abs(m) >= BUDGET_EXCEPTION_PCT
    return False


def _classify(op: dict, logs: dict, win: _Window) -> tuple[bool, bool]:
    """(기록 여부, is_exception).

    순서: ①echo(우리 변경의 지연 반영) 제외 — optimizer 무관, 값 일치 근거 필요.
    ②§3-1 ours 자기변경은 매칭 시 제외, 미매칭 시 외부 개입 예외(단 값 비교가 되는 op는 ①이
    유일한 제외 경로 — _matches_our_write 참조). ③그 외는 기록."""
    if _is_our_echo(op, logs, win):
        log.info(
            "[BM] 우리 변경의 지연 반영(echo) — agency_op 제외: %s %s %s %s→%s (optimizer=%s)",
            op["entity_type"], op["entity_id"], op["op_type"],
            op["before_value"], op["after_value"], op["optimizer"],
        )
        return False, False
    if op["optimizer"] == "ours":
        if _matches_our_write(op, logs, win):
            return False, False  # 우리 손 — agency_op에서 제외
        return True, True        # 매칭 안 됨 = 우리 캠페인에 대한 외부 개입 → 예외 승격
    return True, bool(op["force_exc"] or _magnitude_exception(op))


def detect_agency_ops(db: Session, *, op_date: date | None = None) -> dict:
    """스냅샷 D-1 vs D를 diff해 naver_agency_op 이벤트를 산출(멱등·리플레이, 0 GET).

    bootstrap 가드: D-1 스냅샷이 없으면 전건 add 폭주를 막기 위해 스킵. 멱등: 같은 op_date
    기존 이벤트를 삭제 후 재생성(결정적이라 재도출=동일 결과, §9-1 병존 정책). change_log
    대조창은 D 스냅샷에 저장된 관측 시각 앵커(op_type별, §_window)라 과거 날짜 리플레이도 같은
    결과를 낸다(kst_now는 detected_at 기록에만 쓴다).
    """
    d = op_date or kst_today()
    prev = _snapshot_map(db, d - timedelta(days=1))
    if not prev:  # bootstrap 가드(§3-3) — 최초 실행/전일 스냅샷 부재
        log.info("[BM] SA-2 diff bootstrap 스킵(op_date=%s, D-1 스냅샷 없음)", d)
        return {"op_date": str(d), "bootstrap": True, "events": 0, "exceptions": 0}

    curr = _snapshot_map(db, d)
    now = kst_now()
    win = _window(d, curr)
    raw = _detect_added(curr, prev) + _detect_removed(curr, prev) + _detect_changed(curr, prev)
    logs = _load_change_logs(db, raw, win)

    db.query(NaverAgencyOp).filter(NaverAgencyOp.op_date == d).delete()  # 멱등 재생성
    n_event = n_exc = 0
    for op in raw:
        keep, is_exc = _classify(op, logs, win)
        if not keep:
            continue
        db.add(NaverAgencyOp(
            op_date=d, detected_at=now,
            entity_type=op["entity_type"], entity_id=op["entity_id"],
            campaign_id=op["campaign_id"], optimizer=op["optimizer"],
            op_type=op["op_type"], before_value=op["before_value"],
            after_value=op["after_value"], magnitude=op["magnitude"], is_exception=is_exc,
        ))
        n_event += 1
        n_exc += int(is_exc)
    db.commit()

    result = {"op_date": str(d), "bootstrap": False, "events": n_event, "exceptions": n_exc}
    log.info("[BM] SA-2 조작 감지: %s", result)
    return result
