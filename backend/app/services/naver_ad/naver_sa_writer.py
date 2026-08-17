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

import json
import logging
from datetime import datetime, timezone

import requests

from app.services import naver_sa_ad_fetcher as fetcher

log = logging.getLogger(__name__)

# ★타입은 «쓰는 것»과 «읽는 것»이 다르다 (D-NAO-179, 2026-08-16 라이브 실측).
# 제외키워드 리소스에는 타입이 **둘** 있고(공식 스펙 AdgroupRestrictKwd.type enum), 둘은
# **분리된 목록**이다 — EXP_SEARCH로 등록된 행은 type=KEYWORD_PLUS_RESTRICT 조회에 **안 잡힌다**
# (대조군 실증: WEB_SITE 그룹에 EXP_SEARCH 1건 생성 → EXP_SEARCH 조회 1건 / KEYWORD_PLUS 조회 0건).
# 그래서 한 타입만 묻는 것은 「없다」는 거짓말을 받는 것이다: 계정 전수 실측에서 우리가 보던
# 제외는 723건 중 12건(1.7%)뿐이었고, 나머지 711건(64개 그룹)이 시야 밖에 있었다.
_RESTRICT_TYPE = "KEYWORD_PLUS_RESTRICT"  # 우리가 **쓸 때** 쓰는 타입(효과 실증됨 — 일기 425)
RESTRICT_TYPES = ("KEYWORD_PLUS_RESTRICT", "EXP_SEARCH")  # **읽을 때는 둘 다**

# ★광고그룹 유형별로 제외의 «진실의 소스»가 다르다 (D-NAO-180). 이 상수들이 여기 사는 이유는
# 유형 판정과 소스 선택이 한 모듈 안에 있어야 갈라지지 않기 때문이다 — 종전엔 문자열
# "WEB_SITE"가 소비자마다 하드코딩돼 있었고, 쇼핑을 열 때 그 자리를 전부 찾아다녀야 했다.
WEB_SITE_ADGROUP_TYPE = "WEB_SITE"      # 파워링크 → restricted-keywords 리소스
SHOPPING_ADGROUP_TYPE = "SHOPPING"      # 쇼핑몰 상품형 → /ncc/targets 리소스
_RESTRICT_KEYWORD_TARGET_TP = "RESTRICT_KEYWORD_TARGET"


