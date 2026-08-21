# entity_sync.py — naver_entity_sync_harness (캠페인/그룹/키워드 인벤토리 동기화, P2-S1)
# 역할: /ncc 캠페인·그룹·키워드를 순회 수집(collect_entities, 순수 SA) →
#   naver_entity에 전체 snapshot 교체 적재(sync_entities, 쓰기 harness).
# 키워드 행은 WEB_SITE(파워링크)만 수집 — 실측(docs/references/22): SHOPPING은 AD 리포트에서
#   keyword_id='-'(그룹 단위)로만 집계되어 개별 키워드 진단 대상이 아님. campaign·adgroup 행은
#   전 유형 수집(진단 보드 이름 표시용).
from __future__ import annotations

import json
import logging
from datetime import datetime  # noqa: F401 — external_occurred_at 반환 타입

from sqlalchemy.orm import Session

from app.models import NaverChangeLog, NaverEntity
from app.services.naver_ad.ad_external_change import parse_edit_tm  # D-NAO-147 editTm 파서(전 grain 공유)
from app.services.naver_sa_ad_fetcher import get_adgroups, get_campaigns_full, get_keywords
from app.utils.kst import kst_now

log = logging.getLogger(__name__)


def _status(raw_status: str, user_lock: bool) -> str:
    """네이버 status(ELIGIBLE 등)+userLock(수동 OFF)을 on/off/deleted로 정규화.

    ★off의 정의는 **userLock(사람이 내린 On/Off 스위치)**이지 "지금 노출 중인가"가 아니다.
    네이버 status는 자기 잠금 외의 사유로도 PAUSED가 된다 — 일예산 소진·부모 캠페인 정지·
    검수 중·비즈채널 이상. 그것들을 off로 접으면 "누가 껐다"는 신호가 오염된다(_STATUS_REASON
    분류표 주석 참조).
    """
    if raw_status in ("DELETED", ""):
        return "deleted"
    if user_lock:
        return "off"
    return "on"


# ── statusReason 분류표 (D-NAO-97) ────────────────────────────────────────────
# 출처(추정 아님): 네이버 공식 스키마 docs/references/data/ncc-heroes-ncc.json
#   Campaign.statusReason  = CAMPAIGN_PAUSED / CAMPAIGN_PENDING / CAMPAIGN_ENDED /
#                            CAMPAIGN_LIMITED_BY_BUDGET
#   Adgroup.statusReason   = 위 4종 + BUSINESS_CHANNEL_{PAUSED,DISAPPROVED,UNDER_REVIEW} +
#                            {PC,MOBILE}_BUSINESS_CHANNEL_{APPROVED,DISAPPROVED,UNDER_REVIEW} +
#                            GROUP_DELETED / GROUP_PAUSED / GROUP_LIMITED_BY_BUDGET
#   AdKeyword.statusReason = KEYWORD_DELETED / KEYWORD_PAUSED / KEYWORD_DISAPPROVED /
#                            KEYWORD_UNDER_REVIEW
#
# ★라이브 실측(2026-07-28 07:10 KST, 46캠페인·1,007그룹)이 **문서에 없는 값 2종**을 확인했다:
#   'ELIGIBLE'(정상 노출 중 — 전 캠페인 30건·전 그룹 459건이 이 값) 과
#   'BUSINESS_CHANNEL_ABNORMAL_INTERLOCK'(그룹 3건).
#   → 이 표는 **완전할 수 없다**. 그래서 판정은 화이트리스트로만 한다: "자기 잠금 사유"로
#     명시된 값이 아니면 전부 '사람이 끈 것이 아님'으로 본다(모르는 사유를 사람 탓으로
#     돌리지 않는다 = 유령 신호를 만들지 않는 방향으로 실패한다).
#
# 자기 잠금(userLock=true와 짝) 사유 — 엔티티 타입마다 다르다. 같은 'CAMPAIGN_PAUSED'라도
# 캠페인 행에서는 자기 잠금이지만 **그룹 행에서는 부모 정지 상속**이다(실측: 그룹 331건이
# CAMPAIGN_PAUSED인데 userLock=false).
_SELF_PAUSE_REASON = {
    "campaign": "CAMPAIGN_PAUSED",
    "adgroup": "GROUP_PAUSED",
    "keyword": "KEYWORD_PAUSED",
}
# 노출 중(정지 사유 없음)으로 관측되는 값. 빈 문자열·None도 같은 취급(사유 미제공).
_SERVING_REASONS = frozenset({"", "ELIGIBLE", "LIMITED_ELIGIBLE"})

