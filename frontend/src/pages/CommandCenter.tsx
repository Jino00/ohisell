// CommandCenter.tsx — 🎯 종합 조망 (P7, D-2). 옵션ID 결합 엔진의 3축(회계·광고·상품) 뷰.
// D-3: 시스템은 사실/지표 정리만 — 전략 추천 없음. 해석은 Jino 몫.
import { useState, useEffect, useRef } from "react";
import {
  fetchCommandCenter,
  fetchRevenueReconcile,
  fetchRocketOverview,
  fetchRocketCostMapUnmapped,
  fetchRocketCostMap,
  upsertRocketCostMap,
  excludeRocketCostMap,
  deleteRocketCostMap,
  fetchRocketPromoPnl,
  patchPromotionManual,
  syncRealtime,
  type OverviewResponse,
  type RevenueReconcile,
  type RocketOverview,
  type RocketUnmappedItem,
  type RocketMappingItem,
  type PromoPnlResponse,
  type PromoPnlCard,
} from "../lib/api";
// 갱신 폴링 판정은 여기 한 곳에만 산다(사본 금지 — LESSONS #55). 배너도 같은 것을 쓴다.
import {
  runStreamRefresh,
  runStreamsRefresh,
  describeOutcome,
  specByKey,
  RG_STREAM_SPECS,
} from "../lib/streamRefresh";
import type { RowNote } from "../lib/netScope";
// RG 계정별 정산 내역 카드 — 정의는 공용 모듈에 산다(계약 §1-A-3). 화면 B가 같은 것을 쓴다.
import { RgSettlementCard } from "../components/RgSettlementCard";
// RG 정산공제의 축·근거 자백 — 대시보드 행과 **같은 함수**를 쓴다(두 화면이 갈라지지 않게).
import { rgFeeFactsFromSummary, rgFeeNote } from "../lib/rgSettlementAxis";

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
// ★ratioPct와 다르다: 이건 백엔드가 **이미 퍼센트 단위로 준 값**을 그대로 찍는다(100을 곱하지
//   않는다). 두 관례를 한 함수로 뭉치면 0.02%가 2.05%로 보이는 사고가 또 난다(2026-08-03).
function pct1(s: string | null): string {
  if (s == null) return "—";
  return `${Number(s).toFixed(1)}%`;
}

type Axis = "account" | "ad" | "product" | "rocket";

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

// 'RG 정산 갱신' 버튼이 깨우는 계정(2026-07-27 WING2 편입). 버튼은 하나지만 큐는 계정별로
// 갈려 있어(백엔드 상태행 분리) 각 계정 데몬이 자기 요청만 claim한다 → 요청·폴링도 계정별.
// 폴링 판정 규칙(성공 우선·lease·무작업 종료·RG 다중업로드 정착)은 lib/streamRefresh로 이관됐다.

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
  // 로켓배송(1P) 종합조망 블록
  const [rocket, setRocket] = useState<RocketOverview | null>(null);
  const [rocketRefreshing, setRocketRefreshing] = useState(false);
  const [rocketRefreshMsg, setRocketRefreshMsg] = useState<string | null>(null);
  // 오하이테크(1P) 광고비 "갱신 버튼" 상태 (S3, D-11) — 로켓 갱신과 동일 패턴, 별도 페처.
  const [ohitechAdRefreshing, setOhitechAdRefreshing] = useState(false);
  const [ohitechAdRefreshMsg, setOhitechAdRefreshMsg] = useState<string | null>(null);
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
    // 로켓배송(1P) 블록 — 계정 무관(오하이테크 단일 계정, D-6), fail-soft.
    fetchRocketOverview(f, t)
      .then((r) => { if (seq === reqSeq.current) setRocket(r); })
      .catch(() => { if (seq === reqSeq.current) setRocket(null); });
  }

  // "판매분석 갱신" — Mac Wing 데몬(com.ohisell.wing)을 깨워 쿠팡 공식 GMV를 즉시 가져온다
  // (cf_clearance로 prod 직접 fetch 불가). request-refresh → 데몬 fetch·push →
  // last_success_at 변화를 폴링해 완료 감지 → reconcile 리로드.
  async function refreshSalesAnalysisNow() {
    setSalesRefreshing(true);
    setSalesRefreshMsg("Mac에서 판매분석 가져오는 중… (~20초, 첫 갱신이면 Mac 로그인 창 확인)");
    try {
      const spec = specByKey("ofix_sales");
      const outcome = await runStreamRefresh(spec);
      if (outcome.state === "done") {
        // 대기 중 사용자가 계정/기간을 바꿨을 수 있음 → 현재 선택(selRef)으로 재조회(codex S3 P1).
        const sel = selRef.current;
        doFetch(sel.from, sel.to, sel.account);
        setSalesRefreshMsg("✅ 판매분석 갱신 완료");
        setTimeout(() => setSalesRefreshMsg(null), 4000);
      } else {
        setSalesRefreshMsg(describeOutcome(spec, outcome));
      }
    } catch (e: any) {
      setSalesRefreshMsg("❌ 갱신 요청 실패: " + (e?.message || ""));
    } finally {
      setSalesRefreshing(false);
    }
  }

  // "RG 정산 갱신" — Mac Wing 데몬(com.ohisell.wing / .wing2)이 RG 정산 XLSX를 다운로드·push.
  // 두 계정을 함께 깨우되 큐·결과는 계정별 — 한쪽만 성공/실패해도 어느 쪽인지 문구로 보인다.
  async function refreshRgSettlementNow() {
    setRgRefreshing(true);
    setRgRefreshMsg("Mac에서 RG 정산 가져오는 중… (~30초, 계정 2곳)");
    try {
      // ★계정별로 '정착하는 즉시' 반영한다(codex R1 [P2]) — runStreamsRefresh가 그 규칙을 담당.
      //   Promise.all 결과를 한꺼번에 쓰면 한쪽 데몬이 꺼져 있을 때 이미 끝난 쪽의 데이터·문구가
      //   상대의 215초 타임아웃까지 인질로 잡힌다(오픽스 30초 완료 → 3분 넘게 화면에 아무것도 안 뜸).
      const results = await runStreamsRefresh(RG_STREAM_SPECS, (spec, outcome, all) => {
        // 성공한 계정의 데이터는 곧바로 반영 — 현재 선택(selRef)으로 재조회(codex S3 P1).
        if (outcome.state === "done") {
          const sel = selRef.current;
          doFetch(sel.from, sel.to, sel.account);
        }
        setRgRefreshMsg(
          RG_STREAM_SPECS.map((s) => describeOutcome(s, all.get(s.key) ?? null)).join(" · "),
        );
        void spec;
      });
      // 전건 성공일 때만 자동 소거 — 실패/무응답 문구는 남겨서 Jino가 보게 한다.
      if (RG_STREAM_SPECS.every((s) => results.get(s.key)?.state === "done")) {
        setTimeout(() => setRgRefreshMsg(null), 4000);
      }
    } catch (e: any) {
      setRgRefreshMsg("❌ 갱신 요청 실패: " + (e?.message || ""));
    } finally {
      setRgRefreshing(false);
    }
  }

  // "로켓 갱신" — Mac 로켓 페처(com.ohisell.rocket poll 데몬)를 깨워 발주/정산을 즉시 가져온다.
  // Wing 판매분석 갱신과 동일 패턴.
  async function refreshRocketNow() {
    setRocketRefreshing(true);
    setRocketRefreshMsg("Mac에서 로켓배송 발주/정산 가져오는 중… (~30초)");
    try {
      const spec = specByKey("supplier_hub");
      // 타임아웃·폴링 간격은 기존 값 보존(180초·5초) — 이 버튼의 동작을 바꾸지 않는다.
      const outcome = await runStreamRefresh(spec, { timeoutMs: 180000, pollMs: 5000 });
      if (outcome.state === "done") {
        const sel = selRef.current;
        doFetch(sel.from, sel.to, sel.account);
        setRocketRefreshMsg("✅ 로켓배송 갱신 완료");
        setTimeout(() => setRocketRefreshMsg(null), 4000);
      } else {
        setRocketRefreshMsg(describeOutcome(spec, outcome));
      }
    } catch (e: any) {
      setRocketRefreshMsg("❌ 갱신 요청 실패: " + (e?.message || ""));
    } finally {
      setRocketRefreshing(false);
    }
  }

  // "광고비 갱신" — Mac 오하이테크 광고 페처(com.ohisell.ohitech-ad poll 데몬)를 깨워 광고비를
  // 즉시 가져온다(Akamai로 prod 직접 fetch 불가, D-4). 완료되면 로켓 종합조망을 리로드해
  // ad_spend·net_profit 반영분을 보여준다. 로켓/Wing 갱신과 동일 패턴(트랙 D-11).
  async function refreshOhitechAdNow() {
    setOhitechAdRefreshing(true);
    setOhitechAdRefreshMsg("Mac에서 오하이테크 광고비 가져오는 중… (~수초, 첫 갱신이면 Chrome 로그인 확인)");
    try {
      const spec = specByKey("ohitech_ad");
      // 타임아웃·폴링 간격은 기존 값 보존(180초·5초) — 이 버튼의 동작을 바꾸지 않는다.
      const outcome = await runStreamRefresh(spec, { timeoutMs: 180000, pollMs: 5000 });
      if (outcome.state === "done") {
        const sel = selRef.current;
        doFetch(sel.from, sel.to, sel.account);
        setOhitechAdRefreshMsg("✅ 광고비 갱신 완료");
        setTimeout(() => setOhitechAdRefreshMsg(null), 4000);
      } else {
        setOhitechAdRefreshMsg(describeOutcome(spec, outcome));
      }
    } catch (e: any) {
      setOhitechAdRefreshMsg("❌ 갱신 요청 실패: " + (e?.message || ""));
    } finally {
      setOhitechAdRefreshing(false);
    }
  }

  function load() {
    doFetch(from, to, account);
  }

  async function syncAndLoad() {
    setSyncing(true);
    try { await syncRealtime(); } catch { /* fail-soft */ }
    setSyncing(false);
    // ★대기 중 사용자가 계정/기간을 바꿨을 수 있음 → 현재 선택(selRef)으로 재조회.
    // 여기서 클로저 from/to/account를 쓰면 방금 고른 기간을 옛 값으로 덮는다(issue #235).
    // reqSeq 가드로는 못 막는다 — 이 요청이 더 나중이라 seq가 커서 오히려 이긴다.
    // 창이 넓다: prod 실측 POST /api/sync/realtime = 29.9~32.9초(2026-08-07 13:01 KST, 3회).
    const sel = selRef.current;
    doFetch(sel.from, sel.to, sel.account);
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
          ["rocket", "🚀 로켓배송 1P"],
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
          {axis === "rocket" && (
            <RocketView
              data={rocket}
              from={from}
              to={to}
              onRefresh={refreshRocketNow}
              refreshing={rocketRefreshing}
              refreshMsg={rocketRefreshMsg}
              onRefreshAd={refreshOhitechAdNow}
              adRefreshing={ohitechAdRefreshing}
              adRefreshMsg={ohitechAdRefreshMsg}
            />
          )}
        </>
      )}
    </div>
  );
}

