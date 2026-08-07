# 세션 인수인계: BM 벤치마크 레이어 P1~P6 배포·라이브 검증 + 대행사 교체 사후 조사
> 저장일시: 2026-07-22 12:08 KST · 워크트리 `daily-rank-leash-profit-control-71b501` · 브랜치 `claude/agency-learning-bm-layer-667d1b`
> 앞 HANDOFF `session-dfd814/.claude/memory/HANDOFF_agency-wide-learning+expkeyword-fix+BM-layer_20260722.md`를 잇는다(그 세션의 방향 확정을 이 세션이 전량 구현·배포함).
> 새 대화 시작 시 이 파일을 먼저 읽을 것.

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/worktrees/daily-rank-leash-profit-control-71b501`
- prod: `sellc.ohitech.co.kr` · `/home/ubuntu/ohisell/backend` (pm2 `ohisell-backend`·uvicorn:8001·`.venv/bin/python`) · DB `ohisell.db` · **prod는 UTC**(로그 +9=KST)
- 배포: `scripts/safe_deploy.sh`만(CAS, D-NAO-49). prod alembic head = **`d9e0f1a2b3c4`**
- git: **main == prod** (PR #78 병합 완료). **PR #79 오픈 중**(BM 레이어 전체, 병합 대기 — 병합하면 계속 main==prod)

## 2. 이번 세션 완료 목록
- ✅ **EXPKEYWORD 1년 백필 완결·검증**: 총 2,480,337행(2025-07-23~2026-07-21). 3월 갭 26일 재시도 → 전일 **NONE 종결 = 그날 확장검색어 데이터 자체 없음(정상)** 확정(AD_CONVERSION 때와 같은 패턴). 6월 대조 exp 7,415클릭 vs AD(키워드행 4,061+sentinel 3,355=7,416) 99.99% 일치. **★파워링크는 naver_ad_daily에서 sentinel행과 키워드행이 상호보완(합산)** — 쇼핑과 그레인 의미 다름(실측).
- ✅ **PR #78 생성·병합**(EXPKEYWORD 수정 278e5e1 + D-NAO-78/79 문서) → main==prod 복원. failure-memory에 16→12컬럼 드롭 교훈 기록.
- ✅ **대행사 교체 사후 조사**(Jino 질문 "뭐가 바뀌어서 매출이 좋아졌나"): §5 참조.
- ✅ **BM 벤치마크 레이어 P1~P6 전체 구현·배포·라이브 검증**: §4 참조. **PR #79** 생성.
- ✅ 관측(전 세션 승계): 07:40 검색어+전환 크론 자연 수집 합격(쇼핑 전환 3일치 41·44·49건 + **EXPKEYWORD 수정 후 첫 자연 수집 일 6~7천행**) / 08:50 SS 레인 합격(승격 제안 20건 상한 정확·자동발사 0) / 09:10 스윕 합격(6,803행) / 배포 재시작들이 다른 크론 안 놓침(전 레인 ok).

## 3. 확정된 결정사항
- **BM 계획서 = `docs/PLAN_naver-ad-bm-layer.md`가 정본** (Opus 작성→Fable 보강: 캠페인/그룹 신설 감지 op_type 추가·§9-5 대행사 라벨=ours 식별만 필수로 해소). §0 금지선: 관찰 전용·쓰기 0·회계 불활성·프라이어는 optional(fail-open)·집행 범위 불변.
- **P4 저장소 택일(Opus)**: 신규 `naver_bm_benchmark`(value_json) — learning_state는 verify_harness 단일 쓰기 주체라 오염 금지. 향후 bid_rank_slope만 learning_state에 써서 rank_servo 무개조 소비(그래서 IU-R 주입 지점은 이미 존재).
- **P2 보강(Fable 리뷰)**: campaign-grain status_flip = 항상 is_exception=True(그룹은 임계 기반). either-NULL 스킵(P3 롤아웃 첫날 false positive 차단).
- **브리핑 채널**: 기존 diary(ops_diary_entries observe)→vault_export(Obsidian)+Slack(예외 있는 날만). sellC 상설 배너는 초기 스코프 밖. 드릴다운 GET 3종 `/api/naver/ad/bm/{agency-ops,snapshot,benchmark}`.
- **미코드화 갭 2건 — Jino 결정 대기**(이 세션에서 물었으나 미답): ①번아웃 곡선 조기 경보 ②지면(컨텐츠 매체) 노출 구성 감시. BM P5 관측 항목 추가 vs 백로그 D-N 기록 중 택일.

## 4. BM 레이어 최종 상태 (전 Phase 라이브 검증 완료)
| Phase | 커밋 | 라이브 실측 |
|---|---|---|
| P1 스냅샷 SA-1 | 8216133 | 45캠페인·1,008그룹·키워드 90,174 == naver_entity 정확 일치·GET 46 |
| P2 diff SA-2 | fd91094 | bootstrap 0이벤트(정당). 07-21 대행사 실조작=유닛 픽스처(5이벤트 재현) |
| P3 차원 보강 | a2b0808 | 예산·확장검색 1,004/1,008 채움·API 실측 일치. 주간 bm_deep 일요 09:20 |
| P4 SA-3+배선 | 94fa586 | 프라이어 5행: keyword_verified **41,983**·bid_band 쇼핑[50,250,1970]/PL[70,70,890]·구조(PL 고성과 kw p50 138·확장 43%). None 폴백=바이트 동일 |
| P5 브리핑 | f01ab78 | diary #371 "예외 없음" 실기록·Slack 0건 생략·드릴다운 3종 응답 정상. **에이전트 커밋 직전 스톨→검증 후 대리 커밋** |
| P6 크론 정착 | (코드 無) | 재시작 후 next_run 07-23 07:37·07-27 09:20 확인. catch-up 제외(관찰 잡) |
- 테스트 **2814 passed·회귀 0**(BM 신규 55건). alembic 3체인: b7c8d9e0f1a2→c8d9e0f1a2b3→d9e0f1a2b3c4.
- **내일(07-23) 아침 남은 라이브 관문(자동 검증 예약됨, 07:45)**: ①07:37 크론 첫 자율 발화 ②스냅샷 D-1·D 2일치→**SA-2 첫 실diff**(대행사 조작 잡히는지=P2 최종 합격) ③예외 시 Obsidian(09:05 vault)+Slack 실발송(P5 최종) ④08:50 SS4 승격 후보에 대행사 키워드셋 교차 플래그(P4 최종).

## 5. 대행사 교체 조사 결론 (Jino 보고 완료)
- 타임라인: 07-11 신규 대행사 교체→ROAS 3.0→2.0 급락(07-14~18)→07-21 17:00 원복→저녁 전환 +27%(17~23h)·+62%(19~23h, 시간별 실측).
- 신규 대행사 실패 메커니즘: ①컨텐츠 지면 노출 12배(61만 imp, CTR 0.06% vs 검색 1.9%, ROAS 1.4) ②신설 폴드8/플립8 캠페인 = 콜드스타트(입찰 300·이력 0) → 37만원에 전환 1건(**ROAS 0.06**)·번아웃 곡선(12.2만→14.4만→5.3만→0.9만→0.2만) ③맥세이프쇼검 방치(주간 65만, ROAS 0.37). 07-13 이후 8일간 구조 변경 0건.
- 원복 대행사(07-21 17:36~21:20 editTm 실측): 맥세이프쇼검·"폴드8/플립8 키워드" 캠페인 잠금 / 갤럭시 파워링크에 신모델 그룹 3 신설+**키워드 85개**(입찰 1000~1500) / 쇼핑 01.갤럭시TPU에도 신모델 그룹 3 / 입찰 5건 상향. = "검증된 구조+신모델 수요+제값 입찰".
- **entity_sync 사각 실증**: 그룹 신설·예산 변경 미추적이었음 → BM P2/P3이 정확히 메꿈. 07-21 조작 전체 = SA-2 수용 기준 픽스처(test_naver_bm_diff).
- ⚠️조사 중 오경보 2건 자가 해소(원칙22): "07-19 캠페인 45→28 붕괴"=일별 수집의 아침 지연 적재를 읽은 아티팩트(hourly 교차검증으로 45 전부 정상 확인) / "시간별 수집 사망"=매일 09:10 일괄 스윕 구조(정상).

## 6. 알려진 이슈 / 주의사항
- **P4/P5 에이전트 스톨 2회**(600s watchdog): P4=SendMessage 재개로 완주, P5=커밋 직전 스톨→작업물 스테이징 상태 확인·전체 테스트 직접 실행(2814 passed)·대리 커밋. 장시간 Opus/Sonnet 백그라운드 에이전트는 스톨 대비 재개(SendMessage)·작업물 회수 패턴 유효.
- **codex 리뷰 미실시**(한도 회복 07-23): BM P1~P5 전 커밋 + 전 세션 IU-R·B-X·SS·EXPKEYWORD 소급 대상. **07-23 codex 소급 리뷰 필수.**
- naver_search_term_daily 파워링크 전환=구조적 0(귀속 불가) — 판단 게이트 금지 유지.
- keyword_verified 1행 663KB(41,983 키워드 JSON) — 일 1회 교체 upsert라 허용, 비대해지면 분할 검토.
- 라우터 prefix는 `/api/naver/ad`(옛 추정 `/api/naver-ad` 아님).
- Jino 미답 질문 1건: §3 미코드화 갭 2건 처리 방향.

## 7. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/PLAN_naver-ad-bm-layer.md` | BM 계획서(정본 — §0 금지선·§3 diff 규칙·실전 픽스처) |
| `backend/app/services/naver_ad/bm_snapshot.py` | SA-1 구조 스냅샷(0 GET+마진 46 GET) |
| `backend/app/services/naver_ad/bm_diff.py` | SA-2 조작 감지(노이즈 필터 4종) |
| `backend/app/services/naver_ad/bm_benchmark.py` | SA-3 프라이어 산출 |
| `backend/app/services/naver_ad/bm_briefing.py` | P5 예외 브리핑(diary+Slack) |
| `backend/app/services/naver_ad/bm_harness.py` | BM Harness(SA-1→2→3→브리핑, 전면 fail-open) |
| `backend/app/routers/naver_ad.py` | 드릴다운 GET 3종(:1449~) |
| `docs/tracks/active/track_naver-ad-optimization.md` | 트랙(현재 진행 단계에 이 세션 기록) |

