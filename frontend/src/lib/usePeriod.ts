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

/** 날짜 두 칸(`PeriodRangeBar`)용 기간 상태 — `usePeriod`의 프리셋 키 모델 대신
 *  **from/to 원시 문자열 두 개**만 든다.
 *
 *  왜 만들었나 (2026-09-03, PAO 캘린더 통일 — Jino *"새로 만든 캘린더를 Pao내의 모든
 *  캘린더에 똑같이 만들어줘"*): `PeriodRangeBar`는 키가 아니라 from/to를 받는다. 그 어댑터
 *  6줄(상태 2개 + label + error)을 옮겨가는 화면마다 인라인으로 두면 **네 벌이 되고**,
 *  이 저장소는 정확히 그렇게 갈라진 캘린더를 오늘 통일하는 중이다. 한 곳에 둔다.
 *
 *  ★`maxSpanDays`는 **호출부가 정한다** — API마다 상한이 다르다(변경 이력 365 / 매출·손익 90).
 *    하나로 뭉치면 한쪽이 반드시 틀린다(`customRangeError` 머리말과 같은 이유).
 *  ★반환 모양을 `usePeriod`와 같은 이름(`range`·`label`·`error`)으로 맞췄다 — 옮겨가는
 *    화면의 하위 컴포넌트가 그 이름을 이미 받고 있어서, 이름을 바꾸면 캘린더만 바꾸는
 *    이번 변경이 표 렌더까지 번진다. */
export function usePeriodRange(
  initial: DateRange = { from: kstDate(0), to: kstDate(0) },
  maxSpanDays?: number,
) {
  const [from, setFrom] = useState(initial.from);
  const [to, setTo] = useState(initial.to);
  const range: DateRange = { from, to };
  return {
    from, to, setFrom, setTo, range,
    label: rangeLabel(null, range),
    error: customRangeError(range, undefined, maxSpanDays),
  };
}

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