// value가 ReactNode인 이유: 손익 카드가 음수를 빨강으로, 하한을 "≥ …"로 렌더해야 한다
//   (적대적 리뷰 F3·F13). 기존 호출부는 전부 문자열이라 그대로 호환된다.
function Card({
  label,
  value,
  sub,
  note,
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
  // 자백 한 줄 — 툴팁(title)에 「왜 그런가」가 실린다. `sub`와 달리 **판정 결과**라 색이 다르다.
  note?: RowNote | null;
}) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-3">
      <div className="text-xs text-gray-500">{label}</div>
      <div className="text-lg font-bold text-gray-900">{value}</div>
      {sub && <div className="text-xs text-gray-400">{sub}</div>}
      {note && (
        <div className="text-xs text-gray-500" title={note.title}>
          {note.text}
        </div>
      )}
    </div>
  );
}

// ★모듈 스코프로 올렸다(2026-08-11): 종전엔 ReconciliationCard 안에서 정의돼
//   `react-hooks/static-components`가 **사용처마다** 경고를 냈다(8건). 렌더마다 새 컴포넌트
//   타입이 만들어져 React가 subtree를 통째로 재마운트하는 것도 실제 비용이다.
//   행을 하나 더 붙일 때마다 lint 부채가 늘어나는 구조라, 이번에 행을 늘리면서 같이 없앤다.
// ★RG 매출의 «자기 신뢰도»를 문장으로 옮기는 두 함수 (적대 리뷰 1R P1-1 / P2-1).
//   렌더 안에 인라인으로 두면 테스트가 화면을 통째로 띄워야만 검사할 수 있어서 밖으로 뺐다 —
//   생존 변이 3종이 전부 「이 자리를 지워도 초록」이었다.
export type RgAxisFields = {
  rg_option_axis_days?: string | null;
  rg_option_axis_complete?: boolean | null;
  rg_open_days?: number | null;
};

export function rgAxisHint(s: RgAxisFields): string {
  const base = "판매분석 · 로켓그로스 (콘솔 net)";
  const days = s.rg_option_axis_days ? ` · 옵션축 ${s.rg_option_axis_days}일` : "";
  const open = s.rg_open_days ? ` · 미확정 ${s.rg_open_days}일` : "";
  return base + days + open;
}

// ★`=== false`가 아니라 `!== true`다. 백엔드가 필드를 못 실어 보내는 경우(구버전 응답·직렬화
//   누락)에 «경고 없음»으로 떨어지면, 이 카드가 막으려는 바로 그 단정이 되살아난다.
//   「모른다」의 안전한 기본값은 침묵이 아니라 경고다.
export function rgAxisWarn(s: RgAxisFields): string | undefined {
  if (s.rg_option_axis_complete === true) return undefined;
  if (s.rg_option_axis_days === "0/0" && (s.rg_open_days ?? 0) > 0) {
    // 창 전체가 아직 안 닫힌 경우(예: 「어제」 버튼 = 오늘~오늘). 결함이 아니라 D+1의 결과다.
    return "이 기간은 아직 쿠팡 콘솔이 닫지 않았다(D+1) — RG 매출 0은 «판매 0»이 아니라 «아직 없음»이다.";
  }
  return "옵션축이 이 기간을 다 덮지 못했다 — RG 매출은 부분치다(빈 날은 0원이 아니라 «미상»).";
}

function ReconRow({ label, value, hint, warn }: {
  label: string; value: string | null; hint: string; warn?: string;
}) {
  return (
    <div className="flex items-baseline justify-between py-1.5 border-b border-indigo-100 last:border-0">
      <div>
        <span className="text-sm text-gray-700">{label}</span>
        {/* ★구 `gross` 표식(「gross·취소 미차감」)은 D-CPP-49로 사실이 아니게 됐다. 자리를
            비우지 않고 «부분치 경고»로 바꾼다 — 값이 불완전할 수 있는 조건은 여전히 있고,
            그때 아무 말도 안 하는 것이 종전 gross 오표기보다 나쁘다. */}
        {warn && <span className="ml-1 text-xs text-amber-600" title={warn}>⚠ 부분치</span>}
        <div className="text-xs text-gray-400">{hint}</div>
      </div>
      <span className="text-sm font-semibold text-gray-900 tabular-nums">{won(value)}</span>
    </div>
  );
}

// S7(정합성 트랙 D-1·D-11): 매출·광고 분해 검산 패널 — 쿠팡 Wing 대시보드와 수동 1:1 대조용.
// 시스템은 사실/지표만(D-3) — 일치/불일치 판정은 Jino가 옆 화면과 눈으로 대조한다.
export function ReconciliationCard({ data }: { data: OverviewResponse }) {
  const s = data.account.summary;
  const a = data.ad.summary;
  const Row = ReconRow;
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
          {/* ★D-CPP-49: RG 매출 원천이 gross 주문 원장 → 콘솔 net으로 바뀌었다(계약 ⓑ).
              「(gross·취소 미차감)」 표식을 지우는 것이 이 변경의 표면 절반이다 — 값만 바꾸고
              라벨을 두면 화면이 net을 gross라고 부르게 된다. */}
          <Row
            label="ㄴ 로켓그로스 RG"
            value={s.revenue_rg ?? "0"}
            hint={rgAxisHint(s)}
            warn={rgAxisWarn(s)}
          />
        </div>
        <div>
          {/* ★광고센터 소스는 «광고주 단위»다 — 광고주 계정(오픽스)에만 적용된다.
              적용 안 되는 계정에서 0원이라 단정하면 «광고를 안 썼다»로 읽힌다. 실제로 2026-08-11
              오하이테크 화면이 그랬다: 여기 0원인데 같은 화면 요약·옵션표는 11,247원.
              그래서 «무엇을 보고 있는지»를 라벨로 밝히고 값은 옵션축(=실제 net_profit 차감액)을 쓴다. */}
          <div className="text-xs font-medium text-indigo-500 mb-1 mt-3 md:mt-0">
            광고 {a.ad_confirmed_applies === false ? "(옵션축 — 광고센터 미태깅 계정)" : "(쿠팡 [광고센터])"}
          </div>
          {a.ad_confirmed_applies === false ? (
            <>
              <Row
                label="전체 광고비"
                value={s.ad_spend}
                hint="옵션축 실집행(PA) · net_profit 차감 기준 — 이 계정은 광고센터 보고서에 광고주로 안 잡힌다"
              />
              {/* ★값을 «모른다»고 말한다 — 0원이라 쓰면 이 PR이 없앤 패턴 그대로다(적대 리뷰 P2-3).
                  비-PA는 «전체−집행»인데 광고센터 값이 없으니 산출 자체가 불가능하다.
                  net_profit에 추가 차감이 0인 것은 사실이고, 그건 힌트가 말한다. */}
              <Row
                label="ㄴ 비-PA (브랜드/디스플레이)"
                value={null}
                hint="광고센터 값이 없어 산출 불가 · net_profit 추가 차감은 없음(0원)"
              />
            </>
          ) : (
            <>
              <Row label="전체 광고비 (ALL)" value={a.ad_confirmed_total ?? s.ad_spend} hint="광고센터 · 전체 광고비(비-PA 포함) · net_profit 차감 기준" />
              <Row label="ㄴ 집행 (상품검색광고/PA)" value={a.ad_confirmed_pa ?? s.ad_spend} hint="광고센터 · 집행 광고비(DELIVERED)" />
              <Row label="ㄴ 비-PA (브랜드/디스플레이)" value={a.ad_confirmed_nonpa ?? "0"} hint="전체−집행 · net_profit에 추가 차감 반영(D-15)" />
            </>
          )}
        </div>
      </div>
      {/* ★각주도 계정에 따라 갈라야 한다 — 미적용 계정에 「ALL로 차감 · 집행+비-PA로 분해」라
          적으면 위에서 고친 거짓 단정이 각주로 되살아난다(둘 다 그 계정엔 사실이 아니다). */}
      <p className="text-xs text-indigo-600 mt-2 bg-indigo-100 rounded px-2 py-1">
        RG 매출은 쿠팡 <b>판매분석 콘솔 net</b> 기준이다(D-CPP-49) — 아래 대시보드 「로켓그로스」 행과 같은 축이라 두 화면 숫자가 일치한다.
        우리 gross 주문 원장(취소 미차감)과의 간극은 매출이 아니라 <b>수집 대조</b>로 아래 드리프트 표의 「gross 원장」 줄에서 본다.
        {a.ad_confirmed_applies === false ? (
          <> 이 계정은 쿠팡 <b>광고센터 보고서에 광고주로 잡히지 않아</b> 「전체 광고비(ALL)」·비-PA 분해가 없다 —
          순이익에서 차감되는 값은 <b>옵션축 실집행(PA)</b>이다(D-CPP-38).</>
        ) : (
          <> 광고비는 쿠팡 <b>"전체 광고비"(ALL)</b>로 순이익에서 차감 — 집행(상품검색광고)+비-PA(브랜드/디스플레이)로 분해 표시(D-15).</>
        )}
      </p>
    </div>
  );
}

