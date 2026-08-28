// costHome.tsx — 「💰 원가」의 **새 첫 화면**: 보드 스트립 + 할 일 인박스 + 왕복 표
// (계약 `docs/contracts/CONTRACT_cost_excel_roundtrip.md` §4 S2 · 설계 `docs/PLAN_cost_menu_s2_screen.md` Q1·Q3)
//
// ## 이 파일이 하는 일과 «안» 하는 일
//
// - **한다**: 세 덩어리의 «껍데기». 기존 3탭(부자재·레시피·표준원가 보드)은 그대로 살아 있고,
//   여기서 누르면 그 탭의 **기존 컴포넌트**로 간다(드릴다운). 「심플하게」의 구현은 기능 제거가
//   아니라 **입구 수 축소**다 — 조작은 전부 남는다.
// - **안 한다**: 새 숫자를 «계산»하지 않는다. 보드 스트립은 `/api/cost/board` payload를 **그대로**
//   읽는다 — 같은 화면의 두 자리가 다른 숫자를 말하면 그게 결함이다. 표는 **읽기 전용**이고
//   [다운로드]는 S3까지 비활성이다(계약 §1이 S3를 이번 범위에서 뺐다).
//
// ★**왜 `CostPage.tsx`가 아니라 별도 파일인가**: 컴포넌트 export가 있는 `.tsx`에 export를 더
//   얹을 때마다 `react-refresh/only-export-components` 경고가 붙는데, CI 상한이 96이고 실측이
//   정확히 96/96이라 **여유가 0이다**(costImportedGoods.ts가 같은 자리에서 98로 CI를 빨갛게
//   만든 적이 있다). 그래서 새 컴포넌트는 여기 산다. 이 파일은 **컴포넌트만** export한다.
//
// ★`./CostPage`에서 표시 함수 넷을 들여온다 — `CostPage.tsx`도 이 파일을 들여오므로 **순환**이다.
//   그런데도 사본을 안 만드는 이유: 「승인/미확인」·「원장/등록가」·「—(폼팩터 없음)」·「원」의
//   어휘가 홈과 기존 탭에서 갈라지면 **같은 종을 두 자리가 다르게 부른다**. 이 저장소가 반복해
//   밟은 병이 정확히 그것이다(`priceSourceLabel` docstring: *"사본을 두면 다음에 한쪽만 바뀐다"*).
//   순환이 안전한 이유는 넷 다 **함수 선언**(호이스팅됨)이고 **렌더 시점에만** 불리기 때문이다 —
//   모듈 최상위에서 읽는 자리가 하나도 없다.
import type { ReactNode } from "react";

import type { CostBoard, CostMaterial } from "../lib/api";
import { formatKstDateTime } from "../lib/costMenuSurface";
import {
  ROUND_TRIP_COLUMNS,
  roundTripBadges,
  roundTripCountText,
  type CostInboxGroup,
  type CostInboxTarget,
  type RoundTripFilter,
} from "../lib/costHome";
import {
  formatCostWon,
  formFactorLabel,
  materialStatusLabel,
  priceSourceLabel,
} from "./CostPage";

/** 보드 스트립 — 「지금 어디까지 왔나」의 숫자 한 줄. **읽기 전용**이다.
 *
 * ★숫자는 전부 «있는 payload»에서 온다. 새로 세지 않는다 — 홈이 자기 계산으로 「원가 있는
 * SKU」를 세면 표준원가 보드 탭과 다른 숫자가 나올 수 있고, 그러면 어느 쪽이 참인지 화면이
 * 못 말한다(D-CPP-60 §0-A의 「규칙 두 곳 복제」와 같은 결).
 *
 * ★분모를 **지어내지 않는다**: 「원가 있는 SKU」의 분모는 `board.sku_count`(보드에 실린 SKU)이지
 * `product_master` 963행이 아니다. 두 축을 섞은 오독이 이 트랙에서 이미 한 번 있었다. */
