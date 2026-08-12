// NaverAdExclusionList.tsx — 검색어 제외 «후보 리스트» (D-NAO-173 P1, docs/PLAN_search-term-exclusion-list.md)
//
// 이 화면이 답하는 두 가지, 그 이상은 아니다:
//   ①조치 생존 — 우리가 네이버 콘솔에 건 제외가 아직 걸려 있는가(exclusion-survival)
//   ②제외 후보 — 30일 연속 ROAS × 상품 BEP로 «자를 근거가 있는» 검색어(exclusion-list)
// 시스템은 후보만 만든다 — 실행은 사람이 콘솔에서 한다(PLAN §3 금지선). 그래서 여기엔
// 실행 버튼이 없고, «왜 이걸 자르라는가»와 «어떻게 되돌리나»가 전부 보여야 한다.
//
// ★문구는 새로 짓지 않는다 — reason/why/impact/revert_howto는 백엔드 문장을 그대로 렌더한다.
//   문구 정본이 두 벌이 되면 갈라진다(백엔드 SA docstring 참조).
import { useEffect, useState } from "react";
import { Card, Table, Th, Td, Badge, Button, LayerNav, Loading, EmptyState } from "../components/ui";
import { useAsyncData } from "../lib/useAsyncData";
import { num, won, roasX, NO_DATA } from "../lib/format";
import {
  getSearchTermExclusionList, getSearchTermExclusionSurvival,
  getSearchTermExclusionScorecard, postSearchTermExecution, postSearchTermExecutionDetect,
  type NaverExclusionListResponse, type NaverExclusionCandidate, type NaverExclusionSurvival,
  type NaverSearchTermScorecard, type NaverExclusionVerdict, type NaverSearchTermExecutionResultKind,
  type NaverSearchTermDetectResult,
} from "../lib/api";

/** 「라이브에서 자동 발견」 실행 결과 한 줄.
 *
 * ★«못 본 몫»을 항상 찍는다(D-NAO-174 후속 P1). 이 버튼의 존재 이유는 «보고에 없던 제외를
 *   찾는 것»인데, 아무것도 대조하지 못한 실행과 「찾을 게 없었다」가 같은 문장으로 보이면
 *   버튼이 거짓말을 한다. 백엔드가 통(unverifiable/type_unknown/unattributable)을 나눠 내는데
 *   화면이 안 읽으면 그 구분은 없는 것과 같다 — 어제 P1으로 처분된 것과 같은 모양이다.
 */
export function detectResultText(r: NaverSearchTermDetectResult): string {
  const shopping = r.unverifiable_groups ?? 0;
  const unknownType = r.type_unknown_groups ?? 0;
  const uncompared = shopping + unknownType;
  const held = r.unattributable_count ?? 0;

  const head = uncompared > 0 && uncompared >= r.scanned_groups
    ? `⚠️ ${num(r.scanned_groups)}개 그룹 중 대조된 그룹이 없다 — 「등록 0건」은 «없다»가 아니라 «못 봤다»다`
    : `${num(r.scanned_groups)}개 그룹 조회 · 새로 등록 ${num(r.recorded.length)}건`;

  const parts = [head];
  parts.push(`대조 못 함 ${num(uncompared)}개(쇼핑 ${num(shopping)} · 유형 미상 ${num(unknownType)})`);
  parts.push(`빈 응답 ${num(r.groups_with_zero)}개(실패 ${num(r.errors.length)}건)`);
  if (held > 0) parts.push(`⚠️ 캠페인을 못 붙여 기록 보류 ${num(held)}건`);
  return parts.join(" · ");
}


const DAYS_OPTIONS = [14, 30, 60];
const ROUND_CAP_OPTIONS = [20, 50, 100];

const LIVE_STATE_LABEL: Record<string, string> = {
  alive: "걸려 있음",
  missing: "사라짐",
  deleted: "삭제됨(delFlag)",
  unknown: "확인 실패",
};

type BucketKey = keyof NaverExclusionListResponse["buckets"];

