# 세션 인수인계: prod Basic Auth 전환 — **4/5단계 + rate limit. 비밀번호 결정이 5단계를 막고 있다**

> 저장 2026-08-13 10:1x KST · **⚠️ prod가 «중간 상태»다. 이 문서를 끝까지 읽고 이어갈 것.**
> 선행: `HANDOFF_test-census-and-adcost-trigger_20260812.md`(D-CPP-44·45·46)
> PR #295 병합(`scripts/zero_downtime_restart.sh`) · 나머지는 **prod 서버 직접 변경**(리포 밖)

---

## 0. 한 줄 — 지금 뭘 해야 하나

**5단계(IP 허용목록 해제) 전에 «비밀번호를 무엇으로 할지»가 먼저 정해져야 한다.**
5단계 승인은 이미 받았지만(2026-08-13 「승인 — 5단계 전부」), 그걸 하면 비밀번호가 **유일한
방어선**이 되므로 아래 §2-B의 선택 셋 중 하나가 결정돼야 안전하게 넘어갈 수 있다.

★**Jino가 `rlawlsdh5`로 바꾸겠다고 했으나 아직 적용 안 됐다**(모델이 평문 비밀번호를 대신
넣지 않고 명령만 드렸고, Jino가 아직 안 돌렸다). **현재 비밀번호는 여전히 무작위 24자다.**

★내가 4단계 후 «5단계 해도 되냐»고 **다시 물었고 Jino가 혼란스러워했다.** 이미 승인된 것을
다시 묻지 말 것 — 그게 이 인계가 남는 이유 중 하나다.

---

## 1. 지금 prod는 어떤 상태인가 (★가장 중요)

```
sellc.ohitech.co.kr  =  「허용 IP  또는  비밀번호」   (satisfy any)
```

- **아무것도 안 깨져 있다.** 기존 회선은 비밀번호 없이 그대로 통과하고,
  밖에서는 비밀번호로 들어올 수 있다. 안전한 중단 지점이다.
- 5단계를 하면 `satisfy any` + allowlist include 3줄이 빠지고 **비밀번호만** 남는다.

### 5단계 실행 방법
`/etc/nginx/sites-available/sellc.ohitech.co.kr`의 **3개 location**(`/api/`, `= /index.html`, `/`)에서
아래 2줄씩 제거 → `sudo nginx -t && sudo systemctl reload nginx`:
```
include /etc/nginx/snippets/ohisell-allowlist.conf;
satisfy any;
```
(`auth_basic` 2줄은 **남긴다**. 그게 유일한 방어선이 된다.)

### 5단계 후 라이브 합격기준
- 허용 IP였던 회선에서도 **비밀번호를 요구**한다(전엔 그냥 통과했다)
- 밖에서 비밀번호로 200 / 없이 401
- **데몬 수집 계속**(`data_stale` 0건) — 데몬 경로 30개는 regex location이라 영향 없어야 정상
- `safe_deploy.sh` 무중단 배포 성공
- `sudo certbot renew --dry-run` success

### 롤백
```
sudo cp /etc/nginx/sites-available/sellc.ohitech.co.kr.bak-basicauth-20260813-010021 \
        /etc/nginx/sites-available/sellc.ohitech.co.kr && sudo systemctl reload nginx
```
(daemon-paths도 같은 타임스탬프 백업 있음. 2026-07-16 원본 백업도 그대로 있다.)

---

## 2. 자격증명 — 어디에 있나

| 위치 | 내용 |
|---|---|
| `~/.ohisell_prod_auth` (600) | **`dgfrty:<24자>`** (ID를 jino→dgfrty로 변경, 서버 htpasswd도 갱신·옛 jino 제거) — 배포 스크립트가 읽고, **Jino가 폰 로그인할 때 여기서 본다** |
| `~/.ohisell_{wing,wing2,ad,rocket}_fetcher.json` (600) | `basic_auth_user`/`basic_auth_pass` |
| 서버 `/etc/nginx/.ohisell-htpasswd` (640 root:www-data) | `$apr1$` 해시 |

- **무작위 24자. 모델이 값을 보지 않았고 어디에도 출력하지 않았다.** 서버에는 stdin으로 전달해
  명령줄·프로세스 목록에도 안 남겼다.
- 일치 검증 완료: `htpasswd -vi` → `correct`. ID 변경 후 라이브 재확인: dgfrty→200 · jino→401 · 무인증→401.
- ⚠️`~/.ohisell_watchdog.json`은 **존재하지 않아** 건너뛰었다. `scheduler_watchdog_poll.py`는
  `~/.ohisell_ad_fetcher.json`으로 폴백하게 돼 있으므로 동작하지만, **미검증**이다.

### 2-B. ★비밀번호 결정 — 5단계의 전제조건 (미결)

