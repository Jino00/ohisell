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
import { putPaoScopeAdgroup, putNaverCampaignAutoOperate } from "../lib/api";
import { kstDate } from "../lib/periodRange";

// ★로스터를 테스트마다 갈아끼운다 (D-NAO-267). vi.mock 팩토리는 호이스팅돼서 바깥 변수를
//   그냥 참조하면 초기화 전 접근이 된다 — vi.hoisted가 그 순서 문제의 공식 해법이다.
//   램프업 그룹을 공용 ROSTER에 «더하면» 기존 SUR-3·쓰기 표면 테스트의 findByTitle 단수
//   조회가 중복 매치로 깨진다(토글 버튼이 하나 더 생긴다). 그래서 더하지 않고 갈아끼운다.
const hoisted = vi.hoisted(() => ({ roster: null as unknown, preflight: null as unknown }));

/** ★H1 픽스처 — 「켜면 네이버 실쓰기가 나간다」는 그 경고. `optimizer='none'`이 아니라
 *  'ours'인데도 경고가 뜨는 게 아니라, **'none'이어도 떠야 한다**는 게 백엔드 회귀의 몫이고
 *  여기서 재는 것은 「그 문장이 화면 픽셀이 되나」다. */
const PREFLIGHT_WITH_REOPEN = {
  campaign_id: "cmp-tpu",
  auto_operate: false,
  optimizer: "ours" as const,
  safe_to_ignite: false,
  warnings: [{
    code: "reopen_due",
    message: "켜면 재심사 개방이 **1건** 대기 중이다 — 다음 08:50 레인이 네이버에서 제외키워드를 실제로 삭제한다.",
    detail: { terms: [{ search_term: "아이패드종이필름" }] },
  }],
};

const PREFLIGHT_CLEAN = {
  campaign_id: "cmp-tpu", auto_operate: false, optimizer: "ours" as const,
  safe_to_ignite: true, warnings: [] as { code: string; message: string }[],
};

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
    // ★H1(계약 P2) — 쓰기 표면을 재려면 함수 «자신»을 vi.fn()으로 잡아야 한다(SUR-5의 교훈).
    putNaverCampaignAutoOperate: vi.fn(async () => ({
      campaign_id: "cmp-tpu", optimizer: "ours" as const, auto_operate: true,
      mode: null, target_roas_override: null, memo: null, loss_policy: null,
      updated_at: "2026-08-31T16:00:00",
    })),
    fetchNaverCampaignIgnitionPreflight: vi.fn(async () => hoisted.preflight),
    // ★P2 넷째의 손(재개방 패널)이 이 화면 «안»에 산다 — 모킹을 안 두면 실제 fetch로 새어 나간다.
    fetchNaverSearchTermExclusions: vi.fn(async () => ({
      total: 0, summary_by_status: {}, today_excluded: 0, today_opened: 0, today_restored: 0,
      rows: [],
    })),
    reopenNaverSearchTermExclusion: vi.fn(async () => ({
      ok: true, id: 1, status: "probation", reason: null, probation_until: "2026-09-14",
    })),
    // 레이아웃이 부르는 헬스/스케줄러류는 조용히 실패시켜도 이 화면 판정과 무관하다.
    fetchHealth: vi.fn(async () => { throw new Error("not needed"); }),
    fetchSchedulerStatus: vi.fn(async () => { throw new Error("not needed"); }),
  };
});

