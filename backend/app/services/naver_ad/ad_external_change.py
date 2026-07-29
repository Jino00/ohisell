# ad_external_change.py — 소재(ad) grain 외부 변경 탐지 (D-NAO-127, 관측 전용)
# 역할: 소재 관측치(직전 관측 vs 방금 관측)를 editTm 앵커로 비교해 "우리가 아닌 누군가가
#   우리 소재를 바꿨다"를 naver_agency_op에 기록한다. 네이버 API 쓰기 0 · 제어 없음.
#
# ★왜 필요한가(2026-07-29 실사고): 폴드8와이드 소재 입찰이 15:39:05 KST에 1,600→1,000원으로
#   되돌려졌는데(대행사 추정) **어떤 배치도 이걸 잡지 못했다**. entity_sync·bm_diff·
#   naver_agency_op는 전부 campaign/adgroup/keyword grain만 추적하고, 소재 grain은 쓰기 직전
#   현재값 재조회에서 우연히 걸렸을 뿐이다. 우리가 설정한 값이 유지되는지 확인할 수단이 없으면
#   자동 제어(D-NAO-125 소재 하향)를 켜도 조용히 덮인다.
#
# ★왜 editTm인가: 값 비교만으로는 왕복(우리 800 → 외부가 원래값 1,000으로 되돌림)이 직전 관측과
#   같아서 **무변동으로 보인다**. entity_sync는 이 되돌림 레이스를 시각 비교(changed_at vs
#   synced_at)로 우회했지만, 소재는 네이버가 마지막 수정 시각(editTm)을 직접 준다 —
#   추론 대신 사실을 쓴다. 우리 쓰기의 change_log.after_value에도 그때의 editTm이 통째로
#   들어 있어(harness가 get_ad 응답 원본을 저장), "마지막 편집이 우리 것인가"가 문자열
#   동등비교 한 번으로 끝난다.
#
# ★naver_change_log가 아니라 naver_agency_op에 쓰는 이유(중요): change_log는 쿨다운·일일상한의
#   원료다(naver_execution_harness.compute_change_cadence가 action 무관하게 dry_run=False +
#   after_value 행을 전부 센다). 외부 관측을 거기 넣으면 대행사가 만질 때마다 **우리 소재의
#   2시간 쿨다운이 연장**되어, 되찾으러 가야 할 바로 그 순간 우리 손이 묶인다. 관측은 관측
#   테이블에 넣는다(naver_agency_op 독스트링의 "학습 쿼리 오염 금지"와 같은 규율).
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import NaverAgencyOp, NaverCampaignSettings, NaverChangeLog
from app.services.naver_sa_ad_fetcher import _parse_ad_attr
from app.utils.kst import KST, kst_now

log = logging.getLogger(__name__)

# 예외 브리핑(bm_briefing P5)에 올릴 op_type — 우리 레버가 실제로 흔들린 사건만.
# ad_edit(추적 필드는 그대로인데 editTm만 전진)은 무엇이 바뀌었는지 모르므로 예외 승격하지 않는다.
_EXCEPTION_OP_TYPES = frozenset({"bid_change", "bid_mode_flip", "status_flip"})


def parse_edit_tm(raw) -> datetime | None:
    """네이버 editTm(UTC ISO8601 'Z') → KST naive datetime. 해석 불가면 None.

    라이브 실측 형식: "2026-07-29T06:39:05.000Z" (= 15:39:05 KST).
    ★오프셋이 없는 값은 None을 반환한다 — UTC라고 가정하면 9시간 틀린 시각을 사실처럼
    기록하게 된다(추정 금지). 이 함수는 표시·기록용이고 탐지 판정은 원문 문자열 비교라,
    None이어도 탐지 자체는 계속 동작한다.
    """
    if not raw:
        return None
    s = str(raw).strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(KST).replace(tzinfo=None)


