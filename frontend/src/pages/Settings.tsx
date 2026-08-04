// Settings.tsx — 채널 연동 상태 + 동기화 관리 페이지
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchApi } from "../lib/api";

interface ChannelStatus {
  channel_id: number;
  channel_name: string;
  code: string;
  platform: string;
  api_type: string;
  api_configured: boolean;
  oauth_status: string | null;
  oauth_expires_at: string | null;
  refresh_token_expires_at: string | null;
  last_sync_at: string | null;
  last_sync_status: string | null;
  last_sync_records: number;
}

interface Cafe24AuthUrl {
  auth_url: string;
  mall_id: string;
}

interface SyncResult {
  channel_id: number;
  channel_name: string;
  status: string;
  new_orders: number;
  updated_orders: number;
  errors: string[];
}

function relativeTime(isoStr: string | null): string {
  if (!isoStr) return "-";
  const diff = Date.now() - new Date(isoStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "방금 전";
  if (mins < 60) return `${mins}분 전`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}시간 전`;
  const days = Math.floor(hrs / 24);
  return `${days}일 전`;
}

function statusBadge(ch: ChannelStatus) {
  if (ch.api_type === "excel") {
    return <span className="px-2 py-0.5 text-xs rounded-full bg-gray-100 text-gray-600">엑셀 업로드</span>;
  }
  if (ch.platform === "cafe24") {
    if (ch.oauth_status === "connected") {
      return <span className="px-2 py-0.5 text-xs rounded-full bg-green-100 text-green-700">연동됨</span>;
    }
    if (ch.oauth_status === "expired") {
      return <span className="px-2 py-0.5 text-xs rounded-full bg-red-100 text-red-700">만료됨</span>;
    }
    return <span className="px-2 py-0.5 text-xs rounded-full bg-yellow-100 text-yellow-700">인증 필요</span>;
  }
  if (ch.api_configured) {
    return <span className="px-2 py-0.5 text-xs rounded-full bg-green-100 text-green-700">API 키 설정됨</span>;
  }
  return <span className="px-2 py-0.5 text-xs rounded-full bg-red-100 text-red-700">미설정</span>;
}

function platformIcon(platform: string) {
  switch (platform) {
    case "coupang": return "🟠";
    case "naver": return "🟢";
    case "cafe24": return "🔵";
    default: return "⚪";
  }
}

interface GfaUploadResult {
  inserted: number;
  skipped: number;
  total_spend: number;
  date_from: string;
  date_to: string;
  dates: string[];
  recalculation_triggered: boolean;
}

interface GfaStatus {
  has_data: boolean;
  date_from: string | null;
  date_to: string | null;
  days: number;
  total_spend: number;
}

interface CoupangAdUploadResult {
  vendor_id: string;
  inserted: number;
  skipped: number;
  total_spend: number;
  date_from: string;
  date_to: string;
  channel_summary: Record<string, number>;
  recalculation_triggered: boolean;
}

interface CoupangAdStatusItem {
  source: string;
  channel_name: string;
  date_from: string;
  date_to: string;
  days: number;
  total_spend: number;
}

interface CoupangAdStatus {
  has_data: boolean;
  items: CoupangAdStatusItem[];
}

interface ManualRevenueRecord {
  id: number;
  channel_id: number;
  channel_name: string;
  revenue_date: string;
  gross_revenue: string;
  memo: string | null;
  created_at: string;
  updated_at: string;
}

interface MonthlyFixedCostRecord {
  id: number;
  channel_id: number;
  channel_name: string;
  year_month: string;
  item: string;
  amount: string;
  memo: string | null;
  created_at: string;
  updated_at: string;
}

// 백엔드 FIXED_COST_ITEMS와 같은 순서·같은 문자열. 자유 입력이 아닌 이유는
// 오타 하나로 같은 항목이 둘로 갈라져 월별 추이가 끊기기 때문이다.
const FIXED_COST_ITEMS = ["입고비", "보관료", "항공도선료", "합포장비"] as const;

export default function Settings() {
  const [channels, setChannels] = useState<ChannelStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncingId, setSyncingId] = useState<number | null>(null);
  const [syncResults, setSyncResults] = useState<Record<number, SyncResult>>({});
  const [syncAllLoading, setSyncAllLoading] = useState(false);

  // GFA 업로드 상태
  const [gfaUploading, setGfaUploading] = useState(false);
  const [gfaResult, setGfaResult] = useState<GfaUploadResult | null>(null);
  const [gfaError, setGfaError] = useState<string | null>(null);
  const [gfaStatus, setGfaStatus] = useState<GfaStatus | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const gfaFileRef = useRef<HTMLInputElement>(null);

  // 수동 매출 입력 상태
  const [rocketRecords, setRocketRecords] = useState<ManualRevenueRecord[]>([]);
  const [rocketForm, setRocketForm] = useState(() => {
    const d = new Date();
    const localDate = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    return { date: localDate, amount: "", memo: "" };
  });
  const [rocketSaving, setRocketSaving] = useState(false);
  const [rocketError, setRocketError] = useState<string | null>(null);

  // 월 고정비 입력 상태 (3PL 정산서 — 네이버 채널 전용)
  const [fixedCostRecords, setFixedCostRecords] = useState<MonthlyFixedCostRecord[]>([]);
  const [fixedCostMonth, setFixedCostMonth] = useState(() => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  });
  const [fixedCostAmounts, setFixedCostAmounts] = useState<Record<string, string>>({});
  const [fixedCostSaving, setFixedCostSaving] = useState(false);
  const [fixedCostError, setFixedCostError] = useState<string | null>(null);

  // 쿠팡 광고비 업로드 상태
  const [coupangAdUploading, setCoupangAdUploading] = useState(false);
  const [coupangAdResult, setCoupangAdResult] = useState<CoupangAdUploadResult | null>(null);
  const [coupangAdError, setCoupangAdError] = useState<string | null>(null);
  const [coupangAdStatus, setCoupangAdStatus] = useState<CoupangAdStatus | null>(null);
  const [isCoupangDragOver, setIsCoupangDragOver] = useState(false);
  const coupangFileRef = useRef<HTMLInputElement>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const data = await fetchApi<ChannelStatus[]>("/api/channels/connection-status");
      setChannels(data);
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchGfaStatus = useCallback(async () => {
    try {
      const data = await fetchApi<GfaStatus>("/api/ad-costs/gfa/status");
      setGfaStatus(data);
    } catch { /* silent */ }
  }, []);

  const fetchCoupangAdStatus = useCallback(async () => {
    try {
      const data = await fetchApi<CoupangAdStatus>("/api/ad-costs/coupang/status");
      setCoupangAdStatus(data);
    } catch { /* silent */ }
  }, []);

  const fetchRocketRecords = useCallback(async () => {
    const rocketCh = channels.find((c) => c.code === "COUPANG_ROCKET");
    if (!rocketCh) return;
    try {
      const data = await fetchApi<ManualRevenueRecord[]>(
        `/api/manual-revenue?channel_id=${rocketCh.channel_id}`
      );
      setRocketRecords(data);
    } catch { /* silent */ }
  }, [channels]);

  useEffect(() => {
    fetchStatus();
    fetchGfaStatus();
    fetchCoupangAdStatus();
  }, [fetchStatus, fetchGfaStatus, fetchCoupangAdStatus]);

  const naverChannel = channels.find((c) => c.code === "NAVER");

  const fetchFixedCostRecords = useCallback(async () => {
    if (!naverChannel) return;
    try {
      const data = await fetchApi<MonthlyFixedCostRecord[]>(
        `/api/monthly-fixed-cost?channel_id=${naverChannel.channel_id}`
      );
      setFixedCostRecords(data);
    } catch { /* silent */ }
  }, [naverChannel]);

  useEffect(() => {
    if (channels.length > 0) fetchRocketRecords();
  }, [channels, fetchRocketRecords]);

  useEffect(() => {
    if (channels.length > 0) fetchFixedCostRecords();
  }, [channels, fetchFixedCostRecords]);

  // 입력한 달의 기존 값을 폼에 채운다 — 덮어쓰기 전에 지금 값이 뭔지 보여야 한다
  useEffect(() => {
    const existing: Record<string, string> = {};
    for (const r of fixedCostRecords) {
      if (r.year_month === fixedCostMonth) existing[r.item] = String(Number(r.amount));
    }
    setFixedCostAmounts(existing);
  }, [fixedCostMonth, fixedCostRecords]);

  const handleFixedCostSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!naverChannel) return;
    const entries = FIXED_COST_ITEMS.map((item) => [item, fixedCostAmounts[item]] as const)
      .filter(([, v]) => v !== undefined && v !== "");
    if (entries.length === 0) {
      setFixedCostError("최소 한 항목은 입력해 주세요.");
      return;
    }
    if (entries.some(([, v]) => isNaN(Number(v)) || Number(v) < 0)) {
      setFixedCostError("금액은 0 이상의 숫자여야 합니다.");
      return;
    }
    setFixedCostSaving(true);
    setFixedCostError(null);
    try {
      for (const [item, value] of entries) {
        await fetchApi("/api/monthly-fixed-cost", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            channel_id: naverChannel.channel_id,
            year_month: fixedCostMonth,
            item,
            amount: Number(value),
          }),
        });
      }
      fetchFixedCostRecords();
    } catch {
      setFixedCostError("저장 실패. 다시 시도해 주세요.");
    } finally {
      setFixedCostSaving(false);
    }
  };

  const handleFixedCostDelete = async (id: number) => {
    if (!confirm("삭제하시겠습니까?")) return;
    try {
      await fetchApi(`/api/monthly-fixed-cost/${id}`, { method: "DELETE" });
      fetchFixedCostRecords();
    } catch { /* silent */ }
  };

  const handleRocketSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const rocketCh = channels.find((c) => c.code === "COUPANG_ROCKET");
    if (!rocketCh) return;
    const amount = parseFloat(rocketForm.amount);
    if (isNaN(amount) || amount <= 0) {
      setRocketError("유효한 매출액을 입력해 주세요.");
      return;
    }
    setRocketSaving(true);
    setRocketError(null);
    try {
      await fetchApi("/api/manual-revenue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          channel_id: rocketCh.channel_id,
          revenue_date: rocketForm.date,
          gross_revenue: amount,
          memo: rocketForm.memo || null,
        }),
      });
      setRocketForm((f) => ({ ...f, amount: "", memo: "" }));
      fetchRocketRecords();
    } catch {
      setRocketError("저장 실패. 다시 시도해 주세요.");
    } finally {
      setRocketSaving(false);
    }
  };

  const handleRocketDelete = async (id: number) => {
    if (!confirm("삭제하시겠습니까?")) return;
    try {
      await fetchApi(`/api/manual-revenue/${id}`, { method: "DELETE" });
      fetchRocketRecords();
    } catch { /* silent */ }
  };

  const handleSync = async (channelId: number) => {
    setSyncingId(channelId);
    try {
      const result = await fetchApi<SyncResult>(`/api/sync/channel/${channelId}`, {
        method: "POST",
      });
      setSyncResults((prev) => ({ ...prev, [channelId]: result }));
      fetchStatus();
    } catch {
      /* silent */
    } finally {
      setSyncingId(null);
    }
  };

  const handleSyncAll = async () => {
    setSyncAllLoading(true);
    try {
      const results = await fetchApi<SyncResult[]>("/api/sync/all", { method: "POST" });
      const map: Record<number, SyncResult> = {};
      for (const r of results) map[r.channel_id] = r;
      setSyncResults(map);
      fetchStatus();
    } catch {
      /* silent */
    } finally {
      setSyncAllLoading(false);
    }
  };

  const handleCafe24Auth = async () => {
    try {
      const data = await fetchApi<Cafe24AuthUrl>("/api/oauth/cafe24/auth-url");
      if (data.auth_url) {
        window.open(data.auth_url, "_blank");
      }
    } catch {
      /* silent */
    }
  };

  const handleCafe24Disconnect = async () => {
    if (!confirm("cafe24 연동을 해제하시겠습니까?")) return;
    try {
      await fetchApi("/api/oauth/cafe24/disconnect", { method: "POST" });
      fetchStatus();
    } catch {
      /* silent */
    }
  };

  const uploadGfaFile = async (file: File) => {
    if (!file.name.endsWith(".csv")) {
      setGfaError("CSV 파일만 업로드 가능합니다.");
      return;
    }
    setGfaUploading(true);
    setGfaResult(null);
    setGfaError(null);

    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetch("/api/ad-costs/gfa/upload", {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "업로드 실패");
      setGfaResult(data as GfaUploadResult);
      fetchGfaStatus();
    } catch (err) {
      setGfaError(err instanceof Error ? err.message : "업로드 중 오류 발생");
    } finally {
      setGfaUploading(false);
      if (gfaFileRef.current) gfaFileRef.current.value = "";
    }
  };

  const handleGfaUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) uploadGfaFile(file);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) uploadGfaFile(file);
  };

  const uploadCoupangAdFile = async (file: File) => {
    if (!file.name.endsWith(".xlsx")) {
      setCoupangAdError("xlsx 파일만 업로드 가능합니다.");
      return;
    }
    setCoupangAdUploading(true);
    setCoupangAdResult(null);
    setCoupangAdError(null);
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await fetch("/api/ad-costs/coupang/upload", { method: "POST", body: formData });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "업로드 실패");
      setCoupangAdResult(data as CoupangAdUploadResult);
      fetchCoupangAdStatus();
    } catch (err) {
      setCoupangAdError(err instanceof Error ? err.message : "업로드 중 오류 발생");
    } finally {
      setCoupangAdUploading(false);
      if (coupangFileRef.current) coupangFileRef.current.value = "";
    }
  };

  const handleCoupangAdDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsCoupangDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) uploadCoupangAdFile(file);
  };

  if (loading) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-bold text-gray-900">설정</h1>
        <div className="animate-pulse space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-20 bg-gray-200 rounded-lg" />
          ))}
        </div>
      </div>
    );
  }

  const apiChannels = channels.filter((c) => c.api_type !== "excel");
  const excelChannels = channels.filter((c) => c.api_type === "excel");

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-900">설정</h1>
          <p className="text-sm text-gray-500 mt-1">채널 연동 상태 및 동기화 관리</p>
        </div>
        <button
          onClick={handleSyncAll}
          disabled={syncAllLoading}
          className="px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-50"
        >
          {syncAllLoading ? "동기화 중..." : "전체 동기화"}
        </button>
      </div>

      {/* API 연동 채널 */}
      <div>
        <h2 className="text-sm font-semibold text-gray-700 mb-3">API 연동 채널</h2>
        <div className="space-y-3">
          {apiChannels.map((ch) => {
            const result = syncResults[ch.channel_id];
            return (
              <div key={ch.channel_id} className="bg-white border border-gray-200 rounded-lg p-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <span className="text-lg">{platformIcon(ch.platform)}</span>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-gray-900">{ch.channel_name}</span>
                        {statusBadge(ch)}
                      </div>
                      <div className="text-xs text-gray-500 mt-1 flex items-center gap-3">
                        <span>코드: {ch.code}</span>
                        <span>API: {ch.api_type}</span>
                        {ch.last_sync_at && (
                          <span>
                            마지막 동기화: {relativeTime(ch.last_sync_at)}
                            {ch.last_sync_status === "success" ? " ✓" : " ✗"}
                            {ch.last_sync_records > 0 && ` (${ch.last_sync_records}건)`}
                          </span>
                        )}
                        {!ch.last_sync_at && <span className="text-gray-400">동기화 기록 없음</span>}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    {/* cafe24 OAuth 버튼 */}
                    {ch.platform === "cafe24" && ch.oauth_status !== "connected" && (
                      <button
                        onClick={handleCafe24Auth}
                        className="px-3 py-1.5 text-xs bg-blue-50 text-blue-700 rounded-md hover:bg-blue-100"
                      >
                        OAuth 인증
                      </button>
                    )}
                    {ch.platform === "cafe24" && ch.oauth_status === "connected" && (
                      <button
                        onClick={handleCafe24Disconnect}
                        className="px-3 py-1.5 text-xs bg-gray-50 text-gray-500 rounded-md hover:bg-gray-100"
                      >
                        연동 해제
                      </button>
                    )}

                    {/* 동기화 버튼 */}
                    <button
                      onClick={() => handleSync(ch.channel_id)}
                      disabled={syncingId === ch.channel_id || !ch.api_configured}
                      className="px-3 py-1.5 text-xs bg-gray-100 text-gray-700 rounded-md hover:bg-gray-200 disabled:opacity-40"
                    >
                      {syncingId === ch.channel_id ? "동기화 중..." : "동기화"}
                    </button>
                  </div>
                </div>

                {/* cafe24 토큰 만료 정보 */}
                {ch.platform === "cafe24" && ch.oauth_status === "connected" && ch.refresh_token_expires_at && (
                  <div className="mt-2 text-xs text-gray-400">
                    Refresh Token 만료: {new Date(ch.refresh_token_expires_at).toLocaleDateString("ko-KR")}
                  </div>
                )}

                {/* 동기화 결과 */}
                {result && (
                  <div className={`mt-2 text-xs px-3 py-2 rounded ${
                    result.status === "success" ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"
                  }`}>
                    {result.status === "success"
                      ? `신규 ${result.new_orders}건, 업데이트 ${result.updated_orders}건`
                      : `오류: ${result.errors.join(", ")}`
                    }
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* 엑셀 업로드 채널 */}
      {excelChannels.length > 0 && (
        <div>
          <h2 className="text-sm font-semibold text-gray-700 mb-3">엑셀 업로드 채널</h2>
          <div className="space-y-3">
            {excelChannels.map((ch) => (
              <div key={ch.channel_id} className="bg-white border border-gray-200 rounded-lg p-4">
                <div className="flex items-center gap-3">
                  <span className="text-lg">{platformIcon(ch.platform)}</span>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-gray-900">{ch.channel_name}</span>
                      {statusBadge(ch)}
                    </div>
                    <div className="text-xs text-gray-500 mt-1">
                      코드: {ch.code} — 엑셀 파일로 데이터 업로드
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* GFA(ADVoost) 광고비 업로드 */}
      <div>
        <h2 className="text-sm font-semibold text-gray-700 mb-3">광고비 수동 업로드</h2>
        <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-3">
          {/* 헤더 */}
          <div className="flex items-center gap-2">
            <span className="text-lg">🟢</span>
            <span className="font-medium text-gray-900">GFA · ADVoost 쇼핑</span>
            <span className="px-2 py-0.5 text-xs rounded-full bg-gray-100 text-gray-600">CSV 업로드</span>
          </div>

          {/* 드래그앤드롭 존 */}
          <div
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => !gfaUploading && gfaFileRef.current?.click()}
            className={`
              relative flex flex-col items-center justify-center gap-2
              border-2 border-dashed rounded-lg px-6 py-8 cursor-pointer
              transition-colors duration-150 select-none
              ${gfaUploading
                ? "border-gray-200 bg-gray-50 cursor-not-allowed"
                : isDragOver
                  ? "border-green-400 bg-green-50"
                  : "border-gray-200 bg-gray-50 hover:border-green-300 hover:bg-green-50"
              }
            `}
          >
            <input
              ref={gfaFileRef}
              type="file"
              accept=".csv"
              className="hidden"
              onChange={handleGfaUpload}
              disabled={gfaUploading}
            />
            {gfaUploading ? (
              <>
                <svg className="w-8 h-8 text-gray-300 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
                <p className="text-sm text-gray-500">CSV 파싱 중...</p>
              </>
            ) : (
              <>
                <svg className={`w-8 h-8 transition-colors ${isDragOver ? "text-green-400" : "text-gray-300"}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
                </svg>
                <p className={`text-sm font-medium transition-colors ${isDragOver ? "text-green-600" : "text-gray-500"}`}>
                  {isDragOver ? "여기에 놓으세요" : "CSV 파일을 드래그하거나 클릭해서 업로드"}
                </p>
                <p className="text-xs text-gray-400">
                  cafe24 GFA 콘솔 → 보고서 → CSV 다운로드
                  <span className="mx-1">·</span>
                  theohi11_광고비 보고서_YYYYMMDD_YYYYMMDD.csv
                </p>
              </>
            )}
          </div>

          {/* 현재 적재 현황 */}
          {gfaStatus && (
            <div className={`text-xs px-3 py-2 rounded border ${
              gfaStatus.has_data
                ? "bg-blue-50 border-blue-100 text-blue-700"
                : "bg-gray-50 border-gray-100 text-gray-500"
            }`}>
              {gfaStatus.has_data ? (
                <>
                  <span className="font-medium">현재 적재된 GFA 데이터:</span>{" "}
                  {gfaStatus.date_from} ~ {gfaStatus.date_to}
                  <span className="mx-1 text-blue-400">·</span>
                  {gfaStatus.days}일치
                  <span className="mx-1 text-blue-400">·</span>
                  총 {gfaStatus.total_spend.toLocaleString("ko-KR")}원
                </>
              ) : (
                "아직 적재된 GFA 데이터가 없습니다."
              )}
            </div>
          )}

          {/* 업로드 성공 결과 */}
          {!gfaUploading && gfaResult && (
            <div className="text-xs px-3 py-2 rounded bg-green-50 border border-green-100 text-green-700 space-y-1">
              <div className="font-medium">
                저장 완료 — {gfaResult.inserted}일치 ({gfaResult.date_from} ~ {gfaResult.date_to})
              </div>
              <div className="flex items-center gap-3 text-green-600">
                <span>총 광고비: <span className="font-medium">{gfaResult.total_spend.toLocaleString("ko-KR")}원</span></span>
                {gfaResult.skipped > 0 && <span className="text-yellow-600">건너뜀: {gfaResult.skipped}행</span>}
                {gfaResult.recalculation_triggered && (
                  <span className="px-1.5 py-0.5 rounded bg-green-100 text-green-700">이익 자동 재계산됨</span>
                )}
              </div>
            </div>
          )}

          {/* 에러 */}
          {!gfaUploading && gfaError && (
            <div className="text-xs px-3 py-2 rounded bg-red-50 border border-red-100 text-red-700">
              오류: {gfaError}
            </div>
          )}
        </div>
      </div>

      {/* 쿠팡 광고비 업로드 */}
      <div>
        <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-3">
          {/* 헤더 */}
          <div className="flex items-center gap-2">
            <span className="text-lg">🟠</span>
            <span className="font-medium text-gray-900">쿠팡 광고비</span>
            <span className="px-2 py-0.5 text-xs rounded-full bg-gray-100 text-gray-600">XLSX 업로드</span>
          </div>

          {/* 드래그앤드롭 존 */}
          <div
            onDragOver={(e) => { e.preventDefault(); setIsCoupangDragOver(true); }}
            onDragLeave={(e) => { e.preventDefault(); setIsCoupangDragOver(false); }}
            onDrop={handleCoupangAdDrop}
            onClick={() => !coupangAdUploading && coupangFileRef.current?.click()}
            className={`
              relative flex flex-col items-center justify-center gap-2
              border-2 border-dashed rounded-lg px-6 py-8 cursor-pointer
              transition-colors duration-150 select-none
              ${coupangAdUploading
                ? "border-gray-200 bg-gray-50 cursor-not-allowed"
                : isCoupangDragOver
                  ? "border-orange-400 bg-orange-50"
                  : "border-gray-200 bg-gray-50 hover:border-orange-300 hover:bg-orange-50"
              }
            `}
          >
            <input
              ref={coupangFileRef}
              type="file"
              accept=".xlsx"
              className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadCoupangAdFile(f); }}
              disabled={coupangAdUploading}
            />
            {coupangAdUploading ? (
              <>
                <svg className="w-8 h-8 text-gray-300 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
                <p className="text-sm text-gray-500">처리 중...</p>
              </>
            ) : (
              <>
                <svg className={`w-8 h-8 transition-colors ${isCoupangDragOver ? "text-orange-400" : "text-gray-300"}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
                </svg>
                <p className={`text-sm font-medium transition-colors ${isCoupangDragOver ? "text-orange-600" : "text-gray-500"}`}>
                  {isCoupangDragOver ? "여기에 놓으세요" : "XLSX 파일을 드래그하거나 클릭해서 업로드"}
                </p>
                <p className="text-xs text-gray-400">
                  쿠팡 Wing 광고센터 → 보고서 → 일별 광고그룹 성과 → XLSX 다운로드
                  <span className="mx-1">·</span>
                  {"{vendor_id}_pa_daily_adGroup_YYYYMMDD_YYYYMMDD.xlsx"}
                </p>
              </>
            )}
          </div>

          {/* 현재 적재 현황 */}
          {coupangAdStatus?.has_data && (
            <div className="text-xs px-3 py-2 rounded border bg-blue-50 border-blue-100 text-blue-700 space-y-1">
              <span className="font-medium">현재 적재된 쿠팡 광고비:</span>
              {coupangAdStatus.items.map((item) => (
                <div key={item.source} className="flex items-center gap-2">
                  <span className="w-32 text-blue-500">{item.channel_name}</span>
                  <span>{item.date_from} ~ {item.date_to} · {item.days}일치 · {item.total_spend.toLocaleString("ko-KR")}원</span>
                </div>
              ))}
            </div>
          )}

          {/* 업로드 성공 결과 */}
          {!coupangAdUploading && coupangAdResult && (
            <div className="text-xs px-3 py-2 rounded bg-green-50 border border-green-100 text-green-700 space-y-1">
              <div className="font-medium">
                저장 완료 ({coupangAdResult.date_from} ~ {coupangAdResult.date_to})
              </div>
              <div className="flex flex-wrap items-center gap-3 text-green-600">
                <span>총 광고비: <span className="font-medium">{coupangAdResult.total_spend.toLocaleString("ko-KR")}원</span></span>
                {Object.entries(coupangAdResult.channel_summary).map(([src, spend]) => (
                  <span key={src} className="px-1.5 py-0.5 rounded bg-green-100">
                    {src === "coupang_wing" ? "윙" : src === "coupang_rg" ? "로켓그로스" : "로켓배송"}: {spend.toLocaleString("ko-KR")}원
                  </span>
                ))}
                {coupangAdResult.recalculation_triggered && (
                  <span className="px-1.5 py-0.5 rounded bg-green-100">이익 자동 재계산됨</span>
                )}
              </div>
            </div>
          )}

          {/* 에러 */}
          {!coupangAdUploading && coupangAdError && (
            <div className="text-xs px-3 py-2 rounded bg-red-50 border border-red-100 text-red-700">
              오류: {coupangAdError}
            </div>
          )}
        </div>
      </div>

      {/* 로켓배송 매출 수동 입력 */}
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <h2 className="text-base font-semibold text-gray-800 mb-1">로켓배송 매출 수동 입력</h2>
        <p className="text-xs text-gray-400 mb-4">
          쿠팡 광고센터에서 확인한 일별 로켓배송 전체 매출액을 입력합니다.
          순이익 계산은 제외되며 대시보드 매출에만 합산됩니다.
        </p>

        <form onSubmit={handleRocketSubmit} className="flex flex-wrap gap-2 mb-4">
          <input
            type="date"
            value={rocketForm.date}
            onChange={(e) => setRocketForm((f) => ({ ...f, date: e.target.value }))}
            className="border border-gray-200 rounded px-2 py-1.5 text-sm"
            required
          />
          <input
            type="number"
            value={rocketForm.amount}
            onChange={(e) => setRocketForm((f) => ({ ...f, amount: e.target.value }))}
            placeholder="전체 매출액 (원)"
            className="border border-gray-200 rounded px-2 py-1.5 text-sm w-44"
            min="1"
            required
          />
          <input
            type="text"
            value={rocketForm.memo}
            onChange={(e) => setRocketForm((f) => ({ ...f, memo: e.target.value }))}
            placeholder="메모 (선택)"
            className="border border-gray-200 rounded px-2 py-1.5 text-sm w-32"
          />
          <button
            type="submit"
            disabled={rocketSaving}
            className="px-3 py-1.5 rounded text-sm font-medium bg-orange-500 text-white hover:bg-orange-600 disabled:opacity-50"
          >
            {rocketSaving ? "저장 중..." : "저장"}
          </button>
        </form>

        {rocketError && (
          <div className="text-xs px-3 py-2 mb-3 rounded bg-red-50 border border-red-100 text-red-700">
            {rocketError}
          </div>
        )}

        {rocketRecords.length > 0 && (
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="border-b border-gray-100 text-gray-400">
                <th className="text-left pb-1.5 pr-4 font-medium">날짜</th>
                <th className="text-right pb-1.5 pr-4 font-medium">매출액</th>
                <th className="text-left pb-1.5 pr-4 font-medium">메모</th>
                <th className="text-left pb-1.5 font-medium">수정일</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rocketRecords.map((r) => (
                <tr key={r.id} className="border-b border-gray-50 hover:bg-gray-50">
                  <td className="py-1.5 pr-4">{r.revenue_date}</td>
                  <td className="py-1.5 pr-4 text-right font-medium">
                    {Number(r.gross_revenue).toLocaleString("ko-KR")}원
                  </td>
                  <td className="py-1.5 pr-4 text-gray-400">{r.memo ?? "-"}</td>
                  <td className="py-1.5 text-gray-300">{r.updated_at.slice(0, 10)}</td>
                  <td className="py-1.5 pl-3">
                    <button
                      onClick={() => handleRocketDelete(r.id)}
                      className="text-red-400 hover:text-red-600 text-xs"
                    >
                      삭제
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {rocketRecords.length === 0 && (
          <p className="text-xs text-gray-300">입력된 매출 내역이 없습니다.</p>
        )}
      </div>

      {/* 월 고정비 입력 (3PL 정산서 — 네이버 전용) */}
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <h2 className="text-base font-semibold text-gray-800 mb-1">월 고정비 입력 (네이버)</h2>
        <p className="text-xs text-gray-400 mb-4">
          두핸즈(3PL) 월 정산서의 입고비·보관료·항공도선료·합포장비를 입력합니다.
          재고·입고 기반이라 주문에 붙일 수 없는 비용이라, 입력한 월 총액을{" "}
          <strong className="text-gray-500">일할 배분</strong>해 네이버 채널 일별 손익에 반영합니다.
          같은 달·같은 항목을 다시 저장하면 덮어씁니다.
        </p>

        {!naverChannel && (
          <p className="text-xs text-gray-300">네이버 채널을 찾을 수 없습니다.</p>
        )}

        {naverChannel && (
          <>
            <form onSubmit={handleFixedCostSubmit} className="flex flex-wrap items-end gap-2 mb-4">
              <label className="flex flex-col gap-1">
                <span className="text-[11px] text-gray-400">대상 월</span>
                <input
                  type="month"
                  value={fixedCostMonth}
                  onChange={(e) => setFixedCostMonth(e.target.value)}
                  className="border border-gray-200 rounded px-2 py-1.5 text-sm"
                  required
                />
              </label>
              {FIXED_COST_ITEMS.map((item) => (
                <label key={item} className="flex flex-col gap-1">
                  <span className="text-[11px] text-gray-400">{item}</span>
                  <input
                    type="number"
                    value={fixedCostAmounts[item] ?? ""}
                    onChange={(e) =>
                      setFixedCostAmounts((f) => ({ ...f, [item]: e.target.value }))
                    }
                    placeholder="0"
                    min="0"
                    className="border border-gray-200 rounded px-2 py-1.5 text-sm w-28"
                  />
                </label>
              ))}
              <button
                type="submit"
                disabled={fixedCostSaving}
                className="px-3 py-1.5 rounded text-sm font-medium bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50"
              >
                {fixedCostSaving ? "저장 중..." : "저장"}
              </button>
            </form>

            {fixedCostError && (
              <div className="text-xs px-3 py-2 mb-3 rounded bg-red-50 border border-red-100 text-red-700">
                {fixedCostError}
              </div>
            )}

            {fixedCostRecords.length > 0 && (
              <table className="w-full text-xs border-collapse">
                <thead>
                  <tr className="border-b border-gray-100 text-gray-400">
                    <th className="text-left pb-1.5 pr-4 font-medium">월</th>
                    <th className="text-left pb-1.5 pr-4 font-medium">항목</th>
                    <th className="text-right pb-1.5 pr-4 font-medium">금액</th>
                    <th className="text-left pb-1.5 font-medium">수정일</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {fixedCostRecords.map((r) => (
                    <tr key={r.id} className="border-b border-gray-50 hover:bg-gray-50">
                      <td className="py-1.5 pr-4">{r.year_month}</td>
                      <td className="py-1.5 pr-4 text-gray-500">{r.item}</td>
                      <td className="py-1.5 pr-4 text-right font-medium">
                        {Number(r.amount).toLocaleString("ko-KR")}원
                      </td>
                      <td className="py-1.5 text-gray-300">{r.updated_at.slice(0, 10)}</td>
                      <td className="py-1.5 pl-3">
                        <button
                          onClick={() => handleFixedCostDelete(r.id)}
                          className="text-red-400 hover:text-red-600 text-xs"
                        >
                          삭제
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {fixedCostRecords.length === 0 && (
              <p className="text-xs text-gray-300">입력된 고정비가 없습니다.</p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
