# entity_sync.py — naver_entity_sync_harness (캠페인/그룹/키워드 인벤토리 동기화, P2-S1)
# 역할: /ncc 캠페인·그룹·키워드를 순회 수집(collect_entities, 순수 SA) →
#   naver_entity에 전체 snapshot 교체 적재(sync_entities, 쓰기 harness).
# 키워드 행은 WEB_SITE(파워링크)만 수집 — 실측(docs/references/22): SHOPPING은 AD 리포트에서
#   keyword_id='-'(그룹 단위)로만 집계되어 개별 키워드 진단 대상이 아님. campaign·adgroup 행은
#   전 유형 수집(진단 보드 이름 표시용).
from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.models import NaverChangeLog, NaverEntity
from app.services.naver_sa_ad_fetcher import get_adgroups, get_campaigns_full, get_keywords
from app.utils.kst import kst_now

log = logging.getLogger(__name__)


def _status(raw_status: str, user_lock: bool) -> str:
    """네이버 status(ELIGIBLE 등)+userLock(수동 OFF)을 on/off/deleted로 정규화."""
    if raw_status in ("DELETED", ""):
        return "deleted"
    if user_lock:
        return "off"
    return "on"


def collect_entities(
    *,
    campaigns: list[dict] | None = None,
    adgroups_by_campaign: dict[str, list[dict]] | None = None,
    keywords_by_adgroup: dict[str, list[dict]] | None = None,
) -> list[dict]:
    """캠페인→그룹→(WEB_SITE만)키워드를 순회해 naver_entity 행 형태 dict 리스트로 반환.

    campaigns/adgroups_by_campaign/keywords_by_adgroup은 테스트·재사용 주입용(원칙18-8).
    미주입 시 fetcher에서 실시간 조회(그룹은 캠페인마다, 키워드는 WEB_SITE 그룹마다 1콜).
    """
    if campaigns is None:
        campaigns = get_campaigns_full()

    rows: list[dict] = []
    for c in campaigns:
        cid = c["campaign_id"]
        ctype = c.get("campaign_type", "")
        rows.append({
            "entity_type": "campaign", "entity_id": cid, "parent_id": "",
            "campaign_id": cid, "campaign_type": ctype, "name": c.get("name", ""),
            "status": "off" if str(c.get("status", "")).upper() == "PAUSED" else "on",
            "bid_amt": None,
        })

        ags = (adgroups_by_campaign or {}).get(cid) if adgroups_by_campaign is not None else get_adgroups(cid)
        for ag in ags or []:
            aid = ag["adgroup_id"]
            rows.append({
                "entity_type": "adgroup", "entity_id": aid, "parent_id": cid,
                "campaign_id": cid, "campaign_type": ctype, "name": ag.get("name", ""),
                "status": _status(ag.get("status", ""), ag.get("user_lock", False)),
                "bid_amt": ag.get("bid_amt"),
            })

            if ctype != "WEB_SITE":
                continue  # 실측: SHOPPING/BRAND_SEARCH 키워드는 개별 진단 대상 아님
            kws = (keywords_by_adgroup or {}).get(aid) if keywords_by_adgroup is not None else get_keywords(aid)
            for kw in kws or []:
                rows.append({
                    "entity_type": "keyword", "entity_id": kw["keyword_id"], "parent_id": aid,
                    "campaign_id": cid, "campaign_type": ctype, "name": kw.get("keyword", ""),
                    "status": _status(kw.get("status", ""), kw.get("user_lock", False)),
                    "bid_amt": kw.get("bid_amt"),
                    "qi_grade": kw.get("qi_grade"),  # D-NAO-46②: 품질지수 1~7(get_keywords 무상 편승)
                })

    log.info("naver_entity collect: campaign=%d adgroup=%d keyword=%d",
              sum(1 for r in rows if r["entity_type"] == "campaign"),
              sum(1 for r in rows if r["entity_type"] == "adgroup"),
              sum(1 for r in rows if r["entity_type"] == "keyword"))
    return rows


