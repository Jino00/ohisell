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
// (각 API의 고유 필드 — age_hours·attempt_count 등 — 는 판정에 쓰지 않는다.)
export interface RefreshStatusLike {
  requested: boolean;
  last_success_at: string | null;
  last_error_at: string | null;
  last_error: string | null;
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

  const before = await spec.getStatus();
  const baseline = before.last_success_at;
  const errBaseline = before.last_error_at; // 실패도 감지해야 "진행 중"과 구분된다
  await spec.request();

  const deadline = now() + timeoutMs;
  while (now() < deadline) {
    await sleep(pollMs);
    const st = await spec.getStatus();

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
