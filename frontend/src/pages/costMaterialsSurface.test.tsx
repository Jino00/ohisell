// @vitest-environment jsdom
//
// costMaterialsSurface.test.tsx — 원가 부자재 탭이 «값이 화면 픽셀이 되는 마지막 한 칸»을
// 지킨다 (D-CPP-53 / 계약 A′ S1 합격 1).
//
// ## 존재 이유
//
// 이 저장소의 반복 실패는 «백엔드 변이는 다 죽는데 프론트 변이가 살아남는» 것이다(2회 실측 —
// 교훈 #321 계열, `rgNetAxisSurface.test.tsx` 머리말 참조). 단위 테스트는 「함수가 값을
// 만드나」를 묻지 「사람이 그걸 보나」를 못 묻는다. 합격 1은 **Jino가 화면에서 로트별 단가
// 2건을 본다**이므로, 그 마지막 한 칸에 테스트가 있어야 한다.
//
// 이 파일이 죽이는 변이:
//   FE-1 `MaterialPriceHistory`의 단가 `<td>` 삭제 → 값이 화면에 안 나온다
//   FE-2 `formatCostWon`의 `null` 처리를 `0원`으로 되돌리기 → 미입력이 확정값으로 둔갑
//   FE-3 `LedgerMaterialLines`의 「연결」 버튼 삭제 → 원장에서 단가가 영영 안 온다
//   FE-4 미매칭 사유(`suggestion.reason`) 렌더 삭제 → 화면이 모르는 것을 조용히 넘긴다
//   FE-5 `valuationBadgeText`를 산문 하드코딩으로 되돌리기 → `confirmed`를 안 읽는다
//   FE-6 `excelLabelText`의 자백을 빈 문자열로 되돌리기 → 「미확정」이 사라진다
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, within } from "@testing-library/react";

//   FE-7 재검사 자백(`ledger_check`) 렌더 삭제 → 낡은 값이 「최신」인 척 앉아 있는다
//        (적대 리뷰 1R P1: reopen·값변경·삭제·rowid 재사용 넷의 공통 표면)
import {
  LedgerMaterialLines,
  MaterialList,
  MaterialPriceHistory,
  RecipeImportPanel,
  StandardBreakdown,
  ValuationBadge,
  excelLabelText,
  formatCostWon,
  latestPriceNote,
  ledgerCheckText,
  lotCountText,
  materialStatusLabel,
  priceSourceLabel,
  valuationBadgeText,
} from "./CostPage";
import type {
  CostLedgerCheck,
  CostImportResult,
  CostLedgerMaterialLine,
  CostMaterial,
  CostSetting,
  CostStandard,
} from "../lib/api";

afterEach(cleanup);

const OK_CHECK: CostLedgerCheck = {
  status: "ok",
  ok: true,
  label: "원장과 일치",
  detail: "원장 라인이 지금도 확정 상태이고 값·품목이 저장값과 같다.",
  counts_as_evidence: true,
  refreshable: true,
  ledger_unit_price_ex_vat: "190.82",
  ledger_unit_price_inc_vat: "209.90",
  ledger_item_name: "cleaning kits",
};

// ── prod 실측값 (2026-08-22, `GET /api/import-cost/shipments/{1,2}`) ──
//    id=1 SETR2608170216 통관 2026-08-18 → 190.82 / 209.90
//    id=2 SETR2607220324 통관 2026-07-23 → 178.78 / 196.66
const KIT: CostMaterial = {
  id: 1,
  name: "cleaning kit",
  unit: "ea",
  category: "부자재",
  status: "unconfirmed",
  excel_label: null,
  // ★cleaning kit은 **엑셀 대응 항목이 없는 유일한 종**이다(`excel_label: null`과 같은
  //   사실의 다른 면) — 그래서 참고값도 없다. prod 실측 2026-08-23: 참고값 보유 128/129.
  excel_ref_price: null,
  match_rule: "cleaning kit",
  form_factor: null,
  part: null,
  note: null,
  lot_count: 2,
  price_count: 2,
  stale_count: 0,
  latest_price_ex_vat: "190.82",
  latest_price_inc_vat: "209.90",
  latest_price_inc_derived: false,
  latest_price_source: "ledger",
  price_rule: "latest",
  lot_price_min: "178.78",
  lot_price_max: "190.82",
  lot_price_has_span: true,
  price_conflict: false,
  price_conflict_price_id: null,
  prices: [
    {
      id: 11,
      material_id: 1,
      source: "ledger",
      import_invoice_line_id: 15,
      linked_item_name: "cleaning kits",
      linked_shipment_id: 1,
      supplier: "SHENZHEN OTAO TECHNOLOGY LIMITED",
      unit_price_ex_vat: "190.82",
      unit_price_inc_vat: "209.90",
      effective_date: "2026-08-18",
      note: null,
      shipment: {
        id: 1,
        hbl_no: "SETR2608170216",
        declaration_date: "2026-08-18",
        item_name: "cleaning kits",
        quantity: "2400.000",
      },
      ledger_check: OK_CHECK,
    },
    {
      id: 12,
      material_id: 1,
      source: "ledger",
      import_invoice_line_id: 17,
      linked_item_name: "cleaning kits",
      linked_shipment_id: 2,
      supplier: "SHENZHEN OTAO TECHNOLOGY CO L",
      unit_price_ex_vat: "178.78",
      unit_price_inc_vat: "196.66",
      effective_date: "2026-07-23",
      note: null,
      shipment: {
        id: 2,
        hbl_no: "SETR2607220324",
        declaration_date: "2026-07-23",
        item_name: "cleaning kits",
        quantity: "12000.000",
      },
      ledger_check: {
        ...OK_CHECK,
        ledger_unit_price_ex_vat: "178.78",
        ledger_unit_price_inc_vat: "196.66",
      },
    },
  ],
  // ★사용처 — prod 모양 재현(D-CPP-56 후속). 「어느 제품에 들어가나」 + 승인 여부.
  used_by: [
    {
      recipe_id: 7,
      product_name: "오하이 빛반사, 지문방지 매트 필름 3매",
      form_factor: "bar",
      status: "approved",
      quantity: "1.000",
    },
    {
      recipe_id: 8,
      product_name: "오하이 빛반사, 지문방지 매트 필름 2매",
      form_factor: "bar",
      status: "draft",
      quantity: "1.000",
    },
  ],
  used_by_count: 2,
};

