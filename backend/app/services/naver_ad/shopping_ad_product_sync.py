# shopping_ad_product_sync.py — shopping_ad_product_sync (D-NAO-57 A, 관찰성 sync)
# 역할: **관측 스코프**(campaign_roster.observation_campaign_ids) 쇼핑 캠페인의 활성 광고그룹을
#   순회 → /ncc/ads의 referenceData.mallProductId를 수집 → naver_adgroup_product에 **upsert**.
#   campaign_target_resolver 우선순위 ②(상품 파생 target_roas)가 소비한다.
# 순수 수집(collect_) + 쓰기 harness(sync_) 분리 — 관찰만이라 실행 게이트 없음(fail-open은
#   호출 크론이 담당). 매핑은 느리게 변하는 관측치라 일 1회(07:45, 레인·제안 이전) 충분.
#
# ══ ★★이 모듈은 매핑 행을 삭제하지 않는다 (2026-08-03, codex 2R 이후) ══
#   2026-07-30 긴급정지(D-NAO-132) 다음 날, 이 잡이 `naver_adgroup_product` 276행을 통째로
#   지웠다(prod 로그 2026-07-31 07:45 KST "그룹 0개 ... 정리 276행"). 원인은 관측 스코프를
#   실행 스위치(optimizer)로 잡은 것이었고, 1차 수정은 "대상 0이면 안 지운다"는 가드였다.
#   그 뒤 적대 리뷰 두 라운드 동안 가드가 네 겹으로 늘었는데 매 라운드 새 틈이 나왔다:
#     · 전역 신호(활성 그룹 0 / spend 축 전체 침묵)로 **캠페인별** 삭제를 판단했고,
#     · 네이버의 **부분 HTTP-200**(리스트 일부 누락)을 "없어졌다"와 구분할 방법이 없었다.
#   가드를 더 쌓는 대신 **위험 표면 자체를 없앤다.** 이 잡의 목적은 매핑 *수집*이고 stale
#   정리는 부차적이다. 그래서 정리 계층을 통째로 들어냈다 — 지금 이 파일에 DELETE는 없다.
#
#   ★의도된 트레이드오프: 사라진 소재의 행이 남는다. 그 손해는 작다 — 조회 시 실제 소재와
#     조인하면 걸러지고, `synced_at`이 이번 회차에 갱신되지 않으므로 신선도로도 구분된다.
#     반대편 손해는 실측됐다: 삭제 실패 한 번이 276행을 영구히 날렸다. 이 비대칭이 설계 근거다.
#   ★stale 정리가 필요해지면 **별도 작업**으로 분리한다(그때는 "무엇이 실제로 사라졌는가"를
#     부분 응답과 구분할 증거를 먼저 확보해야 한다 — 이 잡에 되붙이지 말 것).
from __future__ import annotations

import json
import logging
import time
from datetime import date
from typing import NamedTuple

from sqlalchemy import func
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import NaverAccountSettings, NaverAdgroupProduct, NaverEntity
from app.services.naver_ad import ad_external_change, campaign_roster
from app.services.naver_ad.diary import ACTOR_SYSTEM, write_diary_entry
from app.services.naver_sa_ad_fetcher import MAX_CALL_DURATION_S, get_ads
from app.utils.kst import kst_now, kst_today

log = logging.getLogger(__name__)

# 관측 대상 자체가 0일 때 남기는 일기 action(diary observe grain).
DIARY_ACTION_SCOPE_BLIND = "adgroup_product_scope_blind"

# ── 순회 커서(codex 2R P1-3 starvation) ────────────────────────────────────────
# 시간 예산에 걸려 중단하면 다음 회차가 **중단 지점 다음부터** 시작해야 한다. 그렇지 않으면
# 매 회차 index 0에서 시작해 같은 prefix만 갱신되고 꼬리는 영원히 미시도로 굶는다.
# 저장소는 기존 범용 KV(`naver_account_settings`) — 새 테이블·마이그레이션 없이 쓴다.
# ★이 키는 **시스템 소유**다(사람이 콘솔에서 조작하는 expert_delegated_types와 성격이 다름).
CURSOR_SETTINGS_KEY = "shopping_ad_sync_cursor"

