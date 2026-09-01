// @vitest-environment jsdom
//
// naverAdProposalForm.test.tsx — 발의 폼이 «사람에게 닿는가»
// (D-NAO-283 · 계약 `CONTRACT_pao_purpose_and_hands.md` P2-ⓒ)
//
// ## 왜 이 테스트가 있어야 하나
// 계약 §6 P2가 지목한 표면은 「콘솔 발의 폼 → 승인 → 실행」이고, §3 P2 ★v9는
// 「이 유형은 엔진만 발의합니다」를 **화면에 표기**하라고 못 박았다(조용한 실패 금지).
//
// ★그래서 이 파일이 재는 것은 「API가 값을 준다」가 아니라 **그 값이 화면 텍스트가 되는가**다.
//   n=77이 값을 치른 교훈 #380이 정확히 이 자리다 — 백엔드 테스트는 `SPECS[key].warn`(상수)을
//   보고, 프론트 테스트는 손으로 쓴 fixture를 먹어서, **둘을 잇는 한 줄만 아무도 안 지켰다.**
//   그러므로 여기서는 fixture를 손으로 쓰되 **응답 필드를 지우는 변이가 반드시 빨개지도록**
//   「그 필드에서 온 문자열」을 화면에서 찾는다(필드 존재만 검사하지 않는다).
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";

const h = vi.hoisted(() => ({
  types: null as unknown,
  createImpl: null as unknown,
  createCalls: [] as unknown[],
}));

vi.mock("../lib/api", () => ({
  fetchNaverProposableTypes: () => Promise.resolve(h.types),
  createNaverProposal: (input: unknown) => {
    h.createCalls.push(input);
    return (h.createImpl as (i: unknown) => Promise<unknown>)(input);
  },
}));

import NaverAdProposalForm from "./NaverAdProposalForm";

const TYPES = {
  proposable: [
    { proposal_type: "bid_down", action: "update_bid", direction: "down" as const },
    { proposal_type: "bid_up", action: "update_bid", direction: "up" as const },
    { proposal_type: "negative_keyword", action: "add_negative_keyword", direction: null },
    { proposal_type: "search_term_exclude", action: "exclude_search_term", direction: null },
  ],
  engine_only: [
    {
      proposal_type: "bid_up_explore",
      reason: "이 유형은 엔진만 발의합니다 — 탐색 스텝(bid_up_explore)은 explore_op 승인원과 쌍방향으로 잠겨 있고 사람 발의로는 그 상한 근거가 없다",
    },
    {
      proposal_type: "bid_up_cold",
      reason: "이 유형은 엔진만 발의합니다 — 콜드 첫 입찰(bid_up_cold)은 ±15% 변경폭이 완전 면제라 cold_op 승인원 전용이다",
    },
  ],
  open_actions: ["add_negative_keyword", "exclude_search_term", "set_user_lock", "update_bid", "update_budget"],
};

function setup(overrides: Partial<typeof TYPES> = {}) {
  h.types = { ...TYPES, ...overrides };
  h.createCalls = [];
  h.createImpl = (input: unknown) =>
    Promise.resolve({
      id: 4242,
      status: "pending",
      proposal_type: (input as { proposalType: string }).proposalType,
    });
  return render(<NaverAdProposalForm />);
}