export function CostBoardStrip({
  board,
  materialTotal,
  materialWithPrice,
  lastUploadedAt,
  onGoMaterials,
  onGoRecipes,
  onGoBoard,
}: {
  board: CostBoard | null;
  materialTotal: number;
  materialWithPrice: number;
  /** 마지막 원가표 업로드 시각(naive UTC). `null`이면 **한 번도 안 올렸다**는 사실이다. */
  lastUploadedAt: string | null;
  onGoMaterials: () => void;
  onGoRecipes: () => void;
  onGoBoard: () => void;
}) {
  const tiles: { key: string; label: string; value: string; onClick?: () => void }[] = [
    {
      key: "materials",
      label: "단가 있는 종",
      value: `${materialWithPrice}/${materialTotal}`,
      onClick: onGoMaterials,
    },
    {
      key: "sku",
      label: "원가 있는 SKU",
      value: board ? `${board.computed_count}/${board.sku_count}` : "불러오는 중…",
      onClick: onGoBoard,
    },
    {
      key: "recipes",
      label: "승인 레시피 (= 표준원가 계산됨)",
      value: board ? `${board.approved_recipe_count}/${board.recipe_count}` : "불러오는 중…",
      onClick: onGoRecipes,
    },
    {
      key: "uncomputed",
      label: "계산 안 된 SKU",
      value: board ? `${board.uncomputed_count}건` : "불러오는 중…",
      onClick: onGoBoard,
    },
    {
      key: "upload",
      label: "마지막 원가표 업로드",
      value: lastUploadedAt ? formatKstDateTime(lastUploadedAt) : "한 번도 안 올렸다",
    },
  ];
  return (
    <section className="mt-4" data-testid="cost-home-board-strip">
      <h2 className="text-sm font-semibold text-gray-700">보드</h2>
      <div className="mt-2 flex flex-wrap gap-2">
        {tiles.map((t) => (
          <button
            key={t.key}
            type="button"
            disabled={!t.onClick}
            onClick={t.onClick}
            data-testid={`board-tile-${t.key}`}
            className="text-left border rounded-md px-3 py-2 bg-white hover:bg-gray-50 disabled:hover:bg-white disabled:cursor-default min-w-[9rem]"
          >
            <div className="text-[11px] text-gray-500">{t.label}</div>
            <div className="text-sm font-semibold text-gray-900">{t.value}</div>
          </button>
        ))}
      </div>
      {/* ★분모의 뜻을 화면이 말한다 — 안 말하면 「원가 있는 SKU 448/924」의 924가
          `product_master` 963행과 같은 축인 줄 읽힌다(다른 축이다). */}
      <p className="mt-1.5 text-[11px] text-gray-500" data-testid="board-strip-denominator-note">
        「원가 있는 SKU」의 분모는 <b>보드에 실린 SKU</b>다 — 상품 마스터 전체가 아니다. 이 숫자는
        표준원가 보드 탭이 쓰는 값 그대로다(두 자리가 다른 숫자를 말하지 않게).
      </p>
    </section>
  );
}

/** 할 일 인박스 — «사람이 손대야 움직이는 것»의 목록.
 *
 * ★**0건이어도 묶음을 감추지 않는다.** 「빈 인박스」와 「인박스가 안 뜸」이 같은 화면이 되면
 * 안 된다(계약 §3의 「없음」≠「0」과 같은 결) — 그래서 0건 묶음은 `— 없음`으로 선다. */
