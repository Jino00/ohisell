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
    by_cause: { purchased_approved: 141, g3_residual: 36 },
    sample: [
      {
        internal_sku: "OHI-0688",
        product_name: "지문방지 필름",
        cost_price: "2616.00",
        truth: "2350.70",
      },
    ],
    gap_sum: "46958.10",
    gap_sum_signed: "-46958.10",
    cause_labels: { purchased_approved: "매입가 정본", g3_residual: "G3-3·4·5" },
    with_truth: 504,
    no_truth: 459,
    held: 11,
    source: "SKU별 정본 판별표 — 원가표(cost_table_item) + 매입가 원장(cost_purchased_price)",
    ...over,
  });

  it("드리프트가 있으면 건수 + 사유 + 금액 + 할 일을 낸다", () => {
    const banner = buildPipelineHealthBanner(makeHealth({ cost_drift: drift() }));
    expect(banner!.summary).toContain("원가가 정본과 다름 177건");
    // ★어느 사유로 어긋났는지가 첫 단서다 — 건수만 있으면 어디를 볼지 모른다.
    // ★사유 코드가 아니라 **사람 말**이 나온다(적대 리뷰 P2-7)
    expect(banner!.summary).toContain("매입가 정본 141건");
    // ★사람이 다음에 할 일을 적는다. 「드리프트」만 쓰면 무슨 조치를 해야 할지 알 수 없다.
    expect(banner!.summary).toContain("컷오버 필요");
    // ★금액은 `/cost` 화면과 **같은 포맷터**를 쓴다 — 한쪽만 원문자열이면 같은 수가
    //   두 화면에서 다르게 보인다(적대 리뷰 P2-3). 이 단언이 그 결합을 지킨다.
    expect(banner!.summary).toContain("46,958.1원");
  });

  it("★판정기가 꺼졌으면 「어긋남 0건이 아니다」라고 말한다 (적대 리뷰 1R P1-1)", () => {
    // 판정 근거를 옮기면서 「검사기가 꺼졌다」 신호가 한 번 끊겼고, 그동안 판별표 고장과
    // 깨끗한 상태가 응답에서 구분되지 않았다. 이 분기가 그 구분을 화면까지 가져온다.
    const banner = buildPipelineHealthBanner(
      makeHealth({ cost_board_guard: { active: false, reason: "정본 판별표 조회 실패: RuntimeError" } }),
    );
    expect(banner!.summary).toContain("원가 정본 판정 미작동");
    expect(banner!.summary).toContain("「어긋남 0건」이 아니다");
  });

  it("★원인을 «추측»하지 않는다 — 「옛 매핑 엑셀 업로드 의심」을 말하지 않는다", () => {
    // ★★2026-09-03: 그 문장은 판정 근거가 08-07판 엑셀 스냅샷 하나였을 때만 성립했고,
    //   실제로 **최신 원가표대로 올린 값을 「옛 값 복귀」로 신고**했다(오탐 7건).
    //   이제 배너는 관측한 것만 말한다 — 원인은 `/cost` 「정본 판별」에서 사유별로 본다.
    const banner = buildPipelineHealthBanner(makeHealth({ cost_drift: drift() }));
    expect(banner!.summary).not.toContain("옛 매핑 엑셀");
  });

  it("★count=0이면 아무 말도 안 한다 — 0건을 경고로 띄우면 배너가 상시 켜져 무시된다", () => {
    expect(
      buildPipelineHealthBanner(makeHealth({ cost_drift: drift({ count: 0, by_cause: {} }) })),
    ).toBeNull();
  });

  it("★cost_drift가 null/undefined면 침묵 — 구백엔드에서도 배너가 깨지지 않는다", () => {
    expect(buildPipelineHealthBanner(makeHealth({ cost_drift: null }))).toBeNull();
    expect(buildPipelineHealthBanner(makeHealth({ cost_drift: undefined }))).toBeNull();
  });

  it("★«정본 없음 459건»을 배너에 섞지 않는다 — 한 줄에서 드리프트가 묻힌다", () => {
    const banner = buildPipelineHealthBanner(makeHealth({ cost_drift: drift() }));
    expect(banner!.summary).not.toContain("459");
    expect(banner!.summary).not.toContain("504");
  });

  // ═══ 원가 «가드»가 꺼졌다 (계약 D-CPP-64 §4 S1-③, 2026-08-31 배선) ═══
  //
  // ★★위 드리프트와 짝이지 중복이 아니다: 위는 «어긋남을 찾았다», 이건 «찾을 수가 없었다».
  //   업로드 경로의 가드는 정본 스냅샷을 못 찾으면 조용히 통과하고(fail-open), 그때
  //   `cost_drift`도 null이 되어 **「어긋남 0건」과 화면에서 똑같이 생긴다.** 이 분기가 없으면
  //   감시가 꺼진 채로 배너가 침묵한다 — `disk_low`가 판정에만 있고 표시가 없어 통째로 숨었던
  //   2026-08-10 사고(아래 절)의 거울상이다.

  it("★가드가 꺼져 있으면 「원가 가드 미작동 — 스냅샷 부재」를 말한다", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({
        cost_guard: {
          active: false,
          reason: "정본 스냅샷을 못 읽는다",
          snapshot_path: "/app/data/cost_truth_20260807.json",
        },
      }),
    );
    expect(banner!.summary).toContain("원가 가드 미작동");
    expect(banner!.summary).toContain("스냅샷 부재");
    // ★왜 꺼졌는지도 함께 — 「미작동」만 보면 무엇을 고쳐야 할지 모른다.
    expect(banner!.summary).toContain("정본 스냅샷을 못 읽는다");
  });

  it("★가드가 켜져 있으면 아무 말도 안 한다 — 상시 켜진 경고는 안 켜진 것과 같다", () => {
    expect(
      buildPipelineHealthBanner(
        makeHealth({ cost_guard: { active: true, reason: null, snapshot_path: "/x.json" } }),
      ),
    ).toBeNull();
  });

  it("★cost_guard가 null/undefined면 침묵 — 구백엔드에서도 배너가 깨지지 않는다", () => {
    expect(buildPipelineHealthBanner(makeHealth({ cost_guard: null }))).toBeNull();
    expect(buildPipelineHealthBanner(makeHealth({ cost_guard: undefined }))).toBeNull();
  });

  it("★가드 꺼짐은 «돈이 새는» 등급이라 «멈춤» 항목보다 앞에 선다(잘려 나가지 않는다)", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({
        disk_low: [
          {
            path: "/",
            state: "low",
            used_percent: 93.8,
            warn_percent: 90,
            free_bytes: 5_900_000_000,
            total_bytes: 95_000_000_000,
            impact: "수집 중단 위험",
          },
        ],
        cost_guard: { active: false, reason: null, snapshot_path: null },
      }),
    );
    // 등급 0(돈)이 등급 1(멈춤)보다 앞이다 — 뒤에 숨으면 한 줄 배너에서 잘린다.
    expect(banner!.items[0]).toContain("원가 가드 미작동");
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

  // ── 판매분석 보존식 (D-CPP-36) ────────────────────────────────────────
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

  // ── 검색어 제외 조치 생존 (D-NAO-173 P1-①) ─────────────────────────
  // ★이 블록이 없어서 적대 리뷰가 배너 분기를 **통째로 지워도** 311/311이 통과했다(P1-1).
  //   cost_drift·disk_low·보존식 세 분기는 모두 백엔드 판정과 같은 커밋에 배너 테스트가
  //   들어왔는데 이번만 빠졌다 — 그 구멍이 곧 «판정은 하는데 화면은 조용한» 상태다.
  const survival = (
    over: Partial<NonNullable<SchedulerHealth["exclusion_survival"]>> = {},
  ) => ({
    monitored: 1,
    alive: 0,
    breached: [],
    breached_total: 0,
    never_checked: 0,
    never_checked_due: 0,
    last_checked_at: "2026-08-11T08:25:00+09:00",
    stale_hours: 30,
    stale: false,
    healthy: false,
    revert_howto: "네이버 검색광고 콘솔에서 삭제하면 복구된다",
    impact: "우리가 건 제외가 사라지면 그 검색어에 광고비가 다시 흐른다",
    as_of: "2026-08-11T12:00:00+09:00",
    ...over,
  });

  const breachRow = (over = {}) => ({
    campaign_id: "cmp-1", adgroup_id: "grp-1", search_term: "아이패드종이필름",
    live_state: "missing", live_note: "라이브 목록에 없다", excluded_at: "2026-07-22T09:00:00",
    cost_at_exclusion: 22854, ...over,
  });

  it("제외가 사라지면 배너에 검색어까지 나온다", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({ exclusion_survival: survival({ breached: [breachRow()], breached_total: 1 }) }),
    );
    expect(banner).not.toBeNull();
    expect(banner!.summary).toContain("어긋남");
    expect(banner!.summary).toContain("아이패드종이필름");
    expect(banner!.summary).toContain("사라짐");
  });

  it("★조회 실패(unknown)를 «사라짐»이라 말하지 않는다 — 대응이 달라진다", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({
        exclusion_survival: survival({
          breached: [breachRow({ live_state: "unknown", live_note: "라이브 조회 실패" })],
          breached_total: 1,
        }),
      }),
    );
    expect(banner!.summary).toContain("확인하지 못함");
    expect(banner!.summary).not.toContain("어긋남");
  });

  it("★«아직 안 돌았다»와 «멈췄다»를 구분한다", () => {
    const never = buildPipelineHealthBanner(
      makeHealth({
        exclusion_survival: survival({ never_checked: 1, never_checked_due: 1, last_checked_at: null }),
      }),
    );
    expect(never!.summary).toContain("아직 한 번도 대조되지 않음");

    const stopped = buildPipelineHealthBanner(
      makeHealth({
        exclusion_survival: survival({ stale: true, last_checked_at: "2026-08-05T08:25:00+09:00" }),
      }),
    );
    expect(stopped!.summary).toContain("대조가 멈춤");
    expect(stopped!.summary).toContain("2026-08-05");
  });

  it("생존 감시가 정상이면 이 분기는 침묵(다른 문제만 나온다)", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({
        exclusion_survival: survival({ healthy: true, alive: 1 }),
        missing_jobs: ["auto_sync_orders"],
      }),
    );
    expect(banner!.summary).toBe("잡 실패: auto_sync_orders");
  });

  it("구버전 백엔드(키 없음)에서도 다른 판정을 가리지 않는다", () => {
    expect(buildPipelineHealthBanner(makeHealth({ exclusion_survival: undefined }))).toBeNull();
    expect(buildPipelineHealthBanner(makeHealth({ exclusion_survival: null }))).toBeNull();
  });
});

