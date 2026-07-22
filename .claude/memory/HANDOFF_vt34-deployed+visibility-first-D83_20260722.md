# 세션 인수인계: VT3·VT4 완결·배포 + main==prod 회복 + 가시성 우선(D-NAO-83) 확정 + 03 가격 재구성(D-NAO-84)
> 저장일시: 2026-07-22 21:40 KST · 워크트리 `session-409bd8` · 브랜치 `claude/px-vt-sprint-deployment-9082b8`(=main)
> 앞 HANDOFF `bm-layer-p1-p6-deployment-0d68d5/.claude/memory/HANDOFF_px-vt-sprints-deployed+ops-investigation_20260722.md`를 잇는다.
> 새 대화 시작 시 이 파일을 먼저 읽을 것.

## 1. 프로젝트 위치 및 환경
- 워크트리: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/worktrees/session-409bd8`
- prod: `sellc.ohitech.co.kr` · pm2 `ohisell-backend`(:8001) · DB `ohisell.db` · prod=UTC(+9=KST) · 배포=safe_deploy.sh만
- **prod alembic head = `d22ce37d3adb` 단일**(이 세션에서 head 분기 해소·적용)
- **main==prod 완전 정합**(PR #81·82·83 병합). 이 브랜치 = main과 동일 + 이후 docs 커밋 몇 개(푸시됨).
- ⚠️prod 임시 스크립트: `from app.database import SessionLocal` 먼저(dotenv). f-string 안 백슬래시 금지(py3.10). diary 테이블=`ops_diary_entries`(naver_diary 아님). change_log에 event_type 컬럼 없음.

## 2. 이번 세션 완료 목록
- ✅ **main==prod 회복**: 전 세션 "PR #80 오픈"은 stale(실제 14:47 병합) — 병합 뒤 구 브랜치에 쌓인 13커밋(PX codex 3R 수정 3건+VT 전체)을 **PR #81**로 병합. alembic 고아 head `d22ce37d3adb`(PR #68 유래)를 `e0f1a2b3c4d5` 위로 재부모화(`90e0efe`)·prod upgrade 적용(단일 체인).
- ✅ **VT3(소재 CTR 경보, D-NAO-82②) 구현·배포·라이브 합격**: `ctr_alert.py` SA(W1 최근1일 imp≥200∧clk0∧rank≤4 + W3 rolling3일 기대클릭 −80% 분기·트레일링 CTR 폴백 사다리·부분적재 가드·rank_sum=0 오탐 차단) + 일 레인 브리핑(경보 있는 날만 diary+Slack, 그룹 dedupe) + 탐색 래더 skip 게이트(트리거 통과 후 기록). W1 창 신설 근거=착수 전 실측(04 그룹별 3일 확정치에 클릭 1~3 존재 — 3일 창만으론 단일일 가뭄 못 잡음). 구현=Sonnet 에이전트.
- ✅ **VT4(신수요 개척+플로어 결함, D-NAO-82①) 구현·배포·라이브 합격**: ★구조 결함 발견 — 탐색 스텝 하한 70원은 파워링크 키워드 규격 오적용(쇼핑 최소입찰=50원, prod 158그룹 bid=50 라이브 실증). 50원 그룹 스텝 영구 소실(cap 60<70). 수정=쇼핑 하한 50+눈먼 경로 최소증분 승격(+10원, 실관측 기울기 경로 제외=과열밴드 오버슈트 방지)+수요 우선 정렬(플로어≤100·표본미달·7일 노출 내림차순 우선). 구현=Opus 에이전트.
- ✅ **codex challenge 1R→수정→2R AGREE-ALL**: 1R P1×2(★실행층 3겹 — guardrail_gate:154·naver_sa_writer×3·harness가 여전히 70원 하한으로 60원 발사 거부+실패 행은 쿨다운 미기록=매시 재시도 루프 → campaign_type 인지형 게이트(SHOPPING=50)+harness `_build_guardrail_context`에 campaign_type 배선+writer grain별 하한(adgroup/ad=50·keyword=70) 관통 수정 / ctr_alert 예외 fail-open→전파(캠페인 레인 fail-soft=실쓰기 0 방향)) + P2×3 수용(부분적재·브리핑 dedupe·rank_sum0) + N+1 기각(기존 BX1 구조·백로그 — codex PARTIAL AGREE). GATE 자체 발견 1건(CTR skip을 트리거 뒤로 재배치=시간당 blocked 소음 차단 `039d768`). **2981 passed·회귀 0. 배포 17:33 KST(7파일+alembic·부팅 200)**.
- ✅ **라이브 검증(원칙22)**: ctr_alert 실판정 — 04·03 무경보(D-1 클릭 존재=정당)·부분적재 가드 맥세이프 실발동(37.5%→억제)·**★뮤패드 캠페인 진짜 CTR 경보 7그룹 발견**(밴드 내 rank 2.6~3.7·노출 최대 1,723·클릭 0 → 07-23 08:50 첫 브리핑=사람 처방 대상). VT4 sim: adaptive_step(50,6.39)→60·게이트 통과.
- ✅ **첫 실발사 라이브 합격(17:44 시간당 레인 수동 실행=크론 동일 경로)**: 03 쇼핑 3그룹(13미니·12미니·15) **50→60 실집행**(change_log 370~372 before/after 기록) — 오늘 아침까지 구조적 불가였던 동작. 가드레일 정상 차단 동반(BEP 미달·일일 3/3 캡·bleeding·경제성 상한). PR #82 병합.
- ✅ **★D-NAO-82① 전제 정정(레버 오독)**: "아이폰17 노출 34%가 50원 방치"는 오독 — 그룹입찰 50=비활성 레버, 실제 소재입찰이 오늘 1,760→2,290(경제성 상한) 스텝됨. 17에어=bleeding 고삐 보호. 더 못 미는 건 상한·고삐 정상 작동. 교훈=effective_bid(소재 레버) 함께 볼 것(LESSONS #12).
- ✅ **아이폰 수명주기 정정(Jino)**: 아이폰17=2025-10 런칭 성숙 모델(신수요 아님)·아이폰18=2026-10 예정. **방향 승인: B(수명주기 인식)+C(런칭 플레이북) — 다음 스프린트 후보.**
- ✅ **갤럭시 Z8(플립8·폴드8·폴드8울트라) 발표일(오늘) 런칭 트렌드 관찰 착수(Jino 지시)**: 대행사 8시리즈 캠페인 전체 리뷰 → **ref 37**에 baseline 고정(발표 직전 07-17~21: 쇼핑=건강 CTR2~5%·순위2.7~3.1·플로어50·폴드8울트라 노출 32→231 급증 / 파워링크 8키워드 캠페인=번아웃 CTR0·순위11~19, 07-22 OFF됨 / 지문방지 폴드8 노출2,007·클릭0 관찰 대상). 대행사 관리=관찰 전용(실쓰기 0·입찰 추천 금지).
- ✅ **D-NAO-84: 03 17프로강화유리(상품 12382833885) 가격 재구성(Jino 실행)**: 18,900(배송포함)→**13,900+배송비 3,000**(N배송·네이버멤버십 배송비 보전=체감 13,900·판매자 수취 불변). 추정 신 BEP ROAS ~1.39(개선). BEP는 주문 중앙값 소스라 지연 반영. **N배송 첫 주문 raw_data로 배송방식 필드 실측→bep_calculator._order_shipping_cost 훅(:146~208) 실배선 대기**(무료배송 N배송 상품 물류비 1,900 vs 3,020 과소 교정).
- ✅ **17프로 유령 지면 심층 진단**: 입찰 2,290=순위 5.1(장중 /stats 실측)·노출 217·클릭 0. 검색어 타겟팅은 정확(아이폰17프로필름 859노출 등)·CTR 0.12%. 자연검색·광고 모두 Jino 눈에 안 보임=노출 점유율 바닥+유령 순위. 07-17 순위 3.14에도 클릭 0(101노출)=타일 경쟁력 문제 병존. PC 가중치 70%=PC 실입찰 1,600. **판매자센터 확인 필요(Jino): 노출제한 여부·가격비교 카탈로그 매칭**.
- ✅ **★D-NAO-83 가시성 우선 확정 + 근거 분석 완료(ref 38)**: 90일 실측 — 가시 임계 4위(5위 밖 CTR 1/3~1/8 붕괴)·★전환단가 순위 무관 평평(8.1~9.4천원)=가시성 구매가 경제성 훼손 안 함·15프로 템플릿(주 3.4만원=밴드 유지·전환4·이익 종료). 설계 3방향: ①유령 스텝 중단 ②증거 구매 창(주 3~5만원/그룹·클릭 10~15개) ③콜드 상한 개혁(캠페인 전환단가×공헌이익). **다음 세션에서 설계·구현(Jino 승인).**
- ✅ LESSONS 3건(#12 레버 오독 / #13 데이터 부재≠0 / #14 순위 중심 보고 — Jino 질책 반영). failures.jsonl 3건(다층 상수 관통·stale PR·레버 오독).

## 3. 확정된 결정사항 (전부 트랙 파일에 원문 인용 기록)
- **D-NAO-83**: 가시성 우선 — "노출이 기본". 유령 지면(순위>5, 가시 임계 4) 스텝 금지·증거 구매 창·콜드 상한 개혁. 근거=ref 38(전량 자체 실측).
- **D-NAO-84**: 03 17프로 가격 재구성+N배송. 훅 실배선은 첫 N배송 주문 raw_data 실측 후(추정 금지).
- D-NAO-82① 정정(레버 오독), 아이폰 수명주기 정정(17=성숙·18=10월), B+C 방향 승인.
- VT3·4 배선 확정: CTR 경보 스코프=탐색 래더만(밴드·핫셋·vitality 불변), 수요 정렬="순서만".

## 4. 핵심 파일
| 파일 | 역할 |
|---|---|
| `docs/references/38_visibility_first_analysis_20260722.md` | ★D-NAO-83 설계 근거(순위→CTR 곡선·평평한 전환단가·15프로 템플릿·재현 쿼리) |
| `docs/references/37_galaxy_z8_launch_trend_observation_20260722.md` | Z8 런칭 baseline+관찰 포인트 |
| `backend/app/services/naver_ad/ctr_alert.py` | VT3 SA |
| `backend/app/services/naver_ad/exploration.py` | VT4(쇼핑 하한 50·최소증분·prioritize_candidates) |
| `backend/app/services/naver_ad/guardrail_gate.py`·`naver_sa_writer.py`·`naver_execution_harness.py` | codex 1R 관통 수정(campaign_type 인지 하한) |
| `backend/app/services/naver_ad/bep_calculator.py` | :146~208 N배송 판별 훅(대기) |
| `docs/PLAN_naver-ad-vitality.md` | §4 VT3·4 기록 완결 |
| `docs/tracks/active/track_naver-ad-optimization.md` | D-NAO-82정정·83·84 |

## 5. 알려진 이슈 / 주의사항
- **04 오늘(07-22) 노출 968·클릭 0(18시)** — 내일 확정 데이터로 판정. 지속이면 W1 경보 자동 발화 예상.
- **03 회복 중**(오늘 18시 순위 4.5·클릭 5) — 완전 회복 미확정.
- 뮤패드 7그룹 CTR 경보 = 내일 08:50 첫 브리핑 → **사람 처방(썸네일·가격·리뷰) 대상 = Jino**.
- 판매자센터 확인(Jino): 17프로 상품 노출제한·카탈로그 매칭 여부.
- 맥세이프쇼검 대행사 잠금 유지(Jino 판단 대기)·P_Test 접두 복원 미지시(승계).
- PX 첫 재심사 창 08-21·VT 첫 소생 발사 미관측(승계). scheduled-tasks MCP 세션 중 단속적 — durable 예약은 재연결 확인 후.
- codex 소급 리뷰(07-23 09:30 예약)는 BM P1~P5+IU-R·B-X·SS·EXPKEYWORD 대상 — **VT3·4는 이 세션에서 challenge 완료라 스코프 중복 주의**.

## 6. 다음에 할 작업
- [ ] **(아침 자동 확인) 07:45 BM 관문 4개 / 08:50 PX 첫 자동 제외(아이패드종이필름 중복발사 0)+뮤패드 CTR 첫 브리핑+04 W1 경보 여부 / 09:30 codex 소급**.
- [ ] **(아침) 갤럭시 Z8 발표일(07-22) 확정 데이터 관찰** — ref 37 관찰 포인트 3개(쇼핑 서지·전환 첫 발생 / 지문방지 폴드8 클릭 / 파워링크 재개).
- [ ] **(아침) 03·04 판정**: 04 클릭 가뭄 확정 여부 / 03 회복 지속 / **17프로: 2,290 입찰이 산 실제 순위(첫 하루치)·가격 인하(13,900) 후 CTR 반응** — D-NAO-83 설계 입력.
- [ ] **★D-NAO-83 설계·구현**(ref 38 §5 그대로: 유령 스텝 중단·증거 구매 창·콜드 상한 개혁 — 설계=Fable·구현 라우팅 관례). VT3b 백로그 포함 검토.
- [ ] N배송 첫 주문 탐지 → raw_data 필드 실측 → _order_shipping_cost 훅 실배선.
- [ ] B(수명주기)+C(아이폰18 런칭 플레이북) 스프린트 계획(D-NAO-83 뒤).
- [ ] 승계: VT·PX 첫 주 캘리브레이션 / bm_deep(07-27) / bid_rank_slope / SS 캘리브레이션 / 장중 /stats 상설 표면화 백로그.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

`.claude/worktrees/session-409bd8/.claude/memory/HANDOFF_vt34-deployed+visibility-first-D83_20260722.md` 읽고 이어서. 핵심=VT3·4 배포·라이브 합격+main==prod(PR #81~83), D-NAO-83 가시성 우선 확정(ref 38 실측 근거), 03 가격 재구성 13,900+3,000 N배송(D-NAO-84). 다음=아침 자동 3건+Z8·03·04·17프로 관찰 판정+D-NAO-83 설계·구현. 라우팅: 구조=Fable·중요 구현=Opus·단순=Sonnet, 옵션은 추천안 자동.