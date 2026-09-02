import type { NaverSearchTermExclusionRow } from "./api";

// exclusionSlots.ts — 제외 슬롯 화면의 순수 헬퍼 (설계서 §5-4)
//
// ★왜 컴포넌트 파일에서 뺐나: 화면 파일이 컴포넌트 «말고» 다른 것을 export 하면
//   `react-refresh/only-export-components` 경고가 붙는다. 이 저장소는 eslint를
//   `--max-warnings 96`으로 «정확히» 상한에 붙여 두었으므로 경고 하나가 곧 CI 빨강이다.
//   순수 함수는 애초에 화면 파일에 있을 이유가 없다 — 테스트도 여기서 직접 부른다.

/** 스윕 시각 표기. ★`as_of`(응답 생성 시각)를 쓰지 않는다 — 09:35에 본 것을 지금 기준이라
 *  말하게 된다.
 *
 *  ★★그리고 «가장 오래된 관측»을 같이 낸다. 2026-09-02 실측: 1,013그룹 중 1,003은 그날
 *  스윕됐는데 **10그룹은 08-24에 멈춰 있었다**(9일째). 그런데 카운터엔 안 잡힌다 —
 *  `_state`가 「모름을 여유보다 먼저 본다」는 규율대로 `unknown`을 먼저 돌려주고,
 *  그래서 `stale`은 **0**으로 나온다. 「못 센 그룹 10개」만 보면 «오늘 조회가 잠깐 실패했나»로
 *  읽히지만 실제로는 9일째다. 그 «얼마나 오래»를 여기서 말한다. */
export function sweepLabel(from: string | null, to: string | null, now: Date = new Date()): string {
  if (!to) return "아직 한 번도 스윕하지 않았습니다";
  const fmt = (iso: string) => iso.replace("T", " ").slice(0, 16);
  const base = `마지막 스윕 ${fmt(to)} 기준`;
  if (!from) return base;
  const ageDays = Math.floor((now.getTime() - new Date(from).getTime()) / 86_400_000);
  if (from.slice(0, 10) === to.slice(0, 10)) return base;
  return `${base} — 다만 가장 오래된 관측은 ${fmt(from)}로 ${ageDays}일째입니다(그 그룹들은 최근 스윕에서 못 봤습니다)`;
}

/** 칩 툴팁. ★대행사 편입분의 날짜는 `console_excluded_at`(콘솔이 알려준 실제 시각)을 쓴다 —
 *  `excluded_at`은 «우리가 편입한» 시각이라 그걸 쓰면 「오늘 잘랐다」로 읽힌다(D-NAO-177).
 *  그 값이 없으면 「걸린 시점 모름」이라고 쓴다. 모르는 것을 날짜로 메우지 않는다. */
export function termTitle(e: NaverSearchTermExclusionRow): string {
  if (e.source === "console_import") {
    const when = e.console_excluded_at?.slice(0, 10);
    return `대행사 축적분 · ${when ? `${when}에 걸림` : "걸린 시점 모름(콘솔이 안 알려줌)"}`;
  }
  const when = e.excluded_at?.slice(0, 10);
  return `우리 실행분${when ? ` · ${when}` : ""}`;
}
