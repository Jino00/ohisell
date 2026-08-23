// streamRefresh.ts — Mac 페처 갱신 버튼의 **단일 구현**.
//
// 왜 공용 모듈인가: 이 폴링 판정에는 라이브 사고로만 얻을 수 있었던 규칙 네 개가 박혀 있다
// (성공 우선·lease 재시도 중 실패 단정 금지·무작업 정상종료 구분·RG 다중업로드 정착 대기).
// 커맨드센터와 배너가 각자 구현하면 한쪽만 고쳐지고, **우회 사본이 원본 결함을 은폐**한다
// (LESSONS #55 — 같은 레포에서 EXECUTION_ACTIONS가 이미 당한 사고). 그래서 호출자는 늘어나도
// 판정 코드는 여기 하나만 존재한다.
//
// 2026-08-03 신설(배너 '지금 갱신'이 링크라 눌러도 아무 일이 없던 결함 수정). 그날 라이브 실측:
// 4개 스트림 중 2개가 세션 만료로 rc=3 — 로그인은 어떤 버튼도 대신할 수 없으므로 **어느 계정
// 으로 로그인해야 하는지**를 문구에 싣는 것이 이 모듈의 존재 이유 절반이다.
import {
  getAdCostRefreshStatus,
  requestAdCostRefresh,
  getWingVendorSummaryRefreshStatus,
  requestWingVendorSummaryRefresh,
  getWingRgSettlementRefreshStatus,
  requestWingRgSettlementRefresh,
  getRocketRefreshStatus,
  requestRocketRefresh,
  getOhitechAdRefreshStatus,
  requestOhitechAdRefresh,
} from "./api";

// 페처 갱신 상태 5종이 공통으로 갖는 필드만 추린 구조적 타입.
// (각 API의 고유 필드 — age_hours 등 — 는 판정에 쓰지 않는다.)
export interface RefreshStatusLike {
  requested: boolean;
  last_success_at: string | null;
  last_error_at: string | null;
  last_error: string | null;
  /**
   * lease 계약의 시도 횟수(0~3). ★성공/실패 **판정에는 쓰지 않는다** — 오직 "Mac이 요청을
   * 집어갔는가"를 화면에 보이기 위한 진행 표시용이다.
   * 근거(backend/app/services/coupang/refresh_contract.py): request_refresh가 0으로 리셋하고,
   * 데몬이 claim_refresh로 집어갈 때만 +1 된다 → requested && attempt_count>0 ⟺ Mac 수령.
   * 없는 응답(구버전·다른 표면)도 받아들이도록 optional — 없으면 이 표시만 생략된다.
   */
  attempt_count?: number;
  /**
   * 지금 페처가 임대를 붙잡고 실제로 일하는 중인가(refresh_contract.status_fields).
   * ★타임아웃 문구를 가르는 데 쓴다(2026-08-22 W4) — "215초가 지났다"는 사실 하나로
   * 「Mac 꺼짐」과 「아직 일하는 중」을 같은 말로 부르던 것을 나눈다.
   */
  in_flight?: boolean;
  /**
   * 마지막 실패의 **분류**(login_required / access_denied / mapping_broken / no_response / null).
   * ★2026-08-22 W1 신설. 종전엔 이 사실이 last_error **문자열** 안에만 있어서 프론트가
   * 문구 매칭으로 읽었다 — 문구를 바꾸면 화면이 조용히 깨지는 결합이었다.
   */
  last_error_kind?: string | null;
}

export type RefreshOutcome =
  | { state: "done" }
  | { state: "failed"; reason: string; kind?: string | null }
  // 타임아웃. ★단일 상태가 아니다(2026-08-22 W4): 215초가 지났다는 사실 하나에
  //   「Mac 꺼짐」·「데몬이 요청을 못 집음」·「아직 일하는 중」이 뭉쳐 있었고, 그래서
  //   Mac이 켜져 있고 방금 성공까지 한 상황에도 「Mac이 켜져 있는지 확인하세요」가 떴다.
  //   마지막으로 관측한 상태를 실어 보내 문구를 가른다.
  | { state: "no_response"; attemptCount?: number; inFlight?: boolean; kind?: string | null };

export interface StreamRefreshSpec {
  /** collection-status의 stream key와 동일해야 한다(배너 항목 ↔ 갱신 대상 매칭 키). */
  key: string;
  label: string;
  /** 로그인 안내에 실을 계정명. 창이 어느 계정인지 몰라 헤매던 문제(2026-08-03)의 해소책. */
  account: string;
  getStatus: () => Promise<RefreshStatusLike>;
  request: () => Promise<unknown>;
  /**
   * true면 (a)last_success_at 변화만으로 성공 판정하지 않고 요청 소멸(!requested)까지 기다리고,
   * (b)정착 시점에 이번 run이 남긴 실패 흔적이 있으면 **실패가 성공보다 우선**한다.
   * RG 전용: 한 회차가 (정산주기×리포트종류) 여러 엑셀을 올려서 첫 엑셀에 last_success_at이
   * 오른다 — 그것만 보고 이탈하면 뒤 엑셀이 실패한 반쪽 run을 "완료"로 표시한다(codex 3R[P1]).
   * ★(b)가 없으면 (a)는 오보를 **늦출 뿐 막지 못한다**(2026-08-03 codex P1) — 상세는
   * runStreamRefresh 안의 판정 주석.
   */
  settleBeforeSuccess?: boolean;
  /**
   * 무응답 안내에 붙일 창 식별자(예: "Chrome CDP 9225").
   * ★페처마다 전용 프로필·포트라 이 값이 곧 "어느 창을 보라"는 지시다. 2026-08-03 실측으로
   * 확정: 9222=오픽스 Wing · 9223=오하이테크 Wing · 9224=오하이테크 광고센터 ·
   * 9225=오하이테크 공급자허브. (구 문구가 로켓을 9223으로 잘못 안내하고 있었다.)
   */
  windowHint?: string;
  /**
   * 이 레인만의 픽업 상한(ms). 없으면 공통 기본값 PICKUP_TIMEOUT_MS(90초, 계약 승인값).
   *
   * ★왜 레인별인가 (2026-08-23 적대 리뷰 P1-1, 저장소 출하 기본값 실측):
   *     wing(ofix_sales·rg)   poll 15s + 쿨다운 45s → 최악 claim ~60s
   *     ad_cost(ofix_ad)      poll 15s + 쿨다운 45s → ~60s
   *     rocket(supplier_hub)  poll 30s, 쿨다운 없음 → ~30s
   *     **ohitech_ad          poll 60s + 쿨다운 60s → ~120s**  ← 90초를 넘는다
   *   `ohitech_ad_fetcher.py:1103` 쿨다운은 claim «전에» 검사하고 다음 폴 틱으로 미루므로
   *   60+60이 그대로 더해진다. 90초로 자르면 **집힐 예정인 요청을 「Mac이 꺼졌다」고 오보**한다 —
   *   고치려던 병을 다른 레인에서 새로 만드는 셈이다.
   * ★이건 «전 레인 공통 상한의 단순 상향»(계약 §4 금지선)이 아니다. 상한은 그 레인 데몬의
   *   캐던스에서 유도하고, 캐던스가 빠른 레인은 90초 그대로 둔다.
   */
  pickupTimeoutMs?: number;
  /**
   * 사람이 Mac에서 로그인하면 **데몬이 요청을 스스로 되살리는가**.
   *
   * ★처방이 갈리는 지점이라 spec에 둔다(2026-08-23 적대 리뷰 P1-2). 실측: 자동 재개
   *   (`_revive_lane` → `POST …/request-refresh`)는 `wing_browser_fetcher.py:1493·1979-1985`
   *   **한 곳에만** 있고 VS·RG 레인만 덮는다. `ohitech_ad_fetcher.py`·`ad_cost_browser_fetcher.py`
   *   ·`rocket_supplier_fetcher.py`에는 재요청이 0건이다.
   *   그런데 08-22 W3 이후 화면은 **6레인 전부**에 「로그인하면 자동으로 이어받습니다」라고
   *   말했다 — 셋에겐 거짓이고, 사람은 로그인만 하고 기다리다 아무 일도 안 일어난다.
   *   침묵보다 나쁜 실패는 **틀린 처방**이라는 이 계약의 전제가 그대로 적용되는 자리다.
   */
  autoResumeOnLogin?: boolean;
}