const EMPTY_KIT: CostMaterial = {
  ...KIT,
  lot_count: 0,
  price_count: 0,
  stale_count: 0,
  latest_price_ex_vat: null,
  latest_price_inc_vat: null,
  latest_price_inc_derived: false,
  latest_price_source: null,
  prices: [],
};

/** 어긋난 연결 1건만 가진 종 — 「최신 단가」가 **비어야** 하는 상태(적대 리뷰 1R P1-1). */
function staleKit(check: Partial<CostLedgerCheck>): CostMaterial {
  const c: CostLedgerCheck = { ...OK_CHECK, ok: false, counts_as_evidence: false, ...check };
  return {
    ...KIT,
    lot_count: 0,
    price_count: 1,
    stale_count: 1,
    latest_price_ex_vat: null,
    latest_price_inc_vat: null,
    latest_price_inc_derived: false,
    latest_price_source: null,
    prices: [{ ...KIT.prices[0], ledger_check: c }],
  };
}

function ledgerRow(over: Partial<CostLedgerMaterialLine> = {}): CostLedgerMaterialLine {
  return {
    line_id: 15,
    shipment_id: 1,
    hbl_no: "SETR2608170216",
    declaration_date: "2026-08-18",
    item_name: "cleaning kits",
    line_type: "material",
    quantity: "2400.000",
    unit_cost_ex_vat: "190.82",
    unit_cost_inc_vat: "209.90",
    allocated_cost_krw: "54992.00",
    linked_material_id: null,
    linked_material_name: null,
    linked_price_id: null,
    shipment_status: "confirmed",
    linked_price_check: null,
    suggestion: {
      line_id: 15,
      item_name: "cleaning kits",
      material_id: 1,
      reason: "규칙 「cleaning kit」이 품목명에 전부 들어 있다 → 「cleaning kit」 제안",
      candidates: [1],
      ambiguous: false,
      unmatched: false,
    },
    ...over,
  };
}

// ════════════════════════ 순수 표시 규칙 ════════════════════════
describe("formatCostWon — 「없음」은 「0」이 아니다 (계약 §2-7)", () => {
  it("★null은 「—」다 — 0원으로 그리지 않는다 (FE-2 변이가 여기서 죽는다)", () => {
    expect(formatCostWon(null)).toBe("—");
    expect(formatCostWon(undefined)).toBe("—");
    expect(formatCostWon("")).toBe("—");
  });

  it("★진짜 0원은 0원이라고 그린다 — 미입력과 구분된다", () => {
    expect(formatCostWon("0")).toBe("0원");
  });

  it("소수 2자리까지 보존한다 — 178.78이 179로 반올림되면 대조가 깨진다", () => {
    expect(formatCostWon("178.78")).toBe("178.78원");
    expect(formatCostWon("190.82")).toBe("190.82원");
  });

  it("숫자가 아닌 값은 침묵하지 않고 「—」로 떨어진다", () => {
    expect(formatCostWon("N/A")).toBe("—");
  });
});

describe("materialStatusLabel / lotCountText / excelLabelText — 자백 문구", () => {
  it("미승인은 「미확인」이라고 말한다", () => {
    expect(materialStatusLabel("unconfirmed")).toBe("미확인");
    expect(materialStatusLabel("approved")).toBe("승인");
  });

  it("★로트 수를 말한다 — 표본 부족을 숨기지 않는다(계약 §9-5)", () => {
    expect(lotCountText({ lot_count: 2, price_count: 2, stale_count: 0 }, false)).toBe("로트 2건");
    expect(lotCountText({ lot_count: 2, price_count: 3, stale_count: 0 }, false)).toBe(
      "로트 2건 · 수동 1건",
    );
    expect(lotCountText({ lot_count: 0, price_count: 0, stale_count: 0 }, false)).toContain("단가 없음");
  });

  it("★엑셀 대응이 비면 「미확정」이라고 자백한다 (FE-6 · 계약 §9-3)", () => {
    expect(excelLabelText(null)).toContain("미확정");
    expect(excelLabelText("  ")).toContain("미확정");
    expect(excelLabelText("알콜솜 2EA")).toBe("알콜솜 2EA");
  });
});

