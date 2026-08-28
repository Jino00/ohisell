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
import { render, screen, cleanup } from "@testing-library/react";
import type React from "react";

const h = vi.hoisted(() => ({ changes: null as unknown }));

vi.mock("../lib/api", () => ({
  fetchRocketPipeline: () => new Promise<never>(() => {}),
  fetchRocketPipelineStage: () => new Promise<never>(() => {}),
  fetchRocketRiQueue: () => new Promise<never>(() => {}),
  fetchRocketConfirmHistory: () => new Promise<never>(() => {}),
  previewRocketInvoiceConfirm: () => new Promise<never>(() => {}),
  requestRocketInvoiceConfirm: () => new Promise<never>(() => {}),
  isPoStage: () => true,
  fetchRocketPoChanges: () => Promise.resolve(h.changes),
}));

const mod = await import("./rocketPipelineTabs");
// 카드는 PipelineTab 안에 있다 — 탭을 통째로 렌더하면 파이프라인 조회가 pending이라 화면이
// 안 그려진다. 그래서 카드만 직접 렌더한다(export가 없으면 이 테스트가 그 사실을 드러낸다).
const PoChangesCard = (mod as Record<string, unknown>).PoChangesCard as
  | (() => React.ReactElement)
  | undefined;
/** non-null 단언을 JSX 안에 쓸 수 없어 여기서 푼다. undefined면 첫 테스트가 그 사실을 드러낸다. */
const Card = () => (PoChangesCard ? <PoChangesCard /> : <div>NOT_EXPORTED</div>);

const FULL = {
  round_at: "2026-08-28 12:34",
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

beforeEach(() => { h.changes = FULL; });
afterEach(cleanup);

describe("이번 수집에서 달라진 것 — 표면", () => {
  it("카드가 실제로 보인다", async () => {
    expect(PoChangesCard).toBeTypeOf("function");
    render(<Card />);
    expect(await screen.findByText(/이번 수집에서 달라진 것/)).toBeTruthy();
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
});
