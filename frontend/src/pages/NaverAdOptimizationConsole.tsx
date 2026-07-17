// NaverAdOptimizationConsole.tsx — 네이버 SA 광고 최적화 콘솔 (P2-S3b, track_naver-ad-optimization)
// 제안 카드(D-NAO-22) + 캠페인 optimizer/모드/공격성 다이얼 패널(D-NAO-2/13/22-②).
// 다이얼(공격성)은 라벨이 아니라 target_roas_override PUT으로 campaign_target_resolver
// 실계산에 그대로 반영된다(계획서 §4-Phase1 경고 — S3a HANDOFF 교훈).
// X1a T4 — 승인/반려/실행 버튼(반자동 개시). 자동 실행은 없다 — 모든 실쓰기는 사람의
// Confirm을 거친다(D-NAO-5). 현재 개방 액션은 하드코딩하지 않는다: 목록 API의 open_actions
// (백엔드 파생값)를 배너·Confirm 문안이 진실로 삼는다 — 개방 순서가 코드로 진행돼도
// 자동으로 따라온다(제외키워드→정지·재개→입찰→예산, D-NAO-16).
// X1a T5 — E2 위임 스위치(D-NAO-25): Ava agree+가드레일 통과 유형만 사람 승인 없이
// 자동 승인·실행되도록 콘솔에서 켜고 끈다. 스위치 행사자는 Jino뿐.
import { Fragment, useEffect, useRef, useState } from "react";
import {
  fetchNaverAdReport,
  fetchNaverAdProposals,
  fetchNaverCampaignSettings,
  putNaverCampaignSettings,
  fetchNaverAdDiagnosis,
  fetchNaverExpertReviews,
  fetchNaverExpertScorecard,
  updateNaverProposalStatus,
  executeNaverProposal,
  getNaverExpertDelegation,
  putNaverExpertDelegation,
  getNaverDashboardOverview,
  type NaverAdProposal,
  type NaverAdCampaignSettings,
  type NaverAdOptimizer,
  type NaverAdCampaignMode,
  type NaverExpertVerdict,
  type NaverExpertScorecard,
  type NaverExpertDelegationSettings,
  type NaverDashboardOverview,
} from "../lib/api";
import { isoKST, won, roasX, NO_DATA } from "../lib/format";
import { LayerNav } from "../components/ui";

function daysAgo(n: number): string {
  return isoKST(new Date(Date.now() - n * 86400000));
}
// T2 — 백엔드 last_evidence_at은 naive KST 문자열(다른 콘솔 코드도 동일 관례,
// 예: p.created_at?.slice(0,16) 직접 슬라이스 — Date 객체로 재해석하면 이중 시프트된다).
// 오늘이면 "HH:mm", 아니면 "M/D HH:mm".
function fmtEvidenceTime(iso: string | null): string {
  if (!iso) return NO_DATA;
  const datePart = iso.slice(0, 10);
  const timePart = iso.slice(11, 16);
  if (!timePart) return datePart;
  const today = isoKST(new Date());
  if (datePart === today) return timePart;
  const [, m, d] = datePart.split("-");
  return `${Number(m)}/${Number(d)} ${timePart}`;
}

// 실행 액션 → 한국어 라벨(배너의 "현재 개방" 표시용). 맵에 없는 액션은 원문 그대로 노출한다
// (숨기면 새로 개방된 게 안 보인다 — 정직 경계). 진실은 백엔드 open_actions.
const ACTION_LABEL_KO: Record<string, string> = {
  add_negative_keyword: "제외키워드",
  update_bid: "입찰가",
  set_user_lock: "정지·재개",
  update_budget: "예산",
};

function actionLabelKo(action: string): string {
  return ACTION_LABEL_KO[action] ?? action;
}

// 실행 Confirm 문안 — 실쓰기임을 명시(D-NAO-5). ★p.action(백엔드 파생값)으로 분기한다.
// 프론트가 proposal_type으로 액션을 재추론하지 않는다: 미지 액션(null 포함)은 폴백 일반
// 문구를 쓰므로, 새 액션이 개방돼도 "제외키워드"처럼 틀린 액션명을 사칭할 수 없다
// (하드코딩 액션명 재발 방지의 핵심). export = vitest 드리프트 가드 대상.
export function executeConfirmText(p: NaverAdProposal): string {
  const target = `대상: ${p.target_type} ${p.target_id}`;
  const group = `광고그룹: ${p.adgroup_id ?? NO_DATA}`;
  const campaign = `캠페인: ${p.campaign_id}`;
  switch (p.action) {
    case "add_negative_keyword":
      return (
        `제외키워드를 네이버에 실제 등록합니다.\n` +
        `검색어: "${p.target_id}"\n` +
        `${group}\n${campaign}\n\n` +
        `실행할까요? (실쓰기 — 되돌리려면 네이버 콘솔에서 삭제)`
      );
    case "update_bid": {
      const bid = p.target_bid == null
        ? "(값 없음 — 실행 시 서버가 거부)"
        : `${p.target_bid}원`;
      return (
        `입찰가를 변경합니다.\n` +
        `${target}\n` +
        `새 입찰가: ${bid}\n` +
        `${group}\n${campaign}\n\n` +
        `실행할까요? (실쓰기 — 되돌리려면 네이버 콘솔에서 되돌리기)`
      );
    }
    case "set_user_lock": {
      const verb = p.target_lock === true
        ? "광고를 정지합니다(userLock)"
        : p.target_lock === false
          ? "정지된 광고를 재개합니다"
          : "광고 정지/재개 상태를 변경합니다(값 없음 — 실행 시 서버가 거부)";
      return (
        `${verb}.\n` +
        `${target}\n` +
        `${group}\n${campaign}\n\n` +
        `실행할까요? (실쓰기 — 되돌리려면 네이버 콘솔에서 되돌리기)`
      );
    }
    case "update_budget": {
      const budget = p.target_budget == null
        ? "(값 없음 — 실행 시 서버가 거부)"
        : `${p.target_budget}원`;
      return (
        `캠페인 일예산을 변경합니다.\n` +
        `새 예산: ${budget}\n` +
        `${campaign}\n\n` +
        `실행할까요? (실쓰기 — 되돌리려면 네이버 콘솔에서 되돌리기)`
      );
    }
    default:
      // 미지 액션(null 포함) — 구체 액션명을 사칭하지 않는 일반 문구. 대상 필드만 나열한다.
      return (
        `이 제안(${p.proposal_type})을 네이버에 실제 집행합니다.\n` +
        `${target}\n` +
        `${group}\n${campaign}\n\n` +
        `실행할까요? (실쓰기 — 실행 전 대상과 값을 신중히 확인하세요)`
      );
  }
}

