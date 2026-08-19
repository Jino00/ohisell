// @vitest-environment jsdom
//
// naverAdAllocationNotice.test.tsx — 광고비 배분이 **눈에 닿는가** (D-NAO-207).
//
// 왜 생겼나(2026-08-19): 손익 패널의 상품 행이 「이익」이라는 이름으로 매출총이익을 냈다.
//   라이브 2026-08-18: 상품 행 합 993,330원(71.0%) vs 계정 실제 338,395원(21.1%) — 50%p.
//   설명은 있었다 — 표 **아래** 11px 회색 각주 한 줄로. Jino가 화면을 보고 «너무 높다»고
//   지적할 때까지 아무도 못 읽었다. 그래서 이 파일은 «값이 맞나»가 아니라 «말이 보이나»를 잰다.
//
// 백엔드(test_naver_ops_ad_allocation.py)가 값과 검산 등식을 지키고, 이 파일이 그것이 화면에
// 닿는가를 지킨다 — 백엔드가 내놓아도 화면이 안 그리면 사용자에겐 없는 사실이다
// ([[same-defect-three-times-fix-the-shape]]: 같은 결함 3회, 전부 «백엔드는 세는데 화면이 안 읽음»).
//
// 이 파일이 죽이는 변이:
//   M1 배너 삭제(각주만 남음 = 원래 결함으로 복귀)
//   M2 배분 비율을 숫자 없이 「일부 배분됨」으로 (얼마인지 안 말하면 안 읽힌다)
//   M3 원장 창 밖 경고 삭제(그 구간 광고비 0을 «안 썼다»로 오독)
//   M4 미배분 행 삭제(열 합계가 카드와 갈리는데 화면은 조용하다)
//   M5 미배분이 0원일 때 행을 숨김(«없다»와 «0»을 구분 못 함)
//   M6 검산 불일치 경고 삭제(표가 조용히 틀린다)
//   M7 모호 매핑 경고 삭제
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { AdAllocationNotice, UnallocatedRow } from "./NaverOps";
import type {
  NaverSalesSummaryData, NaverUnallocated, NaverAdAlloc, NaverReconciliation,
} from "../lib/api";

afterEach(cleanup);

/** 2026-08-18 라이브 실측값을 그대로 쓴다 — 숫자가 바뀌면 이 파일이 먼저 안다. */
function summary(over: Partial<NaverSalesSummaryData> = {}): NaverSalesSummaryData {
  return {
    revenue: "1601272.73", revenue_vat_incl: "1761400.00",
    product_revenue: "1399454.55", delivery_revenue: "201818.18",
    fee: "71611.82", cost: "334512.73",
    ad_spend: "606874.54", logistics: "249878.18", shipment_count: 98,
    profit: "338395.46", profit_rate: "21.13",
    sa_conv_revenue: "1119800", sa_ad_spend: "574980", sa_roas: "1.95",
    sa_conv_from: "2026-08-18", sa_conv_to: "2026-08-18",
    fee_settled_lines: 2, fee_est_lines: 99,
    ad_allocated: "406242.00", ad_unallocated: "200632.54",
    ...over,
  };
}

const AD_ALLOC: NaverAdAlloc = {
  ledger_from: "2026-08-01", ledger_to: "2026-08-18",
  uncovered_dates: [], shopping_cost: "406242.00", allocated: "406242.00",
  unmapped_cost: "0.00", ambiguous_cost: "0.00", ambiguous_ads: 0,
};

const RECON: NaverReconciliation = {
  sum_product_profit: "538000.00", unknown_cost_profit: "0.00",
  unallocated_profit: "-199604.54", summary_profit: "338395.46",
  residual: "0.00", closes: true,
};

const UNALLOC: NaverUnallocated = {
  ad_spend: "200632.54", logistics: "0.00",
  claim_income: "0.00", claim_fee: "0.00", profit: "-200632.54",
};

function inTable(node: React.ReactNode) {
  return render(<table><tbody>{node}</tbody></table>);
}

