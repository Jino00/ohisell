# otao_po_import.py — 적재 페이로드(JSON) → 발주 원장 + 품목명 사전. **prod에서 돈다.**
#
# 짝은 `otao_po_export.py`(Mac). 이쪽은 pypdf도 Google Drive도 필요 없다 — DB만 있으면 된다.
# 멱등이다: 같은 페이로드를 여러 번 먹여도 원장이 두 배가 되지 않는다(키는 파일 내용 해시).
#
#   python3 scripts/otao_po_import.py --payload /tmp/otao_po_payload.json          # 실제 적재
#   python3 scripts/otao_po_import.py --payload /tmp/otao_po_payload.json --dry-run # 커밋 안 함
#
# ★선행조건: 마이그레이션 `otao1po4n4a`가 적용돼 있어야 한다. 이 앱은 부팅 시 인프로세스
#   마이그레이션을 하지 않으므로 순서는 `scripts/safe_deploy.sh … --migrate`가 강제한다.
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, ".")

from app.database import SessionLocal  # noqa: E402
from app.services.otao_po.ingest import ingest_payload, sync_name_map  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="적재 페이로드 → 발주 원장")
    ap.add_argument("--payload", required=True, help="otao_po_export.py가 만든 JSON")
    ap.add_argument("--dry-run", action="store_true", help="적재해 보고 커밋하지 않는다")
    args = ap.parse_args()

    with open(args.payload, encoding="utf-8") as fh:
        payload = json.load(fh)

    with SessionLocal() as session:
        rep = ingest_payload(session, payload)
        sync_name_map(session, report=rep)

        print(f"PDF {rep.files_scanned} → 발주서 {rep.purchase_orders} / 비발주서 {rep.non_purchase_orders}")
        print(f"신규 {rep.inserted} · 그대로 {rep.unchanged} · 경로갱신 {rep.moved} · 라인 {rep.lines_inserted}")
        print(f"발주번호 {rep.serials} → 정본 {rep.authoritative} / 대체됨 {rep.superseded}")
        # ★아래 다섯 줄은 «0건»이어도 항상 찍는다 — 조용하면 「없다」와 「안 봤다」가 같아진다.
        print(f"수량 검산 불일치 {len(rep.qty_mismatch)}건: {rep.qty_mismatch}")
        print(f"빈 수량(원장 미적재) {len(rep.blank_qty_lines)}건: {rep.blank_qty_lines}")
        print(f"파싱 탈락 라인 {len(rep.dropped_lines)}건: {rep.dropped_lines}")
        print(f"발주일 판독 실패 {len(rep.bad_serial_dates)}건: {rep.bad_serial_dates}")
        print(f"mtime으로만 정본을 정한 발주번호 {len(rep.tie_broken_by_mtime)}건: {rep.tie_broken_by_mtime}")
        print(
            f"사전: 원장 품목명 {rep.map_total}종 → 붙음 {rep.map_resolved} "
            f"(사람 확정 유지 {rep.map_manual_kept}) / 매핑 필요 {len(rep.map_unresolved)}"
        )
        for name in rep.map_unresolved:
            print(f"  매핑 필요: {name}")

        if args.dry_run:
            session.rollback()
            print("⚠️ dry-run — 롤백했다. 아무것도 안 심었다.")
        else:
            session.commit()
            print("✅ 커밋 완료.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
