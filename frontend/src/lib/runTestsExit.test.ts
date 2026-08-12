// 가드가 **조용히 가드를 그만두는** 회귀를 막는다.
//
// ★적대 리뷰 변이 M8(2026-08-12)이 드러낸 것: 판정 «로직»(`test-census.mjs`)은 테스트가 덮는데,
//   그 판정을 **exit 코드로 바꾸는 한 줄**(`run-tests.mjs`의 `if (ok)`)은 아무도 안 덮었다.
//   그 줄을 `if (true)`로 바꾸면 미실행 파일이 있어도 `전부 실행됨 ✓` + exit 0인데
//   **전체 테스트는 그대로 통과했다.** 이 PR이 만들려는 바로 그 장치가 사각지대에 있었다.
//
// 재귀 방지: 자식에게 «파일 하나만» 돌리라고 시킨다. 그 파일에 이 테스트가 없으므로
// 자식은 다시 자식을 낳지 않는다. 나머지 24개가 미실행으로 잡히는 것이 이 테스트의 재료다.
import { describe, expect, it } from "vitest";
import { runTestsChild } from "../../scripts/spawn-run-tests.mjs";

// 자식이 vitest를 한 번 더 띄우므로 기본 5초로는 모자란다.
const TIMEOUT_MS = 120_000;

describe("run-tests.mjs — 미실행이 있으면 반드시 exit≠0", () => {
  it(
    "★파일 하나만 돌리게 하면 나머지를 미실행으로 신고하고 실패한다",
    () => {
      // vitest에 파일 필터를 넘긴다 — 디스크의 25개는 그대로인데 결과를 내는 건 1개뿐이다.
      // (2026-08-12 실사고와 같은 모양: 파일은 있는데 결과가 없다.)
      const r = runTestsChild(["src/lib/format.test.ts"]);

      expect(r.status, "미실행이 있는데 exit 0이면 가드가 죽은 것이다").not.toBe(0);
      const out = r.stdout + r.stderr;
      expect(out).toContain("실행조차 되지 않았습니다");
      // 어느 파일인지 말해야 한다 — 개수만 말하면 운영자가 손댈 곳을 모른다.
      expect(out).toContain("· src/lib/testCensus.test.ts");
      // ★실제로 돈 파일은 미실행 목록에 없어야 한다 — 「전부 미실행」으로 뭉뚱그리면
      //   목록이 정보를 잃고, 이 테스트는 항상 통과하는 동어반복이 된다.
      expect(out).not.toContain("· src/lib/format.test.ts");
    },
    TIMEOUT_MS,
  );
});
