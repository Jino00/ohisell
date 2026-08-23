// @vitest-environment jsdom
//
// kpiEvidenceSurface.test.tsx — KPI 카드 근거가 «사람 눈에 닿는 마지막 한 칸»까지 가는가
// (계약 docs/contracts/CONTRACT_kpi_evidence_page.md §4 A1~A6·A8).
//
// 왜 DOM을 보나: 이 저장소가 반복해서 겪은 결함은 「값은 맞는데 사람이 그걸 못 본다」다 —
// 서비스층은 초록인데 `response_model`이 키를 지웠고(#321), 백엔드는 정확한데 렌더 한 줄이
// 없어 배너가 통째로 숨었다(rgNetAxisSurface). 근거 페이지는 **존재 자체가 표면**이라
// 단위 테스트로는 원리적으로 못 지킨다.
//
// 이 파일이 죽여야 하는 변이(계약 §4 A8 — 최종 산출물까지 가는 경로를 끊는 것):
//   M1 Dashboard의 KPI 카드를 `<Link>` → `<div>`로 되돌리기 (근거에 닿는 길이 사라진다)
//   M2 카드 href에서 조회 조건(date_from·rocket_basis) 떼기 (근거가 다른 창을 말한다)
//   M3 App.tsx에서 /kpi-evidence 라우트 삭제
//   M4 검산 ✓/✗ 배지 렌더 삭제 (안 맞는 날 아무도 모른다)
//   M5 «모른다»(null)를 0원으로 그리기 (「원가 0원」 거짓말)
//   M6 하한 경고·이익률 분모 차액·주문건수 제외 사유 문구 삭제 (자백이 화면 밖으로)
//   M7 「대시보드로 돌아가기」 링크에서 조건 떼기 (돌아가면 창이 리셋된다)
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

const h = vi.hoisted(() => ({ evidence: null as unknown }));

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  syncRealtime: () => Promise.resolve(),
  fetchApi: (url: string) => {
    // App 전체를 렌더하는 M3 테스트에선 Layout·SchedulerStatus도 함께 마운트된다 —
    // 이 화면들이 기대하는 «모양»을 주지 않으면 이 파일과 무관한 곳에서 터진다.
    if (url.includes("/api/scheduler/status")) return Promise.resolve({ jobs: [] });
    if (url.includes("/api/dashboard/kpi?")) {
      return Promise.resolve({
        total_revenue: "1670990", net_profit: "-285507", profit_rate: "-18.7",
        order_count: 84, revenue_change_pct: -44.1, profit_change_pct: 0,
        net_scope: "partial", net_floor_ad: "570405",
      });
    }
    return Promise.resolve([]);
  },
  fetchKpiEvidence: () => Promise.resolve(h.evidence),
}));

import Dashboard from "./Dashboard";
import KpiEvidencePage from "./KpiEvidence";
import App from "../App";

afterEach(cleanup);

const DEDUCTIONS = ["cost", "commission", "ad_spend", "shipping", "fixed_cost", "payable_vat"];

function evidence(over: Record<string, unknown> = {}) {
  return {
    date_from: "2026-08-22",
    date_to: "2026-08-22",
    rocket_basis: "settlement",
    deduction_keys: DEDUCTIONS,
    deduction_totals: {
      cost: "812400", commission: "241330", ad_spend: "570405",
      shipping: "30000", fixed_cost: "88120", payable_vat: "214242",
    },
    deduction_unknown_rows: { cost: 0, commission: 0, ad_spend: 0, shipping: 1, fixed_cost: 1, payable_vat: 1 },
    rows: [
      {
        channel_id: 6, channel_name: "네이버", company: "오하이",
        label: "오하이 · 네이버 스마트스토어",
        revenue: "1100990", product_revenue: "1050990", shipping_revenue: "50000",
        deductions: {
          cost: "812400", commission: "241330", ad_spend: "12517",
          shipping: "30000", fixed_cost: "88120", payable_vat: "214242",
        },
        missing: [], net_profit: "-297619", net_scope: "full", net_floor_ad: "0",
        net_basis_revenue: "1100990", unmapped_revenue: "0",
        residual: "0.00", explains_net: true, order_count: 84,
        counted_in_order_card: true, revenue_basis: null,
      },
      {
        channel_id: 5, channel_name: "쿠팡 로켓배송", company: "오하이테크",
        label: "오하이테크 · 쿠팡 로켓배송(1P)",
        revenue: "570000", product_revenue: "570000", shipping_revenue: "0",
        // ★배송비·고정비·부가세를 **원래 안 싣는 행** — null이지 0이 아니다.
        deductions: {
          cost: null, commission: "0", ad_spend: "557888",
          shipping: null, fixed_cost: null, payable_vat: null,
        },
        missing: ["cost", "shipping", "fixed_cost", "payable_vat"],
        net_profit: "-557888", net_scope: "ad_only", net_floor_ad: "557888",
        net_basis_revenue: "0", unmapped_revenue: "570000",
        residual: "570000.00", explains_net: false, order_count: 31,
        counted_in_order_card: false, revenue_basis: "settlement",
      },
    ],
    totals: {
      revenue: "1670990", net_profit: "-285507", basis_revenue: "1100990",
      floor_ad: "570405", profit_rate: "-18.70", order_count: 84,
      residual: "0.00", unmeasured_revenue: "570000",
    },
    checks: {
      revenue_matches: true, net_matches: true,
      order_count_matches: true, net_fully_explained: true,
    },
    order_count_excluded: 31,
    has_floor: true,
    ...over,
  };
}

