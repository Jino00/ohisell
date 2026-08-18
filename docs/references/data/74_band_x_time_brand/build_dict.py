#!/usr/bin/env python3
"""B1: 브랜드어 사전을 데이터에서 유도 (읽기 전용). 추정 등재 금지 — 항목마다 출처 좌표.
실행: python3 build_dict.py
출력: brand_dict_confirmed.csv, brand_dict_pending_jino.csv (scratch 폴더)
"""
import csv, re, json, sys, collections

REPO = "/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling"
OUT = "/private/tmp/claude-501/-Users-jino-Library-Mobile-Documents-com-apple-CloudDocs-1Personal-AI-Program-Ohiselling/78288970-12ad-4647-a554-94288862eca2/scratchpad/a3_brand"

NORM = re.compile(r"[^0-9A-Za-z가-힣]")  # 정규화: 공백·기호 전부 제거
def norm(s):
    return NORM.sub("", (s or "")).casefold()

rows_map = list(csv.DictReader(open(f"{REPO}/docs/references/data/63_band_decomposition/adgroup_model_map.csv", encoding="utf-8")))

confirmed = []  # dict rows: token, category, source_file, source_column, source_detail, note
pending = []    # token, reason

def add_confirmed(token, category, source_file, source_column, source_detail, note=""):
    t = norm(token)
    if len(t) < 2:
        return None
    confirmed.append({"token": t, "raw": token, "category": category,
                       "source_file": source_file, "source_column": source_column,
                       "source_detail": source_detail, "note": note})
    return t

def add_pending(token, reason, source_detail=""):
    pending.append({"token": norm(token) or token, "raw": token, "reason": reason, "source_detail": source_detail})

# ── Category A: 제조사/제품군 대표어(series root) ──
# 근거: adgroup_model_map.csv의 parsed_model series prefix (galaxy_s/galaxy_a/galaxy_z_fold/
# galaxy_z_flip → 전부 「갤럭시」로 시작. galaxy_tab → 「갤럭시탭」. ipad → 「아이패드」로 시작.
# device_launch_dates_kr.json의 각 series 배열 「model」 필드 전부가 동일 접두사로 시작함을 대조.
series_root_map = {
    "galaxy_s": "갤럭시", "galaxy_a": "갤럭시", "galaxy_z_fold": "갤럭시", "galaxy_z_flip": "갤럭시",
    "galaxy_tab": "갤럭시탭", "ipad": "아이패드", "iphone": "아이폰", "note": "노트", "mupad": "뮤패드",
}
series_seen = collections.defaultdict(int)
for r in rows_map:
    pm = r["parsed_model"] or ""
    for part in pm.split("|"):
        part = part.strip()
        if ":" not in part:
            continue
        series, code = part.split(":", 1)
        series = series.strip()
        if series in series_root_map:
            series_seen[series] += 1

added_roots = set()
for series, root in series_root_map.items():
    n = series_seen.get(series, 0)
    if n == 0:
        continue
    t = add_confirmed(root, "brand_root",
                       "docs/references/data/63_band_decomposition/adgroup_model_map.csv", "parsed_model",
                       f"series={series} 행 {n}건(신뢰도 무관 — series 식별 자체는 확정)",
                       "series 접두사 — model 대조 없이도 series 판정은 유지")
    added_roots.add(root)

# device_launch_dates_kr.json 교차확인(같은 루트가 json model 문자열 접두사로도 나타남을 병기)
dj = json.load(open(f"{REPO}/backend/app/data/device_launch_dates_kr.json", encoding="utf-8"))
json_root_hits = collections.defaultdict(int)
for key in ("galaxy_s", "galaxy_z_fold", "galaxy_z_flip", "galaxy_a", "galaxy_tab", "ipad"):
    for rec in dj.get(key, []):
        m = rec.get("model", "")
        for root in ("갤럭시탭", "갤럭시", "아이패드"):
            if m.startswith(root):
                json_root_hits[root] += 1
                break
for root, n in json_root_hits.items():
    for c in confirmed:
        if c["token"] == norm(root) and c["category"] == "brand_root":
            c["note"] += f" | device_launch_dates_kr.json:model 접두사 일치 {n}건 교차확인"

# 아이폰(iPhone 실제 모델명 문자열은 이 json에 없음 — 날짜 배열뿐. 아래 확인)
ip = json.load(open(f"{REPO}/backend/app/data/iphone_launch_dates.json", encoding="utf-8"))
assert "model" not in json.dumps(ip), "iphone_launch_dates.json에 모델명이 있으면 재검토 필요"

