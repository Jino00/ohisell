// EmptyState.tsx — D-NAO-47. reason이 **필수**다(optional 아님).
// ★MOP는 KPI 8칸을 전부 0으로 찍고 이유를 설명하지 않았다(스펙 §2-3). 우리 1층은 대부분
//   0으로 채워진다(우리 조작 0회·승인 0건). 0을 찍고 침묵하면 MOP의 실패를 복제하는 것이다.
//   reason을 required로 두면 "데이터 없음" 단독 렌더가 **컴파일되지 않는다**(D-47-h).
export function EmptyState({ reason, hint }: { reason: string; hint?: string }) {
  return (
    <div className="p-8 text-center">
      <p className="text-sm text-gray-600">{reason}</p>
      {hint && <p className="mt-1 text-xs text-gray-400">{hint}</p>}
    </div>
  );
}
