#!/usr/bin/env bash
# 로컬 런타임 설치/갱신 — 데몬(adcost·wing·rocket)을 iCloud Drive 밖에서 돌린다.
#
# 왜: 소스/.venv가 iCloud(com~apple~CloudDocs)에 있으면 iCloud가 파일을 클라우드로
#     추방(dataless)해, launchd 데몬이 playwright import 시 'OSError: Resource deadlock
#     avoided'로 크래시 루프에 빠진다. 데몬이 실행하는 Python 런타임과 스크립트는
#     iCloud가 절대 추방 못 하는 로컬 디스크(~/.ohisell)에 둔다.
#
# 멱등: 여러 번 실행해도 안전. 페처 .py를 iCloud 소스에서 로컬로 복사 + 의존성 재설치.
# 재배포: iCloud의 tools/*.py를 수정한 뒤 이 스크립트를 다시 실행하면 로컬에 반영된다.
#
# ★CAS(compare-and-swap) 가드 — 2026-08-03 실사고로 추가 ─────────────────────
#   사고: 13:33 세션 A가 wing_browser_fetcher.py 수정을 ~/.ohisell/tools/에 배포했는데,
#   13:34:41 / 13:45:03 / 14:01:47 세 번에 걸쳐 baseline으로 되돌아갔다. 범인은 이 스크립트다 —
#   다른 워크트리(PR #175 계열)에서 실행된 install_local_runtime.sh가 자기 tools/ 내용으로
#   **6개 파일 전부를 무조건 cp -f** 했고, 그 세션은 wing 페처를 건드릴 의도가 전혀 없었다.
#   로그도 경고도 없어서, 배포한 쪽은 "배포했다"고 믿고 라이브 검증을 하는데 실제로 도는 건
#   구버전이었다.
#
#   prod 서버 배포엔 이미 같은 사고(2026-07-17 qi 수집 clobber)를 구조로 막는
#   scripts/safe_deploy.sh 가 있다(D-NAO-49). Mac 페처 경로엔 그 가드가 없었다 — 여기서 이식한다.
#
#   ★규칙(safe_deploy.sh와 동일한 원리): 덮어쓰려는 ~/.ohisell/tools/<f> 의 **현재 내용**이
#     아래 셋 중 하나여야 복사한다.
#       ① 지금 배포하려는 내용과 동일          → 할 일 없음
#       ② 이 워크트리가 직전에 배포한 버전     → .deploy_manifest.json 의 blob+source 일치
#       ③ 내 브랜치 역사(git rev-list HEAD)에 존재하는 그 파일의 구버전 → 내가 앞서 있다
#     셋 다 아니면 = **내 브랜치가 한 번도 본 적 없는 내용** = 다른 세션의 배포본 →
#     그 파일만 건너뛰고 경고한다(전체 실행은 계속 — 나머지 파일은 정상 배포).
#
#   ★②가 필요한 이유: 이 경로는 prod와 달리 "커밋 안 된 코드로 라이브 계측"이 정상 워크플로다
#     (실제로 08-03 사고의 A 세션이 그랬다). ②가 없으면 자기가 방금 배포한 미커밋 코드를
#     다음 실행에서 자기가 '외부 코드'로 보고 거부한다 = 쓰이지 않는 게이트가 된다.
#
#   ★매니페스트: 배포할 때마다 ~/.ohisell/tools/.deploy_manifest.json 에 파일별
#     blob·워크트리·브랜치·커밋·시각(KST)을 남긴다. "지금 도는 게 누구 코드냐"를 즉시 답한다.
#
# 사용:
#   tools/install_local_runtime.sh                     # 평소(CAS 가드 켜짐)
#   tools/install_local_runtime.sh --dry-run           # 검사만 — 지금 누구 코드가 깔려 있나
#   tools/install_local_runtime.sh --files-only        # 파일만 복사(venv·의존성·launchd 생략)
#   tools/install_local_runtime.sh --force             # 전 파일 강제 덮어쓰기(백업 남김)
#   tools/install_local_runtime.sh --force=wing_browser_fetcher.py   # 그 파일만 강제
#
# 환경변수(테스트용 오버라이드 — 평소엔 건드리지 말 것):
#   OHISELL_LOCAL_HOME=<dir>   기본 $HOME/.ohisell
set -euo pipefail

