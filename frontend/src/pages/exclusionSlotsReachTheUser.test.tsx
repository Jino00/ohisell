// @vitest-environment jsdom
//
// exclusionSlotsReachTheUser.test.tsx — 제외 슬롯 In/Out이 **사람에게 닿는가**
// (Jino 요구 ③ 2026-09-02 09:57 · 설계서 §5-4 · §7½ 2단계)
//
// ## 왜 이 파일이 있나
//
// 이 기능의 백엔드는 D-NAO-264부터 **다 있었는데 프론트 호출이 0이었다.** 값은 만들어지는데
// 아무도 못 봤다 — 이 저장소가 반복해 밟은 바로 그 병이고, 단위 테스트는 원리적으로 못 잡는다
// (「함수가 값을 만드나」는 묻지만 「사람이 그걸 보나」는 못 묻는다).
// 그래서 `App`을 통째로 `/naver-ad/exclusion-list`에서 렌더한다 — 탭·라우트·페이지·호출부·
// 렌더가 한 줄로 이어져야만 통과한다.
//
// ## 픽스처는 실측값이다 (2026-09-02 20:0x KST, prod 읽기 전용 SQL)
//
//   groups 1,013 · live_used 5,757 · ours 2 · agency 3,984 · other_source 0
//   ⇒ 미귀속 1,771 (30.8%) · exhausted 6 · unknown 10
//   observed_from 2026-08-24 09:35  ← ★10그룹이 9일째 멈춰 있다
//   observed_to   2026-09-02 09:35
//
// ★`stale`이 0인데 관측은 9일 전이다 — 모순이 아니라 `_state`가 「모름을 여유보다 먼저 본다」는
//   규율대로 unknown을 먼저 돌려주기 때문이다. 그래서 «얼마나 오래»가 카운터에 안 잡히고,
//   그 사실을 화면이 말해야 한다.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { getSearchTermExclusionSlots, fetchNaverSearchTermExclusions } from "../lib/api";
import { sweepLabel, termTitle } from "../lib/exclusionSlots";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  getSearchTermExclusionSlots: vi.fn(),
  // ★나머지 셋은 «영원히 pending»으로 둔다 — 이 파일이 재는 것은 슬롯 패널이고, 다른
  //   섹션의 픽스처를 어설프게 지어내면 그 섹션이 터지면서 «내 패널이 안 뜬 것»과
  //   구별이 안 된다. pending이면 그 섹션들은 Loading으로 서고 화면은 정상이다.
  getSearchTermExclusionList: vi.fn(() => new Promise(() => {})),
  getSearchTermExclusionSurvival: vi.fn(() => new Promise(() => {})),
  getSearchTermExclusionScorecard: vi.fn(() => new Promise(() => {})),
  fetchNaverSearchTermExclusions: vi.fn(),
}));

const row = (o: Record<string, unknown>) => ({
  adgroup_id: "grp-1", campaign_id: "cmp-1", campaign_name: "● 02. 갤럭시_보급형_M",
  name: "02. S23FE", state: "ok",
  used: 10, cap: 70, remaining: 60, usage_pct: 14.3,
  ours: 0, agency: 10, other_source: 0, unattributed: 0, grades: {},
  inflow_30d: 0, inflow_30d_ours: 0, inflow_30d_agency: 0,
  exhaust_eta_days: null, exhaust_eta_reason: "유입이 없어 예상일을 낼 수 없다",
  probe_status: 200, observed_at: "2026-09-02T09:35:00",
  ...o,
});

