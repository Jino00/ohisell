# ref 106 — 「거래명세서확인」(RI→CI) 전이의 주체·요청 정찰 (2026-08-27 KST)

> 계약 `docs/contracts/CONTRACT_1p_invoice_gap.md` **S3**(쓰기 정찰, 읽기 전용)의 산출물.
> 체인 `1p-계산서` n=2 · 세션 `63b2dfa4` · 트랙 `docs/tracks/active/track_coupang-rocket-1p.md`
> **이 정찰은 GET만 했다.** 확인 버튼을 누르지 않았고 supplier에 상태 변경 요청을 보내지 않았다(계약 §3 금지선).

---

## 0. 한 줄 판정

**벤더 버튼이다.** `#btnConfirmInvoice` → `POST /scm/purchase/order/confirmInvoice?purchaseOrderSeq={poSeq}`.
버튼은 **RI 상태에서만** 서버가 렌더한다(RI 3/3 존재 · CI·PA·RP 0/3). ⇒ **후속 「SellC 확인 버튼」 계약은 성립 가능하다.**

## 1. 방법 (재현 가능)

Jino Mac의 supplier Chrome 프로필(`~/.ohisell_supplier_chrome`)에서 페처 모듈을 import해 같은 탭 안에서
`fetch(path, {credentials:'include'})`로 SSR HTML만 받았다. 네비게이션·클릭·POST 없음.

- 스크립트(일회용, 저장소 밖): 스크래치패드 `s3_recon.py` / `s3_fetch_js.py`
- 대상 경로: `GET https://supplier.coupang.com/scm/purchase/order/get/{purchaseOrderSeq}`
- 핸들러 원본: `GET /scm/resources/20260821172025/supplier-hub/js/app/po/detail.js` (13,052 bytes, 200)

★**감사 흔적의 한계를 밝힌다**(완료 QA 지적): `s3_recon.py`는 **같은 파일을 두 번 돌렸다** — 1회차 `TARGETS`=RI 3건,
2회차 `TARGETS`=대조군 3건(CI·PA·RP). 파일에 남은 것은 2회차뿐이라, RI 3건을 가져온 **그 시점의 TARGETS 리터럴은
파일로 재현되지 않는다.** 다만 요청 방식은 `TARGETS`가 아니라 `FETCH_JS` 상수가 정하고 그 상수는 두 회차가 동일하며
`fetch(path, {credentials:'include'})` **GET 하나뿐**이다(메서드 인자·body·헤더 주입 없음). 다음에 같은 정찰을 하면
회차마다 파일을 따로 남길 것.

## 2. 실측 — 버튼 존재 여부는 상태로 갈린다

관측 시각 2026-08-27 22:28~22:34 KST. `grep -c 'id="btnConfirmInvoice"'`:

| 대상 | 발주번호 | 상태 | 버튼 | HTML bytes |
|---|---|---|---|---|
| RI(지급 완료, 2025-10 발주) | 115340779 | RI | **1** | 48,479 |
| RI(계산서 연결) | 139791428 | RI | **1** | 48,526 |
| RI(계산서 없음) | 140101254 | RI | **1** | 39,906 |
| 대조군 | 140163784 | CI | **0** | 31,424 |
| 대조군 | 140701557 | PA | **0** | 30,006 |
| 대조군 | 140731790 | RP | **0** | 26,011 |

★**대조군을 안 떴으면 「버튼이 있다」만 남고 「언제 있나」를 몰랐을 것이다.** 세 대조군이 0이므로
버튼 렌더는 서버측 상태 게이트이고, **버튼 존재 = 「지금 확인 가능」의 정본**으로 쓸 수 있다.

★그리고 **이미 지급까지 끝난 2025-10 발주에도 버튼이 있다**(115340779, 정산 지급일 2025-12-19).
즉 이 확인은 지급의 선행조건이 아니고, 안 눌러도 돈은 들어오며, **기한 없이 계속 눌릴 수 있는 상태로 남는다.**

## 3. 요청 규격 (`detail.js:215-227` 원문)

```js
elements['$btnConfirmInvoice'].on('click', function () {
  if (confirm(messages.getMessage('po.detail.confirm_transaction_statement_checked'))) {
    var poSeq = $(this).data('po-seq');
    $.post("/scm/purchase/order/confirmInvoice?purchaseOrderSeq=" + poSeq, function (data) {
      if (data.success) {
        alert(messages.getMessage('po.detail.processing_completed'));
      } else {
        alert(data);
      }
      window.location.reload();
    });
  }
});
```

- **메서드·URL**: `POST /scm/purchase/order/confirmInvoice?purchaseOrderSeq={poSeq}`
- **바디**: **없다.** `$.post(url, callback)` — 2번째 인자가 함수라 jQuery는 `data`를 생략한다. 파라미터는 쿼리스트링 하나뿐.
- **인증**: 세션 쿠키(same-origin). ★**CSRF 토큰이 없다** — 페이지 어디에도 토큰 hidden input·헤더 주입이 없다.
- **응답**: JSON. 성공 판정은 `data.success`(불리언). 실패 시 응답 객체를 그대로 `alert`에 넣는다(= 구조화된 에러 메시지가 없다).
- **사람 게이트**: 브라우저 네이티브 `confirm()` 1회. 서버측 2단계 확인 없음.
- **버튼 마크업**: `<button id="btnConfirmInvoice" class="btn btn-default" data-po-seq="{seq}">` — `disabled` 속성 없음.

