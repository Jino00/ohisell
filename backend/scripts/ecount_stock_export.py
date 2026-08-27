# ecount_stock_export.py — ECOUNT 창고별재고현황 → 적재 페이로드(JSON). **Mac에서 돈다.**
#
# 짝은 `otao_stock_import.py`(prod). 발주 원장의 `otao_po_export.py`/`otao_po_import.py`와
# **같은 통로**다: prod 서버는 ECOUNT 허용목록 IP가 아니고, Mac은 prod DB에 직접 못 쓴다.
#
# 이 스크립트는 **DB를 건드리지 않는다** — 산출은 JSON 파일 하나이고, 그 파일 자체가 근거
# 보존물이다(무엇을 심었는지 나중에 파일로 되짚는다).
#
#   python3 scripts/ecount_stock_export.py --out /tmp/otao_stock_payload.json
#   python3 scripts/ecount_stock_export.py --out /tmp/x.json --base-date 20260827
#
# ★★계약 §3-3 금지선을 **코드로** 강제한다 (규칙을 문서에만 두면 안 지켜진다는 것이 이
#   저장소의 반복 실측이다 — safe_deploy.sh·next_ids.sh와 같은 이유):
#     ①호출 전 **현재 공인 IP가 허용목록에 있는지 확인**하고, 아니면 **부르지 않고 종료**한다.
#     ②**실패는 최대 2회**, 그 뒤 즉시 중단하고 사람에게 보고한다. 실패를 반복하는 코드는
#       그 자체가 차단 트리거다 — 같은 IP에서 zone/login **10회 실패 시 ERP 전체가 차단**되고
#       거기엔 **사람의 웹 로그인까지 포함**된다.
#     ③읽기 전용 엔드포인트만 부른다(여기서는 `InventoryBalance/GetList…ByLocation` 하나).
#
# ★ECOUNT 클라이언트는 이 저장소에 없다 — `AI_office`에 산다. 그 저장소의 코드를 **읽어서
#   쓰기만** 하고 고치지 않는다(계약 §1 「안 함」: ECOUNT 사업장 이관은 AI_office 소관).
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

# ★이 스크립트는 sellc의 `app` 패키지를 **일부러 안 쓴다.** 두 저장소가 똑같이 `app`이라는
#   최상위 패키지 이름을 쓰기 때문에, 한쪽을 먼저 import 하면 다른 쪽 `app.…`이 영영 안 잡힌다
#   (sys.modules에 먼저 캐시된 것이 이긴다). 그래서 **수집과 정규화를 가른다**:
#     여기(export)  = ECOUNT 응답 «원문»을 그대로 파일에 담는다  ← 근거 보존
#     저쪽(import)  = sellc 컨텍스트에서 정규화·적재한다          ← `build_stock_payload`
#   경계를 이렇게 그으면 이름 충돌이 원리적으로 사라지고, 파일에 남는 것이 가공 전 원문이라
#   나중에 「무엇을 받았나」를 우리 파싱을 통하지 않고 되짚을 수 있다.

KST = timezone(timedelta(hours=9))

# 계약 §3-3에 원문으로 적힌 허용목록. Jino가 새 IP를 등록하면 **계약을 먼저 고치고** 여기 넣는다
# — 코드가 계약보다 앞서 나가면 그 순간 이 가드는 장식이 된다.
_ALLOWLISTED_IPS = {
    "183.99.236.174",  # 작업 Mac (Jino 등록 2026-08-25 16:39)
    "168.107.19.222",  # 승인 VM
}

_ENDPOINT = "InventoryBalance/GetListInventoryBalanceStatusByLocation"
_MAX_ATTEMPTS = 2  # 계약 §3-3 집행 규칙 ②


