# 세션 인수인계: MOP 유닛 6245 생성 — 03 성장 관찰 시작 (B안 실행)
> 저장일시: 2026-07-12 저녁 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것.
> **★핵심: MOP 최적화 유닛 6245를 실제 생성·저장 완료(03.아이폰_강화유리, 성장/클릭, 42,130원, 입찰시작 07-13). 다음 = 07-13부터 MOP가 03을 어떻게 성장시키는지 관찰.**
> 트랙: `docs/tracks/active/track_naver-ad-optimization.md` **D-NAO-42-d**.
> 직전 HANDOFF(메커니즘 매핑 상세): `HANDOFF_ohisell-mop-A-mechanisms-mapped_20260712.md`.

## 0. 목적 (Jino 교정 — 절대 흐리지 말 것)
- 우리 목적 = **MOP가 실제로 어떻게 네이버 광고를 최적화(성장)하는지 학습해 우리판 MOP로 복제.** (네이버 광고 잘 돌리기 아님.)
- 00.아이폰_17은 관찰용 probe였으나 MOP 밖(predicted:false, inBidding:false)이라 관찰 불가 규명 → **이력 있는 실상품에 MOP 유닛을 켜야 관찰 가능** → 03에 유닛 6245 생성.
- ★**네이버 캠페인 켜기 ≠ MOP 최적화**: MOP는 명시적 '최적화 유닛'을 생성·저장해야 입찰계획·플라이트(실입찰)를 산출한다.

## 1. 프로젝트 위치 및 환경
- 워크트리: `.../Ohiselling/.claude/worktrees/naver-ad-execution-loop-6cc75b` (세션 shell cwd는 `zen-pasteur-c744b8`였으나 **작업·산출물 전부 이 워크트리**, 일부 untracked).
- **MOP**: 콘솔 `mop.co.kr`, API `be.mopapp.net`, advertiserId=**756**(오하이_구민정, Basic Plan). 인증 헤더 `x-session-id: sessionStorage.sessionId`. 광고주 전환=`sessionStorage.setItem('advertiserId','756')`(UI 헥사곤 클릭 실패). API 캡처는 **반드시 비동기 fetch**(동기 XHR=크래시). MOP 로그인은 **Jino만**(비번폼 안전규칙).
- **gstack 브라우저**: `$B="$HOME/.claude/skills/gstack/browse/dist/browse"`, 포트 34567. 헤드리스로 자동재시작되면 `$B connect`로 **헤디드 창** 띄워야 Jino 로그인 가능. ⚠️위저드 조작 중 반복 크래시/로그아웃(§5).
- **prod SA API(자동 수집, 로그인 불필요)**: `ssh sellc.ohitech.co.kr 'cd /home/ubuntu/ohisell; set -a; . backend/.env; set +a; PYTHONPATH=backend backend/.venv/bin/python3 -' < script.py` (env 안 실으면 서명 빈값=403. venv=backend/.venv).

## 2. 이번 세션 완료 목록
- ✅ **MOP 작동원리 완전 매핑**(직전 HANDOFF 상세): SPA 엔진 경로(`/optimizations/sa/shopping/*`·`/report/opt/*`; `/dashboard/saShopping/*`=없음500), 목표입찰(소재/키워드 Avg Rank/ROAS/CPA+상한+스텝=우리 BEP-ROAS+가드레일 동형), 예측모델 40개·키워드 30810·predicted SA142/SPA150.
- ✅ **이지모드 예산 메커니즘 확정**: 예산=선택 캠페인 **7일평균 실지출**(`POST /v1/optimizations/sa/shopping/costs`). **네이버 하루예산 캡과 무관**(Jino가 03=5만·04=3만 바꿔도 MOP는 7일평균 사용; MOP는 캡을 stale 200K로 표시=데이터 지연). 성장운영 슬라이더로 7일평균 +20%(공격적 더 가능). 전문가모드(예산직접입력)=Lite/Pro 잠금. Basic=유닛1개·애드그룹30개 한도.
- ✅ **★MOP 유닛 6245 생성·저장**(Jino 승인, `POST /v1/optimizations/sa/shopping/easy`): 03.아이폰_강화유리 24그룹, 목표 **CLICK**·모드 **GROWTH**(성장운영=클릭최적화), 하루예산 **42,130원**(7일평균 35,110 +20%), 입찰시작 **2026-07-13**, 종료없음, 상태 **INSPECTING**(검수).
- ✅ **유닛 후보 분석**: predicted SPA 150그룹 30일 지출순(01.갤럭시_지문방지_TPU 최다 171K). Jino 선택=03+04지만 30한도로 03만 24그룹. (`mop_unit_candidates_20260712.md`)
- ✅ **03 소재 baseline 캡처**: 03 24그룹 소재 입찰가(MOP 이관 前). 07-13 대조용.
- ✅ **엔진 산출물 전량 수집·왜 활성유닛 없었나 규명**: 4099가 2026-06-17 정상종료(errorStatus null) 후 미갱신이 원인. (`mop_engine_outputs_collection_20260712.md`)
- ✅ 기록: 트랙 D-NAO-42-d, progress, 관찰로그, MEMORY 전부 갱신.

