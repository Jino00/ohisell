# 세션 인수인계: ohisell-coupang-p7-overview
> 저장일시: 2026-06-03 18:27 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 메가 프로젝트 "쿠팡 API 전기능 연결" 트랙. 이 세션 = **P7 종합조망(Command Center) 완결**(설계→구현→codex 3R→prod 배포→라이브 실증→브라우저 시각확인→main 머지). 트랙 4/7 페이즈 완료. 트랙 파일이 진짜 진실 원천.

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: `cd backend && .venv/bin/python3 -m uvicorn app.main:app --reload` (로컬 venv=Python 3.14, 로컬 포트 8000/임의)
- 프론트: `cd frontend && npm run dev` / 빌드 `npm run build`
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`, **서버 포트=8001**, 프론트=nginx가 `frontend/dist` 서빙
- **서버 환경**: Python **3.10**, DB=SQLite `backend/ohisell.db`, **git 없음 → 배포=scp 파일복사**
- ⚠️ tar 전송: `COPYFILE_DISABLE=1 tar --exclude='._*'` (macOS AppleDouble `._*`가 Linux alembic null-bytes 유발)
- 최신 커밋(main, **로컬만 — 미push**): **a2bbd3a**(P7) ← 1c70c3d(returns KST) ← 8abb607(P4) ← f2f35b2(P2) ← b786e11(B) ← 9a45eee(A) ← a4afac7(P1)
- DB head: 로컬·prod 모두 **c8f1a3b5d7e9** (P7은 신규 테이블 없음 → 마이그레이션 불필요)
- 환경변수(이름만): COUPANG_WING1/WING2/RG1/RG2 각 _VENDOR_ID/_ACCESS_KEY/_SECRET_KEY
- ⚠️ 쿠팡 Open API는 IP 화이트리스트(D-8) — 로컬 전부 403, 실sync/검증은 **서버 SSH에서만**. (P7 조망은 DB만 읽으므로 로컬에서도 엔진 동작은 됨 — 단 로컬 DB엔 쿠팡 데이터 0)

## 2. 이번 세션 완료 목록
### ✅ P7 종합조망 Command Center — 완료(main a2bbd3a, prod 배포·라이브 실증·시각확인. **미push**)
- ✅ 결합엔진 신규 `backend/app/services/coupang/intelligence.py` (345줄):
  - 5소스(주문·광고·반품·수수료·상품마스터)를 **vendor_item_id별 독립 GROUP BY 집계 후 dict merge** → fan-out(N×M×K×J 곱) 방지. 각 소스 **자기 날짜축** 필터(order_date·report_date·recognition_date·requested_at). orders는 **platform='coupang'만**(네이버/cafe24 제외).
  - 3축 파생: 회계(매출−반품차감−수수료(+VAT)−광고비−원가=순이익) / 광고(비용·노출·클릭·전환매출·ROAS·CTR 사실만) / 상품(주문수·반품률·재고·판매상태). D-3 사실/지표만, 추천 없음.
  - 헬퍼: `_agg_orders/_agg_ads/_agg_returns/_agg_fees/_product_master` + `compute_command_center` + `_ratio`(비율 4자리 quantize·0분모 None) + `_f`(None→0 Decimal).
- ✅ 라우터 신규 `backend/app/routers/overview.py`: `GET /api/overview/command-center?from&to`(Decimal→str 재귀 직렬화, 기본 7일 KST, 날짜검증 422). `main.py`에 등록(58라우트).
- ✅ 프론트 신규 `frontend/src/pages/CommandCenter.tsx`(313줄): 3축 탭 + 기간선택(어제/7/14/30일) + 요약카드 + 옵션별 표. `Layout.tsx` 사이드바 "🎯 종합 조망" + `App.tsx` 라우트 `/command-center` + `lib/api.ts` 타입·fetchCommandCenter.
- ✅ codex PASS **3R**(대화형, 원칙19): R1[P2×2] ①net_profit이 service_fee_vat 누락(쿠팡은 수수료+VAT 둘 다 차감)→total_fee 합산·차감 ②orders 집계 status 미필터→취소/반품 매출부풀림+반품테이블 이중차감→`Order.status.notin_(REVENUE_EXCLUDED)` 적용. R2 합의. R3[이름폴백·비율quantize] 합의.
- ✅ ★라이브 실증(prod 실데이터, 원칙22): 회계 302옵션 매출2,958,570·반품차감153,862·수수료201,588·광고76,751·순이익2,526,368(4~6월). 광고 ROAS 1.50. 합계 불변 검증.
- ✅ ★브라우저 시각확인(원칙14, Claude in Chrome): 사이드바 메뉴·3축 탭 모두 정상 렌더. 회계 탭 실상품명("오하이 풀커버 강화유리 액정보호필름…")·원가미설정 amber·순이익. 광고 탭 ROAS(41.78x·21.59x)·CTR. "(이름 미상)" 폴백 동작.
- ✅ 이름폴백(실측 보정): master 커버리지 낮음(fee 84옵션 중 master교집합 4·order 862 중 9 — P1 제약, D-3 사실) → 마스터 없으면 주문/매출내역/반품 상품명 폴백, 둘 다 없으면 "(이름 미상)".
- ✅ main fast-forward 머지 a2bbd3a, 브랜치 feat/coupang-p7-overview 삭제. Failure Memory 기록(결합엔진 2버그).
- ✅ prod 롤백자산: DB백업 `ohisell.db.bak-20260603-p7overview`, 프론트 `frontend/dist.bak-p7`, 백엔드 `/tmp/main.py.bak-p7`(서버).

## 3. 확정된 결정사항 (번복 금지)
- **P7 = D-2 최종목적(Command Center) 달성**: 회계 진짜순이익 + 광고 사실 + 상품 판매를 옵션ID(vendor_item_id) 단일축으로 결합. prod 라이브.
- **결합 설계 3원칙(스키마 검증으로 확정, codex PASS)**: ①fan-out 방지(소스별 독립 GROUP BY 후 dict merge) ②각 소스 자기 날짜축 필터 ③orders는 platform='coupang'만.
- **회계 정확성(codex R1 적발·수정)**: ①순이익에서 수수료는 service_fee+service_fee_vat 둘 다 차감(total_fee) ②orders 집계는 REVENUE_EXCLUDED(cancelled/returned/pending) 제외 — 안 그러면 매출부풀림+반품 이중차감.
- D-3 유지: 시스템은 사실/지표 정리만, 전략판단은 Jino. (조망에 추천 엔진 없음)
- ⚠️ **미push 상태**: 사용자가 push 여부 미결정. 이전 페이즈들은 push 완료였음. 새 세션에서 push할지 먼저 확인.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-full-integration.md` | ★단일 진실원천. D-1~D-11, §4 페이즈(P7 [x]), §5 아키텍처, §7 진행, §8 다음액션. **먼저 읽기** |
| `backend/app/services/coupang/intelligence.py` | ★P7 결합엔진(5소스→3축). 다른 결합/조망 작업 시 재사용 |
| `backend/app/routers/overview.py` | P7 라우터 GET /api/overview/command-center |
| `frontend/src/pages/CommandCenter.tsx` | P7 프론트 3축 뷰 |
| `backend/app/services/profit_calculator.py` | 기존 회계 엔진. REVENUE_EXCLUDED 필터 패턴 출처(intelligence.py가 따름) |
| `backend/app/services/cafe24_status_mapper.py` | `REVENUE_EXCLUDED={cancelled,returned,pending}` |
| `backend/app/services/coupang/settlement_sync.py` | P4 Harness + fee_audit(D-11). 코드 스타일 참고 |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **결합 토대 약함(D-3 사실, P1 제약)**: product master 커버리지 낮음 — fee∩master 4옵션·order∩master 9옵션뿐. master vendorItemId가 실거래 옵션ID와 거의 안 겹침(옵션 다수 vendorItemId null·검색옵션/신상품). 그래서 ① 원가(supply_price) 대부분 빈값 → 순이익에 원가 미반영(201/253옵션 0원) ② 조망 옵션 다수가 단일축(이름폴백으로 화면은 유지). **결합 토대 확대 = product_sync 커버리지 개선(별도 작업)**.
- ⚠️ 배포: macOS tar AppleDouble(`._*`) → Linux alembic null-bytes. `COPYFILE_DISABLE=1 --exclude='._*'` 필수. 단 P7은 단일 파일 scp + 프론트 dist만이라 영향 적음.
- ⚠️ 미push(§3). 서버 git 없음 → 배포는 scp(이미 완료), push는 GitHub 백업 목적.
- 스케줄러 prod 등록(변동 없음): sync_coupang_products(05:30)·sync_coupang_returns(05:45)·sync_coupang_settlement(05:50) 전부 enabled. P7은 조회 전용이라 스케줄러 무관.
- 미커밋 워킹트리: `M CLAUDE.md`(세션 전부터, P7 무관) + 다수 `?? .claude/memory/HANDOFF_*`·`?? docs/TRACKS.md·references/*`(이전 페이즈 미추적 문서 — 정리 필요 시 별도).

