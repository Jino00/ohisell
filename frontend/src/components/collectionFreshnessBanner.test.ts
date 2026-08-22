// collectionFreshnessBanner.test.ts — 전역 수집 신선도 배너 빌더 가드.
//   ★존재 이유: 자동 트리거 제거 후 '잊어버림→낡음'과 '로그인 깨짐'을 유일하게 가시화.
import { describe, it, expect } from "vitest";
import { buildCollectionFreshnessBanner } from "./collectionFreshnessBanner";
import type { CollectionStatus, CollectionStreamStatus } from "../lib/api";
import { specsForKeys } from "../lib/streamRefresh";

function s(over: Partial<CollectionStreamStatus>): CollectionStreamStatus {
  return {
    key: "ofix_ad", label: "ofix 광고비", state: "fresh", age_hours: 1,
    last_success_at: null, last_error_at: null, last_error: null, ...over,
  };
}
const wrap = (streams: CollectionStreamStatus[]): CollectionStatus => ({ streams, as_of: "" });

describe("buildCollectionFreshnessBanner", () => {
  it("전부 fresh/in_flight면 null(숨김)", () => {
    expect(buildCollectionFreshnessBanner(wrap([s({ state: "fresh" }), s({ state: "in_flight" })]))).toBeNull();
  });
  it("warn만 있으면 severity yellow", () => {
    const b = buildCollectionFreshnessBanner(wrap([s({ state: "warn", age_hours: 30 })]));
    expect(b?.severity).toBe("yellow");
    expect(b?.items[0].kind).toBe("stale");
  });
  it("critical 있으면 severity red", () => {
    const b = buildCollectionFreshnessBanner(wrap([s({ state: "warn" }), s({ state: "critical", age_hours: 60 })]));
    expect(b?.severity).toBe("red");
  });
  // ── needs_login 상주 배너 (2026-08-22 W1, 계약 CONTRACT_collection_stability_s1 합격기준 ②) ──
  // ★존재 이유: 종전엔 「로그인 필요」가 **버튼 결과 문구로 잠깐 스쳐 지나갈 뿐**이라,
  //   화면을 그 순간에 보고 있지 않으면 존재하지 않는 사실이었다. 그래서 같은 사실을
  //   2026-08-22 하루에 계정당 9번 재발견했다. 이 배너가 그걸 «상태»로 만든다.
  //   이 테스트가 지키는 것은 「배너 빌더가 새 state를 모르면 조용히 아무것도 안 그린다」는
  //   실패 모드다 — else-if 사슬에 안 걸리면 침묵이고, 침묵은 배포 후에나 드러난다.
  it("needs_login은 red + 상주 배너 항목을 만든다(조용히 사라지지 않는다)", () => {
    const b = buildCollectionFreshnessBanner(wrap([
      s({ state: "needs_login", key: "rg_wing1", label: "오픽스 RG 정산",
          last_error_kind: "login_required", age_hours: 3 }),
    ]));
    expect(b).not.toBeNull();
    expect(b?.severity).toBe("red");
    expect(b?.items[0].kind).toBe("needs_login");
  });
  it("needs_login 문구는 «어디서 무엇을 하면 그다음 무슨 일이 나는지»를 말한다", () => {
    const b = buildCollectionFreshnessBanner(wrap([
      s({ state: "needs_login", key: "rg_wing2", label: "오하이테크 RG 정산",
          last_error_kind: "login_required", age_hours: 3 }),
    ]));
    const t = b!.items[0].text;
    expect(t).toContain("오하이테크 RG 정산");
    expect(t).toContain("Mac Chrome");          // 어디서
    expect(t).toContain("로그인");               // 무엇을
    expect(t).toContain("자동으로 이어받습니다");  // 그다음(W3) — 「다시 누르세요」가 아니다
    expect(t).not.toContain("다시 누르세요");
  });
  it("kind가 login_required면 failed 항목에도 로그인 꼬리표가 붙는다(문구 매칭 아님)", () => {
    // 문구엔 「로그인 필요」가 없는데 kind는 login_required — kind가 정본이다.
    const b = buildCollectionFreshnessBanner(wrap([
      s({ state: "failed", key: "rg_wing1", label: "오픽스 RG 정산",
          last_error: "사유 미상", last_error_kind: "login_required" }),
    ]));
    expect(b?.items[0].text).toContain("로그인 필요");
  });
  it("kind가 login_required가 아니면 문구에 「로그인」이 있어도 꼬리표를 안 붙인다(오탐 차단)", () => {
    const b = buildCollectionFreshnessBanner(wrap([
      s({ state: "failed", label: "ofix 광고비",
          last_error: "브라우저 창이 닫혔습니다 — 로그인 미완료", last_error_kind: null }),
    ]));
    expect(b?.items[0].text).not.toContain("· 로그인 필요");
  });

  it("failed는 red + kind failed", () => {
    const b = buildCollectionFreshnessBanner(wrap([s({ state: "failed", key: "supplier_hub", label: "로켓 발주/정산" })]));
    expect(b?.severity).toBe("red");
    expect(b?.items[0].kind).toBe("failed");
  });
  it("stale 항목 텍스트에 라벨+경과 포함", () => {
    const b = buildCollectionFreshnessBanner(wrap([s({ state: "critical", age_hours: 50, label: "ofix 광고비" })]));
    expect(b?.items[0].text).toContain("ofix 광고비");
    expect(b?.items[0].text).toContain("지남");
  });
  it("null/undefined 입력 방어 → null(크래시 금지)", () => {
    expect(buildCollectionFreshnessBanner(null as unknown as CollectionStatus)).toBeNull();
    expect(buildCollectionFreshnessBanner(undefined)).toBeNull();
  });
  it("age 48h+ 는 '일 지남', 48h 미만은 '시간 지남'(일별 데이터 정밀 표기)", () => {
    expect(buildCollectionFreshnessBanner(wrap([s({ state: "critical", age_hours: 72 })]))?.items[0].text).toContain("3일 지남");
    expect(buildCollectionFreshnessBanner(wrap([s({ state: "warn", age_hours: 30 })]))?.items[0].text).toContain("30시간 지남");
    expect(buildCollectionFreshnessBanner(wrap([s({ state: "warn", age_hours: 25 })]))?.items[0].text).toContain("25시간 지남");
  });
  it("age_hours null(수집 기록 없음)이면 '수집 기록 없음' 표기", () => {
    const b = buildCollectionFreshnessBanner(wrap([s({ state: "critical", age_hours: null })]));
    expect(b?.items[0].text).toContain("수집 기록 없음");
  });
});

