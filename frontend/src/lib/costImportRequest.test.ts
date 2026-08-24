// costImportRequest.test.ts — 엑셀 한쪽만 올릴 때 «몸통이 무엇을 싣는가»
// (Jino 2026-08-24 *"여기서 둘중에 하나만도 업데이트가 되게 해줘"* · 적대 리뷰 P2 채택)
//
// 왜 이 파일이 따로 있나 — `kpiEvidenceRequest.test.ts`와 **정확히 같은 이유**다:
// 표면 테스트(`costPageReachesTheUser.test.tsx`)는 `RecipeImportPanel`의 `onImport` prop을
// mock으로 잡아 우회하므로 **`importCostRecipes` 함수 본체를 아무도 실행하지 않는다.**
// 적대 리뷰가 `if (costFile)` / `if (mappingFile)` 가드를 지우는 변이를 넣었더니
// 저장소 전체가 초록이었다(변이 7 SURVIVED).
//
// 그 상태의 라이브 증상: 안 고른 슬롯에 `null`이 `FormData`로 들어가면 문자열 `"null"`이
// 파일 이름으로 붙어 서버는 「**올렸는데 파싱 실패**」로 읽는다 — 즉 「한쪽만 올림」이
// 「깨진 파일을 올림」으로 바뀌어, 사람은 400을 보고 자기 엑셀을 의심하게 된다.
import { describe, it, expect, afterEach, vi } from "vitest";

import { importCostRecipes } from "./api";

const bodies: FormData[] = [];

afterEach(() => {
  bodies.length = 0;
  vi.unstubAllGlobals();
});

function stubFetch() {
  vi.stubGlobal("fetch", (_url: string, init?: RequestInit) => {
    bodies.push(init?.body as FormData);
    return Promise.resolve({
      ok: true,
      status: 200,
      text: () => Promise.resolve("{}"),
    } as unknown as Response);
  });
}

function xlsx(name: string): File {
  return new File(["x"], name, {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
}

describe("importCostRecipes — 안 고른 슬롯은 «아예 안 붙인다»", () => {
  it("원가 정본만: cost_file만 실리고 mapping_file은 «키 자체가 없다»", async () => {
    stubFetch();
    await importCostRecipes(xlsx("cost.xlsx"), null);
    const form = bodies[0];
    expect(form.has("cost_file")).toBe(true);
    // ★`null`이 아니라 **키가 없어야** 한다 — FastAPI의 `File(None)`은 «키 없음»만
    //   `None`으로 읽고, 빈 값이 붙으면 「올렸는데 파싱 실패」가 된다.
    expect(form.has("mapping_file")).toBe(false);
  });

  it("매핑 정본만: mapping_file만 실리고 cost_file은 키 자체가 없다", async () => {
    stubFetch();
    await importCostRecipes(null, xlsx("map.xlsx"));
    const form = bodies[0];
    expect(form.has("mapping_file")).toBe(true);
    expect(form.has("cost_file")).toBe(false);
  });

  it("둘 다 고르면 둘 다 실린다 — 한쪽만 허용이 «양쪽 경로»를 깨지 않았다", async () => {
    stubFetch();
    await importCostRecipes(xlsx("cost.xlsx"), xlsx("map.xlsx"));
    const form = bodies[0];
    expect(form.has("cost_file")).toBe(true);
    expect(form.has("mapping_file")).toBe(true);
  });
});
