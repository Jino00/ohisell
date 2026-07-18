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
});
