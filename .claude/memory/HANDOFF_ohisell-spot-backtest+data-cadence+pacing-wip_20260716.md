# 세션 인수인계: 순위밴드 스팟 백테스트 + 데이터 실시간성 완결 + MOP bidYn=N 정정 + pacing 배선 WIP
> 저장일시: 2026-07-16 19:10 (KST)
> 새 대화 시작 시 이 파일 먼저. 트랙: `docs/tracks/active/track_naver-ad-optimization.md`.
> 직전: `HANDOFF_ohisell-mop-D3-warmup-still-idle_20260716.md`(같은 날 아침 — **그 파일의 "웜업 미탈출" 해석은 이 세션에서 정정됨**, §2-A).
> 발견 밀도가 매우 높은 세션. **§3 확정사항과 §5 주의사항(특히 sentinel 집계 규약)은 다음 세션 필독.**

## 1. 프로젝트 위치 및 환경
- 워크트리: `Ohiselling/.claude/worktrees/exciting-liskov-681358`, 브랜치 `claude/naver-ad-execution-loop-4124ce`. main==prod(PR#21 병합). **미커밋 변경 있음(§2-E pacing WIP)**.
- prod: `ssh sellc.ohitech.co.kr`, 포트 8001(pm2 id0), DB `/home/ubuntu/ohisell/backend/ohisell.db`. SA API: `cd backend; set -a; . .env; set +a; PYTHONPATH=. .venv/bin/python3 …`
- 백테스트 산출물·MOP 도구: 워크트리 `naver-ad-execution-loop-6cc75b/docs/references/data/mop_ui/` (트랙 부채, 미머지).
- MOP: 콘솔 mop.co.kr / API be.mopapp.net / **advertiserId=756**(sessionStorage 기본 1422=Google Ads니 주의). 토큰 `~/.gstack/mop_session`(07-16 Jino 로그인 세션 — 만료 가능, `mop_token.sh get`으로 시험). gstack 헤디드 Chromium에 MOP 로그인 상태로 열려 있음(닫혔으면 재로그인=Jino만).

## 2. 이번 세션 완료 목록

### A. MOP 내부 직접 실측 → "웜업" 해석 정정 (아침 HANDOFF 뒤집음)
- Jino 로그인 세션으로 be.mopapp.net 직접 조회(증거 `mop_internal_*_20260716.json` 5파일): 유닛 **6245(03 쇼핑)·5752(아이패드 파워링크) 둘 다 status=INSPECTION_COMPLETED·bidYn=N**. 엔진: 예측모델 40개·predicted 145그룹·**inBidding 24그룹(03 편입됨)**이나 **planning runCount 0·flight 24h 입찰 0**. bidStartDate=20260713은 지났는데 bidYn 여전히 N.
- **판정(실측): MOP는 "준비"만(검수완료+그룹편입+모델) 하고 입찰을 켠 적이 없음 = 웜업이 아니라 입찰 활성화 미완.** SA API 3일 "조정 0"의 진짜 이유. → **MOP 학습 데이터는 Jino가 콘솔서 bidYn=Y 켜기 전까지 영원히 0**(재정 액션, Claude 금지).
- 03 캠페인 자체 성과는 매일 축적 중(cmp-…008492582) — 그건 MOP 무관 데이터.

### B. 04(우리 카나리) 상태 규명 + Ava 역할 정정
- 04 optimizer=ours(07-12~). **우리가 입찰 안 바꾼 이유 3겹**: ①07-15까지 쇼핑 실행 손 부재(D-NAO-43로 해소) ②반자동 설계 — 위임 테이블(naver_account_settings) 비어있음 → 자동실행 원천차단 ③**계정 전체 executed 제안 = 0건**(pending 5+expired 24). 예외=07-15 S4 통제왕복(+10→원복, 순변화 0).
- **Ava 역할 정정(Jino 확인)**: Ava=조언자·감시자(평결 기록·성적표 축적), **의사결정자 아님**. 위임 스위치=영구 Jino(D-NAO-25). 내 "Ava 공백 선결" 표현은 오류. **미확인 과제**: 코드가 자동경로에 Ava 평결을 하드 전제로 물려놨는지(설계 드리프트 여부) — delegation_gate/expert_desk 배선 확인 필요(A=순수조언 vs B=드리프트).

### C. 데이터 실시간성 조사 완결 (문서: `naver_stat_field_cadence_20260716.md`, 6cc75b/mop_ui/)
- **①종류 16종**(/stats datePreset=today): imp·clk·cost(salesAmt)·**순위3종(avgRnk/pcNx/mblNx)**·ccnt·convAmt·viewCnt + 파생 ctr/cpc/crto/cpConv/**ror(ROAS 직접 제공)**.
- **②갱신주기 = 전 필드 정확히 1시간**(15분 폴러 8h·6캠페인 실측, 간격분포 [43,45,60,75]=매시 갱신+양자화). **순위도 60분 — "순위=빠른신호" 가설 폐기.** 15분 폴링 무의미.
- **③완결도 곡선 v2(v1 45%는 sentinel 이중계산 오류 — 정정)**: **23시=91.3%[87.7~94.2]·18시=64.8%·12시=28.0%**(19표본). 하루끝 ~91% = 실효 보고지연 1~2h로 작음, 곡선 대부분은 저녁쏠림 지출분포. **보정계수 ×1.09@23시~×3.6@12시, 정오 이후 유효(오전 ×10+ 비권장)**.
- **④전환 = 65~70% 간접, 어트리뷰션 ~1일 정착**(과거 열흘 고볼륨 실측 — 간접%가 날짜나이 무관 평평). 당일 ROAS 불가(직접~30%만), +1일 확정이면 충분.
- 실무 결론: 실시간(분단위) 최적화 데이터는 없음. 당일치는 보정계수로 사용 가능(Jino: "다들 같은 지연 데이터로 싸운다"). MOP도 동일 제약 → 승부처=판단 품질.

### D. ★순위밴드 스팟 백테스트 (문서: `naver_rank_band_backtest_20260716.md` v1~v3, 6cc75b/mop_ui/)
- 방법: 과거 12일(07-04~15) 키워드 4,731·쇼핑그룹 411, 상세행만(imp≥20·rank_sum>0), 평균순위=rank_sum/imp. 재현 /tmp/bt1~4.py(VM).
- **결론: 이익 극대 스팟 = 평균순위 2.5~4(3등 밴드)** — 쇼핑에서 매출밀도(51,835/1k노출)·ROAS(221%)·CVR(19.6%)·**진짜 공헌이익 절대금액(+3,269원/그룹일)** 전부 1위(완전 지배). 1등 밴드=ROAS 61% 적자(n=9 약함). 파워링크=1등이 매출최대나 ROAS 151%<BEP → 역시 3등 밴드(186%)가 답.
- **진짜 BEP 반영(v2)**: 계정 매출가중 BEP-ROAS=**1.4758**(마진 67.8%, 상품 506개; target 1.697은 공격성 포함값). 그룹별 개별마진 = 쇼핑 그룹명↔상품명 매칭 **124/1002 그룹 실증**(resolver "②상품BEP 연결 미구현" 갭을 채울 수 있는 경로 발견 — 구현 후보).
- **Jino 가설("ROAS 낮춰도 볼륨↑→이익↑") 정량 판정**: 2등 밴드=노출 2.4배지만 ROAS 160%는 BEP 148%에 근접해 단위마진 1/7 압축 → 이익금액 1/3. **허용 폭 = 한계 ROAS ≥ BEP(1.48), 평균 ROAS ~180-200%선까지. 160%는 초과.**
- **성장 기회 정량화**: 쇼핑 6등+ 밴드에 그룹-일 771표본이 ROAS 218%로 건강하게 굶는 중 = 성장 보드 최우선 타깃 풀.
- **한계(정직)**: 횡단면 — 밴드 간 선택효과(노출/그룹일 비단조가 증거), within-키워드 검증 약함(42%). "옮기면 재현" 보장 아님 → **카나리 실집행 검증 필수**.
- **04 pending 5건 대조(v3)**: 5건 전부 밴드 논리 방향 정합(전부 4~5등/6등+ & ROAS≫BEP — 백테스트가 지목한 풀을 보드가 독립적으로 찍음). 서열: **840(17E, 1500→2090 +39%, 순위 4.2, ROAS 214%·15클릭)=교과서적 1순위** ≫ 836·837(1~2클릭 고ROAS) > 838(7일 0클릭) > **839(50→2200 +4300% 스텝 이상 — 그룹입찰 50=기본값인데 순위 4.1=실효입찰 소재단위 추정, 실행 전 소재 구성(useGroupBidAmt) 확인 권장)**.

### E. 보정계수 pacing 배선 — WIP (Jino 승인 "구현은 Sonnet")
- **현 상태(라이브 확인)**: `backend/app/services/naver_ad/completeness_curve.py`(신규)+`flight_loop.py`(+88/-20 수정)+`tests/test_completeness_curve.py`(13 pass)+`docs/PLAN_naver-ad-pacing-correction.md`. **전부 미커밋. codex 리뷰 미실시·미배포.** flight는 dry-run이라 prod 영향 0.
- 다음 세션: codex review → 커밋 → (Jino) 배포 판단. **"됐다" 금지 — 코드 존재·로컬테스트까지만 사실**(원칙22).

### F. 운영·기록
- **모델 라우팅 3단 확정(Jino)**: 구조/설계=Fable·하위작업=Opus·단순=Sonnet(메모리 `model-routing-fable-opus-sonnet`).
- failures.jsonl 3건(데이터 지연 오판 2건+sentinel 이중계산). 비교로그·progress·트랙 갱신.

## 3. 확정된 결정사항 (번복 금지)
- **이익 극대 스팟 = 순위 2.5~4 밴드 + 한계 ROAS ≥ BEP(1.4758)** — 세 채점(매출·ROAS·이익금액) 일치. 단 횡단면 prior → 카나리로 인과 승격 필요.
- **계정 진짜 BEP-ROAS = 1.4758(순수 손익분기)** vs target 1.697(공격성 포함) — 혼동 금지.
- **네이버 데이터: 전 지표 1시간 주기·완결도 v2 곡선·전환 ~1일 정착·실시간 없음.** 보정계수는 정오 이후만.
- **MOP 유닛은 bidYn=Y 전까지 아무것도 안 함**(웜업 아님). 활성화=Jino 콘솔(재정).
- **Ava=조언자, 결정=Jino**(D-NAO-25 재확인). 모델 라우팅 3단.
- D-NAO-1 이익하한·기존 트랙 결정 전부 유효.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `6cc75b…/mop_ui/naver_rank_band_backtest_20260716.md` | ★스팟 백테스트 v1~v3(밴드표·진짜이익·5건 대조) |
| `6cc75b…/mop_ui/naver_stat_field_cadence_20260716.md` | ★데이터 실시간성(16종·1h·완결도 v2) |
| `6cc75b…/mop_ui/mop_internal_*_20260716.json` | MOP 내부 실측 증거(bidYn=N) |
| `6cc75b…/mop_ui/mop_vs_ours_03_04_comparison.md` | 관찰 로그(D3+내부실측 append) |
| 이 워크트리 `backend/…/completeness_curve.py`+`flight_loop.py`+테스트 | pacing 배선 WIP(미커밋) |
| `docs/PLAN_naver-ad-pacing-correction.md` | pacing 계획서(미커밋) |
| VM `/home/ubuntu/ohisell/backend/stat_cadence_probe.py`+`.jsonl` | 15분 폴러(크론 */15 가동 중)+캡처 로그 |
| VM `/tmp/bt1~4.py`·`/tmp/curve.py` | 백테스트 재현 스크립트 |

