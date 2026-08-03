# collection_watchdog.py — 쿠팡 브라우저 수집이 "사람이 기억해야만 도는" 상태를 끝낸다.
#
# 왜 필요한가(2026-08-03 실사고 2건):
#   ① 07-28: 로켓 판매분석 51일 결손이 롤링 창(57일) 밖으로 밀리기 8시간 전에 발견 — 우연이었다.
#   ② 08-03: 4개 스트림이 6일간 멈춰 있었다. 배너는 떠 있었지만 상시 켜져 무시됐고, 배너의
#      '지금 갱신'은 링크라 눌러도 아무 일이 없었다(PR #169에서 수정).
#   뿌리는 같다 — 07-27 재설계로 자동 트리거가 전부 제거돼 수집의 유일한 트리거가 사람의 클릭이
#   됐는데, 그 클릭을 유도하는 장치가 없었다.
#
# 설계(Jino 승인 2026-08-03 "평소엔 알림만, 데이터 사라지기 직전엔 자동으로"):
#   - **평소**: 낡으면 Slack으로 찌른다. 창은 뜨지 않는다. 사람이 버튼을 누른다.
#   - **위급**: 영구 소실이 임박한 스트림만 자동으로 갱신 요청을 세운다.
#
# ★자동 구조 대상이 supplier_hub 하나뿐인 이유(2026-08-03 라이브 로그로 확정, 추정 아님):
#   | 스트림        | 소급 창 | 6일 공백 후 |
#   | ofix_ad      | 30일    | 자가 복구됨(07-04~08-02 재적재 실측) |
#   | ofix_sales   | 90일    | 자가 복구됨(days=90 push 실측) |
#   | supplier_hub | 발주·정산 90일이나 **판매분석만 7일** | ★판매분석 구멍은 안 메워짐 |
#   즉 영구 소실 위험은 **로켓 판매분석 하나**다. 나머지는 늦게 눌러도 스스로 복구한다.
#   (1차 방어로 페처 config의 sales_days를 7→30으로 넓혀 비대칭 자체를 줄였다. 이 워치독은
#    그 위의 2차 방어 — 30일 창으로도 못 메울 만큼 방치됐을 때만 발동한다.)
#
# ★쿨다운을 위한 별도 저장소가 없는 이유: 이 잡은 **하루 1회** 돈다. 스케줄 자체가 쿨다운이라
#   "같은 건으로 하루 여러 번 알림"이 구조적으로 불가능하다. 30일 창 문제에 시간당 판정은 과하다.
#
# ★Mac이 자도 괜찮은 이유: 여기서 하는 일은 prod DB에 **요청 플래그를 세우는 것뿐**이다.
#   Mac 데몬은 깨어나면 다음 폴에서 그 요청을 집어간다. 시각 기반 크론이 "자는 동안 놓치는"
#   문제(Mac은 실제로 수시로 sleep한다 — 08-03 08:40~08:43 반복 실측)가 여기엔 없다.
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# 며칠 낡으면 Slack으로 찌를지. 24h(배너 warn)보다 넉넉하게 — 배너가 이미 노랗게 뜨는 구간까지
# 알림을 보내면 알림이 배너처럼 상시화돼 다시 무시된다(오늘 배너가 당한 실패).
NOTIFY_STALE_DAYS = int(os.getenv("COUPANG_WATCHDOG_NOTIFY_DAYS", "3"))

# 자동 구조 임계. 페처 sales_days=30 창보다 **작게** 잡아 여유를 남긴다(Mac이 며칠 꺼져 있어도
# 30일 창 안에서 따라잡을 수 있도록). 21일 = 창 30일 - 9일 여유.
RESCUE_STALE_DAYS = int(os.getenv("COUPANG_WATCHDOG_RESCUE_DAYS", "21"))

# ★영구 소실 위험이 있는 스트림만. 나머지는 늦게 눌러도 스스로 복구하므로 자동 발동시키면
#   창만 뜨고 얻는 게 없다(07-27 "창 마구 안 뜨기" 결정을 값 없이 깎아먹는다).
RESCUE_STREAMS = frozenset({"supplier_hub"})