Jino가 처음 `1234`를 요청했고 **모델이 거부**했다(5단계 후 유일 방어선이 되는데 그 뒤에
원가구조 706종·인증 없는 쓰기 112개·실제 네이버 입찰 변경 경로가 있고, 외부 봇이 이미
두드리고 있다). 이어 `rlawlsdh5`를 제시했고 모델은 «훨씬 낫다, 다만 한글 이름 자판 패턴이라
한국 대상 사전에 있다»고 알린 뒤 **명령만 전달**했다(평문 비밀번호는 대신 다루지 않는다).

그래서 **속도 제한을 먼저 걸었다**(아래 §3-B). 그런데 실측해 보니 **속도 제한만으로는 부족하다**:

| | 지금(속도 제한만) | fail2ban 추가 시 |
|---|---:|---:|
| 하루 시도 가능 | 약 170만 회 | 약 120회 |
| `rlawlsdh5` 안전? | ❌ 며칠이면 뚫림 | ✅ 사실상 불가능 |

**선택 셋 (Jino 결정 대기)**
1. **fail2ban 설치**(nginx 401 로그 기반, 실패 5회→1시간 차단) → `rlawlsdh5`로도 안전 → 5단계 진행.
   ⚠️prod에 **새 서비스 설치**라 모델이 임의로 안 했다. 승인만 있으면 실행 가능.
2. **더 긴 비밀번호**(예: `rlawlsdh5-ohisell`) → 그것만으로 충분 → 5단계 진행.
3. **5단계를 안 함** → IP 허용목록을 남기면 지금도 이중 방어라 안전. 대신 폰 접속은 못 함.

★어느 쪽이든 **비밀번호 적용은 Jino가 직접** 명령을 돌려야 한다(모델은 평문을 안 다룬다).
그 명령은 §2-C에 있다.

### 2-C. 비밀번호 변경 명령 (Jino가 실행 · 히스토리에 안 남음)

```bash
read -s -p "새 비밀번호: " P; echo; printf 'dgfrty:%s\n' "$P" > ~/.ohisell_prod_auth; chmod 600 ~/.ohisell_prod_auth; P="$P" python3 -c "
import json,pathlib,os
p=os.environ['P']
for f in ['.ohisell_wing_fetcher.json','.ohisell_wing2_fetcher.json','.ohisell_ad_fetcher.json','.ohisell_rocket_fetcher.json']:
    fp=pathlib.Path.home()/f
    if not fp.is_file(): continue
    d=json.loads(fp.read_text()); d['basic_auth_pass']=p
    fp.write_text(json.dumps(d,ensure_ascii=False,indent=2)); fp.chmod(0o600); print('갱신:',f)
"; printf '%s' "$P" | ssh sellc.ohitech.co.kr 'sudo htpasswd -i /etc/nginx/.ohisell-htpasswd dgfrty' && echo "서버 갱신 완료"; unset P
```
바꾼 뒤 데몬·배포가 붙는지 §5 방식으로 재확인할 것.

---

## 3. 이번에 고친 클라이언트 (Basic Auth 배선)

전부 **「설정에 키가 없으면 지금과 동일 동작」** — 그래서 순서대로 진행할 수 있었다.

