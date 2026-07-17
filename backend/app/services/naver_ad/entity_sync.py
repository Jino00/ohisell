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

    # before를 무엇으로 볼 것인가 — 규칙 하나로 정리(codex[P2] R2, 2026-07-17):
    # **직전 관측 이후 우리가 썼다면(our_target 존재), 외부 행위자가 본 출발값은 old_bid가
    # 아니라 our_target이다.** 우리 쓰기가 이미 값을 our_target으로 옮겨놨기 때문이다.
    # change_log에는 우리 old_bid→our_target 행이 이미 있으므로, 외부 행은 our_target→new_bid로
    # 이어져야 이력이 끊기지 않는다.
    #   (a) our_target 없음            → 평범한 외부 변경: old_bid → new_bid
    #   (b) our_target 있고 new==old   → 외부가 우리 변경을 **되돌림**: our_target → new_bid
    #   (c) our_target 있고 new!=old   → 외부가 우리 변경을 **덮어씀**(제3의 값): our_target → new_bid
    # 초판은 (c)를 놓쳐 old_bid(700)→new_bid(800)로 적었다. 우리가 900으로 써둔 뒤였으므로
    # 실제 외부 전이는 900→800이고, 700→800은 900이라는 중간 상태를 지워 이력을 오귀속한다.
    overwrote_ours = our_target is not None
    before_bid = our_target if overwrote_ours else old_bid
    if not overwrote_ours:
        rationale = "entity_sync 감지: 외부(MOP/사람) 입찰가 변경"
    elif new_bid == old_bid:
        rationale = "entity_sync 감지: 외부(MOP/사람)가 우리 입찰 변경을 되돌림"
    else:
        rationale = "entity_sync 감지: 외부(MOP/사람)가 우리 입찰 변경을 덮어씀"

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
             " (overwrote ours)" if overwrote_ours else "",
             entity.entity_type, entity.entity_id, before_bid, new_bid)


def _log_external_qi_change(db: Session, entity: NaverEntity, new_qi, now) -> None:
    """D-NAO-46②: 품질지수(qi_grade) 변화를 관찰 기록(외부 변경 — 우리는 qi에 쓰기 없음).

    기존 값과 새 값이 둘 다 non-None이고 다를 때만 기록한다(처음 관측되거나 API가 일시
    부재로 None을 반환한 경우는 잡음이라 기록 대상 아님). dry_run=True(관찰만, 실쓰기 아님).

    ⚠️ 병합 메모(2026-07-17): D-NAO-46②(qi)와 D-NAO-47(입찰가)이 **서로 모르는 채 같은
    함수에 같은 패턴의 diff 밸브**를 만들었다(원칙20 병행 세션). 둘 다 유지한다 — 관심사가
    다르고(품질지수 관찰 vs 입찰 인과), 둘 다 무변동 미로깅 가드가 있어 91,005행 크론에서
    안전하다. 호출 순서도 둘 다 대입 **전**이라 옛값을 읽는 계약이 동일하다.
    """
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


_MASS_EVENT_THRESHOLD = 200


