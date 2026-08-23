// @vitest-environment jsdom
//
// rgSettlementCardReuse.test.tsx — 계약 `docs/contracts/CONTRACT_2p_own_screens.md`(D-CPP-54)
// **§1-A-3 미이행분**의 표면 테스트 + 화면 A 「조용한 행 접기」(Jino 발의, 2026-08-23 n=7).
//
// 계약 §1-A-3 원문: *"기존 `RgSettlementCard`(CommandCenter) 컴포넌트를 재사용해 주기별 정산
// 내역을 함께 싣는다."* n=6은 화면 B에 자체 요약 카드만 두고 이 항목을 이행하지 않았다 —
// 완료 QA가 「미이행 1건」으로 남긴 그 자리다.
//
// ★왜 DOM을 보나: 이 사슬이 반복해 밟은 병이 「백엔드는 값을 내는데 사람에게 닿는 마지막 한
//   칸에서 사라진다」이고, `RgSettlementCard`는 데이터가 없으면 **`null`을 그린다** — 즉
//   «못 불러옴»과 «내역이 없음»이 화면에서 **같은 얼굴(빈 자리)**이 되는 구조를 타고났다.
//   그래서 렌더 여부만이 아니라 **실패했을 때 화면이 무슨 말을 하는지**까지 단언한다.
//
// 이 파일이 죽여야 하는 변이(주입 결과는 파일 하단):
//   ⓐ RocketGrowthSettlement.tsx — `<RgSettlementCard data={overview} />` 호출 삭제
//   ⓑ RocketGrowthSettlement.tsx — `ovErr` 분기(못 불러왔다 자백) 삭제 → 조용히 빈 자리
//   ⓒ RocketGrowthSettlement.tsx — `rg_settlement == null` 분기(«0원이 아니라 없음») 삭제
//   ⓓ CommandCenter.tsx        — `<RgSettlementCard .../>` 호출 삭제(원자리 후퇴 = 계약 §3 위반)
//   ⓔ components/RgSettlementCard.tsx — `onRefresh ? (...) : null` → 항상 버튼(읽기 전용 파괴)
//   ⓕ RocketGrowthPnl.tsx      — `visibleRows` → `rows`(접기 무력화)
//   ⓖ RocketGrowthPnl.tsx      — 접힌 개수 자백 행 삭제 → 숨긴 것이 «없는» 것이 된다
//   ⓗ RocketGrowthPnl.tsx      — `isQuiet`에서 `n(r.revenue) === 0` 조건 제거 → 반품 행까지 접힘
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => ({
  overview: null as unknown,
  overviewFails: false,
  optionPnl: null as unknown,
}));

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  syncRealtime: () => Promise.resolve(),
  fetchApi: () => Promise.resolve([]),
  fetchCommandCenter: () =>
    h.overviewFails ? Promise.reject(new Error("백엔드 500")) : Promise.resolve(h.overview),
  fetchRgOptionPnl: () => Promise.resolve(h.optionPnl),
  fetchRevenueReconcile: () => Promise.reject(new Error("no reconcile in test")),
  fetchRocketOverview: () => Promise.reject(new Error("no rocket in test")),
}));

import CommandCenter from "./CommandCenter";
import RocketGrowthPnl from "./RocketGrowthPnl";
import RocketGrowthSettlement from "./RocketGrowthSettlement";
import type { OverviewResponse, RgOptionPnlResponse } from "../lib/api";

afterEach(() => {
  cleanup();
  h.overviewFails = false;
});

