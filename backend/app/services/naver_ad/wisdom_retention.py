# wisdom_retention.py — retention_sa (D-NAO-54 P3 망각층, docs/PLAN_naver-ad-diary-wisdom.md §P3)
# 역할: 미승격(pending) 후보에 Ebbinghaus 망각을 적용해 오래 재등장 없는 약한 패턴을 soft-hide
#   (status=hidden, 삭제 아님)한다. ★TTL 미경과 보호: first_seen_at이 TTL(14일=판사 숙성 게이트)
#   전인 후보는 감쇠 대상에서 제외한다 — 판사가 볼 기회를 뺏으면 망각↔TTL 데드락이 생긴다(리뷰
#   P2-1). hidden은 시그니처 재등장 시 candidate_sa가 pending으로 부활시킨다(재노출 강화).
#   ★불망각: promoted 후보·지혜(ops_wisdom_entries)는 절대 감쇠하지 않고, rejected는 손대지
#   않는다(판사가 이미 기각). 쓰기 = wisdom_candidates status만.
from __future__ import annotations

import logging
import math
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import OpsWisdomCandidate
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 유효 기억강도 임계 — 이 미만이면 soft-hide. importance=5·strength=7 기준 마지막 재등장 후
# 약 9일이면 도달(0.5·exp(-9/7)≈0.14). 단 TTL(_TTL_DAYS) 미경과 후보는 아래 apply_retention에서
# 감쇠 자체를 건너뛴다(숙성 기회 보장). 삭제가 아니라 상태 전이라, hidden 후보는 시그니처
# 재등장 시 candidate_sa가 pending으로 부활시킨다(리뷰 P2-1).
_HIDE_THRESHOLD = 0.15

# 판사 숙성 게이트(wisdom_judge._TTL_DAYS)와 동일 — TTL 전 후보는 감쇠 제외(데드락 해소).
_TTL_DAYS = 14


def _s_eff(cand: OpsWisdomCandidate, now: datetime) -> float | None:
    """Ebbinghaus 유효강도 s_eff = (importance/10)·exp(-Δt_days/strength). last_seen_at
    없으면 None(감쇠 판단 불가 — 건드리지 않음). strength≤0은 방어적으로 감쇠 없음 취급."""
    if cand.last_seen_at is None:
        return None
    strength = cand.strength or 0.0
    if strength <= 0:
        return None
    delta_days = max((now - cand.last_seen_at).days, 0)
    importance = (cand.importance if cand.importance is not None else 5) / 10.0
    return importance * math.exp(-delta_days / strength)


def apply_retention(db: Session, *, now: datetime | None = None) -> dict:
    """pending 후보에 망각 적용(soft-hide). promoted/지혜는 불망각, rejected는 무관.

    감쇠 판정은 last_seen_at(KST 명시 기입) 기준 — created_at(UTC)과 섞지 않는다.
    """
    now = now or kst_now()
    pending = (
        db.query(OpsWisdomCandidate)
        .filter(OpsWisdomCandidate.status == "pending")
        .all()
    )
    totals = {"pending": len(pending), "hidden": 0, "kept": 0, "errors": 0}
    for cand in pending:
        try:
            # TTL(숙성 게이트) 미경과 후보는 감쇠 제외 — 판사가 볼 기회를 뺏지 않는다(데드락 해소).
            if cand.first_seen_at is not None and (now - cand.first_seen_at).days < _TTL_DAYS:
                totals["kept"] += 1
                continue
            s = _s_eff(cand, now)
            if s is not None and s < _HIDE_THRESHOLD:
                cand.status = "hidden"
                totals["hidden"] += 1
            else:
                totals["kept"] += 1
        except Exception as e:  # noqa: BLE001 — 한 건 실패가 나머지를 못 막는다
            totals["errors"] += 1
            log.exception("wisdom_retention: 감쇠 판정 실패(id=%s): %s", getattr(cand, "id", "?"), e)
    try:
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        totals["errors"] += 1
        log.exception("wisdom_retention: 커밋 실패: %s", e)
    return totals
