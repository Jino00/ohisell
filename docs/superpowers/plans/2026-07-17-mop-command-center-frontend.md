# MOP 커맨드 센터 — 프론트엔드 구현 계획 (D-NAO-47 Phase 2/2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 백엔드 45개 모듈이 만들어낸 데이터를 계층으로 여는 커맨드 센터를 만든다. **1층에 "우리 MOP가 돌리는 광고의 성과"와 "왜 0인가"를 놓는다** — 0을 찍고 침묵하는 게 MOP의 최대 실패이고, 지금 우리도 비어 있다.

**Architecture:** `components/ui/` 원시 컴포넌트 9개 + `lib/format.ts` → 1~3층 화면 재편. **N=1(오늘 04 카나리)과 N=여럿(나중)이 같은 컴포넌트**(D-47-c: 카나리 전용 화면 금지). 신규 런타임 의존성 0.

**Tech Stack:** React 19.2.4 · Vite 8.0.1 · Tailwind v4.2.2(CSS-first, config 파일 없음) · recharts 3.8.1 · react-router-dom 7.13.2 · TypeScript

**선행 필독:**
- `docs/superpowers/specs/2026-07-17-mop-command-center-design.md` — **§8(디자인 시스템)·§9(라이브 확인)를 먼저 읽을 것.** §4가 정보 구조.
- **Phase 1(백엔드) 완료·codex PASS.** 이 계획은 그 API를 소비한다.

---

## ⚠️ 착수 전 필독 — 이 계획의 함정 3개

### 1. ★`pct()` 통합은 경계를 넘는 순간 100배 오류가 된다

인벤토리 실측: `pct()`가 **호환되지 않는 입력 계약 2종**으로 존재한다.

| 계열 | 파일 | 입력 | 동작 |
|---|---|---|---|
| **분수** | `NaverAdReport:48` `NaverAdDiagnosisBoard:30` | 0~1 | `(n*100).toFixed()` |
| **이미 스케일됨** | `NaverOps:60` `CoupangOps:40` `AdReport:19` | 0~100 | `n.toFixed()` — ×100 **안 함** |

**두 계열을 하나로 합치면 `5%`가 `0.05%`로, 또는 `500%`로 조용히 렌더된다. 타입 에러도 안 난다.**

→ **`lib/format.ts`를 import하는 파일은 naver-ad 4파일뿐이다.** `NaverOps`/`CoupangOps`/`AdReport`/`Dashboard`의 로컬 정의는 **건드리지 않는다** — 그러면 함정이 발생할 수 없다. **"겸사겸사 다른 화면도 정리"를 절대 하지 말 것.**

### 2. ★리스킨 경계 밖 파일은 diff 0줄

| 리스킨 O | `NaverAdReport` `NaverAdDiagnosisBoard` `NaverAdOptimizationConsole` + 신규 1층 |
|---|---|
| **손대지 않음** | `Dashboard` `Orders` `Settlements` `CommandCenter` `NaverOps` `CoupangOps` `Products` `ProductConnectionMap` `Settings` `AdReport` |

쿠팡 회계 화면(`CommandCenter` 1,227 LOC 등)은 이 트랙과 무관(D-47-f). **돈 계산하는 화면을 UI 스프린트로 건드리는 건 무상관 리스크.** 완료 기준에 `git diff --stat` 검증이 있다.

### 3. ★API 응답 키는 `rows`다 (`items` 아님)

Phase 1의 신규 엔드포인트는 기존 라우터 관례에 맞춰 `{"rows": [...], "total": N}`을 반환한다. `items`로 쓰면 `undefined`가 되고 조용히 빈 화면이 된다.

### 4. ★★타입 체크는 `npx tsc -b`다 — `npx tsc --noEmit`은 **아무것도 검사하지 않는다**

`frontend/tsconfig.json`은 `{"files": [], "references": [...]}` 형태(solution-style)라 **bare `tsc --noEmit`은 파일 0개를 검사하고 조용히 성공한다.** 코드가 아무리 깨져 있어도 통과한다 — 검증이 아니라 **위약(僞藥)**이다.

실측(2026-07-17): `src/`에 `const x: number = "string"`을 넣고 `npx tsc --noEmit` → **출력 없음(통과)**. 같은 파일에 `npx tsc -b` → `error TS2322` 정상 검출.

→ **모든 타입 검증은 `npx tsc -b`로 한다.** (`npm run build`도 내부적으로 `tsc -b`를 쓴다.)

---

## File Structure

| 파일 | 책임 | 신규/수정 |
|---|---|---|
| `frontend/src/index.css` | `@theme` 토큰 **추가만**(현재 23바이트) | 수정 |
| `frontend/src/lib/format.ts` | naver-ad 전용 포매터 단일 출처 | **신규** |
| `frontend/src/lib/format.test.ts` | ★프론트 첫 테스트 — `pctFromFraction` 계약 고정 | **신규** |
| `frontend/src/components/ui/index.ts` | 원시 컴포넌트 배럴 | **신규** |
| `frontend/src/components/ui/{Card,Stat,EmptyState,Table,Button,Badge,Delta,Loading,CoverageBar}.tsx` | 원시 9종 | **신규** |
| `frontend/src/pages/NaverAdCommandCenter.tsx` | **1층** — 3열 대조 + 캠페인 리스트 | **신규** |
| `frontend/src/pages/NaverAdRawExplorer.tsx` | **3층 ⑨** — 원자료 탐색 | **신규** |
| `frontend/src/pages/NaverAdReport.tsx` | 탭 컨테이너 → 3층 리포트 전용으로 축소 | 수정 |
| `frontend/src/pages/NaverAdOptimizationConsole.tsx` | 라벨 14종·target_bid 표시·format.ts 사용 | 수정 |
| `frontend/src/pages/NaverAdDiagnosisBoard.tsx` | format.ts 사용 · `<Loading>` · `<Table>` 페이지네이션 | 수정 |
| `frontend/src/lib/api.ts` | 신규 API 4개 타입·함수 추가 | 수정 |
| `frontend/src/App.tsx` | 라우트 5개 | 수정 |

**테스트 실행:** `cd frontend && npx vitest run` (vitest 미설치면 Task 2 Step 1에서 설치). 타입: `cd frontend && npx tsc -b`. 빌드: `cd frontend && npm run build`.

---

## Task 1: 디자인 토큰 (`index.css`)

**Files:** Modify `frontend/src/index.css` (현재 1줄 `@import "tailwindcss";`)

- [ ] **Step 1: 토큰 추가**

`frontend/src/index.css` 전체를 아래로 교체:

```css
@import "tailwindcss";

/* D-NAO-47 디자인 토큰 — naver-ad 커맨드 센터용.
   ★색을 두 축으로 나눈다. 인벤토리 실측(2026-07-17) 결과 서로 모순되는 색 규약 4개가
   동시에 살아있었다(profitColor 양수=파랑 / Dashboard 양수=초록 / mopDelta 증가=빨강).
   원인은 "누가 틀렸나"가 아니라 **방향과 판단을 한 색으로 칠하려 한 것**이다.
     · 방향(direction): 숫자가 올랐나 내렸나. 가치판단 없음. 한국 증시·MOP 관례.
     · 판단(judgment): 그게 좋은가 나쁜가. BEP 위인가 아래인가.
   "노출 +28%"는 방향이고, 그게 좋은지는 별개다(비용이 같이 올랐으면 나쁨).
   한 색으로 둘 다 하려니 mopDelta의 invert 플래그 같은 땜질이 나왔다 → invert 제거.

   ⚠️ 이 토큰은 naver-ad 화면 전용이다. 기존 쿠팡/대시보드 화면은 참조하지 않으므로
   추가만으로는 영향 0이다(PLAN_naver-ad-dashboard-mini.md:40 "전면 리스킨 안 함" 유지). */
@theme {
  /* 축 1: 방향 — 가치판단 없음 */
  --color-dir-up: var(--color-red-600);
  --color-dir-down: var(--color-blue-600);
  --color-dir-flat: var(--color-gray-400);

  /* 축 2: 판단 — 좋은가 나쁜가 */
  --color-judge-good: var(--color-emerald-600);
  --color-judge-bad: var(--color-red-600);
  --color-judge-warn: var(--color-amber-500);
  /* ★judge-idle이 D-47-h의 색이다. "우리 조작 0회"는 나쁜 게(빨강) 아니라
     **아직 일어나지 않은 것**이다. 빨강이면 고장난 것처럼 보이고 초록이면 거짓말이다. */
  --color-judge-idle: var(--color-gray-400);

  /* 관리주체 3열 대조(1층 ①) */
  --color-owner-ours: var(--color-blue-600);
  --color-owner-mop: var(--color-gray-500);
  --color-owner-manual: var(--color-gray-300);
}
```