# 사람이 읽을 사유 설명 — 없으면 원문 코드를 그대로 쓴다(지어내지 않는다).
_REASON_KO = {
    "CAMPAIGN_LIMITED_BY_BUDGET": "캠페인 일예산 소진",
    "GROUP_LIMITED_BY_BUDGET": "광고그룹 일예산 소진",
    "CAMPAIGN_PENDING": "캠페인 시작 전",
    "CAMPAIGN_ENDED": "캠페인 기간 종료",
    "CAMPAIGN_PAUSED": "상위 캠페인 OFF",
    "GROUP_PAUSED": "상위 광고그룹 OFF",
    "KEYWORD_DISAPPROVED": "키워드 검수 반려",
    "KEYWORD_UNDER_REVIEW": "키워드 검수 중",
    "BUSINESS_CHANNEL_PAUSED": "비즈채널 정지",
    "BUSINESS_CHANNEL_DISAPPROVED": "비즈채널 검수 반려",
    "BUSINESS_CHANNEL_UNDER_REVIEW": "비즈채널 검수 중",
    "BUSINESS_CHANNEL_ABNORMAL_INTERLOCK": "비즈채널 연동 이상",
}


def _campaign_status(c: dict) -> str:
    """캠페인 dict → on/off/deleted. off의 근거는 userLock 하나뿐이다(D-NAO-97).

    `_status()`를 그대로 쓰지 않는 이유: `_status`는 raw_status가 빈 문자열이면 deleted로
    본다. 캠페인 경로에서는 status 필드 부재가 곧 삭제라고 볼 근거가 없고(구버전은 ""를 on으로
    취급했다), 잘못 deleted로 접으면 로스터·진단·스냅샷에서 캠페인이 통째로 사라진다.
    """
    raw = str(c.get("status", "")).upper()
    if raw == "DELETED":
        return "deleted"
    lock = c["user_lock"] if "user_lock" in c else raw == "PAUSED"
    return "off" if lock else "on"


def _is_system_reason(entity_type: str, reason) -> bool:
    """이 사유가 '시스템/상속 사유'인가(= 사람이 이 엔티티를 끈 것이 아닌가).

    True인 경우: 예산 소진, 부모 정지 상속, 검수·비즈채널, 그리고 **문서에 없는 미지의 사유**.
    False인 경우: 노출 중(_SERVING_REASONS) 또는 이 엔티티 자신의 OFF 스위치(_SELF_PAUSE_REASON).
    """
    if reason is None:
        return False
    r = str(reason).upper()
    if r in _SERVING_REASONS:
        return False
    return r != _SELF_PAUSE_REASON.get(entity_type)


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


def external_occurred_at(entity: NaverEntity, edit_tm_raw, now) -> "datetime | None":
    """이 외부 변경이 **실제로 일어난** 시각(KST). 창 밖·부재는 None (D-NAO-147).

    창 = (직전 관측, 이번 관측] = `(entity.synced_at, now]`. bm_diff의 `_occurred_at`과 **같은
    규약**이다 — 다른 건 앵커를 어디서 얻느냐뿐이다(저기는 스냅샷 두 장, 여기는 엔티티 행 하나).

    ★왜 창이 필요한가: `editTm`은 마지막 수정만 남긴다. 값이 변했는데 editTm이 직전 관측보다
    과거라면 그 editTm은 **이 변경의 시각이 아니다**(우리 쓰기가 editTm을 전진시킨 뒤 값만
    뒤늦게 보이는 경우 등). 지어내느니 비워 둔다 — 화면은 NULL이면 "감지 시각"이라고 정직하게
    말한다.
    ★상한이 `now`인 이유: 이 관측을 방금 했으므로 그 이후의 editTm은 있을 수 없다. 있다면
    시계가 어긋난 것이므로 역시 신뢰하지 않는다.
    ★`entity.synced_at`은 호출 시점에 **아직 갱신 전**이어야 한다(직전 관측 시각) — 기존
    밸브들(`_log_external_status_change` 등)이 이미 그 계약 위에서 돌고 있어 같은 자리에 붙인다.
    """
    at = parse_edit_tm(edit_tm_raw)
    if at is None:
        return None
    prev = getattr(entity, "synced_at", None)
    if prev is None:
        return None  # 직전 관측을 모르면 창을 못 만든다 — 판정 유보
    return at if prev < at <= now else None


