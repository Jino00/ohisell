// collectionFreshnessBanner.test.ts — 전역 수집 신선도 배너 빌더 가드.
//   ★존재 이유: 자동 트리거 제거 후 '잊어버림→낡음'과 '로그인 깨짐'을 유일하게 가시화.
import { describe, it, expect } from "vitest";
import { buildCollectionFreshnessBanner } from "./collectionFreshnessBanner";
import type { CollectionStatus, CollectionStreamStatus } from "../lib/api";
import { specsForKeys } from "../lib/streamRefresh";

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

// ── 배너 항목 ↔ 갱신 대상 결합 가드 (2026-08-03) ──────────────────────
// ★존재 이유: 배너의 '지금 갱신'은 items[].key로 갱신 대상을 고른다. 백엔드가 key를 바꾸거나
//   빌더가 key를 안 실으면 버튼이 **조용히 아무것도 갱신하지 않는다** — 링크였던 시절과
//   똑같이 "눌러도 아무 일이 없는" 상태로 되돌아간다. 그 회귀를 여기서 잡는다.
describe("배너 항목은 갱신 가능한 스트림 key를 실어야 한다", () => {
  it("낡은 항목의 key가 STREAM_SPECS로 해석된다", () => {
    const b = buildCollectionFreshnessBanner(
      wrap([
        s({ state: "critical", key: "ohitech_ad", label: "ohitech 로켓광고", age_hours: 160 }),
        s({ state: "failed", key: "supplier_hub", label: "로켓 발주/정산" }),
      ]),
    );
    const { specs } = specsForKeys((b?.items ?? []).map((i) => i.key));
    expect(specs.map((x) => x.key)).toEqual(["ohitech_ad", "supplier_hub"]);
    expect(specsForKeys((b?.items ?? []).map((i) => i.key)).unknown).toEqual([]);
  });

  it("4개 스트림이 전부 낡으면 4건 모두 갱신 대상이 된다(누락 없음)", () => {
    // as const — key는 리터럴 유니온이라 string[]로 넓어지면 타입이 안 맞는다.
    const keys = ["ofix_sales", "ofix_ad", "ohitech_ad", "supplier_hub"] as const;
    const b = buildCollectionFreshnessBanner(
      wrap(keys.map((k) => s({ state: "critical", key: k, age_hours: 100 }))),
    );
    expect(specsForKeys((b?.items ?? []).map((i) => i.key)).specs).toHaveLength(4);
  });
});
