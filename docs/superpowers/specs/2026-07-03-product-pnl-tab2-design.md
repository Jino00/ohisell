# 상품 연관맵 S5 — 화면 C 탭2 통합 손익 UI 설계

> 트랙: `docs/tracks/active/track_product-connection-map.md` (D-12 화면 C)
> 소비 API: `GET /api/products/pnl-reconciliation` (S3 T6, D-11)

## 목적

`ProductConnectionMap.tsx`의 탭2 자리표시(`PnlPlaceholder`)를 실제 UI로 교체한다.
백엔드가 이미 제공하는 대조원장(ledger)·SKU 손익(by_sku)·계정 화해(summary)를
옵션 단위 통합 손익을 확인하려는 사용자(Jino)에게 보여준다.

## 데이터 계약 (기존 백엔드, 변경 없음)

`GET /api/products/pnl-reconciliation?from=&to=&account=`

- `account` 생략 = 전체(네이버/cafe24 포함), `COUPANG_WING1`(오픽스)·`COUPANG_WING2`(오하이테크) = 계정 단위(쿠팡만, 마켓플레이스 컴포넌트 없음).
- 응답:
  - `period`: `{from, to, account?}`
  - `ledger`: `{components[], conservation_ok, sku_conflicts[], warnings[]}`
    - `components[]`: `{channel, component, authoritative_total, allocated_to_sku, allocated_by_sku, residuals, conservation_diff, conservation_ok, date_basis}`
  - `by_sku`: `{internal_sku, channels: {[channel]: {[component]: amount}}, net_profit_allocated_only}[]` — `conservation_ok=false`면 빈 배열.
  - `summary`: `{reconciled_net_profit, net_profit_allocated_total, account_adjustment_residual, trustworthy}`

상품명은 이 API에 없다 — `GET /api/products/connection-map`을 별도 호출해 `internal_sku → product_name` 인덱스를 만들어 조인한다.

## 아키텍처

`frontend/src/pages/ProductConnectionMap.tsx` 안에 컴포넌트 추가(신규 파일 분리 없음, 탭1과 동일한 단일 파일 관례 유지):

```
PnlTab                          — 탭2 루트. 필터 상태 소유, fetch 오케스트레이션
├─ PnlFilterBar                 — 날짜 레인지(from/to) + 계정 드롭다운(전체/오픽스/오하이테크)
├─ PnlSummaryCards               — reconciled_net_profit / net_profit_allocated_total / account_adjustment_residual
├─ PnlSkuTable                   — by_sku 테이블, 행 클릭 시 inline expand(채널×컴포넌트 분해)
└─ PnlLedgerPanel                — 접힘 기본, 대조원장 컴포넌트 테이블 + warnings
```

`frontend/src/lib/api.ts`에 신규 함수 1개:

```ts
export interface PnlComponent {
  channel: string; component: string; authoritative_total: string;
  allocated_to_sku: string; allocated_by_sku: Record<string, string>;
  residuals: Record<string, string>; conservation_diff: string;
  conservation_ok: boolean; date_basis: string;
}
export interface PnlSkuRow {
  internal_sku: string;
  channels: Record<string, Record<string, string>>;
  net_profit_allocated_only: string;
}
export interface PnlReconciliation {
  period: { from: string; to: string; account?: string };
  ledger: { components: PnlComponent[]; conservation_ok: boolean; sku_conflicts: string[]; warnings: unknown[] };
  by_sku: PnlSkuRow[];
  summary: { reconciled_net_profit: string; net_profit_allocated_total: string;
             account_adjustment_residual: string; trustworthy: boolean };
}
export function fetchPnlReconciliation(from: string, to: string, account?: string): Promise<PnlReconciliation> {
  const qs = new URLSearchParams({ from, to, ...(account ? { account } : {}) });
  return fetchApi<PnlReconciliation>(`/api/products/pnl-reconciliation?${qs}`);
}
```

금액 필드는 백엔드가 `Decimal → str`로 직렬화(`_pnl_jsonify`)하므로 타입은 `string`. 표시 시 `Number(str)` 변환 후 `fmt()`(기존 `Intl.NumberFormat("ko-KR")`)로 포맷.

## 동작

