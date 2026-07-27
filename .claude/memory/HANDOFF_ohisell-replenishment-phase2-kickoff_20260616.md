# 세션 인수인계: 원가 일괄등록 + RG 발송관제 Phase 2 착수
> 저장일시: 2026-06-16 16:00
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: FastAPI `backend/`, 로컬 DB `backend/ohisell.db`(SQLite, **핵심 경제테이블 비어있음 → 검증은 prod 필수**)
- prod: `ssh sellc.ohitech.co.kr`(User=ubuntu), DB `/home/ubuntu/ohisell/backend/ohisell.db`, PM2 `ohisell-backend`(:8001), 프론트 nginx, git 아님→scp/rsync 배포
- prod DB 조회: `ssh sellc.ohitech.co.kr 'sqlite3 /home/ubuntu/ohisell/backend/ohisell.db "<SQL>"'`
- prod 엔드포인트 조회: `ssh sellc.ohitech.co.kr 'curl -s http://localhost:8001/api/...'`

## 2. 이번 세션 완료 목록
- ✅ **BEP RoAS 정식기능(D-17) 설계 확정** — 트랙 `track_coupang-full-integration.md`에 D-17(a~f) 기록. 참조표 우리카탈로그우선·광고축 컬럼·2P/3P 둘다·할인가메인+정가툴팁·드리프트감지(표vs정산실측)·원가미등록 비움. **코드는 아직 0줄**(다음에 Opus 구조설계→구현).
- ✅ **쿠팡 원가 미등록 옵션 일괄 등록 (prod DB 데이터, 코드변경 0)**: 백업 `ohisell.db.bak-costreg-20260616-155053`. **마스터 13개 + 옵션 매핑 46개** 생성(전부 channel_id=1 WING1, is_active=1, 896 선례). 원가 다리(`_cost_master`)로 46개 전부 해소 라이브 검증. 원가미등록 실판매옵션 39→1(청바지만, skip). **전부 VAT포함 저장.**
  - A 강화유리아이폰17Pro 3,400×2 · B EZ툴프라이버시강화유리 5,000×7 · C+D 전면3D TPU무광 2,400×26(D 저반사 동일상품 합침) · E 버디필름골프 2,200(10p)×2/4,400(2개입)×1 · H 종이질감 4,500×1 · F 맥세이프카드지갑 **4,070**×1 · G 도어락문캅스 6옵션(지문방지 1,720/3,440/5,160·사생활 2,600/5,200/7,800 = 세트수 배수)
  - ⚠️ **F 카드지갑 4,070 미확정 가능성**: 네이버에서 받은 "3,700 VAT미포함"을 ×1.1 환산한 값. Jino "F: VAT 포함이야"로 해석했으나, 만약 3,700이 VAT포함이었으면 4,070→3,700 수정 필요(다음 세션 확인).
- ✅ **네이버 맥세이프 카드지갑 BEP RoAS 산출(미팅용, 실측 데이터)**: 판매가 16,900, 원가 3,700(VAT미포함), 네이버수수료 실측 4.07%(결제2.73%+매출연동1.34% 블렌디드), 배송비 한진 1,900. **BEP ≈ 186%(공급가)/165%(현금)**. RoAS 150%·100개 판매 시 손해 **현금 약 −10만원/공급가 약 −22만원**. 네이버 SA 실제 RoAS는 계정전체만 가능(상품귀속 불가)=205%(5/20~6/15).
- ✅ **RG 발송관제 Phase 2 착수 — 3축 조사 완료**: 레퍼런스 `docs/references/19_rg-replenishment-forecasting-research.md` 신규 작성(API지원·현시스템구조·외부예측연구 종합). 트랙 `track_coupang-rg-replenishment.md`에 **D-10/D-11 + Phase 2 로드맵** 기록. TRACKS.md 갱신.

