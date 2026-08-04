# ad_settings_collect.py — 쿠팡 광고 **설정** 수집 공용 모듈 (트랙 coupang-ad-change-log).
#
# 두 페처(오픽스 ad_cost_browser_fetcher / 오하이테크 ohitech_ad_fetcher)가 함께 쓴다.
# 엔드포인트가 계정 무관하게 같기 때문이다(오픽스 channel=3P / 오하이테크 channel=Retail,
# 2026-08-04 라이브 실측).
#
# ★왜 페처에 얹는가(자기 크론이 아니라): advertising.coupang.com은 xauth Akamai가 headless를
#   Access Denied로 막는다(coupang_auth.py 실측). 즉 **창이 반드시 뜬다**. 2026-07-27에
#   "창을 스스로 띄우지 않고 버튼 누를 때만 뜬다"로 이미 정리된 구조라, 여기에 콜 몇 개를
#   더 얹으면 창이 뜨는 횟수가 **0회 늘어난다**.
#   주기가 느려도 발생 시각은 안 잃는다 — 쿠팡 updatedAt이 캠페인·광고그룹 양쪽에 있다.
#
# ★수집 실패는 회차를 실패시키지 않는다. 이건 곁다리(광고비가 머니 경로다).
#   실패하면 None을 주고 호출부는 조용히 넘어간다 — 로그에는 남는다.
from __future__ import annotations

import json
from typing import Any

import requests

CAMPAIGNS_URL = "https://advertising.coupang.com/marketing/tetris-api/campaigns"
PAGE_SIZE = 100
# 전량 순회 상한. 오하이테크가 525건(6페이지)이라 넉넉하다. 무한 루프 방지용 안전판이지
# 정책이 아니다 — 걸리면 로그로 드러난다(조용한 절단 금지).
MAX_PAGES = 20

# ★캠페인 목록은 **goalType별로 따로 물어야 한다**(2026-08-04 실측).
#   같은 엔드포인트인데 goalType=SALES면 오픽스 16건, NCA면 4건이 온다 — 합집합이 아니다.
#   처음엔 `goalType: null`로 바꿔보고 "필터가 아무것도 안 숨긴다"고 결론냈는데, null은
#   **필터 해제가 아니라 기본값(SALES)**이었다. 그래서 신규 구매 고객 확보(NCA) 캠페인
#   4건(오픽스)·48건(오하이테크)이 통째로 빠져 있었다 — 오픽스 광고비의 10.7%.
#   ★비워보고 넘어가지 말고 **값을 실제로 훑어서** 확인할 것.
#   실측: SALES·NCA만 유효. BRAND/AWARENESS/REACH 등 14종은 전부 0건이었다.
#   ⚠️단 "0건"은 '그런 캠페인이 없다'와 '그런 이름의 goalType이 아니다'를 구분하지 못한다.
#     쿠팡이 새 목적을 추가하면 이 목록에 넣기 전까지 안 보인다(알려진 한계).
GOAL_TYPES = ("SALES", "NCA")

# 대시보드가 실제로 보내는 payload 그대로(추측 금지 — 2026-08-04 캡처본에서 옮김).
_BASE_PAYLOAD: dict[str, Any] = {
    "isDeleted": False,
    "sortedBy": "IS_ACTIVE",
    "isSortDesc": False,
    "budgetTypes": ["LIFETIME", "DAILY", "DAILY_SOFT", "MONTHLY_FIXED", "MONTHLY_CUSTOM"],
    "isActive": None,
    "name": "",
    "creationContext": None,
    "objective": None,
    "primaryOrderBy": "DEFAULT",
    "goalType": "SALES",
    "targetCampaignId": None,
    "vendorItemId": None,
    "tdeCardTypes": [],
    "isNcaLearningPhaseGroup": True,
    "operatorTypes": ["ADVERTISER", "AGENCY"],
}

_FETCH_JS = """async ([url, payload]) => {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 30000);
  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: {'content-type': 'application/json', 'accept': 'application/json, text/plain, */*'},
      body: JSON.stringify(payload),
      credentials: 'include',
      signal: ctrl.signal,
    });
    return {status: r.status, body: await r.text()};
  } catch (e) {
    return {status: 0, body: String(e)};
  } finally { clearTimeout(t); }
}"""


def _payload(page_no: int, *, is_active: bool | None, goal_type: str) -> dict:
    p = dict(_BASE_PAYLOAD)
    p["isActive"] = is_active
    p["goalType"] = goal_type
    p["pagination"] = {"page": page_no, "size": PAGE_SIZE}
    return p


