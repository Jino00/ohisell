// CostPage.tsx — 「💰 원가」 (D-CPP-53 / 계약 `docs/PLAN_cost-menu-standard-cost.md`)
//
// S1의 범위는 **부자재 탭 하나**다. 레시피·표준원가 보드는 자리만 잡고 「S2에서」라고 말한다 —
// 빈 화면을 «아직 없음»이라고 밝히는 것과 그냥 비어 있는 것은 다르다.
//
// ★이 파일이 지켜야 하는 마지막 한 칸: **값이 화면 픽셀이 된다.** 이 저장소에서 백엔드 변이는
//   다 죽는데 프론트 변이가 살아남은 사고가 2회 실측됐다(교훈 #321 계열 — 렌더 제거·호출부
//   제거가 초록으로 통과). 그래서 표시 함수와 표를 **순수 컴포넌트로 export** 해 테스트가
//   직접 렌더한다(`costMaterialsSurface.test.tsx`).
//
// ★「없음」은 「0」이 아니다(계약 §2-7). 단가 표시는 전부 `formatCostWon`을 지난다 —
//   `null`은 「—」이고, 그 자리에 0원을 그리면 미입력이 확정값으로 둔갑한다.
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  addCostManualPrice,
  createCostMaterial,
  deleteCostMaterialPrice,
  fetchCostLedgerMaterialLines,
  fetchCostMaterials,
  fetchCostSettings,
  linkCostLedgerPrice,
  patchCostMaterial,
  refreshCostLedgerPrice,
  type CostLedgerCheck,
  type CostLedgerMaterialLine,
  type CostMaterial,
  type CostMaterialPrice,
  type CostSetting,
} from "../lib/api";

export type CostTab = "materials" | "recipes" | "board";

// ══════════════════════════════════════════════════════════════════
// 순수 표시 규칙 (테스트가 이 함수들을 직접 잡는다)
// ══════════════════════════════════════════════════════════════════

/** 단가 표시. **`null`은 「—」다 — 0원으로 그리지 않는다**(계약 §2-7).
 *
 * 「단가를 아직 모른다」와 「단가가 0원이다」는 다른 사실이고, 화면이 둘을 같게 그리면
 * 그게 `cost_price` NOT NULL default 0이 만든 혼동의 재생산이다. */
