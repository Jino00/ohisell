# 트랙: Wing 세션 자동화 (Wing Session Automation)

> 생성 2026-06-14. 단일 진실 원천(Layer 1). 결정 발생 즉시 갱신.
> ⚠️ **상태: 구조 설계 스캐폴딩(코딩 전). 다음 세션에서 구조 승인 → Opus 계획 → 구현.**
> 상위 컨텍스트: 정합성 트랙(completed) 자동대조 + RG정산 자동수집이 공통으로 막힌 "Wing 세션 freshness"를 한 번에 해결.

## 1. 목표 (왜 존재하는가)
`wing.coupang.com` 내부 API를 **세션 만료 없이 자동 호출**할 수 있는 공용 인프라(헤드풀 브라우저 페처)를 만든다. 이게 풀리면 두 기능이 동시에 열린다:
- **(A) 매출 자동 대조**: `vendor-summary`(ref 18)로 쿠팡 공식 3P/RG GMV를 당겨 우리 revenue와 드리프트% 자동 감지(정합성 트랙 잔여).
- **(B) RG 정산 자동 수집**: RG 수수료 회계 트랙 S6-auto(현재 수동 cURL 의존)를 자동화.

## 2. 핵심 문제 (왜 어려운가)
- `wing.coupang.com`은 **Cloudflare(`cf_clearance`, IP+UA 바인딩) + Akamai(`_abck`,`bm_*`) + AWS ALB** 다중 봇 방어. 쿠키 단명.
- `requests`/curl 쿠키 재생은 **1회용**(cf_clearance 갱신 불가) → 실제 브라우저가 주기적으로 챌린지를 풀어 세션을 살려둬야 함.
- **이미 검증된 해법**: 광고 페처 `tools/ad_cost_browser_fetcher.py`가 advertising.coupang.com에서 정확히 이 패턴(headful Playwright + storage_state + keycloak SSO 재발급 + launchd poll 데몬 + 버튼 트리거)으로 해결 중. **Wing판으로 복제**가 출발점.

## 3. 확정 결정사항 (번복 금지)
- **D-1**: 광고 페처의 headful Playwright + launchd poll 데몬 패턴을 재사용(재발명 금지). 모바일 UA 필수(ref 18).
- **D-2**: 시스템은 사실·지표·드리프트만 정리(전략 추천 금지) — 종합조망 불변 원칙 계승([[no-ad-strategy-recommendations]]).
- **D-3**: 자동대조는 **닫힌 과거일** 기준으로만 비교(당일은 sync 시차로 부정확, ref 18 실측).
- **D-4 (런타임 경계, 2026-06-14 S0 확정)**: Wing 페처는 광고 페처처럼 **Mac 로컬 헤드풀 상주 프로세스**(`tools/`, residential IP). 백엔드 Harness는 이걸 함수로 직접 호출하지 않고 **push된 데이터를 ingest해서 읽기만** 한다. 도표는 두 런타임(Mac 페처 ↔ 백엔드 ingest/reconcile)을 명시적으로 분리한다.
- **D-5 (vendor_summary 위치 + 별도 데몬, 2026-06-14 S0 확정)**: cf_clearance는 재생 불가이므로 `vendor-summary`/RG XLSX는 **백엔드 `requests` client가 아니라 살아있는 브라우저 세션 안에서 fetch/download**(광고비 `report/SALES` 패턴) → prod push. 백엔드는 ingest+store SA로 수신. **Wing 페처는 별도 launchd 데몬 `com.ohisell.wing`**(별도 storage_state) — 광고(advertising.coupang.com·aid/keycloak)와 Wing(wing.coupang.com·cf_clearance)은 도메인·세션·방어체계가 달라 한 프로세스에 묶으면 취약 세션끼리 상호 파손. RG는 기존 업로드 ingest를 재사용(다운로드만 Mac 페처가 대행, 백엔드 변경 최소).