describe("valuationBadgeText — `confirmed`를 «읽는다»", () => {
  const s = (over: Partial<CostSetting>): CostSetting[] => [
    {
      key: "valuation_method",
      value: "fifo",
      confirmed: false,
      note: null,
      updated_at: null,
      ...over,
    },
  ];

  it("★미신고 상태를 자백한다 — 「선입선출이 우리 신고 방법」이라고 쓰지 않는다(계약 §3)", () => {
    const t = valuationBadgeText(s({}))!;
    expect(t).toContain("무신고 시 법정 기본값");
    expect(t).toContain("신고 내역 미확인");
  });

  it("★confirmed=true면 문구가 바뀐다 (FE-5 하드코딩 변이가 여기서 죽는다)", () => {
    const t = valuationBadgeText(s({ confirmed: true }))!;
    expect(t).not.toContain("미확인");
    expect(t).toContain("신고 내역 확인분");
  });

  it("설정 행 자체가 없으면 침묵하지 않고 「확인 안 됨」이라 한다", () => {
    expect(valuationBadgeText([])).toContain("확인 안 됨");
  });
});

// ════════════════════════ 실제 렌더 ════════════════════════
describe("★합격 1의 표면 — 로트별 단가 2건이 화면에 그려진다", () => {
  it("두 로트의 단가·통관일·수입건이 나란히, 서로 다른 값으로 보인다 (FE-1 변이가 여기서 죽는다)", () => {
    render(<MaterialPriceHistory material={KIT} imported />);

    // 8/18 로트 — prod 실측 190.82 / 209.90
    const aug = screen.getByTestId("price-row-11");
    expect(within(aug).getByText("2026-08-18")).toBeTruthy();
    expect(within(aug).getByText("SETR2608170216")).toBeTruthy();
    expect(within(aug).getByText("209.9원")).toBeTruthy();   // VAT 포함(기본 표시)
    expect(within(aug).getByText("190.82원")).toBeTruthy();  // VAT 제외(보조)

    // 7/23 로트 — prod 실측 178.78 / 196.66
    const jul = screen.getByTestId("price-row-12");
    expect(within(jul).getByText("2026-07-23")).toBeTruthy();
    expect(within(jul).getByText("SETR2607220324")).toBeTruthy();
    expect(within(jul).getByText("196.66원")).toBeTruthy();
    expect(within(jul).getByText("178.78원")).toBeTruthy();

    // 두 행이 실제로 «다른 값»이다 — 같은 값을 두 번 그리면 이력이 아니다
    expect(within(aug).queryByText("178.78원")).toBeNull();
  });

  it("★단가 이력이 없으면 「0원」이 아니라 「빈 칸」이라고 말한다", () => {
    render(<MaterialPriceHistory material={EMPTY_KIT} imported />);
    expect(screen.getByText(/단가 이력이 없다/)).toBeTruthy();
    expect(screen.queryByText("0원")).toBeNull();
  });

  // ★D-CPP-56(2026-08-24)로 어휘가 「원장(로트)」→「원장」, 「수동 입력」→「등록가」가 됐다.
  //   단가 이력 표와 레시피 상태 열이 **같은 함수 하나**(`priceSourceLabel`)를 쓰므로, 이
  //   단언이 그 «한 벌» 성질을 지킨다 — 누가 상태 열만 고치고 여기를 두면 이 줄이 깨진다.
  it("출처가 원장인지 등록가인지 화면이 말한다", () => {
    render(<MaterialPriceHistory material={KIT} imported />);
    expect(screen.getAllByText("원장").length).toBe(2);
    expect(screen.queryByText("원장(로트)")).toBeNull();
  });

  // ★적대 리뷰 1R P2 **채택**(2026-08-24) — 「null 가드를 지워도 135건이 전부 초록」이었다.
  //   가드가 지키는 상태(`source`가 비어 오는 것)는 현재 백엔드에서 도달 불가로 «보이지만»,
  //   그건 추정이고 가드가 조용히 사라지는 것을 막는 비용은 이 세 줄뿐이다.
  //   ★단언은 「빈 문자열이 아니다」에 무게가 있다 — 가드가 없으면 화면에 **빈 칸**이 남고,
  //   이 화면에서 빈 칸은 「깜빡 잊었나」와 구별되지 않는다(계약 §2-7의 같은 결).
  it("★출처가 비어 오면 빈 칸이 아니라 「출처 미상」이라고 말한다", () => {
    expect(priceSourceLabel(null)).toBe("출처 미상");
    expect(priceSourceLabel(undefined)).toBe("출처 미상");
    expect(priceSourceLabel("")).toBe("출처 미상");
  });
});

