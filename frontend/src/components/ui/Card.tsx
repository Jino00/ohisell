// Card.tsx — D-NAO-47. 카드 1종으로 통일. 기존엔 5가지로 쓰이고 있었다
// (rounded-lg/rounded-xl, border/border-gray-200, 패딩 제각각 — 인벤토리 실측).
import type { ReactNode } from "react";

export function Card({ title, right, children, className = "" }: {
  title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`bg-white rounded-lg border border-gray-200 ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
          <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
          {right}
        </header>
      )}
      {children}
    </section>
  );
}
