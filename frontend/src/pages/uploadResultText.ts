// uploadResultText.ts — 엑셀 업로드 결과 배너 문구 조립(순수 함수).
//
// ## 왜 컴포넌트에서 뺐나 (2026-08-10, D-CPP-35 적대 리뷰 2R P2-N1)
//
// 원가 버퍼 차단을 붙였는데, 문구가 컴포넌트 안에 인라인이라 **회귀로 못 박히지 않았다**:
// 배너에서 원가 거부를 빼는 변이 2종이 프론트 290건에서 전부 살아남았다. 즉 P1(사유가
// 화면에 도달하지 않음)이 다시 나도 또 우연히 발견해야 한다.
//
// ★이 파일이 지키는 것은 «표시»다. 백엔드가 아무리 정확히 거부해도 운영자에게 닿지
//   않으면 그 사람은 옛 엑셀을 계속 쓴다 — 거부의 값어치는 전부 이 마지막 한 칸에 있다.
export interface MappingUploadCounts {
  products_created: number;
  products_updated: number;
  mappings_created: number;
  mappings_updated: number;
  mappings_conflicted: number;
  orders_linked: number;
  unknown_labels: string[];
  duplicate_product_names: string[];
  duplicate_channel_ids: string[];
  mapping_conflicts: string[];
  label_mismatches: string[];
  cost_buffers: string[];
  cost_guard_unavailable: string | null;
}

/** 「확인 필요 N건」에 세는 무결성 이슈 수. ★원가 거부는 여기 넣지 않는다(따로 앞세운다). */
export function countMappingIssues(r: MappingUploadCounts): number {
  return (
    r.unknown_labels.length +
    r.duplicate_product_names.length +
    r.duplicate_channel_ids.length +
    r.mapping_conflicts.length +
    r.label_mismatches.length
  );
}

/**
 * 연결맵 마스터 엑셀(`/upload-by-name`) 결과 문구.
 *
 * ★원가 거부를 「확인 필요 N건(콘솔)」에 섞지 않는다. 섞으면 「수정 N」 옆에서 묻히는데,
 *   운영자가 읽어야 할 것은 «내 엑셀의 원가가 반영되지 않았다»이기 때문이다.
 */
export function buildMappingUploadText(r: MappingUploadCounts): string {
  const issues = countMappingIssues(r);
  const blocked = r.cost_buffers?.length ?? 0;
  return (
    `상품 생성 ${r.products_created} · 수정 ${r.products_updated} · 매핑 ${r.mappings_created} · 주문연결 ${r.orders_linked}` +
    (r.mappings_conflicted ? ` / 충돌 ${r.mappings_conflicted}(기존 유지)` : "") +
    (blocked
      ? ` / ⚠️ 원가 거부 ${blocked}건 — 버퍼가 얹힌 값이라 원가를 반영하지 않았다(매핑은 반영됨, 사유는 콘솔)`
      : "") +
    (r.cost_guard_unavailable ? ` / ⚠️ 원가 미검사(${r.cost_guard_unavailable})` : "") +
    (issues ? ` / 확인 필요 ${issues}건(콘솔)` : "")
  );
}

/** 콘솔 경고를 띄울지. ★원가 거부·미검사가 게이트에 없으면 콘솔에서도 사라진다. */
export function shouldWarnMappingUpload(r: MappingUploadCounts): boolean {
  return (
    countMappingIssues(r) > 0 ||
    (r.cost_buffers?.length ?? 0) > 0 ||
    Boolean(r.cost_guard_unavailable)
  );
}

export interface CostSheetUploadCounts {
  created: number;
  updated: number;
  mappings_created: number;
  errors?: string[];
  cost_buffer_blocked?: number;
  cost_guard?: string;
}

/**
 * 상품 원가표 엑셀(`/upload`) 결과 문구.
 *
 * ★`cost_guard`가 "active"가 아니면 이 업로드의 원가는 «검사 통과»가 아니라 «미검사»다.
 *   그 둘이 같은 모양이면 안 된다(교훈 #123).
 */
export function buildCostSheetUploadText(r: CostSheetUploadCounts): string {
  const guardOff = Boolean(r.cost_guard) && r.cost_guard !== "active";
  return (
    `생성 ${r.created}건, 수정 ${r.updated}건, 매핑 ${r.mappings_created}건` +
    (r.cost_buffer_blocked ? ` / ⚠️ 원가 거부 ${r.cost_buffer_blocked}건(버퍼 — 행 미반영)` : "") +
    (guardOff ? ` / ⚠️ 원가 미검사(${r.cost_guard})` : "") +
    (r.errors?.length ? ` / 오류 ${r.errors.length}건` : "")
  );
}
