// naverSymmetryFormat.ts — B5 대칭·탐색 관측 화면 표시 순수 함수 (D-NAO-247 점화 계약 B5)
//
// ══ 왜 순수 함수로 빼는가 ══
// 이 저장소 관례(guardrailParamsSave.ts) — 표시 규칙을 컴포넌트 JSX 안에 섞으면 0/null
// 경계값(표본 0, 파라미터 변경 이력 없음)이 렌더 스냅샷 테스트로만 스치듯 지나가고,
// «0을 안 그린다»·«null을 지어낸다» 같은 결함이 단위 테스트로 안 잡힌다.

/** 비율(0~1, null 가능)을 "N.N%" 문자열로. null은 값이 «없음»(표본 0)을 뜻하고
 * "0.0%"(측정했더니 0)와 다르다 — 절대 같은 문자열로 뭉개지 않는다. */
export function formatShare(x: number | null): string {
  if (x === null) return "표본 없음";
  return `${(x * 100).toFixed(1)}%`;
}

/** 액셀/브레이크 카운트 1건 → 한글 방향 라벨. 0건이어도 "브레이크 0건" 형태로 침묵하지
 * 않는다(교훈 #318) — 호출부가 항상 이 문자열을 렌더해야 한다. */
export function formatDirectionCount(brake: number, accel: number): string {
  return `브레이크 ${brake}건 · 액셀 ${accel}건`;
}

/** 액셀이 0인데 브레이크만 쌓이는 표류 경보(D-NAO-85 실측 재발 방지) — 판정이 아니라
 * «주의를 끌 만한 모양인가»만 반환한다(성과 판정 아님, B5 [판정불능 예약]과 무충돌). */
export function isBrakeOnlyDrift(brake: number, accel: number): boolean {
  return brake > 0 && accel === 0;
}