async function openForm() {
  fireEvent.click(await screen.findByRole("button", { name: "발의하기" }));
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("발의 폼 — 유형 목록이 백엔드에서 온다", () => {
  it("발의 가능 유형이 선택지로 뜬다", async () => {
    setup();
    await openForm();

    const select = await screen.findByLabelText("발의 유형");
    const options = Array.from(select.querySelectorAll("option")).map((o) => o.textContent);
    expect(options).toEqual([
      "bid_down (내림)",
      "bid_up (올림)",
      "negative_keyword",
      "search_term_exclude",
    ]);
  });

  it("★배선 절단 변이: 응답의 proposable을 비우면 선택지가 사라진다 — 화면이 목록을 자체 보유하지 않는다", async () => {
    setup({ proposable: [] });
    await openForm();

    const select = await screen.findByLabelText("발의 유형");
    expect(select.querySelectorAll("option")).toHaveLength(0);
  });
});

describe("★조용한 실패 금지 — 엔진 전용 유형과 «사유»가 화면에 있다", () => {
  it("엔진 전용 유형의 사유 문장이 그대로 렌더된다", async () => {
    setup();
    await openForm();

    fireEvent.click(await screen.findByText(/엔진만 발의하는 유형 2종 — 왜 여기 없나/));
    // ★응답의 reason 문자열 «그 자체»를 찾는다. 프론트가 자기 문구를 지어내면 실패한다.
    expect(screen.getByText(/explore_op 승인원과 쌍방향으로 잠겨 있고/)).toBeTruthy();
    expect(screen.getByText(/±15% 변경폭이 완전 면제라 cold_op 승인원 전용/)).toBeTruthy();
  });

  it("★배선 절단 변이: 응답에서 reason을 지우면 사유가 화면에서 사라진다", async () => {
    setup({
      engine_only: [{ proposal_type: "bid_up_explore", reason: "" }],
    });
    await openForm();
    fireEvent.click(await screen.findByText(/엔진만 발의하는 유형 1종/));

    expect(screen.queryByText(/explore_op 승인원과 쌍방향으로 잠겨 있고/)).toBeNull();
  });

  it("엔진 전용 목록이 비면 그 절 자체가 없다 — 빈 껍데기 금지", async () => {
    setup({ engine_only: [] });
    await openForm();
    expect(screen.queryByText(/왜 여기 없나/)).toBeNull();
  });
});

describe("발의 — 입력이 API 호출로 이어진다", () => {
  it("입찰 유형이면 목표 입찰가 칸이 뜨고 값이 실려 나간다", async () => {
    setup();
    await openForm();

    fireEvent.change(await screen.findByLabelText("발의 유형"), { target: { value: "bid_up" } });
    fireEvent.change(screen.getByLabelText("대상 ID"), { target: { value: "nkw-9" } });
    fireEvent.change(screen.getByLabelText("캠페인 ID"), { target: { value: "cmp-1" } });
    fireEvent.change(screen.getByLabelText("목표 입찰가"), { target: { value: "1400" } });
    fireEvent.change(screen.getByLabelText("근거"), { target: { value: "정착 클릭 42 · ROAS 3.1 — 상향 여력" } });
    fireEvent.click(screen.getByRole("button", { name: "발의" }));

    await waitFor(() => expect(h.createCalls).toHaveLength(1));
    expect(h.createCalls[0]).toMatchObject({
      proposalType: "bid_up",
      targetType: "keyword",
      targetId: "nkw-9",
      campaignId: "cmp-1",
      targetBid: 1400,
      rationale: "정착 클릭 42 · ROAS 3.1 — 상향 여력",
    });
  });

  it("제외 계열이면 목표 입찰가 칸이 없다 — 그 유형엔 없는 개념이다", async () => {
    setup();
    await openForm();

    fireEvent.change(await screen.findByLabelText("발의 유형"), { target: { value: "negative_keyword" } });
    expect(screen.queryByLabelText("목표 입찰가")).toBeNull();
  });

  it("성공하면 «발의됨 · 승인은 별도»가 화면에 뜬다 — 발의를 승인으로 오독시키지 않는다", async () => {
    setup();
    await openForm();

    fireEvent.change(screen.getByLabelText("대상 ID"), { target: { value: "무관검색어" } });
    fireEvent.change(screen.getByLabelText("캠페인 ID"), { target: { value: "cmp-1" } });
    fireEvent.change(screen.getByLabelText("광고그룹 ID"), { target: { value: "grp-1" } });
    fireEvent.change(screen.getByLabelText("근거"), { target: { value: "전환 0 · 비용 12,000원" } });
    fireEvent.click(screen.getByRole("button", { name: "발의" }));

    const msg = await screen.findByText(/제안 #4242 발의됨/);
    expect(msg.textContent).toContain("승인·실행은 별도 Confirm");
  });

  it("★서버 거부 사유가 «그대로» 화면에 뜬다 — 프론트가 사유를 지어내지 않는다", async () => {
    setup();
    h.createImpl = () =>
      Promise.reject(new Error("실행 불가 구조라 발의를 거부한다 — adgroup_id 없음 — 실행 대상 정보 부족"));
    await openForm();

    fireEvent.change(screen.getByLabelText("대상 ID"), { target: { value: "무관검색어" } });
    fireEvent.change(screen.getByLabelText("캠페인 ID"), { target: { value: "cmp-1" } });
    fireEvent.change(screen.getByLabelText("근거"), { target: { value: "전환 0" } });
    fireEvent.click(screen.getByRole("button", { name: "발의" }));

    expect(await screen.findByText(/adgroup_id 없음 — 실행 대상 정보 부족/)).toBeTruthy();
  });

  it("근거 없이 제출하면 브라우저 필수 검증에 막혀 API가 안 불린다", async () => {
    setup();
    await openForm();

    fireEvent.change(screen.getByLabelText("대상 ID"), { target: { value: "무관검색어" } });
    fireEvent.change(screen.getByLabelText("캠페인 ID"), { target: { value: "cmp-1" } });
    fireEvent.click(screen.getByRole("button", { name: "발의" }));

    expect(h.createCalls).toHaveLength(0);
  });

  it("★배선 절단 변이: onCreated 콜백이 끊기면 부모가 목록을 다시 못 읽는다", async () => {
    h.types = TYPES;
    h.createCalls = [];
    h.createImpl = () => Promise.resolve({ id: 7, status: "pending", proposal_type: "negative_keyword" });
    const onCreated = vi.fn();
    render(<NaverAdProposalForm onCreated={onCreated} />);
    await openForm();

    fireEvent.change(screen.getByLabelText("대상 ID"), { target: { value: "무관검색어" } });
    fireEvent.change(screen.getByLabelText("캠페인 ID"), { target: { value: "cmp-1" } });
    fireEvent.change(screen.getByLabelText("광고그룹 ID"), { target: { value: "grp-1" } });
    fireEvent.change(screen.getByLabelText("근거"), { target: { value: "전환 0" } });
    fireEvent.click(screen.getByRole("button", { name: "발의" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
    expect(onCreated.mock.calls[0][0]).toMatchObject({ id: 7 });
  });
});
