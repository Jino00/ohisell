// naverParamChangeApproval.ts — param_change 제안 승인 시 보낼 applied_value 조립 (D-NAO-249 F1)
//
// ══ 왜 순수 함수로 빼는가 ══
// 「승인=적용」 사슬에서 반영될 값은 **사람이 정한다**(D-NAO-249 확정) — 판사(지혜)는 파라미터
// 키와 방향(direction)만 정하고, 크기는 코드가 발명하지 않는다. 그래서 프리필은 항상 «지금
// 봉투 현황판에 떠 있는 현재값»이어야지 「제안이 권하는 값」이면 안 된다. 이 규칙과 lo~hi
// 클램프 판단을 컴포넌트 JSX 안에 묻으면 렌더 경로마다(pending 최초 승인 · failed 재승인)
// 따로 구현되어 갈라질 위험이 있다.
//
// ★guardrailParamsSave.ts(전체 치환 PUT)와는 클램프 방침이 다르다 — 그건 "범위는 서버가
//   판정한다"였다. 여기서는 **승인이라는 되돌리기 어려운 실행**이라 화면에서 먼저 막는다
//   (요청 사양 F1). 서버도 같은 범위를 400으로 막으므로 두 검사가 갈라져도 서버가 최종 판정.

export type ParamSpecForApproval = {
  key: string;
  value: number;
  min: number;
  max: number;
};

export type ApplyValueResult =
  | { ok: true; value: number }
  | { ok: false; error: string };

/**
 * 입력 문자열을 파싱해 param_change 승인 body에 실을 applied_value를 만든다.
 * spec이 없으면(봉투 현황판에서 이 키를 못 찾음) 범위 검증 없이 숫자 파싱만 한다 —
 * 그 경우는 화면이 따로 경고를 낸다(스펙 확인 불가 상태를 화면이 표시).
 */
export function buildApplyValue(
  raw: string,
  spec: ParamSpecForApproval | undefined,
): ApplyValueResult {
  const trimmed = raw.trim();
  if (trimmed === "") return { ok: false, error: "값을 입력하세요" };
  const num = Number(trimmed);
  if (Number.isNaN(num)) return { ok: false, error: "숫자가 아닙니다" };
  if (spec && (num < spec.min || num > spec.max)) {
    return { ok: false, error: `허용 범위 ${spec.min} ~ ${spec.max} 밖입니다` };
  }
  return { ok: true, value: num };
}

/**
 * 승인 카드 입력칸의 프리필 값 — **현재값**이지 제안이 권하는 값이 아니다(판사는 키·방향만
 * 정한다, 크기는 사람이 정한다 — 위 헤더 주석 참조). 사람이 이미 이 카드에서 값을 고쳤으면
 * (edited != null) 그 편집값을 우선한다.
 */
export function prefillApplyValue(
  spec: ParamSpecForApproval | undefined,
  edited: string | undefined,
): string {
  if (edited != null) return edited;
  return spec ? String(spec.value) : "";
}
