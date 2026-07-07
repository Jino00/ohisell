// NaverAdOptimizationConsole.tsx — 네이버 SA 광고 최적화 콘솔 (P2-S3b, track_naver-ad-optimization)
// 제안 카드(D-NAO-22) + 캠페인 optimizer/모드/공격성 다이얼 패널(D-NAO-2/13/22-②).
// 다이얼(공격성)은 라벨이 아니라 target_roas_override PUT으로 campaign_target_resolver
// 실계산에 그대로 반영된다(계획서 §4-Phase1 경고 — S3a HANDOFF 교훈).
import { useEffect, useRef, useState } from "react";
import {
  fetchNaverAdReport,
  fetchNaverAdProposals,
  fetchNaverCampaignSettings,
  putNaverCampaignSettings,
  fetchNaverAdDiagnosis,
  type NaverAdProposal,
  type NaverAdCampaignSettings,
  type NaverAdOptimizer,
  type NaverAdCampaignMode,
} from "../lib/api";

function isoKST(d: Date): string {
  const kst = new Date(d.toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
  return `${kst.getFullYear()}-${String(kst.getMonth() + 1).padStart(2, "0")}-${String(kst.getDate()).padStart(2, "0")}`;
}
function daysAgo(n: number): string {
  return isoKST(new Date(Date.now() - n * 86400000));
}
function fmt(n: number | null | undefined): string {
  if (n == null) return "-";
  return n.toLocaleString("ko-KR");
}
function won(n: number | null | undefined): string {
  if (n == null) return "-";
  return `${fmt(n)}원`;
}
function roasX(n: number | null | undefined): string {
  if (n == null) return "-";
  return `${n.toFixed(2)}배`;
}

// D-NAO-2 공격성 배수 (bep_calculator.AGG_MULT와 동일 값 — 프론트는 이 값으로 override를 계산할 뿐
// 최종 판정은 항상 백엔드 campaign_target_resolver가 override 컬럼을 읽어 수행한다).
const AGGRESSIVENESS_OPTIONS: { key: string; label: string; mult: number }[] = [
  { key: "safe", label: "안전 ×1.30", mult: 1.3 },
  { key: "standard", label: "표준 ×1.15", mult: 1.15 },
  { key: "aggressive", label: "공격 ×1.05", mult: 1.05 },
];

const MODE_OPTIONS: { key: NaverAdCampaignMode; label: string }[] = [
  { key: "growth", label: "성장" },
  { key: "recovery", label: "회복" },
  { key: "launch", label: "런칭" },
  { key: "defense", label: "방어" },
];

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
];

const PROPOSAL_TYPE_LABEL: Record<string, string> = {
  bid_up: "입찰 인상",
  bid_down: "입찰 인하",
  negative_keyword: "제외 키워드",
  budget: "예산 조정",
  growth_bid_up: "성장 입찰 인상",
  new_setup: "신규 세팅",
};

interface CampaignRow {
  campaign_id: string;
  campaign_type: string;
  cost: number;
  roas_naver: number | null;
}

interface EditState {
  optimizer: NaverAdOptimizer;
  mode: NaverAdCampaignMode | "";
  targetRoasOverride: string; // 입력 필드 원문(빈 문자열=해제)
}

function toEditState(s: NaverAdCampaignSettings | undefined): EditState {
  return {
    optimizer: s?.optimizer ?? "none",
    mode: s?.mode ?? "",
    targetRoasOverride: s?.target_roas_override != null ? String(s.target_roas_override) : "",
  };
}