describe("광고비 배분 고지 (표 위)", () => {
  it("M1·M2 — 상품 행이 순이익임을 말하고, 배분 금액·비율을 숫자로 낸다", () => {
    render(<AdAllocationNotice summary={summary()} adAlloc={AD_ALLOC} recon={RECON} />);
    expect(screen.getByText(/광고비·물류비까지 반영한 순이익/)).toBeTruthy();
    // 「일부 배분됨」이 아니라 얼마가 붙었는지 — 406,242원(67%)
    expect(screen.getByText(/406,242원\(67%\)/)).toBeTruthy();
    expect(screen.getByText(/200,633원/)).toBeTruthy();
  });

  it("M3 — 소재 원장이 없는 날은 «0원»이 아니라 «전액 미배분»이라고 말한다", () => {
    render(
      <AdAllocationNotice
        summary={summary()}
        adAlloc={{ ...AD_ALLOC, uncovered_dates: ["2026-07-18", "2026-07-19", "2026-07-20", "2026-07-21"] }}
        recon={RECON}
      />,
    );
    expect(screen.getByText(/4일/)).toBeTruthy();
    expect(screen.getByText(/전액 미배분/)).toBeTruthy();
    expect(screen.getByText(/외 1일/)).toBeTruthy();            // 앞 3개만 나열하고 나머지는 «외 N일»
    expect(screen.getByText(/2026-08-01~2026-08-18/)).toBeTruthy();  // 원장 보유 창을 밝힌다
  });

  it("창을 다 덮으면 경고를 띄우지 않는다 — 늘 켜진 경고는 안 읽힌다", () => {
    render(<AdAllocationNotice summary={summary()} adAlloc={AD_ALLOC} recon={RECON} />);
    expect(screen.queryByText(/전액 미배분/)).toBeNull();
  });

  it("M7 — 소재가 두 상품에 매핑되면 «어느 쪽도 안 붙였다»를 말한다", () => {
    render(
      <AdAllocationNotice
        summary={summary()}
        adAlloc={{ ...AD_ALLOC, ambiguous_cost: "60000.00", ambiguous_ads: 2 }}
        recon={RECON}
      />,
    );
    expect(screen.getByText(/어느 상품에도 붙이지 않았습니다/)).toBeTruthy();
  });

  it("M6 — 검산이 깨지면 «표를 근거로 쓰지 마라»고 말한다", () => {
    render(
      <AdAllocationNotice
        summary={summary()}
        adAlloc={AD_ALLOC}
        recon={{ ...RECON, residual: "12345.00", closes: false }}
      />,
    );
    expect(screen.getByText(/검산 불일치/)).toBeTruthy();
    expect(screen.getByText(/표를 근거로 쓰지 마세요/)).toBeTruthy();
  });

  it("검산이 맞으면 빨간 경고를 띄우지 않는다", () => {
    render(<AdAllocationNotice summary={summary()} adAlloc={AD_ALLOC} recon={RECON} />);
    expect(screen.queryByText(/검산 불일치/)).toBeNull();
  });

  it("구 응답(ad_allocated 없음)에서는 배너를 띄우지 않는다 — 없는 사실을 지어내지 않는다", () => {
    const old = summary(); delete (old as Partial<NaverSalesSummaryData>).ad_allocated;
    render(<AdAllocationNotice summary={old} adAlloc={undefined} recon={undefined} />);
    expect(screen.queryByText(/순이익입니다/)).toBeNull();
  });
});

describe("미배분 행 (표 맨 아래)", () => {
  it("M4 — 못 붙인 광고비가 한 행으로 보이고 이익이 음수로 잡힌다", () => {
    inTable(<UnallocatedRow unallocated={UNALLOC} uncoveredDays={0} />);
    expect(screen.getByText("광고비 미배분")).toBeTruthy();
    expect(screen.getByText("200,633원")).toBeTruthy();
    expect(screen.getByText("-200,633원")).toBeTruthy();
    expect(screen.getByText(/파워링크\(소재=키워드\)·디스플레이는 상품 축이 없어/)).toBeTruthy();
  });

  it("M5 — 0원이어도 행을 그린다 («없다»와 «0»은 다르다)", () => {
    inTable(
      <UnallocatedRow
        unallocated={{ ...UNALLOC, ad_spend: "0.00", profit: "0.00" }}
        uncoveredDays={0}
      />,
    );
    expect(screen.getByText("광고비 미배분")).toBeTruthy();
  });

  it("원장 없는 날이 있으면 행에서도 그 사실을 말한다", () => {
    inTable(<UnallocatedRow unallocated={UNALLOC} uncoveredDays={12} />);
    expect(screen.getByText(/소재 원장 없는 날 12일 포함/)).toBeTruthy();
  });

  it("데이터가 아예 없으면 행을 만들지 않는다", () => {
    const { container } = inTable(<UnallocatedRow unallocated={undefined} />);
    expect(container.querySelectorAll("tr").length).toBe(0);
  });
});

