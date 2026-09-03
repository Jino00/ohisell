// @vitest-environment jsdom
//
// naverAdOwnershipBandsSurface.test.tsx — 관할 밴드가 **사람 눈에 닿는가** (성과분리 목표 §4).
//
// ## 왜 이 파일이 있어야 하나
// 이 계약의 «표면»은 백엔드가 밴드를 계산하는 것이 **아니라** Jino가 성과 화면에서
// 「전체 / PAO가 돌린 광고 / 안 돌린 광고」를 읽는 것이다(계약 §4 원문).
//
// ★적대 리뷰 P1-4 상환. 초판엔 이 파일이 없었고, 그래서 **표면 절단 변이 6종이 전건 생존**했다 —
//   섹션 렌더 줄을 통째로 지워도 프론트 1,196건이 초록이었다. 그중 둘(항등식 경고 제거·「모름」
//   칸 누락)은 내가 **계약 §6에 리뷰어용 예시로 직접 적어 둔 변이**였다. 백엔드는 15종 변이가
//   전건 사망할 만큼 촘촘한데, 그 판정이 **문장이 되는 마지막 한 칸**만 아무도 안 지키고 있었다.
//   이 저장소가 네 번 밟은 「값은 계산되는데 사람이 못 본다」와 같은 모양이다.
//
// 그래서 여기서 지키는 것은 로직이 아니라 **DOM에 그 문자열이 있는가**다.
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, cleanup, within, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// ★fixture에 타입을 건다 — `unknown`으로 두면 응답 타입에 필수 필드를 추가해도 tsc가
//   fixture 갱신을 강제하지 않아 다음 필드 추가에서 조용히 어긋난다(적대 리뷰 P2).
type BandName = import("../lib/api").NaverOwnershipBandName;

const h = vi.hoisted(() => ({
  bands: null as import("../lib/api").NaverOwnershipBands | null,
  ownership: null as import("../lib/api").NaverOwnershipCampaigns | null,
}));

vi.mock("../lib/api", () => ({
  fetchNaverOwnershipBands: () => Promise.resolve(h.bands),
  fetchNaverOwnershipCampaigns: () => Promise.resolve(h.ownership),
  fetchNaverPerformanceDay: () => Promise.resolve(DAY),
  fetchNaverPerformanceCampaignOptions: () => Promise.resolve({ campaigns: [] }),
  fetchNaverPerformanceCampaign: () => Promise.resolve(null),
  fetchNaverPerformanceCompare: () => Promise.resolve(null),
  fetchNaverPerformanceBudget: () => Promise.resolve(null),
  fetchNaverPerformanceBepBreakdown: () => Promise.resolve(null),
  fetchNaverPerformanceTimeline: () => Promise.resolve(null),
}));

import NaverAdPerformance from "./NaverAdPerformance";

const CAMPAIGN_ID = "cmp-a001-02-000000008425541";

const CARD = {
  campaign_id: CAMPAIGN_ID,
  name: "Z폴드8 와이드",
  type_label: "쇼핑검색",
  status_label: "정상 노출 중",
  review_label: null,
  managed_by_label: "우리가 자동으로 운영",
  auto_operate: true,
  active_today: true,
  spend_today: 214932,
  daily_budget: 300000,
  spend_ratio: 0.7164,
  imp_today: 12000,
  clk_today: 182,
  roas_today_proxy: 1.5,
  roas_label: "ROAS (확정)",
  revenue_today_proxy: null,
  revenue_label: null,
  target_roas: 1.94,
  bep_roas: 1.711,
  verdict_sentence: "손익분기 아래입니다.",
  roas_unknown_reason: null,
  shared_product_count: 0,
};

const DAY = {
  as_of: "2026-08-28T09:00:00",
  date: "2026-08-28",
  is_today: false,
  source: "confirmed",
  source_label: "네이버 확정 전환매출",
  data_note: "확정 적재 기준입니다.",
  data_gap_note: null,
  campaign_filter: null,
  campaigns: [CARD],
  totals: { spend_today: 214932, campaigns_active_today: 1, campaigns_total: 1 },
  today_actions: {
    executed_count: 0, blocked_count: 0, unknown_count: 0, items: [],
    quiet_reason: "실제로 반영된 변경이 없습니다.",
  },
};

