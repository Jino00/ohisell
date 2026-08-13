// @vitest-environment jsdom
//
// sellTypeBreakdown.test.tsx — 판매유형(2P/3P) 분해가 **눈에 닿는가**.
//
// 왜 생겼나(2026-08-13):
//   ① 오하이테크 탭이 3P 매출 53,700원에서 1P 광고비 536,212원까지 빼 이익 −543,622원을
//      보였다. 백엔드에서 1P를 빼는 것으로 고쳤는데, **빼기만 하면 그 돈이 화면에서 통째로
//      사라진다**(30일 20,947,574원). 정합이 아니라 은폐가 된다 → 각주가 그걸 막는다.
//   ② 오픽스는 2P(로켓그로스)와 3P(Wing)를 합쳐 한 이익률로 보여줬다. 쿠팡이 가져가는 몫이
//      3P ≈8.58% vs 2P ~19.5%+로 두 배 넘게 다른데 뭉치면 어느 쪽이 버는지 안 보인다.
//
// 백엔드 테스트(test_ops_panel_sell_type_scope.py)가 값을 지키고, 이 파일이 «그 값이 눈에
// 닿는가»를 지킨다 — 백엔드가 내놓아도 화면이 안 그리면 사용자에겐 없는 돈이다.
//
// 이 파일이 죽이는 변이:
//   M1 2P 행 삭제(0원이면 칸을 지움 → «없다»와 «0»을 구분 못 함)
//   M2 1P 각주 삭제(뺀 돈이 사라짐) · M3 각주를 0원일 때도 띄움(늘 켜진 경고는 안 읽힌다)
//   M4 미분류 광고비 각주 삭제 · M5 이익률 미표시
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { SellTypeBreakdown } from "./CoupangOps";
import type { SalesSellTypeRow, SalesSummaryData } from "../lib/api";

afterEach(cleanup);

const ROWS: SalesSellTypeRow[] = [
  {
    sell_type: "3P", channel_type: "Wing",
    revenue: "53700", fee: "4607", cost: "10441",
    ad_spend: "40361", shipping: "1900",
    profit: "-7410", profit_rate: "-13.80",
    conv_revenue: "71600", roas: "1.77",
  },
  {
    sell_type: "2P", channel_type: "로켓그로스",
    revenue: "0", fee: "0", cost: "0",
    ad_spend: "0", shipping: "0",
    profit: "0", profit_rate: null,
    conv_revenue: "0", roas: null,
  },
];

function summary(over: Partial<SalesSummaryData> = {}): SalesSummaryData {
  return {
    revenue: "53700", fee: "4607", cost: "10441",
    ad_spend: "40361", shipping: "1900",
    profit: "-7410", profit_rate: "-13.80",
    conv_revenue: "71600", roas: "1.77",
    excluded_ad_spend: "536212", excluded_ad_conv: "1383850",
    ad_spend_unassigned: "0",
    ...over,
  };
}

describe("판매유형 분해", () => {
  it("M1 3P와 2P가 둘 다 보인다 — 0원이어도 칸이 사라지지 않는다", () => {
    render(<SellTypeBreakdown rows={ROWS} summary={summary()} />);
    expect(screen.getByText(/3P · Wing/)).toBeTruthy();
    expect(screen.getByText(/2P · 로켓그로스/)).toBeTruthy();
  });

  it("M5 각 유형의 이익과 이익률이 보인다", () => {
    render(<SellTypeBreakdown rows={ROWS} summary={summary()} />);
    expect(screen.getByText("-7,410원")).toBeTruthy();
    expect(screen.getByText("-13.8%")).toBeTruthy();
  });

  it("M2 뺀 1P 광고비가 각주로 남는다 — 빼되 숨기지 않는다", () => {
    render(<SellTypeBreakdown rows={ROWS} summary={summary()} />);
    expect(screen.getByText("536,212원")).toBeTruthy();
    expect(screen.getByText(/로켓배송\(1P\) 광고비/)).toBeTruthy();
  });

  it("M3 뺀 게 없으면 각주도 없다 — 늘 켜진 경고는 안 읽힌다", () => {
    render(
      <SellTypeBreakdown rows={ROWS} summary={summary({ excluded_ad_spend: "0" })} />
    );
    expect(screen.queryByText(/로켓배송\(1P\) 광고비/)).toBeNull();
  });

  it("M4 판매유형으로 못 가른 광고비도 각주로 남는다", () => {
    render(
      <SellTypeBreakdown
        rows={ROWS}
        summary={summary({ excluded_ad_spend: "0", ad_spend_unassigned: "12345" })}
      />
    );
    expect(screen.getByText("12,345원")).toBeTruthy();
    expect(screen.getByText(/계정 단위 일별 집계/)).toBeTruthy();
  });

  it("미분류 행은 «가를 수 없다»고 이름 붙는다 — 빈칸이나 3P로 위장하지 않는다", () => {
    const withUn: SalesSellTypeRow[] = [
      ...ROWS,
      {
        sell_type: null, channel_type: "미분류",
        revenue: "0", fee: "0", cost: "0",
        ad_spend: "1363", shipping: "0",
        profit: "-1363", profit_rate: null,
        conv_revenue: "0", roas: null,
      },
    ];
    render(
      <SellTypeBreakdown
        rows={withUn}
        summary={summary({ excluded_ad_spend: "0", ad_spend_unassigned: "1363" })}
      />
    );
    expect(screen.getByText("판매유형 미배분")).toBeTruthy();
    expect(screen.getByText("-1,363원")).toBeTruthy();
  });

  it("★광고비를 판매유형으로 안 가르는 이유가 화면에 적힌다", () => {
    // 라벨이 판매경로가 아니라는 사실(D-CPP-43)을 각주로 못박는다 — 안 적으면
    // 「3P 광고비 0원」이 «광고를 안 했다»로 오독된다.
    render(<SellTypeBreakdown rows={ROWS} summary={summary()} />);
    expect(screen.getByText(/광고비는 판매유형으로 가르지 않는다/)).toBeTruthy();
    expect(screen.getByText(/97.28%/)).toBeTruthy();
  });

  it("「오늘」 탭에서는 광고 수치의 기준일을 밝힌다", () => {
    render(<SellTypeBreakdown rows={ROWS} summary={summary()} refDate="2026-08-12" />);
    expect(screen.getByText(/2026-08-12 기준/)).toBeTruthy();
  });

  it("데이터가 없으면 아무것도 그리지 않는다(빈 표 대신)", () => {
    const { container } = render(<SellTypeBreakdown rows={[]} summary={summary()} />);
    expect(container.firstChild).toBeNull();
  });
});
