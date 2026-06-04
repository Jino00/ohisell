// CoupangOps.tsx — 🔧 쿠팡 운영 패널 (W1·W4 쓰기 라우트 UI).
// D-16: dry_run=true 기본 → 미리보기 확인 → CONFIRM_TOKEN 입력 → 실제 실행.
// D-3: 전략 추천 없음, 실행 게이트만 제공.
import { useState, useEffect } from "react";
import {
  fetchProductItems,
  opsUpdateQuantity,
  opsUpdatePrice,
  opsUpdateBasePrice,
  opsResumeSale,
  opsStopSale,
  opsExpireInstantCoupon,
  type ProductItem,
  type OpsResult,
} from "../lib/api";

const CONFIRM_TOKEN = "CONFIRM_LIVE_WRITE";

type OpTab = "재고" | "가격" | "기준가" | "판매중지" | "판매재개" | "쿠폰파기";

const ACCOUNT_OPTIONS = [
  { value: "COUPANG_WING1", label: "WING1 (오픽스)" },
  { value: "COUPANG_WING2", label: "WING2 (오하이테크)" },
];

function won(v: string | null | undefined) {
  if (v == null) return "—";
  return `${Math.round(Number(v)).toLocaleString("ko-KR")}원`;
}

export default function CoupangOps() {
  const [items, setItems] = useState<ProductItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [accountFilter, setAccountFilter] = useState<string>("ALL");

  const [selected, setSelected] = useState<ProductItem | null>(null);
  const [tab, setTab] = useState<OpTab>("재고");

  // 입력값
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");
  const [basePrice, setBasePrice] = useState("");
  const [couponId, setCouponId] = useState("");
  const [account, setAccount] = useState("COUPANG_WING2");

  // 실행 상태
  const [preview, setPreview] = useState<OpsResult | null>(null);
  const [confirmToken, setConfirmToken] = useState("");
  const [running, setRunning] = useState(false);
  const [liveResult, setLiveResult] = useState<OpsResult | null>(null);
  const [opError, setOpError] = useState<string | null>(null);

  useEffect(() => {
    fetchProductItems()
      .then(setItems)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  function selectItem(item: ProductItem) {
    setSelected(item);
    setAccount(item.account_key);
    setPreview(null);
    setConfirmToken("");
    setLiveResult(null);
    setOpError(null);
    setQuantity("");
    setPrice("");
    setBasePrice("");
    setCouponId("");
  }

  function resetOp() {
    setPreview(null);
    setConfirmToken("");
    setLiveResult(null);
    setOpError(null);
  }

  async function runOp(dryRun: boolean) {
    if (!selected && tab !== "쿠폰파기") return;
    const vid = selected ? Number(selected.vendor_item_id) : 0;
    const confirm = dryRun ? undefined : CONFIRM_TOKEN;
    setRunning(true);
    setOpError(null);
    if (dryRun) setPreview(null);
    else setLiveResult(null);

    try {
      let res: OpsResult;
      if (tab === "재고") {
        res = await opsUpdateQuantity(vid, Number(quantity), account, dryRun, confirm);
      } else if (tab === "가격") {
        res = await opsUpdatePrice(vid, Number(price), account, dryRun, confirm);
      } else if (tab === "기준가") {
        res = await opsUpdateBasePrice(vid, Number(basePrice), account, dryRun, confirm);
      } else if (tab === "판매중지") {
        res = await opsStopSale(vid, account, dryRun, confirm);
      } else if (tab === "판매재개") {
        res = await opsResumeSale(vid, account, dryRun, confirm);
      } else {
        res = await opsExpireInstantCoupon(Number(couponId), account, dryRun, confirm);
      }
      if (dryRun) setPreview(res);
      else setLiveResult(res);
    } catch (e: any) {
      setOpError(e.message);
    } finally {
      setRunning(false);
    }
  }

  const filtered = items.filter((it) => {
    const matchAcc = accountFilter === "ALL" || it.account_key === accountFilter;
    const q = search.toLowerCase();
    const matchQ =
      !q ||
      it.item_name.toLowerCase().includes(q) ||
      it.seller_product_name.toLowerCase().includes(q) ||
      it.vendor_item_id.includes(q);
    return matchAcc && matchQ;
  });

  const tabs: OpTab[] = ["재고", "가격", "기준가", "판매중지", "판매재개", "쿠폰파기"];

  return (
    <div className="flex gap-4 h-full">
      {/* ── 왼쪽: 상품 목록 ── */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-xl font-bold text-gray-900">🔧 쿠팡 운영 패널</h2>
          <button
            onClick={() => {
              setLoading(true);
              fetchProductItems()
                .then(setItems)
                .catch((e) => setError(e.message))
                .finally(() => setLoading(false));
            }}
            className="text-xs text-blue-600 hover:underline"
          >
            새로고침
          </button>
        </div>

        {/* 필터 */}
        <div className="flex gap-2 mb-3">
          <input
            className="border border-gray-300 rounded px-3 py-1.5 text-sm flex-1"
            placeholder="상품명 / 옵션ID 검색…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <select
            className="border border-gray-300 rounded px-2 py-1.5 text-sm"
            value={accountFilter}
            onChange={(e) => setAccountFilter(e.target.value)}
          >
            <option value="ALL">전체 계정</option>
            {ACCOUNT_OPTIONS.map((a) => (
              <option key={a.value} value={a.value}>
                {a.label}
              </option>
            ))}
          </select>
        </div>

        {error && <p className="text-red-600 text-sm mb-2">{error}</p>}

        <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-xs">
              <tr>
                <th className="px-3 py-2 text-left">상품명</th>
                <th className="px-3 py-2 text-left">계정</th>
                <th className="px-3 py-2 text-right">판매가</th>
                <th className="px-3 py-2 text-right">재고</th>
                <th className="px-3 py-2 text-center">상태</th>
                <th className="px-3 py-2 text-left">옵션ID</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={6} className="px-3 py-6 text-center text-gray-400">
                    로딩 중…
                  </td>
                </tr>
              ) : filtered.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-3 py-6 text-center text-gray-400">
                    {items.length === 0
                      ? "상품 없음 — 동기화 후 재시도"
                      : "검색 결과 없음"}
                  </td>
                </tr>
              ) : (
                filtered.map((it) => (
                  <tr
                    key={it.vendor_item_id}
                    onClick={() => selectItem(it)}
                    className={`border-t border-gray-100 cursor-pointer hover:bg-blue-50 ${
                      selected?.vendor_item_id === it.vendor_item_id
                        ? "bg-blue-50"
                        : ""
                    }`}
                  >
                    <td className="px-3 py-2 max-w-[200px] truncate" title={it.item_name}>
                      <div className="font-medium text-gray-900 truncate">{it.item_name}</div>
                      <div className="text-xs text-gray-400 truncate">{it.seller_product_name}</div>
                    </td>
                    <td className="px-3 py-2 text-xs text-gray-500">
                      {it.account_key.replace("COUPANG_", "")}
                    </td>
                    <td className="px-3 py-2 text-right">{won(it.sale_price)}</td>
                    <td className="px-3 py-2 text-right text-gray-700">
                      {it.stock ?? "—"}
                    </td>
                    <td className="px-3 py-2 text-center">
                      {it.on_sale ? (
                        <span className="text-green-600 text-xs font-medium">판매중</span>
                      ) : (
                        <span className="text-red-500 text-xs font-medium">중지</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-xs text-gray-400 font-mono">
                      {it.vendor_item_id}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-gray-400 mt-1">{filtered.length}개 표시</p>
      </div>

      {/* ── 오른쪽: 운영 패널 ── */}
      <div className="w-80 flex-shrink-0">
        <div className="bg-white border border-gray-200 rounded-lg p-4 sticky top-0">
          {!selected ? (
            <div className="text-center text-gray-400 py-12 text-sm">
              ← 상품을 선택하면<br />운영 패널이 열립니다
            </div>
          ) : (
            <>
              {/* 선택 상품 */}
              <div className="mb-4">
                <div className="text-xs text-gray-400 mb-1">선택된 상품</div>
                <div className="font-medium text-gray-900 text-sm truncate">{selected.item_name}</div>
                <div className="text-xs text-gray-500 mt-0.5">
                  {selected.account_key.replace("COUPANG_", "")} · 옵션ID {selected.vendor_item_id}
                </div>
                <div className="text-xs text-gray-500 mt-0.5">
                  현재: {won(selected.sale_price)} / 재고 {selected.stock ?? "—"} /{" "}
                  {selected.on_sale ? "판매중" : "판매중지"}
                </div>
              </div>

              {/* 계정 선택 */}
              <div className="mb-3">
                <label className="text-xs text-gray-500 block mb-1">계정</label>
                <select
                  className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm"
                  value={account}
                  onChange={(e) => { setAccount(e.target.value); resetOp(); }}
                >
                  {ACCOUNT_OPTIONS.map((a) => (
                    <option key={a.value} value={a.value}>{a.label}</option>
                  ))}
                </select>
              </div>

              {/* 작업 탭 */}
              <div className="flex flex-wrap gap-1 mb-4">
                {tabs.map((t) => (
                  <button
                    key={t}
                    onClick={() => { setTab(t); resetOp(); }}
                    className={`px-2 py-1 rounded text-xs font-medium ${
                      tab === t
                        ? "bg-blue-600 text-white"
                        : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                    }`}
                  >
                    {t}
                  </button>
                ))}
              </div>

              {/* 입력 필드 */}
              {tab === "재고" && (
                <div className="mb-3">
                  <label className="text-xs text-gray-500 block mb-1">재고 수량</label>
                  <input
                    type="number" min="0"
                    className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm"
                    value={quantity}
                    onChange={(e) => { setQuantity(e.target.value); resetOp(); }}
                    placeholder="0 이상 정수"
                  />
                </div>
              )}
              {tab === "가격" && (
                <div className="mb-3">
                  <label className="text-xs text-gray-500 block mb-1">판매가 (원)</label>
                  <input
                    type="number" min="1"
                    className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm"
                    value={price}
                    onChange={(e) => { setPrice(e.target.value); resetOp(); }}
                    placeholder="예: 29000"
                  />
                </div>
              )}
              {tab === "기준가" && (
                <div className="mb-3">
                  <label className="text-xs text-gray-500 block mb-1">할인율 기준가 (원)</label>
                  <input
                    type="number" min="0"
                    className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm"
                    value={basePrice}
                    onChange={(e) => { setBasePrice(e.target.value); resetOp(); }}
                    placeholder="예: 39000"
                  />
                </div>
              )}
              {tab === "쿠폰파기" && (
                <div className="mb-3">
                  <label className="text-xs text-gray-500 block mb-1">즉시할인쿠폰 ID</label>
                  <input
                    type="number" min="1"
                    className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm"
                    value={couponId}
                    onChange={(e) => { setCouponId(e.target.value); resetOp(); }}
                    placeholder="couponId"
                  />
                </div>
              )}

              {/* 미리보기 버튼 */}
              <button
                onClick={() => runOp(true)}
                disabled={running}
                className="w-full bg-gray-700 text-white rounded py-1.5 text-sm font-medium mb-2 disabled:opacity-50"
              >
                {running && !preview ? "처리 중…" : "🔍 미리보기 (dry_run)"}
              </button>

              {/* 미리보기 결과 */}
              {preview && (
                <div className="bg-gray-50 rounded p-3 mb-3 text-xs font-mono break-all text-gray-700">
                  <div className="text-gray-400 mb-1">dry_run 결과</div>
                  {JSON.stringify(preview, null, 2)}
                </div>
              )}

              {/* 라이브 실행 영역 */}
              {preview && (
                <>
                  <div className="border-t border-gray-200 pt-3 mb-2">
                    <label className="text-xs text-gray-500 block mb-1">
                      ⚠️ 실제 실행 확인 토큰
                    </label>
                    <input
                      className="w-full border border-red-300 rounded px-2 py-1.5 text-sm"
                      value={confirmToken}
                      onChange={(e) => setConfirmToken(e.target.value)}
                      placeholder={CONFIRM_TOKEN}
                    />
                  </div>
                  <button
                    onClick={() => runOp(false)}
                    disabled={running || confirmToken !== CONFIRM_TOKEN}
                    className="w-full bg-red-600 text-white rounded py-1.5 text-sm font-medium disabled:opacity-40"
                  >
                    {running && liveResult === null ? "실행 중…" : "🚀 라이브 실행"}
                  </button>
                </>
              )}

              {/* 라이브 결과 */}
              {liveResult && (
                <div className="bg-green-50 border border-green-200 rounded p-3 mt-2 text-xs font-mono break-all text-green-800">
                  <div className="text-green-600 font-semibold mb-1">✅ 실행 완료</div>
                  {JSON.stringify(liveResult, null, 2)}
                </div>
              )}

              {/* 에러 */}
              {opError && (
                <div className="bg-red-50 border border-red-200 rounded p-3 mt-2 text-xs text-red-700">
                  {opError}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