beforeEach(() => {
  hoisted.roster = ROSTER; // 테스트마다 기본값으로 되돌린다 — 갈아끼운 게 새지 않게
  hoisted.preflight = PREFLIGHT_WITH_REOPEN;
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

// ── SUR-11: 표가 «칸이 맞게» 그려지는가 (2026-08-29, Jino 지적 「이거 칸 안맞잖아」) ──────
//
// ## 왜 이게 필요했나 — 위 19개가 전부 초록인데 화면은 깨져 있었다
//
// `AdgroupTable`이 `head`를 `<tr>`로 감싸 보냈는데 `Table`(`components/ui/Table.tsx:28`)이
// 이미 `<thead><tr>{head}</tr></thead>`로 감싼다 ⇒ **`<tr><tr><th>…</th></tr></tr>` 중첩**.
// ★깨지는 메커니즘은 **CSS 익명 테이블 박스 생성 규칙**이다(적대 리뷰가 초판의 「파서 fixup」
// 설명을 정정했다): `table-row`의 자식이 `table-cell`이 아니면 익명 셀로 감싸이므로 안쪽
// `<tr>`의 `<th>`들이 **본문과 같은 열 그리드에 참여하지 못한다.** HTML 파서 fixup이 아니라서
// React가 `createElement`로 만든 DOM에도 **그대로 적용된다**(리뷰어가 실제 Chromium 렌더로
// 증상 재현). 증상: 헤더는 왼쪽에 몰리고 숫자는 오른쪽으로 밀린다.
// 호출부 39곳 중 **여기만** 어긋나 있었다(나머지 38곳은 프래그먼트 `<>…</>`).
//
// ★교훈: 이 파일은 「사용자에게 닿는가」를 재는 표면 테스트인데도 **텍스트 존재만 물어서**
//   못 잡았다. **「값이 화면에 있다」와 「사람이 읽을 수 있게 배치됐다」는 다른 질문이다.**
//   그래서 이 블록은 내용이 아니라 **구조**를 잰다.
describe("★SUR-11: 광고그룹 표의 헤더와 본문이 같은 열 그리드에 있다", () => {
  it("thead 안에 `<tr>`이 정확히 1개다 — 중첩되면 헤더가 열 정렬에서 떨어져 나간다", async () => {
    const { container } = await renderApp();
    await screen.findByTitle(/이 그룹을 끕니다/); // 표가 그려질 때까지 기다린다
    const theads = Array.from(container.querySelectorAll("thead"));
    expect(theads.length).toBeGreaterThan(0);
    for (const thead of theads) {
      expect(thead.querySelectorAll("tr").length).toBe(1);
      // 중첩이면 바깥 tr 안에 또 tr이 있다 — 그 모양 자체를 직접 금지한다
      expect(thead.querySelectorAll("tr tr").length).toBe(0);
    }
  });

  // ⚠️★정직 기록 — 아래 테스트는 **이번 버그를 못 잡는다**(변이 주입으로 확인, 2026-08-29).
  //   jsdom은 React가 `createElement`/`appendChild`로 만든 중첩 `<tr>`을 그대로 두므로
  //   `thead th`=9 · `tbody td`=9로 **개수는 여전히 맞다** — 깨지는 건 «브라우저 레이아웃»이지
  //   개수가 아니다. 이번 버그를 죽이는 것은 위의 중첩 검사 하나뿐이다.
  //   그래도 이 테스트를 남기는 이유: **다른 실패 모드**(Th/Td를 한쪽만 추가·삭제)를 지킨다.
  //   ★「살아남은 변이」를 지우지 않고 적어 두는 것이 이 저장소 관례다 — 무엇을 «안» 지키는지
  //   모르는 초록이 가장 위험하다.
  it("헤더 칸 수 == 본문 칸 수 — 하나라도 어긋나면 숫자가 다른 라벨 아래에 선다", async () => {
    const { container } = await renderApp();
    await screen.findByTitle(/이 그룹을 끕니다/);
    const tables = Array.from(container.querySelectorAll("table"));
    expect(tables.length).toBeGreaterThan(0);
    let checked = 0;
    for (const t of tables) {
      const headCells = t.querySelectorAll("thead th").length;
      const firstBodyRow = t.querySelector("tbody tr");
      if (!firstBodyRow) continue;                              // 본문 없는 표는 정렬 대상 아님
      if (firstBodyRow.querySelector("td[colspan]")) continue;  // 빈 상태 안내 행 제외
      expect(firstBodyRow.querySelectorAll("td").length).toBe(headCells);
      checked += 1;
    }
    // ★0건 통과 금지 — 「검사했는데 깨끗하다」와 「검사가 아무것도 안 봤다」는 같은 초록이다(교훈 #123)
    expect(checked).toBeGreaterThan(0);
  });
});

// ── ★H1(계약 P2) 엔진 스위치가 사람에게 닿는가 ──────────────────────────────
//
// 죽여야 할 표면 변이:
//   SUR-11 스위치 버튼 자체를 안 그림 — 「API는 생겼는데 누를 손이 없다」의 재발
//   SUR-12 확인창을 건너뛰고 바로 켬 — preflight가 만들어지지만 아무도 안 본다
//   SUR-13 경고 문구를 화면에 안 실음 — 「1건 대기 중」이 백엔드에만 있고 사람은 모른다
//   SUR-14 「그래도 켠다」가 putNaverCampaignAutoOperate를 안 부름 — 눌러도 아무 일도 안 남
//   SUR-15 끄기에도 확인창을 끼움 — 킬스위치에 마찰을 두면 급할 때 못 끈다
describe("★H1 엔진 스위치 — 켜는 손·끄는 손이 실제로 있고, 켜기 전에 무엇이 열리는지 말한다", () => {
  it("SUR-11: 엔진이 꺼진 캠페인엔 「엔진 켜기」 버튼이 화면에 있다", async () => {
    await renderApp();
    expect(await screen.findByRole("button", { name: "엔진 켜기" })).toBeTruthy();
  });

  it("SUR-12·13: 켜기를 누르면 «먼저» 확인창이 뜨고 경고 문구가 화면 픽셀이 된다", async () => {
    await renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "엔진 켜기" }));
    // ★확인 단계에선 아직 쓰기가 나가면 안 된다
    expect(vi.mocked(putNaverCampaignAutoOperate)).not.toHaveBeenCalled();
    expect(await screen.findByText(/재심사 개방이/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "그래도 켠다" })).toBeTruthy();
  });

  it("SUR-14: 「그래도 켠다」를 눌러야 실제로 켜진다", async () => {
    await renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "엔진 켜기" }));
    fireEvent.click(await screen.findByRole("button", { name: "그래도 켠다" }));
    await waitFor(() => {
      expect(vi.mocked(putNaverCampaignAutoOperate)).toHaveBeenCalledWith({
        campaignId: "cmp-tpu", autoOperate: true,
      });
    });
  });

  it("경고 0건이어도 확인창은 뜬다 — 「안 했다」와 「깨끗하다」가 같아 보이면 안 된다(교훈 #123)", async () => {
    hoisted.preflight = PREFLIGHT_CLEAN;
    await renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "엔진 켜기" }));
    expect(await screen.findByText(/경고 0건/)).toBeTruthy();
  });

  it("SUR-15: 켜져 있으면 「엔진 끄기」이고, 끄기는 확인창 없이 즉시 나간다", async () => {
    hoisted.roster = {
      ...ROSTER,
      campaigns: [{ ...ROSTER.campaigns[0], auto_operate: true }],
    };
    await renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "엔진 끄기" }));
    await waitFor(() => {
      expect(vi.mocked(putNaverCampaignAutoOperate)).toHaveBeenCalledWith({
        campaignId: "cmp-tpu", autoOperate: false,
      });
    });
  });

  it("★안내 문구가 「켜는 것은 이 화면이 아니다」라고 말하지 않는다 — 화면이 자기 기능을 부정하면 안 된다", async () => {
    await renderApp();
    await screen.findByRole("button", { name: "엔진 켜기" });
    expect(screen.queryByText(/켜는 것은 이 화면이 아니라 별도 결정입니다/)).toBeNull();
  });

  it("★중첩 버튼이 없다 — `<button>` 안의 `<button>`은 유효하지 않은 HTML인데 jsdom은 안 막는다", async () => {
    // ★이 회귀의 유래: H1 초판이 EngineSwitch를 캠페인 행 «전체»인 `<button>` 안에 넣었다.
    //   테스트 27건이 전부 초록이었고 vitest도 조용했다 — jsdom이 HTML 중첩 규칙을 강제하지
    //   않기 때문이다. 「테스트가 통과한다」가 「구조가 옳다」를 뜻하지 않는 자리라 명시적으로 센다.
    const { container } = await renderApp();
    await screen.findByRole("button", { name: "엔진 켜기" });
    const nested = container.querySelectorAll("button button");
    expect(
      Array.from(nested).map((b) => b.textContent?.slice(0, 40)),
    ).toEqual([]);
  });
});