describe("제외 슬롯 소진 (S6-a, ref 66 §5-2)", () => {
  // ★이 블록이 없으면 배너 분기를 통째로 지워도 전건 통과한다 — 바로 위 생존 감시 블록이
  //   그 구멍으로 P1을 맞았다. 「백엔드는 세는데 화면이 안 읽음」이 이 저장소에서 3회 반복됐다.
  const slots = (
    over: Partial<NonNullable<SchedulerHealth["exclusion_slots"]>> = {},
  ) => ({
    cap: 70,
    groups: 3,
    exhausted: 0,
    unknown: 0,
    stale: 0,
    healthy: false,
    rows: [],
    rows_truncated: 0,
    ...over,
  });

  const row = (over: Record<string, unknown> = {}) => ({
    adgroup_id: "grp-1", campaign_id: "cmp-1", campaign_name: "01. 갤럭시_지문방지_TPU",
    name: "01. TPU",
    state: "exhausted", used: 70, cap: 70, remaining: 0, usage_pct: 100,
    ours: 2, agency: 60, other_source: 0, unattributed: 8,
    exhaust_eta_days: 0, exhaust_eta_reason: "이미 70/70 — 남은 칸이 없다",
    ...over,
  });

  it("칸이 꽉 차면 «브레이크가 없다»고 말한다", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({ exclusion_slots: slots({ exhausted: 1, rows: [row()] }) }),
    );
    expect(banner!.summary).toContain("제외 슬롯이 꽉 찬 광고그룹 1개");
    expect(banner!.summary).toContain("더 걸 브레이크가 없음");
    expect(banner!.summary).toContain("01. TPU");
  });

  it("★«못 셌다»를 «소진»으로 읽히게 하지 않는다", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({ exclusion_slots: slots({ unknown: 2, rows: [row({ state: "unknown", used: null })] }) }),
    );
    expect(banner!.summary).toContain("확인하지 못한");
    expect(banner!.summary).toContain("«0칸»이 아니라 «모름»");
    expect(banner!.summary).not.toContain("꽉 찬");
  });

  it("관측이 멈춘 것도 조용하지 않다", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({ exclusion_slots: slots({ stale: 1 }) }),
    );
    expect(banner!.summary).toContain("관측이 멈춘");
  });

  it("정상이면 이 분기는 침묵(다른 문제만 나온다)", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({
        exclusion_slots: slots({ healthy: true }),
        missing_jobs: ["auto_sync_orders"],
      }),
    );
    expect(banner!.summary).toBe("잡 실패: auto_sync_orders");
  });

  it("구버전 백엔드(키 없음)에서도 다른 판정을 가리지 않는다", () => {
    expect(buildPipelineHealthBanner(makeHealth({ exclusion_slots: undefined }))).toBeNull();
    expect(buildPipelineHealthBanner(makeHealth({ exclusion_slots: null }))).toBeNull();
  });
});

