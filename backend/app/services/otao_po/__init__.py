"""OTAO 발주서(Purchasing order) PDF 파싱·집계. D-INV-1~4.

- `parser.py`   — PDF/텍스트 → 라인 딕셔너리 (순수 함수, DB 무접촉)
- `name_map.py` — 통관 원장 품목명 → 상품코드 사전 (Jino 확정 규칙 3 집행)
- `roster.py`   — S1 3칸 집계 (발주 누계 · 픽업 누계 · 예약 잔량)

★**아직 없는 것** (체인 `발주예측` n=5 몫이다 — 있다고 적어 두면 다음 세션이 속는다):
- `ingest.py` — 파싱 결과를 `otao_purchase_order`/`_line`에 적재(멱등·정본 판정 D-INV-3).
  모델과 마이그레이션은 이미 있고 **적재기만 비어 있다.** 그래서 prod 테이블은 비어 있고
  `roster.build_roster()`는 지금 빈 로스터를 돌려준다. 화면(API·페이지)도 아직 없다.
- 테스트 — `parse_po_text`가 순수 함수로 분리돼 있어 PDF 없이 텍스트 픽스처로 짤 수 있다.
"""
