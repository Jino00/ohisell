// @vitest-environment jsdom
//
// otaoPoReachesTheUser.test.tsx — 「📦 발주 (OTAO)」가 **사람에게 실제로 닿는가** (계약 §4 S1)
//
// ## 왜 이 파일이 따로 있나
//
// n=4 적대 리뷰가 남긴 관측이 이 파일의 존재 이유다:
//   *"진짜 «최종 표면 절단»은 실행 불가능했다 — `build_roster()`를 호출하는 라우터·화면·
//     `ingest.py`가 저장소 전체에 0건이다. **끊을 마지막 마디 자체가 존재하지 않는다.**"*
// 이제 그 마디가 생겼으므로, 그 마디를 끊는 변이를 여기서 죽인다. 죽여야 할 변이 다섯:
//
//   SUR-1  `App.tsx`의 `/otao-po` **라우트** 제거          → 화면이 아예 안 뜬다
//   SUR-2  `Layout.tsx`의 좌측 「📦 발주 (OTAO)」 **메뉴** 제거 → 갈 길이 없다
//   SUR-3  로스터의 **세 칸 중 하나**를 화면이 안 그림      → 픽업 결정이 사라진다(계약 §3-9)
//   SUR-4  **매핑 필요** 목록을 화면이 안 그림             → 조용한 발주 누락(계약 §2-9)
//   SUR-5  **`ledger_empty`를 무시하고 0을 그림**          → 「안 심었다」가 「발주 0」으로 읽힌다
//
// **`App`을 통째로 `/otao-po`에서 렌더한다.** 라우팅·레이아웃·페이지·직렬화가 한 줄로 이어져야만
// 통과하므로 어느 하나만 끊어도 죽는다. api 모듈은 모킹해 네트워크를 안 탄다 — 재는 것은
// 「값이 화면 픽셀이 되나」이지 서버가 아니다.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import type { OtaoRoster } from "../lib/api";

const ROSTER: OtaoRoster = {
  ledger_empty: false,
  window_start: "2026-01-27",
  rows: [
    {
      product_code: "GAPIP15PR",
      ordered: 500,
      picked: 300,
      reserved: 200,
      out_of_window_ordered: 4000,
      last_order_date: "2026-06-01",
      order_count: 2,
    },
    {
      // ★음수 잔량 — 0으로 깎으면 「창이 어긋났다」는 신호가 사라진다(자백 ③)
      product_code: "GSAS24U",
      ordered: 100,
      picked: 350,
      reserved: -250,
      out_of_window_ordered: 0,
      last_order_date: "2026-05-02",
      order_count: 1,
    },
  ],
  totals: {
    ordered: 600,
    picked: 650,
    reserved: -50,
    out_of_window_ordered: 4000,
    unmapped_qty: 80,
    sku_count: 2,
    unmapped_name_count: 1,
  },
  unmapped: [
    { item_name: "For iPhone 15/16/14Pro Privacy Tempered Glass", quantity: 80 },
  ],
  notes: [
    "예약 잔량은 통관 원장이 덮는 창(2026-01-27 이후) 안의 발주분만 센다.",
    "예약 잔량이 음수인 코드가 있다(GSAS24U) — 창 밖 발주분의 입고가 창 안에 찍혔다는 신호다.",
  ],
  source: {
    orders_total: 3,
    orders_authoritative: 2,
    orders_superseded: 1,
    last_order_date: "2026-06-01",
    name_map_total: 2,
    name_map_resolved: 1,
  },
};

const EMPTY: OtaoRoster = {
  ledger_empty: true,
  window_start: null,
  rows: [],
  totals: {},
  unmapped: [],
  notes: [],
  source: {
    orders_total: 0,
    orders_authoritative: 0,
    orders_superseded: 0,
    last_order_date: null,
    name_map_total: 0,
    name_map_resolved: 0,
  },
};

let payload: OtaoRoster = ROSTER;

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    fetchOtaoRoster: vi.fn(async () => payload),
    // ★S3 판매 축은 같은 화면에 살지만 이 파일이 재는 것은 **발주 3칸**이다. 그쪽 표면은
    //   `otaoSalesReachesTheUser.test.tsx`가 따로 잡는다. 여기서 일부러 «실패»시키는 이유는,
    //   판매 쪽이 죽어도 **발주 3칸은 그대로 보여야** 하기 때문이다 — 한쪽 실패가 다른 쪽을
    //   지우면 화면이 「없다」고 거짓말한다. 그 격리를 이 mock이 매번 검사한다.
    fetchOtaoSales: vi.fn(async () => {
      throw new Error("이 파일은 발주 3칸만 잰다");
    }),
    // 레이아웃이 부르는 헬스/스케줄러류는 조용히 실패해도 이 화면 판정과 무관하다.
    fetchHealth: vi.fn(async () => { throw new Error("not needed"); }),
    fetchSchedulerStatus: vi.fn(async () => { throw new Error("not needed"); }),
  };
});

