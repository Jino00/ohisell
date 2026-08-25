"""OTAO 발주서(Purchasing order) PDF 파싱·적재·집계. D-INV-1~4.

- `parser.py`   — PDF/텍스트 → 라인 딕셔너리 (순수 함수, DB 무접촉)
- `name_map.py` — 통관 원장 품목명 → 상품코드 사전 (Jino 확정 규칙 3 집행)
- `ingest.py`   — 폴더 → 원장 적재(멱등·정본 판정 D-INV-3) + 사전 동기화
- `roster.py`   — S1 3칸 집계 (발주 누계 · 픽업 누계 · 예약 잔량)

★**적재는 사람 머신에서 돈다** — 발주서 PDF가 Google Drive 동기화 폴더에 있고 prod 서버는
그 폴더를 못 본다. 원천의 위치가 정한 제약이지 설계 취향이 아니다(`ingest.py` docstring).

★회귀 그물은 `backend/tests/test_otao_po_ledger.py`다. n=4 적대 리뷰의 변이 4종이 **전건
생존**한 원인이 테스트 0건이었으므로, 이 패키지를 고칠 때는 그 파일을 같이 본다.
"""
