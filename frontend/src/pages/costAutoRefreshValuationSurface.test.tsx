// @vitest-environment jsdom
//
// costAutoRefreshValuationSurface.test.tsx — D-CPP-60 계약 §4의 새 표면(원가 화면)이
// **실제로 사람에게 닿는가**.
//
// 이 파일이 재는 것 셋:
//  ① 순수 함수 넷(`standardPriceRuleText`·`lotSpanText`·`priceConflictText`·
//     `sweepSummaryText`, 전부 `../lib/costMenuSurface`) — 값이 사람 말이 되는 규칙.
//  ② `ValuationBadge`·`AutoRefreshPanel`(`./CostPage`) 직접 렌더 — 컴포넌트 내부 분기.
//  ③ `App`을 `/cost`에서 통째로 렌더 — 배선(호출부) 자체가 살아 있는가
//     (`costPageReachesTheUser.test.tsx` 머리말의 교훈: 단위 테스트는 «함수가 값을
//     만드나»를 묻지 «사람이 그걸 보나»를 못 묻는다. 넷 다 컴포넌트 «바깥»을 끊는
//     변이였다).
//
// 표면 절단 변이(위임문 지정, 넷 다 KILLED — 회신 참조):
//   SUR-F1 평가방법 카드에서 두 번째 줄(`standardPriceRuleText`) 렌더 제거
//   SUR-F2 `AutoRefreshPanel` **호출부** 제거(컴포넌트는 살아 있고 부르는 줄만 지운다)
//   SUR-F3 `sweepSummaryText`가 회전 0건일 때도 「갱신 0건」이라고 말하게 되돌리기
//   SUR-F4 「연결 대기」 큐의 `message`(사유) 렌더 제거
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import {
  formatKstDateTime,
  lotSpanText,
  priceConflictText,
  standardPriceRuleText,
  sweepSummaryText,
  triggerLabel,
} from "../lib/costMenuSurface";
import type {
  CostAutoRefreshEntry,
  CostAutoRefreshRun,
  CostMaterial,
  CostSetting,
  CostSettingHistoryRow,
} from "../lib/api";
import { AutoRefreshPanel, ValuationBadge } from "./CostPage";

afterEach(cleanup);

// ════════════════════════════════════════════════════════════════════
// ① 순수 함수 — 값이 사람 말이 되는 규칙 (SUR-F3의 방어선도 여기다)
// ════════════════════════════════════════════════════════════════════

describe("standardPriceRuleText — 층1이 «지금 계산에 쓰는» 규칙(계약 §4-①)", () => {
  it("★설정이 있으면 규칙 이름을 사람 말로 바꾼다 — 「FIFO」·「선입선출」이라 부르지 않는다(§3 금지선)", () => {
    const settings: CostSetting[] = [
      { key: "standard_price_rule", value: "latest", confirmed: true, note: null, updated_at: null },
    ];
    const t = standardPriceRuleText(settings)!;
    expect(t).toContain("최신 로트 단가");
    expect(t).toContain("재고 원장(C1) 가동 전");
    expect(t).not.toMatch(/FIFO/i);
    expect(t).not.toContain("선입선출");
  });

  it("설정 자체가 없으면 침묵하지 않고 「설정 없음」이라 한다", () => {
    expect(standardPriceRuleText([])).toContain("설정 없음");
  });
});

describe("lotSpanText — 관측 로트 구간 자백(계약 §4-⑥)", () => {
  it("★폭이 있으면 «최신 단가 · 구간 · 가동 전» 한 줄을 정확히 만든다(위임문 예시와 byte-identical)", () => {
    const m = {
      latest_price_ex_vat: "190.82",
      lot_price_min: "178.78",
      lot_price_max: "190.82",
      lot_price_has_span: true,
    };
    expect(lotSpanText(m)).toBe(
      "최신 로트 단가 190.82원 · 관측 로트 구간 178.78~190.82원 · 재고 원장 가동 전",
    );
  });

  it("★폭이 없으면(로트 1건) `null`이다 — 구간을 지어내지 않는다", () => {
    const m = {
      latest_price_ex_vat: "100",
      lot_price_min: "100",
      lot_price_max: "100",
      lot_price_has_span: false,
    };
    expect(lotSpanText(m)).toBeNull();
  });
});