def _epoch_to_utc_iso(value: object) -> str | None:
    """`/ncc/targets` 항목의 `date`(초 단위 epoch) → `regTm`과 같은 UTC ISO 표기.

    ★epoch를 **UTC로** 읽는 것이 옳다는 근거는 라이브 대조다(2026-08-17): 알려진 벽시계 시각
      12:34:18 KST에 직접 등록한 항목의 `date`를 UTC로 환산하면 같은 응답의 `editTm`
      (`2026-08-17T03:34:18.000Z`)과 **초 단위까지 일치**했다. ref 58 §13-5가 「43/43이 +1시간,
      원인 미상」으로 남긴 오차는 API가 아니라 **콘솔 표기** 쪽에 있다.

    못 읽으면 None이다 — 시각 한 칸 때문에 «제외가 있다»는 1급 사실을 버리지 않는다
    (`_parse_reg_tm` 독스트링과 같은 처분).
    """
    if value in (None, "", 0):
        return None
    try:
        ts = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        log.warning("[쇼핑제외] date를 못 읽었다(%r) — 등록시각은 미상으로 둔다", value)
        return None
    try:
        return (
            datetime.fromtimestamp(ts, tz=timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.000Z")
        )
    except (OverflowError, OSError, ValueError):
        log.warning("[쇼핑제외] date가 표현 범위 밖이다(%r) — 등록시각은 미상으로 둔다", value)
        return None


class WriteError(Exception):
    """쓰기 HTTP 실패(2xx 아님). status_code와 body 일부를 메시지에 포함."""


class WriteVerificationError(Exception):
    """쓰기 응답은 성공인데 재조회 결과가 의도와 불일치 — fail-closed 신호."""


class WriteValidationError(Exception):
    """쓰기 전 사전 검증 실패(빈 입력, WEB_SITE 아닌 광고그룹, 중복 키워드 등)."""


class GroupBidDeadError(WriteValidationError):
    """그룹 입찰이 **옥션에서 실효가 아닌** 그룹에 그룹입찰 PUT을 시도 (B-4, D-NAO-166·170).

    ★이 예외는 «관측을 실어 나른다»는 점에서 다른 검증 실패와 다르다. 거부하는 그 순간
    가드는 이미 정답(라이브 소재 목록 = 어느 소재가 실효 레버인가)을 손에 쥐고 있는데,
    D-NAO-166은 그걸 **버렸다.** 그래서 라우터(DB 파생)와 가드(라이브)가 갈라진 그룹은
    **회차마다 같은 거부를 반복**했다 — 재라우팅 기계가 없어서가 아니라(라우터가 이미
    그 기계다) 라우터의 **입력을 고치는 손**이 없어서다.
    `ads`를 실어 보내면 상위(harness)가 그 관측을 `naver_adgroup_product`에 되돌려 쓰고,
    **다음 회차에 기존 라우터가 스스로 소재로 절체**한다(새 경로를 만들지 않는다).

    Attributes:
        adgroup_id: 거부된 광고그룹 id.
        ads: `naver_sa_ad_fetcher.get_ads` 형식의 라이브 소재 목록(추가 API 콜 0 — 가드가
            판별에 이미 쓴 응답 그대로). 조회 실패 경로는 애초에 거부하지 않으므로(fail-open)
            이 예외에 실리는 목록은 항상 «적극적으로 입증된» 관측이다.
    """

    def __init__(self, message: str, *, adgroup_id: str, ads: list[dict]) -> None:
        super().__init__(message)
        self.adgroup_id = adgroup_id
        self.ads = ads


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


def get_restricted_keywords(
    adgroup_id: str, types: tuple[str, ...] = RESTRICT_TYPES
) -> list[dict]:
    """GET /ncc/adgroups/{adgroupId}/restricted-keywords — 등록된 제외키워드 원본 JSON **전 타입**.

    writer의 before/after 재조회(검증)용 유일 소스(ref 27 §2-2·§5)이자 생존감시·자동발견의
    라이브 소스다.

    ★타입별로 한 번씩 GET 해 **union**을 돌려준다(D-NAO-179). 한 타입만 물으면 다른 타입으로
      등록된 제외가 «없는 것»이 되는데, 그 결과가 셋 다 나쁘다: ①생존감시가 멀쩡히 살아 있는
      제외를 「사라졌다」로 본다 ②중복 가드가 이미 있는 키워드를 못 잡는다 ③삭제 대상 id를
      before에서 못 찾아 거부한다.

    각 행의 `type`은 응답에 실려 오지만, 없으면 조회한 타입으로 채운다 — 나중에 이 행을
    **어느 목록에서** 지울지가 그 값에 달렸기 때문에 «모르는 type»을 만들지 않는다.

    ★fail-closed: 한 타입이라도 조회에 실패하면 **예외를 그대로 올린다**(부분 union을 성공으로
      돌려주지 않는다). 부분 목록은 「없다」와 구분되지 않고, 이 리포는 D-NAO-174에서 정확히
      그 결함(모름을 0건으로 셈)으로 P1을 맞았다.
    """
    rows: list[dict] = []
    for typ in types:
        resp = fetcher._get(
            f"/ncc/adgroups/{adgroup_id}/restricted-keywords", {"type": typ}
        )
        resp.raise_for_status()
        for row in resp.json():
            if isinstance(row, dict) and not row.get("type"):
                row["type"] = typ
            rows.append(row)
    return rows


def _get_adgroup(adgroup_id: str) -> dict:
    resp = fetcher._get(f"/ncc/adgroups/{adgroup_id}")
    resp.raise_for_status()
    return resp.json()


def get_adgroup_type(adgroup_id: str) -> str | None:
    """광고그룹 유형(WEB_SITE/SHOPPING/…) — 없으면 None.

    ★왜 공개 함수인가(2026-08-11 실측): `restricted-keywords`는 **WEB_SITE에서만 진실을
    말한다.** 쇼핑 광고그룹은 콘솔에 제외 검색어가 43건 등록돼 있어도 이 API가 200/**0건**을
    돌려준다(쓰기는 400/3728로 아예 막혀 있고, 읽기는 «없다»고 거짓말한다). 그래서 조치 생존
    감시가 이 유형을 먼저 보고 «대조 불가»를 «사라졌다»와 갈라야 한다 — 안 그러면 콘솔에서
    자른 쇼핑 검색어가 매일 「우리 조치가 사라졌다」로 뜬다."""
    try:
        return _get_adgroup(adgroup_id).get("adgroupType")
    except Exception:  # noqa: BLE001 — 조회 실패는 «모름»(None)이지 «WEB_SITE 아님»이 아니다
        log.exception("get_adgroup_type 실패 adgroup=%s", adgroup_id)
        return None


def get_shopping_exclusions(adgroup_id: str) -> list[dict]:
    """GET /ncc/targets?ownerId={id} → 쇼핑 광고그룹의 제외 검색어를 **restricted-keywords 행
    모양으로 정규화**해 돌려준다 (D-NAO-180).

    ★왜 이 함수가 생겼나: 「쇼핑 제외는 API로 못 본다」는 전제가 **틀렸다**(2026-08-17 실측).
      안 보였던 것은 `restricted-keywords` **리소스**였지 쇼핑 제외 자체가 아니다. 같은 계정에서
      이 엔드포인트는 **3,880건/116그룹**을 돌려주고, 콘솔 캡처로 원장에 넣은 43건과 차집합
      양쪽 0으로 일치했다. 그래서 사람이 그룹마다 화면을 캡처하던 입구(S5)가 필요 없어진다.

    ★**반환 모양을 새로 만들지 않는다.** 하류(생존감시 `_classify`·자동발견 편입)는 이미
      restricted-keywords 행 모양을 읽는데, 여기서 새 모양을 돌려주면 소비자마다 분기가 생기고
      그 분기가 갈라지는 것이 이 리포가 반복해 당한 형태다([[same-defect-three-times-fix-the-shape]]).
      그래서 `keyword`/`delFlag`/`type`/`regTm`을 **그 이름 그대로** 채워 내보낸다.

    ★`date`(epoch) → `regTm`(UTC ISO)로 옮기는 이유: 하류의 `_parse_reg_tm`이 이미 「UTC ISO를
      KST로」 하는 유일한 규칙이다. epoch를 따로 파싱하는 두 번째 규칙을 만들면 두 벌이 갈라진다.
      **epoch를 UTC로 읽는 것이 옳다는 근거는 라이브 대조다**(2026-08-17 12:34:18 KST에 직접
      만든 항목의 `date`를 UTC로 환산하면 같은 응답의 `editTm`과 **초 단위까지 일치**했다).
      ref 58 §13-5가 남긴 「+1시간, 원인 미상」은 API가 아니라 **콘솔 표기 쪽**의 오차다.

    ★★**개별 키워드 id를 만들어 내지 않는다.** 이 리소스의 id(`nccTargetId`)는 **그룹 단위**라
      키워드 1건을 가리키지 않는다. 그런데 `_classify`가 회수한 id는 `restrict_kwd_id`에 저장되고
      그 칸의 유일한 실쓰기 소비자가 **개방(`delete_restricted_keywords`)**이다 — 그룹 id를
      키워드 id인 척 넣으면 나중에 **엉뚱한 대상을 지운다.** 그래서 `nccAdgroupRestrictKwdId`를
      **일부러 비운다**(하류는 본문 정확 일치로 대조하도록 이미 만들어져 있다).
      그룹 id는 이름이 다른 칸(`nccTargetId`)에 그대로 실어 보내 쓰기 배선(③)이 쓰게 둔다.

    ※ 벌크(`?ownerIds=a,b,c`)도 200으로 동작한다(2026-08-17 실측, 100그룹/1콜까지 확인).
      지금은 소비자가 이미 그룹당 루프라 per-group으로 둔다 — 필요해지면 그때 올린다.
    """
    resp = fetcher._get("/ncc/targets", {"ownerId": adgroup_id})
    resp.raise_for_status()
    rows: list[dict] = []
    for target in resp.json():
        if target.get("targetTp") != _RESTRICT_KEYWORD_TARGET_TP:
            continue
        if target.get("delFlag"):
            # 타겟 행 자체가 소프트 삭제면 그 안의 키워드는 효력이 없다.
            continue
        items = target.get("target")
        if not isinstance(items, list):
            # 이 targetTp는 리스트를 싣는다. 아니면 스키마가 바뀐 것이므로 «0건»이라 말하지
            # 않고 예외로 올린다 — 모름을 0건으로 세는 것이 이 계열의 고질 결함이다(교훈 #123).
            raise WriteError(
                f"RESTRICT_KEYWORD_TARGET.target이 리스트가 아니다({type(items).__name__}) "
                f"— adgroup={adgroup_id}"
            )
        for item in items:
            if not isinstance(item, dict) or not item.get("keyword"):
                continue
            rows.append({
                "keyword": item.get("keyword"),
                "type": item.get("type"),
                # 이 리소스는 살아 있는 항목만 싣는다(소프트 삭제 개념이 없다) — 하류가
                # delFlag를 반드시 보므로 «없음»이 아니라 명시적 False를 넣는다.
                "delFlag": False,
                "nccAdgroupRestrictKwdId": None,  # ★위 독스트링 — 일부러 비운다
                "nccTargetId": target.get("nccTargetId"),
                "regTm": _epoch_to_utc_iso(item.get("date")),
            })
    return rows


def get_live_exclusions(adgroup_id: str, adgroup_type: str | None) -> list[dict] | None:
    """광고그룹 유형에 맞는 소스로 **라이브 제외 목록**을 돌려준다 (D-NAO-180).

    유형별 진실의 소스가 다르다:
      · `WEB_SITE`(파워링크) → `restricted-keywords` 두 타입 union (D-NAO-179)
      · `SHOPPING`(쇼핑몰 상품형) → `/ncc/targets`의 `RESTRICT_KEYWORD_TARGET` (D-NAO-180)

    ★**«읽을 수 없다»는 None으로 돌려준다 — 빈 리스트가 아니다.** 브랜드검색·플레이스 등 아직
      소스를 모르는 유형과, 유형 자체를 조회하지 못한 경우(`adgroup_type is None`)가 여기 걸린다.
      이 둘을 `[]`로 뭉개면 「제외가 0건이다」와 구별되지 않고, 그 혼동이 정확히 이 리포가
      D-NAO-174에서 P1을 맞은 결함이다(모름을 0건으로 셈). 호출부는 None을 «대조 불가»로
      처분해야 한다.
    """
    if adgroup_type == WEB_SITE_ADGROUP_TYPE:
        return get_restricted_keywords(adgroup_id)
    if adgroup_type == SHOPPING_ADGROUP_TYPE:
        return get_shopping_exclusions(adgroup_id)
    return None


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
# VT4 D-NAO-82①(codex 1R P1-1): adgroup/ad grain 유효 하한 = 50원(SHOPPING 쇼핑검색 최소 유효
# 입찰, prod 158그룹 bid=50 라이브 실증 2026-07-22). keyword grain(update_keyword_bid)은 70원
# 유지(파워링크 키워드 규격·bid_simulator._MIN_BID 정합). WEB_SITE 광고그룹에 50~60원을 쓰려는
# 오용은 네이버 API 400이 fail-closed로 잡는다(이 어댑터는 grain 하한만 방어 — campaign_type을
# 모르므로 grain 최저선까지 허용하고 API 거부에 위임, guardrail_gate가 상위에서 type 인지 차단).
_MIN_BID_GROUP_AD = 50
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
            반영은 실패"를 놓친다) / after 재조회에서 sharedBudgetId가 채워짐(Fix 5,
            codex P2 — 쓰기 중간에 다른 행위자가 공유예산으로 전환해 우리 쓰기가
            무효화된 상태 변화).
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
    # Fix 5(codex P2): before 재조회 시점엔 공유예산이 아니었는데(그래서 PUT까지 진행됐는데),
    # PUT과 after 재조회 사이에 다른 행위자(콘솔의 사람·MOP)가 이 캠페인을 공유예산으로
    # 전환했을 수 있다 — 그러면 우리가 방금 쓴 per-campaign dailyBudget은 공유예산 하위에서
    # 무효(swagger sharedDailyBudget 별도 경로, before 재조회 검증과 동일 근거). dailyBudget/
    # useDailyBudget만 보고 성공 판정하면 이 상태 변화를 놓친다(fail-closed).
    if after.get("sharedBudgetId") is not None:
        raise WriteVerificationError(
            f"update_campaign_budget: 쓰기 응답은 성공(status={resp.status_code})이나 재조회에서 "
            f"공유예산으로 전환됨(fail-closed) — sharedBudgetId={after.get('sharedBudgetId')!r} "
            "(쓰기 중 다른 행위자가 상태를 바꿨을 수 있음, per-campaign dailyBudget 무효)"
        )

    log.info(
        "Naver SA 쓰기 성공: update_campaign_budget campaign=%s dailyBudget=%s",
        ncc_campaign_id, daily_budget,
    )
    return WriteResult(
        action="update_campaign_budget", before=before, response=response_body, after=after,
        created_ids=[],
    )


