# 적대 리뷰 — D-INV-5 「사전 모집단을 발주서 전체로, 단 «층»으로」 (PR #467)

- **대상**: `feat/po-forecast-n6` @ `e20604bb` · `git diff origin/main...HEAD`
- **대조**: `docs/contracts/CONTRACT_inventory_unified.md` §2-9 · §2 9-A(D-INV-5) · §3 금지선 1~10
- **리뷰어**: 적대 리뷰 서브에이전트(구현한 기와 다른 기) · 2026-08-26 KST
- **워크트리**: `/Users/jino/.claude-worktrees/ohiselling/po-forecast-n6` · `python3` 3.14.3

## 판정

**PASS — P1 0건.**

변이 **15종 중 13종 사망**, 생존 2종은 **둘 다 감식용 컬럼(`match_kind`·`evidence`)의 테스트 갭**이고
출하 코드 자체는 옳다(라이브 실행으로 정상 동작 확인). 사망 13종은 **전부 `failed`(테스트가 잡음)**이고
`error`(문법 오류로 죽은 가짜 사망)는 0건이다.

리뷰는 **완주**했다(INCONCLUSIVE 아님). 다만 한계 하나를 명시한다 — 아래 §5.

---

## 1. P1 — 0건

지적할 P1이 없다. 아래는 P1을 찾으려고 실제로 돌린 것들과 그 결과다(전부 반증됐다).

### 1-1. `build_layered_dictionary`의 층 전이 — 구멍 없음

`d.by_key[key] = set(codes)` · `d.originals[key] = set(...)`가 **둘 다 새 `set`을 만든다.**
`supplement`는 함수 지역 객체라 공유 참조로 1층이 오염될 경로 자체가 없다. 실증:

```
── G) 얕은 복사 오염 — supplement를 나중에 바꾸면 d가 흔들리나 ──
  d 변화 없음: True
```

계약이 요구한 층 규율 6가지를 **직접 호출로 전수 재현**했고 전부 계약대로였다:

| 상황 | 관측 결과 | 계약 |
|---|---|---|
| A. 1층 ambiguous(코드 둘) + 2층 같은 키 단일 코드 | `ambiguous` 유지, `후보 GSAS24U, GSAS24UX2` | ✓ 「한 층 안의 모호는 ambiguous로 남긴다·다수결 금지」 |
| B. 1층 침묵 + 2층 ambiguous | `ambiguous` (`후보 X1, X2`) | ✓ |
| C. 1층 `name_en=None`(B형) + 2층이 이름 공급 | `GAPIP17` / `exact_en` / `evidence='B1'` | ✓ D-INV-5 본체 |
| D. 2층에서 빌린 키에 규칙 적용(`screen protector`) | `normalized` | ✓ |
| E. 1층이 코드 없이 이름만 | 2층이 채움(`GQ`) | ✓ 「코드가 없으면 침묵」 |
| F. 1↔2층 코드 충돌 | **`NEWCODE`(정본) 승** | ✓ D-INV-3 우선순위 |

**`originals`가 빠지면?** → 빌려온 키가 `exact_en` 대신 `normalized`가 된다. 코드는 옳게 옮기고 있고,
**테스트만 그걸 안 잠근다**(변이 M3 생존 — P2-1).

### 1-2. 수량 축 불변 — 실증

`--sync-map-only`로 사전을 넓힌 뒤 로스터를 직접 읽었다(격리 SQLite, prod 무접촉):

```
MAP:    'Glass_iP15 pro' -> GAPIP15PR | kind: exact_en | evid: 20260210-2
ROSTER: RosterRow(product_code='GAPIP15PR', ordered=1300, picked=100, reserved=1200, ...)
```

정본(ECOUNT 사본, `name_en=NULL`)이 **1,300**, 대체된 로컬본이 **5,700**인 상황에서
**라벨은 5,700쪽에서 빌려 오고 수량은 1,300만** 셌다. 계약 9-A 「수량 축은 불변」 그대로다.
변이 M5(`is_authoritative` 필터 제거)가 `test_widening_the_dictionary_does_not_widen_the_order_totals`로
사망해 이 분리가 **잠겨 있음**도 확인했다.

### 1-3. 이름이 바뀐 테스트는 «정말» 그것을 잰다

`test_sync_name_map_authoritative_label_wins_over_superseded`가 다른 이유로 초록인지 의심해
`build_layered_dictionary`를 스파이로 감싸 층 내용을 찍었다:

