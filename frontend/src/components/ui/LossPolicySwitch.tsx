// LossPolicySwitch.tsx — D-NAO-65 UI2. 캠페인별 loss(손실) 대응 정책 2단 스위치.
//
// Jino 원문(D-NAO-65 ①): "캠페인별로 스탑로스를 적용할지 아니면 로스일 경우 내가 지금
//   제안했던 방법(고삐)을 적용하는 버튼". 전역 기본값 = 고삐-일일리셋(DL 스프린트에서 구현
//   완료·불변). 이 스위치는 그 기본값을 바꾸는 게 아니라 **캠페인별 예외**(종전 하드 정지로
//   회귀)를 콘솔에서 Jino가 직접 선언하게 하는 것이다.
//
// ★왜 2단인가(OptimizerSwitch는 3단): 상태가 둘뿐이다 — leash(고삐) / stoploss_pause(하드 정지).
//   NULL/미설정은 leash와 의미가 같아 별도 칸을 두지 않고 '고삐(기본)'로 흡수한다(value ?? leash).
//   한 번 눌러 바꾸는 조작감은 OptimizerSwitch와 동일하게 유지한다.
//
// ★'스톱로스 정지'로 바꿀 때만 확인창(D-65 §0). 이건 UX 예의가 아니라 **회귀 경고**다:
//   기본값(고삐)은 loss 시 입찰만 낮췄다가 익일 재시작해 볼륨을 지키는데(총이익 극대화,
//   D-NAO-59), 이 스위치를 켜면 그 캠페인만 loss 즉시 하드 정지(광고 정지)로 돌아간다.
//   화면이 그 차이를 말하지 않으면 아무도 모른 채 볼륨을 죽인다. 반대 방향(고삐로 복귀)은
//   기본값으로 돌아오는 것이라 위험하지 않다 → 확인창 없음(OptimizerSwitch의 방향성과 동형).
import { useState } from "react";
// ★union 타입은 api.ts에 산다(OptimizerSwitch가 NaverAdOptimizer를 api.ts에서 가져오는 관례와 동형).
//   leash = 고삐(기본, 전역 정책) / stoploss_pause = 예외(하드 정지 회귀). null(미설정)=leash로 해석.
import type { NaverLossPolicy } from "../../lib/api";

const OPTIONS: { key: NaverLossPolicy; label: string; title: string }[] = [
  { key: "leash", label: "고삐(기본)", title: "loss 시 입찰 하향 후 익일 재시작 — 볼륨 유지(D-NAO-65 전역 정책)" },
  { key: "stoploss_pause", label: "스톱로스 정지", title: "loss 시 즉시 하드 정지(광고 정지)로 회귀 — 이 캠페인만 예외" },
];

const ACTIVE: Record<NaverLossPolicy, string> = {
  leash: "bg-owner-ours text-white",
  stoploss_pause: "bg-gray-700 text-white",
};

/** value(null 포함)를 실제 정책값으로 정규화 — 화면·비교의 단일 기준. NULL=leash 불변식. */
export function effectiveLossPolicy(value: NaverLossPolicy | null): NaverLossPolicy {
  return value ?? "leash";
}

/** 확인창이 필요한 방향인가 — 'stoploss_pause'로 갈 때만(고삐 회귀는 무해). 순수 함수(테스트 대상). */
export function needsLossPolicyConfirm(next: NaverLossPolicy): boolean {
  return next === "stoploss_pause";
}

/** 'stoploss_pause'로 넘길 때 뜨는 경고. **이 문구가 이 컴포넌트에서 가장 중요하다.**
 *  기본값(고삐)이 전역 정책이라는 것과, 이 캠페인만 하드 정지로 회귀한다는 걸 정직하게 알린다. */
export function lossPolicyConfirmText(campaignName: string): string {
  return (
    `"${campaignName}"을(를) 스톱로스 정지 정책으로 바꿉니다.\n\n` +
    `· 이 캠페인은 loss(손실) 발생 시 순위 고삐(입찰 하향 후 익일 재시작)를 건너뛰고\n` +
    `  즉시 하드 정지(광고 정지)로 회귀합니다.\n\n` +
    `· 이 설정은 loss(스톱로스) 대응 방식만 바꿉니다 — 평상시 밴드 입찰 최적화\n` +
    `  (bid_up/bid_down)는 계속 동작합니다.\n\n` +
    `기본값은 '고삐'입니다 — D-NAO-65 전역 정책(일일 리셋·바닥 대기·승자 관성)이며,\n` +
    `대부분의 캠페인은 기본값이 총이익에 유리합니다.\n\n` +
    `이 캠페인만 예외로 하드 정지를 적용할까요?`
  );
}

export function LossPolicySwitch({
  campaignId, campaignName, value, onChange, disabled,
}: {
  campaignId: string;
  campaignName: string;
  value: NaverLossPolicy | null;   // null/미설정 = 고삐(기본)
  onChange: (campaignId: string, next: NaverLossPolicy) => Promise<void>;
  disabled?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const current = effectiveLossPolicy(value);

  async function pick(next: NaverLossPolicy) {
    if (next === current || busy || disabled) return;
    // ★'스톱로스 정지'로 갈 때만 확인 — 볼륨을 죽이는 회귀는 그 방향에서만 생긴다.
    if (needsLossPolicyConfirm(next) && !window.confirm(lossPolicyConfirmText(campaignName))) return;
    setBusy(true);
    try {
      await onChange(campaignId, next);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="inline-flex rounded border border-gray-200 overflow-hidden" role="group" aria-label="loss 정책">
      {OPTIONS.map((o) => {
        const active = o.key === current;
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
