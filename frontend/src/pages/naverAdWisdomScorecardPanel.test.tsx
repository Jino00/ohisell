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
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => {
  const pending = () => new Promise<never>(() => {});
  return {
    pending,
    wisdom: null as unknown,
    wisdomFails: false,
    avaFails: false,
  };
});

vi.mock("../lib/api", () => ({
  fetchNaverAdReport: () => h.pending(),
  fetchNaverAdProposals: () => h.pending(),
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
  updateNaverProposalStatus: () => h.pending(),
  executeNaverProposal: () => h.pending(),
  getNaverExpertDelegation: () => h.pending(),
  putNaverExpertDelegation: () => h.pending(),
  getNaverDashboardOverview: () => h.pending(),
  getNaverGuardrailParams: () => h.pending(),
  putNaverGuardrailParams: () => h.pending(),
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
});

afterEach(() => {
  cleanup();
  h.wisdom = null;
  h.wisdomFails = false;
  h.avaFails = false;
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
