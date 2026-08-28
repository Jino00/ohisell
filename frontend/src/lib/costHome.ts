// costHome.ts — 「💰 원가」 **홈 탭**의 순수 규칙 (계약 `CONTRACT_cost_excel_roundtrip.md` §4 S2 ·
// 설계 `docs/PLAN_cost_menu_s2_screen.md` Q1·Q3).
//
// ★**왜 `.ts`인가** (costMenuSurface.ts·costImportedGoods.ts와 같은 사정): 컴포넌트를 export
//   하는 `.tsx`에 non-component export를 하나라도 얹으면 `react-refresh/only-export-components`가
//   경고 1건을 낸다. 이 저장소의 CI는 프론트 lint를 **96 warnings 상한**으로 래칫해 뒀고
//   실측(2026-08-28) 기준 정확히 96/96이라 **여유가 0이다.** 순수 함수는 전부 여기 산다.
//
// ★그리고 여기는 **API 타입만** 들여온다(leaf). `CostPage.tsx`를 들여오면 순환이 된다 —
//   `costMenuSurface.ts`가 같은 이유로 `won()` 사본을 둔 그 자리다.
import type { CostMaterial, CostRecipe, CostTableCensusRow } from "./api";

/** 「없음」은 「0」이 아니다 — 이 문구의 **단일 원천**이다 (계약 §3 금지선).
 *
 * ★원래 자리는 `CostPage.tsx`의 빈 단가 이력(구 499줄)이었다. S2에서 왕복 표의
 * 「단가 없음」 행 툴팁이 **같은 말**을 해야 하는데, 문자열을 두 벌 두면 한쪽만 바뀐다
 * (이 저장소가 반복해 밟은 자리). 그래서 자리를 늘리되 원천은 하나로 둔다 —
 * **이동·재사용이지 삭제가 아니다.** */
export const EMPTY_IS_NOT_ZERO = "빈 칸이지 0원이 아니다";
/** 괄호까지 붙은 꼴 — 빈 단가 이력 뒤에 덧붙는 자리에서 쓴다. */
export const EMPTY_IS_NOT_ZERO_NOTE = `(${EMPTY_IS_NOT_ZERO})`;

/** 이 종에 **단가가 없나**.
 *
 * ★판정을 `MaterialList`(부자재 탭)와 **같은 칸**으로 한다 — 거기가 「단가 없음」이라는
 * 낱말을 화면에 처음 세운 자리이고(`latest_price_inc_vat === null`), 두 화면이 같은 종을
 * 두고 다르게 말하면 그 자체가 결함이다. S1(D-CPP-62) 이후 `inc`는 `ex`가 있으면 ×1.1로
 * 파생되므로 둘은 함께 비고 함께 찬다 — 그래도 **둘 다** 본다: 한쪽만 보는 판정이
 * 「값이 있는데 없다고 부르는」 S1의 그 결함이었다. */
export function hasNoUnitPrice(
  m: Pick<CostMaterial, "latest_price_ex_vat" | "latest_price_inc_vat">,
): boolean {
  return m.latest_price_inc_vat === null && m.latest_price_ex_vat === null;
}

/** anomaly 문자열 → **종류** 목록.
 *
 * DB 실측 모양: `price_conflict:부착 안내문:55.0≠30.0,price_conflict:비닐(16*23+4):15.0≠10.0`
 * (`cost_recipe.anomaly_flag` 40자 · `cost_table_item.anomalies` 200자 — **잘려 있을 수 있다**).
 * 그래서 콤마로 가르고 첫 콜론 앞만 취한다. 빈 조각·중복은 버린다.
 *
 * ★이 함수가 인박스의 「중복 접기」를 가능하게 한다 — 레시피 45·97의 `price_conflict`는
 * `cost_recipe.anomaly_flag`와 `cost_table_item.anomalies` **양쪽에** 기록돼 있어서,
 * 원천을 그대로 세면 **같은 사건이 두 줄에 선다**(설계 Q1 ⚠️). */
