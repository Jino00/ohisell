// @vitest-environment jsdom
//
// importedGoodsSurface.test.tsx — 수입 완제품(강화유리류) 원가가 「원장(실제 수입 서류)에서
// 나온 값」으로 보이는가 (계약 D-CPP-61, 2026-08-26 구현).
//
// ## 존재 이유
//
// 이 저장소의 상습병은 **「초록인데 아무것도 안 지키는 테스트」**다 — 함수는 값을 만드는데
// 화면엔 안 뜨는 결함이 반복 실측됐다(교훈 #321 계열, `costMaterialsSurface.test.tsx` 머리말
// 참조). 그래서 각 단언은 «함수가 값을 만드나»가 아니라 **«그 값이 사람 눈에 닿는가»**를
// 재고, 이 파일은 아래 세 표면을 순수 컴포넌트로 직접 렌더해서 지킨다:
//
//   ①표준원가 보드 — 원장 파생 단가 · 엑셀 표준 · 격차 %가 나란히 뜬다.
//   ②부자재 탭 — 수입 완제품 종에 원장 `product` 라인을 연결할 수 있다(첫 연결은 사람 몫).
//   ③레시피 — 폼팩터를 규칙 없이 `bar`로 단정한 레시피에 「추정」 표식이 뜬다.
//
// 이 파일이 죽이는 변이:
//   IG-1 `StandardCostBoard`의 `board-excel-standard` 칸을 `{null}`로 되돌리기
//        → 엑셀 표준값이 화면에서 사라진다
//   IG-2 `LedgerMaterialLines`의 `linkTargetId ?? r.suggestion.material_id`를
//        `r.suggestion.material_id`로 되돌리기 → 제안 없는 수입 완제품 종은 첫 연결 버튼이
//        영영 안 뜬다
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, within } from "@testing-library/react";

import { LedgerMaterialLines, StandardCostBoard } from "./CostPage";
// ★순수 헬퍼는 `CostPage.tsx`가 아니라 lib에 산다 — 컴포넌트 파일에서 함수를 export 하면
//   `react-refresh/only-export-components`가 울고 CI lint 상한(96)을 넘는다.
import {
  IMPORTED_GOODS_CATEGORY,
  isImportedGoodsMaterial,
  pickableProductLines,
} from "../lib/costImportedGoods";
import type {
  CostBoard,
  CostBoardRow,
  CostLedgerMaterialLine,
  CostMaterial,
} from "../lib/api";

afterEach(cleanup);

// ════════════════════════ 픽스처 ════════════════════════

/** 수입 완제품 종 — 강화유리류. `linkTargetId` 테스트가 이 id를 쓴다. */
function importedMaterial(over: Partial<CostMaterial> = {}): CostMaterial {
  return {
    id: 1,
    name: "Glass_iP15 pro",
    unit: "ea",
    category: IMPORTED_GOODS_CATEGORY,
    status: "unconfirmed",
    excel_label: "강화유리 iP15 Pro",
    excel_ref_price: "3103.00",
    match_rule: null,
    form_factor: null,
    part: null,
    note: null,
    lot_count: 0,
    price_count: 0,
    stale_count: 0,
    latest_price_ex_vat: null,
    latest_price_inc_vat: null,
    latest_price_inc_derived: false,
    latest_price_source: null,
    // 단가가 없으면 발효일도 없다 — 채택된 단가 행이 없기 때문이다(D-CPP-62 S2).
    latest_price_effective_date: null,
    price_rule: "latest",
    lot_price_min: null,
    lot_price_max: null,
    lot_price_has_span: false,
    price_conflict: false,
    price_conflict_price_id: null,
    prices: [],
    used_by: [],
    used_by_count: 0,
    ...over,
  };
}

/** 원장 `product` 라인(수입 완제품). `line_type: "material"`로 덮으면 부자재 라인이 된다. */
function productLine(over: Partial<CostLedgerMaterialLine> = {}): CostLedgerMaterialLine {
  return {
    line_id: 501,
    shipment_id: 30,
    hbl_no: "SETR2608170301",
    declaration_date: "2026-08-17",
    item_name: "Glass_iP15pro",
    line_type: "product",
    quantity: "1000.000",
    unit_cost_ex_vat: "2820.00",
    unit_cost_inc_vat: "3102.00",
    allocated_cost_krw: "3102000.00",
    linked_material_id: null,
    linked_material_name: null,
    linked_price_id: null,
    shipment_status: "confirmed",
    linked_price_check: null,
    suggestion: {
      line_id: 501,
      item_name: "Glass_iP15pro",
      // ★수입 완제품 종은 `match_rule`이 없어 제안이 원리적으로 안 붙는다(D-CPP-61) —
      //   그래서 픽스처 기본값도 null이다. 이게 IG-2 변이가 겨눈 자리다.
      material_id: null,
      reason: "규칙 없음 — 수입 완제품 종은 자동 매칭 대상이 아니다",
      candidates: [],
      ambiguous: false,
      unmatched: false,
    },
    ...over,
  };
}

