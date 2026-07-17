// changeLogPeriod.test.ts — 변경 이력 커스텀 기간 검증 가드(D-NAO-54).
// ★존재 이유: `<input type="date">`는 사용자가 값을 지우면 **빈 문자열**을 준다. 최초 구현은
//   `from > to` 하나만 봤는데 `"" > "2026-07-17"`는 false라 그 검사를 그냥 통과했다(실측).
//   그러면 `date_from=`이 그대로 백엔드로 나가고 422 + pydantic 원문이 화면에 뜬다
//   ("불러오지 못했습니다: Input should be a valid date…") — 사용자에게 아무 의미 없는 문자열이다.
//   ISO 날짜의 문자열 비교가 곧 날짜 비교인 건 **양쪽이 채워졌을 때만** 참이다.
import { describe, it, expect } from "vitest";
import { customRangeError, kstDate } from "./NaverAdCommandCenter";

describe("customRangeError — 캘린더 커스텀 구간 검증", () => {
  it("정상 구간은 null (조회 가능)", () => {
    expect(customRangeError({ from: "2026-07-10", to: "2026-07-17" })).toBeNull();
  });

  it("같은 날 하루짜리 구간도 정상 (당일/어제 프리셋과 같은 모양)", () => {
    expect(customRangeError({ from: "2026-07-17", to: "2026-07-17" })).toBeNull();
  });

  it("뒤집힌 구간은 막는다 — 캘린더에서 실제로 고를 수 있다", () => {
    expect(customRangeError({ from: "2026-07-17", to: "2026-07-10" })).toBe("시작일이 종료일보다 늦습니다.");
  });

  it("★시작일을 지우면 막는다 (회귀 고정: 빈 문자열은 from>to를 통과한다)", () => {
    // 이 한 줄이 버그의 정체다 — 방어가 없으면 아래가 false라 요청이 나간다.
    expect("" > "2026-07-17").toBe(false);
    expect(customRangeError({ from: "", to: "2026-07-17" })).toBe("시작일과 종료일을 모두 선택하세요.");
  });

  it("★종료일을 지워도 막는다", () => {
    expect(customRangeError({ from: "2026-07-17", to: "" })).toBe("시작일과 종료일을 모두 선택하세요.");
  });

  it("둘 다 지우면 막는다", () => {
    expect(customRangeError({ from: "", to: "" })).toBe("시작일과 종료일을 모두 선택하세요.");
  });
});

describe("customRangeError — 365일 상한 (백엔드 3개 규칙을 전부 따라해야 한다)", () => {
  it("365일(양끝 포함)은 통과", () => {
    expect(customRangeError({ from: "2025-07-18", to: "2026-07-17" })).toBeNull();
  });

  it("★366일은 막는다 — 안 막으면 백엔드 422 원문이 화면에 뜬다", () => {
    expect(customRangeError({ from: "2025-07-17", to: "2026-07-17" })).toBe("조회 구간은 최대 365일입니다.");
  });

  it("★타이핑 중간값(연도 0002)도 여기서 막힌다 — 디바운스 없이 422 노출을 없애는 방식", () => {
    // Chrome 날짜 입력은 연도 칸에 2026을 치는 동안 0002→0020→0202를 onChange로 흘린다.
    // 이 중간값들은 백엔드 파라미터 검증(date 파싱)을 **통과**한 뒤 span 초과로 422가 된다.
    expect(customRangeError({ from: "0002-07-10", to: "2026-07-17" })).toBe("조회 구간은 최대 365일입니다.");
  });
});

describe("kstDate — 이 파일에서 유일하게 타임존이 걸린 함수", () => {
  // ★고정 시각을 주입해 검증한다. 이 저장소는 타임존으로 두 번 사고 냈고(쿨다운 무력화,
  //   date() 오독) 그 공통점이 "타임존 코드에 테스트가 없었다"는 것이다.
  const KST_0005 = Date.parse("2026-07-17T15:05:00Z"); // = 07-18 00:05 KST

  it("★UTC 날짜와 KST 날짜가 갈리는 순간에 KST를 준다 (UTC로는 07-17, KST로는 07-18)", () => {
    expect(new Date(KST_0005).toISOString().slice(0, 10)).toBe("2026-07-17"); // 브라우저 UTC 계산은 이렇게 틀린다
    expect(kstDate(0, KST_0005)).toBe("2026-07-18");
  });

  it("어제 = -1 (자정 직후에도 정확)", () => {
    expect(kstDate(-1, KST_0005)).toBe("2026-07-17");
  });

  it("7일 프리셋 -7..-1 이 정확히 7일이고 당일을 포함하지 않는다", () => {
    expect(kstDate(-7, KST_0005)).toBe("2026-07-11");
    expect(kstDate(-1, KST_0005)).toBe("2026-07-17");
    const span = (Date.parse("2026-07-17") - Date.parse("2026-07-11")) / 86_400_000 + 1;
    expect(span).toBe(7);
    expect(kstDate(-1, KST_0005)).not.toBe(kstDate(0, KST_0005)); // 당일 미포함
  });

  it("30일 프리셋도 어제 기준 30일", () => {
    const span = (Date.parse(kstDate(-1, KST_0005)) - Date.parse(kstDate(-30, KST_0005))) / 86_400_000 + 1;
    expect(span).toBe(30);
  });

  it("en-CA가 YYYY-MM-DD 형식을 준다 (백엔드 date 파서가 받는 형식)", () => {
    expect(kstDate(0, KST_0005)).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});
