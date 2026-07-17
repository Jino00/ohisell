// executeConfirmText.test.ts — 실행 Confirm 문안 드리프트 가드.
// ★존재 이유: 예전엔 이 문안이 액션 무관하게 항상 "제외키워드를 등록합니다"였다 —
//   입찰가·정지·예산을 집행하는데도 화면엔 "제외키워드"라고 떴다(치명적 오사칭).
//   이제 p.action(백엔드 파생값)으로 분기한다. 이 테스트가 각 액션의 핵심 동사와,
//   ★미지 액션 폴백이 "제외키워드"를 사칭하지 않음을 고정한다(재발 방지 핵심).
import { describe, it, expect } from "vitest";
import { executeConfirmText } from "./NaverAdOptimizationConsole";
import type { NaverAdProposal } from "../lib/api";

function proposal(overrides: Partial<NaverAdProposal>): NaverAdProposal {
  return {
    id: 1,
    created_at: null,
    proposal_type: "bid_up",
    target_type: "keyword",
    target_id: "nkw-1",
    campaign_id: "cmp-1",
    adgroup_id: "grp-1",
    rationale: null,
    expected_effect: null,
    status: "pending",
    slack_ts: null,
    executed_change_log_id: null,
    target_bid: null,
    target_lock: null,
    target_budget: null,
    budget_auto_eligible: null,
    informational: false,
    action: null,
    expert_verdict: null,
    executable: true,
    not_executable_reason: null,
    approval_source: null,
    ...overrides,
  };
}

describe("executeConfirmText — p.action으로 분기(문자열 재추론 금지)", () => {
  it("add_negative_keyword → '제외키워드'", () => {
    const text = executeConfirmText(proposal({ action: "add_negative_keyword", proposal_type: "negative_keyword", target_id: "무료배송" }));
    expect(text).toContain("제외키워드");
    expect(text).toContain("무료배송");
    expect(text).toContain("실행할까요?");
  });

  it("update_bid → '입찰가' + 목표값", () => {
    const text = executeConfirmText(proposal({ action: "update_bid", proposal_type: "bid_up", target_bid: 1720 }));
    expect(text).toContain("입찰가");
    expect(text).toContain("1720원");
    expect(text).not.toContain("제외키워드");
  });

  it("update_bid target_bid null → 값 없음 정직 표기", () => {
    const text = executeConfirmText(proposal({ action: "update_bid", proposal_type: "bid_up", target_bid: null }));
    expect(text).toContain("입찰가");
    expect(text).toContain("값 없음");
  });

  it("set_user_lock target_lock=true → '정지'", () => {
    const text = executeConfirmText(proposal({ action: "set_user_lock", proposal_type: "pause", target_lock: true }));
    expect(text).toContain("정지");
    expect(text).not.toContain("재개합니다");
    expect(text).not.toContain("제외키워드");
  });

  it("set_user_lock target_lock=false → '재개'", () => {
    const text = executeConfirmText(proposal({ action: "set_user_lock", proposal_type: "resume", target_lock: false }));
    expect(text).toContain("재개");
    expect(text).not.toContain("제외키워드");
  });

  it("update_budget → '일예산' + 목표값", () => {
    const text = executeConfirmText(proposal({ action: "update_budget", proposal_type: "budget_up", target_type: "campaign", target_budget: 50000 }));
    expect(text).toContain("일예산");
    expect(text).toContain("50000원");
    expect(text).not.toContain("제외키워드");
  });

  it("★미지 액션(null 포함) 폴백은 '제외키워드'를 사칭하지 않는다", () => {
    const nullAction = executeConfirmText(proposal({ action: null, proposal_type: "some_future_type" }));
    expect(nullAction).not.toContain("제외키워드");
    expect(nullAction).toContain("some_future_type");
    expect(nullAction).toContain("실행할까요?");

    const unknownAction = executeConfirmText(proposal({ action: "totally_new_action", proposal_type: "future_thing" }));
    expect(unknownAction).not.toContain("제외키워드");
    expect(unknownAction).toContain("future_thing");
  });
});
