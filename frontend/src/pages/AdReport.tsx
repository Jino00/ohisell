// AdReport.tsx — 쿠팡 광고 리포트 (기간별 성과 표)
import { useState, useEffect, useRef } from "react";
import {
  fetchCoupangAdReport,
  syncRealtime,
  type CoupangAdReportRow,
  type CoupangAdReportResponse,
} from "../lib/api";

function isoKST(d: Date): string {
  const kst = new Date(d.toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
  return `${kst.getFullYear()}-${String(kst.getMonth() + 1).padStart(2, "0")}-${String(kst.getDate()).padStart(2, "0")}`;
}

/** 오늘 포함 n일치의 시작일(KST). n=7 → 오늘−6일. */
function daysAgoKST(n: number): string {
  return isoKST(new Date(Date.now() - (n - 1) * 86400000));
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
  // ★축은 별도 열로 낸다 — 같은 숫자처럼 보이는 것을 막는 가장 싼 방법이다.
  { key: "roas_basis_label",    label: "ROAS 기준",         align: "left"  },
];

// 컴포넌트 밖에 둔다 — 렌더마다 새 컴포넌트 타입을 만들면 표 전체가 언마운트/재마운트된다
// (react-hooks/static-components). props·모듈 상수(COLS)만 쓰므로 순수 이동이다.
function ReportRow({ row, isTotal }: { row: CoupangAdReportRow; isTotal?: boolean }) {
  return (
    <tr className={isTotal ? "bg-gray-50 font-semibold border-t-2 border-gray-300" : "hover:bg-gray-50"}>
      {COLS.map((col) => {
        const val = row[col.key as keyof CoupangAdReportRow];
        // ★없는 값을 "undefined"로 그리지 않는다 — 구버전 백엔드/캐시 응답이면 축이 빠질 수 있다.
        const display =
          val == null ? "—" : col.fmt && typeof val === "number" ? col.fmt(val) : String(val);
        return (
          <td
            key={col.key}
            className={`px-4 py-2.5 text-sm border-b border-gray-100 ${col.align === "right" ? "text-right tabular-nums" : "text-left"} ${
              col.key === "roas_basis_label" && row.roas_basis !== "our_revenue"
                ? "text-amber-700"   // 우리 매출 축이 아닌 행은 눈에 띄게 — 비교하면 안 되는 값이다
                : ""
            }`}
          >
            {display}
          </td>
        );
      })}
    </tr>
  );
}

export default function AdReport() {
  const today = isoKST(new Date());
  const firstOfMonth = today.slice(0, 8) + "01";

  const [dateFrom, setDateFrom] = useState(firstOfMonth);
  const [dateTo, setDateTo] = useState(today);
  const [report, setReport] = useState<CoupangAdReportResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);

  // 요청 순서 가드 — 기간을 빠르게 바꾸면 이전 요청이 늦게 도착해 옛 기간 표를 새 선택
  // 화면에 렌더할 수 있다. 최신 요청의 응답만 반영한다(CommandCenter와 같은 패턴).
  const reqSeq = useRef(0);
  // 지연 콜백(동기화 완료)이 "현재 조회 중인 기간"을 다시 읽기 위한 ref.
  // 클로저로 캡처한 stale 기간으로 재조회해 현재 화면을 덮는 버그 회피(issue #235).
  const selRef = useRef({ from: dateFrom, to: dateTo });

  // 단일 fetch 코어 — 기간을 명시 인자로 받아 stale state 회피.
  function doFetch(f: string, t: string) {
    const seq = ++reqSeq.current;
    selRef.current = { from: f, to: t }; // 최신 '조회된' 선택 기록(동기화 완료 후 재조회용)
    setLoading(true);
    setError(null);
    fetchCoupangAdReport(f, t)
      .then((d) => { if (seq === reqSeq.current) setReport(d); })
      .catch((e) => { if (seq === reqSeq.current) setError(e.message); })
      .finally(() => { if (seq === reqSeq.current) setLoading(false); });
  }

  function load() {
    doFetch(dateFrom, dateTo);
  }

  async function syncAndLoad() {
    setSyncing(true);
    try { await syncRealtime(); } catch { /* fail-soft */ }
    setSyncing(false);
    // ★대기 중 사용자가 기간을 바꿔 조회했을 수 있음 → 현재 선택(selRef)으로 재조회.
    // 여기서 클로저 dateFrom/dateTo를 쓰면 방금 조회한 기간을 마운트 시점 값으로 덮는다.
    // 창이 넓다: prod 실측 POST /api/sync/realtime = 29.9~32.9초(2026-08-07 13:01 KST, 3회).
    const sel = selRef.current;
    doFetch(sel.from, sel.to);
  }

  useEffect(() => { syncAndLoad(); }, []);

  const hasData = report && (report.items.length > 0 || report.total.impressions > 0);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">쿠팡 광고 리포트</h1>
        <div className="flex items-center gap-3">
          <p className="text-xs text-gray-400">설정 페이지에서 XLSX 업로드 시 자동 저장됩니다.</p>
          <button
            onClick={syncAndLoad}
            disabled={syncing}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            <span className={syncing ? "animate-spin" : ""}>🔄</span>
            {syncing ? "동기화 중…" : "새로고침"}
          </button>
        </div>
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
        {/* 빠른 선택 — 날짜는 **클릭 시점**에 계산한다(렌더 중 Date.now = react-hooks/purity 에러).
            값은 종전과 같다: 7일=오늘 포함 7일치(−6일), 30일=−29일. */}
        {[
          { label: "이번 달", from: () => firstOfMonth },
          { label: "지난 7일", from: () => daysAgoKST(7) },
          { label: "지난 30일", from: () => daysAgoKST(30) },
        ].map((q) => (
          <button
            key={q.label}
            onClick={() => { setDateFrom(q.from()); setDateTo(today); }}
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

      <div className="space-y-1 text-xs text-gray-400">
        <p>광고전환매출 · ROAS는 총 전환매출액(1일) 기준입니다.</p>
        <p className="text-amber-700">
          ⚠️ <strong className="font-medium">행끼리 ROAS를 비교하지 마세요 — 축이 다릅니다.</strong>{" "}
          윙·로켓그로스는 우리가 소비자에게 직접 팔아 전환매출이 <b>우리 매출</b>이지만,
          로켓배송(1P)은 쿠팡이 사입해 자기 가격으로 팔아 전환매출이 <b>쿠팡 매출</b>입니다.
          1P를 우리 납품가로 환산하면 표시값의 약 60% 수준이 됩니다(실측 2026-08-04 우리 몫 59.45%).
          합계 행은 그 둘을 더한 값이라 어느 축으로도 해석되지 않습니다.
        </p>
        <p>
          환산값을 대신 보여주지 않는 이유: 우리 몫 비율은 기간마다 다르고 판매분석이 롤링 약
          2개월만 덮어 과거 구간은 환산할 근거가 없습니다 — 추정하느니 축을 밝힙니다.
          납품가 기준 1P 성과는 「쿠팡 로켓배송(1P) → 매출」 화면에서 봅니다.
        </p>
      </div>
    </div>
  );
}