export function formatCostWon(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${n.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}원`;
}

/** 승인 상태 라벨. 미승인은 **미승인이라고 말한다** — 침묵하지 않는다(계약 §2-2). */
export function materialStatusLabel(status: string): string {
  return status === "approved" ? "승인" : "미확인";
}

/** 단가 출처 라벨 — 「이 값이 어디서 왔나」가 한 칸으로 보여야 추적이 끊기지 않는다. */
export function priceSourceLabel(source: string): string {
  return source === "ledger" ? "원장(로트)" : "수동 입력";
}

/** 엑셀 대응 라벨. **비어 있으면 「미확정」이라고 자백한다**(계약 §9-3).
 *
 * cleaning kit 168원/개가 엑셀의 어느 항목인지 불명이고 원가 정본에도 대응 항목이 없다.
 * 억지 라벨을 붙이면 추론이 확인분으로 굳는다(교훈 #204) — 비워 두고 화면이 말한다. */
export function excelLabelText(label: string | null): string {
  return label && label.trim() ? label : "미확정 — 엑셀 대응 항목 불명";
}

/** 재고 평가방법 자백 문구(계약 §9-1 · 합격 8).
 *
 * ★`confirmed`를 **읽는다**. 산문으로 하드코딩하면 나중에 신고 내역을 확인해
 * `confirmed=true`로 바꿔도 화면이 계속 「미확인」이라고 거짓말한다. */
export function valuationBadgeText(settings: CostSetting[]): string | null {
  const s = settings.find((x) => x.key === "valuation_method");
  if (!s) return "재고 평가방법: 설정 없음 — 확인 안 됨";
  const method = s.value === "fifo" ? "선입선출" : s.value;
  return s.confirmed
    ? `재고 평가방법: ${method} (신고 내역 확인분)`
    : `재고 평가방법: ${method}(무신고 시 법정 기본값) — 신고 내역 미확인`;
}

/** 「이 표준의 근거는 로트 N건」 — 표본 부족을 숨기지 않는다(계약 §9-5).
 *
 * ★어긋난 연결(`stale_count`)을 **따로 말한다**(적대 리뷰 1R P1-1). 그 행들은 최신 단가
 * 산정에서 빠지는데, 왜 빠졌는지를 화면이 안 말하면 「단가가 왜 없지?」가 결함 조사로 번진다. */
export function lotCountText(
  m: Pick<CostMaterial, "lot_count" | "price_count" | "stale_count">,
): string {
  const stale = m.stale_count ?? 0;
  if (m.price_count === 0) return "단가 없음 — 원장 연결 또는 수동 입력 필요";
  const manual = m.price_count - m.lot_count - stale;
  const parts = [`로트 ${m.lot_count}건`];
  if (manual > 0) parts.push(`수동 ${manual}건`);
  if (stale > 0) parts.push(`⚠ 원장과 어긋난 연결 ${stale}건 — 최신 단가에서 제외`);
  return parts.join(" · ");
}

/** 재검사 결과의 한 줄 요약 — **어긋남을 한 단어로 접지 않는다**(처방이 저마다 다르다).
 *
 * ★문구(label·detail)는 **백엔드가 준 것을 그대로 쓴다.** 화면이 사유를 자기 말로 다시
 * 지으면 두 벌이 되고 두 벌은 반드시 갈라진다(계약 §2-6과 같은 결). */
export function ledgerCheckText(check: CostLedgerCheck | null | undefined): string | null {
  if (!check || check.ok) return null;
  return `⚠ ${check.label}`;
}

/** 「최신 단가」 칸이 왜 비었나 / 왜 그 값인가. 침묵하지 않는다(계약 §2-7·§9-5). */
export function latestPriceNote(
  m: Pick<CostMaterial, "lot_count" | "price_count" | "stale_count">,
): string | null {
  const stale = m.stale_count ?? 0;
  if (stale === 0) return null;
  if (m.lot_count === 0 && m.price_count === stale) {
    return `최신 단가 없음 — 단가 행 ${stale}건이 전부 원장과 어긋나 근거로 못 쓴다. 아래 이력에서 처분한다.`;
  }
  return `어긋난 연결 ${stale}건은 최신 단가 산정에서 뺐다 — 이력에는 근거로 남아 있다.`;
}

// ══════════════════════════════════════════════════════════════════
// 표시 컴포넌트 (전부 순수 — props만 본다. 테스트가 직접 렌더한다)
// ══════════════════════════════════════════════════════════════════
export function ValuationBadge({ settings }: { settings: CostSetting[] }) {
  const text = valuationBadgeText(settings);
  if (!text) return null;
  const unconfirmed = text.includes("미확인") || text.includes("확인 안 됨");
  return (
    <div
      className={`text-xs px-3 py-1.5 rounded-md border ${
        unconfirmed
          ? "bg-amber-50 border-amber-200 text-amber-800"
          : "bg-gray-50 border-gray-200 text-gray-700"
      }`}
    >
      {unconfirmed ? "⚠ " : ""}
      {text}
    </div>
  );
}

/** 원가 기준 자백 — 「원가 = 부가세 포함(D-CPP-51)」. 화면이 스스로 밝히는 자리다.
 *
 * 안 적히면 「왜 이익률이 낮지?」가 나중에 결함 조사로 번진다(계약 합격 9의 취지). */
export function VatBasisBadge() {
  return (
    <div className="text-xs px-3 py-1.5 rounded-md border bg-blue-50 border-blue-200 text-blue-800">
      원가 = 부가세 포함 — 사내 관리회계 기준(D-CPP-51). 제외값은 옆 칸에 함께 표시한다.
    </div>
  );
}

export function MaterialPriceHistory({
  material,
  onDelete,
  onRefresh,
  busy,
}: {
  material: CostMaterial;
  onDelete?: (priceId: number) => void;
  /** 어긋난 원장 행을 원장 현재값으로 다시 맞춘다(적대 리뷰 1R P1-2). */
  onRefresh?: (priceId: number) => void;
  busy?: boolean;
}) {
  if (material.prices.length === 0) {
    return (
      <div className="text-sm text-gray-500 py-3">
        단가 이력이 없다 — 아래 「원장 부자재 라인」에서 연결하거나 수동 단가를 입력한다.
        <span className="text-gray-400"> (빈 칸이지 0원이 아니다)</span>
      </div>
    );
  }
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-gray-500 border-b">
          <th className="py-1.5 pr-3">기준일</th>
          <th className="py-1.5 pr-3">출처</th>
          <th className="py-1.5 pr-3">수입건</th>
          <th className="py-1.5 pr-3">공급처</th>
          <th className="py-1.5 pr-3 text-right">단가(VAT 포함)</th>
          <th className="py-1.5 pr-3 text-right">단가(VAT 제외)</th>
          <th className="py-1.5 pr-3">원장 대조</th>
          {onDelete || onRefresh ? <th className="py-1.5" /> : null}
        </tr>
      </thead>
      <tbody>
        {material.prices.map((p: CostMaterialPrice) => {
          const check = p.ledger_check;
          const warn = ledgerCheckText(check);
          return (
            <tr
              key={p.id}
              className={`border-b last:border-0 ${warn ? "bg-amber-50" : ""}`}
              data-testid={`price-row-${p.id}`}
            >
              <td className="py-1.5 pr-3">{p.effective_date ?? "—"}</td>
              <td className="py-1.5 pr-3">{priceSourceLabel(p.source)}</td>
              <td className="py-1.5 pr-3">
                {p.shipment ? (
                  <span title={`수입건 id=${p.shipment.id}`}>{p.shipment.hbl_no}</span>
                ) : (
                  "—"
                )}
              </td>
              <td className="py-1.5 pr-3">{p.supplier ?? "—"}</td>
              <td className="py-1.5 pr-3 text-right font-medium">
                {formatCostWon(p.unit_price_inc_vat)}
              </td>
              <td className="py-1.5 pr-3 text-right text-gray-500">
                {formatCostWon(p.unit_price_ex_vat)}
              </td>
              {/* ★재검사 칸 — 「보존된 값이 지금도 유효한가」. 이 칸이 없으면 낡은 값이
                  「최신 확정 로트 단가」인 척 앉아 있는다(적대 리뷰 1R P1). */}
              <td className="py-1.5 pr-3" data-testid={`price-check-${p.id}`}>
                {warn ? (
                  <span className="text-amber-800">
                    {warn}
                    <span className="block text-[11px] text-gray-600">{check.detail}</span>
                    {check.ledger_unit_price_ex_vat ? (
                      <span className="block text-[11px] text-gray-600">
                        현 원장값(VAT 제외): {formatCostWon(check.ledger_unit_price_ex_vat)}
                      </span>
                    ) : null}
                  </span>
                ) : (
                  <span className="text-gray-500">{check?.label ?? "—"}</span>
                )}
              </td>
              {onDelete || onRefresh ? (
                <td className="py-1.5 text-right whitespace-nowrap">
                  {warn && check.refreshable && onRefresh ? (
                    <button
                      className="text-xs text-blue-600 hover:underline disabled:opacity-40 mr-2"
                      disabled={busy}
                      onClick={() => onRefresh(p.id)}
                    >
                      갱신
                    </button>
                  ) : null}
                  {onDelete ? (
                    <button
                      className="text-xs text-red-600 hover:underline disabled:opacity-40"
                      disabled={busy}
                      onClick={() => onDelete(p.id)}
                    >
                      해제
                    </button>
                  ) : null}
                </td>
              ) : null}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function MaterialList({
  materials,
  selectedId,
  onSelect,
  onApprove,
  busy,
}: {
  materials: CostMaterial[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  onApprove?: (m: CostMaterial) => void;
  busy?: boolean;
}) {
  if (materials.length === 0) {
    return <div className="text-sm text-gray-500 py-3">등록된 부자재 종이 없다.</div>;
  }
  return (
    <ul className="divide-y">
      {materials.map((m) => (
        <li
          key={m.id}
          data-testid={`material-${m.id}`}
          className={`py-2 px-2 cursor-pointer rounded ${
            selectedId === m.id ? "bg-blue-50" : "hover:bg-gray-50"
          }`}
          onClick={() => onSelect(m.id)}
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm font-medium">{m.name}</span>
            <span
              className={`text-[11px] px-1.5 py-0.5 rounded ${
                m.status === "approved"
                  ? "bg-green-100 text-green-700"
                  : "bg-amber-100 text-amber-800"
              }`}
            >
              {materialStatusLabel(m.status)}
            </span>
          </div>
          <div className="text-xs text-gray-500 mt-0.5">
            {/* 최신 단가 칸 — 없으면 「—」다(계약 §2-7). 테스트가 이 칸을 집는다. */}
            <span data-testid={`material-${m.id}-latest`}>
              {formatCostWon(m.latest_price_inc_vat)}
            </span>
            <span className={m.stale_count > 0 ? "text-amber-700" : "text-gray-400"}>
              {" "}
              · {lotCountText(m)}
            </span>
          </div>
          {onApprove && m.status !== "approved" ? (
            <button
              className="mt-1 text-[11px] text-blue-600 hover:underline disabled:opacity-40"
              disabled={busy}
              onClick={(e) => {
                e.stopPropagation();
                onApprove(m);
              }}
            >
              승인
            </button>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

/** 원장의 부자재 라인 — **미매칭도 빠짐없이 그린다.**
 *
 * ★「연결」 버튼이 이 화면의 요점이다: 제안은 이유를 적어 줄 뿐이고, 링크는 사람이 누를 때만
 *   생긴다(계약 §5-2). 버튼을 지우면 이 층은 원장에서 단가를 못 받는다 — 그 변이를 테스트가
 *   죽인다. */
export function LedgerMaterialLines({
  rows,
  materials,
  onLink,
  busy,
}: {
  rows: CostLedgerMaterialLine[];
  materials: CostMaterial[];
  onLink?: (materialId: number, lineId: number) => void;
  busy?: boolean;
}) {
  if (rows.length === 0) {
    return (
      <div className="text-sm text-gray-500 py-3">
        확정된 수입건에 부자재(`material`) 라인이 없다. 원장에서 분류를 먼저 확인한다.
      </div>
    );
  }
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-gray-500 border-b">
          <th className="py-1.5 pr-3">통관일</th>
          <th className="py-1.5 pr-3">수입건</th>
          <th className="py-1.5 pr-3">품목명</th>
          <th className="py-1.5 pr-3 text-right">수량</th>
          <th className="py-1.5 pr-3 text-right">단가(포함)</th>
          <th className="py-1.5 pr-3 text-right">단가(제외)</th>
          <th className="py-1.5 pr-3">상태</th>
          <th className="py-1.5" />
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const suggested = r.suggestion.material_id;
          const suggestedName = materials.find((m) => m.id === suggested)?.name ?? null;
          return (
            <tr key={r.line_id} className="border-b last:border-0" data-testid={`ledger-line-${r.line_id}`}>
              <td className="py-1.5 pr-3">{r.declaration_date ?? "—"}</td>
              <td className="py-1.5 pr-3">
                {r.hbl_no}
                {/* ★확정이 풀린 수입건은 그렇다고 말한다 — 초판은 이 행을 목록에서 통째로
                    빼서, 어긋났다는 사실이 화면에서 사라졌다(적대 리뷰 1R P1-1). */}
                {r.shipment_status !== "confirmed" ? (
                  <span className="block text-[11px] text-red-600">
                    ⚠ 확정 해제됨({r.shipment_status}) — 원장이 단가를 지운 상태다
                  </span>
                ) : null}
              </td>
              <td className="py-1.5 pr-3">{r.item_name}</td>
              <td className="py-1.5 pr-3 text-right">{r.quantity ?? "—"}</td>
              <td className="py-1.5 pr-3 text-right">{formatCostWon(r.unit_cost_inc_vat)}</td>
              <td className="py-1.5 pr-3 text-right text-gray-500">
                {formatCostWon(r.unit_cost_ex_vat)}
              </td>
              <td className="py-1.5 pr-3">
                {r.linked_material_id ? (
                  ledgerCheckText(r.linked_price_check) ? (
                    <span className="text-amber-800">
                      연결됨 · {r.linked_material_name}
                      <span className="block text-[11px]">
                        {ledgerCheckText(r.linked_price_check)}
                      </span>
                    </span>
                  ) : (
                    <span className="text-green-700">연결됨 · {r.linked_material_name}</span>
                  )
                ) : (
                  <span className={r.suggestion.unmatched ? "text-red-600" : "text-amber-700"}>
                    {r.suggestion.unmatched ? "미매칭" : "미연결"}
                    <span className="block text-[11px] text-gray-500">{r.suggestion.reason}</span>
                  </span>
                )}
              </td>
              <td className="py-1.5 text-right">
                {!r.linked_material_id && suggested && onLink ? (
                  <button
                    className="text-xs px-2 py-1 rounded bg-blue-600 text-white disabled:opacity-40"
                    disabled={busy}
                    onClick={() => onLink(suggested, r.line_id)}
                  >
                    「{suggestedName}」로 연결
                  </button>
                ) : null}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** S2·S3 몫인 탭의 빈 상태 — «아직 없음»을 말한다. 그냥 비어 있는 것과 다르다. */
export function NotYetPanel({ what, slice }: { what: string; slice: string }) {
  return (
    <div className="text-sm text-gray-500 border border-dashed rounded-md p-8 text-center">
      <div className="font-medium text-gray-700">{what}</div>
      <div className="mt-1">{slice}에서 만든다 — 지금은 계산하지 않는다(빈 칸이지 0이 아니다).</div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════
// 페이지 (데이터 로딩만 — 표시는 위 순수 컴포넌트가 한다)
// ══════════════════════════════════════════════════════════════════
export default function CostPage() {
  const [tab, setTab] = useState<CostTab>("materials");
  const [materials, setMaterials] = useState<CostMaterial[]>([]);
  const [ledgerLines, setLedgerLines] = useState<CostLedgerMaterialLine[]>([]);
  const [settings, setSettings] = useState<CostSetting[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [m, l, s] = await Promise.all([
        fetchCostMaterials(),
        fetchCostLedgerMaterialLines(),
        fetchCostSettings(),
      ]);
      setMaterials(m.items);
      setLedgerLines(l.items);
      setSettings(s.items);
      setSelectedId((prev) => prev ?? (m.items.length ? m.items[0].id : null));
      setErr(null);
    } catch (e) {
      // ★조용히 빈 화면을 주지 않는다 — 실패는 실패라고 말한다(교훈 #319).
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const selected = useMemo(
    () => materials.find((m) => m.id === selectedId) ?? null,
    [materials, selectedId],
  );

  async function run(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      await load();
      setMsg(ok);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="p-6 max-w-6xl">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h1 className="text-xl font-semibold">💰 원가</h1>
        <div className="flex items-center gap-2 flex-wrap">
          <VatBasisBadge />
          <ValuationBadge settings={settings} />
        </div>
      </div>
      <p className="text-xs text-gray-500 mt-2">
        표준원가는 참고치다 — 손익 엔진 반영(컷오버)은 계약 C 몫이고, 이 화면은{" "}
        <code>product_master.cost_price</code>를 바꾸지 않는다.
      </p>

      <div className="flex gap-1 mt-4 border-b">
        {(
          [
            ["materials", "부자재"],
            ["recipes", "레시피"],
            ["board", "표준원가 보드"],
          ] as [CostTab, string][]
        ).map(([k, label]) => (
          <button
            key={k}
            onClick={() => setTab(k)}
            className={`px-3 py-2 text-sm border-b-2 -mb-px ${
              tab === k
                ? "border-blue-600 text-blue-700 font-medium"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {err ? (
        <div className="mt-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2">
          {err}
        </div>
      ) : null}
      {msg ? (
        <div className="mt-3 text-sm text-green-700 bg-green-50 border border-green-200 rounded p-2">
          {msg}
        </div>
      ) : null}

      {tab === "materials" ? (
        <div className="mt-4 grid grid-cols-1 md:grid-cols-[260px_1fr] gap-6">
          <div>
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-gray-700">부자재 종</h2>
              <button
                className="text-xs text-blue-600 hover:underline disabled:opacity-40"
                disabled={busy}
                onClick={() => {
                  const name = window.prompt("새 부자재 종 이름");
                  if (!name) return;
                  void run(() => createCostMaterial({ name }), `「${name}」 추가됨`);
                }}
              >
                + 종 추가
              </button>
            </div>
            <MaterialList
              materials={materials}
              selectedId={selectedId}
              onSelect={setSelectedId}
              busy={busy}
              onApprove={(m) =>
                run(
                  () => patchCostMaterial(m.id, { status: "approved" }),
                  `「${m.name}」 승인됨`,
                )
              }
            />
          </div>

          <div className="space-y-6">
            {selected ? (
              <section>
                <h2 className="text-sm font-semibold text-gray-700">
                  「{selected.name}」 단가 이력
                </h2>
                <div className="text-xs text-gray-500 mt-0.5">
                  엑셀 대응: {excelLabelText(selected.excel_label)} · {lotCountText(selected)}
                </div>
                {latestPriceNote(selected) ? (
                  <div className="text-xs text-amber-800 mt-1" data-testid="latest-price-note">
                    {latestPriceNote(selected)}
                  </div>
                ) : null}
                <div className="mt-2">
                  <MaterialPriceHistory
                    material={selected}
                    busy={busy}
                    onDelete={(priceId) =>
                      run(
                        () => deleteCostMaterialPrice(selected.id, priceId),
                        "단가 행을 해제했다",
                      )
                    }
                    onRefresh={(priceId) =>
                      run(
                        () => refreshCostLedgerPrice(selected.id, priceId),
                        "원장 현재값으로 갱신했다 (이전 값은 비고에 남는다)",
                      )
                    }
                  />
                </div>
                <button
                  className="mt-2 text-xs text-blue-600 hover:underline disabled:opacity-40"
                  disabled={busy}
                  onClick={() => {
                    const v = window.prompt("수동 단가 (VAT 제외, 원). 모르면 취소한다.");
                    if (!v) return;
                    const supplier = window.prompt("공급처 (예: 조아테크). 없으면 비워 둔다.");
                    void run(
                      () =>
                        addCostManualPrice(selected.id, {
                          unit_price_ex_vat: v,
                          supplier: supplier || null,
                        }),
                      "수동 단가를 입력했다",
                    );
                  }}
                >
                  + 수동 단가 입력
                </button>
              </section>
            ) : null}

            <section>
              <h2 className="text-sm font-semibold text-gray-700">
                원장 부자재 라인 (확정된 수입건 + 이미 연결된 라인)
              </h2>
              <div className="text-xs text-gray-500 mt-0.5">
                제안은 제안이다 — 「연결」을 눌러야 단가 이력이 생긴다(확정은 사람). 이미 연결한
                라인은 수입건의 확정이 풀려도 목록에 남는다 — 사라지면 어긋남이 안 보인다.
              </div>
              <div className="mt-2">
                <LedgerMaterialLines
                  rows={ledgerLines}
                  materials={materials}
                  busy={busy}
                  onLink={(materialId, lineId) =>
                    run(
                      () => linkCostLedgerPrice(materialId, lineId),
                      "원장 로트를 부자재에 연결했다",
                    )
                  }
                />
              </div>
            </section>
          </div>
        </div>
      ) : null}

      {tab === "recipes" ? (
        <div className="mt-4">
          <NotYetPanel what="레시피 (상품명 × 폼팩터 구성)" slice="S2" />
        </div>
      ) : null}
      {tab === "board" ? (
        <div className="mt-4">
          <NotYetPanel what="표준원가 보드" slice="S2·S3" />
        </div>
      ) : null}
    </div>
  );
}
