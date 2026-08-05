// bulkRefreshPersistence.ts — '전체 갱신' 패널 상태를 sessionStorage에 미러링하는 순수 저장소 계층.
// React 무관(테스트가 DOM 없이 순수 함수로 돈다) — Dashboard.tsx가 호출만 한다.
//
// 왜 필요한가(P1-7, 2026-08-05 적대 리뷰): bulkStates/bulkPanelOpen이 컴포넌트 로컬 useState라
// Dashboard를 벗어나면(다른 메뉴 이동, 새로고침) 어느 큐가 실패했는지 흔적이 0으로 사라졌다.
// '전체 갱신'은 title에도 "수 분 소요"라 적혀 있어 사용자가 지켜보지 않는 것이 자연스러운
// 사용 패턴인데, 다시 돌아왔을 때 실패가 안 보이면 "잠깐 떴다 사라진 에러 = 없는 에러"라는
// 이 PR이 고치려던 문제가 그대로 재현된다.
//
// ★진행 중(requesting/requested/fetching) 상태는 복원 시 그대로 보이면 거짓말이다 — 폴링은
//   언마운트로 이미 끊겼으므로 "지금도 수집 중"이 아니다. 복원할 때 "stale"로 바꿔 추적이
//   끊겼음을 드러낸다.
// ★오래된 결과가 영원히 남지 않도록 저장 시각(savedAt)을 같이 두고, 기본 30분이 지나면
//   복원하지 않는다(방치된 실패가 다음 날에도 "방금 실패"처럼 보이는 걸 막는다).

export type BulkQueueState =
  | { kind: "requesting"; retry: number; maxRetries: number }
  | { kind: "requested" }
  | { kind: "fetching" }
  | { kind: "done"; at: string }
  | { kind: "failed"; reason: string; login: boolean }
  | { kind: "timeout" }
  /** 복원된 상태가 마지막으로 어느 진행 단계였는지 — "추적이 끊겼다"는 사실만 전달한다. */
  | { kind: "stale"; lastPhase: "requesting" | "requested" | "fetching" };

export const BULK_REFRESH_STORAGE_KEY = "ohisell.dashboard.bulkRefresh.v1";

/** 이보다 오래된 저장분은 복원하지 않는다(기본 30분). */
export const BULK_REFRESH_MAX_AGE_MS = 30 * 60 * 1000;

/** sessionStorage와 같은 모양만 요구 — 테스트에서 인메모리 가짜로 주입한다. */
export interface SimpleStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

function browserSessionStorage(): SimpleStorage | null {
  try {
    // Node 테스트 환경(environment: "node")에는 sessionStorage 전역이 없다 — typeof는
    // 안전하게 "undefined"를 반환한다(참조 자체는 던지지 않는다).
    return typeof sessionStorage !== "undefined" ? sessionStorage : null;
  } catch {
    return null; // 프라이빗 모드 등에서 접근 자체가 던지는 브라우저 대비.
  }
}

interface PersistedShape {
  states: Record<string, BulkQueueState>;
  panelOpen: boolean;
  savedAt: number; // epoch ms
}

/** 진행 중이던 단계를 stale로 바꾼다 — 언마운트로 폴링이 끊겨 더는 사실이 아니기 때문. */
function toRestoredState(st: BulkQueueState): BulkQueueState {
  if (st.kind === "requesting" || st.kind === "requested" || st.kind === "fetching") {
    return { kind: "stale", lastPhase: st.kind };
  }
  return st;
}

export function saveBulkRefreshState(
  states: Record<string, BulkQueueState>,
  panelOpen: boolean,
  opts: { now?: number; storage?: SimpleStorage | null } = {},
): void {
  const storage = opts.storage !== undefined ? opts.storage : browserSessionStorage();
  if (!storage) return;
  const payload: PersistedShape = { states, panelOpen, savedAt: opts.now ?? Date.now() };
  try {
    storage.setItem(BULK_REFRESH_STORAGE_KEY, JSON.stringify(payload));
  } catch {
    // 저장 실패(용량 초과·프라이빗 모드 등) — 화면 기능 자체엔 영향 없으므로 조용히 무시한다.
  }
}

export function loadBulkRefreshState(
  opts: { now?: number; maxAgeMs?: number; storage?: SimpleStorage | null } = {},
): { states: Record<string, BulkQueueState>; panelOpen: boolean } | null {
  const storage = opts.storage !== undefined ? opts.storage : browserSessionStorage();
  if (!storage) return null;

  let raw: string | null;
  try {
    raw = storage.getItem(BULK_REFRESH_STORAGE_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;

  let parsed: PersistedShape;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null; // 손상된 JSON — 복원하지 않되 던지지도 않는다.
  }
  if (!parsed || typeof parsed.savedAt !== "number" || !parsed.states) return null;

  const now = opts.now ?? Date.now();
  const maxAgeMs = opts.maxAgeMs ?? BULK_REFRESH_MAX_AGE_MS;
  if (now - parsed.savedAt > maxAgeMs) return null; // 너무 오래된 결과는 복원하지 않는다.

  const states: Record<string, BulkQueueState> = {};
  for (const [k, v] of Object.entries(parsed.states)) {
    states[k] = toRestoredState(v as BulkQueueState);
  }
  return { states, panelOpen: !!parsed.panelOpen };
}

export function clearBulkRefreshState(opts: { storage?: SimpleStorage | null } = {}): void {
  const storage = opts.storage !== undefined ? opts.storage : browserSessionStorage();
  if (!storage) return;
  try {
    storage.removeItem(BULK_REFRESH_STORAGE_KEY);
  } catch {
    // 무시 — clear 실패는 다음 저장이 덮어쓴다.
  }
}
