// Busy.tsx — 조회 진행 표시의 공용 조각.
//
// 왜 공용으로 두는가: 운영 패널이 둘(스마트스토어·쿠팡)인데 기간 전환 UX가 갈리면
// 같은 동작에 다른 피드백이 나온다. 실제로 한쪽은 값이 "…"로 사라지고 다른 쪽은
// 아무 표시도 없었다. 조각을 하나로 두면 다음 패널이 생겨도 같은 표시를 쓴다.

/** 갱신 표시의 최소 노출 시간(ms).
 *  sales-summary 응답이 라이브 실측 ~0.2초라 이 바닥이 없으면 스피너가 한 프레임
 *  스치고 사라져 "아무 일도 안 일어났다"로 보인다. 표시가 안 보이면 표시가 아니다. */
export const MIN_BUSY_MS = 350;

export function Spinner({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={`animate-spin ${className}`} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
    </svg>
  );
}

/** 카드 영역 위에 띄우는 알약. 옛 값은 흐린 채로 남기고 이 표시가 상태를 말한다. */
export function BusyOverlay({ label = "데이터 업데이트 중…" }: { label?: string }) {
  return (
    <div className="absolute inset-0 flex items-start justify-center pt-8 pointer-events-none">
      <span className="inline-flex items-center gap-2 text-sm font-medium text-gray-700 bg-white/95 border border-gray-200 rounded-full px-4 py-2 shadow-sm">
        <Spinner className="w-4 h-4 text-green-600" /> {label}
      </span>
    </div>
  );
}
