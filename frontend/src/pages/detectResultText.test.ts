// detectResultText.test.ts — 「라이브에서 자동 발견」 결과 문장이 «못 본 몫»을 숨기지 않는지 지킨다
//   (D-NAO-174 후속 적대 리뷰 P1-2).
//
// ## 이 파일이 막는 것
// 백엔드가 통을 나눠 내는데(unverifiable / type_unknown / unattributable) 화면이 안 읽으면
// 그 구분은 **없는 것과 같다.** 어제 P1으로 처분된 세 건 중 하나가 정확히 이 모양이었다 —
// API는 `unverifiable: 1`인데 화면은 「모두 걸려 있음」이었다. 같은 실패를 detect 쪽에서
// 반복하지 않게 한다.
import { describe, it, expect } from "vitest";

import { detectResultText } from "./NaverAdExclusionList";
import type { NaverSearchTermDetectResult } from "../lib/api";

function result(over: Partial<NaverSearchTermDetectResult> = {}): NaverSearchTermDetectResult {
  return {
    scanned_groups: 0, groups_with_zero: 0, recorded: [], errors: [],
    as_of: "2026-08-12T16:00:00", ...over,
  };
}

describe("detectResultText", () => {
  it("★대조된 그룹이 하나도 없으면 「등록 0건」을 성과처럼 읽히게 두지 않는다", () => {
    // 유형 조회가 전부 실패한 상황 — 변경 전에는 이 그룹들이 어느 표시 숫자에도 안 잡혔다.
    const s = detectResultText(result({ scanned_groups: 5, type_unknown_groups: 5 }));
    expect(s).toContain("대조된 그룹이 없다");
    expect(s).toContain("못 봤다");
    expect(s).not.toContain("새로 등록 0건");
  });

  it("쇼핑과 유형 미상을 갈라서 찍는다 — 둘은 서로 다른 «못 봄»이다", () => {
    const s = detectResultText(result({
      scanned_groups: 10, unverifiable_groups: 3, type_unknown_groups: 2, groups_with_zero: 4,
      recorded: [{ adgroup_id: "g1", search_term: "골프" }] as never,
    }));
    expect(s).toContain("대조 못 함 5개");
    expect(s).toContain("쇼핑 3");
    expect(s).toContain("유형 미상 2");
    expect(s).toContain("새로 등록 1건");
  });

  it("★못 본 몫이 0이어도 그 줄을 지우지 않는다 — 조용히 사라지면 «없음»과 구분이 안 된다", () => {
    const s = detectResultText(result({ scanned_groups: 4, groups_with_zero: 4 }));
    expect(s).toContain("대조 못 함 0개");
  });

  it("캠페인을 못 붙여 기록을 보류한 건이 있으면 경고로 올린다", () => {
    const s = detectResultText(result({
      scanned_groups: 2, groups_with_zero: 1, unattributable_count: 3,
    }));
    expect(s).toContain("기록 보류 3건");
  });

  it("옛 응답(새 필드 없음)에도 터지지 않고 0으로 읽는다", () => {
    const s = detectResultText(result({ scanned_groups: 2, groups_with_zero: 2 }));
    expect(s).toContain("2개 그룹 조회");
    expect(s).not.toContain("NaN");
    expect(s).not.toContain("undefined");
  });
});
