# 세션 인수인계: 스프린트 RL(순위 고삐) RL1~RL5 전 페이즈 완료·배포·라이브 검증
> 저장일시: 2026-07-19 오전 KST
> 새 대화 시작 시 이 파일 먼저 → 트랙 `docs/tracks/active/track_naver-ad-optimization.md` D-NAO-59/60 → 계획서 `docs/PLAN_naver-ad-rank-leash.md` §0·§7 순.

## 1. 위치·환경
- 워크트리: `.claude/worktrees/daily-rank-leash-profit-control-71b501` / 브랜치 동명(main 기준, PR #61 오픈).
- prod: `sellc.ohitech.co.kr:/home/ubuntu/ohisell` — 백엔드 pm2 `ohisell-backend`(8001). read-only 검증=`cd backend && .venv/bin/python3 ...`.
- 배포: **`scripts/safe_deploy.sh <파일...> [--restart]` 만**(CAS 가드). 마이그는 별도 `ssh ... alembic upgrade head`(RL1에서 적용 완료).
- 테스트: `cd backend && python3 -m pytest -q`. 현재 **2208 passed**(스프린트 시작 2149 대비 +59).
- 세션 초 PR #60(D-NAO-59 docs) 병합 → main==prod, 이 브랜치 동기화됨.

## 2. 이번 세션 완료 (전부 배포·라이브 검증)
D-NAO-59 최종 목적(**총 이익 절대액 극대화**·운영점=한계ROAS=BEP)을 실행으로. 재설계 아닌 확장.
- **RL1** ccnt 데이터층 — hh24 `_STATS_HH24_FIELDS`에 ccnt + `NaverKeywordHourly.conv_cnt`(마이그 `b48c2f3bc0a3` **prod 적용됨**) + sweep. 회계 불변(건수만). commit `c225386`. 라이브: 전환 있던 3 애드그룹 hh24 conv_cnt 실값(21h/11h/15h).
- **RL2** `intraday_roas.py`(순수 read-only) — `adgroup_unit_price`(NaverProductBep selling_price/margin/bep_roas × NaverAdgroupProduct, campaign_target_resolver 공개헬퍼 `weighted_product_value_for_adgroup` 재사용)·`estimated_intraday_roas`=(Σccnt×price)/Σcost·`estimated_intraday_profit`(총이익 절대액). **보정계수 불필요**(실판매가 기반). commit `326c1c6`. 라이브: 실 애드그룹 bep_roas 1.5921(D-NAO-57 정밀화 일치).
- **RL3** 순위 고삐 판정 — `_judge_hourly`에 장중 loss DOWN 분기(추정ROAS<bep_roas ∧ 당일소진≥정착창 하루평균 → bid_down 한 등). 우선순위=과열/CPC DOWN 뒤·UP 앞(bleeding day UP 게이트). **D-NAO-4 안전방향 완화**(UP은 여전히 정착창 실측). 비대칭 기억(아래=오늘 곡선만 자정 리셋·위=정착창 관성, 창발적). commit `6f80d88`. **Opus 리뷰 GATE PASS**+영구 테스트 2개(쿨다운 차단 실 execute·총이익 등가). 라이브: 고삐 전경로 무에러("당일 소진 없음" fail-closed).
- **RL4** 스톱로스→고삐 교체(키워드) — `_pause_proposal`→`_stop_loss_proposal`: 키워드는 pause 대신 bid_down 고삐(`_step_down_bid` ×0.85·ceil10·floor70), 하한(70) 도달만 터미널 pause. 쇼핑 adgroup=pause 유지(ML 입찰·ours는 키워드 grain). 자연 graduation(무전환 지속→매일 −15%→하한→pause·전환 살면 사면). commit `53db4d6`. **Opus 리뷰 GATE PASS**(종료 보장 실증: 모든 시작가 pause 도달·최대 48스텝·무한출혈 불가). 라이브: 실 스톱로스 후보 8건 전부 bid_down 고삐(1600→1360·1100→940·600→510).
- **RL5** CD5 — **Part A** `_learned_optimal_skip` 게이트(탐침 발동 후 env_cell 학습 최적밴드 이미 도달시 생략·과climb 방지), `_probe_trigger` 순수 유지(`_probe_window_stats` 공유), `rank_band_upper` 헬퍼. **Part B** probe_cell_aggregate 밴드별 conv_cnt(RL1) → `_optimal_band` 전환 최다 우선(신호하한 유지)·CTR 폴백(백필). commit `2143d67`. **Opus 리뷰 GATE PASS**(게이트 방향 6경계·우회 없음·conv 우선·하위호환 실증). ★**라이브(강력)**: weekend 셀 실전환 82건→optimal **3.0-4.0·basis=conv=이익 스팟밴드 실선택**(CTR였다면 1.0-2.0 쏠림)=**P3-3 실해소·D-NAO-59 정합 실증**. weekday conv_cnt=0→CTR 폴백(정직 경계).

## 3. 확정 결정 (트랙 D-NAO-60에 전문)
- 쿨다운 **2h 유지**(D-NAO-55 진동 근거+CD3 Stage1 밸브가 급성 출혈 별도 처리). 2주 소급채점 후 재검토.
- 쇼핑 adgroup leash 미적용(ML 입찰 복잡성, ours는 키워드 grain으로 커버) — 별도 결정 시 개방.

## 4. 핵심 파일
| 파일 | 역할 |
|------|------|
| `docs/PLAN_naver-ad-rank-leash.md` | 스프린트 RL 계획서(§0 방향고정·§7 체크리스트 전부 [x]) |
| `backend/app/services/naver_ad/intraday_roas.py` | RL2 추정 ROAS/총이익 신호 SA(순수) |
| `backend/app/services/naver_ad/auto_operator.py` | RL3 `_intraday_loss_leash`·`_judge_hourly` 고삐 분기 / RL5 `_learned_optimal_skip`·`_probe_window_stats` |
| `backend/app/services/naver_ad/proposal_writer.py` | RL4 `_stop_loss_proposal`·`_step_down_bid`·`_terminal_pause` |
| `backend/app/services/naver_ad/probe_cell_aggregate.py` | RL5 밴드별 conv_cnt·`_optimal_band`(conv 우선)·`rank_band_upper` |
| `backend/app/services/naver_ad/naver_sa_ad_fetcher.py` | RL1 hh24 ccnt·`fetch_entity_hh24` |

## 5. 미충족분·다음 (원칙22)
- **자연 발동 대기**(코드 경로·실데이터 판정은 전부 검증됨): ①RL3 장중 loss 고삐 실 bid_down(:20 크론에 bleeding 유닛) ②RL5 소비 게이트가 실 탐침 생략/상향하는 왕복(탐침 자연발동 선결) ③RL4 스톱로스 고삐 daily 실집행(08:50, 후보 8건 실존).
- **다음 관측**: 07-20 08:50 daily 레인(RL4)·09:03 probe learning(RL5 basis=conv 확대)·:20 시간당 레인(RL3). conv_cnt 축적으로 basis=conv 셀 증가 추이.
- **잔여**: PR #61 병합·codex 소급 리뷰 07-23·(선택) 이중 bid_down persist P3 클린업(`_bid_proposal`에 adgroup_id 담아 dedup 정합, account_diagnosis 겹침 — Opus 리뷰가 무해 판정한 기존 구조).

## 6. 새 세션 시작 프롬프트
`.claude/worktrees/daily-rank-leash-profit-control-71b501/.claude/memory/HANDOFF_ohisell-rank-leash-RL1-RL5-live_20260719.md 읽고 이어서 작업해줘. 다음은 07-20 크론 자연 발동 관측(RL3 장중 고삐·RL4 스톱로스 고삐 daily 실집행·RL5 basis=conv 확대) + PR #61.`
