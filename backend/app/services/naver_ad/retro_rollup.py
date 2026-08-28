# retro_rollup.py — SA 소급 채점 rollup (D-NAO-45, PLAN_naver-ad-retro-scoring.md §5)
"""역할(SA·순수 함수): 진단 보드 as-of 스냅샷 행들을 지평(d3/d7)별 방향 정밀도로 접는다.

★DB를 읽지 않는다 — 행은 호출부가 넘긴다. 그래서 라우터(/retro-scorecard)와 성과뷰
  타임라인 하니스가 **같은 한 벌**을 쓴다. 종전엔 라우터 안의 비공개 함수였고, 그대로 두면
  타임라인이 같은 산식을 두 번째로 적어 넣게 된다(정의 중복 = 미래의 불일치 사고).

★정직 경계(ref 31 · 원칙22): 이건 **방향 정확도 계기판**이지 인과 성과 검증이 아니다.
  "우리 판단이 맞았나"까지고 "그래서 이익이 늘었나"는 카나리 몫이다.
"""
from __future__ import annotations

from app.models import NaverRetroSignal
from app.services.naver_ad.probe_cell_aggregate import env_cell_of_date

VERDICTS = ("correct", "gray", "wrong", "no_spend")

# D-NAO-267 (M2 계약 §4-A T1 / ref 65 S2-ⓐ · 북극성 §5-3 ② 「확정 지식의 판정면 주입」 첫 사례).
# ★순서가 곧 ref 63 §1-3의 라벨 상호배타 우선순위다(휴일 > 주말 > 평일) — 한 날짜는 정확히
#   한 칸에만 들어간다. 그래야 «평시+주말+공휴일 = 전체» 항등식이 성립한다.
# ★판정을 새로 만들지 않고 probe_cell_aggregate.env_cell_of_date를 그대로 쓴다(계약 §2-3
#   「기존 숫자를 재사용하고 새 상수를 발명하지 않는다」). 두 벌이 되면 같은 날짜가 표면마다
#   다른 칸에 들어간다.
DAY_CLASSES = ("weekday", "weekend", "holiday")

# 항등식 검산 대상 — board_rollup이 내는 값 중 «더할 수 있는» 키만.
# precision_spenders는 비율이라 더해지지 않는다(ref 63 §1-1: 비율은 가법이 아니라서
# 요인별로 쪼개 합산할 수 없다 — 그게 이 트랙이 ROAS 대신 이익 절대액을 축으로 삼은 이유다).
_ADDITIVE_KEYS = ("n", "correct", "gray", "wrong", "no_spend", "bleed_sum")


def board_rollup(rows: list[NaverRetroSignal], horizon: int) -> dict:
    """단일 보드·단일 지평(d3/d7)의 rollup.

    n, correct/gray/wrong/no_spend,
    precision_spenders(= correct/(correct+gray+wrong) — no_spend 제외, 지출 지속 타깃 기준),
    bleed_sum(down/pause & verdict=correct 행의 양수 bleed 합, ref 31 §1-c와 동일 산식).
    """
    verdict_attr, bleed_attr = f"verdict_d{horizon}", f"bleed_post{horizon}"
    counts = dict.fromkeys(VERDICTS, 0)
    bleed_sum = 0
    for row in rows:
        verdict = getattr(row, verdict_attr)
        if verdict is None:  # 아직 채점 전(사후창 미도달) — rollup 대상 아님
            continue
        counts[verdict] = counts.get(verdict, 0) + 1
        if row.direction in ("down", "pause") and verdict == "correct":
            bleed_sum += max(0, getattr(row, bleed_attr) or 0)
    spenders = counts["correct"] + counts["gray"] + counts["wrong"]
    precision = round(counts["correct"] / spenders, 4) if spenders else None
    return {
        "n": spenders + counts["no_spend"],
        "correct": counts["correct"], "gray": counts["gray"],
        "wrong": counts["wrong"], "no_spend": counts["no_spend"],
        "precision_spenders": precision, "bleed_sum": bleed_sum,
    }


