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
    h.data = diagnosis({ factor: 1.3133, factor_low: 1.0, factor_high: 1.3133, factor_point: 1.3133 });
    render(
      <MemoryRouter>
        <NaverAdDiagnosisBoard />
      </MemoryRouter>,
    );

    // 하한과 상한이 각각 화면 텍스트로 존재해야 한다.
    const card = await screen.findByText(/보정계수/);
    const box = card.parentElement as HTMLElement;
    expect(box.textContent).toContain("1.0000");
    expect(box.textContent).toContain("1.3133");
  });

  it("가정을 문장으로 병기한다 — 숫자만으로는 「100% 견인 가정」이 숨는다(계약 §3-6)", async () => {
    h.data = diagnosis({ factor: 1.3133, factor_low: 1.0, factor_high: 1.3133, factor_point: 1.3133 });
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
    h.data = diagnosis({ factor: 1.3133, factor_low: 1.0, factor_high: 1.3133, factor_point: 1.3133 });
    render(
      <MemoryRouter>
        <NaverAdDiagnosisBoard />
      </MemoryRouter>,
    );
    const note = await screen.findByText(/후보 «선정»은 전부 상한/);
    const text = note.parentElement?.textContent ?? "";
    // ★D-NAO-232(2026-08-23)로 이 단언이 **갱신**됐다. 직전 판은 「하한은 실제 쓰기의 «크기»에만
    //   쓴다」였는데, ref 94 §6이 **실행 게이트(BEP 증액금지)도 하한을 쓰고 거기서는 «크기»가 아니라
    //   «통과·차단»을 정한다**는 것을 실측으로 확정했다(액셀 221→195건 · 대칭 3.005→3.405:1).
    //   ⇒ 이 테스트는 그때까지 **반증된 문장을 화면에 붙들어 두는 역할**을 하고 있었다.
    //   같은 병의 전례: 세션 39 P1-1(위 주석) · 세션 37(옛 테스트가 fail-open의 거짓말을 단언 —
    //   고치자 깨졌고 그 깨짐이 증거였다). **테스트가 낡은 주장의 보존 장치가 되는 것**이 이 모양이다.
    expect(text).toMatch(/하한은 두 곳에 쓴다/);
    expect(text).toMatch(/«실행 게이트»의 통과·차단/);
    // 폐기된 문구들이 되살아나면 실패한다.
    expect(text).not.toMatch(/액셀\(확장·상향·재개\)은 하한으로 판정/);
    expect(text).not.toMatch(/«크기»에만 쓴다/);
  });

  it("점추정 원값도 함께 보인다 — 구간이 어디서 왔는지 감사할 수 있어야 한다", async () => {
    h.data = diagnosis({ factor: 1.42, factor_low: 1.0, factor_high: 1.42, factor_point: 1.42 });
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