describe("부자재 목록 — 미확인 상태와 최신 단가가 보인다", () => {
  it("미승인 종에 「미확인」 배지가 그려진다", () => {
    render(
      <MaterialList materials={[KIT]} selectedId={1} onSelect={() => {}} importedIds={new Set()} />,
    );
    const row = screen.getByTestId("material-1");
    expect(within(row).getByText("cleaning kit")).toBeTruthy();
    expect(within(row).getByText("미확인")).toBeTruthy();
    expect(screen.getByTestId("material-1-latest").textContent).toBe("209.9원");
    expect(within(row).getByText(/로트 2건/)).toBeTruthy();
    // ★적대 리뷰 P2 **채택**(2026-08-24) — 출처 `<span>`을 통째로 지워도 137건이 전부
    //   초록이었다(변이 6 SURVIVED). 이 저장소의 상습 패턴 그대로다: **함수는 값을 만드는데
    //   화면엔 안 뜬다.** `priceSourceLabel`의 단위 테스트만으로는 「카드 안에 그려지는가」를
    //   못 묻는다 — 그래서 여기서 **렌더된 카드 안**을 본다.
    expect(within(row).getByText("원장")).toBeTruthy();
  });

  // ★문구가 「—」 → **「단가 없음」**으로 바뀌었다 (D-CPP-56 후속, Jino 2026-08-24:
  //   *"현재 단가 … 좀 더 직관적이었으면 좋겠어"*). **이 테스트의 의도는 그대로다** —
  //   재는 것은 「0원으로 보이지 않는다」이고, 회색 「—」보다 「단가 없음」이 그 의도를
  //   더 강하게 만족한다. 아래 `queryByText(/0원/)` 줄이 그 원래 단언이다.
  it("★단가가 없는 종은 「단가 없음」이라고 말한다 — 0원으로 보이면 안 된다", () => {
    render(
      <MaterialList materials={[EMPTY_KIT]} selectedId={1} onSelect={() => {}} importedIds={new Set()} />,
    );
    expect(screen.getByTestId("material-1-latest").textContent).toBe("단가 없음");
    const row = screen.getByTestId("material-1");
    expect(within(row).queryByText(/0원/)).toBeNull();
    // ★회색 「—」로 되돌아가면 이 줄이 깨진다 — 「없다」를 «말하는» 것이 이 항목의 요점이다.
    expect(screen.getByTestId("material-1-latest").textContent).not.toBe("—");
  });

  // ── D-CPP-62 S1 — 손으로 넣은 단가가 「단가 없음」으로 보이던 자리 ──────────────
  //
  // ★prod 실증(2026-08-27): 17종이 `ex`만 있고 `inc`는 NULL이었다. 계산은 ×1.1로 만들어
  //   정상으로 썼는데(레시피 34 breakdown에 188.10) **목록만 「단가 없음」**이라 말했다.
  //   Jino가 방금 넣은 값이 안 들어간 것처럼 보이는 화면이었다.
  //
  // 이 두 항목이 죽이는 변이:
  //   FE-8 「현재 단가」가 다시 `inc` 칸만 읽게 하면 → 첫 항목이 「단가 없음」이 되어 죽는다
  //   FE-9 `×1.1 파생` 배지 `<span>`을 지우면 → 둘째 항목이 죽는다
  //        (백엔드 테스트는 payload의 `latest_price_inc_derived`까지만 본다 — 배지는 여기서만 죽는다)
  it("★부가세 «제외»로만 넣은 단가도 값으로 보인다 — 「단가 없음」이 아니다", () => {
    const EX_ONLY: CostMaterial = {
      ...KIT,
      id: 8,
      name: "패키지 (flip)",
      // prod 그대로: 사람이 부가세 제외 171을 넣었고 포함 칸은 비었다 → 백엔드가 188.10을 만든다
      latest_price_ex_vat: "171.00",
      latest_price_inc_vat: "188.10",
      latest_price_inc_derived: true,
      latest_price_source: "manual",
      lot_count: 0,
      price_count: 1,
      prices: [],
    };
    render(
      <MaterialList materials={[EX_ONLY]} selectedId={8} onSelect={() => {}} importedIds={new Set()} />,
    );
    expect(screen.getByTestId("material-8-latest").textContent).toBe("188.1원");
    expect(screen.getByTestId("material-8-latest").textContent).not.toBe("단가 없음");
  });

  // ★★적대 리뷰 변이 MF3이 **살아남아서** 고친 항목이다. 초판은 `...KIT`을 퍼뜨려
  //   `latest_price_source: "ledger"`인 픽스처 하나로만 쟀는데, **prod의 파생 종 18개는
  //   100% `manual`**이다. 그래서 배지 조건에 `&& source === "ledger"`를 붙이는 변이가
  //   **실사용 전건에서 배지를 지우는데도 624건이 침묵**했다. 픽스처가 prod와 다르면
  //   그 테스트는 지키는 «척»만 한다 — 이 저장소가 반복해 밟은 자리다.
  //   ⇒ 이제 **출처와 무관하게** 파생 여부만으로 배지가 정해지는 것을 못 박는다.
  it("★그 값이 «우리가 만든 값»이면 화면이 그렇게 말한다 — 출처와 무관하다 (×1.1 파생)", () => {
    const DERIVED_MANUAL: CostMaterial = {
      ...KIT,
      id: 8,
      latest_price_inc_vat: "188.10",
      latest_price_inc_derived: true,
      // prod 실측: 파생 종 18개가 전부 `manual`이다 — 여기가 실사용 경로다.
      latest_price_source: "manual",
      prices: [],
    };
    const DERIVED_LEDGER: CostMaterial = { ...DERIVED_MANUAL, id: 10, latest_price_source: "ledger" };
    const STORED: CostMaterial = { ...DERIVED_MANUAL, id: 9, latest_price_inc_derived: false };

    render(
      <MaterialList
        materials={[DERIVED_MANUAL, DERIVED_LEDGER, STORED]}
        selectedId={8}
        onSelect={() => {}}
        importedIds={new Set()}
      />,
    );
    // 실사용 경로(manual)에서 배지가 뜬다 — MF3 변이가 여기서 죽는다.
    expect(screen.getByTestId("material-8-inc-derived").textContent).toBe("×1.1 파생");
    // 출처가 달라도 «파생이면» 뜬다 — 조건을 출처로 좁히는 변이 전반이 여기서 죽는다.
    expect(screen.getByTestId("material-10-inc-derived").textContent).toBe("×1.1 파생");
    // ★배지가 «항상 켜지는 장식»이 되면 이 줄이 깨진다 — 저장된 값엔 안 붙어야 한다.
    expect(screen.queryByTestId("material-9-inc-derived")).toBeNull();
  });
});

