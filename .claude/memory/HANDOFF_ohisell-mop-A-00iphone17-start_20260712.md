# 세션 인수인계: A안 MOP 라이브 관찰 착수 — 00.아이폰_17

> 저장일시: 2026-07-12 14:37 (KST) — 세션 작업은 2026-07-11 저녁
> 새 대화 시작 시 이 파일을 먼저 읽을 것. **다음 = D+1 관찰(아래 §6)**.
> 트랙 결정으로도 기록됨: `docs/tracks/active/track_naver-ad-optimization.md` **D-NAO-42**.

## 0. 한 줄 요약
실제 **MOP가 캠페인 `00.아이폰_17`을 자동입찰 관리하게 하고, MOP가 뽑는 데이터·판단을 매일 관찰**해 우리판 naver_ad와 비교하는 게 A안. 이번 세션: 대상 캠페인 확정 + MOP 라이브 관찰 방법(API 인증) 확립 + 조사 2건 + baseline 문서/스냅샷 스크립트. **실데이터는 아직 0**(캠페인 07-11 방금 켬, MOP 전일반영이라 D+1부터).

## 1. 프로젝트 위치 및 환경
- 워크트리: `.../Ohiselling/.claude/worktrees/naver-ad-execution-loop-6cc75b`, 브랜치 `claude/iphone-privacy-mop-automation-eb3b57`(main 기준).
- **브라우저**: gstack `/browse` 헤디드 Chromium. 바이너리 `$HOME/.claude/skills/gstack/browse/dist/browse`, 포트 34567. 죽어 있으면 `/connect-chrome` 스킬로 재연결(Step0 클린업→connect). **헤디드여야** Jino가 로그인 가능(headless면 UA=HeadlessChrome, focus 불가).
- **MOP 로그인은 Jino가 직접**(계정 인증은 내가 못 함). MOP 콘솔=`mop.co.kr`, 로그인 계정 김진오/jino.kim@theohi.com, 애드써클 `오하이_구민정`(Operator, **Basic plan**).
- **MOP API 백엔드=`be.mopapp.net`**, advertiserId=**1422**(직전 HANDOFF의 756은 틀림, 실캡처 1422). 인증=헤더 `x-session-id: <sessionStorage.sessionId>`(쿠키는 httpOnly, withCredentials만으론 SESSION_EXPIRE).
- 우리 백엔드 prod: pm2, 크론 07:00/07:30/07:40(수집)·07:50/08:00/08:05/08:10. 로컬 워크트리 DB엔 naver_ad_daily 테이블 없음(라이브는 prod).

