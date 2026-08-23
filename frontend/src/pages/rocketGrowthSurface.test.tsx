// @vitest-environment jsdom
//
// rocketGrowthSurface.test.tsx — RG(로켓그로스 2P) «자기 화면» 둘(`RocketGrowthPnl.tsx`·
// `RocketGrowthSettlement.tsx`)이 백엔드 값을 실제로 화면에 그리는가 (계약
// `docs/contracts/CONTRACT_2p_own_screens.md` D-CPP-54, 위임 세션 2026-08-23).
//
// 왜 이 파일이 필요한가 — `rgSettlementAxisSurface.test.tsx` 머리말과 같은 이유다: 백엔드가
// 옵션 행·계정공통·보존식·요율출처·커버리지·장부대조 값을 dict에 실어도, 화면의 렌더 블록
// (`{note && ...}`·`{ac && ...}`·`{cons && ...}` 등) 한 줄이 지워지면 그 값을 쓰는 순수 함수
// 테스트(`rgOptionPnlFacts.test.ts`)는 전부 초록인 채로 화면만 조용해진다. 그래서 여기서는
// 반환값이 아니라 **DOM 문자열**을 검사한다.
//
// 이 파일이 죽여야 하는 변이(직접 주입해 RED 확인 후 원복 — 결과는 파일 하단 주석):
//   ① RocketGrowthPnl.tsx  — 자백 배너(`{note && ...}`) 삭제
//   ② RocketGrowthPnl.tsx  — 「계정 공통」 표 전체 삭제
//   ③ RocketGrowthPnl.tsx  — 보존식 블록(`{cons && ...}`) 삭제
//   ④ RocketGrowthPnl.tsx  — 상품 행 「남는 이익」 칸의 값만 지우고 헤더는 남김
//   ⑤ RocketGrowthPnl.tsx  — `ad_unallocated` 행 삭제
//   ⑥ RocketGrowthSettlement.tsx — 장부 총액 대조 블록 삭제
//   ⑦ RocketGrowthSettlement.tsx — `rate_basis === "rate_unknown"`일 때의 「요율 미상」 분기 삭제
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => ({
  // 테스트마다 이 참조를 바꿔서 fetchRgOptionPnl이 무엇을 돌려줄지 정한다.
  response: null as unknown,
  // 화면 B가 「주기별 정산 내역」(계약 §1-A-3)을 위해 함께 부르는 조회. 이 파일의 관심사는
  // 아니지만 mock을 안 걸면 jsdom에서 진짜 네트워크로 나간다 — null이면 화면은 "불러오는 중…"
  // 으로 남고, 그 자리를 실제로 검사하는 것은 `rgSettlementCardReuse.test.tsx`다.
  overview: null as unknown,
}));

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  fetchRgOptionPnl: () => Promise.resolve(h.response),
  fetchCommandCenter: () => Promise.resolve(h.overview),
}));

import RocketGrowthPnl from "./RocketGrowthPnl";
import RocketGrowthSettlement from "./RocketGrowthSettlement";
import type { RgOptionPnlResponse } from "../lib/api";

afterEach(() => cleanup());

