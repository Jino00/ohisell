// NaverAdProposalForm.tsx — D-NAO-283 (계약 P2-ⓒ · H2) 사람 발의 폼.
//
// ★이 화면이 하는 일은 «제안 1건을 만드는 것»뿐이다. 승인도 실행도 여기서 안 한다 —
//   만들어진 카드는 바로 아래 「제안 카드」 목록에 pending으로 뜨고, 기존 승인→실행
//   경로(D-NAO-5 Confirm·D-NAO-13 하드체크·가드레일 봉투)를 그대로 탄다.
//
// ★유형 목록·엔진 전용 사유를 이 파일에 «적지 않는다». 전부 백엔드
//   GET /proposals/proposable-types 응답에서 온다 — 게이트가 바뀌면 화면이 저절로 따라온다.
//   (교훈 #380: 두 층 각각은 테스트돼 있는데 «둘을 잇는 한 줄»만 아무도 안 지켰다.)
import { useEffect, useState } from "react";

import {
  createNaverProposal,
  fetchNaverProposableTypes,
  type NaverAdProposal,
  type NaverProposableTypes,
} from "../lib/api";

// 유형별로 사람이 채워야 하는 칸이 다르다. 이 매핑은 «표시»만 정한다 —
// 실제 필수 여부 판정은 백엔드(real_write_blocker)가 하고, 틀리면 서버 사유가 그대로 뜬다.
const TARGET_TYPE_HINT: Record<string, string> = {
  negative_keyword: "search_term",
  search_term_exclude: "search_term",
  bid_up: "keyword",
  bid_down: "keyword",
};

export interface NaverAdProposalFormProps {
  /** 발의 성공 시 부모가 제안 목록을 다시 읽도록 알린다. */
  onCreated?: (proposal: NaverAdProposal) => void;
}

