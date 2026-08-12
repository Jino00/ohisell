# 이 파일은 스케줄러 워치독 Harness다 (S5b S4, 읽기 전용 — SA 정보 유통 허브, 원칙18).
# scheduler.running + 등록 누락 잡 산출 + SchedulerState 로드 + cron→interval 산출을 모아
# 순수 SA②(staleness_evaluator)에 주입하고, /api/scheduler/health가 표면화할 dict를 만든다.
# 머니로직 미접촉. DB/스케줄러 접근은 compute_scheduler_health에 한정하고, 판정 로직(build_health)과
# interval 산출(compute_interval_seconds)은 순수 함수로 분리해 단위 테스트한다(원칙22).
#
# 잡 자기보고·쿠키 상태 외에 '데이터 나이'(data_stale)도 감시한다: 잡·쿠키 보고는 거짓말할 수 있고
# (2026-07-17 사고: last_status='ok'인 채 RG 정산이 26일 침묵), 데이터 나이는 거짓말 못 한다.
# 층1(수집 경로 이관)로 쿠키 행이 사라진 뒤에도 살아남는 최후의 감시선이다.
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional, Sequence

from apscheduler.triggers.cron import CronTrigger

from app.services.scheduler_watchdog import (
    DISK_WARN_PERCENT,
    STATE_DISABLED,
    STATE_FAILED,
    STATE_NEVER_SUCCEEDED,
    STATE_STALE,
    evaluate_cookie_freshness,
    evaluate_data_freshness,
    evaluate_disk_space,
    evaluate_staleness,
)

log = logging.getLogger(__name__)

# 워치독 대상 allowlist (계획 §3, Jino 승인 critical-only). 서버측 필수 잡만.
# 제외(fail-soft/Mac·쿠키 의존): sync_coupang_rg_inbound, sync_coupang_rg_settlement,
#   auto_download_rg_settlement, sync_coupang_ad_cost, cafe24_token_refresh.
WATCHDOG_JOBS: tuple[str, ...] = (
    # ★워치독 자신도 감시 대상이다(2026-08-03 codex 1R[P2]): 이 잡이 죽으면 "수집이
    #   멈춘 걸 알려주는 장치"가 조용히 사라진다 — 감시자가 없는 감시자는 없느니만 못하다.
    #   Mac/쿠키에 의존하지 않고(플래그 쓰기 + Slack뿐) 서버 안에서 완결되므로
    #   fail-soft 제외 사유에 해당하지 않는다.
    "coupang_collection_watchdog",
    "auto_sync_orders",
    "auto_profit_calc",
    "sync_naver_settlement",
    "sync_naver_case_settlement",
    "sync_naver_sa_ad_costs",
    # ★ADVoost·GFA 광고비(2026-08-03 추가). 이 축의 종전 경로는 사람이 CSV를 올리는 것이었고
    #   06-04에 멈춘 뒤 **59일간 488만원이 조용히 누락**됐다 — 감시가 없으면 자동화해도
    #   같은 방식으로 다시 침묵한다. 서버 안에서 완결(SA API)되므로 fail-soft 제외 사유 없음.
    "sync_naver_display_ad_costs",
    "sync_naver_ad_daily",
    "sync_meta_ad_costs",
    "sync_coupang_products",
    "sync_coupang_rg_sizes",
    "sync_coupang_rg_inventory",
    "sync_coupang_returns",
    "sync_coupang_settlement",
    "sync_coupang_rg_orders",
    "sync_coupang_coupons",
    "sync_coupang_cs",
    # 판매분석 갱신 요청 트리거(D-CPP-36). ★이 잡이 죽으면 데이터 나이 감시가 «정체»로 울리기까지
    #   최소 3일이 걸린다 — 원인(요청이 안 걸림)을 바로 보려면 잡 자체도 감시해야 한다.
    #   첫 주기는 never_succeeded 유예에 걸려 조용하다(등록 직후 헛알림 없음).
    "request_wing_vendor_summary_daily",
    # 오픽스 광고비 갱신 요청 트리거(D-CPP-45). ★위 판매분석 트리거와 **같은 이유로** 여기 있다:
    #   광고비 데이터 나이 규칙도 `max_age_days=3.0`이라, 이 잡이 죽으면 원인(요청이 안 걸림)이
    #   보이기까지 마찬가지로 3일이 걸린다. 그리고 이 잡을 만든 동기 자체가 «사람이 안 누른다»인데
    #   그 대체물을 감시 없이 두면 실패 모드가 «사람이 안 누른다»에서 «크론이 조용히 죽는다»로
    #   옮겨갈 뿐이다 — 자동화가 감시를 대체하지 않는다(잡 docstring과 같은 규율).
    "request_coupang_ad_cost_daily",
)

# 에러 요약 최대 길이 — sanitized 한 줄(전체 traceback은 DB에만, codex #12 누출 방지).
_ERR_SUMMARY_MAX = 200