_SLACK_ENV_CANDIDATES = ("COUPANG_SLACK_WEBHOOK_URL", "NAVER_SLACK_WEBHOOK_URL")

# 스트림별 소유 계정 — 알림 문구에 싣는다. 창이 떴을 때 어느 계정으로 로그인해야 하는지
# 몰라 헤매던 문제(2026-08-03 Jino)의 해소책. 프론트 streamRefresh.ts의 값과 같아야 한다.
_ACCOUNT_BY_KEY = {
    "ofix_sales": "오픽스(A01564720)",
    "ofix_ad": "오픽스(A01564720)",
    "ohitech_ad": "오하이테크(A01029796)",
    "supplier_hub": "오하이테크(A01029796)",
}


def _now_kst() -> datetime:
    return datetime.now(KST).replace(tzinfo=None)


def decide(
    streams: list[dict],
    *,
    notify_days: int = NOTIFY_STALE_DAYS,
    rescue_days: int = RESCUE_STALE_DAYS,
    rescue_streams: frozenset[str] = RESCUE_STREAMS,
) -> dict:
    """순수 판정 — 어떤 스트림을 알리고 어떤 스트림을 자동 구조할지.

    입력 streams = collection_status(db)["streams"] 그대로.
    반환 {"notify": [ {...} ], "rescue": [ key ], "skipped_in_flight": [ key ] }

    규칙:
      - in_flight(지금 수집 중)는 건드리지 않는다 — 알림도 구조도 무의미하고, 구조 요청은
        already_pending으로 흡수되지만 알림은 헛소리가 된다.
      - failed(눌렀는데 실패)는 나이와 무관하게 **항상** 알린다. 이건 사람이 개입해야만
        풀리는 상태다(대개 로그인 필요) — 방치하면 나이가 차오를 때까지 조용하다.
      - 자동 구조는 rescue_streams ∩ (나이 >= rescue_days)에만. 그 외에는 알림만.
    """
    notify: list[dict] = []
    rescue: list[str] = []
    skipped: list[str] = []

    for st in streams or []:
        key = st.get("key") or ""
        state = st.get("state")
        age_h = st.get("age_hours")
        age_days = None if age_h is None else age_h / 24.0

        if state == "in_flight":
            skipped.append(key)
            continue

        # ★실패는 나이 무관 즉시 알림 — 사람 개입 없이는 안 풀린다.
        is_failed = state == "failed"
        is_stale = age_days is not None and age_days >= notify_days
        # 수집 기록이 아예 없는 경우(age None + failed 아님)도 알린다 — 조용한 미설정 방지.
        never = age_days is None and state in ("critical", "warn")

        if is_failed or is_stale or never:
            notify.append({
                "key": key,
                "label": st.get("label") or key,
                "account": _ACCOUNT_BY_KEY.get(key, "계정 미상"),
                "state": state,
                "age_days": None if age_days is None else round(age_days, 1),
                "reason": "갱신 실패(로그인 필요 가능)" if is_failed
                          else ("수집 기록 없음" if never else f"{age_days:.0f}일 낡음"),
                "last_error": st.get("last_error"),
            })

        if key in rescue_streams and age_days is not None and age_days >= rescue_days:
            rescue.append(key)

    return {"notify": notify, "rescue": rescue, "skipped_in_flight": skipped}


def _format_slack(notify: list[dict], rescue: list[str]) -> str:
    """사람이 읽고 **바로 행동할 수 있는** 문구. 무슨 일이 있었는지가 아니라 뭘 해야 하는지."""
    lines = ["🕒 *쿠팡 수집이 낡았습니다*"]
    for n in notify:
        mark = "❌" if n["state"] == "failed" else "⚠️"
        line = f"{mark} *{n['label']}* — {n['reason']} · {n['account']}"
        if n["state"] == "failed" and n.get("last_error"):
            line += f"\n    └ {str(n['last_error'])[:120]}"
        lines.append(line)
    if rescue:
        lines.append(
            "\n🤖 *자동 갱신을 걸었습니다* — " + ", ".join(rescue)
            + "\n    (판매분석은 롤링 창 밖으로 밀리면 영구 소멸이라 자동으로 당겼습니다."
              " Mac이 꺼져 있으면 켜질 때 실행됩니다.)"
        )
    lines.append("\n👉 sellC 상단 배너의 *지금 갱신* 또는 종합조망에서 갱신하세요.")
    return "\n".join(lines)


