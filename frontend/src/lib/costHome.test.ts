// costHome.test.ts — 홈 탭의 순수 규칙(`lib/costHome.ts`). D-CPP-62 S2가 새로 만든 것 중
// 이 파일이 이제까지 **0건**이었다(730줄 무보호). 여기는 함수 단위 — 「값이 사람 말이
// 되는 규칙」만 잰다. 그 값이 실제 DOM 픽셀이 되는지는 `costHomeSurface.test.tsx`가 잰다
// (이 저장소가 반복 밟은 「함수는 값을 만드는데 사람은 못 본다」 — 두 층을 나눠서 잡는다).
import { describe, expect, it } from "vitest";

import type { CostMaterial, CostRecipe, CostTableCensusRow } from "./api";
import {
  anomalyKinds,
  buildCostInbox,
  EMPTY_IS_NOT_ZERO_NOTE,
  filterRoundTripRows,
  hasNoUnitPrice,
  materialFormItems,
  materialPartItems,
  ROUND_TRIP_COLUMNS,
  roundTripBadges,
  roundTripCountText,
  ROUND_TRIP_FILTER_NONE,
} from "./costHome";

// ══════════════════════════════════════════════════════════════════
// 픽스처 — prod 실측(2026-08-28) 비율을 흉내낸다: 단가원천은 manual이 절대다수(71),
// ledger는 소수(4)다. 전부 ledger인 픽스처는 이 저장소가 이미 한 번 밟은 오염이다
// (교훈: 배지 픽스처가 `ledger`인데 prod 파생 18종은 100% manual이었다).
// ══════════════════════════════════════════════════════════════════

function material(over: Partial<CostMaterial> = {}): CostMaterial {
  return {
    id: 1,
    name: "지문방지필름 TPU 3매",
    unit: "ea",
    category: "부자재",
    status: "approved",
    excel_label: null,
    excel_ref_price: null,
    match_rule: null,
    form_factor: "fold",
    part: "필름",
    note: null,
    lot_count: 1,
    price_count: 1,
    stale_count: 0,
    latest_price_ex_vat: "600",
    latest_price_inc_vat: "660",
    latest_price_inc_derived: false,
    latest_price_source: "manual",
    latest_price_effective_date: "2026-08-18",
    price_rule: "latest",
    lot_price_min: null,
    lot_price_max: null,
    lot_price_has_span: false,
    price_conflict: false,
    price_conflict_price_id: null,
    prices: [],
    used_by: [],
    used_by_count: 0,
    ...over,
  };
}

function recipe(over: Partial<CostRecipe> = {}): CostRecipe {
  return {
    id: 45,
    product_name: "지문방지필름 TPU 3매",
    form_factor: "fold",
    status: "draft",
    source: "excel",
    // 분할 «전» 레시피 = 단일 그레인. 빈 문자열은 사실이지 자리표시자가 아니다(D-CPP-67).
    variant: "",
    recipe_kind: "assembly",
    form_source: "rule",
    anomaly_flag: null,
    approved_at: null,
    match: null,
    line_count: 3,
    link_count: 0,
    standard: {
      computable: false,
      std_cost_ex_vat: null,
      std_cost_inc_vat: null,
      reason: null,
      unresolved: [],
      partial_ex_vat: null,
      partial_inc_vat: null,
      line_count: 0,
      lines: [],
    },
    picked: {
      state: "none",
      item_id: null,
      item_name: null,
      section: null,
      item_total_inc_vat: null,
      picked_at: null,
      absent_confirmed_at: null,
      absent_note: null,
    },
    ...over,
  };
}

function tableRow(over: Partial<CostTableCensusRow> = {}): CostTableCensusRow {
  return {
    id: 45,
    section: "부자재",
    item_name: "지문방지필름 TPU 3매",
    form_factor: "fold",
    recipe_kind: "assembly",
    total_inc_vat: "2350.7",
    row_number: 12,
    anomalies: null,
    line_count: 3,
    picked: true,
    picked_by_recipe_id: 45,
    ...over,
  };
}

