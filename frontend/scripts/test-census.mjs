// test-census.mjs — "테스트가 통과했다"와 "테스트가 돌았다"를 가른다.
//
// ★왜 있나 (2026-08-12 실측): vitest가 **5개 파일을 아예 실행하지 못한 채**
//   `Test Files  19 passed (19)` 를 찍었다. 실행 못 한 파일은 분모에서도 빠지므로
//   화면 어디에도 5라는 숫자가 없다 — 「19 통과 / 19 중」은 완전한 초록으로 읽힌다.
//   (원인은 아래 `describeDatalessRemedy` 주석 참조. exit code는 1이었지만 사람은
//   숫자를 보고 CI 칩도 결과가 없으면 회색이다 — 교훈 #123 그 자체.)
//
// ★그래서 판정 기준을 바꾼다: 「실패 0건」이 아니라 **「디스크의 테스트 파일 전부가
//   결과를 냈는가」**. 결과를 못 낸 파일은 통과도 실패도 아니라 **미실행**이고,
//   미실행은 초록일 수 없다.
//
// 이 파일은 **순수 함수만** 둔다(I/O 없음) — 그래야 vitest가 이 판정기 자체를 테스트한다.
// I/O 껍데기는 `run-tests.mjs`.

/** 벤치마크가 아니라 계약이다: vite.config.ts의 include와 이 정규식이 같은 것을 가리켜야 한다. */
export const TEST_FILE_RE = /\.test\.tsx?$/;

/**
 * vite.config.ts 원문에서 vitest `include` 글롭 배열을 뽑는다.
 *
 * ★왜 파싱까지 하나: 인구조사(census)의 «분모»는 include가 정한다. 여기서 글롭을
 *   하드코딩해 두면 누가 include를 넓히는 순간 **새 폴더의 테스트는 분모에 없어서
 *   미실행이어도 안 걸린다** — 이 스크립트가 막으려는 실패가 이 스크립트 안에서 재발한다.
 *   그래서 설정을 읽고, 우리가 아는 모양과 다르면 **조용히 넘어가지 않고 멈춘다.**
 */
export function parseIncludeGlobs(viteConfigSource) {
  const m = viteConfigSource.match(/include\s*:\s*\[([^\]]*)\]/);
  if (!m) return null;
  const globs = [...m[1].matchAll(/["'`]([^"'`]+)["'`]/g)].map((g) => g[1]);
  return globs.length ? globs : null;
}

/** 우리가 이 스크립트를 쓴 시점의 include. 여기서 벗어나면 `run-tests.mjs`가 멈춘다. */
export const KNOWN_INCLUDE = ["src/**/*.test.ts", "src/**/*.test.tsx"];

export function includeMatchesKnown(globs) {
  if (!globs) return false;
  return (
    globs.length === KNOWN_INCLUDE.length &&
    KNOWN_INCLUDE.every((g) => globs.includes(g))
  );
}

/**
 * 디스크 목록 ↔ vitest가 실제로 결과를 낸 목록을 대조한다.
 *
 * @param {string[]} expected  디스크에서 찾은 테스트 파일 (repo 상대 경로)
 * @param {string[]} reported  vitest json 리포터의 testResults[].name (repo 상대 경로)
 * @returns {{missing: string[], unexpected: string[], ok: boolean}}
 */
export function censusVerdict(expected, reported) {
  const exp = new Set(expected);
  const rep = new Set(reported);
  const missing = [...exp].filter((f) => !rep.has(f)).sort();
  // 분모 밖에서 뭔가 돌았다면 그것도 계약 위반이다 — include와 이 목록이 갈라졌다는 뜻.
  const unexpected = [...rep].filter((f) => !exp.has(f)).sort();
  return { missing, unexpected, ok: missing.length === 0 && unexpected.length === 0 };
}

/**
 * iCloud Drive가 `node_modules`를 «데이터 없는(dataless)» 상태로 내보냈는지 판정한다.
 *
 * ★2026-08-12에 5개 파일이 안 돈 **진짜 원인**이다. 이 repo는 iCloud Drive 안에 있고
 *   macOS가 저장공간 최적화로 파일 내용을 서버로 내보내면 `stat`의 크기는 그대로인데
 *   `st_blocks`가 0이 된다. 그 상태에서 Node의 CJS 로더가 그 파일을 읽으면
 *   **에러 없이 빈 문자열**을 얻는다 → 모듈이 `{}`로 평가된다 → jsdom·css-tree가
 *   `X is not a function`으로 죽는다 → vitest forks 워커가 아예 못 뜬다.
 *   실측: 19,825개 중 **2,046개가 dataless**였고 jsdom의 9개 파일이 여기 걸렸다.
 *
 * 판정: `size > 0 && blocks === 0`.
 */
export function isDataless(stat) {
  return stat.size > 0 && stat.blocks === 0;
}

/**
 * `npm test`의 최종 exit 코드. **판정을 여기 한 곳에 모은다.**
 *
 * ★적대 리뷰 변이 M8·M9(2026-08-12)가 드러낸 것: 판정 로직은 테스트가 덮는데 그 판정을
 *   exit 코드로 바꾸는 «분기»가 껍데기 스크립트에 흩어져 있으면 거기가 통째로 사각지대가 된다.
 *   실제로 `if (ok)`를 무력화해도(M8), vitest 실패 전파를 지워도(M9) 아무 테스트도 안 죽었다.
 *   → 분기를 순수 함수 하나로 모으고 껍데기에는 `process.exit(exitCodeFor(...))` **한 줄만** 남긴다.
 *     그래야 «판정»은 유닛테스트가, «전파»는 통합테스트 하나가 전부 덮는다.
 *
 * @param {{censusOk: boolean, vitestStatus: number|null}} x
 */
export function exitCodeFor({ censusOk, vitestStatus }) {
  // 미실행이 있으면 vitest가 뭐라 하든 실패다 — 통과도 실패도 아닌 것은 초록일 수 없다.
  if (!censusOk) return 1;
  // vitest가 죽어서 코드를 못 주면(null) «성공»으로 읽지 않는다.
  if (vitestStatus === null || vitestStatus === undefined) return 1;
  return vitestStatus;
}

/**
 * 복구가 끝났는가 — **재스캔 결과만** 본다.
 *
 * ★첫 판의 실사고(2026-08-12): 판정을 «읽은 개수»로 했더니 읽기 실패 373건이 catch에서
 *   조용히 빠져 1,123개가 남은 채로 「복구 완료」가 찍혔다. 무엇을 얼마나 했는지는
 *   **상태가 아니다.** 남아 있는 개수만이 상태다.
 */
export function healIsComplete({ remaining }) {
  return remaining === 0;
}

export function describeDatalessRemedy(count, dir) {
  return [
    `node_modules에 iCloud가 내보낸(dataless) 파일이 ${count}개 있습니다 — ${dir}`,
    "Node는 이런 파일을 «빈 파일»로 읽고 에러를 내지 않으므로, 모듈이 조용히 {}가 됩니다.",
    "복구:  npm run heal",
  ].join("\n");
}
