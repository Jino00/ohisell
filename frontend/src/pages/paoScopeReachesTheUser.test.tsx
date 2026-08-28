// @vitest-environment jsdom
//
// paoScopeReachesTheUser.test.tsx — 「🎛️ PAO 스코프」가 **사람에게 실제로 닿는가** (D-NAO-244)
//
// ## 왜 이 파일이 따로 있나
//
// 계약 §4의 마지막 항목이 **표면 절단 변이**를 요구한다. 이 저장소가 다섯 번 밟은 병이
// 「값은 만드는데 사람이 못 본다」이고(전역 §4 ★), `costPageReachesTheUser.test.tsx`가
// 그 병을 잡으려고 세운 관례가 이 파일의 본이다. 죽여야 할 변이 넷:
//
//   SUR-1 `App.tsx`의 `/naver-ad/scope` **라우트** 제거
//   SUR-2 `Layout.tsx`의 좌측 「🎛️ PAO 스코프」 **메뉴** 제거
//   SUR-3 `pao_scope_roster`가 실어 보낸 **스코프 상태**(맡김·역할)를 화면이 안 그림
//   SUR-4 총이익 **null을 0원으로** 그림 — 적자 그룹이 손익분기로 보인다
//
// **`App`을 통째로 `/naver-ad/scope`에서 렌더한다.** 라우팅·레이아웃·페이지·직렬화가 한 줄로
// 이어져야만 통과하므로 어느 하나만 끊어도 죽는다. api 모듈은 모킹해 네트워크를 안 탄다 —
// 재는 것은 「값이 화면 픽셀이 되나」이지 서버가 아니다.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { PaoScopeRoster } from "../lib/api";
// ★적대 리뷰 P2-3 채택 — 「클릭이 서버에 닿는가」를 재려면 이 함수들 «자신»을 vi.fn()으로
//   잡아야 한다(아래 vi.mock 팩토리가 오버라이드한다). 리뷰어의 표면 변이 SUR-5(호출 제거)가
//   생존했던 이유가 정확히 이것 — 읽기 표면만 재고 쓰기 표면은 아무도 안 봤다.
import { putPaoScopeAdgroup } from "../lib/api";

// ★로스터를 테스트마다 갈아끼운다 (D-NAO-267). vi.mock 팩토리는 호이스팅돼서 바깥 변수를
//   그냥 참조하면 초기화 전 접근이 된다 — vi.hoisted가 그 순서 문제의 공식 해법이다.
//   램프업 그룹을 공용 ROSTER에 «더하면» 기존 SUR-3·쓰기 표면 테스트의 findByTitle 단수
//   조회가 중복 매치로 깨진다(토글 버튼이 하나 더 생긴다). 그래서 더하지 않고 갈아끼운다.
const hoisted = vi.hoisted(() => ({ roster: null as unknown }));

