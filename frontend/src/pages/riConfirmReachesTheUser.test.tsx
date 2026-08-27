// @vitest-environment jsdom
//
// riConfirmReachesTheUser.test.tsx — 「거래명세서확인」 실행 «표면» 회귀.
// 계약 CONTRACT_1p_invoice_confirm_write §4 S1 · §6(변이 조건) — Jino 승인 2026-08-28.
//
// ★존재 이유: 백엔드 게이트 27종은 `test_rocket_invoice_confirm.py`가 지킨다. 그런데 이
//   계약의 합격기준은 전부 **「Jino가 화면에서 무엇을 보고 무엇을 누르는가」**로 쓰여 있다.
//   단위 테스트는 「함수가 값을 만드나」를 묻지 「사람이 그걸 보나」를 못 묻는다 —
//   같은 자리에서 2026-08-22에 표면 변이 3종이 전부 생존한 전례가 있다(지혜 성적표 패널).
//
// 그래서 이 파일이 겨누는 변이(전부 이 파일만 잡는다):
//   ① 「확인」 버튼 렌더 제거 → test 「버튼이 실제로 보인다」
//   ② 모달의 «되돌릴 수 없습니다» 문구 제거 → test 「되돌릴 수 없음을 누르기 전에 말한다」
//   ③ dry-run 미리보기 렌더 제거 → test 「무엇을 보낼지 모달이 보여준다」
//   ④ 「실행」 클릭 → requestRocketInvoiceConfirm 배선 제거 → test 「실행이 실제 요청까지 간다」
//   ⑤ 굳은 행에 버튼을 띄우는 변이 → test 「굳은 행에는 버튼이 없고 이유가 있다」
//   ⑥ blocked_reason 렌더 제거 → test 「못 누르는 이유가 화면에 있다」
//   ⑦ 실행 이력 카드 제거 → test 「이력이 화면에 있다」
//
// ★★그리고 이 파일이 «열기만 해서는 아무 일도 없다»를 잰다 — 모달을 열고 닫는 것만으로
//   requestRocketInvoiceConfirm이 불리면 모달을 여는 행위 자체가 회계 확정이 된다.
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";

const h = vi.hoisted(() => ({
  queue: null as unknown,
  history: null as unknown,
  previewCalls: [] as number[],
  requestCalls: [] as unknown[],
  requestFails: null as null | string,
}));

vi.mock("../lib/api", () => ({
  fetchRocketPipeline: () => new Promise<never>(() => {}),
  fetchRocketPipelineStage: () => new Promise<never>(() => {}),
  isPoStage: () => true,
  fetchRocketRiQueue: () => Promise.resolve(h.queue),
  fetchRocketConfirmHistory: () => Promise.resolve(h.history),
  previewRocketInvoiceConfirm: (seq: number) => {
    h.previewCalls.push(seq);
    return Promise.resolve({
      dry_run: true as const,
      operation: "1P 거래명세서확인(RI→CI)",
      method: "POST",
      path: `/scm/purchase/order/confirmInvoice?purchaseOrderSeq=${seq}`,
      payload: {},
      purchase_order_seq: seq,
      received_amount: "230235",
      po_status: "RI",
      irreversible: true,
      irreversible_note: "되돌릴 수 없습니다",
    });
  },
  requestRocketInvoiceConfirm: (seq: number, note?: string) => {
    h.requestCalls.push({ seq, note });
    return h.requestFails
      ? Promise.reject(new Error(h.requestFails))
      : Promise.resolve({ dry_run: false, executed: true });
  },
}));

const { RiQueueTab } = await import("./rocketPipelineTabs");

function riRow(over: Record<string, unknown> = {}) {
  return {
    purchase_order_seq: 139791428,
    po_date: "2026-08-18",
    receiving_finished_date: "2026-08-26",
    received_amount: "230235",
    first_sku_name: "테스트 상품",
    sku_count: 1,
    status: "RI",
    synced_date: "2026-08-28",
    is_stale: false,
    invoices: [],
    invoice_rows_missing: [],
    confirm: { state: null, command_id: null, can_request: true, blocked_reason: null },
    ...over,
  };
}

