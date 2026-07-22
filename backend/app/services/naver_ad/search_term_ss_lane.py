# search_term_ss_lane.py — 검색어 제외·승격 레인 (SS3·SS4 생성층 + PX 파워링크 자동 제외·재심사,
#   docs/PLAN_naver-ad-powerlink-autoexclude.md §3). 역할(Harness): SS2 판단(search_term_judge)을
#   소비해 (SS3-A/PX2) **파워링크 §1 통과 후보를 자동 발사**(status='approved' + approval_source=
#   ss_exclude 제안 → naver_execution_harness.execute() 즉시 실행)하고, (PX3) in-out 재심사 루프
#   (개방·probation·재제외/복귀)를 돌리며, **쇼핑 후보는 브리핑 diary만** 남기고(제안 생성 없음 —
#   §실측-0 쇼핑 API 제외 불가), (SS4) **전환 검색어 승격 후보**는 pending 제안(영구 Confirm·실행
#   손 없음)으로 생성한다. 일 레인(auto_operator run_daily_lane)과 같은 08:50 흐름에 편입(scheduler).
#
#   ★파워링크 실쓰기 개방(PX): Jino 지시(D-NAO-78) "성과 기반 자동운영 — 수백 검색어 Confirm 불가"
#   → SS3-A pending Confirm 경로 제거·비용(성과) 기반 자동 제외로 전환. 전환 귀속 불가는 in-out
#   재심사 루프가 자가 교정(잘못 자른 것 복귀). 실쓰기는 auto_operate=1(ours)·파워링크(WEB_SITE)만
#   (§0 3 대행사 무실쓰기) — 일일캡·킬스위치·SHOPPING 거부·전환 재검증은 harness가 최종 강제한다.
#   승격(SS4)은 실행 손 자체가 없다(L3 스코프) — 정식 키워드 등록은 Jino 콘솔 밖 수동만.
#   proposal_type=search_term_promote는 naver_execution_harness의 _ACTION_BY_PROPOSAL_TYPE에 절대
#   등록하지 않는다(등록하면 미구현 executor를 부르게 되어 위험 — 매핑 부재 자체가 fail-closed).
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import NaverChangeLog, NaverEntity, NaverProposal, NaverSearchTermExclusion
from app.services.naver_ad import diary, naver_sa_writer, search_term_judge
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 브리핑 diary 요약에 나열할 쇼핑 후보 상위 건수(제안 자체는 전건 생성, 요약만 절삭).
_BRIEF_TOP_N = 10

# ── PX2·PX3: 파워링크 자동 제외 봉투(PLAN_naver-ad-powerlink-autoexclude.md §3) ──
# 일일 복귀(재심사 개방) 캡 — 제외의 일일캡(harness._SS_DAILY_EXCLUDE_CAP=10)과 대칭. 복귀도
# 되돌림 비용(재노출→다시 손실 가능)이 있는 비대칭 액션이라 상한을 둔다(§3 봉투).
_SS_DAILY_RETURN_CAP = 10
# 그룹당 제외 슬롯 — 롤링 큐레이션(§3). 상태 테이블 excluded 행 수로 근사(우리가 유지 중인 제외
# 수 — 라이브 restricted-keywords GET을 핫 레인마다 N회 유발하지 않기 위한 결정, 대행사/수동 등록
# 키워드와 무관하게 우리 자동 제외 슬롯만 제한). 초과 그룹은 이번 라운드 스킵.
_PL_GROUP_SLOT_CAP = 60

# 재심사 개방(복귀) 실쓰기 change_log action·마커(PX3) — 제외(exclude_search_term)와 별도 액션으로
# 분리해 일일 복귀 캡·감사를 독립 카운트한다(제외 캡과 상호 오염 방지).
_RESTORE_ACTION = "restore_search_term"
_RETURN_MARKER = "[검색어제외 복귀]"
# probation 관찰창(§2) — 개방 후 재노출 관찰 기간(일).
_PROBATION_DAYS = 14

# SS4 승격 제안 1회 생성 상한(라이브 첫 실행 실측: 2026-07-22 08:50 크론에서 354건 pending이
# 한꺼번에 쏟아져 Jino 콘솔이 범람 — 운영 결함). conv_direct_cnt 내림차순(→ conv_purchase_amt
# 내림차순 tie-break)으로 정렬해 근거가 가장 강한 후보부터 상위 20건만 신규 생성한다. dedup으로
# 걸러진 기존 pending 건은 이 상한을 소모하지 않는다(신규 생성 건수만 ≤20으로 제한하는 게 목적 —
# 이미 콘솔에 떠 있는 제안 수를 세는 게 아니라 "이번 실행이 콘솔에 새로 얹는 양"을 통제한다).
# 나머지 후보는 다음 실행(익일 08:50)에서 갱신 데이터로 재평가되며 그 사이엔 promote_over_cap
# 카운트로만 유실 없이 관측된다(diary 브리핑 없음 — 승격은 정보성 후보라 브리핑 노이즈보다 로그
# 카운트로 충분, 필요 시 콘솔 promote_candidates 총량으로 잔여를 알 수 있음).
_SS_PROMOTE_CAP = 20

# 제안화 가능한 검색어 최대 길이 = NaverProposal.target_id 컬럼 길이(String(50)) 단일 출처
# (codex 2R[P1]). target_id에 검색어 전문을 저장하는데, 초과분을 넣으면 DB(Postgres)에서
# 잘리거나 오류가 나 "잘린 텍스트로 실쓰기"가 발생한다. NaverProposal에 전문 페이로드 컬럼이
# 없으므로(A안=skip), 초과 검색어는 후보에서 fail-closed로 제외한다 — 잘린 검색어를 실제
# 제외키워드로 등록하면 엉뚱한 검색어가 잘려 등록되는 비대칭 사고가 되기 때문. 현실적으로
# 손실 검색어 대부분은 짧아 skip 손실은 미미하다(harness executor에도 동일 방어 게이트 존재).
_TARGET_ID_MAXLEN = NaverProposal.target_id.property.columns[0].type.length


def _has_open_or_executed(
    db: Session, adgroup_id: str, search_term: str, *, proposal_type: str,
) -> bool:
    """같은 (adgroup, search_term, proposal_type)의 제안이 이미 살아있거나 실행됐는지.

    재생성 금지 대상(PLAN §3 "이미 pending/실행된 동일 (adgroup, search_term) 제안 존재 시
    재생성 금지"): status가 pending/approved/executing(승인 대기·집행 중)이거나 executed_
    change_log_id가 채워진(실집행 완료) 제안. rejected/expired/failed는 재생성 허용(익일 갱신
    데이터로 다시 제안될 수 있음 — in-out 롤링 큐레이션, §0 2). proposal_type을 명시 인자로
    받아 SS3(제외)·SS4(승격)이 서로의 dedup을 오염시키지 않는다(같은 검색어가 동시에 제외
    후보이면서 승격 후보일 순 없지만 — 표본 게이트가 상호배타적 — 방어적으로 분리)."""
    return db.query(NaverProposal.id).filter(
        NaverProposal.proposal_type == proposal_type,
        NaverProposal.adgroup_id == adgroup_id,
        NaverProposal.target_id == search_term,
        or_(
            NaverProposal.status.in_(("pending", "approved", "executing")),
            NaverProposal.executed_change_log_id.isnot(None),
        ),
    ).first() is not None


