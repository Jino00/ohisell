#!/usr/bin/env python3
# promo_file_fetcher.py — 1P 프로모션 제안서 엑셀(구글드라이브) → prod push  (D-CPP-10)
#
# 왜 Mac에서 도나: 제안서 파일은 **Jino Mac에 동기화된 구글드라이브 폴더**에 있고 prod(sellc)는
#   그 폴더를 볼 수 없다. 기존 쿠팡 수집기들과 같은 구조 — fetch는 Mac, 저장은 prod.
#
# 왜 이 파일이 필요한가: 개당 할인액은 **쿠팡 어디에도 없다**(2026-08-05 라이브 확정).
#   ①프로모션 API: discountBudget(총예산)·supplierFundRate(분담%)뿐  ②상세 화면: 「할인액(원)」 빈칸
#   ③계약서 본문: 대상 상품이 "첨부"로 빠짐  → Jino가 BM에 제출한 제안서 파일이 유일 원천.
#
# 단일 책임: 폴더 읽기 + 엑셀 → 레코드 계약 변환 + push. **매칭(기간→프로모션)은 서버가 한다** —
#   틀리면 분담금이 엉뚱한 행사에 붙는 규칙이라 테스트가 있는 쪽(백엔드 SA/Harness)에 둔다.
#
# 사용:
#   1회 실행:  ~/.ohisell/venv/bin/python3 ~/.ohisell/tools/promo_file_fetcher.py
#   검사만:    ... promo_file_fetcher.py --dry-run
#
# 설정 ~/.ohisell_rocket_fetcher.json (기존 로켓 페처 설정을 그대로 쓴다):
#   {"prod_base_url":"https://sellc.ohitech.co.kr", "ingest_token":"<AD_INGEST_TOKEN>",
#    "vendor_id":"A01029796",
#    "promo_file_dir":"/Users/.../Ohi/21. MD/쿠팡/행사"}
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

import requests

CONFIG_PATH = Path(os.path.expanduser("~/.ohisell_rocket_fetcher.json"))
LOG_PATH = Path(os.path.expanduser("~/.ohisell_promo_file_fetcher.log"))
INGEST_PATH = "/api/coupang/ops/rocket/promotion/discount-files/ingest"

