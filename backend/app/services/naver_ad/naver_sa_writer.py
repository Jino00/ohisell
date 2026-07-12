# naver_sa_writer.py — 네이버 SA 쓰기 유일 저수준 어댑터 SA (X1a T2 제외키워드 + X1b T1
# bidAmt·userLock 확장 + P1 dailyBudget 확장, ref 27 + PLAN_naver-ad-budget-control §5-B).
# D-NAO-16 개방 순서(제외키워드→정지·재개→입찰→예산)의 쓰기 함수 전체가 이 모듈에 있다 —
# 다른 SA는 이 모듈을 거치지 않고 네이버 API에 쓰지 않는다.
#
# 예산(dailyBudget) 쓰기는 D-NAO-34 하에서 "영구 스코프 밖"이었으나(ref 27이 의도적으로
# 미상세) D-NAO-42-f가 이를 개방으로 개정 — update_campaign_budget은 그 개방의 어댑터.
#
# 원칙 요약(ref 27 근거):
# - 서명은 실제 HTTP 메서드와 반드시 일치(POST/DELETE 각각 fetcher._headers(path, method=...)),
#   서명 대상은 path만(쿼리스트링 제외).
# - 쓰기(POST/DELETE)는 requests를 직접 호출하고 재시도하지 않는다 — 비멱등이라 429/5xx 포함
#   2xx가 아니면 즉시 실패로 표면화한다(fetcher._get의 내장 재시도는 GET 전용, 여기 쓰지 않음).
# - "성공"은 쓰기 응답 코드가 아니라 재조회(GET) 실측으로만 판정한다(원칙22 — 라이브 증거).
#   재조회에서 의도가 반영되지 않았으면 WriteVerificationError로 fail-closed.
# - DB 접근 없음(순수 API 어댑터, 단일 책임). change_log 기록·원복은 T3 harness 몫.
from __future__ import annotations

import logging

import requests

from app.services import naver_sa_ad_fetcher as fetcher

log = logging.getLogger(__name__)

_RESTRICT_TYPE = "KEYWORD_PLUS_RESTRICT"


class WriteError(Exception):
    """쓰기 HTTP 실패(2xx 아님). status_code와 body 일부를 메시지에 포함."""


class WriteVerificationError(Exception):
    """쓰기 응답은 성공인데 재조회 결과가 의도와 불일치 — fail-closed 신호."""


class WriteValidationError(Exception):
    """쓰기 전 사전 검증 실패(빈 입력, WEB_SITE 아닌 광고그룹, 중복 키워드 등)."""


class WriteResult:
    """쓰기 1건의 실행 전/후 실측값 + 쓰기 응답을 담는 결과 객체 (ref 27 §8-2 계약).

    before/after는 restricted-keywords처럼 리스트 리소스면 list, keyword/adgroup/campaign
    단건 리소스(X1b bidAmt·userLock)면 dict — 대상 리소스의 GET 원본 형태를 그대로 담는다.
    """

    def __init__(self, action: str, before, response: object, after, created_ids: list):
        self.action = action
        self.before = before
        self.response = response
        self.after = after
        self.created_ids = created_ids

    def __repr__(self) -> str:  # pragma: no cover - 디버그 편의
        return (
            f"WriteResult(action={self.action!r}, before={len(self.before)}건, "
            f"after={len(self.after)}건, created_ids={self.created_ids})"
        )


def get_restricted_keywords(adgroup_id: str) -> list[dict]:
    """GET /ncc/adgroups/{adgroupId}/restricted-keywords — 현재 등록된 제외키워드 원본 JSON.

    writer의 before/after 재조회(검증)용 유일 소스(ref 27 §2-2·§5).
    """
    resp = fetcher._get(
        f"/ncc/adgroups/{adgroup_id}/restricted-keywords", {"type": _RESTRICT_TYPE}
    )
    resp.raise_for_status()
    return resp.json()


def _get_adgroup(adgroup_id: str) -> dict:
    resp = fetcher._get(f"/ncc/adgroups/{adgroup_id}")
    resp.raise_for_status()
    return resp.json()