// D-NAO-53-b: 정보성 제안(p.informational)은 실행 대상이 없어 승인해도 no-op이고(harness가
// "정보성 제안 — 실행 대상 아님"으로 응답), 자동 만료(D+1~D+3)가 있어 방치가 정상이며, 소급
// 채점도 status를 읽지 않는다 — 승인/반려/실행/재승인 버튼은 실행형 카드와 틀을 공유하다 따라
// 나온 흔적이므로 렌더하지 않는다. ★백엔드 파생값(p.informational)만 쓰고 proposal_type
// 문자열로 재분류하지 않는다(이 콘솔의 드리프트 교훈 — proposalKindToInformational 상단 주석 참조).
// export = vitest 드리프트 가드 대상.
export function showsActionButtons(p: NaverAdProposal): boolean {
  return !p.informational;
}

// D-NAO-2 공격성 배수 (bep_calculator.AGG_MULT와 동일 값 — 프론트는 이 값으로 override를 계산할 뿐
// 최종 판정은 항상 백엔드 campaign_target_resolver가 override 컬럼을 읽어 수행한다).
const AGGRESSIVENESS_OPTIONS: { key: string; label: string; mult: number }[] = [
  { key: "safe", label: "안전 ×1.30", mult: 1.3 },
  { key: "standard", label: "표준 ×1.15", mult: 1.15 },
  { key: "aggressive", label: "공격 ×1.05", mult: 1.05 },
];

// T4 — 운영모드 라디오 카드(MOP 24b 균형운영/성장운영 카드 패턴). 코드에 기존 설명 문구가
// 없어 작업 지시에 명시된 문구를 그대로 사용(§4 T4-1).
const MODE_OPTIONS: { key: NaverAdCampaignMode; label: string; desc: string }[] = [
  { key: "growth", label: "성장", desc: "볼륨 성장 우선" },
  { key: "recovery", label: "회복", desc: "수익성 회복" },
  { key: "launch", label: "런칭", desc: "신규 진입 탐색" },
  { key: "defense", label: "방어", desc: "이익 방어" },
];

// T4 — MOP `13_budget_pacing` 커버리지 스택바 색 규약(ours=파랑) 재사용.
const STAGE_STATUS_META: Record<string, { dot: string; label: string }> = {
  ok: { dot: "bg-green-500", label: "정상" },
  stale: { dot: "bg-amber-500", label: "지연" },
  none: { dot: "bg-gray-300", label: "없음" },
};

const OPTIMIZER_OPTIONS: { key: NaverAdOptimizer; label: string }[] = [
  { key: "none", label: "없음(수동)" },
  { key: "ours", label: "우리" },
  { key: "mop", label: "MOP" },
];

const PROPOSAL_STATUS_TABS = [
  { key: "pending", label: "대기" },
  { key: "approved", label: "승인" },
  { key: "rejected", label: "반려" },
  { key: "expired", label: "만료" },
  { key: "failed", label: "실패" },
  { key: "executing", label: "실행중" },
];

// D-NAO-47: 제안 카드 유형 필터. 기본이 "실행형"인 이유 — 콘솔의 존재 이유는 "지금 결정할
// 것"을 먼저 보이는 것인데, 목록이 created_at DESC라 정보성 경보(trigger_pacing 등)가 실행형
// (bid_up 등)보다 훨씬 자주 생성돼 limit=100 안에서 실행형을 전부 파묻던 라이브 버그가 있었다
// (2026-07-17 prod: 정보성 다수가 실행형 소수를 밀어내 "결정할 제안 없음"이 잘못 렌더).
type ProposalKind = "actionable" | "informational" | "all";

const PROPOSAL_KIND_TABS: { key: ProposalKind; label: string }[] = [
  { key: "actionable", label: "실행형" },
  { key: "informational", label: "정보성" },
  { key: "all", label: "전부" },
];

// 유형 → 백엔드 informational 쿼리 파라미터(backend GET /proposals: true=정보성만 /
// false=실행형만 / 생략=전부). 분류의 단일 진실은 백엔드 INFORMATIONAL_PROPOSAL_TYPES이며
// 프론트는 유형 문자열로 재분류하지 않는다. export = vitest 매핑 드리프트 가드 대상.
export function proposalKindToInformational(kind: ProposalKind): boolean | undefined {
  switch (kind) {
    case "actionable":
      return false;
    case "informational":
      return true;
    case "all":
      return undefined;
  }
}

// D-NAO-47: 제안 유형 14종 전량. 백엔드 단일 진실 = proposal_writer.ALL_PROPOSAL_TYPES.
// ★기존엔 6종만 정의해 9종이 **영문 원문으로 렌더**됐고(라이브 확인: trigger_pacing·
//   account_brief가 영문 pill), 반대로 백엔드가 만들지 않는 'budget'·'new_setup'
//   **유령 라벨**을 갖고 있었다(스펙 §1-3).
// 백엔드에 유형을 추가하면 여기도 추가한다 — 백엔드
// test_all_proposal_types_constant_covers_every_emitted_type이 상수 쪽을 지킨다.
// export: 커맨드 센터 2층(NaverAdCommandCenter의 PendingPane)도 같은 라벨을 쓴다 —
// 단일 진실을 이 파일 하나로 유지해 두 화면 라벨이 조용히 갈라지는 걸 막는다.
export const PROPOSAL_TYPE_LABEL: Record<string, string> = {
  // 실행형
  bid_up: "입찰 인상",
  bid_down: "입찰 인하",
  growth_bid_up: "성장 입찰 인상",
  negative_keyword: "제외 키워드",
  pause: "정지",
  resume: "재개",
  budget_up: "예산 증액",
  budget_down: "예산 감액",
  budget_pre_exhaustion: "예산 소진 임박",
  // 정보성(informational=true)
  anomaly: "이상 감지",
  anomaly_freshness: "데이터 신선도 이상",
  account_brief: "계정 브리핑",
  trigger_pacing: "페이싱 경보",
  trigger_cpc_spike: "CPC 급등 경보",
  wisdom_promoted: "지혜 승격", // D-NAO-54 P3 — 운영 일기에서 승격된 판단원칙 보고(정보성)
};

