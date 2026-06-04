// NaverOps.tsx — 🛒 네이버 스마트스토어 운영 패널
// 기간별 매출 현황 + 상품별 상세 (쿠팡 패널 단순화 버전)
import { useState, useEffect, useCallback, useRef } from "react";
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
  NAVER_CANCEL_REASONS, NAVER_CLAIM_STATUS_LABELS,
  NAVER_RETURN_REASONS, NAVER_RETURN_HOLDBACK_TYPES, NAVER_COLLECT_DELIVERY_METHODS,
  type NaverSalesSummary, type NaverSalesProductRow, type GfaStatus,
  type NaverSettlement, type NaverInquiries,
  type NaverProductList, type NaverSellerInfo,
  type NaverPendingOrders, type NaverPendingOrder, type NaverWriteResult,
  type NaverDispatchItem, type NaverClaims,
} from "../lib/api";

// 마지막 업로드일로부터 경과 일수 (로컬 기준). null이면 데이터 없음.
function daysSince(dateStr: string | null | undefined): number | null {
  if (!dateStr) return null;
  const d = new Date(dateStr + "T00:00:00");
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((today.getTime() - d.getTime()) / 86400000);
}

const PERIODS = [
  { label: "오늘", days: 0 },
  { label: "어제", days: 1 },
  { label: "7일", days: 7 },
  { label: "15일", days: 15 },
  { label: "30일", days: 30 },
];