def _public_ip(timeout: float = 10.0) -> str | None:
    import urllib.request

    for url in ("https://api.ipify.org", "https://checkip.amazonaws.com"):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.read().decode().strip()
        except Exception:  # noqa: BLE001 — 어느 실패든 「모른다」로 수렴한다
            continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="ECOUNT 창고별재고현황 → 적재 페이로드(JSON)")
    ap.add_argument("--out", required=True, help="쓸 JSON 경로")
    ap.add_argument(
        "--ai-office",
        default=os.path.expanduser(
            "~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/AI_office"
        ),
        help="ECOUNT 클라이언트가 사는 AI_office 저장소 경로",
    )
    ap.add_argument("--base-date", help="조회 기준일 YYYYMMDD (기본: 오늘 KST)")
    ap.add_argument(
        "--allow-unlisted-ip",
        action="store_true",
        help=(
            "허용목록 확인을 건너뛴다. ★탈출구이지 기본값이 아니다 — 쓰면 화면에 자백이 남는다. "
            "미등록 IP에서 쏘면 차단 위험이 실재한다(계약 §3-3)."
        ),
    )
    args = ap.parse_args()

    # ── 가드 ① 공인 IP ────────────────────────────────────────────────────
    ip = _public_ip()
    if ip is None:
        print("공인 IP를 확인하지 못했다 — 「허용목록에 있다」를 추정하지 않는다. 중단.", file=sys.stderr)
        return 2
    if ip not in _ALLOWLISTED_IPS:
        if not args.allow_unlisted_ip:
            print(
                f"현재 공인 IP {ip} 가 ECOUNT 허용목록에 없다 — 부르지 않고 중단한다.\n"
                "  허용목록(계약 §3-3): " + ", ".join(sorted(_ALLOWLISTED_IPS)) + "\n"
                "  Jino가 이 IP를 등록했다면 계약 §3-3을 먼저 고치고 이 스크립트의 목록에 넣을 것.",
                file=sys.stderr,
            )
            return 2
        print(f"⚠️ 자백: 미등록 IP {ip} 에서 --allow-unlisted-ip 로 강행한다.", file=sys.stderr)
    else:
        print(f"IP 확인: {ip} (허용목록 등록분)")

    # ── ECOUNT 클라이언트 (AI_office) ─────────────────────────────────────
    ai_backend = os.path.join(args.ai_office, "backend")
    if not os.path.isdir(ai_backend):
        print(f"AI_office backend를 못 찾았다: {ai_backend}", file=sys.stderr)
        return 2
    sys.path.insert(0, ai_backend)
    try:
        from dotenv import load_dotenv  # noqa: PLC0415

        load_dotenv(os.path.join(ai_backend, ".env"))
        from app.sub_agents.ecount.ecount_client_sa import (  # noqa: PLC0415
            EcountAuthError,
            build_ecount_client,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ECOUNT 클라이언트를 못 불러왔다: {exc}", file=sys.stderr)
        return 2

    # ★「오하이」(원본, ZONE AB) 계정 — 재고가 여기 있다(ref 98 §8). 자격증명 «값»은
    #   화면에도 페이로드에도 절대 싣지 않는다. 키 이름만 코드에 남는다.
    missing = [
        k
        for k in ("ECOUNT_OHI_COM_CODE", "ECOUNT_OHI_USER_ID", "ECOUNT_OHI_API_KEY", "ECOUNT_OHI_ZONE")
        if not os.environ.get(k)
    ]
    if missing:
        print(f"자격증명 환경변수가 없다: {', '.join(missing)}", file=sys.stderr)
        return 2

    # ★`build_ecount_client(**overrides)`는 **overrides를 적용하기 «전»에** env에서 해석한
    #   자격증명을 검사한다(`resolve_ecount_credentials()`). 그래서 override만 넘기면
    #   「자격증명 미설정」으로 막힌다 — 실측 2026-08-27. 해석기가 읽는 이름으로 먼저 채운다.
    os.environ["ECOUNT_COM_CODE"] = os.environ["ECOUNT_OHI_COM_CODE"]
    os.environ["ECOUNT_USER_ID"] = os.environ["ECOUNT_OHI_USER_ID"]
    os.environ["ECOUNT_API_KEY"] = os.environ["ECOUNT_OHI_API_KEY"]
    os.environ["ECOUNT_ZONE"] = os.environ["ECOUNT_OHI_ZONE"]
    os.environ["ECOUNT_MODE"] = "prod"
    # ★클라이언트의 서킷브레이커를 계약 §3-3(실패 최대 2회)에 맞춘다. 기본값은 3이라
    #   계약보다 «관대»하다 — 코드가 계약보다 느슨하면 계약은 장식이 된다.
    os.environ["ECOUNT_MAX_AUTH_FAILURES"] = str(_MAX_ATTEMPTS)

    client = build_ecount_client()

    now = datetime.now(KST)
    base_date = args.base_date or now.strftime("%Y%m%d")
    # `WH_CD`·`PROD_CD`를 비우면 전체 창고·전체 품목이다(공식 스펙 §5b).
    request = {"BASE_DATE": base_date, "WH_CD": "", "PROD_CD": ""}

    # ── 가드 ② 실패 2회 상한 ──────────────────────────────────────────────
    resp = None
    last_err: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = client.post(_ENDPOINT, request)
            break
        except EcountAuthError as exc:
            last_err = exc
            print(f"[시도 {attempt}/{_MAX_ATTEMPTS}] 인증 실패: {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"[시도 {attempt}/{_MAX_ATTEMPTS}] 호출 실패: {exc}", file=sys.stderr)
    if resp is None:
        print(
            f"{_MAX_ATTEMPTS}회 실패 — 즉시 중단한다. 재시도하지 말고 사람에게 보고할 것 "
            "(같은 IP 10회 실패 시 ERP 전체 차단, 사람 웹 로그인 포함).",
            file=sys.stderr,
        )
        raise SystemExit(1) from last_err

    data = (resp or {}).get("Data") or {}
    rows = data.get("Result") or []

    # ★가공하지 않은 «원문»을 담는다. 응답 헤더 계수도 같이 — 우리가 센 행수와 ECOUNT가 말한
    #   개수가 다르면 그 차이 자체가 신호다(우리 파싱을 통하지 않고 되짚을 수 있어야 한다).
    capture = {
        "snapshot_at": now.replace(tzinfo=None).isoformat(timespec="seconds"),
        "base_date": base_date,
        "endpoint": _ENDPOINT,
        "request": request,
        "response_total_cnt": data.get("TotalCnt"),
        "response_is_success": data.get("IsSuccess"),
        "result": rows,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(capture, fh, ensure_ascii=False, indent=1)

    warehouses = sorted({str(r.get("WH_DES") or r.get("WH_CD") or "?") for r in rows})
    print(f"행수 {len(rows)} (응답 TotalCnt={capture['response_total_cnt']})")
    print(f"창고 {len(warehouses)}개: {', '.join(warehouses)}")
    print(f"→ {args.out}")
    print("다음: scp 로 prod에 옮기고 `python3 scripts/otao_stock_import.py --payload …` 실행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