# write_cursor의 `expected` 미지정(=CAS 안 함)을 None(=커서 없음)과 구분하는 센티널.
_UNSET: str = "\x00__unset__"
# "커서 없음"의 저장 표현 — NULL이 아니라 빈 문자열(_encode_cursor docstring 참조).
_CURSOR_NONE = ""

# ── 호출 예산(codex 1R P2 / 2R P2) ────────────────────────────────────────────
# ★네이버 SA API의 **공식 rate limit 수치는 문서에서 확인 안 됨**(추정 금지). 그래서 값을
#   지어내지 않고 이 코드베이스의 기존 실사용 관례 사이에서 보수적으로 고른다:
#     · market_bid_probe._SLEEP_BETWEEN_CALLS = 0.3s (읽기 전용 추정 호출)
#     · bm_snapshot._DEEP_SLEEP_S           = 0.05s (주간 deep GET, 그룹 루프)
#   이 루프는 "그룹 루프"라 후자와 같은 성격이지만 그룹 수가 훨씬 많아졌으므로(관측 스코프
#   교체로 99 → 242그룹, 2026-08-03 prod 실측) 중간값을 쓴다. 429는 fetcher._get이 이미
#   지수 백오프로 재시도하므로 이 상수는 **429를 만들지 않기 위한 사전 간격**이다.
_MIN_CALL_INTERVAL_S = 0.2

# 한 회차 수집의 소프트 예산(초). 넘으면 남은 그룹을 **다음 회차로 이월**한다(커서 전진).
# 값 근거(크론 배치, scheduler_service): 이 잡은 07:45, 하류 소비자인 제안 생성은 08:00 —
# 15분 창에서 5분을 마진으로 남긴다.
_RUN_BUDGET_S = 600.0
# 07:50 forecast_engine 슬롯을 침범하기 시작하는 지점 — 중단은 아니고 WARNING만.
_SLOW_RUN_WARN_S = 240.0


class CollectResult(NamedTuple):
    """수집 1회의 결과 + **그 결과를 얼마나 믿을 수 있는가**.

    삭제를 안 하게 된 뒤에도 이 신뢰도 필드는 남긴다 — 데이터를 지키기 위해서가 아니라
    로그·모니터링이 정직하려면 "0건 관측"과 "관측 안 함"이 구분돼야 하기 때문이다.
    """

    per_group: dict[str, list[dict]]
    failed: set[str]
    truncated: bool          # 시간 예산 소진으로 남은 그룹을 다음 회차로 이월했다
    attempted: list[str]     # 실제로 조회를 **시도**한 그룹(성공 + 실패), 처리 순서대로
    elapsed_s: float
    cursor: str | None       # 다음 회차 시작점(None = 한 바퀴 완주 → 처음부터)
    advance_cursor: bool     # False면 커서를 **건드리지 않는다**(시도 0 = 진행도 없음)


def _require_today(as_of: date, *, caller: str) -> None:
    """★라이브 수집 경로는 **오늘만** 허용한다(codex 2R P1-4).

    왜 문서 경고로는 부족한가: 과거 `as_of`를 주면 이 잡은 **과거 광고비 + 현재 settings +
    현재 엔티티 + 라이브 소재**를 뒤섞어 처리한다. 네 축 중 되감기는 것은 광고비뿐이고
    (settings·엔티티는 히스토리가 없다) 소재는 항상 '지금'이다. 그렇게 만든 반쪽 스코프로
    라이브 API를 때리고 DB를 갱신하면 "언제 것인지 모르는 데이터"가 생긴다 — 어떤 docstring
    경고로도 안전해지지 않는다. 1차 대응의 "문서화 + 파라미터 명시" 판단을 철회하고 거부한다.
    """
    today = kst_today()
    if as_of != today:
        raise ValueError(
            f"{caller}: as_of={as_of}는 오늘({today})이 아니다 — 이 경로는 과거 날짜를 거부한다. "
            "관측 스코프의 settings·엔티티 축은 히스토리가 없어 과거 재현이 불가능하다"
            "(campaign_roster.observation_campaign_ids docstring 참조)."
        )


