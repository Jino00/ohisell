// CommandCenter.tsx — 🎯 종합 조망 (P7, D-2). 옵션ID 결합 엔진의 3축(회계·광고·상품) 뷰.
// D-3: 시스템은 사실/지표 정리만 — 전략 추천 없음. 해석은 Jino 몫.
import { useState, useEffect, useRef } from "react";
import {
  fetchCommandCenter,
  fetchRevenueReconcile,
  requestWingVendorSummaryRefresh,
  getWingVendorSummaryRefreshStatus,
  requestWingRgSettlementRefresh,
  getWingRgSettlementRefreshStatus,
  syncRealtime,
  type OverviewResponse,
  type RevenueReconcile,
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
  const [reconcile, setReconcile] = useState<RevenueReconcile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  // 판매분석(vendor-summary) "갱신 버튼" 상태 — 광고비 버튼과 동일 패턴.
  const [salesRefreshing, setSalesRefreshing] = useState(false);
  const [salesRefreshMsg, setSalesRefreshMsg] = useState<string | null>(null);
  // RG 정산 "갱신 버튼" 상태 — vendor-summary 갱신 버튼과 동일 패턴.
  const [rgRefreshing, setRgRefreshing] = useState(false);
  const [rgRefreshMsg, setRgRefreshMsg] = useState<string | null>(null);
  // 요청 순서 가드(codex S7 P1): 계정/기간을 빠르게 바꾸면 이전 요청이 늦게 도착해
  // 새 선택 화면에 엉뚱한 계정 데이터를 렌더할 수 있다. 검산(reconciliation) 도구라
  // '다른 계정 숫자 표시'는 막으려는 실패 그 자체 → 최신 요청 응답만 반영한다.
  const reqSeq = useRef(0);
  // 갱신 버튼 완료 시점에 "현재" 선택을 다시 읽기 위한 ref — 클로저로 캡처한 stale
  // from/to/account로 재조회해 현재 화면을 덮어쓰는 버그 회피(codex S3 P1).
  const selRef = useRef({ from, to, account });

  // 단일 fetch 코어 — from/to/account를 명시 인자로 받아 stale state 회피.
  // 종합조망(command-center)과 매출 정합성(revenue-reconcile)을 같은 seq로 병렬 조회한다.
  function doFetch(f: string, t: string, acc: string) {
    const seq = ++reqSeq.current;
    selRef.current = { from: f, to: t, account: acc }; // 최신 선택 기록(갱신 완료 후 재조회용)
    setLoading(true);
    setError(null);
    // 이전 계정/기간의 드리프트 카드 잔상 제거 — 검산 surface라 stale 표시는 정확성 버그(codex S3 P1).
    setReconcile(null);
    fetchCommandCenter(f, t, acc)
      .then((d) => { if (seq === reqSeq.current) setData(d); })
      .catch((e) => { if (seq === reqSeq.current) setError(e.message); })
      .finally(() => { if (seq === reqSeq.current) setLoading(false); });
    // reconcile은 보조 지표 — 실패해도 종합조망 본체는 막지 않는다(fail-soft).
    fetchRevenueReconcile(f, t, acc)
      .then((r) => { if (seq === reqSeq.current) setReconcile(r); })
      .catch(() => { if (seq === reqSeq.current) setReconcile(null); });
  }

  // "판매분석 갱신" — Mac Wing 데몬(com.ohisell.wing)을 깨워 쿠팡 공식 GMV를 즉시 가져온다
  // (cf_clearance로 prod 직접 fetch 불가). request-refresh → 데몬 fetch·push →
  // last_success_at 변화를 폴링해 완료 감지 → reconcile 리로드.
  async function refreshSalesAnalysisNow() {
    setSalesRefreshing(true);
    setSalesRefreshMsg("Mac에서 판매분석 가져오는 중… (~20초, 첫 갱신이면 Mac 로그인 창 확인)");
    try {
      const baseline = (await getWingVendorSummaryRefreshStatus()).last_success_at;
      await requestWingVendorSummaryRefresh();
      const deadline = Date.now() + 215000; // 215초 — 데몬 로그인 대기(180s)+fetch 여유까지 커버
      let done = false;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 3000));
        const st = await getWingVendorSummaryRefreshStatus();
        if (st.last_success_at && st.last_success_at !== baseline) { done = true; break; }
      }
      if (done) {
        // 대기 중 사용자가 계정/기간을 바꿨을 수 있음 → 현재 선택(selRef)으로 재조회(codex S3 P1).
        const sel = selRef.current;
        doFetch(sel.from, sel.to, sel.account);
        setSalesRefreshMsg("✅ 판매분석 갱신 완료");
        setTimeout(() => setSalesRefreshMsg(null), 4000);
      } else {
        setSalesRefreshMsg("⚠️ Mac 응답 없음 — Mac이 켜져 있는지, 첫 갱신이면 로그인 창을 확인하세요.");
      }
    } catch (e: any) {
      setSalesRefreshMsg("❌ 갱신 요청 실패: " + (e?.message || ""));
    } finally {
      setSalesRefreshing(false);
    }
  }

  // "RG 정산 갱신" — Mac Wing 데몬(com.ohisell.wing)이 RG 정산 XLSX를 다운로드·push.
  async function refreshRgSettlementNow() {
    setRgRefreshing(true);
    setRgRefreshMsg("Mac에서 RG 정산 가져오는 중… (~30초)");
    try {
      const baseline = (await getWingRgSettlementRefreshStatus()).last_success_at;
      await requestWingRgSettlementRefresh();
      const deadline = Date.now() + 215000;
      let done = false;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 3000));
        const st = await getWingRgSettlementRefreshStatus();
        if (st.last_success_at && st.last_success_at !== baseline) { done = true; break; }
      }
      if (done) {
        const sel = selRef.current;
        doFetch(sel.from, sel.to, sel.account);
        setRgRefreshMsg("✅ RG 정산 갱신 완료");
        setTimeout(() => setRgRefreshMsg(null), 4000);
      } else {
        setRgRefreshMsg("⚠️ Mac 응답 없음 — Mac이 켜져 있는지 확인하세요.");
      }
    } catch (e: any) {
      setRgRefreshMsg("❌ 갱신 요청 실패: " + (e?.message || ""));
    } finally {
      setRgRefreshing(false);
    }
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
          {axis === "account" && (
            <AccountView
              data={data}
              reconcile={reconcile}
              onRefreshSales={refreshSalesAnalysisNow}
              salesRefreshing={salesRefreshing}
              salesRefreshMsg={salesRefreshMsg}
              onRefreshRg={refreshRgSettlementNow}
              rgRefreshing={rgRefreshing}
              rgRefreshMsg={rgRefreshMsg}
            />
          )}
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
  const a = data.ad.summary;
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
          <Row label="전체 광고비 (ALL)" value={a.ad_confirmed_total ?? s.ad_spend} hint="광고센터 · 전체 광고비(비-PA 포함) · net_profit 차감 기준" />
          <Row label="ㄴ 집행 (상품검색광고/PA)" value={a.ad_confirmed_pa ?? s.ad_spend} hint="광고센터 · 집행 광고비(DELIVERED)" />
          <Row label="ㄴ 비-PA (브랜드/디스플레이)" value={a.ad_confirmed_nonpa ?? "0"} hint="전체−집행 · net_profit에 추가 차감 반영(D-15)" />
        </div>
      </div>
      <p className="text-xs text-indigo-600 mt-2 bg-indigo-100 rounded px-2 py-1">
        RG 매출은 주문 API 기준 <b>gross(취소 미차감)</b> — 쿠팡 판매분석의 net과 ~5% 차이는 기준 차이이며 계산 오류 아님(D-11).
        광고비는 쿠팡 <b>"전체 광고비"(ALL)</b>로 순이익에서 차감 — 집행(상품검색광고)+비-PA(브랜드/디스플레이)로 분해 표시(D-15).
      </p>
    </div>
  );
}

