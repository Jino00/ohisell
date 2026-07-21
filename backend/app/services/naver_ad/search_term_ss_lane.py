# search_term_ss_lane.py — 검색어 제외·승격 브리핑/제안 생성 레인 (SS3·SS4 생성층,
#   docs/PLAN_naver-ad-searchterm-ss.md §3). 역할(Harness): SS2 판단(search_term_judge)을
#   소비해 (SS3) **파워링크 제외 후보만** pending 제안(Confirm 전용)으로 생성하고 **쇼핑 후보는
#   브리핑 diary만** 남기며(제안 생성 없음 — §실측-0 쇼핑 API 제외 불가·PLAN §3 SS3-B "브리핑만"),
#   (SS4) **전환 검색어 승격 후보**를 별도 pending 제안(영구 Confirm·실행 손 없음)으로 생성한다.
#   일 레인(auto_operator run_daily_lane)과 같은 08:50 흐름에 편입(scheduler).
#
#   ★실쓰기 0 (PLAN §3 SS3·SS4): 이 레인은 **제안 생성·브리핑만** 한다 — 자동 승인(status=
#   'approved') 배선 절대 없음. approval_source는 항상 None(Jino 콘솔 Confirm이 유일한 승인
#   경로). 쇼핑은 API 제외 불가(§실측-0)라 제안 자체를 만들지 않고 브리핑+콘솔 수동만, 파워링크
#   제외는 초기엔 Confirm 전용(§난제 5). 제외 실쓰기 손은 Confirm 후 naver_execution_harness의
#   search_term_exclude 분기가 담당(쇼핑은 그 분기도 SHOPPING 명시 거부로 도달 자체가 없음).
#   승격(SS4)은 실행 손 자체가 없다(L3 스코프, PLAN §3 SS4) — 정식 키워드 등록은 Jino가 콘솔
#   밖에서 수동으로만 한다. proposal_type=search_term_promote는 naver_execution_harness의
#   _ACTION_BY_PROPOSAL_TYPE에 절대 등록하지 않는다(등록하면 미구현 executor를 부르게 되어
#   위험 — 매핑 부재 자체가 fail-closed 안전장치).
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import NaverProposal
from app.services.naver_ad import diary, search_term_judge
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 브리핑 diary 요약에 나열할 쇼핑 후보 상위 건수(제안 자체는 전건 생성, 요약만 절삭).
_BRIEF_TOP_N = 10

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


def _create_proposal(db: Session, cand: dict) -> NaverProposal:
    """파워링크 제외 후보 1건 → pending 제안(Confirm 전용). approval_source=None(자동 승인
    절대 금지). ★쇼핑은 이 함수를 호출하지 않는다 — SS3-B는 브리핑만(PLAN §3, §실측-0)."""
    obj = NaverProposal(
        proposal_type=search_term_judge.SEARCH_TERM_EXCLUDE_TYPE,
        target_type="search_term",
        target_id=cand["search_term"],
        campaign_id=cand["campaign_id"],
        adgroup_id=cand["adgroup_id"],
        rationale=cand["reason"],
        expected_effect=(
            f"제외 시 낭비비용 회수 추정(전환0·cost={cand['cost']}원). "
            f"Confirm 후 파워링크 자동 제외(§난제 5)."
        ),
        status="pending",  # approval_source 미설정 = None(Jino 콘솔 Confirm이 유일 승인 경로)
    )
    db.add(obj)
    return obj


