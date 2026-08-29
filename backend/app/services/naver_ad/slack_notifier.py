# slack_notifier.py — slack_notifier SA (P2-S3 T4, D-NAO-21 / notify_text=BM Phase5, D-NAO-79)
# 역할(SA): 생성된 제안 요약을 Slack incoming webhook으로 발송. 순수 함수 — 광고 API 무관,
#   naver_proposals 쓰기도 안 함(harness가 slack_ts를 저장할지는 harness 소관).
#   webhook 미설정 시 no-op(D-NAO-21, URL 미제공 상태가 정상 운영 경로).
#   notify_text()는 임의 텍스트(BM 예외 브리핑 등)를 같은 발송 로직(_post_with_retries)으로
#   보낸다 — notify()의 proposals 전용 포맷과 발송 인프라(재시도·no-op 관례)를 공유한다.
from __future__ import annotations

import logging
import os
import time

import requests

log = logging.getLogger(__name__)

_WEBHOOK_ENV = "NAVER_SLACK_WEBHOOK_URL"
_TIMEOUT_SECONDS = 10
_MAX_RETRIES = 3


# ─────────────────────────────────────────────────────────────────────────────
# 사람이 읽는 요약 (D-NAO-249, Jino 2026-08-29 발의)
#
# ★왜 고쳤나(라이브 증거): Slack에 「네이버 SA 제안 1건 생성 / - trigger_pacing: 1건」만
#   도착했고 Jino 원문 *"무슨의미인지 전혀 알 수가 없어"*. 옛 _build_summary가 유형별 건수만
#   세고 **호출부가 이미 쥐고 있던 캠페인·금액·배수를 통째로 버린** 것이 원인이다.
#   같은 병을 CTR 경보에서 이미 앓았고(D-NAO-103) 그때 만든 게 alert_humanizer인데,
#   **Slack 경로에만 연결이 안 돼 있었다.**
#
# ★rationale은 손대지 않는다: trigger_pacing의 rationale 문자열은 retro_pacing_scorer가
#   정규식으로 파싱해 사후 채점한다(_PATTERN). 기계용 문장(rationale)과 사람용 문장(Slack)을
#   갈라 두는 것이 이 수리의 핵심이다 — 읽기 좋게 하겠다고 rationale을 고치면 채점이 죽는다.
#
# 이 모듈은 여전히 DB를 모른다(순수 함수). 이름·숫자 문장은 **harness가 만들어 넘긴다**
# (원칙18-6 — harness가 정보 유통 허브). 제안 dict의 선택 키:
#   name     : 캠페인 표시 이름(alert_humanizer.entity_names 산출). 없으면 ID 폴백.
#   headline : 이 건이 무슨 일인지 평어 제목(같은 유형이라도 저속/과속처럼 갈릴 때).
#   detail   : 숫자·조치를 담은 여러 줄 문장. 그대로 들여쓰기해 싣는다(가공하지 않는다).
# 셋 다 없으면 옛 동작과 같은 「유형 라벨 + 건수」로 떨어진다(08:00 묶음 통지의 폴백).