def _observed_shopping_adgroups(
    db: Session, *, as_of: date, scope_ids: set[str] | None = None,
) -> list[NaverEntity]:
    """**관측 스코프** 캠페인에 속한 활성(status='on') 쇼핑 광고그룹 엔티티.

    스코프 = `campaign_roster.observation_campaign_ids`(최근 7일 광고비>0 ∪ settings 행 존재,
    optimizer 무관). ★여기를 optimizer='ours'로 되돌리면 2026-07-30 사고가 재현된다 —
    이유는 그 함수 docstring에 D-NAO-13 원문·bm_diff.py:10-13과 함께 적혀 있다.

    모듈 고유 필터는 그대로다: 매핑 소스는 SHOPPING 캠페인의 활성 그룹뿐이다
    (파워링크/브랜드검색은 상품 소재가 아님).

    ★`entity_id` 오름차순 **결정적 정렬**(codex 2R P1-3): 커서 순회가 성립하려면 회차마다
      같은 순서여야 한다. 정렬이 없으면 DB가 주는 순서에 기대게 되고, 커서가 가리키는
      "다음"이 회차마다 달라져 꼬리가 굶는 문제를 못 고친다.
    """
    if scope_ids is None:
        scope_ids = campaign_roster.observation_campaign_ids(db, today=as_of)
    if not scope_ids:
        return []
    return (
        db.query(NaverEntity)
        .filter(
            NaverEntity.entity_type == "adgroup",
            NaverEntity.campaign_type == "SHOPPING",
            NaverEntity.status == "on",
            NaverEntity.campaign_id.in_(scope_ids),
        )
        .order_by(NaverEntity.entity_id.asc())
        .all()
    )


def _encode_cursor(cursor: str | None) -> str:
    """커서 → 저장값. None(=한 바퀴 완주)은 **빈 문자열**로 적는다.

    ★NULL을 쓰지 않는 이유: CAS의 WHERE 절이 `value_json = :expected`인데 SQL에서
    `= NULL`은 항상 거짓이라 "커서 없음"을 기대값으로 삼는 순간 CAS가 절대 성립하지 않는다.
    빈 문자열이면 최초 실행(기대값 None)도 같은 한 문장으로 판정된다.
    """
    return cursor or _CURSOR_NONE


def read_cursor(db: Session) -> str | None:
    """직전 회차가 마지막으로 **시도한** adgroup_id(없으면 None = 처음부터)."""
    raw = (
        db.query(NaverAccountSettings.value_json)
        .filter(NaverAccountSettings.key == CURSOR_SETTINGS_KEY)
        .scalar()
    )
    if not raw:
        return None
    if raw.startswith("{"):
        # 구 JSON 형식 관용(이 브랜치의 초기 구현) — 읽기만 지원하고 다음 쓰기에 평문으로 바뀐다.
        try:
            return (json.loads(raw) or {}).get("last_adgroup_id") or None
        except (ValueError, TypeError):
            log.warning("shopping_ad_product_sync: 커서 파싱 실패 — 처음부터 순회한다")
            return None
    return raw


