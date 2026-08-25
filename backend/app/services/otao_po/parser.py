"""발주서(Purchasing order) PDF → 라인 추출.

스크래치 검증(`parse_po.py`, 226줄)에서 실제 발주서 폴더 전체(121파일, 발주서 95건)로
**수량 검산 95/95 통과**를 확인한 뒤 그대로 이식했다 — 로직은 바꾸지 않았다(이식 중 변경점은
없음, `parse_order_pdf`/`scan_order_folder`로 공개 API만 함수 이름을 정리했다).

## 실측으로 발견된 함정 5가지 (반드시 이 문서를 먼저 읽고 로직을 건드릴 것)

1. **여러 페이지 문서는 페이지마다 `Q;ty`가 그 페이지 서브토탈이다** — 문서 전체 합이 아니다.
   그래서 헤더 수량은 문서 안의 모든 occurrence를 **합산**해야 라인 전체 합과 맞는다. 반대로
   "총합계"(금액)는 페이지마다 반복돼도 항상 문서 전체 금액과 같으므로 **마지막 occurrence
   하나만** 쓴다. 이 둘을 같은 방식으로 처리하면(둘 다 합산하거나 둘 다 마지막만) 검산이 깨진다.
2. **단가·금액이 통째로 빈 라인이 있어 「꼬리 정규식 사이 텍스트」 방식은 코드가 한 칸씩 밀린다.**
   그래서 행 파싱은 Product Code를 **줄 앞 앵커로 먼저 잡고**, 다음 앵커까지의 구간을 그 행의
   텍스트로 확정한 뒤, 그 구간 **안에서** 꼬리(수량/통화/단가/금액)를 찾는다 — 꼬리가 없거나
   부분적이어도 앵커 경계 자체는 흔들리지 않는다.
3. **영문상품명이 줄바꿈되며 `IP17` 같은 토큰이 줄 맨 앞에 온다 — 가짜 Product Code 앵커가 된다.**
   진짜 코드 뒤에는 항상 `[`(Remarks 칸 등) 또는 한글(상품명)이 오므로, 그 규칙으로 가짜를 거른다.
4. **코드 자체가 두 줄에 걸치기도 한다**(`GCAPIP15PR_15\nPM`). 1차 앵커 매치 뒤 텍스트가 바로
   설명(한글/`[`)이 아니면, 다음 줄의 짧은 continuation 토큰을 코드에 이어붙이고 나서 다시
   판정한다.
5. **2023년 문서는 수량이 `30ea` 꼴이고, 그 앞에 상품명의 `2ea`(2개입 표기)가 공백 없이 붙어
   있다** — `2ea30ea`. 수량 정규식이 "숫자+선택적ea+공백+통화"를 요구하므로 공백이 없는 앞쪽
   `2ea`는 자연히 안 걸리고 뒤쪽 `30ea`만 매칭된다.

그 외 열 구성이 두 가지다: A형(Product Code|한글상품명|영문상품명|Quantity|Currency|
Unit price|Total amount|Remarks), B형(영문상품명 칸이 없음). 행 텍스트에서 "마지막 한글 문자"
뒤에 라틴 문자가 남아 있으면 그게 영문상품명이고, 없으면(B형) `None`이다 — 없는 값을 한글명에서
지어내지 않는다.

## 공개 API
- `parse_order_pdf(path) -> dict | None` — 파일 하나. 발주서가 아니면(Serial No. 없음) `None`.
- `scan_order_folder(root) -> list[dict]` — 폴더를 훑어 발주서만 반환.
- `parse_po_text(text, *, source_path=None) -> dict | None` — 텍스트 → 결과(테스트용 진입점,
  PDF 추출과 분리해 pypdf 없이도 파싱 로직을 검증할 수 있게 한다).

## 출력 스키마
문서: `{serial, path, header_qty, header_amount, line_qty_sum, line_amount_sum, dropped, lines}`
라인: `{serial, code, name_ko, name_en, qty, currency, unit_price, amount, blank_qty}`
"""
from __future__ import annotations

import math
import os
import re
from typing import Any

SERIAL = re.compile(r"Serial No\.\s*([0-9]{8}-[0-9]+)")
QTY_HDR = re.compile(r"Q;ty\s*([0-9,]+)")
TOTAL_AMT = re.compile(r"총합계\s*([0-9][0-9,]*\.?[0-9]*)")

# 행(row) 시작 후보: 라인 맨 앞에 오는 대문자+숫자+밑줄 토큰(4자 이상). 진짜 Product Code인지는
# _find_anchors()가 뒤따르는 텍스트를 보고 가른다 — 영문상품명 줄바꿈 중간에 "IP17"처럼 우연히
# 줄 맨 앞에 오는 토큰과, "GCAPIP15PR_15\nPM"처럼 코드 자체가 줄바꿈으로 두 줄에 걸치는 경우를
# 구분해야 하기 때문에 정규식 하나로는 안 된다(실측 두 가지 다 발생, 함정 3·4).
CODE_CAND = re.compile(r"(?m)^\s*([A-Z][A-Z0-9_]{3,20})\b")
CODE_CONT = re.compile(r"^\s*([A-Z0-9_]{1,6})\b")
NEXT_IS_DESC = re.compile(r"^\s*[\[가-힣]")

