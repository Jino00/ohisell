# 세션 인수인계: A안 MOP 관찰 D+1(계속) — SPA 엔진·목표입찰 메커니즘 완전 매핑 + MOP는 day-1엔 작업불가

> 저장일시: 2026-07-12 오후 (KST). 직전 = `HANDOFF_ohisell-mop-A-D1-observation_20260712.md`.
> 새 대화 시작 시 이 파일 먼저. **★B안 실행됨: MOP 유닛 6245 생성(03.아이폰_강화유리, CLICK/GROWTH/42,130원, 07-13 집행시작). 다음 = 07-13부터 MOP가 03을 어떻게 성장시키는지 매일 관찰(입찰횟수·입찰계획·소재입찰가·예측·리포트)→우리판 MOP 복제. 관찰로그 `mop_unit_6245_growth_observation.md`.**
> 트랙: `docs/tracks/active/track_naver-ad-optimization.md` **D-NAO-42-c**. 산출물 전부 워크트리 `naver-ad-execution-loop-6cc75b`(일부 untracked).

## 0. 한 줄 요약
A안 D+1(계속). MOP의 SPA 엔진 경로·목표입찰 메커니즘을 완전 매핑하고, **MOP의 노출 레버가 전부 "2주 이력" 게이트라 켠 지 하루된 우리 캠페인엔 지금 할 수 있는 게 없음**을 실증. ★03.6H사생활 소재는 정지가 아니라 소재 연동 비정상(AD_ABNORMAL_INTERLOCK)이라 토글로 안 풀림(당초 PAUSE 해제 안은 무효). "시동"의 본질=돈 넣기가 아니라 MOP 자격 여는 2주 이력 쌓기.

## 1. 환경 (직전과 동일)
- 워크트리: `.../Ohiselling/.claude/worktrees/naver-ad-execution-loop-6cc75b`. 세션 shell cwd는 `zen-pasteur-c744b8`였으나 **작업·산출물은 전부 이 워크트리**.
- 브라우저: gstack `$B="$HOME/.claude/skills/gstack/browse/dist/browse"`, 포트 34567. ★**데몬이 헤드리스로 자동재시작되면 Jino가 로그인할 창이 없음** → `$B connect`로 **헤디드(보이는) Chromium** 띄운 뒤 Jino 로그인. MOP 로그인은 Jino만(비번 폼=안전규칙).
- MOP: 콘솔 `mop.co.kr`, API `be.mopapp.net`, advertiserId=**756**. 인증 `x-session-id: sessionStorage.sessionId`. **광고주 전환=`sessionStorage.setItem('advertiserId','756')`**(UI 헥사곤 클릭은 pointer-events 미수신으로 실패). MOP API 캡처는 **반드시 비동기 fetch**(동기 XHR=크래시).
- prod: `sellc.ohitech.co.kr`, `/home/ubuntu/ohisell`, DB `backend/ohisell.db`(read-only). **SA API 애드혹 호출**: `set -a; . backend/.env; set +a && PYTHONPATH=backend backend/.venv/bin/python3`(env 안 실으면 서명 빈값=403).

## 2. 이번 세션 완료 (전부 라이브 실측, 원칙22)
- ✅ **날짜 정정**: 오늘=**2026-07-12**(D0=07-11). 세션 중 07-13 오기 전부 07-12로 정정(파일명 20260712). 07-13 조회는 미래라 `-`였음.
- ✅ **켠~지금 궤적**: 07-11 imp181·clk0·rank4.07 / 07-12부분 imp57·clk0·rank7.21 = 238노출·0클릭·하루치. → "CTR0%=소재문제" 결론 **철회**(표본 과소).
- ✅ **SPA 엔진 경로 확정**(`mop_engine_outputs.md §5`): `/dashboard/saShopping/*`=존재안함(전부500). SPA=`/v1/optimizations/sa/shopping/*`(후보 adgroups: `active/predicted/inBidding/dailyBudget`)·`/v1/report/opt/*`.
- ✅ **MOP 판단신호**: 우리 3그룹 `predicted:false·inBidding:false`(계정 450그룹 중 predicted150·inBidding0). 이력0→MOP가 ML 예측모델조차 안 만듦.
- ✅ **4099 최적화 리포트=소멸**: report/opt 보존 05-13~07-11(~2개월), 4099 활동기(2025-06~2026-04) 밖 → 전지표0/`-`. 남은 건 설정 스냅샷 `spa_opt_4099_detail_20260712.json`(ROAS·EXPERT·132그룹·kpiValue -1=하드숫자 미설정 → D+1 "EXPERT=명시적 숫자목표" 부분 정정).
- ✅ **★목표입찰(target-bidding) 메커니즘 완전 매핑**(§5-d): `/target-bidding`, `GET /v1/rank-maintenance/shopping/ad`(0/1 등록). 폼=소재1개 + 입찰기준키워드 + **목표(Avg Rank/ROAS/CPA)** + 최대입찰가·변동폭 → MOP가 스텝으로 상한 내 목표 향해 자동입찰. **= 우리 naver_ad BEP-ROAS 다이얼+가드레일과 사실상 동형**(소재/키워드 레벨).
- ✅ **소재 ID=SA API로 확보**(UI 로그인 불필요): `/ncc/ads?nccAdgroupId=` → nccAdId. `00iphone17_soljae_ids_20260712.md`.
- ✅ **★실측 3건**:
  1. **03.6H사생활 0노출 원인=소재 4개 연동 비정상**(nad-...419880022~025; `status=PAUSED, statusReason=AD_ABNORMAL_INTERLOCK`="소재 연동 상태 비정상"). ★ON/OFF 토글 ON·userLock=false → **정지가 아님**(토글로 안 풀림). ⚠️처음 status만 보고 "PAUSED=정지"로 오보고→statusReason으로 정정.
  2. **현재입찰가=1,390원**(MOP `/v1/ads/{nccAdId}`). HANDOFF "50원"은 애드그룹 기본값; hero 소재는 1,390원 → 낮은입찰이 0클릭 원인 아님.
  3. **목표입찰 등록 시도→저장 불가**: hero 소재 `nad-...739856`(01.강화유리, 132노출) 입력→MOP 정상인식(productTitle·campaign 로드)하나 **keywordStats=[]**(2주 키워드실적 없음)→입찰기준키워드 선택불가→**저장 disabled**.
