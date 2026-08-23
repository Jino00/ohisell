// kpiEvidenceRequest.test.ts — 근거 요청 URL이 «조회 조건을 전부 싣는가»
// (계약 CONTRACT_kpi_evidence_page.md §2-5 · 적대 리뷰 2R 생존 변이 L)
//
// 왜 이 파일이 따로 있나: 표면 테스트(`kpiEvidenceSurface.test.tsx`)는 `fetchKpiEvidence`를
// **통째로 mock**한다 — 그래서 **URL 조립을 아무도 안 본다.** 적대 리뷰가 클라이언트에서
// `rocket_basis`를 떼는 변이를 넣었더니 프론트 전건이 초록으로 통과했다.
// 그 상태의 라이브 증상은 조용하다: 백엔드가 기본값 `settlement`로 답하므로 **에러 하나 없이**,
// 카드가 「판매(납품가)」 축인 날 근거 페이지만 계산서 축을 말한다 — 즉 근거가 카드와
// 다른 창을 말하면서 스스로는 「✓ 카드와 일치」라고 찍는다.
import { describe, it, expect, afterEach, vi } from "vitest";

import { fetchKpiEvidence } from "./api";

const calls: string[] = [];

afterEach(() => {
  calls.length = 0;
  vi.unstubAllGlobals();
});

function stubFetch() {
  vi.stubGlobal("fetch", (url: string) => {
    calls.push(String(url));
    return Promise.resolve({
      ok: true,
      status: 200,
      text: () => Promise.resolve("{}"),
    } as unknown as Response);
  });
}

describe("fetchKpiEvidence URL", () => {
  it("기간과 로켓 축을 전부 싣는다", async () => {
    stubFetch();
    await fetchKpiEvidence("2026-08-01", "2026-08-22", "sales");
    expect(calls).toHaveLength(1);
    const url = calls[0];
    expect(url).toContain("/api/dashboard/kpi/evidence");
    expect(url).toContain("date_from=2026-08-01");
    expect(url).toContain("date_to=2026-08-22");
    expect(url).toContain("rocket_basis=sales");
  });

  it("축이 바뀌면 URL도 바뀐다 — 조용히 기본값으로 떨어지지 않는다", async () => {
    stubFetch();
    await fetchKpiEvidence("2026-08-22", "2026-08-22", "settlement");
    await fetchKpiEvidence("2026-08-22", "2026-08-22", "sales");
    expect(calls[0]).not.toBe(calls[1]);
    expect(calls[0]).toContain("rocket_basis=settlement");
    expect(calls[1]).toContain("rocket_basis=sales");
  });
});
