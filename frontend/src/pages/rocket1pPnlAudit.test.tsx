// @vitest-environment jsdom
//
// rocket1pPnlAudit.test.tsx — 근거 화면이 지켜야 하는 것. 적대 리뷰(2026-08-08)가 지목한
// 「거짓 안심」 축을 각각 죽이는 테스트로 구성한다 — 항목 옆에 죽이는 변이(Mn)를 적는다.
//  ① B1(판정 불가)은 «판정 안 함»으로 그린다 — 값이 무엇이든 «통과»가 아니다(거짓 초록 금지).
//  ② 통과한 검사도 **그 행 안에서** 좌·우변 숫자를 보인다(M15 — 좌우변 은닉).
//     ★기존 판은 사다리 카드에도 같은 숫자가 찍혀 좌우변을 지워도 통과했다 — within(row)로 고쳤다.
//  ③ suggested 원가 배지는 «사람 미확인»을 말한다.
//  ④ 원자 상세 요청에 화면이 보고 있는 창(from/to)이 실린다(M3 — 창 인자 누락).
//  ⑤ 옵션 표 잘림 경고 — option_table_truncated=true일 때만 뜬다(거짓일 때는 안 뜬다).
//  ⑥ 순이익 합에서 빠진 행 — **행 수(net_profit_unknown)**가 게이트다(P1-3). 금액이 null이면
//     "모른다"고 말하지, 배너 자체가 사라지지 않는다(그러면 부분합이 전체합으로 읽힌다).
//  ⑦ M4·M5·M6 — null=«모름»이 포맷터에서 "0"/"0원"/"0.0%"로 접히지 않고 "—"로 남는다.
//  ⑧ M7 — 원가 몰라도 상한이 음수인 확정 적자 행은 "≤ N원"으로 표시된다(«—»로 지워지지 않는다).
//  ⑨ M8 — 사다리 blocked면 손익 없음 EmptyState, 정상 KV는 안 뜬다.
//  ⑩ M13 — 원가 확인 부분합 배지가 뜬다. M18 — 사다리 카드 자체가 사라지지 않는다.
//  ⑪ M10·M14 — a6Pass가 아닐 때 "분담금 0은 사실"을 말하지 않는다. promos:null(모름)은
//     "프로모션 없음"과 다르게 그린다.
//  ⑫ M11 — 흑자/적자 색상이 뒤바뀌지 않는다.
//  ⑬ P1-1 — 광고 행이 없을 때 "0원이 맞다"고 단정하지 않는다(사실+결과만, 원인 불명 명시).
//  ⑭ P2-1 — flt/sort가 URL에 실려 공유 가능하다.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, within, cleanup } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import type { PnlAuditAtoms, PnlAuditChecks, PnlAuditAtomDetail } from "../lib/api";

const BASE_LADDER: PnlAuditChecks["ladder"] = {
  basis: "costed_subset", qty: 100, revenue: "1000000", cost: "300000",
  promo_burden: "0", ad_spend: "200000", vat: "45455", net_profit: "454545",
  profit_rate: "0.4545", ad_no_sales: "0", ad_no_sales_included: false,
  ad_no_sales_days: "0", ad_uncosted: "0", ad_unattributed: "0",
  ad_option_total: "200000", ad_account_total: "210000",
  cost_coverage: "0.97", revenue_priced: "1030000", blocked: null,
};

