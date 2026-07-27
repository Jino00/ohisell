# 세션 인수인계: 쿠팡 로켓배송(1P) 트랙 — S1 정찰 완료
> 저장일시: 2026-06-17
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: FastAPI `backend/`. 테스트 = `cd backend && .venv/bin/python -m pytest -q` (★venv는 `backend/.venv`, homebrew python엔 의존성 없음). 로컬 DB는 경제 테이블 비어 검증은 prod 필수.
- prod: `ssh sellc.ohitech.co.kr`(User=ubuntu), DB `/home/ubuntu/ohisell/backend/ohisell.db`(SQLite), PM2 `ohisell-backend`(:8001). git 아님 → scp + `pm2 restart`.
- **정찰 환경(이번 세션 핵심)**: 헤드풀 Chrome(CDP 9223) — `backend/.venv/bin/python3 tools/rocket_supplier_recon.py chrome`로 띄우고 supplier.coupang.com 로그인. 이번 세션 Chrome(PID 33631)은 **로그인 상태로 살아있음**(닫혔으면 재실행+재로그인). 프로필 `~/.ohisell_supplier_chrome`.
- git: 이번 세션 커밋 2개 = `5a7163f`(S1 정찰) + `b1d9f88`(S2 사전확인). **미push**(직전 origin/main=52693a7).

## 2. 이번 세션 완료 목록
- ✅ **세션 진입 판단**: HANDOFF(RG 발송관제 S8)는 D-17 데이터누적대기 → 코딩0. 활성트랙 3개라 원칙20 따라 Jino에게 트랙 확인 → **"로켓배송 1P S1 정찰" 선택**.
- ✅ **S1 정찰 — supplier.coupang.com 발주/납품/정산 3단계 라이브 실측**(추측0, 원칙22):
  - **`docs/references/20_coupang_rocket_1p_recon.md`** 작성(§1~7 + §6-1 S2 사전확인). 정찰 보고 정본.
  - **`docs/references/data/20_rocket_1p_settlement_dom_sample.json`** 정산 DOM 증거(계산서 10건).
  - **`tools/rocket_supplier_recon.py`** 코드화: 4명령 `chrome`/`capture`(원시 CDP Network 도청)/`dom`(SSR DOM 추출)/`fetch`(page-context fetch). dom·fetch 라이브 self-verify 통과.
- ✅ **S2 사전확인 6건 중 5건 해결**(전부 page-context fetch, 추가 클릭 0). §3 아래.
- ✅ 트랙(`docs/tracks/active/track_coupang-rocket-1p.md` — D-9 추가·체크리스트 S1 [x]·다음액션 갱신) + `docs/TRACKS.md` + `claude-progress.txt` 갱신. 커밋·미push.

## 3. 확정된 결정사항 (D-9, 번복 금지 — ref20 정본)
- **3단계 데이터 소스**:
  - ①발주+②납품 = **`GET /po-web/app/purchase-order/list`** JSON 1개. row당 `sumOfOrderAmount`(발주=D-3매출)·`sumOfReceivingAmount`(납품)·`sumOfVendorConfirmedAmount`. grain=발주 `purchaseOrderSeq`. 응답 envelope `{success,message,body:{body:[...], currentPage,lastPageNumber,totalRecordSize,pageSize}}`.
  - ③정산 = **`GET /scm/settlement/general/purchase/account`** 폼-GET SSR HTML(JSON 아님→DOM/HTML 파싱). grain=계산서번호(=vendorPaymentInfoSeq). 컬럼: 공급가액(net)·부가가치세·지급예정금액(gross=공급가+VAT)·작성/지급일자·세금계산서확정일·1/2차지급액.
- **S2 사전확인 5/6 (ref20 §6-1)**:
  1. `searchDateType` = {`WAREHOUSING_PLAN_DATE`(입고예정일), **발주일**} → **매출은 발주일 기준 조회**(발주일 enum 코드값만 S2 첫 캡처 시 확정).
  2. 페이지네이션 = `page=1..lastPageNumber` 루프, **pageSize 고정 50**(size 파라미터 무시).
  3. **발주/입고금액 = VAT 포함(gross)** = 정산 지급예정금액(4/5 정확일치: 30025494/30015106/30015105/29991328). 정산 공급가액=net.
  4. **계산서↔PO 매핑 = list 내장**: PO.`vendorPaymentList[].vendorPaymentInfoSeq` = 계산서번호. **1계산서↔NPO(묶음) AND 1PO↔N계산서(부분정산)**. ⚠부분정산 30003353(단일PO 입고2,375,980 vs 계산서gross1,211,980).
  5. (선택·미해결) SKU 단위 금액 = 발주상세 `/scm/purchase/order/get/{seq}` SSR DOM. 머니수학(D-3/D-4)은 PO grain 충분 → **S2 기본범위 제외**.
  6. size 고정 50.
