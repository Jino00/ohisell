// modificationActor.ts — 「수정 사항」 화면의 주체 표시 규칙(순수 함수, 테스트 대상).
//
// ★★'MOP'라는 말을 화면 어디에도 쓰지 않는다. 코드베이스의 `optimizer='mop'`은
//   **"제3자 소유, 우리가 안 건드림"**이라는 뜻이고 Jino가 말하는 "MOP = 우리 시스템"과
//   정반대다. 같은 세 글자가 정반대 두 뜻을 갖는 화면은 읽는 사람을 반드시 속인다.
//   라벨은 "우리 자동화" / "대행사" / "Jino" 셋뿐이다.
//
// ★UI(.tsx)가 아니라 여기 두는 이유: vitest 설정이 `environment: "node"` +
//   `src/**/*.test.ts`라 컴포넌트는 테스트할 수 없다. 라벨·톤 규칙은 표시의 **계약**이므로
//   테스트 가능한 곳에 둔다.
import type { NaverModificationActor, NaverModificationRow } from "./api";

/** 정정 드롭다운의 선택지 순서. **대행사가 첫 번째**다 — 기본값이 대행사이기 때문
 *  (Jino: "우리가 수정한 게 아니면 대행사", 본인 수정은 드물다). */
export const ACTOR_OPTIONS: readonly NaverModificationActor[] = ["agency", "ours", "jino"] as const;

export const ACTOR_LABEL: Record<NaverModificationActor, string> = {
  ours: "우리 자동화",
  agency: "대행사",
  jino: "Jino",
};

/** Badge tone. 3주체가 **한눈에 갈려야** 한다 — 같은 톤이면 표를 훑을 때 구분이 안 된다.
 *  Badge의 tone 축(dir/judge/owner/neutral)을 쓴다: 우리 것=owner(파랑),
 *  대행사=neutral(회색, 남의 손), Jino=judge(사람 판단). */
export type ActorTone = "owner" | "neutral" | "judge";

const TONE: Record<NaverModificationActor, ActorTone> = {
  ours: "owner",
  agency: "neutral",
  jino: "judge",
};

export function actorLabel(actor: string): string {
  // 모르는 코드는 그대로 노출한다 — 지어내지 않는다(백엔드가 새 주체를 추가해도 조용히
  // 다른 주체로 둔갑하지 않게).
  return ACTOR_LABEL[actor as NaverModificationActor] ?? actor;
}

export function actorTone(actor: string): ActorTone {
  return TONE[actor as NaverModificationActor] ?? "neutral";
}

/** 값 칸에 쓸 문자열. **빈칸을 절대 만들지 않는다** — 값이 없으면 왜 없는지 말한다.
 *  (백필 36건 중 31건은 이전값이 아예 없다. 빈칸이면 "데이터 결손"으로 읽히고 0이면 거짓이다.) */
export function valueText(value: string | null, unknown: string | null): string {
  return value ?? unknown ?? "값 불명";
}

/** 시각 칸 보조 문구. 발생 시각이 없는 건은 **그 사실이 화면에 드러나야** 한다(계약). */
export function timeSuffix(row: Pick<NaverModificationRow, "time_basis">): string | null {
  return row.time_basis === "detected" ? "감지" : null;
}

/** 정정 배지에 쓸 말. corrected=true면 자동 판정과 같은 값이어도 "확인함"으로 남긴다 —
 *  사람이 본 것과 아무도 안 본 것은 다른 상태다. */
export function correctionNote(
  row: Pick<NaverModificationRow, "corrected" | "actor" | "actor_auto">,
): string | null {
  if (!row.corrected) return null;
  return row.actor === row.actor_auto
    ? "사람이 확인함"
    : `정정됨(자동 판정: ${actorLabel(row.actor_auto)})`;
}