def _active_exclusion_exists(db: Session, adgroup_id: str, search_term: str) -> bool:
    """이미 살아있는 제외 상태 행(excluded/probation)이 있는지 — 중복 제외 방지(PX2). restored는
    허용(성과 자가 교정 후 재충족 시 재제외 = 정상 in-out 롤링). probation 중 중복 제외 금지(GATE ⑥)."""
    return db.query(NaverSearchTermExclusion.id).filter(
        NaverSearchTermExclusion.adgroup_id == adgroup_id,
        NaverSearchTermExclusion.search_term == search_term,
        NaverSearchTermExclusion.status.in_(("excluded", "probation")),
    ).first() is not None


def _excluded_slot_count(db: Session, adgroup_id: str) -> int:
    """그룹당 우리 자동 제외 슬롯 카운트(§3 근사) — 상태='excluded' 행 수. probation/restored는
    제외키워드가 네이버에서 이미 개방됐거나 개방 예정이라 슬롯을 점유하지 않는다."""
    return db.query(NaverSearchTermExclusion).filter(
        NaverSearchTermExclusion.adgroup_id == adgroup_id,
        NaverSearchTermExclusion.status == "excluded",
    ).count()


def _apply_exclusion_fields(
    row: NaverSearchTermExclusion, cand: dict, restrict_kwd_id: str | None, now: datetime, cycle: int,
) -> None:
    """제외 상태 행에 excluded 진입 필드를 세팅(§2 상태기계) — cycle·타임스탬프·백오프 next_review.
    신규 insert 경로와 IntegrityError 수렴 경로가 동일 규칙을 공유하도록 분리한다."""
    row.cycle = cycle
    row.excluded_at = now
    row.last_transition_at = now
    row.restrict_kwd_id = restrict_kwd_id
    row.status = "excluded"
    row.probation_until = None
    row.next_review_at = now.date() + timedelta(days=min(30 * cycle, 90))
    row.cost_at_exclusion = int(cand.get("cost", 0))


def _upsert_exclusion(db: Session, cand: dict, restrict_kwd_id: str | None, now: datetime) -> None:
    """제외 실쓰기 성공 후 상태 행 upsert(§2 상태기계). 같은 (adgroup, term) 행이 있으면 cycle
    승계(+1)·행 재사용(restored/probation 재제외 경로), 없으면 cycle=1 신규. next_review_at =
    today + min(30×cycle, 90)일(백오프 30→60→90 cap). status='excluded'로 (재)진입.

    ★IntegrityError 경합 수렴(P2-3): harness 실쓰기(네이버 등록)는 이미 끝난 뒤라, 여기 commit이
    동시 insert 경합으로 uq_naver_search_term_exclusion 위반을 던지면 레인 전체가 중단되고
    orphan(네이버엔 제외됐는데 상태 행 없음)이 남는다. commit을 try/except로 감싸 rollback→기존
    행 재조회→cycle 승계 규칙(+1)로 갱신·재commit해 수렴시킨다(재조회도 없으면 진짜 이상이므로
    re-raise). 이때 이미 존재하는 행은 다른 러너가 방금 커밋한 것이므로 else 분기와 동일하게 다룬다."""
    row = db.query(NaverSearchTermExclusion).filter(
        NaverSearchTermExclusion.adgroup_id == cand["adgroup_id"],
        NaverSearchTermExclusion.search_term == cand["search_term"],
    ).first()
    if row is None:
        cycle = 1
        row = NaverSearchTermExclusion(
            campaign_id=cand["campaign_id"], adgroup_id=cand["adgroup_id"],
            search_term=cand["search_term"],
        )
        db.add(row)
    else:
        cycle = row.cycle + 1
    _apply_exclusion_fields(row, cand, restrict_kwd_id, now, cycle)
    try:
        db.commit()
    except IntegrityError:
        # 동시 insert 경합(uq_naver_search_term_exclusion) — 다른 러너가 방금 같은 (adgroup, term)
        # 행을 먼저 커밋. rollback 후 그 행을 재조회해 cycle 승계(+1)로 갱신·수렴(orphan 방지).
        db.rollback()
        existing = db.query(NaverSearchTermExclusion).filter(
            NaverSearchTermExclusion.adgroup_id == cand["adgroup_id"],
            NaverSearchTermExclusion.search_term == cand["search_term"],
        ).first()
        if existing is None:
            raise  # 경합이 아닌 진짜 무결성 위반 — 삼키지 않는다
        _apply_exclusion_fields(existing, cand, restrict_kwd_id, now, existing.cycle + 1)
        db.commit()


def _adgroup_belongs_to_campaign(db: Session, adgroup_id: str, campaign_id: str) -> bool:
    """실쓰기 직전 adgroup 소속 실검증(codex 1R[P1-3·P2]) — naver_entity에서 이 adgroup 행의
    parent_id == campaign_id인지 확인. 상태 행(NaverSearchTermExclusion)·후보의 campaign_id는
    판정 시점에 굳은 값이라, 그 사이 그룹이 다른 캠페인으로 옮겨졌거나 상태 행이 오염되면 대행사
    그룹에 제외/개방 실쓰기가 갈 수 있다(§0 3 금지선 위반). 인벤토리(entity_type='adgroup')로
    소속을 재확인한다. **행 부재·조회 실패는 fail-closed(False)** — 소속을 증명 못 하면 쓰지 않는다.

    codex 2R[P1-b]: 레인 Session 경유 조회는 결국 같은 Session 트랜잭션 안에서 실행된다 —
    SQLite(WAL)에서 리더는 트랜잭션 시작 시점 스냅샷을 보므로, 장수 레인이 조기 쿼리로 읽기
    트랜잭션을 연 뒤 entity_sync가 이 그룹의 parent_id/삭제를 커밋해도 이 세션엔 안 보인다
    (스테일 스냅샷으로 대행사 그룹 소속을 오판할 수 있다). auto_operator._auto_operate_now와
    같은 관례로 **엔진 레벨 독립 커넥션**(세션 트랜잭션과 무관한 새 트랜잭션)으로 조회한다 —
    타 프로세스 커밋이 항상 보이고, 세션 상태를 오염시키지 않는다(commit/rollback 사이드이펙트 없음)."""
    try:
        with db.get_bind().connect() as conn:
            row = conn.execute(
                select(NaverEntity.parent_id).where(
                    NaverEntity.entity_type == "adgroup",
                    NaverEntity.entity_id == adgroup_id,
                )
            ).first()
    except Exception as e:  # noqa: BLE001 — 조회 실패도 fail-closed(소속 미증명 = 쓰지 않음)
        log.warning("search_term_ss_lane: adgroup 소속 조회 실패 adgroup=%s: %s", adgroup_id, e)
        return False
    return row is not None and row[0] == campaign_id


