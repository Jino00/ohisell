// Settlements.tsx — 정산 관리 페이지 (Sprint 3)
import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchApi,
  uploadFile,
  type Channel,
  type SettlementItem,
  type SettlementListResponse,
  type SettlementSummary,
  type UploadResult,
} from "../lib/api";

function formatKRW(value: number | string): string {
  return Math.round(Number(value)).toLocaleString("ko-KR");
}

function getDefaultDateRange() {
  const to = new Date();
  const from = new Date();
  from.setDate(from.getDate() - 30);
  return {
    from: from.toISOString().split("T")[0],
    to: to.toISOString().split("T")[0],
  };
}

export default function Settlements() {
  const defaults = getDefaultDateRange();
  const [items, setItems] = useState<SettlementItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [summary, setSummary] = useState<SettlementSummary | null>(null);

  // Filters
  const [channelFilter, setChannelFilter] = useState<string>("");
  const [dateFrom, setDateFrom] = useState(defaults.from);
  const [dateTo, setDateTo] = useState(defaults.to);

  // Upload state
  const [showUpload, setShowUpload] = useState(false);
  const [uploadChannel, setUploadChannel] = useState<string>("");
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // Delete state
  const [deleting, setDeleting] = useState<number | null>(null);

  const fetchSettlements = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (channelFilter) params.set("channel_id", channelFilter);
      if (dateFrom) params.set("date_from", dateFrom);
      if (dateTo) params.set("date_to", dateTo);
      params.set("page", String(page));
      params.set("page_size", "50");

      const data = await fetchApi<SettlementListResponse>(`/api/settlements?${params}`);
      setItems(data.items);
      setTotal(data.total);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [channelFilter, dateFrom, dateTo, page]);

  const fetchSummary = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (dateFrom) params.set("date_from", dateFrom);
      if (dateTo) params.set("date_to", dateTo);
      const data = await fetchApi<SettlementSummary>(`/api/settlements/summary?${params}`);
      setSummary(data);
    } catch {
      setSummary(null);
    }
  }, [dateFrom, dateTo]);

  useEffect(() => {
    fetchApi<Channel[]>("/api/channels").then(setChannels).catch(() => {});
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      fetchSettlements();
      fetchSummary();
    }, 300);
    return () => clearTimeout(timer);
  }, [fetchSettlements, fetchSummary]);

  async function handleUpload() {
    const file = fileRef.current?.files?.[0];
    if (!file || !uploadChannel) return;

    setUploading(true);
    setUploadResult(null);
    try {
      const result = await uploadFile(`/api/settlements/upload/${uploadChannel}`, file) as UploadResult;
      setUploadResult(result);
      fetchSettlements();
      fetchSummary();
    } catch (e) {
      setUploadResult({ imported: 0, skipped: 0, errors: [String(e)] });
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(id: number) {
    if (!confirm("이 정산 데이터를 삭제하시겠습니까?")) return;
    setDeleting(id);
    try {
      await fetchApi(`/api/settlements/${id}`, { method: "DELETE" });
      fetchSettlements();
      fetchSummary();
    } catch {
      // ignore
    } finally {
      setDeleting(null);
    }
  }

  const totalPages = Math.ceil(total / 50);

  return (
    <div className="max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold text-gray-900">정산 관리</h2>
        <button
          onClick={() => { setShowUpload(!showUpload); setUploadResult(null); }}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 flex items-center gap-2"
        >
          정산 업로드
        </button>
      </div>

      {/* Upload panel */}
      {showUpload && (
        <div className="bg-white border rounded-lg p-4 mb-6">
          <h3 className="text-sm font-medium text-gray-700 mb-3">정산 파일 업로드</h3>
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">채널 선택</label>
              <select
                value={uploadChannel}
                onChange={(e) => setUploadChannel(e.target.value)}
                className="border rounded-lg px-3 py-2 text-sm"
              >
                <option value="">채널 선택</option>
                {channels.map((ch) => (
                  <option key={ch.id} value={ch.id}>{ch.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">엑셀/CSV 파일</label>
              <input
                ref={fileRef}
                type="file"
                accept=".xlsx,.xls,.csv"
                className="border rounded-lg px-3 py-2 text-sm"
              />
            </div>
            <button
              onClick={handleUpload}
              disabled={uploading || !uploadChannel}
              className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 text-sm"
            >
              {uploading ? "업로드 중..." : "업로드"}
            </button>
          </div>
          {uploadResult && (
            <div className={`mt-3 px-4 py-2 rounded-lg text-sm ${
              uploadResult.errors.length > 0 ? "bg-red-50 text-red-700" : "bg-green-50 text-green-700"
            }`}>
              {uploadResult.errors.length > 0
                ? `오류: ${uploadResult.errors.join(", ")}`
                : `${uploadResult.imported}건 가져옴, ${uploadResult.skipped}건 건너뜀`}
            </div>
          )}
        </div>
      )}

      {/* Summary cards */}
      {summary && summary.count > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-4 mb-6">
          <div className="bg-white border rounded-lg p-4">
            <div className="text-sm text-gray-500">총 매출</div>
            <div className="text-xl font-bold text-blue-600">{formatKRW(summary.total_amount)}원</div>
            <div className="text-xs text-gray-400">{summary.count}건</div>
          </div>
          <div className="bg-white border rounded-lg p-4">
            <div className="text-sm text-gray-500">총 수수료</div>
            <div className="text-xl font-bold text-gray-600">{formatKRW(summary.total_commission)}원</div>
            <div className="text-xs text-gray-400">배송비 {formatKRW(summary.total_shipping_fee)}원 포함</div>
          </div>
          <div className="bg-white border rounded-lg p-4">
            <div className="text-sm text-gray-500">순 정산금</div>
            <div className={`text-xl font-bold ${summary.total_net >= 0 ? "text-green-600" : "text-red-600"}`}>
              {formatKRW(summary.total_net)}원
            </div>
            <div className="text-xs text-gray-400">매출 - 수수료</div>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-4">
        <select
          value={channelFilter}
          onChange={(e) => { setChannelFilter(e.target.value); setPage(1); }}
          className="border rounded-lg px-3 py-2 text-sm"
        >
          <option value="">전체 채널</option>
          {channels.map((ch) => (
            <option key={ch.id} value={ch.id}>{ch.name}</option>
          ))}
        </select>
        <input
          type="date"
          value={dateFrom}
          onChange={(e) => { setDateFrom(e.target.value); setPage(1); }}
          className="border rounded-lg px-3 py-2 text-sm"
        />
        <span className="self-center text-gray-400">~</span>
        <input
          type="date"
          value={dateTo}
          onChange={(e) => { setDateTo(e.target.value); setPage(1); }}
          className="border rounded-lg px-3 py-2 text-sm"
        />
      </div>

      {/* Loading skeleton */}
      {loading && (
        <div className="bg-white border rounded-lg overflow-hidden">
          <div className="overflow-x-auto">
          <table className="min-w-full">
            <thead className="bg-gray-50">
              <tr>
                {["정산일", "채널", "제품정산", "배송정산", "정산액", "수수료", "순정산", ""].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium text-gray-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Array.from({ length: 5 }).map((_, i) => (
                <tr key={i} className="border-t">
                  {Array.from({ length: 8 }).map((_, j) => (
                    <td key={j} className="px-4 py-3">
                      <div className="h-4 bg-gray-200 rounded animate-pulse" style={{ width: `${50 + Math.random() * 40}%` }} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </div>
      )}

      {/* Data table */}
      {!loading && items.length > 0 && (
        <div className="bg-white border rounded-lg overflow-hidden">
          <div className="overflow-x-auto">
          <table className="min-w-full">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">정산일</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">채널</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">제품정산</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">배송정산</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">정산액</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">수수료</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">순정산</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 w-16"></th>
              </tr>
            </thead>
            <tbody>
              {items.map((s) => {
                const prodAmt = Number(s.product_amount ?? (s.total_amount - s.shipping_fee));
                return (
                <tr key={s.id} className="border-t hover:bg-gray-50">
                  <td className="px-4 py-3 text-sm text-gray-600 whitespace-nowrap">
                    {new Date(s.settlement_date).toLocaleDateString("ko-KR")}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-900">{s.channel_name}</td>
                  <td className="px-4 py-3 text-sm text-right text-gray-700">{formatKRW(prodAmt)}원</td>
                  <td className="px-4 py-3 text-sm text-right text-gray-500">
                    {Number(s.shipping_fee) === 0 ? <span className="text-gray-300">—</span> : `${formatKRW(s.shipping_fee)}원`}
                  </td>
                  <td className="px-4 py-3 text-sm text-right font-medium">{formatKRW(s.total_amount)}원</td>
                  <td className="px-4 py-3 text-sm text-right text-gray-600">{formatKRW(s.commission)}원</td>
                  <td className={`px-4 py-3 text-sm text-right font-medium ${s.net_amount >= 0 ? "text-green-600" : "text-red-600"}`}>
                    {formatKRW(s.net_amount)}원
                  </td>
                  <td className="px-4 py-3 text-center">
                    <button
                      onClick={() => handleDelete(s.id)}
                      disabled={deleting === s.id}
                      className="text-red-400 hover:text-red-600 text-sm disabled:opacity-50"
                      title="삭제"
                    >
                      {deleting === s.id ? "..." : "X"}
                    </button>
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between px-4 py-3 border-t bg-gray-50">
              <span className="text-sm text-gray-500">총 {total}건</span>
              <div className="flex gap-1">
                <button
                  onClick={() => setPage(Math.max(1, page - 1))}
                  disabled={page === 1}
                  className="px-3 py-1 text-sm border rounded hover:bg-white disabled:opacity-50"
                >
                  이전
                </button>
                <span className="px-3 py-1 text-sm">{page} / {totalPages}</span>
                <button
                  onClick={() => setPage(Math.min(totalPages, page + 1))}
                  disabled={page === totalPages}
                  className="px-3 py-1 text-sm border rounded hover:bg-white disabled:opacity-50"
                >
                  다음
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Empty state */}
      {!loading && items.length === 0 && (
        <div className="bg-white border rounded-lg p-12 text-center">
          <div className="text-4xl mb-4">💰</div>
          <h3 className="text-lg font-semibold text-gray-700 mb-2">정산 데이터가 없습니다</h3>
          <p className="text-gray-500">정산 업로드 버튼을 눌러 채널별 정산 파일을 가져오세요.</p>
        </div>
      )}
    </div>
  );
}
