// money.ts — 돈 표시 규칙 **한 벌**.
//
// ★왜 따로 뺐나 (2026-09-03, 적대 리뷰 P2-3): `formatCostWon`이 `pages/CostPage.tsx`에만
//   있어서, 같은 수를 그리는 전역 배너(`components/Layout.tsx`)가 **원문자열을 그대로 박고
//   있었다** — `/cost` 화면은 `-46,958.1원`, 배너는 `-46958.10원`. 같은 값이 두 화면에서
//   다르게 보이면 사람이 «다른 수»로 읽는다. 페이지를 배너가 import 하면 번들이 딸려오므로
//   규칙을 이 모듈로 내리고 양쪽이 여기서 가져간다(사본 두 벌을 만들지 않는다).

/** 단가·금액 표시. **`null`은 「—」다 — 0원으로 그리지 않는다**(계약 §2-7).
 *
 * 「값을 아직 모른다」와 「값이 0원이다」는 다른 사실이고, 화면이 둘을 같게 그리면
 * 그게 `cost_price` NOT NULL default 0이 만든 혼동의 재생산이다. */
export function formatCostWon(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${n.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}원`;
}