function queueWith(rows: unknown[]) {
  const live = rows.filter((r) => !(r as { is_stale: boolean }).is_stale);
  const stale = rows.filter((r) => (r as { is_stale: boolean }).is_stale);
  return {
    rows,
    live_count: live.length,
    live_amount: "230235",
    stale_count: stale.length,
    stale_amount: "0",
    last_collection_date_kst: "2026-08-28",
    note: "테스트",
  };
}

const EMPTY_HISTORY = { rows: [], total: 0, limit: 50, lease_ttl_minutes: 20 };

beforeEach(() => {
  h.queue = queueWith([riRow()]);
  h.history = EMPTY_HISTORY;
  h.previewCalls = [];
  h.requestCalls = [];
  h.requestFails = null;
});
afterEach(cleanup);

describe("확인요청함 — 실행 표면", () => {
  it("살아 있는 RI 행에 「확인」 버튼이 실제로 보인다", async () => {
    render(<RiQueueTab />);
    expect(await screen.findByRole("button", { name: "확인" })).toBeTruthy();
  });

  it("굳은 행에는 버튼이 없고 «왜 안 되는지»가 대신 보인다", async () => {
    h.queue = queueWith([
      riRow({
        purchase_order_seq: 115340779,
        is_stale: true,
        synced_date: "2026-08-05",
        confirm: {
          state: null, command_id: null,
          can_request: false, blocked_reason: "재수집 후 확인 가능",
        },
      }),
    ]);
    render(<RiQueueTab />);
    expect(await screen.findByText("재수집 후 확인 가능")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "확인" })).toBeNull();
  });

  it("★백엔드가 굳은 행에 can_request=true를 줘도 화면이 막는다 (2겹 방어의 바깥쪽)", async () => {
    // 적대 리뷰 1R P2-1: 이 가드(`stale || !onConfirm`)를 지워도 아무 테스트가 안 죽었다.
    // 굳은 원장은 「마지막으로 본 상태」라 실행 근거가 못 된다 — 백엔드가 뚫려도 여기서 멈춘다.
    h.queue = queueWith([
      riRow({
        purchase_order_seq: 115340779,
        is_stale: true,
        synced_date: "2026-08-05",
        confirm: { state: null, command_id: null, can_request: true, blocked_reason: null },
      }),
    ]);
    render(<RiQueueTab />);
    expect(await screen.findByText("재수집 후 확인 가능")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "확인" })).toBeNull();
  });

  it("확인 완료 뒤에는 버튼이 아니라 «재수집 반영 대기»가 뜬다", async () => {
    // 적대 리뷰 1R P1-3: succeeded 뒤 원장의 RI는 우리가 이미 틀렸다고 아는 값이다.
    h.queue = queueWith([
      riRow({
        confirm: {
          state: "succeeded", command_id: 3, can_request: false,
          blocked_reason: "확인 완료 — 재수집 반영 대기",
          last_finished_at: "2026-08-28 08:10",
        },
      }),
    ]);
    render(<RiQueueTab />);
    expect(await screen.findByText("확인 완료 — 재수집 반영 대기")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "확인" })).toBeNull();
  });

  it("결과 미상으로 잠긴 행은 사유를 화면에 말한다 — 버튼만 사라지지 않는다", async () => {
    h.queue = queueWith([
      riRow({
        confirm: {
          state: "unknown", command_id: 7, can_request: false,
          blocked_reason: "결과 미상 — 재수집 전 재실행 불가",
          last_finished_at: "2026-08-28 07:10",
        },
      }),
    ]);
    render(<RiQueueTab />);
    expect(await screen.findByText("결과 미상 — 재수집 전 재실행 불가")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "확인" })).toBeNull();
  });

  it("모달이 «되돌릴 수 없다»를 누르기 전에 말하고, 보낼 요청을 그대로 보여준다", async () => {
    render(<RiQueueTab />);
    fireEvent.click(await screen.findByRole("button", { name: "확인" }));

    // 되돌리기 경로가 없다는 사실 — 이 문구가 이 모달의 존재 이유다.
    expect(
      await screen.findByText(/되돌릴 수 없습니다/),
    ).toBeTruthy();
    // dry-run 미리보기가 «지어낸 것»이 아니라 백엔드 응답 원문이다.
    await waitFor(() =>
      expect(
        screen.getByText("POST /scm/purchase/order/confirmInvoice?purchaseOrderSeq=139791428"),
      ).toBeTruthy(),
    );
    expect(h.previewCalls).toEqual([139791428]);
  });

  it("★모달을 열고 닫기만 하면 실행 요청이 0건이다", async () => {
    render(<RiQueueTab />);
    fireEvent.click(await screen.findByRole("button", { name: "확인" }));
    await screen.findByText(/되돌릴 수 없습니다/);
    fireEvent.click(screen.getByRole("button", { name: "취소" }));

    await waitFor(() => expect(screen.queryByText(/되돌릴 수 없습니다/)).toBeNull());
    // ★열기는 dry-run뿐 — 라이브 명령은 만들어지지 않았다.
    expect(h.requestCalls).toEqual([]);
  });

  it("「실행」 클릭이 실제 요청까지 간다 (배선 절단 변이가 여기서 죽는다)", async () => {
    render(<RiQueueTab />);
    fireEvent.click(await screen.findByRole("button", { name: "확인" }));
    await screen.findByText(/되돌릴 수 없습니다/);
    fireEvent.click(screen.getByRole("button", { name: "실행" }));

    await waitFor(() => expect(h.requestCalls.length).toBe(1));
    expect((h.requestCalls[0] as { seq: number }).seq).toBe(139791428);
  });

  it("실행이 실패하면 사유를 모달에 남긴다 — 조용히 닫히지 않는다", async () => {
    h.requestFails = "API error 400: 상태가 굳었습니다";
    render(<RiQueueTab />);
    fireEvent.click(await screen.findByRole("button", { name: "확인" }));
    await screen.findByText(/되돌릴 수 없습니다/);
    fireEvent.click(screen.getByRole("button", { name: "실행" }));

    expect(await screen.findByText(/상태가 굳었습니다/)).toBeTruthy();
    // 모달이 열린 채 남아 있어야 사람이 사유를 읽는다.
    expect(screen.getByRole("button", { name: "실행" })).toBeTruthy();
  });
});

