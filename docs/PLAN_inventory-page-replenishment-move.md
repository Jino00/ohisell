# PLAN — 재고 관리 페이지 분리 (RG 발송관제 이사 + 멀티채널 허브)

> 작성일: 2026-06-18 · 트랙: RG 재고·발송 관제 (maintenance) D-19 · 모델: Opus
> 성격: **프론트엔드 전용 리팩토링** (백엔드·API·머니룰 무변경, 리스크 낮음)

## 1. 목표 (한 줄)
RG 발송관제를 `/coupang-ops`(로켓그로스 탭)에서 떼어내 **`/inventory`(재고 관리) 페이지**로 옮기고, 모든 RG 상품을 노출 + 컬럼 정렬·검색이 가능한 **멀티채널 재고 관제 허브**로 만든다.

## 2. 맥락 (왜)
- 발송관제는 **재고/물류** 이슈인데 매출·수익성 화면(쿠팡 운영)에 얹혀 있어 성격이 섞임 (Jino).
- `/inventory` "재고 관리"(🏭) 메뉴는 이미 존재하나 **빈 stub** → 자연스러운 입주처.
- 향후 **로켓배송 1P·Wing 재고**도 같은 페이지로 들어올 예정 (Jino) → 채널 탭 구조로 설계.
- 백엔드 `build_replenishment_plan`은 이미 `rg_inventory` 행 있는 **전체 옵션(~855)** 반환 → "모든 상품 노출"은 프론트만 다루면 됨.

## 3. 확정 결정 (D-19)
- 발송관제 이사처 = **기존 `/inventory` 페이지** (새 메뉴 추가 안 함).
- 페이지 = **멀티채널 재고 허브**: 채널 탭 [로켓그로스(RG) | 로켓배송 1P | Wing]. RG만 구현, 1P·Wing은 "준비 중" placeholder.
- 기본 노출 = **전체 상품**(showAll 기본 ON). 검색·컬럼 정렬 제공.
- 백엔드·API·머니룰 **무변경**.

## 4. 구조 (Agent/Harness/SA — 원칙 18)
```
[Agent] 재고 관리 페이지 (InventoryPage, /inventory)
  └─[Harness] 채널 탭 컨테이너 + 회사 필터
       ├─[입주①] RgReplenishmentTable (신규 컴포넌트, CoupangOps에서 분리·강화)
       │     ├─ 전체 RG 옵션 노출 + 상품명 검색
       │     ├─ 컬럼 헤더 클릭 정렬(현재고·발송중·유효재고·일판매·리드타임·판매가능일·권장수량)
       │     └─ 신선도 배지·상태 요약(기존 이전)
       ├─[입주②] 로켓배송 1P 재고  (placeholder)
       └─[입주③] Wing 재고          (placeholder)
```

## 5. 작업 체크리스트
- [x] T1. 신규 `frontend/src/components/RgReplenishmentTable.tsx` — 이전 + 정렬/검색/전체노출 강화.
- [x] T2. `frontend/src/pages/InventoryPage.tsx` — 멀티채널 허브(회사 필터+채널 탭+plan fetch+1P/Wing placeholder).
- [x] T3. `frontend/src/pages/CoupangOps.tsx` — 발송관제 관련 전부 제거(import·state·effect·render·컴포넌트).
- [x] T4. `npm run build` 성공(tsc 0 에러).
- [x] T5. 적대검증(Claude 서브 code-reviewer) **GATE PASS**(P1 0, P2 2건 회귀아님·실위험0).
- [x] T6. **깨끗한 dist 배포**(1P 프론트 stash 격리 → index-ChWXZbJv.js prod rsync) + /browse 라이브 self-verify: /inventory RG 전체(~934)·정렬(권장수량 36→32→22)·신선도 464개·콘솔0 / /coupang-ops 로켓그로스 탭 발송관제 ABSENT·상품별현황 PRESENT·콘솔0.

## 8. 완료(2026-06-18)
전 게이트 통과. prod 라이브 배포·검증 완료. **미커밋**(Jino 커밋 지시 대기). 백엔드·머니룰 무변경 재확인.

## 6. 완료 기준
- `/inventory`에서 RG 전체 상품(~855) 노출·정렬·검색 동작.
- `/coupang-ops` 로켓그로스 탭에서 발송관제 사라지고 상품별현황만 남음(매출·회계 무영향).
- 빌드 그린 + prod 라이브 1:1 확인.

## 7. 정렬 패턴 (확인됨)
"상품별 현황" 테이블의 `toggleSort`/`ColHeader` 패턴 복제 → 숫자 컬럼 클릭 정렬. null은 항상 뒤로.