def _autofire_exclude(db: Session, cand: dict, now: datetime) -> NaverChangeLog | None:
    """파워링크 제외 후보 1건 자동 발사(exploration BX2 관례 복제): status='approved' +
    approval_source=APPROVAL_SOURCE_SS_EXCLUDE 제안 생성 → naver_execution_harness.execute()로
    같은 흐름 즉시 실행. 성공 시 WriteResult(after_value.created_ids)에서 restrict_kwd_id 회수해
    상태 행 upsert(§2). 실패(킬스위치/일일캡/SHOPPING/가드)는 harness가 change_log·상태를 확정
    하므로 여기선 None 반환(브리핑 카운트만). 지연 import(순환 회피 — harness가 auto_operator를,
    auto_operator가 이 모듈을 import하지 않지만 exploration 관례 유지)."""
    from app.services.naver_ad import exploration
    from app.services.naver_ad import naver_execution_harness as harness

    # C3(codex 1R[P2]): 제안 생성 전 adgroup 소속 실검증 — 스코프 과신 방지. 상태 행/후보의
    # campaign_id를 그대로 믿지 않고 인벤토리로 (adgroup→campaign) 소속을 재확인한다. 미검증이면
    # fail-closed skip(대행사 그룹 실쓰기 절대 0, §0 3). 신규·재제외 양 경로가 이 게이트를 공유한다.
    if not _adgroup_belongs_to_campaign(db, cand["adgroup_id"], cand["campaign_id"]):
        log.warning(
            "search_term_ss_lane: 자동 제외 skip(adgroup 소속 미검증) adgroup=%s campaign=%s term=%r "
            "— fail-closed(대행사 그룹 실쓰기 차단)",
            cand["adgroup_id"], cand["campaign_id"], cand["search_term"],
        )
        return None

    proposal = NaverProposal(
        proposal_type=search_term_judge.SEARCH_TERM_EXCLUDE_TYPE,
        target_type="search_term", target_id=cand["search_term"],
        campaign_id=cand["campaign_id"], adgroup_id=cand["adgroup_id"],
        rationale=cand["reason"],
        expected_effect=(
            f"파워링크 자동 제외(전환0·cost={cand['cost']}원 낭비비용 회수). "
            "in-out 재심사 루프가 주기적으로 개방·재판정(§2)."
        ),
        status="approved",  # 자동 발사 — approval_source가 킬스위치·감사 경로를 그대로 탄다
        approval_source=search_term_judge.APPROVAL_SOURCE_SS_EXCLUDE,
    )
    db.add(proposal)
    db.commit()

    try:
        log_entry = harness.execute(db, proposal.id, dry_run=False, now=now)
    except Exception as e:  # noqa: BLE001 — harness가 change_log/상태 확정(failed/killswitch 등)
        log.info("search_term_ss_lane: 파워링크 자동 제외 미실행 term=%r: %s", cand["search_term"], e)
        return None

    # restrict_kwd_id 회수 — 실행자가 남긴 change_log.after_value = {"after":[...],"created_ids":[...]}.
    # created_ids[0] = add_restricted_keywords WriteResult의 nccAdgroupRestrictKwdId(개방에 필수).
    restrict_kwd_id: str | None = None
    try:
        payload = json.loads(log_entry.after_value) if log_entry.after_value else {}
        created = payload.get("created_ids") or []
        restrict_kwd_id = created[0] if created else None
    except (ValueError, TypeError) as e:
        log.warning("search_term_ss_lane: restrict_kwd_id 회수 실패(after_value 파싱) term=%r: %s",
                    cand["search_term"], e)

    _upsert_exclusion(db, cand, restrict_kwd_id, now)
    return log_entry


def _remaining_exclude_cap(db: Session, now: datetime) -> int:
    """오늘 남은 신규 제외 슬롯 = harness 일일 캡 − 오늘 성공 제외 수. harness가 최종 하드 강제
    하지만(TOCTOU 3중 방어), 소진 후 무의미한 approved 제안·failed 노이즈를 줄이는 라운드 사전
    제한(§3 봉투). harness의 단일 진실(_SS_DAILY_EXCLUDE_CAP·_count_search_term_excludes_today)
    을 재사용해 캡 값이 갈라지지 않게 한다(지연 import — 순환 회피)."""
    from app.services.naver_ad import naver_execution_harness as harness

    return max(0, harness._SS_DAILY_EXCLUDE_CAP - harness._count_search_term_excludes_today(db, now))


def _count_returns_today(db: Session, now: datetime) -> int:
    """오늘(KST) 성공한 재심사 개방(복귀) 수 — 일일 복귀 캡(_SS_DAILY_RETURN_CAP) 카운트. 실제
    쓰기 확정 행만(dry_run=False ∧ after_value 존재, restore_search_term). changed_at은 executor
    가 now(KST naive)로 심으므로 KST 자정 경계로 비교(제외 캡 카운트와 동일 규율)."""
    today_start = datetime.combine(now.date(), datetime.min.time())
    return db.query(NaverChangeLog).filter(
        NaverChangeLog.action == _RESTORE_ACTION,
        NaverChangeLog.dry_run.is_(False),
        NaverChangeLog.after_value.isnot(None),
        NaverChangeLog.changed_at >= today_start,
    ).count()