def write_cursor(
    db: Session, last_adgroup_id: str | None, *, now, expected: str | None = _UNSET,
) -> bool:
    """다음 회차 시작점 저장(**원자적 CAS**). None이면 '한 바퀴 완주' = 다음 회차는 처음부터.

    반환: 실제로 썼으면 True, CAS 불일치(= 상대가 먼저 진행)로 건너뛰었으면 False.

    ★CAS가 필요한 이유(codex 3R P2): 이 잡은 **동시 실행될 수 있다.** APScheduler는 같은
      잡의 중복 실행을 막지만(`max_instances` 기본 1), `_catch_up_morning_batch`가 **별도
      데몬 스레드에서 스케줄러를 우회해** `shopping_ad_product_sync_job()`을 직접 부른다
      (scheduler_service `_run_chain`). 그래서 07:45 정시 발화와 catch-up 체인이 겹칠 수 있다.

    ★왜 "뒤로 안 간다"가 아니라 CAS인가: 커서는 **링**이라 한 바퀴 돌면 작은 id로 돌아오는
      것이 정상 전진이다(grp-98 → grp-01). 단순 대소 비교로는 정상 전진과 되감기를 구분할 수
      없다. 대신 "내가 회차 시작에 읽은 값이 아직 그대로인가"만 본다.

    ★★판정을 **DB에 맡긴다**(codex 4R P2-6): 종전 구현은 ORM으로 읽고 파이썬에서 비교한 뒤
      따로 UPDATE라, 두 트랜잭션이 **모두 같은 값을 읽으면 둘 다 통과**했다(check-then-act
      경합). 이제 `UPDATE ... WHERE key=:key AND value_json=:expected` 한 문장으로 조건과
      갱신을 묶고 `rowcount == 1`로 승패를 가른다 — 진 쪽은 아무것도 쓰지 않는다.
    """
    new_value = _encode_cursor(last_adgroup_id)

    if expected is _UNSET:
        # CAS 없음(초기 세팅·테스트) — 있으면 갱신, 없으면 삽입.
        updated = db.execute(
            sa_update(NaverAccountSettings)
            .where(NaverAccountSettings.key == CURSOR_SETTINGS_KEY)
            .values(value_json=new_value)
        ).rowcount
        if not updated:
            db.add(NaverAccountSettings(key=CURSOR_SETTINGS_KEY, value_json=new_value))
        return True

    # ① 조건부 UPDATE — 조건 판정과 갱신이 한 문장(원자적).
    res = db.execute(
        sa_update(NaverAccountSettings)
        .where(
            NaverAccountSettings.key == CURSOR_SETTINGS_KEY,
            NaverAccountSettings.value_json == _encode_cursor(expected),
        )
        .values(value_json=new_value)
    )
    if res.rowcount == 1:
        return True

    # ② 행 자체가 없는 최초 실행(기대값도 '커서 없음')만 INSERT를 시도한다.
    #    동시 INSERT는 unique 제약이 잡아준다 — 진 쪽은 IntegrityError를 받고 물러난다.
    if expected is None:
        try:
            with db.begin_nested():
                db.add(NaverAccountSettings(key=CURSOR_SETTINGS_KEY, value_json=new_value))
            return True
        except IntegrityError:
            log.warning(
                "shopping_ad_product_sync: 커서 최초 생성 경합 — 상대가 먼저 만들었다"
                "(동시 실행: 07:45 정시 발화 vs catch-up 스레드). 내 커서(%s)는 쓰지 않는다.",
                last_adgroup_id,
            )
            return False

    log.warning(
        "shopping_ad_product_sync: 커서 CAS 불일치 — 회차 시작 시 %s였는데 그 사이 바뀌었다"
        "(동시 실행 의심). 내 커서(%s)는 쓰지 않고 상대 진행도를 존중한다.",
        expected, last_adgroup_id,
    )
    return False


def _persist_cursor(
    db: Session, new_cursor: str | None, *, expected: str | None, now, advance: bool = True,
) -> None:
    """커서를 **매핑 커밋과 분리된 짧은 트랜잭션**으로 저장(fail-open).

    분리하는 이유: 동시 INSERT가 unique 충돌을 내면 그 예외가 **같은 트랜잭션의 매핑 upsert를
    통째로 되돌린다.** 커서는 최적화이고 매핑은 본 기능이므로, 커서 저장 실패가 수집 결과를
    날리는 일은 없어야 한다(이 파일의 fail-open 규율과 동일).
    """
    if not advance:
        # 시도한 그룹이 0개 = 진행도가 없다. 기존 커서를 **그대로 둔다**(구 코드는 None을 저장해
        # 진행도를 지웠고, 그러면 느린 계정에서 꼬리가 영영 굶는다 — codex 4R P2-6).
        log.warning(
            "shopping_ad_product_sync: 시도한 그룹이 0개 — 커서를 갱신하지 않고 기존 진행도를 "
            "유지한다(예산이 한 호출 최악 소요보다 작을 때 발생)",
        )
        return
    try:
        if write_cursor(db, new_cursor, now=now, expected=expected):
            db.commit()
        else:
            db.rollback()
    except Exception as e:  # noqa: BLE001 — 커서는 정확성 요건이 아니다
        log.warning("shopping_ad_product_sync: 커서 저장 실패(무시하고 진행): %s", e)
        db.rollback()