```
PRIMARY(정본):   [('GAPIP15PR', 'Glass_iP15 pro')]
FALLBACK(비정본): [('WRONGCODE',  'Glass_iP15 pro')]
1 passed
```

**같은 키에 다른 코드**가 양쪽 층에 실재하는 진짜 충돌이다. 그리고 변이 **M1**(우선순위 가드 제거)과
**M6**(층 인자 뒤바꾸기)이 **둘 다 이 테스트로 사망**했다 — 이 테스트는 「정본이 이긴다」를 실제로 잰다.

### 1-4. `--sync-map-only` — `AttributeError`도 오해를 부르는 0도 없다

`IngestReport`의 적재 전용 필드(`files_scanned`·`qty_mismatch` …)를 찍는 코드는 **전부 `else:` 안**에 있고
`--sync-map-only` 분기는 사전 4개 값만 찍는다. 실제 4가지 인자 조합을 돌렸다(prod 미접속·격리 SQLite):

```
$ DATABASE_URL=sqlite:///<scratch>/rev.db python3 scripts/otao_po_import.py --sync-map-only --dry-run
사전만 재계산했다 (원장 무접촉).
사전: 원장 품목명 1종 → 붙음 1 (사람 확정 유지 0) / 매핑 필요 0
⚠️ dry-run — 롤백했다. 아무것도 안 심었다.                                    rc=0

$ … otao_po_import.py                       → error: --payload가 필요하다 (또는 --sync-map-only)  rc=2
$ … otao_po_import.py --sync-map-only       → ✅ 커밋 완료.                                      rc=0
```

**`--payload` 없이 돈다.** 별개의 흠 하나는 P2-4로 내린다.

### 1-5. 메모리 — 문제없음(수치)

`sync_name_map`이 전체 라인을 메모리에 올린다는 점을 prod 규모(1,205행)와 10·100배로 실측했다:

```
라인   1,205행 → 키 200개 | peak   0.39 MB |   8.1 ms
라인  12,050행 → 키 200개 | peak   2.37 MB |  77.2 ms
라인 120,500행 → 키 200개 | peak  22.24 MB | 772.9 ms
```

prod 규모에서 **0.39 MB / 8 ms**. 100배로 늘어도 22 MB다. 우려 반증.

### 1-6. 계약 §2 9-A의 숫자 — 내부 정합 검산 통과

계약·`name_map.py` docstring·트랙 `확인:` 줄에 실린 수치를 서로 곱셈·덧셈으로 대조했다:

```
13,370 + 8,390 = 21,760 ✓      18,970 + 2,790 = 21,760 ✓
13,370 / 21,760 = 61.44% ✓     18,970 / 21,760 = 87.18% ✓
35 + 30 = 65 ✓                 43 + 22 = 65 ✓
43 − 35 = 8종 ✓                18,970 − 13,370 = 5,600 ✓
라이브 잔량: 30,090 − 13,370 = 16,720 ✓ / 30,090 − 18,970 = 11,120 ✓ (Δ = 5,600, 발주 누계 불변)
```

**모든 항이 맞물린다.** 특히 마지막 줄이 「수량 축 무접촉」을 산술로 재확인한다.

## 2. 금지선 검사 (§3) — 위반 0건

| # | 금지선 | 판정 | 근거 |
|---|---|---|---|
| 2 | 자동 «실행» 금지 | ✓ | 스크립트는 사람이 실행. HTTP 표면은 읽기 전용(라우터 GET 1개), 새 자동화 0 |
| 3 | ECOUNT API 호출 | ✓ | diff 추가 줄에 `requests`·`httpx`·ecount 클라이언트 **0건**(grep) |
| 6 | 매핑 미확정의 발주 수량 산출 금지 | ✓ | 라이브 재현: `manual`+코드없음 행이 `picked`에 안 들어가고 `unmapped{'Glass_Human': 60}`로 표면화. ambiguous도 코드를 «고르지 않는다»(A·B) |
| 8 | **A′/B 소관(수입 원장) 수정 금지** | ✓ | `ImportInvoiceLine`은 `select(...)`에만 등장(3곳). `session.add(Import*)`·`internal_sku =` **0건**. 실행 후 `internal_sku` 값 `[None]` 불변 확인 |
| 9 | 합산 단일 숫자 표기 금지 | ✓ | 라우터 3칸 구조 무변경, 변이 SUR-4가 테스트로 사망 |
| — | 마이그레이션 | ✓ | diff에 `alembic/` 파일 **0건** |
| — | prod 쓰기 | ✓ | 리뷰 중 prod 미접속. 전 검증은 격리 SQLite(`<scratch>/rev.db`·`flip.db`) |

