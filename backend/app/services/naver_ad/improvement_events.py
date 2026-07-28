# improvement_events.py — SA ⑥개선 타임라인의 이벤트 소스 (성과뷰 Phase 3, 계획서 §3-2)
"""역할(SA·단일 책임): "우리가 무엇을 바꿨나"를 한 줄짜리 이벤트 목록으로 만든다.

두 소스를 합류시킨다:
  ① 카탈로그(JSON)  — 트랙의 확정 결정 D-NAO-N. `scripts/gen_naver_improvement_events.py`가
                      repo에 커밋해 두고 safe_deploy로 prod에 나간다. 사람 말로 적힌 결정이라
                      의미 전달력이 가장 높다.
  ② 라이브 change_log — 실제로 설정을 바꾼 기록. 카탈로그에 없는 운영 변경을 메운다.

★파일이 없어도 500을 내지 않는다(계획서 §6 Phase3 완료기준 5). `catalog_available:false`로
  말하고 ②만 낸다 — 배포 순서가 어긋난 몇 분 동안 페이지가 죽는 것이 더 나쁘다.

★②에서 **일상 조작을 걸러낸다.** 입찰 변경(update_bid)·페이싱은 하루 수백 건이라 그대로
  실으면 타임라인이 "우리가 뭘 개선했나"가 아니라 로그 덤프가 된다. 구조를 바꾼 것만 남긴다.

★dry_run으로 거르지 않는다(라이브 실측 함정): `optimizer_change`는 광고 API 쓰기가 없는
  **로컬 설정 변경**이라 dry_run=True로 기록된다. dry_run=False만 세면 03 자동운영 재가동
  (change_log 864, D-NAO-101) 같은 가장 중요한 이벤트가 통째로 사라진다.

★`changed_at`은 **KST-naive**다. UTC가 아니다(리뷰 P1-1 교정, 2026-07-28).
  `server_default=func.now()`가 UTC인 건 맞지만 이 테이블의 writer는 전부 `changed_at=kst_now()`
  를 명시 전달한다(라우터 3곳·naver_execution_harness·flight_loop·entity_sync 실측 14곳).
  라이브 확증: change_log 865 = `2026-07-28 12:44:41` ↔ 트랙의 03 재가동 KST 12:44.
  같은 페이지의 Phase 1·2 하니스도 오프셋 없이 `datetime.combine(day, …)`로 자른다 —
  여기만 9시간을 더하면 **섹션 ②④와 ⑥이 같은 사건을 다른 날짜로 표시한다**(계획서 §7 R3).

★중복 제거: change_log의 rationale이 이미 카탈로그에 있는 D-NAO-N을 인용하면 그 행은 버린다
  (같은 사건을 두 줄로 보여주지 않는다 — 카탈로그 쪽 라벨이 사람 말이라 더 낫다).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import NaverChangeLog
from app.services.naver_ad import budget_pacing, change_log_narrator

log = logging.getLogger(__name__)

# repo 루트의 docs/ — 이 파일 기준 backend/app/services/naver_ad → 4단계 위가 repo 루트.
CATALOG_PATH = Path(__file__).resolve().parents[4] / "docs" / "naver_ad_improvement_events.json"

# "구조를 바꾼" 액션만 타임라인에 올린다. 나머지(update_bid·update_*_bid·flight_pacing·
# external_*)는 일상 조작이거나 우리 개선이 아니다(external_* = 대행사·수동 외부 조작).
#
# ★BP 레인은 라벨을 갈라 기록한다(budget_up_pacing/budget_down_pacing) — `update_budget`만
#   세면 예산 자동 증액(D-NAO-102)이 한 건도 안 나온다. perf_today_harness가 같은 함정을 겪고
#   남긴 경고 주석(BUDGET_ACTIONS)을 그대로 따라 `budget_pacing.PACING_ACTIONS`를 합친다.
#   상수를 복제하지 않는다 — 복제하면 BP가 라벨을 또 늘렸을 때 여기만 조용히 뒤처진다.
# ★`create_*`·`auto_operate_change`는 app 코드에 writer가 없지만 **prod에는 실제 행이 있다**
#   (라이브 실측 2026-07-28: create_campaign 1·create_adgroup 3·create_ad 3·
#   auto_operate_change 2). 도구/스크립트 경로로 기록된 것이라 목록에서 빼지 않는다.
STRUCTURAL_ACTIONS = frozenset({
    "optimizer_change",           # 관리주체 전환(MOP↔우리↔없음)
    "auto_operate_change",        # 자동운영 켜기/끄기(킬스위치)
    "update_expert_delegation",   # 위임 스위치(Jino 전속 다이얼)
    "set_loss_policy",            # 손실 정책 변경
    "set_user_lock",              # 엔티티 잠금
    "set_campaign_lock",
    "set_adgroup_lock",
    "set_keyword_lock",
    "system_status_change",       # 시스템 사유 정지/해제
    "create_campaign",            # 신규 광고 개설
    "create_adgroup",
    "create_ad",
    "exclude_search_term",        # 검색어 제외(PX)
    "add_negative_keyword",
    "add_restricted_keywords",    # 제외 키워드 등록/해제
    "delete_restricted_keywords",
    "update_budget",              # 예산 변경(봉투 램프)
    "update_campaign_budget",
} | set(budget_pacing.PACING_ACTIONS))   # BP 자동 증액·원복(D-NAO-102)

_DNAO_RE = re.compile(r"D-NAO-(\d+)")


def load_catalog(path: Path | None = None) -> tuple[list[dict], bool]:
    """(events, available). 파일 부재·깨짐은 예외가 아니라 available=False다."""
    p = path or CATALOG_PATH
    if not p.exists():
        return [], False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("개선 이벤트 카탈로그를 읽지 못했습니다(%s): %s", p, exc)
        return [], False
    events = [_coerce(e) for e in data.get("events", []) if isinstance(e, dict)]
    return [e for e in events if e is not None], True


def _coerce(row: dict) -> dict | None:
    """사람이 손으로 고칠 수 있는 파일이다(계획서 §3-2 curated 경로) — **형태를 믿지 않는다.**

    ref_key가 없거나 날짜가 문자열이 아니면 그 행만 버린다. 종전엔 그런 행 하나가
    `KeyError: 'ref_key'` / `TypeError: '<' not supported`로 **페이지 전체를 500**으로 만들었다
    (리뷰 P2-7). "파일이 없어도 안 죽는다"만 지키고 "파일이 이상해도"를 안 지킨 것이다.
    """
    ref = row.get("ref_key")
    if not isinstance(ref, str) or not ref:
        log.warning("개선 이벤트 카탈로그: ref_key 없는 행을 건너뜁니다(%r)", row)
        return None
    eff = row.get("effective_date")
    if eff is not None and not isinstance(eff, str):
        log.warning("개선 이벤트 카탈로그: %s의 effective_date 형식이 잘못돼 무시합니다", ref)
        eff = None
    out = dict(row)
    out["ref_key"] = ref
    out["effective_date"] = eff
    out["label_ko"] = str(row.get("label_ko") or "")
    out["detail_ko"] = str(row.get("detail_ko") or "")
    out["scope"] = row.get("scope") if row.get("scope") in ("account", "campaign") else "account"
    return out


def _is_real_change(row: NaverChangeLog) -> bool:
    """값이 실제로 달라진 행만. 매일 08:50 예산 봉투 램프처럼 before==after인 no-op은 뺀다.

    라이브 실측: `update_budget`이 매일 같은 시각 같은 값으로 기록돼 있어(봉투 재확인),
    거르지 않으면 타임라인이 "예산을 바꿨습니다" 90줄로 덮인다.

    ★예외 — `outcome='failed'` 행은 before/after가 **의도적으로 비어 있다**(쓰기 실패라
      반영 여부를 모름). change_log_narrator가 이걸 STATE_UNKNOWN이라는 별도 상태로 반드시
      말하라고 규정한 이유가 D-NAO-54("차단 2건이 어느 화면에도 안 떴다")다. 여기서 조용히
      버리면 같은 사고를 반복한다(리뷰 P2-11).
    """
    if row.outcome == "failed":
        return True
    before = (row.before_value or "").strip()
    after = (row.after_value or "").strip()
    if not before and not after:
        return False
    return before != after


def from_change_log(
    db: Session, *, since: date, campaign_id: str | None = None, catalog_refs: set[str] | None = None,
) -> list[dict]:
    """라이브 구조 변경 → 이벤트 목록. 문장은 change_log_narrator를 그대로 재사용한다."""
    since_at = datetime.combine(since, datetime.min.time())   # changed_at은 KST-naive
    q = (
        db.query(NaverChangeLog)
        .filter(
            NaverChangeLog.changed_at >= since_at,
            NaverChangeLog.action.in_(tuple(STRUCTURAL_ACTIONS)),
        )
        .order_by(NaverChangeLog.changed_at.asc())
    )
    if campaign_id:
        q = q.filter(NaverChangeLog.campaign_id == campaign_id)
    rows = [r for r in q.all() if _is_real_change(r)]

    refs = catalog_refs or set()
    kept: list[NaverChangeLog] = []
    for row in rows:
        cited = {f"D-NAO-{n}" for n in _DNAO_RE.findall(row.rationale or "")}
        if cited & refs:  # 카탈로그가 이미 같은 사건을 사람 말로 갖고 있다
            continue
        kept.append(row)
    if not kept:
        return []

    narrated = change_log_narrator.narrate(db, kept)

    # ★한 사건을 여러 줄로 쪼개지 않는다(라이브 실측): 신규 광고 개설 한 번이
    #   create_campaign + create_adgroup×N + create_ad×M 으로 흩어져 기록되고, 그대로 실으면
    #   "설정을 바꿨습니다"가 6줄 반복된다. (그날·그 광고·그 액션)으로 접고 건수를 덧붙인다.
    groups: dict[tuple, dict] = {}
    for row, n in zip(kept, narrated):
        day = row.changed_at.date() if row.changed_at else None
        if day is None:
            continue
        key = (day, row.campaign_id or "", row.action)
        g = groups.get(key)
        if g is None:
            groups[key] = {
                "ref_key": f"change-{day.isoformat()}-{row.campaign_id or 'account'}-{row.action}",
                "decided_date": day.isoformat(),
                "effective_date": day.isoformat(),
                # 로그 타임스탬프 = 실제 적용 시각(git 매칭 추정이 아니다)
                "effective_confidence": "log",
                "label_ko": n["sentence"],
                "detail_ko": "",
                "scope": "campaign" if row.campaign_id else "account",
                "campaign_id": row.campaign_id,
                "campaign_name": n.get("campaign_name"),
                "source": "change_log",
                "curated": False,
                "row_count": 1,
            }
        else:
            g["row_count"] += 1
    out = list(groups.values())
    for g in out:
        if g["row_count"] > 1:
            g["label_ko"] = f"{g['label_ko']} (같은 날 {g['row_count']}건)"
    return out


def collect(
    db: Session, *, days: int = 90, campaign_id: str | None = None,
    today: date | None = None, catalog_path: Path | None = None,
) -> dict:
    """카탈로그 ∪ 라이브 변경 → 날짜순 이벤트 목록.

    ★날짜 없는 카탈로그 행은 타임라인에 **올리지 않는다** — 차트에 놓을 자리가 없는데 억지로
      놓으면 없는 시점을 지어내는 것이다(원칙22). 대신 몇 건을 뺐는지 응답에 남긴다.
    ★계정 범위(scope=account) 이벤트는 캠페인 필터가 걸려도 남는다 — 계정 전체에 적용된
      변경이라 그 캠페인에도 실제로 영향을 준다.
    """
    day = today or date.today()
    since = day - timedelta(days=days)

    catalog, available = load_catalog(catalog_path)
    refs = {e.get("ref_key") for e in catalog if e.get("ref_key")}

    undated = 0
    events: list[dict] = []
    for e in catalog:
        eff = e.get("effective_date")
        if not eff:
            undated += 1
            continue
        if eff < since.isoformat():
            continue
        if campaign_id and e.get("scope") == "campaign" and e.get("campaign_id") != campaign_id:
            continue
        events.append({**e, "source": e.get("source", "track")})

    events.extend(
        from_change_log(db, since=since, campaign_id=campaign_id, catalog_refs=refs)
    )
    events.sort(key=lambda e: (e["effective_date"], e["ref_key"]))
    return {
        "catalog_available": available,
        "undated_catalog_count": undated,
        "events": events,
    }
