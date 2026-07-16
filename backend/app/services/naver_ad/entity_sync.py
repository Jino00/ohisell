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


def _norm_bid(v) -> int | None:
    """입찰가를 int|None으로 정규화 — diff 비교 전 **반드시** 통과시킨다.

    ★왜 필요한가(원칙22, 실측): NaverEntity.bid_amt는 Integer 선언이지만 SQLite는 동적
    타입이라 fetcher가 네이버 API 응답을 그대로 넘긴 값(str일 수 있음 — naver_sa_ad_fetcher
    :505는 k.get("bidAmt")를 캐스팅 없이 전달)이 그대로 저장된다. 정규화 없이 비교하면
    700(DB, int) != "700"(API, str)이 되어 **매일 91,005개 키워드 전부가 '입찰 변경'으로
    오판정**되고 naver_change_log에 91,005행/일이 쌓인다(현재 전체 17행).

    파싱 불가 값은 예외 대신 None을 반환한다 — 이 함수는 매일 07:35 크론 경로에서 91,005번
    호출되므로 쓰레기 값 하나가 동기화 전체를 죽이면 안 된다(fail-safe). None은 호출부에서
    '비교 불가 → 로깅 안 함'으로 처리된다.
    """
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


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


def _load_our_bid_writes(db: Session) -> dict[tuple[str, str], NaverChangeLog]:
    """우리의 최근 update_bid 성공 기록을 (entity_type, entity_id)→최신 1건으로 적재한다.

    ★루프 **전에 1회** 호출한다. codex[P2](2026-07-17)가 지적한 되돌림 레이스를 잡으려면
    무변동 행에서도 "우리가 방금 쓴 값이 있었나"를 봐야 하는데, 그걸 행마다 쿼리하면
    91,005번 조회한다(쓰기 폭증을 읽기 폭증으로 바꾸는 셈). 한 번에 적재해 O(1) 조회로 만든다.

    dry_run=False + after_value 존재 = 실제 성공한 쓰기(실패·가드거부·dry-run은 after_value를
    안 채운다 — naver_execution_harness의 _detect_external_change 주석과 같은 판별).
    """
    rows = (
        db.query(NaverChangeLog)
        .filter(
            NaverChangeLog.action == "update_bid",
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),
        )
        .order_by(NaverChangeLog.changed_at.asc())  # 뒤에 오는 최신이 앞을 덮어씀
        .all()
    )
    return {(r.entity_type, r.entity_id): r for r in rows}


def _our_bid_target(entity: NaverEntity, our_writes: dict[tuple[str, str], NaverChangeLog]) -> int | None:
    """직전 관측(entity.synced_at) *이후*에 우리가 실제로 써넣은 목표 입찰가. 없으면 None.

    시간 판별이 핵심이다(_log_external_status_change의 ⚠️ 주석과 같은 이유): 직전 관측보다
    오래된 우리 쓰기는 "이번 관측 구간에 우리가 한 일"이 아니므로 귀속 근거가 될 수 없다.
    after_value의 키가 camelCase 'bidAmt'인 것은 writer가 네이버 재조회 응답(get_keyword)을
    그대로 json.dumps 하기 때문(naver_sa_writer.py:350) — 'bid_amt'(snake)가 아니다.
    """
    last = our_writes.get((entity.entity_type, entity.entity_id))
    if not (last and last.after_value and entity.synced_at is not None):
        return None
    if last.changed_at <= entity.synced_at:
        return None
    try:
        after = json.loads(last.after_value)
    except (ValueError, TypeError):
        return None
    return _norm_bid(after.get("bidAmt")) if isinstance(after, dict) else None