const DEFAULT_CHECKS: PnlAuditChecks = {
  period: { from: "2026-08-01", to: "2026-08-07", vendor_id: "A01029796" },
  ladder: BASE_LADDER,
  checks: [
    { id: "A1", label: "일별 합 = 사다리 순이익", left: "454545", right: "454545",
      diff: "0", unit: "원", verdict: "pass", note: null },
    // ★M4용 — 판정 불가에 좌·우변이 아예 없는 행. won(null)이 "0원"으로 접히면 이 테스트가
    //   죽는다.
    { id: "A2", label: "옵션별 합 = 사다리 순이익", left: null, right: null,
      diff: null, unit: "원", verdict: "undetermined",
      note: "옵션 표가 잘렸습니다(1/2) — 합을 낼 수 없습니다" },
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

const DEFAULT_ATOM_DETAIL: PnlAuditAtomDetail = {
  period: { from: "2026-08-01", to: "2026-08-07" },
  date: "2026-08-04", option_id: "A",
  atom: { date: "2026-08-04", option_id: "A", qty: 10, our_revenue: "600000",
          cost: "200000", promo_burden: "0", ad_spend: "10000",
          net_profit: "354545", net_profit_upper: null, burden_known: true },
  sales: { date: "2026-08-04", option_id: "A", sku_id: "S1", product_name: "상품 A",
           qty: 10, consumer_revenue: "1000000", visitors: 120,
           source: "vendor_summary", synced_at: "2026-08-05 03:00:00" },
  unit_price: { purchase_order_seq: 999, unit_purchase_price: "60000", order_qty: 500,
                po_created_at: "2026-07-20T00:00:00", sibling_option_count: 2,
                note: "가장 최근 발주의 단가입니다 — 단가가 바뀌면 과거 판매의 매출도 소급됩니다." },
  cost: { map: { product_number: "S1", internal_sku: "SKU1", status: "confirmed",
                 match_method: "suggested", note: null, updated_at: "2026-08-01" },
          master: { internal_sku: "SKU1", product_name: "상품 A", cost_price: "20000" } },
  // ★P1-1 — 광고 행이 없는 상태(광고를 안 돌린 건지 수집이 안 된 건지 이 화면은 모른다).
  ad: null,
  promos: [],
};

const h = vi.hoisted(() => ({
  fetchChecks: vi.fn(),
  fetchAtoms: vi.fn(),
  fetchAtomDetail: vi.fn(),
}));

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  fetchPnlAuditChecks: h.fetchChecks,
  fetchPnlAuditAtoms: h.fetchAtoms,
  fetchPnlAuditAtom: h.fetchAtomDetail,
}));

import Rocket1PPnlAudit from "./Rocket1PPnlAudit";

function resetDefaults() {
  h.fetchChecks.mockReset();
  h.fetchAtoms.mockReset();
  h.fetchAtomDetail.mockReset();
  h.fetchChecks.mockResolvedValue(DEFAULT_CHECKS);
  h.fetchAtoms.mockResolvedValue(DEFAULT_ATOMS);
  // ★(가) 예전 판은 이 목이 **영원히 pending**이라 4단이 한 번도 그려지지 않았다 — 다섯 갈래
  //   원천 행(과 그 안의 P1-1 문구·a6Pass 게이팅)이 전부 무방비였다. 실제 응답을 resolve한다.
  h.fetchAtomDetail.mockResolvedValue(DEFAULT_ATOM_DETAIL);
}
resetDefaults(); // 첫 테스트를 위해 모듈 로드 시점에도 한 번.
afterEach(() => { cleanup(); resetDefaults(); });

function LocationDisplay() {
  const loc = useLocation();
  return <div data-testid="location">{loc.pathname}{loc.search}</div>;
}

function renderPage(initialEntry = "/rocket-1p/pnl-audit?from=2026-08-01&to=2026-08-07") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <LocationDisplay />
      <Routes><Route path="/rocket-1p/pnl-audit" element={<Rocket1PPnlAudit />} /></Routes>
    </MemoryRouter>,
  );
}

/** 검사 표에서 id로 그 행(<tr>)을 찾는다 — within(row)으로 좌·우변을 그 행 안에서만 본다. */
async function findCheckRow(id: string) {
  const idCell = await screen.findByText(id);
  const row = idCell.closest("tr");
  if (!row) throw new Error(`행을 못 찾음: ${id}`);
  return row as HTMLElement;
}

describe("Rocket1PPnlAudit — 1단 산술 검사", () => {
  it("B1은 «판정 안 함»으로 그린다 — 통과가 아니다", async () => {
    renderPage();
    const row = await findCheckRow("B1");
    expect(within(row).getByText("판정 안 함")).toBeTruthy();
    expect(within(row).getByText("44,567,166원")).toBeTruthy();
    expect(within(row).getByText("47,048,828원")).toBeTruthy();
  });

  it("통과한 검사도 그 행 안에서 좌·우변 숫자를 보인다(M15 — 좌우변 은닉 방지)", async () => {
    renderPage();
    const row = await findCheckRow("A1");
    expect(within(row).getByText("통과")).toBeTruthy();
    // 좌변·우변 둘 다 454,545원 — 행 안에 그 텍스트가 정확히 두 번(좌·우) 있어야 한다.
    // 좌변이나 우변 어느 한쪽만 숨겨도(M15) 1개로 줄어 이 assertion이 죽는다.
    expect(within(row).getAllByText("454,545원")).toHaveLength(2);
  });

  it("판정 불가 검사의 좌·우변이 null이면 «0원»이 아니라 «—»다(M4)", async () => {
    renderPage();
    const row = await findCheckRow("A2");
    expect(within(row).getByText("판정 안 함")).toBeTruthy();
    // 좌·우·차이 세 칸 모두 "—" — "0원"으로 접히면(M4) 이 assertion이 죽는다.
    expect(within(row).getAllByText("—")).toHaveLength(3);
    expect(within(row).queryByText("0원")).toBeNull();
  });
});

