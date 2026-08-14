// @vitest-environment jsdom
//
// periodRangeBarClick.test.tsx — 프리셋 버튼을 **눌렀을 때 실제로 나가는 창**.
//
// 적대 리뷰 2R P1-1: 1R 수정이 `SPEC`을 둘로 갈랐다 — `presetWindow`는 `f`/`t`(하이라이트)만
//   채우고, 클릭 시 실행되는 건 별도 `recent()`/`singleDay()`였다. 그래서 「오늘」 버튼의
//   **동작만** 어제로 바꾸는 변이가 402건을 전부 통과했다. 테스트가 못박은 게 «표시용 사본»
//   이었기 때문이다. 순수 함수 테스트(periodRangeBarPresets)는 이 구멍을 원리적으로 못 본다.
//
// 그래서 이 파일은 **클릭 경로**를 본다: 버튼을 눌러 `onFrom`/`onTo`에 무엇이 들어가는가.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { PeriodRangeBar, presetWindow } from "./PeriodRangeBar";
import { kstDate } from "../lib/periodRange";

afterEach(cleanup);

function clickPreset(label: string) {
  const onFrom = vi.fn();
  const onTo = vi.fn();
  render(
    <PeriodRangeBar
      label="판매일" from="" to="" onFrom={onFrom} onTo={onTo}
      presets={["today", "yesterday", "7d", "15d", "30d", "90d"]}
    />
  );
  fireEvent.click(screen.getByText(label));
  return { onFrom, onTo };
}

describe("프리셋 버튼을 누르면 나가는 창", () => {
  it.each([
    ["오늘", "today"],
    ["어제", "yesterday"],
    ["7일", "7d"],
    ["15일", "15d"],
    ["30일", "30d"],
    ["90일", "90d"],
  ] as const)("「%s」 클릭 = presetWindow(%s)와 같은 창", (label, key) => {
    const { onFrom, onTo } = clickPreset(label);
    const w = presetWindow(key, kstDate(0));
    expect(onFrom).toHaveBeenCalledWith(w.f);
    expect(onTo).toHaveBeenCalledWith(w.t);
  });

  it("「오늘」은 오늘 하루를 넣는다 — 어제가 들어가면 백엔드가 당일 광고 축을 잃는다", () => {
    const { onFrom, onTo } = clickPreset("오늘");
    expect(onFrom).toHaveBeenCalledWith(kstDate(0));
    expect(onTo).toHaveBeenCalledWith(kstDate(0));
  });
});
