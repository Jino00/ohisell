# 실제 MOP(03) vs 우리 MOP(04) 철학 대결 — A/B 대조 실험

> 결정: D-NAO-42-e (2026-07-12 밤, Jino 승인). 목적 = 두 최적화기가 실제로 어떻게 입찰을 움직이는지 나란히 관찰해 우리판 MOP 복제·검증.
> 프레이밍 = **철학 대결**: MOP=클릭최대화(GROWTH) / 우리=BEP-ROAS 이익최적화(D-NAO-1). 단일 지표 승패 판정 안 함.
> 비교 방식 = 규모가 다르므로 **각자 baseline 대비 lift %** + **입찰 이동 메커니즘** 중심.

## 실험군 정의
| | A군: 03 (실제 MOP) | B군: 04 (우리 프로그램) |
|---|---|---|
| 상품 | 아이폰_강화유리 | 아이폰_지문방지 |
| 캠페인 | cmp-…008492582 | cmp-…008514959 |
| 애드그룹 | 24 | 11 |
| 최적화기 | MOP 유닛 6245 (CLICK/GROWTH, 예산 42,130) | 우리 프로그램 X1b (BEP-ROAS, 가드레일 ON) |
| 목표 함수 | 클릭 최대화 | 이익 최적화(BEP-ROAS 하한) |
| 개시 | 2026-07-13 | (카나리 개방 후, 선결 검증 중) |

## Baseline (2026-07-06~07-12, 7일, 집행 전, SA API 실측)
| 지표 | 03 (A) | 04 (B) |
|---|---|---|
| 노출 | 2,724 | 928 |
| 클릭 | 17 | 6 |
| 비용 | 32,411원 | 7,214원 |
| 일평균 비용 | ~4,630원 | ~1,031원 |
| 소재 bidAmt 범위 | 800~2,400원 | 300~1,990원 |

- ⚠️ **규모 03 ≈ 4.5× 04** (전체 캠페인 7일 실측). 절대 비교 금지, lift % 비교.
- 재캡처: `unit6245_baseline_snapshot.py`(03) / `campaign_baseline_snapshot.py cmp-a001-02-000000008514959`(04).
- 04 특이: 애드그룹 대부분 그룹bid=None(소재별 개별 bidAmt), 140028 그룹은 소재 1개가 50원(그룹bid 사용)인데 375노출·4클릭·5,898원으로 04 지출의 대부분 차지.

## 관찰 축 (매일 양군 동시 수집)
1. **입찰 이동**: 어느 소재 bidAmt를 올렸/내렸나 (방향·폭·빈도). MOP=클릭 위해, 우리=이익 위해 어떻게 다르게 움직이나.
2. **지출 궤적**: baseline 일평균 대비 상승/유지/감소. MOP는 42,130 한도로 밀어붙이나? 우리는 BEP 하한 지키며 절제하나?
3. **성과 lift %**: 노출·클릭·CTR·전환·ROAS 각각 baseline 대비.
4. **추정 이익**: 지출 대비 공헌이익(BEP-ROAS 기준). MOP의 클릭최대화가 이익엔 어떤 영향?

## 일별 관찰 (매일 append)
### D0 — 2026-07-12 (양군 baseline 확정, 집행 전)
- 위 baseline 표가 t=0. 03은 07-13 MOP 집행 시작, 04는 카나리 개방 후 우리 프로그램 집행 시작.
- 04 카나리 개방 선결 검증 진행 중(게이팅 매핑 → prod 배포·필수수정·허용목록·가드레일·위임 확인).

#### prod 라이브 상태 검증 (2026-07-12 21시경, 원칙22 실측)
- ✅ **X1b 스키마 prod 배포됨**: `naver_proposals`에 `target_bid`·`target_lock`·`adgroup_id`·`approval_source` 컬럼 존재.
- ✅ **활성 카나리 없음**: `naver_campaign_settings`=0행, `naver_account_settings`(KV)=비어있음 → 어떤 캠페인도 optimizer='ours' 미지정. "카나리 보류" 상태와 일치.
- ✅ **우리 프로그램 자율 실쓰기 0건**: `naver_change_log` dry_run=0 11건은 전부 `action=external_status_change`(외부 변경 감지, D-NAO-13) — 우리 write 아님. 게이트 정상 유지.
- ✅ **제안 파이프라인 가동 중**: naver_proposals 464행(pending 92·expired 372) → 일별 제안 생성은 prod에서 돌고 있음.
- ★**외부 락 관찰**: 2026-07-12 07:37 **03 캠페인(cmp-…008492582) 애드그룹 userLock false→true**(외부 잠금) 감지 — MOP가 유닛 6245 인수 준비로 03을 잠근 정황. 07-13 집행 전 MOP의 첫 손댐일 수 있음(관찰 가치).
- 게이팅 코드 매핑(서브에이전트) 완료 후 04 개방 정확 절차 확정.