def _open_exclusion(db: Session, row: NaverSearchTermExclusion, now: datetime) -> bool:
    """재심사 개방 1건(§3) — delete_restricted_keywords로 제외키워드를 삭제하고 change_log 전건
    기록([검색어제외 복귀]). 성공 시 True. restrict_kwd_id 부재(회수 실패 잔존)면 개방 불가(사람
    조사 대상, 무한 재시도 방지 위해 False 반환·상태 유지). delete 실패는 fail-closed로 정직히
    실패 기록 후 False(상태 유지 — 다음 레인 재시도)."""
    if not row.restrict_kwd_id:
        log.warning(
            "search_term_ss_lane: 재심사 개방 불가(restrict_kwd_id 부재) adgroup=%s term=%r "
            "— 사람 조사 대상(상태 유지)", row.adgroup_id, row.search_term,
        )
        return False
    # 복귀 캡 재카운트 백스톱(P2-1) — 제외 캡의 harness 관례(쓰기 직전 재카운트→초과 시 중단)를
    # 미러한다. _run_reexamination의 로컬 remaining_return은 소프트 카운트라, 동시 실행(크론+
    # catch-up 데몬 스레드 동시 발화) 시 각 러너가 remaining=cap을 보고 최대 2배 개방할 수 있다.
    # delete 호출 직전 재카운트가 캡 이상이면 fail-closed로 개방 중단(완전 원자 예약은 불요 —
    # 재카운트 백스톱이 최소 권고). ★재카운트는 change_log 커밋 기준(_count_returns_today가 dry_run
    # =False ∧ after_value 존재 행을 센다) — 러너별 트랜잭션이 커밋 후 상호 가시화되어야 반영된다.
    returns_today = _count_returns_today(db, now)
    if returns_today >= _SS_DAILY_RETURN_CAP:
        log.info(
            "search_term_ss_lane: 재심사 개방 중단(복귀 캡 재카운트 %d≥%d, 동시 실행 백스톱) "
            "adgroup=%s term=%r — fail-closed(상태 유지·다음 레인 재시도)",
            returns_today, _SS_DAILY_RETURN_CAP, row.adgroup_id, row.search_term,
        )
        return False
    # C1①(codex 1R[P1-1]): 킬스위치 delete 직전 재확인 — TOCTOU 창 제거. _run_reexamination이
    # 루프 진입 시 1회 스냅샷만 믿으면 여러 행 순회 도중 Jino가 OFF해도 남은 개방이 진행된다
    # ("즉시 정지" 계약 위반). 엔진 독립 커넥션 조회라 타 프로세스 커밋이 항상 보인다.
    from app.services.naver_ad import auto_operator
    if not auto_operator._auto_operate_now(db, row.campaign_id):
        log.info(
            "search_term_ss_lane: 재심사 개방 중단(킬스위치 delete 직전 OFF) adgroup=%s term=%r "
            "— fail-closed(상태 유지)", row.adgroup_id, row.search_term,
        )
        return False
    # C1②(codex 1R[P1-3]): adgroup 소속 실검증 — 상태 행 campaign_id 오염/그룹 이동 시 대행사
    # 그룹 delete 차단(§0 3). 조회 실패/행 부재 = fail-closed skip(상태 유지).
    if not _adgroup_belongs_to_campaign(db, row.adgroup_id, row.campaign_id):
        log.warning(
            "search_term_ss_lane: 재심사 개방 중단(adgroup 소속 미검증) adgroup=%s campaign=%s "
            "term=%r — fail-closed(상태 유지)", row.adgroup_id, row.campaign_id, row.search_term,
        )
        return False
    # C2(codex 1R[P1-2] 부분 수용): delete 직전 낙관적 클레임(excluded→probation, status 게이트).
    # 동시 실행(크론+catch-up 데몬)에서 두 러너가 같은 행을 각자 delete하면 두 번째가 404·복귀
    # 이중 카운트를 낳는다. `UPDATE ... WHERE id ∧ status='excluded'`를 커밋해 rowcount==1인
    # 러너만 진행(0=다른 러너 선점 → skip). 중간 상태를 새로 만들지 않고 최종 목표 상태(probation)로
    # 원자 전이해 최소 diff를 유지한다(probation_until 등 나머지 필드는 _run_reexamination이 개방
    # 성공 후 세팅 — 그 사이 probation_until=NULL이라 재판정 쿼리에 안 잡혀 안전).
    # ★F2(codex 2R[P1-a]): 클레임과 동시에 last_transition_at=now도 세팅한다 — 이 시각이 이후
    # probation/NULL 크래시 고아 치유(_reconcile_probation_orphans)의 시간 기준이 된다(클레임 이후에
    # 커밋된 복귀 change_log만 "delete 성공 증거"로 인정해 창①/창②를 결정적으로 구분).
    claimed = db.query(NaverSearchTermExclusion).filter(
        NaverSearchTermExclusion.id == row.id,
        NaverSearchTermExclusion.status == "excluded",
    ).update({"status": "probation", "last_transition_at": now}, synchronize_session=False)
    db.commit()
    if claimed != 1:
        log.info(
            "search_term_ss_lane: 재심사 개방 skip(클레임 경합 — 다른 러너 선점) adgroup=%s term=%r",
            row.adgroup_id, row.search_term,
        )
        return False
    try:
        result = naver_sa_writer.delete_restricted_keywords(row.adgroup_id, [row.restrict_kwd_id])
    except Exception as exc:  # noqa: BLE001 — 삭제 실패도 fail-open 금지, 사실대로 기록
        # C2 롤백: 이 러너가 delete 못 했으니 클레임(probation) 되돌림(excluded 복원) — 다음
        # 레인이 재시도한다. fail change_log는 after_value 없음 → _count_returns_today 미카운트.
        db.query(NaverSearchTermExclusion).filter(
            NaverSearchTermExclusion.id == row.id,
        ).update({"status": "excluded"}, synchronize_session=False)
        fail = NaverChangeLog(
            entity_type="search_term", entity_id=row.search_term, campaign_id=row.campaign_id,
            action=_RESTORE_ACTION,
            rationale=f"{_RETURN_MARKER} 개방 실패({type(exc).__name__}: {str(exc)[:200]}) — 상태 유지·재시도",
            dry_run=False, outcome="failed", changed_at=now, executed_at=now,
        )
        db.add(fail)
        db.commit()
        log.warning("search_term_ss_lane: 재심사 개방 실패 adgroup=%s id=%s: %s",
                    row.adgroup_id, row.restrict_kwd_id, exc)
        return False

    entry = NaverChangeLog(
        entity_type="search_term", entity_id=row.search_term, campaign_id=row.campaign_id,
        action=_RESTORE_ACTION,
        rationale=(
            f"{_RETURN_MARKER} 재심사 개방(cycle={row.cycle}) — 제외키워드 삭제, +{_PROBATION_DAYS}일 "
            "재노출 관찰(probation). 성과 자가 교정: 회복하면 복귀, 여전히 손실이면 재제외(§2)"
        ),
        dry_run=False,
        before_value=json.dumps(result.before, ensure_ascii=False),
        # ★F2 후속(codex 3R): adgroup_id를 after_value에 명시 병기 — change_log는 adgroup_id 컬럼이
        # 없어 entity_id(검색어)+campaign_id만으론 같은 캠페인 내 동일 검색어 두 그룹을 구분 못 한다.
        # _reconcile_probation_orphans 창② 증거 판정이 이 필드로 그룹까지 일치를 확인한다(교차 오인 차단).
        after_value=json.dumps({"after": result.after, "adgroup_id": row.adgroup_id}, ensure_ascii=False),
        changed_at=now, executed_at=now,
    )
    db.add(entry)
    db.commit()
    log.info("search_term_ss_lane: 재심사 개방 성공 adgroup=%s term=%r", row.adgroup_id, row.search_term)
    return True


