// costHomeColumnParity.test.ts — 왕복 표 열 스펙의 **파리티 게이트** (계약 D-CPP-62 S3)
//
// ★왜 이 파일이 있나
//   화면은 프론트의 `ROUND_TRIP_COLUMNS`로 그려지고, 다운로드 파일은 백엔드가
//   `round_trip_columns.json`을 **런타임에 읽어** 만든다. 즉 열 스펙이 두 벌이다.
//   두 벌이 조용히 갈라지는 것이 이 저장소의 상습 결함이다 — 표준원가 라인 직렬화기가
//   두 벌(`standard_cost.py` ↔ `recipes.py:standard_payload`)이라 `line_id` 추가 때 한쪽만
//   고쳐졌고, 그건 지금도 부채로 남아 있다.
//
//   그래서 이 테스트가 **백엔드 JSON을 파일시스템에서 직접 읽어** 대조한다. 픽스처 사본을
//   두지 않는다 — 사본을 두면 그게 갈라질 수 있는 **세 번째 자리**가 된다.
//
// ★합격기준(§4:122)의 「같은 행·열」에서 «열»을 지키는 것이 이 파일이고, «행»(값)은
//   백엔드 `tests/test_cost_roundtrip_download.py`가 화면 페이로드와 대조해 지킨다.
import { describe, expect, it } from "vitest";

// ★백엔드가 **런타임에 읽는 바로 그 파일**을 사본 없이 들여온다. 여기에 사본을 두면
//   그게 갈라질 수 있는 세 번째 자리가 된다.
import spec from "../../../backend/app/services/cost_menu/round_trip_columns.json";
import { ROUND_TRIP_COLUMNS } from "./costHome";
import { priceSourceLabel } from "../pages/CostPage";

interface BackendColumn {
  key: string;
  label: string;
  editable: boolean;
  file_label?: string;
  value_labels?: Record<string, string>;
}

function backendColumns(): BackendColumn[] {
  return spec.columns as BackendColumn[];
}

describe("왕복 표 열 스펙 — 화면과 파일이 갈라지지 않는다", () => {
  it("백엔드 스펙 파일을 실제로 읽을 수 있다", () => {
    // ★이 단언이 먼저 있어야 한다 — 경로가 깨지면 아래 대조가 «빈 배열 vs 빈 배열»로
    //   조용히 통과할 수 있다(발견 0건과 실행 안 됨은 같은 숫자로 보인다, 교훈 #123).
    const cols = backendColumns();
    expect(cols.length).toBeGreaterThan(0);
  });

  it("key가 **순서까지** 같다", () => {
    // 순서가 곧 파일의 열 순서다 — 집합만 비교하면 열이 뒤바뀌어도 초록이다.
    expect(backendColumns().map((c) => c.key)).toEqual(
      ROUND_TRIP_COLUMNS.map((c) => c.key),
    );
  });

  it("label이 전건 같다", () => {
    expect(backendColumns().map((c) => c.label)).toEqual(
      ROUND_TRIP_COLUMNS.map((c) => c.label),
    );
  });

  it("editable이 전건 같다 — 「파일에서 고칠 수 있다」는 약속이 두 곳에서 달라지면 안 된다", () => {
    expect(backendColumns().map((c) => c.editable)).toEqual(
      ROUND_TRIP_COLUMNS.map((c) => c.editable),
    );
  });

  it("★출처 낱말이 화면 함수와 같다 — 스펙 표가 아니라 **함수를 호출해** 잰다", () => {
    // 설계 문서 Q3는 「원장 / 수동」이라 적었지만 라이브 화면은 「원장 / 등록가」다
    // (D-CPP-56, Jino 2026-08-24). 파일은 문서가 아니라 화면을 따라야 하므로,
    // 여기서 재는 것은 «스펙이 문서와 같은가»가 아니라 «스펙이 화면 함수와 같은가»다.
    const src = backendColumns().find((c) => c.key === "price_source");
    expect(src?.value_labels).toBeDefined();
    expect(src!.value_labels!.ledger).toBe(priceSourceLabel("ledger"));
    expect(src!.value_labels!.manual).toBe(priceSourceLabel("manual"));
  });

  it("file_label은 **화면과 다른 열에만** 붙는다 — 다르다는 사실을 스펙이 말한다", () => {
    const differing = backendColumns().filter((c) => c.file_label);
    // 지금은 12번째 한 칸뿐이다. 늘어나면 이 테스트가 그 사실을 드러낸다 —
    // 조용히 늘어나면 「화면과 같은 열」이라는 말이 하나씩 거짓이 된다.
    expect(differing.map((c) => c.key)).toEqual(["status_note"]);
    expect(differing[0].file_label).toBe("비고 (상태는 화면에서만)");
  });
});
