# 세션 인수인계: RG 발송관제 → 재고 관리 페이지 분리 (D-19)
> 저장일시: 2026-06-19 14:43
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 프론트: `cd frontend && npm run build`. 배포 = `rsync -az --delete dist/ sellc.ohitech.co.kr:~/ohisell/frontend/dist/` (nginx 서빙, 재시작 불필요).
- 백엔드 테스트: `cd backend && .venv/bin/python -m pytest -q` (★venv=`backend/.venv`, homebrew python엔 의존성 없음).
- prod: `ssh sellc.ohitech.co.kr`(User=ubuntu, ssh config 별칭). PM2 `ohisell-backend`(:8001). **git 아님 → scp+pm2 / rsync 배포.**
- prod 엔드포인트 조회: `ssh sellc.ohitech.co.kr 'curl -s http://localhost:8001/api/...'`
- /browse 스킬로 prod 화면 검증: 로그인 불필요(세션 유지됨), `https://sellc.ohitech.co.kr/inventory` 직행 가능.
- git: HEAD=`89a88a1`(이번 작업, **push 완료**, origin/main 동기화). 브랜치 main.

## 2. 이번 세션 완료 목록
- ✅ **RG 발송관제 트랙 미push 2커밋 push** (세션 초반): `ea4202c`(S6.5)+`6464ff7`(P4) 등 7커밋 origin 동기화.
- ✅ **★메인 작업: RG 발송관제를 /coupang-ops → /inventory 페이지로 분리 (D-19)** — 커밋 `89a88a1`(push 완료).
  - **신규** `frontend/src/components/RgReplenishmentTable.tsx`: CoupangOps의 RgReplenishmentSection+STATUS_META 이전 + **컬럼 헤더 클릭 정렬**(상품명·상태·현재고·발송중·유효재고·일판매·리드타임·판매가능일·권장발송일·권장수량, null 항상 뒤로·0은 null 아님) + **상품명/옵션/ID 검색** + **전체 상품 노출**(showAll 기본 ON, 예전 actionItems 한정 제거).
  - `frontend/src/pages/InventoryPage.tsx`: 빈 stub → **멀티채널 재고 허브**(회사 필터 전체/오픽스/오하이테크 + 채널 탭 [로켓그로스|로켓배송 1P|Wing] + RG plan fetch[cancelled 레이스가드] + 1P/Wing "준비 중" placeholder).
  - `frontend/src/pages/CoupangOps.tsx`: 발송관제 전부 제거(−205줄: import·RgReplenishmentSection·STATUS_META·rgPlan state·useEffect·렌더). 상품별 현황만 남김.
  - **백엔드·API·머니룰 무변경** (build_replenishment_plan이 이미 rg_inventory 전체 옵션 반환).
- ✅ **적대검증** Claude 서브 code-reviewer(superpowers:code-reviewer) → **GATE PASS**(P1 0건, P2 2건은 회귀아님·실위험0). codex 미사용(Jino 방침).
- ✅ **prod 라이브 self-verify(원칙22)**: /browse로 확인 — /inventory에 RG 전체(~934, ⬜데이터부족 포함)·정렬(권장수량↓ 36→32→22)·신선도 배지 464개·콘솔0 / /coupang-ops 로켓그로스 탭 발송관제 ABSENT·상품별현황 PRESENT·콘솔0.
- ✅ **기록**: 트랙 D-19 + 계획서 `docs/PLAN_inventory-page-replenishment-move.md` + failures.jsonl(프론트 dist cross-track 교훈).

