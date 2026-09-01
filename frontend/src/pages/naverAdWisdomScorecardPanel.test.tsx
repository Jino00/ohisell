// @vitest-environment jsdom
//
// naverAdWisdomScorecardPanel.test.tsx — M3-a 지혜 성적표 «표면» 회귀 (적대 리뷰 1R, 2026-08-22).
//
// ★존재 이유: 이 패널을 처음 낼 때 백엔드는 HTTP body까지 잘 지켜져 있었는데(라우터에서
//   판정 키를 떨어뜨리는 변이는 죽었다), **body가 화면에 닿는 마지막 한 칸**은 무엇을
//   지워도 프론트 495개 테스트가 전부 초록이었다. 적대 리뷰가 주입한 표면 변이 3종
//   (evidence_gap 렌더 제거 · setWisdomCard 제거 · 귀속 문구 제거)이 **전부 생존**했다.
//
//   이 패널이 존재하는 이유가 「표본이 0일 때 아무것도 안 그리면 «문제없음»으로 읽힌다」인데,
//   그 문구를 지워도 아무도 몰랐다면 패널은 있으나 마나다. 그래서 이 파일은 «값»이 아니라
//   «사람 눈에 닿는가»를 잰다.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => {
  const pending = () => new Promise<never>(() => {});
  return {
    pending,
    wisdom: null as unknown,
    wisdomFails: false,
    avaFails: false,
    // D-NAO-249 F1 — param_change 승인 카드 테스트용. null이면 pending(영구 로딩).
    proposals: null as unknown,
    guardrail: null as unknown,
    updateStatusCalls: [] as unknown[],
    updateStatusImpl: null as null | ((id: number, status: string, extra?: unknown) => Promise<unknown>),
  };
});

vi.mock("../lib/api", () => ({
  fetchNaverAdReport: () => h.pending(),
  fetchNaverAdProposals: () => (h.proposals ? Promise.resolve(h.proposals) : h.pending()),
  fetchNaverCampaignSettings: () => h.pending(),
  putNaverCampaignSettings: () => h.pending(),
  fetchNaverAdDiagnosis: () => h.pending(),
  fetchNaverExpertReviews: () =>
    h.avaFails ? Promise.reject(new Error("Ava 조회 실패")) : Promise.resolve({ rows: [] }),
  fetchNaverExpertScorecard: () =>
    h.avaFails
      ? Promise.reject(new Error("Ava 조회 실패"))
      : Promise.resolve({ sample_n: 0, accuracy: null, label: "표본 없음" }),
  fetchNaverWisdomScorecard: () =>
    h.wisdomFails ? Promise.reject(new Error("지혜 조회 실패")) : Promise.resolve(h.wisdom),
  updateNaverProposalStatus: (id: number, status: string, extra?: unknown) => {
    h.updateStatusCalls.push([id, status, extra]);
    return h.updateStatusImpl ? h.updateStatusImpl(id, status, extra) : h.pending();
  },
  executeNaverProposal: () => h.pending(),
  getNaverExpertDelegation: () => h.pending(),
  putNaverExpertDelegation: () => h.pending(),
  getNaverDashboardOverview: () => h.pending(),
  getNaverGuardrailParams: () => (h.guardrail ? Promise.resolve(h.guardrail) : h.pending()),
  putNaverGuardrailParams: () => h.pending(),
  // D-NAO-283 — 발의 폼(NaverAdProposalForm)이 콘솔 안에서 부르는 API.
  // 이 테스트들의 관심사가 아니라 pending으로 둔다(폼 자체는 naverAdProposalForm.test.tsx가 잰다).
  fetchNaverProposableTypes: () => h.pending(),
  createNaverProposal: () => h.pending(),
}));

import NaverAdOptimizationConsole from "./NaverAdOptimizationConsole";

const renderPage = () =>
  render(
    <MemoryRouter>
      <NaverAdOptimizationConsole />
    </MemoryRouter>,
  );

const VALUE_DEF = {
  metric: "총이익(gross profit) 절대액",
  formula: "(conv_amt x cf / bep_roas) - cost",
  grain: "조치 1건 (naver_change_log 행)",
  verdict_rule: "조치 전/후 총이익의 부호 비교",
  conversion_delay: { window: "D+1~D+7 (전환 정착 창)", correction_applied: false, note: "미적용" },
  bep_coverage: { groups_total: 1013, groups_with_product_bep: 231, ratio: 0.228, note: "근사" },
  legacy_note: "옛 자는 불변 보존",
};

const ATTRIBUTION = {
  path: "OpsWisdomEntry.param_proposal_id -> NaverProposal -> NaverChangeLog",
  limitation: "추적 가능한 경로는 param_proposal_id 1:1 링크뿐이다. 이 롤업은 지혜 기여의 하한이다.",
};

const ROW_BASE = {
  wisdom_id: 1,
  wisdom_text: "주말·여름·아이폰 비시즌 조건에서 bid_up은 차단한다.",
  status: "active",
  promoted_at: "2026-07-27 08:45:00",
  source_candidate_id: 3,
  linked_proposals: [],
  linked_proposal_count: 1,
  briefing_injected: false,
  briefing_injection_note: "주입 여부만 관측한다 — «주입됐다»가 «효과가 났다»를 뜻하지 않는다.",
  has_evidence: false,
  evidence_gap: "제안은 났으나 실집행 조치가 0건이다 (제안 상태: rejected).",
  changes_total: 0,
  changes_executed: 0,
  changes_scored_profit: 0,
  verdicts: {},
  bep_sources: {},
  gave_before_sum: null,
  gave_after_sum: null,
  gave_delta_sum: null,
  gave_pairs: 0,
  profit_before_sum: null,
  profit_after_sum: null,
  profit_delta_sum: null,
  profit_pairs: 0,
  profit_unavailable: 0,
  profit_unjudged: 0,
  details: [],
};

// 반성 루프 상태(D-NAO-228) — 기본은 «결번이 전부 재료없음»(= L3 정지 중의 정상 상태).
const REFLECTION_HEALTH = {
  window: { start: "2026-07-18", end: "2026-08-22", days: 36 },
  last_success_kst: "2026-08-19",
  gap_days_since_success: 3,
  missing_days: 19,
  counts: { ok: 17, skipped_no_material: 19, failed: 0, unresolved: 0, pending: 0 },
  headline:
    "반성 최근 성공 2026-08-19(3일 전) · 창 2026-07-18~2026-08-22 36일 중 결번 19일 = 재료없음 19 / 실패 0 / 미상 0",
  days: [],
  evidence_gap: "배선 이전 구간은 DB만으로 실패·미상을 못 가른다.",
  material_note: "재료 = 실집행 일기의 D-1·D-2·D-8. L3 정지 중에는 반성이 안 도는 것이 정상이다.",
};

