// 이 판정기 자체가 조용히 죽으면 원래 문제로 되돌아간다 — 그래서 판정기를 테스트한다.
// (구현은 `frontend/scripts/test-census.mjs`. 테스트 전용 임포트라 번들에 안 들어간다.)
import { describe, expect, it } from "vitest";
// 설정 «원문»을 그대로 가져온다 — node:fs를 쓰면 이 프로젝트(tsconfig.app)의 타입 밖이다.
import viteConfigSource from "../../vite.config.ts?raw";
import {
  TEST_FILE_RE,
  parseIncludeGlobs,
  includeMatchesKnown,
  KNOWN_INCLUDE,
  censusVerdict,
  isDataless,
  healIsComplete,
  describeDatalessRemedy,
} from "../../scripts/test-census.mjs";

describe("censusVerdict — «통과»가 아니라 «돌았나»를 본다", () => {
  it("디스크 목록과 결과 목록이 같으면 ok", () => {
    const v = censusVerdict(["a.test.ts", "b.test.ts"], ["b.test.ts", "a.test.ts"]);
    expect(v).toEqual({ missing: [], unexpected: [], ok: true });
  });

  it("★결과를 못 낸 파일을 missing으로 잡는다 — 2026-08-12 실사고의 재현", () => {
    // 그날 vitest는 24개 중 19개만 결과를 냈는데 화면엔 「19 passed (19)」였다.
    const disk = Array.from({ length: 24 }, (_, i) => `src/f${i}.test.ts`);
    const ran = disk.slice(0, 19);
    const v = censusVerdict(disk, ran);
    expect(v.ok).toBe(false);
    expect(v.missing).toHaveLength(5);
    expect(v.missing).toContain("src/f19.test.ts");
  });

  it("분모 밖에서 돈 파일도 계약 위반으로 잡는다", () => {
    const v = censusVerdict(["a.test.ts"], ["a.test.ts", "somewhere/else.test.ts"]);
    expect(v.ok).toBe(false);
    expect(v.unexpected).toEqual(["somewhere/else.test.ts"]);
  });

  it("결과가 0건이면 ok일 수 없다 — 「아무것도 안 돌았다」가 초록이 되는 게 원래 버그다", () => {
    const v = censusVerdict(["a.test.ts", "b.test.ts"], []);
    expect(v.ok).toBe(false);
    expect(v.missing).toEqual(["a.test.ts", "b.test.ts"]);
  });
});

describe("분모 계약 — include가 바뀌면 멈춘다", () => {
  it("vite.config.ts 원문에서 include 글롭을 뽑는다", () => {
    const src = `test: { environment: "node", include: ["src/**/*.test.ts", "src/**/*.test.tsx"] },`;
    expect(parseIncludeGlobs(src)).toEqual(["src/**/*.test.ts", "src/**/*.test.tsx"]);
  });

  it("include가 없으면 null — 조용히 통과시키지 않는다", () => {
    expect(parseIncludeGlobs("test: { environment: 'node' }")).toBeNull();
    expect(includeMatchesKnown(null)).toBe(false);
  });

  it("글롭이 하나라도 늘거나 달라지면 불일치", () => {
    expect(includeMatchesKnown([...KNOWN_INCLUDE])).toBe(true);
    expect(includeMatchesKnown([...KNOWN_INCLUDE, "scripts/**/*.test.mjs"])).toBe(false);
    expect(includeMatchesKnown(["src/**/*.test.ts"])).toBe(false);
  });

  it("★실제 vite.config.ts가 KNOWN_INCLUDE와 맞는다 — 두 파일이 갈라지면 여기서 운다", () => {
    expect(includeMatchesKnown(parseIncludeGlobs(viteConfigSource))).toBe(true);
  });

  it("★디스크 수집 정규식이 include 확장자와 같은 것을 가리킨다", () => {
    // KNOWN_INCLUDE는 .test.ts / .test.tsx 둘 뿐이다. 정규식도 딱 그 둘이어야 한다.
    expect(TEST_FILE_RE.test("a.test.ts")).toBe(true);
    expect(TEST_FILE_RE.test("a.test.tsx")).toBe(true);
    expect(TEST_FILE_RE.test("a.test.js")).toBe(false);
    expect(TEST_FILE_RE.test("a.spec.ts")).toBe(false);
    expect(TEST_FILE_RE.test("atest.ts")).toBe(false);
  });
});

describe("isDataless — iCloud가 내보낸 파일 판정", () => {
  it("크기는 있는데 블록이 0이면 dataless", () => {
    expect(isDataless({ size: 70483, blocks: 0 })).toBe(true);
  });

  it("블록이 있으면 정상", () => {
    expect(isDataless({ size: 70483, blocks: 144 })).toBe(false);
  });

  it("빈 파일은 dataless가 아니다 — 0바이트 파일은 원래 블록이 0이다(오탐 방지)", () => {
    expect(isDataless({ size: 0, blocks: 0 })).toBe(false);
  });

  it("★남은 개수만이 상태다 — «읽은 개수»로 판정하면 안 된다 (2026-08-12 실사고)", () => {
    // 그날 heal은 1,126개를 읽고 373개는 읽기 실패했는데, 실패를 catch에서 빼 버려
    // 1,123개가 남은 채로 「복구 완료」를 찍었다.
    expect(healIsComplete({ read: 1126, readFailed: 373, remaining: 1123 })).toBe(false);
    // 한 개만 남아도 완료가 아니다.
    expect(healIsComplete({ read: 9999, readFailed: 0, remaining: 1 })).toBe(false);
    // 아무것도 못 읽었어도 남은 게 없으면 완료다(다른 프로세스가 이미 내려받은 경우).
    expect(healIsComplete({ read: 0, readFailed: 0, remaining: 0 })).toBe(true);
    // 읽기 실패가 있었어도 결과적으로 남은 게 없으면 완료다.
    expect(healIsComplete({ read: 10, readFailed: 5, remaining: 0 })).toBe(true);
  });

  it("복구 안내에 개수와 명령이 같이 들어간다", () => {
    const msg = describeDatalessRemedy(2046, "node_modules");
    expect(msg).toContain("2046");
    expect(msg).toContain("npm run heal");
  });
});