### 필터
- 기본값: `from`/`to` = 최근 7일(백엔드 기본값과 동일하게 프론트도 최근 7일 계산해 표시), `account` = 전체.
- 값 변경 시 자동 재요청(디바운스 없음 — 탭1 커버리지 패턴과 동일, 사용자가 날짜피커/드롭다운 조작 완료 후 blur/change 이벤트 기준).
- 계정 드롭다운에 오픽스/오하이테크 선택 시: "계정 선택 시 네이버·자사몰 손익은 제외됩니다(계정 단위는 쿠팡만 대조)" 캡션 표시.

### 신뢰도 게이트
- `summary.trustworthy === false`(= `ledger.conservation_ok === false`)면:
  - 상단에 경고 배너: "⚠️ 원장 불균형 — SKU 손익 표시 불가. 아래 대조원장에서 diff≠0 컴포넌트를 확인하세요."
  - `PnlSkuTable` 렌더하지 않음(백엔드가 `by_sku=[]`로 이미 보냄 — 프론트는 단순히 trustworthy로 안내 문구만 분기).
  - `PnlLedgerPanel`은 기본 펼침 상태로 전환(평소엔 접힘).

### PnlSummaryCards
3장: `reconciled_net_profit`(제목 "계정 순익(권위)", 가장 큼) / `net_profit_allocated_total`(제목 "SKU 귀속 순익 합") / `account_adjustment_residual`(제목 "미배분 잔차", `|값| > 0`이면 주황 텍스트, 항상 툴팁 "미매핑 옵션 + 계정 단위 조정[RG 플립·비-PA 광고·정산 매출조정 등]. 안분하지 않음").

### PnlSkuTable
- 컬럼: 상품명(조인, 없으면 `internal_sku` 그대로 표시) / internal_sku / 순익(`net_profit_allocated_only`, 음수는 빨강).
- 정렬은 백엔드가 이미 desc — 프론트 재정렬 없음.
- 행 클릭 → expand: `channels` 객체를 순회해 `channel / component / amount` 미니 테이블. 탭1의 셀 편집 패턴처럼 별도 상태(`expandedSku: string | null`, 토글 클릭)로 구현.

### PnlLedgerPanel
- 토글 버튼 "대조원장 상세 보기 / 접기" (기본: 접힘. 단 trustworthy=false면 기본 펼침).
- `conservation_ok` 배지(초록/빨강).
- `components` 테이블: channel / component / authoritative_total / allocated_to_sku / residuals 합계 / conservation_diff. `conservation_diff !== "0"` 인 행만 빨강 배경으로 강조(대부분 diff=0이라 전부 강조하면 신호가 묻힘).
- `warnings` 배열을 리스트로 아래에 표시(예: `partial_period_settlement`) — 항목이 비어있으면 섹션 자체 숨김.
- `sku_conflicts`(옵션ID→복수 internal_sku 무결성 결손)도 여기 표시, 탭1 충돌 배지와 같은 스타일(빨강 텍스트 배지) 재사용.

## 에러/로딩
- 기존 탭1과 동일한 관례: `loading` boolean, 에러는 `catch`해서 문자열 배너로 표시(예외를 던지지 않음).
- 상품명 조인용 `fetchConnectionMap` 호출도 실패 시 무시(상품명 없이 internal_sku만 표시 — 손익 숫자 표시를 막지 않음, degrade gracefully).

## 테스트 범위
프론트 전용 기능이라 백엔드 신규 테스트 없음(API는 S3에서 이미 검증됨). 완료 기준은 라이브 브라우저 확인(원칙22):
1. dev 서버에서 탭2 진입 → 최근 7일 기본값으로 자동 로드.
2. 계정 드롭다운 전환 시 데이터 변경 확인(오픽스/오하이테크/전체).
3. SKU 행 클릭 → 채널별 분해 expand 확인.
4. (가능하면) 의도적으로 `conservation_ok=false`가 되는 케이스 재현이 어려우므로, 코드 리뷰로 분기 로직만 확인 — 실제 dev DB가 항상 균형 상태라면 이 분기는 라이브로 트리거 못 할 수 있음(그 경우 self-verify 보고 시 "미관측(unfalsifiable in current dev DB)"로 명시, 원칙22 — 단정 금지).

## 범위 밖 (S6 이연)
- 오픽스(WING1/RG1) 매핑 결손 보강 — 별도 S6.
- 잔차 버킷의 시각화(차트) — 이번 스코프는 테이블/카드까지, 차트는 요청 시 후속.
