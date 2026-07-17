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
_DOWN_RE = re.compile(r"^down_revision\s*:?\s*(?:Union\[[^=]*\])?\s*=\s*[\"']([^\"']+)[\"']", re.M)


def _load_revisions() -> list[tuple[str, str | None, str]]:
    """[(revision, down_revision|None, filename), ...] — 파일 파싱(alembic 로더 불요)."""
    out = []
    for f in sorted(VERSIONS_DIR.glob("*.py")):
        text = f.read_text()
        rev = _REV_RE.search(text)
        if not rev:
            continue
        down = _DOWN_RE.search(text)
        out.append((rev.group(1), down.group(1) if down else None, f.name))
    return out


def test_revision_ids_are_globally_unique():
    revs = _load_revisions()
    counts = Counter(r for r, _, _ in revs)
    dups = {r: c for r, c in counts.items() if c > 1}
    dup_files = [(r, f) for r, _, f in revs if r in dups]
    assert not dups, f"revision id 중복 — 로딩 불가: {dup_files}"


def test_single_head_linear_chain():
    revs = _load_revisions()
    ids = {r for r, _, _ in revs}
    referenced_as_down = {d for _, d, _ in revs if d}
    heads = ids - referenced_as_down
    assert len(heads) == 1, f"head가 1개여야 함(선형 체인): {sorted(heads)}"


def test_auto_operate_migration_chained_on_prior_head():
    revs = {r: d for r, d, _ in _load_revisions()}
    auto_operate = [
        (r, d) for r, d, f in _load_revisions() if "auto_operate" in f
    ]
    assert len(auto_operate) == 1
    rev, down = auto_operate[0]
    assert down == "g7h8i9j0k1l2"  # 직전 head(keyword hourly) 다음으로 체인
    assert rev != "h2i3j4k5l6m7"  # 기존 rename_rg 마이그와 충돌하던 id 재사용 금지
