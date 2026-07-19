# 세션 인수인계: 03·맥세이프 완전 자동운영 온보딩 + RL4b 쇼핑 스톱로스 고삐 + 운영 모니터링
> 저장일시: 2026-07-19 20:53 KST
> 새 대화 시작 시 이 파일 먼저 → 트랙 `docs/tracks/active/track_naver-ad-optimization.md`(D-NAO-59~63) → 필요 시 앞선 HANDOFF `HANDOFF_ohisell-rank-leash-RL1-RL5-live_20260719.md`(RL1~RL5 상세).
> ⚠️ 이 세션 = RL 스프린트 완료(별도 HANDOFF) **이후** 후반부(03/맥세이프 운영 + RL4b). 둘 다 같은 워크트리.

## 1. 프로젝트 위치 및 환경
- 워크트리: `.claude/worktrees/daily-rank-leash-profit-control-71b501` / 브랜치 동명. **main==prod 정합**(PR #61·#62 병합 완료, main tip `5265528`).
- prod: `sellc.ohitech.co.kr:/home/ubuntu/ohisell` — pm2 `ohisell-backend`(8001). read-only 검증 = `cd backend && .venv/bin/python3 ...`.
- 배포: **`scripts/safe_deploy.sh <파일> [--restart]` 만**(CAS 가드). 마이그는 `ssh ... alembic upgrade head`.
- 테스트: `cd backend && python3 -m pytest -q` → **2220 passed**.
- prod 조정 실행 경로 = `naver_execution_harness.execute(db, proposal_id, dry_run=False, now=now)`(guardrail 초크포인트). 캠페인 성과 조회 = `fetch_campaign_stats([cid], date_preset='today')`.

## 2. 이번 세션 완료 목록
- ✅ **D-NAO-61 03(아이폰_강화유리, cmp-a001-02-…8492582, SHOPPING) MOP→우리 완전 자동운영**: Jino가 MOP 콘솔에서 03 정지+optimizer='ours' 전환 → 내가 `auto_operate=True`(prod DB) + `shopping_ad_product_sync.sync_adgroup_products`(매핑 36상품) → target 소스 account_default→**product_bep(1.822)**·adgroup bep_roas 1.5735·RL3 고삐 활성. 첫 실집행(12:07): bid_up×3(1500→1720 등)·pause×3(스톱로스, change_log 검증).
- ✅ **D-NAO-62 맥세이프카드케이스_쇼검(cmp-a001-02-…10769985, SHOPPING) 완전 자동운영 + 상품 온보딩**: optimizer='ours' 이미(Jino)+`auto_operate=True`. ★상품 `13563480014`(오하이 맥세이프 이지 거울 카드지갑)이 product_master·channel_mapping 둘 다 부재 → **정식 등록**(ProductMaster id 911·internal_sku OHI-MAXSAFE-EASY-MIRROR-WALLET·**원가 4,100원 Jino 확정**[일반/거울 타입 공통]·ProductChannelMapping 판매가=주문중앙 16,900·mapping_source='manual')+`calculate_bep` 재산출(with_bep 519→520·bep_roas 1.6902). 첫 실집행(12:55): bid_down 500→430·pause 1건. 삭제 광고그룹 2건 네이버 404 guardrail 안전차단.
- ✅ **D-NAO-63 RL4b — RL4 쇼핑 갭 수정**(Jino "03 왜 스탑로스? 입찰 낮추기로 한 거 아닌가" 포착): RL4가 쇼핑을 통째 pause로 뒀던 것(04/03/맥세이프 전부 쇼핑이라 leash 실제 미적용) 수정. `proposal_writer._adgroup_is_manual_bid`(naver_sa_writer.update_adgroup_bid ML가드 `systemBiddingType=='NONE'∧isAutobidActive is False` 재사용)+`_stop_loss_proposal(manual_bid=)`: **수동입찰+여지→bid_down 고삐·ML/판정불가/하한→터미널 pause**. **Opus 적대적 리뷰 GATE PASS**(P1·P2 0·무한출혈 없음·12조합 일치). commit `d30de16`·prod 배포·2220 passed.
- ✅ **PR #62 병합**(RL4b+온보딩+D-NAO-61~63 docs) → main==prod 복원.
- ✅ **운영 모니터링 3회 실적 보고**(16:06/18:54/20:49).

## 3. 확정된 결정사항 (트랙 D-NAO-59~63 전문)
- **ours 4개 캠페인 자동운영**: P_Test(파워링크·WEB_SITE)·03·04·맥세이프(전부 SHOPPING). 03/04/맥세이프는 04와 동급(상품 정밀 BEP+RL3 고삐). P_Test만 파워링크라 계정 기본 BEP·RL3 미적용(키워드↔상품 매핑 없음, 별도 검토 대상).
- **원가는 추정 금지·Jino 확정만 저장**(D-NAO-57). 맥세이프 4,100원 Jino 확정.
- **캠페인 관리주체 전환 = Jino가 MOP 콘솔 끄고 optimizer='ours' 전환 확인 후** 내가 auto_operate=True(D-NAO-13 이중관리 금지).
- **이중 집행 방지**: 이미 오늘 일 레인 돈 캠페인은 재집행 금지(신규 온보딩 캠페인만 스코프 앞당김 집행, 비대상 pending 원복).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_naver-ad-optimization.md` | D-NAO-59~63 결정 전문(먼저 읽기) |
| `backend/app/services/naver_ad/proposal_writer.py` | RL4·RL4b `_stop_loss_proposal`·`_adgroup_is_manual_bid`·`_step_down_bid` |
| `backend/app/services/naver_ad/intraday_roas.py` | RL2 추정 ROAS/총이익 신호(RL3 고삐 입력) |
| `backend/app/services/naver_ad/auto_operator.py` | 시간당 레인(:20)+일 레인(08:50)+RL3 `_intraday_loss_leash` |
| `backend/app/services/naver_ad/shopping_ad_product_sync.py` | 광고그룹↔상품 매핑 동기화(신규 캠페인 온보딩 시) |
| `backend/app/services/naver_ad/bep_calculator.py` | `calculate_bep`(원가 저장 후 재산출) |

## 5. 알려진 이슈 / 주의사항
- **★맥세이프 손실 구간(주시)**: 오늘 20:49 기준 비용 28,519원·전환 1·네이버ROAS 0.59(**실질~0.23 = 심각 손실**). 저녁에 소진 멈춤(28.5K<RL3 문턱 ≈38.5K)이라 오늘 자동 고삐 미발동. 내일 08:50 일 레인이 문제 광고그룹 처리 예정. **광고그룹 단위 범인 규명은 미착수**(Jino가 "맥세이프" 지목했으나 실적 업데이트로 전환) — 다음 세션 후보.
- **★03 광고그룹 3개(59832280/344/206) 현재 운영 중(userLock=False)**: 오늘 정지→내가 정지해제(고삐 시도)→재정지 쿨다운 차단. **내일 08:50 RL4b가 자동 재정지 예정**(최저입찰 50원 무전환=터미널 pause). 이들은 bidAmt=50(우리 하한 70 미만·외부가 07-13 급락시킴)이라 고삐 불가·pause가 정답.
- **★스톱로스 시점 불일치(개선 후보·미착수)**: 임계=현재입찰×10인데 비용=7일 창(과거 고입찰 포함) → 입찰 최근 급락 광고그룹서 잔상 오발동(59832280: 07-10~12 ~2000원서 11,660원 출혈→07-13 50원 급락→비용0인데 스톱로스 발동). 개선안 후보 3: ①비용 시점정규화 ②최근 N일 비용만 ③입찰변경 이후 비용만. 트랙 D-NAO-63 기록. Jino 결정 대기.
- **네이버 ROAS ~2.6× 과대(D-NAO-7)**: 실질=네이버÷2.6. 목표(BEP)≈1.6~1.9.
- **API 403 간헐**(레이트리밋): _get_adgroup 등 반복 호출 시 주의.
- 원칙22: prod 검증은 앱 venv. "됐다"는 라이브 증거로만.

## 6. 다음에 할 작업 (미완료)
- [ ] **맥세이프 광고그룹 단위 심층 진단**: 어느 광고그룹이 전환 없이 돈 쓰는지 규명 → 조기 고삐/정지 여부(Jino가 관심 표명). 실적 더 쌓인 뒤 or 지금.
- [ ] **03 3개 광고그룹 재정지 확인**(내일 08:50 RL4b 자동) — 07-20 아침 관측.
- [ ] **스톱로스 시점 불일치 개선** 설계 여부 Jino 결정(D-NAO-63 후보 3안).
- [ ] RL 스프린트 자연 발동 관측(장중 고삐 실 bid_down·탐침 소비 왕복 — 앞선 HANDOFF §5).
- [ ] codex 소급 리뷰 07-23(RL1~RL5·RL4b 커밋).
- [ ] (선택) P_Test 파워링크 상품 정밀 BEP = 키워드↔상품 매핑 메커니즘 별도 검토.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

`.claude/worktrees/daily-rank-leash-profit-control-71b501/.claude/memory/HANDOFF_ohisell-03-maxsafe-onboard+RL4b-shopping-leash_20260719.md 읽고 이어서 작업해줘. 우선 07-20 08:50 일 레인 결과(03 3개 재정지·맥세이프 관리) 확인하고, 맥세이프 손실 광고그룹 심층 진단.`
