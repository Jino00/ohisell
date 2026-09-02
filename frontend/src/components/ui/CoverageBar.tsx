// CoverageBar.tsx — D-NAO-47. 1층 ① 커버리지(현재 우리 1.15%).
import { won, pctFromFraction } from "../../lib/format";

export function CoverageBar({ ours, mop, manual }: { ours: number; mop: number; manual: number }) {
  const total = ours + mop + manual;
  if (total <= 0) {
    return <p className="text-xs text-gray-500">광고비 데이터가 없습니다(조회 기간에 집행 없음).</p>;
  }
  const pctOf = (v: number) => (v / total) * 100;
  return (
    <div>
      <div className="flex h-3 w-full overflow-hidden rounded bg-gray-100">
        <div className="bg-owner-ours" style={{ width: `${pctOf(ours)}%` }} title="PAO" />
        <div className="bg-owner-mop" style={{ width: `${pctOf(mop)}%` }} title="제3자(대행사)" />
        <div className="bg-owner-manual" style={{ width: `${pctOf(manual)}%` }} title="수동" />
      </div>
      <p className="mt-1 text-xs text-gray-500">
        PAO {won(ours)} ({pctFromFraction(ours / total)}) · 전체 {won(total)}
      </p>
    </div>
  );
}
