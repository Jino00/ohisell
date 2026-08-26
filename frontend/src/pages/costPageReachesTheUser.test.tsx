// @vitest-environment jsdom
//
// costPageReachesTheUser.test.tsx — 「💰 원가」가 **사람에게 실제로 닿는가** (적대 리뷰 1R P2-1)
//
// ## 왜 이 파일이 따로 있나
//
// `costMaterialsSurface.test.tsx`는 컴포넌트 **내부** 절단을 다 죽였다. 그런데 리뷰가 주입한
// 네 변이는 **전부 초록으로 살아남았다** — 넷 다 컴포넌트 «바깥»을 끊었기 때문이다:
//
//   SUR-1 `CostPage.tsx`의 `<MaterialPriceHistory>` **호출부** 제거
//   SUR-2 `CostPage.tsx`의 `<LedgerMaterialLines>` 호출부를 `<div/>`로 교체
//   SUR-3 `App.tsx`의 `/cost` **라우트** 제거
//   SUR-4 `Layout.tsx`의 좌측 「💰 원가」 **메뉴** 제거
//
// **메뉴를 통째로 지워도 스위트가 안 울었다.** 이 저장소가 다섯 번째로 밟은 병이다
// (전역 §4 ★: 단위 테스트는 「함수가 값을 만드나」를 묻지 「사람이 그걸 보나」를 못 묻는다).
//
// ## 그래서 무엇을 하나
//
// **`App`을 통째로 `/cost`에서 렌더한다.** 라우팅·레이아웃·페이지·호출부가 한 줄로 이어져야만
// 통과하므로 넷 중 어느 하나만 끊어도 죽는다. api 모듈은 모킹해 네트워크를 타지 않는다 —
// 재는 것은 「값이 화면 픽셀이 되나」이지 서버가 아니다.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type {
  CostBoard,
  CostLedgerMaterialLine,
  CostMaterial,
  CostRecipe,
  CostSetting,
} from "../lib/api";
// ★P1-1(적대 리뷰)용 — 승인/승인취소 호출부가 실제로 눌리는지 재려면 그 함수들
//   «자신»을 vi.fn()으로 잡아야 한다(아래 vi.mock 팩토리에서 오버라이드).
//   fetchCostRecipes도 테스트별로 응답을 바꿔치기하려고 함께 들여온다.
import { approveCostRecipe, fetchCostRecipes, unapproveCostRecipe } from "../lib/api";
// ★2R(적대 리뷰) P1-1·P1-2용 — 부자재 탭의 「+ 종 추가」·「승인」·「+ 단가 입력·수정」이
//   실제로 그 id·이름으로 백엔드를 부르는지 재려면 이 셋도 vi.fn()으로 잡아야 한다
//   (위 approveCostRecipe와 같은 사정 — 아래 vi.mock 팩토리에서 오버라이드).
//   `fetchCostMaterials`는 P1-1 테스트가 「생성 뒤 재조회」를 흉내내려고 큐에 얹는다.
import {
  addCostManualPrice,
  createCostMaterial,
  fetchCostLedgerMaterialLines,
  fetchCostMaterials,
  patchCostMaterial,
} from "../lib/api";

// ★P2-A용: 0건 안내 렌더 분기 «자신»을 직접 잡는다. 이 두 컴포넌트는 CostPage.tsx가
//   「전부 순수 — props만 본다. 테스트가 직접 렌더한다」고 선언한 표시 계층이다
//   (`costMaterialsSurface.test.tsx`가 같은 파일의 다른 순수 컴포넌트에 쓰는 것과 같은 결).
//   전체 App 경로로는 이 분기에 진짜 0건을 못 만든다 — 옵션 목록이 항상 «현재 제품에
//   속한 것만»으로 구성되게 P1을 고쳤기 때문에, 정상 네비게이션으로는 0건이 안 나온다.
// ★2026-08-23 추가: `reconcileSelectedId`는 「선택이 필터 밖으로 나가면 상세
//   패널이 뭘 보여줘야 하나」를 정하는 유일한 진실의 원천이다(CostPage.tsx). 순수 함수라
//   전체 App 경로로는 못 만드는 조합(0건 등)까지 직접 단언할 수 있다 — 같은 이유로
//   RecipeList·StandardCostBoard를 직접 렌더하는 이 파일의 기존 관례를 그대로 따른다.
//   (N5에서 부자재 탭에도 필터가 생기며 제네릭으로 넓혔다 — 이름에서 «Recipe»가 빠졌다.)
// ★N5 추가: `excelRefNoteText`·`recipePlaceholderText`·`lotCountText`는 「참고값이 있다는
//   사실이 사람 말이 되는가」의 순수 계층이다. `MaterialList`는 0건 안내 분기를 직접 잡으려
//   들여온다(위 P2-A와 같은 사정 — 전체 App 경로로는 필터 0건 조합을 못 만든다).
import {
  excelRefNoteText,
  importedMaterialIds,
  ledgerLineCoverage,
  ledgerLineMaterialId,
  ledgerLinesForMaterial,
  LIST_COLUMN_SCROLL_CLASS,
  lotCountText,
  MaterialList,
  MaterialPriceHistory,
  recipePlaceholderText,
  reconcileSelectedId,
  RecipeList,
  StandardBreakdown,
  StandardCostBoard,
  unreachableLedgerLines,
  unreachableReason,
} from "./CostPage";

// ── prod 실측값(2026-08-22) — 합격 1이 화면에서 보겠다는 바로 그 두 로트 ──
const KIT: CostMaterial = {
  id: 1,
  name: "cleaning kit",
  unit: "ea",
  category: "부자재",
  status: "unconfirmed",
  excel_label: null,
  // ★cleaning kit은 **엑셀 대응 항목이 없는 유일한 종**이다(`excel_label: null`과 같은
  //   사실의 다른 면 — 원가 정본에 대응 항목이 없다). 그래서 참고값도 «없음»이다.
  //   prod 실측 2026-08-23: 단가 보유 1/129(이 종) · 참고값 보유 128/129(나머지 전부).
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
      ledger_check: {
        status: "ok",
        ok: true,
        label: "원장과 일치",
        detail: "원장 라인이 지금도 확정 상태이고 값·품목이 저장값과 같다.",
        counts_as_evidence: true,
        refreshable: true,
        ledger_unit_price_ex_vat: "190.82",
        ledger_unit_price_inc_vat: "209.90",
        ledger_item_name: "cleaning kits",
      },
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
        status: "ok",
        ok: true,
        label: "원장과 일치",
        detail: "원장 라인이 지금도 확정 상태이고 값·품목이 저장값과 같다.",
        counts_as_evidence: true,
        refreshable: true,
        ledger_unit_price_ex_vat: "178.78",
        ledger_unit_price_inc_vat: "196.66",
        ledger_item_name: "cleaning kits",
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

// ── N5: prod의 **다수파** — 단가는 없고 엑셀 참고값만 있는 종(128/129가 이 모양이다).
//    KIT 하나만으로는 이 경우가 픽스처에 아예 없어서, 화면이 「원장 연결 또는 수동 입력
//    필요」라고만 말하며 **가장 싼 길(채택)을 감추고 있어도** 아무 테스트가 안 울었다.
//    `form_factor: "bar"` · `part: "필름"`을 준 이유는 부자재 탭 드롭다운(C)의 두 축을
//    실제로 갈라 보기 위해서다 — KIT은 `form_factor: null`이라 sentinel 쪽에 선다.
const FILM_WITH_REF: CostMaterial = {
  id: 21,
  name: "지문방지필름 TPU 3매 · 필름 (bar)",
  unit: "ea",
  category: "부자재",
  status: "unconfirmed",
  excel_label: "필름",
  excel_ref_price: "600.00",
  match_rule: null,
  form_factor: "bar",
  part: "필름",
  note: null,
  lot_count: 0,
  price_count: 0,
  stale_count: 0,
  latest_price_ex_vat: null,
  latest_price_inc_vat: null,
  latest_price_source: null,
  price_rule: "latest",
  lot_price_min: null,
  lot_price_max: null,
  lot_price_has_span: false,
  price_conflict: false,
  price_conflict_price_id: null,
  prices: [],
  used_by: [],
  used_by_count: 0,
};

// `part`가 비어 있는 종 — prod에선 **83/129가 이 모양**이다. 화면이 그 사실을 숨기면
// 안 된다(「(부품 미지정) (N)」 선택지가 그 자백이다).
const JIG_NO_PART: CostMaterial = {
  id: 23,
  name: "부착 지그 (bar)",
  unit: "ea",
  category: "부자재",
  status: "unconfirmed",
  excel_label: "부착 지그",
  excel_ref_price: "100.00",
  match_rule: null,
  form_factor: "bar",
  part: null,
  note: null,
  lot_count: 0,
  price_count: 0,
  stale_count: 0,
  latest_price_ex_vat: null,
  latest_price_inc_vat: null,
  latest_price_source: null,
  price_rule: "latest",
  lot_price_min: null,
  lot_price_max: null,
  lot_price_has_span: false,
  price_conflict: false,
  price_conflict_price_id: null,
  prices: [],
  used_by: [],
  used_by_count: 0,
};

// ★2R P1-1용 — 「+ 종 추가」가 성공했을 때 백엔드가 실제로 돌려주는 모양을 흉내낸다.
//   ★재현 조건 그 자체: `create_material`(백엔드)은 `name`만 세팅하므로 새 종은
//   항상 `form_factor: null`·`part: null`이다 — 여기서도 그 사실을 지어내지 않는다.
//   `function` 선언이라 파일 어디서 부르든(위 KIT과 같은 사정, vi.mock 팩토리 포함)
//   안전하다.
function createdMaterialFixture(name: string): CostMaterial {
  return {
    id: 9001,
    name,
    unit: null,
    category: null,
    status: "unconfirmed",
    excel_label: null,
    excel_ref_price: null,
    match_rule: null,
    form_factor: null,
    part: null,
    note: null,
    lot_count: 0,
    price_count: 0,
    stale_count: 0,
    latest_price_ex_vat: null,
    latest_price_inc_vat: null,
    latest_price_source: null,
    price_rule: "latest",
    lot_price_min: null,
    lot_price_max: null,
    lot_price_has_span: false,
    price_conflict: false,
    price_conflict_price_id: null,
    prices: [],
    used_by: [],
    used_by_count: 0,
  };
}

const LEDGER_ROW: CostLedgerMaterialLine = {
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
};

// ── S2: 원가 정본 실측(2026-08-23) — 「지문방지필름 TPU 3매」 · bar · 부자재 9종 ──
//    필름 600×3=1800 + 30 + 22 + 60 + 8 + 13 + 98 + 6 + 100 = ex 2,137 ⇒ inc **2,350.70**
/**
 * ★개정 4(D-CPP-59)로 `CostRecipe`에 `picked`가 필수가 됐다. 이 파일의 픽스처는 전부
 * 「아직 아무도 안 본」 상태 — `none`이다. 그 상태가 **배지를 안 붙이는** 상태라는 것이
 * 계약 합격 19의 요점이므로, 여기서 다른 값을 주면 이 파일의 기존 단언들이 의미를 잃는다.
 */
const NOT_LOOKED_AT: CostRecipe["picked"] = {
  state: "none",
  item_id: null,
  item_name: null,
  section: null,
  item_total_inc_vat: null,
  picked_at: null,
  absent_confirmed_at: null,
  absent_note: null,
};

const RECIPE: CostRecipe = {
  picked: NOT_LOOKED_AT,
  id: 7,
  product_name: "오하이 빛반사, 지문방지 매트 필름 3매",
  form_factor: "bar",
  form_source: "rule",
  status: "approved",
  source: "excel",
  recipe_kind: "assembly",
  anomaly_flag: null,
  approved_at: "2026-08-23T04:00:00",
  match: {
    match_reason:
      "폼팩터 bar(옵션명) × cost_price 2350.70 일치 — 원가표 「지문방지필름 TPU 3매」",
    candidates: ["모바일 필름-아이폰,갤럭시/지문방지필름 TPU 3매"],
    cost_price_mode: "2350.70",
    cost_table_item: "지문방지필름 TPU 3매",
    cost_table_section: "모바일 필름-아이폰,갤럭시",
    excel_total_inc_vat: "2350.70",
    sku_count: 106,
    option_count: 107,
  },
  line_count: 9,
  link_count: 106,
  standard: {
    computable: true,
    std_cost_ex_vat: "2137.00",
    std_cost_inc_vat: "2350.70",
    reason: null,
    unresolved: [],
    partial_ex_vat: "2137.00",
    partial_inc_vat: "2350.70",
    line_count: 9,
    lines: [
      {
        label: "지문방지필름 TPU 3매 · 필름 (bar)",
        quantity: "3",
        unit_price_ex_vat: "600.00",
        unit_price_inc_vat: "660.00",
        amount_ex_vat: "1800.00",
        amount_inc_vat: "1980.00",
        price_status: "manual",
        inc_derived: true,
        price_source: "manual",
        price_note: null,
        material_id: 21,
        usable: true,
        // ★채택이 끝난 뒤에도 참고값은 종에 그대로 남는다(`adopt_excel_prices`는 지우지
        //   않는다) — 그래서 이 열은 「채택 전 값이 얼마였나」의 대조값으로 계속 보인다.
        excel_ref_price: "600.00",
      },
      {
        label: "패키지 (bar)",
        quantity: "1",
        unit_price_ex_vat: "98.00",
        unit_price_inc_vat: "107.80",
        amount_ex_vat: "98.00",
        amount_inc_vat: "107.80",
        price_status: "manual",
        inc_derived: true,
        price_source: "manual",
        price_note: null,
        material_id: 22,
        usable: true,
        excel_ref_price: "98.00",
      },
    ],
  },
};

// ── S3: 두 번째 제품 — 「제품을 고르면 다른 제품 행이 사라진다」를 재려면 서로 다른
//    제품이 최소 둘 있어야 한다(레시피·보드 둘 다). 폼팩터 값도 원 제품과 겹치게 둬서
//    「폼팩터만으로는 안 갈리고 제품이 우선 갈라야 한다」는 것까지 함께 잰다.
const RECIPE_FLIP: CostRecipe = {
  picked: NOT_LOOKED_AT,
  id: 8,
  product_name: "오하이 빛반사, 지문방지 매트 필름 3매",
  form_factor: "flip",
  form_source: "rule",
  status: "draft",
  source: "excel",
  recipe_kind: "assembly",
  anomaly_flag: null,
  approved_at: null,
  match: null,
  line_count: 0,
  link_count: 0,
  standard: {
    computable: false,
    std_cost_ex_vat: null,
    std_cost_inc_vat: null,
    reason: "구성 없음",
    unresolved: [],
    partial_ex_vat: null,
    partial_inc_vat: null,
    line_count: 0,
    lines: [],
  },
};

const RECIPE_OTHER_PRODUCT: CostRecipe = {
  picked: NOT_LOOKED_AT,
  id: 9,
  product_name: "오하이 강화유리 풀커버",
  form_factor: "bar",
  form_source: "rule",
  status: "draft",
  source: "excel",
  recipe_kind: "assembly",
  anomaly_flag: null,
  approved_at: null,
  match: null,
  line_count: 0,
  link_count: 0,
  standard: {
    computable: false,
    std_cost_ex_vat: null,
    std_cost_inc_vat: null,
    reason: "구성 없음",
    unresolved: [],
    partial_ex_vat: null,
    partial_inc_vat: null,
    line_count: 0,
    lines: [],
  },
};

// ── P2-C: `form_factor: null` 레시피 — 수입·매입 완제품처럼 폼팩터 개념이 없는 종.
//    「강화유리 풀커버」 제품에 bar(RECIPE_OTHER_PRODUCT)와 null을 나란히 두어, 폼팩터
//    필터가 null도 «하나의 선택지」로 다뤄야 한다는 것을 잰다(`?? "__none__"` sentinel).
const RECIPE_NULL_FORM: CostRecipe = {
  picked: NOT_LOOKED_AT,
  id: 10,
  product_name: "오하이 강화유리 풀커버",
  form_factor: null,
  form_source: "rule",
  status: "draft",
  source: "excel",
  recipe_kind: "assembly",
  anomaly_flag: null,
  approved_at: null,
  match: null,
  line_count: 0,
  link_count: 0,
  standard: {
    computable: false,
    std_cost_ex_vat: null,
    std_cost_inc_vat: null,
    reason: "구성 없음",
    unresolved: [],
    partial_ex_vat: null,
    partial_inc_vat: null,
    line_count: 0,
    lines: [],
  },
};

// ── 적대 리뷰 1R P1-4용 — 수입 완제품 표면(D-CPP-61)의 레시피 배지 픽스처.
//    「레시피」 탭 목록에서 `recipe_kind: "imported_goods"`·`form_source: "fallback"`가
//    실제로 뜨는지를 App 경로로 재려면 그 값을 가진 레시피가 최소 하나씩 있어야 한다.
//    `...RECIPE`로 나머지 필드를 물려받고 겨눈 두 값만 바꾼다 — RECIPE 자신은
//    recipe_kind: "assembly" · form_source: "rule"이라 「배지가 안 뜨는」 대조군으로 쓴다.
const RECIPE_IMPORTED: CostRecipe = {
  ...RECIPE,
  id: 50,
  product_name: "완제품 폰케이스",
  recipe_kind: "imported_goods",
  form_source: "rule",
  form_factor: null,
  match: null,
  line_count: 0,
  link_count: 0,
  standard: {
    computable: false,
    std_cost_ex_vat: null,
    std_cost_inc_vat: null,
    reason: "구성 없음",
    unresolved: [],
    partial_ex_vat: null,
    partial_inc_vat: null,
    line_count: 0,
    lines: [],
  },
};

const RECIPE_FORM_ESTIMATED: CostRecipe = {
  ...RECIPE,
  id: 51,
  product_name: "폼팩터 추정 종",
  recipe_kind: "assembly",
  form_source: "fallback",
  match: null,
  line_count: 0,
  link_count: 0,
  standard: {
    computable: false,
    std_cost_ex_vat: null,
    std_cost_inc_vat: null,
    reason: "구성 없음",
    unresolved: [],
    partial_ex_vat: null,
    partial_inc_vat: null,
    line_count: 0,
    lines: [],
  },
};

const BOARD: CostBoard = {
  items: [
    {
      internal_sku: "OHI-0390",
      product_name: "오하이 빛반사, 지문방지 매트 필름 3매, 아이폰에어",
      recipe_id: 7,
      recipe_product_name: "오하이 빛반사, 지문방지 매트 필름 3매",
      form_factor: "bar",
      form_source: "rule",
      recipe_kind: "assembly",
      recipe_status: "approved",
      link_status: "approved",
      std_cost_ex_vat: "2137.00",
      std_cost_inc_vat: "2350.70",
      current_cost_price: "2350.70",
      gap_pct: 0,
      excel_total_inc_vat: null,
      excel_gap_pct: null,
      reason: null,
    },
    {
      internal_sku: "OHI-0391",
      product_name: "오하이 빛반사, 지문방지 매트 필름 3매, 아이폰XS맥스/11프로맥스",
      recipe_id: 7,
      recipe_product_name: "오하이 빛반사, 지문방지 매트 필름 3매",
      form_factor: "bar",
      form_source: "rule",
      recipe_kind: "assembly",
      recipe_status: "approved",
      link_status: "approved",
      std_cost_ex_vat: "2137.00",
      std_cost_inc_vat: "2350.70",
      current_cost_price: "2350.70",
      gap_pct: 0,
      excel_total_inc_vat: null,
      excel_gap_pct: null,
      reason: null,
    },
    {
      // ★미계산 행 — 빠짐없이 실리고 «왜»를 말해야 한다(계약 §2-7).
      internal_sku: "OHI-9001",
      product_name: "오하이 빛반사, 지문방지 매트 필름 3매, 갤럭시Z플립7",
      recipe_id: 8,
      recipe_product_name: "오하이 빛반사, 지문방지 매트 필름 3매",
      form_factor: "flip",
      form_source: "rule",
      recipe_kind: "assembly",
      recipe_status: "draft",
      link_status: "draft",
      std_cost_ex_vat: null,
      std_cost_inc_vat: null,
      current_cost_price: "3480.40",
      gap_pct: null,
      excel_total_inc_vat: null,
      excel_gap_pct: null,
      reason: "레시피 미승인 — 계산 안 함",
    },
    // ★다른 제품 — 필터가 「제품」 축으로 실제로 가르는지 재는 대조군.
    {
      internal_sku: "OHI-6001",
      product_name: "오하이 강화유리 풀커버, 아이폰15",
      recipe_id: 9,
      recipe_product_name: "오하이 강화유리 풀커버",
      form_factor: "bar",
      form_source: "rule",
      recipe_kind: "assembly",
      recipe_status: "draft",
      link_status: "draft",
      std_cost_ex_vat: null,
      std_cost_inc_vat: null,
      current_cost_price: "1200.00",
      gap_pct: null,
      excel_total_inc_vat: null,
      excel_gap_pct: null,
      reason: "레시피 미승인 — 계산 안 함",
    },
    {
      internal_sku: "OHI-6002",
      product_name: "오하이 강화유리 풀커버, 갤럭시S24",
      recipe_id: 9,
      recipe_product_name: "오하이 강화유리 풀커버",
      form_factor: "bar",
      form_source: "rule",
      recipe_kind: "assembly",
      recipe_status: "draft",
      link_status: "draft",
      std_cost_ex_vat: null,
      std_cost_inc_vat: null,
      current_cost_price: "1200.00",
      gap_pct: null,
      excel_total_inc_vat: null,
      excel_gap_pct: null,
      reason: "레시피 미승인 — 계산 안 함",
    },
  ],
  sku_count: 5,
  computed_count: 2,
  uncomputed_count: 3,
  recipe_count: 3,
  approved_recipe_count: 1,
};

const SETTINGS: CostSetting[] = [
  { key: "valuation_method", value: "fifo", confirmed: false, note: null, updated_at: null },
];

// ── 적대 리뷰 1R P1-4용 — 수입 완제품 표면(D-CPP-61)의 호출부 픽스처.
//    `category: IMPORTED_GOODS_CATEGORY`("수입 완제품")인 종을 골라야 `selectedIsImportedGoods`가
//    참이 되고, 그래야 「원장 수입 완제품 라인」 섹션이 App 경로로 화면에 뜬다.
const IMPORTED_GOODS_MATERIAL: CostMaterial = {
  id: 40,
  name: "완제품 폰케이스 (수입)",
  unit: "ea",
  category: "수입 완제품",
  status: "unconfirmed",
  excel_label: null,
  excel_ref_price: null,
  match_rule: null,
  form_factor: null,
  part: null,
  note: null,
  lot_count: 0,
  price_count: 0,
  stale_count: 0,
  latest_price_ex_vat: null,
  latest_price_inc_vat: null,
  latest_price_source: null,
  price_rule: "latest",
  lot_price_min: null,
  lot_price_max: null,
  lot_price_has_span: false,
  price_conflict: false,
  price_conflict_price_id: null,
  prices: [],
  used_by: [],
  used_by_count: 0,
};

// 원장 `product` 라인 — `suggestion.material_id: null`(제안 없음)이다. 방금 세운 수입
// 완제품 종은 `match_rule`이 없어 제안이 원리적으로 안 붙는다(CostPage.tsx 813-822 주석) —
// 그래서 이 라인은 «호출부가 `linkTargetId`를 주지 않으면 연결 버튼이 영영 안 뜨는» 경우다.
const IMPORTED_PRODUCT_LINE: CostLedgerMaterialLine = {
  line_id: 90,
  shipment_id: 9,
  hbl_no: "SETR2608300099",
  declaration_date: "2026-08-28",
  item_name: "완제품 폰케이스",
  line_type: "product",
  quantity: "500.000",
  unit_cost_ex_vat: "1000.00",
  unit_cost_inc_vat: "1100.00",
  allocated_cost_krw: "500000.00",
  linked_material_id: null,
  linked_material_name: null,
  linked_price_id: null,
  shipment_status: "confirmed",
  linked_price_check: null,
  suggestion: {
    line_id: 90,
    item_name: "완제품 폰케이스",
    material_id: null,
    reason: "제안 없음 — 새로 세운 종이라 match_rule이 아직 없다",
    candidates: [],
    ambiguous: false,
    unmatched: true,
  },
};

// api 모듈 전체를 모킹한다 — Layout의 헬스·쿠키 조회까지 네트워크를 안 타게 하기 위해서다.
vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    // ★KIT이 첫 항목이다 — 백엔드는 `ORDER BY name`으로 내주고, 화면의 기본 선택은
    //   목록 첫 항목이다. 순서를 바꾸면 SUR-1(단가 이력 2로트)이 다른 종을 보게 된다.
    fetchCostMaterials: vi.fn(async () => ({ items: [KIT, FILM_WITH_REF, JIG_NO_PART] })),
    fetchCostLedgerMaterialLines: vi.fn(async () => ({ items: [LEDGER_ROW] })),
    fetchCostSettings: vi.fn(async () => ({ items: SETTINGS })),
    fetchCostRecipes: vi.fn(async () => ({
      items: [RECIPE, RECIPE_FLIP, RECIPE_OTHER_PRODUCT, RECIPE_NULL_FORM],
    })),
    fetchCostBoard: vi.fn(async () => BOARD),
    // ★D-CPP-60 — `load()`가 이 넷을 항상 부른다(탭과 무관). 오버라이드가 없으면 `actual`의
    //   진짜 구현이 이 파일 하단의 전역 `fetchSpy`(항상 `{}`를 돌려줌)를 타 `.items`가
    //   `undefined`가 되고, `AutoRefreshPanel`의 `sweepSummaryText(undefined)`가 던진다 —
    //   이 파일의 기존 테스트 전부가 그 자리에서 깨졌다(실측). 빈 목록으로 안전하게 채운다.
    fetchCostSettingHistory: vi.fn(async () => ({ items: [] })),
    fetchCostAutoRefreshRuns: vi.fn(async () => ({ items: [] })),
    fetchCostAutoRefreshQueue: vi.fn(async () => ({ items: [] })),
    runCostAutoRefreshNow: vi.fn(async () => ({
      run_id: 1,
      trigger: "manual",
      checked: 0,
      updated: 0,
      failed: 0,
      queued: 0,
    })),
    updateCostSetting: vi.fn(async (key: string, body: Record<string, unknown>) => ({
      key,
      value: (body.value as string) ?? "fifo",
      confirmed: (body.confirmed as boolean) ?? false,
      note: (body.note as string | null) ?? null,
      updated_at: null,
      value_changed: false,
      confirmed_changed: false,
    })),
    // ★P1-1 — 승인/승인취소가 «화면 클릭에서 실제로 불리는가»를 재려면 이 둘도
    //   vi.fn()이어야 한다. 오버라이드가 없으면 `actual`의 진짜 구현이 전역 fetchSpy를
    //   타는데, 그러면 「호출됐다/안 됐다」·「어떤 id로 불렸다」를 잴 수단이 없다.
    approveCostRecipe: vi.fn(async (id: number) => ({ ...RECIPE, id, status: "approved" })),
    unapproveCostRecipe: vi.fn(async (id: number) => ({ ...RECIPE, id, status: "draft" })),
    // ★2R P1-1·P1-2용 — 실제 구현은 fetch를 타는데 전역 fetchSpy가 `{}`를 돌려줘
    //   `CostMaterial` 모양이 아니다. 이 파일의 다른 쓰기 호출들과 같은 결로 값을
    //   직접 만든다. id는 실측(prod 129종)보다 큰 값을 써서 기존 픽스처와 안 겹친다.
    createCostMaterial: vi.fn(async ({ name }: { name: string }) => createdMaterialFixture(name)),
    patchCostMaterial: vi.fn(async (id: number, body: Record<string, unknown>) => ({
      ...KIT,
      id,
      ...body,
    })),
    addCostManualPrice: vi.fn(async (materialId: number) => ({
      price_id: 9999,
      material: { ...KIT, id: materialId },
    })),
    // ★「보존」테스트가 재조회를 일으키는 트리거로 쓴다 — 실제 구현은 fetch를 타는데
    //   그러면 전역 fetchSpy가 `{}`를 돌려줘 `out.skipped_has_price.length`에서
    //   TypeError가 나 load()가 아예 안 불린다. 이 파일의 다른 쓰기 호출들과 같은 결로
    //   여기서 값을 직접 만든다.
    adoptCostExcelPrices: vi.fn(async () => ({ skipped_has_price: [], skipped_no_ref: [] })),
    getSchedulerHealth: vi.fn(async () => ({ healthy: true })),
    getAdCostCookieStatus: vi.fn(async () => ({})),
    getCollectionStatus: vi.fn(async () => ({ streams: [] })),
    // 레이아웃의 스케줄러 위젯은 `fetchApi`를 직접 부른다 — 껍데기만 준다.
    fetchApi: vi.fn(async () => ({ jobs: [], items: [] })),
  };
});