## 3. 변이표 (15종 · KILLED 13 / SURVIVED 2)

명령: `python3 -m pytest tests/test_otao_po_ingest.py tests/test_otao_po_http.py tests/test_otao_po_ledger.py -q`
(기준선 **49 passed**) · 프론트: `npx vitest run src/pages/otaoPoReachesTheUser.test.tsx` (기준선 **9 passed**)
모든 변이는 `git checkout -- <파일>`로 원복했고, 대상 파일만 경로 지정했다.

### 3-1. 로직 변이

| ID | 무엇을 어떻게 | 결과 | 죽인 것 / 종류 |
|---|---|---|---|
| M1 | `build_layered_dictionary`의 `if key in d.by_key: continue` 제거 (2층이 1층을 덮음) | **KILLED** | `test_sync_name_map_authoritative_label_wins_over_superseded` · **failed** |
| M2 | `build_layered_dictionary`를 `return build_dictionary(primary)`로 (넓히기 취소) | **KILLED** | `test_sync_name_map_borrows_labels_when_authoritative_is_silent` · **failed** |
| M3 | `d.originals[key] = set(...)` 줄 삭제 | **SURVIVED** | 49 passed — 아무도 못 잡음 |
| M4 | `d.evidence[key] = supplement.evidence[key]` 삭제 | **SURVIVED** | 49 passed — 아무도 못 잡음 |
| M5 | `roster.build_roster` ①의 `is_authoritative` 필터 제거 (수량 축 오염) | **KILLED** | `test_widening_the_dictionary_does_not_widen_the_order_totals` · **failed** |
| M6 | `sync_name_map`에서 층 인자 뒤바꿈 `(other_lines, auth_lines)` | **KILLED** | `..._authoritative_label_wins_over_superseded` · **failed** |

### 3-2. ★표면 절단 변이 — 백엔드 (사용자에게 닿는 마지막 마디를 끊는다)

| ID | 끊은 마디 | 결과 | 죽인 것 / 종류 |
|---|---|---|---|
| **SUR-1** | `sync_name_map`에서 `session.add(row)` 제거 — 사전은 옳게 넓혀지는데 **테이블에 안 앉는다** | **KILLED** | `..._authoritative_label_wins_over_superseded` · **failed** |
| **SUR-2** | `roster.build_roster`가 `unmapped`를 안 채움 — 못 붙인 품목명이 **조용히 사라짐**(§2-9 그 자체) | **KILLED** | `test_unmapped_names_are_in_the_body_with_quantity` · **failed** |
| **SUR-3** | 라우터 응답에서 `"unmapped"` 키 제거 | **KILLED** | `test_unmapped_names_are_in_the_body_with_quantity` · **failed** |
| **SUR-4** | 라우터가 `name_map_resolved` 자리에 `map_total`을 실음 — **87.2%를 100%인 척한다** | **KILLED** | `test_source_reports_authoritative_split` · **failed** |

### 3-3. ★표면 절단 변이 — 프론트 (렌더 제거 · 픽셀까지)

`node_modules`가 워크트리에 없어 메인 저장소 것을 **심볼릭 링크로 빌려** 돌렸고(`package.json` sha 동일 확인),
끝난 뒤 링크를 제거했다. 메인 저장소엔 쓰지 않았다.

| ID | 끊은 마디 | 결과 | 죽인 것 / 종류 |
|---|---|---|---|
| **SUR-F1** | 「매핑 필요」 목록을 통째로 안 그림(항상 EmptyState) | **KILLED** | `SUR-4: 매핑 필요 품목명이 수량과 함께 뜬다` · **failed** |
| **SUR-F2** | 커버리지 배지 `N/M 붙음` 제거 — 결손의 **크기**가 사라짐 | **KILLED** | `SUR-7: 사전 커버리지 배지` · **failed** |
| **SUR-F3** | ★배지를 `total/total`로 — **거짓말**(제거가 아니라 위조) | **KILLED** | `SUR-7` · **failed** |
| **SUR-F4** | 매핑 필요 표에서 **수량 칸만** 제거(이름은 보이나 얼마나 새는지 안 보임) | **KILLED** | `SUR-4` · **failed** |
| **SUR-F5** | `unmapped.filter(u => u.quantity > 100)` — 작은 결손을 그럴듯하게 숨김 | **KILLED** | `SUR-4` · **failed** |

**표면 층은 견고하다.** 특히 SUR-F3·SUR-F5는 «지우기»가 아니라 «그럴듯하게 왜곡하기»인데도 죽었다.

