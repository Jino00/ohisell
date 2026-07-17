// format.ts — naver-ad 커맨드 센터 전용 포매터 단일 출처 (D-NAO-47).
//
// ⚠️⚠️ **이 모듈은 naver-ad 4파일에서만 import한다.** NaverOps/CoupangOps/AdReport/
// Dashboard는 자기 로컬 정의를 그대로 쓴다 — "겸사겸사 정리"하지 말 것.
//
// 왜: 인벤토리 실측(2026-07-17) 결과 pct()가 **호환되지 않는 입력 계약 2종**으로 존재한다.
//   · 분수 계열(NaverAdReport:48, DiagnosisBoard:30): 입력 0~1, (n*100) 함
//   · 스케일 계열(NaverOps:60, CoupangOps:40, AdReport:19): 입력 0~100, ×100 안 함
// 두 계열을 하나로 합치는 순간 5%가 0.05%로(또는 500%로) **조용히** 렌더된다. 타입 에러도
// 안 난다. 그래서 통합 범위를 리스킨 경계와 정확히 일치시켜 함정 자체를 없앤다.
// 이 모듈은 **분수 계열 계약만** 채택한다(naver-ad 4파일이 이미 쓰는 계약 = 이동 시 동작 불변).
//
// 이름이 계약을 선언한다: `pct`라는 모호한 이름을 쓰지 않는다. 스케일 계열이 나중에 필요하면
// `pctFromScaled`를 **별도로** 추가하지, 절대 `pct` 하나로 합치지 않는다.

/** 데이터 없음. ★기존 naver-ad는 "-"(하이픈), 쿠팡 계열은 "—". 스코프 안에서 em-dash로
 *  통일한다(타이포그래피상 옳음). 눈에 보이는 의도적 변경 — 리팩터가 아니라 UI 변경이다. */
export const NO_DATA = "—";

/** Date → 'YYYY-MM-DD' (KST 기준). 기존 6곳에 byte-identical로 중복돼 있던 것 — 무손실 이동. */
export function isoKST(d: Date): string {
  const kst = new Date(d.toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
  return `${kst.getFullYear()}-${String(kst.getMonth() + 1).padStart(2, "0")}-${String(kst.getDate()).padStart(2, "0")}`;
}

/** 정수 천단위 구분. 기존 fmt() — 이름만 명확히 했다(Dashboard:214의 동명이의 fmt는
 *  차트 값 포매터라 의미가 다르다. 이름을 갈라 혼동을 없앤다). */
export function num(n: number | null | undefined): string {
  if (n == null) return NO_DATA;
  return n.toLocaleString("ko-KR");
}

export function won(n: number | null | undefined): string {
  if (n == null) return NO_DATA;
  return `${num(n)}원`;
}

/** ★입력은 **분수(0~1)**다. 0.05 → "5.00%". 이름이 계약이다 —
 *  이미 0~100으로 스케일된 값을 넣으면 500%가 나온다. */
export function pctFromFraction(n: number | null | undefined, digits = 2): string {
  if (n == null) return NO_DATA;
  return `${(n * 100).toFixed(digits)}%`;
}

export function roasX(n: number | null | undefined): string {
  if (n == null) return NO_DATA;
  return `${n.toFixed(2)}배`;
}