// E1a T8 — 제안 카드 평결 배지(Ava 검토, 콘솔 배지용 요약은 백엔드 _serialize_expert_verdict_summary 참조)
const VERDICT_META: Record<NaverExpertVerdict, { icon: string; label: string; className: string }> = {
  agree: { icon: "✓", label: "동의", className: "bg-green-50 text-green-700" },
  partial: { icon: "⚠", label: "부분동의", className: "bg-amber-50 text-amber-700" },
  reject: { icon: "✗", label: "기각", className: "bg-red-50 text-red-700" },
  insufficient_evidence: { icon: "?", label: "판단보류", className: "bg-gray-100 text-gray-600" },
  commentary: { icon: "💬", label: "총평", className: "bg-blue-50 text-blue-700" },
};

interface CampaignRow {
  campaign_id: string;
  campaign_type: string;
  cost: number;
  roas_naver: number | null;
}

interface EditState {
  // ★optimizer 없음(D-NAO-48, codex[P1]): 이 패널은 관리주체를 바꾸지 않는다. 편집 상태로
  //   들고 있으면 누군가 다시 payload에 실어 1층 스위치의 확인창을 우회시키기 쉽다.
  mode: NaverAdCampaignMode | "";
  targetRoasOverride: string; // 입력 필드 원문(빈 문자열=해제)
}

function toEditState(s: NaverAdCampaignSettings | undefined): EditState {
  return {
    mode: s?.mode ?? "",
    targetRoasOverride: s?.target_roas_override != null ? String(s.target_roas_override) : "",
  };
}

