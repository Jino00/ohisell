// CoupangOps.tsx — 🔧 쿠팡 운영 패널
// 회사·기간별 매출 현황 + 상품별 상세. 컬럼 필터(▼) 드롭다운으로 값 선택 표시/숨김.
import { useState, useEffect, useCallback, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { Spinner, BusyOverlay, MIN_BUSY_MS } from "../components/Busy";
import { PeriodRangeBar, type PeriodPreset } from "../components/PeriodRangeBar";
import { customRangeError, kstDate, OPS_MAX_SPAN_DAYS } from "../lib/periodRange";
import { fetchSalesSummary, getCoupangAdCostDaily, requestAdCostRefresh, getAdCostRefreshStatus, type SalesSummary, type SalesProductRow, type SalesSellTypeRow, type SalesSummaryData } from "../lib/api";

const COMPANIES = [
  { value: "ALL", label: "전체" },
  { value: "오픽스", label: "오픽스" },
  { value: "오하이테크", label: "오하이테크" },
];
// ★「1년」은 넣지 않는다 — 백엔드(`utils/date_range.py`) 상한이 90일이라 누르는 즉시 400이다.
const COUPANG_PERIOD_PRESETS: PeriodPreset[] = ["today", "yesterday", "7d", "15d", "30d", "90d"];
const CHANNEL_TYPES = ["전체", "Wing", "로켓그로스", "로켓배송"] as const;
type ChannelType = (typeof CHANNEL_TYPES)[number];
type SortKey = "product_name" | "revenue" | "ad_spend" | "conv_revenue" | "roas" | "profit" | "profit_rate";
type SortDir = "asc" | "desc";
type ColKey = "revenue" | "ad_spend" | "conv_revenue" | "roas" | "profit" | "profit_rate";

function won(s: string | null | undefined) {
  if (s == null) return "—";
  const n = Math.round(Number(s));
  return `${n.toLocaleString("ko-KR")}원`;
}
function roasFmt(s: string | null | undefined) {
  if (s == null) return "—";
  return `${Number(s).toFixed(2)}x`;
}
function pct(s: string | null | undefined) {
  if (s == null) return "—";
  return `${Number(s).toFixed(1)}%`;
}
function profitColor(s: string | null | undefined) {
  if (s == null) return "text-gray-900";
  const n = Number(s);
  return n > 0 ? "text-blue-700" : n < 0 ? "text-red-600" : "text-gray-500";
}
/** 판매유형(2P/3P) 분해 — 쿠팡이 가져가는 몫이 두 배 넘게 다르므로 뭉치면 어느 쪽이
 *  버는지 안 보인다. 3P(Wing)=판매수수료+VAT ≈8.58% / 2P(로켓그로스)=매출의 ~19.5%+
 *  (판매수수료·입출고·배송·보관·RG광고, D-18).
 *
 *  ★1P(로켓배송)는 이 표에 없다 — 쿠팡이 우리에게서 매입하는 구조라 매출이 주문 테이블에
 *    아예 없어서 같은 잣대로 셀 수 없기 때문이다. 그래서 광고비만 있고 매출이 없다.
 *    빼되 숨기지는 않는다: 뺀 금액을 아래 각주로 항상 보인다. */
export function SellTypeBreakdown({ rows, summary, refDate }: {
  rows: SalesSellTypeRow[] | undefined;
  summary: SalesSummaryData | undefined;
  /** 「오늘」 탭에서 광고 수치가 실제로는 이 날짜(최신 XLSX) 기준임을 밝힌다. */
  refDate?: string | null;
}) {
  if (!rows || rows.length === 0) return null;
  const excluded = Number(summary?.excluded_ad_spend ?? 0);
  const unassigned = Number(summary?.ad_spend_unassigned ?? 0);
  const label = (r: SalesSellTypeRow) =>
    r.sell_type === "3P" ? "3P · Wing"
      : r.sell_type === "2P" ? "2P · 로켓그로스"
      : "판매유형 미배분";

  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <div className="px-3 py-2 border-b border-gray-100">
        <span className="text-xs font-semibold text-gray-700">판매유형별</span>
        <span className="ml-2 text-[11px] text-gray-400 break-keep">
          쿠팡이 가져가는 몫이 다르다 — 3P는 판매수수료+VAT, 2P는 거기에 입출고·배송·보관·RG광고까지
          {refDate ? ` · 광고 수치는 ${refDate} 기준(오늘치는 익일 확정)` : ""}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs sm:text-sm">
          <thead className="bg-gray-50 text-gray-500">
            <tr>
              <th className="px-3 py-1.5 text-left font-medium">판매유형</th>
              <th className="px-3 py-1.5 text-right font-medium">매출</th>
              <th className="px-3 py-1.5 text-right font-medium">쿠팡 비용</th>
              <th className="px-3 py-1.5 text-right font-medium">원가</th>
              <th className="px-3 py-1.5 text-right font-medium">광고비</th>
              <th className="px-3 py-1.5 text-right font-medium">배송·물류비</th>
              <th className="px-3 py-1.5 text-right font-medium">이익<span className="font-normal text-gray-400">(광고 전)</span></th>
              <th className="px-3 py-1.5 text-right font-medium">이익률</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map((r) => (
              <tr key={r.channel_type} className={r.sell_type === null ? "bg-amber-50/60" : undefined}>
                <td className="px-3 py-1.5 whitespace-nowrap text-gray-700">{label(r)}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{won(r.revenue)}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{won(r.fee)}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{won(r.cost)}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{won(r.ad_spend)}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{won(r.shipping)}</td>
                <td className={`px-3 py-1.5 text-right tabular-nums font-medium ${profitColor(r.profit)}`}>
                  {won(r.profit)}
                </td>
                <td className={`px-3 py-1.5 text-right tabular-nums ${profitColor(r.profit)}`}>
                  {r.profit_rate ? pct(r.profit_rate) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(excluded > 0 || unassigned > 0 || rows.some((r) => Number(r.ad_spend) > 0)) && (
        <div className="px-3 py-2 border-t border-gray-100 text-[11px] text-gray-500 break-keep space-y-0.5">
          {excluded > 0 && (
            <div>
              ※ 로켓배송(1P) 광고비 <b className="text-gray-700">{won(summary?.excluded_ad_spend)}</b>는 위 합계에
              들어있지 않다 — 1P는 쿠팡이 매입하는 구조라 <b>매출이 주문에 잡히지 않아</b> 같은 잣대로 셀 수 없다.
              그 손익은 로켓배송 화면이 납품가 축으로 따로 본다.
            </div>
          )}
          <div>
            ※ <b className="text-gray-700">광고비는 판매유형으로 가르지 않는다</b> — 광고 원장의 판매방식
            라벨이 <b>실제 판매경로를 뜻하지 않기 때문</b>이다(D-CPP-43 1차 출처: 오픽스 PA 광고비의
            97.28%가 <b>RG로 팔리는 옵션</b>에 쓰이는데 라벨은 「3P」다). 라벨대로 가르면 매출 없는 축에
            광고비만 쌓여 이익률이 −1000%대로 찍힌다. 그래서 <b>3P·2P 행의 이익은 「광고비 전」</b>이고,
            광고비는 아래 「판매유형 미배분」 한 행에 모아 둔다. 각 열 합계는 상단 카드와 정확히 일치한다.
          </div>
          {unassigned > 0 && (
            <div>
              ※ 그중 <b className="text-gray-700">{won(summary?.ad_spend_unassigned)}</b>는 옵션 분해가
              없는 날의 <b>계정 단위 일별 집계</b>다(옵션별로도 못 가른다).
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function fmtVal(row: SalesProductRow, col: ColKey): string {
  if (col === "revenue") return won(row.revenue);
  if (col === "ad_spend") return won(row.ad_spend);
  if (col === "conv_revenue") return won(row.conv_revenue);
  if (col === "profit") return won(row.profit);
  if (col === "profit_rate") return row.profit_rate ? pct(row.profit_rate) : "—";
  return row.roas ? roasFmt(row.roas) : "—";
}
function numOf(s: string): number {
  const n = Number(s.replace(/[^0-9.-]/g, ""));
  return isNaN(n) ? 0 : n;
}
// 쿠팡 비용 카드 부가표기 — D-18 판매유형별 쿠팡 총비용
// 3P(Wing): 판매수수료+VAT / 2P(RG): 판매수수료+VAT+풀필먼트+RG광고
function feeSub(ratio: number | null | undefined): string | undefined {
  if (ratio == null || ratio >= 0.999) return "Wing 수수료+VAT · RG 수수료+풀필먼트+광고";
  return ratio <= 0.001 ? "정산 전 — 추정 포함" : `정산 ${Math.round(ratio * 100)}% · 나머지 추정`;
}
// 원가 카드 부가표기 — 원가 매핑 보유 매출 비율(미설정분은 0으로 빠져 이익 과대).
function costSub(cov: number | null | undefined): string | undefined {
  if (cov == null || cov >= 0.999) return undefined;
  return `${Math.round(cov * 100)}% 반영 (일부 원가 미설정)`;
}
// 배송·물류비 카드 부가표기 — Wing 한진 1,900/건 (RG 풀필먼트는 쿠팡 비용에 포함됨, D-18)
function shipSub(_rgFf: string | null | undefined): string {
  return "Wing 한진 1,900원/건 (RG 풀필먼트는 쿠팡 비용에 포함)";
}
// 오늘 광고비 카드 부가표기 — 마지막 fetch 시각(KST). 광고센터 누적은 실시간이라
// 마지막 갱신 이후 격차가 생긴다 → 갱신 버튼으로 최신화 안내(실시간 오인 방지).
function adTodaySub(synced: string | null | undefined): string {
  if (!synced) return "‘광고비 갱신’으로 최신화";
  const hhmm = synced.slice(11, 16);  // naive KST ISO → HH:MM
  return `${hhmm} 갱신 기준 · 버튼으로 최신화`;
}

// RoAS/이익률 색상 — 테이블·모바일 카드 공용(단일 출처).
function roasClass(s: string | null | undefined): string {
  if (s == null) return "text-gray-300";
  const n = Number(s);
  return n >= 3 ? "text-green-600 font-medium" : n >= 1 ? "text-gray-700" : "text-red-500";
}
function rateClass(s: string | null | undefined): string {
  if (s == null) return "text-gray-300";
  const n = Number(s);
  return n >= 20 ? "text-blue-600 font-medium" : n >= 0 ? "text-gray-700" : "text-red-500";
}

// 모바일 카드용 지표 셀 — 라벨 + 값(우측정렬 숫자). 테이블이 좁은 화면에서 잘리는 문제 대체.
function StatCell({ label, value, valueClass }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] text-gray-400 mb-0.5 break-keep">{label}</div>
      <div className={`text-xs tabular-nums break-keep ${valueClass ?? "text-gray-800"}`}>{value}</div>
    </div>
  );
}

function SummaryCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-3 sm:p-4">
      <div className="text-xs text-gray-500 mb-1 break-keep">{label}</div>
      <div className="text-base sm:text-xl font-bold text-gray-900 tabular-nums break-keep">{value}</div>
      {sub && <div className="text-[11px] sm:text-xs text-gray-400 mt-0.5 break-keep leading-tight">{sub}</div>}
    </div>
  );
}

export default function CoupangOps() {
  const [company, setCompany] = useState("ALL");
  // 기간은 **항상 날짜 두 개**다 — 프리셋 버튼은 그 두 칸을 채우는 단축키일 뿐이다.
  // 종전 기본과 같은 최근 7일. 날짜 계산은 `kstDate`만 쓴다(타임존이 걸린 유일한 코드).
  const [from, setFrom] = useState(() => kstDate(-6));
  const [to, setTo] = useState(() => kstDate(0));
  // 백엔드가 막는 입력은 프론트가 먼저 막는다(빈 칸·뒤집힘·미래).
  // ★상한은 백엔드와 짝(90일) — 화면 note가 「최대 90일」이라 적어놓고 안 막으면
  //   400 원문이 새고 표는 이전 구간 숫자를 그대로 보인다(적대 리뷰 1R P1-2).
  const rangeError = customRangeError({ from, to }, undefined, OPS_MAX_SPAN_DAYS);
  const [channelFilter, setChannelFilter] = useState<ChannelType>("전체");
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("revenue");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  // 컬럼 필터: 제외할 값 집합 (비어있으면 전체 표시)
  const [colExcluded, setColExcluded] = useState<Record<string, Set<string>>>({});
  const [openCol, setOpenCol] = useState<string | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const [data, setData] = useState<SalesSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);

  // 광고 쿠키 설정 (advertising.coupang.com)
  // 전역 만료 배너 CTA(?adcookie=open)로 진입하면 패널 자동 펼침
  const [searchParams] = useSearchParams();
  const [showAdSettings, setShowAdSettings] = useState(searchParams.get("adcookie") === "open");
  const [adCurl, setAdCurl] = useState("");
  const [adCookieStatus, setAdCookieStatus] = useState<{ configured: boolean; status: string; last_success_at: string | null; last_error: string | null } | null>(null);
  const [adSaving, setAdSaving] = useState(false);
  const [adSyncMsg, setAdSyncMsg] = useState<string | null>(null);
  const [todayAdCost, setTodayAdCost] = useState<number | null>(null);  // 오늘 라이브 광고비(coupang_ad_cost_daily)
  const [adRefreshing, setAdRefreshing] = useState(false);
  const adPanelRef = useRef<HTMLDivElement | null>(null);

  // 전역 배너 CTA(?adcookie=open)는 이미 이 페이지에 있을 때도 눌린다 — useState 초기값만으로는
  // 재마운트가 없어 무반응이었음(사용자 "안눌리는 느낌" 보고). 네비게이션마다 패널 펼침+스크롤+포커스.
  useEffect(() => {
    if (searchParams.get("adcookie") !== "open") return;
    setShowAdSettings(true);
    requestAnimationFrame(() => {
      adPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      adPanelRef.current?.querySelector("textarea")?.focus();
    });
  }, [searchParams]);

  const API_BASE = import.meta.env.DEV ? "http://localhost:8000" : "";

  async function triggerSync() {
    const r = await fetch(`${API_BASE}/api/scheduler/trigger/auto_sync_orders`, { method: "POST" });
    if (!r.ok) throw new Error(`sync failed: ${r.status}`);
    return r.json();
  }

  async function loadAdCookieStatus() {
    try {
      const r = await fetch(`${API_BASE}/api/coupang/ops/ad-cost/cookie/status`);
      if (r.ok) setAdCookieStatus(await r.json());
    } catch { /* 조용히 실패 */ }
  }

  async function saveAdCookie() {
    if (!adCurl.trim()) return;
    setAdSaving(true);
    setAdSyncMsg(null);
    try {
      const r = await fetch(`${API_BASE}/api/coupang/ops/ad-cost/cookie`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ curl: adCurl }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "저장 실패");
      setAdSyncMsg("✅ 쿠키 저장 완료");
      setAdCurl("");
      await loadAdCookieStatus();
    } catch (e: any) {
      setAdSyncMsg("❌ " + e.message);
    } finally {
      setAdSaving(false);
    }
  }

  // 오늘 라이브 광고비(coupang_ad_cost_daily, Mac 페처가 채움) 로드.
  async function loadTodayAdCost() {
    try {
      const today = kstDate(0);
      const { costs } = await getCoupangAdCostDaily(today, today);
      const total = costs.reduce((s, c) => s + (c.day_cost || 0), 0);
      setTodayAdCost(costs.length ? total : null);
    } catch { /* 조용히 실패 */ }
  }

  // "광고비 갱신" — Jino Mac 페처를 깨워 오늘 쿠팡 광고비를 즉시 가져온다(Akamai로 prod 직접 fetch 불가).
  // request-refresh → Mac 데몬이 fetch·push → last_success_at 변화를 폴링해 완료 감지 → 오늘 광고비 리로드.
  async function refreshAdCostNow() {
    setAdRefreshing(true);
    setAdSyncMsg("Mac에서 광고비 가져오는 중… (~20초, 첫 갱신이면 Mac 로그인 창 확인)");
    try {
      const before = await getAdCostRefreshStatus();
      const baseline = before.last_success_at;
      const errBaseline = before.last_error_at; // 실패도 감지해야 "진행 중"과 구분된다
      await requestAdCostRefresh();
      const deadline = Date.now() + 215000; // 215초 — 데몬 로그인 대기(180s)+fetch 여유까지 커버
      let done = false;
      let failed: string | null = null;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 3000));
        const st = await getAdCostRefreshStatus();
        if (st.last_success_at && st.last_success_at !== baseline) { done = true; break; }
        // 페처가 **종료된** 실패를 보고하면 즉시 이탈 — 이게 없으면 이미 끝난 실패를 215초 헛기다린다.
        // ★requested가 아직 true면 재시도가 남아 있다는 뜻(lease 계약, 2026-07-27) — 여기서
        // 이탈하면 1회차 실패를 최종 실패로 오보한다. 요청이 소멸(=재시도 소진/로그인 필요)한
        // 뒤에야 실패로 판정한다. last_error에는 소멸 사유가 들어 있다.
        if (st.last_error_at && st.last_error_at !== errBaseline && !st.requested) {
          failed = st.last_error || "원인 미상";
          break;
        }
        // 새 실패 없이 요청만 사라졌다 = 수집이 정상 종료됐다(예: RG "받을 정산주기 없음").
        // 이 분기가 없으면 성공한 무작업 회차를 타임아웃까지 기다린 뒤 "응답 없음"으로 오보한다.
        if (!st.requested) { done = true; break; }
      }
      if (done) {
        await loadTodayAdCost();
        await loadAdCookieStatus();
        setAdSyncMsg("✅ 광고비 갱신 완료");
        setTimeout(() => setAdSyncMsg(null), 4000);
      } else if (failed) {
        await loadAdCookieStatus();
        setAdSyncMsg("❌ Mac 페처 실패: " + failed);
      } else {
        setAdSyncMsg("⚠️ Mac 응답 없음 — Mac이 켜져 있는지, 첫 갱신이면 로그인 창을 확인하세요.");
      }
    } catch (e: any) {
      setAdSyncMsg("❌ 갱신 요청 실패: " + (e?.message || ""));
    } finally {
      setAdRefreshing(false);
    }
  }

  async function syncNow() {
    setSyncing(true);
    setSyncMsg(null);
    try {
      await triggerSync();
      setSyncMsg("동기화 완료");
      // 동기화가 2분+ 걸리므로 그 사이 바뀐 기간을 존중한다(클릭 시점 값이 아니라 현재 값).
      await load(selRef.current.company, selRef.current.from, selRef.current.to);
      await loadAdCookieStatus();
      await loadTodayAdCost();
    } catch (e: any) {
      setSyncMsg("동기화 실패: " + e.message);
    } finally {
      setSyncing(false);
    }
  }

  // 연달아 기간을 바꾸면 응답 도착 순서가 요청 순서와 다를 수 있다 → 마지막 요청만 반영.
  // 최소 노출 시간(MIN_BUSY_MS)을 채운 뒤 값을 갈아끼워, 그동안 옛 값은 흐린 채로 남긴다.
  const reqSeq = useRef(0);
  const load = useCallback(async (c: string, f: string, t: string) => {
    // 잘못된 구간이면 요청 자체를 안 보낸다 — 조용히 보정하지 않고 화면이 말한다.
    if (customRangeError({ from: f, to: t }, undefined, OPS_MAX_SPAN_DAYS)) return;
    const seq = ++reqSeq.current;
    const t0 = performance.now();
    setLoading(true);
    setError(null);
    try {
      // 기간은 날짜로만 보낸다 — `days`는 이제 이 화면에 없다(프리셋도 날짜를 채울 뿐).
      const r = await fetchSalesSummary(c, 0, f, t);
      const rest = MIN_BUSY_MS - (performance.now() - t0);
      if (rest > 0) await new Promise((res) => setTimeout(res, rest));
      if (seq !== reqSeq.current) return;   // 더 최신 요청이 진행 중 — 이 응답은 버린다
      setData(r);
      setColExcluded({});
    } catch (e: any) {
      if (seq !== reqSeq.current) return;
      setError(e.message);
    } finally {
      if (seq === reqSeq.current) setLoading(false);
    }
  }, []);

  // ★지연 실행되는 콜백은 이 ref로 **현재 선택**을 읽는다.
  // 왜: 아래 마운트 이펙트는 triggerSync(라이브 실측 2분+)를 기다렸다가 load를 부르는데,
  // 그 이펙트가 붙잡은 company·days는 **마운트 시점의 값**이다. 그 사이 사용자가 기간을
  // 바꾸면 2분 뒤에 옛 기간을 새로 요청해 화면을 되돌린다(스마트스토어에서 같은 기계가
  // 실제로 화면을 덮은 것을 2026-08-06 네트워크 로그로 확인했다).
  const selRef = useRef({ company, from, to });
  // 렌더 중 대입이 아니라 커밋 후에 갱신한다 — 동시성 렌더에서 **버려진 렌더의 값**이 ref에
  // 남을 수 있기 때문이다(적대 리뷰 P2). 이 ref를 읽는 쪽은 전부 커밋 이후에 도는 지연 콜백이다.
  useEffect(() => { selRef.current = { company, from, to }; }, [company, from, to]);

  // 페이지 접속 시 자동 sync → 완료 후 데이터 로드
  useEffect(() => {
    let cancelled = false;
    setSyncing(true);
    (async () => {
      try { await triggerSync(); } catch { /* sync 실패해도 기존 데이터 표시 */ }
      if (!cancelled) {
        setSyncing(false);
        // 마운트 시점이 아니라 **지금 선택된** 기간을 읽는다(위 selRef 주석 참조).
        load(selRef.current.company, selRef.current.from, selRef.current.to);
        loadAdCookieStatus();
        loadTodayAdCost();
      }
    })();
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);  // 마운트 1회만

  useEffect(() => { load(company, from, to); }, [company, from, to, load]);

  // 드롭다운 외부 클릭 시 닫기
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpenCol(null);
      }
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    else { setSortKey(key); setSortDir("desc"); }
  }

  const allRows = data?.by_product ?? [];

  // 컬럼별 unique 값 목록 (전체 데이터 기준)
  function uniqueVals(col: ColKey): string[] {
    const vals = [...new Set(allRows.map((r) => fmtVal(r, col)))];
    return vals.sort((a, b) => numOf(a) - numOf(b));
  }

  // 필터 적용
  const filtered = allRows
    .filter((row) => {
      const matchCh = channelFilter === "전체" || row.channel_type === channelFilter;
      const q = search.toLowerCase();
      const matchQ =
        !q ||
        row.product_name.toLowerCase().includes(q) ||
        row.option_name.toLowerCase().includes(q);
      return matchCh && matchQ;
    })
    .filter((row) => {
      for (const [col, excluded] of Object.entries(colExcluded)) {
        if (excluded.size > 0 && excluded.has(fmtVal(row, col as ColKey))) return false;
      }
      return true;
    })
    .sort((a, b) => {
      const mul = sortDir === "desc" ? -1 : 1;
      if (sortKey === "product_name")
        return mul * `${a.product_name},${a.option_name}`.localeCompare(`${b.product_name},${b.option_name}`, "ko");
      const getV = (r: SalesProductRow) => {
        if (sortKey === "roas") return Number(r.roas ?? 0);
        if (sortKey === "profit_rate") return Number(r.profit_rate ?? 0);
        return Number((r as any)[sortKey] ?? 0);
      };
      const av = getV(a);
      const bv = getV(b);
      return mul * (av - bv);
    });

  // 합계(테이블 tfoot + 모바일 카드 공용) — 단일 출처로 중복 제거.
  const totals = {
    revenue: filtered.reduce((a, r) => a + Number(r.revenue), 0),
    ad_spend: filtered.reduce((a, r) => a + Number(r.ad_spend), 0),
    conv_revenue: filtered.reduce((a, r) => a + Number(r.conv_revenue), 0),
    profit: filtered.reduce((a, r) => a + Number(r.profit), 0),
  };
  const totalRoas = totals.ad_spend ? `${(totals.conv_revenue / totals.ad_spend).toFixed(2)}x` : "—";
  const totalRate = totals.revenue ? `${((totals.profit / totals.revenue) * 100).toFixed(1)}%` : "—";

  // 컬럼 헤더 (정렬 + 필터 드롭다운)
  function ColHeader({ col, label, align = "right" }: { col: ColKey; label: string; align?: "left" | "right" }) {
    const vals = uniqueVals(col);
    const excluded = colExcluded[col] ?? new Set<string>();
    const hasFilter = excluded.size > 0;
    const isOpen = openCol === col;
    const isSorted = sortKey === col;

    function toggleVal(v: string) {
      setColExcluded((prev) => {
        const next = new Set(prev[col] ?? []);
        if (next.has(v)) next.delete(v);
        else next.add(v);
        return { ...prev, [col]: next };
      });
    }
    function selectAll() { setColExcluded((prev) => ({ ...prev, [col]: new Set() })); }
    function deselectAll() { setColExcluded((prev) => ({ ...prev, [col]: new Set(vals) })); }

    return (
      <th className={`px-3 py-2 text-${align} select-none`}>
        <div className={`inline-flex items-center gap-0 ${align === "right" ? "justify-end" : ""} w-full`}>

          {/* 정렬 버튼 — 라벨 전체 영역 클릭 */}
          <button
            className={`flex items-center gap-1 px-1.5 py-0.5 rounded hover:bg-gray-200 cursor-pointer transition-colors ${isSorted ? "text-blue-600 font-semibold" : "text-gray-500 hover:text-gray-800"}`}
            onClick={() => toggleSort(col as SortKey)}
            title={isSorted ? (sortDir === "desc" ? "내림차순 정렬 중 — 클릭하면 오름차순" : "오름차순 정렬 중 — 클릭하면 내림차순") : "클릭하여 정렬"}
          >
            {label}
            <span className={`text-xs ml-0.5 ${isSorted ? "text-blue-500" : "text-gray-300"}`}>
              {isSorted ? (sortDir === "desc" ? "↓" : "↑") : "↕"}
            </span>
          </button>

          {/* 필터 버튼 — 깔때기 아이콘으로 정렬 버튼과 명확히 구분 */}
          <div className="relative" ref={isOpen ? dropdownRef : undefined}>
            <button
              onClick={(e) => { e.stopPropagation(); setOpenCol(isOpen ? null : col); }}
              className={`ml-1 text-xs px-1 py-0.5 rounded border transition-colors ${
                hasFilter
                  ? "bg-blue-500 text-white border-blue-500"
                  : "text-gray-400 border-gray-300 hover:text-gray-700 hover:bg-gray-100"
              }`}
              title="값 필터"
            >
              {hasFilter ? "🔵" : "⊟"}
            </button>
            {isOpen && (
              <div
                className="absolute top-full right-0 z-50 bg-white border border-gray-200 rounded-lg shadow-xl mt-1 w-44"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="flex gap-2 px-3 py-2 border-b border-gray-100 text-xs font-medium text-gray-600">
                  값 필터
                  <span className="ml-auto flex gap-2">
                    <button onClick={selectAll} className="text-blue-600 hover:underline">전체</button>
                    <button onClick={deselectAll} className="text-red-500 hover:underline">해제</button>
                  </span>
                </div>
                <div className="max-h-52 overflow-y-auto py-1">
                  {vals.map((v) => (
                    <label
                      key={v}
                      className="flex items-center gap-2 px-3 py-1.5 cursor-pointer hover:bg-gray-50 text-xs text-gray-700"
                    >
                      <input type="checkbox" className="accent-blue-500" checked={!excluded.has(v)} onChange={() => toggleVal(v)} />
                      {v}
                    </label>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </th>
    );
  }

  const s = data?.summary;
  const activeFilters = Object.values(colExcluded).filter((s) => s.size > 0).length;

  return (
    <div onClick={() => setOpenCol(null)}>
      {/* ── 헤더 ── */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
        <div>
          <h2 className="text-xl font-bold text-gray-900">🔧 쿠팡 운영 패널</h2>
          {/* 갱신 중에는 기간 라벨 대신 진행 표시를 낸다 — 옛 기간 라벨을 새 값으로 오독하지 않게. */}
          {loading ? (
            <p className="inline-flex items-center gap-1.5 text-xs font-medium text-blue-700 mt-0.5">
              <Spinner className="w-3 h-3" /> 데이터 업데이트 중…
            </p>
          ) : data && (
            <p className="text-xs text-gray-400 mt-0.5">{data.period.from} ~ {data.period.to}</p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={syncNow}
            disabled={syncing}
            className="px-3 py-1.5 rounded text-sm font-medium bg-green-600 text-white hover:bg-green-700 disabled:opacity-50 flex items-center gap-1"
            title="최신 주문 동기화 후 새로고침"
          >
            {syncing ? "동기화 중…" : "🔄 동기화"}
          </button>
          {syncMsg && <span className="text-xs text-gray-500">{syncMsg} (3초 후 갱신)</span>}
          {/* 오늘 라이브 광고비 + 즉시 갱신(버튼 클릭 시 Mac 페처가 가져옴) */}
          <span className="text-sm text-gray-600 border-l border-gray-200 pl-2">
            오늘 광고비{" "}
            <span className="font-semibold text-gray-900">
              {todayAdCost == null ? "—" : `${todayAdCost.toLocaleString("ko-KR")}원`}
            </span>
          </span>
          <button
            onClick={refreshAdCostNow}
            disabled={adRefreshing}
            className="px-3 py-1.5 rounded text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 flex items-center gap-1"
            title="Jino Mac에서 오늘 광고비를 지금 가져옵니다(~20초). Mac이 켜져 있어야 합니다."
          >
            {adRefreshing ? "갱신 중…" : "📣 광고비 갱신"}
          </button>
          {adSyncMsg && <span className="text-xs text-gray-500">{adSyncMsg}</span>}
          <button
            onClick={() => setShowAdSettings((v) => !v)}
            className={`px-3 py-1.5 rounded text-sm font-medium border transition-colors ${
              adCookieStatus?.status === "green" ? "border-green-400 text-green-700 bg-green-50 hover:bg-green-100"
              : adCookieStatus?.status === "red" ? "border-red-400 text-red-700 bg-red-50 hover:bg-red-100"
              : "border-gray-300 text-gray-600 bg-gray-50 hover:bg-gray-100"
            }`}
            title="광고비 쿠키 설정(레거시)"
          >
            ⚙️ {adCookieStatus?.status === "green" ? "🟢" : adCookieStatus?.status === "red" ? "🔴" : "⬜"}
          </button>
          <span className="hidden sm:inline text-xs text-gray-400 border-l border-gray-200 pl-2">
            ※ 쿠팡 API 약 1~2시간 지연 발생 가능
          </span>
        </div>
      </div>

      {/* ── 기간 선택 ── 공용 `PeriodRangeBar`. 화면마다 날짜 UI를 따로 들면 곧 갈라지므로
          같은 물건을 쓰고, **축 이름만** 이 화면이 정한다(여기는 「판매일」).
          ★「오늘」을 고르면(=오늘~오늘) 백엔드가 `ad_ref_date`를 실어 주고 아래 요약이
            「오늘」 전용 레이아웃으로 갈라진다 — 그 분기는 날짜가 정하지 이 바가 정하지 않는다. */}
      <div className="mb-4">
        <PeriodRangeBar
          label="판매일"
          from={from} to={to} onFrom={setFrom} onTo={setTo}
          presets={COUPANG_PERIOD_PRESETS}
          right={
            /* 같은 기간을 다시 골라도 상태가 안 바뀌면 이펙트가 안 돈다 — 조회 실패 후
               회복 수단이 사라지지 않게 «다시 조회»를 남긴다(종전엔 같은 버튼 재클릭이 그 역할). */
            <button
              onClick={() => load(company, from, to)}
              disabled={loading || Boolean(rangeError)}
              className="px-3 py-1.5 rounded text-sm font-medium bg-gray-100 text-gray-600 hover:bg-gray-200 disabled:opacity-50 inline-flex items-center gap-1.5"
            >
              {loading && <Spinner className="w-3.5 h-3.5" />}
              🔁 다시 조회
            </button>
          }
          note={rangeError
            ? <span className="font-medium text-red-600">
                {rangeError} — 기간을 고칠 때까지 조회하지 않습니다
              </span>
            : <>기간은 <b>판매일(KST)</b> 기준이며 양끝을 포함합니다. 조회 구간은 최대 90일입니다.</>}
        />
      </div>

      {/* ── 광고 쿠키 설정 패널 ── */}
      {showAdSettings && (
        <div ref={adPanelRef} className="mb-4 bg-orange-50 border border-orange-200 rounded-lg p-4 text-sm">
          <div className="flex items-center gap-2 mb-3">
            <span className="font-semibold text-orange-800">📣 쿠팡 광고비 쿠키 설정</span>
            <span className="text-xs text-gray-500">advertising.coupang.com 세션쿠키 — 매일 자정 자동 동기화</span>
            {adCookieStatus && (
              <span className={`ml-auto text-xs px-2 py-0.5 rounded-full font-medium ${
                adCookieStatus.status === "green" ? "bg-green-100 text-green-700"
                : adCookieStatus.status === "red" ? "bg-red-100 text-red-700"
                : "bg-gray-100 text-gray-500"
              }`}>
                {adCookieStatus.status === "green" ? "🟢 정상"
                  : adCookieStatus.status === "red" ? "🔴 만료"
                  : adCookieStatus.configured ? "⬜ 미확인" : "⬜ 미설정"}
                {adCookieStatus.last_success_at && (
                  <span className="ml-1 text-gray-400">({adCookieStatus.last_success_at.slice(0, 10)})</span>
                )}
              </span>
            )}
          </div>
          <div className="text-xs text-gray-500 mb-2">
            1. advertising.coupang.com 광고 대시보드 접속 → DevTools Network → cost 요청 우클릭 → "Copy as cURL" → 아래 붙여넣기
          </div>
          <div className="flex gap-2">
            <textarea
              value={adCurl}
              onChange={(e) => setAdCurl(e.target.value)}
              placeholder="여기에 복사한 cURL을 그대로 붙여넣으세요 (curl 'https://advertising.coupang.com/…' -H 'cookie: …' 형태)"
              className="flex-1 h-16 px-3 py-2 text-xs border border-gray-300 rounded-lg font-mono resize-none focus:outline-none focus:ring-1 focus:ring-orange-400"
            />
            <div className="flex flex-col gap-1">
              <button
                onClick={saveAdCookie}
                disabled={adSaving || !adCurl.trim()}
                className="px-4 py-2 rounded text-xs font-medium bg-orange-600 text-white hover:bg-orange-700 disabled:opacity-50"
              >
                {adSaving ? "저장 중…" : "💾 저장"}
              </button>
              <button
                onClick={refreshAdCostNow}
                disabled={adRefreshing}
                title="Jino Mac에서 오늘 광고비를 지금 가져옵니다(~20초). Mac이 켜져 있어야 합니다."
                className="px-4 py-2 rounded text-xs font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {adRefreshing ? "갱신 중…" : "📣 광고비 갱신"}
              </button>
            </div>
          </div>
          {adSyncMsg && <div className="mt-2 text-xs text-gray-700">{adSyncMsg}</div>}
          {adCookieStatus?.last_error && (
            <div className="mt-1 text-xs text-red-600">오류: {adCookieStatus.last_error}</div>
          )}
        </div>
      )}

      {/* ── 회사 탭 ── */}
      <div className="flex gap-1 mb-4 border-b border-gray-200">
        {COMPANIES.map((c) => (
          <button
            key={c.value}
            onClick={() => setCompany(c.value)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              company === c.value ? "border-blue-600 text-blue-700" : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {c.label}
          </button>
        ))}
      </div>

      {error && <div className="bg-red-50 text-red-700 rounded px-4 py-2 text-sm mb-4">{error}</div>}

      {/* ── 요약 카드 ──
          갱신 중에는 값을 지우지 않고 흐리기만 한다 + 오버레이로 상태를 말한다.
          종전에는 각 값이 "…"로 바뀌어, 이전 기간 값을 잃으면서도 "왜" 는 말하지 않았다. */}
      <div className="relative mb-6">
      <div
        className={`transition-opacity duration-150 ${loading ? "opacity-40 pointer-events-none" : ""}`}
        aria-busy={loading}
      >
      {data?.ad_ref_date ? (
        /* 오늘 선택 + 광고 기준일이 다를 때 — 판매/광고 섹션 분리 */
        <div className="space-y-3">
          {/* 오늘 판매 */}
          <div>
            <div className="text-xs text-gray-400 font-medium mb-1.5 px-0.5">
              📦 오늘 판매 ({data.period.from})
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2 sm:gap-3">
              <SummaryCard label="총 매출" value={won(s?.revenue)} />
              <SummaryCard label="쿠팡 비용" value={won(s?.fee)} sub={feeSub(s?.fee_actual_ratio)} />
              <SummaryCard label="원가" value={won(s?.cost)} sub={costSub(s?.cost_coverage)} />
              <SummaryCard label="배송·물류비" value={won(s?.shipping)} sub={shipSub(s?.rg_fulfillment)} />
              <div className={`bg-white border-2 rounded-lg p-3 sm:p-4 ${Number(s?.profit_excl_ad ?? 0) >= 0 ? "border-blue-200" : "border-red-200"}`}>
                <div className="text-xs text-gray-500 mb-1 break-keep">이익 (광고비 제외)</div>
                <div className={`text-base sm:text-xl font-bold tabular-nums break-keep ${profitColor(s?.profit_excl_ad)}`}>{won(s?.profit_excl_ad)}</div>
                {s?.profit_rate_excl_ad && <div className="text-[11px] sm:text-xs mt-0.5 text-gray-400">이익률 {pct(s.profit_rate_excl_ad)}</div>}
              </div>
            </div>
          </div>
          {/* 광고 현황 — 광고비는 오늘 실시간(일자단위), 전환·RoAS·옵션내역은 익일 확정 */}
          <div>
            <div className="text-xs text-gray-400 font-medium mb-1.5 px-0.5">
              📣 광고 현황 (광고비=오늘 실시간 · 전환매출/RoAS는 익일 확정)
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 sm:gap-3">
              <SummaryCard
                label="광고비 (오늘)"
                value={s?.ad_today != null ? won(s.ad_today) : "익일 확정"}
                sub={s?.ad_today != null ? adTodaySub(s?.ad_today_synced_at) : `어제(${data.ad_ref_date}) ${won(s?.ad_spend)}`}
              />
              <SummaryCard
                label="광고 전환 매출"
                value="익일 확정"
                sub={`어제(${data.ad_ref_date}) ${won(s?.conv_revenue)}`}
              />
              <SummaryCard
                label="RoAS"
                value="익일 확정"
                sub={s?.roas ? `어제(${data.ad_ref_date}) ${roasFmt(s.roas)}` : undefined}
              />
            </div>
          </div>
          {/* ★「오늘」 탭에도 판매유형 분해를 낸다(적대 리뷰 1R P1-1).
              이 가지에만 없으면 **1P 광고비를 빼는 바로 그 탭에서** 뺀 금액이 화면에
              한 줄도 안 남는다 — 은폐 금지는 «항상»이지 «어떤 탭에서만»이 아니다. */}
          <SellTypeBreakdown rows={data?.by_sell_type} summary={s} refDate={data.ad_ref_date} />
        </div>
      ) : (
        /* 어제·7일 등 — 동일 기간 */
        <div className="space-y-2">
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2 sm:gap-3">
            <SummaryCard label="총 매출" value={won(s?.revenue)} />
            <SummaryCard label="수수료" value={won(s?.fee)} sub={feeSub(s?.fee_actual_ratio)} />
            <SummaryCard label="원가" value={won(s?.cost)} sub={costSub(s?.cost_coverage)} />
            <SummaryCard label="광고비" value={won(s?.ad_spend)} />
            <SummaryCard label="배송·물류비" value={won(s?.shipping)} sub={shipSub(s?.rg_fulfillment)} />
            <div className={`bg-white border-2 rounded-lg p-3 sm:p-4 ${Number(s?.profit ?? 0) >= 0 ? "border-blue-200" : "border-red-200"}`}>
              <div className="text-xs text-gray-500 mb-1 break-keep">이익</div>
              <div className={`text-base sm:text-xl font-bold tabular-nums break-keep ${profitColor(s?.profit)}`}>{won(s?.profit)}</div>
              {s?.profit_rate && <div className="text-[11px] sm:text-xs mt-0.5 text-gray-400">이익률 {pct(s.profit_rate)}</div>}
            </div>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 sm:gap-3">
            <SummaryCard label="광고 전환 매출" value={won(s?.conv_revenue)} />
            <SummaryCard label="RoAS" value={roasFmt(s?.roas)} sub={s?.roas ? "광고 전환매출 ÷ 광고비" : undefined} />
            <div className="hidden sm:block" />
          </div>
          <SellTypeBreakdown rows={data?.by_sell_type} summary={s} />
        </div>
      )}
      </div>
      {loading && <BusyOverlay />}
      </div>

      {/* ── 상품별 테이블 ──
          갱신 중에도 행을 남기고 흐린다. 단 **첫 로드**에는 남길 행이 없으므로 "로딩 중…"을
          띄운다 — 이때 "데이터 없음"을 렌더하면 모르는 것을 0이라고 단언하게 된다. */}
      <div
        className={`bg-white border border-gray-200 rounded-lg overflow-hidden transition-opacity duration-150 ${
          loading ? "opacity-40 pointer-events-none" : ""
        }`}
        aria-busy={loading}
      >
        {/* 필터 바 */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-100 bg-gray-50 flex-wrap">
          <span className="text-sm font-medium text-gray-700">상품별 현황</span>
          <div className="flex gap-1">
            {CHANNEL_TYPES.map((ct) => (
              <button
                key={ct}
                onClick={() => setChannelFilter(ct)}
                className={`px-2.5 py-1 rounded text-xs font-medium ${
                  channelFilter === ct ? "bg-gray-800 text-white" : "bg-white border border-gray-300 text-gray-600 hover:bg-gray-100"
                }`}
              >
                {ct}
              </button>
            ))}
          </div>
          {activeFilters > 0 && (
            <button
              onClick={() => setColExcluded({})}
              className="text-xs text-red-500 hover:underline border-l border-gray-200 pl-3"
            >
              필터 초기화 ({activeFilters}개 적용 중)
            </button>
          )}
          {/* 모바일 정렬 — 카드 뷰엔 컬럼 헤더가 없어 별도 정렬 컨트롤 제공(데스크톱은 헤더 클릭).
              방향(↓/↑)은 토글 버튼으로 가시화 — select만으론 데스크톱에서 바뀐 방향이 숨겨짐(codex). */}
          <div className="md:hidden flex items-center gap-1">
            <select
              className="border border-gray-300 rounded px-2 py-1.5 text-xs bg-white"
              value={sortKey}
              onChange={(e) => setSortKey(e.target.value as SortKey)}
              aria-label="정렬 기준"
            >
              <option value="revenue">매출순</option>
              <option value="profit">이익순</option>
              <option value="profit_rate">이익률순</option>
              <option value="ad_spend">광고비순</option>
              <option value="conv_revenue">전환매출순</option>
              <option value="roas">RoAS순</option>
            </select>
            <button
              onClick={() => setSortDir((d) => (d === "desc" ? "asc" : "desc"))}
              className="px-2 py-1.5 rounded border border-gray-300 text-xs text-gray-600 bg-white hover:bg-gray-100"
              aria-label={sortDir === "desc" ? "내림차순(클릭하여 오름차순)" : "오름차순(클릭하여 내림차순)"}
              title={sortDir === "desc" ? "내림차순" : "오름차순"}
            >
              {sortDir === "desc" ? "↓" : "↑"}
            </button>
          </div>
          <input
            className="w-full sm:w-48 sm:ml-auto border border-gray-300 rounded px-3 py-1.5 text-sm"
            placeholder="상품명 검색…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        {/* 데스크톱·태블릿: 테이블 (모바일은 아래 카드 리스트로 대체) */}
        <div className="hidden md:block overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-500 text-xs border-b border-gray-100">
            <tr>
              {/* 상품명 — 정렬만, 드롭다운 필터 없음 (값이 너무 다양) */}
              <th
                className="px-4 py-2 text-left cursor-pointer hover:text-gray-800 select-none"
                onClick={() => toggleSort("product_name")}
              >
                상품명
                {sortKey === "product_name" ? (
                  <span className="ml-0.5 text-blue-500">{sortDir === "desc" ? "↓" : "↑"}</span>
                ) : (
                  <span className="ml-0.5 text-gray-300">↕</span>
                )}
              </th>
              <th className="px-3 py-2 text-center">채널</th>
              <ColHeader col="revenue" label="총 매출" />
              <ColHeader col="ad_spend" label="광고비" />
              <ColHeader col="conv_revenue" label="광고 전환매출" />
              <ColHeader col="roas" label="RoAS" />
              <ColHeader col="profit" label="이익" />
              <ColHeader col="profit_rate" label="이익률" />
            </tr>
          </thead>
          <tbody>
            {loading && !data ? (
              <tr><td colSpan={8} className="px-4 py-8 text-center text-gray-400">로딩 중…</td></tr>
            ) : filtered.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-gray-400">
                  {data?.by_product.length === 0 ? "데이터 없음 — 동기화 후 조회하세요" : "검색/필터 결과 없음"}
                </td>
              </tr>
            ) : (
              filtered.map((row, i) => (
                <tr key={i} className="border-t border-gray-50 hover:bg-gray-50">
                  <td className="px-4 py-2 max-w-[380px]">
                    <div
                      className="text-gray-900 text-sm truncate"
                      title={row.option_name ? `${row.product_name}, ${row.option_name}` : row.product_name}
                    >
                      {row.product_name}
                      {row.option_name && <span className="text-gray-400">, {row.option_name}</span>}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-center">
                    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                      row.channel_type === "Wing" ? "bg-blue-50 text-blue-700"
                      : row.channel_type === "로켓그로스" ? "bg-orange-50 text-orange-700"
                      : "bg-purple-50 text-purple-700"
                    }`}>
                      {row.channel_type}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right font-medium">{won(row.revenue)}</td>
                  <td className="px-3 py-2 text-right text-gray-600">{won(row.ad_spend)}</td>
                  <td className="px-3 py-2 text-right text-gray-600">{won(row.conv_revenue)}</td>
                  <td className="px-3 py-2 text-right">
                    {row.roas ? (
                      <span className={
                        Number(row.roas) >= 3 ? "text-green-600 font-medium"
                        : Number(row.roas) >= 1 ? "text-gray-700"
                        : "text-red-500"
                      }>
                        {roasFmt(row.roas)}
                      </span>
                    ) : <span className="text-gray-300">—</span>}
                  </td>
                  <td className="px-3 py-2 text-right font-medium">
                    <span className={profitColor(row.profit)}>{won(row.profit)}</span>
                  </td>
                  <td className="px-3 py-2 text-right">
                    {row.profit_rate ? (
                      <span className={
                        Number(row.profit_rate) >= 20 ? "text-blue-600 font-medium"
                        : Number(row.profit_rate) >= 0 ? "text-gray-700"
                        : "text-red-500"
                      }>
                        {pct(row.profit_rate)}
                      </span>
                    ) : <span className="text-gray-300">—</span>}
                  </td>
                </tr>
              ))
            )}
          </tbody>
          {filtered.length > 0 && (
            <tfoot className="bg-gray-50 border-t border-gray-200 text-sm font-semibold">
              <tr>
                <td className="px-4 py-2 text-gray-600" colSpan={2}>합계 ({filtered.length}개)</td>
                <td className="px-3 py-2 text-right">{won(String(totals.revenue))}</td>
                <td className="px-3 py-2 text-right">{won(String(totals.ad_spend))}</td>
                <td className="px-3 py-2 text-right">{won(String(totals.conv_revenue))}</td>
                <td className="px-3 py-2 text-right">{totalRoas}</td>
                <td className="px-3 py-2 text-right">
                  <span className={totals.profit >= 0 ? "text-blue-700" : "text-red-500"}>{won(String(totals.profit))}</span>
                </td>
                <td className="px-3 py-2 text-right">{totalRate}</td>
              </tr>
            </tfoot>
          )}
        </table>
        </div>

        {/* 모바일: 상품 카드 리스트 (테이블이 좁은 화면에서 잘리는 문제 대체) */}
        <div className="md:hidden">
          {loading && !data ? (
            <div className="px-4 py-8 text-center text-gray-400 text-sm">로딩 중…</div>
          ) : filtered.length === 0 ? (
            <div className="px-4 py-8 text-center text-gray-400 text-sm">
              {data?.by_product.length === 0 ? "데이터 없음 — 동기화 후 조회하세요" : "검색/필터 결과 없음"}
            </div>
          ) : (
            <ul className="divide-y divide-gray-100">
              {filtered.map((row) => (
                <li key={`${row.product_name}|${row.option_name}|${row.channel_type}`} className="px-4 py-3">
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <div className="text-sm text-gray-900 leading-snug break-keep">
                      {row.product_name}
                      {row.option_name && <span className="text-gray-400">, {row.option_name}</span>}
                    </div>
                    <span className={`shrink-0 inline-block px-2 py-0.5 rounded text-xs font-medium ${
                      row.channel_type === "Wing" ? "bg-blue-50 text-blue-700"
                      : row.channel_type === "로켓그로스" ? "bg-orange-50 text-orange-700"
                      : "bg-purple-50 text-purple-700"
                    }`}>{row.channel_type}</span>
                  </div>
                  <div className="grid grid-cols-3 gap-x-3 gap-y-2">
                    <StatCell label="총 매출" value={won(row.revenue)} valueClass="text-gray-900 font-medium" />
                    <StatCell label="광고비" value={won(row.ad_spend)} />
                    <StatCell label="광고 전환매출" value={won(row.conv_revenue)} />
                    <StatCell label="RoAS" value={row.roas ? roasFmt(row.roas) : "—"} valueClass={roasClass(row.roas)} />
                    <StatCell label="이익" value={won(row.profit)} valueClass={`font-medium ${profitColor(row.profit)}`} />
                    <StatCell label="이익률" value={row.profit_rate ? pct(row.profit_rate) : "—"} valueClass={rateClass(row.profit_rate)} />
                  </div>
                </li>
              ))}
            </ul>
          )}
          {filtered.length > 0 && (
            <div className="px-4 py-3 bg-gray-50 border-t border-gray-200">
              <div className="text-xs font-semibold text-gray-600 mb-2">합계 ({filtered.length}개)</div>
              <div className="grid grid-cols-3 gap-x-3 gap-y-2">
                <StatCell label="총 매출" value={won(String(totals.revenue))} valueClass="text-gray-900 font-semibold" />
                <StatCell label="광고비" value={won(String(totals.ad_spend))} valueClass="font-semibold" />
                <StatCell label="광고 전환매출" value={won(String(totals.conv_revenue))} valueClass="font-semibold" />
                <StatCell label="RoAS" value={totalRoas} valueClass="font-semibold text-gray-800" />
                <StatCell label="이익" value={won(String(totals.profit))} valueClass={`font-semibold ${totals.profit >= 0 ? "text-blue-700" : "text-red-500"}`} />
                <StatCell label="이익률" value={totalRate} valueClass="font-semibold text-gray-800" />
              </div>
            </div>
          )}
        </div>

        <div className="px-4 py-2 text-xs text-gray-400 border-t border-gray-100">
          {filtered.length}개 표시 / 전체 {data?.by_product.length ?? 0}개
        </div>
      </div>
    </div>
  );
}
