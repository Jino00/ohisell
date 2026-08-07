// @vitest-environment jsdom
//
// rocket1pPnlAudit.test.tsx — 근거 화면이 지켜야 하는 것:
//  ① B1(판정 불가)은 «판정 안 함»으로 그린다 — 값이 무엇이든 «통과»가 아니다(거짓 초록 금지).
//  ② 통과한 검사도 좌·우변 숫자를 보인다 — 발견 0건과 실행 안 됨은 같은 숫자로 보인다(교훈 #123).
//  ③ suggested 원가 배지는 «사람 미확인»을 말한다.
//  ④ 원자 상세 요청에 화면이 보고 있는 창(from/to)이 실린다 — 창을 좁히면 분담금 «모름»
//     판정이 달라져 화면이 «—»로 그린 행에 숫자가 찍힌다(원자 파생 SA의 창 종속성 계약).
//  ⑤ 옵션 표 잘림 경고 — option_table_truncated=true일 때만 뜬다(거짓일 때는 안 뜬다).
//  ⑥ 순이익 합에서 빠진 매출 — revenue_out_of_net이 있으면 문장이 뜨고, null이면(추정 금지)
//     안 뜬다.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import type { PnlAuditAtoms, PnlAuditChecks } from "../lib/api";

const DEFAULT_CHECKS: PnlAuditChecks = {
  period: { from: "2026-08-01", to: "2026-08-07", vendor_id: "A01029796" },
  ladder: {
    basis: "costed_subset", qty: 100, revenue: "1000000", cost: "300000",
    promo_burden: "0", ad_spend: "200000", vat: "45455", net_profit: "454545",
    profit_rate: "0.4545", ad_no_sales: "0", ad_no_sales_included: false,
    ad_option_total: "200000", ad_account_total: "210000",
    cost_coverage: "0.97", revenue_priced: "1030000", blocked: null,
  },
  checks: [
    { id: "A1", label: "일별 합 = 사다리 순이익", left: "454545", right: "454545",
      diff: "0", unit: "원", verdict: "pass", note: null },
    { id: "A6", label: "창에 걸친 프로모션 전건에 할인액 원천", left: "0", right: "0",
      diff: "0", unit: "건", verdict: "pass", note: "프로모션 수집 최종 갱신 2026-08-07 12:00:00 (KST)." },
    { id: "B1", label: "두 축 대사 (계산서 ↔ 판매)", left: "44567166", right: "47048828",
      diff: "-2481662", unit: "원", verdict: "undetermined",
      note: "1P 재고 데이터가 없어 판정하지 않습니다." },
  ],
};

const DEFAULT_ATOMS: PnlAuditAtoms = {
  period: { from: "2026-08-01", to: "2026-08-07" },
  burden_known: true,
  query: { sort: "revenue", flt: "all", option_id: null },
  count: 1, total: 1,
  option_count: 1, option_limit: 1_000_000, option_table_truncated: false,
  totals: { qty: 10, net_profit: "354545", net_profit_known: 1, net_profit_unknown: 0,
            revenue_in_net: "600000", revenue_out_of_net: null,
            revenue_out_of_net_unknown: 0 },
  atoms: [{
    date: "2026-08-04", option_id: "A", sku_id: "S1", product_name: "상품 A",
    qty: 10, consumer_revenue: "1000000", our_revenue: "600000",
    unit_price: "60000", cost: "200000", unit_cost: "20000",
    ad_spend: "10000", promo_burden: "0", net_profit: "354545",
    net_profit_upper: null, cost_source: "suggested" as const,
  }],
};

const h = vi.hoisted(() => ({
  atomCalls: [] as Array<{ date: string; optionId: string; from: string; to: string }>,
  fetchChecks: vi.fn(),
  fetchAtoms: vi.fn(),
}));

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  fetchPnlAuditChecks: h.fetchChecks,
  fetchPnlAuditAtoms: h.fetchAtoms,
  // ★계약 4번: from/to가 그대로 넘어오는지 인자를 캡처해 단언한다(응답은 이 테스트의 관심사가
  //   아니라 pending으로 둔다 — 로딩 상태만 유지되면 충분하다).
  fetchPnlAuditAtom: (p: { date: string; optionId: string; from: string; to: string }) => {
    h.atomCalls.push(p);
    return new Promise<never>(() => {});
  },
}));

import Rocket1PPnlAudit from "./Rocket1PPnlAudit";