const ROSTER: PaoScopeRoster = {
  window: { date_from: "2026-08-03", date_to: "2026-08-23", days: 21 },
  correction_factor: { low: 0.827, high: 1.3016, source: "actual_revenue_ratio" },
  totals: { cost: 100_000, imp: 1000, clk: 50, conv_amt: 120_000 },
  weekend_holiday: {
    weekday: { days: 15, cost: 80_000, imp: 800, clk: 40, conv_amt: 110_000, roas: 1.375 },
    weekend: { days: 5, cost: 18_000, imp: 180, clk: 9, conv_amt: 9_000, roas: 0.5 },
    holiday: { days: 1, cost: 2_000, imp: 20, clk: 1, conv_amt: 1_000, roas: 0.5 },
    identity: {
      total: { cost: 100_000, imp: 1000, clk: 50, conv_amt: 120_000 },
      sum_of_parts: { cost: 100_000, imp: 1000, clk: 50, conv_amt: 120_000 },
      ok: true,
      note: "평시+주말+공휴일 = 전체 (ref 63 §1-2 검산과 같은 방식·같은 grain)",
    },
    basis: "ad_date (성과 발생일) — ref 63 §1-2와 같은 grain",
    reference: "ref 63 §4-1 확정치: 주말 Σexcess −8,020,470원 · 공휴일 −915,912원.",
  },
  campaigns: [
    {
      campaign_id: "cmp-tpu",
      name: "01. 갤럭시_지문방지_TPU",
      campaign_type: "SHOPPING",
      optimizer: "ours",
      auto_operate: false, // ★스코프는 있는데 엔진은 꺼져 있는 상태 — 화면이 말해야 한다
      has_scope: true,
      scoped_count: 1,
      adgroup_count: 2,
      ramp_up_count: 0,
      cost: 100_000,
      imp: 1000,
      clk: 50,
      conv_amt: 120_000,
      roas: 1.2,
      gross_profit: -30_000,
      gross_profit_low: -60_000,
      gross_profit_high: 10_000,
      adgroups: [
        {
          adgroup_id: "grp-s25fe",
          name: "S25FE",
          status: "on",
          in_scope: true,
          scope_role: "accel",
          scope_enabled: true,
          cost: 10_000,
          imp: 100,
          clk: 10,
          conv_amt: 30_000,
          roas: 3.0,
          bep_roas: 1.711,
          baseline_days: 14,
          gross_profit: 7_534,
          gross_profit_low: 1_200,
          gross_profit_high: 15_000,
          profit_status: "ok",
        },
        {
          adgroup_id: "grp-z8wide",
          name: "Z폴드8와이드",
          status: "on",
          in_scope: false,
          scope_role: null,
          scope_enabled: null,
          cost: 90_000,
          imp: 900,
          clk: 40,
          conv_amt: 90_000,
          roas: 1.0,
          bep_roas: null,
          baseline_days: 14,
          gross_profit: null, // ★모름 — 0원이 아니다
          gross_profit_low: null,
          gross_profit_high: null,
          profit_status: "bep_unknown",
        },
      ],
    },
  ],
};

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    fetchPaoScopeRoster: vi.fn(async () => hoisted.roster),
    putPaoScopeAdgroup: vi.fn(async () => ({
      campaign_id: "cmp-tpu", adgroup_id: "grp-s25fe", role: "accel" as const,
      enabled: true, memo: null,
    })),
    deletePaoScopeAdgroup: vi.fn(async () => ({
      deleted: true, remaining_rows: 0, campaign_now_unrestricted: true,
    })),
    // 레이아웃이 부르는 헬스/스케줄러류는 조용히 실패시켜도 이 화면 판정과 무관하다.
    fetchHealth: vi.fn(async () => { throw new Error("not needed"); }),
    fetchSchedulerStatus: vi.fn(async () => { throw new Error("not needed"); }),
  };
});