// ──────────────────────────────────────────────────────────────────────────────
// 설계서 §7½ 1단계 「도달과 이름」 (PR #665 · 적대 리뷰 P2-2)
//
// ★왜 여기에 더하나: 리뷰가 주입한 M8b(배지가 상수 대신 하드코딩으로 갈라짐)·M8c(배지 렌더
//   제거)가 **전부 초록으로 살아남았다.** 이 파일은 28건이나 있으면서 배지 «낱말»은 한 번도
//   안 봤고, `paoNamingAndReach.test.tsx`의 전수 grep은 'MOP'를 «안 넣는» 되돌림
//   (「가동」·「우리·정지」)을 원리적으로 못 본다. 둘 사이의 틈이 그 자리다.
// ★그리고 「상수를 import 하는가」로는 못 잡는다 — import는 남기고 사용처만 갈라지면 그만이다.
//   **존재 게이트는 성숙 게이트가 아니다.** 그래서 렌더로 잰다.
const OPTIMIZER_ROSTER: PaoScopeRoster = {
  ...ROSTER,
  campaigns: (
    [
      ["cmp-ours-on", "맡기고 가동중", "ours", true],
      ["cmp-ours-off", "맡겼으나 멈춤", "ours", false],
      ["cmp-third", "제3자 소유", "mop", false],
      ["cmp-manual", "손으로만", "none", false],
    ] as const
  ).map(([campaign_id, name, optimizer, auto_operate]) => ({
    ...ROSTER.campaigns[0],
    campaign_id, name, optimizer, auto_operate,
    has_scope: false, scoped_count: 0, adgroups: [],
  })),
};