afterEach(() => {
  cleanup();
  h.fetchChecks.mockReset();
  h.fetchAtoms.mockReset();
  h.fetchChecks.mockResolvedValue(DEFAULT_CHECKS);
  h.fetchAtoms.mockResolvedValue(DEFAULT_ATOMS);
});

// afterEach가 다음 테스트를 위한 리셋이라, 첫 테스트를 위해 여기서도 한 번 세팅해 둔다.
h.fetchChecks.mockResolvedValue(DEFAULT_CHECKS);
h.fetchAtoms.mockResolvedValue(DEFAULT_ATOMS);

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/rocket-1p/pnl-audit?from=2026-08-01&to=2026-08-07"]}>
      <Routes><Route path="/rocket-1p/pnl-audit" element={<Rocket1PPnlAudit />} /></Routes>
    </MemoryRouter>,
  );
}

describe("Rocket1PPnlAudit", () => {
  it("B1은 «판정 안 함»으로 그린다 — 통과가 아니다", async () => {
    renderPage();
    expect(await screen.findByText("판정 안 함")).toBeTruthy();
    // 판정 불가여도 좌·우변은 보인다
    expect(await screen.findByText("44,567,166원")).toBeTruthy();
  });

  it("통과한 검사도 좌·우변 숫자를 보인다", async () => {
    renderPage();
    expect((await screen.findAllByText("통과")).length).toBeGreaterThan(0);
    expect((await screen.findAllByText("454,545원")).length).toBeGreaterThan(0);
  });

  it("suggested 원가는 «사람 미확인» 배지", async () => {
    renderPage();
    expect(await screen.findByText("이름 유사도 — 사람 미확인")).toBeTruthy();
  });

  it("원자 상세 요청에 화면이 보고 있는 창(from/to)이 실린다", async () => {
    h.atomCalls.length = 0;
    renderPage();
    // 원자 행(날짜 셀)을 눌러 상세를 연다 — 클릭 핸들러는 <tr>에 있고 이벤트는 버블링된다.
    fireEvent.click(await screen.findByText("2026-08-04"));

    expect(h.atomCalls.length).toBeGreaterThan(0);
    const call = h.atomCalls[0];
    expect(call.date).toBe("2026-08-04");
    expect(call.optionId).toBe("A");
    // ★창을 좁혀 부르면(예: date~date) 분담금 «모름» 판정이 달라진다 — 화면이 보던 창
    //   그대로여야 한다.
    expect(call.from).toBe("2026-08-01");
    expect(call.to).toBe("2026-08-07");
  });

  it("옵션 표가 잘리면 경고가 뜬다", async () => {
    h.fetchAtoms.mockResolvedValue({
      ...DEFAULT_ATOMS,
      option_count: 2_000_000, option_table_truncated: true,
    });
    renderPage();
    expect(await screen.findByText(/옵션 표가 잘렸습니다/)).toBeTruthy();
  });

  it("옵션 표가 안 잘리면 잘림 경고가 안 뜬다", async () => {
    renderPage(); // DEFAULT_ATOMS.option_table_truncated === false
    // 다른 카드(원자 목록)가 뜰 때까지 기다린 뒤 잘림 경고 부재를 확인한다.
    await screen.findByText("상품 A");
    expect(screen.queryByText(/옵션 표가 잘렸습니다/)).toBeNull();
  });

  it("순이익 합에서 빠진 매출이 있으면 문장이 뜬다", async () => {
    h.fetchAtoms.mockResolvedValue({
      ...DEFAULT_ATOMS,
      totals: { ...DEFAULT_ATOMS.totals, net_profit_unknown: 22,
                revenue_out_of_net: "437110", revenue_out_of_net_unknown: 3 },
    });
    renderPage();
    expect(await screen.findByText(/이 합계는 순이익을 낼 수 있는 행만 더한 값입니다/)).toBeTruthy();
    expect(await screen.findByText(/437,110원/)).toBeTruthy();
    // 매출조차 모르는 행 수도 0으로 접지 않고 밝힌다
    expect(await screen.findByText(/매출조차 모르는 행 3건/)).toBeTruthy();
  });

  it("빠진 매출 필드가 null이면 문장이 안 뜬다(추정 금지)", async () => {
    renderPage(); // DEFAULT_ATOMS.totals.revenue_out_of_net === null
    await screen.findByText("상품 A");
    expect(screen.queryByText(/순이익을 낼 수 있는 행만 더한 값입니다/)).toBeNull();
  });
});
