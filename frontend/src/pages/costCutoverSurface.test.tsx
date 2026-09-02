// @vitest-environment jsdom
//
// costCutoverSurface.test.tsx — 컷오버가 **화면 픽셀이 되는가** (계약 D-CPP-64 §4 S3)
//
// ## 이 파일이 지키는 것 (합격기준과 1:1)
//
//   S3-① 클릭 «전»에 SKU 수 · old→new · Σ격차가 **그려진다**
//   S3-② 누르면 그 그룹만 실행되고, 결과(몇 건 바뀌었나)가 화면에 선다
//   S3-③ **누르기 전엔 아무것도 실행되지 않는다** — 렌더만으로 onRun이 불리면 안 된다
//   S3-④ 컷오버로 **못** 고치는 것(보류·정본 없음)이 같은 화면에 있다
//
// ★**이 파일이 있는 이유**: n=25 적대 리뷰 1R P1이 정확히 이 자리였다 — 백엔드 테스트
//   330건이 전건 초록인데 `match_reason`을 None으로 끊는 변이가 살아남았다. 값이 도는 층과
//   사람이 읽는 층은 **따로** 지켜야 한다.
// ★배선(탭 클릭 → 패널 도달)은 여기서 못 잰다 — 순수 컴포넌트를 props로 렌더하기 때문이다.
//   그건 `costPageReachesTheUser.test.tsx`가 `App`을 통째로 렌더해서 잰다.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { CostCutoverPanel } from "./CostPage";
import type { CostCutoverPreview, CostCutoverResult } from "../lib/api";

afterEach(() => cleanup());

const PREVIEW: CostCutoverPreview = {
  total_sku_count: 278,
  total_gap_sum: "111367.8",
  groups: [
    {
      cause: "g2_parts_299",
      cause_ref118: "G2",
      reason: "엑셀이 부자재 4종을 안 세고 있다 — 격차가 정확히 +299.0원. 계산이 정본",
      sku_count: 269,
      gap_sum: "80431.0",
      items: [
        {
          internal_sku: "OHI-0100",
          product_name: "지문방지 필름 3매",
          old_value: "2350.7",
          new_value: "2649.7",
          gap: "299.0",
          truth_label: "계산값",
        },
      ],
    },
    {
      cause: "g3_2_family_not_split",
      cause_ref118: "G3-2",
      reason: "계열 미분리 — 폴드 원가에 바폰 값이 붙어 있다",
      sku_count: 9,
      gap_sum: "30936.8",
      items: [
        {
          internal_sku: "OHI-0584",
          product_name: "자가복원 EPU 3매 Z폴드SE",
          old_value: "3010.7",
          new_value: "7870.3",
          gap: "4859.6",
          truth_label: "계산값",
        },
      ],
    },
  ],
  not_eligible: {
    held_count: 169,
    none_count: 512,
    sentence: "보류·정본 없음은 컷오버 대상이 아니다 — 맞출 «정본»이 아직 없다.",
  },
};

const RESULT: CostCutoverResult = {
  scope: "all",
  requested_count: 278,
  changed_count: 278,
  skipped_count: 0,
  gap_closed: "111367.8",
  changed: [],
  skipped: [],
};

function renderPanel(over: Partial<Parameters<typeof CostCutoverPanel>[0]> = {}) {
  const onRun = vi.fn();
  render(
    <CostCutoverPanel
      data={PREVIEW}
      result={null}
      busy={null}
      error={null}
      onRun={onRun}
      {...over}
    />,
  );
  return onRun;
}

// ═══════════════════════════════════════════════════════════════════
// S3-③ 누르기 전엔 아무것도 안 움직인다
// ═══════════════════════════════════════════════════════════════════

describe("무해성", () => {
  it("렌더만으로는 컷오버가 실행되지 않는다", () => {
    // ★계약 §4 S3 셋째 항목의 화면 쪽 절반. 패널이 마운트되면서 스스로 실행하면
    //   「클릭 없인 한 건도 안 움직인다」가 화면에서 깨진다.
    const onRun = renderPanel();
    expect(onRun).not.toHaveBeenCalled();
  });

  it("★응답 형식이 깨져도 원가 페이지를 끌고 내려가지 않는다", () => {
    // ★초판엔 이 방어가 없어서 `not_eligible`이 없는 응답 하나에 **원가 페이지가 통째로
    //   죽었다** — 새 패널 하나가 옆 탭(정본 판별·이력)까지 같이 데려갔고, 기존
    //   end-to-end 테스트 4건이 그걸 잡았다. 이 단언이 그 방어를 못 박는다.
    const broken = { groups: PREVIEW.groups, total_sku_count: 1, total_gap_sum: "1" };
    render(
      <CostCutoverPanel
        data={broken as unknown as CostCutoverPreview}
        result={null}
        busy={null}
        error={null}
        onRun={vi.fn()}
      />,
    );
    expect(screen.getByTestId("cost-cutover-broken")).toBeTruthy();
    expect(screen.queryByTestId("cost-cutover-panel")).toBeNull();
  });

  it("로딩 중(data=null)이면 «0건»이라고 말하지 않는다", () => {
    // ★null과 [] 은 다른 사실이다. 로딩을 「대상 0건」으로 그리면 사람이 「할 일 없음」으로
    //   읽고 화면을 닫는다 — 이 저장소가 반복해 밟은 「없음 ≠ 0」이다.
    render(
      <CostCutoverPanel data={null} result={null} busy={null} error={null} onRun={vi.fn()} />,
    );
    expect(screen.getByTestId("cost-cutover-loading")).toBeTruthy();
    expect(screen.queryByTestId("cost-cutover-empty")).toBeNull();
  });
});

