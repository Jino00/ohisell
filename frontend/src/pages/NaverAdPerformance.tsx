// NaverAdPerformance.tsx — 광고 성과(사장님 뷰) · D-NAO-104 Phase 1 + D-NAO-105 Phase 2
// (docs/PLAN_naver-ad-performance-view.md §5. 섹션 ①오늘 한눈에 ②오늘 시스템이 한 일
//  ③캠페인 상세 ④예산 + Jino 확장: 광고 선택·날짜 선택·날짜 비교).
//
// ★이 화면의 존재 이유: 성과를 알려면 화면 4개(커맨드 센터·리포트·진단 보드·원자료)를 돌아야
//   했고 전부 운영자 어휘였다. 여기는 **사장님 뷰**다 — 운영자 뷰를 하나 더 만드는 게 아니다.
// ★읽기 전용(계획서 §0-1): 관리주체 스위치·승인 버튼·예산 변경 위젯을 이 페이지에 두지 않는다.
//   조작은 커맨드 센터(/naver-ad)와 최적화 콘솔(/naver-ad/console)이 계속 담당한다.
// ★표기(D-NAO-103): 화면에 ID를 쓰지 않는다(campaign_id는 select value·title 속성에만).
//   상태·판정은 백엔드가 준 **문장 그대로** 렌더한다. null은 NO_DATA("—")이며 절대 0으로
//   그리지 않는다.
// ★출처 라벨(D-NAO-105): 같은 칸에 오늘=실주문 추정치와 과거=네이버 확정치가 번갈아 들어온다.
//   무엇을 보고 있는지는 백엔드가 준 `source_label`/`roas_label`이 말한다 — 여기서 다시
//   판단하지 않는다.
// ★모바일 우선: Jino가 폰으로 본다. 카드 1열(모바일) → 2열(태블릿) → 3열(데스크톱).
import { useState } from "react";
import { Card, Stat, Badge, EmptyState, Loading, LayerNav, Table, Th, Td } from "../components/ui";
import { PerfBudgetCurveChart } from "../components/PerfBudgetCurveChart";
import { PerfCampaignTrendChart } from "../components/PerfCampaignTrendChart";
import { num, won, roasX, pctFromFraction, isoKST, NO_DATA } from "../lib/format";
import { baseDateError, compareDateError, defaultCompareDate } from "../lib/perfDateRules";
import { stripBoldMarkers, marketBidTone } from "../lib/bepBreakdownRules";
import { confidenceLabel, observationBadge, deltaText, partialDataNote } from "../lib/timelineRules";
import { useAsyncData } from "../lib/useAsyncData";
import {
  fetchNaverPerformanceBudget,
  fetchNaverPerformanceCampaign,
  fetchNaverPerformanceCampaignOptions,
  fetchNaverPerformanceCompare,
  fetchNaverPerformanceDay,
  fetchNaverPerformanceBepBreakdown,
  fetchNaverPerformanceTimeline,
  type NaverPerformanceCampaignCard,
  type NaverPerformanceActionItem,
  type NaverPerformanceCompareRow,
  type NaverPerformanceDelta,
  type NaverPerformanceGroup,
  type NaverPerformanceBepRow,
  type NaverPerformanceTimelineDay,
  type NaverPerformanceTimelineEvent,
} from "../lib/api";

/** 성과 색: 목표 이상=좋음 / 손익분기 위=주의 / 손익분기 아래=나쁨 / 모름=회색.
 *  ★모름을 '나쁨'으로 칠하지 않는다 — 모르는 것은 나쁜 것이 아니다(원칙22). */
function roasTone(c: NaverPerformanceCampaignCard): "good" | "warn" | "bad" | "idle" {
  if (c.roas_today_proxy == null) return "idle";
  if (c.target_roas != null && c.roas_today_proxy >= c.target_roas) return "good";
  if (c.bep_roas != null && c.roas_today_proxy >= c.bep_roas) return "warn";
  if (c.bep_roas != null) return "bad";
  return "idle";
}

const ROAS_TONE_TEXT: Record<string, string> = {
  good: "text-judge-good",
  warn: "text-judge-warn",
  bad: "text-judge-bad",
  idle: "text-judge-idle",
};

const ROAS_BAR_TONE: Record<string, string> = {
  good: "bg-judge-good",
  warn: "bg-judge-warn",
  bad: "bg-judge-bad",
  idle: "bg-gray-300",
};

/** 예산 소진 게이지. 일예산이 없으면 막대 자체를 그리지 않는다(0%로 그리면 거짓). */
function BudgetGauge({ spent, budget, ratio }: {
  spent: number; budget: number | null; ratio: number | null;
}) {
  if (budget == null || ratio == null) {
    return (
      <p className="text-xs text-gray-500">
        {won(spent)} 사용 · 하루 예산이 정해져 있지 않습니다.
      </p>
    );
  }
  const pct = Math.min(100, Math.round(ratio * 100));
  const tone = pct >= 100 ? "bg-judge-bad" : pct >= 90 ? "bg-judge-warn" : "bg-owner-ours";
  return (
    <div>
      <div className="flex h-2 w-full overflow-hidden rounded bg-gray-100">
        <div className={tone} style={{ width: `${pct}%` }} />
      </div>
      <p className="mt-1 text-xs text-gray-500 tabular-nums">
        {won(spent)} / 하루 예산 {won(budget)} ({Math.round(ratio * 100)}%)
      </p>
    </div>
  );
}