export interface RunnerDeps {
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  /**
   * 추적 상한 T_max(기본 600초, 계약 승인값). ★종전 215초 «고정»의 대체다 — 상한 하나로는
   * 「Mac이 안 집었다」와 「집어가서 오래 걸린다」를 가를 수 없어서, 짧게 잡으면 성공한 수집을
   * 오보하고(2026-08-07 supplier_hub 227초 실측) 길게 잡으면 Mac이 꺼진 날 3분 넘게 침묵했다.
   */
  timeoutMs?: number;
  /**
   * 픽업 상한 T_pickup(기본 90초, 계약 승인값). 이 시간까지 데몬이 요청을 집지 않으면
   * (attempt_count가 0에 머물면) 더 기다리지 않고 **조기 정착**한다 — 그 경우에만
   * 「Mac을 보라」가 참이고, 그 사실은 90초면 이미 안다.
   * ★attempt_count를 **모르는** 응답(구버전·다른 표면)에서는 조기 정착하지 않는다:
   *   없는 정보로 「Mac이 꺼졌다」고 단정하면 그게 새 오보다.
   */
  pickupTimeoutMs?: number;
  pollMs?: number;
  /**
   * 지금까지 화면이 **숨어 있던** 누적 시간(ms). 상한 계산에서 이 시간을 뺀다 — 상한이 재려는
   * 것은 «벽시계»가 아니라 «폴링이 실제로 돈 시간»이기 때문이다(2026-08-23 W5, 계약 §0-C-C).
   *
   * ★왜 필요한가: 폰이 잠기거나 탭이 백그라운드로 가면 모바일 사파리가 `setTimeout`을 늘리거나
   *   멈춘다. 그동안 `getStatus`는 한 번도 안 도는데 벽시계만 흐르므로, 화면을 다시 켜는 순간
   *   `now() >= deadline`이 즉시 참이 되어 **얼어붙은 lastSeen으로 거짓 타임아웃**을 낸다.
   *   수집은 Mac에서 멀쩡히 끝났는데 화면만 「응답 없음」이 되는 경로다.
   * 생략하면 `document.visibilitychange`로 자동 측정하고, DOM이 없는 환경(테스트·SSR)에서는 0이다.
   */
  hiddenMs?: () => number;
  /**
   * 정착 **전**의 진행 상태 통지(선택). 결과 판정과 무관 — 화면이 "지금 어디쯤인지"를
   * 말할 수 있게 하는 통로다. 스펙별로 오므로 여러 큐를 한 패널에 세울 수 있다.
   */
  onPhase?: (spec: StreamRefreshSpec, phase: RefreshPhase) => void;
}

/**
 * 큐 하나의 진행 단계. 정착(RefreshOutcome) 전까지만 흐른다.
 *  requesting → requested → fetching → (RefreshOutcome)
 * ★fetching은 attempt_count로만 알 수 있고, 그 필드가 없는 표면에서는 requested에 머문다
 *   — 없는 정보를 있는 척 만들지 않는다.
 */
export type RefreshPhase =
  /** POST 시도 중. retry=0이면 첫 시도, 1 이상이면 재시도 n회차(백오프 대기 포함). */
  | { kind: "requesting"; retry: number; maxRetries: number }
  /** POST 성공 = prod에 요청 플래그가 섰다. 이제 Mac 데몬의 폴링(15~60초)을 기다린다. */
  | { kind: "requested" }
  /** Mac 데몬이 요청을 집어갔다(lease claim) = 실제 수집 중. */
  | { kind: "fetching" };

/**
 * request-refresh POST 재시도 지연(ms) — 첫 시도 실패 후 이 순서로 최대 3회 더 시도한다.
 *
 * ★왜 필요한가(2026-08-05 라이브 사고): prod가 간헐 502를 내는 동안 갱신 버튼을 누르면
 * **POST 자체가 유실**된다. 폴링은 멀쩡히 돌지만 요청 플래그가 안 섰으니 Mac은 영원히
 * 아무것도 하지 않고, 화면엔 "요청 실패"가 잠깐 스쳤다 사라져 Jino는 "버튼이 안 눌린다"로
 * 인지했다. 502는 대개 수초짜리라 **한 번 더 던지면 붙는다**.
 * ★여기 한 곳에만 둔다 — 호출자가 각자 재시도를 짜면 한쪽만 고쳐진다(LESSONS #55).
 */
