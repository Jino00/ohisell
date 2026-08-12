// spawn-run-tests.mjs — `run-tests.mjs`를 **자식 프로세스로** 돌려 exit 코드를 관측한다.
//
// ★왜 필요한가 (적대 리뷰 변이 M8, 2026-08-12): 판정 «로직»은 `test-census.mjs`에 있어서
//   테스트가 덮지만, **그 판정을 exit 코드로 바꾸는 곳**은 `run-tests.mjs`의 `if (ok)` 한 줄이고
//   거기는 아무 테스트도 안 덮었다. 리뷰어가 그 줄을 `if (true)`로 바꿔 봤더니 «1개 파일 미실행»
//   상황에서도 `전부 실행됨 ✓` + exit 0을 찍었고 **전체 테스트는 그대로 통과했다.**
//   가드가 조용히 가드를 그만두는 회귀 — 이 PR이 막으려는 실패 그 자체다.
//
// 자식에게 «파일 하나만» 돌리라고 시키면 나머지가 전부 미실행으로 잡히므로, 실사고와 같은
// 모양을 싸게 재현할 수 있다. 그리고 그 파일에 이 테스트가 없으므로 **재귀하지 않는다.**
//
// spawn을 여기(스크립트 쪽, 순수 JS)에 두는 이유: `src/`의 tsconfig에는 node 타입이 없다.
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

export function runTestsChild(args) {
  const script = fileURLToPath(new URL("./run-tests.mjs", import.meta.url));
  const cwd = fileURLToPath(new URL("..", import.meta.url));
  const r = spawnSync(process.execPath, [script, ...args], {
    cwd,
    encoding: "utf8",
    // 자식이 또 인구조사를 돌리므로 환경은 그대로 물려준다.
    env: process.env,
  });
  return { status: r.status, stdout: r.stdout ?? "", stderr: r.stderr ?? "" };
}
