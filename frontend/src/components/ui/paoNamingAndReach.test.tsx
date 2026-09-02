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
import { OPTIMIZER_LABEL, optimizerBadgeLabel } from "../../lib/optimizerLabels";

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

/**
 * 주석만 걷어내고 나머지는 **한 글자도 잃지 않는다**. 줄 수는 보존한다(줄번호 신고용).
 *
 * ★정규식 두 줄로는 안 된다 — 적대 리뷰 P1(PR #665)이 실증했다. `.replace(/\/\*[\s\S]*?\*\//g,"")`
 *   는 **문자열 리터럴 안의 `/*`** 를 주석 시작으로 오인하고, 그 뒤 처음 나오는 진짜 «블록 주석
 *   닫는 토큰»까지 통째로 지운다. 이 저장소엔 방아쇠가 실재한다 — 라우트 와일드카드 `"/naver-ad/*"`,
 *   `import.meta.glob("./pages/*.tsx")`. 그 한 줄이 위반 라벨 **앞에** 있으면 파일 뒷부분이
 *   스캔에서 사라지는데, 가드는 «파일 개수»만 봤으므로 **내용이 지워진 건 안 보였다.**
 *   즉 가드가 스스로 좁아지면서 초록을 유지했다 — 이 PR이 인용한 D-NAO-227의 일반형이
 *   가드 자신에게 재현된 것이다. 그래서 문자열·템플릿·정규식 리터럴을 **먼저 소비하는**
 *   상태 기계로 바꾼다. 덤으로 꼬리주석(`const a = 1; // MOP 관례`)도 정확히 걸러진다 —
 *   옛 정규식은 «줄 전체 주석»만 알아서 정당한 꼬리주석에 오탐했고(P2-3), 시끄러운 가드는
 *   결국 꺼진다.
 */
export function stripComments(src: string): string {
  const out: string[] = [];
  const keep = (c: string) => out.push(c);
  const blank = (c: string) => out.push(c === "\n" ? "\n" : " "); // 주석 자리는 공백으로 — 줄 보존
  // 정규식 리터럴은 «값이 올 자리»에서만 시작한다. 나눗셈과 가르는 최소 판별자.
  const REGEX_OK_BEFORE = new Set("(,=:[!&|?{};+-*%~^<>".split(""));
  let lastSig = "";
  let i = 0;
  while (i < src.length) {
    const c = src[i], n = src[i + 1];
    if (c === "/" && n === "/") {                       // 줄 주석 (꼬리주석 포함)
      while (i < src.length && src[i] !== "\n") blank(src[i++]);
      continue;
    }
    if (c === "/" && n === "*") {                       // 블록 주석
      blank(src[i++]); blank(src[i++]);
      while (i < src.length && !(src[i] === "*" && src[i + 1] === "/")) blank(src[i++]);
      if (i < src.length) { blank(src[i++]); blank(src[i++]); }
      continue;
    }
    if (c === '"' || c === "'") {                       // 문자열 리터럴 — 통째로 «보존»
      keep(src[i++]);
      while (i < src.length && src[i] !== c) {
        if (src[i] === "\\") keep(src[i++]);
        if (i < src.length) keep(src[i++]);
      }
      if (i < src.length) keep(src[i++]);
      lastSig = '"';
      continue;
    }
    if (c === "`") {                                    // 템플릿 리터럴(중첩 ${} 포함)
      keep(src[i++]);
      while (i < src.length && src[i] !== "`") {
        if (src[i] === "\\") { keep(src[i++]); if (i < src.length) keep(src[i++]); continue; }
        if (src[i] === "$" && src[i + 1] === "{") {     // ${ … } 안은 다시 코드
          keep(src[i++]); keep(src[i++]);
          let depth = 1;
          while (i < src.length && depth > 0) {
            if (src[i] === "{") depth++;
            else if (src[i] === "}") depth--;
            if (depth > 0) keep(src[i++]);
          }
          if (i < src.length) keep(src[i++]);
          continue;
        }
        keep(src[i++]);
      }
      if (i < src.length) keep(src[i++]);
      lastSig = "`";
      continue;
    }
    if (c === "/" && (lastSig === "" || REGEX_OK_BEFORE.has(lastSig))) { // 정규식 리터럴
      keep(src[i++]);
      let inClass = false;
      while (i < src.length && (inClass || src[i] !== "/")) {
        if (src[i] === "\\") keep(src[i++]);
        else if (src[i] === "[") inClass = true;
        else if (src[i] === "]") inClass = false;
        else if (src[i] === "\n") break;               // 미종결 — 정규식이 아니었다
        if (i < src.length) keep(src[i++]);
      }
      if (i < src.length && src[i] === "/") keep(src[i++]);
      lastSig = "/";
      continue;
    }
    if (!/\s/.test(c)) lastSig = c;
    keep(src[i++]);
  }
  return out.join("");
}

