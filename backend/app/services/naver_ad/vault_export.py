# vault_export.py — vault_export_sa (D-NAO-54 P5 열람층, docs/PLAN_naver-ad-diary-wisdom.md §P5)
# 역할: 운영 일기(ops_diary_entries)·지혜(ops_wisdom_entries)를 사람이 읽는 마크다운으로 서버측
#   export한다(VM `<backend>/data/vault/Ohisell/`). Mac pull 스크립트(scripts/ohisell_vault_pull.py)가
#   이 디렉토리를 iCloud Obsidian `Vault/Ohisell`로 순방향 미러 → Jino가 Obsidian에서 열람.
#   읽기(diary·wisdom·candidate) + 파일 쓰기만. 제안 생성·실행 경로 접근 없음(원칙18-1).
#
# ★설계: 매 실행 전체 재생성(멱등) — 소급 outcome 기입(P2)·지혜 승격(P3)이 반영되도록 최근
#   8일 일기와 활성/은퇴 지혜 전량을 다시 쓴다. 쓰기는 tmp→os.replace(원자적, 부분 파일 노출
#   방지). 전체 fail-open(관찰·열람 전용 — 예외는 로그만, 집행·크론 체인을 막지 않는다).
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import OpsDiaryEntry, OpsWisdomCandidate, OpsWisdomEntry
from app.services.naver_ad.diary_outcome import EVENT_TYPES, _kst_date
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 일기 export 범위 — 최근 N일(KST 날짜별 1파일).
_DIARY_DAYS = 8

# 요일(0=월) → 한글 라벨. env_snapshot의 weekday와 KST 날짜 weekday 표기에 공용.
_WEEKDAY_KR = ("월", "화", "수", "목", "금", "토", "일")


# ─── 경로 ──────────────────────────────────────────────────────────────────

