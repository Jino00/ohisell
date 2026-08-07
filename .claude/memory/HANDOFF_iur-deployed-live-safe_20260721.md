# 세션 인수인계: 스프린트 IU-R(순위 서보) 배포·라이브 안전 검증 완료 — 서보 폐루프는 상설 관측

> 저장 2026-07-21 11:50 KST · 워크트리 `knowledge-layer-inversion-phase0-77bb7d` · 브랜치 `claude/iu-r-deploy-mo-bid-down-ce6c8d`
> **main==prod 복원**: PR #74 병합(main tip `cef4863`). prod 백엔드는 배포 커밋 `79cca9e` 코드로 가동(이후 main 추가분은 docs만).
> 필독 순서: 이 파일 → `claude-progress.txt` → `docs/PLAN_naver-ad-rank-servo.md`(§4 체크리스트·§실측) → 트랙 D-NAO-69

## 1. 이번 세션 완료 (D-NAO-69)

1. **IU-R R0~R3 배포** (04:30 KST, safe_deploy CAS, 13파일): 계획서 목록 11 + **diff 감사로 누락 발견** `models.py`(docstring·마이그레이션0)·`diary_outcome.py`(레지스트리 이관) 2개 추가. pm2 재시작·기동 정상.
2. **라이브 안전 검증**(원칙22, 05:20~11:20 시간당 레인):
   - 레인 전부 `run_naver_auto_operator_hourly | ok`·에러 0
   - prod venv 서보 모듈 임포트 OK·레지스트리 `RANK_STEP_TYPES={bid_up_servo,bid_up_rank}`·`BID_UP_TYPES`에 신타입 포함 확인
   - **가드 무결성 실증**: change_log 185(probe_op bid_up, 그룹 070109620 맥세이프_컨텐츠)가 **쿨다운 2h(마지막변경 1.5h前<2h)에 정확히 차단**(status=failed·근거 명시) → R0 레지스트리가 bid_up 가드 라우팅 안 깸을 라이브로 입증. shopping_group_bep bid_down(181·183)도 가드 차단 정상.
   - R3 스테이지 배선(`learning_loops.py` line 41 `bid_rank_curve.run_daily`)·08:10 잡 ok·콜드 slope(서보 실쓰기 0→관측쌍 0=정상)
   - 다운스트림(bid_up/bid_down/probe/resume/pacing/anomaly) 전부 정상 처리·**회귀 0**
3. **PR #74 병합** — main==prod 복원. 트랙 D-NAO-69·progress·계획서 §4 갱신.
4. 사전 봉투(§3.5) 명문화(`79cca9e`) — ±15% 초과 실쓰기 대상/최대금액/잔여예산/롤백.

## 2. ★미완 = 서보 실쓰기 폐루프 (자연 발동 상설 관측)

- §3-4 R1(폐루프 한 바퀴)·R2(±15% 초과 1스텝)는 **첫 ~7h 미발동**. 원인 = 트래픽 게이트: SHOPPING 검색순위 그룹이 **핫셋(그룹당 클릭≥10)+ROAS UP 게이트** 통과해야 서보 발동. 새벽~오전 저집중(11시까지 계정 클릭 45그룹에 ~40회 분산=그룹당 1회 미만)에서 hold=설계대로.
- **첫 자연 발동 포착 신호**: `naver_change_log` action=`update_bid`·dry_run=`0`·entity_type=`adgroup`·proposal_type∈{`bid_up_servo`,`bid_up_rank`}(proposal_id 조인). 기준선 = 현재 change_log max id 185(이하 서보 실쓰기 0).
- **폐루프 한 바퀴 판정**: 서보 실쓰기 → 다음 시간 hh24 avg_rank 목표 방향 이동(bid→rank 인과 지연 = 핵심 가정, §실측4) → 다음 :20 데드밴드 관망/래칫. R3 slope는 첫 실쓰기 후 **익일 08:10** 적립 시작(현재 `naver_learning_state` scope_key `adgroup:%` metric `bid_rank_slope` 공란=정상).
- 오후~저녁 피크(14:20·20:20 레인)가 첫 발동 유력 시간대.

## 3. 다음 세션 순서

1. **서보 첫 자연 발동 상설 관측** → 폐루프 한 바퀴 실측 시 §3-4 최종 합격 기록(계획서 §4 마지막 [ ] → [x]). 원칙22: 실측 전 "폐루프 합격" 단정 금지.
2. **MO 소재 bid_down_first**: 07-21 진단은 resume 후보 2건(1367·1368)으로 산출(eff_bid≤바닥→resume, account_diagnosis 1363행·데이터 의존). Confirm 왕복은 Jino 몫.
3. **로드맵 SS**(검색어 ROAS 레이어) — 전략 `docs/STRATEGY_naver-ad-v2.md`, 실측 3종(제외 한도 70vs140·그룹 생성 한도·검색어 전환 API) 먼저. D-NAO-68=실쓰기 손까지.
4. **codex 소급 리뷰 07-23**(R0~R3 전 커밋 `daaddba`~`f4a7be8`) — 한도 회복 후.

## 4. 주의 (원칙22)

- **"서보 폐루프 됐다" 아직 금지** — 배포·가드·다운스트림만 라이브 검증됨. 실쓰기 폐루프는 미관측.
- 배포는 safe_deploy만(CAS). changed_at=KST·created_at(proposal)=UTC 혼재(1375의 created_at 01:20:08=UTC=10:20 KST).
- 서보 라이브 첫 관측 함정(계획서 §3): 정산 ok 유닛만 반복 상향·데드밴드 관망은 "스텝 없음"이 정상·BRAND_SEARCH/estimate 실패는 hold가 설계.

## 5. 새 세션 시작 프롬프트

`이 HANDOFF 읽고 이어서: IU-R 배포·라이브 안전 검증 완료(D-NAO-69·PR#74·main==prod). 남은 것=서보 실쓰기 폐루프 자연 발동 상설 관측(change_log dry_run=0 proposal_type∈{bid_up_servo,bid_up_rank}, 기준선 max id 185)→폐루프 한 바퀴 실측 시 §3-4 최종 합격 기록. 그다음 로드맵 SS. prod=sellc.ohitech.co.kr DB=/home/ubuntu/ohisell/backend/ohisell.db.`
