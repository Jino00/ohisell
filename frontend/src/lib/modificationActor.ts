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
import type { NaverModificationActor, NaverModificationRow, NaverOutcomeProfit } from "./api";
import { won } from "./format";

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


/** ★설계서 122 §4-2 — 주체 라벨 **옆에** 붙는 자백. 지금 판정 규칙(`change_actor.py`)은
 *  주체를 셋으로만 가르고 **Ava가 없다**. Ava가 대화로 바꾼 것은 「우리 자동화」에
 *  뭉뚱그려지므로, 엔진이 스스로 한 것과 Jino가 Ava에게 시킨 것이 화면에서 같은 얼굴이 된다.
 *  라벨 문자열 «안»에 넣지 않는 이유: 정정 드롭다운은 «주장»이 아니라 «선택지»라
 *  거기에까지 붙으면 고르는 사람에게 거짓 정보가 된다. */
export function actorNote(actor: string): string | null {
  return actor === "ours" ? "(Ava 미분리)" : null;
}

/** §4-4 — 연습 행에 붙는 배지. 이 말이 없으면 화면이 「엔진이 N건 했다」고 거짓말한다. */
export const DRY_RUN_BADGE = "연습(dry_run) — 계정에 안 나감";

/** 결과 칸 본문. **금액은 `scored`일 때만** 나온다 — 그 밖의 상태는 «왜 없는가»를 말한다.
 *  ★「개선/악화」 낱말을 쓰지 않는다(§4-3): 실측에서 매출 −48.3%인 건이 「개선」이었다. */
export function outcomeText(op: NaverOutcomeProfit): string {
  if (op.state !== "scored" || op.delta === null) return op.note ?? "판정 없음";
  if (op.delta === 0) return "±0원";
  return op.delta > 0 ? `+${won(op.delta)}` : `−${won(-op.delta)}`;
}

export type OutcomeTone = "good" | "bad" | "neutral";

export function outcomeTone(op: NaverOutcomeProfit): OutcomeTone {
  if (op.state !== "scored" || op.delta === null || op.delta === 0) return "neutral";
  return op.delta > 0 ? "good" : "bad";
}

/** 자 자백 한 줄(D-NAO-230 — *"자의 가정·창을 성적과 반드시 병기한다"*).
 *  자가 없으면 null — 없는 정확도를 있는 척하지 않는다. */
export function outcomeLensNote(op: NaverOutcomeProfit): string | null {
  if (!op.lens) return null;
  const w = op.window
    ? ` · 전 ${op.window.before_from}~${op.window.before_to} → 후 ${op.window.after_from}~${op.window.after_to}`
    : "";
  return `자: 보정계수 ${op.lens.cf} ${op.lens.kind} · BEP ${op.lens.bep}${w}`;
}

/** 툴팁용 — 위 자백에 «못 재는 것»을 덧붙인다. 북극성 §3의 자는 구간 [하한, 점추정]인데
 *  행에 얼려진 건 점추정뿐이라 「하한으로도 흑자인가」는 이 행에서 물을 수 없다. */
export function outcomeLensTitle(op: NaverOutcomeProfit): string | undefined {
  const note = outcomeLensNote(op);
  if (!note) return undefined;
  return op.lens && !op.lens.interval_low_available
    ? `${note} · 하한으로도 흑자인지는 이 행에서 못 잽니다(렌즈에 점추정만 얼려져 있습니다)`
    : note;
}

/** 교정 전 RPC 자 — **접어서** 보여줄 한 줄. 갈아치우지 않는 이유는 「교정 전 채점기가
 *  무엇을 찍었나」가 증거이기 때문이다(§4-3, 교훈 #274). 안 찍혔으면 null. */
export function legacyOutcomeText(op: NaverOutcomeProfit): string | null {
  if (!op.legacy || !op.legacy.outcome) return null;
  return `${op.legacy.label}: ${op.legacy.outcome} — ${op.legacy.note}`;
}