// A2 후보 현황(D-NAO-248 §4-A) — 기본은 «후보 0건»(빈 상태도 화면에 명시돼야 한다).
const CANDIDATE_STATUS_EMPTY = {
  candidates_total: 0,
  bucket_counts: { legacy: 0, global_pool: 0, separated_experiment: 0, separated_unknown: 0 },
  bucket_labels: {
    legacy: "레거시(캠페인 grain, D-NAO-248 이전)",
    global_pool: "전역 풀(캠페인 통합)",
    separated_experiment: "실험배치 분리(전역 풀과 안 섞임)",
    separated_unknown: "라벨미상 fail-closed 분리(캠페인 단위 고립)",
  },
  retro_harvest_label: "diary 90일 lookback 재집계 — 새 grain 신설이 곧 새 관찰 생성은 아니다",
  candidates: [],
};

const card = (
  row: Record<string, unknown>,
  reflectionHealth: unknown = REFLECTION_HEALTH,
  candidateStatus: unknown = CANDIDATE_STATUS_EMPTY,
  // ★B5(D-NAO-247 점화 계약) — symmetry_report. undefined(기본값)면 그 필드 자체가 응답에
  //   없는 상태를 그대로 재현한다(옵셔널 방어 렌더 검증 — 기존 호출부는 전부 이 상태 그대로).
  symmetryReport: unknown = undefined,
) => ({
  generated_at_kst: "2026-08-22 18:00:00",
  wisdom_total: 1,
  wisdom_active: 1,
  wisdom_with_evidence: row.has_evidence ? 1 : 0,
  candidate_status: candidateStatus,
  value_definition: VALUE_DEF,
  attribution: ATTRIBUTION,
  reflection_health: reflectionHealth,
  wisdom: [row],
  ...(symmetryReport !== undefined ? { symmetry_report: symmetryReport } : {}),
});

// B5 대칭·탐색 관측 픽스처(D-NAO-247 점화 계약) — 기본은 「봉투 변경 0건 · 탐색 일기 0건」.
const SYMMETRY_REPORT_EMPTY = {
  verdict_pending: "[판정불능 예약] 실집행 0건이라 파라미터 변경의 행동·총이익 효과를 관측할 사건이 없다.",
  guardrail_direction: {
    brake: 0, accel: 0, unchanged_or_unknown: 0,
    by_key: {
      cooldown_hours: { brake: 0, accel: 0 },
      max_daily_auto_bid_downs: { brake: 0, accel: 0 },
      max_auto_up_multiple: { brake: 0, accel: 0 },
    },
    total_changes: 0,
  },
  exploration: {
    window_days: 28,
    boundary_changed_at: null,
    before: null,
    after: null,
    whole_window: {
      total: 0, by_actor: {}, explore_share: null,
      explore_total: 0, explore_blocked: 0, explore_blocked_rate: null,
    },
    note: "파라미터 변경 이력이 없다 — «전/후»를 가를 경계가 없다.",
  },
};

// D-NAO-249 F1 — param_change 승인 카드 픽스처.
const PARAM_PROPOSAL_BASE = {
  id: 501,
  created_at: "2026-08-25 10:00:00",
  proposal_type: "param_change",
  target_type: "guardrail_param",
  target_id: "cooldown_hours",
  campaign_id: "cmp1",
  adgroup_id: null,
  target_name: null,
  campaign_name: null,
  rationale: "지혜 근거 — bid_up 진동이 관측됨",
  expected_effect: null,
  status: "pending",
  slack_ts: null,
  executed_change_log_id: null,
  target_bid: null,
  target_lock: null,
  target_budget: null,
  budget_auto_eligible: null,
  informational: false,
  decision_only: true,
  action: null,
  expert_verdict: null,
  executable: false,
  not_executable_reason: null,
  approval_source: null,
} as const;

const BID_UP_PROPOSAL = {
  ...PARAM_PROPOSAL_BASE,
  id: 601,
  proposal_type: "bid_up",
  target_type: "keyword",
  target_id: "kw1",
  decision_only: false,
  action: "update_bid",
  target_bid: 500,
  executable: true,
} as const;

const proposalsList = (rows: unknown[]) => ({ total: rows.length, open_actions: [], rows });

const GUARDRAIL_FIXTURE = {
  params: [
    {
      key: "cooldown_hours", label: "쿨다운", value: 4, source: "db" as const, code_default: 2,
      min: 1, max: 24, why: "근거 문구", direction: "tighten_up" as const, rejected: false,
      updated_at: null,
    },
  ],
  from_db_enabled: true,
  from_db_help: "되돌림 절차 설명 XYZ",
  retro_freshness: { latest_asof: "2026-08-24", expected_asof: "2026-08-25", stale: false, lag_days: 0 },
};

afterEach(() => {
  cleanup();
  h.wisdom = null;
  h.wisdomFails = false;
  h.avaFails = false;
  h.proposals = null;
  h.guardrail = null;
  h.updateStatusCalls = [];
  h.updateStatusImpl = null;
  vi.clearAllMocks();
});

