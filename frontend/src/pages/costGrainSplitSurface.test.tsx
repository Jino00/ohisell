// @vitest-environment jsdom
//
// costGrainSplitSurface.test.tsx — 분할이 **화면 픽셀이 되는가** (계약 D-CPP-67 §4 S1·S2)
//
// ## 이 파일이 지키는 것 (합격기준과 1:1)
//
//   S1-② 계획표 12행이 «변형 · 원가표 줄 · 계획 ↔ 라이브»로 그려진다
//   Q3-B 계획과 다른 칸이 **숨지 않는다** — 다른 행·못 붙이는 SKU가 같은 화면에 선다
//   Q3-B 계획과 다르면 실행 버튼이 **막힌다** (자백 없는 진행이 없다)
//   S1-③ 누르기 «전»엔 아무것도 실행되지 않는다 — 렌더만으로 onRun이 불리면 안 된다
//
// ★**이 파일이 있는 이유**: 값이 도는 층과 사람이 읽는 층은 따로 지켜야 한다. 백엔드 12건이
//   전건 초록인 채 패널이 화면에서 통째로 사라질 수 있다(n=26 적대 리뷰 1R P1이 그 자리였다).
// ★배선(탭 → 패널 도달)은 여기서 못 잰다 — 순수 컴포넌트를 props로 렌더하기 때문이다.
//   그건 `costPageReachesTheUser.test.tsx`가 `App`을 통째로 렌더해서 잰다.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { CostGrainSplitPanel } from "./CostPage";
import type { CostGrainSplitPreview } from "../lib/api";

afterEach(() => cleanup());

const CLEAN: CostGrainSplitPreview = {
  contract: "D-CPP-67",
  plan_sku_total: 92,
  live_sku_total: 92,
  safe_to_execute: true,
  sentence: "계획표 12행과 라이브가 전부 같다 — 실행할 수 있다",
  groups: [
    {
      product_name: "오하이 빛반사, 지문방지 매트 필름 3매",
      form_factor: "fold",
      signal_kind: "composition",
      base_recipe_id: 70,
      base_recipe_status: "approved",
      sku_count: 30,
      matches_plan: true,
      reason: null,
      unassigned: [],
      variants: [
        {
          variant: "외3+내3", is_base: true, cost_table_item: "지문방지_내부3매+외부3매",
          cost_table_item_id: 28, cost_table_item_total: "6186.40",
          expected_skus: 9, live_skus: 9, matches_plan: true,
          recipe_id: 70, recipe_status: "approved", reason: null,
        },
        {
          variant: "외3", is_base: false, cost_table_item: "지문방지_외부3매",
          cost_table_item_id: 36, cost_table_item_total: "2666.40",
          expected_skus: 9, live_skus: 9, matches_plan: true,
          recipe_id: null, recipe_status: null, reason: null,
        },
      ],
    },
  ],
};

const DIRTY: CostGrainSplitPreview = {
  ...CLEAN,
  safe_to_execute: false,
  live_sku_total: 92,
  sentence: "계획표와 다른 칸이 있다 — 실행은 거부된다",
  groups: [
    {
      ...CLEAN.groups[0],
      matches_plan: false,
      reason: "아래 행이 계획표와 다르다",
      unassigned: [
        {
          internal_sku: "OHI-0469",
          product_name: "오하이 빛반사, 지문방지 매트 필름 3매, 갤럭시Z폴드9",
          cost_price: "2666.00",
          reason: "1차 신호 없음 — 상품명이 변형을 말하지 않는다(사람이 지정해야 한다)",
        },
      ],
      variants: [
        CLEAN.groups[0].variants[0],
        {
          ...CLEAN.groups[0].variants[1],
          live_skus: 8,
          matches_plan: false,
          reason: "계획 9건 ↔ 라이브 8건 — 표와 다르다",
        },
      ],
    },
  ],
};

describe("그레인 분할 패널", () => {
  it("계획표 행이 «변형 · 원가표 줄 · 계획 ↔ 라이브»로 그려진다", () => {
    render(
      <CostGrainSplitPanel data={CLEAN} busy={false} error={null} result={null} onRun={vi.fn()} />,
    );
    expect(screen.getByTestId("cost-grain-split-panel")).toBeTruthy();
    expect(screen.getByTestId("cost-grain-split-variant-fold-외3")).toBeTruthy();
    expect(screen.getByTestId("cost-grain-split-live-fold-외3").textContent).toBe("9");
    // 원가표 줄 이름이 «화면»에 있다 — 사람이 픽을 감사할 수 있어야 한다
    expect(screen.getByText("지문방지_외부3매")).toBeTruthy();
    expect(screen.getByTestId("cost-grain-split-live-total").textContent).toBe("92");
  });

  it("★null과 «대상 0건»을 가른다 — 로딩이 「끝났다」로 안 읽힌다", () => {
    render(
      <CostGrainSplitPanel data={null} busy={false} error={null} result={null} onRun={vi.fn()} />,
    );
    expect(screen.getByTestId("cost-grain-split-loading")).toBeTruthy();
    expect(screen.queryByTestId("cost-grain-split-panel")).toBeNull();
  });

  it("★계획과 다른 칸이 숨지 않는다 — 다른 행과 못 붙이는 SKU가 같은 화면에 선다", () => {
    render(
      <CostGrainSplitPanel data={DIRTY} busy={false} error={null} result={null} onRun={vi.fn()} />,
    );
    expect(screen.getByTestId("cost-grain-split-blocked")).toBeTruthy();
    expect(screen.getByText(/계획 9건 ↔ 라이브 8건/)).toBeTruthy();
    const un = screen.getByTestId("cost-grain-split-unassigned-fold");
    expect(un.textContent).toContain("OHI-0469");
    expect(un.textContent).toContain("1차 신호 없음");
  });

  it("★계획과 다르면 실행 버튼이 막힌다 — 자백 없는 진행이 없다", () => {
    const onRun = vi.fn();
    render(
      <CostGrainSplitPanel data={DIRTY} busy={false} error={null} result={null} onRun={onRun} />,
    );
    const btn = screen.getByTestId("cost-grain-split-run") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    fireEvent.click(btn);
    expect(onRun).not.toHaveBeenCalled();
  });

  it("★렌더만으로는 아무것도 실행되지 않는다 — 누를 때만 부른다", () => {
    const onRun = vi.fn();
    render(
      <CostGrainSplitPanel data={CLEAN} busy={false} error={null} result={null} onRun={onRun} />,
    );
    expect(onRun).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("cost-grain-split-run"));
    expect(onRun).toHaveBeenCalledTimes(1);
  });

  it("실패가 조용히 삼켜지지 않는다", () => {
    render(
      <CostGrainSplitPanel
        data={CLEAN}
        busy={false}
        error="계획표와 라이브가 다르다 — 실행을 거부한다"
        result={null}
        onRun={vi.fn()}
      />,
    );
    expect(screen.getByTestId("cost-grain-split-error").textContent).toContain("거부");
  });
});