def _send_slack(text: str) -> dict:
    """Slack 발송. 웹훅 미설정이면 no-op(기존 naver_ad/slack_notifier와 같은 계약).

    ★전용 env를 먼저 보고 없으면 네이버 웹훅으로 폴백한다 — 채널을 나누고 싶으면
      COUPANG_SLACK_WEBHOOK_URL만 설정하면 되고, 안 나누면 지금 쓰는 채널로 간다.
    """
    from app.services.naver_ad import slack_notifier

    url = None
    for env in _SLACK_ENV_CANDIDATES:
        url = os.getenv(env)
        if url:
            break
    if not url:
        log.info("[watchdog] Slack 미연결(%s 없음) — no-op", "/".join(_SLACK_ENV_CANDIDATES))
        return {"sent": False, "reason": "no_webhook"}
    return slack_notifier.notify_text(text, webhook_url=url, log_label="쿠팡 수집 신선도")


def _request_refresh(db: Session, key: str) -> dict:
    """스트림 key → 그 스트림의 갱신 요청. 이미 pending이면 already_pending으로 흡수된다."""
    from app.services.coupang import (
        ad_cost_sync,
        ohitech_ad_sync,
        rocket_supplier_sync,
        vendor_summary_sync,
    )

    if key == "supplier_hub":
        return rocket_supplier_sync.request_rocket_refresh(db)
    if key == "ofix_ad":
        return ad_cost_sync.request_refresh(db)
    if key == "ohitech_ad":
        return ohitech_ad_sync.request_refresh(db)
    if key == "ofix_sales":
        return vendor_summary_sync.request_refresh(db)
    raise ValueError(f"알 수 없는 스트림: {key}")


def run(db: Session) -> dict:
    """하루 1회 실행 — 신선도 점검 → (알림) → (위급 시 자동 갱신 요청).

    ★알림 실패가 구조를 막지 않는다: Slack이 죽어도 자동 갱신은 걸려야 한다. 순서를
      구조 → 알림으로 두고, 알림은 예외를 삼킨다(반환값에는 남긴다).
    """
    from app.services.coupang import collection_status as _cs

    status = _cs.collection_status(db)
    plan = decide(status.get("streams") or [])

    rescued: list[dict] = []
    for key in plan["rescue"]:
        try:
            res = _request_refresh(db, key)
            rescued.append({"key": key, **{k: res.get(k) for k in ("requested", "already_pending")}})
            log.info("[watchdog] 자동 갱신 요청: %s → %s", key, res)
        except Exception as e:  # noqa: BLE001 — 한 스트림 실패가 나머지를 막지 않는다
            log.exception("[watchdog] 자동 갱신 요청 실패 %s: %s", key, e)
            rescued.append({"key": key, "error": str(e)[:200]})

    slack = {"sent": False, "reason": "nothing_to_report"}
    if plan["notify"] or plan["rescue"]:
        try:
            slack = _send_slack(_format_slack(plan["notify"], plan["rescue"]))
        except Exception as e:  # noqa: BLE001 — 알림 실패가 잡을 죽이지 않는다
            log.exception("[watchdog] Slack 발송 실패: %s", e)
            slack = {"sent": False, "reason": str(e)[:200]}

    result = {
        "checked": len(status.get("streams") or []),
        "notify": [n["key"] for n in plan["notify"]],
        "rescue": rescued,
        "skipped_in_flight": plan["skipped_in_flight"],
        "slack": slack,
        "as_of": _now_kst().isoformat(),
    }
    log.info("[watchdog] %s", result)
    return result
