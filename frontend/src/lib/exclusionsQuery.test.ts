// exclusionsQuery.test.ts — 조회 파라미터가 **실제 URL에 실리는가** (적대 리뷰 P1-3)
//
// ## 왜 이 파일이 있나
//
// `fetchNaverSearchTermExclusions`의 `adgroupId → adgroup_id` 배선 한 줄을 지워도
// **1,395개 테스트가 전건 초록**이었다(리뷰 변이 S5). 기존 테스트가 재던 것은 래퍼의
// **함수 인자**였기 때문이다:
//
//     expect(fetchNaverSearchTermExclusions)
//       .toHaveBeenCalledWith(expect.objectContaining({ adgroupId: "grp-full" }))
//
// 인자는 맞는데 URL이 안 실리는 구간이 통째로 사각이었다. 끊기면 서버가 **계정 전체 원장
// 200건**을 돌려주고, 패널은 그것을 그 그룹 것으로 그리며
// *"라이브 70칸 중 우리 원장이 아는 것은 200개 · 전부 압니다"* 를 자신 있게 낸다.
// 이건 이 화면의 코드 주석이 *"`exclude_console_import`에서 이미 한 번 났다"*고 지목한
// 바로 그 병이고, **백엔드 쪽 같은 배선엔 테스트가 있는데 프론트만 무방비였다.**
// 백엔드 테스트가 있다는 것이 프론트 배선의 보증이 아니다.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchNaverSearchTermExclusions, fetchPaoScopeRoster } from "./api";

let seen: string[];

beforeEach(() => {
  seen = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    seen.push(String(url));
    // ★`fetchApi`는 `res.text()`로 읽는다(204·빈 본문 처리 때문) — json만 주면 터진다.
    const body = JSON.stringify({
      total: 0, summary_by_status: {}, today_excluded: 0,
      today_opened: 0, today_restored: 0, rows: [],
    });
    return { ok: true, status: 200, text: async () => body } as unknown as Response;
  }));
});
afterEach(() => vi.unstubAllGlobals());

const qs = () => new URL(seen[0], "http://x").searchParams;

describe("★조회 파라미터가 URL까지 간다 — 함수 인자가 아니라 «나가는 요청»을 잰다", () => {
  it("adgroup_id 가 쿼리에 실린다", async () => {
    await fetchNaverSearchTermExclusions({ adgroupId: "grp-1" });
    expect(qs().get("adgroup_id")).toBe("grp-1");
  });

  it("★adgroup_id 없이 부르면 그 키가 아예 없다 — 빈 값으로 전건 조회가 되면 안 된다", async () => {
    await fetchNaverSearchTermExclusions({ campaignId: "cmp-1" });
    expect(qs().has("adgroup_id")).toBe(false);
    expect(qs().get("campaign_id")).toBe("cmp-1");
  });

  it("status 필터가 실린다 — 빠지면 probation·restored가 「지금 걸려 있는 것」에 섞인다", async () => {
    await fetchNaverSearchTermExclusions({ adgroupId: "grp-1", status: "excluded" });
    expect(qs().get("status")).toBe("excluded");
  });

  it("limit 이 실린다 — 빠지면 기본값(100)이라 200건 그룹이 조용히 잘린다", async () => {
    await fetchNaverSearchTermExclusions({ adgroupId: "grp-1", limit: 200 });
    expect(qs().get("limit")).toBe("200");
  });

  it("campaign_id 와 adgroup_id 를 함께 보내면 둘 다 실린다", async () => {
    await fetchNaverSearchTermExclusions({ campaignId: "cmp-1", adgroupId: "grp-1" });
    expect([qs().get("campaign_id"), qs().get("adgroup_id")]).toEqual(["cmp-1", "grp-1"]);
  });

  it("경로가 이 창구를 가리킨다", async () => {
    await fetchNaverSearchTermExclusions({ adgroupId: "grp-1" });
    expect(seen[0]).toContain("/api/naver/ad/search-term/exclusions");
  });
});

describe("★스코프 로스터도 «나가는 URL»을 잰다 — 화면 테스트는 api를 모킹해 이 구간을 못 본다", () => {
  // ★같은 병이 직전 라운드에 이미 한 번 났다(적대 리뷰 P1-3): 화면이 `{ adgroupId }`를
  //   «인자»로 넘기는 것만 재고 URL에 실리는지는 아무도 안 봤다. 날짜 구간도 똑같다 —
  //   안 실리면 서버가 기본 창(어제로 끝나는 21일)을 돌려주는데, 화면은 사용자가 고른
  //   구간을 보여줬다고 믿게 만든다.
  it("date_from·date_to가 쿼리에 실린다", async () => {
    await fetchPaoScopeRoster({ dateFrom: "2026-08-10", dateTo: "2026-08-20" });
    expect([qs().get("date_from"), qs().get("date_to")]).toEqual(["2026-08-10", "2026-08-20"]);
  });

  it("안 주면 그 키가 아예 없다 — 빈 값으로 서버 기본 창이 조용히 바뀌면 안 된다", async () => {
    await fetchPaoScopeRoster({ days: 21 });
    expect(qs().has("date_from")).toBe(false);
    expect(qs().has("date_to")).toBe(false);
    expect(qs().get("days")).toBe("21");
  });

  it("경로가 로스터 창구를 가리킨다", async () => {
    await fetchPaoScopeRoster({ dateFrom: "2026-08-10" });
    expect(seen[0]).toContain("/api/naver/ad/scope/roster");
  });
});
