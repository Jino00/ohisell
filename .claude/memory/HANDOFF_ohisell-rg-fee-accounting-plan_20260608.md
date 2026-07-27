# 세션 인수인계: ohisell-rg-fee-accounting-plan
> 저장일시: 2026-06-08 22:48
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 이 세션 = "오픽스 수수료를 정확히 카운팅하나?" 질문 → RG 수수료 누락 발견 → **RG 수수료 회계 자동화 트랙 신설 + 계획 확정**(discovery+계획+plan-eng-review+Codex 통과). 구현은 아직 0줄.

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: FastAPI, `backend/.venv/bin/python`, 로컬 DB `backend/ohisell.db`(RG 수수료/재고 데이터 없음 — 검증은 prod)
- prod: `https://sellc.ohitech.co.kr` (PM2 `ohisell-backend`, 포트 8001). SSH `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, prod DB `/home/ubuntu/ohisell/backend/ohisell.db`
- 배포(git 없음): 백엔드 scp + `pm2 reload ohisell-backend`, 프론트 `npm run build`(frontend/)+`rsync -az --delete frontend/dist/ → /home/ubuntu/ohisell/frontend/dist/`
- prod 스크립트 실행 시 .env 수동 로딩 필요: `from dotenv import load_dotenv; load_dotenv("/home/ubuntu/ohisell/backend/.env")`. config는 `app.config.get_coupang_config("COUPANG_WING1")`(os.getenv 기반).
- 윙 로그인 필요 시: `$B connect`(헤디드 Chrome) → Jino 로그인. $B=`~/.claude/skills/gstack/browse/dist/browse`. 공개 검색엔진은 봇차단(한국 IP).

## 2. 이번 세션 완료 목록
- ✅ **진단**: 오픽스(WING1) 수수료 카운팅 점검 → 종합조망 net_profit은 `intelligence._agg_fees`에서 **판매수수료+VAT(total_fee)만** 차감. 오픽스 RG 옵션 31개 ∩ revenue_fee 옵션 4개=공집합 → **RG 매출/수수료 100% 누락** 확정.
- ✅ **소스 규명(라이브 실증, 윙 로그인)**: RG 수수료는 Open API 어디에도 없음(settlement-histories deduction=Wing 3P 통짜·RG 상품 API=치수만). **윙 판매자센터 로켓그로스 정산현황**(별도 스트림)에만 있음. 내부 API: `POST /tenants/rfm/v2/settlements/status/api`(주별 정산리포트 항목별), `profit-status/search`(요약), `download-list/api`(종류별 엑셀). 인증=세션쿠키+x-xsrf-token(기존 inbound 인프라 재사용).
- ✅ **매칭 검증**(Jino 질문): RG 정산 비용 항목(판매수수료·입출고비·배송비·보관비·반품·반출·바코드)이 공개출처 수수료표와 1:1 일치. 오픽스 2026-06-01~07 실측 검산: 매출 1,199,900 − 판매수수료 102,950 → 지급 767,865 − 풀필먼트 206,256(입출고 75,489+배송 130,599+보관 168) = 최종 561,609 ✓.
- ✅ **reference 신규**: `docs/references/17_coupang_rg_fulfillment_fee_policy.md`(수수료 정책 전체+API+라이브 실증+매칭 결론).
- ✅ **트랙 신설**: `docs/tracks/active/track_coupang-rg-fee-accounting.md`(계획서 SoT, D-1~D-13, 구조, S1~S7). `docs/TRACKS.md` Active에 등록(0/7).
- ✅ **plan-eng-review 통과**: Step0 스코프축소 + 아키텍처 이슈 해결 + parser 흡수.
- ✅ **Codex 외부검증 통과**(원칙19): 10건 토론, #10 reconciliation-first 수용(→설계 개정), #3/#4/#5/#6/#7/#8/#9 수용(→D-9~D-13).

## 3. 확정된 결정사항 (트랙 D-1~D-13, 번복 금지)
- **D-1** 수집소스=윙 내부 API(쿠키 인증, inbound 인프라 재사용). Open API엔 RG 수수료 없음(라이브 확정).
- **D-6 reconciliation-first(Codex #10, Jino 승인)**: net_profit 바로 안 건드림. Phase1=RG 정산을 **별도 대조 뷰**로 "빠진 RG 비용"을 순이익 옆에 가시화(net_profit 불변). 규칙 잠근 뒤 Phase2에서 플립.
- **D-7**: RG 비용은 account_sum 차감 아니라 **독립 대조 지표**(account_key별). Phase1 net_profit 불변.
- **D-8**: parser 별도 SA 안 만듦 → Harness에 흡수(inbound 패턴).
- **D-9(Codex #3)**: RG **판매수수료(B)+풀필먼트(J) 둘 다** 수집(풀필먼트만 아님). 명명 "RG 정산 비용".
- **D-10(Codex #4/#5)**: basis=**매출인식일** + **발생비용(f)**(f-g/최종 아님).
- **D-11(Codex #6)**: 광고비 dedup 명시 규칙. RG정산(d) 정본, ad_costs RG분은 대조단계 표시만→플립 시 제외/대체. 키=account_key+날짜(+가능시 campaign/option).
- **D-12(Codex #8 부분수용)**: 머니코드라 fixture committed 테스트(파싱·부호·집계·dedup) — 라이브 self-verify 컨벤션의 예외.
- **D-13(Codex #7)**: Wing API 방어적 파싱+스키마 드리프트 감지. ToS/세션 리스크는 inbound서 수용한 전제.
- **D-2 입자도**: 옵션(vendor_item_id) 단위가 최종 목표(D-8 결합축). Phase2에서 엑셀로 귀속.
- **D-3/D-4/D-5**: 시스템은 사실만(전략 판단 Jino), 모델(치수→등급)은 보조(과오청구 감사), 광고비 정본=RG정산.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| docs/tracks/active/track_coupang-rg-fee-accounting.md | ★계획 SoT. D-1~13, 구조, S1~S7, GSTACK REVIEW REPORT. **먼저 읽기.** |
| docs/references/17_coupang_rg_fulfillment_fee_policy.md | RG 수수료 정책 전체 + 내부 API + 라이브 실증 + 매칭 |
| backend/app/services/coupang/intelligence.py | 종합조망 엔진. `_agg_fees`(177~)·`compute_command_center`(275~). RG 대조 지표 추가 지점 |
| backend/app/clients/coupang/inbound.py | ★재사용 패턴 — 윙 내부 API(세션쿠키+xsrf, 302=만료, 방어 파싱). S1이 본뜸 |
| backend/app/services/coupang/rg_inbound_sync.py | ★재사용 — 쿠키 CRUD·만료감지·fail-soft 인프라 |
| backend/app/models.py | CoupangWingCookie·CoupangRevenueFee(772~)·CoupangSettlementPayout(833~). 신규 CoupangRgSettlementFee 추가 |
| backend/app/utils/crypto.py | Fernet 암복호화(쿠키), .env COOKIE_ENC_KEY |
| backend/app/services/coupang/rg_size_sync.py | 치수/무게 적재(모델 A 보조검증용) |
| backend/app/services/scheduler_service.py | RG cron job 등록 지점 |

## 5. 알려진 이슈 / 주의사항
- **로컬 DB엔 RG 데이터 없음** → 검증 무조건 prod(SSH + .venv python, dotenv 수동로딩).
- **윙 쿠키 만료**: status/api 호출 시 302=만료. inbound와 동일 fail-soft(🔴).
- **API 본문 추측 금지**: status/api·download-list의 POST body는 미확정. 구현 시 윙 화면에서 실제 요청 캡처(네트워크)로 확정.
- **광고비 이중계상**: RG정산 광고비(d) vs 기존 ad_costs. Phase1 대조단계엔 표시만, net_profit 플립(Phase2)에서 D-11로 차단.
- **엑셀 비동기**: 종류별 리포트는 생성→폴링→다운로드(Phase2 S6). 엑셀에 vendor_item_id 있는지 미확정(S5에서 확인).
- **codex 호출 셸쿼팅**: 한글+특수문자 heredoc 깨짐 → 프롬프트 파일로 쓰고 `codex exec "$(cat file)" -C $REPO -s read-only`.
- 활성 트랙 2개(RG 발송관제 6/7 + RG 수수료회계 0/7). 이 세션은 후자.

## 6. 다음에 할 작업 (미완료)
- [ ] **S1**(Phase1 대조뷰 시작): `CoupangWingRgSettlementClient` SA — `status/api` 래퍼(매출인식일 기준 D-10, 방어적 파싱 D-13). inbound.py 패턴. **구현 전 `/model sonnet` 전환.**
- [ ] S2: CoupangRgSettlementFee 모델+마이그레이션(account_key×정산주기×수수료종류, 판매수수료+풀필먼트 둘 다 D-9, 음수 허용).
- [ ] S3: rg_settlement_sync Harness(수집·파싱·검산 f, fail-soft, 일일 sync) + fixture 테스트(D-12).
- [ ] S4: compute_command_center에 'RG 정산 비용(미반영)' 대조 지표(net_profit 불변) + API/프론트 + scheduler.
- [ ] Phase2(S5~S7): 규칙 잠금+엑셀 스키마 확인 → 옵션 단위 수집 → net_profit 플립+dedup 차단+모델 감사.
- [ ] 각 Sprint: self-verify(prod) + fixture 테스트 + codex review pass.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_ohisell-rg-fee-accounting-plan_20260608.md 읽고 이어서 작업해줘