def add_restricted_keywords(adgroup_id: str, keywords: list[str]) -> WriteResult:
    """POST /ncc/adgroups/{adgroupId}/restricted-keywords — 제외키워드 추가(ref 27 §2-1).

    Raises:
        WriteValidationError: keywords 빈 값·요청 내 중복 / adgroup이 WEB_SITE 아님 /
            이미 등록된 키워드 재등록.
        WriteError: POST가 2xx 아님(재시도 없음 — 비멱등 쓰기).
        WriteVerificationError: POST는 2xx였는데 재조회에 반영 안 됨 / 요청 keyword의
            nccAdgroupRestrictKwdId를 after에서 확보하지 못함 / after에 같은 keyword 복수 행
            (동시 등록 경합 — 어느 행이 이번 쓰기 결과인지 판별 불가). 전부 fail-closed.
    """
    if not keywords:
        raise WriteValidationError("add_restricted_keywords: keywords가 비었습니다")
    if len(set(keywords)) != len(keywords):
        # codex[P2]: 요청 리스트 내부 중복 — 서버가 중복 배열을 어떻게 처리하는지 문서에 없음.
        # 확인 안 된 경로는 진입 자체를 차단(추정 금지).
        raise WriteValidationError(
            f"add_restricted_keywords: 요청 내 중복 키워드 — 서버 동작이 문서에 없어 차단: {keywords}"
        )

    adgroup = _get_adgroup(adgroup_id)
    adgroup_type = adgroup.get("adgroupType")
    if adgroup_type != "WEB_SITE":
        raise WriteValidationError(
            f"add_restricted_keywords: adgroup {adgroup_id}는 WEB_SITE가 아님"
            f"(adgroupType={adgroup_type!r}) — restricted-keywords는 WEB_SITE 캠페인 유형 전용"
            "(ref 27 §2-1)"
        )

    before = get_restricted_keywords(adgroup_id)
    existing_keywords = {row.get("keyword") for row in before}
    dup = [k for k in keywords if k in existing_keywords]
    if dup:
        raise WriteValidationError(
            f"add_restricted_keywords: 이미 등록된 키워드 재등록 시도 — 서버 동작이 문서에 없어 "
            f"진입 자체를 차단함(추정 금지): {dup}"
        )

    path = f"/ncc/adgroups/{adgroup_id}/restricted-keywords"
    body = [{"keyword": k, "type": _RESTRICT_TYPE} for k in keywords]
    log.info("Naver SA 쓰기 시도: add_restricted_keywords adgroup=%s keywords=%s", adgroup_id, keywords)
    resp = requests.post(
        fetcher.BASE_URL + path,
        headers=fetcher._headers(path, method="POST"),
        json=body,
        timeout=30,
    )
    if not (200 <= resp.status_code < 300):
        log.error(
            "Naver SA 쓰기 실패: add_restricted_keywords adgroup=%s status=%s body=%s",
            adgroup_id, resp.status_code, resp.text[:300],
        )
        raise WriteError(
            f"add_restricted_keywords 실패: status={resp.status_code} body={resp.text[:300]}"
        )

    # 2xx인데 body가 JSON이 아닐 수 있다(예: 201 + 빈 body — swagger가 201을 나열하나 body
    # 스키마는 200에만 정의됨). 이 시점엔 서버에 쓰기가 이미 반영됐을 수 있으므로 파싱 실패를
    # 실패로 표면화하지 않는다 — 성공 판정의 진실은 아래 after 재조회다.
    try:
        response_body = resp.json()
    except ValueError:
        response_body = None

    after = get_restricted_keywords(adgroup_id)
    after_keywords = {row.get("keyword") for row in after}
    missing = [k for k in keywords if k not in after_keywords]
    if missing:
        raise WriteVerificationError(
            f"add_restricted_keywords: 쓰기 응답은 성공(status={resp.status_code})이나 재조회에 "
            f"반영되지 않음(fail-closed): {missing}"
        )

    # codex[P1]: created_ids는 POST 응답 body가 아니라 검증 완료된 after 재조회에서 파생한다
    # (부분 body·비JSON body에도 견고 — 응답 body는 response 필드에 원본 기록용으로만 유지).
    # 중복 keyword는 사전 차단됐으므로 after에서 요청 keyword와 매칭되는 행 = 이번에 생성된 행.
    # codex[P1 2R]: 단, before/after 사이 다른 행위자(콘솔의 사람·MOP)가 같은 키워드를 등록하면
    # after에 매칭 행이 2개 이상 생길 수 있다 — 어느 행이 이번 쓰기 결과인지 판별 불가.
    # 요청한 각 keyword의 매칭 행이 정확히 1개 + id 비어있지 않을 때만 채택, 아니면 fail-closed
    # (T3 원복에 id가 필수 — 임의 id 채택은 남의 행을 삭제할 위험).
    keyword_set = set(keywords)
    rows_by_keyword: dict[str, list] = {}
    for row in after:
        kw = row.get("keyword")
        if kw in keyword_set:
            rows_by_keyword.setdefault(kw, []).append(row)
    ambiguous = [k for k in keywords if len(rows_by_keyword.get(k, [])) > 1]
    if ambiguous:
        raise WriteVerificationError(
            f"add_restricted_keywords: after 재조회에 같은 keyword 복수 행 — 어느 행이 이번 쓰기 "
            f"결과인지 판별 불가(fail-closed, 임의 id 채택 시 원복이 남의 행 삭제 위험): {ambiguous}"
        )
    id_missing = [
        k for k in keywords
        if not (rows_by_keyword.get(k) and rows_by_keyword[k][0].get("nccAdgroupRestrictKwdId"))
    ]
    if id_missing:
        raise WriteVerificationError(
            f"add_restricted_keywords: after 재조회에서 nccAdgroupRestrictKwdId를 확보하지 못한 "
            f"키워드 존재(fail-closed — 원복에 id 필수): {id_missing}"
        )
    created_ids = [rows_by_keyword[k][0]["nccAdgroupRestrictKwdId"] for k in keywords]

    log.info(
        "Naver SA 쓰기 성공: add_restricted_keywords adgroup=%s created_ids=%s",
        adgroup_id, created_ids,
    )
    return WriteResult(
        action="add_restricted_keywords",
        before=before,
        response=response_body,
        after=after,
        created_ids=created_ids,
    )


