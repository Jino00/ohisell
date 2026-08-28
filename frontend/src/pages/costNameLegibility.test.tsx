// @vitest-environment jsdom
// ══════════════════════════════════════════════════════════════════════════════
// 원가 화면 — 상품명이 «읽히는가» (2026-08-28)
//
// 발단: Jino가 보드 탭 스크린샷을 보내며 «sellC에서 글자가 이렇게 잘리네». 상품명이
// `오하이 빛반사, 지문방지 매트 필름 3매, 갤럭시Z플립7FE (외부액정3…`에서 끊겨 있었다.
//
// ★이 파일이 지키는 것은 «값이 맞는가»가 아니라 «사람이 읽을 수 있는가»다. 보드 표의
//   상품명 칸은 그동안 `truncate max-w-[22rem]`로 잘리는 데다 `title`조차 없어서
//   **전문을 볼 길이 화면에 하나도 없었다** — 그런데 기존 보드 테스트는 전부 초록이었다.
//   `getByText`·`textContent`는 잘린 뒤에도 원문을 그대로 돌려주기 때문이다(잘림은 CSS가
//   하고 jsdom은 레이아웃을 안 돈다). 즉 «만드는 층»은 지켜지고 «닿는 층»만 비어 있었다.
//
// ★그래서 여기서는 **클래스 문자열과 `title` 속성을 직접 검사한다.** jsdom에서 실제 폭을
//   잴 수 없으니 이게 이 결함을 잡을 수 있는 유일한 자리다 — 우아하지 않다는 것을 알고
//   쓴다. 이 파일이 사살하는 변이:
//     M1  보드 상품명 칸에 `truncate`를 되돌린다
//     M2  보드 상품명 칸의 `title`을 지운다
//     M3  숫자 칸의 `whitespace-nowrap`을 지운다 (상품명 캡을 걷은 대가로 이번엔 값이
//         접힌다 — `2,649.7원`이 두 줄로 갈라지던 그 병. Jino 2026-08-28 11:09
//         «날짜가 2줄이 되지 않도록»)
//     M4  머리 `<th>`의 nowrap을 지운다 · M5 `RecipeList`의 `title`을 지운다
//     M6  보드 탭의 페이지 폭 상한 해제를 되돌린다 ★적대 리뷰에서 **살아남았던** 변이
//     M7  상품명 텍스트 렌더 자체를 지운다(`title`만 남긴다) — 「닿는 층」 변이
// ══════════════════════════════════════════════════════════════════════════════
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

import { RecipeList, StandardCostBoard } from "./CostPage";
// ★`costPageWidthClass`는 `CostPage.tsx`가 아니라 `lib/costMenuSurface.ts`에 산다 —
//   그 파일 헤더가 적어 둔 이유 그대로다: 컴포넌트도 export하는 .tsx에 순수 export를 하나
//   더 얹으면 `react-refresh/only-export-components` 경고가 1건 늘고 CI의 warning 래칫이
//   빨간불이 된다(2026-08-28 실측 — 96→97로 실제로 터졌고, 그래서 여기로 옮겼다).
import { costPageWidthClass } from "../lib/costMenuSurface";
import type { CostBoard, CostBoardRow, CostRecipe } from "../lib/api";

/** 보드 상품명 칸의 testid — `recipe_id`까지 넣는 이유는 아래 「중복 SKU」 테스트가 말한다. */
const BOARD_NAME_TESTID = "board-name-45-OHI-0442";

// ★자동 정리가 없다(전역 setup 파일이 없는 설정) — 안 지우면 앞 테스트의 표가 DOM에 남아
//   `getByTestId`가 「여러 개 찾음」으로 죽고, 머리 9개를 세는 검사가 45개를 센다.
afterEach(cleanup);

/** Jino 스크린샷에서 실제로 잘려 있던 이름(OHI-0442 행). */
const LONG_NAME =
  "오하이 빛반사, 지문방지 매트 필름 3매, 갤럭시Z플립7FE (외부액정3매+내부액정3매)";

function boardRow(over: Partial<CostBoardRow> = {}): CostBoardRow {
  return {
    internal_sku: "OHI-0442",
    product_name: LONG_NAME,
    recipe_id: 45,
    recipe_product_name: LONG_NAME,
    form_factor: "flip",
    form_source: "rule",
    recipe_kind: "assembly",
    recipe_status: "approved",
    link_status: "linked",
    std_cost_ex_vat: "3406.00",
    std_cost_inc_vat: "3746.40",
    current_cost_price: "4483.00",
    gap_pct: -16.43,
    excel_total_inc_vat: "3712.50",
    excel_gap_pct: 0.91,
    reason: null,
    ...over,
  };
}