def reg_occurred_at(reg_tm_raw, prev_sync, now) -> "datetime | None":
    """키워드가 **실제로 등록된** 시각(KST). 창 밖·부재는 None (D-NAO-148).

    창 = (직전 sync, 이번 sync] = `(prev_sync, now]`. `external_occurred_at`과 같은 규약이고
    다른 건 두 가지뿐이다:
      ① 앵커가 `regTm`(생성)이지 `editTm`(수정)이 아니다.
      ② 하한을 **엔티티 행에서 못 얻는다** — 신규 등록 키워드는 직전 관측이 존재하지 않기
         때문이다. 대신 직전 sync 실행 시각(기존 행들의 max synced_at)을 쓴다. "그때 수집엔
         없었다"가 곧 "그 이후에 만들어졌다"이므로 이 하한은 정당하다.

    ★재등장(deleted→on)이 자동으로 NULL이 되는 것이 이 창의 핵심 효용이다: 그 키워드의
    regTm은 원래 만들어진 옛 시각이라 창 밖으로 떨어진다. 등록이 아닌 것을 등록 시각으로
    적지 않는다 — 부활·추적 개시(regTm이 2022년)도 같은 이유로 걸러진다.
    ★regTm은 **불변**이라 editTm과 달리 소급 판정이 썩지 않는다(LESSONS #119의 전제인
    "마지막 수정만 남는다"가 생성 시각엔 없다). 과거 행 백필이 성립하는 근거가 이것이다.
    """
    at = parse_edit_tm(reg_tm_raw)  # regTm도 같은 UTC ISO8601 'Z' 형식(라이브 실측)
    if at is None or prev_sync is None:
        return None
    return at if prev_sync < at <= now else None


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
            # ★D-NAO-97: 캠페인도 그룹·키워드와 **똑같이 userLock을 그대로** 쓴다.
            #   구버전은 `status == "PAUSED"` → off로 역추론해서, 일예산 소진 자동정지
            #   (PAUSED · CAMPAIGN_LIMITED_BY_BUDGET · userLock=false)를 사람 조작으로
            #   오분류했다(prod change_log id 847 — 03 캠페인, 2026-07-27 22:46).
            #   ⚠️ user_lock 키가 **아예 없는** 입력(구버전 fetcher·레거시 주입 rows)에서는
            #   옛 규칙으로 되돌아간다. "키가 없다"를 False로 읽으면 사람이 실제로 끈
            #   캠페인이 on으로 뒤집혀 유령 '외부 재개'가 생기기 때문 — 없는 정보를
            #   기본값으로 지어내지 않는다.
            "status": _campaign_status(c),
            "status_reason": c.get("status_reason"),
            "bid_amt": None,
            "edit_tm": c.get("edit_tm"),  # D-NAO-146 발생 시각 앵커(네이버 editTm 원문)
            "reg_tm": c.get("reg_tm"),    # D-NAO-148 생성 시각 앵커(네이버 regTm 원문)
        })

        ags = (adgroups_by_campaign or {}).get(cid) if adgroups_by_campaign is not None else get_adgroups(cid)
        for ag in ags or []:
            aid = ag["adgroup_id"]
            rows.append({
                "entity_type": "adgroup", "entity_id": aid, "parent_id": cid,
                "campaign_id": cid, "campaign_type": ctype, "name": ag.get("name", ""),
                "status": _status(ag.get("status", ""), ag.get("user_lock", False)),
                "status_reason": ag.get("status_reason"),
                "bid_amt": ag.get("bid_amt"),
                "edit_tm": ag.get("edit_tm"),  # D-NAO-146
                "reg_tm": ag.get("reg_tm"),    # D-NAO-148
                "pc_bid_weight": ag.get("pc_bid_weight"),        # D-NAO-218(M2-b2)
                "mobile_bid_weight": ag.get("mobile_bid_weight"),
            })

            if ctype != "WEB_SITE":
                continue  # 실측: SHOPPING/BRAND_SEARCH 키워드는 개별 진단 대상 아님
            kws = (keywords_by_adgroup or {}).get(aid) if keywords_by_adgroup is not None else get_keywords(aid)
            for kw in kws or []:
                rows.append({
                    "entity_type": "keyword", "entity_id": kw["keyword_id"], "parent_id": aid,
                    "campaign_id": cid, "campaign_type": ctype, "name": kw.get("keyword", ""),
                    "status": _status(kw.get("status", ""), kw.get("user_lock", False)),
                    "status_reason": kw.get("status_reason"),
                    "bid_amt": kw.get("bid_amt"),
                    "qi_grade": kw.get("qi_grade"),  # D-NAO-46②: 품질지수 1~7(get_keywords 무상 편승)
                    "edit_tm": kw.get("edit_tm"),  # D-NAO-147
                    "reg_tm": kw.get("reg_tm"),    # D-NAO-148
                })

    log.info("naver_entity collect: campaign=%d adgroup=%d keyword=%d",
              sum(1 for r in rows if r["entity_type"] == "campaign"),
              sum(1 for r in rows if r["entity_type"] == "adgroup"),
              sum(1 for r in rows if r["entity_type"] == "keyword"))
    return rows


