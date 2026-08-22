// @vitest-environment jsdom
//
// rgSettlementAxisSurface.test.tsx — RG 정산공제 자백(`lib/rgSettlementAxis.ts`)이 «사람 눈에
// 닿는 마지막 한 칸»에서 실제로 그려지는가 (계약 CONTRACT_rg_sales_date_axis §4 ⓑⓒⓓⓔ).
//
// 왜 이 파일이 따로 있나: 이 저장소가 직전에 겪은 사고(rgNetAxisSurface.test.tsx 머리말)가
// 그대로 반복될 수 있는 자리다 — 백엔드가 축·요율·커버리지·장부대조 값을 dict에 실어도,
// `Dashboard.tsx`/`CommandCenter.tsx`가 그 값을 렌더하는 한 줄이 지워지면 순수 함수 테스트
// (rgSettlementAxis.test.ts)는 전부 초록인 채로 화면만 조용해진다. 그래서 여기서는 순수
// 함수의 반환값이 아니라 **DOM 문자열**을 검사한다. 이 파일이 죽여야 하는 변이:
//   §8 Dashboard.tsx의 `feeNote &&` 렌더 블록 삭제
//   §9 CommandCenter.tsx의 `Card`가 받는 `note &&` 렌더 블록 삭제
// 두 변이 모두 실제로 지워 보고 이 테스트가 깨지는 것을 확인한 뒤 되돌렸다(경계: 테스트 파일
// 외 파일은 영구 수정하지 않는다) — 결과는 파일 하단 주석 참조.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";

// Dashboard(fetchApi 기반)와 CommandCenter(fetchCommandCenter 등)가 같은 "../lib/api" 모듈을
// 쓰므로 팩토리 하나에 둘 다 싣는다 — vi.mock은 모듈 경로당 한 번만 적용된다.
const h = vi.hoisted(() => ({
  channels: [] as Record<string, unknown>[],   // Dashboard: /api/dashboard/channel-breakdown 응답
  overview: null as unknown,                    // CommandCenter: fetchCommandCenter 응답
}));

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  syncRealtime: () => Promise.resolve(),
  fetchApi: (url: string) => {
    if (url.includes("/api/dashboard/kpi")) {
      return Promise.resolve({
        total_revenue: 0, net_profit: 0, profit_rate: 0, order_count: 0,
        revenue_change_pct: 0, profit_change_pct: 0,
      });
    }
    if (url.includes("/api/dashboard/channel-breakdown")) {
      return Promise.resolve(h.channels);
    }
    // trend, trend-by-channel, product-ranking — 전부 빈 배열이면 각 섹션이
    // "데이터가 없습니다"로 떨어져 recharts(ResponsiveContainer)를 실제로 마운트하지
    // 않는다(jsdom엔 ResizeObserver가 없다 — 차트 마운트는 이 테스트의 관심사가 아니다).
    return Promise.resolve([]);
  },
  fetchCommandCenter: () => Promise.resolve(h.overview),
  // 검산 보조 지표는 fail-soft다(doFetch가 .catch로 삼킨다) — 이 테스트는 순이익 카드만 본다.
  fetchRevenueReconcile: () => Promise.reject(new Error("no reconcile in test")),
  fetchRocketOverview: () => Promise.reject(new Error("no rocket in test")),
}));

import Dashboard from "./Dashboard";
import CommandCenter from "./CommandCenter";
import type { OverviewResponse } from "../lib/api";

afterEach(() => cleanup());

// ════════════════════════════ Dashboard — 채널 요약표 RG 자백 칸 (§8) ════════════════════════════
const RG_ROW = {
  kind: "leaf",
  company: "오하이테크",
  label: "오하이테크 · 로켓그로스",
  revenue: 0,           // 0 유지 — leafPie(revenue>0 필터)에 안 들어가 PieChart를 안 그린다
  product_revenue: 0,
  shipping_revenue: 0,
  ad_spend: 0,
  net_profit: 0,
  profit_rate: null,    // null 유지 — BarChart 필터(profit_rate != null)에서 빠진다
  order_count: 0,
  // ── RG 정산공제 자백 필드 (D-CPP-49 후속, 계약 §4) ──
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
};