// S3(Wing 세션 자동화 트랙): 쿠팡 공식 GMV(판매분석 vendor-summary) 자동 대조 — 드리프트%.
// 우리 매출(revenue_3p/rg) vs 쿠팡 공식 GMV를 닫힌 과거일 기준으로 비교(D-3). 사실·지표만(D-2) —
// 일치/불일치 판정·전략 추천 없음. 임계 색상은 사실의 크기 강조일 뿐. 권위값은 계정 지정+완전 적재일 때만(D-7).
// export: 이 카드의 «gross 원장 줄»과 «같은 축» 문구가 테스트로 지켜져야 한다 —
// 적대 리뷰 1R에서 이 줄을 지우는 변이(FE-4)가 살아남았다.
export function RevenueDriftCard({
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

          <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
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
                ["로켓그로스 RG (net)", reconcile.ours.revenue_rg, reconcile.official.gmv_rg, reconcile.drift.abs_rg, reconcile.drift.pct_rg],
                // ★매출이 아니라 «수집 대조» 줄이다. 라벨을 달리해 한 화면에 같은 이름의 두 값이
                //   생기지 않게 한다(교훈 #235: 위쪽 단정이 아래쪽 사실을 덮는 사고).
                // ★값이 없으면 **줄을 안 그린다**(적대 리뷰 2R P2-5). `?? "0"`로 채우면
                //   `0 − 콘솔net`이 「−178만원 드리프트」로 그려져 감시 장치가 거짓 경보를 낸다.
                //   백엔드도 같은 이유로 조용한 0 폴백을 뺐다 — 두 층의 정책을 같은 방향으로 둔다.
                ...(reconcile.ours.revenue_rg_gross != null
                  ? [["ㄴ RG gross 원장 (수집 대조)", reconcile.ours.revenue_rg_gross, reconcile.official.gmv_rg, reconcile.drift.abs_rg_gross ?? "0", reconcile.drift.pct_rg_gross ?? null]]
                  : []),
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
          </div>

          <p className="text-xs text-violet-600 mt-2 bg-violet-100 rounded px-2 py-1">
            드리프트% = (우리−쿠팡)/쿠팡 · 닫힌 과거일만 대조(당일 제외, D-3) · 사실·지표만(D-2).
            {isReference && (
              <b> ⚠ 참고치 — 권위 판정은 계정 지정(오픽스/오하이테크) + 완전 적재일 때만(D-7).</b>
            )}
            <span className="block mt-0.5 text-violet-500">
              알려진 잔차: 3P 잔여 stale 취소(D-5).
              {/* ★백엔드가 «값으로» 알려 주는 것을 화면이 산문으로 하드코딩하면, 백엔드가
                  플래그를 뒤집어도 화면은 계속 같은 말을 한다(적대 리뷰 1R P2-2). 읽는다. */}
              {reconcile.rg_same_axis ? (
                <> ★RG 「net」 줄은 <b>우리도 콘솔 net</b>이라(D-CPP-49) 차이 0이 정상이고,
                그 0은 「정합」이 아니라 <b>같은 숫자를 두 번 읽었다</b>는 뜻이다 —
                수집 간극은 <b>「ㄴ RG gross 원장」</b> 줄이 잰다(구 D-11 신호).</>
              ) : (
                <> RG 잔차는 우리 gross 원장과 쿠팡 net의 기준 차이다(구 D-11) — 계산 오류 아님.</>
              )}
            </span>
          </p>
        </>
      )}
    </div>
  );
}

// ★`RgSettlementCard`의 정의는 `../components/RgSettlementCard`로 나갔다 (계약 §1-A-3).
//   RG «자기 화면»(화면 B)이 같은 컴포넌트를 재사용해야 하는데, 정의가 이 페이지 안에 갇혀
//   있으면 재사용이 불가능하고 사본을 뜨는 수밖에 없다 — 사본은 두 화면이 갈라지는 경로다
//   (D-CPP-47이 고친 병). **렌더 자리는 이 파일에 그대로 있다**(아래 `RgSettlementCard` 호출)
//   — 옮긴 것은 함수의 주소이지 표면이 아니다(계약 §3 「제거·이동하지 않는다」).

// S2(트랙 revenue-wing-truth, D-1/D-9 A안): 정본 매출 카드 — 닫힌 과거일 매출을 Wing 판매분석
// GMV(net)로 표시(정본). 우리 주문기반(추정)과의 차이는 취소분(gross−net). 읽기전용 — 아래 회계
// 표·순이익은 주문기반 그대로(정밀 정합은 S4). wing_used=false(폴백/부분/집계)면 안내만.
/** D-CPP-32 ④ — 수수료가 «어디까지 사실이고 어디부터 추정인지» 화면이 실토한다.
 *
 * 왜 필요한가: 수수료는 창에 따라 신뢰도가 완전히 달라진다. 최근 열흘 주문은 정산 통보가
 * 오기 전이라 요율이 «그 옵션의 과거 정산»에서 온 것이고, 첫 정산 전 신제품은 요율 자체를
 * 모른다. 1P 화면엔 원가 커버리지 배지가 있는데 3P엔 아무것도 없었다.
 */
function FeeBasisCard({ data }: { data: OverviewResponse }) {
  const s = data.account.summary;
  const chk = s.fee_check;
  const defaultOptions = s.fee_rate_default_options ?? 0;
  const knownOptions = s.fee_rate_known_options ?? 0;
  if (knownOptions === 0 && defaultOptions === 0) return null;

  // 전제 검증: 이미 정산된 라인에서 «계산 == 실측»이어야 한다.
  // ★부호합(diff)만 보면 안 된다 — 라인별 +X/−X가 상쇄돼 초록이 된다. 라인당 어긋남도 본다.
  // ★단 라인당 허용치는 «수량에 비례»한다 — 쿠팡이 개당으로 반올림하기 때문(백엔드가 계산해
  //   max_line_excess로 준다: 0 이하면 전부 반올림 범위 안). 고정 1원으로 재면 수량 2 이상
  //   주문이 있는 창마다 거짓 빨강이 뜨고, 거짓 경보는 감시 장치를 죽인다.
  const diff = Math.abs(Number(chk?.diff ?? "0"));
  const tolerance = Math.max(1, Number(chk?.checked_lines ?? 0));
  const excess = Number(chk?.max_line_excess ?? "0");
  const premiseBroken = !!chk && chk.checked_lines > 0 && (diff > tolerance || excess > 0);

  return (
    <div className={`mb-4 rounded-lg border p-3 text-sm ${premiseBroken ? "border-red-300 bg-red-50" : "border-gray-200 bg-white"}`}>
      <div className="font-medium text-gray-900">
        🧾 수수료 근거 — 과세표준 {won(s.fee_base_total ?? "0")}
        <span className="ml-2 text-xs font-normal text-gray-400">
          3P 매출 − 반품차감 (1P·로켓그로스는 판매수수료가 없어 제외)
        </span>
      </div>
      <div className="mt-1 text-xs text-gray-600">
        수수료 = 과세표준 × 그 옵션의 요율 × 1.1. 요율은 그 옵션의 과거 정산 실측값이고,
        정산 이력이 없으면 기본 요율로 «추정»한다(정산은 주문 후 9~10일쯤 온다).
      </div>
      <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-xs">
        <span className="text-gray-700">실측 요율 {knownOptions}옵션</span>
        {defaultOptions > 0 && (
          <span className="text-amber-600">
            ⚠ 요율 미확인 {defaultOptions}옵션 · {won(s.fee_default_revenue ?? "0")}이 기본 요율 추정
          </span>
        )}
        {(s.fee_base_clamped_options ?? 0) > 0 && (
          <span className="text-amber-600" title="반품차감이 3P매출을 넘어 과세표준이 0으로 끊긴 옵션입니다(반품 창과 주문 창이 어긋나서 생깁니다). 그만큼 수수료가 과소 계상됩니다.">
            ⚠ 과세표준 0으로 끊긴 {s.fee_base_clamped_options}옵션(수수료 과소)
          </span>
        )}
        {chk && chk.checked_lines > 0 && (
          <span className={premiseBroken ? "text-red-700 font-medium" : "text-gray-500"}>
            {premiseBroken ? "⛔" : "✓"} 정산된 {chk.checked_lines}라인 대조: 계산 {won(chk.computed)} vs 실측 {won(chk.actual)} (차 {won(chk.diff)})
          </span>
        )}
        {chk && (chk.refunded_lines_skipped ?? 0) > 0 && (
          <span className="text-gray-400">환불 상계 {chk.refunded_lines_skipped}라인은 대조 제외(계산은 총매출 기준이라 비교 대상이 아님)</span>
        )}
        {chk && chk.checked_lines === 0 && (
          <span className="text-gray-400">
            ⓘ 이 창에선 전제가 «검증되지 않았다» — 대조 가능한 정산 라인이 없다(정산은 주문 후 9~10일쯤 온다).
            검증을 보려면 기간을 한 달 이상으로 넓히세요.
          </span>
        )}
      </div>
      {premiseBroken && (
        <div className="mt-2 text-xs text-red-700">
          계산한 수수료가 실측과 어긋납니다 — 「수수료 = 매출 × 요율」 전제가 깨졌을 수 있습니다.
          쿠팡 쿠폰·프로모션 정산 방식 변경을 의심하고 원천 행을 확인하세요.
        </div>
      )}
    </div>
  );
}

