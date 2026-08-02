# shopping_ad_product_sync.py — shopping_ad_product_sync (D-NAO-57 A, 관찰성 sync)
# 역할: **관측 스코프**(campaign_roster.observation_campaign_ids) 쇼핑 캠페인의 활성 광고그룹을
#   순회 → /ncc/ads의 referenceData.mallProductId를 수집 → naver_adgroup_product에 그룹 단위
#   스냅샷 교체 적재. campaign_target_resolver 우선순위 ②(상품 파생 target_roas)가 소비한다.
# 순수 수집(collect_) + 쓰기 harness(sync_) 분리 — 관찰만이라 실행 게이트 없음(fail-open은
#   호출 크론이 담당). 매핑은 느리게 변하는 관측치라 일 1회(07:45, 레인·제안 이전) 충분.
#
# ★2026-07-30 사고(D-NAO-132 긴급정지)로 두 가지를 고쳤다 — 자세한 근거는
#   `campaign_roster.observation_campaign_ids` docstring:
#   ①스코프를 optimizer='ours'에서 관측 스코프로 교체(스위치를 내리면 눈까지 감던 구조).
#   ②**관측 대상 0일 때의 전량 삭제 제거.** 구 코드는 ours가 하나도 없으면
#     `delete(NaverAdgroupProduct)`로 테이블을 통째로 비웠고, 실제로 2026-07-31 07:45 KST에
#     276행이 0행이 됐다(prod 로그 "그룹 0개 ... 정리 276행"). **"관측 대상 0"은 정상 상태가
#     아니라 이상 신호이며, 데이터를 지우는 근거가 될 수 없다.**
from __future__ import annotations

import logging
import time
from datetime import date
from typing import NamedTuple

from sqlalchemy import delete, func
from sqlalchemy.orm import Session

from app.models import NaverAdgroupProduct, NaverEntity
from app.services.naver_ad import ad_external_change, campaign_roster
from app.services.naver_ad.diary import ACTOR_SYSTEM, write_diary_entry
from app.services.naver_sa_ad_fetcher import get_ads
from app.utils.kst import kst_now, kst_today

log = logging.getLogger(__name__)

# 관측 대상 자체가 0일 때 남기는 일기 action(diary observe grain).
DIARY_ACTION_SCOPE_BLIND = "adgroup_product_scope_blind"

# ── 호출 예산(codex 1R P2, 2026-08-03) ──────────────────────────────────────────
# ★네이버 SA API의 **공식 rate limit 수치는 문서에서 확인 안 됨**(추정 금지). 그래서 값을
#   지어내지 않고 이 코드베이스의 기존 실사용 관례 사이에서 보수적으로 고른다:
#     · market_bid_probe._SLEEP_BETWEEN_CALLS = 0.3s (읽기 전용 추정 호출)
#     · bm_snapshot._DEEP_SLEEP_S           = 0.05s (주간 deep GET, 그룹 루프)
#   이 루프는 "그룹 루프"라 후자와 같은 성격이지만 그룹 수가 훨씬 많아졌으므로(관측 스코프
#   교체로 99 → 242그룹, 2026-08-03 prod 실측) 중간값을 쓴다. 429는 fetcher._get이 이미
#   지수 백오프로 재시도하므로(최대 3회) 이 상수는 **429를 만들지 않기 위한 사전 간격**이다.
_MIN_CALL_INTERVAL_S = 0.2

# 한 회차 수집의 소프트 예산(초). 넘으면 남은 그룹을 **다음 회차로 미루고**(중단이 아니라
# 이월) 그 회차의 정리 계층은 전면 보류한다 — 부분 수집으로 stale 판정을 하면 P1-1과
# 똑같은 함정(안 본 것을 없어진 것으로 오인)에 빠진다.
# 값 근거(크론 배치, scheduler_service): 이 잡은 07:45, 하류 소비자인 제안 생성은 08:00 —
# 15분 창에서 5분을 마진으로 남긴다. 242그룹 × (지연 + 0.2s)는 정상 시 2분 내라 평시에는
# 이 예산에 닿지 않는다(닿으면 그 자체가 이상 신호).
_RUN_BUDGET_S = 600.0
# 07:50 forecast_engine 슬롯을 침범하기 시작하는 지점 — 중단은 아니고 WARNING만.
_SLOW_RUN_WARN_S = 240.0


