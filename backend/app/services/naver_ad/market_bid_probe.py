# market_bid_probe.py — SA2 market_bid_probe (CS 스프린트, 단일 책임: 쇼핑 소재 시장가 관측)
# 역할(SA·읽기 전용): 네이버 **쇼핑검색광고 전용** 추정 API(/npla-estimate/*)로 소재(nccAdId)별
#   순위 1~4 필요 입찰가 + 최소노출입찰가를 조회해 일별로 축적한다. 광고 API 쓰기 없음.
#
# ★왜 새로 필요한가(이 스프린트의 존재 이유): 기존 코드의 estimate 배선은 전부 파워링크 **키워드**
#   grain(/estimate/*, key=nccKeywordId)이라 쇼핑 소재에는 쓸 수 없었다. 그 결과 콜드 소재의
#   입찰은 exploration.adaptive_step의 **눈먼 스텝**(current×1.1, BM 대행사 밴드 p50 프라이어)으로만
#   올랐다 — 실제 시장가를 한 번도 본 적이 없다. 이 모듈이 그 눈을 뜨게 한다.
#
# ★ID 체계 함정(라이브 실측 2026-07-27, 원칙22): 반드시 **소재 ID(nccAdId)**로 조회해야 실값이
#   나온다. `.../product` 경로에 스마트스토어 채널상품ID(예 13684462601)를 넣으면 전부 50원(floor)
#   = 무의미. 그 경로는 응답의 `productId`(예 89927344602 = 네이버쇼핑 원상품ID) 체계를 요구하며
#   우리가 보유한 ID로는 못 쓴다. nccAdId는 naver_adgroup_product.ad_id에만 저장돼 있다
#   (naver_entity에는 ad 타입이 없음 — entity_type ∈ {campaign, adgroup, keyword}).
from __future__ import annotations

import logging
import time
from datetime import date

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import NaverBidEstimateDaily
# ★_estimate_post 재사용(자격증명 os.getenv 재독·429/5xx backoff 포함). naver_sa_ad_fetcher를
#   편집하지 않고 import만 하는 이유: 동시 진행 중인 DV 스프린트가 같은 파일을 수정 중이라
#   충돌면을 0으로 두기 위함(신규 모듈 위주 작성 지침). 비공개 심볼이지만 순수 전송 헬퍼다.
from app.services.naver_sa_ad_fetcher import _estimate_post

log = logging.getLogger(__name__)

# ── 라이브 실측 확정 상수(2026-07-27, prod에서 직접 400 응답 역추적 — 추정 금지 원칙) ──
# position 유효 범위: 1~4. **5 이상은 400**이며, 그 에러 메시지가 하필
#   "invalid collections size"라서 배치 크기 초과로 오독하기 쉽다(실제로 그렇게 오독된 기록 있음).
#   → position 오류와 배치 오류는 같은 문구를 쓴다. 둘을 혼동하지 말 것.
NPLA_VALID_POSITIONS = (1, 2, 3, 4)
# 배치 상한: average-position-bid/id는 items 201개부터 400(실측 200 OK / 201 fail).
#   exposure-minimum-bid/id는 300까지도 200 OK였으나(상한 미발견) 같은 값으로 통일해 운용한다.
NPLA_MAX_ITEMS = 200
# 쇼핑 최소 입찰가(원) — 이 값이 나오면 "시세 정보 없음"(신규 소재 등)의 신호로 취급한다.
#   출처: exploration._EXPLORATION_MIN_BID_SHOPPING(prod 158그룹 bid=50 라이브 실증).
SHOPPING_FLOOR_BID = 50
# 최소노출입찰가를 담는 가상 position 값(테이블 grain 통일용 — 실제 API position 아님).
POSITION_EXPOSURE_MIN = 0
# 요청 간 간격(초) — 읽기 전용 추정 호출이지만 계정 단위 rate limit 배려.
_SLEEP_BETWEEN_CALLS = 0.3


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def fetch_position_ladder(ad_ids: list[str], device: str) -> dict[str, dict[int, int]]:
    """소재별 순위 1~4 필요 입찰가. POST /npla-estimate/average-position-bid/id.

    body: {"device": "MOBILE"|"PC", "items": [{"key": nccAdId, "position": 1~4}]}
    반환: {ad_id: {position: bid}} — 조회 실패한 (소재, 순위)는 **그냥 빠진다**(전체 중단 금지).

    position마다 별도 배치로 나눈다(한 배치에 여러 position을 섞어도 API는 받지만, 실패 시
    영향 범위를 position 단위로 좁혀 부분 실패를 격리하기 위함).
    """
    out: dict[str, dict[int, int]] = {}
    if not ad_ids:
        return out
    for position in NPLA_VALID_POSITIONS:
        for chunk in _chunks(ad_ids, NPLA_MAX_ITEMS):
            items = [{"key": a, "position": position} for a in chunk]
            try:
                resp = _estimate_post(
                    "/npla-estimate/average-position-bid/id", {"device": device, "items": items}
                )
                if resp.status_code != 200:
                    log.warning(
                        "market_bid_probe: average-position-bid %s pos=%d %d — 이 배치 건너뜀: %s",
                        device, position, resp.status_code, resp.text[:200],
                    )
                    continue
                for e in resp.json().get("estimate", []):
                    ad_id = e.get("nccAdId")
                    bid = e.get("bid")
                    if ad_id and bid is not None:
                        out.setdefault(ad_id, {})[position] = int(bid)
            except Exception as err:  # noqa: BLE001 — 부분 실패 격리(레인 전체 중단 금지)
                log.warning(
                    "market_bid_probe: average-position-bid %s pos=%d 실패(배치 건너뜀): %s",
                    device, position, err,
                )
            time.sleep(_SLEEP_BETWEEN_CALLS)
    return out


