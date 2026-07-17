// OptimizerSwitch.tsx — D-NAO-48. 캠페인 관리주체 3단 스위치(우리 MOP / 원본 MOP / 수동).
//
// Jino 원문: "우리가 운영중인 광고 종류별 스마트스토어 광고 옆에 토글버튼을 달아서 우리가
// 만든 MOP로 돌릴지 선택하게 해주자". 기능(optimizer)은 D-NAO-13으로 이미 있었지만 콘솔
// 깊숙이 묻힌 <select>였다 — 카나리 확대가 일어나야 할 자리는 1층이다(D-47-c).
//
// ★왜 2단 토글이 아니라 3단인가(D-48-a): 상태가 셋이다(우리/원본MOP/수동). 2단으로 접으면
//   'mop'을 표현할 수 없어 3열 대조의 MOP 열이 죽는다(D-47-g로 03을 태깅해 199,294원을
//   방금 채운 그 열). 한 번 눌러 바꾸는 조작감은 토글과 같게 유지한다.
//
// ★'우리 MOP'로 바꿀 때 확인창을 띄운다(D-48-b). 이건 UX 예의가 아니라 **안전장치**다:
//   D-NAO-13의 핵심 제약 — 우리 프로그램은 원본 MOP를 **끌 수 없다**(별도 SaaS). 'ours'로
//   지정해도 MOP가 계속 돌면 두 옵티마이저가 같은 캠페인 입찰을 두고 싸운다. Jino가 MOP
//   콘솔에서 직접 꺼야 한다. 화면이 이 말을 안 하면 아무도 모른다(지금 콘솔 select가 그렇다).
import { useState } from "react";
import type { NaverAdOptimizer } from "../../lib/api";

const OPTIONS: { key: NaverAdOptimizer; label: string; title: string }[] = [
  { key: "ours", label: "우리 MOP", title: "우리 프로그램이 제안·실행" },
  { key: "mop", label: "원본 MOP", title: "원본 MOP가 소유 — 우리는 손대지 않음(진단·리포트만)" },
  { key: "none", label: "수동", title: "아무 자동화도 하지 않음" },
];

const ACTIVE: Record<NaverAdOptimizer, string> = {
  ours: "bg-owner-ours text-white",
  mop: "bg-owner-mop text-white",
  none: "bg-gray-200 text-gray-700",
};

/** 'ours'로 넘길 때만 뜨는 경고. **이 문구가 이 컴포넌트에서 가장 중요하다.** */
function confirmText(campaignName: string): string {
  return (
    `"${campaignName}"을(를) 우리 MOP로 넘깁니다.\n\n` +
    `· 우리 프로그램이 이 캠페인의 입찰을 자동으로 바꾸기 시작합니다(사람 승인 게이트는 유지).\n\n` +
    `⚠️ 원본 MOP는 자동으로 꺼지지 않습니다.\n` +
    `우리 프로그램은 MOP를 끌 수 없습니다(별도 SaaS). MOP 콘솔에서 이 캠페인을 직접 꺼주세요.\n` +
    `안 끄면 두 시스템이 같은 캠페인 입찰을 두고 서로 덮어씁니다.\n\n` +
    `계속할까요?`
  );
}

export function OptimizerSwitch({
  campaignId, campaignName, value, onChange, disabled,
}: {
  campaignId: string;
  campaignName: string;
  value: NaverAdOptimizer;
  onChange: (campaignId: string, next: NaverAdOptimizer) => Promise<void>;
  disabled?: boolean;
}) {
  const [busy, setBusy] = useState(false);

  async function pick(next: NaverAdOptimizer) {
    if (next === value || busy || disabled) return;
    // ★'ours'로 갈 때만 확인 — MOP 충돌은 그 방향에서만 생긴다(우리가 손대기 시작하는 순간).
    //   'ours'에서 빠져나오는 건 우리가 손을 떼는 것이라 위험하지 않다.
    if (next === "ours" && !window.confirm(confirmText(campaignName))) return;
    setBusy(true);
    try {
      await onChange(campaignId, next);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="inline-flex rounded border border-gray-200 overflow-hidden" role="group" aria-label="관리 주체">
      {OPTIONS.map((o) => {
        const active = o.key === value;
        return (
          <button
            key={o.key}
            type="button"
            title={o.title}
            aria-pressed={active}
            disabled={busy || disabled}
            onClick={() => pick(o.key)}
            className={`px-2.5 py-1 text-xs whitespace-nowrap transition-colors disabled:opacity-50 ${
              active ? ACTIVE[o.key] : "bg-white text-gray-500 hover:bg-gray-50"
            }`}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