/** 정산 내역이 실린 overview. 금액은 다른 픽스처와 겹치지 않는 값으로 골랐다(오탐 방지). */
function makeOverview(withRg = true): OverviewResponse {
  return {
    period: { from: "2026-08-21", to: "2026-08-21" },
    account: {
      summary: {
        revenue: "5000000", return_deduction: "0", service_fee: "0", service_fee_vat: "0",
        total_fee: "100000", ad_spend: "50000", cost: "2000000", net_profit: "1500000",
        cost_covered_options: 10, option_count: 10,
        fee_rate_known_options: 0, fee_rate_default_options: 0,
        revenue_3p: "3000000", revenue_rg: "2000000",
        rg_settlement_total: "300000", rg_settlement_deducted: "250000",
        rg_flip_status: "applied_ex_ad", ad_nonpa_deducted: "0",
        seller_shipping_3p: "0", shipping_income_3p: "0", payable_vat: "0",
      },
      by_option: [],
    },
    ad: {
      summary: {
        ad_spend: "50000", impressions: 0, clicks: 0, conv_revenue: "0",
        roas: null, ad_confirmed_applies: true,
      },
      by_option: [],
    },
    product: { summary: {}, by_option: [] },
    ...(withRg
      ? {
          rg_settlement: {
            summary: {
              total: "888888", has_data: true, note: "",
              deducted: "777777", axis: "recognition_date", ad_settlement: "111111",
            },
            by_account: [
              {
                account_key: "COUPANG_WING1",
                total: "888888", sale_fee: "222222", fulfillment: "333333",
                delivery: "0", warehousing: "0", storage: "0",
                return_fee: "444444", ad_sales: "111111", other: "0",
              },
            ],
          },
        }
      : {}),
  } as unknown as OverviewResponse;
}

const OPTION_PNL_BASE: RgOptionPnlResponse = {
  account: "COUPANG_WING1",
  date_from: "2026-08-21",
  date_to: "2026-08-21",
  options: [],
  account_common: {
    period_fees: "1000", payable_vat: "2000", revenue_axis_gap: "0",
    ad_unallocated: "0", ad_unallocated_options: 0, fee_axis_fallback_gap: "0",
    cost_unmapped_revenue: "0", fee_unmapped_revenue: "0",
  },
  conservation: {
    options_net_sum: "60000", account_common_sum: "-3000",
    computed_total_net: "57000", reference_net: "57000", diff: "0", ok: true,
  },
  commission_axis: "sales_date",
  rate: "0.105",
  rate_basis: "settled_rate",
  rate_cycles: "07-14~07-20",
  fee_coverage: "0.9",
  cost_coverage: "0.95",
  option_axis_days: "1/1",
  option_axis_complete: true,
  cost_trustworthy: true,
  fee_trustworthy: true,
  reconciliation: null,
  ad_spend_warning: null,
};

/** 옵션 행 하나. 기본은 «조용한 행»(판매 0 · 매출 0 · 광고 0)이다. */
function opt(over: Partial<RgOptionPnlResponse["options"][number]>) {
  return {
    vendor_item_id: "V-000", name: "이름", revenue: "0", units_sold: 0, order_count: 0,
    fee_logistics: "0", fee_sale_fee: "0", fee_total: "0", cost: "0", has_cost: true,
    ad_spend: "0", net_profit: "0",
    ...over,
  } as RgOptionPnlResponse["options"][number];
}

const renderSettlement = () =>
  render(
    <MemoryRouter>
      <RocketGrowthSettlement />
    </MemoryRouter>,
  );

const renderPnl = () =>
  render(
    <MemoryRouter>
      <RocketGrowthPnl />
    </MemoryRouter>,
  );

// ══════════════ 화면 B — 계약 §1-A-3: 같은 컴포넌트를 재사용한다 (변이 ⓐⓑⓒⓔ) ══════════════

