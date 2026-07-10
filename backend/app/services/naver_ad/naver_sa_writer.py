# naver_sa_writer.py — 네이버 SA 쓰기 유일 저수준 어댑터 SA (X1a T2, ref 27). 제외키워드
# (restricted-keywords) 추가/삭제만 쓴다 — userLock·bidAmt는 X1b에서 별도 함수로 확장한다.
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
    """쓰기 1건의 실행 전/후 실측값 + 쓰기 응답을 담는 결과 객체 (ref 27 §8-2 계약)."""

    def __init__(self, action: str, before: list, response: object, after: list, created_ids: list):
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
