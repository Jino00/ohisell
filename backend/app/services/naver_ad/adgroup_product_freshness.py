# adgroup_product_freshness.py — `naver_adgroup_product`를 "현재 유효한 매핑"으로 읽는 단일 진입점.
#
# ══ 왜 이 모듈이 있나 (2026-08-03, codex 3R P1) ══
# 이 테이블의 의미가 바뀌었다. 2026-08-03 이전에는 sync가 스냅샷 교체(delete+insert)를 해서
# "지금의 매핑"이었지만, 2026-07-31 전량 삭제 사고 이후 sync는 **아무것도 지우지 않는**
# 누적 upsert가 됐다(shopping_ad_product_sync 모듈 docstring). 그래서 테이블은
# **"역대 관측의 합집합"** 이 됐는데 소비자들은 여전히 현재값으로 읽고 있었다.
#
# 그 괴리가 실제로 돈 쓰는 경로를 오염시킨다(codex가 추적한 경로):
#   · `campaign_target_resolver`가 캠페인의 **누적 상품 전부**로 target ROAS를 계산 →
#     `auto_operator` → 실행 직전 harness까지 그 값이 간다.
#   · 상품 P가 캠페인 A→B로 이동하면 A의 옛 행과 B의 새 행이 공존 →
#     `product_campaign_share`가 **두 캠페인으로 세고** `today_proxy_revenue`가 매출을 분할 →
#     `budget_pacing`이 그 오염된 값으로 **예산 증액**을 판단한다.
#   · unique 키가 `(adgroup_id, mall_product_id)`뿐이라 같은 `ad_id`가 새 그룹에서 관측되면
#     양쪽 행이 공존 → `cold_start_bid_lane`이 **옛 campaign/adgroup 문맥으로 라이브 ad_id에
#     입찰**할 수 있다.
#
# ══ ★이 필터의 성격: 완벽한 stale 판별이 아니라 **보수적 배제**다 ══
# `synced_at`은 "마지막으로 관측된 시각"일 뿐이다. 오래된 `synced_at`은 세 가지를 뭉뚱그린다:
#   ⓐ 실제로 사라진 매핑(우리가 배제하고 싶은 것)
#   ⓑ 네이버 부분 HTTP-200으로 이번에 안 보인 매핑
#   ⓒ get_ads 실패 / 시간 예산 이월로 **아직 안 본** 매핑
# 이 필터는 셋을 구분하지 못하고 **전부 배제**한다. 그래도 이것이 옳은 선택인 이유:
#   · ⓑⓒ를 배제하면 그 캠페인·그룹의 판단이 **폴백으로 떨어진다.** 그런데 그 폴백은
#     매핑이 0행일 때 이미 일어나던 **기존 동작**이다 — 새 위험이 아니다.
#   · 반대로 ⓐ를 통과시키면 위에 적은 오염이 그대로 돈 쓰는 경로로 간다.
#   · 그리고 삭제는 선택지가 아니다(2026-07-31에 276행을 실제로 날렸다).
# ★다음 사람에게: "이 필터는 불완전하다"는 이유로 필터를 걷어내거나 삭제 로직으로
#   되돌리지 말 것. 불완전함은 알고 고른 것이고, 대안(삭제)은 이미 실패했다.
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Query, Session

from app.models import NaverAdgroupProduct
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# ── 신선도 창(일) ──────────────────────────────────────────────────────────────
# ★근거(임의값 아님):
#   ①**커서 한 바퀴 주기**가 하한을 정한다. sync는 07:45 일 1회이고 한 회차 예산은 600s,
#     한 호출 최악 소요 헤드룸 93s를 빼면 507s가 실제 수집 시간이다. 그룹당 소요는
#     (API 지연 + 스로틀 0.2s)이므로, 2026-08-03 prod 실측 규모 242그룹을 한 회차에 다 돌려면
#     그룹당 507/242 ≈ 2.1s 이내면 된다. 즉 **정상이면 하루에 한 바퀴**이고, 지연이 2.1s를
#     넘겨야 비로소 여러 날에 걸친다. (그룹당 실측 지연은 아직 없다 — `elapsed_s`가 배포 후
#     첫 실측을 만든다. 그래서 값을 지연 추정치로 계산하지 않고 아래 ②로 정한다.)
#   ②**관측 스코프 창과 일치시킨다**: `campaign_roster.OBSERVATION_COST_WINDOW_DAYS`가 7일이다.
#     캠페인이 스코프에서 빠지면(7일 무지출 + settings 행 없음) 그 캠페인의 매핑은 더 이상
#     갱신되지 않는데, 같은 7일을 쓰면 **"관측을 그만둔다"와 "매핑이 무효가 된다"가 같은
#     시계로 움직인다.** 두 창이 어긋나면 "스코프 밖인데 유효한 매핑" 또는 그 반대라는
#     설명 불가능한 중간 상태가 생긴다.
#   ③여유 관점: 정상 주기가 1일이므로 7일은 **연속 6회차 실패·부분 이월을 견딘다.** 그 안에
#     복구되면 유효 매핑이 배제되는 일은 없다.
#   ④★창을 시간 단위로 줄이지 말 것: `synced_at`은 sync가 `kst_now()`(KST)로 쓰지만, 다른
#     경로로 만들어진 행은 모델의 `server_default=func.now()`(=**UTC**,
#     [[sqlite-server-default-now-is-utc]])라 **9시간 어긋난다.** 7일 창에서는 무해하지만
#     시간 단위 창에서는 그 스큐가 곧바로 오판이 된다.
FRESHNESS_WINDOW_DAYS = 7


