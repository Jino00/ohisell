// costPriceGate.ts — `product_master.cost_price`가 «어디서만» 바뀌는지 화면이 말하는 한 문장.
//
// ★왜 상수인가 (계약 D-CPP-64 §4 S1-②): 같은 문장을 백엔드 거부 응답과 화면 두 곳이 쓴다.
//   문구가 두 벌이면 한쪽만 고쳐지고, 그러면 **API는 A라 하고 화면은 B라 하는** 상태가 된다
//   — 이 저장소가 반복해 겪은 「값이 도는 층과 사람이 읽는 층이 갈린다」의 문구판이다.
//   백엔드 정본은 `backend/app/services/cost_price_history.py:REJECTION_SENTENCE`이고,
//   두 문자열이 같은지는 `backend/tests/test_cost_price_gate_and_history.py`가 **이 파일을
//   읽어** 단언한다.
//
// ★원칙 자체(계약 §2-0, Jino 2026-08-31): *"원가는 무조건 sellC의 원가 메뉴를 참고해"*
//   ⇒ `cost_price`는 원가 메뉴 정본의 사본이지 독립 사실이 아니다.

/** 백엔드 `REJECTION_SENTENCE`와 **글자 그대로 같아야 한다**(테스트가 지킨다). */
export const COST_PRICE_REJECTION_SENTENCE =
  "원가는 원가 메뉴가 정본이다 — 정정은 원가 메뉴에서";

/** 원가 메뉴 경로 — 화면이 「어디로 가야 하나」를 링크로 준다(문장만 주면 길을 모른다). */
export const COST_MENU_PATH = "/cost";

/** 원가를 **사람이 읽는 한 가지 모양**으로. 못 읽는 값이면 원문을 그대로 보여 준다 —
 *  「0」으로 접으면 «모르는 값»이 «0원»으로 둔갑한다(「없음 ≠ 0」).
 *
 *  ★`number | string`을 다 받는 이유: `ProductOut.cost_price`가 `Decimal`이라 라이브 JSON은
 *  **문자열** `"2350.70"`을 준다. `api.ts`의 `Product.cost_price: number`는 타입 거짓말이고,
 *  그걸 믿고 `.toLocaleString()`을 부르면 문자열에선 `Object.prototype` 판이 걸려 천단위
 *  구분 없는 원문이 그대로 뜬다(적대 리뷰 P2-3, 2026-08-31).
 *
 *  ★컴포넌트 파일이 아니라 여기 사는 이유: 컴포넌트 파일이 함수를 함께 export 하면
 *  `react-refresh/only-export-components` 경고가 뜬다(lint 경고 0 유지). */
export function formatCost(value: number | string): string {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n.toLocaleString() : String(value);
}

/** `cost_price` 변경 경로의 **사람이 읽는 이름**과 그 문이 지금 열려 있나.
 *
 * ★`open:false`는 「이 문은 계약 D-CPP-64 S1-②로 닫혔다」는 뜻이다. 닫힌 문의 이력 행이
 *   배포 «후» 시각으로 나타나면 그건 **가드가 샜다는 신호**이므로 화면이 그렇게 말한다 —
 *   닫았다고 선언하고 확인하지 않으면 그 선언은 주석일 뿐이다.
 * ★백엔드 어휘의 사본이다(`services/cost_price_history.py`의 `KNOWN_PATHS`).
 * ★컴포넌트 파일(`CostPage.tsx`)이 아니라 여기 사는 이유는 `formatCost`와 같다 —
 *   컴포넌트 파일이 상수·함수를 함께 export 하면 `react-refresh/only-export-components`
 *   경고가 뜨고, CI가 `eslint . --max-warnings 96`으로 그 예산을 강제한다. */
export const COST_PRICE_PATH_LABELS: Record<string, { label: string; open: boolean }> = {
  excel_upload: { label: "상품 원가표 엑셀 업로드", open: true },
  mapping_ingest: { label: "매핑 시트 업로드", open: true },
  product_create: { label: "상품 등록 화면", open: false },
  product_update: { label: "상품 수정 화면", open: false },
  cutover: { label: "원가 메뉴 컷오버", open: true },
  auto: { label: "정본 자동 추종", open: true },
};

/** 모르는 경로 이름도 **그대로 보여 준다** — 「기타」로 접으면 새 문이 생긴 것을 못 본다. */
export function costPricePathLabel(path: string): string {
  return COST_PRICE_PATH_LABELS[path]?.label ?? `알 수 없는 경로 «${path}»`;
}