- **★수집방법 확정** = 브라우저 **page-context `fetch(path,{credentials:"include"})`** 전체 JSON(쿠키 자동·잘림 없음). XHR 캡처보다 우월. 정산만 DOM.
- 인증=쿠키 + **Akamai 봇방어(POST /C_A8aP/… sensor) → 헤드풀 CDP 페처 필수(D-1 확인)**. 호스트=supplier.coupang.com 단일.
- (기존 D-1~D-8 불변): 매출=발주, 순이익=발주−원가(product_master)−광고(로켓배송). 채널=COUPANG_ROCKET(seed id 5). 아키텍처=clients/coupang→services/coupang→routers. **시스템은 사실/지표만, 전략추천 금지**.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-rocket-1p.md` | ★트랙 정본(D-1~D-9·체크리스트·다음액션). 단일 진실 원천 |
| `docs/references/20_coupang_rocket_1p_recon.md` | ★S1 정찰 보고(엔드포인트·필드·쿼리·VAT검산·수집방법) |
| `docs/references/data/20_rocket_1p_settlement_dom_sample.json` | 정산 DOM 증거(계산서 10건) |
| `tools/rocket_supplier_recon.py` | ★정찰/수집 도구. `chrome\|capture\|dom [urlkw]\|fetch <path>` |
| `tools/wing_browser_fetcher.py` | (참고) Wing 헤드풀 CDP 페처 — S3 페처 패턴 원본 |
| `backend/app/clients/coupang/` | 기존 쿠팡 SA들(rocket_supplier.py 신규 추가 예정) |

## 5. 알려진 이슈 / 주의사항
- **정찰 함정 3종(ref20 §7, 도구에 코드화됨)**: ①Playwright `connect_over_cdp`는 기존(자기가 안 띄운)페이지 response 못받음→원시 CDP ws `Network.responseReceived` 직접도청 ②Chrome 디버깅 ws는 Origin헤더 403→`suppress_origin=True` ③최상위 navigation 문서는 `getResponseBody` 빈본문→SSR은 `Runtime.evaluate` DOM.
- **fetch가 수집의 정답**: capture(XHR 도청)는 8000자 잘림·일부 본문 증발(getResponseBody race) 있었음. S2는 page-context fetch로.
- Wing 쿠키/Akamai 단명 — supplier도 동일 예상. S3 페처는 wing_browser_fetcher.py 패턴(CDP 모드) 복제.
- 테스트는 반드시 `backend/.venv/bin/python`. prod 배포는 scp + pm2 restart(git 아님).
- 다른 활성 트랙 2개(RG 수수료회계 운영중·size_mismatch 1건 자동해제대기 / RG 발송관제 D-17 데이터대기) — 이번 세션과 무관, 건드리지 않음.

## 6. 다음에 할 작업 (미완료) — S2
- [ ] (선택) 발주일 enum 코드값 1건 확정: 발주 화면 `기간검색` 드롭다운 발주일 선택→검색 1회 → capture로 searchDateType 값 확인. (또는 fetch로 추정값 시도)
- [ ] **S2 데이터 모델**: 발주/납품 = **PO grain 테이블**(purchaseOrderSeq PK, sumOfOrder/Receiving/ConfirmedAmount[gross], status, center, createdAt/expectedDeliveryDate, vendorPaymentList→계산서매핑) + 정산 = **계산서 grain 테이블**(vendorPaymentInfoSeq PK, 공급가액net·VAT·지급예정gross·작성/지급일·세금계산서확정일) + **alembic**. (★DB 스키마 변경 → Opus 권장)
- [ ] **S2 수집 SA** `clients/coupang/rocket_supplier.py`: page-context fetch로 list `page=1..lastPageNumber` 루프 + 정산 SSR DOM 파서.
- [ ] (S3) 헤드풀 CDP 페처(supplier) + prod push + launchd 데몬. (S4) 종합조망 편입 Harness(매출=Σgross발주금액[발주일]−원가−광고, 발주↔정산 드리프트=vendorPaymentInfoSeq 조인·부분정산 다중성 주의). (S5) 프론트. (S6) prod self-verify+codex.
- [ ] (선택) 미push 커밋 2개(5a7163f·b1d9f88) origin/main push.
- ★S2는 새 테이블·alembic·외부API연동 → **/model opus 계획 권장**, 새 세션 추천.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-rocket-1p-S1-recon_20260617.md 읽고 이어서 작업해줘
```
