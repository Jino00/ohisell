"""킬스위치 2종의 «코드 기본값» 단일 보관소 (D-NAO-281 · 계약 P2-ⓑ).

══ 왜 이 모듈이 따로 있는가 ══
`guardrail_params.SPECS`의 관례는 **default가 코드 상수를 «참조»한다**는 것이다(복사 금지 —
상수가 바뀌면 SPECS도 따라오게). 그런데 이 두 스위치의 원래 자리는 각각
`auto_operator`(`AD_BID_ROUTING_ENABLED`)와 `scheduler_service`(`os.getenv`)인데,
**`auto_operator`가 이미 `guardrail_params`를 import 한다**(auto_operator.py:37).
그래서 `guardrail_params`가 거꾸로 `auto_operator`를 import 하면 순환이다.

⇒ 두 모듈 아래에 **잎 모듈**을 하나 두고 둘 다 여기를 본다. 이 파일은 아무것도 import 하지
않는다 — 그게 이 모듈의 계약이다(무엇이든 import 하는 순간 순환이 되돌아온다).

══ 여기 있는 것은 «기본값»이지 «실효값»이 아니다 ══
실효값은 `guardrail_params.get_params(db)`가 정한다(DB > env > 여기). **실행 경로가 이 상수를
직접 읽으면 안 된다** — 그러면 「사람이 화면에서 끈 스위치」와 「엔진이 실제로 보는 값」이
갈라진다. 이 저장소가 이미 값을 치른 병이다(D-NAO-265: 승인 카드의 판정창 ≠ 실행 재검증창 —
모듈 상수를 직접 읽던 harness가 원인이었다).
"""

from __future__ import annotations

# ── 소재(ad) 레벨 입찰 라우팅 킬스위치 ─────────────────────────────────────────
# False로 내리면 소재-레벨 제안 «생성»이 종전 카나리 집합
# (`auto_operator.AD_BID_ROUTING_FALLBACK_CAMPAIGNS`)으로 되돌아가고, 자동 실행
# (`_ad_auto_exec`)도 함께 죽는다. 두 게이트가 같은 스위치를 봐야 «되돌리는 스위치가 완전히
# 되돌린다»(auto_operator._ad_auto_exec 독스트링 참조).
# ★OFF는 「아무것도 안 한다」가 아니라 **「카나리 allowlist로 복귀한다」**이다 — 그 집합이
#   비어 있지 않으면 OFF 상태에서도 그 캠페인에는 제안이 계속 생성된다.
AD_BID_ROUTING_ENABLED_DEFAULT: bool = True

# ── 콜드스타트(첫 입찰) 레인 dry-run ──────────────────────────────────────────
# True = 관측만(네이버 쓰기 0). 종전엔 `os.getenv("NAVER_CS_DRY_RUN", "1") != "0"` 한 줄이
# 유일한 판정이었고, 바꾸려면 .env 수정 + 재시작이 필요했다.
# ★env를 폐기하지 않는다 — prod `.env`에 `NAVER_CS_DRY_RUN=0`이 **실재한다**(2026-08-31 실측).
#   기본값만 SPECS로 옮기고 env를 무시하면 그 순간 prod가 dry-run으로 조용히 뒤집힌다.
#   그래서 우선순위는 **DB > env > 이 상수**이고, `describe()`가 셋 중 어디서 왔는지를 화면에
#   그대로 말한다(「기록됐다 ≠ 코드가 읽는다」의 스위치판).
NAVER_CS_DRY_RUN_DEFAULT: bool = True
NAVER_CS_DRY_RUN_ENV: str = "NAVER_CS_DRY_RUN"
