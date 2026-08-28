// costMenuSurface.ts — 원가 메뉴 D-CPP-60의 «순수 표시 규칙» 중 CostPage.tsx **밖**에 두는 것.
//
// ★왜 CostPage.tsx에 안 넣나: 그 파일은 컴포넌트도 export하는 .tsx라
//   `react-refresh/only-export-components`가 non-component export 1개마다 경고 1건을 낸다.
//   실측(2026-08-26): 프로젝트 전체 eslint 경고가 정확히 CI 상한(`--max-warnings 96`)에
//   닿아 있고 그중 CostPage.tsx 한 파일이 28건이다 — **이 파일에 pure export를 하나라도
//   더 얹으면 CI가 그 자리에서 빨간불이 된다.** 순수 .ts 파일은 이 규칙 대상이 아니므로
//   (컴포넌트를 export하지 않는 파일은 애초에 검사 대상이 아니다), 새 순수 함수 넷은 여기
//   두고 CostPage.tsx는 **가져다 쓰기만** 한다(재-export도 안 한다 — 재-export도 같은
//   경고를 낸다는 것을 직접 확인했다). 테스트도 이 파일에서 바로 들여온다.
//
// 정본은 여전히 CostPage.tsx의 관례("순수 함수 export → 컴포넌트가 부른다")다 — 이 파일은
// 그 관례를 지키기 위한 **위치만** 옮긴 것이지 새 관례가 아니다.
import type {
  CostAutoRefreshRun,
  CostMaterial,
  CostSetting,
} from "./api";

/** `formatCostWon`(CostPage.tsx)과 **같은 규칙**의 로컬 사본이다 — 순환 임포트를 피하려고
 *  둔다(CostPage.tsx가 이 파일을 가져다 쓰는데, 이 파일이 다시 CostPage.tsx를 가져오면
 *  순환이 생긴다). `null`은 「—」다(계약 §2-7). */