CUR = r"(CNY|US\$|USD|KRW)"
# 수량 숫자 시작 지점 가드: 공백/문장시작 뒤이거나, "2ea50"처럼 상품명 뒤에 붙은 "ea" 바로
# 뒤(구형 문서 포장수량 접미사, 함정 5)여야 진짜 수량이다. "Glass_Ip17"의 "17"처럼 글자에 바로
# 붙은 숫자(구분자 "ea" 없이)는 모델번호일 뿐 수량이 아니므로 배제한다.
QTY_START = r"(?:(?<![A-Za-z0-9_,])|(?<=ea))"
# 라인 꼬리 - 풀: <수량>[ea] <통화> <단가> <금액>. 통화↔단가 사이는 "CNY1,506.36"처럼 공백이
# 아예 없는 문서가 실재해 \s*로 둔다(통화↔수량 사이는 항상 공백이 있어 \s+ 유지).
ROW_FULL = re.compile(
    QTY_START + r"([0-9][0-9,]*)(?:\s?ea)?\s+" + CUR + r"\s*([0-9][0-9,]*\.?[0-9]*)\s+([0-9][0-9,]*)"
)
# 라인 꼬리 - 부분(단가·금액 미기재): <수량>[ea] <통화>
ROW_PARTIAL = re.compile(QTY_START + r"([0-9][0-9,]*)(?:\s?ea)?\s+" + CUR)
# 수량 자체가 완전히 빈 칸인 행(예: "Privacy Glass_Ip17   CNY 19.2    ") — 통화만이라도 잡는다.
CUR_ONLY = re.compile(CUR)

HANGUL_RUN = re.compile(r".*[가-힣]", re.S)  # greedy → 마지막 한글 문자까지
TRAIL_FILLER = re.compile(r"[\s\d\]]*")  # 한글 뒤에 붙는 괄호닫힘류 찌꺼기


def _int(s: str) -> int:
    return int(str(s).replace(",", ""))


def _find_anchors(body: str) -> list[tuple[str, int, int]]:
    """body 안에서 진짜 Product Code 행 시작 위치를 찾는다 (함정 2·3·4).

    반환: `[(code_str, code_start_offset, desc_start_offset), ...]`
    - `code_start_offset`: 코드 토큰이 시작하는 위치(다음 행의 경계로 쓴다)
    - `desc_start_offset`: 코드(및 줄바꿈 이어붙은 continuation)를 지난 바로 다음 위치,
      즉 그 행의 설명 텍스트가 시작하는 지점(이 행 자신의 시작으로 쓴다)
    """
    out: list[tuple[str, int, int]] = []
    for m in CODE_CAND.finditer(body):
        code = m.group(1)
        after = body[m.end():]
        if NEXT_IS_DESC.match(after):
            out.append((code, m.start(), m.end()))
            continue
        cm = CODE_CONT.match(after)
        if cm and NEXT_IS_DESC.match(after[cm.end():]):
            out.append((code + cm.group(1), m.start(), m.end() + cm.end()))
            continue
        # 진짜 코드가 아니다(영문상품명 줄바꿈 중 우연히 대문자 토큰이 줄 앞에 온 경우) — 버린다.
    return out


def _split_name(name_area: str) -> tuple[str, str | None]:
    """행 텍스트에서 qty 매치 앞부분(name_area)을 한글상품명/영문상품명으로 가른다."""
    m = HANGUL_RUN.search(name_area)
    if not m:
        ko, en = "", name_area
    else:
        ko_end = m.end()
        tm = TRAIL_FILLER.match(name_area, ko_end)
        ko_end = tm.end() if tm else ko_end
        ko, en = name_area[:ko_end], name_area[ko_end:]
    ko = " ".join(ko.split())
    en = " ".join(en.split())
    return ko, (en or None)


