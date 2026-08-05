# rocket_promo_file.py — 1P 프로모션 제안서(MD 제출 엑셀) 순수 파서 SA
#   (트랙 coupang-promo-pnl, D-CPP-10)
#
# 무엇을 파싱하나: Jino가 쿠팡 BM에게 제출하는 **즉시할인 제안서 엑셀**의 파일명과 행.
#   이 파일이 개당 할인액의 **유일한 출처**다 — 2026-08-05 라이브 실측으로 확정:
#     ①프로모션 목록/상세 API   : discountBudget(총예산)·supplierFundRate(분담%)뿐, 개당 할인액 없음
#     ②프로모션 상세 **화면**    : 「할인액(원)」 칸이 **빈칸**, 「적용 상품」은 개수만(목록 없음)
#     ③계약서 본문(contract details): 대상 상품이 "첨부. 즉시할인 대상 상품"으로 빠져 본문에 없음
#   세 표면에 다 없으므로 파일이 권위값이다(D-CPP-7의 "수기 1칸"을 파일 자동수집으로 대체).
#
# 단일 책임(원칙18-1): 파일명·행의 값 정규화·검증만. HTTP 없음, DB 없음, 파일 IO 없음.
#   프로모션 매칭(기간→request_id)과 적재는 services/coupang/rocket_promo_file_sync.py(Harness).
# 방어적 파싱: 규칙 밖 파일명·불량 행은 **그것만 skip**하고 이유를 남긴다 — 하나가 배치를 죽이지 않는다.
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

log = logging.getLogger(__name__)

# 파일명 규칙(2026-08-05 Jino 확정):
#   instant_upload_template_<시작YYMMDD>_<종료YYMMDD>_<라벨>.xlsx
#   예) instant_upload_template_260723_260815_플립폴드8시리즈
#   라벨은 Jino가 알아보기 위한 자유 텍스트 — 기간이 같은 프로모션이 둘일 때만 가름에 쓴다.
_NAME_RE = re.compile(r"^instant_upload_template_(\d{6})_(\d{6})_(.+)$")

# 할인 타입 정규화. 엑셀 헤더: "필수값_할인타입_정액수량또는정률_택1입력"
FIXED = "정액수량"
RATE = "정률"
_TYPE_ALIAS = {
    "정액수량": FIXED,
    "정액": FIXED,
    "정률": RATE,
    "정율": RATE,   # 오타 실사용 대비(맞춤법상 '정률'이 맞다)
}

# 정률 상한. 우리 실측 표본은 0.2(=20%) 하나뿐이라 **비율 표기만** 받는다.
#   20(=20%) 같은 퍼센트 표기를 알아서 100으로 나누지 않는다 — 추측 금지(금지선).
#   1을 넘으면 "20%인지 2000%인지" 우리가 정할 수 없으므로 skip하고 소리내어 남긴다.
_RATE_MAX = Decimal("1")


def normalize_name(name: str) -> str:
    """파일명을 NFC로 정규화한다.

    ★왜 필요한가(2026-08-05 라이브에서 실제로 깨졌다): macOS 파일시스템은 한글 파일명을
      **NFD(자모 분리)** 로 돌려주는데 쿠팡 API/DB의 한글은 **NFC(완성형)** 다. 눈으로는
      똑같이 "폴드7"인데 `==`도 `in`도 False가 되어, 기간이 같은 프로모션 둘을 라벨로
      가르는 단계가 **조용히** 실패했다(매칭 0건인데 에러도 없음).
    """
    return unicodedata.normalize("NFC", name or "")


def _yymmdd(s: str) -> date | None:
    try:
        return date(2000 + int(s[0:2]), int(s[2:4]), int(s[4:6]))
    except ValueError:
        return None


def parse_file_name(name: str) -> dict | None:
    """제안서 파일명 → {period_from, period_to, label, name}. 규칙 밖이면 None.

    name은 확장자를 뺀 basename이어야 한다(페처가 벗겨서 준다).
    """
    n = normalize_name(name).strip()
    m = _NAME_RE.match(n)
    if not m:
        return None
    pf, pt = _yymmdd(m.group(1)), _yymmdd(m.group(2))
    if pf is None or pt is None:
        log.warning("제안서 파일명 날짜 파싱 실패 → skip: %r", n)
        return None
    if pt < pf:
        log.warning("제안서 파일명 기간 역전(%s > %s) → skip: %r", pf, pt, n)
        return None
    return {"name": n, "period_from": pf, "period_to": pt, "label": m.group(3).strip()}


def _dec(v: Any) -> Decimal | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s == "":
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def parse_discount_rows(rows: list[dict], *, stats: dict | None = None) -> list[dict]:
    """제안서 행 → [{product_number, discount_type, discount_value}].

    rows는 페처가 엑셀에서 뽑아 보낸 레코드 계약:
        {"product_number": "76350897", "discount_type": "정액수량", "discount_value": 3000}
    엑셀 상단의 안내문·헤더 행은 페처가 이미 걸렀지만, 여기서도 한 번 더 방어한다
    (SKUID가 숫자가 아니면 안내문 행이다).

    검증 규칙:
      · product_number: 숫자 문자열, 30자 이하 (그레인 키 — 자르지 않고 skip)
      · 정액수량: 0 초과. 원 단위.
      · 정률   : 0 초과 1 이하(비율). 1 초과는 표기 의미가 확정 안 돼 skip(위 _RATE_MAX 주석).
    """
    st = stats if stats is not None else {}
    out: list[dict] = []
    seen: set[str] = set()
    for r in rows or []:
        if not isinstance(r, dict):
            st["skipped"] = st.get("skipped", 0) + 1
            continue
        sku = str(r.get("product_number") or "").strip()
        if not sku.isdigit() or len(sku) > 30:
            st["skipped"] = st.get("skipped", 0) + 1
            continue
        raw_type = str(r.get("discount_type") or "").strip().replace(" ", "")
        dtype = _TYPE_ALIAS.get(raw_type)
        val = _dec(r.get("discount_value"))
        if dtype is None or val is None:
            log.warning("제안서 행 skip(타입/값 불량) sku=%s type=%r value=%r", sku, raw_type, r.get("discount_value"))
            st["skipped"] = st.get("skipped", 0) + 1
            continue
        if val <= 0:
            log.warning("제안서 행 skip(할인값 0 이하) sku=%s value=%s", sku, val)
            st["skipped"] = st.get("skipped", 0) + 1
            continue
        if dtype == RATE and val > _RATE_MAX:
            # 0.2=20%만 실측됐다. 20을 20%로 접으면 100배 틀린 분담금이 조용히 들어간다.
            log.warning("제안서 행 skip(정률이 1 초과 — 비율/퍼센트 표기 불명) sku=%s value=%s", sku, val)
            st["skipped"] = st.get("skipped", 0) + 1
            continue
        if sku in seen:
            st["deduped"] = st.get("deduped", 0) + 1
            continue
        seen.add(sku)
        out.append({"product_number": sku, "discount_type": dtype, "discount_value": val})
    return out