function CanonicalRevenueCard({ data }: { data: OverviewResponse }) {
  const rc = data.revenue_canonical;
  if (!rc || !rc.applicable) return null;
  const s = rc.summary;
  const wingUsed = s.wing_used;
  const gap = Number(s.our_closed_3p) + Number(s.our_closed_rg)
    - Number(s.closed_3p) - Number(s.closed_rg);
  return (
    <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-4 mb-4">
      <div className="text-sm font-semibold text-emerald-800 mb-2">
        🎯 정본 매출 — 닫힌 과거일 = Wing 판매분석 GMV{rc.closed_through ? ` (~${rc.closed_through})` : ""}
      </div>
      {wingUsed ? (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <div className="bg-white rounded-lg border border-emerald-200 p-3">
              <div className="text-xs text-emerald-600 mb-1">정본 매출 합계 (3P+RG)</div>
              <div className="text-lg font-bold text-emerald-900 tabular-nums">{won(s.canonical_total)}</div>
            </div>
            <div className="bg-white rounded-lg border border-gray-200 p-3">
              <div className="text-xs text-gray-500 mb-1">ㄴ 마켓플레이스 3P</div>
              <div className="text-sm font-semibold text-gray-900 tabular-nums">{won(s.canonical_3p)}</div>
            </div>
            <div className="bg-white rounded-lg border border-gray-200 p-3">
              <div className="text-xs text-gray-500 mb-1">ㄴ 로켓그로스 RG</div>
              <div className="text-sm font-semibold text-gray-900 tabular-nums">{won(s.canonical_rg)}</div>
            </div>
          </div>
          <p className="text-xs text-emerald-700 mt-2 bg-emerald-100 rounded px-2 py-1">
            닫힌 과거일 매출은 <b>Wing 실제(net)</b>로 표시(정본, D-1/A안). 우리 주문기반(추정, 취소포함){" "}
            {won(String(Number(s.our_closed_3p) + Number(s.our_closed_rg)))}와의 차이{" "}
            <b>{won(String(gap))}</b>는 취소분(gross−net). 아래 회계 표·순이익은 주문기반 그대로(정밀 정합 후속, S4).
            {rc.coverage ? ` · Wing 적재 ${rc.coverage.days_with_data}/${rc.coverage.expected_days}일(완전).` : ""}
          </p>
        </>
      ) : (
        <p className="text-xs text-emerald-700 bg-emerald-100 rounded px-2 py-1">{rc.note}</p>
      )}
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
      <CanonicalRevenueCard data={data} />
      <FeeBasisCard data={data} />
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <Card label="매출 (주문기반·추정)" value={won(s.revenue)} sub={data.revenue_canonical?.summary.wing_used ? "닫힌일 정본은 위 🎯 정본 매출 참조" : undefined} />
        <Card label="반품 차감" value={won(s.return_deduction)} />
        <Card
          label="수수료(+VAT)"
          value={won(s.total_fee)}
          sub={
            (s.fee_rate_default_options ?? 0) > 0
              ? `요율확인 ${s.fee_rate_known_options ?? 0}옵션 · 미확인 ${s.fee_rate_default_options}옵션(${won(s.fee_default_revenue ?? "0")} 기본 7.8% 추정)`
              : `요율확인 ${s.fee_rate_known_options ?? 0}옵션 전부 실측 요율`
          }
        />
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
          note={rgFeeNote(rgFeeFactsFromSummary(s as unknown as Record<string, unknown>))}
          sub={
            // D-CPP-33: 배송수입·납부세액이 순이익에 들어왔다. 옛 문구("매출−반품−수수료−광고−원가")는
            // 이제 사실이 아니라 그대로 두면 화면이 거짓말을 한다.
            [
              // ★축이 바뀌었다(계약 CONTRACT_rg_sales_date_axis) — 「광고 제외」만 적으면
              //   이 값이 **그 창에 판 것**의 공제인지 정산 주기 통짜인지 화면이 말하지 않는다.
              s.rg_flip_status === "applied_ex_ad" || s.rg_flip_status === "applied_full"
                ? `RG정산 −${won(s.rg_settlement_deducted ?? s.rg_non_ad_deducted ?? "0")}(광고 제외, ${
                    s.rg_settlement_axis === "sales_date" ? "판매일 축" : "정산 인식일 축"
                  })`
                : "RG 정산 데이터 없음",
              `배송 수입 +${won(s.shipping_income_3p ?? "0")} / 비용 −${won(s.seller_shipping_3p ?? "0")}`,
              // ★납부세액은 «음수»(매입세액 환급)가 될 수 있다 — 매출이 없고 비용만 있는 창에서
              //   실제로 그렇다. 부호를 앞에 박아 두면 「−−15원」이 찍힌다(적대 리뷰 P2-6).
              Number(s.payable_vat ?? "0") < 0
                ? `부가세 환급 +${won(String(Math.abs(Number(s.payable_vat ?? "0"))))}`
                : `납부세액 −${won(s.payable_vat ?? "0")}`,
            ].join(" · ")
          }
        />
      </div>
      <div className="overflow-x-auto">
      <table className="min-w-full text-sm bg-white rounded-lg border border-gray-200">
        <thead>
          <tr className="text-left text-gray-500 border-b border-gray-200">
            <th className="px-3 py-2">옵션 / 상품</th>
            <th className="px-3 py-2 text-right">매출</th>
            <th className="px-3 py-2 text-right">반품차감</th>
            <th className="px-3 py-2 text-right">수수료</th>
            <th className="px-3 py-2 text-right">요율</th>
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
              <td className="px-3 py-2 text-right text-xs">
                {Number(r.fee_base ?? "0") === 0 ? (
                  <span className="text-gray-300">—</span>
                ) : r.fee_basis === "default_rate" ? (
                  <span className="text-amber-600" title="이 옵션은 아직 정산 이력이 없어 기본 7.8%로 추정한 값입니다">
                    {(Number(r.fee_rate ?? "0") * 100).toFixed(1)}% 추정
                  </span>
                ) : (
                  <span className="text-gray-500">{(Number(r.fee_rate ?? "0") * 100).toFixed(1)}%</span>
                )}
              </td>
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
      </div>
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
      <div className="overflow-x-auto">
      <table className="min-w-full text-sm bg-white rounded-lg border border-gray-200">
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
      </div>
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
      <div className="overflow-x-auto">
      <table className="min-w-full text-sm bg-white rounded-lg border border-gray-200">
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
      </div>
    </>
  );
}

