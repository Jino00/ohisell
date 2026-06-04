# 세션 인수인계: ohisell-coupang-full-integration
> 저장일시: 2026-06-02 18:40
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 이 세션은 신규 메가 프로젝트 "기획·설계" 세션. 코드 구현은 아직 0. 트랙 파일이 진짜 진실 원천.

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && .venv/bin/python3 -m uvicorn app.main:app --reload` (venv: `backend/.venv/bin/python3`)
- 프론트 실행: `cd frontend && npm run dev` / 빌드: `npm run build`
- 프로덕션 URL: https://sellc.ohitech.co.kr
- 서버 SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`
- 최신 커밋: 433d99a / DB head: 79c5bf56a7eb
- 주요 환경변수(이름만): COUPANG_ACCESS_KEY, COUPANG_SECRET_KEY, COUPANG_WING1_VENDOR_ID 등 채널별 *_VENDOR_ID, NAVER_*, CAFE24_*
- 웹브라우징: 쿠팡 개발자 포털은 Cloudflare 차단 → `/browse` **headed 모드**로만 접근 가능 (`$B --headed goto ...`). headless는 403.

## 2. 이번 세션 완료 목록
- ✅ `claude-progress.txt` 갱신: 날짜 2026-06-02, DB head 79c5bf56a7eb, 최신커밋 433d99a 교정 + 맨 위 ★메가 프로젝트 포인터 추가
- ✅ 외부 리서치(공식 자료): **쿠팡 셀러 광고(윙/로켓그로스 광고)는 공식 Open API 없음** 확정 → 광고비는 XLSX 업로드 유지
- ✅ 데스크톱 광고파일 분석: `A01564720_pa_daily_keyword_20260526_20260601.xlsx` = 윙(3P) 1주, 광고비 76,751원, 키워드 32개, 검색이 광고비 68%. 기존 파서가 `pa_daily_keyword` 포맷 이미 지원(ad_costs.py 386~). 로켓그로스(2P)는 판매 0이라 데이터 없음(정상)
- ✅ `/browse` headed로 쿠팡 Open API 포털 전수 크롤링 → **100개 엔드포인트** 전수 검증(섹션별: 상품22·쿠폰21·배송환불12·RG9·물류8·반품7·카테고리6·CS6·교환4·브랜드3·정산2). 1차 누락분(`송장업데이트 처리`) 발견·교정
- ✅ 상품 사이즈 API 제공 **문서 증거로 확정**: 로켓그로스 상품조회 `GET /v2/providers/seller_api/.../seller-products/{sellerProductId}` 응답에 width/length/height(mm)·weight/netWeight(g)
- ✅ 신규 레퍼런스: `docs/references/01_coupang_api_full_catalog.md` (100개 전수 카탈로그 + 현재사용2개 + 우선순위)
- ✅ 신규 트랙 생성: `docs/tracks/active/track_coupang-full-integration.md` (D-1~D-7, 아키텍처, 100개 커버리지, P1~P7) + `docs/TRACKS.md` 인덱스
- ✅ `CLAUDE.md`(프로젝트)에 "진행 중 메가 프로젝트" 섹션 추가 (매 세션 자동 로드 보장)
- ✅ 기억 시스템: `active-track-coupang-integration.md`, `no-ad-strategy-recommendations.md` + MEMORY.md 인덱스
- ⚠️ 코드 구현 0줄. P0(설계·검증)까지만 완료. 미커밋 변경: CLAUDE.md, claude-progress.txt (+ 신규 docs/memory 파일들)