- ✅ **★★최종 결론**: MOP 노출 레버 **둘 다 history-gated** — ①입찰최적화(성장)=7일평균예산+predicted 모델, ②목표입찰=2주 키워드실적. **day-1 캠페인엔 MOP가 지금 할 수 있는 작업 없음.** 레버 열림=~2주 이력 선행. "시동" 본질=돈 아니라 이력.

## 3. 확정 결정(불변)
- 자동화 경로=MOP 유지(A안). 대상=00.아이폰_17(cmp-a001-02-000000009793536). 저장/시작(돈)은 Jino 명시 확인 후만.
- **애드혹 라이브 쓰기 금지**: 광고 계정 변경은 우리 harness(가드레일·위임게이트) 통해서만. 수동 조치는 Jino가 광고주센터에서.
- BEP-ROAS 하한 유지 기본(D-NAO-1).

## 4. 핵심 파일 (전부 `docs/references/data/mop_ui/`)
| 파일 | 역할 |
|---|---|
| `mop_engine_outputs.md` | ★메커니즘 정본(§1 SA엔진·§2 최적화유닛·§5 SPA엔진/목표입찰/소재ID/블로커) |
| `mop_live_A_00iphone17.md` | A안 관찰일지(D0~D+1계속, 궤적·판단·결론) |
| `00iphone17_soljae_ids_20260712.md` | 소재ID·03 연동이상(AD_ABNORMAL_INTERLOCK)·hero소재(739856) |
| `spa_opt_4099_detail_20260712.json` / `spa_candidate_adgroups_20260712.json` | 원천 캡처 |
| `mop_engine_outputs_collection_20260712.md` | ★엔진 산출물 전량 수집(예측모델40·키워드30810·입찰계획0·플라이트0·predicted SA142/SPA150) |
| ★`mop_unit_6245_growth_observation.md` / `spa_unit_6245_detail_20260712.json` | ★MOP 유닛 6245(03) 성장 관찰 로그·설정 |
| `mop_unit_candidates_20260712.md` | 유닛 후보(predicted SPA 캠페인별 지출) |
| `sa_candidate_adgroups_20260712.json`(90KB) | SA 예측대상 후보 트리(512그룹·predicted142) |
| `soljae_snapshot.py` / `soljae_daily_observation.md` | 소재 단위 일별 관찰 도구·베이스라인 |

## 5. 다음 작업 (D+2~, 2주 관찰) — ★Jino 결정 반영
- **Jino 결정 2건**: ①**03 상품 살리지 않고 현 상태 유지**(연동 비정상 상태만 추적, 액션 없음). ②**나머지 소재 전부 관찰**(hero 하나 아니라 **광고그룹 3개(01·02·03) 소재 12개 전부** 소재 단위 — 셋 다 운영가능·ON. 01 활발서빙·02 저노출·03 연동비정상).
- [x] **소재 단위 관찰 셋업 완료**: `soljae_snapshot.py`(재사용 도구) + `soljae_daily_observation.md`(D+1 베이스라인, 소재 12개 상태·입찰·성과).
  - 실행: `ssh sellc.ohitech.co.kr 'cd /home/ubuntu/ohisell; set -a; . backend/.env; set +a; PYTHONPATH=backend backend/.venv/bin/python3 -' < soljae_snapshot.py <since> <until>`
- [ ] **매일 소재 스냅샷 append**(status+statusReason+입찰가+노출/클릭/비용/순위). 변화(클릭 발생·상태 변동) 시 Jino 보고.
- [ ] **~2주 후 MOP 레버 열리는지 재확인**: 목표입찰 keywordStats 채워지는지·후보목록 predicted:true 되는지·이지모드 7일평균예산>0 되는지.
- [ ] (참고, Jino 전략 몫) 소재별 실입찰 상이(01=790~1,390 서빙·02=300 rank20 미노출), 살아있는 8소재 전부 238노출·0클릭(하루치). 02 저노출·클릭0 지속 관찰.

## 6. Jino 대기 / 판단
- 03: 현 상태 유지 확정(살리지 않음). 별도 대기 없음.
- 관찰: 광고그룹 3개(01·02·03) 소재 12개 매일 스냅샷 축적(전부 ON, 03은 연동비정상 상태 추적), 유의미 변화 시 보고.

## 7. 새 세션 시작 프롬프트
```
.claude/memory/HANDOFF_ohisell-mop-A-mechanisms-mapped_20260712.md 읽고 MOP 유닛 6245 성장 관찰 이어서 진행해줘 (advertiserId=756, 03.아이폰_강화유리를 MOP가 07-13부터 어떻게 성장시키는지: 입찰횟수·입찰계획·소재입찰가변화·예측·리포트 매일 수집→우리판 MOP 복제. 로그 mop_unit_6245_growth_observation.md)
```
