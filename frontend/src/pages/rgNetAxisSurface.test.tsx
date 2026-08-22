// @vitest-environment jsdom
//
// rgNetAxisSurface.test.tsx — RG 매출 net 축 전환이 «사람 눈에 닿는 자리»를 지킨다 (D-CPP-49).
//
// ## 존재 이유 (적대 리뷰 1R, 2026-08-22)
//
// 백엔드는 촘촘했다 — 값이 만들어지는 경로의 변이 14종이 전부 죽었고, summary dict에서 키를
// 빼는 «표면 절단» 3종도 죽었다. 그런데 **dict에 실린 값이 화면 픽셀이 되는 마지막 한 칸**은
// 아무도 안 지켜서, 프론트 변이 3종이 471/471 초록인 채로 살아남았다:
//   FE-1 RG 행의 ⚠ 부분치 경고를 통째로 삭제 · FE-4 드리프트 표의 「ㄴ RG gross 원장」 줄 삭제
//   FE-5 「옵션축 N/M일」 힌트 삭제
// 셋 다 «이 변경의 자백 장치»다. 자백이 사라져도 초록이면, 화면은 모르는 것을 아는 척하게 된다.
//
// 그리고 P1-1이 정확히 이 구멍의 산물이었다: 백엔드가 `null`을 내보내고 프론트가 `=== false`로만
// 받는 **두 층의 계약 불일치**는 백엔드 dict 테스트로도, 렌더만 보는 테스트로도 안 잡히고
// **둘을 잇는 테스트**로만 잡힌다(교훈 #321의 다른 층 — 그때는 서비스층↔HTTP, 이번엔 HTTP↔렌더).
//
// 이 파일이 죽이는 변이: FE-1 · FE-4 · FE-5 · `!== true` → `=== false` 되돌리기 ·
//   `rg_same_axis`를 안 읽고 산문 하드코딩으로 되돌리기.
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

import { ReconciliationCard, RevenueDriftCard, rgAxisHint, rgAxisWarn } from "./CommandCenter";
import type { OverviewResponse, RevenueReconcile } from "../lib/api";

afterEach(cleanup);

function makeOverview(accountSummary: Record<string, unknown>): OverviewResponse {
  return {
    period: { from: "2026-08-05", to: "2026-08-20" },
    account: {
      summary: {
        revenue: "3200000.00",
        revenue_3p: "47140.00",
        revenue_rg: "3152860.00",
        revenue_rg_basis: "console_net",
        ad_spend: "0.00",
        ...accountSummary,
      },
      by_option: [],
    },
    ad: { summary: { ad_spend: "0.00", ad_confirmed_applies: true }, by_option: [] },
    product: { summary: {}, by_option: [] },
  } as unknown as OverviewResponse;
}

// ════════════════════════════ 자백 문구 (순수 함수) ════════════════════════════
describe("rgAxisWarn — 「모른다」의 기본값은 침묵이 아니라 경고다", () => {
  it("complete=true면 경고 없음", () => {
    expect(rgAxisWarn({ rg_option_axis_complete: true, rg_option_axis_days: "16/16" }))
      .toBeUndefined();
  });

  it("complete=false면 부분치 경고", () => {
    expect(rgAxisWarn({ rg_option_axis_complete: false, rg_option_axis_days: "9/16" }))
      .toContain("부분치");
  });

  it("★필드가 아예 없으면(구버전 응답·직렬화 누락) **경고한다** — 침묵으로 떨어지지 않는다", () => {
    // 이게 P1-1의 회귀 가드다. `=== false`로 되돌리면 여기서 죽는다.
    expect(rgAxisWarn({})).toBeTruthy();
    expect(rgAxisWarn({ rg_option_axis_complete: null })).toBeTruthy();
  });

  it("★창 전체가 아직 안 닫힌 경우는 «결함»이 아니라 D+1의 결과라고 말한다", () => {
    const w = rgAxisWarn({ rg_option_axis_complete: false, rg_option_axis_days: "0/0", rg_open_days: 1 });
    expect(w).toContain("D+1");
    expect(w).not.toContain("부분치");   // 백필 구멍 경고와 구분된다
  });
});

describe("rgAxisHint — 축과 커버리지를 밝힌다", () => {
  it("콘솔 net임을 말하고 옵션축 일수·미확정 일수를 붙인다", () => {
    const h = rgAxisHint({ rg_option_axis_days: "15/16", rg_open_days: 1 });
    expect(h).toContain("콘솔 net");
    expect(h).toContain("옵션축 15/16일");   // ★FE-5 변이가 여기서 죽는다
    expect(h).toContain("미확정 1일");
  });

  it("미확정 0일이면 그 문구를 안 붙인다(잡음 억제)", () => {
    expect(rgAxisHint({ rg_option_axis_days: "16/16", rg_open_days: 0 }))
      .not.toContain("미확정");
  });
});

