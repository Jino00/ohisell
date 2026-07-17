// Loading.tsx — D-NAO-47. ★§9 라이브 실측: 진단보드가 5~8초간 **완전 백지**였다
// (/api/naver/ad/diagnosis 지연, 스피너·스켈레톤 없음). 멈춘 게 아닌데 멈춘 것처럼 보인다.
export function Loading({ label = "불러오는 중…", rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div className="p-4" aria-busy="true" aria-live="polite">
      <p className="text-xs text-gray-400 mb-2">{label}</p>
      <div className="space-y-2">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="h-4 bg-gray-100 rounded animate-pulse" />
        ))}
      </div>
    </div>
  );
}
