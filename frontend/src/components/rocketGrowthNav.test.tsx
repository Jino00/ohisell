// @vitest-environment jsdom
//
// rocketGrowthNav.test.tsx — 사이드바에 「쿠팡 로켓그로스(2P)」 그룹과 하위 3링크가 실제로
// 그려지는가 (계약 `docs/contracts/CONTRACT_2p_own_screens.md` D-CPP-54 §1-B-2).
//
// Jino 원문(2026-08-23 실측이 이 화면들을 만든 이유): "2P에 대한 내용이 sellc의 데시보드와
// 서브메뉴에 전혀 안보이는게 문제". Layout.tsx의 `ROCKET_GROWTH_GROUP` 상수는 존재만으로는
// 아무것도 보장하지 않는다 — `DASHBOARD_CHILDREN` 배열에서 빠지면(변이⑧) 상수는 죽은 채
// 남고 사이드바는 여전히 침묵한다. 그래서 값이 아니라 **렌더된 DOM**을 검사한다.
//
// 짝 검사: `InventoryPage.tsx`의 `channelFromTabParam()` — 사이드바 세 번째 링크
// `/inventory?tab=rg`가 실제로 «RG 탭에 착지»하는지는 이 함수가 결정한다(변이⑨). 기본값이
// 우연히 "로켓그로스"라 값 비교만으로는 「맞는 곳에 도착한 것처럼」 보이므로, 기본값과 다른
// `?tab=1p`/`?tab=wing`으로 재서 잡는다.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  // Layout이 상주시키는 세 폴링 + SchedulerStatus의 fetchApi — 전부 실패해도 조용히
  // 삼켜지는 경로다(각 컴포넌트의 .catch). 실제 네트워크를 타지 않게만 막는다.
  getAdCostCookieStatus: () => Promise.reject(new Error("no network in test")),
  getSchedulerHealth: () => Promise.reject(new Error("no network in test")),
  getCollectionStatus: () => Promise.reject(new Error("no network in test")),
  fetchApi: () => Promise.reject(new Error("no network in test")),
}));

import Layout from "./Layout";
import InventoryPage, { channelFromTabParam } from "../pages/InventoryPage";

afterEach(() => cleanup());

const renderLayout = () =>
  render(
    <MemoryRouter initialEntries={["/"]}>
      <Layout />
    </MemoryRouter>,
  );

describe("사이드바 — 「쿠팡 로켓그로스(2P)」 그룹 (변이⑧)", () => {
  it("그룹 헤더가 사이드바에 보인다", () => {
    renderLayout();
    expect(screen.getByText("쿠팡 로켓그로스(2P)")).toBeTruthy();
  });

  it("헤더를 펼치면 하위 3링크(손익·정산·근거·재고 발송관제)가 올바른 href로 뜬다", () => {
    renderLayout();
    // 헤더 텍스트를 클릭 — 이벤트는 부모 <button onClick>으로 버블링된다.
    fireEvent.click(screen.getByText("쿠팡 로켓그로스(2P)"));

    const pnlLink = screen.getByText("손익(판매일 축)").closest("a");
    expect(pnlLink?.getAttribute("href")).toBe("/rocket-growth");

    const settlementLink = screen.getByText("정산·근거").closest("a");
    expect(settlementLink?.getAttribute("href")).toBe("/rocket-growth/settlement");

    const inventoryLink = screen.getByText("재고·발송관제").closest("a");
    expect(inventoryLink?.getAttribute("href")).toBe("/inventory?tab=rg");
  });

  it("1P 그룹(「쿠팡 로켓배송(1P)」)은 그대로 살아 있다 — 2P 추가가 1P를 밀어내지 않았다", () => {
    renderLayout();
    expect(screen.getByText("쿠팡 로켓배송(1P)")).toBeTruthy();
  });
});

