"""점화 준비 부품 생존 점검 — 읽기 전용 1회 실행 (계약 §1 ⓔ).

포트를 스스로 찾는다: 블루-그린 배포로 8001↔8011이 뒤집히므로 하드코딩하면
배포 한 번에 명령이 죽는다.
"""
import json
import sqlite3
import urllib.request

PORT = None
for p in ("8001", "8011"):
    try:
        urllib.request.urlopen("http://localhost:%s/health" % p, timeout=5)
        PORT = p
        break
    except Exception:
        continue
if PORT is None:
    for p in ("8001", "8011"):
        try:
            urllib.request.urlopen(
                "http://localhost:%s/api/naver/ad/settings/guardrail-params" % p, timeout=5)
            PORT = p
            break
        except Exception:
            continue
print("활성 포트: %s" % (PORT or "찾지 못함"))


def api(path):
    return json.load(urllib.request.urlopen(
        "http://localhost:%s/api/naver/ad%s" % (PORT, path), timeout=25))


c = sqlite3.connect("ohisell.db")

print("\n── 점화 게이트 (닫혀 있어야 정상) ──")
r = c.execute("SELECT COUNT(*), SUM(auto_operate), COUNT(DISTINCT optimizer), MAX(updated_at) "
              "FROM naver_campaign_settings").fetchone()
print("  settings %s행 · auto_operate합 %s · optimizer %s종 · 최종갱신 %s" % r)

print("\n── S1 탐색 stale 차단 (★점화 후에만 «값»이 참 — 지금은 키만 있으면 정상) ──")
print("  grep -haE 'held_by_reason' /home/ubuntu/.pm2/logs/ohisell-backend-%s-error.log | tail -2" % PORT)

print("\n── S2 제외 등급 원장 ──")
try:
    for k, v in c.execute("SELECT COALESCE(grade,'(없음)'), COUNT(*) "
                          "FROM naver_search_term_exclusion GROUP BY 1 ORDER BY 2 DESC"):
        print("  %-10s %s" % (k, v))
except Exception as e:
    print("  조회 실패: %s" % e)

print("\n── S4 학습 가능 파라미터 (SPECS) ──")
try:
    for p in api("/settings/guardrail-params")["params"]:
        print("  %-26s %-6s [%s~%s] %s" % (p["key"], p["value"], p["min"], p["max"], p["source"]))
except Exception as e:
    print("  조회 실패: %s" % e)

print("\n── S5 파워링크 문안 ──")
try:
    print("  소재 %s행" % c.execute("SELECT COUNT(*) FROM naver_ad_creative_text").fetchone())
except Exception as e:
    print("  조회 실패: %s" % e)

print("\n── S6 제외 슬롯 ──")
try:
    d = api("/search-term/exclusion-slots")
    print("  groups %s · exhausted %s · unknown %s · healthy %s"
          % (d["groups"], d["exhausted"], d["unknown"], d["healthy"]))
except Exception as e:
    print("  조회 실패: %s" % e)

print("\n── 점화 선행 검사 (safe_to_ignite) ──")
try:
    from app.database import SessionLocal
    from app.models import NaverCampaignSettings
    from app.services.naver_ad import ignition_preflight as pre
    db = SessionLocal()
    ok = sum(1 for row in db.query(NaverCampaignSettings).all()
             if pre.check(db, row.campaign_id)["safe_to_ignite"])
    tot = db.query(NaverCampaignSettings).count()
    print("  safe_to_ignite %s/%s" % (ok, tot))
except Exception as e:
    print("  조회 실패: %s" % e)
