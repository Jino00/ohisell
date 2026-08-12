// `test-census.mjs`의 타입. 구현이 순수 JS인 이유는 그 파일 상단 참조
// (node가 로더 없이 그대로 실행해야 하므로 .ts가 아니다).
export declare const TEST_FILE_RE: RegExp;
export declare const KNOWN_INCLUDE: readonly string[];
export declare function parseIncludeGlobs(viteConfigSource: string): string[] | null;
export declare function includeMatchesKnown(globs: string[] | null): boolean;
export declare function censusVerdict(
  expected: string[],
  reported: string[],
): { missing: string[]; unexpected: string[]; ok: boolean };
export declare function isDataless(stat: { size: number; blocks: number }): boolean;
export declare function healIsComplete(counts: {
  read?: number;
  readFailed?: number;
  remaining: number;
}): boolean;
export declare function describeDatalessRemedy(count: number, dir: string): string;
