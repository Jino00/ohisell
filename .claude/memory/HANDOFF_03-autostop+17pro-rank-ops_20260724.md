# 세션 인수인계: 03 자동운영 정지 + 17프로 순위 수동운영 (D-NAO-90·91·92)
> 저장일시: 2026-07-27 18:46 (작업 자체는 2026-07-23~24)
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ⚠️ 이 세션은 07-23~24 작업. 지금은 07-27이고 그 사이 **다른 세션들이 main에서 활동**(wing 3P·ondemand·wing2 RG 등). 아래 prod 상태(특히 03 정지·17프로 입찰)는 07-24 기준이므로, 재개 시 **라이브 재확인 필수**(원칙 22).

## 1. 프로젝트 위치 및 환경
- 워크트리: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/worktrees/loving-brattain-436688` (재생성됨 — 옛 `ex-sprint-post-deployment-2e9656`·`model-usage-structure-a686b5` 경로는 사라짐)
- 브랜치: `claude/ex-sprint-post-deployment-2e9656` (main 기준, 활성 트랙 = 네이버 SA 광고 최적화)
- prod: `sellc.ohitech.co.kr:/home/ubuntu/ohisell`, 실DB `backend/ohisell.db`(1.4GB, `ad_data.db`는 0바이트 방치). 배포 = `scripts/safe_deploy.sh`(CAS). pm2 프로세스 `ohisell-backend`.
- Slack 웹훅: prod `.env` `NAVER_SLACK_WEBHOOK_URL` 설정 완료(라이브 검증됨). 값은 .env에만.
- 트랙 단일 진입: `docs/TRACKS.md` → `docs/tracks/active/track_naver-ad-optimization.md` (확정결정 D-NAO-1~92).

## 2. 이번 세션 완료 목록
- ✅ **D-NAO-90 확정 기록**: 예산 봉투 vs BEP 안전선 충돌(캠페인 10769985) = **BEP 안전선 우선, 봉투 예외 없음**. 코드 변경 0. (커밋 `b677c12`, PR #100 병합)
- ✅ **Slack 웹훅 prod 개통**: `.env`에 `NAVER_SLACK_WEBHOOK_URL` 추가+pm2 재시작+**라이브 실발송 2건 검증**(curl `ok` + 앱경로 `slack_notifier.notify_text` sent:True, Jino 실수신 확인). 역대 발송 0건 해소. (커밋 `078e156`, PR #101 병합)
- ✅ **17프로 소재 입찰 수동 상향(2회, 실집행)**: `nad-a001-02-000000455468669`(그룹 17프로 `grp-a001-02-000000059879629`, 03 소속): 2,290→**3,000**(14:54 KST, change_log 587)→**3,060**(16:35, change_log 590, 상한게이트가 3,700 요청을 상한 3,060으로 캡). useGroupBidAmt=false 유지.
- ✅ **D-NAO-91 확정 기록**: 17프로 3,060 유지·관망. (커밋 `73f9206`, **미푸시**)
- ✅ **03 캠페인 우리 자동운영 정지**(2026-07-24 11:2x KST): `optimizer` ours→**none**(정식 라우터, change_log 639) + `auto_operate` 1→**0**(prod DB 직접 UPDATE, change_log 640 수동기록). 라이브 검증: 03만 변경·04/P_Test/맥세이프 무변경·전 엔티티 status=on(광고 계속 집행)·입찰 전건 무변경.
- ✅ **D-NAO-92 확정 기록**. (커밋 `da3b8e5`, **미푸시**)
- ✅ 메모리 2건 신규: `naver-ad-today-conversion-via-smartstore`, `shopping-avg-rank-masks-head-keyword` (+MEMORY.md 인덱스).
- ✅ 별건 chip 2건 발행: `task_10c8e239`(N배송 BEP 물류비 회계), `task_2fb0dc9b`(킬스위치 위임경로 구멍).

## 3. 확정된 결정사항 (번복 금지 — Jino 승인)
- **D-NAO-90**: 봉투가 증액 제안해도 BEP 안전선이 항상 우선. 적자 캠페인 증액은 목적함수(D-NAO-59) 역행.
- **D-NAO-91**: 17프로는 3,060원(BEP 안전 최대치)에서 관망. **top-5는 Jino의 firm 요구**("어쨌든 오등 안에 들어가 있어야")이나 현재 경제성상 top-5가 BEP 상한(3,060) 초과 → **BEP 우선, 한계적자 안 냄, ~6위 수용**. Jino 원문: "3060원으로 기다려보자". top-5 재개방 조건 = 90일 RPC 회복 또는 BEP 개선.
- **D-NAO-92**: 03 우리 자동운영 정지(광고는 계속). Jino 원문: "운영중이던 03 캠페인 정지해줘"→"우리 자동운영만 정지". **재가동은 Jino 지시 있을 때까지 유지.** 04·P_Test·맥세이프는 계속 자동운영.
- **KX(D-NAO-88)**: 승인됨, 07-24 크론 무인 확인 후 착수 예정(아직 미착수).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_naver-ad-optimization.md` | 활성 트랙, D-NAO-1~92 확정결정 |
| `claude-progress.txt` | 직전 세션 상태(최신 = D-NAO-92 블록) |
| `backend/app/services/naver_ad/naver_execution_harness.py` | 광고 쓰기 최종 관문(optimizer 하드체크·킬스위치) |
| `backend/app/services/naver_ad/delegation_gate.py` | ⚠️ 위임 게이트 — auto_operate 미검사(chip task_2fb0dc9b) |
| `backend/app/services/naver_ad/bep_calculator.py` | BEP 계산(N배송 물류비 낙관가정, chip task_10c8e239) |
| `backend/app/services/naver_ad/exploration.py` | [탐색UP] 래더 — 어제 03 적자 유발 레인, 04 등서도 동일 가동 |
| `backend/app/models.py` | `NaverCampaignSettings`(optimizer·auto_operate) |