/** prod 실측 모양(2026-08-29) — 픽스처가 prod와 같아야 결함을 잡는다. */
function bandsPayload(over: Record<string, unknown> = {}) {
  const band = (b: BandName, label: string, cost: number, note: string | null = null) => ({
    band: b, label, note, cost, imp: 0, clk: 0, conv_amt: 0, roas: null, cpc: null,
    campaigns: 1, adgroups: 1, days: 1, share_of_cost: cost / 19479832,
  });
  return {
    window: {
      date_from: "2026-07-30", date_to: "2026-08-28",
      requested_to: "2026-08-28", latest_confirmed: "2026-08-28", truncated: false,
    },
    total: {
      cost: 19479832, imp: 0, clk: 0, conv_amt: 0, roas: null, cpc: null,
      campaigns: 28, adgroups: 408, days: 30, share_of_cost: 1,
    },
    bands: [
      band("pao", "PAO가 돌린 광고", 0),
      band("not_pao", "PAO가 안 돌린 광고", 19007252),
      band("transition", "담당이 바뀐 날", 472580, "하루 중간에 담당이 바뀐 날입니다."),
      band("unknown", "모름(기록 없음)", 25153015, "담당 변경 기록이 남기 전 구간입니다."),
    ],
    identity: { ok: true, total_cost: 19479832, band_cost_sum: 19479832, diff: 0 },
    diagnostics: {
      history_start: "2026-07-11", unparsable_events: 0, unparsable_samples: [],
      inconsistent_events: 0, inconsistent_samples: [],
      campaigns_with_unknown_tail: {}, transition_days: {},
    },
    notes: [],
    empty: false,
    ...over,
  };
}

function ownershipPayload(band: BandName, over: Record<string, unknown> = {}) {
  return {
    as_of: "2026-08-28",
    requested: "2026-08-28",
    clamped: false,
    note: null as string | null,
    campaigns: {
      [CAMPAIGN_ID]: {
        band,
        label: band === "pao" ? "PAO가 돌린 광고"
          : band === "unknown" ? "모름(기록 없음)"
            : band === "transition" ? "담당이 바뀐 날" : "PAO가 안 돌린 광고",
        partial: false,
        pao_adgroups: band === "pao" ? 1 : 0,
        not_pao_adgroups: band === "not_pao" ? 1 : 0,
        transition_adgroups: band === "transition" ? 1 : 0,
        unknown_adgroups: band === "unknown" ? 1 : 0,
        adgroups: 1,
        ...over,
      },
    },
  };
}

function renderPage() {
  return render(<MemoryRouter><NaverAdPerformance /></MemoryRouter>);
}

beforeEach(() => {
  h.bands = bandsPayload();
  h.ownership = ownershipPayload("not_pao");
});
afterEach(cleanup);

describe("관할 밴드 섹션이 화면에 있다", () => {
  // 변이 S1: <OwnershipBandSection /> 렌더 제거 → 이 단언들이 죽는다
  it("네 밴드가 라벨과 금액으로 전부 그려진다", async () => {
    renderPage();
    expect(await screen.findByText("누가 돌린 광고인가")).toBeTruthy();
    for (const label of [
      "PAO가 돌린 광고", "PAO가 안 돌린 광고", "담당이 바뀐 날", "모름(기록 없음)",
    ]) {
      expect(await screen.findByText(label)).toBeTruthy();
    }
    // 금액도 실제로 렌더된다 — 라벨만 있고 숫자가 없으면 읽을 게 없다
    expect(await screen.findByText("19,007,252원")).toBeTruthy();
    expect(await screen.findByText("472,580원")).toBeTruthy();
  });

  // 변이 S4: "unknown"을 출력 목록에서 제외 → 이 단언이 죽는다
  it("★「모름」 칸이 금액과 함께 뜬다 — 0으로 뭉개거나 빼면 안 된다", async () => {
    renderPage();
    expect(await screen.findByText("모름(기록 없음)")).toBeTruthy();
    expect(await screen.findByText("25,153,015원")).toBeTruthy();
  });

  it("전환일·모름 칸은 «왜 따로 뒀나»를 말한다", async () => {
    renderPage();
    expect(await screen.findByText(/하루 중간에 담당이 바뀐 날입니다/)).toBeTruthy();
    expect(await screen.findByText(/담당 변경 기록이 남기 전 구간입니다/)).toBeTruthy();
  });

  it("기간 버튼 30/90/180일이 있다", async () => {
    renderPage();
    // ★페이지 다른 곳에도 「30일」 버튼(추이 기간)이 있으므로 **이 바 안으로** 범위를 좁힌다 —
    //   전역으로 찾으면 «다른 버튼이 있어서» 통과하는 가짜 초록이 된다.
    // 2026-09-03: 기간 선택이 카드 안 버튼 셋에서 공용 `PeriodRangeBar`로 바뀌어 바가 카드
    //   **바깥**(형제)에 산다. 그래서 카드 제목이 아니라 이 바에만 있는 안내문으로 좁힌다 —
    //   프리셋 셋은 그대로여야 한다(하나라도 빠지면 기능 회귀다).
    const bar = (await screen.findByText(/오늘치는 아직 적재 전이라 창에서 빠집니다/))
      .closest("section")!;
    for (const d of ["30일", "90일", "180일"]) {
      expect(within(bar).getByRole("button", { name: d })).toBeTruthy();
    }
  });

  it("날짜 두 칸이 생겼고 시작일이 종료일보다 뒤면 조회 자체를 안 한다", async () => {
    // ★새 캘린더의 «추가된 기능»을 재는 자리. 버튼만 재면 날짜 칸이 통째로 사라져도 초록이다.
    renderPage();
    const bar = (await screen.findByText(/오늘치는 아직 적재 전이라 창에서 빠집니다/))
      .closest("section")!;
    const dateInputs = within(bar).getAllByDisplayValue(/^\d{4}-\d{2}-\d{2}$/);
    expect(dateInputs.length).toBe(2);
  });
});

