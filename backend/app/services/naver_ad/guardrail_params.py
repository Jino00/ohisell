# guardrail_params.py — 안전 봉투 파라미터의 «근거 있는 기준» 층 (D-NAO-172, P1)
#
# ══ 왜 만드는가 ══
# 봉투(±15%·쿨다운 2h·자동하향 3회/일·누적 2.0×)가 파이썬 상수로 박혀 있어, 바꾸려면 배포가
# 필요하고 «지금 무슨 값으로 돌고 있는지» 화면 어디에도 안 보였다. Jino 지시(2026-08-10):
#   *"안전봉투도 절대규칙이 아니라 조절할 수 있는 기준으로 만들자 … 기준은 정하데 기준도
#     상황에 따라서 바뀔 수 있는거지"*
#
# ══ 착수 근거가 된 실측 (2026-07-16~30, 차단 3,863건) ══
# 봉투가 실제로 막은 건 **86건 = 2.2%**뿐이다(쿨다운 36 · 일일상한 50). 1위는 소급채점 stale
# 2,138건(55%)이었다. 즉 **봉투는 병목이 아니었고, 푸는 것의 기대 이득은 작다.** 그래서 이 층은
# 「풀기 위한 장치」가 아니라 **①지금 값이 무엇인지 보이게 하고 ②조이는 쪽을 상황에 맞게
# 움직일 수 있게** 하는 것이 목적이다(풀기는 사람 승인 경로로만 — P3).
#
# ══ 3층 구조와 fail 방향 ══
#   코드 상수(최후 폴백)  ←  DB KV(`naver_account_settings.guardrail_params`)  ←  상황 조정(P2·P3)
# - KV 없음/파싱 실패/타입 불일치/범위 밖 → **그 항목만 코드 상수로 폴백**(fail-to-current).
#   fail-closed(0으로)도 fail-open(무제한)도 아니다 — 「모르면 지금까지 하던 대로」가
#   이 층의 유일하게 안전한 기본값이다. 봉투가 0이면 광고가 멈추고, 무제한이면 돈이 샌다.
# - `_PARAMS_FROM_DB = False` 한 줄로 전부 코드 상수로 원복(사고 시 되돌림 스위치).
#
# ══ 범위(min/max)가 이 파일의 핵심이다 ══
# DB에 값을 넣을 수 있다는 것은 **누군가 잘못 넣을 수 있다**는 뜻이다. 그래서 각 파라미터에
# 「아무리 조절해도 여기까지」를 박아 둔다. 이 범위는 배포로만 바뀐다 — DB가 자기 상한을
# 넓힐 수 없다는 것이 되먹임 차단의 마지막 층이다(첫 층은 「풀기는 사람 승인」).
from __future__ import annotations

import json
import logging
import os
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import NaverAccountSettings, NaverChangeLog
from app.services.naver_ad import guardrail_gate, runtime_switches, search_term_judge
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

SETTINGS_KEY = "guardrail_params"

# ★D-NAO-248 §4-B(B7-5) — param_change NaverProposal이 SPECS 키를 **구조적으로** 실어 나르는
# target_type 값. rationale 자유텍스트 파싱을 승인 핸들러에 강제하지 않기 위해 기존 컬럼
# (target_type/target_id)에 담는다 — target_id에 SPECS 키 문자열을 그대로 쓴다.
# ★grep 확인(2026-08-25): 기존 target_type 값(campaign/adgroup/keyword/ad/search_term/account)
# 어디에도 "guardrail_param"은 없다 — 새 값이 기존 분기와 충돌하지 않는다.
# ★길이: "guardrail_param"=15자(NaverProposal.target_type String(20) 안), SPECS 키 중 최장
# "max_daily_auto_bid_downs"=25자(target_id String(50) 안).
TARGET_TYPE = "guardrail_param"

# ★되돌림 스위치 — False면 DB를 아예 읽지 않는다(D-NAO-172).
#   `_GROUP_STEP_ALL_ADS`와 같은 관례: 사고 났을 때 한 줄로 원복된다는 믿음이 이런 스위치의
#   존재 이유다.
# ★★**끄는 것은 «DB 층»뿐이다 — 「전부 코드 상수」가 아니다**(D-NAO-281 적대 리뷰 P1-1).
#   `env` 폴백을 가진 키(`naver_cs_dry_run`)는 이 스위치를 내려도 **여전히 환경변수 값으로
#   돈다.** 그게 옳다 — 이 레버가 끄려는 것은 D-NAO-172가 «새로 들인» DB 층이고, env는 그
#   이전부터 그 키의 정본이었다(끄는 순간 CS 레인 동작이 바뀌면 그건 원복이 아니라 새 사고다).
#   위험한 것은 동작이 아니라 **문구**였다: 화면·API가 「전부 코드 기본값」이라고 약속하면
#   사고 중에 레버를 내린 사람이 「= dry-run = 안전」으로 읽는데, prod `.env`엔
#   `NAVER_CS_DRY_RUN=0`이 실재한다. 그래서 문구를 사실로 고쳤고(라우터 from_db_help·콘솔
#   배너) `describe()`의 `source` 칸이 항목별 진실을 말한다.
_PARAMS_FROM_DB: bool = True


