// CommandCenter.tsx — 🎯 종합 조망 (P7, D-2). 옵션ID 결합 엔진의 3축(회계·광고·상품) 뷰.
// D-3: 시스템은 사실/지표 정리만 — 전략 추천 없음. 해석은 Jino 몫.
import { useState, useEffect } from "react";
import {
  fetchCommandCenter,
  syncRealtime,
  type OverviewResponse,
  type RgSettlementByAccount,
} from "../lib/api";

function isoKST(d: Date): string {
  const kst = new Date(d.toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
  return `${kst.getFullYear()}-${String(kst.getMonth() + 1).padStart(2, "0")}-${String(kst.getDate()).padStart(2, "0")}`;
}
function won(s: string | null | undefined): string {
  if (s == null) return "—";
  return `${Math.round(Number(s)).toLocaleString("ko-KR")}원`;
}
function num(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString("ko-KR");
}
function ratioX(s: string | null): string {
  if (s == null) return "—";
  return `${Number(s).toFixed(2)}x`;
}
function ratioPct(s: string | null): string {
  if (s == null) return "—";
  return `${(Number(s) * 100).toFixed(2)}%`;
}

type Axis = "account" | "ad" | "product";

const QUICK = [
  { label: "어제", days: 1 },
  { label: "7일", days: 7 },
  { label: "14일", days: 14 },
  { label: "30일", days: 30 },
];

// S7(정합성 트랙 D-4): 계정별 분리 뷰 — 쿠팡 대시보드(계정별)와 1:1 대조의 전제.
// account_key는 env 매핑(WING1=오픽스 개인회사, WING2=오하이테크). 생략/ALL=전체(법인 합산).
const ACCOUNTS = [
  { value: "ALL", label: "전체" },
  { value: "COUPANG_WING1", label: "오픽스" },
  { value: "COUPANG_WING2", label: "오하이테크" },
];

export default function CommandCenter() {
  const today = isoKST(new Date());
  const ago = (n: number) => {
    const d = new Date();
    d.setDate(d.getDate() - (n - 1));
    return isoKST(d);
  };

  const [from, setFrom] = useState(ago(7));
  const [to, setTo] = useState(today);
  const [account, setAccount] = useState("ALL");
  const [axis, setAxis] = useState<Axis>("account");
  const [data, setData] = useState<OverviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);

  // 단일 fetch 코어 — from/to/account를 명시 인자로 받아 stale state 회피.
  function doFetch(f: string, t: string, acc: string) {
    setLoading(true);
    setError(null);
    fetchCommandCenter(f, t, acc)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }

  function load() {
    doFetch(from, to, account);
  }

  async function syncAndLoad() {
    setSyncing(true);
    try { await syncRealtime(); } catch { /* fail-soft */ }
    setSyncing(false);
    doFetch(from, to, account);
  }

  useEffect(() => {
    syncAndLoad();
  }, []);

  function applyQuick(days: number) {
    const f = ago(days);
    setFrom(f);
    setTo(today);
    doFetch(f, today, account);
  }

  // 계정 전환 시 즉시 재조회(현재 기간 유지).
  function applyAccount(acc: string) {
    setAccount(acc);
    doFetch(from, to, acc);
  }

  return (
    <div>
      <div className="mb-4 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">🎯 종합 조망</h1>
          <p className="text-sm text-gray-500">
            쿠팡 옵션ID 결합 — 회계·광고·상품 한눈에 (사실/지표만, 해석은 직접)
          </p>
        </div>
        <button
          onClick={syncAndLoad}
          disabled={syncing}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
        >
          <span className={syncing ? "animate-spin" : ""}>🔄</span>
          {syncing ? "동기화 중…" : "새로고침"}
        </button>
      </div>

      {/* 기간 선택 */}
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        {QUICK.map((q) => (
          <button
            key={q.label}
            onClick={() => applyQuick(q.days)}
            className="px-3 py-1 text-sm rounded-md border border-gray-300 bg-white hover:bg-gray-100"
          >
            {q.label}
          </button>
        ))}
        <input
          type="date"
          value={from}
          onChange={(e) => setFrom(e.target.value)}
          className="px-2 py-1 text-sm border border-gray-300 rounded-md"
        />
        <span className="text-gray-400">~</span>
        <input
          type="date"
          value={to}
          onChange={(e) => setTo(e.target.value)}
          className="px-2 py-1 text-sm border border-gray-300 rounded-md"
        />
        <button
          onClick={load}
          className="px-3 py-1 text-sm rounded-md bg-blue-600 text-white hover:bg-blue-700"
        >
          조회
        </button>
      </div>

      {/* 계정 선택 (S7, D-4) — 쿠팡 대시보드(계정별)와 1:1 대조 */}
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <span className="text-xs text-gray-400">계정</span>
        {ACCOUNTS.map((a) => (
          <button
            key={a.value}
            onClick={() => applyAccount(a.value)}
            className={`px-3 py-1 text-sm rounded-md border ${
              account === a.value
                ? "bg-blue-600 text-white border-blue-600"
                : "bg-white text-gray-700 border-gray-300 hover:bg-gray-100"
            }`}
          >
            {a.label}
          </button>
        ))}
      </div>

      {/* 축 탭 */}
      <div className="flex gap-1 mb-4 border-b border-gray-200">
        {([
          ["account", "💰 회계 (순이익)"],
          ["ad", "📈 광고 (사실)"],
          ["product", "📦 상품 (판매)"],
        ] as [Axis, string][]).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setAxis(key)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${
              axis === key
                ? "border-blue-600 text-blue-700"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {loading && <p className="text-gray-500">불러오는 중…</p>}
      {error && <p className="text-red-600">에러: {error}</p>}

      {data && !loading && (
        <>
          {axis === "account" && <AccountView data={data} />}
          {axis === "ad" && <AdView data={data} />}
          {axis === "product" && <ProductView data={data} />}
        </>
      )}
    </div>
  );
}

function Card({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-3">
      <div className="text-xs text-gray-500">{label}</div>
      <div className="text-lg font-bold text-gray-900">{value}</div>
      {sub && <div className="text-xs text-gray-400">{sub}</div>}
    </div>
  );
}

// S7(정합성 트랙 D-1·D-11): 매출·광고 분해 검산 패널 — 쿠팡 Wing 대시보드와 수동 1:1 대조용.
// 시스템은 사실/지표만(D-3) — 일치/불일치 판정은 Jino가 옆 화면과 눈으로 대조한다.
function ReconciliationCard({ data }: { data: OverviewResponse }) {
  const s = data.account.summary;
  const Row = ({ label, value, hint, gross }: { label: string; value: string; hint: string; gross?: boolean }) => (
    <div className="flex items-baseline justify-between py-1.5 border-b border-indigo-100 last:border-0">
      <div>
        <span className="text-sm text-gray-700">{label}</span>
        {gross && <span className="ml-1 text-xs text-indigo-400">(gross·취소 미차감)</span>}
        <div className="text-xs text-gray-400">{hint}</div>
      </div>
      <span className="text-sm font-semibold text-gray-900 tabular-nums">{won(value)}</span>
    </div>
  );
  return (
    <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-4 mb-4">
      <div className="text-sm font-semibold text-indigo-800 mb-2">
        📊 매출·광고 정합성 검산 — 쿠팡 Wing 대시보드와 수동 대조
      </div>
      <div className="grid md:grid-cols-2 gap-x-6">
        <div>
          <div className="text-xs font-medium text-indigo-500 mb-1">매출 (쿠팡 [판매분석])</div>
          <Row label="매출 합계 (3P+RG)" value={s.revenue} hint="판매분석 · 전체 매출" />
          <Row label="ㄴ 마켓플레이스 3P" value={s.revenue_3p ?? "0"} hint="판매분석 · 마켓플레이스" />
          <Row label="ㄴ 로켓그로스 RG" value={s.revenue_rg ?? "0"} hint="판매분석 · 로켓그로스" gross />
        </div>
        <div>
          <div className="text-xs font-medium text-indigo-500 mb-1 mt-3 md:mt-0">광고 (쿠팡 [광고센터])</div>
          <Row label="집행 광고비" value={s.ad_spend} hint="광고센터 · 집행 광고비(상품검색광고)" />
        </div>
      </div>
      <p className="text-xs text-indigo-600 mt-2 bg-indigo-100 rounded px-2 py-1">
        RG 매출은 주문 API 기준 <b>gross(취소 미차감)</b> — 쿠팡 판매분석의 net과 ~5% 차이는 기준 차이이며 계산 오류 아님(D-11).
        광고는 <b>상품검색광고</b>만 — 쿠팡 "전체 광고비"엔 비-상품검색 광고상품이 더 포함될 수 있음(S5).
      </p>
    </div>
  );
}

function RgSettlementCard({ data }: { data: OverviewResponse }) {
  const rg = data.rg_settlement;
  if (!rg) return null;
  if (!rg.summary.has_data) {
    return (
      <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mb-4">
        <span className="text-sm text-amber-700">🚧 RG 정산 비용(미반영) — 데이터 없음 (sync 필요)</span>
      </div>
    );
  }
  return (
    <div className="bg-orange-50 border border-orange-200 rounded-lg p-4 mb-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-semibold text-orange-800">✅ RG 정산 비용 — 순이익 반영됨 (계정 단위, 전액 차감)</span>
        <span className="text-right">
          {/* 헤드라인 = 실제 순이익 차감액 = RG 정산 총액(광고 포함, D-16). 부호 인식(Codex): 양수=차감(−), 음수=환급(+). */}
          {(() => {
            const d = Number(rg.summary.deducted ?? rg.summary.total);
            const sign = d < 0 ? "+" : "−";
            return <span className="text-base font-bold text-orange-900">{sign}{won(String(Math.abs(d)))}{d < 0 ? " (환급)" : ""}</span>;
          })()}
          <span className="block text-xs text-orange-500">광고 {won(rg.summary.ad_settlement ?? '0')} 포함 · 광고제외 {won(rg.summary.non_ad_deducted ?? '0')}</span>
        </span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
        {rg.by_account.map((a: RgSettlementByAccount) => (
          <div key={a.account_key} className="bg-white rounded border border-orange-100 p-2 text-xs">
            <div className="font-medium text-gray-700 mb-1">{a.account_key}</div>
            <div className="flex justify-between"><span className="text-gray-500">판매수수료</span><span>{won(a.sale_fee)}</span></div>
            <div className="flex justify-between"><span className="text-gray-500">풀필먼트(배송·입출고·보관)</span><span>{won(a.fulfillment)}</span></div>
            <div className="flex justify-between"><span className="text-gray-500">반품비</span><span>{won(a.return_fee)}</span></div>
            <div className="flex justify-between"><span className="text-gray-500">광고비<span className="text-orange-400">*</span></span><span>{won(a.ad_sales)}</span></div>
            {Number(a.other) !== 0 && (
              <div className="flex justify-between text-red-600"><span>기타(미매핑)</span><span>{won(a.other)}</span></div>
            )}
            <div className="flex justify-between font-semibold border-t border-orange-100 mt-1 pt-1"><span>합계</span><span>{won(a.total)}</span></div>
          </div>
        ))}
      </div>
      <div className="text-xs text-orange-700 mt-2 bg-orange-100 rounded px-2 py-1">
        정산주기 기준(부분 윈도우도 주기 전액). RG 광고비 {won(rg.summary.ad_settlement ?? '0')}는 광고센터 보고서에 없고 정산에만 있어 전액 차감에 포함(D-16, 라이브 조사).
      </div>
      <p className="text-xs text-orange-600 mt-2">
        ✅ 순이익에 반영됨(계정 단위, RG 정산 총액 전액 차감, D-14/D-16).
        <span className="text-orange-400"> *</span>RG 광고비는 광고센터 PA 보고서에 잡히지 않고 RG 정산에만 존재(라이브 조사) → 전액 차감에 포함.
      </p>
    </div>
  );
}

function AccountView({ data }: { data: OverviewResponse }) {
  const s = data.account.summary;
  return (
    <>
      <ReconciliationCard data={data} />
      <RgSettlementCard data={data} />
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <Card label="매출" value={won(s.revenue)} />
        <Card label="반품 차감" value={won(s.return_deduction)} />
        <Card label="수수료(+VAT)" value={won(s.total_fee)} />
        <Card label="광고비" value={won(s.ad_spend)} />
        <Card label="원가" value={won(s.cost)} sub={`원가반영 ${s.cost_covered_options}/${s.option_count}옵션`} />
        <Card
          label="순이익"
          value={won(s.net_profit)}
          sub={
            s.rg_flip_status === "applied_full"
              ? `플립전 ${won(s.net_profit_pre_rg ?? "0")} − RG정산 ${won(s.rg_settlement_total ?? "0")}(전액)`
              : "매출−반품−수수료−광고−원가 (RG 정산 데이터 없음)"
          }
        />
      </div>
      <table className="w-full text-sm bg-white rounded-lg border border-gray-200">
        <thead>
          <tr className="text-left text-gray-500 border-b border-gray-200">
            <th className="px-3 py-2">옵션 / 상품</th>
            <th className="px-3 py-2 text-right">매출</th>
            <th className="px-3 py-2 text-right">반품차감</th>
            <th className="px-3 py-2 text-right">수수료</th>
            <th className="px-3 py-2 text-right">광고비</th>
            <th className="px-3 py-2 text-right">원가</th>
            <th className="px-3 py-2 text-right">순이익</th>
          </tr>
        </thead>
        <tbody>
          {data.account.by_option.map((r) => (
            <tr key={r.vendor_item_id} className="border-b border-gray-100 hover:bg-gray-50">
              <td className="px-3 py-2">
                <div className="text-gray-900">{r.name}</div>
                <div className="text-xs text-gray-400">{r.vendor_item_id}</div>
              </td>
              <td className="px-3 py-2 text-right">{won(r.revenue)}</td>
              <td className="px-3 py-2 text-right text-gray-500">{won(r.return_deduction)}</td>
              <td className="px-3 py-2 text-right text-gray-500">{won(r.total_fee)}</td>
              <td className="px-3 py-2 text-right text-gray-500">{won(r.ad_spend)}</td>
              <td className="px-3 py-2 text-right text-gray-500">
                {r.has_cost ? won(r.cost) : <span className="text-amber-500">원가 미설정</span>}
              </td>
              <td className={`px-3 py-2 text-right font-semibold ${Number(r.net_profit) < 0 ? "text-red-600" : "text-gray-900"}`}>
                {won(r.net_profit)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function AdView({ data }: { data: OverviewResponse }) {
  const s = data.ad.summary;
  return (
    <>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
        <Card label="광고비" value={won(s.ad_spend)} />
        <Card label="노출수" value={num(s.impressions)} />
        <Card label="클릭수" value={num(s.clicks)} />
        <Card label="전환매출" value={won(s.conv_revenue)} />
        <Card label="ROAS" value={ratioX(s.roas)} />
      </div>
      <table className="w-full text-sm bg-white rounded-lg border border-gray-200">
        <thead>
          <tr className="text-left text-gray-500 border-b border-gray-200">
            <th className="px-3 py-2">옵션 / 상품</th>
            <th className="px-3 py-2 text-right">광고비</th>
            <th className="px-3 py-2 text-right">노출</th>
            <th className="px-3 py-2 text-right">클릭</th>
            <th className="px-3 py-2 text-right">CTR</th>
            <th className="px-3 py-2 text-right">전환매출</th>
            <th className="px-3 py-2 text-right">ROAS</th>
          </tr>
        </thead>
        <tbody>
          {data.ad.by_option
            .filter((r) => Number(r.ad_spend) > 0 || Number(r.conv_revenue) > 0)
            .map((r) => (
              <tr key={r.vendor_item_id} className="border-b border-gray-100 hover:bg-gray-50">
                <td className="px-3 py-2">
                  <div className="text-gray-900">{r.name}</div>
                  <div className="text-xs text-gray-400">{r.vendor_item_id}</div>
                </td>
                <td className="px-3 py-2 text-right">{won(r.ad_spend)}</td>
                <td className="px-3 py-2 text-right text-gray-500">{num(r.impressions)}</td>
                <td className="px-3 py-2 text-right text-gray-500">{num(r.clicks)}</td>
                <td className="px-3 py-2 text-right text-gray-500">{ratioPct(r.ctr)}</td>
                <td className="px-3 py-2 text-right">{won(r.conv_revenue)}</td>
                <td className="px-3 py-2 text-right font-semibold">{ratioX(r.roas)}</td>
              </tr>
            ))}
        </tbody>
      </table>
    </>
  );
}

function ProductView({ data }: { data: OverviewResponse }) {
  const s = data.product.summary;
  return (
    <>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <Card label="옵션 수" value={num(s.option_count)} />
        <Card label="주문 건수" value={num(s.order_count)} />
        <Card label="판매 수량" value={num(s.order_qty)} />
        <Card label="반품 수량" value={num(s.return_qty)} />
      </div>
      <table className="w-full text-sm bg-white rounded-lg border border-gray-200">
        <thead>
          <tr className="text-left text-gray-500 border-b border-gray-200">
            <th className="px-3 py-2">옵션 / 상품</th>
            <th className="px-3 py-2 text-right">판매가</th>
            <th className="px-3 py-2 text-right">주문</th>
            <th className="px-3 py-2 text-right">수량</th>
            <th className="px-3 py-2 text-right">반품</th>
            <th className="px-3 py-2 text-right">반품률</th>
            <th className="px-3 py-2 text-right">재고</th>
            <th className="px-3 py-2 text-center">상태</th>
          </tr>
        </thead>
        <tbody>
          {data.product.by_option.map((r) => (
            <tr key={r.vendor_item_id} className="border-b border-gray-100 hover:bg-gray-50">
              <td className="px-3 py-2">
                <div className="text-gray-900">{r.name}</div>
                <div className="text-xs text-gray-400">{r.vendor_item_id}</div>
              </td>
              <td className="px-3 py-2 text-right text-gray-500">{won(r.sale_price)}</td>
              <td className="px-3 py-2 text-right">{num(r.order_count)}</td>
              <td className="px-3 py-2 text-right text-gray-500">{num(r.order_qty)}</td>
              <td className="px-3 py-2 text-right text-gray-500">{num(r.return_qty)}</td>
              <td className="px-3 py-2 text-right text-gray-500">{ratioPct(r.return_rate)}</td>
              <td className="px-3 py-2 text-right text-gray-500">{r.stock == null ? "—" : num(r.stock)}</td>
              <td className="px-3 py-2 text-center text-xs">
                {r.in_master ? (
                  <span className={r.on_sale ? "text-green-600" : "text-gray-400"}>
                    {r.status_name || (r.on_sale ? "판매중" : "—")}
                  </span>
                ) : (
                  <span className="text-amber-500">마스터 없음</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