// ═══════════════════════════════════════════════════════════════════
// S3-① 클릭 전에 서는 것 — 수 · old→new · Σ격차
// ═══════════════════════════════════════════════════════════════════

describe("클릭 전에 서는 것", () => {
  it("전체 대상 수와 Σ격차가 그려진다", () => {
    renderPanel();
    const total = screen.getByTestId("cost-cutover-total").textContent ?? "";
    expect(total).toContain("278");
    expect(total).toContain("111,367.8");
  });

  it("사유 그룹마다 SKU 수와 Σ격차가 그려진다", () => {
    renderPanel();
    const groups = screen.getByTestId("cost-cutover-groups").textContent ?? "";
    expect(groups).toContain("G2");
    expect(groups).toContain("269");
    expect(groups).toContain("80,431");
    expect(groups).toContain("G3-2");
    expect(groups).toContain("30,936.8");
  });

  it("★old→new가 «둘 다» 그려진다 — 하나만 있으면 무엇이 무엇으로 바뀌는지 모른다", () => {
    renderPanel();
    const row = screen.getByTestId("cost-cutover-item-OHI-0584").textContent ?? "";
    expect(row).toContain("3,010.7");
    expect(row).toContain("7,870.3");
  });
});

// ═══════════════════════════════════════════════════════════════════
// S3-④ 못 고치는 것도 같은 화면에
// ═══════════════════════════════════════════════════════════════════

describe("컷오버로 못 고치는 것", () => {
  it("보류·정본 없음 건수와 사유 문장이 그려진다", () => {
    // ★이걸 안 그리면 화면이 「278건 하면 원가가 다 맞는다」로 읽힌다. 실제로는 681건이
    //   정본 자체가 없다 — 이 트랙에서 가장 비싼 오독이다.
    renderPanel();
    const box = screen.getByTestId("cost-cutover-not-eligible").textContent ?? "";
    expect(box).toContain("169");
    expect(box).toContain("512");
    expect(box).toContain("대상이 아니다");
    expect(box).toContain("맞출 «정본»이 아직 없다");
  });
});

// ═══════════════════════════════════════════════════════════════════
// S3-② 누르면 그것만 실행되고 결과가 화면에 선다
// ═══════════════════════════════════════════════════════════════════

describe("실행", () => {
  it("「전체 맞추기」는 scope=all로 부른다", () => {
    const onRun = renderPanel();
    fireEvent.click(screen.getByTestId("cost-cutover-run-all"));
    expect(onRun).toHaveBeenCalledWith({ scope: "all" }, "all");
  });

  it("★그룹 버튼은 «그 사유만» 부른다 — 전건으로 새면 안 누른 그룹까지 움직인다", () => {
    const onRun = renderPanel();
    fireEvent.click(screen.getByTestId("cost-cutover-run-g3_2_family_not_split"));
    expect(onRun).toHaveBeenCalledWith(
      { scope: "cause", cause: "g3_2_family_not_split" },
      "g3_2_family_not_split",
    );
    expect(onRun).toHaveBeenCalledTimes(1);
  });

  it("실행 중에는 버튼이 잠긴다 — 두 번 눌러 두 번 도는 것을 막는다", () => {
    const onRun = renderPanel({ busy: "all" });
    fireEvent.click(screen.getByTestId("cost-cutover-run-all"));
    expect(onRun).not.toHaveBeenCalled();
  });

  it("대상이 0건이면 「전체 맞추기」가 잠긴다", () => {
    const onRun = renderPanel({
      data: { ...PREVIEW, total_sku_count: 0, groups: [], total_gap_sum: "0" },
    });
    fireEvent.click(screen.getByTestId("cost-cutover-run-all"));
    expect(onRun).not.toHaveBeenCalled();
    expect(screen.getByTestId("cost-cutover-empty")).toBeTruthy();
  });

  it("★결과가 화면에 선다 — 몇 건 바뀌었는지 사람이 읽는다", () => {
    renderPanel({ result: RESULT });
    const box = screen.getByTestId("cost-cutover-result").textContent ?? "";
    expect(box).toContain("278");
    expect(box).toContain("111,367.8");
  });

  it("★건너뛴 건이 있으면 사유가 화면에 선다 — 조용히 삼키면 「전부 됐다」가 거짓말이 된다", () => {
    renderPanel({
      result: {
        ...RESULT,
        changed_count: 277,
        skipped_count: 1,
        skipped: [
          {
            internal_sku: "OHI-9999",
            skip_reason: "not_cutover_ready",
            sentence: "컷오버 대상이 아니다 — 정본이 없거나 이미 일치한다",
          },
        ],
      },
    });
    const box = screen.getByTestId("cost-cutover-result").textContent ?? "";
    expect(box).toContain("건너뜀 1건");
    expect(box).toContain("컷오버 대상이 아니다");
  });

  it("★실패는 실패라고 말한다 — 조용하면 「됐다」로 읽힌다", () => {
    renderPanel({ error: "500 Internal Server Error" });
    const box = screen.getByTestId("cost-cutover-error").textContent ?? "";
    expect(box).toContain("컷오버 실패");
    expect(box).toContain("500");
  });
});