REPO_TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # iCloud 소스 tools/
LOCAL_HOME="${OHISELL_LOCAL_HOME:-$HOME/.ohisell}"
LOCAL_VENV="$LOCAL_HOME/venv"
LOCAL_TOOLS="$LOCAL_HOME/tools"
MANIFEST="$LOCAL_TOOLS/.deploy_manifest.json"
PY_VERSION_PIN="3.14"
PLAYWRIGHT_PIN="1.60.0"
REQUESTS_PIN="2.33.1"

# ── 인자 ────────────────────────────────────────────────────────────
FORCE_ALL=0; FORCE_LIST=""; DRY_RUN=0; FILES_ONLY=0
for a in "$@"; do
  case "$a" in
    --force)      FORCE_ALL=1 ;;
    --force=*)    FORCE_LIST="$FORCE_LIST $(printf '%s' "${a#--force=}" | tr ',' ' ')" ;;
    --dry-run)    DRY_RUN=1 ;;
    --files-only) FILES_ONLY=1 ;;
    -h|--help)    sed -n '2,/^set -euo/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "❌ 알 수 없는 인자: $a  (--help 참조)" >&2; exit 2 ;;
  esac
done

echo "==> 로컬 런타임 경로: $LOCAL_HOME"
[ "$DRY_RUN" = 1 ] && echo "==> (--dry-run: 검사만 하고 아무것도 복사·설치하지 않습니다)"
mkdir -p "$LOCAL_TOOLS"

PY_BIN="$(command -v python${PY_VERSION_PIN} || command -v python3)"
echo "==> Python: $PY_BIN ($($PY_BIN --version))"

# ── CAS 가드 구현 ───────────────────────────────────────────────────
# 소스 워크트리 신원. git이 없으면(저장소 밖 사본) CAS는 판정 근거가 없다 → 경고 후 통과시킨다.
GIT_OK=1
command -v git >/dev/null 2>&1 || GIT_OK=0
if [ "$GIT_OK" = 1 ] && ! git -C "$REPO_TOOLS" rev-parse --git-dir >/dev/null 2>&1; then GIT_OK=0; fi
SRC_ROOT=""; SRC_BRANCH=""; SRC_COMMIT=""; SRC_PREFIX=""
if [ "$GIT_OK" = 1 ]; then
  SRC_ROOT="$(git -C "$REPO_TOOLS" rev-parse --show-toplevel)"
  SRC_BRANCH="$(git -C "$REPO_TOOLS" branch --show-current)"; [ -n "$SRC_BRANCH" ] || SRC_BRANCH="(detached)"
  SRC_COMMIT="$(git -C "$REPO_TOOLS" rev-parse --short HEAD)"
  SRC_PREFIX="$(git -C "$REPO_TOOLS" rev-parse --show-prefix)"   # 예: "tools/"
  echo "==> 소스: $SRC_BRANCH @ $SRC_COMMIT  ($SRC_ROOT)"
else
  echo "==> ⚠️ git 저장소가 아님 → CAS 가드 비활성(다른 세션 배포본을 덮을 수 있습니다)"
fi
TS_UTC="$(date -u +%FT%TZ)"
TS_KST="$(TZ=Asia/Seoul date '+%Y-%m-%d %H:%M:%S KST')"

# ★git은 반드시 **저장소 최상위**에서 돌린다(-C tools/ 아님). pathspec은 cwd 기준으로 해석되므로
#   `git -C tools rev-list HEAD -- tools/x.py` 는 tools/tools/x.py 를 찾아 **항상 0건**을 낸다
#   = ③(내 브랜치 역사) 검사가 조용히 무력화되고 모든 파일이 '외부 코드'로 거부된다.
#   (2026-08-03 이 가드의 첫 라이브 테스트에서 실제로 그렇게 나왔다 — scheduler_watchdog_poll.py가
#    내 HEAD 그대로인데도 거부됨.)
g() { git -C "${SRC_ROOT:-$REPO_TOOLS}" "$@"; }

