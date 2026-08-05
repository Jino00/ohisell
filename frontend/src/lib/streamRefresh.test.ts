// streamRefresh.test.ts — 갱신 판정 4규칙 회귀.
// 이 규칙들은 전부 라이브 오보 사고의 처방이라, 리팩터가 하나라도 지우면 여기서 터져야 한다.
import { describe, it, expect } from "vitest";
import {
  runStreamRefresh,
  runStreamsRefresh,
  describeOutcome,
  isLoginRequired,
  specsForKeys,
  STREAM_SPECS,
  RG_STREAM_SPECS,
  ALL_REFRESH_SPECS,
  withRequestRetry,
  REQUEST_RETRY_DELAYS_MS,
  type RefreshStatusLike,
  type StreamRefreshSpec,
  type RefreshOutcome,
} from "./streamRefresh";

// 폴링 시퀀스를 그대로 재생하는 가짜 스트림. 시간은 가상(now/sleep 주입)이라 테스트가 즉시 끝난다.
function mkSpec(
  statuses: RefreshStatusLike[],
  over: Partial<StreamRefreshSpec> = {},
): { spec: StreamRefreshSpec; requested: () => number } {
  let i = 0;
  let requests = 0;
  const spec: StreamRefreshSpec = {
    key: "t",
    label: "테스트",
    account: "오하이테크(A01029796)",
    getStatus: async () => statuses[Math.min(i++, statuses.length - 1)],
    request: async () => { requests += 1; },
    ...over,
  };
  return { spec, requested: () => requests };
}

// 가상 시계: sleep이 시간을 앞으로 민다 → 타임아웃 경로도 실시간 대기 없이 검증된다.
function fakeClock() {
  let t = 0;
  return {
    now: () => t,
    sleep: async (ms: number) => { t += ms; },
  };
}

const S = (o: Partial<RefreshStatusLike>): RefreshStatusLike => ({
  requested: false,
  last_success_at: null,
  last_error_at: null,
  last_error: null,
  ...o,
});