export function anomalyKinds(flag: string | null | undefined): string[] {
  if (!flag) return [];
  const out: string[] = [];
  for (const chunk of flag.split(",")) {
    const kind = chunk.split(":")[0]?.trim();
    if (kind && !out.includes(kind)) out.push(kind);
  }
  return out;
}

export type CostInboxGroupKey = "conflict" | "no-price" | "no-recipe" | "cost-table";

/** 인박스 한 줄이 «어디로» 가는가. `null`이면 **이 줄에서 갈 곳이 없다는 사실**이다
 *  (원가표 항목 중 아직 어느 레시피도 안 고른 것 — 화면이 레시피를 고르지 않는다). */
export type CostInboxTarget =
  | { kind: "material"; id: number }
  | { kind: "recipe"; id: number }
  | null;

export interface CostInboxRow {
  key: string;
  label: string;
  /** 「왜 이 줄이 여기 있나」. 사유가 틀리면 사람이 틀린 일을 한다(교훈 #349). */
  note: string | null;
  target: CostInboxTarget;
}

export interface CostInboxGroup {
  key: CostInboxGroupKey;
  title: string;
  /** 묶음 전체의 자백 — 이 숫자가 **어느 컬럼에서 왔나**. */
  source: string;
  /** 묶음 머리를 눌렀을 때 가는 곳. */
  goto: "table-no-price" | "recipes" | null;
  rows: CostInboxRow[];
}

/** 「할 일 인박스」 — 여섯 재고를 **넷**으로 접는다 (설계 Q1).
 *
 * ★묶음의 기준은 원천 테이블이 아니라 **「사람이 하는 일의 종류」**다: ①고른다(모순)
 * ②넣는다(단가) ③잇는다(구성) ④확인한다(픽·이상). 여섯 원천을 여섯 줄로 세우면 같은
 * 사건이 두 줄에 선다.
 *
 * ★**0건이어도 묶음을 지우지 않는다** — 「빈 인박스」와 「인박스가 안 뜸」이 같은 화면이
 * 되면 안 된다(계약 §3의 「없음」≠「0」과 같은 결). 그래서 이 함수는 **항상 4묶음**을 낸다. */
