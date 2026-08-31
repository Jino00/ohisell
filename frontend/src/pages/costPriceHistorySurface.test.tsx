// @vitest-environment jsdom
//
// costPriceHistorySurface.test.tsx — `cost_price` 이력·문 닫기가 **사람에게 닿는가**
// (계약 D-CPP-64 §4 S1-①·②)
//
// 백엔드 테스트가 「이력이 남나 · 거부되나」를 재고, 이 파일은 「그게 화면 픽셀이 되나」를 잰다.
// 이 저장소가 반복해 밟은 병이 정확히 그 사이에 산다 — 값은 만들어지는데 렌더가 없어 사람은
// 못 본다(렌더 삭제가 단위 테스트를 전부 통과한 채 살아남는다).
//
// 재는 것:
//  P1 이력 행이 «시각·SKU·old→new·경로·근거» 다섯을 다 보여 준다 (하나라도 빠지면 못 되짚는다)
//  P2 0건이 **왜** 비었는지 말한다 — 빈 표는 「이상 없음」으로 읽힌다
//  P3 신규(old_value=null)가 「0」이 아니라 「없음」으로 뜬다 (「없음 ≠ 0」)
//  P4 근거가 없으면 「근거 없음」이라고 **말한다** — 빈칸으로 두지 않는다
//  P5 닫힌 문으로 들어온 행에 경고가 붙는다 — 닫았다는 선언을 화면이 확인한다
//  P6 상품 폼의 원가 칸이 읽기 전용이고, 사유 문장과 «갈 길»이 함께 뜬다
//  P7 상품 폼이 `cost_price`를 **키 자체로** 안 보낸다 (보내면 백엔드가 400을 준다)
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { CostPriceHistoryPanel } from "./CostPage";
import ProductForm from "../components/ProductForm";
import { COST_PRICE_REJECTION_SENTENCE } from "../lib/costPriceGate";
import type { CostPriceHistoryList } from "../lib/api";

afterEach(() => cleanup());

const LIST = (over: Partial<CostPriceHistoryList> = {}): CostPriceHistoryList => ({
  items: [
    {
      id: 1,
      internal_sku: "OHI-0001",
      product_id: 11,
      old_value: "2350.70",
      new_value: "2500.00",
      path: "excel_upload",
      actor: "excel",
      reason: "엑셀 업로드 「t.xlsx」 「상품 원가표」 시트 행 2",
      created_at: "2026-08-31T12:00:00",
    },
  ],
  total: 1,
  started_at: "2026-08-31T12:00:00",
  empty_reason: null,
  ...over,
});

