# test_alembic_revision_integrity.py — alembic 마이그레이션 체인 정합성 (codex 1R[P1-1], D-NAO-49)
# 배경: auto_operate 마이그레이션이 기존 h2i3j4k5l6m7(rename_rg_fulfillment_to_delivery)와
# revision id가 중복돼 스크립트 디렉토리 로딩 자체가 깨질 뻔함 — id 전역 유일성과 단일
# head(선형 체인)를 정적으로 검증해 재발을 막는다(alembic 미설치 환경에서도 도는 파일 파싱).
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"

_REV_RE = re.compile(r"^revision\s*:?\s*(?:str\s*)?=\s*[\"']([^\"']+)[\"']", re.M)
# down_revision은 두 형태다:
#   · 단일 부모  down_revision = 'abc'
#   · 병합 리비전 down_revision = ('abc', 'def')  ← 병렬 세션이 같은 부모에서 갈라졌을 때
#     alembic이 지원하는 합류 방식. 아래 정규식은 값 부분을 통째로 잡고 따옴표 토큰을 전부 뽑는다.
_DOWN_RE = re.compile(
    r"^down_revision\s*:?\s*(?:Union\[[^=]*\])?\s*=\s*(.+?)(?:\n|$)", re.M
)
_TOKEN_RE = re.compile(r"[\"']([^\"']+)[\"']")


def _load_revisions() -> list[tuple[str, list[str], str]]:
    """[(revision, [down_revision...], filename), ...] — 파일 파싱(alembic 로더 불요).

    병합 리비전은 부모가 2개 이상이므로 down은 항상 리스트(부모 없음=빈 리스트)."""
    out = []
    for f in sorted(VERSIONS_DIR.glob("*.py")):
        text = f.read_text()
        rev = _REV_RE.search(text)
        if not rev:
            continue
        down_m = _DOWN_RE.search(text)
        downs = _TOKEN_RE.findall(down_m.group(1)) if down_m else []
        out.append((rev.group(1), downs, f.name))
    return out


def test_revision_ids_are_globally_unique():
    revs = _load_revisions()
    counts = Counter(r for r, _, _ in revs)
    dups = {r: c for r, c in counts.items() if c > 1}
    dup_files = [(r, f) for r, _, f in revs if r in dups]
    assert not dups, f"revision id 중복 — 로딩 불가: {dup_files}"


def test_single_head_linear_chain():
    """head는 항상 1개. 병렬 세션이 갈래를 만들었으면 **병합 리비전으로 합류**시켜야 한다."""
    revs = _load_revisions()
    ids = {r for r, _, _ in revs}
    referenced_as_down = {d for _, downs, _ in revs for d in downs}
    heads = ids - referenced_as_down
    assert len(heads) == 1, f"head가 1개여야 함(병합 리비전으로 합류 필요): {sorted(heads)}"


def test_down_revisions_all_exist():
    """부모로 지목한 리비전이 실제로 존재하는가(오타·미배포 파일 참조 방지)."""
    revs = _load_revisions()
    ids = {r for r, _, _ in revs}
    missing = [(r, d, f) for r, downs, f in revs for d in downs if d not in ids]
    assert not missing, f"존재하지 않는 down_revision 참조: {missing}"


def test_auto_operate_migration_chained_on_prior_head():
    auto_operate = [
        (r, d) for r, d, f in _load_revisions() if "auto_operate" in f
    ]
    assert len(auto_operate) == 1
    rev, downs = auto_operate[0]
    assert downs == ["g7h8i9j0k1l2"]  # 직전 head(keyword hourly) 다음으로 체인
    assert rev != "h2i3j4k5l6m7"  # 기존 rename_rg 마이그와 충돌하던 id 재사용 금지