def _log_external_status_change(
    db: Session, entity: NaverEntity, new_status: str, now, status_reason=None, edit_tm=None,
) -> None:
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
    그 이후 재개를 보수적으로 건너뛰게 만들 뿐 안전하다.

    ★D-NAO-97 2중 안전장치(status_reason): 정지 방향(→off)인데 네이버가 준 사유가 '시스템/상속
    사유'(예산 소진·검수·부모 정지 등)면 사람 조작으로 기록하지 않는다. 정상 경로에서는 이미
    collect_entities가 userLock을 그대로 쓰므로 예산 정지는 여기까지 오지도 않는다 — 이 가드는
    userLock을 못 받는 경로(구버전 fetcher·레거시 주입)에서만 발화하는 **벨트 위의 멜빵**이다.
    재개 방향(→on)에는 걸지 않는다: 잠금 해제는 사유 필드와 무관하게 실제 상태 변화다."""
    old_lock = entity.status == "off"
    new_lock = new_status == "off"
    if old_lock == new_lock:
        return

    if new_lock and _is_system_reason(entity.entity_type, status_reason):
        log.info(
            "external_status_change 억제(시스템 사유): %s %s reason=%s — 사람 조작 아님",
            entity.entity_type, entity.entity_id, status_reason,
        )
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
        occurred_at=external_occurred_at(entity, edit_tm, now),  # D-NAO-147(창 밖·부재면 None)
        before_value=json.dumps({"userLock": old_lock}),
        after_value=json.dumps({"userLock": new_lock}),
        rationale="entity_sync 감지: 외부(MOP/사람) 상태 변경",
    ))
    log.info("external_status_change detected: %s %s %s→%s",
             entity.entity_type, entity.entity_id, entity.status, new_status)


def _log_system_status_change(db: Session, entity: NaverEntity, new_reason, now) -> None:
    """D-NAO-97: 네이버가 시스템 사유로 캠페인을 멈추거나 다시 돌리면 **관찰**로 기록한다.

    ★왜 별도 액션인가: 이 사건들은 지금까지 두 가지 방식으로만 다뤄졌다 — 캠페인은 '사람이
    잠갔다'로 **오분류**(id 847)되고, 그룹·키워드는 아예 **기록되지 않았다**. 둘 다 틀렸다.
    일예산 소진은 진짜로 일어난 일이고(D-NAO-59 목적함수에서 "볼륨을 남기고 멈춘 순간"이다),
    사람 조작과 같은 통에 담으면 안 된다. 그래서 액션 이름을 나눈다.

    `budget_exhausted_pause`가 아니라 `system_status_change`인 이유: 같은 자리에서 예산 소진
    외에 검수 중·기간 종료·비즈채널 이상·**문서에 없는 미지의 사유**도 나온다(_is_system_reason
    주석). 사유는 payload에 원문 그대로 싣고, 액션 이름은 사유를 미리 단정하지 않는다.

    ⚠️ 캠페인 행만 기록한다(쓰기 폭증 방어): 부모 캠페인 하나가 멈추면 그 밑 그룹 수백 개가
    동시에 CAMPAIGN_PAUSED로 바뀐다(실측 331그룹). 키워드까지 열면 91,005행 크론에서
    입찰 밸브가 막으려던 폭증이 그대로 재현된다. 캠페인은 46행이라 상한이 구조적으로 막힌다.
    그룹·키워드의 사유는 naver_entity.status_reason 컬럼에 현재값으로 남아 조회 가능하다.

    dry_run=True(관찰 — 네이버 API 쓰기가 아님, `_log_external_qi_change`와 같은 규약).
    EXTERNAL_DETECTION_ACTIONS에도 넣지 않는다: 커맨드센터의 actor=external은 "MOP/사람이
    바꿨다"는 뜻이고, 시스템 정지는 행위자가 없다.

    호출 계약: `e.status`·`e.status_reason` 대입 **전**(옛값 비교 — 다른 밸브와 동일).
    """
    if entity.entity_type != "campaign":
        return
    was_system = entity.status != "off" and _is_system_reason(entity.entity_type, entity.status_reason)
    now_system = entity.status != "deleted" and _is_system_reason(entity.entity_type, new_reason)
    if was_system == now_system:
        return

    reason = str(new_reason or "") if now_system else str(entity.status_reason or "")
    gloss = _REASON_KO.get(reason.upper(), reason or "사유 미제공")
    verb = "시작" if now_system else "해제"
    db.add(NaverChangeLog(
        entity_type=entity.entity_type,
        entity_id=entity.entity_id,
        campaign_id=entity.campaign_id,
        action="system_status_change",
        proposal_id=None,
        dry_run=True,
        changed_at=now,
        before_value=json.dumps({"statusReason": entity.status_reason}, ensure_ascii=False),
        after_value=json.dumps({"statusReason": new_reason}, ensure_ascii=False),
        rationale=f"entity_sync 감지: 시스템 사유 정지 {verb} — {gloss}({reason or 'N/A'})",
    ))
    log.info("system_status_change detected: %s %s %s→%s (%s)",
             entity.entity_type, entity.entity_id, entity.status_reason, new_reason, verb)


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
    our_writes: dict[tuple[str, str], NaverChangeLog], edit_tm=None,
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
        occurred_at=external_occurred_at(entity, edit_tm, now),  # D-NAO-147(창 밖·부재면 None)
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
    prev_sync=None,
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

    prev_sync(D-NAO-148): 직전 sync 실행 시각 — added 방향의 occurred_at 창 하한. removed는
    쓰지 않는다(`regTm`은 "언제 지웠나"에 답하지 못한다 — 삭제 시각은 응답에 없다).
    """
    if added and not bootstrap:
        _emit_inventory_side(db, added, now, direction="added", prev_sync=prev_sync)
    if removed:
        _emit_inventory_side(db, removed, now, direction="removed")


