#!/usr/bin/env python3
"""check_pao_canon.py — docs/PAO_OPS.md 좌표(파일·심볼·API·테이블.컬럼·크론)가 아직
실재하는지 세는 자. 계약 `docs/contracts/CONTRACT_pao_ops_canon.md` §8 · 슬라이스 S3.

왜: PAO_OPS.md는 두 종류의 사실 주장을 담는다 — ①실측값(매일 변해 원리적으로 자동
검사 불가, `<!-- MEASURED -->` 블록에 가둔다) ②좌표(파일·심볼·API·테이블·크론 잡 이름 —
안 변하거나 변하면 코드에서 확인 가능). 이 스크립트는 ②만 센다.

★가장 위험한 자리(마커 짝 검사): MEASURED 여는 마커 수 ≠ 닫는 마커 수면 문서 뒷부분이
통째로 검사에서 빠질 수 있는데, 그러면 「검사할 게 없어서 실패 0건」이 뜬다 — 교훈 #123
(발견 0건과 검사 안 됨이 같은 숫자로 보이는 병)과 같은 모양이다. 그래서 마커가 안 맞으면
침묵하지 않고 비-0 exit로 죽는다.

★P1 수리(2026-08-30, 리뷰어 D8·D11 재현): 마커 «개수»가 맞아도 구간이 잘못 벌어지면
(마커가 산문에 잘못 삽입되거나, 닫는 마커가 실수로 아래로 밀리면) 위 검사를 통과한 채
좌표가 침묵 제외될 수 있었다. 그래서 이 검사기는 ①백틱·펜스 코드블록 안의 마커는 마커로
안 치고 ②MEASURED 블록 «안»에 분류 가능한 좌표(파일·심볼·API·테이블·크론 모양)가 있으면
그 자체를 실패로 보고하며(존재 여부와 무관 — 블록은 값만 담아야 한다) ③MEASURED 구간
수·제외된 백틱 수를 매번 출력한다.

읽기 전용·표준 라이브러리만 사용·외부 호출 0(계약 §4 금지선). 게이트가 아니다 — 훅 등록·
CI 필수화 없음(계약 §3-6). 명령 한 줄로 재는 자일 뿐이다.

사용법:
    python scripts/check_pao_canon.py [--doc <경로>]

exit code: 0=전건 통과, 1=실패 좌표 ≥1건, 2=MEASURED 마커 짝 불일치·문서 없음.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# repo 루트는 이 스크립트 위치 기준으로 찾는다(scripts/의 부모) — cwd에 의존하지 않는다.
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

DEFAULT_DOC = REPO_ROOT / "docs" / "PAO_OPS.md"

# --- MEASURED 마커 -----------------------------------------------------------
# 두 마커는 서로의 접두를 공유하지 않는다("<!-- /MEASURED -->"는 "<!--" 뒤가 곧장
# "/"라 OPEN_RE의 "MEASURED" 리터럴과 안 겹친다) — 교차 오매칭 없음(수동 대조 확인).
OPEN_RE = re.compile(r"<!--\s*MEASURED\s*-->")
CLOSE_RE = re.compile(r"<!--\s*/MEASURED\s*-->")

# 백틱 인용 — 줄을 안 넘는다(이 문서의 인라인 코드 관례).
BACKTICK_RE = re.compile(r"`([^`\n]+)`")

# 펜스 코드블록(```...```) — 여러 줄에 걸칠 수 있어 DOTALL.
FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


def _code_spans(text: str) -> list[tuple[int, int]]:
    """인라인 백틱·펜스 코드블록의 [시작, 끝) 구간 목록.

    ★P1 수리(2026-08-30, 리뷰어 재현 D8·D11): 문서 머리(6행)의 규약 설명용 리터럴
    예시 "`<!-- MEASURED -->`"가 «백틱 안»에 있는데도 진짜 마커로 잡혀 실제 구간 하나를
    만들고 있었다. 코드·인용 예시 안의 마커는 마커로 치지 않는다 — 이 구간 안에서
    발견된 OPEN_RE/CLOSE_RE 매치는 find_excluded_regions()가 무시한다.
    """
    spans: list[tuple[int, int]] = []
    for m in FENCE_RE.finditer(text):
        spans.append((m.start(), m.end()))
    for m in BACKTICK_RE.finditer(text):
        spans.append((m.start(), m.end()))
    return spans


def _in_spans(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in spans)


HTTP_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH")

# 테이블.컬럼과 파일명(예: safe_deploy.sh)이 헷갈리는 것을 막는 확장자 배제 목록
# (계약 §8 "테이블.컬럼은 foo.py 같은 파일명과 헷갈리므로 확장자 배제가 필요하다").
_FILE_EXTENSIONS = {
    "py", "md", "sh", "json", "ts", "tsx", "js", "jsx", "sql", "yml", "yaml",
    "txt", "db", "log", "ini", "cfg", "toml", "plist", "jsonl", "csv", "tsv",
    "env", "lock", "html", "css",
}

# 크론 잡 이름 접두 — 계약 §8 "run_naver_* · sync_naver_* · sweep_naver_* ·
# snapshot_naver_* 등 잡 이름 접두". 현재 정본의 실제 크론 목록(generate_*·verify_*)까지
# "등"으로 포괄하기 위해 넓힌다 — 분류 우선순위는 아래 classify()의 주석 참조.
CRON_PREFIXES = (
    "run_naver_", "sync_naver_", "sweep_naver_", "snapshot_naver_",
    "generate_", "verify_",
)

# 분류 정규식. 우선순위가 중요하다(§8: "경로::심볼이 /도 포함하므로 파일보다 먼저 봐야
# 한다"):
#   1) 심볼(경로::이름) — "/"도 "::"도 다 가질 수 있으므로 가장 먼저 본다.
#   2) API(METHOD /path) — "/"를 가지므로 파일보다 먼저.
#   3) 파일("/" 포함) — 위 둘에 안 걸리고 "/"가 있으면 파일.
#   4) 테이블.컬럼(snake_case.snake_case, 확장자 아님) — 파일명 오판 방지로 확장자 배제.
#   5) 크론 잡 이름(접두 일치).
#   그 외 = 미분류.
SYMBOL_RE = re.compile(r"^(?P<path>[\w./\-]+)::(?P<sym>[A-Za-z_][A-Za-z0-9_]*)$")
API_RE = re.compile(r"^(?P<method>GET|POST|PUT|DELETE|PATCH)\s+(?P<path>/\S+)$")
TABLE_RE = re.compile(r"^(?P<table>[a-z][a-z0-9_]*)\.(?P<col>[a-z][a-z0-9_]*)$")
CRON_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# "파일" 후보는 repo-상대 경로처럼 생겨야 한다 — 영숫자/밑줄로 시작하고 단어문자·점·
# 슬래시·하이픈만 포함. 이게 없으면 프런트 라우팅 경로("/naver-ad/performance", 앞에
# "/"가 붙어 repo 루트 기준으로 존재할 수 없다)나 코드 스니펫(`load_dotenv("/home/...")`,
# 괄호·따옴표가 섞여 있다)이 "/"를 포함한다는 이유만으로 파일로 오분류돼 거짓 실패를 낸다
# (2026-08-30 실측: 이 둘이 실제로 그렇게 오분류됐다 — 미분류로 떨어지는 게 맞다).
# ★P2-6(2026-08-30): 첫 글자에 "."을 허용한다(.gitignore·.github/workflows/*.yml처럼
# 점으로 시작하는 실재 파일이 「경로처럼 안 생겼다」는 이유로 영구 미분류되는 함정 수리).
# 위 두 배제 사례(앞에 "/"만 있거나 괄호·따옴표가 섞인 경우)는 이 변경으로도 여전히
# 안 걸린다 — "/"·"("·"\"" 자체가 `\.?[A-Za-z0-9_]` 시작 조건을 못 넘기 때문.
FILE_PATH_RE = re.compile(r"^\.?[A-Za-z0-9_][\w./\-]*$")


class MarkerError(Exception):
    """MEASURED 마커 짝이 안 맞을 때 — 침묵 스킵 금지, 호출부가 비-0으로 죽인다."""


def find_excluded_regions(text: str) -> list[tuple[int, int]]:
    """MEASURED 블록의 [시작, 끝) 문자 위치 구간 목록.

    여는 마커 수 ≠ 닫는 마커 수, 닫는 마커가 먼저 나옴, 안 닫힌 블록 — 셋 다
    MarkerError로 종료한다(계약 §2-2: "조용히 넘어가지 마세요").
    """
    code_spans = _code_spans(text)

    events: list[tuple[int, str, int]] = []
    for m in OPEN_RE.finditer(text):
        if _in_spans(m.start(), code_spans):
            continue
        events.append((m.start(), "open", m.end()))
    for m in CLOSE_RE.finditer(text):
        if _in_spans(m.start(), code_spans):
            continue
        events.append((m.start(), "close", m.end()))
    events.sort(key=lambda e: e[0])

    regions: list[tuple[int, int]] = []
    depth = 0
    region_start = 0
    n_open = n_close = 0
    for pos, kind, end in events:
        if kind == "open":
            n_open += 1
            if depth == 0:
                region_start = pos
            depth += 1
        else:
            n_close += 1
            if depth == 0:
                raise MarkerError(
                    f"닫는 마커(<!-- /MEASURED -->)가 여는 마커 없이 위치 {pos}에 나타남"
                )
            depth -= 1
            if depth == 0:
                regions.append((region_start, end))

    if depth != 0:
        raise MarkerError(
            f"MEASURED 블록이 닫히지 않음 — 여는 마커 {n_open} / 닫는 마커 {n_close}"
        )
    if n_open != n_close:
        raise MarkerError(
            f"MEASURED 마커 수 불일치 — 여는 마커 {n_open} / 닫는 마커 {n_close}"
        )
    return regions


def _in_regions(pos: int, regions: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in regions)


def extract_coordinates(text: str, regions: list[tuple[int, int]]) -> list[str]:
    """MEASURED 블록 밖의 백틱 인용을 유니크하게(등장 순서 보존) 뽑는다."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for m in BACKTICK_RE.finditer(text):
        if _in_regions(m.start(), regions):
            continue
        val = m.group(1)
        if val not in seen_set:
            seen_set.add(val)
            seen.append(val)
    return seen