describe("Rocket1PPnlAudit — 2단 손익 사다리", () => {
  it("suggested 원가는 «사람 미확인» 배지", async () => {
    renderPage();
    expect(await screen.findByText("이름 유사도 — 사람 미확인")).toBeTruthy();
  });

  it("원가 확인 부분합 배지가 뜬다(M13)", async () => {
    renderPage();
    expect(await screen.findByText("원가 확인 97.0%분만")).toBeTruthy();
  });

  it("사다리 카드 자체가 사라지지 않는다(M18)", async () => {
    renderPage();
    expect(await screen.findByText("손익 사다리 (손익 화면과 같은 함수 산출)")).toBeTruthy();
  });

  it("cost_coverage가 null이면 배지도 «0.0%»가 아니라 «—»다(M6)", async () => {
    h.fetchChecks.mockResolvedValue({
      ...DEFAULT_CHECKS,
      ladder: { ...BASE_LADDER, cost_coverage: null },
    });
    renderPage();
    expect(await screen.findByText("원가 확인 —분만")).toBeTruthy();
    expect(screen.queryByText(/0\.0%분만/)).toBeNull();
  });

  it("blocked면 손익 없음을 보이고 정상 KV는 안 뜬다(M8)", async () => {
    h.fetchChecks.mockResolvedValue({
      ...DEFAULT_CHECKS,
      ladder: { ...BASE_LADDER, blocked: { code: "NO_COST", reason: "원가 확인 매출이 없습니다" } },
    });
    renderPage();
    expect(await screen.findByText("손익 없음")).toBeTruthy();
    expect(await screen.findByText("원가 확인 매출이 없습니다")).toBeTruthy();
    expect(screen.queryByText("우리 매출(납품가)")).toBeNull();
  });
});

