// bucketKeysToRender.test.ts — 「후보에서 빠진 것도 전부 세어 보여준다」는 카드 제목이
//   실제로 참인지 지킨다 (D-NAO-176 적대 리뷰 P1).
//
// ## 이 파일이 막는 것 — 사흘 새 세 번 난 같은 결함
//   ① D-NAO-174 P1-3  백엔드가 `unverifiable`을 내는데 화면이 안 읽음
//   ② D-NAO-175 P1-2  백엔드가 `type_unknown_groups`를 내는데 화면이 안 읽음
//   ③ D-NAO-176 P1    백엔드가 `already_excluded`를 내는데 화면이 안 읽음
// 세 번 다 «키를 추가하면 되는 문제»로 고쳤다면 네 번째가 온다. 근본 원인은 화면이 응답이
// 아니라 **하드코딩 배열**을 돌았고, 타입이 고정 키 Record라 **키가 늘어도 TS가 침묵**했다는
// 것이다. 그래서 이 테스트는 「already_excluded가 보이나」가 아니라
// **「모르는 키가 와도 보이나」**를 단언한다 — 그게 세 번째로 끝내는 유일한 방법이다.
import { describe, it, expect } from "vitest";

import { bucketKeysToRender } from "./NaverAdExclusionList";

describe("bucketKeysToRender", () => {
  it("★백엔드가 새 버킷을 늘리면 화면이 몰라도 그린다 — 이게 이 파일의 전부다", () => {
    const keys = bucketKeysToRender({
      insufficient_sample: {}, profitable: {},
      brand_new_bucket_nobody_told_the_frontend_about: {},
    });
    expect(keys).toContain("brand_new_bucket_nobody_told_the_frontend_about");
  });

  it("알려진 버킷은 정해진 순서로, 모르는 버킷은 그 뒤에", () => {
    const keys = bucketKeysToRender({
      zzz_unknown: {}, profitable: {}, already_excluded: {}, aaa_unknown: {},
    });
    expect(keys).toEqual(["already_excluded", "profitable", "aaa_unknown", "zzz_unknown"]);
  });

  it("already_excluded가 맨 앞이다 — 「이미 잘랐다」는 다른 어떤 판정보다 앞선다", () => {
    const keys = bucketKeysToRender({
      profitable: {}, bep_unknown: {}, already_excluded: {}, insufficient_sample: {},
    });
    expect(keys[0]).toBe("already_excluded");
  });

  it("응답에 없는 키는 그리지 않는다 — 0건과 «항목 자체가 없음»은 다르다", () => {
    const keys = bucketKeysToRender({ profitable: {} });
    expect(keys).toEqual(["profitable"]);
  });

  it("빈 응답에도 터지지 않는다", () => {
    expect(bucketKeysToRender({})).toEqual([]);
  });
});
