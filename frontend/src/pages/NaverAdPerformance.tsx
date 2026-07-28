// NaverAdPerformance.tsx — 광고 성과(사장님 뷰) · D-NAO-104 Phase 1
// (docs/PLAN_naver-ad-performance-view.md §5. 섹션 ①오늘 한눈에 + ②오늘 시스템이 한 일).
//
// ★이 화면의 존재 이유: 성과를 알려면 화면 4개(커맨드 센터·리포트·진단 보드·원자료)를 돌아야
//   했고 전부 운영자 어휘였다. 여기는 **사장님 뷰**다 — 운영자 뷰를 하나 더 만드는 게 아니다.
// ★읽기 전용(계획서 §0-1): 관리주체 스위치·승인 버튼·예산 변경 위젯을 이 페이지에 두지 않는다.
//   조작은 커맨드 센터(/naver-ad)와 최적화 콘솔(/naver-ad/console)이 계속 담당한다.
// ★표기(D-NAO-103): 화면에 ID를 쓰지 않는다(campaign_id는 title 속성에만). 상태·판정은
//   백엔드가 준 **문장 그대로** 렌더한다. null은 NO_DATA("—")이며 절대 0으로 그리지 않는다.
// ★모바일 우선: Jino가 폰으로 본다. 카드 1열(모바일) → 2열(태블릿) → 3열(데스크톱).
import { useState } from "react";
import { Card, Stat, Badge, EmptyState, Loading, LayerNav } from "../components/ui";
import { num, won, roasX, NO_DATA } from "../lib/format";
import { useAsyncData } from "../lib/useAsyncData";
import {
  fetchNaverPerformanceToday,
  type NaverPerformanceCampaignCard,
  type NaverPerformanceActionItem,
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
        오늘 {won(spent)} 사용 · 하루 예산이 정해져 있지 않습니다.
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
        오늘 {won(spent)} / 하루 예산 {won(budget)} ({pct}%)
      </p>
    </div>
  );
}

function CampaignCard({ c }: { c: NaverPerformanceCampaignCard }) {
  const tone = roasTone(c);
  return (
    // ID는 title에만 — 화면 텍스트로는 절대 나가지 않는다(D-NAO-103①).
    <div className="rounded-lg border border-gray-200 bg-white p-4" title={c.campaign_id}>
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-sm font-semibold text-gray-900 break-keep">{c.name}</h3>
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
          <div className="text-xs text-gray-500">오늘 ROAS(추정)</div>
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
        {c.revenue_today_proxy != null && ` · 오늘 매출(추정) ${won(c.revenue_today_proxy)}`}
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

export default function NaverAdPerformance() {
  const { data, error } = useAsyncData(() => fetchNaverPerformanceToday(), []);
  const [showIdle, setShowIdle] = useState(false);

  if (error) {
    return (
      <>
        <LayerNav />
        <EmptyState
          reason={`불러오지 못했습니다: ${error}`}
          hint="새로고침하거나 백엔드 상태를 확인하세요."
        />
      </>
    );
  }
  if (data === null) return <><LayerNav /><Loading label="오늘 광고 성과를 불러오는 중…" rows={6} /></>;

  const active = data.campaigns.filter((c) => c.active_today);
  const idle = data.campaigns.filter((c) => !c.active_today);
  const shown = showIdle ? data.campaigns : active;
  const actions = data.today_actions;

  return (
    <div className="space-y-4">
      <LayerNav />

      {/* ① 오늘 한눈에 */}
      <Card
        title="오늘 광고, 이렇게 돌고 있습니다"
        right={<span className="text-xs text-gray-400 tabular-nums">{data.as_of.slice(0, 16).replace("T", " ")} 기준</span>}
      >
        <div className="p-4 space-y-4">
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
            <Stat label="오늘 쓴 광고비" value={won(data.totals.spend_today)}
              isEmpty={data.totals.spend_today === 0}
              reason="오늘 아직 집행된 광고비가 없습니다."
              tone={data.totals.spend_today === 0 ? "idle" : "neutral"} />
            <Stat label="오늘 돌아간 광고" value={`${num(data.totals.campaigns_active_today)}개`}
              isEmpty={data.totals.campaigns_active_today === 0}
              reason="오늘 집행된 광고가 아직 없습니다."
              tone={data.totals.campaigns_active_today === 0 ? "idle" : "neutral"}
              sub={`전체 ${num(data.totals.campaigns_total)}개 중`} />
            <Stat label="오늘 시스템이 한 일" value={`${num(actions.executed_count)}건`}
              isEmpty={actions.executed_count === 0}
              reason={actions.quiet_reason ?? "오늘 실제로 반영된 변경이 없습니다."}
              tone={actions.executed_count === 0 ? "idle" : "neutral"}
              sub={actions.blocked_count > 0 ? `안전장치가 막은 것 ${num(actions.blocked_count)}건` : undefined} />
          </div>

          {/* ★프록시 경고는 상시 노출(계획서 R1) — 접히면 안 읽힌다. */}
          <p className="text-xs text-gray-500 break-keep border-l-2 border-gray-200 pl-2">
            {data.data_note}
          </p>

          {shown.length === 0 ? (
            <EmptyState
              reason="오늘 집행된 광고가 없습니다."
              hint={idle.length > 0 ? `쉬고 있는 광고 ${idle.length}개는 아래 버튼으로 볼 수 있습니다.` : undefined}
            />
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              {shown.map((c) => <CampaignCard key={c.campaign_id} c={c} />)}
            </div>
          )}

          {idle.length > 0 && (
            <button
              type="button"
              onClick={() => setShowIdle((v) => !v)}
              className="text-xs text-gray-500 underline underline-offset-2"
            >
              {showIdle
                ? "오늘 집행 없는 광고 접기"
                : `오늘 집행 없는 광고 ${idle.length}개도 보기`}
            </button>
          )}
        </div>
      </Card>

      {/* ② 오늘 시스템이 한 일 */}
      <Card
        title="오늘 시스템이 한 일"
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
            reason={actions.quiet_reason ?? "오늘 반영된 변경이 없습니다."}
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
    </div>
  );
}
