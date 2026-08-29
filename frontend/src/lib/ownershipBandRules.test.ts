// ownershipBandRules 테스트 — 「모름이 0으로 둔갑하지 않는가」가 이 파일의 전부다.
import { describe, expect, it } from "vitest";
import {
  matchesBandFilter,
  ownershipBadgeText,
  emptyReasonFor,
  type BandFilter,
} from "./ownershipBandRules";
import type { NaverOwnershipCampaignSlot } from "./api";

function slot(over: Partial<NaverOwnershipCampaignSlot> = {}): NaverOwnershipCampaignSlot {
  return {
    band: "not_pao",
    label: "PAO가 안 돌린 광고",
    partial: false,
    pao_adgroups: 0,
    not_pao_adgroups: 3,
    transition_adgroups: 0,
    unknown_adgroups: 0,
    adgroups: 3,
    ...over,
  };
}

describe("matchesBandFilter", () => {
  it("전체 필터는 판정이 없어도 통과시킨다", () => {
    expect(matchesBandFilter(undefined, "all")).toBe(true);
    expect(matchesBandFilter(slot(), "all")).toBe(true);
  });

  it("★판정이 없는 캠페인은 PAO에도 비PAO에도 안 든다 — 모름을 0으로 밀지 않는다", () => {
    expect(matchesBandFilter(undefined, "pao")).toBe(false);
    expect(matchesBandFilter(undefined, "not_pao")).toBe(false);
  });

  it("한 그룹이라도 PAO면 PAO 목록이다 (「광고그룹만도 가져올 수 있잖아」)", () => {
    const partial = slot({ pao_adgroups: 1, not_pao_adgroups: 57, adgroups: 58, partial: true });
    expect(matchesBandFilter(partial, "pao")).toBe(true);
    expect(matchesBandFilter(partial, "not_pao")).toBe(false);
  });

  it("캠페인을 통째로 가져온 경우도 PAO 목록이다", () => {
    const whole = slot({ pao_adgroups: 58, not_pao_adgroups: 0, adgroups: 58, band: "pao" });
    expect(matchesBandFilter(whole, "pao")).toBe(true);
    expect(matchesBandFilter(whole, "not_pao")).toBe(false);
  });

  it("PAO 그룹이 0이면 비PAO 목록이다", () => {
    expect(matchesBandFilter(slot(), "not_pao")).toBe(true);
    expect(matchesBandFilter(slot(), "pao")).toBe(false);
  });

  it("두 필터는 서로 배타적이다 — 같은 캠페인이 양쪽에 뜨지 않는다", () => {
    const cases = [
      slot(),
      slot({ pao_adgroups: 1, adgroups: 58, partial: true }),
      slot({ pao_adgroups: 58, adgroups: 58 }),
      slot({ unknown_adgroups: 3, not_pao_adgroups: 0, adgroups: 3, band: "unknown" }),
    ];
    for (const s of cases) {
      expect(matchesBandFilter(s, "pao") && matchesBandFilter(s, "not_pao")).toBe(false);
    }
  });
});

describe("ownershipBadgeText", () => {
  it("부분 관할은 분모를 같이 보여준다", () => {
    expect(
      ownershipBadgeText(slot({ partial: true, pao_adgroups: 1, adgroups: 58 })),
    ).toBe("그날 PAO 부분 담당 (1/58 그룹)");
  });

  it("부분이 아니면 백엔드 라벨을 그대로 쓴다 — 프론트가 문구를 새로 만들지 않는다", () => {
    expect(ownershipBadgeText(slot({ label: "PAO가 돌린 광고" }))).toBe("그날 PAO가 돌린 광고");
  });
});

describe("emptyReasonFor", () => {
  it("빈 목록은 밴드에 맞는 이유를 말한다", () => {
    const seen = new Set<string>();
    for (const f of ["all", "pao", "not_pao"] as BandFilter[]) seen.add(emptyReasonFor(f));
    expect(seen.size).toBe(3);
  });
});
