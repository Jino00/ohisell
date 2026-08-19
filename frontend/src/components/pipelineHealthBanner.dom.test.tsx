// @vitest-environment jsdom
//
// pipelineHealthBanner.dom.test.tsx — 배너의 «표시» 절반 (D-NAO-205).
//
// ★적대 리뷰(2026-08-19)가 짚은 구멍: 판정 함수 테스트 47건이 있는데도 **화면은 무방비**였다.
//   토글 조건을 `>1`→`>0`으로 바꾸거나, 접힘에서 `items[0]` 대신 마지막 항목을 그리거나,
//   펼침 `<ul>`을 통째로 지워도 전건 통과했다. 순수 함수 테스트는 이 셋을 **원리적으로 못 본다**.
//
// ⚠️짝 파일: `pipelineHealthBanner.test.ts`(판정·등급). 여기는 «무엇이 보이나»만 본다.
//   한쪽을 지우면 나머지가 조용히 절반만 지키게 되므로 둘은 같이 산다.
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { PipelineHealthBanner } from "./PipelineHealthBanner";

afterEach(cleanup);

const ITEMS = [
  "네이버 스마트스토어 주문이 덜 수집됨 — 최근: [부분수집] 미완주 1일: 2026-08-18",
  "디스크 여유 부족 95.5% — 포화 시 전 수집 잡이 조용히 멈춘다",
  "잡 실패: auto_sync_orders",
];

const show = (items: string[]) =>
  render(
    <MemoryRouter>
      <PipelineHealthBanner items={items} />
    </MemoryRouter>,
  );

const toggle = () => screen.queryByRole("button", { name: /외 \d+건|접기/ });

describe("PipelineHealthBanner — 접힘(기본)", () => {
  it("맨 앞 1건만 보이고 나머지는 안 보인다", () => {
    show(ITEMS);
    expect(screen.getByText(ITEMS[0])).toBeTruthy();
    expect(screen.queryByText(ITEMS[1])).toBeNull();
    expect(screen.queryByText(ITEMS[2])).toBeNull();
  });

  it("보이는 1건은 «맨 앞»이다 — 마지막을 그리면 우선순위 정렬이 무의미해진다", () => {
    show(ITEMS);
    expect(screen.queryByText(ITEMS[2])).toBeNull();
  });

  it("「외 N-1건 ▾」 토글이 있고 aria-expanded=false다", () => {
    show(ITEMS);
    const b = toggle()!;
    expect(b.textContent).toBe("외 2건 ▾");
    expect(b.getAttribute("aria-expanded")).toBe("false");
  });

  it("목록(<ul>)은 렌더되지 않는다", () => {
    const { container } = show(ITEMS);
    expect(container.querySelector("ul")).toBeNull();
  });
});

describe("PipelineHealthBanner — 펼침", () => {
  it("★토글을 누르면 전건이 보인다 (이 변경의 목적)", () => {
    const { container } = show(ITEMS);
    fireEvent.click(toggle()!);
    const lis = [...container.querySelectorAll("li")].map((li) => li.textContent);
    expect(lis).toEqual(ITEMS);
  });

  it("헤더에 건수가 붙고 토글이 「접기 ▴」로 바뀐다", () => {
    show(ITEMS);
    fireEvent.click(toggle()!);
    expect(screen.getByText(/파이프라인 경고 \(3건\)/)).toBeTruthy();
    const b = toggle()!;
    expect(b.textContent).toBe("접기 ▴");
    expect(b.getAttribute("aria-expanded")).toBe("true");
  });

  it("aria-controls가 실제 목록 id를 가리킨다", () => {
    const { container } = show(ITEMS);
    fireEvent.click(toggle()!);
    const id = toggle()!.getAttribute("aria-controls");
    expect(id).toBeTruthy();
    expect(container.querySelector(`ul#${id}`)).toBeTruthy();
  });

  it("다시 누르면 접힌다", () => {
    const { container } = show(ITEMS);
    fireEvent.click(toggle()!);
    fireEvent.click(toggle()!);
    expect(container.querySelector("ul")).toBeNull();
  });
});

describe("PipelineHealthBanner — 경고 1건", () => {
  it("토글이 안 뜬다 (불필요한 UI 없음)", () => {
    show([ITEMS[0]]);
    expect(toggle()).toBeNull();
    expect(screen.getByText(ITEMS[0])).toBeTruthy();
  });

  it("건수 헤더도 안 붙는다", () => {
    show([ITEMS[0]]);
    expect(screen.queryByText(/\(1건\)/)).toBeNull();
  });
});

describe("PipelineHealthBanner — 펼친 뒤 항목이 줄어들 때 (적대 리뷰 P2)", () => {
  it("★1건으로 줄면 자동으로 접힌다 — 안 그러면 «접을 방법이 없는 펼침»이 남는다", () => {
    const { container, rerender } = show(ITEMS);
    fireEvent.click(toggle()!);
    expect(container.querySelector("ul")).toBeTruthy();

    rerender(
      <MemoryRouter>
        <PipelineHealthBanner items={[ITEMS[0]]} />
      </MemoryRouter>,
    );
    expect(toggle()).toBeNull();              // 토글 버튼이 사라진다
    expect(container.querySelector("ul")).toBeNull();   // 목록도 같이 접힌다
    expect(screen.queryByText(/\(1건\)/)).toBeNull();
  });
});