describe("광고비 괴리 (D-CPP-46)", () => {
  // ★이 분기가 없으면 백엔드가 healthy=false를 만드는데 화면은 조용하다 — disk_low가
  //   정확히 그 상태로 있었다(교훈 #223). 그래서 판정과 «같은 커밋»에 표시를 넣고,
  //   그게 실제로 문장이 되는지를 여기서 못 박는다.
  const div = (
    over: Partial<NonNullable<SchedulerHealth["ad_cost_divergence"]>> = {},
  ): NonNullable<SchedulerHealth["ad_cost_divergence"]> => ({
    window: { start: "2026-05-15", end: "2026-08-09" },
    pa_spend: 16_565_714,
    nonpa_spend: 1_648_923,
    deducted: 18_214_637,
    settled: 17_160_142,
    ratio: 0.9421,
    max_ratio: 1.1,
    account_key: "COUPANG_WING1",
    verdict: "ok",
    ...over,
  });

  it("★파이프 정지 — 우리가 하나도 안 뺐는데 쿠팡은 뗐다", () => {
    const b = buildPipelineHealthBanner(
      makeHealth({
        ad_cost_divergence: div({
          verdict: "pipe_stopped", pa_spend: 0, nonpa_spend: 0, deducted: 0, ratio: null,
        }),
      }),
    );
    expect(b).not.toBeNull();
    expect(b!.summary).toContain("광고비 수집이 비었는데");
    expect(b!.summary).toContain("17,160,142");
    // «왜 문제인가»를 말해야 운영자가 손을 댄다.
    expect(b!.summary).toContain("과대계상");
  });

  it("★괴리 — 배율과 금액을 같이 준다(배율만으론 규모를, 금액만으론 임계를 모른다)", () => {
    const b = buildPipelineHealthBanner(
      makeHealth({
        ad_cost_divergence: div({ verdict: "diverged", ratio: 1.35, settled: 24_589_760 }),
      }),
    );
    expect(b).not.toBeNull();
    expect(b!.summary).toContain("1.350배");
    expect(b!.summary).toContain("24,589,760");
    expect(b!.summary).toContain("18,214,637");
    // 창을 밝힌다 — 창 없는 비율은 거짓말이 된다(교훈 #263).
    expect(b!.summary).toContain("2026-05-15~2026-08-09");
  });

  it("★verdict가 ok면 침묵 — 정상인데 배너에 숫자를 흘리면 배너가 무뎌진다", () => {
    expect(buildPipelineHealthBanner(makeHealth({ ad_cost_divergence: div() }))).toBeNull();
  });

  it("★★insufficient_data는 «어긋남»이 아니다 — 뭉치면 «못 쟀다»가 «틀렸다»로 보인다", () => {
    expect(
      buildPipelineHealthBanner(
        makeHealth({
          ad_cost_divergence: div({ verdict: "insufficient_data", ratio: null, settled: 0 }),
        }),
      ),
    ).toBeNull();
  });

  it("★null/undefined면 침묵 — 구백엔드에서도 배너가 깨지지 않는다", () => {
    expect(buildPipelineHealthBanner(makeHealth({ ad_cost_divergence: null }))).toBeNull();
    expect(buildPipelineHealthBanner(makeHealth({}))).toBeNull();
  });
});

