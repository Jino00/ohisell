// costPriceGate.ts — `product_master.cost_price`가 «어디서만» 바뀌는지 화면이 말하는 한 문장.
//
// ★왜 상수인가 (계약 D-CPP-64 §4 S1-②): 같은 문장을 백엔드 거부 응답과 화면 두 곳이 쓴다.
//   문구가 두 벌이면 한쪽만 고쳐지고, 그러면 **API는 A라 하고 화면은 B라 하는** 상태가 된다
//   — 이 저장소가 반복해 겪은 「값이 도는 층과 사람이 읽는 층이 갈린다」의 문구판이다.
//   백엔드 정본은 `backend/app/services/cost_price_history.py:REJECTION_SENTENCE`이고,
//   두 문자열이 같은지는 `backend/tests/test_cost_price_gate.py`가 **파일을 읽어** 단언한다.
//
// ★원칙 자체(계약 §2-0, Jino 2026-08-31): *"원가는 무조건 sellC의 원가 메뉴를 참고해"*
//   ⇒ `cost_price`는 원가 메뉴 정본의 사본이지 독립 사실이 아니다.

/** 백엔드 `REJECTION_SENTENCE`와 **글자 그대로 같아야 한다**(테스트가 지킨다). */
export const COST_PRICE_REJECTION_SENTENCE =
  "원가는 원가 메뉴가 정본이다 — 정정은 원가 메뉴에서";

/** 원가 메뉴 경로 — 화면이 「어디로 가야 하나」를 링크로 준다(문장만 주면 길을 모른다). */
export const COST_MENU_PATH = "/cost";