describe("hasNoUnitPrice — 「없음」과 「0」을 가르는 그 판정", () => {
  it("ex·inc 둘 다 null이면 단가 없음이다", () => {
    expect(hasNoUnitPrice({ latest_price_ex_vat: null, latest_price_inc_vat: null })).toBe(true);
  });
  it("ex만 있어도(inc가 아직 파생 전이어도) 단가 없음이 아니다", () => {
    expect(hasNoUnitPrice({ latest_price_ex_vat: "600", latest_price_inc_vat: null })).toBe(false);
  });
  it("inc만 있어도 단가 없음이 아니다 — 「한쪽만 본다」가 이 저장소의 상습 실패다", () => {
    expect(hasNoUnitPrice({ latest_price_ex_vat: null, latest_price_inc_vat: "660" })).toBe(false);
  });
  it("둘 다 있으면 당연히 단가 없음이 아니다", () => {
    expect(hasNoUnitPrice({ latest_price_ex_vat: "600", latest_price_inc_vat: "660" })).toBe(false);
  });
});

describe("anomalyKinds — anomaly 문자열 → 종류 목록", () => {
  it("null·빈 문자열은 빈 목록이다", () => {
    expect(anomalyKinds(null)).toEqual([]);
    expect(anomalyKinds(undefined)).toEqual([]);
    expect(anomalyKinds("")).toEqual([]);
  });
  it("콤마로 갈라 첫 콜론 앞만 취한다 (실측 모양)", () => {
    expect(
      anomalyKinds("price_conflict:부착 안내문:55.0≠30.0,price_conflict:비닐(16*23+4):15.0≠10.0"),
    ).toEqual(["price_conflict"]); // 중복은 버린다
  });
  it("서로 다른 종류는 각각 남는다", () => {
    expect(anomalyKinds("no_recipe_match:이유,needs_manual_lines:이유2")).toEqual([
      "no_recipe_match",
      "needs_manual_lines",
    ]);
  });
  it("콜론이 없는 조각도 그대로 종류가 된다", () => {
    expect(anomalyKinds("mystery_kind")).toEqual(["mystery_kind"]);
  });
});