- [ ] **Step 2: 빌드 확인**

Run: `cd frontend && npm run build`
Expected: 성공. Tailwind v4가 `@theme`를 파싱해 `text-dir-up` 등의 유틸을 생성한다.

- [ ] **Step 3: 토큰이 실제 유틸을 만드는지 확인**

`frontend/src/index.css`를 참조하는 임시 확인 — `frontend/src/pages/NaverAdReport.tsx`에 잠시 `<span className="text-dir-up">x</span>`를 넣고 `npm run build` 후 산출 CSS에 `--color-dir-up`이 있는지 grep:

Run: `cd frontend && npm run build && grep -r "color-dir-up" dist/assets/*.css | head -1`
Expected: 매치 1건. 확인 후 임시 `<span>` 제거.

- [ ] **Step 4: 커밋**

```bash
cd frontend && git add src/index.css
git commit -m "feat(naver-ad): D-NAO-47 P2-T1 디자인 토큰 — 방향/판단 두 축 분리"
```

---

## Task 2: `lib/format.ts` + 프론트 첫 테스트

**Files:** Create `frontend/src/lib/format.ts`, `frontend/src/lib/format.test.ts`

- [ ] **Step 1: vitest 확인·설치**

Run: `cd frontend && npx vitest --version 2>/dev/null || echo MISSING`

`MISSING`이면:
```bash
cd frontend && npm install -D vitest
```
그리고 `frontend/vite.config.ts`에 test 설정 추가(기존 defineConfig에 `test` 키만 추가, 나머지 불변):
```ts
  test: { environment: "node", include: ["src/**/*.test.ts"] },
```
`package.json` scripts에 `"test": "vitest run"` 추가.

- [ ] **Step 2: 실패하는 테스트 작성**

`frontend/src/lib/format.test.ts` 신규:

```ts
// format.test.ts — D-NAO-47 P2-T2. 프론트 첫 테스트 파일.
// ★존재 이유: pct()가 서로 호환 안 되는 입력 계약 2종(분수 0~1 / 스케일 0~100)으로
//   중복 정의돼 있었다. 순진하게 합치면 5%가 0.05%로 조용히 렌더된다(타입 에러도 안 남).
//   pctFromFraction은 이름으로 계약을 선언하고, 이 테스트가 그 계약을 고정한다.
import { describe, it, expect } from "vitest";
import { isoKST, num, won, pctFromFraction, roasX, NO_DATA } from "./format";

describe("pctFromFraction — 입력은 분수(0~1)다", () => {
  it("0.05를 5.00%로 (×100 한다)", () => {
    expect(pctFromFraction(0.05)).toBe("5.00%");
  });
  it("자릿수 지정", () => {
    expect(pctFromFraction(0.0512, 1)).toBe("5.1%");
  });
  it("1.0은 100%", () => {
    expect(pctFromFraction(1)).toBe("100.00%");
  });
  it("null/undefined는 NO_DATA", () => {
    expect(pctFromFraction(null)).toBe(NO_DATA);
    expect(pctFromFraction(undefined)).toBe(NO_DATA);
  });
  it("0은 NO_DATA가 아니라 0.00% — 0과 '없음'은 다르다(D-47-h)", () => {
    expect(pctFromFraction(0)).toBe("0.00%");
  });
});

describe("num", () => {
  it("천단위 구분", () => expect(num(91005)).toBe("91,005"));
  it("null은 NO_DATA", () => expect(num(null)).toBe(NO_DATA));
  it("0은 '0' — 0과 '없음'은 다르다", () => expect(num(0)).toBe("0"));
});

describe("won", () => {
  it("원 붙임", () => expect(won(204135)).toBe("204,135원"));
  it("null은 NO_DATA", () => expect(won(null)).toBe(NO_DATA));
});

describe("roasX", () => {
  it("배 붙임", () => expect(roasX(2.62)).toBe("2.62배"));
  it("null은 NO_DATA", () => expect(roasX(null)).toBe(NO_DATA));
});

describe("isoKST", () => {
  it("KST 날짜 문자열", () => {
    // 2026-07-17 00:30 KST = 2026-07-16 15:30 UTC
    expect(isoKST(new Date("2026-07-16T15:30:00Z"))).toBe("2026-07-17");
  });
});

describe("NO_DATA", () => {
  it("em-dash — 하이픈이 아니다(§8-2 의도적 통일)", () => {
    expect(NO_DATA).toBe("—");
  });
});
```

- [ ] **Step 3: 실패 확인**

Run: `cd frontend && npx vitest run`
Expected: FAIL — `Cannot find module './format'`

- [ ] **Step 4: 구현**

`frontend/src/lib/format.ts` 신규:

```ts
// format.ts — naver-ad 커맨드 센터 전용 포매터 단일 출처 (D-NAO-47).
//
// ⚠️⚠️ **이 모듈은 naver-ad 4파일에서만 import한다.** NaverOps/CoupangOps/AdReport/
// Dashboard는 자기 로컬 정의를 그대로 쓴다 — "겸사겸사 정리"하지 말 것.
//
// 왜: 인벤토리 실측(2026-07-17) 결과 pct()가 **호환되지 않는 입력 계약 2종**으로 존재한다.
//   · 분수 계열(NaverAdReport:48, DiagnosisBoard:30): 입력 0~1, (n*100) 함
//   · 스케일 계열(NaverOps:60, CoupangOps:40, AdReport:19): 입력 0~100, ×100 안 함
// 두 계열을 하나로 합치는 순간 5%가 0.05%로(또는 500%로) **조용히** 렌더된다. 타입 에러도
// 안 난다. 그래서 통합 범위를 리스킨 경계와 정확히 일치시켜 함정 자체를 없앤다.
// 이 모듈은 **분수 계열 계약만** 채택한다(naver-ad 4파일이 이미 쓰는 계약 = 이동 시 동작 불변).
//
// 이름이 계약을 선언한다: `pct`라는 모호한 이름을 쓰지 않는다. 스케일 계열이 나중에 필요하면
// `pctFromScaled`를 **별도로** 추가하지, 절대 `pct` 하나로 합치지 않는다.

/** 데이터 없음. ★기존 naver-ad는 "-"(하이픈), 쿠팡 계열은 "—". 스코프 안에서 em-dash로
 *  통일한다(타이포그래피상 옳음). 눈에 보이는 의도적 변경 — 리팩터가 아니라 UI 변경이다. */
export const NO_DATA = "—";

/** Date → 'YYYY-MM-DD' (KST 기준). 기존 6곳에 byte-identical로 중복돼 있던 것 — 무손실 이동. */
export function isoKST(d: Date): string {
  const kst = new Date(d.toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
  return `${kst.getFullYear()}-${String(kst.getMonth() + 1).padStart(2, "0")}-${String(kst.getDate()).padStart(2, "0")}`;
}

/** 정수 천단위 구분. 기존 fmt() — 이름만 명확히 했다(Dashboard:214의 동명이의 fmt는
 *  차트 값 포매터라 의미가 다르다. 이름을 갈라 혼동을 없앤다). */
export function num(n: number | null | undefined): string {
  if (n == null) return NO_DATA;
  return n.toLocaleString("ko-KR");
}

export function won(n: number | null | undefined): string {
  if (n == null) return NO_DATA;
  return `${num(n)}원`;
}

/** ★입력은 **분수(0~1)**다. 0.05 → "5.00%". 이름이 계약이다 —
 *  이미 0~100으로 스케일된 값을 넣으면 500%가 나온다. */
export function pctFromFraction(n: number | null | undefined, digits = 2): string {
  if (n == null) return NO_DATA;
  return `${(n * 100).toFixed(digits)}%`;
}

export function roasX(n: number | null | undefined): string {
  if (n == null) return NO_DATA;
  return `${n.toFixed(2)}배`;
}
```

- [ ] **Step 5: 통과 확인**

Run: `cd frontend && npx vitest run`
Expected: PASS (전부)

- [ ] **Step 6: 커밋**

```bash
cd frontend && git add src/lib/format.ts src/lib/format.test.ts vite.config.ts package.json package-lock.json
git commit -m "feat(naver-ad): D-NAO-47 P2-T2 lib/format.ts + 프론트 첫 테스트 (pct 100배 함정 차단)"
```

---

## Task 3: 공통 컴포넌트 `components/ui/`

**Files:** Create `frontend/src/components/ui/*.tsx` + `index.ts`