| 파일 | 줄 |
|---|---|
| `~/.ohisell/tools/wing_browser_fetcher.py` | 1271, 1293 |
| `~/.ohisell/tools/rocket_supplier_fetcher.py` | 2438 |
| `~/.ohisell/tools/ad_cost_browser_fetcher.py` | 875 |
| `~/.ohisell/tools/scheduler_watchdog_poll.py` | 201 (+ `_load_cfg` 82-107 확장) |
| `scripts/zero_downtime_restart.sh` | 209 (PR #295 병합) |

★백업: 각 `.py`에 `.bak-basicauth` 있음. **리포 밖 파일이라 git에 없다.**
### 3-B. 속도 제한 (2026-08-13 추가)

`/etc/nginx/conf.d/ohisell-ratelimit.conf` 신설 — `limit_req_zone ... rate=20r/s`,
3개 location에 `limit_req zone=ohisell_auth burst=50 nodelay;`.
**데몬 regex location에는 안 걸었다**(별도 location, 상속 없음).

라이브 확인: 정상 사용 40/40 통과 ✅ · 병렬 100회 중 **29회 503 차단** ✅ · 데몬 폴링 성공 ✅
★단 **이건 속도 제한이지 방벽이 아니다** — §2-B 표 참조.

★서브에이전트가 내 표의 오류를 잡았다 — 내가 「6곳」이라 했는데 실제 **5곳**이다
(`rocket_supplier_fetcher`에 `ad-cost/refresh-status` 호출은 없다). 없는 걸 만들지 않고 보고한 게 옳았다.

---

## 4. ★오늘 발견한 실제 보안 구멍 (닫음)

`ohisell-daemon-paths.conf`의 regex location이 IP 허용목록을 **우회**하는데, 그 36개 중
**6개는 앱 인증도 없었다.** 허용목록 밖 IP(대만)에서 **200**이 나오는 것을 라이브로 확인했다:

```
rocket/refresh-status · ad-cost/refresh-status ·
wing/vendor-summary/refresh-status · wing/rg-settlement/refresh-status   → 200 ⚠️
(+ rocket/request-refresh · rocket/ad-cost/request-refresh = POST, 익명 잡 트리거 가능)
대조군 /api/health · /  → 403 ✅
```

→ **6개를 예외 목록에서 제거**(36→30). 지금은 전부 403/401. 돈·원가 데이터는 안 나왔고
노출된 건 수집 상태 플래그였다. 나머지 30개는 `X-Ingest-Token`(compare_digest)이 제대로 지킨다.

---

## 5. 4단계 라이브 합격 (전부 실측)

| 확인 | 결과 |
|---|---|
| 밖에서 비밀번호 없이 (`/`·`/api/health`·구멍 경로) | **401** |
| 밖에서 비밀번호로 | **200** |
| 데몬 경로 30개 (`ad-cost/ingest`) | **422** = nginx 통과·앱 도달 |
| **실제 데몬 코드로 prod 폴링** (`_prod_refresh_status`) | **성공** (wing·ad_cost 둘 다) |
| **무중단 배포 프로브** | **성공** pid=639059 ← 어제부터 막혀 있던 게 풀림 |
| `certbot renew --dry-run` | **success** |
| `data_stale` | **0건** |
| D-CPP-46 광고비 괴리 | `ok · 0.9395` 유지 |

---

## 6. ★인계 문서 정정 (다음 세션이 속지 않게)

- **`sellc-prod-was-publicly-exposed.md`의 「비밀번호는 안 씀(최종)」은 무효다.**
  Jino가 2026-08-13에 뒤집었다: *"B — Basic Auth 붙이고 IP 해제"*. 그 파일의 「먼저 제안 금지」
  경고도 이제 해당 없음(요청받아 착수했다).
- **인증서 만료는 2026-10-29**다(메모리의 2026-08-30은 낡음). 갱신 잘 돈다.
- `daemon-paths.conf` include가 vhost에 **2번** 걸려 있다(regex라 동작 무해, 미정리).

---

## 7. 이월 (스코프 밖 — 고치지 않고 적음)

1. **`COUPANG_OHITECH_AD` 수집이 3.5일째 멈춤** (마지막 성공 2026-08-09 21:56). 오늘 임계값을
   넘어 `cookies_stale`로 떴다. **내 변경과 무관**(3일 전부터). 오하이테크 광고비 축이라 손익 영향 있음.
2. `~/.ohisell_watchdog.json` 부재 → 워치독 폴러의 자격증명 폴백 **미검증**.
3. `rocket/ad-cost`(bare)는 라우트가 없는 **죽은 항목**이 예외 목록에 남아 있다(404, 구멍 아님).
4. 앱 레벨 인증은 여전히 0 — 비밀번호가 새면 2026-07-17 상태와 같아진다.
5. Postgres 5432가 `0.0.0.0/0` 노출(다른 앱 DB, scram 비번은 걸림).
6. 어제 것: 임계 1.1의 대가(약 10% 누락 못 봄) · `--testNamePattern` 가드 구멍 ·
   옵션축↔계정축 47,337원 · PA 이전 창 90,841원 · WhatsApp 잔여 고아 38GB ·
   **스냅샷이 206GB 붙잡고 있음**(`sudo tmutil thinlocalsnapshots /System/Volumes/Data 300000000000 4`) ·
   휴지통 5.3GB · Google Drive `Ohi` 303GB 온라인 전용 전환(Jino) · 워크트리 4곳 node_modules

---

## 8. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_prod-basic-auth-4of5_20260813.md 읽고 이어서 하자.

prod는 「허용 IP 또는 비밀번호」(satisfy any) + 속도 제한 상태다 — 안전하지만 미완이다.
★막혀 있는 건 5단계가 아니라 **비밀번호 결정**이다(§2-B 선택 셋). 5단계 승인은 이미 받았으니
그것만 다시 묻지 말고, §2-B 중 무엇으로 할지만 확인하고 진행할 것.

- fail2ban을 고르면: 설치 + nginx-http-auth jail(실패 5회→1시간 차단) → 그다음 5단계
- 더 긴 비밀번호를 고르면: §2-C 명령은 **Jino가 직접** 돌린다(모델은 평문 비밀번호를 안 다룬다)
- 5단계 스킵을 고르면: 그대로 두고 문서만 정리

5단계 실행법·합격기준·롤백은 §1. 끝나면 §5 표 방식으로 라이브 재확인
(특히 «실제 데몬 코드로 prod 폴링»과 «무중단 배포 프로브»).

그다음 §7 이월 1번(COUPANG_OHITECH_AD 3.5일 정체)을 볼 것 — 손익에 영향이 있다.
```
