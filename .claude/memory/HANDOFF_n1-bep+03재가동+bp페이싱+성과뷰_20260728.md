# 세션 인수인계: N1 BEP 정합 → 03 재가동 → BP 페이싱 → VT3 개편 → 성과뷰 Phase1-2 (2026-07-28)
> 저장일시: 2026-07-28 KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 세션 모델: Fable(오케스트레이션) → 중간 Opus 전환(구현·병합). 트랙: `docs/tracks/active/track_naver-ad-optimization.md`(D-NAO-99~105 신규).

## 1. 프로젝트 위치 및 환경
- repo 루트: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (main)
- prod: `ssh -o BatchMode=yes sellc.ohitech.co.kr`, `/home/ubuntu/ohisell/backend`, DB `backend/ohisell.db`, pm2 `ohisell-backend`
- 배포는 `scripts/safe_deploy.sh`만 (D-NAO-49, CAS 가드)
- codex 게이트: 08-02 쿼터 리셋까지 사용 불가 — 오늘 배포분은 전부 적대적 Claude(Opus) 리뷰로 대체, 소급 리뷰 부채로 누적

## 2. 이번 세션 완료 목록 (전부 prod 라이브 검증 완료)

### A. N1 — BEP 배송방식 인지형 정합 (D-NAO-99·100, PR #138)
- ✅ 배경: `bep_calculator._PRICE_WINDOW_DAYS=120` 창이 ①물류비 혼합비 ②수수료율 ③판매가를 지연 반영하는 3중 왜곡 발견(ref 42 = `docs/references/42_n1_bep_alignment_simulation_20260728.md`).
- ✅ 시뮬레이션: 525종 중 1% 이상 변동 25종(03 EZ툴 라인·폴드7/플립7 매트필름 집중), 03 상한 최대 −15.9% 과대.
- ✅ **판매가 추정기 백테스트(ref 43 = `docs/references/43_price_estimator_backtest_20260728.md`)**: Jino 제안("과거 데이터로 가장 잘 맞는 중앙값을 잡으면 되지 않을까")대로 실데이터 리플레이(733 상품일·196개 상품·8종 추정기) → **E5 = 최근 10건 주문 중앙값(120일 이내)** 이 정확도·안정성·전환감지 3축 전부에서 승리, D-NAO-99가 승인했던 적응형 사다리(nmin=10)는 폐기(일간 ±10~17% 계단 진동). 일일 ±5% 스텝 캡(E4)도 왕복 악화로 기각.
- ✅ nb_share(N배송 혼합비)도 같은 표본으로 통일 — 이건 백테스트 불가(표본 81건뿐), 실측이 아니라 일관성 근거의 **구조 판단**임을 트랙에 명기.
- ✅ 구현: 신규 SA `product_commission.py`(주문관리 2.724% + 실측 매출연동 + N배송 프리미엄 +1.5%p×혼합비), `bep_calculator` 3함수 교체, 물류비 = 1,900 + 1,120×혼합비.
- ✅ 리뷰: codex 한도 소진 → 적대적 Claude(Opus) 리뷰 2R 대체. P2-1(정산 조인 데카르트 중복 — 정확 키 `product_order_id`↔`platform_order_line_id`로 수정), P2-2(parity 검증 스크립트가 무력화돼 있던 것 재무장), 정렬키 `datetime+id`로 확정.
- ✅ 라이브 검증(원칙22): prod 재계산 앵커 일치, `verify_bep_delivery_parity.py` 실대조 10,094건 불일치 0.