def _our_last_ad_writes(db: Session, ad_ids: list[str]) -> dict[str, dict]:
    """소재별 **우리의 마지막 성공 쓰기** after_value(dict). 없으면 키 없음.

    dry_run=False ∧ after_value 존재 = 실제 성공한 쓰기(가드거부·실패·dry-run은 after를 안
    채운다 — 이 코드베이스의 기존 규약: _detect_external_change·_load_our_bid_writes 주석).
    after_value는 harness가 get_ad 응답 원본을 그대로 json.dumps 한 것이라 editTm·adAttr·
    userLock이 전부 들어 있다.
    """
    if not ad_ids:
        return {}
    rows = (
        db.query(NaverChangeLog)
        .filter(
            NaverChangeLog.entity_type == "ad",
            NaverChangeLog.entity_id.in_(ad_ids),
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),
        )
        .order_by(NaverChangeLog.changed_at.asc())  # 뒤에 오는 최신이 앞을 덮어씀
        .all()
    )
    out: dict[str, dict] = {}
    for r in rows:
        try:
            after = json.loads(r.after_value or "")
        except (ValueError, TypeError):
            continue
        if isinstance(after, dict):
            out[r.entity_id] = after
    return out


def _ours_state(after: dict | None) -> dict:
    """우리 change_log after_value(get_ad 원본) → 비교 가능한 소재 상태."""
    if not after:
        return {}
    bid, ugba = _parse_ad_attr(after.get("adAttr"))
    return {
        "edit_tm": after.get("editTm"),
        "ad_bid_amt": bid,
        "use_group_bid_amt": ugba,
        "ad_user_lock": bool(after["userLock"]) if "userLock" in after else None,
    }


def _pct(before, after) -> float | None:
    """Δ%(before 기준). before가 0/None이면 None(분모 방어 — bm_diff._pct와 같은 규율)."""
    if not before or after is None:
        return None
    return round((after - before) / before * 100, 2)


def _lock_label(v) -> str | None:
    return None if v is None else ("off" if v else "on")


def _diff_ops(before: dict, obs: dict) -> list[dict]:
    """추적 필드 3종 비교 → op dict 리스트. 하나도 안 바뀌었으면 빈 리스트.

    한 편집이 여러 필드를 동시에 바꿀 수 있으므로(입찰 + 그룹입찰 커플링) 필드별로 독립 기록한다.
    어느 쪽이든 None인 필드는 비교하지 않는다 — 미수집을 변경으로 읽으면 유령이 생긴다
    (bm_diff의 either-NULL 스킵과 같은 근거).
    """
    ops: list[dict] = []

    b_bid, c_bid = before.get("ad_bid_amt"), obs.get("ad_bid_amt")
    if b_bid is not None and c_bid is not None and b_bid != c_bid:
        ops.append({"op_type": "bid_change", "before_value": str(b_bid),
                    "after_value": str(c_bid), "magnitude": _pct(b_bid, c_bid)})

    b_ugba, c_ugba = before.get("use_group_bid_amt"), obs.get("use_group_bid_amt")
    if b_ugba is not None and c_ugba is not None and b_ugba != c_ugba:
        # ★우리 소재 입찰 레버의 생사가 이 플래그다: true가 되면 소재 bidAmt가 무시되고
        # (naver_sa_writer.update_ad_bid가 fail-closed로 거부) 그룹입찰이 실효가 된다.
        ops.append({"op_type": "bid_mode_flip", "before_value": str(b_ugba),
                    "after_value": str(c_ugba), "magnitude": None})

    b_lock, c_lock = before.get("ad_user_lock"), obs.get("ad_user_lock")
    if b_lock is not None and c_lock is not None and b_lock != c_lock:
        ops.append({"op_type": "status_flip", "before_value": _lock_label(b_lock),
                    "after_value": _lock_label(c_lock), "magnitude": None})

    return ops