def delete_restricted_keywords(adgroup_id: str, restrict_kwd_ids: list[str]) -> WriteResult:
    """DELETE /ncc/adgroups/{adgroupId}/restricted-keywords?ids=... — 제외키워드 삭제(ref 27 §2-3).

    스펙상 성공 응답은 204 No Content이나, 성공 판정의 진실은 재조회에 둔다(fail-closed).

    Raises:
        WriteValidationError: restrict_kwd_ids 빈 값 / before 재조회에 존재하지 않는 id.
        WriteError: DELETE가 2xx 아님(재시도 없음).
        WriteVerificationError: DELETE는 2xx였는데 대상 id가 재조회에 여전히 존재(fail-closed).
    """
    if not restrict_kwd_ids:
        raise WriteValidationError("delete_restricted_keywords: restrict_kwd_ids가 비었습니다")

    before = get_restricted_keywords(adgroup_id)
    before_ids = {row.get("nccAdgroupRestrictKwdId") for row in before}
    unknown = [i for i in restrict_kwd_ids if i not in before_ids]
    if unknown:
        # codex[P1]: stale/오타 id는 서버가 204 no-op를 줄 수 있고, 그러면 'after에 없음' 검증이
        # 공허하게 통과한다 — 삭제 대상이 before에 실재하는지 DELETE 호출 전에 검증(fail-closed).
        raise WriteValidationError(
            f"delete_restricted_keywords: before 재조회에 존재하지 않는 id(stale/오타 가능) — "
            f"no-op 삭제 차단: {unknown}"
        )

    path = f"/ncc/adgroups/{adgroup_id}/restricted-keywords"
    log.info(
        "Naver SA 쓰기 시도: delete_restricted_keywords adgroup=%s ids=%s",
        adgroup_id, restrict_kwd_ids,
    )
    resp = requests.delete(
        fetcher.BASE_URL + path,
        headers=fetcher._headers(path, method="DELETE"),
        params={"ids": ",".join(restrict_kwd_ids)},
        timeout=30,
    )
    if not (200 <= resp.status_code < 300):
        log.error(
            "Naver SA 쓰기 실패: delete_restricted_keywords adgroup=%s status=%s body=%s",
            adgroup_id, resp.status_code, resp.text[:300],
        )
        raise WriteError(
            f"delete_restricted_keywords 실패: status={resp.status_code} body={resp.text[:300]}"
        )

    after = get_restricted_keywords(adgroup_id)
    after_ids = {row.get("nccAdgroupRestrictKwdId") for row in after}
    remaining = [i for i in restrict_kwd_ids if i in after_ids]
    if remaining:
        raise WriteVerificationError(
            f"delete_restricted_keywords: 쓰기 응답은 성공(status={resp.status_code})이나 재조회에 "
            f"잔존(fail-closed): {remaining}"
        )

    log.info("Naver SA 쓰기 성공: delete_restricted_keywords adgroup=%s", adgroup_id)
    return WriteResult(
        action="delete_restricted_keywords",
        before=before,
        response=None,
        after=after,
        created_ids=[],
    )


