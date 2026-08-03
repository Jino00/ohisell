// modificationActor.test.ts — 「수정 사항」 주체 표시 계약.
// ★존재 이유 두 가지:
//   ① 'MOP'라는 말이 화면에 새는 것을 **테스트로** 막는다. 코드베이스의 optimizer='mop'은
//      "제3자 소유"라는 뜻이고 Jino가 말하는 "MOP=우리 시스템"과 정반대라, 그 세 글자가
//      라벨에 들어가는 순간 화면이 정확히 반대로 읽힌다.
//   ② 값이 없을 때 **빈칸을 만들지 않는다**는 계약. 백필 36건 중 31건은 이전값이 아예 없다.
import { describe, it, expect } from "vitest";
import {
  ACTOR_LABEL, ACTOR_OPTIONS, actorLabel, actorTone, correctionNote, timeSuffix, valueText,
} from "./modificationActor";

describe("주체 라벨 — 3주체가 서로 다른 말·다른 색", () => {
  it("우리 자동화 / 대행사 / Jino", () => {
    expect(actorLabel("ours")).toBe("우리 자동화");
    expect(actorLabel("agency")).toBe("대행사");
    expect(actorLabel("jino")).toBe("Jino");
  });

  it("★어떤 라벨에도 'MOP'가 들어가지 않는다 (용어 함정 회귀 고정)", () => {
    for (const label of Object.values(ACTOR_LABEL)) {
      expect(label.toUpperCase()).not.toContain("MOP");
    }
  });

  it("모르는 주체 코드는 지어내지 않고 그대로 노출한다", () => {
    expect(actorLabel("robot")).toBe("robot");
    expect(actorTone("robot")).toBe("neutral");
  });

  it("3주체의 톤이 전부 다르다 — 같으면 표를 훑을 때 구분이 안 된다", () => {
    const tones = ["ours", "agency", "jino"].map(actorTone);
    expect(new Set(tones).size).toBe(3);
  });

  it("★드롭다운 첫 선택지가 대행사다 — 기본값이 대행사이기 때문", () => {
    expect(ACTOR_OPTIONS[0]).toBe("agency");
    expect([...ACTOR_OPTIONS].sort()).toEqual(["agency", "jino", "ours"]);
  });
});

describe("값 표시 — 모르는 건 모른다고, 빈칸은 만들지 않는다", () => {
  it("값이 있으면 값을 쓴다", () => {
    expect(valueText("2,330원", null)).toBe("2,330원");
  });

  it("★이전값이 없으면 사유를 쓴다 (빈칸·0 금지)", () => {
    expect(valueText(null, "이전값 불명(관측 공백)")).toBe("이전값 불명(관측 공백)");
  });

  it("사유조차 없어도 빈 문자열이 되지 않는다", () => {
    expect(valueText(null, null)).toBe("값 불명");
    expect(valueText(null, null)).not.toBe("");
  });
});

describe("시각 근거 — 발생 시각이 없는 건은 그 사실이 드러나야 한다", () => {
  it("실제 발생 시각이면 꼬리표 없음", () => {
    expect(timeSuffix({ time_basis: "occurred" })).toBeNull();
  });

  it("감지 시각이면 '감지'라고 표시한다", () => {
    expect(timeSuffix({ time_basis: "detected" })).toBe("감지");
  });
});

describe("정정 표시 — 사람이 본 것과 아무도 안 본 것은 다른 상태다", () => {
  it("정정이 없으면 아무 말도 안 한다", () => {
    expect(correctionNote({ corrected: false, actor: "agency", actor_auto: "agency" })).toBeNull();
  });

  it("자동 판정을 뒤집었으면 원래 판정을 함께 보여준다", () => {
    expect(correctionNote({ corrected: true, actor: "jino", actor_auto: "ours" }))
      .toBe("정정됨(자동 판정: 우리 자동화)");
  });

  it("★같은 값으로 확정해도 '확인함'으로 남긴다 — 검토 이력이 사라지면 안 된다", () => {
    expect(correctionNote({ corrected: true, actor: "agency", actor_auto: "agency" }))
      .toBe("사람이 확인함");
  });
});