## 8. 다음에 할 작업 (미완료)
- [ ] **내일 07:45 자동 검증**(예약됨): §4의 라이브 관문 4개 확인. 실패 시 원인 조사.
- [ ] **codex 소급 리뷰(07-23)**: BM P1~P5 + 전 세션 커밋. 지적사항은 대화형 검증(원칙19).
- [ ] **PR #79 병합**(Jino 또는 검증 후) — 병합해야 main==prod 유지.
- [ ] **파워링크 검색어 자동 제외+재심사 루프 설계**(Fable): EXPKEYWORD 데이터 흐르는 중(일 6~7천행). 비용 기반 컷+in-out 재심사(전 HANDOFF §5 설계 방향).
- [ ] P4 잔여: bid_rank_slope(agency_op 데이터 쌓이면 learning_state에 산출→rank_servo 무개조 소비).
- [ ] 미코드화 갭 2건(번아웃 경보·지면 구성 감시) Jino 결정 후 처리.
- [ ] SS 상수 첫 주 캘리브레이션(제외 후보 0 지속 시 clk 문턱 완화) — 승계.
- [ ] bm_deep 첫 실행(07-27 일요 09:20) 관측: 제외키워드·소재수 채움 확인.

## 9. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

`.claude/worktrees/daily-rank-leash-profit-control-71b501/.claude/memory/HANDOFF_bm-layer-P1-P6-deployed+agency-investigation_20260722.md` 읽고 이어서. 핵심=BM 레이어 P1~P6 배포·라이브 검증 완료(PR #79 오픈)·대행사 교체 조사 완료·내일 아침 라이브 관문 4개(07:37 자율발화→SA-2 첫 실diff→브리핑→SS4 교차). 다음=관문 확인+codex 소급 리뷰(07-23)+파워링크 자동 제외 설계. 라우팅: 구조=Fable·설계/구현=Opus·단순=Sonnet.
