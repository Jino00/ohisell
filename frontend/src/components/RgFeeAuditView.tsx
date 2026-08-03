// RgFeeAuditView.tsx — RG(로켓그로스) 청구액 감사 뷰 (트랙 RG-Fee S8, D-17)
// 읽기 전용 스크리닝 화면. 회계 수치(net_profit 등)는 계산·표시하지 않는다.
// ★프론트는 단가를 다시 계산하지 않는다 — per_unit_delivery/per_unit_warehousing은 API가
//   낸 값을 그대로 렌더한다. charged_delivery(전 주기 청구총액) ÷ order_count를 프론트에서
//   또 하면 2026-08-03에 규명된 오탐 4건이 되살아난다(rg_fee_audit.py 상단 주석 참조).
import { useMemo, useState } from "react";
import type { RgFeeAudit, RgFeeAuditItem, RgFeeAuditPeriodDetail } from "../lib/api";

function won(n: number | null | undefined): string {
  if (n == null) return "—";
  return `${Math.round(n).toLocaleString("ko-KR")}원`;
}
function num(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString("ko-KR");
}

// 플래그 표시 순서·톤 — billed_size_vs_amount_mismatch가 가장 강한 신호(정산서 내부 모순,
// 추론 0개). 그다음 measured_vs_billed_mismatch(물류센터 실측과 불일치) → size_mismatch_high
// (등록 치수 기준 추정이라 가장 약함) → below_floor 순으로 눈에 띄게 배치한다.
const FLAG_META: Record<string, { label: string; className: string }> = {
  billed_size_vs_amount_mismatch: {
    label: "정산서 자체모순",
    className: "bg-red-600 text-white font-semibold",
  },
  measured_vs_billed_mismatch: {
    label: "실측↔청구 불일치",
    className: "bg-orange-100 text-orange-800 font-semibold",
  },
  size_mismatch_high: {
    label: "사이즈 불일치(추정)",
    className: "bg-amber-100 text-amber-800",
  },
  below_floor: {
    label: "최소금액 미달",
    className: "bg-purple-100 text-purple-700",
  },
  missing_dims: { label: "치수 미측정", className: "bg-gray-100 text-gray-600" },
  oversize: { label: "초과(입고불가)", className: "bg-gray-100 text-gray-600" },
  unit_unknown: { label: "수량 미확인", className: "bg-gray-100 text-gray-600" },
};

// 플래그 정렬 우선순위(강한 신호 먼저 렌더).
const FLAG_ORDER = [
  "billed_size_vs_amount_mismatch",
  "measured_vs_billed_mismatch",
  "size_mismatch_high",
  "below_floor",
  "missing_dims",
  "oversize",
  "unit_unknown",
];

function FlagBadges({ flags }: { flags: string[] }) {
  if (flags.length === 0) return <span className="text-gray-300 text-xs">—</span>;
  const sorted = [...flags].sort((a, b) => {
    const ia = FLAG_ORDER.indexOf(a);
    const ib = FLAG_ORDER.indexOf(b);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  });
  return (
    <div className="flex flex-wrap gap-1">
      {sorted.map((f) => {
        const meta = FLAG_META[f] ?? { label: f, className: "bg-gray-100 text-gray-600" };
        return (
          <span key={f} className={`px-1.5 py-0.5 rounded text-xs whitespace-nowrap ${meta.className}`}>
            {meta.label}
          </span>
        );
      })}
    </div>
  );
}

const SIZE_SOURCE_LABEL: Record<string, string> = {
  settlement_billed: "정산서 기재",
  coupang_measured: "물류센터 실측",
  registered_dims: "등록 치수(추정)",
};

function SizeSourceBadge({ source }: { source: string | null }) {
  if (!source) return null;
  const label = SIZE_SOURCE_LABEL[source] ?? source;
  const tone =
    source === "settlement_billed"
      ? "bg-emerald-100 text-emerald-700"
      : source === "coupang_measured"
      ? "bg-sky-100 text-sky-700"
      : "bg-gray-100 text-gray-500";
  return <span className={`px-1.5 py-0.5 rounded text-xs ${tone}`}>{label}</span>;
}

