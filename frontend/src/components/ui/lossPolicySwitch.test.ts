// lossPolicySwitch.test.ts — D-NAO-65 UI2. loss 정책 스위치의 확인창·정규화 로직 고정.
// ★존재 이유: 이 스위치를 '스톱로스 정지'로 켜면 그 캠페인만 loss 즉시 하드 정지로 회귀해
//   볼륨을 죽인다(총이익 극대화 D-NAO-59 역행). 그 회귀는 확인창으로만 열려야 하고, 반대
//   방향(고삐 복귀)은 무해하니 확인창이 없어야 한다. NULL=고삐(기본) 정규화도 여기서 고정한다.
//   (repo 관례: 렌더 대신 순수 함수 단위 테스트 — executeConfirmText.test.ts와 동형.)
import { describe, it, expect } from "vitest";
import {
  lossPolicyConfirmText,
  needsLossPolicyConfirm,
  effectiveLossPolicy,
} from "./LossPolicySwitch";

describe("needsLossPolicyConfirm — 확인창은 하드 정지로 갈 때만", () => {
  it("stoploss_pause로 가면 확인창 필요(볼륨 죽이는 회귀)", () => {
    expect(needsLossPolicyConfirm("stoploss_pause")).toBe(true);
  });
  it("leash(고삐 복귀)는 확인창 불필요(기본값 복귀는 무해)", () => {
    expect(needsLossPolicyConfirm("leash")).toBe(false);
  });
});

describe("effectiveLossPolicy — NULL/미설정은 고삐(기본)로 정규화", () => {
  it("null → leash", () => {
    expect(effectiveLossPolicy(null)).toBe("leash");
  });
  it("명시값은 그대로", () => {
    expect(effectiveLossPolicy("leash")).toBe("leash");
    expect(effectiveLossPolicy("stoploss_pause")).toBe("stoploss_pause");
  });
});

describe("lossPolicyConfirmText — 회귀를 정직하게 경고", () => {
  it("캠페인 이름을 담는다", () => {
    expect(lossPolicyConfirmText("04.지문방지필름")).toContain("04.지문방지필름");
  });
  it("하드 정지 회귀와 기본값(고삐)이 전역 정책임을 명시한다", () => {
    const t = lossPolicyConfirmText("캠페인");
    expect(t).toContain("하드 정지");
    expect(t).toContain("기본값은 '고삐'");
    expect(t).toContain("D-NAO-65");
  });
  it("확인 질문으로 끝난다", () => {
    expect(lossPolicyConfirmText("캠페인")).toContain("적용할까요?");
  });
});
