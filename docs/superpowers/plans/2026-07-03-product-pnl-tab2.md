# S5 — 상품 연관맵 탭2 통합 손익 UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `ProductConnectionMap.tsx`의 탭2 자리표시(`PnlPlaceholder`)를 `GET /api/products/pnl-reconciliation`을 소비하는 실제 통합 손익 UI로 교체한다.

**Architecture:** `frontend/src/lib/api.ts`에 타입 3개(`PnlComponent`/`PnlSkuRow`/`PnlReconciliation`) + 함수 1개(`fetchPnlReconciliation`)를 추가. `frontend/src/pages/ProductConnectionMap.tsx`에 `PnlPlaceholder`를 지우고 `PnlTab`(필터+오케스트레이션) · `PnlSummaryCards` · `PnlSkuTable`(행 확장) · `PnlLedgerPanel`(대조원장, 기본 접힘)을 추가. 상품명은 기존 `fetchConnectionMap()`을 재사용해 클라이언트에서 `internal_sku → product_name` 인덱스를 만들어 조인한다.

**Tech Stack:** React + TypeScript + Vite + Tailwind (기존 스택, 신규 의존성 없음). 이 프로젝트 프론트엔드에는 자동화 테스트 프레임워크가 없다(vitest 등 미설치, `*.test.*` 파일 0개) — 각 태스크는 `tsc`(빌드) 컴파일 체크로 회귀를 잡고, 최종 태스크에서 dev 서버 라이브 브라우저 확인으로 동작을 검증한다(원칙14 self-verification).

**Spec:** `docs/superpowers/specs/2026-07-03-product-pnl-tab2-design.md`
**Track:** `docs/tracks/active/track_product-connection-map.md` (S5)

---

### Task 1: api.ts — PNL 타입 + fetchPnlReconciliation

**Files:**
- Modify: `frontend/src/lib/api.ts:282` (바로 다음 줄, `MappingIngestResult` 인터페이스 닫는 `}` 직후 · `// ── Order Types ──` 직전에 삽입)

- [ ] **Step 1: 신규 블록 삽입**

`frontend/src/lib/api.ts`의 282번째 줄(`}` — `MappingIngestResult` 닫는 줄) 바로 뒤, `// ── Order Types ──` 줄 바로 앞에 아래 블록을 삽입한다:

```ts

// ── 통합 손익 대조원장 (트랙 S5, GET /api/products/pnl-reconciliation) ──
// 금액 필드는 백엔드가 Decimal→str로 직렬화(_pnl_jsonify)하므로 전부 string.
export interface PnlComponent {
  channel: string;
  component: string;
  authoritative_total: string;
  allocated_to_sku: string;
  allocated_by_sku: Record<string, string>;
  residuals: Record<string, string>;
  conservation_diff: string;
  conservation_ok: boolean;
  date_basis: string;
}
export interface PnlLedgerWarning {
  [key: string]: unknown;
}
export interface PnlSkuRow {
  internal_sku: string;
  channels: Record<string, Record<string, string>>;
  net_profit_allocated_only: string;
}
export interface PnlReconciliation {
  period: { from: string; to: string; account?: string };
  ledger: {
    components: PnlComponent[];
    conservation_ok: boolean;
    sku_conflicts: string[];
    warnings: PnlLedgerWarning[];
  };
  by_sku: PnlSkuRow[];
  summary: {
    reconciled_net_profit: string;
    net_profit_allocated_total: string;
    account_adjustment_residual: string;
    trustworthy: boolean;
  };
}
export function fetchPnlReconciliation(
  from: string,
  to: string,
  account?: string,
): Promise<PnlReconciliation> {
  const params = new URLSearchParams({ from, to });
  if (account) params.set("account", account);
  return fetchApi<PnlReconciliation>(`/api/products/pnl-reconciliation?${params.toString()}`);
}
```