describe("항등식이 화면까지 간다", () => {
  it("맞으면 「빠뜨린 광고비가 없습니다」를 말한다", async () => {
    renderPage();
    expect(await screen.findByText(/빠뜨린 광고비가 없습니다/)).toBeTruthy();
  });

  // ★변이 S2: 항등식 분기 제거(항상 초록 문구) → 이 단언이 죽는다.
  //   백엔드 테스트는 identity.ok=false를 «만드는» 것만 지킨다 — 그게 «문장이 되는» 구간은 여기다.
  it("★깨지면 경고가 뜬다 — 숨기지 않는다", async () => {
    h.bands = bandsPayload({
      identity: { ok: false, total_cost: 19479832, band_cost_sum: 19474832, diff: 5000 },
    });
    renderPage();
    expect(await screen.findByText(/합계가 5,000원 어긋납니다/)).toBeTruthy();
    expect(await screen.findByText(/숫자를 믿지 마세요/)).toBeTruthy();
    expect(screen.queryByText(/빠뜨린 광고비가 없습니다/)).toBeNull();
  });
});

describe("경계 안내(notes)가 화면까지 간다", () => {
  // 변이 S3: data.notes.map(...) 제거 → 이 단언이 죽는다
  it("오늘 잘림·기록 모순 안내가 그대로 렌더된다", async () => {
    h.bands = bandsPayload({
      notes: [
        "오늘·미확정 구간은 뺐습니다 — 밴드는 2026-08-28까지의 확정 데이터만 셉니다.",
        "담당 변경 기록 2건이 기록끼리 어긋납니다(기록에 안 남은 변경이 있었다는 뜻입니다) — 그 앞 구간은 «모름»에 넣었습니다.",
      ],
    });
    renderPage();
    expect(await screen.findByText(/확정 데이터만 셉니다/)).toBeTruthy();
    expect(await screen.findByText(/기록끼리 어긋납니다/)).toBeTruthy();
  });

  it("기록 시작일을 밝힌다", async () => {
    renderPage();
    expect(await screen.findByText(/2026-07-11부터 남아 있습니다/)).toBeTruthy();
  });
});