export default function NaverAdProposalForm({ onCreated }: NaverAdProposalFormProps) {
  const [types, setTypes] = useState<NaverProposableTypes | null>(null);
  const [typesError, setTypesError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const [proposalType, setProposalType] = useState("");
  const [targetType, setTargetType] = useState("");
  const [targetId, setTargetId] = useState("");
  const [campaignId, setCampaignId] = useState("");
  const [adgroupId, setAdgroupId] = useState("");
  const [targetBid, setTargetBid] = useState("");
  const [rationale, setRationale] = useState("");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [okMessage, setOkMessage] = useState<string | null>(null);

  useEffect(() => {
    fetchNaverProposableTypes()
      .then((t) => {
        setTypes(t);
        if (t.proposable.length > 0) {
          const first = t.proposable[0].proposal_type;
          setProposalType(first);
          setTargetType(TARGET_TYPE_HINT[first] ?? "");
        }
      })
      .catch((e: unknown) => setTypesError(e instanceof Error ? e.message : String(e)));
  }, []);

  function onTypeChange(next: string) {
    setProposalType(next);
    setTargetType(TARGET_TYPE_HINT[next] ?? "");
    setError(null);
  }

  const selected = types?.proposable.find((p) => p.proposal_type === proposalType);
  const isBid = selected?.direction != null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setOkMessage(null);
    try {
      const created = await createNaverProposal({
        proposalType,
        targetType,
        targetId: targetId.trim(),
        campaignId: campaignId.trim(),
        adgroupId: adgroupId.trim() || null,
        rationale: rationale.trim(),
        targetBid: isBid && targetBid.trim() !== "" ? Number(targetBid) : null,
      });
      setOkMessage(
        `제안 #${created.id} 발의됨 — 아래 목록에 «대기»로 떴습니다. 승인·실행은 별도 Confirm입니다.`,
      );
      setTargetId("");
      setAdgroupId("");
      setTargetBid("");
      setRationale("");
      onCreated?.(created);
    } catch (err: unknown) {
      // ★서버 사유를 그대로 보여준다 — 프론트가 사유를 지어내면 실행기와 갈라진다.
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
      <div className="p-4 border-b border-gray-200 flex items-center justify-between flex-wrap gap-2">
        <div>
          <h3 className="text-sm font-medium text-gray-700">발의</h3>
          <p className="text-xs text-gray-500 mt-0.5">
            사람이 직접 제안을 만듭니다. 발의는 승인이 아닙니다 — 만들어진 카드는 아래 목록에
            «대기»로 뜨고, 승인·실행은 지금까지와 똑같이 별도 Confirm을 거칩니다.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="px-3 py-1.5 text-xs rounded bg-gray-100 text-gray-700 hover:bg-gray-200"
        >
          {open ? "접기" : "발의하기"}
        </button>
      </div>

      {typesError && <div className="p-3 text-sm text-red-600 bg-red-50">{typesError}</div>}

      {open && types && (
        <div className="p-4 space-y-4">
          <form onSubmit={submit} className="space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <label className="text-xs text-gray-600">
                유형
                <select
                  aria-label="발의 유형"
                  value={proposalType}
                  onChange={(e) => onTypeChange(e.target.value)}
                  className="mt-1 w-full border border-gray-300 rounded px-2 py-1.5 text-sm"
                >
                  {types.proposable.map((t) => (
                    <option key={t.proposal_type} value={t.proposal_type}>
                      {t.proposal_type}
                      {t.direction ? ` (${t.direction === "up" ? "올림" : "내림"})` : ""}
                    </option>
                  ))}
                </select>
              </label>

              <label className="text-xs text-gray-600">
                대상 종류(target_type)
                <input
                  aria-label="대상 종류"
                  value={targetType}
                  onChange={(e) => setTargetType(e.target.value)}
                  className="mt-1 w-full border border-gray-300 rounded px-2 py-1.5 text-sm"
                />
              </label>

              <label className="text-xs text-gray-600">
                대상 ID / 검색어
                <input
                  aria-label="대상 ID"
                  value={targetId}
                  onChange={(e) => setTargetId(e.target.value)}
                  className="mt-1 w-full border border-gray-300 rounded px-2 py-1.5 text-sm"
                />
              </label>

              <label className="text-xs text-gray-600">
                캠페인 ID
                <input
                  aria-label="캠페인 ID"
                  value={campaignId}
                  onChange={(e) => setCampaignId(e.target.value)}
                  className="mt-1 w-full border border-gray-300 rounded px-2 py-1.5 text-sm"
                />
              </label>

              <label className="text-xs text-gray-600">
                광고그룹 ID
                <input
                  aria-label="광고그룹 ID"
                  value={adgroupId}
                  onChange={(e) => setAdgroupId(e.target.value)}
                  className="mt-1 w-full border border-gray-300 rounded px-2 py-1.5 text-sm"
                />
              </label>

              {isBid && (
                <label className="text-xs text-gray-600">
                  목표 입찰가(원)
                  <input
                    aria-label="목표 입찰가"
                    type="number"
                    value={targetBid}
                    onChange={(e) => setTargetBid(e.target.value)}
                    className="mt-1 w-full border border-gray-300 rounded px-2 py-1.5 text-sm"
                  />
                </label>
              )}
            </div>

            <label className="text-xs text-gray-600 block">
              근거 (필수)
              <textarea
                aria-label="근거"
                required
                rows={2}
                value={rationale}
                onChange={(e) => setRationale(e.target.value)}
                placeholder="무엇을 보고 이 판단을 했는지 — 숫자와 함께"
                className="mt-1 w-full border border-gray-300 rounded px-2 py-1.5 text-sm"
              />
            </label>

            {error && <div className="text-sm text-red-600 bg-red-50 rounded p-2">{error}</div>}
            {okMessage && (
              <div className="text-sm text-green-700 bg-green-50 rounded p-2">{okMessage}</div>
            )}

            <button
              type="submit"
              disabled={busy}
              className="px-3 py-1.5 text-xs rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {busy ? "발의 중..." : "발의"}
            </button>
          </form>

          {/* ★조용한 실패 금지(계약 §3 P2 ★v9): 없는 유형을 «없다»로 감추지 않고,
              엔진 전용임을 사유와 함께 보인다. 사유 문구는 백엔드가 낸다. */}
          {types.engine_only.length > 0 && (
            <details className="text-xs text-gray-500 border-t border-gray-100 pt-3">
              <summary className="cursor-pointer">
                엔진만 발의하는 유형 {types.engine_only.length}종 — 왜 여기 없나
              </summary>
              <ul className="mt-2 space-y-1">
                {types.engine_only.map((t) => (
                  <li key={t.proposal_type}>
                    <span className="font-mono text-gray-600">{t.proposal_type}</span> — {t.reason}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
