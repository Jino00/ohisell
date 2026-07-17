// Delta.tsx — D-NAO-47. **방향 전용**. invert prop이 없다(§8-1 규칙 2).
// ★기존 mopDelta는 invert 플래그로 "비용 증가는 나쁨"이라는 **판단**을 방향색에 섞었다.
//   판단은 판단 토큰(judge-*)으로 옆에 따로 표시한다. 여기는 오르내림만 말한다.
//   MOP/한국 증시 관례: 증가=▲빨강, 감소=▼파랑.
import { pctFromFraction } from "../../lib/format";

export function Delta({ fraction }: { fraction: number | null | undefined }) {
  if (fraction == null) return <span className="text-dir-flat">—</span>;
  if (fraction === 0) return <span className="text-dir-flat tabular-nums">0.00%</span>;
  const up = fraction > 0;
  return (
    <span className={`tabular-nums ${up ? "text-dir-up" : "text-dir-down"}`}>
      {up ? "▲" : "▼"} {pctFromFraction(Math.abs(fraction))}
    </span>
  );
}