def _reconcile_orphan_exclusions(db: Session, now: datetime) -> int:
    """C5(codex 1R[P1-4]) 크래시 고아 자가 치유. _autofire_exclude는 harness.execute()로 실쓰기·
    change_log 커밋을 끝낸 **직후** _upsert_exclusion 커밋 전에 크래시하면, 네이버엔 제외됐는데
    상태 행이 없는 고아가 남는다(다음 재심사가 이 제외를 영영 개방·재판정 못 함 = 영구 정지된 낭비
    절단). 최근 3일 exclude_search_term change_log(dry_run=False ∧ after_value 존재 = 실쓰기 확정,
    캡초과 롤백 fail 행은 after_value 없어 자동 제외) 중 대응 상태 행이 없는 (adgroup, term)을 찾아
    상태 행 재생성(cycle=1·next_review=+30일). adgroup_id는 change_log에 없어 proposal 조인으로
    회수(exclude change_log는 항상 proposal_id 보유). after_value에서 created_ids[0]=restrict_kwd_id
    회수. **파싱 실패는 skip+경고(fail-open — 치유 실패가 레인을 죽이면 안 됨).** 경합 재생성
    IntegrityError도 rollback 후 skip(다른 러너/치유가 먼저 만든 정상 케이스)."""
    healed = 0
    cutoff = datetime.combine((now - timedelta(days=3)).date(), datetime.min.time())
    rows = (
        db.query(NaverChangeLog, NaverProposal.adgroup_id)
        .join(NaverProposal, NaverChangeLog.proposal_id == NaverProposal.id)
        .filter(
            NaverChangeLog.action == "exclude_search_term",
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),
            NaverChangeLog.changed_at >= cutoff,
        )
        .all()
    )
    for cl, adgroup_id in rows:
        term = cl.entity_id  # exclude change_log의 entity_id = proposal.target_id = 검색어 전문
        if not adgroup_id or not term:
            continue
        if db.query(NaverSearchTermExclusion.id).filter(
            NaverSearchTermExclusion.adgroup_id == adgroup_id,
            NaverSearchTermExclusion.search_term == term,
        ).first() is not None:
            continue  # 상태 행 존재(어느 status든) — 고아 아님
        try:
            payload = json.loads(cl.after_value) if cl.after_value else {}
            created = payload.get("created_ids") or []
            restrict_kwd_id = created[0] if created else None
        except (ValueError, TypeError) as e:
            log.warning(
                "search_term_ss_lane: 고아 치유 파싱 실패(after_value) adgroup=%s term=%r: %s "
                "— skip(fail-open)", adgroup_id, term, e,
            )
            continue
        db.add(NaverSearchTermExclusion(
            campaign_id=cl.campaign_id, adgroup_id=adgroup_id, search_term=term,
            restrict_kwd_id=restrict_kwd_id, status="excluded", cycle=1,
            excluded_at=now, last_transition_at=now,
            next_review_at=now.date() + timedelta(days=30), probation_until=None,
            cost_at_exclusion=0,
        ))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()  # 경합으로 다른 러너/치유가 방금 생성 — 정상, skip
            continue
        healed += 1
        log.info(
            "search_term_ss_lane: 고아 제외 상태 행 재생성 adgroup=%s term=%r restrict_kwd_id=%s "
            "(change_log %s 실쓰기 확정·상태 행 부재)", adgroup_id, term, restrict_kwd_id, cl.id,
        )
    return healed


def _reconcile_probation_orphans(db: Session, now: datetime) -> int:
    """F2(codex 2R[P1-a]) 복귀 클레임 크래시 창 치유. _open_exclusion은 delete 직전 행을
    excluded→probation으로 클레임 커밋(C2)한 뒤 delete·복귀 change_log를 커밋하고, 그 후에야
    _run_reexamination이 probation_until을 세팅한다. 두 지점 사이에서 크래시하면 행이
    status='probation' ∧ probation_until IS NULL로 남는다:
      창①  클레임 커밋 후 delete 전 크래시 — 키워드는 네이버에 여전히 등록(제외 유지).
      창②  delete·change_log 커밋 후 probation_until 세팅 전 크래시 — delete는 성공한 상태.
    이 행은 개방 스캔(excluded만)·재판정 스캔(probation_until NOT NULL만)·기존 고아 reconcile
    (상태 행 존재 시 skip) 어디에도 안 잡혀 영구 방치된다(개방도 재판정도 못 하는 좀비).
    change_log 증거로 두 창을 결정적으로 구분해 상태기계에 복귀시킨다.

    대상: status='probation' ∧ probation_until IS NULL ∧ last_transition_at < now-30분
      (진행 중인 정상 클레임과 구분하는 안전 창 — _open_exclusion 한 번은 수 초 내 끝나므로
      30분 이상 이 상태로 머문 행만 고아로 본다).
    판정(결정적): 그 행의 복귀 change_log(action=restore_search_term ∧ dry_run=False ∧
      after_value 존재 = delete 성공 확정, 실패 fail 행은 after_value 없어 자동 제외)가
      last_transition_at 이후에 있고 **after_value JSON의 adgroup_id까지 이 행과 일치**하면
      → 창②(delete 성공) → probation_until = last_transition_at + _PROBATION_DAYS일로 소급
      세팅(재판정 루프 복귀). 없으면 → 창①(delete 미실행, 키워드 등록 상태) → status='excluded'
      복원(다음 재심사 주기가 정상 개방 재시도). ★adgroup_id 매칭(codex 3R): change_log는
      adgroup_id 컬럼이 없어 entity_id(검색어)+campaign_id만으론 같은 캠페인 내 동일 검색어 두
      그룹을 구분 못 한다 — A그룹 복귀 로그를 B그룹 행 증거로 오인하면 B가 잘못 probation_until
      소급→restored로 흘러 네이버 제외키워드가 살아있는 채 영구 고아가 된다. after_value의
      adgroup_id로 그룹까지 확인한다. **fail-open**(치유 실패가 레인을 죽이면 안 됨) — 행별
      try/except로 격리하고 건수만 반환한다."""
    healed = 0
    stale_before = now - timedelta(minutes=30)
    rows = (
        db.query(NaverSearchTermExclusion)
        .filter(
            NaverSearchTermExclusion.status == "probation",
            NaverSearchTermExclusion.probation_until.is_(None),
            NaverSearchTermExclusion.last_transition_at < stale_before,
        )
        .all()
    )
    for row in rows:
        try:
            # 복귀 change_log 증거(delete 성공 확정) — 이 행 클레임(last_transition_at) 이후에
            # 커밋된 것 중, after_value JSON의 adgroup_id가 이 행과 일치하는 것만 인정한다
            # (codex 3R: 같은 캠페인 내 동일 검색어 두 그룹 교차 오인 차단). adgroup_id 키가 없는
            # 구형/파싱불가 로그는 증거로 불인정(fail-safe: 창① 취급 → 키워드가 실제 살아있을
            # 가능성에 보수적으로 excluded 복원).
            cand_after = db.query(NaverChangeLog.after_value).filter(
                NaverChangeLog.action == _RESTORE_ACTION,
                NaverChangeLog.entity_id == row.search_term,
                NaverChangeLog.campaign_id == row.campaign_id,
                NaverChangeLog.dry_run.is_(False),
                NaverChangeLog.after_value.isnot(None),
                NaverChangeLog.changed_at >= row.last_transition_at,
            ).all()
            delete_committed = False
            for (av,) in cand_after:
                try:
                    if (json.loads(av) or {}).get("adgroup_id") == row.adgroup_id:
                        delete_committed = True
                        break
                except (ValueError, TypeError):
                    continue  # 파싱 불가 로그는 증거로 불인정(보수적 — 창① 취급)
            if delete_committed:
                # 창② — delete 성공. probation_until만 소급 세팅해 재판정 루프에 복귀시킨다.
                # (클레임 시각 기준 +14일 — 원래 개방 성공 직후 세팅됐어야 할 값을 복원.)
                row.probation_until = row.last_transition_at.date() + timedelta(days=_PROBATION_DAYS)
                resolution = "창②(delete 성공)→probation_until 소급 복원"
            else:
                # 창① — delete 미실행(키워드 네이버 등록 유지). excluded 복원 → 다음 재심사가
                # next_review 도래 시 정상 개방을 재시도한다.
                row.status = "excluded"
                resolution = "창①(delete 미실행)→excluded 복원"
            row.last_transition_at = now
            db.commit()
        except Exception as e:  # noqa: BLE001 — 치유 실패는 fail-open(레인 보호), 행별 격리 후 skip
            db.rollback()
            log.warning(
                "search_term_ss_lane: probation 고아 치유 실패 adgroup=%s term=%r: %s — skip(fail-open)",
                row.adgroup_id, row.search_term, e,
            )
            continue
        healed += 1
        log.info(
            "search_term_ss_lane: probation 클레임 크래시 창 치유 adgroup=%s term=%r — %s",
            row.adgroup_id, row.search_term, resolution,
        )
    return healed