def _create_promote_proposal(db: Session, cand: dict) -> NaverProposal:
    """전환 검색어 승격 후보 1건 → pending 제안(SS4, PLAN §3 SS4·§0 4 영구 Confirm). rationale에
    근거 수치(전환수·매출·검색어·출처 그룹)를 병기한다 — judge가 산출한 판정 문구(cand["reason"])
    뒤에 원자료를 덧붙여 콘솔에서 숫자만 보고도 판단할 수 있게 한다.

    approval_source는 항상 None(자동 승인 절대 금지 — 생성류는 §0 4). ★실행 executor는 만들지
    않는다(L3 스코프, 모듈 상단 주석) — 이 제안이 실수로 approved되어 harness.execute()가
    호출돼도 proposal_type이 _ACTION_BY_PROPOSAL_TYPE에 없어 ActionNotExecutableError로
    fail-closed 거부된다(등록 자체를 안 하는 것이 안전장치)."""
    obj = NaverProposal(
        proposal_type=search_term_judge.SEARCH_TERM_PROMOTE_TYPE,
        target_type="search_term",
        target_id=cand["search_term"],
        campaign_id=cand["campaign_id"],
        adgroup_id=cand["adgroup_id"],
        rationale=(
            f"{cand['reason']} 근거: 검색어='{cand['search_term']}'(출처그룹={cand['adgroup_id']}, "
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


def run_search_term_ss_lane(db: Session, *, now: datetime | None = None) -> dict:
    """SS2 판단 → (SS3) 파워링크 제외 후보만 pending 제안 생성 + 쇼핑은 브리핑 diary만(제안
    생성 없음, PLAN §3 SS3-B) + (SS4) 승격 후보 전건 pending 제안 생성(영구 Confirm·실행 손
    없음). 실쓰기 0(전부 Confirm 전용).

    반환: {"shopping_candidates","powerlink_candidates","promote_candidates",
           "proposals_created","deduped","skipped_too_long",
           "promote_proposals_created","promote_deduped","promote_skipped_too_long"}."""
    now = now or kst_now()
    judged = search_term_judge.judge_search_terms(db, now=now)
    shopping = judged["exclude_candidates"]["shopping"]
    powerlink = judged["exclude_candidates"]["powerlink"]
    promote = judged["promote_candidates"]

    created = 0
    deduped = 0
    skipped_too_long = 0
    # ★쇼핑(shopping)은 제안을 만들지 않는다 — API 제외 불가(§실측-0)라 브리핑 diary(아래)만
    # 남긴다. 제안을 만들면 콘솔에서 approve+execute 시 harness가 422(SHOPPING 명시 거부)로
    # 튕기고 failed 감사 기록만 쌓이는 노이즈가 발생한다(PLAN §3 SS3-B "브리핑만" 사양).
    for cand in powerlink:
        # target_id(String(50)) 초과 검색어는 fail-closed skip(codex 2R[P1]) — 잘린 텍스트로
        # 실쓰기 경로 자체를 물리적으로 차단한다(전문 페이로드 컬럼 부재 → A안=skip).
        if len(cand["search_term"]) > _TARGET_ID_MAXLEN:
            skipped_too_long += 1
            continue
        if _has_open_or_executed(
            db, cand["adgroup_id"], cand["search_term"],
            proposal_type=search_term_judge.SEARCH_TERM_EXCLUDE_TYPE,
        ):
            deduped += 1
            continue
        _create_proposal(db, cand)
        created += 1
    db.commit()

    # SS4 승격 후보 — 제외(SS3)와 동일한 표현 가능성 방어(target_id 길이) + dedup 규약을 그대로
    # 적용한다. 소스(shopping/expkeyword) 무관 전건 대상(judge가 이미 dconv≥1로 판정 완료).
    promote_created = 0
    promote_deduped = 0
    promote_skipped_too_long = 0
    for cand in promote:
        if len(cand["search_term"]) > _TARGET_ID_MAXLEN:
            promote_skipped_too_long += 1
            continue
        if _has_open_or_executed(
            db, cand["adgroup_id"], cand["search_term"],
            proposal_type=search_term_judge.SEARCH_TERM_PROMOTE_TYPE,
        ):
            promote_deduped += 1
            continue
        _create_promote_proposal(db, cand)
        promote_created += 1
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

    result = {
        "shopping_candidates": len(shopping),
        "powerlink_candidates": len(powerlink),
        "promote_candidates": len(promote),
        "proposals_created": created,
        "deduped": deduped,
        "skipped_too_long": skipped_too_long,
        "promote_proposals_created": promote_created,
        "promote_deduped": promote_deduped,
        "promote_skipped_too_long": promote_skipped_too_long,
    }
    log.info("search_term_ss_lane: %s", result)
    return result
