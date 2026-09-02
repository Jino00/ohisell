// @vitest-environment jsdom
//
// naverAdExclusionImportedRows.test.tsx — 편입분이 화면에서 증발하지 않는지 지킨다
//   (D-NAO-176 적대 리뷰 P1).
//
// ★백엔드는 콘솔 편입 행을 성적표에서 **판정하지 않되 `imported_unjudgeable_count`로 센다.**
//   그 저자(나)는 주석에 이렇게 적어 뒀다:
//   *"이 숫자가 없으면 「total 2건」이 「우리가 아는 제외가 2건뿐」으로 읽히고, 편입한 43건이
//     화면에서 통째로 증발한다."*
//   그리고 화면에 그 숫자를 안 이었다 — 예고한 피해를 그대로 남긴 것이다.
//
//   이게 사흘 새 **세 번째** 같은 모양이다(unverifiable · type_unknown_groups · 이것).
//   그래서 여기서는 렌더로 단언한다 — 타입에 필드가 있는지가 아니라 **화면에 글자가 나오는지**.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => {
  const pending = () => new Promise<never>(() => {});
  return { pending, scorecard: null as unknown };
});

vi.mock("../lib/api", () => ({
  getSearchTermExclusionList: () => h.pending(),
  getSearchTermExclusionSurvival: () => h.pending(),
  // ★설계서 §5-4 슬롯 패널이 이 페이지에 붙으면서 생긴 의존 — 이 파일이 보는 것과는
  //   무관하지만, 화이트리스트 mock이라 빠지면 페이지가 통째로 터진다.
  getSearchTermExclusionSlots: () => h.pending(),
  getSearchTermExclusionScorecard: () => Promise.resolve(h.scorecard),
  postSearchTermExecution: () => h.pending(),
  postSearchTermExecutionDetect: () => h.pending(),
}));

import NaverAdExclusionList from "./NaverAdExclusionList";

const renderPage = () =>
  render(
    <MemoryRouter>
      <NaverAdExclusionList />
    </MemoryRouter>,
  );

const BASE = {
  window_days: 14,
  maturity_lag_days: 3,
  mature_through: "2026-08-09",
  by_verdict: { stopped: 0, still_spending: 0, pending: 0, no_baseline: 0 },
  profit_recovered_judged: 0,
  judged_count: 0,
  pending_count: 0,
  items: [],
  as_of: "2026-08-12T20:30:00",
};

// ★백엔드 note 원문 그대로(정본은 search_term_scorecard). 2026-08-12 D-NAO-177에서
//   「실행 시점을 모르므로」가 거짓이 되어 교체됐다 — 콘솔에 등록시각이 실제로 있다.
const NOTE =
  "콘솔에 이미 걸려 있던 제외를 장부에 편입한 행이다. 등록시각을 아는 행도 있지만, 편입 시점" +
  " 기준으로는 사후 대조에 쓸 사전 검색어 데이터가 없어 성적표가 판정하지 않는다 —" +
  " 조치 생존 감시에는 포함된다.";

afterEach(() => cleanup());

describe("성적표 — 콘솔 편입분", () => {
  it("★판정 대상이 하나도 없어도 «제외가 없습니다»라고 말하지 않는다", async () => {
    // 편입 43건만 있는 구성 — 장부에는 43건이 있는데 판정할 것은 0건이다.
    h.scorecard = { ...BASE, total: 0, imported_unjudgeable_count: 43,
                    imported_unjudgeable_note: NOTE };
    renderPage();

    expect(await screen.findByText(/43건/)).toBeTruthy();
    expect(screen.queryByText(/아직 실행된 제외가 없습니다/)).toBeNull();
    // ★빈 화면 문구는 TSX 하드코딩 사본이라 백엔드 note를 고쳐도 같이 안 바뀐다
    //   (D-NAO-177 적대 리뷰 P1-1: 같은 사실의 형제 문장이 두 벌이었다).
    expect(screen.queryByText(/실행 시점을 몰라/)).toBeNull();
    expect(screen.getByText(/사전 데이터가 없어/)).toBeTruthy();
  });

  it("판정 대상이 있으면 헤더에 «편입분 N건은 판정 대상 아님»을 함께 적는다", async () => {
    h.scorecard = { ...BASE, total: 2, judged_count: 1, pending_count: 1,
                    by_verdict: { stopped: 1, still_spending: 0, pending: 1, no_baseline: 0 },
                    imported_unjudgeable_count: 43, imported_unjudgeable_note: NOTE };
    renderPage();

    expect(await screen.findByText(/편입분 43건은 판정 대상 아님/)).toBeTruthy();
    expect(screen.getByText(new RegExp("사전 검색어 데이터가 없어"))).toBeTruthy();
  });

  it("편입분이 0이면 그 줄을 만들지 않는다 — 없는 것을 있는 것처럼 적지 않는다", async () => {
    h.scorecard = { ...BASE, total: 2, judged_count: 1, pending_count: 1,
                    by_verdict: { stopped: 1, still_spending: 0, pending: 1, no_baseline: 0 } };
    renderPage();

    expect(await screen.findByText(/총 2건/)).toBeTruthy();
    expect(screen.queryByText(/판정 대상 아님/)).toBeNull();
  });

  it("옛 응답(편입 필드 없음)에도 터지지 않는다", async () => {
    h.scorecard = { ...BASE, total: 0 };
    renderPage();
    expect(await screen.findByText(/아직 실행된 제외가 없습니다/)).toBeTruthy();
  });
});