describe("RocketGrowthSettlement — 주기별 정산 내역 카드 재사용 (§1-A-3)", () => {
  it("계정별 정산 내역이 화면 B의 DOM에 실제로 뜬다 — 변이ⓐ", async () => {
    h.overview = makeOverview();
    h.optionPnl = OPTION_PNL_BASE;
    renderSettlement();
    await waitFor(() => expect(screen.getByText("주기별 정산 내역 (쿠팡 원장)")).toBeTruthy());
    // 카드 본체 — 종합 조망의 그것과 «같은 컴포넌트»가 내는 문자열이다.
    expect(screen.getByText(/RG 정산 비용 — 순이익 반영됨/)).toBeTruthy();
    expect(screen.getByText("222,222원")).toBeTruthy(); // sale_fee
    expect(screen.getByText("333,333원")).toBeTruthy(); // fulfillment
    expect(screen.getByText("444,444원")).toBeTruthy(); // return_fee
    expect(screen.getByText("COUPANG_WING1")).toBeTruthy();
  });

  it("갱신 버튼은 «없다» — 두 화면이 같은 데몬을 경쟁적으로 깨우지 않게 (변이ⓔ)", async () => {
    h.overview = makeOverview();
    h.optionPnl = OPTION_PNL_BASE;
    renderSettlement();
    await waitFor(() => expect(screen.getByText(/RG 정산 비용 — 순이익 반영됨/)).toBeTruthy());
    // ★있어야 할 것(카드)과 없어야 할 것(버튼)을 함께 단언한다 — 부정 단언만 두면 카드가
    //   통째로 사라져도 조용히 참이 된다(직전 계약 3R P2가 잡은 공허 단언의 모양).
    expect(screen.queryByRole("button", { name: /RG 정산 갱신/ })).toBeNull();
  });

  it("조회가 실패하면 «못 불러왔다»고 말한다 — 빈 자리로 두지 않는다 (변이ⓑ)", async () => {
    h.overviewFails = true;
    h.optionPnl = OPTION_PNL_BASE;
    renderSettlement();
    await waitFor(() => expect(screen.getByText(/정산 내역을 못 불러왔다/)).toBeTruthy());
    expect(screen.getByText(/못 잰 것이지 「내역이 없다」가 아니다/)).toBeTruthy();
    // 그리고 카드는 «안» 떠야 한다 — 실패했는데 옛 값이 남아 있으면 그게 더 나쁘다.
    expect(screen.queryByText(/RG 정산 비용 — 순이익 반영됨/)).toBeNull();
    // 나머지 화면(요율 출처·커버리지)은 살아 있다 — 한쪽 실패가 화면 전체를 죽이지 않는다.
    expect(screen.getByText("판매수수료 요율의 출처")).toBeTruthy();
  });

  it("응답에 rg_settlement가 없으면 «0원이 아니라 없음»이라고 말한다 (변이ⓒ)", async () => {
    h.overview = makeOverview(false);
    h.optionPnl = OPTION_PNL_BASE;
    renderSettlement();
    await waitFor(() => expect(screen.getByText(/0원이 아니라 «없음»/)).toBeTruthy());
    expect(screen.queryByText(/RG 정산 비용 — 순이익 반영됨/)).toBeNull();
    // 「0원」을 단정하는 옛 결함의 서명이 없어야 한다.
    expect(screen.queryByText("0원")).toBeNull();
  });
});

// ══════════════ 종합 조망 — 원자리 후퇴 없음 (계약 §3 · 합격 ⓖ) (변이 ⓓ) ══════════════

describe("CommandCenter — RgSettlementCard 원자리가 그대로다 (계약 §3)", () => {
  it("같은 카드가 종합 조망에서도 뜨고, 여기서는 갱신 버튼이 «있다» — 변이ⓓ", async () => {
    h.overview = makeOverview();
    render(<CommandCenter />);
    await waitFor(() => expect(screen.getByText(/RG 정산 비용 — 순이익 반영됨/)).toBeTruthy());
    expect(screen.getByText("222,222원")).toBeTruthy();
    // ★정의를 공용 모듈로 옮긴 것이 «이사»가 아니라 «재사용»임을 이 단언이 지킨다:
    //   원자리는 갱신 버튼까지 종전 그대로여야 한다.
    expect(screen.getByRole("button", { name: /RG 정산 갱신/ })).toBeTruthy();
  });
});

