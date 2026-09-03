// @vitest-environment jsdom
//
// paoLockedCalendar.test.tsx — **잠긴 캘린더**(`datesReadOnly`)가 실제로 하는 일을 잰다.
//
// ## 왜 이 파일이 있나 (적대 리뷰 1R P1-1·P1-2, 2026-09-03)
//
// PAO 캘린더 통일에서 두 화면(제외 후보·개선 타임라인)은 날짜 칸을 잠갔다 — 그 창의 끝점을
// 서버가 규칙으로 정하기 때문이다(제외는 전환 성숙 지연만큼 당기고, 타임라인은 D-0을 뺀다).
// 잠근 대신 프리셋은 날짜가 아니라 **창의 «길이»**를 바꾸는데, 그 배선(`onPreset`)을
// 무력화해도 **프론트 1,442건이 전부 초록이었다.** 그러면 두 화면의 프리셋 버튼은
// 「하이라이트도 안 바뀌고 데이터도 안 바뀌는 죽은 버튼」이 되는데 아무도 안 잡는다.
// 성과 타임라인의 기간 바를 **성공 렌더 경로에서 통째로 지우는 변이**도 무증상 통과했다.
//
// ⇒ 이 파일이 재는 것은 셋이다:
//   ① 잠긴 칸에 **서버가 낸 창**이 그대로 찍히는가(프론트가 지어낸 값이 아닌가)
//   ② 칸이 실제로 **잠겨 있는가**(자유 날짜를 열면 서버 규칙이 우회된다)
//   ③ 프리셋을 누르면 **창의 길이가 바뀌어 재조회가 나가는가**
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => {
  const pending = () => new Promise<never>(() => {});
  return {
    pending,
    listCalls: [] as unknown[],
    timelineCalls: [] as unknown[],
  };
});

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  getSearchTermExclusionList: (p: unknown) => {
    h.listCalls.push(p);
    return Promise.resolve(LIST);
  },
  getSearchTermExclusionSurvival: () => h.pending(),
  getSearchTermExclusionSlots: () => h.pending(),
  getSearchTermExclusionScorecard: () => h.pending(),
  postSearchTermExecution: () => h.pending(),
  postSearchTermExecutionDetect: () => h.pending(),
  fetchNaverPerformanceTimeline: (p: unknown) => {
    h.timelineCalls.push(p);
    return Promise.resolve(TIMELINE);
  },
}));

import NaverAdExclusionList from "./NaverAdExclusionList";
import { ImprovementTimelineSection } from "./NaverAdPerformance";

const bucket = (terms: number, cost: number) => ({ terms, cost });

/** ★서버 창은 «오늘까지»가 아니다 — 성숙 지연(3일)만큼 당겨져 있다. 어떤 프리셋 창과도
 *  문자로 같지 않다는 것이 이 픽스처의 핵심이다(그래서 날짜 비교로 하이라이트를 못 정한다). */
const LIST = {
  window: { days: 30, from: "2026-07-10", to: "2026-08-09" },
  freshness: { latest_ad_date: "2026-08-09", latest_synced_at: "2026-08-12T07:40:00",
               as_of: "2026-08-12", lag_days: 3 },
  maturity: { lag_days: 3, excluded_from: "2026-08-10", excluded_to: "2026-08-12",
              excluded_terms: 751, excluded_cost: 1_000_000, why: "최근 3일은 전환이 덜 잡힌다" },
  totals: { terms: 7150, cost: 20_524_919, conv_amt: 0 },
  gates: { min_click: 10, round_cap: 50 },
  candidates: [],
  candidate_cost: 0,
  buckets: {
    already_excluded: bucket(1, 31_411),
    insufficient_sample: bucket(3886, 0),
    bep_unknown: bucket(70, 0),
    powerlink_undecidable: bucket(2255, 0),
    profitable: bucket(876, 0),
    capped_out: { ...bucket(13, 0), why: "다음 회차 대상" },
    maturity_excluded: bucket(751, 0),
  } as Record<string, { terms: number; cost: number; why?: string }>,
  revert_howto: "콘솔에서 삭제하면 복구된다",
  generated_at: "2026-08-12T20:45:00",
};

/** ★타임라인 창도 서버가 낸다(`window`). 화면이 `as_of`·`days`로 지어내면 「화면이 말하는
 *  창」과 「서버가 쓴 창」이 두 벌이 되므로, 여기서는 **일부러 days와 안 맞는 날짜**를 준다 —
 *  프론트가 계산해서 그리면 이 픽스처와 다른 값이 나와 테스트가 죽는다. */
const TIMELINE = {
  as_of: "2026-09-03",
  days: 90,
  window: { from: "2026-06-05", to: "2026-09-03" },
  campaign_id: null,
  catalog_available: true,
  undated_catalog_count: 0,
  event_count: 0,
  timeline: [],
  retro: { window_days: 7, n: 0, improved: 0, declined: 0, neutral: 0, sentence: "아직 없습니다." },
  data_note: "관찰입니다.",
};

beforeEach(() => {
  h.listCalls = [];
  h.timelineCalls = [];
});
afterEach(cleanup);