export function CostTodoInbox({
  groups,
  onGoTarget,
  onGoGroup,
}: {
  groups: CostInboxGroup[];
  /** 항목 한 줄 → 그 일을 «하는 자리»(드릴다운). */
  onGoTarget: (target: CostInboxTarget) => void;
  /** 묶음 머리 ▸ → 그 묶음의 자리(왕복 표 필터 또는 레시피 탭). */
  onGoGroup: (group: CostInboxGroup) => void;
}) {
  return (
    <section className="mt-5" data-testid="cost-home-inbox">
      <h2 className="text-sm font-semibold text-gray-700">할 일 인박스</h2>
      <div className="mt-2 border rounded-md divide-y">
        {groups.map((g) => (
          <div key={g.key} data-testid={`inbox-group-${g.key}`}>
            <div className="flex items-start justify-between gap-3 px-3 py-2">
              <div className="min-w-0">
                <div className="text-sm font-medium text-gray-800">{g.title}</div>
                <div className="text-[11px] text-gray-500 mt-0.5">{g.source}</div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <span
                  className={`text-sm font-semibold ${
                    g.rows.length === 0 ? "text-gray-400" : "text-amber-700"
                  }`}
                  data-testid={`inbox-count-${g.key}`}
                >
                  {/* ★0건은 줄을 지우는 게 아니라 「— 없음」이라고 말한다. */}
                  {g.rows.length === 0 ? "— 없음" : `${g.rows.length}건`}
                </span>
                <button
                  type="button"
                  className="text-xs px-2 py-1 rounded border text-gray-600 hover:bg-gray-50 disabled:opacity-40"
                  disabled={g.goto === null || g.rows.length === 0}
                  data-testid={`inbox-goto-${g.key}`}
                  onClick={() => onGoGroup(g)}
                >
                  ▸
                </button>
              </div>
            </div>
            {g.rows.length > 0 ? (
              <details className="px-3 pb-2">
                <summary className="text-xs text-blue-700 cursor-pointer">
                  {g.rows.length}건 펼쳐 보기
                </summary>
                <ul className="mt-1 space-y-0.5" data-testid={`inbox-rows-${g.key}`}>
                  {g.rows.map((row) => (
                    <li key={row.key} className="text-xs">
                      {row.target ? (
                        <button
                          type="button"
                          className="text-left text-blue-700 hover:underline"
                          data-testid={`inbox-row-${row.key}`}
                          onClick={() => onGoTarget(row.target)}
                        >
                          {row.label}
                        </button>
                      ) : (
                        <span data-testid={`inbox-row-${row.key}`} className="text-gray-700">
                          {row.label}
                        </span>
                      )}
                      {row.note ? (
                        <span className="text-gray-500"> — {row.note}</span>
                      ) : null}
                      {/* ★갈 곳이 없으면 «없다고» 말한다 — 조용히 안 눌리는 줄은
                          「고장났나」로 읽힌다. */}
                      {row.target === null ? (
                        <span className="text-gray-400">
                          {" "}
                          · 아직 어느 레시피도 이 항목을 안 골랐다 — 화면이 대신 고르지 않는다
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </div>
        ))}
      </div>
    </section>
  );
}

/** 왕복 표 — 부자재 한 줄 = 식별자 있는 한 행. **이 표가 곧 S3 다운로드 파일의 모양이다.**
 *
 * ★S2에서는 **읽기 전용**이다: 값을 고치는 자리는 행을 눌러 여는 부자재 드릴다운(기존 패널)이고,
 * 파일로 고치는 길은 S3·S4다. 그래서 열마다 «파일에서 고칠 수 있나»만 미리 말한다. */
export function CostRoundTripTable({
  rows,
  totalCount,
  filter,
  onFilterChange,
  onSelectRow,
  filterBar,
}: {
  /** 이미 필터가 적용된 행. */
  rows: CostMaterial[];
  /** 필터 «전» 전체 종 수 — 필터가 만든 0건과 «원래 없음»을 가른다. */
  totalCount: number;
  filter: RoundTripFilter;
  onFilterChange: (next: RoundTripFilter) => void;
  /** 행 클릭 → 부자재 드릴다운(기존 패널 그대로). */
  onSelectRow: (materialId: number) => void;
  /** 폼팩터·부품 드롭다운. 기존 `ProductOptionPicker`를 호출부가 그대로 넘긴다
   *  (새 피커를 만들면 네 벌이 되고 네 벌은 갈라진다 — `RecipeDetail`의 `picker`와 같은 관례). */
  filterBar: ReactNode;
}) {
  return (
    <section className="mt-5" data-testid="cost-home-roundtrip">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="text-sm font-semibold text-gray-700">
          왕복 표 (부자재 {totalCount}종 — 이 표가 다운로드 파일의 모양이다)
        </h2>
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled
            title="S3에서 만든다 — 지금은 표만 서 있다"
            data-testid="roundtrip-download"
            className="text-xs px-2 py-1 rounded border text-gray-400 border-gray-300 cursor-not-allowed"
          >
            다운로드 — S3에서 만든다
          </button>
        </div>
      </div>
      <p className="mt-1 text-[11px] text-gray-500" data-testid="roundtrip-rule-note">
        행 키는 부자재 종의 <b>ID</b>다 — 이름이 아니다. 이름을 키로 쓰면 개명이 「사라짐 + 새
        행」으로 오독되어, 파일에서 오타 하나 고친 종이 죽는다. 이 화면에서 표는 <b>읽기 전용</b>
        이고, 값을 고치는 자리는 행을 눌러 여는 부자재 상세다.
      </p>

      <div className="mt-2">{filterBar}</div>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <label className="text-xs text-gray-700 flex items-center gap-1">
          <input
            type="checkbox"
            data-testid="roundtrip-filter-no-price"
            checked={filter.noPriceOnly}
            onChange={(e) => onFilterChange({ ...filter, noPriceOnly: e.target.checked })}
          />
          단가 없음만
        </label>
        <label className="text-xs text-gray-700 flex items-center gap-1">
          <input
            type="checkbox"
            data-testid="roundtrip-filter-conflict"
            checked={filter.conflictOnly}
            onChange={(e) => onFilterChange({ ...filter, conflictOnly: e.target.checked })}
          />
          모순만
        </label>
        <span className="text-xs text-gray-600" data-testid="roundtrip-count">
          {roundTripCountText(rows.length, totalCount)}
        </span>
      </div>

      {rows.length === 0 ? (
        <div className="mt-2 text-xs text-gray-500 border border-dashed rounded p-4">
          {totalCount === 0
            ? "등록된 부자재 종이 없다."
            : "해당 조건에 맞는 부자재 종이 없다 — 필터를 풀거나 다른 폼팩터를 고른다."}
        </div>
      ) : (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-gray-500">
              <tr className="text-left border-b align-bottom">
                {ROUND_TRIP_COLUMNS.map((c) => (
                  <th key={c.key} className="py-1 pr-2 whitespace-nowrap" title={c.note}>
                    <div>{c.label}</div>
                    {/* ★열마다 「파일에서 고칠 수 있나」를 미리 말한다 — 안 말하면 사람이
                        S3 파일을 받아 고쳐 올린 «뒤»에야 반영 불가를 알게 된다. */}
                    <div
                      className={`text-[10px] font-normal ${
                        c.editable ? "text-blue-600" : "text-gray-400"
                      }`}
                      data-testid={`roundtrip-col-${c.key}-editable`}
                    >
                      {c.editable ? "수정 가능" : "읽기전용"}
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((m) => {
                const badges = roundTripBadges(m);
                return (
                  <tr
                    key={m.id}
                    data-testid={`roundtrip-row-${m.id}`}
                    className="border-b last:border-0 cursor-pointer hover:bg-gray-50"
                    onClick={() => onSelectRow(m.id)}
                  >
                    <td className="py-1 pr-2 font-mono">{m.id}</td>
                    <td className="py-1 pr-2 max-w-[18rem] truncate" title={m.name}>
                      {m.name}
                    </td>
                    <td className="py-1 pr-2">{formFactorLabel(m.form_factor)}</td>
                    <td className="py-1 pr-2">{m.part && m.part.trim() ? m.part : "—"}</td>
                    <td className="py-1 pr-2">{m.unit ?? "—"}</td>
                    {/* ★단가 없음 행은 **빈 칸**이지 0원이 아니다 — `formatCostWon(null)`이
                        「—」를 낸다. 그 사실을 말하는 원문은 아래 배지의 툴팁에 산다. */}
                    <td className="py-1 pr-2 text-right font-medium">
                      {formatCostWon(m.latest_price_ex_vat)}
                    </td>
                    <td className="py-1 pr-2 text-right text-gray-600">
                      {formatCostWon(m.latest_price_inc_vat)}
                    </td>
                    <td className="py-1 pr-2 text-gray-500">
                      {m.latest_price_inc_derived ? "×1.1" : "—"}
                    </td>
                    <td className="py-1 pr-2">
                      {m.latest_price_source ? priceSourceLabel(m.latest_price_source) : "—"}
                    </td>
                    {/* ★`whitespace-nowrap` — 이 표는 `w-full`이라 브라우저가 「상태 / 비고」의
                        긴 글에 폭을 몰아주고 이 칸을 짜낸다. 그러면 `2026-08-24`가 하이픈에서
                        접혀 **한 행이 두 줄**이 되고, 139행 전체의 높이가 들쭉날쭉해진다
                        (Jino 2026-08-28 11:09 «날짜가 2줄이 되지 않도록»). 헤더 `th`는 이미
                        nowrap이라 열 폭은 헤더가 잡아 주는데, 접히던 것은 값 쪽이었다. */}
                    <td className="py-1 pr-2 whitespace-nowrap">
                      {m.latest_price_effective_date ?? "—"}
                    </td>
                    <td className="py-1 pr-2 text-right text-gray-500">
                      {formatCostWon(m.excel_ref_price)}
                    </td>
                    <td className="py-1 pr-2">
                      <span
                        className={
                          m.status === "approved" ? "text-green-700" : "text-amber-800"
                        }
                      >
                        {materialStatusLabel(m.status)}
                      </span>
                      {m.note ? <span className="text-gray-500"> · {m.note}</span> : null}
                      {badges.map((b) => (
                        <span
                          key={b.key}
                          title={b.title}
                          data-testid={`roundtrip-badge-${m.id}-${b.key}`}
                          className="ml-1 text-[10px] px-1 rounded border border-gray-300 text-gray-600"
                        >
                          {b.label}
                        </span>
                      ))}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