// 버킷 라벨 — «후보에서 빠진 것도 전부 세어 보여준다»는 이 화면의 계약이다(백엔드 docstring).
// 이 라벨 자체는 새 문구가 아니라 버킷 키의 한글 이름표일 뿐이고, 근거 문장(why)은 백엔드 것을 쓴다.
const BUCKET_LABEL: Record<BucketKey, string> = {
  already_excluded: "이미 제외됨(우리 장부에 있음)",
  insufficient_sample: "표본 부족",
  bep_unknown: "BEP 미확인",
  powerlink_undecidable: "파워링크 판정 불가",
  profitable: "BEP 이상(수익성 있음)",
  capped_out: "라운드 상한 초과",
  maturity_excluded: "성숙도 미달 제외",
};

const BUCKET_ORDER: BucketKey[] = [
  "already_excluded", "insufficient_sample", "bep_unknown", "powerlink_undecidable",
  "profitable", "capped_out", "maturity_excluded",
];

/** 그릴 버킷 키 = **응답에 있는 키 전부**(알려진 것 먼저, 모르는 것은 뒤에).
 *
 * ★왜 응답 기준인가(D-NAO-176 적대 리뷰 P1): 종전엔 `BUCKET_ORDER` 하드코딩 배열만 돌았고
 *   타입이 고정 키 Record라 **백엔드가 버킷을 늘려도 TS가 침묵**했다. 그 침묵으로 같은 결함이
 *   사흘 새 세 번 났다(`unverifiable` · `type_unknown_groups` · `already_excluded`).
 *   카드 제목이 「후보에서 빠진 것도 **전부** 세어 보여준다」인데 화면이 «전부»를 모르면
 *   그 약속은 매번 조용히 깨진다. 라벨이 없는 새 키는 키 이름 그대로라도 그린다 —
 *   못생긴 줄 하나가 안 보이는 손실보다 낫다.
 */
export function bucketKeysToRender(buckets: Record<string, unknown>): string[] {
  const known = BUCKET_ORDER.filter((k) => k in buckets);
  const unknown = Object.keys(buckets).filter((k) => !(BUCKET_ORDER as string[]).includes(k));
  return [...known, ...unknown.sort()];
}

