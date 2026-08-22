// rgSettlementAxis.test.ts — RG 정산공제가 「어느 축이고 무엇을 근거로 하는가」를 화면이
// 말하게 하는 자백 층(`rgSettlementAxis.ts`)의 순수 함수 테스트.
//
// 왜 이 테스트가 필요한가: 이 저장소에서 직전에 겪은 사고가 있다 — 백엔드 변이 14종은 다
// 죽었는데 「값이 화면 픽셀이 되는 마지막 한 칸」은 무보호라 생존 변이 3종이 전부 프론트에서
// 나왔다(rgNetAxisSurface.test.tsx 머리말). 이 파일은 그 「마지막 한 칸」 앞에 있는 순수 판정
// 층을 지킨다 — 렌더 보호는 `rgSettlementAxisSurface.test.tsx`가 맡는다.
import { describe, it, expect } from "vitest";
import {
  rgFeeNote,
  rgFeeFactsFromRow,
  rgFeeFactsFromSummary,
  AXIS_SALES_DATE,
  BASIS_SETTLED,
  BASIS_RATE_UNKNOWN,
} from "./rgSettlementAxis";

describe("rgFeeNote — RG가 아닌 행엔 아무것도 안 뜬다", () => {
  it("axis도 basis도 없으면 null(다른 채널 행이 이 문구를 물려받지 않는다)", () => {
    expect(rgFeeNote({})).toBeNull();
    expect(rgFeeNote({ rate: 10, coverage: 0.5 })).toBeNull();
  });
});

describe("rgFeeNote — 판매일 축 · 실측 요율", () => {
  it("「판매일 축」과 요율 %가 문구에 뜬다", () => {
    const n = rgFeeNote({
      axis: AXIS_SALES_DATE,
      basis: BASIS_SETTLED,
      rate: 10.5,
      cycles: "07-14~07-20",
    });
    expect(n).not.toBeNull();
    expect(n!.text).toContain("판매일 축");
    expect(n!.text).toContain("10.50%");
    expect(n!.title).toContain("실측값");
  });
});

describe("rgFeeNote — 요율 미상", () => {
  it("「요율 미상」이 뜨고 기본 요율 숫자는 문구에 등장하지 않는다", () => {
    const n = rgFeeNote({
      axis: AXIS_SALES_DATE,
      basis: BASIS_RATE_UNKNOWN,
      rate: 7.8, // 옛 기본 요율값 — 있어도 화면엔 안 나가야 한다
    });
    expect(n).not.toBeNull();
    expect(n!.text).toContain("요율 미상");
    expect(n!.text).not.toContain("7.8");
    expect(n!.title).toContain("기본 요율로 추정하지 않는다");
  });
});

describe("rgFeeNote — 정산 인식일 축(못 잰 경우의 자백)", () => {
  it("⚠️와 「정산 인식일 축」이 뜬다", () => {
    const n = rgFeeNote({ axis: "recognition_date" });
    expect(n).not.toBeNull();
    expect(n!.text).toContain("⚠️");
    expect(n!.text).toContain("정산 인식일 축");
  });

  it("axis 필드가 아예 없어도(구버전 응답) 「정산 인식일 축」으로 물러선다 — basis만 있어도 트리거된다", () => {
    const n = rgFeeNote({ basis: BASIS_SETTLED, rate: 5 });
    expect(n).not.toBeNull();
    expect(n!.text).toContain("정산 인식일 축");
  });
});

describe("rgFeeNote — 커버리지", () => {
  it("커버리지 < 1이면 %가 뜨고, unmappedRevenue가 있으면 그 금액이 title(툴팁)에 뜬다", () => {
    const n = rgFeeNote({
      axis: AXIS_SALES_DATE,
      basis: BASIS_SETTLED,
      rate: 10,
      coverage: 0.833,
      unmappedRevenue: 123456,
    });
    expect(n).not.toBeNull();
    expect(n!.text).toContain("83.3%");
    expect(n!.title).toContain("123,456원");
  });

  it("커버리지가 1이면(완전) 커버리지 문구를 안 붙인다", () => {
    const n = rgFeeNote({ axis: AXIS_SALES_DATE, basis: BASIS_SETTLED, rate: 10, coverage: 1 });
    expect(n!.text).not.toContain("커버리지");
  });

  it("커버리지 < 1인데 unmappedRevenue가 0/없음이면 그 금액 문구는 title에 안 붙는다", () => {
    const n = rgFeeNote({ axis: AXIS_SALES_DATE, basis: BASIS_SETTLED, rate: 10, coverage: 0.9 });
    expect(n!.text).toContain("90.0%");
    // ★문구가 바뀌면 부정 단언은 «조용히» 공허해진다(적대 리뷰 3R P2). 옛 단언은
    //   `"물류비를 0으로"`를 봤는데 그 문자열이 새 사유 문구엔 아예 없어서, 자백을 항상
    //   붙이는 변이(`un > 0` → `un >= 0`)가 그대로 살아남았다. 지금 문구를 가리킨다.
    expect(n!.title).not.toContain("비용을 못 붙였다");
  });
});