export const REQUEST_RETRY_DELAYS_MS = [2000, 5000, 10000];

/**
 * 픽업 상한 T_pickup — 「데몬이 요청을 집었는가」의 답을 기다리는 시간(ms).
 * 계약 승인값 90초(CONTRACT_collection_works_everywhere, Jino 2026-08-23).
 *
 * 근거: 데몬 폴링 주기가 15~60초라 정상이면 이 안에 attempt_count가 1 이상이 된다.
 * 90초를 넘도록 0이면 Mac이 꺼졌거나 데몬이 죽은 것이고, **그때만** 「Mac을 보라」가 참이다.
 */
export const PICKUP_TIMEOUT_MS = 90000;

/**
 * 추적 상한 T_max — 픽업된 요청의 «화면 추적»을 접는 시간(ms). 계약 승인값 600초.
 *
 * ★이 값은 «수집 제한시간»이 아니다. 도달해도 Mac의 수집은 계속되고, 화면은 실패가 아니라
 *   「백그라운드에서 계속됩니다」라고 말한다(outcomeView). 종전 215초는 이 둘을 구분하지
 *   못해, 2026-08-07 supplier_hub 227초 회차처럼 **성공한 수집을 실패로 그렸다**.
 */
export const MAX_TRACKING_MS = 600000;

/**
 * 절대 천장 배수 — 벽시계로 `T_max × 이 값`이 지나면 화면이 숨어 있든 말든 추적을 끝낸다.
 *
 * ★왜 필요한가(2026-08-23 적대 리뷰 P1-1): W5가 상한을 «깨어 있던 시간»으로 바꾸면서
 *   **종료 보장이 사라졌다**. 숨어 있는 동안 벽시계와 hidden 시간이 같이 자라 깨어 있던
 *   시간이 상수로 굳기 때문이다. 탭이 안 돌아오면 루프가 영원히 돌고(실측: 가상 83시간),
 *   프로미스가 정착하지 않아 패널은 「Mac이 수집 중…」에 박제된다.
 * ★3배인 이유: 정상 경로(깨어 있는 탭)는 T_max에서 이미 끝나므로 이 값은 «비정상 경로의
 *   상한»일 뿐이다. 너무 짧으면 잠깐 숨었다 돌아오는 흔한 경우를 잘라 W5가 무의미해지고,
 *   너무 길면 배터리·prod 부하가 는다. T_max 600초 기준 30분.
 */
export const ABSOLUTE_CEILING_MULTIPLE = 3;

/**
 * 폴링 루프 안 getStatus가 연속으로 실패해도 되는 횟수 상한(P1-6, 2026-08-05 적대 리뷰).
 *
 * ★왜 필요한가: 215초 폴링 창(pollMs마다 반복)은 POST 재시도 창(길어야 수십 초)보다 훨씬
 * 길어서, prod의 찰나 502·네트워크 끊김을 만날 확률은 폴링 쪽이 압도적으로 높다. 예전 주석은
 * "루프 자체가 pollMs마다 재시도라 감쌀 필요 없다"고 했지만 실제로는 루프에 try/catch가 없어
 * **getStatus 1회 reject가 runStreamRefresh 전체를 즉시 reject**시켰다 — Mac은 실제로 수집을
 * 완료하는데 화면은 "❌ 실패"로 영구 확정되고, 패널도 접히지 않고, 사용자는 이미 끝난 수집을
 * 재클릭으로 또 돌리게 된다. 그렇다고 실패를 무한히 삼키면 진짜 장애(Mac이 꺼짐 등)가 215초
 * 내내 "진행 중"으로 보이므로, 연속 N회 실패하면 이탈해 실패로 확정한다. 성공 시 리셋된다.
 */
export const POLL_FAILURE_LIMIT = 5;

/**
 * POST 재시도 대상인지 판정(P2-3, 적대 리뷰 2026-08-05).
 *
 * ★왜 필요한가: withRequestRetry는 원래 오류 종류를 가리지 않고 2+5+10초를 태웠다. 400/401·
 * "로그인 필요"는 몇 번을 더 던져도 붙지 않는데, 그 사용자는 재시도 지연만큼 헛되이 기다렸다.
 * 판단이 애매하면 보수적으로 — **네트워크 오류(상태 코드 없음)와 5xx만** 재시도 대상으로 본다.
 */
export function isRetryableRequestError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : typeof err === "string" ? err : "";
  if (isLoginRequired(msg)) return false;
  const m = msg.match(/API error (\d{3}):/);
  if (m) {
    const status = Number(m[1]);
    return status >= 500; // 4xx(400/401 등)는 재시도해도 안 붙는다 — 즉시 포기.
  }
  return true; // 상태 코드가 없는 오류(fetch 자체 실패 등)는 일시적 문제로 보고 재시도한다.
}

/**
 * 일시적 실패에 강한 호출 래퍼. 총 시도 = 1(첫 시도) + REQUEST_RETRY_DELAYS_MS.length.
 * onRetry는 **대기 시작 시점**에 불린다 — 화면이 "요청 재시도 2/3…"을 백오프 동안 띄우기 위해.
 * ★재시도 불가 오류(isRetryableRequestError가 false)는 즉시 포기한다(P2-3).
 */
export async function withRequestRetry<T>(
  fn: () => Promise<T>,
  opts: {
    sleep: (ms: number) => Promise<void>;
    delays?: number[];
    onRetry?: (retry: number, maxRetries: number, err: unknown) => void;
  },
): Promise<T> {
  const delays = opts.delays ?? REQUEST_RETRY_DELAYS_MS;
  let lastErr: unknown = new Error("요청 실패");
  for (let i = 0; i <= delays.length; i++) {
    if (i > 0) {
      // 대기 전에 알린다 — 대기 중 화면이 조용하면 "멈춘 것"으로 보인다(이 PR이 고치는 인지 실패).
      opts.onRetry?.(i, delays.length, lastErr);
      await opts.sleep(delays[i - 1]);
    }
    try {
      return await fn();
    } catch (e) {
      lastErr = e;
      if (!isRetryableRequestError(e)) throw e; // 4xx·로그인 필요 — 재시도해도 안 붙는다.
    }
  }
  throw lastErr;
}