describe("지혜 성적표 패널 — 사람 눈에 닿는가", () => {
  it("표본 0이면 «왜 잴 것이 없나»를 화면에 낸다 (빈 성적표를 «문제없음»으로 읽지 않게)", async () => {
    h.wisdom = card(ROW_BASE);
    renderPage();
    expect(await screen.findByText(/아직 잴 것이 없습니다/)).toBeTruthy();
    expect(screen.getByText(/실집행 조치가 0건/)).toBeTruthy();
  });

  // ── 반성 루프 상태(D-NAO-228 · 계약 PLAN_naver-m5-reflection-visibility.md §5 ⓐ) ──
  // ★2026-07-18~08-22 결번 19일 동안 스케줄러 로그는 성공·재료없음·LLM 실패를 전부 'ok'로
  //   적었고 아무도 몰랐다. 여기서 재는 것은 「그 사실이 Jino 눈에 닿는가」다.
  it("반성 결번 내역이 성적표 위에 한 줄로 뜬다 (침묵이 화면에 보여야 한다)", async () => {
    h.wisdom = card(ROW_BASE);
    renderPage();
    expect(await screen.findByText(/반성 최근 성공 2026-08-19/)).toBeTruthy();
    expect(screen.getByText(/결번 19일/)).toBeTruthy();
  });

  it("결번이 전부 «재료 없음»이면 고장으로 그리지 않는다 (L3 정지 중엔 정상이다)", async () => {
    h.wisdom = card(ROW_BASE);
    renderPage();
    expect(await screen.findByText(/결번은 전부 «재료 없음»이다/)).toBeTruthy();
  });

  it("실패·미상이 있으면 그 숫자를 «그날의 학습이 없다»와 함께 낸다", async () => {
    h.wisdom = card(ROW_BASE, {
      ...REFLECTION_HEALTH,
      counts: { ok: 17, skipped_no_material: 12, failed: 6, unresolved: 1, pending: 0 },
      headline: "반성 최근 성공 2026-08-19(3일 전) · 결번 19일 = 재료없음 12 / 실패 6 / 미상 1",
    });
    renderPage();
    expect(await screen.findByText(/실패 6일 · 원인미상 1일/)).toBeTruthy();
    expect(screen.getByText(/그날의 학습이 없다/)).toBeTruthy();
  });

  it("결번 0일이면 «전부 재료 없음»이라 쓰지 않는다 (거짓 문구 방지)", async () => {
    h.wisdom = card(ROW_BASE, {
      ...REFLECTION_HEALTH,
      missing_days: 0,
      counts: { ok: 36, skipped_no_material: 0, failed: 0, unresolved: 0, pending: 0 },
      headline: "반성 최근 성공 2026-08-22(오늘) · 창 36일 중 결번 0일 = 재료없음 0 / 실패 0 / 미상 0",
    });
    renderPage();
    expect(await screen.findByText(/결번 없음/)).toBeTruthy();
  });

  it("오늘 08:35 미도래(pending)는 경고색으로 뜨지 않는다 (적대 리뷰 1R P1-2)", async () => {
    h.wisdom = card(ROW_BASE, {
      ...REFLECTION_HEALTH,
      missing_days: 0,
      counts: { ok: 35, skipped_no_material: 0, failed: 0, unresolved: 0, pending: 1 },
      headline: "반성 최근 성공 2026-08-22(오늘) · 창 36일 중 결번 0일 = 재료없음 0 / 실패 0 / 미상 0 (오늘은 08:35 미도래)",
    });
    renderPage();
    const line = await screen.findByText(/결번 없음/);
    // «그날의 학습이 없다»(경고 문구)가 뜨면 미도래를 고장으로 그린 것이다.
    expect(screen.queryByText(/그날의 학습이 없다/)).toBeNull();
    expect(line).toBeTruthy();
  });

  it("승격 지혜가 0건이어도 반성 상태는 뜬다 (빈 성적표의 원인을 구분하는 자리다)", async () => {
    h.wisdom = { ...card(ROW_BASE), wisdom: [], wisdom_total: 0, wisdom_active: 0 };
    renderPage();
    expect(await screen.findByText(/아직 승격된 지혜가 없습니다/)).toBeTruthy();
    expect(screen.getByText(/반성 최근 성공 2026-08-19/)).toBeTruthy();
  });

  it("귀속의 «한계»가 화면에 남는다 (롤업이 하한이라는 사실이 숫자 옆에 있어야 한다)", async () => {
    h.wisdom = card(ROW_BASE);
    renderPage();
    expect(await screen.findByText(/지혜 기여의 하한/)).toBeTruthy();
  });

  it("값의 정의(식·정착보정 상태·BEP 커버리지)가 산출물 옆에 붙는다", async () => {
    h.wisdom = card(ROW_BASE);
    renderPage();
    expect(await screen.findByText(/conv_amt x cf \/ bep_roas/)).toBeTruthy();
    expect(screen.getByText(/정착 보정 미적용/)).toBeTruthy();
    expect(screen.getByText(/231\/1013/)).toBeTruthy();
  });

  it("★크기 축은 총이익 «금액»이다 — GAVE가 헤드라인이 되면 판정과 반대 부호를 가리킬 수 있다", async () => {
    h.wisdom = card({
      ...ROW_BASE,
      has_evidence: true,
      changes_total: 1,
      changes_executed: 1,
      changes_scored_profit: 1,
      verdicts: { declined: 1 },
      bep_sources: { product_bep: 1 },
      gave_delta_sum: 250000,
      gave_pairs: 1,
      profit_delta_sum: -533333,
      profit_pairs: 1,
      evidence_gap: null,
    });
    renderPage();
    // 총이익 금액이 «있어야» 한다
    expect(await screen.findByText(/총이익 델타 -533,333원/)).toBeTruthy();
    // GAVE는 남되 «참고»로 강등돼야 한다
    expect(screen.getByText(/참고 GAVE 델타/)).toBeTruthy();
    expect(screen.getByText(/총이익 악화 1건/)).toBeTruthy();
  });

  it("금액 산출불가 건수가 숨지 않는다 (렌즈 미기록을 0원으로 읽지 않게)", async () => {
    h.wisdom = card({
      ...ROW_BASE, has_evidence: true, changes_total: 2, changes_executed: 2,
      changes_scored_profit: 2, verdicts: { improved: 2 }, profit_unavailable: 2,
      evidence_gap: null,
    });
    renderPage();
    expect(await screen.findByText(/금액 산출불가 2건/)).toBeTruthy();
  });

  it("★적대 리뷰 2R P1: 합계에서 빠진 «판정 보류» 건수가 화면에 남는다", async () => {
    h.wisdom = card({
      ...ROW_BASE, has_evidence: true, changes_total: 4, changes_executed: 4,
      changes_scored_profit: 1, verdicts: { improved: 1 },
      profit_delta_sum: 100000, profit_pairs: 1, profit_unjudged: 3,
      evidence_gap: null,
    });
    renderPage();
    expect(await screen.findByText(/총이익 델타 \+100,000원/)).toBeTruthy();
    // 합에서 빠진 3건이 조용히 사라지면 「4건 중 1건만 쟀다」는 사실이 화면에서 증발한다
    expect(screen.getByText(/판정 보류 3건/)).toBeTruthy();
  });

  it("BEP 커버리지 산출이 실패해도 그 사실이 화면에 남는다", async () => {
    h.wisdom = card(ROW_BASE);
    (h.wisdom as any).value_definition = {
      ...VALUE_DEF,
      bep_coverage: { groups_total: null, groups_with_product_bep: null, ratio: null,
                      note: "커버리지 산출에 실패했다(판정불능)" },
    };
    renderPage();
    expect(await screen.findByText(/판정불능/)).toBeTruthy();
  });

  it("★적대 리뷰 P1-2: Ava 조회가 실패해도 지혜 성적표는 화면에 남는다", async () => {
    h.avaFails = true;
    h.wisdom = card(ROW_BASE);
    renderPage();
    // 지혜 응답은 이미 성공했다 — 옆 패널의 장애가 이걸 «조용한 빈 카드»로 만들면 안 된다.
    expect(await screen.findByText(/아직 잴 것이 없습니다/)).toBeTruthy();
  });

  it("지혜 조회 자체가 실패하면 그 사실을 말한다 (조용히 비어 있지 않게)", async () => {
    h.wisdomFails = true;
    renderPage();
    await waitFor(() => expect(screen.getByText(/지혜 조회 실패/)).toBeTruthy());
  });

  // ── ★D-NAO-248 §4-A2: 후보 현황(승격 전) 블록 ──────────────────────────────
  describe("A2 후보 현황 블록", () => {
    it("후보 0건이면 «아직 수확된 후보가 없습니다»를 낸다 (침묵 방지)", async () => {
      h.wisdom = card(ROW_BASE);
      renderPage();
      expect(await screen.findByText(/아직 수확된 후보가 없습니다/)).toBeTruthy();
      // 버킷 라벨은 0건이어도 항상 뜬다
      expect(screen.getByText(/전역 풀\(캠페인 통합\) 0건/)).toBeTruthy();
    });

    it("시그니처·캠페인별 분해·「기존 재료 재집계」 라벨이 화면에 뜬다", async () => {
      h.wisdom = card(ROW_BASE, REFLECTION_HEALTH, {
        ...CANDIDATE_STATUS_EMPTY,
        candidates_total: 1,
        bucket_counts: { ...CANDIDATE_STATUS_EMPTY.bucket_counts, global_pool: 1 },
        candidates: [
          {
            candidate_id: 30,
            signature: "g|SHOPPING|bid_up|weekday|summer|normal|",
            status: "pending",
            grain: "global",
            bucket: "global_pool",
            bucket_label: "전역 풀(캠페인 통합)",
            campaign_type: "SHOPPING",
            experiment_batch: null,
            action: "bid_up",
            occurrences: 91,
            good_count: 60,
            bad_count: 31,
            campaign_count: 2,
            by_campaign: { cmp1: { good: 45, bad: 20 }, cmp2: { good: 15, bad: 11 } },
            observation: "[패턴·전역] SHOPPING 유형의 bid_up",
            first_seen_at: "2026-07-01 00:00:00",
            last_seen_at: "2026-08-20 00:00:00",
          },
        ],
      });
      renderPage();
      expect(await screen.findByText(/g\|SHOPPING\|bid_up\|weekday\|summer\|normal\|/)).toBeTruthy();
      expect(screen.getByText(/관찰 91회\(good 60\/bad 31\)/)).toBeTruthy();
      expect(screen.getByText(/캠페인 2개/)).toBeTruthy();
      expect(screen.getByText(/cmp1\(45\/20\), cmp2\(15\/11\)/)).toBeTruthy();
      // 「기존 재료 재집계」 라벨 — 없으면 새 배움처럼 읽힌다(계약 판단기준)
      expect(screen.getByText(/재집계/)).toBeTruthy();
    });

    it("레거시(캠페인 grain) 후보와 전역 후보가 라벨로 구별된다", async () => {
      h.wisdom = card(ROW_BASE, REFLECTION_HEALTH, {
        ...CANDIDATE_STATUS_EMPTY,
        candidates_total: 2,
        bucket_counts: { legacy: 1, global_pool: 1, separated_experiment: 0, separated_unknown: 0 },
        candidates: [
          {
            candidate_id: 1, signature: "cmp1|bid_up|weekday|summer|normal", status: "rejected",
            grain: null, bucket: "legacy", bucket_label: "레거시(캠페인 grain, D-NAO-248 이전)",
            campaign_type: null, experiment_batch: null, action: "bid_up",
            occurrences: 45, good_count: 20, bad_count: 25, campaign_count: 1, by_campaign: {},
            observation: "[패턴] 캠페인 cmp1의 bid_up", first_seen_at: null, last_seen_at: null,
          },
          {
            candidate_id: 31, signature: "g|SHOPPING|bid_up|weekday|summer|normal|", status: "pending",
            grain: "global", bucket: "global_pool", bucket_label: "전역 풀(캠페인 통합)",
            campaign_type: "SHOPPING", experiment_batch: null, action: "bid_up",
            occurrences: 91, good_count: 60, bad_count: 31, campaign_count: 2,
            by_campaign: { cmp1: { good: 45, bad: 20 }, cmp2: { good: 15, bad: 11 } },
            observation: "[패턴·전역] SHOPPING 유형의 bid_up", first_seen_at: null, last_seen_at: null,
          },
        ],
      });
      renderPage();
      // 같은 라벨이 요약 줄(버킷별 건수)과 후보별 항목 줄 «두 곳」에 뜨므로 findAllByText로 잰다.
      expect((await screen.findAllByText(/레거시\(캠페인 grain, D-NAO-248 이전\)/)).length).toBeGreaterThan(0);
      expect(screen.getAllByText(/전역 풀\(캠페인 통합\)/).length).toBeGreaterThan(0);
    });
  });

  // ── ★D-NAO-248 §4-A7: 소비 현황(브리핑 주입·제안 결정 메타) ────────────────
  describe("A7 소비 현황", () => {
    it("브리핑 주입 여부가 배지로 뜬다", async () => {
      h.wisdom = card({ ...ROW_BASE, briefing_injected: true });
      renderPage();
      expect(await screen.findByText(/브리핑 주입 됨/)).toBeTruthy();
    });

    it("브리핑 미주입은 «안 됨»으로 뜬다 (침묵이 아니라 명시)", async () => {
      h.wisdom = card({ ...ROW_BASE, briefing_injected: false });
      renderPage();
      expect(await screen.findByText(/브리핑 주입 안 됨/)).toBeTruthy();
    });

    it("★기존 제안(컬럼 신설 전 결정)은 «기록 없음»으로 뜬다 — 사유를 지어내지 않는다", async () => {
      h.wisdom = card({
        ...ROW_BASE,
        linked_proposals: [
          {
            proposal_id: 2314, proposal_type: "param_change", status: "rejected",
            campaign_id: "cmp1", executed_change_log_id: null,
            decided_at: null, decided_by: null, decision_note: "기록 없음(컬럼 신설 전)",
          },
        ],
      });
      renderPage();
      expect(await screen.findByText(/제안 #2314\(param_change\) · rejected/)).toBeTruthy();
      expect(screen.getByText(/기록 없음\(컬럼 신설 전\)/)).toBeTruthy();
    });

    it("결정 메타가 있으면 결정일·주체·사유가 그대로 뜬다", async () => {
      h.wisdom = card({
        ...ROW_BASE,
        linked_proposals: [
          {
            proposal_id: 500, proposal_type: "param_change", status: "approved",
            campaign_id: "cmp1", executed_change_log_id: 50,
            decided_at: "2026-08-25 09:00:00", decided_by: "console:jino",
            decision_note: "승인 — 근거 충분",
          },
        ],
      });
      renderPage();
      expect(await screen.findByText(/결정 2026-08-25 \(console:jino\): 승인 — 근거 충분/)).toBeTruthy();
    });

    it("이 지혜가 낳은 제안이 없으면 명시적으로 「없음」을 낸다", async () => {
      h.wisdom = card({ ...ROW_BASE, linked_proposals: [] });
      renderPage();
      expect(await screen.findByText(/이 지혜가 낳은 제안 없음/)).toBeTruthy();
    });
  });
});

// ── ★D-NAO-249 F1: param_change 승인 카드 값 입력 ─────────────────────────
describe("param_change 승인 카드 — 값 입력(D-NAO-249 F1)", () => {
  it("★프리필은 «현재값»이다 — 제안이 권하는 값이 아니다(판사는 키·방향만 정한다)", async () => {
    h.wisdom = card(ROW_BASE);
    h.guardrail = GUARDRAIL_FIXTURE;
    h.proposals = proposalsList([PARAM_PROPOSAL_BASE]);
    renderPage();
    const input = (await screen.findByLabelText("cooldown_hours 적용 값")) as HTMLInputElement;
    expect(input.value).toBe("4"); // GUARDRAIL_FIXTURE의 cooldown_hours 현재값
  });

  it("승인 시 applied_value가 body에 그대로 실려 나간다", async () => {
    window.confirm = vi.fn(() => true);
    h.wisdom = card(ROW_BASE);
    h.guardrail = GUARDRAIL_FIXTURE;
    h.proposals = proposalsList([PARAM_PROPOSAL_BASE]);
    h.updateStatusImpl = async () => ({ ...PARAM_PROPOSAL_BASE, status: "approved" });
    renderPage();
    const input = await screen.findByLabelText("cooldown_hours 적용 값");
    fireEvent.change(input, { target: { value: "6" } });
    const approveBtn = within(input.parentElement as HTMLElement).getByRole("button", { name: "승인" });
    fireEvent.click(approveBtn);
    await waitFor(() => expect(h.updateStatusCalls.length).toBeGreaterThan(0));
    expect(h.updateStatusCalls[0]).toEqual([501, "approved", { appliedValue: 6 }]);
  });

  it("★허용 범위 밖 값은 서버로 보내지 못하게 막는다(제출 차단, 화면에서 먼저 알린다)", async () => {
    window.confirm = vi.fn(() => true);
    h.wisdom = card(ROW_BASE);
    h.guardrail = GUARDRAIL_FIXTURE; // min 1 ~ max 24
    h.proposals = proposalsList([PARAM_PROPOSAL_BASE]);
    renderPage();
    const input = await screen.findByLabelText("cooldown_hours 적용 값");
    fireEvent.change(input, { target: { value: "99" } });
    const approveBtn = within(input.parentElement as HTMLElement).getByRole("button", { name: "승인" });
    fireEvent.click(approveBtn);
    expect(await screen.findByText(/허용 범위 1 ~ 24 밖입니다/)).toBeTruthy();
    expect(h.updateStatusCalls.length).toBe(0); // 서버에 안 나감
  });

  it("param_change가 아닌 제안의 승인 흐름은 회귀 없이 그대로다(body가 예전과 같다)", async () => {
    window.confirm = vi.fn(() => true);
    h.wisdom = card(ROW_BASE);
    h.guardrail = GUARDRAIL_FIXTURE;
    h.proposals = proposalsList([BID_UP_PROPOSAL]);
    h.updateStatusImpl = async () => ({ ...BID_UP_PROPOSAL, status: "approved" });
    renderPage();
    // ★"승인"이라는 accessible name은 상태 필터 탭 버튼과도 겹친다 — 제안 카드 행으로
    // 스코프를 좁혀 탭이 아니라 카드의 승인 버튼을 누른다.
    const rationale = await screen.findByText(BID_UP_PROPOSAL.rationale);
    const row = rationale.closest(".p-4") as HTMLElement;
    const approveBtn = within(row).getByRole("button", { name: "승인" });
    fireEvent.click(approveBtn);
    await waitFor(() => expect(h.updateStatusCalls.length).toBeGreaterThan(0));
    // extra 인자를 아예 안 넘긴다 — 예전 시그니처 그대로(회귀 대상).
    expect(h.updateStatusCalls[0]).toEqual([601, "approved", undefined]);
  });

  it("★400 응답의 서버 메시지를 그대로 보여준다(삼키지 않는다)", async () => {
    window.confirm = vi.fn(() => true);
    h.wisdom = card(ROW_BASE);
    h.guardrail = GUARDRAIL_FIXTURE;
    h.proposals = proposalsList([PARAM_PROPOSAL_BASE]);
    h.updateStatusImpl = async () => {
      throw new Error(
        'API error 400: {"detail":"param_change 승인은 applied_value가 필요합니다 — 적용할 값은 사람이 정합니다"}',
      );
    };
    renderPage();
    const input = await screen.findByLabelText("cooldown_hours 적용 값");
    const approveBtn = within(input.parentElement as HTMLElement).getByRole("button", { name: "승인" });
    fireEvent.click(approveBtn);
    expect(await screen.findByText(/param_change 승인은 applied_value가 필요합니다/)).toBeTruthy();
  });
});

// ── ★D-NAO-249 F2: 되돌림 도움말(B3) ──────────────────────────────────────
describe("봉투 현황판 — 되돌림 도움말(D-NAO-249 F2)", () => {
  it("from_db_help가 화면에 뜬다(서버 문구를 그대로 낸다)", async () => {
    h.wisdom = card(ROW_BASE);
    h.guardrail = { ...GUARDRAIL_FIXTURE, from_db_enabled: false };
    renderPage();
    expect(await screen.findByText(/되돌림 절차 설명 XYZ/)).toBeTruthy();
  });
});

// ── ★D-NAO-249 F3/F4: 지혜 성적표 섹션 4 — param_gate · search_term_material ──
describe("지혜 성적표 — param_gate · search_term_material(D-NAO-249 F3/F4)", () => {
  it("param_gate 4종은 0이어도 화면에 뜬다(조용한 0과 죽은 카운터를 구분)", async () => {
    h.wisdom = card(ROW_BASE, REFLECTION_HEALTH, {
      ...CANDIDATE_STATUS_EMPTY,
      param_gate: {
        unconditional_mapped: 0, conditional_fallback: 0, unmapped_param: 0, no_suggestion: 0,
      },
    });
    renderPage();
    expect(await screen.findByText(/제안 생성 0건/)).toBeTruthy();
    expect(screen.getByText(/제안 안 냄\(조건부\) 0건/)).toBeTruthy();
    expect(screen.getByText(/제안 안 냄\(미매핑\) 0건/)).toBeTruthy();
    expect(screen.getByText(/제안 없음 0건/)).toBeTruthy();
  });

  it("param_gate 카운트가 있으면 그 값을 낸다(0이 아닌 값도 정확히)", async () => {
    h.wisdom = card(ROW_BASE, REFLECTION_HEALTH, {
      ...CANDIDATE_STATUS_EMPTY,
      param_gate: {
        unconditional_mapped: 3, conditional_fallback: 5, unmapped_param: 1, no_suggestion: 40,
      },
    });
    renderPage();
    expect(await screen.findByText(/제안 생성 3건/)).toBeTruthy();
    expect(screen.getByText(/제안 안 냄\(조건부\) 5건/)).toBeTruthy();
    expect(screen.getByText(/제안 안 냄\(미매핑\) 1건/)).toBeTruthy();
    expect(screen.getByText(/제안 없음 40건/)).toBeTruthy();
  });

  it("★search_term_material이 없으면 아무것도 안 그린다(옵셔널 방어, 백엔드가 아직 안 줄 수 있다)", async () => {
    h.wisdom = card(ROW_BASE); // CANDIDATE_STATUS_EMPTY에는 search_term_material 필드가 없다
    renderPage();
    // 렌더 자체가 죽지 않고 기존 표면은 그대로 뜬다.
    expect(await screen.findByText(/아직 수확된 후보가 없습니다/)).toBeTruthy();
    expect(screen.queryByText(/검색어 재료/)).toBeNull();
  });

  it("search_term_material이 있으면 건수·분포·label을 낸다", async () => {
    h.wisdom = card(ROW_BASE, REFLECTION_HEALTH, {
      ...CANDIDATE_STATUS_EMPTY,
      search_term_material: {
        total: 12,
        by_status: { stopped: 3, leaking: 2, ambiguous: 1, no_data: 4, absent: 1, unknown: 1 },
        label: "재료 라벨 문구",
      },
    });
    renderPage();
    expect(await screen.findByText(/검색어 재료 · 12건/)).toBeTruthy();
    expect(screen.getByText(/stopped 3건/)).toBeTruthy();
    expect(screen.getByText(/재료 라벨 문구/)).toBeTruthy();
  });

  // ★적대 리뷰 2R P2-F 상환 — 백엔드가 버킷을 늘렸는데 `statusOrder`를 안 늘리면 헤더의
  //   총계와 칩 합계가 **말없이 어긋난다.** 2026-08-25 not_harvestable 때는 타입·statusOrder·
  //   label 셋 다 고쳤는데 S3 return_experiment 때는 label만 고쳐 같은 병이 재발했다.
  //   그래서 「칩 하나가 빠졌다」가 아니라 **「합이 안 맞는다」**를 못 박는다 — 다음 버킷이
  //   생겨도 이 테스트가 빨개진다.
  it("★칩 합계가 헤더 총계와 일치한다(버킷이 늘어도 화면이 조용히 어긋나지 않는다)", async () => {
    const byStatus = {
      stopped: 3, leaking: 2, ambiguous: 1, no_data: 4, absent: 1, unknown: 1,
      not_harvestable: 2, return_experiment: 5,
    };
    const total = Object.values(byStatus).reduce((a, b) => a + b, 0); // 19
    h.wisdom = card(ROW_BASE, REFLECTION_HEALTH, {
      ...CANDIDATE_STATUS_EMPTY,
      search_term_material: { total, by_status: byStatus, label: "재료 라벨 문구" },
    });
    renderPage();
    expect(await screen.findByText(new RegExp(`검색어 재료 · ${total}건`))).toBeTruthy();

    let chipSum = 0;
    for (const [key, n] of Object.entries(byStatus)) {
      const chip = screen.queryByText(new RegExp(`${key} ${n}건`));
      expect(chip, `칩 '${key}'가 화면에 없다 — statusOrder에 키를 안 넣었다`).toBeTruthy();
      chipSum += n;
    }
    expect(chipSum).toBe(total);
  });
});

// ── ★B5 대칭·탐색 관측(D-NAO-247 점화 계약) ─────────────────────────────────────
describe("지혜 성적표 — 대칭·탐색 관측(B5)", () => {
  it("symmetry_report가 없으면(백엔드 배포 순서상 아직 없을 수 있다) 아무것도 안 그리고 렌더가 죽지 않는다", async () => {
    h.wisdom = card(ROW_BASE); // symmetryReport 기본값 undefined — 필드 자체가 응답에 없음
    renderPage();
    expect(await screen.findByText(/아직 수확된 후보가 없습니다/)).toBeTruthy();
    expect(screen.queryByText(/대칭·탐색 관측/)).toBeNull();
  });

  it("0건이어도 [판정불능 예약] 문구·브레이크/액셀 0·by_key 3종이 침묵하지 않고 뜬다", async () => {
    h.wisdom = card(ROW_BASE, REFLECTION_HEALTH, CANDIDATE_STATUS_EMPTY, SYMMETRY_REPORT_EMPTY);
    renderPage();
    expect(await screen.findByText(/대칭·탐색 관측\(B5\)/)).toBeTruthy();
    expect(screen.getByText(/판정불능 예약/)).toBeTruthy();
    expect(screen.getByText(/브레이크 0건 · 액셀 0건/)).toBeTruthy();
    expect(screen.getByText(/cooldown_hours: 브레이크 0·액셀 0/)).toBeTruthy();
    expect(screen.getByText(/max_daily_auto_bid_downs: 브레이크 0·액셀 0/)).toBeTruthy();
    expect(screen.getByText(/max_auto_up_multiple: 브레이크 0·액셀 0/)).toBeTruthy();
    // 파라미터 변경 이력이 없다 — before/after가 아니라 whole_window로 정직하게 표시된다.
    expect(screen.getByText(/창 전체\(경계 없음\)/)).toBeTruthy();
    expect(screen.getByText(/파라미터 변경 이력이 없다/)).toBeTruthy();
  });

  it("브레이크만 있고 액셀이 0건이면 표류 경보를 낸다(성과 판정은 아니다)", async () => {
    h.wisdom = card(ROW_BASE, REFLECTION_HEALTH, CANDIDATE_STATUS_EMPTY, {
      ...SYMMETRY_REPORT_EMPTY,
      guardrail_direction: {
        ...SYMMETRY_REPORT_EMPTY.guardrail_direction,
        brake: 4, accel: 0, total_changes: 4,
        by_key: {
          cooldown_hours: { brake: 4, accel: 0 },
          max_daily_auto_bid_downs: { brake: 0, accel: 0 },
          max_auto_up_multiple: { brake: 0, accel: 0 },
        },
      },
    });
    renderPage();
    expect(await screen.findByText(/브레이크 4건 · 액셀 0건/)).toBeTruthy();
    expect(screen.getByText(/표류 경보/)).toBeTruthy();
  });

  it("파라미터 변경 경계가 있으면 «변경 전/후»를 나눠 각각의 탐색 몫·차단률을 낸다", async () => {
    h.wisdom = card(ROW_BASE, REFLECTION_HEALTH, CANDIDATE_STATUS_EMPTY, {
      ...SYMMETRY_REPORT_EMPTY,
      exploration: {
        window_days: 28,
        boundary_changed_at: "2026-08-20 09:00:00",
        note: "가장 최근 파라미터 변경(change_log_id=5) 시각을 경계로 나눴다.",
        whole_window: null,
        before: {
          total: 10, by_actor: { explore: 6, daily: 4 }, explore_share: 0.6,
          explore_total: 6, explore_blocked: 3, explore_blocked_rate: 0.5,
        },
        after: {
          total: 8, by_actor: { explore: 2, daily: 6 }, explore_share: 0.25,
          explore_total: 2, explore_blocked: 0, explore_blocked_rate: 0,
        },
      },
    });
    renderPage();
    expect(await screen.findByText(/변경 전 · 10건/)).toBeTruthy();
    expect(screen.getByText(/변경 후 · 8건/)).toBeTruthy();
    // 0(측정했더니 0)과 표본없음(null)이 다른 문자열로 렌더된다.
    expect(screen.getByText(/탐색\(explore\) 몫 60\.0% · 탐색 차단률 50\.0% \(3\/6건\)/)).toBeTruthy();
    expect(screen.getByText(/탐색\(explore\) 몫 25\.0% · 탐색 차단률 0\.0% \(0\/2건\)/)).toBeTruthy();
    expect(screen.getByText(/경계 2026-08-20 09:00:00/)).toBeTruthy();
  });
});

// ── ★D-NAO-251 증거보전: 판사 대기열 적체 + 기각분 재개방 상태 ──
// 계약 §5 ③-b가 지목한 «사용자가 보는 표면»이 이 콘솔 패널이다. 백엔드가 값을 만들어도
// 화면이 안 읽으면 「만드는 데까지」에서 합격이 난다(적대 리뷰 1R P1-1이 잡은 자리).
describe("지혜 성적표 — 판사 대기열·재개방 상태(D-NAO-251)", () => {
  const BACKLOG = {
    pending_total: 20,
    pending_ripe: 17,
    cap_next_run: 15,
    days_to_drain: 2,
    cron: "08:45 KST 1일 1회 (캐치업 크론 없음 — 적체는 회차 상한으로 흡수)",
    assumption: "days_to_drain은 신규 후보 유입 0 가정. 실제로는 매일 새 후보가 생긴다.",
  };

  it("적체 지표가 화면에 뜨고, 소화 일수의 «가정»도 함께 보인다", async () => {
    h.wisdom = card(ROW_BASE, REFLECTION_HEALTH, {
      ...CANDIDATE_STATUS_EMPTY,
      judge_backlog: BACKLOG,
    });
    renderPage();
    expect(await screen.findByText(/판사 대기열 · 숙성 17건 \/ 대기 20건/)).toBeTruthy();
    expect(screen.getByText(/다음 회차 상한 15건/)).toBeTruthy();
    expect(screen.getByText(/소화 예상 2일/)).toBeTruthy();
    // ★창·가정을 안 밝힌 커버리지 주장 금지 — 이 문장이 사라지면 「2일」이 단정이 된다.
    expect(screen.getByText(/신규 후보 유입 0 가정/)).toBeTruthy();
  });

  it("평시(적체 없음)엔 상한 5·소화 1일이 그대로 보인다 — 0건도 침묵하지 않는다", async () => {
    h.wisdom = card(ROW_BASE, REFLECTION_HEALTH, {
      ...CANDIDATE_STATUS_EMPTY,
      judge_backlog: { ...BACKLOG, pending_total: 3, pending_ripe: 3, cap_next_run: 5, days_to_drain: 1 },
    });
    renderPage();
    expect(await screen.findByText(/판사 대기열 · 숙성 3건 \/ 대기 3건/)).toBeTruthy();
    expect(screen.getByText(/다음 회차 상한 5건/)).toBeTruthy();
  });

  it("judge_backlog가 없는 응답(배포 순서상 구버전)에서도 화면이 안 깨진다", async () => {
    h.wisdom = card(ROW_BASE, REFLECTION_HEALTH, { ...CANDIDATE_STATUS_EMPTY });
    renderPage();
    expect(await screen.findByText(/후보 현황\(승격 전\)/)).toBeTruthy();
    expect(screen.queryByText(/판사 대기열/)).toBeNull();
  });

  const REJECTED_ROW = {
    candidate_id: 24,
    signature: "g|SHOPPING|bid_up|weekday|summer|normal|",
    status: "rejected",
    grain: "global",
    bucket: "global_pool",
    bucket_label: "전역 풀(캠페인 통합)",
    campaign_type: "SHOPPING",
    experiment_batch: null,
    action: "bid_up",
    occurrences: 91,
    good_count: 60,
    bad_count: 31,
    campaign_count: 1,
    by_campaign: { cmp1: { good: 60, bad: 31 } },
    observation: "obs",
    first_seen_at: "2026-07-20 08:45:00",
    last_seen_at: "2026-08-25 08:45:00",
  };

  it("기각분의 «판정 이후» 증거 축적과 재심 여력이 후보 행에 보인다", async () => {
    h.wisdom = card(ROW_BASE, REFLECTION_HEALTH, {
      ...CANDIDATE_STATUS_EMPTY,
      candidates_total: 1,
      bucket_counts: { ...CANDIDATE_STATUS_EMPTY.bucket_counts, global_pool: 1 },
      candidates: [{
        ...REJECTED_ROW,
        judged_at: "2026-07-28 08:45:00",
        judged_occurrences: 45,
        occurrences_since_judgment: 46,
        rejudge_count: 1,
        reopen_ready: true,
        prior_judgment_count: 1,
      }],
    });
    renderPage();
    // ★이 줄이 곧 「함정이 풀렸다」의 화면 증거다 — 구판은 45에서 얼어 있었다.
    expect(await screen.findByText(/판정 시점 45회 → 이후 \+46회 · 재심 1회/)).toBeTruthy();
    expect(screen.getByText(/이전 판정 1건 보존/)).toBeTruthy();
    expect(screen.getByText("재개방 대기")).toBeTruthy();
  });

  it("판정된 적 없는 후보엔 그 줄을 아예 안 그린다(0으로 그리면 «판정 후 0건»과 구별 불가)", async () => {
    h.wisdom = card(ROW_BASE, REFLECTION_HEALTH, {
      ...CANDIDATE_STATUS_EMPTY,
      candidates_total: 1,
      bucket_counts: { ...CANDIDATE_STATUS_EMPTY.bucket_counts, global_pool: 1 },
      candidates: [{ ...REJECTED_ROW, status: "pending", judged_occurrences: null }],
    });
    renderPage();
    expect(await screen.findByText(/후보 현황\(승격 전\)/)).toBeTruthy();
    expect(screen.queryByText(/판정 시점/)).toBeNull();
    expect(screen.queryByText("재개방 대기")).toBeNull();
  });

  it("기준선이 0이어도 줄을 그린다 — truthy 검사로 퇴화하면 여기서 죽는다", async () => {
    // ★적대 리뷰 2R SUR-4가 드러낸 커버리지 갭. 출하 코드는 `judged_occurrences != null`로
    //   맞지만, 픽스처에 0인 행이 하나도 없어 이걸 `judged_occurrences &&`(truthy)로 바꿔도
    //   전건 초록이었다. 0은 «유효한 기준선»이다(판정 시점에 관찰이 0이었던 후보) —
    //   truthy 검사면 그 행의 재개방 상태가 화면에서 조용히 사라진다.
    //   n=52 P1과 같은 병이다: 재료가 흐를 때의 «모습»이 픽스처에 있어야 변이가 죽는다.
    h.wisdom = card(ROW_BASE, REFLECTION_HEALTH, {
      ...CANDIDATE_STATUS_EMPTY,
      candidates_total: 1,
      bucket_counts: { ...CANDIDATE_STATUS_EMPTY.bucket_counts, global_pool: 1 },
      candidates: [{
        ...REJECTED_ROW, occurrences: 6, judged_occurrences: 0,
        occurrences_since_judgment: 6, rejudge_count: 0, reopen_ready: true,
        prior_judgment_count: 0,
      }],
    });
    renderPage();
    expect(await screen.findByText(/판정 시점 0회 → 이후 \+6회 · 재심 0회/)).toBeTruthy();
    expect(screen.getByText("재개방 대기")).toBeTruthy();
  });

  it("재개방 문턱을 아직 못 넘었으면 배지를 안 단다(축적은 보이되 «대기»는 아니다)", async () => {
    h.wisdom = card(ROW_BASE, REFLECTION_HEALTH, {
      ...CANDIDATE_STATUS_EMPTY,
      candidates_total: 1,
      bucket_counts: { ...CANDIDATE_STATUS_EMPTY.bucket_counts, global_pool: 1 },
      candidates: [{
        ...REJECTED_ROW, occurrences: 47, judged_occurrences: 45,
        occurrences_since_judgment: 2, rejudge_count: 0, reopen_ready: false,
        prior_judgment_count: 0,
      }],
    });
    renderPage();
    expect(await screen.findByText(/판정 시점 45회 → 이후 \+2회 · 재심 0회/)).toBeTruthy();
    expect(screen.queryByText("재개방 대기")).toBeNull();
    expect(screen.queryByText(/이전 판정 .*건 보존/)).toBeNull();
  });
});

// ── ★D-NAO-251 §5 ②-b 상환: action 미상 후보가 «화면까지» 닿는다 ──
// 초판은 이 카운터를 백엔드 안쪽(_sibling_buckets·harvest totals)에만 뒀다. 합격기준은
// 「응답에 존재」였는데 응답엔 없었고 적대 리뷰 2R도 못 잡았다 — 완료 QA가 라이브로 반증했다.
// 「카운터는 생겼는데 화면까지 안 닿는다」의 재발이라, 이 describe는 «닿는 층»만 잠근다.
describe("지혜 성적표 — action 미상 후보(D-NAO-251 §5 ②-b)", () => {
  const NO_ACTION = {
    total: 6,
    by_status: { hidden: 6 },
    unresolved: 0,
    candidates: [
      { candidate_id: 45, signature: "g|SHOPPING|None|weekday|summer|normal|", status: "hidden", occurrences: 11 },
    ],
    label:
      "action 미상 후보 — 형제 매칭이 원리적으로 불가해 대조군을 못 만든다. " +
      "수확층이 더는 만들지 않고(skipped_no_action), 기존분은 hidden 처분됐다. " +
      "unresolved > 0이면 처분이 안 된 행이 남아 있다는 뜻이다.",
  };

  it("전건 처분됐으면 그렇게 표시한다", async () => {
    h.wisdom = card(ROW_BASE, REFLECTION_HEALTH, { ...CANDIDATE_STATUS_EMPTY, no_action: NO_ACTION });
    renderPage();
    expect(await screen.findByText(/action 미상 후보 · 6건/)).toBeTruthy();
    expect(screen.getByText("전건 처분됨")).toBeTruthy();
    expect(screen.getByText(/hidden 6건/)).toBeTruthy();
  });

  it("미처분이 남으면 눈에 띄게 표시한다 — 처분 누락이 침묵하면 안 된다", async () => {
    h.wisdom = card(ROW_BASE, REFLECTION_HEALTH, {
      ...CANDIDATE_STATUS_EMPTY,
      no_action: { ...NO_ACTION, by_status: { hidden: 5, pending: 1 }, unresolved: 1 },
    });
    renderPage();
    expect(await screen.findByText("미처분 1건")).toBeTruthy();
    expect(screen.queryByText("전건 처분됨")).toBeNull();
  });

  it("0건이어도 블록을 그린다 — 조용한 0과 죽은 카운터를 가른다", async () => {
    h.wisdom = card(ROW_BASE, REFLECTION_HEALTH, {
      ...CANDIDATE_STATUS_EMPTY,
      no_action: { total: 0, by_status: {}, unresolved: 0, candidates: [], label: NO_ACTION.label },
    });
    renderPage();
    expect(await screen.findByText(/action 미상 후보 · 0건/)).toBeTruthy();
    expect(screen.getByText("전건 처분됨")).toBeTruthy();
  });

  it("no_action이 없는 응답(구버전 백엔드)에서도 화면이 안 깨진다", async () => {
    h.wisdom = card(ROW_BASE, REFLECTION_HEALTH, { ...CANDIDATE_STATUS_EMPTY });
    renderPage();
    expect(await screen.findByText(/후보 현황\(승격 전\)/)).toBeTruthy();
    expect(screen.queryByText(/action 미상 후보/)).toBeNull();
  });
});
