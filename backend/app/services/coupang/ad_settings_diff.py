# ad_settings_diff.py — 쿠팡 광고 설정 스냅샷 → 변경 이력 (트랙 coupang-ad-change-log).
"""역할(단일 책임): 두 벌의 조회 결과를 받아 **스냅샷을 갱신하고 변경 행을 남긴다**. 네트워크 0.

★쿠팡은 **모든 변경이 외부다** — 우리가 쿠팡 광고를 쓰는 경로가 없어서 네이버의
  `naver_change_log`(우리 실집행) 같은 원천이 원리적으로 안 생긴다. 전량이 스냅샷 diff 유래다.

★두 축을 가른다 (D-CAC-3, Jino 원문: "새로 생긴 캠페인이 있는지, Off가 된 캠페인이 있는지를
  파악하고 캠페인의 내용 변경에 대해서는 On되어 있는 캠페인만 보면 되는거야"):
    A. 존재·On/Off → `all_rows`(전량). 신규 / Off·On 전환 / 삭제.
    B. 내용 변경   → `active_rows`(활성만, 전체 필드 + groupList).
  왜 이 분리가 필요한가: 오하이테크 캠페인이 525개인데 활성은 16개다. 전량을 필드 단위로 매번
  비교할 이유가 없다(서버가 `isActive:true` 필터를 받아준다 — 활성은 1콜).

★성과 필드는 절대 넣지 않는다. `spentBudget`이 20분에 6,501→6,592로 움직인 걸 실측했다
  (2026-08-04 오픽스). 이걸 diff하면 매일 아침 "전 캠페인 수정됨"이 뜬다 — 네이버에서 소재
  `editTm`이 상품 피드 재적용으로 전진해 `ad_edit`이 229:4로 오염된 것과 같은 함정이다.
  그래서 **허용목록(allowlist)** 방식이다. 거부목록이면 쿠팡이 필드를 하나 추가할 때마다
  조용히 노이즈가 는다. 대신 모르는 키는 세어서 결과에 실어 보낸다(가려지지 않게).

★발생 시각: 쿠팡 `updatedAt`이 있으면 그것(`time_basis='src'`), 없으면 감지 시각
  (`time_basis='detected'`). 모르는 걸 아는 척하지 않는다 — 네이버에서 감지일로 귀속했다가
  07-30 변경을 08-03으로 잡은 실사고가 있었다.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CoupangAdChangeLog, CoupangAdEntitySnapshot

log = logging.getLogger("coupang.ad_settings_diff")

ACCOUNT_OFIX = "ofix"
ACCOUNT_OHITECH = "ohitech"
ACCOUNTS = (ACCOUNT_OFIX, ACCOUNT_OHITECH)

ENTITY_CAMPAIGN = "campaign"
ENTITY_ADGROUP = "adgroup"

OP_CREATED = "created"
OP_TURNED_ON = "turned_on"
OP_TURNED_OFF = "turned_off"
OP_DELETED = "deleted"
OP_FIELD = "field_change"

SOURCE_SNAPSHOT = "snapshot"
SOURCE_COUPANG = "coupang"


def _is_unique_violation(exc: IntegrityError) -> bool:
    """이 테이블의 UNIQUE 위반인가 — **방언에 기대지 않고** 판별한다.

    ★왜 문자열 하나로는 안 되나: SQLite는 `UNIQUE constraint failed: coupang_ad_change_log.account,…`,
      PostgreSQL은 `duplicate key value violates unique constraint "uq_coupang_ad_change_log"`로
      같은 사건을 다르게 말한다. SQLite 문구만 보면 **PG 이관 순간 500이 그대로 부활한다**
      (이 앱의 DB 축은 SQLite→PostgreSQL이다 — CLAUDE.md 기술 스택). 1순위는 SQLSTATE 23505
      (psycopg2 `pgcode` / psycopg3 `sqlstate`·`diag.sqlstate`), 그게 없으면 양쪽 문구를 다 본다.

    ★테이블 확인은 남긴다: 두 방언 모두 메시지에 `coupang_ad_change_log`가 들어간다(PG는 제약명
      `uq_coupang_ad_change_log`로). 못 알아보면 **삼키지 않고 올리는 쪽**으로 틀린다.
    """
    orig = getattr(exc, "orig", None)
    msg = str(orig if orig is not None else exc)
    if "coupang_ad_change_log" not in msg:
        return False          # 다른 테이블의 무결성 오류 — 중복으로 오분류하지 않는다
    sqlstate = (getattr(orig, "pgcode", None)
                or getattr(orig, "sqlstate", None)
                or getattr(getattr(orig, "diag", None), "sqlstate", None))
    if sqlstate == "23505":   # PG: unique_violation
        return True
    low = msg.lower()
    return "unique constraint failed" in low or "duplicate key value" in low


def safe_add_change_log(db: Session, obj: CoupangAdChangeLog) -> bool:
    """CoupangAdChangeLog 1행 삽입 — UNIQUE 충돌은 SAVEPOINT로 격리해 조용히 흡수한다.

    ★왜 필요한가(2026-08-05 500 사고): 이 세션은 `autoflush=False`(database.py)라, "먼저 SELECT로
      존재를 확인하고 없으면 add" 패턴만으로는 **같은 요청 안에서** 다른 서비스가 방금 add(아직
      flush 안 됨)한 행이 안 보인다 — events(쿠팡 이력)를 먼저 넣고 settings-diff(스냅샷)를 뒤에
      돌리는 순서 자체가 "겹치면 쿠팡이 이긴다"를 이 가시성에 의존하고 있었다. 여기서 즉시 flush해
      다음 삽입 시도가 항상 최신 상태를 보게 만든다. 동시 요청 경합(진짜 UNIQUE 충돌)도 같은
      경로로 흡수된다 — 배치 하나 때문에 전체 트랜잭션이 죽지 않는다.

    ★진입 즉시 `db.flush()` 하는 이유 — **SAVEPOINT가 자기 INSERT 하나만 담게 만든다**:
      `autoflush=False`에서 이 시점까지 쌓인 미flush 쓰기(예: `ingest_ads`가 방금 add한 신규 소재
      스냅샷, `ingest`가 갱신한 `present`/`settings_json`)가 savepoint **안에서** 함께 기록되면,
      UNIQUE 충돌 시 `ROLLBACK TO SAVEPOINT`가 그것들까지 되돌린다. flush가 끝난 객체는 다시
      dirty로 표시되지 않으므로 되돌아간 쓰기는 **영영 재시도되지 않는다** — "배치 한 건 실패가
      배치 전체를 죽이지 않는다"가 "조용히 버린다"로 뒤집힌다.
      실측(SQLAlchemy 2.0.48): `begin_nested()`는 `SessionTransaction._take_snapshot()`에서
      SAVEPOINT를 걸기 **전에** 이미 `session.flush()`를 부르므로 오늘 이 사고는 나지 않는다.
      그래도 명시적으로 부르는 값이 있다 — ①불변식이 라이브러리 내부 구현이 아니라 이 함수에
      적혀 있게 되고, ②그 flush가 `except IntegrityError` **밖으로** 나와, 남의 미flush 쓰기가
      낸 오류를 우리 "중복"으로 오분류할 여지가 사라진다. 비용은 0에 가깝다 —
      `Session.flush()`는 세션이 깨끗하면 즉시 반환하고, 어차피 `begin_nested()`가 할 일이다.

    반환: True=삽입됨, False=이미 같은 키가 있어 스킵(호출자가 "중복" 카운트). UNIQUE 제약 위반이
      아닌 무결성 오류는 **삼키지 않고** 그대로 올린다(데이터 오류를 중복으로 오분류하지 않는다).
    """
    db.flush()
    try:
        with db.begin_nested():
            db.add(obj)
            db.flush()
    except IntegrityError as exc:
        if not _is_unique_violation(exc):
            raise
        return False
    return True


def trunc_second(dt: datetime) -> datetime:
    """★두 원천의 시각을 **초 단위로 맞춘다**.

    쿠팡 `executionTime`은 초까지("2026-08-04T01:51:21Z")인데 우리가 쓰는 `updatedAt`은
    밀리초까지다("...:21.372Z"). 절삭하지 않으면 **같은 사건이 영영 안 겹쳐** 두 줄로 뜬다.
    """
    return dt.replace(microsecond=0)

# ── 설정 필드 허용목록 ────────────────────────────────────────────────
# isActive는 여기 없다 — On/Off는 별도 축(op)이라 넣으면 같은 사건이 두 줄로 뜬다.
CAMPAIGN_FIELDS = (
    "name", "budget", "capType", "budgetRule", "budgetSharingGroupId",
    "roasTarget", "targetType", "objective", "goalType", "campaignType",
    "startDate", "endDate", "isSuspended", "disabledByCoupang", "uneditable",
    "isAgencyManaged", "promotionScheme",
)
ADGROUP_FIELDS = (
    "name", "bidType", "pricingType", "pricing", "roasTarget", "andonRoasTarget",
    "keywordTargeting", "budget", "budgetType", "startDate", "endDate",
    "adSelectionType", "smartTargetingPricingType", "smartTargetingPricing",
    "adDiscountOptIn",
)
# 성과·파생 필드 — 허용목록 밖이라 어차피 안 들어오지만, "왜 뺐는지"를 코드에 남긴다.
KNOWN_VOLATILE = (
    "spentBudget", "calculatedBudget", "averageTimeBudgetUtilRate", "servingStatus",
    "learningStatus", "totalAdCount", "apsAdsLength", "apsAdsWithImpressionLength",
    "apsImpressionStartDate", "fundingSupportAmount", "version", "createdAt", "updatedAt",
)


def _parse_dt(v: Any) -> datetime | None:
    """쿠팡 ISO8601(Z) → **UTC naive**. 파싱 실패는 None(없는 것과 같게 다룬다).

    UTC naive로 통일하는 이유: 이 앱의 다른 쿠팡 시각(po_created_at 등)이 전부 UTC naive이고,
    KST 환산은 표시층에서 한다. 여기서 KST로 바꾸면 저장층에 두 규약이 섞인다.
    """
    if not v or not isinstance(v, str):
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _norm(v: Any) -> str | None:
    """비교·저장용 정규화. dict/list는 정렬된 JSON으로(키 순서 흔들림이 가짜 변경이 되지 않게)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    return str(v)


