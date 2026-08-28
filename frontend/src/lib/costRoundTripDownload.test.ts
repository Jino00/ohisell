// costRoundTripDownload.test.ts — 다운로드 API 함수 «자체»를 잡는다 (계약 D-CPP-62 S3)
//
// ★왜 이 파일이 따로 있나 (적대 리뷰 P2-1, 2026-08-28 채택)
//   화면 테스트(`costHomeSurface.test.tsx`)는 `downloadCostRoundTrip`을 **통째로 mock**한다 —
//   그래야 「버튼→저장」 배선을 잴 수 있다. 그 대가로 **이 함수 안쪽은 한 번도 안 밟힌다.**
//   리뷰어가 `parseContentDispositionFilename`의 정규식 순서를 뒤집는 변이를 넣었더니
//   지정된 3개 스위트 70건이 **전부 초록**이었다(SURVIVED).
//
//   ★그 함수의 docstring이 *"순서를 뒤집으면 파일이 언제나 `CRT-12.xlsx`로 떨어진다"*고
//   **자기 입으로 경고**하고 있었는데도 그걸 지키는 테스트가 없었다. 자백은 방어가 아니다.
//
// ★그래서 여기서는 mock을 «한 칸 아래»에 둔다: `fetch`만 가짜로 세우고
//   `downloadCostRoundTrip` 본체는 **진짜로 실행**한다.
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  downloadCostRoundTrip,
  parseContentDispositionFilename,
} from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("parseContentDispositionFilename — 한글 이름은 RFC 5987 쪽에만 실린다", () => {
  it("★`filename*`(UTF-8)을 `filename`(ASCII)보다 **먼저** 본다", () => {
    // 백엔드가 실제로 내려보내는 모양 그대로다(`cost_menu.py:roundtrip_download`).
    const header =
      `attachment; filename="CRT-7.xlsx"; ` +
      `filename*=UTF-8''${encodeURIComponent("원가_왕복_CRT-7_20260828_1405.xlsx")}`;
    // 순서를 뒤집으면 여기서 `CRT-7.xlsx`가 나온다 — 사람이 받은 파일에서
    // 「언제 받은 것인가」를 못 읽게 된다.
    expect(parseContentDispositionFilename(header)).toBe(
      "원가_왕복_CRT-7_20260828_1405.xlsx",
    );
  });

  it("`filename*`이 없으면 ASCII로 내려간다", () => {
    expect(parseContentDispositionFilename('attachment; filename="CRT-7.xlsx"')).toBe(
      "CRT-7.xlsx",
    );
  });

  it("인코딩이 깨져 있으면 **지어내지 않고** ASCII 폴백으로 간다", () => {
    const header = `attachment; filename="CRT-7.xlsx"; filename*=UTF-8''%E0%A4%A`;
    expect(parseContentDispositionFilename(header)).toBe("CRT-7.xlsx");
  });

  it("헤더가 없으면 null이다 — 빈 문자열을 파일명으로 쓰지 않는다", () => {
    expect(parseContentDispositionFilename(null)).toBeNull();
    expect(parseContentDispositionFilename("attachment")).toBeNull();
  });
});

describe("downloadCostRoundTrip — 함수 본체를 실제로 실행한다", () => {
  function stubFetch(init: {
    ok?: boolean;
    status?: number;
    headers?: Record<string, string>;
    body?: string;
  }) {
    const headers = new Headers(init.headers ?? {});
    const spy = vi.fn(async () => ({
      ok: init.ok ?? true,
      status: init.status ?? 200,
      headers,
      blob: async () => new Blob([init.body ?? "xlsx"]),
      text: async () => init.body ?? "",
    }));
    vi.stubGlobal("fetch", spy);
    return spy;
  }

  it("★POST로 부르고 **쿼리를 하나도 안 붙인다** — 필터가 파일에 닿을 길이 없다", async () => {
    const spy = stubFetch({
      headers: {
        "Content-Disposition":
          `attachment; filename="CRT-9.xlsx"; ` +
          `filename*=UTF-8''${encodeURIComponent("원가_왕복_CRT-9.xlsx")}`,
        "X-Snapshot-Id": "CRT-9",
      },
    });

    const got = await downloadCostRoundTrip();

    expect(spy).toHaveBeenCalledTimes(1);
    const [url, options] = spy.mock.calls[0] as unknown as [string, RequestInit];
    // 부분집합 파일이 나가면 재업로드에서 빠진 종이 전부 「사라짐」에 선다.
    expect(url).toMatch(/\/api\/cost\/roundtrip\/download$/);
    expect(url).not.toContain("?");
    expect(options.method).toBe("POST");

    expect(got.filename).toBe("원가_왕복_CRT-9.xlsx");
    expect(got.snapshotId).toBe("CRT-9");
  });

  it("헤더가 이름을 못 주면 스냅샷 ID로 짓는다 — 이름 없는 파일을 만들지 않는다", async () => {
    stubFetch({ headers: { "X-Snapshot-Id": "CRT-3" } });
    expect((await downloadCostRoundTrip()).filename).toBe("CRT-3.xlsx");
  });

  it("스냅샷 ID조차 없으면 그래도 이름이 있다", async () => {
    stubFetch({});
    const got = await downloadCostRoundTrip();
    expect(got.snapshotId).toBeNull();
    expect(got.filename).toBe("원가_왕복.xlsx");
  });

  it("실패하면 **던진다** — 빈 Blob을 파일인 척 내려보내지 않는다", async () => {
    stubFetch({ ok: false, status: 500, body: "boom" });
    await expect(downloadCostRoundTrip()).rejects.toThrow(/500/);
  });
});