const LIVE = {
  cap: 70,
  as_of: "2026-09-02T20:00:27.845275",          // ★응답 «생성» 시각 — 기준 시각이 아니다
  groups: 1013, exhausted: 6, unknown: 10, stale: 0, healthy: false,
  observed_from: "2026-08-24T09:35:00.002340",  // ★9일째 멈춘 그룹
  observed_to: "2026-09-02T09:35:00.005685",
  rows: [
    row({ adgroup_id: "grp-full", name: "02. S23FE", state: "exhausted",
          used: 70, remaining: 0, usage_pct: 100, agency: 70,
          exhaust_eta_days: 0, exhaust_eta_reason: "이미 70/70 — 남은 칸이 없다" }),
    row({ adgroup_id: "grp-unknown", name: "09. 기타상품", state: "unknown",
          used: null, remaining: null, usage_pct: null, unattributed: null,
          observed_at: "2026-08-24T09:35:00" }),
    row({ adgroup_id: "grp-ok", name: "01. 갤럭시_TPU", used: 12, remaining: 58, agency: 12 }),
  ],
  rows_truncated: 1010,
  totals: { used: 5757, ours: 2, agency: 3984, other_source: 0, unattributed: 1771, capacity: 70910 },
  reclaim_note: "대행사 칸은 우리가 반납하지 않는다 — 소유권 분리 협의 전엔 금지선이다.",
};

const term = (o: Record<string, unknown> = {}) => ({
  id: 1, campaign_id: "cmp-1", adgroup_id: "grp-full", search_term: "S23보호필름",
  status: "excluded", cycle: 1, source: "console_import",
  excluded_at: "2026-08-13T10:00:00",            // ★우리가 «편입한» 시각
  console_excluded_at: "2024-08-27T09:00:00",    // ★대행사가 «실제로 건» 시각
  next_review_at: null, probation_until: null, reopen_block_reason: null,
  ...o,
});

const drill = (rows: unknown[]) => ({
  total: rows.length, summary_by_status: { excluded: rows.length },
  today_excluded: 0, today_opened: 0, today_restored: 0, rows,
});

beforeEach(() => {
  vi.mocked(getSearchTermExclusionSlots).mockResolvedValue(LIVE as never);
  vi.mocked(fetchNaverSearchTermExclusions).mockResolvedValue(drill([term()]) as never);
  window.history.pushState({}, "", "/naver-ad/exclusion-list");
});
afterEach(cleanup);

async function renderApp() {
  const { default: App } = await import("../App");
  return render(<App />);
}

const panel = async () =>
  (await screen.findByText("제외 슬롯 In/Out — 더 걸 칸이 남았는가", {}, { timeout: 5000 }))
    .closest("div")!.parentElement!;

describe("★도달 — 백엔드가 만들던 값이 처음으로 화면에 선다", () => {
  it("탭 → 라우트 → 페이지 → 호출부가 한 줄로 이어진다", async () => {
    await renderApp();
    expect((await screen.findByRole("link", { name: "검색어 제외" })).getAttribute("href"))
      .toBe("/naver-ad/exclusion-list");
    await waitFor(() => expect(vi.mocked(getSearchTermExclusionSlots)).toHaveBeenCalled(),
      { timeout: 5000 });
    await panel();
  });
});

describe("★설계서 §5-4의 넷 — 하나라도 빠지면 화면이 거짓말을 한다", () => {
  it("①·②·③ 귀속 3분할이 뜨고 미귀속이 0으로 뭉개지지 않는다", async () => {
    await renderApp();
    const p = await panel();
    // 미귀속 1,771 = 라이브 5,757 − 원장 귀속분. 이 값이 곧 「원장↔라이브 차이」다.
    expect(within(p).getAllByText("1,771").length).toBeGreaterThan(0);
    expect(within(p).getAllByText(/미귀속/).length).toBeGreaterThan(0);
    expect(within(p).getAllByText("3,984").length).toBeGreaterThan(0); // 대행사
    expect(p.textContent).toContain("정본은 라이브");
    expect(p.textContent).toContain("0으로 뭉개지 않습니다");
  });

  it("④ 소진 예상일이 «상한»이라고 말한다 — 값만 두면 거짓말이다", async () => {
    await renderApp();
    const p = await panel();
    expect(p.textContent).toContain("상한");
    expect(p.textContent).toContain("점화하면 더 짧아집니다");
  });
});

describe("★70/70은 문턱 없이 무조건 빨강", () => {
  it("소진 그룹 수가 뜨고 빨강 톤이 붙는다", async () => {
    await renderApp();
    const p = await panel();
    expect(within(p).getAllByText("6개").length).toBeGreaterThan(0);
    expect(p.querySelector(".bg-red-50, .border-red-300")).toBeTruthy();
  });

  it("행의 70/70이 «70/70»으로 보이고 못 센 그룹은 0이 아니다", async () => {
    await renderApp();
    const p = await panel();
    expect(within(p).getByText("70/70")).toBeTruthy();
    // ★null을 0으로 그리면 「70칸 비었다」가 된다 — 못 센 행에 「0/70」이 있으면 안 된다.
    expect(within(p).queryByText("0/70")).toBeNull();
  });
});

