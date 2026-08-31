// guardrailParamsSave.ts — 봉투 파라미터 저장 본문 조립 (D-NAO-172 P1)
//
// ══ 왜 순수 함수로 빼는가 ══
// `PUT /api/naver/ad/settings/guardrail-params`는 **전체 치환**이다 — 넘긴 키만 남고 나머지는
// 코드 상수로 돌아간다. 그래서 화면에서 파라미터 하나만 고쳐 저장하면, 이미 설정돼 있던 다른
// 파라미터가 **조용히 기본값으로 리셋된다.** 그건 이 기능이 막으려던 바로 그 사고(봉투가
// 아무도 모르게 바뀌는 것)라, 조립 규칙을 컴포넌트 안에 묻어 두면 안 된다.
//
// 규칙: 보낼 본문 = (사람이 손댄 키의 새 값) ∪ (안 손댔지만 이미 `source==="db"`인 키의 현재 값)
// `rejected` 행은 일부러 뺀다 — DB에 있지만 범위 밖이라 폴백된 값이므로, 저장 시 정리되는 게 맞다.
//
// ★D-NAO-282 — `source`에 `"env"`가 생겼다(우선순위 DB > env > 코드 상수). **env 행은 `"code"`와
// 같이 취급한다 = 안 건드렸으면 안 보낸다.** 보내면 서버 환경변수 값이 조용히 DB로 «승격»되어
// 출처가 env→db로 바뀐다 — 사람이 만진 적 없는 값이 만진 값으로 둔갑하는 것이고, 이 파일이
// 애초에 막으려던 사고(봉투가 아무도 모르게 바뀌는 것)와 같은 종류다. 안 보내면 DB에 값이 없어
// 서버가 다시 env로 내려가므로 **실효값은 그대로**다.

export type GuardrailParamRow = {
  key: string;
  label: string;
  value: number;
  source: "db" | "env" | "code";
  rejected: boolean;
};

export type BuildResult =
  | { ok: true; body: Record<string, number> }
  | { ok: false; error: string };

/**
 * @param params   서버가 준 현재 상태(GET 응답의 params)
 * @param edits    key → 입력칸 문자열
 * @param touched  key → 사람이 실제로 건드렸는가
 */
export function buildGuardrailSaveBody(
  params: GuardrailParamRow[],
  edits: Record<string, string>,
  touched: Record<string, boolean>,
): BuildResult {
  const body: Record<string, number> = {};
  for (const p of params) {
    if (touched[p.key]) {
      const raw = (edits[p.key] ?? "").trim();
      const num = Number(raw);
      // 숫자가 아닌 입력만 여기서 막는다. min/max 판정은 **서버 몫**이다 —
      // 프론트가 범위를 복제하면 두 곳이 갈라지고, 갈라지면 서버가 이긴다(사람은 이유를 모른다).
      if (raw === "" || Number.isNaN(num)) {
        return { ok: false, error: `「${p.label}」 값이 숫자가 아닙니다` };
      }
      body[p.key] = num;
    } else if (p.source === "db" && !p.rejected) {
      // 안 건드렸지만 이미 설정돼 있던 값 — 전체 치환에 쓸려 사라지지 않게 되돌려 보낸다.
      body[p.key] = p.value;
    }
  }
  return { ok: true, body };
}