beforeEach(() => {
  hoisted.roster = ROSTER; // 테스트마다 기본값으로 되돌린다 — 갈아끼운 게 새지 않게
  window.history.pushState({}, "", "/naver-ad/scope");
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

describe("★「🎛️ PAO 스코프」가 사람에게 닿는 경로 — 라우트·메뉴·직렬화가 한 줄로 이어진다", () => {
  it("SUR-1: `/naver-ad/scope` 라우트가 있어야 PAO 스코프 화면이 뜬다", async () => {
    await renderApp();
    expect(await screen.findByRole("heading", { name: /PAO 스코프/ })).toBeTruthy();
  });

  it("SUR-2: 좌측 메뉴에 「PAO 스코프」가 있어야 사람이 찾아올 수 있다", async () => {
    await renderApp();
    const links = await screen.findAllByRole("link", { name: /PAO 스코프/ });
    expect(links.length).toBeGreaterThan(0);
    expect(links.some((a) => a.getAttribute("href") === "/naver-ad/scope")).toBe(true);
  });

  it("SUR-3: 스코프 상태(맡김·역할)가 화면 픽셀이 된다", async () => {
    await renderApp();
    // 맡긴 그룹은 「맡김」으로, 안 맡긴 그룹은 「안 맡김」으로 그려져야 한다
    expect(await screen.findByTitle(/이 그룹을 끕니다/)).toBeTruthy();   // in_scope=true 쪽
    expect(await screen.findByTitle(/이 그룹을 엔진에 맡깁니다/)).toBeTruthy(); // in_scope=false 쪽
    // 역할 select가 accel을 실제로 선택하고 있어야 한다(직렬화가 끊기면 빈 값이 된다)
    const selects = await screen.findAllByRole("combobox");
    expect(selects.some((s) => (s as HTMLSelectElement).value === "accel")).toBe(true);
  });

  it("SUR-4: 총이익 null은 «모름»으로 그린다 — 0원으로 그리면 적자가 손익분기로 보인다", async () => {
    await renderApp();
    await waitFor(() => expect(screen.getAllByText("모름").length).toBeGreaterThan(0));
    // 그리고 그 행에 「0」이 총이익으로 찍히지 않았는지 — 모름과 0을 뭉치면 이 테스트가 죽는다
    expect(screen.queryByText(/^0$/)).toBeNull();
  });

  it("★엔진이 꺼져 있으면 화면이 그렇게 말한다 — 「맡겼다」와 「돌고 있다」를 뭉치지 않는다", async () => {
    await renderApp();
    expect(
      await screen.findByText(/스코프는 지정돼 있지만 엔진은 이 캠페인에서 돌지 않습니다/),
    ).toBeTruthy();
  });

  it("★SUR-5: 총이익 «구간»이 화면에 뜬다 — 단일값이면 가정이 사실처럼 읽힌다", async () => {
    await renderApp();
    // 큰 숫자는 «있는 그대로»(보정 없음), 작은 [하한 ~ 상한]이 «얼마나 모르는지»다.
    expect(await screen.findByText(/1,200 ~ 15,000/)).toBeTruthy();
    // 헤더도 단일 보정값이 아니라 구간을 말해야 한다
    expect(await screen.findByText(/구간\[×0\.827 ~ ×1\.302\]/)).toBeTruthy();
  });

  it("★SUR-6: 캠페인 «요약 행»의 구간도 화면에 뜬다 (적대 리뷰 P2-8 채택)", async () => {
    // 리뷰가 변이로 잡았다: 캠페인 요약 행의 low/high prop을 제거해도 828건이 전부 생존했다.
    // 그룹 행만 지켜지고 요약 행은 아무도 안 보고 있었다.
    await renderApp();
    expect(await screen.findByText(/-60,000 ~ 10,000/)).toBeTruthy();
  });

  it("★스코프가 있는 캠페인은 「예산은 엔진이 안 만진다」를 화면에서 밝힌다", async () => {
    await renderApp();
    expect(await screen.findByText(/캠페인 예산 조정은 엔진이 하지 않습니다/)).toBeTruthy();
  });
});

// ── ★적대 리뷰 P2-3 채택 — 쓰기 표면: 「클릭이 실제로 서버에 닿는가」 ──────────────────
//
// 리뷰어가 주입한 표면 변이 SUR-5(`setScope`에서 `putPaoScopeAdgroup` 호출 제거)가 **824건을
// 전부 통과하며 생존**했다. 위 SUR-1~4는 「값이 화면 픽셀이 되나」(읽기 표면)만 재고, 「사람이
// 누른 것이 서버에 닿나」(쓰기 표면)는 아무도 안 봤기 때문이다. 사용자가 토글을 눌러도 아무
// 일도 안 일어나는 회귀가 조용히 지나간다 — 이 저장소가 반복해 밟은 병의 쓰기 쪽 얼굴이다.
describe("★쓰기 표면 — 토글·역할 선택이 실제로 서버를 부른다", () => {
  it("「안 맡김」 토글을 누르면 putPaoScopeAdgroup가 enabled=true로 호출된다", async () => {
    await renderApp();
    const btn = await screen.findByTitle(/이 그룹을 엔진에 맡깁니다/); // in_scope=false 쪽(Z폴드8와이드)
    fireEvent.click(btn);
    await waitFor(() => expect(putPaoScopeAdgroup).toHaveBeenCalled());
    expect(putPaoScopeAdgroup).toHaveBeenCalledWith(
      expect.objectContaining({
        campaign_id: "cmp-tpu",
        adgroup_id: "grp-z8wide",
        enabled: true,
      }),
    );
  });

  it("「맡김」 토글을 누르면 enabled=false로 호출된다 — 끄기는 해제가 아니다", async () => {
    await renderApp();
    const btn = await screen.findByTitle(/이 그룹을 끕니다/); // in_scope=true 쪽(S25FE)
    fireEvent.click(btn);
    await waitFor(() => expect(putPaoScopeAdgroup).toHaveBeenCalled());
    expect(putPaoScopeAdgroup).toHaveBeenCalledWith(
      expect.objectContaining({
        campaign_id: "cmp-tpu",
        adgroup_id: "grp-s25fe",
        enabled: false, // ★행은 남기고 끈다(삭제와 결과가 정반대)
      }),
    );
  });

  it("역할 select를 바꾸면 그 역할로 호출된다", async () => {
    await renderApp();
    const selects = await screen.findAllByRole("combobox");
    const target = selects.find((s) => (s as HTMLSelectElement).value === "accel")!;
    fireEvent.change(target, { target: { value: "brake" } });
    await waitFor(() => expect(putPaoScopeAdgroup).toHaveBeenCalled());
    expect(putPaoScopeAdgroup).toHaveBeenCalledWith(
      expect.objectContaining({ adgroup_id: "grp-s25fe", role: "brake" }),
    );
  });
});

// ── ★D-NAO-267 — 교란축 X9「램프업」이 화면 픽셀이 되는가 (M2 계약 §4-C S2-④·공통) ──
//
// 죽여야 할 표면 변이:
//   SUR-7 ProfitCell이 ramp_up을 그냥 「모름」으로 그림 — 신규 그룹의 초기 잡음이
//         「상품 원가 미연결」과 한 칸에 뭉개진다(둘은 사람이 할 일이 다르다)
//   SUR-8 캠페인 행의 `ramp_up_count` 배지 제거 — 총이익이 «덜 센» 값인데 그냥
//         「그만큼인 값」으로 읽힌다
//
// ★백엔드 테스트가 「응답까지 간다」를 지키고, 이 파일이 「응답이 픽셀이 된다」를 지킨다.
//   둘 중 하나만 있으면 그 사이 한 칸에서 조용히 끊긴다 — 이 저장소가 반복해 밟은 자리다.
const RAMP_ROSTER: PaoScopeRoster = {
  ...ROSTER,
  campaigns: [
    {
      ...ROSTER.campaigns[0],
      ramp_up_count: 1,
      adgroups: [
        ROSTER.campaigns[0].adgroups[0], // 평범한 그룹 — 대조군
        {
          ...ROSTER.campaigns[0].adgroups[1],
          adgroup_id: "grp-newly-created",
          name: "신규 그룹",
          baseline_days: 0,          // ★평시 관측 0일 = ref 63 §10 「baseline 부재」
          bep_roas: 1.711,           // BEP는 해석됐다 — 그래야 램프업이 이긴다
          gross_profit: null,
          gross_profit_low: null,
          gross_profit_high: null,
          profit_status: "ramp_up",
        },
      ],
    },
  ],
};

describe("★교란축 X9 — 「램프업」이 사람에게 닿는다", () => {
  it("SUR-7: ramp_up은 「램프업」으로 그린다 — 「모름」과 뭉개지 않는다", async () => {
    hoisted.roster = RAMP_ROSTER;
    await renderApp();
    expect(await screen.findByText("램프업")).toBeTruthy();
  });

  it("SUR-7b: 램프업 셀은 «왜»를 말한다 — 평시 관측 0일이라는 사유가 붙는다", async () => {
    hoisted.roster = RAMP_ROSTER;
    await renderApp();
    // 라벨만 있고 사유가 없으면 보는 사람은 코드를 열어야 안다
    expect(await screen.findByTitle(/평시.*관측이 0일/)).toBeTruthy();
  });

  it("SUR-8: 캠페인 행이 「몇 개가 빠졌는지」 말한다 — 총이익이 덜 센 값이기 때문", async () => {
    hoisted.roster = RAMP_ROSTER;
    await renderApp();
    expect(await screen.findByText(/램프업 1 제외/)).toBeTruthy();
  });

  it("램프업이 없으면 그 배지도 없다 — 항상 뜨면 아무 정보가 아니다", async () => {
    await renderApp(); // 기본 ROSTER(ramp_up_count: 0)
    await screen.findByRole("heading", { name: /PAO 스코프/ });
    expect(screen.queryByText(/램프업/)).toBeNull();
  });
});

// ── ★D-NAO-267 — 평시/주말/공휴일 분리가 화면 픽셀이 되는가 (적대 리뷰 1R P1-1 상환) ──
//
// 죽여야 할 표면 변이:
//   SUR-9  DayClassStrip 호출부 제거 — 백엔드가 세 칸을 실어 보내도 화면이 안 그린다
//   SUR-10 항등식 불일치 경고 렌더 제거 — 「합이 안 맞는데 맞는 것처럼」 보인다
describe("★평시·주말·공휴일 분리가 사람에게 닿는다", () => {
  it("SUR-9: 세 칸이 화면에 그려진다 — 섞인 값만 보이면 평시가 과소평가된다", async () => {
    await renderApp();
    expect(await screen.findByText("평시")).toBeTruthy();
    expect(await screen.findByText("주말")).toBeTruthy();
    expect(await screen.findByText("공휴일")).toBeTruthy();
  });

  it("칸별 광고비·ROAS가 실제 값으로 뜬다 — 직렬화가 끊기면 빈 칸이 된다", async () => {
    await renderApp();
    expect(await screen.findByText("80,000원")).toBeTruthy();  // 평시
    expect(await screen.findByText("18,000원")).toBeTruthy();  // 주말
    // 주말 ROAS 0.50은 BEP(1.711) 한참 아래 — 사람이 그 대비를 읽을 수 있어야 한다
    expect((await screen.findAllByText(/0\.50/)).length).toBeGreaterThan(0);
  });

  it("SUR-10: 항등식이 깨지면 화면이 말한다 — 조용히 넘어가면 검산이 아니다", async () => {
    hoisted.roster = {
      ...RAMP_ROSTER,
      weekend_holiday: {
        ...ROSTER.weekend_holiday,
        identity: { ...ROSTER.weekend_holiday.identity, ok: false },
      },
    };
    await renderApp();
    expect(await screen.findByText(/항등식 불일치/)).toBeTruthy();
  });

  it("항등식이 맞으면 경고를 띄우지 않는다 — 항상 뜨면 아무 정보가 아니다", async () => {
    await renderApp();
    await screen.findByText("평시");
    expect(screen.queryByText(/항등식 불일치/)).toBeNull();
  });
});
