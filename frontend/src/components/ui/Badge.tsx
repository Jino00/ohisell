// Badge.tsx — D-NAO-47. tone이 두 축을 타입으로 가른다(§8-1).
import type { ReactNode } from "react";

const TONE = {
  dir: "bg-gray-100 text-gray-700",
  judge: "bg-gray-100 text-gray-700",
  owner: "bg-blue-50 text-blue-700",
  neutral: "bg-gray-100 text-gray-600",
  // D-NAO-139 — "이건 사람이 봐야 한다". 두 축(dir/judge, owner) 어디에도 안 들어가는
  // 세 번째 용도라 색을 새로 준다. amber는 이 코드베이스에서 이미 주의 색이다
  // (「수정 사항」의 시각 보정·소급 백필 표시).
  alert: "bg-amber-50 text-amber-700",
} as const;

export function Badge({ tone = "neutral", children }: { tone?: keyof typeof TONE; children: ReactNode }) {
  return <span className={`px-2 py-0.5 text-xs rounded-full ${TONE[tone]}`}>{children}</span>;
}
