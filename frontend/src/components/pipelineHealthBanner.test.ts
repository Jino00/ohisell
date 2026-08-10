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

  // ═══ 디스크 여유 (2026-08-10 Jino 승인 후 추가) ═══
  //
  // ★★이 분기는 **없었다.** 백엔드는 `disk_low`로 healthy=false를 만드는데 배너 빌더에
  //   분기가 없어 parts가 비고 → null → **배너가 통째로 숨었다.**
  //   2026-08-10 prod 실측이 정확히 그 상태였다: 사용률 93.8%(여유 5.9GB)로 unhealthy인데
  //   화면은 조용. 2026-08-03 ENOSPC 사고(서버 3시간 40분 마비·자동수집 12개 유실)를 막으려고
  //   만든 «유일한 사전 신호»가 화면까지 이어지지 않은 채 있었다.

  const disk = (over: Record<string, unknown> = {}) => ({
    path: "/home/ubuntu/ohisell/backend",
    state: "low",
    used_percent: 93.83,
    warn_percent: 85,
    free_bytes: 6387777536,
    total_bytes: 103865303040,
    impact: "디스크 포화 시 전 수집 잡이 조용히 멈춘다(2026-08-03 ENOSPC 사고)",
    ...over,
  });

  it("★disk_low만 있어도 배너가 뜬다 — 종전엔 통째로 숨었다", () => {
    const banner = buildPipelineHealthBanner(makeHealth({ disk_low: [disk()] }));
    expect(banner).not.toBeNull();
    expect(banner!.summary).toContain("디스크 여유 부족 93.8%");
    // ★백엔드 impact 라벨을 그대로 노출한다 — 판정도 문구도 백엔드가 정본이다.
    expect(banner!.summary).toContain("전 수집 잡이 조용히 멈춘다");
  });

  it("disk_low가 비었으면 침묵", () => {
    expect(buildPipelineHealthBanner(makeHealth({ disk_low: [] }))).toBeNull();
    expect(buildPipelineHealthBanner(makeHealth({ disk_low: undefined }))).toBeNull();
  });

  it("다른 문제와 함께 있으면 둘 다 나온다(드리프트가 잡 실패를 가리지 않는다)", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({ cost_drift: drift({ count: 5 }), missing_jobs: ["auto_sync_orders"] }),
    );
    expect(banner!.summary).toContain("원가가 정본과 다름 5건");
    expect(banner!.summary).toContain("auto_sync_orders");
  });

  // ── 판매분석 보존식 (D-CPP-35) ────────────────────────────────────────
  // ★백엔드 판정과 **같은 커밋에** 이 분기가 있어야 한다. disk_low가 판정에만 있고 표시가
  //   없어 배너가 통째로 숨었던 것이 바로 이 실패다(교훈 #223).
  const conservation = (
    over: Partial<NonNullable<SchedulerHealth["vendor_item_conservation"]>> = {},
  ) => ({
    window: { start: "2026-07-27", end: "2026-08-10" },
    compared: 9,
    mismatch: [],
    summary_only: [],
    option_only: [],
    ...over,
  });

  it("보존식 불일치가 배너에 뜬다 — 건수 + 최대 차액의 날짜/유형", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({
        vendor_item_conservation: conservation({
          mismatch: [
            {
              account_key: "COUPANG_WING2", date: "2026-08-05",
              registration_type: "NORMAL", option_gmv: 100000,
              summary_gmv: 188800, diff: -88800,
            },
            {
              account_key: "COUPANG_WING2", date: "2026-08-07",
              registration_type: "NORMAL", option_gmv: 86500,
              summary_gmv: 86501, diff: -1,
            },
          ],
        }),
      }),
    );
    expect(banner).not.toBeNull();
    expect(banner!.summary).toContain("판매분석 옵션↔요약 합 불일치 2건");
    // ★차액이 가장 큰 건을 고른다 — 첫 건을 쓰면 1원 차이가 대표로 나가 심각도가 가려진다.
    expect(banner!.summary).toContain("2026-08-05");
    expect(banner!.summary).toContain("-88,800원");
    expect(banner!.summary).not.toContain("2026-08-07");
  });

  it("★summary_only만 있으면 침묵 — «아직 안 옴»은 «틀렸다»가 아니다", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({
        vendor_item_conservation: conservation({
          summary_only: [
            {
              account_key: "COUPANG_WING2", date: "2026-08-09",
              registration_type: "NORMAL", summary_gmv: 34300,
            },
          ],
        }),
      }),
    );
    expect(banner).toBeNull();
  });

  it("보존식이 null/undefined면 침묵 — 구백엔드에서도 배너가 깨지지 않는다", () => {
    expect(
      buildPipelineHealthBanner(makeHealth({ vendor_item_conservation: null })),
    ).toBeNull();
    expect(
      buildPipelineHealthBanner(makeHealth({ vendor_item_conservation: undefined })),
    ).toBeNull();
  });

  it("보존식 불일치가 잡 실패를 가리지 않는다", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({
        vendor_item_conservation: conservation({
          mismatch: [{
            account_key: "COUPANG_WING2", date: "2026-08-05",
            registration_type: "RFM", option_gmv: 0, summary_gmv: 1, diff: -1,
          }],
        }),
        missing_jobs: ["request_wing_vendor_summary_daily"],
      }),
    );
    expect(banner!.summary).toContain("판매분석 옵션↔요약 합 불일치 1건");
    expect(banner!.summary).toContain("request_wing_vendor_summary_daily");
  });

  it("판매분석 신선도 impact가 그대로 배너에 나온다(data_stale 경로 재사용)", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({
        data_stale: [{
          name: "wing_vendor_item_sales", account_key: "COUPANG_WING2",
          state: "stale", age_days: 15.2, max_age_days: 3,
          impact: "쿠팡 판매분석 옵션축(오하이테크) 정체 — 옵션별 3P 매출이 낡은 값으로 구른다",
        }],
      }),
    );
    expect(banner!.summary).toContain("옵션별 3P 매출이 낡은 값으로 구른다");
    expect(banner!.summary).toContain("(15일째)");
  });
});