describe("priceConflictText — 채택은 원장인데 더 늦은 수동 입력이 있다(§2-5 자백)", () => {
  it("어긋남이 있으면 «수동 입력은 채택되지 않았다»까지 말한다", () => {
    expect(priceConflictText({ price_conflict: true })).toBe(
      "채택: 원장 값 — 더 늦은 수동 입력이 있다(수동 입력은 채택되지 않았다)",
    );
  });

  it("어긋남이 없으면 `null`이다", () => {
    expect(priceConflictText({ price_conflict: false })).toBeNull();
  });
});

describe("sweepSummaryText — 「갱신 0건」과 「한 번도 안 돎」을 반드시 구별한다", () => {
  it("★SUR-F3의 방어선 — 회전이 0건이면 «한 번도 안 돌았다»이지 «갱신 0건»이 아니다", () => {
    const t = sweepSummaryText([]);
    expect(t).toContain("아직 한 번도 안 돌았다");
    expect(t).toContain("지금 검사");
    // ★이 규칙이 이 저장소가 반복 실측한 fail-open이다 — 「갱신 0건」이라고 쓰면
    //   「돌았는데 바뀔 게 없었다」와 「죽어서 한 번도 안 돌았다」가 같은 화면이 된다.
    expect(t).not.toContain("갱신 0건");
  });

  it("★회전이 있으면 최신 1건(맨 앞) 기준으로 요약한다 — 위임문 예시와 byte-identical", () => {
    const runs: CostAutoRefreshRun[] = [
      {
        id: 9,
        trigger: "cron",
        started_at: "2026-08-26T00:40:00", // naive UTC(서버) → KST 09:40
        finished_at: "2026-08-26T00:40:05",
        checked: 12,
        updated: 1,
        failed: 0,
        queued: 2,
        note: null,
        entries: [],
      },
    ];
    expect(sweepSummaryText(runs)).toBe(
      "최근 검사: 2026-08-26 09:40 (일일 sweep) · 검사 12종 · 갱신 1건 · 실패 0건 · 대기 2건",
    );
  });
});

describe("triggerLabel / formatKstDateTime — 보조 표시 규칙", () => {
  it("트리거 코드를 사람 말로 바꾼다", () => {
    expect(triggerLabel("cron")).toBe("일일 sweep");
    expect(triggerLabel("manual")).toBe("지금 검사");
    expect(triggerLabel("event")).toBe("로트 확정 직후");
  });

  it("★naive datetime(서버 로컬 TZ=UTC)을 KST로 환산한다 — 그대로 렌더하면 9시간이 어긋난다", () => {
    expect(formatKstDateTime("2026-08-26T00:40:00")).toBe("2026-08-26 09:40");
    expect(formatKstDateTime(null)).toBe("—");
  });
});

// ════════════════════════════════════════════════════════════════════
// ② 컴포넌트 직접 렌더 — props만 본다(이 파일이 이미 순수 컴포넌트로 export한 것들)
// ════════════════════════════════════════════════════════════════════

const RUN_EMPTY_QUEUE: CostAutoRefreshEntry[] = [];