// S3(Wing 세션 자동화 트랙): 쿠팡 공식 GMV(판매분석 vendor-summary) 자동 대조 — 드리프트%.
// 우리 매출(revenue_3p/rg) vs 쿠팡 공식 GMV를 닫힌 과거일 기준으로 비교(D-3). 사실·지표만(D-2) —
// 일치/불일치 판정·전략 추천 없음. 임계 색상은 사실의 크기 강조일 뿐. 권위값은 계정 지정+완전 적재일 때만(D-7).
function RevenueDriftCard({
  reconcile,
  onRefresh,
  refreshing,
  msg,
}: {
  reconcile: RevenueReconcile | null;
  onRefresh: () => void;
  refreshing: boolean;
  msg: string | null;
}) {
  // 드리프트% 포맷 — official 0이면 백엔드가 null로 준다.
  const pctFmt = (p: string | null): string => {
    if (p == null) return "—";
    const v = Number(p);
    const sign = v > 0 ? "+" : "";
    return `${sign}${v.toFixed(2)}%`;
  };
  // 임계 색상(사실 크기 강조, 추천 아님): |드리프트|<5% 회색, 5~10% 주황, ≥10% 빨강.
  const pctColor = (p: string | null): string => {
    if (p == null) return "text-gray-400";
    const a = Math.abs(Number(p));
    if (a >= 10) return "text-red-600";
    if (a >= 5) return "text-amber-600";
    return "text-gray-700";
  };

  // 권위/참고치 라벨(D-7): 집계(account 없음) 또는 부분 적재면 "참고치", 계정 지정+완전 적재면 "권위값".
  const acc = reconcile?.period.account;
  const complete = reconcile?.coverage?.complete ?? false;
  const isReference = !acc || !complete;

  const RefreshButton = (
    <button
      onClick={onRefresh}
      disabled={refreshing}
      className="flex items-center gap-1.5 px-2.5 py-1 text-xs bg-violet-600 text-white rounded-md hover:bg-violet-700 disabled:opacity-50"
    >
      <span className={refreshing ? "animate-spin" : ""}>🔄</span>
      {refreshing ? "갱신 중…" : "판매분석 갱신"}
    </button>
  );

  return (
    <div className="bg-violet-50 border border-violet-200 rounded-lg p-4 mb-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-semibold text-violet-800">
          🔬 매출 자동 대조 — 쿠팡 공식 GMV(판매분석) vs 우리 매출
        </span>
        {RefreshButton}
      </div>

      {msg && (
        <div className="text-xs text-violet-700 bg-violet-100 rounded px-2 py-1 mb-2">{msg}</div>
      )}

      {!reconcile && <p className="text-xs text-violet-500">불러오는 중…</p>}

      {reconcile && !reconcile.has_closed_days && (
        <p className="text-xs text-violet-600">
          {reconcile.note}
        </p>
      )}

      {reconcile && reconcile.has_closed_days && !reconcile.has_official && (
        <p className="text-xs text-violet-600">
          쿠팡 공식 판매분석 데이터가 없습니다 — 위 <b>'판매분석 갱신'</b> 버튼으로 Mac 페처가 가져오게 하세요.
          (닫힌 과거일 {reconcile.period.from} ~ {reconcile.period.closed_through} 기준)
        </p>
      )}

      {reconcile && reconcile.has_official && reconcile.official && reconcile.ours && reconcile.drift && (
        <>
          <div className="flex items-center gap-2 mb-1.5">
            <span
              className={`text-xs font-medium px-1.5 py-0.5 rounded ${
                isReference
                  ? "bg-amber-100 text-amber-700"
                  : "bg-emerald-100 text-emerald-700"
              }`}
            >
              {isReference ? "참고치" : "권위값"}
            </span>
            <span className="text-xs text-gray-500">
              닫힌 과거일 {reconcile.period.from} ~ {reconcile.period.closed_through}
              {reconcile.coverage && ` · 적재 ${reconcile.coverage.days_with_data}/${reconcile.coverage.expected_days}일`}
              {!acc && " · 계정 전체(집계)"}
            </span>
          </div>

          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-violet-500 border-b border-violet-200">
                <th className="py-1 font-medium">구분</th>
                <th className="py-1 text-right font-medium">우리 매출</th>
                <th className="py-1 text-right font-medium">쿠팡 공식 GMV</th>
                <th className="py-1 text-right font-medium">차이</th>
                <th className="py-1 text-right font-medium">드리프트%</th>
              </tr>
            </thead>
            <tbody className="tabular-nums">
              {([
                ["마켓플레이스 3P", reconcile.ours.revenue_3p, reconcile.official.gmv_3p, reconcile.drift.abs_3p, reconcile.drift.pct_3p],
                ["로켓그로스 RG", reconcile.ours.revenue_rg, reconcile.official.gmv_rg, reconcile.drift.abs_rg, reconcile.drift.pct_rg],
                ["합계", reconcile.ours.revenue_total, reconcile.official.gmv_total, reconcile.drift.abs_total, reconcile.drift.pct_total],
              ] as [string, string, number, string, string | null][]).map(([label, ours, official, abs, pct], i, arr) => (
                <tr key={label} className={`border-b border-violet-100 ${i === arr.length - 1 ? "font-semibold" : ""}`}>
                  <td className="py-1.5 text-gray-700">{label}</td>
                  <td className="py-1.5 text-right text-gray-900">{won(ours)}</td>
                  <td className="py-1.5 text-right text-gray-900">{won(String(official))}</td>
                  <td className="py-1.5 text-right text-gray-500">{won(abs)}</td>
                  <td className={`py-1.5 text-right font-semibold ${pctColor(pct)}`}>{pctFmt(pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <p className="text-xs text-violet-600 mt-2 bg-violet-100 rounded px-2 py-1">
            드리프트% = (우리−쿠팡)/쿠팡 · 닫힌 과거일만 대조(당일 제외, D-3) · 사실·지표만(D-2).
            {isReference && (
              <b> ⚠ 참고치 — 권위 판정은 계정 지정(오픽스/오하이테크) + 완전 적재일 때만(D-7).</b>
            )}
            <span className="block mt-0.5 text-violet-500">
              알려진 잔차: 3P 잔여 stale 취소(D-5), RG gross-vs-net(우리 gross·쿠팡 net, D-11) — 계산 오류 아님.
            </span>
          </p>
        </>
      )}
    </div>
  );
}

function RgSettlementCard({
  data,
  onRefresh,
  refreshing,
  msg,
}: {
  data: OverviewResponse;
  onRefresh: () => void;
  refreshing: boolean;
  msg: string | null;
}) {
  const rg = data.rg_settlement;
  const RefreshButton = (
    <button
      onClick={onRefresh}
      disabled={refreshing}
      className="flex items-center gap-1.5 px-2.5 py-1 text-xs bg-orange-600 text-white rounded-md hover:bg-orange-700 disabled:opacity-50"
    >
      <span className={refreshing ? "animate-spin" : ""}>🔄</span>
      {refreshing ? "갱신 중…" : "RG 정산 갱신"}
    </button>
  );
  if (!rg) return null;
  if (!rg.summary.has_data) {
    return (
      <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mb-4 flex items-center justify-between">
        <span className="text-sm text-amber-700">🚧 RG 정산 비용(미반영) — 데이터 없음</span>
        {RefreshButton}
      </div>
    );
  }
  return (
    <div className="bg-orange-50 border border-orange-200 rounded-lg p-4 mb-4">
      {msg && (
        <div className="text-xs text-orange-700 bg-orange-100 rounded px-2 py-1 mb-2">{msg}</div>
      )}
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-semibold text-orange-800">✅ RG 정산 비용 — 순이익 반영됨 (계정 단위, 전액 차감)</span>
        <div className="flex items-center gap-2">
          {RefreshButton}
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

function AccountView({
  data,
  reconcile,
  onRefreshSales,
  salesRefreshing,
  salesRefreshMsg,
  onRefreshRg,
  rgRefreshing,
  rgRefreshMsg,
}: {
  data: OverviewResponse;
  reconcile: RevenueReconcile | null;
  onRefreshSales: () => void;
  salesRefreshing: boolean;
  salesRefreshMsg: string | null;
  onRefreshRg: () => void;
  rgRefreshing: boolean;
  rgRefreshMsg: string | null;
}) {
  const s = data.account.summary;
  return (
    <>
      <ReconciliationCard data={data} />
      <RevenueDriftCard
        reconcile={reconcile}
        onRefresh={onRefreshSales}
        refreshing={salesRefreshing}
        msg={salesRefreshMsg}
      />
      <RgSettlementCard data={data} onRefresh={onRefreshRg} refreshing={rgRefreshing} msg={rgRefreshMsg} />
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <Card label="매출" value={won(s.revenue)} />
        <Card label="반품 차감" value={won(s.return_deduction)} />
        <Card label="수수료(+VAT)" value={won(s.total_fee)} />
        <Card
          label="광고비"
          value={won(s.ad_spend)}
          sub={Number(s.ad_nonpa_deducted ?? "0") > 0
            ? `+비-PA ${won(s.ad_nonpa_deducted ?? "0")}(계정 단위, 순이익 차감)`
            : undefined}
        />
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
