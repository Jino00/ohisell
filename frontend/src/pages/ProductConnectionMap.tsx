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

  // 미매핑 옵션ID 목록 펼침 + "이 옵션을 연결" 대상 선택(온라인 매핑 보강용)
  const [openCoverage, setOpenCoverage] = useState<number | null>(null);
  const [pendingAttach, setPendingAttach] = useState<
    { channelId: number; optionId: string; orderCount: number } | null
  >(null);

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
      setPendingAttach(null);
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

      {/* 미매핑 옵션ID 펼침 패널 — 채널 헤더의 "미매핑 N" 클릭 시 표시 */}
      {openCoverage !== null &&
        (() => {
          const cov = covByChannel.get(openCoverage);
          const ch = map?.channels.find((c) => c.channel_id === openCoverage);
          if (!cov || !ch) return null;
          return (
            <div className="mb-3 p-3 bg-amber-50 border border-amber-200 rounded-md text-sm">
              <div className="flex items-center justify-between mb-2">
                <span className="font-medium text-amber-900">
                  {ch.channel_name} 미매핑 옵션ID
                </span>
                <button onClick={() => setOpenCoverage(null)} className="text-amber-700 underline text-xs">
                  닫기
                </button>
              </div>
              {cov.unmapped_order_options.length === 0 ? (
                <div className="text-gray-500 text-xs">미매핑 옵션이 없습니다.</div>
              ) : (
                <ul className="space-y-1">
                  {cov.unmapped_order_options.map((opt) => (
                    <li
                      key={opt.option_id}
                      className="flex items-center justify-between gap-2 bg-white rounded px-2 py-1 border"
                    >
                      <span className="font-mono text-xs">{opt.option_id}</span>
                      <span className="text-[11px] text-gray-500">주문 {opt.order_count}건</span>
                      <button
                        onClick={() => {
                          setPendingAttach({
                            channelId: openCoverage,
                            optionId: opt.option_id,
                            orderCount: opt.order_count,
                          });
                          setOpenCoverage(null);
                        }}
                        className="text-[11px] bg-amber-600 text-white rounded px-2 py-0.5 hover:bg-amber-700"
                      >
                        연결할 상품 찾기
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {cov.unmapped_order_options_truncated > 0 && (
                <div className="mt-2 text-[11px] text-gray-500">
                  그 외 {cov.unmapped_order_options_truncated}건 더 있음(표시 생략)
                </div>
              )}
            </div>
          );
        })()}

      {/* 옵션ID 연결 대상 선택 안내 */}
      {pendingAttach && (
        <div className="mb-3 p-3 bg-green-50 border border-green-300 rounded-md text-sm text-green-800 flex flex-wrap items-center justify-between gap-2">
          <span>
            옵션 <b className="font-mono">{pendingAttach.optionId}</b>(주문 {pendingAttach.orderCount}건)을
            연결할 상품을 아래 표에서 찾아 해당 채널 열의{" "}
            <b>"↳ 이 옵션 연결"</b> 버튼을 누르세요. 위 검색창으로 상품명·내부코드를 좁혀보세요.
          </span>
          <button onClick={() => setPendingAttach(null)} className="text-green-700 underline text-xs">
            취소
          </button>
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
                          {cov.unmapped_order_options.length + cov.unmapped_order_options_truncated > 0 ? (
                            <button
                              onClick={() =>
                                setOpenCoverage(openCoverage === ch.channel_id ? null : ch.channel_id)
                              }
                              className="text-amber-600 underline hover:text-amber-800"
                              title="미매핑 옵션ID 목록 보기"
                            >
                              {cov.unmapped_order_options.length + cov.unmapped_order_options_truncated}
                            </button>
                          ) : (
                            <span>0</span>
                          )}
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
                              initialOptionId={
                                pendingAttach && pendingAttach.channelId === ch.channel_id
                                  ? pendingAttach.optionId
                                  : undefined
                              }
                              onSave={(v) => addMapping({ channel_id: ch.channel_id, ...v })}
                              onCancel={() => setAdding(null)}
                            />
                          ) : pendingAttach && pendingAttach.channelId === ch.channel_id ? (
                            <button
                              onClick={() => {
                                setEditing(null);
                                setAdding({ productId: r.product_id, channelId: ch.channel_id });
                              }}
                              className="text-[11px] text-green-700 bg-green-100 hover:bg-green-200 rounded px-1.5 py-0.5 self-start font-medium"
                              title={`옵션 ${pendingAttach.optionId} 연결`}
                            >
                              ↳ 이 옵션 연결
                            </button>
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
  initialOptionId,
  onSave,
  onCancel,
}: {
  initialOptionId?: string;
  onSave: (v: { channel_product_id: string; selling_price: number }) => void;
  onCancel: () => void;
}) {
  const [cpid, setCpid] = useState(initialOptionId || "");
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

function PnlSummaryCards({
  summary,
}: {
  summary: PnlReconciliation["summary"];
}) {
  const residual = Number(summary.account_adjustment_residual);
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
      <div className="bg-white rounded-lg border p-4">
        <div className="text-xs text-gray-500 mb-1">계정 순익(권위)</div>
        <div className="text-xl font-bold text-gray-900">{won(summary.reconciled_net_profit)}</div>
      </div>
      <div className="bg-white rounded-lg border p-4">
        <div className="text-xs text-gray-500 mb-1">SKU 귀속 순익 합</div>
        <div className="text-xl font-bold text-gray-900">{won(summary.net_profit_allocated_total)}</div>
      </div>
      <div
        className="bg-white rounded-lg border p-4"
        title="미매핑 옵션 + 계정 단위 조정(RG 플립·비-PA 광고·정산 매출조정 등). 안분하지 않음."
      >
        <div className="text-xs text-gray-500 mb-1">미배분 잔차</div>
        <div className={`text-xl font-bold ${residual !== 0 ? "text-amber-600" : "text-gray-900"}`}>
          {won(summary.account_adjustment_residual)}
        </div>
      </div>
    </div>
  );
}

function PnlSkuTable({
  rows,
  names,
  expanded,
  onToggle,
}: {
  rows: PnlSkuRow[];
  names: Record<string, string>;
  expanded: string | null;
  onToggle: (sku: string) => void;
}) {
  if (rows.length === 0) {
    return (
      <div className="bg-white rounded-lg border p-8 text-center text-gray-400 text-sm mb-4">
        표시할 SKU 손익이 없습니다.
      </div>
    );
  }
  return (
    <div className="bg-white rounded-lg border overflow-x-auto mb-4">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="border-b bg-gray-50">
            <th className="text-left px-3 py-2 font-medium text-gray-600">상품명</th>
            <th className="text-left px-3 py-2 font-medium text-gray-600">내부코드</th>
            <th className="text-right px-3 py-2 font-medium text-gray-600">순익(SKU 귀속)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const net = Number(r.net_profit_allocated_only);
            const isOpen = expanded === r.internal_sku;
            return (
              <tr key={r.internal_sku}>
                <td colSpan={3} className="p-0">
                  <button
                    onClick={() => onToggle(r.internal_sku)}
                    className="w-full flex border-b hover:bg-gray-50 text-left"
                  >
                    <div className="px-3 py-2 flex-1">{names[r.internal_sku] || r.internal_sku}</div>
                    <div className="px-3 py-2 font-mono text-gray-500 w-40">{r.internal_sku}</div>
                    <div
                      className={`px-3 py-2 text-right font-medium w-40 ${
                        net < 0 ? "text-red-600" : "text-gray-900"
                      }`}
                    >
                      {won(r.net_profit_allocated_only)}
                    </div>
                  </button>
                  {isOpen && (
                    <div className="px-3 py-2 bg-gray-50 border-b">
                      <table className="min-w-full text-xs">
                        <thead>
                          <tr className="text-gray-500">
                            <th className="text-left py-1 pr-3">채널</th>
                            <th className="text-left py-1 pr-3">컴포넌트</th>
                            <th className="text-right py-1">금액</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(r.channels).flatMap(([channel, comps]) =>
                            Object.entries(comps).map(([comp, amt]) => (
                              <tr key={`${channel}-${comp}`}>
                                <td className="py-0.5 pr-3 text-gray-600">{channel}</td>
                                <td className="py-0.5 pr-3 text-gray-600">{comp}</td>
                                <td className="py-0.5 text-right">{won(amt)}</td>
                              </tr>
                            )),
                          )}
                        </tbody>
                      </table>
                    </div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PnlLedgerPanel({
  ledger,
  open,
  onToggle,
}: {
  ledger: PnlReconciliation["ledger"];
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="bg-white rounded-lg border">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium text-gray-700 hover:bg-gray-50"
      >
        <span>
          대조원장 상세 {open ? "접기" : "보기"}
          <span
            className={`ml-2 text-xs px-2 py-0.5 rounded ${
              ledger.conservation_ok ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"
            }`}
          >
            {ledger.conservation_ok ? "균형" : "불균형"}
          </span>
        </span>
        <span className="text-gray-400">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="border-t px-4 py-3">
          <div className="overflow-x-auto">
            <table className="min-w-full text-xs mb-3">
              <thead>
                <tr className="text-gray-500">
                  <th className="text-left py-1 pr-3">채널</th>
                  <th className="text-left py-1 pr-3">컴포넌트</th>
                  <th className="text-right py-1 pr-3">권위 총액</th>
                  <th className="text-right py-1 pr-3">SKU 귀속</th>
                  <th className="text-right py-1 pr-3">잔차 합</th>
                  <th className="text-right py-1">diff</th>
                </tr>
              </thead>
              <tbody>
                {ledger.components.map((c, i) => {
                  const residualSum = Object.values(c.residuals).reduce(
                    (a, v) => a + Number(v),
                    0,
                  );
                  const diffNonZero = !c.conservation_ok;
                  return (
                    <tr key={i} className={diffNonZero ? "bg-red-50" : ""}>
                      <td className="py-0.5 pr-3">{c.channel}</td>
                      <td className="py-0.5 pr-3">{c.component}</td>
                      <td className="py-0.5 pr-3 text-right">{won(c.authoritative_total)}</td>
                      <td className="py-0.5 pr-3 text-right">{won(c.allocated_to_sku)}</td>
                      <td className="py-0.5 pr-3 text-right">{won(String(residualSum))}</td>
                      <td className={`py-0.5 text-right ${diffNonZero ? "text-red-600 font-medium" : ""}`}>
                        {won(c.conservation_diff)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {ledger.sku_conflicts.length > 0 && (
            <div className="mb-2 text-xs">
              <span className="text-red-600 font-medium">채널옵션ID 충돌 {ledger.sku_conflicts.length}건: </span>
              <span className="text-gray-600 font-mono">{ledger.sku_conflicts.join(", ")}</span>
            </div>
          )}

          {ledger.warnings.length > 0 && (
            <div className="text-xs text-amber-700">
              {ledger.warnings.map((w, i) => (
                <div key={i}>⚠ {JSON.stringify(w)}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