def _rotate_from_cursor(adgroups: list[NaverEntity], cursor: str | None) -> list[NaverEntity]:
    """커서 **다음** 그룹부터 시작하도록 링 회전(한 바퀴 = 전체 1회 순회).

    커서가 이번 목록에 없어도(그룹 삭제·스코프 변경) 커서보다 큰 첫 원소부터 이어간다 —
    정렬돼 있으므로 자연스럽게 '그 다음'이 된다. 그런 원소가 없으면(커서가 마지막 이후)
    처음부터 새 바퀴를 돈다.
    """
    if not cursor or not adgroups:
        return adgroups
    start = next((i for i, ag in enumerate(adgroups) if ag.entity_id > cursor), None)
    if start is None:
        return adgroups
    return adgroups[start:] + adgroups[:start]


def _log_observation_blind(db: Session, *, scope_ids: set[str], now) -> None:
    """관측 대상이 0일 때의 유성 실패 기록 — WARNING + 운영 일기(observe).

    `flight_loop._log_flight_silence`의 관례를 따른다: **병리 상태일 때만** 1행 남긴다
    (정상일 때 0행이라 소음이 되지 않는다). 다만 이 모듈은 캠페인 단위가 아니므로
    change_log가 아니라 diary observe에 남긴다(캠페인 조인에 섞이지 않게 campaign_id="").

    ★문장이 곧 진단이어야 한다: 구 코드의 INFO `그룹 0개`는 "관측했더니 0건"으로도,
      "볼 대상 자체가 없다"로도 읽혀서, 사흘간의 맹목이 안전 확인처럼 보였다.
    ★"삭제하지 않고 보존한다"류 서술은 넣지 않는다 — 이 잡은 애초에 아무것도 안 지우므로
      그 문장은 무의미하고, 있으면 독자가 "지울 수도 있는 잡"으로 오해한다.
    """
    existing = db.query(func.count(NaverAdgroupProduct.id)).scalar() or 0
    if not scope_ids:
        cause = "관측 스코프 캠페인이 0개(최근 7일 광고비>0 ∪ settings 행 — 둘 다 비었다)"
    else:
        cause = (
            f"관측 스코프 캠페인은 {len(scope_ids)}개인데 그 안의 활성 SHOPPING 광고그룹이 0개"
            "(entity_sync 침묵 또는 그룹 전부 off)"
        )
    msg = (
        f"관측 대상 자체가 0 — 스코프 결함 의심. {cause}. "
        f"이번 회차 갱신은 0행이다(기존 {existing}행은 그대로 남지만 신선도가 멈춘다)."
    )
    log.warning("shopping_ad_product_sync: %s", msg)
    write_diary_entry(
        db, "observe", "", actor=ACTOR_SYSTEM,
        action=DIARY_ACTION_SCOPE_BLIND, rationale=msg, now=now,
    )


