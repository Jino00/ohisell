// @vitest-environment jsdom
//
// naverAdExclusionSurvivalPanel.test.tsx — 적대 리뷰 P1-3 회귀 (2026-08-12).
//
// ★백엔드는 쇼핑 광고그룹의 제외를 `unverifiable`로 갈라 **어긋남으로 세지 않는다**(옳다 —
//   「사라졌다」가 아니라 「볼 수 없다」다). 그리고 그 저자는 주석에 이렇게 적어 뒀다:
//   *"조용히 묻히면 «감시되고 있다»는 착시가 생기므로 건수를 따로 낸다."*
//   그런데 화면은 `unverifiable`도 `unverifiable_note`도 **한 번도 읽지 않았다.**
//
//   그 결과 prod 라이브(2026-08-12 11:45)에서:
//     API   → {monitored: 2, alive: 1, unverifiable: 1, breached: [], healthy: true}
//     화면  → "우리가 건 제외 1건 모두 걸려 있음"
//   감시 2건 중 1건이 미지인데 「모두」라고 말했다. 그 1건이 하필 이 스프린트의 유일한
//   실집행(「골프」)이고, 쇼핑이라 API로는 영영 되읽을 수 없는 건이다.
//
//   대행사가 우리 조치를 되돌린 전례가 2회 있고 그중 1회는 로그에 흔적이 없었다. 되돌림을
//   «못 보는» 상태가 «확인됨»으로 읽히면 그게 이 감시가 죽는 방식이다.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => {
  const pending = () => new Promise<never>(() => {});
  return { pending, survival: null as unknown };
});

vi.mock("../lib/api", () => ({
  getSearchTermExclusionList: () => h.pending(),
  getSearchTermExclusionScorecard: () => h.pending(),
  getSearchTermExclusionSurvival: () => Promise.resolve(h.survival),
  postSearchTermExecution: () => h.pending(),
  postSearchTermExecutionDetect: () => h.pending(),
}));

import NaverAdExclusionList from "./NaverAdExclusionList";

// LayerNav가 NavLink를 쓰므로 Router 컨텍스트가 필요하다(이 테스트가 보는 것과 무관한 배선).
const renderPage = () =>
  render(
    <MemoryRouter>
      <NaverAdExclusionList />
    </MemoryRouter>,
  );

const BASE = {
  monitored: 2,
  alive: 1,
  breached: [],
  breached_total: 0,
  never_checked: 0,
  never_checked_due: 0,
  last_checked_at: "2026-08-12T08:25:00",
  stale_hours: 30,
  stale: false,
  healthy: true,
  revert_howto: "콘솔에서 삭제하면 복구된다",
  impact: "제외가 사라지면 광고비가 다시 흐른다",
  as_of: "2026-08-12T11:45:19",
};

afterEach(() => cleanup());

describe("제외 생존 배너 — «못 보는 몫»", () => {
  it("대조 불가 건이 있으면 «모두 걸려 있음»이라 말하지 않는다", async () => {
    h.survival = {
      ...BASE,
      unverifiable: 1,
      unverifiable_note:
        "쇼핑 광고그룹의 제외는 네이버 API가 돌려주지 않아 라이브 대조가 불가능하다(콘솔에서만 확인 가능).",
    };
    renderPage();

    // 이 문장이 뜨면 감시 2건 중 1건이 미지인데 전건 확인됐다고 말하는 것이다.
    expect(await screen.findByText(/대조 불가 1건/)).toBeTruthy();
    expect(screen.queryByText(/모두 걸려 있음/)).toBeNull();
    // 감시 대상 총계도 같이 보여야 «1건»이 전부가 아니라는 게 읽힌다.
    expect(screen.getByText(/감시 대상 2건/)).toBeTruthy();
    // 왜 못 보는지를 화면이 스스로 밝힌다 — 백엔드가 이 문장을 내보내는 이유가 그것이다.
    expect(screen.getByText(/네이버 API가 돌려주지 않아/)).toBeTruthy();
  });

  it("대조 불가가 0건이면 종전 문장을 그대로 쓴다 (파워링크만 있는 정상 상태)", async () => {
    h.survival = { ...BASE, monitored: 1, alive: 1, unverifiable: 0 };
    renderPage();

    expect(await screen.findByText(/1건 모두 걸려 있음/)).toBeTruthy();
    expect(screen.queryByText(/대조 불가/)).toBeNull();
  });

  it("구버전 백엔드(unverifiable 키 없음)에서도 죽지 않는다", async () => {
    const { unverifiable, ...legacy } = { ...BASE, monitored: 1, unverifiable: undefined };
    void unverifiable;
    h.survival = legacy;
    renderPage();

    expect(await screen.findByText(/1건 모두 걸려 있음/)).toBeTruthy();
  });

  it("어긋남이 있는 빨강 상태에서도 대조 불가 건수를 함께 적는다", async () => {
    h.survival = {
      ...BASE,
      monitored: 3,
      alive: 1,
      unverifiable: 1,
      healthy: false,
      breached_total: 1,
      breached: [
        {
          campaign_id: "cmp-1",
          adgroup_id: "grp-1",
          search_term: "사라진검색어",
          live_state: "missing",
          live_note: "라이브 제외키워드 목록에 없다",
          excluded_at: "2026-08-01T10:00:00",
          cost_at_exclusion: 12345,
        },
      ],
    };
    renderPage();

    expect(await screen.findByText(/어긋남 1건/)).toBeTruthy();
    expect(screen.getByText(/대조 불가 1건/)).toBeTruthy();
  });
});