def _vault_root() -> Path:
    """볼트 루트 — OHISELL_VAULT_DIR env 우선, 없으면 <backend>/data/vault/Ohisell.

    vault_export.py = backend/app/services/naver_ad/vault_export.py → parents[3]=backend.
    """
    env = os.environ.get("OHISELL_VAULT_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "data" / "vault" / "Ohisell"


def _atomic_write(path: Path, text: str) -> None:
    """tmp에 쓰고 os.replace로 교체(원자적) — 리더가 부분 파일을 보지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ─── 포매팅 헬퍼 ──────────────────────────────────────────────────────────

def _kst_dt(created_at: datetime) -> datetime:
    """created_at은 UTC 저장([[sqlite-server-default-now-is-utc]]) → +9h 해서 KST datetime."""
    return created_at + timedelta(hours=9)


def _cell(value) -> str:
    """마크다운 테이블 셀 안전화 — None='-', 파이프·개행 이스케이프."""
    if value is None or value == "":
        return "-"
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def _env_summary(e: OpsDiaryEntry) -> str:
    """환경 스냅샷 컬럼 요약(각 필드 있는 것만)."""
    parts: list[str] = []
    if e.weekday is not None and 0 <= e.weekday <= 6:
        parts.append(_WEEKDAY_KR[e.weekday])
    if e.is_kr_holiday:
        parts.append("공휴일")
    if e.season:
        parts.append(e.season)
    if e.iphone_launch_offset_days is not None:
        parts.append(f"iph{e.iphone_launch_offset_days:+d}")
    if e.spend_pacing_pct is not None:
        parts.append(f"소진{e.spend_pacing_pct:g}%")
    if e.avg_rank is not None:
        parts.append(f"순위{e.avg_rank:g}")
    return " · ".join(parts) if parts else "-"


def _outcome_summary(e: OpsDiaryEntry) -> str:
    """outcome_json(P2 소급 기입) 요약 — d1/d7 보정ROAS·비용 + retro 채점(있으면)."""
    if not e.outcome_json:
        return "-"
    try:
        o = json.loads(e.outcome_json)
    except Exception:  # noqa: BLE001 — 깨진 JSON도 열람을 막지 않는다
        return "-"
    parts: list[str] = []
    for key in ("d1", "d7"):
        w = o.get(key)
        if isinstance(w, dict):
            parts.append(f"{key}: roas {w.get('roas_c')} / 비용 {w.get('cost')}")
    retro = o.get("retro")
    if isinstance(retro, dict):
        verdict = retro.get("verdict_d7") or retro.get("verdict_d3") or "?"
        parts.append(f"retro: {retro.get('direction')}/{verdict}")
    return "<br>".join(parts) if parts else "-"


def _change_str(e: OpsDiaryEntry) -> str:
    """before→after 변경 표기(둘 다 없으면 '-')."""
    if e.before_value is None and e.after_value is None:
        return "-"
    before = "-" if e.before_value is None else e.before_value
    after = "-" if e.after_value is None else e.after_value
    return _cell(f"{before}→{after}")


def _target_str(e: OpsDiaryEntry) -> str:
    if e.target_id:
        return _cell(f"{e.target_type or '?'}:{e.target_id}")
    return _cell(e.target_type)


def _slug(text: str, limit: int = 40) -> str:
    """지혜 파일명용 슬러그 — 한글·영숫자 유지, 나머지는 하이픈. 빈 결과는 'wisdom'."""
    out: list[str] = []
    for ch in text.strip()[:limit]:
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    slug = "".join(out).strip("-")
    return slug or "wisdom"


# ─── 일기 마크다운 ────────────────────────────────────────────────────────

def _render_diary_day(day: date, entries: list[OpsDiaryEntry]) -> str:
    """하루치 일기 md — 집행/차단 이벤트 테이블 + 해석문(observe) 섹션."""
    wd = _WEEKDAY_KR[day.weekday()]
    events = [e for e in entries if e.event_type in EVENT_TYPES]
    observes = [e for e in entries if e.event_type == "observe"]

    lines = [f"# 운영 일기 {day.isoformat()} ({wd})", ""]

    lines.append("## 집행·차단 이벤트")
    if events:
        lines.append("| 시각(KST) | 유형 | 캠페인 | 대상 | 액션 | 변경 | 환경 | 결과 | 사유 |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for e in sorted(events, key=lambda x: x.created_at or datetime.min):
            hhmm = _kst_dt(e.created_at).strftime("%H:%M") if e.created_at else "-"
            lines.append(
                f"| {hhmm} | {_cell(e.event_type)} ({_cell(e.actor)}) | {_cell(e.campaign_id)} "
                f"| {_target_str(e)} | {_cell(e.action)} | {_change_str(e)} | {_env_summary(e)} "
                f"| {_outcome_summary(e)} | {_cell(e.rationale)} |"
            )
    else:
        lines.append("_이 날 집행·차단 이벤트 없음(diary는 best-effort — '행 없음 = 무행위' 해석 금지)._")
    lines.append("")

    lines.append("## 해석문")
    if observes:
        for e in sorted(observes, key=lambda x: x.created_at or datetime.min):
            hhmm = _kst_dt(e.created_at).strftime("%H:%M") if e.created_at else "-"
            label = e.action or "observe"
            lines.append(f"### {label} ({hhmm} KST)")
            lines.append(e.rationale or "_(내용 없음)_")
            lines.append("")
    else:
        lines.append("_해석문 없음._")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _export_diary(db: Session, root: Path, today: date, lower_utc: datetime) -> tuple[int, list[date]]:
    """최근 8일 일기 파일 재생성. 반환 = (쓴 파일 수, 링크용 날짜 목록)."""
    rows = (
        db.query(OpsDiaryEntry)
        .filter(OpsDiaryEntry.created_at.isnot(None), OpsDiaryEntry.created_at >= lower_utc)
        .all()
    )
    target_dates = [today - timedelta(days=i) for i in range(_DIARY_DAYS)]
    buckets: dict[date, list[OpsDiaryEntry]] = {d: [] for d in target_dates}
    for e in rows:
        d = _kst_date(e.created_at)
        if d in buckets:
            buckets[d].append(e)

    written: list[date] = []
    for d in target_dates:
        entries = buckets[d]
        if not entries:
            continue  # 행 없는 날은 빈 파일 안 만든다
        try:
            _atomic_write(root / "diary" / f"{d.isoformat()}.md", _render_diary_day(d, entries))
            written.append(d)
        except Exception as e:  # noqa: BLE001 — 한 파일 실패가 나머지를 막지 않는다
            log.warning("vault_export: 일기 파일 쓰기 실패 %s: %s", d, e)
    return len(written), written


# ─── 지혜 마크다운 ────────────────────────────────────────────────────────

def _win_rate(cand: OpsWisdomCandidate | None) -> tuple[str, str]:
    """후보 tally 조인 → (승률 한 줄, frontmatter 값). 후보 없거나 표본 0이면 'n/a'."""
    if cand is None:
        return ("n/a (후보 tally 없음)", "n/a")
    good = int(cand.good_count or 0)
    bad = int(cand.bad_count or 0)
    total = good + bad
    if total <= 0:
        return (f"good {good} / bad {bad} (표본 0)", "n/a")
    pct = round(good / total * 100.0, 1)
    return (f"good {good} / bad {bad} → {pct}% (표본 {total})", f"{pct}%")


def _render_wisdom(entry: OpsWisdomEntry, cand: OpsWisdomCandidate | None) -> str:
    """지혜 1건 md — frontmatter(status/승률/promoted_at) + 원칙 + 판사 근거 + 승률 + 조건."""
    win_line, win_fm = _win_rate(cand)
    promoted = entry.promoted_at.date().isoformat() if entry.promoted_at else "-"
    env_bucket = "-"
    if cand is not None and cand.env_bucket_json:
        try:
            env_bucket = json.dumps(json.loads(cand.env_bucket_json), ensure_ascii=False)
        except Exception:  # noqa: BLE001
            env_bucket = cand.env_bucket_json

    lines = [
        "---",
        f"status: {entry.status}",
        f"wisdom_id: {entry.id}",
        f"source_candidate_id: {entry.source_candidate_id}",
        f"promoted_at: {promoted}",
        f"win_rate: {win_fm}",
        "---",
        "",
        f"# 지혜 #{entry.id}"
        + ("  (은퇴)" if entry.status == "retired" else ""),
        "",
        "## 원칙",
        entry.wisdom_text or "_(내용 없음)_",
        "",
        "## 판사 근거",
        entry.judge_rationale or "_(근거 없음)_",
        "",
        "## 승률",
        f"- {win_line}",
        f"- 조건: {env_bucket}",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _export_wisdom(db: Session, root: Path) -> tuple[int, list[OpsWisdomEntry]]:
    """활성·은퇴 지혜 전량 재생성(은퇴는 파일 삭제 아니라 frontmatter status). 반환 = (파일 수, active 목록)."""
    entries = db.query(OpsWisdomEntry).order_by(OpsWisdomEntry.id).all()
    cand_ids = [e.source_candidate_id for e in entries]
    cands: dict[int, OpsWisdomCandidate] = {}
    if cand_ids:
        for c in db.query(OpsWisdomCandidate).filter(OpsWisdomCandidate.id.in_(cand_ids)).all():
            cands[c.id] = c

    written = 0
    active: list[OpsWisdomEntry] = []
    for entry in entries:
        try:
            cand = cands.get(entry.source_candidate_id)
            fname = f"{entry.id:03d}-{_slug(entry.wisdom_text or '')}.md"
            _atomic_write(root / "wisdom" / fname, _render_wisdom(entry, cand))
            written += 1
            if entry.status == "active":
                active.append(entry)
        except Exception as e:  # noqa: BLE001 — 한 건 실패가 나머지를 막지 않는다
            log.warning("vault_export: 지혜 파일 쓰기 실패 id=%s: %s", getattr(entry, "id", "?"), e)
    return written, active


# ─── 인덱스 ────────────────────────────────────────────────────────────────

def _render_index(now: datetime, diary_dates: list[date], active: list[OpsWisdomEntry]) -> str:
    lines = [
        "# Ohisell 운영 일기·지혜",
        "",
        f"_생성: {now.strftime('%Y-%m-%d %H:%M')} KST (매일 09:05 자동 재생성, D-NAO-54 P5)_",
        "",
        "## 최근 일기",
    ]
    if diary_dates:
        for d in sorted(diary_dates, reverse=True):
            wd = _WEEKDAY_KR[d.weekday()]
            lines.append(f"- [[diary/{d.isoformat()}|{d.isoformat()} ({wd})]]")
    else:
        lines.append("_아직 일기 없음._")
    lines.append("")
    lines.append("## 활성 지혜")
    if active:
        for entry in active:
            title = (entry.wisdom_text or "").strip().splitlines()[0] if entry.wisdom_text else "(제목 없음)"
            fname = f"{entry.id:03d}-{_slug(entry.wisdom_text or '')}"
            lines.append(f"- [[wisdom/{fname}|#{entry.id} {title[:60]}]]")
    else:
        lines.append("_아직 승격된 지혜 없음._")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ─── 엔트리 ────────────────────────────────────────────────────────────────

def export_vault(db: Session, *, now: datetime | None = None) -> dict:
    """일기·지혜·인덱스를 마크다운으로 재생성(매일 09:05 크론). 전체 fail-open.

    now: as-of 관통(미지정 시 kst_now). 반환 = 쓴 파일 수·에러 여부(로그·테스트용).
    """
    now = now or kst_now()
    today = now.date()
    lower_utc = (now - timedelta(hours=9)) - timedelta(days=_DIARY_DAYS)
    result = {"diary_files": 0, "wisdom_files": 0, "index": False, "error": None}
    try:
        root = _vault_root()
        root.mkdir(parents=True, exist_ok=True)
        result["diary_files"], diary_dates = _export_diary(db, root, today, lower_utc)
        result["wisdom_files"], active = _export_wisdom(db, root)
        _atomic_write(root / "INDEX.md", _render_index(now, diary_dates, active))
        result["index"] = True
    except Exception as e:  # noqa: BLE001 — 관찰·열람 전용: 예외는 로그만, 크론 체인 보호
        log.exception("vault_export: export_vault 실패(fail-open): %s", e)
        result["error"] = str(e)
    return result