describe("rgFeeNote — 장부대조", () => {
  it("reconcileDiff가 음수일 때 「−」 부호가 정상 표기되고 이중부호가 안 나온다", () => {
    const n = rgFeeNote({
      axis: AXIS_SALES_DATE,
      basis: BASIS_SETTLED,
      rate: 10,
      reconcileCycle: "07-14~07-20",
      reconcileActual: 1000000,
      reconcileDiff: -12345,
      reconcilePct: -1.2,
    });
    expect(n!.text).toContain("−12,345원");
    expect(n!.text).not.toContain("−−");
    expect(n!.text).not.toContain("+−");
  });

  it("reconcileDiff가 양수면 「+」 부호", () => {
    const n = rgFeeNote({
      axis: AXIS_SALES_DATE,
      basis: BASIS_SETTLED,
      rate: 10,
      reconcileCycle: "07-14~07-20",
      reconcileActual: 1000000,
      reconcileDiff: 5000,
      reconcilePct: 0.5,
    });
    expect(n!.text).toContain("+5,000원");
  });

  it("|pct| > 5면 ⚠️가 대조 항목에 붙는다", () => {
    const n = rgFeeNote({
      axis: AXIS_SALES_DATE,
      basis: BASIS_SETTLED,
      rate: 10,
      reconcileCycle: "07-14~07-20",
      reconcileActual: 1000000,
      reconcileDiff: -60000,
      reconcilePct: -6,
    });
    // 장부대조 파트 자체에 ⚠️가 붙어야 한다(axis가 sales_date라 파트[0]엔 ⚠️가 없다).
    const reconcilePart = n!.text.split(" · ").find((p) => p.includes("장부대조"));
    expect(reconcilePart).toContain("⚠️");
  });

  it("|pct| <= 5면 ⚠️가 안 붙는다", () => {
    const n = rgFeeNote({
      axis: AXIS_SALES_DATE,
      basis: BASIS_SETTLED,
      rate: 10,
      reconcileCycle: "07-14~07-20",
      reconcileActual: 1000000,
      reconcileDiff: -50000,
      reconcilePct: -5,
    });
    const reconcilePart = n!.text.split(" · ").find((p) => p.includes("장부대조"));
    expect(reconcilePart).not.toContain("⚠️");
  });
});

describe("rgFeeFactsFromRow — 대시보드 채널 요약 행의 칸 이름 매핑", () => {
  it("행의 commission_rate는 이미 %다 — 그대로 옮긴다(×100 하지 않는다)", () => {
    const facts = rgFeeFactsFromRow({
      commission_axis: "sales_date",
      commission_basis: "settled_rate",
      commission_rate: "10.50",
      commission_rate_cycles: "07-14~07-20",
      fee_coverage: "0.9",
      fee_unmapped_revenue: "1000",
      settlement_reconcile_cycle: "07-14~07-20",
      settlement_reconcile_actual: "1000000",
      settlement_reconcile_diff: "-500",
      settlement_reconcile_pct: "-0.05",
    });
    expect(facts.rate).toBe("10.50");
    const n = rgFeeNote(facts);
    expect(n!.text).toContain("10.50%");
  });

  it("필드가 없는 행은 axis/basis 둘 다 null → rgFeeNote가 null", () => {
    const facts = rgFeeFactsFromRow({ kind: "leaf", label: "자사몰" });
    expect(facts.axis).toBeNull();
    expect(facts.basis).toBeNull();
    expect(rgFeeNote(facts)).toBeNull();
  });
});

describe("rgFeeFactsFromSummary — 종합조망 계정 요약의 칸 이름 매핑", () => {
  it("요약축 rg_fee_rate는 비율(0~1)이다 — ×100 되어야 한다", () => {
    const facts = rgFeeFactsFromSummary({
      rg_settlement_axis: "sales_date",
      rg_fee_basis: "settled_rate",
      rg_fee_rate: 0.105, // 10.5%를 비율로
    });
    expect(facts.rate).toBeCloseTo(10.5, 5);
    const n = rgFeeNote(facts);
    expect(n!.text).toContain("10.50%");
  });

  it("행의 %값(10.5)을 실수로 요약 매핑에 넣으면 1050%가 되어 확실히 틀려 보인다(뒤바뀜 회귀 가드)", () => {
    // 이 테스트의 목적: rgFeeFactsFromSummary가 ×100을 하는 함수라는 사실 자체를 못박는다.
    // rgFeeFactsFromRow가 주는 "이미 %인 값"을 실수로 이 함수에 통과시키면 100배가 튄다.
    const facts = rgFeeFactsFromSummary({ rg_fee_rate: 10.5 });
    expect(facts.rate).toBeCloseTo(1050, 1);
  });

  it("rg_fee_reconcile 중첩 객체에서 대조 4필드를 꺼낸다", () => {
    const facts = rgFeeFactsFromSummary({
      rg_settlement_axis: "sales_date",
      rg_fee_basis: "settled_rate",
      rg_fee_rate: 0.1,
      rg_fee_reconcile: {
        cycle_from: "07-14",
        cycle_to: "07-20",
        actual: "1000000",
        diff: "-500",
        diff_pct: "-0.05",
      },
    });
    expect(facts.reconcileCycle).toBe("07-14~07-20");
    expect(facts.reconcileActual).toBe("1000000");
    expect(facts.reconcileDiff).toBe("-500");
    expect(facts.reconcilePct).toBe("-0.05");
  });

  it("rg_fee_reconcile이 없으면 대조 필드는 전부 null(대조 파트가 안 뜬다)", () => {
    const facts = rgFeeFactsFromSummary({ rg_settlement_axis: "sales_date", rg_fee_basis: "settled_rate", rg_fee_rate: 0.1 });
    expect(facts.reconcileCycle).toBeNull();
    const n = rgFeeNote(facts);
    expect(n!.text).not.toContain("장부대조");
  });
});