def _settings_of(raw: dict, fields: Iterable[str]) -> dict[str, str | None]:
    return {f: _norm(raw.get(f)) for f in fields if f in raw}


def _load_snapshots(db: Session, account: str) -> dict[tuple[str, str], CoupangAdEntitySnapshot]:
    rows = (
        db.query(CoupangAdEntitySnapshot)
        .filter(CoupangAdEntitySnapshot.account == account)
        .all()
    )
    return {(r.entity_type, r.entity_id): r for r in rows}


class _Recorder:
    """변경 행 수집기 — 같은 회차 안의 중복을 먼저 걸러 UNIQUE 충돌을 만들지 않는다."""

    def __init__(self, account: str, detected_at: datetime):
        self.account = account
        self.detected_at = detected_at
        self._seen: set[tuple] = set()
        self.rows: list[CoupangAdChangeLog] = []

    def add(self, *, entity_type: str, entity_id: str, campaign_id: str, name: str,
            op: str, field: str = "", before: str | None = None, after: str | None = None,
            src_updated_at: datetime | None = None) -> None:
        occurred_at = trunc_second(src_updated_at or self.detected_at)
        basis = "src" if src_updated_at else "detected"
        key = (entity_type, entity_id, op, field, occurred_at)
        if key in self._seen:
            return
        self._seen.add(key)
        self.rows.append(CoupangAdChangeLog(
            account=self.account, entity_type=entity_type, entity_id=entity_id,
            campaign_id=campaign_id, entity_name=name, op=op, field=field,
            before_value=before, after_value=after,
            occurred_at=occurred_at, time_basis=basis, detected_at=self.detected_at,
            source=SOURCE_SNAPSHOT,
        ))