- [ ] **Step 2: 타입체크로 검증**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: 에러 없음(0 errors) — 이 시점엔 아직 아무도 새 타입/함수를 사용하지 않으므로 unused-export 경고는 없다(export이므로 unused 취급 안 됨).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(product-connection-map): S5 pnl-reconciliation API 클라이언트 타입/함수 추가"
```

---

### Task 2: PnlTab 골격 — 필터 바 + 데이터 페치 오케스트레이션

**Files:**
- Modify: `frontend/src/pages/ProductConnectionMap.tsx:1-16` (import 블록)
- Modify: `frontend/src/pages/ProductConnectionMap.tsx:35` (`<PnlPlaceholder />` → `<PnlTab />`)
- Modify: `frontend/src/pages/ProductConnectionMap.tsx:503-513` (`PnlPlaceholder` 함수를 `PnlTab`으로 교체)

- [ ] **Step 1: import 블록에 신규 함수/타입 추가**

`frontend/src/pages/ProductConnectionMap.tsx` 상단 import를 아래로 교체한다(기존 4-16줄):

```tsx
import { useState, useEffect, useCallback, useRef } from "react";
import {
  fetchApi,
  uploadFile,
  downloadUrl,
  fetchConnectionMap,
  fetchMappingCoverage,
  updateMapping,
  fetchPnlReconciliation,
  type ConnectionMap,
  type ConnCell,
  type ChannelCoverage,
  type MappingIngestResult,
  type PnlReconciliation,
  type PnlSkuRow,
} from "../lib/api";

const fmt = (n: number) => new Intl.NumberFormat("ko-KR").format(n);
const won = (s: string) => `${fmt(Math.round(Number(s)))}원`;

