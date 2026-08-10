// pipelineHealthBanner.test.ts — 전역 헬스 배너 요약 빌더 가드(PLAN §2a).
// ★존재 이유: 배너가 없어 쿠키 만료(cookies_stale)가 26일 방치됐던 사고의 재발 방지.
//   빌더가 ①COUPANG_ADS1을 제외(광고비 배너 전담)하고 제외 후 0개면 숨기며
//   ②disabled(정상)를 문제로 세지 않고 ③scheduler 정지를 최우선 표기하는지 고정한다.
import { describe, it, expect } from "vitest";
import { buildPipelineHealthBanner } from "./Layout";
import type { SchedulerHealth } from "../lib/api";

function makeHealth(overrides: Partial<SchedulerHealth>): SchedulerHealth {
  return {
    healthy: false,
    scheduler_running: true,
    missing_jobs: [],
    failed: [],
    stale: [],
    never_succeeded: [],
    disabled: [],
    cookies_stale: [],
    data_stale: [],
    as_of: "2026-07-17T00:00:00+09:00",
    ...overrides,
  };
}

describe("buildPipelineHealthBanner", () => {
  it("healthy:true면 null(배너 숨김)", () => {
    expect(buildPipelineHealthBanner(makeHealth({ healthy: true }))).toBeNull();
  });

  it("COUPANG_ADS1 쿠키만 stale이면 제외되어 null(광고비 배너 전담)", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({
        cookies_stale: [
          { account_key: "COUPANG_ADS1", state: "stale", age_days: 3, status: "red" },
        ],
      }),
    );
    expect(banner).toBeNull();
  });

  it("disabled 버킷만 있으면 정상이므로 null", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({ disabled: [{ job_name: "some_job", state: "disabled" }] }),
    );
    expect(banner).toBeNull();
  });

  it("WING1 쿠키 stale → 한글 라벨(오픽스) + N일째, 광고비 배너와 별개", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({
        cookies_stale: [
          { account_key: "COUPANG_WING1", state: "stale", age_days: 26.3, status: "red" },
        ],
      }),
    );
    expect(banner).not.toBeNull();
    expect(banner!.summary).toContain("오픽스");
    expect(banner!.summary).toContain("26일째"); // Math.floor
  });

  it("scheduler_running:false는 최우선 표기(맨 앞)", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({
        scheduler_running: false,
        failed: [{ job_name: "naver_forecast", state: "failed" }],
      }),
    );
    expect(banner!.summary.startsWith("스케줄러 정지")).toBe(true);
  });

  it("여러 문제(data_stale impact + 잡 실패)를 ' · '로 잇고 detail은 줄바꿈", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({
        data_stale: [
          {
            name: "rg_settlement_account_rows",
            account_key: "COUPANG_WING1",
            state: "stale",
            age_days: 12.9,
            impact: "RG 정산 데이터 미갱신",
          },
        ],
        failed: [{ job_name: "naver_forecast", state: "failed" }],
      }),
    );
    expect(banner!.summary).toContain("RG 정산 데이터 미갱신 (12일째)");
    expect(banner!.summary).toContain("잡 실패: naver_forecast");
    expect(banner!.summary).toContain(" · ");
    expect(banner!.detail).toContain("\n");
  });

  it("no_data(age_days=null)면 '일째' 없이 impact만", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({
        data_stale: [
          {
            name: "x",
            account_key: "COUPANG_WING2",
            state: "no_data",
            age_days: null,
            impact: "RG 정산 데이터 없음",
          },
        ],
      }),
    );
    expect(banner!.summary).toBe("RG 정산 데이터 없음");
  });

  // ═══ 원가 정본 드리프트 (2026-08-10 배선, ref 54 §7-6) ═══
  //
  // ★왜 여기까지 테스트하나: 이 배너가 **유일한 상시 표면**이다. 원가 버퍼는 에러를 안 내고
  //   화면을 비우지도 않는다 — 배너 문구가 사라지면 감지 수단이 통째로 없어진다.
  //   종전 상태가 정확히 그거였다(«검사기는 있는데 아무도 안 부른다», 177건 방치).

  const drift = (over: Partial<NonNullable<SchedulerHealth["cost_drift"]>> = {}) => ({
    count: 177,
    by_buffer: { 폰: 141, "도어락·플립": 18, 폴드: 18 },
    sample: [
      { internal_sku: "OHI-0688", product_name: "지문방지 필름", cost_price: 2616, truth: 2350.7 },
    ],
    ok: 313,
    undetermined: 459,
    source: "MD_원가 계산_Jino_260807.xlsx (sha 7ed336b4c55ea71b)",
    ...over,
  });

  it("드리프트가 있으면 건수 + 버퍼 계열 + 원인 단서를 낸다", () => {
    const banner = buildPipelineHealthBanner(makeHealth({ cost_drift: drift() }));
    expect(banner!.summary).toContain("원가가 정본과 다름 177건");
    // ★어느 계열이 되돌아왔는지가 원인 추정의 첫 단서다 — 건수만 있으면 어디를 볼지 모른다.
    expect(banner!.summary).toContain("폰 141건");
    // ★사람이 다음에 할 일을 적는다. 「드리프트」만 쓰면 무슨 조치를 해야 할지 알 수 없다.
    expect(banner!.summary).toContain("옛 매핑 엑셀");
  });

  it("★count=0이면 아무 말도 안 한다 — 0건을 경고로 띄우면 배너가 상시 켜져 무시된다", () => {
    expect(
      buildPipelineHealthBanner(makeHealth({ cost_drift: drift({ count: 0, by_buffer: {} }) })),
    ).toBeNull();
  });

  it("★cost_drift가 null/undefined면 침묵 — 구백엔드에서도 배너가 깨지지 않는다", () => {
    expect(buildPipelineHealthBanner(makeHealth({ cost_drift: null }))).toBeNull();
    expect(buildPipelineHealthBanner(makeHealth({ cost_drift: undefined }))).toBeNull();
  });

  it("★«판정 불가 459건»을 배너에 섞지 않는다 — 한 줄에서 드리프트가 묻힌다", () => {
    const banner = buildPipelineHealthBanner(makeHealth({ cost_drift: drift() }));
    expect(banner!.summary).not.toContain("459");
    expect(banner!.summary).not.toContain("313");
  });

  it("다른 문제와 함께 있으면 둘 다 나온다(드리프트가 잡 실패를 가리지 않는다)", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({ cost_drift: drift({ count: 5 }), missing_jobs: ["auto_sync_orders"] }),
    );
    expect(banner!.summary).toContain("원가가 정본과 다름 5건");
    expect(banner!.summary).toContain("auto_sync_orders");
  });
});