def fetch_exposure_min(ad_ids: list[str], device: str) -> dict[str, int]:
    """소재별 최소노출입찰가. POST /npla-estimate/exposure-minimum-bid/id.

    body: {"device": ..., "items": [nccAdId, ...]}  ← 순위 사다리와 달리 **문자열 배열**이다.
    반환: {ad_id: bid}. 실패한 배치는 건너뛴다.

    ★의미 주의(라이브 실측 2026-07-27): 이 값은 순위 사다리의 **하한이 아니다**. 실측에서
      expmin이 사다리 중간에 앉는 경우가 흔했다(예 nad-…558730: pos1=3420·pos2=3100·pos3=2810·
      pos4=2630인데 expmin=3270 — pos2~4보다 높다). 즉 "이 값 미만이면 노출 불가"로 읽으면
      실제로 그 순위에 노출 중인 소재까지 전부 차단된다. 두 엔드포인트는 서로 다른 추정 모델이며,
      expmin은 **참고 신호**로만 기록하고 게이트로 쓰지 않는다(cold_start_bid_decider 참조).
    """
    out: dict[str, int] = {}
    if not ad_ids:
        return out
    for chunk in _chunks(ad_ids, NPLA_MAX_ITEMS):
        try:
            resp = _estimate_post(
                "/npla-estimate/exposure-minimum-bid/id", {"device": device, "items": chunk}
            )
            if resp.status_code != 200:
                log.warning(
                    "market_bid_probe: exposure-minimum-bid %s %d — 이 배치 건너뜀: %s",
                    device, resp.status_code, resp.text[:200],
                )
                continue
            for e in resp.json().get("estimate", []):
                ad_id = e.get("nccAdId")
                bid = e.get("bid")
                if ad_id and bid is not None:
                    out[ad_id] = int(bid)
        except Exception as err:  # noqa: BLE001 — 부분 실패 격리
            log.warning("market_bid_probe: exposure-minimum-bid %s 실패(배치 건너뜀): %s", device, err)
        time.sleep(_SLEEP_BETWEEN_CALLS)
    return out


def detect_floor(ladder: dict[int, int]) -> tuple[bool, str]:
    """시세 무의미(floor) 판정 — 이 값을 진짜 시장가로 오인하면 안 되는 케이스.

    floor로 보는 조건(둘 중 하나):
      ① 관측된 입찰가 중 하나라도 쇼핑 최소입찰가(50원) 이하 — 시세 산정 자체가 안 된 소재.
      ② 순위 1~4가 **전부 같은 값** — 순위별 차등이 없다 = 경쟁 정보 없음(신규 소재에서 발생).
    반환: (is_floor, reason).
    """
    if not ladder:
        return True, "시세 응답 없음(관측 0건)"
    bids = list(ladder.values())
    if min(bids) <= SHOPPING_FLOOR_BID:
        return True, f"쇼핑 최소입찰가 이하 관측(min={min(bids)}≤{SHOPPING_FLOOR_BID}) — 시세 미산정"
    if len(ladder) >= 2 and len(set(bids)) == 1:
        return True, f"순위 1~4 전부 동일값({bids[0]}원) — 순위별 차등 없음(경쟁 정보 부재)"
    return False, ""


