// Rocket1PFunnel.tsx — 로켓배송(1P) 유입·전환 퍼널 화면 (조회 전용).
//
// Jino(2026-08-06): "조회·주문·장바구니·검색량까지 보는건 별도의 전문 메뉴를 만드는게
//   이 디테일한 자료까지 보는데 도움이 될거 같은데?"
//
// 이 화면이 답하는 질문은 매출 화면(S2)과 **다르다**:
//   매출 화면 = "얼마 벌었나"(합계 중심) · 이 화면 = "왜 안 팔리나"(옵션 단위로 깊게)
//   그래서 같은 표에 얹지 않고 메뉴를 갈랐다.
//
// ★정직성 규칙 4개:
//   1) **전환율은 Σ주문/Σ조회.** 일별 비율의 평균이 아니다 — 조회 4회에 1건인 날(25%)과
//      400회에 40건인 날(10%)을 평균 내면 17.5%가 되는데 실제는 10.1%다.
//   2) **위치는 서술이지 권고가 아니다.** 전략 추천은 하지 않는다(금지선). 기간 중앙값 대비
//      상/하만 말하고, 쓰인 임계값을 화면에 그대로 적는다 — 숨은 기준의 분류는 의견이다.
//   3) **표본이 얕으면 판정하지 않는다.** 조회 3회의 33%를 상위로 올리면 표가 거짓말한다.
//   4) **모름은 "—".** 리뷰 0건의 평점은 0점이 아니고, 수집 안 된 조회는 0회가 아니다.
import { useState } from "react";
import { Card, Stat, Table, Th, Td, Loading, EmptyState, Badge } from "../components/ui";
import { useAsyncData } from "../lib/useAsyncData";
import { FreshnessNote } from "../components/FreshnessNote";
import {
  fetchRocket1PFunnel,
  type FunnelPosition,
  type Rocket1PFunnelOption,
} from "../lib/api";

const NO_DATA = "—";

const num = (v: number | null | undefined) => (v == null ? NO_DATA : v.toLocaleString("ko-KR"));

const pct = (v: string | number | null | undefined, digits = 2) => {
  if (v == null) return NO_DATA;
  const n = typeof v === "string" ? Number(v) : v;
  return Number.isFinite(n) ? `${(n * 100).toFixed(digits)}%` : NO_DATA;
};

const dec = (v: string | null, digits = 2) => {
  if (v == null) return NO_DATA;
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(digits) : NO_DATA;
};

const won = (v: string | null) => {
  if (v == null) return NO_DATA;
  const n = Number(v);
  return Number.isFinite(n) ? `${Math.round(n).toLocaleString("ko-KR")}원` : NO_DATA;
};

/** 위치 라벨 — **서술**이다. 무엇을 하라는 말이 아니라 어디에 있는지다. */
const POSITION: Record<FunnelPosition, { label: string; tone: "neutral" | "alert" }> = {
  views_high_cvr_high: { label: "조회↑ 전환↑", tone: "neutral" },
  views_high_cvr_low: { label: "조회↑ 전환↓", tone: "alert" },
  views_low_cvr_high: { label: "조회↓ 전환↑", tone: "alert" },
  views_low_cvr_low: { label: "조회↓ 전환↓", tone: "neutral" },
  low_sample: { label: "표본 부족", tone: "neutral" },
};

function isoKST(d: Date): string {
  const kst = new Date(d.toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
  return `${kst.getFullYear()}-${String(kst.getMonth() + 1).padStart(2, "0")}-${String(kst.getDate()).padStart(2, "0")}`;
}

function daysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return isoKST(d);
}

function OptionRow({ o }: { o: Rocket1PFunnelOption }) {
  const pos = POSITION[o.position];
  return (
    <tr className="hover:bg-gray-50">
      <Td>
        <div className="flex items-center gap-2">
          <span className="max-w-sm truncate text-gray-900" title={o.product_name ?? o.option_id}>
            {o.product_name ?? o.option_id}
          </span>
          {o.is_oos === true && <Badge tone="alert">품절</Badge>}
        </div>
        <div className="mt-0.5 text-xs text-gray-400">
          옵션 {o.option_id}
          {o.days_missing > 0 && ` · 지표 결손 ${o.days_missing}일`}
        </div>
      </Td>
      <Td right>{num(o.visitors)}</Td>
      <Td right>{num(o.page_views)}</Td>
      <Td right>{dec(o.views_per_visitor)}</Td>
      <Td right>{num(o.orders)}</Td>
      <Td right><span className="font-medium text-blue-700">{pct(o.cvr)}</span></Td>
      <Td right>{num(o.qty)}</Td>
      <Td right>{dec(o.units_per_order)}</Td>
      <Td right>{won(o.consumer_revenue)}</Td>
      <Td right>
        {o.rating_score == null ? NO_DATA : `${dec(o.rating_score, 1)} (${num(o.rating_count)})`}
      </Td>
      <Td><Badge tone={pos.tone}>{pos.label}</Badge></Td>
    </tr>
  );
}

