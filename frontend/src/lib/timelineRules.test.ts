// timelineRules.test.ts — ⑥개선 타임라인 순수 로직 가드(D-NAO-104 Phase 3).
import { describe, it, expect } from "vitest";
import {
  confidenceLabel, observationBadge, deltaText, partialDataNote,
  filterMarkersInRange, toChartMarkers, formatEventMarkerLabel,
  type ChartEventMarker,
} from "./timelineRules";
import type {
  NaverPerformanceTimelineDay, NaverPerformanceTimelineImpact, NaverPerformanceTimelineWindow,
} from "./api";

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

// ── ③차트 마커 순수 로직 ───────────────────────────────────────────
function makeEvent(overrides: Partial<NaverPerformanceTimelineDay["events"][number]> = {}):
  NaverPerformanceTimelineDay["events"][number] {
  return {
    ref_key: "ref-1",
    label_ko: "03 재가동",
    detail_ko: "",
    effective_confidence: "log",
    scope: "campaign",
    source: "change_log",
    curated: true,
    ...overrides,
  };
}

function makeDay(date: string, events: NaverPerformanceTimelineDay["events"] = [makeEvent()]):
  NaverPerformanceTimelineDay {
  return { date, events, impact: null };
}

describe("filterMarkersInRange — 시리즈 날짜 범위 밖 마커 제거", () => {
  const seriesDates = ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-28"];

  it("범위 안의 마커만 남긴다", () => {
    const markers: ChartEventMarker[] = [
      { date: "2026-07-15", label: "범위 밖(이전)", count: 1 },
      { date: "2026-07-21", label: "범위 안", count: 1 },
      { date: "2026-08-01", label: "범위 밖(이후)", count: 1 },
    ];
    expect(filterMarkersInRange(markers, seriesDates)).toEqual([
      { date: "2026-07-21", label: "범위 안", count: 1 },
    ]);
  });

  it("경계값(첫 날짜·마지막 날짜)은 포함한다", () => {
    const markers: ChartEventMarker[] = [
      { date: "2026-07-20", label: "첫날", count: 1 },
      { date: "2026-07-28", label: "마지막날", count: 1 },
    ];
    expect(filterMarkersInRange(markers, seriesDates)).toEqual(markers);
  });

  it("seriesDates가 비어있으면(집행 이력 없음) 빈 배열 — 그릴 축이 없다", () => {
    const markers: ChartEventMarker[] = [{ date: "2026-07-21", label: "x", count: 1 }];
    expect(filterMarkersInRange(markers, [])).toEqual([]);
  });

  it("마커가 비어있으면 빈 배열 그대로", () => {
    expect(filterMarkersInRange([], seriesDates)).toEqual([]);
  });
});

describe("toChartMarkers — timeline 응답 → 차트 마커", () => {
  const seriesDates = ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-28"];

  it("이벤트가 있는 날만 마커로 변환하고, 첫 이벤트의 label_ko를 대표 라벨로 쓴다", () => {
    const timeline: NaverPerformanceTimelineDay[] = [
      makeDay("2026-07-21", [makeEvent({ label_ko: "03 재가동" })]),
      makeDay("2026-07-23", []),
    ];
    expect(toChartMarkers(timeline, seriesDates)).toEqual([
      { date: "2026-07-21", label: "03 재가동", count: 1 },
    ]);
  });

  it("한 날짜에 이벤트가 여러 건이면 count에 전부 반영한다", () => {
    const timeline: NaverPerformanceTimelineDay[] = [
      makeDay("2026-07-22", [
        makeEvent({ label_ko: "첫 결정" }),
        makeEvent({ ref_key: "ref-2", label_ko: "두번째 결정" }),
      ]),
    ];
    expect(toChartMarkers(timeline, seriesDates)).toEqual([
      { date: "2026-07-22", label: "첫 결정", count: 2 },
    ]);
  });

  it("이벤트가 없는 날은 마커를 만들지 않는다", () => {
    const timeline: NaverPerformanceTimelineDay[] = [makeDay("2026-07-21", [])];
    expect(toChartMarkers(timeline, seriesDates)).toEqual([]);
  });

  it("timeline에 있어도 seriesDates 범위 밖이면 걸러진다(D-NAO-101 마커가 07-28 위치에만 뜨는 것의 근거)", () => {
    const timeline: NaverPerformanceTimelineDay[] = [
      makeDay("2026-06-01", [makeEvent({ label_ko: "훨씬 이전 결정" })]),
      makeDay("2026-07-28", [makeEvent({ label_ko: "03 재가동" })]),
    ];
    expect(toChartMarkers(timeline, seriesDates)).toEqual([
      { date: "2026-07-28", label: "03 재가동", count: 1 },
    ]);
  });
});

describe("formatEventMarkerLabel — 차트 마커 라벨(12자 제한 + 개수 압축)", () => {
  it("12자 이내면 그대로", () => {
    expect(formatEventMarkerLabel("03 재가동", 1)).toBe("03 재가동");
  });

  it("12자를 넘으면 자른다", () => {
    const long = "아주아주아주아주아주아주긴라벨입니다";
    expect(formatEventMarkerLabel(long, 1)).toBe(long.slice(0, 12));
    expect(formatEventMarkerLabel(long, 1).length).toBe(12);
  });

  it("count가 1을 넘으면 '외 N건'이 아니라 '+N'으로 압축한다", () => {
    expect(formatEventMarkerLabel("03 재가동", 3)).toBe("03 재가동 +2");
    expect(formatEventMarkerLabel("03 재가동", 3)).not.toContain("외");
    expect(formatEventMarkerLabel("03 재가동", 3)).not.toContain("건");
  });

  it("count가 1이면 접미사 없음", () => {
    expect(formatEventMarkerLabel("03 재가동", 1)).toBe("03 재가동");
  });
});