def _log_keyword_inventory_changes(
    db: Session,
    added: list[dict],
    removed: list[dict],
    now,
    *,
    bootstrap: bool,
) -> None:
    """D-NAO-50: 키워드가 새로 나타나거나(등록·재등장) 사라지는(삭제·수집 소실) 인벤토리 변화를
    change_log에 기록한다 — 상태·입찰·품질 밸브에 이은 네 번째 diff 밸브.

    ★이 함수가 없던 동안 키워드 add/remove 이력이 **어디에도** 남지 않았다. MOP(또는 Jino)가
    네이버 콘솔에서 추가한 키워드는 그냥 새 naver_entity 행으로 조용히 나타났고, 삭제된 키워드는
    status='deleted'만 찍힐 뿐 로그가 없었다(스펙 D-47-d "입찰가·키워드 diff 로깅" 중 입찰가만
    구현됨). Jino "키워드들의 history는 어디서 볼 수 있어?"의 답이 add/remove에 대해선 '없음'이었다.

    설계상 두 action:
      - external_keyword_added   : (a) 신규 등록  (b) 재등장(기존 deleted → 수집 on)
      - external_keyword_removed : (a) API가 DELETED 반환  (b) 수집에서 완전 소실(stale 루프)

    ★행 단위로 루프 안에서 로깅하지 않는다 — sync 루프/stale 루프에서 후보만 모아(added/removed
    리스트) 여기서 **한 번에** 기록한다. 아래 두 가드가 방향(등록/소실)마다 독립적으로 걸린다.

    ⚠️ 가드 1 — 부트스트랩(added 전용): 호출부가 `bootstrap`(기존 키워드 0행)을 넘긴다. 새 DB의
      첫 sync는 91,005개 키워드 전부를 '외부 등록'으로 볼 것이므로 added 로깅을 통째로 건너뛴다.
      (removed는 부트스트랩에서 발화 불가 — 기존 행이 없으니 사라질 것도 없다.)

    ⚠️ 가드 2 — 대량 이벤트(_MASS_EVENT_THRESHOLD=200): 네이버 API가 부분 장애로 어떤 광고그룹에
      빈 응답을 주면, 그 그룹의 키워드 수천 개가 한꺼번에 stale 처리되어 소실로 잡힌다. 이걸 개별
      로깅하면 change_log가 정확히 입찰가 밸브가 막으려던 쓰기 폭증처럼 폭파한다(스펙 §쓰기증폭).
      방향별 후보가 200을 넘으면 개별행 대신 요약 1행(entity_id="__bulk__")만 남기고 개별 로깅을
      생략한다 — "대량 작업 또는 API 부분 장애 의심"이라는 신호는 요약행이 그대로 전달한다.

    ⚠️ 가드 3 — 귀속(our-write) 체크 없음(의도적): `_log_external_bid_change`와 달리 여기엔 "우리가
      방금 한 것이면 억제"가 없다. 우리 프로그램에는 **정규 키워드를 등록/삭제하는 쓰기 경로가
      아예 없기 때문**이다. `add_negative_keyword`는 제외키워드(restricted-keywords API)를 쓰는데,
      이는 /ncc/keywords 인벤토리에 절대 나타나지 않는 **다른 네이버 리소스**라 여기 diff에 걸리지
      않는다. ★P4(키워드 발굴→등록)가 실제 등록 쓰기 경로를 열면 이 밸브도 `_load_our_bid_writes`
      같은 귀속 로직이 필요해진다 — 없으면 우리 자신의 등록이 '외부'로 오분류된다.

    호출 계약: stale 루프 이후, `db.commit()` 이전에 1회 호출한다.
    """
    if added and not bootstrap:
        _emit_inventory_side(db, added, now, direction="added")
    if removed:
        _emit_inventory_side(db, removed, now, direction="removed")