describe("buildCostInbox — 여섯 재고를 넷으로 접는다", () => {
  it("전부 빈 입력이어도 항상 4묶음이다 — 「빈 인박스」와 「인박스가 안 뜸」은 다른 사실이다", () => {
    const groups = buildCostInbox({ materials: [], recipes: [], tableItems: [] });
    expect(groups.map((g) => g.key)).toEqual(["conflict", "no-price", "no-recipe", "cost-table"]);
    expect(groups.every((g) => g.rows.length === 0)).toBe(true);
  });

  it("모순 묶음 — price_conflict 레시피만 잡는다, 다른 anomaly는 안 잡는다", () => {
    const groups = buildCostInbox({
      materials: [],
      recipes: [
        recipe({ id: 1, anomaly_flag: "price_conflict:비닐:15.0≠10.0" }),
        recipe({ id: 2, anomaly_flag: "no_recipe_match:이유" }),
        recipe({ id: 3, anomaly_flag: null }),
      ],
      tableItems: [],
    });
    const conflict = groups.find((g) => g.key === "conflict")!;
    expect(conflict.rows.map((r) => r.key)).toEqual(["conflict-1"]);
    expect(conflict.rows[0].target).toEqual({ kind: "recipe", id: 1 });
    expect(conflict.rows[0].note).toBe("price_conflict:비닐:15.0≠10.0");
  });

  it("단가 없음 묶음 — hasNoUnitPrice와 같은 판정을 쓴다", () => {
    const groups = buildCostInbox({
      materials: [
        material({ id: 10, latest_price_ex_vat: null, latest_price_inc_vat: null, excel_ref_price: "168" }),
        material({ id: 11 }), // 단가 있음 — 안 잡힌다
      ],
      recipes: [],
      tableItems: [],
    });
    const noPrice = groups.find((g) => g.key === "no-price")!;
    expect(noPrice.rows.map((r) => r.key)).toEqual(["no-price-10"]);
    // ★엑셀 참고값이 있으면 그 사실을 note가 말한다 — 「단가가 아니라 대조값」을 반복한다.
    expect(noPrice.rows[0].note).toContain("엑셀 참고값 168");
    expect(noPrice.rows[0].target).toEqual({ kind: "material", id: 10 });
  });

  it("단가 없음 묶음 — 엑셀 참고값도 없으면 note는 null이다(할 말이 없다)", () => {
    const groups = buildCostInbox({
      materials: [material({ id: 12, latest_price_ex_vat: null, latest_price_inc_vat: null, excel_ref_price: null })],
      recipes: [],
      tableItems: [],
    });
    expect(groups.find((g) => g.key === "no-price")!.rows[0].note).toBeNull();
  });

  it("구성 없음 묶음 — no_recipe_match·needs_manual_lines 둘 다 잡는다", () => {
    const groups = buildCostInbox({
      materials: [],
      recipes: [
        recipe({ id: 20, anomaly_flag: "no_recipe_match:이유" }),
        recipe({ id: 21, anomaly_flag: "needs_manual_lines:이유" }),
        recipe({ id: 22, anomaly_flag: "price_conflict:이유" }), // 다른 묶음 소관 — 안 잡힌다
      ],
      tableItems: [],
    });
    const noRecipe = groups.find((g) => g.key === "no-recipe")!;
    expect(noRecipe.rows.map((r) => r.key).sort()).toEqual(["no-recipe-20", "no-recipe-21"]);
  });

  it("★넷째 묶음(원가표 항목) — 픽 안 된 항목은 이상 유무와 무관하게 남는다", () => {
    const groups = buildCostInbox({
      materials: [],
      recipes: [],
      tableItems: [tableRow({ id: 60, picked: false, picked_by_recipe_id: null, anomalies: null })],
    });
    const costTable = groups.find((g) => g.key === "cost-table")!;
    expect(costTable.rows.map((r) => r.key)).toEqual(["cost-table-60"]);
    expect(costTable.rows[0].note).toBe("픽 안 됨 — 아직 어느 레시피도 이 항목을 안 골랐다");
    expect(costTable.rows[0].target).toBeNull();
  });

  it("★★중복 접기 — price_conflict가 레시피와 원가표 항목 «양쪽에» 서면 한 줄로만 센다", () => {
    // 레시피 45·97의 실측 모양: cost_recipe.anomaly_flag와 cost_table_item.anomalies
    // 둘 다에 price_conflict가 적혀 있다. 원천을 그대로 세면 같은 사건이 두 줄에 선다
    // (설계 Q1 ⚠️) — 이 테스트가 그 결함의 재발을 막는다.
    const groups = buildCostInbox({
      materials: [],
      recipes: [recipe({ id: 45, anomaly_flag: "price_conflict:부착 안내문:55.0≠30.0" })],
      tableItems: [
        tableRow({
          id: 45,
          picked: true,
          picked_by_recipe_id: 45,
          anomalies: "price_conflict:부착 안내문:55.0≠30.0",
        }),
      ],
    });
    const conflict = groups.find((g) => g.key === "conflict")!;
    const costTable = groups.find((g) => g.key === "cost-table")!;
    // ★첫 묶음(모순)에서 1건으로 잡힌다.
    expect(conflict.rows.map((r) => r.key)).toEqual(["conflict-45"]);
    // ★넷째 묶음에는 «같은 사건»이 다시 서지 않는다 — 픽이 됐고 이상이 price_conflict뿐이면 뺀다.
    expect(costTable.rows.map((r) => r.key)).toEqual([]);
  });

  it("같은 항목이라도 «픽이 안 됐으면» 넷째 묶음에 남는다 — 별개의 할 일이라서다", () => {
    const groups = buildCostInbox({
      materials: [],
      recipes: [recipe({ id: 97, anomaly_flag: "price_conflict:비닐:15.0≠10.0" })],
      tableItems: [
        tableRow({
          id: 97,
          picked: false,
          picked_by_recipe_id: null,
          anomalies: "price_conflict:비닐:15.0≠10.0",
        }),
      ],
    });
    const costTable = groups.find((g) => g.key === "cost-table")!;
    expect(costTable.rows.map((r) => r.key)).toEqual(["cost-table-97"]);
    expect(costTable.rows[0].note).toContain("픽 안 됨");
  });

  it("넷째 묶음 — 픽 됐고 price_conflict «가 아닌» 다른 이상이면 남는다", () => {
    const groups = buildCostInbox({
      materials: [],
      recipes: [],
      tableItems: [
        tableRow({ id: 61, picked: true, picked_by_recipe_id: 61, anomalies: "no_recipe_match:이유" }),
      ],
    });
    const costTable = groups.find((g) => g.key === "cost-table")!;
    expect(costTable.rows.map((r) => r.key)).toEqual(["cost-table-61"]);
    expect(costTable.rows[0].note).toBe("⚠ no_recipe_match:이유");
    expect(costTable.rows[0].target).toEqual({ kind: "recipe", id: 61 });
  });
});