function CampaignCard({ c, onOpen }: {
  c: NaverPerformanceCampaignCard; onOpen: (id: string) => void;
}) {
  const tone = roasTone(c);
  return (
    // ID는 title에만 — 화면 텍스트로는 절대 나가지 않는다(D-NAO-103①).
    <div className="rounded-lg border border-gray-200 bg-white p-4" title={c.campaign_id}>
      <div className="flex items-start justify-between gap-2">
        <button type="button" onClick={() => onOpen(c.campaign_id)}
          className="text-left text-sm font-semibold text-gray-900 break-keep underline-offset-2 hover:underline">
          {c.name}
        </button>
        <span className="shrink-0"><Badge>{c.type_label}</Badge></span>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-1">
        <Badge tone={c.status_label === "정상 노출 중" ? "owner" : "neutral"}>{c.status_label}</Badge>
        {c.review_label && <Badge>{c.review_label}</Badge>}
        <Badge tone={c.auto_operate ? "owner" : "neutral"}>{c.managed_by_label}</Badge>
      </div>

      <div className="mt-3">
        <BudgetGauge spent={c.spend_today} budget={c.daily_budget} ratio={c.spend_ratio} />
      </div>

      <div className="mt-3 grid grid-cols-3 gap-3">
        <div className="min-w-0">
          {/* 라벨은 백엔드가 준다 — 오늘이면 '(추정)', 과거면 '(확정)'. */}
          <div className="text-xs text-gray-500">{c.roas_label ?? "ROAS"}</div>
          <div className={`mt-0.5 text-lg font-semibold tabular-nums ${ROAS_TONE_TEXT[tone]}`}>
            {/* ★null은 "—". 0.00배로 렌더하면 "성과가 바닥"이라는 거짓 단언이 된다. */}
            {c.roas_today_proxy == null ? NO_DATA : roasX(c.roas_today_proxy)}
          </div>
          <div className="mt-0.5 flex h-1 w-full overflow-hidden rounded bg-gray-100">
            <div className={`${ROAS_BAR_TONE[tone]} w-full`} />
          </div>
        </div>
        <Stat label="노출" value={num(c.imp_today)} />
        <Stat label="클릭" value={num(c.clk_today)} />
      </div>

      <p className="mt-2 text-xs text-gray-500 tabular-nums">
        목표 {c.target_roas == null ? NO_DATA : roasX(c.target_roas)} ·
        {" "}남는 기준 {c.bep_roas == null ? NO_DATA : roasX(c.bep_roas)}
        {c.revenue_today_proxy != null &&
          ` · ${c.revenue_label ?? "매출"} ${won(c.revenue_today_proxy)}`}
      </p>

      <p className="mt-2 text-sm text-gray-700 break-keep">{c.verdict_sentence}</p>

      {c.roas_unknown_reason && (
        <p className="mt-1 text-xs text-gray-400 break-keep">{c.roas_unknown_reason}</p>
      )}
      {c.shared_product_count > 0 && (
        <p className="mt-1 text-xs text-gray-400 break-keep">
          이 광고의 상품 {c.shared_product_count}개는 다른 광고와도 겹쳐서, 매출을 나눠 잡았습니다.
        </p>
      )}
    </div>
  );
}

const ACTION_BADGE: Record<string, { label: string; tone: "owner" | "neutral" }> = {
  executed: { label: "실행됨", tone: "owner" },
  blocked: { label: "막힘", tone: "neutral" },
  unknown: { label: "확인 필요", tone: "neutral" },
};

function ActionRow({ item }: { item: NaverPerformanceActionItem }) {
  const badge = ACTION_BADGE[item.state] ?? ACTION_BADGE.unknown;
  return (
    <li className="flex items-start gap-2 px-4 py-2 border-b border-gray-100 last:border-0">
      <span className="shrink-0 pt-0.5"><Badge tone={badge.tone}>{badge.label}</Badge></span>
      <span className="text-sm text-gray-700 break-keep">{item.sentence}</span>
    </li>
  );
}

/** 증감 한 칸. ★증가=빨강·감소=파랑(Delta와 같은 관례)이되, 여기는 절대값도 함께 보여준다 —
 *  % 만으로는 "3만원이 6만원 됐다"와 "3원이 6원 됐다"가 같아 보인다. */
function DeltaCell({ delta, format }: {
  delta: NaverPerformanceDelta; format: (n: number) => string;
}) {
  if (delta.abs == null) return <span className="text-dir-flat">{NO_DATA}</span>;
  if (delta.abs === 0) return <span className="text-dir-flat tabular-nums">변화 없음</span>;
  const up = delta.abs > 0;
  return (
    <span className={`tabular-nums ${up ? "text-dir-up" : "text-dir-down"}`}>
      {up ? "▲" : "▼"} {format(Math.abs(delta.abs))}
      {delta.pct != null && ` (${pctFromFraction(Math.abs(delta.pct), 1)})`}
    </span>
  );
}

// ── ③ 캠페인 상세 ────────────────────────────────────────────────
const GROUP_TONE: Record<string, "owner" | "neutral"> = {
  // owner(파랑) = **우리가 하고 있는 일**. observed는 우리가 손대지 않는 광고라 중립이다.
  expanding: "owner", watching: "neutral", hold: "neutral", blocked: "neutral",
  observed: "neutral",
};

function GroupBadgeRow({ g }: { g: NaverPerformanceGroup }) {
  return (
    <tr title={g.adgroup_id}>
      <Td>{g.name}</Td>
      <Td><Badge tone={GROUP_TONE[g.state] ?? "neutral"}>{g.state_label}</Badge></Td>
      <Td right>{won(g.cost)}</Td>
      <Td right>{g.roas == null ? NO_DATA : roasX(g.roas)}</Td>
      <Td><span className="text-gray-600 break-keep">{g.reason_sentence}</span></Td>
    </tr>
  );
}