def _run_reexamination(db: Session, powerlink: list[dict], now: datetime) -> dict:
    """PX3 in-out 재심사 루프(§2·§3) — 같은 08:50 레인 스텝. ①excluded ∧ next_review_at≤today →
    개방(delete)·probation 전이(일일 복귀 캡·킬스위치 존중). ②probation ∧ probation_until≤today →
    §1 재판정: 여전히 후보면 재제외(자동 발사·cycle 승계), 아니면 restored. 킬스위치 OFF면 그
    캠페인 상태기계 전체 동결(제외·복귀 양쪽 정지·미실행 정직 — 다음 레인 재시도).

    powerlink = 이번 라운드 judge 산출 §1 통과 후보(재판정 재사용 — 같은 now라 재조회 불필요)."""
    from app.services.naver_ad import auto_operator

    # C5: 재심사 스텝 시작부에 크래시 고아 자가 치유(상태 행 없는 확정 제외 재생성) — 개방·재판정
    # 전에 돌려 고아가 상태기계에 복귀하도록 한다(이후 next_review 도래 시 정상 개방 대상이 됨).
    healed = _reconcile_orphan_exclusions(db, now)
    # F2(codex 2R[P1-a]): 복귀 클레임 크래시 창(probation ∧ probation_until=NULL) 치유 — C5와 같은
    # 목적(좀비 상태 행 복귀)이나 대상이 다르다(C5=상태 행 부재, F2=probation/NULL 방치). 개방·재판정
    # 전에 돌려 창②는 재판정 루프로, 창①은 개방 루프로 각각 복귀시킨다.
    probation_healed = _reconcile_probation_orphans(db, now)

    today = now.date()
    opened = 0
    reexcluded = 0
    restored = 0

    # ① 개방(excluded → probation) — 일일 복귀 캡 잔여만큼, next_review 이른 순.
    remaining_return = max(0, _SS_DAILY_RETURN_CAP - _count_returns_today(db, now))
    due = (
        db.query(NaverSearchTermExclusion)
        .filter(
            NaverSearchTermExclusion.status == "excluded",
            NaverSearchTermExclusion.next_review_at.isnot(None),
            NaverSearchTermExclusion.next_review_at <= today,
        )
        .order_by(NaverSearchTermExclusion.next_review_at)
        .all()
    )
    for row in due:
        if remaining_return <= 0:
            break
        if not auto_operator._auto_operate_now(db, row.campaign_id):
            continue  # 킬스위치 OFF — 복귀 정지(미실행 정직)
        if _open_exclusion(db, row, now):
            row.status = "probation"
            row.probation_until = today + timedelta(days=_PROBATION_DAYS)
            row.last_transition_at = now
            db.commit()
            opened += 1
            remaining_return -= 1

    # ② probation 만료 재판정(probation → excluded 재제외 or restored).
    pl_map = {(c["adgroup_id"], c["search_term"]): c for c in powerlink}
    prob = (
        db.query(NaverSearchTermExclusion)
        .filter(
            NaverSearchTermExclusion.status == "probation",
            NaverSearchTermExclusion.probation_until.isnot(None),
            NaverSearchTermExclusion.probation_until <= today,
        )
        .all()
    )
    for row in prob:
        if not auto_operator._auto_operate_now(db, row.campaign_id):
            continue  # 킬스위치 OFF — 재판정 동결(제외·복귀 양쪽 정지)
        cand = pl_map.get((row.adgroup_id, row.search_term))
        if cand is not None:
            # 여전히 §1 후보 → 재제외(자동 발사, _upsert_exclusion이 cycle 승계·백오프 30→60→90).
            if _autofire_exclude(db, cand, now) is not None:
                reexcluded += 1
            # 실패(일일캡 소진 등)면 probation 유지 — 다음 레인 재시도(카운트 미증가, 정직).
        else:
            # 성과 자가 교정: 더는 후보 아님 → restored(행 보존=기억, 재충족 시 일반 경로로 재제외).
            row.status = "restored"
            row.last_transition_at = now
            db.commit()
            restored += 1

    return {
        "opened": opened, "reexcluded": reexcluded, "restored": restored,
        "healed": healed, "probation_healed": probation_healed,
    }