def _emit_inventory_side(db: Session, items: list[dict], now, *, direction: str,
                         prev_sync=None) -> None:
    """added/removed 한 방향을 기록. 200 초과면 __bulk__ 요약 1행, 아니면 개별행.

    payload 배치가 방향에 따라 다르다: added는 after_value(before=None, 등장), removed는
    before_value(after=None, 소실). 대량 요약은 {"bulk":True,"count":N,"sample":[이름 20개]}.
    """
    is_added = direction == "added"
    action = "external_keyword_added" if is_added else "external_keyword_removed"
    n = len(items)
    n_occurred = 0  # D-NAO-148: 등록 시각까지 확정된 건수(로그로 매일 표면화)

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
        # D-NAO-148: 등록 방향만 시각을 붙인다(창 밖·부재·재등장이면 None = 시각 불명).
        occurred = reg_occurred_at(it.get("reg_tm"), prev_sync, now) if is_added else None
        n_occurred += int(occurred is not None)
        db.add(NaverChangeLog(
            entity_type="keyword", entity_id=it["entity_id"], campaign_id=it["campaign_id"],
            action=action, proposal_id=None, dry_run=False, changed_at=now,
            before_value=None if is_added else payload,
            after_value=payload if is_added else None,
            rationale=rationale,
            occurred_at=occurred,
        ))
    log.info("keyword_inventory %s: %d건 개별 기록(occurred=%d)", direction, n, n_occurred)


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
    # D-NAO-148: 직전 sync 실행 시각 = 기존 행들의 max(synced_at). **루프 전에** 구한다 —
    # 루프가 본 행마다 synced_at을 now로 갱신하므로 뒤에서 구하면 항상 now가 나온다.
    # 신규 등록 키워드의 occurred_at 창 하한이 이 값이다(그 키워드엔 직전 관측 행이 없다).
    # stale 행은 synced_at이 옛날에 멈춰 있지만 max라 영향 없다. 부트스트랩이면 None → NULL.
    prev_sync = max((e.synced_at for e in existing.values() if e.synced_at is not None),
                    default=None)
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
                    "reg_tm": r.get("reg_tm"),  # D-NAO-148: 등록 시각 앵커
                })
            db.add(NaverEntity(
                entity_type=r["entity_type"], entity_id=r["entity_id"], parent_id=r["parent_id"],
                campaign_id=r["campaign_id"], campaign_type=r["campaign_type"], name=r["name"],
                status=r["status"], status_reason=r.get("status_reason"),
                bid_amt=r.get("bid_amt"), qi_grade=r.get("qi_grade"),
                edit_tm=r.get("edit_tm"), reg_tm=r.get("reg_tm"), synced_at=now,
                # D-NAO-218(M2-b2): campaign/keyword 행엔 키 자체가 없다 → get()이 None
                # → "미상(NULL)"으로 정직하게 저장(가짜 100을 지어내지 않는다).
                pc_bid_weight=r.get("pc_bid_weight"), mobile_bid_weight=r.get("mobile_bid_weight"),
            ))
        else:
            if r["entity_type"] == "keyword":
                if e.status == "deleted" and r["status"] != "deleted":
                    # (b) 재등장: 기존 deleted 키워드가 다시 켜짐. (status 밸브는 e.status=deleted라 미발화)
                    # ★reg_tm을 실어도 창 밖(원래 만들어진 옛 시각)이라 occurred_at은 NULL이 된다
                    #   — 재등장은 등록이 아니므로 그게 맞다(D-NAO-148).
                    added_keywords.append({
                        "name": r["name"], "entity_id": r["entity_id"],
                        "campaign_id": r["campaign_id"], "bid": _norm_bid(r.get("bid_amt")),
                        "reg_tm": r.get("reg_tm"),
                    })
                elif e.status != "deleted" and r["status"] == "deleted":
                    # (a) 삭제: API가 DELETED 반환. (on→deleted는 lock 변화가 아니라 status 밸브 미발화)
                    removed_keywords.append({
                        "name": e.name, "entity_id": e.entity_id,
                        "campaign_id": e.campaign_id, "bid": _norm_bid(e.bid_amt),
                    })
            if e.status != r["status"] and e.status != "deleted":
                _log_external_status_change(db, e, r["status"], now, r.get("status_reason"),
                                            edit_tm=r.get("edit_tm"))
            # D-NAO-97: 시스템 사유 정지(예산 소진 등) 관찰 — 캠페인 행만, dry_run=True.
            _log_system_status_change(db, e, r.get("status_reason"), now)
            # ★두 밸브 모두 **대입 전**에 호출한다 — entity의 옛값을 읽어야 diff가 나온다.
            #   (D-NAO-47 입찰가 / D-NAO-46② 품질지수. 병합 2026-07-17: 두 세션이 서로 모르는
            #   채 같은 자리에 같은 패턴을 만들었고, 관심사가 달라 둘 다 유지한다.)
            _log_external_bid_change(db, e, r.get("bid_amt"), now, our_bid_writes,
                                     edit_tm=r.get("edit_tm"))
            _log_external_qi_change(db, e, r.get("qi_grade"), now)
            e.parent_id = r["parent_id"]
            e.campaign_id = r["campaign_id"]
            e.campaign_type = r["campaign_type"]
            e.name = r["name"]
            e.status = r["status"]
            if "status_reason" in r:
                # 키가 없는 레거시 주입 rows는 마지막 관측 사유를 지우지 않는다(qi와 같은 규약).
                e.status_reason = r["status_reason"]
            e.bid_amt = r.get("bid_amt")
            # D-NAO-218(M2-b2): bid_amt와 같은 규약(항상 최신 관측으로 덮어쓴다·last-known
            # 보존 안 함) — campaign/keyword 행은 r에 키가 없어 매번 None(=해당없음)으로 유지.
            e.pc_bid_weight = r.get("pc_bid_weight")
            e.mobile_bid_weight = r.get("mobile_bid_weight")
            if r.get("reg_tm") is not None:
                # D-NAO-148: regTm은 불변이므로 last-known을 None으로 지우지 않는다(qi와 같은
                # 규약). 레거시 주입 rows·API 누락에 기존 생성 시각을 잃지 않기 위함.
                e.reg_tm = r["reg_tm"]
            if "edit_tm" in r:
                # D-NAO-146: 키가 없는 레거시 주입 rows는 마지막 관측 editTm을 지우지 않는다
                # (status_reason과 같은 규약). 값이 None으로 **온** 경우는 관측 결과이므로 반영한다.
                e.edit_tm = r["edit_tm"]
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
    _log_keyword_inventory_changes(db, added_keywords, removed_keywords, now,
                                   bootstrap=bootstrap, prev_sync=prev_sync)

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
