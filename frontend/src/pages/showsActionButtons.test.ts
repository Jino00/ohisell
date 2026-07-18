// showsActionButtons.test.ts — 정보성 제안 카드는 액션 버튼을 렌더하지 않는다(D-NAO-53-b).
// ★존재 이유: 정보성 제안(p.informational=true)은 실행 대상이 없어 승인해도 no-op이고
//   (harness "정보성 제안 — 실행 대상 아님"), 자동 만료(D+1~D+3)가 있어 방치가 정상이며,
//   소급 채점도 status를 읽지 않는다. 승인/반려/실행/재승인 버튼은 실행형 카드와 렌더를
//   공유하다 따라 나온 흔적이므로, 이 predicate가 informational=true일 때 false를 고정한다.
import { describe, it, expect } from "vitest";
import { showsActionButtons } from "./NaverAdOptimizationConsole";
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
    decision_only: false,
    action: null,
    expert_verdict: null,
    executable: true,
    not_executable_reason: null,
    approval_source: null,
    ...overrides,
  };
}

describe("showsActionButtons — informational 파생값으로만 판단(proposal_type 재분류 금지)", () => {
  it("informational: true → false (버튼 렌더 안 함)", () => {
    expect(showsActionButtons(proposal({ informational: true }))).toBe(false);
  });

  it("informational: false → true (버튼 렌더함)", () => {
    expect(showsActionButtons(proposal({ informational: false }))).toBe(true);
  });

  it("status(pending/approved/failed)와 무관하게 informational=true면 항상 false", () => {
    expect(showsActionButtons(proposal({ informational: true, status: "pending" }))).toBe(false);
    expect(showsActionButtons(proposal({ informational: true, status: "approved" }))).toBe(false);
    expect(showsActionButtons(proposal({ informational: true, status: "failed" }))).toBe(false);
  });
});