# ── update_adgroup_bid (쇼핑 광고그룹 단위 입찰가, D-NAO-16 3단계 SHOPPING 대칭 확장) ──
# 성공 판정은 여기서도 재조회로만(fail-closed) — PUT 응답 body는 response 필드에 원본
# 기록용으로만 유지한다(update_keyword_bid와 동일 규율). 근거: ref 27 §85 + swagger
# (ncc-heroes-ncc.json) AdgroupRequest.bidAmt(70~100,000, 10원 단위, fields=bidAmt) 실측
# (2026-07-14). 키워드의 useGroupBidAmt 커플링은 adgroup엔 없음(adgroup이 입찰 최하위 단위 —
# swagger Adgroup 정의에 그런 필드 없음).


def _reject_if_group_bid_is_dead(ncc_adgroup_id: str) -> None:
    """B-4(D-NAO-164·교훈 #202): 그룹 입찰이 **옥션에서 실효가 아닌** 그룹이면 거부.

    판별자는 추론이 아니라 **데이터**다 — `/ncc/ads?nccAdgroupId=`의 `adAttr.useGroupBidAmt`.
    쇼핑 소재가 하나라도 `useGroupBidAmt=true`면 그 소재는 그룹 입찰을 따르므로 그룹 PUT은
    실효가 있다(허용). **전부 false일 때만** 그룹 입찰이 죽은 값이므로 거부한다.

    ★fail-**open** on ambiguity다(이 파일의 다른 가드들과 방향이 반대 — 의도적):
      - 소재 목록 조회 실패 → 통과. 판별 못 하는 것을 이유로 **정상 입찰을 막으면**
        조회 장애가 곧 광고 운영 정지가 된다. 이 가드가 막는 것은 «돈이 새는 쓰기»가 아니라
        «아무 일도 안 일어나는 쓰기»라, 오탐의 대가가 미탐의 대가보다 크다.
      - `use_group_bid_amt`가 None(파싱 실패·adAttr 부재)인 소재가 섞여 있으면 → 통과.
        「전부 false」를 **적극적으로 입증**했을 때만 거부한다.
      - 소재 0건 → 통과. 파워링크(WEB_SITE) 그룹은 키워드가 입찰을 지고 `get_ads`가
        상품 소재만 돌려주므로 여기서 비어 나온다 — 쇼핑 판별자를 그쪽에 적용하면 안 된다.

    Raises:
        WriteValidationError: 이 그룹의 쇼핑 소재가 **전부** useGroupBidAmt=false
            (= 그룹 입찰이 옥션에서 아무것도 지배하지 않음). 실효 레버는 소재이므로
            `update_ad_bid`를 써야 한다.
    """
    try:
        ads = fetcher.get_ads(ncc_adgroup_id)
    except Exception as exc:  # noqa: BLE001 — 조회 실패로 정상 입찰을 막지 않는다(위 §fail-open)
        log.warning(
            "update_adgroup_bid: 실효 레이어 판별용 소재 조회 실패 adgroup=%s (%s) — 가드 통과",
            ncc_adgroup_id, exc,
        )
        return

    flags = [a.get("use_group_bid_amt") for a in ads]
    if not flags:
        return  # 쇼핑 소재 없음(파워링크 등) — 이 판별자의 대상이 아니다
    if any(f is not False for f in flags):
        return  # true가 하나라도 있거나 불명이 섞임 — 그룹 입찰이 실효일 수 있다

    # ★D-NAO-170: 거부하면서 **관측을 실어 보낸다**(추가 API 콜 0 — 위 `ads` 그대로).
    #   상위가 이걸 DB에 되돌려 쓰면 다음 회차에 라우터가 스스로 소재로 절체한다.
    raise GroupBidDeadError(
        f"update_adgroup_bid: adgroup {ncc_adgroup_id}의 쇼핑 소재 {len(flags)}개가 "
        "**전부** useGroupBidAmt=false — 그룹 입찰은 옥션에서 실효가 아니다"
        "(PUT은 200을 받고 재조회도 새 값을 주지만 CPC는 안 바뀐다). "
        "실효 레버는 소재이므로 update_ad_bid를 쓸 것 (B-4, D-NAO-164·교훈 #202)",
        adgroup_id=ncc_adgroup_id,
        ads=ads,
    )


