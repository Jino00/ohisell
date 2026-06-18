// RgReplenishmentTable.tsx — RG(로켓그로스) 발송관제 테이블
// 재고 관리 페이지 입주①. 모든 RG 옵션 노출 + 컬럼 정렬 + 상품명 검색.
// (이전: CoupangOps 로켓그로스 탭 내 RgReplenishmentSection — D-19로 분리)
import { useMemo, useState } from "react";
import type { ReplenishmentPlan, ReplenishmentItem } from "../lib/api";

const STATUS_META: Record<string, { label: string; badge: string; row: string }> = {
  reorder_now:      { label: "🔴 즉시발송", badge: "bg-red-100 text-red-700",     row: "bg-red-50" },
  ok:               { label: "🟢 정상",     badge: "bg-green-100 text-green-700", row: "" },
  well_stocked:     { label: "🔵 여유",     badge: "bg-blue-100 text-blue-700",   row: "" },
  insufficient_data:{ label: "⬜ 데이터부족", badge: "bg-gray-100 text-gray-500",  row: "" },
};

// 상태 정렬 우선순위(발송 긴급도 순) — 서버 기본 정렬과 동일.
const STATUS_ORDER: Record<string, number> = {
  reorder_now: 0, ok: 1, insufficient_data: 2, well_stocked: 3,
};

type SortKey =
  | "name" | "status" | "current_stock" | "in_transit_qty" | "effective_stock"
  | "daily_base_rate" | "lead_p90" | "days_to_safety" | "ship_by_date" | "recommend_qty";
type SortDir = "asc" | "desc";

// 정렬값 추출: 숫자/문자 모두 number|string|null 반환. null은 항상 뒤로.
function sortValue(it: ReplenishmentItem, key: SortKey): number | string | null {
  switch (key) {
    case "name": return (it.product_name || it.item_name || it.vendor_item_id).toLowerCase();
    case "status": return STATUS_ORDER[it.status] ?? 99;
    case "current_stock": return it.current_stock ?? null;
    case "in_transit_qty": return it.in_transit_qty ?? null;
    case "effective_stock": return it.effective_stock ?? null;
    case "daily_base_rate": return it.daily_base_rate ?? null;
    case "lead_p90": return it.lead_p90 ?? null;
    case "days_to_safety": return it.days_to_safety ?? null;
    case "ship_by_date": return it.ship_by_date ?? null;
    case "recommend_qty": return it.recommend_qty ?? null;
  }
}

function SortableTh({
  label, col, sortKey, sortDir, onSort, align = "right",
}: {
  label: string; col: SortKey; sortKey: SortKey | null; sortDir: SortDir;
  onSort: (k: SortKey) => void; align?: "left" | "center" | "right";
}) {
  const active = sortKey === col;
  return (
    <th className={`px-3 py-2 text-${align} select-none`}>
      <button
        onClick={() => onSort(col)}
        className={`inline-flex items-center gap-0.5 hover:text-gray-800 ${active ? "text-blue-600 font-semibold" : "text-gray-500"}`}
      >
        {label}
        <span className={active ? "text-blue-500" : "text-gray-300"}>
          {active ? (sortDir === "desc" ? "↓" : "↑") : "↕"}
        </span>
      </button>
    </th>
  );
}