// ── 실패 문구 정직성 가드 (2026-08-07) ────────────────────────────────
// ★존재 이유(실사고): 배너가 08-05 23:03의 실패를 시각 없이 "갱신 실패 · 로그인 필요"로
//   띄웠고, Jino가 그걸 "지금 고장 나 있다"로 읽었다. 실제로는 33시간 동안 아무도 시도를
//   안 한 것뿐이라 버튼을 누르니 47초 만에 성공했다. 그리고 사유도 하드코딩이라, 로그인과
//   무관한 실패(브라우저 크래시 등)까지 "로그인 필요"라고 잘못 말하고 있었다.
describe("failed 문구는 실패 시각과 실제 사유에 근거해야 한다", () => {
  it("실패 시각을 'MM-DD HH:MM'으로 붙인다", () => {
    const b = buildCollectionFreshnessBanner(
      wrap([s({ state: "failed", label: "ofix 판매분석", last_error_at: "2026-08-05T23:03:54.847247" })]),
    );
    expect(b?.items[0].text).toContain("08-05 23:03");
  });

  it("last_error에 '로그인'이 있을 때만 '로그인 필요'를 붙인다", () => {
    const login = buildCollectionFreshnessBanner(
      wrap([s({ state: "failed", last_error: "vendor-summary 실패 — 'login' 재실행 필요. [로그인 필요]" })]),
    );
    expect(login?.items[0].text).toContain("로그인 필요");

    // 브라우저 크래시 — 로그인과 무관하다. 예전 코드는 여기도 '로그인 필요'라고 적었다.
    const crash = buildCollectionFreshnessBanner(
      wrap([s({ state: "failed", last_error: "Target page, context or browser has been closed" })]),
    );
    expect(crash?.items[0].text).not.toContain("로그인");
    expect(crash?.items[0].text).toContain("갱신 실패");
  });

  it("last_error_at이 없거나 잘려도 크래시 없이 시각만 생략한다", () => {
    for (const bad of [null, "2026-08-05"]) {
      const b = buildCollectionFreshnessBanner(wrap([s({ state: "failed", last_error_at: bad })]));
      expect(b?.items[0].text).toContain("갱신 실패");
      expect(b?.items[0].text).not.toContain("(");
    }
  });

  it("표기는 원문 슬라이스와 정확히 일치한다(Date 파싱으로 바뀌면 깨진다)", () => {
    // ★전문(全文) 고정: 백엔드가 주는 건 naive KST ISO다(utils/kst.py). 구현이 `toISOString()`
    //   이나 `timeZone:'Asia/Seoul'` 변환으로 바뀌면 실행 타임존에 따라 시각이 밀린다 —
    //   CI는 ubuntu-latest(TZ=UTC)이므로 거기서 깨진다. 전문 고정이라야 잡힌다(toContain은
    //   부분 일치라 놓친다).
    //   ⚠️ repo 어디에도 테스트 TZ가 고정돼 있지 않다 — 누가 vitest에 TZ=Asia/Seoul을 주면
    //     이 가드는 조용히 무력해진다(적대리뷰 P2, 이월).
    const b = buildCollectionFreshnessBanner(
      wrap([s({ state: "failed", label: "ofix 판매분석", age_hours: 1,
                last_error_at: "2026-08-05T23:03:54.847247", last_error: "로그인 필요" })]),
    );
    expect(b?.items[0].text).toBe(
      "ofix 판매분석 갱신 실패(08-05 23:03) · 마지막 성공 1시간 전 · 로그인 필요",
    );
  });
});

