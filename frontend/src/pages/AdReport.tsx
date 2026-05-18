// AdReport.tsx — 쿠팡 광고 리포트 (기간별 성과 표)
import { useState, useEffect } from "react";
import {
  fetchCoupangAdReport,
  type CoupangAdReportRow,
  type CoupangAdReportResponse,
} from "../lib/api";

function isoKST(d: Date): string {
  const kst = new Date(d.toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
  return `${kst.getFullYear()}-${String(kst.getMonth() + 1).padStart(2, "0")}-${String(kst.getDate()).padStart(2, "0")}`;
}

function fmt(n: number): string {
  return n.toLocaleString("ko-KR");
}

function pct(n: number): string {
  return `${n.toFixed(2)}%`;
}

type ColDef = {
  key: keyof CoupangAdReportRow | "sell_type";
  label: string;
  align: "left" | "right";
  fmt?: (n: number) => string;
};

const COLS: ColDef[] = [
  { key: "sell_type",           label: "판매방식",          align: "left"  },
  { key: "impressions",         label: "노출수",            align: "right", fmt: fmt },
  { key: "clicks",              label: "클릭수",            align: "right", fmt: fmt },
  { key: "ctr",                 label: "클릭률",            align: "right", fmt: pct },
  { key: "orders",              label: "주문수",            align: "right", fmt: fmt },
  { key: "sales_qty",           label: "판매수",            align: "right", fmt: fmt },
  { key: "cvr",                 label: "전환율",            align: "right", fmt: pct },
  { key: "ad_spend",            label: "광고비",            align: "right", fmt: (n) => `${fmt(n)}원` },
  { key: "conversion_revenue",  label: "광고전환매출",      align: "right", fmt: (n) => `${fmt(n)}원` },
  { key: "roas",                label: "광고수익률(ROAS)",  align: "right", fmt: (n) => `${n.toFixed(1)}%` },
];

export default function AdReport() {
  const today = isoKST(new Date());
  const firstOfMonth = today.slice(0, 8) + "01";

  const [dateFrom, setDateFrom] = useState(firstOfMonth);
  const [dateTo, setDateTo] = useState(today);
  const [report, setReport] = useState<CoupangAdReportResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function load() {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchCoupangAdReport(dateFrom, dateTo);
      setReport(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  function ReportRow({ row, isTotal }: { row: CoupangAdReportRow; isTotal?: boolean }) {
    return (
      <tr className={isTotal ? "bg-gray-50 font-semibold border-t-2 border-gray-300" : "hover:bg-gray-50"}>
        {COLS.map((col) => {
          const val = row[col.key as keyof CoupangAdReportRow];
          const display = col.fmt && typeof val === "number" ? col.fmt(val) : String(val);
          return (
            <td
              key={col.key}
              className={`px-4 py-2.5 text-sm border-b border-gray-100 ${col.align === "right" ? "text-right tabular-nums" : "text-left"}`}
            >
              {display}
            </td>
          );
        })}
      </tr>
    );
  }

  const hasData = report && (report.items.length > 0 || report.total.impressions > 0);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">쿠팡 광고 리포트</h1>
        <p className="text-xs text-gray-400">설정 페이지에서 XLSX 업로드 시 자동 저장됩니다.</p>
      </div>

      {/* 기간 필터 */}
      <div className="bg-white rounded-lg border border-gray-200 p-4 flex items-center gap-4">
        <span className="text-sm text-gray-600">조회 기간</span>
        <input
          type="date"
          value={dateFrom}
          onChange={(e) => setDateFrom(e.target.value)}
          className="text-sm border border-gray-300 rounded px-2 py-1"
        />
        <span className="text-gray-400">~</span>
        <input
          type="date"
          value={dateTo}
          onChange={(e) => setDateTo(e.target.value)}
          className="text-sm border border-gray-300 rounded px-2 py-1"
        />
        <button
          onClick={load}
          className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700"
        >
          조회
        </button>
        {/* 빠른 선택 */}
        {[
          { label: "이번 달", from: firstOfMonth, to: today },
          { label: "지난 7일", from: isoKST(new Date(Date.now() - 6 * 86400000)), to: today },
          { label: "지난 30일", from: isoKST(new Date(Date.now() - 29 * 86400000)), to: today },
        ].map((q) => (
          <button
            key={q.label}
            onClick={() => { setDateFrom(q.from); setDateTo(q.to); }}
            className="px-2 py-1 text-xs text-blue-600 border border-blue-200 rounded hover:bg-blue-50"
          >
            {q.label}
          </button>
        ))}
      </div>

      {/* 테이블 */}
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-gray-400 text-sm">불러오는 중...</div>
        ) : error ? (
          <div className="p-8 text-center text-red-500 text-sm">{error}</div>
        ) : !hasData ? (
          <div className="p-8 text-center text-gray-400 text-sm">
            해당 기간에 광고 데이터가 없습니다.
            <br />
            <span className="text-xs text-gray-400">설정 → 쿠팡 광고비 업로드에서 pa_daily_keyword XLSX를 업로드하세요.</span>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200">
                  {COLS.map((col) => (
                    <th
                      key={col.key}
                      className={`px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider ${col.align === "right" ? "text-right" : "text-left"}`}
                    >
                      {col.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {report!.items.map((row) => (
                  <ReportRow key={row.sell_type} row={row} />
                ))}
                <ReportRow row={report!.total} isTotal />
              </tbody>
            </table>
          </div>
        )}
      </div>

      <p className="text-xs text-gray-400">
        광고전환매출 · ROAS는 쿠팡 광고 XLSX의 총 전환매출액(1일) 기준입니다.
      </p>
    </div>
  );
}