is_forced() {   # $1=파일명
  [ "$FORCE_ALL" = 1 ] && return 0
  local x
  for x in $FORCE_LIST; do [ "$x" = "$1" ] && return 0; done
  return 1
}

manifest_get() {   # $1=파일명 → "blob<TAB>source<TAB>branch<TAB>commit<TAB>ts_kst" (없으면 빈 출력)
  [ -f "$MANIFEST" ] || return 0
  "$PY_BIN" - "$MANIFEST" "$1" <<'PY' 2>/dev/null || true
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    e = (d.get("files") or {}).get(sys.argv[2])
except Exception:
    e = None
if isinstance(e, dict):
    print("\t".join(str(e.get(k, "")) for k in ("blob", "source", "branch", "commit", "ts_kst")))
PY
}

manifest_set() {   # $1=파일명 $2=blob $3=verdict $4=dirty(0/1)
  [ "$DRY_RUN" = 1 ] && return 0
  # 원자적 교체(같은 디렉터리 임시파일 → os.replace). 동시 실행 시 마지막 쓰기가 이긴다 —
  # 매니페스트는 참고용이고, 판정의 진실은 항상 파일 내용 해시다.
  "$PY_BIN" - "$MANIFEST" "$1" "$2" "$3" "${SRC_ROOT:-?}" "${SRC_BRANCH:-?}" "${SRC_COMMIT:-?}" "$4" "$TS_UTC" "$TS_KST" <<'PY' 2>/dev/null || true
import json, os, sys, tempfile
mf, f, blob, verdict, src, branch, commit, dirty, ts, ts_kst = sys.argv[1:11]
try:
    d = json.load(open(mf))
    if not isinstance(d, dict):
        raise ValueError
except Exception:
    d = {}
d.setdefault("files", {})
d["files"][f] = {"blob": blob, "verdict": verdict, "source": src, "branch": branch,
                 "commit": commit, "dirty": dirty == "1", "ts": ts, "ts_kst": ts_kst}
d["updated_at"] = ts
d["updated_at_kst"] = ts_kst
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(mf) or ".")
with os.fdopen(fd, "w") as fh:
    json.dump(d, fh, ensure_ascii=False, indent=2, sort_keys=True)
os.replace(tmp, mf)
PY
}

origin_hint() {   # $1=파일명 $2=설치본 blob — 출처를 최대한 알아낸다(진단용)
  local f="$1" blob="$2" mline mblob br c b
  mline="$(manifest_get "$f")"
  mblob="$(printf '%s' "$mline" | cut -f1)"
  if [ -n "$mblob" ] && [ "$mblob" = "$blob" ]; then
    echo "      출처(매니페스트): $(printf '%s' "$mline" | cut -f3) @ $(printf '%s' "$mline" | cut -f4)"
    echo "                        $(printf '%s' "$mline" | cut -f2)"
    echo "                        배포 시각 $(printf '%s' "$mline" | cut -f5)"
    return 0
  fi
  # 매니페스트에 없으면 로컬 브랜치 **끝점**만 훑는다(전 역사 스캔은 iCloud에서 너무 느리다).
  if [ "$GIT_OK" = 1 ]; then
    while read -r br; do
      c="$(g rev-list -1 "$br" -- "$SRC_PREFIX$f" 2>/dev/null || true)"
      [ -n "$c" ] || continue
      b="$(g rev-parse -q --verify "$c:$SRC_PREFIX$f" 2>/dev/null || true)"
      if [ "$b" = "$blob" ]; then
        echo "      출처(브랜치 끝점 일치): ${br#refs/heads/} @ $(printf '%s' "$c" | cut -c1-9)"
        return 0
      fi
    done < <(g for-each-ref --format='%(refname)' refs/heads 2>/dev/null)
  fi
  echo "      출처 불명 — 어느 브랜치 끝점에도 매니페스트에도 없음(= 커밋 안 된 다른 세션의 배포본)"
}

