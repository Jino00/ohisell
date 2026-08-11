// @vitest-environment jsdom
//
// reconciliationAdBasis.test.tsx — 정합성 카드의 «광고» 블록이 거짓 단정을 하지 않는지 (D-CPP-38).
//
// ## 존재 이유 (2026-08-11 시각 QA 실사고)
//
// 오하이테크 종합조망 화면이 한 화면에서 광고비를 **두 값**으로 말하고 있었다:
//   · 위쪽 정합성 카드: 「전체 광고비(ALL) 0원 · net_profit 차감 기준」
//   · 아래 요약 카드·옵션표: 11,247원
// 돈은 안 틀렸다(순이익은 옵션축 11,247원을 제대로 뺀다). 틀린 건 **실토**였다 —
// 읽는 사람은 «오하이테크는 광고를 안 썼다»로 읽는다. 「위쪽 단정이 아래쪽 사실을
// 덮는다」(교훈 #235)의 재발이다.
//
// 원인: 광고센터(report/SALES) 소스는 «광고주 단위»라 광고주 계정(오픽스)에만 적용되는데,
// 미적용 계정에도 **0**을 실어 보냈다. 프론트의 `?? s.ad_spend` 폴백은 null만 잡으므로
// 0은 그대로 «측정된 0원»처럼 그려졌다.
//
// 이 파일이 죽이는 변이:
//   M1 미적용인데 0원으로 단정 · M2 적용 계정에서 «측정된 0원»을 폴백으로 뭉갬
//   M3 미적용 라벨을 「광고센터」로 표기 · M4 미적용인데 비-PA를 차감된 것처럼 표기
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

import { ReconciliationCard } from "./CommandCenter";
import type { OverviewResponse } from "../lib/api";

afterEach(cleanup);

function makeData(
  adSummary: Record<string, unknown>,
  accountSummary: Record<string, unknown> = {},
): OverviewResponse {
  return {
    period: { from: "2026-07-13", to: "2026-08-11" },
    account: {
      summary: {
        revenue: "908510.00",
        revenue_3p: "867810.00",
        revenue_rg: "40700.00",
        ad_spend: "11247.00",
        ...accountSummary,
      },
      by_option: [],
    },
    ad: { summary: { ad_spend: "11247.00", ...adSummary }, by_option: [] },
    product: { summary: {}, by_option: [] },
  } as unknown as OverviewResponse;
}

describe("정합성 카드 — 광고 블록", () => {
  it("★미적용 계정: 0원이라 단정하지 않고 실제 차감액(옵션축)을 보인다", () => {
    render(<ReconciliationCard data={makeData({
      ad_confirmed_applies: false,
      ad_confirmed_total: null,
      ad_confirmed_pa: null,
      ad_confirmed_nonpa: null,
    })} />);

    // 실제 net_profit 차감액이 보인다
    expect(screen.getByText("11,247원")).toBeTruthy();
    // ★«광고센터 기준 0원»이라는 거짓 단정이 없다 — 광고 블록에 0원 행은 비-PA 하나뿐이고
    //   그건 「산출 불가」라고 말한다.
    expect(screen.getByText(/광고센터 값이 없어 산출 불가/)).toBeTruthy();
    // 라벨이 출처를 밝힌다(「광고센터」라고 하지 않는다)
    expect(screen.getByText(/광고센터 미태깅 계정/)).toBeTruthy();
    expect(screen.getByText(/옵션축 실집행/)).toBeTruthy();
  });

  it("★미적용 계정: 「집행(DELIVERED)」 행을 그리지 않는다 — 광고센터 값이 없으니 분해가 불가능하다", () => {
    render(<ReconciliationCard data={makeData({
      ad_confirmed_applies: false,
      ad_confirmed_total: null,
      ad_confirmed_pa: null,
      ad_confirmed_nonpa: null,
    })} />);
    expect(screen.queryByText(/집행 \(상품검색광고\/PA\)/)).toBeNull();
    expect(screen.queryByText(/광고센터 · 집행 광고비\(DELIVERED\)/)).toBeNull();
  });

  it("★적용 계정: 광고센터 값을 그대로 쓴다(옵션축으로 폴백하지 않는다)", () => {
    render(<ReconciliationCard data={makeData({
      ad_confirmed_applies: true,
      ad_confirmed_total: "5166137",
      ad_confirmed_pa: "4612681",
      ad_confirmed_nonpa: "553456",
    })} />);
    expect(screen.getByText("5,166,137원")).toBeTruthy();
    expect(screen.getByText("4,612,681원")).toBeTruthy();
    expect(screen.getByText("553,456원")).toBeTruthy();
    expect(screen.getByText(/광고 \(쿠팡 \[광고센터\]\)/)).toBeTruthy();
  });

  it("★★적용 계정의 «측정된 0원»은 0원으로 남는다 — 폴백으로 뭉개지 않는다", () => {
    // 광고주 계정인데 그 기간 광고비가 정말 0원이면 그건 사실이다. 여기서 옵션축(11,247)으로
    // 폴백하면 «광고센터 기준 0원»이라는 진짜 사실을 잃는다. 「전부 null로 주기」식 게으른
    // 수정이 이 테스트에서 죽는다.
    render(<ReconciliationCard data={makeData({
      ad_confirmed_applies: true,
      ad_confirmed_total: "0",
      ad_confirmed_pa: "0",
      ad_confirmed_nonpa: "0",
    })} />);
    expect(screen.queryByText("11,247원")).toBeNull();
    expect(screen.getAllByText("0원").length).toBeGreaterThanOrEqual(3);
  });

  it("구백엔드 호환: applies 필드가 없으면 종전 동작(광고센터 블록)을 유지한다", () => {
    render(<ReconciliationCard data={makeData({
      ad_confirmed_total: "5166137",
      ad_confirmed_pa: "4612681",
      ad_confirmed_nonpa: "553456",
    })} />);
    expect(screen.getByText(/광고 \(쿠팡 \[광고센터\]\)/)).toBeTruthy();
    expect(screen.getByText("5,166,137원")).toBeTruthy();
  });

  it("매출 행은 이 수정에 영향받지 않는다(회귀 가드)", () => {
    render(<ReconciliationCard data={makeData({ ad_confirmed_applies: false })} />);
    expect(screen.getByText("908,510원")).toBeTruthy();
    expect(screen.getByText("867,810원")).toBeTruthy();
    expect(screen.getByText("40,700원")).toBeTruthy();
  });
});