export default function Rocket1PFunnel() {
  const [from, setFrom] = useState(daysAgo(6));
  const [to, setTo] = useState(isoKST(new Date()));
  const [minPv, setMinPv] = useState(30);

  const { data, error } = useAsyncData(
    () => fetchRocket1PFunnel({ from, to, limit: 300, minPageViews: minPv }),
    [from, to, minPv],
  );

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold text-gray-900">로켓배송(1P) 유입·전환 퍼널</h1>
        <p className="mt-1 text-sm text-gray-500">
          방문자 → 조회 → 주문 → 판매수량으로 내려가며 어느 단계에서 새는지 옵션별로 봅니다.
          조회 전용이며 전략 추천은 하지 않습니다 — 위치만 말합니다.
        </p>
      </div>

      <Card>
        <div className="flex flex-wrap items-end gap-3 px-4 py-3">
          <label className="text-sm">
            <span className="mr-2 text-gray-500">시작</span>
            <input type="date" value={from} onChange={(e) => setFrom(e.target.value)}
              className="rounded border border-gray-300 px-2 py-1" />
          </label>
          <label className="text-sm">
            <span className="mr-2 text-gray-500">종료</span>
            <input type="date" value={to} onChange={(e) => setTo(e.target.value)}
              className="rounded border border-gray-300 px-2 py-1" />
          </label>
          <label className="text-sm">
            <span className="mr-2 text-gray-500">판정 최소 조회수</span>
            <input type="number" min={0} value={minPv}
              onChange={(e) => setMinPv(Math.max(0, Number(e.target.value) || 0))}
              className="w-24 rounded border border-gray-300 px-2 py-1" />
          </label>
        </div>
      </Card>

      {error && (
        <Card><EmptyState reason="퍼널 데이터를 불러오지 못했습니다." hint={String(error)} /></Card>
      )}
      {!data && !error && <Card><Loading /></Card>}

      {data && (
        <>
          <Card title="기간 퍼널">
            <FreshnessNote f={data.freshness} />
            <div className="grid grid-cols-2 gap-4 px-4 py-4 md:grid-cols-7">
              <Stat label="방문자" value={num(data.totals.visitors)} />
              <Stat label="조회" value={num(data.totals.page_views)}
                sub={`방문자당 ${dec(data.totals.views_per_visitor)}회`} />
              <Stat label="주문" value={num(data.totals.orders)} />
              <Stat label="구매전환율" value={pct(data.totals.cvr)} tone="good"
                sub="주문 ÷ 조회" />
              <Stat label="판매수량" value={num(data.totals.qty)}
                sub={`주문당 ${dec(data.totals.units_per_order)}개`} />
              <Stat label="소비자 매출" value={won(data.totals.consumer_revenue)}
                sub="쿠팡가 기준(쿠팡의 매출)" />
              {/* ★"결손 없음"을 단언하지 않는다(적대 리뷰 P1): 이 카운터는 **존재하는 행의
                  빈 컬럼**만 센다. 그 날 행이 통째로 없는 결손은 원리적으로 안 잡히므로
                  위 FreshnessNote(날짜 축)와 **함께** 봐야 한다. 단위도 '일'이 아니라 옵션×일이다. */}
              <Stat label="빈 지표(옵션×일)"
                value={num(data.coverage.option_days_missing_metrics)}
                tone={data.coverage.option_days_missing_metrics > 0 ? "warn" : "idle"}
                sub={data.coverage.option_days_missing_metrics > 0
                  ? "합계가 부분값이다"
                  : "행 부재는 위 기준일로 확인"} />
            </div>
            <p className="mx-4 mb-4 text-xs text-gray-500">
              모든 비율은 <b>합계의 몫</b>입니다(Σ주문 ÷ Σ조회). 일별 비율의 평균이 아닙니다 —
              작은 날이 큰 날과 같은 비중을 갖게 되어 값이 틀어집니다.
            </p>
          </Card>

          <Card
            title={`옵션별 퍼널 (${data.shown}/${data.option_count})`}
            right={data.shown < data.option_count
              ? <Badge tone="alert">{data.option_count - data.shown}개 생략됨</Badge>
              : undefined}
          >
            <div className="mx-4 mt-3 rounded bg-gray-50 px-3 py-2 text-xs text-gray-600">
              「위치」는 <b>기간 중앙값 대비 상/하 서술</b>이며 권고가 아닙니다. 이번 판정 기준 —
              중앙 전환율 <b>{pct(data.thresholds.median_cvr)}</b> · 중앙 조회수{" "}
              <b>{num(data.thresholds.median_page_views == null ? null : Number(data.thresholds.median_page_views))}</b> · 최소 조회{" "}
              <b>{num(data.thresholds.min_page_views)}</b>회(미만은 표본 부족) · 판정 대상{" "}
              <b>{num(data.thresholds.eligible_options)}</b>개.
            </div>
            {data.options.length === 0 ? (
              <EmptyState reason="이 기간에 유입이 잡힌 옵션이 없습니다." hint="기간을 넓혀 보세요." />
            ) : (
              <Table
                head={<>
                  <Th>옵션 / 상품</Th>
                  <Th right>방문자</Th>
                  <Th right>조회</Th>
                  <Th right>조회/방문</Th>
                  <Th right>주문</Th>
                  <Th right>구매전환율</Th>
                  <Th right>판매수량</Th>
                  <Th right>주문당 개수</Th>
                  <Th right>소비자 매출</Th>
                  <Th right>평점(리뷰)</Th>
                  <Th>위치</Th>
                </>}
              >
                {data.options.map((o) => <OptionRow key={o.option_id} o={o} />)}
              </Table>
            )}
            <p className="px-4 py-3 text-xs text-gray-500">
              평점이 <b>—</b> 인 옵션은 리뷰가 아직 없는 것입니다(0점이 아닙니다).
              바이박스 승자 필드는 1P 전 옵션이 False라 열로 넣지 않았습니다 — 3P와 의미가 다를
              수 있어, 값이 생기면 그때 넣습니다.
            </p>
          </Card>
        </>
      )}
    </div>
  );
}