# Slack 본문 전용 라벨. 콘솔 라벨(프론트 PROPOSAL_TYPE_LABEL)과 목적이 다르다 — 화면은 표
# 안의 짧은 pill이라 "페이싱 경보"로 충분하지만, Slack은 맥락 없이 알림 하나만 떠서 문장으로
# 읽힌다. 미등록 유형은 원문 그대로 폴백한다(라벨이 없다고 알림이 사라지는 게 더 나쁘다).
# 드리프트는 test_slack_labels_cover_all_proposal_types가 지킨다(백엔드 ALL_PROPOSAL_TYPES 기준).
_TYPE_LABEL: dict[str, str] = {
    # 실행형 — 승인하면 광고 계정에 반영된다
    "bid_up": "입찰 인상",
    "bid_down": "입찰 인하",
    "growth_bid_up": "성장 입찰 인상",
    "bid_up_servo": "입찰 인상(노출 순위 조정)",
    "bid_up_rank": "입찰 인상(목표 순위 직행)",
    "bid_up_explore": "입찰 인상(저볼륨 탐색)",
    "bid_up_cold": "입찰 인상(신규 소재 첫 입찰)",
    "negative_keyword": "제외 키워드 추가",
    "search_term_exclude": "검색어 제외",
    "pause": "정지",
    "resume": "재개",
    "budget_up": "예산 증액",
    "budget_down": "예산 감액",
    "budget_pre_exhaustion": "예산 소진 임박",
    # 정보성 — 읽기만 한다(실행 매핑 자체가 없어 승인할 수도 없다)
    "anomaly": "이상 감지",
    "anomaly_freshness": "성과 데이터가 제때 안 들어옴",
    "account_brief": "계정 상황 브리핑",
    "trigger_pacing": "예산 소진 속도가 평소와 다름",
    "trigger_cpc_spike": "클릭 단가가 갑자기 뜀",
    "wisdom_promoted": "운영 원칙 새로 굳음",
    "search_term_promote": "키워드로 올릴 만한 검색어 발견",
    # 결정 전용 — 승인해도 자동 적용은 없다
    "param_change": "설정값 변경 제안",
}

# 정보성 유형(실행 매핑 없음). 단일 진실은 proposal_writer.INFORMATIONAL_PROPOSAL_TYPES지만
# 그걸 import하면 순환이 된다(proposal_writer → trigger_watch → slack_notifier). 그래서 값을
# 복제하고 **드리프트를 테스트가 잡는다**(test_informational_set_matches_proposal_writer).
_INFORMATIONAL_TYPES: frozenset[str] = frozenset({
    "anomaly", "anomaly_freshness", "account_brief",
    "trigger_pacing", "trigger_cpc_spike", "wisdom_promoted", "search_term_promote",
})

_MAX_TARGETS_PER_LINE = 3  # 압축 목록에 이름을 몇 개까지 나열하고 나머지는 "외 N개"로 접을지
_MAX_DETAIL_ITEMS = 5      # 상세 블록을 몇 건까지 펼칠지(그 이상은 알림이 스크롤이 된다)


def _label(proposal_type: str) -> str:
    return _TYPE_LABEL.get(proposal_type, proposal_type)


def _target_label(p: dict) -> str:
    """표시 대상 — 이름이 있으면 이름, 없으면 ID. 항상 무언가는 남는다
    (alert_humanizer.entity_names의 폴백 계약과 같은 정신)."""
    name = str(p.get("name") or "").strip()
    if name:
        return name
    return str(p.get("campaign_id") or p.get("target_id") or "대상 미상")


def _grouped(proposals: list[dict]) -> list[tuple[str, list[dict]]]:
    """(제목, 건들) 목록 — 제목은 headline이 있으면 그것, 없으면 유형 라벨.
    같은 유형이라도 headline이 갈리면(저속/과속) 따로 묶인다. 입력 순서를 보존한다
    (호출부가 이미 «중요한 것 먼저»로 정렬해 넘긴다 — 여기서 다시 정렬하지 않는다)."""
    groups: dict[str, list[dict]] = {}
    for p in proposals:
        title = str(p.get("headline") or "").strip() or _label(str(p.get("proposal_type") or ""))
        groups.setdefault(title, []).append(p)
    return list(groups.items())


def _compact_lines(groups: list[tuple[str, list[dict]]], *, title_in_header: bool = False) -> list[str]:
    """상세 없는 묶음(08:00 통지) — 「유형 N건 — 대상, 대상 외 N개」 한 줄씩.

    title_in_header면 제목을 반복하지 않는다(_detail_lines와 같은 이유 — 적대 리뷰 R1 P2-2)."""
    lines = []
    for title, items in groups:
        # ★같은 이름을 중복 나열하지 않는다(적대 리뷰 R1 P2-1): 08:00 경로의 bid_up은 키워드
        # 단위인데 라벨은 캠페인이라 한 캠페인에 여러 건이 흔하다 — 접지 않으면
        # 「01. TPU, 01. TPU, 01. TPU」가 서로 다른 세 캠페인으로 읽힌다.
        names = list(dict.fromkeys(_target_label(p) for p in items))
        shown = ", ".join(names[:_MAX_TARGETS_PER_LINE])
        remainder = len(names) - _MAX_TARGETS_PER_LINE
        if remainder > 0:
            shown = f"{shown} 외 {remainder}개"  # 쉼표로 잇지 않는다("A, B, 외 1개"는 어색하다)
        lines.append(f" • {shown}" if title_in_header else f" • {title} {len(items)}건 — {shown}")
    return lines


