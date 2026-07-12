# 세션 인수인계: A안 MOP 라이브 관찰 D+1 — advertiserId 정정 + 엔진 산출물 캡처

> 저장일시: 2026-07-12 (KST). 직전 = `HANDOFF_ohisell-mop-A-00iphone17-start_20260712.md`(D0/착수).
> 새 대화 시작 시 이 파일 먼저 읽을 것. **다음 = D+2 관찰 + Jino 결정 2건(아래 §6·§7)**.
> 트랙: `docs/tracks/active/track_naver-ad-optimization.md` **D-NAO-42-b**.

## 0. 한 줄 요약
A안(진짜 MOP가 우리 캠페인을 자동입찰 관리하게 하고 관찰) D+1. **직전 세션의 치명적 오류 정정**: 관찰 대상 advertiserId=**756(오하이_구민정=우리 네이버 스마트스토어 광고계정)**, 1422 아님(1422=Google Ads). MOP 엔진 산출물(예측/플라이트) 라이브 캡처 완료. 캠페인은 노출만 나오고 클릭0→지출0이라 MOP 예산0으로 아직 못 돎.

## 1. 프로젝트 위치·환경
- 워크트리: `.../Ohiselling/.claude/worktrees/naver-ad-execution-loop-6cc75b`, 브랜치 `claude/iphone-privacy-mop-automation-eb3b57`(main 기준). ★산출물 전부 이 워크트리(일부 untracked). 세션 cwd는 `suspicious-shaw-b5315f`였으나 작업은 여기서 함.
- 브라우저: gstack `/browse` 헤디드. `$B="$HOME/.claude/skills/gstack/browse/dist/browse"`, 포트 34567. 죽으면 kill 34567점유 PID→`$B connect`. **MOP 로그인은 Jino만**(비번 폼 입력은 Claude 금지 규칙 — 안전규칙, 사용자 허락으로도 불가).
- **MOP: 콘솔 `mop.co.kr`, API 백엔드 `be.mopapp.net`. advertiserId=756(오하이_구민정, OPERATE, Basic plan).** 인증 헤더 `x-session-id: <sessionStorage.sessionId>`. 광고주 목록 `GET /v1/advertisers`(756/1422 Google Ads/1428/780 카카오).
- 우리 prod: `sellc.ohitech.co.kr`(SSH BatchMode 가능), `/home/ubuntu/ohisell`, DB `backend/ohisell.db`(read-only 조회). pm2 `ohisell-backend`. 서버 UTC.

## 2. 이번 세션(D+1) 완료
- ✅ **★advertiserId 정정 1422→756**(원칙22). 직전 HANDOFF가 "756은 틀림 1422 맞다"고 반대로 오기. 1422=Google Ads라 네이버 캠페인 조회 전부 `-`, 이를 "지출0 탓"으로 오귀속했던 것. 763만원 지출 캠페인도 1422에선 `-`. 756으로 정상. **`-`=계정에 데이터 없음**(756에선 cost=0인 우리 캠페인도 imp=181 정상 반환). mop_snapshot.js·관찰로그·트랙·failures.jsonl 수정.
- ✅ **00.아이폰_17 실측(756)**: 07-11 imp=181·clk=0·cost=0(prod adgroup 178+3=181 정확 일치). 7일 동일. 07-12 imp=57·clk0. **이지모드 예산 0원 유지**(7일지출0). MOP adgroupsTree SA_SHOPPING/NAVER=25캠페인에 `● 00.아이폰_17` 활성.
- ✅ **SPA 이지모드 위저드 라이브 진입**(헥사곤 SPA face 클릭→`/shopping-optimization`): 최적화 애드그룹 0/30, 기존 유닛 2개 종료. 운영모드 균형/성장, 하루예산=7일평균(0)기반. 목표입찰은 잠금없음(Basic 가능성).
- ✅ **MOP 엔진 산출물 지도+실데이터 캡처**(Jino 질문). `docs/references/data/mop_ui/mop_engine_outputs.md`. 엔드포인트: `/v1/dashboard/sa/{collection|projection|flight|abnormal}/...`. 실측: **ML 모델 40개 상시생성**, 플래닝·플라이트는 **활성유닛0이라 산출0**. 최적화유닛 `/v1/optimizations/sa/shopping` — 4099(ROAS·EXPERT·193,940·44그룹)·1119(CONVERSION·EXPERT·40,200·23그룹) 둘다 종료.
- ✅ **★재발견: MOP EXPERT 모드=명시적 숫자목표(ROAS/CONVERSION)** → 우리 BEP-ROAS에 훨씬 근접("MOP 숫자목표 없음"은 이지모드만 본 오해).
- ✅ **크래시 해결**: 동기 XHR이 느린 엔드포인트(flight/bids ~30s)에서 페이지 블록→크래시(4회). **비동기 fetch로 회피**(mop_engine_outputs.md §1-c, failures.jsonl). 스크립트 `/private/tmp/mop_async_kick.js`.
- ✅ **D-NAO-41 크론 자연 재확인**(별건, 원칙22): prod naver_ad_daily 07-11=1682행·cost 1,207,218 / search_term 07-11=8060행(synced 07-40=크론시각 일치). 자체생성 복구 유지 확정.