function CampaignDetailSection({ campaignId, days }: { campaignId: string; days: number }) {
  const { data, error } = useAsyncData(
    () => fetchNaverPerformanceCampaign(campaignId, days), [campaignId, days],
  );
  if (error) {
    return (
      <Card title="이 광고 자세히 보기">
        <EmptyState reason={`불러오지 못했습니다: ${error}`} hint="잠시 후 다시 시도하세요." />
      </Card>
    );
  }
  if (data === null) return <Card title="이 광고 자세히 보기"><Loading rows={4} /></Card>;

  return (
    <Card
      title={`${data.name} — 최근 ${data.window.days}일 흐름`}
      right={
        <span className="flex items-center gap-2">
          <Badge tone={data.managed_by_us ? "owner" : "neutral"}>{data.managed_by_label}</Badge>
          <span className="text-xs text-gray-400 tabular-nums">
            {data.window.from} ~ {data.window.to}
          </span>
        </span>
      }
    >
      <div className="p-4 space-y-4">
        {/* 우리가 운영하지 않는 광고임을 먼저 말한다 — 아래 성과를 우리 조치로 읽지 않도록. */}
        {data.managed_note && (
          <p className="text-xs text-judge-warn break-keep border-l-2 border-judge-warn pl-2">
            {data.managed_note}
          </p>
        )}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <Stat label="쓴 광고비" value={won(data.totals.cost)}
            isEmpty={data.totals.cost === 0} reason="이 기간에 집행된 광고비가 없습니다."
            tone={data.totals.cost === 0 ? "idle" : "neutral"} />
          <Stat label="전환매출(확정)" value={won(data.totals.conv_amt)}
            isEmpty={data.totals.conv_amt === 0} reason="이 기간에 잡힌 전환매출이 없습니다."
            tone={data.totals.conv_amt === 0 ? "idle" : "neutral"} />
          <Stat label="ROAS(확정)"
            value={data.totals.roas == null ? NO_DATA : roasX(data.totals.roas)} />
          <Stat label="클릭" value={num(data.totals.clk)}
            isEmpty={data.totals.clk === 0} reason="이 기간에 클릭이 없습니다."
            tone={data.totals.clk === 0 ? "idle" : "neutral"} />
        </div>

        <PerfCampaignTrendChart series={data.series} bepRoas={data.lines.bep_roas}
          targetRoas={data.lines.target_roas} />
        <p className="text-xs text-gray-500 break-keep border-l-2 border-gray-200 pl-2">
          {data.series_note}
        </p>

        <div>
          <h4 className="mb-2 text-sm font-semibold text-gray-900">
            상품 그룹별로 지금 뭘 하고 있나
          </h4>
          {data.groups.length === 0 ? (
            <EmptyState reason="이 광고에는 등록된 상품 그룹이 없습니다." />
          ) : (
            <Table head={
              <>
                <Th>그룹</Th><Th>지금 상태</Th><Th right>쓴 광고비</Th><Th right>ROAS</Th><Th>이유</Th>
              </>
            }>
              {data.groups.map((g) => <GroupBadgeRow key={g.adgroup_id} g={g} />)}
            </Table>
          )}
        </div>
      </div>
    </Card>
  );
}

// ── ④ 예산 ───────────────────────────────────────────────────────
function BudgetSection({ date, campaignId }: { date: string; campaignId: string }) {
  const { data, error } = useAsyncData(
    () => fetchNaverPerformanceBudget({ date, campaignId: campaignId || undefined }),
    [date, campaignId],
  );
  const [showAll, setShowAll] = useState(false);

  if (error) {
    return (
      <Card title="예산, 언제 다 썼나">
        <EmptyState reason={`불러오지 못했습니다: ${error}`} hint="잠시 후 다시 시도하세요." />
      </Card>
    );
  }
  if (data === null) return <Card title="예산, 언제 다 썼나"><Loading rows={4} /></Card>;

  // 기본은 예산을 많이 쓴 순 상위 3개 — 46개를 다 그리면 아무것도 안 읽힌다.
  const shown = showAll ? data.curves : data.curves.slice(0, 3);

  return (
    <Card
      title="예산, 언제 다 썼나"
      right={<span className="text-xs text-gray-400 tabular-nums">{data.date}</span>}
    >
      <div className="p-4 space-y-4">
        {data.curves.length === 0 ? (
          <EmptyState reason="이날은 시간별 관측 기록이 없습니다."
            hint="시간별 기록은 매시간 쌓입니다. 아주 오래된 날짜는 남아있지 않을 수 있습니다." />
        ) : (
          shown.map((c) => (
            <div key={c.campaign_id} title={c.campaign_id}>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h4 className="text-sm font-semibold text-gray-900 break-keep">{c.campaign_name}</h4>
                <span className="text-xs text-gray-500 tabular-nums">
                  {won(c.spend_total)}
                  {c.daily_budget != null && ` / 하루 예산 ${won(c.daily_budget)}`}
                  {c.spend_ratio != null && ` (${Math.round(c.spend_ratio * 100)}%)`}
                </span>
              </div>
              <PerfBudgetCurveChart
                curve={c}
                changeHours={data.budget_changes
                  .filter((b) => b.campaign_id === c.campaign_id && b.hour != null)
                  .map((b) => b.hour as number)}
              />
              {c.blackout_sentence && (
                <p className="mt-1 text-sm text-gray-700 break-keep">{c.blackout_sentence}</p>
              )}
              {/* 멈추긴 했는데 예산 탓이라 단언할 근거가 없는 구간 — 원인을 말하지 않는다. */}
              {c.stall_sentence && (
                <p className="mt-1 text-xs text-gray-500 break-keep">{c.stall_sentence}</p>
              )}
            </div>
          ))
        )}

        {data.curves.length > 3 && (
          <button type="button" onClick={() => setShowAll((v) => !v)}
            className="text-xs text-gray-500 underline underline-offset-2">
            {showAll ? "많이 쓴 광고 3개만 보기" : `나머지 광고 ${data.curves.length - 3}개도 보기`}
          </button>
        )}

        <div>
          <h4 className="mb-1 text-sm font-semibold text-gray-900">이날 예산을 바꾼 기록</h4>
          {data.budget_changes.length === 0 ? (
            // 0건을 숨기지 않는다 — 왜 0인지 말한다(D-47-h).
            <p className="text-sm text-gray-500">{data.budget_changes_empty_reason}</p>
          ) : (
            <ul className="rounded border border-gray-200">
              {data.budget_changes.map((item, i) => (
                <ActionRow key={`${item.at ?? "na"}-${i}`} item={item} />
              ))}
            </ul>
          )}
        </div>

        <p className="text-xs text-gray-500 break-keep border-l-2 border-gray-200 pl-2">
          {data.data_note}
        </p>
      </div>
    </Card>
  );
}