def _persist(db: Session, rows: list[CoupangAdChangeLog]) -> tuple[int, int, int]:
    """UNIQUE 위반은 '이미 기록된 변경'이므로 조용히 건너뛴다(회차 재실행 idempotent).

    ★같은 키에 **쿠팡 유래** 행이 있으면 덮지 않는다 — 쿠팡은 전/후 값을 갖고 있고 우리는
      시각만 안다. 우선순위가 한 방향(coupang > snapshot)이라 어느 쪽이 먼저 들어와도 결과가 같다.

    사전 SELECT는 흔한 경우(진짜 신규/진짜 기존)를 빠르게 가르는 최적화일 뿐이고, 실제 idempotency
    보장은 `safe_add_change_log`(SAVEPOINT)가 진다 — 사전 SELECT가 "없다"고 오판해도(같은 요청 안의
    다른 서비스가 방금 넣은 미flush 행 등) 삽입 시점에 다시 걸러진다.

    반환: (written, duplicate, race_absorbed).
      ★duplicate와 race_absorbed를 **한 칸에 합치지 않는다**: duplicate은 "페처가 90일 이력을
        통째로 재push했다"는 평시 신호(매 회차 수백 건이 정상)이고, race_absorbed는 "사전 SELECT와
        INSERT 사이에 누가 같은 키를 넣었다"는 **경합** 신호다(평시 0이어야 한다). 한 칸에 더하면
        평시 잡음이 경합을 덮어 영영 안 보인다.
    """
    written = duplicate = raced = 0
    for r in rows:
        exists = (
            db.query(CoupangAdChangeLog.id)
            .filter(
                CoupangAdChangeLog.account == r.account,
                CoupangAdChangeLog.entity_type == r.entity_type,
                CoupangAdChangeLog.entity_id == r.entity_id,
                CoupangAdChangeLog.op == r.op,
                CoupangAdChangeLog.field == r.field,
                CoupangAdChangeLog.occurred_at == r.occurred_at,
            )
            .first()
        )
        if exists:
            duplicate += 1
            continue
        if safe_add_change_log(db, r):
            written += 1
        else:
            raced += 1
    return written, duplicate, raced