### 3-4. 회귀

전체 백엔드 스위트 **6,700 passed / 0 failed** (257.94s). 회귀 0건.

## 4. P2 — 선택 사항 (라운드를 늘리지 않는다 · 처분 권고 병기)

1. **[채택 권고] M3 생존** — `..._borrows_labels_when_authoritative_is_silent`에
   `assert row.match_kind == "exact_en"` 한 줄. 지금은 빌려온 키의 `exact_en`↔`normalized` 판정이
   조용히 틀어져도 49건이 전부 초록이다. (`Dictionary.originals` docstring이 이 컬럼의 **존재 이유**를
   「규칙 의존도를 나중에 재려고」라고 적어 뒀는데, 재는 대상이 **정확히 이번에 넓힌 2층**이다.)
2. **[채택 권고] M4 생존** — 같은 테스트에 `assert row.evidence == "20251121-1"` 한 줄.
   빌려온 매핑의 「왜 이 코드인가」 되짚기(발주번호)가 통째로 NULL이 돼도 아무도 안 잡는다.
3. **[기각 가능·정리]** `if key in supplement.evidence:` 가드는 **죽은 코드**다 — `Dictionary.add()`가
   `by_key`와 `evidence`를 항상 함께 채우므로 `by_key`에 있는 키는 반드시 `evidence`에도 있다.
   지우거나 「방어적」이라고 한 줄 밝히면 다음 독자가 조건을 찾아 헤매지 않는다.
4. **[채택 권고] `--sync-map-only`인데 `--payload`를 같이 주면 파일을 열고 파싱한 뒤 버린다.**
   재현: `python3 scripts/otao_po_import.py --payload /nope.json --sync-map-only --dry-run`
   → `FileNotFoundError: [Errno 2] No such file or directory: '/nope.json'`.
   **「페이로드가 필요 없다」고 적어 둔 경로가 페이로드 때문에 죽는다.** `if args.payload and not args.sync_map_only:`
   로 바꾸거나 두 인자 동시 지정을 `ap.error`로 거부.
5. **[이월]** `--sync-map-only` 분기에 테스트 **0건**. 이번 배포가 쓴 바로 그 경로다.
   내가 손으로 4가지 인자 조합을 돌려 확인했으나 회귀 잠금은 없다.
6. **[이월·기존]** `match_kind='manual'` + `product_code=NULL` 행은 넓힌 사전이 **답을 알게 된 뒤에도**
   영원히 미해결로 남고, 「사전이 이제 답을 안다」는 신호가 어디에도 없다.
   라이브 재현: `Glass_Human`이 사전에 `GHUMAN`으로 실재하는데 `map_manual_kept=1`·`unresolved=['Glass_Human']`.
   §2-9는 지켜진다(「매핑 필요」에 실린다) — 위반이 아니라 **운영상 막다른 길**이다. 이 PR이 만든 것도 아니다.
7. **[이월·기존 · 이 PR이 노출을 키움]** 화면이 수량 **단위**를 선언하지 않는다.
   `OtaoPurchaseOrderLine` docstring이 *"화면은 단위를 명시하고, 두 해석을 합산하지 않는다"*를 요구하는데
   `OtaoPurchaseOrders.tsx`에 `단위`·`2매입`·`2ea`·`미상` 문자열 **0건**(grep). `2ea` 줄의 수량이
   세트인지 낱장인지는 **[미상] 잔여**(D-INV-2)인데, **이 PR이 그 칸으로 5,600개를 더 밀어 넣었다.**
   결함은 이 PR 것이 아니지만 **노출은 이 PR이 키웠으므로** 소관 슬라이스에 실어 둔다.

## 5. 리뷰의 한계 (명시)

- **prod 라이브 수치는 내가 재지 않았다.** 트랙 `확인:` 줄의 라이브 관측(사전 43/65, 픽업 13,370→18,970,
  잔량 16,720→11,120, 발주 누계 30,090 불변, `internal_sku` 0 불변)은 **구현 세션의 관측**이고,
  나는 prod에 접속하지 않았다(리뷰 지시·§3 준수). 내가 한 것은 ①**그 수치들의 산술 정합 검산**(§1-6 — 전부 맞물림)
  ②**같은 모양을 격리 SQLite에서 재현**(§1-2)이다. prod 값 자체의 재확인은 완료 QA 몫이다.
- 프론트 테스트는 메인 저장소 `node_modules`를 빌려 돌렸다. `package.json` 해시가 동일함을 먼저 확인했다.
