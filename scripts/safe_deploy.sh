#!/usr/bin/env bash
# safe_deploy.sh — prod 배포 CAS(compare-and-swap) 가드 (D-NAO-49)
#
# ★존재 이유(2026-07-17 사고): 병행 세션 A가 07:35에 entity_sync.py(qi 수집)를 prod에
# 배포했고, 세션 B(나)가 07:41에 구 base 기준 같은 파일을 scp로 덮어 **A의 기능이 4분
# 만에 죽었다**. 원칙20("트랙 동시 작업 금지")은 문서라 세 번 다 못 막았다 — 구조로 막는다.
#
# ★왜 "origin/main 병합했나" 체크로는 안 되는가: 이 프로젝트 관례가 **배포 → 나중에 PR**
# 이라(양쪽 다 그랬다), 충돌 시점엔 상대 코드가 어느 공유 ref에도 없다. ref 수준 체크는
# 원리적으로 이 사고를 못 잡는다.
#
# ★유일하게 잡는 규칙(이 스크립트의 핵심):
#   덮으려는 prod 파일의 현재 내용이 **내 브랜치 역사(git rev-list HEAD)에 존재하는
#   버전**이어야 한다. 내 브랜치가 한 번도 본 적 없는 내용이 prod에 있다
#   = 다른 세션이 배포했다 = **즉시 중단**(병합 후 재시도).
#
# 추가 안전장치:
#   · prod 측 배포 락(mkdir 원자성) — 두 세션 동시 배포 자체를 차단
#   · 커밋 안 된 변경이 있는 파일은 배포 거부(배포물 = 커밋된 내용, 재현 가능)
#   · 배포 후 sha 대조 + prod 매니페스트(deploy-manifest.jsonl)에 who/when/commit 기록
#
# 사용:
#   scripts/safe_deploy.sh backend/app/routers/naver_ad.py [파일...] [--restart]
#   scripts/safe_deploy.sh --frontend            # frontend/dist rsync(락+매니페스트만)
#   scripts/safe_deploy.sh ... --steal-lock      # 죽은 세션의 락 강제 해제(사유 확인 후)
set -euo pipefail

# 테스트용 오버라이드 허용(실 prod 안 건드리고 실패 경로 검증) — 평소엔 건드리지 말 것.
HOST="${SAFE_DEPLOY_HOST:-sellc.ohitech.co.kr}"
REMOTE_REPO="${SAFE_DEPLOY_REMOTE:-/home/ubuntu/ohisell}"   # repo-relative 경로가 그대로 매핑됨
LOCK_DIR="$REMOTE_REPO/.deploy-lock"
MANIFEST="$REMOTE_REPO/deploy-manifest.jsonl"

RESTART=0; FRONTEND=0; STEAL=0; FILES=()
for a in "$@"; do
  case "$a" in
    --restart) RESTART=1 ;;
    --frontend) FRONTEND=1 ;;
    --steal-lock) STEAL=1 ;;
    -h|--help) grep '^# ' "$0" | sed 's/^# //'; exit 0 ;;
    *) FILES+=("$a") ;;
  esac
done

cd "$(git rev-parse --show-toplevel)"
BRANCH=$(git branch --show-current)
COMMIT=$(git rev-parse --short HEAD)

fail() { echo "❌ ABORT: $1" >&2; exit 1; }

# ── 0. 배포 락 (prod 측, mkdir 원자성) ──────────────────────────────
release_lock() { ssh -o BatchMode=yes "$HOST" "rm -rf '$LOCK_DIR'" 2>/dev/null || true; }
if [ "$STEAL" = 1 ]; then
  echo "⚠️ 락 강제 해제 — 기존 소유자:"
  ssh -o BatchMode=yes "$HOST" "cat '$LOCK_DIR/owner' 2>/dev/null || echo '(없음)'"
  release_lock
fi
if ! ssh -o BatchMode=yes "$HOST" "mkdir '$LOCK_DIR' 2>/dev/null"; then
  echo "── 락 소유자 ──" >&2
  ssh -o BatchMode=yes "$HOST" "cat '$LOCK_DIR/owner' 2>/dev/null" >&2 || true
  fail "다른 배포가 진행 중(락 존재). 끝나길 기다리거나, 죽은 세션이 확실하면 --steal-lock."
fi
trap release_lock EXIT
ssh -o BatchMode=yes "$HOST" "printf '%s\n' 'branch=$BRANCH commit=$COMMIT host=$(hostname -s) ts=$(date -u +%FT%TZ)' > '$LOCK_DIR/owner'"

# ── frontend 모드: dist 전체 교체라 파일 CAS 불가(빌드 산출물은 git 역사에 없음).
#    락 + 매니페스트 + 배포 전 dist 백업으로 방어 ──────────────────────
if [ "$FRONTEND" = 1 ]; then
  [ -d frontend/dist ] || fail "frontend/dist 없음 — 먼저 npm run build"
  STAMP=$(date +%Y%m%d_%H%M)
  ssh -o BatchMode=yes "$HOST" "cp -r '$REMOTE_REPO/frontend/dist' '$REMOTE_REPO/frontend/dist_backup_$STAMP'"
  rsync -az --delete frontend/dist/ "$HOST:$REMOTE_REPO/frontend/dist/"
  L=$(shasum -a 256 frontend/dist/index.html | cut -d' ' -f1)
  R=$(ssh -o BatchMode=yes "$HOST" "sha256sum '$REMOTE_REPO/frontend/dist/index.html' | cut -d' ' -f1")
  [ "$L" = "$R" ] || fail "index.html sha 불일치"
  ssh -o BatchMode=yes "$HOST" "printf '%s\n' '{\"ts\":\"$(date -u +%FT%TZ)\",\"kind\":\"frontend\",\"branch\":\"$BRANCH\",\"commit\":\"$COMMIT\",\"backup\":\"dist_backup_$STAMP\"}' >> '$MANIFEST'"
  echo "✅ frontend 배포 완료 (백업: dist_backup_$STAMP)"
  exit 0
