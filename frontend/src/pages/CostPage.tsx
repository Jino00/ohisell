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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  addCostManualPrice,
  adoptCostExcelPrices,
  approveCostRecipe,
  createCostMaterial,
  deleteCostMaterialPrice,
  fetchCostBoard,
  fetchCostLedgerMaterialLines,
  fetchCostMaterials,
  fetchCostRecipes,
  fetchCostSettings,
  importCostRecipes,
  linkCostLedgerPrice,
  patchCostMaterial,
  refreshCostLedgerPrice,
  unapproveCostRecipe,
  type CostBoard,
  type CostBoardRow,
  type CostImportResult,
  type CostLedgerCheck,
  type CostLedgerMaterialLine,
  type CostMaterial,
  type CostMaterialPrice,
  type CostRecipe,
  type CostRecipeMatch,
  type CostSetting,
  type CostStandard,
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
// S3 — 제품 → 옵션 2단 드롭다운 필터 (Jino: "제품, 옵션 구조로 … 찾는게 쉽지 않네")
//
// 병목은 계산이 아니라 «찾기»다 — 보드 924행 · 레시피 100건을 눈으로 훑을 수 없다.
// 이 컴포넌트는 순수 표시/선택 계층이다: 집계(제품별 건수 세기 등)는 호출부가 하고,
// 여기는 검색·선택·초기화만 담당한다(다른 순수 컴포넌트들과 같은 결).
// ══════════════════════════════════════════════════════════════════

export interface PickerItem {
  value: string;
  label: string;
  count?: number;
}

/** 대소문자 무시·공백 무시 부분일치. */
function normalizeForSearch(s: string): string {
  return s.toLowerCase().replace(/\s+/g, "");
}

/** 제품 검색어로 제품 목록을 좁힌다 — 셀렉트 하나로는 88~100종을 못 찾는다는 게
 * Jino가 실제로 겪은 문제다. */
export function filterPickerItems(items: PickerItem[], search: string): PickerItem[] {
  const q = search.trim();
  if (!q) return items;
  const needle = normalizeForSearch(q);
  return items.filter((it) => normalizeForSearch(it.label).includes(needle));
}

