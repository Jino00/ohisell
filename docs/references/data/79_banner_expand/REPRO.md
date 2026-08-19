# 배너 접기/펼치기 실렌더 재현 절차 (D-NAO-205)

> ★이 문서가 있는 이유: D-NAO-204 완료 QA가 「라이브 화면」을 **독립 재현하지 못해** 부분달성으로
> 판정했다. 확인만 하고 산출물을 안 남긴 대가였다. 이번엔 재현 가능하게 남긴다.
> **prod 원장은 건드리지 않는다** — 별도 로컬 DB를 쓴다.

## 1. 로컬 DB 시드 (부분수집 1건)
```bash
rm -f /tmp/dnao205_demo.db
cd backend && DATABASE_URL="sqlite:////tmp/dnao205_demo.db" python3 - <<'PY'
import sys; sys.path.insert(0, ".")
from datetime import timedelta
from app.database import Base, engine, SessionLocal
from app.models import Channel, SyncLog
from app.services.sync_service import PARTIAL_SYNC_MARKER
from app.utils.kst import kst_now
Base.metadata.create_all(engine)
db = SessionLocal(); now = kst_now()
db.add(Channel(id=6, name="네이버 스마트스토어", code="NAVER", platform="naver",
               api_type="oauth2_bcrypt", api_config_key="naver"))
db.add(SyncLog(channel_id=6, sync_type="orders", status="success", records_synced=336,
               error_message=f"{PARTIAL_SYNC_MARKER} 변경상태 스윕 미완주 1일: 2026-08-18",
               started_at=now - timedelta(hours=1), completed_at=now - timedelta(hours=1)))
db.commit(); db.close()
PY
```

## 2. 백엔드 + 배포 번들 서빙
```bash
cd backend && DATABASE_URL="sqlite:////tmp/dnao205_demo.db" \
  python3 -m uvicorn app.main:app --host 127.0.0.1 --port 4600 &
cd frontend && npm run build          # dist 생성
# dist를 서빙하고 /api/* 를 :4600으로 프록시하는 정적 서버를 4599에 띄운다
```

## 3. 관측 (브라우저 콘솔)
```js
const wrap = [...document.querySelectorAll('div')].find(d => d.textContent.startsWith('⚠️ 파이프라인 경고'));
const b = wrap.querySelector('button');
({ header: wrap.querySelector('span.font-semibold')?.textContent,
   toggle: b?.textContent, ariaExpanded: b?.getAttribute('aria-expanded'),
   items: [...wrap.querySelectorAll('li')].map(li => li.textContent) })
```

## 4. 2026-08-19 16:2x 실관측 결과
| 상태 | header | toggle | aria-expanded | 보이는 항목 |
|---|---|---|---|---|
| 접힘(기본) | `⚠️ 파이프라인 경고` | `외 10건 ▾` | `false` | 1건, **잘리지 않음**(`scrollWidth > clientWidth` = false) |
| 펼침 | `⚠️ 파이프라인 경고 (11건)` | `접기 ▴` | `true` | **11건 전부** |

상세: `banner_dom_collapsed.json` · `banner_dom_expanded.json` · 원 응답 `health_payload.json`

## 5. 스크린샷 (실렌더, headless Chrome)
> ★**배포된 번들 `index-D8xSLZ6i.js`로 재촬영**(2026-08-19 19:09). 첫 촬영본은 적대 리뷰 P1 수정 «전» 빌드라 폐기했다 — 증거가 배포 코드와 어긋나면 증거가 아니다.
- `banner_collapsed.png` — 접힘 기본. `⚠️ 파이프라인 경고  RG 정산비용(오픽스)이 net_profit에서 누락 중  외 11건 ▾`
- `banner_expanded.png` — 펼침. `⚠️ 파이프라인 경고 (12건)  접기 ▴` + 전건 목록(`max-h-64` 스크롤)

캡처 명령:
```bash
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" --headless --disable-gpu --hide-scrollbars --virtual-time-budget=6000 \
  --window-size=1280,320 --screenshot=banner_collapsed.png "http://127.0.0.1:4599/"
```
펼침은 같은 오리진 iframe에서 토글을 눌러 캡처했다(`expand.html`, 캡처 후 삭제).
★경고 건수는 로컬 DB 상태에 따라 달라진다(11건/12건 등) — **건수가 아니라 «전건이 보이는가»가 관측 대상**이다.

★**종전 결함**: 11건을 ` · `로 이어 한 줄에 넣고 `truncate` → 화면 폭을 넘는 순간 뒤가 통째로 안 보였다.
호버 `title`에는 있었지만 «호버해야 보이는 경고»는 배너가 아니다.