# ── 데이터 나이 감시 규칙(선언적) ─────────────────────────────────────────
# 왜 잡·쿠키 감시가 있는데 또 데이터 나이인가: 잡·쿠키의 자기보고는 거짓말할 수 있다
# (2026-07-17 사고: RG 정산 수집이 쿠키 만료로 26일 조용히 죽었는데 last_status='ok'로 green-while-dead).
# 데이터 나이는 거짓말 못 한다 — '가장 최신 row가 며칠 전 것인가'가 파이프라인 생존의 직접 증거다.
# 층1(수집 경로를 정적 쿠키 → Mac 상주 브라우저로 이관) 후 쿠키 행이 사라져도 이 감시는 살아남는다.
#
# max_age_days=14 근거: RG 정산은 주별(월~일) + ~2일 랙으로 인식된다 → 정상이면 최신 계정 row의
# recognition_date_to가 9~16일 이내에 들어온다. 14일 = 한 주를 통째로 놓친 것 + 여유(헛알림 방지).
#
# ★`source`는 2026-08-10(D-CPP-36)에 추가됐다. 그전엔 아래 조회가 **RG 정산 테이블 한 곳에
#   하드코딩**돼 있어서, 규칙을 늘릴 수단이 아예 없었다. 그 결과 쿠팡 Wing 판매분석(3P 정본
#   매출 축)이 **13일 동안 멈췄는데 `healthy: true`**였고 `refresh-status`도 `green`이었다
#   (07-26까지만 적재 → 08-10 발견). 「감시선이 셋 있다」는 말이 「이 파이프라인을 본다」는
#   뜻이 아니다 — 판정 목록에 없으면 배너는 아무 말도 하지 않는다.
DATA_FRESHNESS_RULES: tuple[dict, ...] = (
    {"source": "rg_settlement", "name": "rg_settlement_account_rows",
     "account_key": "COUPANG_WING1", "max_age_days": 14.0,
     "impact": "RG 정산비용(오픽스)이 net_profit에서 누락 중"},
    {"source": "rg_settlement", "name": "rg_settlement_account_rows",
     "account_key": "COUPANG_WING2", "max_age_days": 14.0,
     "impact": "RG 정산비용(오하이테크)이 net_profit에서 누락 중"},
    # ── 쿠팡 Wing 판매분석 (D-CPP-36, 2026-08-10 신설) ──
    # max_age_days=3.0 근거: 두 축 모두 «닫힌 과거일»만 받으므로 수집 직후에도 최신 날짜는
    #   어제다(age ≈ 1.2일). 일일 트리거(request_wing_vendor_summary_daily, 05:20 KST)가
    #   정상이면 age는 다음 회차 직전에 최대 ~2.25일에 이른다. 3.0이면 **한 회차를 통째로
    #   놓쳤을 때 울리고**(2.25 < 3.0 < 3.25) 정상 운영에선 조용하다.
    # ★WING1도 편입한다 (D-CPP-40, 2026-08-12). 종전 주석은 «WING1은 일부러 제외 — 오픽스 3P는
    #   실적이 거의 없고(90일 3P 옵션 3개) 일일 트리거 대상도 아니다»였다. 그 전제 두 개가 모두
    #   바뀌었다: ①「3P가 비었다」는 참이지만 같은 판매분석이 싣는 **RFM(RG)축이 63일 3,931만원**
    #   으로 이 계정의 지배 축이다(오픽스 매출의 98.9%가 RG) ②WING1이 이제 일일 트리거 대상이다
    #   (request_wing_vendor_summary_daily 튜플에 편입).
    # ★「영구 빨강」 우려가 여기선 성립하지 않는다 — 수집 경로가 살아 있음을 실측했다:
    #   데몬 `com.ohisell.wing` 상주 · config에 vs_days/vi_days 없음(기본 45/7 적용) ·
    #   요약축 126행 06-07~08-08 연속. 즉 울리면 그건 «고칠 수 있는 정체»이지 «구조적 부재»가 아니다.
    #   (옵션축은 신설 후 WING1 0행이었는데, 그 원인도 부재가 아니라 트리거 누락이었다.)
    {"source": "vendor_summary", "name": "wing_vendor_summary", "account_key": "COUPANG_WING1",
     "max_age_days": 3.0,
     "impact": "쿠팡 판매분석 요약축(오픽스) 정체 — RG 매출(계정의 98.9%) 대조 상대가 낡았다"},
    {"source": "vendor_item_sales", "name": "wing_vendor_item_sales", "account_key": "COUPANG_WING1",
     "max_age_days": 3.0,
     "impact": "쿠팡 판매분석 옵션축(오픽스) 정체 — 옵션별 RG 매출이 낡은 값으로 구른다"},
    {"source": "vendor_summary", "name": "wing_vendor_summary", "account_key": "COUPANG_WING2",
     "max_age_days": 3.0,
     "impact": "쿠팡 판매분석 요약축(오하이테크) 정체 — 3P 매출 대조 상대가 낡았다"},
    {"source": "vendor_item_sales", "name": "wing_vendor_item_sales", "account_key": "COUPANG_WING2",
     "max_age_days": 3.0,
     "impact": "쿠팡 판매분석 옵션축(오하이테크) 정체 — 옵션별 3P 매출이 낡은 값으로 구른다"},
    # ★아래 두 규칙에서 `account_key`는 **표시용**이다(배너 문구·verdict 라벨) — 쿼리 필터는
    #   vendor_id로 건다. 위 네 규칙은 account_key로 **실제 필터**를 걸므로 같은 필드가 규칙에
    #   따라 다른 역할을 한다. 그래도 vendor_id를 새로 하드코딩하지 않고 기존 상수를 재사용하는
    #   쪽을 택했다 — 진실이 두 곳에 생기는 비용이 더 크기 때문이다(적대 리뷰 P2 채택).
    # ── 쿠팡 광고비 (2026-08-11 신설) ──
    # max_age_days=3.0 근거(라이브 실측): report/SALES 축(vendor_id=ADV_SALES)은 최근 21일 중
    #   20일이 적재된 «일별» 소스다. 정상이면 최신 cost_date는 어제(age≈1일)이고, 한 회차를
    #   놓치면 2일이 된다. 3.0이면 **두 회차 연속 실패에서 울리고** 정상 운영에선 조용하다
    #   (판매분석 규칙 3.0과 같은 논리).
    # ★광고센터 광고주로 잡히는 계정은 WING1뿐이다(D-CPP-38) — account_key는 그래서 WING1이다.
    {"source": "ad_cost", "name": "coupang_ad_cost_sales", "account_key": "COUPANG_WING1",
     "max_age_days": 3.0,
     "impact": "쿠팡 광고비(report/SALES) 정체 — net_profit 차감 축이 낡은 값으로 구른다"},
    # ── 로켓1P 정산 (2026-08-11 신설) ──
    # max_age_days=10.0 근거(라이브 실측): 최근 90일 issue_date 간격 분포는 1일 47회 · 2일 7회 ·
    #   3일 4회 · **7일 2회**로 관측 최대가 7일이다. 7.0으로 잡으면 정상 운영에서 두 번 울려
    #   알림 피로가 생기고, 그건 이 감시선 계열이 실제로 죽는 방식이다(WING1을 판매분석에서
    #   제외한 것과 같은 이유). 10.0 = 관측 최대 7 + 여유 3.
    {"source": "rocket_settlement", "name": "rocket_1p_settlement", "account_key": "COUPANG_WING2",
     "max_age_days": 10.0,
     "impact": "로켓1P 정산(세금계산서) 정체 — 1P 매출 정본이 낡았다"},
)

