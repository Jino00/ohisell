// @vitest-environment jsdom
//
// paoNamingAndReach.test.tsx — 설계서 §7½ 1단계 「도달과 이름」의 표면 계약
// (`docs/references/122_pao_console_uiux_design_20260902.md`).
//
// ★왜 이 파일이 필요한가 — 개명 «전»에 테스트 1,355건이 전부 초록이었다. 즉 라벨을
//   「우리 MOP」로 되돌려도, 새 화면이 「MOP」를 또 써도 아무것도 죽지 않았다. 결함의 모양은
//   기능 부재가 아니라 **집행 지점이 한 층에만 있었던 것**이다: `modificationActor.test.ts`가
//   「어떤 라벨에도 MOP가 없다」를 이미 지키고 있었지만 그건 `ACTOR_LABEL` 한 상수뿐이었고,
//   그 밖의 세 화면(커맨드 센터·스코프·콘솔)은 그 규칙 밖에 있었다.
//   그래서 여기 가드는 **오늘 고친 세 화면을 열거하지 않는다** — 전 소스를 훑는다. 열거하면
//   내일 생기는 네 번째 화면이 또 규칙 밖에 선다(CLAUDE.md D-NAO-227의 일반형).
//
// ⚠️짝: 위 `modificationActor.test.ts`(주체 라벨 축). 이쪽은 «관리주체 라벨 + 도달»이다.
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { LayerNav } from "./LayerNav";

afterEach(cleanup);

describe("도달 — 탭바에서 클릭으로 닿는다", () => {
  // ★이 둘은 화면도 라우트도 **이미 다 있었다**. 광고그룹 On/Off 스위치가 안 쓰인 이유는
  //   기능 부재가 아니라 탭에 링크가 없어서였다(설계서 §2-2·§7-1 실측).
  it.each([
    ["PAO 스코프", "/naver-ad/scope"],
    ["검색어 제외", "/naver-ad/exclusion-list"],
  ])("「%s」 탭이 %s 로 간다", (label, href) => {
    render(<MemoryRouter><LayerNav /></MemoryRouter>);
    expect(screen.getByRole("link", { name: label }).getAttribute("href")).toBe(href);
  });

  it("기존 8개 탭이 하나도 사라지지 않았다 — 붙이는 단계이지 갈아엎는 단계가 아니다", () => {
    render(<MemoryRouter><LayerNav /></MemoryRouter>);
    for (const label of ["성과", "커맨드 센터", "리포트", "진단 보드", "최적화 콘솔",
                         "소재 성과", "수정 사항", "원자료"]) {
      expect(screen.getByRole("link", { name: label })).toBeTruthy();
    }
    expect(screen.getAllByRole("link")).toHaveLength(10);
  });
});

/** 주석(줄·블록·JSX)을 걷어낸 뒤 남는 것 = 사용자에게 닿을 수 있는 문자열. */
function stripComments(src: string): string {
  return src
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, "") // {/* JSX */}
    .replace(/\/\*[\s\S]*?\*\//g, "")           // /* block */ · /** doc */
    .replace(/^[ \t]*\/\/.*$/gm, "");           // // 줄 전체 주석
}

// ★소스를 읽는 수단으로 node:fs가 아니라 Vite의 `import.meta.glob`을 쓴다 — 이 앱의
//   tsconfig엔 node 타입이 없어서 fs를 쓰면 **테스트는 초록인데 `tsc -b`가 빨강**이 된다
//   (실제로 그렇게 한 번 깨졌다). 테스트가 도는 것과 빌드가 되는 것은 다른 질문이다.
const SOURCES = import.meta.glob("../../**/*.{ts,tsx}", {
  query: "?raw", import: "default", eager: true,
}) as Record<string, string>;

describe("이름 — 'MOP'는 화면에 쓰지 않는다 (전 소스 전수)", () => {
  it("★어떤 소스 파일의 주석 밖에도 'MOP'가 없다", () => {
    // 'MOP'는 **경쟁 상용 도구**의 이름이다. 코드의 optimizer='mop'은 「제3자 소유」라는 뜻이라
    // Jino가 말하는 「MOP=우리 시스템」과 정반대다 — 그 세 글자가 라벨에 들어가는 순간
    // 화면이 정확히 반대로 읽힌다. 확정된 우리 이름은 PAO다(D-NAO-162).
    // ★대문자 'MOP' **그대로**만 잡는다. 소문자 `optimizer: "mop"`은 API 계약값이고
    //   `MopKpiCard`는 내부 컴포넌트 이름이라 둘 다 사용자에게 안 보인다 — 그걸 같이 잡으면
    //   가드가 못 쓰게 시끄러워지고, 시끄러운 가드는 결국 꺼진다. 화면에 쓰이는 낱말은
    //   언제나 대문자로 적힌다("우리 MOP" · "원본 MOP" · "MOP").
    const offenders: string[] = [];
    for (const [f, src] of Object.entries(SOURCES)) {
      if (f.includes(".test.")) continue;
      stripComments(src).split("\n").forEach((line, i) => {
        if (/\bMOP\b/.test(line)) offenders.push(`${f}:${i + 1}: ${line.trim()}`);
      });
    }
    // 훑은 파일이 0이면 glob이 조용히 빈 걸 준 것이다 — 그 경우 위 루프는 «위반 0»으로
    // 보이지만 실은 **아무것도 안 본 것**이다(교훈 #123: 발견 0건과 실행 안 됨은 같은 숫자다).
    expect(Object.keys(SOURCES).length).toBeGreaterThan(100);
    expect(offenders).toEqual([]);
  });
});

describe("이름 — 관리주체 3값의 라벨", () => {
  it("ours=PAO · mop=제3자(대행사) · none=수동, 그리고 셋이 서로 다르다", async () => {
    const { OptimizerSwitch } = await import("./OptimizerSwitch");
    render(
      <OptimizerSwitch campaignId="c1" campaignName="테스트 캠페인" value="ours"
                       onChange={async () => {}} />,
    );
    // ★라벨·title 둘 다 본다 — 하나만 고치고 나머지를 두면 hover가 옛 이름을 말한다.
    const pao = screen.getByRole("button", { name: "PAO" });
    expect(pao.getAttribute("aria-pressed")).toBe("true");
    expect(pao.getAttribute("title")).toContain("PAO");
    expect(screen.getByRole("button", { name: "제3자(대행사)" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "수동" })).toBeTruthy();
    expect(screen.getAllByRole("button")).toHaveLength(3);
  });
});
