# 세션 인수인계: ohisell-rg-replenishment-ui

> 저장일시: 2026-06-08 15:18
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 직전 HANDOFF(HANDOFF_ohisell-adcost-option-automation_20260608.md)의 후속. RG 발송관제 UI 보강 4건 세션.

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: FastAPI, `backend/.venv/bin/python`, 로컬 DB `backend/ohisell.db`(RG 재고 0건 — 검증은 prod에서)
- prod: `https://sellc.ohitech.co.kr` (PM2 `ohisell-backend`, 포트 8001). SSH `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`
- 배포: git 없음 → **백엔드 scp + `pm2 reload ohisell-backend`**, **프론트 `npm run build`(frontend/) + `rsync -az --delete frontend/dist/ → /home/ubuntu/ohisell/frontend/dist/`**. nginx가 그 dist 서빙. 마이그레이션 없음(이번 세션 전부).
- 프론트 빌드 후 브라우저 캐시 주의: 검증 시 `location.reload(true)`로 새 번들 강제. 현재 prod 번들 `index-DikHsobf.js`.
- RG 발송관제 = 쿠팡 운영 페이지(`/coupang-ops`) **로켓그로스 탭 선택 시에만** 표시(테이블 위 섹션).

## 2. 이번 세션 완료 목록 (커밋 순서대로, 전부 prod 배포·라이브검증)
- ✅ **상품명 표기**(커밋 `b33cd42`): 발송관제 상품명 컬럼을 메인 테이블처럼 `상품명, 옵션명`으로. `rg_replenishment.py` build_replenishment_plan이 `seller_product_name`도 조회→`product_name` 반환(등가성계약 불변=표시필드만), `api.ts` ReplenishmentItem.product_name, `CoupangOps.tsx` 렌더(상품명 진하게+옵션명 회색, 없으면 옵션명만). codex PASS. 라이브 product_name 777/784.
- ✅ **재고 목표 7일치**(커밋 `aef26fc`, **트랙 D-9**): Jino "우리의 재고 목표를 1주일치로 잡자" → target_days 3→7. 4곳: `replenishment_calc.DEFAULT_TARGET_DAYS=7`, 엔드포인트 Query 기본7, `api.ts`/`CoupangOps.tsx` fetchReplenishmentPlan(…,7). 권장수량 ~2배↑(아이폰16 5→8). 라이브 기본 target_days=7·UI "7일치 목표".
- ✅ **컬럼명 변경**(커밋 `f07b5de`): 발송관제 `며칠치`→`판매가능일`(현재고로 며칠 더 파는지=days_to_safety). `CoupangOps.tsx` th 1곳. 라이브 헤더 확인.
- ✅ **회사 필터 버그픽스**(커밋 `772a232`, codex PASS): RG 발송관제가 회사 탭(오픽스/오하이테크) 무시하고 전계정 혼합표시 버그. `coupang_ops.py`에 `_RG_ACCOUNT_BY_COMPANY`(_CHANNEL_META Wing항목 파생, 회사→Wing셀러계정) + 엔드포인트 `company` 파라미터→account_key 변환. 프론트 fetchReplenishmentPlan(company,7)+effect deps에 company. codex P2 3건 수용(아래 §3). 라이브: 오픽스31·오하이테크753·ALL784·미지(Opix)0.
- ✅ progress 갱신(커밋 직후 `docs:`), TRACKS.md RG 5/7→6/7 동기화(b33cd42에 포함).
- ✅ Failure Memory 1건(iCloud EPERM, 아래 §5).

