// @vitest-environment jsdom
//
// bulkPanelReachesTheUser.test.tsx — 「전체 갱신」 패널의 문구가 **사람 눈까지 닿는가**
// (2026-08-23 W4, 계약 CONTRACT_collection_works_everywhere §3)
//
// ## 왜 이 파일이 따로 있나
//
// `streamRefresh.test.ts`는 08-22 W4에 타임아웃을 «처방이 다른 세 경우»로 가르고 그걸 전부
// 단언했다. **그리고 그 스위트는 계속 초록이었다.** 그런데 08-23 10:32 Jino의 폰 화면에는
// 수집에 **성공한** 레인(로켓 발주/정산, prod 10:32:54 `fresh`) 옆에
// 「⚠️ 응답 없음 — Mac이 켜져 있는지 확인하세요」가 떠 있었다.
//
// 이유: 문구를 짓는 곳이 둘이었다. `describeOutcome`은 세 경우를 갈랐지만, 패널은
// `Dashboard.bulkStateText`라는 **자기 문구**를 갖고 있었고 판정 결과를 done/failed/timeout
// 셋으로 접어 받느라 attemptCount·inFlight·kind를 **버렸다**. 함수는 옳았고, 사람이 보는
// 자리에는 그 옳음이 도착하지 않았다(전역 §4 ★: 단위 테스트는 「함수가 값을 만드나」를 묻지
// 「사람이 그걸 보나」를 못 묻는다).
//
// ## 그래서 무엇을 하나
//
// **Dashboard를 통째로 렌더하고, 화면에 실제로 그려진 글자를 `outcomeView`의 출력과 대조한다.**
// 저장소 복원 → 상태 운반 → 위임 → JSX 렌더가 한 줄로 이어져야만 통과하므로, 다음 변이는
// 전부 여기서 죽는다:
//   SUR-1 `bulkStateText`의 outcomeView 위임 분기 제거(자기 문구로 복귀)
//   SUR-2 렌더에서 `bulkStateText(…, spec)`의 spec 인자 제거
//   SUR-3 `<span>{v.text}</span>` 렌더 제거
//   SUR-4 `put()`이 outcome을 싣지 않도록 되돌리기(= 08-23 결함 그 자체)
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

import { BULK_REFRESH_STORAGE_KEY } from "../lib/bulkRefreshPersistence";
import { outcomeView, specByKey } from "../lib/streamRefresh";

// 네트워크는 타지 않는다 — 재는 것은 「판정이 화면 픽셀이 되나」이지 서버가 아니다.
//
// ★부분 모킹이다: streamRefresh의 STREAM_SPECS가 같은 모듈의 갱신 함수들을 실제로 참조하므로
//   통짜로 갈아치우면 spec 레지스트리가 무너진다(= 이 테스트가 보려는 배선의 한쪽이 사라진다).
// ★그리고 spy가 아니라 **팩토리**여야 한다: STREAM_SPECS는 모듈 로드 시점에 함수 «참조»를
//   객체에 박아 두므로, 나중에 vi.spyOn으로 export를 갈아치워도 그 참조는 옛 함수를 가리킨다
//   (첫 시도에서 실제 fetch가 나가 「요청 재시도 2/3…」에 머물렀다).
const h = vi.hoisted(() => ({
  /** 레인별 폴 응답. null이면 영원히 pending(첫 블록처럼 렌더만 볼 때). */
  statusFor: null as null | ((lane: string, nth: number) => unknown),
}));

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  const counts = new Map<string, number>();
  const status = (lane: string) => async () => {
    if (!h.statusFor) return new Promise(() => {}); // 영원히 pending
    const n = counts.get(lane) ?? 0;
    counts.set(lane, n + 1);
    return h.statusFor(lane, n);
  };
  const request = async () => ({});
  return {
    ...actual,
    fetchApi: () => new Promise(() => {}),
    syncRealtime: () => new Promise(() => {}),
    getWingVendorSummaryRefreshStatus: status("ofix_sales"),
    getAdCostRefreshStatus: status("ofix_ad"),
    getOhitechAdRefreshStatus: status("ohitech_ad"),
    getRocketRefreshStatus: status("supplier_hub"),
    getWingRgSettlementRefreshStatus: (account: string) => status(`rg_${account}`)(),
    requestWingVendorSummaryRefresh: request,
    requestAdCostRefresh: request,
    requestOhitechAdRefresh: request,
    requestRocketRefresh: request,
    requestWingRgSettlementRefresh: request,
  };
});