// ── ⑤ BEP 구성 ────────────────────────────────────────────────────
const MARKET_BID_TONE_TEXT: Record<string, string> = {
  good: "text-judge-good",
  bad: "text-judge-bad",
  idle: "text-gray-400",
};

function BepRowView({ r }: { r: NaverPerformanceBepRow }) {
  const tone = marketBidTone(r.market_bid, r.ceiling_bid, r.ceiling_is_borrowed);
  // 상한을 못 세운 행만 사유를 상품명 아래 붙인다(blocked_reason 우선, 없으면 ceiling_basis).
  const noCeilingReason = r.ceiling_bid == null
    ? stripBoldMarkers(r.blocked_reason || r.ceiling_basis || "")
    : "";
  return (
    <tr>
      <Td>
        <div className="font-medium text-gray-900 break-keep">{r.product_name}</div>
        <div className="mt-0.5 text-xs text-gray-400">광고 {r.ad_count}곳</div>
        {noCeilingReason && (
          <div className="mt-0.5 text-xs text-gray-500 break-keep">{noCeilingReason}</div>
        )}
      </Td>
      <Td right>{won(r.selling_price)}</Td>
      <Td right>
        {won(r.commission_won)}
        <div className="text-xs text-gray-400">{pctFromFraction(r.commission_rate)}</div>
      </Td>
      <Td right>{r.cost_price == null ? NO_DATA : won(r.cost_price)}</Td>
      <Td right>
        {won(r.logistics_cost)}
        {r.nbaesong_share != null && (
          <div className="text-xs text-gray-400">
            도착보장 {pctFromFraction(r.nbaesong_share)}
          </div>
        )}
      </Td>
      <Td right>{r.pre_vat_margin == null ? NO_DATA : won(r.pre_vat_margin)}</Td>
      <Td right>{r.contribution_margin == null ? NO_DATA : won(r.contribution_margin)}</Td>
      <Td right>{r.bep_roas == null ? NO_DATA : roasX(r.bep_roas)}</Td>
      <Td right>{r.target_roas == null ? NO_DATA : roasX(r.target_roas)}</Td>
      <Td right>
        {r.ceiling_bid == null ? NO_DATA : (
          <span className="inline-flex items-center gap-1 justify-end">
            {won(r.ceiling_bid)}
            {r.ceiling_is_borrowed && (
              <span
                className="rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-500"
                title="이 상품 자체의 광고 실적이 얇아 계정 평균으로 계산한 값입니다 — 실제보다 후하게(낙관적으로) 나왔을 수 있습니다."
              >
                빌린 값
              </span>
            )}
          </span>
        )}
      </Td>
      <Td right>
        <span className={`tabular-nums ${MARKET_BID_TONE_TEXT[tone]}`}>
          {r.market_bid == null ? NO_DATA : won(r.market_bid)}
        </span>
      </Td>
    </tr>
  );
}

function BepBreakdownSection({ campaignId }: { campaignId: string }) {
  const { data, error } = useAsyncData(
    () => fetchNaverPerformanceBepBreakdown({ campaignId: campaignId || undefined }),
    [campaignId],
  );
  const title = "이 상품, 클릭당 얼마까지 써야 남나";

  if (error) {
    return (
      <Card title={title}>
        <EmptyState reason={`불러오지 못했습니다: ${error}`} hint="잠시 후 다시 시도하세요." />
      </Card>
    );
  }
  if (data === null) return <Card title={title}><Loading rows={4} /></Card>;

  return (
    <Card
      title={title}
      right={<span className="text-xs text-gray-400 tabular-nums">{data.as_of} 기준</span>}
    >
      <div className="p-4 space-y-3">
        <p className="text-xs text-gray-500 break-keep border-l-2 border-gray-200 pl-2">
          {stripBoldMarkers(data.data_note)}
        </p>
        {data.missing_cost_count > 0 && (
          <p className="text-xs text-judge-warn break-keep border-l-2 border-judge-warn pl-2">
            원가가 아직 입력되지 않은 상품 {num(data.missing_cost_count)}개는 상한을 계산하지
            못했습니다.
          </p>
        )}

        {data.rows.length === 0 ? (
          <EmptyState
            reason="광고에 연결된 상품이 없어 상한 근거를 보여줄 수 없습니다."
            hint="상품-광고 연결이 생기면 이 표에 나타납니다."
          />
        ) : (
          <Table head={
            <>
              <Th>상품</Th>
              <Th right>판매가</Th>
              <Th right>수수료</Th>
              <Th right>원가</Th>
              <Th right>물류비</Th>
              <Th right>세전 잔액</Th>
              <Th right>{`공헌이익 (÷${vatDivisorLabel(data.vat_divisor)})`}</Th>
              <Th right>손익분기</Th>
              <Th right>목표</Th>
              <Th right>클릭당 상한</Th>
              <Th right>지금 시장가</Th>
            </>
          }>
            {data.rows.map((r, i) => (
              <BepRowView key={`${r.product_name}-${i}`} r={r} />
            ))}
          </Table>
        )}
      </div>
    </Card>
  );
}