def _log_external_status_change(db: Session, entity: NaverEntity, new_status: str, now) -> None:
    """D-NAO-40: 우리 change_log에 없는 외부 상태 변경을 감지하면 기록한다.
    우리 실행으로 인한 변경(최근 set_user_lock 성공 기록과 방향이 일치 **그리고** 그 쓰기가
    지난 관측(entity.synced_at) 이후에 실제로 일어났을 때)이면 건너뛴다.

    ⚠️ 방향 일치만으로 판단하면 안 된다(원 버그): 우리 정지→외부 재개→외부 재정지 시퀀스에서
    마지막 외부 재정지는 방향이 (우리의 옛 정지 기록과) 우연히 같아, 그 사이 sync가 없었던
    것처럼 오인해 스킵되어 버렸다. 그러면 resume_candidates가 옛 우리 정지를 최신 잠금변경으로
    착각해 외부/사람이 정지한 것을 임의로 재개해버리는 안전사고로 이어진다.

    시간 판별: last_our_write.changed_at이 entity.synced_at(이번 sync로 갱신되기 *전*의
    직전 관측 시각 — 호출부가 e.synced_at=now 갱신 이전에 이 함수를 호출하는 것에 의존)보다
    나중이어야만 "우리가 방금 한 변경"으로 인정한다. 즉 직전 관측 이후 실제로 쓰기가 없었다면
    방향이 우연히 같아도 외부 변경으로 기록한다.

    changed_at·synced_at 모두 kst_now()로 명시 기록되는 KST naive datetime이라 직접 비교
    가능(SQLite server_default=func.now()는 UTC라 다르지만 여기선 둘 다 명시 값만 사용).
    entity.synced_at이 없거나(비정상 데이터) 판단 근거가 불충분하면 fail-closed로 스킵하지
    않는다 — 최악의 경우도 "우리가 방금 한 변경을 외부로 오기록"일 뿐이라 resume_candidates가
    그 이후 재개를 보수적으로 건너뛰게 만들 뿐 안전하다."""
    old_lock = entity.status == "off"
    new_lock = new_status == "off"
    if old_lock == new_lock:
        return

    last_our_write = (
        db.query(NaverChangeLog)
        .filter(
            NaverChangeLog.entity_type == entity.entity_type,
            NaverChangeLog.entity_id == entity.entity_id,
            NaverChangeLog.action == "set_user_lock",
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),
        )
        .order_by(NaverChangeLog.changed_at.desc())
        .first()
    )
    if (
        last_our_write
        and last_our_write.after_value
        and entity.synced_at is not None
        and last_our_write.changed_at > entity.synced_at
    ):
        try:
            last_after = json.loads(last_our_write.after_value)
            if isinstance(last_after, dict) and last_after.get("userLock") == new_lock:
                return
        except (ValueError, TypeError):
            pass

    db.add(NaverChangeLog(
        entity_type=entity.entity_type,
        entity_id=entity.entity_id,
        campaign_id=entity.campaign_id,
        action="external_status_change",
        proposal_id=None,
        dry_run=False,
        changed_at=now,
        before_value=json.dumps({"userLock": old_lock}),
        after_value=json.dumps({"userLock": new_lock}),
        rationale="entity_sync 감지: 외부(MOP/사람) 상태 변경",
    ))
    log.info("external_status_change detected: %s %s %s→%s",
             entity.entity_type, entity.entity_id, entity.status, new_status)


def _log_external_qi_change(db: Session, entity: NaverEntity, new_qi, now) -> None:
    """D-NAO-46②: 품질지수(qi_grade) 변화를 관찰 기록(외부 변경 — 우리는 qi에 쓰기 없음).

    기존 값과 새 값이 둘 다 non-None이고 다를 때만 기록한다(처음 관측되거나 API가 일시
    부재로 None을 반환한 경우는 잡음이라 기록 대상 아님). dry_run=True(관찰만, 실쓰기 아님)."""
    if entity.qi_grade is None or new_qi is None or entity.qi_grade == new_qi:
        return
    db.add(NaverChangeLog(
        entity_type=entity.entity_type,
        entity_id=entity.entity_id,
        campaign_id=entity.campaign_id,
        action="external_qi_change",
        proposal_id=None,
        dry_run=True,
        changed_at=now,
        before_value=str(entity.qi_grade),
        after_value=str(new_qi),
        rationale="entity_sync 감지: 품질지수(qi) 변화(관찰)",
    ))
    log.info("external_qi_change detected: %s %s %s→%s",
             entity.entity_type, entity.entity_id, entity.qi_grade, new_qi)


def sync_entities(db: Session, *, rows: list[dict] | None = None) -> dict:
    """naver_entity upsert(멱등, 일 1회 동기화) — keywordstool 보강 필드(monthly_volume 등) 보존.

    rows 미주입 시 collect_entities로 실시간 수집. 기존 행은 이름·상태·계층만 갱신하고
    monthly_volume/competition/volume_updated_at은 건드리지 않는다(별도 키워드 볼륨 갱신 잡이
    채움 — 전체 삭제 후 재삽입 시 매번 날아가는 걸 방지). 최신 수집에 없는 기존 행은
    status='deleted'로 표시(물리 삭제 안 함 — search_term_daily 등의 참조·이력 보존).
    """
    if rows is None:
        rows = collect_entities()

    existing = {(e.entity_type, e.entity_id): e for e in db.query(NaverEntity).all()}
    seen: set[tuple[str, str]] = set()
    now = kst_now()

    for r in rows:
        key = (r["entity_type"], r["entity_id"])
        seen.add(key)
        e = existing.get(key)
        if e is None:
            db.add(NaverEntity(
                entity_type=r["entity_type"], entity_id=r["entity_id"], parent_id=r["parent_id"],
                campaign_id=r["campaign_id"], campaign_type=r["campaign_type"], name=r["name"],
                status=r["status"], bid_amt=r.get("bid_amt"), qi_grade=r.get("qi_grade"), synced_at=now,
            ))
        else:
            if e.status != r["status"] and e.status != "deleted":
                _log_external_status_change(db, e, r["status"], now)
            _log_external_qi_change(db, e, r.get("qi_grade"), now)
            e.parent_id = r["parent_id"]
            e.campaign_id = r["campaign_id"]
            e.campaign_type = r["campaign_type"]
            e.name = r["name"]
            e.status = r["status"]
            e.bid_amt = r.get("bid_amt")
            e.qi_grade = r.get("qi_grade")
            e.synced_at = now

    stale = 0
    for key, e in existing.items():
        if key not in seen and e.status != "deleted":
            e.status = "deleted"
            stale += 1

    db.commit()

    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["entity_type"]] = by_type.get(r["entity_type"], 0) + 1
    log.info("naver_entity sync: %s (stale→deleted=%d)", by_type, stale)
    return {"rows": len(rows), "stale_marked_deleted": stale, **by_type}