# ── X1b T1: bidAmt·userLock (D-NAO-16 2·3단계, ref 27 §3·§4) ────────────────
# 성공 판정은 여기서도 재조회로만(fail-closed) — PUT 응답 body는 response 필드에 원본
# 기록용으로만 유지한다(add_restricted_keywords와 동일 규율).

_MIN_BID = 70
_MAX_BID = 100_000
_BID_INCREMENT = 10


def get_keyword(ncc_keyword_id: str) -> dict:
    """GET /ncc/keywords/{nccKeywordId} — 현재 키워드 원본 JSON(bidAmt·userLock 포함).

    update_keyword_bid·set_keyword_lock의 before/after 재조회(검증)용 유일 소스(ref 27 §5).
    """
    resp = fetcher._get(f"/ncc/keywords/{ncc_keyword_id}")
    resp.raise_for_status()
    return resp.json()


def get_campaign(ncc_campaign_id: str) -> dict:
    """GET /ncc/campaigns/{nccCampaignId} — 현재 캠페인 원본 JSON(userLock 포함).

    set_campaign_lock의 before/after 재조회(검증)용 유일 소스(ref 27 §5).
    """
    resp = fetcher._get(f"/ncc/campaigns/{ncc_campaign_id}")
    resp.raise_for_status()
    return resp.json()