def update_adgroup_bid(ncc_adgroup_id: str, bid_amt: int) -> WriteResult:
    """PUT /ncc/adgroups/{nccAdgroupId}?fields=bidAmt — 쇼핑 광고그룹 입찰가 변경
    (swagger Adgroup.bidAmt, 실측 2026-07-14).

    bid_amt 사전검증(50~100,000원, 10원 단위 — VT4 P1-1 adgroup grain 하한 50원,
    _MIN_BID_GROUP_AD)은 여기서도 반복한다(이중 방벽, 상위 guardrail_gate가 이미 걸렀어도 이
    어댑터 단독 호출 시에도 무효 입찰가가 네이버에 그대로 전송되지 않도록 방어, fail-closed).

    ML 자동입찰 충돌 사전가드: swagger Adgroup.systemBiddingType(NONE|ML)이 'NONE'이 아니거나
    autobidStrategy.isAutobidActive가 true면 시스템(ML) 자동입찰이 이미 이 그룹의 입찰을
    관리 중 — 수동 bidAmt PUT은 무의미하거나 충돌한다. systemBiddingType 필드 자체가 응답에
    없는 경우도 'NONE'과 다르므로(추정 금지) 안전 쪽(차단)으로 판정한다.

    Raises:
        WriteValidationError: bid_amt가 범위 밖이거나 10원 단위가 아님 / 대상 광고그룹이
            시스템(ML) 자동입찰 중(systemBiddingType != 'NONE' 또는 isAutobidActive=true).
        WriteError: PUT이 2xx 아님(재시도 없음 — 비멱등 쓰기).
        WriteVerificationError: PUT은 2xx였는데 재조회에 반영 안 됨.
    """
    if not (_MIN_BID_GROUP_AD <= bid_amt <= _MAX_BID) or bid_amt % _BID_INCREMENT != 0:
        raise WriteValidationError(
            f"update_adgroup_bid: bid_amt={bid_amt}는 유효 범위 밖({_MIN_BID_GROUP_AD}~100,000원, 10원 단위)"
        )

    before = _get_adgroup(ncc_adgroup_id)

    system_bidding_type = before.get("systemBiddingType")
    autobid_strategy = before.get("autobidStrategy")
    autobid_active = autobid_strategy.get("isAutobidActive") if isinstance(autobid_strategy, dict) else None
    # codex[P1] S2: isAutobidActive가 **명시적으로 False**일 때만 수동입찰로 인정(추정 금지).
    # 필드 누락·비-dict·True는 전부 차단 — 부분응답/스키마변경 시 False로 강제해 ML 가드를
    # 우회하던 것 방지(fail-closed on ambiguity). systemBiddingType도 'NONE' 명시 필수.
    if system_bidding_type != "NONE" or autobid_active is not False:
        raise WriteValidationError(
            f"update_adgroup_bid: adgroup {ncc_adgroup_id}는 수동입찰로 확인 불가"
            f"(systemBiddingType={system_bidding_type!r}, isAutobidActive={autobid_active!r}) "
            "— 시스템(ML) 자동입찰이거나 상태 불명이라 수동 bidAmt PUT 차단(fail-closed)"
        )

    # ★B-4 실효 레이어 가드 (D-NAO-164·교훈 #202, 2026-08-10) — update_ad_bid의 대칭.
    #   쇼핑 소재가 전부 useGroupBidAmt=false면 **그룹 입찰은 옥션에서 아무것도 지배하지
    #   않는다**. 그런 그룹에 PUT하면 API는 200을 주고 재조회도 새 값을 돌려주므로
    #   "성공"으로 보이지만 CPC는 꿈쩍도 안 한다 — 라이브 실사고: 03. 아이폰_강화유리에서
    #   PAO가 9일간 그룹 입찰 59건(전부 상향)을 썼는데 소재 36/36이 false라 전부 무접촉이었다.
    #   ★update_ad_bid는 처음부터 이 가드를 갖고 있었는데(useGroupBidAmt가 false 아니면 거부)
    #   반대 방향이 비어 있었다. 한쪽만 있는 가드는 «막는다»가 아니라 «한 방향만 막는다»다.
    _reject_if_group_bid_is_dead(ncc_adgroup_id)

    path = f"/ncc/adgroups/{ncc_adgroup_id}"
    body = {"nccAdgroupId": ncc_adgroup_id, "bidAmt": bid_amt}
    log.info("Naver SA 쓰기 시도: update_adgroup_bid adgroup=%s bidAmt=%s", ncc_adgroup_id, bid_amt)
    resp = requests.put(
        fetcher.BASE_URL + path,
        headers=fetcher._headers(path, method="PUT"),
        params={"fields": "bidAmt"},
        json=body,
        timeout=30,
    )
    if not (200 <= resp.status_code < 300):
        log.error(
            "Naver SA 쓰기 실패: update_adgroup_bid adgroup=%s status=%s body=%s",
            ncc_adgroup_id, resp.status_code, resp.text[:300],
        )
        raise WriteError(
            f"update_adgroup_bid 실패: status={resp.status_code} body={resp.text[:300]}"
        )

    try:
        response_body = resp.json()
    except ValueError:
        response_body = None

    after = _get_adgroup(ncc_adgroup_id)
    if after.get("bidAmt") != bid_amt:
        raise WriteVerificationError(
            f"update_adgroup_bid: 쓰기 응답은 성공(status={resp.status_code})이나 재조회에 "
            f"반영되지 않음(fail-closed): 요청={bid_amt} 재조회={after.get('bidAmt')}"
        )

    log.info("Naver SA 쓰기 성공: update_adgroup_bid adgroup=%s bidAmt=%s", ncc_adgroup_id, bid_amt)
    return WriteResult(
        action="update_adgroup_bid", before=before, response=response_body, after=after, created_ids=[],
    )


