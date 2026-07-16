// NaverAdRawExplorer.tsx — D-NAO-47 3층 ⑨ 원자료 탐색.
// ★수집은 풍부한데 API가 0건이라 볼 방법이 없었다(스펙 §1-4): 키워드 91,005 · 검색어
//   114,285 · 시간당 스냅샷. 여기가 처음으로 그걸 여는 자리.
// ★규모 때문에 서버 페이지네이션이 계약이다(§9 라이브: 489행 무페이징 → 스크롤 27,305px).
import { useEffect, useState } from "react";
import { Card, Table, Th, Td, Pager, Loading, EmptyState, Button, LayerNav } from "../components/ui";
import { num, won, pctFromFraction, NO_DATA } from "../lib/format";
import {
  fetchNaverRawKeywords, fetchNaverRawSearchTerms, fetchNaverRawHourly,
  type NaverRawKeywordRow, type NaverRawSearchTermRow, type NaverRawHourlyRow,
} from "../lib/api";

type Tab = "keywords" | "search-terms" | "hourly";
const PAGE = 50;

export default function NaverAdRawExplorer() {
  const [tab, setTab] = useState<Tab>("keywords");
  return (
    <div className="space-y-4">
      <LayerNav />
      <Card
        title="원자료 탐색"
        right={
          <div className="flex gap-1">
            {(["keywords", "search-terms", "hourly"] as Tab[]).map((t) => (
              <Button key={t} variant={tab === t ? "primary" : "ghost"} onClick={() => setTab(t)}>
                {t === "keywords" ? "등록 키워드" : t === "search-terms" ? "검색어" : "시간당"}
              </Button>
            ))}
          </div>
        }
      >
        {tab === "keywords" && <KeywordsPane />}
        {tab === "search-terms" && <SearchTermsPane />}
        {tab === "hourly" && <HourlyPane />}
      </Card>
    </div>
  );
}

function KeywordsPane() {
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<{ total: number; rows: NaverRawKeywordRow[] } | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    fetchNaverRawKeywords({ q: q || undefined, limit: PAGE, offset })
      .then((r) => { if (alive) setData(r); })
      .catch(() => { if (alive) setData({ total: 0, rows: [] }); });
    return () => { alive = false; };
  }, [q, offset]);

  return (
    <div>
      <div className="px-4 py-2 border-b border-gray-100">
        <input
          className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
          placeholder="키워드 검색"
          value={q}
          onChange={(e) => { setOffset(0); setQ(e.target.value); }}
        />
      </div>
      {data === null ? <Loading rows={5} /> : data.rows.length === 0 ? (
        <EmptyState reason={q ? `"${q}"와 일치하는 키워드가 없습니다.` : "등록된 키워드가 없습니다."} />
      ) : (
        <>
          <Table head={<><Th>키워드</Th><Th>캠페인</Th><Th right>입찰가</Th><Th right>월 검색량</Th><Th>상태</Th></>}>
            {data.rows.map((r) => (
              <tr key={r.entity_id}>
                <Td>{r.name}</Td>
                <Td><span className="text-xs text-gray-500">{r.campaign_id}</span></Td>
                <Td right>{r.bid_amt == null ? NO_DATA : won(r.bid_amt)}</Td>
                <Td right>{r.monthly_volume == null ? NO_DATA : num(r.monthly_volume)}</Td>
                <Td>{r.status}</Td>
              </tr>
            ))}
          </Table>
          <Pager total={data.total} offset={offset} pageSize={PAGE} onOffset={setOffset} />
        </>
      )}
    </div>
  );
}

function SearchTermsPane() {
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<{ total: number; rows: NaverRawSearchTermRow[] } | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    fetchNaverRawSearchTerms({ days: 14, limit: PAGE, offset })
      .then((r) => { if (alive) setData(r); })
      .catch(() => { if (alive) setData({ total: 0, rows: [] }); });
    return () => { alive = false; };
  }, [offset]);

  if (data === null) return <Loading rows={5} />;
  if (data.rows.length === 0) {
    return <EmptyState reason="최근 14일 검색어 데이터가 없습니다." hint="검색어 리포트는 매일 07:40 크론이 수집합니다." />;
  }
  return (
    <>
      <Table head={<><Th>날짜</Th><Th>검색어</Th><Th>소스</Th><Th right>노출</Th><Th right>클릭</Th><Th right>비용</Th></>}>
        {data.rows.map((r, i) => (
          <tr key={`${r.ad_date}-${r.search_term}-${i}`}>
            <Td>{r.ad_date ?? NO_DATA}</Td>
            <Td>{r.search_term}</Td>
            <Td><span className="text-xs text-gray-500">{r.source}</span></Td>
            <Td right>{num(r.imp)}</Td>
            <Td right>{num(r.clk)}</Td>
            <Td right>{won(r.cost)}</Td>
          </tr>
        ))}
      </Table>
      <Pager total={data.total} offset={offset} pageSize={PAGE} onOffset={setOffset} />
    </>
  );
}

function HourlyPane() {
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<{ total: number; rows: NaverRawHourlyRow[] } | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    fetchNaverRawHourly({ days: 3, limit: PAGE, offset })
      .then((r) => { if (alive) setData(r); })
      .catch(() => { if (alive) setData({ total: 0, rows: [] }); });
    return () => { alive = false; };
  }, [offset]);

  if (data === null) return <Loading rows={5} />;
  if (data.rows.length === 0) {
    return <EmptyState reason="최근 3일 시간당 스냅샷이 없습니다." hint="시간당 스냅샷은 매시 수집됩니다." />;
  }
  return (
    <>
      <Table head={<><Th>날짜</Th><Th right>시</Th><Th>캠페인</Th><Th right>비용</Th><Th right>일예산</Th><Th right>소진율</Th></>}>
        {data.rows.map((r, i) => (
          <tr key={`${r.ad_date}-${r.snapshot_hour}-${r.campaign_id}-${i}`}>
            <Td>{r.ad_date ?? NO_DATA}</Td>
            <Td right>{r.snapshot_hour}시</Td>
            <Td><span className="text-xs text-gray-500">{r.campaign_id}</span></Td>
            <Td right>{won(r.cost)}</Td>
            <Td right>{r.daily_budget == null ? NO_DATA : won(r.daily_budget)}</Td>
            {/* ★spend_ratio가 null이면 "0%"가 아니라 "알 수 없음"이다(예산 미설정/0). */}
            <Td right>{r.spend_ratio == null ? NO_DATA : pctFromFraction(r.spend_ratio, 1)}</Td>
          </tr>
        ))}
      </Table>
      <Pager total={data.total} offset={offset} pageSize={PAGE} onOffset={setOffset} />
    </>
  );
}