function won(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${n.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}원`;
}

/** 백엔드가 `datetime.now()`로 찍는 naive datetime을 KST로 표시한다.
 *
 * ★왜 그냥 `new Date(iso)`가 아닌가: 프로덕션 서버 로컬 TZ는 **UTC**다
 * (`scheduler_service.py`의 `CronTrigger.from_crontab(..., timezone="Asia/Seoul")` 주석이
 * "from_crontab는 timezone 미지정 시 서버 로컬 TZ(프로덕션=UTC)로 …"라고 명시). `datetime.now()`는
 * tzinfo가 없어 ISO 문자열에 오프셋이 없다 — 그걸 그대로 `new Date()`에 넣으면 브라우저가
 * **로컬(대개 KST)** 시각으로 오독해 실제보다 9시간 앞선 값이 뜬다. 오프셋이 없는 문자열은
 * UTC로 못박고(`Z`를 붙여 파싱) KST로 환산한다. */
export function formatKstDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const hasTz = /Z$|[+-]\d{2}:\d{2}$/.test(iso);
  const d = new Date(hasTz ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(d);
}

/** 회전의 방아쇠 → 사람 말. 화면에 트리거 코드(`event`/`cron`/`manual`)를 그대로 노출하지
 *  않는다 — 사람이 무슨 일이 있었는지 알아야 한다(교훈 #349와 같은 결). */
export function triggerLabel(trigger: string): string {
  if (trigger === "cron") return "일일 sweep";
  if (trigger === "manual") return "지금 검사";
  if (trigger === "event") return "로트 확정 직후";
  return trigger;
}

/** 층1 표준원가가 «지금 쓰는» 단가 규칙(계약 §4-① 두 번째 줄).
 *
 * ★**「FIFO」·「선입선출」로 부르지 않는다**(계약 §3 금지선) — 최신 로트는 선입선출과
 *  방향이 반대다(최신 것을 쓰지 가장 오래된 것을 쓰지 않는다). `valuationBadgeText`(법정
 *  평가방법 신고 자백)와 **다른 사실**이라 문장도 다르다: 이건 「우리가 실제로 계산에 쓰는
 *  규칙」이고, 그건 「국세청에 뭐라고 신고했나」다. 둘을 섞으면 그 자체가 §3 위반이다. */
export function standardPriceRuleText(settings: CostSetting[]): string | null {
  const s = settings.find((x) => x.key === "standard_price_rule");
  if (!s) return "층1 표준원가가 지금 쓰는 단가: 설정 없음 — 확인 안 됨";
  const label = s.value === "latest" ? "최신 로트 단가" : s.value;
  return `층1 표준원가가 지금 쓰는 단가: ${label} — 재고 원장(C1) 가동 전`;
}

/** 관측 로트 구간 자백(계약 §4-⑥). 폭이 없으면(로트 1건 이하, 또는 값이 같음)
 *  구간을 지어내지 않는다 — `null`을 돌려 배지 자체를 안 그린다. */
export function lotSpanText(
  m: Pick<CostMaterial, "lot_price_min" | "lot_price_max" | "lot_price_has_span" | "latest_price_ex_vat">,
): string | null {
  if (!m.lot_price_has_span) return null;
  const minLabel =
    m.lot_price_min !== null && Number.isFinite(Number(m.lot_price_min))
      ? Number(m.lot_price_min).toLocaleString("ko-KR", { maximumFractionDigits: 2 })
      : "—";
  return `최신 로트 단가 ${won(m.latest_price_ex_vat)} · 관측 로트 구간 ${minLabel}~${won(m.lot_price_max)} · 재고 원장 가동 전`;
}

/** §2-5 자백 — 채택은 원장 값인데 더 늦은 수동 입력이 있다. 그 수동 입력은 **채택되지
 *  않았다**는 사실을 문장이 스스로 말한다(침묵하면 「어느 값이 진짜냐」가 결함 조사로 번진다). */
export function priceConflictText(m: Pick<CostMaterial, "price_conflict">): string | null {
  if (!m.price_conflict) return null;
  return "채택: 원장 값 — 더 늦은 수동 입력이 있다(수동 입력은 채택되지 않았다)";
}

/** 자동 갱신 맨 위 한 줄 — **「갱신 0건」과 「한 번도 안 돎」을 반드시 구별한다.**
 *
 * ★이 구별이 이 저장소가 반복 실측한 fail-open이다: 회전 자체가 없는 것과, 회전은 돌았는데
 *  바뀔 게 없었던 것이 같은 화면으로 보이면 「자동이 죽었다」를 아무도 못 알아챈다(§2-6). */
export function sweepSummaryText(runs: CostAutoRefreshRun[]): string {
  if (runs.length === 0) {
    return "아직 한 번도 안 돌았다 — 「지금 검사」를 누르거나 09:40 sweep을 기다린다";
  }
  const latest = runs[0];
  return (
    `최근 검사: ${formatKstDateTime(latest.started_at)} (${triggerLabel(latest.trigger)}) · ` +
    `검사 ${latest.checked}종 · 갱신 ${latest.updated}건 · 실패 ${latest.failed}건 · 대기 ${latest.queued}건`
  );
}

/**
 * 페이지 폭 상한 — 가르는 축은 「홈이냐」가 아니라 **「넓은 표냐 폼·목록이냐」**다.
 * `home`(왕복 표 12열 × 139행)·`board`(9열 × 924 SKU)·`materials`(8열 표가 **최대 3벌**)는
 * 폭이 다 필요하고, `recipes`만 `max-w-[96rem]`(1536px) 상한을 유지한다.
 *
 * ★2026-08-28 정정 — 이 주석은 원래 «`materials`·`recipes`는 폼·목록 위주»라고 적혀 있었고
 *   그게 **사실이 아니었다**: 부자재 탭 오른쪽 `1fr` 칼럼에 8열 표(단가 이력·원장 라인)가
 *   최대 3벌 동시에 뜨고, 96rem에서 왼쪽 22~28rem을 빼면 오른쪽은 ≈1,000px뿐이라 품목명이
 *   짜부라지고 날짜가 두 줄이 됐다. ⇒ `materials`도 상한을 푼다.
 *
 * ★반대로 `recipes`는 **상한을 풀어도 안 낫는다** — 거기서 잘리는 것은 왼쪽 레시피 목록의
 *   상품명인데 그 칼럼이 `320px` **고정**이었기 때문이다(페이지가 넓어져도 1px도 안 넓어진다).
 *   그 자리는 `minmax(320px,28rem)`으로 따로 고쳤다(`CostPage.tsx`의 recipes 그리드).
 *   **「넓히면 낫는다」가 아니라 「무엇이 폭을 안 받고 있나」를 봐야 하는 자리였다.**
 *
 * ★함수로 뽑은 이유는 순전히 **테스트가 잡을 수 있게** 하려는 것이다(2026-08-28 적대 리뷰 P2-2).
 *   인라인 삼항으로 두었을 때 「보드 탭 상한 해제」를 되돌리는 변이가 프론트 1,173건 전건 초록
 *   속에서 **살아남았다** — Jino가 «글자가 이렇게 잘리네»라고 한 잘림의 절반이 이 상한이었는데
 *   그 절반을 아무도 안 지키고 있었다. 최상위 wrapper의 className은 `<CostPage>`를 통째로
 *   렌더해야 닿는 자리라 어느 테스트도 보지 않았다.
 *
 * ★그리고 **이 파일에** 둔 이유는 이 파일 헤더가 이미 적어 둔 그대로다: `CostPage.tsx`에
 *   순수 export를 하나 더 얹으면 `react-refresh/only-export-components` 경고가 1건 늘고
 *   CI의 warning 래칫이 그 자리에서 빨간불이 된다(2026-08-28 실측 — 96→97로 실제로 터졌다).
 *
 * 탭 이름은 `CostPage.tsx`의 `CostTab`과 같은 값이다. 타입을 import하면 lib→pages 방향의
 * 의존이 생기므로(이 파일이 `formatCostWon`을 로컬 사본으로 둔 것과 같은 이유) 여기서는
 * 유니온을 그대로 적는다 — 값이 갈라지면 `costPageWidthClass`를 부르는 쪽에서 타입이 걸린다.
 */
export function costPageWidthClass(tab: "home" | "materials" | "recipes" | "board"): string {
  return tab === "recipes" ? "p-6 max-w-[96rem]" : "p-6";
}