// ── 적대 리뷰 변이 MF2가 살아남은 자리 ────────────────────────────────────────
//
// ★업로드 결과의 「원가표 이상 N건」 블록을 통째로 지워도 **44파일 624건이 전부 초록**이었다.
//   그때는 이 블록이 **파일의 모순(같은 부자재가 두 값)이 사람에게 보이는 유일한 표면**이었다
//   — `material_refs` 리포트가 아직 화면에 안 닿았기 때문이다. **이제는 닿는다**(D-CPP-62 S2 —
//   「참고값 모순 보류」 블록, 아래 `업로드 결과 — 참고값 미러 리포트` describe). 이 블록은
//   여전히 **원가표 파싱 단계의** 이상(단위 불일치 등)을 보이는 자리라 존치한다.
//   보호막이 없는 문장은 문장이 아니다.
describe("업로드 결과 — 원가표 이상이 사람에게 보인다", () => {
  it("★`cost_table_anomalies`가 화면에 그려진다 (MF2 변이가 여기서 죽는다)", () => {
    const result = {
      recipes_created: 0,
      recipes_updated: 12,
      skipped_approved: 20,
      skipped_pinned: 10,
      unmatched: 0,
      materials_seen: 139,
      cost_table_recipes: 60,
      cost_table_anomalies: ["price_conflict:패키지 (fold)=171.00|320.00"],
      cost_table_items: 60,
      pins: { relinked: 10, lost: 0, ambiguous: 0 },
      mapping_options: 0,
      mapping_anomalies: [],
      groups: 0,
      report: [],
    } as unknown as CostImportResult;

    render(<RecipeImportPanel busy={false} onImport={() => {}} result={result} />);

    expect(screen.getByText("원가표 이상 1건")).toBeTruthy();
    // ★건수만 세는 게 아니라 **내용이 그려지는지** 본다 — 목록을 지우는 변이도 죽어야 한다.
    expect(
      screen.getByText("price_conflict:패키지 (fold)=171.00|320.00"),
    ).toBeTruthy();
  });
});

// ── 업로드 결과 — 참고값 미러 리포트 (D-CPP-62 S2) ────────────────────────────
//
// S1 적대 리뷰가 이 배선을 **승인 조건**으로 걸었다: *"P2-1을 S2에서 반드시 닫는다는 전제
// 위에서 이 읽기를 승인한다. 닫히지 않으면 그건 미러가 아니라 그냥 조용한 쓰기다."*
// 백엔드는 두 갈래(원가만 업로드=347줄 early return · 원가+매핑 동시 업로드=533줄 최종
// return)에서 각각 `material_refs`를 싣는다. **한 갈래만 밟는 테스트는 MB1을 되살린다**
// (변이가 한쪽 return에서만 `material_refs`를 지워도 안 잡힌다) — 그래서 두 갈래를 각각
// 흉내 낸 픽스처로 따로 잰다.
//
// ★값(`old`·`new`·`kept`)은 문자열이거나 **null일 수 있다**(백엔드 `_money()`가 새 종엔
//   옛값 없이 `None`을 준다) — MF3 교훈(픽스처가 prod와 달라 변이가 침묵한 전례)을 여기서
//   반복하지 않으려면 null 케이스를 픽스처에 반드시 넣는다.
describe("업로드 결과 — 참고값 미러 리포트가 사람에게 보인다 (D-CPP-62 S2)", () => {
  const baseFields = {
    recipes_created: 0,
    recipes_updated: 5,
    skipped_approved: 0,
    unmatched: 0,
    materials_seen: 20,
    cost_table_recipes: 10,
    cost_table_anomalies: [],
    report: [],
  };

  it("★원가만 업로드한 응답(백엔드 347줄 갈래)에서 갱신·모순이 둘 다 그려진다", () => {
    const result = {
      ...baseFields,
      cost_table_items: 10,
      pins: { relinked: 0, lost: 0, ambiguous: 0 },
      mapping_options: 0,
      mapping_anomalies: [],
      groups: 0,
      updated_halves: ["구성"],
      untouched: ["SKU 링크·옵션 수 — 매핑 정본을 안 올렸으므로 그대로 둔다"],
      material_refs: {
        refreshed: [{ name: "패키지 (fold)", old: "171.00", new: "320.00" }],
        refreshed_count: 1,
        conflicted: [
          { name: "지문방지 필름", values: ["1,500.00", "1,750.00"], kept: null },
        ],
        conflicted_count: 1,
      },
    } as unknown as CostImportResult;

    render(<RecipeImportPanel busy={false} onImport={() => {}} result={result} />);

    expect(screen.getByTestId("import-material-refs-refreshed").textContent).toContain(
      "참고값 갱신",
    );
    expect(screen.getByText(/패키지 \(fold\) · 171\.00 → 320\.00/)).toBeTruthy();
    expect(screen.getByTestId("import-material-refs-conflicted").textContent).toContain(
      "참고값 모순 보류",
    );
    expect(screen.getByText(/지문방지 필름/)).toBeTruthy();
    // ★null(옛 값 없음)이 조용히 사라지지 않고 「(없음)」으로 보인다.
    expect(screen.getByText(/그대로 둔 값 \(없음\)/)).toBeTruthy();
  });

  it("★원가+매핑 동시 업로드한 응답(백엔드 533줄 갈래)에서도 갱신·모순이 그려진다", () => {
    const result = {
      ...baseFields,
      cost_table_items: 10,
      skipped_pinned: 3,
      pins: { relinked: 2, lost: 0, ambiguous: 0 },
      mapping_options: 8,
      mapping_anomalies: [],
      groups: 4,
      updated_halves: ["구성", "SKU 링크"],
      untouched: [],
      material_refs: {
        refreshed: [{ name: "지문방지 내부 필름", old: null, new: "650.00" }],
        refreshed_count: 1,
        conflicted: [],
        conflicted_count: 0,
      },
    } as unknown as CostImportResult;

    render(<RecipeImportPanel busy={false} onImport={() => {}} result={result} />);

    expect(screen.getByTestId("import-material-refs-refreshed").textContent).toContain(
      "참고값 갱신",
    );
    // ★null(신규 종, 옛값 없음)도 「(없음)」으로 보인다 — 지어내지 않는다.
    expect(screen.getByText(/지문방지 내부 필름 · \(없음\) → 650\.00/)).toBeTruthy();
    expect(screen.getByTestId("import-material-refs-conflicted").textContent).toContain(
      "참고값 모순 보류",
    );
    expect(screen.getByTestId("import-material-refs-conflicted").textContent).toContain("0");
  });

  it("★갱신 0건일 때도 「참고값 갱신 0건」이 보인다 — 「안 실림」과 「0건」이 갈려야 한다", () => {
    const result = {
      ...baseFields,
      cost_table_items: 10,
      pins: { relinked: 0, lost: 0, ambiguous: 0 },
      mapping_options: 0,
      mapping_anomalies: [],
      groups: 0,
      material_refs: {
        refreshed: [],
        refreshed_count: 0,
        conflicted: [],
        conflicted_count: 0,
      },
    } as unknown as CostImportResult;

    render(<RecipeImportPanel busy={false} onImport={() => {}} result={result} />);

    expect(screen.getByTestId("import-material-refs-refreshed").textContent).toBe(
      "참고값 갱신 0건",
    );
    expect(screen.getByTestId("import-material-refs-conflicted").textContent).toContain(
      "참고값 모순 보류",
    );
    expect(screen.queryByTestId("import-material-refs-missing")).toBeNull();
  });

  it("★`material_refs`가 응답에 없으면(구버전 백엔드) 「못 받았다」 경고가 뜬다 — 「모순 0건」으로 오독되면 안 된다", () => {
    const result = {
      ...baseFields,
      cost_table_items: 10,
      pins: { relinked: 0, lost: 0, ambiguous: 0 },
      mapping_options: 0,
      mapping_anomalies: [],
      groups: 0,
    } as unknown as CostImportResult;

    render(<RecipeImportPanel busy={false} onImport={() => {}} result={result} />);

    expect(screen.getByTestId("import-material-refs-missing").textContent).toContain(
      "참고값 리포트가 응답에 없다",
    );
    expect(screen.queryByTestId("import-material-refs-refreshed")).toBeNull();
    expect(screen.queryByTestId("import-material-refs-conflicted")).toBeNull();
  });
});

