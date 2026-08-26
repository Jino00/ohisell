// costImportedGoods.ts — 수입 완제품 종의 «표지»와 그 표지가 여는 것 (계약 D-CPP-61 §4-Q1)
//
// ★**왜 `CostPage.tsx`가 아니라 여기인가**: 컴포넌트 파일에서 함수를 export 하면
//   `react-refresh/only-export-components`가 경고를 낸다. 이 저장소의 CI는 프론트 lint를
//   **96 warnings 상한**으로 래칫해 두었고(`.github/workflows/ci.yml`), 그 주석이
//   *"이 값을 다시 올리려면 «게이트가 꺼져 있었다» 수준의 근거가 있어야 한다"*고 못 박았다.
//   실제로 초판이 이 두 함수를 `CostPage.tsx`에 두어 **98(상한 96)**로 CI가 빨갛게 떴다 —
//   n=11이 겪은 것과 같은 자리다(그때는 +1, 이번엔 +2).
//   ⇒ 상한을 올리는 게 아니라 **경고를 안 만드는 자리로 옮긴다.**

import type { CostLedgerMaterialLine, CostMaterial } from "./api";

/** `cost_material.category` — 수입 완제품 종의 표지 (백엔드 `IMPORTED_GOODS_CATEGORY`와 같은 값). */
export const IMPORTED_GOODS_CATEGORY = "수입 완제품";

/** 이 종에 원장 `product` 라인을 붙일 수 있나.
 *
 * ★표지가 서는 자리는 **픽 하나뿐**이다 — 사람이 「이 레시피는 수입 완제품이다」라고 고른
 * 순간이고, 그 앞엔 아무 문도 안 열려 있다. 그래서 이 판정을 화면이 스스로 넓히면 안 된다. */
export function isImportedGoodsMaterial(m: CostMaterial | null): boolean {
  return m?.category === IMPORTED_GOODS_CATEGORY;
}

/** 이 수입 완제품 종에 **고를 수 있는** 원장 완제품 라인 — 아직 아무 종에도 안 붙은 것 + 이 종 것.
 *
 * ★남의 종에 이미 붙은 라인은 뺀다(같은 로트가 두 번 세지면 이력이 거짓말이 된다 —
 * `link_ledger_line`의 dup 규율과 같은 이유). 이 종에 붙은 것은 남긴다: 붙어 있는 것이
 * 안 보이면 「연결했나 안 했나」를 화면이 못 말한다. */
export function pickableProductLines(
  rows: CostLedgerMaterialLine[],
  materialId: number,
): CostLedgerMaterialLine[] {
  return rows.filter(
    (r) =>
      r.line_type === "product" &&
      (r.linked_material_id === null || r.linked_material_id === materialId),
  );
}