def parse_po_text(text: str, *, source_path: str | None = None) -> dict[str, Any] | None:
    """텍스트(이미 추출된 PDF 텍스트) → 발주서 딕셔너리. `Serial No.`가 없으면 `None`.

    PDF I/O에서 분리한 순수 함수 — pypdf 없이도(텍스트 픽스처만으로) 파싱 로직을 검증할 수 있다.
    """
    m = SERIAL.search(text)
    if not m:
        return None
    serial = m.group(1)

    # 헤더 Q;ty: 페이지마다 그 페이지 라인합의 서브토탈을 찍으므로 전체 occurrence를 합산한다
    # (함정 1). 총합계(금액)는 페이지마다 반복돼도 항상 문서 전체 금액과 같으므로 마지막
    # occurrence만 쓴다.
    qtys = [_int(x) for x in QTY_HDR.findall(text)]
    header_qty = sum(qtys) if qtys else None

    amt_matches = TOTAL_AMT.findall(text)
    header_amount = _int(amt_matches[-1]) if (amt_matches and amt_matches[-1].strip()) else None

    first_q = QTY_HDR.search(text)
    body = text[first_q.end():] if first_q else text
    last_tot = None
    for tm in re.finditer("총합계", body):
        last_tot = tm.start()
    if last_tot is not None:
        body = body[:last_tot]

    anchors = _find_anchors(body)  # [(code, code_start, desc_start), ...]
    lines: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for i, (code, _code_start, row_start) in enumerate(anchors):
        row_end = anchors[i + 1][1] if i + 1 < len(anchors) else len(body)
        row_text = body[row_start:row_end]

        blank_qty = False
        fm = ROW_FULL.search(row_text)
        if fm:
            name_area = row_text[:fm.start()]
            qty = _int(fm.group(1))
            currency = fm.group(2)
            unit_price = float(fm.group(3).replace(",", ""))
            amount = _int(fm.group(4))
            # 일부 구형 문서는 "Remarks" 칸이 "Total amount" 칸과 공백 없이 붙어 추출된다
            # (실측: 20231226-1 "74,5552500" = 금액 74,555 + Remarks 글자수 2500). 그 결과
            # 금액이 자릿수 뒤섞인 거대한 숫자로 잡힌다. qty×단가로 재계산한 값이 캡처된
            # 숫자열의 "접두사"와 맞아떨어지면 그게 진짜 금액이고 나머지는 Remarks 찌꺼기다.
            expected = math.floor(qty * unit_price + 0.5)  # 반올림(0.5는 올림) — PDF 표기 방식
            raw_digits = fm.group(4).replace(",", "")
            if amount != expected and raw_digits.startswith(str(expected)) and str(expected) != raw_digits:
                amount = expected
        else:
            pm = ROW_PARTIAL.search(row_text)
            if pm:
                name_area = row_text[:pm.start()]
                qty = _int(pm.group(1))
                currency = pm.group(2)
                unit_price = None
                amount = None
            else:
                # 수량 자체가 완전히 빈 칸인 행 — 통화 토큰만이라도 남아 있으면 그것으로
                # "가격 미정·수량 미정" 라인임을 기록한다(0으로 억지로 채우지 않는다).
                cm = CUR_ONLY.search(row_text)
                if not cm:
                    dropped.append({"code": code, "raw": " ".join(row_text.split())[:120]})
                    continue
                name_area = row_text[:cm.start()]
                qty = None
                currency = cm.group(1)
                unit_price = None
                amount = None
                blank_qty = True

        name_ko, name_en = _split_name(name_area)
        lines.append({
            "serial": serial,
            "code": code,
            "name_ko": name_ko,
            "name_en": name_en,
            "qty": qty,
            "currency": currency,
            "unit_price": unit_price,
            "amount": amount,
            "blank_qty": blank_qty,
        })

    line_qty_sum = sum(l["qty"] for l in lines if l["qty"] is not None)
    line_amount_sum = sum(l["amount"] for l in lines if l["amount"] is not None)
    return {
        "serial": serial,
        "header_qty": header_qty,
        "header_amount": header_amount,
        "lines": lines,
        "dropped": dropped,
        "line_qty_sum": line_qty_sum,
        "line_amount_sum": line_amount_sum,
        "path": source_path,
    }


def extract_pdf_text(path: str) -> str:
    """발주서 PDF에서 텍스트를 뽑는다. `pypdf`는 선택 의존성이다(같은 이유로
    `app/services/import_cost/parser.py`도 지연 import를 쓴다 — 서버에 없어도 나머지 기능은
    죽지 않아야 한다).
    """
    import pypdf  # noqa: PLC0415 — 선택 의존성

    reader = pypdf.PdfReader(path)
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def parse_order_pdf(path: str) -> dict[str, Any] | None:
    """PDF 파일 하나를 파싱한다. 발주서가 아니면(Serial No. 없음) `None`."""
    text = extract_pdf_text(path)
    return parse_po_text(text, source_path=path)


def scan_order_folder(root: str) -> list[dict[str, Any]]:
    """`root` 아래를 재귀로 훑어 발주서 PDF만 파싱해 반환한다. 비발주서는 조용히 건너뛴다."""
    out: list[dict[str, Any]] = []
    for dirpath, _dirs, files in os.walk(root):
        for fname in sorted(files):
            if not fname.lower().endswith(".pdf"):
                continue
            result = parse_order_pdf(os.path.join(dirpath, fname))
            if result is not None:
                out.append(result)
    return out