// ★타임아웃을 명시한다(기본 1s → 5s): 이 세 묶음은 `App`을 통째로 렌더하므로 CI처럼
//   부하가 걸린 곳에서 기본값이 아슬아슬하다. 완료 QA가 전체 스위트 8회 중 1회
//   `findAllByText("PAO 가동")` 타임아웃을 관측했다(단독 실행 땐 재현 안 됨).
//   **거짓 빨강은 진짜 빨강보다 나쁘다** — 몇 번 겪으면 사람이 빨강을 안 보게 된다.
describe("★설계서 §7½ 1단계 — 탭바에서 이 화면에 «닿는다»", () => {
  it("SUR-12: 상단 탭바에 「PAO 스코프」 링크가 있고 이 화면을 가리킨다", async () => {
    // ★이 화면은 라우트도 컴포넌트도 **이미 다 있었는데** 탭에 링크가 없어서 아무도 못 갔다.
    //   광고그룹 On/Off 스위치가 안 쓰인 이유가 기능 부재가 아니라 도달 불능이었다(§7-1 실측).
    await renderApp();
    expect(
      (await screen.findByRole("link", { name: "PAO 스코프" }, { timeout: 5000 })).getAttribute("href"),
    ).toBe("/naver-ad/scope");
  });
});

describe("★설계서 §7½ 1단계 — 닿은 자리에서 「PAO」라고 부른다", () => {
  it.each([
    ["PAO 가동", "ours ∧ auto_operate"],
    ["PAO 정지", "ours 인데 멈춤 — 「맡김」과 「손댐」은 다른 상태다(§7-3)"],
    ["제3자(대행사)", "mop = 제3자 소유, 우리는 안 건드림"],
    ["수동", "none — 이미 멀쩡했으므로 그대로 둔다"],
  ])("SUR-13: 관할 배지가 「%s」로 뜬다 (%s)", async (label) => {
    hoisted.roster = OPTIMIZER_ROSTER;
    await renderApp();
    expect((await screen.findAllByText(label, {}, { timeout: 5000 })).length).toBeGreaterThan(0);
  });

  it("SUR-14: ★옛 라벨은 배지에 한 글자도 없다 — 하드코딩으로 갈라지면 여기서 죽는다", async () => {
    hoisted.roster = OPTIMIZER_ROSTER;
    const { container } = await renderApp();
    await screen.findAllByText("PAO 가동", {}, { timeout: 5000 });
    // 캠페인 «이름»에 든 낱말은 데이터라 셈에서 빼고, 배지가 쓰는 라벨만 본다.
    const badgeTexts = [...container.querySelectorAll("span")]
      .map((el) => el.textContent?.trim() ?? "");
    for (const old of ["우리·정지", "우리", "MOP", "우리 MOP", "원본 MOP", "가동"]) {
      expect(badgeTexts, `옛 라벨 「${old}」이(가) 배지에 되살아났다`).not.toContain(old);
    }
  });
});

