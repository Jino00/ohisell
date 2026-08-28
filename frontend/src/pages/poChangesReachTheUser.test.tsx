// @vitest-environment jsdom
//
// poChangesReachTheUser.test.tsx — 「이번 수집에서 달라진 것」 «표면» 회귀.
// 계약 CONTRACT_1p_po_status_history §4 (Jino 승인 2026-08-28 13:33).
//
// ★존재 이유: 백엔드 규율 16종은 `test_rocket_po_changes.py`가 지킨다. 그런데 이 계약의
//   합격기준은 전부 **「Jino가 화면에서 무엇을 보는가」**로 쓰여 있고, 이 기능이 태어난 이유
//   자체가 «화면이 답하지 못해서»다. 값이 만들어지는 것과 사람이 그걸 보는 것은 다른 문제다.
//
// 이 파일이 겨누는 변이:
//   ① 카드 자체 렌더 제거 → test 「카드가 실제로 보인다」
//   ② 「처음 본 발주」/「바뀐 발주」 두 줄을 한 줄로 합치기 → test 「두 줄로 갈려 보인다」
//   ③ 구간(observed_from ~ to) 렌더 제거 → test 「구간으로 말한다」
//   ④ 소급 불가 자백 문구 제거 → test 「이력 시작을 자백한다」
//   ⑤ 금지 문구(「신규 발주」·「들어옴」·「확정됨」)를 되살리는 변이 → test 「단정하지 않는다」
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import type React from "react";

const h = vi.hoisted(() => ({
  changes: null as unknown,
  history: null as unknown,
  historyCalls: [] as number[],
}));

vi.mock("../lib/api", () => ({
  fetchRocketPipeline: () => new Promise<never>(() => {}),
  fetchRocketPipelineStage: () => new Promise<never>(() => {}),
  fetchRocketRiQueue: () => new Promise<never>(() => {}),
  fetchRocketConfirmHistory: () => new Promise<never>(() => {}),
  previewRocketInvoiceConfirm: () => new Promise<never>(() => {}),
  requestRocketInvoiceConfirm: () => new Promise<never>(() => {}),
  isPoStage: () => true,
  fetchRocketPoChanges: () => Promise.resolve(h.changes),
  fetchRocketPoHistory: (seq: number) => {
    h.historyCalls.push(seq);
    return Promise.resolve(h.history);
  },
}));

const mod = await import("./rocketPipelineTabs");
// 카드는 PipelineTab 안에 있다 — 탭을 통째로 렌더하면 파이프라인 조회가 pending이라 화면이
// 안 그려진다. 그래서 카드만 직접 렌더한다(export가 없으면 이 테스트가 그 사실을 드러낸다).
const PoChangesCard = (mod as Record<string, unknown>).PoChangesCard as
  | (() => React.ReactElement)
  | undefined;
/** non-null 단언을 JSX 안에 쓸 수 없어 여기서 푼다. undefined면 첫 테스트가 그 사실을 드러낸다. */
const Card = () => (PoChangesCard ? <PoChangesCard /> : <div>NOT_EXPORTED</div>);

const HISTORY = {
  purchase_order_seq: 140778881,
  known_po: true,
  history_start: "2026-08-28 10:14",
  empty_reason: null,
  rows: [
    { event: "first_seen", field: null, label: null, before: null, after: "RP",
      observed_from: null, observed_to: "2026-08-28 10:14", is_amount: false, delta: null },
    { event: "field_change", field: "purchase_order_status", label: "상태",
      before: "RP", after: "PA", observed_from: "2026-08-28 10:14",
      observed_to: "2026-08-28 12:34", is_amount: false, delta: null },
  ],
};

const FULL = {
  round_at: "2026-08-28 12:34",
  round: { records: 9, changes: 2, dropped: 0, error: null },
  history_start: "2026-08-28 10:14",
  first_seen: {
    count: 1, amount: 7404840,
    rows: [{
      purchase_order_seq: 140780515,
      status_when_first_seen: "PA",
      order_amount: 7404840,
      label: "처음 관측됨",
    }],
  },
  changed: {
    count: 1, amount: 4104360,
    rows: [{
      purchase_order_seq: 140778881,
      order_amount: 4104360,
      status_from: "RP", status_to: "PA",
      observed_from: "2026-08-28 10:14",
      observed_to: "2026-08-28 12:34",
      fields: [
        { field: "purchase_order_status", label: "상태", before: "RP", after: "PA",
          is_amount: false, delta: null },
        { field: "vendor_confirmed_qty", label: "확정수량", before: "384", after: "373",
          is_amount: false, delta: -11 },
      ],
    }],
  },
  note: "「처음 관측됨」은 그 발주가 이번 수집에서 우리 눈에 처음 들어왔다는 뜻입니다.",
};

