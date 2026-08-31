/**
 * 매입 완제품 단가 — 파일 근거를 붙이는 화면 (계약 D-CPP-63 S1 3/3)
 *
 * ★이 화면이 존재하는 이유는 «숫자»가 아니라 «족보»다. 매입품 SKU들은 이미
 *   `product_master.cost_price`를 갖고 있다 — 없는 것은 그 숫자가 어디서 왔는가다.
 *
 * ★★묶음은 「대상 확정」이 아니라 「분류 필요」다. prod 실측(2026-08-31): 구성 0줄
 *   레시피 77건에 매입품(케이스·스트랩·렌즈·충전)과 조립품 «필름»이 섞여 있고
 *   `form_factor`가 둘 다 `bar`라 **가르는 DB 신호가 없다**. 그래서 레시피명을 묶음의
 *   얼굴로 크게 세운다 — 「종이질감 저반사 … 액정보호필름 2매」는 사람이 보면 필름인 줄
 *   알지만 시스템은 모른다. 확인 클릭이 곧 「이것은 매입품이다」라는 분류다(계약 §4 S1).
 */
import { useCallback, useEffect, useState } from "react";

import {
  confirmPurchasedPrices,
  fetchPurchasedBoard,
  previewPurchasedPrices,
  type PurchasedBoard,
  type PurchasedGroup,
  type PurchasedPreview,
  type PurchasedSkuRow,
} from "../lib/api";

function won(v: string | null): string {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString("ko-KR") : String(v);
}

/** 차이는 부호를 살려서 보여준다 — 「+」가 파일이 더 비싸다는 뜻이다. */
function diffCell(v: string | null) {
  if (v === null) return <span className="text-gray-400">비교 불가</span>;
  const n = Number(v);
  if (!Number.isFinite(n)) return <span className="text-gray-400">—</span>;
  if (n === 0) return <span className="text-gray-500">0</span>;
  return (
    <span className={n > 0 ? "text-red-600" : "text-blue-600"}>
      {n > 0 ? "+" : ""}
      {n.toLocaleString("ko-KR")}
    </span>
  );
}

