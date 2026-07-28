// timelineRules.test.ts — ⑥개선 타임라인 순수 로직 가드(D-NAO-104 Phase 3).
import { describe, it, expect } from "vitest";
import { confidenceLabel, observationBadge, deltaText, partialDataNote } from "./timelineRules";
import type { NaverPerformanceTimelineImpact, NaverPerformanceTimelineWindow } from "./api";

describe("confidenceLabel — 신뢰도 꼬리표", () => {
  it("assumed는 적용 시점 추정", () => {
    expect(confidenceLabel("assumed")).toBe("적용 시점 추정");
  });

  it("unknown은 시점 불명", () => {
    expect(confidenceLabel("unknown")).toBe("시점 불명");
  });

  it("commit은 코드 반영일 기준(첫 커밋 날짜일 뿐 실제 적용 시각이 아니다)", () => {
    expect(confidenceLabel("commit")).toBe("코드 반영일 기준");
  });

  it("log는 꼬리표 없음(실제 기록 시각)", () => {
    expect(confidenceLabel("log")).toBe("");
  });
});

function makeImpact(overrides: Partial<NaverPerformanceTimelineImpact> = {}):
  NaverPerformanceTimelineImpact {
  return {
    pre: { days: 7, days_with_data: 7, cost: 100, conv_amt: 200, roas: 2.0 },
    post: { days: 7, days_with_data: 7, cost: 100, conv_amt: 200, roas: 2.0, complete: true },
    confounded_with: [],
    confounded_count: 0,
    same_day_count: 1,
    sentence: "이 변경 전 7일과 후 7일은 이랬습니다.",
    ...overrides,
  };
}

describe("observationBadge — 관찰 중 배지", () => {
  it("impact가 null이면(날짜 파싱 실패) 배지 없음", () => {
    expect(observationBadge(null)).toBeNull();
  });

  it("post.complete가 true면 배지 없음", () => {
    expect(observationBadge(makeImpact())).toBeNull();
  });

  it("post.complete가 false면 N/7일 문자열", () => {
    const impact = makeImpact({
      post: { days: 3, days_with_data: 3, cost: 50, conv_amt: 80, roas: 1.6, complete: false },
    });
    expect(observationBadge(impact)).toBe("관찰 중 (3/7일)");
  });

  it("post.days가 0이어도(오늘 바로 다음날 변경) 배지에 0을 그대로 보여준다", () => {
    const impact = makeImpact({
      post: { days: 0, days_with_data: 0, cost: null, conv_amt: null, roas: null, complete: false },
    });
    expect(observationBadge(impact)).toBe("관찰 중 (0/7일)");
  });
});

describe("partialDataNote — 데이터 있는 날만 셌다는 보조 문구", () => {
  const full: NaverPerformanceTimelineWindow = { days: 7, days_with_data: 7, cost: 100, conv_amt: 200, roas: 2.0 };

  it("전·후 모두 days_with_data === days면 null(문구 없음)", () => {
    expect(partialDataNote(full, full)).toBeNull();
  });

  it("전 기간만 부분 적재면 전·후 일수를 함께 말한다", () => {
    const pre: NaverPerformanceTimelineWindow = { days: 7, days_with_data: 5, cost: 80, conv_amt: 150, roas: 1.875 };
    expect(partialDataNote(pre, full)).toBe("기록이 있는 날만 셌습니다 (전 5일 · 후 7일)");
  });

  it("후 기간만 부분 적재여도 마찬가지로 말한다", () => {
    const post: NaverPerformanceTimelineWindow = { days: 7, days_with_data: 2, cost: 20, conv_amt: 10, roas: 0.5 };
    expect(partialDataNote(full, post)).toBe("기록이 있는 날만 셌습니다 (전 7일 · 후 2일)");
  });

  it("둘 다 부분 적재면 둘 다의 숫자를 말한다", () => {
    const pre: NaverPerformanceTimelineWindow = { days: 7, days_with_data: 3, cost: 30, conv_amt: 40, roas: 1.33 };
    const post: NaverPerformanceTimelineWindow = { days: 7, days_with_data: 1, cost: 10, conv_amt: 5, roas: 0.5 };
    expect(partialDataNote(pre, post)).toBe("기록이 있는 날만 셌습니다 (전 3일 · 후 1일)");
  });
});

describe("deltaText — 전후 문자열 결합", () => {
  it("두 포맷 문자열을 화살표로 잇는다", () => {
    expect(deltaText("5,342,799원", "4,318,919원")).toBe("5,342,799원 → 4,318,919원");
  });

  it("NO_DATA끼리도 그대로 잇는다", () => {
    expect(deltaText("—", "—")).toBe("— → —");
  });
});

describe("화면 문구 금지어 회귀 — '개선/덕분/효과' 없음", () => {
  const FORBIDDEN = ["개선", "덕분", "효과"];

  it("confidenceLabel의 모든 가능한 출력에 금지어가 없다", () => {
    const outputs = ["commit", "assumed", "log", "unknown", "무엇이든"].map(confidenceLabel);
    for (const out of outputs) {
      for (const word of FORBIDDEN) {
        expect(out).not.toContain(word);
      }
    }
  });

  it("observationBadge의 출력에 금지어가 없다", () => {
    const outputs = [
      observationBadge(null),
      observationBadge(makeImpact()),
      observationBadge(makeImpact({
        post: { days: 5, days_with_data: 5, cost: 1, conv_amt: 1, roas: 1, complete: false },
      })),
    ];
    for (const out of outputs) {
      if (out == null) continue;
      for (const word of FORBIDDEN) {
        expect(out).not.toContain(word);
      }
    }
  });

  it("deltaText는 입력을 그대로 이을 뿐이라, 금지어 없는 입력이면 출력도 없다", () => {
    const out = deltaText("1,000원", "2,000원");
    for (const word of FORBIDDEN) {
      expect(out).not.toContain(word);
    }
  });

  it("partialDataNote의 출력에 금지어가 없다", () => {
    const pre: NaverPerformanceTimelineWindow = { days: 7, days_with_data: 3, cost: 30, conv_amt: 40, roas: 1.33 };
    const post: NaverPerformanceTimelineWindow = { days: 7, days_with_data: 7, cost: 100, conv_amt: 200, roas: 2 };
    const out = partialDataNote(pre, post);
    expect(out).not.toBeNull();
    for (const word of FORBIDDEN) {
      expect(out).not.toContain(word);
    }
  });
});