// divisor_source: "settlement" | "order_table" | "order_table+settlement" | null.
// 정산서 기재값(신뢰 높음) vs 주문테이블 추정(신뢰 낮음) — 단가 신뢰도 지표(S9).
function DivisorSourceBadge({ source }: { source: string | null }) {
  if (!source) return <span className="text-gray-300 text-xs">—</span>;
  if (source === "settlement") {
    return (
      <span className="px-1.5 py-0.5 rounded text-xs bg-emerald-100 text-emerald-700" title="정산서 기재값 — 추론 없음(신뢰 높음)">
        정산서 기재
      </span>
    );
  }
  return (
    <span className="px-1.5 py-0.5 rounded text-xs bg-amber-100 text-amber-700" title="주문테이블 추정 포함(신뢰 낮음)">
      주문테이블 추정{source.includes("settlement") ? "+정산서 일부" : ""}
    </span>
  );
}

function PeriodDetailRow({ p }: { p: RgFeeAuditPeriodDetail }) {
  return (
    <tr className="border-t border-gray-100 text-xs">
      <td className="px-3 py-1.5 text-gray-600">{p.date_from} ~ {p.date_to}</td>
      <td className="px-3 py-1.5 text-right">{won(p.delivery)}</td>
      <td className="px-3 py-1.5 text-right">{won(p.warehousing)}</td>
      <td className="px-3 py-1.5 text-right">{num(p.order_count)}</td>
      <td className="px-3 py-1.5 text-right">{num(p.quantity)}</td>
      <td className="px-3 py-1.5 text-center">
        {p.judged ? (
          <span className="text-emerald-600">판정됨</span>
        ) : (
          <span className="text-gray-400" title="주문이 이 주기에 대응되지 않아 판정에서 제외됨">
            미대응(제외)
          </span>
        )}
      </td>
      <td className="px-3 py-1.5 text-right">{p.per_unit_delivery != null ? won(p.per_unit_delivery) : "—"}</td>
      <td className="px-3 py-1.5">
        <FlagBadges flags={p.flags} />
      </td>
    </tr>
  );
}