beforeEach(() => {
  payload = ROSTER;
  window.history.pushState({}, "", "/otao-po");
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function renderApp() {
  // 동적 임포트 — vi.mock이 먼저 걸린 뒤 App이 api를 집게 하려면 이 순서여야 한다.
  const { default: App } = await import("../App");
  return render(<App />);
}

describe("★「📦 발주 (OTAO)」가 사람에게 닿는 경로 — 라우트·메뉴·직렬화가 한 줄로 이어진다", () => {
  it("SUR-1: `/otao-po` 라우트가 있어야 발주 화면이 뜬다", async () => {
    await renderApp();
    expect(await screen.findByRole("heading", { name: /발주 \(OTAO\)/ })).toBeTruthy();
  });

  it("SUR-2: 좌측 메뉴에 「발주 (OTAO)」 링크가 있어야 갈 길이 있다", async () => {
    await renderApp();
    const link = await screen.findByRole("link", { name: /발주 \(OTAO\)/ });
    expect(link.getAttribute("href")).toBe("/otao-po");
  });

  it("SUR-3: ★세 칸이 **전부** 화면에 뜬다 — 하나만 빠져도 픽업 결정이 사라진다", async () => {
    await renderApp();
    await screen.findByText("GAPIP15PR");

    // 헤더 세 칸이 실재해야 한다(합치면 이 중 하나가 사라진다 — 계약 §3-9).
    expect(screen.getByRole("columnheader", { name: "발주 누계" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "픽업 누계" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "예약 잔량" })).toBeTruthy();

    // 값 셋이 각각 픽셀이 됐는가 — 같은 행 안에서 본다(다른 행 숫자에 속지 않도록).
    const row = screen.getByText("GAPIP15PR").closest("tr")!;
    const cells = Array.from(row.querySelectorAll("td")).map((td) => td.textContent?.trim());
    expect(cells[1]).toBe("500"); // 발주 누계
    expect(cells[2]).toBe("300"); // 픽업 누계
    expect(cells[3]).toContain("200"); // 예약 잔량
  });

  it("SUR-3b: 음수 잔량이 **음수로** 그려진다 — 0으로 깎으면 창 어긋남 신호가 사라진다", async () => {
    await renderApp();
    const row = (await screen.findByText("GSAS24U")).closest("tr")!;
    const cells = Array.from(row.querySelectorAll("td")).map((td) => td.textContent?.trim());
    expect(cells[3]).toContain("-250");
  });

  it("자백 ①: 데이터 구간(2026-01-27)이 화면에 적힌다", async () => {
    await renderApp();
    await screen.findByText("GAPIP15PR");
    expect(screen.getByText(/2026-01-27 이후/)).toBeTruthy();
    // 창 밖 발주분도 «따로» 보인다 — 잔량에서 뺀 몫이 사라지면 안 된다.
    const row = screen.getByText("GAPIP15PR").closest("tr")!;
    expect(Array.from(row.querySelectorAll("td")).map((td) => td.textContent?.trim())[4]).toBe("4,000");
  });

  it("SUR-4: 매핑 필요 품목명이 **수량과 함께** 뜬다 — 숨기면 조용한 발주 누락이다", async () => {
    await renderApp();
    expect(
      await screen.findByText("For iPhone 15/16/14Pro Privacy Tempered Glass"),
    ).toBeTruthy();
    const row = screen
      .getByText("For iPhone 15/16/14Pro Privacy Tempered Glass")
      .closest("tr")!;
    expect(row.textContent).toContain("80");
  });

  it("SUR-6: 정본/대체됨 건수가 화면에 있다 — D-INV-3의 근거 보존 표면", async () => {
    // ★적대 리뷰 P2-5 — 이 표면을 통째로 지워도 7건이 전부 초록이었다. 「왜 이 숫자인가」를
    //   되짚는 유일한 자리라(개정 전 판본을 버리지 않고 보관하는 이유가 그것이다) 잠근다.
    await renderApp();
    await screen.findByText("GAPIP15PR");
    expect(screen.getByText(/정본 발주서 2건/)).toBeTruthy();
    expect(screen.getByText(/대체됨 1건/)).toBeTruthy();
  });

  it("SUR-7: 사전 커버리지 배지(붙음 N/M)가 화면에 있다", async () => {
    // ★적대 리뷰 P2-5 — 「87.2%를 100%인 척하지 않는다」의 표면. 지워지면 매핑 결손의
    //   «크기»가 화면에서 사라지고 목록만 남는다.
    await renderApp();
    expect(await screen.findByText(/1\/2 붙음/)).toBeTruthy();
  });

  it("SUR-5: ★원장이 비면 0을 그리지 않고 «아직 안 심었다»라고 말한다", async () => {
    payload = EMPTY;
    await renderApp();
    expect(await screen.findByText(/아직 안 심었다/)).toBeTruthy();
    // 표를 그리지 않는다 — 0으로 채운 표는 「발주가 없다」로 읽힌다.
    expect(screen.queryByRole("columnheader", { name: "발주 누계" })).toBeNull();
  });
});