def ingest(db: Session, account: str, all_rows: list[dict], active_rows: list[dict],
           *, detected_at: datetime | None = None) -> dict:
    """스냅샷 갱신 + 변경 행 기록.

    all_rows:    전량 조회(`isActive` 무필터). id·name·isActive만 쓴다.
    active_rows: 활성 조회(`isActive:true`, 전체 필드 + `groupList`). 내용 diff는 이것만.

    ★all_rows가 비면 아무것도 하지 않는다 — 조회 실패를 "전부 삭제됨"으로 오독하면
      change_log가 통째로 오염된다(되돌리기 어렵다).
    """
    if account not in ACCOUNTS:
        raise ValueError(f"알 수 없는 계정: {account}")
    detected_at = detected_at or datetime.now(timezone.utc).replace(tzinfo=None)
    if not all_rows:
        log.warning("[%s] 전량 조회가 비었다 — 삭제 오판을 막기 위해 이번 회차는 건너뛴다.", account)
        return {"account": account, "skipped": "empty_all_rows", "changes": 0}

    snaps = _load_snapshots(db, account)
    rec = _Recorder(account, detected_at)
    active_by_id = {str(c.get("id")): c for c in active_rows if c.get("id") is not None}
    unknown_keys: set[str] = set()

    seen_ids: set[str] = set()

    # ── A축: 존재 · On/Off (전량) ────────────────────────────────────
    for raw in all_rows:
        cid = str(raw.get("id"))
        if not cid or cid == "None":
            continue
        seen_ids.add(cid)
        name = str(raw.get("name") or "")
        is_active = bool(raw.get("isActive"))
        src_at = _parse_dt(raw.get("updatedAt"))
        snap = snaps.get((ENTITY_CAMPAIGN, cid))

        if snap is None:
            rec.add(entity_type=ENTITY_CAMPAIGN, entity_id=cid, campaign_id=cid, name=name,
                    op=OP_CREATED, after=name, src_updated_at=_parse_dt(raw.get("createdAt")))
            snap = CoupangAdEntitySnapshot(
                account=account, entity_type=ENTITY_CAMPAIGN, entity_id=cid, campaign_id=cid,
                name=name, is_active=is_active, settings_json=None,
                src_updated_at=src_at, observed_at=detected_at, present=True,
            )
            db.add(snap)
            snaps[(ENTITY_CAMPAIGN, cid)] = snap
            continue

        if bool(snap.is_active) != is_active:
            rec.add(entity_type=ENTITY_CAMPAIGN, entity_id=cid, campaign_id=cid, name=name,
                    op=OP_TURNED_ON if is_active else OP_TURNED_OFF,
                    before="on" if snap.is_active else "off",
                    after="on" if is_active else "off", src_updated_at=src_at)
        snap.name = name
        snap.is_active = is_active
        snap.observed_at = detected_at
        snap.present = True
        if src_at:
            snap.src_updated_at = src_at

    # 삭제: 지난 회차엔 있었는데(present) 이번 전량 조회에 없다.
    # ★Off와 구분된다 — Off는 목록에 남는다(isActive=false).
    for (etype, eid), snap in snaps.items():
        if etype != ENTITY_CAMPAIGN or eid in seen_ids or not snap.present:
            continue
        rec.add(entity_type=ENTITY_CAMPAIGN, entity_id=eid, campaign_id=eid, name=snap.name,
                op=OP_DELETED, before=snap.name)
        snap.present = False
        snap.observed_at = detected_at

    # ── B축: 내용 변경 (활성만) ──────────────────────────────────────
    for cid, raw in active_by_id.items():
        name = str(raw.get("name") or "")
        src_at = _parse_dt(raw.get("updatedAt"))
        snap = snaps.get((ENTITY_CAMPAIGN, cid))
        if snap is None:      # 전량에 없던 활성 — 있을 수 없지만 조용히 지나가지 않는다
            log.warning("[%s] 활성 캠페인 %s가 전량 조회에 없다 — 이번 회차 내용 diff 생략.", account, cid)
            continue
        unknown_keys |= {k for k in raw if k not in CAMPAIGN_FIELDS
                         and k not in KNOWN_VOLATILE and k != "groupList"}
        cur = _settings_of(raw, CAMPAIGN_FIELDS)
        _diff_into(rec, snap, cur, ENTITY_CAMPAIGN, cid, cid, name, src_at)
        snap.settings_json = json.dumps(cur, ensure_ascii=False, sort_keys=True)

        for g in (raw.get("groupList") or []):
            gid = str(g.get("id"))
            if not gid or gid == "None":
                continue
            gname = str(g.get("name") or "")
            g_src = _parse_dt(g.get("updatedAt"))
            gsnap = snaps.get((ENTITY_ADGROUP, gid))
            gcur = _settings_of(g, ADGROUP_FIELDS)
            if gsnap is None:
                rec.add(entity_type=ENTITY_ADGROUP, entity_id=gid, campaign_id=cid, name=gname,
                        op=OP_CREATED, after=gname, src_updated_at=_parse_dt(g.get("createdAt")))
                gsnap = CoupangAdEntitySnapshot(
                    account=account, entity_type=ENTITY_ADGROUP, entity_id=gid, campaign_id=cid,
                    name=gname, is_active=bool(g.get("isActive", True)),
                    settings_json=json.dumps(gcur, ensure_ascii=False, sort_keys=True),
                    src_updated_at=g_src, observed_at=detected_at, present=True,
                )
                db.add(gsnap)
                snaps[(ENTITY_ADGROUP, gid)] = gsnap
                continue
            g_active = bool(g.get("isActive", True))
            if bool(gsnap.is_active) != g_active:
                rec.add(entity_type=ENTITY_ADGROUP, entity_id=gid, campaign_id=cid, name=gname,
                        op=OP_TURNED_ON if g_active else OP_TURNED_OFF,
                        before="on" if gsnap.is_active else "off",
                        after="on" if g_active else "off", src_updated_at=g_src)
            _diff_into(rec, gsnap, gcur, ENTITY_ADGROUP, gid, cid, gname, g_src)
            gsnap.name = gname
            gsnap.is_active = g_active
            gsnap.settings_json = json.dumps(gcur, ensure_ascii=False, sort_keys=True)
            gsnap.observed_at = detected_at
            gsnap.present = True
            if g_src:
                gsnap.src_updated_at = g_src

    written, duplicate, raced = _persist(db, rec.rows)
    db.flush()
    if raced:
        # 평시 0이어야 하는 값이다 — 0이 아니면 같은 키를 두 경로가 동시에 쓰고 있다는 뜻.
        log.warning("[%s] UNIQUE 경합 %d건을 SAVEPOINT가 흡수했다(사전 SELECT 이후 삽입됨).",
                    account, raced)
    if unknown_keys:
        # 허용목록 밖 = 이번엔 안 봤다는 뜻. 가려지지 않게 남긴다(설계 재검토 신호).
        log.info("[%s] 허용목록 밖 캠페인 필드 %d종: %s",
                 account, len(unknown_keys), sorted(unknown_keys)[:12])
    return {
        "account": account,
        "campaigns_total": len(seen_ids),
        "campaigns_active": len(active_by_id),
        "changes": written,
        "changes_duplicate": duplicate,       # 사전 SELECT가 이미 있다고 본 것(평시 정상)
        "changes_race_absorbed": raced,       # SAVEPOINT가 흡수한 진짜 경합(평시 0)
        "unknown_field_count": len(unknown_keys),
    }