# ── update_ad_bid (쇼핑 소재 단위 실효입찰, B3 D-NAO-65 설계질문 3·4) ──────────────
# 소재(SHOPPING_PRODUCT_AD)의 adAttr.bidAmt 직접 수정. useGroupBidAmt=false 소재는 소재
# 개별 bidAmt가 실효 입찰이고 그룹입찰을 무시하므로(공식 apidoc), 그룹/키워드 레버가 헛도는
# 이런 소재를 실효 레버로 잡는다. useGroupBidAmt는 절대 불변(§0 강제 전환 금지) —
# useGroupBidAmt=true(또는 불명) 소재는 그룹입찰이 실효라 개별 bidAmt 수정이 무의미·혼란 →
# fail-closed 거부. 성공 판정은 여기서도 재조회 실측으로만(fail-closed) — PUT 응답 body는
# response 필드에 원본 기록용으로만 유지(update_keyword_bid와 동일 규율).


def get_ad(ncc_ad_id: str) -> dict:
    """GET /ncc/ads/{nccAdId} — 현재 소재 원본 JSON(adAttr·userLock·nccAdgroupId 포함).

    update_ad_bid의 before/after 재조회(검증)용 유일 소스(update_keyword_bid의 get_keyword
    대칭). adAttr은 JSON 문자열(공식 apidoc) — 파싱은 fetcher._parse_ad_attr 재사용(B1과
    단일 진실 소스, 추정 금지)."""
    resp = fetcher._get(f"/ncc/ads/{ncc_ad_id}")
    resp.raise_for_status()
    return resp.json()