# 기본 폴더(Jino 확인, 2026-08-05). 설정으로 덮을 수 있다.
DEFAULT_DIR = (
    "/Users/jino/Library/CloudStorage/GoogleDrive-jino.kim@ohitech.co.kr/"
    ".shortcut-targets-by-id/1-sXdQoAGFvN14IET1A7DqeUoKD66GJ2K/Ohi/21. MD/쿠팡/행사"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("promo_file")


def load_config() -> dict:
    cfg: dict = {}
    if CONFIG_PATH.is_file():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            log.warning("설정 파싱 실패(기본값 사용): %s", e)
    cfg.setdefault("promo_file_dir", DEFAULT_DIR)
    return cfg


def read_workbook(path: Path) -> list[dict]:
    """제안서 엑셀 → [{product_number, discount_type, discount_value}].

    엑셀 구조(실측): 1행 헤더 / 2~4행 안내문 / 5행부터 데이터.
      A=SKUID  B=할인타입(정액수량|정률)  C=할인액 또는 할인율  D=발주가능재고(선택)
    ★단, 정률 파일은 컬럼이 2개뿐인 변형이 있다(260213_S26출시 할인율.xlsx: A=SKUID, B=0.2).
      그래서 위치가 아니라 **값의 꼴**로 판정한다: B가 문자열이면 타입, 숫자면 정률 값.
    안내문 행은 A가 숫자가 아니라 자연히 걸러진다(서버 파서도 한 번 더 방어한다).
    """
    import openpyxl  # 지연 import — 이 도구를 쓰지 않는 환경에서 설치를 강요하지 않는다

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    out: list[dict] = []
    try:
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                if not row or row[0] is None:
                    continue
                sku = str(row[0]).strip()
                if not sku.isdigit():
                    continue  # 헤더·안내문
                rest = [c for c in row[1:] if c is not None and str(c).strip() != ""]
                if not rest:
                    continue
                if isinstance(rest[0], str):
                    if len(rest) < 2:
                        continue
                    dtype, dval = rest[0].strip(), rest[1]
                else:
                    dtype, dval = "정률", rest[0]
                out.append({
                    "product_number": sku,
                    "discount_type": dtype,
                    "discount_value": str(dval),
                })
    finally:
        wb.close()
    return out


def collect(folder: Path) -> list[dict]:
    """폴더의 .xlsx 전부를 레코드 계약으로. 파일명은 NFC로 정규화해서 보낸다.

    ★NFC 정규화를 여기서도 하는 이유: macOS는 파일명을 NFD(자모 분리)로 준다. 서버도 다시
      정규화하지만, 로그·매니페스트에 NFD가 섞이면 사람이 눈으로 비교할 때 혼란스럽다.
    """
    files: list[dict] = []
    for p in sorted(folder.glob("*.xlsx")):
        if p.name.startswith("~$"):
            continue  # 엑셀 임시 잠금 파일
        name = unicodedata.normalize("NFC", p.stem)
        try:
            rows = read_workbook(p)
        except Exception as e:  # 한 파일이 배치를 죽이지 않는다
            log.error("엑셀 읽기 실패 → skip: %s (%s)", name, e)
            continue
        if not rows:
            log.warning("데이터 행 없음 → skip: %s", name)
            continue
        files.append({
            "file_name": name,
            "file_mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            "rows": rows,
        })
    return files


def push(cfg: dict, files: list[dict]) -> int:
    base = str(cfg.get("prod_base_url") or "").rstrip("/")
    token = cfg.get("ingest_token")
    vendor = cfg.get("vendor_id")
    if not (base and token and vendor):
        log.error("push 설정 없음(prod_base_url·ingest_token·vendor_id) — 중단")
        return 2
    try:
        r = requests.post(
            base + INGEST_PATH,
            json={"vendor_id": vendor, "files": files},
            headers={"X-Ingest-Token": str(token)},
            timeout=60,
        )
    except requests.RequestException as e:
        log.error("push 실패(네트워크): %s", e)
        return 1
    if r.status_code != 200:
        log.error("push 실패 status=%s body=%s", r.status_code, r.text[:300])
        return 1
    out = r.json()
    log.info(
        "push 완료: 매칭 %d파일 %d항목 · 미매칭 %d파일 · 제거 %d",
        len(out.get("matched") or []), out.get("items_ingested", 0),
        len(out.get("unmatched") or []), out.get("items_removed", 0),
    )
    for m in out.get("matched") or []:
        log.info("  ✓ %s → 프로모션 %s (%s, %d항목)",
                 m.get("file_name"), m.get("request_id"), m.get("why"), m.get("items", 0))
    # ★미매칭은 WARNING으로 — 조용히 빠지면 그 기간 분담금이 0이 되어 순이익이 과대해진다.
    for u in out.get("unmatched") or []:
        log.warning("  ✗ %s — %s", u.get("file_name"), u.get("reason"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="읽기만 하고 push 안 함")
    args = ap.parse_args()

    cfg = load_config()
    folder = Path(os.path.expanduser(str(cfg.get("promo_file_dir"))))
    if not folder.is_dir():
        log.error("제안서 폴더 없음: %s", folder)
        return 2

    files = collect(folder)
    if not files:
        log.warning("보낼 파일 없음: %s", folder)
        return 0
    log.info("제안서 %d개 수집: %s", len(files), ", ".join(f["file_name"][:40] for f in files))

    if args.dry_run:
        print(json.dumps(files, ensure_ascii=False, indent=2)[:4000])
        return 0
    return push(cfg, files)


if __name__ == "__main__":
    sys.exit(main())