fi

[ ${#FILES[@]} -gt 0 ] || fail "배포할 파일을 지정하세요 (repo-relative 경로)"

# ── 1. 파일별 사전 검사 ──────────────────────────────────────────────
declare -a TO_SEND=()
for f in "${FILES[@]}"; do
  [ -f "$f" ] || fail "$f — 로컬에 없음"
  # 배포물 = 커밋된 내용. 미커밋 변경이 섞이면 prod와 git이 어긋나 다음 CAS가 깨진다.
  git diff --quiet HEAD -- "$f" || fail "$f — 커밋 안 된 변경 있음. 커밋 후 배포."

  # 원격 내용을 **한 번만** 가져와 git blob id로 변환 — 이후 비교는 전부 트리 조회
  # (git rev-parse commit:path)라 내용을 다시 읽지 않는다. 파일 없으면 빈 결과.
  # ★sha256 대신 blob id를 쓰는 이유: 이 저장소가 iCloud Drive에 있어 콜드 I/O가 느리다
  #   (실측: rev-list 1회 50초, CPU 0% = 전부 디스크 대기). 버전마다 `git show | sha256`을
  #   돌리면 파일당 수 분 — 안 쓰게 되는 게이트는 없는 것과 같다. blob 비교는 warm 후 수 초.
  RTMP=$(mktemp)
  ssh -o BatchMode=yes "$HOST" "cat '$REMOTE_REPO/$f' 2>/dev/null" > "$RTMP" || true
  LOCAL_BLOB=$(git rev-parse "HEAD:$f")
  if [ ! -s "$RTMP" ]; then
    echo "  🆕 신규: $f"
    TO_SEND+=("$f"); rm -f "$RTMP"; continue
  fi
  REMOTE_BLOB=$(git hash-object "$RTMP"); rm -f "$RTMP"

  if [ "$REMOTE_BLOB" = "$LOCAL_BLOB" ]; then
    echo "  ⏭  동일(이미 배포됨): $f"; continue
  fi

  # ★핵심 CAS: prod의 현재 내용이 내 브랜치 역사에 있는 버전인가.
  KNOWN=0
  while read -r c; do
    if [ "$(git rev-parse -q --verify "$c:$f" 2>/dev/null)" = "$REMOTE_BLOB" ]; then
      KNOWN=1; break
    fi
  done < <(git rev-list HEAD -- "$f")

  if [ "$KNOWN" = 1 ]; then
    echo "  ✅ CAS 통과(prod=내 역사 속 구버전): $f"
    TO_SEND+=("$f")
  else
    echo "" >&2
    echo "  ★ prod의 $f 내용(blob ${REMOTE_BLOB:0:12}…)이 이 브랜치 역사에 없습니다." >&2
    echo "    = 다른 세션이 배포한 코드입니다. 지금 덮으면 그 기능이 죽습니다" >&2
    echo "      (2026-07-17 qi 수집 clobber 사고와 동일 패턴)." >&2
    echo "    → git fetch 후 그쪽 브랜치/main을 병합하고 다시 실행하세요." >&2
    fail "$f — CAS 실패(미지의 prod 내용)"
  fi
done

[ ${#TO_SEND[@]} -gt 0 ] || { echo "보낼 파일 없음(전부 최신)"; exit 0; }

# ── 2. 전송 + 검증 + 매니페스트 ─────────────────────────────────────
for f in "${TO_SEND[@]}"; do
  scp -q "$f" "$HOST:$REMOTE_REPO/$f"
  L=$(git show "HEAD:$f" | shasum -a 256 | cut -d' ' -f1)
  R=$(ssh -o BatchMode=yes "$HOST" "sha256sum '$REMOTE_REPO/$f' | cut -d' ' -f1")
  [ "$L" = "$R" ] || fail "$f — 전송 후 sha 불일치"
  echo "  📦 배포: $f"
done
FILES_JSON=$(printf '"%s",' "${TO_SEND[@]}"); FILES_JSON="[${FILES_JSON%,}]"
ssh -o BatchMode=yes "$HOST" "printf '%s\n' '{\"ts\":\"$(date -u +%FT%TZ)\",\"kind\":\"backend\",\"branch\":\"$BRANCH\",\"commit\":\"$COMMIT\",\"files\":$FILES_JSON}' >> '$MANIFEST'"

# ── 3. 재시작(선택) ─────────────────────────────────────────────────
if [ "$RESTART" = 1 ]; then
  ssh -o BatchMode=yes "$HOST" "pm2 restart ohisell-backend >/dev/null 2>&1; sleep 6; pm2 list | grep ohisell-backend"
fi
echo "✅ 배포 완료 (commit $COMMIT)"