def collect_adgroup_products(
    db: Session, *,
    as_of: date,
    ads_by_adgroup: dict[str, list[dict]] | None = None,
    adgroups: list[NaverEntity] | None = None,
    cursor: str | None = None,
    budget_s: float = _RUN_BUDGET_S,
) -> CollectResult:
    """대상 광고그룹별 상품 매핑 + 수집 신뢰도(실패·이월·커서)를 반환. **DB 쓰기 없음.**

    `as_of`는 필수다 — 조용한 `kst_today()` 폴백을 두지 않는다(codex 2R P1-4). 이 함수는
    비파괴(순수 읽기)라 자체적으로 과거 날짜를 막지는 않는다. 거부는 쓰기 주체인
    `sync_adgroup_products`가 한다.

    ads_by_adgroup는 테스트·재사용 주입용(원칙18-8). 미주입 시 get_ads로 그룹마다 1콜.
    같은 그룹에서 같은 mall_product_id가 여러 소재로 중복되면 dedup(첫 이름 채택).
    개별 그룹의 조회 실패는 그 그룹만 skip(fail-open)하되 **failed에 기록**한다.

    ★호출 예산: 실 API 경로에서만 호출 간 `_MIN_CALL_INTERVAL_S` 스로틀을 건다. 새 호출을
      **시작하기 전에** "남은 예산 ≥ 한 호출의 최악 소요(`MAX_CALL_DURATION_S`=93s)"를
      확인한다 — 경과만 보고 판단하면 재시도까지 물린 한 호출이 예산을 통째로 넘긴다
      (codex 2R P2). 모자라면 남은 그룹을 다음 회차로 이월하고 커서를 남긴다.
    """
    if adgroups is None:
        adgroups = _observed_shopping_adgroups(db, as_of=as_of)
    live = ads_by_adgroup is None
    ordered = _rotate_from_cursor(adgroups, cursor)
    # 주입 경로(테스트)는 네트워크가 없어 한 호출의 최악 소요가 0이다 — 헤드룸을 요구하면
    # 예산과 무관하게 항상 truncated가 된다.
    headroom = MAX_CALL_DURATION_S if live else 0.0
    started = time.monotonic()
    result: dict[str, list[dict]] = {}
    failed: set[str] = set()
    attempted: list[str] = []
    truncated = False
    if not adgroups:
        # ★"대상 0" ≠ "관측했는데 0건". 구 코드는 둘 다 조용히 넘겨 맹목 상태가 안전 확인처럼
        #   보였다(2026-07-31~08-02 사흘간 INFO "그룹 0개"만 찍히며 침묵).
        log.warning(
            "shopping_ad_product_sync: 관측 대상 광고그룹이 0 — 관측 스코프 결함 의심"
            "(스코프 정의는 campaign_roster.observation_campaign_ids)",
        )
    for idx, ag in enumerate(ordered):
        aid = ag.entity_id
        if time.monotonic() - started + headroom > budget_s:
            truncated = True
            log.warning(
                "shopping_ad_product_sync: 수집 예산 %.0fs 소진(한 호출 최악 %.0fs 헤드룸 포함) — "
                "남은 그룹 %d개를 다음 회차로 이월(커서=%s)",
                budget_s, headroom, len(ordered) - idx, attempted[-1] if attempted else "없음",
            )
            break
        if live and idx > 0:
            time.sleep(_MIN_CALL_INTERVAL_S)
        attempted.append(aid)
        try:
            ads = (ads_by_adgroup or {}).get(aid) if ads_by_adgroup is not None else get_ads(aid)
        except Exception as e:  # noqa: BLE001 — 그룹 단위 fail-open
            log.warning("shopping_ad_product_sync: 광고그룹 %s 소재 조회 실패(skip): %s", aid, e)
            failed.add(aid)
            continue
        seen: set[str] = set()
        rows: list[dict] = []
        for ad in ads or []:
            mall_pid = str(ad.get("mall_product_id") or "")
            if not mall_pid or mall_pid in seen:
                continue
            seen.add(mall_pid)
            rows.append({
                "mall_product_id": mall_pid,
                "product_name": (ad.get("product_name") or "")[:300],
                "campaign_id": ag.campaign_id,
                # B1(D-NAO-65): 소재-레벨 입찰 필드(get_ads가 채움 — 미주입/부재 시 None).
                # dedup은 첫 소재를 채택하므로 입찰 필드도 첫 소재 값(product_name과 동형).
                "ad_id": ad.get("ad_id") or None,
                "ad_bid_amt": ad.get("ad_bid_amt"),
                "use_group_bid_amt": ad.get("use_group_bid_amt"),
                "ad_user_lock": ad.get("ad_user_lock"),
                # D-NAO-127: 소재 외부 변경 탐지 앵커(editTm). 미주입 경로는 None → 판정 유보.
                "edit_tm": ad.get("edit_tm"),
                "adgroup_id": aid,
            })
        result[aid] = rows
    # 커서 결정(codex 4R P2-6):
    #   · 중간에 끊겼고 시도한 게 있다 → 마지막 시도 지점(다음 회차가 그 **다음**부터).
    #   · 한 바퀴 완주 → None(다음 회차는 처음부터 = 링 되감기).
    #   · ★첫 호출 **전에** 예산이 소진돼 시도가 0이다 → `advance=False`로 **기존 커서를
    #     그대로 둔다.** 구 코드는 이 경우에도 None을 저장해 그동안의 진행도를 지웠고,
    #     그러면 느린 계정에서 매 회차 앞부분만 다시 갈고 꼬리가 영영 굶는다(P1-3 재발).
    advance_cursor = bool(attempted)
    next_cursor = attempted[-1] if (truncated and attempted) else None
    return CollectResult(
        per_group=result, failed=failed, truncated=truncated, attempted=attempted,
        elapsed_s=time.monotonic() - started, cursor=next_cursor,
        advance_cursor=advance_cursor,
    )