def _fetch_page(page, page_no: int, *, is_active: bool | None, goal_type: str, log) -> dict | None:
    res = page.evaluate(_FETCH_JS,
                        [CAMPAIGNS_URL, _payload(page_no, is_active=is_active, goal_type=goal_type)])
    status = (res or {}).get("status")
    body = (res or {}).get("body") or ""
    if status != 200:
        log.warning("광고 설정 조회 status=%s (page=%s active=%s goal=%s) — %s",
                    status, page_no, is_active, goal_type, body[:120].replace("\n", " "))
        return None
    # ★로그인 HTML은 200으로도 온다(쿠팡 실측) — JSON 파싱 실패로 걸러진다.
    try:
        j = json.loads(body)
    except ValueError:
        log.warning("광고 설정 조회가 JSON이 아니다(세션 만료 가능, page=%s).", page_no)
        return None
    if not isinstance(j, dict) or "campaigns" not in j:
        log.warning("광고 설정 응답에 campaigns가 없다(page=%s).", page_no)
        return None
    return j


def _fetch_all_pages(page, *, is_active: bool | None, goal_type: str, log) -> list[dict] | None:
    """★`hasNextPage`를 믿지 않는다 — **`totalCount`가 권위값이다.**

    2026-08-04 실측(광고그룹 204811906의 `/ads`): `size=100`으로 받으면 100건이 오는데
    `pageInfo`는 `{totalCount: 447, hasNextPage: False}`다. hasNextPage만 보고 끊으면
    **347건이 조용히 사라지고 로그에도 안 남는다**. 실제로 이 트랙의 첫 비용 측정이
    오픽스 소재를 543개가 아니라 196개로 과소 보고했다(같은 함정에 내가 물렸다).

    campaigns 엔드포인트는 지금까지 hasNextPage가 정직했지만(525건 수신=totalCount 525),
    같은 서버의 형제 엔드포인트가 거짓말하는 이상 그 정직함에 기대지 않는다.
    """
    out: list[dict] = []
    total: int | None = None
    for n in range(MAX_PAGES):
        j = _fetch_page(page, n, is_active=is_active, goal_type=goal_type, log=log)
        if j is None:
            return None if n == 0 else out   # 1페이지도 못 받으면 실패, 중간이면 받은 만큼
        batch = j.get("campaigns") or []
        out.extend(batch)
        total = (j.get("pageInfo") or {}).get("totalCount")
        if total is None:
            # totalCount가 없으면 판단 근거가 없다 — hasNextPage로 폴백하되 그 사실을 남긴다.
            log.warning("pageInfo.totalCount 부재(active=%s) — hasNextPage로 폴백한다.", is_active)
            if not (j.get("pageInfo") or {}).get("hasNextPage"):
                return out
        elif len(out) >= total:
            return out
        elif not batch:
            # 더 줄 게 있다는데(total 미달) 빈 페이지가 왔다 = 서버가 멈춘 것. 조용히 덮지 않는다.
            log.warning("광고 설정 조회가 %d/%s에서 빈 페이지를 냈다(active=%s) — 일부만 수집됐다.",
                        len(out), total, is_active)
            return out
    log.warning("광고 설정 순회가 상한 %d페이지에 걸렸다 — %d/%s만 수집됐다(active=%s).",
                MAX_PAGES, len(out), total, is_active)
    return out


def _fetch_by_goals(page, *, is_active: bool | None, log) -> tuple[list[dict], dict[str, int]]:
    """GOAL_TYPES를 각각 조회해 합친다. id 기준 중복 제거(같은 캠페인이 두 목적에 뜨진 않지만 방어).

    ★한 goalType이 실패해도 나머지는 살린다 — 다만 몇 건을 못 받았는지 로그로 드러난다.
    """
    merged: dict[str, dict] = {}
    per_goal: dict[str, int] = {}
    for g in GOAL_TYPES:
        rows = _fetch_all_pages(page, is_active=is_active, goal_type=g, log=log)
        if rows is None:
            log.warning("goalType=%s 조회 실패(active=%s) — 이 목적은 이번 회차에서 빠진다.", g, is_active)
            per_goal[g] = -1
            continue
        per_goal[g] = len(rows)
        for c in rows:
            cid = c.get("id")
            if cid is not None:
                merged[str(cid)] = c
    return list(merged.values()), per_goal