def extract_measured_coordinates(text: str, regions: list[tuple[int, int]]) -> list[str]:
    """MEASURED 블록 «안»의 백틱 인용을 유니크하게(등장 순서 보존) 뽑는다.

    ★P1 수리 ②의 원료: MEASURED 블록은 «값»을 담는 곳이지 «좌표»를 담는 곳이 아니다.
    이 함수가 뽑은 각 값을 run()이 classify()로 다시 분류해, 5유형 중 하나로 분류되는
    것이 있으면(=좌표가 블록 안에 잘못 들어있으면) 실패로 올린다. 존재 여부(살아있나
    죽었나)와 무관하게 «블록 안에 좌표 모양이 있다는 사실 자체»가 결함이다 — 그래야
    D8(마커 쌍이 산문에 잘못 삽입)·D11(닫는 마커가 아래로 밀림)처럼 «개수는 맞지만
    구간이 잘못 벌어지는» 경우를 잡는다(실제 구간 안에 삼켜진 좌표가 반드시 있다).
    """
    seen: list[str] = []
    seen_set: set[str] = set()
    for m in BACKTICK_RE.finditer(text):
        if not _in_regions(m.start(), regions):
            continue
        val = m.group(1)
        if val not in seen_set:
            seen_set.add(val)
            seen.append(val)
    return seen


