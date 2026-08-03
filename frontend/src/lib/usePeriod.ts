// usePeriod.ts — 기간 선택 상태 훅(D-NAO-54에서 커맨드 센터가 만든 것을 공용화).
// 검증·프리셋 규칙은 `periodRange.ts`(순수 함수, 테스트 대상), 표시는 `ui/PeriodTabs.tsx`.
//
// ★상태는 공유하지 않는다(패널마다 독립 usePeriod). 의도된 것이다: 외부 감지는 하루 1회
//   (07:35 entity_sync)라 우리 시간당 집행과 자연스러운 창이 다르다. 대신 각 카드의 탭이
//   보이고 라벨에 실제 날짜가 병기되므로 숨은 상태가 아니다(Jino 결정 2026-07-17).
import { useState } from "react";
import {
  PERIOD_PRESETS, customRangeError, kstDate, rangeLabel,
  type DateRange, type PeriodKey,
} from "./periodRange";

export type Period = ReturnType<typeof usePeriod>;

/** 기간 상태. range는 원시 문자열 2개라 useAsyncData deps에 그대로 넣어도 안정적이다. */
export function usePeriod(initialKey: PeriodKey = "today") {
  const [key, setKey] = useState<PeriodKey>(initialKey);
  // ★초기화 함수 형태 — 객체 리터럴로 쓰면 Intl.DateTimeFormat 2개가 매 렌더 생성되고 버려진다.
  const [custom, setCustom] = useState<DateRange>(() => ({ from: kstDate(-7), to: kstDate(0) }));
  const preset = PERIOD_PRESETS.find((p) => p.key === key);
  const range = preset ? preset.range() : custom;
  // 잘못된 구간은 백엔드도 422로 막지만(조용한 빈 결과 = "변경 없음"으로 읽히므로),
  // 화면에서 먼저 잡아 요청 자체를 안 보낸다 — 422 원문은 사용자에게 무의미하다.
  const error = preset ? null : customRangeError(custom);
  const label = rangeLabel(preset ? preset.label : null, range);
  return { key, setKey, custom, setCustom, range, error, label };
}