// ══════════════ 화면 A — 조용한 행 접기 (Jino 발의) (변이 ⓕⓖⓗ) ══════════════

const QUIET_FIXTURE: RgOptionPnlResponse = {
  ...OPTION_PNL_BASE,
  options: [
    opt({ vendor_item_id: "V-ACTIVE", name: "팔린 상품", revenue: "100000", units_sold: 10, ad_spend: "2000", net_profit: "60000" }),
    opt({ vendor_item_id: "V-QUIET-1", name: "조용한 상품 1" }),
    opt({ vendor_item_id: "V-QUIET-2", name: "조용한 상품 2" }),
  ],
};

function foldRow(): HTMLElement {
  const btn = screen.getByRole("button", { name: /펼치기|다시 접기/ });
  const tr = btn.closest("tr");
  if (!tr) throw new Error("접기 자백 행을 못 찾았다");
  return tr;
}

describe("RocketGrowthPnl — 판매도 광고도 없는 행 접기", () => {
  it("기본은 접힘이고 진짜 행만 보인다 — 변이ⓕ", async () => {
    h.optionPnl = QUIET_FIXTURE;
    renderPnl();
    await waitFor(() => expect(screen.getByText("팔린 상품")).toBeTruthy());
    expect(screen.queryByText("조용한 상품 1")).toBeNull();
    expect(screen.queryByText("조용한 상품 2")).toBeNull();
  });

  it("접은 «개수»를 수치로 자백한다 — 숨긴 것을 «없는» 것으로 만들지 않는다 (변이ⓖ)", async () => {
    h.optionPnl = QUIET_FIXTURE;
    renderPnl();
    await waitFor(() => expect(screen.getByText("팔린 상품")).toBeTruthy());
    const row = foldRow();
    expect(row.textContent).toContain("2개");
    expect(row.textContent).toContain("접은 것이지 «없는» 것이 아니다");
  });

  it("펼치면 조용한 행이 실제로 나타나고, 다시 접으면 사라진다", async () => {
    h.optionPnl = QUIET_FIXTURE;
    renderPnl();
    await waitFor(() => expect(screen.getByText("팔린 상품")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "펼치기" }));
    expect(screen.getByText("조용한 상품 1")).toBeTruthy();
    expect(screen.getByText("조용한 상품 2")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "다시 접기" }));
    expect(screen.queryByText("조용한 상품 1")).toBeNull();
  });

  // ★합격 ㉣ — 접기는 «표시»이지 «계산»이 아니다. 소계는 백엔드 conservation.options_net_sum이라
  //   구조상 접기와 무관한데, 누군가 화면에서 다시 더하는 순간(그게 완료 QA가 잡은 네 번째
  //   발현이었다) 접힌 행만큼 소계가 새게 된다. 그 회귀를 여기서 못 박는다.
  it("상품 행 소계가 접기 전후로 «완전히 같다» — 접기는 표시이지 계산이 아니다", async () => {
    h.optionPnl = QUIET_FIXTURE;
    renderPnl();
    await waitFor(() => expect(screen.getByText("팔린 상품")).toBeTruthy());
    const readSubtotal = () => {
      const tr = screen.getAllByText("상품 행 소계")
        .map((el) => el.closest("tr"))
        .find((x): x is HTMLTableRowElement => x != null);
      if (!tr) throw new Error("소계 행을 못 찾았다");
      return tr.textContent ?? "";
    };
    const folded = readSubtotal();
    expect(folded).toContain("60,000원"); // conservation.options_net_sum
    fireEvent.click(screen.getByRole("button", { name: "펼치기" }));
    expect(readSubtotal()).toBe(folded);
  });

  // ★변이ⓗ가 노리는 자리 — 판매수량 0이라고 «조용»한 게 아니다. 반품으로 매출이 음수인 행은
  //   손익에 실제로 영향을 준다. 판매수량만 보고 접으면 그 행이 화면에서 사라진다.
  it("판매수량 0이어도 매출이 음수(반품)면 접지 않는다 — 변이ⓗ", async () => {
    h.optionPnl = {
      ...OPTION_PNL_BASE,
      options: [
        opt({ vendor_item_id: "V-RETURN", name: "반품난 상품", revenue: "-30000", units_sold: 0, net_profit: "-30000" }),
        opt({ vendor_item_id: "V-QUIET-1", name: "조용한 상품 1" }),
      ],
    };
    renderPnl();
    await waitFor(() => expect(screen.getByText("반품난 상품")).toBeTruthy());
    expect(screen.queryByText("조용한 상품 1")).toBeNull();
    expect(foldRow().textContent).toContain("1개");
  });

  it("조용한 행이 하나도 없으면 자백 행 자체가 안 뜬다 — 0을 억지로 그리지 않는다", async () => {
    h.optionPnl = {
      ...OPTION_PNL_BASE,
      options: [opt({ vendor_item_id: "V-ACTIVE", name: "팔린 상품", revenue: "100000", units_sold: 10, net_profit: "60000" })],
    };
    renderPnl();
    await waitFor(() => expect(screen.getByText("팔린 상품")).toBeTruthy());
    expect(screen.queryByRole("button", { name: /펼치기|다시 접기/ })).toBeNull();
  });

  it("전 행이 조용해도 표가 «비어 보이지» 않는다 — 접었다는 사실이 남는다", async () => {
    h.optionPnl = {
      ...OPTION_PNL_BASE,
      options: [opt({ vendor_item_id: "V-QUIET-1", name: "조용한 상품 1" })],
    };
    renderPnl();
    await waitFor(() => expect(screen.getByRole("button", { name: "펼치기" })).toBeTruthy());
    // 「그 창에 판매도 광고도 없다」(행이 0건일 때의 빈 상태)와 혼동되면 안 된다 — 행은 있다.
    expect(screen.queryByText("그 창에 판매도 광고도 없다")).toBeNull();
    expect(within(foldRow()).getByText("1개")).toBeTruthy();
  });
});

