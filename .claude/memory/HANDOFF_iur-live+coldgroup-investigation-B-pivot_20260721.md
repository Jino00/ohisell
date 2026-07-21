# 세션 인수인계: IU-R 배포·라이브 + 콜드그룹 조사 → B 스프린트 전환

> 저장 2026-07-21 15:05 KST · 워크트리 `knowledge-layer-inversion-phase0-77bb7d` · 브랜치 `claude/iu-r-deploy-mo-bid-down-ce6c8d`
> 새 대화 시작 시 이 파일을 먼저 읽을 것. (이전 HANDOFF `HANDOFF_iur-deployed-live-safe_20260721.md`를 잇는다 — 그 뒤 콜드그룹 조사·B 전환이 추가됨)

## 1. 프로젝트 위치 및 환경
- 로컬 워크트리: `.../Ohiselling/.claude/worktrees/knowledge-layer-inversion-phase0-77bb7d`
- prod: `sellc.ohitech.co.kr` · 백엔드 `/home/ubuntu/ohisell/backend` (pm2 `ohisell-backend`·uvicorn:8001·venv `.venv/bin/python`) · DB `ohisell.db`
- 배포: **`scripts/safe_deploy.sh` 만**(CAS·직접 scp 금지, D-NAO-49)
- main tip = `cef4863`(PR #74 병합, main==prod)
- 프로덕션 데이터 조회 관례: SHOPPING 그룹입찰 실효 여부는 `effective_bid.adgroup_effective_bids`(source='group'/'ad'). naver_ad_daily 집계는 `__backfill__`·'' sentinel 제외(2배 함정).

## 2. 이번 세션 완료 목록
- ✅ **IU-R(순위 서보 R0~R3) prod 배포**: safe_deploy CAS 13파일(계획 11 + diff 감사로 누락 발견 `models.py`·`diary_outcome.py` 추가). pm2 재시작·기동 정상. **2554 passed·회귀0**.
- ✅ **라이브 안전 검증(원칙22, 05:20~14:20 레인 전부 `ok`)**: prod 서보 모듈 임포트·레지스트리 확인 / **가드 무결성 실증**(probe bid_up change_log 185가 쿨다운 2h[1.5h<2h]에 정확 차단 → R0 레지스트리가 bid_up 가드 라우팅 안 깸) / R3 스테이지 배선·08:10 잡 ok(콜드 slope=정상) / 다운스트림 정상.
- ✅ **PR #74 병합** → main==prod 복원. 트랙 **D-NAO-69**·claude-progress·계획서 §3.5(사전봉투)·§4 갱신.
- ✅ **콜드그룹 조사(Jino "클릭 안 나오면 순위 올려야 하는데 왜 안 보이나")**: 3단 진단 → 서보(ROAS게이트, 0클릭=미발동 정상) / fast-loop 탐침(CD2/CD5, 핫셋 정착클릭≥10만) / **A″(핫셋 밖 저볼륨 탐침) 설계 → 실측으로 기각**.
- ✅ **A″ 설계서 작성·보류**: `docs/PLAN_naver-ad-lowvol-exploration.md`. 실측 결정타=콜드(정착 클릭≤3)·노출>0 SHOPPING 그룹 **18개 전부 source='ad'(소재-레벨), source='group' 0개** → 그룹입찰 탐색 후보 0 → **B 의존 보류**.

## 3. 확정된 결정사항
- **D-NAO-69**: IU-R 구현·배포·라이브 안전 검증 완료(트랙 기록). 서보 실쓰기 폐루프는 **자연 발동 상설 관측**(트래픽 게이트).
- **콜드 탐색의 유일 레버 = 소재입찰 = B 스프린트**(실측 확정). A/A″ 순서를 뒤집어 **B 우선**.
- **`_EXPLORATION_MAX_SETTLE_CLK`=3 (Jino "진짜 콜드까지 적극 탐색")** — A″ 로직을 B로 이식 시 이 값 사용.
- A″ 설계 로직(탐색 트리거·후보·D+1 CD3 되돌림·안전장치)은 유효 → **레버만 그룹입찰→소재입찰(B)로 이식해 재활용**.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/PLAN_naver-ad-rank-servo.md` | IU-R 스펙·§4 체크리스트(서보 폐루프=상설관측 [ ]) |
| `docs/PLAN_naver-ad-lowvol-exploration.md` | A″ 설계서(⛔B 의존 보류·실측 교차표 상단 기록) |
| `docs/tracks/active/track_naver-ad-optimization.md` | 트랙(D-NAO-69 추가) |
| `docs/STRATEGY_naver-ad-v2.md` | 전략 v2(로드맵 IU→IU-R→SS→L2→L3→소재) |
| `backend/app/services/naver_ad/auto_operator.py` | 레인·핫셋(`_hot_set_candidates`:648, 정착클릭≥10)·탐침(`_probe_trigger`:1324) |
| `backend/app/services/naver_ad/effective_bid.py` | 소재 실효입찰 source='group'/'ad' 판정 |
| `backend/app/services/naver_ad/rank_servo.py`·`bid_rank_curve.py`·`bid_step_types.py` | IU-R 신규 SA |

## 5. 알려진 이슈 / 주의사항
- **서보 "폐루프 됐다" 아직 금지**(원칙22) — 배포·가드·다운스트림만 검증. 실쓰기 폐루프 미관측(트래픽 게이트).
- **콜드 롱테일은 100% 소재-레벨** — 그룹입찰로는 손 못 댐. B 없이는 Jino가 원한 콜드 탐색 불가.
- auto_operate 4개(파워링크10236310·쇼핑8492582·8514959·맥세이프10769985)는 콜드(노출은 있으나 클릭 0~2). 맥세이프는 실질ROAS 0.07 출혈로 손실고삐가 조이는 중. 파워링크는 만성 0.1% CTR(순위 아닌 관련성 문제).
- 시각 혼재: change_log changed_at=KST, proposal created_at=UTC(+9h). B1 "96% useGroupBidAmt=false"는 계정 전체 통계(이 4개만 보면 group 37/ad 24).

## 6. 다음에 할 작업 (미완료)
- [ ] **★Jino 결정 대기 — B 확장 방향**: 소재입찰 **UP** 제어 어디까지 자동화(Confirm 유지 vs 콜드 탐색은 자동 허용). 현재 B(D-NAO-65 B3/B4)=맥세이프 1호 카나리·소재입찰 DOWN·Confirm 전용. 이 경계는 프로젝트 규칙상 **Jino 결정**(위임 스위치는 Jino만).
- [ ] 결정 후: B 확장 설계(소재-레벨 UP + A″ 탐색 로직 이식) → GATE·codex(원칙19) → Sonnet 구현 → 배포.
- [ ] IU-R 서보 첫 자연 발동 상설 관측(백그라운드 웨이크업): change_log dry_run=0·proposal_type∈{bid_up_servo,bid_up_rank} 발생 시 다음시간 avg_rank 이동→폐루프 한 바퀴→계획서 §4·트랙 D-NAO-69 갱신.
- [ ] (이월) SS 로드맵·codex 소급 리뷰 07-23(IU-R R0~R3 전 커밋).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

`.claude/worktrees/knowledge-layer-inversion-phase0-77bb7d/.claude/memory/HANDOFF_iur-live+coldgroup-investigation-B-pivot_20260721.md 읽고 이어서 작업해줘. 핵심=IU-R 배포·라이브 안전검증 완료(D-NAO-69·PR#74·main==prod, 서보 폐루프는 상설관측). 콜드그룹 조사 결과 A″ 기각(콜드 18개 전부 소재-레벨)→B 스프린트가 유일 레버. 다음=Jino의 B 확장(소재입찰 UP 자동화 경계) 결정 대기.`
