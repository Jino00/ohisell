// NaverOps.tsx — 🛒 네이버 스마트스토어 운영 패널
// 기간별 매출 현황 + 상품별 상세 (쿠팡 패널 단순화 버전)
import { useState, useEffect, useCallback, useRef } from "react";
import { Spinner, BusyOverlay, MIN_BUSY_MS } from "../components/Busy";
import { PeriodRangeBar, type PeriodPreset } from "../components/PeriodRangeBar";
import { customRangeError, kstDate, OPS_MAX_SPAN_DAYS } from "../lib/periodRange";
import {
  fetchNaverSalesSummary, fetchGfaStatus, uploadGfaCsv,
  fetchNaverSettlement, syncNaverSettlement,
  fetchNaverInquiries, fetchNaverProducts, fetchNaverSellerInfo,
  fetchNaverPendingOrders, naverConfirmOrders, naverDispatchOrders, naverDelayOrder,
  NAVER_DELIVERY_COMPANIES, NAVER_DELIVERY_METHODS, NAVER_DELAY_REASONS,
  fetchNaverClaims, naverApproveCancel, naverRequestCancel,
  naverApproveReturn, naverRejectReturn, naverHoldbackReturn,
  naverReleaseReturnHoldback, naverRequestReturn,
  naverApproveExchangeCollect, naverDispatchExchange, naverHoldbackExchange,
  naverReleaseExchangeHoldback, naverRejectExchange,
  naverChangeProductStatus, NAVER_PRODUCT_STATUS_OPTIONS, type NaverProductStatus,
  NAVER_CANCEL_REASONS, NAVER_CLAIM_STATUS_LABELS,
  NAVER_RETURN_REASONS, NAVER_RETURN_HOLDBACK_TYPES, NAVER_COLLECT_DELIVERY_METHODS,
  type NaverSalesSummary, type NaverSalesProductRow, type GfaStatus,
  type NaverSalesSummaryData, type NaverUnallocated, type NaverAdAlloc,
  type NaverReconciliation,
  type NaverSettlement, type NaverInquiries,
  type NaverProductList, type NaverSellerInfo,
  type NaverPendingOrders, type NaverPendingOrder, type NaverWriteResult,
  type NaverDispatchItem, type NaverClaims,
  syncRealtime,
} from "../lib/api";

// N8 판매상태 유효 전이 (API센터 실측 — 현재상태별 변경 가능 목표). 무효 전이는 네이버 400.
const NAVER_STATUS_TRANSITIONS: Record<string, NaverProductStatus[]> = {
  SALE: ["OUTOFSTOCK", "SUSPENSION"],
  OUTOFSTOCK: ["SALE", "SUSPENSION"],
  SUSPENSION: ["SALE"],
};
const NAVER_STATUS_LABEL: Record<string, string> = { SALE: "판매중", OUTOFSTOCK: "품절", SUSPENSION: "판매중지" };

// 마지막 업로드일로부터 경과 일수 (로컬 기준). null이면 데이터 없음.
function daysSince(dateStr: string | null | undefined): number | null {
  if (!dateStr) return null;
  const d = new Date(dateStr + "T00:00:00");
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((today.getTime() - d.getTime()) / 86400000);
}

/** 서버가 KST naive ISO로 준다(전역 원칙 0) — Date로 파싱하면 로컬 타임존이 한 번 더 붙는다.
 *  문자열에서 잘라 쓴다. */
function hhmm(iso: string | null | undefined): string {
  return iso ? iso.slice(11, 16) : "—";
}

// ★프리셋에 「1년」이 없는 이유: 백엔드(`utils/date_range.py`)의 상한이 90일이라
//   1년을 넣으면 누르는 즉시 400이 된다. 못 쓰는 버튼은 두지 않는다.
const NAVER_PERIOD_PRESETS: PeriodPreset[] = ["today", "yesterday", "7d", "15d", "30d", "90d"];

type SortKey = "product_name" | "revenue_total" | "ad_spend" | "profit" | "profit_rate";
type SortDir = "asc" | "desc";
type ColKey = "revenue_total" | "ad_spend" | "profit" | "profit_rate";