def _create_promote_proposal(db: Session, cand: dict, *, bm_verified: bool = False) -> NaverProposal:
    """전환 검색어 승격 후보 1건 → pending 제안(SS4, PLAN §3 SS4·§0 4 영구 Confirm). rationale에
    근거 수치(전환수·매출·검색어·출처 그룹)를 병기한다 — judge가 산출한 판정 문구(cand["reason"])
    뒤에 원자료를 덧붙여 콘솔에서 숫자만 보고도 판단할 수 있게 한다.

    ★bm_verified(BM P4, D-NAO-78 교차): 이 검색어가 대행사(사람)가 이미 정식 키워드로 등록한
    셋(bench_kind='keyword_verified')에 있으면 rationale 앞에 교차 플래그를 붙여 확신도 가점을
    표면화한다. **보조 신호일 뿐** — 전환 게이트(judge dconv≥1)를 대체하지 않고(§9-6), 승격은
    여전히 제안만·영구 Confirm(자동 발사 없음). bm_verified=False(기본·프라이어 부재)면 rationale은
    기존과 byte-동일(fail-open, §0 금지선 4).

    approval_source는 항상 None(자동 승인 절대 금지 — 생성류는 §0 4). ★실행 executor는 만들지
    않는다(L3 스코프, 모듈 상단 주석) — 이 제안이 실수로 approved되어 harness.execute()가
    호출돼도 proposal_type이 _ACTION_BY_PROPOSAL_TYPE에 없어 ActionNotExecutableError로
    fail-closed 거부된다(등록 자체를 안 하는 것이 안전장치)."""
    cross = "[대행사 검증 키워드 교차 — 사람이 이미 등록한 정답지, 확신도↑] " if bm_verified else ""
    obj = NaverProposal(
        proposal_type=search_term_judge.SEARCH_TERM_PROMOTE_TYPE,
        target_type="search_term",
        target_id=cand["search_term"],
        campaign_id=cand["campaign_id"],
        adgroup_id=cand["adgroup_id"],
        rationale=(
            f"{cross}{cand['reason']} 근거: 검색어='{cand['search_term']}'(출처그룹={cand['adgroup_id']}, "
            f"source={cand['source']}), 직접전환={cand['conv_direct_cnt']}건, "
            f"전체전환(직+간)={cand['conv_purchase_cnt']}건, 전환매출={cand['conv_purchase_amt']}원, "
            f"clk={cand['clk']}, cost={cand['cost']}원."
        ),
        expected_effect=(
            "직접전환 실증 검색어 — 정식 키워드 등록 시 노출 안정화 추정(확장검색 의존 탈피). "
            "실행 손 미구현(L3 스코프) — 등록은 Jino 콘솔 밖 수동 조작만 가능, 자동 발사 없음."
        ),
        status="pending",  # approval_source 미설정 = None(생성류 영구 Confirm, §0 4)
    )
    db.add(obj)
    return obj