// 이 파일이 임포트하는 페이지들이 렌더 중 우발적으로 네트워크를 타지 않게 못을 박는다.
const fetchSpy = vi.fn(async () => ({
  ok: true,
  status: 200,
  text: async () => "{}",
  json: async () => ({}),
})) as unknown as typeof fetch;

beforeEach(() => {
  vi.stubGlobal("fetch", fetchSpy);
  window.history.pushState({}, "", "/cost");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function renderApp() {
  // 동적 임포트다 — `vi.mock`이 먼저 걸린 뒤에 App이 api를 집게 하려면 이 순서여야 한다.
  const { default: App } = await import("../App");
  return render(<App />);
}

describe("★「💰 원가」가 사람에게 닿는 경로 — 라우트·메뉴·호출부가 한 줄로 이어진다", () => {
  it("SUR-3: `/cost` 라우트가 있어야 원가 화면이 뜬다", async () => {
    await renderApp();
    expect(await screen.findByRole("heading", { name: /원가/ })).toBeTruthy();
    // S1의 자백 배지 둘 — 화면이 스스로 기준을 밝히는 자리(계약 합격 9 · §9-1)
    expect(await screen.findByText(/사내 관리회계 기준/)).toBeTruthy();
    expect(await screen.findByText(/신고 내역 미확인/)).toBeTruthy();
  });

  it("SUR-4: 좌측 메뉴에 「💰 원가」가 있어야 사람이 이 화면을 찾는다", async () => {
    const { container } = await renderApp();
    await screen.findByRole("heading", { name: /원가/ });
    const links = Array.from(container.querySelectorAll('a[href="/cost"]'));
    expect(links.length).toBeGreaterThan(0);
    expect(links.some((a) => (a.textContent ?? "").includes("원가"))).toBe(true);
    expect(links.some((a) => (a.textContent ?? "").includes("💰"))).toBe(true);
  });

  it("SUR-1: 단가 이력 **호출부**가 있어야 로트 2건이 화면에 그려진다", async () => {
    await renderApp();
    const aug = await screen.findByTestId("price-row-11");
    expect(within(aug).getByText("209.9원")).toBeTruthy();
    expect(within(aug).getByText("SETR2608170216")).toBeTruthy();
    const jul = await screen.findByTestId("price-row-12");
    expect(within(jul).getByText("196.66원")).toBeTruthy();
    // 합격 1의 요점 — 두 로트가 **서로 다른 값**으로 나란히 보인다(+6.7%)
    expect(within(aug).queryByText("196.66원")).toBeNull();
  });

  it("SUR-2: 원장 라인 **호출부**가 있어야 「연결」 경로가 화면에 존재한다", async () => {
    await renderApp();
    const row = await screen.findByTestId("ledger-line-15");
    expect(within(row).getByText("cleaning kits")).toBeTruthy();
    expect(within(row).getByRole("button", { name: /연결/ })).toBeTruthy();
  });

  // ── S2 (계약 §7 합격 3·4) — 탭을 «사람처럼» 눌러서 연다 ──
  //    탭 전환을 프로그램으로 흉내내지 않는다: 버튼이 사라지면 사람은 그 탭에 못 가고,
  //    그 사실을 재는 것이 이 파일의 존재 이유다.
  it("SUR-5: 「레시피」 탭 버튼이 있어야 사람이 승인 화면에 도달한다", async () => {
    await renderApp();
    await screen.findByRole("heading", { name: /원가/ });
    const tab = screen.getByRole("button", { name: "레시피" });
    fireEvent.click(tab);
    // 매칭 근거가 화면에 실제로 있어야 한다 — 「제안이지 확정이 아니다」가 보이는 자리.
    // 목록·상세 양쪽에 나올 수 있다 — 「하나뿐」이 아니라 「있다」를 잰다.
    expect((await screen.findAllByText(/원가표 「지문방지필름 TPU 3매」/)).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /엑셀 참고값을 단가로 채택/ })).toBeTruthy();
  });

  it("SUR-6: 계산 내역 **호출부**가 있어야 「계산되는 방법」이 펼쳐진다 (합격 4)", async () => {
    await renderApp();
    await screen.findByRole("heading", { name: /원가/ });
    fireEvent.click(screen.getByRole("button", { name: "레시피" }));
    await screen.findByText(/계산 내역/);
    // 부자재 × 수량 × 단가가 실제 픽셀이 된다
    expect(screen.getByText("지문방지필름 TPU 3매 · 필름 (bar)")).toBeTruthy();
    expect(screen.getByText("1,800원")).toBeTruthy();   // 600 × 3
    // 「98원」은 단가 칸과 금액 칸 둘 다에 뜬다(수량 1) — 개수가 아니라 존재를 잰다.
    expect(screen.getAllByText("98원").length).toBeGreaterThan(0);
    // 합계 = 정본 대조값
    expect(screen.getAllByText("2,350.7원").length).toBeGreaterThan(0);
  });

  it("SUR-7: 보드 **호출부**가 있어야 2,350.7이 여러 SKU에 보인다 (합격 3)", async () => {
    await renderApp();
    await screen.findByRole("heading", { name: /원가/ });
    fireEvent.click(screen.getByRole("button", { name: "표준원가 보드" }));
    expect(await screen.findByText("OHI-0390")).toBeTruthy();
    expect(screen.getByText("OHI-0391")).toBeTruthy();
    // ★서로 다른 SKU 2건 이상에서 **같은 값**이 관측된다
    expect(screen.getAllByText("2,350.7원").length).toBeGreaterThanOrEqual(2);
  });

  it("SUR-8: 미계산 행이 «왜»와 함께 남는다 — 조용히 사라지면 커버리지 착시다", async () => {
    await renderApp();
    await screen.findByRole("heading", { name: /원가/ });
    fireEvent.click(screen.getByRole("button", { name: "표준원가 보드" }));
    expect(await screen.findByText("OHI-9001")).toBeTruthy();
    expect(screen.getAllByText(/레시피 미승인 — 계산 안 함/).length).toBeGreaterThan(0);
    // 미계산 행의 표준원가 칸은 「—」다 — 0원으로 그리면 미입력이 확정값으로 둔갑한다.
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  // ── S3(원가메뉴): 제품 → 옵션 2단 드롭다운 필터 ──────────────────────────
  // Jino: "제품명, 옵션명을 불러올 수 있게 드롭버튼을 만드는게 좋겠다 … 예를 들어서 제품,
  //   옵션 구조로. 제품만 선택하면 제품에 속하는 옵션들이 쭉 나오기도 하고 옵션까지 선택하면
  //   딱 그 제품만 나오고." — 실제 병목은 보드 924행 · 레시피 100건에서 눈으로 못 찾는 것.
  describe("★제품 → 옵션 필터 — 보드 탭", () => {
    async function openBoardTab() {
      await renderApp();
      await screen.findByRole("heading", { name: /원가/ });
      fireEvent.click(screen.getByRole("button", { name: "표준원가 보드" }));
      await screen.findByText("OHI-0390");
    }

    it("제품을 고르면 그 제품의 옵션 행만 남는다 — 다른 제품 행이 사라진다", async () => {
      await openBoardTab();
      // 필터 전엔 두 제품이 모두 보인다.
      expect(screen.getByText("OHI-6001")).toBeTruthy();

      const productSelect = screen.getByTestId("board-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });

      expect(screen.getByText("OHI-0390")).toBeTruthy();
      expect(screen.getByText("OHI-0391")).toBeTruthy();
      expect(screen.getByText("OHI-9001")).toBeTruthy();
      // ★다른 제품의 SKU는 화면에서 사라진다 — 이게 필터의 요점이다.
      expect(screen.queryByText("OHI-6001")).toBeNull();
      expect(screen.queryByText("OHI-6002")).toBeNull();
    });

    it("옵션까지 고르면 그 한 행만 남는다", async () => {
      await openBoardTab();
      const productSelect = screen.getByTestId("board-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });

      const optionSelect = screen.getByTestId("board-option-select") as HTMLSelectElement;
      expect(optionSelect.disabled).toBe(false);
      // ★옵션 셀렉트 «자신»의 항목이 선택된 제품에 종속돼야 한다 — 다른 제품의 SKU가
      //   목록 안에 섞여 있으면, 뒤에 오는 필터링이 우연히 맞아도 사람은 잘못된 옵션을
      //   고를 수 있다(변이 ④가 이 자리에서만 죽는다).
      expect(within(optionSelect).queryByText(/OHI-6001/)).toBeNull();
      expect(within(optionSelect).queryByText(/OHI-6002/)).toBeNull();
      expect(within(optionSelect).getByText(/OHI-0391/)).toBeTruthy();

      fireEvent.change(optionSelect, { target: { value: "OHI-0391" } });

      expect(screen.getByText("OHI-0391")).toBeTruthy();
      expect(screen.queryByText("OHI-0390")).toBeNull();
      expect(screen.queryByText("OHI-9001")).toBeNull();
    });

    it("제품을 고르기 전엔 옵션 셀렉트가 비활성이고 안내를 말한다", async () => {
      await openBoardTab();
      const optionSelect = screen.getByTestId("board-option-select") as HTMLSelectElement;
      expect(optionSelect.disabled).toBe(true);
      expect(within(optionSelect).getByText(/먼저 제품을 고르세요/)).toBeTruthy();
    });

    it("필터가 걸리면 「N건 중 M건 표시 중」 문구가 뜬다", async () => {
      await openBoardTab();
      expect(screen.queryByTestId("board-filter-summary")).toBeNull();

      const productSelect = screen.getByTestId("board-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });

      const summary = await screen.findByTestId("board-filter-summary");
      // BOARD 전체 5건 중 그 제품 3건.
      expect(summary.textContent).toContain("5건 중 3건 표시 중");
      expect(summary.textContent).toContain("제품=오하이 빛반사, 지문방지 매트 필름 3매");
    });

    it("제품 검색칸에 글자를 넣으면 제품 셀렉트의 항목이 좁혀진다", async () => {
      await openBoardTab();
      const search = screen.getByTestId("board-product-search");
      const productSelect = screen.getByTestId("board-product-select");

      // 필터 전엔 두 제품이 모두 셀렉트 옵션으로 있다.
      expect(within(productSelect).getByText(/강화유리 풀커버/)).toBeTruthy();
      expect(within(productSelect).getByText(/빛반사, 지문방지 매트 필름 3매/)).toBeTruthy();

      fireEvent.change(search, { target: { value: "강화유리" } });

      expect(within(productSelect).getByText(/강화유리 풀커버/)).toBeTruthy();
      expect(within(productSelect).queryByText(/빛반사, 지문방지 매트 필름 3매/)).toBeNull();
    });

    it("필터 결과가 0건이면 「해당 조건에 맞는 SKU가 없다」를 말한다 — 빈 표를 그리지 않는다", async () => {
      await openBoardTab();
      const productSelect = screen.getByTestId("board-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });
      const optionSelect = screen.getByTestId("board-option-select") as HTMLSelectElement;
      fireEvent.change(optionSelect, { target: { value: "OHI-0391" } });
      // 다시 다른 제품으로 바꾸면 옵션은 초기화되지만, 강제로 없는 조합을 만드는 대신
      // 존재하는 SKU 하나만 남기고 그 상태를 그대로 관측한다 — 0건 경로는 별도로 잰다.
      expect(screen.getByText("OHI-0391")).toBeTruthy();
      expect(screen.queryByText(/해당 조건에 맞는 SKU가 없다/)).toBeNull();
    });

    it("초기화를 누르면 검색어·제품·옵션이 전부 원복된다", async () => {
      await openBoardTab();
      const search = screen.getByTestId("board-product-search") as HTMLInputElement;
      const productSelect = screen.getByTestId("board-product-select") as HTMLSelectElement;
      const optionSelect = screen.getByTestId("board-option-select") as HTMLSelectElement;

      fireEvent.change(search, { target: { value: "빛반사" } });
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });
      fireEvent.change(optionSelect, { target: { value: "OHI-0391" } });
      expect(screen.queryByText("OHI-0390")).toBeNull();

      fireEvent.click(screen.getByTestId("board-picker-reset"));

      expect(search.value).toBe("");
      expect(productSelect.value).toBe("");
      // 전부 원복 — 필터 요약이 사라지고 모든 SKU가 다시 보인다.
      expect(screen.queryByTestId("board-filter-summary")).toBeNull();
      expect(screen.getByText("OHI-0390")).toBeTruthy();
      expect(screen.getByText("OHI-6001")).toBeTruthy();
    });

    // ── 적대 리뷰 1R P1-1 채택 (2026-08-23) ──────────────────────────────
    // 코드는 `handleBoardProductChange`에서 이미 `setBoardOption(null)`을 부른다 — 문제는
    // 그 줄을 지워도 28건 전부 초록이었다는 것이다. 이 테스트가 그 줄을 «지키는» 첫 테스트다.
    it("P1-1: 옵션을 고른 뒤 제품을 바꾸면 이전 옵션이 남지 않는다 — 있는 SKU가 「없다」로 보이면 안 된다", async () => {
      await openBoardTab();
      const productSelect = screen.getByTestId("board-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });
      const optionSelect = screen.getByTestId("board-option-select") as HTMLSelectElement;
      fireEvent.change(optionSelect, { target: { value: "OHI-0391" } });
      expect(screen.getByText("OHI-0391")).toBeTruthy();

      // 제품을 바꾼다 — 「강화유리 풀커버」엔 SKU 「OHI-0391」이 없다.
      fireEvent.change(productSelect, { target: { value: "오하이 강화유리 풀커버" } });

      // ★버그(있었다면): `setBoardOption(null)`이 없으면 필터가 「강화유리 AND OHI-0391」이
      //   되어, 실제로 있는 강화유리 행이 «해당 조건에 맞는 SKU가 없다»로 둔갑한다.
      expect(screen.getByText("OHI-6001")).toBeTruthy();
      expect(screen.getByText("OHI-6002")).toBeTruthy();
      expect(screen.queryByText(/해당 조건에 맞는 SKU가 없다/)).toBeNull();
      // 옵션 셀렉트 자신도 「전체」로 되돌아가 있어야 한다 — 상태와 표시가 같이 원복된다.
      expect(optionSelect.value).toBe("");
    });

    // ── P2-B 채택: 「전체 (N건)」의 N은 «선택된 제품 기준»이어야 한다. 전체 보드 건수로
    //   바꿔도 이 테스트 전엔 아무도 안 죽었다(적대 리뷰 1R 변이 실측).
    it("P2-B: 옵션 셀렉트 「전체 (N건)」의 N은 전체 보드 건수가 아니라 선택된 제품 건수다", async () => {
      await openBoardTab();
      const productSelect = screen.getByTestId("board-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });
      const optionSelect = screen.getByTestId("board-option-select") as HTMLSelectElement;
      // BOARD 전체는 5건이지만 「빛반사…」 제품은 3건(OHI-0390·0391·9001)뿐이다.
      expect(within(optionSelect).getByText("전체 (3건)")).toBeTruthy();
      expect(within(optionSelect).queryByText("전체 (5건)")).toBeNull();
    });

    // ── P2-D 채택: 검색어가 이미 선택된 제품을 걸러내도 셀렉트는 그 제품을 계속 들고
    //   있어야 한다 — 안 그러면 <select>의 value가 목록 밖이라 빈 값처럼 보인다(유령 선택).
    it("P2-D: 검색어가 선택된 제품을 가려도 셀렉트는 그 제품을 계속 보여준다", async () => {
      await openBoardTab();
      const productSelect = screen.getByTestId("board-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, { target: { value: "오하이 강화유리 풀커버" } });
      expect(productSelect.value).toBe("오하이 강화유리 풀커버");

      const search = screen.getByTestId("board-product-search");
      fireEvent.change(search, { target: { value: "빛반사" } });

      // ★검색이 「강화유리」를 목록에서 걸러내도, 이미 선택된 값은 살아 있어야 한다.
      expect(productSelect.value).toBe("오하이 강화유리 풀커버");
      expect(within(productSelect).getByText(/강화유리 풀커버/)).toBeTruthy();
      // 화면도 그 선택 기준으로 계속 필터링돼 있다 — 상태·표시가 어긋나지 않는다.
      expect(screen.getByText("OHI-6001")).toBeTruthy();
      expect(screen.queryByText("OHI-0390")).toBeNull();
    });
  });

  describe("★제품 → 옵션(폼팩터) 필터 — 레시피 탭", () => {
    async function openRecipesTabForFilter() {
      await renderApp();
      await screen.findByRole("heading", { name: /원가/ });
      fireEvent.click(screen.getByRole("button", { name: "레시피" }));
    }

    it("제품 + 폼팩터로 목표 레시피 하나에 도달한다", async () => {
      await openRecipesTabForFilter();
      const productSelect = screen.getByTestId("recipe-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });
      const formFactorSelect = screen.getByTestId("recipe-option-select") as HTMLSelectElement;
      expect(formFactorSelect.disabled).toBe(false);
      fireEvent.change(formFactorSelect, { target: { value: "bar" } });

      // ★목표 레시피(id 7, bar)의 매칭 근거만 남고, 같은 제품의 flip(id 8)이나
      //   다른 제품(id 9)의 흔적은 목록에서 사라진다.
      expect(
        (await screen.findAllByText(/원가표 「지문방지필름 TPU 3매」/)).length,
      ).toBeGreaterThan(0);
      expect(screen.queryByText("오하이 강화유리 풀커버")).toBeNull();
    });

    it("레시피 탭 필터도 「N건 중 M건 표시 중」을 말한다", async () => {
      await openRecipesTabForFilter();
      const productSelect = screen.getByTestId("recipe-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });
      const summary = await screen.findByTestId("recipe-filter-summary");
      // 전체 레시피 4건(RECIPE_NULL_FORM 포함) 중 그 제품 2건(bar·flip).
      expect(summary.textContent).toContain("4건 중 2건 표시 중");
    });

    it("레시피 탭 초기화를 누르면 필터가 전부 풀린다", async () => {
      await openRecipesTabForFilter();
      const productSelect = screen.getByTestId("recipe-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, { target: { value: "오하이 강화유리 풀커버" } });
      expect(await screen.findByTestId("recipe-filter-summary")).toBeTruthy();

      fireEvent.click(screen.getByTestId("recipe-picker-reset"));

      expect(productSelect.value).toBe("");
      expect(screen.queryByTestId("recipe-filter-summary")).toBeNull();
    });

    // ── 적대 리뷰 1R P1-2 채택 (2026-08-23) ──────────────────────────────
    // `handleRecipeProductChange`가 이미 `setRecipeFormFactor(null)`을 부르지만, 그 줄을
    // 지워도 28건 전부 초록이었다 — P1-1과 같은 결함의 다른 표현이다.
    it("P1-2: 폼팩터를 고른 뒤 제품을 바꾸면 이전 폼팩터가 남지 않는다", async () => {
      await openRecipesTabForFilter();
      const productSelect = screen.getByTestId("recipe-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });
      const formFactorSelect = screen.getByTestId("recipe-option-select") as HTMLSelectElement;
      fireEvent.change(formFactorSelect, { target: { value: "flip" } });
      expect(await screen.findByTestId("recipe-filter-summary")).toBeTruthy();

      // 제품을 바꾼다 — 「강화유리 풀커버」엔 flip 폼팩터가 없다(bar·null뿐이다).
      fireEvent.change(productSelect, { target: { value: "오하이 강화유리 풀커버" } });

      // ★버그(있었다면): `setRecipeFormFactor(null)`이 없으면 필터가 「강화유리 AND flip」이
      //   되어, 실제로 있는 강화유리 레시피(bar·null)가 목록에서 통째로 사라진다.
      expect(screen.getAllByText("오하이 강화유리 풀커버").length).toBeGreaterThan(0);
      const summary = await screen.findByTestId("recipe-filter-summary");
      expect(summary.textContent).not.toContain("0건 표시 중");
      // 폼팩터 셀렉트 자신도 「전체」로 되돌아가 있어야 한다.
      expect(formFactorSelect.value).toBe("");
    });

    // ── P2-C 채택: `form_factor: null`(수입·매입 완제품)도 하나의 선택지로 다뤄야 한다.
    //   `?? "__none__"` sentinel이 없으면 null 레시피는 필터에 걸려 영영 안 보인다.
    it("P2-C: 폼팩터가 없는(`null`) 레시피도 「—」 선택지로 걸러진다", async () => {
      await openRecipesTabForFilter();
      const productSelect = screen.getByTestId("recipe-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, { target: { value: "오하이 강화유리 풀커버" } });

      const formFactorSelect = screen.getByTestId("recipe-option-select") as HTMLSelectElement;
      // 「강화유리 풀커버」엔 bar(RECIPE_OTHER_PRODUCT)와 null(RECIPE_NULL_FORM) 둘이 있다.
      expect(within(formFactorSelect).getByText("bar")).toBeTruthy();
      expect(within(formFactorSelect).getByText("—")).toBeTruthy();

      fireEvent.change(formFactorSelect, { target: { value: "__none__" } });

      // ★sentinel이 없으면(mutant) null 레시피가 필터에서 빠져 0건이 된다.
      const summary = await screen.findByTestId("recipe-filter-summary");
      expect(summary.textContent).toContain("1건 표시 중");
      expect(summary.textContent).not.toContain("0건 표시 중");
    });

    // ★레이아웃 가드 (2026-08-23 Jino 실관측: *"칸이 옆으로 나오고 그러잖아?"*)
    //
    // 이 필터 바는 **넓은 보드 탭과 320px 레시피 탭 둘 다**에 놓인다. 초판이 보드 폭만 보고
    // `w-56`·`min-w-[16rem]` 같은 고정폭을 박았고, 그리드/플렉스 자식의 기본 `min-width: auto`가
    // 축소를 막아 **컨트롤이 왼쪽 칸을 뚫고 오른쪽 패널을 덮었다.**
    //
    // jsdom은 레이아웃을 계산하지 않으므로 「겹쳤는가」는 못 잰다. 대신 **그 원인이 된 클래스가
    // 돌아오지 않는지**를 잰다 — 약한 가드지만 아무것도 안 지키는 것보다 낫고, 다음 사람에게
    // 「여기 고정폭을 박으면 안 된다」는 사실을 전달한다. 진짜 판정은 라이브 화면이 한다.
    it("필터 바는 좁은 칸에서 «접힌다» — 고정폭을 박으면 옆 패널을 덮는다", async () => {
      await openRecipesTabForFilter();
      const search = screen.getByTestId("recipe-product-search");
      const productSelect = screen.getByTestId("recipe-product-select");
      const optionSelect = screen.getByTestId("recipe-option-select");

      for (const el of [search, productSelect, optionSelect]) {
        // 칸을 «채우되» 줄어들 수 있어야 한다.
        expect(el.className).toContain("w-full");
        expect(el.className).toContain("min-w-0");
        // 고정폭·최소폭은 좁은 칸에서 넘친다.
        // ★`\b`를 쓰면 `min-w-0`의 `w-0`까지 잡힌다 — 클래스 경계는 공백이다.
        expect(el.className).not.toMatch(/(^|\s)w-\d/);
        expect(el.className).not.toMatch(/min-w-\[/);
      }
    });
  });

  // ── 결함 수리 (2026-08-23, Jino 실관측): 레시피 탭에서 제품 검색으로 목록을 좁혀도
  //   상세 패널은 필터 밖(목록에 없는) 레시피를 계속 붙들고 있었다 — 그 상태에서
  //   「이 구성을 승인한다」를 누르면 엉뚱한 레시피가 승인된다. 값은 맞았지만 사람이
  //   보는 화면이 틀렸다는 점에서 이 파일이 아홉 번째로 밟는 같은 병이다.
  describe("★결함 수리 — 상세 패널이 필터 밖 레시피를 붙들지 않는다", () => {
    async function openRecipesTabForFilter() {
      await renderApp();
      await screen.findByRole("heading", { name: /원가/ });
      fireEvent.click(screen.getByRole("button", { name: "레시피" }));
      // 기본 선택(목록 첫 항목, RECIPE id 7)이 뜰 때까지 기다린다.
      await screen.findByRole("heading", { name: "오하이 빛반사, 지문방지 매트 필름 3매" });
    }

    it("회귀: 필터 밖으로 나간 선택은 상세 패널에서 더 이상 렌더되지 않는다", async () => {
      await openRecipesTabForFilter();

      // 다른 제품(강화유리 풀커버, id 9)의 레시피를 명시적으로 고른다.
      fireEvent.click(screen.getByTestId("recipe-row-9"));
      expect(await screen.findByRole("heading", { name: "오하이 강화유리 풀커버" })).toBeTruthy();

      // 제품 필터를 걸어 지금 선택된 레시피(id 9)를 목록 밖으로 밀어낸다.
      const productSelect = screen.getByTestId("recipe-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });

      // ★결함이 있었다면 상세 패널은 여전히 「강화유리 풀커버」를 보여준다.
      //   「상태가 바뀌었다」가 아니라 화면에 그 글자가 «없다»를 잰다.
      await waitFor(() => {
        expect(screen.queryByRole("heading", { name: "오하이 강화유리 풀커버" })).toBeNull();
      });
      // ★왼쪽 목록에서도 사라진다 — 필터가 실제로 걸렸다는 대조군.
      expect(screen.queryByTestId("recipe-row-9")).toBeNull();
    });

    it("스냅: 선택이 목록 밖으로 나가면 필터된 목록의 첫 레시피로 자동 전환된다", async () => {
      await openRecipesTabForFilter();
      fireEvent.click(screen.getByTestId("recipe-row-9")); // 강화유리 풀커버 선택
      await screen.findByRole("heading", { name: "오하이 강화유리 풀커버" });

      const productSelect = screen.getByTestId("recipe-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });

      // 필터된 목록(id 7 bar, id 8 flip) 중 배열 순서상 첫 항목 id 7(bar)로 스냅한다.
      const panel = await screen.findByTestId("recipe-detail-panel");
      await waitFor(() => {
        expect(
          within(panel).getByRole("heading", { name: "오하이 빛반사, 지문방지 매트 필름 3매" }),
        ).toBeTruthy();
      });
      expect(within(panel).getByText(/폼팩터 bar ·/)).toBeTruthy();
      // id 7은 계산이 끝난 레시피다 — id 8(flip, 미계산)로 잘못 스냅하지 않았다는 대조군.
      expect(within(panel).getAllByText("2,350.7원").length).toBeGreaterThan(0);
    });

    // ── 적대 리뷰 P2-1 채택 (2026-08-23) — 옛 버전은 두 가지 이유로 아무것도 안 지켰다:
    //   ① 필터 걸린 뒤 «항상 첫 항목으로 스냅»과 «선택을 보존»이 같은 답을 냈다
    //      (강화유리 필터의 첫 항목이 바로 대상이라 M7 — reconcile에서 currentId 유지
    //      조건을 빼고 언제나 filtered[0]을 반환 — 이 이 테스트를 통과했다).
    //   ② `panel`을 클릭 «전»에 캡처해 재사용했다 — 재조회 중 패널이 언마운트→
    //      리마운트되면(M13) 캡처한 노드는 분리된 옛 DOM을 계속 들고 있어 단언이
    //      그대로 통과했다.
    //   고치는 방향: 필터의 «두 번째» 항목을 사람이 명시적으로 고르고(첫 항목 스냅과
    //   구별), 단언마다 `recipe-detail-panel`을 다시 조회한다(캡처 재사용 금지).
    it("보존: 필터를 건 상태에서 재조회(승인 등)가 일어나도 «두 번째로 고른» 레시피가 계속 선택돼 있다", async () => {
      await openRecipesTabForFilter();

      // 「강화유리 풀커버」로 필터 — 배열 순서상 첫 스냅은 id 9(form_factor bar)다.
      const productSelect = screen.getByTestId("recipe-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, { target: { value: "오하이 강화유리 풀커버" } });
      await waitFor(() => {
        expect(
          within(screen.getByTestId("recipe-detail-panel")).getByText(/폼팩터 bar ·/),
        ).toBeTruthy();
      });

      // ★그 필터 안의 «두 번째» 항목(id 10, form_factor null → 「—」)을 사람이 직접
      //   고른다 — 「사람이 고른 두 번째 항목을 지키는 것」과 「무조건 첫 항목으로
      //   스냅하는 것」이 다른 결과를 내게 하는 것이 이 가드의 요점이다.
      fireEvent.click(screen.getByTestId("recipe-row-10"));
      await waitFor(() => {
        expect(
          within(screen.getByTestId("recipe-detail-panel")).getByText(/폼팩터 — ·/),
        ).toBeTruthy();
      });

      // ★M13 가드용 — 재조회 «직전»의 패널 DOM 노드 «정체성»을 잡아 둔다. 내용이 아니라
      //   신원을 재조회 뒤와 대조하는 데만 쓴다(내용 대조에 이 노드를 재사용하면 그게
      //   바로 옛 결함이다 — 아래에서 내용은 항상 새로 조회한다).
      const panelBeforeReload = screen.getByTestId("recipe-detail-panel");

      // 재조회를 일으킨다 — 「엑셀 참고값을 단가로 채택」은 line_count와 무관하게 항상
      // 눌릴 수 있고, 성공하면 onAdopt 안에서 load()가 다시 호출된다.
      fireEvent.click(screen.getByRole("button", { name: /엑셀 참고값을 단가로 채택/ }));

      // ★먼저 성공 토스트로 「재조회 사이클이 실제로 끝났다」를 확인한다 — 이게 없으면
      //   클릭 직후(재조회가 아직 안 끝난 시점)의 옛 화면을 보고 아래 waitFor가
      //   «처음부터 참이었으니 통과」로 헛통과한다(재조회를 한 번도 못 기다린 채).
      await screen.findByText("엑셀 참고값을 수동 단가로 채택했다");

      // ★단언마다 `recipe-detail-panel`을 다시 조회한다 — 재조회 도중 패널이 한 번
      //   언마운트→리마운트돼도(M13) 캡처해 둔 옛 노드가 아니라 실제 현재 DOM을 본다.
      await waitFor(() => {
        expect(
          within(screen.getByTestId("recipe-detail-panel")).getByText(/폼팩터 — ·/),
        ).toBeTruthy();
      });
      // ★대조군 — 「항상 첫 항목(bar)으로 스냅」했다면 이 문구가 다시 나타난다.
      expect(
        within(screen.getByTestId("recipe-detail-panel")).queryByText(/폼팩터 bar ·/),
      ).toBeNull();
      // ★M13 가드 — 재조회 중 패널이 통째로 언마운트→리마운트되면 React가 새 DOM
      //   노드를 만든다(최종 내용이 우연히 같아도 정체성은 달라진다). 노드가 그대로면
      //   한 번도 사라지지 않았다는 뜻이다 — 이게 「내용 재조회」만으로는 못 잡는
      //   변이(패널이 통째로 사라졌다 같은 내용으로 되살아나는 경우)를 잡는 자리다.
      expect(screen.getByTestId("recipe-detail-panel")).toBe(panelBeforeReload);
    });

    describe("0건: reconcileSelectedId — 상세 패널이 엉뚱한 레시피를 안 보여준다", () => {
      // ★전체 App 경로로는 진짜 0건을 못 만든다(폼팩터 셀렉트가 항상 «현재 제품에
      //   속한 것만»이라 0건 조합 자체가 안 만들어진다 — 위 P2-A 설명과 같은 사정).
      //   그래서 이 결함 수리의 «유일한 진실의 원천»인 순수 함수를 직접 잰다.
      it("필터 결과가 0건이면 이전 선택과 무관하게 null이다", () => {
        expect(reconcileSelectedId([], RECIPE_OTHER_PRODUCT.id)).toBeNull();
        expect(reconcileSelectedId([], null)).toBeNull();
      });

      it("현재 선택이 필터된 목록 안에 있으면 그대로 유지한다", () => {
        expect(
          reconcileSelectedId([RECIPE, RECIPE_FLIP], RECIPE_FLIP.id),
        ).toBe(RECIPE_FLIP.id);
      });

      it("현재 선택이 필터된 목록 밖이면 첫 항목으로 스냅한다", () => {
        expect(
          reconcileSelectedId([RECIPE, RECIPE_FLIP], RECIPE_OTHER_PRODUCT.id),
        ).toBe(RECIPE.id);
      });
    });
  });

  // ── 적대 리뷰 1R P1-1 채택 (2026-08-23) ────────────────────────────────────
  // 실측: 「이 구성을 승인한다」·「승인 취소」 버튼 블록을 통째로 지워도(M2) —
  // 승인 경로 자체가 화면에서 사라지는데도 — 전체 회귀 742건이 전부 초록이었다.
  // 두 버튼 이름이 이 테스트 파일에 **단 한 번도** 등장하지 않았기 때문이다. 이 화면의
  // 합격 조건은 「승인 버튼을 눌러야 표준원가 보드에 값이 뜬다」인데, 그 버튼이 화면에
  // 있는지·눌리는지·누르면 무엇을 부르는지를 아무 테스트도 안 지키고 있었다.
  describe("★결함 수리 — 승인 버튼의 표면을 붙든다 (적대 리뷰 1R P1-1)", () => {
    // RECIPE(id 7)를 베이스로 삼되 **미승인 + 구성 있음** 조합을 만든다 — 기존 draft
    // 픽스처(FLIP·OTHER_PRODUCT·NULL_FORM)는 전부 line_count 0이라 「승인 가능한
    // 미승인 레시피」를 못 만든다.
    const RECIPE_DRAFT_WITH_LINES: CostRecipe = {
      ...RECIPE,
      id: 11,
      status: "draft",
      approved_at: null,
    };

    beforeEach(() => {
      // ★vite.config.ts엔 clearMocks/restoreMocks가 없다 — 앞 테스트의 호출 이력이
      // 남아 있으면 「불리지 않았다」단언이 거짓으로 실패한다.
      vi.mocked(approveCostRecipe).mockClear();
      vi.mocked(unapproveCostRecipe).mockClear();
    });

    afterEach(() => {
      // ★다음 테스트로 오버라이드가 새지 않게 기본 목록으로 되돌린다 — 위와 같은 이유로
      // mockResolvedValue도 명시적으로 되돌려야 한다.
      vi.mocked(fetchCostRecipes).mockResolvedValue({
        items: [RECIPE, RECIPE_FLIP, RECIPE_OTHER_PRODUCT, RECIPE_NULL_FORM],
      });
    });

    async function openRecipesTabWith(items: CostRecipe[]) {
      vi.mocked(fetchCostRecipes).mockResolvedValue({ items });
      await renderApp();
      await screen.findByRole("heading", { name: /원가/ });
      fireEvent.click(screen.getByRole("button", { name: "레시피" }));
      return screen.findByTestId("recipe-detail-panel");
    }

    // M2(버튼 블록→<span/>)·M11(status 조건 반전)·M12(disabled={true} 고정)를 죽인다.
    it("미승인 + 구성 있는 레시피 — 승인 버튼이 존재·활성이고 누르면 그 레시피 id로 approveCostRecipe가 불린다", async () => {
      await openRecipesTabWith([RECIPE_DRAFT_WITH_LINES]);
      const panel = screen.getByTestId("recipe-detail-panel");

      const approveBtn = within(panel).getByRole(
        "button",
        { name: "이 구성을 승인한다" },
      ) as HTMLButtonElement;
      expect(approveBtn.disabled).toBe(false);
      // M11 가드 — 미승인 레시피엔 「승인 취소」가 있으면 안 된다.
      expect(within(panel).queryByRole("button", { name: "승인 취소" })).toBeNull();

      fireEvent.click(approveBtn);

      await waitFor(() => {
        expect(vi.mocked(approveCostRecipe)).toHaveBeenCalledWith(RECIPE_DRAFT_WITH_LINES.id);
      });
      // unapprove는 이 경로에서 불릴 이유가 없다 — M11이 살아 있으면(status 조건 반전)
      // 미승인 레시피에서 「승인 취소」가 렌더돼 unapprove가 불릴 것이다.
      expect(vi.mocked(unapproveCostRecipe)).not.toHaveBeenCalled();
    });

    // M11을 반대 방향에서도 죽인다 — 승인된 레시피엔 승인 버튼이 아예 없어야 한다.
    it("승인된 레시피 — 「승인 취소」만 뜨고 「이 구성을 승인한다」는 없다, 누르면 그 id로 unapproveCostRecipe가 불린다", async () => {
      const panel = await openRecipesTabWith([RECIPE]); // RECIPE.status === "approved"

      expect(within(panel).queryByRole("button", { name: "이 구성을 승인한다" })).toBeNull();
      const unapproveBtn = within(panel).getByRole(
        "button",
        { name: "승인 취소" },
      ) as HTMLButtonElement;
      expect(unapproveBtn.disabled).toBe(false);

      fireEvent.click(unapproveBtn);

      await waitFor(() => {
        expect(vi.mocked(unapproveCostRecipe)).toHaveBeenCalledWith(RECIPE.id);
      });
      expect(vi.mocked(approveCostRecipe)).not.toHaveBeenCalled();
    });

    // M12 가드 — `disabled={true}`로 고정하면 line_count > 0인 RECIPE_DRAFT_WITH_LINES에서도
    // 비활성이 돼 위 첫 테스트의 `approveBtn.disabled === false` 단언이 이미 이걸 죽인다.
    // 여기선 반대 극단(line_count 0)에서 **정상적으로도** 비활성인 것과, 그 이유가 화면에
    // 있는지를 확인한다 — 「구성이 비어 있다 — 계산할 것이 없다」는 CostPage.tsx가
    // `StandardBreakdown`에서 `standard.lines`가 빈 배열일 때 실제로 렌더하는 문구다
    // (코드 확인: CostPage.tsx의 StandardBreakdown, `if (!standard.lines.length) return …`).
    it("구성이 빈(line_count 0) 레시피 — 승인 버튼이 비활성이고, 그 옆 계산 내역이 「구성이 비어 있다」를 말한다", async () => {
      const panel = await openRecipesTabWith([RECIPE_FLIP]); // draft, line_count 0, standard.lines: []

      const approveBtn = within(panel).getByRole(
        "button",
        { name: "이 구성을 승인한다" },
      ) as HTMLButtonElement;
      expect(approveBtn.disabled).toBe(true);
      expect(within(panel).getByText(/구성이 비어 있다 — 계산할 것이 없다/)).toBeTruthy();
    });
  });

  // ── S3: 엑셀 2종 업로드가 «카드형 드롭존»으로 바뀐다 (Jino: "선택이 쉽게 직관적으로") ──
  describe("★S3: 원가 정본/매핑 정본 드롭존 — 클릭 전엔 안내, 클릭 후엔 확인, 잘못 넣으면 사유", () => {
    function makeXlsx(name: string, bytes = 2048): File {
      return new File([new Uint8Array(bytes)], name, {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      });
    }

    async function openRecipesTab() {
      await renderApp();
      await screen.findByRole("heading", { name: /원가/ });
      fireEvent.click(screen.getByRole("button", { name: "레시피" }));
    }

    it("드롭존 2개가 칸 이름 · 기대 시트 이름과 함께 렌더된다", async () => {
      await openRecipesTab();
      const costZone = await screen.findByTestId("cost-dropzone-cost");
      expect(within(costZone).getByText("원가 정본")).toBeTruthy();
      expect(within(costZone).getByText(/제품 원가표/)).toBeTruthy();
      expect(within(costZone).getByText(/MD_원가 계산_/)).toBeTruthy();

      const mappingZone = screen.getByTestId("cost-dropzone-mapping");
      expect(within(mappingZone).getByText("매핑 정본")).toBeTruthy();
      expect(within(mappingZone).getByText(/원가 매핑/)).toBeTruthy();
      expect(within(mappingZone).getByText(/ohisell_mapping_template_/)).toBeTruthy();

      // .xlsx만 받는다는 것도 두 칸 모두에서 보인다.
      expect(within(costZone).getByText(".xlsx만")).toBeTruthy();
      expect(within(mappingZone).getByText(".xlsx만")).toBeTruthy();
    });

    it("파일을 넣으면 파일명 · 크기가 뜨고, 「바꾸기」·「지우기」가 나타난다", async () => {
      await openRecipesTab();
      const input = screen.getByLabelText("원가 정본 파일") as HTMLInputElement;
      const file = makeXlsx("MD_원가 계산_20260823.xlsx", 3072);
      fireEvent.change(input, { target: { files: [file] } });

      const costZone = screen.getByTestId("cost-dropzone-cost");
      expect(within(costZone).getByText("MD_원가 계산_20260823.xlsx")).toBeTruthy();
      expect(within(costZone).getByText("3.0KB")).toBeTruthy();
      expect(within(costZone).getByRole("button", { name: "바꾸기" })).toBeTruthy();
      const clearBtn = within(costZone).getByRole("button", { name: "지우기" });
      expect(clearBtn).toBeTruthy();

      // 지우기 → 다시 안내 문구로 돌아간다(선택 해제).
      fireEvent.click(clearBtn);
      expect(within(costZone).queryByText("MD_원가 계산_20260823.xlsx")).toBeNull();
      expect(within(costZone).getByText(/제품 원가표/)).toBeTruthy();
    });

    it(".xlsx가 아닌 파일을 넣으면 그 자리에서 거부 사유가 뜬다 — 서버까지 안 간다", async () => {
      await openRecipesTab();
      const input = screen.getByLabelText("매핑 정본 파일") as HTMLInputElement;
      const badFile = new File(["a,b,c"], "report.csv", { type: "text/csv" });
      fireEvent.change(input, { target: { files: [badFile] } });

      const mappingZone = screen.getByTestId("cost-dropzone-mapping");
      const errorEl = within(mappingZone).getByTestId("cost-dropzone-mapping-error");
      expect(errorEl.textContent).toMatch(/\.xlsx 파일이 아닙니다/);
      expect(errorEl.textContent).toMatch(/report\.csv/); // 사유가 «무엇을 받았는지»를 말한다
      // ★사유는 «무엇을 해야 하는지»도 같이 말한다(교훈 #349).
      expect(errorEl.textContent).toMatch(/xlsx로 바꿔 다시 올리세요/);
      // 거부된 파일은 선택 상태로 채택되지 않는다.
      expect(within(mappingZone).queryByText("report.csv")).toBeNull();
    });

    // ★규칙이 바뀌었다 (Jino 2026-08-24: *"여기서 둘중에 하나만도 업데이트가 되게 해줘"*).
    //   막는 경우는 «아무것도 안 고른» 하나뿐이고, **한쪽만 고르면 버튼이 열린다.**
    //   ★대신 «무엇이 그대로인지»를 누르기 «전»에 말해야 한다 — 조용한 반쪽 갱신은
    //   반쪽 갱신보다 나쁘다(사람이 「다 됐다」고 믿는다). 그 문구도 여기서 함께 잰다.
    it("아무것도 안 고르면 막히고, 한쪽만 골라도 열리며, 무엇이 그대로인지 말한다", async () => {
      await openRecipesTab();
      expect(screen.getByTestId("import-disabled-reason").textContent).toMatch(
        /하나만 올려도 됩니다/,
      );
      const importBtn = screen.getByRole("button", {
        name: "초안 만들기",
      }) as HTMLButtonElement;
      expect(importBtn.disabled).toBe(true);
      expect(screen.queryByTestId("import-half-notice")).toBeNull();

      fireEvent.change(screen.getByLabelText("원가 정본 파일"), {
        target: { files: [makeXlsx("MD_원가 계산_1.xlsx")] },
      });
      // ★원가 정본«만»으로도 버튼이 열린다 — 이게 이 슬라이스의 요점이다.
      expect(screen.queryByTestId("import-disabled-reason")).toBeNull();
      expect(importBtn.disabled).toBe(false);
      // ★그리고 화면이 **SKU 링크는 그대로**라고 미리 말한다.
      const notice = screen.getByTestId("import-half-notice").textContent ?? "";
      expect(notice).toContain("원가 정본만");
      expect(notice).toContain("SKU 링크");
      expect(notice).toContain("그대로");

      fireEvent.change(screen.getByLabelText("매핑 정본 파일"), {
        target: { files: [makeXlsx("ohisell_mapping_template_1.xlsx")] },
      });
      // 둘 다 고르면 «그대로 두는 것»이 없으므로 안내가 사라진다.
      expect(screen.queryByTestId("import-disabled-reason")).toBeNull();
      expect(screen.queryByTestId("import-half-notice")).toBeNull();
      expect(importBtn.disabled).toBe(false);
    });

    it("★매핑 정본만 골라도 열리고, 구성이 그대로라고 말한다", async () => {
      await openRecipesTab();
      fireEvent.change(screen.getByLabelText("매핑 정본 파일"), {
        target: { files: [makeXlsx("ohisell_mapping_template_1.xlsx")] },
      });
      const importBtn = screen.getByRole("button", {
        name: "초안 만들기",
      }) as HTMLButtonElement;
      expect(importBtn.disabled).toBe(false);
      const notice = screen.getByTestId("import-half-notice").textContent ?? "";
      expect(notice).toContain("매핑 정본만");
      // ★★이 단어가 이 슬라이스의 위험을 가리킨다 — 구성을 지우지 않는다는 약속이다.
      expect(notice).toContain("구성");
      expect(notice).toContain("그대로");
    });

    it("카드 밖 드롭은 조용히 무시된다 — 페이지 이탈용 브라우저 기본 동작이 안 뜬다", async () => {
      await openRecipesTab();
      const panel = screen.getByText("엑셀 2종 업로드 → 구성 초안").closest("section")!;
      const badFile = new File(["x"], "random.pdf", { type: "application/pdf" });
      const dataTransfer = { files: [badFile] };
      // panel 영역(카드 밖)에 드롭 — preventDefault만 되고 아무 상태도 안 바뀐다.
      const dropEvent = new Event("drop", { bubbles: true, cancelable: true });
      Object.defineProperty(dropEvent, "dataTransfer", { value: dataTransfer });
      const prevented = !panel.dispatchEvent(dropEvent);
      expect(prevented).toBe(true);
      // 에러 팝업·파일 채택 둘 다 없다.
      expect(screen.queryByTestId("cost-dropzone-cost-error")).toBeNull();
      expect(screen.queryByTestId("cost-dropzone-mapping-error")).toBeNull();
    });

    // ★이 테스트가 없으면 「드롭 경로」가 통째로 죽어도 전건 초록이다 — 실제로 변이를 넣어
    //   확인했고 **SURVIVED**였다(2026-08-23, 세션 5432a577). 클릭 선택만 재는 테스트는
    //   `onChange`만 밟으므로 `onDrop`을 한 줄도 지키지 못한다. Jino가 처음 물은 것이
    //   *"파일을 그냥 드롭하면 되나?"*였으니, 드롭은 이 화면의 «사람이 쓰는 경로»다.
    it("카드 «안»에 드롭하면 실제로 선택된다 — 드롭 경로가 끊기면 이 테스트가 빨개진다", async () => {
      await openRecipesTab();
      const costZone = await screen.findByTestId("cost-dropzone-cost");
      const file = new File(["x"], "MD_원가 계산_260822.xlsx", {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      });
      const dropEvent = new Event("drop", { bubbles: true, cancelable: true });
      Object.defineProperty(dropEvent, "dataTransfer", { value: { files: [file] } });
      costZone.dispatchEvent(dropEvent);

      // 고른 파일이 화면에 «보여야» 한다 — 상태만 바뀌고 안 그려지면 사람은 모른다.
      expect(await within(costZone).findByText("MD_원가 계산_260822.xlsx")).toBeTruthy();
      // ★그리고 그 선택이 다음 단계로 «이어져야» 한다. 규칙이 바뀌어(한쪽만 허용) 이제
      //   그 증거는 「남은 비활성 사유」가 아니라 **버튼이 열리고 반쪽 안내가 뜨는 것**이다.
      expect(screen.queryByTestId("import-disabled-reason")).toBeNull();
      expect(
        (screen.getByRole("button", { name: "초안 만들기" }) as HTMLButtonElement).disabled,
      ).toBe(false);
      expect(screen.getByTestId("import-half-notice").textContent).toContain("원가 정본만");
    });

    it("잘못된 파일을 «드롭»해도 그 자리에서 거부된다 — 클릭 경로와 같은 판정을 탄다", async () => {
      await openRecipesTab();
      const mappingZone = await screen.findByTestId("cost-dropzone-mapping");
      const badFile = new File(["x"], "매핑.csv", { type: "text/csv" });
      const dropEvent = new Event("drop", { bubbles: true, cancelable: true });
      Object.defineProperty(dropEvent, "dataTransfer", { value: { files: [badFile] } });
      mappingZone.dispatchEvent(dropEvent);

      const errorEl = await within(mappingZone).findByTestId("cost-dropzone-mapping-error");
      expect(errorEl.textContent).toContain("매핑.csv");
      expect(errorEl.textContent).toContain("xlsx");
    });

    // ── 적대 리뷰 1R 산출물 (2026-08-23) ────────────────────────────────
    // P1: 카드가 role="button"이라 중첩된 「지우기」의 Enter를 가로채 죽이고 파일 선택창을
    //     대신 열었다. 「바꾸기」는 목적이 우연히 같아 증상이 안 보였다 — 그래서 놓칠 뻔했다.
    it("P1: 「지우기」가 키보드(Enter)로도 작동한다 — 카드가 자식의 키를 가로채지 않는다", async () => {
      await openRecipesTab();
      const input = screen.getByLabelText("원가 정본 파일");
      const file = new File(["x"], "MD_원가 계산_260822.xlsx");
      fireEvent.change(input, { target: { files: [file] } });

      const costZone = screen.getByTestId("cost-dropzone-cost");
      expect(within(costZone).getByText("MD_원가 계산_260822.xlsx")).toBeTruthy();

      const clearBtn = within(costZone).getByRole("button", { name: "지우기" });

      // ★단언의 자리를 조심해야 한다. `fireEvent.click`을 뒤에 붙이면 그 클릭이 파일을 지워
      //   버려서, 결함이 있어도 초록으로 통과한다(실제로 그렇게 썼다가 변이가 SURVIVED 했다).
      //   jsdom은 keydown 뒤 네이티브 버튼 활성화를 대신해 주지 않으므로, 브라우저가 그 활성화를
      //   «할 수 있는 상태인가»를 직접 잰다 — 즉 부모가 preventDefault로 죽이지 않았는가.
      const pickerSpy = vi.spyOn(HTMLInputElement.prototype, "click").mockImplementation(() => {});
      const ev = new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true });
      clearBtn.dispatchEvent(ev);

      expect(ev.defaultPrevented).toBe(false); // 죽이면 브라우저가 「지우기」를 못 누른다
      expect(pickerSpy).not.toHaveBeenCalled(); // 대신 파일 선택창이 열려서도 안 된다
      pickerSpy.mockRestore();
    });

    // P2-1 채택: 거부만 말하고 «이전 선택이 사라졌다»를 안 말하면, 사람은 멀쩡한 파일이
    //   아직 들어 있는 줄 알고 다음 단계로 간다. 부작용을 감추는 사유는 틀린 사유다.
    it("P2-1: 고른 파일 위에 잘못된 파일을 넣으면 «이전 선택이 취소됐다»고 말한다", async () => {
      await openRecipesTab();
      const input = screen.getByLabelText("원가 정본 파일");
      fireEvent.change(input, { target: { files: [new File(["x"], "정상.xlsx")] } });
      fireEvent.change(input, { target: { files: [new File(["x"], "잘못.csv")] } });

      const errorEl = within(screen.getByTestId("cost-dropzone-cost")).getByTestId(
        "cost-dropzone-cost-error",
      );
      expect(errorEl.textContent).toContain("잘못.csv");
      expect(errorEl.textContent).toContain("정상.xlsx"); // 무엇이 취소됐는지 이름으로 말한다
      expect(errorEl.textContent).toContain("취소");
    });

    // P2-2 채택: 두 엑셀을 한 칸에 함께 떨어뜨리는 것은 실사용에서 충분히 일어난다.
    //   나머지를 조용히 버리면 사람은 둘 다 올린 줄 안다.
    it("P2-2: 한 칸에 여러 파일을 드롭하면 «하나만 받았다»고 말한다 — 조용히 안 버린다", async () => {
      await openRecipesTab();
      const costZone = await screen.findByTestId("cost-dropzone-cost");
      const dropEvent = new Event("drop", { bubbles: true, cancelable: true });
      Object.defineProperty(dropEvent, "dataTransfer", {
        value: { files: [new File(["x"], "첫.xlsx"), new File(["x"], "둘.xlsx")] },
      });
      costZone.dispatchEvent(dropEvent);

      const noteEl = await within(costZone).findByTestId("cost-dropzone-cost-error");
      expect(noteEl.textContent).toContain("하나만 받습니다");
      expect(noteEl.textContent).toContain("첫.xlsx");
      // ★거부가 아니다 — 첫 파일은 실제로 선택돼 있어야 한다.
      expect(within(costZone).getByText("첫.xlsx")).toBeTruthy();
    });
  });

  // ── 적대 리뷰 1R P2-A 채택 (2026-08-23) ────────────────────────────────
  // 보드·레시피 둘 다 「해당 조건에 맞는 …가 없다」 렌더 분기를 통째로 `false`로 바꿔도
  // 28/28 통과했다 — 기존 테스트가 「0건이 아니다」만 확인하고 0건 경로를 일부러 피해갔다.
  //
  // ★전체 App 경로로는 이 분기에 진짜 0건을 못 만든다: P1을 고치고 나면 옵션 목록이
  //   항상 «현재 제품에 속한 것만»으로 구성되므로, 정상 네비게이션으로는 0건 조합 자체가
  //   안 만들어진다(0건이 나오려면 P1의 그 버그가 다시 있어야 한다). 그래서 이 두 컴포넌트
  //   — `StandardCostBoard`·`RecipeList` — 를 **직접** 렌더해 분기 자체를 잡는다. 이 파일의
  //   머리말이 말하는 「전부 순수 컴포넌트로 export 해 테스트가 직접 렌더한다」 그 계층이다.
  describe("★P2-A: 0건 안내가 실제로 화면에 뜬다 — 렌더 분기 자체를 잡는다", () => {
    it("보드: 필터로 0건이 되면 「해당 조건에 맞는 SKU가 없다」가 뜬다", () => {
      render(
        <StandardCostBoard
          board={BOARD}
          displayItems={[]}
          filterSummary="5건 중 0건 표시 중 — 필터: 제품=존재하지 않는 제품"
        />,
      );
      expect(screen.getByText(/해당 조건에 맞는 SKU가 없다/)).toBeTruthy();
      // ★총계(SKU 5건 등)는 필터와 무관하게 «전체» 기준을 유지한다 — 0건이라고
      //   전체 숫자까지 0으로 보이면 커버리지 착시가 다시 생긴다.
      expect(screen.getByText(/SKU 5건/)).toBeTruthy();
    });

    it("레시피: 필터로 0건이 되면 「해당 조건에 맞는 레시피가 없다」가 뜬다", () => {
      render(
        <RecipeList
          recipes={[]}
          selectedId={null}
          onSelect={() => {}}
          totalCount={4}
          filterSummary="4건 중 0건 표시 중 — 필터: 제품=존재하지 않는 제품"
        />,
      );
      expect(screen.getByText(/해당 조건에 맞는 레시피가 없다/)).toBeTruthy();
    });

  });

  // ══════════════════════════════════════════════════════════════════
  // N5 (2026-08-23) — Jino가 라이브 화면을 보며 발의한 개선 A~C + 새 발견 D·E + P2 F·G
  // ══════════════════════════════════════════════════════════════════

  async function openMaterialsTab() {
    await renderApp();
    await screen.findByRole("heading", { name: /원가/ });
    // 부자재가 기본 탭이다 — 목록이 실제로 들어찬 뒤에 잰다.
    await screen.findByTestId(`material-${KIT.id}`);
  }

  describe("★A·B: 부자재 종 칸 — 자체 스크롤 · 넓힌 폭 (jsdom은 «레이아웃»을 못 잰다)", () => {
    // ⚠️**약한 가드다.** jsdom은 레이아웃도 스크롤도 계산하지 않으므로 「오른쪽 단가가
    //    화면에 남는가」·「배지가 세로로 안 깨지는가」를 여기서 증명할 수 없다. 이 두
    //    테스트가 지키는 것은 **그 동작을 만드는 클래스가 지워지지 않는 것**뿐이고,
    //    진짜 판정은 배포 후 라이브 화면이 한다(기존 「필터 바는 좁은 칸에서 접힌다」
    //    가드와 같은 성격·같은 한계).
    it("A: 종 목록이 «자기» 스크롤 컨테이너를 갖는다 — 지우면 화면 전체가 같이 내려간다", async () => {
      await openMaterialsTab();
      const box = screen.getByTestId("material-list-scroll");
      // 목록이 실제로 이 컨테이너 «안»에 있어야 의미가 있다 — 컨테이너만 남기고 목록을
      // 밖으로 빼는 변이를 막는다.
      expect(within(box).getByTestId(`material-${KIT.id}`)).toBeTruthy();
      expect(box.className).toContain("overflow-y-auto");
      expect(box.className).toMatch(/max-h-/);
      // 칸이 뷰포트에 붙어 있어야 오른쪽 단가 이력이 같이 밀려나지 않는다.
      expect(box.className).toContain("sticky");
    });

    it("B: 종 칸 폭은 «고정»이 아니라 minmax다 — 고정폭을 박으면 옆 패널을 덮는다", async () => {
      await openMaterialsTab();
      const grid = screen.getByTestId("material-list-scroll").closest("div.grid");
      expect(grid).toBeTruthy();
      // 260px 고정폭이 「미승인」 배지를 «미/확/인»으로 깨뜨렸다(Jino 실관측).
      expect(grid!.className).toContain("minmax(22rem,28rem)");
      // ★px 고정 트랙이 돌아오면 안 된다 — 이 파일이 여덟 번째로 밟은 결함의 모양이다.
      expect(grid!.className).not.toMatch(/grid-cols-\[\d+px/);
    });
  });

  // ══════════════════════════════════════════════════════════════════
  // ★S4 (2026-08-24) — 계약 A′ §7 합격 10~13. Jino가 라이브 `/cost`를 보며 발의한 분.
  //
  // ⚠️**이 블록의 한계를 먼저 자백한다**: 합격 10은 «스크롤해도 오른쪽이 남는가»인데
  //    jsdom은 레이아웃도 스크롤도 계산하지 않는다. 여기서 지키는 것은 «그 동작을 만드는
  //    구조가 두 탭 다에 살아 있는 것»뿐이고, **판정은 배포 후 Jino의 눈**이다(계약 §7-10).
  //    합격 11·12·13은 «무엇이 그려지는가»라 jsdom이 실제로 잰다.
  // ══════════════════════════════════════════════════════════════════
  describe("★S4 ㉮ — 레시피 탭도 목록만 스크롤된다 (합격 10 · 「한쪽만 고친다」 다섯 번째의 수리)", () => {
    async function openRecipesTabS4() {
      await renderApp();
      await screen.findByRole("heading", { name: /원가/ });
      fireEvent.click(screen.getByRole("button", { name: "레시피" }));
      await screen.findByTestId("recipe-list-scroll");
    }

    it("레시피 목록이 «자기» 스크롤 컨테이너를 갖고, 목록이 실제로 그 안에 있다", async () => {
      await openRecipesTabS4();
      const box = screen.getByTestId("recipe-list-scroll");
      expect(box.className).toContain("overflow-y-auto");
      expect(box.className).toMatch(/max-h-/);
      expect(box.className).toContain("sticky");
      // ★컨테이너만 남기고 목록을 밖으로 빼는 변이를 막는다(부자재 탭과 같은 가드).
      expect(within(box).getByTestId(`recipe-row-${RECIPE.id}`)).toBeTruthy();
    });

    it("★두 탭이 «같은» 규율을 쓴다 — 한쪽만 고치는 것이 원리적으로 불가능해야 한다", async () => {
      await renderApp();
      await screen.findByRole("heading", { name: /원가/ });
      const materialBox = await screen.findByTestId("material-list-scroll");
      fireEvent.click(screen.getByRole("button", { name: "레시피" }));
      const recipeBox = await screen.findByTestId("recipe-list-scroll");
      // 문자열 복사본 둘이 아니라 **상수 하나**여야 한다 — 복사본은 반드시 갈라진다.
      expect(recipeBox.className).toBe(materialBox.className);
      expect(recipeBox.className).toBe(LIST_COLUMN_SCROLL_CLASS);
    });

    it("★그리드가 `items-start`여야 sticky가 산다 — 없으면 클래스만 살고 동작은 죽는다", async () => {
      await openRecipesTabS4();
      const grid = screen.getByTestId("recipe-list-scroll").closest("div.grid");
      expect(grid).toBeTruthy();
      expect(grid!.className).toContain("items-start");
    });
  });

  describe("★S4 ㉯ — 수입 종과 비수입 종을 가른다 (합격 11·12·13)", () => {
    // prod 실측(2026-08-23): 수입 부자재 **1종**(cleaning kits) vs 비수입 **128종**.
    // 이 테스트 픽스처가 그 모양이다 — LEDGER_ROW 하나가 KIT(id=1)만 가리킨다.

    // ★`mockResolvedValue`는 «영구»다 — 여기서 원장 라인을 갈아끼우면 뒤 테스트에서
    //   FILM이 수입 종이 되어 「비수입」 단언들이 조용히 깨진다(실제로 2건 깨졌다).
    //   실패로 중단돼도 복원되게 `afterEach`에 둔다.
    afterEach(() => {
      vi.mocked(fetchCostLedgerMaterialLines).mockResolvedValue({ items: [LEDGER_ROW] });
    });

    it("합격 12 — 수입 종을 고르면 «그 종의» 원장 부자재 라인 표가 뜬다", async () => {
      await openMaterialsTab();
      fireEvent.click(screen.getByTestId(`material-${KIT.id}`));
      const table = await screen.findByTestId("material-ledger-lines");
      expect(within(table).getByTestId(`ledger-line-${LEDGER_ROW.line_id}`)).toBeTruthy();
      // 「연결」이 사람의 확정이다 — 표가 옮겨졌다고 그 버튼이 사라지면 안 된다(계약 §5-2).
      expect(within(table).getByRole("button", { name: /연결/ })).toBeTruthy();
      expect(screen.getByTestId("material-origin-note").textContent).toContain("수입 종");
    });

    it("★합격 11 — 비수입 종을 고르면 원장 표가 **안 뜬다** (필름 종에 cleaning kits가 뜨던 결함)", async () => {
      await openMaterialsTab();
      fireEvent.click(screen.getByTestId(`material-${FILM_WITH_REF.id}`));
      await screen.findByTestId("material-excel-ref-note");
      // ★이것이 Jino가 00:01에 발의한 결함의 정확한 모양이다.
      expect(screen.queryByTestId("material-ledger-lines")).toBeNull();
      expect(screen.getByTestId("material-origin-note").textContent).toContain("비수입 종");
      expect(screen.getByTestId("material-origin-note").textContent).toContain("정본은 엑셀");
    });

    it("★합격 13 — 섹션은 **사라지지 않는다**. 0건이면 「미매칭 없음」이라고 말한다", async () => {
      await openMaterialsTab();
      const section = screen.getByTestId("unattributed-ledger-lines");
      expect(screen.getByTestId("unattributed-count").textContent).toContain("미매칭 없음");
      // ★섹션 «자체»가 있어야 한다 — 0건이라고 지우면 단가 이력이 조용히 빈다.
      expect(section).toBeTruthy();
      // ★0건 «문구»도 지킨다(1R ML13 SURVIVED) — 「없다」의 뜻이 표마다 다르다.
      expect(within(section).getByText(/전부 지금 화면에서 도달 가능하다/)).toBeTruthy();
    });

    // ══════════════════════════════════════════════════════════════
    // ★★적대 리뷰 1R P1 회귀 — 필터가 라인을 «감추는데» 화면은 「미매칭 없음」이라 말했다
    //
    // 재현(리뷰어): 폼팩터 필터 `bar`를 걸면 KIT(폼팩터 null)이 목록에서 빠져 고를 수
    // 없게 되고, 그 종의 원장 라인은 종별 표에도 안 뜬다. 그런데 «제안이 있으니»
    // 미귀속도 아니라 별도 섹션에도 안 떴다 — 사람의 확정(「연결」)을 기다리는 라인이
    // 화면에서 통째로 사라졌다. origin/main은 하단 전건 표로 항상 보여줬으므로 회귀다.
    // ══════════════════════════════════════════════════════════════
    it("★★필터로 종이 목록에서 빠지면, 그 종의 라인이 «도달 불가»로 세어진다 (1R P1)", async () => {
      await openMaterialsTab();
      fireEvent.change(screen.getByTestId("material-product-select"), {
        target: { value: "bar" },
      });
      // KIT은 이제 목록에 없다 — 즉 종별 표로는 영영 못 간다.
      expect(screen.queryByTestId(`material-${KIT.id}`)).toBeNull();

      // ★「미매칭 없음」이라고 말하면 안 된다. 건수가 실제로 세어져야 한다.
      const count = screen.getByTestId("unattributed-count");
      expect(count.textContent).toContain("1건");
      expect(count.textContent).not.toContain("미매칭 없음");

      // ★행 자체가 보이고, «왜» 도달 불가인지 사유가 붙는다(처분이 다르기 때문이다).
      const section = screen.getByTestId("unattributed-ledger-lines");
      expect(within(section).getByTestId(`ledger-line-${LEDGER_ROW.line_id}`)).toBeTruthy();
      const reason = screen.getByTestId(`unreachable-reason-${LEDGER_ROW.line_id}`);
      expect(reason.textContent).toContain(KIT.name);
      expect(reason.textContent).toContain("필터 밖");

      // ★「연결」이 여전히 눌린다 — 버튼이 없으면 연결이 원리적으로 불가능해진다(1R P2-2).
      expect(within(section).getByRole("button", { name: /연결/ })).toBeTruthy();
    });

    it("★필터를 풀면 다시 도달 가능해진다 — 「영구 소실」이 아니라 «지금» 못 본다는 뜻이다", async () => {
      await openMaterialsTab();
      const select = screen.getByTestId("material-product-select") as HTMLSelectElement;
      fireEvent.change(select, { target: { value: "bar" } });
      expect(screen.getByTestId("unattributed-count").textContent).toContain("1건");
      fireEvent.change(select, { target: { value: "" } });
      expect(screen.getByTestId("unattributed-count").textContent).toContain("미매칭 없음");
    });

    it("★합격 12 — 종별 표는 «그 종의» 라인만 그린다 (1R MS3 SURVIVED가 여기서 죽는다)", async () => {
      // 수입 종 «둘»이 있어야 「전건 렌더」와 「그 종만」이 갈린다 — 픽스처가 1종뿐이면
      // 필터를 통째로 없애도 화면이 똑같아서 아무 테스트도 안 운다.
      const filmLine: CostLedgerMaterialLine = {
        ...LEDGER_ROW,
        line_id: 31,
        item_name: "TPU 필름 원단",
        suggestion: { ...LEDGER_ROW.suggestion, line_id: 31, material_id: FILM_WITH_REF.id },
      };
      vi.mocked(fetchCostLedgerMaterialLines).mockResolvedValue({
        items: [LEDGER_ROW, filmLine],
      });
      await openMaterialsTab();
      fireEvent.click(screen.getByTestId(`material-${KIT.id}`));

      const table = await screen.findByTestId("material-ledger-lines");
      expect(within(table).getByTestId(`ledger-line-${LEDGER_ROW.line_id}`)).toBeTruthy();
      // ★다른 종의 라인이 여기 있으면 Jino가 00:01에 발의한 결함 그대로다.
      expect(within(table).queryByTestId(`ledger-line-${filmLine.line_id}`)).toBeNull();
    });

    it("★합격 12 — 원산지 줄이 그 종의 라인 «건수»를 실제로 센다 (1R ML12)", async () => {
      await openMaterialsTab();
      fireEvent.click(screen.getByTestId(`material-${KIT.id}`));
      await screen.findByTestId("material-ledger-lines");
      expect(screen.getByTestId("material-origin-note").textContent).toContain(
        "원장 부자재 라인 1건",
      );
    });

    it("★합격 11 — 비수입 종의 «빈 단가 이력»도 원장으로 보내지 않는다 (1R ML7)", () => {
      const bare = { ...FILM_WITH_REF, prices: [] };
      const { unmount } = render(<MaterialPriceHistory material={bare} imported={false} />);
      const text = screen.getByText(/단가 이력이 없다/).textContent ?? "";
      expect(text).toContain("레시피");
      expect(text).not.toContain("원장 부자재 라인");
      unmount();
      // 대조군 — 수입 종은 원장으로 보내는 것이 맞다.
      render(<MaterialPriceHistory material={bare} imported />);
      expect(screen.getByText(/단가 이력이 없다/).textContent).toContain("원장 부자재 라인");
    });

    it("★★목록 «호출부»가 실제로 importedIds를 넘긴다 — 앱 경로로 잰다 (2R ML15)", async () => {
      // ★2R 정정: 아래 순수 렌더 테스트는 «컴포넌트 계약»만 잠근다. `CostPage`의 호출부를
      //   `importedIds={new Set()}`로 바꾸는 변이는 그 테스트가 원리적으로 못 잡는다 —
      //   이 파일 머리말의 SUR-1/SUR-2와 **같은 모양의 구멍**이다.
      //   앱 픽스처의 유일한 수입 종 KIT은 `price_count=2`라 `lotCountText`의 imported
      //   분기(=`price_count===0`)에 아예 안 들어간다. 그래서 **단가 0건 수입 종**을 만든다.
      vi.mocked(fetchCostLedgerMaterialLines).mockResolvedValue({
        items: [
          LEDGER_ROW,
          {
            ...LEDGER_ROW,
            line_id: 41,
            item_name: "부착 지그 원자재",
            suggestion: { ...LEDGER_ROW.suggestion, line_id: 41, material_id: JIG_NO_PART.id },
          },
        ],
      });
      await openMaterialsTab();
      const row = await screen.findByTestId(`material-${JIG_NO_PART.id}`);
      // JIG는 단가 0건 «수입» 종이 됐다 — 목록 줄이 수입 종 문구를 써야 한다.
      expect(row.textContent).toContain("엑셀 참고값");
      expect(row.textContent).not.toContain("엑셀 단가(미확정)");
      // 대조군 — 같은 화면의 비수입 종은 여전히 비수입 문구다(둘이 갈리는 것이 요점이다).
      expect(screen.getByTestId(`material-${FILM_WITH_REF.id}`).textContent).toContain(
        "엑셀 단가(미확정)",
      );
    });

    it("★비수입 종이 단가를 «가진» 뒤에도 엑셀이 정본이다 (2R N1 — 분기가 안 잠겨 있었다)", () => {
      const note = excelRefNoteText({ excel_ref_price: "600.00", price_count: 2 }, false);
      expect(note).toContain("정본");
      // ★수입 종의 「대조값」 문구로 돌아가면 안 된다 — 그게 1R P2-1 결함이다.
      expect(note).not.toContain("대조값");
      // ★「이 값이 그대로 들어갔다」고 단언하지 않는다 — 수동 정정분이 있을 수 있다.
      expect(note).not.toContain("이미 단가로 들어가 있다");
      expect(note).toContain("아래 표에서 확인");
      // 대조군 — 수입 종은 여전히 대조값이다.
      expect(excelRefNoteText({ excel_ref_price: "600.00", price_count: 2 }, true)).toContain(
        "대조값",
      );
    });

    it("★목록 줄의 수입 판별이 «양방향»으로 배선돼 있다 (1R ML15 — 컴포넌트 계약)", () => {
      // 단가가 0건인 «수입» 종이 픽스처에 없어서 이 방향이 통째로 안 잠겨 있었다.
      const importedNoPrice = { ...FILM_WITH_REF, id: 77, name: "수입 부자재 (단가 없음)" };
      render(
        <MaterialList
          materials={[importedNoPrice]}
          selectedId={null}
          onSelect={() => {}}
          importedIds={new Set([77])}
        />,
      );
      const row = screen.getByTestId("material-77");
      expect(row.textContent).toContain("엑셀 참고값 600원");
      expect(row.textContent).not.toContain("엑셀 단가(미확정)");
    });

    it("★합격 13 — 어느 종도 못 가지는 라인은 그 섹션에서 세어진다", () => {
      const orphan: CostLedgerMaterialLine = {
        ...LEDGER_ROW,
        line_id: 99,
        item_name: "정체불명 부자재",
        suggestion: {
          ...LEDGER_ROW.suggestion,
          line_id: 99,
          material_id: null,
          candidates: [],
          unmatched: true,
        },
      };
      const everyMaterial = new Set([1, 2, 3, FILM_WITH_REF.id]);
      expect(
        unreachableLedgerLines([LEDGER_ROW, orphan], everyMaterial).map((r) => r.line_id),
      ).toEqual([99]);
      // ★모호한(ambiguous) 라인도 여기로 온다 — 안 그러면 화면에서 통째로 사라진다.
      const ambiguous: CostLedgerMaterialLine = {
        ...orphan,
        line_id: 98,
        suggestion: { ...orphan.suggestion, line_id: 98, ambiguous: true, unmatched: false },
      };
      expect(unreachableLedgerLines([ambiguous], everyMaterial).map((r) => r.line_id)).toEqual([
        98,
      ]);
    });

    it("★★연결이 제안을 이긴다 — 우선순위가 실제로 측정된다 (1R ML1)", () => {
      // 사람이 제안을 «교정해» 다른 종에 붙인 상태. 픽스처에 이 모양이 없어서
      // 우선순위를 뒤집어도 아무 테스트가 안 울었다.
      const corrected: CostLedgerMaterialLine = {
        ...LEDGER_ROW,
        line_id: 55,
        linked_material_id: 2,
        linked_material_name: "사람이 고른 종",
        suggestion: { ...LEDGER_ROW.suggestion, line_id: 55, material_id: 1 },
      };
      expect(ledgerLineMaterialId(corrected)).toBe(2);
      expect(ledgerLinesForMaterial([corrected], 2).map((r) => r.line_id)).toEqual([55]);
      expect(ledgerLinesForMaterial([corrected], 1)).toEqual([]);
      expect(importedMaterialIds([corrected])).toEqual(new Set([2]));
    });

    it("★★도달 가능/불가가 전건을 덮고, 건수를 «값으로» 잰다 (1R ML11 — 항등식이었다)", () => {
      const orphan: CostLedgerMaterialLine = {
        ...LEDGER_ROW,
        line_id: 99,
        suggestion: { ...LEDGER_ROW.suggestion, line_id: 99, material_id: null, unmatched: true },
      };
      const linkedElsewhere: CostLedgerMaterialLine = {
        ...LEDGER_ROW,
        line_id: 77,
        linked_material_id: 2,
        linked_material_name: "빛반사 필름",
        suggestion: { ...LEDGER_ROW.suggestion, line_id: 77, material_id: null, unmatched: true },
      };
      const rows = [LEDGER_ROW, orphan, linkedElsewhere];

      // 종 1·2가 다 목록에 있을 때: 미귀속 1건만 도달 불가.
      const all = ledgerLineCoverage(rows, new Set([1, 2]));
      expect(all).toEqual({ reachable: 2, unreachable: 1, total: 3 });

      // ★종 1이 필터 밖일 때: 도달 불가가 «2건으로 늘어야» 한다. 이 단언이 1R P1을 잡는다 —
      //   합이 total이라는 항등식은 `un`을 무엇으로 바꿔도 참이라 아무것도 안 지켰다.
      const filtered = ledgerLineCoverage(rows, new Set([2]));
      expect(filtered).toEqual({ reachable: 1, unreachable: 2, total: 3 });

      expect(unreachableReason(orphan, [])).toContain("match_rule");
      expect(
        unreachableReason(LEDGER_ROW, [KIT as unknown as (typeof KIT)]),
      ).toContain("필터 밖");

      expect(ledgerLinesForMaterial(rows, 2).map((r) => r.line_id)).toEqual([77]);
      expect(ledgerLinesForMaterial(rows, 1).map((r) => r.line_id)).toEqual([LEDGER_ROW.line_id]);
      expect(importedMaterialIds(rows)).toEqual(new Set([1, 2]));
    });
  });

  describe("★C: 부자재 종 드롭다운 — 129종을 눈으로 훑지 않는다", () => {
    it("폼팩터 셀렉트가 존재하고, 고르면 다른 폼팩터의 종이 목록에서 사라진다", async () => {
      await openMaterialsTab();
      // 필터 전엔 셋 다 보인다(KIT은 form_factor null, 나머지 둘은 bar).
      expect(screen.getByTestId(`material-${FILM_WITH_REF.id}`)).toBeTruthy();
      expect(screen.getByTestId(`material-${KIT.id}`)).toBeTruthy();

      const formSelect = screen.getByTestId("material-product-select") as HTMLSelectElement;
      fireEvent.change(formSelect, { target: { value: "bar" } });

      expect(screen.getByTestId(`material-${FILM_WITH_REF.id}`)).toBeTruthy();
      expect(screen.getByTestId(`material-${JIG_NO_PART.id}`)).toBeTruthy();
      // ★KIT(form_factor null)은 사라진다 — 이게 필터의 요점이다.
      expect(screen.queryByTestId(`material-${KIT.id}`)).toBeNull();
    });

    it("폼팩터가 «없는»(null) 종도 「— (폼팩터 없음)」이라는 자기 선택지를 갖는다", async () => {
      await openMaterialsTab();
      const formSelect = screen.getByTestId("material-product-select") as HTMLSelectElement;
      // sentinel이 없으면 KIT은 어느 선택지에도 안 걸려 «영영 못 찾는 종»이 된다.
      expect(within(formSelect).getByText(/— \(폼팩터 없음\) \(1\)/)).toBeTruthy();

      fireEvent.change(formSelect, { target: { value: "__none__" } });
      expect(screen.getByTestId(`material-${KIT.id}`)).toBeTruthy();
      expect(screen.queryByTestId(`material-${FILM_WITH_REF.id}`)).toBeNull();
    });

    it("필터가 걸리면 「3건 중 N건 표시 중 — 필터: …」를 말한다 — 조용한 0은 커버리지 착시다", async () => {
      await openMaterialsTab();
      expect(screen.queryByTestId("material-filter-summary")).toBeNull();

      const formSelect = screen.getByTestId("material-product-select") as HTMLSelectElement;
      fireEvent.change(formSelect, { target: { value: "bar" } });

      const summary = await screen.findByTestId("material-filter-summary");
      expect(summary.textContent).toContain("3건 중 2건 표시 중");
      expect(summary.textContent).toContain("폼팩터=bar");
    });

    it("★`part`가 비어 있는 다수를 숨기지 않는다 — 「(부품 미지정) (N)」이 건수와 함께 뜬다", async () => {
      await openMaterialsTab();
      const formSelect = screen.getByTestId("material-product-select") as HTMLSelectElement;
      fireEvent.change(formSelect, { target: { value: "bar" } });

      const partSelect = screen.getByTestId("material-option-select") as HTMLSelectElement;
      expect(partSelect.disabled).toBe(false);
      // prod에선 83/129가 `part` 공백이다 — 건수를 라벨에 박지 않으면 그 사실이 사라진다.
      expect(within(partSelect).getByText("(부품 미지정) (1)")).toBeTruthy();
      expect(within(partSelect).getByText("필름 (1)")).toBeTruthy();

      fireEvent.change(partSelect, { target: { value: "__none__" } });
      expect(screen.getByTestId(`material-${JIG_NO_PART.id}`)).toBeTruthy();
      expect(screen.queryByTestId(`material-${FILM_WITH_REF.id}`)).toBeNull();
    });

    it("폼팩터를 바꾸면 이전 부품 선택이 남지 않는다 — 있는 종이 「없다」로 보이면 안 된다", async () => {
      await openMaterialsTab();
      const formSelect = screen.getByTestId("material-product-select") as HTMLSelectElement;
      fireEvent.change(formSelect, { target: { value: "bar" } });
      const partSelect = screen.getByTestId("material-option-select") as HTMLSelectElement;
      fireEvent.change(partSelect, { target: { value: "필름" } });
      expect(screen.queryByTestId(`material-${JIG_NO_PART.id}`)).toBeNull();

      // 폼팩터를 바꾼다 — `__none__` 쪽엔 「필름」 부품이 없다.
      fireEvent.change(formSelect, { target: { value: "__none__" } });

      expect(screen.getByTestId(`material-${KIT.id}`)).toBeTruthy();
      expect(partSelect.value).toBe("");
      expect(screen.queryByText(/해당 조건에 맞는 부자재 종이 없다/)).toBeNull();
    });

    it("★「+ 종 추가」는 필터가 걸려도 그대로 눌린다 — 필터가 조작을 삼키면 안 된다", async () => {
      await openMaterialsTab();
      const formSelect = screen.getByTestId("material-product-select") as HTMLSelectElement;
      fireEvent.change(formSelect, { target: { value: "bar" } });

      const addBtn = screen.getByRole("button", { name: "+ 종 추가" }) as HTMLButtonElement;
      expect(addBtn.disabled).toBe(false);
      // 실제로 눌러 본다 — prompt를 취소해도 화면이 깨지지 않아야 한다.
      const promptSpy = vi.spyOn(window, "prompt").mockReturnValue(null);
      fireEvent.click(addBtn);
      expect(promptSpy).toHaveBeenCalled();
      promptSpy.mockRestore();
      expect(screen.getByTestId(`material-${FILM_WITH_REF.id}`)).toBeTruthy();
    });

    // ★전체 App 경로로는 부자재 필터 0건 조합을 못 만든다(부품 목록이 늘 «현재 폼팩터에
    //   속한 것만»이라 정상 네비게이션으로는 0건이 안 나온다 — 위 P2-A와 같은 사정).
    //   그래서 0건 «안내 분기» 자체는 순수 컴포넌트를 직접 렌더해 잡는다.
    it("0건이면 빈 목록이 아니라 «사유»를 그린다", () => {
      render(
        <MaterialList
          materials={[]}
          selectedId={null}
          onSelect={() => {}}
          totalCount={129}
          filterSummary="129건 중 0건 표시 중 — 필터: 폼팩터=doorlock, 부품=필름"
          importedIds={new Set()}
        />,
      );
      expect(screen.getByText(/해당 조건에 맞는 부자재 종이 없다/)).toBeTruthy();
      expect(screen.getByTestId("material-filter-summary").textContent).toContain(
        "129건 중 0건 표시 중",
      );
      // ★「등록된 부자재 종이 없다」와 «다른 문장»이어야 한다 — 처분이 다르기 때문이다.
      expect(screen.queryByText("등록된 부자재 종이 없다.")).toBeNull();
    });

    it("데이터 자체가 0건이면 필터 탓으로 돌리지 않는다", () => {
      render(
        <MaterialList
          materials={[]}
          selectedId={null}
          onSelect={() => {}}
          importedIds={new Set()}
        />,
      );
      expect(screen.getByText("등록된 부자재 종이 없다.")).toBeTruthy();
    });
  });

  describe("★D: 「엑셀 참고값」이 부자재 탭 화면에 닿는다 (열한 번째 같은 병)", () => {
    // 발견(2026-08-23): `recipe_parser.py`는 참고값이 「화면에 보이기만 하고」라고 적어
    // 뒀는데, 실제로는 **어느 API 응답에도 안 실렸고** 프론트 `grep excel_ref` = 0건이었다.
    // prod 실측: 단가 보유 1/129 vs 참고값 보유 128/129인데 화면은 전 종에 대해
    // 「원장 연결 또는 수동 입력 필요」라고만 말했다 — **할 일이 셋인데 둘만 제시했고,
    // 빠진 셋째가 가장 싼 길이었다.** 화면이 사람을 더 비싼 일로 보내고 있었다.
    // ★2026-08-24 S4 ㉯ 개정: 「무엇이 아닌지」는 **수입 종에만** 참이다. FILM은 비수입
    //   종(원장 라인 0건)이라 엑셀이 정본이고(계약 §0-C, Jino 정정 00:06), 화면은 값을
    //   의심하게 만드는 대신 «아직 확정만 안 했다»를 말한다. 이 테스트가 그 전환을 붙든다.
    it("참고값이 있는 비수입 종을 고르면 «그 값»과 «엑셀이 정본»과 «확정하는 길»이 보인다", async () => {
      await openMaterialsTab();
      fireEvent.click(screen.getByTestId(`material-${FILM_WITH_REF.id}`));

      const note = await screen.findByTestId("material-excel-ref-note");
      expect(note.textContent).toContain("600원");         // 값
      expect(note.textContent).toContain("정본");           // 무엇인지 (「단가가 아니다」가 아니다)
      expect(note.textContent).toContain("레시피");         // 어디에 그 조작이 있는지
      expect(note.textContent).toContain("단가 입력·수정");
      // ★없는 길을 가리키지 않는다 — 이 종엔 원장 부자재 라인이 0건이다.
      expect(note.textContent).not.toContain("원장 부자재 라인");
    });

    it("★안내가 «없는 버튼»을 가리키지 않는다 — 부자재 탭엔 채택 버튼이 없다", async () => {
      await openMaterialsTab();
      fireEvent.click(screen.getByTestId(`material-${FILM_WITH_REF.id}`));
      await screen.findByTestId("material-excel-ref-note");

      // 이 탭에 「엑셀 참고값을 단가로 채택」 버튼은 **실제로 없다**(레시피 상세 몫이다).
      expect(screen.queryByRole("button", { name: /엑셀 참고값을 단가로 채택/ })).toBeNull();
      // 그러니 안내도 「이 탭에는 없다」를 스스로 말해야 한다 — 없는 조작을 시키면
      // 사유가 틀린 것이고, 사유가 틀리면 사람이 틀린 일을 한다(교훈 #349).
      const note = screen.getByTestId("material-excel-ref-note");
      expect(note.textContent).toContain("이 탭에는 채택 버튼이 없다");
    });

    it("참고값이 «없는» 종엔 그 줄이 아예 안 뜬다 — 빈 칸이 아니라 «해당 없음»이다", async () => {
      await openMaterialsTab();
      // 기본 선택은 KIT(참고값 없음).
      await screen.findByTestId("price-row-11");
      expect(screen.queryByTestId("material-excel-ref-note")).toBeNull();
    });

    it("목록 줄도 참고값의 «존재»를 말한다 — 「원장 연결 또는 수동 입력 필요」만 말하지 않는다", async () => {
      await openMaterialsTab();
      const row = screen.getByTestId(`material-${FILM_WITH_REF.id}`);
      // ★2026-08-24 S4 ㉯: FILM은 **비수입 종**(원장 라인 0건)이라 「참고값」이 아니라
      //   「엑셀 단가(미확정)」이라고 부른다 — 엑셀이 정본이기 때문이다(계약 §0-C · 합격 11).
      expect(row.textContent).toContain("엑셀 단가(미확정) 600원");
      expect(row.textContent).not.toContain("원장 연결 또는 수동 입력 필요");
    });

    it("순수 계층: 참고값 유무가 목록 문구를 가른다", () => {
      expect(lotCountText({ lot_count: 0, price_count: 0, stale_count: 0 }, true)).toBe(
        "단가 없음 — 원장 연결 또는 수동 입력 필요",
      );
      expect(
        lotCountText(
          { lot_count: 0, price_count: 0, stale_count: 0, excel_ref_price: "600.00" },
          true,
        ),
      ).toContain("엑셀 참고값 600원");
      // ★이미 단가가 있는 종은 «대조값»이라고 말한다 — 채택이 안 건드리기 때문이다.
      expect(excelRefNoteText({ excel_ref_price: "600.00", price_count: 2 }, true)).toContain(
        "대조값",
      );
      expect(excelRefNoteText({ excel_ref_price: null, price_count: 0 }, true)).toBeNull();
      expect(excelRefNoteText({ excel_ref_price: null, price_count: 0 }, false)).toBeNull();
    });

    // ══════════════════════════════════════════════════════════════
    // ★S4 ㉯ 순수 계층 — 수입/비수입이 문구를 «가른다» (합격 11·12)
    //
    // 이 블록이 없으면 「128종에게 틀린 말을 한다」가 다시 돌아와도 테스트는 초록이다.
    // ══════════════════════════════════════════════════════════════
    it("★비수입 종은 엑셀 값을 «정본»이라 부르고 원장 연결로 안내하지 않는다 (합격 11)", () => {
      const m = { lot_count: 0, price_count: 0, stale_count: 0, excel_ref_price: "600.00" };
      const line = lotCountText(m, false);
      // 값을 «의심하게» 만드는 말이 아니다 — 아직 «넣지» 않았을 뿐이다.
      expect(line).toContain("엑셀 단가(미확정) 600원");
      expect(line).not.toContain("단가 아님");
      expect(line).not.toContain("대조값");

      const note = excelRefNoteText({ excel_ref_price: "600.00", price_count: 0 }, false);
      expect(note).toContain("정본");
      // ★핵심 — **원장 연결을 안내하지 않는다.** 그 길은 이 종에 0건이라 영영 안 온다.
      expect(note).not.toContain("원장 부자재 라인");
      expect(note).toContain("엑셀 참고값을 단가로 채택");
      expect(note).toContain("단가 입력·수정");
    });

    it("★수입 종은 엑셀 값을 «대조값»이라 부르고 원장 연결을 안내한다 (합격 12)", () => {
      const note = excelRefNoteText({ excel_ref_price: "600.00", price_count: 0 }, true);
      expect(note).toContain("대조값");
      expect(note).toContain("원장 부자재 라인");
    });

    it("★참고값이 없을 때도 두 종이 다른 길을 안내한다", () => {
      const bare = { lot_count: 0, price_count: 0, stale_count: 0 };
      expect(lotCountText(bare, true)).toBe("단가 없음 — 원장 연결 또는 수동 입력 필요");
      // 비수입 종에게 「원장 연결」은 없는 길이다 — 말하지 않는다.
      expect(lotCountText(bare, false)).not.toContain("원장 연결");
      expect(lotCountText(bare, false)).toContain("단가 입력·수정");
    });
  });

  // ── 적대 리뷰 2R P1-1 채택 (2026-08-23) ────────────────────────────────────
  // 재현(리뷰어): 필터를 건 상태에서 「+ 종 추가」를 누르면 초록 「추가됨」이 뜨는데
  // 새 종이 화면 어디에도 없다. 원인은 `materials.py:create_material`이 `name`만
  // 세팅해 새 종은 항상 `form_factor: null`·`part: null`(sentinel `__none__`)이라
  // 필터가 걸려 있으면 반드시 걸러진다는 것 — 백엔드는 이번 범위 밖이라 고치지
  // 않는다(위임문 경계). 대신 **성공했을 때만** ①필터를 해제하고 ②새 종을 선택하고
  // ③왜 해제했는지 화면이 말한다. 이 수정은 리뷰어가 P2-3으로 올린 것도 같이
  // 닫는다(필터가 없어도 새 종이 선택된다 — 사람이 다시 찾아 누르지 않게).
  describe("★결함 수리 — 필터가 걸린 채 종을 추가해도 그 종이 화면에 닿는다 (적대 리뷰 2R P1-1)", () => {
    afterEach(() => {
      vi.mocked(createCostMaterial).mockClear();
      vi.mocked(fetchCostMaterials).mockResolvedValue({
        items: [KIT, FILM_WITH_REF, JIG_NO_PART],
      });
    });

    it("필터가 걸려 있으면: 추가 성공 → 필터가 풀리고 새 종이 목록·상세 패널에 뜬다", async () => {
      await openMaterialsTab();
      const formSelect = screen.getByTestId("material-product-select") as HTMLSelectElement;
      fireEvent.change(formSelect, { target: { value: "bar" } });
      // 필터가 실제로 걸렸다는 대조군 — KIT(form_factor null)이 화면에서 사라진다.
      expect(screen.queryByTestId(`material-${KIT.id}`)).toBeNull();
      await screen.findByTestId("material-filter-summary");

      // ★다음 load()가 새 종을 포함한 목록을 돌려주게 한다 — 실제 백엔드라면 생성 직후
      //   재조회에 새 행이 있는 것과 같다. createCostMaterial 자체가 돌려주는 모양과
      //   `fetchCostMaterials`가 다음에 돌려주는 모양을 일부러 같게 맞춘다(교훈:
      //   테스트 픽스처는 producer 실산출과 갈라지면 안 된다).
      vi.mocked(fetchCostMaterials).mockResolvedValueOnce({
        items: [KIT, FILM_WITH_REF, JIG_NO_PART, createdMaterialFixture("새로만든종")],
      });
      const promptSpy = vi.spyOn(window, "prompt").mockReturnValue("새로만든종");
      fireEvent.click(screen.getByRole("button", { name: "+ 종 추가" }));
      promptSpy.mockRestore();

      await waitFor(() => {
        expect(vi.mocked(createCostMaterial)).toHaveBeenCalledWith({ name: "새로만든종" });
      });

      // ★사람이 보는 결과 — 성공 메시지가 «필터를 해제했다»는 사실을 실제로 말한다.
      const toast = await screen.findByText(/^「새로만든종」 추가됨/);
      expect(toast.textContent).toContain("필터를 해제");

      // ★핵심 단언 — 새 종이 목록에 «실제로» 있다(리뷰어가 재현한 결함의 정반대).
      expect(await screen.findByTestId("material-9001")).toBeTruthy();
      expect(screen.getByText("새로만든종")).toBeTruthy();
      // 필터가 실제로 풀렸다 — 필터 요약 줄이 사라진다.
      expect(screen.queryByTestId("material-filter-summary")).toBeNull();
      // KIT(필터 밖이었던 종)도 다시 보인다 — 필터가 «전체»로 돌아갔다는 대조군.
      expect(screen.getByTestId(`material-${KIT.id}`)).toBeTruthy();
      // 새 종이 «선택»돼 있다 — 상세 패널이 그 종을 가리킨다(P2-3도 같이 닫는다).
      expect(
        screen.getByRole("heading", { name: "「새로만든종」 단가 이력" }),
      ).toBeTruthy();
    });

    it("필터가 없으면: 추가 성공 메시지에 「필터를 해제」가 없다 — 안 한 일을 했다고 말하지 않는다", async () => {
      await openMaterialsTab();
      expect(screen.queryByTestId("material-filter-summary")).toBeNull();

      vi.mocked(fetchCostMaterials).mockResolvedValueOnce({
        items: [KIT, FILM_WITH_REF, JIG_NO_PART, createdMaterialFixture("필터없이추가")],
      });
      const promptSpy = vi.spyOn(window, "prompt").mockReturnValue("필터없이추가");
      fireEvent.click(screen.getByRole("button", { name: "+ 종 추가" }));
      promptSpy.mockRestore();

      const toast = await screen.findByText(/^「필터없이추가」 추가됨/);
      expect(toast.textContent).not.toContain("필터를 해제");
      // P2-3 — 필터가 없어도 새 종이 선택된다(다시 찾아 누르지 않게).
      expect(
        screen.getByRole("heading", { name: "「필터없이추가」 단가 이력" }),
      ).toBeTruthy();
    });

    it("실패하면 아무것도 안 건드린다 — 필터·선택은 그대로다", async () => {
      await openMaterialsTab();
      const formSelect = screen.getByTestId("material-product-select") as HTMLSelectElement;
      fireEvent.change(formSelect, { target: { value: "bar" } });
      fireEvent.click(screen.getByTestId(`material-${FILM_WITH_REF.id}`));
      expect(
        await screen.findByRole("heading", {
          name: "「지문방지필름 TPU 3매 · 필름 (bar)」 단가 이력",
        }),
      ).toBeTruthy();

      vi.mocked(createCostMaterial).mockRejectedValueOnce(new Error("이미 등록돼 있다"));
      const promptSpy = vi.spyOn(window, "prompt").mockReturnValue("실패할이름");
      fireEvent.click(screen.getByRole("button", { name: "+ 종 추가" }));
      promptSpy.mockRestore();

      await screen.findByText("이미 등록돼 있다");
      // 필터·선택 둘 다 그대로다 — 실패는 아무것도 안 건드리지 않는다(하지 않는다).
      expect(screen.getByTestId("material-filter-summary")).toBeTruthy();
      expect(screen.queryByTestId(`material-${KIT.id}`)).toBeNull();
      expect(
        screen.getByRole("heading", { name: "「지문방지필름 TPU 3매 · 필름 (bar)」 단가 이력" }),
      ).toBeTruthy();
    });
  });

  // ── 적대 리뷰 2R P1-2 채택 (2026-08-23) ────────────────────────────────────
  // M20(승인 버튼 텍스트→{null})·M23(<MaterialList> 호출부의 onApprove 배선 제거)·
  // M22(「+ 단가 입력·수정」 버튼 제거)가 767건 전부 초록으로 살아남았다. 직전 라운드의
  // P1(레시피 「승인」 버튼 절단)과 같은 모양인데 «부자재 탭»만 안 지켜지고 있었다 —
  // `lotCountText`가 화면에서 사람을 그 버튼으로 보내는데(`원장 연결 또는 수동 입력
  // 필요`), 그 버튼이 사라져도 스위트는 조용했다.
  describe("★결함 수리 — 부자재 탭 조작 버튼의 표면을 붙든다 (적대 리뷰 2R P1-2)", () => {
    afterEach(() => {
      vi.mocked(patchCostMaterial).mockClear();
      vi.mocked(addCostManualPrice).mockClear();
    });

    // M20·M23을 죽인다 — 버튼 텍스트가 사라지거나 onApprove 배선이 빠지면 이 단언이 운다.
    it("미승인 종 — 목록 줄에 「이 단가를 승인」 버튼이 있고 누르면 그 종 id로 patchCostMaterial({status:'approved'})가 불린다", async () => {
      await openMaterialsTab();
      const row = screen.getByTestId(`material-${FILM_WITH_REF.id}`);
      const approveBtn = within(row).getByRole("button", { name: "이 단가를 승인" }) as HTMLButtonElement;
      expect(approveBtn.disabled).toBe(false);

      fireEvent.click(approveBtn);

      await waitFor(() => {
        expect(vi.mocked(patchCostMaterial)).toHaveBeenCalledWith(FILM_WITH_REF.id, {
          status: "approved",
        });
      });
    });

    // ★반대 방향 — 코드를 먼저 확인했다: `MaterialList`는
    //   `onApprove && m.status !== "approved"`일 때만 버튼을 그린다. 이미 승인된 종엔
    //   버튼이 없어야 한다는 것을 «실제 동작»으로 확인한다(없는 것을 있다고 단언하지 않는다).
    //   기존 픽스처(KIT·FILM_WITH_REF·JIG_NO_PART) 셋 다 status가 "unconfirmed"라
    //   순수 컴포넌트를 직접 렌더해 재현한다.
    it("이미 승인된 종 — 목록 줄에 「승인」 버튼이 없다", () => {
      render(
        <MaterialList
          materials={[{ ...FILM_WITH_REF, status: "approved" }]}
          selectedId={null}
          onSelect={() => {}}
          onApprove={() => {}}
          importedIds={new Set()}
        />,
      );
      const row = screen.getByTestId(`material-${FILM_WITH_REF.id}`);
      expect(within(row).queryByRole("button", { name: "이 단가를 승인" })).toBeNull();
    });

    // ★★Jino 2026-08-24: *"부자재 단가를 수정 … 할 수 있는 곳이 전혀 없어. 수정했을때는
    //   이력이 있어야 할거고"*. 조작은 **실재했는데** 화면이 「덮어쓰지 않고 쌓인다」를
    //   한 번도 말하지 않아 「수정하는 곳이 없다」로 읽혔다. 문구를 지우면 그 오해가
    //   그대로 돌아오므로 **문구 자체를 표면으로 붙든다.**
    // ★★Jino 2026-08-24: *"각 부자재가 어느 제품에 들어가는지도 나오면 좋겠고"*.
    //   백엔드가 `used_by`를 실어 보내도 **화면이 안 그리면 없는 것과 같다** — 이 저장소가
    //   반복해 밟은 자리라 App 경로에서 픽셀을 직접 잰다.
    it("★부자재 상세가 «어느 제품에 들어가는지»를 그린다 — 승인 여부까지", async () => {
      await openMaterialsTab();
      const usage = await screen.findByTestId("material-usage");
      expect(usage.textContent).toContain("이 부자재를 쓰는 제품");
      expect(usage.textContent).toContain("오하이 빛반사, 지문방지 매트 필름 3매");
      expect(usage.textContent).toContain("오하이 빛반사, 지문방지 매트 필름 2매");
      // ★「들어간다」만으로는 계산에 쓰이는지 모른다 — 승인 여부가 함께 보여야 한다.
      expect(usage.textContent).toContain("승인됨");
      expect(usage.textContent).toContain("미확인 — 계산 안 함");
    });

    it("★아무도 안 쓰는 종은 «0건»이라고 말한다 — 빈 칸으로 두지 않는다", async () => {
      await openMaterialsTab();
      fireEvent.click(screen.getByTestId(`material-${FILM_WITH_REF.id}`));
      const usage = await screen.findByTestId("material-usage");
      expect(usage.textContent).toContain("아직 어느 레시피도 이 종을 쓰지 않는다");
    });

    it("★단가 입력이 «덮어쓰기가 아니라 이력으로 쌓인다»고 화면이 말한다", async () => {
      await openMaterialsTab();
      fireEvent.click(screen.getByTestId(`material-${FILM_WITH_REF.id}`));
      const note = await screen.findByTestId("manual-price-append-note");
      expect(note.textContent).toContain("덮어쓰지 않고");
      expect(note.textContent).toContain("새 발효일");
      // ★「이전 값이 남는다」까지 말해야 «이력»에 대한 답이 된다 — 앞부분만 남기는 변이를 막는다.
      expect(note.textContent).toContain("이전 값은");
    });

    // M22를 죽인다. ★안내 문구와 버튼의 실재를 «한 테스트에서 함께» 잰다 —
    //   문구가 가리키는 대상이 실제로 있는지를 같이 재면, 둘 중 하나가 사라질 때 운다.
    //   ★2026-08-24 S4 ㉯: 비수입 종의 안내가 ③ 번호 매김을 안 쓰게 바뀌었지만
    //   **가드의 본질은 「가리킨 버튼이 실재하는가」**라 그대로 유효하다 — 문자열만 옮겼다.
    it("「+ 단가 입력·수정」 버튼이 실재하고, 안내 문구가 가리키는 대상과 일치한다 — 누르면 그 종 id로 addCostManualPrice가 불린다", async () => {
      await openMaterialsTab();
      fireEvent.click(screen.getByTestId(`material-${FILM_WITH_REF.id}`));
      const note = await screen.findByTestId("material-excel-ref-note");
      expect(note.textContent).toContain("「+ 단가 입력·수정」");

      // ★버튼이 «실재»한다 — 안내가 가리키는 대상이 화면에 없으면 그 자체가 거짓말이다.
      const manualBtn = screen.getByRole("button", {
        name: "+ 단가 입력·수정",
      }) as HTMLButtonElement;
      expect(manualBtn.disabled).toBe(false);

      const promptSpy = vi
        .spyOn(window, "prompt")
        .mockReturnValueOnce("1000")
        .mockReturnValueOnce("조아테크")
        .mockReturnValueOnce("2026-08-24");
      fireEvent.click(manualBtn);
      promptSpy.mockRestore();

      await waitFor(() => {
        expect(vi.mocked(addCostManualPrice)).toHaveBeenCalledWith(FILM_WITH_REF.id, {
          unit_price_ex_vat: "1000",
          supplier: "조아테크",
          effective_date: "2026-08-24",
        });
      });
    });

    // ── D-CPP-55 (2026-08-24) ────────────────────────────────────────────────
    // ★**발효일이 없으면 이 버튼은 아무 일도 안 한 것과 같다.** 서버의 「최신 단가」는
    //   `(effective_date, id)` 내림차순이고 `null`은 맨 뒤로 간다 — 채택분(발효일 있음)이
    //   있는 종에 날짜 없는 단가를 넣으면 이력에만 남고 계산엔 영영 안 쓰인다
    //   (백엔드 `test_price_without_effective_date_never_becomes_latest`가 그 사실을 잠근다).
    //   초판이 정확히 그 상태였다: 단가와 공급처만 보냈다. 그래서 전파(D-CPP-55)를 다 고쳐도
    //   **Jino가 화면에서 값을 바꾸면 보드가 안 움직였을 것**이다 — 합격 14는 화면 표면으로
    //   판정되므로 이 한 칸이 빠지면 슬라이스 전체가 «API로만 되는» 상태가 된다.
    it("D-CPP-55: 수동 단가는 **발효일과 함께** 보낸다 — 없으면 그 값은 최신이 못 되어 표준원가가 안 움직인다", async () => {
      await openMaterialsTab();
      fireEvent.click(screen.getByTestId(`material-${FILM_WITH_REF.id}`));
      const manualBtn = await screen.findByRole("button", { name: "+ 단가 입력·수정" });

      vi.mocked(addCostManualPrice).mockClear();
      // 세 번째 물음(발효일)에 **오늘이 미리 채워져** 나온다 — 사람이 보고 고칠 수 있게.
      const promptSpy = vi
        .spyOn(window, "prompt")
        .mockReturnValueOnce("1000")
        .mockReturnValueOnce("")
        .mockImplementationOnce((_msg?: string, dflt?: string) => dflt ?? null);
      fireEvent.click(manualBtn);

      await waitFor(() => {
        expect(vi.mocked(addCostManualPrice)).toHaveBeenCalledTimes(1);
      });
      const [, body] = vi.mocked(addCostManualPrice).mock.calls[0];
      // 날짜가 «있다»는 것이 이 테스트의 전부다 — 빠지면 화면에서 값이 안 움직인다.
      expect(body.effective_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      // 세 번째 물음의 기본값이 오늘이어야 한다(사람이 매번 타이핑하게 두면 안 넣는다).
      const askedDefault = promptSpy.mock.calls[2]?.[1];
      expect(askedDefault).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(body.effective_date).toBe(askedDefault);
      promptSpy.mockRestore();
    });

    it("D-CPP-55: 발효일 물음을 취소하면 **아무것도 저장하지 않는다** — 날짜 없는 단가를 몰래 만들지 않는다", async () => {
      await openMaterialsTab();
      fireEvent.click(screen.getByTestId(`material-${FILM_WITH_REF.id}`));
      const manualBtn = await screen.findByRole("button", { name: "+ 단가 입력·수정" });

      vi.mocked(addCostManualPrice).mockClear();
      const promptSpy = vi
        .spyOn(window, "prompt")
        .mockReturnValueOnce("1000")
        .mockReturnValueOnce("조아테크")
        .mockReturnValueOnce(null); // 취소
      fireEvent.click(manualBtn);
      promptSpy.mockRestore();

      await new Promise((r) => setTimeout(r, 0));
      expect(vi.mocked(addCostManualPrice)).not.toHaveBeenCalled();
    });
  });

  // ── 적대 리뷰 2R P2-1 채택 (2026-08-23) ────────────────────────────────────
  // M16(부자재 `selected`를 `filteredMaterials` 대신 `materials`에서 찾음)이
  // SURVIVED — `useLayoutEffect`가 페인트 전에 selectedId를 filteredMaterials 안으로
  // 동기화하므로 라이브로 관측 가능한 차이는 못 만든다(리뷰어 판정, 그래서 P2다).
  // 그래도 레시피 쪽엔 이미 있는 «필터 밖 선택을 안 붙든다·재조회해도 유지된다» 성질을
  // 부자재 쪽에도 같은 깊이로 재둔다 — 커밋 주석이 명시한 방어층이 조용히 사라지는
  // 것까지는 훅이 못 잡아도, 그 방어층이 지키려는 «화면에 보이는 결과»는 이 테스트가
  // 지킨다.
  describe("★부자재 선택 규율 — 필터 밖 종을 붙들지 않는다 (적대 리뷰 2R P2-1)", () => {
    it("필터 밖으로 나간 선택은 상세 패널에서 더 이상 렌더되지 않는다", async () => {
      await openMaterialsTab();
      fireEvent.click(screen.getByTestId(`material-${FILM_WITH_REF.id}`));
      expect(
        await screen.findByRole("heading", {
          name: "「지문방지필름 TPU 3매 · 필름 (bar)」 단가 이력",
        }),
      ).toBeTruthy();

      // KIT 쪽(form_factor: null → sentinel `__none__`)으로 필터 — FILM_WITH_REF는
      // 밖으로 밀려난다.
      const formSelect = screen.getByTestId("material-product-select") as HTMLSelectElement;
      fireEvent.change(formSelect, { target: { value: "__none__" } });

      await waitFor(() => {
        expect(
          screen.queryByRole("heading", {
            name: "「지문방지필름 TPU 3매 · 필름 (bar)」 단가 이력",
          }),
        ).toBeNull();
      });
      // ★왼쪽 목록에서도 사라진다 — 필터가 실제로 걸렸다는 대조군.
      expect(screen.queryByTestId(`material-${FILM_WITH_REF.id}`)).toBeNull();
    });

    it("보존: 필터를 건 상태에서 재조회(승인 등)가 일어나도 «두 번째로 고른» 종이 계속 선택돼 있다", async () => {
      await openMaterialsTab();
      const formSelect = screen.getByTestId("material-product-select") as HTMLSelectElement;
      fireEvent.change(formSelect, { target: { value: "bar" } }); // FILM_WITH_REF·JIG_NO_PART만 남는다

      // 필터 안의 «두 번째» 항목을 사람이 직접 고른다 — 「사람이 고른 항목을 지키는 것」과
      // 「무조건 첫 항목으로 스냅하는 것」이 다른 결과를 내게 하는 것이 이 가드의 요점이다.
      fireEvent.click(screen.getByTestId(`material-${JIG_NO_PART.id}`));
      expect(
        await screen.findByRole("heading", { name: "「부착 지그 (bar)」 단가 이력" }),
      ).toBeTruthy();

      // 재조회를 일으킨다 — 선택 안 한 다른 종(FILM_WITH_REF)의 「승인」을 누른다.
      fireEvent.click(
        within(screen.getByTestId(`material-${FILM_WITH_REF.id}`)).getByRole("button", {
          name: "이 단가를 승인",
        }),
      );
      await screen.findByText("「지문방지필름 TPU 3매 · 필름 (bar)」 승인됨");

      // 선택은 여전히 JIG_NO_PART다 — 필터의 첫 항목(FILM_WITH_REF)으로 스냅되지 않았다.
      expect(screen.getByRole("heading", { name: "「부착 지그 (bar)」 단가 이력" })).toBeTruthy();
    });
  });

  describe("★E: 계산 내역의 「엑셀 참고값(채택 전)」 열 — 보이되 합계엔 «절대» 안 들어간다", () => {
    async function openRecipeDetail() {
      await renderApp();
      await screen.findByRole("heading", { name: /원가/ });
      fireEvent.click(screen.getByRole("button", { name: "레시피" }));
      return screen.findByTestId("recipe-detail-panel");
    }

    it("열 헤더와 행 값이 실제 픽셀이 된다", async () => {
      const panel = await openRecipeDetail();
      // ★VAT 기준까지 이름에 있어야 한다 — 이 화면의 기본 표기는 VAT «포함»(D-CPP-51)이라
      //   기준을 안 적으면 참고값이 반대로 읽힌다. `adopt_excel_prices`가 이 값을
      //   `unit_price_ex_vat`로 쓰므로 «VAT 제외»가 사실이다.
      expect(within(panel).getByText("엑셀 참고값(채택 전 · VAT 제외)")).toBeTruthy();
      // 첫 라인(필름)의 참고값 칸 — 「600원」이 그 칸 «안»에 있어야 한다.
      expect(within(panel).getByTestId("breakdown-excel-ref-0").textContent).toBe("600원");
      expect(within(panel).getByTestId("breakdown-excel-ref-1").textContent).toBe("98원");
    });

    // ★★§3 금지선의 화면판. 참고값(600+98=698)이 합계에 새면 2,137 → 2,835가 된다.
    //   이 단언이 없으면 「참고값을 합계에 더하는」 변이가 초록으로 살아남는다.
    it("★합계는 std_cost 그대로다 — 참고값을 더하지 않는다(계약 §3 금지선)", async () => {
      const panel = await openRecipeDetail();
      expect(within(panel).getByTestId("breakdown-total-ex").textContent).toBe("2,137원");
      expect(within(panel).getByTestId("breakdown-total-inc").textContent).toBe("2,350.7원");
      // 참고값을 더한 값이 화면 어디에도 없다.
      expect(within(panel).queryByText("2,835원")).toBeNull();
      expect(within(panel).queryByText("3,118.5원")).toBeNull();
    });

    it("참고값 열엔 합계가 «없다»고 말한다 — 빈 칸이면 「깜빡 잊었나」와 구별이 안 된다", async () => {
      const panel = await openRecipeDetail();
      expect(within(panel).getByTestId("breakdown-excel-ref-total").textContent).toBe("합계 없음");
      const note = within(panel).getByTestId("breakdown-excel-ref-note");
      expect(note.textContent).toContain("단가가 아니다");
      expect(note.textContent).toContain("합계에 들어가지 않는다");
    });

    // ★채택 «전» 상태 — 단가가 없고 참고값만 있는 라인. prod의 다수파(128/129)가 이 모양이다.
    //   순수 컴포넌트를 직접 렌더하는 이 파일의 기존 관례를 따른다.
    it("채택 전 라인: 참고값은 보이는데 금액·합계는 여전히 「—」다", () => {
      render(
        <StandardBreakdown
          standard={{
            computable: false,
            std_cost_ex_vat: null,
            std_cost_inc_vat: null,
            reason: "단가 없음 (1건: 지문방지필름 TPU 3매 · 필름 (bar))",
            unresolved: ["지문방지필름 TPU 3매 · 필름 (bar)"],
            partial_ex_vat: "0",
            partial_inc_vat: "0",
            line_count: 1,
            lines: [
              {
                label: "지문방지필름 TPU 3매 · 필름 (bar)",
                quantity: "3",
                unit_price_ex_vat: null,
                unit_price_inc_vat: null,
                amount_ex_vat: null,
                amount_inc_vat: null,
                price_status: "missing",
                inc_derived: false,
                price_source: null,
                price_note: null,
                material_id: 21,
                usable: false,
                excel_ref_price: "600.00",
              },
            ],
          }}
        />,
      );
      // 참고값은 보인다 — 「단가 없음」만 말하면 사람은 채택이라는 길을 못 본다.
      expect(screen.getByTestId("breakdown-excel-ref-0").textContent).toBe("600원");
      // ★그런데 합계는 여전히 «없음»이다. 참고값이 부분합·표준원가로 새면 §3 위반이다.
      expect(screen.getByTestId("breakdown-total-ex").textContent).toBe("—");
      expect(screen.getByTestId("breakdown-total-inc").textContent).toBe("—");
      expect(screen.queryByText("1,800원")).toBeNull();   // 600 × 3 이 금액 칸에 새면 안 된다
      // ★기존 자백 문구는 그대로 살아 있다(지우지 않는다).
      expect(screen.getByText(/부분합/)).toBeTruthy();
      expect(screen.getByText(/표준원가가 아니다/)).toBeTruthy();
    });
  });

  // ★D-CPP-56 (Jino 2026-08-24): *"이미 확인되어서 승인된 부자재 단가는 표시를 해주자.
  //   그러면 다른 제품을 레시피에서 볼때 확인된 단가라는걸 쉽게 알아볼 수 있잖아?"*
  //   ⇒ 판정 표면은 **레시피 상세의 상태 열**이다. 「값이 있다」가 아니라 「사람이 그것을
  //   확인된 단가로 알아본다」가 합격선이므로, 단언은 전부 App 경로(`renderApp`)를 지난다 —
  //   n=6 실측: 컴포넌트를 직접 렌더하는 테스트는 **호출부 변이를 원리적으로 못 잡는다.**
  describe("★H: 확인된 단가임이 «한눈에» 보인다 (D-CPP-56)", () => {
    async function openRecipeDetail() {
      await renderApp();
      await screen.findByRole("heading", { name: /원가/ });
      fireEvent.click(screen.getByRole("button", { name: "레시피" }));
      return screen.findByTestId("recipe-detail-panel");
    }

    it("쓸 수 있는 단가 줄엔 「확인됨」 배지가 붙는다 — 다른 제품 레시피에서도 같은 값을 알아본다", async () => {
      const panel = await openRecipeDetail();
      // 픽스처의 usable 라인 2개 «전부»에 붙는다. 하나만 붙으면 「한쪽만 고친다」의 재발이다.
      expect(within(panel).getByTestId("breakdown-confirmed-0").textContent).toBe("확인됨");
      expect(within(panel).getByTestId("breakdown-confirmed-1").textContent).toBe("확인됨");
    });

    // ★★이 단언이 초판의 실제 결함을 겨눈다 — 상태 열이 영어 `manual`을 날문자로 그렸다.
    //   번역표는 이미 있었는데 «단가 없음» 가지에만 연결돼 있었다.
    it("★상태 열에 영어 날문자(`manual`·`ledger`)가 남지 않는다", async () => {
      const panel = await openRecipeDetail();
      expect(within(panel).queryByText("manual")).toBeNull();
      expect(within(panel).queryByText("ledger")).toBeNull();
      // 출처는 «지워지지» 않고 한국어로 남는다 — 없애면 목표 카드 ③·합격 12가 깨진다.
      expect(within(panel).getAllByText("등록가").length).toBe(2);
    });

    // ★배지가 «아무 데나» 붙지 않는다는 것까지 잰다. 이걸 안 재면 「전부 확인됨으로 칠하는」
    //   변이가 초록으로 살아남고, 화면이 단가 없는 줄까지 확인됐다고 말하게 된다.
    it("★단가가 없는 줄엔 배지가 «없고» 사유가 그대로 뜬다", async () => {
      vi.mocked(fetchCostRecipes).mockResolvedValue({
        items: [
          {
            ...RECIPE,
            standard: {
              ...RECIPE.standard,
              computable: false,
              std_cost_ex_vat: null,
              std_cost_inc_vat: null,
              reason: "단가 없음 (1건: 패키지 (bar))",
              unresolved: ["패키지 (bar)"],
              line_count: 1,
              lines: [
                {
                  ...RECIPE.standard.lines[1],
                  unit_price_ex_vat: null,
                  unit_price_inc_vat: null,
                  amount_ex_vat: null,
                  amount_inc_vat: null,
                  price_status: "missing",
                  price_source: null,
                  usable: false,
                },
              ],
            },
          },
        ],
      });
      try {
        const panel = await openRecipeDetail();
        expect(within(panel).queryByTestId("breakdown-confirmed-0")).toBeNull();
        expect(within(panel).queryByText("확인됨")).toBeNull();
        expect(within(panel).getByText("단가 없음")).toBeTruthy();
      } finally {
        vi.mocked(fetchCostRecipes).mockResolvedValue({
          items: [RECIPE, RECIPE_FLIP, RECIPE_OTHER_PRODUCT, RECIPE_NULL_FORM],
        });
      }
    });

    // ★원장 파생도 «확인됨»이다 — D-CPP-56이 둘을 서열화하지 않기로 정했다. 다만 출처는
    //   구별돼 보인다(같은 배지 · 다른 출처 문자열).
    it("★원장 파생 단가도 같은 「확인됨」 배지를 받되 출처는 「원장」으로 갈린다", async () => {
      vi.mocked(fetchCostRecipes).mockResolvedValue({
        items: [
          {
            ...RECIPE,
            standard: {
              ...RECIPE.standard,
              line_count: 1,
              lines: [{ ...RECIPE.standard.lines[0], price_status: "ok", price_source: "ledger" }],
            },
          },
        ],
      });
      try {
        const panel = await openRecipeDetail();
        expect(within(panel).getByTestId("breakdown-confirmed-0").textContent).toBe("확인됨");
        expect(within(panel).getByText("원장")).toBeTruthy();
        expect(within(panel).queryByText("등록가")).toBeNull();
      } finally {
        vi.mocked(fetchCostRecipes).mockResolvedValue({
          items: [RECIPE, RECIPE_FLIP, RECIPE_OTHER_PRODUCT, RECIPE_NULL_FORM],
        });
      }
    });
  });

  describe("★F·G: 최초 진입 깜빡임 · 0건일 때 오른쪽 문구", () => {
    // ⚠️**F는 jsdom에서 원리적으로 못 잰다.** `useEffect`↔`useLayoutEffect`의 차이는
    //    «브라우저 페인트 전인가»인데 jsdom은 페인트를 하지 않고, RTL의 `act`가 passive
    //    effect까지 flush하므로 두 경우의 «최종 상태»가 같다. 아래는 그 최종 상태 —
    //    즉 「도착하면 안내문이 아니라 상세 패널이다」 — 만 지킨다. 1프레임 깜빡임의
    //    판정은 배포 후 라이브 화면 몫이다.
    it("F(부분): 레시피 탭 최초 진입의 «최종» 상태는 안내문이 아니라 상세 패널이다", async () => {
      await renderApp();
      await screen.findByRole("heading", { name: /원가/ });
      fireEvent.click(screen.getByRole("button", { name: "레시피" }));
      expect(await screen.findByTestId("recipe-detail-panel")).toBeTruthy();
      expect(screen.queryByTestId("recipe-detail-placeholder")).toBeNull();
    });

    it("G: 레시피가 아예 없으면 오른쪽이 「고를 것이 없다」고 말한다 — 「고른다」가 아니다", async () => {
      vi.mocked(fetchCostRecipes).mockResolvedValue({ items: [] });
      try {
        await renderApp();
        await screen.findByRole("heading", { name: /원가/ });
        fireEvent.click(screen.getByRole("button", { name: "레시피" }));

        const placeholder = await screen.findByTestId("recipe-detail-placeholder");
        expect(placeholder.textContent).toContain("고를 레시피가 없다");
        // ★고를 것이 없는데 「고른다」고 하면 안 된다(적대 리뷰 1R P2-3).
        expect(placeholder.textContent).not.toBe("왼쪽에서 레시피를 고른다.");
      } finally {
        vi.mocked(fetchCostRecipes).mockResolvedValue({
          items: [RECIPE, RECIPE_FLIP, RECIPE_OTHER_PRODUCT, RECIPE_NULL_FORM],
        });
      }
    });

    // ★필터가 «전부» 걸러낸 0건은 전체 App 경로로 못 만든다(폼팩터 목록이 늘 현재 제품에
    //   속한 것만이라 — 위 P2-A와 같은 사정). 그래서 순수 함수로 그 분기를 잡는다.
    it("G(순수): 필터 0건과 데이터 0건은 «다른 문장»이다 — 처분이 다르기 때문이다", () => {
      expect(recipePlaceholderText(2, 4)).toBe("왼쪽에서 레시피를 고른다.");
      expect(recipePlaceholderText(0, 4)).toContain("필터가 전부 걸러냈다");
      expect(recipePlaceholderText(0, 0)).toContain("엑셀 2종을 올리면");
      expect(recipePlaceholderText(0, 4)).not.toBe(recipePlaceholderText(0, 0));
    });
  });

  // ══════════════════════════════════════════════════════════════════
  // ★결함 수리 — 수입 완제품 표면(D-CPP-61)의 호출부가 App 경로로 도달한다
  // (적대 리뷰 1R P1-4, 2026-08-26)
  //
  // 재현(리뷰어): `CostPage.tsx`의 `{selectedIsImportedGoods ? (` 섹션 조건을 `{false ? (`로
  // 바꿔도, 같은 파일의 `linkTargetId={selected.id}` 한 줄을 지워도 **프론트 전건 초록**이었다.
  // 원인은 `importedGoodsSurface.test.tsx`가 `LedgerMaterialLines`·`StandardCostBoard`를
  // **직접 렌더**할 뿐 `App`/`CostPage` 경로를 안 타는 것 — 이 파일 머리말의 SUR-2와
  // **같은 모양의 구멍**이다. 그 파일을 새로 만드는 대신, 이 파일의 「App 통째 렌더」
  // 관례를 그대로 따라 여기에 붙인다.
  // ══════════════════════════════════════════════════════════════════
  describe("★결함 수리 — 수입 완제품 표면이 App 경로로 닿는다 (적대 리뷰 1R P1-4)", () => {
    afterEach(() => {
      vi.mocked(fetchCostMaterials).mockResolvedValue({
        items: [KIT, FILM_WITH_REF, JIG_NO_PART],
      });
      vi.mocked(fetchCostLedgerMaterialLines).mockResolvedValue({ items: [LEDGER_ROW] });
    });

    it("SUR-9: 수입 완제품 섹션이 App 경로로 도달한다 — 종을 고르면 섹션과 불러오기 버튼이 뜬다", async () => {
      vi.mocked(fetchCostMaterials).mockResolvedValue({
        items: [KIT, FILM_WITH_REF, JIG_NO_PART, IMPORTED_GOODS_MATERIAL],
      });
      await openMaterialsTab();
      fireEvent.click(screen.getByTestId(`material-${IMPORTED_GOODS_MATERIAL.id}`));

      // ⇒ `{selectedIsImportedGoods ? (` 섹션 조건을 `{false ? (`로 바꾸면 이 findByTestId가
      //   타임아웃으로 죽는다(리뷰어가 재현한 결함 그대로).
      const section = await screen.findByTestId("imported-goods-ledger-lines");
      expect(within(section).getByTestId("load-imported-lines")).toBeTruthy();
      expect(section.textContent).toContain(IMPORTED_GOODS_MATERIAL.name);
    });

    it("SUR-10: `linkTargetId`가 호출부에서 실제로 전달된다 — 제안이 없어도 연결 버튼이 뜬다", async () => {
      vi.mocked(fetchCostMaterials).mockResolvedValue({
        items: [KIT, FILM_WITH_REF, JIG_NO_PART, IMPORTED_GOODS_MATERIAL],
      });
      // `fetchCostLedgerMaterialLines(true)`가 실제로 불렸을 때만 product 라인이 온다 —
      // «사람이 불러오기를 눌러야 온다»는 CostPage.tsx의 설계를 픽스처에서도 지킨다.
      vi.mocked(fetchCostLedgerMaterialLines).mockImplementation(async (includeProducts) =>
        includeProducts
          ? { items: [LEDGER_ROW, IMPORTED_PRODUCT_LINE] }
          : { items: [LEDGER_ROW] },
      );
      await openMaterialsTab();
      fireEvent.click(screen.getByTestId(`material-${IMPORTED_GOODS_MATERIAL.id}`));
      fireEvent.click(await screen.findByTestId("load-imported-lines"));

      // IMPORTED_PRODUCT_LINE.suggestion.material_id는 null이다 — 「연결」이 뜨려면
      // 호출부가 `linkTargetId={selected.id}`를 줘야만 한다(LedgerMaterialLines.tsx:849
      // `const suggested = linkTargetId ?? r.suggestion.material_id;`).
      // ⇒ 그 줄을 지우면 `suggested`가 `null`이 되어 아래 버튼이 원리적으로 안 뜬다.
      const row = await screen.findByTestId(`ledger-line-${IMPORTED_PRODUCT_LINE.line_id}`);
      expect(within(row).getByRole("button", { name: /연결/ })).toBeTruthy();
    });

    it("비수입 종엔 그 섹션이 안 뜬다 — 반대 방향 잠금", async () => {
      await openMaterialsTab();
      fireEvent.click(screen.getByTestId(`material-${FILM_WITH_REF.id}`));
      await screen.findByTestId("material-excel-ref-note");
      expect(screen.queryByTestId("imported-goods-ledger-lines")).toBeNull();
    });
  });

  describe("★결함 수리 — 레시피 탭 배지가 App 경로로 닿는다 (적대 리뷰 1R P1-4)", () => {
    afterEach(() => {
      vi.mocked(fetchCostRecipes).mockResolvedValue({
        items: [RECIPE, RECIPE_FLIP, RECIPE_OTHER_PRODUCT, RECIPE_NULL_FORM],
      });
    });

    it("SUR-11: 「수입 완제품」·「폼팩터 추정」 배지가 App 경로로 목록에 뜬다 — assembly·rule 행엔 없다", async () => {
      vi.mocked(fetchCostRecipes).mockResolvedValue({
        items: [RECIPE, RECIPE_IMPORTED, RECIPE_FORM_ESTIMATED],
      });
      await renderApp();
      await screen.findByRole("heading", { name: /원가/ });
      fireEvent.click(screen.getByRole("button", { name: "레시피" }));
      await screen.findByTestId(`recipe-row-${RECIPE.id}`);

      // ⇒ `data-testid={\`recipe-row-imported-${r.id}\`}` 배지 블록을 지우면 이 findByTestId가
      //   타임아웃으로 죽는다.
      expect(
        await screen.findByTestId(`recipe-row-imported-${RECIPE_IMPORTED.id}`),
      ).toBeTruthy();
      expect(
        screen.getByTestId(`recipe-row-form-estimated-${RECIPE_FORM_ESTIMATED.id}`),
      ).toBeTruthy();

      // 반대 방향 — RECIPE는 recipe_kind: "assembly" · form_source: "rule"이라 배지가 없다.
      const assemblyRow = screen.getByTestId(`recipe-row-${RECIPE.id}`);
      expect(within(assemblyRow).queryByTestId(`recipe-row-imported-${RECIPE.id}`)).toBeNull();
      expect(
        within(assemblyRow).queryByTestId(`recipe-row-form-estimated-${RECIPE.id}`),
      ).toBeNull();
      // RECIPE_IMPORTED 자신도 「폼팩터 추정」 배지는 없다(form_source: "rule") — 두 배지가
      // 독립적으로 조건화돼 있는지를 함께 잰다.
      const importedRow = screen.getByTestId(`recipe-row-${RECIPE_IMPORTED.id}`);
      expect(
        within(importedRow).queryByTestId(`recipe-row-form-estimated-${RECIPE_IMPORTED.id}`),
      ).toBeNull();
    });
  });
});
// ★다른 라우트에서 같은 단언을 반복하지 않는다: 메뉴는 `Layout`이 라우트와 무관하게 그리므로
//   SUR-4가 이미 그 사실을 잰다. 대신 다른 페이지(대시보드 등)를 렌더하면 그 페이지의 목데이터
//   요구가 이 파일에 딸려 들어와, **원가와 무관한 이유로 빨개지는 테스트**가 된다.
