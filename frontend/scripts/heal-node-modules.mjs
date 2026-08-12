// heal-node-modules.mjs — iCloud가 내보낸 node_modules 파일을 다시 내려받는다.
//
// 배경은 `test-census.mjs`의 `isDataless` 주석. 요약: 이 repo는 iCloud Drive 안에 있고,
// macOS가 저장공간을 아끼려고 파일 내용을 서버로 내보내면 `stat` 크기는 남지만 실제
// 블록이 0이 된다. Node의 CJS 로더는 그런 파일을 **에러 없이 빈 문자열로** 읽어서
// 모듈이 `{}`가 되고, 그 결과가 `X is not a function` 같은 엉뚱한 곳에서 터진다.
//
// 고치는 법은 그냥 **읽는 것**이다 — 읽으면 iCloud가 내려받는다. 느리므로(파일당 수 초)
// 필요한 것만 골라 읽는다.
//
// ★판정은 «내가 뭘 했나»가 아니라 «지금 남아 있나»로 한다 (2026-08-12 실사고).
//   첫 판에서는 시작 시점 목록만 재확인했고 **읽기 실패는 catch에서 조용히 빠졌다** —
//   그래서 1,123개가 그대로 남아 있는데 「복구 완료」를 찍었다. 고치려던 병(«안 된 것이
//   된 것처럼 보인다»)을 이 스크립트가 그대로 앓고 있었다. 이제 끝에서 **트리 전체를
//   다시 걷는다**: 읽기 실패든, 도중에 새로 내보내진 파일이든 남아 있으면 실패다.
//
// ⚠️이건 재발한다. macOS가 언제든 다시 내보낸다. 근본 해결은 node_modules를 iCloud
//   동기화 밖에 두는 것이고(예: `node_modules.nosync` + 심볼릭 링크), 그건 워크스테이션
//   설정이라 이 스크립트의 범위가 아니다.
import { readdirSync, statSync, readFileSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { isDataless, healIsComplete } from "./test-census.mjs";

const root = fileURLToPath(new URL("..", import.meta.url));
const nm = join(root, "node_modules");

/** 지금 이 순간 dataless인 파일 전부. 판정도 이 함수로 한다(시작 목록을 재활용하지 않는다). */
function scan() {
  const found = [];
  const stack = [nm];
  while (stack.length) {
    const dir = stack.pop();
    let entries;
    try {
      entries = readdirSync(dir, { withFileTypes: true });
    } catch {
      continue; // 사라진 디렉터리
    }
    for (const e of entries) {
      const p = join(dir, e.name);
      if (e.isDirectory()) stack.push(p);
      else if (e.isFile()) {
        try {
          if (isDataless(statSync(p))) found.push(p);
        } catch { /* 사라진 파일 */ }
      }
    }
  }
  return found;
}

const targets = scan();
if (targets.length === 0) {
  console.log("node_modules에 dataless 파일 없음 ✓");
  process.exit(0);
}

console.log(`dataless ${targets.length}개 — 읽어서 내려받습니다(파일당 수 초 걸릴 수 있음)`);
let read = 0;
let readFailed = 0;
for (const [i, p] of targets.entries()) {
  try {
    readFileSync(p);
    read++;
  } catch {
    readFailed++;
  }
  if ((i + 1) % 100 === 0) console.log(`  ${i + 1}/${targets.length}`);
}

// ★검증은 전체 재스캔이다. 위 카운터는 «내가 한 일»일 뿐 «상태»가 아니다.
const left = scan();
console.log(
  `읽기 성공 ${read} · 읽기 실패 ${readFailed} · **재스캔 결과 남은 dataless ${left.length}**`,
);

if (!healIsComplete({ read, readFailed, remaining: left.length })) {
  console.error(
    `\n복구되지 않았습니다 — ${left.length}개가 아직 dataless입니다.` +
      "\n(읽기 실패했거나, 작업 도중 macOS가 새로 내보냈습니다.)" +
      "\n예:" +
      left.slice(0, 5).map((p) => `\n  · ${relative(nm, p).split(sep).join("/")}`).join("") +
      "\n\niCloud 동기화가 끝날 때까지 기다렸다가 다시 돌리거나," +
      " `rm -rf node_modules && npm ci`로 새로 받으세요(레지스트리에서 받는 쪽이 훨씬 빠릅니다).",
  );
  process.exit(1);
}
console.log(`복구 완료: ${relative(root, nm).split(sep).join("/")}`);
