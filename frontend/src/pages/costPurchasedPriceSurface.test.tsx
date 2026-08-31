// @vitest-environment jsdom
//
// costPurchasedPriceSurface.test.tsx — 매입품 단가 축이 **사람에게 닿는가** (D-CPP-63 S1 3/3)
//
// 서비스·HTTP 테스트가 「판정이 옳나」를 재고, 이 파일은 「그 판정이 화면 픽셀이 되나」를 잰다.
// 이 저장소가 반복해 밟은 병이 정확히 그 사이에 산다 — 값은 만들어지는데 렌더가 없어서
// 사람은 못 본다(호출부·렌더 삭제가 단위 테스트를 전부 통과한 채 살아남는다).
//
// 재는 것:
//  P1 탭이 실제로 있고, 누르면 패널이 뜬다 (탭바에서 빠지면 이 축은 도달 불가다)
//  P2 보드 숫자가 payload «그대로»다 — 특히 「보류」와 「미확인」이 **따로** 보인다
//  P3 묶음의 얼굴이 «레시피명»이다 — 사람이 필름/매입품을 그것으로 가른다
//  P4 「대상 아님」이 사유와 «함께» 렌더된다 (개수만 맞고 사유가 없으면 판단 불가)
//  P5 공백(1원)은 「공백」으로 뜨고 숫자 0으로 보이지 않는다
//  P6 확인 클릭이 그 묶음의 SKU와 단가를 «그대로» 보낸다
//  P7 서버가 일부를 거부하면 그 사실이 화면에 뜬다 (조용한 성공 금지)
//  P8 400 사유(「원가 열이 없다」)가 화면에 그대로 뜬다
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";

import type { PurchasedPreview } from "../lib/api";

const PREVIEW: PurchasedPreview = {
  source_file: "ohisell_mapping_template_20260807.xlsx",
  read_columns: { name: "상품명", price: "원가" },
  counts: {
    groups: 2,
    target_skus: 3,
    blank_skus: 1,
    excluded_skus: 1,
    unmatched_rows: 0,
  },
  groups: [
    {
      recipe_id: 84,
      recipe_name: "오하이 일미리 케이스 1mm 슬림 변색 없는 케이스",
      price: "922.00",
      sku_count: 2,
      already_approved: 0,
      skus: [
        {
          internal_sku: "C1",
          product_name: "일미리 케이스, 아이폰15",
          source_product_name: "일미리 케이스, 아이폰15",
          file_price: "922.00",
          is_placeholder: false,
          current_cost_price: "1000.00",
          diff: "-78.00",
          recipe_id: 84,
          recipe_name: "오하이 일미리 케이스 1mm 슬림 변색 없는 케이스",
          excluded_reason: null,
          approved_price: null,
        },
        {
          internal_sku: "C2",
          product_name: "일미리 케이스, 아이폰16",
          source_product_name: "일미리 케이스, 아이폰16",
          file_price: "922.00",
          is_placeholder: false,
          current_cost_price: "1000.00",
          diff: "-78.00",
          recipe_id: 84,
          recipe_name: "오하이 일미리 케이스 1mm 슬림 변색 없는 케이스",
          excluded_reason: null,
          approved_price: null,
        },
      ],
    },
    {
      // ★조립품 «초안»(구성 0줄)이라 시스템이 못 가른다 — 사람이 이름으로 가른다
      recipe_id: 99,
      recipe_name: "종이질감 저반사 지문방지 블루라이트 차단 액정보호필름 2매",
      price: "4352.70",
      sku_count: 1,
      already_approved: 0,
      skus: [
        {
          internal_sku: "T1",
          product_name: "종이질감 필름, 갤럭시탭S10",
          source_product_name: "종이질감 필름, 갤럭시탭S10",
          file_price: "4352.70",
          is_placeholder: false,
          current_cost_price: "4254.00",
          diff: "98.70",
          recipe_id: 99,
          recipe_name: "종이질감 저반사 지문방지 블루라이트 차단 액정보호필름 2매",
          excluded_reason: null,
          approved_price: null,
        },
      ],
    },
  ],
  blanks: [
    {
      internal_sku: "B1",
      product_name: "시스루 케이스, 아이폰13",
      source_product_name: "시스루 케이스 블랙, 아이폰13",
      file_price: null,
      is_placeholder: true,
      current_cost_price: "3000.00",
      diff: null,
      recipe_id: 80,
      recipe_name: "오하이 아이폰 매트 시스루 케이스 블랙",
      excluded_reason: null,
      approved_price: null,
    },
  ],
  excluded: [
    {
      internal_sku: "F1",
      product_name: "유리코팅 필름, 아이폰16",
      source_product_name: "유리코팅 필름, 아이폰16",
      file_price: "2616.00",
      is_placeholder: false,
      current_cost_price: "2616.00",
      diff: "0.00",
      recipe_id: 83,
      recipe_name: "오하이 유리코팅 고화질 액정보호필름 2매입",
      excluded_reason:
        "조립품 — 구성이 있는 레시피다(우리 계산이 정본, 파일 값 금지)",
      approved_price: null,
    },
  ],
  unmatched: [],
  anomalies: [],
};

