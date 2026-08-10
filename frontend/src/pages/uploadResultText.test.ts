// uploadResultText.test.ts — 업로드 배너가 **원가 거부를 말하는지** 지킨다.
//
// 적대 리뷰 2R P2-N1: 백엔드는 정확히 거부하는데 배너에서 그 사실을 빼는 변이 2종이
// 프론트 290건에서 전부 살아남았다. 거부의 값어치는 전부 이 마지막 한 칸에 있다 —
// 운영자가 못 보면 옛 엑셀을 계속 쓴다.
import { describe, expect, it } from "vitest";

import {
  buildCostSheetUploadText,
  buildMappingUploadText,
  countMappingIssues,
  shouldWarnMappingUpload,
  type CostSheetUploadCounts,
  type MappingUploadCounts,
} from "./uploadResultText";

const clean: MappingUploadCounts = {
  products_created: 0,
  products_updated: 3,
  mappings_created: 3,
  mappings_updated: 0,
  mappings_conflicted: 0,
  orders_linked: 0,
  unknown_labels: [],
  duplicate_product_names: [],
  duplicate_channel_ids: [],
  mapping_conflicts: [],
  label_mismatches: [],
  cost_buffers: [],
  cost_guard_unavailable: null,
};

describe("연결맵 엑셀 업로드 배너", () => {
  it("원가 거부가 있으면 배너가 그것을 말한다", () => {
    const t = buildMappingUploadText({ ...clean, cost_buffers: ["행2: ...", "행3: ..."] });
    expect(t).toContain("원가 거부 2건");
  });

  it("★「수정 3」만 남아 거부를 가리지 않는다", () => {
    // 1R P1의 실제 관측: 배너가 「수정 3」이라고 적극적으로 오도했다.
    const t = buildMappingUploadText({ ...clean, cost_buffers: ["행2: ..."] });
    expect(t).toContain("수정 3");
    expect(t).toContain("⚠️");
  });

  it("원가 거부를 「확인 필요 N건」에 합산하지 않는다", () => {
    // 섞으면 무결성 이슈 속에 묻힌다 — 두 수는 뜻이 다르다.
    expect(countMappingIssues({ ...clean, cost_buffers: ["행2: ..."] })).toBe(0);
  });

  it("스냅샷이 없으면 «원가 미검사»를 말한다", () => {
    const t = buildMappingUploadText({ ...clean, cost_guard_unavailable: "정본 스냅샷 없음" });
    expect(t).toContain("미검사");
  });

  it("깨끗하면 경고 문구가 없다 — 상시 경고는 곧 무시된다", () => {
    const t = buildMappingUploadText(clean);
    expect(t).not.toContain("⚠️");
    expect(shouldWarnMappingUpload(clean)).toBe(false);
  });

  it("★콘솔 경고 게이트가 원가 거부·미검사를 포함한다", () => {
    // 1R P1: console.warn이 issues>0에만 게이트돼 있어 콘솔에서도 사라졌다.
    expect(shouldWarnMappingUpload({ ...clean, cost_buffers: ["행2: ..."] })).toBe(true);
    expect(shouldWarnMappingUpload({ ...clean, cost_guard_unavailable: "없음" })).toBe(true);
  });
});

describe("상품 원가표 엑셀 업로드 배너", () => {
  const base: CostSheetUploadCounts = { created: 0, updated: 0, mappings_created: 0 };

  it("원가 거부 건수를 「오류 N건」과 따로 말한다", () => {
    const t = buildCostSheetUploadText({ ...base, cost_buffer_blocked: 2, errors: ["e1", "e2"] });
    expect(t).toContain("원가 거부 2건");
    expect(t).toContain("오류 2건");
  });

  it("★가드가 꺼져 있으면 «미검사»를 말한다(변이 M-a가 살던 자리)", () => {
    const t = buildCostSheetUploadText({ ...base, cost_guard: "unavailable(정본 스냅샷 없음)" });
    expect(t).toContain("미검사");
  });

  it("가드가 active면 미검사 문구가 없다", () => {
    expect(buildCostSheetUploadText({ ...base, cost_guard: "active" })).not.toContain("미검사");
  });
});