describe("Rocket1PPnlAudit — 3단 원자 목록", () => {
  it("옵션 표가 잘리면 경고가 뜬다", async () => {
    h.fetchAtoms.mockResolvedValue({
      ...DEFAULT_ATOMS,
      option_count: 2_000_000, option_table_truncated: true,
    });
    renderPage();
    // ★A2 검사의 note도 같은 문구("옵션 표가 잘렸습니다")를 우연히 공유한다(백엔드가 같은
    //   사실을 두 곳에서 말해서다) — 배너 고유 문구("위 검사의 A2·A7은…")로 특정한다.
    expect(await screen.findByText(/위 검사의 A2·A7은 이 때문에/)).toBeTruthy();
  });

  it("옵션 표가 안 잘리면 잘림 경고가 안 뜬다", async () => {
    renderPage(); // DEFAULT_ATOMS.option_table_truncated === false
    await screen.findByText("상품 A");
    expect(screen.queryByText(/위 검사의 A2·A7은 이 때문에/)).toBeNull();
  });

  it("순이익 합에서 빠진 행이 있으면(금액 앎) 문장과 금액이 뜬다", async () => {
    h.fetchAtoms.mockResolvedValue({
      ...DEFAULT_ATOMS,
      totals: { ...DEFAULT_ATOMS.totals, net_profit_unknown: 22,
                revenue_out_of_net: "437110", revenue_out_of_net_unknown: 3 },
    });
    renderPage();
    expect(await screen.findByText(/이 합계는 순이익을 낼 수 있는 행만 더한 값입니다/)).toBeTruthy();
    expect(await screen.findByText(/437,110원/)).toBeTruthy();
    expect(await screen.findByText(/매출조차 모르는 행 3건/)).toBeTruthy();
  });

  it("★P1-3: 빠진 행이 있는데 금액을 모르면(revenue_out_of_net=null) 배너가 사라지지 않고 "
     + "«모른다»고 말한다", async () => {
    h.fetchAtoms.mockResolvedValue({
      ...DEFAULT_ATOMS,
      totals: { ...DEFAULT_ATOMS.totals, net_profit_unknown: 3,
                revenue_out_of_net: null, revenue_out_of_net_unknown: 3 },
    });
    renderPage();
    // «빠진 게 없다»와 «빠졌는데 금액도 모른다»가 같은 null이라 예전 판은 후자에서 배너가
    // 통째로 사라졌다(적대 리뷰 P1-3) — 행 수 게이트로 고쳤으니 배너는 뜨고, 금액 대신
    // "모른다"고 말해야 한다.
    expect(await screen.findByText(/이 합계는 순이익을 낼 수 있는 행만 더한 값입니다/)).toBeTruthy();
    expect(await screen.findByText(/빠진 행의 매출도 모릅니다/)).toBeTruthy();
  });

  it("빠진 행이 0건이면 배너 자체가 없다", async () => {
    renderPage(); // DEFAULT_ATOMS.totals.net_profit_unknown === 0
    await screen.findByText("상품 A");
    expect(screen.queryByText(/순이익을 낼 수 있는 행만 더한 값입니다/)).toBeNull();
  });

  it("blocked 창에서 원자 0건은 «필터 탓»이 아니라 손익 없음 이유를 말한다(M8/P2-2)", async () => {
    h.fetchChecks.mockResolvedValue({
      ...DEFAULT_CHECKS,
      ladder: { ...BASE_LADDER, blocked: { code: "NO_COST", reason: "원가 확인 매출이 없습니다" } },
    });
    h.fetchAtoms.mockResolvedValue({ ...DEFAULT_ATOMS, count: 0, total: 0, atoms: [] });
    renderPage();
    // "손익 없음"이 여러 카드(사다리+원자)에 뜨는 것 자체가 정상 — 두 번째 EmptyState가
    // «조건에 맞는 원자가 없습니다»(필터 탓 오독)로 안 빠졌는지만 본다.
    expect((await screen.findAllByText("손익 없음")).length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText("조건에 맞는 원자가 없습니다")).toBeNull();
  });

  it("확정 적자(net_profit_upper)는 «—»로 지워지지 않고 «≤ N원»으로 뜬다(M7)", async () => {
    h.fetchAtoms.mockResolvedValue({
      ...DEFAULT_ATOMS,
      atoms: [{
        date: "2026-08-05", option_id: "B", sku_id: "S2", product_name: "상품 B",
        qty: 5, consumer_revenue: "500000", our_revenue: null,
        unit_price: null, cost: null, unit_cost: null,
        ad_spend: "50000", promo_burden: null, net_profit: null,
        net_profit_upper: "-50000", cost_source: "no_cost" as const,
      }],
    });
    renderPage();
    expect(await screen.findByText(/≤ -50,000원/)).toBeTruthy();
  });

  it("흑자·적자 색상이 뒤바뀌지 않는다(M11)", async () => {
    // ★사다리(고정값: 매출 1,000,000·원가 300,000·광고 200,000·순이익 454,545)와 겹치지 않는
    //   금액을 골라야 findByText가 여러 개를 찾지 않는다.
    h.fetchAtoms.mockResolvedValue({
      ...DEFAULT_ATOMS,
      atoms: [
        { ...DEFAULT_ATOMS.atoms[0], date: "2026-08-04", option_id: "GOOD",
          our_revenue: "620000", net_profit: "88000" },
        { ...DEFAULT_ATOMS.atoms[0], date: "2026-08-05", option_id: "BAD",
          our_revenue: "100000", net_profit: "-15000" },
      ],
    });
    renderPage();
    const goodCell = await screen.findByText("88,000원");
    const badCell = await screen.findByText("-15,000원");
    expect(goodCell.className).toContain("text-judge-good");
    expect(badCell.className).toContain("text-judge-bad");
  });
});

