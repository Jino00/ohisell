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
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

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

/** 단가 출처 라벨 — 「이 값이 어디서 왔나」가 한 칸으로 보여야 추적이 끊기지 않는다.
 *
 * ★D-CPP-56(2026-08-24)로 어휘가 바뀌었다. 초판의 「수동 입력」은 «누가 손으로 넣은 값»으로
 * 읽혀서, 다른 제품 레시피를 열었을 때 그 단가가 **이미 확인·승인된 공용 값**이라는 사실이
 * 전달되지 않았다. Jino 결정: *"지금 등록이 되어 있는 엑셀값을 공식 가격으로 쓰자."*
 * ⇒ 「등록가」. 원장 파생과 **서열이 아니다** — 둘 다 확인된 공식 단가이고 이 문자열은
 * 「어디서 왔나」만 말한다.
 *
 * ★함수를 **하나만** 둔다. 상태 열과 단가 이력 표가 같은 어휘를 쓰게 하려는 것이고,
 * 사본을 두면 다음에 한쪽만 바뀐다(이 저장소가 반복해 밟은 자리). */
export function priceSourceLabel(source: string | null | undefined): string {
  if (!source) return "출처 미상";
  return source === "ledger" ? "원장" : "등록가";
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

// ══════════════════════════════════════════════════════════════════
// S4 ㉯ — 수입 종 / 비수입 종을 가른다 (계약 §6 S4 · 합격 11·12·13)
//
// ★판별은 **새 필드도 마이그레이션도 없이** 한다: 「이 종에 대응하는 원장 라인이 있는가」.
//   prod 실측(2026-08-23): 수입 부자재 **1종**(cleaning kits) vs 비수입 **128종**.
//   그런데 화면은 128종에게 ①엑셀 값을 「단가 아님」이라 불러 **값을 의심하게** 만들고
//   ②「원장 연결」을 첫 번째 길로 안내했다 — 그 종엔 원장 라인이 **0건이라 영영 안 오는
//   길**이다(계약 §0-C, Jino 정정 2026-08-24 00:06).
// ══════════════════════════════════════════════════════════════════

/** 이 원장 라인이 «가리키는» 종 — 연결됐으면 연결된 종, 아니면 제안된 종.
 *
 * 둘 다 없으면 `null`이고 그건 **어느 종도 이 라인을 못 가진다**는 뜻이다(합격 13의 감시 대상).
 * 제안이 모호한(`ambiguous`) 라인도 `material_id`가 null이라 여기로 떨어진다 — 그래야 한다.
 * 「모호해서 어디에도 안 그린다」가 되면 그 라인은 화면에서 **통째로 사라진다.** */
export function ledgerLineMaterialId(r: CostLedgerMaterialLine): number | null {
  return r.linked_material_id ?? r.suggestion.material_id ?? null;
}

/** 한 종에 붙는 원장 라인만. 종을 고르면 그 종 것만 보인다(합격 12). */
export function ledgerLinesForMaterial(
  rows: CostLedgerMaterialLine[],
  materialId: number,
): CostLedgerMaterialLine[] {
  return rows.filter((r) => ledgerLineMaterialId(r) === materialId);
}

/** ★적대 리뷰 1R P1 — **「어느 종에도 안 붙었다」보다 넓은 질문이 진짜 감시 대상이다.**
 *
 * 재현(리뷰어): 종 목록에 폼팩터 필터 `bar`를 걸면 `cleaning kits`(폼팩터 null)가 목록에서
 * 빠져 **고를 수 없게** 되고, 그 종의 원장 라인은 종별 표에도 안 뜬다. 그런데 그 라인은
 * «제안이 있으니» 미귀속도 아니라 별도 섹션에도 안 떴다 — 결과적으로 **화면이 「미매칭 없음」
 * 이라고 말하면서 사람의 확정(「연결」)을 기다리는 라인을 통째로 감췄다.**
 * `origin/main`은 하단 전건 표로 항상 보여줬으므로 **이 슬라이스가 만든 회귀**였다.
 *
 * ⇒ 섹션이 세는 것은 「미매칭」이 아니라 **「지금 이 화면에서 도달할 수 없는 라인」**이다.
 * 그게 계약 §6 S4가 적은 목적(*"안 보이면 단가 이력이 조용히 빈다"*)의 정확한 문언이다.
 *
 * `reachableMaterialIds` = **지금 목록에 떠 있는**(필터 통과) 종의 id. 그 종은 클릭하면
 * 종별 표가 뜨므로 도달 가능하다 — 여기서 뺀다. */
export function unreachableLedgerLines(
  rows: CostLedgerMaterialLine[],
  reachableMaterialIds: Set<number>,
): CostLedgerMaterialLine[] {
  return rows.filter((r) => {
    const id = ledgerLineMaterialId(r);
    return id === null || !reachableMaterialIds.has(id);
  });
}

/** 도달 불가 라인이 «왜» 도달 불가인가. 두 사유는 **처분이 다르다** — 하나는 매칭 규칙을
 * 손봐야 하고, 하나는 필터만 풀면 된다. 한 단어로 접으면 사람이 틀린 일을 한다. */
export function unreachableReason(
  r: CostLedgerMaterialLine,
  materials: CostMaterial[],
): string {
  const id = ledgerLineMaterialId(r);
  if (id === null) {
    return "어느 종도 이 라인을 못 가진다 — 매칭 규칙(match_rule)을 손봐야 붙는다";
  }
  const name = materials.find((m) => m.id === id)?.name;
  return `「${name ?? `종 id=${id}`}」의 라인인데 그 종이 지금 필터 밖이라 고를 수 없다 — 필터를 풀면 종별 표에서 보인다`;
}

/** 원장 라인이 하나라도 가리키는 종의 id 집합 = **수입 종**. */
export function importedMaterialIds(rows: CostLedgerMaterialLine[]): Set<number> {
  const out = new Set<number>();
  for (const r of rows) {
    const id = ledgerLineMaterialId(r);
    if (id !== null) out.add(id);
  }
  return out;
}

/** 종별 표 + 도달 불가 섹션이 **전건을 덮는가**. 한 라인도 화면 밖으로 떨어지면 안 된다.
 *
 * ★이 함수는 화면이 쓰라고 있는 게 아니라 **테스트가 재라고** 있다 — 필터를 도입하면
 * 「어느 표에도 안 들어가는 행」이 소리 없이 생기는 것이 전형적인 실패 모드다.
 * ★그래서 **`reachable`를 인자로 받는다**(적대 리뷰 1R P2-3): 필터를 안 보는 커버리지는
 * 「필터가 만든 구멍」을 원리적으로 못 재고, 실제로 1R P1이 그 틈으로 통과했다. */
export function ledgerLineCoverage(
  rows: CostLedgerMaterialLine[],
  reachableMaterialIds: Set<number>,
): {
  reachable: number;
  unreachable: number;
  total: number;
} {
  const un = unreachableLedgerLines(rows, reachableMaterialIds).length;
  return { reachable: rows.length - un, unreachable: un, total: rows.length };
}

/** 「이 표준의 근거는 로트 N건」 — 표본 부족을 숨기지 않는다(계약 §9-5).
 *
 * ★어긋난 연결(`stale_count`)을 **따로 말한다**(적대 리뷰 1R P1-1). 그 행들은 최신 단가
 * 산정에서 빠지는데, 왜 빠졌는지를 화면이 안 말하면 「단가가 왜 없지?」가 결함 조사로 번진다.
 *
 * ★`imported`는 **필수 인자다**(기본값 없음, 2026-08-24 S4). 기본값을 주면 새 호출부가
 * 조용히 「수입 종」 문구를 쓰게 되고, 그게 이 파일이 아홉 번 밟은 「한쪽만 고친다」의
 * 재발 경로다 — 호출부마다 **판단을 강제**한다. */
export function lotCountText(
  m: Pick<CostMaterial, "lot_count" | "price_count" | "stale_count"> &
    Partial<Pick<CostMaterial, "excel_ref_price">>,
  imported: boolean,
): string {
  const stale = m.stale_count ?? 0;
  if (m.price_count === 0) {
    // ★단가가 없을 때 할 일은 **셋**이지 둘이 아니다(2026-08-23 발견).
    //   prod 실측: 단가 보유 종 1/129 · 엑셀 참고값 보유 종 128/129. 그런데 이 줄이
    //   「원장 연결 또는 수동 입력 필요」라고만 말해 **가장 싼 길(채택)을 감췄다** —
    //   화면이 사람을 더 비싼 일로 보내고 있었다.
    //   ★문구는 «있는 조작»만 가리킨다: 부자재 탭엔 채택 버튼이 없으므로, 여기서
    //   「채택을 누르세요」라고 쓰면 없는 버튼을 가리키는 거짓말이 된다(교훈 #349).
    if (!imported) {
      // ★비수입 종 — **엑셀이 정본이다**(계약 §0-C). 「단가 아님」은 값을 의심하게 만드는
      //   말인데 실제로는 **아직 «넣지» 않았을 뿐**이다. 그리고 원장은 이 종에 영영 안 온다.
      return m.excel_ref_price
        ? `엑셀 단가(미확정) ${formatCostWon(m.excel_ref_price)} — 값은 있고 아직 확정만 안 했다`
        : "단가 없음 — 「+ 단가 입력·수정」으로 넣는다 (수입 종이 아니라 원장에서 올 값이 없다)";
    }
    return m.excel_ref_price
      ? `단가 없음 — 엑셀 참고값 ${formatCostWon(m.excel_ref_price)}은 있다(대조값)`
      : "단가 없음 — 원장 연결 또는 수동 입력 필요";
  }
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

/** ★엑셀 참고값 자백 — 「이 값이 있다 · 이건 단가가 아니다 · 단가가 되는 길은 어디에 있나」.
 *
 * ★**없는 조작을 지시하지 않는다.** 「채택」은 **레시피 단위** 동작이고
 * (`POST /recipes/{id}/adopt-excel-prices` — 레시피 상세의 「엑셀 참고값을 단가로 채택」),
 * 부자재 탭에는 그 버튼이 없다. 그러니 여기서 「채택 버튼을 누르세요」라고 쓰면 **없는
 * 버튼을 가리키는 거짓말**이 된다 — 그 대신 «그 버튼이 어디 있는지»를 말한다.
 * 사유가 틀리면 사람이 틀린 일을 한다(교훈 #349).
 *
 * 반환 `null` = 참고값이 없다(할 말이 없다). 조용한 빈 칸이 아니라 «해당 없음»이다. */
export function excelRefNoteText(
  m: Pick<CostMaterial, "excel_ref_price" | "price_count">,
  imported: boolean,
): string | null {
  if (!m.excel_ref_price) return null;
  const value = formatCostWon(m.excel_ref_price);
  // ★비수입 검사가 **먼저다**(적대 리뷰 1R P2-1). 순서를 뒤집으면 비수입 종이 「채택」으로
  //   단가를 갖는 순간 그 엑셀 값을 다시 「대조값」이라 부른다 — 그 종엔 엑셀이 정본인데도.
  if (!imported && m.price_count > 0) {
    // ★「이 값이 그대로 들어갔다」고 단언하지 않는다(적대 리뷰 2R 기록). §0-C가 명시한
    //   Jino의 수동 정정 경로(*"단가가 조정되면 그때 수정을 별도로"*)로 «다른 값»이
    //   들어가 있을 수 있고, 그러면 그 단언은 거짓이다. 실값은 바로 아래 단가 이력이 말한다.
    return `엑셀 단가(정본) ${value} — 이 종은 수입 종이 아니라 이 값이 정본이다. 단가 이력에 이미 값이 있으니 «지금 쓰이는 값»은 아래 표에서 확인한다(둘이 다르면 수동 정정분이다).`;
  }
  if (m.price_count > 0) {
    // 이미 단가가 있는 수입 종 — 채택은 «단가 없는 종»만 건드리므로 여기선 대조값일 뿐이다.
    return `엑셀 참고값 ${value} — 단가가 아니라 대조값이다. 이 종엔 이미 단가가 있어 「채택」은 건드리지 않는다.`;
  }
  if (!imported) {
    // ★비수입 종(현재 128종) — Jino 원문(2026-08-24 00:06): *"수입하는게 아닌 물건은
    //   엑셀파일에 있던 값이 맞고, 단가가 조정되면 그때 수정을 별도로 하면 되지"*.
    //   ⇒ **엑셀이 정본이다.** 그리고 **원장 연결을 안내하지 않는다** — 이 종엔 원장 라인이
    //   0건이라 그 길은 영영 안 온다. 없는 길을 첫 번째로 가리키면 사람이 거기서 멈춘다.
    return (
      `엑셀 단가(미확정) ${value} — 이 종은 수입 종이 아니라 **엑셀 값이 정본**이다(계약 §0-C). ` +
      `아직 확정만 안 한 상태다. 확정하는 길: 이 종을 쓰는 레시피의 상세 화면에서 ` +
      `「엑셀 참고값을 단가로 채택」, 또는 값이 다르면 「+ 단가 입력·수정」. ` +
      `이 탭에는 채택 버튼이 없다 — 채택은 레시피 단위 동작이기 때문이다.`
    );
  }
  return (
    `엑셀 참고값 ${value} — 이 종은 **수입 종**이라 정본은 원장이고 이 값은 대조값이다(계약 §3). ` +
    `단가로 만드는 길 셋: ①이 종을 쓰는 레시피의 상세 화면에서 「엑셀 참고값을 단가로 채택」 ` +
    `②아래 「원장 부자재 라인」에서 연결 ③「+ 단가 입력·수정」. ` +
    `이 탭에는 채택 버튼이 없다 — 채택은 레시피 단위 동작이기 때문이다.`
  );
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
  imported,
}: {
  material: CostMaterial;
  onDelete?: (priceId: number) => void;
  /** 어긋난 원장 행을 원장 현재값으로 다시 맞춘다(적대 리뷰 1R P1-2). */
  onRefresh?: (priceId: number) => void;
  busy?: boolean;
  /** 수입 종인가(계약 §6 S4 ㉯). **빈 이력이 가리킬 길이 갈린다** — 비수입 종에게
   *  「원장 부자재 라인에서 연결하라」는 0건짜리 길이라 영영 안 온다. */
  imported: boolean;
}) {
  if (material.prices.length === 0) {
    return (
      <div className="text-sm text-gray-500 py-3">
        {imported
          ? "단가 이력이 없다 — 아래 「원장 부자재 라인」에서 연결하거나 수동 단가를 입력한다."
          : "단가 이력이 없다 — 이 종을 쓰는 레시피 상세에서 「엑셀 참고값을 단가로 채택」하거나 「+ 단가 입력·수정」으로 넣는다."}
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
  totalCount,
  filterSummary,
  importedIds,
}: {
  materials: CostMaterial[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  onApprove?: (m: CostMaterial) => void;
  busy?: boolean;
  /** 필터 적용 «전» 전체 종 수 — 필터가 만든 0건과 «원래 없음»을 가른다. */
  totalCount?: number;
  /** 「129건 중 N건 표시 중 — 필터: …」. null/undefined면 필터 없음. */
  filterSummary?: string | null;
  /** 원장 라인이 가리키는 종 = **수입 종**(계약 §6 S4 ㉯). 목록의 요약 줄도 상세 패널과
   *  **같은 말을 해야 한다** — 한쪽만 고치는 것이 이 파일의 상습 실패 모드다. */
  importedIds: Set<number>;
}) {
  // ★조용한 0은 커버리지 착시다 — 0건이면 «사유»를 그린다(RecipeList와 같은 관례).
  if (materials.length === 0) {
    return (
      <div>
        {filterSummary ? (
          <div className="text-xs text-gray-500 mb-2" data-testid="material-filter-summary">
            {filterSummary}
          </div>
        ) : null}
        <div className="text-sm text-gray-500 py-3">
          {totalCount
            ? "해당 조건에 맞는 부자재 종이 없다 — 필터를 풀거나 다른 폼팩터를 고른다."
            : "등록된 부자재 종이 없다."}
        </div>
      </div>
    );
  }
  return (
    <div>
      {filterSummary ? (
        <div className="text-xs text-gray-500 mb-2" data-testid="material-filter-summary">
          {filterSummary}
        </div>
      ) : null}
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
          {/* ★현재 단가를 «주인공»으로 (Jino 2026-08-24: *"현재 단가, 수동입력가능한 버튼이
              좀 더 직관적이었으면 좋겠어"*). 초판은 이 값이 `text-xs text-gray-500`이라
              옆의 긴 로트 설명문과 **같은 크기**였고, 값이 없을 땐 회색 「—」여서 화면에서
              가장 중요한 숫자가 가장 안 보였다.
              ★없을 때 「—」가 아니라 **「단가 없음」이라고 말한다** — 기존 테스트의 의도는
              «0원으로 보이면 안 된다»였고 그 의도는 그대로 지켜진다(오히려 더 명확하다). */}
          <div className="mt-1 flex items-baseline gap-1.5 flex-wrap">
            <span className="text-[11px] text-gray-500">현재 단가</span>
            <span
              className={`text-sm font-semibold ${
                m.latest_price_inc_vat === null ? "text-gray-400" : "text-gray-900"
              }`}
              data-testid={`material-${m.id}-latest`}
            >
              {m.latest_price_inc_vat === null
                ? "단가 없음"
                : formatCostWon(m.latest_price_inc_vat)}
            </span>
            {m.latest_price_source ? (
              <span className="text-[11px] text-gray-500">
                {priceSourceLabel(m.latest_price_source)}
              </span>
            ) : null}
          </div>
          <div
            className={`text-xs mt-0.5 ${
              m.stale_count > 0 ? "text-amber-700" : "text-gray-400"
            }`}
          >
            {lotCountText(m, importedIds.has(m.id))}
          </div>
          {onApprove && m.status !== "approved" ? (
            /* ★조작을 «조작처럼» 보이게 — 초판은 `text-[11px] text-blue-600 hover:underline`이라
               본문 각주와 구별되지 않았고, Jino가 *"confirm 할 수 있는 곳이 전혀 없어"*라고
               읽었다. 있는데 못 찾으면 없는 것이다(목표 카드 「판정 표면」). */
            <button
              className="mt-1.5 text-[11px] px-2 py-1 rounded border border-gray-300 bg-white font-medium text-gray-700 hover:bg-gray-50 hover:border-gray-400 disabled:opacity-40"
              disabled={busy}
              onClick={(e) => {
                e.stopPropagation();
                onApprove(m);
              }}
            >
              이 단가를 승인
            </button>
          ) : null}
        </li>
      ))}
      </ul>
    </div>
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
  emptyText,
}: {
  rows: CostLedgerMaterialLine[];
  materials: CostMaterial[];
  onLink?: (materialId: number, lineId: number) => void;
  busy?: boolean;
  /** 0건일 때 할 말. 호출부마다 «없다»의 뜻이 다르다(종별 표의 0건 ≠ 미귀속의 0건). */
  emptyText?: string;
}) {
  if (rows.length === 0) {
    return (
      <div className="text-sm text-gray-500 py-3">
        {emptyText ??
          "확정된 수입건에 부자재(`material`) 라인이 없다. 원장에서 분류를 먼저 확인한다."}
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

/** ★목록 칼럼의 «독립 스크롤» 규율 — 부자재 탭·레시피 탭이 **같은 상수**를 쓴다.
 *
 * Jino 원문 ①(2026-08-23 22:25, 부자재): *"부자재 종에서 스크롤을 내리면 부자재 종만
 * 내려가고 전체 화면은 고정되게 만들자"*
 * Jino 원문 ②(2026-08-24 00:10, 레시피): *"레시피에도 우리가 부자재에서 했던 똑같은 문제가
 * 있네. … 레시피 밑의 상품명을 밑으로 스크롤 하면 화면 전체가 움직여서 내용을 볼 수가 없어."*
 *
 * ★**문자열을 복사하지 않고 상수로 묶는 이유**: 원문 ①을 부자재 탭에만 적용한 것이 이 파일이
 * 아홉 번 밟은 「같은 병을 고칠 때 한쪽만 고친다」의 다섯 번째였다. 복사본을 두면 다음 수정이
 * 또 한쪽에만 간다 — 상수 하나면 «한쪽만 고치는 것»이 **원리적으로 불가능**해진다.
 *
 * ⚠️jsdom은 레이아웃을 계산하지 않는다 — 테스트는 「이 클래스가 두 칼럼 다에 살아 있나」까지만
 * 재고, **진짜 판정은 배포 후 라이브 눈 확인**이다(계약 §7 합격 10). */
export const LIST_COLUMN_SCROLL_CLASS =
  "md:sticky md:top-4 md:max-h-[calc(100vh-14rem)] md:overflow-y-auto pr-1";

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
 * ★필터가 걸린 화면에서 «오른쪽 패널이 무엇을 가리키는가»의 유일한 진실의 원천.
 *
 * 필터가 바뀌거나 데이터가 재조회돼 `filtered`가 달라질 때마다 이 함수 하나로 다음 선택을
 * 다시 계산한다 — 호출하는 자리가 여럿이어도 로직은 여기 하나뿐이라 서로 다른 기준으로
 * 같은 상태를 다투지 않는다.
 *
 * - 현재 선택이 `filtered` 안에 여전히 있으면 그대로 유지한다(승인 직후 재조회돼도
 *   방금 승인한 항목을 계속 보여주기 위해서다).
 * - 없으면(필터가 바뀌었거나 항목 자체가 사라졌으면) `filtered`의 첫 항목으로 스냅한다.
 * - `filtered`가 0건이면 `null`이다 — 있지도 않은 항목을 붙들고 있지 않는다.
 *
 * ★2026-08-23: 레시피 전용이던 것을 **부자재 탭에도 필터가 생기면서** 제네릭으로 넓혔다.
 *   복사본을 하나 더 만들지 않는다 — 「같은 결함을 두 번 밟으면 값이 아니라 «모양»을
 *   고쳐라」(교훈 #348 계열). 두 벌이 되면 반드시 갈라진다.
 */
export function reconcileSelectedId<T extends { id: number }>(
  filtered: T[],
  currentId: number | null,
): number | null {
  if (currentId !== null && filtered.some((r) => r.id === currentId)) return currentId;
  return filtered.length ? filtered[0].id : null;
}

/** 오른쪽 패널의 «고를 것이 없다» 안내 — 왼쪽이 0건인데 「왼쪽에서 고른다」고 하면
 * **고를 것이 없는데 고르라고 하는 것**이다(적대 리뷰 1R P2-3 채택, 2026-08-23).
 *
 * `totalCount`가 0이면 필터 문제가 아니라 데이터가 아예 없는 것이다 — 처분이 다르므로
 * 문장도 다르다(`lotCountText`·`RecipeList`의 0건 분기와 같은 결). */
export function recipePlaceholderText(filteredCount: number, totalCount: number): string {
  if (filteredCount > 0) return "왼쪽에서 레시피를 고른다.";
  if (totalCount > 0) {
    return "고를 레시피가 없다 — 필터가 전부 걸러냈다. 위에서 필터를 풀거나 다른 제품을 고른다.";
  }
  return "고를 레시피가 없다 — 위에서 엑셀 2종을 올리면 초안이 생긴다.";
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

/* ★단가 출처 라벨(`priceSourceLabel`)은 이 파일 위쪽에 **이미 있다**(D-CPP-56에서 어휘 개정).
   여기에 두 번째 사본을 만들지 않는다 — 그게 이 저장소가 반복해 밟는 자리다. */

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

/** 「초안 만들기」가 왜 비활성인지 — 흐려지기만 하면 사람은 무엇을 해야 할지 모른다.
 *
 * ★**한쪽만으로도 된다** (Jino 2026-08-24: *"여기서 둘중에 하나만도 업데이트가 되게 해줘"*).
 * 그러니 막는 경우는 «아무것도 안 고른» 하나뿐이다. */
export function importDisabledReason(cost: File | null, mapping: File | null): string | null {
  if (!cost && !mapping) return "엑셀을 고르세요 — 원가 정본·매핑 정본 중 하나만 올려도 됩니다";
  return null;
}

/** 한쪽만 고른 상태에서 «무엇이 갱신되고 무엇이 그대로인지» — 누르기 «전»에 말한다.
 *
 * ★백엔드 응답의 `updated_halves`·`untouched`와 **같은 사실**을 눌러 보기 전에 미리 보여
 * 준다. 조용한 반쪽 갱신은 반쪽 갱신보다 나쁘다 — 사람이 「다 됐다」고 믿기 때문이다. */
export function importHalfNotice(cost: File | null, mapping: File | null): string | null {
  if (cost && mapping) return null;
  if (cost)
    // ★「갱신」이라고만 쓰면 «늘어나기만 한다»로 읽힌다(적대 리뷰 P2 채택). 재매칭이
    //   실패하면 그 레시피의 구성은 **비워진다** — 두 파일 경로와 같은 규칙이지만,
    //   말하지 않으면 사람은 그 가능성을 모른 채 누른다.
    return "원가 정본만 올립니다 — 부자재 종과 구성이 다시 맞춰집니다(원가표에서 못 찾은 레시피는 구성이 비워집니다). SKU 링크·옵션 수는 그대로 둡니다.";
  if (mapping)
    return "매핑 정본만 올립니다 — SKU 링크가 갱신되고, 구성·부자재 종은 그대로 둡니다.";
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
  onImport: (cost: File | null, mapping: File | null) => void;
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
  const halfNotice = importHalfNotice(cost, mapping);

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
      {/* ★한쪽만 고른 상태에서 «무엇이 그대로인지»를 누르기 «전»에 말한다.
          조용한 반쪽 갱신은 반쪽 갱신보다 나쁘다 — 사람이 「다 됐다」고 믿기 때문이다. */}
      {halfNotice ? (
        <div className="mt-2 text-xs text-blue-700" data-testid="import-half-notice">
          {halfNotice}
        </div>
      ) : null}
      <button
        className="mt-2 text-xs px-3 py-1.5 rounded bg-blue-600 text-white disabled:opacity-40"
        disabled={busy || (!cost && !mapping)}
        onClick={() => (cost || mapping) && onImport(cost, mapping)}
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
          {/* ★어느 절반이 «그대로인지»를 결과에도 남긴다 — 누르기 전 안내와 같은 사실이지만,
              누른 «뒤»에 확인할 곳이 없으면 나중에 「다 갱신된 줄 알았다」가 된다. */}
          {result.untouched?.length ? (
            <div className="text-blue-700" data-testid="import-untouched">
              그대로 둔 것: {result.untouched.join(" · ")}
            </div>
          ) : null}
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
            {/* ★열 이름이 스스로 「단가가 아니다」를 말한다 — 색·기울임만으로는 안 된다.
                합계 행에서도 이 열은 비어 있고, 그 이유를 각주가 한 줄로 밝힌다.
                ★VAT 기준을 이름에 박는다 — `adopt_excel_prices`가 이 값을
                `unit_price_ex_vat`로 쓰므로 «VAT 제외»가 사실이고, 이 화면의 기본
                표기는 VAT «포함»이라(D-CPP-51) 기준을 안 적으면 반대로 읽힌다. */}
            <th className="py-1 pr-2 text-right text-gray-400 font-normal">
              엑셀 참고값(채택 전 · VAT 제외)
            </th>
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
              {/* ★참고값 칸 — 흐린 이탤릭으로 「값이지만 단가는 아니다」를 시각으로도 말한다.
                  `formatCostWon`을 지나므로 없으면 「—」다(0원으로 안 그린다, §2-7). */}
              <td
                className="py-1 pr-2 text-right text-gray-400 italic"
                data-testid={`breakdown-excel-ref-${i}`}
              >
                {formatCostWon(ln.excel_ref_price)}
              </td>
              <td className="py-1 pr-2 text-right">{formatCostWon(ln.amount_ex_vat)}</td>
              <td className="py-1 pr-2 text-right">
                {formatCostWon(ln.amount_inc_vat)}
                {/* ★유도했다는 사실이 화면까지 온다 — ×1.1은 «실제로 낸 부가세»가 아니다. */}
                {ln.inc_derived ? <span className="text-gray-400"> (유도)</span> : null}
              </td>
              {/* ★확인된 단가임을 «한눈에» — Jino 원문(2026-08-24): *"이미 확인되어서 승인된
                  부자재 단가는 표시를 해주자. 그러면 다른 제품을 레시피에서 볼때 확인된
                  단가라는걸 쉽게 알아볼 수 있잖아?"*. 다른 제품 레시피를 열면 이 종들이 이미
                  단가를 갖고 있는데, 초판은 그것을 영어 `manual`로만 말해 「내가 손으로 넣은
                  값인가」와 「이미 확인된 공용 단가인가」가 구별되지 않았다.
                  ★`usable === true`는 곧 «종이 승인됐고 쓸 수 있는 단가가 있다»이다 —
                  미승인 종은 단가가 있어도 `material_unapproved`로 떨어져 이 가지에 못 온다
                  (`recipes._recipe_lines`). 그래서 이 배지는 승인 사실을 다시 조회하지 않는다. */}
              <td className="py-1">
                {ln.usable ? (
                  <span className="inline-flex items-center gap-1">
                    <span
                      className="text-[10px] px-1.5 py-0.5 rounded border bg-green-50 text-green-700 border-green-200"
                      data-testid={`breakdown-confirmed-${i}`}
                    >
                      확인됨
                    </span>
                    <span className="text-gray-500">{priceSourceLabel(ln.price_source)}</span>
                  </span>
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
            {/* ★★참고값 열엔 **합계가 없다.** 여기에 Σ를 그리는 순간 화면이 계약 §3
                («저장되는 단가는 원장 파생이거나 Jino가 입력·승인한 값뿐»)을 어긴다 —
                채택하지 않은 값으로 만든 총액은 표준원가가 아니다. 「합계 없음」이라고
                **말한다** — 빈 칸으로 두면 「깜빡 잊었나」와 구별이 안 된다. */}
            <td
              className="pt-2 text-right text-[10px] text-gray-400 font-normal"
              data-testid="breakdown-excel-ref-total"
            >
              합계 없음
            </td>
            <td className="pt-2 text-right" data-testid="breakdown-total-ex">
              {formatCostWon(standard.std_cost_ex_vat)}
            </td>
            <td className="pt-2 text-right" data-testid="breakdown-total-inc">
              {formatCostWon(standard.std_cost_inc_vat)}
            </td>
            <td />
          </tr>
        </tfoot>
      </table>
      {/* ★각주 — 열 이름만으로 부족한 「왜 합계에 안 들어가나」를 한 줄로 말한다. */}
      <div className="mt-1 text-[11px] text-gray-500" data-testid="breakdown-excel-ref-note">
        「엑셀 참고값(채택 전 · VAT 제외)」은 <b>단가가 아니다</b> — 합계에 들어가지 않는다. 채택은 이 패널
        위쪽의 「엑셀 참고값을 단가로 채택」이 하고, 그때 <code>source=manual</code> 단가 행이
        생긴다(계약 §3).
      </div>
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

  // ── N5: 부자재 탭 폼팩터 → 부품 필터 (Jino: "부자재 종도 드랍다운 버튼을 만들자") ──
  //
  // prod 실측(2026-08-23, 129종): 폼팩터 fold 30 · flip 30 · tablet 27 · bar 14 ·
  // trifold 10 · doorlock 10 · buddy 7 · **null 1**. `part`는 **83/129가 비어 있어**
  // 주축이 못 된다 — 그래서 폼팩터가 1단, `part`가 2단이고, 빈 `part`는 감추지 않고
  // 「(부품 미지정)」이라는 **자기 이름을 가진 선택지**로 세운다(조용한 0 금지).
  const [materialForm, setMaterialForm] = useState<string | null>(null);
  const [materialPart, setMaterialPart] = useState<string | null>(null);
  const handleMaterialFormChange = useCallback((v: string | null) => {
    setMaterialForm(v);
    setMaterialPart(null); // 폼팩터가 바뀌면 이전 폼팩터의 부품값은 더 이상 유효하지 않다.
  }, []);
  const handleMaterialReset = useCallback(() => {
    setMaterialForm(null);
    setMaterialPart(null);
  }, []);

  const materialForms = useMemo<PickerItem[]>(() => {
    const counts = new Map<string, number>();
    for (const m of materials) {
      const key = m.form_factor ?? "__none__";
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return Array.from(counts, ([value, count]) => ({
      value,
      label: value === "__none__" ? "— (폼팩터 없음)" : value,
      count,
    })).sort((a, b) => a.label.localeCompare(b.label, "ko"));
  }, [materials]);

  const materialPartsForForm = useMemo<PickerItem[]>(() => {
    if (!materialForm) return [];
    const counts = new Map<string, number>();
    for (const m of materials) {
      if ((m.form_factor ?? "__none__") !== materialForm) continue;
      const key = m.part && m.part.trim() ? m.part : "__none__";
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    // ★라벨에 건수를 박는다 — 이 셀렉트는 `count`를 따로 안 그리므로, 안 박으면
    //   「부품 미지정이 절대다수」라는 사실이 화면에서 사라진다(prod 83/129).
    return Array.from(counts, ([value, count]) => ({
      value,
      label: value === "__none__" ? `(부품 미지정) (${count})` : `${value} (${count})`,
      count,
    })).sort((a, b) => a.label.localeCompare(b.label, "ko"));
  }, [materials, materialForm]);

  const materialCountForSelectedForm = useMemo(() => {
    if (!materialForm) return 0;
    return materialForms.find((f) => f.value === materialForm)?.count ?? 0;
  }, [materialForms, materialForm]);

  const filteredMaterials = useMemo(() => {
    return materials.filter((m) => {
      if (materialForm && (m.form_factor ?? "__none__") !== materialForm) return false;
      if (materialPart) {
        const key = m.part && m.part.trim() ? m.part : "__none__";
        if (key !== materialPart) return false;
      }
      return true;
    });
  }, [materials, materialForm, materialPart]);

  const materialFilterSummary = useMemo(() => {
    if (!materialForm && !materialPart) return null;
    const parts: string[] = [];
    if (materialForm) {
      parts.push(`폼팩터=${materialForm === "__none__" ? "—(없음)" : materialForm}`);
    }
    if (materialPart) {
      parts.push(`부품=${materialPart === "__none__" ? "(미지정)" : materialPart}`);
    }
    return `${materials.length}건 중 ${filteredMaterials.length}건 표시 중 — 필터: ${parts.join(", ")}`;
  }, [materials, materialForm, materialPart, filteredMaterials]);

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
      // ★부자재 선택도 여기서 건드리지 않는다(2026-08-23 N5) — 부자재 탭에도 필터가
      //   생겼으므로, 여기서 `setSelectedId`를 하면 「전체 목록 기준」과 「필터된 목록
      //   기준」 두 곳이 같은 상태를 다투게 된다. 레시피에서 이미 밟은 결함이다.
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

  // ★레시피와 같은 규율: 오른쪽 단가 이력 패널은 **필터된 목록 안에서만** 종을 찾는다.
  //   `materials`(전체 129종)에서 찾으면 필터 밖 종이 그대로 패널에 남아, 사람은 목록에
  //   없는 종의 단가를 보며 「승인」을 누르게 된다 — 그게 레시피에서 이미 밟은 결함이다.
  const selected = useMemo(
    () => filteredMaterials.find((m) => m.id === selectedId) ?? null,
    [filteredMaterials, selectedId],
  );

  // ★S4 ㉯ — 수입/비수입 판별은 **원장 라인에서 파생**한다(새 필드·마이그 없음, 계약 §6 S4).
  //   진실의 원천은 `ledgerLines` 하나이고, 목록·상세·원장 표가 전부 이 파생값을 본다.
  //   따로 계산하는 자리를 만들면 두 벌이 되고 두 벌은 갈라진다.
  const importedIds = useMemo(() => importedMaterialIds(ledgerLines), [ledgerLines]);
  const selectedImported = selected ? importedIds.has(selected.id) : false;
  const selectedLedgerLines = useMemo(
    () => (selected ? ledgerLinesForMaterial(ledgerLines, selected.id) : []),
    [ledgerLines, selected],
  );
  // ★적대 리뷰 1R P1 — 「도달 가능」의 기준은 **지금 목록에 떠 있는 종**이다. `materials`
  //   (전체 129종)를 쓰면 필터가 만든 구멍을 원리적으로 못 본다 — 그게 1R P1의 뿌리였다.
  const unreachableLines = useMemo(
    () =>
      unreachableLedgerLines(
        ledgerLines,
        new Set(filteredMaterials.map((m) => m.id)),
      ),
    [ledgerLines, filteredMaterials],
  );

  // ★선택 ID를 필터에 맞춰 되돌리는 유일한 자리(부자재). `useLayoutEffect`인 이유는
  //   아래 레시피 쪽과 같다 — 페인트 전에 맞춰야 첫 프레임이 깜빡이지 않는다.
  useLayoutEffect(() => {
    setSelectedId((prev) => reconcileSelectedId(filteredMaterials, prev));
  }, [filteredMaterials]);

  // ★진실의 원천은 여기 하나뿐이다 — selectedRecipeId가 무엇을 가리키든,
  // 화면에 실제로 뜨는 selectedRecipe는 항상 filteredRecipes(현재 필터가 적용된
  // 목록) 안에서만 찾는다. recipes(전체 100건) 안에서 찾으면 필터 밖 레시피가
  // 그대로 상세 패널에 남는다 — 그게 이 파일이 고치는 결함이다.
  const selectedRecipe = useMemo(
    () => filteredRecipes.find((r) => r.id === selectedRecipeId) ?? null,
    [filteredRecipes, selectedRecipeId],
  );

  // ★선택 ID를 필터에 맞춰 되돌리는 유일한 자리 — load()는 더 이상 selectedRecipeId를
  // 건드리지 않는다. 로직 자체는 reconcileSelectedId 하나뿐이고, 여기선 그저
  // 「filteredRecipes가 바뀔 때마다 다시 계산해라」만 배선한다.
  //
  // ★`useEffect`가 아니라 `useLayoutEffect`다 (적대 리뷰 1R P2-2 채택, 2026-08-23).
  //   load()에서 선택 설정을 빼고 나서 최초 진입이 **두 페인트**로 갈렸다:
  //   ①데이터 도착(선택 null → 「왼쪽에서 레시피를 고른다」) ②effect(스냅 → 상세 패널).
  //   `useLayoutEffect`는 브라우저가 그리기 «전»에 동기로 돌아 그 1프레임을 없앤다.
  //   ★진실의 원천은 여전히 하나다 — 로직을 두 곳으로 되돌리지 않고 «시점»만 당겼다.
  useLayoutEffect(() => {
    setSelectedRecipeId((prev) => reconcileSelectedId(filteredRecipes, prev));
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

  // ★P1-1 수정(적대 리뷰 2R) — 필터가 걸린 채 종을 추가하면 새 종은 늘 필터 밖에
  //   떨어진다. `create_material`(백엔드)이 `name`만 세팅해 `form_factor`·`part`가
  //   항상 `null`이기 때문이다 — 이건 이번 범위 밖이라 고치지 않는다(위임문 경계).
  //   대신 **성공했을 때만** ①필터를 해제하고 ②새로 만든 종을 선택하고 ③왜
  //   해제했는지 화면이 말한다. 실패·취소 시엔 아무것도 건드리지 않는다.
  //   ★필터가 애초에 안 걸려 있었으면 「필터를 해제했다」는 거짓말을 붙이지 않는다 —
  //   `hadFilter`로 문구 자체를 가른다.
  //   ★P2-3도 같이 닫는다 — 필터가 없어도 새로 만든 종을 항상 선택한다(사람이 방금
  //   만든 종을 다시 찾아 누르지 않게).
  async function handleAddMaterial(name: string) {
    const hadFilter = materialForm !== null || materialPart !== null;
    setBusy(true);
    setMsg(null);
    try {
      const created = await createCostMaterial({ name });
      await load();
      if (hadFilter) {
        setMaterialForm(null);
        setMaterialPart(null);
      }
      setSelectedId(created.id);
      setMsg(
        hadFilter
          ? `「${name}」 추가됨 — 새 종은 폼팩터·부품이 비어 있어 지금 건 필터 밖이다. 필터를 해제하고 방금 만든 종을 선택했다.`
          : `「${name}」 추가됨`,
      );
      setErr(null);
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
        // ★B (Jino 2026-08-23: *"오른쪽 빈 곳이 넓어 … 부자재 종 부분의 공간을 좀 더 옆으로"*)
        //   260px에선 종 이름이 2~3줄로 접히고 「미승인」 배지가 «미/확/인» 세로로 깨졌다.
        //   ★고정폭을 **박지 않는다** — `minmax()`라 좁은 화면에선 22rem까지 줄고 넓으면
        //   28rem까지만 자란다. 고정폭을 박았다가 옆 패널을 덮은 것이 이 파일의 여덟 번째
        //   결함이었고, 그 가드 테스트(「필터 바는 좁은 칸에서 접힌다」)가 아직 살아 있다.
        <div className="mt-4 grid grid-cols-1 md:grid-cols-[minmax(22rem,28rem)_1fr] gap-6 items-start">
          <div className="min-w-0">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-gray-700">부자재 종</h2>
              <button
                className="text-xs text-blue-600 hover:underline disabled:opacity-40"
                disabled={busy}
                onClick={() => {
                  const name = window.prompt("새 부자재 종 이름");
                  if (!name) return;
                  void handleAddMaterial(name);
                }}
              >
                + 종 추가
              </button>
            </div>
            {/* ★C — 129종을 눈으로 훑을 수 없다. 보드·레시피 탭이 쓰는 «같은» 컴포넌트를
                재사용한다(새 피커를 만들면 세 벌이 되고 세 벌은 갈라진다). */}
            <div className="mt-2 mb-2">
              <ProductOptionPicker
                idPrefix="material"
                productLabel="폼팩터"
                optionLabel="부품"
                products={materialForms}
                options={materialPartsForForm}
                optionTotalCount={materialCountForSelectedForm}
                productValue={materialForm}
                optionValue={materialPart}
                onProductChange={handleMaterialFormChange}
                onOptionChange={setMaterialPart}
                onReset={handleMaterialReset}
              />
            </div>
            {/* ★A (Jino 2026-08-23: *"부자재 종에서 스크롤을 내리면 부자재 종만 내려가고
                전체 화면은 고정되게"*). 목록 «자신»이 스크롤 컨테이너가 되고, 칼럼 전체는
                `sticky`로 뷰포트에 붙어 오른쪽 단가 이력이 화면 밖으로 밀려나지 않는다.
                ⚠️jsdom은 레이아웃을 계산하지 않는다 — 테스트는 「이 클래스가 살아 있나」까지만
                재고, 진짜 판정은 배포 후 라이브 화면이 한다. */}
            <div className={LIST_COLUMN_SCROLL_CLASS} data-testid="material-list-scroll">
              <MaterialList
                materials={filteredMaterials}
                selectedId={selectedId}
                onSelect={setSelectedId}
                busy={busy}
                totalCount={materials.length}
                filterSummary={materialFilterSummary}
                importedIds={importedIds}
                onApprove={(m) =>
                  run(
                    () => patchCostMaterial(m.id, { status: "approved" }),
                    `「${m.name}」 승인됨`,
                  )
                }
              />
            </div>
          </div>

          <div className="space-y-6 min-w-0">
            {selected ? (
              <section>
                <h2 className="text-sm font-semibold text-gray-700">
                  「{selected.name}」 단가 이력
                </h2>
                <div className="text-xs text-gray-500 mt-0.5">
                  엑셀 대응: {excelLabelText(selected.excel_label)} ·{" "}
                  {lotCountText(selected, selectedImported)}
                </div>
                {/* ★S4 ㉯ — 「이 종이 수입 종인가」를 화면이 **먼저** 말한다. 이 한 줄이 없으면
                    아래에서 원장 표가 왜 있는지/없는지를 아무도 설명 못 한다. */}
                <div
                  className={`text-xs mt-1 ${
                    selectedImported ? "text-gray-600" : "text-gray-500"
                  }`}
                  data-testid="material-origin-note"
                >
                  {selectedImported
                    ? `수입 종 — 원장 부자재 라인 ${selectedLedgerLines.length}건. 정본은 원장이고 엑셀 값은 대조값이다.`
                    : "비수입 종 — 원장 부자재 라인 0건. 정본은 엑셀이다(계약 §0-C)."}
                </div>
                {/* ★D — 참고값이 «있다는 사실»과 «그 값»과 «단가가 되는 길»을 말한다.
                    참고값이 없으면 이 줄 자체가 없다(해당 없음 — 빈 칸이 아니다). */}
                {excelRefNoteText(selected, selectedImported) ? (
                  <div
                    className="text-xs text-blue-800 bg-blue-50 border border-blue-200 rounded p-2 mt-1"
                    data-testid="material-excel-ref-note"
                  >
                    {excelRefNoteText(selected, selectedImported)}
                  </div>
                ) : null}
                {latestPriceNote(selected) ? (
                  <div className="text-xs text-amber-800 mt-1" data-testid="latest-price-note">
                    {latestPriceNote(selected)}
                  </div>
                ) : null}
                <div className="mt-2">
                  <MaterialPriceHistory
                    material={selected}
                    busy={busy}
                    imported={selectedImported}
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
                  className="mt-2 text-xs px-3 py-1.5 rounded bg-blue-600 text-white font-medium hover:bg-blue-700 disabled:opacity-40"
                  disabled={busy}
                  onClick={() => {
                    const v = window.prompt("수동 단가 (VAT 제외, 원). 모르면 취소한다.");
                    if (!v) return;
                    const supplier = window.prompt("공급처 (예: 조아테크). 없으면 비워 둔다.");
                    // ★**발효일을 반드시 함께 보낸다** (D-CPP-55 · 합격 14).
                    //   초판은 단가와 공급처만 보냈고 발효일이 `null`로 저장됐다. 그런데
                    //   「최신 단가」는 `(effective_date, id)` 내림차순으로 고르고 `null`은
                    //   맨 뒤로 간다 — 즉 **채택분(발효일 있음)이 있으면 새로 입력한 단가가
                    //   영영 최신이 못 되어, 사람이 값을 바꿔도 표준원가가 안 움직인다.**
                    //   서버가 오늘로 채우게 하지 않는 이유는 §2-7(«모름»을 지어내지 않는다)이라,
                    //   **사람에게 보여 주고 고칠 수 있게** 오늘을 미리 채워 묻는다.
                    const today = new Date().toLocaleDateString("sv-SE"); // KST 기준 YYYY-MM-DD
                    const eff = window.prompt(
                      "이 단가는 언제부터인가? (YYYY-MM-DD) — 이 날짜가 「최신 단가」를 정한다.",
                      today,
                    );
                    if (!eff) return;
                    void run(
                      () =>
                        addCostManualPrice(selected.id, {
                          unit_price_ex_vat: v,
                          supplier: supplier || null,
                          effective_date: eff,
                        }),
                      "수동 단가를 입력했다 — 이 종을 쓰는 표준원가가 함께 갱신된다",
                    );
                  }}
                >
                  + 단가 입력·수정
                </button>
                {/* ★★화면이 **append-only라는 사실을 말한다** (Jino 2026-08-24:
                    *"부자재 단가를 수정 … 할 수 있는 곳이 전혀 없어. 수정했을때는 이력이
                    있어야 할거고"*). 「수정」 버튼이 없어 보였던 진짜 이유는 이 화면이
                    **한 번도 「덮어쓰지 않고 쌓인다」고 말하지 않았기 때문**이다 —
                    설계는 맞는데 말을 안 했다. 값을 고치는 방법이 「새로 넣는 것」임을
                    여기서 밝히면 「수정하는 곳이 없다」는 오해가 사라진다. */}
                <p
                  className="mt-1.5 text-[11px] text-gray-500"
                  data-testid="manual-price-append-note"
                >
                  입력하면 <b>덮어쓰지 않고 새 발효일로 쌓인다</b> — 최신값이 계산에 쓰이고
                  이전 값은 위 이력에 그대로 남는다.
                </p>

                {/* ★S4 ㉯ 합격 12 — 원장 표는 **이 종의 라인만** 그린다. 초판은 확정 라인
                    전건을 무필터로 그려, 필름 종을 골라도 `cleaning kits`가 떴다
                    (Jino 2026-08-24 00:01). 합격 11은 그 뒷면이다 — 비수입 종엔 **안 그린다.** */}
                {selectedImported ? (
                  <section className="mt-6" data-testid="material-ledger-lines">
                    <h2 className="text-sm font-semibold text-gray-700">
                      「{selected.name}」 원장 부자재 라인
                    </h2>
                    <div className="text-xs text-gray-500 mt-0.5">
                      제안은 제안이다 — 「연결」을 눌러야 단가 이력이 생긴다(확정은 사람). 이미
                      연결한 라인은 수입건의 확정이 풀려도 목록에 남는다 — 사라지면 어긋남이 안
                      보인다.
                    </div>
                    <div className="mt-2">
                      <LedgerMaterialLines
                        rows={selectedLedgerLines}
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
                ) : null}
              </section>
            ) : null}

            {/* ★S4 ㉯ 합격 13 — **도달 불가 라인은 자리를 옮기는 것이지 없애는 게 아니다.**
                종별 표를 도입하면서 이 섹션을 안 두면 「원장에 부자재 라인이 있는데 화면 어디에도
                안 보인다」가 되고 단가 이력이 조용히 빈다(계약 §6 S4 · 합격 13).
                ★건수는 `<summary>`에 있어 **접혀 있어도 보인다** — 접힌 것과 없는 것은 다르다.
                ★적대 리뷰 1R P1으로 **질문이 넓어졌다**: 「어느 종에도 안 붙었다」만 세면,
                붙을 종이 «필터 밖»이라 못 고르는 라인이 어느 표에도 안 남는데 화면은 「미매칭
                없음」이라고 말한다. 세는 것은 **지금 도달할 수 없는 라인**이다. */}
            <details className="border rounded" data-testid="unattributed-ledger-lines">
              <summary className="text-sm font-semibold text-gray-700 cursor-pointer px-3 py-2">
                지금 화면에서 도달할 수 없는 원장 부자재 라인{" "}
                <span data-testid="unattributed-count">
                  {unreachableLines.length === 0
                    ? "— 미매칭 없음"
                    : `${unreachableLines.length}건`}
                </span>
              </summary>
              <div className="px-3 pb-3">
                <div className="text-xs text-gray-500 mb-2">
                  원장엔 있는데 지금 이 화면의 어느 표에도 안 뜨는 라인이다 — ①어느 종도 이 라인을
                  못 가지거나(제안 없음·모호) ②붙을 종이 **필터 밖**이라 고를 수 없거나. 사유는
                  행마다 아래에 적는다. 여기서 사라지면 그 종의 단가 이력이 조용히 빈다.
                </div>
                {/* ★행마다 «왜» 도달 불가인지 — 두 사유는 처분이 다르다(규칙 수정 vs 필터 해제). */}
                <ul className="text-xs text-gray-600 mb-2 space-y-0.5">
                  {unreachableLines.map((r) => (
                    <li key={r.line_id} data-testid={`unreachable-reason-${r.line_id}`}>
                      · {r.item_name} — {unreachableReason(r, materials)}
                    </li>
                  ))}
                </ul>
                {/* ★`onLink`를 넘긴다(적대 리뷰 1R P2-2) — 이 섹션엔 «제안이 있는데 종이 필터
                    밖인» 라인이 오므로, 버튼을 빼면 연결이 원리적으로 불가능해진다. */}
                <LedgerMaterialLines
                  rows={unreachableLines}
                  materials={materials}
                  busy={busy}
                  onLink={(materialId, lineId) =>
                    run(
                      () => linkCostLedgerPrice(materialId, lineId),
                      "원장 로트를 부자재에 연결했다",
                    )
                  }
                  emptyText="미매칭 없음 — 원장의 부자재 라인이 전부 지금 화면에서 도달 가능하다."
                />
              </div>
            </details>
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
          {/* ★`items-start`가 없으면 `md:sticky`가 **작동하지 않는다** — 그리드 아이템이
              기본값(`stretch`)으로 칼럼 전체 높이를 차지해 붙을 여백이 안 생긴다.
              부자재 탭 그리드엔 이미 있었고 여기만 없었다(같은 규율의 나머지 반쪽). */}
          <div className="grid grid-cols-1 md:grid-cols-[320px_1fr] gap-6 items-start">
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
              {/* ★S4 ㉮ 합격 10 — 부자재 탭과 **같은 상수**를 쓴다(위 `LIST_COLUMN_SCROLL_CLASS`).
                  원문 ②(2026-08-24 00:10)가 가리킨 자리이고, 원문 ①을 부자재 탭에만 적용한 것이
                  이 트랙의 「한쪽만 고친다」 다섯 번째였다. */}
              <div className={LIST_COLUMN_SCROLL_CLASS} data-testid="recipe-list-scroll">
                <RecipeList
                  recipes={filteredRecipes}
                  selectedId={selectedRecipeId}
                  onSelect={setSelectedRecipeId}
                  totalCount={recipes.length}
                  filterSummary={recipeFilterSummary}
                />
              </div>
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
              // ★G (적대 리뷰 1R P2-3 채택) — 왼쪽이 0건인데 「왼쪽에서 고른다」고 하면
              //   **고를 것이 없는데 고르라고 하는 것**이다. 왼쪽은 이미 정직하게
              //   「해당 조건에 맞는 레시피가 없다」라고 말하는데 오른쪽만 어긋나 있었다.
              <div className="text-xs text-gray-500" data-testid="recipe-detail-placeholder">
                {recipePlaceholderText(filteredRecipes.length, recipes.length)}
              </div>
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