def classify(coord: str) -> str:
    if SYMBOL_RE.match(coord):
        return "symbol"
    if API_RE.match(coord):
        return "api"
    if "/" in coord and FILE_PATH_RE.match(coord):
        return "file"
    m = TABLE_RE.match(coord)
    if m and m.group("col").lower() not in _FILE_EXTENSIONS:
        return "table"
    if CRON_RE.match(coord) and coord.startswith(CRON_PREFIXES):
        return "cron"
    return "unclassified"


# --- 존재 검사 (5유형) --------------------------------------------------------

def check_file(coord: str) -> tuple[bool, str]:
    p = REPO_ROOT / coord
    if p.exists():
        return True, ""
    return False, "파일 없음"


def check_symbol(coord: str) -> tuple[bool, str]:
    m = SYMBOL_RE.match(coord)
    assert m
    path, sym = m.group("path"), m.group("sym")
    p = REPO_ROOT / path
    if not p.exists():
        return False, f"파일 없음: {path}"
    try:
        content = p.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        return False, f"파일 읽기 실패: {path} ({e})"
    if sym not in content:
        return False, f"심볼 없음: {sym} (in {path})"
    return True, ""


_ROUTERS_CACHE: set[tuple[str, str]] | None = None


def _load_routers() -> set[tuple[str, str]]:
    """backend/app/routers/*.py를 한 번만 읽어 {(METHOD, 전체경로)} 집합으로 캐시.

    전체경로 = APIRouter(prefix=...)와 @router.<method>("...") 경로 문자열 그대로의
    단순 이어붙이기다(파라미터 이름까지 정확히 같아야 하는 exact-match).
    """
    global _ROUTERS_CACHE
    if _ROUTERS_CACHE is not None:
        return _ROUTERS_CACHE

    routers_dir = REPO_ROOT / "backend" / "app" / "routers"
    combined: set[tuple[str, str]] = set()
    prefix_re = re.compile(r'APIRouter\([^)]*prefix\s*=\s*["\']([^"\']*)["\']')
    route_re = re.compile(r'@router\.(get|post|put|delete|patch)\(\s*["\']([^"\']+)["\']')

    if routers_dir.exists():
        for f in sorted(routers_dir.glob("*.py")):
            text = f.read_text(encoding="utf-8", errors="ignore")
            pm = prefix_re.search(text)
            prefix = pm.group(1) if pm else ""
            for rm in route_re.finditer(text):
                method = rm.group(1).upper()
                path = rm.group(2)
                combined.add((method, prefix + path))

    _ROUTERS_CACHE = combined
    return _ROUTERS_CACHE


