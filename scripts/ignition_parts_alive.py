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


# ★읽기 전용을 «관례»가 아니라 «구조»로 강제한다(적대 리뷰 P2 채택). 지금은 SELECT뿐이지만,
#   향후 누가 쓰기를 섞으면 즉시 예외로 죽는 편이 조용히 prod를 바꾸는 것보다 낫다.
c = sqlite3.connect("file:ohisell.db?mode=ro", uri=True)

print("\n── 점화 게이트 (닫혀 있어야 정상) ──")
# ★P1-1(적대 리뷰): 이 첫 블록만 try/except 밖에 있었다 — 「부품별 try/except」 약속을 자기가
#   어긴 것이다. 여기서 죽으면 아래 6개 섹션이 **전부** 안 나온다(「하나 실패 = 전체 침묵」의
#   정확한 재현). 실제 트리거가 있다: 이 저장소엔 4KB 미끼 DB가 있어 실행 위치가 틀리면
#   `no such table`로 죽는다.
try:
    r = c.execute("SELECT COUNT(*), SUM(auto_operate), MAX(updated_at) "
                  "FROM naver_campaign_settings").fetchone()
    # ★P1-2(적대 리뷰): 이전엔 `COUNT(DISTINCT optimizer)`로 «1종»만 찍었다. 그러면 그 1종이
    #   `none`(안전)인지 `ours`(엔진이 손을 댄다)인지 **구분이 안 된다** — 헤더는 "닫혀 있어야
    #   정상"이라 못 박아 놓고 정작 닫혔는지 판정할 유일한 값을 숨긴 것이다. `optimizer='none'`은
    #   `auto_operate`와 **독립적인** 킬스위치라(harness가 'ours'가 아니면 실행 자체를 거부)
    #   이 필드의 «값»이 곧 안전/위험을 가른다. 그래서 값을 «그대로» 센다.
    dist = c.execute("SELECT COALESCE(optimizer,'(NULL)'), COUNT(*) FROM naver_campaign_settings "
                     "GROUP BY 1 ORDER BY 2 DESC").fetchall()
    opt = " · ".join("%s×%s" % (k, v) for k, v in dist) or "(행 없음)"
    print("  settings %s행 · auto_operate합 %s · 최종갱신 %s" % r)
    print("  optimizer: %s" % opt)
    unsafe = [k for k, _ in dist if k not in ("none", "(NULL)")]
    print("  ⇒ %s" % ("★열림 — optimizer=%s 존재" % ",".join(unsafe) if unsafe
                      else "닫힘(전건 none) · auto_operate합 %s" % r[1]))
except Exception as e:
    print("  조회 실패: %s" % e)

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