// ── 페이지가 이 컴포넌트들을 실제로 쓰는가 (변이 M11·M12) ──────────────
//
// ★왜 소스를 읽어서 재나: 위 테스트들은 컴포넌트를 «직접» 렌더해서 전부 초록인데,
//   페이지에서 `<AdAllocationNotice/>`·`<UnallocatedRow/>` 호출을 지워도 **한 개도 안 깨졌다**
//   (1차 변이 주입 15종 중 M11·M12 생존). 이게 정확히 이 저장소가 세 번 당한 모양이다 —
//   «백엔드는 세는데 화면이 안 읽음»의 컴포넌트판([[same-defect-three-times-fix-the-shape]]).
//   페이지 전체 렌더는 fetch 수십 개를 물고 와야 해서, 배선 사실 자체를 소스로 고정한다.
//   리팩터로 이름이 바뀌면 여기서 빨간불이 난다 — 그건 오탐이 아니라 «배선을 다시 확인하라»다.
describe("페이지 배선", () => {
  // ★소스를 vite의 `?raw`로 읽는다. `node:fs`를 쓰면 vitest는 통과하지만 `npm run build`의
  //   `tsc -b`가 @types/node 부재로 깨진다(빌드 tsconfig는 `types: ["vite/client"]`만 싣는다).
  //   ⚠️ 그리고 `tsc --noEmit -p tsconfig.json`으로는 그걸 못 잡는다 — 그 파일은 `files: []`인
  //   **솔루션 파일**이라 아무것도 검사하지 않고 exit 0을 낸다. 검증은 `tsc -b`(=npm run build)로.
  const mods = import.meta.glob("./NaverOps.tsx", { query: "?raw", import: "default", eager: true });
  const src = mods["./NaverOps.tsx"] as string;

  it("M11 — 배너를 실제로 렌더한다", () => {
    expect(src).toMatch(/<AdAllocationNotice[\s>]/);
  });

  it("M12 — 미배분 행을 실제로 렌더한다", () => {
    expect(src).toMatch(/<UnallocatedRow[\s>]/);
  });

  it("표에 광고비 열이 있다 — 얼마가 붙었는지 상품마다 보여야 한다", () => {
    expect(src).toMatch(/label="광고비"\s+sk="ad_spend"/);
  });
});

// ── 적대 리뷰 1R P1의 화면 몫 ────────────────────────────────────────
describe("판매 0건인데 광고비가 나간 상품", () => {
  it("배너가 «순수 손실»이라고 말한다 — 상품 행에 안 나타나므로 여기서만 보인다", () => {
    render(
      <AdAllocationNotice
        summary={summary()}
        adAlloc={{ ...AD_ALLOC, no_sale_cost: "30000.00", no_sale_products: 3 }}
        recon={RECON}
      />,
    );
    expect(screen.getByText(/판매가 0건/)).toBeTruthy();
    expect(screen.getByText(/30,000원/)).toBeTruthy();
  });

  it("0건이면 경고를 띄우지 않는다 — 늘 켜진 경고는 안 읽힌다", () => {
    render(<AdAllocationNotice summary={summary()} adAlloc={AD_ALLOC} recon={RECON} />);
    expect(screen.queryByText(/판매가 0건/)).toBeNull();
  });

  it("미배분 행도 그 몫이 섞여 있음을 말한다", () => {
    inTable(<UnallocatedRow unallocated={UNALLOC} uncoveredDays={0} noSaleCount={3} />);
    expect(screen.getByText(/판매 0건 상품 3개의 광고비 포함/)).toBeTruthy();
  });
});