### B. 03 캠페인 자동운영 재가동 (D-NAO-101) — D-NAO-92 번복
- ✅ Jino "그래, 우리가 돌리자" — `optimizer` none→ours(API), `auto_operate` 0→1(DB), change_log 864·865.
- ✅ 근거: 정지 사유(ROAS 1.33 급락)가 소멸(7일 2.26·07-27 2.94로 BEP 위 회복) + 재가동 전제(ref 42 §8 #3 상한 재산출)가 N1 배포로 충족.
- ✅ 13:20 첫 회차 편입 실증: 탐색 UP 5건 집행(12프로 1000→1100, 16프로맥스 1500→1650, 13미니·12미니·15 270→350), 스톱로스 차단 2건(17프로맥스·"12"). 예산 5만 유지(변경 없음).

### C. BP(Budget Pacing) 예산 자동 증액 레인 (D-NAO-102, PR #149)
- ✅ 트리거 = 당일 소진율≥90%. 조건 = 당일 프록시 ROAS≥target(볼륨 게이트: 클릭≥5·소진≥5천). 증액 = 최근 3h 소진 속도×잔여시간×1.2(캡 base×2, 계정 일일 10만 상한). 익일 00:05 자동 원복. fail-closed. 적용 범위 = auto_operate 전 캠페인(전역, 03 한정 아님).
- ✅ 적대적 리뷰 2R: **P1 원복 래칫**(원복 1회 실패 시 base 재시드가 증거를 소멸시켜 영구 증액·복리로 번지는 결함) 해소 + 수동 개입 존중 가드 추가(우리 설정값≠현재 예산이면 물러섬).
- ✅ 라이브: 14:20 첫 평가 `reviewed=5 raised=0 failed=0`(소진율 아직 90% 미만이라 미발동 = 정상), `base_daily_budget` 자동 시드 확인.

### D. VT3 경보 사람말 개편 (D-NAO-103, PR #149)
- ✅ 배경: 아이패드 파워링크에서 07-23부터 매일 5~8그룹 반복 발화(만성 저CTR 기준선 내 통계적 평범 다수) + "탐색 래더 UP 중지" 문구가 파워링크에 원천 미적용(탐색 래더=쇼핑 전용, bid_up 이력 0건).
- ✅ 개편: 신규 진입만 발화 + 주1회 요약 + 급악화 3배 에스컬레이션, 기대클릭≥2 필터 + 완전 무반응(누적1000노출·클릭0) 별도 발화, 파워링크 문구 교체, 캠페인 압축, 신규 SA `alert_humanizer.py`.
- ✅ 알림 가독성 4원칙 확정(향후 모든 Slack 알림 공통): ①ID 아니라 이름 ②내부 용어(W1/W3·밴드·D-NAO 코드) 풀어쓰기 ③구조 = 무슨 일→숫자 근거→권하는 행동 ④같은 내용 매일 반복 금지.
- ✅ 6일 스윕 45줄→6건으로 압축 확인. 리뷰 2R.

### E. 광고 성과 "사장님 뷰" — Phase 1 (D-NAO-104, PR #151) + Phase 2 (D-NAO-105, PR #157)
- ✅ 신설 페이지 `/naver-ad/performance`(읽기 전용), 계획서 `docs/PLAN_naver-ad-performance-view.md`.
- ✅ **Phase 1**: ①오늘 한눈에(캠페인 카드) ②오늘 시스템이 한 일(한글 문장, 실행/차단 요약).
- ✅ **Phase 2**: 캠페인 선택기, 날짜 선택(today_proxy/settling/confirmed 소스 라벨 표기), 기준일 vs 비교일 증감, 캠페인 상세(ROAS 추이 + BEP/target 선), 예산 소진 곡선(암전 검출).
- ✅ **암전(블랙아웃) 검출 재설계 — 이 세션 안에서 실사고 정정 발생**: 옛 규칙(소진율≥98%)이 07-27 03의 4시간 암전(97.4%에서 멈춤)을 놓쳤다. 1차 신호를 "증분(시간당 광고비) 0이 2시간 연속"으로 바꾸고 소진율은 귀속 근거로 강등, 판정은 최종값이 아니라 **멈춘 시점 값**으로. 21일 전수 재스캔 → 신규 암전 2건 발견(07-27 03 4시간·07-19 맥세이프 4시간).
- ✅ 리뷰 지적 반영: 집행 0원 그룹 "확장 중" 오표시 → "관망"으로 정정, 비운영 43캠페인의 능동 관리 문장 → "관찰만 — 우리가 운영하는 광고가 아닙니다"로 정정.
- ⚠️ **알려진 한계**: 당일 프록시 ROAS 산출 가능 = 46캠페인 중 2개뿐(매핑이 optimizer='ours' 캠페인에만 적재되는 구조). 03은 재가동으로 익일 sync부터 편입 예상. 전 캠페인 확대는 Phase 2 후보였다가 Phase 3으로 이월.

### F. 기타
- ✅ CS 콜드스타트 08:50 첫 실집행 성공(4건, 실패 0).
- ✅ 신규 폴드8/플립8 캠페인 검수 통과·노출 중(14시 imp 815·clk 2).
- ✅ 확정 원가 반영(플립 3,227 / 폴드 3,890, 부가세포함) + BEP 수동 재계산.

## 3. 확정된 결정사항 (번복 금지, 트랙 파일에 D-NAO-99~105로 전부 기록됨)
- D-NAO-99·100: N1 적용 승인 + 판매가 추정기 E5(최근 10건 중앙값) 채택, 적응형 사다리 폐기.
- D-NAO-101: 03 자동운영 재가동(D-NAO-92 번복). 예산은 그대로 5만.
- D-NAO-102: BP 예산 자동 증액 레인 — 트리거·조건·증액폭(동적 산출)·적용범위(전역)·익일 원복·목표기준(target ROAS) 전부 파라미터 확정.
- D-NAO-103: 알림 가독성 4원칙 + VT3 개편.
- D-NAO-104·105: 성과 페이지 신설 + Phase 1/2 범위(캠페인 선택·날짜 선택·날짜 비교).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_naver-ad-optimization.md` | D-NAO-1~105 (단일 진실) |
| `docs/references/42_n1_bep_alignment_simulation_20260728.md` | N1 정합 시뮬레이션(왜곡 3중 발견) |
| `docs/references/43_price_estimator_backtest_20260728.md` | 판매가 추정기 백테스트(E5 채택 근거) |
| `docs/PLAN_naver-ad-performance-view.md` | 성과 뷰 6섹션·3 Phase 계획서 |
| `backend/app/services/naver_ad/product_commission.py` | N1 신규 — 상품별 실측 수수료 산출 |
| `backend/app/services/naver_ad/bep_calculator.py` | N1 대상 — 3함수 교체 완료 |
| `backend/app/services/naver_ad/budget_pacing.py` | BP 레인(D-NAO-102) |
| `backend/app/services/naver_ad/alert_humanizer.py` | VT3 개편(D-NAO-103) — 성과뷰도 재사용 |
| `backend/app/services/naver_ad/perf_today_harness.py` | 성과뷰 Phase 1/2 하니스 |
| `frontend/.../NaverAdPerformance.tsx` | `/naver-ad/performance` 페이지 |

## 5. 알려진 이슈 / 주의사항
1. **codex 소급 리뷰 부채 4건**(N1·프로모션 손익·BP+VT3·성과뷰 Phase1/2) — 08-02 쿼터 리셋 후 일괄 재실행 필요.
2. **내일 아침 확인 필수(원칙22, 라이브 증거)**: ①08:50 새 형식 Slack 경보 실발화 확인 ②03 ROAS가 성과 페이지 카드에 표시 시작하는지(매핑 sync 후) ③밤사이 BP 증액 발동 여부(03이 저녁에 90% 도달 시 raised>0 로그).
3. 별도 세션 조사 3건 미결: `naver_ad_daily` 상세/센티널 전환매출 불일치(task_124adb63), 폴드8울트라 소재 탐색 미편입(task_806c4425), 커맨드센터 BP 액션 집계 누락(task_c86494e4 — 종료됐다는 보고가 있으나 결과 재확인 필요).
4. 03 예산 상향 여부는 Jino 미결정(현재 5만, BP가 자동 대응하므로 급하지 않다는 판단).
5. 성과 페이지 스크린샷 미확보 — 브라우저 도구의 파일 저장 경로 문제로 Jino가 직접 화면 확인 필요.
6. Phase 3(⑤BEP 구성 뷰 + ⑥개선 타임라인, 이벤트 소스=트랙 D-N 파싱+git log 매칭, 신규 테이블 없음)은 계획서 §6에 설계만 있고 미착수 — 정직 규약("개선됐습니다" 단정 금지·confounded 표기) 유지할 것.

## 6. 다음에 할 작업 (우선순위순)
- [ ] 내일 아침 §5-2 3항목 라이브 확인(가장 우선).
- [ ] Phase 3(BEP 구성 + 개선 타임라인) 착수.
- [ ] 08-02 이후 codex 소급 리뷰 4건 일괄 재실행.
- [ ] 성과 페이지 당일 프록시 ROAS 커버리지 확대 여부 Jino 판단(현재 46개 중 2개만 산출 가능).
- [ ] §5-3 별도 조사 3건 결과 회수.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_n1-bep+03재가동+bp페이싱+성과뷰_20260728.md 읽고 이어서 작업해줘.
핵심: N1(BEP 배송정합)·03 재가동·BP 예산페이싱·VT3 개편·성과뷰 Phase1-2 전부 prod 배포·라이브 검증 완료.
먼저 ①08:50 새 경보 실발화 ②03 ROAS 성과페이지 반영 ③BP 증액 발동 여부를 라이브 확인하고, Phase 3(BEP구성+개선타임라인) 착수해줘.
```
