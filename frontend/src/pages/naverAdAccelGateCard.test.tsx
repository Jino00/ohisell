// @vitest-environment jsdom
//
// naverAdAccelGateCard.test.tsx — 「액셀 게이트」 카드가 **막힌 것을 화면에 내는가** (D-NAO-232 §4-④).
//
// ## 왜 이 테스트가 있어야 하나
// 이 계약의 «표면»은 백엔드가 `accel_gate`를 계산하는 것이 **아니라** Jino가 진단 보드에서
// 「어느 가드레일이 몇 건·얼마를 막았나」를 읽을 수 있는 것이다(계약 §4-④ 원문).
// 백엔드가 정확히 계산해도 카드가 안 그려지면 **화면은 종전과 똑같고 계약은 미달**이고,
// 그 되돌림은 tsc도 백엔드 테스트도 못 잡는다.
// ★이 저장소는 정확히 그 자리에서 이미 네 번 데였다(교훈: 값은 있는데 사람이 못 본다) —
// 세션 39 적대 리뷰 P1-1은 한술 더 떠 **카드 문구가 배포 동작과 정반대인데 프론트 테스트가
// 그 거짓을 단언**하고 있었다. 그래서 여기서는 문구가 아니라 **숫자와 부호**를 단언한다.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => ({ data: null as unknown }));

vi.mock("../lib/api", () => ({
  fetchNaverAdDiagnosis: () => Promise.resolve(h.data),
}));

import NaverAdDiagnosisBoard from "./NaverAdDiagnosisBoard";

const EMPTY_BOARDS = {
  bleeding_keywords: [],
  starving_winners: [],
  expansion_bucket: { cost: 0, conv_amt: 0, roas_naver: null, roas_corrected: null, cost_share: 0 },
  shopping_group_bep: [],
  shopping_group_growth: [],
  exclusion_candidates: [],
  keyword_triage: { winners: [], losers: [], unknowns: [] },
  vicious_cycle: [],
  pause_candidates: [],
  resume_candidates: [],
  shopping_pause_candidates: [],
  shopping_resume_candidates: [],
  shopping_lever_resume_candidates: [],
  floor_wait_units: [],
};

// ★라이브 실측값(2026-08-23 17:1x KST, ref 94 §5) — 픽스처가 prod 모양과 같아야 결함을 잡는다.
const LIVE_GATE = {
  gate_end: "factor_low",
  gate_note: "액셀 게이트(BEP 증액금지)는 구간의 «하한»을 쓴다 — 하한은 보정을 없애 차단을 최대로 만든다.",
  assumption: "보정계수의 분자에 광고 귀속 조인이 없어 「채널 매출 100%를 광고가 견인」 가정과 동치다 — 그래서 총이익을 구간 양끝으로 병기한다(D-NAO-230).",
  factor_low: 1.0,
  factor_high: 1.3213,
  target_roas: 1.9358841828557574,
  bep_roas: 1.6833747072015681,
  accel_total: 221,
  brake_total: 664,
  accel_total_ext: 221,
  brake_total_ext: 667,
  survive_low: 195,
  survive_high: 221,
  ratio_selection: 3.005,
  ratio_after_gate_low: 3.405,
  ratio_after_gate_high: 3.005,
  buckets: {
    passing_both: { count: 195, cost: 2_098_920, conv_amt: 7_668_840, profit_high: 3_920_440, profit_low: 2_456_715 },
    blocked_low_only: { count: 26, cost: 2_806_318, conv_amt: 4_706_260, profit_high: 887_679, profit_low: -10_589 },
    blocked_both: { count: 0, cost: 0, conv_amt: 0, profit_high: 0, profit_low: 0 },
    unmeasurable: 0,
  },
  by_board: [
    { board: "starving_winners", total: 136, blocked_low_only: 6, blocked_both: 0, unmeasurable: 0 },
    { board: "shopping_group_growth", total: 85, blocked_low_only: 20, blocked_both: 0, unmeasurable: 0 },
  ],
};