def _log_external_bid_change(
    db: Session, entity: NaverEntity, new_bid_raw, now,
    our_writes: dict[tuple[str, str], NaverChangeLog],
) -> None:
    """D-NAO-47: 입찰가 변경을 change_log에 기록한다 — `_log_external_status_change`의 대칭.

    ★이 함수가 없던 동안 `e.bid_amt = r.get("bid_amt")`가 매일 91,005개 키워드의 어제
    입찰가를 조용히 덮어썼다(스펙 §1-5). 그래서 "우리(또는 MOP)가 CPC를 얼마에서 얼마로
    바꿨나"를 보여줄 데이터가 **아예 존재하지 않았다**(prod change_log 전체 17행 · 우리
    자동 입찰변경 0건).

    ⚠️ 쓰기 폭증 방어(이 함수의 존재 이유의 절반):
      - 무변동 행은 로깅하지 않는다. naver_entity 91,005행 중 절대다수는 매일 그대로다.
      - 비교 전 반드시 `_norm_bid()`로 정규화한다 — 타입 불일치(DB int vs API str) 하나로
        전 행이 '변경됨'이 되어 91,005행/일이 쌓인다(_norm_bid docstring 참조).
      - old/new 어느 쪽이든 None이면 로깅하지 않는다. 신규 관측·수집 누락은 '변경'이 아니고,
        특히 API 장애로 bid_amt가 전부 None이 되면 91,005행이 쏟아진다.
      - our_writes는 **호출부가 루프 전에 1회** 적재해 넘긴다(_load_our_bid_writes). 여기서
        쿼리하면 91,005번 조회한다.

    호출 계약: `e.bid_amt` 대입 **전에** 호출해야 한다(entity.bid_amt가 옛값이어야 함).
    `_log_external_status_change`가 `e.synced_at` 대입 전에 호출되는 것과 같은 이유다.

    ★되돌림 레이스(codex[P2] 2026-07-17): 우리가 700→900을 쓴 뒤 다음 sync 전에 외부가
    900→700으로 되돌리면 old_bid==new_bid(700==700)라 무변동으로 보인다. 하지만 실제로는
    외부가 우리 변경을 무효화한 것이고, 그걸 안 남기면 change_log는 "우리가 900으로 바꿈"에서
    멈춰 **현재 값이 900인 줄로 읽힌다**(실제 700). 03(MOP) vs 04(우리) 철학 대결에서 정확히
    측정하려는 시나리오라 반드시 남긴다. prod 실측상 update_bid 쓰기가 아직 0건이라 지금은
    도달 불가하지만, 카나리가 열리는 순간 도달 가능해진다.
    """
    if entity.status == "deleted":
        return

    old_bid = _norm_bid(entity.bid_amt)
    new_bid = _norm_bid(new_bid_raw)

    # ★파싱 실패를 조용히 넘기지 않는다(codex[P2] 2026-07-17): 값이 있는데 정규화가 실패하면
    # 그 행은 영영 로깅되지 않는다. prod 실측(2026-07-17)상 91,005행 전부 integer라 현재
    # 발생하지 않지만, 네이버가 형식을 바꾸면 **아무 신호 없이** 이력이 끊긴다. 추측으로
    # 콤마 파싱 같은 걸 미리 넣지 않되(원칙: 추정 금지), 벌어지면 시끄럽게 만든다.
    if new_bid_raw is not None and new_bid is None:
        log.warning(
            "naver_entity bid_amt 정규화 실패 — 이 행의 입찰 변경은 기록되지 않는다: "
            "%s %s raw=%r(%s). 네이버 응답 형식 변경 의심(원칙22: 실측 후 _norm_bid 확장).",
            entity.entity_type, entity.entity_id, new_bid_raw, type(new_bid_raw).__name__,
        )

    if old_bid is None or new_bid is None:
        return  # 신규 관측/수집 누락/파싱 실패는 변경이 아님

    our_target = _our_bid_target(entity, our_writes)

    if our_target is not None and our_target == new_bid:
        return  # 우리가 방금 써넣은 값 그대로 관측됨 = 외부 변경 아님

    if old_bid == new_bid and our_target is None:
        return  # ★절대다수가 여기서 끊긴다 — 이 한 줄이 쓰기 폭증을 막는다

    # 여기 도달하는 두 경우:
    #  (a) old != new           → 평범한 외부 변경(before=old_bid)
    #  (b) old == new 인데 our_target이 있고 new와 다름 → 외부가 우리 변경을 되돌림.
    #      이때 실제 전이는 our_target(우리가 써넣은 값) → new_bid 다. before를 old_bid로
    #      쓰면 700→700이라는 무의미한 행이 되므로 our_target을 before로 쓴다.
    reverted = old_bid == new_bid
    before_bid = our_target if reverted else old_bid
    rationale = (
        "entity_sync 감지: 외부(MOP/사람)가 우리 입찰 변경을 되돌림"
        if reverted else "entity_sync 감지: 외부(MOP/사람) 입찰가 변경"
    )

    db.add(NaverChangeLog(
        entity_type=entity.entity_type,
        entity_id=entity.entity_id,
        campaign_id=entity.campaign_id,
        action="external_bid_change",
        proposal_id=None,
        dry_run=False,
        changed_at=now,
        before_value=json.dumps({"bidAmt": before_bid}),
        after_value=json.dumps({"bidAmt": new_bid}),
        rationale=rationale,
    ))
    log.info("external_bid_change detected%s: %s %s %s→%s",
             " (revert)" if reverted else "",
             entity.entity_type, entity.entity_id, before_bid, new_bid)


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
    # ★루프 전 1회 적재(D-NAO-47, codex[P2]) — 루프 안에서 행마다 조회하면 91,005번 쿼리한다.
    our_bid_writes = _load_our_bid_writes(db)

    for r in rows:
        key = (r["entity_type"], r["entity_id"])
        seen.add(key)
        e = existing.get(key)
        if e is None:
            db.add(NaverEntity(
                entity_type=r["entity_type"], entity_id=r["entity_id"], parent_id=r["parent_id"],
                campaign_id=r["campaign_id"], campaign_type=r["campaign_type"], name=r["name"],
                status=r["status"], bid_amt=r.get("bid_amt"), synced_at=now,
            ))
        else:
            if e.status != r["status"] and e.status != "deleted":
                _log_external_status_change(db, e, r["status"], now)
            # ★ e.bid_amt 대입 *전*에 호출 — 함수가 entity.bid_amt를 옛값으로 읽는다(D-NAO-47).
            _log_external_bid_change(db, e, r.get("bid_amt"), now, our_bid_writes)
            e.parent_id = r["parent_id"]
            e.campaign_id = r["campaign_id"]
            e.campaign_type = r["campaign_type"]
            e.name = r["name"]
            e.status = r["status"]
            e.bid_amt = r.get("bid_amt")
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
