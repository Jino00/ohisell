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

  it("★모르는 경로도 **이름 그대로** 보여 준다 — 「기타」로 접으면 새 문이 생긴 걸 못 본다", () => {
    // 적대 리뷰 P2-10: `costPricePathLabel`의 폴백을 「기타」로 바꿔도 아무도 안 죽었다.
    // 이 표의 존재 이유가 「어느 문으로 들어왔나」인데, 모르는 문을 접으면 그 질문이 사라진다.
    render(
      <CostPriceHistoryPanel
        data={LIST({ items: [{ ...LIST().items[0], id: 9, path: "some_new_door" }] })}
      />,
    );
    const row = screen.getByTestId("cost-price-history-row-9");
    expect(row.textContent).toContain("some_new_door");
    expect(row.textContent).toContain("알 수 없는 경로");
  });

  it("★null(아직 안 부름)과 0건(불렀는데 없음)을 다르게 말한다", () => {
    render(<CostPriceHistoryPanel data={null} />);
    expect(screen.getByTestId("cost-price-history-loading")).toBeTruthy();
    expect(screen.queryByTestId("cost-price-history-empty")).toBeNull();
  });
});

describe("ProductForm — 안 잠긴 문이 닫혔다는 것이 화면에 보이는가", () => {
  // ★픽스처가 **prod와 같아야** 한다 (적대 리뷰 P2-3, 2026-08-31): `ProductOut.cost_price`가
  //   `Decimal`이라 라이브 JSON은 **문자열** `"2350.70"`을 준다. `api.ts`의
  //   `Product.cost_price: number`는 타입 거짓말이고, number 픽스처로 재면 **사용자가 못 보는
  //   것을 증명**하게 된다(문자열에 `.toLocaleString()`을 부르면 천단위 구분 없이 원문이 뜬다).
  const INITIAL = {
    internal_sku: "OHI-0001",
    product_name: "지문방지 필름 3매",
    cost_price: "2350.70",
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
    // 라이브가 주는 문자열이 **사람이 읽는 모양**으로 렌더된다.
    expect(block.textContent).toContain("2,350.7");
    expect(block.textContent).toContain(COST_PRICE_REJECTION_SENTENCE);
    // 문장만 주면 길을 모른다 — 원가 메뉴로 가는 링크가 있어야 한다.
    expect(within(block).getByRole("link").getAttribute("href")).toBe("/cost");
  });

  it("★거부 사유가 실제로 오면 **모달 안에** 그대로 뜬다 — 조용히 닫히지 않는다", () => {
    // 적대 리뷰 P2-2: 종전엔 `fetchApi` 예외를 아무도 안 잡아 400이 나도 화면이 침묵했다.
    // 모달은 `fixed inset-0` 오버레이라 **페이지 본문에 띄우면 덮개 뒤에 숨는다.**
    render(
      <MemoryRouter>
        <ProductForm
          title="상품 수정"
          initial={INITIAL}
          onSubmit={vi.fn()}
          onCancel={vi.fn()}
          error={`${COST_PRICE_REJECTION_SENTENCE} · 상품 수정에서는 원가를 바꿀 수 없다`}
        />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("product-form-error").textContent).toContain(
      COST_PRICE_REJECTION_SENTENCE,
    );
  });

  it("사유가 없으면 빨간 칸을 그리지 않는다 — 상시 켜진 경고는 안 켜진 것과 같다", () => {
    render(
      <MemoryRouter>
        <ProductForm title="상품 수정" initial={INITIAL} onSubmit={vi.fn()} onCancel={vi.fn()} />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("product-form-error")).toBeNull();
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