def check_api(coord: str) -> tuple[bool, str]:
    """(METHOD, 전체경로) exact 매치만 통과시킨다.

    ★P2-3 수리(2026-08-30): 예전엔 exact 매치 실패 시 경로를 앞에서부터 잘라가며
    가장 긴 접미사가 routers 원문에 «리터럴 부분문자열»로 있는지 보는 폴백이 있었다.
    이게 `GET /api/COMPLETELY-BOGUS/health`처럼 **중간 세그먼트가 통째로 조작된** 좌표를
    접미사 "/health"가 원문에 있다는 이유만으로 통과시켰다(조용한 거짓 초록). 현재 정본의
    12개 API 좌표는 전부 exact 매치로 통과한다(폴백 없이도 실패 0) — 그러니 폴백 자체를
    없앤다: 조용히 통과하는 경로보다 「exact 매치가 아니면 실패로 보고 사람이 확인」이
    더 안전하다(§8 규약이 요구하는 «경로 조각 존재»는 이 정도로 좁혀도 충분히 충족된다 —
    현재 12/12 exact).
    """
    m = API_RE.match(coord)
    assert m
    method, path = m.group("method"), m.group("path")
    combined = _load_routers()

    if (method, path) in combined:
        return True, ""

    return False, f"backend/app/routers/*.py에 exact 매치 없음: {coord}"