## 3. 확정 결정(불변)
- 자동화 경로=MOP 유지(A안). 대상=00.아이폰_17(SPA/쇼핑, cmp-a001-02-000000009793536). 저장/시작(돈)은 Jino 명시 확인 후만.
- BEP-ROAS 숫자 하한 유지 기본(D-NAO-1). 애드그룹 입찰가는 MOP 자유도 최대·우리 개입 최소.
- 관찰=행동 궤적 역설계. 스냅샷 하루 1회(네이버 API 일 단위).

## 4. 핵심 파일
| 파일 | 역할 |
|---|---|
| `docs/references/data/mop_ui/mop_engine_outputs.md` | ★엔진 엔드포인트 지도+실데이터+안전(비동기) 캡처법+유닛 구조 |
| `docs/references/data/mop_ui/mop_live_A_00iphone17.md` | A안 관찰 로그(D+1 실측 채움, advertiserId 정정) |
| `docs/references/data/mop_ui/mop_snapshot.js` | 스냅샷 스크립트(ADV=756 수정됨) |
| `docs/tracks/active/track_naver-ad-optimization.md` | 트랙(D-NAO-42-b) |

## 5. 알려진 이슈
- ⚠️ **gstack 헤디드 반복 로그아웃/크래시**: 크래시=동기 XHR(해결=비동기). 그러나 로그아웃 빈발 자체는 별도(세션TTL/네비)로 잔존. 재접속 시 Jino 재로그인 필요.
- MOP API 캡처는 **반드시 비동기 fetch**(동기 XHR 금지).

## 6. 다음 작업(D+2)
- [ ] Jino 재로그인 후 → **SPA(saShopping) 전용 엔진 엔드포인트 캡처**(`/dashboard/saShopping/...` 예상) — 우리 캠페인에 직접적. 비동기로.
- [ ] 종료 유닛 4099 **최적화 리포트**(리포트→최적화 리포트) — MOP가 실제 뽑은 입찰변경·예측vs실적 시계열. report 엔드포인트 날짜파라미터 필요.
- [ ] 00.아이폰_17 일별 스냅샷 계속(mop_snapshot.js, ADV=756).

## 7. Jino 결정 2건(대기)
- **결정1 시동**: 캠페인 노출은 나오나 입찰 50/50/500원 낮아 클릭0→지출0→MOP 예산0. (A)입찰 소폭↑로 첫 지출 만들고 MOP 유닛 생성(A안 실가동) / (B)며칠 더 관찰. Claude 추천=A.
- **결정2 모드**: 이지모드 균형/성장(숫자목표 없음) vs **EXPERT+ROAS/CONVERSION(우리 BEP-ROAS 정합)**. Claude 추천=EXPERT-ROAS 방향 관찰.
- 둘 다 돈 걸림 → 세팅안만 Claude 준비, 저장은 Jino.

## 8. 새 세션 시작 프롬프트
```
.claude/memory/HANDOFF_ohisell-mop-A-D1-observation_20260712.md 읽고 A안 MOP 라이브 관찰 D+2 이어서 진행해줘 (advertiserId=756, 다음=SPA 엔진 캡처)
```