def _emit_inventory_side(db: Session, items: list[dict], now, *, direction: str) -> None:
    """added/removed 한 방향을 기록. 200 초과면 __bulk__ 요약 1행, 아니면 개별행.

    payload 배치가 방향에 따라 다르다: added는 after_value(before=None, 등장), removed는
    before_value(after=None, 소실). 대량 요약은 {"bulk":True,"count":N,"sample":[이름 20개]}.
    """
    is_added = direction == "added"
    action = "external_keyword_added" if is_added else "external_keyword_removed"
    n = len(items)

    if n > _MASS_EVENT_THRESHOLD:
        verb = "등록" if is_added else "소실"
        summary = json.dumps(
            {"bulk": True, "count": n, "sample": [it["name"] for it in items[:20]]},
            ensure_ascii=False,
        )
        db.add(NaverChangeLog(
            entity_type="keyword", entity_id="__bulk__", campaign_id="",
            action=action, proposal_id=None, dry_run=False, changed_at=now,
            before_value=None if is_added else summary,
            after_value=summary if is_added else None,
            rationale=(
                f"entity_sync 감지: 키워드 대량 {verb} {n}건 — "
                "API 부분 장애 또는 대량 작업 의심, 개별 로깅 생략"
            ),
        ))
        log.warning("keyword_inventory %s bulk: %d건 요약 1행 기록(개별 생략)", direction, n)
        return

    rationale = (
        "entity_sync 감지: 외부(MOP/사람) 키워드 등록"
        if is_added else
        "entity_sync 감지: 외부(MOP/사람) 키워드 삭제·소실"
    )
    for it in items:
        payload = json.dumps({"keyword": it["name"], "bidAmt": it["bid"]}, ensure_ascii=False)
        db.add(NaverChangeLog(
            entity_type="keyword", entity_id=it["entity_id"], campaign_id=it["campaign_id"],
            action=action, proposal_id=None, dry_run=False, changed_at=now,
            before_value=None if is_added else payload,
            after_value=payload if is_added else None,
            rationale=rationale,
        ))
    log.info("keyword_inventory %s: %d건 개별 기록", direction, n)


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

    # D-NAO-50 키워드 인벤토리 밸브: 루프에서 후보만 모아 stale 루프 뒤 일괄 로깅한다.
    # bootstrap = 기존 키워드 행이 0 → 새 DB 첫 sync가 91,005 add를 쏟아내는 걸 막는 added 가드.
    bootstrap = not any(k[0] == "keyword" for k in existing)
    added_keywords: list[dict] = []
    removed_keywords: list[dict] = []

    for r in rows:
        key = (r["entity_type"], r["entity_id"])
        seen.add(key)
        e = existing.get(key)
        if e is None:
            # (a) 신규 등록: 키워드 신규 행 + 수집 status가 deleted가 아닐 때. 부트스트랩이면 스킵.
            if r["entity_type"] == "keyword" and not bootstrap and r["status"] != "deleted":
                added_keywords.append({
                    "name": r["name"], "entity_id": r["entity_id"],
                    "campaign_id": r["campaign_id"], "bid": _norm_bid(r.get("bid_amt")),
                })
            db.add(NaverEntity(
                entity_type=r["entity_type"], entity_id=r["entity_id"], parent_id=r["parent_id"],
                campaign_id=r["campaign_id"], campaign_type=r["campaign_type"], name=r["name"],
                status=r["status"], bid_amt=r.get("bid_amt"), qi_grade=r.get("qi_grade"), synced_at=now,
            ))
        else:
            if r["entity_type"] == "keyword":
                if e.status == "deleted" and r["status"] != "deleted":
                    # (b) 재등장: 기존 deleted 키워드가 다시 켜짐. (status 밸브는 e.status=deleted라 미발화)
                    added_keywords.append({
                        "name": r["name"], "entity_id": r["entity_id"],
                        "campaign_id": r["campaign_id"], "bid": _norm_bid(r.get("bid_amt")),
                    })
                elif e.status != "deleted" and r["status"] == "deleted":
                    # (a) 삭제: API가 DELETED 반환. (on→deleted는 lock 변화가 아니라 status 밸브 미발화)
                    removed_keywords.append({
                        "name": e.name, "entity_id": e.entity_id,
                        "campaign_id": e.campaign_id, "bid": _norm_bid(e.bid_amt),
                    })
            if e.status != r["status"] and e.status != "deleted":
                _log_external_status_change(db, e, r["status"], now)
            # ★두 밸브 모두 **대입 전**에 호출한다 — entity의 옛값을 읽어야 diff가 나온다.
            #   (D-NAO-47 입찰가 / D-NAO-46② 품질지수. 병합 2026-07-17: 두 세션이 서로 모르는
            #   채 같은 자리에 같은 패턴을 만들었고, 관심사가 달라 둘 다 유지한다.)
            _log_external_bid_change(db, e, r.get("bid_amt"), now, our_bid_writes)
            _log_external_qi_change(db, e, r.get("qi_grade"), now)
            e.parent_id = r["parent_id"]
            e.campaign_id = r["campaign_id"]
            e.campaign_type = r["campaign_type"]
            e.name = r["name"]
            e.status = r["status"]
            e.bid_amt = r.get("bid_amt")
            if r.get("qi_grade") is not None:
                # codex[P2] D-NAO-46②: qi는 느리게 변하는 관측치 — API가 일시적으로 nccQi를
                # 누락(또는 레거시 rows 주입)해도 last-known 등급을 None으로 지우지 않는다.
                e.qi_grade = r["qi_grade"]
            e.synced_at = now

    stale = 0
    for key, e in existing.items():
        if key not in seen and e.status != "deleted":
            # (b) 소실: 최신 수집에 없는 기존 live 키워드 → removed 후보(entity_type=keyword만).
            if e.entity_type == "keyword":
                removed_keywords.append({
                    "name": e.name, "entity_id": e.entity_id,
                    "campaign_id": e.campaign_id, "bid": _norm_bid(e.bid_amt),
                })
            e.status = "deleted"
            stale += 1

    # D-NAO-50: commit 전에 인벤토리 변화 일괄 로깅(부트스트랩·대량 가드는 함수 내부에서 적용).
    _log_keyword_inventory_changes(db, added_keywords, removed_keywords, now, bootstrap=bootstrap)

    db.commit()

    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["entity_type"]] = by_type.get(r["entity_type"], 0) + 1
    log.info("naver_entity sync: %s (stale→deleted=%d, kw_added=%d kw_removed=%d)",
             by_type, stale, len(added_keywords), len(removed_keywords))
    return {
        "rows": len(rows), "stale_marked_deleted": stale,
        "keyword_added": len(added_keywords), "keyword_removed": len(removed_keywords),
        **by_type,
    }