def detect_ad_external_changes(
    db: Session, *, prev_by_ad: dict[str, dict], observed: list[dict],
) -> list[dict]:
    """직전 관측 vs 방금 관측 → 외부 변경 op 리스트(순수 판정, DB 쓰기 없음).

    prev_by_ad: {ad_id: {"edit_tm","ad_bid_amt","use_group_bid_amt","ad_user_lock"}} — 직전 관측.
    observed:   [{"ad_id","adgroup_id","campaign_id","edit_tm","ad_bid_amt","use_group_bid_amt",
                  "ad_user_lock"}, ...] — 방금 관측(get_ads 결과 + 그룹 맥락).

    판정 순서(각 단계가 곧 '기록하지 않는 이유'다):
      ① 직전 관측 없음        → 신규 소재. '변경'이 아니다.
      ② 양쪽 editTm 중 하나라도 없음 → 앵커 부재(최초 도입일·API 미제공). 판정 유보.
         (값 비교로 폴백하지 않는다 — 왕복을 못 보는 반쪽 신호를 사실처럼 남기지 않는다.)
      ③ editTm 동일          → 아무도 안 만졌다. **절대다수가 여기서 끊긴다.**
      ④ 마지막 편집 = 우리 쓰기(우리 change_log after의 editTm == 라이브 editTm) → 우리 손.
      ⑤ 그 외                → 외부 행위자.

    ★before를 무엇으로 볼 것인가(entity_sync._log_external_bid_change의 R2 규칙과 동형):
      직전 관측 이후 우리가 썼다면(우리 editTm != 직전 관측 editTm) 외부 행위자가 본 출발값은
      직전 관측값이 아니라 **우리가 써넣은 값**이다. 그래야 "우리 1,600 → 외부 1,000" 이력이
      끊기지 않는다. 초판 실수를 재현하면 "1,000 → 1,000"이 되어 되돌림이 통째로 사라진다.
    """
    ad_ids = [o["ad_id"] for o in observed if o.get("ad_id")]
    our_writes = _our_last_ad_writes(db, ad_ids)
    ops: list[dict] = []

    for obs in observed:
        ad_id = obs.get("ad_id")
        if not ad_id:
            continue
        prev = prev_by_ad.get(ad_id)
        if prev is None:
            continue  # ①
        prev_edit, live_edit = prev.get("edit_tm"), obs.get("edit_tm")
        if not prev_edit or not live_edit:
            continue  # ②
        if prev_edit == live_edit:
            continue  # ③

        ours = _ours_state(our_writes.get(ad_id))
        ours_edit = ours.get("edit_tm")
        if ours_edit and ours_edit == live_edit:
            continue  # ④ 마지막 편집이 우리 쓰기

        # 우리 쓰기가 직전 관측보다 뒤라면(그 editTm이 직전 관측에 안 잡혔다) 외부의 출발값은
        # 우리 값이다. 우리 쓰기가 직전 관측 이전이면 prev_edit == ours_edit이라 여기 안 걸린다.
        overwrote_ours = bool(ours_edit and ours_edit != prev_edit)
        before = {**prev, **{k: v for k, v in ours.items() if v is not None}} if overwrote_ours else prev

        field_ops = _diff_ops(before, obs)
        if not field_ops:
            # 편집은 일어났는데 우리가 추적하는 3필드는 그대로 = 다른 필드(소재 문구·연결 상품 등)
            # 변경. 무엇이 바뀌었는지 모르므로 지어내지 않고 '편집됨'만 남긴다(예외 승격 없음).
            field_ops = [{
                "op_type": "ad_edit",
                "before_value": _fmt_stamp(prev_edit),
                "after_value": _fmt_stamp(live_edit),
                "magnitude": None,
            }]

        for op in field_ops:
            ops.append({
                **op,
                "entity_id": ad_id,
                "campaign_id": obs.get("campaign_id") or "",
                "adgroup_id": obs.get("adgroup_id") or "",
                "occurred_at": parse_edit_tm(live_edit),
                "overwrote_ours": overwrote_ours,
            })

    return ops


def _fmt_stamp(raw) -> str:
    """editTm 원문 → 사람이 읽는 KST 문자열. 해석 불가면 원문 그대로(정보 소실 방지)."""
    dt = parse_edit_tm(raw)
    return dt.strftime("%m-%d %H:%M:%S") if dt else str(raw)


