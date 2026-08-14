// periodRangeBarPresets.test.ts — 공용 기간 바의 프리셋이 **어느 창을 가리키는가**.
//
// 왜 생겼나(적대 리뷰 1R P1-1, 2026-08-14): 운영 패널을 공용 바로 옮기면서 기간 판정이
//   백엔드 `days`에서 **프론트로 이동**했다. 그런데 옮겨간 자리엔 테스트가 0건이라,
//   「오늘」이 어제를 가리키게 바꿔도 백엔드 5,545건 + 프론트 387건이 **전부 통과**했다.
//
//   「오늘」이 어제가 되면 백엔드의 `is_today_only`(= dfrom==dto==오늘)가 False가 되어
//   당일 광고 축을 통째로 벗어난다. 네이버는 시간별 스냅샷 «관측 최대치» 대신 `ad_costs`를,
//   쿠팡은 `latest_ad`·`ad_today` 갈래를 잃는다 — 2026-08-06에 「오늘 매출 − 어제 광고비」로
//   이익 부호가 뒤집혔던(−202,434원 적자로 표시, 실제 +134,953원 흑자) 그 경로다.
//
// 이 파일이 죽이는 변이: 「오늘」→어제 · 7일→8일 · 15/30/90일 창 이동 · 어제→오늘.
import { describe, it, expect } from "vitest";
import { presetWindow } from "./PeriodRangeBar";

const T = "2026-08-14";   // 기준일 고정 — 오늘이 언제냐에 따라 통과 여부가 바뀌면 안 된다

describe("기간 프리셋이 가리키는 창", () => {
  it("「오늘」은 오늘 하루다 — 시작=끝=오늘", () => {
    expect(presetWindow("today", T)).toEqual({ f: "2026-08-14", t: "2026-08-14" });
  });

  it("「어제」는 어제 하루다 — 오늘을 포함하지 않는다", () => {
    expect(presetWindow("yesterday", T)).toEqual({ f: "2026-08-13", t: "2026-08-13" });
  });

  it("「7일」은 오늘까지 7일(경계 포함)", () => {
    // 종전 백엔드 `_date_range(7)` = today-6 ~ today 와 **정확히 같아야** 한다.
    expect(presetWindow("7d", T)).toEqual({ f: "2026-08-08", t: "2026-08-14" });
  });

  it("「15일」은 오늘까지 15일 — 운영 패널이 원래 갖고 있던 버튼", () => {
    expect(presetWindow("15d", T)).toEqual({ f: "2026-07-31", t: "2026-08-14" });
  });

  it("「30일」은 오늘까지 30일", () => {
    expect(presetWindow("30d", T)).toEqual({ f: "2026-07-16", t: "2026-08-14" });
  });

  it("「90일」은 오늘까지 90일 — 백엔드 상한(90)에 정확히 닿되 넘지 않는다", () => {
    const w = presetWindow("90d", T);
    expect(w).toEqual({ f: "2026-05-17", t: "2026-08-14" });
    const span = (Date.parse(`${w.t}T00:00:00Z`) - Date.parse(`${w.f}T00:00:00Z`))
      / 86_400_000 + 1;
    expect(span).toBe(90);
  });

  it("월·연 경계를 넘어도 하루도 밀리지 않는다", () => {
    expect(presetWindow("7d", "2026-01-03")).toEqual({ f: "2025-12-28", t: "2026-01-03" });
    expect(presetWindow("yesterday", "2026-03-01")).toEqual({ f: "2026-02-28", t: "2026-02-28" });
  });

  it("모든 프리셋이 시작 ≤ 끝을 지킨다", () => {
    for (const k of ["today", "yesterday", "7d", "15d", "30d", "90d", "1y"] as const) {
      const w = presetWindow(k, T);
      expect(w.f <= w.t).toBe(true);
    }
  });
});
