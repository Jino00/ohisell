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
import { Card, Table, Th, Td, Badge, LayerNav, Loading, EmptyState } from "../components/ui";
import { useAsyncData } from "../lib/useAsyncData";
import { num, won, roasX, NO_DATA } from "../lib/format";
import {
  getSearchTermExclusionList, getSearchTermExclusionSurvival,
  type NaverExclusionListResponse, type NaverExclusionCandidate, type NaverExclusionSurvival,
} from "../lib/api";

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
  insufficient_sample: "표본 부족",
  bep_unknown: "BEP 미확인",
  powerlink_undecidable: "파워링크 판정 불가",
  profitable: "BEP 이상(수익성 있음)",
  capped_out: "라운드 상한 초과",
  maturity_excluded: "성숙도 미달 제외",
};

const BUCKET_ORDER: BucketKey[] = [
  "insufficient_sample", "bep_unknown", "powerlink_undecidable",
  "profitable", "capped_out", "maturity_excluded",
];

export default function NaverAdExclusionList() {
  const [days, setDays] = useState(30);
  const [roundCap, setRoundCap] = useState(50);
  const [campaignId, setCampaignId] = useState("");
  const [campaignOptions, setCampaignOptions] = useState<{ id: string; name: string }[]>([]);

  const { data, error } = useAsyncData(
    () => getSearchTermExclusionList({ days, campaignId: campaignId || undefined, roundCap }),
    [days, campaignId, roundCap],
  );
  const { data: survival, error: survivalError } = useAsyncData(
    () => getSearchTermExclusionSurvival(),
    [],
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
          <CandidateTable candidates={data.candidates} candidateCost={data.candidate_cost} />
        </>
      )}
    </div>
  );
}

function SurvivalPanel({ survival }: { survival: NaverExclusionSurvival }) {
  const lastChecked = survival.last_checked_at
    ? survival.last_checked_at.slice(0, 16).replace("T", " ")
    : NO_DATA;

  if (survival.healthy) {
    return (
      <p className="px-4 py-3 text-sm text-emerald-700">
        우리가 건 제외 {num(survival.alive)}건 모두 걸려 있음 · 마지막 대조 {lastChecked}
      </p>
    );
  }

  return (
    <div>
      <p className="px-4 py-3 text-sm text-red-700">
        감시 대상 {num(survival.monitored)}건 중 어긋남 {num(survival.breached.length)}건
        {survival.stale && " · 대조 자체가 멈춤"}
        {survival.never_checked > 0 && ` · 한 번도 대조 안 됨 ${num(survival.never_checked)}건`}
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
        {BUCKET_ORDER.map((key) => {
          const b = data.buckets[key];
          return (
            <div key={key} className="flex items-center justify-between px-4 py-2 text-sm">
              <div>
                <span className="text-gray-700">{BUCKET_LABEL[key]}</span>
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

function CandidateTable({
  candidates, candidateCost,
}: { candidates: NaverExclusionCandidate[]; candidateCost: number }) {
  return (
    <Card title="제외 후보">
      <p className="px-4 py-2 text-xs text-gray-500 border-b border-gray-100">
        합계 {num(candidates.length)}건 · 광고비 합계 {won(candidateCost)}
      </p>
      {/* 되돌림 경로는 표 위에 한 번만 둔다 — 전 행이 같은 문장이라 열로 두면 표를 못 읽는다.
          「무엇을 자를지와 어떻게 되돌릴지가 같은 화면에」(계약 판단기준 5)는 이걸로 충족된다. */}
      {candidates.length > 0 && (
        <p className="px-4 py-2 text-xs text-gray-500 border-b border-gray-100">
          되돌림 — {candidates[0].revert_howto}
        </p>
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
              <Th right>ROAS</Th><Th right>적용 BEP</Th><Th>사유</Th>
            </>
          }
        >
          {candidates.map((c, i) => (
            <tr key={`${c.adgroup_id}/${c.search_term}#${i}`}>
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
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}
