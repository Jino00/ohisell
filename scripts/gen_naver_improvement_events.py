#!/usr/bin/env python3
# gen_naver_improvement_events.py — 트랙의 확정 결정(D-NAO-N)을 성과뷰 ⑥개선 타임라인용
# JSON 카탈로그로 뽑는다. 로컬 실행·멱등. (성과뷰 Phase 3, 계획서 §3-2)
"""트랙 파일 → docs/naver_ad_improvement_events.json

  입력 A: docs/tracks/active/track_naver-ad-optimization.md   D-NAO-N 헤더
  입력 B: git log --grep 'D-NAO-<N>'                          그 결정이 코드가 된 커밋 날짜
  출력  : docs/naver_ad_improvement_events.json               repo 커밋 → safe_deploy로 prod 반영

신규 테이블·마이그레이션 없음(계획서 §3-2). 이벤트는 100건 미만·읽기 전용이고 변경 이력이
곧 git 이력이라 DB에 둘 이득이 없다.

★파서 계약은 계획서 §3-2의 정규식이 아니다(2026-07-28 교정). 그 정규식은 "날짜가 제목보다
  먼저"인 형태만 잡아 라이브 트랙 114개 헤더 중 24개만 매칭했다 — 완료 기준 skipped=0을
  원리적으로 통과할 수 없었다. 실제 헤더는 아래 6형태가 섞여 있고, 이 스크립트는 전부 흡수한다:
    ① - **D-NAO-1 제목.** (2026-07-07 개정, …) 본문        날짜가 볼드 **밖**
    ② - **D-NAO-59 제목 (2026-07-19, Jino 확정 — …).** 본문  날짜가 볼드 안 **뒤**
    ③ - **D-NAO-75 (2026-07-21) 제목 — 부제.** 본문          날짜가 볼드 안 **앞**
    ④ - **D-NAO-83 (2026-07-22 밤, …) 제목 — "…"**: 본문     볼드가 `**:` 로 끝남
    ⑤ - **D-NAO-2 제목**: 본문                                날짜 **없음**
    ⑥ - **D-NAO-8 제목** — 본문                               날짜 없음 + 대시 본문

★무성 누락 금지(이 코드베이스 관례): parsed/skipped/duplicate를 stdout에 찍고 skipped>0이면
  exit 1. "조용히 몇 개 빠졌다"가 이 파일이 만들 수 있는 최악의 실패다.

★사람 우선(계획서 §3-2): 기존 JSON에 `curated: true`인 행은 label_ko/detail_ko/scope/
  campaign_id를 **덮어쓰지 않는다.** 자동 라벨이 안 읽힐 때 사람이 고친 문구가 영구 보존된다.

사용:
    python3 scripts/gen_naver_improvement_events.py            # 생성/갱신
    python3 scripts/gen_naver_improvement_events.py --check    # 쓰지 않고 차이만 확인(CI용)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TRACK_FILE = REPO_ROOT / "docs" / "tracks" / "active" / "track_naver-ad-optimization.md"
OUT_FILE = REPO_ROOT / "docs" / "naver_ad_improvement_events.json"

# 헤더 한 줄: "- **D-NAO-<번호> <볼드 본문>**<나머지>"
#   볼드 본문에 `**`가 다시 나오지 않는다는 보장이 없어 최소매칭(.*?)으로 첫 닫힘까지만 잡는다.
HEADER_RE = re.compile(r"^- \*\*D-NAO-(\d+)\s*(.*?)\*\*(.*)$")
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# 날짜가 들어 있는 괄호구 — 라벨에서 걷어낸다("(2026-07-19, Jino 확정 — …)").
# ★괄호 **한 단계 중첩**까지 흡수한다: 라이브 실측에 "(2026-07-28, N배송(도착보장) 규명 …)"
#   같은 헤더가 있고, [^)]*로 잡으면 안쪽 ')'에서 끊겨 라벨 앞에 잔해가 남는다(D-NAO-98 실사례).
DATE_PAREN_RE = re.compile(r"\(\s*\d{4}-\d{2}-\d{2}(?:[^()]|\([^()]*\))*\)")
# 라벨 앞뒤에 남는 장식들.
TRIM_CHARS = " \t—-–·:.,"
LABEL_MAX = 80
DETAIL_MAX = 200
# 날짜를 볼드 밖에서 찾을 때 훑는 범위. 본문 끝까지 훑으면 한참 뒤 문장의 날짜(예: 인용된
# 과거 사고 날짜)를 결정일로 잘못 집는다.
REST_DATE_SCAN = 140


def _screen_safe(text: str) -> str:
    """★사장님 화면에 나갈 문자열에서 **내부 용어를 걷어낸다**(계획서 §0-2 명시 금지).

    트랙은 엔지니어가 엔지니어에게 쓴 글이라 결정 코드(D-NAO-19-④)·파일명(forecast_source.py)·
    PR 번호·URL·스네이크케이스 식별자(naver_ad_daily, target_roas)가 그대로 섞여 있다.
    실측(2026-07-28): 99행 중 D-NAO 코드 20행·기술 토큰 40행. Phase 1은 이걸 테스트로 막았는데
    타임라인만 뚫려 있었다(리뷰 P1-3).

    자동 정화가 어색해지는 행은 `curated:true`로 사람이 고치면 영구 보존된다 — 그게 §3-2가
    curated 플래그를 둔 이유다. 여기서는 **기계적으로 확실한 것만** 지운다.
    """
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\[\[[^\]]*\]\]", " ", text)                    # 메모리 위키링크 [[topic-name]]
    text = re.sub(r"\bD-NAO-\d+(?:-[^\s,)\]]+)?", " ", text)      # 결정 코드(하위 기호 포함)
    text = re.sub(r"\bPR\s*#\d+", " ", text)
    text = re.sub(r"\bref\s*\d+\b", " ", text, flags=re.IGNORECASE)
    # 호스트·경로 — 2R 리뷰 실측: prod 호스트+DB 경로가 그대로 남아 있었다
    # (sellc.ohitech.co.kr:/home/ubuntu/ohisell/backend/ohisell.db). `.db`가 확장자 목록에
    # 없었고, 호스트명은 어떤 규칙에도 안 걸렸다.
    text = re.sub(r"\b[\w-]+(?:\.[\w-]+){2,}(?::\S+)?", " ", text)          # 도메인(+경로)
    text = re.sub(r"(?:^|(?<=[\s(]))/[\w./-]+", " ", text)                  # 절대 경로
    text = re.sub(r"[\w./-]*\.(?:py|md|json|tsx?|sh|sql|jsonl|db|ya?ml)\b", " ", text)
    # 엔티티 raw ID — D-NAO-103① 정면 금지. cmp-a001-02-…/grp-…/nad-…/kwd-…
    text = re.sub(r"\b(?:cmp|grp|nad|kwd|adg)-[\w-]+", " ", text)
    # snake_case 식별자. 뒤에 한글 조사가 붙으면 \b가 안 걸려 살아남던 것을 lookahead로 잡는다
    # (forecast_scorer의 → 2R 리뷰 지적).
    text = re.sub(r"(?<![\w])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![\w])", " ", text)
    # 코드 조각만 남은 빈 괄호·연속 구두점 정리
    text = re.sub(r"\(\s*[,·—\-]*\s*\)", " ", text)
    text = re.sub(r"\s*,\s*(?=[,)])", "", text)
    return text


def _clean(text: str, limit: int) -> str:
    """마크다운 장식·중복 공백을 걷어내고 limit자로 자른다(자르면 말줄임표)."""
    text = DATE_PAREN_RE.sub(" ", text)
    text = text.replace("**", "").replace("`", "").replace("*", "")
    text = _screen_safe(text)
    text = re.sub(r"\s+", " ", text).strip(TRIM_CHARS).strip()
    # 문장 중간에서 잘려 홀로 남은 따옴표를 떼어낸다(원문 인용이 중간에 끊긴 경우).
    if text.count('"') % 2 == 1:
        text = text.rstrip('"').rstrip(TRIM_CHARS)
    if len(text) > limit:
        text = text[: limit - 1].rstrip(TRIM_CHARS) + "…"
    return text


def _strip_leading_parenthetical(body: str) -> str:
    """맨 앞 괄호구를 통째로 걷어낸다(중첩 1단계까지).

    헤더 직후의 괄호는 실측상 거의 전부 **메타 기록**이다 — "(2026-07-07 개정, Jino 원문: …)",
    "(번호 조정 사유: D-NAO-97은 PR #127이 선점 …)". 사장님 화면에 나갈 한 줄은 그다음부터다.
    """
    while body.startswith("("):
        depth, end = 0, -1
        for i, ch in enumerate(body):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end < 0:  # 닫히지 않은 괄호 — 손대지 않는다(더 망가뜨리지 않기)
            break
        # 괄호 앞 문장의 마침표가 그대로 남으면 문장 분리가 위치 0에서 끊긴다(detail이 빈다).
        body = body[end + 1 :].lstrip(" :—-.,").strip()
    return body


def _is_readable(text: str) -> bool:
    """정화 후 **뜻이 남았는지** 판정. 껍데기만 남았으면 안 쓰는 편이 낫다.

    2R 리뷰 실측: 정화가 주어를 지워 `의 정밀화·재확인`(D-NAO-59), 기호만 남은
    `①유형별 차등 TTL: · · =D+1 만료`(D-NAO-37) 같은 조각이 화면에 나갔다. 이건 거짓은
    아니지만 사장님이 읽을 수 있는 문장도 아니다 — 빈 값으로 두면 화면이 라벨만 보여준다.
    """
    if len(text) < 8:
        return False
    # 한글 조사로 시작 = 앞말이 지워진 조각
    if re.match(r"^(?:의|을|를|이|가|은|는|에|에서|으로|로|와|과|도)\s", text):
        return False
    # 한글 글자가 절반도 안 되면 기호·코드 잔해
    hangul = len(re.findall(r"[가-힣]", text))
    return hangul >= max(6, len(text.replace(" ", "")) * 0.4)


def _first_sentence(rest: str) -> str:
    """본문 첫 문장(마침표/느낌표 기준). 문장 경계가 없으면 앞부분만.

    정화 뒤 읽을 수 없는 조각이 되면 **빈 문자열**을 낸다(§0-2 · 2R 리뷰).
    """
    body = _strip_leading_parenthetical(rest.lstrip(" :—-").strip()).lstrip(" :—-.,")
    if not body:
        return ""
    m = re.search(r"(?<=[.!?])\s", body)
    out = _clean(body[: m.start() + 1] if m else body, DETAIL_MAX)
    return out if _is_readable(out) else ""


def parse_track(path: Path) -> tuple[list[dict], list[str], list[int]]:
    """트랙 → (events, skipped_lines, duplicate_nums).

    같은 번호가 여러 번 헤더로 나오면 **첫 등장만** 채택한다(트랙은 최신을 위에 덧붙이는
    구조라 첫 등장이 그 결정의 최신 서술이다). 중복은 실패가 아니므로 skipped와 분리해 센다.
    """
    events: dict[int, dict] = {}
    skipped: list[str] = []
    duplicates: list[int] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.startswith("- **D-NAO-"):
            continue
        m = HEADER_RE.match(raw)
        if not m:
            skipped.append(raw[:160])
            continue
        num, bold, rest = int(m.group(1)), m.group(2), m.group(3)
        label = _clean(bold, LABEL_MAX)
        if not label:
            # 볼드가 번호뿐인 헤더 — 라벨을 본문 앞에서 빌려온다.
            label = _clean(rest, LABEL_MAX)
        if not label:
            skipped.append(raw[:160])
            continue
        if num in events:
            duplicates.append(num)
            continue
        # 날짜: 볼드 안 우선(그 결정의 날짜), 없으면 볼드 직후 본문 앞부분.
        dm = DATE_RE.search(bold) or DATE_RE.search(rest[:REST_DATE_SCAN])
        events[num] = {
            "ref_key": f"D-NAO-{num}",
            "decided_date": dm.group(1) if dm else None,
            "label_ko": label,
            "detail_ko": _first_sentence(rest),
            "scope": "account",
            "campaign_id": None,
            "curated": False,
        }
    return [events[k] for k in sorted(events)], skipped, duplicates


def git_effective_dates(nums: list[int]) -> dict[int, str]:
    """D-NAO-<N>을 인용한 **가장 이른** 커밋 날짜 = 그 결정이 코드가 된 날.

    한 번의 git log로 전부 훑는다(번호마다 부르면 100회 프로세스 기동).
    """
    if not nums:
        return {}
    try:
        out = subprocess.run(
            ["git", "log", "--reverse", "--date=short", "--pretty=format:%ad%x09%s%x09%b%x00"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True, timeout=120,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(f"[warn] git log 실패 — 적용일은 전부 결정일 추정으로 갑니다: {exc}", file=sys.stderr)
        return {}
    found: dict[int, str] = {}
    cite = re.compile(r"D-NAO-(\d+)")
    for chunk in out.split("\0"):
        if not chunk.strip():
            continue
        day, _, body = chunk.partition("\t")
        day = day.strip()
        if not DATE_RE.fullmatch(day):
            continue
        for n in {int(x) for x in cite.findall(body)}:
            found.setdefault(n, day)  # --reverse라 첫 등장이 가장 이른 커밋
    return found


def merge_curated(events: list[dict], existing_path: Path) -> tuple[list[dict], int]:
    """기존 파일의 curated:true 행이 쓴 문구를 그대로 살린다(재생성해도 사람 손이 안 지워짐)."""
    if not existing_path.exists():
        return events, 0
    try:
        old = json.loads(existing_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[warn] 기존 카탈로그를 읽지 못해 curated 보존을 건너뜁니다: {exc}", file=sys.stderr)
        return events, 0
    curated = {r["ref_key"]: r for r in old.get("events", []) if r.get("curated")}
    kept = 0
    for ev in events:
        c = curated.get(ev["ref_key"])
        if not c:
            continue
        for field in ("label_ko", "detail_ko", "scope", "campaign_id"):
            if field in c:
                ev[field] = c[field]
        ev["curated"] = True
        kept += 1
    # 트랙에서 사라진 curated 행도 버리지 않는다(사람이 손으로 넣은 이벤트일 수 있다).
    known = {e["ref_key"] for e in events}
    for key, c in curated.items():
        if key not in known:
            events.append(c)
            kept += 1
    events.sort(key=lambda e: (e.get("effective_date") or e.get("decided_date") or "", e["ref_key"]))
    return events, kept


def build() -> dict:
    events, skipped, duplicates = parse_track(TRACK_FILE)
    commits = git_effective_dates([int(e["ref_key"].split("-")[-1]) for e in events])
    for ev in events:
        n = int(ev["ref_key"].split("-")[-1])
        commit_day = commits.get(n)
        if commit_day:
            ev["effective_date"] = commit_day
            ev["effective_confidence"] = "commit"
        else:
            ev["effective_date"] = ev["decided_date"]
            ev["effective_confidence"] = "assumed" if ev["decided_date"] else "unknown"
    events, kept = merge_curated(events, OUT_FILE)
    payload = {
        "generated_from": str(TRACK_FILE.relative_to(REPO_ROOT)),
        "events": events,
    }
    return {
        "payload": payload, "skipped": skipped,
        "duplicates": duplicates, "curated_kept": kept,
        "commit_matched": sum(1 for e in events if e.get("effective_confidence") == "commit"),
        "no_date": sum(1 for e in events if not e.get("effective_date")),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="트랙 D-NAO → 개선 타임라인 JSON 카탈로그")
    ap.add_argument("--check", action="store_true", help="파일을 쓰지 않고 파싱 결과만 확인")
    args = ap.parse_args()

    if not TRACK_FILE.exists():
        print(f"[error] 트랙 파일이 없습니다: {TRACK_FILE}", file=sys.stderr)
        return 2
    res = build()
    events = res["payload"]["events"]
    print(
        f"parsed={len(events)} skipped={len(res['skipped'])} duplicate={len(res['duplicates'])} "
        f"commit_matched={res['commit_matched']} no_date={res['no_date']} "
        f"curated_kept={res['curated_kept']}"
    )
    for line in res["skipped"]:
        print(f"  [skip] {line}", file=sys.stderr)
    if res["skipped"]:
        print("[error] 파싱하지 못한 헤더가 있습니다 — 무성 누락 금지(계획서 §3-2)", file=sys.stderr)
        return 1
    if args.check:
        return 0
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(
        json.dumps(res["payload"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUT_FILE.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