export default function NaverAdOptimizationConsole() {
  const [proposals, setProposals] = useState<NaverAdProposal[]>([]);
  const [proposalStatus, setProposalStatus] = useState("pending");
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

  async function loadProposals() {
    const mySeq = ++proposalsReqSeq.current;
    setProposalsLoading(true);
    setProposalsError(null);
    try {
      const data = await fetchNaverAdProposals({ status: proposalStatus, limit: 100 });
      if (mySeq !== proposalsReqSeq.current) return; // stale 응답 무시(탭 빠르게 전환 시 레이스 방지)
      setProposals(data.rows);
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

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { loadProposals(); }, [proposalStatus]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { loadPanel(); }, []);

  function updateEdit(campaignId: string, patch: Partial<EditState>) {
    setEdits((prev) => ({ ...prev, [campaignId]: { ...prev[campaignId], ...patch } }));
    setSavedId(null);
  }

  function applyAggressiveness(campaignId: string, mult: number) {
    if (accountBepRoas == null) return;
    const override = Math.round(accountBepRoas * mult * 10000) / 10000;
    updateEdit(campaignId, { targetRoasOverride: String(override) });
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
        optimizer: e.optimizer,
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

  return (
    <div className="space-y-6">
      <div className="bg-amber-50 border border-amber-200 text-amber-700 text-xs rounded-lg p-3">
        관찰 모드 — 제안은 자동 실행되지 않습니다. optimizer/모드/공격성 다이얼은 저장 즉시
        실제 목표 ROAS 계산(campaign_target_resolver)에 반영됩니다(라벨이 아님).
      </div>

      {/* 섹션 1: 제안 카드 */}
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <div className="p-4 border-b border-gray-200 flex items-center justify-between flex-wrap gap-2">
          <h3 className="text-sm font-medium text-gray-700">제안 카드</h3>
          <div className="flex gap-1">
            {PROPOSAL_STATUS_TABS.map((t) => (
              <button key={t.key} onClick={() => setProposalStatus(t.key)}
                className={`px-2.5 py-1 text-xs rounded ${proposalStatus === t.key ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}>
                {t.label}
              </button>
            ))}
          </div>
        </div>
        {proposalsError && <div className="p-3 text-sm text-red-600 bg-red-50">{proposalsError}</div>}
        <div className="divide-y divide-gray-100">
          {proposalsLoading ? (
            <div className="p-8 text-center text-gray-400 text-sm">불러오는 중...</div>
          ) : proposals.length === 0 ? (
            <div className="p-8 text-center text-gray-400 text-sm">해당 상태의 제안이 없습니다</div>
          ) : (
            proposals.map((p) => (
              <div key={p.id} className="p-4 flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="px-2 py-0.5 text-xs rounded bg-blue-50 text-blue-700 font-medium">
                      {PROPOSAL_TYPE_LABEL[p.proposal_type] ?? p.proposal_type}
                    </span>
                    <span className="text-xs text-gray-400">{p.campaign_id}</span>
                    <span className="text-xs text-gray-400">{p.target_type}:{p.target_id}</span>
                    <span className="text-xs text-gray-300">{p.created_at?.slice(0, 16).replace("T", " ")}</span>
                  </div>
                  {p.rationale && <p className="text-sm text-gray-700 mt-1.5">{p.rationale}</p>}
                  {p.expected_effect && <p className="text-xs text-gray-500 mt-1">예상 효과: {p.expected_effect}</p>}
                </div>
                <div className="text-right shrink-0">
                  <button disabled title="관찰 모드 — 자동 실행 비활성화"
                    className="px-3 py-1.5 text-xs rounded border border-gray-200 text-gray-300 cursor-not-allowed">
                    실행
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* 섹션 2: 캠페인 optimizer/모드/공격성 패널 */}
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <div className="p-4 border-b border-gray-200 flex items-center justify-between flex-wrap gap-2">
          <h3 className="text-sm font-medium text-gray-700">캠페인 관리 주체 · 모드 · 공격성</h3>
          <span className="text-xs text-gray-400">
            계정 BEP ROAS {accountBepRoas != null ? roasX(accountBepRoas) : "-"} (공격성 다이얼 기준값)
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
                        <select value={e.optimizer} onChange={(ev) => updateEdit(c.campaign_id, { optimizer: ev.target.value as NaverAdOptimizer })}
                          className="text-xs border border-gray-300 rounded px-1.5 py-1">
                          {OPTIMIZER_OPTIONS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                        </select>
                      </td>
                      <td className="px-4 py-2 text-sm border-b border-gray-100">
                        <select value={e.mode} onChange={(ev) => updateEdit(c.campaign_id, { mode: ev.target.value as NaverAdCampaignMode | "" })}
                          className="text-xs border border-gray-300 rounded px-1.5 py-1">
                          <option value="">(미지정)</option>
                          {MODE_OPTIONS.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
                        </select>
                      </td>
                      <td className="px-4 py-2 text-sm border-b border-gray-100">
                        <div className="flex gap-1 flex-wrap mb-1">
                          {AGGRESSIVENESS_OPTIONS.map((a) => (
                            <button key={a.key} disabled={accountBepRoas == null}
                              onClick={() => applyAggressiveness(c.campaign_id, a.mult)}
                              title={accountBepRoas == null ? "계정 BEP ROAS 산출 불가" : `override = ${accountBepRoas} × ${a.mult}`}
                              className="px-1.5 py-0.5 text-[11px] rounded border border-blue-200 text-blue-600 hover:bg-blue-50 disabled:opacity-40 disabled:cursor-not-allowed">
                              {a.label}
                            </button>
                          ))}
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
    </div>
  );
}