describe("runStreamRefresh — 판정 규칙", () => {
  it("성공: last_success_at이 baseline에서 변하면 done", async () => {
    const { spec, requested } = mkSpec([
      S({ last_success_at: "T0" }),                        // before
      S({ last_success_at: "T0", requested: true }),       // 진행 중
      S({ last_success_at: "T1", requested: true }),       // 성공
    ]);
    expect(await runStreamRefresh(spec, fakeClock())).toEqual({ state: "done" });
    expect(requested()).toBe(1);
  });

  it("★성공 우선: 성공과 실패가 함께 갱신되면 성공이 이긴다", async () => {
    // 2026-07-17 RG 실측 — 페처는 클라 타임아웃으로 실패 보고했지만 서버는 완주(138ms 차).
    const { spec } = mkSpec([
      S({ last_success_at: "T0", last_error_at: "E0" }),
      S({ last_success_at: "T1", last_error_at: "E1", last_error: "타임아웃" }),
    ]);
    expect(await runStreamRefresh(spec, fakeClock())).toEqual({ state: "done" });
  });

  it("★lease: 재시도가 남아있으면(requested=true) 실패로 단정하지 않는다", async () => {
    const { spec } = mkSpec([
      S({ last_error_at: "E0" }),
      // 1회차 실패했지만 요청은 살아있음 → 아직 실패 아님
      S({ last_error_at: "E1", last_error: "1회차 실패", requested: true }),
      // 재시도가 성공
      S({ last_error_at: "E1", last_success_at: "T1", requested: false }),
    ]);
    expect(await runStreamRefresh(spec, fakeClock())).toEqual({ state: "done" });
  });

  it("실패: 요청이 소멸(!requested)한 뒤의 새 에러는 failed + 사유", async () => {
    const { spec } = mkSpec([
      S({ last_error_at: "E0" }),
      S({ last_error_at: "E1", last_error: "로그인 필요 — 재시도 안 함", requested: false }),
    ]);
    expect(await runStreamRefresh(spec, fakeClock())).toEqual({
      state: "failed",
      reason: "로그인 필요 — 재시도 안 함",
    });
  });

  it("★무작업 정상종료: 새 성공도 실패도 없이 요청만 사라지면 done", async () => {
    const { spec } = mkSpec([
      S({ last_success_at: "T0" }),
      S({ last_success_at: "T0", requested: false }), // 예: RG '받을 정산주기 없음'
    ]);
    expect(await runStreamRefresh(spec, fakeClock())).toEqual({ state: "done" });
  });

  it("무응답: 요청이 계속 살아있는 채 타임아웃이면 no_response", async () => {
    const { spec } = mkSpec([
      S({ requested: false }),
      S({ requested: true }), // 영원히 진행 중(데몬 무응답)
    ]);
    expect(await runStreamRefresh(spec, { ...fakeClock(), timeoutMs: 30000, pollMs: 3000 }))
      .toEqual({ state: "no_response" });
  });

  it("★RG(settleBeforeSuccess): 첫 엑셀로 성공이 올라도 요청이 살아있으면 아직 완료 아님", async () => {
    const { spec } = mkSpec(
      [
        S({ last_success_at: "T0" }),
        // 첫 엑셀 업로드로 success는 올랐지만 run은 계속 중 → 여기서 done이면 반쪽 run 오보
        S({ last_success_at: "T1", requested: true }),
        S({ last_success_at: "T2", requested: false }), // run 종료
      ],
      { settleBeforeSuccess: true },
    );
    expect(await runStreamRefresh(spec, fakeClock())).toEqual({ state: "done" });
  });

  it("RG가 아니면 요청이 살아있어도 성공 즉시 done (기존 커맨드센터 동작 보존)", async () => {
    const { spec } = mkSpec([
      S({ last_success_at: "T0" }),
      S({ last_success_at: "T1", requested: true }),
    ]);
    expect(await runStreamRefresh(spec, fakeClock())).toEqual({ state: "done" });
  });

  // ── RG 반쪽 run 오보(2026-08-03 codex P1) ────────────────────────────
  // 백엔드 계약이 이 세 케이스를 가른다(refresh_contract.py · rg_settlement_sync.py):
  //   성공 upload  → rg_mark_heartbeat: last_success_at=now, **last_error_at=NULL**
  //   run 정상종료 → refresh-complete → mark_success(clear_error=True): 요청 소멸 + **error=NULL**
  //   run 실패종료 → report_failure(소멸 사유 있음): last_error_at=now + 요청 소멸
  // 즉 **요청이 소멸한 시점에 실패 흔적이 남아 있다 ⟺ 그 run은 실패로 끝났다**.
  it("★RG 반쪽 run: 첫 엑셀 성공 → 마지막 엑셀 실패로 요청 소멸이면 failed", async () => {
    const { spec } = mkSpec(
      [
        S({ last_success_at: "T0", last_error_at: "E0" }),
        // 첫 엑셀 성공(heartbeat가 옛 실패 흔적을 지운다) — run은 계속 중
        S({ last_success_at: "T1", last_error_at: null, requested: true }),
        // 마지막 엑셀 실패 → 재시도 예산 소진 → 요청 소멸 + 새 실패 흔적
        S({ last_success_at: "T1", last_error_at: "E1",
            last_error: "RG 정산 수집 실패 [재시도 3회 소진]", requested: false }),
      ],
      { settleBeforeSuccess: true },
    );
    // 여기서 done이 나오면 정산 데이터가 반쪽인데 화면은 "✅ 완료"다(거짓 완료).
    expect(await runStreamRefresh(spec, fakeClock())).toEqual({
      state: "failed",
      reason: "RG 정산 수집 실패 [재시도 3회 소진]",
    });
  });

  it("★RG: 중간 실패 후 재시도가 완주하면 done — 실패 흔적은 백엔드가 지운다", async () => {
    const { spec } = mkSpec(
      [
        S({ last_success_at: "T0", last_error_at: "E0" }),
        // 1회차 중간 실패(lease만 반납 → 요청은 살아있음)
        S({ last_success_at: "T1", last_error_at: "E1", last_error: "타임아웃", requested: true }),
        // 2회차 완주 → refresh-complete(clear_error=True)가 요청과 실패 흔적을 함께 지운다
        S({ last_success_at: "T2", last_error_at: null, requested: false }),
      ],
      { settleBeforeSuccess: true },
    );
    expect(await runStreamRefresh(spec, fakeClock())).toEqual({ state: "done" });
  });

  it("RG: 버튼 이전의 낡은 실패 흔적은 실패로 읽지 않는다", async () => {
    const { spec } = mkSpec(
      [
        S({ last_success_at: "T0", last_error_at: "E0" }),
        // last_error_at이 baseline 그대로 = 이번 run이 남긴 게 아니다
        S({ last_success_at: "T1", last_error_at: "E0", requested: false }),
      ],
      { settleBeforeSuccess: true },
    );
    expect(await runStreamRefresh(spec, fakeClock())).toEqual({ state: "done" });
  });
});

