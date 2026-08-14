// periodRange.ts — 날짜 구간 선택의 **순수 로직**(D-NAO-54에서 커맨드 센터가 만든 것을 공용화).
//
// ★왜 옮겼나: 「수정 사항」 화면이 같은 프리셋·같은 검증을 필요로 한다. 복사하면 두 화면이
//   서로 다른 규칙으로 같은 백엔드를 호출하게 되고(백엔드 규칙은 하나다), 어느 한쪽만
//   422 원문을 사용자에게 흘린다. UI(PeriodTabs)와 분리해 순수 함수만 여기 둔다 —
//   vitest 설정이 `environment: "node"` + `src/**/*.test.ts`라 .tsx는 테스트할 수 없다.
//
// ★이 파일이 프론트에서 **유일하게 타임존이 걸린 코드**다. 이 저장소는 타임존으로 두 번
//   사고를 냈고(쿨다운 무력화·date() 오독) 공통점이 "타임존 코드에 테스트가 없었다"는 것이다.

export type DateRange = { from: string; to: string };

/** KST 기준 N일 전 날짜(YYYY-MM-DD).
 *  ★브라우저 로컬시각(`new Date().toISOString().slice(0,10)` 등)으로 계산하면 안 된다:
 *  그건 UTC/로컬 날짜이고 서버의 changed_at은 **KST**다. 자정~09:00 사이에 하루가 어긋난다.
 *  ★en-CA 로케일이 YYYY-MM-DD를 준다. 한국은 DST가 없어 ±N일이 정확히 86,400초다.
 *  ★`now` 주입 가능 — 테스트 못 하는 타임존 코드를 두는 게 정확히 위 두 사고의 모양이었다. */
export function kstDate(offsetDays: number, now: number = Date.now()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul" })
    .format(new Date(now + offsetDays * 86_400_000));
}

// Jino 확정(2026-07-17): 기본=당일 · 7일/30일은 **어제 기준**(당일은 진행 중이라 별도 탭이고,
// 섞으면 "완결된 과거"라는 탭의 의미가 깨진다) · 임의 구간은 캘린더.
export const PERIOD_PRESETS = [
  { key: "today", label: "당일", range: (): DateRange => ({ from: kstDate(0), to: kstDate(0) }) },
  { key: "yesterday", label: "어제", range: (): DateRange => ({ from: kstDate(-1), to: kstDate(-1) }) },
  // 어제를 끝점으로 7일/30일 — 당일 미포함(끝점 포함이므로 -7..-1이 정확히 7일이다).
  { key: "7d", label: "7일", range: (): DateRange => ({ from: kstDate(-7), to: kstDate(-1) }) },
  { key: "30d", label: "30일", range: (): DateRange => ({ from: kstDate(-30), to: kstDate(-1) }) },
] as const;

export type PeriodKey = (typeof PERIOD_PRESETS)[number]["key"] | "custom";

/** 백엔드 `_MAX_CHANGE_LOG_SPAN_DAYS`와 같은 값. 양끝 포함이라 365일째까지 합법. */
export const MAX_SPAN_DAYS = 365;

/** 운영 패널(매출·손익) 계열의 상한. 백엔드 `app/utils/date_range.MAX_SPAN_DAYS`와 **같은 값**.
 *  ★상한이 API마다 다르다: 변경 이력은 365일(422), 매출·손익은 90일(400). 그래서
 *  `customRangeError`의 상한은 **호출부가 정한다** — 하나로 뭉치면 한쪽이 반드시 틀린다.
 *  (적대 리뷰 1R P1-2: 화면엔 「최대 90일」이라 적어놓고 365일까지 통과시켜, 91일을 고르면
 *   백엔드 400 원문이 새고 표는 이전 구간 숫자를 그대로 보여줬다.) */
export const OPS_MAX_SPAN_DAYS = 90;

/** 커스텀 구간이 조회 불가한 이유. null이면 조회 가능.
 *  ★`<input type="date">`는 사용자가 지우면 **빈 문자열**을 준다(실측). 그걸 안 잡으면
 *  `date_from=`이 그대로 나가고 백엔드가 422 + 날 것의 pydantic 메시지를 뱉어
 *  화면에 "불러오지 못했습니다: Input should be a valid date…"가 뜬다(실측).
 *  ★`from > to` 하나만 보면 빈 문자열을 못 잡는다: `"" > "2026-07-17"`는 **false**다(실측).
 *  ISO 날짜라서 문자열 비교가 곧 날짜 비교인 건 **양쪽이 채워졌을 때만** 참이다.
 *  ★백엔드 3개 규칙(빈값·뒤집힘·365일)을 **전부** 따라해야 한다. 게다가 날짜 입력은
 *  타이핑 중간값도 onChange로 흘린다 — 연도 칸에 2026을 치면 0002·0020·0202를 거치는데
 *  그 중간값들이 파라미터 검증을 **통과**한 뒤 span 초과로 422가 된다.
 *  여기서 막으면 요청 자체가 안 나가므로 디바운스 없이도 원문 노출이 사라진다.
 *  ★불변식: **백엔드가 막는 입력은 프론트가 먼저 막는다**(프론트가 더 엄격한 건 허용). */
export function customRangeError(
  range: DateRange,
  today: string = kstDate(0),
  maxSpanDays: number = MAX_SPAN_DAYS,
): string | null {
  if (!range.from || !range.to) return "시작일과 종료일을 모두 선택하세요.";
  if (range.from > range.to) return "시작일이 종료일보다 늦습니다.";
  // ★미래 차단(D-NAO-54 R2): 변경 이력은 지나간 사건의 기록이라 미래 구간은 의미가 없다.
  //   동시에 이게 `9999-12-31` 계열을 통째로 막는다 — 백엔드는 그 날짜에서
  //   `date_to + 1일`이 OverflowError라 422를 준다(자체 프로브로 발견).
  if (range.to > today) return "미래 날짜는 조회할 수 없습니다.";
  const spanDays = (Date.parse(`${range.to}T00:00:00Z`) - Date.parse(`${range.from}T00:00:00Z`))
    / 86_400_000 + 1;
  if (!Number.isFinite(spanDays)) return "날짜 형식이 올바르지 않습니다.";
  if (spanDays > maxSpanDays) return `조회 구간은 최대 ${maxSpanDays}일입니다.`;
  return null;
}

/** 해석된 구간을 사람이 읽는 문자열로. 프리셋도 **실제 날짜를 병기**한다.
 *  ★"당일"만 쓰면 라벨이 거짓말할 수 있다: range는 렌더 시점의 Date.now()로 계산되는데
 *  useAsyncData에는 타이머가 없다. 23:50에 열어둔 화면이 00:10이 되어도 재렌더가 없으면
 *  어제 데이터를 "당일"이라고 표시한 채 굳는다. 날짜를 병기하면 최소한 거짓말은 아니다. */
export function rangeLabel(presetLabel: string | null, range: DateRange): string {
  const span = range.from === range.to ? range.from : `${range.from} ~ ${range.to}`;
  return presetLabel ? `${presetLabel} (${span})` : span;
}
