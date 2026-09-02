// optimizerLabels.ts — 관리주체(`optimizer`) 3값의 **화면 이름 단일 출처**.
//
// ★왜 상수를 따로 두는가 (적대 리뷰 P2-2, PR #665): 개명을 화면마다 손으로 하면 「같은 값을
//   두 화면이 다르게 부르는」 상태로 조용히 되돌아간다. 실제로 리뷰가 그걸 변이로 증명했다 —
//   콘솔 필터·스코프 배지·커맨드센터 배지를 각각 옛 이름으로 되돌려도 테스트 1,360건이 전부
//   초록이었다. 「MOP가 없다」는 **음성** 조건만 지키고 「PAO라고 부른다」는 **양성** 조건은
//   아무도 안 지켰기 때문이다. 이름을 여기 한 곳에 두면 그 규칙이 테스트가 아니라 **구조**가
//   된다(고칠 곳이 하나뿐이므로 갈라질 자리가 없다).
//
// 낱말의 근거 — 설계서 §7-2(`docs/references/122_pao_console_uiux_design_20260902.md`):
//   ours → 「PAO」        확정된 이름(D-NAO-162). 「우리」는 주체 축(우리 자동화/Ava/Jino)이
//                        이미 쓰므로 이 축의 라벨에서 뺀다.
//   mop  → 「제3자(대행사)」 코드의 `mop`은 «제3자 소유, 우리가 안 건드림»이다. 지금 그 제3자가
//                        대행사라 괄호로 밝힌다. 재검토 조건: 대행사 외 제3자가 생기면 괄호를 뗀다.
//   none → 「수동」        이미 멀쩡하다 — 바꾸면 불필요한 변경이다.
//
// ⚠️'MOP'는 **경쟁 상용 도구**의 이름이다. 화면에서 그 도구를 가리켜야 하면 「벤치마크 도구」라
//   쓴다. 세 글자를 라벨에 넣는 순간 화면이 정확히 반대로 읽힌다(`optimizer='mop'`은 우리가
//   아니라 남이라는 뜻이다).
import type { NaverAdOptimizer } from "./api";

export const OPTIMIZER_LABEL: Record<NaverAdOptimizer, string> = {
  ours: "PAO",
  mop: "제3자(대행사)",
  none: "수동",
};

export const OPTIMIZER_TITLE: Record<NaverAdOptimizer, string> = {
  ours: "PAO(우리 프로그램)가 제안·실행",
  mop: "제3자가 소유 — 우리는 손대지 않음(진단·리포트만)",
  none: "아무 자동화도 하지 않음",
};

/** 스코프 화면 배지 — 관할만이 아니라 «지금 실제로 도는가»(auto_operate)까지 한 낱말에 담는다.
 *  ★「맡김」과 「손댐」은 다른 상태다(설계서 §7-3) — 'ours'인데 정지면 그걸 말해야 한다. */
export function optimizerBadgeLabel(
  optimizer: NaverAdOptimizer | string,
  autoOperate: boolean,
): string {
  if (optimizer === "ours") return autoOperate ? `${OPTIMIZER_LABEL.ours} 가동` : `${OPTIMIZER_LABEL.ours} 정지`;
  if (optimizer === "mop") return OPTIMIZER_LABEL.mop;
  return OPTIMIZER_LABEL.none;
}