## 3. 확정된 결정사항
- **D-9(트랙 기록됨)**: RG 재고 목표 = **7일치**(기존 D-2 "2~3일치"·D-7③ "상한 3일" 변경). target_days 기본 7. 보관료↑/품절↓ 트레이드오프 Jino 인지. 안전재고 공식·calc 알고리즘 불변(target_days는 기존 파라미터). 원문: "우리의 재고 목표를 1주일치로 잡자".
- **RG 재고는 셀러 Wing 계정 단위 적재**(COUPANG_RG1/RG2 아님). 실측: `CoupangRgInventory.account_key` = COUPANG_WING1(오픽스 31건)·COUPANG_WING2(오하이테크 753건). 회사 필터는 이 Wing 계정으로 변환.
- **RG 발송관제는 회사 탭을 따름**(오픽스→오픽스RG만, 오하이테크→오하이테크RG만, 전체→전부). build_replenishment_plan은 단일 account_key 유지(등가성 계약 불변).
- codex P2 3건 수용 확정: ①미지 회사명 fail-closed(`__unknown__`→빈결과, 전체노출 금지) ②회사당 Wing계정 1대1 assert ③회사 빠른전환 레이스 cancelled 플래그.
- 컬럼명: 발송관제 "판매가능일"(=days_to_safety, 현재고 소진 임박일), 상품명="상품명, 옵션명".

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| backend/app/services/coupang/rg_replenishment.py | RG 발송관제 Harness. build_replenishment_plan(db, account_key, target_days). product_name+item_name 반환. |
| backend/app/services/coupang/replenishment_calc.py | calc SA. `DEFAULT_TARGET_DAYS=7`, _confidence(저신뢰 판정). |
| backend/app/routers/coupang_ops.py | `/replenishment-plan` 엔드포인트(company·target_days·account_key), `_CHANNEL_META`, `_RG_ACCOUNT_BY_COMPANY`(회사→Wing계정). |
| backend/app/services/coupang/sales_velocity_estimator.py | 일판매속도 SA(평일/주말/휴일, TRUST_START=2026-06-04). |
| backend/app/services/coupang/lead_time_estimator.py | 리드타임 SA(글로벌 폴백). |
| frontend/src/pages/CoupangOps.tsx | 쿠팡 운영 페이지. RgReplenishmentSection(로켓그로스 탭), company 탭 상태. |
| frontend/src/lib/api.ts | fetchReplenishmentPlan(company, targetDays), ReplenishmentItem 타입. |
| docs/tracks/active/track_coupang-rg-replenishment.md | RG 트랙 SoT. D-9 기록됨. 6/7(S6완료). |

## 5. 알려진 이슈 / 주의사항
- ★**iCloud Drive EPERM**(Failure Memory 기록됨): 세션 도중 프로젝트 파일 전체 "Operation not permitted" 발생 가능($HOME은 정상). 원인=macOS TCC가 Claude 앱의 iCloud Drive 접근 회수. 해결=시스템설정→개인정보보호→전체 디스크 접근 권한에서 **claude 항목(버전마다 여러 개) 전부 ON**(재시작 불필요). 진단: $HOME OK인데 iCloud 경로만 EPERM이면 이 문제.
- **저신뢰(confidence=low)**: 지금 전 항목 저신뢰. 트리거(_confidence)=①base_source≠order_item(sold_30d 기반) ②리드 global 폴백 ③요일계수 collecting. 데이터 4일치만 누적(2026-06-04 RG매출버그 수정 후)이라 그렇고, 2~3주 누적되면 자동 "정상" 승격(S7). 정상 동작이지 버그 아님.
- **로컬 DB엔 RG 재고 0건** → 발송관제 검증은 무조건 prod API/UI로. (build_replenishment_plan import는 로컬 OK)
- **codex 호출 시 셸 쿼팅**: 프롬프트에 한글+특수문자(아포스트로피) 있으면 heredoc 깨짐. → diff를 파일로 쓰고 python으로 프롬프트 파일 생성 후 `codex exec -s read-only "$(cat prompt.txt)"`로 호출(이번 세션 검증된 패턴).
- **다중 scp 주의**: `scp a b dest`(소스 2개)는 dest를 디렉터리로 취급해 실패. 파일별로 개별 scp.

## 6. 다음에 할 작업 (미완료)
- [ ] (선택, Jino 결정 대기) **발송관제 발견성**: 현재 로켓그로스 탭 전용이라 전체/오픽스 탭 기본화면에선 안 보임. 추천 A안=전체 탭에서도 항상 상단 표시+즉시발송 0건이면 자동숨김. Jino "그래"는 했으나 그 직후 다른 작업으로 전환됨 → 재확인 필요.
- [ ] (선택) "저신뢰" 라벨에 hover 툴팁(위 §5 트리거 설명) — Jino에게 제안만 함.
- [ ] (선택) 오하이테크 광고계정 광고비 수집(직전 트랙 §6, 별도 로그인).
- [ ] ★활성 트랙 = **쿠팡 RG 발송관제**(docs/tracks/active/track_coupang-rg-replenishment.md, 6/7). 다음 S7=요일/휴일 세분화(데이터 2~3주 누적 후 자동 승격, 코딩 불필요).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_ohisell-rg-replenishment-ui_20260608.md 읽고 이어서 작업해줘