beforeEach(() => {
  h.changes = FULL;
  h.history = HISTORY;
  h.historyCalls = [];
});
afterEach(cleanup);

describe("이번 수집에서 달라진 것 — 표면", () => {
  it("카드가 실제로 보인다", async () => {
    expect(PoChangesCard).toBeTypeOf("function");
    render(<Card />);
    expect(await screen.findByText(/이번 수집에서 달라진 것/)).toBeTruthy();
    // ★§4-1 「마지막 수집 회차 기준」 — 회차 시각이 **제목**에 있어야 «어느 회차 얘기인지» 안다.
    //   (같은 시각이 행의 구간 표기에도 나오므로 제목만 겨눈다.)
    expect(
      await screen.findByText(/이번 수집에서 달라진 것 — 2026-08-28 12:34/),
    ).toBeTruthy();
  });

  it("★「처음 본 발주」와 「바뀐 발주」가 다른 줄로 갈려 보인다", async () => {
    render(<Card />);
    // 이 갈림이 이 기능의 존재 이유다 — 합치면 발단의 혼동이 그대로 돌아온다.
    expect(await screen.findByText(/처음 본 발주 — 1건 · 7,404,840원/)).toBeTruthy();
    expect(screen.getByText(/상태·수량이 바뀐 발주 — 1건 · 4,104,360원/)).toBeTruthy();
  });

  it("★변화를 «구간»으로 말한다 — 시점을 단정하지 않는다", async () => {
    render(<Card />);
    expect(
      await screen.findByText(/2026-08-28 10:14 ~ 2026-08-28 12:34 사이에 바뀜/),
    ).toBeTruthy();
    expect(screen.getByText(/RP →/)).toBeTruthy();
  });

  it("수량이 깎인 것이 증감과 함께 보인다 — ①금액이 준 이유가 감액일 때", async () => {
    render(<Card />);
    expect(await screen.findByText(/확정수량 384→373/)).toBeTruthy();
    expect(screen.getByText(/\(-11\)/)).toBeTruthy();
  });

  it("★이력 시작을 자백한다 — 소급이 불가하므로", async () => {
    render(<Card />);
    expect(await screen.findByText(/그 전 변화는 기록이 없습니다/)).toBeTruthy();
    expect(screen.getByText(/이력 시작 2026-08-28/)).toBeTruthy();
  });

  it("★금지 문구가 화면에 없다 — 「신규 발주 발생」·「들어옴」·「확정됨」", async () => {
    render(<Card />);
    await screen.findByText(/처음 본 발주/);
    const body = document.body.textContent ?? "";
    for (const banned of ["신규 발주", "확정됨", "확정했습니다"]) {
      expect(body).not.toContain(banned);
    }
    // 「처음 들어온」은 «우리 눈에» 들어왔다는 뜻으로 쓰이므로 그 맥락이 같이 있어야 한다.
    expect(body).toContain("그때 발주가 생겼다는 뜻이 아닙니다");
  });

  it("변화가 0건이면 0건이라고 말한다 — 지난 회차를 보여주지 않는다", async () => {
    h.changes = {
      ...FULL,
      first_seen: { count: 0, amount: 0, rows: [] },
      changed: { count: 0, amount: 0, rows: [] },
    };
    render(<Card />);
    expect(await screen.findByText(/이번 수집에서는 달라진 발주가 없습니다/)).toBeTruthy();
  });

  it("이력이 아직 없으면 그렇게 말한다", async () => {
    h.changes = {
      round_at: null, history_start: null,
      first_seen: { count: 0, amount: 0, rows: [] },
      changed: { count: 0, amount: 0, rows: [] },
      note: "아직 관측 이력이 없습니다 — 다음 수집부터 쌓입니다.",
    };
    render(<Card />);
    expect(await screen.findByText(/다음 수집부터 쌓입니다/)).toBeTruthy();
  });

  it("조회 실패를 «달라진 게 없다»로 말하지 않는다", async () => {
    h.changes = Promise.reject(new Error("boom")) as unknown;
    render(<Card />);
    expect(await screen.findByText(/조회 실패는 '달라진 게 없다'와 다릅니다/)).toBeTruthy();
  });

  it("★버린 이벤트가 있으면 «달라진 게 없다»고 말하지 않는다 (적대 리뷰 1R P1-1)", async () => {
    // 리뷰어가 재현한 결함: 적재가 통째로 실패한 회차에도 화면이 「달라진 발주가 없습니다」를
    // **단언**했다 — 전이가 실제로 있었는데도. 침묵이 아니라 거짓말이라 더 나쁘다.
    h.changes = {
      ...FULL,
      round: { records: 9, changes: 0, dropped: 2, error: "no such table: coupang_rocket_po_change_log" },
      first_seen: { count: 0, amount: 0, rows: [] },
      changed: { count: 0, amount: 0, rows: [] },
    };
    render(<Card />);
    expect(await screen.findByText(/2건을 기록하지 못했습니다/)).toBeTruthy();
    expect(screen.getByText(/no such table/)).toBeTruthy();
    // ★「없습니다」로 단언하지 않는다.
    expect(screen.queryByText(/이번 수집에서는 달라진 발주가 없습니다/)).toBeNull();
    expect(screen.getByText(/화면에 안 뜨는 것과 일어나지 않은 것은 다릅니다/)).toBeTruthy();
  });

  it("★발주번호를 누르면 그 발주의 관측 이력이 펼쳐진다 (적대 리뷰 1R P1-2)", async () => {
    // 초판은 이 UI가 없었다 — 라우터와 API 클라이언트를 통째로 지워도 테스트가 전부 초록이었다.
    render(<Card />);
    const btn = await screen.findByRole("button", { name: "140778881" });
    fireEvent.click(btn);
    await waitFor(() => expect(h.historyCalls).toEqual([140778881]));
    // 펼쳐진 이력 안의 항목을 겨눈다. 문구가 <b>로 쪼개지고 카드 본문에도 같은 낱말이
    // 있으므로 요소 조회 대신 본문 전체로 판정한다.
    await waitFor(() => {
      const body = document.body.textContent ?? "";
      expect(body).toContain("처음 관측됨(상태 RP)");   // ★이력에만 나오는 모양
      expect(body).toContain("상태 RP→PA");
    });
  });

  it("★이력이 없는 발주는 «왜» 비었는지 화면이 말한다", async () => {
    h.history = {
      ...HISTORY, rows: [],
      empty_reason: "이력은 2026-08-28부터입니다 — 그 전 변화는 기록이 없습니다.",
    };
    render(<Card />);
    fireEvent.click(await screen.findByRole("button", { name: "140778881" }));
    expect(await screen.findByText(/그 전 변화는 기록이 없습니다/)).toBeTruthy();
  });

  it("★원장에 없는 발주는 «배선 전»과 다르게 말한다", async () => {
    h.history = {
      ...HISTORY, rows: [], known_po: false,
      empty_reason: "이 발주번호를 우리 원장에서 본 적이 없습니다.",
    };
    render(<Card />);
    fireEvent.click(await screen.findByRole("button", { name: "140778881" }));
    expect(await screen.findByText(/본 적이 없습니다/)).toBeTruthy();
  });

  it("★카드가 「열린 파이프라인」 탭에 실제로 «붙어» 있다 (배선 절단 변이가 여기서 죽는다)", async () => {
    // ★자체 변이 검증에서 이 구멍이 드러났다: 컴포넌트만 따로 테스트하면 «만들어졌는가»는
    //   지키지만 «탭에 붙어 있는가»는 아무도 안 지킨다. 카드를 통째로 떼어내도 9종이 전부
    //   초록이었다 — 「통과하는데 아무것도 안 지키는 테스트」의 교과서적 모양이다.
    //   PipelineTab을 통째로 렌더할 수 없어(파이프라인 조회가 pending) 소스를 직접 잰다.
    const src = await import("./rocketPipelineTabs?raw").then(
      (m) => (m as { default: string }).default,
    );
    expect(src).toContain("<PoChangesCard />");
  });

  it("★발주 이력 «경로»가 실재한다 — 클라이언트가 진짜 엔드포인트를 부른다 (리뷰어 M1)", async () => {
    // ★이 파일은 `../lib/api`를 통째로 모킹하므로, 실제 클라이언트가 삭제돼도 화면 테스트는
    //   전부 초록이다(리뷰어의 M1이 그렇게 살아남았다). 그래서 «경로의 실재»는 소스로 잰다.
    const api = await import("../lib/api?raw").then(
      (m) => (m as { default: string }).default,
    );
    expect(api).toContain("/api/overview/rocket-po-changes/${seq}");
    expect(api).toMatch(/export function fetchRocketPoHistory/);
  });
});
