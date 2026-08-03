// PeriodTabs.tsx — 날짜 구간 선택 UI(D-NAO-54에서 커맨드 센터가 만든 것을 공용화).
// 순수 로직은 `lib/periodRange.ts`(vitest가 .tsx를 안 잡으므로 분리 — 검증 규칙은 테스트로
// 고정되어야 한다), 상태 훅은 `lib/usePeriod.ts`(fast-refresh는 컴포넌트만 내보내는 파일에서
// 동작한다 — 훅을 여기 두면 이 파일 편집마다 상태가 날아간다).
import { PERIOD_PRESETS, kstDate } from "../../lib/periodRange";
import type { Period } from "../../lib/usePeriod";

export function PeriodTabs({ p }: { p: Period }) {
  return (
    <div className="px-4 py-2 border-b border-gray-100 flex items-center gap-1 flex-wrap">
      {PERIOD_PRESETS.map((preset) => (
        <button
          key={preset.key}
          type="button"
          onClick={() => p.setKey(preset.key)}
          className={`px-2 py-0.5 text-xs rounded-full ${
            p.key === preset.key ? "bg-blue-50 text-blue-700 font-semibold" : "text-gray-600 hover:bg-gray-100"
          }`}
        >
          {preset.label}
        </button>
      ))}
      <button
        type="button"
        onClick={() => p.setKey("custom")}
        className={`px-2 py-0.5 text-xs rounded-full ${
          p.key === "custom" ? "bg-blue-50 text-blue-700 font-semibold" : "text-gray-600 hover:bg-gray-100"
        }`}
      >
        기간 지정
      </button>
      {p.key === "custom" && (
        <span className="flex items-center gap-1 ml-1">
          <input
            type="date" value={p.custom.from} max={kstDate(0)}
            onChange={(e) => p.setCustom({ ...p.custom, from: e.target.value })}
            className="text-xs border border-gray-200 rounded px-1 py-0.5"
          />
          <span className="text-xs text-gray-400">~</span>
          <input
            type="date" value={p.custom.to} max={kstDate(0)}
            onChange={(e) => p.setCustom({ ...p.custom, to: e.target.value })}
            className="text-xs border border-gray-200 rounded px-1 py-0.5"
          />
        </span>
      )}
    </div>
  );
}
