# PLAN — 소재-레벨 실효입찰 인식·제어 (스프린트 B, D-NAO-65)

> 이 시스템을 건드리는 모든 세션은 §0을 먼저 읽으세요. 트랙 `docs/tracks/active/track_naver-ad-optimization.md`
> D-NAO-64/65 항목이 결정의 단일 진실 원천입니다. 특히 D-NAO-65 (b) 스프린트 순서 **DL→B→UI→L2→L3**
> 와 "B 선행 실측 — 소재-레벨 입찰 구조 공식 문서 확정(2026-07-20 09:35)"이 이 스프린트의 착수 근거·스펙 원문.
> 선행 스프린트: DL(일일 손실 고삐, `docs/PLAN_naver-ad-daily-loss-leash.md`) 완료·배포. B는 DL이
> 남긴 **"pause 예외② lever_broken"의 근본 수정**이다.

## §0 방향 고정 (변형 금지 — 변경은 Jino 승인 후 D-N 기록)

### 이 스프린트의 목적 (D-NAO-65 (b), Jino 원문)
> DL의 "pause = 레버 불능 예외" 중 **예외②(입찰-지출 연결 끊김)** 를 해소한다. DL §0:
> *"②입찰-지출 연결 끊김(그룹입찰 50인데 실측 CPC 800대 — 입찰을 낮춰도 지출이 안 줄어드는 그룹,
> 소재-레벨 입찰 정황). 예외②는 근본수정 B 스프린트가 해소하면 해제."*

**문제(실측·공식 확정)**: ours 쇼핑 그룹 다수가 그룹입찰 50인데 실측 CPC 900~1800이다. 원인은
소재(`SHOPPING_PRODUCT_AD`)의 `adAttr={"bidAmt":N,"useGroupBidAmt":false}` — `useGroupBidAmt=false`인
소재는 **소재 개별 bidAmt가 실효 입찰**이고 그룹입찰을 무시한다(네이버 공식 apidoc, D-NAO-65 B 선행 실측).
**우리 자동운영의 모든 입찰 레버(고삐·밴드·스톱로스·재시작)는 그룹입찰만 쓰므로 이런 그룹에서 전부 헛돈다.**
DL2가 이를 임시로 `lever_broken`(CPC>5×그룹입찰) 판정 → 터미널 pause로 지혈 중이지만, pause는
D-NAO-65 철학상 정책이 아니라 예외여야 한다. B의 목적 = **소재-레벨 실효입찰을 인식·제어**해 고삐·밴드
레버가 이런 그룹에서도 실효하게 만들고, 예외②를 pause에서 leash로 되돌린다.

### 최종 목적 (상위, D-NAO-59 — 변형 금지)
**우리판 MOP의 최종 목적 = 총 이익(절대액) 최대화.** 한계 ROAS ≥ BEP 구간에서는 볼륨 확장.
B는 이 목적의 **실행 레버 정합층** — 레버가 실효해야 고삐-일일리셋(DL)·밴드(RL)·스톱로스가
소재-레벨 그룹에서도 총이익을 제어할 수 있다. 볼륨 0=이익 0이므로 kill(pause)보다 leash가 우월하다는
D-NAO-65 원칙이 소재-레벨 그룹에도 적용되게 만드는 것이 B다.

### B가 바꾸는 것 (재설계 아님 = 확장)
목적함수(D-NAO-1/59)·실행 엔진(harness·가드레일·시간당/일 레인·응답곡선·CD1~5·RL1~5·DL1~4)은
**전부 재사용**. 바꾸는 것 4가지(Phase B1~B4):
1. **인식(읽기)**: 기존 `get_ads`가 이미 부르는 `/ncc/ads` 응답에서 `adAttr`(bidAmt·useGroupBidAmt)·
   userLock을 추가 파싱해 저장(추가 API 콜 0). 소재별 실효입찰을 시스템이 최초로 안다.
2. **실효 입찰 파생**: 그룹의 "실효 입찰" 단일값을 소재 실효입찰에서 파생. 진단 보드·스톱로스 임계
   (=입찰×10)·`lever_broken` 판정기를 **그룹입찰 → 실효입찰** 기준으로 재정의. 예외②의 오판 표면이
   구조적으로 축소.
3. **제어(쓰기)**: 소재 bidAmt 직접 수정(`PUT /ncc/ads`) 레버 신설(`target_type='ad'`). 고삐·밴드가
   `useGroupBidAmt=false` 소재를 실효 레버로 잡아 하향/상향한다.
4. **예외② 해소 + 재개 흐름**: B3 제어가 라이브 검증되면 DL2의 `lever_broken→터미널 pause`를 은퇴시키고,
   이미 pause된 레버끊김 그룹을 소재입찰 하향 후 resume하는 흐름을 배선.

### 소재-레벨 입찰 구조 (공식 스펙, D-NAO-65 B 선행 실측 — 변형 금지)
- 소재 `Ad.adAttr` = JSON **문자열** `{"bidAmt":N,"useGroupBidAmt":bool}`. 스펙 원문 "Bid infomation of
  this instance. **(Required if Shopping Ad)** #required-update". 소재 type=`SHOPPING_PRODUCT_AD`.