## 3. 확정된 결정사항 (번복 금지 — 트랙 D-1~D-7 원본)
- **D-1**: 쿠팡 API 100개 전 기능(읽기+쓰기, 상품등록 포함) 연결. 쓰기는 안전장치(dry_run+명시확인) 내장
- **D-2**: 최종 목적 = 종합 조망(Command Center). 3축(회계/광고사실/상품현황) 모두 "옵션ID 결합 엔진"에서 파생. 사이드바 새 메뉴 🎯 종합 조망
- **D-3**: 시스템은 사실/지표 정리만 — **전략 추천 안 함**(끊어라/늘려라/밀어라 금지). 해석은 Jino 몫. (원문: "너가 그런 일을 할 수 있는 능력은 없잖아?")
- **D-4**: 광고비는 XLSX 업로드 (공식 셀러광고 API 없음). 정산/주문/상품/재고는 Open API
- **D-5**: 상품 사이즈 API 제공 확정 (위 경로)
- **D-6**: **백엔드 우선 → 프론트 나중**. (원문: "frontend에 구조화시키기전에 먼저 backend에서 구축하자는거지?" → 그렇다)
- **D-7**: 전부 연결하되 구현 우선순위는 회계·상품·재고부터. 무관한 것(도서캐시백 등)은 모듈에 자리만

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-full-integration.md` | ★단일 진실 원천. 결정·아키텍처·100개 커버리지·페이즈. **먼저 읽기** |
| `docs/references/01_coupang_api_full_catalog.md` | 쿠팡 API 100개 전수 카탈로그 (이름·섹션 레벨) |
| `docs/TRACKS.md` | 트랙 인덱스 |
| `CLAUDE.md` | "진행 중 메가 프로젝트" 섹션 (자동 로드) |
| `backend/app/clients/coupang.py` | 현재 단일 파일(177줄, HMAC+fetch_orders). → `clients/coupang/` 패키지로 승격 예정 |
| `backend/app/routers/ad_costs.py` | 쿠팡 광고 XLSX 업로드 파서 (pa_daily_keyword 지원, 386~) |
| `backend/app/services/profit_calculator.py` | 이익 계산 엔진 (재활용 대상) |

## 5. 알려진 이슈 / 주의사항
- **쿠팡 포털 = Cloudflare 차단**: 명세 수집은 반드시 `$B --headed goto`. headless 403. 명령마다 `--headed` 붙이고, 데몬 꼬이면 `$B disconnect` 후 재시작
- **아키텍처 (변형 금지)**: Layer1 `clients/coupang/*.py`(SA 100, 도메인별) → Layer2 `services/coupang/*.py`(Harness, 쓰기 dry_run 기본) → Layer3 routers/pages. SA간 직접호출 금지(원칙18)
- **쓰기 API 위험**: 상품삭제/주문취소/쿠폰파기/가격변경 등 라이브 스토어 변경 → Harness에서 명시 확인 없으면 거부
- **명세 미수집**: 100개 중 path/파라미터/응답 스키마 확정된 건 극소수(revenue-history, ordersheets, RG 상품조회). 나머지는 페이즈별 just-in-time 수집 필요
- **광고 누락 기존 이슈**(별개): calculate_daily_trend가 위탁채널 광고비 미집계 → 순이익 과대. 이 트랙과 별개로 존재
- 미커밋 상태 — 필요 시 git commit (Jino 요청 시에만)

## 6. 다음에 할 작업 (미완료) — P1부터
- [ ] **P1 상품 도메인(22개)**: ① `/browse` headed로 상품 API 정확 명세 수집 ② `clients/coupang/_base.py`+`products.py` 구축(단일파일→패키지 승격, fetch_orders 호환 유지) ③ `services/coupang/product_sync.py` + 상품마스터 DB 스키마(옵션ID↔상품) ④ /codex review → 라이브 검증
- [ ] P2 반품/취소/교환 (순매출 정확화)
- [ ] P3 로켓그로스 (사이즈·창고재고·RG주문)
- [ ] P4 정산(지급내역 신규) → P5 쿠폰 → P6 물류/카테고리/브랜드/CS
- [ ] P7 종합 조망 프론트 결합 (옵션ID 결합 엔진 + 3축 뷰)
- 참고: P1 명세수집은 Opus 권장(외부 API 정확도), 구현은 Sonnet 가능

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-full-integration_20260602.md 읽고 이어서 작업해줘
```