// ── 부분수집 (D-NAO-204) ────────────────────────────────────────────────────
// ★존재 이유: 2026-08-18에 주문 23건·상품매출 356,100원이 사라졌는데 그날 sync_log 네 회차가
//   전부 `success`였다. 수집 자체는 D-NAO-202에서 고쳤지만 그 «부분수집» 표식이 로그와
//   sync_log에만 있고 화면 어디에도 안 나왔다 — disk_low가 판정에만 있고 표시가 없어 배너가
//   통째로 숨었던 것과 같은 실패(교훈 #223). 이 블록이 그 분기를 고정한다.
describe("buildPipelineHealthBanner — partial_sync", () => {
  const partial = (over: Partial<NonNullable<SchedulerHealth["partial_sync"]>[number]> = {}) => ({
    sync_log_id: 4326,
    channel_id: 6,
    channel_name: "네이버 스마트스토어",
    at: "2026-08-19T10:25:12+09:00",
    records_synced: 336,
    detail: "[부분수집] 변경상태 스윕 미완주 1일: 2026-08-18",
    ...over,
  });

  it("부분수집이 있으면 배너에 뜬다 (백엔드만 세고 화면이 숨는 것을 막는다)", () => {
    const banner = buildPipelineHealthBanner(makeHealth({ partial_sync: [partial()] }));
    expect(banner).not.toBeNull();
    expect(banner!.summary).toContain("네이버 스마트스토어");
    expect(banner!.summary).toContain("덜 수집됨");
    // 백엔드 원문(어느 날인지)이 살아 있어야 재수집 대상을 고를 수 있다.
    expect(banner!.summary).toContain("2026-08-18");
    // 「성공인데 덜 들어옴」이라는 것이 말로 드러나야 한다.
    expect(banner!.summary).toContain("과소계상");
  });

  it("여러 채널이면 채널명을 잃지 않는다 (접어서 묻으면 어디를 볼지 모른다)", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({
        partial_sync: [
          partial(),
          partial({ sync_log_id: 4327, channel_id: 7, channel_name: "자사몰" }),
        ],
      }),
    );
    expect(banner!.summary).toContain("네이버 스마트스토어");
    expect(banner!.summary).toContain("자사몰");
    expect(banner!.summary).toContain("2건");
  });

  it("같은 채널이 여러 번이면 이름은 한 번만 쓴다", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({ partial_sync: [partial(), partial({ sync_log_id: 4327 })] }),
    );
    expect(banner!.summary.match(/네이버 스마트스토어/g)).toHaveLength(1);
  });

  it("맨 앞은 «최신»이다 — 백엔드가 started_at 내림차순으로 준다", () => {
    const banner = buildPipelineHealthBanner(
      makeHealth({
        partial_sync: [
          partial({ detail: "[부분수집] 최신건" }),
          partial({ sync_log_id: 4327, detail: "[부분수집] 오래된건" }),
        ],
      }),
    );
    expect(banner!.summary).toContain("최신건");
    expect(banner!.summary).not.toContain("오래된건");
  });

  it("빈 배열·null·미지원 백엔드에서는 아무 말도 안 한다", () => {
    expect(buildPipelineHealthBanner(makeHealth({ partial_sync: [] }))).toBeNull();
    expect(buildPipelineHealthBanner(makeHealth({ partial_sync: null }))).toBeNull();
    expect(buildPipelineHealthBanner(makeHealth({}))).toBeNull();
  });
});

