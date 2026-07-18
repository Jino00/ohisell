// rationaleFormat.test.ts — 근거(rationale) 사람말 변환 검증(Jino 2026-07-18:
// "내가 알아볼 수 있는 내용으로 적어줘"). 입력은 prod change_log에서 실측한 원문이다.
// ★핵심 계약: (1) 못 알아보는 모양은 원문 그대로(지어내지 않음), (2) 원문은 title에 보존.
import { describe, it, expect } from "vitest";
import { formatRationale } from "./NaverAdCommandCenter";

describe("formatRationale — 진단 보드 입찰 제안", () => {
  it("쇼핑그룹 성장(prod 08:50 실측)을 사람 문장으로", () => {
    const raw =
      "[shopping_group_growth] 보정ROAS=1.7708 cost=75914원 clk=51 — 시뮬 근거=economic_ceiling_only, " +
      "추천입찰=1550원, target_roas 근거=account_default(1.6974206025810457756724719). " +
      "예측(오늘): clk=7, cost=11134원, conv_amt=5841원.";
    const { text, title } = formatRationale(raw);
    expect(text).toContain("쇼핑그룹 성장 판단");
    expect(text).toContain("ROAS 1.77");
    expect(text).toContain("(목표 1.70)");
    expect(text).toContain("광고비 75,914원");
    expect(text).toContain("클릭 51회");
    expect(text).toContain("오늘 예상: 클릭 7회·광고비 11,134원·전환매출 5,841원");
    // 기계용 토큰이 화면에 남지 않는다
    expect(text).not.toContain("보정ROAS");
    expect(text).not.toContain("economic_ceiling_only");
    expect(text).not.toContain("account_default");
    // 원문은 감사용으로 title에 보존
    expect(title).toBe(raw);
  });

  it("스텝 클램프(prod 08:55 실측)는 '일부만 반영' 안내를 붙인다", () => {
    const raw =
      "[shopping_group_growth] 보정ROAS=2.2846 cost=58856원 clk=40 — 시뮬 근거=economic_ceiling_only, " +
      "추천입찰=1720원(경제상한 1950원, 스텝 클램프), target_roas 근거=account_default(1.697313). " +
      "예측(오늘): clk=3, cost=4208원, conv_amt=9735원.";
    const { text } = formatRationale(raw);
    expect(text).toContain("쇼핑그룹 성장 판단");
    expect(text).toContain("15%까지만 조정");
    expect(text).not.toContain("스텝 클램프");
  });
});

describe("formatRationale — 시간당 밴드", () => {
  it("과열밴드(prod 19:20 실측)는 '입찰가를 낮췄다'", () => {
    const { text, title } = formatRationale("[시간당밴드] 가중avg_rank=2.20<2.5(과열밴드)");
    expect(text).toBe("시간당 점검 — 평균 노출순위 2.20위로 상위권이 과열(기준 2.5위)이라 입찰가를 낮췄습니다.");
    expect(title).toBe("[시간당밴드] 가중avg_rank=2.20<2.5(과열밴드)");
  });
});

describe("formatRationale — 차단/실패 접미사", () => {
  it("가드레일 차단(prod 20:20)은 '막혀 안 바뀌었다'를 붙이고 내부 태그를 걷어낸다", () => {
    const raw = "[시간당밴드] 가중avg_rank=2.36<2.5(과열밴드) [실행 불가] 변경폭 39.3% 초과(상한 15%, D-NAO-5)";
    const { text } = formatRationale(raw);
    expect(text).toContain("상위권이 과열");
    expect(text).toContain("가드레일에 막혀 실제로는 바뀌지 않았습니다");
    expect(text).toContain("변경폭 39.3% 초과(상한 15%)");
    expect(text).not.toContain("D-NAO-5");
  });

  it("쓰기 실패는 '반영 여부 불확실'로 (단언하지 않음, 원칙22)", () => {
    const raw = "[시간당밴드] 가중avg_rank=2.36<2.5(과열밴드) [실행 실패] update_keyword_bid: useGroupBidAmt 미전환";
    const { text } = formatRationale(raw);
    expect(text).toContain("실제 반영 여부가 불확실합니다");
  });
});

describe("formatRationale — 폴백·경계", () => {
  it("빈 값은 NO_DATA", () => {
    expect(formatRationale(null).text).toBe("—");
    expect(formatRationale("").text).toBe("—");
  });

  it("못 알아보는 모양은 원문 그대로(지어내지 않음)", () => {
    const raw = "[미지의보드] 전혀 다른 형식 zzz=1";
    const { text, title } = formatRationale(raw);
    expect(text).toBe(raw);
    expect(title).toBeUndefined(); // 변형 없음 → 툴팁 불필요
  });

  it("정지 판단", () => {
    const { text } = formatRationale("[pause_candidates] 정지(2026-07-10) 직전 보정ROAS 0.83");
    expect(text).toContain("정지 후보");
    expect(text).toContain("ROAS 0.83");
    expect(text).toContain("정지로 판단");
  });
});
