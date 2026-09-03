// NaverAdCreatives.tsx — 「소재 성과」. 소재별 ROAS를 BEP와 나란히 (D-NAO-140 S2).
//
// ★존재 이유: 캠페인 평균이 적자 소재를 가린다. 2026-08-03 실측으로 캠페인 03의 ROAS는
//   2.07~3.26인데 그 안의 소재 하나는 0.61이었다(3일 10.4만원 써서 6.4만원). 우리가 입찰을
//   조작하는 단위는 소재인데 성과는 그룹까지만 있어서, 그 소재는 존재조차 드러나지 않았다.
// ★판정은 BEP 대비로만 한다 — ROAS 숫자 자체는 상품마다 의미가 다르다(공헌이익률이 다르므로).
// ★BEP를 모르면 "판정 불가"라고 말한다. 모르는 걸 '미달'로 그리면 매핑 결손이 적자로 둔갑한다.
// ★조회 전용이다. 이 화면에서 광고를 조작하는 기능은 없다(D-NAO-140 계약 금지선).
import { useState } from "react";
import { Card, Table, Th, Td, Badge, Pager, Loading, EmptyState, LayerNav } from "../components/ui";
import { PeriodRangeBar, type PeriodPreset } from "../components/PeriodRangeBar";
import { presetWindowKeepingToday } from "../lib/periodRange";
import { usePeriodRange } from "../lib/usePeriod";
import { useAsyncData } from "../lib/useAsyncData";
import { num } from "../lib/format";
import { fetchNaverCreatives, type NaverCreativeRow } from "../lib/api";

const PAGE = 50;

/** 종전 `PeriodTabs`의 프리셋 넷(당일·어제·7일·30일)을 그대로 옮긴다 — 버튼이 조용히
 *  사라지면 그건 기능 회귀다. 90일은 이 화면 상한(365일) 안이라 덤으로 붙인다. */
const CREATIVE_PERIOD_PRESETS: PeriodPreset[] = ["today", "yesterday", "7d", "30d", "90d"];

export default function NaverAdCreatives() {
  // ★기본 창은 종전 `usePeriod()`의 기본값(당일)과 같다 — 열자마자 보던 것이 안 바뀐다.
  const p = usePeriodRange();
  const { from, to, setFrom, setTo } = p;
  // ★기본은 "적자만"이 아니라 전체다. 필터를 기본으로 걸면 안 보이는 광고비가 생기고,
  //   그건 이 화면이 없애려는 문제(가려진 소재)를 형태만 바꿔 되살린다.
  const [belowOnly, setBelowOnly] = useState(false);
  const queryKey = `${p.range.from}|${p.range.to}`;
  const [page, setPage] = useState({ key: queryKey, offset: 0 });
  const offset = page.key === queryKey ? page.offset : 0;

  return (
    <div className="space-y-4">
      <LayerNav />
      <Card
        title="소재 성과"
        right={
          <button
            type="button"
            onClick={() => setBelowOnly((v) => !v)}
            className={`px-2 py-0.5 text-xs rounded-full ${
              belowOnly ? "bg-amber-50 text-amber-700 font-semibold" : "text-gray-600 hover:bg-gray-100"
            }`}
          >
            BEP 미달만
          </button>
        }
      >
        {/* 기간 바 — PAO 화면 공통(Jino 2026-09-03 *"새로 만든 캘린더를 Pao내의 모든
            캘린더에 똑같이 만들어줘"*). ★「당일」은 당일 그대로다 — 이 화면은 오늘 돌고
            있는 소재를 보는 자리라 `presetWindowKeepingToday`를 쓴다. */}
        <PeriodRangeBar
          label="성과 발생일"
          from={from} to={to} onFrom={setFrom} onTo={setTo}
          presets={CREATIVE_PERIOD_PRESETS}
          windowFor={presetWindowKeepingToday}
          note="소재별 광고비·전환이 일어난 날입니다. 당일은 전환이 아직 정착 전이라 나중에 값이 올라갈 수 있습니다."
        />
        {p.error ? (
          <EmptyState reason={p.error} hint="기간을 다시 선택하세요." />
        ) : (
          <CreativeTable
            from={p.range.from}
            to={p.range.to}
            label={p.label}
            belowOnly={belowOnly}
            offset={offset}
            onOffset={(n) => setPage({ key: queryKey, offset: n })}
          />
        )}
      </Card>
    </div>
  );
}