SKIPPED_FILES=""
# cas_install <파일명> : 0=복사됨/동일, 1=CAS 거부로 건너뜀
cas_install() {
  local f="$1"
  local src="$REPO_TOOLS/$f" dst="$LOCAL_TOOLS/$f" rel="$SRC_PREFIX$f"
  local local_blob inst_blob verdict="" mline mblob msrc c b dirty=0 bak

  if [ "$GIT_OK" = 0 ]; then
    [ "$DRY_RUN" = 1 ] || cp -f "$src" "$dst"
    echo "    ⚠️ $f (CAS 비활성 — 무조건 복사)"
    return 0
  fi

  local_blob="$(g hash-object -- "$src")"
  g diff --quiet HEAD -- "$rel" 2>/dev/null || dirty=1

  if [ ! -f "$dst" ]; then
    verdict="new"
  else
    inst_blob="$(g hash-object -- "$dst")"
    if [ "$inst_blob" = "$local_blob" ]; then
      verdict="same"
    else
      # ② 이 워크트리가 직전에 배포한 버전인가
      mline="$(manifest_get "$f")"
      mblob="$(printf '%s' "$mline" | cut -f1)"
      msrc="$(printf '%s' "$mline" | cut -f2)"
      if [ -n "$mblob" ] && [ "$mblob" = "$inst_blob" ] && [ "$msrc" = "$SRC_ROOT" ]; then
        verdict="mine"
      else
        # ③ 내 브랜치 역사에 있는 그 파일의 구버전인가 (safe_deploy.sh와 동일한 핵심 CAS)
        while read -r c; do
          b="$(g rev-parse -q --verify "$c:$rel" 2>/dev/null || true)"
          if [ "$b" = "$inst_blob" ]; then verdict="ancestor"; break; fi
        done < <(g rev-list HEAD -- "$rel" 2>/dev/null)
        [ -n "$verdict" ] || verdict="foreign"
      fi
    fi
  fi

  case "$verdict" in
    same)
      echo "    ⏭  $f (동일 — 이미 배포됨)"
      # mtime을 건드리지 않는다: "언제 누가 덮었나"의 유일한 포렌식 신호다(08-03 사고 추적 근거).
      # 매니페스트 항목이 아예 없을 때만 기록해 기존 설치본을 추적 대상에 편입시킨다.
      [ -n "$(manifest_get "$f")" ] || manifest_set "$f" "$local_blob" "$verdict" "$dirty"
      return 0 ;;
    new)      echo "    🆕 $f (신규 설치)" ;;
    mine)     echo "    ✅ $f (CAS 통과 — 이 워크트리가 직전에 배포한 버전 위에 덮음)" ;;
    ancestor) echo "    ✅ $f (CAS 통과 — 설치본이 내 브랜치 역사의 구버전)" ;;
    foreign)
      if is_forced "$f"; then
        if [ "$DRY_RUN" = 1 ]; then
          echo "    ⚠️ $f — CAS 실패지만 --force (dry-run이라 덮지 않음)"
          origin_hint "$f" "$inst_blob"
          return 0
        fi
        bak="$dst.clobbered-$(TZ=Asia/Seoul date +%Y%m%d_%H%M%S)"
        cp -p "$dst" "$bak"
        echo "    ⚠️ $f — CAS 실패했지만 --force 로 덮어씀"
        origin_hint "$f" "$inst_blob"
        echo "      덮인 내용 백업: $bak"
        cp -f "$src" "$dst"
        manifest_set "$f" "$local_blob" "force-clobber" "$dirty"
        return 0
      fi
      echo "" >&2
      echo "    ★ 건너뜀: $f" >&2
      echo "      설치본(blob ${inst_blob:0:12}…)이 이 브랜치 역사에 없습니다 = 다른 세션의 배포본." >&2
      echo "      지금 덮으면 그 세션은 '배포했다'고 믿은 채 구버전을 라이브 검증하게 됩니다" >&2
      echo "      (2026-08-03 wing 페처 3회 되돌림 사고와 동일 패턴)." >&2
      origin_hint "$f" "$inst_blob" >&2
      echo "      → 그 브랜치를 병합한 뒤 재실행하거나, 의도적이면:" >&2
      echo "        tools/install_local_runtime.sh --force=$f" >&2
      echo "" >&2
      SKIPPED_FILES="$SKIPPED_FILES $f"
      return 1 ;;
  esac

  if [ "$DRY_RUN" = 1 ]; then return 0; fi
  cp -f "$src" "$dst"
  manifest_set "$f" "$local_blob" "$verdict" "$dirty"
  return 0
}