describe("AutoRefreshPanel — 회전 0건 / 대기 0건은 «없다»고 명시한다", () => {
  it("회전 이력·연결 대기가 둘 다 비면 각각 이유가 다른 문구로 뜬다", () => {
    render(
      <AutoRefreshPanel runs={[]} queue={RUN_EMPTY_QUEUE} onRunNow={() => {}} />,
    );
    expect(screen.getByTestId("auto-refresh-summary").textContent).toContain(
      "한 번도 안 돌았다",
    );
    expect(screen.getByTestId("auto-refresh-runs-empty")).toBeTruthy();
    expect(screen.getByTestId("auto-refresh-queue-empty")).toBeTruthy();
  });

  it("★실패 항목은 사유(`message`)를 반드시 보여준다 — 사유 없는 실패는 침묵과 같다", () => {
    const run: CostAutoRefreshRun = {
      id: 1,
      trigger: "manual",
      started_at: "2026-08-26T00:00:00",
      finished_at: "2026-08-26T00:00:01",
      checked: 1,
      updated: 0,
      failed: 1,
      queued: 0,
      note: null,
      entries: [
        {
          id: 101,
          run_id: 1,
          outcome: "failed",
          material_id: 5,
          material_name: "cleaning kit",
          price_id: null,
          import_invoice_line_id: 7,
          hbl_no: "SETR1",
          item_name: "cleaning kits",
          old_price_ex_vat: null,
          new_price_ex_vat: null,
          message: "IntegrityError: 원장 라인이 이미 다른 종에 연결돼 있다",
          created_at: "2026-08-26T00:00:01",
        },
      ],
    };
    render(<AutoRefreshPanel runs={[run]} queue={[]} onRunNow={() => {}} />);
    expect(
      screen.getByText(/IntegrityError: 원장 라인이 이미 다른 종에 연결돼 있다/),
    ).toBeTruthy();
  });

  it("큐 항목에 `material_id`가 있으면 「부자재 연결 화면으로 이동」을 누를 수 있다", () => {
    const onGo = vi.fn();
    const entry: CostAutoRefreshEntry = {
      id: 501,
      run_id: 1,
      outcome: "queued",
      material_id: 5,
      material_name: null,
      price_id: null,
      import_invoice_line_id: 7,
      hbl_no: "SETR1",
      item_name: "cleaning kits",
      old_price_ex_vat: null,
      new_price_ex_vat: "12.34",
      message: "3회 연속 실패 — 큐에 고정",
      created_at: "2026-08-26T00:00:00",
    };
    render(
      <AutoRefreshPanel runs={[]} queue={[entry]} onRunNow={() => {}} onGoToMaterial={onGo} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /부자재 연결 화면으로 이동/ }));
    expect(onGo).toHaveBeenCalledWith(5);
  });

  it("★큐 항목에 `material_id`가 없으면(첫 연결) 이동 버튼을 안 그린다 — 자동 연결 버튼을 만들지 않는다(§7-4)", () => {
    const onGo = vi.fn();
    const entry: CostAutoRefreshEntry = {
      id: 502,
      run_id: 1,
      outcome: "queued",
      material_id: null,
      material_name: null,
      price_id: null,
      import_invoice_line_id: 8,
      hbl_no: "SETR2",
      item_name: "새 원단 X",
      old_price_ex_vat: null,
      new_price_ex_vat: "1.00",
      message: "「새 원단 X」은 아직 어느 부자재에도 연결된 적이 없다 — 첫 연결은 사람이 한다(계약 §7-4).",
      created_at: "2026-08-26T00:00:00",
    };
    render(
      <AutoRefreshPanel runs={[]} queue={[entry]} onRunNow={() => {}} onGoToMaterial={onGo} />,
    );
    expect(screen.queryByRole("button", { name: /부자재 연결 화면으로 이동/ })).toBeNull();
    expect(screen.getByText(/첫 연결은 사람이 한다/)).toBeTruthy();
  });
});