/** 1.1 → "1.1". vat_divisor는 항상 소수 1자리대 값이라 불필요한 꼬리 0을 만들지 않는다. */
function vatDivisorLabel(n: number): string {
  return n.toLocaleString("ko-KR", { maximumFractionDigits: 3 });
}

// ── ⑥ 개선 타임라인 ───────────────────────────────────────────────
// ★정직 규약(계획서 §3-3·원칙22): "개선됐습니다"류 단정 문구를 이 섹션에서 만들지 않는다.
//   sentence/data_note는 백엔드가 준 문장을 그대로 렌더한다. campaign_id·ref_key는 화면에
//   렌더하지 않는다(ref_key는 React key로만 쓴다).
const TIMELINE_WINDOW_OPTIONS = [30, 90, 180] as const;
const TIMELINE_INITIAL_SHOWN = 10;

function TimelineEventRow({ ev }: { ev: NaverPerformanceTimelineEvent }) {
  const confTag = confidenceLabel(ev.effective_confidence);
  return (
    <li className="py-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        {ev.source === "change_log" ? (
          <Badge tone="owner">실제 설정 변경</Badge>
        ) : (
          <Badge>결정</Badge>
        )}
        <span className="text-sm text-gray-900 break-keep">{ev.label_ko}</span>
        {confTag && <span className="text-xs text-gray-400">({confTag})</span>}
      </div>
      {ev.detail_ko && (
        <div className="mt-0.5 line-clamp-2 text-xs text-gray-500 break-keep">
          {ev.detail_ko}
        </div>
      )}
      {ev.campaign_name && (
        <div className="mt-0.5 text-xs text-gray-400">{ev.campaign_name}</div>
      )}
    </li>
  );
}

function TimelineDayCard({ day }: { day: NaverPerformanceTimelineDay }) {
  const impact = day.impact;
  const badge = observationBadge(impact);
  const partialNote = impact ? partialDataNote(impact.pre, impact.post) : null;
  return (
    <div className="rounded-lg border border-gray-200 p-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-gray-900 tabular-nums">{day.date}</span>
        <span className="text-xs text-gray-500">결정 {day.events.length}건</span>
        {impact && impact.same_day_count > 1 && (
          <span className="px-2 py-0.5 text-xs rounded-full bg-gray-100 text-gray-500">
            함께 바꾼 {num(impact.same_day_count)}건
          </span>
        )}
        {badge && (
          <span className="px-2 py-0.5 text-xs rounded-full bg-gray-100 text-judge-warn">
            {badge}
          </span>
        )}
      </div>

      {impact && (
        <div className="space-y-1 text-xs text-gray-600">
          <div>
            광고비 {deltaText(
              impact.pre.cost == null ? NO_DATA : won(impact.pre.cost),
              impact.post.cost == null ? NO_DATA : won(impact.post.cost),
            )}
          </div>
          <div>
            매출 {deltaText(
              impact.pre.conv_amt == null ? NO_DATA : won(impact.pre.conv_amt),
              impact.post.conv_amt == null ? NO_DATA : won(impact.post.conv_amt),
            )}
          </div>
          <div>
            ROAS {deltaText(
              impact.pre.roas == null ? NO_DATA : roasX(impact.pre.roas),
              impact.post.roas == null ? NO_DATA : roasX(impact.post.roas),
            )}
          </div>
          {partialNote && (
            <p className="text-gray-400">{partialNote}</p>
          )}
          <p className="break-keep text-gray-500">{impact.sentence}</p>
          {impact.confounded_count > 0 && (
            <p className="text-gray-400">같은 기간 다른 변경 {num(impact.confounded_count)}건</p>
          )}
        </div>
      )}

      <ul className="divide-y divide-gray-100">
        {day.events.map((ev) => (
          <TimelineEventRow key={ev.ref_key} ev={ev} />
        ))}
      </ul>
    </div>
  );
}

