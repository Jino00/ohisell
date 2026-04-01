// Orders.tsx — 주문 관리 페이지 (Sprint 2)
import { useCallback, useEffect, useState } from "react";
import {
  fetchApi,
  type Channel,
  type OrderItem,
  type OrderListResponse,
  type ProfitSummary,
  type SyncResult,
  type SyncStatus,
} from "../lib/api";

const STATUS_LABELS: Record<string, string> = {
  confirmed: "확인",
  shipped: "배송중",
  delivered: "배송완료",
  cancelled: "취소",
  returned: "반품",
};

const CHANNEL_COLORS: Record<string, string> = {
  coupang: "bg-red-100 text-red-700",
  naver: "bg-green-100 text-green-700",
  cafe24: "bg-blue-100 text-blue-700",
};

function formatKRW(value: number): string {
  return Math.round(value).toLocaleString("ko-KR");
}

function relativeTime(isoStr: string): string {
  const diff = Date.now() - new Date(isoStr).getTime();
  const hours = Math.floor(diff / 3600000);
  if (hours < 1) return "방금 전";
  if (hours < 24) return `${hours}시간 전`;
  const days = Math.floor(hours / 24);
  return `${days}일 전`;
}

function getDefaultDateRange() {
  const to = new Date();
  const from = new Date();
  from.setDate(from.getDate() - 7);
  return {
    from: from.toISOString().split("T")[0],
    to: to.toISOString().split("T")[0],
  };
}