// ──────────────────────────────────────────────────────────────────────────────
// 기간 바 — Jino 2026-09-02 23:35: *"캘린더가 너무 부실하다. 대시보드에 있는 캘린더처럼 만들자"*
//
// ★이 화면은 `7일 / 21일 / 51일` 버튼 3개를 **자기가 따로** 들고 있었다. 공용
//   `PeriodRangeBar`의 머리말이 스스로 적어 둔 이유가 그대로 적용된다 —
//   *"같은 UI를 두 화면이 각자 들고 있으면 곧 갈라진다"*.
// ★★그리고 날짜 입력을 주는 순간 새 위험이 생긴다: 서버가 `days`만 받으면 «고른 날짜»와
//   «실제 조회 창»이 갈라진다. 그러면 화면은 **사용자가 고른 구간을 보여줬다고 믿게 만든다.**
//   그래서 여기서 재는 것은 「달력이 예쁜가」가 아니라 **「고른 날짜가 요청에 실리는가」**다.
describe("★기간 바 — 대시보드와 같은 물건을 쓴다", () => {
  it("날짜 입력 두 칸과 프리셋이 뜬다", async () => {
    await renderApp();
    const inputs = await waitFor(() => {
      const found = document.querySelectorAll<HTMLInputElement>('input[type="date"]');
      expect(found.length).toBeGreaterThanOrEqual(2);
      return found;
    });
    expect(inputs.length).toBeGreaterThanOrEqual(2);
    for (const label of ["어제", "7일", "30일"]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
  });

  it("★고른 날짜가 «요청»에 실린다 — 안 실리면 화면이 거짓말한다", async () => {
    const { fetchPaoScopeRoster } = await import("../lib/api");
    await renderApp();
    await waitFor(() => expect(vi.mocked(fetchPaoScopeRoster)).toHaveBeenCalled());
    const [first] = vi.mocked(fetchPaoScopeRoster).mock.calls.at(-1)!;
    expect(first, "요청에 날짜 구간이 없다 — 서버는 기본 창을 돌려준다").toEqual(
      expect.objectContaining({ dateFrom: expect.any(String), dateTo: expect.any(String) }),
    );

    const [fromInput] = Array.from(document.querySelectorAll<HTMLInputElement>('input[type="date"]'));
    const before = vi.mocked(fetchPaoScopeRoster).mock.calls.length;
    fireEvent.change(fromInput, { target: { value: "2026-08-10" } });
    await waitFor(() =>
      expect(vi.mocked(fetchPaoScopeRoster).mock.calls.length).toBeGreaterThan(before));
    const [after] = vi.mocked(fetchPaoScopeRoster).mock.calls.at(-1)!;
    expect(after).toEqual(expect.objectContaining({ dateFrom: "2026-08-10" }));
  });

  it("★서버가 창을 잘랐으면 «왜»가 화면에 뜬다 — 조용히 자르면 고른 날짜가 사라진 것과 같다", async () => {
    hoisted.roster = {
      ...ROSTER,
      window: {
        ...ROSTER.window, clamped: true,
        note: "2026-09-02까지 고르셨지만 오늘은 전환이 아직 정착 전이라 총이익이 실제보다 적게 보입니다 — 2026-09-01까지로 보여드립니다.",
      },
    };
    await renderApp();
    // ★«정착 전»만 보면 안 된다 — 기간 바에 상시로 붙는 안내 문구에도 같은 말이 있어서
    //   경고를 «안 그려도» 통과한다(내 초판이 그랬다). 서버 note에만 있는 말로 잰다.
    await waitFor(() => expect(screen.getByText(/고르셨지만/)).toBeTruthy());
    expect(screen.getByText(/보여드립니다/)).toBeTruthy();
  });

  it("★note가 있어도 clamped가 아니면 경고가 아니다 — 계약은 «잘랐을 때»만이다", async () => {
    hoisted.roster = {
      ...ROSTER,
      window: { ...ROSTER.window, clamped: false, note: "고르셨지만 참고용 안내입니다" },
    };
    await renderApp();
    await waitFor(() => expect(screen.getByText("01. 갤럭시_지문방지_TPU")).toBeTruthy());
    expect(screen.queryByText(/고르셨지만/), "안 잘랐는데 경고가 떴다").toBeNull();
  });

  it("자르지 않았으면 경고를 띄우지 않는다 — 늘 뜨는 경고는 아무도 안 읽는다", async () => {
    await renderApp();
    // 기본 로스터(clamped 없음)가 그려질 때까지 기다린 뒤에 «없음»을 잰다 —
    // 렌더 전에 재면 아무 경고도 없는 게 당연해서 아무것도 보증하지 않는다.
    await waitFor(() => expect(screen.getByText("01. 갤럭시_지문방지_TPU")).toBeTruthy());
    expect(screen.queryByText(/고르셨지만/), "안 잘랐는데 경고가 떴다").toBeNull();
  });
});

// ──────────────────────────────────────────────────────────────────────────────
// 적대 리뷰 P1-1·P1-2 — 「무엇을 그리나」가 아니라 **「무엇을 요청하나」**를 잰다
//
// ★P1-1이 통과한 이유가 여기 있다: 방어선이 「목 로스터의 `clamped`를 화면이 그리나」에만
//   서 있었고 **「이 화면이 실제로 어떤 창을 요청하나」에는 한 줄도 없었다.** 그래서 프리셋이
//   전부 오늘을 보내 서버가 매번 창을 자르는데도 1,433건이 전건 초록이었다.
// ★★그러면 「예외일 때만 뜨는 경고」가 상시 경고가 되고, 서버 자백문이 *"…까지 고르셨지만"*
//   이라 **사용자가 하지 않은 입력을 사용자 탓으로** 돌린다. 버튼 라벨(7일)과 실제 창
//   길이(6일)도 어긋난다.
describe("★이 화면이 «실제로 요청하는 창» (적대 리뷰 P1-1·P1-2)", () => {
  const lastArgs = async () => {
    const { fetchPaoScopeRoster } = await import("../lib/api");
    await waitFor(() => expect(vi.mocked(fetchPaoScopeRoster)).toHaveBeenCalled());
    return vi.mocked(fetchPaoScopeRoster).mock.calls.at(-1)![0] as
      { dateFrom?: string; dateTo?: string };
  };
  const daysBetween = (f: string, t: string) =>
    Math.round((Date.parse(`${t}T00:00:00Z`) - Date.parse(`${f}T00:00:00Z`)) / 86_400_000) + 1;

  it("★시작 창은 «어제로 끝나는 21일»이다 — 열자마자 보던 것이 안 바뀐다", async () => {
    await renderApp();
    const a = await lastArgs();
    expect(a.dateTo, "창이 오늘로 끝난다 — D-0 제외 관례 위반").toBe(kstDate(-1));
    expect(daysBetween(a.dateFrom!, a.dateTo!), "시작 창이 21일이 아니다").toBe(21);
  });

  it.each([
    ["7일", 7], ["21일", 21], ["30일", 30], ["90일", 90],
  ])("★프리셋 「%s」은 오늘을 안 보내고 길이가 라벨과 같다", async (label, want) => {
    await renderApp();
    const btn = await screen.findByRole("button", { name: label });
    fireEvent.click(btn);
    await waitFor(async () => {
      const a = await lastArgs();
      // ★오늘을 보내면 서버가 자르고 「예외 경고」가 상시가 된다 — 그게 P1-1이었다.
      expect(a.dateTo, `${label}이 오늘을 보낸다`).toBe(kstDate(-1));
      expect(daysBetween(a.dateFrom!, a.dateTo!), `${label} 창 길이가 라벨과 다르다`).toBe(want);
    });
  });

  it("「어제」는 하루짜리 창이다", async () => {
    await renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "어제" }));
    await waitFor(async () => {
      const a = await lastArgs();
      expect([a.dateFrom, a.dateTo]).toEqual([kstDate(-1), kstDate(-1)]);
    });
  });

  it("★축 이름이 화면에 있다 — 안 적으면 사용자는 자기가 아는 축으로 읽는다", async () => {
    await renderApp();
    expect(await screen.findByText("성과 발생일")).toBeTruthy();
  });
});