describe("Dashboard 채널 요약표 — RG 정산공제 자백 칸", () => {
  it("판매일 축·요율·커버리지·장부대조 문구가 실제 DOM에 나타난다", async () => {
    h.channels = [RG_ROW];
    render(<Dashboard />);
    await waitFor(() => expect(screen.getByText(/판매일 축/)).toBeTruthy());
    // 한 <div>의 텍스트 노드 하나에 " · "로 이어 붙는다(rgSettlementAxis.ts의 rgFeeNote).
    const note = screen.getByText((content) => content.includes("판매일 축") && content.includes("요율"));
    expect(note.textContent).toContain("10.50%");
    expect(note.textContent).toContain("비용 커버리지 90.0%");
    expect(note.textContent).toContain("장부대조 −500원");
  });

  it("RG가 아닌 행(축·basis 둘 다 없음)에는 이 칸이 안 뜬다", async () => {
    h.channels = [{
      ...RG_ROW, commission_axis: undefined, commission_basis: undefined,
      label: "자사몰",
    }];
    render(<Dashboard />);
    await waitFor(() => expect(screen.getByText("자사몰")).toBeTruthy());
    expect(screen.queryByText(/요율.*실측/)).toBeNull();
  });
});

// ════════════════════════════ CommandCenter — 순이익 카드 (§9) ════════════════════════════
function makeOverview(axis: "sales_date" | "recognition_date"): OverviewResponse {
  return {
    period: { from: "2026-07-14", to: "2026-07-20" },
    account: {
      summary: {
        revenue: "5000000", return_deduction: "0", service_fee: "0", service_fee_vat: "0",
        total_fee: "100000", ad_spend: "50000", cost: "2000000", net_profit: "1500000",
        cost_covered_options: 10, option_count: 10,
        fee_rate_known_options: 0, fee_rate_default_options: 0,   // FeeBasisCard를 null로(관심사 밖)
        revenue_3p: "3000000", revenue_rg: "2000000", revenue_rg_basis: "console_net",
        rg_option_axis_days: "16/16", rg_option_axis_complete: true, rg_open_days: 0,
        rg_settlement_total: "300000", rg_settlement_deducted: "250000", rg_non_ad_deducted: "250000",
        rg_flip_status: "applied_ex_ad",
        ad_nonpa_deducted: "0",
        seller_shipping_3p: "0", shipping_income_3p: "0", payable_vat: "0",
        // ── 이 트랙의 대상 필드 ──
        rg_settlement_axis: axis,
        rg_fee_basis: "settled_rate",
        rg_fee_rate: 0.105,           // 비율(0~1) — rgFeeFactsFromSummary가 ×100 해야 10.50%
        rg_fee_coverage: 0.9,
        rg_fee_unmapped_revenue: 1000,
        rg_fee_reconcile: {
          cycle_from: "07-14", cycle_to: "07-20",
          computed: "250000", actual: "250500", diff: "-500", diff_pct: "-0.05",
        },
      },
      by_option: [],
    },
    ad: { summary: { ad_spend: "50000", impressions: 0, clicks: 0, conv_revenue: "0", roas: null, ad_confirmed_applies: true }, by_option: [] },
    product: { summary: {}, by_option: [] },
  } as unknown as OverviewResponse;
}