def _detail_lines(groups: list[tuple[str, list[dict]]], *, title_in_header: bool) -> list[str]:
    """상세 있는 묶음(매시 경보) — 건마다 「제목 — 캠페인 이름」 + detail 들여쓰기.

    title_in_header면 제목을 반복하지 않는다 — 알림 하나짜리는 제목 줄이 이미 그 문장이라
    바로 아래에 같은 말이 또 나오면 읽는 눈이 한 번 헛돈다."""
    lines = []
    for title, items in groups:
        for p in items[:_MAX_DETAIL_ITEMS]:
            lines.append(f" • {_target_label(p)}" if title_in_header else f" • {title} — {_target_label(p)}")
            detail = str(p.get("detail") or "").strip()
            for row in detail.splitlines():
                if row.strip():
                    lines.append(f"   {row.strip()}")
        remainder = len(items) - _MAX_DETAIL_ITEMS
        if remainder > 0:
            lines.append(f" • {title} 외 {remainder}건 (콘솔에서 전체 확인)")
    return lines


def _section(proposals: list[dict], *, title_in_header: bool = False) -> list[str]:
    """한 묶음을 렌더 — detail을 가진 건이 하나라도 있으면 상세 형식, 아니면 압축 형식."""
    groups = _grouped(proposals)
    fold_title = title_in_header and len(groups) == 1
    if any(str(p.get("detail") or "").strip() for p in proposals):
        return _detail_lines(groups, title_in_header=fold_title)
    return _compact_lines(groups, title_in_header=fold_title)


def _headline_text(actionable: list[dict], informational: list[dict]) -> str:
    """제목 한 줄 — 「몇 건인가」보다 「무슨 일인가」가 먼저 보이게 한다."""
    if actionable and informational:
        return (
            f"📋 네이버 SA 알림 {len(actionable) + len(informational)}건 "
            f"— 승인 대기 {len(actionable)}건 · 참고 {len(informational)}건"
        )
    if actionable:
        return f"📋 네이버 SA · 승인을 기다리는 제안 {len(actionable)}건"
    groups = _grouped(informational)
    if len(groups) == 1:
        # 알림 하나짜리가 대부분이다 — 그때는 제목 줄이 곧 「무슨 일인지」여야 한다.
        return f"👀 네이버 SA · {groups[0][0]} ({len(informational)}건)"
    return f"👀 네이버 SA · 참고 알림 {len(informational)}건"


def _build_summary(proposals: list[dict]) -> str:
    """제안 묶음 → 사람이 읽는 Slack 본문.

    구조: 제목 / (승인 대기 절) / (참고용 절) / 마무리 안내 1줄.
    상세(detail)를 넘겨준 건은 숫자까지 펼치고, 아닌 건은 「유형 N건 — 대상들」로 압축한다.
    """
    actionable = [p for p in proposals if p.get("proposal_type") not in _INFORMATIONAL_TYPES]
    informational = [p for p in proposals if p.get("proposal_type") in _INFORMATIONAL_TYPES]

    lines = [_headline_text(actionable, informational)]
    if actionable:
        lines.append("")
        if informational:
            lines.append(f"승인이 필요한 것 {len(actionable)}건")
        lines.extend(_section(actionable))
    if informational:
        lines.append("")
        if actionable:
            lines.append(f"참고용 {len(informational)}건 (조치 불필요)")
        # 정보성만 왔고 묶음이 하나면 제목 줄이 이미 그 문장이다(_headline_text 참조).
        lines.extend(_section(informational, title_in_header=not actionable))

    lines.append("")
    if actionable:
        lines.append("승인은 콘솔에서 — 네이버 광고 최적화 > 제안")
    else:
        # 정보성만 왔을 때가 바로 이번 사고("1건 생성"만 뜨고 뭘 해야 하는지 없었다)의 자리다.
        lines.append("참고용 알림입니다 — 자동으로 바뀌는 건 없습니다. 입찰·예산 조정은 08:00 정기 검토가 판단합니다.")
    return "\n".join(lines)


