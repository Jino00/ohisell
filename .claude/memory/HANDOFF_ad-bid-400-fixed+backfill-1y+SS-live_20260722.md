# 세션 인수인계: 소재입찰 400 근본수정·라이브 합격 + 과거 1년 백필 + SS 스프린트 전체 완주

> 저장 2026-07-22 01:45 KST · 워크트리 `session-dfd814` · 브랜치 `claude/bx-exploration-ad-bid-400-fix-fb114e`
> **PR #76·#77 병합 완료 = main==prod (`af1be1b`)**. 전부 배포·라이브 상태.

## 1. 이번 세션 완료 (전부 라이브 실측 근거 있음, 원칙22)

### ① 소재입찰 400 code:3830 근본수정 — 라이브 합격 (D-NAO-75)
- 원인 확정 여정: 1차(adAttr 문자열→객체+최소 body) 배포 후 **22:20 라이브 재실패** → prod 무해 프로브(현재 입찰가 재전송) 3변형 실측 → **GET 전체 객체에 adAttr(dict)만 교체 PUT = 200** 확정(2차 f8d5757).
- **23:20 레인 소재 10건 전건 실쓰기 성공**(강화유리 8·04 2, change_log 213~222, 실패 0) = 콜드 탐색 유일 레버(소재 손) 개통. failure-memory 기록됨.
- 교훈: 네이버 Ad PUT은 최소 body 불가 — type 포함 전체 객체 필요(update_adgroup_bid와 동일 규율).

### ② 과거 1년 그룹-그레인 백필 (Jino 승인)
- 네이버 한도 실측: 대용량 보고서 **롤링 1년**(AD/CONV 동일)·hh24는 7일. AD_CONVERSION 잡 **NONE=그날 전환 0건**(보존한도 아님).
- `backend/scripts/backfill_naver_ad_daily_history.py` 신설·실행 완료: **2025-07-22~2026-07-20, 364일·90.3만 행**(적재 335·기존 2·보고서부재 10=저활동일 fail-closed 보존·에러 0).
- 함정 2개 수정: `_safe_int` float 표기('9670.0') 소실(f05d54d)·NONE 오판. 계절성/GAVE/환경셀 학습 기반이 17일→1년.

### ③ SS 스프린트(검색어 ROAS 레이어) 전 페이즈 — 설계→구현→검증→배포→라이브 (D-NAO-76·77)
- **SS0 실측**: 검색어×전환 = `SHOPPINGKEYWORD_CONVERSION_DETAIL`(15컬럼, ±2 오프셋) 확정 / **쇼핑 제외키워드 API 쓰기 불가**(400 code 3728, 가역 프로브·원복 확인) → SS3 분기 확정.
- **SS1** 수집층: 마이그 `f2a3b4c5d6e7`(전환 5컬럼)·`fetch_search_term_conversion`(STCONV_COL_*)·병합 ingest(캐리포워드=보고서 부재 날짜만·보고서 실재=진실)·07:40 크론 편입.
- **SS2** `search_term_judge`: rolling 14일·봉투 게이트(①전환 보호 ②clk≥10∧conv=0∧cost≥공헌이익 ③화이트리스트 casefold)·fail-closed.
- **SS3** 실행 배선: **자동발사 0** — 쇼핑=브리핑 전용 / 파워링크=pending Confirm 제안만. harness `exclude_search_term` executor(SHOPPING 2중 거부·일일캡 10=오늘 클레임 예약+쓰기후 재카운트 롤백 3중 방어·실행시점 전환 재검증·killswitch·전건 change_log). `ss_exclude` 자동 승인원은 정의만·비활성(개방=Jino 승인 필요).
- **SS4** 승격 제안: `search_term_promote`(informational·실행 매핑 없음=원천 거부)·**상한 20**(라이브 354건 범람 실측 후 보강).
- 검증: GATE 적대 PASS(P1 0) + **codex 왕복 3R 전건 수용**(1R P1×3·2R P1+P2×2·3R P2×1 — 전환 캐리포워드/실행시점 재검증/캡 원자예약/target_id 50자/보고서 실재=진실/report_dates 순서). **2756 passed·회귀 0**.
- 라이브(01:35~50): alembic 적용·부팅 200·**전환 프라이밍 14일=799행 병합**·레인 1회 실측(제외 후보 0=표본 게이트 미달 정당 절제·승격 354→정리·상한 재생성은 08:50 크론).

## 2. 남은 관측/작업 (다음 세션)
- [ ] 07:40 크론 검색어+전환 수집 자연 발동 확인(첫 크론 경유).
- [ ] 08:50 SS 레인 첫 크론 실행 — 승격 상위 20 재생성·쇼핑 브리핑 diary 확인.
- [ ] 탐색(B-X) 순위 반응 관측 — 23:20 상향 10건의 avg_rank 이동→래더 재판정(상설).
- [ ] SS 상수 첫 주 캘리브레이션: `_SS_MIN_CLICK`/`_SS_MIN_COST`(제외 후보 0이 계속되면 완화 검토)·화이트리스트 폭(Jino 확정)·제외 슬롯 한도(70 vs 140, 첫 파워링크 Confirm 실쓰기 시).
- [ ] 파워링크 첫 제외 Confirm 왕복(Jino 콘솔) = SS3-A 라이브 합격 마지막 조각.
- [ ] codex 소급 리뷰 07-23(IU-R·B-X 커밋 — SS는 이번에 왕복 완료).
- [ ] (백로그) 쇼핑 제외 콘솔 API 리버스/브라우저 자동화·D-NAO-73 키워드 예산 재분배(SS후 L2)·D-NAO-74 페이지 경계.

## 3. 주의
- 세션 중 실수 기록: 라우팅 우회(Fable이 Sonnet 몫 직접 수행 → Jino 질책) — `.claude/memory/LESSONS_LEARNED.md`(프로젝트 루트) 기록. 이후 전 작업 위임 준수.
- 백필 스크립트는 재실행 멱등(상세 있으면 skip). 잔여 센티널 27일 중 17일은 07-04 이후 정상 이중구조(2배 함정 — 집계 시 센티널 제외 관례 유지).
- naver_search_term_daily 전환 컬럼은 07-08 이후만 채워짐(프라이밍 14일). 그 이전 검색어 전환은 없음(판단 창 14일이라 영향 없음).

## 4. 새 세션 시작 프롬프트
`.claude/worktrees/session-dfd814/.claude/memory/HANDOFF_ad-bid-400-fixed+backfill-1y+SS-live_20260722.md 읽고 이어서. 핵심=소재입찰 400 수정 라이브합격·1년 백필 완주·SS 스프린트 전체 배포(PR #76·77 병합, main==prod). 다음=07:40/08:50 크론 관측→SS 캘리브레이션→파워링크 첫 제외 Confirm.`