export function buildCostInbox(input: {
  materials: CostMaterial[];
  recipes: CostRecipe[];
  tableItems: CostTableCensusRow[];
}): CostInboxGroup[] {
  const { materials, recipes, tableItems } = input;

  const conflictRows: CostInboxRow[] = recipes
    .filter((r) => anomalyKinds(r.anomaly_flag).includes("price_conflict"))
    .map((r) => ({
      key: `conflict-${r.id}`,
      label: `레시피 ${r.id} 「${r.product_name}」${r.form_factor ? ` (${r.form_factor})` : ""}`,
      note: r.anomaly_flag,
      target: { kind: "recipe" as const, id: r.id },
    }));

  const noPriceRows: CostInboxRow[] = materials
    .filter(hasNoUnitPrice)
    .map((m) => ({
      key: `no-price-${m.id}`,
      label: `${m.name}${m.form_factor ? ` (${m.form_factor})` : ""}`,
      note: m.excel_ref_price ? `엑셀 참고값 ${m.excel_ref_price} — 단가가 아니라 대조값이다` : null,
      target: { kind: "material" as const, id: m.id },
    }));

  const noRecipeRows: CostInboxRow[] = recipes
    .filter((r) => {
      const kinds = anomalyKinds(r.anomaly_flag);
      return kinds.includes("no_recipe_match") || kinds.includes("needs_manual_lines");
    })
    .map((r) => ({
      key: `no-recipe-${r.id}`,
      label: `레시피 ${r.id} 「${r.product_name}」${r.form_factor ? ` (${r.form_factor})` : ""}`,
      note: r.anomaly_flag,
      target: { kind: "recipe" as const, id: r.id },
    }));

  // ★넷째 묶음의 분모 — **픽 안 된 항목 ∪ (price_conflict 아닌) 이상 항목**.
  //   ①합집합이라 한 항목이 두 줄에 서지 않는다(항목 = 사건 1건).
  //   ②`price_conflict`는 **첫 묶음에서 이미 셌으므로 여기서 뺀다** — 레시피 45·97의 그
  //     충돌이 `cost_table_item.anomalies`에도 같이 적혀 있기 때문이다(설계 Q1 ⚠️).
  //     단 그 항목이 «픽도 안 됐으면» 그건 별개의 할 일이라 남는다.
  const costTableRows: CostInboxRow[] = tableItems
    .filter(
      (it) =>
        !it.picked || anomalyKinds(it.anomalies).some((k) => k !== "price_conflict"),
    )
    .map((it) => ({
      key: `cost-table-${it.id}`,
      label: `${it.section} / ${it.item_name}${
        it.row_number === null ? "" : ` (엑셀 행 ${it.row_number})`
      }`,
      note: it.picked
        ? `⚠ ${it.anomalies ?? "이상 사유 미상"}`
        : it.anomalies
          ? `픽 안 됨 · ⚠ ${it.anomalies}`
          : "픽 안 됨 — 아직 어느 레시피도 이 항목을 안 골랐다",
      target: it.picked_by_recipe_id === null ? null : { kind: "recipe", id: it.picked_by_recipe_id },
    }));

  return [
    {
      key: "conflict",
      title: "모순 — 사람이 고른다",
      source: "레시피의 `price_conflict` — 픽이 가져온 원가표 값과 구성이 가리키는 종의 단가가 다르다. 화면은 어느 쪽도 추천하지 않는다.",
      goto: "recipes",
      rows: conflictRows,
    },
    {
      key: "no-price",
      title: `단가 없음 — ${EMPTY_IS_NOT_ZERO}`,
      source: "부자재 종 중 최신 단가가 없는 것. 아래 왕복 표에서 「단가 없음만」으로 좁혀 볼 수 있다.",
      goto: "table-no-price",
      rows: noPriceRows,
    },
    {
      key: "no-recipe",
      title: "구성 없음 — 원가표와 못 이었다",
      source: "레시피의 `no_recipe_match` · `needs_manual_lines`. 레시피 상세의 「원가표 항목 고르기」가 잇는 자리다.",
      goto: "recipes",
      rows: noRecipeRows,
    },
    {
      key: "cost-table",
      title: "원가표 항목 — 픽·이상 확인 대기",
      source: "아직 아무 레시피도 안 고른 항목 + 이상이 붙은 항목. 모순(첫 묶음)으로 이미 센 건은 여기서 뺐다 — 같은 사건을 두 번 세지 않는다.",
      goto: "recipes",
      rows: costTableRows,
    },
  ];
}

// ══════════════════════════════════════════════════════════════════
// 왕복 표 — 행 = `cost_material.id`, 열 12 (설계 Q3)
//
// ★**이 표가 곧 S3 다운로드 파일의 모양이다.** 그래서 열마다 «파일에서 고칠 수 있나»를
//   화면이 미리 말한다 — 사람이 「무엇을 고쳐 올릴 수 있는지」를 표에서 읽지 못하면
//   S3 파일을 받아 봐야 알게 되고, 그때는 이미 고친 뒤다.
// ══════════════════════════════════════════════════════════════════

export interface RoundTripColumn {
  key: string;
  label: string;
  /** 다운로드 파일에서 **고쳐 올릴 수 있는가**(S3·S4). 표 자체는 S2에서 읽기 전용이다. */
  editable: boolean;
  /** 왜 못 고치나 / 고치면 어떻게 되나. */
  note: string;
}

