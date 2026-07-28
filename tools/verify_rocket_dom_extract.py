#!/usr/bin/env python3
# verify_rocket_dom_extract.py — 로켓배송(1P) 페처 DOM 추출 JS의 **실동작** 실증 하니스.
#   왜 pytest가 아닌가: 백엔드 스위트는 브라우저를 띄우지 않는 방침이다
#   (backend/tests/test_fetcher_button_only_chrome.py — playwright를 스텁으로 막는다).
#   그래서 '실제 Chromium에서 실제 추출 함수를 돌리는' 검증은 이 스크립트가 담당한다.
#
# 무엇을 증명하는가 (2026-07-28 리뷰 지적: DOMParser 문서는 렌더링되지 않아 innerText가
#   textContent로 떨어지고, textContent는 요소 사이에 공백을 넣지 않는다):
#   ① 회귀 0     — ref20b 원본 HTML 추출 결과 == backend/tests의 _PO_DETAIL_ROWS(기록된 현행 출력)
#   ② 공백 독립성 — 태그 사이 공백을 전부 제거한(미니파이) HTML도 **동일한** rows
#   ③ 바코드 보호 — 미니파이에서도 '바코드 상품명'이 분리된다(붙으면 인덱스 걸린 barcode 컬럼 오염)
#   ④ 정산 링크   — 들여쓰기 없이도 버튼 라벨과 전송상태 토큰이 분리된다
#
# 사용: python3 tools/verify_rocket_dom_extract.py       (0=통과, 1=실패, 2=환경 부족)
#   playwright + chromium 필요. prod·라이브 세션 무관(로컬 증거 HTML만 사용, 네트워크 없음).
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FETCHER = REPO / "tools" / "rocket_supplier_fetcher.py"
PO_HTML = REPO / "docs" / "references" / "data" / "20b_rocket_1p_po_detail_134342890.html"
TESTS = REPO / "backend" / "tests" / "test_rocket_supplier.py"


def _consts(path: Path, want: set[str]) -> dict:
    """지정한 최상위 대입문만 실행해 값을 얻는다(모듈 import 부작용 없이)."""
    ns: dict = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in want for t in node.targets):
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), ns)
    missing = want - set(ns)
    if missing:
        raise SystemExit(f"❌ {path.name}에서 상수를 찾지 못했다(이름 변경?): {missing}")
    return ns


def _extract(page, js: str, html: str, path: str) -> list:
    """페처의 _FETCH_*_JS를 원본 그대로 실행 — fetch 응답만 로컬 HTML로 물린다."""
    page.route("**/*", lambda route: route.fulfill(
        status=200, content_type="text/html; charset=utf-8", body=html))
    page.goto("http://rocket-1p.verify/base", wait_until="domcontentloaded")
    return (page.evaluate(js, [path]) or {}).get("rows") or []


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("❌ playwright 없음 — pip install playwright && playwright install chromium")
        return 2
    for p in (FETCHER, PO_HTML, TESTS):
        if not p.is_file():
            print(f"❌ 필요한 파일 없음: {p}")
            return 2

    js = _consts(FETCHER, {"_CELL_HELPERS_JS", "_FETCH_SETTLEMENT_JS", "_FETCH_PO_DETAIL_JS"})
    baseline = _consts(TESTS, {"_PO_DETAIL_ROWS"})["_PO_DETAIL_ROWS"]
    html = PO_HTML.read_text(encoding="utf-8")
    minified = re.sub(r">\s+<", "><", html)  # 실제 미니파이어가 하는 일(텍스트 내용은 그대로)

    fails: list[str] = []
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as e:  # noqa: BLE001
            print(f"❌ chromium 기동 실패 — playwright install chromium 필요: {str(e)[:120]}")
            return 2
        try:
            page = browser.new_context().new_page()
            po_path = "/scm/purchase/order/get/134342890"
            rows = _extract(page, js["_FETCH_PO_DETAIL_JS"], html, po_path)
            rows_min = _extract(page, js["_FETCH_PO_DETAIL_JS"], minified, po_path)
            # 정산 추출기(같은 헬퍼 공용) — 들여쓰기 없는 링크 셀
            settle_html = ("<html><body><table>"
                           "<tr><th>계산서번호</th><th>공급가액</th><th></th></tr>"
                           "<tr><td>30015106</td><td>204,437</td>"
                           "<td><a>발주현황</a><a>입고상세내역</a><a>전송성공</a></td></tr>"
                           "</table></body></html>")
            settle_rows = _extract(page, js["_FETCH_SETTLEMENT_JS"], settle_html, "/scm/settlement")
        finally:
            browser.close()

    def diff(label: str, got: list, exp: list) -> None:
        fails.append(label)
        for i in range(max(len(got), len(exp))):
            a = got[i] if i < len(got) else None
            b = exp[i] if i < len(exp) else None
            if a != b:
                print(f"   ✗ row{i}\n     got={a}\n     exp={b}")

    print(f"① 회귀 0 — ref20b 원본 추출 == _PO_DETAIL_ROWS (rows={len(rows)}/{len(baseline)})")
    if rows != baseline:
        diff("원본 추출이 기록된 fixture와 다르다", rows, baseline)
    else:
        print("   ✓ 완전 일치")

    print("② 공백 독립성 — 미니파이 HTML도 동일한 rows")
    if rows_min != baseline:
        diff("미니파이 추출이 fixture와 다르다", rows_min, baseline)
    else:
        print("   ✓ 마크업 들여쓰기에 의존하지 않는다")

    print("③ 바코드/상품명 분리(미니파이)")
    sku = next((r for r in rows_min if len(r) >= 12 and r[0] == "1"), None)
    if not sku or sku[2].split(" ")[0] != "8809465525057":
        fails.append(f"바코드 분리 실패: {sku[2][:60] if sku else '<SKU행 없음>'!r}")
    else:
        print(f"   ✓ barcode={sku[2].split(' ')[0]} / name={sku[2].split(' ', 1)[1][:30]}…")

    print("④ 정산 링크 셀 분리(들여쓰기 없음)")
    cell = settle_rows[1][-1] if len(settle_rows) > 1 and settle_rows[1] else ""
    if cell != "발주현황 입고상세내역 전송성공":
        fails.append(f"정산 전송상태 셀 분리 실패: {cell!r}")
    else:
        print(f"   ✓ {cell!r}")

    print()
    if fails:
        print("❌ FAIL")
        for f in fails:
            print("  -", f)
        return 1
    print("✅ 전부 통과 (회귀 0 + 공백 독립성)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