**설계 원칙 — 이 태스크의 핵심 한 줄:** MOP의 최대 실패는 **빈 상태 미설계**(§2-3: KPI 8칸 전부 0인데 이유 미설명)이고 우리 1층은 대부분 0으로 채워진다. **문서에 "이유를 쓰자"고 적으면 안 지켜진다. 타입이 강제해야 한다.**

- [ ] **Step 1: `EmptyState` + `Stat` (reason 강제가 핵심)**

`frontend/src/components/ui/EmptyState.tsx`:

```tsx
// EmptyState.tsx — D-NAO-47. reason이 **필수**다(optional 아님).
// ★MOP는 KPI 8칸을 전부 0으로 찍고 이유를 설명하지 않았다(스펙 §2-3). 우리 1층은 대부분
//   0으로 채워진다(우리 조작 0회·승인 0건). 0을 찍고 침묵하면 MOP의 실패를 복제하는 것이다.
//   reason을 required로 두면 "데이터 없음" 단독 렌더가 **컴파일되지 않는다**(D-47-h).
export function EmptyState({ reason, hint }: { reason: string; hint?: string }) {
  return (
    <div className="p-8 text-center">
      <p className="text-sm text-gray-600">{reason}</p>
      {hint && <p className="mt-1 text-xs text-gray-400">{hint}</p>}
    </div>
  );
}
```

`frontend/src/components/ui/Stat.tsx`:

```tsx
// Stat.tsx — D-NAO-47. ★값이 0/null이면 reason이 **타입으로** 강제된다.
// 이유 없는 0은 컴파일이 안 된다 — D-47-h를 문서가 아니라 API로 못박은 자리.
import type { ReactNode } from "react";

type StatProps = {
  label: string;
  /** 표시할 값. 이미 포맷된 문자열(lib/format.ts 사용). */
  value: ReactNode;
  /** ★값이 "비어있음"(0건·미발생)일 때 왜 그런지. isEmpty면 필수. */
  reason?: string;
  /** 값이 0/없음 상태인가. true면 reason 필수(아래 유니온이 강제). */
  isEmpty?: boolean;
  tone?: "good" | "bad" | "warn" | "idle" | "neutral";
  sub?: string;
};

type StatPropsStrict =
  | (StatProps & { isEmpty: true; reason: string })
  | (StatProps & { isEmpty?: false });

const TONE: Record<string, string> = {
  good: "text-judge-good",
  bad: "text-judge-bad",
  warn: "text-judge-warn",
  // ★0회는 나쁜 게(빨강) 아니라 아직 안 일어난 것 — 회색이 정답이다.
  idle: "text-judge-idle",
  neutral: "text-gray-900",
};

export function Stat({ label, value, reason, isEmpty, tone = "neutral", sub }: StatPropsStrict) {
  return (
    <div className="min-w-0">
      <div className="text-xs text-gray-500">{label}</div>
      <div className={`mt-0.5 text-lg font-semibold tabular-nums ${TONE[tone]}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-gray-400">{sub}</div>}
      {/* ★"왜 0인가" — MOP가 하지 않은 유일한 것 */}
      {isEmpty && reason && <div className="mt-1 text-xs text-gray-500">{reason}</div>}
    </div>
  );
}
```

- [ ] **Step 2: 나머지 원시 7종**

`frontend/src/components/ui/Card.tsx`:
```tsx
// Card.tsx — D-NAO-47. 카드 1종으로 통일. 기존엔 5가지로 쓰이고 있었다
// (rounded-lg/rounded-xl, border/border-gray-200, 패딩 제각각 — 인벤토리 실측).
import type { ReactNode } from "react";