function boardRow(over: Partial<CostBoardRow> = {}): CostBoardRow {
  return {
    internal_sku: "SKU-GLASS-15PRO",
    product_name: "오하이 강화유리 iP15 Pro",
    recipe_id: 201,
    recipe_product_name: "오하이 강화유리 iP15 Pro",
    form_factor: "bar",
    form_source: "rule",
    recipe_kind: "imported_goods",
    recipe_status: "approved",
    link_status: "linked",
    std_cost_ex_vat: "2820.00",
    std_cost_inc_vat: "3102.00",
    current_cost_price: "3200.00",
    gap_pct: -3.06,
    excel_total_inc_vat: "3103.00",
    excel_gap_pct: -0.03,
    reason: null,
    ...over,
  };
}

function board(items: CostBoardRow[]): CostBoard {
  return {
    items,
    sku_count: items.length,
    computed_count: items.filter((r) => r.std_cost_inc_vat !== null).length,
    uncomputed_count: items.filter((r) => r.std_cost_inc_vat === null).length,
    recipe_count: items.length,
    approved_recipe_count: items.length,
  };
}

/** 렌더된 표에서 본문 행(헤더 제외)만 — 각 행에 testid가 없어 role로 스코프한다. */
function bodyRows(): HTMLElement[] {
  return screen.getAllByRole("row").slice(1);
}

// ════════════════════════ ①표준원가 보드 ════════════════════════
describe("★합격 1 — 표준원가 보드에 원장 파생·엑셀 표준·격차가 나란히 뜬다 (IG-1)", () => {
  it("수입 완제품 행에서 표준원가·엑셀 표준·엑셀 대비가 한 행에 함께 보인다", () => {
    render(<StandardCostBoard board={board([boardRow()])} />);
    const [row] = bodyRows();
    // 표준원가(원장 파생) — 계산에 쓰이는 값
    expect(within(row).getByText("3,102원")).toBeTruthy();
    // 엑셀 표준(대조값) — formatCostWon 실제 출력 형식(쉼표+원)
    expect(within(row).getByTestId("board-excel-standard").textContent).toBe("3,103원");
    // 격차 — gapText 실제 출력 형식(부호+소수 2자리+%)
    expect(within(row).getByTestId("board-excel-gap").textContent).toBe("-0.03%");
  });

  it("「수입 완제품」 배지는 recipe_kind === imported_goods 행에만 뜬다 — 조립형 행엔 없다", () => {
    render(
      <StandardCostBoard
        board={board([
          boardRow({ recipe_id: 201, internal_sku: "SKU-IMPORTED" }),
          boardRow({
            recipe_id: 202,
            internal_sku: "SKU-ASSEMBLY",
            recipe_kind: "assembly",
            form_source: "rule",
          }),
        ])}
      />,
    );
    const [importedRow, assemblyRow] = bodyRows();
    expect(within(importedRow).getByTestId("board-imported-badge")).toBeTruthy();
    expect(within(assemblyRow).queryByTestId("board-imported-badge")).toBeNull();
  });

  it("「추정」 배지는 form_source === fallback 행에만 뜬다 — rule 행엔 없다", () => {
    render(
      <StandardCostBoard
        board={board([
          boardRow({ recipe_id: 201, internal_sku: "SKU-RULE", form_source: "rule" }),
          boardRow({ recipe_id: 202, internal_sku: "SKU-FALLBACK", form_source: "fallback" }),
        ])}
      />,
    );
    const [ruleRow, fallbackRow] = bodyRows();
    expect(within(ruleRow).queryByTestId("board-form-estimated")).toBeNull();
    expect(within(fallbackRow).getByTestId("board-form-estimated")).toBeTruthy();
  });

  it("★excel_total_inc_vat가 null이어도 행이 안 무너진다 — 빈 칸이지 0원이 아니다", () => {
    render(
      <StandardCostBoard
        board={board([boardRow({ excel_total_inc_vat: null, excel_gap_pct: null })])}
      />,
    );
    const [row] = bodyRows();
    expect(within(row).getByTestId("board-excel-standard").textContent).toBe("—");
    expect(within(row).getByTestId("board-excel-gap").textContent).toBe("—");
    // 「없음」의 자리에 「0원」이 앉지 않는다 — 엑셀 대조 칸 두 곳 다 확인
    expect(within(row).getByTestId("board-excel-standard").textContent).not.toBe("0원");
    // 표준원가(원장 파생) 자체는 여전히 그려진다 — 대조값이 없어도 계산값은 살아 있다
    expect(within(row).getByText("3,102원")).toBeTruthy();
  });
});