/**
 * 화면이 숨어 있던 누적 시간을 재는 시계(2026-08-23 W5).
 *
 * DOM이 없는 환경(vitest node·SSR)에서는 «0을 돌려주는 시계»로 조용히 물러난다 — 없는 정보를
 * 지어내지 않고, 그 경우 종전과 똑같이 벽시계로 잰다.
 * ★`now`를 주입받는다: 테스트가 가상 시계를 쓰는데 여기만 `Date.now()`를 보면 두 시간축이 갈린다.
 */
function createHiddenClock(now: () => number): { hiddenMs: () => number; dispose: () => void } {
  if (typeof document === "undefined" || typeof document.addEventListener !== "function") {
    return { hiddenMs: () => 0, dispose: () => {} };
  }
  let total = 0;
  let hiddenSince: number | null = document.visibilityState === "hidden" ? now() : null;
  const onChange = () => {
    if (document.visibilityState === "hidden") {
      if (hiddenSince === null) hiddenSince = now();
    } else if (hiddenSince !== null) {
      total += now() - hiddenSince;
      hiddenSince = null;
    }
  };
  document.addEventListener("visibilitychange", onChange);
  return {
    // 아직 숨어 있는 중이면 그 구간도 «지금까지» 분을 더해 돌려준다(복귀 이벤트를 기다리지 않는다).
    hiddenMs: () => total + (hiddenSince === null ? 0 : now() - hiddenSince),
    dispose: () => document.removeEventListener("visibilitychange", onChange),
  };
}

/**
 * 갱신 1건: 요청 → last_success_at/last_error_at 변화 폴링 → 결과 판정.
 *
 * 판정 순서를 바꾸지 말 것. 아래 분기는 전부 라이브 오보 사고의 처방이다.
 * 순서는 스트림 종류에 따라 갈린다: RG(settleBeforeSuccess)는 실패 우선, 나머지는 성공 우선.
 */