cas_skipped() { case " $SKIPPED_FILES " in *" $1 "*) return 0 ;; esac; return 1; }

if [ "$FILES_ONLY" = 0 ] && [ "$DRY_RUN" = 0 ]; then
  # 1. 로컬 venv (없으면 생성). homebrew python3.14 = 로컬 디스크.
  if [ ! -x "$LOCAL_VENV/bin/python3" ]; then
    echo "==> venv 생성: $LOCAL_VENV"
    "$PY_BIN" -m venv "$LOCAL_VENV"
  fi

  # 2. 의존성 (페처는 requests + playwright만 import). 버전 핀 = 캐시된 브라우저 호환.
  echo "==> 의존성 설치 (requests==$REQUESTS_PIN, playwright==$PLAYWRIGHT_PIN)"
  "$LOCAL_VENV/bin/pip" install --quiet --upgrade pip
  "$LOCAL_VENV/bin/pip" install --quiet "requests==$REQUESTS_PIN" "playwright==$PLAYWRIGHT_PIN"

  # 3. playwright 브라우저 — 보통 ~/Library/Caches/ms-playwright(로컬)에 이미 있음.
  #    핀 버전과 캐시가 일치하면 즉시 종료, 불일치면 해당 브라우저만 다운로드.
  echo "==> playwright chromium 확인/설치"
  "$LOCAL_VENV/bin/playwright" install chromium >/dev/null 2>&1 || true
fi

LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$LAUNCH_AGENTS"
UID_NUM="$(id -u)"

# ★2026-07-27: Chrome 상주 supervisor 잡(wing-chrome·ohitech-chrome·rocket-chrome)은 폐기됐다.
#   창을 닫아도 launchd가 Chrome을 되살리던 원인 → 이제 poll 데몬이 fetch 때만 Chrome을 띄우고
#   닫는다. 남는 잡은 poll 데몬 4개뿐(adcost·wing·rocket·ohitech-ad).
#
# ★구 supervisor 잡 자동 제거(codex R1 P1#1): 설치 목록에서 빼기만 하면 **이미 로드된 구 잡은
#   그대로 살아남는다** — 실행 중인 파이썬 프로세스는 아래 cp가 스크립트를 덮어써도 교체되지 않아
#   구 코드가 계속 Chrome을 상주시킨다(= 이번 전환의 목적이 통째로 무효화). 문서 안내로는 세 번
#   못 막았다(원칙: 부탁이 아니라 구조로) → 여기서 bootout·plist 삭제까지 수행하고, 잔존하면
#   설치를 실패로 끝낸다. (구 plist 소멸을 재확인한 뒤 2026-07-27 no-op 스텁 chrome-supervise는
#   페처 코드에서 완전히 제거했다 — 이 bootout·plist 삭제 로직만이 유일한 방어선이다.)
if [ "$FILES_ONLY" = 0 ] && [ "$DRY_RUN" = 0 ]; then
  _deprecated_left=0
  for _dep in wing-chrome ohitech-chrome rocket-chrome; do
    _dep_plist="$LAUNCH_AGENTS/com.ohisell.$_dep.plist"
    _dep_loaded=0
    launchctl print "gui/$UID_NUM/com.ohisell.$_dep" >/dev/null 2>&1 && _dep_loaded=1
    if [ "$_dep_loaded" = "1" ]; then
      launchctl bootout "gui/$UID_NUM/com.ohisell.$_dep" 2>/dev/null || true
      for _i in $(seq 1 25); do
        launchctl print "gui/$UID_NUM/com.ohisell.$_dep" >/dev/null 2>&1 || break
        sleep 1
      done
      if launchctl print "gui/$UID_NUM/com.ohisell.$_dep" >/dev/null 2>&1; then
        echo "    ⚠️ 구 supervisor com.ohisell.$_dep bootout 실패 — 상주 Chrome이 남는다."
        _deprecated_left=1
        continue
      fi
      echo "    구 supervisor com.ohisell.$_dep → bootout 완료"
    fi
    if [ -f "$_dep_plist" ]; then
      rm -f "$_dep_plist"
      echo "    구 plist 삭제: $_dep_plist"
    fi
  done
  if [ "$_deprecated_left" = "1" ]; then
    echo "==> ❌ 설치 중단: 구 Chrome supervisor 잡을 제거하지 못했습니다."
    echo "    수동: launchctl bootout gui/$UID_NUM/com.ohisell.<label> 후 재실행"
    exit 1
  fi