function renderEvidence(metric: string, extra = "") {
  return render(
    <MemoryRouter
      initialEntries={[
        `/kpi-evidence?metric=${metric}&date_from=2026-08-22&date_to=2026-08-22` +
          `&rocket_basis=settlement${extra}`,
      ]}
    >
      <Routes>
        <Route path="/kpi-evidence" element={<KpiEvidencePage />} />
      </Routes>
    </MemoryRouter>,
  );
}

// ════════ A1~A4 · M1·M2 — 카드에서 근거로 «가는 길»이 실제로 있는가 ════════
describe("대시보드 KPI 카드 → 근거 페이지", () => {
  it("네 칸이 전부 근거 페이지 링크이고, 지금 화면의 조회 조건을 싣고 간다", async () => {
    render(<Dashboard />, {
      wrapper: ({ children }) => (
        <MemoryRouter initialEntries={["/?date_from=2026-08-01&date_to=2026-08-22&rocket_basis=sales"]}>
          {children}
        </MemoryRouter>
      ),
    });
    await waitFor(() => expect(screen.getByText("총 매출")).toBeTruthy());

    for (const [metric, label] of [
      ["revenue", "총 매출"],
      ["net_profit", "순이익"],
      ["profit_rate", "이익률"],
      ["order_count", "주문 건수"],
    ] as const) {
      const link = screen.getByLabelText(`${label} 근거 보기`);
      expect(link.tagName, `${label} 카드가 링크가 아니다 — 근거에 닿을 길이 없다`).toBe("A");
      const href = link.getAttribute("href") ?? "";
      expect(href).toContain(`/kpi-evidence?metric=${metric}`);
      // M2: 조건이 빠지면 근거가 카드와 «다른 창»을 말한다
      expect(href).toContain("date_from=2026-08-01");
      expect(href).toContain("date_to=2026-08-22");
      expect(href).toContain("rocket_basis=sales");
    }
  });

  it("M3 — /kpi-evidence 라우트가 실제로 등록돼 있다", async () => {
    h.evidence = evidence();
    window.history.pushState({}, "", "/kpi-evidence?metric=revenue&date_from=2026-08-22&date_to=2026-08-22");
    render(<App />);
    await waitFor(() => expect(screen.getByText("총 매출 — 근거")).toBeTruthy());
    window.history.pushState({}, "", "/");
  });
});

// ════════ A5 · M7 — 돌아오면 조회 조건이 살아 있는가 ════════
describe("근거 → 대시보드 되돌아오기", () => {
  it("「돌아가기」 링크가 조회 기간·로켓 축을 그대로 싣는다", async () => {
    h.evidence = evidence();
    renderEvidence("revenue");
    const back = await screen.findByText("← 대시보드로 돌아가기");
    const href = back.getAttribute("href") ?? "";
    expect(href).toContain("date_from=2026-08-22");
    expect(href).toContain("date_to=2026-08-22");
    expect(href).toContain("rocket_basis=settlement");
  });

  it("대시보드가 URL의 조건을 초기값으로 읽는다 — 안 읽으면 돌아온 화면이 어제로 리셋된다", async () => {
    render(<Dashboard />, {
      wrapper: ({ children }) => (
        <MemoryRouter initialEntries={["/?date_from=2026-07-01&date_to=2026-07-31&rocket_basis=sales"]}>
          {children}
        </MemoryRouter>
      ),
    });
    await waitFor(() => expect(screen.getByText("총 매출")).toBeTruthy());
    const dates = document.querySelectorAll('input[type="date"]');
    expect((dates[0] as HTMLInputElement).value).toBe("2026-07-01");
    expect((dates[1] as HTMLInputElement).value).toBe("2026-07-31");
  });
});

// ════════ A2 · M4·M6 — 순이익 검산식과 하한 자백 ════════
describe("순이익 근거", () => {
  it("검산식·합계·「카드와 일치」 ✓가 화면에 뜬다", async () => {
    h.evidence = evidence();
    renderEvidence("net_profit");
    await waitFor(() => expect(screen.getByText("순이익 — 근거")).toBeTruthy());

    expect(screen.getByText("− 원가")).toBeTruthy();
    expect(screen.getByText("− 납부 부가세")).toBeTruthy();
    expect(screen.getByText("= 순이익")).toBeTruthy();
    // M4: 검산 배지가 사라지면 안 맞는 날 아무도 모른다
    expect(screen.getByText(/✓ 카드와 일치/)).toBeTruthy();
    expect(screen.getAllByText("-285,507원").length).toBeGreaterThan(0);
  });

  it("M4 — 합계가 카드와 어긋나면 화면이 ✗를 찍는다", async () => {
    h.evidence = evidence({
      checks: { revenue_matches: true, net_matches: false, order_count_matches: true, net_fully_explained: true },
    });
    renderEvidence("net_profit");
    await waitFor(() => expect(screen.getByText(/✗ 카드와 불일치/)).toBeTruthy());
  });

  it("M6 — 하한이 섞였으면 그 사실과 금액을 화면이 말한다", async () => {
    h.evidence = evidence();
    renderEvidence("net_profit");
    const warn = await screen.findByText(/이 순이익은 하한입니다/);
    expect(warn.parentElement?.textContent).toContain("570,405원");
  });

  it("하한이 없으면 그 경고를 띄우지 않는다", async () => {
    h.evidence = evidence({ has_floor: false });
    renderEvidence("net_profit");
    await waitFor(() => expect(screen.getByText("순이익 — 근거")).toBeTruthy());
    expect(screen.queryByText(/이 순이익은 하한입니다/)).toBeNull();
  });
});