describe("원장 부자재 라인 — 「연결」이 사람의 확정이다 (계약 §5-2)", () => {
  it("★미연결 라인에 「연결」 버튼이 그려지고 누르면 제안된 종으로 연결된다 (FE-3 변이가 여기서 죽는다)", () => {
    const onLink = vi.fn();
    render(
      <LedgerMaterialLines rows={[ledgerRow()]} materials={[KIT]} onLink={onLink} />,
    );
    const row = screen.getByTestId("ledger-line-15");
    expect(within(row).getByText("190.82원")).toBeTruthy();
    const btn = within(row).getByRole("button", { name: /연결/ });
    btn.click();
    expect(onLink).toHaveBeenCalledWith(1, 15);
  });

  it("★이미 연결된 라인엔 버튼이 없고 연결된 종 이름이 보인다 — 같은 로트가 두 번 안 세진다", () => {
    render(
      <LedgerMaterialLines
        rows={[ledgerRow({ linked_material_id: 1, linked_material_name: "cleaning kit", linked_price_id: 11 })]}
        materials={[KIT]}
        onLink={() => {}}
      />,
    );
    const row = screen.getByTestId("ledger-line-15");
    expect(within(row).getByText(/연결됨 · cleaning kit/)).toBeTruthy();
    expect(within(row).queryByRole("button")).toBeNull();
  });

  it("★미매칭 라인은 「미매칭」과 «그 이유»를 둘 다 그린다 (FE-4 변이가 여기서 죽는다)", () => {
    render(
      <LedgerMaterialLines
        rows={[
          ledgerRow({
            suggestion: {
              line_id: 15,
              item_name: "cleaning kits",
              material_id: null,
              reason: "매칭 규칙에 걸리는 부자재 종이 없다 — 미매칭. 종을 만들거나 규칙을 고쳐야 한다.",
              candidates: [],
              ambiguous: false,
              unmatched: true,
            },
          }),
        ]}
        materials={[KIT]}
        onLink={() => {}}
      />,
    );
    const row = screen.getByTestId("ledger-line-15");
    expect(within(row).getByText("미매칭")).toBeTruthy();
    expect(within(row).getByText(/매칭 규칙에 걸리는 부자재 종이 없다/)).toBeTruthy();
    // 고를 대상이 없으므로 버튼도 없다 — 억지로 아무 종에나 붙이지 않는다
    expect(within(row).queryByRole("button")).toBeNull();
  });

  it("★후보가 여럿이면 버튼 없이 「사람이 확정한다」 이유가 보인다", () => {
    render(
      <LedgerMaterialLines
        rows={[
          ledgerRow({
            suggestion: {
              line_id: 15,
              item_name: "cleaning kits",
              material_id: null,
              reason: "후보 2종(cleaning kit / cleaning kit (구형)) — 자동으로 고르지 않는다. 사람이 확정한다.",
              candidates: [1, 7],
              ambiguous: true,
              unmatched: false,
            },
          }),
        ]}
        materials={[KIT]}
        onLink={() => {}}
      />,
    );
    const row = screen.getByTestId("ledger-line-15");
    expect(within(row).getByText(/사람이 확정한다/)).toBeTruthy();
    expect(within(row).queryByRole("button")).toBeNull();
  });

  it("원장에 부자재 라인이 없으면 «없다»고 말한다 — 빈 표를 그리지 않는다", () => {
    render(<LedgerMaterialLines rows={[]} materials={[KIT]} />);
    expect(screen.getByText(/부자재\(`material`\) 라인이 없다/)).toBeTruthy();
  });
});