// ──────────────────────────────────────────────
// 로켓배송(1P) 탭 — 돈 축 종합조망 블록 (S5, D-10/D-11)
// ──────────────────────────────────────────────
function RocketView({
  data,
  from,
  to,
  onRefresh,
  refreshing,
  refreshMsg,
  onRefreshAd,
  adRefreshing,
  adRefreshMsg,
}: {
  data: RocketOverview | null;
  from: string;
  to: string;
  onRefresh: () => void;
  refreshing: boolean;
  refreshMsg: string | null;
  onRefreshAd: () => void;
  adRefreshing: boolean;
  adRefreshMsg: string | null;
}) {
  const [unmapped, setUnmapped] = useState<RocketUnmappedItem[] | null>(null);
  const [mappings, setMappings] = useState<RocketMappingItem[] | null>(null);
  const [mapLoading, setMapLoading] = useState(false);
  const [mapTab, setMapTab] = useState<"unmapped" | "confirmed">("unmapped");
  const [mapMsg, setMapMsg] = useState<string | null>(null);
  const [mapLoaded, setMapLoaded] = useState(false);

  function loadCostMap() {
    setMapLoading(true);
    Promise.all([
      fetchRocketCostMapUnmapped(200, true),
      fetchRocketCostMap(),
    ]).then(([u, m]) => {
      setUnmapped(u);
      setMappings(m);
    }).catch((e) => setMapMsg("매핑 로드 실패: " + e.message))
      .finally(() => setMapLoading(false));
  }

  function openMap() {
    if (!mapLoaded) { setMapLoaded(true); loadCostMap(); }
  }

  async function doConfirm(item: RocketUnmappedItem, sku: string) {
    setMapMsg(null);
    try {
      await upsertRocketCostMap({ product_number: item.product_number, internal_sku: sku, status: "confirmed", match_method: "suggested" });
      setMapMsg(`✅ ${item.product_number} → ${sku} 확정`);
      loadCostMap();
    } catch (e: any) {
      setMapMsg("❌ 확정 실패: " + (e?.message || ""));
    }
  }

  /** 「연결 안 함」으로 정한다 — **사유를 받는다**.
   *
   *  ★왜 사유를 묻나(2026-08-10): 2026-06-17 일괄 매핑이 «후보를 못 찾은» 22건을 이 상태로
   *    찍었고, 사유가 `no suggestion or low score`뿐이라 나중에 «사람의 결정»과 «매칭 실패»를
   *    가릴 수 없었다. 그 사이 두 엔진은 그것을 **원가 0원 = 전액 이익**으로 셌다.
   *    백엔드도 사유 없으면 422로 거부한다 — 여기서 먼저 물어 왕복을 아낀다.
   *  ★제외해도 **원가가 0이 되는 게 아니다**(그 해석은 없앴다). 손익에서는 「모름」이라
   *    그 상품은 계산에서 빠진다 — 버튼 옆 설명이 그렇게 말한다. */
  async function doExclude(productNumber: string) {
    setMapMsg(null);
    const reason = window.prompt(
      `${productNumber}을(를) 「연결 안 함」으로 정합니다.\n` +
      "· 작업 목록에서 사라지고 다시 제안하지 않습니다\n" +
      "· 손익에서는 「원가 모름」으로 다뤄져 계산에서 빠집니다(원가 0원이 아닙니다)\n\n" +
      "사유를 적어 주세요 (나중에 이 결정이 맞았는지 볼 근거가 됩니다):",
    );
    if (reason === null) return;                    // 취소
    if (!reason.trim()) {
      setMapMsg("❌ 사유가 필요합니다 — 사유 없는 제외는 나중에 «결정»과 «매칭 실패»를 가를 수 없습니다");
      return;
    }
    try {
      await excludeRocketCostMap(productNumber, reason);
      setMapMsg(`⏭ ${productNumber} 「연결 안 함」 처리 — 사유: ${reason.trim()}`);
      loadCostMap();
    } catch (e: any) {
      setMapMsg("❌ 제외 실패: " + (e?.message || ""));
    }
  }

  async function doDelete(productNumber: string) {
    setMapMsg(null);
    try {
      await deleteRocketCostMap(productNumber);
      setMapMsg(`🗑 ${productNumber} 매핑 삭제`);
      loadCostMap();
    } catch (e: any) {
      setMapMsg("❌ 삭제 실패: " + (e?.message || ""));
    }
  }

  const cov = data?.cost_coverage;
  const covPct = cov ? Math.round(cov.coverage_pct * 100) : null;

  return (
    <div>
      {/* 헤더 + 갱신 버튼 */}
      <div className="flex items-center justify-between mb-3">
        <div>
          <span className="text-sm font-semibold text-gray-700">🚀 로켓배송(1P) — 오하이테크 발주 돈 축</span>
          <span className="ml-2 text-xs text-gray-400">{from} ~ {to}</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={onRefreshAd}
            disabled={adRefreshing}
            title="Jino Mac에서 오하이테크 광고비를 지금 가져옵니다(~수초). Mac·Chrome(CDP 9224)이 켜져 있어야 합니다."
            className="flex items-center gap-1.5 px-2.5 py-1 text-xs bg-amber-600 text-white rounded-md hover:bg-amber-700 disabled:opacity-50"
          >
            <span className={adRefreshing ? "animate-spin" : ""}>📣</span>
            {adRefreshing ? "갱신 중…" : "광고비 갱신"}
          </button>
          <button
            onClick={onRefresh}
            disabled={refreshing}
            className="flex items-center gap-1.5 px-2.5 py-1 text-xs bg-sky-600 text-white rounded-md hover:bg-sky-700 disabled:opacity-50"
          >
            <span className={refreshing ? "animate-spin" : ""}>🔄</span>
            {refreshing ? "갱신 중…" : "로켓 갱신"}
          </button>
        </div>
      </div>

      {refreshMsg && (
        <div className="text-xs text-sky-700 bg-sky-50 border border-sky-200 rounded px-2 py-1 mb-3">{refreshMsg}</div>
      )}
      {adRefreshMsg && (
        <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1 mb-3">{adRefreshMsg}</div>
      )}

      {!data && (
        <div className="text-sm text-gray-500 bg-gray-50 border border-gray-200 rounded-lg p-4 mb-3">
          로켓배송(1P) 데이터 없음 — 위 '로켓 갱신' 버튼으로 Mac 페처가 가져오게 하거나, prod 배포 후 사용 가능합니다.
        </div>
      )}

      {data && (
        <>
          {/* 순이익 요약 카드 */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            <Card label="매출(발주 gross)" value={won(data.revenue)} sub={`발주 ${num(data.po_count)}건 / 수량 ${num(data.order_qty)}`} />
            <Card label="광고비" value={won(data.ad_spend)} sub="Retail 계정단위(D-4)" />
            <Card
              label="원가"
              value={data.has_cost ? won(data.cost) : "—"}
              sub={
                data.has_cost
                  ? covPct !== null ? `커버리지 ${covPct}%${covPct < 100 ? " ⚠ 부분반영" : ""}` : undefined
                  : "원가 미반영(has_cost=false)"
              }
            />
            <Card
              label="순이익"
              value={won(data.net_profit)}
              sub={data.has_cost && covPct !== null && covPct < 100
                ? `⚠ 원가 ${covPct}% 반영 — 과대 가능(원칙22)`
                : data.has_cost ? "매출−광고−원가" : "매출−광고(원가 미반영)"}
            />
          </div>

          {/* 커버리지 배지 */}
          {data.has_cost && cov && (
            <div className={`rounded-lg border p-3 mb-4 text-xs ${covPct !== null && covPct < 100 ? "bg-amber-50 border-amber-200 text-amber-800" : "bg-emerald-50 border-emerald-200 text-emerald-800"}`}>
              <div className="font-semibold mb-1">
                📊 원가 커버리지 {covPct !== null ? `${covPct}%` : "—"}
                {covPct !== null && covPct < 100 && " — ⚠ net_profit 원가 과소반영(순이익 과대 가능, 원칙22)"}
              </div>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-0.5 text-gray-700">
                <span>발주상세 수집금액: <b>{won(cov.detail_order_amount)}</b></span>
                <span>미매핑 금액: <b>{won(cov.unmapped_order_amount)}</b></span>
                <span>미수집 PO: <b>{num(cov.pos_without_detail_count)}건</b></span>
                <span>확정 SKU: <b>{num(cov.confirmed_sku_count)}</b></span>
                <span title="연결 안 하기로 정한 SKU. 원가는 「모름」이라 커버리지에 들어가지 않습니다.">
                  연결 안 함(미해결): <b>{num(cov.excluded_sku_count)}</b>
                </span>
                <span>미매핑 SKU: <b>{num(cov.unmapped_sku_count)}</b></span>
              </div>
              {covPct !== null && covPct < 100 && (
                <p className="mt-1 text-amber-700">아래 '원가 매핑 관리'에서 미매핑 상품번호를 확정하면 커버리지가 올라갑니다.</p>
              )}
            </div>
          )}

          {/* 발주↔정산 드리프트 */}
          <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-3 mb-4 text-xs">
            <div className="font-semibold text-indigo-800 mb-1">📋 발주↔정산 드리프트</div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-0.5 text-gray-700">
              <span>발주(gross): <b>{won(data.revenue)}</b></span>
              <span>정산합계: <b>{won(data.drift.settled_amount)}</b></span>
              <span>차이: <b>{won(data.drift.drift_amount)}</b></span>
              <span>드리프트%: <b>{data.drift.drift_pct != null ? `${(Number(data.drift.drift_pct) * 100).toFixed(2)}%` : "—"}</b></span>
            </div>
            <div className="text-indigo-600 mt-1">{data.drift.note}</div>
          </div>

          {/* 상품(SKU)별 손익 — 백엔드 sku_pnl 블록(배포 순서상 없을 수 있음).
              ★그레인이 옵션이 아니라 SKU인 이유: 매출·원가는 원래 상품번호 그레인이고 광고비만
                옵션 그레인인데 sku→option이 1:N이라, 옵션으로 내리면 매출·원가를 안분(추정)해야
                한다. SKU로 올리면 광고비를 더하기만 하면 되고 추정이 사라진다(D-16). */}
          {data.sku_pnl && data.sku_pnl.skus.length > 0 && (
            <details className="bg-white border border-gray-200 rounded-lg mb-4" open>
              <summary className="px-4 py-2 cursor-pointer select-none hover:bg-gray-50 text-sm font-semibold text-gray-700">
                💵 상품별 손익 (상위 {num(data.sku_pnl.shown)}/{num(data.sku_pnl.sku_count)}개)
              </summary>
              <div className="border-t border-gray-100 p-4">
                <div className="text-xs text-gray-500 mb-2" title={data.sku_pnl.coverage.note}>
                  광고비 {won(data.sku_pnl.coverage.ad_total)} 중 · 상품 연결{" "}
                  <b>{pct1(data.sku_pnl.coverage.ad_bridged_pct)}</b> · 매출 도달{" "}
                  <b>{pct1(data.sku_pnl.coverage.ad_with_revenue_pct)}</b> · 순이익 도달{" "}
                  <b className={Number(data.sku_pnl.coverage.ad_with_cost_pct ?? 0) < 90 ? "text-amber-600" : ""}>
                    {pct1(data.sku_pnl.coverage.ad_with_cost_pct)}
                  </b>
                  {/* ★원가 출처를 갈라 보인다(D-19) — sellc 등록원가와 이름 유사도 자동매핑은
                      신뢰 등급이 다르다. 같은 숫자로 뭉쳐 보이던 것이 2026-08-03 사고의 원인. */}
                  {" ("}
                  <span className="text-emerald-700">sellc {pct1(data.sku_pnl.coverage.ad_cost_sellc_pct)}</span>
                  {" · "}
                  <span className="text-amber-600">자동추정 {pct1(data.sku_pnl.coverage.ad_cost_auto_pct)}</span>
                  {")"}
                  {Number(data.sku_pnl.coverage.ad_unbridged) > 0 && (
                    <> · 상품 미연결 {won(data.sku_pnl.coverage.ad_unbridged)}
                      ({num(data.sku_pnl.coverage.unbridged_option_count)}개 옵션)</>
                  )}
                  <span className="block text-[11px] text-gray-400 mt-0.5">
                    ※ 표시 전용 — 광고비가 PA 기준이라 SKU 순이익 합은 계정 순이익과 다릅니다. 원가 미확정 상품은 순이익을 내지 않습니다.
                  </span>
                </div>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-xs">
                    <thead>
                      <tr className="text-left text-gray-400 border-b border-gray-100">
                        <th className="py-1 pr-2">상품명</th>
                        <th className="py-1 pr-2">상품번호</th>
                        <th className="py-1 pr-2 text-right">매출(발주)</th>
                        <th className="py-1 pr-2 text-right">광고비</th>
                        <th className="py-1 pr-2 text-right">원가</th>
                        <th className="py-1 pr-2 text-right">순이익</th>
                        <th className="py-1 pr-2 text-right">ROAS</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.sku_pnl.skus.map((r) => {
                        const adSpend = Number(r.ad_spend);
                        const roas = adSpend > 0 ? ratioX((Number(r.conversion_revenue) / adSpend).toString()) : "—";
                        const np = r.net_profit == null ? null : Number(r.net_profit);
                        return (
                          <tr key={r.sku_id} className="border-b border-gray-50 hover:bg-gray-50">
                            <td className="py-1.5 pr-2 text-gray-700 max-w-[20rem] truncate" title={r.product_name ?? undefined}>
                              {r.product_name || <span className="text-gray-300">—</span>}
                              {r.option_count > 1 && (
                                <span className="ml-1 text-[10px] text-gray-400">옵션 {num(r.option_count)}</span>
                              )}
                            </td>
                            <td className="py-1.5 pr-2 text-gray-500 font-mono">{r.sku_id}</td>
                            <td className="py-1.5 pr-2 text-right text-gray-600">{won(r.revenue)}</td>
                            <td className="py-1.5 pr-2 text-right text-gray-600">{won(r.ad_spend)}</td>
                            <td className="py-1.5 pr-2 text-right text-gray-500">
                              {r.cost == null ? (
                                <span className="text-gray-300">—</span>
                              ) : (
                                <>
                                  {won(r.cost)}
                                  {/* 자동추정 원가는 눈에 띄게 — 검증된 sellc 값과 섞이면 안 된다 */}
                                  {r.cost_source === "auto_map" && (
                                    <span className="ml-1 text-[10px] text-amber-600" title="이름 유사도 자동매핑 — 검증되지 않은 추정값입니다">추정</span>
                                  )}
                                </>
                              )}
                            </td>
                            {/* ★순이익 null = 계산 안 함(0이 아니다). 사유는 툴팁으로 말한다 —
                                모르는 것을 0으로 채우면 과대 순이익이 정상값과 섞인다. */}
                            <td className="py-1.5 pr-2 text-right font-semibold" title={r.profit_basis}>
                              {np == null ? (
                                <span className="text-gray-300 cursor-help">—</span>
                              ) : (
                                <span className={np >= 0 ? "text-emerald-700" : "text-rose-600"}>{won(r.net_profit)}</span>
                              )}
                            </td>
                            <td className="py-1.5 pr-2 text-right text-purple-700">{roas}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </details>
          )}

          {/* 상품별(옵션) 광고비 표 — 백엔드 ad_options 블록(배포 순서상 없을 수 있음) */}
          {data.ad_options && data.ad_options.options.length > 0 && (
            <details className="bg-white border border-gray-200 rounded-lg mb-4">
              <summary className="px-4 py-2 cursor-pointer select-none hover:bg-gray-50 text-sm font-semibold text-gray-700">
                📦 상품별 광고비 (상위 {num(data.ad_options.shown)}/{num(data.ad_options.option_count)}개)
              </summary>
              <div className="border-t border-gray-100 p-4">
                <div className="text-xs text-gray-500 mb-2" title={data.ad_options.reconciliation.basis}>
                  옵션 합계 {won(data.ad_options.reconciliation.option_sum)} · 계정 총액 {won(data.ad_options.reconciliation.account_total)} · 차이{" "}
                  {Number(data.ad_options.reconciliation.diff) > 0 ? "+" : ""}
                  {/* ★diff_pct는 백엔드가 **이미 퍼센트 단위**로 준다(diff/account_total*100).
                      여기서 또 100을 곱하면 0.02%가 2.05%로 보인다 — 수집이 크게 어긋난 것처럼
                      읽히는 숫자라 조용한 오해를 만든다(2026-08-03 라이브에서 발견). */}
                  {won(data.ad_options.reconciliation.diff)} ({Number(data.ad_options.reconciliation.diff_pct).toFixed(2)}%)
                  <span className="block text-[11px] text-gray-400 mt-0.5">※ 순이익에는 계정 총액을 씁니다.</span>
                </div>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-xs">
                    <thead>
                      <tr className="text-left text-gray-400 border-b border-gray-100">
                        <th className="py-1 pr-2">상품명</th>
                        <th className="py-1 pr-2">옵션ID</th>
                        <th className="py-1 pr-2 text-right">광고비</th>
                        <th className="py-1 pr-2 text-right">클릭</th>
                        <th className="py-1 pr-2 text-right">전환매출</th>
                        <th className="py-1 pr-2 text-right">ROAS</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.ad_options.options.map((opt) => {
                        const adSpend = Number(opt.ad_spend);
                        const roas = adSpend > 0 ? ratioX((Number(opt.conversion_revenue) / adSpend).toString()) : "—";
                        return (
                          <tr key={opt.option_id} className="border-b border-gray-50 hover:bg-gray-50">
                            {/* 상품명은 없을 수 있다(컬럼 추가 이전 적재분) → 표가 비지 않게 —로 폴백 */}
                            <td
                              className="py-1.5 pr-2 text-gray-700 max-w-[22rem] truncate"
                              title={opt.product_name ?? undefined}
                            >
                              {opt.product_name || <span className="text-gray-300">—</span>}
                            </td>
                            <td className="py-1.5 pr-2 text-gray-500 font-mono">{opt.option_id}</td>
                            <td className="py-1.5 pr-2 text-right text-gray-600">{won(opt.ad_spend)}</td>
                            <td className="py-1.5 pr-2 text-right text-gray-500">{num(opt.clicks)}</td>
                            <td className="py-1.5 pr-2 text-right text-gray-600">{won(opt.conversion_revenue)}</td>
                            <td className="py-1.5 pr-2 text-right font-semibold text-purple-700">{roas}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </details>
          )}

          {/* 원가 매핑 관리 패널 */}
          <div className="bg-white border border-gray-200 rounded-lg">
            <div
              className="flex items-center justify-between px-4 py-2 cursor-pointer select-none hover:bg-gray-50"
              onClick={openMap}
            >
              <span className="text-sm font-semibold text-gray-700">🔗 원가 매핑 관리 (발주상품번호 → SKU)</span>
              <span className="text-xs text-gray-400">{mapLoaded ? "새로고침↑" : "클릭해서 열기"}</span>
            </div>

            {mapLoaded && (
              <div className="border-t border-gray-100 p-4">
                <div className="flex gap-2 mb-3">
                  <button
                    onClick={() => setMapTab("unmapped")}
                    className={`px-3 py-1 text-xs rounded-md border ${mapTab === "unmapped" ? "bg-amber-600 text-white border-amber-600" : "bg-white text-gray-600 border-gray-300 hover:bg-gray-50"}`}
                  >
                    미매핑 {unmapped ? `(${unmapped.length})` : ""}
                  </button>
                  <button
                    onClick={() => setMapTab("confirmed")}
                    className={`px-3 py-1 text-xs rounded-md border ${mapTab === "confirmed" ? "bg-emerald-600 text-white border-emerald-600" : "bg-white text-gray-600 border-gray-300 hover:bg-gray-50"}`}
                  >
                    확정/연결안함 {mappings ? `(${mappings.length})` : ""}
                  </button>
                  <button onClick={loadCostMap} disabled={mapLoading} className="px-2 py-1 text-xs text-gray-500 hover:text-gray-700 disabled:opacity-50">
                    {mapLoading ? "⟳" : "새로고침"}
                  </button>
                </div>

                {mapMsg && (
                  <div className="text-xs mb-2 px-2 py-1 rounded bg-gray-50 border border-gray-200 text-gray-700">{mapMsg}</div>
                )}

                {mapTab === "unmapped" && unmapped && (
                  unmapped.length === 0 ? (
                    <p className="text-xs text-gray-400">미매핑 상품번호 없음 — 발주상세 수집 후 다시 확인하세요.</p>
                  ) : (
                    <div className="overflow-x-auto">
                    <table className="min-w-full text-xs">
                      <thead>
                        <tr className="text-left text-gray-400 border-b border-gray-100">
                          <th className="py-1 pr-2">상품번호</th>
                          <th className="py-1 pr-2">상품명</th>
                          <th className="py-1 pr-2 text-right">발주수량</th>
                          <th className="py-1">
                            제안(상위 3) / 액션
                            <span className="ml-1 font-normal text-[10px] text-gray-400">
                              「공용 N」 = 이미 N개 상품이 그 원가를 씀(이름이 기종을 가리켜도)
                            </span>
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {unmapped.map((item) => (
                          <tr key={item.product_number} className="border-b border-gray-50 hover:bg-gray-50">
                            <td className="py-1.5 pr-2 text-gray-700 font-mono">{item.product_number}</td>
                            <td className="py-1.5 pr-2 text-gray-600 max-w-xs truncate">{item.product_name || "—"}</td>
                            <td className="py-1.5 pr-2 text-right text-gray-500">{num(item.total_order_qty)}</td>
                            <td className="py-1.5">
                              <div className="flex flex-wrap gap-1">
                                {item.suggestions.map((s) => (
                                  <button
                                    key={s.internal_sku}
                                    onClick={() => doConfirm(item, s.internal_sku)}
                                    className="px-1.5 py-0.5 text-xs bg-blue-50 text-blue-700 border border-blue-200 rounded hover:bg-blue-100"
                                    title={`${s.product_name} / 원가 ${s.cost_price?.toLocaleString() ?? "미설정"}원 / 유사도 ${(s.score * 100).toFixed(0)}%`
                                      + (s.already_mapped_count > 0
                                        ? ` / ★이미 ${s.already_mapped_count}개 상품번호가 이 원가를 씁니다 — 기종 공용 원가입니다(이름이 특정 기종을 가리켜도)`
                                        : " / 아직 아무 상품번호도 안 쓰는 전용 SKU")}
                                  >
                                    {s.internal_sku} ({(s.score * 100).toFixed(0)}%)
                                    {/* ★이름이 기종을 지목해도 실제로는 공용 원가일 수 있다.
                                        라이브: OHI-TGLASS-IP17PRO("아이폰17 Pro")에 붙은 12개가
                                        아이폰12~16. 개수를 보이면 이름의 오해를 막는다. */}
                                    {s.already_mapped_count > 0 && (
                                      <span className="ml-1 text-[10px] text-amber-700">
                                        공용 {s.already_mapped_count}
                                      </span>
                                    )}
                                  </button>
                                ))}
                                <button
                                  onClick={() => doExclude(item.product_number)}
                                  className="px-1.5 py-0.5 text-xs bg-gray-100 text-gray-500 border border-gray-200 rounded hover:bg-gray-200"
                                  title="연결 안 함 + 재제안 방지. 손익에서는 「원가 모름」으로 다뤄집니다(원가 0원이 아닙니다). 사유를 적어야 합니다."
                                >
                                  연결 안 함
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    </div>
                  )
                )}

                {mapTab === "confirmed" && mappings && (
                  mappings.length === 0 ? (
                    <p className="text-xs text-gray-400">확정·연결 안 함 매핑 없음</p>
                  ) : (
                    <div className="overflow-x-auto">
                    <table className="min-w-full text-xs">
                      <thead>
                        <tr className="text-left text-gray-400 border-b border-gray-100">
                          <th className="py-1 pr-2">상품번호</th>
                          <th className="py-1 pr-2">internal_sku</th>
                          <th className="py-1 pr-2">상태</th>
                          <th className="py-1 pr-2 text-right">원가</th>
                          <th className="py-1">삭제</th>
                        </tr>
                      </thead>
                      <tbody>
                        {mappings.map((m) => (
                          <tr key={m.product_number} className="border-b border-gray-50 hover:bg-gray-50">
                            <td className="py-1.5 pr-2 text-gray-700 font-mono">{m.product_number}</td>
                            <td className="py-1.5 pr-2 text-gray-600">{m.internal_sku}</td>
                            <td className="py-1.5 pr-2">
                              <span className={`px-1.5 py-0.5 rounded text-xs ${m.status === "confirmed" ? "bg-emerald-100 text-emerald-700" : "bg-gray-100 text-gray-500"}`}>
                                {m.status === "confirmed" ? "확정" : "연결 안 함"}
                              </span>
                            </td>
                            <td className="py-1.5 pr-2 text-right text-gray-500">
                              {m.cost_price != null ? `${m.cost_price.toLocaleString()}원` : "미설정"}
                            </td>
                            <td className="py-1.5">
                              <button
                                onClick={() => doDelete(m.product_number)}
                                className="px-1.5 py-0.5 text-xs text-red-500 border border-red-200 rounded hover:bg-red-50"
                              >
                                삭제
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    </div>
                  )
                )}
              </div>
            )}
          </div>
        </>
      )}

      {/* 프로모션 손익 레이어 (트랙 coupang-promo-pnl Phase 2) — data 유무와 무관하게 항상 렌더:
          신선도·구독 경고는 로켓 조망이 비어 있을 때도 보여야 한다(그때가 더 위험하다). */}
      <PromoPnlBlock />
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════
// 프로모션 손익 블록 (트랙 coupang-promo-pnl Phase 2)
// ──────────────────────────────────────────────────────────────────
// ★null = **모름**이다. 0으로 렌더하면 "광고비 0원 → 흑자"가 조용히 태어난다(원칙22).
//   미상은 전부 "—"로 두고, 사유(blockers·unresolved_reasons)를 함께 보여준다.
// ★기간 셀렉터를 두지 않는다: 창은 사용자가 고르는 게 아니라 프로모션 행사기간이 정한다.
// ══════════════════════════════════════════════════════════════════
function PromoPnlBlock() {
  const [data, setData] = useState<PromoPnlResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [editing, setEditing] = useState<Record<string, { disc: string; skus: string }>>({});
  // 서버가 준 값 원본 — 저장 시 "무엇이 바뀌었나"를 판정하는 기준(F14).
  const [baseline, setBaseline] = useState<Record<string, { disc: string; skus: string }>>({});
  const [saving, setSaving] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setErr(null);
    fetchRocketPromoPnl(20)
      .then((d) => {
        setData(d);
        // 입력 칸 초기값을 서버 값으로 채운다(수기값이 화면에서 지워져 보이지 않도록).
        const init: Record<string, { disc: string; skus: string }> = {};
        for (const p of d.promotions) {
          init[p.request_id] = {
            disc: p.unit_discount_amount != null ? String(Number(p.unit_discount_amount)) : "",
            skus: (p.target_sku_ids || []).join(", "),
          };
        }
        setEditing(init);
        setBaseline(init);
      })
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false));
  }

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next && !loaded) { setLoaded(true); load(); }
  }

  async function save(requestId: string) {
    const e = editing[requestId];
    const base = baseline[requestId];
    // ★서버 값을 못 받은 상태(baseline 없음)에서 저장하면 두 수기값을 동시에 지운다.
    if (!e || !base) return;
    setSaving(requestId);
    setMsg(null);
    try {
      const discRaw = e.disc.trim();
      const skus = e.skus.split(",").map((s) => s.trim()).filter(Boolean);
      // ★바뀐 키만 보낸다. 백엔드는 "보낸 키만 갱신"을 보장하는데 UI가 항상 둘 다 보내면
      //   그 설계가 무력화되고, 한쪽만 고치려다 다른 쪽을 덮어쓰는 경로가 생긴다(F14).
      const body: { unit_discount_amount?: string | null; target_sku_ids?: string[] | null } = {};
      if (e.disc.trim() !== base.disc.trim()) {
        // 빈 칸 = '모름'으로 되돌림(0원 할인과 구분 — 백엔드가 null을 그렇게 해석한다).
        body.unit_discount_amount = discRaw === "" ? null : discRaw;
      }
      if (e.skus.trim() !== base.skus.trim()) body.target_sku_ids = skus;
      if (Object.keys(body).length === 0) {
        setMsg("변경사항 없음");
        setTimeout(() => setMsg(null), 3000);
        return;
      }
      await patchPromotionManual(requestId, body);
      setMsg(`✅ ${requestId} 저장`);
      load();
      setTimeout(() => setMsg(null), 4000);
    } catch (ex: any) {
      setMsg("❌ 저장 실패: " + (ex?.message || ""));
    } finally {
      setSaving(null);
    }
  }

  const fr = data?.freshness;
  const sub = fr?.subscription;

  return (
    <div className="mt-6 border-t border-gray-200 pt-4">
      <button
        onClick={toggle}
        className="w-full flex items-center justify-between px-3 py-2 bg-purple-50 border border-purple-200 rounded-lg hover:bg-purple-100"
      >
        <span className="text-sm font-semibold text-purple-800">
          🏷 프로모션 손익 — 진짜 BEP ROAS (셀러 부담 즉시할인)
        </span>
        <span className="text-xs text-purple-600">{open ? "접기 ▲" : "펼치기 ▼"}</span>
      </button>

      {open && (
        <div className="mt-3">
          {loading && <div className="text-sm text-gray-500 p-3">불러오는 중…</div>}
          {err && (
            <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-2 py-1 mb-3">
              손익 조회 실패: {err} — prod 미배포 상태면 404가 납니다(Phase 1 배포 선행 필요).
            </div>
          )}
          {msg && (
            <div className="text-xs text-purple-700 bg-purple-50 border border-purple-200 rounded px-2 py-1 mb-3">{msg}</div>
          )}

          {/* ② 구독 체험 종료 경고 (D-CPP-5) — 끊기면 판매분석이 조용히 멈춘다 */}
          {sub && (sub.warn || sub.expired) && (
            <div className={`rounded-lg border p-3 mb-3 text-xs ${sub.expired ? "bg-red-50 border-red-300 text-red-800" : "bg-amber-50 border-amber-300 text-amber-800"}`}>
              <div className="font-semibold">
                {sub.expired
                  ? `⛔ 판매분석 무료체험 종료됨 (${sub.free_trial_end}) — 수집이 조용히 멈출 수 있습니다`
                  : `⚠️ 판매분석 무료체험 종료 D-${sub.days_left} (${sub.free_trial_end})`}
              </div>
              <div className="mt-0.5 text-gray-600">{sub.basis}</div>
            </div>
          )}

          {/* ① 판매분석 유효구간 내 결손 배너 — 창 밖으로 밀리기 전에만 메울 수 있다 */}
          {fr && fr.missing_count > 0 && (
            <div className="rounded-lg border p-3 mb-3 text-xs bg-amber-50 border-amber-200 text-amber-800">
              <div className="font-semibold mb-1">
                📉 판매분석 결손 {fr.missing_count}일 / 유효구간 {fr.window.from}~{fr.window.to}
                {fr.urgent_count > 0 && ` — ⚠ ${fr.urgent_count}일이 7일 내 만료`}
              </div>
              <div className="text-gray-700">
                최신 수집일 <b>{fr.latest_date || "—"}</b>
                {fr.stale_days != null && fr.stale_days > 0 && (
                  <span className="text-amber-700"> (어제 대비 {fr.stale_days}일 뒤처짐)</span>
                )}
              </div>
              <div className="mt-1 text-gray-600">
                만료 임박:{" "}
                {fr.missing_dates.slice(0, 10).map((m) => (
                  <span key={m.date} className={`inline-block mr-1.5 px-1 rounded ${m.days_until_expiry <= 7 ? "bg-amber-200" : "bg-gray-100"}`}>
                    {m.date}(D-{m.days_until_expiry})
                  </span>
                ))}
                {fr.missing_dates.length > 10 && <span>… 외 {fr.missing_dates.length - 10}일</span>}
              </div>
              <p className="mt-1 text-amber-700">
                롤링 창을 벗어난 날짜는 영원히 못 채웁니다. 위 '로켓 갱신'으로 재수집하세요.
              </p>
            </div>
          )}

          {data && data.promotion_count === 0 && !loading && (
            <div className="text-sm text-gray-500 bg-gray-50 border border-gray-200 rounded-lg p-4">
              수집된 프로모션이 없습니다.
            </div>
          )}

          {data?.promotions.map((p) => (
            <PromoCard
              key={p.request_id}
              p={p}
              edit={editing[p.request_id] || { disc: "", skus: "" }}
              onEdit={(v) => setEditing((s) => ({ ...s, [p.request_id]: v }))}
              onSave={() => save(p.request_id)}
              saving={saving === p.request_id}
            />
          ))}

          {/* RG(2P) 쿠폰 — 나열 수준(used_amount 원천 미확정, 계획서 §3) */}
          {data && data.rg_coupons.count > 0 && (
            <div className="mt-4 bg-white border border-gray-200 rounded-lg p-3">
              <div className="text-sm font-semibold text-gray-700 mb-1">
                🎟 RG(2P) 즉시할인 쿠폰 {data.rg_coupons.count}건
                {data.rg_coupons.pending_count > 0 && (
                  <span className="ml-2 text-xs text-amber-700">
                    · 사용금액 미수집 {data.rg_coupons.pending_count}건
                  </span>
                )}
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-gray-500 border-b border-gray-200">
                      <th className="py-1 pr-2">쿠폰</th>
                      <th className="py-1 pr-2">계정</th>
                      <th className="py-1 pr-2">기간</th>
                      <th className="py-1 pr-2">상태</th>
                      <th className="py-1 pr-2 text-right">옵션</th>
                      <th className="py-1 pr-2 text-right">사용 금액(우리 부담)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.rg_coupons.coupons.map((c) => (
                      <tr key={c.account_key + c.coupon_id} className="border-b border-gray-50">
                        <td className="py-1.5 pr-2 text-gray-700">
                          {c.promotion_name || c.coupon_id}
                          <span className="ml-1 text-gray-400 font-mono">{c.coupon_id}</span>
                        </td>
                        <td className="py-1.5 pr-2 text-gray-500">{c.account_key}</td>
                        <td className="py-1.5 pr-2 text-gray-500">
                          {(c.start_at || "").slice(0, 10)}~{(c.end_at || "").slice(0, 10)}
                        </td>
                        <td className="py-1.5 pr-2 text-gray-500">{c.status || "—"}</td>
                        <td className="py-1.5 pr-2 text-right text-gray-500">{c.option_count}</td>
                        <td className="py-1.5 pr-2 text-right">
                          {c.used_amount_pending ? (
                            <span className="text-amber-700">미수집</span>
                          ) : (
                            <b>{won(c.used_amount)}</b>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-1 text-xs text-gray-500">
                {data.rg_coupons.window
                  ? `적용 창 ${data.rg_coupons.window.from ?? "—"} ~ ${data.rg_coupons.window.to ?? "—"}`
                  : data.rg_coupons.window_note}
              </p>
              <p className="mt-1 text-xs text-gray-500">{data.rg_coupons.note}</p>
            </div>
          )}

          {data && <p className="mt-3 text-xs text-gray-400">{data.accounting_note}</p>}
        </div>
      )}
    </div>
  );
}

/** 손익용 금액 렌더 — ★음수를 빨강으로. won()은 마이너스 기호 하나로만 구분돼서
 *  적자를 흑자 옆에 나란히 두면 눈으로 안 걸린다(적대적 리뷰 F13). */
function Money({ v, prefix }: { v: string | null | undefined; prefix?: string }) {
  if (v == null) return <span className="text-gray-400">—</span>;
  const neg = Number(v) < 0;
  return <span className={neg ? "text-red-600" : undefined}>{prefix}{won(v)}</span>;
}

function PromoCard({
  p, edit, onEdit, onSave, saving,
}: {
  p: PromoPnlCard;
  edit: { disc: string; skus: string };
  onEdit: (v: { disc: string; skus: string }) => void;
  onSave: () => void;
  saving: boolean;
}) {
  const t = p.totals;
  // ★BEP 광고비 vs 실광고비. 실광고비가 미상이면(옵션 귀속 불가) 계정 전체를 상한으로 비교한다.
  const adKnown = !!p.ad?.available;
  const adShown = adKnown ? p.ad!.attributed : p.ad?.account_window_spend ?? null;
  const overBep =
    t?.bep_ad_spend != null && adShown != null && Number(adShown) > Number(t.bep_ad_spend);
  // ★공헌이익이 이미 음수면 광고비 0원이어도 확정 적자다. 이걸 상한 비교 문구("적자 확정은
  //   아니지만")로 덮으면 운영자를 안심시키고, 퍼센트도 음수가 찍힌다(적대적 리뷰 F6).
  const bepNonPositive = t?.bep_ad_spend != null && Number(t.bep_ad_spend) <= 0;
  const lb = t?.net_profit_lower_bound_resolved_only ?? null;
  const w = p.window;

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-3 mb-3">
      <div className="flex items-start justify-between gap-2 mb-2">
        <div>
          <span className="text-sm font-semibold text-gray-800">{p.promotion_name || p.request_id}</span>
          <span className="ml-2 text-xs text-gray-400 font-mono">{p.request_id}</span>
          <div className="text-xs text-gray-500 mt-0.5">
            {w ? `${w.from} ~ ${w.to} (${w.days}일)` : "기간 미상"}
            {w && w.in_flight && (
              <span className="ml-1 px-1 rounded bg-blue-100 text-blue-700">
                진행 중 · {w.data_through}까지 확정
              </span>
            )}
            {w && w.missing_days.length > 0 && (
              <span className="ml-1 px-1 rounded bg-amber-200 text-amber-900">
                판매 결손 {w.missing_days.length}일 — 수량은 하한
              </span>
            )}
            {p.share_ratio != null && ` · 분담 ${Number(p.share_ratio)}%`}
            {p.status && ` · ${p.status}`}
            {p.applied_product_count != null && ` · 적용상품 ${p.applied_product_count}`}
          </div>
        </div>
        {t?.bep_roas != null && (
          <div className="text-right shrink-0">
            <div className="text-xs text-gray-500">진짜 BEP ROAS</div>
            <div className="text-lg font-bold text-purple-700">{ratioX(t.bep_roas)}</div>
            <div className="text-xs text-gray-400">이 이상이어야 본전</div>
          </div>
        )}
      </div>

      {p.blockers.length > 0 && (
        <div className="text-xs bg-amber-50 border border-amber-200 rounded px-2 py-1 mb-2 text-amber-800">
          {p.blockers.map((b, i) => <div key={i}>⚠ {b}</div>)}
        </div>
      )}

      {t && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-2">
          <Card
            label="판매량"
            value={num(t.qty_all)}
            sub={t.qty_all !== t.qty ? `손익 반영 ${num(t.qty)} (미해결 ${num(t.unresolved_qty)})` : undefined}
          />
          <Card
            label="순이익"
            // ★N/A일 때 하한을 부등호로 보여준다 — 이 지표의 존재 이유("최악에도 남는가")가
            //   백엔드에만 있고 화면에 없으면 전달되지 않는다(F3).
            value={
              t.net_profit != null ? <Money v={t.net_profit} />
                : lb != null ? <Money v={lb} prefix="≥ " />
                : <span className="text-gray-400">—</span>
            }
            sub={
              t.net_profit != null
                ? (t.qty_is_lower_bound ? "판매 결손분 제외 — 과소 방향 하한" : "납품매출−원가−분담금−광고비")
                : lb != null
                  ? `광고비 미상 → 계정 전체를 물린 하한${t.lower_bound_excludes_unresolved ? ` (미해결 ${t.unresolved_sku_ids.length}건 제외)` : ""}`
                  : "광고비 미상 → 산출 불가"
            }
          />
          <Card label="BEP 광고비" value={<Money v={t.bep_ad_spend} />}
                sub={bepNonPositive ? "★0 이하 — 광고비와 무관하게 적자" : "이 금액을 넘으면 적자"} />
          <Card
            label={adKnown ? "실광고비" : "계정 광고비(상한)"}
            value={<Money v={adShown} />}
            sub={
              adKnown
                ? `옵션 귀속 (${p.ad!.ad_days_covered}/${p.ad!.ad_days_judged}일 정합)`
                : `★옵션 귀속 불완전(${p.ad?.ad_days_covered ?? 0}/${p.ad?.ad_days_judged ?? 0}일) — 상한 프록시`
            }
          />
        </div>
      )}

      {t && bepNonPositive && (
        <div className="text-xs bg-red-50 border border-red-300 rounded px-2 py-1 mb-2 text-red-800 font-semibold">
          BEP 광고비가 0 이하입니다 — 광고비를 한 푼도 안 써도 팔수록 손해인 창입니다
          (개당 공헌이익 ≤ 0). 본전 ROAS는 존재하지 않습니다.
        </div>
      )}
      {t && overBep && !bepNonPositive && (
        <div className="text-xs bg-red-50 border border-red-200 rounded px-2 py-1 mb-2 text-red-700">
          {adKnown
            ? `실광고비가 BEP 광고비를 초과 — 이 창은 적자입니다.`
            : `계정 전체 광고비(${won(adShown)})가 BEP 광고비를 넘습니다. 상한 비교라 적자 확정은 아니지만, 이 프로모션 SKU에 계정 광고비의 ${Number(adShown) > 0 ? Math.round((Number(t.bep_ad_spend) / Number(adShown)) * 100) : "?"}% 넘게 쓰였다면 적자입니다.`}
        </div>
      )}

      {/* SKU 분해 */}
      {p.skus.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-gray-500 border-b border-gray-200">
                <th className="py-1 pr-2">SKU</th>
                <th className="py-1 pr-2 text-right">판매</th>
                <th className="py-1 pr-2 text-right">실현단가</th>
                <th className="py-1 pr-2 text-right">납품단가</th>
                <th className="py-1 pr-2 text-right">원가</th>
                <th className="py-1 pr-2 text-right">분담금</th>
                <th className="py-1 pr-2 text-right">개당 공헌이익</th>
                <th className="py-1 pr-2 text-right">BEP ROAS</th>
              </tr>
            </thead>
            <tbody>
              {p.skus.map((s) => (
                <tr key={s.sku_id} className={`border-b border-gray-50 ${s.resolved ? "" : "bg-amber-50/40"}`}>
                  <td className="py-1.5 pr-2 text-gray-700">
                    <span className="font-mono">{s.sku_id}</span>
                    {s.product_name && <div className="text-gray-400">{s.product_name.slice(0, 30)}</div>}
                    {!s.resolved && (
                      <div className="text-amber-700">{s.unresolved_reasons.join(" / ")}</div>
                    )}
                  </td>
                  <td className="py-1.5 pr-2 text-right text-gray-600">{num(s.qty)}</td>
                  <td className="py-1.5 pr-2 text-right text-gray-600">{won(s.realized_unit_price)}</td>
                  <td className="py-1.5 pr-2 text-right text-gray-600">{won(s.supply_unit_price)}</td>
                  <td className="py-1.5 pr-2 text-right text-gray-600">{won(s.cost_price)}</td>
                  <td className="py-1.5 pr-2 text-right text-gray-600">{won(s.funding)}</td>
                  <td className="py-1.5 pr-2 text-right text-gray-700 font-semibold"><Money v={s.unit_contribution} /></td>
                  <td className="py-1.5 pr-2 text-right text-purple-700 font-semibold">{ratioX(s.bep_roas)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 수기 입력 — 개당 할인액(D-CPP-7) + 대상 SKU(API에 적용상품 목록이 없다) */}
      <div className="mt-2 pt-2 border-t border-gray-100 flex flex-wrap items-end gap-2">
        <label className="text-xs text-gray-500">
          개당 할인액(원)
          <input
            value={edit.disc}
            onChange={(e) => onEdit({ ...edit, disc: e.target.value })}
            placeholder="예: 4000"
            className="block mt-0.5 w-28 px-2 py-1 text-xs border border-gray-300 rounded"
          />
        </label>
        <label className="text-xs text-gray-500 flex-1 min-w-[220px]">
          대상 SKU(상품번호, 쉼표 구분)
          <input
            value={edit.skus}
            onChange={(e) => onEdit({ ...edit, skus: e.target.value })}
            placeholder="예: 62178970, 69411570"
            className="block mt-0.5 w-full px-2 py-1 text-xs border border-gray-300 rounded"
          />
        </label>
        <button
          onClick={onSave}
          disabled={saving}
          className="px-3 py-1 text-xs bg-purple-600 text-white rounded hover:bg-purple-700 disabled:opacity-50"
        >
          {saving ? "저장 중…" : "저장"}
        </button>
      </div>
      <p className="mt-1 text-xs text-gray-400">
        빈 칸은 <b>0이 아니라 '모름'</b>으로 저장됩니다. 프로모션 API에 적용상품 목록이 없어 대상
        SKU는 수기 지정입니다(추정 매핑 금지). {p.window_basis}
      </p>
    </div>
  );
}
