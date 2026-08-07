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
}

export type RefreshOutcome =
  | { state: "done" }
  | { state: "failed"; reason: string }
  | { state: "no_response" }; // 타임아웃 = Mac 꺼짐·데몬 미설치·응답 없음

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
}

export interface RunnerDeps {
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  /** 215초 — 데몬 로그인 대기(180s) + fetch 여유까지 커버. */
  timeoutMs?: number;
  pollMs?: number;
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
  const timeoutMs = deps.timeoutMs ?? 215000;
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

  const deadline = now() + timeoutMs;
  let sawFetching = false;
  let pollFailures = 0;
  while (now() < deadline) {
    await sleep(pollMs);

    let st: RefreshStatusLike;
    try {
      st = await spec.getStatus();
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
      continue; // 일시적 실패(찰나 502 등) — 다음 폴에서 다시 시도한다. 215초 창이 이를 흡수한다.
    }

    // Mac 데몬이 요청을 집어갔다 → "요청 전달됨"과 "실제 수집 중"을 구분해 보여준다.
    // 판정에는 관여하지 않는다(아래 분기들은 attempt_count를 보지 않는다).
    if (!sawFetching && st.requested && (st.attempt_count ?? 0) > 0) {
      sawFetching = true;
      emit({ kind: "fetching" });
    }

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
      return { state: "failed", reason: st.last_error || "원인 미상" };
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
      return { state: "failed", reason: st.last_error || "원인 미상" };
    }

    // 새 실패 없이 요청만 사라졌다 = 수집이 정상 종료됐다(예: RG "받을 정산주기 없음").
    // 이 분기가 없으면 성공한 무작업 회차를 타임아웃까지 기다린 뒤 "응답 없음"으로 오보한다.
    if (!st.requested) return { state: "done" };
  }
  return { state: "no_response" };
}

/**
 * 페처가 "로그인 필요"로 요청을 소멸시켰는지.
 *
 * ★판정이 아니라 **안내 강화**에만 쓴다. 성공/실패는 위 runStreamRefresh가 상태 필드로만
 * 정하고, 이 문자열 매칭이 빗나가도 원문 사유는 그대로 노출된다(문구가 바뀌어도 조용히
 * 오작동하지 않는다). prod 실측 문구: "…(rc=3) [로그인 필요 — 재시도 안 함(…)]".
 */
export function isLoginRequired(reason: string | null | undefined): boolean {
  return !!reason && reason.includes("로그인 필요");
}

/** 결과 1건을 사람이 읽는 한 줄로. 실패엔 **어느 계정인지**를 반드시 붙인다. */
export function describeOutcome(spec: StreamRefreshSpec, outcome: RefreshOutcome | null): string {
  if (!outcome) return `⏳ ${spec.label} 진행 중`;
  if (outcome.state === "done") return `✅ ${spec.label} 완료`;
  if (outcome.state === "failed") {
    return isLoginRequired(outcome.reason)
      ? `🔑 ${spec.label} — ${spec.account} 로그인 필요(열린 Chrome 창에서 로그인 후 다시 누르세요)`
      : `❌ ${spec.label} 실패(${outcome.reason})`;
  }
  const where = spec.windowHint ? `, ${spec.windowHint} 창이 떠 있는지` : "";
  return `⚠️ ${spec.label} 응답 없음 — Mac이 켜져 있는지${where} 확인하세요`;
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
  },
  {
    key: "ofix_ad",
    label: "ofix 광고비",
    account: "오픽스(A01564720)",
    getStatus: getAdCostRefreshStatus,
    request: requestAdCostRefresh,
  },
  {
    key: "ohitech_ad",
    label: "ohitech 로켓광고",
    account: "오하이테크(A01029796)",
    getStatus: getOhitechAdRefreshStatus,
    request: requestOhitechAdRefresh,
    windowHint: "Chrome CDP 9224(오하이테크 광고센터)",
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
  },
  {
    key: "rg_wing2",
    label: "오하이테크",
    account: "오하이테크(A01029796)",
    getStatus: () => getWingRgSettlementRefreshStatus("COUPANG_WING2"),
    request: () => requestWingRgSettlementRefresh("COUPANG_WING2"),
    settleBeforeSuccess: true,
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
