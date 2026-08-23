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
  /** 손익 조회도 매달 수 있어야 한다 — ①②③의 in-flight 구간을 재려면 필요하다(2R NEW P1). */
  nextOptionPnl: null as null | (() => Promise<unknown>),
  /** 조회를 «매달아» 둘 수 있게 하는 손잡이 — in-flight 구간의 화면을 재려면 필요하다.
   *  null이면 즉시 resolve(기본). 함수를 넣으면 그 함수가 promise를 만든다. */
  nextOverview: null as null | (() => Promise<unknown>),
}));

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  syncRealtime: () => Promise.resolve(),
  fetchApi: () => Promise.resolve([]),
  fetchCommandCenter: () =>
    h.overviewFails
      ? Promise.reject(new Error("백엔드 500"))
      : h.nextOverview
        ? h.nextOverview()
        : Promise.resolve(h.overview),
  fetchRgOptionPnl: () =>
    h.nextOptionPnl ? h.nextOptionPnl() : Promise.resolve(h.optionPnl),
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
  h.nextOverview = null;
  h.nextOptionPnl = null;
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

  // ★적대 리뷰 1R P1 — 이 사슬이 밟은 「모름이 0으로 접히는」 자리의 **다섯 번째**.
  //   `has_data`는 «판매일 축 차감액이 0이 아님»으로도 참이 되어(원장 row와 독립) 계정 카드가
  //   0개인데 ✅ 녹색으로 「정산총액 0원」을 단정했다. RG 정산 성숙도 D+12 · 이 화면은 「어제」
  //   고정 ⇒ **거의 매일**이 이 상태다. 초판은 가드를 «백엔드가 결코 내지 않는» 분기
  //   (rg_settlement == null)에 걸어 둬서 아무것도 못 막았다.
  it("원장 계정 row가 0건이면 «0원»이 아니라 «아직 안 들어왔다»고 말한다 — 1R P1", async () => {
    h.overview = {
      ...(makeOverview() as unknown as Record<string, unknown>),
      rg_settlement: {
        summary: {
          total: "0", has_data: true, note: "",
          deducted: "4550", axis: "sales_date", ad_settlement: "0",
        },
        by_account: [],
      },
    } as unknown as OverviewResponse;
    h.optionPnl = OPTION_PNL_BASE;
    renderSettlement();
    await waitFor(() => expect(screen.getByText(/아직 안 들어왔다/)).toBeTruthy());
    expect(screen.getByText(/D\+12/)).toBeTruthy();
    // ★있어야 할 것과 «없어야 할 것»을 함께 — 옛 결함의 서명은 초록 카드와 「정산총액 0원」이다.
    expect(screen.queryByText(/RG 정산 비용 — 순이익 반영됨/)).toBeNull();
    expect(screen.queryByText(/정산총액 0원/)).toBeNull();
    // 그리고 위 ①②③은 살아 있어야 한다 — 원장이 비었다고 판매일 축까지 죽이면 안 된다.
    expect(screen.getByText("판매수수료 요율의 출처")).toBeTruthy();
  });

  it("계정 row가 있으면 그 자백이 «안» 뜬다 — 공허 단언 방지 짝", async () => {
    h.overview = makeOverview();
    h.optionPnl = OPTION_PNL_BASE;
    renderSettlement();
    await waitFor(() => expect(screen.getByText(/RG 정산 비용 — 순이익 반영됨/)).toBeTruthy());
    expect(screen.queryByText(/아직 안 들어왔다/)).toBeNull();
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

// ★1R P2-4·P2-6. 초판 테스트는 **공허했다**: 「실패하면 옛 값이 남지 않는다」를 단언했지만
//   실패 경로에선 `ovErr` 분기가 먼저 걸려 카드를 어차피 가리므로, 리셋을 통째로 지워도
//   초록이었다(변이 N5 생존으로 실측). 진짜 위험은 **성공하는 계정 전환의 in-flight 구간**이다 —
//   그 사이 화면은 «다른 법인»의 정산 카드를 계정키까지 달고 그대로 보인다.
//   그래서 조회를 매달아 두고 그 구간의 DOM을 직접 잰다.
describe("RocketGrowthSettlement — 계정 전환이 옛 계정의 카드를 남기지 않는다 (1R P2-6)", () => {
  it("전환 조회가 끝나기 전, 직전 계정의 금액·계정키가 «화면에 없다»", async () => {
    h.overview = makeOverview();
    h.optionPnl = OPTION_PNL_BASE;
    renderSettlement();
    // 1단계: 오픽스로 성공 — 카드가 실제로 떠 있어야 이 테스트가 의미를 갖는다.
    await waitFor(() => expect(screen.getByText("222,222원")).toBeTruthy());
    expect(screen.getByText("COUPANG_WING1")).toBeTruthy();

    // 2단계: 다음 조회를 «매달아» 둔다(응답이 아직 안 온 상태를 만든다).
    let release: ((v: unknown) => void) | null = null;
    h.nextOverview = () => new Promise((res) => { release = res; });
    fireEvent.click(screen.getByRole("button", { name: "오하이테크" }));

    // ★in-flight 구간: 「불러오는 중…」이어야 하고, 옛 계정의 값이 남아 있으면 안 된다.
    await waitFor(() => expect(screen.getByText("불러오는 중…")).toBeTruthy());
    expect(screen.queryByText("222,222원")).toBeNull();
    expect(screen.queryByText("COUPANG_WING1")).toBeNull();

    // 3단계: 응답이 오면 다시 정상으로 — 리셋이 화면을 영구히 죽이지 않았는지 확인한다.
    release!(makeOverview());
    await waitFor(() => expect(screen.getByText("222,222원")).toBeTruthy());
  });

  // ★2R NEW P1 — 위 P2-6 수정(`setData(null)`)이 만든 회귀. ①②③의 칸은 값이 없으면
  //   「요율 미상」·「원가 커버리지 미달 — 순이익을 내지 않는다」 같은 **결론 문장**으로
  //   떨어지므로, 전환 in-flight 구간 내내 새 계정에 대해 그 다섯 문장이 «거짓으로» 뜬다.
  //   같은 순간 ④는 「불러오는 중…」이라고 정직하게 말했다 — 한 화면이 스스로 비대칭이었다.
  //   ★리뷰어의 관측: `setData(null)` 한 줄만 빼면 다섯 문장이 전부 사라졌다(= 이 커밋이 만든 것).
  it("전환 in-flight 구간에 ①②③이 «미상/미달»을 단정하지 않는다 — 2R NEW P1", async () => {
    h.overview = makeOverview();
    h.optionPnl = OPTION_PNL_BASE;
    renderSettlement();
    // 1단계: 정상 로딩 — 「실측」이 실제로 떠 있어야 이 테스트가 의미를 갖는다.
    await waitFor(() => expect(screen.getByText("실측 (완결 정산주기에서 역산)")).toBeTruthy());

    // 2단계: 손익 조회를 매달아 둔다.
    let release: ((v: unknown) => void) | null = null;
    h.nextOptionPnl = () => new Promise((res) => { release = res; });
    fireEvent.click(screen.getByRole("button", { name: "오하이테크" }));

    // ★in-flight: «모른다»라고 말해야 한다. 결론 문장은 하나도 없어야 한다.
    await waitFor(() => expect(screen.getByText(/«모른다»이지 «미상\/미달»이 아니다/)).toBeTruthy());
    expect(screen.queryByText(/요율 미상 — 잴 완결 주기가 없다/)).toBeNull();
    expect(screen.queryByText(/원가 커버리지 미달/)).toBeNull();
    expect(screen.queryByText(/순이익을 내지 않는다/)).toBeNull();
    expect(screen.queryByText(/판매일 축을 못 냈다/)).toBeNull();
    expect(screen.queryByText(/완결 정산주기가 없어 대조할 수 없다/)).toBeNull();
    // 그리고 옛 계정의 「실측」도 남아 있으면 안 된다(그게 P2-6이 고친 것).
    expect(screen.queryByText("실측 (완결 정산주기에서 역산)")).toBeNull();

    // 3단계: 응답이 오면 정상 복귀 — 로딩 가드가 화면을 영구히 죽이지 않았는지.
    release!(OPTION_PNL_BASE);
    await waitFor(() => expect(screen.getByText("실측 (완결 정산주기에서 역산)")).toBeTruthy());
    expect(screen.queryByText(/«모른다»이지 «미상\/미달»이 아니다/)).toBeNull();
  });

  it("손익 조회가 «실패»하면 로딩이 아니라 에러를 말한다 — 두 결손이 같은 얼굴이면 안 된다", async () => {
    h.overview = makeOverview();
    h.nextOptionPnl = () => Promise.reject(new Error("손익 500"));
    renderSettlement();
    await waitFor(() => expect(screen.getByText(/손익 500/)).toBeTruthy());
    expect(screen.queryByText(/«모른다»이지 «미상\/미달»이 아니다/)).toBeNull();
  });

  it("전환 조회가 실패하면 «못 불러왔다»로 끝난다 — 옛 값으로 되돌아가지 않는다", async () => {
    h.overview = makeOverview();
    h.optionPnl = OPTION_PNL_BASE;
    renderSettlement();
    await waitFor(() => expect(screen.getByText("222,222원")).toBeTruthy());
    h.overviewFails = true;
    fireEvent.click(screen.getByRole("button", { name: "오하이테크" }));
    await waitFor(() => expect(screen.getByText(/정산 내역을 못 불러왔다/)).toBeTruthy());
    expect(screen.queryByText("222,222원")).toBeNull();
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

  // ★1R P2-1·P2-2 — 이 두 분기는 «옮기기 전에도» 아무 테스트가 안 지키고 있었다. 계약 §4 ⓖ가
  //   재는 것이 「종전대로 렌더된다」이므로, 추출 리팩터가 조용히 뒤집어도 아무도 모르는 상태를
  //   그대로 두면 ⓖ를 잴 수단이 없다. 추출한 김에 두 분기를 못 박는다.
  it("차감액이 음수면 «+»와 「(환급)」이 뜬다 — 부호를 찍지 말고 계산해야 하는 자리 (P2-1)", async () => {
    const ov = makeOverview() as unknown as Record<string, unknown>;
    (ov.rg_settlement as { summary: Record<string, unknown> }).summary.deducted = "-777777";
    h.overview = ov as unknown as OverviewResponse;
    render(<CommandCenter />);
    await waitFor(() => expect(screen.getByText(/\(환급\)/)).toBeTruthy());
    expect(screen.getByText(/^\+777,777원/)).toBeTruthy();
    expect(screen.queryByText(/^−777,777원/)).toBeNull();
  });

  it("axis=sales_date면 헤드라인 축 문구가 «판매일 축»으로 바뀐다 (P2-2)", async () => {
    const ov = makeOverview() as unknown as Record<string, unknown>;
    (ov.rg_settlement as { summary: Record<string, unknown> }).summary.axis = "sales_date";
    h.overview = ov as unknown as OverviewResponse;
    render(<CommandCenter />);
    await waitFor(() =>
      expect(screen.getByText(/헤드라인은 «판매일 축»/)).toBeTruthy(),
    );
    expect(screen.queryByText(/«정산 인식일 축» — 정산 주기 통짜라/)).toBeNull();
  });

  it("axis=recognition_date면 «정산 인식일 축»으로 뜬다 — 두 분기가 같은 얼굴이면 안 된다 (P2-2 짝)", async () => {
    h.overview = makeOverview(); // 픽스처 기본이 recognition_date다
    render(<CommandCenter />);
    await waitFor(() =>
      expect(screen.getByText(/«정산 인식일 축» — 정산 주기 통짜라/)).toBeTruthy(),
    );
    expect(screen.queryByText(/헤드라인은 «판매일 축»/)).toBeNull();
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

  // ★1R P2-3 — `isQuiet`의 `ad_spend` 조건을 지워도 초록이었다. 백엔드가 **명시적으로 만드는**
  //   행이다(`rg_daily_pnl.py`: 광고비만 있는 옵션도 행으로 낸다). 접히면 손익 화면에서 가장
  //   봐야 할 행(안 팔리면서 광고비만 태우는 옵션)이 사라진다 — 접기가 «은폐»가 되는 자리다.
  it("판매 0 · 매출 0인데 광고비가 있으면 접지 않는다 — 안 팔리며 돈만 쓰는 행이다 (P2-3)", async () => {
    h.optionPnl = {
      ...OPTION_PNL_BASE,
      options: [
        opt({ vendor_item_id: "V-ADONLY", name: "광고만 태운 상품", ad_spend: "5000", net_profit: "-5000" }),
        opt({ vendor_item_id: "V-QUIET-1", name: "조용한 상품 1" }),
      ],
    };
    renderPnl();
    await waitFor(() => expect(screen.getByText("광고만 태운 상품")).toBeTruthy());
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
//
// ──────── 2R: 적대 리뷰 1R 지적을 고친 뒤 추가 주입 (기준선 20/20) ────────
//   N1 `by_account.length === 0 ? (` → `false ? (`   (1R P1 가드 제거)    → 1건 RED
//   N2 헤드라인 부호 반전 `d < 0 ? "+" : "−"` 뒤집기  (1R P2-1)           → 1건 RED
//   N3 축 배너 분기 반전 `=== "sales_date"` → `!==`   (1R P2-2)           → 2건 RED
//   N4 `isQuiet`에서 `n(r.ad_spend) === 0` 제거       (1R P2-3)           → 1건 RED
//   N5 계정 전환 리셋 2곳 제거                        (1R P2-6)           → 1건 RED
//   N5b `load` 시작 리셋 1곳만 제거                                        → 1건 RED
//
// ★★N5는 **초판에서 생존했다** — 그리고 그 생존이 이 파일의 가장 값진 발견이다. 초판 테스트는
//   「실패하면 옛 값이 안 남는다」를 «실패 경로»로 쟀는데, 실패 경로는 `ovErr` 분기가 카드를
//   어차피 가리므로 리셋을 통째로 지워도 초록이었다 — **주석은 무엇을 지킨다고 선언하는데
//   실제로는 아무것도 안 지키는** 공허 단언이었다(직전 계약 3R P2와 같은 병). 진짜 위험한
//   구간은 «성공하는 전환의 in-flight»이고, 그걸 재려면 조회를 매달아야 했다.
//
// ──────── 3R: 2R NEW P1(1R 수정이 만든 회귀)을 고친 뒤 주입 (기준선 22/22) ────────
//   T1 `{!err && data == null ? (` → `{false ? (`  (로딩 가드 무력화)     → 1건 RED
//   T2 로딩 문구 텍스트만 삭제(블록·조건은 유지)                          → 1건 RED
//   T3 `!err &&` 제거 → 실패도 로딩 얼굴이 됨                             → 1건 RED
//
// ★★2R NEW P1이 이 PR의 가장 값진 기록이다: **1R 지적(P2-6)을 고친 «수단»이 회귀를 낳았다.**
//   `setData(null)`로 옛 계정 값을 버렸더니, ①②③의 칸이 값 없음을 「요율 미상」·「원가 커버리지
//   미달 — 순이익을 내지 않는다」 같은 **결론 문장**으로 떨어뜨렸다. 같은 순간 ④만 「불러오는
//   중…」이라 정직하게 말해 **한 화면이 스스로 비대칭**이었다. 「모름」이 「아니다」로 접히는
//   이 사슬의 여섯 번째 자리이고, 다섯 번째를 고치는 커밋이 여섯 번째를 만들었다 —
//   국소 수리가 병의 «모양»을 못 없앤다는 증거를 한 PR 안에서 다시 얻었다.