export default function Orders() {
  const defaults = getDefaultDateRange();
  const [orders, setOrders] = useState<OrderItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [syncStatuses, setSyncStatuses] = useState<SyncStatus[]>([]);
  const [summary, setSummary] = useState<ProfitSummary | null>(null);

  // Filters
  const [channelFilter, setChannelFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [dateFrom, setDateFrom] = useState(defaults.from);
  const [dateTo, setDateTo] = useState(defaults.to);
  const [search, setSearch] = useState("");

  // Sync state
  const [syncing, setSyncing] = useState(false);
  const [syncResults, setSyncResults] = useState<SyncResult[] | null>(null);
  const [syncDropdown, setSyncDropdown] = useState(false);

  const hasStaleChannels = syncStatuses.some((s) => {
    if (!s.last_sync) return true;
    return Date.now() - new Date(s.last_sync).getTime() > 86400000;
  });

  const fetchOrders = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (channelFilter) params.set("channel_id", channelFilter);
      if (statusFilter) params.set("status", statusFilter);
      if (dateFrom) params.set("date_from", dateFrom);
      if (dateTo) params.set("date_to", dateTo);
      if (search) params.set("search", search);
      params.set("page", String(page));
      params.set("page_size", "50");

      const data = await fetchApi<OrderListResponse>(`/api/orders?${params}`);
      setOrders(data.items);
      setTotal(data.total);
    } catch {
      setOrders([]);
    } finally {
      setLoading(false);
    }
  }, [channelFilter, statusFilter, dateFrom, dateTo, search, page]);

  const fetchSummary = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (channelFilter) params.set("channel_id", channelFilter);
      if (dateFrom) params.set("date_from", dateFrom);
      if (dateTo) params.set("date_to", dateTo);
      const data = await fetchApi<ProfitSummary>(`/api/orders/summary?${params}`);
      setSummary(data);
    } catch {
      setSummary(null);
    }
  }, [channelFilter, dateFrom, dateTo]);

  useEffect(() => {
    fetchApi<Channel[]>("/api/channels").then(setChannels).catch(() => {});
    fetchApi<SyncStatus[]>("/api/sync/status").then(setSyncStatuses).catch(() => {});
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      fetchOrders();
      fetchSummary();
    }, 300);
    return () => clearTimeout(timer);
  }, [fetchOrders, fetchSummary]);

  async function handleSync(channelId?: number) {
    setSyncing(true);
    setSyncDropdown(false);
    setSyncResults(null);
    try {
      if (channelId) {
        const result = await fetchApi<SyncResult>(`/api/sync/channel/${channelId}`, {
          method: "POST",
          body: JSON.stringify({}),
        });
        setSyncResults([result]);
      } else {
        const results = await fetchApi<SyncResult[]>("/api/sync/all", {
          method: "POST",
          body: JSON.stringify({}),
        });
        setSyncResults(results);
      }
      fetchOrders();
      fetchSummary();
      fetchApi<SyncStatus[]>("/api/sync/status").then(setSyncStatuses).catch(() => {});
    } catch (e) {
      setSyncResults([{ channel_id: 0, channel_name: "", status: "error", new_orders: 0, updated_orders: 0, errors: [String(e)] }]);
    } finally {
      setSyncing(false);
      setTimeout(() => setSyncResults(null), 8000);
    }
  }

  const totalPages = Math.ceil(total / 50);
  const isFirstRun = !loading && total === 0 && !search && !channelFilter && !statusFilter;

  return (
    <div className="max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold text-gray-900">주문 관리</h2>
        <div className="relative">
          <button
            disabled={syncing}
            onClick={() => setSyncDropdown(!syncDropdown)}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 flex items-center gap-2"
          >
            {syncing ? (
              <><span className="animate-spin">↻</span> 동기화 중...</>
            ) : (
              "Sync Now"
            )}
          </button>
          {syncDropdown && (
            <div className="absolute right-0 mt-1 w-64 bg-white border border-gray-200 rounded-lg shadow-lg z-10">
              <button
                onClick={() => handleSync()}
                className="w-full px-4 py-2 text-left hover:bg-gray-50 font-medium border-b"
              >
                전체 동기화
              </button>
              {channels.filter((c) => c.api_type !== "excel").map((ch) => {
                const ss = syncStatuses.find((s) => s.channel_id === ch.id);
                const isStale = !ss?.last_sync || Date.now() - new Date(ss.last_sync).getTime() > 86400000;
                return (
                  <button
                    key={ch.id}
                    onClick={() => handleSync(ch.id)}
                    className="w-full px-4 py-2 text-left hover:bg-gray-50 flex items-center justify-between text-sm"
                  >
                    <span>{ch.name}</span>
                    <span className={isStale ? "text-orange-500" : "text-gray-400"}>
                      {isStale && "● "}
                      {ss?.last_sync ? relativeTime(ss.last_sync) : "미동기화"}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Sync results toast */}
      {syncResults && (
        <div className="mb-4">
          {syncResults.map((r, i) => (
            <div
              key={i}
              className={`px-4 py-2 rounded-lg text-sm mb-1 ${
                r.status === "error" ? "bg-red-50 text-red-700" :
                r.status === "skipped" ? "bg-yellow-50 text-yellow-700" :
                "bg-green-50 text-green-700"
              }`}
            >
              {r.status === "error"
                ? `${r.channel_name || "동기화"} 실패: ${r.errors.join(", ")}`
                : r.status === "skipped"
                ? `${r.channel_name}: ${r.errors.join(", ")}`
                : `${r.channel_name}: ${r.new_orders}건 추가, ${r.updated_orders}건 갱신`}
            </div>
          ))}
        </div>
      )}

      {/* Stale warning */}
      {hasStaleChannels && !syncing && (
        <div className="mb-4 px-4 py-2 bg-yellow-50 border border-yellow-200 rounded-lg text-sm text-yellow-700">
          일부 채널이 24시간 이상 동기화되지 않았습니다.
        </div>
      )}

      {/* Profit summary cards */}
      {summary && summary.order_count > 0 && (
        <div className="grid grid-cols-3 gap-4 mb-6">
          <div className="bg-white border rounded-lg p-4">
            <div className="text-sm text-gray-500">총 매출</div>
            <div className="text-xl font-bold text-blue-600">₩{formatKRW(summary.total_revenue)}</div>
            <div className="text-xs text-gray-400">{summary.order_count}건</div>
          </div>
          <div className="bg-white border rounded-lg p-4">
            <div className="text-sm text-gray-500">총 비용</div>
            <div className="text-xl font-bold text-gray-600">
              ₩{formatKRW(summary.total_cost + summary.total_commission + summary.total_shipping + summary.total_vat)}
            </div>
            <div className="text-xs text-gray-400">원가+수수료+배송+VAT</div>
          </div>
          <div className="bg-white border rounded-lg p-4">
            <div className="text-sm text-gray-500">순이익</div>
            <div className={`text-xl font-bold ${summary.net_profit >= 0 ? "text-green-600" : "text-red-600"}`}>
              ₩{formatKRW(summary.net_profit)}
            </div>
            <div className="text-xs text-gray-400">광고비 제외</div>
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
        <select
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
          className="border rounded-lg px-3 py-2 text-sm"
        >
          <option value="">전체 상태</option>
          {Object.entries(STATUS_LABELS).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
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
        <input
          type="text"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          placeholder="주문번호 / 상품명 검색"
          className="border rounded-lg px-3 py-2 text-sm flex-1 min-w-48"
        />
      </div>

      {/* First-run empty state */}
      {isFirstRun && (
        <div className="bg-white border rounded-lg p-12 text-center">
          <div className="text-4xl mb-4">📦</div>
          <h3 className="text-lg font-semibold text-gray-700 mb-2">아직 주문 데이터가 없습니다</h3>
          <p className="text-gray-500 mb-4">Sync Now 버튼을 눌러 첫 주문 데이터를 가져오세요.</p>
        </div>
      )}

      {/* Loading skeleton */}
      {loading && (
        <div className="bg-white border rounded-lg overflow-hidden">
          <table className="w-full">
            <thead className="bg-gray-50">
              <tr>
                {["날짜", "상품명", "채널", "수량", "가격", "상태", "주문번호"].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Array.from({ length: 5 }).map((_, i) => (
                <tr key={i} className="border-t">
                  {Array.from({ length: 7 }).map((_, j) => (
                    <td key={j} className="px-4 py-3">
                      <div className="h-4 bg-gray-200 rounded animate-pulse" style={{ width: `${60 + Math.random() * 40}%` }} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Orders table */}
      {!loading && orders.length > 0 && (
        <div className="bg-white border rounded-lg overflow-hidden">
          <table className="w-full">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">날짜</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">상품명</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">채널</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">수량</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">가격</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">상태</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 text-gray-400">주문번호</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => {
                const ch = channels.find((c) => c.id === o.channel_id);
                const colorClass = CHANNEL_COLORS[ch?.platform || ""] || "bg-gray-100 text-gray-700";
                return (
                  <tr key={o.id} className="border-t hover:bg-gray-50">
                    <td className="px-4 py-3 text-sm text-gray-600 whitespace-nowrap">
                      {new Date(o.order_date).toLocaleDateString("ko-KR")}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-900">
                      {o.product_name || o.platform_product_name || "-"}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`text-xs px-2 py-1 rounded-full ${colorClass}`}>
                        {o.channel_name}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm text-right text-gray-600">{o.quantity}</td>
                    <td className="px-4 py-3 text-sm text-right font-medium">₩{formatKRW(o.selling_price)}</td>
                    <td className="px-4 py-3 text-sm text-gray-600">
                      {STATUS_LABELS[o.status] || o.status}
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-400">{o.order_number}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>

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

      {/* Filter empty state */}
      {!loading && orders.length === 0 && !isFirstRun && (
        <div className="bg-white border rounded-lg p-8 text-center">
          <p className="text-gray-500">조건에 맞는 주문이 없습니다. 날짜나 채널을 조정해보세요.</p>
        </div>
      )}
    </div>
  );
}