def collect(page, *, log) -> dict | None:
    """전량(A축: 신규·On/Off·삭제) + 활성(B축: 내용 diff) 두 벌.

    D-CAC-3(Jino): "새로 생긴 캠페인이 있는지, Off가 된 캠페인이 있는지를 파악하고
    캠페인의 내용 변경에 대해서는 On되어 있는 캠페인만 보면 되는거야."
    활성만 따로 받는 이유: 오하이테크가 525건인데 활성은 16건이라 서버 필터로 1콜에 끝난다.
    """
    # ★goalType별로 따로 물어서 합친다(GOAL_TYPES 주석 참조). 한 번에 다 주지 않는다.
    all_rows, per_goal = _fetch_by_goals(page, is_active=None, log=log)
    if not all_rows:
        log.warning("광고 설정 전량 조회 실패 — 이번 회차는 설정 수집을 건너뛴다.")
        return None      # ★빈 목록을 올리면 서버가 '전부 삭제'로 오독할 수 있다(서버도 막지만 여기서도 막는다)
    active_rows, _ = _fetch_by_goals(page, is_active=True, log=log)
    if active_rows is None:
        log.warning("광고 설정 활성 조회 실패 — 내용 diff 없이 존재·On/Off만 올린다.")
        active_rows = []
    log.info("광고 설정 수집: 전량 %d건(%s) · 활성 %d건", len(all_rows),
             " ".join(f"{g}={n}" for g, n in per_goal.items()), len(active_rows))
    # 쿠팡이 직접 주는 변경 이력(전/후 값 + 실행 시각 + 90일 소급). 실패해도 나머지는 올린다.
    events = _fetch_history(page, [str(c.get("id")) for c in all_rows if c.get("id") is not None],
                            days=HISTORY_DAYS, log=log) or []
    # ★전량은 **얇게** 보낸다. 서버(A축)가 쓰는 건 이 다섯 필드뿐인데 full payload로 보내면
    #   525건 ≈ 1MB다. 내용 diff는 어차피 활성분(B축)으로만 한다(D-CAC-3).
    # 소재(옵션ID) — 활성 캠페인만. 실패해도 나머지는 올린다.
    try:
        ads = _collect_ads(page, active_rows, log=log)
    except Exception as e:  # noqa: BLE001
        log.warning("소재 수집 실패(회차는 계속): %s", str(e)[:140])
        ads = []
    return {"all": [_thin(c) for c in all_rows], "active": active_rows,
            "events": events, "ads": ads}


_THIN_KEYS = ("id", "name", "isActive", "updatedAt", "createdAt")


def _thin(c: dict) -> dict:
    return {k: c.get(k) for k in _THIN_KEYS}


# ── 소재(광고 상품) 목록 ──────────────────────────────────────────────
# `VIID` 이벤트는 개수만 준다. 이 목록이 **어떤 옵션ID인지**를 채운다.
ADS_URL = "https://advertising.coupang.com/marketing/tetris-api/%s/ads"
ADS_SIZE = 500          # ★size를 크게 줘야 한 번에 온다(size=100이면 447건 중 100건만 온다)
ADS_MAX_PAGES = 10


def _fetch_ads(page, adgroup_id: str, *, log) -> list[dict] | None:
    """한 광고그룹의 소재 전량. ★`totalCount`가 권위값이다(`hasNextPage`는 거짓말한다)."""
    got: list[dict] = []
    total = None
    for n in range(ADS_MAX_PAGES):
        res = page.evaluate(_FETCH_JS, [ADS_URL % adgroup_id, {
            "isDeleted": False, "pagination": {"page": n, "size": ADS_SIZE},
            "sortedBy": "ID", "isSortDesc": True,
        }])
        if (res or {}).get("status") != 200:
            log.warning("소재 조회 status=%s (그룹 %s p%s)", (res or {}).get("status"), adgroup_id, n)
            return got or None
        try:
            j = json.loads((res or {}).get("body") or "")
        except ValueError:
            log.warning("소재 응답이 JSON이 아니다(그룹 %s).", adgroup_id)
            return got or None
        batch = j.get("ads") or []
        got.extend(batch)
        total = (j.get("pageInfo") or {}).get("totalCount")
        if total is None or len(got) >= total or not batch:
            break
    if total is not None and len(got) < total:
        log.warning("소재 %s: %d/%d만 받았다 — 조용한 절단 방지 경고.", adgroup_id, len(got), total)
    return got