const BOARD = {
  candidates: 473,
  grounded: 12,
  held_blank: 34,
  unconfirmed: 427,
};

interface ConfirmBody {
  internal_skus: string[];
  price: string;
  source_file: string;
  source_names?: Record<string, string>;
  note?: string;
}

// ★스파이의 «인자 타입»을 살려 둔다 — `(...a: unknown[])`로 뭉개면 `mock.calls[0][0]`이
//   빈 튜플이 되어 P6(무엇을 보냈나)이 타입 수준에서 검사 불가가 된다.
const previewSpy = vi.fn(async (_file: File) => PREVIEW);
const confirmSpy = vi.fn(async (_body: ConfirmBody) => ({
  written: 2,
  skipped: [] as { internal_sku: string; reason: string }[],
  board: { ...BOARD, grounded: 14, unconfirmed: 425 },
}));
const boardSpy = vi.fn(async () => BOARD);

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    // ★화살표로 «감싸서» 넘긴다 — `vi.mock` 팩토리는 파일 맨 위로 끌어올려지므로 스파이를
    //   직접 참조하면 초기화 전에 읽혀 ReferenceError가 난다. 감싸면 호출 시점에 읽힌다.
    //   (인자 타입은 스파이 선언에 살아 있어 `mock.calls[0][0]`이 여전히 타입을 갖는다.)
    previewPurchasedPrices: (file: File) => previewSpy(file),
    confirmPurchasedPrices: (body: ConfirmBody) => confirmSpy(body),
    fetchPurchasedBoard: () => boardSpy(),
  };
});

import CostPurchasedPricePanel from "./costPurchasedPricePanel";

function xlsxFile() {
  return new File(["x"], "ohisell_mapping_template_20260807.xlsx", {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
}

async function uploadAndWait() {
  render(<CostPurchasedPricePanel />);
  await screen.findByTestId("purchased-board");
  const input = screen.getByTestId("purchased-file") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [xlsxFile()] } });
  await screen.findAllByTestId("purchased-group");
}

beforeEach(() => {
  previewSpy.mockClear();
  confirmSpy.mockClear();
  boardSpy.mockClear();
});
afterEach(cleanup);

describe("매입품 단가 화면", () => {
  it("P2 보드는 「보류」와 「미확인」을 따로 보여준다", async () => {
    render(<CostPurchasedPricePanel />);
    const board = await screen.findByTestId("purchased-board");

    // 접히면 안 되는 두 숫자 — 「값이 없다고 확인함」과 「아직 안 봄」은 다른 사실이다
    expect(within(board).getByText("34")).toBeTruthy();
    expect(within(board).getByText("427")).toBeTruthy();
    expect(within(board).getByText("12")).toBeTruthy();
    expect(within(board).getByText("473")).toBeTruthy();
  });

  it("P3 묶음의 얼굴은 레시피명이다 — 사람이 그것으로 필름을 가른다", async () => {
    await uploadAndWait();
    const groups = screen.getAllByTestId("purchased-group");
    expect(groups).toHaveLength(2);

    // 필름 초안도 묶음으로 «선다»(시스템이 못 가르므로) — 이름이 보여야 사람이 가른다
    expect(
      screen.getByText(/종이질감 저반사 지문방지 블루라이트 차단 액정보호필름 2매/),
    ).toBeTruthy();
    expect(
      screen.getByText(/오하이 일미리 케이스 1mm 슬림 변색 없는 케이스/),
    ).toBeTruthy();
    // 「필름이 섞여 있다」는 경고가 화면에 실제로 있다
    expect(screen.getByText(/조립품 초안도 섞여 있다/)).toBeTruthy();
  });

  it("P4 「대상 아님」은 사유와 함께 렌더된다", async () => {
    await uploadAndWait();
    const band = screen.getByTestId("purchased-excluded");
    expect(within(band).getByText(/조립품 — 구성이 있는 레시피다/)).toBeTruthy();
    expect(within(band).getByText("F1")).toBeTruthy();
  });

  it("P5 공백은 「공백」으로 뜨고 0원으로 보이지 않는다", async () => {
    await uploadAndWait();
    const band = screen.getByTestId("purchased-blanks");
    expect(within(band).getByText("공백")).toBeTruthy();
    expect(within(band).queryByText("0")).toBeNull();
  });

  it("P6 확인은 그 묶음의 SKU와 단가를 그대로 보낸다", async () => {
    await uploadAndWait();
    fireEvent.click(screen.getAllByTestId("purchased-confirm")[0]);

    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1));
    const arg = confirmSpy.mock.calls[0][0];
    expect(arg.internal_skus).toEqual(["C1", "C2"]);
    expect(arg.price).toBe("922.00");
    expect(arg.source_file).toBe("ohisell_mapping_template_20260807.xlsx");
    // 근거(파일의 상품명)가 함께 간다 — 없으면 나중에 매칭을 재현 못 한다
    expect(arg.source_names?.C1).toBe("일미리 케이스, 아이폰15");
  });

  it("P6b 확인 뒤 보드 숫자가 응답값으로 갱신된다", async () => {
    await uploadAndWait();
    fireEvent.click(screen.getAllByTestId("purchased-confirm")[0]);
    await waitFor(() =>
      expect(within(screen.getByTestId("purchased-board")).getByText("14")).toBeTruthy(),
    );
  });

  it("P7 서버가 거부한 건이 화면에 뜬다 — 조용한 성공 금지", async () => {
    confirmSpy.mockResolvedValueOnce({
      written: 1,
      skipped: [
        {
          internal_sku: "T1",
          reason: "조립품 — 구성이 있는 레시피다(우리 계산이 정본, 파일 값 금지)",
        },
      ],
      board: BOARD,
    });
    await uploadAndWait();
    fireEvent.click(screen.getAllByTestId("purchased-confirm")[0]);

    const msg = await screen.findByTestId("purchased-msg");
    expect(msg.textContent).toMatch(/1건은 대상이 아니라 쓰지 않았다/);
    expect(msg.textContent).toMatch(/T1/);
  });

  it("P8 400 사유가 화면에 그대로 뜬다 (원가 열 없는 판)", async () => {
    previewSpy.mockRejectedValueOnce(
      new Error("「원가」 열이 이 판에는 없다 — 08-22판처럼…"),
    );
    render(<CostPurchasedPricePanel />);
    await screen.findByTestId("purchased-board");
    fireEvent.change(screen.getByTestId("purchased-file"), {
      target: { files: [xlsxFile()] },
    });

    const err = await screen.findByTestId("purchased-error");
    expect(err.textContent).toMatch(/「원가」 열이 이 판에는 없다/);
    expect(screen.queryAllByTestId("purchased-group")).toHaveLength(0);
  });

  it("P9 읽은 «열 이름»이 화면에 뜬다 — 위치로 읽지 않았음의 표면", async () => {
    await uploadAndWait();
    expect(screen.getByText(/「상품명」·「\s*원가」 열을 읽었다/)).toBeTruthy();
  });
});