type SortKey = "product_name" | "revenue" | "profit" | "profit_rate";
type SortDir = "asc" | "desc";
type ColKey = "revenue" | "profit" | "profit_rate";

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
  if (col === "revenue") return won(row.revenue);
  if (col === "profit") return won(row.profit);
  return row.profit_rate ? pct(row.profit_rate) : "—";
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
  const [days, setDays] = useState(7);
  const [data, setData] = useState<NaverSalesSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("revenue");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [colFilters, setColFilters] = useState<Partial<Record<ColKey, Set<string>>>>({});
  const [openFilter, setOpenFilter] = useState<ColKey | null>(null);
  const [gfa, setGfa] = useState<GfaStatus | null>(null);
  const [gfaUploading, setGfaUploading] = useState(false);
  const [gfaMsg, setGfaMsg] = useState<string | null>(null);
  const gfaFileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      setData(await fetchNaverSalesSummary(days));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [days]);

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
            await (reload ? reload() : loadPending(pendingDays));
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
      () => loadClaims(claimsDays),
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
      () => loadClaims(claimsDays),
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
      () => loadClaims(claimsDays),
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
      () => loadClaims(claimsDays),
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
      () => loadClaims(claimsDays),
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
      () => loadClaims(claimsDays),
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
      () => loadClaims(claimsDays),
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
      () => loadClaims(claimsDays),
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
      () => loadClaims(claimsDays),
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
      () => loadClaims(claimsDays),
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
      () => loadClaims(claimsDays),
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
      () => loadClaims(claimsDays),
    );
  }

  useEffect(() => { load(); }, [load]);
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
      setTimeout(() => { setSyncing(false); load(); }, 3000);
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
    av = Number(a[sortKey] ?? 0); bv = Number(b[sortKey] ?? 0);
    return sortDir === "asc" ? av - bv : bv - av;
  });

  const s = data?.summary;
  const profitN = s ? Number(s.profit) : 0;

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

      {/* 기간 선택 */}
      <div className="flex flex-wrap items-center gap-3 mb-6">
        <div className="flex gap-1">
          {PERIODS.map((p) => (
            <button
              key={p.days}
              onClick={() => setDays(p.days)}
              className={`px-3 py-1.5 text-sm rounded-md font-medium transition-colors ${
                days === p.days
                  ? "bg-green-600 text-white"
                  : "bg-white border border-gray-300 text-gray-700 hover:bg-gray-50"
              }`}
            >{p.label}</button>
          ))}
        </div>
        <button
          onClick={handleSync}
          disabled={syncing}
          className="px-3 py-1.5 text-sm rounded-md border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-50"
        >{syncing ? "동기화 중…" : "🔄 동기화"}</button>
        {data && (
          <span className="text-xs text-gray-400">
            {data.period.from} ~ {data.period.to}
            {data.ad_ref_date && ` (광고비 기준일: ${data.ad_ref_date})`}
          </span>
        )}
      </div>

      {/* 디스플레이(GFA) 광고비 신선도 배지 + 업로드 */}
      {(() => {
        const ago = daysSince(gfa?.date_to);
        const stale = ago == null || ago >= 2;   // 어제(1일)까지는 정상(당일 데이터는 미제공)
        return (
          <div className={`flex flex-wrap items-center gap-3 mb-6 px-4 py-3 rounded-lg border ${
            stale ? "border-red-300 bg-red-50" : "border-green-200 bg-green-50"
          }`}>
            <span className="text-sm font-medium text-gray-700">디스플레이 광고비(GFA)</span>
            {gfa?.date_to ? (
              <span className={`text-sm ${stale ? "text-red-600 font-semibold" : "text-green-700"}`}>
                마지막 업로드: {gfa.date_to}
                {ago != null && ago > 0 && ` (${ago}일 전)`}
                {stale ? " ⚠️ 업데이트 필요" : " ✓ 최신"}
              </span>
            ) : (
              <span className="text-sm text-red-600 font-semibold">데이터 없음 ⚠️ CSV 업로드 필요</span>
            )}
            <button
              onClick={() => gfaFileRef.current?.click()}
              disabled={gfaUploading}
              className="ml-auto px-3 py-1.5 text-sm rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
            >{gfaUploading ? "업로드 중…" : "📤 CSV 업로드"}</button>
            <input
              ref={gfaFileRef} type="file" accept=".csv"
              onChange={handleGfaUpload} className="hidden"
            />
            {gfaMsg && <span className="w-full text-xs text-gray-600">{gfaMsg}</span>}
            <span className="w-full text-xs text-gray-400">
              네이버 광고주센터 → 보고서 → 광고비 보고서 CSV 다운로드 후 업로드 (API 미제공으로 수동)
            </span>
          </div>
        );
      })()}

      {/* 요약 카드 */}
      {s && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
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
          <SummaryCard label="원가" value={won(s.cost)} />
          <SummaryCard label="광고비" value={won(s.ad_spend)} sub="검색+디스플레이 · 상품별 미배분" />
          <SummaryCard
            label="물류비(한진)"
            value={won(s.logistics)}
            sub={`배송 ${s.shipment_count}건 × 1,900`}
          />
          <SummaryCard
            label="이익"
            value={won(s.profit)}
            highlight={profitN >= 0 ? "blue" : "red"}
          />
          <SummaryCard
            label="이익률"
            value={s.profit_rate ? pct(s.profit_rate) : "—"}
            highlight={profitN >= 0 ? "blue" : "red"}
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
        </div>
      )}

      {/* 상태 */}
      {loading && <p className="text-sm text-gray-500">불러오는 중…</p>}
      {error && <p className="text-sm text-red-600">{error}</p>}

      {/* 상품별 테이블 */}
      {!loading && sorted.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="min-w-full bg-white text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <Th label="상품명" sk="product_name" />
                <Th label="총매출" sk="revenue" col="revenue" />
                <Th label="이익" sk="profit" col="profit" />
                <Th label="이익률" sk="profit_rate" col="profit_rate" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {sorted.map((r, i) => (
                <tr key={i} className="hover:bg-gray-50">
                  <td className="px-3 py-2 max-w-xs">
                    <div className="text-gray-900 truncate" title={r.product_name}>{r.product_name}</div>
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{won(r.revenue)}</td>
                  <td className={`px-3 py-2 text-right tabular-nums font-medium ${profitColor(r.profit)}`}>{won(r.profit)}</td>
                  <td className={`px-3 py-2 text-right tabular-nums font-medium ${profitColor(r.profit)}`}>{pct(r.profit_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="text-xs text-gray-400 px-3 py-2 border-t border-gray-100">
            * 모든 금액은 <b>공급가(부가세 제외)</b> 기준 — 부가세는 통과항목이라 매출·비용 모두 ÷1.1로 통일<br />
            * 요약 순이익 = (상품매출 + 고객배송비 − 수수료 − 원가 − 한진물류비) ÷ 1.1 − 광고비<br />
            * 상품별 이익 = (상품매출 − 수수료 − 원가) ÷ 1.1 · 배송·물류·광고비는 배송/계정 단위라 요약에만 반영<br />
            * 수수료 = 정산 완료분은 네이버 건별정산 <b>실측</b>, 미정산 최근 주문은 주문시점 <b>예상</b>(하이브리드)
          </div>
        </div>
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
            <button onClick={() => loadClaims(claimsDays)} className="px-3 py-1 rounded text-sm bg-gray-100 hover:bg-gray-200">↺</button>
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
            <button onClick={() => loadProducts(productStatus)} className="px-3 py-1 rounded text-sm bg-gray-100 hover:bg-gray-200">↺</button>
          </div>
        </div>
        {productLoading ? (
          <p className="text-sm text-gray-500">로딩 중…</p>
        ) : products ? (
          <>
            <div className="grid grid-cols-3 gap-3 mb-4">
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
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {products.contents.flatMap(p => p.channel_products).map((cp) => (
                    <tr key={cp.channel_product_no} className="hover:bg-gray-50">
                      <td className="px-3 py-2 text-gray-500 text-xs">{cp.channel_product_no}</td>
                      <td className="px-3 py-2 font-medium max-w-xs truncate">{cp.name}</td>
                      <td className="px-3 py-2 text-gray-500 text-xs max-w-xs truncate">{cp.category}</td>
                      <td className="px-3 py-2 text-right">{cp.sale_price != null ? cp.sale_price.toLocaleString() + "원" : "-"}</td>
                      <td className="px-3 py-2 text-right">{cp.stock_quantity != null ? cp.stock_quantity.toLocaleString() : "-"}</td>
                      <td className="px-3 py-2 text-center">
                        <span className={`px-2 py-0.5 rounded text-xs font-medium ${cp.status_type === "SALE" ? "bg-green-100 text-green-700" : cp.status_type === "CLOSE" ? "bg-yellow-100 text-yellow-700" : "bg-gray-100 text-gray-600"}`}>
                          {cp.status_type === "SALE" ? "판매중" : cp.status_type === "SUSPENSION" ? "판매중지" : cp.status_type === "CLOSE" ? "품절" : cp.status_type}
                        </span>
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
            <div className="grid grid-cols-3 gap-3">
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