def cutoff(now: datetime | None = None) -> datetime:
    """이 시각 이후에 관측된 행만 '현재 유효'로 본다."""
    return (now or kst_now()) - timedelta(days=FRESHNESS_WINDOW_DAYS)


def fresh_condition(now: datetime | None = None):
    """SQLAlchemy 조건식 — `NaverAdgroupProduct`를 읽는 **모든** 쿼리에 이걸 붙인다.

    쿼리 shape(어떤 컬럼을 뽑는지·어떻게 조인하는지)은 소비자마다 다르므로 행을 돌려주는
    대신 조건식을 돌려준다. 신선도 기준의 소유자는 이 모듈 하나다 — 소비자가 각자
    `synced_at >= ...`를 쓰기 시작하면 또 어긋난다(그게 이번 사고의 패턴이다).
    """
    return NaverAdgroupProduct.synced_at >= cutoff(now)


def fresh_rows(db: Session, now: datetime | None = None) -> Query:
    """신선한 매핑 행 전체 쿼리(추가 필터는 호출부가 `.filter()`로 얹는다)."""
    return db.query(NaverAdgroupProduct).filter(fresh_condition(now))


def fresh_rows_by_ad_id(
    db: Session, ad_ids: list[str] | None = None, *, now: datetime | None = None,
) -> dict[str, NaverAdgroupProduct]:
    """`ad_id` → 그 소재의 **현재 유효한** 매핑 행 1개.

    ★왜 dedup이 필요한가: unique 키가 `(adgroup_id, mall_product_id)`뿐이라 **같은 `ad_id`가
      여러 행에 존재할 수 있다.** 소재가 그룹 A→B로 옮겨가면 A의 옛 행과 B의 새 행이 공존하고,
      `ad_id`로 campaign/adgroup 문맥을 되찾는 소비자(cold_start_bid_lane·today_hourly_sweep·
      perf_campaign_harness 등)가 **옛 문맥을 집을 수 있다.**

    결정 규칙: **`synced_at`이 더 최근인 행**을 채택한다(같으면 `id`가 큰 쪽 = 나중에 삽입된 행).
    ★양쪽 다 신선한 중복은 진짜 이상 상태다 — 조용히 넘기지 않고 WARNING을 남긴다.
      (같은 소재가 두 그룹에 동시에 살아 있을 수는 없으므로, 우리 관측이나 네이버 응답 둘 중
      하나가 잘못됐다는 신호다.)
    """
    q = fresh_rows(db, now).filter(NaverAdgroupProduct.ad_id.isnot(None))
    if ad_ids is not None:
        wanted = list(dict.fromkeys(ad_ids))
        if not wanted:
            return {}
        q = q.filter(NaverAdgroupProduct.ad_id.in_(wanted))

    out: dict[str, NaverAdgroupProduct] = {}
    for row in q.all():
        prev = out.get(row.ad_id)
        if prev is None:
            out[row.ad_id] = row
            continue
        log.warning(
            "adgroup_product_freshness: 소재 %s가 **양쪽 다 신선한** 매핑 2행에 존재한다 — "
            "adgroup %s(synced %s) vs %s(synced %s). 같은 소재가 두 그룹에 동시에 살 수는 "
            "없으므로 관측 또는 네이버 응답이 잘못됐다는 신호다. 더 최근 관측을 채택한다.",
            row.ad_id, prev.adgroup_id, prev.synced_at, row.adgroup_id, row.synced_at,
        )
        if (row.synced_at, row.id) > (prev.synced_at, prev.id):
            out[row.ad_id] = row
    return out
