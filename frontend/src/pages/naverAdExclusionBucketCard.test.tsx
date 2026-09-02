// @vitest-environment jsdom
//
// naverAdExclusionBucketCard.test.tsx — 버킷 카드가 «전부»를 그린다는 약속을 **화면에서** 지킨다
//   (D-NAO-176 적대 리뷰 2R에서 살아남은 변이 M2 상환).
//
// ## 왜 순수 함수 테스트로는 부족했나
// `bucketKeysToRender`는 순수 함수로 이미 테스트돼 있다(bucketKeysToRender.test.ts).
// 그런데 **`BucketSummary`가 그 함수를 쓰는지는 아무것도 안 지켰다** — 호출부 한 줄을 옛
// `BUCKET_ORDER.map`으로 되돌리면 tsc도 조용하고 프론트 테스트 전건이 초록이었다.
// 즉 사흘 새 세 번 난 결함(unverifiable · type_unknown_groups · already_excluded)이
// 네 번째로 재발하는 경로가 그대로 열려 있었다.
//
// ★이건 위키 부채 `write-to-the-binding-layer`(B-4)의 실증 사례다: **기제를 만들고 결합 층을
//   안 지키면 기제는 없는 것과 같다.** 그래서 여기서는 함수가 아니라 «화면에 글자가 나오는가»를
//   단언한다 — 백엔드가 우리가 모르는 버킷을 보내도 사람이 그걸 볼 수 있어야 한다.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => {
  const pending = () => new Promise<never>(() => {});
  return { pending, list: null as unknown };
});

vi.mock("../lib/api", () => ({
  getSearchTermExclusionList: () => Promise.resolve(h.list),
  getSearchTermExclusionSurvival: () => h.pending(),
  // ★설계서 §5-4 슬롯 패널이 이 페이지에 붙으면서 생긴 의존 — 이 파일이 보는 것과는
  //   무관하지만, 화이트리스트 mock이라 빠지면 페이지가 통째로 터진다.
  getSearchTermExclusionSlots: () => h.pending(),
  getSearchTermExclusionScorecard: () => h.pending(),
  postSearchTermExecution: () => h.pending(),
  postSearchTermExecutionDetect: () => h.pending(),
}));

import NaverAdExclusionList from "./NaverAdExclusionList";

const bucket = (terms: number, cost: number) => ({ terms, cost });

const LIST = {
  window: { days: 30, from: "2026-07-10", to: "2026-08-09" },
  freshness: { latest_ad_date: "2026-08-09", latest_synced_at: "2026-08-12T07:40:00",
               as_of: "2026-08-12", lag_days: 3 },
  maturity: { lag_days: 3, excluded_from: "2026-08-10", excluded_to: "2026-08-12",
              excluded_terms: 751, excluded_cost: 1_000_000, why: "최근 3일은 전환이 덜 잡힌다" },
  totals: { terms: 7150, cost: 20_524_919, conv_amt: 0 },
  gates: { min_click: 10, round_cap: 50 },
  candidates: [],
  candidate_cost: 0,
  buckets: {
    already_excluded: bucket(1, 31_411),
    insufficient_sample: bucket(3886, 0),
    bep_unknown: bucket(70, 0),
    powerlink_undecidable: bucket(2255, 0),
    profitable: bucket(876, 0),
    capped_out: { ...bucket(13, 0), why: "다음 회차 대상" },
    maturity_excluded: bucket(751, 0),
  } as Record<string, { terms: number; cost: number; why?: string }>,
  revert_howto: "콘솔에서 삭제하면 복구된다",
  generated_at: "2026-08-12T20:45:00",
};

const renderPage = () =>
  render(
    <MemoryRouter>
      <NaverAdExclusionList />
    </MemoryRouter>,
  );

afterEach(() => cleanup());

describe("버킷 카드 — «후보에서 빠진 것도 전부»", () => {
  it("★백엔드가 우리가 모르는 버킷을 보내도 화면에 나온다 (M2 상환)", async () => {
    h.list = {
      ...LIST,
      buckets: { ...LIST.buckets, some_future_bucket_v2: bucket(7, 12_345) },
    };
    renderPage();

    // 라벨이 없어도 키 이름 그대로라도 보인다 — 못생긴 줄 하나가 안 보이는 손실보다 낫다.
    expect(await screen.findByText("some_future_bucket_v2")).toBeTruthy();
  });

  it("이미 제외된 몫이 «이미 제외됨»으로, 건수·비용과 함께 보인다", async () => {
    h.list = LIST;
    renderPage();

    expect(await screen.findByText(/이미 제외됨/)).toBeTruthy();
    expect(screen.getByText(/30일 창이라 당분간 계속 집계된다/)).toBeTruthy();
  });

  it("알려진 버킷도 하나도 빠지지 않는다", async () => {
    h.list = LIST;
    renderPage();

    for (const label of ["이미 제외됨(우리 장부에 있음)", "표본 부족", "BEP 미확인",
                         "파워링크 판정 불가", "BEP 이상(수익성 있음)", "라운드 상한 초과",
                         "성숙도 미달 제외"]) {
      expect(await screen.findByText(label)).toBeTruthy();
    }
  });
});