describe("Rocket1PPnlAudit — 4단 원천 행 (다섯 갈래)", () => {
  it("행을 열면 다섯 블록이 실제로 그려진다(가 — 목이 pending에 갇혀 있지 않다)", async () => {
    renderPage();
    fireEvent.click(await screen.findByText("2026-08-04"));

    expect(await screen.findByText("① 판매행 (판매분석)")).toBeTruthy();
    expect(await screen.findByText("② 납품단가 (최근 발주)")).toBeTruthy();
    expect(await screen.findByText("③ 원가 (다리 → 등록원가)")).toBeTruthy();
    expect(await screen.findByText("④ 광고비 (옵션×일)")).toBeTruthy();
    expect(await screen.findByText("⑤ 분담금 (프로모션 제안서)")).toBeTruthy();
    // 내용도 실제로 그려졌는지(단순 pending Loading이 아니라) 몇 곳을 확인한다.
    expect(await screen.findByText("발주번호")).toBeTruthy();
    expect(await screen.findByText(/가장 최근 발주의 단가입니다/)).toBeTruthy();
  });

  it("원자 상세 요청에 화면이 보고 있는 창(from/to)이 실린다(M3 — 창 인자 누락)", async () => {
    renderPage();
    fireEvent.click(await screen.findByText("2026-08-04"));
    expect(h.fetchAtomDetail).toHaveBeenCalled();
    const call = h.fetchAtomDetail.mock.calls[0][0] as
      { date: string; optionId: string; from: string; to: string };
    expect(call.date).toBe("2026-08-04");
    expect(call.optionId).toBe("A");
    expect(call.from).toBe("2026-08-01");
    expect(call.to).toBe("2026-08-07");
  });

  it("★P1-1: 광고 행이 없으면 사실과 결과만 말하고 «0원이 맞다»고 단정하지 않는다", async () => {
    renderPage(); // DEFAULT_ATOM_DETAIL.ad === null
    fireEvent.click(await screen.findByText("2026-08-04"));
    expect(await screen.findByText(/그날 이 옵션에 광고 행이 없습니다/)).toBeTruthy();
    expect(await screen.findByText(/손익 계산에는 0원으로 들어갑니다/)).toBeTruthy();
    expect(await screen.findByText(/이 화면만으로 알 수 없습니다/)).toBeTruthy();
    // 예전 문장 — 원인을 단정하는 문구는 더 이상 없어야 한다.
    expect(screen.queryByText(/0원이 맞음/)).toBeNull();
  });

  it("A6이 pass가 아니면 «분담금 0은 사실»을 말하지 않는다(M10)", async () => {
    h.fetchChecks.mockResolvedValue({
      ...DEFAULT_CHECKS,
      checks: DEFAULT_CHECKS.checks.map((c) =>
        c.id === "A6" ? { ...c, verdict: "undetermined" as const } : c),
    });
    renderPage();
    fireEvent.click(await screen.findByText("2026-08-04"));
    expect(await screen.findByText(/이 창의 프로모션 수집 신선도는 위 A6 참조/)).toBeTruthy();
    expect(screen.queryByText("그날 걸린 프로모션 없음 — 분담금 0은 사실")).toBeNull();
  });

  it("promos:null(모름)은 «프로모션 없음»과 다르게 그린다(M14)", async () => {
    h.fetchAtomDetail.mockResolvedValue({ ...DEFAULT_ATOM_DETAIL, promos: null });
    renderPage();
    fireEvent.click(await screen.findByText("2026-08-04"));
    expect(await screen.findByText("원천 테이블 없음(모름)")).toBeTruthy();
    expect(screen.queryByText(/그날 걸린 프로모션 없음/)).toBeNull();
  });

  it("광고 노출 수가 null이면 «0»이 아니라 «—»다(M5)", async () => {
    h.fetchAtomDetail.mockResolvedValue({
      ...DEFAULT_ATOM_DETAIL,
      ad: { report_date: "2026-08-04", ad_option_id: "A", ad_spend: "10000",
            impressions: null, clicks: 5, orders: null, sales_qty: null, row_count: 1 },
    });
    renderPage();
    fireEvent.click(await screen.findByText("2026-08-04"));
    expect(await screen.findByText("—/5")).toBeTruthy();
  });
});

describe("Rocket1PPnlAudit — URL 공유(P2-1)", () => {
  it("필터·정렬을 바꾸면 URL에 실린다 — 공유하면 같은 화면이 재현된다", async () => {
    renderPage();
    await screen.findByText("상품 A");
    const [fltSelect, sortSelect] = screen.getAllByRole("combobox");
    fireEvent.change(fltSelect, { target: { value: "loss" } });
    fireEvent.change(sortSelect, { target: { value: "net" } });
    const loc = screen.getByTestId("location").textContent ?? "";
    expect(loc).toContain("flt=loss");
    expect(loc).toContain("sort=net");
  });

  it("기본값(전체/매출순)은 URL을 지저분하게 만들지 않는다", async () => {
    renderPage();
    await screen.findByText("상품 A");
    const loc = screen.getByTestId("location").textContent ?? "";
    expect(loc).not.toContain("flt=");
    expect(loc).not.toContain("sort=");
  });
});