export async function runStreamRefresh(
  spec: StreamRefreshSpec,
  deps: RunnerDeps = {},
): Promise<RefreshOutcome> {
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const now = deps.now ?? (() => Date.now());
  const timeoutMs = deps.timeoutMs ?? MAX_TRACKING_MS;
  // 호출자 지정 > 레인 고유값 > 공통 기본값(계약 승인 90초) 순.
  const pickupTimeoutMs = deps.pickupTimeoutMs ?? spec.pickupTimeoutMs ?? PICKUP_TIMEOUT_MS;
  const pollMs = deps.pollMs ?? 3000;

  const maxRetries = REQUEST_RETRY_DELAYS_MS.length;
  const emit = (phase: RefreshPhase) => deps.onPhase?.(spec, phase);
  const onRetry = (retry: number) => emit({ kind: "requesting", retry, maxRetries });

  emit({ kind: "requesting", retry: 0, maxRetries });
  // ★baseline 조회도 같은 래퍼로 감싼다: 502 창은 GET/POST를 가리지 않으므로, 여기서 한 번
  //   튕기면 POST를 던져보지도 못하고 "요청 실패"로 끝난다 — 재시도를 넣은 이유가 그대로 샌다.
  //   폴링 중의 getStatus는 아래 루프에서 **따로** try/catch로 감싼다(POLL_FAILURE_LIMIT 참조) —
  //   215초 폴링 창은 POST 재시도 창보다 훨씬 길어 502를 만날 확률이 압도적으로 높다.
  const before = await withRequestRetry(() => spec.getStatus(), { sleep, onRetry });
  const baseline = before.last_success_at;
  const errBaseline = before.last_error_at; // 실패도 감지해야 "진행 중"과 구분된다
  await withRequestRetry(() => spec.request(), { sleep, onRetry });
  emit({ kind: "requested" });

  // ── 2단 판정 (2026-08-23 W3, 계약 CONTRACT_collection_works_everywhere §3) ────────
  // 종전: 상한 하나(215초). 그 한 숫자에 두 질문이 뭉쳐 있었다 —
  //   ①「Mac이 요청을 집기는 했나」(답은 보통 30초 안에 나온다)
  //   ②「집어간 수집이 끝났나」(로그인·옵션보고서가 끼면 수 분이 걸린다)
  // 그래서 어느 값을 골라도 한쪽이 틀렸다: 짧으면 성공한 수집을 「응답 없음」이라 부르고
  // (2026-08-07 supplier_hub 227초), 길면 Mac이 꺼진 날 3분 넘게 아무 말도 못 했다.
  // ⇒ 픽업 전에는 T_pickup(90초)에서 조기 정착하고, 픽업 뒤에는 T_max(600초)까지 «추적»한다.
  //   T_max 도달은 실패가 아니다 — 수집은 Mac에서 계속되고, 화면은 그렇게 말한다(outcomeView).
  const startedAt = now();
  // ★상한은 «벽시계»가 아니라 «폴링이 실제로 돈 시간»으로 잰다(2026-08-23 W5, 계약 §0-C-C).
  //   폰이 잠긴 동안 setTimeout이 멈추면 getStatus는 한 번도 안 도는데 벽시계만 흐른다 —
  //   그 시간을 상한에 계상하면 화면을 켜는 순간 «폴링을 한 번도 못 해 본 채» 타임아웃이 난다.
  const hiddenClock = deps.hiddenMs ? null : createHiddenClock(now);
  const hiddenMs = deps.hiddenMs ?? hiddenClock!.hiddenMs;
  const awakeElapsed = () => now() - startedAt - hiddenMs();
  // ★절대 천장 (2026-08-23 적대 리뷰 P1-1). «깨어 있던 시간»만으로 루프를 돌리면 **끝난다는
  //   보장이 사라진다**: 화면이 숨어 있는 동안 `now()`와 `hiddenMs()`가 같은 속도로 자라
  //   awakeElapsed가 상수로 굳고, 탭이 끝내 돌아오지 않으면 루프가 영원히 돈다(리뷰 실측:
  //   가상 벽시계 83시간·폴 10만 회). 데스크톱 Chrome 백그라운드 탭은 setTimeout을 «멈추는»
  //   게 아니라 ~1분에 1회로 스로틀하므로, 그동안 6레인이 prod를 계속 두드리고 프로미스는
  //   영영 정착하지 않아 패널이 「Mac이 수집 중…」에 고정된다 — 합격 ④에 직접 걸린다.
  //   종전 `now() < deadline`이 갖고 있던 liveness 천장을 **복원**하는 것이지 상한을 올리는
  //   것이 아니다(§4 금지선 무관): 정상 경로의 판정은 여전히 awakeElapsed가 한다.
  const absoluteCeilingMs = timeoutMs * ABSOLUTE_CEILING_MULTIPLE;
  let sawFetching = false;
  let pollFailures = 0;
  // ★타임아웃 문구를 가르려면 «마지막으로 본 상태»가 필요하다(W4). 루프 안에서만 살아 있던
  //   st를 밖으로 들고 나간다 — 없으면 타임아웃 시점에 우리가 아는 게 "215초 지났다"뿐이다.
  let lastSeen: RefreshStatusLike | null = null;

  /**
   * 상태 1건에 대한 정착 판정. 정착이면 outcome, 아니면 null.
   *
   * ★루프에서 «추출»만 했다 — 분기 순서·조건은 한 글자도 바꾸지 않았다(위 docstring의 금지).
   *   추출한 이유는 하나다: 마감 직후 **판정 전 강제 1회 조회**(W5)가 같은 판정을 써야 하는데,
   *   사본을 만들면 둘 중 하나만 고쳐지는 그 병이 그대로 재발한다(LESSONS #55).
   */
  const settleVerdict = (st: RefreshStatusLike): RefreshOutcome | null => {
    // 페처가 **종료된** 실패를 보고하면 즉시 이탈 — 이게 없으면 이미 끝난 실패를 215초 헛기다린다.
    // ★requested가 아직 true면 재시도가 남아 있다는 뜻(lease 계약, 2026-07-27) — 여기서
    // 이탈하면 1회차 실패를 최종 실패로 오보한다. 요청이 소멸(=재시도 소진/로그인 필요)한
    // 뒤에야 실패로 판정한다. last_error에는 소멸 사유가 들어 있다.
    const settledFailure =
      !!st.last_error_at && st.last_error_at !== errBaseline && !st.requested;

    // ★RG(settleBeforeSuccess)만 실패 우선. 아래 '성공 우선'보다 먼저 평가한다.
    // 왜 예외인가(2026-08-03 codex P1): RG 한 회차는 여러 엑셀을 올리므로 첫 엑셀에서 이미
    // last_success_at이 baseline을 벗어난다. 그 뒤 뒷단이 실패해 요청이 소멸하면 성공 조건
    // (변한 success + !requested)이 **먼저** 참이 되어, 정산이 반쪽만 들어온 run을 "✅ 완료"로
    // 표시했다 — settleBeforeSuccess가 막으려던 바로 그 오보를 스스로 통과시키고 있었다.
    //
    // 이 판정이 안전한 근거는 백엔드 계약이다(refresh_contract.py · rg_settlement_sync.py):
    //   run 중 upload  → rg_mark_heartbeat: last_success_at=now + **요청이 살아있을 때만** error=NULL
    //   run 정상종료   → refresh-complete → mark_success(clear_error=True): 요청 소멸 + error=NULL
    //   run 실패종료   → report_failure(소멸 사유)·_reap_exhausted: last_error_at=now + 요청 소멸
    // 즉 요청이 소멸한 시점에 이번 run이 남긴 실패 흔적이 살아 있다 ⟺ 그 run은 실패로 끝났다.
    // 시각 대소 비교로 추측하지 않는다.
    // ★heartbeat의 "요청이 살아있을 때만"이 이 동치의 전제다(codex 1R·2R[P1]). 무조건 지우면
    // 클라 타임아웃(60s) 뒤 서버가 완주해 **늦게 도착한 업로드**가 방금 기록된 terminal error를
    // 지워, 여기서 다시 반쪽 run이 done으로 샌다. 백엔드 가드를 되돌리면 이 판정도 무너진다.
    //
    // 2026-07-17 '성공 우선' 사고와 상충하지 않는 이유: 그 사고(업로드는 서버에서 완주,
    // 페처는 클라 타임아웃으로 실패 보고 — 실측 순서는 last_error_at > last_success_at,
    // 커밋 7118ef5)는 lease 계약 도입 이전이었다. 지금 그 경로는 kind 없는 평범한 실패라
    // 요청이 살아남아(재시도) !requested가 거짓 → 여기서 이탈하지 않고, 재시도가 완주하면
    // 실패 흔적이 지워져 done이 된다. 3회 모두 그렇게 끝날 때만 실패로 보고되는데, 정산(돈)
    // 데이터에서는 **거짓 완료가 거짓 실패보다 훨씬 나쁘다**(재클릭은 무해).
    if (spec.settleBeforeSuccess && settledFailure) {
      return { state: "failed", reason: st.last_error || "원인 미상", kind: st.last_error_kind };
    }

    // ★성공 우선(순서 바꾸지 말 것 — 비RG): 둘 다 변했으면 성공이 이긴다. 라이브 실측
    // (2026-07-17 RG): 업로드가 클라 타임아웃(60s)을 넘겨 페처는 실패로 보고했지만
    // 서버는 완주해 success/error가 138ms 차로 함께 갱신됐다 — 데이터는 실제로 들어왔다.
    if (
      st.last_success_at &&
      st.last_success_at !== baseline &&
      (!spec.settleBeforeSuccess || !st.requested)
    ) {
      return { state: "done" };
    }

    if (settledFailure) {
      return { state: "failed", reason: st.last_error || "원인 미상", kind: st.last_error_kind };
    }

    // 새 실패 없이 요청만 사라졌다 = 수집이 정상 종료됐다(예: RG "받을 정산주기 없음").
    // 이 분기가 없으면 성공한 무작업 회차를 타임아웃까지 기다린 뒤 "응답 없음"으로 오보한다.
    if (!st.requested) return { state: "done" };

    return null; // 아직 정착 안 함 — 계속 본다.
  };

  try {
    while (awakeElapsed() < timeoutMs && now() - startedAt < absoluteCeilingMs) {
      await sleep(pollMs);

      let st: RefreshStatusLike;
      try {
        st = await spec.getStatus();
        lastSeen = st;
        pollFailures = 0; // 성공하면 연속 실패 카운터를 리셋한다.
      } catch (e) {
        pollFailures += 1;
        if (pollFailures >= POLL_FAILURE_LIMIT) {
          // 연속 N회 실패 — 일시적 문제가 아니라고 보고 실패로 확정한다(무한히 삼키지 않는다).
          return {
            state: "failed",
            reason: e instanceof Error ? e.message : "폴링 실패(연속 오류)",
          };
        }
        continue; // 일시적 실패(찰나 502 등) — 다음 폴에서 다시 시도한다. 폴링 창이 이를 흡수한다.
      }

      // Mac 데몬이 요청을 집어갔다 → "요청 전달됨"과 "실제 수집 중"을 구분해 보여준다.
      // 판정에는 관여하지 않는다(정착 분기들은 attempt_count를 보지 않는다).
      if (!sawFetching && st.requested && (st.attempt_count ?? 0) > 0) {
        sawFetching = true;
        emit({ kind: "fetching" });
      }

      const verdict = settleVerdict(st);
      if (verdict) return verdict;

      // ★T_pickup 초과 = 「아무도 안 집었다」 조기 정착(W3). 위 정착 판정 **뒤**에 둔다 —
      //   성공·실패가 이미 났으면 그게 이긴다. attempt_count를 모르는 응답에서는 발동하지 않는다.
      //   시간은 «깨어 있던» 것으로 잰다(W5) — 폰이 잠긴 동안 데몬이 못 집은 것은 데몬 탓이 아니다.
      if (
        !sawFetching &&
        awakeElapsed() >= pickupTimeoutMs &&
        st.requested &&
        st.attempt_count !== undefined &&
        st.attempt_count === 0
      ) {
        return {
          state: "no_response",
          attemptCount: 0,
          inFlight: st.in_flight,
          kind: st.last_error_kind,
        };
      }
    }

    // ★판정 전 강제 1회 조회 (2026-08-23 W5, 계약 §3) — 여기가 이 슬라이스의 본체다.
    //   마감 시점의 `lastSeen`은 **마지막 폴의 값**이고, 폰이 잠겨 있었다면 그 폴은 아주 오래
    //   전 것이다(모바일 사파리가 setTimeout을 멈춘다). 그 얼어붙은 값으로 판정하면 Mac에서
    //   이미 끝난 수집을 「응답 없음」이라 부른다 — 계약 §0-C-C가 「잠재 결함」으로 적어 둔 자리다.
    //   그래서 마감했다고 바로 단정하지 않고, **지금 상태를 한 번 더 확인하고** 판정한다.
    try {
      const st = await spec.getStatus();
      lastSeen = st;
      const verdict = settleVerdict(st);
      if (verdict) return verdict;
    } catch {
      // 마지막 확인이 실패하면 아래에서 lastSeen으로 판정한다 — 없는 정보를 지어내지 않는다.
    }

    // ★T_max 도달 = «추적 종료»이지 실패도 «Mac 꺼짐»도 아니다(W3·W4). 여기까지 왔다는 것은
    //   데몬이 요청을 집었거나(sawFetching) attempt_count를 모르는 표면이라는 뜻이다 —
    //   집지도 않은 요청은 위 T_pickup 분기가 이미 조기 정착시켰다. 마지막으로 본 상태를 실어
    //   보내 outcomeView가 처방이 다른 경우를 갈라 말하게 한다.
    return {
      state: "no_response",
      attemptCount: lastSeen?.attempt_count,
      inFlight: lastSeen?.in_flight,
      kind: lastSeen?.last_error_kind,
    };
  } finally {
    hiddenClock?.dispose(); // 리스너를 남기지 않는다 — 버튼을 여러 번 누르면 그만큼 쌓인다.
  }
}