def update_keyword_bid(ncc_keyword_id: str, bid_amt: int) -> WriteResult:
    """PUT /ncc/keywords/{nccKeywordId}?fields=bidAmt — 키워드 입찰가 변경(ref 27 §3-1).

    bid_amt 사전검증(70~100,000원, 10원 단위 — bid_simulator 규격과 동일, ref 27 §3-1
    swagger 명시)은 여기서도 반복한다. 상위(guardrail_gate)가 이미 걸렀어도 이 어댑터
    단독 호출 시에도 무효 입찰가가 네이버에 그대로 전송되지 않도록 방어(fail-closed).

    Raises:
        WriteValidationError: bid_amt가 범위 밖이거나 10원 단위가 아님.
        WriteError: PUT이 2xx 아님(재시도 없음 — 비멱등 쓰기).
        WriteVerificationError: PUT은 2xx였는데 재조회에 반영 안 됨.
    """
    if not (_MIN_BID <= bid_amt <= _MAX_BID) or bid_amt % _BID_INCREMENT != 0:
        raise WriteValidationError(
            f"update_keyword_bid: bid_amt={bid_amt}는 유효 범위 밖(70~100,000원, 10원 단위)"
        )

    before = get_keyword(ncc_keyword_id)

    path = f"/ncc/keywords/{ncc_keyword_id}"
    body = {"nccKeywordId": ncc_keyword_id, "bidAmt": bid_amt, "useGroupBidAmt": False}
    log.info("Naver SA 쓰기 시도: update_keyword_bid keyword=%s bidAmt=%s", ncc_keyword_id, bid_amt)
    resp = requests.put(
        fetcher.BASE_URL + path,
        headers=fetcher._headers(path, method="PUT"),
        params={"fields": "bidAmt"},
        json=body,
        timeout=30,
    )
    if not (200 <= resp.status_code < 300):
        log.error(
            "Naver SA 쓰기 실패: update_keyword_bid keyword=%s status=%s body=%s",
            ncc_keyword_id, resp.status_code, resp.text[:300],
        )
        raise WriteError(
            f"update_keyword_bid 실패: status={resp.status_code} body={resp.text[:300]}"
        )

    try:
        response_body = resp.json()
    except ValueError:
        response_body = None

    after = get_keyword(ncc_keyword_id)
    if after.get("bidAmt") != bid_amt:
        raise WriteVerificationError(
            f"update_keyword_bid: 쓰기 응답은 성공(status={resp.status_code})이나 재조회에 "
            f"반영되지 않음(fail-closed): 요청={bid_amt} 재조회={after.get('bidAmt')}"
        )
    # codex[P2]: bidAmt가 재조회에서 요청대로 나와도 useGroupBidAmt가 여전히 true면
    # 실효 CPC는 광고그룹 입찰가를 그대로 쓴다(키워드별 입찰이 반영 안 됨) — bidAmt만
    # 보고 성공 판정하면 "응답은 성공, 실효 반영은 실패"를 놓친다(fail-closed).
    if after.get("useGroupBidAmt") is not False:
        raise WriteVerificationError(
            f"update_keyword_bid: bidAmt는 반영됐으나 useGroupBidAmt가 false로 전환되지 "
            f"않음(fail-closed) — 실효 CPC는 광고그룹 입찰가를 그대로 사용: "
            f"재조회 useGroupBidAmt={after.get('useGroupBidAmt')}"
        )

    log.info("Naver SA 쓰기 성공: update_keyword_bid keyword=%s bidAmt=%s", ncc_keyword_id, bid_amt)
    return WriteResult(
        action="update_keyword_bid", before=before, response=response_body, after=after, created_ids=[],
    )


def _put_user_lock(*, action: str, path: str, body: dict, before: dict, after_fetch) -> WriteResult:
    """3계층(keyword/adgroup/campaign) userLock PUT 공통 로직 — before/after 재조회 계약,
    성공 판정은 재조회로만(fail-closed). fields=userLock 항상 명시(ref 27 §4 전체교체 함정)."""
    log.info("Naver SA 쓰기 시도: %s path=%s body=%s", action, path, body)
    resp = requests.put(
        fetcher.BASE_URL + path,
        headers=fetcher._headers(path, method="PUT"),
        params={"fields": "userLock"},
        json=body,
        timeout=30,
    )
    if not (200 <= resp.status_code < 300):
        log.error("Naver SA 쓰기 실패: %s status=%s body=%s", action, resp.status_code, resp.text[:300])
        raise WriteError(f"{action} 실패: status={resp.status_code} body={resp.text[:300]}")

    try:
        response_body = resp.json()
    except ValueError:
        response_body = None

    after = after_fetch()
    if after.get("userLock") != body["userLock"]:
        raise WriteVerificationError(
            f"{action}: 쓰기 응답은 성공(status={resp.status_code})이나 재조회에 반영되지 "
            f"않음(fail-closed): 요청={body['userLock']} 재조회={after.get('userLock')}"
        )

    log.info("Naver SA 쓰기 성공: %s userLock=%s", action, body["userLock"])
    return WriteResult(action=action, before=before, response=response_body, after=after, created_ids=[])