describe("ValuationBadge — 확인·변경 UI + 이력(합격 ②)", () => {
  const SETTINGS: CostSetting[] = [
    { key: "valuation_method", value: "fifo", confirmed: false, note: null, updated_at: null },
    { key: "standard_price_rule", value: "latest", confirmed: true, note: null, updated_at: null },
  ];

  it("`onReconfirm`이 없으면(다른 헤더 자리) 버튼·이력을 안 그린다 — 기존 동작 불변", () => {
    render(<ValuationBadge settings={SETTINGS} />);
    expect(screen.queryByRole("button", { name: /선입선출 재확인/ })).toBeNull();
  });

  it("`onReconfirm`이 있으면 두 줄 + 버튼이 함께 뜬다(계약 §4-① «함께 보여야 한다»)", () => {
    render(<ValuationBadge settings={SETTINGS} onReconfirm={() => {}} />);
    expect(screen.getByText(/신고 내역 미확인/)).toBeTruthy();
    expect(screen.getByTestId("standard-price-rule-line")).toBeTruthy();
    expect(screen.getByRole("button", { name: /선입선출 재확인/ })).toBeTruthy();
  });

  it("이력이 0건이면 「아직 확인·변경 기록 없음」이라고 명시한다(§2-6 침묵 금지)", () => {
    render(<ValuationBadge settings={SETTINGS} history={[]} onReconfirm={() => {}} />);
    fireEvent.click(screen.getByText(/확인·변경 이력/));
    expect(screen.getByTestId("valuation-history-empty")).toBeTruthy();
  });

  it("★값이 안 바뀌어도 이력 1건은 화면에 남는다(합격 ②)", () => {
    const history: CostSettingHistoryRow[] = [
      {
        id: 1,
        key: "valuation_method",
        old_value: "fifo",
        new_value: "fifo",
        old_confirmed: false,
        new_confirmed: false,
        actor: "jino",
        note: "홈택스 재확인 — 여전히 무신고",
        created_at: "2026-08-26T00:00:00",
      },
    ];
    render(<ValuationBadge settings={SETTINGS} history={history} onReconfirm={() => {}} />);
    fireEvent.click(screen.getByText(/확인·변경 이력/));
    expect(screen.getByText(/값은 그대로\(fifo\)/)).toBeTruthy();
    expect(screen.getByText(/홈택스 재확인 — 여전히 무신고/)).toBeTruthy();
  });

  it("버튼을 누르면 메모 프롬프트를 받아 `onReconfirm`을 부른다 — 취소는 아무 일도 안 한다", () => {
    const onReconfirm = vi.fn();
    render(<ValuationBadge settings={SETTINGS} history={[]} onReconfirm={onReconfirm} />);
    const promptSpy = vi.spyOn(window, "prompt");

    promptSpy.mockReturnValueOnce(null); // 취소
    fireEvent.click(screen.getByRole("button", { name: /선입선출 재확인/ }));
    expect(onReconfirm).not.toHaveBeenCalled();

    promptSpy.mockReturnValueOnce("  "); // 빈 메모로 진행
    fireEvent.click(screen.getByRole("button", { name: /선입선출 재확인/ }));
    expect(onReconfirm).toHaveBeenCalledWith(null, false);

    promptSpy.mockReturnValueOnce("현장 확인함");
    fireEvent.click(screen.getByRole("button", { name: /선입선출 재확인/ }));
    expect(onReconfirm).toHaveBeenCalledWith("현장 확인함", false);

    promptSpy.mockRestore();
  });

  // ══════════════════════════════════════════════════════════════════
  // ★SUR-F5 — 「확인됨」으로 «바꿀 길»이 화면에 있는가
  //
  //   초판은 `confirmed: false`가 핸들러에 하드코딩돼 있었다. 그러면 Jino가 홈택스에서
  //   신고방법을 확인하고 와도 배지는 **영원히 「신고 내역 미확인」**이다 — 계약 §7-1이
  //   *"확인 결과가 FIFO면 배지만 「확인됨」으로 바뀌고"*라고 정한 경로가 통째로 막히고,
  //   이 카드는 자백하는 척만 하는 장식이 된다.
  //   ★변이: 「✓ 홈택스에서 확인했다」 버튼을 지우거나 `true` 대신 `false`를 넘기면 깨진다.
  // ══════════════════════════════════════════════════════════════════
  it("★미확인 상태에선 「확인됨으로 표시」 버튼이 있고, `confirmed=true`로 부른다", () => {
    const onReconfirm = vi.fn();
    render(<ValuationBadge settings={SETTINGS} history={[]} onReconfirm={onReconfirm} />);
    const promptSpy = vi.spyOn(window, "prompt");
    promptSpy.mockReturnValueOnce("2025 귀속 신고서 ③신고방법 = 선입선출법");

    fireEvent.click(screen.getByTestId("valuation-mark-confirmed"));
    expect(onReconfirm).toHaveBeenCalledWith(
      "2025 귀속 신고서 ③신고방법 = 선입선출법",
      true,
    );
    promptSpy.mockRestore();
  });

  it("이미 확인된 상태면 「확인됨으로 표시」 버튼을 안 그린다 — 이미 한 일을 또 시키지 않는다", () => {
    const confirmed: CostSetting[] = [
      { key: "valuation_method", value: "fifo", confirmed: true, note: null, updated_at: null },
      { key: "standard_price_rule", value: "latest", confirmed: true, note: null, updated_at: null },
    ];
    render(<ValuationBadge settings={confirmed} history={[]} onReconfirm={() => {}} />);
    expect(screen.queryByTestId("valuation-mark-confirmed")).toBeNull();
    // 그래도 «재확인 기록»은 계속 남길 수 있어야 한다(§4-②).
    expect(screen.getByRole("button", { name: /선입선출 재확인/ })).toBeTruthy();
  });
});