/**
 * 페처가 "로그인 필요"로 요청을 소멸시켰는지.
 *
 * ★판정이 아니라 **안내 강화**에만 쓴다. 성공/실패는 위 runStreamRefresh가 상태 필드로만
 * 정하고, 이 문자열 매칭이 빗나가도 원문 사유는 그대로 노출된다(문구가 바뀌어도 조용히
 * 오작동하지 않는다). prod 실측 문구: "…(rc=3) [로그인 필요 — 재시도 안 함(…)]".
 */
export function isLoginRequired(
  reason: string | null | undefined,
  kind?: string | null,
): boolean {
  // ★kind가 «오면» 그것이 정본이다(2026-08-22 W1) — 백엔드가 기계 판독 값으로 알려주므로
  //   문구가 바뀌어도 안 깨진다. 문자열 매칭은 kind를 **모르는** 응답용 폴백으로만 남긴다.
  // ★null과 undefined를 가른다: null = 백엔드가 「분류된 실패 없음」이라고 **말한 것**이므로
  //   정본이고, undefined = 그 필드를 모르는 응답(구버전 백엔드·프론트 선배포 창)이라 폴백이다.
  //   섞으면 백엔드가 「로그인 문제 아님」이라 판정한 실패에도 문구 매칭이 로그인 꼬리표를
  //   붙인다 — 그게 이번에 없애려는 결합 그 자체다.
  if (kind !== undefined) return kind === "login_required";
  return !!reason && reason.includes("로그인 필요");
}

/**
 * 결과 1건의 화면 표현 — **문구의 유일한 저자**(2026-08-23 W2).
 *
 * 왜 문자열이 아니라 구조인가: 「전체 갱신」 패널은 아이콘·라벨·문구를 각각 다른 칸에
 * 렌더하느라 describeOutcome(한 줄 문자열)을 못 쓰고 **자기 문구를 따로 썼다**. 그래서
 * 2026-08-22 W4가 타임아웃을 세 경우로 가른 뒤에도 패널만 옛 한 문구
 * 「응답 없음 — Mac이 켜져 있는지 확인하세요」에 머물렀고, 08-23 10:32 폰 화면에서
 * **수집에 성공한 레인**에 그 오보가 떴다(수집은 10:32:54 성공, prod `fresh`).
 * ⇒ 판정 코드가 한 곳이어야 하는 것과 같은 이유로(모듈 머리말·LESSONS #55) **처방 문구도
 *   한 곳**이어야 한다. 라벨을 뺀 조각을 돌려주면 라벨을 따로 그리는 표면도 이걸 쓸 수 있다.
 *
 * tone은 의미값이다 — Tailwind 클래스는 화면 몫이다(lib이 프레젠테이션을 들고 있지 않는다).
 */
export type OutcomeTone = "progress" | "ok" | "warn" | "error";

export interface OutcomeView {
  icon: string;
  /** 라벨을 뺀 «사실 + 처방» 한 줄. 계정명은 여기 남는다 — 어느 창에 로그인할지가 이 모듈의 존재 이유 절반이다. */
  text: string;
  tone: OutcomeTone;
}