- `useGroupBidAmt=true` → 그룹입찰 사용 / `false` → 소재 bidAmt가 실효 입찰.
- 쓰기 = `PUT /ncc/ads/{adId}{?fields}` (AdRequest.adAttr) — 읽기·제어 모두 공식 API로 가능.
- ⚠️`adAttr`는 JSON **문자열**이라 파싱 필요(추정 금지 — 라이브 실측 스윕이 B1의 첫 작업).

### 금지선 (절대 불변)
- **BEP 하한·킬스위치 3중 방어·일예산 불가침·쿨다운 2h·±15% 클램프 불변.** 소재-레벨 쓰기도
  guardrail_gate 전량 통과(우회 경로 금지) — 단일 초크포인트 `naver_execution_harness` 유지.
- **개별 캠페인 이름/ID 하드코딩 금지**(D-NAO-65 ③ 소방수 금지). 맥세이프 등 문제 캠페인은 전역 규칙의
  구멍 신호로만 다루고 산출물은 항상 전역 규칙. `optimizer='ours'` + `auto_operate=True` 집합으로만 스코프.
- **소재별 차등 입찰 의도 보존**: 운영자/MOP가 상품별로 다르게 건 소재 bidAmt를 파괴하지 않는다 —
  제어는 소재 bidAmt를 개별 조정할 뿐 `useGroupBidAmt`를 강제 전환하지 않는다(Q3 (b) 기각 근거).
- **예외② 은퇴는 B3 라이브 검증 후에만**: 인식(B2)만으로 lever_broken pause를 끄면 그룹이 pause에서
  풀리는데 제어(B3)는 아직 없어 **통제 불능 출혈 갭**이 생긴다. B2까지 예외② pause는 안전망으로 유지.
