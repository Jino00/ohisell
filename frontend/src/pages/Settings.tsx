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
  const gfaFileRef = useRef<HTMLInputElement>(null);

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
    } catch {
      /* silent */
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    fetchGfaStatus();
  }, [fetchStatus, fetchGfaStatus]);

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

  const handleGfaUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

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
      fetchGfaStatus();  // 적재 현황 즉시 갱신
    } catch (err) {
      setGfaError(err instanceof Error ? err.message : "업로드 중 오류 발생");
    } finally {
      setGfaUploading(false);
      if (gfaFileRef.current) gfaFileRef.current.value = "";
    }
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
          {/* 헤더 + 업로드 버튼 */}
          <div className="flex items-start justify-between">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-lg">🟢</span>
                <span className="font-medium text-gray-900">GFA · ADVoost 쇼핑</span>
                <span className="px-2 py-0.5 text-xs rounded-full bg-gray-100 text-gray-600">CSV 업로드</span>
              </div>
              <div className="text-xs text-gray-500 mt-1">
                cafe24 GFA 콘솔 → 보고서 → CSV 다운로드 후 업로드
                <span className="ml-2 text-gray-400">파일명 예: theohi11_광고비 보고서_20260515_20260515.csv</span>
              </div>
            </div>
            <div>
              <input
                ref={gfaFileRef}
                type="file"
                accept=".csv"
                className="hidden"
                onChange={handleGfaUpload}
              />
              <button
                onClick={() => gfaFileRef.current?.click()}
                disabled={gfaUploading}
                className="px-3 py-1.5 text-xs bg-green-50 text-green-700 rounded-md hover:bg-green-100 disabled:opacity-50"
              >
                {gfaUploading ? "처리 중..." : "CSV 업로드"}
              </button>
            </div>
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

          {/* 업로드 진행 중 */}
          {gfaUploading && (
            <div className="text-xs px-3 py-2 rounded bg-yellow-50 border border-yellow-100 text-yellow-700">
              CSV 파싱 중... 잠시 기다려 주세요.
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
    </div>
  );
}
