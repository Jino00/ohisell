# 세션 인수인계: ohisell-coupang-ad-option
> 저장일시: 2026-06-02 21:02
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 메가 프로젝트 "쿠팡 API 전기능 연결" 트랙의 **(A) 광고 옵션ID 보존 완료 + prod 배포·실증** 세션. 트랙 파일이 진짜 진실 원천.

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && .venv/bin/python3 -m uvicorn app.main:app --reload` (로컬 venv=Python 3.14)
- 프론트: `cd frontend && npm run dev`
- 프로덕션 URL: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`
- **서버 환경**: Python **3.10**, DB=SQLite `backend/ohisell.db`(51MB, DATABASE_URL=sqlite:///./ohisell.db). **서버에 git 없음 → 배포=파일복사**(scp). **서버 uvicorn 포트=8001**(8000 아님).
- 최신 커밋(main): **9a45eee** (A) ← a4afac7(P1) ← 433d99a. origin/main push 완료.
- DB head: 로컬 **9b2e4f6a7c1d**, **prod도 9b2e4f6a7c1d**(이번에 적용).
- 환경변수(이름만): COUPANG_WING1/WING2/RG1/RG2 각 _VENDOR_ID/_ACCESS_KEY/_SECRET_KEY, NAVER_*, CAFE24_*
- ⚠️ 쿠팡 Open API는 IP 화이트리스트 — 로컬 전부 403, 검증은 서버 SSH. **단 광고 XLSX 파싱은 API 무관·로컬 검증 가능**(이번 작업이 그 케이스).

## 2. 이번 세션 완료 목록
- ✅ 트랙 **D-9** 기록: 광고측 옵션ID 보존 설계(실측 XLSX 기반, 기존 롤업 무변경 + 옵션 그레인 신설)
- ✅ `backend/app/models.py`: **CoupangAdOptionDaily** 모델 추가 (옵션 그레인, UNIQUE 5키: report_date·vendor_id·sell_type·ad_option_id·conv_option_id)
- ✅ `backend/alembic/versions/9b2e4f6a7c1d_add_coupang_ad_option_daily.py` 신규 (테이블+인덱스 3개)
- ✅ `backend/app/routers/ad_costs.py` 파서 확장: `_detect_xlsx_format`에 [8]광고집행 옵션ID·[10]전환매출 옵션ID 키워드 감지, 옵션 단위 집계, **delete-then-insert**(멱등+stale방지). 공용 헬퍼 `_cell_int/_cell_dec/_norm_opt`로 리팩토링. 기존 coupang_ad_report(롤업)는 무변경.
- ✅ codex review 게이트 **PASS** (2라운드): [P2] 재업로드 stale행 지적 → delete-then-insert로 합의·수정 → 2R 확인
- ✅ 로컬 완결검증(TestClient+임시DB): 196옵션 적재, 집계 76751 원본 정확일치, 광고⨝상품 조인 금액흐름(31982/16900), 멱등성·stale제거 PASS
- ✅ main 머지(9a45eee)+push, feature 브랜치(feat/coupang-ad-option-daily) 정리
- ✅ **prod 배포**: DB백업(`ohisell.db.bak-20260602-adopt`) → models·ad_costs·마이그2개 scp → `alembic upgrade head`(79c5bf56a7eb→8a1f2c3d4e5b→9b2e4f6a7c1d) → pm2 재기동 → status HTTP200·도메인200
- ✅ **prod 옵션 실적재 라이브 실증**: 실제 XLSX를 prod 도메인 업로드(HTTP200) → coupang_ad_option_daily **196행·75옵션·광고비76751·전환115220** 로컬과 100%일치
- ✅ 트랙/progress 갱신

## 3. 확정된 결정사항 (번복 금지)
- 광고측 옵션ID는 **두 컬럼 모두 보존**(ad_option_id=[8] 비용·노출 귀속, conv_option_id=[10] 매출·주문 귀속). keyword 리포트에선 보통 같지만 14일 전환윈도우로 갈릴 수 있음.
- 옵션 저장은 **delete-then-insert**(업로드 커버 vendor_id+등장날짜 범위 삭제 후 전량 삽입) — 멱등성 + stale 차단. 기존 coupang_ad_report는 손대지 않음.
- 옵션 단위 집계 필수((날짜,옵션) 중복 최대 9행 — 캠페인/지면/키워드로 쪼개짐).
- **3자 조인 구조**: coupang_ad_option_daily.ad_option_id ⨝ coupang_product_item.vendor_item_id ⨝ Order.platform_product_id. 광고축·상품축 모두 옵션ID로 섰음.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-full-integration.md` | ★단일 진실 원천. D-1~D-9, §8 다음액션. **먼저 읽기** |
| `backend/app/models.py` | CoupangAdOptionDaily(A) · CoupangProductItem(P1) · CoupangAdReport(기존 롤업) |
| `backend/app/routers/ad_costs.py` | 광고 XLSX 파서. 옵션 보존 로직(`has_opt`/`_norm_opt`/delete-then-insert, 590~) |
| `backend/app/clients/coupang/products.py` | 상품 SA(읽기5·stub17) — P1, **prod 미배포** |
| `backend/app/services/coupang/product_sync.py` | 상품 동기화 Harness — P1, **prod 미배포** |
| `docs/references/02_coupang_product_api_specs.md` | 상품 읽기 5 API 명세 |
| `docs/references/01_coupang_api_full_catalog.md` | 100개 전수 카탈로그 |

## 5. 알려진 이슈 / 주의사항
- **prod에서 광고⨝상품 조인은 아직 0건**: 광고축은 채워졌으나 **상품축(P1 코드)이 prod 미배포**라 coupang_product_item이 빈 테이블. 3자 조인이 prod에서 실제 결과를 내려면 **(B)에서 P1 sync를 prod 배포**해야 함. 로컬은 조인까지 검증됨.
- **prod는 P1 코드 전체 미배포**: clients/coupang 패키지·product_sync 없음(서버엔 옛 단일 app/clients/coupang.py만). prod DB엔 coupang_product_item 빈 테이블만 존재(마이그레이션 체인으로 생성). ad_costs/models는 P1 패키지에 의존 안 해서 (A) 단독 동작 OK.
- 서버 배포는 **scp 단일파일**(git pull 아님). 서버 uvicorn **포트 8001**. tar 전송 시 `--exclude='*__pycache__*'`(3.14 pyc 섞이면 alembic null-bytes).
- 미커밋: claude-progress.txt·docs(트랙/레퍼런스)는 프로젝트 메모리라 의도적 미커밋(기존 패턴). 코드만 main 착지.
- prod DB 백업본: 서버 `/home/ubuntu/ohisell/backend/ohisell.db.bak-20260602-adopt`(배포 전 51MB).

## 6. 다음에 할 작업 (미완료) — 트랙 §8
- [ ] **(B) product_sync 소비자 연결 + P1 prod 배포**: 스케줄러/엔드포인트에 product_sync 붙이고, 그때 P1 코드(clients/coupang 패키지·services/coupang) prod 파일복사 + 실sync. 이게 끝나야 prod에서 광고⨝상품 3자 조인이 실제로 채워짐.
- [ ] (C) P2 반품/취소/교환 (순매출 정확화) — clients/coupang/returns.py·exchanges.py 신규.
- [ ] P7 종합 조망 화면(소비자) — 3자 조인 엔진을 실제로 보여주는 UI. 당겨올 수도 있음.
- 구현은 Sonnet 가능. 외부 API 명세 정확도 필요 시 Opus.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-ad-option_20260602.md 읽고 이어서 작업해줘
```