describe("평가방법 배지 — 시스템이 스스로 미확인을 자백한다", () => {
  it("confirmed=false면 ⚠ 배지로 그린다", () => {
    render(
      <ValuationBadge
        settings={[
          { key: "valuation_method", value: "fifo", confirmed: false, note: null, updated_at: null },
        ]}
      />,
    );
    expect(screen.getByText(/신고 내역 미확인/)).toBeTruthy();
    expect(screen.getByText(/⚠/)).toBeTruthy();
  });
});

// ══════════════ 재검사 자백 — 적대 리뷰 1R P1의 표면 ══════════════
//
// ★재는 것은 하나다: **어긋난 단가가 아무 말 없이 「최신」 자리에 앉아 있지 않는가.**
// 네 갈래(reopen · 값 변경 · 삭제 · rowid 재사용)는 백엔드가 판정하고, 화면은 그 판정을
// 사람이 읽을 수 있게 내놓는지가 여기서 결정된다.
describe("lotCountText / latestPriceNote — 「왜 최신 단가가 비었나」를 말한다", () => {
  it("★어긋난 연결을 따로 센다 — 침묵하면 「단가가 왜 없지?」가 결함 조사로 번진다", () => {
    expect(lotCountText({ lot_count: 1, price_count: 2, stale_count: 1 }, false)).toContain(
      "원장과 어긋난 연결 1건",
    );
    expect(lotCountText({ lot_count: 1, price_count: 3, stale_count: 1 }, false)).toContain("수동 1건");
  });

  it("★전부 어긋나면 「최신 단가 없음」의 이유를 말한다 (0원이 아니라 «근거가 없다»)", () => {
    const t = latestPriceNote({ lot_count: 0, price_count: 1, stale_count: 1 })!;
    expect(t).toContain("최신 단가 없음");
    expect(t).toContain("어긋나");
  });

  it("일부만 어긋나면 «뺐다»고 말하되 이력엔 남아 있음을 밝힌다", () => {
    const t = latestPriceNote({ lot_count: 1, price_count: 2, stale_count: 1 })!;
    expect(t).toContain("뺐다");
    expect(t).toContain("이력");
  });

  it("어긋남이 없으면 잔소리하지 않는다 — 늘 뜨는 경고는 안 읽힌다", () => {
    expect(latestPriceNote({ lot_count: 2, price_count: 2, stale_count: 0 })).toBeNull();
    expect(ledgerCheckText(OK_CHECK)).toBeNull();
    expect(ledgerCheckText(null)).toBeNull();
  });
});

describe("★MaterialPriceHistory — 어긋난 행이 «스스로 자백»한다 (FE-7)", () => {
  it("①reopen: 「수입건 확정 해제됨」과 그 사유가 값 옆에 그려진다", () => {
    const m = staleKit({
      status: "unconfirmed",
      label: "수입건 확정 해제됨",
      detail: "수입건이 확정 상태가 아니다(status=draft) — 원장은 그때 단가를 지웠다.",
      refreshable: false,
    });
    render(<MaterialPriceHistory material={m} imported />);
    const cell = screen.getByTestId("price-check-11");
    expect(within(cell).getByText(/수입건 확정 해제됨/)).toBeTruthy();
    expect(within(cell).getByText(/원장은 그때 단가를 지웠다/)).toBeTruthy();
    // 값 자체는 남는다 — 근거 보존이 이 테이블의 존재 이유다
    expect(screen.getByText("190.82원")).toBeTruthy();
  });

  it("②값 변경: 저장값과 «현 원장값»이 나란히 보이고 「갱신」이 열린다", () => {
    const onRefresh = vi.fn();
    const m = staleKit({
      status: "changed",
      label: "원장 값이 달라졌다",
      detail: "저장값 190.82 / 현 원장값 198.91 (VAT 제외) — 원장이 재계산됐다.",
      refreshable: true,
      ledger_unit_price_ex_vat: "198.91",
    });
    render(<MaterialPriceHistory material={m} onRefresh={onRefresh} imported />);
    const cell = screen.getByTestId("price-check-11");
    expect(within(cell).getByText(/원장 값이 달라졌다/)).toBeTruthy();
    expect(within(cell).getByText(/198.91원/)).toBeTruthy();
    const row = screen.getByTestId("price-row-11");
    within(row).getByRole("button", { name: "갱신" }).click();
    expect(onRefresh).toHaveBeenCalledWith(11);
  });

  it("③삭제: 고아 행이 「원장 라인 없음」으로 보이고 「갱신」 버튼은 없다", () => {
    const m = staleKit({
      status: "missing",
      label: "원장 라인 없음",
      detail: "이 단가가 나온 원장 라인이 지금은 없다 — 해제하고 다시 연결한다.",
      refreshable: false,
    });
    render(<MaterialPriceHistory material={m} onRefresh={() => {}} onDelete={() => {}} imported />);
    expect(within(screen.getByTestId("price-check-11")).getByText(/원장 라인 없음/)).toBeTruthy();
    const row = screen.getByTestId("price-row-11");
    expect(within(row).queryByRole("button", { name: "갱신" })).toBeNull();
    expect(within(row).getByRole("button", { name: "해제" })).toBeTruthy();  // 처분 경로는 있다
  });

  it("④rowid 재사용: 「다른 품목을 가리킨다」가 보이고 갱신으로 삼킬 수 없다", () => {
    const m = staleKit({
      status: "item_mismatch",
      label: "다른 품목을 가리킨다",
      detail:
        "연결 당시 품목은 「cleaning kits」인데 지금 그 라인은 「Glass_iP12promax」이다 — id가 재사용됐다.",
      refreshable: false,
    });
    render(<MaterialPriceHistory material={m} onRefresh={() => {}} imported />);
    const cell = screen.getByTestId("price-check-11");
    expect(within(cell).getByText(/다른 품목을 가리킨다/)).toBeTruthy();
    expect(within(cell).getByText(/Glass_iP12promax/)).toBeTruthy();
    expect(
      within(screen.getByTestId("price-row-11")).queryByRole("button", { name: "갱신" }),
    ).toBeNull();
  });

  it("정상 행엔 경고가 없다 — 늘 노란 화면은 아무것도 못 말한다", () => {
    render(<MaterialPriceHistory material={KIT} imported />);
    expect(screen.queryByText(/⚠/)).toBeNull();
    expect(within(screen.getByTestId("price-check-11")).getByText("원장과 일치")).toBeTruthy();
  });
});

