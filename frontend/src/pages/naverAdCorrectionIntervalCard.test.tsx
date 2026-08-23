// @vitest-environment jsdom
//
// naverAdCorrectionIntervalCard.test.tsx — 「D-NAO-21 보정계수」 카드가 **구간 양끝을 화면에
//   내는가**를 지킨다 (D-NAO-230 계약 §5-5 표면 요건).
//
// ## 왜 이 테스트가 있어야 하나
// 이 계약의 «표면»은 코드가 구간을 계산하는 것이 **아니라** Jino가 진단 보드에서 「이 총이익이
// 어떤 가정 위의 값인지」를 읽을 수 있는 것이다(계약 §2 목표 원문). 백엔드가 `factor_low`/
// `factor_high`를 아무리 정확히 내보내도 카드가 `factor` 하나만 그리면 **화면은 다시 점추정으로
// 돌아가고 계약은 미달**이다 — 그리고 그 되돌림은 tsc도 백엔드 테스트도 잡지 못한다.
// 이 저장소는 같은 자리에서 이미 데였다: 응답 키는 살아 있는데 표면이 죽은 결함이
// D-NAO-204(`response_model`이 배너 키 삭제)·D-NAO-176(버킷 카드 호출부 되돌림)으로 재발했고,
// 전역 §4가 요구하는 「사용자에게 닿는 마지막 표면을 끊는 변이」가 겨냥하는 자리가 바로 여기다.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => ({ data: null as unknown }));

vi.mock("../lib/api", () => ({
  fetchNaverAdDiagnosis: () => Promise.resolve(h.data),
}));

import NaverAdDiagnosisBoard from "./NaverAdDiagnosisBoard";

const EMPTY_BOARDS = {
  bleeding_keywords: [],
  starving_winners: [],
  expansion_bucket: { cost: 0, conv_amt: 0, roas_naver: null, roas_corrected: null, cost_share: 0 },
  shopping_group_bep: [],
  shopping_group_growth: [],
  exclusion_candidates: [],
  keyword_triage: { winners: [], losers: [], unknowns: [] },
  vicious_cycle: [],
  pause_candidates: [],
  resume_candidates: [],
  shopping_pause_candidates: [],
  shopping_resume_candidates: [],
  shopping_lever_resume_candidates: [],
  floor_wait_units: [],
};

function diagnosis(cf: Record<string, unknown>) {
  return {
    window: { date_from: "2026-08-09", date_to: "2026-08-23" },
    correction_factor: {
      source: "actual_revenue_ratio",
      window_from: "2026-07-25",
      window_to: "2026-08-23",
      window_revenue: 45_307_260,
      window_conv_amt: 34_499_980,
      ...cf,
    },
    account_bep_roas: 1.6833,
    account_target_roas: 1.9358,
    boards: EMPTY_BOARDS,
  };
}

afterEach(cleanup);