def run_search_term_ss_lane(
    db: Session, *, now: datetime | None = None, bm_prior: set[str] | None = None,
) -> dict:
    """SS2 판단 → (SS3) 파워링크 제외 후보만 pending 제안 생성 + 쇼핑은 브리핑 diary만(제안
    생성 없음, PLAN §3 SS3-B) + (SS4) 승격 후보 전건 pending 제안 생성(영구 Confirm·실행 손
    없음). 실쓰기 0(전부 Confirm 전용).

    ★bm_prior(BM P4, D-NAO-78 교차·optional): 대행사 등록 키워드 텍스트 셋(bench_kind=
    'keyword_verified'). 승격 후보 중 이 셋에 든 검색어는 (a) 상한 슬롯을 먼저 채우도록 정렬
    가점 + (b) rationale에 교차 플래그를 받는다("사람이 이미 등록한 정답지"). **보조 신호일
    뿐** — 전환 게이트(judge)를 대체하지 않는다(§9-6). None(기본·프라이어 부재)이면 빈 셋으로
    처리 → 정렬·rationale 모두 기존과 동일(회귀 0, §0 금지선 4 fail-open).

    반환: {"shopping_candidates","powerlink_candidates","promote_candidates",
           "proposals_created","deduped","skipped_too_long",
           "promote_proposals_created","promote_deduped","promote_skipped_too_long",
           "promote_over_cap","promote_bm_crossed"}."""
    now = now or kst_now()
    verified = bm_prior or set()  # None → 빈 셋(교차 없음 = 기존 동일)
    judged = search_term_judge.judge_search_terms(db, now=now)
    shopping = judged["exclude_candidates"]["shopping"]
    powerlink = judged["exclude_candidates"]["powerlink"]
    promote = judged["promote_candidates"]

    # C4(codex 1R[P2] 기아 방지): PX3 재심사 루프를 신규 파워링크 자동 발사보다 **먼저** 돌린다.
    # probation 만료된 기지 손실 검색어의 재제외가 신규 후보보다 일일캡 우선권을 갖게 하기 위함
    # (같은 _SS_DAILY_EXCLUDE_CAP 공유 시, 이미 손실 실증된 기지가 하루 더 사는 것보다 재절단이
    # 우선). judge 산출(powerlink 리스트)을 재판정에 재사용하므로 judge 호출은 그대로 앞에 둔다.
    # 재제외가 오늘 캡을 소비하면 아래 remaining_cap 계산에 반영돼 신규는 잔여만큼만 발사된다.
    reexam = _run_reexamination(db, powerlink, now)

    # ★쇼핑(shopping)은 제안을 만들지 않는다 — API 제외 불가(§실측-0)라 브리핑 diary(아래)만
    # 남긴다. ★파워링크(PX2): pending Confirm 경로 제거 — §1 통과 후보를 status='approved' +
    # approval_source=ss_exclude 제안으로 자동 발사(exploration BX2 관례). 일일캡·킬스위치·
    # SHOPPING 거부·전환 재검증은 harness가 최종 강제(자동 경로도 동일 봉투).
    fired = 0
    deduped = 0
    skipped_too_long = 0
    slot_skipped = 0
    autofire_over_cap = 0
    autofire_failed = 0
    # 일일캡 잔여 — harness가 하드 강제하지만, 소진 후 무의미한 approved 제안·failed 노이즈를
    # 줄이려 라운드 내 사전 제한(§3 봉투). 재심사 재제외(PX3)도 같은 캡을 소비한다.
    remaining_cap = _remaining_exclude_cap(db, now)
    slot_cache: dict[str, int] = {}
    for cand in powerlink:
        # target_id(String(50)) 초과 검색어는 fail-closed skip(codex 2R[P1]) — 잘린 텍스트 실쓰기 차단.
        if len(cand["search_term"]) > _TARGET_ID_MAXLEN:
            skipped_too_long += 1
            continue
        if remaining_cap <= 0:
            autofire_over_cap += 1
            continue  # 일일 신규 제외 캡 소진 — 익일 재평가(harness도 하드 강제)
        adg = cand["adgroup_id"]
        # 그룹당 제외 슬롯(§3) — 상태 테이블 excluded 근사(캐시). 이번 라운드 발사분도 반영.
        if slot_cache.get(adg, _excluded_slot_count(db, adg)) >= _PL_GROUP_SLOT_CAP:
            slot_cache.setdefault(adg, _PL_GROUP_SLOT_CAP)
            slot_skipped += 1
            continue
        # 중복 방지: 살아있는 제외 제안 or 활성 제외 상태 행(excluded/probation) 있으면 스킵.
        if _has_open_or_executed(
            db, adg, cand["search_term"],
            proposal_type=search_term_judge.SEARCH_TERM_EXCLUDE_TYPE,
        ) or _active_exclusion_exists(db, adg, cand["search_term"]):
            deduped += 1
            continue
        if _autofire_exclude(db, cand, now) is None:
            autofire_failed += 1
            continue
        fired += 1
        remaining_cap -= 1
        slot_cache[adg] = slot_cache.get(adg, _excluded_slot_count(db, adg) - 1) + 1
    db.commit()

    # SS4 승격 후보 — 제외(SS3)와 동일한 표현 가능성 방어(target_id 길이) + dedup 규약을 그대로
    # 적용한다. 소스(shopping/expkeyword) 무관 전건 대상(judge가 이미 dconv≥1로 판정 완료).
    # ★상한(_SS_PROMOTE_CAP): 근거가 가장 강한 후보부터 채우기 위해 정렬 후 순회한다(라이브
    # 콘솔 범람 방지, 상수 주석 참조). 정렬 키 최상위 = 대행사 검증 교차(bm_prior 든 검색어 우선)
    # → 그다음 conv_direct_cnt 내림차순 → conv_purchase_amt 내림차순. verified가 빈 셋(프라이어
    # 부재)이면 최상위 키가 전건 0이라 나머지 두 키가 기존과 동일 순서를 결정한다(회귀 0).
    promote_sorted = sorted(
        promote,
        key=lambda c: (int(c["search_term"] in verified), c["conv_direct_cnt"], c["conv_purchase_amt"]),
        reverse=True,
    )
    promote_created = 0
    promote_deduped = 0
    promote_skipped_too_long = 0
    promote_over_cap = 0
    promote_bm_crossed = 0
    for cand in promote_sorted:
        if len(cand["search_term"]) > _TARGET_ID_MAXLEN:
            promote_skipped_too_long += 1
            continue
        if _has_open_or_executed(
            db, cand["adgroup_id"], cand["search_term"],
            proposal_type=search_term_judge.SEARCH_TERM_PROMOTE_TYPE,
        ):
            # dedup은 상한을 소모하지 않는다 — 신규 생성 슬롯은 그대로 다음 후보로 넘어간다.
            promote_deduped += 1
            continue
        if promote_created >= _SS_PROMOTE_CAP:
            promote_over_cap += 1
            continue
        bm_verified = cand["search_term"] in verified
        _create_promote_proposal(db, cand, bm_verified=bm_verified)
        promote_created += 1
        promote_bm_crossed += int(bm_verified)
    db.commit()

    # 표현 불가 후보 skip 기록(diary observe) — 길이 초과로 제안화하지 못한 파워링크 손실
    # 검색어를 운영자가 콘솔 수동 제외할 수 있게 남긴다(fail-closed 흔적, diary는 fail-open 계약).
    if skipped_too_long:
        diary.write_diary_entry(
            db, "observe", "",
            actor=diary.ACTOR_DAILY,
            action=search_term_judge.SEARCH_TERM_EXCLUDE_TYPE,
            rationale=(
                f"[검색어제외 skip] 파워링크 손실 검색어 {skipped_too_long}건 — 검색어 길이 "
                f">{_TARGET_ID_MAXLEN}자로 제안 표현 불가(target_id String({_TARGET_ID_MAXLEN})), "
                "콘솔 수동 제외 대상(codex 2R[P1] 잘린 텍스트 실쓰기 차단)"
            ),
            now=now,
        )

    # 승격(SS4) 길이 초과 skip 흔적 — 위와 동일 이유(표현 불가), 콘솔 수동 등록 후보로 남긴다.
    if promote_skipped_too_long:
        diary.write_diary_entry(
            db, "observe", "",
            actor=diary.ACTOR_DAILY,
            action=search_term_judge.SEARCH_TERM_PROMOTE_TYPE,
            rationale=(
                f"[검색어승격 skip] 전환 검색어 {promote_skipped_too_long}건 — 검색어 길이 "
                f">{_TARGET_ID_MAXLEN}자로 제안 표현 불가(target_id String({_TARGET_ID_MAXLEN})), "
                "콘솔 수동 등록 검토 대상"
            ),
            now=now,
        )

    # 쇼핑 브리핑 diary(1건 요약, event_type='observe' 재사용 — 관측/브리핑 성격). API 제외 불가라
    # Jino 콘솔 수동이 필요한 후보를 상위 N개 나열(전건은 제안 테이블에 노출). fail-open(diary.py 계약).
    if shopping:
        top = shopping[:_BRIEF_TOP_N]
        listed = "; ".join(f"{c['search_term']}(cost={c['cost']}·clk={c['clk']})" for c in top)
        more = f" 외 {len(shopping) - len(top)}건" if len(shopping) > len(top) else ""
        diary.write_diary_entry(
            db, "observe", "",
            actor=diary.ACTOR_DAILY,
            action=search_term_judge.SEARCH_TERM_EXCLUDE_TYPE,
            rationale=(
                f"[검색어제외 브리핑] 쇼핑 손실 검색어 {len(shopping)}건 — API 자동 제외 불가"
                f"(§실측-0), 콘솔 수동 제외 대상: {listed}{more}"
            ),
            now=now,
        )

    # 대행사 파워링크 고비용 검색어 브리핑(§4 2)은 PX4(search_term_px_briefing.
    # run_agency_powerlink_weekly_briefing)가 주간(일요일) 리듬으로 별도 담당한다 — 매일
    # 무조건 diary를 쓰면 D-NAO-79 예외 브리핑 원칙(노이즈 억제)에 어긋나 이 자리를 정리했다
    # (이 변수는 agency_powerlink_candidates 카운트에만 쓰인다, 원 diary 호출은 PX4로 이전).
    agency = judged.get("agency_powerlink", [])

    result = {
        "shopping_candidates": len(shopping),
        "powerlink_candidates": len(powerlink),
        "agency_powerlink_candidates": len(agency),
        "promote_candidates": len(promote),
        # PX2: 파워링크 자동 발사(pending 제안 대신) — 하위호환 위해 proposals_created 키 유지(=fired).
        "proposals_created": fired,
        "powerlink_fired": fired,
        "deduped": deduped,
        "skipped_too_long": skipped_too_long,
        "slot_skipped": slot_skipped,
        "autofire_over_cap": autofire_over_cap,
        "autofire_failed": autofire_failed,
        # PX3 재심사 루프
        "reexam_opened": reexam["opened"],
        "reexam_reexcluded": reexam["reexcluded"],
        "reexam_restored": reexam["restored"],
        "reexam_healed": reexam["healed"],  # C5: 크래시 고아 상태 행 재생성 건수
        "reexam_probation_healed": reexam["probation_healed"],  # F2: probation 클레임 크래시 창 치유 건수

        "promote_proposals_created": promote_created,
        "promote_deduped": promote_deduped,
        "promote_skipped_too_long": promote_skipped_too_long,
        "promote_over_cap": promote_over_cap,
        "promote_bm_crossed": promote_bm_crossed,  # BM P4: 승격 생성분 중 대행사 검증 교차 건수
    }
    log.info("search_term_ss_lane: %s", result)
    return result