export function ProductOptionPicker({
  idPrefix,
  productLabel = "제품",
  optionLabel = "옵션",
  products,
  options,
  optionTotalCount,
  productValue,
  optionValue,
  onProductChange,
  onOptionChange,
  onReset,
}: {
  /** 렌더 인스턴스마다 다른 `data-testid` 접두사(보드 탭 · 레시피 탭이 동시에 이 컴포넌트를 쓴다). */
  idPrefix: string;
  productLabel?: string;
  optionLabel?: string;
  /** 이미 건수까지 집계된 제품 목록(호출부가 만든다). */
  products: PickerItem[];
  /** 선택된 제품에 속한 옵션 목록. 제품 미선택이면 빈 배열을 넘긴다. */
  options: PickerItem[];
  /** 옵션 셀렉트 「전체 (N건)」의 N. */
  optionTotalCount: number;
  productValue: string | null;
  optionValue: string | null;
  onProductChange: (value: string | null) => void;
  onOptionChange: (value: string | null) => void;
  onReset: () => void;
}) {
  const [search, setSearch] = useState("");
  const filteredProducts = useMemo(() => {
    const base = filterPickerItems(products, search);
    // ★유령 선택 방지(적대 리뷰 1R P2-D): 검색어가 좁혀도 «이미 선택된» 제품은 목록에서
    //   빠지면 안 된다. 안 빠지게 하지 않으면 <select>의 value가 목록에 없는 상태가 되어
    //   브라우저가 빈 값처럼 그린다 — 필터링 자체는 여전히 그 제품 기준으로 맞게 도는데
    //   화면만 「아무것도 안 골랐다」고 거짓말한다(상태 ≠ 표시).
    if (productValue && !base.some((p) => p.value === productValue)) {
      const pinned = products.find((p) => p.value === productValue);
      if (pinned) return [pinned, ...base];
    }
    return base;
  }, [products, search, productValue]);

  return (
    // ★`min-w-0`이 없으면 그리드/플렉스 자식은 기본 `min-width: auto`라 **줄어들지 못하고**
    //   고정폭 컨트롤이 칸을 뚫고 옆 패널로 삐져나온다(레시피 탭은 왼쪽이 320px다).
    //   그래서 폭을 «고정»하지 않고 «채우고 넘치면 접히게» 한다 — 이 바는 보드 탭(넓다)과
    //   레시피 탭(좁다) 둘 다에 놓이므로 어느 쪽에도 못 박으면 안 된다.
    <div className="flex flex-wrap items-end gap-2 border rounded-md p-2 bg-gray-50 min-w-0">
      <div className="flex flex-col gap-1 min-w-0 flex-1 basis-48">
        <label className="text-[11px] text-gray-500" htmlFor={`${idPrefix}-product-search`}>
          {productLabel} 검색
        </label>
        <input
          id={`${idPrefix}-product-search`}
          type="text"
          value={search}
          placeholder={`${productLabel}명으로 찾기`}
          className="text-xs border rounded px-2 py-1 w-full min-w-0"
          data-testid={`${idPrefix}-product-search`}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      <div className="flex flex-col gap-1 min-w-0 flex-1 basis-56">
        <label className="text-[11px] text-gray-500" htmlFor={`${idPrefix}-product-select`}>
          {productLabel}
        </label>
        <select
          id={`${idPrefix}-product-select`}
          className="text-xs border rounded px-2 py-1 w-full min-w-0"
          value={productValue ?? ""}
          data-testid={`${idPrefix}-product-select`}
          onChange={(e) => onProductChange(e.target.value || null)}
        >
          <option value="">전체</option>
          {filteredProducts.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label} ({p.count ?? 0})
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1 min-w-0 flex-1 basis-56">
        <label className="text-[11px] text-gray-500" htmlFor={`${idPrefix}-option-select`}>
          {optionLabel}
        </label>
        <select
          id={`${idPrefix}-option-select`}
          className="text-xs border rounded px-2 py-1 w-full min-w-0 disabled:bg-gray-100 disabled:text-gray-400"
          value={productValue ? (optionValue ?? "") : ""}
          disabled={!productValue}
          data-testid={`${idPrefix}-option-select`}
          onChange={(e) => onOptionChange(e.target.value || null)}
        >
          {!productValue ? (
            <option value="">먼저 {productLabel}을 고르세요</option>
          ) : (
            <>
              <option value="">전체 ({optionTotalCount}건)</option>
              {options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </>
          )}
        </select>
      </div>
      <button
        type="button"
        className="text-xs px-2 py-1 rounded border text-gray-600 hover:bg-gray-50 disabled:opacity-40"
        data-testid={`${idPrefix}-picker-reset`}
        onClick={() => {
          setSearch("");
          onReset();
        }}
      >
        초기화
      </button>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════
// S2 — 레시피 · 표준원가 보드 (계약 §5-3 탭2·탭3)
// ══════════════════════════════════════════════════════════════════

/** 폼팩터 표시. `null`(수입 완제품·매입품)은 「—」다 — 0이나 자리표시자로 채우지 않는다. */
export function formFactorLabel(form: string | null): string {
  return form && form.trim() ? form : "—";
}

/**
 * ★레시피 상세 패널이 «어떤 레시피를 가리키는가»의 유일한 진실의 원천.
 *
 * 필터(제품/폼팩터)가 바뀌거나 데이터가 재조회돼 `filtered`가 달라질 때마다 이 함수
 * 하나로 다음 선택을 다시 계산한다 — 호출하는 자리가 둘(필터 변경 · 데이터 재조회)이어도
 * 로직은 여기 하나뿐이라 서로 다른 기준으로 같은 상태를 다투지 않는다.
 *
 * - 현재 선택이 `filtered` 안에 여전히 있으면 그대로 유지한다(승인 직후 재조회돼도
 *   방금 승인한 레시피를 계속 보여주기 위해서다).
 * - 없으면(필터가 바뀌었거나 레시피 자체가 사라졌으면) `filtered`의 첫 항목으로 스냅한다.
 * - `filtered`가 0건이면 `null`이다 — 있지도 않은 레시피를 붙들고 있지 않는다.
 */
export function reconcileSelectedRecipeId(
  filtered: CostRecipe[],
  currentId: number | null,
): number | null {
  if (currentId !== null && filtered.some((r) => r.id === currentId)) return currentId;
  return filtered.length ? filtered[0].id : null;
}

/** 격차 표시. `null`은 「—」다 — 「격차 0%」와 「잴 수 없음」은 다른 사실이다. */
export function gapText(gap: number | null): string {
  if (gap === null || gap === undefined || !Number.isFinite(gap)) return "—";
  const sign = gap > 0 ? "+" : "";
  return `${sign}${gap.toFixed(2)}%`;
}

/** 왜 표준원가가 «없는지». ★빈 문자열을 돌려주지 않는다 — 이유 없는 빈 칸이 이 화면의 적이다. */
export function uncomputedReason(row: CostBoardRow): string | null {
  if (row.std_cost_inc_vat !== null) return null;
  return row.reason ?? "계산 안 됨 — 사유 미상";
}

/** 단가 상태 → 사람 말. ★상태 이름(`material_unapproved`)을 그대로 그리면 사람은
 * 무엇을 해야 할지 모른다 — 움직일 수 없는 자백은 자백이 아니라 장식이다(적대 리뷰 1R P1-1). */
export const PRICE_STATUS_LABEL: Record<string, string> = {
  ok: "원장",
  manual: "수동 입력",
  missing: "단가 없음",
  unconfirmed: "수입건 확정 해제",
  changed: "원장 값 달라짐 — 「갱신」",
  item_mismatch: "품목 바뀜 — 해제 후 재연결",
  material_unapproved: "종 미승인 — 부자재 탭에서 「승인」",
};

export function priceStatusLabel(status: string): string {
  return PRICE_STATUS_LABEL[status] ?? status;
}

/** 매칭 근거 한 줄. 제안이지 확정이 아님을 문장이 스스로 말한다. */
export function matchReasonText(match: CostRecipeMatch | null): string {
  if (!match || !match.match_reason) return "매칭 근거 없음";
  return match.match_reason;
}

export function RecipeStatusBadge({ recipe }: { recipe: CostRecipe }) {
  const approved = recipe.status === "approved";
  return (
    <span
      className={`text-xs px-1.5 py-0.5 rounded border ${
        approved
          ? "bg-green-50 text-green-700 border-green-200"
          : "bg-amber-50 text-amber-800 border-amber-200"
      }`}
    >
      {approved ? "승인됨" : "미확인 — 계산 안 함"}
    </span>
  );
}

/** `.xlsx`인지 그 자리에서 판별한다. 서버 400까지 가기 전에 화면이 먼저 사유를 말한다.
 *
 * ★사유는 「무엇을 해야 하는지」를 같이 말한다(교훈 #349 — 사유가 틀리면 사람이 틀린 일을 한다).
 * 확장자만 보고 파일명을 그대로 되돌려줘 「받은 것이 무엇인지」가 사람 눈에 바로 보이게 한다. */
export function validateCostExcelFile(file: File): string | null {
  if (!file.name.toLowerCase().endsWith(".xlsx")) {
    return `.xlsx 파일이 아닙니다 (받은 것: ${file.name}) — 엑셀에서 「다른 이름으로 저장」 → 파일 형식을 xlsx로 바꿔 다시 올리세요.`;
  }
  return null;
}

/** 선택 한 번이 만드는 «할 말»을 전부 모은다 — 거부 사유 + 부작용 고지.
 *
 * ★거부만 말하고 «그래서 이전 선택이 사라졌다»를 안 말하면, 사람은 멀쩡한 파일이
 *   아직 들어 있는 줄 알고 다음 단계로 간다. 부작용을 감추는 사유는 틀린 사유다
 *   (교훈 #349의 같은 결 — 적대 리뷰 1R P2-1·P2-2 채택, 2026-08-23). */
export function buildSelectNotes(
  problem: string | null,
  had: File | null,
  droppedCount: number,
  file: File,
): { message: string | null; rejected: boolean } {
  const parts: string[] = [];
  if (problem) {
    parts.push(problem);
    if (had) parts.push(`앞서 고른 「${had.name}」은 취소됐습니다 — 다시 골라 주세요.`);
  } else if (droppedCount > 1) {
    // 거부가 아니다. 받아들이되 «나머지를 안 받았다»는 사실을 말한다.
    parts.push(`한 칸은 파일 하나만 받습니다 — ${droppedCount}개 중 「${file.name}」만 골랐습니다.`);
  }
  return { message: parts.length ? parts.join(" ") : null, rejected: Boolean(problem) };
}

/** 파일 크기를 KB/MB로 사람이 읽게 바꾼다. */
export function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes}B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)}KB`;
  return `${(kb / 1024).toFixed(2)}MB`;
}

/** 「초안 만들기」가 왜 비활성인지 — 흐려지기만 하면 사람은 무엇을 해야 할지 모른다. */
export function importDisabledReason(cost: File | null, mapping: File | null): string | null {
  if (!cost && !mapping) return "엑셀 2종을 모두 고르세요 — 원가 정본 · 매핑 정본";
  if (!cost) return "원가 정본을 아직 고르지 않았습니다";
  if (!mapping) return "매핑 정본을 아직 고르지 않았습니다";
  return null;
}

/** 카드형 드롭존 하나 — 클릭·드래그·키보드 셋 다로 파일을 고를 수 있다.
 *
 * ★숨은 `<input type="file">`의 `aria-label`은 그대로 유지한다 — 접근성·기존 테스트 훅이다.
 * 카드 자체는 `role="button"` + `tabIndex`로 Enter/Space 선택을 받는다. */
function CostExcelDropZone({
  slot,
  title,
  sheetHint,
  exampleHint,
  ariaLabel,
  file,
  error,
  busy,
  onSelect,
  onClear,
}: {
  slot: "cost" | "mapping";
  title: string;
  sheetHint: string;
  exampleHint: string;
  ariaLabel: string;
  file: File | null;
  error: string | null;
  busy: boolean;
  onSelect: (file: File, droppedCount?: number) => void;
  onClear: () => void;
}) {
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const openPicker = () => {
    if (busy) return;
    inputRef.current?.click();
  };

  return (
    <div
      role="button"
      tabIndex={busy ? -1 : 0}
      aria-label={`${title} 선택 영역 — ${sheetHint}`}
      data-testid={`cost-dropzone-${slot}`}
      onClick={openPicker}
      onKeyDown={(e) => {
        // ★카드 «자신»에 포커스가 있을 때만 받는다. 이 가드가 없으면 카드가 role="button"이라
        //   중첩된 「바꾸기」·「지우기」의 Enter/Space를 가로채 preventDefault로 죽이고 대신
        //   파일 선택창을 연다 — 「지우기」는 목적이 정반대라 **키보드로는 영영 안 지워진다**.
        //   (적대 리뷰 1R P1, 2026-08-23. 「바꾸기」는 우연히 목적이 같아 증상이 안 보였다.)
        if (e.target !== e.currentTarget) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openPicker();
        }
      }}
      onDragOver={(e) => {
        e.preventDefault();
        e.stopPropagation();
        if (!busy) setDragOver(true);
      }}
      onDragLeave={(e) => {
        e.preventDefault();
        e.stopPropagation();
        setDragOver(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        e.stopPropagation();
        setDragOver(false);
        if (busy) return;
        const files = e.dataTransfer.files;
        const f = files?.[0];
        // ★한 칸은 파일 하나만 받는다. 나머지를 «조용히» 버리면 사람은 둘 다 올린 줄 안다.
        if (f) onSelect(f, files?.length ?? 1);
      }}
      className={`relative flex flex-col gap-1 rounded-md border-2 border-dashed p-3 select-none ${
        busy ? "cursor-not-allowed" : "cursor-pointer"
      } transition-colors ${
        error
          ? "border-red-300 bg-red-50"
          : dragOver
            ? "border-blue-400 bg-blue-50"
            : busy
              ? "border-gray-200 bg-gray-50"
              : "border-gray-300 bg-gray-50 hover:border-blue-300 hover:bg-blue-50"
      }`}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".xlsx"
        aria-label={ariaLabel}
        className="sr-only"
        disabled={busy}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onSelect(f);
          // 같은 파일을 다시 골라도 onChange가 뜨도록 값을 비운다.
          e.target.value = "";
        }}
      />
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-gray-700">{title}</span>
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-200 text-gray-600 shrink-0">
          .xlsx만
        </span>
      </div>
      <div className="text-[11px] text-gray-500">{sheetHint}</div>

      {file ? (
        <div className="mt-1 flex items-center justify-between gap-2 rounded border bg-white px-2 py-1.5">
          <div className="min-w-0">
            <div className="text-xs text-gray-800 truncate" title={file.name}>
              {file.name}
            </div>
            <div className="text-[11px] text-gray-400">{formatFileSize(file.size)}</div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              className="text-[11px] text-blue-600 hover:underline disabled:opacity-40"
              disabled={busy}
              onClick={(e) => {
                e.stopPropagation();
                openPicker();
              }}
            >
              바꾸기
            </button>
            <button
              type="button"
              className="text-[11px] text-gray-500 hover:underline disabled:opacity-40"
              disabled={busy}
              onClick={(e) => {
                e.stopPropagation();
                onClear();
              }}
            >
              지우기
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="text-[11px] text-gray-400 truncate">예: {exampleHint}</div>
          <div className={`mt-0.5 text-[11px] ${dragOver ? "text-blue-600" : "text-gray-400"}`}>
            {dragOver ? "여기에 놓으세요" : "클릭하거나 파일을 끌어다 놓으세요"}
          </div>
        </>
      )}

      {error ? (
        <div
          className="mt-1 text-[11px] text-red-700"
          data-testid={`cost-dropzone-${slot}-error`}
        >
          ⚠ {error}
        </div>
      ) : null}
    </div>
  );
}

/** 두 엑셀 업로드. **아무것도 승인하지 않는다**는 것을 화면이 먼저 말한다. */
export function RecipeImportPanel({
  busy,
  onImport,
  result,
}: {
  busy: boolean;
  onImport: (cost: File, mapping: File) => void;
  result: CostImportResult | null;
}) {
  const [cost, setCost] = useState<File | null>(null);
  const [mapping, setMapping] = useState<File | null>(null);
  const [costError, setCostError] = useState<string | null>(null);
  const [mappingError, setMappingError] = useState<string | null>(null);

  const handleSelect = (slot: "cost" | "mapping", file: File, droppedCount = 1) => {
    const had = slot === "cost" ? cost : mapping;
    const notes = buildSelectNotes(validateCostExcelFile(file), had, droppedCount, file);
    if (slot === "cost") {
      setCostError(notes.message);
      setCost(notes.rejected ? null : file);
    } else {
      setMappingError(notes.message);
      setMapping(notes.rejected ? null : file);
    }
  };

  const disabledReason = importDisabledReason(cost, mapping);

  return (
    <section
      className="border rounded-md p-4"
      // ★실수로 카드 밖에 떨어뜨려도 브라우저가 그 파일을 열어 페이지를 이탈하지 않게 한다.
      //   카드 밖은 조용히 무시한다 — 카드 안의 드롭은 카드가 stopPropagation으로 먼저 받는다.
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => e.preventDefault()}
    >
      <h2 className="text-sm font-semibold text-gray-700">엑셀 2종 업로드 → 구성 초안</h2>
      <p className="text-xs text-gray-500 mt-1">
        파싱은 <b>구성(부자재 목록·수량)</b>까지다 — 엑셀의 단가는 <b>참고값</b>으로만 실리고,
        승인·채택을 눌러야 단가가 된다(계약 §3). 이미 승인된 레시피는 덮지 않는다.
      </p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <CostExcelDropZone
          slot="cost"
          title="원가 정본"
          sheetHint="「제품 원가표」 시트"
          exampleHint="MD_원가 계산_….xlsx"
          ariaLabel="원가 정본 파일"
          file={cost}
          error={costError}
          busy={busy}
          onSelect={(f, n) => handleSelect("cost", f, n)}
          onClear={() => {
            setCost(null);
            setCostError(null);
          }}
        />
        <CostExcelDropZone
          slot="mapping"
          title="매핑 정본"
          sheetHint="「원가 매핑」 시트"
          exampleHint="ohisell_mapping_template_….xlsx"
          ariaLabel="매핑 정본 파일"
          file={mapping}
          error={mappingError}
          busy={busy}
          onSelect={(f, n) => handleSelect("mapping", f, n)}
          onClear={() => {
            setMapping(null);
            setMappingError(null);
          }}
        />
      </div>

      {disabledReason ? (
        <div className="mt-2 text-xs text-amber-700" data-testid="import-disabled-reason">
          {disabledReason}
        </div>
      ) : null}
      <button
        className="mt-2 text-xs px-3 py-1.5 rounded bg-blue-600 text-white disabled:opacity-40"
        disabled={busy || !cost || !mapping}
        onClick={() => cost && mapping && onImport(cost, mapping)}
      >
        초안 만들기
      </button>

      {result ? (
        <div className="mt-3 text-xs text-gray-700 space-y-1">
          <div>
            레시피 신규 <b>{result.recipes_created}</b> · 갱신 <b>{result.recipes_updated}</b> ·
            승인분 건너뜀 <b>{result.skipped_approved}</b> · 구성 못 찾음{" "}
            <b>{result.unmatched}</b> (묶음 {result.groups})
          </div>
          {/* ★이상은 숨기지 않는다 — 「몇 건 파싱됨」만 보이면 무엇이 빠졌는지 모른다. */}
          {result.cost_table_anomalies.length ? (
            <details>
              <summary className="cursor-pointer text-amber-700">
                원가표 이상 {result.cost_table_anomalies.length}건
              </summary>
              <ul className="mt-1 ml-4 list-disc text-gray-600">
                {result.cost_table_anomalies.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            </details>
          ) : null}
          {result.mapping_anomalies.length ? (
            <details>
              <summary className="cursor-pointer text-amber-700">
                매핑 이상 {result.mapping_anomalies.length}건
              </summary>
              <ul className="mt-1 ml-4 list-disc text-gray-600">
                {result.mapping_anomalies.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

export function RecipeList({
  recipes,
  selectedId,
  onSelect,
  totalCount,
  filterSummary,
}: {
  recipes: CostRecipe[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  /** 필터 적용 전 전체 레시피 건수 — 필터가 걸린 0건과 «원래 없음»을 가른다. */
  totalCount?: number;
  /** 「100건 중 N건 표시 중 — 필터: …」. null/undefined면 필터 없음. */
  filterSummary?: string | null;
}) {
  if (!recipes.length) {
    return (
      <div>
        {filterSummary ? (
          <div className="text-xs text-gray-500 mb-2" data-testid="recipe-filter-summary">
            {filterSummary}
          </div>
        ) : null}
        <div className="text-xs text-gray-500 border border-dashed rounded p-4">
          {totalCount ? (
            "해당 조건에 맞는 레시피가 없다."
          ) : (
            "레시피가 없다 — 위에서 엑셀 2종을 올리면 초안이 생긴다."
          )}
        </div>
      </div>
    );
  }
  return (
    <div>
      {filterSummary ? (
        <div className="text-xs text-gray-500 mb-2" data-testid="recipe-filter-summary">
          {filterSummary}
        </div>
      ) : null}
      <ul className="text-sm divide-y border rounded-md overflow-hidden">
        {recipes.map((r) => (
        <li key={r.id}>
          <button
            data-testid={`recipe-row-${r.id}`}
            className={`w-full text-left px-3 py-2 hover:bg-gray-50 ${
              r.id === selectedId ? "bg-blue-50" : ""
            }`}
            onClick={() => onSelect(r.id)}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate">{r.product_name}</span>
              <span className="text-xs text-gray-500 shrink-0">
                {formFactorLabel(r.form_factor)}
              </span>
            </div>
            <div className="mt-1 flex items-center gap-2 flex-wrap">
              <RecipeStatusBadge recipe={r} />
              <span className="text-xs text-gray-500">
                구성 {r.line_count} · SKU {r.link_count}
              </span>
              <span className="text-xs font-medium">
                {formatCostWon(r.standard.std_cost_inc_vat)}
              </span>
              {r.anomaly_flag ? (
                <span className="text-xs text-amber-700">⚠ {r.anomaly_flag}</span>
              ) : null}
            </div>
          </button>
        </li>
      ))}
      </ul>
    </div>
  );
}

/** ★계약 §7 합격 4의 표면 — 「계산되는 방법이 나오는」 화면. 부자재 × 수량 × 단가가 펼쳐진다. */
export function StandardBreakdown({ standard }: { standard: CostStandard }) {
  if (!standard.lines.length) {
    return <div className="text-xs text-gray-500">구성이 비어 있다 — 계산할 것이 없다.</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-gray-500">
          <tr className="text-left border-b">
            <th className="py-1 pr-2">부자재</th>
            <th className="py-1 pr-2 text-right">수량</th>
            <th className="py-1 pr-2 text-right">단가(VAT 제외)</th>
            <th className="py-1 pr-2 text-right">금액(VAT 제외)</th>
            <th className="py-1 pr-2 text-right">금액(VAT 포함)</th>
            <th className="py-1">상태</th>
          </tr>
        </thead>
        <tbody>
          {standard.lines.map((ln, i) => (
            <tr key={`${ln.label}-${i}`} className="border-b last:border-0">
              <td className="py-1 pr-2">{ln.label}</td>
              <td className="py-1 pr-2 text-right">{ln.quantity ?? "—"}</td>
              <td className="py-1 pr-2 text-right">{formatCostWon(ln.unit_price_ex_vat)}</td>
              <td className="py-1 pr-2 text-right">{formatCostWon(ln.amount_ex_vat)}</td>
              <td className="py-1 pr-2 text-right">
                {formatCostWon(ln.amount_inc_vat)}
                {/* ★유도했다는 사실이 화면까지 온다 — ×1.1은 «실제로 낸 부가세»가 아니다. */}
                {ln.inc_derived ? <span className="text-gray-400"> (유도)</span> : null}
              </td>
              <td className="py-1">
                {ln.usable ? (
                  <span className="text-gray-500">{ln.price_source ?? ln.price_status}</span>
                ) : (
                  <span className="text-amber-700">{priceStatusLabel(ln.price_status)}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="font-medium">
            <td className="pt-2" colSpan={3}>
              합계
            </td>
            <td className="pt-2 text-right">{formatCostWon(standard.std_cost_ex_vat)}</td>
            <td className="pt-2 text-right">{formatCostWon(standard.std_cost_inc_vat)}</td>
            <td />
          </tr>
        </tfoot>
      </table>
      {!standard.computable ? (
        <div className="mt-2 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded p-2">
          {standard.reason ?? "계산 안 됨"} — 부분합{" "}
          {formatCostWon(standard.partial_inc_vat)}은 <b>부분</b>이지 표준원가가 아니다.
        </div>
      ) : null}
    </div>
  );
}

export function RecipeDetail({
  recipe,
  busy,
  onApprove,
  onUnapprove,
  onAdopt,
}: {
  recipe: CostRecipe;
  busy: boolean;
  onApprove: () => void;
  onUnapprove: () => void;
  onAdopt: () => void;
}) {
  const m = recipe.match;
  return (
    <section className="border rounded-md p-4" data-testid="recipe-detail-panel">
      <div className="flex items-start justify-between gap-2 flex-wrap">
        <div>
          <h2 className="text-sm font-semibold text-gray-800">{recipe.product_name}</h2>
          <div className="text-xs text-gray-500 mt-0.5">
            폼팩터 {formFactorLabel(recipe.form_factor)} · 구성 {recipe.line_count}종 · 링크된
            SKU {recipe.link_count}건
          </div>
        </div>
        <RecipeStatusBadge recipe={recipe} />
      </div>

      <div className="mt-3 text-xs bg-gray-50 border rounded p-2 text-gray-700">
        <div className="font-medium text-gray-600">매칭 근거 (제안이지 확정이 아니다)</div>
        <div className="mt-0.5">{matchReasonText(m)}</div>
        {m?.cost_table_item ? (
          <div className="mt-0.5 text-gray-500">
            원가표 「{m.cost_table_item}」 · 제품원가(+VAT){" "}
            {formatCostWon(m.excel_total_inc_vat)}
          </div>
        ) : null}
        {m?.candidates && m.candidates.length > 1 ? (
          <div className="mt-0.5 text-amber-700">후보 {m.candidates.length}건 — 사람이 고른다</div>
        ) : null}
      </div>

      <div className="mt-3 flex gap-2 flex-wrap">
        {recipe.status === "approved" ? (
          <button
            className="text-xs px-3 py-1.5 rounded border disabled:opacity-40"
            disabled={busy}
            onClick={onUnapprove}
          >
            승인 취소
          </button>
        ) : (
          <button
            className="text-xs px-3 py-1.5 rounded bg-green-600 text-white disabled:opacity-40"
            disabled={busy || recipe.line_count === 0}
            onClick={onApprove}
          >
            이 구성을 승인한다
          </button>
        )}
        <button
          className="text-xs px-3 py-1.5 rounded border disabled:opacity-40"
          disabled={busy}
          onClick={onAdopt}
          title="엑셀에 적힌 참고값을 수동 단가로 채택한다. 이미 단가가 있는 종은 건드리지 않는다."
        >
          엑셀 참고값을 단가로 채택
        </button>
      </div>

      <div className="mt-4">
        <h3 className="text-xs font-semibold text-gray-600 mb-1">계산 내역</h3>
        <StandardBreakdown standard={recipe.standard} />
      </div>
    </section>
  );
}

/** 표준원가 보드 — SKU별. ★미계산 행도 빠짐없이 실리고 «왜»를 말한다. */
export function StandardCostBoard({
  board,
  displayItems,
  filterSummary,
}: {
  board: CostBoard | null;
  /** 제품/옵션 필터 적용 후 실제로 그릴 행 — 생략하면 `board.items`를 그대로 쓴다(필터 없음). */
  displayItems?: CostBoardRow[];
  /** 「924건 중 107건 표시 중 — 필터: …」. null/undefined면 필터 없음(조용히 안 숨긴다). */
  filterSummary?: string | null;
}) {
  if (!board) {
    return <div className="text-xs text-gray-500">불러오는 중…</div>;
  }
  if (!board.items.length) {
    return (
      <div className="text-xs text-gray-500 border border-dashed rounded p-4">
        보드에 실릴 SKU가 없다 — 레시피 탭에서 엑셀을 올려 링크를 만든다.
      </div>
    );
  }
  // ★필터가 걸려도 이 총계는 «전체» 기준을 유지한다 — 필터가 924건 자체를 못 보게 만들면
  //   커버리지 착시가 다시 생긴다. 「몇 건 중 몇 건을 보고 있나」는 filterSummary가 따로 말한다.
  const items = displayItems ?? board.items;
  return (
    <div>
      <div className="text-xs text-gray-600">
        SKU {board.sku_count}건 · 계산됨 <b>{board.computed_count}</b> · 계산 안 됨{" "}
        <b>{board.uncomputed_count}</b> · 승인 레시피 {board.approved_recipe_count}/
        {board.recipe_count}
      </div>
      {filterSummary ? (
        <div className="mt-1 text-xs text-blue-700" data-testid="board-filter-summary">
          {filterSummary}
        </div>
      ) : null}
      {items.length === 0 ? (
        <div className="mt-2 text-xs text-gray-500 border border-dashed rounded p-4">
          해당 조건에 맞는 SKU가 없다.
        </div>
      ) : (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-gray-500">
              <tr className="text-left border-b">
                <th className="py-1 pr-2">SKU</th>
                <th className="py-1 pr-2">상품</th>
                <th className="py-1 pr-2">폼팩터</th>
                <th className="py-1 pr-2 text-right">표준원가(VAT 포함)</th>
                <th className="py-1 pr-2 text-right">현 cost_price</th>
                <th className="py-1 pr-2 text-right">격차</th>
                <th className="py-1">비고</th>
              </tr>
            </thead>
            <tbody>
              {items.map((row) => (
                <tr key={`${row.recipe_id}-${row.internal_sku}`} className="border-b last:border-0">
                  <td className="py-1 pr-2 font-mono">{row.internal_sku}</td>
                  <td className="py-1 pr-2 truncate max-w-[22rem]">
                    {row.product_name ?? row.recipe_product_name}
                  </td>
                  <td className="py-1 pr-2">{formFactorLabel(row.form_factor)}</td>
                  <td className="py-1 pr-2 text-right font-medium">
                    {formatCostWon(row.std_cost_inc_vat)}
                  </td>
                  {/* ★읽기 전용 대조값이다 — 이 화면은 이 칸에 쓰지 않는다(계약 §3 금지선). */}
                  <td className="py-1 pr-2 text-right text-gray-600">
                    {formatCostWon(row.current_cost_price)}
                  </td>
                  <td className="py-1 pr-2 text-right">{gapText(row.gap_pct)}</td>
                  <td className="py-1 text-amber-700">{uncomputedReason(row) ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
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
  const [recipes, setRecipes] = useState<CostRecipe[]>([]);
  const [selectedRecipeId, setSelectedRecipeId] = useState<number | null>(null);
  const [board, setBoard] = useState<CostBoard | null>(null);
  const [importResult, setImportResult] = useState<CostImportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // ── S3: 보드 탭 제품 → 옵션 필터 ──────────────────────────────────
  const [boardProduct, setBoardProduct] = useState<string | null>(null);
  const [boardOption, setBoardOption] = useState<string | null>(null);
  const handleBoardProductChange = useCallback((v: string | null) => {
    setBoardProduct(v);
    setBoardOption(null); // ★제품이 바뀌면 이전 제품의 옵션값은 더 이상 유효하지 않다.
  }, []);
  const handleBoardReset = useCallback(() => {
    setBoardProduct(null);
    setBoardOption(null);
  }, []);

  const boardProducts = useMemo<PickerItem[]>(() => {
    if (!board) return [];
    const counts = new Map<string, number>();
    for (const row of board.items) {
      counts.set(row.recipe_product_name, (counts.get(row.recipe_product_name) ?? 0) + 1);
    }
    return Array.from(counts, ([value, count]) => ({ value, label: value, count })).sort((a, b) =>
      a.label.localeCompare(b.label, "ko"),
    );
  }, [board]);

  const boardOptionsForProduct = useMemo<PickerItem[]>(() => {
    if (!board || !boardProduct) return [];
    return board.items
      .filter((r) => r.recipe_product_name === boardProduct)
      .map((r) => ({
        value: r.internal_sku,
        label: `${r.internal_sku} · ${r.product_name ?? r.recipe_product_name}`,
      }));
  }, [board, boardProduct]);

  const filteredBoardItems = useMemo(() => {
    if (!board) return [];
    return board.items.filter((r) => {
      if (boardProduct && r.recipe_product_name !== boardProduct) return false;
      if (boardOption && r.internal_sku !== boardOption) return false;
      return true;
    });
  }, [board, boardProduct, boardOption]);

  const boardFilterSummary = useMemo(() => {
    if (!board || (!boardProduct && !boardOption)) return null;
    const parts: string[] = [];
    if (boardProduct) parts.push(`제품=${boardProduct}`);
    if (boardOption) parts.push(`옵션=${boardOption}`);
    return `${board.items.length}건 중 ${filteredBoardItems.length}건 표시 중 — 필터: ${parts.join(", ")}`;
  }, [board, boardProduct, boardOption, filteredBoardItems]);

  // ── S3: 레시피 탭 제품 → 폼팩터 필터 ──────────────────────────────
  const [recipeProduct, setRecipeProduct] = useState<string | null>(null);
  const [recipeFormFactor, setRecipeFormFactor] = useState<string | null>(null);
  const handleRecipeProductChange = useCallback((v: string | null) => {
    setRecipeProduct(v);
    setRecipeFormFactor(null);
  }, []);
  const handleRecipeReset = useCallback(() => {
    setRecipeProduct(null);
    setRecipeFormFactor(null);
  }, []);

  const recipeProducts = useMemo<PickerItem[]>(() => {
    const counts = new Map<string, number>();
    for (const r of recipes) counts.set(r.product_name, (counts.get(r.product_name) ?? 0) + 1);
    return Array.from(counts, ([value, count]) => ({ value, label: value, count })).sort((a, b) =>
      a.label.localeCompare(b.label, "ko"),
    );
  }, [recipes]);

  // ★레시피엔 «옵션» 개념이 없다 — 제품 안에서는 **폼팩터**로 가른다(위임문 §C).
  const recipeFormFactorsForProduct = useMemo<PickerItem[]>(() => {
    if (!recipeProduct) return [];
    const seen = new Set<string>();
    const out: PickerItem[] = [];
    for (const r of recipes) {
      if (r.product_name !== recipeProduct) continue;
      const key = r.form_factor ?? "__none__";
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ value: key, label: formFactorLabel(r.form_factor) });
    }
    return out;
  }, [recipes, recipeProduct]);

  const recipeCountForSelectedProduct = useMemo(() => {
    if (!recipeProduct) return 0;
    return recipeProducts.find((p) => p.value === recipeProduct)?.count ?? 0;
  }, [recipeProducts, recipeProduct]);

  const filteredRecipes = useMemo(() => {
    return recipes.filter((r) => {
      if (recipeProduct && r.product_name !== recipeProduct) return false;
      if (recipeFormFactor && (r.form_factor ?? "__none__") !== recipeFormFactor) return false;
      return true;
    });
  }, [recipes, recipeProduct, recipeFormFactor]);

  const recipeFilterSummary = useMemo(() => {
    if (!recipeProduct && !recipeFormFactor) return null;
    const parts: string[] = [];
    if (recipeProduct) parts.push(`제품=${recipeProduct}`);
    if (recipeFormFactor) parts.push(`폼팩터=${formFactorLabel(recipeFormFactor === "__none__" ? null : recipeFormFactor)}`);
    return `${recipes.length}건 중 ${filteredRecipes.length}건 표시 중 — 필터: ${parts.join(", ")}`;
  }, [recipes, recipeProduct, recipeFormFactor, filteredRecipes]);

  const load = useCallback(async () => {
    try {
      const [m, l, s, r, b] = await Promise.all([
        fetchCostMaterials(),
        fetchCostLedgerMaterialLines(),
        fetchCostSettings(),
        fetchCostRecipes(),
        fetchCostBoard(),
      ]);
      setMaterials(m.items);
      setLedgerLines(l.items);
      setSettings(s.items);
      setRecipes(r.items);
      setBoard(b);
      setSelectedId((prev) => prev ?? (m.items.length ? m.items[0].id : null));
      // ★레시피 선택은 여기서 건드리지 않는다 — 진실의 원천은 아래 단일 effect
      // (filteredRecipes 기준)뿐이다. 여기서도 setSelectedRecipeId를 하면 두 곳이
      // 서로 다른 기준(전체 recipes vs 필터된 목록)으로 같은 값을 다투게 된다.
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

  // ★진실의 원천은 여기 하나뿐이다 — selectedRecipeId가 무엇을 가리키든,
  // 화면에 실제로 뜨는 selectedRecipe는 항상 filteredRecipes(현재 필터가 적용된
  // 목록) 안에서만 찾는다. recipes(전체 100건) 안에서 찾으면 필터 밖 레시피가
  // 그대로 상세 패널에 남는다 — 그게 이 파일이 고치는 결함이다.
  const selectedRecipe = useMemo(
    () => filteredRecipes.find((r) => r.id === selectedRecipeId) ?? null,
    [filteredRecipes, selectedRecipeId],
  );

  // ★선택 ID를 필터에 맞춰 되돌리는 유일한 자리 — load()는 더 이상 selectedRecipeId를
  // 건드리지 않는다. 로직 자체는 reconcileSelectedRecipeId 하나뿐이고, 여기선 그저
  // 「filteredRecipes가 바뀔 때마다 다시 계산해라」만 배선한다.
  useEffect(() => {
    setSelectedRecipeId((prev) => reconcileSelectedRecipeId(filteredRecipes, prev));
  }, [filteredRecipes]);

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
    <div className="p-6 max-w-[96rem]">
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
        <div className="mt-4 space-y-4">
          <RecipeImportPanel
            busy={busy}
            result={importResult}
            onImport={(cost, mapping) =>
              run(async () => {
                setImportResult(await importCostRecipes(cost, mapping));
              }, "엑셀에서 구성 초안을 만들었다 — 아직 아무것도 승인하지 않았다")
            }
          />
          <div className="grid grid-cols-1 md:grid-cols-[320px_1fr] gap-6">
            {/* ★`min-w-0` — 없으면 320px 트랙이 내용 폭에 밀려 오른쪽 패널을 침범한다. */}
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-gray-700 mb-2">
                레시피 (상품명 × 폼팩터)
              </h2>
              <div className="mb-2">
                <ProductOptionPicker
                  idPrefix="recipe"
                  optionLabel="폼팩터"
                  products={recipeProducts}
                  options={recipeFormFactorsForProduct}
                  optionTotalCount={recipeCountForSelectedProduct}
                  productValue={recipeProduct}
                  optionValue={recipeFormFactor}
                  onProductChange={handleRecipeProductChange}
                  onOptionChange={setRecipeFormFactor}
                  onReset={handleRecipeReset}
                />
              </div>
              <RecipeList
                recipes={filteredRecipes}
                selectedId={selectedRecipeId}
                onSelect={setSelectedRecipeId}
                totalCount={recipes.length}
                filterSummary={recipeFilterSummary}
              />
            </div>
            {selectedRecipe ? (
              <RecipeDetail
                recipe={selectedRecipe}
                busy={busy}
                onApprove={() =>
                  run(
                    () => approveCostRecipe(selectedRecipe.id),
                    "구성을 승인했다 — 이제 표준원가가 저장된다",
                  )
                }
                onUnapprove={() =>
                  run(
                    () => unapproveCostRecipe(selectedRecipe.id),
                    "승인을 취소했다 — 저장된 표준원가도 함께 사라진다",
                  )
                }
                onAdopt={() =>
                  run(async () => {
                    const out = await adoptCostExcelPrices(selectedRecipe.id);
                    // ★건너뛴 것을 조용히 넘기지 않는다 — 「채택됨」만 보이면 무엇이 안 바뀌었는지 모른다.
                    if (out.skipped_has_price.length || out.skipped_no_ref.length) {
                      setErr(
                        `건너뜀 — 이미 단가 있음 ${out.skipped_has_price.length}건 · 참고값 없음 ${out.skipped_no_ref.length}건`,
                      );
                    }
                  }, "엑셀 참고값을 수동 단가로 채택했다")
                }
              />
            ) : (
              <div className="text-xs text-gray-500">왼쪽에서 레시피를 고른다.</div>
            )}
          </div>
        </div>
      ) : null}
      {tab === "board" ? (
        <div className="mt-4">
          <div className="mb-3">
            <ProductOptionPicker
              idPrefix="board"
              products={boardProducts}
              options={boardOptionsForProduct}
              optionTotalCount={boardOptionsForProduct.length}
              productValue={boardProduct}
              optionValue={boardOption}
              onProductChange={handleBoardProductChange}
              onOptionChange={setBoardOption}
              onReset={handleBoardReset}
            />
          </div>
          <StandardCostBoard
            board={board}
            displayItems={boardProduct || boardOption ? filteredBoardItems : undefined}
            filterSummary={boardFilterSummary}
          />
        </div>
      ) : null}
    </div>
  );
}