export default function NaverAdExclusionList() {
  const [days, setDays] = useState(30);
  const [roundCap, setRoundCap] = useState(50);
  const [campaignId, setCampaignId] = useState("");
  const [campaignOptions, setCampaignOptions] = useState<{ id: string; name: string }[]>([]);
  // 실행 기록·라이브 자동 발견 성공 후 성적표·후보 목록을 재조회하기 위한 트리거
  // (docs/PLAN §4-a P2 — 화면이 최신이 되게 한다).
  const [refreshTick, setRefreshTick] = useState(0);
  const refetch = () => setRefreshTick((t) => t + 1);

  const { data, error } = useAsyncData(
    () => getSearchTermExclusionList({ days, campaignId: campaignId || undefined, roundCap }),
    [days, campaignId, roundCap, refreshTick],
  );
  const { data: survival, error: survivalError } = useAsyncData(
    () => getSearchTermExclusionSurvival(),
    [refreshTick],
  );
  const { data: scorecard, error: scorecardError } = useAsyncData(
    () => getSearchTermExclusionScorecard(),
    [refreshTick],
  );

  // 캠페인 필터 옵션 — 별도 목록 API가 없어 「전체」 조회 응답의 candidates에서 뽑는다.
  //   특정 캠페인으로 필터를 걸어놓은 동안엔 목록이 그 하나로 줄어들지 않도록,
  //   「전체」(campaignId="") 조회일 때만 옵션을 갱신한다.
  useEffect(() => {
    if (campaignId || !data) return;
    const map = new Map<string, string>();
    for (const c of data.candidates) {
      if (c.campaign_id) map.set(c.campaign_id, c.campaign_name ?? c.campaign_id);
    }
    setCampaignOptions([...map.entries()].map(([id, name]) => ({ id, name })));
  }, [data, campaignId]);

  return (
    <div className="space-y-4">
      <LayerNav />

      {/* 상단 컨트롤 */}
      <Card>
        <div className="flex flex-wrap items-center gap-4 p-4">
          <label className="text-xs text-gray-500 flex items-center gap-2">
            창(일)
            <select
              className="text-sm border border-gray-300 rounded px-2 py-1"
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
            >
              {DAYS_OPTIONS.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </label>
          <label className="text-xs text-gray-500 flex items-center gap-2">
            라운드 상한
            <select
              className="text-sm border border-gray-300 rounded px-2 py-1"
              value={roundCap}
              onChange={(e) => setRoundCap(Number(e.target.value))}
            >
              {ROUND_CAP_OPTIONS.map((n) => <option key={n} value={n}>{n}건</option>)}
            </select>
          </label>
          <label className="text-xs text-gray-500 flex items-center gap-2">
            캠페인
            <select
              className="text-sm border border-gray-300 rounded px-2 py-1"
              value={campaignId}
              onChange={(e) => setCampaignId(e.target.value)}
            >
              <option value="">전체</option>
              {campaignOptions.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
        </div>
      </Card>

      {/* ① 조치 생존 카드 */}
      <Card title="조치 생존 — 우리가 건 제외가 아직 걸려 있는가">
        {survivalError ? (
          <EmptyState
            reason={`불러오지 못했습니다: ${survivalError}`}
            hint="새로고침하거나 백엔드 상태를 확인하세요."
          />
        ) : survival === null ? (
          <Loading rows={2} />
        ) : (
          <SurvivalPanel survival={survival} />
        )}
      </Card>

      {/* ①-b 성적표 — 실행된 제외가 실제로 효과가 있었나(후보 표보다 위, PLAN §4-a P2-②) */}
      <ScorecardSection
        scorecard={scorecard}
        error={scorecardError}
        campaignId={campaignId}
        onChanged={refetch}
      />

      {error ? (
        <EmptyState reason={`불러오지 못했습니다: ${error}`} hint="새로고침하거나 백엔드 상태를 확인하세요." />
      ) : data === null ? (
        <Loading rows={6} />
      ) : (
        <>
          {/* ② 입력 신선도 + 창 표시 */}
          <FreshnessPanel data={data} />

          {/* ③ 버킷 요약 줄 */}
          <BucketSummary data={data} />

          {/* ④ 후보 표 */}
          <CandidateTable
            candidates={data.candidates}
            candidateCost={data.candidate_cost}
            onRecorded={refetch}
          />
        </>
      )}
    </div>
  );
}

function SurvivalPanel({ survival }: { survival: NaverExclusionSurvival }) {
  const lastChecked = survival.last_checked_at
    ? survival.last_checked_at.slice(0, 16).replace("T", " ")
    : NO_DATA;

  // ★«못 보는 몫»을 초록 문장 안에 함께 적는다(적대 리뷰 P1-3). 백엔드는 쇼핑 제외를
  //   unverifiable로 갈라 배너를 안 켜지만(어긋남이 아니므로 옳다), 화면이 alive만 세면
  //   감시 2건 중 1건이 미지인데 「모두 걸려 있음」이 뜬다 — 되돌림을 못 보는 상태가
  //   «확인됨»으로 읽힌다. 대행사가 우리 조치를 되돌린 전례가 2회 있고 1회는 로그에 흔적이
  //   없었으므로, 이 착시는 그대로 감시 실패다.
  const unverifiable = survival.unverifiable ?? 0;

  if (survival.healthy) {
    return (
      <div className="px-4 py-3">
        <p className="text-sm text-emerald-700">
          {unverifiable > 0
            ? `대조 가능한 제외 ${num(survival.alive)}건 걸려 있음 · 대조 불가 ${num(unverifiable)}건 (감시 대상 ${num(survival.monitored)}건)`
            : `우리가 건 제외 ${num(survival.alive)}건 모두 걸려 있음`}
          {` · 마지막 대조 ${lastChecked}`}
        </p>
        {unverifiable > 0 && survival.unverifiable_note && (
          <p className="pt-1 text-xs text-gray-500">{survival.unverifiable_note}</p>
        )}
      </div>
    );
  }

  return (
    <div>
      <p className="px-4 py-3 text-sm text-red-700">
        감시 대상 {num(survival.monitored)}건 중 어긋남 {num(survival.breached.length)}건
        {survival.stale && " · 대조 자체가 멈춤"}
        {survival.never_checked > 0 && ` · 한 번도 대조 안 됨 ${num(survival.never_checked)}건`}
        {unverifiable > 0 && ` · 대조 불가 ${num(unverifiable)}건`}
      </p>
      <p className="px-4 pb-1 text-xs text-gray-500">{survival.impact}</p>
      <p className="px-4 pb-3 text-xs text-gray-500">{survival.revert_howto}</p>
      <p className="px-4 pb-2 text-xs text-gray-400">마지막 대조 {lastChecked}</p>
      {survival.breached.length > 0 && (
        <Table
          head={
            <>
              <Th>검색어</Th><Th>캠페인/그룹</Th><Th>상태</Th><Th>사유</Th><Th right>제외 시점 광고비</Th>
            </>
          }
        >
          {survival.breached.map((b, i) => (
            <tr key={`${b.adgroup_id ?? ""}/${b.search_term ?? ""}#${i}`}>
              <Td>{b.search_term ?? NO_DATA}</Td>
              <Td><span className="text-gray-500">{b.campaign_id ?? NO_DATA} / {b.adgroup_id ?? NO_DATA}</span></Td>
              <Td>
                <Badge tone="alert">
                  {b.live_state ? (LIVE_STATE_LABEL[b.live_state] ?? b.live_state) : "확인 실패"}
                </Badge>
              </Td>
              <Td><span className="text-xs text-gray-500">{b.live_note ?? NO_DATA}</span></Td>
              <Td right>{won(b.cost_at_exclusion)}</Td>
            </tr>
          ))}
        </Table>
      )}
    </div>
  );
}

const VERDICT_ORDER: NaverExclusionVerdict[] = ["stopped", "still_spending", "pending", "no_baseline"];

const VERDICT_LABEL: Record<NaverExclusionVerdict, string> = {
  stopped: "멈췄음",
  still_spending: "아직 쓰고 있음",
  pending: "판정 대기",
  no_baseline: "기준 없음",
};

// still_spending은 「제외가 반영되지 않았을 수 있음」의 핵심 신호라 붉게, stopped는 초록.
const VERDICT_BADGE_TONE: Record<NaverExclusionVerdict, "good" | "bad" | "neutral"> = {
  stopped: "good",
  still_spending: "bad",
  pending: "neutral",
  no_baseline: "neutral",
};

/** 소수 일평균 값 — 화면 표시 직전에만 반올림한다(집계·회수액 계산엔 원값을 그대로 쓴다). */
function wonPerDay(n: number | null | undefined): string {
  return n == null ? NO_DATA : won(Math.round(n));
}

function ScorecardSection({
  scorecard, error, campaignId, onChanged,
}: {
  scorecard: NaverSearchTermScorecard | null;
  error: string | null;
  campaignId: string;
  onChanged: () => void;
}) {
  const [detecting, setDetecting] = useState(false);
  const [detectMsg, setDetectMsg] = useState<string | null>(null);
  const [detectErr, setDetectErr] = useState<string | null>(null);

  async function handleDetect() {
    setDetecting(true);
    setDetectErr(null);
    setDetectMsg(null);
    try {
      const r = await postSearchTermExecutionDetect(campaignId || undefined);
      setDetectMsg(detectResultText(r));
      onChanged();
    } catch (e) {
      setDetectErr(e instanceof Error ? e.message : String(e));
    } finally {
      setDetecting(false);
    }
  }

  return (
    <Card
      title="성적표 — 실행된 제외가 실제로 효과가 있었나"
      right={
        <Button variant="secondary" onClick={handleDetect} disabled={detecting}>
          {detecting ? "조회 중…" : "라이브에서 자동 발견"}
        </Button>
      }
    >
      {(detectMsg || detectErr) && (
        <p className={`px-4 pt-3 text-xs ${detectErr ? "text-red-700" : "text-gray-500"}`}>
          {detectErr ? `불러오지 못했습니다: ${detectErr}` : detectMsg}
        </p>
      )}
      {error ? (
        <EmptyState reason={`불러오지 못했습니다: ${error}`} hint="새로고침하거나 백엔드 상태를 확인하세요." />
      ) : scorecard === null ? (
        <Loading rows={3} />
      ) : scorecard.total === 0 ? (
        <EmptyState
          reason={
            // ★«판정할 것이 없다»와 «아는 제외가 없다»는 다르다(D-NAO-176). 편입분만 있는
            //   구성에서 「제외가 없습니다」는 거짓말이고, 43건이 화면에서 통째로 증발한다.
            (scorecard.imported_unjudgeable_count ?? 0) > 0
              // ★문구는 백엔드 note와 한 몸이다(D-NAO-177 P1-1). 「실행 시점을 몰라」는
              //   콘솔에 등록시각이 있다는 사실이 확인된 뒤로 거짓이 됐다 — 여기 사본만
              //   남겨 두면 백엔드를 고쳐도 화면은 계속 틀린 말을 한다.
              ? `판정할 제외가 없습니다 — 장부의 ${num(scorecard.imported_unjudgeable_count ?? 0)}건은`
                + " 콘솔에서 편입한 것이라 사후 대조에 쓸 사전 데이터가 없어 성적표가 판정하지 않습니다"
                + "(조치 생존 감시에는 포함됩니다)"
              : "아직 실행된 제외가 없습니다 — 아래 후보에서 콘솔 실행 후 기록하세요"
          }
        />
      ) : (
        <>
          <p className="px-4 py-2 text-xs text-gray-500 border-b border-gray-100">
            총 {num(scorecard.total)}건 · 판정됨 {num(scorecard.judged_count)}건 ·
            판정 대기 {num(scorecard.pending_count)}건 ·
            회수한 총이익(판정분) {won(scorecard.profit_recovered_judged)} ·
            {/* ★합계가 «못 잰 건»을 0원으로 세므로 그 건수를 같이 적는다(적대 리뷰 P1-2).
                안 적으면 「회수액이 적다」와 「회수액을 못 잰다」가 화면에서 같아 보인다. */}
            {(scorecard.profit_unknown_count ?? 0) > 0 &&
              ` 그중 BEP 없어 미산출 ${num(scorecard.profit_unknown_count ?? 0)}건 ·`}
            {/* ★편입분은 판정 «대상이 아니다» — 위 total에 안 들어가므로 여기 안 적으면
                장부 45건 중 43건이 이 화면에서 통째로 사라진다(D-NAO-176 적대 리뷰 P1). */}
            {(scorecard.imported_unjudgeable_count ?? 0) > 0 &&
              ` 콘솔 편입분 ${num(scorecard.imported_unjudgeable_count ?? 0)}건은 판정 대상 아님 ·`}
            성숙 기준일 {scorecard.mature_through}
          </p>
          {(scorecard.imported_unjudgeable_count ?? 0) > 0 && scorecard.imported_unjudgeable_note && (
            <p className="px-4 pb-2 text-xs text-gray-400">{scorecard.imported_unjudgeable_note}</p>
          )}
          <div className="flex flex-wrap gap-3 px-4 py-2 text-sm border-b border-gray-100">
            {VERDICT_ORDER.map((v) => (
              <span key={v} className="flex items-center gap-1.5">
                <Badge tone={VERDICT_BADGE_TONE[v]}>{VERDICT_LABEL[v]}</Badge>
                <span className="tabular-nums text-gray-700">{num(scorecard.by_verdict[v] ?? 0)}건</span>
              </span>
            ))}
          </div>
          <Table
            head={
              <>
                <Th>검색어</Th><Th>캠페인/그룹</Th><Th>실행일</Th>
                <Th right>전 일평균 광고비</Th><Th right>후 일평균 광고비</Th>
                <Th>판정</Th><Th right>회수 총이익</Th><Th>사유</Th>
                <Th right>캠페인 전환매출 전→후(일평균)</Th>
              </>
            }
          >
            {scorecard.items.map((it) => (
              <tr key={it.exclusion_id}>
                <Td>{it.search_term}</Td>
                <Td>
                  <span className="text-gray-500">
                    {it.campaign_name ?? it.campaign_id ?? NO_DATA} / {it.adgroup_name ?? it.adgroup_id ?? NO_DATA}
                  </span>
                </Td>
                <Td>{it.excluded_at.slice(0, 10)}</Td>
                <Td right>{wonPerDay(it.before.cost_per_day)}</Td>
                <Td right>{wonPerDay(it.after.cost_per_day)}</Td>
                <Td>
                  <Badge tone={VERDICT_BADGE_TONE[it.verdict]}>{VERDICT_LABEL[it.verdict]}</Badge>
                </Td>
                <Td right>{it.profit_recovered == null ? NO_DATA : won(it.profit_recovered)}</Td>
                <Td><span className="text-xs text-gray-600">{it.why}</span></Td>
                <Td right>
                  <span className="text-gray-500">
                    {wonPerDay(it.campaign.before.conv_amt_per_day)} →{" "}
                    {it.campaign.after == null ? NO_DATA : wonPerDay(it.campaign.after.conv_amt_per_day)}
                  </span>
                </Td>
              </tr>
            ))}
          </Table>
        </>
      )}
    </Card>
  );
}

function FreshnessPanel({ data }: { data: NaverExclusionListResponse }) {
  return (
    <Card title="입력 신선도 + 판정 창 — 빠진 것이 화면에 보인다">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 p-4">
        <div>
          <div className="text-xs text-gray-500">판정 창</div>
          <div className="text-sm font-medium text-gray-900">
            {data.window.from} ~ {data.window.to} ({data.window.days}일)
          </div>
        </div>
        <div>
          <div className="text-xs text-gray-500">최신 광고 데이터</div>
          <div className="text-sm font-medium text-gray-900">{data.freshness.latest_ad_date ?? NO_DATA}</div>
          {data.freshness.lag_days != null && (
            <div className="text-xs text-gray-400">{data.freshness.lag_days}일 지연</div>
          )}
        </div>
        <div>
          <div className="text-xs text-gray-500">최근 {data.maturity.lag_days}일(성숙도 미달) 제외</div>
          <div className="text-sm font-medium text-gray-900">
            {num(data.maturity.excluded_terms)}건 · {won(data.maturity.excluded_cost)}
          </div>
        </div>
        <div>
          <div className="text-xs text-gray-500">기준 시각</div>
          <div className="text-sm font-medium text-gray-900">{data.freshness.as_of}</div>
        </div>
      </div>
      <p className="px-4 pb-4 text-xs text-gray-500 border-t border-gray-100 pt-2">{data.maturity.why}</p>
    </Card>
  );
}

function BucketSummary({ data }: { data: NaverExclusionListResponse }) {
  return (
    <Card title="버킷 요약 — 후보에서 빠진 것도 전부 세어 보여준다">
      <div className="divide-y divide-gray-100">
        {bucketKeysToRender(data.buckets).map((key) => {
          const b = data.buckets[key];
          if (!b) return null;
          return (
            <div key={key} className="flex items-center justify-between px-4 py-2 text-sm">
              <div>
                <span className="text-gray-700">{BUCKET_LABEL[key as BucketKey] ?? key}</span>
                {key === "already_excluded" && (
                  <span className="ml-2 text-xs text-gray-400">
                    이미 잘라 둔 검색어 — 30일 창이라 당분간 계속 집계된다(다시 자를 필요 없음)
                  </span>
                )}
                {key === "powerlink_undecidable" && (
                  <span className="ml-2 text-xs text-gray-400">
                    네이버 API가 파워링크 검색어에 전환을 주지 않아 판정 불가
                  </span>
                )}
                {key === "capped_out" && b.why && (
                  <span className="ml-2 text-xs text-gray-400">{b.why}</span>
                )}
              </div>
              <div className="tabular-nums text-gray-900">{num(b.terms)}건 · {won(b.cost)}</div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

type ExecState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "done"; result: NaverSearchTermExecutionResultKind; diary: boolean }
  | { status: "error"; message: string };

const EXEC_RESULT_LABEL: Record<NaverSearchTermExecutionResultKind, string> = {
  created: "기록됨",
  already_recorded: "이미 기록됨",
  re_excluded: "재제외 기록됨",
};

function ExecutionCell({ state, onClick }: { state: ExecState; onClick: () => void }) {
  if (state.status === "loading") {
    return <Button variant="secondary" disabled>기록 중…</Button>;
  }
  if (state.status === "error") {
    return (
      <div className="space-y-1">
        <p className="text-xs text-red-700">{state.message}</p>
        <Button variant="secondary" onClick={onClick}>다시 시도</Button>
      </div>
    );
  }
  if (state.status === "done") {
    return (
      <div className="space-y-1">
        <Badge tone="good">{EXEC_RESULT_LABEL[state.result]}</Badge>
        {/* diary===false는 조용히 넘기면 안 된다 — 이 조치가 학습 사슬 입구에서 빠졌다는 뜻이다
            (search_term_execution.py의 fail-open 계약: 등록은 유지, 학습만 안 잡힌다). */}
        {state.diary === false && (
          <p className="text-xs text-red-700">
            운영일기 기록 실패 — 이 조치는 학습에 잡히지 않습니다
          </p>
        )}
      </div>
    );
  }
  return <Button variant="secondary" onClick={onClick}>제외했음 기록</Button>;
}

function CandidateTable({
  candidates, candidateCost, onRecorded,
}: { candidates: NaverExclusionCandidate[]; candidateCost: number; onRecorded: () => void }) {
  const [execState, setExecState] = useState<Record<string, ExecState>>({});

  async function handleRecord(c: NaverExclusionCandidate) {
    const key = `${c.adgroup_id}/${c.search_term}`;
    setExecState((s) => ({ ...s, [key]: { status: "loading" } }));
    try {
      const r = await postSearchTermExecution({
        campaignId: c.campaign_id,
        adgroupId: c.adgroup_id,
        searchTerm: c.search_term,
        // ★백엔드 문구 정본은 c.reason(리스트 생성기가 만든 근거 문장) — 화면이 새로 짓지 않는다.
        rationale: c.reason,
      });
      setExecState((s) => ({
        ...s, [key]: { status: "done", result: r.result, diary: r.diary },
      }));
      onRecorded();
    } catch (e) {
      setExecState((s) => ({
        ...s,
        [key]: { status: "error", message: e instanceof Error ? e.message : String(e) },
      }));
    }
  }

  return (
    <Card title="제외 후보">
      <p className="px-4 py-2 text-xs text-gray-500 border-b border-gray-100">
        합계 {num(candidates.length)}건 · 광고비 합계 {won(candidateCost)}
      </p>
      {/* 되돌림 경로는 표 위에 한 번만 둔다 — 전 행이 같은 문장이라 열로 두면 표를 못 읽는다.
          「무엇을 자를지와 어떻게 되돌릴지가 같은 화면에」(계약 판단기준 5)는 이걸로 충족된다. */}
      {candidates.length > 0 && (
        <>
          <p className="px-4 py-2 text-xs text-gray-500 border-b border-gray-100">
            되돌림 — {candidates[0].revert_howto}
          </p>
          <p className="px-4 py-2 text-xs text-gray-400 border-b border-gray-100">
            네이버 콘솔에서 실제로 자른 뒤 눌러 주세요 — 시스템은 네이버에 쓰지 않습니다
          </p>
        </>
      )}
      {candidates.length === 0 ? (
        <EmptyState
          reason="이번 창에는 자를 근거가 있는 검색어가 없습니다."
          hint="위 버킷 요약에서 어디로 빠졌는지 확인하세요(표본 부족·BEP 미확인·파워링크 판정 불가 등)."
        />
      ) : (
        <Table
          head={
            <>
              <Th>검색어</Th><Th>캠페인</Th><Th>광고그룹</Th>
              <Th right>30일 광고비</Th><Th right>클릭</Th><Th right>전환</Th><Th right>전환매출</Th>
              <Th right>ROAS</Th><Th right>적용 BEP</Th><Th>사유</Th><Th>실행 기록</Th>
            </>
          }
        >
          {candidates.map((c, i) => {
            const key = `${c.adgroup_id}/${c.search_term}`;
            const st = execState[key] ?? { status: "idle" as const };
            return (
              <tr key={`${key}#${i}`}>
                <Td>
                  {c.search_term}
                  {c.whitelisted && (
                    <span className="ml-1"><Badge tone="owner">상품 핵심어</Badge></span>
                  )}
                </Td>
                <Td><span className="text-gray-500">{c.campaign_name ?? c.campaign_id}</span></Td>
                <Td><span className="text-gray-500">{c.adgroup_name ?? c.adgroup_id}</span></Td>
                <Td right>{won(c.cost)}</Td>
                <Td right>{num(c.clk)}</Td>
                <Td right>{num(c.conv_purchase_cnt)}</Td>
                <Td right>{won(c.conv_purchase_amt)}</Td>
                <Td right>{roasX(c.roas)}</Td>
                <Td right>
                  {roasX(c.applied_bep)}
                  {c.bep_product_count > 1 && (
                    <span
                      className="block text-[11px] text-gray-400"
                      title={c.bep_products.map((p) => `${p.product_name ?? p.channel_product_id}: ${roasX(p.bep_roas)}`).join(", ")}
                    >
                      상품 {c.bep_product_count}개 중 최저
                    </span>
                  )}
                </Td>
                <Td><span className="text-xs text-gray-600">{c.reason}</span></Td>
                <Td>
                  <ExecutionCell state={st} onClick={() => handleRecord(c)} />
                </Td>
              </tr>
            );
          })}
        </Table>
      )}
    </Card>
  );
}
