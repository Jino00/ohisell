// ImportCostPage.tsx — 수입건 원장(landed cost) 화면 (D-CPP-48)
//   목록 → 상세(검산 3종·미배분 잔액·인보이스/비용 라인·배부기준 비교·원본 서류·확정) → 수기 입력폼.
//   백엔드는 이미 구현돼 있다(app/routers/import_cost.py) — 이 파일은 그 위에 화면만 얹는다.
//   ★검산 판정 어휘(ok/mismatch/missing)는 절대 뭉개지 않는다 — missing을 "통과"로 그리면
//     「발견 0건」과 「실행 안 됨」이 같은 숫자로 보인다(원칙: 교훈 #123과 같은 결).
import { useState, useEffect, useCallback, useRef } from "react";
import {
  fetchImportShipments,
  fetchImportShipment,
  createImportShipment,
  updateImportShipment,
  deleteImportShipment,
  confirmImportShipment,
  reopenImportShipment,
  fetchImportBasisComparison,
  uploadImportDocument,
  importDocumentDownloadUrl,
  deleteImportDocument,
  parseImportDocuments,
  type ImportShipmentListItem,
  type ImportShipmentDetail,
  type ImportShipmentInput,
  type ImportCostLine,
  type ImportInvoiceLine,
  type ImportPackingLine,
  type ImportReconcile,
  type ImportReconcileCheck,
  type ImportConfirmResult,
  type ImportBasisComparison,
  type ImportAllocationBasis,
  type ImportAllocation,
  type ImportDocType,
  type ImportLineType,
  type ImportParseResult,
} from "../lib/api";

// ── 공용 포맷터 (ProductConnectionMap.tsx `won()` 규약과 동일 — 없는 값은 "—") ──
const fmt = (n: number) => new Intl.NumberFormat("ko-KR").format(n);
const won = (s: string | null | undefined) =>
  s == null ? "—" : `${fmt(Math.round(Number(s)))}원`;
const numStr = (s: string | null | undefined, digits = 0) =>
  s == null || s === "" ? "—" : Number(s).toLocaleString("ko-KR", { maximumFractionDigits: digits });

// ── 관세율 퍼센트⇄소수 변환 (D-CPP-50) ──
//   사람은 "5.6"(%)을 입력하고 API엔 "0.056"(소수)이 가야 한다. Number 왕복만 쓰면 부동소수 잡음이
//   낀다(예: 5.6/100 → 0.05600000000000001) — toFixed(10)로 자리를 넉넉히 잡고 끝 0을 잘라낸다.
function pctToRateStr(pct: string): string | null {
  const t = pct.trim();
  if (t === "") return null;
  const n = Number(t);
  if (!Number.isFinite(n)) return null;
  let s = (n / 100).toFixed(10).replace(/0+$/, "").replace(/\.$/, "");
  if (s === "" || s === "-") s = "0";
  return s;
}
function rateToPctStr(rate: string | null | undefined): string {
  if (rate == null || rate === "") return "";
  const n = Number(rate);
  if (!Number.isFinite(n)) return "";
  return (n * 100).toFixed(10).replace(/0+$/, "").replace(/\.$/, "");
}
// 관세율(%) 입력 칸 — 커밋된 소수(rate)로부터 매 타이핑마다 재포맷하면 "5." 같은 중간 입력이
// 지워진다. lastEmitted로 "내가 방금 낸 변경"과 "외부에서 바뀐 값"을 구분해 전자는 재포맷하지 않는다.
function PercentInput({
  rate,
  onChange,
}: {
  rate: string | null | undefined;
  onChange: (rate: string | null) => void;
}) {
  const [text, setText] = useState<string>(() => rateToPctStr(rate));
  const lastEmitted = useRef<string | null | undefined>(rate);

  useEffect(() => {
    if (rate === lastEmitted.current) return;
    lastEmitted.current = rate;
    setText(rateToPctStr(rate));
  }, [rate]);

  return (
    <input
      type="text"
      inputMode="decimal"
      value={text}
      onChange={(e) => {
        const v = e.target.value;
        setText(v);
        const next = pctToRateStr(v);
        lastEmitted.current = next;
        onChange(next);
      }}
      placeholder="예: 5.6"
      className="w-full border rounded px-1.5 py-1 text-xs text-right"
    />
  );
}

const BASIS_LABEL: Record<ImportAllocationBasis, string> = {
  amount: "금액",
  weight: "중량",
  volume: "부피",
  quantity: "수량",
};
const LINE_TYPE_LABEL: Record<ImportLineType, string> = {
  product: "판매SKU",
  material: "부자재",
  unknown: "미분류",
};
const LINE_TYPE_CLASS: Record<ImportLineType, string> = {
  product: "bg-blue-100 text-blue-700",
  material: "bg-purple-100 text-purple-700",
  unknown: "bg-gray-100 text-gray-600",
};
const DOC_TYPE_LABEL: Record<ImportDocType, string> = {
  ci: "상업송장(CI)",
  pl: "포장명세서(PL)",
  expense: "통관경비서",
  etc: "기타",
};

function StatusBadge({ status }: { status: "draft" | "confirmed" }) {
  return status === "confirmed" ? (
    <span className="bg-green-100 text-green-700 px-2 py-0.5 rounded-full text-xs">확정</span>
  ) : (
    <span className="bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full text-xs">작성중</span>
  );
}

// ─────────────────────────────────────────────────────────────
// 최상위 — 목록/상세/폼 3뷰를 페이지 안에서 전환한다(이 저장소는 useParams를 쓰지 않는다 —
// Products.tsx·ProductConnectionMap.tsx와 같은 관습, 상세도 라우트가 아니라 화면 상태다).
// ─────────────────────────────────────────────────────────────
type View = { name: "list" } | { name: "detail"; id: number } | { name: "form"; id: number | null };

