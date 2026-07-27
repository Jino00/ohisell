# 세션 인수인계: 상품 연관맵 (Product Connection Map) — S0 구조확정·트랙생성
> 저장일시: 2026-07-03 10:22
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 실행: 백엔드 FastAPI(`backend/`, `uvicorn`), 프론트 React+Vite+TS(`frontend/`)
- DB(dev): `backend/ohisell.db` (SQLite, `DATABASE_URL=sqlite:///./ohisell.db`)
- 브랜치: `feat/ohitech-ad-cost` (이번 트랙과 무관 — S1 시작 시 새 브랜치 `feat/product-connection-map` 권장)
- 주요 환경변수(키 이름만): `COUPANG_WING1_VENDOR_ID`, `COUPANG_WING2_VENDOR_ID`, `COUPANG_RG1_VENDOR_ID`, `COUPANG_RG2_VENDOR_ID` (backend/.env)
- 소스 엑셀: `/Users/jino/Library/CloudStorage/GoogleDrive-jino.kim@ohitech.co.kr/.shortcut-targets-by-id/1-sXdQoAGFvN14IET1A7DqeUoKD66GJ2K/Ohi/15. 기획/상품 리스트/ohisell_mapping_template.xlsx` (899행, 시트 2개: `원가 매핑`, `채널 목록`)

## 2. 이번 세션 완료 목록
- ✅ 요구 파악: sellc.ohitech.co.kr에서 자사몰·스마트스토어·쿠팡(ofix/ohitech) 제품을 **옵션 단위**로 하나로 묶어 관리 + 통합 손익까지(레벨2).
- ✅ 엑셀 마스터 시트 검토(`ohisell_mapping_template.xlsx`): 1행=통합옵션, 채널별 옵션ID 컬럼. 커버리지·중복·충돌 실측.
- ✅ 채널 축 라이브 확정(D-2): Wing 로그인 스크린샷("오픽스 A01564720") + `backend/.env` + `backend/app/seed.py` 교차.
- ✅ 레벨2 데이터 준비도 라이브 검증: 네이버 상품번호=옵션(`Order.platform_product_id`), 엑셀↔주문 일치 네이버 294/329(89%)·cafe24 85/86(99%). 주문↔마스터 연결 cafe24 100%·쿠팡·네이버 95%.
- ✅ 기존 자산 발견: `product_master` 894행(internal_sku `OHI-xxxx` 스파인 이미 존재)·`product_channel_mapping` 2,610행.
- ✅ 구조 승인(Agent/Harness/SA) — Jino "그래".
- ✅ 트랙 파일 생성: `docs/tracks/active/track_product-connection-map.md` (D-1~D-4·라이브실측·구조·체크리스트 S0~S6).
- ✅ `docs/TRACKS.md` Active 등록 (1/6).

## 3. 확정된 결정사항 (번복 금지 — 트랙 D-1~D-4)
- **D-1**: grain = **옵션 단위** (엑셀 1행 = 통합옵션 1개). 사용자 원문 "옵션까지 내려가야해."
- **D-2**: 채널 축 = (회사 ofix/ohitech) × (판매형태 3P/RG/1P) + 네이버 + 자사몰. **대조표(라이브 확정)**:
  - 오픽스 = `COUPANG_WING1`(3P) + `COUPANG_RG1`(RG) = vendor **A01564720**
  - 오하이테크 = `COUPANG_WING2`(3P) + `COUPANG_RG2`(RG) + `COUPANG_ROCKET`(1P) = vendor **A01029796**
  - ★vendor_id는 회사별로 WING·RG **공유** → vendor_id만으로 3P/RG 구분 불가, sell_type으로 구분. 엑셀이 판매자/로켓그로스 컬럼으로 이미 분리.
- **D-3**: 통합옵션마다 **내부코드 스파인**(`OHI-xxxx`), 상품명은 표시용. **이미 구현됨**(product_master.internal_sku).
- **D-4**: 목표 = **레벨2(통합 손익 조망)**. 네이버 옵션 매출 신규수집 불필요(기존 데이터 조인).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_product-connection-map.md` | ★단일 진실 원천(트랙) |
| `docs/TRACKS.md` | 트랙 인덱스 (Active 1/6) |
| `backend/app/models.py:64-122` | ProductMaster(64) + ProductChannelMapping(93) — 재사용 대상 |
| `backend/app/models.py:221-269` | Order(platform_product_id=채널옵션ID, product_id=마스터FK) |
| `backend/app/models.py:1128-1186` | Naver 정산 모델(product_id=상품번호) |
| `backend/app/seed.py:5-60` | 채널 정의(WING1/2·RG1/2·ROCKET·company) |
| `backend/app/routers/products.py` | 기존 products 라우터(upload / upload-by-name / download) |
| `frontend/src/pages/Products.tsx` | 기존 원가표 화면(연관맵 전용 아님) |

## 5. 알려진 이슈 / 주의사항
- **무결성 문제(화면 C가 잡아야 함)**: 채널옵션ID 중복=매핑 충돌(cafe24 20·스스 22·로켓배송 2), 상품명 중복 5, 미매핑 주문(네이버 35·cafe24 1), 미연결 주문 5%, **오픽스(WING1/RG1) 매핑 결손(각 20건뿐 — 엑셀 소스 자체 부족)**.
- **돈 안전**: 수수료는 실측 엔진 재사용(쿠팡 7.8% 실측·RG정산·네이버 case). 엑셀 '채널 목록' 시트 수수료율(쿠팡 10.8%)은 참고용 — **돈 계산에 쓰면 회귀**.
- 매핑 충돌(1채널옵션→2마스터)은 매출·원가 이중귀속 → 적재 시 유일성 검증 필수.
- 원칙22: "된다"는 라이브 증거로만. S1~S6 각 완료 시 prod 라이브 self-verify.

## 6. 다음에 할 작업 (미완료)
- [ ] **착수 전 Jino 결정 2건**: ① 기존 `upload-by-name` 라우터 **대체** vs 병행신설(추천: 대체·흡수) ② 매핑에 `sell_type`(3P/RG/1P) 컬럼 보강 여부(추천: 둔다). *미응답 시 추천대로 진행 승인 가능.*
- [ ] S1 매핑 적재 Harness: 엑셀 파서(헤더명 동적매핑) + 채널 라벨 리졸버(D-2) + 매핑 upsert(멱등) + 무결성 검사(순수함수 리포트)
- [ ] S2 매출 조인/백필 + 커버리지 리포트
- [ ] S3 통합 손익 조망 Harness(SA 4종: 매출·원가·수수료·광고 → 조합)
- [ ] S4 화면 C 탭1 연관맵 관리 UI
- [ ] S5 화면 C 탭2 통합 손익 UI
- [ ] S6 오픽스 매핑 결손 보강 + prod 배포·라이브 self-verify
- [ ] S1 상세계획 후 /plan-eng-review, 구현은 /model sonnet

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-product-connection-map-S0_20260703.md 읽고 이어서 작업해줘
```
