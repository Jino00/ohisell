// @vitest-environment jsdom
//
// rocketPipelineTabs.test.tsx — 「열린 파이프라인」·「확인요청함」 탭 (계약 1P계산서 목표 S1·S2).
//
// ★이 테스트가 지키는 것은 «값이 계산되나»가 아니라 **«사람이 그걸 보나»**다.
//   백엔드 테스트(test_rocket_pipeline.py)가 이미 산술을 지키므로, 여기서는 그 값이 **화면에
//   문자로 나타나는지**만 본다 — 렌더를 지워도 초록인 테스트는 아무것도 안 지킨다(교훈 #362:
//   「만드는 층은 테스트하고 닿는 층은 안 지키는」 병이 이 저장소에서 네 번 재발했다).
//   그래서 단언은 전부 `screen.findByText`이고, 호출 횟수·상태값은 세지 않는다.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";

const h = vi.hoisted(() => ({
  pipeline: null as unknown,
  stageRows: null as unknown,
  riQueue: null as unknown,
  stageCalls: [] as Array<{ stage: string; shipFrom?: string | null; shipTo?: string | null }>,
}));

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  fetchRocketPipeline: () => Promise.resolve(h.pipeline),
  fetchRocketPipelineStage: (stage: string, p: { shipFrom?: string | null; shipTo?: string | null }) => {
    h.stageCalls.push({ stage, ...p });
    return Promise.resolve(h.stageRows);
  },
  fetchRocketRiQueue: () => Promise.resolve(h.riQueue),
}));

import { PipelineTab, RiQueueTab } from "./rocketPipelineTabs";

/** prod 실측(2026-08-27)을 축소한 응답. 숫자는 실제 값을 그대로 쓴다. */
function pipelineFixture(over: Record<string, unknown> = {}) {
  return {
    as_of_kst: "2026-08-27",
    ship_window: null,
    stages: [
      { key: "await_confirm", po_count: 30, amount: "2907817", fresh_amount: "665315",
        stale_amount: "2242502", stale_po_count: 24, oldest_stale_synced_date: "2026-06-18" },
      { key: "await_ship", po_count: 50, amount: "12895305", fresh_amount: "11745795",
        stale_amount: "1149510", stale_po_count: 9, oldest_stale_synced_date: "2026-07-18" },
      { key: "await_receive", po_count: 27, amount: "9373788.00", fresh_amount: "9373788.00",
        stale_amount: "0", stale_po_count: 0, oldest_stale_synced_date: null },
      { key: "await_payment", invoice_count: 66, amount: "116587510.00",
        next_payment_date: "2026-08-28", last_payment_date: "2026-10-23" },
    ],
    pre_invoice_subtotal: { amount: "25176910.00", stages: ["await_confirm", "await_ship", "await_receive"] },
    closed_unshipped: { po_count: 2, amount: "55200.00" },
    unexplained: {
      po_count: 137, amount: "8939475.00", oldest_po_date: "2025-07-23",
      newest_po_date: "2026-07-27", confirmed: false,
      reason: "우리 발송 신고량 > 쿠팡 인정 입고량. 덜 보냄·반송·진짜 미수금이 구별 불가로 섞여 있다.",
    },
    clamp: {
      over_shipped: { po_count: 0, amount: "0" },
      over_received: { po_count: 2, amount: "170640" },
      asn_missing: { po_count: 2, received_amount: "170640" },
    },
    unknown_status: { po_count: 0, confirmed_amount: "0", codes: [] },
    unpriced_shipped_qty: 0,
    last_collection_date_kst: "2026-08-27",
    freshness: {
      po_synced_at_kst: "2026-08-27", shipment_synced_at_kst: "2026-08-27",
      latest_shipped_date_kst: "2026-08-25",
      note: "발송 데이터의 최신 발송일 이후가 비어 있는 것이 「발송이 없었다」인지 「아직 수집이 안 됐다」인지는 이 데이터로 구분되지 않는다.",
    },
    ...over,
  };
}