describe("filterRoundTripRows — 왕복 표 행 필터", () => {
  const rows: CostMaterial[] = [
    material({ id: 1, form_factor: "fold", part: "필름" }),
    material({ id: 2, form_factor: "flip", part: null }),
    material({ id: 3, form_factor: null, part: null, latest_price_ex_vat: null, latest_price_inc_vat: null }),
    material({ id: 4, form_factor: "fold", part: "필름", price_conflict: true }),
  ];

  it("필터 없음(ROUND_TRIP_FILTER_NONE)이면 전건이다", () => {
    expect(filterRoundTripRows(rows, ROUND_TRIP_FILTER_NONE).map((m) => m.id)).toEqual([1, 2, 3, 4]);
  });

  it("form 필터 — __none__은 「폼팩터 없음」 종만 남긴다", () => {
    expect(
      filterRoundTripRows(rows, { ...ROUND_TRIP_FILTER_NONE, form: "__none__" }).map((m) => m.id),
    ).toEqual([3]);
  });

  it("part 필터 — 빈 part도 __none__으로 매칭된다", () => {
    expect(
      filterRoundTripRows(rows, { ...ROUND_TRIP_FILTER_NONE, part: "__none__" }).map((m) => m.id),
    ).toEqual([2, 3]);
  });

  it("noPriceOnly — hasNoUnitPrice인 것만 남긴다", () => {
    expect(
      filterRoundTripRows(rows, { ...ROUND_TRIP_FILTER_NONE, noPriceOnly: true }).map((m) => m.id),
    ).toEqual([3]);
  });

  it("conflictOnly — price_conflict인 것만 남긴다", () => {
    expect(
      filterRoundTripRows(rows, { ...ROUND_TRIP_FILTER_NONE, conflictOnly: true }).map((m) => m.id),
    ).toEqual([4]);
  });

  it("필터를 합치면 AND다", () => {
    expect(
      filterRoundTripRows(rows, { ...ROUND_TRIP_FILTER_NONE, form: "fold", conflictOnly: true }).map(
        (m) => m.id,
      ),
    ).toEqual([4]);
  });
});