describe("runStreamsRefresh — 다건", () => {
  it("한 건이 정착할 때마다 onSettled가 불린다(느린 건이 빠른 건을 인질로 잡지 않음)", async () => {
    const fast = mkSpec([S({ last_success_at: "A0" }), S({ last_success_at: "A1" })]).spec;
    const slow = mkSpec([
      S({ last_success_at: "B0" }),
      S({ last_success_at: "B0", requested: true }),
      S({ last_success_at: "B0", requested: true }),
      S({ last_success_at: "B1" }),
    ]).spec;
    const order: string[] = [];
    const res = await runStreamsRefresh(
      [{ ...fast, key: "fast" }, { ...slow, key: "slow" }],
      (spec) => order.push(spec.key),
      fakeClock(),
    );
    expect(order[0]).toBe("fast");            // 빠른 쪽이 먼저 보고된다
    expect(res.get("fast")).toEqual({ state: "done" });
    expect(res.get("slow")).toEqual({ state: "done" });
  });

  it("한 건이 throw해도 다른 건의 결과를 삼키지 않는다", async () => {
    const boom: StreamRefreshSpec = {
      key: "boom", label: "터짐", account: "오픽스(A01564720)",
      getStatus: async () => { throw new Error("네트워크"); },
      request: async () => {},
    };
    const ok = { ...mkSpec([S({ last_success_at: "A0" }), S({ last_success_at: "A1" })]).spec, key: "ok" };
    const res = await runStreamsRefresh([boom, ok], () => {}, fakeClock());
    expect(res.get("boom")).toEqual({ state: "failed", reason: "네트워크" });
    expect(res.get("ok")).toEqual({ state: "done" });
  });
});

describe("describeOutcome — 로그인 필요엔 계정명이 반드시 붙는다", () => {
  const spec = STREAM_SPECS.find((s) => s.key === "supplier_hub")!;

  it("로그인 필요면 계정명을 문구에 싣는다", () => {
    // prod 실측 문구(2026-08-03)
    const reason = "로켓 수집 실패(rc=3): 사유 미상 [로그인 필요 — 재시도 안 함(로그인 후 다시 갱신을 눌러주세요)]";
    const msg = describeOutcome(spec, { state: "failed", reason });
    expect(msg).toContain("오하이테크(A01029796)");
    expect(msg).toContain("로그인");
  });

  it("로그인 외 실패는 원문 사유를 그대로 노출한다(문자열 매칭이 빗나가도 침묵하지 않음)", () => {
    const msg = describeOutcome(spec, { state: "failed", reason: "디스크 가득" });
    expect(msg).toContain("디스크 가득");
  });

  it("진행 중·완료·무응답", () => {
    expect(describeOutcome(spec, null)).toContain("진행 중");
    expect(describeOutcome(spec, { state: "done" })).toContain("완료");
    expect(describeOutcome(spec, { state: "no_response" })).toContain("응답 없음");
  });
});

describe("isLoginRequired / specsForKeys", () => {
  it("로그인 필요 문구를 인식한다", () => {
    expect(isLoginRequired("… [로그인 필요 — 재시도 안 함]")).toBe(true);
    expect(isLoginRequired("타임아웃")).toBe(false);
    expect(isLoginRequired(null)).toBe(false);
  });

  it("배너 key → spec 매칭, 모르는 key는 **버리지 않고 돌려준다**", () => {
    // codex R1[P2]: 조용히 버리면 백엔드 key 개명 시 버튼이 아무것도 갱신하지 않으면서
    // 성공한 척한다 — 이 PR이 고치는 "눌러도 아무 일 없음"의 재발 경로.
    const { specs, unknown } = specsForKeys(["ohitech_ad", "supplier_hub", "존재하지않음"]);
    expect(specs.map((s) => s.key)).toEqual(["ohitech_ad", "supplier_hub"]);
    expect(unknown).toEqual(["존재하지않음"]);
  });

  it("전부 모르는 key면 specs는 비고 unknown이 전건을 담는다", () => {
    const { specs, unknown } = specsForKeys(["new_stream_v2"]);
    expect(specs).toEqual([]);
    expect(unknown).toEqual(["new_stream_v2"]);
  });

  it("레지스트리 key가 collection-status 스트림 4종과 일치한다", () => {
    // 백엔드가 key를 바꾸면 배너 버튼이 조용히 아무것도 갱신하지 않게 된다 → 여기서 고정.
    expect(STREAM_SPECS.map((s) => s.key).sort()).toEqual(
      ["ofix_ad", "ofix_sales", "ohitech_ad", "supplier_hub"],
    );
  });

  it("모든 spec이 계정명을 갖는다(로그인 안내의 전제)", () => {
    for (const s of STREAM_SPECS) expect(s.account).toMatch(/A0\d{7}/);
  });
});

describe("타입 계약", () => {
  it("RefreshOutcome는 세 상태뿐 — 새 상태를 늘리면 호출자 분기가 조용히 빠진다", () => {
    const outcomes: RefreshOutcome[] = [
      { state: "done" },
      { state: "failed", reason: "x" },
      { state: "no_response" },
    ];
    expect(outcomes.map((o) => o.state).sort()).toEqual(["done", "failed", "no_response"]);
  });
});