def _collect_ads(page, active_rows: list[dict], *, log) -> list[dict]:
    """활성 캠페인의 모든 광고그룹 소재. 서버가 쓰는 형태로 얇게 만들어 보낸다."""
    out: list[dict] = []
    for c in active_rows:
        cid = str(c.get("id"))
        for g in (c.get("groupList") or []):
            gid = str(g.get("id"))
            if not gid or gid == "None":
                continue
            ads = _fetch_ads(page, gid, log=log)
            if ads is None:
                continue
            for a in ads:
                out.append({
                    "campaign_id": cid, "adgroup_id": gid,
                    "ad_node_id": str(a.get("adNodeId") or ""),
                    "vendor_item_id": a.get("vendoritemid"),
                    "item_name": (a.get("itemName") or "")[:200],
                    "is_active": bool(a.get("isActive")),
                    "is_suspended": bool(a.get("isSuspended")),
                    "pricing_override": a.get("pricingOverride"),
                })
    log.info("소재 수집: %d건", len(out))
    return out


# ── 쿠팡이 직접 주는 변경 이력 ────────────────────────────────────────
# `/marketing/change-history` 화면이 쓰는 API. 스냅샷 diff와 달리 **전→후 값**과 **실행 시각**을
# 주고 **소급 조회**가 된다(90일 실측 OK, 1년은 500).
HISTORY_URL = "https://advertising.coupang.com/marketing/tetris-api/change-history/events-simple"
HISTORY_DAYS = 90        # 실측 상한 안쪽. 넘기면 500(`Cannot read properties of null`).
HISTORY_CHUNK = 200      # campaignIds를 한 번에 몇 개씩 물을지(오하이테크 525개 대비).


def _fetch_history(page, campaign_ids: list[str], *, days: int, log) -> list[dict] | None:
    """최근 `days`일 변경 이벤트. 실패하면 None(회차는 계속된다 — 곁다리다)."""
    if not campaign_ids:
        return []
    # 날짜는 KST 기준 YYYYMMDD를 그대로 쓴다(화면이 보내는 형식과 동일).
    import datetime as _dt
    today = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=9))).date()
    frm = (today - _dt.timedelta(days=days)).strftime("%Y%m%d")
    to = today.strftime("%Y%m%d")

    out: list[dict] = []
    for i in range(0, len(campaign_ids), HISTORY_CHUNK):
        chunk = [int(c) for c in campaign_ids[i:i + HISTORY_CHUNK] if str(c).isdigit()]
        if not chunk:
            continue
        res = page.evaluate(_FETCH_JS, [HISTORY_URL, {
            "campaignIds": chunk,
            "filter": {"executionTimeFrom": frm, "executionTimeTo": to},
        }])
        status = (res or {}).get("status")
        body = (res or {}).get("body") or ""
        if status != 200:
            log.warning("변경 이력 조회 status=%s (%d개) — %s",
                        status, len(chunk), body[:120].replace("\n", " "))
            return out or None
        try:
            j = json.loads(body)
        except ValueError:
            log.warning("변경 이력 응답이 JSON이 아니다(세션 만료 가능).")
            return out or None
        if not isinstance(j, list):
            log.warning("변경 이력 응답이 리스트가 아니다: %s", str(j)[:120])
            return out or None
        out.extend(j)
    log.info("변경 이력 %d일: 이벤트 %d건", days, len(out))
    return out


def push(prod_base_url: str, ingest_token: str, account: str, payload: dict, *, log) -> bool:
    """prod /ad-settings/ingest push. 실패해도 회차를 실패시키지 않는다(곁다리)."""
    url = prod_base_url.rstrip("/") + "/api/coupang/ops/ad-settings/ingest"
    body = {"account": account, "all": payload["all"], "active": payload["active"],
            "events": payload.get("events") or [], "ads": payload.get("ads") or []}
    try:
        r = requests.post(url, json=body, headers={"X-Ingest-Token": ingest_token}, timeout=60)
    except requests.RequestException as e:
        log.warning("광고 설정 push 네트워크 오류: %s", str(e)[:140])
        return False
    if r.status_code != 200:
        log.warning("광고 설정 push 실패 HTTP %s — %s", r.status_code, r.text[:160])
        return False
    try:
        info = r.json()
    except ValueError:
        info = r.text[:160]
    log.info("광고 설정 push 성공(%s) → %s", account, info)
    return True


def collect_and_push(page, cfg: dict, account: str, *, log) -> bool:
    """페처가 부르는 단일 진입점. 어떤 실패도 예외를 밖으로 내보내지 않는다.

    ★광고비(머니 경로)를 곁다리가 무너뜨리면 안 된다 — 이 함수는 절대 raise하지 않는다.
    """
    try:
        payload = collect(page, log=log)
        if payload is None:
            return False
        return push(cfg["prod_base_url"], cfg["ingest_token"], account, payload, log=log)
    except Exception as e:  # noqa: BLE001
        log.warning("광고 설정 수집·push 중 예외(회차는 계속): %s", str(e)[:160])
        return False