### 사용자 원문 인용 (왜곡 방지)
- "C로 하면 안되는 이유는 뭐야?" → C는 기술 장벽 없음, 단 스프린트 규모·별도 트랙 감이라 정합성 트랙과 분리.
- "너의 제안대로 가자" → 정합성 트랙 B 마감 + 본 트랙 스캐폴딩 승인.
- "그래" (2026-06-14) → S0 구조 검토안(런타임 경계 명시 D-4 + vendor_summary 브라우저측 fetch·별도 데몬 D-5) 승인.

## 4. 확정 구조 (2026-06-14 S0 승인, 레고 계층 — 런타임 2개 분리)
```
┌─ [Mac 로컬·헤드풀·residential IP]  tools/wing_browser_fetcher.py  (광고페처 복제)
│   launchd: com.ohisell.wing  (별도 데몬·별도 storage_state, D-5)
│   SA: WingBrowserFetcher — cf_clearance 세션 유지 + SSO 재발급(login/run/poll 골격)
│     ├─ 브라우저측 fetch: vendor-summary (ref 18, 모바일 UA)   → prod push
│     └─ 브라우저측 download: RG 정산 XLSX                      → prod push(기존 ingest)
│   (기존 재사용: parse_curl_cookies[inbound.py], coupang_wing_cookie store[Fernet] — 최초 로그인/쿠키 부트스트랩)
│
└─ [백엔드 prod]  backend/app/...
    Harness: revenue_reconcile (매출 대조)
      ├ SA: vendor_summary ingest+store  ← 신규(push 수신·스냅샷 저장)
      ├ SA: compute_command_center (기존, revenue_3p/rg)
      └ → 닫힌일 드리프트% 산출(D-3) → 검산패널 노출
    Harness: rg_settlement_auto = 기존 업로드 ingest 재사용(S6-auto, 백엔드 변경 최소)
```
- 데이터 흐름: WingBrowserFetcher가 세션 유지 → 브라우저측에서 vendor-summary fetch / RG XLSX download → **prod push** → 백엔드 ingest 저장 → revenue_reconcile이 우리 값과 비교 → 검산 패널에 "쿠팡 공식 GMV + 드리프트%" 노출.
- **계층 배치 주의(D-4/D-5)**: vendor_summary는 백엔드 `requests` client가 아님(cf_clearance 재생 불가). 호출은 Mac 페처 브라우저측, 백엔드는 ingest 수신만.

## 5. 체크리스트 (1/6)
- [x] **S0 구조 승인(Jino) + Opus 계획서** — 구조 확정(D-4/D-5), 계획서 `docs/PLAN_wing-session-automation.md`
- [ ] S1 WingBrowserFetcher(헤드풀 세션 유지) — 광고 페처 복제·wing.coupang.com SSO/cf_clearance 흐름 라이브 확인
- [ ] S2 vendor_summary ingest+store SA + revenue_reconcile Harness(닫힌일 기준 드리프트%)
- [ ] S3 검산 패널 UI에 "쿠팡 공식 GMV + 드리프트%" 컬럼
- [ ] S4 RG정산 자동수집(S6-auto) 흡수
- [ ] 각 Sprint: codex 교차검증 + prod 라이브 self-verify(원칙22)

## 6. 현재 진행 단계
- 2026-06-14 S0: **구조 확정·승인 완료**(D-4 런타임 경계, D-5 vendor_summary 브라우저측 fetch·별도 데몬). Opus 계획서 작성 단계. **코딩 미착수.** 데이터 경로는 vendor-summary 프로브로 검증됨(ref 18).

## 7. 다음 액션
- S1 착수: `tools/wing_browser_fetcher.py` 스캐폴드(광고 페처 복제) → wing.coupang.com 헤드풀 로그인 1회 → vendor-summary 브라우저측 fetch 라이브 확인 → SSO/cf_clearance 갱신 흐름 실측(추정 금지).
- 참고: ref 18(vendor-summary), `tools/ad_cost_browser_fetcher.py`(헤드풀 패턴 원본), `backend/app/clients/coupang/rg_settlement.py`(Wing 호출), `coupang_wing_cookie`(인증 저장), `backend/app/clients/coupang/inbound.py`(parse_curl_cookies).