def probe_market_bids(ad_ids: list[str], device: str) -> dict[str, dict]:
    """소재별 시장가 관측 1회분(순위 사다리 + 최소노출 + floor 판정).

    반환: {ad_id: {"ladder": {pos: bid}, "exposure_min": int|None,
                   "is_floor": bool, "floor_reason": str}}
    """
    ladders = fetch_position_ladder(ad_ids, device)
    expmins = fetch_exposure_min(ad_ids, device)
    out: dict[str, dict] = {}
    for ad_id in ad_ids:
        ladder = ladders.get(ad_id, {})
        is_floor, reason = detect_floor(ladder)
        out[ad_id] = {
            "ladder": ladder,
            "exposure_min": expmins.get(ad_id),
            "is_floor": is_floor,
            "floor_reason": reason,
        }
    return out


def collect_daily(
    db: Session, rows: list[tuple[str, str, str]], today: date,
    *, devices: tuple[str, ...] = ("MOBILE", "PC"),
) -> dict:
    """일 1회 시장가 사다리 수집 → naver_bid_estimate_daily 적재(당일 재실행 시 교체).

    rows: [(ad_id, adgroup_id, campaign_id), ...]. devices 각각에 대해 사다리+최소노출을 조회한다.
    반환: {"ads": n, "rows": n, "floor_ads": n, "devices": [...]}.

    ★이 함수는 예외를 밖으로 던지지 않는다 — 기존 일별 수집 크론 안에서 호출되므로
      여기서 실패해도 다른 수집을 죽이면 안 된다(호출부도 try/except로 한 번 더 격리한다).
    """
    result = {"ads": len(rows), "rows": 0, "floor_ads": 0, "devices": list(devices)}
    if not rows:
        return result
    meta = {ad_id: (adgroup_id, campaign_id) for ad_id, adgroup_id, campaign_id in rows}
    ad_ids = list(meta.keys())

    # 당일 재실행 멱등: 오늘자 행을 먼저 지우고 다시 넣는다(유니크 충돌 방지).
    db.execute(delete(NaverBidEstimateDaily).where(NaverBidEstimateDaily.date == today))

    floor_ads: set[str] = set()
    for device in devices:
        probed = probe_market_bids(ad_ids, device)
        for ad_id, info in probed.items():
            adgroup_id, campaign_id = meta[ad_id]
            if info["is_floor"]:
                floor_ads.add(ad_id)
            for position, bid in sorted(info["ladder"].items()):
                db.add(NaverBidEstimateDaily(
                    date=today, ad_id=ad_id, adgroup_id=adgroup_id, campaign_id=campaign_id,
                    device=device, position=position, bid=int(bid), is_floor=info["is_floor"],
                ))
                result["rows"] += 1
            if info["exposure_min"] is not None:
                db.add(NaverBidEstimateDaily(
                    date=today, ad_id=ad_id, adgroup_id=adgroup_id, campaign_id=campaign_id,
                    device=device, position=POSITION_EXPOSURE_MIN,
                    bid=int(info["exposure_min"]), is_floor=info["is_floor"],
                ))
                result["rows"] += 1
    db.commit()
    result["floor_ads"] = len(floor_ads)
    log.info(
        "market_bid_probe: 시장가 사다리 수집 — 소재 %d개·행 %d개·floor %d개(devices=%s)",
        result["ads"], result["rows"], result["floor_ads"], ",".join(devices),
    )
    return result


def load_today_ladder(
    db: Session, ad_id: str, device: str, today: date,
) -> dict:
    """오늘자 축적분에서 한 소재의 사다리를 되읽는다(SA3 입력용 — API 재호출 없음).

    반환: {"ladder": {pos: bid}, "exposure_min": int|None, "is_floor": bool}.
    행이 없으면 ladder={} · is_floor=True(시세 없음과 동일 취급).
    """
    rows = db.query(NaverBidEstimateDaily).filter(
        NaverBidEstimateDaily.date == today,
        NaverBidEstimateDaily.ad_id == ad_id,
        NaverBidEstimateDaily.device == device,
    ).all()
    ladder = {r.position: r.bid for r in rows if r.position != POSITION_EXPOSURE_MIN}
    expmin = next((r.bid for r in rows if r.position == POSITION_EXPOSURE_MIN), None)
    is_floor = bool(rows) and any(r.is_floor for r in rows)
    if not rows:
        is_floor = True
    return {"ladder": ladder, "exposure_min": expmin, "is_floor": is_floor}