## 2. 이번 세션 완료 목록
- ✅ **대상 캠페인 변경(Jino)**: `05.아이폰_사생활보호` → **`00. 아이폰_17`**(cmp-a001-02-000000009793536, 쇼핑검색). 애드그룹3: 01.강화유리(입찰50)·02.사생활(50)·03.6H사생활(500).
- ✅ **캠페인 ON(Jino, 07-11 MOP 테스트용)**: 캠페인+그룹3 전부 운영가능, 하루예산 150만→**5만원**(소액 안전판). (그 전엔 전부 OFF·7일지출0이라 내 실측이 OFF였던 것 — 해소.)
- ✅ **MOP 관찰 방법 확립(원칙22, 라이브)**: be.mopapp.net·1422·x-session-id 인증 리버스. 캠페인 실적 API `POST /v1/report/campaign/1422/summary?startDate&endDate` + body `saShoppingCampaigns.naverCampaignIds:[cid]`(그 외 sa/da/va/app·*Campaigns 다 빈배열). adgroups 트리 `GET /v1/report/campaign/1422/adgroups`. available-period `GET /v1/report/available-period/1422?reportType=REPORT_CAMPAIGN`.
- ✅ **MOP 이지모드 위저드 실측(저장 안 함)**: 최적화 유닛 신규1개 가능(Basic 0/1, 기존 4099·1119 종료), 운영모드=**균형**(기본·추천, 다중목표)/**성장**(광고비 증액), 하루예산=**7일평균 기반**(직접입력 없음→7일지출0→**0원 계산**), 제외키워드 Basic Pro잠김. 저장버튼="저장"(안 누름).
- ✅ **조사1(네이버 API 실시간)**: 공식 Swagger `ncc-report.json` — stat API `timeIncrement` enum=`"1"(일)`/`"allDays"`뿐, **hourly 없음**. `breakdown=hh24`는 기간합산 시간대분해(시계열 아님). 당일=`datePreset=today`+`cycleBaseTm`(부분치). **→ 실시간 데이터 불가, 일 단위가 최소.**
- ✅ **조사2(우리 목표 코드)**: 실제 목표는 **단일**(D-NAO-1: BEP-ROAS 하한 내 전환매출 극대화). `NaverCampaignSettings.mode`(성장/회복/런칭/방어 4종)=**저장만·계산 미반영**(D-NAO-22-② 미구현). `gave_score.py`=docstring상 목적함수라나 **미배선**(호출 0건). BEP 하한은 `guardrail_gate.py:126-132` 하드게이트로 실작동. MOP식 다중목표 대응 개념 없음.
- ✅ 문서 3개 저장: `docs/references/data/mop_ui/mop_live_A_00iphone17.md`(baseline+비교표), `.../mop_snapshot.js`(재현 스냅샷), 트랙 D-NAO-42 + progress.txt 갱신.

## 3. 확정된 결정사항
- **자동화 경로=MOP 유지**(A안). 우리가 데이터 돌리는 게 아니라 진짜 MOP가 관리하는 걸 관찰(Jino 명시).
- **대상=00.아이폰_17**(위 ID). 소재 여러 개(애드그룹3)는 MOP가 애드그룹 단위로 묶어 자동입찰.
- **애드그룹 기본입찰가는 MOP가 재설정하도록 자유도 최대**(우리 개입 최소, 예산 5만이 안전판) — Jino 지시.
- **저장/시작(돈)은 Jino 명시 확인 후에만**. 임의 커밋 금지.
- **BEP-ROAS 숫자 하한은 유지 기본**(D-NAO-1 불변). MOP식 목표 채택은 신규 구현이며 **며칠 관찰 후 결정**(설계 선고정 금지, Jino 논의 중·미확정).
- **관찰 접근=행동 궤적 역설계**(MOP 내부 러닝/플래닝은 블랙박스, Basic 예측화면 Pro잠김). 매일 입찰가·CPC·순위·소재성과 스냅샷.
- **관찰 스냅샷 주기=하루 1회**(네이버 API가 일 단위라 하루 여러 번 찍어도 당일 부분치만).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/references/data/mop_ui/mop_live_A_00iphone17.md` | A안 관찰 로그·MOP↔우리 구조 비교표·관찰일지(매일 채움) |
| `docs/references/data/mop_ui/mop_snapshot.js` | MOP API 스냅샷 재현 스크립트(로그인 상태에서 `$B eval` 실행) |
| `docs/tracks/active/track_naver-ad-optimization.md` | 트랙 마스터(D-NAO-42가 이 세션) |
| `claude-progress.txt` | 세션 상태(상단 블록 갱신됨) |
| `backend/app/models.py:1448` `NaverAdDaily` | 우리 지표 컬럼(imp/clk/cost/rank_sum/conv_direct·indirect) |
| `backend/app/services/naver_ad/bep_calculator.py`·`guardrail_gate.py` | BEP 배수·하한 게이트 |

## 5. 알려진 이슈 / 주의사항
- **gstack 헤디드 브라우저 세션 반복 로그아웃**(원인 미규명 — 세션TTL vs 프로세스 크래시). 자동관찰의 걸림돌. D+1에 원인 점검. browse 서버가 headless(`launched`)로 되돌아가면 포트34567 점유 프로세스 kill 후 `connect` 재실행.
- **MOP summary가 지출0 구간에 전 지표 `-` 반환**(프론트 응답도 동일 확인). 데이터 쌓이면 재검증.
- **시계열(일별 테이블) 엔드포인트 미확정**: 오늘 데이터0이라 캡처 못 함. D+1에 리포트-캠페인 "날짜별/매체별" 탭 클릭→응답캡처 후킹으로 잡아 mop_snapshot.js에 추가.
- **stale 정보 주의**: 구 HANDOFF의 advertiserId 756은 틀림(실측 1422). 원칙22 — 실캡처값 우선.
- **별도 발견(스코프 밖, Jino 판단)**: `mode` 다이얼 미반영·`gave_score` 미배선 = 우리 프로그램 표방↔실구현 괴리. 원하면 백그라운드 작업으로 플래그.
- **병행 미결(별건)**: D-NAO-41 크론 자연 재확인(07-12 아침 07:00/07:30/07:40 실행 후 naver_ad_daily 07-11 등장·scheduler ok — 원칙22). A안과 독립.

## 6. 다음에 할 작업 (D+1, 2026-07-12)
- [ ] gstack 헤디드 창 살리고(필요시 /connect-chrome) → **Jino가 MOP 재로그인**
- [ ] `$B eval docs/references/data/mop_ui/mop_snapshot.js` 실행 → 07-11 노출·클릭·지출이 MOP summary/adgroups에 반영됐는지 + 이지모드 예산계산이 **0원 탈출**했는지 확인(원칙22 라이브)
- [ ] 시계열 엔드포인트 캡처(리포트-캠페인 날짜별 탭) → mop_snapshot.js 보강
- [ ] 반영 확인되면 → **Jino 승인 후** MOP 쇼핑 입찰최적화 유닛 생성(이지모드, 캠페인=00.아이폰_17 애드그룹3, 운영모드 균형or성장, 예산 프리셋) → **저장(돈)** → 러닝 관찰 시작
- [ ] mop_live_A_00iphone17.md 관찰일지 D+1 칸 실측으로 채우기
- [ ] gstack 세션 반복 로그아웃 원인 점검
- [ ] (별건) D-NAO-41 크론 자연 재확인

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_ohisell-mop-A-00iphone17-start_20260712.md 읽고 A안 MOP 라이브 관찰 D+1 이어서 진행해줘
