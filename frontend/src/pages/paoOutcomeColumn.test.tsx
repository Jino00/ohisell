// @vitest-environment jsdom
//
// paoOutcomeColumn.test.tsx — 「수정 사항」의 **결과 칸**이 사용자에게 실제로 닿는지 잰다.
// (설계서 122 §7½ 3단계 = §4-2·§4-3·§4-4)
//
// ## 왜 이 파일이 있나
//
// 이번 슬라이스로 서버 응답에 `outcome_profit`·`by_execution`을 가산했는데, **그 시점에
// 프론트 98파일 1,457건이 전부 초록이었다.** 즉 결과 칸을 통째로 지워도, 연습 배지를 떼도,
// 「(Ava 미분리)」를 없애도 CI는 안 빨개진다 — n=8 적대 리뷰가 «사용자에게 닿는 마지막
// 표면을 끊는 변이»로 잡아낸 그 병이 이 화면에도 그대로 있었다.
//
// ⇒ 여기서 재는 것은 넷이다:
//   ① 채점된 행의 **금액**이 화면에 찍히는가(그리고 그 자 자백이 함께 뜨는가)
//   ② 아직 안 채워진 칸이 0이나 「—」가 아니라 **«언제 채워지는가»**를 말하는가
//   ③ 연습(dry_run) 행이 **목록에 남고** 배지가 붙으며 실집행과 **따로 세어지는가**
//   ④ 주체 라벨 옆에 **「(Ava 미분리)」**가 붙는가
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { NaverModificationResponse, NaverModificationRow } from "../lib/api";

const h = vi.hoisted(() => ({ calls: [] as Record<string, unknown>[] }));

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  fetchNaverModifications: (p: Record<string, unknown>) => {
    h.calls.push(p);
    return Promise.resolve(RESPONSE);
  },
}));

import NaverAdModifications from "./NaverAdModifications";

const BASE: Omit<NaverModificationRow, "key" | "entity_id" | "dry_run" | "outcome_profit"> = {
  source: "change_log", source_label: "우리 기록", source_id: 1,
  occurred_at: "2026-07-30T10:12:09", occurred_date: "2026-07-30",
  time_basis: "occurred", time_note: "발생 시각", actor: "ours", actor_label: "우리 자동화",
  actor_auto: "ours", actor_evidence: null, corrected: false, correction_note: null,
  entity_type: "ad", entity_type_label: "소재", entity_name: "소재 1", campaign_id: "cmp-1",
  campaign_name: "캠페인 1", op_type: "update_bid", op_label: "입찰 변경",
  before: "2,730원", after: "2,330원", before_unknown: null, after_unknown: null,
  execution_state: null, summary: "입찰을 내렸습니다.", backfilled: false,
  feed_verdict: null, feed_verdict_label: null, feed_evidence: null,
  feed_group_size: 1, feed_group_ids: [],
};

const LENS = {
  cf: 1.25, bep: 2, bep_source: "product",
  basis: "있는 그대로(보정 없음)",
  high_basis: "보정계수 점추정(구간의 위쪽 끝) — 채널 매출 전액을 광고 공으로 돌리는 가정",
  interval_low_available: false,
};
const WINDOW = {
  days: 14, before_from: "2026-07-16", before_to: "2026-07-29",
  after_from: "2026-07-30", after_to: "2026-08-12",
};

const RESPONSE: NaverModificationResponse = {
  total: 3,
  by_actor: { ours: 2, agency: 1, jino: 0 },
  feed_reapply: {
    verdict_rows: 0, feed_rows: 0, hidden: 0, collapsed_into: 0,
    included: true, collapsed: true,
  },
  reclaimed_ours: 0,
  by_execution: { executed: 1, dry_run: 1, includes_dry_run: true },
  rows: [
    {
      ...BASE, key: "change_log:1", entity_id: "nad-1", dry_run: false,
      outcome_profit: {
        state: "scored", delta: 40000, before: 50000, after: 90000, verdict: "improved",
        delta_high: 52500, scored_by: "high", sign_flips: false,
        note: null, lens: LENS, window: WINDOW,
        legacy: { outcome: "improved", label: "교정 전 자 — 증거용", note: "전/후 RPC 배율" },
      },
    },
    {
      ...BASE, key: "change_log:2", entity_id: "nad-2", source_id: 2, dry_run: false,
      outcome_profit: {
        state: "pending", delta: null, before: null, after: null, verdict: null,
        delta_high: null, scored_by: null, sign_flips: false,
        note: "채점 전 · D+14 · 2026-09-15부터", scored_from: "2026-09-15",
        lens: null, window: null,
        legacy: { outcome: null, label: "교정 전 자 — 증거용", note: "전/후 RPC 배율" },
      },
    },
    {
      ...BASE, key: "change_log:3", entity_id: "nad-3", source_id: 3, dry_run: true,
      outcome_profit: {
        state: "dry_run", delta: null, before: null, after: null, verdict: null,
        delta_high: null, scored_by: null, sign_flips: false,
        note: "채점 대상 아님 — 연습(dry_run)이라 계정에 안 나갔다",
        lens: null, window: null, legacy: null,
      },
    },
    {
      ...BASE, key: "change_log:4", entity_id: "nad-4", source_id: 4, dry_run: false,
      outcome_profit: {
        // 있는 그대로는 악화(−10,000)인데 상한 가정으로는 개선(+15,000)인 행.
        state: "scored", delta: -10000, before: 50000, after: 40000, verdict: "improved",
        delta_high: 15000, scored_by: "high", sign_flips: true,
        note: null, lens: LENS, window: WINDOW,
        legacy: { outcome: "improved", label: "교정 전 자 — 증거용", note: "전/후 RPC 배율" },
      },
    },
  ],
};

