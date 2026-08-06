// stamp-build.mjs — 빌드 직후 dist에 "이 산출물이 어느 커밋에서 나왔는가"를 심는다.
//
// ★왜 배포 시점이 아니라 빌드 시점인가(2026-08-06 적대 리뷰 P1-2): 배포 스크립트가 심으면
//   스탬프는 "지금 내 HEAD"를 적을 뿐이라, **병합만 하고 재빌드를 안 한 옛 dist**를 올려도
//   스탬프는 최신으로 전진한다. 그러면 상대 작업이 사라지는데 사후에 스탬프만 봐서는
//   손실 자체를 알 수 없다(가드가 증거를 오염시킨다). 빌드가 심으면 dist와 커밋이 함께 묶여
//   "이 dist는 저 커밋에서 나왔다"가 **주장이 아니라 사실**이 된다.
import { execFileSync } from "node:child_process";
import { writeFileSync, existsSync } from "node:fs";

const git = (...a) => execFileSync("git", a, { encoding: "utf8" }).trim();

if (!existsSync("dist")) {
  console.error("stamp-build: dist 없음 — vite build가 먼저 돌아야 한다");
  process.exit(1);
}

const commit = git("rev-parse", "HEAD");
// 추적/미추적 모두 본다(`git diff`는 새 파일을 못 본다 — 리뷰 P2).
const dirty = git("status", "--porcelain", "--", ".") !== "" ? "1" : "0";

writeFileSync(
  "dist/.build-stamp",
  `commit=${commit}\ndirty=${dirty}\nbuilt_at=${new Date().toISOString()}\n`,
);
console.log(`stamp-build: commit=${commit.slice(0, 12)} dirty=${dirty}`);