function row(over: Record<string, unknown> = {}) {
  return {
    purchase_order_seq: 139899792, status: "PA", status_label: "발주확정",
    po_date: "2026-08-19", receiving_finished_date: null, synced_date: "2026-08-27",
    order_qty: 14, confirmed_qty: 12, received_qty: 11, shipped_qty: 12,
    order_amount: "134340", confirmed_amount: "114180", shipped_amount: "114180",
    received_amount: "104640", effective_shipped_amount: "114180", asn_missing: false,
    unpriced_shipped_qty: 0, invoice_seqs: [30859090], has_invoice: true,
    center_name: "인천14", first_sku_name: "오하이 풀커버 지문방지 무광택 액정보호필름 2매입",
    sku_count: 7, unshipped_raw: "0", unreceived_raw: "9540",
    stage_amount: "9540", is_stale: false, ...over,
  };
}

beforeEach(() => {
  h.stageCalls = [];
  h.pipeline = pipelineFixture();
  h.stageRows = { stage: "await_receive", total_count: 1, rows: [row()], truncated: false,
                  last_collection_date_kst: "2026-08-27" };
  h.riQueue = null;
});
afterEach(cleanup);

// ══════════════════════════════════════════════════════════════
// S1 「열린 파이프라인」
// ══════════════════════════════════════════════════════════════
describe("열린 파이프라인 탭", () => {
  it("칸 넷의 금액과 계산서 전 소계가 화면에 뜬다", async () => {
    render(<PipelineTab />);
    expect(await screen.findByText("2,907,817원")).toBeTruthy();    // ①확인대기
    expect(await screen.findByText("12,895,305원")).toBeTruthy();   // ②발송대기
    expect(await screen.findByText("9,373,788원")).toBeTruthy();    // ③입고대기
    expect(await screen.findByText("116,587,510원")).toBeTruthy();  // ④지급대기
    expect(await screen.findByText("25,176,910원")).toBeTruthy();   // 소계
  });

  it("미해명 덩어리가 「확정 아님」과 함께 소계 밖 별도 줄로 뜬다", async () => {
    render(<PipelineTab />);
    // ★소계에 섞이면 이 금액이 화면에서 사라지거나 소계와 합쳐진다 — 별도로 보이는 것이 계약이다
    expect(await screen.findByText("8,939,475원")).toBeTruthy();
    expect(await screen.findByText("확정 아님 — 구별 불가")).toBeTruthy();
    expect(await screen.findByText(/발주일 2025-07-23 ~ 2026-07-27/)).toBeTruthy();
  });

  it("발송 없이 닫힌 분이 별도 줄로 뜬다", async () => {
    render(<PipelineTab />);
    expect(await screen.findByText("55,200원")).toBeTruthy();
    expect(await screen.findByText(/확정했는데 발송 없이 닫힘/)).toBeTruthy();
  });

  it("칸마다 «지금 상태 모름» 금액과 「이후 다시 안 봄」이 갈려 보인다", async () => {
    render(<PipelineTab />);
    // ★문구 규칙(2026-08-28 Jino 지시): 「굳음」이 아니라 «모른다»를 말하고,
    //   신선도 축에 「확인」을 쓰지 않는다(이 화면에서 「확인」은 사람이 누르는 동작이다).
    expect(await screen.findByText(/지금 상태 모름 2,242,502원/)).toBeTruthy();
    expect(await screen.findByText(/2026-06-18 이후 다시 안 봄/)).toBeTruthy();
    // 모르는 게 없는 ③은 그렇게 말한다(빈칸으로 두지 않는다)
    expect(await screen.findByText("전액 오늘 수집분")).toBeTruthy();
    // 옛 문구가 되살아나면 죽는다
    expect(screen.queryByText(/굳음/)).toBeNull();
    expect(screen.queryByText(/이후 미확인/)).toBeNull();
  });

  it("보정(clamp·ASN 결손)이 자백 줄로 뜬다", async () => {
    render(<PipelineTab />);
    expect(await screen.findByText(/보정·결손 자백/)).toBeTruthy();
    expect(await screen.findByText(/입고>발송 보정 2건/)).toBeTruthy();
    expect(await screen.findByText(/발송 기록 없이 입고된 발주 2건/)).toBeTruthy();
  });

  it("보정이 0건이면 자백 줄을 만들지 않는다", async () => {
    h.pipeline = pipelineFixture({
      clamp: {
        over_shipped: { po_count: 0, amount: "0" },
        over_received: { po_count: 0, amount: "0" },
        asn_missing: { po_count: 0, received_amount: "0" },
      },
    });
    render(<PipelineTab />);
    await screen.findByText("25,176,910원");
    expect(screen.queryByText(/보정·결손 자백/)).toBeNull();
  });

  it("신선도(마지막 수집·최신 발송일)와 그 한계 문구가 뜬다", async () => {
    render(<PipelineTab />);
    // ^ 앵커 — 아래 한계 문구에도 「최신 발송일」이 들어 있어 그냥 찾으면 둘이 잡힌다.
    expect(await screen.findByText(/^최신 발송일/)).toBeTruthy();
    expect(await screen.findByText(/^마지막 수집/)).toBeTruthy();
    expect(await screen.findByText(/「발송이 없었다」인지/)).toBeTruthy();
  });

  it("칸을 누르면 발주 목록이 열리고 «이 칸 금액»이 행에 보인다", async () => {
    render(<PipelineTab />);
    fireEvent.click(await screen.findByText("③ 입고 대기"));
    // ★잔여(이 칸 금액) 9,540원이 화면에 나야 한다 — 이 단언이 «표면 절단» 변이를 잡는 자리다
    expect(await screen.findByText("9,540원")).toBeTruthy();
    expect(await screen.findByText("139899792")).toBeTruthy();
  });

  it("발송 기록이 없는 행은 0원이 아니라 「기록 없음」으로 뜬다", async () => {
    h.stageRows = {
      stage: "closed_unshipped", total_count: 1,
      rows: [row({ asn_missing: true, shipped_amount: "0", shipped_qty: 0, stage_amount: "0" })],
      truncated: false, last_collection_date_kst: "2026-08-27",
    };
    render(<PipelineTab />);
    fireEvent.click(await screen.findByText("③ 입고 대기"));
    expect(await screen.findByText("기록 없음")).toBeTruthy();
  });

  it("발송 기록이 없는 행은 계산에 쓰인 «입고 기준» 하한을 함께 낸다", async () => {
    // ★적대 리뷰 1R P2-1 — 초판은 이 값을 계산만 하고 화면 어디서도 안 썼다(변이 M2 생존).
    //   「기록 없음」만 보이면 사람이 「0원어치 보냈다」로 읽는다.
    h.stageRows = {
      stage: "closed_unshipped", total_count: 1,
      rows: [row({ asn_missing: true, shipped_amount: "0", shipped_qty: 0,
                   received_amount: "161100", effective_shipped_amount: "161100", stage_amount: "0" })],
      truncated: false, last_collection_date_kst: "2026-08-27",
    };
    render(<PipelineTab />);
    fireEvent.click(await screen.findByText("③ 입고 대기"));
    expect(await screen.findByText("기록 없음")).toBeTruthy();
    expect(await screen.findByText(/입고 기준 161,100원/)).toBeTruthy();
  });

  it("모르는 상태 코드가 있으면 «판정 불가»로 자백한다", async () => {
    // ★적대 리뷰 1R P2-2 — 모르는 상태를 미종결로 접으면 그 돈이 ②③에 조용히 섞인다.
    h.pipeline = pipelineFixture({
      unknown_status: { po_count: 2, confirmed_amount: "123456", codes: ["XX", "YY"] },
    });
    render(<PipelineTab />);
    expect(await screen.findByText(/모르는 상태 코드\(XX, YY\)인 발주 2건/)).toBeTruthy();
    expect(await screen.findByText(/판정 불가입니다/)).toBeTruthy();
  });

  it("목록이 잘리면 잘렸다고 화면이 말한다", async () => {
    h.stageRows = { stage: "await_ship", total_count: 500, rows: [row()], truncated: true,
                    last_collection_date_kst: "2026-08-27" };
    render(<PipelineTab />);
    fireEvent.click(await screen.findByText("② 발송 대기"));
    expect(await screen.findByText(/전체 500건 중 1건만 표시/)).toBeTruthy();
  });

  it("발송일 창은 ③을 열 때만 실려 나간다", async () => {
    render(<PipelineTab />);
    fireEvent.change(await screen.findByLabelText("발송일 시작"), { target: { value: "2026-08-20" } });
    fireEvent.click(await screen.findByText("② 발송 대기"));
    // ★②는 발송일 축이 아니다 — 창을 실으면 「지금 보낼 것」이 발송일로 잘려 사라진다
    const shipCall = h.stageCalls.find((c) => c.stage === "await_ship");
    expect(shipCall?.shipFrom).toBeFalsy();
    fireEvent.click(await screen.findByText("③ 입고 대기"));
    const recvCall = h.stageCalls.find((c) => c.stage === "await_receive");
    expect(recvCall?.shipFrom).toBe("2026-08-20");
  });

  it("창을 걸면 「금액은 발주 전체 기준」과 창 밖 발송 섞임 건수를 자백한다", async () => {
    h.pipeline = pipelineFixture({
      ship_window: { from: "2026-08-20", to: "2026-08-27", applies_to: "await_receive",
                     po_with_out_of_window_shipment: 3 },
    });
    render(<PipelineTab />);
    expect(await screen.findByText(/창 밖 발송이 섞인 발주 3건이 포함/)).toBeTruthy();
  });

  it("조회 실패를 「걸린 돈 없음」으로 위장하지 않는다", async () => {
    h.pipeline = Promise.reject(new Error("boom")) as unknown;
    render(<PipelineTab />);
    expect(await screen.findByText(/파이프라인을 불러오지 못했습니다/)).toBeTruthy();
  });
});