function board(items: CostBoardRow[]): CostBoard {
  return {
    items,
    sku_count: items.length,
    computed_count: items.filter((r) => r.std_cost_inc_vat !== null).length,
    uncomputed_count: items.filter((r) => r.std_cost_inc_vat === null).length,
    recipe_count: items.length,
    approved_recipe_count: items.length,
  };
}

describe("★보드 표 — 상품명이 잘리지 않는다 (Jino «글자가 이렇게 잘리네», 2026-08-28)", () => {
  it("M1 — 상품명 칸에 `truncate`도 `max-w` 캡도 없다", () => {
    render(<StandardCostBoard board={board([boardRow()])} />);
    const cell = screen.getByTestId(BOARD_NAME_TESTID);
    const cls = cell.className;
    // `truncate` = overflow-hidden + text-ellipsis + whitespace-nowrap. 하나라도 살아 있으면
    // 이름이 다시 잘린다 — 개별 유틸리티로 되돌리는 우회까지 함께 막는다.
    expect(cls).not.toMatch(/\btruncate\b/);
    expect(cls).not.toMatch(/\btext-ellipsis\b/);
    expect(cls).not.toMatch(/\boverflow-hidden\b/);
    expect(cls).not.toMatch(/\bwhitespace-nowrap\b/);
    // 폭 캡이 있으면 그 너머는 어차피 잘리거나 접힌다.
    expect(cls).not.toMatch(/\bmax-w-/);
    // 대신 줄바꿈은 허용돼야 한다 — 창이 좁을 때 옆 열을 밀어내지 않는 유일한 길이다.
    expect(cls).toMatch(/\bbreak-words\b/);
  });

  it("M2 — 상품명 칸이 전문을 `title`로 들고 있다 (줄바꿈이 나도 원문 한 줄은 남는다)", () => {
    render(<StandardCostBoard board={board([boardRow()])} />);
    const cell = screen.getByTestId(BOARD_NAME_TESTID);
    expect(cell.getAttribute("title")).toBe(LONG_NAME);
    // 화면 텍스트 자체도 잘린 형태가 아니다 — 말줄임표가 본문에 섞여 들어오면 안 된다.
    expect(cell.textContent).toContain(LONG_NAME);
    expect(cell.textContent).not.toContain("…");
  });

  it("★product_name이 null이면 recipe_product_name으로 폴백하고 title도 그 값이다", () => {
    render(<StandardCostBoard board={board([boardRow({ product_name: null })])} />);
    const cell = screen.getByTestId(BOARD_NAME_TESTID);
    expect(cell.getAttribute("title")).toBe(LONG_NAME);
    expect(cell.textContent).toContain(LONG_NAME);
  });

  it("M3 — 숫자·SKU·폼팩터 칸은 `whitespace-nowrap`을 유지한다 (값이 두 줄로 갈라지지 않는다)", () => {
    render(<StandardCostBoard board={board([boardRow()])} />);
    const [row] = screen.getAllByRole("row").slice(1);

    // 이름이 넓어진 만큼 남는 폭 경쟁이 열렸다 — 접혀도 되는 것은 문장(상품명·비고)이지
    // 값이 아니다. 두 대조 칸은 testid가 있고, 나머지는 위치로 짚는다.
    for (const id of ["board-excel-standard", "board-excel-gap"]) {
      expect(within(row).getByTestId(id).className).toMatch(/\bwhitespace-nowrap\b/);
    }
    const cells = Array.from(row.querySelectorAll("td"));
    // 0=SKU · 2=폼팩터 · 3=표준원가 · 6=현 cost_price · 7=격차 (1=상품명, 8=비고는 제외)
    for (const idx of [0, 2, 3, 6, 7]) {
      expect(cells[idx].className).toMatch(/\bwhitespace-nowrap\b/);
    }
    // 그리고 상품명 칸(1)은 그 반대여야 한다 — 위 M1과 짝이다.
    expect(cells[1].className).not.toMatch(/\bwhitespace-nowrap\b/);
  });

  it("★머리 9열이 전부 nowrap이다 — auto layout에서 열 폭을 잡는 것은 머리다", () => {
    render(<StandardCostBoard board={board([boardRow()])} />);
    const headers = screen.getAllByRole("columnheader");
    expect(headers).toHaveLength(9);
    for (const th of headers) {
      expect(th.className).toMatch(/\bwhitespace-nowrap\b/);
    }
  });

  // ★적대 리뷰 P2-1 — 같은 `internal_sku`가 **두 레시피**에 링크될 수 있다
  //   (`CostRecipeLink`에 (sku, recipe) 유니크 제약이 없다. `<tr key>`가 이미
  //   `${recipe_id}-${internal_sku}` 조합을 쓰는 것이 그 자백이다). testid를 SKU만으로
  //   두면 그때 `getByTestId`가 「여러 개 찾음」으로 죽는다 — **화면은 멀쩡한데 테스트만
  //   조용히 못 쓰게 되는** 자리다. 이런 결함은 결함이 났을 때가 아니라 결함을 잡으러
  //   갔을 때 드러나므로 여기서 미리 고정한다.
  it("★같은 SKU가 두 레시피에 서도 상품명 칸을 각각 유일하게 짚을 수 있다", () => {
    render(
      <StandardCostBoard
        board={board([
          boardRow({ recipe_id: 45 }),
          boardRow({ recipe_id: 97, product_name: `${LONG_NAME} (두 번째 레시피)` }),
        ])}
      />,
    );
    expect(screen.getByTestId("board-name-45-OHI-0442").getAttribute("title")).toBe(LONG_NAME);
    expect(screen.getByTestId("board-name-97-OHI-0442").getAttribute("title")).toBe(
      `${LONG_NAME} (두 번째 레시피)`,
    );
  });
});