// ════════════════════════════════════════════════════════════════════
// ③ App을 `/cost`에서 통째로 렌더 — 배선(호출부) 자체가 살아 있는가
//    (SUR-F1 · SUR-F2 · SUR-F4는 여기서만 잡힌다 — ②의 직접 렌더로는 "CostPage.tsx가
//    이 컴포넌트를 실제로 부르는가"를 원리적으로 못 잰다)
// ════════════════════════════════════════════════════════════════════

const MATERIAL_MIN: CostMaterial = {
  id: 1,
  name: "cleaning kit",
  unit: "ea",
  category: "부자재",
  status: "unconfirmed",
  excel_label: null,
  excel_ref_price: null,
  match_rule: "cleaning kit",
  form_factor: null,
  part: null,
  note: null,
  lot_count: 0,
  price_count: 0,
  stale_count: 0,
  latest_price_ex_vat: null,
  latest_price_inc_vat: null,
  latest_price_inc_derived: false,
  latest_price_source: null,
  // 단가가 없으면 발효일도 없다 — 채택된 단가 행이 없기 때문이다(D-CPP-62 S2).
  latest_price_effective_date: null,
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

const SETTINGS_FULL: CostSetting[] = [
  { key: "valuation_method", value: "fifo", confirmed: false, note: null, updated_at: null },
  { key: "standard_price_rule", value: "latest", confirmed: true, note: null, updated_at: null },
];

const RUN_1: CostAutoRefreshRun = {
  id: 1,
  trigger: "cron",
  started_at: "2026-08-26T00:40:00",
  finished_at: "2026-08-26T00:40:05",
  checked: 12,
  updated: 1,
  failed: 0,
  queued: 1,
  note: null,
  entries: [],
};

const QUEUE_ENTRY: CostAutoRefreshEntry = {
  id: 501,
  run_id: 1,
  outcome: "queued",
  material_id: null,
  material_name: null,
  price_id: null,
  import_invoice_line_id: 777,
  hbl_no: "SETR9999999999",
  item_name: "새 원단 X",
  old_price_ex_vat: null,
  new_price_ex_vat: "12.34",
  message: "「새 원단 X」은 아직 어느 부자재에도 연결된 적이 없다 — 첫 연결은 사람이 한다(계약 §7-4).",
  created_at: "2026-08-26T00:41:00",
};

const HISTORY_ROW: CostSettingHistoryRow = {
  id: 1,
  key: "valuation_method",
  old_value: "fifo",
  new_value: "fifo",
  old_confirmed: false,
  new_confirmed: false,
  actor: "jino",
  note: "홈택스 확인함",
  created_at: "2026-08-26T00:00:00",
};

// api 모듈 전체를 모킹한다 — Layout의 헬스·쿠키 조회까지 네트워크를 안 타게 하기 위해서다
// (costPageReachesTheUser.test.tsx와 같은 관례).
vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    fetchCostMaterials: vi.fn(async () => ({ items: [MATERIAL_MIN] })),
    fetchCostLedgerMaterialLines: vi.fn(async () => ({ items: [] })),
    fetchCostSettings: vi.fn(async () => ({ items: SETTINGS_FULL })),
    fetchCostRecipes: vi.fn(async () => ({ items: [] })),
    fetchCostBoard: vi.fn(async () => ({
      items: [],
      sku_count: 0,
      computed_count: 0,
      uncomputed_count: 0,
      recipe_count: 0,
      approved_recipe_count: 0,
    })),
    fetchCostSettingHistory: vi.fn(async () => ({ items: [HISTORY_ROW] })),
    fetchCostAutoRefreshRuns: vi.fn(async () => ({ items: [RUN_1] })),
    fetchCostAutoRefreshQueue: vi.fn(async () => ({ items: [QUEUE_ENTRY] })),
    runCostAutoRefreshNow: vi.fn(async () => ({
      run_id: 2,
      trigger: "manual",
      checked: 0,
      updated: 0,
      failed: 0,
      queued: 0,
    })),
    updateCostSetting: vi.fn(async () => ({
      key: "valuation_method",
      value: "fifo",
      confirmed: false,
      note: null,
      updated_at: null,
      value_changed: false,
      confirmed_changed: false,
    })),
    getSchedulerHealth: vi.fn(async () => ({ healthy: true })),
    getAdCostCookieStatus: vi.fn(async () => ({})),
    getCollectionStatus: vi.fn(async () => ({ streams: [] })),
    fetchApi: vi.fn(async () => ({ jobs: [], items: [] })),
  };
});

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
  vi.unstubAllGlobals();
});