## 3. 확정된 결정사항
- **자동화 경로=MOP 유지, 학습·복제 목적**(네이버 운영 아님).
- **유닛 6245 생성 확정**(03, CLICK/GROWTH/42,130원). 되돌리려면 유닛 중단(Jino).
- **저장/시작(돈)은 Jino 명시 확인 후만.** 애드혹 라이브 쓰기 금지(harness/위임게이트 경유 또는 Jino 수동).
- 00.아이폰_17: 03.6H사생활 소재는 현 상태 유지(연동 비정상 AD_ABNORMAL_INTERLOCK=연동상품 삭제, 살리지 않음).
- BEP-ROAS 하한 유지 기본(D-NAO-1).

## 4. 핵심 파일 목록 (전부 `docs/references/data/mop_ui/`)
| 파일 | 역할 |
|------|------|
| ★`mop_unit_6245_growth_observation.md` | ★유닛 6245 성장 관찰 로그(매일 append) |
| `spa_unit_6245_detail_20260712.json` | 유닛 6245 설정 원본 |
| `mop_unit_candidates_20260712.md` | 유닛 후보(predicted SPA 캠페인별 지출) |
| `mop_engine_outputs.md` | MOP 메커니즘 정본(SA엔진·최적화유닛·SPA·목표입찰·소재ID·이지모드예산) |
| `mop_engine_outputs_collection_20260712.md` | 엔진 산출물 전량+왜 활성유닛 없었나 |
| `soljae_snapshot.py` | 소재단위 성과/입찰 스냅샷 도구(재사용) |
| `mop_live_A_00iphone17.md` | 00.아이폰_17 관찰일지(probe, MOP밖) |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **MOP 콘솔 관찰은 Jino 로그인 필요**(비번폼 안전규칙). 자동 불가. gstack 세션 자주 크래시 → `$B connect` 후 재로그인.
- ⚠️ **MOP의 실제 입찰변경·성과는 네이버 SA API에 그대로 나타남**(MOP가 네이버에 입찰을 밀어넣으므로) → 로그인 없이 자동 수집 가능. 이게 "MOP가 실제로 어떻게 운영했나"의 핵심.
- ⚠️ SPA 유닛의 플라이트/입찰계획/입찰횟수는 `/dashboard/sa/*`(검색광고)와 별개 — 07-13 집행 시작 시 정확한 SPA 엔드포인트 확정 필요(콘솔 네트워크 캡처).
- ⚠️ 유닛 07-13까지 INSPECTING → 그전엔 모든 MOP 지표 0.
- ⚠️ MOP 캠페인 데이터 지연(네이버 하루예산 변경을 stale하게 표시).

## 6. 다음에 할 작업 (미완료)
- [ ] **2026-07-13 07:00 KST 관찰**(크론 473a33fb 예약 — 이 세션 유지 시 발동; 아니면 새 세션에서 "유닛 6245 관찰 업데이트"): MOP가 밤새 03에 한 **실제 입찰변경(baseline 대조)+성과**(SA API 자동) + MOP 콘솔(로그인 시 유닛상태·입찰횟수·입찰계획·예측·03 predicted/inBidding 편입). "MOP 가동 데이터 vs 내 수집 데이터" 표로 보고.
- [ ] 매일 관찰 로그 append, MOP 성장 궤적 시계열 축적 → 우리판 MOP 복제 설계 반영.
- [ ] SPA 플라이트/입찰횟수 엔드포인트 확정(07-13 집행 후).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-mop-unit6245-growth-observation_20260712.md 읽고 MOP 유닛 6245 성장 관찰 이어서 진행해줘 (advertiserId=756, 03.아이폰_강화유리를 MOP가 07-13부터 어떻게 성장시키는지: 실제 입찰변경·성과=SA API 자동, 콘솔 입찰횟수·입찰계획·예측=로그인 시. 우리판 MOP 복제 목적)
```