// ── 우선순위 정렬 + items (D-NAO-205) ────────────────────────────────────────
// ★존재 이유: 종전엔 한 줄 truncate라 경고가 많으면 뒤 항목이 화면에서 통째로 사라졌다.
//   2026-08-19 실측 — 11건 중 매출에 닿는 「주문이 덜 수집됨」이 잘려 안 보였다.
//   이제 접기/펼치기가 있지만 **접힌 상태에서 무엇이 보이느냐**는 이 순서가 정한다.
describe("buildPipelineHealthBanner — 우선순위와 items", () => {
  const partial = {
    sync_log_id: 1, channel_id: 6, channel_name: "네이버 스마트스토어",
    at: "2026-08-19T10:25:12+09:00", records_synced: 336,
    detail: "[부분수집] 변경상태 스윕 미완주 1일: 2026-08-18",
  };
  const disk = {
    path: "/", state: "low", used_percent: 95.5, warn_percent: 90,
    free_bytes: 3_300_000_000, total_bytes: 73_000_000_000,
    impact: "디스크 포화 시 전 수집 잡이 조용히 멈춘다",
  };

  it("items 배열을 돌려준다 (화면이 줄 단위로 그릴 수 있어야 한다)", () => {
    const b = buildPipelineHealthBanner(makeHealth({ partial_sync: [partial], disk_low: [disk] }));
    expect(Array.isArray(b!.items)).toBe(true);
    expect(b!.items).toHaveLength(2);
    // summary/detail은 items에서 파생된다 — 두 벌이 갈라지면 접힘/펼침이 다른 말을 한다.
    expect(b!.summary).toBe(b!.items.join(" · "));
    expect(b!.detail).toBe(b!.items.join("\n"));
  });

  it("★매출에 직접 닿는 신호가 앞에 온다 — 접히면 이게 유일하게 보이는 줄이다", () => {
    // 발견 순서상 disk_low(5번)가 partial_sync(9번)보다 먼저 push된다. 정렬이 없으면 disk가 앞.
    const b = buildPipelineHealthBanner(makeHealth({ partial_sync: [partial], disk_low: [disk] }));
    expect(b!.items[0]).toContain("덜 수집됨");
    expect(b!.items[1]).toContain("디스크");
  });

  it("같은 등급 안에서는 발견 순서를 지킨다 (안정 정렬)", () => {
    const b = buildPipelineHealthBanner(
      makeHealth({
        disk_low: [disk],
        failed: [{ job_name: "sync_x", state: "error" }],
      }),
    );
    // 둘 다 등급 1 — 발견 순서(disk 5번 < 잡 10번)가 유지돼야 한다.
    expect(b!.items[0]).toContain("디스크");
    expect(b!.items[1]).toContain("잡 실패");
  });

  it("경고가 1건이면 items도 1건 — 화면이 토글을 안 띄우는 근거다", () => {
    const b = buildPipelineHealthBanner(makeHealth({ partial_sync: [partial] }));
    expect(b!.items).toHaveLength(1);
  });
});


