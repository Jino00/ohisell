import { describe, expect, it } from "vitest";
import { netScopeNote, unmappedNote } from "./netScope";

describe("netScopeNote — 하한을 완전한 손익으로 오독하지 않게 한다", () => {
  it("ad_only 행은 «광고비만(하한)»을 붙인다", () => {
    const n = netScopeNote({ net_scope: "ad_only", net_floor_ad: 597888 });
    expect(n?.text).toBe("광고비만(하한)");
    expect(n?.title).toContain("판매(납품가)");
  });

  it("소계는 섞인 하한 금액을 그대로 말한다", () => {
    expect(netScopeNote({ net_scope: "partial", net_floor_ad: 597888 })?.text).toBe(
      "하한 포함 597,888원",
    );
  });

  it("full 행엔 아무것도 안 붙는다", () => {
    expect(netScopeNote({ net_scope: "full", net_floor_ad: 0 })).toBeNull();
  });

  it("partial인데 하한이 0이면 붙이지 않는다 — 없는 경고를 만들지 않는다", () => {
    expect(netScopeNote({ net_scope: "partial", net_floor_ad: 0 })).toBeNull();
  });

  it("문자열로 와도(백엔드는 Decimal을 문자열로 낸다) 판정이 산다", () => {
    expect(netScopeNote({ net_scope: "partial", net_floor_ad: "597888.00" as never })?.text).toBe(
      "하한 포함 597,888원",
    );
  });

  it("필드가 아예 없으면 조용하다", () => {
    expect(netScopeNote({})).toBeNull();
  });
});

describe("unmappedNote — 이익률이 왜 높은지 행이 스스로 말한다", () => {
  it("자사몰 2026-08-18 재현: 277,300원 중 180,000원이 원가 0 → 64.9%", () => {
    const n = unmappedNote({ unmapped_revenue: 180000, product_revenue: 277300 });
    expect(n?.pct).toBeCloseTo(64.9, 1);
    expect(n?.text).toBe("⚠️ 원가 미상 64.9%");
  });

  it("원가가 다 붙었으면 조용하다", () => {
    expect(unmappedNote({ unmapped_revenue: 0, product_revenue: 277300 })).toBeNull();
  });

  it("제품매출이 0이면 0으로 나누지 않는다", () => {
    expect(unmappedNote({ unmapped_revenue: 180000, product_revenue: 0 })).toBeNull();
  });
});