#### 04 카나리 개방 게이팅 — 코드+prod 확정 (2026-07-12 밤)
- **X1a/X1b는 캠페인별 플래그 아님**: 액션 개방=전역 코드 `OPEN_ACTIONS={add_negative_keyword, update_bid, set_user_lock}`(naver_execution_harness.py:100) — 이 워크트리엔 `update_bid`(입찰)·`set_user_lock`(정지/재개) **이미 전역 개방**. 코드 수정 불필요.
- **04 개방 = 설정 하나**: `naver_campaign_settings.optimizer='ours'`(현재 04=행 없음=none=완전 닫힘). 이 값이 제안 생성(proposal_writer `_ours_campaign_ids`)과 실행 게이트(harness `optimizer=='ours'` 재검증) 둘 다 연다. `PUT /api/naver/ad/campaign-settings` 또는 DB.
- ⚠️ **'ours'는 X1a/X1b 구분 안 함**: 켜는 순간 04에 제외키워드·입찰·정지/재개 제안이 다 생성·실행가능 범위. "입찰만" 스코핑은 **위임/승인 단계**에서만 가능(아래).
- **자동 vs 반자동**: 자동발사 5조건(유형이 `expert_delegated_types` KV에 위임 + Ava평결 'agree' + pending + real_write_blocker None + optimizer 'ours'). **현재 prod**: expert_delegated_types **미설정**(위임 0=fail-closed) → optimizer만 켜도 **자동발사 0**, 제안은 pending으로 쌓임 → 콘솔 승인+실행 버튼 눌러야 실쓰기(반자동).
- **★Ava 공백(원칙22)**: expert_review_run 07-10이 마지막(id=2). generate_expert_desk 크론은 'ok'지만 07-11·07-12 Ava 런 미생성 → **자동 경로는 지금 Ava 수리 전까지 사실상 불가**(신선한 'agree' 없음). 반자동은 Ava 무관하게 작동(제안 생성=generate_naver_proposals 08:00 정상).
- **가드레일 자동 적용**: 모든 입찰 write가 `_execute_update_bid`→`guardrail_gate.check`(클램프70~100k/10원·±15%[growth 면제]·쿨다운5h·하루3건·스톱로스·BEP하한·일예산불가침, 방향일치). 추가 설정 불필요.
- **D-NAO-40 필수수정(resume가 수동/MOP 정지 덮어쓸 위험)**: 04에 **입찰만** 스코핑(정지/재개 제안은 승인 안 함/위임 안 함)하면 이 위험 회피됨. 04는 MOP 밖이라 MOP-정지 충돌도 없음. 자동 전환 전엔 정식 수정/검증 권장.

#### ★04 카나리 개방 실행 (2026-07-12 21:22 KST, Jino 승인 "반자동 개시")
- `PUT /api/naver/ad/campaign-settings {campaign_id: cmp-…008514959, optimizer: 'ours'}` (포트 8001) → **성공·검증**. mode/target_roas_override=NULL(우리 네이티브 BEP-ROAS 이익 최적화; mode는 라벨필드로 로직 미소비 확인).
- 감사로그: `optimizer_change none→ours` @ 2026-07-12 12:22:01 UTC(=21:22 KST).
- **위임 미설정 유지**(expert_delegated_types 비어있음) → **자동발사 0**. 04 입찰 제안은 pending으로만 쌓임 → Jino가 콘솔에서 **입찰 제안만** 승인+실행해야 실쓰기(가드레일 통과분만).
- **동작**: 내일 08:00 `generate_naver_proposals` 크론이 04 입찰 제안 생성 → 콘솔 pending. Jino 승인 시 04에 우리 옵티마이저 첫 실입찰. MOP는 07-13 03 자율 가동.
- 관찰 크론 `6b2c0462`(07-13 20:20 KST): 03 MOP delta + 04 우리 제안·집행 D1 풀데이 대조.

### D1 — 2026-07-13 (진행 중)
#### 06:20 KST 상태 확인 (원칙22 라이브 실측, D1 데이터 아직 없음 — 정직 라벨)
- ✅ **04 카나리 여전히 라이브**: `GET /api/naver/ad/campaign-settings?campaign_id=cmp-…008514959` → `optimizer='ours'`, mode=null, updated 2026-07-12T12:22:01(UTC). 야간 사이 변경 없음.
- ⏳ **04 첫 입찰 제안 미생성**: 08:00 `generate_naver_proposals` 크론이 아직 안 돎(현재 06:20). D1 04 제안·집행 데이터는 08:00 이후 생성.
- ⏳ **MOP 유닛 6245(03) 집행 시작일=07-13** — 오전엔 입찰 이력 얕음. MOP 콘솔 지표(입찰횟수·플라이트·예측)는 Jino 로그인 필요(be.mopapp.net 세션). SA 자동수집(03 delta)은 naver_ad_daily D-1 확정치라 07-13 종일치는 07-14 07:30 크론에 들어옴.
- **결론(원칙22)**: **의미 있는 D1 대조는 오늘 저녁**(04 제안·집행 발생 + 03 하루 진행 후). 06:20에 "관찰 완료" 단정 금지 — 지금은 baseline 대비 변화 0(집행 전).
- ⚠️ **관찰 크론 6b2c0462는 죽은 세션(6cc75b)에 종속** — 이 세션에서 발동 안 될 수 있음. D1 대조는 저녁 새 세션 "03 vs 04 D1 관찰 업데이트"로 수동 실행하거나, 이 세션 유지 시 저녁에 실행.