describe("확인요청함 — 실행 이력 표면", () => {
  it("이력이 화면에 있다 — 감사 레코드가 DB에만 있으면 미달이다", async () => {
    h.history = {
      rows: [{
        command_id: 1,
        purchase_order_seq: 139791428,
        state: "succeeded",
        requested_at: "2026-08-28 07:30:00",
        received_amount_at_request: 230235,
        precheck: "button_present",
        http_status: 200,
        has_response_body: true,
        response_excerpt: '{"success": true}',
        finished_at: "2026-08-28 07:30:12",
        error: null,
      }],
      total: 1, limit: 50, lease_ttl_minutes: 20,
    };
    render(<RiQueueTab />);
    expect(await screen.findByText("확인 완료")).toBeTruthy();
    expect(screen.getByText("2026-08-28 07:30:00")).toBeTruthy();
    // 응답 원문이 «보존됐다»는 사실이 화면에 있다.
    expect(screen.getByText(/본문 보존됨/)).toBeTruthy();
  });

  it("「결과 미상」을 «실패»라고 부르지 않는다", async () => {
    h.history = {
      rows: [{
        command_id: 2,
        purchase_order_seq: 139791428,
        state: "unknown",
        requested_at: "2026-08-28 07:31:00",
        received_amount_at_request: 230235,
        precheck: "button_present",
        http_status: null,
        has_response_body: false,
        response_excerpt: null,
        finished_at: "2026-08-28 07:51:00",
        error: "임대 TTL 20분 초과 — 페처 보고 없음.",
      }],
      total: 1, limit: 50, lease_ttl_minutes: 20,
    };
    render(<RiQueueTab />);
    expect(await screen.findByText("결과 미상")).toBeTruthy();
    expect(screen.queryByText("실패(반영 안 됨)")).toBeNull();
    // 「재시도 없음」이 화면의 문장으로 있어야 사람이 또 누르지 않는다.
    expect(screen.getByText(/자동 재시도 없음/)).toBeTruthy();
  });
});