beforeEach(() => { h.calls.length = 0; });
afterEach(cleanup);

async function renderScreen() {
  render(<MemoryRouter><NaverAdModifications /></MemoryRouter>);
  await waitFor(() => expect(h.calls.length).toBeGreaterThan(0));
}

describe("결과 칸이 사용자에게 닿는다", () => {
  it("① 채점된 행은 금액을 찍고, 그 금액을 «무슨 자로» 쟀는지 함께 말한다", async () => {
    await renderScreen();
    // 첫 숫자는 **있는 그대로**(보정 없음)다 — 상한만 실으면 가장 낙관적으로 보인다.
    expect(await screen.findByText("+40,000원")).toBeTruthy();
    // 상한 가정은 나란히, 그리고 「채점은 이 자로 했다」까지 말한다(ref 93 §1 행 9).
    expect(screen.getByText(/상한 가정 \+52,500원/).textContent).toContain("채점 판정은 이 자로");
    // 「개선」이라는 낱말이 아니다(§4-3 — 매출 −48.3%가 「개선」이던 그 얼굴).
    expect(screen.queryByText("개선")).toBeNull();
    // 자 자백(D-NAO-230) — 자의 정체·BEP·전후 창이 성적과 **함께** 뜬다.
    const lens = screen.getAllByText(/있는 그대로\(보정 없음\)/)[0];
    expect(lens.textContent).toContain("BEP 2");
    expect(lens.textContent).toContain("2026-07-16~2026-07-29");
    expect(lens.textContent).toContain("2026-07-30~2026-08-12");
    // 「하한으로도 흑자인가」는 못 잰다 — 그 자백은 툴팁에 있다.
    expect(lens.getAttribute("title")).toContain("하한");
  });

  it("①-b 자에 따라 부호가 갈리는 행은 화면이 그렇게 말한다", async () => {
    await renderScreen();
    // ★상한만 실었으면 이 행은 그냥 「개선」으로 보인다 — 자가 결론을 만들었다는 사실이 사라진다.
    expect(await screen.findByText("−10,000원")).toBeTruthy();
    expect(screen.getByText(/상한 가정 \+15,000원/)).toBeTruthy();
    expect(screen.getByText("자에 따라 부호가 뒤집힙니다 — 자 선택이 결론을 바꿉니다")).toBeTruthy();
  });

  it("② 아직 안 채워진 칸은 0도 「—」도 아니라 «언제 채워지는가»를 말한다", async () => {
    await renderScreen();
    expect(await screen.findByText("채점 전 · D+14 · 2026-09-15부터")).toBeTruthy();
  });

  it("③ 연습 행은 목록에 남고 배지가 붙으며 실집행과 따로 세어진다", async () => {
    await renderScreen();
    // 조회가 연습을 **일부러 포함**한다 — 빼면 PAO 자기 행동 대부분이 화면에서 사라진다.
    expect(h.calls[0].include_dry_run).toBe(true);
    expect(await screen.findByText("연습(dry_run) — 계정에 안 나감")).toBeTruthy();
    const summary = screen.getByText(/실집행 1건/);
    expect(summary.textContent).toContain("연습(dry_run) 1건");
  });

  it("③-b 연습을 못 셌으면 0이 아니라 그렇게 말한다", async () => {
    RESPONSE.by_execution = { executed: 1, dry_run: null, includes_dry_run: false };
    await renderScreen();
    expect(await screen.findByText(/세지 못했습니다/)).toBeTruthy();
    RESPONSE.by_execution = { executed: 1, dry_run: 1, includes_dry_run: true };
  });

  it("④ 「우리 자동화」 옆에 (Ava 미분리)가 붙는다 — 없는 정확도를 있는 척하지 않는다", async () => {
    await renderScreen();
    // 행 배지 옆 + 주체 분포 요약, **두 자리 모두**(라벨 경로가 갈리면 화면이 두 말을 한다).
    await waitFor(() => expect(screen.getAllByText("(Ava 미분리)").length).toBeGreaterThanOrEqual(2));
  });
});