// ════════════════════════════ 실제 렌더 ════════════════════════════
describe("정합성 카드 — RG 행", () => {
  it("RG 매출을 콘솔 net으로 보이고 «gross·취소 미차감» 표식을 더는 달지 않는다", () => {
    render(<ReconciliationCard data={makeOverview({
      rg_option_axis_days: "16/16", rg_option_axis_complete: true, rg_open_days: 0,
    })} />);
    expect(screen.getByText("3,152,860원")).toBeTruthy();
    // 행 힌트와 각주 둘 다 「콘솔 net」이라 말한다 — 행 힌트를 정확히 집는다.
    expect(screen.getByText("판매분석 · 로켓그로스 (콘솔 net) · 옵션축 16/16일")).toBeTruthy();
    // ★축을 바꿔 놓고 라벨을 안 고치면 화면이 net을 gross라고 부른다.
    expect(screen.queryByText(/gross·취소 미차감/)).toBeNull();
    expect(screen.queryByText("⚠ 부분치")).toBeNull();
  });

  it("★커버리지가 미완이면 ⚠ 부분치를 그린다 (FE-1 변이가 여기서 죽는다)", () => {
    render(<ReconciliationCard data={makeOverview({
      rg_option_axis_days: "9/16", rg_option_axis_complete: false, rg_open_days: 0,
    })} />);
    expect(screen.getByText("⚠ 부분치")).toBeTruthy();
    expect(screen.getByText(/옵션축 9\/16일/)).toBeTruthy();
  });

  it("★커버리지 필드가 통째로 없어도 ⚠를 그린다 — 화면이 조용히 단정하지 않는다", () => {
    render(<ReconciliationCard data={makeOverview({})} />);
    expect(screen.getByText("⚠ 부분치")).toBeTruthy();
  });
});

// ════════════════════════════ 드리프트 표 ════════════════════════════
const RECONCILE: RevenueReconcile = {
  period: { from: "2026-06-08", to: "2026-06-08", closed_through: "2026-06-08" },
  has_closed_days: true,
  has_official: true,
  coverage: { expected_days: 1, days_with_data: 1, complete: true },
  official: { gmv_3p: 1693230, gmv_rg: 1786500, gmv_total: 3479730,
              days_with_data: 1, last_refresh: null },
  ours: { revenue_3p: "1724230", revenue_rg: "1786500", revenue_total: "3510730",
          revenue_rg_gross: "1918700" },
  rg_same_axis: true,
  drift: {
    abs_3p: "31000", abs_rg: "0", abs_total: "31000",
    pct_3p: "1.83", pct_rg: "0", pct_total: "0.89",
    abs_rg_gross: "132200", pct_rg_gross: "7.40",
  },
  note: "테스트",
} as unknown as RevenueReconcile;

describe("드리프트 표 — net으로 옮기며 잃은 신호가 살아 있는가", () => {
  it("★「ㄴ RG gross 원장」 줄이 렌더되고 구 D-11 잔차(+7.40%)를 보인다 (FE-4 변이가 여기서 죽는다)", () => {
    render(<RevenueDriftCard reconcile={RECONCILE} onRefresh={() => {}} refreshing={false} msg={null} />);
    expect(screen.getByText("ㄴ RG gross 원장 (수집 대조)")).toBeTruthy();
    expect(screen.getByText("+7.40%")).toBeTruthy();
    expect(screen.getByText("1,918,700원")).toBeTruthy();
  });

  it("★`rg_same_axis`를 «읽는다» — 산문 하드코딩이면 플래그를 뒤집어도 화면이 안 바뀐다", () => {
    render(<RevenueDriftCard reconcile={RECONCILE} onRefresh={() => {}} refreshing={false} msg={null} />);
    expect(screen.getByText(/같은 숫자를 두 번 읽었다/)).toBeTruthy();
    cleanup();

    const legacy = { ...RECONCILE, rg_same_axis: false } as unknown as RevenueReconcile;
    render(<RevenueDriftCard reconcile={legacy} onRefresh={() => {}} refreshing={false} msg={null} />);
    expect(screen.queryByText(/같은 숫자를 두 번 읽었다/)).toBeNull();
    expect(screen.getByText(/기준 차이/)).toBeTruthy();
  });
});