describe("CommandCenter 순이익 카드 — rg_settlement_axis 분기", () => {
  it("sales_date면 「판매일 축」이 뜬다 — sub 문구와 note 문구 둘 다에서", async () => {
    h.overview = makeOverview("sales_date");
    render(<CommandCenter />);
    await waitFor(() => expect(screen.getByText(/RG정산/)).toBeTruthy());
    // sub("RG정산 −…(광고 제외, 판매일 축)")와 note(rgFeeNote 첫 파트) 둘 다에 뜬다 — 2곳.
    expect(screen.getAllByText(/판매일 축/).length).toBeGreaterThanOrEqual(2);
    // feeNote(rgFeeNote) 전용 문구 — sub엔 없는, note에만 있는 표식으로 note 렌더를 특정한다.
    const note = screen.getByText((c) => c.includes("판매일 축") && c.includes("요율"));
    expect(note.textContent).toContain("10.50%");
  });

  it("recognition_date면 「정산 인식일 축」이 뜨고 「판매일 축」은 안 뜬다", async () => {
    h.overview = makeOverview("recognition_date");
    render(<CommandCenter />);
    await waitFor(() => expect(screen.getByText(/RG정산/)).toBeTruthy());
    expect(screen.getAllByText(/정산 인식일 축/).length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText(/판매일 축/)).toBeNull();
  });

  // M5(★표면 변이, 위임 세션 2026-08-23): CommandCenter.tsx 순이익 카드 sub의
  //   `−${won(s.rg_settlement_deducted ...)}` 금액 부분만 지우고 라벨·축 문구는 남기는 변이.
  //   위 두 테스트는 "판매일 축"/"정산 인식일 축" 문구의 «개수»만 세므로(sub·note 두 곳) 이
  //   변이엔 안 죽는다 — sub의 라벨이 살아 있으면 개수는 그대로 2다. 금액 «숫자» 자체가
  //   DOM에 실제로 찍히는지를 봐야 이 변이를 잡는다.
  it("sub에 실제 차감 금액이 «숫자로» 찍힌다 — 라벨만 남고 금액이 지워지면 안 된다", async () => {
    h.overview = makeOverview("sales_date");
    render(<CommandCenter />);
    await waitFor(() => expect(screen.getByText(/RG정산/)).toBeTruthy());
    // sub 문구는 "RG정산 −250,000원(광고 제외, 판매일 축)"로 시작한다(note는 "판매일 축"으로
    // 시작해 겹치지 않는다) — 이 접두어로 sub 텍스트 노드를 특정한다.
    const sub = screen.getByText((content) => content.startsWith("RG정산"));
    expect(sub.textContent).toContain("250,000원");
  });
});

// ════════════════════════════ 변이 주입 결과 (실행 확인, 원복 완료·소스 영구 변경 없음) ════════════════════════════
// §8 Dashboard.tsx:821-825 `{feeNote && (...)}` 블록을 지우고 재실행
//   → "판매일 축·요율·커버리지·장부대조 문구가 실제 DOM에 나타난다" 테스트가 실패로 죽었다
//     (나머지 3개는 살아남음 — 이 변이가 노리는 자리를 정확히 잡았다는 뜻). 원복 후 재실행, 4/4 다시 초록.
// §9 CommandCenter.tsx:432-436 `Card`의 `{note && (...)}` 블록을 지우고 재실행
//   → 두 CommandCenter 테스트가 **둘 다** 실패로 죽었다. sales_date 테스트는 `getAllByText(/판매일 축/)
//     .length >= 2` 단언(sub·note 두 곳에서 나와야 함)이 1곳(=sub만)으로 줄어 실패했고, note 전용
//     문구("10.50%") 단언도 요소를 못 찾아 실패했다. recognition_date 테스트도 같은 이유로
//     `getAllByText(/정산 인식일 축/).length >= 2`가 1로 줄어 실패했다. 즉 note가 없어도 sub 쪽
//     문구는 남아 화면이 완전히 침묵하지는 않지만, **자백의 절반(판정 근거·요율·커버리지·장부대조)**은
//     사라지고 이 테스트가 그것을 정확히 잡는다. 원복 후 재실행, 4/4 다시 초록.
