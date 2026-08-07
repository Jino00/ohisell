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

export type PeriodPreset = "today" | "yesterday" | "7d" | "30d" | "90d" | "1y";

const PRESET_LABEL: Record<PeriodPreset, string> = {
  today: "오늘", yesterday: "어제", "7d": "7일", "30d": "30일", "90d": "90일", "1y": "1년",
};

export function PeriodRangeBar({
  label, from, to, onFrom, onTo,
  presets = ["today", "yesterday", "7d", "30d", "90d", "1y"],
  note, right, title = "조회 조건",
}: {
  /** 날짜 축 이름 — 「발주일」·「판매일」처럼 **무엇의 날짜인지**. 화면마다 다르다. */
  label: string;
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
  /** 오늘까지의 최근 N일. N=1이면 오늘 하루. */
  const recent = (days: number) => { onFrom(kstDate(-(days - 1))); onTo(today); };
  /** 하루짜리 창(어제처럼 시작=끝). 최근 N일과 달리 오늘을 포함하지 않는다. */
  const singleDay = (agoDays: number) => { const d = kstDate(-agoDays); onFrom(d); onTo(d); };
  const active = (f: string, t: string) => from === f && to === t;

  const SPEC: Record<PeriodPreset, { f: string; t: string; go: () => void }> = {
    today: { f: today, t: today, go: () => recent(1) },
    yesterday: { f: kstDate(-1), t: kstDate(-1), go: () => singleDay(1) },
    "7d": { f: kstDate(-6), t: today, go: () => recent(7) },
    "30d": { f: kstDate(-29), t: today, go: () => recent(30) },
    "90d": { f: kstDate(-89), t: today, go: () => recent(90) },
    "1y": { f: kstDate(-364), t: today, go: () => recent(365) },
  };

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