_MODELS_CACHE: str | None = None


def _load_models() -> str:
    global _MODELS_CACHE
    if _MODELS_CACHE is None:
        p = REPO_ROOT / "backend" / "app" / "models.py"
        _MODELS_CACHE = p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""
    return _MODELS_CACHE


# ★P2-2 수리(2026-08-30): 테이블·컬럼 클래스 블록 스코핑.
# models.py는 SQLAlchemy declarative 스타일이 균일하다(2026-08-30 확인: 129개
# `class ... :` 전부 최상위 컬럼 0-indent, `mapped_column(...)`만 쓰고 구식 `Column(...)`
# 없음 1676건). 그래서 "다음 `class ` 줄 전까지"를 그 테이블의 몸통으로 자르고,
# 컬럼은 그 몸통 «안에서만» "줄 시작(들여쓰기 허용) + 컬럼명 + :" 패턴(Mapped 타입
# 애노테이션 관례)으로 찾는다.
_CLASS_HEADER_RE = re.compile(r"^class\s+\w+.*:\s*$", re.MULTILINE)
_TABLENAME_RE = re.compile(r'__tablename__\s*=\s*["\']([^"\']+)["\']')

_TABLE_BLOCKS_CACHE: dict[str, str] | None = None


def _load_table_blocks() -> dict[str, str]:
    """models.py를 클래스 단위로 잘라 {테이블명: 클래스 몸통 텍스트} 캐시.

    ★왜: 예전 check_table()은 "테이블명이 파일 전체에 있는가"·"컬럼명이 파일 전체에
    있는가"를 «독립적으로» 봐서, `naver_ad_daily.executed_change_log_id`처럼 테이블은
    A에 실재하고 컬럼은 B에만 실재하는 «교차 오염» 좌표가 통과했다(2026-08-30 리뷰
    재현, P2-2). 클래스 몸통으로 스코핑하면 그 컬럼이 «그 테이블 소속»일 때만 통과한다.
    """
    global _TABLE_BLOCKS_CACHE
    if _TABLE_BLOCKS_CACHE is not None:
        return _TABLE_BLOCKS_CACHE

    content = _load_models()
    blocks: dict[str, str] = {}
    if content:
        starts = [m.start() for m in _CLASS_HEADER_RE.finditer(content)]
        starts.append(len(content))
        for i in range(len(starts) - 1):
            block = content[starts[i] : starts[i + 1]]
            tm = _TABLENAME_RE.search(block)
            if tm:
                blocks[tm.group(1)] = block

    _TABLE_BLOCKS_CACHE = blocks
    return blocks


def check_table(coord: str) -> tuple[bool, str]:
    m = TABLE_RE.match(coord)
    assert m
    table, col = m.group("table"), m.group("col")
    blocks = _load_table_blocks()
    if not blocks:
        return False, "backend/app/models.py 없음 또는 테이블 클래스 0건"
    block = blocks.get(table)
    if block is None:
        return False, f"테이블 없음: {table}"
    if not re.search(rf"(?m)^\s*{re.escape(col)}\s*:", block):
        return False, f"컬럼 없음: {col} (테이블 {table} 클래스 블록 안에서 못 찾음)"
    return True, ""


_SCHEDULER_CACHE: str | None = None


def _load_scheduler() -> str:
    global _SCHEDULER_CACHE
    if _SCHEDULER_CACHE is None:
        p = REPO_ROOT / "backend" / "app" / "services" / "scheduler_service.py"
        _SCHEDULER_CACHE = p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""
    return _SCHEDULER_CACHE


def check_cron(coord: str) -> tuple[bool, str]:
    content = _load_scheduler()
    if not content:
        return False, "backend/app/services/scheduler_service.py 없음"
    if f'"{coord}"' in content or f"'{coord}'" in content:
        return True, ""
    return False, f"크론 잡 이름 없음: {coord}"