def _diff_into(rec: _Recorder, snap: CoupangAdEntitySnapshot, cur: dict[str, str | None],
               entity_type: str, entity_id: str, campaign_id: str, name: str,
               src_at: datetime | None) -> None:
    """이전 settings_json과 비교해 필드별 변경 행을 만든다.

    ★settings_json이 없으면(=이 엔티티의 설정을 이번에 처음 본다) 한 줄도 만들지 않는다.
      Off였다가 On된 캠페인은 그 사이 설정을 추적한 적이 없다 — 없던 걸 "방금 바뀐 것"처럼
      쏟아내면 거짓이다. 이번 값이 기준선이 되고, 다음 회차부터 잡힌다.
    """
    if not snap.settings_json:
        return
    try:
        prev = json.loads(snap.settings_json)
    except (ValueError, TypeError):
        log.warning("settings_json 파손(%s %s) — 이번 회차는 기준선만 갱신한다.", entity_type, entity_id)
        return
    if not isinstance(prev, dict):
        return
    for f in sorted(set(prev) | set(cur)):
        before, after = prev.get(f), cur.get(f)
        if before == after:
            continue
        rec.add(entity_type=entity_type, entity_id=entity_id, campaign_id=campaign_id,
                name=name, op=OP_FIELD, field=f, before=before, after=after,
                src_updated_at=src_at)