const SOURCES = import.meta.glob("../../**/*.{ts,tsx}", {
  query: "?raw", import: "default", eager: true,
}) as Record<string, string>;

// ★범위를 이름에 정직하게 적는다 — 이 검사가 훑는 것은 **프론트 소스뿐**이다(`frontend/src`).
//   적대 리뷰 P2-1이 실증했듯 «화면»에는 백엔드가 내려주는 문자열도 뜬다:
//   `backend/app/services/naver_ad/entity_sync.py`가 `rationale`에 「외부(MOP/사람)」을 쓰고
//   커맨드 센터 표 셀이 그걸 그대로 렌더한다. 그래서 이 초록은 **「화면에 MOP가 없다」가
//   아니라 「프론트 소스에 MOP가 없다」**만 뜻한다 — 그 백엔드 몫은 §7½ 합격면(라벨·확인창·
//   title) 밖이라 이번 범위가 아니고, 소관은 PAO 최적화 트랙으로 넘겼다.
//   ⚠️이름이 범위보다 넓으면 초록이 «안 잰 것»까지 보증하는 것처럼 읽힌다.
describe("이름 — 'MOP'는 프론트 소스 어디에도 쓰지 않는다 (frontend/src 전수)", () => {
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
      if (/\.test\.tsx?$/.test(f)) continue; // ★`includes(".test.")`는 `foo.test.helper.tsx` 같은
                                             //   **출하 파일**까지 조용히 뺀다(리뷰 P2-5).
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

describe("가드 자신을 의심한다 — 「위반 0건」이 「아무것도 안 봤다」일 수 있다", () => {
  // ★교훈 #123: 발견 0건과 실행 안 됨은 화면에 같은 숫자로 보인다. 위 전수 검사는 스캐너가
  //   내용을 통째로 먹어도 «위반 0건»으로 초록이 된다 — 실제로 옛 정규식 판이 그렇게 뚫렸다
  //   (적대 리뷰 P1, PR #665). 그래서 스캐너가 «얼마나 남겼는가»를 따로 잰다.
  it("주석을 걷어내도 소스 대부분이 남는다 — 통째로 먹었으면 실패한다", () => {
    for (const [f, src] of Object.entries(SOURCES)) {
      if (/\.test\.tsx?$/.test(f)) continue;
      const kept = stripComments(src).replace(/\s/g, "").length;
      const total = src.replace(/\s/g, "").length;
      if (total < 200) continue; // 아주 작은 파일은 주석 비율이 튄다
      expect(kept / total, `${f}: 주석 제거 후 ${kept}/${total}자만 남았다`).toBeGreaterThan(0.3);
    }
  });

  it("★문자열 안의 `/*`가 뒷부분을 삼키지 않는다 (P1 회귀 고정)", () => {
    // 이 저장소에 실재하는 방아쇠: 라우트 와일드카드·`import.meta.glob("./pages/*.tsx")`.
    const src = [
      'const ROUTE = "/naver-ad/*";',
      'const LABEL = "우리 MOP";',
      '/** 파일에 원래 있던 JSDoc — 옛 정규식은 여기까지 삼켰다 */',
    ].join("\n");
    expect(stripComments(src)).toContain("우리 MOP");
  });

  it("꼬리주석은 정확히 걸러진다 — 정당한 주석에 오탐하면 가드가 꺼진다 (P2-3)", () => {
    expect(stripComments('const ORDER = 1; // MOP 관례: 증가=▲빨강')).not.toContain("MOP");
    expect(stripComments('const ORDER = 1; // MOP 관례')).toContain("const ORDER = 1;");
  });

  it("템플릿 리터럴 안에서 `//`로 시작하는 «화면 문자열»은 지워지지 않는다 (P1 두 번째 경로)", () => {
    expect(stripComments('const t = `\n// 우리 MOP\n`;')).toContain("우리 MOP");
  });
});

describe("이름 — 양성 조건: 같은 값을 두 화면이 다르게 부르지 않는다", () => {
  // ★적대 리뷰 P2-2가 실증한 구멍: 개명 전엔 「MOP가 없다」는 **음성** 조건만 지켰다. 그래서
  //   콘솔 필터·스코프 배지·커맨드센터 배지를 'MOP'를 «안 넣고» 옛 이름(「우리」·「가동」)으로
  //   되돌려도 1,360건이 전부 초록이었다. 이름을 `lib/optimizerLabels.ts` 한 곳에 모았으므로
  //   이제 갈라질 자리가 구조적으로 없고, 여기서는 그 한 곳을 못 박는다.
  it("ours=PAO · mop=제3자(대행사) · none=수동", () => {
    expect(OPTIMIZER_LABEL.ours).toBe("PAO");
    expect(OPTIMIZER_LABEL.mop).toBe("제3자(대행사)");
    expect(OPTIMIZER_LABEL.none).toBe("수동");
  });

  it("스코프 배지는 «맡김»과 «손댐»을 가른다 — 'ours'인데 정지면 그렇게 말한다", () => {
    expect(optimizerBadgeLabel("ours", true)).toBe("PAO 가동");
    expect(optimizerBadgeLabel("ours", false)).toBe("PAO 정지");
    expect(optimizerBadgeLabel("mop", true)).toBe("제3자(대행사)");
    expect(optimizerBadgeLabel("none", false)).toBe("수동");
  });

  it("세 라벨이 서로 다르다 — 같으면 표를 훑을 때 구분이 안 된다", () => {
    expect(new Set(Object.values(OPTIMIZER_LABEL)).size).toBe(3);
  });

  it("★라벨을 쓰는 화면들이 상수를 «실제로» 쓴다 — 하드코딩으로 갈라지면 실패한다", () => {
    // ★적대 리뷰 P2-2가 살려 보낸 M7b·M9가 이 검사의 초판을 뚫었다: 초판은 「상수를
    //   import 하는가」만 봤고, **import는 남긴 채 사용처만 하드코딩**하면 그만이었다.
    //   존재 게이트는 성숙 게이트가 아니다. 그래서 ①import 말고 «사용»이 있는가
    //   ②옛 라벨 리터럴이 되살아나지 않았는가 둘을 본다.
    //
    // ⚠️이건 소스 검사이지 렌더 검사가 아니다 — 진짜 증명은 `App`을 통째로 렌더하는
    //   `pages/paoScopeReachesTheUser.test.tsx` 쪽이다. 스코프 화면은 거기서 잡히고,
    //   커맨드 센터·콘솔은 §7½ 4·5단계에서 화면 자체가 다시 지어지므로 그때 같은 렌더
    //   테스트를 세운다(그때까지는 이 소스 검사가 임시 집행 지점이다).
    const FORBIDDEN = ['"우리"', ">우리<", '"우리·정지"', '"원본 MOP"', '"우리 MOP"'];
    for (const f of ["../../pages/NaverAdScope.tsx",
                     "../../pages/NaverAdOptimizationConsole.tsx",
                     "../../pages/NaverAdCommandCenter.tsx",
                     "./CoverageBar.tsx", "./OptimizerSwitch.tsx"]) {
      const src = SOURCES[f];
      expect(src, `${f} 를 못 찾았다 — 파일이 옮겨졌으면 이 목록을 고쳐라`).toBeTruthy();
      const uses = (src.match(/OPTIMIZER_LABEL|OPTIMIZER_TITLE|optimizerBadgeLabel/g) ?? []).length;
      expect(uses, `${f}: 상수를 import만 하고 «쓰지» 않는다`).toBeGreaterThan(1);
      for (const bad of FORBIDDEN) {
        expect(stripComments(src).includes(bad), `${f}: 옛 라벨 ${bad} 이(가) 되살아났다`).toBe(false);
      }
    }
  });
});
