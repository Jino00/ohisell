// @vitest-environment jsdom
/**
 * 「supplier에 붙여넣을 발주번호 줄」 표면 회귀 테스트.
 *
 * ★왜 이 파일이 따로 있나 (2026-08-28, Jino 지시):
 *   *"확인이 필요한 번호를 내가 직접 누를께. 대신에 그 번호들을 모아서 볼 수 있게 해줘.
 *     발주서 번호를 넣으면 눌러야 하는 건들만 모이거든. 그래서 발주번호를 모아서 , 로 구분해줘"*
 *
 *   이 줄이 없으면 사장님은 8개 번호를 화면에서 눈으로 옮겨 적어야 한다. 그리고 이 화면의
 *   「확인」 버튼 경로는 **수집 창 밖 발주(2025-10~2026-01)에서 당일 결과가 안 돌아온다** —
 *   신선도 판정이 `date(synced_at) != last_day`라 오늘 이미 본 건은 재수집 대상에서 빠지기
 *   때문이다(2026-08-28 실측: 17:55 확인 성공 → 17:58 재수집 677건 → 그 발주 synced_at은
 *   12:34 그대로). supplier에 직접 붙여넣는 이 경로는 그 사각을 통째로 비켜간다.
 *
 * ★그래서 테스트가 지키는 것은 「join이 되나」가 아니라 **「사람이 그 줄을 화면에서 보고
 *   가져갈 수 있나」**다. 값을 만드는 층과 사람에게 닿는 층을 같이 지킨다.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within, cleanup, fireEvent, act } from "@testing-library/react";

const h = { queue: null as unknown, history: null as unknown };
vi.mock("../lib/api", () => ({
  fetchRocketRiQueue: () => Promise.resolve(h.queue),
  fetchRocketConfirmHistory: () => Promise.resolve(h.history),
  previewRocketInvoiceConfirm: () => Promise.resolve({ count: 0, rows: [] }),
  requestRocketInvoiceConfirm: () => Promise.resolve({ command_id: 1 }),
}));

const { RiQueueTab, PoSeqCopyBox } = await import("./rocketPipelineTabs");

/** 라이브에서 실제로 나온 8건(2026-08-28 18:0x) — 전부 수집 창 밖 발주다. */
const LIVE_SEQS = [
  115340779, 115484299, 120917675, 123018668,
  123017214, 123017061, 123016751, 123014942,
];

function riRow(seq: number, isStale = false) {
  return {
    purchase_order_seq: seq,
    po_date: "2025-10-15",
    receiving_finished_date: "2025-11-11",
    received_amount: "75430",
    first_sku_name: "오하이 지문방지 PET 무광필름",
    sku_count: 1,
    status: "RI",
    synced_date: "2026-08-28",
    is_stale: isStale,
    invoices: [],
    invoice_rows_missing: [],
    confirm: { state: null, command_id: null, can_request: !isStale, blocked_reason: null },
  };
}

function queueWith(rows: ReturnType<typeof riRow>[]) {
  const live = rows.filter((r) => !r.is_stale);
  const stale = rows.filter((r) => r.is_stale);
  return {
    rows,
    live_count: live.length,
    live_amount: "4870435",
    stale_count: stale.length,
    stale_amount: "0",
    last_collection_date_kst: "2026-08-28",
    note: "테스트 안내문",
  };
}

const EMPTY_HISTORY = { rows: [], total: 0, limit: 50, lease_ttl_minutes: 20 };

beforeEach(() => {
  cleanup();
  h.queue = queueWith(LIVE_SEQS.map((x) => riRow(x)));
  h.history = EMPTY_HISTORY;
});