export function outcomeView(spec: StreamRefreshSpec, outcome: RefreshOutcome | null): OutcomeView {
  const loginView = (): OutcomeView => ({
    icon: "🔑",
    // ★처방은 «그 레인에 자동 재개가 배선돼 있는가»로 갈린다(2026-08-23 적대 리뷰 P1-2).
    //   - 배선된 레인(wing: 판매분석·RG): 08-22 W3 이후 로그인만 하면 데몬이 요청을 되살린다.
    //     「다시 누르세요」는 참이 아니고, 그 문구 탓에 사람이 로그인하고도 폰으로 돌아가 또 눌렀다.
    //   - 배선 없는 레인(광고비·로켓광고·공급자허브): 되살리는 주체가 **없다**. 여기에
    //     「자동으로 이어받습니다」를 쓰면 사람은 로그인만 하고 영영 기다린다 — 틀린 처방이다.
    text: spec.autoResumeOnLogin
      ? `${spec.account} 로그인 필요(Mac Chrome 탭에서 로그인하면 자동으로 이어받습니다)`
      : `${spec.account} 로그인 필요(Mac Chrome 탭에서 로그인한 뒤 「전체 갱신」을 다시 눌러주세요 — 이 레인은 자동 재개가 없습니다)`,
    tone: "error",
  });

  if (!outcome) return { icon: "⏳", text: "진행 중", tone: "progress" };
  if (outcome.state === "done") return { icon: "✅", text: "완료", tone: "ok" };
  if (outcome.state === "failed") {
    return isLoginRequired(outcome.reason, outcome.kind)
      ? loginView()
      : { icon: "❌", text: `실패(${outcome.reason})`, tone: "error" };
  }

  // ── 타임아웃(no_response) 3분할 (2026-08-22 W4) ──────────────────────────
  // 종전엔 여기가 한 문구였다: 「응답 없음 — Mac이 켜져 있는지 확인하세요」.
  // 2026-08-22 10:47 실측에서 그 문구가 떴을 때 Mac은 **켜져 있었고 수집 중이었다** —
  // 옆 레인이 로그인 대기로 폴 루프를 3분 점유해 이 요청을 늦게 집었을 뿐이다.
  // 틀린 처방("Mac을 켜세요")은 침묵보다 나은 실패가 아니다.
  if (isLoginRequired(null, outcome.kind)) return loginView();
  if (outcome.inFlight) {
    // Mac이 임대를 붙잡고 실제로 일하는 중 — 화면만 먼저 포기한 것이다. 실패가 아니다.
    return {
      icon: "⏱️",
      text: "수집이 백그라운드에서 계속됩니다(화면 대기 시간 초과). 잠시 뒤 새로고침하세요",
      tone: "warn",
    };
  }
  if ((outcome.attemptCount ?? 0) === 0) {
    // 요청은 걸렸는데 아무도 claim하지 않았다 = Mac이 꺼졌거나 데몬이 죽었다.
    // 이 경우에만 「Mac을 보라」가 참이다.
    const where = spec.windowHint ? `, ${spec.windowHint} 창이 떠 있는지` : "";
    return {
      icon: "⚠️",
      text: `Mac이 요청을 집지 않았습니다(Mac이 켜져 있는지${where} 확인하세요)`,
      tone: "warn",
    };
  }
  // 집어갔는데 정착 보고가 안 왔다 — Mac 탓으로 돌리지 않는다.
  return {
    icon: "⚠️",
    text: "응답 지연 — Mac이 요청을 받았으나 아직 결과가 없습니다(잠시 뒤 새로고침)",
    tone: "warn",
  };
}

/**
 * 결과 1건을 사람이 읽는 **한 줄**로. 실패엔 어느 계정인지가 반드시 붙는다.
 * 라벨을 한 줄 안에 넣는 표면(커맨드센터 배너·통합 대사)이 쓴다 — 조립만 하고 문구는
 * outcomeView가 짓는다.
 */
export function describeOutcome(spec: StreamRefreshSpec, outcome: RefreshOutcome | null): string {
  const v = outcomeView(spec, outcome);
  return `${v.icon} ${spec.label} — ${v.text}`;
}

/**
 * 여러 스트림 동시 갱신. **계정별로 정착하는 즉시 반영**한다(codex R1[P2] 계열 규칙):
 * Promise.all 결과를 한꺼번에 쓰면 한쪽 데몬이 꺼져 있을 때 이미 끝난 쪽이 상대의 215초
 * 타임아웃까지 인질로 잡힌다(30초 완료 → 3분 넘게 화면에 아무것도 안 뜸).
 *
 * onSettled는 한 건이 정착할 때마다 호출된다(진행 문구 갱신·데이터 리로드용).
 */
export async function runStreamsRefresh(
  specs: StreamRefreshSpec[],
  onSettled: (spec: StreamRefreshSpec, outcome: RefreshOutcome, all: Map<string, RefreshOutcome>) => void,
  deps: RunnerDeps = {},
): Promise<Map<string, RefreshOutcome>> {
  const results = new Map<string, RefreshOutcome>();
  await Promise.all(
    // 한 스트림의 요청 실패가 다른 스트림 결과를 삼키면 안 된다 → 스트림별 catch.
    specs.map(async (spec) => {
      const outcome = await runStreamRefresh(spec, deps).catch(
        (e: any): RefreshOutcome => ({ state: "failed", reason: e?.message || "요청 실패" }),
      );
      results.set(spec.key, outcome);
      onSettled(spec, outcome, results);
    }),
  );
  return results;
}