def _extract_slack_ts(resp: requests.Response) -> str | None:
    """incoming webhook은 대개 본문 'ok'만 반환하고 message ts를 주지 않음(실측 문서화,
    docs/references/23 §2 slack 부분 미해당이지만 동일 성격 — codex #15). JSON이 아니거나
    ts 필드가 없으면 None(정상, 완료기준 아님)."""
    try:
        data = resp.json()
    except ValueError:
        return None
    return data.get("ts") if isinstance(data, dict) else None


def _post_with_retries(url: str, text: str) -> dict:
    """공통 발송 로직(재시도·타임아웃) — notify()·notify_text()가 공유한다.
    반환: {"sent": bool, "reason": str|None, "slack_ts": str|None}."""
    last_err: str | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.post(url, json={"text": text}, timeout=_TIMEOUT_SECONDS)
        except requests.RequestException as e:
            last_err = str(e)
        else:
            if resp.status_code == 200:
                return {"sent": True, "reason": None, "slack_ts": _extract_slack_ts(resp)}
            last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
        if attempt < _MAX_RETRIES - 1:
            time.sleep(2 ** attempt)
    return {"sent": False, "reason": last_err, "slack_ts": None}


def notify(proposals: list[dict], *, webhook_url: str | None = None) -> dict:
    """제안 요약을 Slack에 발송. webhook_url 미지정 시 env(NAVER_SLACK_WEBHOOK_URL)를 읽고,
    그마저 없으면 no-op + 로그(D-NAO-21 — 미연결이 정상 상태, 예외 아님).

    반환: {"sent": bool, "reason": str|None, "slack_ts": str|None, "proposal_count": int}.
    slack_ts는 best-effort — 없어도 실패로 취급하지 않는다(codex #15).
    """
    url = webhook_url or os.getenv(_WEBHOOK_ENV)
    if not url:
        log.info("Naver 제안 Slack 미연결(%s 없음) — no-op, %d건 스킵", _WEBHOOK_ENV, len(proposals))
        return {"sent": False, "reason": "no_webhook", "slack_ts": None, "proposal_count": len(proposals)}

    if not proposals:
        return {"sent": False, "reason": "no_proposals", "slack_ts": None, "proposal_count": 0}

    text = _build_summary(proposals)
    result = _post_with_retries(url, text)
    result["proposal_count"] = len(proposals)
    if not result["sent"]:
        log.warning("Naver 제안 Slack 발송 실패(%d회 재시도): %s", _MAX_RETRIES, result["reason"])
    return result


def notify_text(text: str, *, webhook_url: str | None = None, log_label: str = "메시지") -> dict:
    """임의 텍스트(제안 요약이 아닌 메시지)를 Slack에 발송(BM 예외 브리핑 재사용, D-NAO-79).

    webhook_url 미지정 시 env(NAVER_SLACK_WEBHOOK_URL), 그마저 없으면 no-op(D-NAO-21과 동일
    관례 — 미연결이 정상 상태). 호출부가 발송 여부(예: 예외 0건은 생략)를 이미 판단했다는
    전제 — 이 함수는 "주어진 텍스트를 보낸다"만 책임진다(원칙18-1).
    반환: {"sent": bool, "reason": str|None, "slack_ts": str|None}.
    """
    url = webhook_url or os.getenv(_WEBHOOK_ENV)
    if not url:
        log.info("%s Slack 미연결(%s 없음) — no-op", log_label, _WEBHOOK_ENV)
        return {"sent": False, "reason": "no_webhook", "slack_ts": None}
    result = _post_with_retries(url, text)
    if not result["sent"]:
        log.warning("%s Slack 발송 실패(%d회 재시도): %s", log_label, _MAX_RETRIES, result["reason"])
    return result