describe("밴드 필터와 「그날 담당」 배지", () => {
  // 변이 S6: bandFilterRow 렌더 제거 → 이 단언이 죽는다
  it("필터 버튼 3종이 있고 판정 기준일을 밝힌다", async () => {
    renderPage();
    for (const label of ["전체", "PAO가 돌리는 광고", "PAO가 안 돌리는 광고"]) {
      expect(await screen.findByRole("button", { name: label })).toBeTruthy();
    }
    expect(await screen.findByText("2026-08-28 시점 담당 기준")).toBeTruthy();
  });

  // 변이 S5: ownershipBadgeText 배지 제거 → 이 단언이 죽는다
  it("카드에 「그날 담당」 배지가 붙고 «지금 담당»과 구별된다", async () => {
    h.ownership = ownershipPayload("pao");
    renderPage();
    expect(await screen.findByText("그날 PAO가 돌린 광고")).toBeTruthy();
    // 지금 담당 라벨은 별도로 남아 있어야 한다 — 둘이 다를 수 있고 다른 게 정상이다
    expect(await screen.findByText("우리가 자동으로 운영")).toBeTruthy();
  });

  it("부분 관할은 분모를 화면에 보여준다", async () => {
    h.ownership = ownershipPayload("pao", { partial: true, pao_adgroups: 1, adgroups: 58 });
    renderPage();
    expect(await screen.findByText("그날 PAO 부분 담당 (1/58 그룹)")).toBeTruthy();
  });
});

describe("★고른 날짜가 확정 전이면 그 사실을 말한다 (완료 QA가 잡은 자리)", () => {
  it("되돌렸으면 «왜»가 화면에 뜬다 — 날짜만 바꿔 놓고 침묵하지 않는다", async () => {
    h.ownership = {
      ...ownershipPayload("not_pao"),
      as_of: "2026-08-29",
      requested: "2026-08-30",
      clamped: true,
      note: "2026-08-30은 아직 확정 전이라 그날 담당을 가릴 수 없습니다 — 2026-08-29 기준으로 보여줍니다.",
    };
    renderPage();
    expect(await screen.findByText(/2026-08-30은 아직 확정 전이라/)).toBeTruthy();
    expect(await screen.findByText(/2026-08-29 기준으로 보여줍니다/)).toBeTruthy();
  });

  it("되돌린 게 없으면 경고를 안 띄운다 — 상시 경고는 아무도 안 읽는다", async () => {
    renderPage();
    expect(await screen.findByText("2026-08-28 시점 담당 기준")).toBeTruthy();
    expect(screen.queryByText(/확정 전이라/)).toBeNull();
  });

  it("★확정 데이터가 0건일 때의 문장도 화면에 닿는다 — 죽은 문자열이 아니다", async () => {
    // 게이트가 `clamped &&`였을 때 이 문장은 백엔드가 만들고 화면이 버렸다(적대 리뷰 P2).
    h.ownership = {
      as_of: null, requested: null, clamped: false,
      note: "확정된 광고 데이터가 아직 없습니다.",
      campaigns: {},
    };
    renderPage();
    expect(await screen.findByText("확정된 광고 데이터가 아직 없습니다.")).toBeTruthy();
  });
});

describe("★판정 못 한 광고를 「PAO 아님」으로 말하지 않는다 (적대 리뷰 P1-3)", () => {
  it("「모름」 캠페인은 비PAO 필터에서 빠지고, 빠졌다는 사실이 화면에 뜬다", async () => {
    h.ownership = ownershipPayload("unknown");
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "PAO가 안 돌리는 광고" }));
    // 두 자리에서 말해야 한다 — 필터 줄(무엇을 뺐나)과 빈 목록 안내(왜 비었나).
    expect(
      await screen.findAllByText(/담당을 판정할 수 없는 광고 1개는 목록에서 뺐습니다/),
    ).toHaveLength(2);
    // ★「없습니다」로 단언하지 않는다 — 모르는 것을 없는 것으로 말하면 그게 P1-3이다.
    expect(await screen.findByText(/PAO 밖 광고가 확인되지 않습니다/)).toBeTruthy();
    expect(screen.queryByText(/PAO 밖 광고가 없습니다\./)).toBeNull();
  });

  it("전환일 캠페인도 같은 처분을 받는다", async () => {
    h.ownership = ownershipPayload("transition");
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "PAO가 돌리는 광고" }));
    expect(
      await screen.findAllByText(/담당을 판정할 수 없는 광고 1개는 목록에서 뺐습니다/),
    ).toHaveLength(2);
  });

  it("전체 필터에서는 그대로 보인다 — 숨기는 게 아니라 «모름»으로 두는 것이다", async () => {
    h.ownership = ownershipPayload("unknown");
    renderPage();
    expect(await screen.findByText("Z폴드8 와이드")).toBeTruthy();
    expect(await screen.findByText("그날 모름(기록 없음)")).toBeTruthy();
  });
});