describe("★화면이 «언제 기준»인지 말한다 — as_of가 아니라 스윕 시각", () => {
  it("응답 생성 시각(20:00)을 기준이라 말하지 않는다", async () => {
    await renderApp();
    const p = await panel();
    expect(p.textContent).toContain("마지막 스윕 2026-09-02 09:35 기준");
    expect(p.textContent).not.toContain("20:00");
  });

  it("★가장 오래된 관측이 며칠째인지 말한다 — 카운터엔 안 잡히는 사실이다", () => {
    // stale=0인데 9일째다. 「못 센 그룹 10개」만 보면 오늘 잠깐 실패한 걸로 읽힌다.
    const s = sweepLabel(LIVE.observed_from, LIVE.observed_to, new Date("2026-09-02T20:00:00"));
    expect(s).toContain("2026-08-24");
    expect(s).toContain("9일째");
  });

  it("같은 날 안에 다 봤으면 군더더기를 붙이지 않는다", () => {
    const s = sweepLabel("2026-09-02T09:35:00", "2026-09-02T09:36:00", new Date("2026-09-02T20:00:00"));
    expect(s).toBe("마지막 스윕 2026-09-02 09:36 기준");
  });

  it("한 번도 스윕 안 했으면 시각을 지어내지 않는다", () => {
    expect(sweepLabel(null, null)).toContain("아직 한 번도");
  });
});

describe("★잘렸다는 사실을 숨기지 않는다", () => {
  it("20건만 보인다는 것과 나머지 수를 말한다", async () => {
    await renderApp();
    const p = await panel();
    expect(p.textContent).toContain("1,010");
    expect(p.textContent).toMatch(/나머지|여기 없습니다/);
  });
});

describe("★대행사 칸 반납 금지선이 화면에 남는다", () => {
  it("reclaim_note를 그대로 렌더한다 — 문구를 새로 짓지 않는다", async () => {
    await renderApp();
    const p = await panel();
    expect(p.textContent).toContain("대행사 칸은 우리가 반납하지 않는다");
  });
});

// ══════════════════════════════════════════════════════════════════
// Jino 지적 2건 (2026-09-02 21:31) — 둘 다 «화면을 실제로 봐야» 드러난 것이다
// ══════════════════════════════════════════════════════════════════

describe("★어느 캠페인의 그룹인지 화면이 말한다", () => {
  // Jino 원문: *"어느 광고캠페인에 속해있는 광고그룹인지 알 수 없어"*
  // 「01. TEST_S20」 같은 그룹 이름은 캠페인을 모르면 어디 것인지 가려낼 수 없다.
  it("캠페인 이름이 그룹과 같은 행에 뜬다", async () => {
    await renderApp();
    const p = await panel();
    const row = within(p).getByText("02. S23FE").closest("tr")!;
    expect(within(row).getByText("● 02. 갤럭시_보급형_M")).toBeTruthy();
  });

  it("캠페인 이름을 못 찾으면 id로 폴백한다 — 지어내지 않는다", async () => {
    vi.mocked(getSearchTermExclusionSlots).mockResolvedValue({
      ...LIVE,
      rows: [{ ...LIVE.rows[2], campaign_name: "", campaign_id: "cmp-이름없음" }],
    } as never);
    await renderApp();
    const p = await panel();
    expect(within(p).getByText("cmp-이름없음")).toBeTruthy();
  });
});