export default function NaverAdOptimizationConsole() {
  // T2 — 엔진 파이프라인 5단계 + optimizer 커버리지(대시보드 미니 스프린트).
  const [dashboardOverview, setDashboardOverview] = useState<NaverDashboardOverview | null>(null);
  const [dashboardLoading, setDashboardLoading] = useState(false);
  const [dashboardError, setDashboardError] = useState<string | null>(null);

  const [proposals, setProposals] = useState<NaverAdProposal[]>([]);
  // null = 아직 미도착(배너는 "확인 중" 중립 표기). 도착하면 백엔드 open_actions를 그대로 든다.
  const [openActions, setOpenActions] = useState<string[] | null>(null);
  const [proposalStatus, setProposalStatus] = useState("pending");
  // 기본 "actionable"(실행형) — 콘솔은 결정할 것을 먼저 보인다(D-NAO-47 라이브 버그: 정보성이
  // 최신순으로 실행형을 파묻어 "결정할 제안 없음"이 잘못 렌더됐다).
  const [proposalKind, setProposalKind] = useState<ProposalKind>("actionable");
  const [proposalsLoading, setProposalsLoading] = useState(false);
  const [proposalsError, setProposalsError] = useState<string | null>(null);

  const [campaigns, setCampaigns] = useState<CampaignRow[]>([]);
  const [settingsMap, setSettingsMap] = useState<Record<string, NaverAdCampaignSettings>>({});
  const [edits, setEdits] = useState<Record<string, EditState>>({});
  const [accountBepRoas, setAccountBepRoas] = useState<number | null>(null);
  const [panelLoading, setPanelLoading] = useState(false);
  const [panelError, setPanelError] = useState<string | null>(null);
  const [savingIds, setSavingIds] = useState<Record<string, boolean>>({});
  const [savedId, setSavedId] = useState<string | null>(null);
  const proposalsReqSeq = useRef(0);

  const [avaCommentary, setAvaCommentary] = useState<string | null>(null);
  const [avaScorecard, setAvaScorecard] = useState<NaverExpertScorecard | null>(null);
  const [avaLoading, setAvaLoading] = useState(false);
  const [avaError, setAvaError] = useState<string | null>(null);

  // X1a T4 — 제안 카드 승인/반려/실행 액션 상태(카드 단위, in-flight 중복클릭 방지 + 결과 피드백).
  const [actionBusy, setActionBusy] = useState<Record<number, boolean>>({});
  const [actionMsg, setActionMsg] = useState<Record<number, string>>({});
  const [actionMsgIsError, setActionMsgIsError] = useState<Record<number, boolean>>({});

  // X1a T5 — E2 위임 스위치 상태(콘솔 패널, 기본 접힘).
  const [delegation, setDelegation] = useState<NaverExpertDelegationSettings | null>(null);
  const [delegationLoading, setDelegationLoading] = useState(false);
  const [delegationError, setDelegationError] = useState<string | null>(null);
  // 전역 busy(유형별 아님) — PUT이 전체 배열 치환이라 저장 중 다른 유형을 토글하면
  // 서로 덮어쓸 수 있음(codex 지적). 저장 중엔 패널 전체를 잠근다.
  const [delegationBusy, setDelegationBusy] = useState(false);
  const [delegationPanelOpen, setDelegationPanelOpen] = useState(false);

  async function loadDashboardOverview() {
    setDashboardLoading(true);
    setDashboardError(null);
    try {
      const data = await getNaverDashboardOverview();
      setDashboardOverview(data);
    } catch (e: any) {
      setDashboardError(e.message);
    } finally {
      setDashboardLoading(false);
    }
  }

  async function loadProposals() {
    const mySeq = ++proposalsReqSeq.current;
    setProposalsLoading(true);
    setProposalsError(null);
    try {
      const data = await fetchNaverAdProposals({
        status: proposalStatus,
        informational: proposalKindToInformational(proposalKind),
        limit: 100,
      });
      if (mySeq !== proposalsReqSeq.current) return; // stale 응답 무시(탭 빠르게 전환 시 레이스 방지)
      setProposals(data.rows);
      setOpenActions(data.open_actions);
    } catch (e: any) {
      if (mySeq !== proposalsReqSeq.current) return;
      setProposalsError(e.message);
    } finally {
      if (mySeq === proposalsReqSeq.current) setProposalsLoading(false);
    }
  }

  async function loadPanel() {
    setPanelLoading(true);
    setPanelError(null);
    try {
      const [report, settings, diagnosis] = await Promise.all([
        fetchNaverAdReport({ dateFrom: daysAgo(29), dateTo: daysAgo(0), grain: "campaign" }),
        fetchNaverCampaignSettings(),
        fetchNaverAdDiagnosis(),
      ]);
      const rows = (report.rows as any[]).map((r) => ({
        campaign_id: r.campaign_id,
        campaign_type: r.campaign_type,
        cost: r.cost,
        roas_naver: r.roas_naver,
      })) as CampaignRow[];
      rows.sort((a, b) => b.cost - a.cost);
      setCampaigns(rows);

      const map: Record<string, NaverAdCampaignSettings> = {};
      for (const s of settings.rows) map[s.campaign_id] = s;
      setSettingsMap(map);

      const nextEdits: Record<string, EditState> = {};
      for (const r of rows) nextEdits[r.campaign_id] = toEditState(map[r.campaign_id]);
      setEdits(nextEdits);

      setAccountBepRoas(diagnosis.account_bep_roas);
    } catch (e: any) {
      setPanelError(e.message);
    } finally {
      setPanelLoading(false);
    }
  }

  async function loadAva() {
    setAvaLoading(true);
    setAvaError(null);
    try {
      const [reviews, scorecard] = await Promise.all([
        fetchNaverExpertReviews({ asOf: daysAgo(0) }),
        fetchNaverExpertScorecard(),
      ]);
      const commentaryRow = reviews.rows.find((r) => r.proposal_id === null && r.verdict === "commentary");
      setAvaCommentary(commentaryRow?.reasoning ?? null);
      setAvaScorecard(scorecard);
    } catch (e: any) {
      setAvaError(e.message);
    } finally {
      setAvaLoading(false);
    }
  }

  async function loadDelegation() {
    setDelegationLoading(true);
    setDelegationError(null);
    try {
      const data = await getNaverExpertDelegation();
      setDelegation(data);
    } catch (e: any) {
      setDelegationError(e.message);
    } finally {
      setDelegationLoading(false);
    }
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { loadDashboardOverview(); }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { loadProposals(); }, [proposalStatus, proposalKind]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { loadPanel(); }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { loadAva(); }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { loadDelegation(); }, []);

  function updateEdit(campaignId: string, patch: Partial<EditState>) {
    setEdits((prev) => ({ ...prev, [campaignId]: { ...prev[campaignId], ...patch } }));
    setSavedId(null);
  }

  function applyAggressiveness(campaignId: string, mult: number) {
    if (accountBepRoas == null) return;
    const override = Math.round(accountBepRoas * mult * 10000) / 10000;
    updateEdit(campaignId, { targetRoasOverride: String(override) });
  }

  // T4 — 슬라이더 위치는 AGGRESSIVENESS_OPTIONS 3개 배수 중 현재 override에 가장 가까운
  // 인덱스로 파생(코드에 이미 있는 배수 3개만 사용 — 새 범위 발명 금지). 매칭 실패/미지정
  // 시 표준(idx 1)을 기본값으로 보여준다. 저장되는 값은 항상 숫자 input이 담당(정밀 보존).
  function sliderIndexForCampaign(campaignId: string): number {
    const e = edits[campaignId];
    if (!e || accountBepRoas == null || accountBepRoas <= 0) return 1;
    const trimmed = e.targetRoasOverride.trim();
    if (trimmed === "") return 1;
    const parsed = Number(trimmed);
    if (!Number.isFinite(parsed) || parsed <= 0) return 1;
    const mult = parsed / accountBepRoas;
    let bestIdx = 1;
    let bestDiff = Infinity;
    AGGRESSIVENESS_OPTIONS.forEach((opt, idx) => {
      const diff = Math.abs(opt.mult - mult);
      if (diff < bestDiff) { bestDiff = diff; bestIdx = idx; }
    });
    return bestIdx;
  }

  async function save(campaignId: string) {
    const e = edits[campaignId];
    if (!e) return;
    let override: number | null = null;
    if (e.targetRoasOverride.trim() !== "") {
      const parsed = Number(e.targetRoasOverride);
      if (!Number.isFinite(parsed) || parsed <= 0) {
        setPanelError(`목표 ROAS override는 0보다 큰 유효한 숫자여야 합니다: "${e.targetRoasOverride}"`);
        return;
      }
      override = parsed;
    }
    setSavingIds((prev) => ({ ...prev, [campaignId]: true }));
    setSavedId(null);
    try {
      const updated = await putNaverCampaignSettings({
        campaignId,
        // ★optimizer를 보내지 않는다(D-NAO-48, codex[P1]) — 백엔드가 생략 시 기존 값을
        //   보존한다. 보내면 stale 버퍼가 1층 스위치의 변경을 덮어써 쓰기 게이트가
        //   의도치 않게 재무장된다.
        mode: e.mode === "" ? null : e.mode,
        targetRoasOverride: override,
        memo: settingsMap[campaignId]?.memo ?? null, // 콘솔에 memo 편집 UI 없음 — 기존 값 보존(덮어쓰기 방지)
      });
      setSettingsMap((prev) => ({ ...prev, [campaignId]: updated }));
      setSavedId(campaignId);
    } catch (err: any) {
      setPanelError(err.message);
    } finally {
      setSavingIds((prev) => {
        const next = { ...prev };
        delete next[campaignId];
        return next;
      });
    }
  }

  // ── X1a T4: 제안 카드 승인/반려/실행 액션 ──
  function setActionResult(id: number, msg: string, isError: boolean) {
    setActionMsg((prev) => ({ ...prev, [id]: msg }));
    setActionMsgIsError((prev) => ({ ...prev, [id]: isError }));
  }

  function clearActionResult(id: number) {
    setActionMsg((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }

  async function runExecute(id: number) {
    setActionBusy((prev) => ({ ...prev, [id]: true }));
    try {
      const result = await executeNaverProposal(id);
      setActionResult(id, `✓ 등록 완료 (change_log #${result.change_log_id})`, false);
    } catch (e: any) {
      setActionResult(id, e.message, true);
    } finally {
      // codex P2: 목록 갱신이 끝난 뒤에 busy 해제 — 갱신 전에 풀면 짧은 이중클릭 창이 생긴다.
      await loadProposals();
      setActionBusy((prev) => ({ ...prev, [id]: false }));
    }
  }

  async function handleExecuteClick(p: NaverAdProposal) {
    if (!window.confirm(executeConfirmText(p))) return;
    await runExecute(p.id);
  }

  // 승인(pending→approved) + 재승인(failed→approved) 공용 — 승인 성공 후 executable이면
  // 즉시 실행을 Confirm으로 제안한다(취소 시 승인 상태로만 남기고 별도 안내 없음, 사양대로).
  // preConfirm 필수(codex P1-1): 승인/재승인 모두 Confirm을 먼저 통과해야 status API를
  // 호출한다. 즉시 실행 흐름에서 다이얼로그 2회가 되는 것은 의도된 안전장치(X1a 첫 실쓰기 개방).
  async function approveAndMaybeExecute(p: NaverAdProposal, preConfirm: string) {
    if (!window.confirm(preConfirm)) return;
    setActionBusy((prev) => ({ ...prev, [p.id]: true }));
    clearActionResult(p.id);
    let updated: NaverAdProposal;
    try {
      updated = await updateNaverProposalStatus(p.id, "approved");
    } catch (e: any) {
      setActionResult(p.id, e.message, true);
      await loadProposals(); // codex P2: 갱신 완료 후 busy 해제
      setActionBusy((prev) => ({ ...prev, [p.id]: false }));
      return;
    }
    if (updated.executable && window.confirm(executeConfirmText(updated))) {
      await runExecute(p.id); // 자체적으로 재조회 후 busy 해제
      return;
    }
    await loadProposals(); // codex P2: 갱신 완료 후 busy 해제
    setActionBusy((prev) => ({ ...prev, [p.id]: false }));
  }

  function handleApprove(p: NaverAdProposal) {
    return approveAndMaybeExecute(p, "이 제안을 승인할까요? (실행은 별도 Confirm)");
  }

  function handleReapprove(p: NaverAdProposal) {
    return approveAndMaybeExecute(p, "실패한 제안을 재승인해 재시도할까요?");
  }

  async function handleReject(p: NaverAdProposal) {
    if (!window.confirm("이 제안을 반려할까요?")) return;
    setActionBusy((prev) => ({ ...prev, [p.id]: true }));
    try {
      await updateNaverProposalStatus(p.id, "rejected");
      setActionResult(p.id, "반려되었습니다.", false);
    } catch (e: any) {
      setActionResult(p.id, e.message, true);
    } finally {
      // codex P2: 목록 갱신이 끝난 뒤에 busy 해제
      await loadProposals();
      setActionBusy((prev) => ({ ...prev, [p.id]: false }));
    }
  }

  // ── X1a T5: E2 위임 스위치 토글 ──
  // ON 전환은 Confirm 필수(자동 실행 개시 — D-NAO-5 취지 연장), OFF는 즉시(자율성 축소는
  // 마찰 불필요). 전체 배열 치환 PUT이므로 다른 이미 위임된 유형은 그대로 유지한다.
  async function toggleDelegation(proposalType: string, nextOn: boolean) {
    if (!delegation || delegationBusy) return;
    if (nextOn) {
      const label = PROPOSAL_TYPE_LABEL[proposalType] ?? proposalType;
      const confirmText =
        `"${label}" 유형을 Ava 위임 자동승인으로 전환합니다.\n\n` +
        `다음 08:05 크론부터, 이 유형의 제안 중 Ava가 동의(agree) 평결하고 가드레일을 통과한 ` +
        `건은 사람 승인 없이 자동으로 승인·실행됩니다.\n` +
        `그 외(부분동의·기각·판단보류·구조결함·미개방 액션)는 지금처럼 전부 사람 검토로 남습니다.\n\n` +
        `켤까요?`;
      if (!window.confirm(confirmText)) return;
    }
    const prev = delegation;
    const nextTypes = nextOn
      ? Array.from(new Set([...delegation.delegated_types, proposalType]))
      : delegation.delegated_types.filter((t) => t !== proposalType);
    setDelegationBusy(true);
    try {
      const updated = await putNaverExpertDelegation(nextTypes);
      setDelegation(updated);
    } catch (e: any) {
      alert(e.message);
      setDelegation(prev); // 실패 시 이전 상태로 복원
    } finally {
      setDelegationBusy(false);
    }
  }

  function renderProposalActions(p: NaverAdProposal, busy: boolean) {
    // D-NAO-53-b: 정보성 카드는 승인해도 no-op이고 자동 만료되므로 액션 버튼을 렌더하지 않는다.
    if (!showsActionButtons(p)) {
      return <span className="text-xs text-gray-300">자동 만료</span>;
    }
    const btnBase = "px-3 py-1.5 text-xs rounded border disabled:opacity-50 disabled:cursor-not-allowed";
    if (p.status === "pending") {
      return (
        <div className="flex gap-1.5">
          <button disabled={busy} onClick={() => handleApprove(p)}
            className={`${btnBase} border-blue-200 text-blue-600 hover:bg-blue-50`}>
            승인
          </button>
          <button disabled={busy} onClick={() => handleReject(p)}
            className={`${btnBase} border-gray-200 text-gray-500 hover:bg-gray-50`}>
            반려
          </button>
        </div>
      );
    }
    if (p.status === "approved") {
      const alreadyExecuted = p.executed_change_log_id != null;
      const execDisabled = busy || alreadyExecuted || !p.executable;
      const execTitle = alreadyExecuted ? "이미 실행됨" : (p.not_executable_reason ?? undefined);
      return (
        <div className="flex gap-1.5">
          <button disabled={execDisabled} title={execTitle} onClick={() => handleExecuteClick(p)}
            className={`${btnBase} border-blue-600 bg-blue-600 text-white hover:bg-blue-700 disabled:bg-gray-200 disabled:border-gray-200 disabled:text-gray-400`}>
            실행
          </button>
          {!alreadyExecuted && (
            <button disabled={busy} onClick={() => handleReject(p)}
              className={`${btnBase} border-gray-200 text-gray-500 hover:bg-gray-50`}>
              반려
            </button>
          )}
        </div>
      );
    }
    if (p.status === "failed") {
      return (
        <div className="flex gap-1.5">
          <button disabled={busy} onClick={() => handleReapprove(p)}
            className={`${btnBase} border-blue-200 text-blue-600 hover:bg-blue-50`}>
            재승인
          </button>
          <button disabled={busy} onClick={() => handleReject(p)}
            className={`${btnBase} border-gray-200 text-gray-500 hover:bg-gray-50`}>
            반려
          </button>
        </div>
      );
    }
    return null; // rejected/expired/executing — 버튼 없음(executing은 카드에 경고 배지만)
  }

  return (
    <div className="space-y-6">
      <LayerNav />
      {/* 엔진 파이프라인 5단계 + optimizer 커버리지(T1/T2, 대시보드 미니 스프린트) */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <h3 className="text-sm font-medium text-gray-700 mb-3">엔진 파이프라인</h3>
        {dashboardError && <div className="text-sm text-red-600 mb-2">{dashboardError}</div>}
        {dashboardLoading ? (
          <div className="text-sm text-gray-400 py-4 text-center">불러오는 중...</div>
        ) : dashboardOverview ? (
          <div className="flex items-stretch gap-1 overflow-x-auto">
            {dashboardOverview.engine_stages.map((s, i) => (
              <Fragment key={s.key}>
                <div className="flex-1 min-w-[130px] rounded border border-gray-200 bg-white p-2.5">
                  <div className="flex items-center gap-1.5">
                    <span
                      className={`inline-block h-2 w-2 shrink-0 rounded-full ${STAGE_STATUS_META[s.status]?.dot ?? "bg-gray-300"}`}
                      title={STAGE_STATUS_META[s.status]?.label ?? s.status}
                    />
                    <span className="text-xs font-medium text-gray-700">{s.name}</span>
                  </div>
                  <div className="text-[11px] text-gray-400 mt-1">{fmtEvidenceTime(s.last_evidence_at)}</div>
                  <div className="text-[11px] text-gray-500 mt-0.5 truncate" title={s.detail}>{s.detail}</div>
                </div>
                {i < dashboardOverview.engine_stages.length - 1 && (
                  <div className="flex items-center text-gray-300 text-sm px-0.5">→</div>
                )}
              </Fragment>
            ))}
          </div>
        ) : (
          <div className="text-sm text-gray-400 py-4 text-center">데이터 없음</div>
        )}

        {dashboardOverview && (() => {
          const cov = dashboardOverview.optimizer_coverage;
          const total = cov.total_cost;
          const oursPct = total > 0 ? (cov.ours_cost / total) * 100 : 0;
          const mopPct = total > 0 ? (cov.mop_cost / total) * 100 : 0;
          const nonePct = total > 0 ? (cov.none_cost / total) * 100 : 0;
          return (
            <div className="mt-4 pt-3 border-t border-gray-100">
              <div className="flex items-center justify-between gap-2 flex-wrap mb-1.5">
                <h4 className="text-xs font-medium text-gray-600">최적화 커버리지 (최근 {cov.window_days}일 광고비 기준)</h4>
                {total > 0 && (
                  <span className="text-xs text-gray-500">
                    ours {won(cov.ours_cost)} ({(cov.ours_ratio * 100).toFixed(1)}%)
                  </span>
                )}
              </div>
              {total === 0 ? (
                <div className="text-sm text-gray-400">데이터 없음</div>
              ) : (
                <div className="h-3 w-full rounded-full overflow-hidden flex bg-gray-100">
                  {oursPct > 0 && (
                    <div style={{ width: `${oursPct}%`, backgroundColor: "#2563eb" }}
                      title={`ours ${won(cov.ours_cost)} (${oursPct.toFixed(1)}%)`} />
                  )}
                  {mopPct > 0 && (
                    <div className="bg-gray-400" style={{ width: `${mopPct}%` }}
                      title={`mop ${won(cov.mop_cost)} (${mopPct.toFixed(1)}%)`} />
                  )}
                  {nonePct > 0 && (
                    <div className="bg-gray-200" style={{ width: `${nonePct}%` }}
                      title={`none ${won(cov.none_cost)} (${nonePct.toFixed(1)}%)`} />
                  )}
                </div>
              )}
            </div>
          );
        })()}
      </div>

      <div className="bg-amber-50 border border-amber-200 text-amber-700 text-xs rounded-lg p-3">
        반자동 모드 — 자동 실행은 없습니다. 승인 후 Confirm을 거친 제안만 실제 집행됩니다(현재
        개방:{" "}
        {openActions === null
          ? "확인 중"
          : openActions.length === 0
            ? "없음"
            : openActions.map(actionLabelKo).join(", ")}
        ). 모드·공격성 다이얼은 저장 즉시 실제 목표 ROAS 계산에 반영됩니다.
      </div>

      {/* Ava 위임 자동승인 설정 (X1a T5, D-NAO-25) — 기본 접힘 */}
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <button type="button" onClick={() => setDelegationPanelOpen((v) => !v)}
          className="w-full p-3 flex items-center justify-between gap-2 text-left hover:bg-gray-50">
          <span className="text-sm font-medium text-gray-700 flex items-center gap-1.5">
            Ava 위임 자동승인 (E2)
            {delegation && delegation.delegated_types.length > 0 && (
              <span className="px-1.5 py-0.5 text-[11px] rounded font-medium bg-purple-50 text-purple-700">
                {delegation.delegated_types.length}개 위임 중
              </span>
            )}
          </span>
          <span className="text-xs text-gray-400">{delegationPanelOpen ? "접기 ▲" : "펼치기 ▼"}</span>
        </button>
        {delegationPanelOpen && (
          <div className="px-4 pb-4 border-t border-gray-100">
            <p className="text-xs text-gray-500 mt-3 mb-3">
              위임 ON = Ava가 동의(agree) 평결하고 가드레일을 통과한 제안만 08:05 크론에서 사람
              승인 없이 자동 승인·실행됩니다. 그 외(부분동의·기각·판단보류·구조결함·미개방
              액션)는 전부 사람 검토로 남습니다. 스위치는 Jino만 조작합니다.
            </p>
            {delegationError && <div className="text-sm text-red-600 mb-2">{delegationError}</div>}
            {delegationLoading ? (
              <div className="text-sm text-gray-400 py-2">불러오는 중...</div>
            ) : !delegation || delegation.delegable_types.length === 0 ? (
              <div className="text-sm text-gray-400 py-2">현재 위임 가능한 유형 없음</div>
            ) : (
              <div className="flex flex-col gap-2">
                {delegation.delegable_types.map((t) => {
                  const on = delegation.delegated_types.includes(t);
                  return (
                    <label key={t} className="flex items-center gap-2 text-sm text-gray-700">
                      <input type="checkbox" checked={on} disabled={delegationBusy}
                        onChange={(ev) => toggleDelegation(t, ev.target.checked)}
                        className="h-4 w-4 rounded border-gray-300 text-blue-600 disabled:opacity-50" />
                      {PROPOSAL_TYPE_LABEL[t] ?? t}
                    </label>
                  );
                })}
                {delegationBusy && <span className="text-xs text-gray-400">저장 중...</span>}
              </div>
            )}
          </div>
        )}
      </div>

      {/* 섹션 1: 제안 카드 */}
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <div className="p-4 border-b border-gray-200 flex items-center justify-between flex-wrap gap-2">
          <h3 className="text-sm font-medium text-gray-700">제안 카드</h3>
          <div className="flex items-center gap-3 flex-wrap">
            {/* 유형 필터(D-NAO-47) — 상태 탭과 동일 스타일 관례. 기본 '실행형'. */}
            <div className="flex gap-1">
              {PROPOSAL_KIND_TABS.map((t) => (
                <button key={t.key} onClick={() => setProposalKind(t.key)}
                  className={`px-2.5 py-1 text-xs rounded ${proposalKind === t.key ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}>
                  {t.label}
                </button>
              ))}
            </div>
            <div className="h-4 w-px bg-gray-200" aria-hidden />
            <div className="flex gap-1">
              {PROPOSAL_STATUS_TABS.map((t) => (
                <button key={t.key} onClick={() => setProposalStatus(t.key)}
                  className={`px-2.5 py-1 text-xs rounded ${proposalStatus === t.key ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}>
                  {t.label}
                </button>
              ))}
            </div>
          </div>
        </div>
        {proposalsError && <div className="p-3 text-sm text-red-600 bg-red-50">{proposalsError}</div>}
        <div className="divide-y divide-gray-100">
          {proposalsLoading ? (
            <div className="p-8 text-center text-gray-400 text-sm">불러오는 중...</div>
          ) : proposals.length === 0 ? (
            // 정직성(D-NAO-47): 실행형 0건이면 정보성이 다른 탭에 있을 수 있음을 알린다.
            // 응답 total은 현재 필터에 종속돼 다른 유형 건수를 못 주므로(백엔드 변경 금지),
            // 정확한 N 대신 "정보성 탭에서 확인"으로 안내한다.
            <div className="p-8 text-center text-gray-400 text-sm">
              {proposalKind === "actionable"
                ? "지금 결정할 실행형 제안이 없습니다 (정보성 경보는 '정보성' 탭에서 확인)"
                : proposalKind === "informational"
                  ? "정보성 제안이 없습니다"
                  : "해당 상태의 제안이 없습니다"}
            </div>
          ) : (
            proposals.map((p) => (
              <div key={p.id} className="p-4 flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="px-2 py-0.5 text-xs rounded bg-blue-50 text-blue-700 font-medium">
                      {PROPOSAL_TYPE_LABEL[p.proposal_type] ?? p.proposal_type}
                    </span>
                    {/* D-NAO-47: "입찰 인상" 카드가 *얼마로* 올리는지 화면에 없던 결함(스펙 §1-6).
                        현재 pending 실행대상 5건이 전부 bid_up이라 바로 체감된다. */}
                    {p.target_bid != null && (
                      <span className="text-xs text-gray-600 tabular-nums">→ {won(p.target_bid)}</span>
                    )}
                    {p.target_budget != null && (
                      <span className="text-xs text-gray-600 tabular-nums">→ 일예산 {won(p.target_budget)}</span>
                    )}
                    <span className="text-xs text-gray-400">{p.campaign_id}</span>
                    <span className="text-xs text-gray-400">{p.target_type}:{p.target_id}</span>
                    <span className="text-xs text-gray-300">{p.created_at?.slice(0, 16).replace("T", " ")}</span>
                    {p.expert_verdict && (
                      <span
                        className={`px-1.5 py-0.5 text-xs rounded font-medium ${VERDICT_META[p.expert_verdict.verdict].className}`}
                        title={`Ava 검토(${p.expert_verdict.as_of}): ${VERDICT_META[p.expert_verdict.verdict].label}${p.expert_verdict.confidence != null ? ` · 확신도 ${(p.expert_verdict.confidence * 100).toFixed(0)}%` : ""}`}
                      >
                        {VERDICT_META[p.expert_verdict.verdict].icon} {VERDICT_META[p.expert_verdict.verdict].label}
                      </span>
                    )}
                    {p.approval_source === "delegation" && (
                      <span className="px-1.5 py-0.5 text-xs rounded font-medium bg-purple-50 text-purple-700"
                        title="Ava가 동의 평결 + 가드레일을 통과해 사람 승인 없이 자동 승인·실행됨(E2, D-NAO-25)">
                        🤖 Ava 위임 자동승인
                      </span>
                    )}
                    {p.status === "executing" && (
                      <span className="px-1.5 py-0.5 text-xs rounded font-medium bg-amber-50 text-amber-700"
                        title="클레임 후 완료되지 않고 잔존한 실행 — 쓰기 결과를 네이버 콘솔에서 직접 확인하세요.">
                        ⚠ 실행중 잔존 — 쓰기 결과 불확실, 수동 조사 필요
                      </span>
                    )}
                    {p.executed_change_log_id != null && (
                      <span className="px-1.5 py-0.5 text-xs rounded font-medium bg-green-50 text-green-700">
                        ✓ 실행됨 #{p.executed_change_log_id}
                      </span>
                    )}
                  </div>
                  {p.rationale && <p className="text-sm text-gray-700 mt-1.5">{p.rationale}</p>}
                  {p.expected_effect && <p className="text-xs text-gray-500 mt-1">예상 효과: {p.expected_effect}</p>}
                  {actionMsg[p.id] && (
                    <p className={`text-xs mt-1.5 ${actionMsgIsError[p.id] ? "text-red-600" : "text-green-600"}`}>
                      {actionMsg[p.id]}
                    </p>
                  )}
                </div>
                <div className="text-right shrink-0">
                  {renderProposalActions(p, !!actionBusy[p.id])}
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* 섹션 2: 캠페인 모드·공격성 패널. 관리주체(optimizer)는 읽기 전용 —
          변경은 1층 커맨드 센터 스위치가 유일 경로다(D-NAO-48, codex[P1]). */}
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <div className="p-4 border-b border-gray-200 flex items-center justify-between flex-wrap gap-2">
          <h3 className="text-sm font-medium text-gray-700">캠페인 모드 · 공격성</h3>
          <span className="text-xs text-gray-400">
            계정 BEP ROAS {accountBepRoas != null ? roasX(accountBepRoas) : NO_DATA} (공격성 다이얼 기준값)
          </span>
        </div>
        {panelError && <div className="p-3 text-sm text-red-600 bg-red-50">{panelError}</div>}
        <div className="overflow-x-auto">
          {panelLoading ? (
            <div className="p-8 text-center text-gray-400 text-sm">불러오는 중...</div>
          ) : campaigns.length === 0 ? (
            <div className="p-8 text-center text-gray-400 text-sm">캠페인 데이터가 없습니다(최근 30일)</div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200">
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-left">캠페인</th>
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-right">광고비(30일)</th>
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-right">ROAS</th>
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-left">관리 주체</th>
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-left">모드</th>
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-left">공격성 → 목표 ROAS</th>
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-left"></th>
                </tr>
              </thead>
              <tbody>
                {campaigns.map((c) => {
                  const e = edits[c.campaign_id] ?? toEditState(settingsMap[c.campaign_id]);
                  return (
                    <tr key={c.campaign_id} className="hover:bg-gray-50 align-top">
                      <td className="px-4 py-2 text-sm border-b border-gray-100">
                        <div className="max-w-[220px] truncate" title={c.campaign_id}>{c.campaign_id}</div>
                        <div className="text-xs text-gray-400">{c.campaign_type}</div>
                      </td>
                      <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{won(c.cost)}</td>
                      <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{roasX(c.roas_naver)}</td>
                      <td className="px-4 py-2 text-sm border-b border-gray-100">
                        {/* ★D-NAO-48(codex[P1] 2026-07-17): 여기서 optimizer를 **바꾸지 않는다** — 읽기 전용.
                            원래 select였는데, 그러면 1층 스위치의 확인창(원본 MOP가 자동으로 꺼지지
                            않는다는 D-NAO-13 경고)을 우회해 캠페인이 라이브 쓰기 대상이 된다. 게다가
                            이 패널의 stale 편집 버퍼가 나중에 커밋되면 스위치로 끈 걸 'ours'로 되돌려
                            **아무도 의도하지 않은 채 쓰기가 재무장**된다(레이스).
                            → optimizer의 쓰기 경로는 1층 스위치 하나. 이 패널은 모드·공격성 전용이고
                            저장 payload에서도 optimizer를 뺐다(백엔드가 생략 시 기존 값 보존). */}
                        <span className="text-xs text-gray-600">
                          {OPTIMIZER_OPTIONS.find((o) => o.key === (settingsMap[c.campaign_id]?.optimizer ?? "none"))?.label}
                        </span>
                        <a href="/naver-ad" className="ml-1.5 text-xs text-blue-600 hover:underline">변경 →</a>
                      </td>
                      <td className="px-4 py-2 text-sm border-b border-gray-100">
                        {/* T4 — 라디오 카드 2×2(MOP 24b 운영모드 카드 패턴). 선택된 모드를
                            다시 클릭하면 해제(미지정)된다 — 기존 select의 "(미지정)" 옵션과 동등. */}
                        <div className="grid grid-cols-2 gap-1 w-[168px]">
                          {MODE_OPTIONS.map((m) => {
                            const selected = e.mode === m.key;
                            return (
                              <button key={m.key} type="button"
                                onClick={() => updateEdit(c.campaign_id, { mode: selected ? "" : m.key })}
                                title={m.desc}
                                className={`text-left px-1.5 py-1 rounded border leading-tight ${selected ? "border-blue-500 bg-blue-50" : "border-gray-200 hover:bg-gray-50"}`}>
                                <div className={`text-[11px] font-medium ${selected ? "text-blue-700" : "text-gray-700"}`}>{m.label}</div>
                                <div className="text-[10px] text-gray-400 truncate">{m.desc}</div>
                              </button>
                            );
                          })}
                        </div>
                      </td>
                      <td className="px-4 py-2 text-sm border-b border-gray-100">
                        {/* T4 — 공격성 슬라이더(MOP 24b 이지모드 슬라이더 패턴, 눈금 라벨
                            보수적↔공격적). 3단 배수는 AGGRESSIVENESS_OPTIONS 그대로(신규 범위
                            발명 없음) — 슬라이더는 빠른 프리셋 선택, 숫자 input이 정밀 조정 담당. */}
                        <div className="mb-1.5 w-40">
                          <input type="range" min={0} max={AGGRESSIVENESS_OPTIONS.length - 1} step={1}
                            value={sliderIndexForCampaign(c.campaign_id)}
                            disabled={accountBepRoas == null}
                            onChange={(ev) => applyAggressiveness(c.campaign_id, AGGRESSIVENESS_OPTIONS[Number(ev.target.value)].mult)}
                            title={accountBepRoas == null ? "계정 BEP ROAS 산출 불가" : undefined}
                            className="w-40 accent-blue-600 disabled:opacity-40" />
                          <div className="flex justify-between text-[10px] text-gray-400 mt-0.5">
                            <span>보수적</span>
                            <span>공격적</span>
                          </div>
                          {accountBepRoas != null && (
                            <div className="text-[10px] text-gray-500 mt-0.5">
                              {AGGRESSIVENESS_OPTIONS[sliderIndexForCampaign(c.campaign_id)].label}
                            </div>
                          )}
                        </div>
                        <input type="number" step="0.0001" value={e.targetRoasOverride}
                          onChange={(ev) => updateEdit(c.campaign_id, { targetRoasOverride: ev.target.value })}
                          placeholder="override 없음(계정 기본값 사용)"
                          className="text-xs border border-gray-300 rounded px-1.5 py-1 w-40" />
                      </td>
                      <td className="px-4 py-2 text-sm border-b border-gray-100">
                        <button onClick={() => save(c.campaign_id)} disabled={!!savingIds[c.campaign_id]}
                          className="px-2.5 py-1 text-xs rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50">
                          {savingIds[c.campaign_id] ? "저장 중..." : "저장"}
                        </button>
                        {savedId === c.campaign_id && <div className="text-[11px] text-green-600 mt-1">저장됨</div>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* 섹션 3: Ava의 검토(E1a T8) */}
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <div className="p-4 border-b border-gray-200 flex items-center justify-between flex-wrap gap-2">
          <h3 className="text-sm font-medium text-gray-700">Ava의 검토</h3>
          {avaScorecard && avaScorecard.sample_n > 0 && (
            <span className="text-xs text-gray-400">
              {/* codex review 발견(P2): 정직 라벨은 정확도%보다 먼저 노출 — 표본 부족 상태를
                  숫자 뒤에 묻어두면 정확도가 먼저 눈에 들어와 헤드라인 금지 취지가 무색해진다. */}
              {avaScorecard.label ? `${avaScorecard.label} · ` : ""}
              표본 {avaScorecard.sample_n}건
              {avaScorecard.accuracy != null && !avaScorecard.label ? ` · 예측 적중률 ${(avaScorecard.accuracy * 100).toFixed(0)}%` : ""}
            </span>
          )}
        </div>
        {avaError && <div className="p-3 text-sm text-red-600 bg-red-50">{avaError}</div>}
        <div className="p-4">
          {avaLoading ? (
            <div className="text-center text-gray-400 text-sm py-4">불러오는 중...</div>
          ) : avaCommentary ? (
            <p className="text-sm text-gray-700 whitespace-pre-wrap">{avaCommentary}</p>
          ) : (
            <p className="text-sm text-gray-400">오늘 아직 검토가 없습니다(08:05 크론 대기 또는 pending 제안 없음).</p>
          )}
          {avaScorecard && avaScorecard.sample_n === 0 && (
            <p className="text-xs text-gray-400 mt-2">{avaScorecard.label}</p>
          )}
        </div>
      </div>
    </div>
  );
}