describe("★원장 라인 목록 — 확정이 풀린 건도 사라지지 않는다 (적대 리뷰 1R P1-1)", () => {
  it("「확정 해제됨」이 그려진다 — 목록에서 빠지면 어긋남이 화면에서 통째로 사라진다", () => {
    render(
      <LedgerMaterialLines
        rows={[
          ledgerRow({
            shipment_status: "draft",
            linked_material_id: 1,
            linked_material_name: "cleaning kit",
            linked_price_id: 11,
            linked_price_check: {
              ...OK_CHECK,
              status: "unconfirmed",
              ok: false,
              counts_as_evidence: false,
              label: "수입건 확정 해제됨",
              refreshable: false,
            },
          }),
        ]}
        materials={[KIT]}
        onLink={() => {}}
      />,
    );
    const row = screen.getByTestId("ledger-line-15");
    // 수입건 칸: 「이 로트는 지금 확정이 아니다」
    expect(within(row).getByText(/확정 해제됨\(draft\)/)).toBeTruthy();
    // 상태 칸: 「그래서 붙어 있는 단가가 어긋났다」 — 둘은 다른 사실이고 둘 다 필요하다
    expect(within(row).getByText(/⚠ 수입건 확정 해제됨/)).toBeTruthy();
  });

  it("확정 상태면 그 경고를 안 그린다", () => {
    render(<LedgerMaterialLines rows={[ledgerRow()]} materials={[KIT]} onLink={() => {}} />);
    expect(screen.queryByText(/확정 해제됨/)).toBeNull();
  });
});

// ── S2 (적대 리뷰 1R P1-1) — 「못 쓴다」의 사유가 사람 말로 화면에 닿는가 ──
//
// 초판은 단가가 178.78원으로 실재하는데도 「단가 미확정」이라 말하고 값을 「—」로 감췄다.
// 사유가 틀리면 사람이 틀린 일을 한다(이미 있는 단가를 입력하러 간다). 아래가 그 자리를 지킨다.
describe("표준원가 계산 내역 — 못 쓰는 사유가 사람 말이 된다", () => {
  const UNAPPROVED: CostStandard = {
    computable: false,
    std_cost_ex_vat: null,
    std_cost_inc_vat: null,
    reason: "부자재 종 미승인 — 단가는 있다, 부자재 탭에서 「승인」 (1건: cleaning kit)",
    unresolved: ["cleaning kit"],
    partial_ex_vat: "0",
    partial_inc_vat: "0",
    line_count: 1,
    lines: [
      {
        label: "cleaning kit",
        quantity: "1",
        unit_price_ex_vat: "178.78",
        unit_price_inc_vat: "196.66",
        amount_ex_vat: null,
        amount_inc_vat: null,
        price_status: "material_unapproved",
        inc_derived: false,
        price_source: "ledger",
        price_note: null,
        material_id: 1,
        usable: false,
        // cleaning kit엔 엑셀 대응 항목이 없다 → 참고값도 없다(위 KIT과 같은 사실).
        excel_ref_price: null,
      },
    ],
  };

  it("상태 이름이 아니라 «무엇을 해야 하는지»가 보인다", () => {
    render(<StandardBreakdown standard={UNAPPROVED} />);
    expect(screen.getByText(/종 미승인 — 부자재 탭에서 「승인」/)).toBeTruthy();
    // 원시 상태 문자열이 그대로 새어 나오면 안 된다
    expect(screen.queryByText("material_unapproved")).toBeNull();
  });

  it("★실재하는 단가를 감추지 않는다 — 178.78원이 보인다", () => {
    render(<StandardBreakdown standard={UNAPPROVED} />);
    expect(screen.getByText("178.78원")).toBeTruthy();
    // 합계는 «없음»이다 — 부분합을 표준원가로 그리지 않는다
    expect(screen.getByText(/부분합/)).toBeTruthy();
  });
});