_CHECKERS = {
    "file": check_file,
    "symbol": check_symbol,
    "api": check_api,
    "table": check_table,
    "cron": check_cron,
}

_LABELS = {
    "file": "파일",
    "symbol": "심볼",
    "api": "API",
    "table": "테이블.컬럼",
    "cron": "크론",
}


def run(doc_path: Path) -> int:
    if not doc_path.exists():
        print(f"문서 없음: {doc_path}")
        return 2

    text = doc_path.read_text(encoding="utf-8", errors="ignore")

    try:
        regions = find_excluded_regions(text)
    except MarkerError as e:
        print(f"MEASURED 마커 오류: {e}")
        return 2

    coords = extract_coordinates(text, regions)
    measured_coords = extract_measured_coordinates(text, regions)

    counts = {"file": 0, "symbol": 0, "api": 0, "table": 0, "cron": 0, "unclassified": 0}
    failures: list[tuple[str, str, str]] = []

    for c in coords:
        t = classify(c)
        counts[t] += 1
        if t == "unclassified":
            continue
        ok, reason = _CHECKERS[t](c)
        if not ok:
            failures.append((c, t, reason))

    total = len(coords)
    unclassified_n = counts["unclassified"]
    classified_n = total - unclassified_n
    fail_n = len(failures)
    pass_n = classified_n - fail_n

    # ★P1 수리 ②: MEASURED 블록 «안»의 백틱도 똑같이 분류한다. 5유형 중 하나로
    # 분류되면(=좌표 모양이 값이 담겨야 할 자리에 있으면) 그 자체가 결함이다 — 실재
    # 여부(살았나 죽었나)는 안 본다. 이게 D8(마커 쌍 삽입)·D11(닫는 마커 이동)을 잡는
    # 자리다: 마커가 잘못 벌어지면 삼켜진 구간에 좌표 모양 백틱이 반드시 섞여 있다.
    leak_failures: list[tuple[str, str, str]] = []
    for c in measured_coords:
        t = classify(c)
        if t == "unclassified":
            continue
        leak_failures.append(
            (c, t, f"MEASURED 블록 안에 좌표가 들어 있다 — 마커 위치를 확인하라 (유형: {_LABELS[t]})")
        )

    # ★P1 수리 ③: 제외량을 항상 출력한다 — 「어제 몇이었나」를 기억 안 해도 이상이
    # 보이게 하는 층. 값만 계산하고 안 찍으면 고친 게 아니다(위임문 §1 지시).
    print(
        f"전체 {total} / 통과 {pass_n} / 실패 {fail_n} / 미분류 {unclassified_n}"
        f"   (MEASURED 구간 {len(regions)} · 제외 백틱 {len(measured_coords)}"
        f" · 블록내 좌표오염 {len(leak_failures)})"
    )
    print(
        "유형별 — 파일 {file} / 심볼 {symbol} / API {api} / "
        "테이블.컬럼 {table} / 크론 {cron}".format(**counts)
    )

    if failures:
        print("실패 목록:")
        for name, t, reason in failures:
            print(f"  - [{_LABELS[t]}] {name} — {reason}")

    if leak_failures:
        print("MEASURED 블록 내 좌표 오염 목록:")
        for name, t, reason in leak_failures:
            print(f"  - [{_LABELS[t]}] {name} — {reason}")

    if failures or leak_failures:
        return 1

    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="docs/PAO_OPS.md(기본값)의 좌표(파일·심볼·API·테이블.컬럼·크론)가 "
        "아직 실재하는지 repo 정적으로 센다. 읽기 전용 — prod·네트워크 호출 0."
    )
    ap.add_argument(
        "--doc",
        default=str(DEFAULT_DOC),
        help="검사할 문서 경로 (기본값: docs/PAO_OPS.md)",
    )
    args = ap.parse_args(argv)
    return run(Path(args.doc))


if __name__ == "__main__":
    sys.exit(main())