## 3. 확정된 결정사항
- **D-17 (BEP RoAS 기능, full-integration 트랙)**: 위 §2 6개 결정. 계산정본=메모리 `bep-roas-calculation-structure.md`.
- **D-10 (발송관제, 예측엔진 교체)**: 단순평균 → Croston계열 **SBA/TSB**(Nixtla `statsforecast`). SKU ADI/CV² 분류. 목표재고 newsvendor 분위수. (z·σ 안전재고는 간헐수요에 부적합.)
- **D-11 (발송관제, in-transit)**: "발송중 물량"은 공식API 없음 → **Wing 내부 API `rfm-inbound` 사용 확정**(D-14 전역 "공식만" 재검토). 근거=Wing 세션 페처가 이미 RG정산을 같은방식 수집 중(검증된 위험). Jino "그래. 쓰자."
- 두 회계엔진 순이익 정의는 직전 세션에 이미 통일됨(VAT 둘다 미차감·한진 둘다 차감, 커밋 ab1ec81).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/references/19_rg-replenishment-forecasting-research.md` | ★발송관제 Phase 2 종합조사(API·구조·논문·로드맵) |
| `docs/tracks/active/track_coupang-rg-replenishment.md` | ★발송관제 트랙(Phase 2 D-10/D-11/로드맵) |
| `docs/tracks/active/track_coupang-full-integration.md` | BEP D-17 기록 (활성 트랙) |
| `backend/app/services/coupang/rg_replenishment.py` | 발송관제 Harness (S5) |
| `backend/app/services/coupang/sales_velocity_estimator.py` | 판매속도 SA (S3) — SBA 교체 대상 |
| `backend/app/services/coupang/lead_time_estimator.py` | 리드타임 SA (S2) |
| `backend/app/services/coupang/replenishment_calc.py` | 역산 SA (S4, target_days=7) |
| `backend/app/services/coupang/intelligence.py` | 종합조망 결합엔진 + `_cost_master` 원가다리 |
| `tools/wing_browser_fetcher.py` | Wing 내부 API 페처(in-transit rfm-inbound 추가 대상) |
| `backend/app/models.py` | coupang_rg_inventory·coupang_rg_inbound·coupang_rg_order_item·product_master·product_channel_mapping |

## 5. 알려진 이슈 / 주의사항
- **F 카드지갑 원가 4,070 재확인 필요**(VAT 해석, §2 참조).
- 원가 등록은 prod 데이터만 변경(코드 무관). 원복=신규 마스터13(id≥897)+매핑46 삭제 또는 백업 복원.
- **발송관제 prod 현실**: 855옵션 중 **98.6% insufficient_data**. 원인 ① trust_days 짧음(자동회복) ② 대부분 간헐수요인데 예측이 단순평균(=D-10이 푸는 문제).
- in-transit 데이터(`coupang_rg_inbound`)는 적재구조 있으나 6/5이 마지막 동기화·조망 미연동(D-11이 배선).
- 원칙22: "됐다"는 prod 라이브 증거로만. statsforecast 라이선스는 채택 전 repo LICENSE 직접 확인.
- 원칙: 코딩 전 구조 확정→Jino 승인→Opus계획→Sonnet구현→codex. Phase 2는 새 Harness/SA+외부API+ML → **Opus 권장**.

## 6. 다음에 할 작업 (미완료)
- [ ] **(우선) RG 발송관제 Phase 2 — /model opus 후 구조설계(Agent/Harness/SA 도표)**: 신규 SA `demand_classifier`(ADI/CV²)·`sba_forecaster`(statsforecast), 기존 velocity/lead_time/calc 관계 재배치, in-transit 수집 SA + wing_browser_fetcher rfm-inbound 배선, 화면 "발송중/도착예정" 컬럼. → Jino 승인 → 계획서 → Sonnet 구현 → codex. 로드맵 P0→P5(ref 19 §4).
- [ ] BEP RoAS 기능(D-17) 구현 — 발송관제 Phase 2 후 또는 병행(별도 트랙). 참조표 2개 디지털화(우리 카탈로그 카테고리).
- [ ] F 카드지갑 원가 VAT 재확인(4,070 vs 3,700).
- [ ] (선택) git 커밋/푸시 — 이번 세션은 docs(트랙·ref19)만 변경, 코드 무변경. prod 원가는 DB 데이터.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-replenishment-phase2-kickoff_20260616.md 읽고 이어서 작업해줘
```
