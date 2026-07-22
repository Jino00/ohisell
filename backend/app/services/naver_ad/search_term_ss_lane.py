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

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import NaverChangeLog, NaverProposal, NaverSearchTermExclusion
from app.services.naver_ad import diary, search_term_judge
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


def _upsert_exclusion(db: Session, cand: dict, restrict_kwd_id: str | None, now: datetime) -> None:
    """제외 실쓰기 성공 후 상태 행 upsert(§2 상태기계). 같은 (adgroup, term) 행이 있으면 cycle
    승계(+1)·행 재사용(restored/probation 재제외 경로), 없으면 cycle=1 신규. next_review_at =
    today + min(30×cycle, 90)일(백오프 30→60→90 cap). status='excluded'로 (재)진입."""
    row = db.query(NaverSearchTermExclusion).filter(
        NaverSearchTermExclusion.adgroup_id == cand["adgroup_id"],
        NaverSearchTermExclusion.search_term == cand["search_term"],
    ).first()
    if row is None:
        cycle = 1
        row = NaverSearchTermExclusion(
            campaign_id=cand["campaign_id"], adgroup_id=cand["adgroup_id"],
            search_term=cand["search_term"], cycle=cycle,
            excluded_at=now, last_transition_at=now,
        )
        db.add(row)
    else:
        cycle = row.cycle + 1
        row.cycle = cycle
        row.excluded_at = now
        row.last_transition_at = now
    row.restrict_kwd_id = restrict_kwd_id
    row.status = "excluded"
    row.probation_until = None
    row.next_review_at = now.date() + timedelta(days=min(30 * cycle, 90))
    row.cost_at_exclusion = int(cand.get("cost", 0))
    db.commit()


def _autofire_exclude(db: Session, cand: dict, now: datetime) -> NaverChangeLog | None:
    """파워링크 제외 후보 1건 자동 발사(exploration BX2 관례 복제): status='approved' +
    approval_source=APPROVAL_SOURCE_SS_EXCLUDE 제안 생성 → naver_execution_harness.execute()로
    같은 흐름 즉시 실행. 성공 시 WriteResult(after_value.created_ids)에서 restrict_kwd_id 회수해
    상태 행 upsert(§2). 실패(킬스위치/일일캡/SHOPPING/가드)는 harness가 change_log·상태를 확정
    하므로 여기선 None 반환(브리핑 카운트만). 지연 import(순환 회피 — harness가 auto_operator를,
    auto_operator가 이 모듈을 import하지 않지만 exploration 관례 유지)."""
    from app.services.naver_ad import exploration
    from app.services.naver_ad import naver_execution_harness as harness

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

    # PX3 재심사 루프(개방·probation·재제외/복귀)는 다음 페이즈에서 이 자리에 배선한다.
    reexam = {"opened": 0, "reexcluded": 0, "restored": 0}

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

    # 대행사 파워링크 고비용 검색어 브리핑(§4 2) — 스코프 밖(실쓰기 0) 관찰 산출물. 신규 진입만
    # 노이즈 억제 없이 상위 N 나열(주 리듬 절삭은 PX4 몫, 여기선 후보 존재 시 diary 1건).
    agency = judged.get("agency_powerlink", [])
    if agency:
        top = agency[:_BRIEF_TOP_N]
        listed = "; ".join(f"{c['search_term']}(cost={c['cost']}·clk={c['clk']})" for c in top)
        more = f" 외 {len(agency) - len(top)}건" if len(agency) > len(top) else ""
        diary.write_diary_entry(
            db, "observe", "",
            actor=diary.ACTOR_DAILY,
            action=search_term_judge.SEARCH_TERM_EXCLUDE_TYPE,
            rationale=(
                f"[파워링크 대행사 고비용 브리핑] {len(agency)}건(30d cost≥30,000·clk≥10) — 대행사 "
                f"캠페인이라 자동 제외 없음, Jino 전달 검토: {listed}{more}"
            ),
            now=now,
        )

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
        "promote_proposals_created": promote_created,
        "promote_deduped": promote_deduped,
        "promote_skipped_too_long": promote_skipped_too_long,
        "promote_over_cap": promote_over_cap,
        "promote_bm_crossed": promote_bm_crossed,  # BM P4: 승격 생성분 중 대행사 검증 교차 건수
    }
    log.info("search_term_ss_lane: %s", result)
    return result