# 쿠키 freshness 감시 대상 — 돈에 직결되는 fail-soft 잡의 쿠키만(codex P2: 전체 감시 시 폐기/회전
# 쿠키가 영구 stale 노이즈). ADS1=쿠팡 광고비(net_profit).
# ★WING1/WING2 제거(2026-07-17 층1 라이브 합격): RG 계정 수수료는 Mac 상주 브라우저 push로
#   이관돼(ingest-status) 서버 쿠키 없이 흐른다 — 실측: 백필 98행·data_stale WING1 소멸.
#   쿠키 경보를 유지하면 영구 노이즈(정확히 이 계열 사고의 알림 피로 원인). 이 파이프라인의
#   감시는 DATA_FRESHNESS_RULES(데이터 나이 — 거짓말 불가)가 전담한다.
WATCHDOG_COOKIES: tuple[str, ...] = (
    "COUPANG_ADS1",
    # 1P 로켓 광고비(net_profit)·데일리 주기. 워치독 밖이라 만료가 조용히 묻혀 광고비가
    # 끊긴 사고 재발 방지 — prod 행 존재 실측(07-17).
    "COUPANG_OHITECH_AD",
)


def compute_interval_seconds(cron_expression: str) -> float:
    """cron 식의 기대 주기(초)를 CronTrigger 2회 발화 diff로 산출한다(신규 의존 0, 순수).

    파싱 실패/발화 불가 시 0.0 반환 → evaluator가 해당 잡을 stale/never_succeeded로 판정하지 않음
    (fail-safe: 주기 불명이면 헛알림보다 침묵, 계획 §9 failure-mode).

    ★가정(codex S4 [P2] #2): WATCHDOG_JOBS는 전부 '매일 고정시각' 또는 'N시간마다'(균등 주기)이고
    타임존 Asia/Seoul은 DST가 없으므로 첫 2회 발화 diff = 정확한 주기다. 만약 allowlist에 요일限定
    (예: 평일만)·월간·day-of-month 같은 불규칙 cron을 추가하면 이 산출이 '최소 gap'을 잡아 stale을
    과탐할 수 있다(누락은 아님) → 그때는 연속 N회 발화의 최대 gap을 쓰도록 재검토할 것.
    """
    try:
        trig = CronTrigger.from_crontab(cron_expression, timezone="Asia/Seoul")
    except Exception:
        log.warning("[워치독] cron 파싱 실패 — interval 0 처리: %r", cron_expression)
        return 0.0

    try:
        tz = getattr(trig, "timezone", None)
        base = datetime(2026, 1, 1, 0, 0, 0)
        if tz is not None and hasattr(tz, "localize"):  # pytz
            ref = tz.localize(base)
        elif tz is not None:  # zoneinfo/tzinfo
            ref = base.replace(tzinfo=tz)
        else:
            ref = base

        first = trig.get_next_fire_time(None, ref)
        if first is None:
            return 0.0
        # 첫 발화 직후를 now로 주면 그 다음 발화를 얻는다(같은 시각 재반환 회피).
        second = trig.get_next_fire_time(first, first + timedelta(microseconds=1))
        if second is None:
            return 0.0
        return (second - first).total_seconds()
    except Exception:
        log.warning("[워치독] interval 산출 실패 — 0 처리: %r", cron_expression)
        return 0.0