// ── 스트림 레지스트리 ───────────────────────────────────────────────
// key는 백엔드 collection-status의 stream key와 1:1. account는 2026-08-03 라이브 실측으로
// 확인한 소유 계정(페처마다 전용 Chrome 프로필이라 창은 섞이지 않는다).
export const STREAM_SPECS: StreamRefreshSpec[] = [
  {
    key: "ofix_sales",
    label: "ofix 판매분석",
    account: "오픽스(A01564720)",
    getStatus: getWingVendorSummaryRefreshStatus,
    request: requestWingVendorSummaryRefresh,
    // wing_browser_fetcher: poll 15s + 쿨다운 45s → 최악 ~60s. 90초 기본값으로 충분.
    // ★자동 재개 배선 있음(`_revive_lane`, wing_browser_fetcher.py:1983).
    autoResumeOnLogin: true,
  },
  {
    key: "ofix_ad",
    label: "ofix 광고비",
    account: "오픽스(A01564720)",
    getStatus: getAdCostRefreshStatus,
    request: requestAdCostRefresh,
    // ad_cost_browser_fetcher: poll 15s + 쿨다운 45s → ~60s. 자동 재개 배선 **없음**.
  },
  {
    key: "ohitech_ad",
    label: "ohitech 로켓광고",
    account: "오하이테크(A01029796)",
    getStatus: getOhitechAdRefreshStatus,
    request: requestOhitechAdRefresh,
    windowHint: "Chrome CDP 9224(오하이테크 광고센터)",
    // ★이 레인만 캐던스가 느리다: ohitech_ad_fetcher poll 60s + 쿨다운 60s → 최악 ~120s.
    //   90초로 자르면 집힐 예정인 요청을 「Mac이 꺼졌다」고 오보한다(적대 리뷰 P1-1).
    //   (60+60)×1.5 = 180초. 자동 재개 배선 **없음**.
    pickupTimeoutMs: 180000,
  },
  {
    key: "supplier_hub",
    label: "로켓 발주/정산",
    account: "오하이테크(A01029796)",
    getStatus: getRocketRefreshStatus,
    request: requestRocketRefresh,
    // ★9225다. 구 커맨드센터 문구는 9223(오하이테크 Wing)으로 잘못 안내했다 — 2026-08-03 실측 정정.
    windowHint: "Chrome CDP 9225(오하이테크 공급자허브)",
  },
];

/** RG 정산은 collection-status 스트림이 아니라 계정별 큐 2개 — 커맨드센터 전용. */
export const RG_STREAM_SPECS: StreamRefreshSpec[] = [
  {
    key: "rg_wing1",
    label: "오픽스",
    account: "오픽스(A01564720)",
    getStatus: () => getWingRgSettlementRefreshStatus("COUPANG_WING1"),
    request: () => requestWingRgSettlementRefresh("COUPANG_WING1"),
    settleBeforeSuccess: true,
    autoResumeOnLogin: true,
    // ★RG 자체 캐던스는 빠르다(poll 15s·버튼은 쿨다운 면제). 그런데 같은 프로세스의 판매분석
    //   run이 flock을 쥔 채 동기 실행되는 구간이 앞에 있어(wing_browser_fetcher.py:1797-1845),
    //   그 run이 길면 RG claim이 그만큼 밀린다.
    // ★근거 정정 (2026-08-23 적대 리뷰 2R): 08-22의 「약 3분 점유」를 만든 원인이던
    //   `_LOGIN_WAIT_S = 180` 블로킹 대기는 **이미 삭제됐다**(`:1328` 주석 · 데몬 두 경로 모두
    //   `login_wait_secs=0`). 즉 그 3분은 지금 재현되지 않는다. 남은 실제 구속은 **VS run 소요
    //   자체**인데 그 값은 아직 실측이 없다(계약 §7 미확인 그대로).
    //   ⇒ 180초는 «측정된 값»이 아니라 **미측정 구간에 대한 보수적 상한**이다. 오차 방향이
    //     안전해서 유지한다: 과하면 참인 「Mac 꺼짐」을 늦게 말할 뿐 **거짓 「Mac 꺼짐」을 만들지
    //     않는다**(그리고 이 PR 이전의 215초보다는 여전히 빠르다). VS run이 실측되면 그 값으로 좁힌다.
    pickupTimeoutMs: 180000,
  },
  {
    key: "rg_wing2",
    label: "오하이테크",
    account: "오하이테크(A01029796)",
    getStatus: () => getWingRgSettlementRefreshStatus("COUPANG_WING2"),
    request: () => requestWingRgSettlementRefresh("COUPANG_WING2"),
    settleBeforeSuccess: true,
    autoResumeOnLogin: true,
    pickupTimeoutMs: 180000, // 위 WING1과 같은 이유(같은 프로세스·같은 flock 구조)
  },
];

/**
 * 수동 수집 **전 큐**(대시보드 '전체 갱신' 전용).
 *
 * ★두 레지스트리를 합친 것이지 세 번째 목록이 아니다 — 스트림이 추가되면 위 두 배열만
 * 고치면 여기도 따라온다. 사본을 만들면 "버튼이 커버하지 않는 큐"가 조용히 생기고, 그건
 * 화면상 '전체 갱신'이라는 이름이 거짓말이 되는 실패다(이 기능이 고치려는 것과 같은 종류).
 *
 * 화면 버튼은 5개인데 큐가 6개인 이유: RG 정산 버튼 하나가 계정 큐 2개를 깨운다.
 */
export const ALL_REFRESH_SPECS: StreamRefreshSpec[] = [...STREAM_SPECS, ...RG_STREAM_SPECS];

/**
 * 스트림 key 하나로 spec을 집는다. **없는 key는 던진다** — 조용히 undefined를 돌려주면
 * 버튼이 아무것도 갱신하지 않으면서 성공한 척한다(specsForKeys와 같은 이유).
 *
 * 2026-08-07 커맨드센터 로컬 함수에서 여기로 올림 — 통합 대사 화면이 두 번째 호출자가 되면서
 * 사본이 둘이 될 참이었다(LESSONS #55: 우회 사본이 원본 결함을 은폐한다).
 */
export function specByKey(key: string): StreamRefreshSpec {
  const s = STREAM_SPECS.find((x) => x.key === key);
  if (!s) throw new Error(`알 수 없는 스트림: ${key}`);
  return s;
}

/**
 * 배너 항목(collection-status key)들을 갱신 대상 spec으로.
 *
 * ★모르는 key를 **조용히 버리지 않는다**(codex R1[P2]): 백엔드가 스트림 key를 추가·개명하면
 * 매칭이 0건이 되고, 버튼은 아무것도 갱신하지 않으면서 성공한 척한다 — 이 PR이 고치고 있는
 * "눌러도 아무 일이 없다"가 그대로 재현된다. 못 알아본 key를 돌려주고 호출자가 문구로 드러낸다.
 */
export function specsForKeys(keys: string[]): { specs: StreamRefreshSpec[]; unknown: string[] } {
  const specs = STREAM_SPECS.filter((s) => keys.includes(s.key));
  const known = new Set(STREAM_SPECS.map((s) => s.key));
  return { specs, unknown: keys.filter((k) => !known.has(k)) };
}