/** 열 12개 — 이 목록이 S3 파일의 헤더가 된다(설계 Q3). */
export const ROUND_TRIP_COLUMNS: RoundTripColumn[] = [
  { key: "id", label: "ID", editable: false, note: "행 키다. 파일에서 비면 「신규 종 후보」, 겹치면 「모순」으로 선다." },
  { key: "name", label: "이름", editable: true, note: "개명은 같은 행의 값 변경 1건이다 — 「사라짐+신규」가 아니다." },
  { key: "form_factor", label: "폼팩터", editable: true, note: "없으면 화면은 「— (폼팩터 없음)」, 파일은 빈 칸이다." },
  { key: "part", label: "부품", editable: true, note: "없으면 화면은 「(부품 미지정)」, 파일은 빈 칸이다." },
  { key: "unit", label: "단위", editable: true, note: "" },
  {
    key: "price_ex",
    label: "단가 (VAT 제외)",
    editable: true,
    note: "고치면 덮어쓰지 않고 새 `source=manual` 단가 행으로 쌓인다. 원장 정본 종은 예외 — 「반영 불가」로 선다.",
  },
  { key: "price_inc", label: "단가 (VAT 포함)", editable: false, note: "표시 전용. ex·inc를 둘 다 고칠 수 있으면 한 행에서 서로 모순되게 고쳐지고, 그 심판 규칙이 곧 자동 병합이다." },
  { key: "vat_derived", label: "VAT 파생", editable: false, note: "`×1.1`이면 우리가 만든 값이지 실제로 낸 세액이 아니다." },
  { key: "price_source", label: "단가 출처", editable: false, note: "빈 칸이면 단가가 없다는 뜻이다." },
  { key: "effective_date", label: "단가 발효일", editable: true, note: "단가 열과 짝이다. 비우면 확인 화면이 업로드 날짜를 «제안»으로 보이고 사람이 확인한다." },
  { key: "excel_ref", label: "엑셀 참고값", editable: false, note: "미러다 — 갱신 주체는 업로드의 참고값 리포트 경로다." },
  { key: "status_note", label: "상태 / 비고", editable: true, note: "상태는 못 고친다(승인은 사람이 화면에서 누른다). 비고만 고칠 수 있다." },
];

/** 오른쪽 정렬(`tabular-nums`)로 세우는 열 — **숫자 열만**이다.
 *
 * ★왜 목록으로 두나: 정렬은 «보기 좋으라고»가 아니라 **자릿수를 세로로 맞춰 눈이 크기를
 * 비교하게** 하는 장치다. 왼쪽 정렬된 숫자 기둥은 `1,600`과 `180`의 크기 차이를 안 보여 준다.
 * 참조 화면(`Rocket1PFunnel.tsx`)이 쓰는 규격과 같다 — 두 화면이 숫자를 다르게 세우면
 * 같은 저장소의 표가 서로 다른 관례를 말하게 된다. */
export const ROUND_TRIP_NUMERIC: ReadonlySet<string> = new Set([
  "price_ex",
  "price_inc",
  "excel_ref",
]);

/** 받은 Blob을 브라우저가 **실제로 저장하게** 한다 (계약 D-CPP-62 S3).
 *
 * ★이 함수가 없으면 다운로드는 「서버가 파일을 만들었다」에서 끝나고 **사람 손에는 아무것도
 * 안 남는다.** 이 저장소가 반복해 밟은 「값을 만드는 층은 맞는데 사람에게 닿는 층이 끊긴」
 * 결함의 자리라, 인라인으로 묻지 않고 별도 함수로 세워 테스트가 이 경로를 직접 잡는다.
 *
 * ★`revokeObjectURL`을 반드시 부른다 — 안 부르면 받을 때마다 Blob이 탭 수명 동안 메모리에
 * 남는다. 139행 파일이라 한 번은 작지만, **여러 번 받는 것이 이 화면의 정상 사용**이다. */