// ════════ A6 · M5 — «모른다»를 0으로 그리지 않는가 ════════
describe("모르는 항목", () => {
  it("null 항목은 «—»로 뜬다 (0원으로 그리면 「원가 0원」 거짓말이 된다)", async () => {
    h.evidence = evidence();
    renderEvidence("net_profit");
    await waitFor(() => expect(screen.getByText("오하이테크 · 쿠팡 로켓배송(1P)")).toBeTruthy());

    const rocketRow = screen.getByText("오하이테크 · 쿠팡 로켓배송(1P)").closest("tr")!;
    const cells = within(rocketRow).getAllByText("—");
    // cost·shipping·fixed_cost·payable_vat 넷이 «모른다»다
    expect(cells.length).toBeGreaterThanOrEqual(4);
    expect(rocketRow.textContent).not.toContain("0원원");
    // 각주도 그 뜻을 말해야 한다
    expect(screen.getByText(/"—"는 0원이 아니라/)).toBeTruthy();
  });

  it("잔차가 남은 행은 그 금액을 숫자로 보인다 — 설명 못 한 돈에 이름을 붙인다", async () => {
    h.evidence = evidence();
    renderEvidence("net_profit");
    await waitFor(() => expect(screen.getByText(/잔차 570,000원/)).toBeTruthy());
  });
});

// ════════ A3 · M6 — 이익률 분모 ════════
describe("이익률 근거", () => {
  it("분자·분모가 둘 다 숫자로 뜨고, 총매출과 다른 이유를 화면이 말한다", async () => {
    h.evidence = evidence();
    renderEvidence("profit_rate");
    await waitFor(() => expect(screen.getByText("이익률 — 근거")).toBeTruthy());

    expect(screen.getByText(/분자 · 순이익/).textContent).toContain("-285,507원");
    expect(screen.getByText(/분모 · 손익을 잰 매출/).textContent).toContain("1,100,990원");
    const note = screen.getByText(/분모는 총 매출이 아닙니다/);
    expect(note.parentElement?.textContent).toContain("570,000원");
    expect(note.parentElement?.textContent).toContain("원가를 몰라 손익을 못 잰 매출");
  });

  it("분모와 총매출이 같으면 그 경고를 띄우지 않는다", async () => {
    h.evidence = evidence({
      totals: { ...evidence().totals, basis_revenue: "1670990", unmeasured_revenue: "0" },
    });
    renderEvidence("profit_rate");
    await waitFor(() => expect(screen.getByText("이익률 — 근거")).toBeTruthy());
    expect(screen.queryByText(/분모는 총 매출이 아닙니다/)).toBeNull();
  });
});

// ════════ A4 · M6 — 주문 건수에서 빠진 것 ════════
describe("주문 건수 근거", () => {
  it("채널별 건수와 합계가 뜨고, 로켓1P가 빠진 «이유»를 화면이 말한다", async () => {
    h.evidence = evidence();
    renderEvidence("order_count");
    await waitFor(() => expect(screen.getByText("주문 건수 — 근거")).toBeTruthy());

    expect(screen.getByText("오하이 · 네이버 스마트스토어")).toBeTruthy();
    expect(screen.getAllByText("84건").length).toBeGreaterThan(0);
    const note = screen.getByText(/로켓배송 1P는 이 카드에서 빠집니다/);
    expect(note.parentElement?.textContent).toContain("주문이라는 개념이 없고");
    expect(note.parentElement?.textContent).toContain("판매수량");
    expect(note.parentElement?.textContent).toContain("31");
  });
});

// ════════ 조용한 실패 금지 ════════
describe("근거를 못 불러왔을 때", () => {
  it("빈 화면 대신 실패 사유를 말한다", async () => {
    // fetchKpiEvidence가 «거부»되도록 값 자체를 rejected promise로 둔다.
    // (미리 .catch를 붙여 두지 않으면 vitest가 unhandled rejection으로 잡는다)
    const rejected: Promise<never> = Promise.reject(new Error("boom"));
    rejected.catch(() => {});
    h.evidence = rejected;
    renderEvidence("revenue");
    await waitFor(() => expect(screen.getByText(/근거를 불러오지 못했습니다/)).toBeTruthy());
  });
});