// ── 멈춘 요청이 낡음을 가리면 안 된다 (2026-08-07, 이 PR 자기리뷰 P1) ──
// ★존재 이유: 백엔드는 requested=true면 state를 in_flight로 덮어써 낡음 정보를 지운다.
//   예전엔 빨강 광고쿠키 배너가 시간 기준이라 backstop이었는데 이 PR이 그걸 뺐다. 요청이
//   소비되지 않고 멈추는 일은 실제로 있다(2026-08-07 403: RG 요청 1시간 반 pending).
//   backstop 없이 in_flight를 숨기면 33시간 낡은 데이터가 어느 배너에도 안 뜬다.
describe("in_flight라도 이미 낡았으면 숨기지 않는다", () => {
  it("갓 요청한 것(age < 24h)은 여전히 숨긴다 — 정상 갱신 중 소음 방지", () => {
    expect(buildCollectionFreshnessBanner(wrap([s({ state: "in_flight", age_hours: 2 })]))).toBeNull();
  });

  it("24h 넘게 낡은 채 요청만 걸려 있으면 뜬다 + '아직 안 받음'을 명시", () => {
    const b = buildCollectionFreshnessBanner(wrap([s({ state: "in_flight", age_hours: 33 })]));
    expect(b).not.toBeNull();
    expect(b?.items[0].kind).toBe("stale");
    expect(b?.items[0].text).toContain("33시간 지남");
    expect(b?.items[0].text).toContain("아직 안 받음");
    expect(b?.severity).toBe("yellow");
  });

  it("48h 넘으면 in_flight여도 red", () => {
    expect(buildCollectionFreshnessBanner(wrap([s({ state: "in_flight", age_hours: 60 })]))?.severity).toBe("red");
  });

  it("수집 기록 없음(age null) + 요청만 걸림도 red로 뜬다", () => {
    const b = buildCollectionFreshnessBanner(wrap([s({ state: "in_flight", age_hours: null })]));
    expect(b?.items[0].text).toContain("수집 기록 없음");
    expect(b?.severity).toBe("red");
  });

  it("구제된 in_flight 항목도 갱신 대상 key를 실어야 한다(버튼이 비지 않게)", () => {
    const b = buildCollectionFreshnessBanner(
      wrap([s({ state: "in_flight", key: "ofix_ad", age_hours: 33 })]),
    );
    const { specs, unknown } = specsForKeys((b?.items ?? []).map((i) => i.key));
    expect(specs.map((x) => x.key)).toEqual(["ofix_ad"]);
    expect(unknown).toEqual([]);
  });
});