function ItemRow({ item, expanded, onToggle }: {
  item: RgFeeAuditItem;
  expanded: boolean;
  onToggle: () => void;
}) {
  const sizeDiff = item.billed_vs_measured_size_diff;
  const hasStrongFlag = item.flags.includes("billed_size_vs_amount_mismatch");
  return (
    <>
      <tr
        className={`border-t border-gray-50 hover:bg-gray-50 cursor-pointer ${hasStrongFlag ? "bg-red-50/50" : ""}`}
        onClick={onToggle}
      >
        <td className="px-4 py-2 max-w-[260px]">
          <div
            className="truncate text-gray-900"
            title={item.product_name ? `${item.product_name}, ${item.item_name}` : item.item_name}
          >
            {item.product_name ? (
              <>{item.product_name}<span className="text-gray-400">, {item.item_name}</span></>
            ) : (
              item.item_name
            )}
          </div>
          <div className="text-xs text-gray-400">{item.vendor_item_id}</div>
        </td>
        <td className="px-3 py-2">
          <div className="flex flex-col gap-1">
            <div className="text-xs">
              <span className="text-gray-400">청구</span>{" "}
              <span className={sizeDiff ? "text-red-600 font-semibold" : "text-gray-700"}>
                {item.billed_size_type ?? "—"}
              </span>
            </div>
            <div className="text-xs">
              <span className="text-gray-400">실측</span>{" "}
              <span className={sizeDiff ? "text-red-600 font-semibold" : "text-gray-700"}>
                {item.measured_size_type ?? "—"}
              </span>
            </div>
            {sizeDiff && (
              <span className="px-1.5 py-0.5 rounded text-xs bg-red-100 text-red-700 w-fit">
                기재값 간 불일치
              </span>
            )}
            <SizeSourceBadge source={item.size_source} />
          </div>
        </td>
        <td className="px-3 py-2 text-right">
          <div className="font-medium">{item.per_unit_delivery != null ? won(item.per_unit_delivery) : "—"}</div>
          <div className="text-xs text-gray-400">
            floor {item.floor?.delivery != null ? won(item.floor.delivery) : "—"}
          </div>
          {item.implied_size_delivery && (
            <div className="text-xs text-gray-400">함의 {item.implied_size_delivery}</div>
          )}
        </td>
        <td className="px-3 py-2 text-center">
          <DivisorSourceBadge source={item.divisor_source} />
        </td>
        <td className="px-3 py-2 text-center text-xs">
          <span className={item.periods_unmatched > 0 ? "text-amber-600 font-medium" : "text-gray-600"}>
            {item.periods_judged}/{item.periods_total}
          </span>
          {item.periods_unmatched > 0 && (
            <div className="text-amber-500" title="일부 정산주기에 대응 주문이 없어 부분 표본입니다">
              부분표본
            </div>
          )}
        </td>
        <td className="px-3 py-2">
          <FlagBadges flags={item.flags} />
        </td>
        <td className="px-2 py-2 text-center text-gray-400">{expanded ? "▲" : "▼"}</td>
      </tr>
      {expanded && (
        <tr className="border-t border-gray-100 bg-gray-50/60">
          <td colSpan={7} className="px-4 py-3">
            <div className="text-xs text-gray-600 mb-2 flex flex-wrap gap-x-6 gap-y-1">
              <span>
                <span className="text-gray-400">청구총액(전 주기)</span>{" "}
                배송 {won(item.charged_delivery)} · 입출고 {won(item.charged_warehousing)}
              </span>
              <span>
                <span className="text-gray-400">판정액(단가의 분자)</span>{" "}
                배송 {won(item.judged_delivery)} · 입출고 {won(item.judged_warehousing)}
              </span>
            </div>
            {item.period_detail.length === 0 ? (
              <div className="text-xs text-gray-400">주기별 상세 없음</div>
            ) : (
              <table className="w-full text-xs bg-white rounded border border-gray-100 overflow-hidden">
                <thead className="bg-gray-100 text-gray-500">
                  <tr>
                    <th className="px-3 py-1.5 text-left">기간</th>
                    <th className="px-3 py-1.5 text-right">배송비 청구액</th>
                    <th className="px-3 py-1.5 text-right">입출고비 청구액</th>
                    <th className="px-3 py-1.5 text-right">주문수</th>
                    <th className="px-3 py-1.5 text-right">수량</th>
                    <th className="px-3 py-1.5 text-center">판정여부</th>
                    <th className="px-3 py-1.5 text-right">주기 단가</th>
                    <th className="px-3 py-1.5 text-left">주기 플래그</th>
                  </tr>
                </thead>
                <tbody>
                  {item.period_detail.map((p, i) => (
                    <PeriodDetailRow key={`${p.date_from}-${p.date_to}-${i}`} p={p} />
                  ))}
                </tbody>
              </table>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

// summary 배지 하나: 값이 0이면 회색으로 덜 두드러지게, 0 초과면 지정 톤.
function SummaryBadge({ label, value, tone }: { label: string; value: number; tone: string }) {
  const active = value > 0;
  return (
    <span
      className={`px-2 py-0.5 rounded text-xs whitespace-nowrap ${active ? tone : "bg-gray-100 text-gray-400"}`}
    >
      {label} {value}
    </span>
  );
}

export default function RgFeeAuditView({
  audit, loading, error,
}: {
  audit: RgFeeAudit | null;
  loading: boolean;
  error: string | null;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");

  function toggle(vii: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(vii)) next.delete(vii);
      else next.add(vii);
      return next;
    });
  }

  const rows = useMemo(() => {
    const items = audit?.items ?? [];
    const q = search.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (it) =>
        (it.product_name ?? "").toLowerCase().includes(q) ||
        it.item_name.toLowerCase().includes(q) ||
        it.vendor_item_id.toLowerCase().includes(q)
    );
  }, [audit, search]);

  if (error) {
    return (
      <div className="bg-red-50 text-red-700 rounded px-4 py-2 text-sm">
        청구 감사 로드 실패: {error}
      </div>
    );
  }

  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      {/* 헤더 — summary 배지 */}
      <div className="px-4 py-3 border-b border-gray-100 bg-orange-50 flex items-center gap-2 flex-wrap">
        <span className="text-sm font-semibold text-orange-800">🧾 RG 청구 감사</span>
        {audit && (
          <>
            <span className="text-xs text-gray-500">
              생성 {audit.generated_at}
              {audit.date_from ? ` · ${audit.date_from} ~ ${audit.date_to ?? "…"}` : ""}
            </span>
            <span className="px-2 py-0.5 rounded text-xs bg-gray-800 text-white font-medium">
              전체 {audit.summary.total_options} · 플래그 {audit.summary.flagged}
            </span>
            {/* 강한 신호 순: billed_size_vs_amount_mismatch(최강) → measured_vs_billed_mismatch
                → size_mismatch_high → below_floor */}
            <SummaryBadge
              label="정산서 자체모순"
              value={audit.summary.billed_size_vs_amount_mismatch}
              tone="bg-red-600 text-white font-semibold"
            />
            <SummaryBadge
              label="실측↔청구 불일치"
              value={audit.summary.measured_vs_billed_mismatch}
              tone="bg-orange-100 text-orange-800 font-semibold"
            />
            <SummaryBadge
              label="사이즈 불일치(추정)"
              value={audit.summary.size_mismatch_high}
              tone="bg-amber-100 text-amber-800"
            />
            <SummaryBadge
              label="최소금액 미달"
              value={audit.summary.below_floor}
              tone="bg-purple-100 text-purple-700"
            />
            <input
              className="ml-auto border border-gray-300 rounded px-3 py-1.5 text-sm w-48"
              placeholder="상품명·옵션ID 검색…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </>
        )}
      </div>

      {/* 부가 배지(참고 신호) */}
      {audit && (
        <div className="px-4 py-2 border-b border-gray-100 flex flex-wrap gap-1.5">
          <SummaryBadge label="기재값 간 불일치" value={audit.summary.billed_vs_measured_size_diff} tone="bg-red-100 text-red-700" />
          <SummaryBadge label="정산서 분모 확보" value={audit.summary.divisor_from_settlement} tone="bg-emerald-100 text-emerald-700" />
          <SummaryBadge label="치수 미측정" value={audit.summary.missing_dims} tone="bg-gray-200 text-gray-600" />
          <SummaryBadge label="수량 미확인" value={audit.summary.unit_unknown} tone="bg-gray-200 text-gray-600" />
          <SummaryBadge label="초과(입고불가)" value={audit.summary.oversize} tone="bg-gray-200 text-gray-600" />
          <SummaryBadge label="부분표본" value={audit.summary.coverage_partial} tone="bg-amber-100 text-amber-700" />
          <SummaryBadge label="판정불가(표본0)" value={audit.summary.coverage_none} tone="bg-gray-200 text-gray-600" />
          <SummaryBadge label="집계는 깨끗·주기이상" value={audit.summary.clean_but_period_outlier} tone="bg-sky-100 text-sky-700" />
        </div>
      )}

      {loading ? (
        <div className="px-4 py-6 text-center text-gray-400 text-sm">청구 감사 로딩 중…</div>
      ) : !audit || rows.length === 0 ? (
        <div className="px-4 py-6 text-center text-gray-400 text-sm">
          {!audit ? "데이터 없음" : search.trim() ? "검색 결과 없음" : "표시할 옵션 없음"}
        </div>
      ) : (
        <>
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-xs border-b border-gray-100">
              <tr>
                <th className="px-4 py-2 text-left">상품명</th>
                <th className="px-3 py-2 text-left">사이즈(청구/실측)</th>
                <th className="px-3 py-2 text-right">주문당 배송단가</th>
                <th className="px-3 py-2 text-center">분모 출처</th>
                <th className="px-3 py-2 text-center">표본(판정/전체)</th>
                <th className="px-3 py-2 text-left">플래그</th>
                <th className="px-2 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((it) => (
                <ItemRow
                  key={it.vendor_item_id}
                  item={it}
                  expanded={expanded.has(it.vendor_item_id)}
                  onToggle={() => toggle(it.vendor_item_id)}
                />
              ))}
            </tbody>
          </table>
          <div className="px-4 py-2 border-t border-gray-100 text-xs text-gray-400">
            전체 {audit.items.length}개{search.trim() && rows.length !== audit.items.length ? ` 중 ${rows.length}개 표시` : ""}
          </div>
        </>
      )}

      {audit && (
        <div className="px-4 py-2 border-t border-gray-100 bg-gray-50 text-[11px] leading-relaxed text-gray-500">
          {audit.disclaimer}
        </div>
      )}
    </div>
  );
}