def day_class_rollup(rows: list[NaverRetroSignal], horizon: int) -> dict:
    """★**신호 발신일**의 평시/주말/공휴일 분리 (D-NAO-267 · 계약 §4-A T1 = ref 65 S2-ⓐ).

    ## ★★먼저 읽을 것 — 이 함수가 «가르지 못하는» 것 (적대 리뷰 1R P1-1)

    초판은 이 분리를 「ref 63의 확정 지식을 판정면에 그대로 옮긴 것」이라고 적었다.
    **그 주장은 거짓이었다.** 이유는 grain이다:

      · ref 63의 축은 `ad_profit_{g,d}` — 날짜 d **당일**에 난 이익이다(§1-2).
      · 이 모듈의 축은 `verdict_d{h}`·`bleed_post{h}` — `asof_date` **이후 h일**에 걸쳐
        측정된 값이다(`retro_scorer._score_window`: post = asof+1 … asof+h).

    그래서 `asof_date`로 칸을 갈라도 **사후창은 안 갈린다.** 실측(달력 산술이라 예외 없음):

      | asof 요일 | 이 함수의 칸 | 사후창 d3 비평시 | 사후창 d7 비평시 |
      |---|---|---|---|
      | 월·화 | weekday | 0/3 | **2/7** |
      | 목·금 | weekday | **2/3** | **2/7** |
      | 일    | weekend | **0/3** | **2/7** |

    읽는 법 두 가지 — 둘 다 나쁘다:
      ① **d7은 분리가 원리적으로 0이다.** 7일 연속 구간은 시작 요일과 무관하게 토·일을
         정확히 2일 포함한다. 즉 `weekday` 칸의 `bleed_sum`도 주말 효과에 그대로 오염돼
         있다 — 계약 §1이 걷어내려던 «평시 과소평가»가 이 칸에선 하나도 안 걷힌다.
      ② **d3은 부분적으로 «뒤집혀» 있다.** 일요일 발신 신호(weekend 칸)의 사후창이
         비평시 0/3인데, 목·금 발신(weekday 칸)은 2/3다. 약한 게 아니라 반대를 가리킨다.

    ⇒ **이 함수가 답하는 질문은 「주말 성과가 어땠나」가 아니라 「주말에 «내린 판단»이
       어땠나」다.** 그것도 볼 값어치가 있어서 남기되, 이름·응답 필드·문서 어디서도 전자를
       주장하지 않는다. ref 63의 질문에 답하는 것은 날짜 grain이 실재하는 표면 —
       `pao_scope_roster.day_class_split`(같은 슬라이스, 계약 §4-A T1의 「밴드 판정 표면」) —
       이고, 항등식이 «진짜» 성립하는 곳도 거기다.

    ## 왜 «분리»이고 «보정»이 아닌가

    ref 63 §4-1이 계정 391일 창에서 확정한 것: **주말 Σexcess −8,020,470원**(30,606
    group-day) · **공휴일 −915,912원**(4,547 group-day). 둘 다 홀드아웃 게이트 통과 +
    라벨 우선순위 민감도에서 부호 안정(주말 ≤29%·공휴일 ≤18%)이다 — 이 문서에서 가장 강한
    두 행이다. 그런데 성적표는 이 날들을 평시와 **섞어서** 재 왔다. 그러면 평시 성과가
    확정된 음의 효과에 눌려 **과소평가**된다.

    그래서 분리해 «따로» 보인다. **보정(빼기)이 아니다** — 계수를 곱하거나 빼는 순간 그
    숫자는 「모형이 만든 값」이 되고, 이 트랙은 그 자리에서 보정계수로 이미 한 번 데었다
    (D-NAO-244: 상한만 실었더니 TPU 21일 총이익 부호가 뒤집혔다). 원본을 세 칸으로 나눠
    놓기만 하면 «얼마나 다른지»는 보는 사람이 직접 읽는다.

    ## 무엇을 «안» 하나 (계약 §3 금지선 — 주입은 A/B만)

    ref 63이 같은 표에서 잰 다른 축은 여기 **안 들어온다**:
      · **명절연휴** −1,050,319원 — 크기는 실측이나 홀드아웃 **판정불능**(검증창 연휴 0일).
        재현이 미검증이라 층으로 못 올린다. env_cell_of_date가 `holidays.SouthKorea()`로
        판정하므로 연휴 «당일»은 holiday 칸에 들어가지만, 그건 공휴일 축의 일부로 세는
        것이지 연휴를 독립 층으로 승격한 게 아니다.
      · **휴가창·출시축(F1a·F1b·F2)** — 부호 미확정(휴가창은 두 해 방향이 반대, F1b는 라벨
        우선순위 대안A에서 부호 반전). 계약 §3 「미확정 환경은 층 승격 금지, 라벨만」.

    즉 이 함수의 층은 정확히 **2층(평시 / 주말+공휴일)**이고, 계약 §4-B⑤가 「그 이상 쪼개기
    금지(첫 라운드)」로 미리 못 박은 그대로다. weekend·holiday를 따로 내는 것은 ref 63이
    둘을 **따로** 확정했기 때문이지 3층으로 쓰라는 뜻이 아니다.

    ## 항등식

    `identity.ok`가 이 함수의 자기 검산이다 — 세 칸의 가법 키 합이 전체와 일치해야 한다.
    한 날짜가 두 칸에 들어가면(이중계상) 여기서 깨진다. 값을 그냥 믿지 않고 **응답에 검산
    결과를 같이 싣는** 이유: 이 트랙에서 「분리해 놨는데 합이 안 맞는」 종류의 결함은 화면을
    봐선 안 보이고, 보는 사람은 세 칸을 각각만 읽는다.
    """
    buckets: dict[str, list[NaverRetroSignal]] = {dc: [] for dc in DAY_CLASSES}
    for row in rows:
        # asof_date = 신호를 «뜬» 날. 사후창(asof+1..asof+h)은 이걸로 안 갈린다 —
        # 위 docstring의 표 참조. 그래서 이 칸의 뜻은 「그 날의 성과」가 아니라
        # 「그 날 내린 판단」이고, 아래 basis/limitation이 응답에서 그걸 밝힌다.
        buckets[env_cell_of_date(row.asof_date)].append(row)

    out: dict = {dc: board_rollup(buckets[dc], horizon) for dc in DAY_CLASSES}

    total = board_rollup(rows, horizon)
    summed = {k: sum(out[dc][k] for dc in DAY_CLASSES) for k in _ADDITIVE_KEYS}
    out["identity"] = {
        "total": {k: total[k] for k in _ADDITIVE_KEYS},
        "sum_of_parts": summed,
        "ok": all(summed[k] == total[k] for k in _ADDITIVE_KEYS),
        "note": "평시+주말+공휴일 = 전체 (칸 배타성 검산)",
    }
    # ★한계를 «응답에» 싣는다 (적대 리뷰 1R P1-1). 문서에만 적으면 이 값을 읽는 쪽은
    #   못 본다 — 그리고 이 값은 「주말 성과」로 오해되기 딱 좋은 모양이다.
    out["basis"] = "asof_date (신호 발신일) — 사후창 아님"
    out["measures"] = "그 날 내린 «판단»의 정확도. 그 날의 «성과»가 아니다."
    out["limitation"] = (
        f"사후창(asof+1..asof+{horizon})은 이 칸으로 분리되지 않는다. "
        "d7은 7일 연속이라 어느 발신일이든 주말을 정확히 2일 포함하므로 분리가 원리적으로 0이고, "
        "d3은 일요일 발신의 사후창이 목·금 발신보다 오히려 더 «평시»다. "
        "ref 63의 주말·공휴일 효과(−8,020,470원·−915,912원)를 재려면 날짜 grain 표면을 볼 것 "
        "— pao_scope_roster.day_class_split."
    )
    return out
