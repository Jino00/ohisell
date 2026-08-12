// run-tests.mjs — `npm run test`의 실체. vitest를 돌리고 **인구조사**로 판정한다.
//
// vitest 자체의 판정(실패 0건)은 그대로 존중하되, 그 위에 한 겹을 더 얹는다:
// **디스크의 테스트 파일 전부가 결과를 냈는가.** 근거는 `test-census.mjs` 상단 주석.
//
// ★json 리포터를 쓰는 이유: 기본 리포터의 화면 숫자만으로는 «안 돈 파일»을 알 수 없다.
//   json의 `testResults[].name`은 **결과를 낸 파일만** 담으므로 디스크 목록과 빼면
//   미실행 목록이 정확히 남는다. (화면 리포터는 사람용으로 그대로 같이 켠다.)
import { spawnSync } from "node:child_process";
import { readFileSync, readdirSync, statSync, mkdtempSync, rmSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";

import {
  TEST_FILE_RE,
  parseIncludeGlobs,
  includeMatchesKnown,
  KNOWN_INCLUDE,
  censusVerdict,
  exitCodeFor,
  isDataless,
  describeDatalessRemedy,
} from "./test-census.mjs";

const root = fileURLToPath(new URL("..", import.meta.url)); // frontend/
const rel = (p) => relative(root, p).split(sep).join("/");

function printBox(...lines) {
  console.error("\n" + "━".repeat(72));
  for (const l of lines) console.error(l);
  console.error("━".repeat(72) + "\n");
}

/** 인구조사에 도달하기 «전»에 죽는 경우들 — 여기선 판정할 재료 자체가 없다. */
function die(...lines) {
  printBox(...lines);
  process.exit(1);
}

// ── 1. 분모 계약 확인 ────────────────────────────────────────────────────────
// include가 우리가 아는 모양이 아니면 «이 스크립트의 분모가 낡았다»는 뜻이다. 멈춘다.
const viteConfig = readFileSync(join(root, "vite.config.ts"), "utf8");
const globs = parseIncludeGlobs(viteConfig);
if (!includeMatchesKnown(globs)) {
  die(
    "vite.config.ts의 vitest include가 바뀌었습니다.",
    `  설정: ${JSON.stringify(globs)}`,
    `  이 스크립트가 아는 값: ${JSON.stringify(KNOWN_INCLUDE)}`,
    "",
    "인구조사의 «분모»가 include에서 나오므로 한쪽만 바뀌면 새 위치의 테스트가",
    "미실행이어도 안 걸립니다. scripts/test-census.mjs의 KNOWN_INCLUDE와 파일 수집",
    "경로를 같이 갱신하세요.",
  );
}

// ── 2. 디스크의 테스트 파일 (= 분모) ────────────────────────────────────────
function walk(dir, out = []) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (TEST_FILE_RE.test(e.name)) out.push(rel(p));
  }
  return out;
}
const expected = walk(join(root, "src")).sort();
if (expected.length === 0) {
  die("src/ 아래에 테스트 파일이 하나도 없습니다 — 수집 경로가 깨졌습니다.");
}

// ── 3. vitest 실행 ──────────────────────────────────────────────────────────
const outDir = mkdtempSync(join(tmpdir(), "vitest-census-"));
const jsonPath = join(outDir, "results.json");
const args = [
  "vitest",
  "run",
  "--reporter=default",
  "--reporter=json",
  `--outputFile.json=${jsonPath}`,
  ...process.argv.slice(2),
];
const res = spawnSync("npx", args, { cwd: root, stdio: "inherit" });

// ── 4. 인구조사 ────────────────────────────────────────────────────────────
let report;
try {
  report = JSON.parse(readFileSync(jsonPath, "utf8"));
} catch {
  rmSync(outDir, { recursive: true, force: true });
  die(
    "vitest가 json 결과를 남기지 못했습니다 — 실행 자체가 죽었을 가능성이 큽니다.",
    `vitest exit code: ${res.status}`,
  );
}
rmSync(outDir, { recursive: true, force: true });

const reported = (report.testResults ?? []).map((t) => rel(t.name)).sort();
const { missing, unexpected, ok } = censusVerdict(expected, reported);

console.log(
  `\n인구조사: 디스크 ${expected.length}개 · 결과를 낸 파일 ${reported.length}개`,
);

if (ok) {
  console.log("전부 실행됨 ✓");
} else {
  const lines = ["테스트 파일이 «실행조차 되지 않았습니다» — 통과도 실패도 아닙니다."];
  if (missing.length) {
    lines.push("", `결과를 못 낸 파일 ${missing.length}개:`);
    for (const f of missing) lines.push(`  · ${f}`);
  }
  if (unexpected.length) {
    lines.push("", `분모 밖에서 돈 파일 ${unexpected.length}개(수집 경로 불일치):`);
    for (const f of unexpected) lines.push(`  · ${f}`);
  }

  // 이 repo에서 실제로 이 증상을 만든 원인을 같이 진단해 준다(2026-08-12).
  try {
    const nm = join(root, "node_modules");
    let dataless = 0;
    const stack = [nm];
    while (stack.length) {
      const dir = stack.pop();
      for (const e of readdirSync(dir, { withFileTypes: true })) {
        const p = join(dir, e.name);
        if (e.isDirectory()) stack.push(p);
        else if (e.isFile() && isDataless(statSync(p))) dataless++;
      }
    }
    if (dataless > 0) lines.push("", describeDatalessRemedy(dataless, rel(nm)));
  } catch {
    // 진단 실패는 판정을 바꾸지 않는다 — 위의 미실행 목록이 이미 판정이다.
  }
  printBox(...lines);
}

// ★★이 파일에 판정 분기는 없다. 종료 코드는 `exitCodeFor` 하나가 정하고 여기 **한 줄**로 나간다.
//   (적대 리뷰 M8·M9: 분기가 껍데기에 흩어져 있으면 그 분기가 통째로 테스트 사각지대가 된다.
//    출구가 하나면 «미실행 → exit≠0» 통합테스트 하나가 이 줄까지 같이 지킨다.)
process.exit(exitCodeFor({ censusOk: ok, vitestStatus: res.status }));