## 5. 알려진 이슈 / 주의사항
- ★★**naver_ad_daily 집계 규약**: sentinel 캠페인행(`adgroup_id='__backfill__'`)과 상세행 공존 — **집계 시 반드시 택일**(전행 SUM=2배). 확정치=sentinel행, 세부=상세행. 이 세션 초반 Jino에게 보인 03/04/425541 일별표도 2배 인플레였음(비율·상대비교는 유효). failures.jsonl 기록.
- **MOP 토큰**: `~/.gstack/mop_session`은 Jino 로그인 세션 의존 — 데이터 엔드포인트가 SESSION_EXPIRE 나오면 Jino 재로그인 필요. advertiserId는 **756 명시** 필수.
- **예약 루틴 2개**: ①`naver-stat-field-cadence-analysis-0716`(07-17 09:30) = 완결도 v2 out-of-sample 검증+**임시 폴러 크론 제거**. ②`mop-5752-6245-warmup-exit-check-0722`(07-22 09:00) = **프레이밍 구식**(웜업 전제였는데 실상은 bidYn=N) — bidYn=Y가 안 켜져 있으면 "조정 0"은 당연한 결과이니 그 루틴의 해석 주의(Jino가 켜기 전엔 사실상 무의미).
- 폴러 크론(`*/15 stat_cadence`)이 VM에서 계속 돌고 있음 — 07-17 루틴이 정리 예정, 안 되면 수동 제거.
- 04 카나리 optimizer=ours 유지 중. pending 5건은 만료 TTL 있음(정보성 아닌 실행형이라 유지되나 확인 권장).
- 워크트리 부채: 백테스트·관찰 산출물이 6cc75b에만(untracked). 언젠가 커밋 정리.