export default function ImportCostPage() {
  const [view, setView] = useState<View>({ name: "list" });

  return (
    <div>
      {view.name === "list" && (
        <ShipmentListView
          onOpen={(id) => setView({ name: "detail", id })}
          onCreate={() => setView({ name: "form", id: null })}
        />
      )}
      {view.name === "detail" && (
        <ShipmentDetailView
          id={view.id}
          onBack={() => setView({ name: "list" })}
          onEdit={(id) => setView({ name: "form", id })}
          onDeleted={() => setView({ name: "list" })}
        />
      )}
      {view.name === "form" && (
        <ShipmentFormView
          id={view.id}
          onDone={(id) => setView({ name: "detail", id })}
          onCancel={() => setView(view.id != null ? { name: "detail", id: view.id } : { name: "list" })}
        />
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// 목록
// ─────────────────────────────────────────────────────────────
function ShipmentListView({ onOpen, onCreate }: { onOpen: (id: number) => void; onCreate: () => void }) {
  const [items, setItems] = useState<ImportShipmentListItem[]>([]);
  const [statusFilter, setStatusFilter] = useState<"" | "draft" | "confirmed">("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const res = await fetchImportShipments({ limit: 200, status: statusFilter || undefined });
      setItems(res.items);
    } catch (e) {
      setErr(`불러오기 실패: ${e}`);
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const FILTERS: { value: "" | "draft" | "confirmed"; label: string }[] = [
    { value: "", label: "전체" },
    { value: "draft", label: "작성중" },
    { value: "confirmed", label: "확정" },
  ];

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-2xl font-bold text-gray-900">📥 수입건 원장</h2>
        <button
          onClick={onCreate}
          className="px-3 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700"
        >
          + 수입건 등록
        </button>
      </div>

      <div className="flex gap-1 mb-3">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            onClick={() => setStatusFilter(f.value)}
            className={`px-2.5 py-1 rounded text-xs font-medium ${
              statusFilter === f.value
                ? "bg-gray-800 text-white"
                : "bg-white border border-gray-300 text-gray-600 hover:bg-gray-100"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {err && <div className="mb-3 p-3 bg-red-50 text-red-700 rounded-md text-sm">{err}</div>}

      <div className="bg-white rounded-lg border">
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b bg-gray-50">
                <th className="text-left px-4 py-3 font-medium text-gray-600">HBL</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">신고일</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Shipper</th>
                <th className="text-right px-4 py-3 font-medium text-gray-600">통화·환율</th>
                <th className="text-center px-4 py-3 font-medium text-gray-600">라인수</th>
                <th className="text-center px-4 py-3 font-medium text-gray-600">상태</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">확정일시</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-gray-400">
                    로딩 중…
                  </td>
                </tr>
              )}
              {!loading &&
                items.map((s) => (
                  <tr
                    key={s.id}
                    className="border-b hover:bg-gray-50 cursor-pointer"
                    onClick={() => onOpen(s.id)}
                  >
                    <td className="px-4 py-3 font-mono text-gray-700">{s.hbl_no}</td>
                    <td className="px-4 py-3 text-gray-600">{s.declaration_date ?? "—"}</td>
                    <td className="px-4 py-3">{s.shipper_name ?? "—"}</td>
                    <td className="px-4 py-3 text-right">
                      {s.currency} {numStr(s.fx_rate, 4)}
                    </td>
                    <td className="px-4 py-3 text-center">{s.line_count}</td>
                    <td className="px-4 py-3 text-center">
                      <StatusBadge status={s.status} />
                    </td>
                    <td className="px-4 py-3 text-gray-500">{s.confirmed_at ?? "—"}</td>
                  </tr>
                ))}
              {!loading && items.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-gray-400">
                    등록된 수입건이 없습니다.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// 상세
// ─────────────────────────────────────────────────────────────
function ShipmentDetailView({
  id,
  onBack,
  onEdit,
  onDeleted,
}: {
  id: number;
  onBack: () => void;
  onEdit: (id: number) => void;
  onDeleted: () => void;
}) {
  const [ship, setShip] = useState<ImportShipmentDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmResult, setConfirmResult] = useState<ImportConfirmResult | null>(null);

  const [basis, setBasis] = useState<ImportBasisComparison | null>(null);
  const [basisOpen, setBasisOpen] = useState(false);
  const [basisLoading, setBasisLoading] = useState(false);

  const [docType, setDocType] = useState<ImportDocType>("ci");
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const s = await fetchImportShipment(id);
      setShip(s);
    } catch (e) {
      setErr(`불러오기 실패: ${e}`);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
    setConfirmResult(null);
    setBasis(null);
    setBasisOpen(false);
  }, [load]);

  async function loadBasis() {
    if (basis) {
      setBasisOpen((o) => !o);
      return;
    }
    setBasisLoading(true);
    try {
      const b = await fetchImportBasisComparison(id);
      setBasis(b);
      setBasisOpen(true);
    } catch (e) {
      setErr(`배부기준 비교 실패: ${e}`);
    } finally {
      setBasisLoading(false);
    }
  }

  async function handleConfirm() {
    setBusy(true);
    setErr("");
    try {
      const res = await confirmImportShipment(id);
      setConfirmResult(res.confirm_result);
      setShip(res);
    } catch (e) {
      setErr(`확정 실패: ${e}`);
    } finally {
      setBusy(false);
    }
  }

  async function handleReopen() {
    if (!confirm("확정을 해제하시겠습니까? 계산된 단가가 지워집니다.")) return;
    setBusy(true);
    setErr("");
    try {
      const s = await reopenImportShipment(id);
      setShip(s);
      setConfirmResult(null);
    } catch (e) {
      setErr(`확정 해제 실패: ${e}`);
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!confirm("이 수입건을 삭제하시겠습니까?")) return;
    setBusy(true);
    setErr("");
    try {
      await deleteImportShipment(id);
      onDeleted();
    } catch (e) {
      setErr(`삭제 실패: ${e}`);
      setBusy(false);
    }
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      await uploadImportDocument(id, docType, file);
      load();
    } catch (e) {
      setErr(`업로드 실패: ${e}`);
    }
    if (fileRef.current) fileRef.current.value = "";
  }

  async function handleDeleteDoc(docId: number) {
    if (!confirm("이 서류를 삭제하시겠습니까?")) return;
    try {
      await deleteImportDocument(id, docId);
      load();
    } catch (e) {
      setErr(`삭제 실패: ${e}`);
    }
  }

  if (loading && !ship) return <div className="text-center text-gray-400 py-12">로딩 중…</div>;
  if (err && !ship)
    return <div className="p-3 bg-red-50 text-red-700 rounded-md text-sm">{err}</div>;
  if (!ship) return null;

  // ★확정 시도 직후엔 그 시도의 검산 리포트를 우선 보인다(성공이든 실패든) — ship.reconcile은
  //   재조회 전까지 옛 값일 수 있어서다. `confirm` 응답의 reconcile은 이번 시도의 것이다.
  const reconcile: ImportReconcile = confirmResult ? confirmResult.reconcile : ship.reconcile;
  const unallocated = ship.allocation?.unallocated_krw ?? null;

  return (
    <div>
      <button onClick={onBack} className="text-sm text-blue-600 hover:underline mb-3">
        ← 목록으로
      </button>

      {err && <div className="mb-3 p-3 bg-red-50 text-red-700 rounded-md text-sm">{err}</div>}

      {confirmResult && !confirmResult.confirmed && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded-md text-sm">
          확정 실패 — {confirmResult.reason || "검산 미통과"}. 아래 검산 패널에서 사유를 확인하세요.
        </div>
      )}
      {confirmResult && confirmResult.confirmed && (
        <div className="mb-4 p-3 bg-green-50 border border-green-200 text-green-700 rounded-md text-sm">
          확정되었습니다. 인보이스 라인에 배부 단가가 저장됐습니다.
        </div>
      )}

      <div className="flex items-start justify-between mb-4 flex-wrap gap-2">
        <div>
          <h2 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            {ship.hbl_no} <StatusBadge status={ship.status} />
          </h2>
          <p className="text-sm text-gray-500 mt-1">
            {ship.shipper_name ?? "—"} · {ship.currency} {numStr(ship.fx_rate, 4)}
            {ship.declaration_date && ` · 신고일 ${ship.declaration_date}`}
            {ship.confirmed_at && ` · 확정 ${ship.confirmed_at}`}
          </p>
        </div>
        <div className="flex gap-2">
          {ship.status === "draft" ? (
            <>
              <button
                onClick={() => onEdit(ship.id)}
                className="px-3 py-2 text-sm bg-gray-100 text-gray-700 rounded-md hover:bg-gray-200"
              >
                수정
              </button>
              <button onClick={handleDelete} disabled={busy} className="px-3 py-2 text-sm text-red-600 hover:underline disabled:opacity-50">
                삭제
              </button>
              <button
                onClick={handleConfirm}
                disabled={busy}
                className="px-3 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
              >
                확정
              </button>
            </>
          ) : (
            <button
              onClick={handleReopen}
              disabled={busy}
              className="px-3 py-2 text-sm bg-amber-500 text-white rounded-md hover:bg-amber-600 disabled:opacity-50"
            >
              확정 해제
            </button>
          )}
        </div>
      </div>

      <ReconcilePanel reconcile={reconcile} />
      <UnallocatedPanel unallocated={unallocated} allocationError={ship.allocation_error} />
      <DutyCheckPanel allocation={ship.allocation} />
      <InvoiceLinesTable lines={ship.invoice_lines} allocation={ship.allocation} />
      <CostLinesTable lines={ship.cost_lines} actualVat={ship.actual_vat_krw} />
      <BasisComparisonPanel
        open={basisOpen}
        loading={basisLoading}
        data={basis}
        currentBasis={ship.allocation_basis}
        onToggle={loadBasis}
      />
      <DocumentsPanel
        ship={ship}
        docType={docType}
        setDocType={setDocType}
        fileRef={fileRef}
        onUpload={handleUpload}
        onDelete={handleDeleteDoc}
      />
    </div>
  );
}

// ── 검산 3종 패널 ──
function ReconcilePanel({ reconcile }: { reconcile: ImportReconcile }) {
  return (
    <div className="bg-white rounded-lg border mb-4">
      <div className="px-4 py-3 border-b flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-700">검산 3종</h3>
        <span
          className={`text-xs px-2 py-0.5 rounded-full font-medium ${
            reconcile.passed ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"
          }`}
        >
          {reconcile.passed ? "전항 통과" : "미통과"}
        </span>
      </div>
      <div className="divide-y">
        {reconcile.checks.map((c) => (
          <ReconcileCheckRow key={c.key} check={c} />
        ))}
      </div>
    </div>
  );
}

function ReconcileCheckRow({ check }: { check: ImportReconcileCheck }) {
  const [open, setOpen] = useState(false);
  // ★missing은 회색(=판정 안 함/해당 없음)이 아니라 노랑(=경고, 원료 자체가 없어 대조를 못 했다).
  const badge =
    check.status === "ok"
      ? { text: "통과", cls: "bg-green-100 text-green-700" }
      : check.status === "mismatch"
        ? { text: "불일치", cls: "bg-red-100 text-red-700" }
        : { text: "실행 안 됨 — 원료 없음", cls: "bg-yellow-100 text-yellow-800" };

  return (
    <div className="px-4 py-3">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <span className={`text-xs px-2 py-0.5 rounded font-medium ${badge.cls}`}>{badge.text}</span>
          <span className="text-sm text-gray-800">{check.label}</span>
        </div>
        <div className="text-xs text-gray-500 tabular-nums">
          기대 {numStr(check.expected, 2)} / 실제 {numStr(check.actual, 2)}
        </div>
      </div>
      {check.detail && <p className="mt-1 text-xs text-gray-500">{check.detail}</p>}
      {check.rows.length > 0 && (
        <div className="mt-2">
          <button onClick={() => setOpen((o) => !o)} className="text-xs text-blue-600 hover:underline">
            어긋난 품목 {check.rows.length}건 {open ? "접기" : "보기"}
          </button>
          {open && (
            <div className="mt-2 overflow-x-auto">
              <table className="min-w-full text-xs">
                <thead>
                  <tr className="text-gray-500">
                    <th className="text-left py-1 pr-3">품목</th>
                    <th className="text-right py-1 pr-3">CI</th>
                    <th className="text-right py-1 pr-3">PL</th>
                    <th className="text-right py-1">차이</th>
                  </tr>
                </thead>
                <tbody>
                  {check.rows.map((r, i) => (
                    <tr key={i} className="border-t border-gray-100">
                      <td className="py-1 pr-3">{r.item}</td>
                      <td className="py-1 pr-3 text-right">{numStr(r.ci, 2)}</td>
                      <td className="py-1 pr-3 text-right">{numStr(r.pl, 2)}</td>
                      <td className="py-1 text-right text-red-600 font-medium">{numStr(r.diff, 2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── 미배분 잔액 ──
function UnallocatedPanel({
  unallocated,
  allocationError,
}: {
  unallocated: string | null;
  allocationError: string;
}) {
  const n = unallocated == null ? null : Number(unallocated);
  const ok = n != null && n === 0;
  return (
    <div className="bg-white rounded-lg border mb-4 px-4 py-4 flex items-center justify-between flex-wrap gap-2">
      <div>
        <div className="text-sm text-gray-500">미배분 잔액</div>
        {allocationError && (
          <div className="text-xs text-amber-700 mt-1">⚠ 배부 불가: {allocationError}</div>
        )}
      </div>
      <div className={`text-2xl font-bold ${n == null ? "text-gray-400" : ok ? "text-green-600" : "text-red-600"}`}>
        {unallocated == null ? "—" : won(unallocated)}
      </div>
    </div>
  );
}

// ── 인보이스 라인 표 ──
// 배부액(공통/관세) 내역은 invoice_lines가 아니라 allocation.lines에 실린다(D-CPP-50) — seq로 매칭한다.
function InvoiceLinesTable({
  lines,
  allocation,
}: {
  lines: ImportInvoiceLine[];
  allocation: ImportAllocation | null;
}) {
  const allocBySeq = new Map((allocation?.lines ?? []).map((a) => [a.seq, a]));
  return (
    <div className="bg-white rounded-lg border mb-4">
      <h3 className="px-4 py-3 border-b text-sm font-semibold text-gray-700">인보이스 라인 (CI)</h3>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b bg-gray-50 text-gray-600">
              <th className="text-left px-3 py-2 font-medium">#</th>
              <th className="text-left px-3 py-2 font-medium">품목</th>
              <th className="text-center px-3 py-2 font-medium">구분</th>
              <th className="text-right px-3 py-2 font-medium">수량</th>
              <th className="text-right px-3 py-2 font-medium">외화단가</th>
              <th className="text-right px-3 py-2 font-medium">물품대(원)</th>
              <th className="text-right px-3 py-2 font-medium">관세율</th>
              <th className="text-right px-3 py-2 font-medium">배부액(공통)</th>
              <th className="text-right px-3 py-2 font-medium">배부액(관세)</th>
              <th className="text-right px-3 py-2 font-medium">배부액 합계</th>
              <th className="text-right px-3 py-2 font-medium">개당원가(VAT제외)</th>
              <th className="text-right px-3 py-2 font-medium">개당원가(VAT포함)</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((l) => {
              const a = allocBySeq.get(l.seq);
              return (
                <tr key={l.seq} className="border-b">
                  <td className="px-3 py-2 text-gray-500">{l.seq}</td>
                  <td className="px-3 py-2">
                    {l.item_name}
                    {l.internal_sku && (
                      <span className="ml-1 text-xs text-gray-400 font-mono">({l.internal_sku})</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-center">
                    <span className={`text-xs px-1.5 py-0.5 rounded ${LINE_TYPE_CLASS[l.line_type]}`}>
                      {LINE_TYPE_LABEL[l.line_type]}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right">{numStr(l.quantity, 2)}</td>
                  <td className="px-3 py-2 text-right">{numStr(l.unit_price_foreign, 4)}</td>
                  <td className="px-3 py-2 text-right">{won(l.goods_amount_krw)}</td>
                  <td className="px-3 py-2 text-right">
                    {l.duty_rate == null ? (
                      <span className="text-gray-400">모름</span>
                    ) : (
                      `${rateToPctStr(l.duty_rate)}%`
                    )}
                  </td>
                  <td className="px-3 py-2 text-right">{won(a?.allocated_common_krw ?? null)}</td>
                  <td className="px-3 py-2 text-right">{won(a?.allocated_duty_krw ?? null)}</td>
                  <td className="px-3 py-2 text-right font-medium">{won(l.allocated_cost_krw)}</td>
                  <td className="px-3 py-2 text-right">{won(l.unit_cost_ex_vat)}</td>
                  <td className="px-3 py-2 text-right font-medium">{won(l.unit_cost_inc_vat)}</td>
                </tr>
              );
            })}
            {lines.length === 0 && (
              <tr>
                <td colSpan={12} className="px-3 py-6 text-center text-gray-400">
                  인보이스 라인이 없습니다.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── 관세 검산 (D-CPP-50) — 이 기능의 검증 장치. 세율을 맞게 넣었는지 확인하는 유일한 방법 ──
function DutyCheckPanel({ allocation }: { allocation: ImportAllocation | null }) {
  if (!allocation || !allocation.duty_mode) return null;

  if (allocation.duty_mode === "blended") {
    return (
      <div className="bg-yellow-50 border border-yellow-200 rounded-lg mb-4 px-4 py-4">
        <div className="text-sm font-semibold text-yellow-800">⚠ 관세 검산</div>
        <p className="text-sm text-yellow-800 mt-1">
          관세가 공통비에 섞여 배부되고 있습니다. 품목별 관세율을 입력하면 정확해집니다.
        </p>
      </div>
    );
  }

  const dutyPool = allocation.duty_pool_krw != null ? Number(allocation.duty_pool_krw) : null;
  const diff = allocation.duty_check_diff != null ? Number(allocation.duty_check_diff) : null;

  let diffCls = "text-gray-400";
  if (dutyPool != null && dutyPool !== 0 && diff != null) {
    diffCls = Math.abs(diff) <= Math.abs(dutyPool) * 0.01 ? "text-green-600" : "text-red-600";
  }

  return (
    <div className="bg-white rounded-lg border mb-4">
      <div className="px-4 py-3 border-b">
        <h3 className="text-sm font-semibold text-gray-700">관세 검산</h3>
      </div>
      <div className="px-4 py-4 grid grid-cols-3 gap-4 text-center">
        <div>
          <div className="text-xs text-gray-500">서류 관세</div>
          <div className="text-lg font-bold text-gray-800">{won(allocation.duty_pool_krw ?? null)}</div>
        </div>
        <div>
          <div className="text-xs text-gray-500">계산 관세(세율 기준)</div>
          <div className="text-lg font-bold text-gray-800">{won(allocation.duty_computed_krw ?? null)}</div>
        </div>
        <div>
          <div className="text-xs text-gray-500">차이</div>
          <div className={`text-lg font-bold ${diffCls}`}>{won(allocation.duty_check_diff ?? null)}</div>
        </div>
      </div>
      <p className="px-4 py-3 border-t text-xs text-gray-500">
        차이가 작다는 것은 입력한 세율이 서류의 관세를 재현한다는 뜻입니다.
      </p>
    </div>
  );
}

// ── 비용 라인 표 (통관경비서) ──
function CostLinesTable({ lines, actualVat }: { lines: ImportCostLine[]; actualVat: string | null }) {
  return (
    <div className="bg-white rounded-lg border mb-4">
      <h3 className="px-4 py-3 border-b text-sm font-semibold text-gray-700">비용 라인 (통관경비서)</h3>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b bg-gray-50 text-gray-600">
              <th className="text-left px-3 py-2 font-medium">#</th>
              <th className="text-left px-3 py-2 font-medium">항목</th>
              <th className="text-right px-3 py-2 font-medium">공급가액</th>
              <th className="text-right px-3 py-2 font-medium">세액</th>
              <th className="text-center px-3 py-2 font-medium">원가성</th>
              <th className="text-center px-3 py-2 font-medium">관세</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((l) => (
              <tr key={l.seq} className={`border-b ${!l.is_costing ? "text-gray-400" : ""}`}>
                <td className="px-3 py-2">{l.seq}</td>
                <td className="px-3 py-2">
                  {l.item_name}
                  {l.note && <span className="ml-1 text-xs text-gray-400">({l.note})</span>}
                </td>
                <td className="px-3 py-2 text-right">{won(l.supply_amount)}</td>
                <td className="px-3 py-2 text-right">{won(l.tax_amount)}</td>
                <td className="px-3 py-2 text-center">
                  <span
                    className={`text-xs px-1.5 py-0.5 rounded ${
                      l.is_costing ? "bg-blue-100 text-blue-700" : "bg-gray-100 text-gray-500"
                    }`}
                  >
                    {l.is_costing ? "원가성" : "원가성 아님(제외)"}
                  </span>
                </td>
                <td className="px-3 py-2 text-center">
                  {l.is_duty ? (
                    <span
                      className="text-xs px-1.5 py-0.5 rounded bg-orange-100 text-orange-700"
                      title="이 줄은 배부하지 않고 품목별로 귀속합니다"
                    >
                      관세
                    </span>
                  ) : (
                    <span className="text-gray-300">—</span>
                  )}
                </td>
              </tr>
            ))}
            {lines.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-gray-400">
                  비용 라인이 없습니다.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="px-4 py-3 border-t text-xs text-gray-500">
        실제 부가세 합계 {won(actualVat)} — 개당원가(VAT포함)은 원가성 라인의 부가세 제외 원가에
        ×1.1을 적용한 값이며, 위 실제 납부 세액과 다를 수 있습니다(참고값). 부가세 제외/포함 중
        어느 쪽이 회계상 정답인지는 아직 확인되지 않았습니다.
      </p>
    </div>
  );
}

// ── 배부기준 비교 (접이식) ──
function BasisComparisonPanel({
  open,
  loading,
  data,
  currentBasis,
  onToggle,
}: {
  open: boolean;
  loading: boolean;
  data: ImportBasisComparison | null;
  currentBasis: ImportAllocationBasis;
  onToggle: () => void;
}) {
  return (
    <div className="bg-white rounded-lg border mb-4">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium text-gray-700 hover:bg-gray-50"
      >
        <span>배부기준 비교 {open ? "접기" : "보기"}</span>
        <span className="text-gray-400">{loading ? "…" : open ? "▲" : "▼"}</span>
      </button>
      {open && data && (
        <div className="border-t px-4 py-3">
          <p className="text-xs text-gray-500 mb-2">
            기준별 배부액을 나란히 비교합니다. 현재 이 수입건은{" "}
            <span className="font-medium text-gray-700">{BASIS_LABEL[currentBasis]}</span> 기준을
            씁니다.
          </p>
          <div className="overflow-x-auto">
            <table className="min-w-full text-xs">
              <thead>
                <tr className="text-gray-500">
                  <th className="text-left py-1 pr-3">기준</th>
                  <th className="text-center py-1 pr-3">가용</th>
                  <th className="text-right py-1 pr-3">미배분</th>
                  <th className="text-left py-1">대표 라인 배부액(상위 3건)</th>
                </tr>
              </thead>
              <tbody>
                {data.comparison.map((c) => (
                  <tr
                    key={c.basis}
                    className={`border-t border-gray-100 ${c.basis === currentBasis ? "bg-blue-50" : ""}`}
                  >
                    <td className="py-1 pr-3 font-medium">
                      {BASIS_LABEL[c.basis]}
                      {c.basis === currentBasis && <span className="ml-1 text-blue-600">← 현재</span>}
                    </td>
                    <td className="py-1 pr-3 text-center">{c.available ? "✓" : "✗"}</td>
                    <td className="py-1 pr-3 text-right">
                      {c.available ? won(c.unallocated_krw ?? null) : "—"}
                    </td>
                    <td className="py-1">
                      {c.available
                        ? c.lines
                            .slice(0, 3)
                            .map((ln) => `${ln.item_name} ${won(ln.allocated_cost_krw)}`)
                            .join(" · ") || "—"
                        : c.reason}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

// ── 원본 서류 ──
function DocumentsPanel({
  ship,
  docType,
  setDocType,
  fileRef,
  onUpload,
  onDelete,
}: {
  ship: ImportShipmentDetail;
  docType: ImportDocType;
  setDocType: (t: ImportDocType) => void;
  fileRef: React.RefObject<HTMLInputElement | null>;
  onUpload: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onDelete: (docId: number) => void;
}) {
  return (
    <div className="bg-white rounded-lg border mb-4">
      <div className="px-4 py-3 border-b flex items-center justify-between flex-wrap gap-2">
        <h3 className="text-sm font-semibold text-gray-700">원본 서류</h3>
        <div className="flex items-center gap-2">
          <select
            value={docType}
            onChange={(e) => setDocType(e.target.value as ImportDocType)}
            className="border rounded-md px-2 py-1.5 text-xs"
          >
            {(Object.keys(DOC_TYPE_LABEL) as ImportDocType[]).map((t) => (
              <option key={t} value={t}>
                {DOC_TYPE_LABEL[t]}
              </option>
            ))}
          </select>
          <label className="px-3 py-1.5 text-xs bg-gray-500 text-white rounded-md hover:bg-gray-600 cursor-pointer">
            업로드
            <input ref={fileRef} type="file" className="hidden" onChange={onUpload} />
          </label>
        </div>
      </div>
      <div className="divide-y">
        {ship.documents.map((d) => (
          <div key={d.id} className="px-4 py-2 flex items-center justify-between text-sm flex-wrap gap-1">
            <div>
              <span className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 mr-2">
                {DOC_TYPE_LABEL[d.doc_type]}
              </span>
              <a
                href={importDocumentDownloadUrl(ship.id, d.id)}
                className="text-blue-600 hover:underline"
              >
                {d.filename}
              </a>
              <span className="ml-2 text-xs text-gray-400">
                {(d.size_bytes / 1024).toFixed(0)}KB · {d.uploaded_at ?? "—"}
              </span>
            </div>
            {ship.status === "draft" && (
              <button onClick={() => onDelete(d.id)} className="text-red-500 hover:underline text-xs">
                삭제
              </button>
            )}
          </div>
        ))}
        {ship.documents.length === 0 && (
          <div className="px-4 py-6 text-center text-gray-400 text-sm">업로드된 서류가 없습니다.</div>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// 입력 폼 (신규/수정) — 헤더 필드 + 라인 3종 편집 표.
//   ★파싱 업로드는 붙이지 않는다(별도 세션에서 파서 연결 예정) — 여기 있는 「업로드」는
//     상세 화면의 원본 서류 보관용뿐이다.
// ─────────────────────────────────────────────────────────────
interface ColDef<T> {
  key: keyof T;
  label: string;
  type?: "text" | "number" | "checkbox" | "select";
  options?: { value: string; label: string }[];
  align?: "left" | "right" | "center";
  // 제네릭 셀 렌더로 표현 안 되는 칸(예: 퍼센트↔소수 변환)은 이걸로 직접 그린다 — type을 무시한다.
  render?: (row: T, onChange: (value: unknown) => void) => React.ReactNode;
}
const ALIGN_CLASS: Record<string, string> = {
  left: "text-left",
  right: "text-right",
  center: "text-center",
};

function EditableLineTable<T>({
  title,
  rows,
  setRows,
  columns,
  newRow,
}: {
  title: string;
  rows: T[];
  setRows: (rows: T[]) => void;
  columns: ColDef<T>[];
  newRow: () => T;
}) {
  function update(i: number, key: keyof T, value: unknown) {
    const next = rows.slice();
    next[i] = { ...next[i], [key]: value } as T;
    setRows(next);
  }
  function addRow() {
    setRows([...rows, newRow()]);
  }
  function removeRow(i: number) {
    setRows(rows.filter((_, idx) => idx !== i));
  }

  return (
    <div className="bg-white rounded-lg border mb-4">
      <div className="px-4 py-3 border-b flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-700">{title}</h3>
        <button
          type="button"
          onClick={addRow}
          className="text-xs bg-blue-500 text-white px-2 py-1 rounded hover:bg-blue-600"
        >
          + 행 추가
        </button>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-xs">
          <thead>
            <tr className="border-b bg-gray-50 text-gray-500">
              <th className="text-left px-2 py-1.5 w-8">#</th>
              {columns.map((c) => (
                <th key={String(c.key)} className={`px-2 py-1.5 font-medium ${ALIGN_CLASS[c.align ?? "left"]}`}>
                  {c.label}
                </th>
              ))}
              <th className="w-10"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-b border-gray-100">
                <td className="px-2 py-1 text-gray-400">{i + 1}</td>
                {columns.map((c) => {
                  const cell: any = (row as any)[c.key];
                  return (
                    <td key={String(c.key)} className="px-2 py-1">
                      {c.render ? (
                        c.render(row, (value) => update(i, c.key, value))
                      ) : c.type === "checkbox" ? (
                        <input
                          type="checkbox"
                          checked={!!cell}
                          onChange={(e) => update(i, c.key, e.target.checked)}
                        />
                      ) : c.type === "select" ? (
                        <select
                          value={cell ?? ""}
                          onChange={(e) => update(i, c.key, e.target.value)}
                          className="w-full border rounded px-1 py-1 text-xs"
                        >
                          {c.options!.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <input
                          type="text"
                          inputMode={c.type === "number" ? "decimal" : "text"}
                          value={cell ?? ""}
                          onChange={(e) => update(i, c.key, e.target.value)}
                          className={`w-full border rounded px-1.5 py-1 text-xs ${
                            c.align === "right" ? "text-right" : ""
                          }`}
                        />
                      )}
                    </td>
                  );
                })}
                <td className="px-2 py-1 text-center">
                  <button type="button" onClick={() => removeRow(i)} className="text-red-500 hover:underline">
                    삭제
                  </button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={columns.length + 2} className="px-2 py-4 text-center text-gray-400">
                  행이 없습니다.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const COST_COLUMNS: ColDef<ImportCostLine>[] = [
  { key: "item_name", label: "항목" },
  { key: "supply_amount", label: "공급가액", type: "number", align: "right" },
  { key: "tax_amount", label: "세액", type: "number", align: "right" },
  { key: "is_costing", label: "원가성", type: "checkbox", align: "center" },
  {
    key: "is_duty",
    label: "관세",
    type: "checkbox",
    align: "center",
    render: (row, onChange) => (
      <input
        type="checkbox"
        checked={!!row.is_duty}
        onChange={(e) => onChange(e.target.checked)}
        title="이 줄은 배부하지 않고 품목별로 귀속합니다"
      />
    ),
  },
  { key: "note", label: "비고" },
];

const INVOICE_COLUMNS: ColDef<ImportInvoiceLine>[] = [
  { key: "item_name", label: "품목" },
  {
    key: "line_type",
    label: "구분",
    type: "select",
    align: "center",
    options: [
      { value: "unknown", label: "미분류" },
      { value: "product", label: "판매SKU" },
      { value: "material", label: "부자재" },
    ],
  },
  { key: "internal_sku", label: "내부SKU" },
  { key: "order_no", label: "발주번호" },
  { key: "quantity", label: "수량", type: "number", align: "right" },
  { key: "unit_price_foreign", label: "외화단가", type: "number", align: "right" },
  {
    key: "duty_rate",
    label: "관세율(%)",
    align: "right",
    render: (row, onChange) => (
      <PercentInput rate={row.duty_rate} onChange={(r) => onChange(r)} />
    ),
  },
  { key: "gross_weight_kg", label: "중량(kg)", type: "number", align: "right" },
  { key: "cbm", label: "CBM", type: "number", align: "right" },
];

const PACKING_COLUMNS: ColDef<ImportPackingLine>[] = [
  { key: "item_name", label: "품목" },
  { key: "carton_range", label: "박스번호" },
  { key: "quantity", label: "수량", type: "number", align: "right" },
  { key: "qty_per_carton", label: "박스당수량", type: "number", align: "right" },
  { key: "carton_count", label: "박스수", type: "number", align: "right" },
  { key: "gross_weight_kg", label: "중량(kg)", type: "number", align: "right" },
  { key: "measure", label: "규격" },
  { key: "cbm", label: "CBM", type: "number", align: "right" },
  { key: "remark", label: "비고" },
];

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs font-medium text-gray-500 mb-0.5">{label}</label>
      {children}
    </div>
  );
}

const inputCls = "w-full border rounded px-2 py-1.5 text-sm";

// ── 서류 올려서 자동 채우기 (POST /api/import-cost/parse, 저장하지 않는다) ──
// ★파싱은 «채워주기 편의»다 — 정본은 사람이 확인한 폼(백엔드 주석과 동일 원칙).
function DocumentUploadPanel({ onApply }: { onApply: (result: ImportParseResult) => void }) {
  const [open, setOpen] = useState(true);
  const [ciPlFile, setCiPlFile] = useState<File | null>(null);
  const [plFile, setPlFile] = useState<File | null>(null);
  const [expenseFile, setExpenseFile] = useState<File | null>(null);
  const [textOpen, setTextOpen] = useState(false);
  const [expenseText, setExpenseText] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [result, setResult] = useState<ImportParseResult | null>(null);

  const canSubmit = !!(ciPlFile || plFile || expenseFile || expenseText.trim());

  async function handleLoad() {
    setLoading(true);
    setErr("");
    try {
      const res = await parseImportDocuments({
        ciPlFile,
        plFile,
        expenseFile,
        expenseText: expenseText.trim() ? expenseText : undefined,
      });
      setResult(res);
      onApply(res);
    } catch (e) {
      setErr(`불러오기 실패: ${e}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="bg-white rounded-lg border mb-4">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium text-gray-700 hover:bg-gray-50"
      >
        <span>📄 서류 올려서 자동 채우기</span>
        <span className="text-gray-400">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="border-t px-4 py-4 space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <Field label="① 인보이스(CI) 엑셀 — PL이 같은 파일이면 이것만 (.xls, .xlsx)">
              <input
                type="file"
                accept=".xls,.xlsx"
                onChange={(e) => setCiPlFile(e.target.files?.[0] ?? null)}
                className="w-full text-xs"
              />
            </Field>
            <Field label="② 포장명세서(PL) — 별도 파일일 때만 (.xls, .xlsx)">
              <input
                type="file"
                accept=".xls,.xlsx"
                onChange={(e) => setPlFile(e.target.files?.[0] ?? null)}
                className="w-full text-xs"
              />
            </Field>
            <Field label="통관경비서 PDF">
              <input
                type="file"
                accept=".pdf"
                onChange={(e) => setExpenseFile(e.target.files?.[0] ?? null)}
                className="w-full text-xs"
              />
            </Field>
          </div>

          <div>
            <button
              type="button"
              onClick={() => setTextOpen((o) => !o)}
              className="text-xs text-blue-600 hover:underline"
            >
              PDF가 안 읽히면 내용을 붙여넣으세요 {textOpen ? "접기" : "펼치기"}
            </button>
            {textOpen && (
              <textarea
                value={expenseText}
                onChange={(e) => setExpenseText(e.target.value)}
                rows={6}
                placeholder="통관경비서 내용을 그대로 붙여넣으세요"
                className={`${inputCls} mt-2`}
              />
            )}
          </div>

          <p className="text-xs text-gray-500">
            ※ 통관경비서 PDF와 붙여넣은 텍스트를 둘 다 넣으면 붙여넣은 텍스트를 우선합니다.
          </p>

          <button
            type="button"
            onClick={handleLoad}
            disabled={!canSubmit || loading}
            className="px-3 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5"
          >
            {loading && (
              <span className="inline-block w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
            )}
            {loading ? "불러오는 중…" : "불러오기"}
          </button>

          {err && <div className="p-3 bg-red-50 text-red-700 rounded-md text-sm">{err}</div>}

          {result && (
            <div className="space-y-2">
              <div className="text-sm text-gray-700 bg-blue-50 border border-blue-200 rounded-md px-3 py-2">
                인보이스 {result.invoice_lines.length}건 · 패킹 {result.packing_lines.length}건 ·
                비용 {result.cost_lines.length}건을 불러왔습니다.
              </div>
              {result.errors.length > 0 && (
                <div className="p-3 bg-red-50 border border-red-200 text-red-700 rounded-md text-sm">
                  <p className="font-medium mb-1">오류</p>
                  <ul className="list-disc list-inside space-y-0.5">
                    {result.errors.map((m, i) => (
                      <li key={i}>{m}</li>
                    ))}
                  </ul>
                </div>
              )}
              {result.warnings.length > 0 && (
                <div className="p-3 bg-yellow-50 border border-yellow-200 text-yellow-800 rounded-md text-sm">
                  <p className="font-medium mb-1">경고</p>
                  <ul className="list-disc list-inside space-y-0.5">
                    {result.warnings.map((m, i) => (
                      <li key={i}>{m}</li>
                    ))}
                  </ul>
                </div>
              )}
              <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
                ⚠ 구분(판매 SKU/부자재)은 자동으로 정하지 않습니다 — 아래 인보이스 라인 표에서
                직접 지정하세요.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ShipmentFormView({
  id,
  onDone,
  onCancel,
}: {
  id: number | null;
  onDone: (id: number) => void;
  onCancel: () => void;
}) {
  const editing = id !== null;
  const [loading, setLoading] = useState(editing);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  const [hblNo, setHblNo] = useState("");
  const [currency, setCurrency] = useState("CNY");
  const [fxRate, setFxRate] = useState("");
  const [declarationNo, setDeclarationNo] = useState("");
  const [declarationDate, setDeclarationDate] = useState("");
  const [eta, setEta] = useState("");
  const [shipperName, setShipperName] = useState("");
  const [invoiceNo, setInvoiceNo] = useState("");
  const [vessel, setVessel] = useState("");
  const [declaredInvValue, setDeclaredInvValue] = useState("");
  const [customsValueKrw, setCustomsValueKrw] = useState("");
  const [cartonCount, setCartonCount] = useState("");
  const [grossWeightKg, setGrossWeightKg] = useState("");
  const [cbm, setCbm] = useState("");
  const [allocationBasis, setAllocationBasis] = useState<ImportAllocationBasis>("amount");
  const [memo, setMemo] = useState("");

  const [costLines, setCostLines] = useState<ImportCostLine[]>([]);
  const [invoiceLines, setInvoiceLines] = useState<ImportInvoiceLine[]>([]);
  const [packingLines, setPackingLines] = useState<ImportPackingLine[]>([]);

  useEffect(() => {
    if (!editing) return;
    setLoading(true);
    fetchImportShipment(id!)
      .then((s) => {
        setHblNo(s.hbl_no);
        setCurrency(s.currency);
        setFxRate(s.fx_rate);
        setDeclarationNo(s.declaration_no ?? "");
        setDeclarationDate(s.declaration_date ?? "");
        setEta(s.eta ?? "");
        setShipperName(s.shipper_name ?? "");
        setInvoiceNo(s.invoice_no ?? "");
        setVessel(s.vessel ?? "");
        setDeclaredInvValue(s.declared_inv_value ?? "");
        setCustomsValueKrw(s.customs_value_krw ?? "");
        setCartonCount(s.carton_count != null ? String(s.carton_count) : "");
        setGrossWeightKg(s.gross_weight_kg ?? "");
        setCbm(s.cbm ?? "");
        setAllocationBasis(s.allocation_basis);
        setMemo(s.memo ?? "");
        setCostLines(s.cost_lines);
        setInvoiceLines(s.invoice_lines);
        setPackingLines(s.packing_lines);
      })
      .catch((e) => setErr(`불러오기 실패: ${e}`))
      .finally(() => setLoading(false));
  }, [editing, id]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setErr("");
    const body: ImportShipmentInput = {
      hbl_no: hblNo,
      fx_rate: fxRate,
      currency,
      declaration_no: declarationNo || null,
      declaration_date: declarationDate || null,
      eta: eta || null,
      shipper_name: shipperName || null,
      invoice_no: invoiceNo || null,
      vessel: vessel || null,
      declared_inv_value: declaredInvValue || null,
      customs_value_krw: customsValueKrw || null,
      carton_count: cartonCount ? Number(cartonCount) : null,
      gross_weight_kg: grossWeightKg || null,
      cbm: cbm || null,
      allocation_basis: allocationBasis,
      memo: memo || null,
      // ★서버가 라인을 통째로 교체한다 — seq는 화면 순서로 다시 매긴다(사용자가 직접 관리하지 않는다).
      cost_lines: costLines.map((l, i) => ({ ...l, seq: i + 1 })),
      invoice_lines: invoiceLines.map((l, i) => ({ ...l, seq: i + 1 })),
      packing_lines: packingLines.map((l, i) => ({ ...l, seq: i + 1 })),
    };
    try {
      const saved = editing ? await updateImportShipment(id!, body) : await createImportShipment(body);
      onDone(saved.id);
    } catch (e) {
      setErr(`저장 실패: ${e}`);
    } finally {
      setSaving(false);
    }
  }

  // ★키가 있는 것만 채운다 — 없는 키는 기존 값을 지우지 않는다(파싱이 못 읽은 필드는 그대로 둔다).
  // ★라인 3종은 응답이 비어 있지 않을 때만 통째로 교체한다(파싱 실패 시 기존 입력을 지우지 않는다).
  function applyParseResult(result: ImportParseResult) {
    const h = result.header;
    if (h.hbl_no !== undefined) setHblNo(h.hbl_no);
    if (h.currency !== undefined) setCurrency(h.currency);
    if (h.fx_rate !== undefined) setFxRate(h.fx_rate);
    if (h.declaration_no !== undefined) setDeclarationNo(h.declaration_no);
    if (h.declaration_date !== undefined) setDeclarationDate(h.declaration_date);
    if (h.eta !== undefined) setEta(h.eta);
    if (h.shipper_name !== undefined) setShipperName(h.shipper_name);
    if (h.invoice_no !== undefined) setInvoiceNo(h.invoice_no);
    if (h.vessel !== undefined) setVessel(h.vessel);
    if (h.declared_inv_value !== undefined) setDeclaredInvValue(h.declared_inv_value);
    if (h.customs_value_krw !== undefined) setCustomsValueKrw(h.customs_value_krw);
    if (h.carton_count !== undefined) setCartonCount(String(h.carton_count));
    if (h.gross_weight_kg !== undefined) setGrossWeightKg(h.gross_weight_kg);
    if (h.cbm !== undefined) setCbm(h.cbm);

    if (result.invoice_lines.length > 0) {
      setInvoiceLines(
        result.invoice_lines.map((l): ImportInvoiceLine => ({
          seq: l.seq,
          item_name: l.item_name,
          quantity: l.quantity,
          unit_price_foreign: l.unit_price_foreign,
          line_type: l.line_type,
          order_no: l.order_no ?? "",
          internal_sku: l.internal_sku ?? "",
          gross_weight_kg: l.gross_weight_kg ?? "",
          cbm: l.cbm ?? "",
          // 파싱은 관세율을 추론하지 않는다 — 사람이 채운다(모름=null, 0%로 짐작해 넣지 않는다).
          duty_rate: null,
        })),
      );
    }
    if (result.packing_lines.length > 0) {
      setPackingLines(
        result.packing_lines.map((l): ImportPackingLine => ({
          seq: l.seq,
          item_name: l.item_name,
          quantity: l.quantity,
          carton_range: l.carton_range ?? "",
          qty_per_carton: l.qty_per_carton ?? "",
          carton_count: l.carton_count ?? "",
          gross_weight_kg: l.gross_weight_kg ?? "",
          measure: l.measure ?? "",
          cbm: l.cbm ?? "",
          remark: l.remark ?? "",
        })),
      );
    }
    if (result.cost_lines.length > 0) {
      setCostLines(
        result.cost_lines.map((l): ImportCostLine => ({
          seq: l.seq,
          item_name: l.item_name,
          supply_amount: l.supply_amount,
          tax_amount: l.tax_amount,
          is_costing: l.is_costing,
          // 파싱은 관세 라인을 추론하지 않는다 — 사람이 체크한다.
          is_duty: false,
          note: l.note ?? "",
        })),
      );
    }
  }

  if (loading) return <div className="text-center text-gray-400 py-12">로딩 중…</div>;

  return (
    <form onSubmit={handleSubmit}>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-2xl font-bold text-gray-900">{editing ? "수입건 수정" : "수입건 등록"}</h2>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-md"
          >
            취소
          </button>
          <button
            type="submit"
            disabled={saving}
            className="px-4 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
          >
            저장
          </button>
        </div>
      </div>

      {err && <div className="mb-3 p-3 bg-red-50 text-red-700 rounded-md text-sm">{err}</div>}

      <DocumentUploadPanel onApply={applyParseResult} />

      <div className="bg-white rounded-lg border mb-4 p-4 grid grid-cols-2 md:grid-cols-4 gap-3">
        <Field label="HBL No *">
          <input required value={hblNo} onChange={(e) => setHblNo(e.target.value)} className={inputCls} />
        </Field>
        <Field label="통화">
          <input value={currency} onChange={(e) => setCurrency(e.target.value)} className={inputCls} />
        </Field>
        <Field label="환율 *">
          <input required value={fxRate} onChange={(e) => setFxRate(e.target.value)} className={inputCls} />
        </Field>
        <Field label="배부기준">
          <select
            value={allocationBasis}
            onChange={(e) => setAllocationBasis(e.target.value as ImportAllocationBasis)}
            className={inputCls}
          >
            {(Object.keys(BASIS_LABEL) as ImportAllocationBasis[]).map((b) => (
              <option key={b} value={b}>
                {BASIS_LABEL[b]}
              </option>
            ))}
          </select>
        </Field>
        <Field label="신고번호">
          <input value={declarationNo} onChange={(e) => setDeclarationNo(e.target.value)} className={inputCls} />
        </Field>
        <Field label="신고일">
          <input type="date" value={declarationDate} onChange={(e) => setDeclarationDate(e.target.value)} className={inputCls} />
        </Field>
        <Field label="ETA">
          <input type="date" value={eta} onChange={(e) => setEta(e.target.value)} className={inputCls} />
        </Field>
        <Field label="Shipper">
          <input value={shipperName} onChange={(e) => setShipperName(e.target.value)} className={inputCls} />
        </Field>
        <Field label="인보이스 번호">
          <input value={invoiceNo} onChange={(e) => setInvoiceNo(e.target.value)} className={inputCls} />
        </Field>
        <Field label="선박/편명">
          <input value={vessel} onChange={(e) => setVessel(e.target.value)} className={inputCls} />
        </Field>
        <Field label="CI 총액(외화)">
          <input value={declaredInvValue} onChange={(e) => setDeclaredInvValue(e.target.value)} className={inputCls} />
        </Field>
        <Field label="관세가(원)">
          <input value={customsValueKrw} onChange={(e) => setCustomsValueKrw(e.target.value)} className={inputCls} />
        </Field>
        <Field label="박스수">
          <input type="number" value={cartonCount} onChange={(e) => setCartonCount(e.target.value)} className={inputCls} />
        </Field>
        <Field label="총중량(kg)">
          <input value={grossWeightKg} onChange={(e) => setGrossWeightKg(e.target.value)} className={inputCls} />
        </Field>
        <Field label="CBM">
          <input value={cbm} onChange={(e) => setCbm(e.target.value)} className={inputCls} />
        </Field>
        <div className="col-span-2 md:col-span-4">
          <Field label="메모">
            <textarea value={memo} onChange={(e) => setMemo(e.target.value)} rows={2} className={inputCls} />
          </Field>
        </div>
      </div>

      <EditableLineTable
        title="통관경비서 (비용 라인)"
        rows={costLines}
        setRows={setCostLines}
        columns={COST_COLUMNS}
        newRow={(): ImportCostLine => ({ seq: 0, item_name: "", supply_amount: "0", tax_amount: "0", is_costing: false, is_duty: false, note: "" })}
      />

      <EditableLineTable
        title="인보이스 라인 (CI)"
        rows={invoiceLines}
        setRows={setInvoiceLines}
        columns={INVOICE_COLUMNS}
        newRow={(): ImportInvoiceLine => ({
          seq: 0,
          item_name: "",
          quantity: "0",
          unit_price_foreign: "0",
          line_type: "unknown",
          order_no: "",
          internal_sku: "",
          gross_weight_kg: "",
          cbm: "",
          // ★부자재라고 자동으로 0을 넣지 않는다 — 사람이 정한다. null="모름"(0%가 아니다).
          duty_rate: null,
        })}
      />

      <EditableLineTable
        title="패킹리스트 라인 (PL)"
        rows={packingLines}
        setRows={setPackingLines}
        columns={PACKING_COLUMNS}
        newRow={(): ImportPackingLine => ({
          seq: 0,
          item_name: "",
          quantity: "0",
          carton_range: "",
          qty_per_carton: "",
          carton_count: "",
          gross_weight_kg: "",
          measure: "",
          cbm: "",
          remark: "",
        })}
      />
    </form>
  );
}
