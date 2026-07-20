# 세션 인수인계: D-NAO-65 실행 — 스프린트 DL 완료·라이브 + 스프린트 B1/B2 라이브·B3 GATE 중
> 저장일시: 2026-07-20 11:05 KST · **최종 갱신 12:15 KST — §3 대체됨: B3·B4 완료·배포·병합(PR #67·#69), 스프린트 B 전 페이즈 종료.**
> ★12:15 추가: B3 GATE 3R PASS→카나리 1호=맥세이프 개방(DOWN만·Confirm-only·위임/브리핑 전면 제외·자동발사 5경로 봉쇄). B4 GATE 2R PASS→바닥 재개 정책(>70=bid_down_first·70에서만 resume·재pause 3일 쿨다운·ML 제외). 2384 passed·main==prod `ceb638f`. **다음 세션 첫 행동: 07-21 08:00 후 MO(grp-…070109616) bid_down_first pending 생성 확인(소재 551485078, 800→680) → Jino 콘솔 Confirm 왕복 관측(B 라이브 합격 시작). 이후 UI→L2→L3.** MO 실측: 소재입찰 800=CPC 824 미스터리 완결. 운영 노트: 800→70은 Confirm ~15회(수 주) — 서두르려면 Jino 콘솔에서 소재입찰 직접 하향이 빠름.
> 새 대화: 이 파일 → 트랙 `docs/tracks/active/track_naver-ad-optimization.md`(D-NAO-59~65) → 계획서 2개(`docs/PLAN_naver-ad-daily-loss-leash.md`·`docs/PLAN_naver-ad-ad-level-bid.md`) → 직전 HANDOFF(같은 폴더 `HANDOFF_ohisell-maxsafe-diag+D-NAO-64-floored-loss+daily-leash-direction_20260719.md`).
> 모델 라우팅(Jino): 구조=Fable·설계/구현=Opus·단순=Sonnet, 옵션은 Claude 추천안 자동. "끝까지 자동 진행+handoff로 신선도 유지" 지시 하에 자동 진행 중.

## 1. 환경
- 워크트리 `video-content-summary-0e6c41` / 브랜치 `claude/maxsafe-loss-adgroup-diagnosis-4710fe`. **main==prod**(PR #63·64·65·66 병합, main tip `fcba9f1`).
- prod: sellc.ohitech.co.kr `/home/ubuntu/ohisell`(pm2 8001). 배포=`scripts/safe_deploy.sh`만. 마이그=`ssh … alembic upgrade head`(적용 head `c5d6e7f8a9b0`).
- prod read-only: `ssh … 'cd /home/ubuntu/ohisell/backend && .venv/bin/python3 -'`(★cd 필수). 실 DB=`backend/ohisell.db`. kst_today=`app.utils.kst`.
- 테스트: `cd backend && python3 -m pytest -q` → **2351 passed**(B3까지 로컬).
- 네이버 API 403 간헐(아침 sync 후 쿼터) — 수동 프로브는 오후가 안전. 앱 크론 경로는 정상.

## 2. 이번 세션 완료 (전부 배포·라이브·PR 병합)
- ✅ **D-NAO-65 승인·기록**: 일일 고삐 정책+DL→B→UI→L2→L3 순서 (Jino "그래, 끝까지 계속 진행하자").
- ✅ **스프린트 DL(일일 손실 고삐) DL1~4 — PR #64**: DL1 스톱로스 창 절체(행동=성공 입찰변경 후·만성 7일, GATE 2R — 실패행 창리셋 이중침묵·KST 이중시프트 P1 2건 수정) / DL2 pause 예외화(바닥 대기·레버끊김 판정기·지속 밸브 3일, GATE 3R) / DL3 bid_down 일일상한 면제(~8스텝/일, GATE 1R) / DL4 익일 밴드 재시작(BEP 종속·learned band 천장·승자 관성, GATE 1R).
- ✅ **08:50 관측(배포 전 코드)**: D-NAO-64 첫 발동 — 맥세이프 MO pause 실집행(cl 143)·03 5그룹 재정지·이중 bid_down 쿨다운 차단·삭제그룹 404 무해. 전부 예측 일치.
- ✅ **스프린트 B1(소재입찰 인식) — PR #65**: NaverAdgroupProduct additive 4컬럼(마이그 적용)·get_ads adAttr 파싱·sync 편승(추가 API 콜 0). **★계정 구조 실측: 88개 소재 중 85개(96%) useGroupBidAmt=false = 소재입찰(1500~2400)이 실효, 그룹입찰 레버는 ~4%에만 연결.**
- ✅ **스프린트 B2(실효입찰 파생·재정의) — PR #66**: effective_bid SA(max·배치·폴백)·임계=실효×10·미연결(source='ad') 그룹의 그룹입찰 발사 억제("[레버 미연결] — B3 대기")·미연결 증거 창=만성 7일·정직 사유문. GATE 2R PASS. 라이브: 30/33 미연결 정확 판정·보드 38→32.
- ✅ ref 33 딥리서치(예산·키워드·시간대, 3표 검증 11 findings — L2/L3 설계 근거) — 어제 밤.

## 3. ★진행 중 (이 세션이 멈춘 지점)
- **B3(소재입찰 제어·카나리) 구현 완료(2351 passed)·Opus GATE 리뷰 진행 중** — update_ad_bid(PUT adAttr, useGroupBidAmt 불변)·harness 'ad' 분기·`AD_BID_CANARY_CAMPAIGNS=frozenset()`(기본 빈=배포 즉시 행위 변화 0)·미연결 hold→카나리 ad 라우팅·ad change_log 창 인식. **미커밋 상태**(GATE 결과 대기). GATE 공격 각도: 가드레일 우회 전수·ad/그룹 쿨다운 분리 우회·max 소재 단일 제어의 지출 이전 구멍·TOCTOU·카나리 견고성·up BEP 근사 위험(down만 1차 개방 판정 요청).
- **관측 대기**: ①11:20 시간당 레인 — B2 미연결 hold 첫 자연 발동("[레버 미연결]" held 사유) ②DL 자연 발동(8스텝 하향·밸브·재시작 천장·"재시작 대기" 사유) ③임계 미달 미연결 유닛 무전환 비용 합계(침묵 대역 실측).

## 4. 다음 작업 (순서)
- [ ] B3 GATE 결과 처리(수정 루프 or PASS→커밋) → safe_deploy 배포(카나리 빈 상태=무행위) → **카나리 1캠페인 개방은 Jino 보고 후**(제어 첫 실쓰기 — 자동 진행 위임 범위라도 실쓰기 개방은 보고 가치. 추천: 맥세이프 or 04 중 미연결 그룹 있는 쪽 1개, down만).
- [ ] B4: lever_broken pause 은퇴(소재 leash로 대체)·pause된 MO형 재개 흐름.
- [ ] UI 스프린트(sellc loss 정책 스위치) → L2(예산 자동증액, ref 33 규칙) → L3(인벤토리 확장: 검색어 n-gram 승격→미광고 상품→지면).
- [ ] codex 소급 리뷰 07-23(DL·B 전 커밋).
- [ ] (관측) 위 §3 관측 3종 + DL2 P3(보드 가시성·retro 채점 드리프트) 후속.

## 5. 주의
- 원칙22: B3 "됐다" 금지(GATE·배포·카나리 CPC 실하락 왕복 전). 개별 캠페인 소방수 금지(전역 규칙만). naver_ad_daily 2배 함정(센티널 dedup). 보정계수 ×1.21. codex 07-23.

## 6. 새 세션 시작 프롬프트
`.claude/worktrees/video-content-summary-0e6c41/.claude/memory/HANDOFF_ohisell-D-NAO-65-DL+B-sprints-live_20260720.md 읽고 이어서 진행해줘. B3 GATE 결과 처리부터.`