## 6. 다음에 할 작업 (미완료)
- [ ] **(Jino 게이트) 04 pending 승인**: 권장 840 먼저(첫 카나리 겸 밴드 인과 검증) → 승인 시 D+2~3 순위·ROAS 추적 루틴 걸기. 839는 승인 전 소재 구성 확인.
- [ ] **(Jino 게이트) MOP bidYn=Y 활성화**(콘솔) → 켜지면 감지기가 자동 포착 → 학습 데이터 시작.
- [ ] **pacing 배선 마무리**: codex review → 커밋 → 배포(Jino) (§2-E, 현재 미커밋 WIP).
- [ ] (승인 후 구현 후보, Sonnet) 성장 보드 "목표 순위 밴드 2.5~4" 로직 / 그룹별 BEP 이름매칭 연결(resolver ② 갭).
- [ ] Ava 배선 확인(A=순수조언 vs B=자동경로 하드전제 드리프트) — delegation_gate/expert_desk.
- [ ] 07-17 09:30 루틴 자동 실행 확인(곡선 검증+폴러 정리).

## 7. 새 세션 시작 프롬프트
```
.claude/worktrees/exciting-liskov-681358/.claude/memory/HANDOFF_ohisell-spot-backtest+data-cadence+pacing-wip_20260716.md 읽고 이어서. 라우팅: 구조=Fable·하위=Opus·단순=Sonnet. 핵심 상태: ①이익극대 스팟=순위 2.5~4밴드+한계ROAS≥BEP 1.4758 실측(카나리 검증 대기) ②04 pending 5건 대조완료(840 최우선, 839는 소재확인 먼저) — 승인=Jino 콘솔 ③MOP 6245/5752는 bidYn=N(웜업 아님, Jino 활성화 전 데이터 0) ④pacing 보정 배선 미커밋 WIP(codex부터) ⑤naver_ad_daily 집계=sentinel/상세 택일(2배 함정) ⑥07-17 09:30 곡선검증 루틴 예약됨. 원칙22: 라이브 증거로만.
```