## 5. 알려진 이슈 / 주의사항
- **★03 정지는 두 스위치를 다 내려야 완전 정지**: `auto_operate`만으로는 전문가 위임(delegation) 자동실행 경로를 못 막음(delegation_gate가 optimizer만 검사, harness 킬스위치 화이트리스트에서 delegation 명시적 제외). 현재 위임 비활성(`expert_delegated_types=[]`)이라 실피해 0이었으나 구조는 뚫려 있었음. **다음 세션이 auto_operate만 보고 "정지됨" 오판 금지.** 코드 수정 = chip task_2fb0dc9b.
- **★쇼핑 avg_rank는 머리 키워드를 가림**: 03 그룹 avg_rank 3.7~4.8은 롱테일이 끌어올린 평균, 머리 키워드 "아이폰17프로강화유리"는 실제 ~6위(07-22 5.61). 판단을 그룹 평균으로만 하지 말 것. 메모리 `shopping-avg-rank-masks-head-keyword`.
- **당일 전환/매출/ROAS는 스마트스토어 실주문으로** 즉시 조회(광고 D+1 안 기다림): `orders`(channel 6)×`naver_adgroup_product`. 단 상한 프록시. 메모리 `naver-ad-today-conversion-via-smartstore`.
- **자동 브라우징으로 네이버쇼핑 SERP 조회 불가**: 인앱 브라우저=도메인 policy 차단, gstack /browse=네이버 봇차단(HTTP 418). 머리 키워드 실순위는 익일 배치(`naver_search_term_daily`) 또는 Jino SERP로만.
- **BEP 상한은 90일 캠페인 RPC에 지배**되며 창에 따라 흔들림(90일 4,750→상한 3,060 / 한때 5,795→3,740). RPC는 캠페인 블렌디드라 머리 키워드 단독 경제성과 다름.
- **미푸시 커밋 2개**: `73f9206`(D-NAO-91)·`da3b8e5`(D-NAO-92) — 로컬만. push/PR 여부 Jino 미결.

## 6. 다음에 할 작업 (미완료)
- [ ] **미푸시 문서 커밋 2개(D-NAO-91·92) push→PR** — Jino가 "정리해서 올릴까요?"에 아직 미응답.
- [ ] **04·P_Test·맥세이프 어제(07-23) 성과 점검** — 03 적자 유발한 [탐색UP] 레인이 이들에서도 동일 가동 중. "03만의 문제냐 레인 자체 문제냐"를 가르는 점검(Jino에게 제안했으나 미응답).
- [ ] **07-24 EX·예산봉투 아침 크론 무인 판정 상세 확인** — 크론 무인 가동 자체는 실증됨(아침 배치 정상+11:20 자동 실집행). 단 EX/봉투가 어떻게 판정했는지 상세는 미확인. 03 정지됐으니 04 등 기준.
- [ ] **KX(D-NAO-88) 착수 여부** — 무인 확인됐으니 Jino 승인 시 설계 착수.
- [ ] chip 2건 처리: `task_10c8e239`(N배송 BEP 회계), `task_2fb0dc9b`(킬스위치 위임 구멍).
- [ ] **재개 시 prod 상태 라이브 재확인**(07-24 이후 다른 세션 활동으로 03 정지·17프로 3,060이 유지되는지).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_03-autostop+17pro-rank-ops_20260724.md 읽고 이어서 작업해줘.
핵심: 03 우리 자동운영 정지(D-NAO-92, optimizer=none+auto_operate=0)·17프로 3,060 관망(D-NAO-91)·봉투vsBEP=안전선우선(D-NAO-90) 완료. 미푸시 문서커밋 2건(D-NAO-91·92) 있음.
지금은 07-27이라 03 정지·17프로 입찰이 아직 유지되는지 prod 라이브 먼저 재확인. 다음 후보: 미푸시 push, 04 등 나머지 캠페인 [탐색UP] 성과 점검, KX 착수 여부.
```
