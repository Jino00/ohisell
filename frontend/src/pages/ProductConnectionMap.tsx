// ProductConnectionMap.tsx — 상품 연결맵 (트랙 S4, D-12)
//   탭1 연관맵 매트릭스(내부옵션×채널·인라인 편집·커버리지/충돌 배지·엑셀 업로드)
//   탭2 통합 손익(S5에서 GET /api/products/pnl-reconciliation 소비 — 지금은 자리표시)
import { useState, useEffect, useCallback, useRef } from "react";
import {
  fetchApi,
  uploadFile,
  downloadUrl,
  fetchConnectionMap,
  fetchMappingCoverage,
  updateMapping,
  fetchPnlReconciliation,
  type ConnectionMap,
  type ConnCell,
  type ChannelCoverage,
  type MappingIngestResult,
  type PnlReconciliation,
  type PnlSkuRow,
} from "../lib/api";

const fmt = (n: number) => new Intl.NumberFormat("ko-KR").format(n);
const won = (s: string) => `${fmt(Math.round(Number(s)))}원`;

function isoKST(d: Date): string {
  const kst = new Date(d.toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
  return `${kst.getFullYear()}-${String(kst.getMonth() + 1).padStart(2, "0")}-${String(kst.getDate()).padStart(2, "0")}`;
}

const PNL_ACCOUNTS = [
  { value: "", label: "전체" },
  { value: "COUPANG_WING1", label: "오픽스" },
  { value: "COUPANG_WING2", label: "오하이테크" },
];

type Tab = "map" | "pnl";

export default function ProductConnectionMap() {
  const [tab, setTab] = useState<Tab>("map");
  return (
    <div>
      <h2 className="text-2xl font-bold text-gray-900 mb-4">상품 연결맵</h2>
      <div className="flex gap-1 border-b border-gray-200 mb-4">
        <TabBtn active={tab === "map"} onClick={() => setTab("map")}>
          연관맵 관리
        </TabBtn>
        <TabBtn active={tab === "pnl"} onClick={() => setTab("pnl")}>
          통합 손익
        </TabBtn>
      </div>
      {tab === "map" ? <ConnectionMapTab /> : <PnlTab />}
    </div>
  );
}

function TabBtn({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 text-sm font-medium -mb-px border-b-2 ${
        active
          ? "border-blue-600 text-blue-700"
          : "border-transparent text-gray-500 hover:text-gray-700"
      }`}
    >
      {children}
    </button>
  );
}

// ─────────────────────────────────────────────────────────────
// 탭1: 연관맵 매트릭스
// ─────────────────────────────────────────────────────────────
function ConnectionMapTab() {
  const [map, setMap] = useState<ConnectionMap | null>(null);
  const [coverage, setCoverage] = useState<ChannelCoverage[]>([]);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState("");
  const [err, setErr] = useState("");
  const mappingFileRef = useRef<HTMLInputElement>(null);

  // 편집/추가 중인 셀
  const [editing, setEditing] = useState<{ productId: number; mapping: ConnCell } | null>(null);
  const [adding, setAdding] = useState<{ productId: number; channelId: number } | null>(null);

  const load = useCallback(async (query: string) => {
    setLoading(true);
    setErr("");
    try {
      const [m, cov] = await Promise.all([
        fetchConnectionMap(query || undefined),
        fetchMappingCoverage(),
      ]);
      setMap(m);
      setCoverage(cov);
    } catch (e) {
      setErr(`불러오기 실패: ${e}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load("");
  }, [load]);

  const covByChannel = new Map(coverage.map((c) => [c.channel_id, c]));

  async function handleMappingUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const r: MappingIngestResult = await uploadFile("/api/products/upload-by-name", file);
      const issues =
        r.unknown_labels.length +
        r.duplicate_product_names.length +
        r.duplicate_channel_ids.length +
        r.mapping_conflicts.length +
        r.label_mismatches.length;
      setUploadMsg(
        `상품 생성 ${r.products_created} · 수정 ${r.products_updated} · 매핑 ${r.mappings_created} · 주문연결 ${r.orders_linked}` +
          (r.mappings_conflicted ? ` / 충돌 ${r.mappings_conflicted}(기존 유지)` : "") +
          (issues ? ` / 확인 필요 ${issues}건(콘솔)` : ""),
      );
      if (issues) console.warn("매핑 적재 무결성 이슈", r);
      load(q);
    } catch (e) {
      setUploadMsg(`업로드 실패: ${e}`);
    }
    if (mappingFileRef.current) mappingFileRef.current.value = "";
  }

  async function saveEdit(patch: {
    channel_product_id: string;
    selling_price: number;
    is_active: boolean;
  }) {
    if (!editing) return;
    setErr("");
    try {
      await updateMapping(editing.productId, editing.mapping.mapping_id, patch);
      setEditing(null);
      load(q);
    } catch (e) {
      setErr(`${e}`.replace(/^Error:\s*/, ""));
    }
  }

  async function deleteMapping(productId: number, mappingId: number) {
    if (!confirm("이 매핑을 삭제하시겠습니까?")) return;
    setErr("");
    try {
      await fetchApi(`/api/products/${productId}/mappings/${mappingId}`, { method: "DELETE" });
      setEditing(null);
      load(q);
    } catch (e) {
      setErr(`${e}`);
    }
  }

  async function addMapping(add: {
    channel_id: number;
    channel_product_id: string;
    selling_price: number;
  }) {
    if (!adding) return;
    setErr("");
    try {
      await fetchApi(`/api/products/${adding.productId}/mappings`, {
        method: "POST",
        body: JSON.stringify(add),
      });
      setAdding(null);
      load(q);
    } catch (e) {
      setErr(`${e}`);
    }
  }

  return (
    <div>
      {/* 툴바 */}
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            load(q);
          }}
          className="flex gap-2"
        >
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="내부코드·상품명 검색"
            className="px-3 py-2 text-sm border rounded-md w-56"
          />
          <button className="px-3 py-2 text-sm bg-gray-100 text-gray-700 rounded-md hover:bg-gray-200">
            검색
          </button>
          {q && (
            <button
              type="button"
              onClick={() => {
                setQ("");
                load("");
              }}
              className="px-2 py-2 text-sm text-gray-500 hover:underline"
            >
              초기화
            </button>
          )}
        </form>
        <div className="flex gap-2">
          <a
            href={downloadUrl("/api/products/mapping-template")}
            className="px-3 py-2 text-sm bg-purple-600 text-white rounded-md hover:bg-purple-700"
          >
            매핑 템플릿
          </a>
          <label
            className="px-3 py-2 text-sm bg-orange-500 text-white rounded-md hover:bg-orange-600 cursor-pointer"
            title='마스터 엑셀("원가 매핑" 시트, 1행=옵션 1개, 채널별 옵션ID 컬럼 포맷)'
          >
            연관맵 마스터 업로드
            <input
              ref={mappingFileRef}
              type="file"
              accept=".xlsx,.xls"
              className="hidden"
              onChange={handleMappingUpload}
            />
          </label>
        </div>
      </div>

      {uploadMsg && (
        <div className="mb-3 p-3 bg-blue-50 text-blue-800 rounded-md text-sm">
          {uploadMsg}
          <button onClick={() => setUploadMsg("")} className="ml-2 text-blue-600 underline">
            닫기
          </button>
        </div>
      )}
      {err && (
        <div className="mb-3 p-3 bg-red-50 text-red-800 rounded-md text-sm">
          {err}
          <button onClick={() => setErr("")} className="ml-2 text-red-600 underline">
            닫기
          </button>
        </div>
      )}

      {/* 요약 */}
      {map && (
        <div className="mb-3 text-sm text-gray-600">
          내부옵션 <b>{map.shown_products}</b>
          {map.total_products !== map.shown_products && ` / ${map.total_products}`}개 표시 · 채널옵션ID 충돌{" "}
          <b className={map.conflict_option_count ? "text-red-600" : "text-gray-700"}>
            {map.conflict_option_count}
          </b>
          건
        </div>
      )}

      {/* 매트릭스 */}
      <div className="bg-white rounded-lg border overflow-x-auto">
        {loading || !map ? (
          <div className="p-8 text-center text-gray-400 text-sm">
            {loading ? "불러오는 중…" : "데이터 없음"}
          </div>
        ) : (
          <table className="min-w-full text-xs">
            <thead>
              <tr className="border-b bg-gray-50">
                <th className="text-left px-3 py-2 font-medium text-gray-600 sticky left-0 bg-gray-50">
                  내부코드
                </th>
                <th className="text-left px-3 py-2 font-medium text-gray-600">상품명</th>
                <th className="text-right px-3 py-2 font-medium text-gray-600">원가</th>
                {map.channels.map((ch) => {
                  const cov = covByChannel.get(ch.channel_id);
                  return (
                    <th key={ch.channel_id} className="text-left px-3 py-2 font-medium text-gray-600 min-w-[9rem]">
                      <div className="flex items-center gap-1">
                        {ch.channel_name}
                        {ch.sell_type && (
                          <span className="text-[10px] bg-gray-200 text-gray-600 px-1 rounded">
                            {ch.sell_type}
                          </span>
                        )}
                      </div>
                      {cov && (
                        <div className="font-normal text-[10px] text-gray-400 mt-0.5">
                          매핑 {cov.mapped_option_count} · 미매핑{" "}
                          <span className={cov.unmapped_order_options.length ? "text-amber-600" : ""}>
                            {cov.unmapped_order_options.length + cov.unmapped_order_options_truncated}
                          </span>
                        </div>
                      )}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {map.rows.map((r) => (
                <tr key={r.product_id} className="border-b hover:bg-gray-50 align-top">
                  <td className="px-3 py-2 font-mono text-gray-700 sticky left-0 bg-white">
                    {r.internal_sku}
                    {r.has_conflict && <span className="ml-1 text-red-500" title="충돌 포함">⚠</span>}
                  </td>
                  <td className="px-3 py-2">{r.product_name}</td>
                  <td className="px-3 py-2 text-right text-gray-500">{fmt(r.cost_price)}</td>
                  {map.channels.map((ch) => {
                    const cells = r.cells[String(ch.channel_id)] || [];
                    return (
                      <td key={ch.channel_id} className="px-3 py-2">
                        <div className="flex flex-col gap-1">
                          {cells.map((cell) =>
                            editing &&
                            editing.mapping.mapping_id === cell.mapping_id ? (
                              <CellEditor
                                key={cell.mapping_id}
                                cell={cell}
                                onSave={saveEdit}
                                onCancel={() => setEditing(null)}
                                onDelete={() => deleteMapping(r.product_id, cell.mapping_id)}
                              />
                            ) : (
                              <CellView
                                key={cell.mapping_id}
                                cell={cell}
                                onClick={() => {
                                  setAdding(null);
                                  setEditing({ productId: r.product_id, mapping: cell });
                                }}
                              />
                            ),
                          )}
                          {adding &&
                          adding.productId === r.product_id &&
                          adding.channelId === ch.channel_id ? (
                            <AddEditor
                              onSave={(v) => addMapping({ channel_id: ch.channel_id, ...v })}
                              onCancel={() => setAdding(null)}
                            />
                          ) : (
                            <button
                              onClick={() => {
                                setEditing(null);
                                setAdding({ productId: r.product_id, channelId: ch.channel_id });
                              }}
                              className="text-[11px] text-gray-300 hover:text-blue-500 self-start"
                              title="매핑 추가"
                            >
                              +
                            </button>
                          )}
                        </div>
                      </td>
                    );
                  })}
                </tr>
              ))}
              {map.rows.length === 0 && (
                <tr>
                  <td colSpan={3 + map.channels.length} className="px-4 py-8 text-center text-gray-400">
                    표시할 옵션이 없습니다.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function CellView({ cell, onClick }: { cell: ConnCell; onClick: () => void }) {
  const sourceTag =
    cell.mapping_source === "excel_master"
      ? { t: "엑셀", c: "bg-green-100 text-green-700" }
      : cell.mapping_source === "manual"
      ? { t: "수동", c: "bg-blue-100 text-blue-700" }
      : { t: "자동", c: "bg-gray-100 text-gray-500" };
  return (
    <button
      onClick={onClick}
      className={`text-left px-1.5 py-1 rounded border text-[11px] hover:bg-gray-50 ${
        cell.conflict
          ? "border-red-300 bg-red-50"
          : cell.is_active
          ? "border-gray-200"
          : "border-gray-100 opacity-50"
      }`}
      title={`${cell.channel_product_name || ""} · ${fmt(cell.selling_price)}원`}
    >
      <span className="font-mono">{cell.channel_product_id}</span>
      <span className={`ml-1 text-[9px] px-1 rounded ${sourceTag.c}`}>{sourceTag.t}</span>
      {cell.conflict && <span className="ml-1 text-red-600">⚠충돌</span>}
      {!cell.is_active && <span className="ml-1 text-gray-400">(비활성)</span>}
    </button>
  );
}

function CellEditor({
  cell,
  onSave,
  onCancel,
  onDelete,
}: {
  cell: ConnCell;
  onSave: (p: { channel_product_id: string; selling_price: number; is_active: boolean }) => void;
  onCancel: () => void;
  onDelete: () => void;
}) {
  const [cpid, setCpid] = useState(cell.channel_product_id);
  const [price, setPrice] = useState(String(cell.selling_price));
  const [active, setActive] = useState(cell.is_active);
  return (
    <div className="flex flex-col gap-1 p-1.5 border border-blue-300 rounded bg-blue-50 min-w-[8rem]">
      <input
        value={cpid}
        onChange={(e) => setCpid(e.target.value)}
        placeholder="옵션ID"
        className="px-1 py-0.5 text-[11px] border rounded font-mono"
      />
      <input
        value={price}
        onChange={(e) => setPrice(e.target.value)}
        placeholder="판매가"
        inputMode="numeric"
        className="px-1 py-0.5 text-[11px] border rounded text-right"
      />
      <label className="flex items-center gap-1 text-[11px] text-gray-600">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
        활성
      </label>
      <div className="flex gap-1">
        <button
          onClick={() =>
            onSave({ channel_product_id: cpid.trim(), selling_price: Number(price) || 0, is_active: active })
          }
          className="flex-1 text-[11px] bg-blue-600 text-white rounded px-1 py-0.5 hover:bg-blue-700"
        >
          저장
        </button>
        <button
          onClick={onCancel}
          className="text-[11px] bg-gray-200 text-gray-600 rounded px-1 py-0.5 hover:bg-gray-300"
        >
          취소
        </button>
        <button
          onClick={onDelete}
          className="text-[11px] text-red-600 rounded px-1 py-0.5 hover:bg-red-100"
        >
          삭제
        </button>
      </div>
    </div>
  );
}

function AddEditor({
  onSave,
  onCancel,
}: {
  onSave: (v: { channel_product_id: string; selling_price: number }) => void;
  onCancel: () => void;
}) {
  const [cpid, setCpid] = useState("");
  const [price, setPrice] = useState("");
  return (
    <div className="flex flex-col gap-1 p-1.5 border border-green-300 rounded bg-green-50 min-w-[8rem]">
      <input
        value={cpid}
        onChange={(e) => setCpid(e.target.value)}
        placeholder="새 옵션ID"
        className="px-1 py-0.5 text-[11px] border rounded font-mono"
        autoFocus
      />
      <input
        value={price}
        onChange={(e) => setPrice(e.target.value)}
        placeholder="판매가"
        inputMode="numeric"
        className="px-1 py-0.5 text-[11px] border rounded text-right"
      />
      <div className="flex gap-1">
        <button
          disabled={!cpid.trim()}
          onClick={() => onSave({ channel_product_id: cpid.trim(), selling_price: Number(price) || 0 })}
          className="flex-1 text-[11px] bg-green-600 text-white rounded px-1 py-0.5 hover:bg-green-700 disabled:opacity-50"
        >
          추가
        </button>
        <button
          onClick={onCancel}
          className="text-[11px] bg-gray-200 text-gray-600 rounded px-1 py-0.5 hover:bg-gray-300"
        >
          취소
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// 탭2: 통합 손익 (S5, D-12 — GET /api/products/pnl-reconciliation)
// ─────────────────────────────────────────────────────────────
function PnlTab() {
  const today = isoKST(new Date());
  const ago = (n: number) => {
    const d = new Date();
    d.setDate(d.getDate() - (n - 1));
    return isoKST(d);
  };

  const [from, setFrom] = useState(ago(7));
  const [to, setTo] = useState(today);
  const [account, setAccount] = useState("");
  const [data, setData] = useState<PnlReconciliation | null>(null);
  const [skuNames, setSkuNames] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [ledgerOpen, setLedgerOpen] = useState(false);

  const load = useCallback(async (f: string, t: string, acc: string) => {
    setLoading(true);
    setErr("");
    try {
      const result = await fetchPnlReconciliation(f, t, acc || undefined);
      setData(result);
      setLedgerOpen(!result.summary.trustworthy);
      // 상품명 조인 — 손익 숫자 표시를 막지 않도록 실패는 무시(원칙: degrade gracefully).
      try {
        const map = await fetchConnectionMap();
        const names: Record<string, string> = {};
        for (const r of map.rows) names[r.internal_sku] = r.product_name;
        setSkuNames(names);
      } catch {
        setSkuNames({});
      }
    } catch (e) {
      setErr(`불러오기 실패: ${e}`);
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(from, to, account);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function applyFilters(f: string, t: string, acc: string) {
    setFrom(f);
    setTo(t);
    setAccount(acc);
    load(f, t, acc);
  }

  return (
    <div>
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <input
          type="date"
          value={from}
          onChange={(e) => applyFilters(e.target.value, to, account)}
          className="px-2 py-1 text-sm border border-gray-300 rounded-md"
        />
        <span className="text-gray-400">~</span>
        <input
          type="date"
          value={to}
          onChange={(e) => applyFilters(from, e.target.value, account)}
          className="px-2 py-1 text-sm border border-gray-300 rounded-md"
        />
        <select
          value={account}
          onChange={(e) => applyFilters(from, to, e.target.value)}
          className="px-2 py-1 text-sm border border-gray-300 rounded-md"
        >
          {PNL_ACCOUNTS.map((a) => (
            <option key={a.value} value={a.value}>
              {a.label}
            </option>
          ))}
        </select>
      </div>
      {account && (
        <div className="mb-3 text-xs text-gray-500">
          계정 선택 시 네이버·자사몰 손익은 제외됩니다(계정 단위는 쿠팡만 대조).
        </div>
      )}

      {err && (
        <div className="mb-3 p-3 bg-red-50 text-red-800 rounded-md text-sm">
          {err}
          <button onClick={() => setErr("")} className="ml-2 text-red-600 underline">
            닫기
          </button>
        </div>
      )}

      {loading && <div className="p-8 text-center text-gray-400 text-sm">불러오는 중…</div>}

      {!loading && data && (
        <>
          {!data.summary.trustworthy && (
            <div className="mb-3 p-3 bg-amber-50 text-amber-800 rounded-md text-sm">
              ⚠️ 원장 불균형 — SKU 손익 표시 불가. 아래 대조원장에서 diff≠0 컴포넌트를 확인하세요.
            </div>
          )}

          <PnlSummaryCards summary={data.summary} />

          {data.summary.trustworthy && (
            <PnlSkuTable
              rows={data.by_sku}
              names={skuNames}
              expanded={expanded}
              onToggle={(sku) => setExpanded(expanded === sku ? null : sku)}
            />
          )}

          <PnlLedgerPanel
            ledger={data.ledger}
            open={ledgerOpen}
            onToggle={() => setLedgerOpen(!ledgerOpen)}
          />
        </>
      )}
    </div>
  );
}