# search_term_judge.py 화이트리스트 중 브랜드성 토큰만(기능어 제외 — 강화유리/지문방지/보호필름은
# 상품 카테고리·기능 서술어이지 제조사·제품군 이름이 아니므로 브랜드어에서 제외)
_SS_WHITELIST_TOKENS = ("아이폰", "아이패드", "맥세이프", "강화유리", "지문방지", "보호필름")
brand_from_whitelist = {"아이폰", "아이패드", "맥세이프"}
excluded_from_whitelist = set(_SS_WHITELIST_TOKENS) - brand_from_whitelist
for tok in brand_from_whitelist:
    found = any(c["token"] == norm(tok) for c in confirmed)
    if not found:
        add_confirmed(tok, "brand_root",
                      "backend/app/services/naver_ad/search_term_judge.py", "_SS_WHITELIST_TOKENS",
                      "하드코딩 화이트리스트 상수(코드 정본)", "브랜드성 토큰만 채택")
    else:
        for c in confirmed:
            if c["token"] == norm(tok):
                c["source_file"] += " ∪ backend/app/services/naver_ad/search_term_judge.py"
                c["source_column"] += " ∪ _SS_WHITELIST_TOKENS"

# 맥세이프: adgroup_model_map campaign_name 교차확인
mag_hits = sum(1 for r in rows_map if "맥세이프" in (r["campaign_name"] or ""))
for c in confirmed:
    if c["token"] == norm("맥세이프"):
        c["note"] += f" | adgroup_model_map.csv:campaign_name '맥세이프카드케이스' {mag_hits}건 교차확인"

# 아이뮤즈 (mupad 캠페인명에서 확인 — parsed_model엔 series root가 아니라 "mupad"만 있고
# 한글 대표어 "아이뮤즈"는 campaign_name에서 옴)
amuse_hits = [r for r in rows_map if "아이뮤즈" in (r["campaign_name"] or "")]
if amuse_hits:
    add_confirmed("아이뮤즈", "brand_root",
                  "docs/references/data/63_band_decomposition/adgroup_model_map.csv", "campaign_name",
                  f"'● 13. 아이뮤즈_뮤패드' 캠페인 {len(amuse_hits)}행", "타사 태블릿 제조사 브랜드(뮤패드 제조사)")

# ── Category B: 자사 브랜드 ──
# 근거: naver_search_term_daily의 「자사키워드」 캠페인(핸드폰필름·골프필름 adgroup) 실검색어 리터럴.
# (SQL 실행은 별도 — 여기서는 이미 확보한 관측 결과를 고정 기입, 좌표 병기)
add_confirmed("오하이", "self_brand", "naver_search_term_daily(prod)", "search_term",
              "adgroup_id=grp-a001-01-000000031116306(핸드폰필름,자사키워드) 최다클릭 검색어, clk=757 cost=83368 n=363행(2026-08-18 조회, 창 전체)",
              "리터럴 검색어 텍스트에서 직접 관측 — 매핑표 없음")
add_confirmed("OHI", "self_brand", "naver_search_term_daily(prod)", "search_term",
              "adgroup_id=grp-a001-01-000000031116306, 검색어='OHI' clk=10 cost=906 n=329행 · 'OHI필름' clk=6 n=61행(2026-08-18 조회)",
              "영문 표기형")

# ── Category C: 모델 코드(기기 세대·모델 식별자) — 기계적 추출 ──
# C1: parsed_model series:code, match_confidence in (exact,fuzzy)만(unresolved/UNKNOWN/generic 제외)
BAD_CODE = re.compile(r"unresolved|UNKNOWN|generic|^구형$|^\d+-\d+$")
c1_added = collections.defaultdict(lambda: {"n": 0, "conf": set(), "adgroups": []})
for r in rows_map:
    conf = r["match_confidence"]
    if conf not in ("exact", "fuzzy"):
        continue
    pm = r["parsed_model"] or ""
    for part in pm.split("|"):
        part = part.strip()
        if ":" not in part:
            continue
        series, code = part.split(":", 1)
        series = series.strip(); code = code.strip()
        if not code or BAD_CODE.search(code):
            continue
        key = code
        c1_added[key]["n"] += 1
        c1_added[key]["conf"].add(conf)
        if len(c1_added[key]["adgroups"]) < 3:
            c1_added[key]["adgroups"].append(r["adgroup_id"])

for code, info in sorted(c1_added.items()):
    add_confirmed(code, "model_code",
                  "docs/references/data/63_band_decomposition/adgroup_model_map.csv", "parsed_model",
                  f"match_confidence={'/'.join(sorted(info['conf']))}, {info['n']}행, 예: {','.join(info['adgroups'])}",
                  "series:code 파싱 결과 중 code 부분")