fi

# 4. 파일 배포(CAS 가드) — 공용 모듈 → 페처 → 워치독 순.
# ★공용 모듈이 **먼저**여야 한다(2026-08-03): 페처들이 `import coupang_auth` 한다. 이게 없으면
#   데몬이 import 실패로 즉사하고 launchd가 무한 재기동한다 — LESSONS #54와 똑같은 기전
#   (의존 모듈을 빼고 그걸 import하는 파일만 배포해 크래시 루프 265회).
#   순서가 뒤면 새 페처가 잠시 구 모듈 없이 존재한다.
echo "==> 파일 배포 (CAS 가드)"
for f in coupang_auth.py \
         ad_cost_browser_fetcher.py wing_browser_fetcher.py \
         rocket_supplier_fetcher.py ohitech_ad_fetcher.py \
         scheduler_watchdog_poll.py; do
  if [ ! -f "$REPO_TOOLS/$f" ]; then
    case "$f" in
      coupang_auth.py)
        echo "    ❌ 공용 모듈 없음: $REPO_TOOLS/$f — 중단(페처가 import 실패로 죽는다)"; exit 1 ;;
      *) echo "    (소스 없음, 건너뜀: $f)"; continue ;;
    esac
  fi
  cas_install "$f" || true
done

# 5. plist 렌더/설치/reload.
# macOS 기본 bash는 3.2 → 연관배열(declare -A) 미지원. name:script 쌍을 공백 구분 문자열로.
if [ "$FILES_ONLY" = 0 ] && [ "$DRY_RUN" = 0 ]; then
echo "==> plist 설치"
# ★wing2(2026-07-27, codex R1 [P1]): WING2=오하이테크 인스턴스. 같은 스크립트를 env로 분리 기동한다.
#   이 loop에 없으면 plist의 __PYTHON__/__SCRIPT__가 치환되지 않은 채로만 존재해 데몬이 영영 안 뜬다
#   (= WING2 RG 갱신 요청이 아무도 claim하지 않는다 = 이번 스프린트가 라이브에서 무효).
for pair in "adcost:ad_cost_browser_fetcher.py" "wing:wing_browser_fetcher.py" "wing2:wing_browser_fetcher.py" "rocket:rocket_supplier_fetcher.py" "ohitech-ad:ohitech_ad_fetcher.py"; do
  name="${pair%%:*}"
  script="${pair##*:}"
  # ★설정 없는 Mac에 wing2를 올리면 poll이 fail-fast(exit 2) → KeepAlive가 10초마다 되살리는
  #   크래시 루프가 된다. 계정 설정 파일이 생긴 뒤에 설치한다(재실행하면 그때 붙는다 — 멱등).
  if [ "$name" = "wing2" ] && [ ! -f "$HOME/.ohisell_wing2_fetcher.json" ]; then
    # ★그냥 건너뛰지 않고 '내린다'(codex R2 [P2]): 예전에 수동 cp/load 했거나 나중에 설정을
    #   지운 Mac에서는 건너뛰기만 하면 크래시 루프가 그대로 살아남는다. 설정 없음 = 이 잡은
    #   존재하면 안 되는 상태이므로 bootout + plist 삭제까지가 '설정 없음'의 올바른 수렴점.
    # ★내렸다고 '말하지' 말고 내려간 걸 '확인'한다(codex R3 [P2]): bootout은 실패해도 조용하다
    #   (2>/dev/null || true). 검증 없이 성공을 출력하고 plist까지 지우면 크래시 루프는 그대로인데
    #   화면만 깨끗해지고, 파일이 없어져 다음 실행이 문제를 볼 근거마저 사라진다. 위 구 supervisor
    #   정리와 같은 패턴(대기·재확인·실패 시 설치 중단)을 쓴다.
    if launchctl print "gui/$UID_NUM/com.ohisell.wing2" >/dev/null 2>&1; then
      launchctl bootout "gui/$UID_NUM/com.ohisell.wing2" 2>/dev/null || true
      for _i in $(seq 1 25); do
        launchctl print "gui/$UID_NUM/com.ohisell.wing2" >/dev/null 2>&1 || break
        sleep 1
      done
      if launchctl print "gui/$UID_NUM/com.ohisell.wing2" >/dev/null 2>&1; then
        echo "==> ❌ 설치 중단: com.ohisell.wing2 bootout 실패 — 설정 없는 잡이 크래시 루프로 남습니다."
        echo "    수동: launchctl bootout gui/$UID_NUM/com.ohisell.wing2 후 재실행"
        exit 1   # plist는 남긴다 — 지우면 다음 실행이 문제를 못 본다
      fi
      echo "    com.ohisell.wing2 → bootout 완료(설정 없음 — 크래시 루프 방지)"
    fi
    rm -f "$LAUNCH_AGENTS/com.ohisell.wing2.plist"
    echo "    (건너뜀: com.ohisell.wing2 — ~/.ohisell_wing2_fetcher.json 없음. 설정 후 이 스크립트 재실행)"
    continue
  fi
  # ★CAS로 건너뛴 파일이어도 plist는 설치·reload 한다: 데몬이 가리키는 경로는 그대로고,
  #   잡 자체는 떠 있어야 한다(안 띄우면 수집이 통째로 멈춘다). 다만 그 데몬이 실행하는 코드는
  #   **다른 세션의 것**이므로 그 사실을 화면에 남긴다.
  if cas_skipped "$script"; then
    echo "    ⚠️ com.ohisell.$name — 이 잡이 실행할 $script 는 다른 세션의 배포본입니다(위 경고 참조)"
  fi
  tmpl="$REPO_TOOLS/com.ohisell.$name.plist"
  [ -f "$tmpl" ] || { echo "    (템플릿 없음: $name)"; continue; }
  dest="$LAUNCH_AGENTS/com.ohisell.$name.plist"
  sed -e "s#__PYTHON__#$LOCAL_VENV/bin/python3#g" \
      -e "s#__SCRIPT__#$LOCAL_TOOLS/$script#g" \
      -e "s#__HOME__#$HOME#g" \
      "$tmpl" > "$dest"
  # 리로드 검증용 구 잡 PID 캡처(label은 list의 3번째 컬럼).
  _old_pid="$(launchctl list 2>/dev/null | awk -v n="com.ohisell.$name" '$3==n{print $1}')"
  launchctl bootout "gui/$UID_NUM/com.ohisell.$name" 2>/dev/null || true
  # bootout 완료 대기 — 고정 sleep은 느린 종료(fetch 중인 데몬이 Chrome을 최대 15초 정리)와
  # 레이스로 bootstrap이 '미로드'로 조용히 실패한다. 잡이 실제로 사라질 때까지
  # (launchctl print 실패) 최대 25초 폴링.
  for _i in $(seq 1 25); do
    launchctl print "gui/$UID_NUM/com.ohisell.$name" >/dev/null 2>&1 || break
    sleep 1
  done
  # 25초 후에도 잔존 = bootout 실패 → 강제 1회 더(레이스/멈춤 잡 방어).
  if launchctl print "gui/$UID_NUM/com.ohisell.$name" >/dev/null 2>&1; then
    launchctl bootout "gui/$UID_NUM/com.ohisell.$name" 2>/dev/null || true
    sleep 3
  fi
  launchctl bootstrap "gui/$UID_NUM" "$dest" 2>/dev/null || launchctl load "$dest" 2>/dev/null || true
  sleep 1
  _new_pid="$(launchctl list 2>/dev/null | awk -v n="com.ohisell.$name" '$3==n{print $1}')"
  # 성공 = 잡이 로드돼 있고 PID가 갱신됨(구 잡 잔존이면 PID 동일 → 실패). 이전에 미로드면
  # _old_pid가 비어 어떤 로드든 성공. 구 잡이 그대로 살아있는 silent stale-deploy 차단(codex R3).
  if launchctl print "gui/$UID_NUM/com.ohisell.$name" >/dev/null 2>&1 \
     && { [ -z "$_old_pid" ] || [ "$_new_pid" != "$_old_pid" ]; }; then
    echo "    com.ohisell.$name → 설치+reload (pid ${_old_pid:-none}→${_new_pid:-?})"
  else
    echo "    com.ohisell.$name → ⚠️ reload 실패(구 잡 잔존 가능, pid=${_new_pid:-?}) — 수동: launchctl bootout gui/$UID_NUM/com.ohisell.$name && launchctl bootstrap gui/$UID_NUM $dest"
  fi