def _optimizer_map(db: Session, campaign_ids: set[str]) -> dict[str, str]:
    """campaign_id → optimizer(none/ours/mop). 설정 행이 없으면 매핑에서 빠짐(호출부가 'none')."""
    if not campaign_ids:
        return {}
    return {
        r.campaign_id: (r.optimizer or "none")
        for r in db.query(NaverCampaignSettings.campaign_id, NaverCampaignSettings.optimizer)
        .filter(NaverCampaignSettings.campaign_id.in_(campaign_ids))
        .all()
    }


def record_ad_external_changes(db: Session, ops: list[dict], now: datetime) -> int:
    """op 리스트 → naver_agency_op 적재(중복 방지). 반환: 실제 삽입 행 수.

    멱등 키는 (entity_id, op_type, occurred_at)이다 — bm_diff처럼 날짜 통째 삭제-재생성을 쓰지
    않는다(같은 테이블을 두 프로듀서가 쓰므로 남의 행을 지우면 안 된다). occurred_at이 곧 그
    편집의 고유 식별자라, 같은 편집을 두 번 관측해도 두 번 기록되지 않는다.

    ★op_date = **감지일**(컬럼 정의 그대로). occurred_at이 어제여도 op_date는 오늘이다 —
    그래야 오늘 도는 예외 브리핑(bm_briefing은 op_date == 오늘로 조회)이 이 사건을 놓치지 않는다.
    "언제 손댔나"는 occurred_at이 답한다.
    """
    if not ops:
        return 0
    opt = _optimizer_map(db, {op["campaign_id"] for op in ops if op["campaign_id"]})
    existing = {
        (r.entity_id, r.op_type, r.occurred_at)
        for r in db.query(NaverAgencyOp.entity_id, NaverAgencyOp.op_type, NaverAgencyOp.occurred_at)
        .filter(NaverAgencyOp.entity_type == "ad")
        .all()
    }
    inserted = 0
    for op in ops:
        key = (op["entity_id"], op["op_type"], op["occurred_at"])
        if key in existing:
            continue
        existing.add(key)
        db.add(NaverAgencyOp(
            op_date=now.date(),
            detected_at=now,
            entity_type="ad",
            entity_id=op["entity_id"],
            campaign_id=op["campaign_id"],
            optimizer=opt.get(op["campaign_id"], "none"),
            op_type=op["op_type"],
            before_value=op["before_value"],
            after_value=op["after_value"],
            magnitude=op["magnitude"],
            is_exception=op["op_type"] in _EXCEPTION_OP_TYPES,
            occurred_at=op["occurred_at"],
        ))
        inserted += 1
        log.warning(
            "소재 외부 변경 감지(D-NAO-127): ad=%s %s %s→%s (실제 변경 시각 %s%s)",
            op["entity_id"], op["op_type"], op["before_value"], op["after_value"],
            op["occurred_at"].strftime("%m-%d %H:%M:%S") if op["occurred_at"] else "불명",
            ", 우리 값을 덮음" if op.get("overwrote_ours") else "",
        )
    return inserted


def run(
    db: Session, *, prev_by_ad: dict[str, dict], observed: list[dict],
    now: datetime | None = None,
) -> dict:
    """탐지 + 적재 1회(harness). 자체 commit — 호출부(매핑 sync)의 트랜잭션과 분리한다.

    반환: {"observed", "ops", "recorded"}.
    """
    n = now or kst_now()
    ops = detect_ad_external_changes(db, prev_by_ad=prev_by_ad, observed=observed)
    recorded = record_ad_external_changes(db, ops, n)
    if recorded:
        db.commit()
    log.info("ad_external_change: 관측 %d소재, 외부변경 op %d건(적재 %d)",
             len(observed), len(ops), recorded)
    return {"observed": len(observed), "ops": len(ops), "recorded": recorded}