describe("channelFromTabParam() — ?tab= 딥링크 매핑 (변이⑨)", () => {
  it('"rg" → 「로켓그로스」', () => {
    expect(channelFromTabParam("rg")).toBe("로켓그로스");
  });

  it('"1p" → 「로켓배송 1P」 — 기본값(로켓그로스)과 다른 값이라 값 비교로 잡힌다', () => {
    expect(channelFromTabParam("1p")).toBe("로켓배송 1P");
  });

  it('"wing" → 「Wing」', () => {
    expect(channelFromTabParam("wing")).toBe("Wing");
  });

  it("대소문자 무관 — \"RG\"도 「로켓그로스」", () => {
    expect(channelFromTabParam("RG")).toBe("로켓그로스");
  });

  it("모르는 값이면 null — 기존 기본값을 화면이 스스로 지키게 둔다", () => {
    expect(channelFromTabParam("unknown")).toBeNull();
  });

  it("null이면 null", () => {
    expect(channelFromTabParam(null)).toBeNull();
  });
});

// ★변이⑨는 순수 함수 테스트만으로는 안 죽는다(함수 자체는 안 다치므로) — «호출부 배선»이
//   끊기는 변이라 InventoryPage를 실제로 그 route로 렌더해야 잡힌다. 기본값이 우연히
//   "로켓그로스"라 `?tab=rg`로는 안 잡히므로 기본값과 다른 `?tab=1p`/`?tab=wing`으로 잰다.
describe("InventoryPage — ?tab= 딥링크가 실제로 «착지»한다 (변이⑨, 배선)", () => {
  const renderInventory = (path: string) =>
    render(
      <MemoryRouter initialEntries={[path]}>
        <InventoryPage />
      </MemoryRouter>,
    );

  it("?tab=1p로 열면 「로켓배송 1P」 탭 콘텐츠가 뜬다 — 기본값(로켓그로스)이 아니다", () => {
    renderInventory("/inventory?tab=1p");
    expect(screen.getByText(/로켓배송 1P 재고 — 준비 중/)).toBeTruthy();
    // 채널 탭 버튼 자체는 항상 "로켓그로스"라는 라벨을 그린다(탭 3개 고정 목록) — 그건 기본값
    // 여부와 무관하다. 판정 대상은 로켓그로스 전용 서브탭("발송관제"/"청구 감사")이 안 뜨는가다.
    expect(screen.queryByText("발송관제")).toBeNull();
    expect(screen.queryByText("청구 감사")).toBeNull();
  });

  it("?tab=wing으로 열면 「Wing」 탭 콘텐츠가 뜬다", () => {
    renderInventory("/inventory?tab=wing");
    expect(screen.getByText(/Wing 재고 — 준비 중/)).toBeTruthy();
  });

  it("?tab= 없이 열면 기본값 그대로 로켓그로스 탭(발송관제 서브탭)이 뜬다", () => {
    renderInventory("/inventory");
    expect(screen.getByText("발송관제")).toBeTruthy();
  });
});

// ════════════════════════ 변이 주입 결과 (실행 확인, 원복 완료·소스 영구 변경 없음) ════════════════════════
// ⑧ Layout.tsx DASHBOARD_CHILDREN 배열에서 ROCKET_GROWTH_GROUP 항목 삭제
//     → "그룹 헤더가 사이드바에 보인다" · "헤더를 펼치면 하위 3링크가 …" 2건 RED
//       ("1P 그룹은 그대로 살아 있다" 1건은 그린 — 2P 삭제가 1P를 안 건드렸다는 걸 정확히 구분).
//       12건 중 10건 그린 · 2건 RED. 원복 후 12/12 그린.
// ⑨ InventoryPage.tsx `channelFromTabParam(searchParams.get("tab")) ?? "로켓그로스"` 호출부를
//    지우고 `useState<Channel>("로켓그로스")` 고정값으로 치환
//     → "?tab=1p로 열면 …" · "?tab=wing으로 열면 …" 2건 RED("로켓배송 1P 재고 — 준비 중"/
//       "Wing 재고 — 준비 중" 텍스트가 안 뜸). "?tab= 없이 열면 기본값 그대로 …" 1건은
//       그린(기본값 케이스라 변이와 무관 — 정상). channelFromTabParam() 순수 함수 단위
//       테스트 6건도 전부 그린(함수 자체는 안 다쳤다 — 배선만 끊었다는 걸 그대로 보여줌).
//       12건 중 10건 그린 · 2건 RED. 원복 후 12/12 그린.
// 두 변이 모두 주입 직후 대상 테스트만 정확히 RED, 원복 직후 12/12 그린으로 재확인했다. 소스
// 파일은 git diff로 매번 원본과 바이트 동일함을 확인했다(diff 출력 없음).
