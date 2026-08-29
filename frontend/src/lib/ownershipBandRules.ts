// ownershipBandRules.ts — 성과 화면 «관할 밴드» 필터 규칙 (성과분리 목표).
//
// ★규칙을 컴포넌트 안에 두지 않는 이유: 「판정을 못 받은 광고를 어느 밴드로 세는가」가 이
//   화면에서 가장 조용히 틀릴 수 있는 자리다. 컴포넌트 안에 있으면 렌더 테스트가 «필터가
//   동작한다»만 확인하고 **모름이 0으로 둔갑하는 것**은 못 잡는다.
import type { NaverOwnershipCampaignSlot } from "./api";

export type BandFilter = "all" | "pao" | "not_pao";

export const BAND_FILTER_LABEL: Record<BandFilter, string> = {
  all: "전체",
  pao: "PAO가 돌리는 광고",
  not_pao: "PAO가 안 돌리는 광고",
};

/** 판정을 받지 못한 캠페인인가 — 「모름」·「전환일」·slot 부재.
 *
 *  ★적대 리뷰 P1-3 상환. 종전엔 `slot === undefined`만 걸렀는데, 그러면 **백엔드가 「모름」이라고
 *    명시적으로 답한 경우**(이력 밖 날짜·해석불가·기록 모순)와 **장중 전환일**이 `pao_adgroups===0`
 *    이라는 이유로 「PAO가 안 돌리는 광고」에 그대로 담겼다. 모듈 주석과 테스트는 「모름을 0으로
 *    밀지 않는다」고 선언하면서 실제로는 그 명제의 절반만 지키고 있었다. */
export function isUndetermined(slot: NaverOwnershipCampaignSlot | undefined): boolean {
  return !slot || slot.band === "unknown" || slot.band === "transition";
}

/** 이 캠페인이 선택한 밴드에 드는가.
 *
 *  ★판정을 못 받은 캠페인은 **어느 밴드에도 넣지 않는다.** 「모름」을 「PAO 아님」으로 밀면
 *    모르는 것이 아는 것으로 둔갑한다(원칙22). `all`이 아닌 필터에서는 빠지고 `all`에선 보인다.
 *  ★부분 관할(그룹 일부만 PAO)은 **PAO 쪽**이다 — Jino: *"광고그룹만도 가져올 수 있잖아"*.
 *    그 캠페인은 「PAO가 돌리는 광고」 목록에 있어야 하고, 몇 개 그룹인지는 배지가 말한다. */
export function matchesBandFilter(
  slot: NaverOwnershipCampaignSlot | undefined,
  filter: BandFilter,
): boolean {
  if (filter === "all") return true;
  if (isUndetermined(slot)) return false;
  return filter === "pao" ? isPaoSide(slot!) : !isPaoSide(slot!);
}

/** 이 캠페인이 «PAO 쪽»인가 — 필터와 배지 색이 **같은 술어**를 봐야 목록과 색이 안 갈라진다. */
export function isPaoSide(slot: NaverOwnershipCampaignSlot): boolean {
  return slot.pao_adgroups > 0;
}

/** 필터가 «가려낸» 광고 수 — 목록에서 빠진 것이 몇 개인지 화면이 말해야 한다.
 *  숨기면서 아무 말 안 하면 「없다」와 구별이 안 된다. */
export function undeterminedCount(
  campaignIds: string[],
  slotOf: (id: string) => NaverOwnershipCampaignSlot | undefined,
): number {
  return campaignIds.filter((id) => isUndetermined(slotOf(id))).length;
}

/** 카드 배지 문구. 부분 관할은 분모를 함께 보여준다 — 「일부만」이 숫자로 읽혀야 한다. */
export function ownershipBadgeText(slot: NaverOwnershipCampaignSlot): string {
  return slot.partial
    ? `그날 PAO 부분 담당 (${slot.pao_adgroups}/${slot.adgroups} 그룹)`
    : `그날 ${slot.label}`;
}

/** 목록이 비었을 때 «왜 비었나»를 밴드에 맞게 말한다(빈 화면은 이유를 말해야 한다).
 *
 *  ★가려낸 광고가 있으면 「없다」고 단언하지 않는다 — 판정 못 한 것을 없는 것으로 말하면
 *    그것도 모름을 0으로 미는 것이다(적대 리뷰 P1-3). */
export function emptyReasonFor(filter: BandFilter, undetermined = 0): string {
  const base =
    filter === "pao"
      ? "이 날짜에 PAO가 돌린 광고가 없습니다."
      : filter === "not_pao"
        ? "이 날짜에 PAO 밖 광고가 없습니다."
        : "집행된 광고가 없습니다.";
  if (filter !== "all" && undetermined > 0) {
    return `${base.replace(/없습니다\.$/, "확인되지 않습니다.")} 담당을 판정할 수 없는 광고 ${undetermined}개는 목록에서 뺐습니다.`;
  }
  return base;
}