describe("★펼치면 «그 행 바로 밑»에 열린다", () => {
  // Jino 원문: *"접기, 펼치기를 눌러도 그것만 바뀔 뿐 아무 정보가 나오지 않아"*
  // 초판은 패널을 표 «아래»에 붙였다 — 내용은 렌더됐지만 저 멀리 생겨서 안 보였다.
  // **붙는 자리가 곧 기능이다.** 그래서 «존재»가 아니라 «인접»을 잰다.
  const openFirst = async () => {
    const p = await panel();
    const row = within(p).getByText("02. S23FE").closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: /펼치기/ }));
    return row;
  };

  it("펼침 내용이 그 행의 «바로 다음 형제»다", async () => {
    await renderApp();
    const row = await openFirst();
    const next = row.nextElementSibling as HTMLElement;
    expect(next, "행 바로 뒤에 아무것도 없다 — 패널이 표 밖에 붙었다").toBeTruthy();
    await waitFor(() => expect(next.textContent).toContain("걸린 검색어"));
  });

  it("펼치면 실제 검색어가 뜬다 — 버튼 글자만 바뀌지 않는다", async () => {
    await renderApp();
    await openFirst();
    await waitFor(() => expect(screen.getByText("S23보호필름")).toBeTruthy());
    expect(vi.mocked(fetchNaverSearchTermExclusions)).toHaveBeenCalledWith(
      expect.objectContaining({ adgroupId: "grp-full" }),
    );
  });

  it("다시 누르면 접힌다", async () => {
    await renderApp();
    const row = await openFirst();
    await waitFor(() => expect(screen.getByText("S23보호필름")).toBeTruthy());
    fireEvent.click(within(row).getByRole("button", { name: /접기/ }));
    await waitFor(() => expect(screen.queryByText("S23보호필름")).toBeNull());
  });
});

describe("★아는 것과 모르는 것을 갈라서 말한다", () => {
  it("라이브 칸 수와 «우리가 아는 수»를 같은 줄에서 말한다", async () => {
    // 라이브 70칸인데 원장이 아는 건 1개 ⇒ 69칸은 무엇인지 모른다.
    await renderApp();
    const p = await panel();
    const row = within(p).getByText("02. S23FE").closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: /펼치기/ }));
    await waitFor(() => expect(screen.getByText("S23보호필름")).toBeTruthy());
    const box = row.nextElementSibling as HTMLElement;
    // ★느슨하게 «모릅니다|미귀속»만 보면 안 된다 — 문장을 지워도 옆의 "(미귀속)" 글자가
    //   남아 통과한다(변이 F1이 그렇게 살아남았다). **모르는 칸 수 자체**를 잰다:
    //   라이브 70칸 − 원장이 아는 1개 = 69칸.
    expect(box.textContent).toContain("70");
    expect(box.textContent).toContain("69");
    expect(box.textContent).toContain("무엇인지 모릅니다");
  });

  it("★원장이 0건이면 «제외가 없다»가 아니라 «모른다»고 말한다", async () => {
    // 2026-09-02 실측: 70/70 소진 6그룹 중 5개가 원장 0건이다. 칸은 찼는데 우리가 모른다.
    vi.mocked(fetchNaverSearchTermExclusions).mockResolvedValue(drill([]) as never);
    await renderApp();
    const p = await panel();
    const row = within(p).getByText("02. S23FE").closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: /펼치기/ }));
    const box = row.nextElementSibling as HTMLElement;
    await waitFor(() => expect(box.textContent).toContain("한 건도 없습니다"));
    expect(box.textContent).toContain("칸은 찼는데");
    expect(box.textContent).not.toMatch(/제외가 없습니다/);
  });
});

describe("★대행사 편입분의 날짜는 «편입 시각»이 아니다 (D-NAO-177)", () => {
  it("콘솔이 알려준 실제 시각을 쓴다 — 편입 시각을 쓰면 「오늘 잘랐다」로 읽힌다", () => {
    expect(termTitle(term() as never)).toContain("2024-08-27");
    expect(termTitle(term() as never)).not.toContain("2026-08-13");
  });

  it("실제 시각이 없으면 «모름»이라 쓴다 — 편입 시각으로 메우지 않는다", () => {
    const t = termTitle(term({ console_excluded_at: null }) as never);
    expect(t).toContain("모름");
    expect(t).not.toContain("2026-08-13");
  });

  it("우리 실행분은 우리 시각을 쓴다", () => {
    expect(termTitle(term({ source: null }) as never)).toContain("우리 실행분");
  });
});