def _sanitize_error(last_error: Optional[str]) -> Optional[str]:
    """저장된 traceback에서 마지막 줄(예외 클래스+메시지)만 추출한다(누출 방지, codex #12).

    전체 traceback은 DB(last_error)에만 남기고, API 응답엔 한 줄 요약만 노출한다.
    """
    if not last_error:
        return None
    lines = [ln for ln in last_error.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    return lines[-1].strip()[:_ERR_SUMMARY_MAX]


def disk_target_path(database_url: str) -> str:
    """감시할 파일시스템 경로를 DATABASE_URL에서 유도한다(순수 — I/O 없음).

    sqlite면 DB 파일이 놓인 디렉터리를 본다. 백업·WAL·로그가 전부 그 파일시스템에서 경쟁하므로
    거기가 포화의 현장이다. sqlite가 아니면(Postgres 등) DB는 원격이므로 로컬 루트를 본다.
    경로가 비면 "/"로 떨어진다 — 판정 불가보다 루트라도 보는 게 낫다.
    """
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return "/"
    raw = database_url[len(prefix):]
    if raw.startswith("file:"):  # sqlite:///file:...?mode=ro 형태(읽기전용 첨부 DB)
        raw = raw[len("file:"):].split("?", 1)[0]
    return os.path.dirname(os.path.abspath(raw)) or "/"


def build_health(
    watched_jobs: Sequence[str],
    states: Iterable[Any],
    registered_job_names: set[str],
    scheduler_running: bool,
    now: datetime,
    cookies: Iterable[Any] = (),
    data_snapshots: Iterable[dict] = (),
    disk_snapshots: Iterable[dict] = (),
    cost_drift: dict | None = None,
    vendor_item_conservation: dict | None = None,
    exclusion_survival: dict | None = None,
) -> dict:
    """워치독 판정 코어(순수: DB/스케줄러 미접촉 — 인자로 받은 스냅샷만 사용).

    states: SchedulerState 유사 객체(.job_name/.is_enabled/.cron_expression/.last_run_at/
            .last_status/.last_status_at/.created_at/.last_error). registered_job_names:
            현재 APScheduler에 등록된 잡 id 집합. cookies: CoupangWingCookie 유사 객체
            (.account_key/.status/.last_success_at) — fail-soft 잡의 쿠키 만료를 직접 감시.
            data_snapshots: 데이터 나이 스냅샷 dict 목록(name/account_key/latest/max_age_days/
            impact) — 잡·쿠키 보고와 무관하게 '최신 데이터가 며칠 전인가'를 직접 본다(기본 () 하위호환).
            cost_drift: 원가 정본 드리프트 요약 dict 또는 None(기본 None 하위호환).
            반환 dict는 그대로 API 응답이 된다.

    ★cost_drift는 다른 넷과 **종류가 다르다**: 잡·쿠키·데이터나이·디스크는 «파이프라인이
      살아 있나»를 보지만 이건 «흘러온 값이 맞나»를 본다. 그런데 표면은 하나가 낫다 —
      2026-08-10까지 원가 버퍼 177건은 아무 배너에도 안 뜬 채 이익을 과소 계상시켰고,
      «검사기는 있는데 아무도 안 부른다»가 그 상태의 이름이었다(ref 54 §7-6).
    """
    by_name = {getattr(s, "job_name", None): s for s in states}

    missing_jobs: list[str] = []
    snapshots: list[dict] = []
    err_by_name: dict[str, Optional[str]] = {}

    for name in watched_jobs:
        state = by_name.get(name)
        if state is None:
            # allowlist에 있으나 DB row 부재 → 등록·기록 자체가 없는 1급 결손.
            missing_jobs.append(name)
            continue

        is_enabled = bool(getattr(state, "is_enabled", True))
        # 스케줄러가 돌고 잡이 enabled인데 APScheduler 미등록 → start_scheduler 실패 등 1급 신호(codex #4).
        if scheduler_running and is_enabled and name not in registered_job_names:
            missing_jobs.append(name)

        err_by_name[name] = getattr(state, "last_error", None)
        snapshots.append(
            {
                "job_name": name,
                "is_enabled": is_enabled,
                "expected_interval_sec": compute_interval_seconds(
                    getattr(state, "cron_expression", "") or ""
                ),
                "last_run_at": getattr(state, "last_run_at", None),
                "last_status": getattr(state, "last_status", None),
                "last_status_at": getattr(state, "last_status_at", None),
                "created_at": getattr(state, "created_at", None),
            }
        )

    verdicts = evaluate_staleness(snapshots, now)

    failed: list[dict] = []
    stale: list[dict] = []
    never_succeeded: list[dict] = []
    disabled: list[dict] = []
    for v in verdicts:
        state_name = v["state"]
        if state_name == STATE_FAILED:
            entry = dict(v)
            entry["error_summary"] = _sanitize_error(err_by_name.get(v["job_name"]))
            failed.append(entry)
        elif state_name == STATE_STALE:
            stale.append(dict(v))
        elif state_name == STATE_NEVER_SUCCEEDED:
            never_succeeded.append(dict(v))
        elif state_name == STATE_DISABLED:
            disabled.append(dict(v))

    # 쿠키 freshness — fail-soft 잡(RG 정산·광고)이 쿠키 만료로 조용히 멈춘 걸 직접 잡는다.
    cookie_snaps = [
        {
            "account_key": getattr(c, "account_key", "?"),
            "status": getattr(c, "status", None),
            "last_success_at": getattr(c, "last_success_at", None),
        }
        for c in cookies
    ]
    cookies_stale = evaluate_cookie_freshness(cookie_snaps, now)

    # 데이터 나이 — 잡·쿠키 보고가 거짓말해도(2026-07-17 사고) 최신 데이터 나이는 거짓말 못 한다.
    data_stale = evaluate_data_freshness(list(data_snapshots), now)

    # 디스크 여유 — 위 셋이 전부 '이미 죽은 뒤'를 보는 사후 지표인 반면 이건 유일한 사전 신호다
    # (2026-08-03 ENOSPC 사고: 디스크 포화가 서버를 3시간 40분 마비시켜 자동수집 12개 유실).
    disk_low = evaluate_disk_space(list(disk_snapshots))

    # 판매분석 보존식 어긋남 — 나이가 아니라 **값**을 본다(D-CPP-36).
    # 신선도 감시와 종류가 다르다: 옵션축이 제때 와도 요약축과 합이 안 맞으면 그건 두 축이
    # 서로 다른 것을 세고 있다는 뜻이고, 그 상태로 옵션별 손익을 쓰면 조용히 틀린다.
    # `summary_only`(옵션 수집이 아직 안 온 날)는 여기서 healthy를 깨지 않는다 — 그건 «없음»이고
    # 신선도 규칙이 이미 본다. 둘을 한 신호로 뭉치면 «아직»이 «틀렸다»로 보인다(교훈 #123).
    conservation_mismatch = list((vendor_item_conservation or {}).get("mismatch") or [])

    # 조치 생존 — «우리가 건 제외가 아직 걸려 있나»(D-NAO-173 P1-①). 위 어느 감시와도 종류가
    # 다르다: 파이프라인도 값도 아니고 **우리가 바깥 세계에 한 조치**를 본다. 대행사가 우리
    # 조치를 되돌린 2회 중 1회는 change_log에 흔적조차 없었다 — 사건 로그로는 못 잡는다.
    # ★어긋남뿐 아니라 «대조가 멈춤»(stale)·«한 번도 대조 안 함»도 이상으로 센다. SA가 그 셋을
    #   healthy 한 칸으로 합쳐 주므로 빌더는 그 결론만 읽는다(판정 규칙이 두 벌이 되지 않게).
    exclusion_survival_bad = bool(exclusion_survival) and not exclusion_survival.get("healthy", True)

    # disabled는 정상(노이즈 제외) — healthy 판정에서 무시. 그 외 어떤 비정상이라도 healthy=False.
    healthy = (
        scheduler_running
        and not missing_jobs
        and not failed
        and not stale
        and not never_succeeded
        and not cookies_stale
        and not data_stale
        and not disk_low
        # ★드리프트가 있으면 healthy=False다. 「값이 틀렸다」도 파이프라인 고장으로 센다 —
        #   조용히 이익이 줄어드는 쪽이 잡 하나 죽는 것보다 늦게 발견된다(177건 × 여러 달).
        and not cost_drift
        # ★보존식 어긋남도 cost_drift와 같은 종류다 — 파이프라인은 살아 있는데 «값»이 틀렸다.
        and not conservation_mismatch
        # ★조치 생존 — 파이프라인도 값도 정상인데 **우리가 한 조치만** 사라질 수 있다.
        and not exclusion_survival_bad
    )

    return {
        "healthy": healthy,
        "scheduler_running": scheduler_running,
        "missing_jobs": missing_jobs,
        "failed": failed,
        "stale": stale,
        "never_succeeded": never_succeeded,
        "disabled": disabled,
        "cookies_stale": cookies_stale,
        "data_stale": data_stale,
        "disk_low": disk_low,
        # 드리프트가 없으면 None(키는 항상 있다 — 없는 키와 0건을 프론트가 구분 못 하면
        # «판정 안 함»이 «이상 없음»으로 읽힌다).
        "cost_drift": cost_drift,
        # 판매분석 두 축의 보존식 대조. 어긋남이 없으면 mismatch=[]이고, 대조 자체를 못 했으면
        # None이다(키는 항상 있다 — cost_drift와 같은 이유: 없는 키와 0건이 같아 보이면
        # «판정 안 함»이 «이상 없음»으로 읽힌다).
        "vendor_item_conservation": vendor_item_conservation,
        # 조치 생존 대조 결과. 대조 자체를 못 했으면 None이다(위 둘과 같은 이유 — 없는 키와
        # 0건이 같아 보이면 «판정 안 함»이 «이상 없음»으로 읽힌다).
        "exclusion_survival": exclusion_survival,
        "as_of": now.isoformat(),
    }


def _latest_for_rule(db, rule: dict):
    """규칙 하나의 «최신 데이터 시각»을 그 규칙이 가리키는 소스에서 읽는다(I/O, D-CPP-36).

    ★`source` 분기가 없던 시절엔 이 조회가 RG 정산 테이블에 하드코딩돼 있었다 → 규칙을 늘릴
      수단이 없어 판매분석이 13일 정체를 조용히 통과했다. 새 파이프라인을 감시에 넣는 비용이
      «분기 한 줄»이어야 실제로 넣게 된다.
    ★모르는 source는 None을 돌려주지 않고 **예외를 낸다** — None은 `no_data`(즉시 비정상)로
      해석되므로, 오타 하나가 «한 번도 수집 안 됨»이라는 거짓 경보로 나타난다. 호출부의
      try/except가 데이터 감시만 강등하고 로그를 남긴다.
    """
    # ★`func`는 여기서 임포트한다 — 호출부(compute_scheduler_health)의 지연 임포트에 의존하면
    #   `NameError`가 나고, 그건 호출부 try/except가 삼켜 **data_stale 감시가 통째로 사라진다**
    #   (실제로 이 커밋 작성 중 그 상태였다. 규칙 존재를 단언하는 테스트만이 그걸 잡았다).
    from sqlalchemy import func  # noqa: PLC0415 — 지연 임포트(순수 코어 테스트용)

    source = rule.get("source", "rg_settlement")
    if source == "rg_settlement":
        from app.models import CoupangRgSettlementFee  # noqa: PLC0415

        # RG 정산은 계정 row(vendor_item_id='' sentinel)의 recognition_date_to가 진도 지표다.
        return (
            db.query(func.max(CoupangRgSettlementFee.recognition_date_to))
            .filter(
                CoupangRgSettlementFee.account_key == rule["account_key"],
                CoupangRgSettlementFee.vendor_item_id == "",
            )
            .scalar()
        )
    if source == "vendor_summary":
        from app.models import CoupangVendorSummaryDaily  # noqa: PLC0415 — 지연 임포트(순수 코어 테스트용)

        return (
            db.query(func.max(CoupangVendorSummaryDaily.summary_date))
            .filter(CoupangVendorSummaryDaily.account_key == rule["account_key"])
            .scalar()
        )
    if source == "vendor_item_sales":
        from app.models import CoupangVendorItemSalesDaily  # noqa: PLC0415

        return (
            db.query(func.max(CoupangVendorItemSalesDaily.sale_date))
            .filter(CoupangVendorItemSalesDaily.account_key == rule["account_key"])
            .scalar()
        )
    if source == "ad_cost":
        from app.models import CoupangAdCostDaily  # noqa: PLC0415
        from app.services.coupang.ad_cost_sync import _SALES_KEY  # noqa: PLC0415

        # report/SALES 축이 일별 확정 정본이다(vendor별 축은 성격이 달라 대상이 아니다).
        return (
            db.query(func.max(CoupangAdCostDaily.cost_date))
            .filter(CoupangAdCostDaily.vendor_id == _SALES_KEY)
            .scalar()
        )
    if source == "rocket_settlement":
        from app.models import CoupangRocketSettlement  # noqa: PLC0415
        from app.services.coupang.rocket_1p_channel_pnl import ROCKET_1P_VENDOR_ID  # noqa: PLC0415

        return (
            db.query(func.max(CoupangRocketSettlement.issue_date))
            .filter(CoupangRocketSettlement.vendor_id == ROCKET_1P_VENDOR_ID)
            .scalar()
        )
    raise ValueError(f"알 수 없는 data freshness source: {source!r}")


# 보존식 대조 창 — ★**페처의 옵션축 수집창과 같아야 한다**(`_VI_DEFAULT_DAYS = 7`).
#
# 적대 리뷰 P1-3(2026-08-10)이 잡은 것: 종전 14일은 수집창 7일보다 넓어서, 8~14일 전 구간의
# 불일치를 **어떤 회차도 고칠 수 없었다**. 페처는 최근 7일만 다시 받고 요약축은 45일을 매 회차
# «확정치로 교체»하므로, 9일 전 요약축 값이 갱신되면 옵션축은 영원히 따라가지 못한다.
# 그 결과가 «healthy=false + 「옵션별 3P 손익을 신뢰할 수 없음」 배너가 최대 7~14일 켜져 있고
# 운영자가 할 수 있는 조치는 없다» — 내가 WING1을 제외한 근거(영구 빨강 = 알림 피로가 이
# 감시선 계열이 실제로 죽는 방식)에 정면으로 걸린다.
#
# ★검사창을 넓히려면 **페처 `vi_days`를 같이 넓혀야 한다.** 서버는 Mac config를 볼 수 없으므로
#   이 상수가 그 계약의 유일한 기록이다. 한쪽만 늘리면 위 실패가 그대로 돌아온다.
CONSERVATION_WINDOW_DAYS = 7
# 보존식을 볼 계정 — 옵션축을 **실제로 받고 있는** 계정만.
#
# ★WING1 편입 완료 (D-CPP-44, 2026-08-12). D-CPP-40이 WING1을 일일 트리거에 넣었지만
#   그때는 `coupang_vendor_item_sales_daily`의 WING1 행이 0건이라 넣으면 대조할 것이 없는
#   상태에서 `summary_only`만 매일 쌓였다 — 그래서 **조건부 보류**였다.
#   2026-08-12 08:44 Jino가 쿠팡 Wing에 로그인하자 옵션축이 처음 적재됐고, 편입 조건이
#   충족됐다. 편입 직전 prod 실측(창 08-05~08-12):
#       compared 13칸 · mismatch 0 · summary_only 0 · option_only 0
#   즉 편입해도 노이즈가 0이다 — 보류 사유가 사라진 것을 **관측으로** 확인하고 넣었다.
# ★같이 확인한 계약: `CONSERVATION_WINDOW_DAYS`(7) == Mac 페처 `vi_days`.
#   `~/.ohisell_wing_fetcher.json`(WING1)에 `vi_days` 키가 없어 기본 7이 적용된다(실측:
#   적재된 WING1 행이 08-05~08-11 정확히 7일). 한쪽만 넓히면 영구 빨강이 된다(위 주석 참조).
CONSERVATION_ACCOUNTS: tuple[str, ...] = ("COUPANG_WING1", "COUPANG_WING2")


def compute_scheduler_health(db, scheduler, now: datetime) -> dict:
    """Harness 진입점(I/O 경계): DB에서 SchedulerState 로드 + 스케줄러 등록 잡 조회 후 build_health.

    읽기 전용. 머니로직 미접촉. 라우터(GET /api/scheduler/health)가 호출한다.
    """
    # 지연 임포트(순수 코어를 app 의존 없이 테스트하기 위함)
    from sqlalchemy import func

    from app.models import CoupangRgSettlementFee, CoupangWingCookie, SchedulerState

    running = bool(getattr(scheduler, "running", False))
    registered: set[str] = set()
    if running:
        try:
            registered = {j.id for j in scheduler.get_jobs()}
        except Exception:
            log.exception("[워치독] get_jobs 조회 실패 — 등록 목록 빈 집합 처리")

    states = (
        db.query(SchedulerState)
        .filter(SchedulerState.job_name.in_(WATCHDOG_JOBS))
        .all()
    )
    cookies = (
        db.query(CoupangWingCookie)
        .filter(CoupangWingCookie.account_key.in_(WATCHDOG_COOKIES))
        .all()
    )

    # 데이터 나이 스냅샷: 규칙별로 계정 row(vendor_item_id='' sentinel)의 최신 recognition_date_to를
    # 조회한다. 계정 row가 하나도 없으면 max→None → SA가 no_data(즉시 비정상)로 판정.
    # ★try/except(적대적 리뷰 P2): 이 쿼리는 이번에 추가된 유일한 새 raise 경로 — 실패해도 헬스
    #   API 전체(잡·쿠키 감시)를 죽이면 안 된다(워치독 침묵 = 이 스프린트가 막으려는 바로 그 실패).
    #   실패 시 데이터 감시만 구버전 동작으로 강등(로그만 남김).
    # ★try/except가 **규칙 안**에 있다(적대 리뷰 P1-2). 루프 전체를 감싸면 규칙 하나의 실패가
    #   나머지 전부를 지우고(`data_snapshots = []`) 그게 «이상 없음»과 구별되지 않는다 —
    #   이 PR이 감시 대상 테이블을 1개→3개로 늘렸으므로 그 위험이 세 배가 됐다.
    #   실패한 규칙은 `error`를 실어 보내 evaluator가 `check_failed`로 표면화한다.
    data_snapshots: list[dict] = []
    for rule in DATA_FRESHNESS_RULES:
        snap = {
            "name": rule["name"],
            "account_key": rule["account_key"],
            "latest": None,
            "max_age_days": rule["max_age_days"],
            "impact": rule["impact"],
        }
        try:
            snap["latest"] = _latest_for_rule(db, rule)
        except Exception as e:  # noqa: BLE001 — 규칙 하나가 나머지 감시를 죽이지 않는다
            log.exception("[워치독] 데이터 나이 조회 실패(%s/%s) — 이 규칙만 check_failed",
                          rule["name"], rule["account_key"])
            snap["error"] = f"{type(e).__name__}: {e}"
        data_snapshots.append(snap)

    # 디스크 여유 — DB가 사는 파일시스템을 본다(백업·WAL·로그가 전부 여기서 경쟁한다).
    # ★try/except: 데이터 나이 쿼리와 같은 이유 — 이 조회가 실패해도 헬스 API 전체를 죽이면 안 된다
    #   (워치독 침묵 = 이 계열 사고가 막으려는 바로 그 실패). 실패 시 디스크 감시만 생략한다.
    disk_snapshots: list[dict] = []
    try:
        import shutil

        from app.database import DATABASE_URL  # noqa: PLC0415 — 지연 임포트(순수 코어 테스트용)

        target = disk_target_path(DATABASE_URL)
        usage = shutil.disk_usage(target)
        disk_snapshots.append({
            "path": target,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "warn_percent": DISK_WARN_PERCENT,
            "impact": "디스크 포화 시 전 수집 잡이 조용히 멈춘다(2026-08-03 ENOSPC 사고)",
        })
    except Exception:
        log.exception("[워치독] 디스크 조회 실패 — disk_low 감시만 생략(헬스 API는 유지)")
        disk_snapshots = []

    # 원가 정본 드리프트 — `product_master.cost_price`가 «정본 + 알려진 버퍼»인가.
    # ★2026-08-10 이전엔 이 판정이 CLI(`scripts/audit_cost_buffer.py`)에만 있어 **아무도
    #   안 불렀다.** 옛 매핑 엑셀을 올리면 177건이 조용히 되돌아오고 이익만 줄어든다
    #   — 에러가 안 나므로 다른 어떤 감시에도 안 걸린다(ref 54 §7-6).
    # ★try/except: 위 둘과 같은 이유. 스냅샷 파일이 없거나 스키마가 달라도 헬스 API 전체를
    #   죽이면 안 된다. 실패 시 이 감시만 생략한다(= None = 배너 침묵).
    #   ⚠️그래서 «스냅샷이 없어서 못 봤다»와 «봤는데 0건»이 겉으로 같다 — 로그로만 갈린다.
    cost_drift: dict | None = None
    try:
        from app.models import ProductMaster
        from app.services import cost_truth_audit as _cta

        truth = _cta.load_truth()
        rows = (
            db.query(
                ProductMaster.internal_sku, ProductMaster.product_name, ProductMaster.cost_price
            )
            # ★이 필터는 지금 **도달 불가**다 — `cost_price`가 모델·prod DDL 둘 다 NOT NULL이고
            #   prod NULL 행은 0건이다(2026-08-10 실측). 그래서 «지워도 테스트가 안 죽는다»
            #   (적대 리뷰 변이 M04). 그럼에도 남기는 이유: nullable로 바뀌는 날
            #   `float(None)`이 터지고 아래 fail-soft가 그걸 삼켜 **감시가 조용히 꺼진다.**
            #   전제가 바뀌면 test_cost_price_is_not_nullable_so_the_null_filter_is_a_dormant_guard
            #   가 울린다 — 도달 불가 코드를 «검증했다»고 말하지 않기 위한 짝이다.
            .filter(ProductMaster.cost_price.isnot(None))
            .all()
        )
        cost_drift = _cta.summarize_drift(_cta.classify_rows(rows, truth), truth)
    except Exception:
        log.exception("[워치독] 원가 정본 대조 실패 — cost_drift 감시만 생략(헬스 API는 유지)")
        cost_drift = None

    # 판매분석 보존식 — Σ옵션 GMV == 요약축 GMV (D-CPP-36).
    # ★try/except: 위 셋(데이터 나이·디스크·원가)과 같은 이유 — 이 조회가 실패해도 헬스 API
    #   전체를 죽이면 안 된다(워치독 침묵 = 이 계열 사고가 막으려는 바로 그 실패).
    vendor_item_conservation: dict | None = None
    try:
        from datetime import timedelta  # noqa: PLC0415 — 지연 임포트(순수 코어 테스트용)

        from app.services.coupang import vendor_item_sales_sync as _vis  # noqa: PLC0415

        end = now.date()
        start = end - timedelta(days=CONSERVATION_WINDOW_DAYS)
        checks = [_vis.conservation_check(db, ak, start, end) for ak in CONSERVATION_ACCOUNTS]
        vendor_item_conservation = {
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "compared": sum(c["compared"] for c in checks),
            # 계정별 항목에 account_key가 이미 들어 있으므로 평탄화해도 출처를 잃지 않는다.
            "mismatch": [m for c in checks for m in
                         ({**m, "account_key": c["account_key"]} for m in c["mismatch"])],
            "summary_only": [m for c in checks for m in
                             ({**m, "account_key": c["account_key"]} for m in c["summary_only"])],
            "option_only": [m for c in checks for m in
                            ({**m, "account_key": c["account_key"]} for m in c["option_only"])],
        }
    except Exception:
        log.exception("[워치독] 판매분석 보존식 대조 실패 — 이 감시만 생략(헬스 API는 유지)")
        vendor_item_conservation = None

    # 조치 생존 — 우리가 건 검색어 제외가 아직 걸려 있나(D-NAO-173 P1-①).
    # ★여기서는 **DB만 읽는다.** 라이브 대조는 하루 1회 잡(verify_search_term_exclusions)이 하고
    #   그 결과가 행에 적혀 있다. 헬스 요청마다 네이버 API를 부르면 배너가 외부 지연·한도에
    #   묶이고, 그 순간 감시가 감시 대상보다 먼저 죽는다.
    # ★try/except: 위 넷과 같은 이유 — 이 조회가 실패해도 헬스 API 전체를 죽이지 않는다.
    exclusion_survival: dict | None = None
    try:
        from app.services.naver_ad import exclusion_survival as _es  # noqa: PLC0415

        exclusion_survival = _es.survival_summary(db, now=now)
    except Exception:
        log.exception("[워치독] 제외 생존 요약 실패 — 이 감시만 생략(헬스 API는 유지)")
        exclusion_survival = None

    return build_health(
        WATCHDOG_JOBS, states, registered, running, now,
        cookies=cookies, data_snapshots=data_snapshots, disk_snapshots=disk_snapshots,
        cost_drift=cost_drift,
        vendor_item_conservation=vendor_item_conservation,
        exclusion_survival=exclusion_survival,
    )