describe("D-NAO-21 보정계수 카드 — 구간 자 표면(D-NAO-230 §5-5)", () => {
  it("구간 양끝을 «둘 다» 화면에 낸다 — 한쪽만 그리면 점추정으로 되돌아간 것이다", async () => {
    h.data = diagnosis({ factor: 1.3133, factor_low: 0.827, factor_high: 1.3133, factor_point: 1.3133,
      factor_low_source: "inflowpath_ad_prefix_over_direct",
      factor_low_window: "2026-07-25~2026-08-23",
      factor_low_evidence: "docs/references/95_inflowpath_yardstick_census_20260823.md",
      factor_low_caveat: "마지막터치 라벨 기준·창 2026-07-25~08-23 스냅샷. 「네이버플러스스토어검색>광고」(6,877,600원·446건)의 SA 소속은 공식 출처 미확인이라 하한에서 제외 — 포함 시 1.067.",
      factor_low_window_spread: "0.8289~0.8862 (창 4개, 폭 5.7%p — 채택값은 가장 보수적인 창)" });
    render(
      <MemoryRouter>
        <NaverAdDiagnosisBoard />
      </MemoryRouter>,
    );

    // 하한과 상한이 각각 화면 텍스트로 존재해야 한다.
    const card = await screen.findByText(/보정계수/);
    const box = card.parentElement as HTMLElement;
    expect(box.textContent).toContain("0.8270");
    expect(box.textContent).toContain("1.3133");
  });

  it("가정을 문장으로 병기한다 — 숫자만으로는 「100% 견인 가정」이 숨는다(계약 §3-6)", async () => {
    h.data = diagnosis({ factor: 1.3133, factor_low: 0.827, factor_high: 1.3133, factor_point: 1.3133,
      factor_low_source: "inflowpath_ad_prefix_over_direct",
      factor_low_window: "2026-07-25~2026-08-23",
      factor_low_evidence: "docs/references/95_inflowpath_yardstick_census_20260823.md",
      factor_low_caveat: "마지막터치 라벨 기준·창 2026-07-25~08-23 스냅샷. 「네이버플러스스토어검색>광고」(6,877,600원·446건)의 SA 소속은 공식 출처 미확인이라 하한에서 제외 — 포함 시 1.067.",
      factor_low_window_spread: "0.8289~0.8862 (창 4개, 폭 5.7%p — 채택값은 가장 보수적인 창)" });
    render(
      <MemoryRouter>
        <NaverAdDiagnosisBoard />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/100% 견인 가정/)).toBeTruthy();
  });

  // ★적대 리뷰 1R P1-1 상환 — 이 단언이 초판엔 「액셀은 하한으로 판정」이었는데, 그건
  //   Jino 결정(D-NAO-231)으로 **폐기된 초판 배정**이었다. 화면이 배포되는 동작과 정반대를
  //   말하고 있었고 테스트가 그 거짓을 «단언»하고 있었다. 지금 단언은 실배선과 같다:
  //   보드 선정 = 전부 상한(`test_no_board_ever_receives_the_lower_end`가 백엔드에서 강제),
  //   하한 = 실쓰기 크기 층만.
  it("어느 끝이 어느 층에 쓰이는지 화면이 «실제 배선대로» 말한다", async () => {
    h.data = diagnosis({ factor: 1.3133, factor_low: 0.827, factor_high: 1.3133, factor_point: 1.3133,
      factor_low_source: "inflowpath_ad_prefix_over_direct",
      factor_low_window: "2026-07-25~2026-08-23",
      factor_low_evidence: "docs/references/95_inflowpath_yardstick_census_20260823.md",
      factor_low_caveat: "마지막터치 라벨 기준·창 2026-07-25~08-23 스냅샷. 「네이버플러스스토어검색>광고」(6,877,600원·446건)의 SA 소속은 공식 출처 미확인이라 하한에서 제외 — 포함 시 1.067.",
      factor_low_window_spread: "0.8289~0.8862 (창 4개, 폭 5.7%p — 채택값은 가장 보수적인 창)" });
    render(
      <MemoryRouter>
        <NaverAdDiagnosisBoard />
      </MemoryRouter>,
    );
    // ★testid로 «위치»를 고정한다 — textContent 어딘가에 문구가 있으면 통과하는 단언은
    //   위치를 바꾸는 변이에 뚫린다(ref 94 1R 생존 변이 4종이 그 모양이었다).
    const text = (await screen.findByTestId("factor-end-assignment")).textContent ?? "";
    // ★★D-NAO-234 ⓐ(2026-08-23)로 이 단언이 **또 갱신**됐다 — 세 번째다.
    //   D-NAO-232 판은 「하한은 두 곳에 쓴다 — 크기 + «실행 게이트»의 통과·차단」이었다.
    //   그건 «당시 배선»의 정확한 서술이었지만, 그 배선 자체가 결함(분류의 구멍)이었고
    //   D-NAO-234 ⓐ가 게이트를 상한으로 옮겼다. ⇒ 이제 하한은 «크기»에만 쓴다.
    //   ⚠️주의: 「«크기»에만 쓴다」는 문구는 D-NAO-232가 **폐기했던 바로 그 문구**인데
    //   지금은 다시 참이다. 문구의 참·거짓은 배선이 정하지 이력이 정하지 않는다 —
    //   그래서 이 테스트는 **문구를 금지 목록으로 관리하지 않고 현재 배선을 단언**한다.
    expect(text).toMatch(/«선정»은 상한/);
    expect(text).toMatch(/«게이트»\(통과·차단\)도 상한/);
    expect(text).toMatch(/«크기»에만/);
    // 게이트가 하한을 쓴다는 (지금은 반증된) 주장이 되살아나면 실패한다.
    expect(text).not.toMatch(/하한은 두 곳에 쓴다/);
    expect(text).not.toMatch(/«실행 게이트»의 통과·차단/);
  });

  // ★★D-NAO-234 표면 요건 — 값 옆에 «근거»가 붙는가(계약 §6-5).
  it("하한의 근거·창·[미상]을 화면이 말한다 — 숫자만 그리면 「0.827이 어디서 왔나」가 사라진다", async () => {
    h.data = diagnosis({ factor: 1.3291, factor_low: 0.827, factor_high: 1.3291, factor_point: 1.3291,
      factor_low_source: "inflowpath_ad_prefix_over_direct",
      factor_low_window: "2026-07-25~2026-08-23",
      factor_low_evidence: "docs/references/95_inflowpath_yardstick_census_20260823.md",
      factor_low_caveat: "마지막터치 라벨 기준. 「네이버플러스스토어검색>광고」의 SA 소속은 공식 출처 미확인이라 하한에서 제외 — 포함 시 1.067.",
      factor_low_window_spread: "0.8289~0.8862 (창 4개, 폭 5.7%p)" });
    render(
      <MemoryRouter>
        <NaverAdDiagnosisBoard />
      </MemoryRouter>,
    );
    const basis = (await screen.findByTestId("factor-low-basis")).textContent ?? "";
    expect(basis).toMatch(/하한 근거/);
    expect(basis).toMatch(/광고>/);           // 무엇을 분자로 썼나
    expect(basis).toMatch(/direct/);          // 무엇을 분모로 썼나(짝 규율)
    expect(basis).toMatch(/2026-07-25~2026-08-23/);  // 창 없이 계수를 말하지 않는다
    expect(basis).toMatch(/마지막터치/);       // 가정 병기(금지선 5)
    expect(basis).toMatch(/플러스스토어/);     // 하한에 붙박인 [미상]
    expect(basis).toMatch(/1\.067/);           // 그 [미상]을 포함하면 얼마가 되나
    expect(basis).toMatch(/0\.8289~0\.8862/);  // 「고정값이 안 흔들린다」고 말하지 않는다
  });

  // ★★적대 리뷰 P1-3 — 점추정<0.827이면 기준선이 «상한» 자리로 올라간다.
  //   초판은 그때 근거를 통째로 빠뜨려 화면이 「계수를 못 만들어 [1,1]로 퇴화」라는
  //   거짓 문장을 그렸다(계수는 산출됐고 구간도 퇴화하지 않았는데).
  it("기준선이 «상한» 자리로 올라가도 근거를 말하고, 그 위치를 밝힌다", async () => {
    h.data = diagnosis({ factor: 0.827, factor_low: 0.72, factor_high: 0.827, factor_point: 0.72,
      factor_floor: 0.827, factor_floor_end: "high",
      factor_low_source: "inflowpath_ad_prefix_over_direct",
      factor_low_window: "2026-07-25~2026-08-23",
      factor_low_caveat: "마지막터치 라벨 기준.",
      factor_low_window_spread: "0.8289~0.8862 (창 4개)" });
    render(
      <MemoryRouter>
        <NaverAdDiagnosisBoard />
      </MemoryRouter>,
    );
    const basis = (await screen.findByTestId("factor-low-basis")).textContent ?? "";
    expect(basis).toMatch(/상한 근거/);          // «하한 근거»라고 말하면 값과 이름표가 어긋난다
    expect(basis).toMatch(/광고>/);              // 근거가 살아 있다
    expect(basis).toMatch(/기준선이 «상한» 자리에 있다/);  // 무슨 뜻인지까지 말한다
    expect(basis).not.toMatch(/퇴화/);           // ★거짓 문장이 되살아나면 실패
  });

  it("근거가 없으면 근거를 말하지 않는다 — 퇴화 구간에 없는 출처를 붙이지 않는다", async () => {
    h.data = diagnosis({
      source: "unavailable",
      factor: 1.0, factor_low: 1.0, factor_high: 1.0, factor_point: 1.0,
    });
    render(
      <MemoryRouter>
        <NaverAdDiagnosisBoard />
      </MemoryRouter>,
    );
    const basis = (await screen.findByTestId("factor-low-basis")).textContent ?? "";
    expect(basis).toMatch(/실측 기준선 미적용/);
    expect(basis).not.toMatch(/광고>/);
    expect(basis).not.toMatch(/마지막터치/);
  });

  it("점추정 원값도 함께 보인다 — 구간이 어디서 왔는지 감사할 수 있어야 한다", async () => {
    h.data = diagnosis({ factor: 1.42, factor_low: 0.827, factor_high: 1.42, factor_point: 1.42, factor_low_source: "inflowpath_ad_prefix_over_direct" });
    render(
      <MemoryRouter>
        <NaverAdDiagnosisBoard />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/점추정 ×1\.4200/)).toBeTruthy();
  });

  it("계수 산출 불가(폴백)여도 구간 표기와 사유가 같이 나온다", async () => {
    h.data = diagnosis({
      source: "unavailable",
      factor: 1.0, factor_low: 1.0, factor_high: 1.0, factor_point: 1.0,
    });
    render(
      <MemoryRouter>
        <NaverAdDiagnosisBoard />
      </MemoryRouter>,
    );
    const card = await screen.findByText(/보정계수/);
    expect((card.parentElement as HTMLElement).textContent).toContain("1.0000");
    expect(await screen.findByText(/산출 불가/)).toBeTruthy();
  });
});