# C2: adgroup_name에서 (폴드|플립)+숫자[와이드|울트라|SE] 및 트라이폴드 — 정규식 구조 추출
PAT_FOLD = re.compile(r"(폴드|플립)\d+(와이드|울트라|SE)?|트라이폴드")
c2_hits = collections.defaultdict(lambda: {"n": 0, "adgroups": []})
for r in rows_map:
    for field in ("adgroup_name", "campaign_name"):
        s = r[field] or ""
        for m in PAT_FOLD.finditer(s):
            tok = m.group()
            c2_hits[tok]["n"] += 1
            if len(c2_hits[tok]["adgroups"]) < 3:
                c2_hits[tok]["adgroups"].append(r["adgroup_id"])
for tok, info in sorted(c2_hits.items()):
    add_confirmed(tok, "model_code",
                  "docs/references/data/63_band_decomposition/adgroup_model_map.csv", "adgroup_name/campaign_name",
                  f"정규식 (폴드|플립)+숫자[와이드/울트라/SE] 매치 {info['n']}행, 예: {','.join(info['adgroups'])}",
                  "리터럴 adgroup_name/campaign_name에서 직접 추출")

# C3: 소다케이스_갤럭시/소다케이스_아이폰 campaign의 adgroup_name에서 모델 서브명 추출
# (parsed_model이 이 accessory 캠페인들을 커버하지 않음 — band_group_total의 model map 대상 밖)
SODA_CAMPAIGNS = {"소다케이스_갤럭시", "소다케이스_아이폰"}
SUFFIX_STRIP = re.compile(r"(_맥|_일)$")
c3_hits = collections.defaultdict(lambda: {"n": 0, "adgroups": []})
for r in rows_map:
    if r["campaign_name"] not in SODA_CAMPAIGNS:
        continue
    an = SUFFIX_STRIP.sub("", r["adgroup_name"] or "")
    an = an.strip()
    if len(an) < 2:
        continue
    c3_hits[an]["n"] += 1
    if len(c3_hits[an]["adgroups"]) < 3:
        c3_hits[an]["adgroups"].append(r["adgroup_id"])
for tok, info in sorted(c3_hits.items()):
    add_confirmed(tok, "model_code",
                  "docs/references/data/63_band_decomposition/adgroup_model_map.csv", "adgroup_name",
                  f"소다케이스_갤럭시/아이폰 캠페인, _맥/_일 접미(케이스 부착방식 태그) 제거 후 {info['n']}행, 예: {','.join(info['adgroups'])}",
                  "band_group_total의 parsed_model이 커버 안 하는 액세서리 캠페인 — adgroup_name 원문에서 직접 추출")

# ── Jino 확인 대기(회색) ──
add_pending("소다케이스", "자사 제품 캠페인명(소다케이스_갤럭시/아이폰)엔 있으나, naver_search_term_daily 실검색어에선 244건 중 대다수가 '오하이'와 결합된 형태로만 나타남(예: 오하이하이브리드소다케이스). 독립 자사 브랜드어인지 제품 스타일/재질 서술어(케이스 촉감 은유)인지 데이터만으로 못 가른다.")
add_pending("버디", "galaxy_a:budi_unresolved(LG U+ 갤럭시 버디4/5 리브랜딩폰)의 series는 확인되나 match_confidence=unresolved라 모델 대조가 안 됐고, 동시에 골프필름 adgroup(grp-a001-01-000000043935093, 자사키워드 캠페인)의 검색어 '버디필름'·'오하이버디필름'은 골프 용어(버디=birdie)로 보이는 문맥과 공존한다(같은 검색어 표층형이 두 문맥에서 관측됨). 토큰 하나로 두 개념이 충돌해 사전에 넣으면 골프 검색어를 폰 브랜드로 오분류한다 — 사전에서 제외.")
add_pending("A73", "device_launch_dates_kr.json 자체가 출처 상충으로 launch_kr=null(status=unreleased) 표기. adgroup_model_map.csv엔 2행 존재하나 match_confidence=unresolved.")
add_pending("일미리케이스", "자사몰(○ 01. 자사몰) adgroup_name이나, '일미리'=1mm 두께 서술어로 보여 브랜드어가 아니라 제품 규격 서술어일 가능성 — 데이터만으로 판정 불가.")

# ── 출력 ──
with open(f"{OUT}/brand_dict_confirmed_raw.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["token", "raw", "category", "source_file", "source_column", "source_detail", "note"])
    w.writeheader()
    for c in confirmed:
        w.writerow(c)

with open(f"{OUT}/brand_dict_pending_jino_raw.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["token", "raw", "reason", "source_detail"])
    w.writeheader()
    for p in pending:
        w.writerow(p)

print(f"confirmed tokens: {len(confirmed)} (unique norm: {len(set(c['token'] for c in confirmed))})")
print(f"pending tokens: {len(pending)}")
print("category counts:", collections.Counter(c["category"] for c in confirmed))
print("제외된 화이트리스트 토큰(기능어라 브랜드어 아님):", excluded_from_whitelist)
