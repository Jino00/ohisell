// Badge.tsx — D-NAO-47. tone이 두 축을 타입으로 가른다(§8-1).
import type { ReactNode } from "react";

const TONE = {
  dir: "bg-gray-100 text-gray-700",
  judge: "bg-gray-100 text-gray-700",
  owner: "bg-blue-50 text-blue-700",
  neutral: "bg-gray-100 text-gray-600",
} as const;

export function Badge({ tone = "neutral", children }: { tone?: keyof typeof TONE; children: ReactNode }) {
  return <span className={`px-2 py-0.5 text-xs rounded-full ${TONE[tone]}`}>{children}</span>;
}
