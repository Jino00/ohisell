// Stat.tsx — D-NAO-47. ★값이 0/null이면 reason이 **타입으로** 강제된다.
// 이유 없는 0은 컴파일이 안 된다 — D-47-h를 문서가 아니라 API로 못박은 자리.
import type { ReactNode } from "react";

type StatProps = {
  label: string;
  /** 표시할 값. 이미 포맷된 문자열(lib/format.ts 사용). */
  value: ReactNode;
  /** ★값이 "비어있음"(0건·미발생)일 때 왜 그런지. isEmpty면 필수. */
  reason?: string;
  /** 값이 0/없음 상태인가. true면 reason 필수(아래 유니온이 강제). */
  isEmpty?: boolean;
  tone?: "good" | "bad" | "warn" | "idle" | "neutral";
  sub?: string;
};

type StatPropsStrict =
  | (StatProps & { isEmpty: true; reason: string })
  | (StatProps & { isEmpty?: false });

const TONE: Record<string, string> = {
  good: "text-judge-good",
  bad: "text-judge-bad",
  warn: "text-judge-warn",
  // ★0회는 나쁜 게(빨강) 아니라 아직 안 일어난 것 — 회색이 정답이다.
  idle: "text-judge-idle",
  neutral: "text-gray-900",
};

export function Stat({ label, value, reason, isEmpty, tone = "neutral", sub }: StatPropsStrict) {
  return (
    <div className="min-w-0">
      <div className="text-xs text-gray-500">{label}</div>
      <div className={`mt-0.5 text-lg font-semibold tabular-nums ${TONE[tone]}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-gray-400">{sub}</div>}
      {/* ★"왜 0인가" — MOP가 하지 않은 유일한 것 */}
      {isEmpty && reason && <div className="mt-1 text-xs text-gray-500">{reason}</div>}
    </div>
  );
}