export function saveBlobAsFile(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** `CostPage.tsx`의 `PickerItem`과 같은 모양(구조적으로 호환). 순환을 피하려고 여기 둔다. */
export interface CostPickerItem {
  value: string;
  label: string;
  count?: number;
}

/** 폼팩터 드롭다운 항목 — **부자재 탭과 홈 왕복 표가 같은 함수를 쓴다.**
 *
 * ★두 화면이 각자 세면 「부자재 탭엔 tablet 27인데 홈엔 26」 같은 어긋남이 생기고, 그때
 * 어느 쪽이 참인지 화면이 못 말한다. 빈 폼팩터는 감추지 않고 **자기 이름을 가진 선택지**로
 * 세운다(조용한 0 금지). */
export function materialFormItems(
  materials: Pick<CostMaterial, "form_factor">[],
): CostPickerItem[] {
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
}

/** 고른 폼팩터 안의 부품 항목. 라벨에 건수를 **박는다** — 이 셀렉트는 `count`를 따로 안
 *  그리므로, 안 박으면 「부품 미지정이 절대다수」라는 사실이 화면에서 사라진다(prod 83/139). */
export function materialPartItems(
  materials: Pick<CostMaterial, "form_factor" | "part">[],
  form: string | null,
): CostPickerItem[] {
  if (!form) return [];
  const counts = new Map<string, number>();
  for (const m of materials) {
    if ((m.form_factor ?? "__none__") !== form) continue;
    const key = m.part && m.part.trim() ? m.part : "__none__";
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return Array.from(counts, ([value, count]) => ({
    value,
    label: value === "__none__" ? `(부품 미지정) (${count})` : `${value} (${count})`,
    count,
  })).sort((a, b) => a.label.localeCompare(b.label, "ko"));
}

export interface RoundTripFilter {
  form: string | null;
  part: string | null;
  noPriceOnly: boolean;
  conflictOnly: boolean;
}

export const ROUND_TRIP_FILTER_NONE: RoundTripFilter = {
  form: null,
  part: null,
  noPriceOnly: false,
  conflictOnly: false,
};

/** 왕복 표의 행 필터. `__none__`은 「값이 없음」이라는 **자기 이름을 가진 선택지**다
 *  (부자재 탭 드롭다운과 같은 규약 — 조용한 0 금지). */
export function filterRoundTripRows(
  materials: CostMaterial[],
  f: RoundTripFilter,
): CostMaterial[] {
  return materials.filter((m) => {
    if (f.form && (m.form_factor ?? "__none__") !== f.form) return false;
    if (f.part) {
      const key = m.part && m.part.trim() ? m.part : "__none__";
      if (key !== f.part) return false;
    }
    if (f.noPriceOnly && !hasNoUnitPrice(m)) return false;
    if (f.conflictOnly && !m.price_conflict) return false;
    return true;
  });
}

/** 「139건 중 N건 표시」 — 필터가 만든 0건과 «원래 없음»을 가른다. */
export function roundTripCountText(shown: number, total: number): string {
  if (total === 0) return "부자재 종이 0건이다 — 원가 정본을 아직 안 올렸다.";
  return `${total}건 중 ${shown}건 표시`;
}

export interface RoundTripBadge {
  key: string;
  label: string;
  title: string;
}

/** 행 배지 — 부자재 첫 화면의 **행 단위 장문**이 여기서는 짧은 배지가 되고, 원문은
 *  행을 눌러 드릴다운(부자재 탭)에서 그대로 읽힌다(설계 Q4 T2 — 이동이지 삭제가 아니다). */
export function roundTripBadges(m: CostMaterial): RoundTripBadge[] {
  const out: RoundTripBadge[] = [];
  if (hasNoUnitPrice(m)) {
    out.push({
      key: "no-price",
      label: "▢단가없음",
      // ★「없음」과 「0」을 가르는 그 문구가 여기서 다시 일한다(단일 원천 재사용).
      title: `단가 없음 ${EMPTY_IS_NOT_ZERO_NOTE} — 행을 누르면 부자재 상세에서 넣는 길이 나온다`,
    });
  }
  if (m.price_conflict) {
    out.push({
      key: "conflict",
      label: "⚠모순",
      title: "채택은 원장 값인데 더 늦은 수동 입력이 있다 — 어느 쪽도 화면이 고르지 않는다",
    });
  }
  if (m.latest_price_source === "ledger") {
    out.push({
      key: "ledger",
      label: "원장정본",
      title: "이 종의 정본은 원장이다 — 파일에서 이 칸을 고치면 「반영 불가」 묶음에 선다(S4)",
    });
  }
  if (m.latest_price_inc_derived) {
    out.push({
      key: "derived",
      label: "×1.1 파생",
      title: "부가세 제외 값만 입력돼 있어 ×1.1로 만든 값이다 — 실제로 낸 세액이 아니다",
    });
  }
  return out;
}
