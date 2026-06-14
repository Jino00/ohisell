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

### 사용자 원문 인용 (왜곡 방지)
- "C로 하면 안되는 이유는 뭐야?" → C는 기술 장벽 없음, 단 스프린트 규모·별도 트랙 감이라 정합성 트랙과 분리.
- "너의 제안대로 가자" → 정합성 트랙 B 마감 + 본 트랙 스캐폴딩 승인.

## 4. 제안 구조 (초안 — 승인 대기, 레고 계층)
```
Agent: Wing 자동 수집 (메뉴: 종합조망 검산패널 자동대조 + RG정산 자동)
  └ Harness: wing_session (세션 유지 허브)
       ├ SA: WingBrowserFetcher (headful Playwright, cf_clearance/세션 유지·SSO 재발급)  ← 신규, 광고페처 복제
       ├ SA: parse_curl_cookies (기존 inbound.py 재사용)
       └ SA: coupang_wing_cookie store (기존, Fernet)
  └ Harness: revenue_reconcile (매출 대조)
       ├ SA: vendor_summary client (POST vendor-summary, ref 18)  ← 신규
       └ SA: compute_command_center (기존, revenue_3p/rg 제공)
  └ Harness: rg_settlement_auto (기존 rg_settlement.py + 자동 다운로드)  ← S6-auto 흡수
```
- 데이터 흐름: WingBrowserFetcher가 세션 유지 → vendor_summary/rg_settlement이 그 세션으로 호출 → revenue_reconcile이 우리 값과 비교 → 검산 패널에 "쿠팡 공식 + 드리프트%" 노출.

## 5. 체크리스트 (0/N — 미착수)
- [ ] S0 구조 승인(Jino) + Opus 계획서
- [ ] S1 WingBrowserFetcher(헤드풀 세션 유지) — 광고 페처 복제·wing.coupang.com SSO 흐름 라이브 확인
- [ ] S2 vendor_summary SA + 매출 대조 Harness(닫힌일 기준 드리프트%)
- [ ] S3 검산 패널 UI에 "쿠팡 공식 GMV + 드리프트%" 컬럼
- [ ] S4 RG정산 자동수집(S6-auto) 흡수
- [ ] 각 Sprint: codex 교차검증 + prod 라이브 self-verify(원칙22)

## 6. 현재 진행 단계
- 2026-06-14: 스캐폴딩만 생성(구조 초안). **코딩 미착수.** vendor-summary 프로브로 데이터 경로는 검증됨(ref 18).

## 7. 다음 액션
- 다음 세션: 본 트랙 구조(§4) 검토·확정 → "이 구조로 진행할까요?" 승인 → /model opus 계획서 → S1 착수.
- 참고: ref 18(vendor-summary), `tools/ad_cost_browser_fetcher.py`(헤드풀 패턴), `backend/app/clients/coupang/rg_settlement.py`(Wing 호출), `coupang_wing_cookie`(인증 저장).