// ── 적대리뷰 P1 회귀 가드 (2026-08-07) ────────────────────────────────
// ★존재 이유: 이 배너가 "낡음"의 **단일 표면**이 된 뒤(빨강 광고쿠키 배너에서 stale 갈래를
//   흡수), 여기서 빠지면 어느 화면에도 안 남는다. 아래 둘은 그 단일 표면의 구멍이었다.
describe("failed 항목은 '언제 깨졌나'와 '데이터가 얼마나 낡았나'를 둘 다 말한다", () => {
  it("실패 시각과 마지막 성공 경과가 함께 뜬다 — 둘은 다른 질문이다", () => {
    const b = buildCollectionFreshnessBanner(wrap([s({
      state: "failed", label: "ofix 광고비",
      last_success_at: "2026-07-08T09:00:00", last_error_at: "2026-08-07T14:35:12",
      age_hours: 24 * 30, last_error: null,
    })]));
    const t = b?.items[0].text ?? "";
    expect(t).toContain("08-07 14:35");     // 언제 깨졌나
    expect(t).toContain("마지막 성공 30일 전"); // 데이터가 얼마나 낡았나
  });
  it("★같은 '갱신 실패'라도 3시간 전 데이터와 3개월 전 데이터가 다른 문구여야 한다", () => {
    const mk = (age: number) => buildCollectionFreshnessBanner(wrap([s({
      state: "failed", age_hours: age, last_error_at: "2026-08-07T14:35:12",
    })]))?.items[0].text;
    // 이 단언이 깨지면 = 경과가 문구에서 빠진 것 = P1 재발.
    expect(mk(3)).not.toBe(mk(24 * 90));
    expect(mk(3)).toContain("마지막 성공 3시간 전");
    expect(mk(24 * 90)).toContain("마지막 성공 90일 전");
  });
  it("age_hours null이면 '수집 기록 없음'(0시간으로 접지 않는다)", () => {
    const b = buildCollectionFreshnessBanner(wrap([s({
      state: "failed", age_hours: null, last_error_at: "2026-08-07T14:35:12",
    })]));
    expect(b?.items[0].text).toContain("수집 기록 없음");
    expect(b?.items[0].text).not.toContain("0시간");
  });
});

describe("unknown(백엔드 판정 불가)은 숨기지 않는다", () => {
  it("red + kind unknown + '판정 불가'를 명시", () => {
    const b = buildCollectionFreshnessBanner(wrap([s({ state: "unknown", label: "ofix 광고비" })]));
    expect(b?.severity).toBe("red");
    expect(b?.items[0].kind).toBe("unknown");
    expect(b?.items[0].text).toContain("판정 불가");
  });
  it("★한 스트림이 unknown이어도 나머지 스트림 판정은 살아 있다(전면 실명 금지)", () => {
    const b = buildCollectionFreshnessBanner(wrap([
      s({ state: "unknown", key: "supplier_hub", label: "로켓 발주/정산" }),
      s({ state: "critical", key: "ofix_ad", label: "ofix 광고비", age_hours: 100 }),
    ]));
    expect(b?.items).toHaveLength(2);
    expect(b?.items[1].text).toContain("4일 지남");
    // 갱신 버튼 대상에서도 빠지지 않는다
    expect(specsForKeys((b?.items ?? []).map((i) => i.key)).specs).toHaveLength(2);
  });
});