function diagnosis(accel_gate: unknown) {
  return {
    window: { date_from: "2026-08-09", date_to: "2026-08-23" },
    correction_factor: {
      factor: 1.3213, factor_low: 1.0, factor_high: 1.3213, factor_point: 1.3213,
      source: "actual_revenue_ratio",
      window_from: "2026-07-25", window_to: "2026-08-23",
      window_revenue: 45_583_760, window_conv_amt: 34_499_980,
    },
    account_bep_roas: 1.6833747072015681,
    account_target_roas: 1.9358841828557574,
    boards: EMPTY_BOARDS,
    accel_gate,
  };
}

const draw = async (payload: unknown) => {
  h.data = diagnosis(payload);
  render(
    <MemoryRouter>
      <NaverAdDiagnosisBoard />
    </MemoryRouter>,
  );
  return screen.findByTestId("accel-gate-card");
};

afterEach(cleanup);

describe("액셀 게이트 카드 — 표면 요건(D-NAO-232 §4-④)", () => {
  it("카드가 화면에 실재한다 — 카드 렌더를 끊으면 여기서 죽는다", async () => {
    const card = await draw(LIVE_GATE);
    expect(card).toBeTruthy();
  });

  it("★막힌 건수를 그린다 — 「몇 건 막혔나」가 이 표면의 존재 이유다", async () => {
    const card = await draw(LIVE_GATE);
    // 26 = 현행 게이트가 죽이는 액셀. 이 숫자가 화면에서 사라지면 계약 §4-④ 미달이다.
    expect(card.textContent).toContain("26");
    expect(card.textContent).toContain("195"); // 게이트 통과
    expect(card.textContent).toContain("221"); // 액셀 후보 전체
  });

  it("★막힌 건의 총이익을 «양끝»으로 그린다 — 한쪽만 그리면 부호가 숨는다", async () => {
    const card = await draw(LIVE_GATE);
    const t = card.textContent ?? "";
    expect(t).toMatch(/887,679/);   // 상한 기준 흑자
    expect(t).toMatch(/-?10,589/);  // 하한 기준 적자 — 부호가 갈리는 그 값
  });

  it("★하한 총이익이 적자면 그 셀이 경고색이다 — 부호가 눈에 안 띄면 안 본 것과 같다", async () => {
    const card = await draw(LIVE_GATE);
    const negative = Array.from(card.querySelectorAll("td")).find((td) =>
      (td.textContent ?? "").includes("10,589"),
    );
    expect(negative?.className).toContain("text-red-600");
  });

  it("대칭 비율을 게이트 전후로 그린다 — 북극성 §7의 검사 항목", async () => {
    const card = await draw(LIVE_GATE);
    const t = card.textContent ?? "";
    expect(t).toContain("3.005");
    expect(t).toContain("3.405");
  });

  it("게이트가 «하한»을 쓴다는 사실이 화면에 적혀 있다 — 화면과 동작이 어긋나면 안 된다", async () => {
    const card = await draw(LIVE_GATE);
    expect(card.textContent).toContain("하한");
  });

  it("판정 불가 건수를 «0이어도» 그린다 — 키 부재와 0건은 다르다(교훈 #123)", async () => {
    const card = await draw({
      ...LIVE_GATE,
      buckets: { ...LIVE_GATE.buckets, unmeasurable: 3 },
    });
    expect(card.textContent).toContain("판정 불가");
    expect(card.textContent).toContain("3");
  });

  it("accel_gate가 null이면 카드를 안 그린다 — 0으로 위장하지 않는다", async () => {
    h.data = diagnosis(null);
    render(
      <MemoryRouter>
        <NaverAdDiagnosisBoard />
      </MemoryRouter>,
    );
    // 보드 자체는 떠야 한다(카드만 없다)
    await screen.findByText(/출혈 키워드/);
    expect(screen.queryByTestId("accel-gate-card")).toBeNull();
  });
});