// ════════════════════════ 변이 주입 결과 (실행 확인 후 원복) ════════════════════════
// 기준선 12/12 그린. 8종 전부 주입 직후 RED, `git checkout --` 원복 직후 12/12 그린으로 재확인했고
// 마지막에 `git status --porcelain`이 빈 출력임을 확인했다(소스 영구 변경 0).
//   ⓐ `<RgSettlementCard data={overview} />` → `<span />`            → 2건 RED
//   ⓑ `{ovErr ? (` → `{false ? (`                                    → 1건 RED
//   ⓒ `overview.rg_settlement == null ? (` → `false ? (`             → 1건 RED
//   ⓓ CommandCenter의 카드 호출 → `<span />`                          → 1건 RED (원자리 후퇴 탐지)
//   ⓔ `const RefreshButton = onRefresh ? (` → `true ? (`             → 1건 RED (읽기 전용 파괴)
//   ⓕ `{visibleRows.map(` → `{rows.map(`                             → 3건 RED
//   ⓖ `{quietCount > 0 && (` → `{false && (`                         → 5건 RED
//   ⓗ `isQuiet`에서 `n(r.revenue) === 0` 제거                         → 1건 RED (반품 행이 접힘)
//
// ★ⓓ가 이 파일의 «최종 표면까지 가는 경로를 끊는» 변이다(전역 §4). 정의를 공용 모듈로 옮긴
//   리팩터는 타입체크·기존 테스트가 전부 초록인 채로 «원자리 렌더를 지워도» 통과할 수 있다 —
//   종합 조망을 실제로 마운트해 카드 문자열과 갱신 버튼을 함께 보는 단언만이 그걸 잡는다.