describe("제외 후보 — 잠긴 캘린더", () => {
  const renderPage = () =>
    render(<MemoryRouter><NaverAdExclusionList /></MemoryRouter>);

  const bar = async () =>
    (await screen.findByText(/끝점은 서버가 정합니다/, {}, { timeout: 5000 }))
      .closest("section")!;

  it("① 날짜 두 칸에 **서버가 낸 창**이 그대로 찍힌다", async () => {
    renderPage();
    const b = await bar();
    // ★프론트가 지어낸 값이면 여기가 깨진다 — 서버 창은 오늘 기준 어떤 프리셋과도 다르다.
    expect(within(b).getByDisplayValue("2026-07-10")).toBeTruthy();
    expect(within(b).getByDisplayValue("2026-08-09")).toBeTruthy();
  });

  it("② 날짜 칸이 **잠겨 있다** — 열면 서버의 성숙 지연 규칙이 우회된다", async () => {
    renderPage();
    const b = await bar();
    for (const v of ["2026-07-10", "2026-08-09"]) {
      const input = within(b).getByDisplayValue(v) as HTMLInputElement;
      expect(input.disabled).toBe(true);
      expect(input.readOnly).toBe(true);
    }
  });

  it("③ 프리셋을 누르면 **창의 길이**가 바뀌어 재조회가 나간다", async () => {
    renderPage();
    const b = await bar();
    await waitFor(() => expect(h.listCalls.length).toBeGreaterThan(0));
    expect((h.listCalls[0] as { days: number }).days).toBe(30);

    fireEvent.click(within(b).getByRole("button", { name: "60일" }));

    // ★날짜가 아니라 days가 바뀌어야 한다. onFrom/onTo는 no-op이라 날짜를 보내면 아무 일도
    //   안 일어나고, 버튼은 「눌리는데 아무것도 안 바뀌는」 죽은 버튼이 된다.
    await waitFor(() =>
      expect(h.listCalls.map((c) => (c as { days: number }).days)).toContain(60));
  });

  it("③-b 지금 걸린 길이의 버튼이 «눌린 것»으로 보인다", async () => {
    renderPage();
    const b = await bar();
    // 기본 30일 — 날짜 비교로는 못 정한다(서버 창이 성숙 지연만큼 당겨져 있다).
    const btn30 = within(b).getByRole("button", { name: "30일" });
    const btn60 = within(b).getByRole("button", { name: "60일" });
    // ★DOM 노드는 살아 있어 클릭 뒤 className이 바뀐다 — «눌린 모습»을 문자열로 붙잡아 둔다.
    const activeClass = btn30.className;
    const idleClass = btn60.className;
    expect(activeClass).not.toBe(idleClass);

    fireEvent.click(btn60);
    await waitFor(() => {
      expect(within(b).getByRole("button", { name: "60일" }).className).toBe(activeClass);
      expect(within(b).getByRole("button", { name: "30일" }).className).toBe(idleClass);
    });
  });

  it("④ 잠근 «이유»를 화면이 말한다 — 없는 자유도를 있는 척하지 않는다", async () => {
    renderPage();
    expect(await screen.findByText(/전환이 아직 덜 잡혀 판정에서 뺐습니다/)).toBeTruthy();
  });
});

describe("개선 타임라인 — 잠긴 캘린더", () => {
  const renderSection = () =>
    render(<MemoryRouter><ImprovementTimelineSection campaignId="" /></MemoryRouter>);

  it("① 데이터가 **성공적으로 로드된 뒤에도** 기간 바가 화면에 있다", async () => {
    // ★적대 리뷰 1R P1-2: 성공 렌더 경로에서 `{periodBar}`를 지워도 1,442건이 전부 초록이었다.
    //   이 화면을 여는 가장 흔한 상태가 바로 그 경로다.
    renderSection();
    expect(await screen.findByText("우리가 바꾼 것들과 그 전후", {}, { timeout: 5000 })).toBeTruthy();
    expect(screen.getByText(/끝점은 서버가 정합니다/)).toBeTruthy();
  });

  it("② 날짜 두 칸에 **서버가 낸 window**가 찍힌다(as_of·days로 지어내지 않는다)", async () => {
    renderSection();
    const b = (await screen.findByText(/끝점은 서버가 정합니다/, {}, { timeout: 5000 }))
      .closest("section")!;
    // days=90인데 창은 2026-06-05~09-03이다 — 프론트가 계산하면 이 값이 안 나온다.
    expect(within(b).getByDisplayValue("2026-06-05")).toBeTruthy();
    expect(within(b).getByDisplayValue("2026-09-03")).toBeTruthy();
  });

  it("③ 프리셋을 누르면 days가 바뀌어 재조회가 나간다", async () => {
    renderSection();
    const b = (await screen.findByText(/끝점은 서버가 정합니다/, {}, { timeout: 5000 }))
      .closest("section")!;
    await waitFor(() => expect(h.timelineCalls.length).toBeGreaterThan(0));

    fireEvent.click(within(b).getByRole("button", { name: "180일" }));

    await waitFor(() =>
      expect(h.timelineCalls.map((c) => (c as { days: number }).days)).toContain(180));
  });

  it("④ 종전 버튼 셋(30·90·180일)이 하나도 안 사라졌다", async () => {
    renderSection();
    const b = (await screen.findByText(/끝점은 서버가 정합니다/, {}, { timeout: 5000 }))
      .closest("section")!;
    for (const d of ["30일", "90일", "180일"]) {
      expect(within(b).getByRole("button", { name: d })).toBeTruthy();
    }
  });
});