## 3. 확정된 결정사항
- **D-19 (발송관제 화면 이전, 확정 2026-06-18~19)**: RG 발송관제는 `/inventory`(재고 관리) 페이지가 **단일 위치**. /coupang-ops에서 완전 제거. 재고 관리 = **멀티채널 허브**(채널 탭 RG|로켓배송1P|Wing), RG만 구현·1P/Wing은 향후 입주(Jino "나중에는 로켓배송 1P·Wing 재고까지 모두 들어와야지"). 전체상품 노출+정렬+검색. 백엔드 무변경. 트랙 `track_coupang-rg-replenishment.md` D-19에 원문 인용 포함 기록.
- **RG 발송관제 트랙 = maintenance 유지** (D-18 3/3 완료). 이번 D-19는 운영 개선(프론트 전용)이라 maintenance 내 작업으로 처리, 트랙 상태 변경 없음.
- **★프론트 dist cross-track 배포 주의(신규 교훈, failures.jsonl 기록)**: vite 빌드는 working tree 전체를 1번들로 묶음 → 미커밋 타 트랙 프론트(로켓1P의 api.ts·CommandCenter.tsx)가 dist에 섞임. **배포 전 `git stash push`로 타 트랙 프론트 격리 → 깨끗한 dist 재빌드(번들 크기로 제외 확인: 838→826KB) → rsync → stash pop**. 백엔드 cross-track 교훈(coupang_ops.py scp)의 프론트 버전.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `frontend/src/pages/InventoryPage.tsx` | ★재고 관리 = 멀티채널 재고 허브(채널 탭·회사 필터·RG fetch) |
| `frontend/src/components/RgReplenishmentTable.tsx` | ★RG 발송관제 테이블(정렬·검색·전체노출). 1P/Wing 추가 시 이 패턴 복제 |
| `frontend/src/pages/CoupangOps.tsx` | 쿠팡 운영(매출·상품별현황·광고). 발송관제 제거됨 |
| `frontend/src/components/Layout.tsx` | 사이드바 메뉴(재고 관리=🏭 /inventory) |
| `backend/app/services/coupang/rg_replenishment.py` | build_replenishment_plan(전체 옵션 반환, 무변경) |
| `backend/app/routers/coupang_ops.py` | GET /replenishment-plan (무변경) |
| `docs/tracks/active/track_coupang-rg-replenishment.md` | RG 트랙 정본(D-1~D-19) |
| `docs/PLAN_inventory-page-replenishment-move.md` | 이번 작업 계획서+체크리스트(전부 [x]) |

## 5. 알려진 이슈 / 주의사항
- **워킹트리 미커밋 = 로켓1P 트랙 작업분** (그 트랙 세션 몫, 건드리지 말 것): `frontend/src/lib/api.ts`, `frontend/src/pages/CommandCenter.tsx`(S5 RocketView), `backend/.../coupang_ops.py`+`rocket_supplier_sync.py`, `tools/com.ohisell.rocket.plist`+`rocket_supplier_fetcher.py`, `track_coupang-rocket-1p.md`, `track_coupang-full-integration.md`, `claude-progress.txt`, MEMORY.md, 다수 HANDOFF_*.md. **이 세션은 이 중 어느 것도 커밋 안 함**(내 5파일만 89a88a1로 스코프).
- **prod에 로켓1P 프론트 미배포**: 이번 /inventory 배포는 1P 프론트를 stash 격리한 깨끗한 dist(index-ChWXZbJv.js). 향후 로켓1P 세션이 dist 재배포 시 1P 화면 포함됨(codex 게이트 통과 후).
- 광고비 쿠키 만료 배너 prod에 떠있음(마지막 수집 2026-06-17) — 별건, 이번 작업 무관.
- Wing 쿠키 만료 시 in-transit freshness-gate가 발송중 0 취급(방어). 만료 시 `POST /api/coupang/ops/inbound/cookie`.

## 6. 다음에 할 작업 (미완료)
- [ ] **재고 관리 페이지 1P/Wing 탭 실구현** (현재 placeholder). 로켓1P 재고는 그 트랙 진행 후, Wing 재고도 데이터 소스 확정 후. RgReplenishmentTable 패턴 복제.
- [ ] **(다른 트랙) 로켓배송 1P** — S5 프론트까지 완료(미커밋·미배포), codex 게이트(quota 리셋) 후 prod 배포+push. HANDOFF=`HANDOFF_ohisell-rocket-1p-S5-frontend_20260618.md`.
- [ ] **(다른 트랙) RG 수수료 회계** — size_mismatch 1건(91313543029) 자동해제 대기(다음 입고 시).
- [ ] (선택) RgReplenishmentTable P2 권고 2건(헤더 "없음 하단" 힌트·빈상태 배지) — 회귀아님, 여유 시.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-inventory-page-split_20260619.md 읽고 이어서 작업해줘
```
