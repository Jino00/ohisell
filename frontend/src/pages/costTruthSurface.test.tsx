// @vitest-environment jsdom
//
// costTruthSurface.test.tsx — 정본 판별이 **화면 픽셀이 되는가** (계약 D-CPP-64 §4 S2)
//
// ## 이 파일이 지키는 것
//
//   S2-① 963 전 SKU가 한 표에 — 유형·정본값·현재값·격차 네 칸이 **한 행에** 그려진다
//   S2-② 보류는 **사유와 소관을 문장으로** 말한다 — 빈 칸도 0도 아니다
//   S2-③ 정본 없음이 **소관별로** 갈라지고, 승인된 매입가는 「매입가」로 서 있다
//
// ★**「없음 ≠ 0」이 이 파일의 중심이다.** 보류·정본 없음의 정본값 칸에 0원이 그려지면
//   미판정이 확정값으로 둔갑하고, 그건 이 계약이 없애려는 병 그 자체다.
// ★배선(탭 클릭 → 패널 도달)은 여기서 못 잰다 — 순수 컴포넌트를 props로 렌더하기 때문이다.
//   그건 `costPageReachesTheUser.test.tsx`가 `App`을 통째로 렌더해서 잰다(호출부 절단 변이).
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

import { CostTruthBoardPanel } from "./CostPage";
import type { CostTruthBoard, CostTruthRow } from "../lib/api";

afterEach(() => cleanup());

const ROW = (over: Partial<CostTruthRow> = {}): CostTruthRow => ({
  internal_sku: "OHI-0001",
  product_name: "지문방지 필름 3매",
  truth_type: "computed",
  truth_label: "계산값",
  truth_value: "2649.7",
  current_cost_price: "2350.7",
  gap: "299.0",
  cause: "g2_parts_299",
  cause_ref118: "G2",
  reason: "엑셀이 부자재 4종을 안 세고 있다 — 격차가 정확히 +299.0원. 계산이 정본",
  owner: "계약 D-CPP-64 S3 — 컷오버",
  recipe_id: 68,
  recipe_product_name: "매트 필름 3매",
  form_factor: "bar",
  recipe_kind: "assembly",
  computed_value: "2649.7",
  ...over,
});

const HELD = ROW({
  internal_sku: "OHI-G1",
  truth_type: "held",
  truth_label: "보류",
  truth_value: null,
  gap: null,
  cause: "g1_grain_mismatch",
  cause_ref118: "G1",
  reason: "그레인 불일치 — 레시피는 계산값 1개인데 이 묶음의 SKU가 현재 원가를 5종 갖고 있다",
  owner: "트랙 A2 — 그레인 정의",
});

const NONE = ROW({
  internal_sku: "OHI-DUPE",
  product_name: "[중복] 필름",
  truth_type: "none",
  truth_label: "정본 없음",
  truth_value: null,
  current_cost_price: "1.0",
  gap: null,
  cause: "no_link_dupe",
  cause_ref118: null,
  reason: "상품명에 「[중복]」 표지가 있다 — 정리 대상이지 원가를 세울 대상이 아니다",
  owner: "소관 없음",
});

const PURCHASED = ROW({
  internal_sku: "OHI-0887",
  product_name: "매입 완제품",
  truth_type: "purchased",
  truth_label: "매입가",
  truth_value: "47000",
  current_cost_price: "45000",
  gap: "2000",
  cause: "purchased_approved",
  cause_ref118: null,
  reason: "승인된 매입가가 있다 — 매입품의 정본은 매입가다(계산값은 원리적으로 없다)",
  owner: "계약 D-CPP-64 S3 — 컷오버",
});

const BOARD = (over: Partial<CostTruthBoard> = {}): CostTruthBoard => ({
  items: [ROW(), HELD, NONE, PURCHASED],
  sku_count: 4,
  price_rule: "latest",
  census: {
    by_truth_type: { computed: 1, purchased: 1, held: 1, none: 1 },
    by_cause: { g2_parts_299: 1, g1_grain_mismatch: 1, no_link_dupe: 1, purchased_approved: 1 },
    cause_ref118: { g2_parts_299: "G2", g1_grain_mismatch: "G1" },
    cutover_ready_count: 2,
    cutover_gap_sum: "2299.0",
    matched_count: 0,
    held_count: 1,
    none_count: 1,
  },
  caveats: ["이 표는 읽기 전용이다 — cost_price를 한 건도 바꾸지 않는다."],
  ...over,
});