function CreativeTable({
  from, to, label, belowOnly, offset, onOffset,
}: {
  from: string; to: string; label: string; belowOnly: boolean;
  offset: number; onOffset: (n: number) => void;
}) {
  const { data, error } = useAsyncData(
    () => fetchNaverCreatives({ date_from: from, date_to: to, limit: PAGE, offset }),
    [from, to, offset],
  );

  if (error) {
    return <EmptyState reason={`불러오지 못했습니다: ${error}`} hint="새로고침하거나 백엔드 상태를 확인하세요." />;
  }
  if (data === null) return <Loading rows={5} />;
  if (data.rows.length === 0) {
    // ★0을 찍고 침묵하지 않는다 — 왜 비었는지 말한다. 이 수집은 2026-08-04에 시작됐고
    //   네이버 보고서 보관 한계로 그 이전 날짜는 원천적으로 없다.
    return (
      <EmptyState
        reason={`${label}: 소재별 성과 기록이 없습니다.`}
        hint="소재 단위 수집은 2026-08-04에 시작됐습니다 — 그 이전 날짜는 네이버 보고서 보관 한계로 채울 수 없습니다."
      />
    );
  }

  // 클라이언트 필터임을 숨기지 않는다: 아래 안내에 '이 페이지 안에서'라고 적는다.
  const rows = belowOnly ? data.rows.filter((r) => r.verdict === "below") : data.rows;

  return (
    <>
      <p className="px-4 py-2 text-xs text-gray-500 border-b border-gray-100">
        {label} · 소재 <span className="text-gray-700">{num(data.totals.ads)}</span>개 · 광고비{" "}
        <span className="text-gray-700">{num(data.totals.cost)}</span>원 · 전환매출{" "}
        <span className="text-gray-700">{num(data.totals.rev)}</span>원
        {belowOnly && (
          <span className="text-amber-600">
            {" "}· BEP 미달만 보는 중({num(rows.length)}/{num(data.rows.length)}, 이 페이지 안에서)
          </span>
        )}
      </p>
      <Table
        head={
          <>
            <Th>소재 · 상품</Th><Th>캠페인</Th><Th>입찰</Th><Th>노출·클릭</Th>
            <Th>광고비</Th><Th>전환매출</Th><Th>ROAS vs BEP</Th>
          </>
        }
      >
        {rows.map((r) => (
          <tr key={r.ad_id}>
            <Td><TargetCell row={r} /></Td>
            <Td>
              <div className="text-xs leading-tight" title={r.campaign_id}>
                <div className="text-gray-700">{r.campaign_name ?? r.campaign_id}</div>
                {r.adgroup_name && <div className="text-gray-400">{r.adgroup_name}</div>}
              </div>
            </Td>
            <Td><BidCell row={r} /></Td>
            <Td>
              <div className="text-xs tabular-nums text-gray-600">
                {num(r.imp)} · {num(r.clk)}
              </div>
            </Td>
            <Td><span className="text-xs tabular-nums">{num(r.cost)}원</span></Td>
            <Td>
              <div className="text-xs tabular-nums">
                {num(r.rev)}원
                <span className="text-gray-400"> · 전환 {num(r.conv)}</span>
              </div>
            </Td>
            <Td><VerdictCell row={r} /></Td>
          </tr>
        ))}
      </Table>
      <Pager total={data.total} offset={offset} pageSize={PAGE} onOffset={onOffset} />
    </>
  );
}

function TargetCell({ row }: { row: NaverCreativeRow }) {
  return (
    <div className="text-xs leading-tight" title={row.ad_id}>
      <div className={row.product_name ? "font-medium text-gray-800" : "text-gray-500"}>
        {row.product_name ?? "상품 매핑 없음"}
      </div>
      <div className="text-gray-400 tabular-nums">…{row.ad_id.slice(-9)}</div>
    </div>
  );
}

/** ★소재 입찰이 실효인지 밝힌다: `use_group_bid_amt=true`면 그룹 입찰이 이기므로 이 숫자를
 *  보고 판단하면 틀린다(소재 96%가 false라는 실측이 있지만 나머지 4%가 함정이다). */
function BidCell({ row }: { row: NaverCreativeRow }) {
  if (row.bid_amt == null) return <span className="text-xs text-gray-400">미수집</span>;
  return (
    <div className="text-xs leading-tight tabular-nums">
      <div className="text-gray-700">{num(row.bid_amt)}원</div>
      {row.use_group_bid_amt && <div className="text-[11px] text-gray-400">그룹 입찰 사용</div>}
    </div>
  );
}

/** ★3상태를 3상태로 그린다. "판정 불가"를 회색 미달처럼 그리면 매핑 결손이 적자로 읽힌다. */
function VerdictCell({ row }: { row: NaverCreativeRow }) {
  if (row.roas == null) {
    return <span className="text-xs text-gray-400">비용 0 — ROAS 없음</span>;
  }
  if (row.verdict == null) {
    return (
      <div className="text-xs leading-tight">
        <div className="tabular-nums text-gray-700">{row.roas.toFixed(2)}</div>
        <div className="text-[11px] text-gray-400">BEP 미상 — 판정 불가</div>
      </div>
    );
  }
  return (
    <div className="text-xs leading-tight space-y-0.5">
      <div className="tabular-nums text-gray-800">
        {row.roas.toFixed(2)} <span className="text-gray-400">/ BEP {row.bep_roas?.toFixed(2)}</span>
      </div>
      <Badge tone={row.verdict === "below" ? "alert" : "neutral"}>
        {row.verdict === "below" ? "BEP 미달" : "BEP 이상"}
      </Badge>
    </div>
  );
}