function SkuTable({ rows }: { rows: PurchasedSkuRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-xs" data-testid="purchased-sku-table">
        <thead className="text-gray-500">
          <tr className="border-b">
            <th className="text-left py-1 pr-3">SKU</th>
            <th className="text-left py-1 pr-3">상품명</th>
            <th className="text-right py-1 pr-3">현재 원가</th>
            <th className="text-right py-1 pr-3">파일 단가</th>
            <th className="text-right py-1 pr-3">차이</th>
            <th className="text-left py-1">파일의 상품명(근거)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.internal_sku} className="border-b last:border-0">
              <td className="py-1 pr-3 font-mono">{s.internal_sku}</td>
              <td className="py-1 pr-3">{s.product_name}</td>
              <td className="py-1 pr-3 text-right">{won(s.current_cost_price)}</td>
              <td className="py-1 pr-3 text-right font-medium">
                {s.is_placeholder ? (
                  <span className="text-amber-600">공백</span>
                ) : (
                  won(s.file_price)
                )}
              </td>
              <td className="py-1 pr-3 text-right">{diffCell(s.diff)}</td>
              <td className="py-1 text-gray-500">{s.source_product_name}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GroupCard({
  g,
  sourceFile,
  busy,
  onConfirm,
}: {
  g: PurchasedGroup;
  sourceFile: string;
  busy: boolean;
  onConfirm: (g: PurchasedGroup) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border rounded p-3" data-testid="purchased-group">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          {/* ★레시피명이 묶음의 얼굴이다 — 사람이 「필름인가 매입품인가」를 여기서 가른다 */}
          <div className="font-medium text-sm break-words">{g.recipe_name}</div>
          <div className="text-xs text-gray-500 mt-0.5">
            단가 <b className="text-gray-800">{won(g.price)}원</b> · SKU {g.sku_count}건
            {g.already_approved > 0 ? (
              <span className="ml-2 text-green-700">
                이미 근거 있음 {g.already_approved}건
              </span>
            ) : null}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            className="text-xs text-gray-600 underline"
            onClick={() => setOpen((v) => !v)}
          >
            {open ? "접기" : `SKU ${g.sku_count}건 보기`}
          </button>
          <button
            className="px-3 py-1.5 text-sm rounded bg-blue-600 text-white disabled:opacity-50"
            disabled={busy}
            onClick={() => onConfirm(g)}
            data-testid="purchased-confirm"
          >
            매입품으로 확인
          </button>
        </div>
      </div>
      {open ? (
        <div className="mt-3">
          <SkuTable rows={g.skus} />
          <p className="text-[11px] text-gray-400 mt-1">
            출처: {sourceFile}
          </p>
        </div>
      ) : null}
    </div>
  );
}

export default function CostPurchasedPricePanel() {
  const [board, setBoard] = useState<PurchasedBoard | null>(null);
  const [preview, setPreview] = useState<PurchasedPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const loadBoard = useCallback(() => {
    fetchPurchasedBoard()
      .then(setBoard)
      .catch((e) => setErr(String(e?.message ?? e)));
  }, []);

  useEffect(() => loadBoard(), [loadBoard]);

  async function handleFile(file: File) {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      setPreview(await previewPurchasedPrices(file));
    } catch (e: unknown) {
      setPreview(null);
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirm(g: PurchasedGroup) {
    if (!preview) return;
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const names: Record<string, string> = {};
      for (const s of g.skus) names[s.internal_sku] = s.source_product_name;
      const r = await confirmPurchasedPrices({
        internal_skus: g.skus.map((s) => s.internal_sku),
        price: g.price,
        source_file: preview.source_file,
        source_names: names,
      });
      setBoard(r.board);
      // ★거부를 «세어서» 보여준다 — 막은 것과 안 막은 것이 화면에서 갈려야 한다.
      setMsg(
        r.skipped.length === 0
          ? `${g.recipe_name} — ${r.written}건에 근거를 붙였다.`
          : `${g.recipe_name} — ${r.written}건 확정 · ${r.skipped.length}건은 대상이 아니라 쓰지 않았다 (${r.skipped
              .map((s) => `${s.internal_sku}: ${s.reason}`)
              .join(" / ")})`,
      );
      // ★★배지는 «실제로 써진 건수»만 말한다(적대 리뷰 P1-2). 초판은 `x.sku_count`를
      //   그대로 찍어, 서버가 전건 거부해도 카드가 「이미 근거 있음 2건」으로 초록이 됐다 —
      //   메시지 줄은 거부를 말하는데 배지는 반대를 말하는 상태다. `ConfirmResult`가
      //   *"막는 것과 막았다고 말하는 것은 다른 일이다"*라고 적어 둔 그 자리를 화면이 어겼다.
      const refused = new Set(r.skipped.map((s) => s.internal_sku));
      setPreview({
        ...preview,
        groups: preview.groups.map((x) =>
          x.recipe_id === g.recipe_id && x.price === g.price
            ? {
                ...x,
                already_approved: x.skus.filter(
                  (s) => !refused.has(s.internal_sku),
                ).length,
              }
            : x,
        ),
      });
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-4 space-y-4" data-testid="purchased-panel">
      {/* ── 보드: 「어디까지 왔나」를 세션 없이 읽는다 (계약 §4 S1 넷째) ── */}
      <div className="border rounded p-3">
        <div className="text-sm font-medium">매입품 근거화 현황</div>
        {board ? (
          <div className="flex gap-6 mt-2 text-sm" data-testid="purchased-board">
            <div>
              <div className="text-xs text-gray-500">대상 후보</div>
              <div className="text-lg font-semibold">{board.candidates}</div>
            </div>
            <div>
              <div className="text-xs text-gray-500">근거 있음</div>
              <div className="text-lg font-semibold text-green-700">
                {board.grounded}
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-500">보류(공백)</div>
              <div className="text-lg font-semibold text-amber-600">
                {board.held_blank}
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-500">미확인</div>
              <div className="text-lg font-semibold text-gray-700">
                {board.unconfirmed}
              </div>
            </div>
          </div>
        ) : (
          <div className="text-xs text-gray-400 mt-2">불러오는 중…</div>
        )}
        <p className="text-[11px] text-gray-500 mt-2">
          「보류(공백)」는 사람이 <b>값이 없다</b>고 확인한 상태이고, 「미확인」은 아직
          아무도 안 본 것이다 — 다른 사실이라 따로 센다.
        </p>
      </div>

      {/* ── 업로드 ── */}
      <div className="border rounded p-3">
        <div className="text-sm font-medium">원가 매핑 파일 올리기</div>
        <p className="text-xs text-gray-500 mt-1">
          <b>원가 열을 가진 판</b>(08-07판 계열)만 읽는다. 08-22판처럼 「원가」 열이 없는
          판은 거부한다 — 열을 <b>위치가 아니라 이름</b>으로 찾기 때문이다.
        </p>
        <input
          type="file"
          accept=".xlsx"
          className="mt-2 text-sm"
          disabled={busy}
          data-testid="purchased-file"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void handleFile(f);
          }}
        />
      </div>

      {err ? (
        <div
          className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2"
          data-testid="purchased-error"
        >
          {err}
        </div>
      ) : null}
      {msg ? (
        <div
          className="text-sm text-green-700 bg-green-50 border border-green-200 rounded p-2"
          data-testid="purchased-msg"
        >
          {msg}
        </div>
      ) : null}

      {preview ? (
        <>
          <div className="text-xs text-gray-600 border rounded p-2 bg-gray-50">
            <b>{preview.source_file}</b> 에서 「{preview.read_columns.name}」·「
            {preview.read_columns.price}」 열을 읽었다 · 묶음 {preview.counts.groups} ·
            분류 필요 SKU {preview.counts.target_skus} · 공백 {preview.counts.blank_skus}{" "}
            · 대상 아님 {preview.counts.excluded_skus} · 미매칭 행{" "}
            {preview.counts.unmatched_rows}
            <div className="mt-1 text-gray-500">
              확인을 누르기 전까지 <b>아무 값도 써지지 않는다.</b>
            </div>
          </div>

          <div>
            <div className="text-sm font-medium mb-2">
              분류 필요 — 매입품이면 확인을 누른다 ({preview.groups.length}묶음)
            </div>
            <p className="text-xs text-gray-500 mb-2">
              이 목록에는 <b>필름 같은 조립품 초안도 섞여 있다</b> — 구성이 아직 0줄이라
              시스템이 매입품과 가르지 못한다. 레시피명을 보고 <b>매입품일 때만</b> 누른다.
              필름은 우리 계산이 정본이라 파일 값을 쓰지 않는다.
            </p>
            <div className="space-y-2">
              {preview.groups.map((g) => (
                <GroupCard
                  key={`${g.recipe_id}-${g.price}`}
                  g={g}
                  sourceFile={preview.source_file}
                  busy={busy}
                  onConfirm={(x) => void handleConfirm(x)}
                />
              ))}
            </div>
          </div>

          {preview.blanks.length > 0 ? (
            <div data-testid="purchased-blanks">
              <div className="text-sm font-medium mb-1">
                공백 — 파일이 자리표시자(1원)로 둔 것 ({preview.blanks.length}건)
              </div>
              <p className="text-xs text-gray-500 mb-2">
                값으로 싣지 않는다. 「없음」과 「1원」이 같은 얼굴이 되면 안 되기 때문이다.
              </p>
              <SkuTable rows={preview.blanks} />
            </div>
          ) : null}

          {preview.excluded.length > 0 ? (
            <div data-testid="purchased-excluded">
              <div className="text-sm font-medium mb-1">
                대상 아님 ({preview.excluded.length}건)
              </div>
              <div className="text-xs text-gray-500 mb-2">
                {Object.entries(
                  preview.excluded.reduce<Record<string, number>>((acc, s) => {
                    const k = s.excluded_reason ?? "(사유 없음)";
                    acc[k] = (acc[k] ?? 0) + 1;
                    return acc;
                  }, {}),
                ).map(([reason, n]) => (
                  <div key={reason}>
                    {n}건 — {reason}
                  </div>
                ))}
              </div>
              <SkuTable rows={preview.excluded} />
            </div>
          ) : null}

          {preview.unmatched.length > 0 ? (
            <div data-testid="purchased-unmatched">
              <div className="text-sm font-medium mb-1">
                우리 SKU에 못 붙은 파일 행 ({preview.unmatched.length}건)
              </div>
              <ul className="text-xs text-gray-600 list-disc pl-5">
                {preview.unmatched.slice(0, 40).map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
              {preview.unmatched.length > 40 ? (
                <div className="text-xs text-gray-400 mt-1">
                  … 외 {preview.unmatched.length - 40}건
                </div>
              ) : null}
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