// ═══════════════════════════════════════════════════════════════════
// S2-① 네 칸이 한 행에
// ═══════════════════════════════════════════════════════════════════

describe("CostTruthBoardPanel — 정본이 화면에 닿는가", () => {
  it("S2-① 한 행에 정본 유형·정본값·현재값·격차가 함께 그려진다", () => {
    render(<CostTruthBoardPanel data={BOARD()} />);
    const row = screen.getByTestId("cost-truth-row-OHI-0001");
    const cells = within(row);
    expect(cells.getByTestId("cost-truth-type-OHI-0001").textContent).toContain("계산값");
    expect(cells.getByTestId("cost-truth-value-OHI-0001").textContent).toContain("2,649.7원");
    expect(cells.getByTestId("cost-truth-gap-OHI-0001").textContent).toContain("299원");
    expect(row.textContent).toContain("2,350.7원");
  });

  it("S2-① 상단 집계가 네 유형 + 즉시 가능 + 일치를 말한다", () => {
    render(<CostTruthBoardPanel data={BOARD()} />);
    expect(screen.getByTestId("cost-truth-census-computed").textContent).toContain("계산값 1");
    expect(screen.getByTestId("cost-truth-census-purchased").textContent).toContain("매입가 1");
    expect(screen.getByTestId("cost-truth-census-held").textContent).toContain("보류 1");
    expect(screen.getByTestId("cost-truth-census-none").textContent).toContain("정본 없음 1");
    const ready = screen.getByTestId("cost-truth-census-ready").textContent ?? "";
    expect(ready).toContain("2건");
    expect(ready).toContain("2,299원");
  });

  it("ref 118의 원인 이름표(G1·G2)가 행에 보인다 — 그래야 그 표와 대조된다", () => {
    render(<CostTruthBoardPanel data={BOARD()} />);
    expect(screen.getByTestId("cost-truth-type-OHI-0001").textContent).toContain("G2");
    expect(screen.getByTestId("cost-truth-type-OHI-G1").textContent).toContain("G1");
  });

  // ═════════════════════════════════════════════════════════════════
  // ★없음 ≠ 0
  // ═════════════════════════════════════════════════════════════════

  it("★보류의 정본값·격차 칸은 「—」다 — 0원을 그리면 미판정이 확정값으로 둔갑한다", () => {
    render(<CostTruthBoardPanel data={BOARD()} />);
    expect(screen.getByTestId("cost-truth-value-OHI-G1").textContent).toBe("—");
    expect(screen.getByTestId("cost-truth-gap-OHI-G1").textContent).toBe("—");
    expect(screen.getByTestId("cost-truth-value-OHI-DUPE").textContent).toBe("—");
    expect(screen.getByTestId("cost-truth-gap-OHI-DUPE").textContent).toBe("—");
  });

  it("정본 없음 행이라도 «현재 원가»는 그대로 보인다 — 그 값으로 손익이 계산되고 있으니까", () => {
    render(<CostTruthBoardPanel data={BOARD()} />);
    expect(screen.getByTestId("cost-truth-row-OHI-DUPE").textContent).toContain("1원");
  });

  // ═════════════════════════════════════════════════════════════════
  // S2-② 사유와 소관
  // ═════════════════════════════════════════════════════════════════

  it("S2-② 보류 행이 사유를 «문장으로» 말한다", () => {
    render(<CostTruthBoardPanel data={BOARD()} />);
    expect(screen.getByTestId("cost-truth-reason-OHI-G1").textContent).toContain("그레인 불일치");
    expect(screen.getByTestId("cost-truth-reason-OHI-G1").textContent).toContain("5종");
  });

  it("S2-② 소관 칸이 «어디가 이걸 움직이나»를 말한다 — 「소관 없음」도 유효한 답이다", () => {
    render(<CostTruthBoardPanel data={BOARD()} />);
    expect(screen.getByTestId("cost-truth-owner-OHI-G1").textContent).toContain("트랙 A2");
    expect(screen.getByTestId("cost-truth-owner-OHI-DUPE").textContent).toContain("소관 없음");
  });

  it("★모든 행의 사유·소관이 비어 있지 않다 — 한 칸이라도 비면 그 행은 화면에서 침묵한다", () => {
    render(<CostTruthBoardPanel data={BOARD()} />);
    for (const sku of ["OHI-0001", "OHI-G1", "OHI-DUPE", "OHI-0887"]) {
      expect(screen.getByTestId(`cost-truth-reason-${sku}`).textContent?.trim()).toBeTruthy();
      expect(screen.getByTestId(`cost-truth-owner-${sku}`).textContent?.trim()).toBeTruthy();
    }
  });

  // ═════════════════════════════════════════════════════════════════
  // S2-③ 매입가 승격
  // ═════════════════════════════════════════════════════════════════

  it("S2-③ 승인된 매입가를 가진 SKU가 「매입가」로 서고 격차가 보인다", () => {
    render(<CostTruthBoardPanel data={BOARD()} />);
    expect(screen.getByTestId("cost-truth-type-OHI-0887").textContent).toContain("매입가");
    expect(screen.getByTestId("cost-truth-value-OHI-0887").textContent).toContain("47,000원");
    expect(screen.getByTestId("cost-truth-gap-OHI-0887").textContent).toContain("2,000원");
  });

  // ═════════════════════════════════════════════════════════════════
  // 침묵하지 않는다 — 로딩·0건·형식 파손·잘림
  // ═════════════════════════════════════════════════════════════════

  it("아직 안 불렀을 때와 0건일 때가 «다른 얼굴»이다", () => {
    const { unmount } = render(<CostTruthBoardPanel data={null} />);
    expect(screen.getByTestId("cost-truth-loading")).toBeTruthy();
    expect(screen.queryByTestId("cost-truth-empty")).toBeNull();
    unmount();

    render(<CostTruthBoardPanel data={BOARD({ items: [], sku_count: 0 })} />);
    expect(screen.getByTestId("cost-truth-empty")).toBeTruthy();
    expect(screen.queryByTestId("cost-truth-loading")).toBeNull();
  });

  it("★목록이 배열이 아니면 «0건»이 아니라 «형식이 다르다»고 말한다(교훈 #123)", () => {
    const broken = { ...BOARD(), items: undefined } as unknown as CostTruthBoard;
    render(<CostTruthBoardPanel data={broken} />);
    expect(screen.getByTestId("cost-truth-broken")).toBeTruthy();
    expect(screen.queryByTestId("cost-truth-empty")).toBeNull();
  });

  it("★자백 문구를 화면이 띄운다 — 집계가 조용히 완전해 보이면 안 된다", () => {
    render(<CostTruthBoardPanel data={BOARD()} />);
    expect(screen.getByTestId("cost-truth-caveats").textContent).toContain("읽기 전용");
  });

  it("★잘라 그릴 땐 몇 건을 안 그렸는지 말한다 — 조용한 절단이 「전부 봤다」로 읽힌다", () => {
    const many = Array.from({ length: 5 }, (_, i) =>
      ROW({ internal_sku: `OHI-${1000 + i}` }),
    );
    render(<CostTruthBoardPanel data={BOARD({ items: many, sku_count: 5 })} limit={2} />);
    expect(screen.getByTestId("cost-truth-row-OHI-1000")).toBeTruthy();
    expect(screen.queryByTestId("cost-truth-row-OHI-1004")).toBeNull();
    const note = screen.getByTestId("cost-truth-truncated").textContent ?? "";
    expect(note).toContain("5건 중 2건");
    expect(note).toContain("3건");
  });

  it("집계가 없어도 표는 그린다 — 패널 하나가 던지면 원가 메뉴 전체가 하얘진다", () => {
    const noCensus = { ...BOARD(), census: undefined } as unknown as CostTruthBoard;
    render(<CostTruthBoardPanel data={noCensus} />);
    expect(screen.getByTestId("cost-truth-row-OHI-0001")).toBeTruthy();
    expect(screen.queryByTestId("cost-truth-census")).toBeNull();
  });
});