// 정상 케이스 — 옵션 1건 · 계정 공통 4항목 전부 0이 아님 · 보존식 일치 · 완결 정산주기 대조 있음 ·
// 요율은 실측(settled_rate).
const BASE: RgOptionPnlResponse = {
  account: "COUPANG_WING1",
  date_from: "2026-08-22",
  date_to: "2026-08-22",
  options: [
    {
      vendor_item_id: "V-001",
      name: "테스트 상품 A",
      revenue: "100000",
      units_sold: 10,
      order_count: 8,
      fee_logistics: "3000",
      fee_sale_fee: "5000",
      fee_total: "8000",
      cost: "40000",
      has_cost: true,
      ad_spend: "2000",
      net_profit: "55123",
    },
  ],
  account_common: {
    period_fees: "1000",
    payable_vat: "2000",
    revenue_axis_gap: "700",
    ad_unallocated: "3456",
    ad_unallocated_options: 2,
    fee_axis_fallback_gap: "800",
    cost_unmapped_revenue: "0",
    fee_unmapped_revenue: "500",
  },
  conservation: {
    options_net_sum: "55123",
    account_common_sum: "-3000",
    computed_total_net: "52123",
    reference_net: "52123",
    diff: "0",
    ok: true,
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
  reconciliation: {
    cycle_from: "07-14",
    cycle_to: "07-20",
    computed: "250000",
    actual: "250500",
    diff: "-500",
    diff_pct: "-0.05",
  },
  ad_spend_warning: null,
};

const renderPnl = () =>
  render(
    <MemoryRouter>
      <RocketGrowthPnl />
    </MemoryRouter>,
  );

const renderSettlement = () =>
  render(
    <MemoryRouter>
      <RocketGrowthSettlement />
    </MemoryRouter>,
  );

// ════════════════════════ 화면 A: RocketGrowthPnl ════════════════════════

describe("RocketGrowthPnl — 상품 행", () => {
  it("옵션 행이 상품명·판매수량·「남는 이익」 값을 실제 DOM에 낸다", async () => {
    h.response = BASE;
    renderPnl();
    await waitFor(() => expect(screen.getByText("테스트 상품 A")).toBeTruthy());
    expect(screen.getByText("10")).toBeTruthy(); // 판매수량
    // ★변이④가 지우는 자리. 옵션이 1건뿐이라 「상품 행 소계」도 같은 금액(55,123원)을 낸다
    //   (소계는 rows 배열에서 직접 reduce하므로 셀 렌더와 무관하게 그대로 남는다) — 그래서
    //   개수(2)로 판정한다. 변이④가 셀을 비우면 이 값이 1개(소계만)로 줄어 RED가 된다.
    expect(screen.getAllByText("55,123원").length).toBe(2);
  });
});

describe("RocketGrowthPnl — 자백 배너 (변이①)", () => {
  it("판매일 축·요율·커버리지 문구가 배너로 뜬다", async () => {
    h.response = BASE;
    renderPnl();
    await waitFor(() => expect(screen.getByText(/판매일 축/)).toBeTruthy());
    const note = screen.getByText((c) => c.includes("판매일 축") && c.includes("요율"));
    expect(note.textContent).toContain("10.50%");
  });
});

describe("RocketGrowthPnl — 「계정 공통」 표 (변이②)", () => {
  it("보관비·납부세액·매출 축 차이·원장 축 폴백·미배분 광고비가 전부 뜬다", async () => {
    h.response = BASE;
    renderPnl();
    await waitFor(() => expect(screen.getByText("계정 공통 (상품에 못 붙는 것)")).toBeTruthy());
    expect(screen.getByText("−1,000원")).toBeTruthy(); // period_fees
    expect(screen.getByText("−2,000원")).toBeTruthy(); // payable_vat
    expect(screen.getByText("700원")).toBeTruthy(); // revenue_axis_gap
    expect(screen.getByText("−800원")).toBeTruthy(); // fee_axis_fallback_gap
  });

  // ★완료 QA가 라이브에서 잡았다(2026-08-23): 마이너스를 글자로 박아 둬서 실제로 음수인
  //   payable_vat(부가세 «환급» −50,119원)에 `−-50,119원`이 떴다. 부호가 둘이고, 하나만
  //   남겼어도 방향이 거꾸로였을 값이다. 차감액 v가 이익에 주는 영향은 −v다 — 계산해야 한다.
  it("차감액이 음수면(부가세 환급) 이중부호가 아니라 «+»로 뜬다 — 완료 QA 지적", async () => {
    h.response = {
      ...BASE,
      account_common: { ...BASE.account_common, payable_vat: "-50119.20", period_fees: "1000" },
    };
    renderPnl();
    await waitFor(() => expect(screen.getByText("계정 공통 (상품에 못 붙는 것)")).toBeTruthy());
    // 있어야 할 것: 환급이므로 이익을 «늘린다»
    expect(screen.getByText("+50,119원")).toBeTruthy();
    // 없어야 할 것: 이중부호와, 부호를 잃은 형태
    expect(screen.queryByText("−-50,119원")).toBeNull();
    expect(screen.queryByText("−50,119원")).toBeNull();
    // 양수 차감액은 여전히 «−»다(수정이 정상 방향까지 뒤집지 않았는지)
    expect(screen.getByText("−1,000원")).toBeTruthy();
  });

  it("계정 공통 표 자체가 없으면(응답에 account_common이 없으면) 안 뜬다", async () => {
    h.response = { ...BASE, account_common: undefined as unknown as RgOptionPnlResponse["account_common"] };
    renderPnl();
    await waitFor(() => expect(screen.getByText("테스트 상품 A")).toBeTruthy());
    expect(screen.queryByText("계정 공통 (상품에 못 붙는 것)")).toBeNull();
  });
});

describe("RocketGrowthPnl — ad_unallocated 행 (변이⑤)", () => {
  it("미배분 광고비 행과 「이 손익엔 안 실린다」 자백 문구가 뜬다", async () => {
    h.response = BASE;
    renderPnl();
    await waitFor(() => expect(screen.getByText(/미배분 광고비 \(2옵션\)/)).toBeTruthy());
    expect(screen.getByText(/이 손익엔 안 실린다/)).toBeTruthy();
  });

  it("ad_unallocated=0이면 그 행은 안 뜬다 — 0을 억지로 그리지 않는다", async () => {
    h.response = {
      ...BASE,
      account_common: { ...BASE.account_common, ad_unallocated: "0", ad_unallocated_options: 0 },
    };
    renderPnl();
    await waitFor(() => expect(screen.getByText("테스트 상품 A")).toBeTruthy());
    expect(screen.queryByText(/미배분 광고비/)).toBeNull();
  });
});

describe("RocketGrowthPnl — 보존식 블록 (변이③)", () => {
  it("일치하면 ✅ 문구와 상품행소계/계정공통/합계/대시보드/차이 숫자가 뜬다", async () => {
    h.response = BASE;
    renderPnl();
    await waitFor(() => expect(screen.getByText("✅ 대시보드 RG 행과 일치")).toBeTruthy());
    // 보존식 줄: "상품 행 소계 55,123원 + 계정 공통 −3,000원 = 52,123원 / 대시보드 52,123원 / 차이 0원"
    const line = screen.getByText((c) => c.includes("상품 행 소계") && c.includes("계정 공통"));
    expect(line.textContent).toContain("52,123원");
    expect(line.textContent).toContain("0원");
  });

  it("어긋나면 ⚠️ 문구로 바뀐다 — diff를 0으로 숨기지 않는다", async () => {
    h.response = {
      ...BASE,
      conservation: { ...BASE.conservation, ok: false, diff: "999", reference_net: "51124" },
    };
    renderPnl();
    await waitFor(() => expect(screen.getByText("⚠️ 대시보드 RG 행과 어긋남")).toBeTruthy());
    expect(screen.queryByText("✅ 대시보드 RG 행과 일치")).toBeNull();
  });

  it("conservation 자체가 없으면 보존식 블록이 안 뜬다", async () => {
    h.response = { ...BASE, conservation: undefined as unknown as RgOptionPnlResponse["conservation"] };
    renderPnl();
    await waitFor(() => expect(screen.getByText("테스트 상품 A")).toBeTruthy());
    expect(screen.queryByText(/대시보드 RG 행과/)).toBeNull();
  });

  // ★적대 리뷰 1R P1-1이 잡은 자리 — 이 두 케이스가 없어서 결함이 통과했다.
  //   백엔드는 원가 게이트 미달 창에서 다섯 칸을 «전부» null로 낸다(rg_daily_pnl.py:196,248).
  //   그런데 화면은 앞의 두 칸만 가드가 빠져 「0원」으로 덮고 있었다 — 「모름」과 「0」이 같은
  //   얼굴이 되는 자리다. 값 비교로는 안 잡힌다(0도 «있을 수 있는 값»이라서). 그러니
  //   **null일 때 「0원」이 «없어야» 한다**를 직접 단언한다.
  // ★완료 QA가 잡은 «네 번째» 발현 — 표 하단 「상품 행 소계」와 보존식 박스의 같은 라벨이
  //   한 화면에서 다른 숫자를 말했다. 화면이 `rows.reduce(...)`로 «직접 더하는» 두 번째
  //   진실의 원천을 갖고 있었고, 원장 축 폴백 창에선 전 행 net_profit이 null이라 0으로 접혔다.
  //   ⇒ 원천을 하나로(백엔드 `conservation.options_net_sum`) 줄였다.
  it("원장 축 폴백 창에서 표 하단 소계가 보존식 박스와 «같은 숫자»다 — 완료 QA 지적", async () => {
    h.response = {
      ...BASE,
      commission_axis: "recognition_date",
      fee_trustworthy: false,
      // 폴백 창: 개별 행은 net_profit을 못 낸다(null)지만 백엔드는 소계를 안다.
      options: BASE.options.map((o) => ({
        ...o, fee_logistics: null, fee_sale_fee: null, fee_total: null, net_profit: null,
      })),
      conservation: { ...BASE.conservation, options_net_sum: "1915331", ok: true },
    };
    renderPnl();
    // 보존식 줄과 표 하단 소계가 «둘 다» 1,915,331원이어야 한다.
    const line = await waitFor(() =>
      screen.getByText((c) => c.includes("상품 행 소계") && c.includes("계정 공통")),
    );
    expect(line.textContent).toContain("1,915,331원");
    // 표 하단 소계 «행»도 같은 숫자여야 한다 — 두 원천이 갈라지면 여기서 깨진다.
    const subtotalRow = screen
      .getAllByText("상품 행 소계")
      .map((el) => el.closest("tr"))
      .find((tr): tr is HTMLTableRowElement => tr != null);
    expect(subtotalRow).toBeTruthy();
    expect(subtotalRow?.textContent).toContain("1,915,331원");
    // ★그리고 옛 결함의 서명(0원 소계)이 «없어야» 한다.
    expect(subtotalRow?.textContent).not.toContain("0원");
  });

  // ★라이브가 잡은 세 번째 발현(2026-08-23, 08-22 창) — 배지가 「모름」을 「어긋남」으로
  //   단정했다. 백엔드는 `ok: null`을 정직하게 내는데 프론트가 `cons.ok ? A : B`로 써서
  //   null이 falsy로 접혔다. 「모름」과 「아니다」는 다른 말이다.
  it("ok=null이면 「어긋남」이 아니라 «대조할 수 없다»로 뜬다 — 라이브 08-22", async () => {
    h.response = {
      ...BASE,
      cost_trustworthy: false,
      conservation: {
        options_net_sum: null, account_common_sum: null, computed_total_net: null,
        reference_net: null, diff: null, ok: null,
      },
    };
    renderPnl();
    await waitFor(() => expect(screen.getByText(/대조할 수 없다/)).toBeTruthy());
    // «없어야 할 것»과 «있어야 할 것»을 함께 단언한다(부정 단언만 두면 문구가 바뀔 때 공허해진다).
    expect(screen.queryByText(/어긋남/)).toBeNull();
    expect(screen.queryByText(/일치/)).toBeNull();
  });

  it("ok=false면 여전히 「어긋남」이다 — 위 수정이 진짜 어긋남까지 덮지 않는다", async () => {
    h.response = {
      ...BASE,
      conservation: { ...BASE.conservation, ok: false, diff: "1200", reference_net: "50923" },
    };
    renderPnl();
    await waitFor(() => expect(screen.getByText(/어긋남/)).toBeTruthy());
    expect(screen.queryByText(/대조할 수 없다/)).toBeNull();
  });

  it("원가 게이트 미달 창(전 칸 null)에서 보존식이 「0원」이 아니라 「—」로 뜬다 — 1R P1-1", async () => {
    h.response = {
      ...BASE,
      cost_trustworthy: false,
      conservation: {
        options_net_sum: null,
        account_common_sum: null,
        computed_total_net: null,
        reference_net: null,
        diff: null,
        ok: false,
      },
    };
    renderPnl();
    // ★「상품 행 소계」는 표의 소계 행에도 있다 — 보존식 줄만 집으려면 「계정 공통」과 함께 본다
    //   (바로 위 두 테스트와 같은 셀렉터).
    const line = await waitFor(() =>
      screen.getByText((c) => c.includes("상품 행 소계") && c.includes("계정 공통")),
    );
    // ★부정 단언만 두면 문구가 바뀌는 순간 소리 없이 참이 된다(직전 계약 3R P2).
    //   그래서 «있어야 할 것»(— 다섯 개)과 «없어야 할 것»(0원)을 함께 단언한다.
    expect(line.textContent).toContain("상품 행 소계");
    expect(line.textContent).not.toContain("0원");
    expect((line.textContent ?? "").match(/—/g) ?? []).toHaveLength(5);
  });

  // ★적대 리뷰 1R 변이 B — 이 렌더 블록을 지웠는데 25건이 전부 그린이었다(생존).
  //   vendor_id를 못 찾으면 광고비가 0으로 내려오는데, 그게 「0원이다」인지 「모른다」인지를
  //   말하는 유일한 칸이 이것이다. 사라지면 화면이 0원을 «단정»한다.
  it("ad_spend_warning이 있으면 화면이 「광고비 미상」을 말한다 — 1R 변이 B 생존분", async () => {
    h.response = { ...BASE, ad_spend_warning: "vendor_id를 못 찾았다" };
    renderPnl();
    await waitFor(() => expect(screen.getByText(/광고비 미상/)).toBeTruthy());
    expect(screen.getByText(/vendor_id를 못 찾았다/)).toBeTruthy();
  });

  it("ad_spend_warning이 없으면 그 배너가 «안» 뜬다 — 공허 단언 방지 짝", async () => {
    h.response = BASE;
    renderPnl();
    await waitFor(() => expect(screen.getByText("테스트 상품 A")).toBeTruthy());
    expect(screen.queryByText(/광고비 미상/)).toBeNull();
  });
});

// ════════════════════════ 화면 B: RocketGrowthSettlement ════════════════════════

describe("RocketGrowthSettlement — 장부 총액 대조 (변이⑥)", () => {
  it("완결 주기·이 방식의 합·원장 실청구액·차이가 전부 뜬다", async () => {
    h.response = BASE;
    renderSettlement();
    await waitFor(() => expect(screen.getByText("장부 총액 대조")).toBeTruthy());
    expect(screen.getByText("07-14 ~ 07-20")).toBeTruthy();
    expect(screen.getByText("250,000원")).toBeTruthy(); // computed
    expect(screen.getByText("250,500원")).toBeTruthy(); // actual
    // diff=-500 → dd 텍스트 "−500원 (-0.05%)". dt("차이")의 다음 형제가 그 dd다 — 자백 배너의
    // "장부대조 −500원(-0.05%)"(공백 없음, 같은 숫자 포함)과 텍스트 매칭이 겹치므로 위치로 특정한다.
    const diffDd = screen.getByText("차이").nextElementSibling as HTMLElement;
    expect(diffDd.textContent).toContain("−500원");
    expect(diffDd.textContent).toContain("-0.05%");
  });

  it("reconciliation이 null이면 「완결 정산주기가 없어 대조할 수 없다」로 바뀐다", async () => {
    h.response = { ...BASE, reconciliation: null };
    renderSettlement();
    await waitFor(() => expect(screen.getByText("장부 총액 대조")).toBeTruthy());
    expect(screen.getByText(/완결 정산주기가 없어 대조할 수 없다/)).toBeTruthy();
    expect(screen.queryByText("250,500원")).toBeNull();
  });
});

describe("RocketGrowthSettlement — 요율 출처 (변이⑦)", () => {
  it("rate_basis=settled_rate면 「실측」이 뜬다", async () => {
    h.response = BASE;
    renderSettlement();
    await waitFor(() => expect(screen.getByText("판매수수료 요율의 출처")).toBeTruthy());
    expect(screen.getByText("실측 (완결 정산주기에서 역산)")).toBeTruthy();
    expect(screen.queryByText(/잴 완결 주기가 없다/)).toBeNull();
  });

  it("rate_basis=rate_unknown이면 「요율 미상 — 잴 완결 주기가 없다」로 바뀐다 — 실측과 같은 얼굴을 하면 안 된다", async () => {
    h.response = { ...BASE, rate_basis: "rate_unknown", rate: null, rate_cycles: null };
    renderSettlement();
    await waitFor(() => expect(screen.getByText("판매수수료 요율의 출처")).toBeTruthy());
    expect(screen.getByText(/잴 완결 주기가 없다/)).toBeTruthy();
    expect(screen.queryByText("실측 (완결 정산주기에서 역산)")).toBeNull();
  });
});

// ════════════════════════ 결함 B 회귀 — 소수점 노출 (2026-08-23) ════════════════════════
//
// prod 08-21 실측: `판매수수료 2,892.324원` · `남는 이익 14,334.676원` · `원가 2,930.4원`이 그대로
// 떴다. 원인: 공용 `format.won()`은 반올림하지 않는데, 2P 두 화면만 `Decimal` 문자열을 그대로
// 넘겼다. 수정은 두 화면에 로컬 `wonR = (v) => won(Math.round(n(v)))`를 두고 금액 렌더를 전부
// 그것으로 통일한 것 — 이 아래 테스트는 백엔드가 소수 문자열을 주는 응답을 mock해 화면 A·B의
// 여러 금액 칸이 전부 반올림된 원 단위로만 뜨는지(소수점 문자열이 «없는지») 확인한다.
// 한 칸만 막으면 나머지가 다시 새므로 화면 A의 상품 행 5칸 + 소계 + 계정 공통 4칸 + 보존식
// 5칸, 화면 B의 커버리지·장부대조 칸까지 덮는다.
const DECIMAL: RgOptionPnlResponse = {
  ...BASE,
  options: [
    {
      vendor_item_id: "V-002",
      name: "소수점 상품",
      revenue: "100000.5",
      units_sold: 7,
      order_count: 5,
      fee_logistics: "3000.12",
      fee_sale_fee: "2892.324",
      fee_total: "5892.444",
      cost: "2930.4",
      has_cost: true,
      ad_spend: "1500.6",
      net_profit: "14334.676",
    },
  ],
  account_common: {
    period_fees: "1000.1",
    payable_vat: "2000.2",
    revenue_axis_gap: "700.7",
    ad_unallocated: "3456.3",
    ad_unallocated_options: 2,
    fee_axis_fallback_gap: "800.8",
    cost_unmapped_revenue: "0",
    fee_unmapped_revenue: "500.5",
  },
  conservation: {
    options_net_sum: "14334.676",
    account_common_sum: "-3000.3",
    computed_total_net: "11334.376",
    reference_net: "11334.376",
    diff: "0",
    ok: true,
  },
  reconciliation: {
    cycle_from: "07-14",
    cycle_to: "07-20",
    computed: "250000.5",
    actual: "250500.9",
    diff: "-500.4",
    diff_pct: "-0.05",
  },
};

describe("RocketGrowthPnl — 결함 B 회귀: 소수점이 그대로 노출되면 안 된다", () => {
  it("상품 행 다섯 칸(매출·물류비·판매수수료·원가·광고비·남는이익)이 반올림된 원 단위로만 뜬다", async () => {
    h.response = DECIMAL;
    renderPnl();
    await waitFor(() => expect(screen.getByText("소수점 상품")).toBeTruthy());
    expect(screen.getByText("100,001원")).toBeTruthy(); // revenue 100000.5 → round → 100001
    expect(screen.getByText("3,000원")).toBeTruthy(); // fee_logistics 3000.12 → 3000
    expect(screen.getByText("2,892원")).toBeTruthy(); // fee_sale_fee 2892.324 → 2892
    expect(screen.getByText("2,930원")).toBeTruthy(); // cost 2930.4 → 2930
    expect(screen.getByText("1,501원")).toBeTruthy(); // ad_spend 1500.6 → round → 1501
    // net_profit 14334.676 → round → 14335. 옵션이 1건뿐이라 「상품 행 소계」도 같은 금액을
    // 낸다(BASE 픽스처의 같은 자리 테스트와 동일 이유) — 개수(2)로 판정한다.
    expect(screen.getAllByText("14,335원").length).toBe(2);

    // ★있어야 할 것과 없어야 할 것을 함께 — 부정 단언만 두면 문구가 바뀌는 순간 소리 없이
    //   참이 된다.
    expect(screen.queryByText(/2,892\.324원/)).toBeNull();
    expect(screen.queryByText(/14,334\.676원/)).toBeNull();
    expect(screen.queryByText(/2,930\.4원/)).toBeNull();
    expect(screen.queryByText(/100,000\.5원/)).toBeNull();
    expect(screen.queryByText(/1,500\.6원/)).toBeNull();
    expect(screen.queryByText(/3,000\.12원/)).toBeNull();
  });

  it("「계정 공통」 네 칸이 반올림된 원 단위로만 뜬다", async () => {
    h.response = DECIMAL;
    renderPnl();
    await waitFor(() => expect(screen.getByText("계정 공통 (상품에 못 붙는 것)")).toBeTruthy());
    expect(screen.getByText("−1,000원")).toBeTruthy(); // period_fees 1000.1 → 1000
    expect(screen.getByText("−2,000원")).toBeTruthy(); // payable_vat 2000.2 → 2000
    expect(screen.getByText("701원")).toBeTruthy(); // revenue_axis_gap 700.7 → round → 701
    expect(screen.getByText("−801원")).toBeTruthy(); // fee_axis_fallback_gap 800.8 → round → 801

    expect(screen.queryByText(/1,000\.1원/)).toBeNull();
    expect(screen.queryByText(/2,000\.2원/)).toBeNull();
    expect(screen.queryByText(/700\.7원/)).toBeNull();
    expect(screen.queryByText(/800\.8원/)).toBeNull();
  });

  it("보존식 다섯 칸이 반올림된 원 단위로만 뜬다", async () => {
    h.response = DECIMAL;
    renderPnl();
    await waitFor(() => expect(screen.getByText("✅ 대시보드 RG 행과 일치")).toBeTruthy());
    const line = screen.getByText((c) => c.includes("상품 행 소계") && c.includes("계정 공통"));
    expect(line.textContent).toContain("14,335원"); // options_net_sum 14334.676 → 14335
    // ★account_common_sum의 부호는 JSX가 붙인 게 아니라 Number.toLocaleString이 낸 것이라
    //   하이픈("-")이다(다른 칸의 "−"는 JSX가 직접 붙인 별개 문자 — U+2212).
    expect(line.textContent).toContain("-3,000원"); // account_common_sum -3000.3 → -3000
    expect(line.textContent).toContain("11,334원"); // computed_total_net 11334.376 → 11334
    expect(line.textContent).not.toContain("14,334.676원");
    expect(line.textContent).not.toContain("11,334.376원");
    expect(line.textContent).not.toContain("-3,000.3원");
  });
});

describe("RocketGrowthSettlement — 결함 B 회귀: 소수점이 그대로 노출되면 안 된다", () => {
  it("커버리지 칸(원가 미상 매출)이 반올림된 원 단위로만 뜬다", async () => {
    h.response = DECIMAL;
    renderSettlement();
    await waitFor(() => expect(screen.getByText("얼마나 덮었나 (커버리지)")).toBeTruthy());
    expect(screen.getByText(/매출 501원에는/)).toBeTruthy(); // fee_unmapped_revenue 500.5 → round → 501
    expect(screen.queryByText(/500\.5원/)).toBeNull();
  });

  it("장부 총액 대조 칸이 반올림된 원 단위로만 뜬다", async () => {
    h.response = DECIMAL;
    renderSettlement();
    await waitFor(() => expect(screen.getByText("장부 총액 대조")).toBeTruthy());
    expect(screen.getByText("250,001원")).toBeTruthy(); // computed 250000.5 → round → 250001
    expect(screen.getByText("250,501원")).toBeTruthy(); // actual 250500.9 → round → 250501
    const diffDd = screen.getByText("차이").nextElementSibling as HTMLElement;
    expect(diffDd.textContent).toContain("500원"); // |diff|=500.4 → round → 500
    expect(diffDd.textContent).not.toContain("500.4원");
    expect(screen.queryByText(/250,000\.5원/)).toBeNull();
    expect(screen.queryByText(/250,500\.9원/)).toBeNull();
  });
});

// ════════════════════════ 변이 주입 결과 (실행 확인, 원복 완료·소스 영구 변경 없음) ════════════════════════
// ①  {note && (...)} 삭제 → "RocketGrowthPnl — 자백 배너" 1건 RED, 나머지 12건 그린. 원복 후 13/13 그린.
// ②  「계정 공통」 표 {ac && (...)} 삭제 → "「계정 공통」 표" 1건 + "ad_unallocated 행" 1건째(값 존재
//     쪽) RED(⑤가 그 안에 중첩돼 있어 같이 죽음 — 의도된 결과). 원복 후 13/13 그린.
// ③  {cons && (...)} 삭제 → "보존식 블록" 중 일치/어긋남 2건 RED("conservation 없으면 안 뜬다"
//     1건은 cons가 이미 null이라 원래도 그린 — 정상). 원복 후 13/13 그린.
// ④  <td>{cell(r.net_profit)}</td> → 빈 <td/>(헤더는 남김) → "옵션 행이 …" 1건 RED
//     ("55,123원" 2개 중 1개(소계)만 남아 length===2 단언 실패). 원복 후 13/13 그린.
// ⑤  ac 블록 안 ad_unallocated 행만 별도 확인 — ②와 같은 자리(중첩)라 ②의 RED가 곧 ⑤의 RED.
// ⑥  장부 총액 대조 블록 전체 삭제(rec!=null·rec==null 두 분기 다 제거) → "장부 총액 대조" 2건
//     모두 RED("장부 총액 대조" 헤딩 자체가 없어짐). 원복 후 13/13 그린.
// ⑦  rate_basis===settled_rate 삼항의 else 분기(rate_unknown 문구) 삭제, 참 분기는 유지
//     → "rate_basis=rate_unknown이면 …" 1건만 RED, "rate_basis=settled_rate면 …"은 그대로 그린
//     (정확히 그 분기만 잡음). 원복 후 13/13 그린.
// 9종 전부 각 변이 주입 직후 대상 테스트가 RED, 원복 직후 13/13 그린으로 재확인했다. 소스 파일은
// git diff로 매번 원본과 바이트 동일함을 확인했다(diff 출력 없음).
