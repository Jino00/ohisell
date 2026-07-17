// approvePreConfirmText.test.ts — 승인 preConfirm 문안 드리프트 가드(D-NAO-54 P4).
// ★존재 이유: 결정 전용 제안(param_change, decision_only=true)은 승인해도 자동 적용이 없다 —
//   "기록만 되며 자동 적용되지 않습니다"를 문안이 못 박아야 Jino가 오해 없이 결정한다.
//   ★백엔드 파생값(p.decision_only)만으로 분기(proposal_type 재분류 금지 — D-NAO-53 패턴).
import { describe, it, expect } from "vitest";
import { approvePreConfirmText } from "./NaverAdOptimizationConsole";
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

describe("approvePreConfirmText — decision_only 파생값으로 분기", () => {
  it("decision_only: true → '기록만·자동 적용 없음' 문안", () => {
    const text = approvePreConfirmText(proposal({ decision_only: true, proposal_type: "param_change" }));
    expect(text).toContain("기록만");
    expect(text).toContain("자동 적용");
  });

  it("decision_only: false(실행형) → 기존 '실행은 별도 Confirm' 문안", () => {
    const text = approvePreConfirmText(proposal({ decision_only: false }));
    expect(text).toContain("실행은 별도 Confirm");
    expect(text).not.toContain("기록만");
  });
});