class CollectResult(NamedTuple):
    """수집 1회의 결과 + **그 결과를 얼마나 믿을 수 있는가**.

    `truncated`/`attempted`가 없으면 호출부가 "안 본 그룹"과 "없어진 그룹"을 구분할 수
    없다 — 그 구분 실패가 2026-07-30 사고의 형태였다.
    """

    per_group: dict[str, list[dict]]
    failed: set[str]
    truncated: bool          # 시간 예산 초과로 남은 그룹을 다음 회차로 이월했다
    attempted: set[str]      # 실제로 조회를 **시도**한 그룹(성공 + 실패)
    elapsed_s: float


def _observed_shopping_adgroups(
    db: Session, *, as_of: date | None = None, scope_ids: set[str] | None = None,
) -> list[NaverEntity]:
    """**관측 스코프** 캠페인에 속한 활성(status='on') 쇼핑 광고그룹 엔티티.

    스코프 = `campaign_roster.observation_campaign_ids`(최근 7일 광고비>0 ∪ settings 행 존재,
    optimizer 무관). ★여기를 optimizer='ours'로 되돌리면 2026-07-30 사고가 재현된다 —
    이유는 그 함수 docstring에 D-NAO-13 원문·bm_diff.py:10-13과 함께 적혀 있다.

    모듈 고유 필터는 그대로다: 매핑 소스는 SHOPPING 캠페인의 활성 그룹뿐이다
    (파워링크/브랜드검색은 상품 소재가 아님).

    `scope_ids`를 주면 스코프 재조회를 생략한다(호출부가 이미 `observation_scope()`로
    같은 값을 떴을 때 — 같은 run 안에서 두 번 물으면 답이 갈릴 수 있다).
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
        .all()
    )


def _entity_enumerated_campaigns(db: Session, scope_ids: set[str]) -> set[str]:
    """그룹 인벤토리를 **실제로 본** 캠페인 = `naver_entity`에 adgroup 행이 1개 이상 있는 캠페인.

    ★왜 이 신호인가(codex 1R P1-1): stale 정리는 "이 캠페인의 활성 쇼핑 그룹 전체를
      열거했다"를 전제로 한다. 그런데 활성 그룹 0개는 두 가지가 겹쳐 보인다 —
        ⓐ 그룹이 정말 다 꺼졌다/삭제됐다(정리해야 함)
        ⓑ `sync_naver_entity`가 이 캠페인을 아직/영영 못 채웠다(정리하면 안 됨)
      status·campaign_type을 **묻지 않고** adgroup 행 존재만 보면 둘이 갈린다: ⓐ는 off/
      deleted 행이라도 남고, ⓑ는 행 자체가 없다. 엔티티 동기화가 죽으면 그 캠페인은 통째로
      행이 사라지므로 ⓑ로 떨어져 보존된다(안전 방향).
    """
    if not scope_ids:
        return set()
    return {
        r[0]
        for r in db.query(NaverEntity.campaign_id)
        .filter(NaverEntity.entity_type == "adgroup", NaverEntity.campaign_id.in_(scope_ids))
        .distinct()
        .all()
        if r[0]
    }


def _log_observation_blind(
    db: Session, *, scope_ids: set[str], adgroups: list[NaverEntity], now,
) -> None:
    """관측 대상이 0일 때의 유성 실패 기록 — WARNING + 운영 일기(observe).

    `flight_loop._log_flight_silence`의 관례를 따른다: **병리 상태일 때만** 1행 남긴다
    (정상일 때 0행이라 소음이 되지 않는다). 다만 이 모듈은 캠페인 단위가 아니므로
    change_log가 아니라 diary observe에 남긴다(캠페인 조인에 섞이지 않게 campaign_id="").

    ★문장이 곧 진단이어야 한다: 구 코드의 INFO `그룹 0개`는 "관측했더니 0건"으로도,
      "볼 대상 자체가 없다"로도 읽혀서, 사흘간의 맹목이 안전 확인처럼 보였다.
    """
    existing = db.query(func.count(NaverAdgroupProduct.id)).scalar() or 0
    if not scope_ids:
        # 스코프 공집합 = reconcile_allowed False → 이 회차엔 확실히 아무것도 안 지운다.
        msg = (
            "관측 대상 자체가 0 — 스코프 결함 의심. 관측 스코프 캠페인이 0개"
            "(최근 7일 광고비>0 ∪ settings 행 — 둘 다 비었다). "
            f"기존 매핑 {existing}행은 **삭제하지 않고 보존**한다"
            "(2026-07-30 사고 재발 방지: 대상 0은 이상 신호이지 삭제 근거가 아니다)."
        )
    else:
        # ★여기서 "보존한다"고 단정하면 로그가 거짓말한다(codex 1R P1-1 이후): 스코프는
        #   있는데 활성 그룹만 0인 경우, 엔티티 열거에 성공한 캠페인은 **정리가 정당하다**
        #   (마지막 그룹을 의도적으로 내린 경우). 실제 처분은 캠페인별로 갈리므로 여기서는
        #   관측 사실만 남기고 처분은 요약 로그(reconciled/withheld)가 말한다.
        msg = (
            f"관측 대상 자체가 0 — 스코프 결함 의심. 관측 스코프 캠페인은 {len(scope_ids)}개인데 "
            "그 안의 활성 SHOPPING 광고그룹이 0개(entity_sync 침묵 또는 그룹 전부 off). "
            f"기존 매핑 {existing}행의 처분은 캠페인별 열거 신뢰도로 갈린다 — 엔티티 인벤토리를 "
            "못 본 캠페인은 보존, 열거에 성공한 캠페인만 정리한다."
        )
    log.warning("shopping_ad_product_sync: %s", msg)
    write_diary_entry(
        db, "observe", "", actor=ACTOR_SYSTEM,
        action=DIARY_ACTION_SCOPE_BLIND, rationale=msg, now=now,
    )


def collect_adgroup_products(
    db: Session, *,
    ads_by_adgroup: dict[str, list[dict]] | None = None,
    as_of: date | None = None,
    adgroups: list[NaverEntity] | None = None,
    budget_s: float = _RUN_BUDGET_S,
) -> CollectResult:
    """대상 광고그룹별 상품 매핑 + 수집 신뢰도(실패·이월)를 반환.

    ads_by_adgroup는 테스트·재사용 주입용(원칙18-8). 미주입 시 get_ads로 그룹마다 1콜.
    같은 그룹에서 같은 mall_product_id가 여러 소재로 중복되면 dedup(첫 이름 채택).
    개별 그룹의 조회 실패는 그 그룹만 skip(fail-open)하되 **failed에 기록**한다 — 리컨실(P2-3)이
    실패 그룹의 기존 매핑을 stale로 오인해 지우지 않게(일시 장애에 매핑 소실 금지).

    ★호출 예산(codex 1R P2): 실 API 경로에서만 호출 간 `_MIN_CALL_INTERVAL_S` 스로틀을 걸고,
      누적 경과가 `budget_s`를 넘으면 **남은 그룹을 다음 회차로 이월**하고 `truncated=True`로
      돌려준다(예외를 던지거나 중단하지 않는다 — 부분 결과도 매핑 갱신에는 쓸모가 있다).
      주입 경로(테스트)는 네트워크가 없으므로 sleep 하지 않는다.
    """
    if adgroups is None:
        adgroups = _observed_shopping_adgroups(db, as_of=as_of)
    live = ads_by_adgroup is None
    started = time.monotonic()
    result: dict[str, list[dict]] = {}
    failed: set[str] = set()
    attempted: set[str] = set()
    truncated = False
    if not adgroups:
        # ★"대상 0" ≠ "관측했는데 0건". 구 코드는 둘 다 조용히 넘겨 맹목 상태가 안전 확인처럼
        #   보였다(2026-07-31~08-02 사흘간 INFO "그룹 0개"만 찍히며 침묵).
        log.warning(
            "shopping_ad_product_sync: 관측 대상 광고그룹이 0 — 관측 스코프 결함 의심"
            "(스코프 정의는 campaign_roster.observation_campaign_ids)",
        )
    for idx, ag in enumerate(adgroups):
        aid = ag.entity_id
        if time.monotonic() - started >= budget_s:
            truncated = True
            log.warning(
                "shopping_ad_product_sync: 수집 예산 %.0fs 초과 — 남은 그룹 %d개를 다음 회차로 "
                "이월한다(이번 회차 정리 계층은 전면 보류: 부분 수집으로 stale 판정 금지)",
                budget_s, len(adgroups) - idx,
            )
            break
        if live and idx > 0:
            time.sleep(_MIN_CALL_INTERVAL_S)
        attempted.add(aid)
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
    return CollectResult(
        per_group=result, failed=failed, truncated=truncated, attempted=attempted,
        elapsed_s=time.monotonic() - started,
    )


def sync_adgroup_products(
    db: Session, *,
    ads_by_adgroup: dict[str, list[dict]] | None = None,
    as_of: date | None = None,
    budget_s: float = _RUN_BUDGET_S,
) -> dict:
    """naver_adgroup_product 그룹 단위 스냅샷 교체 + stale 리컨실(멱등, 리뷰 P2-3).

    `as_of`는 관측 스코프의 기준일(호출부가 명시적으로 넘긴다 — 조용한 `kst_today()` 폴백
    금지, codex 1R P1-2). ★단 `as_of`로 과거를 완전히 재현할 수는 없다(settings 축은 현재
    상태) — 한계는 `campaign_roster.observation_campaign_ids` docstring 참조.

    3층 정리로 stale 행 영구 잔존을 막는다:
      (0) 그룹 스냅샷 교체 — 동기화한 그룹의 행 삭제 후 재삽입(상품 이탈 반영, 기존 동작)
      (1) **관측 스코프를 벗어난** 캠페인의 행 전체 삭제(더 이상 관측 대상이 아님)
      (2) **열거를 신뢰할 수 있는 캠페인에 한해** 활성 그룹 목록에 없는 그룹의 행 삭제
          (그룹 삭제/off/캠페인의 그룹 구성 변화).

    ══ ★삭제의 비대칭 — 이 함수의 설계 원칙 ══
    **지우는 실패가 안 지우는 실패보다 훨씬 비싸다.** 지운 매핑은 되돌릴 수 없고
    (2026-07-31 07:45 KST에 276행이 실제로 소실됐다) resolver ②가 조용히 ③ 폴백으로
    강등돼 target이 드리프트한다. 반면 남은 stale 행은 다음 성공 회차가 정리한다.
    그래서 **조금이라도 불확실하면 보존한다.** 불확실의 종류는 넷:
      ⓐ 스코프 자체가 비었다(`scope_ids` 공집합)                     → (1)·(2) 전면 보류
      ⓑ 광고비 축이 통째로 침묵(`spend_axis_alive` False)            → (1)·(2) 전면 보류
      ⓒ 시간 예산 초과로 부분 수집(`truncated`)                      → (1)·(2) 전면 보류
      ⓓ 이 캠페인만 못 믿겠다(엔티티 미열거 / get_ads 실패)          → **그 캠페인만** (2) 보류

    ★ⓓ가 codex 1R P1-1의 수정점이다: 초판은 "스코프 전체에 활성 쇼핑 그룹이 0개"라는
      **전역** 조건으로 (2)를 통째로 껐다. 그러면 관리 캠페인의 마지막 그룹을 **의도적으로**
      비활성화한 경우에도 stale 매핑이 영구히 남는다. 이제 판정은 캠페인 단위이고, "열거
      성공"의 근거는 `_entity_enumerated_campaigns`(그 캠페인의 adgroup 엔티티 행 존재)다 —
      "그룹이 다 꺼졌다"(off 행이 남음)와 "엔티티 동기화가 이 캠페인을 못 봤다"(행 자체 없음)를
      가르는 신호. 활성 그룹 0 + 엔티티 열거됨 = **정리한다**.

    반환: {adgroups, mappings, products, removed, failed_adgroups, external_ad_changes,
           observation_blind, truncated, reconciled_campaigns, withheld_campaigns, elapsed_s}.
    """
    as_of = as_of or kst_today()
    scope = campaign_roster.observation_scope(db, today=as_of)
    scope_ids = set(scope.campaign_ids)
    adgroups = _observed_shopping_adgroups(db, scope_ids=scope_ids)
    # ★D-NAO-127: 직전 관측 상태를 **삭제 전에** 메모리로 뜬다(이 함수는 스냅샷 교체라
    #   아래 delete가 지나가면 어제 값이 사라진다 = 비교 대상 소멸).
    prev_by_ad = {
        r.ad_id: {
            "edit_tm": r.ad_edit_tm, "ad_bid_amt": r.ad_bid_amt,
            "use_group_bid_amt": r.use_group_bid_amt, "ad_user_lock": r.ad_user_lock,
        }
        for r in db.query(NaverAdgroupProduct).filter(NaverAdgroupProduct.ad_id.isnot(None)).all()
    }
    collected = collect_adgroup_products(
        db, ads_by_adgroup=ads_by_adgroup, adgroups=adgroups, budget_s=budget_s,
    )
    per_group, failed = collected.per_group, collected.failed
    now = kst_now()
    removed = 0

    # ★관측 맹목 판정 — 2026-07-30 사고의 감지기(전량 삭제 방지). 여전히 **전역** 판정이지만,
    #   이제 stale 정리(2)의 게이트가 아니라 ⓐ·ⓑ 전용이다(⑵의 게이트는 캠페인 단위).
    #   `scope_ids`가 비면 `adgroups`도 반드시 비지만(스코프로 필터하므로) 둘을 따로 본다:
    #   "우리가 아무 캠페인도 안 본다"와 "볼 캠페인은 있는데 활성 쇼핑 그룹이 하나도 없다"는
    #   원인이 다르고, 로그에서 구분돼야 사람이 어디를 고칠지 안다.
    observation_blind = not scope_ids or not adgroups
    if observation_blind:
        _log_observation_blind(db, scope_ids=scope_ids, adgroups=adgroups, now=now)

    # ⓐ 스코프 공집합 · ⓒ 부분 수집 — 어떤 삭제도 하지 않는다.
    reconcile_allowed = bool(scope_ids) and not collected.truncated

    # (1) 스코프 이탈 캠페인 정리.
    #   ★ⓑ 추가 가드: 이 계층만은 **광고비 축이 살아 있을 때만** 돈다. "스코프를 벗어났다"의
    #     근거가 곧 `naver_ad_daily`이기 때문이다 — 적재가 죽으면 멀쩡히 돈 쓰는 캠페인이
    #     전부 '이탈'로 보인다. (2)는 근거가 `naver_entity`라 이 축과 무관하므로 함께 묶지
    #     않는다(초판은 묶었는데, 그건 증거 출처가 다른 두 판단을 한 신호로 껐던 것이다).
    #     경보는 **실제로 지울 게 있을 때만** 낸다(없으면 판단 자체가 무의미 = 소음).
    spend_axis_blocked = False
    if reconcile_allowed:
        out_of_scope = db.query(func.count(NaverAdgroupProduct.id)).filter(
            NaverAdgroupProduct.campaign_id.not_in(scope_ids)
        ).scalar() or 0
        if out_of_scope and not scope.spend_axis_alive:
            spend_axis_blocked = True
            log.warning(
                "shopping_ad_product_sync: 최근 %d일 광고비>0 캠페인이 0인데 스코프 밖 매핑이 "
                "%d행 있다 — 광고비 축 침묵(naver_ad_daily 적재 중단 의심)일 수 있으므로 "
                "'스코프 이탈' 판정을 보류하고 보존한다(settings 행 %d개).",
                campaign_roster.OBSERVATION_COST_WINDOW_DAYS, out_of_scope, len(scope.managed),
            )
        elif out_of_scope:
            res = db.execute(delete(NaverAdgroupProduct).where(
                NaverAdgroupProduct.campaign_id.not_in(scope_ids)
            ))
            removed += res.rowcount or 0

    # (0) 그룹 스냅샷 교체(동기화 성공 그룹만).
    n_map = 0
    distinct: set[str] = set()
    for aid, rows in per_group.items():
        db.execute(delete(NaverAdgroupProduct).where(NaverAdgroupProduct.adgroup_id == aid))
        for r in rows:
            db.add(NaverAdgroupProduct(
                adgroup_id=aid,
                campaign_id=r["campaign_id"],
                mall_product_id=r["mall_product_id"],
                product_name=r["product_name"],
                # B1(D-NAO-65): 입찰 필드 미주입(기존 소비자 형식) 시 .get→None(하위호환).
                ad_id=r.get("ad_id"),
                ad_bid_amt=r.get("ad_bid_amt"),
                use_group_bid_amt=r.get("use_group_bid_amt"),
                ad_user_lock=r.get("ad_user_lock"),
                ad_edit_tm=r.get("edit_tm"),  # D-NAO-127 앵커
                synced_at=now,
            ))
            n_map += 1
            distinct.add(r["mall_product_id"])

    # (2) 열거를 신뢰할 수 있는 캠페인의 stale 그룹 정리 — 판정은 **캠페인 단위**(P1-1).
    reconciled: list[str] = []
    withheld: dict[str, str] = {}
    if reconcile_allowed:
        active_by_campaign: dict[str, set[str]] = {}
        for ag in adgroups:
            active_by_campaign.setdefault(ag.campaign_id, set()).add(ag.entity_id)
        failed_campaigns = {ag.campaign_id for ag in adgroups if ag.entity_id in failed}
        enumerated = _entity_enumerated_campaigns(db, scope_ids)
        for cid in sorted(scope_ids):
            if cid in failed_campaigns:
                # 일시 API 장애 — 이 캠페인만 stale 판정 유보(다음 성공 sync가 정리).
                withheld[cid] = "get_ads_failed"
                continue
            if cid not in enumerated:
                # 엔티티에 이 캠페인의 adgroup 행이 **하나도** 없다 = 인벤토리를 못 봤다.
                # "그룹이 없어졌다"가 아니라 "안 보인다"이므로 지울 근거가 없다.
                withheld[cid] = "entity_not_enumerated"
                continue
            gids = active_by_campaign.get(cid, set())
            cond = [NaverAdgroupProduct.campaign_id == cid]
            if gids:
                cond.append(NaverAdgroupProduct.adgroup_id.not_in(gids))
            res = db.execute(delete(NaverAdgroupProduct).where(*cond))
            removed += res.rowcount or 0
            reconciled.append(cid)
        if withheld:
            log.warning(
                "shopping_ad_product_sync: stale 정리 보류 %d캠페인(보존) — %s",
                len(withheld),
                ", ".join(f"{c[-12:]}:{r}" for c, r in sorted(withheld.items())[:10]),
            )

    db.commit()

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
    #   맹목 경로의 상세 진단은 이미 _log_observation_blind가 남겼으므로 여기선 요약만.
    #   ★소요시간을 항상 싣는다(codex 1R P2): 07:45 잡이 08:00 제안 잡을 침범하는지는
    #   숫자가 로그에 있어야 사후에 판정된다(느려진 뒤에 재보면 이미 stale).
    elapsed = collected.elapsed_s
    slow = elapsed >= _SLOW_RUN_WARN_S
    notes = []
    if observation_blind:
        notes.append("관측 대상 0 — 정리 전면 보류")
    if collected.truncated:
        notes.append("시간예산 초과·부분 수집 — 정리 전면 보류(다음 회차 이월)")
    if spend_axis_blocked:
        notes.append("광고비 축 침묵 — 스코프 이탈 정리 보류")
    if withheld:
        notes.append(f"캠페인 {len(withheld)}개 정리 보류")
    if slow:
        notes.append(f"소요 {elapsed:.0f}s ≥ {_SLOW_RUN_WARN_S:.0f}s — 하류 잡(07:50/08:00) 침범 위험")
    log_fn = (log.warning if (observation_blind or collected.truncated or slow or spend_axis_blocked)
              else log.info)
    log_fn(
        "naver_adgroup_product sync: 그룹 %d개, 매핑 %d행, 상품 %d종, 정리 %d행, 실패그룹 %d, "
        "외부변경 %d건, 소요 %.1fs%s",
        len(per_group), n_map, len(distinct), removed, len(failed), detected["recorded"],
        elapsed, f" [{' / '.join(notes)}]" if notes else "",
    )
    return {"adgroups": len(per_group), "mappings": n_map, "products": len(distinct),
            "removed": removed, "failed_adgroups": len(failed),
            "external_ad_changes": detected["recorded"],
            # additive: 호출부(스케줄러 로그)·테스트가 신뢰도를 값으로 확인할 수 있게.
            "observation_blind": observation_blind,
            "truncated": collected.truncated,
            "reconciled_campaigns": len(reconciled),
            "withheld_campaigns": len(withheld),
            "elapsed_s": round(elapsed, 3)}