function ImprovementTimelineSection({ campaignId }: { campaignId: string }) {
  const [days, setDays] = useState<number>(90);
  const [showAll, setShowAll] = useState(false);
  const { data, error } = useAsyncData(
    () => fetchNaverPerformanceTimeline({ days, campaignId: campaignId || undefined }),
    [days, campaignId],
  );
  const title = "우리가 바꾼 것들과 그 전후";

  const windowButtons = (
    <div className="flex items-center gap-1">
      {TIMELINE_WINDOW_OPTIONS.map((d) => (
        <button key={d} type="button" onClick={() => setDays(d)}
          className={`px-2 py-1 text-xs rounded border ${
            days === d
              ? "bg-blue-600 text-white border-blue-600"
              : "text-gray-600 border-gray-300 hover:bg-gray-50"
          }`}>
          {d}일
        </button>
      ))}
    </div>
  );

  if (error) {
    return (
      <Card title={title} right={windowButtons}>
        <EmptyState reason={`불러오지 못했습니다: ${error}`} hint="잠시 후 다시 시도하세요." />
      </Card>
    );
  }
  if (data === null) return <Card title={title} right={windowButtons}><Loading rows={4} /></Card>;

  // 최신이 위 — timeline은 오름차순으로 오므로 역순으로 뒤집는다.
  const reversed = [...data.timeline].reverse();
  const shown = showAll ? reversed : reversed.slice(0, TIMELINE_INITIAL_SHOWN);
  const remaining = reversed.length - shown.length;

  return (
    <Card
      title={title}
      right={
        <div className="flex items-center gap-2">
          {windowButtons}
          <span className="text-xs text-gray-400 tabular-nums">
            {data.as_of} 기준 · {data.days}일 창
          </span>
        </div>
      }
    >
      <div className="p-4 space-y-3">
        <p className="text-xs text-gray-500 break-keep border-l-2 border-gray-200 pl-2">
          {stripBoldMarkers(data.data_note)}
        </p>

        <div className="rounded border border-gray-200 bg-gray-50 p-2 text-xs text-gray-600 break-keep">
          {data.retro.sentence}
        </div>

        {!data.catalog_available && (
          <p className="text-xs text-judge-warn break-keep border-l-2 border-judge-warn pl-2">
            결정 목록 파일을 아직 읽지 못해, 실제 설정 변경 기록만 표시합니다.
          </p>
        )}

        {reversed.length === 0 ? (
          <EmptyState
            reason="이 창에는 표시할 변경이 없습니다."
            hint="창 길이를 늘리면 더 이전 변경을 볼 수 있습니다."
          />
        ) : (
          <div className="space-y-2">
            {shown.map((day) => (
              <TimelineDayCard key={day.date} day={day} />
            ))}
          </div>
        )}

        {remaining > 0 && (
          <button
            type="button"
            onClick={() => setShowAll(true)}
            className="text-xs text-gray-500 underline underline-offset-2"
          >
            {`더 보기 (${remaining}일 더)`}
          </button>
        )}

        {data.undated_catalog_count > 0 && (
          <p className="text-xs text-gray-400 break-keep">
            날짜를 알 수 없어 타임라인에 올리지 못한 결정이 {num(data.undated_catalog_count)}개
            있습니다.
          </p>
        )}
      </div>
    </Card>
  );
}

// ── 날짜 비교 ─────────────────────────────────────────────────────
const ROAS_FMT = (n: number) => `${n.toFixed(2)}배`;

function CompareRow({ r }: { r: NaverPerformanceCompareRow }) {
  return (
    <tr title={r.campaign_id}>
      <Td>{r.name}</Td>
      <Td right><DeltaCell delta={r.deltas.spend} format={won} /></Td>
      <Td right><DeltaCell delta={r.deltas.imp} format={num} /></Td>
      <Td right><DeltaCell delta={r.deltas.clk} format={num} /></Td>
      <Td right><DeltaCell delta={r.deltas.revenue} format={won} /></Td>
      <Td right><DeltaCell delta={r.deltas.roas} format={ROAS_FMT} /></Td>
      <Td right>
        <span className="text-xs text-gray-500 tabular-nums">
          {won(r.against.spend)} → {won(r.base.spend)}
        </span>
      </Td>
    </tr>
  );
}

function CompareSection({ base, against, campaignId }: {
  base: string; against: string; campaignId: string;
}) {
  const { data, error } = useAsyncData(
    () => fetchNaverPerformanceCompare(base, against, campaignId || undefined),
    [base, against, campaignId],
  );
  if (error) {
    return (
      <Card title="두 날짜 비교">
        <EmptyState reason={`불러오지 못했습니다: ${error}`} hint="잠시 후 다시 시도하세요." />
      </Card>
    );
  }
  if (data === null) return <Card title="두 날짜 비교"><Loading rows={4} /></Card>;

  const b = data.base.totals;
  const a = data.against.totals;
  return (
    <Card
      title={`${data.base.date} vs ${data.against.date}`}
      right={
        <span className="text-xs text-gray-400">
          {data.base.source_label} · {data.against.source_label}
        </span>
      }
    >
      <div className="p-4 space-y-4">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <div>
            <div className="text-xs text-gray-500">광고비</div>
            <div className="mt-0.5 text-lg font-semibold tabular-nums text-gray-900">{won(b.spend)}</div>
            <div className="mt-0.5 text-xs"><DeltaCell delta={data.deltas.spend} format={won} /></div>
          </div>
          <div>
            <div className="text-xs text-gray-500">매출</div>
            <div className="mt-0.5 text-lg font-semibold tabular-nums text-gray-900">
              {b.revenue == null ? NO_DATA : won(b.revenue)}
            </div>
            <div className="mt-0.5 text-xs"><DeltaCell delta={data.deltas.revenue} format={won} /></div>
          </div>
          <div>
            <div className="text-xs text-gray-500">ROAS</div>
            <div className="mt-0.5 text-lg font-semibold tabular-nums text-gray-900">
              {b.roas == null ? NO_DATA : roasX(b.roas)}
            </div>
            <div className="mt-0.5 text-xs">
              <DeltaCell delta={data.deltas.roas} format={ROAS_FMT} />
            </div>
          </div>
          <div>
            <div className="text-xs text-gray-500">클릭</div>
            <div className="mt-0.5 text-lg font-semibold tabular-nums text-gray-900">{num(b.clk)}</div>
            <div className="mt-0.5 text-xs"><DeltaCell delta={data.deltas.clk} format={num} /></div>
          </div>
        </div>

        {/* ★모름을 0으로 더하지 않았다는 사실을 화면이 말한다(합계가 조용히 과소가 되는 자리). */}
        {(b.revenue_unknown_campaigns > 0 || a.revenue_unknown_campaigns > 0) && (
          <p className="text-xs text-gray-500 break-keep">
            매출을 알 수 없는 광고가 있어 합계에서 빠졌습니다
            {` (${data.base.date} ${b.revenue_unknown_campaigns}개 · ${data.against.date} ${a.revenue_unknown_campaigns}개)`}.
          </p>
        )}
        {data.mixed_source_note && (
          <p className="text-xs text-judge-warn break-keep border-l-2 border-judge-warn pl-2">
            {data.mixed_source_note}
          </p>
        )}
        {data.settling_note && (
          <p className="text-xs text-gray-500 break-keep border-l-2 border-gray-200 pl-2">
            {data.settling_note}
          </p>
        )}

        {data.rows.length === 0 ? (
          <EmptyState reason={data.empty_reason ?? "비교할 광고가 없습니다."} />
        ) : (
          <Table head={
            <>
              <Th>광고</Th>
              <Th right>광고비</Th>
              <Th right>노출</Th>
              <Th right>클릭</Th>
              <Th right>매출</Th>
              <Th right>ROAS</Th>
              <Th right>광고비 원값</Th>
            </>
          }>
            {data.rows.map((r) => <CompareRow key={r.campaign_id} r={r} />)}
          </Table>
        )}
      </div>
    </Card>
  );
}