/** `/cost`를 통째로 렌더한다.
 *
 *  ★`materials`를 갈아끼울 수 있게 둔 이유: SUR-F6·F7이 재는 배지는 **특정 데이터 모양**
 *  (어긋남 있음 / 로트 구간 폭 있음)에서만 뜬다. 기본 픽스처는 둘 다 없는 «조용한» 종이라
 *  그대로 두면 그 렌더 자리를 원리적으로 못 잰다 — 실제로 리뷰어의 변이가 그 틈으로 살아남았다. */
async function renderApp(over?: { materials?: CostMaterial[] }) {
  // ★★**항상** 세운다 — 조건부로 덮어쓰면 그 값이 다음 테스트로 «샌다»(적대 리뷰 2R P1).
  //   초판은 `over?.materials`가 있을 때만 `mockResolvedValue`를 불렀고 되돌리지 않아서,
  //   SUR-F7이 주입한 `lot_price_has_span: true` 객체가 그 뒤 테스트에도 그대로 반환됐다
  //   (리뷰어가 진단 테스트로 오염을 확정했다). 지금 초록인 것은 «마침» 뒤 테스트가 그
  //   내용을 안 보기 때문이지 격리가 지켜져서가 아니다 — 이 저장소가 반복 지적해 온
  //   「테스트가 초록인데 아무것도 안 지킨다」가 **이 PR의 새 테스트 안에서** 재발한 것이다.
  //   ⇒ 매 호출이 자기 상태를 «전부» 정한다. 조건부 갈래를 없애는 것이 곧 누수의 제거다.
  const api = await import("../lib/api");
  (api.fetchCostMaterials as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
    items: over?.materials ?? [MATERIAL_MIN],
  });
  const { default: App } = await import("../App");
  const result = render(<App />);
  await screen.findByRole("heading", { name: /원가/ });
  // ★홈이 기본 탭이다(D-CPP-62 S2, `CostPage.tsx`). 이 파일의 모든 테스트가 재는 것은
  //   부자재 탭 내용(자동 갱신 패널·종 목록 배지)이라, 여기 한 곳에서 이동시킨다 —
  //   각 `it()`에서 따로 클릭하게 두면 다음에 한쪽만 고치는 병이 재발한다.
  fireEvent.click(screen.getByRole("button", { name: "부자재" }));
  await screen.findByTestId("material-list-scroll");
  return result;
}