describe("materialFormItems / materialPartItems — 부자재 탭·홈이 같은 함수를 쓴다", () => {
  it("폼팩터 없는(null) 종도 자기 이름을 가진 선택지를 갖는다", () => {
    const items = materialFormItems([
      { form_factor: "fold" },
      { form_factor: "fold" },
      { form_factor: null },
    ]);
    const none = items.find((i) => i.value === "__none__");
    expect(none).toBeTruthy();
    expect(none!.label).toBe("— (폼팩터 없음)");
    expect(none!.count).toBe(1);
    expect(items.find((i) => i.value === "fold")!.count).toBe(2);
  });

  it("form이 null이면 부품 목록은 빈 배열이다 — 「폼팩터부터 고르라」는 뜻", () => {
    expect(materialPartItems([{ form_factor: "fold", part: "필름" }], null)).toEqual([]);
  });

  it("빈 part는 「(부품 미지정) (N)」으로 건수와 함께 뜬다 — prod 83/139가 여기다", () => {
    const items = materialPartItems(
      [
        { form_factor: "fold", part: null },
        { form_factor: "fold", part: "  " },
        { form_factor: "fold", part: "필름" },
        { form_factor: "flip", part: "다른폼팩터" }, // 다른 폼팩터는 안 센다
      ],
      "fold",
    );
    const none = items.find((i) => i.value === "__none__");
    expect(none!.label).toBe("(부품 미지정) (2)");
    expect(items.find((i) => i.value === "필름")!.label).toBe("필름 (1)");
  });
});

describe("roundTripCountText", () => {
  it("전체 0건이면 「원가 정본을 아직 안 올렸다」다 — 필터 0건과 다른 사실이다", () => {
    expect(roundTripCountText(0, 0)).toBe("부자재 종이 0건이다 — 원가 정본을 아직 안 올렸다.");
  });
  it("전체는 있는데 필터로 0건이면 「N건 중 0건」이다", () => {
    expect(roundTripCountText(0, 139)).toBe("139건 중 0건 표시");
  });
  it("일반형", () => {
    expect(roundTripCountText(64, 139)).toBe("139건 중 64건 표시");
  });
});

describe("roundTripBadges — 왕복 표 행 배지", () => {
  it("단가 없으면 «없음이지 0이 아니다» 배지가 선다", () => {
    const badges = roundTripBadges(
      material({ latest_price_ex_vat: null, latest_price_inc_vat: null }),
    );
    const b = badges.find((x) => x.key === "no-price")!;
    expect(b.label).toBe("▢단가없음");
    // ★같은 자백 문구의 단일 원천 — 부자재 탭 빈 이력이 쓰는 그 문구다.
    expect(b.title).toContain(EMPTY_IS_NOT_ZERO_NOTE);
  });

  it("단가가 있으면 no-price 배지가 없다", () => {
    expect(roundTripBadges(material()).find((x) => x.key === "no-price")).toBeUndefined();
  });

  it("모순·원장정본·파생 배지 — 조건이 각각 독립이다", () => {
    const badges = roundTripBadges(
      material({ price_conflict: true, latest_price_source: "ledger", latest_price_inc_derived: true }),
    );
    expect(badges.map((b) => b.key).sort()).toEqual(["conflict", "derived", "ledger"]);
  });

  it("아무 조건도 없으면 배지가 0건이다", () => {
    expect(roundTripBadges(material())).toEqual([]);
  });
});

describe("ROUND_TRIP_COLUMNS — 설계 Q3의 열 12개 (S3 파일의 헤더가 되는 정본)", () => {
  it("열이 정확히 12개다 — 하나가 조용히 빠지면 다운로드 파일의 헤더가 준다", () => {
    expect(ROUND_TRIP_COLUMNS.length).toBe(12);
  });
  it("키가 전부 유니크하다", () => {
    const keys = ROUND_TRIP_COLUMNS.map((c) => c.key);
    expect(new Set(keys).size).toBe(keys.length);
  });
  it("단가(ex_vat)·발효일·이름·폼팩터·부품·단위·비고는 수정 가능이다(설계 Q3)", () => {
    const editable = new Set(
      ROUND_TRIP_COLUMNS.filter((c) => c.editable).map((c) => c.key),
    );
    expect(editable).toEqual(
      new Set(["name", "form_factor", "part", "unit", "price_ex", "effective_date", "status_note"]),
    );
  });
});