describe("supplier 붙여넣기 줄", () => {
  it("확인이 필요한 건들의 발주번호를 「, 」로 이어 화면에 보여준다", async () => {
    h.queue = queueWith(LIVE_SEQS.map((x) => riRow(x)));
    render(<RiQueueTab />);

    const line = await screen.findByTestId("po-seq-copy-live");
    // ★순서·구분자까지 지킨다 — supplier 검색창이 그대로 먹어야 한다.
    expect(line.textContent).toBe(LIVE_SEQS.join(", "));
  });

  it("번호 줄은 값을 만드는 데서 끝나지 않고 «복사» 수단과 함께 온다", async () => {
    h.queue = queueWith(LIVE_SEQS.map((x) => riRow(x)));
    render(<RiQueueTab />);

    const box = await screen.findByTestId("po-seq-copy-live-box");
    expect(within(box).queryByRole("button", { name: /복사/ })).not.toBeNull();
  });

  it("복사 버튼을 누르면 클립보드에 콤마 목록이 들어간다", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    h.queue = queueWith(LIVE_SEQS.map((x) => riRow(x)));
    render(<RiQueueTab />);

    const box = await screen.findByTestId("po-seq-copy-live-box");
    fireEvent.click(within(box).getByRole("button", { name: /복사/ }));

    expect(writeText).toHaveBeenCalledWith(LIVE_SEQS.join(", "));
  });

  it("클립보드가 막혀 있어도 번호는 화면에 남는다 — 손으로 가져갈 수 있어야 한다", async () => {
    h.queue = queueWith(LIVE_SEQS.map((x) => riRow(x)));
    render(<RiQueueTab />);

    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    Object.assign(navigator, { clipboard: { writeText } });

    const box = await screen.findByTestId("po-seq-copy-live-box");
    await act(async () => {
      fireEvent.click(within(box).getByRole("button", { name: /복사/ }));
      // ★거부가 실제로 처리될 때까지 기다린 «뒤»에 단언한다.
      //   종전엔 waitFor가 첫 동기 검사에서 통과해, 실패 시 줄을 감추는 변이(M5)가 살아남았다.
      await Promise.resolve();
      await Promise.resolve();
    });
    await waitFor(() => expect(writeText).toHaveBeenCalled());

    // 실패해도 줄 자체가 사라지면 안 된다(그게 이 기능의 유일한 대안 경로다).
    expect(screen.queryByTestId("po-seq-copy-live")).not.toBeNull();
    expect(screen.getByTestId("po-seq-copy-live").textContent).toBe(LIVE_SEQS.join(", "));
    // 그리고 실패를 «성공»으로 보이게 하지 않는다.
    expect(within(box).getByRole("button", { name: /복사/ }).textContent).toBe("복사");
  });

  it("「지금 상태를 모르는 건」도 별도 줄로 준다 — 두 목록을 섞지 않는다", async () => {
    h.queue = queueWith([riRow(111111111), riRow(222222222, true), riRow(333333333, true)]);
    render(<RiQueueTab />);

    expect((await screen.findByTestId("po-seq-copy-live")).textContent).toBe("111111111");
    const staleLine = await screen.findByTestId("po-seq-copy-stale");
    expect(staleLine.textContent).toBe("222222222, 333333333");
    // ★섞이면 supplier 검색 결과가 「눌러야 할 것」과 「모르는 것」을 한 화면에 뭉갠다.
    expect(staleLine.textContent).not.toContain("111111111");
  });

  it("건수가 0이면 줄을 아예 만들지 않는다 — 빈 줄을 붙여넣게 두지 않는다", () => {
    // ★컴포넌트를 «직접» 렌더한다. RiQueueTab 경유로는 이 가드에 닿지 않는다 —
    //   Card가 먼저 `live.length === 0` 분기로 EmptyState를 그려서 이 줄이 아예 안 불린다.
    //   그래서 종전 테스트는 통과하면서도 가드를 하나도 안 지켰다(변이 M4가 살아남은 자리).
    render(<PoSeqCopyBox rows={[]} kind="live" hint="힌트" />);
    expect(screen.queryByTestId("po-seq-copy-live")).toBeNull();
    expect(screen.queryByTestId("po-seq-copy-live-box")).toBeNull();
  });

  it("한 건뿐이면 구분자 없이 그 번호만 준다", () => {
    render(<PoSeqCopyBox rows={[riRow(999999999)] as never} kind="live" hint="힌트" />);
    expect(screen.getByTestId("po-seq-copy-live").textContent).toBe("999999999");
  });

  it("탭 안에서도 stale 목록이 비면 그 줄은 안 나온다", async () => {
    h.queue = queueWith(LIVE_SEQS.map((x) => riRow(x)));
    render(<RiQueueTab />);

    await screen.findByTestId("po-seq-copy-live");
    expect(screen.queryByTestId("po-seq-copy-stale")).toBeNull();
  });

  it("이 줄을 어디에 쓰는지 화면이 말해 준다 — 숫자만 던지지 않는다", async () => {
    h.queue = queueWith(LIVE_SEQS.map((x) => riRow(x)));
    render(<RiQueueTab />);

    const box = await screen.findByTestId("po-seq-copy-live-box");
    expect(box.textContent).toMatch(/발주서번호/);
  });
});
