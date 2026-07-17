// Table.tsx — D-NAO-47. ★페이지네이션이 계약이다.
// §9 라이브 실측: 진단보드가 489행을 무페이징으로 그려 **페이지 스크롤 27,305px**가 나왔다.
// 3층 원자료 탐색은 키워드 **91,005행**이 대상이다. 상한 없이 그리면 브라우저가 죽는다.
import type { ReactNode } from "react";
import { Button } from "./Button";
import { num } from "../../lib/format";

export function Th({ children, right }: { children: ReactNode; right?: boolean }) {
  return (
    <th className={`px-4 py-3 text-xs font-medium text-gray-500 ${right ? "text-right" : "text-left"}`}>
      {children}
    </th>
  );
}

export function Td({ children, right }: { children: ReactNode; right?: boolean }) {
  return (
    <td className={`px-4 py-2 text-sm border-b border-gray-100 ${right ? "text-right tabular-nums" : ""}`}>
      {children}
    </td>
  );
}

export function Table({ head, children }: { head: ReactNode; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead className="bg-gray-50"><tr>{head}</tr></thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

/** 서버 페이지네이션 바. total > pageSize면 반드시 붙인다. */
export function Pager({ total, offset, pageSize, onOffset }: {
  total: number; offset: number; pageSize: number; onOffset: (n: number) => void;
}) {
  if (total <= pageSize) return null;
  const from = offset + 1;
  const to = Math.min(offset + pageSize, total);
  return (
    <div className="flex items-center justify-between px-4 py-2 border-t border-gray-100">
      <span className="text-xs text-gray-500">{num(from)}–{num(to)} / {num(total)}</span>
      <div className="flex gap-1">
        <Button disabled={offset === 0} onClick={() => onOffset(Math.max(0, offset - pageSize))}>이전</Button>
        <Button disabled={to >= total} onClick={() => onOffset(offset + pageSize)}>다음</Button>
      </div>
    </div>
  );
}