## 4. 상태 모델 (진행바 `data-status` 원문)

발주상세 진행바가 전이 순서를 그대로 싣는다. 주석 처리된 칸(`<!--...-->`)은 화면에 안 뜨는 내부 상태다.

| 표시 | data-status | 목록 API 코드 |
|---|---|---|
| 거래처 확인 요청 | `REQUEST_CONFIRM_PARTNER` | RP |
| (숨김) 거래처 확인 | `CONFIRM_PARTNER` | — |
| 거래처 수정 | `MODIFY_PARTNER` | — |
| (숨김) MD 확인 요청 | `REQUEST_CONFIRM_MD` | — |
| 발주 확정 | `PURCHASE_ORDER_ACCEPT` | PA |
| (숨김) 발주 마감 | `PURCHASEORDER_FINISH` | — |
| **거래명세서 확인 요청** | `REQUEST_PARTNER_CONFIRM_INVOICE` | **RI** |
| **거래명세서 확인** | `PARTNER_CONFIRM_INVOICE` | **CI** |

★`CI = 거래명세서확인`은 **벤더가 눌러 도달하는 종착 상태**다. prod 분포(2026-08-27 22:2x): CI 2,582 · PA 71 · RP 32 · RI 15.

## 5. 같은 페이지의 다른 쓰기 경로 (참고 — 이번 스코프 아님)

정찰 중 같은 `detail.js`에서 관측된 것. **어느 것도 호출하지 않았다.**

- `POST /scm/purchase/order/modify/location` (회송지 수정, form param) — 응답 `{success, message}`
- `GET /scm/receive/detail?requestSeq={poSeq}` (정산내역보기, 새 창)
- 업로드 폼 `#inspectionUploadForm` (multipart, 식용란 선별포장확인서 — 우리와 무관)

## 6. 후속 계약이 성립 가능한가 — 성립한다. 단 미상 3건이 남는다

**성립 근거**: 주체가 벤더로 확정됐고, 요청이 단일 POST에 파라미터 하나이며, 대상 판별(RI)이 이미 우리
원장에 있고, 버튼 렌더 게이트가 서버측이라 「눌러도 되는가」를 우리가 지어낼 필요가 없다.

**계약 초안 전에 못 박아야 할 미상**:
1. `[미상]` **멱등성** — 이미 CI인 건에 같은 POST를 보내면 어떻게 되는지 모른다(호출하지 않았으므로).
   저장소 전체에 supplier 쓰기 0건이라 참조 코드도 없다. ⇒ **재시도 금지**가 계약 금지선이어야 한다.
2. `[미상]` **부분 실패의 표면** — 실패 응답이 구조화돼 있지 않다(`alert(data)`). 배치로 여러 건을 보낼 때
   무엇이 성공하고 무엇이 실패했는지 응답만으로 가를 수 있는지 미검증.
3. `[미상]` **되돌리기** — RI로 되돌리는 경로가 화면에 없다. 회계 확정이므로 **되돌릴 수 없다고 가정**해야 한다.
   ⇒ 전역 §1 승인 지점(되돌릴 수 없는 액션)이고, 실행은 Jino 승인 계약에서만.

**권고 형태**(초안 아님 — 다음 계약이 정한다): 백엔드 `_write_guard.guarded_write` 패턴(dry_run 기본 +
`CONFIRM_LIVE_WRITE` 토큰 + WARNING 감사 로그)을 게이트로 두고 Mac 페처는 실행기로만 쓴다.
`refresh_contract`의 lease 재시도는 **읽기용 설계**라 그대로 쓰면 같은 확인을 두 번 누른다.

## 7. 이 정찰이 뒤집은 인계 주장 (S2-3 재관측과 함께 관측됨)

- 인계·MEMORY의 「굳은 RI 8건 = 이미 지급까지 끝난 **죽은 유령**」 → **반증.** 재수집 후 8건 전부
  `synced_date` 2026-08-05 → 2026-08-27로 갱신됐는데 **상태는 여전히 RI**이고 **버튼도 살아 있다.**
  지급이 끝난 것은 맞으나 «죽은 줄»이 아니라 **아직 안 누른 줄**이다.
- 그 결과 화면의 살아있음/굳음 분리는 **살아있음 15 / 굳음 0**이 됐다 — 즉 이 분리는
  «업무상 살아있음»이 아니라 «수집 신선도»를 재고 있었고, 재훑기가 고쳐지자 **판별력이 사라졌다.**
  상세는 `HANDOFF_ri-confirm-recon_20260827.md` 참조.
