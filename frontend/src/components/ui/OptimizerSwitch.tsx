// OptimizerSwitch.tsx — D-NAO-48. 캠페인 관리주체 3단 스위치(PAO / 제3자(대행사) / 수동).
//
// Jino 원문: "우리가 운영중인 광고 종류별 스마트스토어 광고 옆에 토글버튼을 달아서 우리가
// 만든 MOP로 돌릴지 선택하게 해주자". 기능(optimizer)은 D-NAO-13으로 이미 있었지만 콘솔
// 깊숙이 묻힌 <select>였다 — 카나리 확대가 일어나야 할 자리는 1층이다(D-47-c).
//
// ★왜 2단 토글이 아니라 3단인가(D-48-a): 상태가 셋이다(우리/원본MOP/수동). 2단으로 접으면
//   'mop'을 표현할 수 없어 3열 대조의 제3자 열이 죽는다(D-47-g로 03을 태깅해 199,294원을
//   방금 채운 그 열). 한 번 눌러 바꾸는 조작감은 토글과 같게 유지한다.
//
// ★'PAO'로 바꿀 때 확인창을 띄운다(D-48-b). 이건 UX 예의가 아니라 **안전장치**다:
//   D-NAO-13의 핵심 제약 — PAO는 제3자 도구를 **끌 수 없다**(별도 SaaS). 'ours'로
//   지정해도 제3자가 계속 돌면 두 옵티마이저가 같은 캠페인 입찰을 두고 싸운다. Jino가 그쪽
//   콘솔에서 직접 꺼야 한다. 화면이 이 말을 안 하면 아무도 모른다(지금 콘솔 select가 그렇다).
import { useState } from "react";
import type { NaverAdOptimizer } from "../../lib/api";
import { OPTIMIZER_LABEL, OPTIMIZER_TITLE } from "../../lib/optimizerLabels";

// ★설계서 §7-2 개명(122 문서). 'MOP'는 **경쟁 상용 도구**의 이름인데 화면이 그 한 낱말을
//   두 뜻으로 썼다 — 「우리 MOP」(=우리 엔진)와 「원본 MOP」(=그 경쟁 도구). Jino는 「PAO
//   스코프」라고 말하는데 화면은 「우리 MOP」라고 답하고 있었다. 이름은 D-NAO-162로 이미
//   PAO(Profit Ad Optimizer)로 확정돼 있었고 백엔드 주체 판정(`change_actor.py`)엔 「화면에
//   MOP를 쓰지 않는다」가 서 있었다 — **규칙이 한 층에만 있어서** 프론트가 그 밖에 있었다.
//   `none`=「수동」은 이미 멀쩡하므로 손대지 않는다(불필요한 변경).
const OPTIONS: { key: NaverAdOptimizer; label: string; title: string }[] =
  (["ours", "mop", "none"] as const).map((k) => ({
    key: k, label: OPTIMIZER_LABEL[k], title: OPTIMIZER_TITLE[k],
  }));

const ACTIVE: Record<NaverAdOptimizer, string> = {
  ours: "bg-owner-ours text-white",
  mop: "bg-owner-mop text-white",
  none: "bg-gray-200 text-gray-700",
};

/** 'ours'로 넘길 때만 뜨는 경고. **이 문구가 이 컴포넌트에서 가장 중요하다.** */
function confirmText(campaignName: string): string {
  return (
    `"${campaignName}"을(를) ${OPTIMIZER_LABEL.ours}로 넘깁니다.\n\n` +
    `· ${OPTIMIZER_LABEL.ours}가 이 캠페인의 입찰을 자동으로 바꾸기 시작합니다(사람 승인 게이트는 유지).\n\n` +
    `⚠️ ${OPTIMIZER_LABEL.mop} 쪽은 자동으로 꺼지지 않습니다.\n` +
    `${OPTIMIZER_LABEL.ours}는 그쪽을 끌 수 없습니다(별도 SaaS). 그 콘솔에서 이 캠페인을 직접 꺼주세요.\n` +
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
    // ★'ours'로 갈 때만 확인 — 제3자와의 충돌은 그 방향에서만 생긴다(우리가 손대기 시작하는 순간).
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