def set_keyword_lock(ncc_keyword_id: str, user_lock: bool) -> WriteResult:
    """PUT /ncc/keywords/{nccKeywordId}?fields=userLock — 키워드 정지(true)/재개(false)
    (ref 27 §4 키워드 계층). D-NAO-16 개방 순서 2단계(정지·재개)의 키워드 단위 실행 함수.

    Raises:
        WriteValidationError: user_lock이 bool이 아님.
        WriteError: PUT이 2xx 아님.
        WriteVerificationError: PUT은 2xx였는데 재조회에 반영 안 됨.
    """
    if not isinstance(user_lock, bool):
        raise WriteValidationError(f"set_keyword_lock: user_lock은 bool이어야 함: {user_lock!r}")

    before = get_keyword(ncc_keyword_id)
    path = f"/ncc/keywords/{ncc_keyword_id}"
    body = {"nccKeywordId": ncc_keyword_id, "userLock": user_lock}
    return _put_user_lock(
        action="set_keyword_lock", path=path, body=body, before=before,
        after_fetch=lambda: get_keyword(ncc_keyword_id),
    )


def set_adgroup_lock(ncc_adgroup_id: str, user_lock: bool) -> WriteResult:
    """PUT /ncc/adgroups/{nccAdgroupId}?fields=userLock — 광고그룹 정지(true)/재개(false)
    (ref 27 §4 광고그룹 계층). customerId 불필요(Adgroup 정의엔 required-update 표시 없음,
    swagger definitions 재확인 — Campaign과 다름).

    Raises:
        WriteValidationError: user_lock이 bool이 아님.
        WriteError: PUT이 2xx 아님.
        WriteVerificationError: PUT은 2xx였는데 재조회에 반영 안 됨.
    """
    if not isinstance(user_lock, bool):
        raise WriteValidationError(f"set_adgroup_lock: user_lock은 bool이어야 함: {user_lock!r}")

    before = _get_adgroup(ncc_adgroup_id)
    path = f"/ncc/adgroups/{ncc_adgroup_id}"
    body = {"nccAdgroupId": ncc_adgroup_id, "userLock": user_lock}
    return _put_user_lock(
        action="set_adgroup_lock", path=path, body=body, before=before,
        after_fetch=lambda: _get_adgroup(ncc_adgroup_id),
    )


def set_campaign_lock(ncc_campaign_id: str, user_lock: bool) -> WriteResult:
    """PUT /ncc/campaigns/{nccCampaignId}?fields=userLock — 캠페인 정지(true)/재개(false)
    (ref 27 §4 캠페인 계층, D-NAO-16 원문 사례 "캠페인이 죽었으면 정지"). customerId는
    swagger에서 캠페인 PUT의 #required-update — 반드시 포함(Campaign 정의 재확인).

    Raises:
        WriteValidationError: user_lock이 bool이 아님.
        WriteError: PUT이 2xx 아님.
        WriteVerificationError: PUT은 2xx였는데 재조회에 반영 안 됨.
    """
    if not isinstance(user_lock, bool):
        raise WriteValidationError(f"set_campaign_lock: user_lock은 bool이어야 함: {user_lock!r}")

    before = get_campaign(ncc_campaign_id)
    path = f"/ncc/campaigns/{ncc_campaign_id}"
    body = {
        "nccCampaignId": ncc_campaign_id, "customerId": int(fetcher.CUSTOMER_ID), "userLock": user_lock,
    }
    return _put_user_lock(
        action="set_campaign_lock", path=path, body=body, before=before,
        after_fetch=lambda: get_campaign(ncc_campaign_id),
    )


# ── P1: update_campaign_budget (D-NAO-16 4단계, D-NAO-42-f 개방, PLAN §5-B) ──────
# 성공 판정은 여기서도 재조회로만(fail-closed) — PUT 응답 body는 response 필드에 원본
# 기록용으로만 유지한다(update_keyword_bid/set_campaign_lock과 동일 규율).