// ══════════════════════════════════════════════════════════════
// S2 「확인요청함」
// ══════════════════════════════════════════════════════════════
function riRow(over: Record<string, unknown> = {}) {
  return {
    ...row(),
    purchase_order_seq: 139791428, status: "RI", status_label: "거래명세서확인요청",
    po_date: "2026-08-18", receiving_finished_date: "2026-08-26",
    received_amount: "230235", invoice_seqs: [30871641],
    invoices: [{
      invoice_seq: 30871641, issue_date: "2026-08-26", payment_date: "2026-10-23",
      tax_invoice_confirmed_date: "2026-08-27", tax_invoice_transmitted: true,
      payment_amount: "404745",
    }],
    invoice_rows_missing: [], is_stale: false, ...over,
  };
}

describe("확인요청함 탭", () => {
  beforeEach(() => {
    h.riQueue = {
      rows: [
        // ★라이브 행은 «계산서 없음»으로 둔다 — 2026-09-06부터 「지금 확인이 필요한 건」의
        //   금액은 계산서가 아직 안 나간 것만 센다(Jino 지시). 계산서 붙은 라이브 행은
        //   아래 「계산서 이미 발행」 구역으로 내려간다.
        riRow({ invoice_seqs: [], has_invoice: false, invoices: [] }),
        riRow({
          purchase_order_seq: 115340779, po_date: "2025-10-15", received_amount: "75430",
          synced_date: "2026-08-05", is_stale: true, invoice_seqs: [27442270],
          invoices: [{
            invoice_seq: 27442270, issue_date: "2025-10-22", payment_date: "2025-12-19",
            tax_invoice_confirmed_date: "2025-10-23", tax_invoice_transmitted: true,
            payment_amount: "6733440",
          }],
        }),
      ],
      live_count: 1, live_amount: "230235",
      live_no_invoice_count: 1, live_no_invoice_amount: "230235",
      live_invoiced_count: 0, live_invoiced_amount: "0",
      stale_count: 1, stale_amount: "75430",
      last_collection_date_kst: "2026-08-27",
      note: "「지금 상태를 모르는 건」은 최근 수집에서 다시 보지 못한 발주입니다.",
    };
  });

  it("살아 있는 건과 «지금 상태를 모르는» 건이 다른 섹션으로 갈려 뜬다", async () => {
    render(<RiQueueTab />);
    // ★한 표에 섞으면 죽은 줄을 누른다 — 섹션 제목 둘이 이 갈림의 표면이다
    expect(await screen.findByText(/지금 확인이 필요한 건 — 1건 · 230,235원/)).toBeTruthy();
    expect(await screen.findByText(/지금 상태를 모르는 건 — 1건 · 75,430원/)).toBeTruthy();
    // ★2026-08-28: 발주번호가 «표»와 «supplier 붙여넣기 줄» 두 곳에 뜬다(의도된 중복).
    //   이 테스트가 지키는 것은 「표에 그 행이 있나」이므로 표 안으로 범위를 좁힌다 —
    //   전역 findByText로 두면 붙여넣기 줄이 생길 때마다 이 단언이 애매해진다.
    const inTables = (seq: string) =>
      screen.getAllByText(seq).filter((el) => el.closest("table") !== null);
    await screen.findAllByText("139791428");
    expect(inTables("139791428").length).toBe(1);
    expect(inTables("115340779").length).toBe(1);
  });

  it("모르는 건에는 「이후 다시 안 봄」 배지와 재수집 안내가 붙는다", async () => {
    render(<RiQueueTab />);
    expect(await screen.findByText("2026-08-05 이후 다시 안 봄")).toBeTruthy();
    // ★고치는 법이 화면에 있어야 한다 — 「모른다」만 말하고 끝내면 사람이 할 게 없다.
    expect(await screen.findByText(/미종결 발주 재수집」을 한 번 돌리면/)).toBeTruthy();
    // ★그리고 «남은 금액»이 아니라 «모르는 금액»이라는 것도 화면이 말한다(Jino 오독 지점).
    expect(await screen.findByText(/모르는 금액/)).toBeTruthy();
  });

  it("지급일이 지난 계산서는 «지급일 경과»로 죽은 근거를 화면이 말한다", async () => {
    render(<RiQueueTab />);
    // ★유령 8건의 정체가 이것이었다(지급까지 끝난 건). 근거를 안 쓰면 왜 죽었는지 모른다
    expect(await screen.findByText("지급일 경과")).toBeTruthy();
  });

  it("파이프라인 합계에 안 들어간다는 사실을 화면이 말한다", async () => {
    render(<RiQueueTab />);
    expect(await screen.findByText(/같은 돈을 두 번 셉니다/)).toBeTruthy();
  });

  it("정산행 미수집은 「미발행」이 아니라 「모름」으로 쓴다", async () => {
    h.riQueue = {
      rows: [riRow({ invoices: [], invoice_rows_missing: [999999] })],
      live_count: 1, live_amount: "230235",
      // ★번호는 있으니 «발행된 쪽»이다 — 「미수집」을 「미발행」으로 넘기지 않는다(원칙22)
      live_no_invoice_count: 0, live_no_invoice_amount: "0",
      live_invoiced_count: 1, live_invoiced_amount: "230235",
      stale_count: 0, stale_amount: "0",
      last_collection_date_kst: "2026-08-27", note: "",
    };
    render(<RiQueueTab />);
    expect(await screen.findByText(/정산행 미수집 999999 — 「미발행」이 아니라 「모름」/)).toBeTruthy();
  });

  // ══════════════════════════════════════════════════════════════
  // ★계산서 발행분을 제목 금액에서 뺀다 (Jino 지시 2026-09-06)
  //   실측이 만든 요구다: 라이브 12건 4,536,216원 중 계산서 보유는 4건 3,038,131원뿐이었고,
  //   섞인 제목은 이미 ④지급대기에 있는 돈까지 「지금 확인이 필요한 돈」으로 보이게 했다.
  // ══════════════════════════════════════════════════════════════
  describe("계산서 발행분 분리", () => {
    beforeEach(() => {
      h.riQueue = {
        rows: [
          riRow({ purchase_order_seq: 141005728, received_amount: "51300",
            invoice_seqs: [], has_invoice: false, invoices: [] }),
          riRow({ purchase_order_seq: 141004734, received_amount: "309515",
            invoice_seqs: [], has_invoice: false, invoices: [] }),
          riRow({ purchase_order_seq: 141475041, received_amount: "861600" }),
        ],
        live_count: 3, live_amount: "1222415",
        live_no_invoice_count: 2, live_no_invoice_amount: "360815",
        live_invoiced_count: 1, live_invoiced_amount: "861600",
        stale_count: 0, stale_amount: "0",
        last_collection_date_kst: "2026-09-06", note: "",
      };
    });

    it("제목의 금액·건수가 계산서 발행분을 뺀 값이다", async () => {
      render(<RiQueueTab />);
      expect(await screen.findByText(/지금 확인이 필요한 건 — 2건 · 360,815원/)).toBeTruthy();
      // ★섞인 옛 값이 남아 있으면 안 된다 — 이 부정 단언이 이번 변경의 핵심 표면이다
      expect(screen.queryByText(/지금 확인이 필요한 건 — 3건 · 1,222,415원/)).toBeNull();
    });

    it("붙여넣기 줄에 계산서 발행분이 안 섞인다", async () => {
      render(<RiQueueTab />);
      const line = await screen.findByTestId("po-seq-copy-live");
      expect(line.textContent).toBe("141005728, 141004734");
      // 발행분은 «지워지지» 않고 자기 줄을 갖는다 — 누르는 일은 여전히 남아 있다
      expect((await screen.findByTestId("po-seq-copy-invoiced")).textContent).toBe("141475041");
    });

    it("계산서 발행분은 «지워지지» 않고 별도 구역으로 내려간다", async () => {
      render(<RiQueueTab />);
      expect(await screen.findByText(/계산서 이미 발행 — 1건 · 861,600원/)).toBeTruthy();
      // ★「목록엔 따로 표시」(Jino 지시) — 금액에서 뺐다고 행까지 사라지면 그 건이 영영
      //   RI로 남아도 화면이 아무 말도 안 하게 된다.
      const inTables = (seq: string) =>
        screen.getAllByText(seq).filter((el) => el.closest("table") !== null);
      expect(inTables("141475041").length).toBe(1);
      expect(await screen.findByText(/거래명세서확인은 아직 남은 일/)).toBeTruthy();
    });

    it("두 덩어리의 합이 라이브 전체와 맞는지 화면이 스스로 검산한다", async () => {
      render(<RiQueueTab />);
      expect(await screen.findByText(/검산: 계산서 없음 2건 \+ 이미 발행 1건 = 오늘 본 확인요청 3건/))
        .toBeTruthy();
    });

    // ★적대 리뷰 P2-4(변이 F4 SURVIVED): 가르는 자를 `invoices.length`로 바꿔도 종전
    //   프론트 테스트 50건이 전부 통과했다. 백엔드엔 같은 자를 못 박은 테스트가 있는데
    //   프론트엔 없어서, 「같은 규칙이 두 곳에 살고 한쪽만 고쳐지는」 위험이 열려 있었다.
    it("정산행을 못 받아온 건은 「계산서 없음」으로 새지 않는다", async () => {
      h.riQueue = {
        rows: [
          // 계산서 번호는 붙었는데 정산행이 아직 없다 → 「발행된 쪽」이다(원칙22)
          riRow({ purchase_order_seq: 141477039, received_amount: "688620",
            invoices: [], invoice_rows_missing: [31012484] }),
          riRow({ purchase_order_seq: 141005728, received_amount: "51300",
            invoice_seqs: [], has_invoice: false, invoices: [] }),
        ],
        live_count: 2, live_amount: "739920",
        live_no_invoice_count: 1, live_no_invoice_amount: "51300",
        live_invoiced_count: 1, live_invoiced_amount: "688620",
        stale_count: 0, stale_amount: "0",
        last_collection_date_kst: "2026-09-06", note: "",
      };
      render(<RiQueueTab />);
      // 붙여넣기 줄이 곧 «어느 쪽으로 갈렸나»의 표면이다
      expect((await screen.findByTestId("po-seq-copy-live")).textContent).toBe("141005728");
      expect((await screen.findByTestId("po-seq-copy-invoiced")).textContent).toBe("141477039");
    });

    // ★적대 리뷰 P2-5(변이 F10 SURVIVED): 상단 배너를 옛 «전건 단정» 문장으로 통째 되돌려도
    //   50건이 전부 통과했다 — 유일한 단언이 신·구 문장에 공통인 부분문자열이었다.
    it("상단 배너가 「RI는 전부 계산서가 나갔다」고 단정하지 않는다", async () => {
      render(<RiQueueTab />);
      // ★문구가 <b>로 쪼개져 있어 findByText로는 못 잡는다 — 배너 «전체 텍스트»를 본다.
      const banner = await screen.findByTestId("ri-queue-banner");
      expect(banner.textContent).toMatch(/계산서가 이미 발행된 1건은 아래/);
      expect(banner.textContent).toMatch(/빼고 별도 구역에 모아 뒀습니다/);
      // 옛 전건 단정이 되살아나면 여기서 걸린다
      expect(banner.textContent).not.toMatch(/이 건들은 계산서가 이미 나가/);
    });

    // ★적대 리뷰 P2-6: 검산은 「계산서 이미 발행」 카드가 없어도 떠야 한다 —
    //   갈림이 무너지는 회귀에서 카드와 함께 사라지면 아무도 못 본다.
    it("발행분이 0건이어도 검산 줄은 남는다", async () => {
      h.riQueue = {
        rows: [riRow({ purchase_order_seq: 141005728, received_amount: "51300",
          invoice_seqs: [], has_invoice: false, invoices: [] })],
        live_count: 1, live_amount: "51300",
        live_no_invoice_count: 1, live_no_invoice_amount: "51300",
        live_invoiced_count: 0, live_invoiced_amount: "0",
        stale_count: 0, stale_amount: "0",
        last_collection_date_kst: "2026-09-06", note: "",
      };
      render(<RiQueueTab />);
      expect(screen.queryByText(/계산서 이미 발행 —/)).toBeNull();
      const ck = await screen.findByTestId("ri-live-split-checksum");
      expect(ck.textContent).toMatch(/계산서 없음 1건 \+ 이미 발행 0건 = 오늘 본 확인요청 1건/);
    });

    it("계산서 없는 건이 0이어도 「할 일 없음」으로 위장하지 않는다", async () => {
      h.riQueue = {
        rows: [riRow({ purchase_order_seq: 141475041, received_amount: "861600" })],
        live_count: 1, live_amount: "861600",
        live_no_invoice_count: 0, live_no_invoice_amount: "0",
        live_invoiced_count: 1, live_invoiced_amount: "861600",
        stale_count: 0, stale_amount: "0",
        last_collection_date_kst: "2026-09-06", note: "",
      };
      render(<RiQueueTab />);
      // ★0건을 「누를 것 없음」이라고 쓰면 남은 1건이 화면에서 조용히 사라진다
      expect(await screen.findByText(/아래 「계산서 이미 발행」 구역은 여전히 눌러야 할 건/))
        .toBeTruthy();
    });
  });

  it("조회 실패를 「확인할 것 없음」으로 위장하지 않는다", async () => {
    h.riQueue = Promise.reject(new Error("boom")) as unknown;
    render(<RiQueueTab />);
    expect(await screen.findByText(/확인요청 목록을 불러오지 못했습니다/)).toBeTruthy();
  });
});
