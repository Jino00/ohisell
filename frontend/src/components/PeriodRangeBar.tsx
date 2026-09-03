// PeriodRangeBar.tsx — 「조회 조건」 기간 바 (날짜 두 개 + 프리셋 버튼 + 축 설명).
//
// 왜 공용인가 (Jino 2026-08-07): *"매출손익 화면의 캘린더 부분을 통합 대사처럼 만들자"*.
//   같은 UI를 두 화면이 각자 들고 있으면 곧 갈라진다 — 이 저장소는 광고 축이 그렇게 갈라져
//   63일 43,147,487원이 통째로 빠진 적이 있다. 화면 요소도 같은 이유로 한 곳에 둔다.
//
// ★날짜 축 이름(`label`)과 설명(`note`)은 **화면마다 다르다**. 통합 대사는 「발주일」이고
//   매출·손익은 「판매일」이다. 같은 컴포넌트를 쓰되 그 축이 무엇인지는 화면이 말해야 한다 —
//   축을 안 적으면 사용자는 자기가 아는 축으로 읽는다.
//
// ★날짜는 **`lib/periodRange.kstDate`만** 쓴다. 그 파일이 "프론트에서 유일하게 타임존이 걸린
//   코드"라고 스스로 못 박고 테스트까지 갖고 있다. 두 화면이 각자 `toLocaleString` 기반
//   헬퍼를 들고 있었는데(같은 함수 두 벌), 이 저장소는 타임존으로 이미 두 번 사고를 냈고
//   공통점이 "타임존 코드에 테스트가 없었다"는 것이었다 — 정의를 늘리지 않는다.
import type { ReactNode } from "react";
import { kstDate } from "../lib/periodRange";
import { Button, Card } from "./ui";

// ★"15d"는 운영 패널(쿠팡·스마트스토어)이 원래 갖고 있던 버튼이다 — 그 화면들을 이 공용
//   바로 옮기면서 프리셋 하나가 조용히 사라지면 그건 기능 회귀다. 여기에 더해 공유한다.
// ★"180d"는 성과 화면의 「누가 돌린 광고인가」가 원래 갖고 있던 버튼이다(30·90·180).
//   그 화면을 이 공용 바로 옮기면서 프리셋 하나가 조용히 사라지면 그건 기능 회귀다 —
//   "15d"를 더한 것과 **같은 이유**로 여기에 더해 공유한다.
export type PeriodPreset =
  | "today" | "yesterday" | "7d" | "15d" | "21d" | "30d" | "90d" | "180d" | "1y";

const PRESET_LABEL: Record<PeriodPreset, string> = {
  today: "오늘", yesterday: "어제", "7d": "7일", "15d": "15일", "21d": "21일",
  "30d": "30일", "90d": "90일", "180d": "180일", "1y": "1년",
};

/** 프리셋이 가리키는 **실제 창**(시작일·종료일). 순수 함수 — 테스트가 여기를 못박는다.
 *
 *  ★왜 밖으로 뺐나(적대 리뷰 1R P1-1): 기간 판정이 백엔드 `days`에서 이 컴포넌트로
 *    옮겨왔는데, 컴포넌트 안에 있는 동안엔 «「오늘」이 어제를 가리키게» 바꿔도 프론트·백엔드
 *    테스트 387건이 전부 통과했다. 판정이 사는 곳에 테스트가 없으면 그 판정은 안 지켜진다.
 *    「오늘」이 어제가 되면 백엔드 `is_today_only`가 False가 되어 당일 광고 축을 통째로
 *    벗어난다 — 2026-08-06에 이익 부호가 뒤집혔던 바로 그 경로다.
 */
export function presetWindow(k: PeriodPreset, today: string = kstDate(0)): { f: string; t: string } {
  /** today 기준 n일 전. 인자가 today면 kstDate와 같은 축이다. */
  const shift = (n: number) => {
    const ms = Date.parse(`${today}T00:00:00Z`) + n * 86_400_000;
    return new Date(ms).toISOString().slice(0, 10);
  };
  switch (k) {
    case "today":     return { f: today, t: today };
    case "yesterday": return { f: shift(-1), t: shift(-1) };
    case "7d":        return { f: shift(-6), t: today };
    case "15d":       return { f: shift(-14), t: today };
    case "21d":       return { f: shift(-20), t: today };
    case "30d":       return { f: shift(-29), t: today };
    case "90d":       return { f: shift(-89), t: today };
    case "180d":      return { f: shift(-179), t: today };
    case "1y":        return { f: shift(-364), t: today };
  }
}