import Dashboard from "./Dashboard";

// prod 실측에서 온 세 경우 — 옛 패널은 이 셋을 **한 문구**로 뭉쳤다.
const ROCKET = specByKey("supplier_hub"); // 집어가서 아직 일하는 중
const OFIX_AD = specByKey("ofix_ad"); // 세션 만료(kind=login_required)
const OHITECH = specByKey("ohitech_ad"); // 아무도 안 집었다(attempt 0)

const IN_FLIGHT = { state: "no_response", attemptCount: 1, inFlight: true } as const;
const LOGIN = {
  state: "failed",
  reason: "로켓광고 수집 실패(rc=3): 사유 미상 [로그인 필요 — 재시도 안 함]",
  kind: "login_required",
} as const;
const NOT_PICKED = { state: "no_response", attemptCount: 0 } as const;

function seedPanel() {
  sessionStorage.setItem(
    BULK_REFRESH_STORAGE_KEY,
    JSON.stringify({
      states: {
        [ROCKET.key]: { kind: "timeout", outcome: IN_FLIGHT },
        [OFIX_AD.key]: { kind: "failed", reason: LOGIN.reason, login: true, outcome: LOGIN },
        [OHITECH.key]: { kind: "timeout", outcome: NOT_PICKED },
      },
      panelOpen: true,
      savedAt: Date.now(),
    }),
  );
}

/** 패널에서 그 레인의 <li> 한 줄. 라벨로 찾는다 — 사람이 화면에서 찾는 방법과 같다. */
function laneRow(label: string): HTMLElement {
  const labelNode = screen.getByText((_t, el) => el?.tagName === "SPAN" && el.textContent?.startsWith(label) === true);
  const li = labelNode.closest("li");
  if (!li) throw new Error(`패널 줄을 못 찾았다: ${label}`);
  return li as HTMLElement;
}

describe("전체 갱신 패널 — 문구의 저자는 outcomeView 하나다", () => {
  beforeEach(() => {
    sessionStorage.clear();
    seedPanel();
  });
  afterEach(() => {
    cleanup();
    sessionStorage.clear();
  });

  it("집어가서 일하는 중 → 화면 글자가 outcomeView와 **같고**, 「Mac이 켜져 있는지」가 없다", () => {
    render(<Dashboard />);
    const row = laneRow(ROCKET.label);
    // ★대조 대상은 내가 이 파일에 적은 문자열이 아니라 **모듈이 짓는 문구**다 —
    //   그래야 문구를 고쳐도 이 단언이 낡지 않고, 저자가 갈라지면 즉시 터진다.
    expect(row.textContent).toContain(outcomeView(ROCKET, IN_FLIGHT).text);
    expect(row.textContent).toContain("백그라운드에서 계속");
    // 이 자리가 08-23 10:32 폰 오보의 자리다. 되살아나면 여기서 죽는다.
    expect(row.textContent).not.toContain("Mac이 켜져 있는지");
    expect(within(row).getByText(outcomeView(ROCKET, IN_FLIGHT).icon)).toBeTruthy();
  });

  it("세션 만료 → 「자동으로 이어받습니다」가 뜬다(「다시 누르세요」가 아니다)", () => {
    render(<Dashboard />);
    const row = laneRow(OFIX_AD.label);
    expect(row.textContent).toContain(outcomeView(OFIX_AD, LOGIN).text);
    expect(row.textContent).toContain("자동으로 이어받습니다");
    expect(row.textContent).not.toContain("다시 누르세요");
  });

  it("아무도 안 집었다 → **그때만** 「Mac이 켜져 있는지」가 참이다", () => {
    render(<Dashboard />);
    const row = laneRow(OHITECH.label);
    expect(row.textContent).toContain(outcomeView(OHITECH, NOT_PICKED).text);
    expect(row.textContent).toContain("Mac이 요청을 집지 않았습니다");
    expect(row.textContent).toContain("Mac이 켜져 있는지");
  });

  it("★세 경우가 화면에서 서로 다른 문구로 갈린다(옛 패널은 셋 다 같은 한 줄이었다)", () => {
    render(<Dashboard />);
    const texts = [ROCKET, OFIX_AD, OHITECH].map((s) => laneRow(s.label).textContent ?? "");
    expect(new Set(texts).size).toBe(3);
    // 옛 문구가 어느 줄에도 통째로 남아 있지 않다.
    for (const t of texts) expect(t).not.toContain("응답 없음 — Mac이 켜져 있는지 확인하세요");
  });
});