def update_campaign_budget(ncc_campaign_id: str, daily_budget: int) -> WriteResult:
    """PUT /ncc/campaigns/{nccCampaignId}?fields=budget — 캠페인 일예산 변경
    (PLAN_naver-ad-budget-control.md §5-B, swagger+라이브 04 확정 2026-07-13).

    dailyBudget의 정확한 최소값·증분 단위는 swagger(ncc-heroes-ncc.json)에 정의돼
    있지 않다(integer, default 0, minimum/multipleOf 미정의) — 추정 금지 원칙에 따라
    여기서는 `daily_budget > 0`인 정수만 사전검증하고, 그 외 유효 범위는 네이버 API의
    거부(WriteError) + after 재조회 exact-match(fail-closed)에 위임한다. 정확한
    min·증분은 P4 라이브 왕복에서 실측한다(sizer가 100원 단위로 반올림해 침묵
    반올림을 방어 — PLAN §5-G).

    공유예산(sharedBudgetId) 캠페인은 per-campaign dailyBudget이 무효하므로(swagger
    sharedDailyBudget 별도 경로) before 재조회에서 sharedBudgetId가 있으면 PUT
    자체를 시도하지 않고 fail-closed 차단한다.

    Raises:
        WriteValidationError: daily_budget이 양의 정수가 아님 / 대상 캠페인이
            공유예산(sharedBudgetId != None)에 속함.
        WriteError: PUT이 2xx 아님(재시도 없음 — 비멱등 쓰기).
        WriteVerificationError: PUT은 2xx였는데 재조회에 dailyBudget이 반영되지
            않음 / useDailyBudget이 true로 확인되지 않음(useGroupBidAmt 이중
            확인과 동형 — dailyBudget만 보고 성공 판정하면 "응답은 성공, 실효
            반영은 실패"를 놓친다).
    """
    if not isinstance(daily_budget, int) or isinstance(daily_budget, bool) or daily_budget <= 0:
        raise WriteValidationError(
            f"update_campaign_budget: daily_budget={daily_budget!r}는 양의 정수여야 함"
        )

    before = get_campaign(ncc_campaign_id)
    if before.get("sharedBudgetId") is not None:
        raise WriteValidationError(
            f"update_campaign_budget: 캠페인 {ncc_campaign_id}는 공유예산"
            f"(sharedBudgetId={before.get('sharedBudgetId')!r})에 속함 — "
            "per-campaign dailyBudget 변경은 무효(swagger sharedDailyBudget 별도 경로)"
        )

    path = f"/ncc/campaigns/{ncc_campaign_id}"
    body = {
        "nccCampaignId": ncc_campaign_id,
        "customerId": int(fetcher.CUSTOMER_ID),
        "useDailyBudget": True,
        "dailyBudget": daily_budget,
    }
    log.info(
        "Naver SA 쓰기 시도: update_campaign_budget campaign=%s dailyBudget=%s",
        ncc_campaign_id, daily_budget,
    )
    resp = requests.put(
        fetcher.BASE_URL + path,
        headers=fetcher._headers(path, method="PUT"),
        params={"fields": "budget"},
        json=body,
        timeout=30,
    )
    if not (200 <= resp.status_code < 300):
        log.error(
            "Naver SA 쓰기 실패: update_campaign_budget campaign=%s status=%s body=%s",
            ncc_campaign_id, resp.status_code, resp.text[:300],
        )
        raise WriteError(
            f"update_campaign_budget 실패: status={resp.status_code} body={resp.text[:300]}"
        )

    try:
        response_body = resp.json()
    except ValueError:
        response_body = None

    after = get_campaign(ncc_campaign_id)
    if after.get("dailyBudget") != daily_budget:
        raise WriteVerificationError(
            f"update_campaign_budget: 쓰기 응답은 성공(status={resp.status_code})이나 재조회에 "
            f"반영되지 않음(fail-closed): 요청={daily_budget} 재조회={after.get('dailyBudget')}"
        )
    if after.get("useDailyBudget") is not True:
        raise WriteVerificationError(
            f"update_campaign_budget: dailyBudget은 반영됐으나 useDailyBudget이 true로 전환되지 "
            f"않음(fail-closed) — false면 네이버가 dailyBudget 값을 무시함(swagger 명시): "
            f"재조회 useDailyBudget={after.get('useDailyBudget')}"
        )

    log.info(
        "Naver SA 쓰기 성공: update_campaign_budget campaign=%s dailyBudget=%s",
        ncc_campaign_id, daily_budget,
    )
    return WriteResult(
        action="update_campaign_budget", before=before, response=response_body, after=after,
        created_ids=[],
    )