describe("경계값 — 상수·비교연산자가 새면 잡는다", () => {
  it("in_flight 구제는 24h에서 켜지고 23.9h에서 꺼진다", () => {
    const at = (age: number) =>
      buildCollectionFreshnessBanner(wrap([s({ state: "in_flight", age_hours: age })]));
    expect(at(23.9)).toBeNull();          // 아직 정상 갱신 중 — 소음 금지
    expect(at(24)?.items[0].kind).toBe("stale");
  });
  it("in_flight 구제의 색은 48h에서 노랑→빨강", () => {
    const sev = (age: number) =>
      buildCollectionFreshnessBanner(wrap([s({ state: "in_flight", age_hours: age })]))?.severity;
    expect(sev(47.9)).toBe("yellow");
    expect(sev(48)).toBe("red");
  });
  it("failed 문구의 '일/시간' 전환도 48h 경계", () => {
    const t = (age: number) => buildCollectionFreshnessBanner(wrap([s({
      state: "failed", age_hours: age, last_error_at: "2026-08-07T14:35:12",
    })]))?.items[0].text;
    expect(t(47.9)).toContain("마지막 성공 47시간 전");
    expect(t(48)).toContain("마지막 성공 2일 전");
  });
});

describe("로그인 사유 판정은 streamRefresh와 한 벌이어야 한다", () => {
  it("'로그인 필요'가 있으면 붙는다", () => {
    const b = buildCollectionFreshnessBanner(wrap([s({
      state: "failed", last_error: "RG: status/api에서 로그아웃 확증 — 로그인 필요. [재시도 안 함]",
      last_error_at: "2026-08-07T14:35:12",
    })]));
    expect(b?.items[0].text).toContain("로그인 필요");
  });
  it("★'로그인'이 들어가도 로그인 실패가 아닌 문구엔 안 붙는다(넓은 술어 금지)", () => {
    // 실제 페처 문구 — 크래시인데 '로그인'이라는 낱말이 들어 있다.
    const b = buildCollectionFreshnessBanner(wrap([s({
      state: "failed", last_error: "브라우저 창이 닫혔습니다 — 로그인 미완료(창을 닫지 말고 로그인만 하세요)",
      last_error_at: "2026-08-07T14:35:12",
    })]));
    expect(b?.items[0].text).not.toContain("· 로그인 필요");
  });
  it("영문 크래시 문구에도 안 붙는다", () => {
    const b = buildCollectionFreshnessBanner(wrap([s({
      state: "failed", last_error: "Target page, context or browser has been closed",
      last_error_at: "2026-08-07T14:35:12",
    })]));
    expect(b?.items[0].text).not.toContain("로그인");
  });
});

// ── 배너 항목 ↔ 갱신 대상 결합 가드 (2026-08-03) ──────────────────────
// ★존재 이유: 배너의 '지금 갱신'은 items[].key로 갱신 대상을 고른다. 백엔드가 key를 바꾸거나
//   빌더가 key를 안 실으면 버튼이 **조용히 아무것도 갱신하지 않는다** — 링크였던 시절과
//   똑같이 "눌러도 아무 일이 없는" 상태로 되돌아간다. 그 회귀를 여기서 잡는다.
describe("배너 항목은 갱신 가능한 스트림 key를 실어야 한다", () => {
  it("낡은 항목의 key가 STREAM_SPECS로 해석된다", () => {
    const b = buildCollectionFreshnessBanner(
      wrap([
        s({ state: "critical", key: "ohitech_ad", label: "ohitech 로켓광고", age_hours: 160 }),
        s({ state: "failed", key: "supplier_hub", label: "로켓 발주/정산" }),
      ]),
    );
    const { specs } = specsForKeys((b?.items ?? []).map((i) => i.key));
    expect(specs.map((x) => x.key)).toEqual(["ohitech_ad", "supplier_hub"]);
    expect(specsForKeys((b?.items ?? []).map((i) => i.key)).unknown).toEqual([]);
  });

  it("4개 스트림이 전부 낡으면 4건 모두 갱신 대상이 된다(누락 없음)", () => {
    // as const — key는 리터럴 유니온이라 string[]로 넓어지면 타입이 안 맞는다.
    const keys = ["ofix_sales", "ofix_ad", "ohitech_ad", "supplier_hub"] as const;
    const b = buildCollectionFreshnessBanner(
      wrap(keys.map((k) => s({ state: "critical", key: k, age_hours: 100 }))),
    );
    expect(specsForKeys((b?.items ?? []).map((i) => i.key)).specs).toHaveLength(4);
  });
});