// ════════════════════════ ②부자재 탭 — 첫 연결 ════════════════════════
describe("★합격 2 — 수입 완제품 종에 원장 product 라인을 연결할 수 있다 (IG-2)", () => {
  it("linkTargetId가 없고 제안도 없으면 연결 버튼이 안 뜬다 — 종전 동작 보존", () => {
    const onLink = vi.fn();
    render(
      <LedgerMaterialLines rows={[productLine()]} materials={[importedMaterial()]} onLink={onLink} />,
    );
    const row = screen.getByTestId("ledger-line-501");
    expect(within(row).queryByRole("button")).toBeNull();
  });

  it("★linkTargetId를 주면 제안이 없어도 연결 버튼이 뜨고, 누르면 onLink(linkTargetId, line_id)로 불린다 — 이게 첫 연결의 유일한 길이다", () => {
    const onLink = vi.fn();
    render(
      <LedgerMaterialLines
        rows={[productLine()]}
        materials={[importedMaterial({ id: 1, name: "Glass_iP15 pro" })]}
        onLink={onLink}
        linkTargetId={1}
      />,
    );
    const row = screen.getByTestId("ledger-line-501");
    const btn = within(row).getByRole("button", { name: /Glass_iP15 pro.*연결/ });
    btn.click();
    expect(onLink).toHaveBeenCalledWith(1, 501);
  });

  it("이미 연결된 행엔 linkTargetId가 있어도 버튼이 안 뜬다 — 같은 로트 이중 계상 방지", () => {
    const onLink = vi.fn();
    render(
      <LedgerMaterialLines
        rows={[
          productLine({
            linked_material_id: 1,
            linked_material_name: "Glass_iP15 pro",
            linked_price_id: 90,
          }),
        ]}
        materials={[importedMaterial()]}
        onLink={onLink}
        linkTargetId={1}
      />,
    );
    const row = screen.getByTestId("ledger-line-501");
    expect(within(row).queryByRole("button")).toBeNull();
    expect(within(row).getByText(/연결됨 · Glass_iP15 pro/)).toBeTruthy();
  });
});

// ════════════════════════ 순수 헬퍼 ════════════════════════
describe("pickableProductLines — 이 종에 고를 수 있는 원장 완제품 라인만 남긴다", () => {
  it("product 라인 중 미연결이거나 이 종에 연결된 것만 남기고, 남의 종 것은 뺀다", () => {
    const rows: CostLedgerMaterialLine[] = [
      productLine({ line_id: 1, linked_material_id: null }), // 미연결 product → 포함
      productLine({ line_id: 2, linked_material_id: 1 }), // 이 종(1)에 연결됨 → 포함
      productLine({ line_id: 3, linked_material_id: 7 }), // 남의 종(7)에 연결됨 → 제외
      productLine({ line_id: 4, linked_material_id: null, line_type: "material" }), // 부자재 라인 → 제외
    ];
    const picked = pickableProductLines(rows, 1);
    expect(picked.map((r) => r.line_id).sort()).toEqual([1, 2]);
  });

  it("product 라인이 아예 없으면 빈 배열이다", () => {
    const rows: CostLedgerMaterialLine[] = [productLine({ line_id: 1, line_type: "material" })];
    expect(pickableProductLines(rows, 1)).toEqual([]);
  });
});

describe("isImportedGoodsMaterial — category가 정확히 일치할 때만 참", () => {
  it("category가 「수입 완제품」이면 참이다", () => {
    expect(isImportedGoodsMaterial(importedMaterial())).toBe(true);
  });

  it("category가 null이거나 다른 값이면 거짓이다", () => {
    expect(isImportedGoodsMaterial(importedMaterial({ category: null }))).toBe(false);
    expect(isImportedGoodsMaterial(importedMaterial({ category: "부자재" }))).toBe(false);
  });

  it("종 자체가 null이면 거짓이다", () => {
    expect(isImportedGoodsMaterial(null)).toBe(false);
  });
});