class ParamSpec:
    """파라미터 1개의 «근거». 값 자체보다 이 메타가 이 설계의 산출물이다.

    Attributes:
        key: KV JSON의 키.
        default: 코드 상수(최후 폴백). guardrail_gate·runtime_switches에서 가져온다 — 두 곳에
            값을 적지 않는다.
        kind: 'decimal' | 'int' | 'bool'. ★'bool'은 D-NAO-281(계약 P2-ⓑ)이 킬스위치 2종을
            런타임화하며 추가했다. 저장은 종전과 같은 문자열(`str(True)`=='True')이고 화면·API
            로는 1.0/0.0으로 나간다 — **수치 계약은 불변**이고, 프론트가 `kind`를 보고 토글로
            그린다.
        lo/hi: **배포로만 바뀌는** 허용 범위. DB는 이 밖으로 못 나간다. bool은 (False, True).
        label: 화면 표기.
        why: 이 값이 왜 이 값인가(현황판에 그대로 노출 — 「근거를 만들자」는 지시의 이행부).
        direction: 'tighten_down' = 값이 **작아지면** 조이는 것 / 'tighten_up' = 커지면 조이는 것.
            풀기/조이기 판정에 쓴다(P2·P3). 사람이 헷갈리는 축이라 데이터로 박아 둔다.
        env: 이 파라미터의 **환경변수 폴백** 이름(없으면 None). 우선순위 **DB > env > default**.
            ★env 층을 남긴 이유: prod `.env`에 `NAVER_CS_DRY_RUN=0`이 **실재한다**(2026-08-31
            실측). 기본값만 SPECS로 옮기고 env를 무시하면 그 순간 prod가 dry-run으로 조용히
            뒤집힌다 — 「값을 옮기는 것」이 「동작을 바꾸는 것」이 되는 자리다.
        warn: **접지 않고 항상 보이는** 경고 1줄(없으면 None). `why`는 「근거 보기」 안에 접혀
            있어서, 「이 스위치를 내리면 무슨 일이 벌어지는가」처럼 **끄기 직전에 봐야 하는
            사실**을 담기엔 자리가 틀렸다 — 접힌 곳에 적은 사실은 없는 사실과 같다.
        llm_proposable: 지혜 승격 판사(LLM)가 이 키의 변경을 «제안»할 수 있는가. 기본 True.
            ★D-NAO-281 적대 리뷰가 잡은 자리: `wisdom_judge`·`wisdom_apply`가 화이트리스트를
            `SPECS` **전건**으로 만들었기 때문에, SPECS에 키를 등재하는 것만으로 **엔진이 자기
            킬스위치 해제를 제안하는 카드**가 콘솔에 뜰 수 있게 됐다. 자동 발사는 없지만
            (사람이 값을 직접 입력해 승인), 계약 §5 「지출 백스톱·킬스위치 약화 금지」가 보는
            방향과 반대라 **킬스위치는 판사 목록에서 뺀다.** 봉투는 종전대로 제안 가능하다 —
            이 축이 없었다면 「등재 = 제안 가능」이 영원히 암묵 규칙으로 남았다.
    """

    def __init__(self, key: str, default: Any, kind: str, lo: Any, hi: Any,
                 label: str, why: str, direction: str,
                 env: str | None = None, warn: str | None = None,
                 llm_proposable: bool = True) -> None:
        self.key, self.default, self.kind = key, default, kind
        self.lo, self.hi = lo, hi
        self.label, self.why, self.direction = label, why, direction
        self.env, self.warn = env, warn
        self.llm_proposable = llm_proposable