// ── 페이지 ────────────────────────────────────────────────────────
const TREND_DAY_OPTIONS = [7, 14, 30, 90] as const;

export default function NaverAdPerformance() {
  const today = isoKST(new Date());
  const [campaignId, setCampaignId] = useState("");
  const [date, setDate] = useState(today);
  const [compareOn, setCompareOn] = useState(false);
  const [compareDate, setCompareDate] = useState(() => defaultCompareDate(today));
  const [trendDays, setTrendDays] = useState<number>(30);
  const [showIdle, setShowIdle] = useState(false);

  const options = useAsyncData(() => fetchNaverPerformanceCampaignOptions(), []);
  const dateError = baseDateError(date, today);
  const compareError = compareOn ? compareDateError(date, compareDate, today) : null;
  // ★날짜가 잘못됐으면 **요청 자체를 보내지 않는다.** 빈 날짜를 보내면 백엔드가 오늘로
  //   해석해, 사용자가 지운 날짜 자리에 오늘 숫자가 그대로 남는다(조용한 거짓말).
  const { data, error } = useAsyncData(
    () => dateError
      ? Promise.reject(new Error(dateError))
      : fetchNaverPerformanceDay({ date, campaignId: campaignId || undefined }),
    [date, campaignId],
  );

  const controls = (
    <div className="flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 bg-white p-3">
      <label className="flex flex-col gap-1">
        <span className="text-xs text-gray-500">어떤 광고를 볼까요</span>
        {/* value에만 ID가 들어간다 — 사람이 읽는 텍스트는 이름뿐이다(D-NAO-103①). */}
        <select value={campaignId} onChange={(e) => setCampaignId(e.target.value)}
          className="min-w-56 rounded border border-gray-300 px-2 py-1 text-sm">
          <option value="">전체 광고</option>
          {(options.data?.campaigns ?? []).map((c) => (
            <option key={c.campaign_id} value={c.campaign_id}>
              {c.name} · {c.type_label}
            </option>
          ))}
        </select>
      </label>

      <label className="flex flex-col gap-1">
        <span className="text-xs text-gray-500">날짜</span>
        <input type="date" value={date} max={today} onChange={(e) => setDate(e.target.value)}
          className="rounded border border-gray-300 px-2 py-1 text-sm" />
      </label>

      <label className="flex items-center gap-1.5 pb-1.5 text-sm text-gray-700">
        <input type="checkbox" checked={compareOn} onChange={(e) => setCompareOn(e.target.checked)} />
        다른 날과 비교
      </label>

      {compareOn && (
        <label className="flex flex-col gap-1">
          <span className="text-xs text-gray-500">비교할 날짜</span>
          <input type="date" value={compareDate} max={today}
            onChange={(e) => setCompareDate(e.target.value)}
            className="rounded border border-gray-300 px-2 py-1 text-sm" />
        </label>
      )}

      {options.error && (
        <span className="pb-1.5 text-xs text-gray-500">
          광고 목록을 불러오지 못했습니다 — 전체 기준으로 보고 있습니다.
        </span>
      )}
    </div>
  );

  if (error) {
    return (
      <div className="space-y-4">
        <LayerNav />
        {controls}
        <EmptyState
          reason={dateError ?? `불러오지 못했습니다: ${error}`}
          hint={dateError ? "날짜를 고르면 그날 성과가 나옵니다." : "새로고침하거나 백엔드 상태를 확인하세요."}
        />
      </div>
    );
  }
  if (data === null) {
    return (
      <div className="space-y-4">
        <LayerNav />
        {controls}
        <Loading label="광고 성과를 불러오는 중…" rows={6} />
      </div>
    );
  }

  const active = data.campaigns.filter((c) => c.active_today);
  const idle = data.campaigns.filter((c) => !c.active_today);
  const shown = showIdle ? data.campaigns : active;
  const actions = data.today_actions;
  const dayWord = data.is_today ? "오늘" : data.date;

  return (
    <div className="space-y-4">
      <LayerNav />
      {controls}

      {/* ① 그날 한눈에 */}
      <Card
        title={data.is_today ? "오늘 광고, 이렇게 돌고 있습니다" : `${data.date} 광고는 이랬습니다`}
        right={
          <span className="text-xs text-gray-400 tabular-nums">
            {data.source_label} · {data.as_of.slice(0, 16).replace("T", " ")} 기준
          </span>
        }
      >
        <div className="p-4 space-y-4">
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
            <Stat label={`${dayWord} 쓴 광고비`} value={won(data.totals.spend_today)}
              isEmpty={data.totals.spend_today === 0}
              reason="집행된 광고비가 없습니다."
              tone={data.totals.spend_today === 0 ? "idle" : "neutral"} />
            <Stat label="돌아간 광고" value={`${num(data.totals.campaigns_active_today)}개`}
              isEmpty={data.totals.campaigns_active_today === 0}
              reason="집행된 광고가 없습니다."
              tone={data.totals.campaigns_active_today === 0 ? "idle" : "neutral"}
              sub={`전체 ${num(data.totals.campaigns_total)}개 중`} />
            <Stat label="시스템이 한 일" value={`${num(actions.executed_count)}건`}
              isEmpty={actions.executed_count === 0}
              reason={actions.quiet_reason ?? "실제로 반영된 변경이 없습니다."}
              tone={actions.executed_count === 0 ? "idle" : "neutral"}
              sub={actions.blocked_count > 0 ? `안전장치가 막은 것 ${num(actions.blocked_count)}건` : undefined} />
          </div>

          {/* ★출처 경고는 상시 노출(계획서 R1) — 접히면 안 읽힌다. */}
          <p className="text-xs text-gray-500 break-keep border-l-2 border-gray-200 pl-2">
            {data.data_note}
          </p>
          {/* "0원 집행"과 "수집 안 됨"을 화면이 구분해 말한다(원칙22). */}
          {data.data_gap_note && (
            <p className="text-xs text-judge-warn break-keep border-l-2 border-judge-warn pl-2">
              {data.data_gap_note}
            </p>
          )}

          {shown.length === 0 ? (
            <EmptyState
              reason="집행된 광고가 없습니다."
              hint={idle.length > 0 ? `쉬고 있는 광고 ${idle.length}개는 아래 버튼으로 볼 수 있습니다.` : undefined}
            />
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              {shown.map((c) => (
                <CampaignCard key={c.campaign_id} c={c} onOpen={setCampaignId} />
              ))}
            </div>
          )}

          {idle.length > 0 && (
            <button
              type="button"
              onClick={() => setShowIdle((v) => !v)}
              className="text-xs text-gray-500 underline underline-offset-2"
            >
              {showIdle
                ? "집행 없는 광고 접기"
                : `집행 없는 광고 ${idle.length}개도 보기`}
            </button>
          )}
        </div>
      </Card>

      {/* 날짜 비교 */}
      {compareOn && (
        compareError
          ? <Card title="두 날짜 비교"><EmptyState reason={compareError} /></Card>
          : <CompareSection base={date} against={compareDate} campaignId={campaignId} />
      )}

      {/* ② 그날 시스템이 한 일 */}
      <Card
        title={data.is_today ? "오늘 시스템이 한 일" : `${data.date}에 시스템이 한 일`}
        right={
          <span className="text-xs text-gray-400 tabular-nums">
            실행 {num(actions.executed_count)} · 막힘 {num(actions.blocked_count)}
            {actions.unknown_count > 0 && ` · 확인 필요 ${num(actions.unknown_count)}`}
          </span>
        }
      >
        {actions.items.length === 0 ? (
          // 0건을 숨기지 않는다 — 왜 0인지 말한다(D-47-h).
          <EmptyState
            reason={actions.quiet_reason ?? "반영된 변경이 없습니다."}
            hint="사람이 승인해야 하는 제안은 최적화 콘솔에서 확인할 수 있습니다."
          />
        ) : (
          <ul>
            {actions.items.map((item, i) => (
              <ActionRow key={`${item.at ?? "na"}-${i}`} item={item} />
            ))}
          </ul>
        )}
      </Card>

      {/* ③ 캠페인 상세 — 광고를 골랐을 때만 연다(전체 46개 추이는 질문이 아니다). */}
      {campaignId ? (
        <>
          <div className="flex items-center gap-1">
            <span className="text-xs text-gray-500">추이 기간</span>
            {TREND_DAY_OPTIONS.map((d) => (
              <button key={d} type="button" onClick={() => setTrendDays(d)}
                className={`px-2 py-1 text-xs rounded border ${
                  trendDays === d
                    ? "bg-blue-600 text-white border-blue-600"
                    : "text-gray-600 border-gray-300 hover:bg-gray-50"
                }`}>
                {d}일
              </button>
            ))}
          </div>
          <CampaignDetailSection campaignId={campaignId} days={trendDays} />
        </>
      ) : (
        <Card title="이 광고 자세히 보기">
          <EmptyState
            reason="아직 광고를 고르지 않았습니다."
            hint="위에서 광고를 고르거나 카드의 광고 이름을 누르면 일별 흐름과 그룹별 상태가 나옵니다."
          />
        </Card>
      )}

      {/* ④ 예산 */}
      <BudgetSection date={date} campaignId={campaignId} />

      {/* ⑤ BEP 구성 */}
      <BepBreakdownSection campaignId={campaignId} />

      {/* ⑥ 개선 타임라인 */}
      <ImprovementTimelineSection campaignId={campaignId} />
    </div>
  );
}