export function PeriodRangeBar({
  label, from, to, onFrom, onTo,
  presets = ["today", "yesterday", "7d", "30d", "90d", "1y"],
  windowFor = presetWindow,
  note, right, title = "조회 조건",
}: {
  /** 날짜 축 이름 — 「발주일」·「판매일」처럼 **무엇의 날짜인지**. 화면마다 다르다. */
  label: string;
  /** 프리셋이 가리키는 창. ★창 관례가 다른 화면(D-0 제외)은 `presetWindowExcludingToday`를
   *  넘긴다 — 안 넘기면 프리셋이 오늘을 보내 서버가 매번 창을 자른다. */
  windowFor?: (k: PeriodPreset, today?: string) => { f: string; t: string };
  from: string; to: string;
  onFrom: (v: string) => void; onTo: (v: string) => void;
  presets?: PeriodPreset[];
  /** 축의 뜻·주의사항. 이 자리가 비면 사용자가 빈 화면을 «수집 실패»로 오독한다. */
  note?: ReactNode;
  /** 화면 고유 필터(체크박스 등)를 오른쪽에 붙이는 슬롯. */
  right?: ReactNode;
  title?: string;
}) {
  const today = kstDate(0);
  const active = (f: string, t: string) => from === f && to === t;

  // ★버튼이 «쓰는 창»과 «눌린 것처럼 보이는 창»은 **같은 함수 한 곳**에서 나온다.
  //   적대 리뷰 2R P1-1: 종전엔 하이라이트만 `presetWindow`를 쓰고 클릭 동작은 별도
  //   `recent()`/`singleDay()`가 했다. 그래서 「오늘」 버튼의 **동작만** 어제로 바꾸는 변이가
  //   402건을 통과했다 — 테스트가 못박은 게 표시용 사본이었기 때문이다. 게다가 두 사본은
  //   날짜 엔진마저 달랐다(문자열 UTC 산술 vs `kstDate`). 이 파일 헤더가 «정의를 늘리지
  //   않는다»고 못박았는데 그 수정이 오히려 한 벌 늘렸었다.
  const apply = (k: PeriodPreset) => {
    const w = windowFor(k, today);
    onFrom(w.f);
    onTo(w.t);
  };

  const SPEC = Object.fromEntries(
    (Object.keys(PRESET_LABEL) as PeriodPreset[]).map((k) => {
      const w = windowFor(k, today);
      return [k, { ...w, go: () => apply(k) }];
    }),
  ) as Record<PeriodPreset, { f: string; t: string; go: () => void }>;

  return (
    <Card title={title}>
      <div className="flex flex-wrap items-center gap-3 px-4 py-3">
        <label className="text-xs text-gray-500">{label}</label>
        <input
          type="date" value={from} onChange={(e) => onFrom(e.target.value)}
          className="rounded border border-gray-300 px-2 py-1 text-sm"
        />
        <span className="text-gray-400">~</span>
        <input
          type="date" value={to} onChange={(e) => onTo(e.target.value)}
          className="rounded border border-gray-300 px-2 py-1 text-sm"
        />
        {/* ★지금 걸린 기간과 같은 프리셋은 눌린 상태로 보인다 — 어느 창을 보고 있는지가
            날짜 두 개를 읽어야만 알 수 있으면 오독한다. */}
        <div className="flex flex-wrap gap-1">
          {presets.map((k) => (
            <Button
              key={k}
              variant={active(SPEC[k].f, SPEC[k].t) ? "primary" : "secondary"}
              onClick={SPEC[k].go}
            >
              {PRESET_LABEL[k]}
            </Button>
          ))}
        </div>
        {right && <div className="ml-auto flex flex-wrap items-center gap-3">{right}</div>}
      </div>
      {note && <p className="px-4 pb-3 text-xs leading-relaxed text-gray-400">{note}</p>}
    </Card>
  );
}