def sync_adgroup_products(
    db: Session, *,
    as_of: date,
    ads_by_adgroup: dict[str, list[dict]] | None = None,
    budget_s: float = _RUN_BUDGET_S,
) -> dict:
    """naver_adgroup_product **upsert**(멱등) + 소재 외부 변경 탐지 트리거.

    `as_of`는 필수이며 **오늘이어야 한다**(`_require_today`, codex 2R P1-4).

    ══ 이 함수는 매핑 행을 삭제하지 않는다 ══
    모듈 docstring의 "위험 표면 제거" 참조. (1) 스코프 이탈 정리와 (2) 캠페인별 stale 정리는
    2026-08-03에 **통째로 제거**됐고, (0) 그룹 스냅샷 교체도 delete+insert에서 upsert로 바꿨다.
    (0)까지 바꾼 이유: 네이버가 `/ncc/ads`에 **부분 200**을 주면 "이 그룹의 상품이 줄었다"와
    구분할 수 없어, 성공적으로 조회한 그룹에서조차 삭제가 안전하지 않다(codex 2R가 지적한
    부분-200 문제는 엔티티 목록만의 병이 아니다).

    갱신 규칙: (adgroup_id, mall_product_id)가 이미 있으면 필드만 갱신, 없으면 삽입.
    두 경우 모두 `synced_at`을 이번 회차 시각으로 올린다 — 이번에 관측되지 않은 행은
    `synced_at`이 그대로라 **신선도로 stale을 판별**할 수 있다(소비자 측 필터의 근거).

    반환: {adgroups, mappings, products, inserted, updated, failed_adgroups,
           external_ad_changes, observation_blind, truncated, cursor, elapsed_s}.
    """
    _require_today(as_of, caller="sync_adgroup_products")
    scope_ids = campaign_roster.observation_campaign_ids(db, today=as_of)
    adgroups = _observed_shopping_adgroups(db, as_of=as_of, scope_ids=scope_ids)
    cursor = read_cursor(db)

    # 기존 행 전량을 한 번에 뜬다(테이블 규모 수백 행). 두 용도를 한 스캔으로 해결한다:
    #   ①upsert 대상 조회 ②D-NAO-127 직전 관측 상태(prev_by_ad).
    existing_rows = db.query(NaverAdgroupProduct).all()
    by_key = {(r.adgroup_id, r.mall_product_id): r for r in existing_rows}
    prev_by_ad = {
        r.ad_id: {
            "edit_tm": r.ad_edit_tm, "ad_bid_amt": r.ad_bid_amt,
            "use_group_bid_amt": r.use_group_bid_amt, "ad_user_lock": r.ad_user_lock,
        }
        for r in existing_rows if r.ad_id
    }

    collected = collect_adgroup_products(
        db, as_of=as_of, ads_by_adgroup=ads_by_adgroup, adgroups=adgroups,
        cursor=cursor, budget_s=budget_s,
    )
    per_group, failed = collected.per_group, collected.failed
    now = kst_now()

    observation_blind = not adgroups
    if observation_blind:
        _log_observation_blind(db, scope_ids=scope_ids, now=now)

    inserted = updated = 0
    distinct: set[str] = set()
    for aid, rows in per_group.items():
        for r in rows:
            key = (aid, r["mall_product_id"])
            row = by_key.get(key)
            if row is None:
                row = NaverAdgroupProduct(adgroup_id=aid, mall_product_id=r["mall_product_id"])
                db.add(row)
                by_key[key] = row
                inserted += 1
            else:
                updated += 1
            row.campaign_id = r["campaign_id"]
            row.product_name = r["product_name"]
            # B1(D-NAO-65): 입찰 필드 미주입(기존 소비자 형식) 시 .get→None(하위호환).
            row.ad_id = r.get("ad_id")
            row.ad_bid_amt = r.get("ad_bid_amt")
            row.use_group_bid_amt = r.get("use_group_bid_amt")
            row.ad_user_lock = r.get("ad_user_lock")
            row.ad_edit_tm = r.get("edit_tm")  # D-NAO-127 앵커
            row.synced_at = now                # 신선도 — stale 판별의 유일 근거
            distinct.add(r["mall_product_id"])

    db.commit()
    # 커서는 매핑 커밋 **이후** 별도 트랜잭션에 쓴다(_persist_cursor docstring — 동시 INSERT
    # 충돌이 매핑 upsert를 되돌리면 안 된다). CAS 기준은 이 회차 시작에 읽은 값.
    _persist_cursor(db, collected.cursor, expected=cursor, now=now,
                    advance=collected.advance_cursor)

    # ★D-NAO-127 소재 외부 변경 탐지 — 매핑 커밋 **이후**에 독립 실행한다.
    #   ①prev_by_ad는 이미 메모리에 떠 있어 DB가 새 값으로 덮여도 비교가 가능하고,
    #   ②탐지 실패가 매핑 sync를 되돌리지 않는다(관측 부가기능이 본 기능을 죽이면 안 됨 —
    #     이 파일의 그룹 단위 fail-open과 같은 규율).
    detected = {"observed": 0, "ops": 0, "recorded": 0}
    try:
        observed = [r for rows in per_group.values() for r in rows if r.get("ad_id")]
        detected = ad_external_change.run(db, prev_by_ad=prev_by_ad, observed=observed, now=now)
    except Exception as e:  # noqa: BLE001 — 관측 전용 부가기능, fail-open
        log.exception("shopping_ad_product_sync: 소재 외부 변경 탐지 실패(무시하고 진행): %s", e)
        db.rollback()

    # ★로그 레벨로 "대상 0"과 "관측했는데 0건"을 가른다(맹목은 WARNING, 정상은 INFO).
    #   소요시간을 항상 싣는다(codex 1R P2): 07:45 잡이 08:00 제안 잡을 침범하는지는 숫자가
    #   로그에 있어야 사후에 판정된다.
    elapsed = collected.elapsed_s
    slow = elapsed >= _SLOW_RUN_WARN_S
    notes = []
    if observation_blind:
        notes.append("관측 대상 0 — 이번 회차 갱신 0행")
    if collected.truncated:
        notes.append(f"시간예산 소진 — 다음 회차 이월(커서 {collected.cursor})")
    if slow:
        notes.append(f"소요 {elapsed:.0f}s ≥ {_SLOW_RUN_WARN_S:.0f}s — 하류 잡(07:50/08:00) 침범 위험")
    log_fn = log.warning if (observation_blind or collected.truncated or slow) else log.info
    log_fn(
        "naver_adgroup_product sync: 그룹 %d개 시도(%d개 수집), 매핑 신규 %d·갱신 %d, 상품 %d종, "
        "실패그룹 %d, 외부변경 %d건, 소요 %.1fs%s",
        len(collected.attempted), len(per_group), inserted, updated, len(distinct),
        len(failed), detected["recorded"], elapsed,
        f" [{' / '.join(notes)}]" if notes else "",
    )
    return {"adgroups": len(per_group), "mappings": inserted + updated,
            "products": len(distinct), "inserted": inserted, "updated": updated,
            "failed_adgroups": len(failed),
            "external_ad_changes": detected["recorded"],
            "observation_blind": observation_blind,
            "truncated": collected.truncated,
            "cursor": collected.cursor,
            "elapsed_s": round(elapsed, 3)}