// ── POST 재시도(2026-08-05 실사고 처방) ─────────────────────────────
// ★사고: prod가 간헐 502를 내는 창에 갱신 버튼을 누르면 request-refresh POST가 **유실**됐다.
// 폴링·데몬은 멀쩡했으므로 화면상으론 "버튼이 안 눌린다"로만 보였다. 502는 대개 수초짜리라
// 한 번 더 던지면 붙는다 — 아래 규칙이 지워지면 그 사고가 그대로 재현된다.
describe("withRequestRetry", () => {
  const sleep = async () => {}; // 백오프는 즉시 통과(대기 시간 자체는 검증 대상이 아니다)

  it("첫 시도가 성공하면 재시도하지 않는다", async () => {
    let calls = 0;
    const r = await withRequestRetry(async () => { calls += 1; return "ok"; }, { sleep });
    expect(r).toBe("ok");
    expect(calls).toBe(1);
  });

  it("일시적 실패는 재시도로 살아난다(502 창을 건너뛴다)", async () => {
    let calls = 0;
    const r = await withRequestRetry(
      async () => {
        calls += 1;
        if (calls < 3) throw new Error("502 Bad Gateway");
        return "ok";
      },
      { sleep },
    );
    expect(r).toBe("ok");
    expect(calls).toBe(3);
  });

  it("전패하면 마지막 오류를 그대로 올린다(삼키지 않는다)", async () => {
    let calls = 0;
    await expect(
      withRequestRetry(async () => { calls += 1; throw new Error(`실패${calls}`); }, { sleep }),
    ).rejects.toThrow("실패4");
    expect(calls).toBe(1 + REQUEST_RETRY_DELAYS_MS.length); // 첫 시도 + 재시도 3회
  });

  it("재시도 전 onRetry로 알린다 — 백오프 동안 화면이 조용하면 '멈춘 것'으로 보인다", async () => {
    const seen: number[] = [];
    await withRequestRetry(
      async () => { if (seen.length < 2) throw new Error("일시적"); return 1; },
      { sleep, onRetry: (retry) => seen.push(retry) },
    );
    expect(seen).toEqual([1, 2]);
  });
});

describe("전체 갱신 대상(ALL_REFRESH_SPECS)", () => {
  it("수동 수집 6큐를 빠짐없이 덮는다", () => {
    expect(ALL_REFRESH_SPECS).toHaveLength(6);
    expect(ALL_REFRESH_SPECS.map((s) => s.key).sort()).toEqual(
      ["ofix_ad", "ofix_sales", "ohitech_ad", "rg_wing1", "rg_wing2", "supplier_hub"],
    );
  });

  it("두 레지스트리의 사본이 아니라 합집합이다(스트림 추가 시 자동 반영)", () => {
    // 사본이면 원본이 늘어도 여기가 안 늘어 '전체 갱신'이 거짓말이 된다.
    expect(ALL_REFRESH_SPECS).toHaveLength(STREAM_SPECS.length + RG_STREAM_SPECS.length);
    for (const s of [...STREAM_SPECS, ...RG_STREAM_SPECS]) {
      expect(ALL_REFRESH_SPECS).toContain(s);
    }
  });
});

describe("진행 단계 통지(onPhase)", () => {
  it("요청 → 전달됨 → Mac 수령 순으로 흐른다", async () => {
    const clock = fakeClock();
    const { spec } = mkSpec([
      { requested: false, last_success_at: null, last_error_at: null, last_error: null },
      { requested: true, last_success_at: null, last_error_at: null, last_error: null, attempt_count: 1 },
      { requested: false, last_success_at: "2026-08-05T21:00:00", last_error_at: null, last_error: null },
    ]);
    const phases: string[] = [];
    const out = await runStreamRefresh(spec, {
      ...clock,
      onPhase: (_s, p) => phases.push(p.kind),
    });
    expect(out.state).toBe("done");
    expect(phases).toEqual(["requesting", "requested", "fetching"]);
  });

  it("attempt_count가 없는 표면에서는 fetching을 지어내지 않는다", async () => {
    const clock = fakeClock();
    const { spec } = mkSpec([
      { requested: false, last_success_at: null, last_error_at: null, last_error: null },
      { requested: true, last_success_at: null, last_error_at: null, last_error: null },
      { requested: false, last_success_at: "2026-08-05T21:00:00", last_error_at: null, last_error: null },
    ]);
    const phases: string[] = [];
    await runStreamRefresh(spec, { ...clock, onPhase: (_s, p) => phases.push(p.kind) });
    expect(phases).not.toContain("fetching");
  });
});
