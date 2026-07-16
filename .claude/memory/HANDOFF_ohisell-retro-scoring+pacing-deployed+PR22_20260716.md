# 세션 인수인계: 소급 채점 상설화 + pacing 보정 + 3건 합동 배포 + PR #22 (D-NAO-44/45/46)
> 저장일시: 2026-07-16 23:59 (KST)
> 새 대화 시작 시 이 파일 먼저. 트랙: `docs/tracks/active/track_naver-ad-optimization.md`.
> 직전: `HANDOFF_ohisell-spot-backtest+data-cadence+pacing-wip_20260716.md`(같은 날 저녁, 다른 워크트리) — 그 WIP는 이 세션에서 완결·배포됨.

## 1. 프로젝트 위치 및 환경
- 워크트리: `Ohiselling/.claude/worktrees/spot-backtest-cadence-pacing-2e3bfa`, 브랜치 `claude/spot-backtest-cadence-pacing-2e3bfa`(main 기준, **PR #22 오픈**: https://github.com/Jino00/ohisell/pull/22). 워킹트리 clean, 커밋 18개 push됨.
- prod: `ssh sellc.ohitech.co.kr`, 포트 8001(pm2 id0), DB `/home/ubuntu/ohisell/backend/ohisell.db`. 실행 규약: `cd backend; set -a; . .env; set +a; PYTHONPATH=. .venv/bin/python3 …`. **prod == 이 브랜치 HEAD 코드**(main보다 앞섬 — PR 병합해야 main==prod 복원).
- DB 백업: `/home/ubuntu/ohisell_bak/naver-retro-pacing_20260716_predeploy.db`(167MB, 마이그레이션 전).
- 로컬 테스트: homebrew `python3 -m pytest`(venv 없음, 라우터류 bcrypt collection 에러는 기존 이슈).

## 2. 이번 세션 완료 목록
- ✅ **D-NAO-44 pacing 완결도 보정 완결**: 직전 세션 WIP(exciting-liskov-681358 미커밋)를 이 워크트리로 이식 → `completeness_curve.py`(신규 SA)+`flight_loop.py` 배선+테스트. **codex 3R PASS**([P2]×3 수정: ①build_curve `today=` as-of 관통 ②clk·conv_amt 동일 factor 투영으로 so-far ROAS 비율 보존(mutation check) ③factor를 `snapshot_hour` 기준 캠페인별 조회). flight 통합테스트 5종 신규(WIP에 없었음).
- ✅ **ref 31 소급 채점(일회성 실측)**: 기존 912제안 중 실행형은 5건뿐(전부 07-16 bid_up, 채점 불가) → 진단 보드를 as-of 07-08~12 리플레이+사후 채점. **결과: down/pause 정밀도 61~88%(3일 지속 flag 시 88%)·유니크 적자 148타깃 50.3만원 실출혈 / up 29~60%(소표본 평균회귀 — 풀링 필요 실증) / trigger_pacing 저속경보 98.7% 진짜(만성 저소진 실재 — 완결도 보정해도 2.6%만 억제)·과속경보 9건 전오탐**. 문서 `docs/references/31_retro_scoring_boards+pacing_20260716.md`+재현 스크립트.
- ✅ **D-NAO-45 상설 소급 채점 구현·배포**: 계획서 `docs/PLAN_naver-ad-retro-scoring.md`(Fable) → Sonnet TDD 구현 → codex 2R PASS([P2]×2 수정: growth 렌즈에 캠페인 override 목표 고정 / 단계 실패 시 db.rollback 격리, 둘 다 mutation check). 산출: 테이블 2(`naver_retro_signal`·`naver_retro_pacing_score`, 마이그 `f6g7h8i9j0k1`)+SA 3(`retro_snapshotter/scorer/pacing_scorer`)+Harness(`retro_scoring_loop`)+크론 `run_naver_retro_scoring` 08:30+`GET /api/naver/ad/retro-scorecard`.
- ✅ **D-NAO-46 방향 확정+①착수**: "시간별 데이터 영구 축적+이중 루프 관제(시간=순위·페이싱 / 일=경제성)+폭 우선 성장"(Jino 승인). ①`hourly_snapshot.py` `_RETAIN_DAYS` 7→365(codex PASS). 경쟁사 220~240% ROAS = 우리 3등밴드 실측 221%와 일치 — 비결은 폭(771 건강 굶는 그룹), 깊이 아님.
- ✅ **D-NAO-44-a Ava 배선 확인(§6-⑤ 해소)**: 드리프트 아님 — 실행 코어는 Ava 무인지, delegation_gate 자동 레인만 `verdict=='agree'` 필요조건 소비(위임 스위치=Jino 전용이 선행 게이트, 현재 빈 set). D-NAO-25 일관, 수정 불요.
- ✅ **3건 합동 prod 배포(Jino "지금 배포하자")**: 백업→11파일 scp·sha256 11/11→alembic e5f6g7h8i9j0→f6g7h8i9j0k1(테이블 2 생성 확인)→pm2 online·크래시0→크론 등록 확인→**백필(07-08~15) 완료: 신호 5,377건(일 514~808)·D+3 3,068·D+7 514·페이싱 788(unparsed 0)**→`/retro-scorecard` 실데이터 rollup이 ref31과 정합(bleeding d3 82.2%·starving 17.9%·sgb 68.2%)→완결도 곡선 prod 라이브 산출(12시 0.280·18시 0.648·23시 0.913=v2 정확 일치, 23시 factor ×1.095).
- ✅ **PR #22 생성**(위 3건+문서 전부, 신규 76 test 포함 naver 스위트 902 pass·codex 총 7R PASS).
- ✅ failures.jsonl 1건(pacing factor 시각 불일치+ROAS 비대칭 투영), 트랙 D-NAO-44/44-a/45/46·progress·PLAN §7/§8 전부 갱신.

## 3. 확정된 결정사항 (번복 금지)
- **D-NAO-45**: 소급 채점 상설화 — 매일 08:30 as-of 스냅샷(렌즈 고정)+D+3/D+7 채점. 판정 규약=ref 31 §1-a 고정. resume류 채점 제외(정직 경계).
- **D-NAO-46**: 시간별 영구 축적+이중 루프+폭 우선 성장. **개방 순서 게이트: 840 카나리 인과 검증 → 성적표 수주 신뢰 확인 → 시간당 실입찰 개방**(검증 안 된 신호의 고빈도 실행 금지). 파워링크 키워드 조합 실험은 일~주 단위(시간당 금지 — 검수·표본 물리학).
- **시간당 루프는 ROAS를 쫓지 않는다**(전환 간접 65~70%·~1일 정착) — 순위·CPC·페이싱만. 경제성은 일 단위 루프.
- 이익극대 스팟=순위 2.5~4밴드+한계ROAS≥진짜BEP 1.4758(카나리 인과승격 대기), Ava=조언자(D-NAO-25), 모델 라우팅 3단(Fable/Opus/Sonnet), D-NAO-1 이익하한 — 전부 유효.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/PLAN_naver-ad-retro-scoring.md` | D-NAO-45 스펙+진행기록(§8 배포까지 체크됨) |
| `docs/PLAN_naver-ad-pacing-correction.md` | D-NAO-44 스펙+의미확정 기록(§7) |
| `docs/references/31_retro_scoring_boards+pacing_20260716.md` | ★소급 채점 실측 보고서(정밀도·출혈·페이싱) |
| `backend/app/services/naver_ad/retro_{snapshotter,scorer,pacing_scorer,scoring_loop}.py` | 소급 채점 SA 3+Harness |
| `backend/app/services/naver_ad/completeness_curve.py`+`flight_loop.py` | 완결도 보정 |
| `backend/app/services/naver_ad/hourly_snapshot.py` | `_RETAIN_DAYS=365`(D-NAO-46①) |
| prod `/home/ubuntu/ohisell_bak/naver-retro-pacing_20260716_predeploy.db` | 마이그레이션 전 백업 |

## 5. 알려진 이슈 / 주의사항
- ★**04 예측모델 강등 중**: campaign cmp-…8514959가 07-12 forecast_scorer 강등(demoted_until=07-16) → 07-13~16 flight 루프 "forecast 없음" 정당 스킵(로그 공백의 원인, 버그 아님). **07-17 07:50 게이트 재평가로 자동 복귀 예정**(활동일 14/14 확인됨) — 복귀 안 되면 그때 조사.
- **retro 페이싱 skipped 102건** = sentinel 최종치 미도래(07-16 경보 등) — 다음 날 크론이 자동 재시도(설계대로).
- naver_ad_daily 집계=sentinel/상세 택일(2배 함정) 상시 주의. MOP 6245/5752는 여전히 bidYn=N(Jino 활성화 전 학습데이터 0).
- 워크트리 부채: exciting-liskov-681358의 pacing WIP는 이제 **stale**(이 브랜치 커밋본이 codex 수정 포함 최신) — 정리 대상. 6cc75b의 백테스트·관찰 산출물 untracked 부채 여전(핵심은 ref 31·HANDOFF에 흡수됨).
- 07-17 09:30 예약 루틴(곡선 out-of-sample 검증+VM 임시 폴러 크론 제거) 발동 예정. 07-22 웜업 루틴은 프레이밍 구식(bidYn=N 정정 반영 필요).

## 6. 다음에 할 작업 (미완료)
- [ ] **(Jino) PR #22 병합** → main==prod 정합 복원.
- [ ] **07-17 아침 라이브 확인 2건(원칙22 잔여)**: ①08:15 flight 크론 change_log에 projection 필드 실출현(04 forecast 복귀 후, `SELECT after_value FROM naver_change_log WHERE action='flight_pacing' ORDER BY id DESC LIMIT 1`) ②08:30 retro 크론 자연 발화(as-of 07-16 신호 추가: `SELECT count(*) FROM naver_retro_signal WHERE asof_date='2026-07-16'`).
- [ ] **D-NAO-46 잔여 설계**: 키워드 grain 시간별 수집+스냅샷 순위(avgRnk) 필드 확장 타당성(API 부하 실측) → 이중 루프(시간당 밴드 관제) 설계 스프린트(Fable). 품질지수(1~7) 수집 확장 검토(recon 문서 미확인 — 스웨거/실API 확인 필요).
- [ ] (Jino 게이트) 04 pending 5건 콘솔 승인 — 840 최우선(첫 카나리 겸 밴드 인과 검증), 839는 소재 구성(useGroupBidAmt) 확인 먼저.
- [ ] (Jino 게이트) MOP 6245/5752 bidYn=Y 활성화.
- [ ] 소급 채점 성적표 주간 추이 관찰 → 위임 개방 근거 축적(예: bid_down 4주 85%+).

## 7. 새 세션 시작 프롬프트
```
.claude/worktrees/spot-backtest-cadence-pacing-2e3bfa/.claude/memory/HANDOFF_ohisell-retro-scoring+pacing-deployed+PR22_20260716.md 읽고 이어서. 라우팅: 구조=Fable·하위=Opus·단순=Sonnet. 핵심: ①3건(pacing 보정·소급채점+백필·보존365일) prod 배포 완료, PR #22 병합 대기 ②07-17 아침 확인 2건 — 08:15 flight projection 필드(04 모델 07:50 자동복귀 후)+08:30 retro 크론 발화 ③D-NAO-46 잔여=키워드 시간별·순위 필드 타당성→이중 루프 설계 ④성적표 API /retro-scorecard 가동 중(bleeding 82%·starving 18%) ⑤04 pending 840 승인=Jino. 원칙22: 라이브 증거로만.
```
