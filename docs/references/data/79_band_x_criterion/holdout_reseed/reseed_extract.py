#!/usr/bin/env python3
"""D-NAO-207 holdout re-seed — ref 79 extract.sql (3)절 HOLD 쿼리를 다른 md5 시드로 재실행하기
위한 .sql 생성기. band_group_total.csv를 읽어 band/half 임시 테이블 INSERT 구문을 만들고,
그 뒤에 extract.sql (3)절 HOLD 쿼리를 한 글자도 안 바꾸고 이어붙인다.

읽기 전용 — DB에 아무것도 쓰지 않는다. 생성된 .sql은 `sqlite3 -readonly`로만 실행할 것.

시드 정의:
  seed0: half = 'A' if md5(adgroup_id) % 2 == 0 else 'B'          (기존 cross_raw.psv와 동일해야 함)
  seed1: half = 'A' if md5("s1:" + adgroup_id) % 2 == 0 else 'B'
  seed2: half = 'A' if md5("s2:" + adgroup_id) % 2 == 0 else 'B'
  seed3: half = 'A' if md5("s3:" + adgroup_id) % 2 == 0 else 'B'

사용:
  python3 reseed_extract.py seed0 > seed0.sql
  python3 reseed_extract.py seed1 > seed1.sql
  ...
  ssh -o BatchMode=yes sellc.ohitech.co.kr "sqlite3 -readonly /home/ubuntu/ohisell/backend/ohisell.db" < seed0.sql > seed0.psv
"""
import csv
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# repo 루트 기준 정본 경로 (이 스크립트는 스크래치패드에 있으므로 절대경로로 명시)
BAND_CSV = ("/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/"
            "Ohiselling/docs/references/data/63_band_decomposition/band_group_total.csv")
# extract.sql (3)절만 뽑아 쓰기 위해 원본 경로도 명시
EXTRACT_SQL = ("/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/"
               "Ohiselling/docs/references/data/79_band_x_criterion/extract.sql")


def half_letter(key: str) -> str:
    v = int(hashlib.md5(key.encode()).hexdigest(), 16) % 2
    return "A" if v == 0 else "B"


def half_key(adgroup_id: str, seed: str) -> str:
    if seed == "seed0":
        return adgroup_id
    prefix = {"seed1": "s1:", "seed2": "s2:", "seed3": "s3:"}[seed]
    return prefix + adgroup_id


def extract_section3(sql_text: str) -> str:
    """extract.sql에서 '-- (3)' 시작부터 '-- (4)' 직전까지를 한 글자도 안 바꾸고 뽑는다."""
    lines = sql_text.splitlines(keepends=True)
    start = None
    end = None
    for i, line in enumerate(lines):
        if start is None and line.lstrip().startswith("-- (3)"):
            start = i
        elif start is not None and line.lstrip().startswith("-- (4)"):
            end = i
            break
    if start is None:
        raise RuntimeError("extract.sql에서 '-- (3)' 절을 못 찾음")
    if end is None:
        end = len(lines)
    return "".join(lines[start:end])


def sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("seed0", "seed1", "seed2", "seed3"):
        print("usage: reseed_extract.py {seed0|seed1|seed2|seed3}", file=sys.stderr)
        sys.exit(1)
    seed = sys.argv[1]

    rows = []
    with open(BAND_CSV, newline="") as f:
        for r in csv.DictReader(f):
            rows.append((r["adgroup_id"], r["campaign_id"], r["campaign_type"], r["band"]))

    with open(EXTRACT_SQL) as f:
        section3 = extract_section3(f.read())

    out = []
    out.append(".mode list\n")
    out.append(".separator |\n")
    out.append("\n")
    out.append("CREATE TEMP TABLE band(adgroup_id TEXT, campaign_id TEXT, campaign_type TEXT, band TEXT);\n")
    out.append("CREATE TEMP TABLE half(adgroup_id TEXT, half TEXT);\n")
    out.append("\n")
    out.append("BEGIN;\n")
    for adgroup_id, campaign_id, campaign_type, band in rows:
        out.append(
            f"INSERT INTO band VALUES ({sql_str(adgroup_id)}, {sql_str(campaign_id)}, "
            f"{sql_str(campaign_type)}, {sql_str(band)});\n"
        )
    for adgroup_id, _campaign_id, _campaign_type, _band in rows:
        h = half_letter(half_key(adgroup_id, seed))
        out.append(f"INSERT INTO half VALUES ({sql_str(adgroup_id)}, {sql_str(h)});\n")
    out.append("COMMIT;\n")
    out.append("\n")
    out.append(section3)

    sys.stdout.write("".join(out))


if __name__ == "__main__":
    main()