// ── ★등급은 push 지점에서 붙는다 — 산문 재파싱 금지 (적대 리뷰 P1, 2026-08-19) ──
// 초판은 완성된 문자열을 정규식으로 되읽어 등급을 매겼다. WING1/WING2 쿠키만 문구가
// `RG 정산 수집 중단(…) — 쿠키 재등록 필요`로 하드코딩돼 있어 `쿠키 만료` 토큰에 안 걸렸고,
// **가장 중요한 쿠키 케이스 둘이 「잡 실패」보다 뒤로** 갔다. 문구는 사람이 읽으라고 바뀌는
// 것이고 등급이 그걸 따라가면 안 된다 — 그래서 «분기별 등급»을 여기에 못 박는다.
describe("buildPipelineHealthBanner — 등급 (분기별 고정)", () => {
  const cookie = (account_key: string) =>
    ({ account_key, state: "stale", age_days: 9 }) as never;
  const job = "auto_sync_orders";

  it.each([
    ["COUPANG_WING1", "RG 정산 수집 중단(오픽스)"],
    ["COUPANG_WING2", "RG 정산 수집 중단(오하이테크)"],
  ])("★%s 쿠키 만료가 「잡 실패」보다 앞선다 (P1 재발 방지)", (key, expected) => {
    const b = buildPipelineHealthBanner(
      makeHealth({ cookies_stale: [cookie(key)], missing_jobs: [job] }),
    );
    expect(b!.items[0]).toContain(expected);
    expect(b!.items[1]).toContain("잡 실패");
  });

  it("일반 쿠키 만료도 잡 실패보다 앞선다 (WING만 특별대우하지 않는다)", () => {
    const b = buildPipelineHealthBanner(
      makeHealth({ cookies_stale: [cookie("NAVER_X")], missing_jobs: [job] }),
    );
    expect(b!.items[0]).toContain("쿠키 만료");
  });

  it.each([
    ["net_profit 누락형 data_stale", { data_stale: [{ source: "rg", state: "stale", impact: "RG 정산비용(오픽스)이 net_profit에서 누락 중" }] as never }],
    ["원가 드리프트", { cost_drift: { drift_count: 177, by_buffer: { "0": 177 }, sample: [] } as never }],
    ["보존식 불일치", { vendor_item_conservation: { mismatch: [{ sales_date: "2026-08-18", diff: -88800, summary_gmv: 1, option_gmv: 2 }] } as never }],
    ["부분수집", { partial_sync: [{ sync_log_id: 1, channel_id: 6, channel_name: "네이버", at: "2026-08-19T10:00:00", records_synced: 1, detail: "[부분수집] x" }] as never }],
  ])("%s 는 잡 실패보다 앞선다 (돈이 조용히 샌다)", (_label, extra) => {
    const b = buildPipelineHealthBanner(makeHealth({ ...extra, missing_jobs: [job] }));
    expect(b!.items[b!.items.length - 1]).toContain("잡 실패");
  });

  it("정체형 data_stale은 «멈춤»이라 돈 계열보다 뒤로 간다", () => {
    const b = buildPipelineHealthBanner(
      makeHealth({
        data_stale: [
          { source: "a", state: "stale", impact: "쿠팡 판매분석 요약축(오픽스) 정체 — 대조 상대가 낡았다" },
          { source: "b", state: "stale", impact: "RG 정산비용(오픽스)이 net_profit에서 누락 중" },
        ] as never,
      }),
    );
    expect(b!.items[0]).toContain("net_profit");
    expect(b!.items[1]).toContain("정체");
  });

  it("등급 2(분류 실패)로 떨어지는 분기가 없다 — 모든 push가 0 또는 1을 명시한다", () => {
    // 모든 분기를 한 번에 켜고, 「잡 실패」(등급 1)보다 뒤에 오는 항목이 없는지 본다.
    // 등급 2가 생기면 그 항목만 잡 실패 뒤로 밀려 여기서 걸린다.
    const b = buildPipelineHealthBanner(
      makeHealth({
        scheduler_running: false,
        cookies_stale: [cookie("COUPANG_WING1")],
        data_stale: [{ source: "a", state: "stale", impact: "쿠팡 판매분석 요약축 정체 — 낡았다" }] as never,
        disk_low: [{ path: "/", state: "low", used_percent: 95.5, warn_percent: 90, free_bytes: 1, total_bytes: 2, impact: "포화 시 멈춘다" }],
        missing_jobs: [job],
      }),
    );
    const lastRankOne = b!.items.filter((t) => /잡 실패|정체|디스크|스케줄러 정지/.test(t));
    expect(lastRankOne.length).toBe(4);
    // 등급 1 그룹이 연속으로 끝에 몰려 있어야 한다(그 뒤에 등급 2가 없다).
    const firstRankOneIdx = b!.items.findIndex((t) => /잡 실패|정체|디스크|스케줄러 정지/.test(t));
    expect(b!.items.slice(firstRankOneIdx).every((t) => /잡 실패|정체|디스크|스케줄러 정지/.test(t))).toBe(true);
  });
});
