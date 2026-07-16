# 세션 인수인계: D-NAO-46② 키워드 시간별 축적 — recon·구현·배포·라이브합격 + PR #22 병합·PR #23 (2026-07-17 새벽)

> 저장일시: 2026-07-17 02:20 (KST)
> 새 대화 시작 시 이 파일 먼저. 트랙: `docs/tracks/active/track_naver-ad-optimization.md`.
> 직전: `HANDOFF_ohisell-retro-scoring+pacing-deployed+PR22_20260716.md` (2e3bfa 워크트리) — 그 §6의 PR#22 병합·D-NAO-46 잔여 설계가 이 세션에서 완결.

## 1. 프로젝트 위치 및 환경
- 워크트리: `Ohiselling/.claude/worktrees/spot-backtest-cadence-pacing-dedfad`, 브랜치 `claude/spot-backtest-cadence-pacing-dedfad`(main 기준, **PR #23 오픈**: https://github.com/Jino00/ohisell/pull/23). 커밋 9개 push됨.
- **PR #22는 이 세션 시작에 병합 완료**(merge c4a728e) — main==prod였다가, 이 세션 배포로 **prod가 다시 브랜치 HEAD 코드**(PR #23 병합하면 재정합).
- prod: `ssh sellc.ohitech.co.kr`, 포트 8001(pm2 id0), DB `/home/ubuntu/ohisell/backend/ohisell.db`. DB 백업: `/home/ubuntu/ohisell_bak/naver-keyword-hourly_20260717_predeploy.db`(174MB, 마이그 전).
- 로컬 테스트: `cd backend && python3 -m pytest tests/...`(홈브류, venv 없음. 라우터·apscheduler류 collection 에러는 기존 이슈).

## 2. 이번 세션 완료 목록
- ✅ **PR #22 병합**(D-NAO-44/45/46①) → 당시 main==prod 복원.
- ✅ **recon(Opus, ref 32 신설)**: prod 라이브 ~49콜 전부 읽기 GET. ①avgRnk=`/stats` 필드로 실시간 확보(리포트 rank_sum/imp 교차검증 일치) ②**★핵심 발견 `breakdown=hh24`+`timeIncrement=allDays`+단일일 timeRange** = 유닛 1콜로 그 날 24시간대 imp/clk/cost/avgRnk 곡선(매시 폴링 41,664콜/일→일 1회 1,736콜/일, 1/24) ③**hh24 상세 보존=최근 7일 하드리밋**(400 code 11004 — 놓치면 영구 소실=배포 긴급성) ④자식분해(breakdown=keyword)=불가(무언 무시 — breakdowns 부재 시 실패 처리 필수) ⑤qi=`/ncc/keywords` `nccQi.qiGrade`(1~7) 무상(41/41 실측, entity_sync 열거 편승) ⑥ids 복수=키워드도 400(id 단수 확정).
- ✅ **설계(Fable)**: `docs/PLAN_naver-ad-keyword-hourly-accrual.md` — 이중 루프(시간=순위·CPC·페이싱, ROAS 금지 / 일=경제성) 데이터 계층. 실입찰 시간당 루프는 스코프 밖(개방 게이트 불변).
- ✅ **구현(Sonnet TDD)**: 테이블 `naver_keyword_hourly`(grain ad_date×entity_id×hour, WEB_SITE=키워드/쇼핑·브랜드=그룹, 365d 롤링, 마이그 `g7h8i9j0k1l2`) + `fetch_entity_hh24` + `keyword_hourly_sweep`(D-1 본스윕+[D-6,D-2] 유닛단위 캐치업+콜캡 3,500+**유닛 증분 커밋**) + `naver_hourly_snapshot.avg_rank` + `naver_entity.qi_grade`(None 응답 시 last-known 유지) + 크론 `sweep_naver_keyword_hourly` 09:10(catch-up 목록 포함). 테스트 813 pass(신규 37).
- ✅ **codex 5R GATE PASS**(P1 0): [P2]×4 전부 RED 재현 후 수정 — ①qi None 덮어쓰기 ②빈 fetch가 기존 이력 삭제 ③campaign_type 공백 시 grain 오분류(→keyword_id로 판정) ④캐치업 sweep_date 중복(IntegrityError 실재현). 4R·5R clean.
- ✅ **prod 배포+라이브 합격(원칙22)**: 백업→7파일 sha256 7/7→마이그 적용(head g7h8i9j0k1l2)→pm2 online→**수동 스윕 3회 8,597콜 failed 0** → **07-11~15 백필 완결 43,346행·유닛 1,639~1,800/일**(캐치업이 만료임박 오래된 날짜 우선 처리 확인)→정합 스팟체크 **키워드 3/3 imp·clk·cost 완전 일치**·쇼핑그룹 일치(1건 0.6% 시점차)·avg_rank 100% 채움→**02:05 스냅샷에 avg_rank 30/45 유입(평균 6.01)**.
- ✅ **★라이브가 잡은 결함 1건(failures.jsonl 기록)**: 구코드 스윕이 단일 커밋으로 ~12분 SQLite 쓰기락 보유 → **01:05 스냅샷 크론 `database is locked` 실측 실패**(매일 09:10 vs 09:05 충돌 구조적 보장이었음) → 유닛 증분 커밋(d8f0ca5)으로 수정 → **02:05 크론이 스윕3 실행 중 성공 = 해소 라이브 증명**. 02:00 이후 락 에러 0.
- ✅ PR #23 생성, 트랙 D-NAO-46②·progress·PLAN §7 전부 갱신, 09:30 예약 루틴에 아침 확인 4건 병합.

## 3. 확정 결정사항 (번복 금지)
- **수집 아키텍처**: 키워드 grain 시간별 = 매시간 폴링이 아니라 **일 1회 D-1 hh24 스윕**(+7일 캐치업). 실시간 관제는 캠페인 grain 스냅샷(avg_rank 추가됨)이 담당. 핫셋 intraday·순위 이탈 트리거는 축적 후 별도 스프린트(계획서 §6).
- **grain 판정=keyword_id 비어있음 여부**(campaign_type 아님 — 공백 타입 방어). ad_daily 읽기 시 `__backfill__` sentinel 제외 규약 유지.
- **SQLite 장시간 루프 잡=유닛 증분 커밋**(교훈 일반화, failures.jsonl).
- D-NAO-46 개방 게이트 불변: 840 카나리 → 성적표 신뢰 → 시간당 실입찰. 이번 배포는 전부 관찰(GET)만.

## 4. 핵심 파일
| 파일 | 역할 |
|---|---|
| `docs/PLAN_naver-ad-keyword-hourly-accrual.md` | 설계+진행기록(§7 전부 체크, §8 라이브 합격 시나리오) |
| `docs/references/32_naver_sa_hh24_breakdown_recon_20260717.md` | ★hh24 recon 실측(부하표·7일 리밋·qi) |
| `backend/app/services/naver_ad/keyword_hourly_sweep.py` | 스윕 Harness(증분 커밋) |
| `backend/app/services/naver_sa_ad_fetcher.py` | fetch_entity_hh24·_STATS_FIELDS avgRnk·get_keywords qi |
| prod `/home/ubuntu/ohisell_bak/naver-keyword-hourly_20260717_predeploy.db` | 마이그 전 백업 |

## 5. 알려진 이슈 / 주의
- `sync_naver_keyword_volume` last_status=error — 01:05 락 사건의 피해자(수정 전). 다음 자연 실행에서 자가 회복 예상, 09:30 루틴이 확인.
- 07-16분 keyword_hourly는 **오늘 09:10 크론이 첫 본스윕**(08:00 ad_daily 적재 후). 01~02시 수동 스윕에선 targets 0이 정상이었음(ad_daily 미적재).
- hh24 `breakdowns[].name`은 한글 라벨 파싱("00시~01시") — API 포맷 변경 시 파싱 skip+warn으로 드러남.
- naver_ad_daily 집계=sentinel/상세 택일(2배 함정) 상시 주의. MOP 6245/5752 bidYn=N 여전(Jino 게이트).

## 6. 다음에 할 작업
- [ ] **(Jino) PR #23 병합** → main==prod 재정합.
- [ ] **09:30 예약 루틴 자동 확인 4건**: ①08:15 flight projection(04 모델 07:50 복귀 후) ②08:30 retro 크론 ③**09:10 sweep 크론 첫 자연 발화+09:05 스냅샷 무충돌** ④qi_grade 채움+keyword_volume 회복. (루틴이 안 돌면 수동으로 이 4건 확인.)
- [ ] **다음 스프린트 후보(계획서 §6)**: trigger_watch 순위 이탈 트리거(avg_rank 2~3주 축적 후) / 핫셋 intraday 관제+시간당 입찰(개방 게이트 뒤) / 쇼핑 소재 qi(/ncc/ads).
- [ ] (Jino 게이트) 04 pending 840 콘솔 승인(첫 카나리 겸 밴드 인과 검증), MOP 6245/5752 bidYn=Y.
- [ ] 소급 채점 성적표 주간 추이 관찰(위임 개방 근거 축적).

## 7. 새 세션 시작 프롬프트
```
.claude/worktrees/spot-backtest-cadence-pacing-dedfad/.claude/memory/HANDOFF_ohisell-keyword-hourly-accrual-deployed+PR23_20260717.md 읽고 이어서. 라우팅: 구조=Fable·하위=Opus·단순=Sonnet. 핵심: ①D-NAO-46② 키워드 시간별 축적 배포·라이브합격 완료(hh24 스윕·avgRnk·qi, 백필 07-11~15 완결), PR #23 병합 대기 ②09:30 루틴이 아침 크론 4건 확인(09:10 sweep 첫 발화 포함) ③라이브 발견 결함(스윕 12분 쓰기락→database is locked) 증분 커밋으로 수정·02:05 크론 성공 증명 ④다음 스프린트 후보=순위 이탈 트리거(2~3주 축적 후)·핫셋 intraday(게이트 뒤) ⑤04 pending 840 승인=Jino. 원칙22: 라이브 증거로만.
```