function won(s: string | null | undefined) {
  if (s == null) return "—";
  const n = Math.round(Number(s));
  return `${n.toLocaleString("ko-KR")}원`;
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
function fmtVal(row: NaverSalesProductRow, col: ColKey): string {
  if (col === "revenue_total") return won(row.revenue_total ?? row.revenue);
  if (col === "ad_spend") return won(row.ad_spend ?? "0");
  if (col === "profit") return won(row.profit);
  return row.profit_rate ? pct(row.profit_rate) : "—";
}

/** 정렬용 원값. 신설 열이 비어 있으면(구 응답) 옛 열로 떨어뜨린다 — 정렬이 통째로
 *  «모름»이 되어 전 행이 끝으로 밀리는 것을 막는다. */
function sortVal(row: NaverSalesProductRow, key: Exclude<SortKey, "product_name">): string | null {
  if (key === "revenue_total") return row.revenue_total ?? row.revenue;
  if (key === "ad_spend") return row.ad_spend ?? "0";
  return row[key];
}

/** D-NAO-207 — 「상품 행이 무슨 이익인가」를 표 **위**에서 말한다.
 *
 * ★왜 표 위인가: 종전엔 «광고비·물류비 미반영»이 표 아래 11px 회색 각주 한 줄이었고, 열 이름은
 *   그냥 「이익」이었다. 2026-08-19 Jino가 화면을 보고 «이익이 너무 높다»고 지적했다 — 상품 행
 *   71.0% vs 계정 실제 21.1%. 훑는 눈에는 큰 숫자가 이긴다(이 저장소가 반복해서 배운 것).
 * ★배분 비율을 **숫자로** 낸다: 「일부 배분됨」 같은 말은 얼마나인지를 안 말해서 안 읽힌다.
 */
export function AdAllocationNotice({ summary, adAlloc, recon }: {
  summary?: NaverSalesSummaryData;
  adAlloc?: NaverAdAlloc;
  recon?: NaverReconciliation;
}) {
  const uncovered = adAlloc?.uncovered_dates ?? [];
  const allocN = Number(summary?.ad_allocated ?? 0) || 0;
  const unallocN = Number(summary?.ad_unallocated ?? 0) || 0;
  const totalN = allocN + unallocN;
  const pctAlloc = totalN > 0 ? (allocN / totalN) * 100 : 0;
  return (
    <>
      {summary?.ad_allocated != null && (
        <div className="mb-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-900">
          <b>상품 행은 광고비·물류비까지 반영한 순이익입니다.</b>{" "}
          광고비 {won(summary.ad_spend)} 중 <b>{won(summary.ad_allocated)}({pctAlloc.toFixed(0)}%)</b>가
          쇼핑 캠페인 소재→상품 조인으로 실제 귀속됐고, 나머지 {won(summary.ad_unallocated)}는
          상품 축이 없어 <b>맨 아래 「광고비 미배분」 행</b>에 있습니다.
          {uncovered.length > 0 && (
            <div className="mt-1 text-amber-800">
              ⚠️ 이 구간 중 <b>{uncovered.length}일</b>은 광고 소재 원장이 없어 상품별 광고비를
              배분할 수 없습니다({uncovered.slice(0, 3).join(", ")}
              {uncovered.length > 3 ? ` 외 ${uncovered.length - 3}일` : ""}).
              그 날의 광고비는 0원이 아니라 <b>전액 미배분</b>으로 들어가 있습니다
              {adAlloc?.ledger_from && ` — 원장 보유 창은 ${adAlloc.ledger_from}~${adAlloc.ledger_to}입니다`}.
            </div>
          )}
          {adAlloc && Number(adAlloc.no_sale_cost ?? 0) > 0 && (
            <div className="mt-1 text-red-800">
              ⚠️ 상품 <b>{adAlloc.no_sale_products}개</b>는 광고비 <b>{won(adAlloc.no_sale_cost)}</b>가
              나갔는데 이 기간 <b>판매가 0건</b>입니다 — 매출이 없어 상품 행에 나타나지 않으므로
              미배분에 들어가 있습니다(순수 손실).
            </div>
          )}
          {adAlloc && Number(adAlloc.ambiguous_cost) > 0 && (
            <div className="mt-1 text-amber-800">
              ⚠️ 소재 {adAlloc.ambiguous_ads}개가 두 상품에 매핑돼 있어 {won(adAlloc.ambiguous_cost)}는
              <b> 어느 상품에도 붙이지 않았습니다</b>(아무거나 고르면 그 상품 이익이 조용히 틀린다).
            </div>
          )}
        </div>
      )}
      {/* 검산이 깨지면 화면이 스스로 말한다 — 표 합계와 카드가 갈렸다는 뜻이다.
          ★«조용히 어긋난 표»가 이 패널이 이미 두 번 낸 결함 모양이라 경고를 값 옆에 둔다. */}
      {recon && !recon.closes && (
        <div className="mb-2 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800">
          ⚠️ <b>검산 불일치</b> — 상품 이익 합 + 미배분({won(recon.unallocated_profit)})이
          요약 이익({won(recon.summary_profit)})과 {won(recon.residual)} 어긋납니다. 표를 근거로 쓰지 마세요.
        </div>
      )}
    </>
  );
}

/** D-NAO-207 — 상품에 못 붙인 광고비 한 행. 쿠팡 패널 「판매유형 미배분」과 같은 모양.
 *
 * ★이 행이 있어야 열 합계가 상단 카드와 일치한다 — 표가 스스로 검산된다.
 *   빼면 상품 이익률이 다시 «전부 반영된 순이익»으로 읽힌다(이 작업이 고치려던 바로 그 오독).
 * ★0원이어도 그린다 — «없다»와 «0»이 같아 보이면 안 된다. */
export function UnallocatedRow({ unallocated, uncoveredDays = 0, noSaleCount = 0 }: {
  unallocated?: NaverUnallocated;
  uncoveredDays?: number;
  noSaleCount?: number;
}) {
  if (!unallocated) return null;
  return (
    <tr className="bg-amber-50/60 border-t-2 border-amber-200">
      <td className="px-3 py-2">
        <div className="text-amber-900 font-medium">광고비 미배분</div>
        <div className="mt-0.5 text-[11px] text-amber-700">
          파워링크(소재=키워드)·디스플레이는 상품 축이 없어 붙일 수 없다
          {uncoveredDays > 0 && ` · 소재 원장 없는 날 ${uncoveredDays}일 포함`}
          {noSaleCount > 0 && ` · 판매 0건 상품 ${noSaleCount}개의 광고비 포함`}
        </div>
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-gray-400">—</td>
      <td className="px-3 py-2 text-right tabular-nums text-amber-900">{won(unallocated.ad_spend)}</td>
      <td className={`px-3 py-2 text-right tabular-nums font-medium ${profitColor(unallocated.profit)}`}>
        {won(unallocated.profit)}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-gray-400">—</td>
    </tr>
  );
}

function SummaryCard({ label, value, sub, highlight }: {
  label: string; value: string; sub?: string; highlight?: "blue" | "red";
}) {
  const border = highlight === "blue"
    ? "border-blue-400 bg-blue-50"
    : highlight === "red"
    ? "border-red-400 bg-red-50"
    : "border-gray-200 bg-white";
  return (
    <div className={`border rounded-lg p-4 ${border}`}>
      <div className="text-xs text-gray-500 mb-1">{label}</div>
      <div className={`text-xl font-bold ${highlight === "blue" ? "text-blue-700" : highlight === "red" ? "text-red-600" : "text-gray-900"}`}>{value}</div>
      {sub && <div className="text-xs text-gray-400 mt-0.5">{sub}</div>}
    </div>
  );
}

// 컬럼 값 필터 드롭다운
function ColFilter({
  col, rows, active, onClose, onSelect,
}: {
  col: ColKey;
  rows: NaverSalesProductRow[];
  active: Set<string>;
  onClose: () => void;
  onSelect: (vals: Set<string>) => void;
}) {
  const uniq = Array.from(new Set(rows.map((r) => fmtVal(r, col)))).sort((a, b) => {
    const na = Number(a.replace(/[^0-9.-]/g, ""));
    const nb = Number(b.replace(/[^0-9.-]/g, ""));
    return isNaN(na) || isNaN(nb) ? a.localeCompare(b) : nb - na;
  });
  const [sel, setSel] = useState<Set<string>>(active.size ? active : new Set(uniq));
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  return (
    <div ref={ref} className="absolute z-50 bg-white border border-gray-200 rounded shadow-lg p-2 min-w-[140px] max-h-64 overflow-y-auto">
      <div className="flex gap-1 mb-1">
        <button className="text-xs text-blue-600 underline" onClick={() => setSel(new Set(uniq))}>전체</button>
        <span className="text-gray-300">|</span>
        <button className="text-xs text-blue-600 underline" onClick={() => setSel(new Set())}>없음</button>
      </div>
      {uniq.map((v) => (
        <label key={v} className="flex items-center gap-1 text-xs py-0.5 cursor-pointer hover:bg-gray-50">
          <input type="checkbox" checked={sel.has(v)} onChange={(e) => {
            const next = new Set(sel);
            if (e.target.checked) next.add(v); else next.delete(v);
            setSel(next);
          }} />
          <span>{v}</span>
        </label>
      ))}
      <button
        className="mt-1 w-full text-xs bg-blue-600 text-white rounded py-1"
        onClick={() => { onSelect(sel); onClose(); }}
      >적용</button>
    </div>
  );
}

export default function NaverOps() {
  // 기간은 **항상 날짜 두 개**다 — 프리셋 버튼은 그 두 칸을 채우는 단축키일 뿐이다
  // (공용 `PeriodRangeBar`가 그 계약을 들고 있다). 종전 기본과 같은 최근 7일로 시작한다.
  // ★날짜는 `kstDate`만 쓴다: 프론트에서 타임존이 걸린 유일한 코드이고, 이 저장소가
  //   타임존으로 두 번 사고를 낸 공통점이 "그 코드에 테스트가 없었다"는 것이다.
  const [from, setFrom] = useState(() => kstDate(-6));
  const [to, setTo] = useState(() => kstDate(0));
  // 백엔드가 막는 입력은 프론트가 먼저 막는다 — 빈 칸·뒤집힘·미래를 여기서 잡아
  // 400 원문("Input should be a valid date…")이 화면에 새지 않게 한다.
  // ★상한은 백엔드와 짝(90일) — 적대 리뷰 1R P1-2 참조.
  const rangeError = customRangeError({ from, to }, undefined, OPS_MAX_SPAN_DAYS);
  const [data, setData] = useState<NaverSalesSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("revenue_total");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [colFilters, setColFilters] = useState<Partial<Record<ColKey, Set<string>>>>({});
  const [openFilter, setOpenFilter] = useState<ColKey | null>(null);
  const [gfa, setGfa] = useState<GfaStatus | null>(null);
  const [gfaUploading, setGfaUploading] = useState(false);
  const [gfaMsg, setGfaMsg] = useState<string | null>(null);
  const gfaFileRef = useRef<HTMLInputElement>(null);

  // 기간을 연달아 바꾸면 응답 도착 순서가 요청 순서와 다를 수 있다. 시퀀스를 붙여
  // **마지막 요청의 응답만** 반영한다 — 안 그러면 늦게 온 옛 기간이 새 화면을 덮어쓰고,
  // 그 상태가 "버튼과 데이터가 안 맞는다"로 보인다(이 화면이 방금 겪은 증상과 같은 모양).
  const reqSeq = useRef(0);
  const load = useCallback(async () => {
    if (rangeError) return;   // 조용히 보정하지 않는다 — 화면이 잘못된 입력을 말한다
    const seq = ++reqSeq.current;
    const t0 = performance.now();
    setLoading(true); setError(null);
    try {
      // 기간은 날짜로만 보낸다 — `days`는 이제 이 화면에 없다(프리셋도 날짜를 채울 뿐).
      const r = await fetchNaverSalesSummary(0, from, to);
      // 응답이 ~0.2초라 그냥 두면 진행 표시가 깜빡이고 만다(사실상 안 보인다).
      // 최소 노출 시간을 채운 뒤에 값을 갈아끼운다 — 그동안 옛 값은 흐린 채로 남는다.
      const rest = MIN_BUSY_MS - (performance.now() - t0);
      if (rest > 0) await new Promise((res) => setTimeout(res, rest));
      if (seq !== reqSeq.current) return;   // 더 최신 요청이 진행 중 — 이 응답은 버린다
      setData(r);
    } catch (e) {
      if (seq !== reqSeq.current) return;
      setError(String(e));
    } finally {
      if (seq === reqSeq.current) setLoading(false);
    }
  }, [from, to, rangeError]);

  // ★지연 실행되는 콜백은 반드시 이 ref로 최신 load를 부른다.
  // 왜: syncRealtime(마운트, 수 초)·handleSync(3초 setTimeout)가 **그 시점의 기간을 붙잡은**
  // load를 나중에 호출하면, 그 사이 사용자가 기간을 바꿔도 옛 기간을 새로 요청해 화면을 덮는다.
  // 라이브 실측(2026-08-06): 30일 클릭 직후 `days=30` 다음에 `days=7`이 뒤따라 와 7일치가 남았다.
  // (의존성이 `days`에서 `from`/`to`로 바뀌었을 뿐, 기계는 그대로다.)
  // 시퀀스 가드(reqSeq)는 응답 도착 순서만 정리할 뿐, 뒤늦게 발사되는 새 요청은 못 막는다.
  const loadRef = useRef(load);
  useEffect(() => { loadRef.current = load; }, [load]);

  const loadGfa = useCallback(async () => {
    try { setGfa(await fetchGfaStatus()); } catch { /* silent */ }
  }, []);

  const [settlement, setSettlement] = useState<NaverSettlement | null>(null);
  const [settleSyncing, setSettleSyncing] = useState(false);
  const loadSettlement = useCallback(async () => {
    try { setSettlement(await fetchNaverSettlement(30)); } catch { /* silent */ }
  }, []);
  async function handleSettleSync() {
    setSettleSyncing(true);
    try {
      await syncNaverSettlement(30);
      await loadSettlement();
    } catch { /* silent */ } finally { setSettleSyncing(false); }
  }

  const [inquiries, setInquiries] = useState<NaverInquiries | null>(null);
  const [inquiryDays, setInquiryDays] = useState(30);
  const [inquiryLoading, setInquiryLoading] = useState(false);
  const loadInquiries = useCallback(async (d: number) => {
    setInquiryLoading(true);
    try { setInquiries(await fetchNaverInquiries(d)); } catch { /* silent */ } finally { setInquiryLoading(false); }
  }, []);

  const [products, setProducts] = useState<NaverProductList | null>(null);
  const [productLoading, setProductLoading] = useState(false);
  const [productStatus, setProductStatus] = useState<string>("SALE");
  const loadProducts = useCallback(async (status: string) => {
    setProductLoading(true);
    try { setProducts(await fetchNaverProducts(status || undefined)); } catch { /* silent */ } finally { setProductLoading(false); }
  }, []);

  const [sellerInfo, setSellerInfo] = useState<NaverSellerInfo | null>(null);
  const loadSeller = useCallback(async () => {
    try { setSellerInfo(await fetchNaverSellerInfo()); } catch { /* silent */ }
  }, []);

  // ── N6. 발주/발송 처리 (쓰기 — dry_run+confirm) ──────────────
  const [pending, setPending] = useState<NaverPendingOrders | null>(null);
  const [pendingLoading, setPendingLoading] = useState(false);
  const [pendingDays, setPendingDays] = useState(14);
  const [selPlace, setSelPlace] = useState<Set<string>>(new Set());   // 발주확인 선택
  const [selDispatch, setSelDispatch] = useState<Set<string>>(new Set()); // 발송 선택
  // 발송 폼: poid → {company, tracking, method}
  const [dispatchForm, setDispatchForm] = useState<Record<string, { company: string; tracking: string; method: string }>>({});
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [executing, setExecuting] = useState(false);
  // dry_run 미리보기 모달
  const [preview, setPreview] = useState<{ title: string; result: NaverWriteResult; execute: () => Promise<void> } | null>(null);
  // 발송지연 모달 (단건)
  const [delayTarget, setDelayTarget] = useState<NaverPendingOrder | null>(null);
  const [delayReason, setDelayReason] = useState("PRODUCT_PREPARE");
  const [delayDue, setDelayDue] = useState("");
  const [delayDetail, setDelayDetail] = useState("");

  const loadPending = useCallback(async (d: number) => {
    setPendingLoading(true);
    try {
      const r = await fetchNaverPendingOrders(d);
      setPending(r);
      // 발송 폼 기본값 채우기 (예상 택배사)
      setDispatchForm((prev) => {
        const next = { ...prev };
        for (const o of r.awaiting_dispatch) {
          if (!next[o.product_order_id]) {
            next[o.product_order_id] = {
              company: o.expected_delivery_company || "HANJIN",
              tracking: "",
              method: o.expected_delivery_method || "DELIVERY",  // 예상 배송방법 시드 (codex P2-4)
            };
          }
        }
        return next;
      });
      setSelPlace(new Set());
      setSelDispatch(new Set());
    } catch (e) {
      setActionMsg(`미발송 주문 조회 실패: ${e instanceof Error ? e.message : e}`);
    } finally {
      setPendingLoading(false);
    }
  }, []);

  function toggleSet(setter: React.Dispatch<React.SetStateAction<Set<string>>>, id: string) {
    setter((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  // dry_run 호출 → 미리보기 모달. execute는 실제(dry_run=false) 재호출.
  async function runPreview(
    title: string,
    dryCall: () => Promise<NaverWriteResult>,
    realCall: () => Promise<NaverWriteResult>,
    okMsg: string,
    reload?: () => Promise<void>,
  ) {
    setActionMsg(null);
    try {
      const result = await dryCall();
      setPreview({
        title,
        result,
        execute: async () => {
          setExecuting(true);
          try {
            await realCall();
            setActionMsg(okMsg);
            setPreview(null);
            await (reload ? reload() : reloadPending());
          } catch (e) {
            setActionMsg(`실행 실패: ${e instanceof Error ? e.message : e}`);
          } finally {
            setExecuting(false);
          }
        },
      });
    } catch (e) {
      setActionMsg(`미리보기 실패: ${e instanceof Error ? e.message : e}`);
    }
  }

  function previewConfirm() {
    const ids = [...selPlace];
    if (!ids.length) { setActionMsg("발주확인할 주문을 선택하세요."); return; }
    runPreview(
      `발주확인 ${ids.length}건`,
      () => naverConfirmOrders(ids, true),
      () => naverConfirmOrders(ids, false),
      `✅ 발주확인 완료: ${ids.length}건`,
    );
  }

  function buildDispatchItems(): NaverDispatchItem[] {
    return [...selDispatch].map((poid) => {
      const f = dispatchForm[poid] || { company: "", tracking: "", method: "DELIVERY" };
      return {
        product_order_id: poid,
        delivery_method: f.method || "DELIVERY",
        delivery_company_code: f.company || undefined,
        tracking_number: f.tracking || undefined,
      };
    });
  }

  function previewDispatch() {
    const items = buildDispatchItems();
    if (!items.length) { setActionMsg("발송처리할 주문을 선택하세요."); return; }
    runPreview(
      `발송처리 ${items.length}건`,
      () => naverDispatchOrders(items, true),
      () => naverDispatchOrders(items, false),
      `✅ 발송처리 완료: ${items.length}건`,
    );
  }

  function previewDelay() {
    if (!delayTarget) return;
    if (!delayDue) { setActionMsg("발송기한을 선택하세요."); return; }
    const payload = {
      product_order_id: delayTarget.product_order_id,
      dispatch_due_date: `${delayDue}T23:59:59+09:00`,
      delayed_dispatch_reason: delayReason,
      dispatch_delayed_detailed_reason: delayDetail,
    };
    setDelayTarget(null);
    runPreview(
      `발송지연 1건 (${payload.product_order_id})`,
      () => naverDelayOrder(payload, true),
      () => naverDelayOrder(payload, false),
      `✅ 발송지연 처리 완료`,
    );
  }

  // ── N7. 클레임 (취소/반품/교환) — wave 1 취소 ──────────────
  const [claims, setClaims] = useState<NaverClaims | null>(null);
  const [claimsLoading, setClaimsLoading] = useState(false);
  const [claimsDays, setClaimsDays] = useState(14);

  // ★지연 재조회는 **호출 시점 값이 아니라 현재 선택**을 읽는다(적대 리뷰 지적).
  //   runPreview는 dry_run 왕복(네트워크)을 기다린 뒤 모달을 띄우므로, 그 사이 사용자가
  //   기간·상태를 바꿀 수 있다. 옛 값으로 재조회하면 **셀렉트는 7일인데 목록은 14일**이 된다
  //   — 기간 버튼에서 고친 것과 같은 결함 클래스이고, 여기엔 reqSeq 같은 가드도 없다.
  //   loadClaims/loadPending/loadProducts 자체는 useCallback([])이라 안정적이므로,
  //   흔들리는 것은 **인자뿐**이다. 그래서 인자만 ref로 뺀다.
  const selRef = useRef({ claimsDays, pendingDays, productStatus });
  useEffect(() => {
    selRef.current = { claimsDays, pendingDays, productStatus };
  }, [claimsDays, pendingDays, productStatus]);
  function reloadClaims()   { return loadClaims(selRef.current.claimsDays); }
  function reloadPending()  { return loadPending(selRef.current.pendingDays); }
  function reloadProducts() { return loadProducts(selRef.current.productStatus); }
  // 직접 취소요청 모달
  const [reqCancelOpen, setReqCancelOpen] = useState(false);
  const [rcPoid, setRcPoid] = useState("");
  const [rcReason, setRcReason] = useState("INTENT_CHANGED");
  const [rcDetail, setRcDetail] = useState("");
  const [rcQty, setRcQty] = useState("");

  const loadClaims = useCallback(async (d: number) => {
    setClaimsLoading(true);
    try {
      setClaims(await fetchNaverClaims(d));
    } catch (e) {
      setActionMsg(`클레임 조회 실패: ${e instanceof Error ? e.message : e}`);
    } finally {
      setClaimsLoading(false);
    }
  }, []);

  function previewApproveCancel(poid: string) {
    runPreview(
      `취소 승인 (${poid})`,
      () => naverApproveCancel(poid, true),
      () => naverApproveCancel(poid, false),
      `✅ 취소 승인 완료`,
      reloadClaims,
    );
  }

  function previewRequestCancel() {
    const poid = rcPoid.trim();
    if (!poid) { setActionMsg("상품주문번호를 입력하세요."); return; }
    // 수량은 양의 정수만 — 잘못된 값이 NaN→null→전체취소로 새지 않도록 (codex P2)
    let qty: number | null = null;
    if (rcQty.trim()) {
      const n = Number(rcQty);
      if (!Number.isInteger(n) || n < 1) { setActionMsg("취소 수량은 1 이상의 정수여야 합니다 (비우면 전체 취소)."); return; }
      qty = n;
    }
    const payload = {
      product_order_id: poid,
      cancel_reason: rcReason,
      cancel_detailed_reason: rcDetail,
      cancel_quantity: qty,
    };
    setReqCancelOpen(false);
    runPreview(
      `직접 취소요청 (${poid})`,
      () => naverRequestCancel(payload, true),
      () => naverRequestCancel(payload, false),
      `✅ 취소 요청 완료`,
      reloadClaims,
    );
  }

  // ── N7 wave2 반품 (Return) ──────────────────────────────
  // 반품 거부 모달
  const [rejReturnPoid, setRejReturnPoid] = useState<string | null>(null);
  const [rejReturnReason, setRejReturnReason] = useState("");
  // 반품 보류 모달
  const [hbReturnPoid, setHbReturnPoid] = useState<string | null>(null);
  const [hbType, setHbType] = useState("RETURN_DELIVERYFEE");
  const [hbDetail, setHbDetail] = useState("");
  const [hbFee, setHbFee] = useState("");
  // 반품 보류 해제 모달 (수동 poid 입력)
  const [relReturnOpen, setRelReturnOpen] = useState(false);
  const [relPoid, setRelPoid] = useState("");
  // 직접 반품요청 모달
  const [reqReturnOpen, setReqReturnOpen] = useState(false);
  const [rrPoid, setRrPoid] = useState("");
  const [rrReason, setRrReason] = useState("INTENT_CHANGED");
  const [rrMethod, setRrMethod] = useState("DELIVERY");
  const [rrCompany, setRrCompany] = useState("");
  const [rrTracking, setRrTracking] = useState("");
  const [rrQty, setRrQty] = useState("");

  function previewApproveReturn(poid: string) {
    runPreview(
      `반품 승인 (${poid})`,
      () => naverApproveReturn(poid, true),
      () => naverApproveReturn(poid, false),
      `✅ 반품 승인 완료`,
      reloadClaims,
    );
  }

  function previewRejectReturn() {
    const poid = (rejReturnPoid || "").trim();
    if (!poid) return;
    const reason = rejReturnReason.trim();
    if (!reason) { setActionMsg("반품 거부 사유를 입력하세요."); return; }
    if (reason.length > 250) { setActionMsg("반품 거부 사유는 250자를 넘을 수 없습니다."); return; }
    setRejReturnPoid(null);
    runPreview(
      `반품 거부 (${poid})`,
      () => naverRejectReturn(poid, reason, true),
      () => naverRejectReturn(poid, reason, false),
      `✅ 반품 거부 완료`,
      reloadClaims,
    );
  }

  function previewHoldbackReturn() {
    const poid = (hbReturnPoid || "").trim();
    if (!poid) return;
    const detail = hbDetail.trim();
    if (!detail) { setActionMsg("보류 상세 사유를 입력하세요."); return; }
    if (detail.length > 250) { setActionMsg("보류 상세 사유는 250자를 넘을 수 없습니다."); return; }
    let fee: number | null = null;
    if (hbFee.trim()) {
      const n = Number(hbFee);
      if (!Number.isInteger(n) || n < 0) { setActionMsg("기타 반품 비용은 0 이상의 정수여야 합니다."); return; }
      fee = n;
    }
    const payload = {
      product_order_id: poid,
      holdback_class_type: hbType,
      holdback_return_detail_reason: detail,
      extra_return_fee_amount: fee,
    };
    setHbReturnPoid(null);
    runPreview(
      `반품 보류 (${poid})`,
      () => naverHoldbackReturn(payload, true),
      () => naverHoldbackReturn(payload, false),
      `✅ 반품 보류 완료`,
      reloadClaims,
    );
  }

  function previewReleaseReturnHoldback() {
    const poid = relPoid.trim();
    if (!poid) { setActionMsg("상품주문번호를 입력하세요."); return; }
    setRelReturnOpen(false);
    runPreview(
      `반품 보류 해제 (${poid})`,
      () => naverReleaseReturnHoldback(poid, true),
      () => naverReleaseReturnHoldback(poid, false),
      `✅ 반품 보류 해제 완료`,
      reloadClaims,
    );
  }

  function previewRequestReturn() {
    const poid = rrPoid.trim();
    if (!poid) { setActionMsg("상품주문번호를 입력하세요."); return; }
    let qty: number | null = null;
    if (rrQty.trim()) {
      const n = Number(rrQty);
      if (!Number.isInteger(n) || n < 1) { setActionMsg("반품 수량은 1 이상의 정수여야 합니다 (비우면 전체 반품)."); return; }
      qty = n;
    }
    const payload = {
      product_order_id: poid,
      return_reason: rrReason,
      collect_delivery_method: rrMethod,
      collect_delivery_company: rrCompany || undefined,
      collect_tracking_number: rrTracking || undefined,
      return_quantity: qty,
    };
    setReqReturnOpen(false);
    runPreview(
      `직접 반품요청 (${poid})`,
      () => naverRequestReturn(payload, true),
      () => naverRequestReturn(payload, false),
      `✅ 반품 요청 완료`,
      reloadClaims,
    );
  }

  // ── N7 wave3 교환 (Exchange) ──────────────────────────────
  // 교환 거부 모달
  const [rejExPoid, setRejExPoid] = useState<string | null>(null);
  const [rejExReason, setRejExReason] = useState("");
  // 교환 보류 모달
  const [hbExPoid, setHbExPoid] = useState<string | null>(null);
  const [hbExType, setHbExType] = useState("EXCHANGE_PRODUCT_NOT_DELIVERED");
  const [hbExDetail, setHbExDetail] = useState("");
  const [hbExFee, setHbExFee] = useState("");
  // 교환 재배송 모달
  const [dispExPoid, setDispExPoid] = useState<string | null>(null);
  const [dispExMethod, setDispExMethod] = useState("DELIVERY");
  const [dispExCompany, setDispExCompany] = useState("");
  const [dispExTracking, setDispExTracking] = useState("");
  // 교환 보류 해제 모달 (수동 poid 입력)
  const [relExOpen, setRelExOpen] = useState(false);
  const [relExPoid, setRelExPoid] = useState("");

  function previewApproveExchangeCollect(poid: string) {
    runPreview(
      `교환 수거완료 (${poid})`,
      () => naverApproveExchangeCollect(poid, true),
      () => naverApproveExchangeCollect(poid, false),
      `✅ 교환 수거완료 처리`,
      reloadClaims,
    );
  }

  function previewRejectExchange() {
    const poid = (rejExPoid || "").trim();
    if (!poid) return;
    const reason = rejExReason.trim();
    if (!reason) { setActionMsg("교환 거부 사유를 입력하세요."); return; }
    if (reason.length > 250) { setActionMsg("교환 거부 사유는 250자를 넘을 수 없습니다."); return; }
    setRejExPoid(null);
    runPreview(
      `교환 거부 (${poid})`,
      () => naverRejectExchange(poid, reason, true),
      () => naverRejectExchange(poid, reason, false),
      `✅ 교환 거부 완료`,
      reloadClaims,
    );
  }

  function previewHoldbackExchange() {
    const poid = (hbExPoid || "").trim();
    if (!poid) return;
    const detail = hbExDetail.trim();
    if (!detail) { setActionMsg("보류 상세 사유를 입력하세요."); return; }
    if (detail.length > 250) { setActionMsg("보류 상세 사유는 250자를 넘을 수 없습니다."); return; }
    let fee: number | null = null;
    if (hbExFee.trim()) {
      const n = Number(hbExFee);
      if (!Number.isInteger(n) || n < 0) { setActionMsg("기타 교환 비용은 0 이상의 정수여야 합니다."); return; }
      fee = n;
    }
    const payload = {
      product_order_id: poid,
      holdback_class_type: hbExType,
      holdback_exchange_detail_reason: detail,
      extra_exchange_fee_amount: fee,
    };
    setHbExPoid(null);
    runPreview(
      `교환 보류 (${poid})`,
      () => naverHoldbackExchange(payload, true),
      () => naverHoldbackExchange(payload, false),
      `✅ 교환 보류 완료`,
      reloadClaims,
    );
  }

  function previewDispatchExchange() {
    const poid = (dispExPoid || "").trim();
    if (!poid) return;
    const method = dispExMethod;
    const company = dispExCompany.trim();
    const tracking = dispExTracking.trim();
    // 스펙상 전 필드 선택(codex 합의) — 입력한 값만 그대로 전송, 네이버가 검증.
    const payload = {
      product_order_id: poid,
      re_delivery_method: method || undefined,
      re_delivery_company: company || undefined,
      re_delivery_tracking_number: tracking || undefined,
    };
    setDispExPoid(null);
    runPreview(
      `교환 재배송 (${poid})`,
      () => naverDispatchExchange(payload, true),
      () => naverDispatchExchange(payload, false),
      `✅ 교환 재배송 처리`,
      reloadClaims,
    );
  }

  function previewReleaseExchangeHoldback() {
    const poid = relExPoid.trim();
    if (!poid) { setActionMsg("상품주문번호를 입력하세요."); return; }
    setRelExOpen(false);
    runPreview(
      `교환 보류 해제 (${poid})`,
      () => naverReleaseExchangeHoldback(poid, true),
      () => naverReleaseExchangeHoldback(poid, false),
      `✅ 교환 보류 해제 완료`,
      reloadClaims,
    );
  }

  // ── N8 상품 판매상태 변경 (트랙 D-11) ──────────────────────────
  // 모달: 원상품 단위로 판매중/품절/판매중지 전환. SALE(재입고)만 재고 입력.
  const [statusModal, setStatusModal] = useState<{ originNo: number; name: string; status: string; stock: number | null } | null>(null);
  const [csStatus, setCsStatus] = useState<NaverProductStatus>("OUTOFSTOCK");
  const [csStock, setCsStock] = useState<string>("");

  function openStatusModal(originNo: number, name: string, status: string, stock: number | null) {
    const targets = NAVER_STATUS_TRANSITIONS[status] || [];
    if (!targets.length) { setActionMsg(`현재 상태(${NAVER_STATUS_LABEL[status] || status})에서는 판매상태를 변경할 수 없습니다.`); return; }
    setStatusModal({ originNo, name, status, stock });
    setCsStatus(targets[0]);  // 현재 상태에서 유효한 첫 전이를 기본값으로
    setCsStock(stock != null ? String(stock) : "");
  }

  function previewChangeStatus() {
    if (!statusModal) return;
    const { originNo, name } = statusModal;
    const payload: { origin_product_no: number; status_type: NaverProductStatus; stock_quantity?: number } = {
      origin_product_no: originNo,
      status_type: csStatus,
    };
    if (csStatus === "SALE") {
      const n = Number(csStock);
      if (!csStock.trim() || !Number.isInteger(n) || n < 1) {
        setActionMsg("판매중(재입고) 전환 시 재고 수량을 1 이상 정수로 입력하세요 (0이면 품절로 유지됨)."); return;
      }
      if (n > 99999999) { setActionMsg("재고 수량이 너무 큽니다 (최대 99,999,999)."); return; }
      payload.stock_quantity = n;
    }
    setStatusModal(null);
    runPreview(
      `판매상태 변경 — ${name}`,
      () => naverChangeProductStatus(payload, true),
      () => naverChangeProductStatus(payload, false),
      "✅ 판매상태 변경 완료",
      reloadProducts,
    );
  }

  // 마운트 시 실시간 동기화 후 데이터 로드(fail-soft), 이후 기간(days)이 바뀌면 재조회.
  // ★종전에는 의존성이 `[]`라 **마운트 1회만** 실행됐다 — 기간 버튼 5개가 setDays로
  //   하이라이트만 옮기고 재조회는 일어나지 않아, 「어제」를 눌러도 7일치가 그대로 남았다.
  //   아래 다른 로더들은 전부 [loadX, xDays]를 달고 있는데 이 요약 하나만 빠져 있었다.
  const didInitLoad = useRef(false);
  useEffect(() => {
    if (!didInitLoad.current) {
      didInitLoad.current = true;
      // ★setLoading(true)를 먼저 켠다: syncRealtime은 전 채널 수집이라 수 초 걸리는데, 그동안
      //   loading=false·data=null이라 요약도 스켈레톤도 테이블도 **아무것도 렌더되지 않아
      //   화면이 통째로 비어 있었다**. dev에서는 StrictMode 이중 마운트가 두 번째 이펙트에서
      //   즉시 load를 불러 이 백지를 가려버려, 내 확인을 통과했다(적대 리뷰가 잡음).
      setLoading(true);
      syncRealtime().catch(() => {}).then(() => loadRef.current());
      return;
    }
    load();
  }, [load]);
  useEffect(() => { loadGfa(); }, [loadGfa]);
  useEffect(() => { loadSettlement(); }, [loadSettlement]);
  useEffect(() => { loadInquiries(inquiryDays); }, [loadInquiries, inquiryDays]);
  useEffect(() => { loadProducts(productStatus); }, [loadProducts, productStatus]);
  useEffect(() => { loadSeller(); }, [loadSeller]);
  useEffect(() => { loadPending(pendingDays); }, [loadPending, pendingDays]);
  useEffect(() => { loadClaims(claimsDays); }, [loadClaims, claimsDays]);

  async function handleGfaUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.name.endsWith(".csv")) { setGfaMsg("CSV 파일만 업로드 가능합니다."); return; }
    setGfaUploading(true); setGfaMsg(null);
    try {
      const r = await uploadGfaCsv(file);
      setGfaMsg(`✅ ${r.inserted}일치 등록 (${r.date_from}~${r.date_to}, 총 ${Number(r.total_spend).toLocaleString("ko-KR")}원)`);
      await Promise.all([loadGfa(), load()]);
    } catch (err) {
      setGfaMsg(err instanceof Error ? err.message : "업로드 실패");
    } finally {
      setGfaUploading(false);
      if (gfaFileRef.current) gfaFileRef.current.value = "";
    }
  }

  async function handleSync() {
    setSyncing(true);
    try {
      await fetch("/api/scheduler/trigger/auto_sync_orders", { method: "POST" });
      setTimeout(() => { setSyncing(false); loadRef.current(); }, 3000);
    } catch {
      setSyncing(false);
    }
  }

  function toggleSort(k: SortKey) {
    if (sortKey === k) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(k); setSortDir("desc"); }
  }

  const rows: NaverSalesProductRow[] = data?.by_product ?? [];

  // 컬럼 필터 적용
  const filtered = rows.filter((r) => {
    for (const [col, vals] of Object.entries(colFilters) as [ColKey, Set<string>][]) {
      if (vals && vals.size > 0 && !vals.has(fmtVal(r, col))) return false;
    }
    return true;
  });

  // 정렬
  const sorted = [...filtered].sort((a, b) => {
    let av: number | string, bv: number | string;
    if (sortKey === "product_name") {
      av = a.product_name; bv = b.product_name;
      return sortDir === "asc" ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
    }
    // ★«모름»(null)은 방향과 무관하게 항상 끝으로 — 0으로 접으면 원가 미상 상품이
    //   "이익 0원"인 것처럼 정렬 한가운데 섞여 들어간다(모름을 0으로 읽는 그 결함의 재발).
    const an = sortVal(a, sortKey), bn = sortVal(b, sortKey);
    if (an == null || bn == null) return an == null ? (bn == null ? 0 : 1) : -1;
    av = Number(an); bv = Number(bn);
    return sortDir === "asc" ? av - bv : bv - av;
  });

  const s = data?.summary;
  const profitN = s ? Number(s.profit) : 0;

  // ── D-NAO-207 광고비 배분 ────────────────────────────────────────
  // ★「상품별 미배분」이던 광고비가 이제 **일부만** 배분된다. 얼마가 붙었는지를 카드가
  //   직접 말한다 — 안 그러면 표의 상품 이익이 «전부 반영된 순이익»으로 읽힌다.
  const unalloc = data?.unallocated;
  const adAlloc = data?.ad_alloc;
  const recon = data?.reconciliation;
  const adAllocatedN = Number(s?.ad_allocated ?? 0) || 0;
  const adUnallocN = Number(s?.ad_unallocated ?? 0) || 0;
  const adTotalN = adAllocatedN + adUnallocN;
  const adAllocPct = adTotalN > 0 ? (adAllocatedN / adTotalN) * 100 : 0;
  const adAllocSub = s?.ad_allocated == null
    ? "검색+디스플레이 · 상품별 미배분"
    : `검색+디스플레이 · 상품 배분 ${won(s.ad_allocated)}(${adAllocPct.toFixed(0)}%) · 미배분 ${won(s.ad_unallocated)}`;
  // 소재 원장이 못 덮는 날 — 그 구간은 배분이 **원리적으로** 0이다. 0원이라고 내면 화면이
  // 자신 있게 틀리므로 배너가 먼저 말한다.
  const uncovered = adAlloc?.uncovered_dates ?? [];
  // 「오늘」 광고비가 «모름»인가 — 광고비·이익·이익률 세 카드가 같은 판정을 써야 한다.
  // (한 카드만 모름이라고 하면 나머지 카드가 그걸 부정한다.)
  const adPending = !!data?.ad_basis?.pending;
  // 원천 후퇴: 최신 조회가 관측 최대치보다 낮다. 값은 최대치를 쓰되 그 사실을 화면이 말한다 —
  // 조용히 보정하면 "왜 광고 리포트와 다르냐"는 질문에 답할 근거가 사라진다.
  const adRegressedBy = Number(data?.ad_basis?.regressed_by ?? 0) || 0;
  // 원가 미상 상품 수 — 요약 「이익」·「이익률」 카드가 **스스로** 이 사실을 말해야 한다.
  // ★적대 리뷰 P1: 배너와 원가 카드에만 경고를 두면, 이 PR이 방금 고친 것과 같은 구조가 된다
  //   (값은 확정 숫자에 파란색인데 경고는 카드 밖에만 있음 → 훑는 눈에는 카드가 이긴다).
  const costUnknownN = s?.cost_unknown_products ?? 0;

  function Th({ label, sk, col }: { label: string; sk?: SortKey; col?: ColKey }) {
    const active = col ? (colFilters[col]?.size ?? 0) > 0 : false;
    return (
      <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 whitespace-nowrap">
        <div className="flex items-center gap-1">
          {sk ? (
            <button onClick={() => toggleSort(sk)} className="flex items-center gap-0.5 hover:text-gray-900">
              {label}
              <span className="text-gray-400">
                {sortKey === sk ? (sortDir === "asc" ? " ↑" : " ↓") : " ↕"}
              </span>
            </button>
          ) : label}
          {col && (
            <div className="relative">
              <button
                onClick={() => setOpenFilter(openFilter === col ? null : col)}
                className={`text-xs px-0.5 rounded ${active ? "text-blue-600 font-bold" : "text-gray-300 hover:text-gray-500"}`}
                title="값 필터"
              >⊟</button>
              {openFilter === col && (
                <ColFilter
                  col={col} rows={rows}
                  active={colFilters[col] ?? new Set()}
                  onClose={() => setOpenFilter(null)}
                  onSelect={(vals) => setColFilters((prev) => ({ ...prev, [col]: vals }))}
                />
              )}
            </div>
          )}
        </div>
      </th>
    );
  }

  return (
    <div className="max-w-7xl mx-auto">
      <h1 className="text-2xl font-bold text-gray-900 mb-6">🛒 네이버 스마트스토어 운영 패널</h1>

      {/* 기간 선택 — 공용 `PeriodRangeBar`. 화면마다 날짜 UI를 따로 들면 곧 갈라지므로
          같은 물건을 쓰고, **축 이름만** 이 화면이 정한다(여기는 「판매일」). */}
      <div className="mb-6">
        <PeriodRangeBar
          label="판매일"
          from={from} to={to} onFrom={setFrom} onTo={setTo}
          presets={NAVER_PERIOD_PRESETS}
          right={<>
            <button
              onClick={handleSync}
              disabled={syncing}
              className="px-3 py-1.5 text-sm rounded-md border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >{syncing ? "동기화 중…" : "🔄 동기화"}</button>
            {/* ★같은 기간을 다시 골라도 상태가 안 바뀌면 이펙트가 안 돈다 — 조회 실패 후
                회복 수단이 사라지지 않게 «다시 조회»를 남긴다(종전엔 같은 버튼 재클릭이
                그 역할이었다. 없으면 다른 기간을 경유하거나 F5뿐이다). */}
            <button
              onClick={() => load()}
              disabled={loading || Boolean(rangeError)}
              className="px-3 py-1.5 text-sm rounded-md border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >🔁 다시 조회</button>
            {/* 갱신 중에는 기간 라벨 대신 진행 표시를 낸다 — 옛 기간 라벨을 새 값으로 오독하지 않게. */}
            {loading ? (
              <span className="inline-flex items-center gap-1.5 text-xs font-medium text-green-700">
                <Spinner className="w-3 h-3" /> 데이터 업데이트 중…
              </span>
            ) : data && (
              <span className="text-xs text-gray-400">
                {data.period.from} ~ {data.period.to}
                {/* pending이어도 as_of는 버리지 않는다 — "언제 확인한 0인가"가 정보다 */}
                {data.ad_basis?.as_of
                  ? data.ad_basis.pending
                    ? ` (광고비 «모름» · ${hhmm(data.ad_basis.as_of)} 확인)`
                    : ` (광고비 ${hhmm(data.ad_basis.as_of)} 확인)`
                  : data.ad_basis?.pending ? " (광고비 «모름» · 수집 전)" : ""}
                {data.ad_ref_date && ` (광고비 기준일: ${data.ad_ref_date})`}
              </span>
            )}
          </>}
          note={rangeError
            ? <span className="font-medium text-red-600">
                {rangeError} — 기간을 고칠 때까지 조회하지 않습니다
              </span>
            : <>기간은 <b>판매일(KST)</b> 기준이며 양끝을 포함합니다. 조회 구간은 최대 90일입니다.</>}
        />
      </div>

      {/* ★원천 후퇴 표면화 — NAVER /stats 당일 누적이 뒤로 가는 일이 있다(2026-08-06 20:04 실측:
          19:05~20:00 9회 506,370원 → 20:04 398,102원, 17시 값과 동일). 값은 관측 최대치를 쓰지만
          조용히 보정하면 광고 리포트 화면과 숫자가 갈릴 때 답할 근거가 없다. */}
      {adRegressedBy > 0 && (
        <div className="mb-3 px-3 py-2 rounded border border-amber-300 bg-amber-50 text-xs text-amber-800">
          ⚠️ 네이버 당일 광고비 조회가 <b>{Math.round(adRegressedBy).toLocaleString("ko-KR")}원 후퇴</b>했습니다
          (지금 조회 {won(data?.ad_basis?.latest_cost)} · 오늘 관측 최대 {won(s?.ad_spend)}).
          누적은 뒤로 갈 수 없으므로 <b>관측 최대치</b>를 씁니다 — 원천이 일시적으로 과거 상태를 주는 구간입니다.
        </div>
      )}

      {/* ★원가 미상 표면화 — 요약 이익은 미상분 원가가 **빠진 채** 계산돼 그만큼 과대다.
          왜 요약까지 「—」로 만들지 않나: 미상은 보통 매출의 몇 %인데 그 때문에 나머지를 못 보게
          하면 화면이 쓸모없어진다. 대신 얼마짜리 매출이 원가 없이 계산됐는지를 여기서 말한다.
          ★과대 금액을 추정해 적지 않는다 — 평균 원가율을 곱하면 그럴듯해지고 근거는 사라진다. */}
      {costUnknownN > 0 && (
        <div className="mb-3 px-3 py-2 rounded border border-amber-300 bg-amber-50 text-xs text-amber-800">
          ⚠️ <b>원가 미상 {costUnknownN}개 상품</b>(매출 {won(s!.cost_unknown_revenue)})의
          이익·이익률은 <b>«모름»</b>으로 비워 뒀습니다.
          {/* ★"그만큼 과대"라고 쓰지 않는다(적대 리뷰 P1) — 문장에서 굵은 숫자가 매출 하나뿐이라
              지시어가 매출에 붙어 읽힌다. 과대분은 매출이 아니라 **빠진 원가**이고, 그 액수는
              모른다(그래서 «모름»이다). 추정해 적지 않는 대신 **방향만** 말한다. */}
          {" "}이 상품들의 <b>원가가 요약에서 빠져</b> 있습니다 —
          {adPending
            ? <> 지금은 광고비도 «모름»이라 요약 이익 자체가 「—」입니다.</>
            : <> 실제 이익은 <b>위 요약보다 작습니다</b>(빠진 금액은 원가를 몰라 계산 불가).</>}
          {(s!.cost_unknown_unmapped ?? 0) > 0 && <> · 상품 매핑 필요 <b>{s!.cost_unknown_unmapped}개</b></>}
          {(s!.cost_unknown_zero_cost ?? 0) > 0 && <> · 원가 입력 필요 <b>{s!.cost_unknown_zero_cost}개</b></>}
          {(s!.cost_unknown_ambiguous ?? 0) > 0 && <> · 중복 매핑 정리 필요 <b>{s!.cost_unknown_ambiguous}개</b></>}
          <> · 어느 상품인지는 아래 표에 표시됩니다.</>
        </div>
      )}

      {/* 디스플레이(GFA·ADVoost) 광고비 신선도 배지 + 수동 보정 업로드 */}
      {(() => {
        // ★판정은 **수집기**로 한다(2026-08-06 적대 리뷰). 데이터(MAX(ad_date))로 판정하면
        //   `ad_costs`의 '행 없음'이 「그날 소진 0」과 「수집 실패」를 겸해서 반드시 한쪽으로 틀린다:
        //     소스별로 보면 → 소진 0인 날을 사고로 오탐(거짓 빨강)
        //     계열 합집합으로 보면 → 형제 소스가 죽어도 초록(거짓 초록) ← 직전 판이 이랬다
        //   "우리가 물어봤는가"는 그것과 독립이므로, 그걸로 판정하면 둘 다 안 틀린다.
        const col = gfa?.collection ?? null;
        const stale = col ? col.stale : true;   // 판정 근거가 없으면 초록으로 넘기지 않는다
        const latest = gfa?.date_to ?? null;
        const ago = daysSince(latest);
        const bySource = (gfa?.by_source ?? []).filter((s) => s.date_to);
        return (
          <div className={`flex flex-wrap items-center gap-3 mb-6 px-4 py-3 rounded-lg border ${
            stale ? "border-red-300 bg-red-50" : "border-green-200 bg-green-50"
          }`}>
            <span className="text-sm font-medium text-gray-700">디스플레이 광고비(GFA·ADVoost)</span>
            <span className={`text-sm ${stale ? "text-red-600 font-semibold" : "text-green-700"}`}>
              {stale ? "⚠️ 수집 확인 필요" : "✓ 수집 정상"}
              {col?.last_success_at && ` · 마지막 성공 ${col.last_success_at.slice(0, 16).replace("T", " ")}`}
              {col?.age_hours != null && ` (${Math.floor(col.age_hours)}시간 전)`}
            </span>
            {/* 데이터 최신일은 **사실 진술**이지 판정 근거가 아니다 — 라벨로 분리한다. */}
            <span className="text-sm text-gray-500">
              {latest ? <>적재 최신일 {latest}{ago != null && ago > 0 && ` (${ago}일 전)`}</> : "적재 데이터 없음"}
            </span>
            <button
              onClick={() => gfaFileRef.current?.click()}
              disabled={gfaUploading}
              className="ml-auto px-3 py-1.5 text-sm rounded-md border border-gray-300 bg-white text-gray-600 hover:bg-gray-50 disabled:opacity-50"
            >{gfaUploading ? "업로드 중…" : "📤 CSV 수동 보정"}</button>
            <input
              ref={gfaFileRef} type="file" accept=".csv"
              onChange={handleGfaUpload} className="hidden"
            />
            {gfaMsg && <span className="w-full text-xs text-gray-600">{gfaMsg}</span>}
            <span className="w-full text-xs text-gray-400">
              비즈머니 실차감 API로 <strong className="font-medium">매일 07:10 자동 수집</strong>(어제치).
              {col?.reason && <> {"· "}<strong className="font-medium">{col.reason}</strong></>}
              {col?.last_error && <> {"· 마지막 오류: "}{col.last_error.slice(0, 120)}</>}
              {bySource.length > 0 && (
                <> {"· "}{bySource.map((s) => `${s.source.replace("gfa:", "")} ~${s.date_to}`).join(" · ")}</>
              )}
              {" · CSV 업로드는 자동 수집 이전 구간(~2026-06-04) 보정용."}
            </span>
          </div>
        );
      })()}

      {/* 요약 카드 — 갱신 중에는 값을 흐리고 스피너를 덮는다.
          왜 옛 값을 지우지 않고 흐리기만 하나: 화면이 통째로 비면 어디를 보고 있었는지
          잃어버린다. 대신 흐림+오버레이로 "이 숫자는 아직 이전 기간"임을 분명히 한다. */}
      {(s || loading) && (
        <div className="relative mb-6">
          <div
            className={`grid grid-cols-2 sm:grid-cols-4 gap-3 transition-opacity duration-150 ${
              loading ? "opacity-40 pointer-events-none" : ""
            }`}
            aria-busy={loading}
          >
          {s ? (<>
          <SummaryCard
            label="총매출"
            value={won(s.revenue)}
            sub={`상품+배송 · VAT포함 ${won(s.revenue_vat_incl)}`}
          />
          <SummaryCard
            label="수수료"
            value={won(s.fee)}
            sub={
              s.fee_settled_lines + s.fee_est_lines > 0
                ? `실측 ${s.fee_settled_lines} · 예상 ${s.fee_est_lines}건`
                : "정산 실측+주문시점 예상"
            }
          />
          {/* 원가 카드도 미상 사실을 스스로 말한다 — 배너를 못 본 사람이 이 숫자를 전부라고 읽는다. */}
          <SummaryCard
            label="원가"
            value={won(s.cost)}
            // ★"이보다 큼"이 아니라 "이 값 이상"이다(적대 리뷰 P2) — 미상 상품의 원가가 정말 0일
            //   수도 있다(사은품). 모른다고 해놓고 방향을 단정하면 그것도 거짓이다. 단 원가는
            //   음수일 수 없으므로 "이상"은 확실히 참이다.
            sub={costUnknownN > 0
              ? `원가 미상 ${costUnknownN}개 빠짐 — 실제 원가는 이 값 이상`
              : undefined}
          />
          <SummaryCard
            label="광고비"
            // ★pending이면 **큰 숫자 자체가 «모름»**이어야 한다. 종전엔 값이 "0원"이고 회색
            //   sub만 "0원이 아니다"라고 해서, 한 카드가 위에서 0원이라 하고 아래에서 부정했다.
            //   훑어보는 눈에는 위가 이긴다 — 라벨은 사면이 아니다(교훈 #151).
            value={adPending ? "—" : won(s.ad_spend)}
            sub={
              // 오늘은 **검색광고 당일 누적**이다(디스플레이는 실차감이라 당일치가 없다).
              // 기준시각을 반드시 같이 낸다 — 종전엔 어제 전일치를 넣고 라벨만 달아서,
              // 이익 카드가 「오늘 매출 − 어제 광고비」인 줄 모르고 읽혔다.
              // ★"매시 05분 갱신"은 우리 수집 주기일 뿐이다 — NAVER가 주는 당일 값 자체가
              //   실측상 시간 단위로만 바뀐다(2026-08-06 16:05·16:43·16:46·19:05·19:26·19:39
              //   관측이 원 단위까지 동일). 공식 문서엔 갱신 주기 언급이 없어 "실측"으로 적는다.
              // ★원인을 단정하지 않는다 — 「미집계」인지 「정말 0원」인지 가릴 방법이 없다
              //   (캠페인을 전부 끈 날도 똑같이 전부 0이다). 종전 문구는 "집계 전"·"자정~02시경"
              //   으로 원인과 시간대를 단정해, 낮에 지출이 0인 날엔 맞는 이익을 스스로 할인해
              //   읽게 만들었다.
              data?.ad_basis
                ? adPending
                  ? "네이버 당일 값이 아직 전부 0 — 미집계인지 실제 0원인지 구분 불가"
                  : data.ad_basis.kind === "today_snapshot"
                    ? `검색광고만(당일 누적·관측 최대치) · ${hhmm(data.ad_basis.as_of)} 확인 · 매시 05분 수집 · 디스플레이는 익일 확정`
                    : "오늘 수집 전 — 첫 스냅샷(매시 05분) 후 표시"
                : data?.ad_basis?.kind === "period"
                  // ★기간 탭은 «검색은 오늘까지 + 디스플레이는 어제까지»인 혼합 축이다.
                  //   섞였다는 사실을 숨기면 "왜 광고 리포트와 다르냐"에 답할 근거가 없다.
                  ? data.ad_basis.today_search_source === "today_snapshot"
                    ? `검색+디스플레이 · 오늘 검색광고 ${won(data.ad_basis.today_search_added)} 포함(${hhmm(data.ad_basis.as_of)} 확인) · 오늘 디스플레이는 원천에 당일치가 없어 빠짐`
                    : data.ad_basis.today_search_source === "pending"
                      ? "검색+디스플레이 · 오늘치는 아직 «모름»(수집 전) — 그만큼 이익이 과대"
                      : adAllocSub
                  : adAllocSub
            }
          />
          <SummaryCard
            label="물류비(한진)"
            value={won(s.logistics)}
            sub={`배송 ${s.shipment_count}건 (반품 회수비 포함)`}
          />
          {/* ★반품·교환 배송 손익 — 종전엔 이 패널에 통째로 없어서 «반품이 늘어도 이익이 반응하지
              않았다»(반품이 공짜인 것처럼 보였다). 귀속은 클레임 **완료일**이라 지난 기간 이익이
              반품이 생길 때마다 흔들리지 않는다.
              ★부호를 «손실»로 단정하지 않는다 — 반품비 청구가 출고+회수비를 넘는 건은 배송 축만
              보면 흑자다. 반품의 진짜 손실은 매출을 잃는 쪽이고 그건 매출 카드에 이미 반영돼 있다. */}
          {(s.claim_count ?? 0) > 0 && (
            <SummaryCard
              label="반품·교환 배송손익"
              value={won(s.claim_net)}
              sub={`${s.claim_count}건 · 수입 ${won(s.claim_income)} − 비용 ${won(s.claim_cost)} · 완료일 귀속`}
            />
          )}
          {/* ★광고비를 모르면 이익도 모른다 — 값을 「—」로 두고 색도 칠하지 않는다.
              종전엔 이익 카드에만 경고를 달았는데, 이익률 카드는 경고도 없이 45.5pp 과대(94% vs
              48.5%)를 파란색(양호)으로 띄웠다. 이익률을 먼저 보는 사람은 경고를 한 번도 못 본다.
              이 저장소의 선례와도 같다: 커버리지 미달이면 순이익 「—」, 원가 미상은 0이 아니라 None. */}
          {/* ★원가 미상이 하나라도 있으면 강조색을 빼고 카드 스스로 그 사실을 말한다(적대 리뷰 P1).
              값을 「—」로 비우지는 않는다 — 미상은 보통 매출의 몇 %라 전체를 가리면 손해가 더 크다.
              대신 "양호(파랑)"라는 확신 신호를 거둔다: 이 숫자는 원가 일부가 빠진 값이다. */}
          <SummaryCard
            label="이익"
            value={adPending ? "—" : won(s.profit)}
            sub={
              adPending
                ? "광고비를 모르는 동안은 이익도 «모름»"
                : costUnknownN > 0
                  ? `원가 미상 ${costUnknownN}개 빠짐 — 실제 이익은 이 값 이하`
                  : data?.ad_basis ? "광고비=검색 당일 누적(관측 최대치)" : undefined
            }
            highlight={adPending || costUnknownN > 0 ? undefined : profitN >= 0 ? "blue" : "red"}
          />
          <SummaryCard
            label="이익률"
            value={adPending ? "—" : s.profit_rate ? pct(s.profit_rate) : "—"}
            sub={
              adPending ? "광고비 «모름»"
              : costUnknownN > 0 ? `원가 미상 ${costUnknownN}개 빠짐 — 실제는 이 값 이하`
              : undefined
            }
            highlight={adPending || costUnknownN > 0 ? undefined : profitN >= 0 ? "blue" : "red"}
          />
          <SummaryCard
            label="검색광고 전환매출"
            value={won(s.sa_conv_revenue)}
            sub={s.sa_conv_from ? `구매·직접+간접 · ${s.sa_conv_from}~${s.sa_conv_to}` : "구매 기준(직접+간접)"}
          />
          <SummaryCard
            label="검색광고 RoAS"
            value={s.sa_roas ? `${Number(s.sa_roas).toFixed(2)}x` : "—"}
            sub={s.sa_conv_from ? `전환매출÷광고비 · 전환 ${s.sa_conv_from}~${s.sa_conv_to}` : "디스플레이 제외"}
          />
          </>) : (
            // 첫 로드 — 보여줄 이전 값이 없을 때의 자리표시
            Array.from({ length: 9 }, (_, i) => (
              <div key={i} className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                <div className="h-3 w-16 rounded bg-gray-200" />
                <div className="mt-2 h-6 w-28 rounded bg-gray-200" />
              </div>
            ))
          )}
          </div>
          {loading && (
            <BusyOverlay />
          )}
        </div>
      )}

      {/* 상태 */}
      {error && <p className="text-sm text-red-600">{error}</p>}

      {/* 상품별 테이블 — 갱신 중에도 남겨두고 흐린다(사라졌다 나타나면 스크롤 위치를 잃는다) */}
      {sorted.length > 0 && (
        <>
        <AdAllocationNotice summary={s} adAlloc={adAlloc} recon={recon} />
        <div
          className={`overflow-x-auto rounded-lg border border-gray-200 transition-opacity duration-150 ${
            loading ? "opacity-40 pointer-events-none" : ""
          }`}
          aria-busy={loading}
        >
          <table className="min-w-full bg-white text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <Th label="상품명" sk="product_name" />
                <Th label="총매출" sk="revenue_total" col="revenue_total" />
                <Th label="광고비" sk="ad_spend" col="ad_spend" />
                <Th label="이익" sk="profit" col="profit" />
                <Th label="이익률" sk="profit_rate" col="profit_rate" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {sorted.map((r, i) => (
                <tr key={i} className="hover:bg-gray-50">
                  <td className="px-3 py-2 max-w-xs">
                    <div className="text-gray-900 truncate" title={r.product_name}>{r.product_name}</div>
                    {/* ★원가 미상은 이유까지 말한다 — 「—」만 보면 무엇을 해야 할지 모른다.
                        두 종류의 조치가 다르다: 매핑을 잇는 것 vs 원가를 입력하는 것. */}
                    {r.cost_known === false && (
                      <div className="mt-0.5 text-[11px] text-amber-700">
                        원가 미상 — {
                          r.cost_unknown_kind === "unmapped" ? "상품 매핑 필요"
                          : r.cost_unknown_kind === "ambiguous" ? "중복 매핑 정리 필요(원가가 서로 다름)"
                          : "원가 입력 필요"
                        }
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{won(r.revenue_total ?? r.revenue)}</td>
                  {/* ★광고비 0을 「—」로 쓰지 않는다 — «안 썼다»와 «못 붙였다»가 같아 보이면
                      이 화면이 고치려던 오독이 그대로 재발한다. 0원이면 0원이라 쓰고,
                      붙일 수 없었던 사유는 위 배너와 아래 미배분 행이 말한다. */}
                  <td className="px-3 py-2 text-right tabular-nums text-gray-600">{won(r.ad_spend ?? "0")}</td>
                  <td className={`px-3 py-2 text-right tabular-nums font-medium ${profitColor(r.profit)}`}>{won(r.profit)}</td>
                  <td className={`px-3 py-2 text-right tabular-nums font-medium ${profitColor(r.profit)}`}>{pct(r.profit_rate)}</td>
                </tr>
              ))}
              {/* ★미배분 한 행 — 쿠팡 패널 「판매유형 미배분」과 같은 모양.
                  이 행이 있어야 열 합계가 상단 카드와 정확히 일치한다(표가 스스로 검산된다).
                  없애면 상품 이익률이 다시 «전부 반영된 순이익»으로 읽힌다. */}
              <UnallocatedRow
                unallocated={unalloc}
                uncoveredDays={uncovered.length}
                noSaleCount={adAlloc?.no_sale_products ?? 0}
              />
            </tbody>
          </table>
          <div className="text-xs text-gray-400 px-3 py-2 border-t border-gray-100">
            * 모든 금액은 <b>공급가(부가세 제외)</b> 기준 — 부가세는 통과항목이라 매출·비용 모두 ÷1.1로 통일<br />
            * 요약 순이익 = (상품매출 + 고객배송비 − 수수료 − 원가 − 한진물류비) ÷ 1.1 − 광고비<br />
            * 상품별 이익 = (상품매출 + 고객배송비 − 수수료 − 원가 − 한진물류비) ÷ 1.1 − <b>광고비</b> — 요약과 <b>같은 식</b>이다<br />
            * 광고비는 쇼핑 캠페인만 소재→상품 조인으로 실측 귀속 · 파워링크·디스플레이는 상품 축이 없어 미배분 행으로<br />
            * 물류비는 패키지 단위 → 단일상품 패키지는 전액, 다상품 패키지만 상품매출 비례<br />
            * 수수료 = 정산 완료분은 네이버 건별정산 <b>실측</b>, 미정산 최근 주문은 주문시점 <b>예상</b>(하이브리드)<br />
            * 원가를 모르는 상품은 이익·이익률을 <b>「—」</b>로 비운다 — 0원으로 계산하면 이익률이 90%대로 나온다
          </div>
        </div>
        </>
      )}
      {!loading && data && sorted.length === 0 && (
        <p className="text-sm text-gray-500">해당 기간에 주문 데이터가 없습니다.</p>
      )}

      {/* 💰 정산 내역 (실측, 정산예정일 기준 최근 30일) — 트랙 N1 */}
      <div className="mt-8">
        <div className="flex items-center gap-3 mb-3">
          <h2 className="text-lg font-bold text-gray-900">💰 정산 내역 <span className="text-xs font-normal text-gray-400">(실측·정산예정일 기준)</span></h2>
          <button
            onClick={handleSettleSync}
            disabled={settleSyncing}
            className="px-3 py-1.5 text-sm rounded-md border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >{settleSyncing ? "동기화 중…" : "🔄 정산 동기화"}</button>
        </div>

        {settlement && settlement.rows.length > 0 ? (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
              <SummaryCard label="정산금액(30일)" value={won(settlement.summary.settle_amount)} highlight="blue" />
              <SummaryCard label="실측 수수료" value={won(settlement.summary.commission_amount)} sub="네이버 정산 기준" highlight="red" />
              <SummaryCard label="혜택정산" value={won(settlement.summary.benefit_amount)} />
              <SummaryCard label="지급보류" value={won(settlement.summary.payholdback_amount)} />
            </div>
            <div className="overflow-x-auto rounded-lg border border-gray-200">
              <table className="min-w-full bg-white text-sm">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 whitespace-nowrap">정산예정일</th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 whitespace-nowrap">정산금액</th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 whitespace-nowrap">결제정산</th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 whitespace-nowrap">수수료</th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 whitespace-nowrap">혜택</th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 whitespace-nowrap">지급보류</th>
                    <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 whitespace-nowrap">완료일</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {settlement.rows.map((r, i) => (
                    <tr key={i} className="hover:bg-gray-50">
                      <td className="px-3 py-2 whitespace-nowrap">{r.settle_expect_date}</td>
                      <td className="px-3 py-2 text-right tabular-nums font-medium">{won(r.settle_amount)}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-gray-500">{won(r.pay_settle_amount)}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-red-600">{won(r.commission_amount)}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-gray-500">{won(r.benefit_amount)}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-gray-500">{won(r.payholdback_amount)}</td>
                      <td className="px-3 py-2 whitespace-nowrap text-gray-400">{r.settle_complete_date || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="text-xs text-gray-400 px-3 py-2 border-t border-gray-100">
                * 네이버 커머스 API 실측 정산 (수수료·혜택은 차감되어 음수). 정산예정일 기준이라 주문일과 시점이 다릅니다.
              </div>
            </div>
          </>
        ) : (
          <p className="text-sm text-gray-500">정산 데이터가 없습니다. "🔄 정산 동기화"를 눌러 불러오세요.</p>
        )}
      </div>

      {/* 💬 고객 문의 — 트랙 N3 */}
      <div className="mt-8">
        <div className="flex items-center gap-3 mb-3 flex-wrap">
          <h2 className="text-lg font-bold text-gray-900">💬 고객 문의</h2>
          <div className="flex gap-1">
            {[7, 30, 90].map(d => (
              <button
                key={d}
                onClick={() => setInquiryDays(d)}
                className={`px-2.5 py-1 text-xs rounded border ${inquiryDays === d ? "bg-blue-600 text-white border-blue-600" : "bg-white text-gray-600 border-gray-300 hover:bg-gray-50"}`}
              >{d}일</button>
            ))}
          </div>
          {inquiryLoading && <span className="text-xs text-gray-400">불러오는 중…</span>}
        </div>

        {inquiries && (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-4">
              <SummaryCard label={`전체 문의 (${inquiryDays}일)`} value={String(inquiries.total)} />
              <SummaryCard label="미답변" value={String(inquiries.unanswered)} highlight={inquiries.unanswered > 0 ? "red" : undefined} />
              <SummaryCard label="답변 완료" value={String(inquiries.total - inquiries.unanswered)} highlight={inquiries.unanswered === 0 && inquiries.total > 0 ? "blue" : undefined} />
            </div>

            {inquiries.rows.length > 0 ? (
              <div className="overflow-x-auto rounded-lg border border-gray-200">
                <table className="min-w-full bg-white text-sm">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 whitespace-nowrap">문의일</th>
                      <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 whitespace-nowrap">유형</th>
                      <th className="px-3 py-2 text-left text-xs font-medium text-gray-500">제목</th>
                      <th className="px-3 py-2 text-left text-xs font-medium text-gray-500">상품명</th>
                      <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 whitespace-nowrap">구매자</th>
                      <th className="px-3 py-2 text-center text-xs font-medium text-gray-500 whitespace-nowrap">답변</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {inquiries.rows.map((r) => (
                      <tr key={r.inquiry_no} className={`hover:bg-gray-50 ${!r.answered ? "bg-red-50" : ""}`}>
                        <td className="px-3 py-2 whitespace-nowrap text-gray-500 text-xs">{r.inquiry_date.slice(0, 10)}</td>
                        <td className="px-3 py-2 whitespace-nowrap">
                          <span className="px-1.5 py-0.5 text-xs rounded bg-gray-100 text-gray-600">{r.category}</span>
                        </td>
                        <td className="px-3 py-2 max-w-[200px] truncate" title={r.title}>{r.title}</td>
                        <td className="px-3 py-2 max-w-[180px] truncate text-gray-500 text-xs" title={r.product_name}>{r.product_name || "—"}</td>
                        <td className="px-3 py-2 whitespace-nowrap text-gray-500 text-xs">{r.customer_name}</td>
                        <td className="px-3 py-2 text-center">
                          {r.answered
                            ? <span className="text-xs text-green-600 font-medium">✓ 완료</span>
                            : <span className="text-xs text-red-600 font-bold">미답변</span>
                          }
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sm text-gray-500">해당 기간 문의가 없습니다.</p>
            )}
          </>
        )}
      </div>

      {/* 📦 발주/발송 처리 — 트랙 N6 (쓰기, dry_run+confirm) */}
      <div className="bg-white rounded-lg shadow p-4 mt-8">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <h2 className="text-lg font-bold text-gray-900">📦 발주/발송 처리</h2>
          <div className="flex items-center gap-2">
            <select
              value={pendingDays}
              onChange={(e) => setPendingDays(Number(e.target.value))}
              className="text-sm border border-gray-300 rounded px-2 py-1"
            >
              {[7, 14, 30].map((d) => <option key={d} value={d}>최근 {d}일</option>)}
            </select>
            <button onClick={() => loadPending(pendingDays)} className="px-3 py-1 rounded text-sm bg-gray-100 hover:bg-gray-200">↺ 새로고침</button>
          </div>
        </div>
        <p className="text-xs text-gray-500 mb-3">
          ⚠️ 실제 주문 상태를 변경합니다. 모든 작업은 <b>미리보기(dry-run)</b>로 보낼 내용을 확인한 뒤 실행됩니다.
        </p>
        {actionMsg && (
          <div className="mb-3 text-sm px-3 py-2 rounded bg-blue-50 text-blue-800 border border-blue-200">{actionMsg}</div>
        )}

        {pendingLoading ? (
          <p className="text-sm text-gray-500">로딩 중…</p>
        ) : pending ? (
          <div className="space-y-6">
            {/* 발주확인 대기 */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-sm font-bold text-gray-700">📥 발주확인 대기 <span className="text-gray-400">({pending.awaiting_place.length})</span></h3>
                <button
                  onClick={previewConfirm}
                  disabled={!selPlace.size}
                  className="px-3 py-1.5 rounded text-sm font-medium bg-indigo-600 text-white disabled:opacity-40 hover:bg-indigo-700"
                >선택 발주확인 ({selPlace.size}) ›</button>
              </div>
              {pending.awaiting_place.length ? (
                <div className="overflow-x-auto border rounded">
                  <table className="min-w-full text-sm">
                    <thead className="bg-gray-50 text-gray-600 text-xs">
                      <tr>
                        <th className="px-2 py-2 w-8"></th>
                        <th className="px-3 py-2 text-left">상품주문번호</th>
                        <th className="px-3 py-2 text-left">상품명</th>
                        <th className="px-3 py-2 text-right">수량</th>
                        <th className="px-3 py-2 text-left">주문자</th>
                        <th className="px-3 py-2 text-left">발송기한</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {pending.awaiting_place.map((o) => (
                        <tr key={o.product_order_id} className="hover:bg-gray-50">
                          <td className="px-2 py-2 text-center">
                            <input type="checkbox" checked={selPlace.has(o.product_order_id)} onChange={() => toggleSet(setSelPlace, o.product_order_id)} />
                          </td>
                          <td className="px-3 py-2 text-gray-500 text-xs">{o.product_order_id}</td>
                          <td className="px-3 py-2 max-w-xs truncate">{o.product_name}</td>
                          <td className="px-3 py-2 text-right">{o.quantity}</td>
                          <td className="px-3 py-2">{o.orderer_name}</td>
                          <td className="px-3 py-2 text-xs text-gray-500">{o.shipping_due_date?.slice(0, 10)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <p className="text-sm text-gray-400">발주확인 대기 주문이 없습니다.</p>}
            </div>

            {/* 발송 대기 */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-sm font-bold text-gray-700">🚚 발송 대기 <span className="text-gray-400">({pending.awaiting_dispatch.length})</span></h3>
                <button
                  onClick={previewDispatch}
                  disabled={!selDispatch.size}
                  className="px-3 py-1.5 rounded text-sm font-medium bg-emerald-600 text-white disabled:opacity-40 hover:bg-emerald-700"
                >선택 발송처리 ({selDispatch.size}) ›</button>
              </div>
              {pending.awaiting_dispatch.length ? (
                <div className="overflow-x-auto border rounded">
                  <table className="min-w-full text-sm">
                    <thead className="bg-gray-50 text-gray-600 text-xs">
                      <tr>
                        <th className="px-2 py-2 w-8"></th>
                        <th className="px-3 py-2 text-left">상품주문번호</th>
                        <th className="px-3 py-2 text-left">상품명</th>
                        <th className="px-3 py-2 text-left">배송방법</th>
                        <th className="px-3 py-2 text-left">택배사</th>
                        <th className="px-3 py-2 text-left">송장번호</th>
                        <th className="px-3 py-2 text-left">발송기한</th>
                        <th className="px-3 py-2 text-center">지연</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {pending.awaiting_dispatch.map((o) => {
                        const f = dispatchForm[o.product_order_id] || { company: "HANJIN", tracking: "", method: "DELIVERY" };
                        const setF = (patch: Partial<typeof f>) =>
                          setDispatchForm((prev) => ({ ...prev, [o.product_order_id]: { ...f, ...patch } }));
                        return (
                          <tr key={o.product_order_id} className="hover:bg-gray-50">
                            <td className="px-2 py-2 text-center">
                              <input type="checkbox" checked={selDispatch.has(o.product_order_id)} onChange={() => toggleSet(setSelDispatch, o.product_order_id)} />
                            </td>
                            <td className="px-3 py-2 text-gray-500 text-xs">{o.product_order_id}</td>
                            <td className="px-3 py-2 max-w-[180px] truncate">{o.product_name}</td>
                            <td className="px-3 py-2">
                              <select value={f.method} onChange={(e) => setF({ method: e.target.value })} className="border border-gray-300 rounded px-1 py-0.5 text-xs">
                                {NAVER_DELIVERY_METHODS.map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}
                                {!NAVER_DELIVERY_METHODS.some((m) => m.code === f.method) && f.method && <option value={f.method}>{f.method}</option>}
                              </select>
                            </td>
                            <td className="px-3 py-2">
                              <select value={f.company} onChange={(e) => setF({ company: e.target.value })} disabled={f.method !== "DELIVERY"} className="border border-gray-300 rounded px-1 py-0.5 text-xs disabled:bg-gray-100 disabled:text-gray-400">
                                {NAVER_DELIVERY_COMPANIES.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
                                {!NAVER_DELIVERY_COMPANIES.some((c) => c.code === f.company) && f.company && <option value={f.company}>{f.company}</option>}
                              </select>
                            </td>
                            <td className="px-3 py-2">
                              <input value={f.tracking} onChange={(e) => setF({ tracking: e.target.value })} disabled={f.method !== "DELIVERY"} placeholder={f.method === "DELIVERY" ? "송장번호" : "—"} className="border border-gray-300 rounded px-2 py-0.5 text-xs w-32 disabled:bg-gray-100" />
                            </td>
                            <td className="px-3 py-2 text-xs text-gray-500">{o.shipping_due_date?.slice(0, 10)}</td>
                            <td className="px-3 py-2 text-center">
                              <button onClick={() => { setDelayTarget(o); setDelayDue(o.shipping_due_date?.slice(0, 10) || ""); setDelayReason("PRODUCT_PREPARE"); setDelayDetail(""); }} className="text-xs text-amber-600 hover:underline">지연</button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : <p className="text-sm text-gray-400">발송 대기 주문이 없습니다.</p>}
            </div>
          </div>
        ) : (
          <p className="text-sm text-gray-500">미발송 주문을 불러오지 못했습니다.</p>
        )}
      </div>

      {/* ⚖️ 클레임 (취소/반품/교환) — 트랙 N7 wave1 취소 */}
      <div className="bg-white rounded-lg shadow p-4 mt-8">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <h2 className="text-lg font-bold text-gray-900">⚖️ 클레임 (취소/반품/교환)</h2>
          <div className="flex flex-wrap items-center gap-2">
            <button onClick={() => { setRcPoid(""); setRcReason("INTENT_CHANGED"); setRcDetail(""); setRcQty(""); setReqCancelOpen(true); }} className="px-3 py-1 rounded text-sm bg-rose-600 text-white hover:bg-rose-700">+ 직접 취소요청</button>
            <button onClick={() => { setRrPoid(""); setRrReason("INTENT_CHANGED"); setRrMethod("DELIVERY"); setRrCompany(""); setRrTracking(""); setRrQty(""); setReqReturnOpen(true); }} className="px-3 py-1 rounded text-sm bg-orange-600 text-white hover:bg-orange-700">+ 직접 반품요청</button>
            <button onClick={() => { setRelPoid(""); setRelReturnOpen(true); }} className="px-3 py-1 rounded text-sm bg-gray-100 hover:bg-gray-200">반품 보류해제</button>
            <button onClick={() => { setRelExPoid(""); setRelExOpen(true); }} className="px-3 py-1 rounded text-sm bg-gray-100 hover:bg-gray-200">교환 보류해제</button>
            <select value={claimsDays} onChange={(e) => setClaimsDays(Number(e.target.value))} className="text-sm border border-gray-300 rounded px-2 py-1">
              {[7, 14, 30].map((d) => <option key={d} value={d}>최근 {d}일</option>)}
            </select>
            <button onClick={reloadClaims} className="px-3 py-1 rounded text-sm bg-gray-100 hover:bg-gray-200">↺</button>
          </div>
        </div>
        <p className="text-xs text-gray-500 mb-3">
          ⚠️ 실주문 상태 변경. 모든 작업은 미리보기(dry-run) 후 실행. <b>취소 승인</b> · <b>반품 승인/거부/보류</b> · <b>교환 수거완료/거부/보류·재배송</b> 처리 가능.
        </p>
        {actionMsg && (
          <div className="mb-3 text-sm px-3 py-2 rounded bg-blue-50 text-blue-800 border border-blue-200">{actionMsg}</div>
        )}
        {claimsLoading ? (
          <p className="text-sm text-gray-500">로딩 중…</p>
        ) : claims && claims.claims.length ? (
          <div className="overflow-x-auto border rounded">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 text-gray-600 text-xs">
                <tr>
                  <th className="px-3 py-2 text-left">종류</th>
                  <th className="px-3 py-2 text-left">상태</th>
                  <th className="px-3 py-2 text-left">상품주문번호</th>
                  <th className="px-3 py-2 text-left">상품명</th>
                  <th className="px-3 py-2 text-right">수량</th>
                  <th className="px-3 py-2 text-left">주문자</th>
                  <th className="px-3 py-2 text-center">처리</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {claims.claims.map((c) => {
                  const tColor = c.claim_type === "CANCEL" ? "bg-red-100 text-red-700" : c.claim_type === "RETURN" ? "bg-orange-100 text-orange-700" : c.claim_type === "EXCHANGE" ? "bg-blue-100 text-blue-700" : "bg-gray-100 text-gray-600";
                  const tLabel = c.claim_type === "CANCEL" ? "취소" : c.claim_type === "RETURN" ? "반품" : c.claim_type === "EXCHANGE" ? "교환" : c.claim_type || "-";
                  return (
                    <tr key={c.product_order_id} className="hover:bg-gray-50">
                      <td className="px-3 py-2"><span className={`px-2 py-0.5 rounded text-xs font-medium ${tColor}`}>{tLabel}</span></td>
                      <td className="px-3 py-2 text-xs text-gray-700">{NAVER_CLAIM_STATUS_LABELS[c.claim_status] || c.claim_status}</td>
                      <td className="px-3 py-2 text-gray-500 text-xs">{c.product_order_id}</td>
                      <td className="px-3 py-2 max-w-[200px] truncate">{c.product_name}</td>
                      <td className="px-3 py-2 text-right">{c.quantity}</td>
                      <td className="px-3 py-2">{c.orderer_name}</td>
                      <td className="px-3 py-2 text-center">
                        {c.claim_status === "CANCEL_REQUEST" ? (
                          <button onClick={() => previewApproveCancel(c.product_order_id)} className="text-xs px-2 py-1 rounded bg-indigo-600 text-white hover:bg-indigo-700">취소 승인</button>
                        ) : c.claim_status === "RETURN_REQUEST" ? (
                          <div className="flex items-center justify-center gap-1">
                            <button onClick={() => previewApproveReturn(c.product_order_id)} className="text-xs px-2 py-1 rounded bg-indigo-600 text-white hover:bg-indigo-700">승인</button>
                            <button onClick={() => { setRejReturnReason(""); setRejReturnPoid(c.product_order_id); }} className="text-xs px-2 py-1 rounded bg-rose-600 text-white hover:bg-rose-700">거부</button>
                            <button onClick={() => { setHbType("RETURN_DELIVERYFEE"); setHbDetail(""); setHbFee(""); setHbReturnPoid(c.product_order_id); }} className="text-xs px-2 py-1 rounded bg-amber-600 text-white hover:bg-amber-700">보류</button>
                          </div>
                        ) : c.claim_status === "EXCHANGE_REQUEST" ? (
                          <div className="flex items-center justify-center gap-1">
                            <button onClick={() => previewApproveExchangeCollect(c.product_order_id)} className="text-xs px-2 py-1 rounded bg-indigo-600 text-white hover:bg-indigo-700">수거완료</button>
                            <button onClick={() => { setRejExReason(""); setRejExPoid(c.product_order_id); }} className="text-xs px-2 py-1 rounded bg-rose-600 text-white hover:bg-rose-700">거부</button>
                            <button onClick={() => { setHbExType("EXCHANGE_PRODUCT_NOT_DELIVERED"); setHbExDetail(""); setHbExFee(""); setHbExPoid(c.product_order_id); }} className="text-xs px-2 py-1 rounded bg-amber-600 text-white hover:bg-amber-700">보류</button>
                          </div>
                        ) : c.claim_status === "COLLECT_DONE" && c.claim_type === "EXCHANGE" ? (
                          <button onClick={() => { setDispExMethod("DELIVERY"); setDispExCompany(""); setDispExTracking(""); setDispExPoid(c.product_order_id); }} className="text-xs px-2 py-1 rounded bg-sky-600 text-white hover:bg-sky-700">재배송</button>
                        ) : (
                          <span className="text-xs text-gray-400">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-gray-400">최근 {claimsDays}일 클레임이 없습니다.</p>
        )}
      </div>

      {/* 직접 취소요청 모달 */}
      {reqCancelOpen && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setReqCancelOpen(false)}>
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold mb-1">직접 취소 요청</h3>
            <p className="text-xs text-gray-500 mb-3">판매자가 특정 주문을 직접 취소 요청합니다.</p>
            <label className="block text-xs text-gray-600 mb-1">상품주문번호</label>
            <input value={rcPoid} onChange={(e) => setRcPoid(e.target.value)} placeholder="상품주문번호" className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-3" />
            <label className="block text-xs text-gray-600 mb-1">취소 사유</label>
            <select value={rcReason} onChange={(e) => setRcReason(e.target.value)} className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-3">
              {NAVER_CANCEL_REASONS.map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}
            </select>
            <label className="block text-xs text-gray-600 mb-1">상세 사유 (선택)</label>
            <input value={rcDetail} onChange={(e) => setRcDetail(e.target.value)} placeholder="상세 사유" className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-3" />
            <label className="block text-xs text-gray-600 mb-1">취소 수량 (비우면 전체)</label>
            <input value={rcQty} onChange={(e) => setRcQty(e.target.value)} type="number" min="1" placeholder="전체 취소" className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-4" />
            <div className="flex justify-end gap-2">
              <button onClick={() => setReqCancelOpen(false)} className="px-4 py-2 rounded text-sm bg-gray-100 hover:bg-gray-200">취소</button>
              <button onClick={previewRequestCancel} className="px-4 py-2 rounded text-sm font-medium bg-rose-600 text-white hover:bg-rose-700">미리보기 ›</button>
            </div>
          </div>
        </div>
      )}

      {/* 반품 거부 모달 */}
      {rejReturnPoid && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setRejReturnPoid(null)}>
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold mb-1">반품 거부(철회)</h3>
            <p className="text-xs text-gray-500 mb-3 truncate">{rejReturnPoid}</p>
            <label className="block text-xs text-gray-600 mb-1">거부 사유 (필수, 최대 250자)</label>
            <textarea value={rejReturnReason} onChange={(e) => setRejReturnReason(e.target.value)} rows={3} placeholder="예: 고객님께서 통화로 교환을 원하셨습니다." className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-4" />
            <div className="flex justify-end gap-2">
              <button onClick={() => setRejReturnPoid(null)} className="px-4 py-2 rounded text-sm bg-gray-100 hover:bg-gray-200">취소</button>
              <button onClick={previewRejectReturn} className="px-4 py-2 rounded text-sm font-medium bg-rose-600 text-white hover:bg-rose-700">미리보기 ›</button>
            </div>
          </div>
        </div>
      )}

      {/* 반품 보류 모달 */}
      {hbReturnPoid && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setHbReturnPoid(null)}>
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold mb-1">반품 보류</h3>
            <p className="text-xs text-gray-500 mb-3 truncate">{hbReturnPoid}</p>
            <label className="block text-xs text-gray-600 mb-1">보류 유형</label>
            <select value={hbType} onChange={(e) => setHbType(e.target.value)} className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-3">
              {NAVER_RETURN_HOLDBACK_TYPES.map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}
            </select>
            <label className="block text-xs text-gray-600 mb-1">보류 상세 사유 (필수)</label>
            <input value={hbDetail} onChange={(e) => setHbDetail(e.target.value)} placeholder="예: 미입고" className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-3" />
            <label className="block text-xs text-gray-600 mb-1">기타 반품 비용 (선택)</label>
            <input value={hbFee} onChange={(e) => setHbFee(e.target.value)} type="number" min="0" placeholder="0" className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-4" />
            <div className="flex justify-end gap-2">
              <button onClick={() => setHbReturnPoid(null)} className="px-4 py-2 rounded text-sm bg-gray-100 hover:bg-gray-200">취소</button>
              <button onClick={previewHoldbackReturn} className="px-4 py-2 rounded text-sm font-medium bg-amber-600 text-white hover:bg-amber-700">미리보기 ›</button>
            </div>
          </div>
        </div>
      )}

      {/* 반품 보류 해제 모달 */}
      {relReturnOpen && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setRelReturnOpen(false)}>
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold mb-1">반품 보류 해제</h3>
            <p className="text-xs text-gray-500 mb-3">보류했던 반품의 상품주문번호를 입력하세요.</p>
            <label className="block text-xs text-gray-600 mb-1">상품주문번호</label>
            <input value={relPoid} onChange={(e) => setRelPoid(e.target.value)} placeholder="상품주문번호" className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-4" />
            <div className="flex justify-end gap-2">
              <button onClick={() => setRelReturnOpen(false)} className="px-4 py-2 rounded text-sm bg-gray-100 hover:bg-gray-200">취소</button>
              <button onClick={previewReleaseReturnHoldback} className="px-4 py-2 rounded text-sm font-medium bg-gray-700 text-white hover:bg-gray-800">미리보기 ›</button>
            </div>
          </div>
        </div>
      )}

      {/* 직접 반품요청 모달 */}
      {reqReturnOpen && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setReqReturnOpen(false)}>
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold mb-1">직접 반품 요청</h3>
            <p className="text-xs text-gray-500 mb-3">판매자가 특정 주문을 직접 반품 접수합니다.</p>
            <label className="block text-xs text-gray-600 mb-1">상품주문번호</label>
            <input value={rrPoid} onChange={(e) => setRrPoid(e.target.value)} placeholder="상품주문번호" className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-3" />
            <label className="block text-xs text-gray-600 mb-1">반품 사유</label>
            <select value={rrReason} onChange={(e) => setRrReason(e.target.value)} className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-3">
              {NAVER_RETURN_REASONS.map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}
            </select>
            <label className="block text-xs text-gray-600 mb-1">수거 배송 방법</label>
            <select value={rrMethod} onChange={(e) => setRrMethod(e.target.value)} className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-3">
              {NAVER_COLLECT_DELIVERY_METHODS.map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}
            </select>
            <label className="block text-xs text-gray-600 mb-1">수거 택배사 (선택)</label>
            <select value={rrCompany} onChange={(e) => setRrCompany(e.target.value)} className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-3">
              <option value="">선택 안 함</option>
              {NAVER_DELIVERY_COMPANIES.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
            </select>
            <label className="block text-xs text-gray-600 mb-1">수거 송장번호 (선택)</label>
            <input value={rrTracking} onChange={(e) => setRrTracking(e.target.value)} placeholder="수거 송장번호" className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-3" />
            <label className="block text-xs text-gray-600 mb-1">반품 수량 (비우면 전체)</label>
            <input value={rrQty} onChange={(e) => setRrQty(e.target.value)} type="number" min="1" placeholder="전체 반품" className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-4" />
            <div className="flex justify-end gap-2">
              <button onClick={() => setReqReturnOpen(false)} className="px-4 py-2 rounded text-sm bg-gray-100 hover:bg-gray-200">취소</button>
              <button onClick={previewRequestReturn} className="px-4 py-2 rounded text-sm font-medium bg-orange-600 text-white hover:bg-orange-700">미리보기 ›</button>
            </div>
          </div>
        </div>
      )}

      {/* 교환 거부 모달 */}
      {rejExPoid && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setRejExPoid(null)}>
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold mb-1">교환 거부(철회)</h3>
            <p className="text-xs text-gray-500 mb-3 truncate">{rejExPoid}</p>
            <label className="block text-xs text-gray-600 mb-1">거부 사유 (필수, 최대 250자)</label>
            <textarea value={rejExReason} onChange={(e) => setRejExReason(e.target.value)} rows={3} placeholder="예: 착용한 상품은 교환할 수 없습니다." className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-4" />
            <div className="flex justify-end gap-2">
              <button onClick={() => setRejExPoid(null)} className="px-4 py-2 rounded text-sm bg-gray-100 hover:bg-gray-200">취소</button>
              <button onClick={previewRejectExchange} className="px-4 py-2 rounded text-sm font-medium bg-rose-600 text-white hover:bg-rose-700">미리보기 ›</button>
            </div>
          </div>
        </div>
      )}

      {/* 교환 보류 모달 */}
      {hbExPoid && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setHbExPoid(null)}>
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold mb-1">교환 보류</h3>
            <p className="text-xs text-gray-500 mb-3 truncate">{hbExPoid}</p>
            <label className="block text-xs text-gray-600 mb-1">보류 유형</label>
            <select value={hbExType} onChange={(e) => setHbExType(e.target.value)} className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-3">
              {NAVER_RETURN_HOLDBACK_TYPES.map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}
            </select>
            <label className="block text-xs text-gray-600 mb-1">보류 상세 사유 (필수)</label>
            <input value={hbExDetail} onChange={(e) => setHbExDetail(e.target.value)} placeholder="예: 미입고 상태" className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-3" />
            <label className="block text-xs text-gray-600 mb-1">기타 교환 비용 (선택)</label>
            <input value={hbExFee} onChange={(e) => setHbExFee(e.target.value)} type="number" min="0" placeholder="0" className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-4" />
            <div className="flex justify-end gap-2">
              <button onClick={() => setHbExPoid(null)} className="px-4 py-2 rounded text-sm bg-gray-100 hover:bg-gray-200">취소</button>
              <button onClick={previewHoldbackExchange} className="px-4 py-2 rounded text-sm font-medium bg-amber-600 text-white hover:bg-amber-700">미리보기 ›</button>
            </div>
          </div>
        </div>
      )}

      {/* 교환 재배송 모달 */}
      {dispExPoid && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setDispExPoid(null)}>
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold mb-1">교환 재배송</h3>
            <p className="text-xs text-gray-500 mb-3 truncate">{dispExPoid}</p>
            <label className="block text-xs text-gray-600 mb-1">배송 방법</label>
            <select value={dispExMethod} onChange={(e) => setDispExMethod(e.target.value)} className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-3">
              {NAVER_COLLECT_DELIVERY_METHODS.map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}
            </select>
            <label className="block text-xs text-gray-600 mb-1">택배사 (선택)</label>
            <select value={dispExCompany} onChange={(e) => setDispExCompany(e.target.value)} className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-3">
              <option value="">선택 안 함</option>
              {NAVER_DELIVERY_COMPANIES.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
            </select>
            <label className="block text-xs text-gray-600 mb-1">재배송 송장번호 (선택)</label>
            <input value={dispExTracking} onChange={(e) => setDispExTracking(e.target.value)} placeholder="재배송 송장번호" className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-4" />
            <div className="flex justify-end gap-2">
              <button onClick={() => setDispExPoid(null)} className="px-4 py-2 rounded text-sm bg-gray-100 hover:bg-gray-200">취소</button>
              <button onClick={previewDispatchExchange} className="px-4 py-2 rounded text-sm font-medium bg-sky-600 text-white hover:bg-sky-700">미리보기 ›</button>
            </div>
          </div>
        </div>
      )}

      {/* 교환 보류 해제 모달 */}
      {relExOpen && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setRelExOpen(false)}>
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold mb-1">교환 보류 해제</h3>
            <p className="text-xs text-gray-500 mb-3">보류했던 교환의 상품주문번호를 입력하세요.</p>
            <label className="block text-xs text-gray-600 mb-1">상품주문번호</label>
            <input value={relExPoid} onChange={(e) => setRelExPoid(e.target.value)} placeholder="상품주문번호" className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-4" />
            <div className="flex justify-end gap-2">
              <button onClick={() => setRelExOpen(false)} className="px-4 py-2 rounded text-sm bg-gray-100 hover:bg-gray-200">취소</button>
              <button onClick={previewReleaseExchangeHoldback} className="px-4 py-2 rounded text-sm font-medium bg-gray-700 text-white hover:bg-gray-800">미리보기 ›</button>
            </div>
          </div>
        </div>
      )}

      {/* 판매상태 변경 모달 — 트랙 N8 (D-11) */}
      {statusModal && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setStatusModal(null)}>
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold mb-1">판매상태 변경</h3>
            <p className="text-xs text-gray-500 mb-1 max-w-full truncate" title={statusModal.name}>{statusModal.name}</p>
            <p className="text-xs text-gray-400 mb-3">
              원상품번호 {statusModal.originNo} · 현재{" "}
              {statusModal.status === "SALE" ? "판매중" : statusModal.status === "SUSPENSION" ? "판매중지" : statusModal.status === "OUTOFSTOCK" ? "품절" : statusModal.status}
              {statusModal.stock != null ? ` · 재고 ${statusModal.stock.toLocaleString()}` : ""}
            </p>
            <label className="block text-xs text-gray-600 mb-1">변경할 상태</label>
            <div className="flex gap-2 mb-3">
              {(NAVER_STATUS_TRANSITIONS[statusModal.status] || []).map((code) => {
                const o = NAVER_PRODUCT_STATUS_OPTIONS.find((x) => x.code === code)!;
                return (
                  <button
                    key={code}
                    onClick={() => setCsStatus(code)}
                    className={`px-3 py-1.5 rounded text-sm font-medium ${csStatus === code ? "bg-indigo-600 text-white" : "bg-gray-100 text-gray-700 hover:bg-gray-200"}`}
                  >{o.label}</button>
                );
              })}
            </div>
            {csStatus === "SALE" && (
              <div className="mb-3">
                <label className="block text-xs text-gray-600 mb-1">재고 수량 <span className="text-red-500">(판매중 전환 시 필수)</span></label>
                <input
                  type="number" min={0} max={99999999}
                  value={csStock}
                  onChange={(e) => setCsStock(e.target.value)}
                  placeholder="예: 100"
                  className="border border-gray-300 rounded px-2 py-1 text-sm w-full"
                />
              </div>
            )}
            <p className="text-xs text-gray-500 mb-4">
              {csStatus === "OUTOFSTOCK" && "품절 처리 시 재고가 0으로 변경됩니다."}
              {csStatus === "SUSPENSION" && "판매중지로 전환합니다."}
              {csStatus === "SALE" && "판매중(재입고)으로 전환합니다. 가격은 변경하지 않습니다."}
            </p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setStatusModal(null)} className="px-4 py-2 rounded text-sm bg-gray-100 hover:bg-gray-200">취소</button>
              <button onClick={previewChangeStatus} className="px-4 py-2 rounded text-sm font-medium bg-indigo-600 text-white hover:bg-indigo-700">미리보기 ›</button>
            </div>
          </div>
        </div>
      )}

      {/* dry-run 미리보기 모달 */}
      {preview && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => !executing && setPreview(null)}>
          <div className="bg-white rounded-lg shadow-xl max-w-lg w-full p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold mb-1">미리보기 — {preview.title}</h3>
            <p className="text-xs text-gray-500 mb-3">아래 내용이 네이버로 전송됩니다. 확인 후 실행하세요.</p>
            <pre className="bg-gray-900 text-green-300 text-xs rounded p-3 overflow-auto max-h-72">{JSON.stringify(preview.result.would_send, null, 2)}</pre>
            <div className="flex justify-end gap-2 mt-4">
              <button onClick={() => setPreview(null)} disabled={executing} className="px-4 py-2 rounded text-sm bg-gray-100 hover:bg-gray-200 disabled:opacity-50">취소</button>
              <button onClick={() => preview.execute()} disabled={executing} className="px-4 py-2 rounded text-sm font-medium bg-red-600 text-white hover:bg-red-700 disabled:opacity-50">
                {executing ? "실행 중…" : "실제 실행 ▶"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 발송지연 입력 모달 */}
      {delayTarget && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setDelayTarget(null)}>
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold mb-1">발송지연 처리</h3>
            <p className="text-xs text-gray-500 mb-3 truncate">{delayTarget.product_order_id} · {delayTarget.product_name}</p>
            <label className="block text-xs text-gray-600 mb-1">발송기한</label>
            <input type="date" value={delayDue} onChange={(e) => setDelayDue(e.target.value)} className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-3" />
            <label className="block text-xs text-gray-600 mb-1">지연 사유</label>
            <select value={delayReason} onChange={(e) => setDelayReason(e.target.value)} className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-3">
              {NAVER_DELAY_REASONS.map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}
            </select>
            <label className="block text-xs text-gray-600 mb-1">상세 사유</label>
            <input value={delayDetail} onChange={(e) => setDelayDetail(e.target.value)} placeholder="예: 상품 준비중입니다." className="border border-gray-300 rounded px-2 py-1 text-sm w-full mb-4" />
            <div className="flex justify-end gap-2">
              <button onClick={() => setDelayTarget(null)} className="px-4 py-2 rounded text-sm bg-gray-100 hover:bg-gray-200">취소</button>
              <button onClick={previewDelay} className="px-4 py-2 rounded text-sm font-medium bg-amber-600 text-white hover:bg-amber-700">미리보기 ›</button>
            </div>
          </div>
        </div>
      )}

      {/* 🛍️ 상품 목록 — 트랙 N4 */}
      <div className="bg-white rounded-lg shadow p-4">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
          <h2 className="text-lg font-bold text-gray-900">🛍️ 상품 목록</h2>
          <div className="flex gap-2">
            {["SALE", "SUSPENSION", "CLOSE", ""].map((s) => (
              <button
                key={s || "ALL"}
                onClick={() => setProductStatus(s)}
                className={`px-3 py-1 rounded text-sm font-medium ${productStatus === s ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-700 hover:bg-gray-200"}`}
              >
                {s === "SALE" ? "판매중" : s === "SUSPENSION" ? "판매중지" : s === "CLOSE" ? "품절" : "전체"}
              </button>
            ))}
            <button onClick={reloadProducts} className="px-3 py-1 rounded text-sm bg-gray-100 hover:bg-gray-200">↺</button>
          </div>
        </div>
        {productLoading ? (
          <p className="text-sm text-gray-500">로딩 중…</p>
        ) : products ? (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-4">
              <div className="bg-blue-50 rounded p-3 text-center">
                <p className="text-xs text-gray-500">전체 상품</p>
                <p className="text-xl font-bold text-blue-700">{products.total_elements.toLocaleString()}</p>
              </div>
              <div className="bg-green-50 rounded p-3 text-center">
                <p className="text-xs text-gray-500">현재 페이지</p>
                <p className="text-xl font-bold text-green-700">{products.contents.flatMap(p => p.channel_products).length.toLocaleString()}</p>
              </div>
              <div className="bg-gray-50 rounded p-3 text-center">
                <p className="text-xs text-gray-500">총 페이지</p>
                <p className="text-xl font-bold text-gray-700">{products.total_pages}</p>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 text-gray-600 text-xs">
                    <th className="px-3 py-2 text-left">상품번호</th>
                    <th className="px-3 py-2 text-left">상품명</th>
                    <th className="px-3 py-2 text-left">카테고리</th>
                    <th className="px-3 py-2 text-right">판매가</th>
                    <th className="px-3 py-2 text-right">재고</th>
                    <th className="px-3 py-2 text-center">상태</th>
                    <th className="px-3 py-2 text-center">판매상태 변경</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {products.contents.flatMap((p) => p.channel_products.map((cp) => ({ cp, originNo: p.origin_product_no }))).map(({ cp, originNo }) => (
                    <tr key={cp.channel_product_no} className="hover:bg-gray-50">
                      <td className="px-3 py-2 text-gray-500 text-xs">{cp.channel_product_no}</td>
                      <td className="px-3 py-2 font-medium max-w-xs truncate">{cp.name}</td>
                      <td className="px-3 py-2 text-gray-500 text-xs max-w-xs truncate">{cp.category}</td>
                      <td className="px-3 py-2 text-right">{cp.sale_price != null ? cp.sale_price.toLocaleString() + "원" : "-"}</td>
                      <td className="px-3 py-2 text-right">{cp.stock_quantity != null ? cp.stock_quantity.toLocaleString() : "-"}</td>
                      <td className="px-3 py-2 text-center">
                        <span className={`px-2 py-0.5 rounded text-xs font-medium ${cp.status_type === "SALE" ? "bg-green-100 text-green-700" : cp.status_type === "OUTOFSTOCK" ? "bg-yellow-100 text-yellow-700" : "bg-gray-100 text-gray-600"}`}>
                          {cp.status_type === "SALE" ? "판매중" : cp.status_type === "SUSPENSION" ? "판매중지" : cp.status_type === "OUTOFSTOCK" ? "품절" : cp.status_type === "CLOSE" ? "종료" : cp.status_type}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-center">
                        {NAVER_STATUS_TRANSITIONS[cp.status_type] ? (
                          <button
                            onClick={() => openStatusModal(originNo, cp.name, cp.status_type, cp.stock_quantity)}
                            className="px-2 py-1 rounded text-xs font-medium bg-indigo-50 text-indigo-700 hover:bg-indigo-100"
                          >변경 ›</button>
                        ) : (
                          <span className="text-gray-300 text-xs">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <p className="text-sm text-gray-500">상품 데이터를 불러오지 못했습니다.</p>
        )}
      </div>

      {/* 🏪 판매자 정보 — 트랙 N5 */}
      <div className="bg-white rounded-lg shadow p-4">
        <h2 className="text-lg font-bold text-gray-900 mb-4">🏪 판매자 정보</h2>
        {sellerInfo ? (
          <div className="space-y-3">
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              <div className="bg-gray-50 rounded p-3">
                <p className="text-xs text-gray-500">계정 ID</p>
                <p className="font-medium">{sellerInfo.account_id}</p>
              </div>
              <div className="bg-gray-50 rounded p-3">
                <p className="text-xs text-gray-500">등급</p>
                <p className="font-medium">{sellerInfo.grade || "-"}</p>
              </div>
              <div className="bg-gray-50 rounded p-3">
                <p className="text-xs text-gray-500">채널 수</p>
                <p className="font-medium">{sellerInfo.channels.length}</p>
              </div>
            </div>
            {sellerInfo.channels.map((ch) => (
              <div key={ch.channel_no} className="border rounded p-3 text-sm">
                <div className="flex items-center gap-2 mb-1">
                  <span className="font-medium">{ch.name}</span>
                  <span className="px-2 py-0.5 bg-blue-100 text-blue-700 text-xs rounded">{ch.channel_type}</span>
                </div>
                <div className="text-gray-500 space-y-0.5 text-xs">
                  <p>채널번호: {ch.channel_no}</p>
                  {ch.url && <p>URL: <a href={ch.url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">{ch.url}</a></p>}
                  {ch.talktalk_id && <p>톡톡 ID: {ch.talktalk_id}</p>}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-500">판매자 정보를 불러오는 중…</p>
        )}
      </div>
    </div>
  );
}
