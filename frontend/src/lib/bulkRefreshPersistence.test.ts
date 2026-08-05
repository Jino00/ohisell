// bulkRefreshPersistence.test.ts — P1-7 회귀: '전체 갱신' 패널 상태가 sessionStorage로 살아남는가.
//
// 적대 리뷰(2026-08-05): bulkStates/bulkPanelOpen이 컴포넌트 로컬 state라 페이지를 벗어나면
// 실패 흔적이 0으로 사라졌다. 여기서는 React 없이 순수 저장/복원 로직만 검증한다.
import { describe, it, expect } from "vitest";
import {
  saveBulkRefreshState,
  loadBulkRefreshState,
  clearBulkRefreshState,
  BULK_REFRESH_STORAGE_KEY,
  BULK_REFRESH_MAX_AGE_MS,
  type BulkQueueState,
  type SimpleStorage,
} from "./bulkRefreshPersistence";

// sessionStorage와 같은 모양의 인메모리 가짜(Node 테스트 환경엔 진짜 sessionStorage가 없다).
function fakeStorage(): SimpleStorage {
  const m = new Map<string, string>();
  return {
    getItem: (k) => (m.has(k) ? m.get(k)! : null),
    setItem: (k, v) => { m.set(k, v); },
    removeItem: (k) => { m.delete(k); },
  };
}

describe("saveBulkRefreshState / loadBulkRefreshState", () => {
  it("저장한 상태(진행 중이 아닌 것)를 그대로 복원한다", () => {
    const storage = fakeStorage();
    const states: Record<string, BulkQueueState> = {
      ofix_sales: { kind: "done", at: "12:00" },
      ofix_ad: { kind: "failed", reason: "타임아웃", login: false },
      supplier_hub: { kind: "timeout" },
    };
    saveBulkRefreshState(states, true, { now: 1000, storage });
    const restored = loadBulkRefreshState({ now: 1500, storage });
    expect(restored).toEqual({ states, panelOpen: true });
  });

  it("아무것도 저장 안 됐으면 null(패널을 강제로 열지 않는다)", () => {
    const storage = fakeStorage();
    expect(loadBulkRefreshState({ storage })).toBeNull();
  });

  // ── ★핵심: 진행 중 상태는 복원 시 "추적 중단됨"으로 바뀐다 ──────────────
  // 폴링은 언마운트로 이미 끊겼으므로 "지금도 수집 중"으로 보이면 그것도 거짓말이다.
  it("requesting/requested/fetching은 복원 시 stale로 바뀐다", () => {
    const storage = fakeStorage();
    const states: Record<string, BulkQueueState> = {
      supplier_hub: { kind: "fetching" },
      rg_wing1: { kind: "requested" },
      rg_wing2: { kind: "requesting", retry: 1, maxRetries: 3 },
    };
    saveBulkRefreshState(states, true, { now: 1000, storage });
    const restored = loadBulkRefreshState({ now: 1000, storage });
    expect(restored?.states.supplier_hub).toEqual({ kind: "stale", lastPhase: "fetching" });
    expect(restored?.states.rg_wing1).toEqual({ kind: "stale", lastPhase: "requested" });
    expect(restored?.states.rg_wing2).toEqual({ kind: "stale", lastPhase: "requesting" });
  });

  it("done/failed/timeout은 stale로 바뀌지 않는다(결과가 난 큐는 결과 그대로 보존)", () => {
    const storage = fakeStorage();
    const states: Record<string, BulkQueueState> = {
      a: { kind: "done", at: "09:00" },
      b: { kind: "failed", reason: "로그인 필요", login: true },
      c: { kind: "timeout" },
    };
    saveBulkRefreshState(states, false, { now: 0, storage });
    const restored = loadBulkRefreshState({ now: 0, storage });
    expect(restored?.states).toEqual(states);
  });

  // ── 타임스탬프 만료(오래된 결과가 영원히 남지 않는다) ────────────────
  it(`${BULK_REFRESH_MAX_AGE_MS}ms(기본 30분)보다 오래되면 복원하지 않는다`, () => {
    const storage = fakeStorage();
    saveBulkRefreshState({ x: { kind: "done", at: "12:00" } }, true, { now: 0, storage });
    const restored = loadBulkRefreshState({ now: BULK_REFRESH_MAX_AGE_MS + 1, storage });
    expect(restored).toBeNull();
  });

  it("경계값: maxAge 이내(정확히 같은 시각)는 여전히 복원된다", () => {
    const storage = fakeStorage();
    saveBulkRefreshState({ x: { kind: "done", at: "12:00" } }, true, { now: 0, storage });
    const restored = loadBulkRefreshState({ now: BULK_REFRESH_MAX_AGE_MS, storage });
    expect(restored).not.toBeNull();
  });

  it("커스텀 maxAgeMs를 존중한다", () => {
    const storage = fakeStorage();
    saveBulkRefreshState({ x: { kind: "done", at: "12:00" } }, true, { now: 0, storage });
    expect(loadBulkRefreshState({ now: 100, maxAgeMs: 50, storage })).toBeNull();
    expect(loadBulkRefreshState({ now: 50, maxAgeMs: 50, storage })).not.toBeNull();
  });

  it("손상된 JSON은 null을 반환한다(던지지 않는다)", () => {
    const storage = fakeStorage();
    storage.setItem(BULK_REFRESH_STORAGE_KEY, "{이건 JSON이 아니다");
    expect(loadBulkRefreshState({ storage })).toBeNull();
  });

  it("getItem이 던져도 loadBulkRefreshState는 던지지 않고 null(프라이빗 모드 대비)", () => {
    const storage: SimpleStorage = {
      getItem: () => { throw new Error("접근 불가"); },
      setItem: () => {},
      removeItem: () => {},
    };
    expect(loadBulkRefreshState({ storage })).toBeNull();
  });

  it("setItem이 던져도 saveBulkRefreshState는 조용히 무시한다(화면 기능에 영향 없음)", () => {
    const storage: SimpleStorage = {
      getItem: () => null,
      setItem: () => { throw new Error("저장 공간 부족"); },
      removeItem: () => {},
    };
    expect(() => saveBulkRefreshState({}, true, { now: 0, storage })).not.toThrow();
  });

  it("clearBulkRefreshState 이후엔 복원되지 않는다", () => {
    const storage = fakeStorage();
    saveBulkRefreshState({ x: { kind: "done", at: "12:00" } }, true, { now: 0, storage });
    clearBulkRefreshState({ storage });
    expect(loadBulkRefreshState({ now: 0, storage })).toBeNull();
  });
});