done

# 6. 스케줄러 워치독 폴 데몬(S5b S5) — 별도 블록(위 loop와 독립).
#    브라우저/플레이라이트 불필요(requests만). prod /api/scheduler/health 폴 → 비정상 시 Mac 알림.
echo "==> 스케줄러 워치독 폴 설치"
WD_SCRIPT="scheduler_watchdog_poll.py"
cas_skipped "$WD_SCRIPT" && echo "    ⚠️ com.ohisell.scheduler-watchdog — $WD_SCRIPT 는 다른 세션의 배포본입니다"
WD_TMPL="$REPO_TOOLS/com.ohisell.scheduler-watchdog.plist"
if [ -f "$WD_TMPL" ]; then
  WD_DEST="$LAUNCH_AGENTS/com.ohisell.scheduler-watchdog.plist"
  sed -e "s#__PYTHON__#$LOCAL_VENV/bin/python3#g" \
      -e "s#__SCRIPT__#$LOCAL_TOOLS/$WD_SCRIPT#g" \
      -e "s#__HOME__#$HOME#g" \
      "$WD_TMPL" > "$WD_DEST"
  launchctl bootout "gui/$UID_NUM/com.ohisell.scheduler-watchdog" 2>/dev/null || true
  sleep 2
  launchctl bootstrap "gui/$UID_NUM" "$WD_DEST" 2>/dev/null || launchctl load "$WD_DEST" 2>/dev/null || true
  echo "    com.ohisell.scheduler-watchdog → 설치+reload"
fi
fi   # FILES_ONLY / DRY_RUN

# ── 마무리 ──────────────────────────────────────────────────────────
if [ -n "$SKIPPED_FILES" ]; then
  echo ""
  echo "==> ⚠️ CAS로 건너뛴 파일:$SKIPPED_FILES"
  echo "    이 파일들은 **다른 세션이 배포한 내용 그대로** 남아 있습니다(당신 코드가 아닙니다)."
  echo "    ★공용 모듈(coupang_auth.py)이 목록에 있으면 특히 주의: 새로 깔린 페처와 구 모듈이"
  echo "      섞여 import 오류가 날 수 있습니다. 그 브랜치를 병합한 뒤 재실행하는 게 정석입니다."
  echo "    의도적으로 덮으려면: tools/install_local_runtime.sh --force=$(printf '%s' "$SKIPPED_FILES" | tr ' ' ',' | sed 's/^,//')"
fi
echo "==> 완료. 데몬이 로컬 런타임($LOCAL_VENV)으로 기동됨."
echo "    상태: launchctl list | grep com.ohisell"
echo "    지금 도는 코드의 출처: $MANIFEST"
