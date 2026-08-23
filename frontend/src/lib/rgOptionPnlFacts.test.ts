// rgOptionPnlFacts.test.ts — `rgFeeFactsFromOptionPnl()`(rgSettlementAxis.ts) 순수 함수 단위 테스트.
//
// 이 어댑터가 틀리면 화면 셋(대시보드 leaf 행·종합조망·이 RG 자기 화면)이 «같은 사실을 다르게
// 말하는» 병이 재발한다(파일 머리말 참조). 특히 요율 스케일(0~1 vs 0~100)을 한 곳이라도
// 놓치면 5%가 500%나 0.05%로 조용히 렌더된다 — 그 지점을 좁혀서 잡는다.
import { describe, it, expect } from "vitest";
import { rgFeeFactsFromOptionPnl } from "./rgSettlementAxis";

describe("rgFeeFactsFromOptionPnl — 요율 스케일(0~1 → %)", () => {
  it("rate=0.105(분수)이면 facts.rate=10.5(%)로 바뀐다 — ×100 누락 시 0.105%로 샌다", () => {
    const facts = rgFeeFactsFromOptionPnl({ rate: 0.105 });
    expect(facts.rate).toBe(10.5);
  });

  it("rate가 문자열로 와도(백엔드 Decimal 관례) 같은 결과다", () => {
    const facts = rgFeeFactsFromOptionPnl({ rate: "0.105" });
    expect(facts.rate).toBe(10.5);
  });

  it("rate=null이면 facts.rate=null이다 — 0%로 지어내지 않는다", () => {
    const facts = rgFeeFactsFromOptionPnl({ rate: null });
    expect(facts.rate).toBeNull();
  });

  it("rate가 필드 자체 없음이어도(undefined) null로 떨어진다", () => {
    const facts = rgFeeFactsFromOptionPnl({});
    expect(facts.rate).toBeNull();
  });
});

describe("rgFeeFactsFromOptionPnl — account_common.fee_unmapped_revenue를 집어온다", () => {
  it("account_common 아래 중첩된 fee_unmapped_revenue 값을 unmappedRevenue로 끌어올린다", () => {
    const facts = rgFeeFactsFromOptionPnl({
      account_common: { fee_unmapped_revenue: "12345" },
    });
    expect(facts.unmappedRevenue).toBe("12345");
  });

  it("account_common이 아예 없으면(옵션 응답이 아닌 경우) unmappedRevenue는 null이다", () => {
    const facts = rgFeeFactsFromOptionPnl({});
    expect(facts.unmappedRevenue).toBeNull();
  });

  it("account_common은 있는데 fee_unmapped_revenue 키가 없으면 null이다 — 0으로 채우지 않는다", () => {
    const facts = rgFeeFactsFromOptionPnl({ account_common: { period_fees: "1000" } });
    expect(facts.unmappedRevenue).toBeNull();
  });
});

describe("rgFeeFactsFromOptionPnl — reconciliation이 null이어도 안 터진다", () => {
  it("reconciliation: null이면 네 필드 전부 null — 예외를 던지지 않는다", () => {
    expect(() => rgFeeFactsFromOptionPnl({ reconciliation: null })).not.toThrow();
    const facts = rgFeeFactsFromOptionPnl({ reconciliation: null });
    expect(facts.reconcileCycle).toBeNull();
    expect(facts.reconcileActual).toBeNull();
    expect(facts.reconcileDiff).toBeNull();
    expect(facts.reconcilePct).toBeNull();
  });

  it("reconciliation 키 자체가 없어도(완전 빈 응답) 안 터지고 null로 떨어진다", () => {
    expect(() => rgFeeFactsFromOptionPnl({})).not.toThrow();
    const facts = rgFeeFactsFromOptionPnl({});
    expect(facts.reconcileCycle).toBeNull();
  });

  it("reconciliation이 있으면 cycle_from~cycle_to를 합쳐 reconcileCycle을 만든다", () => {
    const facts = rgFeeFactsFromOptionPnl({
      reconciliation: { cycle_from: "07-14", cycle_to: "07-20", actual: "250500", diff: "-500", diff_pct: "-0.05" },
    });
    expect(facts.reconcileCycle).toBe("07-14~07-20");
    expect(facts.reconcileActual).toBe("250500");
    expect(facts.reconcileDiff).toBe("-500");
    expect(facts.reconcilePct).toBe("-0.05");
  });
});

describe("rgFeeFactsFromOptionPnl — axis·basis·cycles·coverage 그대로 통과", () => {
  it("commission_axis/rate_basis/rate_cycles/fee_coverage를 각각 옮긴다", () => {
    const facts = rgFeeFactsFromOptionPnl({
      commission_axis: "sales_date",
      rate_basis: "settled_rate",
      rate_cycles: "07-14~07-20",
      fee_coverage: "0.9",
    });
    expect(facts.axis).toBe("sales_date");
    expect(facts.basis).toBe("settled_rate");
    expect(facts.cycles).toBe("07-14~07-20");
    expect(facts.coverage).toBe("0.9");
  });
});