- **마이그레이션 최소**: 신규 테이블 금지. `NaverAdgroupProduct`에 additive nullable 컬럼만
  (LESSONS #14 additive 마이그, 하위호환).
- 모델 라우팅: 구조=Fable, 설계·구현=Sonnet(단순)/Opus(행위변경·판정), 리뷰=Opus 독립 적대적
  (**Fable 금지·5R 이내**·행위변경/제어 페이즈 필수 GATE). codex 소급.

### 스코프 밖 (이 계획서에 포함 금지 — D-NAO-65 (b) 후속 스프린트)
- **UI(sellc 캠페인별 loss 정책 스위치)** — B는 백엔드 인식·제어만. 소재-레벨 노출/토글 UI는 UI 스프린트.
- **L2(예산 자동증액)·L3(인벤토리 확장·시간대 가중)** — 예산 변경 개방은 B 스코프 밖(불변).
- **키워드(파워링크) 소재-레벨 입찰** — 파워링크는 키워드 입찰이 이미 직접 유효(useGroupBidAmt 커플링은
  키워드에만 있고 update_keyword_bid가 이미 처리). B는 **쇼핑 소재**만.

## 구조 (Agent / Harness / SA — 원칙18, 전부 기존 재사용 + 최소 확장)

```
인식층 (일 1회 08:20 sync, 기존 shopping_ad_product_sync에 편승 — 추가 크론 0)
├── [B1] naver_sa_ad_fetcher.get_ads          ← adAttr(bidAmt·useGroupBidAmt)·userLock 파싱 추가(순수)
└── [B1] shopping_ad_product_sync.sync_*       ← NaverAdgroupProduct 스냅샷에 소재 입찰 필드 적재(기존 sync 확장)

실효입찰 파생 SA (신규 순수 SA, 원칙18 단일책임)
└── [B2] effective_bid.group_effective_bid(db, adgroup_id) → int|None
        소재 실효입찰 = ad.bidAmt if useGroupBidAmt=false else group.bidAmt
        그룹 단일 실효값 = max(소재 실효입찰들)  ← 보수(과-pause 방지·최대 CPC 정합)

진단층 (기존 account_diagnosis — 그룹입찰 → 실효입찰 기준으로 재정의)
├── [B2] shopping_pause_candidates: 스톱로스 임계=실효입찰×10 · lever_broken=CPC>5×실효입찰
│         (effective_bid SA 주입) — 예외② 오판 표면 축소(실효입찰≈CPC라 lever_broken 대부분 소멸)
└── [B2] shopping_group_bep / shopping_group_growth(밴드): 실효입찰 기반 시뮬 입력(bid_simulator)

제어(쓰기)층 (기존 단일 초크포인트 — target_type='ad' 대칭 확장)
├── [B3] naver_sa_writer.update_ad_bid(ad_id, bid_amt)   ← PUT /ncc/ads?fields=adAttr(신규 어댑터)
├── [B3] naver_execution_harness._execute_update_bid     ← keyword/adgroup에 'ad' 분기 추가(대칭)
├── [B3] guardrail_gate.check                            ← entity_id=ad_id로 쿨다운·클램프·BEP(불변)
└── [B3] proposal_writer                                 ← 고삐·밴드가 실효레버(useGroupBidAmt=false 소재)로 라우팅

예외② 해소·재개 (B4)
├── [B4] account_diagnosis: lever_broken→pause 은퇴, 소재-레벨 bid_down 라우팅으로 대체
└── [B4] 이미 pause된 레버끊김 그룹: 소재입찰 하향 → shopping_resume_candidates resume(쿨다운 준수)

데이터: NaverAdgroupProduct(+additive 컬럼) · naver_change_log · NaverEntity · naver_ad_daily — 재사용.
★마이그레이션 = additive 컬럼 1회(NaverAdgroupProduct), 신규 테이블 0.
```

## 6개 설계 질문 — 결정

### Q1 인식(읽기)층: 어디에 저장하나 → **NaverAdgroupProduct additive 확장 + 기존 sync 편승**
- **저장소 = `NaverAdgroupProduct`에 additive nullable 컬럼**: `ad_id`, `bid_amt`, `use_group_bid_amt`,
  `user_lock`. 근거: grain이 **정확히 일치** — 각 `SHOPPING_PRODUCT_AD` 소재 = (adgroup_id, mall_product_id)
  1행(이미 unique 제약). 별도 테이블(neue) / NaverEntity(entity_type='ad') 대비 마이그 최소·조인 불필요.
- **NaverEntity(entity_type='ad') 기각**: NaverEntity는 `entity_sync`의 도메인인데 entity_sync는 get_ads를
  부르지 않는다 — 소재 입찰을 채우려면 다른 sync를 손대야 한다. 반면 `shopping_ad_product_sync`는 **이미
  get_ads를 ours-SHOPPING 그룹마다 부른다**(D-NAO-57 A). 그 sync에 편승하는 게 자연(과제 지시대로 실측 확인).
- **sync 비용 = 추가 API 콜 0**: get_ads 응답 파싱만 확장. 크론 신설 없음(기존 08:20 shopping_ad_product_sync).

### Q2 실효 입찰 파생: 단일 실효값 정의 → **max(소재 실효입찰)** · lever_broken은 실효입찰 재정의 후 유지
- **소재 실효입찰** = `ad.bidAmt` (useGroupBidAmt=false) / `group.bidAmt` (true). 그룹 단일 실효값 =
  **소재 실효입찰들의 max**. 근거: (a) **보수** — 임계(입찰×10)가 높아져 과-pause를 막는다, (b) 실측 CPC는
  가장 비싼 실효 소재가 좌우하므로 max가 realized CPC 상한과 정합. (노출가중 평균은 대표성은 좋으나
  임계를 낮춰 과-pause 위험 — pause는 터미널이라 보수가 안전. 노출가중은 B2 GATE에서 라이브 대조 후
  재고 여지, 기본=max.)
- **DL2 lever_broken 판정기와의 관계**: 판정기를 **실효입찰 기준으로 재정의**(임계=실효입찰×10,
  CPC>5×실효입찰). 실효입찰≈CPC가 되므로 lever_broken은 **대부분 자연 소멸**. 단 **B3 제어 라이브 검증
  전까지 예외② pause는 유지**(안전망) — 인식만으로 끄면 통제불능 갭(§0 금지선). 형식적 은퇴는 B4.

### Q3 제어(쓰기)층 → **추천 = (a) 소재 bidAmt 직접 수정(PUT /ncc/ads)**
| 옵션 | 리스크 | 되돌림 | 가드레일 |
|---|---|---|---|
| **(a) 소재 bidAmt 직접** ✅ | 낮음 — 소재별 차등 의도 보존 | change_log before/after adAttr로 완전 원복 | ±15% 클램프=소재 자기 bidAmt / 쿨다운=ad_id / BEP=소재↔상품(더 정밀) |
| (b) useGroupBidAmt=true 강제 | **높음** — 운영자/MOP 차등 입찰 파괴·MOP와 핑퐁·일방 데이터 소실 | 원 useGroupBidAmt 복원해도 원 bidAmt 유실 | 그룹입찰 일원화 후 기존 경로 |
| (c) 하이브리드 | 중 — 분기 복잡 | 부분 | 혼합 |

**추천 근거(Q3 답)**: **(a)**. ① 소재별 차등 입찰은 운영자/MOP가 상품별로 의도적으로 건 값일 수 있어
(§0 금지선) 파괴 금지 → (b)는 일방 데이터 소실·MOP와 핑퐁 위험이라 기각. ② BEP가 **소재 레벨에서 더
정밀** — 각 소재↔단일 mall_product_id↔`naver_product_bep`(그룹은 여러 상품 혼합). ③ 가드레일이 소재
단위로 깔끔히 적용(클램프=소재 자기 bidAmt, 쿨다운=ad_id, BEP=소재 상품). ④ 완전 원복 가능
(change_log adAttr). **(c) 하이브리드는 "그룹의 모든 소재가 useGroupBidAmt=true"인 경우에 한해** 그룹입찰이
곧 실효레버이므로 기존 update_adgroup_bid 경로를 그대로 쓴다 — 즉 제어는 **실효 레버로 라우팅**하되,
소재가 실효레버면 (a), 그룹입찰이 실효레버면 기존 경로. useGroupBidAmt 강제 전환만 금지.

### Q4 guardrail/harness 통합 → **기존 bid_down 재사용 + target_type='ad' 분기(신규 proposal_type 없음)**
- **신규 proposal_type 불필요**: 기존 `bid_up`/`bid_down`을 그대로 쓰고 `target_type='ad'`만 추가.
  `_execute_update_bid`(harness:658)가 이미 `keyword→update_keyword_bid / else→update_adgroup_bid`로
  분기하므로, `ad→update_ad_bid` 한 분기 추가로 대칭 완성. `_ACTION_BY_PROPOSAL_TYPE`·`OPEN_ACTIONS`
  ("update_bid") 불변, `ALL_PROPOSAL_TYPES` 불변(드리프트 가드 회귀 0).
- **ML 가드**: 소재엔 systemBiddingType이 없다 — **부모 광고그룹**이 ML이면 소재 bidAmt PUT도 무의미.
  update_ad_bid는 부모 그룹 `_adgroup_is_manual_bid`(재사용)로 사전 확인, ML/불명이면 fail-closed.
- **킬스위치 3중 방어 불변**: optimizer=='ours' 하드체크(harness) + guardrail_gate + auto_operate.
  change_log는 entity_type='ad'·action='update_bid'로 기록. guardrail 쿨다운/클램프는 이미 entity_id
  제네릭이라 ad_id로 그대로 동작.

### Q5 스코프 → **B = P1 인식 → P2 파생·진단 → P3 제어(카나리) → P4 예외② 해소·재개.** 제어는 B에 포함(B2 분리 안 함)
- 제어를 B2로 미루면 DL 예외② pause가 **영구화**돼 D-NAO-65 "pause는 정책 아닌 예외" 철학과 충돌 →
  제어는 B에서 완결해야 예외②를 leash로 되돌린다. 단 **P3는 카나리 게이트**(ours 1개 캠페인 먼저·Confirm).
- **DL 예외② 해소 시점 = P4**(P3 라이브 검증 후). 흐름: P3 소재-레벨 bid_down 개방 → P4에서 ①lever_broken
  판정기의 pause 라우팅을 소재-레벨 bid_down으로 대체(고삐), ②이미 pause된 레버끊김 그룹(MO형)은
  소재입찰을 밴드로 하향 → `shopping_resume_candidates` resume(쿨다운 2h·D-NAO-19 flip-flop 방지 준수).
- **403이 P3를 막으면**: P1/P2만으로도 lever_broken 오판 대부분 소멸(실효입찰 인식). P3는 API 여건 회복 후
  카나리. 즉 B는 P2까지 "인식으로 지혈 정확화" + P3~P4 "제어로 근본 해소"의 2단계로 안전 분해된다.

### Q6 403 레이트리밋 대응 → **P1 스윕 = 추가 API 콜 0(편승) · P3 쓰기는 제안당 소수**
- **P1 실측 스윕 = 0 신규 콜**: get_ads는 이미 08:20 shopping_ad_product_sync가 ours-SHOPPING 그룹마다
  부른다(D-NAO-61 실측 ~38 그룹). B1은 그 **응답 파싱만 확장**(adAttr) — 콜 수 불변. 착수 실측 스윕은
  이미 수집된 데이터(또는 read-only 1회 스윕)로 useGroupBidAmt 분포 분석.
- **백오프**: get_ads→`_get`이 이미 429/5xx 지수 백오프 재시도(1s·2s·4s, fetcher:74) 내장. 신규 없음.
- **크론 시간대**: 08:20(레인 08:50·:20 이전, 아침 sync 러시 회피 — 기존 sync 시각 그대로).
- **API 콜 예산 실측(B1 첫 작업)**: ours-SHOPPING 그룹 수 × (그룹당 get_ads 1콜). D-NAO-61 기준 38 그룹
  → 38콜(기존과 동일). P3 쓰기는 제안 승인분당 PUT 1 + 재조회 GET 1(few) — 스윕 아님.

## Phase 계획 (각 Phase: 구현(Sonnet/Opus,TDD RED→GREEN)→독립 적대적 리뷰(Opus,5R,행위변경/제어 GATE)→PR→safe_deploy(CAS)→라이브 합격 시나리오 검증(원칙22)→트랙/계획서 §7 갱신→HANDOFF)

### B1 — 인식(읽기+저장) · 설계질문 1·6
**무엇**: 소재 실효입찰을 시스템이 최초로 인식·저장(행위변경 없음, read-only).
- `naver_sa_ad_fetcher.get_ads`: 응답 각 소재의 `adAttr`(JSON **문자열** — `json.loads` 파싱, 실패 시
  None 안전) → `bid_amt`·`use_group_bid_amt` 추출 + `userLock`·소재 status 추가. 반환 dict에 필드 추가
  (기존 소비자 `collect_adgroup_products`는 mall_product_id만 읽으므로 회귀 0). **mall_product_id 없는
  소재도 이제 포함할지**: 현행은 mall_product_id 없으면 skip(매핑 대상 아님) — B1은 이 필터 유지(입찰 인식
  대상 = 상품 소재). 단 adAttr 파싱 실패/부재는 필드 None(부분응답 견고).
- `NaverAdgroupProduct` additive 컬럼(nullable): `ad_id String(50)`, `bid_amt Integer`,
  `use_group_bid_amt Boolean`, `user_lock Boolean`. alembic additive 마이그 1개(LESSONS #14 — 기존 행
  하위호환, backfill 불필요).
- `shopping_ad_product_sync.collect_adgroup_products`/`sync_adgroup_products`: rows에 새 필드 채워 적재
  (스냅샷 교체 로직 불변).
- **실측 스윕 산출물**(원칙22): ours-SHOPPING 전 소재의 (useGroupBidAmt 분포·소재 bidAmt vs 그룹입찰·실측
  CPC) 표 — 예외②(그룹 50 vs CPC 800)가 소재-레벨에서 어떻게 나타나는지 라이브 확증. 이 표가 B2 임계
  검증의 baseline.
**어디**: `naver_sa_ad_fetcher.get_ads`(파싱 확장)·`models.py NaverAdgroupProduct`(컬럼)·마이그 1개·
`shopping_ad_product_sync`(적재 필드). 신규 순수 SA 없음.
**완료 기준(원칙22)**: prod read-only 스윕에서 D-NAO-64 MO(그룹입찰 50·실측 CPC 800대) 소재의
useGroupBidAmt=false·소재 bidAmt≈800 실측 확인. 08:20 sync 후 NaverAdgroupProduct에 소재 입찰 적재 실증.
**리뷰 GATE**: 행위변경 없음(read-only)이나 마이그 포함 — Opus 검토(경량). 확인: adAttr 문자열 파싱 견고
(비JSON·필드누락 None)·기존 소비자 회귀 0·마이그 additive(기존 행 파괴 0)·get_ads 콜 수 불변(0 신규).

### B2 — 실효 입찰 파생 + 진단 재정의 · 설계질문 2
**무엇**: 그룹 "실효 입찰" 단일값을 파생하고, 진단 보드·스톱로스 임계·lever_broken을 그룹입찰 →
실효입찰 기준으로 재정의. **제어(쓰기)는 아직 없음** — 예외② pause는 안전망 유지.
- 신규 순수 SA `effective_bid.py`: `group_effective_bid(db, adgroup_id) → int|None`. 소재 실효입찰
  (useGroupBidAmt=false→소재 bidAmt / true→그룹입찰) → **max**. 소재 데이터 부재 그룹은 None(폴백=기존
  그룹입찰 — 하위호환, fail-safe). 단일책임(원칙18).
- `account_diagnosis.shopping_pause_candidates`: 스톱로스 임계(`bid_amt × LOW_CLICK_THRESHOLD`)·
  lever_broken(`CPC > k × bid_amt`)의 `entity.bid_amt`를 **effective_bid로 치환**(effective None이면
  기존 그룹입찰 폴백). at-floor 판정도 실효입찰 기준. 결과: 실효입찰≈CPC라 lever_broken 대부분 미발동
  → 예외② 오판 축소. **단 예외② pause 경로 자체는 B2에서 제거하지 않음**(B3 제어 전 갭 방지).
- `shopping_group_bep`/`shopping_group_growth`(밴드): bid_simulator 입력 입찰을 실효입찰로(밴드 하향/상향
  판정이 실효 CPC 기준이 되도록). 그룹입찰만 보던 밴드가 실효입찰을 본다.
**어디**: 신규 `effective_bid.py`(순수 SA)·`account_diagnosis.shopping_pause_candidates`(bid 소스 치환·
effective_bid 주입)·`diagnosis.build_diagnosis`(effective_bid 주입 배선). proposal_writer 불변(아직 제어 없음).
**완료 기준(원칙22)**: prod read-only에서 (a) 실효입찰 인식 후 lever_broken 판정이 B1 스윕 실측과 정합
(실효≈CPC 그룹은 lever_broken=False로 뒤집힘), (b) 진짜 소재-레벨 끊김(소재 bidAmt도 낮은데 CPC 폭등 등
비정상)만 lever_broken 유지, (c) 밴드 판정이 실효입찰 기준으로 이동. **행위**: lever_broken 축소로 08:50
일 레인 pause 후보 감소(오판 pause 방지) 실측.
**리뷰 GATE**: 행위변경(pause 후보 집합 변경) — Opus 적대적. 확인: effective None 폴백이 기존 동작
보존(하위호환)·max 선택이 과-pause 유발 안 함(임계 상승 방향)·예외② pause 안전망이 B2에서 유지됨
(제어 전 조기 은퇴 없음)·밴드 실효입찰 전환이 밴드 논리 회귀 0·non-ours 무노출.

### B3 — 제어(쓰기) 개방: 소재-레벨 bidAmt + 카나리 · 설계질문 3·4
**무엇**: 소재 bidAmt 직접 수정 레버 신설(Q3 (a)). 고삐·밴드가 실효레버(useGroupBidAmt=false 소재)를
잡아 하향/상향. 카나리(ours 1개 캠페인·Confirm 먼저).
- `naver_sa_writer.update_ad_bid(ad_id, bid_amt) → WriteResult`: `PUT /ncc/ads/{adId}?fields=adAttr`,
  body adAttr=`{"bidAmt":N,"useGroupBidAmt":false}`(JSON 문자열 직렬화). 성공 판정=재조회 실측
  (fail-closed) — after adAttr.bidAmt==요청 ∧ useGroupBidAmt==false 확인(update_keyword_bid의
  useGroupBidAmt 이중확인과 동형). bid_amt 사전검증(70~100,000·10원 단위·`_MIN_BID` 재사용). **부모 그룹
  ML 가드**: `_get_adgroup(parent)`가 systemBiddingType!='NONE'/autobid active면 WriteValidationError
  (소재 PUT 무의미). DB 접근 없음(순수 어댑터).
- `naver_execution_harness._execute_update_bid`: writer 분기에 `ad→update_ad_bid` 추가(keyword/adgroup
  대칭). guardrail_gate context는 소재 현재 실효입찰(current_bid) 기준. change_log entity_type='ad'.
- `proposal_writer`: 고삐(`_stop_loss_proposal` 쇼핑 at-floor 아닌 하향)·밴드(`_bid_proposal` adgroup)가
  실효레버를 판별해 `target_type='ad'`·target_id=ad_id로 라우팅(그룹의 useGroupBidAmt=false 소재 대상).
  그룹 전체가 useGroupBidAmt=true면 기존 update_adgroup_bid 경로(Q3 (c) — 실효레버로 라우팅). 소재가
  복수 실효레버면 각각 제안(고삐가 그룹당 1건→소재당 1건, 쿨다운은 ad_id별).
- **카나리**: `OPEN_ACTIONS` 불변("update_bid" 이미 개방)이나 **target_type='ad' 실쓰기는 첫 배포 시
  ours 1개 캠페인·Jino Confirm 승인분만**(D-NAO-5 신규 유형 액션 무조건 승인). 자동발사 0 → 실적 확인 후 확대.
- **B3 GATE 반영(2026-07-20)**: ①카나리 1단계 방향 = **bid_down만**(`_AD_BID_CANARY_DIRECTIONS`,
  ad UP은 2단계 — 상수 확장으로 개방) ②**탐침(probe) UP은 ad 라우팅 제외** — CD3 되돌림 기계가
  'ad' grain을 처리 못 함(`probe_revert._standing_probes`의 before_value 최상위 bidAmt 파싱 vs
  ad의 adAttr JSON 문자열 중첩 + `_conv_direct_today` grain 필터 부재). **탐침의 ad 확장은 별도
  페이즈로 이월**(CD3 'ad' 확장이 선행조건) ③Confirm-only 코드화 — ad 제안은 레인 자동승인·인라인
  실행 금지(시간당=pending 생성만·일 레인=심사/stale 정리 모두 제외), 실행 경로는 콘솔 Confirm만.
  ④라이브 합격 기준 보강: max 소재입찰 하락뿐 아니라 **그룹 실현 CPC 하강**을 확인(2위 소재로의
  노출 이전 효과까지 포착 — max만 보면 놓침).
**어디**: `naver_sa_writer.update_ad_bid`(신규)·`_execute_update_bid`(분기)·`proposal_writer`(실효레버
라우팅)·`guardrail_gate`(entity_id=ad_id, 로직 불변). effective_bid 재사용.
**완료 기준(원칙22)**: 카나리에서 (a) useGroupBidAmt=false 소재에 bid_down 실집행 → 재조회 실측 반영
(CPC 실제 하락 D+1 관측), (b) ±15% 클램프·쿨다운 2h·BEP 하한이 소재 레벨에서 작동, (c) 부모 ML 그룹은
fail-closed 차단, (d) change_log ad 레벨 before/after 기록.
**리뷰 GATE**: 제어(신규 쓰기 경로) — Opus 적대적 필수 GATE. 확인: 단일 초크포인트 유지(harness만 씀)·
재조회 fail-closed(useGroupBidAmt 이중확인)·useGroupBidAmt 강제전환 없음(false 유지만)·부모 ML 가드·
킬스위치 3중·클램프/쿨다운/BEP 소재 레벨 정합·소재별 차등 의도 파괴 0·이중제안(그룹입찰 vs 소재입찰
상호배타) 방어·non-ours 무노출.

### B4 — 예외② 해소 + 재개 흐름 · 설계질문 5
**무엇**: B3 제어 라이브 검증 후, DL2 lever_broken→pause를 은퇴하고 소재-레벨 고삐로 대체 + 이미 pause된
레버끊김 그룹의 재개 배선.
- `account_diagnosis.shopping_pause_candidates`: lever_broken 경로의 **pause 반환을 소재-레벨 bid_down
  라우팅으로 대체**(B3 제어가 실효레버를 잡으므로 leash로 되돌림). 무한출혈 방지 터미널 pause는 **floor_bleed
  (실효입찰도 바닥·N일 지속 출혈)만** 잔존 — 진짜 레버 불능(소재입찰까지 하한인데 CPC 폭등)만 pause.
- **재개(resume) 흐름**: 이미 pause된 레버끊김 그룹(MO형)은 → B3 소재입찰을 밴드로 하향 →
  `shopping_resume_candidates` 정상 경로로 resume 제안(정착창 ROAS≥target·BEP 게이트 종속·쿨다운 2h·
  D-NAO-19 flip-flop 방지). 재개 후 소재-레벨 고삐가 통제. **per-campaign 하드코딩 없이** 전역 규칙
  (lever_broken이었다가 실효레버 확보된 그룹).
- DL2 예외② 판정기 문서/상수(`_LEVER_BROKEN_CPC_MULTIPLE`)는 잔존하되 **pause가 아닌 leash 트리거**로
  의미 전환(레버끊김 = 소재-레벨 제어 대상 신호).
**어디**: `account_diagnosis.shopping_pause_candidates`(lever_broken→bid_down 라우팅)·`proposal_writer`
(레버끊김 그룹의 소재 bid_down·resume)·문서. 신규 SA 0.
**완료 기준(원칙22)**: 라이브에서 (a) 과거 lever_broken pause 그룹이 소재-레벨 bid_down 고삐로 대체
집행·pause 미발동, (b) 이미 pause된 MO형 그룹이 소재입찰 하향 후 resume→leash 통제 실증, (c) 진짜
레버불능(소재입찰까지 하한·CPC 폭등)만 floor_bleed pause 잔존, (d) flip-flop 0(쿨다운 준수).
**리뷰 GATE**: 행위변경(pause→leash 은퇴) — Opus 적대적 필수 GATE. 확인: 예외② 은퇴가 통제불능 갭 안 만듦
(B3 제어 실효 전제 검증)·재개가 재출혈 사이클 안 만듦(BEP 게이트 종속·DL4 정합)·floor_bleed 안전망 잔존·
flip-flop 방어·MO 재개가 per-campaign 하드코딩 아님(전역 규칙).

## 리스크·결정 로그
- **max 실효입찰의 과소 대표성**: 한 그룹에 useGroupBidAmt=false 소재가 여럿·입찰 편차 크면 max가 저입찰
  소재의 출혈을 가릴 수 있다. 완화: 제어(B3)는 소재별 개별 하향이라 저입찰 소재도 자기 레버로 잡힌다
  (진단 단일값만 max, 제어는 소재별). B2 GATE에서 노출가중 대안 라이브 대조 후 재고 여지.
- **예외② 은퇴 타이밍(B2 vs B4)**: B2에서 은퇴하면 통제불능 갭 → **B4(B3 제어 검증 후)로 고정**. §0 금지선.
- **useGroupBidAmt 강제전환 유혹((b))**: 그룹 일원화가 코드는 단순하나 MOP/운영자 차등 의도 파괴·핑퐁 →
  영구 기각. 제어는 소재 bidAmt만, useGroupBidAmt=false 유지.
- **소재 복수 실효레버 → 제안 폭증**: 고삐가 그룹당 1→소재당 1이면 제안 수 증가. 완화: 쿨다운 ad_id별·
  DL3 bid_down 일일상한 면제는 소재별 적용(진동은 쿨다운 2h 방어). B3 라이브에서 제안 수 실측.
- **부모 ML 그룹의 소재 입찰**: ML 그룹은 소재 bidAmt도 무시 → 소재 제어도 무의미 → 이 경우는 예외①
  (ML pause) 유지(B가 해소 대상 아님). update_ad_bid 부모 ML 가드로 fail-closed.
- **403 레이트리밋**: P1 인식은 0 신규 콜(편승)이라 무영향. P3 카나리 쓰기가 403이면 재조회 fail-closed로
  안전(미반영 표면화). API 여건 회복까지 P3 지연 가능(P1/P2로 지혈 정확화는 이미 확보).
- **adAttr 스키마 드리프트**: 라이브 API 403이라 스펙은 공식 apidoc 기반 — B1 첫 스윕이 실 응답으로
  `adAttr` 문자열 구조를 확증(추정 금지, 원칙22). 스윕 결과가 스펙과 다르면 B2 착수 전 재설계.

- **★B4 GATE P2 자동 결정(2026-07-20 11:50, Claude 추천안 — Jino 번복 가능)**: ①**재개 정책 = "바닥(70원)에서 재개"** — 판매가/target_roas 안전선은 CVR=1 상한이라 실질 무필터(MO 실측: 소재 800 vs 현실 BEP CPC ~78원). 재개=최소 노출로 증거 축적 재개, 올리는 건 DL4 밴드 재시작의 BEP 게이트가 실증과 함께(D-NAO-59 "증거 없이 올리지 않는다" 정합). rationale 정직화(카나리 체제에선 교정도 Confirm-only임을 명시 — "자동 감시" 문구 금지). ②**카나리 캠페인 = 위임 전면 제외**(delegation·브리핑) — 카나리 기간은 그 캠페인 전체가 사람 감독 기간, 졸업(상수 제거) 시 자동 해제. ③flip-flop 방지(재pause 3일 쿨다운)+ML 사전 제외.
- **★B3 카나리 캠페인 = 맥세이프 확정(2026-07-20 11:06, Jino 원문 "수정 끝나면 배포하고 카나리는 맥세이프로 열자")**: `AD_BID_CANARY_CAMPAIGNS = frozenset({"cmp-a001-02-000000010769985"})`. GATE P2 수정(탐침 제외·Confirm-only·DOWN 한정) 반영 후 배포와 함께 개방. ad 제안은 콘솔 Confirm 전용이라 실집행은 Jino 승인 경유. 참고: 맥세이프 현재 활성 그룹=컨텐츠(070109620)뿐(MO·PC pause) — 미연결 그룹 실집행 후보는 B4 재개 흐름과 결합 시 확대.
- **★B2 GATE P2 자동 결정(2026-07-20 10:15, Claude 추천안 — Jino 번복 가능)**: ①P2-1 임계=실효×10은 유지(통계적으로 옳음: "실효입찰 10클릭 무전환 증거". pre-B2 임계 500이 오히려 1클릭 미만 증거로 발사하던 것) — 침묵 대역의 진짜 원인은 3일 폴백 창이므로 **레버 미연결(source='ad') 유닛의 증거 창 = 만성 7일**로 확장(이 유닛은 우리가 입찰을 안 바꿔 창 리셋 우려 구조적 부재 = DL1 시점 정합 유지. 일 2,843원+ 출혈은 7일 내 도달, 미만은 진짜 증거 부족=성급 사살 금지 정당). ②P2-2 밴드 레인(RL3·bep·growth)의 미연결 그룹 그룹입찰 발사 억제(지출 무효+창 리셋 오염+슬롯 낭비 차단) — 계획서 B2 명시 항목의 실질 완성. ③lever_broken 트리거는 sync된 그룹에서 도달 불능화(CPC≤실효라 5×실효 불가)를 수용 — 미sync 그룹 폴백용으로 유지, sync 그룹의 안전망은 ①의 7일 창 밸브가 대체.
- **★Fable 계획 검토 승인(2026-07-20 09:50)**: 6개 설계 결정 전부 승인. 강조 2: ①sync 편승=추가 API 콜 0(403 리스크 구조 회피) ②useGroupBidAmt 강제 전환 금지=운영자 의도 보존 + 제어는 항상 실효 레버로 라우팅. 관측 조건: B2의 max() 실효입찰이 스톱로스 임계를 올리므로(실효 800→임계 8000) 임계 상승이 지혈 지연을 만드는지 B2 라이브에서 실측.

## §7 체크리스트 (현재 위치)
- [x] B0 계획서 작성·D-NAO-65 방향고정 (이 문서) — 완료(2026-07-20)
- [x] B0-r Fable 계획 검토 승인(2026-07-20 09:50, 관측 조건: max 실효입찰의 임계 상승 실측)
- [x] B1 인식 — 완료(2026-07-20): 배포·마이그 c5d6e7f8a9b0·sync 실측 36그룹/88매핑, ★85/88(96%) useGroupBidAmt=false 확정. PR #65. 2284 passed.
- [x] B2 실효입찰 파생 SA + 진단 재정의 + 미연결 억제 — 완료(2026-07-20): GATE 2R PASS(1R P2 2건: 침묵 대역→7일 증거 창·밴드 미전환→전 레인 억제). 배포 1b3f8ec·2314 passed. 라이브: 30/33 미연결 정확 판정·보드 38→32. ★B3 이월: 소재입찰 change_log 추적 시 미연결 창='마지막 소재입찰 변경 이후' 절체. 잔여 관측: 11:20 레인 미연결 hold·임계 미달 무전환 비용 합계.
- [x] B3 제어 개방 — 완료(2026-07-20): GATE 3라운드 PASS(P2 4건 수정: 탐침 미회수·Confirm-only 부재·delegation 5번째 경로·pending 홍수). 배포 3c4bf5c·2359 passed. ★카나리 1호=맥세이프 개방(DOWN만·콘솔 Confirm 전용·자동발사 5경로 봉쇄). 라이브: 카나리·방향·update_ad_bid 로드 확인, MO 소재 551485078 입찰=800 실측(CPC 824 미스터리 완결). 잔여=첫 Confirm 실집행 왕복·그룹 실현 CPC 하강(D+1) 관측.
- [ ] B4 예외② 해소(lever_broken→leash 은퇴) + 재개 흐름
- [ ] PR 병합·트랙 D-NAO-65 B 진행 갱신·HANDOFF

## 스프린트 B 완료 기준 (전체)
B1~B4 구현·배포·라이브 검증 완료. 행위변경/제어 페이즈(B2·B3·B4) 전부 Opus 독립 적대적 리뷰 GATE
PASS(P1·P2 0). 마이그레이션 = NaverAdgroupProduct additive 1개(신규 테이블 0). 라이브 실증:
(1) 소재 실효입찰 인식·저장(MO형 useGroupBidAmt=false·소재 bidAmt≈실측 CPC), (2) lever_broken 오판 축소
(실효≈CPC 그룹 뒤집힘), (3) useGroupBidAmt=false 소재 bid_down 카나리 실집행→CPC 실하락, (4) DL 예외②가
pause에서 소재-레벨 leash로 전환·이미 pause된 레버끊김 그룹 재개→통제, (5) 소재별 차등 입찰 의도 보존
(useGroupBidAmt 강제전환 0). 단일 초크포인트·킬스위치 3중·가드레일 소재 레벨 정합 유지.