export default function RgReplenishmentTable({
  plan, loading, error,
}: {
  plan: ReplenishmentPlan | null;
  loading: boolean;
  error: string | null;
}) {
  const [sortKey, setSortKey] = useState<SortKey | null>(null);  // null = 서버 기본(긴급도) 순
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [search, setSearch] = useState("");

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    else { setSortKey(key); setSortDir(key === "name" || key === "status" ? "asc" : "desc"); }
  }

  // 검색 + 정렬 (전체 노출 — D-19). 검색은 상품명/옵션명/옵션ID 부분일치.
  const rows = useMemo(() => {
    const items = plan?.items ?? [];
    const q = search.trim().toLowerCase();
    const filtered = q
      ? items.filter(
          (it) =>
            (it.product_name ?? "").toLowerCase().includes(q) ||
            it.item_name.toLowerCase().includes(q) ||
            it.vendor_item_id.toLowerCase().includes(q)
        )
      : items;
    if (sortKey === null) return filtered;  // 서버 기본 순 유지
    const dir = sortDir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const av = sortValue(a, sortKey);
      const bv = sortValue(b, sortKey);
      if (av === null && bv === null) return 0;
      if (av === null) return 1;   // null 항상 뒤로(정렬방향 무관)
      if (bv === null) return -1;
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return 0;
    });
  }, [plan, search, sortKey, sortDir]);

  if (error) {
    return (
      <div className="bg-red-50 text-red-700 rounded px-4 py-2 text-sm">
        발송관제 로드 실패: {error}
      </div>
    );
  }

  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      {/* 헤더 */}
      <div className="px-4 py-3 border-b border-gray-100 bg-orange-50 flex items-center gap-3 flex-wrap">
        <span className="text-sm font-semibold text-orange-800">📦 RG 발송관제</span>
        {plan && (
          <>
            <span className="text-xs text-gray-500">기준일 {plan.generated_at} · {plan.target_days}일치 목표 · 데이터 {plan.trust_days}일분 누적</span>
            {plan.in_transit_meta && (
              plan.in_transit_meta.fresh ? (
                <span
                  className="text-xs px-2 py-0.5 rounded bg-emerald-100 text-emerald-700"
                  title={`마지막 입고 동기화 ${plan.in_transit_meta.last_fetch_at ?? "—"} · 유효재고 = 현재고 + 발송중`}
                >
                  🚚 발송중 최신 · 총 {(plan.in_transit_meta.total_in_transit_qty ?? 0).toLocaleString("ko-KR")}개
                </span>
              ) : (
                <span
                  className="text-xs px-2 py-0.5 rounded bg-amber-100 text-amber-700"
                  title="Wing 쿠키 만료 — 발송중 수량을 0으로 취급(보수적). 입고 쿠키 갱신 필요."
                >
                  ⚠️ 발송중 데이터 만료(0 취급)
                </span>
              )
            )}
            <div className="flex gap-2 text-xs">
              <span className="px-2 py-0.5 rounded bg-red-100 text-red-700">즉시발송 {plan.summary.reorder_now}</span>
              <span className="px-2 py-0.5 rounded bg-green-100 text-green-700">정상 {plan.summary.ok}</span>
              <span className="px-2 py-0.5 rounded bg-blue-100 text-blue-700">여유 {plan.summary.well_stocked}</span>
              <span className="px-2 py-0.5 rounded bg-gray-100 text-gray-500">부족 {plan.summary.insufficient_data}</span>
              {plan.summary.low_confidence > 0 && (
                <span className="px-2 py-0.5 rounded bg-yellow-100 text-yellow-700">저신뢰 {plan.summary.low_confidence}</span>
              )}
            </div>
            <input
              className="ml-auto border border-gray-300 rounded px-3 py-1.5 text-sm w-48"
              placeholder="상품명·옵션·ID 검색…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </>
        )}
      </div>

      {loading ? (
        <div className="px-4 py-6 text-center text-gray-400 text-sm">발송관제 로딩 중…</div>
      ) : !plan || rows.length === 0 ? (
        <div className="px-4 py-6 text-center text-gray-400 text-sm">
          {!plan ? "데이터 없음" : search.trim() ? "검색 결과 없음" : "표시할 상품 없음"}
        </div>
      ) : (
        <>
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-xs border-b border-gray-100">
              <tr>
                <SortableTh label="상품명" col="name" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} align="left" />
                <SortableTh label="상태" col="status" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} align="center" />
                <SortableTh label="현재고" col="current_stock" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                <SortableTh label="발송중" col="in_transit_qty" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                <SortableTh label="유효재고" col="effective_stock" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                <SortableTh label="일판매" col="daily_base_rate" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                <SortableTh label="리드타임" col="lead_p90" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                <SortableTh label="판매가능일" col="days_to_safety" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                <SortableTh label="권장 발송일" col="ship_by_date" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} align="center" />
                <SortableTh label="권장 수량" col="recommend_qty" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
              </tr>
            </thead>
            <tbody>
              {rows.map((it) => {
                const meta = STATUS_META[it.status] ?? STATUS_META.insufficient_data;
                return (
                  <tr key={it.vendor_item_id} className={`border-t border-gray-50 hover:bg-gray-50 ${meta.row}`}>
                    <td className="px-4 py-2 max-w-[320px]">
                      <div
                        className="truncate text-gray-900"
                        title={it.product_name ? `${it.product_name}, ${it.item_name}` : it.item_name}
                      >
                        {it.product_name ? (
                          <>{it.product_name}<span className="text-gray-400">, {it.item_name}</span></>
                        ) : (
                          it.item_name
                        )}
                      </div>
                      <div className="text-xs text-gray-400">{it.vendor_item_id}</div>
                    </td>
                    <td className="px-3 py-2 text-center">
                      <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${meta.badge}`}>
                        {meta.label}
                      </span>
                      {it.confidence === "low" && (
                        <span className="ml-1 text-xs text-yellow-600 font-medium">저신뢰</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right font-medium">
                      {it.current_stock != null ? `${it.current_stock.toLocaleString("ko-KR")}개` : "—"}
                    </td>
                    <td className="px-3 py-2 text-right text-gray-600">
                      {/* 발송중은 유효재고가 있는(=추천 역산된) 행에서만 표시 — insufficient 행은 유효재고가 비므로 일관되게 "—"(P2-1 정합). */}
                      {it.effective_stock != null && it.in_transit_qty != null && it.in_transit_qty > 0 ? (
                        <span
                          className="text-sky-600"
                          title={it.expected_stowing_at ? `판매개시 예정 ${it.expected_stowing_at}` : "발송중(판매개시 전)"}
                        >
                          +{it.in_transit_qty.toLocaleString("ko-KR")}개
                        </span>
                      ) : "—"}
                    </td>
                    <td className="px-3 py-2 text-right font-medium" title="유효재고 = 현재고 + 발송중 (추천 수량 역산 기준)">
                      {it.effective_stock != null ? `${it.effective_stock.toLocaleString("ko-KR")}개` : "—"}
                    </td>
                    <td className="px-3 py-2 text-right text-gray-600">
                      {it.daily_base_rate != null ? `${it.daily_base_rate.toFixed(2)}/일` : "—"}
                    </td>
                    <td className="px-3 py-2 text-right text-gray-600">
                      {it.lead_p90 != null ? `${it.lead_p90.toFixed(1)}일` : "—"}
                    </td>
                    <td className="px-3 py-2 text-right">
                      {it.days_to_safety != null ? (
                        <span className={it.days_to_safety <= 3 ? "text-red-600 font-semibold" : it.days_to_safety <= 7 ? "text-orange-500" : "text-gray-700"}>
                          {it.days_to_safety}일
                        </span>
                      ) : "—"}
                    </td>
                    <td className="px-3 py-2 text-center">
                      {it.ship_by_date ? (
                        <span className={it.status === "reorder_now" ? "text-red-600 font-semibold" : "text-gray-700"}>
                          {it.ship_by_date}
                        </span>
                      ) : "—"}
                    </td>
                    <td className="px-3 py-2 text-right font-medium">
                      {it.recommend_qty != null && it.recommend_qty > 0
                        ? `${it.recommend_qty.toLocaleString("ko-KR")}개`
                        : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="px-4 py-2 border-t border-gray-100 text-xs text-gray-400">
            전체 {plan.items.length}개{search.trim() && rows.length !== plan.items.length ? ` 중 ${rows.length}개 표시` : ""}
          </div>
        </>
      )}
    </div>
  );
}