function isoKST(d: Date): string {
  const kst = new Date(d.toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
  return `${kst.getFullYear()}-${String(kst.getMonth() + 1).padStart(2, "0")}-${String(kst.getDate()).padStart(2, "0")}`;
}

const PNL_ACCOUNTS = [
  { value: "", label: "전체" },
  { value: "COUPANG_WING1", label: "오픽스" },
  { value: "COUPANG_WING2", label: "오하이테크" },
];
```

(기존 `const fmt = ...` 한 줄을 위 블록으로 대체 — `won`/`isoKST`/`PNL_ACCOUNTS`가 새로 추가된 것이 핵심 변경.)

- [ ] **Step 2: 탭 렌더 분기 교체**

35번째 줄:

```tsx
      {tab === "map" ? <ConnectionMapTab /> : <PnlPlaceholder />}
```

를 아래로 교체:

```tsx
      {tab === "map" ? <ConnectionMapTab /> : <PnlTab />}
```

- [ ] **Step 3: PnlPlaceholder 함수를 PnlTab 골격으로 교체**

파일 끝의 `PnlPlaceholder` 함수(503-513줄, `// ─── 탭2: 통합 손익 (S5 자리표시) ───` 주석부터 파일 끝까지)를 통째로 아래로 교체:

```tsx
// ─────────────────────────────────────────────────────────────
// 탭2: 통합 손익 (S5, D-12 — GET /api/products/pnl-reconciliation)
// ─────────────────────────────────────────────────────────────
function PnlTab() {
  const today = isoKST(new Date());
  const ago = (n: number) => {
    const d = new Date();
    d.setDate(d.getDate() - (n - 1));
    return isoKST(d);
  };

  const [from, setFrom] = useState(ago(7));
  const [to, setTo] = useState(today);
  const [account, setAccount] = useState("");
  const [data, setData] = useState<PnlReconciliation | null>(null);
  const [skuNames, setSkuNames] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [ledgerOpen, setLedgerOpen] = useState(false);

  const load = useCallback(async (f: string, t: string, acc: string) => {
    setLoading(true);
    setErr("");
    try {
      const result = await fetchPnlReconciliation(f, t, acc || undefined);
      setData(result);
      setLedgerOpen(!result.summary.trustworthy);
      // 상품명 조인 — 손익 숫자 표시를 막지 않도록 실패는 무시(원칙: degrade gracefully).
      try {
        const map = await fetchConnectionMap();
        const names: Record<string, string> = {};
        for (const r of map.rows) names[r.internal_sku] = r.product_name;
        setSkuNames(names);
      } catch {
        setSkuNames({});
      }
    } catch (e) {
      setErr(`불러오기 실패: ${e}`);
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(from, to, account);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function applyFilters(f: string, t: string, acc: string) {
    setFrom(f);
    setTo(t);
    setAccount(acc);
    load(f, t, acc);
  }

  return (
    <div>
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <input
          type="date"
          value={from}
          onChange={(e) => applyFilters(e.target.value, to, account)}
          className="px-2 py-1 text-sm border border-gray-300 rounded-md"
        />
        <span className="text-gray-400">~</span>
        <input
          type="date"
          value={to}
          onChange={(e) => applyFilters(from, e.target.value, account)}
          className="px-2 py-1 text-sm border border-gray-300 rounded-md"
        />
        <select
          value={account}
          onChange={(e) => applyFilters(from, to, e.target.value)}
          className="px-2 py-1 text-sm border border-gray-300 rounded-md"
        >
          {PNL_ACCOUNTS.map((a) => (
            <option key={a.value} value={a.value}>
              {a.label}
            </option>
          ))}
        </select>
      </div>
      {account && (
        <div className="mb-3 text-xs text-gray-500">
          계정 선택 시 네이버·자사몰 손익은 제외됩니다(계정 단위는 쿠팡만 대조).
        </div>
      )}

      {err && (
        <div className="mb-3 p-3 bg-red-50 text-red-800 rounded-md text-sm">
          {err}
          <button onClick={() => setErr("")} className="ml-2 text-red-600 underline">
            닫기
          </button>
        </div>
      )}

      {loading && <div className="p-8 text-center text-gray-400 text-sm">불러오는 중…</div>}

      {!loading && data && (
        <>
          {!data.summary.trustworthy && (
            <div className="mb-3 p-3 bg-amber-50 text-amber-800 rounded-md text-sm">
              ⚠️ 원장 불균형 — SKU 손익 표시 불가. 아래 대조원장에서 diff≠0 컴포넌트를 확인하세요.
            </div>
          )}

          <PnlSummaryCards summary={data.summary} />

          {data.summary.trustworthy && (
            <PnlSkuTable
              rows={data.by_sku}
              names={skuNames}
              expanded={expanded}
              onToggle={(sku) => setExpanded(expanded === sku ? null : sku)}
            />
          )}

          <PnlLedgerPanel
            ledger={data.ledger}
            open={ledgerOpen}
            onToggle={() => setLedgerOpen(!ledgerOpen)}
          />
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 4: 타입체크(아직 하위 컴포넌트 미정의 — 의도적 실패 확인)**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: FAIL — `PnlSummaryCards`, `PnlSkuTable`, `PnlLedgerPanel`이 정의되지 않았다는 에러(`Cannot find name 'PnlSummaryCards'` 등). 이 실패를 확인하고 다음 태스크로 진행한다(하위 컴포넌트는 Task 3~4에서 추가).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/ProductConnectionMap.tsx
git commit -m "feat(product-connection-map): S5 PnlTab 골격 — 필터바+페치 오케스트레이션 (하위 컴포넌트 미정의, 다음 커밋에서 추가)"
```

---

### Task 3: PnlSummaryCards + PnlSkuTable

**Files:**
- Modify: `frontend/src/pages/ProductConnectionMap.tsx` (파일 끝에 추가)

- [ ] **Step 1: PnlSummaryCards 컴포넌트 추가**

파일 끝에 추가:

```tsx
function PnlSummaryCards({
  summary,
}: {
  summary: PnlReconciliation["summary"];
}) {
  const residual = Number(summary.account_adjustment_residual);
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
      <div className="bg-white rounded-lg border p-4">
        <div className="text-xs text-gray-500 mb-1">계정 순익(권위)</div>
        <div className="text-xl font-bold text-gray-900">{won(summary.reconciled_net_profit)}</div>
      </div>
      <div className="bg-white rounded-lg border p-4">
        <div className="text-xs text-gray-500 mb-1">SKU 귀속 순익 합</div>
        <div className="text-xl font-bold text-gray-900">{won(summary.net_profit_allocated_total)}</div>
      </div>
      <div
        className="bg-white rounded-lg border p-4"
        title="미매핑 옵션 + 계정 단위 조정(RG 플립·비-PA 광고·정산 매출조정 등). 안분하지 않음."
      >
        <div className="text-xs text-gray-500 mb-1">미배분 잔차</div>
        <div className={`text-xl font-bold ${residual !== 0 ? "text-amber-600" : "text-gray-900"}`}>
          {won(summary.account_adjustment_residual)}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: PnlSkuTable 컴포넌트 추가**

이어서 파일 끝에 추가:

```tsx
function PnlSkuTable({
  rows,
  names,
  expanded,
  onToggle,
}: {
  rows: PnlSkuRow[];
  names: Record<string, string>;
  expanded: string | null;
  onToggle: (sku: string) => void;
}) {
  if (rows.length === 0) {
    return (
      <div className="bg-white rounded-lg border p-8 text-center text-gray-400 text-sm mb-4">
        표시할 SKU 손익이 없습니다.
      </div>
    );
  }
  return (
    <div className="bg-white rounded-lg border overflow-x-auto mb-4">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="border-b bg-gray-50">
            <th className="text-left px-3 py-2 font-medium text-gray-600">상품명</th>
            <th className="text-left px-3 py-2 font-medium text-gray-600">내부코드</th>
            <th className="text-right px-3 py-2 font-medium text-gray-600">순익(SKU 귀속)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const net = Number(r.net_profit_allocated_only);
            const isOpen = expanded === r.internal_sku;
            return (
              <tr key={r.internal_sku}>
                <td colSpan={3} className="p-0">
                  <button
                    onClick={() => onToggle(r.internal_sku)}
                    className="w-full flex border-b hover:bg-gray-50 text-left"
                  >
                    <div className="px-3 py-2 flex-1">{names[r.internal_sku] || r.internal_sku}</div>
                    <div className="px-3 py-2 font-mono text-gray-500 w-40">{r.internal_sku}</div>
                    <div
                      className={`px-3 py-2 text-right font-medium w-40 ${
                        net < 0 ? "text-red-600" : "text-gray-900"
                      }`}
                    >
                      {won(r.net_profit_allocated_only)}
                    </div>
                  </button>
                  {isOpen && (
                    <div className="px-3 py-2 bg-gray-50 border-b">
                      <table className="min-w-full text-xs">
                        <thead>
                          <tr className="text-gray-500">
                            <th className="text-left py-1 pr-3">채널</th>
                            <th className="text-left py-1 pr-3">컴포넌트</th>
                            <th className="text-right py-1">금액</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(r.channels).flatMap(([channel, comps]) =>
                            Object.entries(comps).map(([comp, amt]) => (
                              <tr key={`${channel}-${comp}`}>
                                <td className="py-0.5 pr-3 text-gray-600">{channel}</td>
                                <td className="py-0.5 pr-3 text-gray-600">{comp}</td>
                                <td className="py-0.5 text-right">{won(amt)}</td>
                              </tr>
                            )),
                          )}
                        </tbody>
                      </table>
                    </div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 3: 타입체크**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: `PnlLedgerPanel`을 찾을 수 없다는 에러만 남는다(Task 4에서 추가 예정). `PnlSummaryCards`/`PnlSkuTable` 관련 에러는 사라져야 함.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/ProductConnectionMap.tsx
git commit -m "feat(product-connection-map): S5 PnlSummaryCards + PnlSkuTable(행 확장)"
```

---

### Task 4: PnlLedgerPanel

**Files:**
- Modify: `frontend/src/pages/ProductConnectionMap.tsx` (파일 끝에 추가)

- [ ] **Step 1: PnlLedgerPanel 컴포넌트 추가**

파일 끝에 추가:

```tsx
function PnlLedgerPanel({
  ledger,
  open,
  onToggle,
}: {
  ledger: PnlReconciliation["ledger"];
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="bg-white rounded-lg border">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium text-gray-700 hover:bg-gray-50"
      >
        <span>
          대조원장 상세 {open ? "접기" : "보기"}
          <span
            className={`ml-2 text-xs px-2 py-0.5 rounded ${
              ledger.conservation_ok ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"
            }`}
          >
            {ledger.conservation_ok ? "균형" : "불균형"}
          </span>
        </span>
        <span className="text-gray-400">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="border-t px-4 py-3">
          <div className="overflow-x-auto">
            <table className="min-w-full text-xs mb-3">
              <thead>
                <tr className="text-gray-500">
                  <th className="text-left py-1 pr-3">채널</th>
                  <th className="text-left py-1 pr-3">컴포넌트</th>
                  <th className="text-right py-1 pr-3">권위 총액</th>
                  <th className="text-right py-1 pr-3">SKU 귀속</th>
                  <th className="text-right py-1 pr-3">잔차 합</th>
                  <th className="text-right py-1">diff</th>
                </tr>
              </thead>
              <tbody>
                {ledger.components.map((c, i) => {
                  const residualSum = Object.values(c.residuals).reduce(
                    (a, v) => a + Number(v),
                    0,
                  );
                  const diffNonZero = Number(c.conservation_diff) !== 0;
                  return (
                    <tr key={i} className={diffNonZero ? "bg-red-50" : ""}>
                      <td className="py-0.5 pr-3">{c.channel}</td>
                      <td className="py-0.5 pr-3">{c.component}</td>
                      <td className="py-0.5 pr-3 text-right">{won(c.authoritative_total)}</td>
                      <td className="py-0.5 pr-3 text-right">{won(c.allocated_to_sku)}</td>
                      <td className="py-0.5 pr-3 text-right">{won(String(residualSum))}</td>
                      <td className={`py-0.5 text-right ${diffNonZero ? "text-red-600 font-medium" : ""}`}>
                        {won(c.conservation_diff)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {ledger.sku_conflicts.length > 0 && (
            <div className="mb-2 text-xs">
              <span className="text-red-600 font-medium">채널옵션ID 충돌 {ledger.sku_conflicts.length}건: </span>
              <span className="text-gray-600 font-mono">{ledger.sku_conflicts.join(", ")}</span>
            </div>
          )}

          {ledger.warnings.length > 0 && (
            <div className="text-xs text-amber-700">
              {ledger.warnings.map((w, i) => (
                <div key={i}>⚠ {JSON.stringify(w)}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 타입체크 — 전체 통과 확인**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: PASS(0 errors). `PnlPlaceholder`는 삭제됐으므로 참조가 남아있으면 이 시점에 에러로 드러난다.

- [ ] **Step 3: ESLint**

Run: `cd frontend && npm run lint`
Expected: `frontend/src/pages/ProductConnectionMap.tsx`와 `frontend/src/lib/api.ts`에 새 에러 없음(기존 프로젝트에 이미 있던 무관한 warning은 무시).

- [ ] **Step 4: 빌드**

Run: `cd frontend && npm run build`
Expected: `tsc -b && vite build` 성공, `dist/` 생성.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/ProductConnectionMap.tsx
git commit -m "feat(product-connection-map): S5 PnlLedgerPanel — 대조원장·warnings·sku_conflicts"
```

---

### Task 5: 라이브 브라우저 검증 (원칙14/22)

**목적:** 코드가 컴파일된다고 "됐다"고 말하지 않는다 — 실제로 화면이 그려지고 데이터가 맞물리는지 dev 서버에서 직접 확인한다.

- [ ] **Step 1: 백엔드 dev 서버 기동**

Run: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000` (백그라운드)
Expected: `Uvicorn running on http://127.0.0.1:8000`

- [ ] **Step 2: 프론트 dev 서버 기동**

Run: `cd frontend && npm run dev` (백그라운드)
Expected: `Local: http://localhost:5173/`

- [ ] **Step 3: 브라우저로 탭2 진입 확인**

`preview_start`/`preview_screenshot`/`preview_snapshot`(또는 동일 계열 브라우저 확인 도구)로 `http://localhost:5173/product-connection-map` 진입 → "통합 손익" 탭 클릭.

확인 항목:
1. 최근 7일 기본값으로 자동 로드되고, `PnlSummaryCards` 3장이 숫자를 표시한다(콘솔 에러 없음).
2. `PnlSkuTable`에 최소 1개 이상 행이 보인다(또는 dev DB가 균형 상태가 아니라면 경고 배너가 뜬다 — 둘 중 하나는 반드시 관측되어야 함).
3. 계정 드롭다운을 "오픽스"→"오하이테크"→"전체"로 전환하며 각 전환 시 데이터가 바뀌는지 네트워크 탭(`preview_network`)으로 확인(`/api/products/pnl-reconciliation?...account=COUPANG_WING1` 등 쿼리스트링이 바뀌는지).
4. SKU 행을 클릭해 확장 패널이 열리고 채널별 컴포넌트 분해가 보이는지 확인.
5. "대조원장 상세 보기"를 눌러 컴포넌트 테이블이 펼쳐지고, `conservation_diff`가 0이 아닌 행이 있으면 빨간 배경으로 강조되는지 확인.

- [ ] **Step 4: 결과를 트랙 파일에 원칙22 방식으로 기록**

`docs/tracks/active/track_product-connection-map.md`의 체크리스트에서 `S5 화면 C 탭2 통합 손익 UI` 항목을 `[x]`로 바꾸고, "S5 완료 기록" 섹션을 추가한다. Step 3에서 실제로 관측한 내용(예: "dev DB 최근 7일 기준 conservation_ok=true 확인" 또는 "균형이 안 맞는 케이스는 관측 못함 — unfalsifiable in current dev DB")을 그대로 적는다 — 관측하지 못한 항목은 관측했다고 쓰지 않는다.

- [ ] **Step 5: Commit**

```bash
git add docs/tracks/active/track_product-connection-map.md
git commit -m "docs(product-connection-map): S5 완료 — 탭2 통합 손익 UI 라이브 검증 기록"
```

---

## Self-Review Notes (작성자 메모, 실행 시 무시)

- Spec의 4개 섹션(필터/신뢰도게이트/SummaryCards/SkuTable/LedgerPanel) 전부 Task 2~4에 매핑됨.
- 타입 이름(`PnlComponent`/`PnlSkuRow`/`PnlReconciliation`)은 Task 1에서 정의된 그대로 Task 2~4에서 일관되게 사용.
- `won()`/`isoKST()`는 Task 2 Step 1에서 로컬 정의 — 이후 태스크에서 재정의하지 않고 그대로 재사용.
- 백엔드는 변경하지 않는다(S3에서 이미 완료·배포 게이트 통과) — 이 계획은 100% 프론트엔드.
