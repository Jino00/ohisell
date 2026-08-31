// purchasedPriceApi.test.ts — 400의 «사유»가 살아서 올라오는가 (적대 리뷰 P2-3)
//
// ★화면 테스트(`costPurchasedPriceSurface.test.tsx` P8)는 `previewPurchasedPrices` 자체를
//   mock 하므로 **이 층을 한 번도 안 지난다** — `await res.text()`를 지우는 변이(M9)가
//   1,228개 초록 속에서 살아남았다. 08-22판을 올린 사람이 「원가 열이 없다」를 못 읽게
//   되는 변이가 무방비였다. 그래서 mock을 `fetch`로 한 칸 내려 이 파일에서 따로 잡는다.
//   (n=17이 `parseContentDispositionFilename`에서 밟은 것과 정확히 같은 모양이다.)
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { previewPurchasedPrices } from "./api";

function file() {
  return new File(["x"], "ohisell_mapping_template_20260822.xlsx");
}

beforeEach(() => vi.unstubAllGlobals());
afterEach(() => vi.unstubAllGlobals());

it("400이면 서버가 준 사유를 그대로 실어 던진다", async () => {
  const detail =
    '{"detail":"「원가」 열이 이 판에는 없다 — 08-22판처럼 상품명/옵션명만 있는 판일 수 있다."}';
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: false,
      status: 400,
      text: async () => detail,
      json: async () => JSON.parse(detail),
    })) as unknown as typeof fetch,
  );

  await expect(previewPurchasedPrices(file())).rejects.toThrow(
    /「원가」 열이 이 판에는 없다/,
  );
});

it("본문이 비면 상태코드라도 말한다 — 조용한 실패 금지", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: false,
      status: 500,
      text: async () => "",
      json: async () => ({}),
    })) as unknown as typeof fetch,
  );

  await expect(previewPurchasedPrices(file())).rejects.toThrow(/500/);
});

it("성공하면 payload를 그대로 돌려준다", async () => {
  const payload = { source_file: "f.xlsx", counts: { groups: 0 } };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      status: 200,
      text: async () => JSON.stringify(payload),
      json: async () => payload,
    })) as unknown as typeof fetch,
  );

  await expect(previewPurchasedPrices(file())).resolves.toEqual(payload);
});
