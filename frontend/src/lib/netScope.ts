// netScope.ts — 요약표 행이 「이 순이익이 무엇인가」를 말하게 하는 판정 (D-22, 2026-08-19)
//
// 왜 컴포넌트 밖으로 뺐나: 적대 리뷰 1R 변이 주입에서 `Dashboard.tsx`의 net_scope 분기를
// 통째로 지워도 프론트 테스트 445개가 전부 초록이었다(그 파일을 참조하는 테스트가 0개).
// 판정을 순수 함수로 빼면 테스트가 붙고, 붙으면 다음에 누가 지울 때 깨진다.
import type { GroupedSummaryRow } from "./api";

export type RowNote = { text: string; title: string };

const won = (n: number) => `${Math.round(n).toLocaleString("ko-KR")}원`;

/**
 * 순이익 칸 아래에 붙는 자백.
 *  ad_only  = 이 행의 손익은 **광고비만 반영된 하한**이다(매출·원가가 손익에 안 닿는 축).
 *  partial  = 소계 안에 그런 하한이 섞여 있다.
 * 이 자백이 없으면 하한을 완전한 손익으로 오독한다 — 그게 이 작업의 발단이었다.
 */
export function netScopeNote(row: Pick<GroupedSummaryRow, "net_scope" | "net_floor_ad">): RowNote | null {
  const floorAd = Number(row.net_floor_ad ?? 0);
  if (row.net_scope === "ad_only") {
    return {
      text: "광고비만(하한)",
      title:
        "이 축에서는 매출·원가가 손익에 안 닿는다. 확정 비용인 광고비만 반영한 하한이다 — " +
        "실제 손익은 「판매(납품가)」 축으로 본다.",
    };
  }
  if (row.net_scope === "partial" && floorAd > 0) {
    return {
      text: `하한 포함 ${won(floorAd)}`,
      title: `이 소계에는 손익을 못 잰 채 광고비만 반영된 ${won(floorAd)}이 섞여 있다 — 실제 손익의 하한이다`,
    };
  }
  return null;
}

/**
 * 이익률 칸 아래에 붙는 경고 — 원가를 못 붙인 매출은 이익률을 **위로만** 흔든다.
 * 표 상단 배너는 전체 합계라 어느 행이 범인인지 못 말한다(자사몰 67.4%의 정체가 그것이었다).
 */
export function unmappedNote(
  row: Pick<GroupedSummaryRow, "unmapped_revenue" | "product_revenue">,
): (RowNote & { pct: number }) | null {
  const unmapped = Number(row.unmapped_revenue ?? 0);
  const prodRev = Number(row.product_revenue ?? 0);
  if (unmapped <= 0 || prodRev <= 0) return null;
  const pct = (unmapped / prodRev) * 100;
  return {
    pct,
    text: `⚠️ 원가 미상 ${pct.toFixed(1)}%`,
    title: `제품매출 ${won(prodRev)} 중 ${won(unmapped)}이 원가 0으로 계산됐다 — 이 이익률은 실제보다 높다`,
  };
}