describe("★D-CPP-60 표면이 `/cost`에서 실제로 닿는가 (App 통째 렌더)", () => {
  it("SUR-F1: 평가방법 카드의 두 줄이 «함께» 뜬다 — 하나만 보이면 화면이 거짓말한다(§4-①)", async () => {
    await renderApp();
    await screen.findByRole("heading", { name: /원가/ });
    expect(await screen.findByText(/신고 내역 미확인/)).toBeTruthy();
    // ★이 줄이 SUR-F1이 지키는 자리다 — 「층1 표준원가가 지금 쓰는 단가」 렌더 자체를
    //   지우는 변이는 이 assert에서만 죽는다(직접 렌더 테스트는 호출부 삭제를 못 잡는다).
    expect(await screen.findByTestId("standard-price-rule-line")).toBeTruthy();
    expect(screen.getByText(/재고 원장\(C1\) 가동 전/)).toBeTruthy();
  });

  it("SUR-F2: 「단가 자동 갱신」 패널 **호출부**가 있어야 화면에 검사 결과가 뜬다", async () => {
    await renderApp();
    await screen.findByRole("heading", { name: /원가/ });
    // ★컴포넌트 자체는 CostPage.tsx에 살아 있다 — 이 assert는 「부르는 줄」이 살아
    //   있는지를 잰다. 호출부를 지우면 findByTestId가 타임아웃으로 실패한다.
    expect(await screen.findByTestId("auto-refresh-panel")).toBeTruthy();
    expect(screen.getByText(/최근 검사: 2026-08-26 09:40 \(일일 sweep\)/)).toBeTruthy();
  });

  it("SUR-F4: 「연결 대기」 큐의 사유(`message`)가 화면에 그려진다", async () => {
    await renderApp();
    await screen.findByRole("heading", { name: /원가/ });
    expect(
      await screen.findByText(/첫 연결은 사람이 한다\(계약 §7-4\)/),
    ).toBeTruthy();
  });

  // ══════════════════════════════════════════════════════════════════
  // ★SUR-F6·F7 — 적대 리뷰 1R에서 **살아남은** 변이의 자리
  //
  //   리뷰어가 `CostPage.tsx`의 `price_conflict` 배지 렌더 블록을 `{null}`로 치환했는데
  //   프론트 534개가 **전부 초록이었다**. 순수 함수(`priceConflictText`) 단위 테스트만 있고
  //   **그 문구가 실제 DOM에 뜨는지 재는 테스트가 없었다** — 이 저장소가 반복해 밟은
  //   「함수는 값을 만드는데 사람은 못 본다」의 정확히 그 모양이다.
  //   §4-⑥ 로트 구간 배지도 같은 구멍이었으므로 둘 다 여기서 잠근다.
  // ══════════════════════════════════════════════════════════════════
  it("★SUR-F6: 어긋남 배지(§4-⑦)가 실제 DOM에 뜬다 — 순수 함수만 재면 렌더가 안 잠긴다", async () => {
    await renderApp({
      materials: [
        {
          ...MATERIAL_MIN,
          latest_price_ex_vat: "190.82",
          latest_price_source: "ledger",
          price_conflict: true,
          price_conflict_price_id: 29,
        },
      ],
    });
    await screen.findByRole("heading", { name: /원가/ });
    expect(
      await screen.findByTestId(`material-${MATERIAL_MIN.id}-price-conflict`),
    ).toBeTruthy();
    expect(screen.getByText(/더 늦은 수동 입력이 있다/)).toBeTruthy();
  });

  it("★SUR-F7: 관측 로트 구간 배지(§4-⑥)가 실제 DOM에 뜬다 — 폭이 없으면 안 뜬다", async () => {
    await renderApp({
      materials: [
        {
          ...MATERIAL_MIN,
          lot_count: 2,
          latest_price_ex_vat: "190.82",
          latest_price_source: "ledger",
          lot_price_min: "178.78",
          lot_price_max: "190.82",
          lot_price_has_span: true,
        },
      ],
    });
    await screen.findByRole("heading", { name: /원가/ });
    expect(
      await screen.findByTestId(`material-${MATERIAL_MIN.id}-lot-span`),
    ).toBeTruthy();
    expect(screen.getByText(/178\.78~190\.82원/)).toBeTruthy();
    // ★그리고 「FIFO」로 부르지 않는다(§3 금지선) — 방향이 반대다.
    expect(screen.queryByText(/선입선출 근사|FIFO 근사/)).toBeNull();
  });

  // ★적대 리뷰 2R P1 회귀 — 픽스처 오염이 다음 테스트로 새면 안 된다.
  //   이 테스트는 **SUR-F7 뒤에** 놓여야 의미가 있다(F7이 span 있는 종을 주입한다).
  it("★렌더 픽스처가 다음 테스트로 새지 않는다(2R P1 회귀)", async () => {
    await renderApp();
    await screen.findByRole("heading", { name: /원가/ });
    // 기본 픽스처엔 구간도 어긋남도 없다 — 앞 테스트의 주입이 새면 이 둘이 뜬다.
    expect(screen.queryByTestId(`material-${MATERIAL_MIN.id}-lot-span`)).toBeNull();
    expect(screen.queryByTestId(`material-${MATERIAL_MIN.id}-price-conflict`)).toBeNull();
  });

  it("「지금 검사」를 누르면 자동 갱신을 다시 부르고 목록이 새로고침된다", async () => {
    const api = await import("../lib/api");
    await renderApp();
    await screen.findByRole("heading", { name: /원가/ });
    fireEvent.click(screen.getByRole("button", { name: "지금 검사" }));
    await waitFor(() => {
      expect(api.runCostAutoRefreshNow).toHaveBeenCalled();
    });
    await waitFor(() => {
      // load()가 재호출됐다 — 초기 1회 + 지금 검사 후 1회.
      expect((api.fetchCostAutoRefreshRuns as unknown as { mock: { calls: unknown[] } }).mock.calls.length).toBeGreaterThan(1);
    });
  });
});
