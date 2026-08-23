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
  window_caveat: "보드 창 기준 근사 — 실제 게이트는 as_of=D-1 창을 쓴다. 양끝의 «차이»는 정확하고 절대 건수는 근사.",
  assumption: "보정계수의 분자에 광고 귀속 조인이 없어 「채널 매출 100%를 광고가 견인」 가정과 동치다 — 그래서 총이익을 구간 양끝으로 병기한다(D-NAO-230).",
  factor_low: 1.0,
  factor_high: 1.3213,
  target_roas: 1.9358841828557574,
  target_roas_source: "per_campaign",
  target_roas_min: 1.6724,
  target_roas_max: 2.4261,
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
    target_roas_min: 1.6724,
    target_roas_max: 2.4261,
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

  // ══════════════════════════════════════════════════════════
  // ★적대 리뷰 1R에서 «생존»한 표면 변이 셋을 죽이는 가드
  //   M1 = 헤드라인의 survive_low → survive_high (카드가 「통과 221건」 = 「막힌 게 없다」)
  //   M2 = gate_note + assumption 자백 블록 통째 삭제
  //   M3 = 「브레이크 후보 N건」 스팬 삭제 (대칭의 분자)
  //   ⇒ 셋 다 `card.textContent`에 숫자가 «어딘가» 있으면 만족하는 단언이라 통과했다.
  //      숫자의 **위치**를 testid로 고정한다.
  // ══════════════════════════════════════════════════════════
  it("★M1 — 「게이트 통과」 자리에 하한 기준 값이 온다(상한이 오면 「막힌 게 없다」가 된다)", async () => {
    await draw(LIVE_GATE);
    const survive = screen.getByTestId("accel-gate-survive");
    expect(survive.textContent).toContain("195");
    expect(survive.textContent).not.toContain("221");
    // ★2R 변이 N8 상환 — 반대쪽 끝(반사실) 병기를 지워도 초록이었다.
    //   「하한이면 195, 상한이었다면 221」의 대비가 이 카드의 논지다.
    const headline = screen.getByTestId("accel-gate-headline");
    expect(headline.textContent).toContain("상한이었다면");
    expect(headline.textContent).toContain("221");
  });

  it("★M2 — 가정·자의 끝 자백 블록이 실재한다(D-NAO-230이 요구한 「가정 병기」)", async () => {
    await draw(LIVE_GATE);
    const caveats = screen.getByTestId("accel-gate-caveats");
    expect(caveats.textContent).toContain("하한");
    expect(caveats.textContent).toContain("가정과 동치"); // 자의 분자에 광고 귀속 조인이 없다는 자백
    // 1R P2-2 — 확정값이 아니라 근사임을 화면이 자백한다
    expect(caveats.textContent).toContain("근사");
  });

  it("★M3 — 대칭의 분자(브레이크 후보 수)가 화면에 있다(북극성 §7 검사의 절반)", async () => {
    await draw(LIVE_GATE);
    const sym = screen.getByTestId("accel-gate-symmetry");
    expect(sym.textContent).toContain("브레이크 후보");
    expect(sym.textContent).toContain("664");
  });

  it("★1R P2-1 — 「어디서 죽나」가 화면에 있다(값만 만들고 안 그리면 처분을 못 정한다)", async () => {
    await draw(LIVE_GATE);
    const byBoard = screen.getByTestId("accel-gate-by-board");
    expect(byBoard.textContent).toContain("starving_winners");
    expect(byBoard.textContent).toContain("6/136");
    expect(byBoard.textContent).toContain("shopping_group_growth");
    expect(byBoard.textContent).toContain("20/85");
  });

  it("★1R P1-1 — 계정 기본값으로 잰 경우 화면이 그 사실을 경고한다", async () => {
    await draw({ ...LIVE_GATE, target_roas_source: "account_default" });
    const caveats = screen.getByTestId("accel-gate-caveats");
    expect(caveats.textContent).toContain("게이트와 다른 자");
    expect(caveats.className).not.toContain("hidden");
  });

  it("캠페인별로 쟀으면 그 범위를 그린다 — 어느 자로 쟀는지가 숫자와 함께 보여야 한다", async () => {
    await draw(LIVE_GATE);
    const caveats = screen.getByTestId("accel-gate-caveats");
    expect(caveats.textContent).toContain("캠페인별");
    expect(caveats.textContent).toContain("1.6724");
    expect(caveats.textContent).toContain("2.4261");
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
