// collectionFreshnessBanner.test.ts — 전역 수집 신선도 배너 빌더 가드.
//   ★존재 이유: 자동 트리거 제거 후 '잊어버림→낡음'과 '로그인 깨짐'을 유일하게 가시화.
import { describe, it, expect } from "vitest";
import { buildCollectionFreshnessBanner } from "./collectionFreshnessBanner";
import type { CollectionStatus, CollectionStreamStatus } from "../lib/api";

function s(over: Partial<CollectionStreamStatus>): CollectionStreamStatus {
  return {
    key: "ofix_ad", label: "ofix 광고비", state: "fresh", age_hours: 1,
    last_success_at: null, last_error_at: null, last_error: null, ...over,
  };
}
const wrap = (streams: CollectionStreamStatus[]): CollectionStatus => ({ streams, as_of: "" });

describe("buildCollectionFreshnessBanner", () => {
  it("전부 fresh/in_flight면 null(숨김)", () => {
    expect(buildCollectionFreshnessBanner(wrap([s({ state: "fresh" }), s({ state: "in_flight" })]))).toBeNull();
  });
  it("warn만 있으면 severity yellow", () => {
    const b = buildCollectionFreshnessBanner(wrap([s({ state: "warn", age_hours: 30 })]));
    expect(b?.severity).toBe("yellow");
    expect(b?.items[0].kind).toBe("stale");
  });
  it("critical 있으면 severity red", () => {
    const b = buildCollectionFreshnessBanner(wrap([s({ state: "warn" }), s({ state: "critical", age_hours: 60 })]));
    expect(b?.severity).toBe("red");
  });
  it("failed는 red + kind failed", () => {
    const b = buildCollectionFreshnessBanner(wrap([s({ state: "failed", key: "supplier_hub", label: "로켓 발주/정산" })]));
    expect(b?.severity).toBe("red");
    expect(b?.items[0].kind).toBe("failed");
  });
  it("stale 항목 텍스트에 라벨+경과 포함", () => {
    const b = buildCollectionFreshnessBanner(wrap([s({ state: "critical", age_hours: 50, label: "ofix 광고비" })]));
    expect(b?.items[0].text).toContain("ofix 광고비");
    expect(b?.items[0].text).toContain("지남");
  });
  it("null/undefined 입력 방어 → null(크래시 금지)", () => {
    expect(buildCollectionFreshnessBanner(null as unknown as CollectionStatus)).toBeNull();
    expect(buildCollectionFreshnessBanner(undefined)).toBeNull();
  });
  it("age 48h+ 는 '일 지남', 48h 미만은 '시간 지남'(일별 데이터 정밀 표기)", () => {
    expect(buildCollectionFreshnessBanner(wrap([s({ state: "critical", age_hours: 72 })]))?.items[0].text).toContain("3일 지남");
    expect(buildCollectionFreshnessBanner(wrap([s({ state: "warn", age_hours: 30 })]))?.items[0].text).toContain("30시간 지남");
    expect(buildCollectionFreshnessBanner(wrap([s({ state: "warn", age_hours: 25 })]))?.items[0].text).toContain("25시간 지남");
  });
  it("age_hours null(수집 기록 없음)이면 '수집 기록 없음' 표기", () => {
    const b = buildCollectionFreshnessBanner(wrap([s({ state: "critical", age_hours: null })]));
    expect(b?.items[0].text).toContain("수집 기록 없음");
  });
});