# ★default는 guardrail_gate 상수를 **참조**한다(복사 금지) — 상수가 바뀌면 여기도 따라온다.
SPECS: dict[str, ParamSpec] = {
    # ★`max_change_pct`(±15% 스텝)는 **일부러 뺐다** — 적대 리뷰 P1-1(2026-08-10).
    #   ±15%는 게이트 전용이 아니라 스텝 «생성기» 7곳이 자기들끼리 재현한다:
    #   `proposal_writer._step_down_bid`(:175) · `_ad_step_bid`(:188) · bid_up step_cap(:309) ·
    #   `proposal_pipeline._build_expansion_bid_up` · `auto_operator._fire_vitality_revive` ·
    #   `_check_bid_up_conditions`(:612) — 전부 `_MAX_CHANGE_PCT`를 직접 import한다.
    #   `_clamp_step` 한 곳만 파라미터를 보게 하고 DB로 값을 내리면 나머지 생성기의 제안이
    #   게이트에서 전건 「변경폭 초과」로 죽고, 그것도 **`failed` 영구 종결**(재승인만 재시도)이라
    #   다음 회차에 저절로 회복되지 않는다. 하필 `_step_down_bid`가 **손실 하향** 산식이라
    #   **조이려고 값을 내리면 조이는 레버가 가장 먼저 죽는다.**
    #   ★아래 셋만 남긴 이유가 이것이다 — 전부 **게이트 전용**이라 생성기 중복이 없다.
    #   되살리려면 **생성기 전수를 먼저 배선**하고 정합 테스트를 `_clamp_step` 하나가 아니라
    #   생성기 전수로 확장할 것(P2가 스텝을 건드리므로 그때가 그 자리다).
    "cooldown_hours": ParamSpec(
        "cooldown_hours", guardrail_gate._COOLDOWN_HOURS, "int", 1, 24,
        "같은 유닛 쿨다운",
        "2시간(D-NAO-19). 진동 방어 담당 — 조이기 등급이 올라가도 이건 유지한다. "
        "2026-07 실측 36건만 물어 병목이 아니었다.",
        "tighten_up",
    ),
    "max_daily_auto_bid_downs": ParamSpec(
        "max_daily_auto_bid_downs", guardrail_gate._MAX_DAILY_AUTO_BID_DOWNS, "int", 1, 8,
        "자동 하향 일일 상한",
        "3회/일 = 하루 최대 −39%(0.85³). ★손실이 확인된 상품도 이 속도로만 내려가 «둔한» "
        "지점이다 — P2의 조이기 자동화가 여기를 등급별로 올린다. 상한 8회면 하루 −73%.",
        "tighten_up",
    ),
    "max_auto_up_multiple": ParamSpec(
        "max_auto_up_multiple", guardrail_gate._MAX_AUTO_UP_MULTIPLE, "decimal",
        Decimal("1.0"), Decimal("3.0"),
        "자동 상향 누적 상한",
        "사람/대행사가 정한 기준가의 2.0배. codex 적대 3R이 «기준점이 옛 고가에 머물러 "
        "자동화가 되돌려 올림» 구멍을 잡은 자리이고, 2026-08-10 적대 리뷰 P1도 정확히 이 "
        "브레이크를 무력화하는 결함이었다. BEP 하한은 부모 그룹 30일 집계라 개별 소재를 "
        "못 막으므로 **대체 브레이크가 없다.**",
        "tighten_down",
    ),
    # ══ D-NAO-262 (S4) — 파워링크 제외 게이트 2종 승격 ══
    # 계약 `CONTRACT_ignition_readiness.md` §4-B⑤ 봉투 표 그대로. 끝값은 전부 기존 숫자의
    # 재사용이고 이 세션이 발명한 수는 없다.
    #
    # ★같은 표의 SS 게이트 2종은 D-NAO-262에서 **분산 탓 승격 보류**였고, D-NAO-265(S4-a
    #   잔여)가 그 분산을 배선으로 없앤 뒤 승격했다. 보류 사유와 그 해소를 같이 남긴다 —
    #   「왜 없었나」를 지우면 다음 세션이 같은 자리를 또 판다:
    #   · `_SS_WINDOW_DAYS` — `naver_execution_harness`가 모듈 상수를 **직접 읽어**(GATE ⑥ 실행
    #     재검증) 판정 창과 갈릴 수 있었다. ⇒ 그 자리를 `search_term_judge._ss_params(db)`로
    #     바꿔 **판정과 실행 재검증이 같은 출처**를 보게 했다(재사용의 원래 목적이 «신선도 갭
    #     메우기»였으므로, 같은 DB값을 보는 것이 그 목적의 유일한 보존 방법이다).
    #   · `_SS_MIN_CLICK` — `search_term_exclusion_list`에 **복제 리터럴 `MIN_CLICK = 10`**이
    #     있어 카드에서 내려도 하류가 안 따라왔다(승인 카드가 거짓말). ⇒ 리터럴을 지우고 그
    #     모듈도 같은 `_ss_params(db)`를 읽게 했다.
    #   ★가드: `tests/test_pl_gate_specs_promotion.py`의 소비처 전수 인구조사 —
    #     보류를 재던 `test_분산이_남은_SS_게이트_2종은_아직_SPECS에_없다`는 **「배선됐음」을 재는
    #     것으로 바꿨다**(그 테스트 docstring이 지시한 그대로). 검사를 지운 것이 아니다.
    "ss_min_click": ParamSpec(
        "ss_min_click", search_term_judge._SS_MIN_CLICK, "int", 5, 21,
        "쇼핑 제외 최소 클릭",
        "10클릭(§1 2, D-NAO-70 핫셋 게이트와 동일 값). 하한 5는 파워링크 게이트 현행값 재사용, "
        "상한 21은 산업 표준 통계 컷. ★**작아지면 조인다** — 더 적은 클릭으로도 제외 후보가 "
        "되어 브레이크가 세진다. 이 값은 개별 grain 판정과 의미단위 풀링 판정, 그리고 제외 후보 "
        "리스트 API가 **같이** 쓴다(D-NAO-265로 단일 출처화 — 복제 리터럴 제거).",
        "tighten_down",
    ),
    "ss_window_days": ParamSpec(
        "ss_window_days", search_term_judge._SS_WINDOW_DAYS, "int", 7, 16,
        "쇼핑 제외 판정 창",
        "14일(§난제 2 — 저볼륨 롱테일 표본 누적). 하한 7은 민감도 관례 축(ref 66 §8-5), "
        "상한 16은 계약 §4-B⑤가 「원본 보존 상한」으로 적은 값이다. ★그 전제는 2026-08-27에 "
        "정정됐다 — 16일은 우리 DB 보존이 아니라 **네이버 리포트 보관 기한**(ref 21 §10)이고 "
        "prod는 이미 shopping 53일을 갖고 있다. 계약이 「보존 연장 배포 후 hi를 28로 개정 «허용»」"
        "이라 적었으나 **이 세션은 16을 그대로 쓴다**: 봉투 확대는 브레이크를 넓히는 방향이라 "
        "액셀 짝 없이 혼자 움직이면 §7 대칭 검사에 걸린다(D-NAO-85형 표류). 28로 여는 것은 "
        "근거가 아니라 **결정**이므로 Jino 발의로 남긴다. ★**커지면 조인다** — 창이 길수록 "
        "클릭·비용이 누적돼 게이트를 더 잘 넘는다.",
        "tighten_up",
    ),
    "pl_min_click": ParamSpec(
        "pl_min_click", search_term_judge._PL_MIN_CLICK, "int", 5, 10,
        "파워링크 제외 최소 클릭",
        "5클릭(§1 2 — 쇼핑 10과 분리). 하한 5는 현행 유지가 근거다: 대행사 컷 재판정에서 "
        "D구간(1–9클릭) 264건/579,991원이 «표본 미달이라 판정 근거가 없다»의 실증이었다. "
        "상한 10은 쇼핑 게이트값 재사용. ★**작아지면 조인다** — 더 적은 클릭으로도 제외 "
        "후보가 되어 브레이크가 세진다.",
        "tighten_down",
    ),
    "pl_window_days": ParamSpec(
        "pl_window_days", search_term_judge._PL_WINDOW_DAYS, "int", 14, 90,
        "파워링크 제외 판정 창",
        "30일(§1 1 — 실측: 14일 창에선 최대 clk=5라 표본이 안 서서 30일로 넓힌 값). "
        "하한 14는 쇼핑 창, 상한 90은 재심사 백오프 상한 재사용. 원본이 창을 막지 않는다 "
        "(2026-08-27 실측: expkeyword 2,618,269행·373일 보존). ★**커지면 조인다** — 창이 "
        "길수록 클릭·비용이 누적돼 게이트를 더 잘 넘는다. 다만 순손실 프록시(④)도 같은 창으로 "
        "다시 재므로 방향이 단조롭다고 단정하지 않는다.",
        "tighten_up",
    ),
    # ── 킬스위치 2종 (D-NAO-281 · 계약 P2-ⓑ) ──────────────────────────────────
    # ★이 둘은 «봉투»가 아니라 «스위치»다. 같은 레지스트리에 넣는 이유는 하나 —
    #   「지금 무슨 값으로 돌고 있고 그게 어디서 왔나」를 한 화면에서 보게 하는 것이
    #   이 레지스트리의 존재 이유이고, 배포로만 바뀌던 스위치야말로 그게 안 보였다.
    "ad_bid_routing_enabled": ParamSpec(
        "ad_bid_routing_enabled", runtime_switches.AD_BID_ROUTING_ENABLED_DEFAULT,
        "bool", False, True,
        "소재(ad) 입찰 라우팅",
        "True(D-NAO-125). 소재-레벨 제안 «생성»과 «자동 실행»을 함께 여는 스위치다 — 두 게이트가 "
        "같은 값을 봐야 「되돌리는 스위치가 완전히 되돌린다」(종전엔 배포로만 바뀌어, 사고가 나도 "
        "한 줄 원복에 배포 한 번이 필요했다). ★**끄는 것이 조이는 쪽**이라 direction은 "
        "tighten_down이다. ★단 OFF는 「전면 정지」가 아니다 — 아래 경고 참조.",
        "tighten_down",
        warn=(
            "OFF = 전면 정지가 아니라 **카나리 allowlist로 복귀**입니다"
            "(AD_BID_ROUTING_FALLBACK_CAMPAIGNS에 남은 캠페인에는 소재 제안이 계속 생성됩니다). "
            "현재 그 집합은 2026-07-30에 optimizer='none'으로 꺼진 캠페인 1개를 가리킵니다."
        ),
        llm_proposable=False,  # 킬스위치는 판사가 제안하지 않는다(계약 §5)
    ),
    "naver_cs_dry_run": ParamSpec(
        "naver_cs_dry_run", runtime_switches.NAVER_CS_DRY_RUN_DEFAULT,
        "bool", False, True,
        "콜드스타트 레인 dry-run",
        "코드 기본값 True(관측만·네이버 쓰기 0). 종전엔 `os.getenv(\"NAVER_CS_DRY_RUN\")` 한 "
        "줄이 유일한 판정이라 바꾸려면 .env 수정 + **재시작**이 필요했고, 무엇보다 «지금 켜져 "
        "있는지»가 화면 어디에도 안 보였다. ★**켜는 것(dry-run=True)이 조이는 쪽**이라 "
        "direction은 tighten_up이다. ★env는 폐기하지 않았다 — 우선순위 DB > env > 코드 상수. "
        "출처 칸이 셋 중 어디서 온 값인지 그대로 말한다.",
        "tighten_up",
        env=runtime_switches.NAVER_CS_DRY_RUN_ENV,
        warn=(
            "OFF(dry-run 해제)면 콜드스타트 레인이 **네이버에 실제로 입찰을 씁니다.** "
            "prod .env에 NAVER_CS_DRY_RUN=0이 실재하므로, DB에 값을 넣지 않으면 현재값은 "
            "env가 정합니다(출처 칸 확인)."
        ),
        llm_proposable=False,  # 킬스위치는 판사가 제안하지 않는다(계약 §5)
    ),
}