## 6. 다음에 할 작업 (미완료)
- [ ] **(우선 확인) origin/main push 여부** — 사용자 결정. `git push origin main`(a2bbd3a 외 P4 8abb607·returns 1c70c3d도 함께 올라감 — 이미 main에 있으나 origin 상태 확인 필요).
- [ ] **P3 로켓그로스 도메인** — 상품조회=사이즈(보관비 원가)·로켓창고 재고·RG주문. clients/coupang/rocketgrowth.py(9 SA) 신규. ⚠️ 단 RG 실데이터 0(사이즈 null·재고0·판매0) — RG 활성화 시점에. /browse 명세수집 → Opus 권장.
- [ ] **P5 쿠폰/캐시백** (할인 비용) — coupons.py(21 SA).
- [ ] **P6 물류센터·카테고리·브랜드·CS** (보조).
- [ ] **쓰기 페이즈** — products.py 17 stub·returns/exchanges 쓰기 stub 채우기(dry_run 안전장치 D-1). ⚠️ 라이브 스토어 변경이라 신중.
- [ ] (별도) product_sync 커버리지 개선 → 조망 결합 토대 확대(원가·등록수수료율 매칭 늘리기).
- [ ] (별도) 조망 drill-down·기존 페이지(상품/정산)에 새 컬럼 붙이기 등 프론트 보강.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-p7-overview_20260603.md 읽고 이어서 작업해줘.
```
