# alert_humanizer.py — 운영 알림을 "사람이 읽는 말"로 바꾸는 순수 변환 SA (D-NAO-103).
#   역할(단일 책임·DB read-only): 엔티티 ID → 사람이 아는 이름, 내부 용어(W1/W3·밴드·
#   D-NAO 코드) → 일상어, 숫자 → 읽기 쉬운 표기. 판정도 조립도 하지 않는다 — 어떤 알림을
#   보낼지는 각 브리핑 harness가 정하고, 이 모듈은 그 결과를 문장 재료로만 바꾼다.
#
#   왜 별도 모듈인가(Jino 2026-07-28): CTR 경보 메시지가 ID·W1/W3·"밴드 내≤4.0"·D-NAO 코드
#   위주라 해석이 불가능했다. 같은 문제가 다른 알림(스파이럴·유령·예산봉투 브리핑)에도 있으므로
#   변환 규칙을 한 곳에 모아 재사용한다. 이 모듈은 naver_ad 다른 SA를 import하지 않는다
#   (원칙18-6 — 순수 변환기, 흐름에 개입 없음).
from __future__ import annotations

import re
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import NaverEntity

# 이름 앞의 **장식 기호만**(○ ● ◎ ■ ▶ 등) 걷어낸다 — 표시용 정리, 원본은 안 바꾼다.
# ★숫자·문자 코드 접두("02. ", "51 ", "P. ")는 **절대 지우지 않는다**(적대적 리뷰 P2-3):
# prod 엔티티 이름 1,057개는 "00. 지문방지필름 / 01. 갤럭시 / 02. 아이폰_카메라"처럼 코드가
# 유일한 구분자인 경우가 155종이라, 접두를 지우면 서로 다른 그룹이 같은 라벨로 뭉개진다
# ("A4.용지"처럼 코드가 아닌 본문을 잘라먹는 오작동도 함께 발생).
_LEADING_DECOR = re.compile(r"^[^0-9A-Za-z가-힣]+")

# 내부 창 코드 → 일상어. 브리핑 harness가 dedupe하면서 'W1+W3'처럼 병기하므로 조합도 처리한다.
_WINDOW_WORDS = {"W1": "최근 1일", "W3": "최근 3일"}

# 캠페인 유형 → 사람이 쓰는 지면 이름.
_CAMPAIGN_TYPE_WORDS = {
    "SHOPPING": "쇼핑검색",
    "WEB_SITE": "파워링크",
    "BRAND_SEARCH": "브랜드검색",
}


def clean_name(name: str | None) -> str:
    """표시용 이름 정리 — 앞머리 **장식 기호만** 제거하고 공백 정돈. 코드 접두는 보존한다
    (그것이 사람이 그룹을 구분하는 유일한 표식인 경우가 많다). 빈 값이면 ''(호출부가 ID 폴백).

    ★대괄호 라벨 접두는 통째로 보존한다(D-NAO-104 라이브 실측): prod 캠페인명
    `● [P_삭제금지]03. 아이폰_강화유리`에서 장식(`● `)과 함께 여는 괄호까지 지우면 화면에
    `P_삭제금지]03. …`라는 **깨진 이름**이 남는다(닫는 괄호만 덩그러니). 뒤에 짝이 되는
    `]`가 있으면 그 `[`는 장식이 아니라 라벨의 시작이다 — 짝 없는 `[`만 장식으로 지운다."""
    if not name:
        return ""
    s = str(name).strip()
    lead = _LEADING_DECOR.match(s)
    if lead:
        decor, rest = lead.group(0), s[lead.end():]
        s = decor[decor.index("["):] + rest if ("[" in decor and "]" in rest) else rest
    return " ".join(s.split())


def entity_names(db: Session, entity_type: str, ids: list[str] | set[str]) -> dict[str, str]:
    """{entity_id: 표시 이름} — naver_entity에서 1쿼리로 조회(N+1 금지).

    ★폴백 계약: 이름이 없거나(행 부재·빈 문자열) 정리 후 비면 **ID를 그대로 값으로 넣는다**.
    사람이 못 읽는 것보다 나쁜 건 "이름이 없어서 대상이 사라지는 것"이다 — 항상 무언가는 남는다."""
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids:
        return {}
    rows = (
        db.query(NaverEntity.entity_id, NaverEntity.name)
        .filter(NaverEntity.entity_type == entity_type, NaverEntity.entity_id.in_(ids))
        .all()
    )
    found = {str(eid): clean_name(nm) for eid, nm in rows}
    return {i: (found.get(i) or i) for i in ids}


def campaign_name(db: Session, campaign_id: str) -> str:
    """캠페인 표시 이름(없으면 ID)."""
    return entity_names(db, "campaign", [campaign_id]).get(campaign_id, campaign_id)


def window_label(window: str) -> str:
    """'W1'→'최근 1일', 'W3'→'최근 3일', 'W1+W3'→'최근 1일·3일'. 모르는 코드는 그대로 노출."""
    parts = [w.strip() for w in str(window or "").split("+") if w.strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return _WINDOW_WORDS.get(parts[0], parts[0])
    if set(parts) == {"W1", "W3"}:
        return "최근 1일·3일"
    return "·".join(_WINDOW_WORDS.get(p, p) for p in parts)


def campaign_type_label(campaign_type: str | None) -> str:
    """'SHOPPING'→'쇼핑검색' 등. 미상('')이면 ''."""
    return _CAMPAIGN_TYPE_WORDS.get(str(campaign_type or ""), "")


def rank_label(avg_rank: float | Decimal | None, *, band_top: float = 4.0) -> str:
    """'밴드 내≤4.0' 같은 내부 표기 대신 사람 말로. 순위 미상이면 '순위 확인 불가'."""
    if avg_rank is None:
        return "순위 확인 불가"
    r = float(avg_rank)
    if r <= band_top:
        return f"노출 순위 정상(평균 {r:.1f}위)"
    return f"노출 순위 밀림(평균 {r:.1f}위)"


def money(won: int | float | Decimal | None) -> str:
    """원 단위 금액 → 읽기 쉬운 한국어(1만 미만은 그대로, 1만~1억은 만 단위, 그 이상 억)."""
    if won is None:
        return "0원"
    v = float(won)
    if abs(v) >= 100_000_000:
        return f"{v / 100_000_000:.1f}억 원"
    if abs(v) >= 10_000:
        return f"{v / 10_000:.1f}만 원"
    return f"{int(round(v)):,}원"


def count(n: int | float | None) -> str:
    """천 단위 구분 숫자 문자열(없으면 '0')."""
    if n is None:
        return "0"
    return f"{int(round(float(n))):,}"


def streak_label(days: int | None) -> str:
    """연속 일수 → '(N일째)'. 1일 이하/미상이면 '(오늘 처음)'."""
    if not days or days <= 1:
        return "(오늘 처음)"
    return f"({days}일째)"


def bullet_list(labels: list[str], *, top_n: int = 3) -> list[str]:
    """상위 top_n만 남기고 나머지는 '외 N개' 한 줄로 압축한 목록(들여쓰기는 호출부 몫)."""
    top = labels[:top_n]
    remainder = len(labels) - len(top)
    if remainder > 0:
        top.append(f"외 {remainder}개")
    return top