# ★bool의 «받아들이는 모양»을 한 곳에 모은다 — 세 경로가 서로 다른 모양을 보내기 때문이다:
#   ①프론트 입력칸 → JSON 숫자 1/0  ②JSON 리터럴 true/false  ③**저장 왕복** → 문자열
#   'True'/'False'(`apply_params`가 `str(val)`로 저장한다 — 종전 int·decimal과 같은 관례).
#   ③을 빠뜨리면 «저장은 되는데 다음 읽기에서 코드 상수로 폴백»하는 조용한 실패가 된다
#   (화면엔 저장 성공으로 뜨고 값만 안 바뀐다 — 이 저장소가 반복해 데인 모양).
_BOOL_TRUE = {"true", "1"}
_BOOL_FALSE = {"false", "0"}


def _coerce_bool(raw: Any) -> bool | None:
    """bool 파라미터의 원값 → True/False. 모르는 모양이면 None(호출부가 폴백)."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):  # bool은 위에서 이미 걸렀다
        return True if raw == 1 else (False if raw == 0 else None)
    if isinstance(raw, float):
        return True if raw == 1.0 else (False if raw == 0.0 else None)
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in _BOOL_TRUE:
            return True
        if s in _BOOL_FALSE:
            return False
    return None


def _coerce(spec: ParamSpec, raw: Any) -> Any | None:
    """DB 원값 → 타입·범위 검증. 실패하면 None(호출부가 코드 상수로 폴백)."""
    try:
        if spec.kind == "bool":
            val: Any = _coerce_bool(raw)
            if val is None:
                log.error("guardrail_params: %s=%r는 bool로 못 읽는다 — 폴백", spec.key, raw)
                return None
        elif spec.kind == "decimal":
            val = Decimal(str(raw))
        else:
            if isinstance(raw, bool):  # bool은 int의 하위형이라 먼저 걸러낸다
                return None
            val = int(raw)
    except (TypeError, ValueError, ArithmeticError, InvalidOperation):
        return None
    # NaN은 비교 자체가 InvalidOperation을 던진다 — 범위 검사보다 먼저 걸러낸다(적대 리뷰 P2-1).
    # `get_params`는 harness 핫패스에 있고 「설정 한 줄이 광고 집행 경로를 죽이면 안 된다」가
    # 이 층의 계약이다. UI 가드로 도달 불가여도 계약은 도달 가능성과 무관하게 성립해야 한다.
    if spec.kind == "decimal" and not val.is_finite():
        log.error("guardrail_params: %s=%r는 유한한 수가 아니다 — 코드 상수로 폴백", spec.key, raw)
        return None
    if val < spec.lo or val > spec.hi:
        log.error(
            "guardrail_params: %s=%s는 허용 범위 [%s, %s] 밖 — 코드 상수 %s로 폴백"
            "(범위는 배포로만 바뀐다: DB가 자기 상한을 넓힐 수 없다)",
            spec.key, val, spec.lo, spec.hi, spec.default,
        )
        return None
    return val


def _raw_overrides(db: Session) -> tuple[dict, Any]:
    """KV 원본 dict와 updated_at. 없거나 깨졌으면 ({}, None) — 조용히 넘어가지 않고 로그."""
    if not _PARAMS_FROM_DB:
        return {}, None
    row = db.query(NaverAccountSettings).filter(
        NaverAccountSettings.key == SETTINGS_KEY).first()
    if row is None or not row.value_json:
        return {}, None
    try:
        parsed = json.loads(row.value_json)
        if not isinstance(parsed, dict):
            raise ValueError(f"{SETTINGS_KEY}는 객체여야 하는데 {type(parsed).__name__}")
        return parsed, row.updated_at
    except (TypeError, ValueError) as exc:
        log.error("guardrail_params: %s 파싱 실패 — 전부 코드 상수로 폴백(%s: %s)",
                  SETTINGS_KEY, type(exc).__name__, exc)
        return {}, None


def _resolve(spec: ParamSpec, overrides: dict) -> tuple[Any, str, bool, bool]:
    """한 파라미터의 (실효값, 출처, db_rejected, env_rejected). **단일 출처**.

    ★`get_params`(실행 경로)와 `describe`(화면)가 **이 함수 하나**를 본다. 종전엔 두 함수가
    같은 폴백 규칙을 각자 재현했고, env 층이 붙으면서 규칙이 셋으로 늘었다 — 재현하면
    갈라지고, 갈라지면 **화면이 말하는 값과 엔진이 쓰는 값이 달라진다.** 이 저장소가 이미 값을
    치른 병이다(D-NAO-265: 승인 카드의 판정창 ≠ 실행 재검증창).

    우선순위 **DB > env > 코드 상수**. 각 층은 «있는데 못 읽힌» 경우 다음 층으로 내려가되,
    거부됐다는 사실은 조용히 삼키지 않고 플래그로 올려 보낸다.
    """
    db_rejected = False
    if spec.key in overrides:
        val = _coerce(spec, overrides[spec.key])
        if val is not None:
            return val, "db", False, False
        db_rejected = True  # DB에 값이 있는데 거부됨 — 화면이 이 사실을 말해야 한다

    env_rejected = False
    if spec.env:
        raw_env = os.getenv(spec.env)
        if raw_env is not None and raw_env.strip() != "":
            val = _coerce(spec, raw_env)
            if val is not None:
                return val, "env", db_rejected, False
            env_rejected = True

    return spec.default, "code", db_rejected, env_rejected


def get_params(db: Session) -> dict[str, Any]:
    """실행 경로가 쓰는 유효 파라미터 {key: 값}. **항상 전 키가 채워져 나온다.**

    개별 항목이 잘못돼도 그 항목만 아래 층으로 떨어지고 나머지는 DB 값을 쓴다 — 한 칸이
    깨졌다고 전부 되돌리면 «부분 실패가 전체 롤백»이 되어 오히려 예측이 어렵다.
    """
    overrides, _ = _raw_overrides(db)
    return {key: _resolve(spec, overrides)[0] for key, spec in SPECS.items()}


def llm_proposable_keys() -> list[str]:
    """지혜 승격 판사가 «제안»할 수 있는 SPECS 키. 소비층이 각자 필터를 재현하지 않게 한 곳.

    필터를 세 곳(프롬프트 목록·스키마 enum·코드 클램프)에 각자 적으면 갈라지고, 갈라지면
    **프롬프트는 못 고르게 해 놓고 클램프는 통과시키는** 조합이 생긴다.
    """
    return [k for k, sp in SPECS.items() if sp.llm_proposable]


def get_switch(db: Session, key: str) -> bool:
    """bool 파라미터(킬스위치) 하나의 실효값. **폴백 규칙을 한 곳에 둔다** (D-NAO-281).

    ★왜 도메인 모듈이 각자 try/except를 쓰지 않고 여기를 부르는가: 폴백 규칙(「조회가 실패하면
    코드 기본값으로 — 설정 한 줄이 광고 집행 경로를 죽이면 안 된다」)을 두 곳에 적으면 두 곳이
    갈라지고, 갈라진 쪽은 사고가 나야 드러난다. 규칙은 한 곳, 호출은 도메인 모듈에서.

    ★조회 실패 = 「모르면 지금까지 하던 대로」(fail-to-current). fail-closed도 fail-open도
    아니다 — 이 파일 헤더 §3층 구조의 규율 그대로다.
    """
    spec = SPECS[key]  # 없는 키는 오타이므로 KeyError로 «시끄럽게» 죽는 게 맞다
    try:
        return bool(get_params(db)[key])
    except SQLAlchemyError as e:
        # ★적대 리뷰 P2-2(D-NAO-281): DB 예외를 «삼키기만» 하면 세션이 rollback 대기 상태로
        #   남아, 호출부가 이어서 쓰다 PendingRollbackError로 죽는다 — 즉 「설정 조회 실패가
        #   집행 경로를 죽이지 않는다」는 이 함수의 계약이 예외 «종류»에 따라 안 지켜졌다.
        #   DB 예외면 트랜잭션은 이미 돌이킬 수 없으므로(그 안의 미커밋 작업도 어차피 커밋
        #   불가) rollback이 버리는 것은 없다. ★반대로 DB 예외가 «아닌» 것(KeyError 등)까지
        #   rollback 하면 멀쩡한 미커밋 작업을 지우게 되므로 분기를 나눈다.
        try:
            db.rollback()
        except Exception:  # noqa: BLE001 — 원복 시도 실패가 집행 경로를 죽이지 않는다
            log.exception("guardrail_params: 스위치 %s 조회 실패 후 rollback도 실패", key)
        log.warning("guardrail_params: 스위치 %s DB 조회 실패 — rollback 후 코드 기본값 %s로 "
                    "폴백(%s: %s)", key, spec.default, type(e).__name__, e)
        return bool(spec.default)
    except Exception as e:  # noqa: BLE001 — 설정 조회 실패가 집행 경로를 죽이지 않는다
        log.warning("guardrail_params: 스위치 %s 조회 실패 — 코드 기본값 %s로 폴백(%s: %s)",
                    key, spec.default, type(e).__name__, e)
        return bool(spec.default)


def describe(db: Session) -> list[dict]:
    """봉투 현황판용 — 값 + **출처** + 근거 + 허용 범위.

    ★`source`가 이 함수의 존재 이유다: 「지금 무슨 값으로 돌고 있나」와 「그게 어디서 왔나」가
    같이 보여야 한다. 값만 보이면 DB를 고쳤는데 코드 상수가 이기고 있는 상태를 못 본다
    (이 리포가 반복해 데인 «기록됐다 ≠ 코드가 읽는다»의 봉투판).
    """
    overrides, updated_at = _raw_overrides(db)
    rows: list[dict] = []
    for key, spec in SPECS.items():
        val, source, db_rejected, env_rejected = _resolve(spec, overrides)
        rows.append({
            "key": key,
            "label": spec.label,
            # ★bool도 숫자로 나간다(True→1.0) — 응답의 수치 계약을 바꾸지 않기 위해서다.
            #   모양은 `kind`가 말하고, 프론트가 그걸 보고 토글로 그린다.
            "value": float(val),
            "source": source,
            "code_default": float(spec.default),
            "min": float(spec.lo),
            "max": float(spec.hi),
            "why": spec.why,
            "direction": spec.direction,
            "kind": spec.kind,
            "warn": spec.warn,
            "env": spec.env,
            # DB에 값이 있는데 source가 db가 아니면 «거부됨»이다 — 조용히 무시하지 않고 표면화한다.
            "rejected": db_rejected,
            # env에 값이 있는데 그것도 거부됐다 — 같은 이유로 표면화한다(조용한 폴백 금지).
            "env_rejected": env_rejected,
            "updated_at": updated_at.isoformat() if (source == "db" and updated_at) else None,
        })
    return rows


class InvalidGuardrailParams(ValueError):
    """apply_params 검증 실패(본문 비-객체·미지원 키·범위 밖 값). 호출부(라우터)가 이걸
    HTTPException(400, str(e))로 변환한다 — 이 서비스 층은 FastAPI를 모른다(레이어 분리)."""


def apply_params(
    db: Session, body: Any, *, rationale: str,
    proposal_id: int | None = None, wisdom_id: int | None = None,
    merge: bool = False,
) -> dict:
    """봉투 파라미터 **검증 + KV 저장 + change_log 기록**의 단일 진실(B0, D-NAO-248 §4-B).

    ★`merge`가 저장 «범위»를 정한다 — 두 호출부의 맥락이 다르기 때문이다:
      · `merge=False`(기본, **PUT 경로**) — 전체 치환. 넘긴 키만 남고 나머지는 코드 상수로
        복귀한다. 사람이 **화면 전체를 보고 저장**하는 맥락이라 정당하다(기존 계약 불변).
      · `merge=True`(**승인 경로**) — 저장된 행에 그 키만 덮어쓴다. 승인은 «제안 한 건»의
        맥락이라 전체 치환을 쓰면 **사람이 따로 설정해 둔 다른 키가 조용히 코드 기본값으로
        되돌아간다.** 되돌아간 흔적은 화면 source가 'db'→'code'로 바뀌는 것뿐이라, 사람은
        자기가 안 만진 값이 바뀐 걸 못 쫓는다(회귀 테스트
        `test_param_change_approve_does_not_wipe_the_other_params`가 고정).
      ★병합 기준은 «저장된 행의 원문»이지 `get_params()`의 실효값이 아니다 — 실효값으로
        병합하면 사람이 한 번도 설정한 적 없는 키가 코드 기본값 그대로 DB에 박혀 `source`가
        'code'에서 'db'로 바뀐다(안 만졌는데 만진 것으로 보인다).

    ★기존 PUT /settings/guardrail-params 핸들러 본문을 그대로 옮긴 것이다 — 동작·400 조건·
    change_log 내용은 **완전히 동일**해야 한다(회귀 테스트 test_guardrail_params_p1.py가 고정).
    이제 콘솔 승인 핸들러(B1)도 같은 함수를 호출해 검증·기록을 복제하지 않는다 — 복제하면
    두 경로(PUT·승인)가 갈라진다(이 저장소가 반복해 데인 «표방↔실구현 괴리»의 새 버전이 된다).

    ★`db.commit()`을 하지 않는다 — `db.flush()`까지만 한다. 승인 경로(B1)가 「제안 상태 전이
    + 파라미터 적용」을 **한 트랜잭션**으로 묶어야 하므로(실패 시 상태 전이도 롤백), 커밋 시점은
    호출부 책임이다. PUT 핸들러는 이 함수 호출 직후 자신이 커밋한다(기존과 동일한 최종 결과).

    proposal_id/wisdom_id가 주어지면 change_log의 `rationale`에 그 좌표를 병기하고
    `proposal_id` 컬럼에도 심는다(B4가 요구하는 「제안→change_log」 조인 — wisdom_scorecard의
    `_change_rows_for`가 이미 `NaverChangeLog.proposal_id`를 그 방향으로 조회한다).

    반환: {"changed": bool, "change_log_id": int|None, "before": dict, "after": dict}.
    무변화(before==after)면 change_log를 만들지 않고 change_log_id=None(PUT의 기존 계약과 동일).
    """
    if not isinstance(body, dict):
        raise InvalidGuardrailParams("본문은 객체여야 합니다")
    unknown = set(body) - set(SPECS)
    if unknown:
        raise InvalidGuardrailParams(f"알 수 없는 파라미터: {sorted(unknown)}")
    cleaned: dict = {}
    for key, raw in body.items():
        spec = SPECS[key]
        val = _coerce(spec, raw)
        if val is None:
            raise InvalidGuardrailParams(
                f"{key}={raw!r}는 허용 범위 [{spec.lo}, {spec.hi}] 밖이거나 타입이 맞지 않습니다",
            )
        cleaned[key] = str(val)

    before = {k: str(v) for k, v in get_params(db).items()}
    row = db.query(NaverAccountSettings).filter(
        NaverAccountSettings.key == SETTINGS_KEY).first()
    to_store = cleaned
    if merge and row is not None:
        # 저장된 «원문»에 이번 키만 덮어쓴다. 원문이 깨졌으면(파싱 실패) 병합할 근거가 없으니
        # 이번 키만 남긴다 — 읽기 경로(`get_params`)가 깨진 행을 이미 코드 상수로 폴백시키므로
        # 사람이 잃는 값이 없다. SPECS 밖 키는 여기서 떨군다(옛 키가 영원히 따라다니지 않게).
        try:
            existing = json.loads(row.value_json) or {}
        except (TypeError, ValueError):
            existing = {}
        if isinstance(existing, dict):
            to_store = {k: v for k, v in existing.items() if k in SPECS}
            to_store.update(cleaned)
    if row is None:
        row = NaverAccountSettings(key=SETTINGS_KEY, value_json=json.dumps(to_store))
        db.add(row)
    else:
        row.value_json = json.dumps(to_store)
    db.flush()
    after = {k: str(v) for k, v in get_params(db).items()}

    change_log_id = None
    if before != after:
        now = kst_now()
        coords = []
        if proposal_id is not None:
            coords.append(f"proposal_id={proposal_id}")
        if wisdom_id is not None:
            coords.append(f"wisdom_id={wisdom_id}")
        full_rationale = f"{rationale} ({', '.join(coords)})" if coords else rationale
        log_row = NaverChangeLog(
            entity_type="account", entity_id="", campaign_id="",
            action="update_guardrail_params",
            before_value=json.dumps(before, ensure_ascii=False),
            after_value=json.dumps(after, ensure_ascii=False),
            rationale=full_rationale,
            proposal_id=proposal_id,
            # changed_at·executed_at 둘 다 KST 명시 — B-1 가드(D-NAO-169)가 30분 초과 어긋남을 거부한다.
            dry_run=False, executed_at=now, changed_at=now,
        )
        db.add(log_row)
        db.flush()
        change_log_id = log_row.id
    return {"changed": before != after, "change_log_id": change_log_id, "before": before, "after": after}