// ── 적대 리뷰 1R 회귀 (PR #595) ──────────────────────────────────────────────

describe("적대 리뷰가 살려 보낸 자리", () => {
  it("P1-2 서버가 전건 거부하면 배지가 「이미 근거 있음」으로 초록이 되면 안 된다", async () => {
    confirmSpy.mockResolvedValueOnce({
      written: 0,
      skipped: [
        { internal_sku: "C1", reason: "조립품 — 구성이 있는 레시피다" },
        { internal_sku: "C2", reason: "조립품 — 구성이 있는 레시피다" },
      ],
      board: BOARD,
    });
    await uploadAndWait();
    fireEvent.click(screen.getAllByTestId("purchased-confirm")[0]);

    await screen.findByTestId("purchased-msg");
    // 메시지는 거부를 말하는데 배지가 반대를 말하던 자리
    expect(screen.queryByText(/이미 근거 있음/)).toBeNull();
  });

  it("P1-2b 일부만 거부되면 배지는 «실제로 써진 수»만 말한다", async () => {
    confirmSpy.mockResolvedValueOnce({
      written: 1,
      skipped: [{ internal_sku: "C2", reason: "조립품 — 구성이 있는 레시피다" }],
      board: BOARD,
    });
    await uploadAndWait();
    fireEvent.click(screen.getAllByTestId("purchased-confirm")[0]);

    expect(await screen.findByText(/이미 근거 있음 1건/)).toBeTruthy();
  });

  it("P2-2 묶음을 펼치면 SKU별 제 값과 «차이»가 실제로 렌더된다", async () => {
    // ★계약 §4 S1 둘째·셋째 항목의 표면. 초판 테스트는 묶음을 한 번도 «펼치지» 않아
    //   SkuTable 통째 제거·diff 열 제거 변이가 전부 살아남았다(M20·M22·M23 SURVIVED).
    await uploadAndWait();
    fireEvent.click(screen.getAllByText(/SKU 2건 보기/)[0]);

    const table = await screen.findAllByTestId("purchased-sku-table");
    const t = within(table[0]);
    expect(t.getByText("C1")).toBeTruthy();
    expect(t.getByText("C2")).toBeTruthy();
    // SKU별 제 값 — 파일 단가와 현재 원가가 «나란히»
    expect(t.getAllByText("922").length).toBeGreaterThan(0);
    expect(t.getAllByText("1,000").length).toBeGreaterThan(0);
    // 차이가 부호와 함께 선다
    expect(t.getAllByText("-78").length).toBeGreaterThan(0);
  });
});
