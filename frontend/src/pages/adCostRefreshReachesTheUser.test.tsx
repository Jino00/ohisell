// @vitest-environment jsdom
//
// adCostRefreshReachesTheUser.test.tsx — 「광고비 갱신」 결과 문구가 사람에게 닿는가
// (2026-08-23 적대 리뷰 P2-2 채택)
//
// ## 왜 이 파일이 있나
//
// W2가 `CoupangOps.refreshAdCostNow`의 **자체 폴링·자체 문구 사본**을 걷어내고 공용 모듈에
// 위임했는데, 그 화면에는 가드가 하나도 없었다. 리뷰어가 `describeOutcome` 호출부를 옛
// 고정 문구(「⚠️ Mac 응답 없음 — Mac이 켜져 있는지 확인하세요」)로 되돌리는 변이를 넣었더니
// **580건 전건이 초록이었다** — 고쳐 놓고 되돌아가도 아무도 울지 않는 상태였다.
//
// 이 저장소가 반복해서 밟은 병이다: 단위 테스트는 「함수가 값을 만드나」를 묻지 「사람이
// 그걸 보나」를 못 묻는다(전역 CLAUDE.md §4 ★). 그래서 여기서는 **화면에 그려진 글자**를
// `describeOutcome`의 출력과 대조한다.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { MemoryRouter } from "react-router-dom";

import { describeOutcome, specByKey } from "../lib/streamRefresh";

const h = vi.hoisted(() => ({
  statusFor: null as null | ((nth: number) => unknown),
}));

// ★전역 fetch를 막는다(2026-08-23 적대 리뷰 P2-2). `refreshAdCostNow`의 실패 분기가
//   `loadAdCookieStatus()`를 await 하는데 그 함수는 api 모듈이 아니라 **날 fetch**를 쓴다
//   (`CoupangOps.tsx:260`). 모킹하지 않으면 가짜 타이머 아래에서 «실제 네트워크 거부가
//   언제 도착하느냐»에 따라 문구 세팅 시점이 갈려 이 가드가 **플레이키**해진다 —
//   실측 3회 중 2회 실패(리뷰어 관측). 플레이키한 표면 가드는 없는 것보다 나쁘다:
//   표면이 끊겨도 「어쩌다 초록」이면 아무도 안 운다.
vi.stubGlobal("fetch", async () => ({ ok: false, status: 503, json: async () => ({}) }));

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  let n = 0;
  return {
    ...actual,
    // 화면 데이터 조회는 영원히 pending — 재는 것은 갱신 결과 문구다.
    fetchSalesSummary: () => new Promise(() => {}),
    getCoupangAdCostDaily: () => new Promise(() => {}),
    getAdCostRefreshStatus: async () => (h.statusFor ? h.statusFor(n++) : new Promise(() => {})),
    requestAdCostRefresh: async () => ({}),
  };
});

import CoupangOps from "./CoupangOps";

const AD = specByKey("ofix_ad");

describe("광고비 갱신 — 결과 문구의 저자는 공용 모듈이다", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    cleanup();
    h.statusFor = null;
  });

  it("세션 만료 → 옛 고정 문구가 아니라 describeOutcome이 지은 처방이 화면에 뜬다", async () => {
    const settled = {
      requested: false,
      last_success_at: null,
      last_error_at: "2026-08-23T13:40:00",
      last_error: "광고비 수집 실패(rc=3): 사유 미상",
      last_error_kind: "login_required",
      attempt_count: 1,
    };
    h.statusFor = (nth) =>
      nth === 0
        ? { requested: false, last_success_at: null, last_error_at: null, last_error: null }
        : settled;

    render(
      <MemoryRouter>
        <CoupangOps />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: /광고비 갱신/ }));
    await vi.advanceTimersByTimeAsync(10000);

    const expected = describeOutcome(AD, {
      state: "failed",
      reason: settled.last_error,
      kind: "login_required",
    });
    expect(document.body.textContent).toContain(expected);
    // 이 레인은 자동 재개 배선이 없다 — 처방이 「다시 눌러주세요」여야 참이다(리뷰 P1-2).
    expect(document.body.textContent).toContain("다시 눌러주세요");
    // 옛 사본의 고정 문구가 되살아나면 여기서 죽는다.
    expect(document.body.textContent).not.toContain("Mac 응답 없음 — Mac이 켜져 있는지");
  });
});