describe("CostPriceHistoryPanel — 이력이 화면에 닿는가", () => {
  it("P1 한 행에 시각·SKU·old→new·경로·근거가 **다 있다**", () => {
    render(<CostPriceHistoryPanel data={LIST()} />);
    const row = screen.getByTestId("cost-price-history-row-1");
    expect(within(row).getByText("OHI-0001")).toBeTruthy();
    expect(row.textContent).toContain("2350.70");
    expect(row.textContent).toContain("2500.00");
    // 경로는 «사람이 읽는 이름»으로 — 코드값(`excel_upload`)만 보이면 아무 뜻이 없다.
    expect(row.textContent).toContain("상품 원가표 엑셀 업로드");
    expect(row.textContent).toContain("t.xlsx");
    // 시각은 KST로 — UTC 저장을 그대로 뿌리면 9시간 틀린 시각을 사람이 읽는다.
    expect(row.textContent).toContain("2026-08-31");
  });

  it("P2 0건이면 **왜** 비었는지 말한다 — 빈 표는 「이상 없음」으로 읽힌다", () => {
    render(
      <CostPriceHistoryPanel
        data={LIST({
          items: [],
          total: 0,
          started_at: null,
          empty_reason: "이력이 아직 한 건도 없다 — 소급 불가.",
        })}
      />,
    );
    expect(screen.getByTestId("cost-price-history-empty").textContent).toContain(
      "소급 불가",
    );
    // 표 자체가 없어야 한다 — 헤더만 있는 빈 표는 「0건」을 «정상»처럼 보이게 한다.
    expect(screen.queryByTestId("cost-price-history-table")).toBeNull();
  });

  it("P3 신규는 「없음」이지 「0」이 아니다 (「없음 ≠ 0」)", () => {
    render(
      <CostPriceHistoryPanel
        data={LIST({
          items: [{ ...LIST().items[0], id: 2, old_value: null }],
        })}
      />,
    );
    const row = screen.getByTestId("cost-price-history-row-2");
    expect(row.textContent).toContain("없음(신규)");
    // 「0 → 2500」으로 보이면 «없던 사실»(0원이었다가 올랐다)이 이력에 생긴다.
    expect(row.textContent).not.toContain("0 → 2500");
  });

  it("P4 근거가 없으면 「근거 없음」이라고 **말한다** — 빈칸으로 두지 않는다", () => {
    render(
      <CostPriceHistoryPanel
        data={LIST({ items: [{ ...LIST().items[0], id: 3, reason: null }] })}
      />,
    );
    expect(screen.getByTestId("cost-price-history-row-3").textContent).toContain(
      "근거 없음",
    );
  });

  it("P5 ★닫힌 문으로 들어온 행엔 경고가 붙는다 — 「닫았다」는 선언을 화면이 확인한다", () => {
    render(
      <CostPriceHistoryPanel
        data={LIST({
          items: [{ ...LIST().items[0], id: 4, path: "product_update" }],
        })}
      />,
    );
    expect(screen.getByTestId("cost-price-history-closed-door-4")).toBeTruthy();
    // 열린 문(업로드)엔 경고가 없다 — 상시 경고는 안 켜진 것과 같다.
    cleanup();
    render(<CostPriceHistoryPanel data={LIST()} />);
    expect(screen.queryByTestId("cost-price-history-closed-door-1")).toBeNull();
  });

  it("★null(아직 안 부름)과 0건(불렀는데 없음)을 다르게 말한다", () => {
    render(<CostPriceHistoryPanel data={null} />);
    expect(screen.getByTestId("cost-price-history-loading")).toBeTruthy();
    expect(screen.queryByTestId("cost-price-history-empty")).toBeNull();
  });
});

describe("ProductForm — 안 잠긴 문이 닫혔다는 것이 화면에 보이는가", () => {
  const INITIAL = {
    internal_sku: "OHI-0001",
    product_name: "지문방지 필름 3매",
    cost_price: 2350.7,
    category: "필름",
    memo: "",
  };

  it("P6 원가 칸이 읽기 전용이고 사유 문장 + 갈 길이 함께 뜬다", () => {
    render(
      <MemoryRouter>
        <ProductForm title="상품 수정" initial={INITIAL} onSubmit={vi.fn()} onCancel={vi.fn()} />
      </MemoryRouter>,
    );
    const block = screen.getByTestId("product-form-cost-locked");
    // 입력 칸이 아예 없어야 한다 — 있으면 「쳤는데 저장이 안 된다」가 된다.
    expect(within(block).queryByRole("spinbutton")).toBeNull();
    // 지금 값은 보여 준다 — 칸을 지우면 닫는 게 아니라 숨기는 것이다.
    expect(block.textContent).toContain("2,350.7");
    expect(block.textContent).toContain(COST_PRICE_REJECTION_SENTENCE);
    // 문장만 주면 길을 모른다 — 원가 메뉴로 가는 링크가 있어야 한다.
    expect(within(block).getByRole("link").getAttribute("href")).toBe("/cost");
  });

  it("P7 ★저장 payload에 `cost_price` **키 자체가 없다**", () => {
    const onSubmit = vi.fn();
    render(
      <MemoryRouter>
        <ProductForm title="상품 수정" initial={INITIAL} onSubmit={onSubmit} onCancel={vi.fn()} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText("저장"));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const payload = onSubmit.mock.calls[0][0];
    // ★`undefined`로라도 실으면 백엔드 가드(`model_fields_set`)가 「보냈다」로 읽어
    //   상품명만 고치는 정상 수정까지 400이 된다. 그래서 **키 유무**를 단언한다.
    expect(Object.prototype.hasOwnProperty.call(payload, "cost_price")).toBe(false);
    expect(payload.product_name).toBe("지문방지 필름 3매");
  });
});
