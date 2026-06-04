# 세션 인수인계: ohisell-coupang-p6-meta
> 저장일시: 2026-06-04 06:03 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 메가 프로젝트 "쿠팡 API 전기능 연결" 트랙. 이 세션 = **P6 물류·카테고리·브랜드·CS prod 배포·라이브 실증 완료**. 다음 = **쓰기 페이즈** or **RG 조망 편입**. **트랙 파일이 진짜 진실 원천.**

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 로컬: `cd backend && .venv/bin/python3 -m uvicorn app.main:app --reload` (로컬 venv=Python 3.14)
- 프론트: `cd frontend && npm run dev` / 빌드 `npm run build`
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`, **포트=8001**, 프론트=nginx가 `frontend/dist` 서빙
- **서버 환경**: Python **3.10**, DB=SQLite `backend/ohisell.db`, **git 없음 → 배포=scp 파일복사**
- ⚠️ scp 전송: `COPYFILE_DISABLE=1` + `tar --exclude='._*' --exclude='*__pycache__*'`(macOS AppleDouble가 Linux alembic null-bytes 유발)
- 최신 커밋(main): **eaf7131**(P6) ← f40a387(P5) ← db28742(D-15) ← … ⚠️ **로컬 다수 origin 미푸시**(prod는 scp 배포 완료, 코드 일치)
- DB head: 로컬·prod 모두 **b1c2d3e4f5a6**(P6 coupang_inquiry)
- 환경변수(이름만): COUPANG_WING1/WING2/RG1/RG2 각 _VENDOR_ID/_ACCESS_KEY/_SECRET_KEY
- ⚠️ 쿠팡 Open API는 **IP 화이트리스트**(D-8) — 로컬 전부 403, 실sync/검증은 **서버 SSH에서만**

## 2. 이번 세션 완료 목록

### ✅ P6 물류·카테고리·브랜드·CS — SA 23개 + Harness + Router (commit eaf7131, prod 배포·라이브 실증)

**신규 SA 파일 4개:**
- `backend/app/clients/coupang/logistics.py`: 물류 8 SA (읽기 #1·#5·#6 구현 + 쓰기 #2·#3·#4·#7 stub + #8 택배사 상수 11종 COURIER_CODES)
- `backend/app/clients/coupang/category.py`: 카테고리 6 SA + RG #8·#9 본구현. ⚠️ RG #8·#9는 seller_api 게이트웨이(rg_open_api 아님 — references/05 §27 확인, codex[P2] 수정)
- `backend/app/clients/coupang/brand.py`: 브랜드 3 SA (#1 POST검색/#2 등록목록/#3 단건)
- `backend/app/clients/coupang/cs.py`: CS 6 SA (읽기 #1·#3·#6 구현 + 쓰기 #2·#4·#5 stub). code 필드 오류 감지 추가(codex[P2])

**신규 Harness:**
- `backend/app/services/coupang/cs_sync.py`: CS 동기화 (NO_ANSWER+COMPLETE 2-pass로 answered 상태 갱신 — codex[P2] stale 방지). 실패 시 api_failures 표면화.

**신규 라우터:**
- `backend/app/routers/p6_meta.py`: 14 엔드포인트 (/api/p6/logistics/* + /api/p6/categories/* + /api/p6/brands/* + /api/p6/inquiries)

**신규 DB:**
- `backend/alembic/versions/b1c2d3e4f5a6_add_coupang_p6_inquiry.py`: coupang_inquiry 테이블 (account_key·inquiry_type·inquiry_id UNIQUE, answered 인덱스)
- `backend/app/models.py`: CoupangInquiry 모델 추가

**기존 파일 수정:**
- `backend/app/clients/coupang/__init__.py`: CoupangLogisticsClient·CoupangCategoryClient·CoupangBrandClient·CoupangCsClient export 추가
- `backend/app/main.py`: p6_meta 라우터 등록
- `backend/app/routers/sync.py`: POST /api/sync/coupang-cs 추가
- `backend/app/routers/scheduler.py`: sync_coupang_cs 트리거맵 추가
- `backend/app/services/scheduler_service.py`: sync_coupang_cs_job 함수 + defaults 06:05 KST + start_scheduler 분기 추가

**codex PASS (5건 수정):**
- [P1] CoupangBaseClient 생성자 — `config` 객체 1개만 받음. `access_key=` 키워드 호출 → `CoupangXxxClient(cfg)` 패턴으로 통일 (p6_meta.py + cs_sync.py)
- [P2] CS iterator code 필드 오류 감지 (빈 data를 정상 빈페이지로 착각 방지)
- [P2] NO_ANSWER만 조회 → COMPLETE 2-pass로 answered 상태 갱신
- [P2] 스케줄러 실패 시 total_api_failures > 0이면 RuntimeError 표면화
- [P2] RG 카테고리 경로 — rg_open_api → seller_api (references/05 §27 공식 확인)

**★prod 라이브 실증(원칙22):**
- 총 72 라우트 로드 / P6 14 라우트 등록 확인
- alembic head `b1c2d3e4f5a6` prod 적용
- `/api/p6/logistics/courier-codes` → 택배사 11종 정상
- `/api/p6/inquiries` → total:0 unanswered:0 (sync 전 상태, 정상)
- DB 백업: 서버 `ohisell.db.bak-p6-20260604-*`

## 3. 확정된 결정사항 (번복 금지)
- **P6 범위**: 물류·카테고리·브랜드·CS SA 모두 읽기 구현 + 쓰기 stub. DB 적재는 CS(CoupangInquiry)만 — 물류/카테고리/브랜드는 온디맨드 조회
- **RG 카테고리(#8·#9) 경로**: `seller_api` 게이트웨이. `category-related-metas/{code}` + `display-categories?registrationType=RFM`. rg_open_api 아님(references/05 §27 확인됨)
- **CS sync 2-pass**: NO_ANSWER(신규 미답변) + COMPLETE(답변완료·상태갱신) 순서로 조회
- **CoupangBaseClient 생성자 패턴**: `CoupangXxxClient(config)` — config 객체 1개. 키워드 분리 불가
- **7/7 읽기 페이즈 완료**: P1~P7+P6 전부. 다음은 쓰기 페이즈 또는 RG 조망 편입

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-full-integration.md` | ★단일 진실원천. D-1~D-15, 페이즈 7/7, §8 다음액션. **먼저 읽기** |
| `backend/app/clients/coupang/logistics.py` | 물류 SA (읽기3+쓰기4 stub+택배사상수) |
| `backend/app/clients/coupang/category.py` | 카테고리 SA (6+RG#8·#9, seller_api 경로) |
| `backend/app/clients/coupang/brand.py` | 브랜드 SA (3) |
| `backend/app/clients/coupang/cs.py` | CS SA (읽기3+쓰기3 stub, code 오류감지) |
| `backend/app/services/coupang/cs_sync.py` | CS Harness (NO_ANSWER+COMPLETE 2-pass) |
| `backend/app/routers/p6_meta.py` | P6 조회 라우터 14 엔드포인트 |
| `backend/app/models.py` | CoupangInquiry 추가됨 |
| `backend/alembic/versions/b1c2d3e4f5a6_add_coupang_p6_inquiry.py` | P6 migration |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **로컬 커밋 다수 origin 미푸시**. prod는 scp 배포 완료(코드 일치). 푸시 필요 시 Jino 지시 후.
- ⚠️ 쿠팡 API는 **서버 IP에서만**(로컬 403). 검증/실sync는 ssh oracle_vm. 배포=scp(git 없음).
- **CS sync 첫 실행 필요**: `/api/sync/coupang-cs` POST 또는 스케줄러 06:05 KST 대기. 현재 DB = 0건(sync 전).
- **쓰기 stub 목록**: logistics #2·#3·#4·#7 / cs #2·#4·#5 / (기존) products 17개·coupons 8개·rocketgrowth 2개. 쓰기 페이즈에서 dry_run + 본문스키마 재확인(D-1).
- **D-13 카테고리율 2차 교차**: P6에서 "인프라는 준비(category.py #4 get_category_meta 구현)" but 정적 수수료율 매핑표 미작성. 쓰기 페이즈 또는 별도 D-16으로 진행.
- Failure Memory 기록됨: CoupangBaseClient 생성자 불일치, CS NO_ANSWER stale

## 6. 다음에 할 작업 (미완료)
- [ ] **쓰기 페이즈** — RG 상품생성/수정(rocketgrowth.py stub) + products 17 stub + coupons 쓰기8(coupons.py stub) + logistics 쓰기4 + CS 쓰기3. ⚠️ dry_run(D-1), product_write.py Harness, 본문스키마 구현시점 재확인(추정금지)
- [ ] **(선택) RG 조망 편입** — 로켓창고 재고축·보관비 CBM 모델을 intelligence.py/Command Center에(현재 적재만)
- [ ] **(선택) D-13 카테고리율 2차 교차** — cloud.mkt.coupang.com 정적 수수료율 표 수집 → settlement_sync _audit_category_rate() 레이어
- [ ] **(선택) CS 첫 라이브 sync 실증** — POST /api/sync/coupang-cs → DB 적재·미답변 현황 확인
- [ ] (선택) origin 푸시 — 로컬 커밋 다수(Jino 지시 시)

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-p6-meta_20260604.md 읽고 이어서 작업해줘.
```