export function Card({ title, right, children, className = "" }: {
  title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`bg-white rounded-lg border border-gray-200 ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
          <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
          {right}
        </header>
      )}
      {children}
    </section>
  );
}
```

`frontend/src/components/ui/Button.tsx`:
```tsx
import type { ButtonHTMLAttributes } from "react";

const VARIANT = {
  primary: "bg-blue-600 text-white hover:bg-blue-700",
  secondary: "bg-gray-100 text-gray-800 hover:bg-gray-200",
  ghost: "text-gray-600 hover:bg-gray-100",
} as const;

export function Button({ variant = "secondary", className = "", ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: keyof typeof VARIANT }) {
  return (
    <button
      className={`px-3 py-1.5 text-xs rounded border border-gray-200 disabled:opacity-50 ${VARIANT[variant]} ${className}`}
      {...rest}
    />
  );
}
```

`frontend/src/components/ui/Badge.tsx`:
```tsx
// Badge.tsx — D-NAO-47. tone이 두 축을 타입으로 가른다(§8-1).
import type { ReactNode } from "react";

const TONE = {
  dir: "bg-gray-100 text-gray-700",
  judge: "bg-gray-100 text-gray-700",
  owner: "bg-blue-50 text-blue-700",
  neutral: "bg-gray-100 text-gray-600",
} as const;

export function Badge({ tone = "neutral", children }: { tone?: keyof typeof TONE; children: ReactNode }) {
  return <span className={`px-2 py-0.5 text-xs rounded-full ${TONE[tone]}`}>{children}</span>;
}
```

`frontend/src/components/ui/Delta.tsx`:
```tsx
// Delta.tsx — D-NAO-47. **방향 전용**. invert prop이 없다(§8-1 규칙 2).
// ★기존 mopDelta는 invert 플래그로 "비용 증가는 나쁨"이라는 **판단**을 방향색에 섞었다.
//   판단은 판단 토큰(judge-*)으로 옆에 따로 표시한다. 여기는 오르내림만 말한다.
//   MOP/한국 증시 관례: 증가=▲빨강, 감소=▼파랑.
import { pctFromFraction } from "../../lib/format";

export function Delta({ fraction }: { fraction: number | null | undefined }) {
  if (fraction == null) return <span className="text-dir-flat">—</span>;
  if (fraction === 0) return <span className="text-dir-flat tabular-nums">0.00%</span>;
  const up = fraction > 0;
  return (
    <span className={`tabular-nums ${up ? "text-dir-up" : "text-dir-down"}`}>
      {up ? "▲" : "▼"} {pctFromFraction(Math.abs(fraction))}
    </span>
  );
}
```

`frontend/src/components/ui/Loading.tsx`:
```tsx
// Loading.tsx — D-NAO-47. ★§9 라이브 실측: 진단보드가 5~8초간 **완전 백지**였다
// (/api/naver/ad/diagnosis 지연, 스피너·스켈레톤 없음). 멈춘 게 아닌데 멈춘 것처럼 보인다.
export function Loading({ label = "불러오는 중…", rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div className="p-4" aria-busy="true" aria-live="polite">
      <p className="text-xs text-gray-400 mb-2">{label}</p>
      <div className="space-y-2">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="h-4 bg-gray-100 rounded animate-pulse" />
        ))}
      </div>
    </div>
  );
}
```

`frontend/src/components/ui/CoverageBar.tsx`:
```tsx
// CoverageBar.tsx — D-NAO-47. 1층 ① 커버리지(현재 우리 1.15%).
import { won, pctFromFraction } from "../../lib/format";

export function CoverageBar({ ours, mop, manual }: { ours: number; mop: number; manual: number }) {
  const total = ours + mop + manual;
  if (total <= 0) {
    return <p className="text-xs text-gray-500">광고비 데이터가 없습니다(조회 기간에 집행 없음).</p>;
  }
  const pctOf = (v: number) => (v / total) * 100;
  return (
    <div>
      <div className="flex h-3 w-full overflow-hidden rounded bg-gray-100">
        <div className="bg-owner-ours" style={{ width: `${pctOf(ours)}%` }} title="우리 MOP" />
        <div className="bg-owner-mop" style={{ width: `${pctOf(mop)}%` }} title="원본 MOP" />
        <div className="bg-owner-manual" style={{ width: `${pctOf(manual)}%` }} title="수동" />
      </div>
      <p className="mt-1 text-xs text-gray-500">
        우리 MOP {won(ours)} ({pctFromFraction(ours / total)}) · 전체 {won(total)}
      </p>
    </div>
  );
}
```

`frontend/src/components/ui/Table.tsx`:
```tsx
// Table.tsx — D-NAO-47. ★페이지네이션이 계약이다.
// §9 라이브 실측: 진단보드가 489행을 무페이징으로 그려 **페이지 스크롤 27,305px**가 나왔다.
// 3층 원자료 탐색은 키워드 **91,005행**이 대상이다. 상한 없이 그리면 브라우저가 죽는다.
import type { ReactNode } from "react";
import { Button } from "./Button";
import { num } from "../../lib/format";

export function Th({ children, right }: { children: ReactNode; right?: boolean }) {
  return (
    <th className={`px-4 py-3 text-xs font-medium text-gray-500 ${right ? "text-right" : "text-left"}`}>
      {children}
    </th>
  );
}

export function Td({ children, right }: { children: ReactNode; right?: boolean }) {
  return (
    <td className={`px-4 py-2 text-sm border-b border-gray-100 ${right ? "text-right tabular-nums" : ""}`}>
      {children}
    </td>
  );
}

export function Table({ head, children }: { head: ReactNode; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead className="bg-gray-50"><tr>{head}</tr></thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

/** 서버 페이지네이션 바. total > pageSize면 반드시 붙인다. */
export function Pager({ total, offset, pageSize, onOffset }: {
  total: number; offset: number; pageSize: number; onOffset: (n: number) => void;
}) {
  if (total <= pageSize) return null;
  const from = offset + 1;
  const to = Math.min(offset + pageSize, total);
  return (
    <div className="flex items-center justify-between px-4 py-2 border-t border-gray-100">
      <span className="text-xs text-gray-500">{num(from)}–{num(to)} / {num(total)}</span>
      <div className="flex gap-1">
        <Button disabled={offset === 0} onClick={() => onOffset(Math.max(0, offset - pageSize))}>이전</Button>
        <Button disabled={to >= total} onClick={() => onOffset(offset + pageSize)}>다음</Button>
      </div>
    </div>
  );
}
```

`frontend/src/components/ui/index.ts`:
```ts
export { Card } from "./Card";
export { Stat } from "./Stat";
export { EmptyState } from "./EmptyState";
export { Button } from "./Button";
export { Badge } from "./Badge";
export { Delta } from "./Delta";
export { Loading } from "./Loading";
export { CoverageBar } from "./CoverageBar";
export { Table, Th, Td, Pager } from "./Table";
```

- [ ] **Step 3: 타입·빌드 확인**

Run: `cd frontend && npx tsc -b && npm run build`
Expected: 둘 다 성공

- [ ] **Step 4: 커밋**

```bash
cd frontend && git add src/components/ui
git commit -m "feat(naver-ad): D-NAO-47 P2-T3 공통 컴포넌트 9종 — Stat/EmptyState가 reason을 타입으로 강제"
```

---

## Task 4: `api.ts` — 신규 API 4개 배선

**Files:** Modify `frontend/src/lib/api.ts` (네이버 광고 구간 ≈1510~1872)

- [ ] **Step 1: 타입·함수 추가**

`frontend/src/lib/api.ts` **끝**에 추가. **★응답 키는 `rows`다**(`items` 아님 — 기존 라우터 관례):

```ts
// ── D-NAO-47 커맨드 센터 API ──
// ★응답 키는 rows다(items 아님). 기존 /proposals·/bep·/expert-reviews와 같은 관례.

export interface NaverChangeLogRow {
  id: number;
  changed_at: string | null;
  entity_type: string;
  entity_id: string;
  campaign_id: string;
  action: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  rationale: string | null;
  outcome: string | null;
  dry_run: boolean;
  proposal_id: number | null;
  executed_at: string | null;
}

export interface NaverChangeLogResponse { total: number; rows: NaverChangeLogRow[] }

/** 변경 이력. ★include_dry_run 기본 false — "우리 조작 N회"는 실집행만 센다(D-47-h). */
export async function fetchNaverChangeLog(params: {
  campaign_id?: string; action?: string; days?: number;
  include_dry_run?: boolean; limit?: number; offset?: number;
} = {}): Promise<NaverChangeLogResponse> {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v != null) q.set(k, String(v)); });
  return fetchApi(`/api/naver/ad/change-log?${q.toString()}`);
}

export interface NaverRawKeywordRow {
  entity_id: string; name: string; parent_id: string; campaign_id: string;
  campaign_type: string; status: string; bid_amt: number | null;
  monthly_volume: number | null; competition: string | null; synced_at: string | null;
}

export async function fetchNaverRawKeywords(params: {
  q?: string; campaign_id?: string; status?: string; limit?: number; offset?: number;
} = {}): Promise<{ total: number; rows: NaverRawKeywordRow[] }> {
  const s = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v != null) s.set(k, String(v)); });
  return fetchApi(`/api/naver/ad/raw/keywords?${s.toString()}`);
}

export interface NaverRawSearchTermRow {
  ad_date: string | null; campaign_id: string; adgroup_id: string;
  search_term: string; source: string; imp: number; clk: number; cost: number;
}

export async function fetchNaverRawSearchTerms(params: {
  q?: string; campaign_id?: string; days?: number; limit?: number; offset?: number;
} = {}): Promise<{ total: number; rows: NaverRawSearchTermRow[] }> {
  const s = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v != null) s.set(k, String(v)); });
  return fetchApi(`/api/naver/ad/raw/search-terms?${s.toString()}`);
}

export interface NaverRawHourlyRow {
  ad_date: string | null; snapshot_hour: number; snapshot_at: string | null;
  campaign_id: string; campaign_type: string; cost: number; clk: number; imp: number;
  daily_budget: number | null;
  /** ★예산이 없거나 0이면 null — "소진율 0%"가 아니라 "알 수 없음"이다. */
  spend_ratio: number | null;
}

export async function fetchNaverRawHourly(params: {
  campaign_id?: string; days?: number; limit?: number; offset?: number;
} = {}): Promise<{ total: number; rows: NaverRawHourlyRow[] }> {
  const s = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v != null) s.set(k, String(v)); });
  return fetchApi(`/api/naver/ad/raw/hourly?${s.toString()}`);
}
```

**⚠️ 확인**: `fetchApi`가 이 파일의 실제 헬퍼 이름인지 grep으로 확인하고, 다르면 기존 함수들이 쓰는 패턴을 그대로 따를 것.

- [ ] **Step 2: 기존 `NaverProposal` 인터페이스에 Phase 1 신규 필드 추가**

`api.ts`에서 제안 인터페이스를 찾아 추가:
```ts
  target_bid: number | null;
  target_lock: boolean | null;
  target_budget: number | null;
  budget_auto_eligible: boolean | null;
  /** 백엔드가 주는 정보성/실행형 구분. ★프론트에서 유형 문자열로 재분류하지 말 것 —
   *  백엔드에 유형이 추가되면 조용히 드리프트한다. */
  informational: boolean;
```

- [ ] **Step 3: 타입 확인 + 커밋**

Run: `cd frontend && npx tsc -b`
```bash
cd frontend && git add src/lib/api.ts
git commit -m "feat(naver-ad): D-NAO-47 P2-T4 api.ts — change-log·raw 3종 배선 + 제안 target_bid/informational"
```

---

## Task 5: 제안 라벨 14종 정합

**Files:** Modify `frontend/src/pages/NaverAdOptimizationConsole.tsx:105-112`

**★14종이다(13종 아님).** 계획 초판이 `budget_down`을 빠뜨렸는데 백엔드에 이미 배선돼 있었고(`naver_execution_harness._ACTION_BY_PROPOSAL_TYPE:104`, D-NAO-42-f), Phase 1의 드리프트 가드 테스트가 그걸 잡아냈다. 백엔드 단일 진실 = `proposal_writer.ALL_PROPOSAL_TYPES`.

- [ ] **Step 1: 라벨 맵 교체**

`NaverAdOptimizationConsole.tsx:105`의 `PROPOSAL_TYPE_LABEL`을 교체:

```tsx
// D-NAO-47: 제안 유형 14종 전량. 백엔드 단일 진실 = proposal_writer.ALL_PROPOSAL_TYPES.
// ★기존엔 6종만 정의해 9종이 **영문 원문으로 렌더**됐고(라이브 확인: trigger_pacing·
//   account_brief가 영문 pill), 반대로 백엔드가 만들지 않는 'budget'·'new_setup'
//   **유령 라벨**을 갖고 있었다(스펙 §1-3).
// 백엔드에 유형을 추가하면 여기도 추가한다 — 백엔드
// test_all_proposal_types_constant_covers_every_emitted_type이 상수 쪽을 지킨다.
const PROPOSAL_TYPE_LABEL: Record<string, string> = {
  // 실행형
  bid_up: "입찰 인상",
  bid_down: "입찰 인하",
  growth_bid_up: "성장 입찰 인상",
  negative_keyword: "제외 키워드",
  pause: "정지",
  resume: "재개",
  budget_up: "예산 증액",
  budget_down: "예산 감액",
  budget_pre_exhaustion: "예산 소진 임박",
  // 정보성(informational=true)
  anomaly: "이상 감지",
  anomaly_freshness: "데이터 신선도 이상",
  account_brief: "계정 브리핑",
  trigger_pacing: "페이싱 경보",
  trigger_cpc_spike: "CPC 급등 경보",
};
```

- [ ] **Step 2: `target_bid` 표시 — "얼마로" 올리는지**

제안 카드 렌더 지점(`:680` 근처, `PROPOSAL_TYPE_LABEL[p.proposal_type]`을 쓰는 곳)에서 라벨 옆에 목표값을 추가:

```tsx
{/* D-NAO-47: "입찰 인상" 카드가 *얼마로* 올리는지 화면에 없던 결함(스펙 §1-6).
    현재 pending 실행대상 5건이 전부 bid_up이라 바로 체감된다. */}
{p.target_bid != null && (
  <span className="ml-1 text-xs text-gray-600 tabular-nums">→ {won(p.target_bid)}</span>
)}
{p.target_budget != null && (
  <span className="ml-1 text-xs text-gray-600 tabular-nums">→ 일예산 {won(p.target_budget)}</span>
)}
```

`won`은 `lib/format`에서 import(로컬 정의 제거는 Task 6).

- [ ] **Step 3: 타입·빌드 + 커밋**

Run: `cd frontend && npx tsc -b && npm run build`
```bash
cd frontend && git add src/pages/NaverAdOptimizationConsole.tsx
git commit -m "feat(naver-ad): D-NAO-47 P2-T5 제안 라벨 14종 정합 + target_bid 표시 (유령 2종 제거)"
```

---

## Task 6: 기존 3파일에서 로컬 유틸 제거 → `lib/format.ts`

**Files:** Modify `NaverAdReport.tsx` `NaverAdDiagnosisBoard.tsx` `NaverAdOptimizationConsole.tsx`

**⚠️ 이 3파일만이다.** `AdReport.tsx`·`NaverOps.tsx`·`CoupangOps.tsx`·`Dashboard.tsx`는 **건드리지 않는다**(pct 100배 함정 — 착수 전 필독 §1).

- [ ] **Step 1: 3파일에서 로컬 정의 삭제 + import로 교체**

각 파일에서 `isoKST` / `fmt` / `won` / `pct` / `roasX` 로컬 함수 정의를 **삭제**하고 상단에 추가:
```ts
import { isoKST, num, won, pctFromFraction, roasX, NO_DATA } from "../lib/format";
```

호출부 치환:
- `fmt(` → `num(`
- `pct(` → `pctFromFraction(` — **★기존 3파일은 전부 분수 계열이므로 동작 불변.** 자릿수 기본값 차이 주의: `NaverAdReport`는 `digits=2`, `DiagnosisBoard`는 `digits=1`이었다. **호출부에 명시**해 기존 렌더를 보존할 것(`pctFromFraction(x, 1)`).
- `"-"` 리터럴이 "없음" 의미로 쓰인 곳 → `NO_DATA`

- [ ] **Step 2: ★검증 — 로컬 정의가 0건인지**

Run:
```bash
cd frontend && grep -nE "^(function|const) (isoKST|fmt|won|pct|roasX)" src/pages/NaverAdReport.tsx src/pages/NaverAdDiagnosisBoard.tsx src/pages/NaverAdOptimizationConsole.tsx
```
Expected: **매치 0건**

- [ ] **Step 3: ★검증 — 스코프 밖 파일 diff 0줄**

Run:
```bash
cd frontend && git diff --stat -- src/pages/AdReport.tsx src/pages/NaverOps.tsx src/pages/CoupangOps.tsx src/pages/Dashboard.tsx src/pages/CommandCenter.tsx src/pages/Orders.tsx src/pages/Settlements.tsx
```
Expected: **출력 없음**(0줄 변경). 출력이 있으면 경계를 넘은 것 — 되돌릴 것.

- [ ] **Step 4: 타입·빌드 + 커밋**

Run: `cd frontend && npx tsc -b && npm run build && npx vitest run`
```bash
cd frontend && git add src/pages/NaverAdReport.tsx src/pages/NaverAdDiagnosisBoard.tsx src/pages/NaverAdOptimizationConsole.tsx
git commit -m "refactor(naver-ad): D-NAO-47 P2-T6 naver-ad 3파일 유틸 통합 (경계 밖 파일 불변)"
```

---

## Task 7: 1층 — `NaverAdCommandCenter.tsx`

**Files:** Create `frontend/src/pages/NaverAdCommandCenter.tsx`

**설계:** 스펙 §4 1층. **D-47-c: N=1과 N=여럿이 같은 컴포넌트** — 합계 + 리스트(오늘 1행, 나중 N행). 카나리 전용 화면 금지.

**★이 화면은 대부분 "0"으로 채워진다. 그게 사실이고, 사실을 보여주는 게 이 화면의 일이다(D-47-h).** 커버리지 1.15% · 우리 조작 0회 · 승인 0건.

- [ ] **Step 1: 구현**

`frontend/src/pages/NaverAdCommandCenter.tsx`:

```tsx
// NaverAdCommandCenter.tsx — D-NAO-47 1층. 우리 MOP가 돌리는 광고의 성과.
//
// D-47-a: 1층은 "우리 MOP가 돌리는 광고의 성과"다(Jino: "우리 MOP가 돌리는 광고성과를 보자는거야").
// D-47-c: N=1(오늘 04 카나리 1개)과 N=여럿(나중)이 **같은 컴포넌트**다 — 카나리 전용 화면 금지.
// D-47-h: **"왜 0인가"가 1층 시민**이다. 라이브 실측상 이 화면은 대부분 0으로 채워진다
//         (커버리지 1.15% · 우리 조작 0회 · 승인 0건). 0을 찍고 침묵하면 MOP의 실패를
//         복제하는 것이다(스펙 §2-3). 볼품없어도 그게 사실이다.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Card, Stat, EmptyState, Loading, CoverageBar, Table, Th, Td, Badge } from "../components/ui";
import { num, won, roasX, pctFromFraction } from "../lib/format";
import {
  fetchNaverAdDashboardOverview, fetchNaverChangeLog, fetchNaverCampaignSettings,
} from "../lib/api";

export default function NaverAdCommandCenter() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [overview, setOverview] = useState<any>(null);
  const [changeCount, setChangeCount] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        setLoading(true);
        const [ov, cl] = await Promise.all([
          fetchNaverAdDashboardOverview(),
          // ★dry_run 제외가 기본 — 실집행만 센다. 아무것도 안 했는데 일한 것처럼
          //   보이면 안 된다(D-47-h 정직성).
          fetchNaverChangeLog({ days: 30, limit: 1 }),
        ]);
        if (!alive) return;
        setOverview(ov);
        setChangeCount(cl.total);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  if (loading) return <Loading label="커맨드 센터를 불러오는 중…" rows={6} />;
  if (error) return <EmptyState reason={`불러오지 못했습니다: ${error}`} hint="새로고침하거나 백엔드 상태를 확인하세요." />;

  const cov = overview?.optimizer_coverage ?? { ours_cost: 0, mop_cost: 0, none_cost: 0, total: 0 };

  return (
    <div className="space-y-4">
      {/* ① 관리주체 3열 대조 — 우리 열만 크게(위계=대비) */}
      <Card title="누가 이 광고를 돌리는가">
        <div className="p-4 space-y-4">
          <CoverageBar ours={cov.ours_cost ?? 0} mop={cov.mop_cost ?? 0} manual={cov.none_cost ?? 0} />
          <div className="grid grid-cols-3 gap-4">
            <div className="rounded border border-blue-200 bg-blue-50/40 p-3">
              <Badge tone="owner">우리 MOP</Badge>
              <div className="mt-2">
                <Stat
                  label="광고비"
                  value={won(cov.ours_cost ?? 0)}
                  isEmpty={(cov.ours_cost ?? 0) === 0}
                  reason="아직 우리 MOP에 넘긴 캠페인이 없습니다."
                  tone={(cov.ours_cost ?? 0) === 0 ? "idle" : "neutral"}
                  sub={cov.total ? `전체의 ${pctFromFraction((cov.ours_cost ?? 0) / cov.total)}` : undefined}
                />
              </div>
            </div>
            <div className="rounded border border-gray-200 p-3">
              <Badge>원본 MOP</Badge>
              <div className="mt-2">
                <Stat
                  label="광고비"
                  value={won(cov.mop_cost ?? 0)}
                  isEmpty={(cov.mop_cost ?? 0) === 0}
                  // ★D-47-g: 03을 optimizer='mop'으로 태깅해야 이 열이 채워진다.
                  reason="원본 MOP가 돌리는 캠페인이 optimizer='mop'으로 태깅되지 않았습니다(D-47-g)."
                  tone="idle"
                />
              </div>
            </div>
            <div className="rounded border border-gray-200 p-3">
              <Badge>수동</Badge>
              <div className="mt-2">
                <Stat label="광고비" value={won(cov.none_cost ?? 0)} tone="neutral" />
              </div>
            </div>
          </div>
        </div>
      </Card>

      {/* ② 우리 MOP 캠페인 리스트 — 오늘 1행, 나중 N행(D-47-c) */}
      <Card
        title="우리 MOP가 돌리는 캠페인"
        right={<Link to="/naver-ad/console" className="text-xs text-blue-600 hover:underline">캠페인 넘기기 →</Link>}
      >
        <OursCampaignList changeCount={changeCount} />
      </Card>
    </div>
  );
}

function OursCampaignList({ changeCount }: { changeCount: number | null }) {
  const [rows, setRows] = useState<any[] | null>(null);

  useEffect(() => {
    let alive = true;
    fetchNaverCampaignSettings()
      .then((r: any) => { if (alive) setRows((r.rows ?? r ?? []).filter((c: any) => c.optimizer === "ours")); })
      .catch(() => { if (alive) setRows([]); });
    return () => { alive = false; };
  }, []);

  if (rows === null) return <Loading rows={2} />;
  if (rows.length === 0) {
    return (
      <EmptyState
        reason="우리 MOP에 넘긴 캠페인이 아직 없습니다."
        hint="최적화 콘솔에서 캠페인의 관리 주체를 '우리'로 바꾸면 여기에 나타납니다."
      />
    );
  }

  return (
    <Table head={<>
      <Th>캠페인</Th>
      <Th right>우리 조작</Th>
      <Th>상태</Th>
    </>}>
      {rows.map((c) => (
        <tr key={c.campaign_id}>
          <Td><span className="text-gray-900">{c.campaign_name ?? c.campaign_id}</span></Td>
          {/* ★"우리 조작 N회" — 프로그램이 일하는지 매일 보이는 자리.
              0은 회색(judge-idle)이다. 빨강이면 고장난 것처럼 보이고 초록이면 거짓말이다. */}
          <Td right>
            <span className={changeCount === 0 ? "text-judge-idle" : "text-gray-900"}>
              {changeCount == null ? "—" : `${num(changeCount)}회`}
            </span>
          </Td>
          <Td>
            {changeCount === 0 ? (
              <span className="text-xs text-gray-500">
                제안은 나오지만 승인된 실행이 없습니다(사람 승인 게이트 대기).
              </span>
            ) : <Badge tone="owner">가동 중</Badge>}
          </Td>
        </tr>
      ))}
    </Table>
  );
}
```

**⚠️ 구현자 확인 필요**: `fetchNaverAdDashboardOverview`·`fetchNaverCampaignSettings`의 **실제 함수명과 응답 형태**를 `api.ts`에서 grep으로 확인하고 맞출 것. 위는 설계 의도이며, 실제 시그니처가 다르면 **실제에 맞춘다**(추측 금지).

- [ ] **Step 2: 타입·빌드 + 커밋**

Run: `cd frontend && npx tsc -b && npm run build`
```bash
cd frontend && git add src/pages/NaverAdCommandCenter.tsx
git commit -m "feat(naver-ad): D-NAO-47 P2-T7 1층 커맨드 센터 — 3열 대조 + 우리 조작 N회 + 왜 0인가"
```

---

## Task 8: 3층 ⑨ — `NaverAdRawExplorer.tsx`

**Files:** Create `frontend/src/pages/NaverAdRawExplorer.tsx`

**★키워드 91,005행 · 검색어 114,285행이 대상이다. 서버 페이지네이션 필수**(백엔드가 limit 200을 강제하지만 프론트도 페이저를 붙인다).

- [ ] **Step 1: 구현**

```tsx
// NaverAdRawExplorer.tsx — D-NAO-47 3층 ⑨ 원자료 탐색.
// ★수집은 풍부한데 API가 0건이라 볼 방법이 없었다(스펙 §1-4): 키워드 91,005 · 검색어
//   114,285 · 시간당 스냅샷. 여기가 처음으로 그걸 여는 자리.
// ★규모 때문에 서버 페이지네이션이 계약이다(§9 라이브: 489행 무페이징 → 스크롤 27,305px).
import { useEffect, useState } from "react";
import { Card, Table, Th, Td, Pager, Loading, EmptyState, Button } from "../components/ui";
import { num, won, pctFromFraction, NO_DATA } from "../lib/format";
import { fetchNaverRawKeywords, fetchNaverRawSearchTerms, fetchNaverRawHourly } from "../lib/api";

type Tab = "keywords" | "search-terms" | "hourly";
const PAGE = 50;

export default function NaverAdRawExplorer() {
  const [tab, setTab] = useState<Tab>("keywords");
  return (
    <Card
      title="원자료 탐색"
      right={
        <div className="flex gap-1">
          {(["keywords", "search-terms", "hourly"] as Tab[]).map((t) => (
            <Button key={t} variant={tab === t ? "primary" : "ghost"} onClick={() => setTab(t)}>
              {t === "keywords" ? "등록 키워드" : t === "search-terms" ? "검색어" : "시간당"}
            </Button>
          ))}
        </div>
      }
    >
      {tab === "keywords" && <KeywordsPane />}
      {tab === "search-terms" && <SearchTermsPane />}
      {tab === "hourly" && <HourlyPane />}
    </Card>
  );
}

function KeywordsPane() {
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<{ total: number; rows: any[] } | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    fetchNaverRawKeywords({ q: q || undefined, limit: PAGE, offset })
      .then((r) => { if (alive) setData(r); })
      .catch(() => { if (alive) setData({ total: 0, rows: [] }); });
    return () => { alive = false; };
  }, [q, offset]);

  return (
    <div>
      <div className="px-4 py-2 border-b border-gray-100">
        <input
          className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
          placeholder="키워드 검색"
          value={q}
          onChange={(e) => { setOffset(0); setQ(e.target.value); }}
        />
      </div>
      {data === null ? <Loading rows={5} /> : data.rows.length === 0 ? (
        <EmptyState reason={q ? `"${q}"와 일치하는 키워드가 없습니다.` : "등록된 키워드가 없습니다."} />
      ) : (
        <>
          <Table head={<><Th>키워드</Th><Th>캠페인</Th><Th right>입찰가</Th><Th right>월 검색량</Th><Th>상태</Th></>}>
            {data.rows.map((r) => (
              <tr key={r.entity_id}>
                <Td>{r.name}</Td>
                <Td><span className="text-xs text-gray-500">{r.campaign_id}</span></Td>
                <Td right>{r.bid_amt == null ? NO_DATA : won(r.bid_amt)}</Td>
                <Td right>{r.monthly_volume == null ? NO_DATA : num(r.monthly_volume)}</Td>
                <Td>{r.status}</Td>
              </tr>
            ))}
          </Table>
          <Pager total={data.total} offset={offset} pageSize={PAGE} onOffset={setOffset} />
        </>
      )}
    </div>
  );
}

function SearchTermsPane() {
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<{ total: number; rows: any[] } | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    fetchNaverRawSearchTerms({ days: 14, limit: PAGE, offset })
      .then((r) => { if (alive) setData(r); })
      .catch(() => { if (alive) setData({ total: 0, rows: [] }); });
    return () => { alive = false; };
  }, [offset]);

  if (data === null) return <Loading rows={5} />;
  if (data.rows.length === 0) {
    return <EmptyState reason="최근 14일 검색어 데이터가 없습니다." hint="검색어 리포트는 매일 07:40 크론이 수집합니다." />;
  }
  return (
    <>
      <Table head={<><Th>날짜</Th><Th>검색어</Th><Th>소스</Th><Th right>노출</Th><Th right>클릭</Th><Th right>비용</Th></>}>
        {data.rows.map((r, i) => (
          <tr key={`${r.ad_date}-${r.search_term}-${i}`}>
            <Td>{r.ad_date ?? NO_DATA}</Td>
            <Td>{r.search_term}</Td>
            <Td><span className="text-xs text-gray-500">{r.source}</span></Td>
            <Td right>{num(r.imp)}</Td>
            <Td right>{num(r.clk)}</Td>
            <Td right>{won(r.cost)}</Td>
          </tr>
        ))}
      </Table>
      <Pager total={data.total} offset={offset} pageSize={PAGE} onOffset={setOffset} />
    </>
  );
}

function HourlyPane() {
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<{ total: number; rows: any[] } | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    fetchNaverRawHourly({ days: 3, limit: PAGE, offset })
      .then((r) => { if (alive) setData(r); })
      .catch(() => { if (alive) setData({ total: 0, rows: [] }); });
    return () => { alive = false; };
  }, [offset]);

  if (data === null) return <Loading rows={5} />;
  if (data.rows.length === 0) {
    return <EmptyState reason="최근 3일 시간당 스냅샷이 없습니다." hint="시간당 스냅샷은 매시 수집됩니다." />;
  }
  return (
    <>
      <Table head={<><Th>날짜</Th><Th right>시</Th><Th>캠페인</Th><Th right>비용</Th><Th right>일예산</Th><Th right>소진율</Th></>}>
        {data.rows.map((r, i) => (
          <tr key={`${r.ad_date}-${r.snapshot_hour}-${r.campaign_id}-${i}`}>
            <Td>{r.ad_date ?? NO_DATA}</Td>
            <Td right>{r.snapshot_hour}시</Td>
            <Td><span className="text-xs text-gray-500">{r.campaign_id}</span></Td>
            <Td right>{won(r.cost)}</Td>
            <Td right>{r.daily_budget == null ? NO_DATA : won(r.daily_budget)}</Td>
            {/* ★spend_ratio가 null이면 "0%"가 아니라 "알 수 없음"이다(예산 미설정/0). */}
            <Td right>{r.spend_ratio == null ? NO_DATA : pctFromFraction(r.spend_ratio, 1)}</Td>
          </tr>
        ))}
      </Table>
      <Pager total={data.total} offset={offset} pageSize={PAGE} onOffset={setOffset} />
    </>
  );
}
```

- [ ] **Step 2: 타입·빌드 + 커밋**

Run: `cd frontend && npx tsc -b && npm run build`
```bash
cd frontend && git add src/pages/NaverAdRawExplorer.tsx
git commit -m "feat(naver-ad): D-NAO-47 P2-T8 3층 원자료 탐색 — 키워드 91k·검색어 114k 서버 페이지네이션"
```

---

## Task 9: 라우팅 — 탭을 URL로

**Files:** Modify `frontend/src/App.tsx`, `frontend/src/pages/NaverAdReport.tsx`

**§9 라이브 확인:** 탭 3개가 전부 `/naver-ad` 한 URL이라 **딥링크·북마크·새로고침 복원 불가**. 3층 구조를 만들면서 이걸 두면 "원자료 보여줘"를 링크로 못 준다.

- [ ] **Step 1: 라우트 5개**

`App.tsx`의 기존 `/naver-ad` 라우트를 아래로 교체(기존 라우트 구조·Layout 래핑 방식은 그대로 따를 것):

```tsx
<Route path="/naver-ad" element={<NaverAdCommandCenter />} />
<Route path="/naver-ad/report" element={<NaverAdReport />} />
<Route path="/naver-ad/diagnosis" element={<NaverAdDiagnosisBoard />} />
<Route path="/naver-ad/console" element={<NaverAdOptimizationConsole />} />
<Route path="/naver-ad/raw" element={<NaverAdRawExplorer />} />
```

- [ ] **Step 2: `NaverAdReport.tsx`에서 탭 컨테이너 제거**

`NaverAdReport.tsx`는 현재 탭 3개를 조건부 렌더하는 컨테이너다(592 LOC). 탭 상태·조건부 렌더를 걷어내고 **리포트 본문만** 남긴다. 탭 네비게이션은 각 화면 상단의 `<NavLink>` 5개로 대체(공통 컴포넌트로 뽑아 5개 화면이 공유):

`frontend/src/components/ui/LayerNav.tsx` 신규:
```tsx
// LayerNav.tsx — D-NAO-47. 탭을 URL로(§8-4). 딥링크·북마크·새로고침 복원이 되어야 한다.
import { NavLink } from "react-router-dom";

const LINKS = [
  { to: "/naver-ad", label: "커맨드 센터", end: true },
  { to: "/naver-ad/report", label: "리포트" },
  { to: "/naver-ad/diagnosis", label: "진단 보드" },
  { to: "/naver-ad/console", label: "최적화 콘솔" },
  { to: "/naver-ad/raw", label: "원자료" },
];

export function LayerNav() {
  return (
    <nav className="flex gap-1 border-b border-gray-200 mb-4">
      {LINKS.map((l) => (
        <NavLink
          key={l.to}
          to={l.to}
          end={l.end}
          className={({ isActive }) =>
            `px-3 py-2 text-sm border-b-2 -mb-px ${
              isActive ? "border-blue-600 text-blue-700 font-medium" : "border-transparent text-gray-500 hover:text-gray-800"
            }`
          }
        >
          {l.label}
        </NavLink>
      ))}
    </nav>
  );
}
```
`components/ui/index.ts`에 export 추가. 5개 화면 상단에 `<LayerNav />` 배치.

- [ ] **Step 3: ★딥링크 검증**

Run: `cd frontend && npm run build` 후 dev 서버로 5개 URL 직접 진입(새로고침 포함)해 각각 올바른 화면이 뜨는지 확인.

- [ ] **Step 4: 커밋**

```bash
cd frontend && git add src/App.tsx src/pages/NaverAdReport.tsx src/components/ui
git commit -m "feat(naver-ad): D-NAO-47 P2-T9 탭을 URL로 — 딥링크 5개 라우트"
```

---

## Task 10: 2층 — 성적표 두 겹 + 저소진 롤업 + 대기 제안

**Files:** Modify `frontend/src/pages/NaverAdCommandCenter.tsx` (1층 아래에 2층 추가)

**★성적표는 두 겹이고 중복이 아니라 상보다**(스펙 §4 2층 ③):
- **ⓐ 우리 조언이 맞았나** — `/retro-scorecard`(D-NAO-45, **이미 있음**). 방향 정밀도. **실행 0이어도 채점된다** → "우리 조작 0회" 옆에 붙일 유일한 신뢰 근거.
- **ⓑ 우리가 한 일의 결과** — `change_log`(인과). 현재 0건, Phase 1 밸브가 채우기 시작.

**★저속 경보는 접지 말고 집계한다**(스펙 §1-3 정정): 초판이 "노이즈"라 규정한 게 틀렸다. 저속 경보 779건 중 **769건 correct(98.7%)**, 평균 최종 소진율 **4.9%** = 만성 저소진이 실재하고 대응 레버가 없어 전량 만료된 것. **접으면 진짜 신호를 숨긴다.**

- [ ] **Step 1: 2층 추가**

`NaverAdCommandCenter.tsx`의 1층 `</Card>` 뒤에 추가:

```tsx
      {/* ③ 성적표 두 겹 — 중복 아니라 상보 */}
      <div className="grid grid-cols-2 gap-4">
        <Card title="우리 조언이 맞았나 (방향 정밀도)">
          <RetroScorecardPane />
        </Card>
        <Card title="우리가 한 일의 결과 (인과)">
          <ChangeLogPane />
        </Card>
      </div>

      {/* ④ 나를 기다리는 것 */}
      <Card title="나를 기다리는 것">
        <PendingPane />
      </Card>
```

```tsx
function RetroScorecardPane() {
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    let alive = true;
    fetchNaverRetroScorecard().then((r: any) => { if (alive) setData(r); }).catch(() => { if (alive) setData({}); });
    return () => { alive = false; };
  }, []);
  if (data === null) return <Loading rows={3} />;
  // ★실행이 0이어도 이건 채워진다 — 그게 이 패널의 존재 이유다.
  //   "우리 조작 0회"만 있으면 초라하지만, 그 옆에 방향 정밀도가 붙으면 신뢰의 근거가 된다.
  //   정직 경계(D-NAO-45 docstring): "방향 정확도 계기판이지 인과 성과 검증이 아니다
  //   — 인과 승격은 카나리 몫". 그 카나리가 바로 이 화면의 1층이다.
  return <RetroRollup data={data} />;
}

function ChangeLogPane() {
  const [data, setData] = useState<{ total: number; rows: any[] } | null>(null);
  useEffect(() => {
    let alive = true;
    fetchNaverChangeLog({ days: 30, limit: 10 })
      .then((r) => { if (alive) setData(r); })
      .catch(() => { if (alive) setData({ total: 0, rows: [] }); });
    return () => { alive = false; };
  }, []);
  if (data === null) return <Loading rows={3} />;
  if (data.rows.length === 0) {
    return (
      <EmptyState
        reason="최근 30일 우리가 집행한 변경이 없습니다."
        hint="제안은 생성되지만 사람 승인 게이트를 통과한 실행이 아직 없습니다. 승인하면 여기에 '무엇을 왜 바꿨는지'가 쌓입니다."
      />
    );
  }
  return (
    <Table head={<><Th>시각</Th><Th>대상</Th><Th>변경</Th><Th>근거</Th></>}>
      {data.rows.map((r) => (
        <tr key={r.id}>
          <Td><span className="text-xs text-gray-500">{r.changed_at?.slice(5, 16) ?? NO_DATA}</span></Td>
          <Td><span className="text-xs">{r.entity_type} {r.entity_id}</span></Td>
          {/* ★"무엇을 왜 바꿨는지" — MOP에 0개인 컬럼(ref24). 우리가 이길 자리. */}
          <Td>
            <span className="text-xs tabular-nums">
              {String(r.before?.bidAmt ?? r.before?.userLock ?? NO_DATA)} → {String(r.after?.bidAmt ?? r.after?.userLock ?? NO_DATA)}
            </span>
          </Td>
          <Td><span className="text-xs text-gray-600">{r.rationale ?? NO_DATA}</span></Td>
        </tr>
      ))}
    </Table>
  );
}
```

**⚠️ 구현자**: `fetchNaverRetroScorecard`의 실제 이름·응답 스키마를 `api.ts`/백엔드 `/retro-scorecard` 라우터에서 확인하고 `RetroRollup`을 실제 필드에 맞춰 구현할 것. **저속 경보 롤업**(correct 건수·정밀도·평균 최종 소진율)을 반드시 포함 — 접지 말고 집계(스펙 §1-3 정정).

- [ ] **Step 2: 대기 제안 — 실행형만 표면, 정보성은 롤업**

```tsx
function PendingPane() {
  const [rows, setRows] = useState<any[] | null>(null);
  useEffect(() => {
    let alive = true;
    fetchNaverProposals({ status: "pending", limit: 100 })
      .then((r: any) => { if (alive) setRows(r.rows ?? []); })
      .catch(() => { if (alive) setRows([]); });
    return () => { alive = false; };
  }, []);
  if (rows === null) return <Loading rows={3} />;

  // ★백엔드가 준 informational 플래그로 가른다 — 프론트에서 유형 문자열을 하드코딩해
  //   재분류하면 백엔드에 유형이 추가될 때 조용히 드리프트한다.
  const actionable = rows.filter((p) => !p.informational);
  const informational = rows.filter((p) => p.informational);

  return (
    <div>
      {actionable.length === 0 ? (
        <EmptyState reason="지금 결정할 제안이 없습니다." hint="정보성 경보는 아래에 집계됩니다." />
      ) : (
        <Table head={<><Th>유형</Th><Th>대상</Th><Th right>목표</Th><Th>근거</Th></>}>
          {actionable.map((p) => (
            <tr key={p.id}>
              <Td>{PROPOSAL_TYPE_LABEL[p.proposal_type] ?? p.proposal_type}</Td>
              <Td><span className="text-xs">{p.target_id}</span></Td>
              <Td right>{p.target_bid != null ? won(p.target_bid) : p.target_budget != null ? won(p.target_budget) : NO_DATA}</Td>
              <Td><span className="text-xs text-gray-600">{p.rationale}</span></Td>
            </tr>
          ))}
        </Table>
      )}
      {/* ★정보성은 접지 말고 집계 — 저속 경보 98.7%가 진짜였다(스펙 §1-3 정정).
          개별 788건을 나열하진 않되, 숨기지도 않는다. */}
      {informational.length > 0 && (
        <p className="px-4 py-2 text-xs text-gray-500 border-t border-gray-100">
          정보성 경보 {num(informational.length)}건 집계됨(개별 나열 안 함) —{" "}
          <Link to="/naver-ad/console" className="text-blue-600 hover:underline">콘솔에서 보기</Link>
        </p>
      )}
    </div>
  );
}
```

- [ ] **Step 3: 타입·빌드 + 커밋**

Run: `cd frontend && npx tsc -b && npm run build`
```bash
cd frontend && git add src/pages/NaverAdCommandCenter.tsx
git commit -m "feat(naver-ad): D-NAO-47 P2-T10 2층 — 성적표 두 겹(방향정밀도+인과) + 저소진 롤업"
```

---

## Task 11: ★ codex 게이트 (원칙 19)

- [ ] **Step 1: `/codex review` 실행**

프론트 diff 전체 대상. codex에게 명시적으로 물을 것:
1. **`pct` 100배 함정**: `lib/format.ts` 통합이 경계를 넘지 않았는가? 스코프 밖 파일이 `format.ts`를 import하지 않는가? 3파일의 `pct(` → `pctFromFraction(` 치환에서 자릿수 기본값(2 vs 1) 차이로 기존 렌더가 바뀐 곳은 없는가?
2. **`rows` vs `items`**: 신규 API 소비부가 전부 `rows`를 읽는가? `items`를 읽어 조용히 빈 화면이 되는 곳은 없는가?
3. **`<Stat>` reason 강제**가 실제로 타입 레벨에서 동작하는가(유니온이 우회 가능한가)?
4. **경계**: `git diff --stat`으로 스코프 밖 10개 파일 diff가 0줄인가?

- [ ] **Step 2: 대화형 검증(최대 3라운드) → PASS 후 커밋**

---

## Task 12: 배포 + 라이브 검증 (Jino 게이트)

**⚠️ Phase 1(백엔드)과 함께 배포한다.** 프론트만 배포하면 신규 API가 없어 화면이 깨진다.

- [ ] **Step 1: 백엔드 배포**(Phase 1 계획서 Task 8 절차 — DB 백업 → sha 대조 → pm2 재시작 → 엔드포인트 왕복)
- [ ] **Step 2: 프론트 빌드·배포**
- [ ] **Step 3: D-47-g — 03을 `optimizer='mop'`으로 태깅**(3열 대조 MOP 열이 채워짐). prod `naver_campaign_settings`에 03 행 추가. **Jino 승인 필요**(데이터 변경).
- [ ] **Step 4: ★`/browse` 라이브 확인**(원칙22): 딥링크 5개 URL 왕복 · 1층 3열 대조 렌더 · "우리 조작 N회" 표시 · 원자료 탐색 페이지네이션 동작 · 영문 라벨 0건.
- [ ] **Step 5: ★다음날 07:35 크론 후 쓰기 폭증 실측**(Phase 1 계획서 Task 8 Step 4) — **이게 전체 스프린트의 진짜 합격 기준.**

---

## Self-Review (계획 작성자 자체 점검)

**1. 스펙 커버리지:**

| 스펙 §5 포함 | 태스크 |
|---|---|
| 디자인 시스템(@theme·공통 컴포넌트·format.ts) | T1·T2·T3 ✅ |
| 제안 라벨 정합 | T5 ✅(**14종** — 13종은 계획 초판 오류) |
| target_bid 표시 | T5 ✅ |
| 프론트 1~3층 재편 | T7(1층)·T10(2층)·T8·T9(3층) ✅ |
| 원자료 탐색 | T8 ✅ |
| recharts Y축 점검 | **불필요 — §9에서 이미 0 시작 확인**. 신규 차트만 명시 고정 |
| D-47-g 03 태깅 | T12 Step 3 ✅ |

**2. 갭:**
- **⑥ 이상 피드(anomaly)**: 2층 ⑥. 현재 pending anomaly 5건뿐이고 `informational`로 분류돼 T10의 정보성 롤업에 포함된다 → **별도 패널 불필요**(중복). 스펙 §4의 ⑥은 롤업으로 흡수.
- **⑤ 엔진 5단**: 기존 콘솔에 이미 있음(`PLAN_naver-ad-dashboard-mini.md` T2 완료) → 재사용. 1층으로 올리지 않음(2층 유지).
- **⑩ 키워드 랩**: 스코프 밖(P4 백엔드 없음) — 자리도 만들지 않음(빈 탭은 MOP의 🔒 업셀과 같은 실수).

**3. 타입 일관성:**
- `NO_DATA`/`num`/`won`/`pctFromFraction`/`roasX`/`isoKST` — T2 정의와 T6~T10 사용 일치 ✅
- API 응답 키 `rows` 일관 ✅ (Phase 1 실제 구현과 대조 완료)
- `snapshot_hour`(not `hour`) ✅
- `informational` 플래그 — Phase 1 `_serialize_proposal` 실제 구현과 일치 ✅

**4. 플레이스홀더:** 각 태스크에 실제 코드 존재. 단 T7·T10의 3개 함수(`fetchNaverAdDashboardOverview`·`fetchNaverCampaignSettings`·`fetchNaverRetroScorecard`)는 **기존 API라 시그니처를 구현자가 grep으로 확인**하도록 명시(추측 금지). 이건 플레이스홀더가 아니라 "실제에 맞춰라"는 지시.