def get_ad_bid(ncc_ad_id: str) -> int | None:
    """소재의 현재 실효 bidAmt(adAttr.bidAmt) 라이브 재조회 — harness 가드레일 컨텍스트·
    auto_operator 스텝 기준용(get_ad + _parse_ad_attr, 단일 진실 소스)."""
    ad = get_ad(ncc_ad_id)
    bid, _ = fetcher._parse_ad_attr(ad.get("adAttr"))
    return bid


def update_ad_bid(
    ncc_ad_id: str, bid_amt: int, *, expected_before_bid: int | None = None,
) -> WriteResult:
    """PUT /ncc/ads/{nccAdId}?fields=adAttr — 쇼핑 소재 개별 입찰가 변경(B3, D-NAO-65).

    adAttr의 bidAmt만 변경, useGroupBidAmt는 불변(§0 강제 전환 금지 — before가 false인
    소재에 false를 재전송할 뿐 강제 전환이 아니다). 대상 소재가 useGroupBidAmt=true(또는
    불명)면 그룹입찰이 실효라 개별 bidAmt 수정이 무의미·혼란 → fail-closed 거부
    (WriteValidationError). 부모 광고그룹이 ML 자동입찰이면 소재 bidAmt도 무시되므로
    update_adgroup_bid와 동일 사전가드(systemBiddingType/isAutobidActive)로 차단. bid_amt
    사전검증(50~100,000원·10원 단위 — VT4 P1-1 ad grain 하한 50원 _MIN_BID_GROUP_AD, 이중 방벽). 성공 판정 =
    재조회 실측(after adAttr.bidAmt==요청 ∧ useGroupBidAmt==false, update_keyword_bid의
    useGroupBidAmt 이중확인 동형). DB 접근 없음(순수 어댑터).

    Raises:
        WriteValidationError: bid_amt 범위 밖·10원 단위 아님 / before 재조회에서
            useGroupBidAmt가 false 아님(그룹입찰 실효 소재) / 부모 그룹 ML 자동입찰·상태불명.
        WriteError: PUT이 2xx 아님(재시도 없음 — 비멱등 쓰기).
        WriteVerificationError: PUT은 2xx였는데 재조회에 bidAmt 미반영 / useGroupBidAmt가
            false로 유지되지 않음(강제 전환 감지) / before에 nccAdgroupId 부재로 ML 재확인 불가 /
            before adAttr을 dict로 정규화 불가(병합 기반 소실 — 요청 body 구성 전 fail-closed).
    """
    if not (_MIN_BID_GROUP_AD <= bid_amt <= _MAX_BID) or bid_amt % _BID_INCREMENT != 0:
        raise WriteValidationError(
            f"update_ad_bid: bid_amt={bid_amt}는 유효 범위 밖({_MIN_BID_GROUP_AD}~100,000원, 10원 단위)"
        )

    before = get_ad(ncc_ad_id)
    _before_bid, before_ugba = fetcher._parse_ad_attr(before.get("adAttr"))

    # useGroupBidAmt=false 소재만 개별 bidAmt가 실효 — true/불명이면 그룹입찰이 실효라
    # 개별 수정 무의미(fail-closed on ambiguity, update_adgroup_bid ML 가드와 동형 규율).
    if before_ugba is not False:
        raise WriteValidationError(
            f"update_ad_bid: 소재 {ncc_ad_id}의 useGroupBidAmt가 false가 아님"
            f"(useGroupBidAmt={before_ugba!r}) — 그룹입찰이 실효 레버라 개별 bidAmt 수정 무의미"
            "(fail-closed, 강제 전환 금지 §0)"
        )

    # 부모 그룹 ML 가드: 부모가 ML 자동입찰이면 소재 bidAmt도 무시된다 → 차단(update_adgroup_bid
    # 와 동일 판정 — systemBiddingType 'NONE' 명시 + isAutobidActive explicit False만 수동 인정,
    # 그 외는 전부 fail-closed on ambiguity).
    parent_adgroup_id = before.get("nccAdgroupId")
    if not parent_adgroup_id:
        raise WriteVerificationError(
            f"update_ad_bid: 소재 {ncc_ad_id} 재조회에 nccAdgroupId 부재 — 부모 ML 가드 확인 "
            "불가(fail-closed)"
        )
    parent = _get_adgroup(parent_adgroup_id)
    system_bidding_type = parent.get("systemBiddingType")
    autobid_strategy = parent.get("autobidStrategy")
    autobid_active = autobid_strategy.get("isAutobidActive") if isinstance(autobid_strategy, dict) else None
    if system_bidding_type != "NONE" or autobid_active is not False:
        raise WriteValidationError(
            f"update_ad_bid: 소재 {ncc_ad_id}의 부모 그룹 {parent_adgroup_id}가 수동입찰로 확인 "
            f"불가(systemBiddingType={system_bidding_type!r}, isAutobidActive={autobid_active!r}) "
            "— ML 자동입찰이면 소재 bidAmt도 무시(fail-closed)"
        )

    path = f"/ncc/ads/{ncc_ad_id}"
    # body = **GET 원본 전체 객체**에 adAttr만 dict로 교체(update_adgroup_bid와 동일 규율).
    # 라이브 실측(2026-07-21, 원칙22): ①adAttr를 json.dumps 문자열로 보내면 400 code 3830
    # "Invalid ad type" ②{nccAdId, nccAdgroupId, adAttr(객체)} 최소 body도 동일 400 ③GET 전체
    # 객체 + adAttr(객체) 교체는 200(prod 무해 프로브로 확정 — type 등 전체 필드가 있어야
    # 네이버가 소재 타입을 판정한다). adAttr는 before 서브필드 보존 병합 + bidAmt 갱신,
    # useGroupBidAmt=false는 before와 동일값 재전송(불변 — 강제 전환 아님). fields=adAttr로
    # 부분교체 명시(다른 필드는 전송돼도 수정 대상 아님).
    before_attr = before.get("adAttr")
    if isinstance(before_attr, str):
        try:
            base_attr: object = json.loads(before_attr)
        except (ValueError, TypeError):
            base_attr = None
    else:
        base_attr = before_attr
    if not isinstance(base_attr, dict):
        # 여기 도달 시 위 _parse_ad_attr가 useGroupBidAmt=False를 이미 확인했으므로 adAttr은
        # 정상 파싱 가능한 상태이나, 방어적으로 병합 기반 소실을 fail-closed(추정 금지).
        raise WriteVerificationError(
            f"update_ad_bid: 소재 {ncc_ad_id}의 before adAttr을 dict로 정규화 불가"
            f"(type={type(before_attr).__name__}) — 병합 기반 소실, fail-closed"
        )
    ad_attr = dict(base_attr)
    ad_attr["bidAmt"] = bid_amt
    ad_attr["useGroupBidAmt"] = False
    body = dict(before)
    body["adAttr"] = ad_attr
    # ★D-NAO-129 codex 적대 2R·3R[P1] — 쓰기 직전 **최신 상태 재확인 + body 재조립**.
    #   ①왜 PUT 직전인가(2R): 가드레일은 executor가 그 시점에 읽은 값을 기준으로 ±15%·방향을
    #     판정한다. 그 기준이 쓰기 시점에 달라져 있으면 검증한 변경폭이 성립하지 않는다
    #     (800 판정 → 외부가 400으로 내림 → 그대로 PUT하면 실제 +130%). 오늘 15:39 대행사가
    #     폴드8와이드 소재를 1,600→1,000으로 되돌린 것이 바로 이 부류다(D-NAO-126).
    #   ②왜 bidAmt만 보면 안 되는가(3R): body를 **첫 GET**으로 조립해 두면, 그 사이 외부가
    #     useGroupBidAmt를 true로 바꿔도 우리가 false를 다시 써서 **남의 변경을 조용히 되돌린
    #     뒤 성공으로 기록**한다(after 검증은 false를 성공 조건으로 보므로 통과하고, 최종
    #     editTm이 우리 것이라 D-NAO-127 사후 탐지도 "우리 쓰기"로 분류한다 = 아무도 못 잡는다).
    #     그래서 최신 응답으로 **body를 다시 만들고** bidAmt·useGroupBidAmt·부모그룹을 전부
    #     재검증한다 — 우리는 우리가 바꾸려는 필드만 바꾼다.
    #   ★한계를 정직하게: 네이버 SA API에는 조건부 쓰기(If-Match류)가 없다. 이건 **원자적 CAS가
    #     아니고**, 이 GET과 아래 PUT 사이 왕복 한 번이 잔여 창으로 남는다(초판은 부모그룹 조회·
    #     body 조립까지 창에 포함돼 훨씬 넓었다). 완전한 제거는 API가 조건부 쓰기를 줘야 가능하다.
    if expected_before_bid is not None:
        latest = get_ad(ncc_ad_id)
        latest_bid, latest_ugba = fetcher._parse_ad_attr(latest.get("adAttr"))
        if latest_bid != expected_before_bid:
            raise WriteValidationError(
                f"update_ad_bid: 소재 {ncc_ad_id}의 입찰이 판정 이후 바뀜(기준가 불일치) — "
                f"가드레일 판정 기준={expected_before_bid}원, PUT 직전 실측={latest_bid}원. "
                "검증된 변경폭이 성립하지 않아 쓰지 않는다(fail-closed, D-NAO-129). "
                "다음 회차가 새 현재값으로 다시 판정한다"
            )
        if latest_ugba is not False:
            raise WriteValidationError(
                f"update_ad_bid: 소재 {ncc_ad_id}의 useGroupBidAmt가 판정 이후 {latest_ugba!r}로 "
                "바뀜 — 우리가 false를 다시 써서 그 변경을 되돌리지 않는다(fail-closed, "
                "D-NAO-129 codex 3R). 그룹입찰 전환은 사람이 판단할 일이다"
            )
        if latest.get("nccAdgroupId") != parent_adgroup_id:
            raise WriteValidationError(
                f"update_ad_bid: 소재 {ncc_ad_id}가 판정 이후 다른 그룹으로 이동 "
                f"({parent_adgroup_id} → {latest.get('nccAdgroupId')!r}) — 부모 ML 가드를 통과한 "
                "그룹이 아니므로 쓰지 않는다(fail-closed)"
            )
        # ★body를 최신 응답으로 다시 만든다(위 ②) — 그 사이 바뀐 다른 필드를 덮지 않기 위해서다.
        latest_attr = latest.get("adAttr")
        if isinstance(latest_attr, str):
            try:
                latest_attr = json.loads(latest_attr)
            except (ValueError, TypeError):
                latest_attr = None
        if not isinstance(latest_attr, dict):
            raise WriteVerificationError(
                f"update_ad_bid: 소재 {ncc_ad_id}의 최신 adAttr을 dict로 정규화 불가"
                f"(type={type(latest.get('adAttr')).__name__}) — 병합 기반 소실, fail-closed"
            )
        ad_attr = dict(latest_attr)
        ad_attr["bidAmt"] = bid_amt
        ad_attr["useGroupBidAmt"] = False
        body = dict(latest)
        body["adAttr"] = ad_attr

    log.info("Naver SA 쓰기 시도: update_ad_bid ad=%s bidAmt=%s", ncc_ad_id, bid_amt)
    resp = requests.put(
        fetcher.BASE_URL + path,
        headers=fetcher._headers(path, method="PUT"),
        params={"fields": "adAttr"},
        json=body,
        timeout=30,
    )
    if not (200 <= resp.status_code < 300):
        log.error(
            "Naver SA 쓰기 실패: update_ad_bid ad=%s status=%s body=%s",
            ncc_ad_id, resp.status_code, resp.text[:300],
        )
        raise WriteError(
            f"update_ad_bid 실패: status={resp.status_code} body={resp.text[:300]}"
        )

    try:
        response_body = resp.json()
    except ValueError:
        response_body = None

    after = get_ad(ncc_ad_id)
    after_bid, after_ugba = fetcher._parse_ad_attr(after.get("adAttr"))
    if after_bid != bid_amt:
        raise WriteVerificationError(
            f"update_ad_bid: 쓰기 응답은 성공(status={resp.status_code})이나 재조회에 반영되지 "
            f"않음(fail-closed): 요청={bid_amt} 재조회={after_bid}"
        )
    # useGroupBidAmt 불변 확인(§0) — 우리 쓰기가 실수로 그룹입찰 커플링을 전환하지 않았는지,
    # 또는 다른 행위자가 전환하지 않았는지 이중확인(update_keyword_bid의 useGroupBidAmt 검증 동형).
    if after_ugba is not False:
        raise WriteVerificationError(
            f"update_ad_bid: bidAmt는 반영됐으나 useGroupBidAmt가 false로 유지되지 않음"
            f"(fail-closed 강제 전환 감지) — 재조회 useGroupBidAmt={after_ugba!r}"
        )

    log.info("Naver SA 쓰기 성공: update_ad_bid ad=%s bidAmt=%s", ncc_ad_id, bid_amt)
    return WriteResult(
        action="update_ad_bid", before=before, response=response_body, after=after, created_ids=[],
    )