// ── 페이지 폭 상한 — 잘림의 «나머지 절반» ──────────────────────────────────────
// ★적대 리뷰 변이 M6이 **살아남은** 자리다: 보드 탭의 `max-w-[96rem]` 해제를 되돌려도
//   프론트 1,173건이 전건 초록이었다. 최상위 wrapper의 className은 `<CostPage>`를 통째로
//   렌더해야 닿아서 어느 테스트도 보지 않았기 때문이다. 판정을 순수 함수로 뽑아 잡는다.
describe("★페이지 폭 상한 — 넓은 표(home·board)만 상한을 푼다", () => {
  it("board·home은 폭 상한이 없다 (오른쪽을 비워 둔 채 이름을 자르던 자리)", () => {
    expect(costPageWidthClass("board")).not.toMatch(/\bmax-w-/);
    expect(costPageWidthClass("home")).not.toMatch(/\bmax-w-/);
  });

  it("materials·recipes는 상한을 유지한다 — 폼·목록이라 상한이 가독성을 돕는다", () => {
    expect(costPageWidthClass("materials")).toMatch(/\bmax-w-\[96rem\]/);
    expect(costPageWidthClass("recipes")).toMatch(/\bmax-w-\[96rem\]/);
  });
});

// ── 레시피 목록 — 같은 결함의 다른 자리 ────────────────────────────────────────
// 이쪽은 좌우 2단(이름 ↔ 폼팩터)이라 캡을 걷으면 폼팩터가 밀린다. 그래서 처방이 갈린다:
// 잘림은 남기고 **툴팁만** 준다. 「고쳤다」가 두 자리에서 다른 뜻이라는 것을 여기 적어 둔다.
const RECIPE: CostRecipe = {
  id: 45,
  product_name: LONG_NAME,
  form_factor: "flip",
  status: "approved",
  source: "excel",
  recipe_kind: "assembly",
  form_source: "rule",
  anomaly_flag: null,
  approved_at: "2026-08-28T10:00:00+09:00",
  match: null,
  line_count: 3,
  link_count: 8,
  standard: {
    computable: true,
    std_cost_ex_vat: "3406.00",
    std_cost_inc_vat: "3746.40",
    reason: null,
    unresolved: [],
    partial_ex_vat: null,
    partial_inc_vat: null,
    line_count: 3,
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
};

describe("★레시피 목록 — 잘리더라도 전문을 볼 길은 있다", () => {
  it("이름 span에 `title`이 붙어 있다", () => {
    render(<RecipeList recipes={[RECIPE]} selectedId={null} onSelect={() => {}} />);
    const named = screen.getByTitle(LONG_NAME);
    expect(named.textContent).toBe(LONG_NAME);
  });
});