// ── 버튼을 실제로 눌러 본다 ────────────────────────────────────────────────
//
// ★위 블록은 sessionStorage를 직접 심어 «복원 → 렌더» 경로만 지킨다. 그래서 판정 결과를
//   패널 상태에 **싣는** 배선(runBulkRefresh의 put)이 끊겨도 살아남는다 — 그 배선이 바로
//   08-23 결함의 발원지다. 여기서는 버튼을 눌러 «판정 → 운반 → 렌더» 전 구간을 잇는다.
describe("전체 갱신 버튼 — 판정 결과가 패널까지 운반된다", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    cleanup();
    sessionStorage.clear();
    h.statusFor = null;
  });

  it("kind=login_required로 소멸한 요청 → 패널이 로그인 안내를 보인다(문구 매칭 없이)", async () => {
    // ★사유 문자열에 「로그인 필요」를 **일부러 넣지 않는다**. 백엔드의 기계 판독 분류(kind)만으로
    //   갈려야 하기 때문이다 — isLoginRequired를 1인자로 되돌리면(=W1이 없앤 결합) 이 줄이
    //   평범한 실패로 그려지고 테스트가 죽는다.
    const settled = {
      requested: false,
      last_success_at: null,
      last_error_at: "2026-08-23T13:20:00",
      last_error: "수집 실패(rc=3): 사유 미상",
      last_error_kind: "login_required",
      attempt_count: 1,
    };
    const baseline = { requested: false, last_success_at: null, last_error_at: null, last_error: null };
    h.statusFor = (_lane, nth) => (nth === 0 ? baseline : settled);

    render(<Dashboard />);
    fireEvent.click(screen.getByRole("button", { name: /전체 갱신/ }));
    await vi.advanceTimersByTimeAsync(10000); // 폴 1회(3초)면 정착한다

    const row = laneRow(OFIX_AD.label);
    expect(row.textContent).toContain("자동으로 이어받습니다");
    expect(row.textContent).toContain(OFIX_AD.account);
  });

  it("아무도 안 집은 요청 → 90초에 정착하고, 그 «타임아웃»도 상세를 실어 나른다", async () => {
    // ★이 케이스가 따로 필요한 이유: 위 테스트는 failed 경로만 지나서, 정착 상태 중
    //   **timeout**의 운반이 끊겨도(= 08-23 결함 그 자체) 살아남았다(자가 변이 MUT-4 생존).
    h.statusFor = (_lane, nth) =>
      nth === 0
        ? { requested: false, last_success_at: null, last_error_at: null, last_error: null, attempt_count: 0 }
        : { requested: true, last_success_at: null, last_error_at: null, last_error: null, attempt_count: 0 };

    render(<Dashboard />);
    fireEvent.click(screen.getByRole("button", { name: /전체 갱신/ }));
    await vi.advanceTimersByTimeAsync(120000); // T_pickup(90초)을 넘긴다 — T_max 600초는 안 기다린다

    const row = laneRow(OHITECH.label);
    expect(row.textContent).toContain(outcomeView(OHITECH, NOT_PICKED).text);
    expect(row.textContent).toContain("Mac이 요청을 집지 않았습니다");
    // 운반이 끊기면 화면은 「상세를 알 수 없습니다」로 떨어진다 — 그건 이 회차의 사실이 아니다.
    expect(row.textContent).not.toContain("상세를 알 수 없습니다");
  });
});
