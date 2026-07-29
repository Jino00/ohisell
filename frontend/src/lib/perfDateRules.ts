// perfDateRules.ts — 광고 성과 뷰의 날짜 선택·비교 **순수 규칙** (D-NAO-105 Phase 2).
//
// ★왜 페이지에서 분리했나: 이 규칙들은 React와 아무 상관이 없는데 페이지 파일에 있으면
//   테스트가 페이지(→recharts→React 전체)를 끌고 들어온다. 단일 책임으로 떼어두면 규칙만
//   따로 검증된다(원칙18-1).
// ★`<input type="date">`는 사용자가 값을 지우면 **빈 문자열**을 준다. 그걸 그대로
//   `date || undefined`로 넘기면 날짜 파라미터가 통째로 빠지고 백엔드는 '오늘'로 해석한다 —
//   사용자는 날짜를 지웠는데 화면엔 오늘 숫자가 남는다. 에러도 안 나고 화면도 안 비어서
//   아무도 모르는 거짓말이 된다. 그래서 **빈 값을 에러로 세운다**(원칙22).
//   같은 클래스의 함정을 커맨드 센터에서 한 번 겪었다(changeLogPeriod).
import { isoKST } from "./format";

/** 비교일 기본값 = 기준일 -7일(같은 요일. 주중/주말 편차를 타지 않는다). */
export function defaultCompareDate(date: string): string {
  const d = new Date(`${date}T00:00:00`);
  d.setDate(d.getDate() - 7);
  return isoKST(d);
}

/** 기준 날짜가 조회 가능한가. 불가하면 **사람이 읽는 이유**를 돌려준다(422 원문 노출 금지). */
export function baseDateError(date: string, today: string): string | null {
  if (!date) return "날짜를 선택하세요.";
  if (date > today) return "아직 오지 않은 날짜입니다.";
  return null;
}

/** 비교 날짜 검증. 같은 날끼리 비교는 백엔드가 400으로 막으므로 화면에서 먼저 말한다. */
export function compareDateError(
  date: string, compareDate: string, today: string,
): string | null {
  if (!compareDate) return "비교할 날짜를 선택하세요.";
  if (compareDate > today) return "비교 날짜가 아직 오지 않은 날짜입니다.";
  if (compareDate === date) return "기준 날짜와 비교 날짜가 같습니다.";
  return null;
}
