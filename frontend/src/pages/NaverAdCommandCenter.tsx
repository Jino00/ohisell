// NaverAdCommandCenter.tsx — D-NAO-47 1층. 우리 MOP가 돌리는 광고의 성과.
//
// D-47-a: 1층은 "우리 MOP가 돌리는 광고의 성과"다(Jino: "우리 MOP가 돌리는 광고성과를 보자는거야").
// D-47-c: N=1(오늘 04 카나리 1개)과 N=여럿(나중)이 **같은 컴포넌트**다 — 카나리 전용 화면 금지.
// D-47-h: **"왜 0인가"가 1층 시민**이다. 라이브 실측상 이 화면은 대부분 0으로 채워진다
//         (커버리지 1.15% · 우리 조작 0회 · 승인 0건). 0을 찍고 침묵하면 MOP의 실패를
//         복제하는 것이다(스펙 §2-3). 볼품없어도 그게 사실이다.
//
// ★계획 대비 실측 정정(P2-T7): 계획서는 `fetchNaverAdDashboardOverview`·`fetchNaverCampaignSettings`
// 를 가정했으나 api.ts의 실제 함수명은 `getNaverDashboardOverview`(campaign-settings 쪽은 계획과
// 이름이 일치했음). optimizer_coverage의 총합 필드명도 `total`이 아니라 `total_cost`다
// (NaverDashboardOptimizerCoverage, api.ts:1856). NaverAdCampaignSettings에는 campaign_name이
// 없어(백엔드 _serialize_settings가 안 줌) campaign_id만 표시한다 — 추측으로 필드를 만들지 않는다.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Card, Stat, EmptyState, Loading, CoverageBar, Table, Th, Td, Badge, LayerNav, OptimizerSwitch } from "../components/ui";
import { num, won, roasX, pctFromFraction, NO_DATA } from "../lib/format";
import { useAsyncData } from "../lib/useAsyncData";
import {
  getNaverDashboardOverview, fetchNaverChangeLog,
  fetchNaverRetroScorecard, fetchNaverAdProposals,
  fetchNaverCampaignRoster, putNaverCampaignOptimizer,
  type NaverDashboardOverview, type NaverCampaignRosterRow, type NaverAdOptimizer,
  type NaverRetroScorecard, type NaverChangeLogRow,
} from "../lib/api";
import { PROPOSAL_TYPE_LABEL } from "./NaverAdOptimizationConsole";

// D-NAO-47 2층 ③ — 보드 6종(진단 보드와 동일 키) 한글 라벨. NaverRetroSignal.board 실측
// (models.py:1638 docstring) 기준.
const BOARD_LABEL: Record<string, string> = {
  bleeding_keywords: "출혈 키워드",
  starving_winners: "굶는 승자",
  shopping_group_bep: "쇼핑그룹 BEP",
  shopping_group_growth: "쇼핑그룹 성장",
  pause_candidates: "정지 후보",
  shopping_pause_candidates: "쇼핑 정지 후보",
};

// D-NAO-47 2층 ③ — pacing kind 한글은 백엔드가 이미 한글로 준다("저속"/"과속",
// retro_pacing_scorer._bucket). "unparsed"는 kind가 None인 행을 라우터가 묶은 버킷명
// (naver_ad.py:737 `kind = row.kind or "unparsed"`)이라 프론트에서 한글화한다.
const PACING_KIND_LABEL: Record<string, string> = { unparsed: "파싱 실패" };

export default function NaverAdCommandCenter() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [overview, setOverview] = useState<NaverDashboardOverview | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        setLoading(true);
        const ov = await getNaverDashboardOverview();
        if (!alive) return;
        setOverview(ov);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  if (loading) return <><LayerNav /><Loading label="커맨드 센터를 불러오는 중…" rows={6} /></>;
  if (error) return <><LayerNav /><EmptyState reason={`불러오지 못했습니다: ${error}`} hint="새로고침하거나 백엔드 상태를 확인하세요." /></>;

  const cov = overview?.optimizer_coverage ?? { window_days: 7, ours_cost: 0, mop_cost: 0, none_cost: 0, total_cost: 0, ours_ratio: 0 };

  return (
    <div className="space-y-4">
      <LayerNav />
      {/* ① 관리주체 3열 대조 — 우리 열만 크게(위계=대비) */}
      <Card title="누가 이 광고를 돌리는가">
        <div className="p-4 space-y-4">
          <CoverageBar ours={cov.ours_cost} mop={cov.mop_cost} manual={cov.none_cost} />
          <div className="grid grid-cols-3 gap-4">
            <div className="rounded border border-blue-200 bg-blue-50/40 p-3">
              <Badge tone="owner">우리 MOP</Badge>
              <div className="mt-2">
                {/* ★reason은 **측정한 사실만** 말한다(codex[P2] R5). ours_cost는 최근
                    N일 **광고비 롤업**이지 캠페인 보유 여부가 아니다. 0을 보고 "넘긴 캠페인이
                    없다"고 단언하면, 넘겼는데 이 기간 집행만 없었던 경우(정지·신규 인계)
                    바로 아래 캠페인 리스트에는 그 캠페인이 보이면서 위에선 없다고 말하는
                    **자기모순 화면**이 된다. 캠페인 유무는 아래 리스트가 말하게 둔다. */}
                <Stat
                  label="광고비"
                  value={won(cov.ours_cost)}
                  isEmpty={cov.ours_cost === 0}
                  reason={`최근 ${cov.window_days}일 우리 MOP 캠페인의 광고비 집행이 없습니다.`}
                  tone={cov.ours_cost === 0 ? "idle" : "neutral"}
                  sub={cov.total_cost ? `전체의 ${pctFromFraction(cov.ours_cost / cov.total_cost)}` : undefined}
                />
              </div>
            </div>
            <div className="rounded border border-gray-200 p-3">
              <Badge>원본 MOP</Badge>
              <div className="mt-2">
                {/* ★같은 이유로 태깅 여부를 단언하지 않는다 — 측정한 건 광고비 0이고,
                    태깅 누락은 **가장 유력한 원인**이지 관측 사실이 아니다(D-47-g). */}
                <Stat
                  label="광고비"
                  value={won(cov.mop_cost)}
                  isEmpty={cov.mop_cost === 0}
                  reason={`최근 ${cov.window_days}일 optimizer='mop' 태깅 캠페인의 광고비가 없습니다 — 03 태깅 전이면 태깅이 필요합니다(D-47-g).`}
                  tone="idle"
                />
              </div>
            </div>
            <div className="rounded border border-gray-200 p-3">
              <Badge>수동</Badge>
              <div className="mt-2">
                <Stat label="광고비" value={won(cov.none_cost)} tone="neutral" />
              </div>
            </div>
          </div>
        </div>
      </Card>

      {/* ② 우리 MOP 캠페인 리스트 — 오늘 1행, 나중 N행(D-47-c) */}
      <Card
        title="스마트스토어 캠페인 — 누구에게 맡길지"
        right={<Link to="/naver-ad/console" className="text-xs text-blue-600 hover:underline">모드·공격성 설정 →</Link>}
      >
        <CampaignRoster />
      </Card>

      {/* ③ 성적표 두 겹 — 중복 아니라 상보 + 외부 변경 감지(D-NAO-50) */}
      <div className="grid grid-cols-2 gap-4">
        <Card title="우리 조언이 맞았나 (방향 정밀도)">
          <RetroScorecardPane />
        </Card>
        <Card title="우리가 한 일의 결과 (인과)">
          <ChangeLogPane />
        </Card>
        <Card title="외부(MOP/사람)가 바꾼 것 감지">
          <ExternalChangesPane />
        </Card>
      </div>

      {/* ④ 나를 기다리는 것 */}
      <Card title="나를 기다리는 것">
        <PendingPane />
      </Card>
    </div>
  );
}

// D-NAO-48 — 광고 종류 한글 라벨. naver_entity.campaign_type 실측값 3종.
const CAMPAIGN_TYPE_LABEL: Record<string, string> = {
  WEB_SITE: "파워링크",
  SHOPPING: "쇼핑",
  BRAND_SEARCH: "브랜드검색",
};

function CampaignRoster() {
  // D-NAO-48: Jino "광고 종류별 스마트스토어 광고 옆에 토글버튼을 달아서 우리가 만든 MOP로
  // 돌릴지 선택하게 해주자". 여기가 카나리가 확대되는 자리다(D-47-c).
  // ★전체 캠페인을 보여준다 — 우리 것만 보여주면 넘길 대상이 화면에 없어서 확대를 못 한다.
  const [rows, setRows] = useState<NaverCampaignRosterRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetchNaverCampaignRoster({ days: 30 })
      .then((r) => { if (alive) setRows(r.rows); })
      // ★실패를 빈 목록으로 위장하지 않는다 — "캠페인이 없다"고 단언하게 된다(D-47-h).
      .catch((e) => { if (alive) setError(e instanceof Error ? e.message : String(e)); });
    return () => { alive = false; };
  }, []);

  async function changeOptimizer(campaignId: string, next: NaverAdOptimizer) {
    setSaveError(null);
    try {
      // ★putNaverCampaignSettings(전체 치환)가 아니라 전용 엔드포인트 — 그걸 쓰면
      //   mode·override·gamma·memo가 전부 null로 날아간다(D-NAO-48 백엔드 주석 참조).
      await putNaverCampaignOptimizer({ campaignId, optimizer: next });
      setRows((prev) => prev?.map((r) => (r.campaign_id === campaignId ? { ...r, optimizer: next } : r)) ?? prev);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    }
  }

  if (error) {
    return <EmptyState reason={`캠페인 목록을 불러오지 못했습니다: ${error}`} hint="새로고침하거나 백엔드 상태를 확인하세요." />;
  }
  if (rows === null) return <Loading rows={4} />;
  if (rows.length === 0) {
    return <EmptyState reason="수집된 캠페인이 없습니다." hint="캠페인 인벤토리는 매일 07:35 크론이 동기화합니다." />;
  }

  // 광고 종류별로 묶는다(Jino "광고 종류별"). 종류 안에서는 광고비 순(명부 SA가 정렬).
  const byType = new Map<string, NaverCampaignRosterRow[]>();
  for (const r of rows) {
    const list = byType.get(r.campaign_type) ?? [];
    list.push(r);
    byType.set(r.campaign_type, list);
  }
  const oursCount = rows.filter((r) => r.optimizer === "ours").length;

  return (
    <div>
      {saveError && <p className="px-4 py-2 text-xs text-judge-bad bg-red-50">{saveError}</p>}
      <p className="px-4 py-2 text-xs text-gray-500 border-b border-gray-100">
        전체 {num(rows.length)}개 중 우리 MOP {num(oursCount)}개 — 넘기면 우리 프로그램이 그 캠페인의
        입찰을 제안·실행합니다(사람 승인 게이트 유지). 원본 MOP는 자동으로 꺼지지 않습니다.
      </p>
      {[...byType.entries()].map(([type, list]) => (
        <div key={type}>
          <p className="px-4 py-1.5 text-xs font-medium text-gray-600 bg-gray-50 border-y border-gray-100">
            {CAMPAIGN_TYPE_LABEL[type] ?? type} · {num(list.length)}개
          </p>
          <Table head={<>
            <Th>캠페인</Th>
            <Th right>광고비(30일)</Th>
            <Th right>ROAS</Th>
            <Th>관리 주체</Th>
          </>}>
            {list.map((c) => (
              <tr key={c.campaign_id}>
                <Td>
                  {/* ★내부 ID 대신 이름 — MOP가 저지른 실수(§2-7)를 우리도 하고 있었다.
                      D-NAO-48 명부 SA가 naver_entity의 이름을 실어준다. */}
                  <span className="text-gray-900">{c.name || c.campaign_id}</span>
                  {c.status !== "on" && <Badge>{c.status === "off" ? "정지" : c.status}</Badge>}
                </Td>
                <Td right>{won(c.cost)}</Td>
                {/* ★cost 0이면 roas는 null — "0배"가 아니라 "알 수 없음"이다. */}
                <Td right>{c.roas_naver == null ? NO_DATA : roasX(c.roas_naver)}</Td>
                <Td>
                  <OptimizerSwitch
                    campaignId={c.campaign_id}
                    campaignName={c.name || c.campaign_id}
                    value={c.optimizer}
                    onChange={changeOptimizer}
                  />
                </Td>
              </tr>
            ))}
          </Table>
        </div>
      ))}
    </div>
  );
}

function RetroScorecardPane() {
  const { data, error } = useAsyncData<NaverRetroScorecard>(() => fetchNaverRetroScorecard(), []);
  if (error) return <EmptyState reason={`불러오지 못했습니다: ${error}`} hint="새로고침하거나 백엔드 상태를 확인하세요." />;
  if (data === null) return <Loading rows={3} />;
  return <RetroRollup data={data} />;
}

function RetroRollup({ data }: { data: NaverRetroScorecard }) {
  const boardEntries = Object.entries(data.boards);
  const pacingEntries = Object.entries(data.pacing);

  if (boardEntries.length === 0 && pacingEntries.length === 0) {
    return (
      <EmptyState
        reason="최근 창에 채점된 소급 신호가 없습니다."
        hint={`조회 창 ${data.window_days}일 — 진단 보드 스냅샷이 D+3/D+7 사후창에 아직 도달하지 않았을 수 있습니다.`}
      />
    );
  }

  return (
    <div className="p-4 space-y-4">
      {/* 보드별 방향 정밀도(D+3/D+7) — 지출 지속 타깃 기준(no_spend 제외), 정직 경계는
          위 함수 docstring 참조. */}
      {boardEntries.length > 0 && (
        <div>
          <p className="text-xs text-gray-500 mb-2">보드별 방향 정밀도 (지출 대상 기준, no_spend 제외)</p>
          <Table head={<><Th>보드</Th><Th right>D+3 정밀도</Th><Th right>D+7 정밀도</Th></>}>
            {boardEntries.map(([board, h]) => (
              <tr key={board}>
                <Td>{BOARD_LABEL[board] ?? board}</Td>
                <Td right>
                  {h.d3.precision_spenders == null ? NO_DATA : pctFromFraction(h.d3.precision_spenders, 1)}
                  <span className="text-gray-400"> ({num(h.d3.correct)}/{num(h.d3.correct + h.d3.gray + h.d3.wrong)})</span>
                </Td>
                <Td right>
                  {h.d7.precision_spenders == null ? NO_DATA : pctFromFraction(h.d7.precision_spenders, 1)}
                  <span className="text-gray-400"> ({num(h.d7.correct)}/{num(h.d7.correct + h.d7.gray + h.d7.wrong)})</span>
                </Td>
              </tr>
            ))}
          </Table>
        </div>
      )}

      {/* ★저속 경보 롤업 — 접지 말고 집계(스펙 §1-3 정정): 초판이 "노이즈"라 규정한 게
          틀렸다. 저속 경보는 만성 저소진의 실재 신호였다(실측: correct 98.7%, D-NAO-45 HANDOFF).
          verdict 버킷은 retro_pacing_scorer._bucket 고정: correct/partial/false_alarm. */}
      {pacingEntries.length > 0 && (
        <div>
          <p className="text-xs text-gray-500 mb-2">페이싱 경보 채점 (그날 최종 소진과 대조)</p>
          <div className="space-y-1">
            {pacingEntries.map(([kind, verdicts]) => {
              const correct = verdicts.correct ?? 0;
              const partial = verdicts.partial ?? 0;
              const falseAlarm = verdicts.false_alarm ?? 0;
              const unparsed = verdicts.unparsed ?? 0;
              const scored = correct + partial + falseAlarm;
              const precision = scored > 0 ? correct / scored : null;
              // ★평균 최종 소진율 — 이 롤업의 진짜 punchline(D-NAO-47에서 백엔드에 추가).
              //   correct 건수는 "경보가 맞았다"까지고, 이 숫자라야 **"하루가 끝나도 일예산의
              //   4.9%만 썼다 = 만성 저소진이 실재한다"**는 증거가 된다. null이면 "0%"가 아니라
              //   "알 수 없음"이다(final_ratio가 전부 NULL인 unparsed 버킷).
              const correctRatio = data.pacing_final_ratio?.[kind]?.correct ?? null;
              return (
                <div key={kind} className="border-b border-gray-100 py-1.5 last:border-0">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-gray-700">{PACING_KIND_LABEL[kind] ?? kind}</span>
                    <span className="text-xs text-gray-500 tabular-nums">
                      {scored > 0 ? (
                        <>correct {num(correct)} · partial {num(partial)} · false_alarm {num(falseAlarm)} · 정밀도 {precision != null ? pctFromFraction(precision, 1) : NO_DATA}</>
                      ) : unparsed > 0 ? (
                        <>{num(unparsed)}건</>
                      ) : NO_DATA}
                    </span>
                  </div>
                  {correct > 0 && (
                    <p className="mt-0.5 text-xs text-gray-500 tabular-nums">
                      맞은 경보의 평균 최종 소진율{" "}
                      <span className={correctRatio != null && correctRatio < 0.5 ? "text-judge-warn font-medium" : ""}>
                        {correctRatio != null ? pctFromFraction(correctRatio, 1) : NO_DATA}
                      </span>
                      {correctRatio != null && correctRatio < 0.5 && " — 하루가 끝나도 일예산을 이만큼밖에 못 씀(만성 저소진)"}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

/** change_log 한 행을 "무엇이 어떻게 바뀌었나" 한 줄로. ★action마다 after_value 모양이 다르다
 *  (writer가 네이버 재조회 응답을 그대로 dumps하므로 camelCase, add_negative_keyword만 래핑됨).
 *  bidAmt/userLock만 읽으면 update_budget·add_negative_keyword 실집행이 "— → —"로 보인다
 *  (codex[P2] R3) — 그 두 액션도 EXECUTION_ACTIONS라 이 패널에 뜨는데, **"무엇을 왜 바꿨는지"가
 *  MOP의 최대 공백이자 우리가 이길 자리**(ref24)라 절반을 비워두면 자기모순이다.
 *  실측 근거: update_bid→get_keyword()의 bidAmt / update_budget→campaign의 dailyBudget /
 *  set_user_lock→userLock / add_negative_keyword→{after, created_ids}(harness:466 래핑). */
/** D-NAO-50 __bulk__ 요약행 전용 — entity_sync._emit_inventory_side가 200건 초과 시 남기는
 *  {"bulk": true, "count": N, "sample": [이름 최대 20개]} 모양을 그린다(keyword/bidAmt 없음). */
function describeBulkKeywordChange(payload: Record<string, unknown> | null, verb: string): string {
  const count = typeof payload?.count === "number" ? num(payload.count) : NO_DATA;
  const sample = Array.isArray(payload?.sample)
    ? payload.sample.filter((s): s is string => typeof s === "string")
    : [];
  const preview = sample.length > 0 ? ` (예: ${sample.slice(0, 3).join(", ")} …)` : "";
  return `키워드 대량 ${verb} ${count}건${preview}`;
}

function describeChange(row: NaverChangeLogRow): string {
  const b = row.before as Record<string, unknown> | null;
  const a = row.after as Record<string, unknown> | null;
  const lock = (v: unknown) => (v === true ? "정지" : v === false ? "재개" : NO_DATA);
  const n = (v: unknown) => (typeof v === "number" ? won(v) : NO_DATA);

  switch (row.action) {
    case "update_bid":
      return `입찰가 ${n(b?.bidAmt)} → ${n(a?.bidAmt)}`;
    case "update_budget":
      return `일예산 ${n(b?.dailyBudget)} → ${n(a?.dailyBudget)}`;
    case "set_user_lock":
      return `${lock(b?.userLock)} → ${lock(a?.userLock)}`;
    case "add_negative_keyword": {
      const created = Array.isArray(a?.created_ids) ? a.created_ids.length : null;
      return created == null ? "제외 키워드 추가" : `제외 키워드 ${num(created)}개 추가`;
    }
    // 외부 감지 2종(라이브 검증이 발견 — ExternalChangesPane이 생기기 전엔 어떤 화면에도
    // 안 떠서 케이스 누락이 보이지 않았고, prod 15행 전부가 action 원문으로 렌더됐다).
    // payload는 우리 실행과 같은 키다: userLock / bidAmt(entity_sync.py:163·300 실측,
    // bidAmt는 직전 관측 부재 시 null 가능 → n()이 막는다).
    case "external_status_change":
      return `상태 변경 감지: ${lock(b?.userLock)} → ${lock(a?.userLock)}`;
    case "external_bid_change":
      return `입찰가 변경 감지: ${n(b?.bidAmt)} → ${n(a?.bidAmt)}`;
    // D-NAO-50 키워드 밸브: added는 after_value, removed는 before_value에 실린다(entity_sync.py
    // _emit_inventory_side). ★대량(__bulk__)은 payload 모양이 다르다({bulk,count,sample} —
    // keyword/bidAmt 없음) — entity_id로 먼저 분기해 일반 케이스와 섞이지 않게 한다.
    case "external_keyword_added": {
      if (row.entity_id === "__bulk__") return describeBulkKeywordChange(a, "등록");
      const kw = typeof a?.keyword === "string" ? a.keyword : NO_DATA;
      const bid = typeof a?.bidAmt === "number" ? ` (입찰가 ${won(a.bidAmt)})` : "";
      return `키워드 등록 감지: "${kw}"${bid}`;
    }
    case "external_keyword_removed": {
      if (row.entity_id === "__bulk__") return describeBulkKeywordChange(b, "소실");
      const kw = typeof b?.keyword === "string" ? b.keyword : NO_DATA;
      return `키워드 삭제·소실 감지: "${kw}"`;
    }
    default:
      // 알 수 없는 액션은 지어내지 않는다 — action 원문을 그대로 보여준다.
      return row.action;
  }
}

// ══════════════════════════════════════════════════════════════════
// D-NAO-54 기간 선택 — 변경 이력 두 패널이 공유하는 최소 단위.
// Jino 확정(2026-07-17): 기본=당일 · 7일/30일은 **어제 기준**(당일은 진행 중이라 별도 탭이고,
// 섞으면 "완결된 과거"라는 탭의 의미가 깨진다) · 임의 구간은 캘린더.
// ══════════════════════════════════════════════════════════════════

type DateRange = { from: string; to: string };

/** KST 기준 N일 전 날짜(YYYY-MM-DD).
 *  ★브라우저 로컬시각(`new Date().toISOString().slice(0,10)` 등)으로 계산하면 안 된다:
 *  그건 UTC/로컬 날짜이고 서버의 changed_at은 **KST**다(harness·entity_sync가 kst_now()로
 *  명시 저장). 자정~09:00 사이에 하루가 어긋난다 — 이 저장소가 이미 두 번 당한 함정
 *  (memory: sqlite-server-default-now-is-utc). 그래서 타임존을 명시해 KST로 못박는다.
 *  ★en-CA 로케일이 YYYY-MM-DD를 준다. 한국은 DST가 없어 ±N일이 정확히 86,400초다. */
function kstDate(offsetDays: number): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul" })
    .format(new Date(Date.now() + offsetDays * 86_400_000));
}

const PERIOD_PRESETS = [
  { key: "today", label: "당일", range: (): DateRange => ({ from: kstDate(0), to: kstDate(0) }) },
  { key: "yesterday", label: "어제", range: (): DateRange => ({ from: kstDate(-1), to: kstDate(-1) }) },
  // 어제를 끝점으로 7일/30일 — 당일 미포함(끝점 포함이므로 -7..-1이 정확히 7일이다).
  { key: "7d", label: "7일", range: (): DateRange => ({ from: kstDate(-7), to: kstDate(-1) }) },
  { key: "30d", label: "30일", range: (): DateRange => ({ from: kstDate(-30), to: kstDate(-1) }) },
] as const;

type PeriodKey = (typeof PERIOD_PRESETS)[number]["key"] | "custom";
type Period = ReturnType<typeof usePeriod>;

/** 커스텀 구간이 조회 불가한 이유. null이면 조회 가능.
 *  ★`<input type="date">`는 사용자가 지우면 **빈 문자열**을 준다(실측). 그걸 안 잡으면
 *  `date_from=`이 그대로 나가고 백엔드가 422 + 날 것의 pydantic 메시지를 뱉어
 *  화면에 "불러오지 못했습니다: Input should be a valid date…"가 뜬다(실측).
 *  ★`from > to` 하나만 보면 빈 문자열을 못 잡는다: `"" > "2026-07-17"`는 **false**다(실측).
 *  ISO 날짜라서 문자열 비교가 곧 날짜 비교인 건 **양쪽이 채워졌을 때만** 참이다. */
export function customRangeError(range: DateRange): string | null {
  if (!range.from || !range.to) return "시작일과 종료일을 모두 선택하세요.";
  if (range.from > range.to) return "시작일이 종료일보다 늦습니다.";
  return null;
}

/** 기간 상태. range는 원시 문자열 2개라 useAsyncData deps에 그대로 넣어도 안정적이다. */
function usePeriod() {
  const [key, setKey] = useState<PeriodKey>("today");
  const [custom, setCustom] = useState<DateRange>({ from: kstDate(-7), to: kstDate(0) });
  const preset = PERIOD_PRESETS.find((p) => p.key === key);
  const range = preset ? preset.range() : custom;
  // 잘못된 구간은 백엔드도 422로 막지만(조용한 빈 결과 = "변경 없음"으로 읽히므로),
  // 화면에서 먼저 잡아 요청 자체를 안 보낸다 — 422 원문은 사용자에게 무의미하다.
  const error = preset ? null : customRangeError(custom);
  const label = preset ? preset.label : `${custom.from} ~ ${custom.to}`;
  return { key, setKey, custom, setCustom, range, error, label };
}

function PeriodTabs({ p }: { p: Period }) {
  return (
    <div className="px-4 py-2 border-b border-gray-100 flex items-center gap-1 flex-wrap">
      {PERIOD_PRESETS.map((preset) => (
        <button
          key={preset.key}
          type="button"
          onClick={() => p.setKey(preset.key)}
          className={`px-2 py-0.5 text-xs rounded-full ${
            p.key === preset.key ? "bg-blue-50 text-blue-700 font-semibold" : "text-gray-600 hover:bg-gray-100"
          }`}
        >
          {preset.label}
        </button>
      ))}
      <button
        type="button"
        onClick={() => p.setKey("custom")}
        className={`px-2 py-0.5 text-xs rounded-full ${
          p.key === "custom" ? "bg-blue-50 text-blue-700 font-semibold" : "text-gray-600 hover:bg-gray-100"
        }`}
      >
        기간 지정
      </button>
      {p.key === "custom" && (
        <span className="flex items-center gap-1 ml-1">
          <input
            type="date" value={p.custom.from}
            onChange={(e) => p.setCustom({ ...p.custom, from: e.target.value })}
            className="text-xs border border-gray-200 rounded px-1 py-0.5"
          />
          <span className="text-xs text-gray-400">~</span>
          <input
            type="date" value={p.custom.to}
            onChange={(e) => p.setCustom({ ...p.custom, to: e.target.value })}
            className="text-xs border border-gray-200 rounded px-1 py-0.5"
          />
        </span>
      )}
    </div>
  );
}

/** 변경 칸. ★차단 행은 before/after가 **둘 다 없다**(harness._guard_failure는 writer를 부르지도
 *  않는다) — describeChange에 그대로 넘기면 "입찰가 — → —"가 되어 데이터 결손처럼 보인다.
 *  차단은 결손이 아니라 **가드레일이 일한 사건**이다(왜 막혔는지는 근거 칸이 말한다). */
function ChangeCell({ row }: { row: NaverChangeLogRow }) {
  if (!row.executed) return <Badge tone="neutral">🚫 차단됨</Badge>;
  return <span className="text-xs tabular-nums">{describeChange(row)}</span>;
}

function ChangeLogPane() {
  // ★이 패널은 "우리가 한 일의 결과"(인과)다 — 외부가 바꾼 걸 감지한 행이 섞이면
  //   남의 조작을 우리 성과로 보여주게 된다(codex[P2] 2026-07-17). 외부 변경은
  //   별도 관심사라 여기 섞지 않는다 — 그 피드는 ExternalChangesPane(바로 아래)이 그린다.
  // ★include_blocked(D-NAO-54): 가드레일이 막은 시도도 보여준다. prod 실측(2026-07-17)에서
  //   입찰 시도 4건 중 2건이 차단됐는데(변경폭 39.3%>15% · 쿨다운 1.0h<5h) 어느 화면에도
  //   안 떴다 — 가드레일이 일한 것도 우리가 한 일이다. total의 정직성은 백엔드가 지킨다
  //   (기본 False라 1층 "우리 조작 N회"는 여전히 실집행만 센다).
  const p = usePeriod();
  const { data, error } = useAsyncData(
    // ★잘못된 구간이면 요청을 아예 안 보낸다(422 원문 노출 방지). deps에 p.error를 넣어
    //   구간이 고쳐지는 순간 다시 질의하게 한다.
    () => p.error
      ? Promise.resolve({ total: 0, rows: [] })
      : fetchNaverChangeLog({
          date_from: p.range.from, date_to: p.range.to, limit: 10, actor: "ours", include_blocked: true,
        }),
    [p.range.from, p.range.to, p.error],
  );
  const body = () => {
    if (p.error) return <EmptyState reason={p.error} hint="기간을 다시 선택하세요." />;
    if (error) return <EmptyState reason={`불러오지 못했습니다: ${error}`} hint="새로고침하거나 백엔드 상태를 확인하세요." />;
    if (data === null) return <Loading rows={3} />;
    if (data.rows.length === 0) {
      return (
        <EmptyState
          reason={`${p.label}: 우리가 집행한 변경이 없습니다.`}
          hint="기간을 넓혀 보세요. 제안이 있어도 승인 게이트를 통과한 실행이 없으면 여기는 빕니다."
        />
      );
    }
    return (
      <>
      <Table head={<><Th>시각</Th><Th>대상</Th><Th>변경</Th><Th>근거</Th></>}>
        {data.rows.map((r) => (
          <tr key={r.id}>
            <Td><span className="text-xs text-gray-500">{r.changed_at?.slice(5, 16) ?? NO_DATA}</span></Td>
            <Td><span className="text-xs">{r.entity_type} {r.entity_id}</span></Td>
            {/* ★"무엇을 왜 바꿨는지" — MOP에 0개인 컬럼(ref24). 우리가 이길 자리이므로
                지원하는 실행 액션 4종을 전부 제대로 그린다(codex[P2] R3). */}
            <Td><ChangeCell row={r} /></Td>
            <Td><span className="text-xs text-gray-600">{r.rationale ?? NO_DATA}</span></Td>
          </tr>
        ))}
      </Table>
      {/* ★잘림을 숨기지 않는다(라이브 검증: 15건인데 10건만 보이고 표식 0). PendingPane의
          집계 푸터와 같은 패턴. */}
      {data.total > data.rows.length && (
        <p className="px-4 py-2 text-xs text-gray-500 border-t border-gray-100">
          최근 {num(data.rows.length)}건 표시 · 총 {num(data.total)}건
        </p>
      )}
      </>
    );
  };
  return <><PeriodTabs p={p} />{body()}</>;
}

function ExternalChangesPane() {
  // D-NAO-50: actor=external의 **첫 화면 소비자**. 이 패널이 없으면 키워드·입찰가·상태
  // diff 밸브가 DB에만 쌓이고 어디서도 안 보인다 — 정확히 "수집은 풍부한데 화면엔 없음"
  // (스펙 §1-4)의 재발이다. ChangeLogPane과 구조는 같지만 관심사가 반대다:
  // 그쪽=우리가 한 일(인과), 이쪽=외부(MOP/사람)가 바꾼 걸 우리가 관측한 것(감지).
  // ★include_blocked를 안 쓴다: 외부 변경엔 '차단'이 없다(우리 가드레일은 우리 쓰기에만 건다).
  const p = usePeriod();
  const { data, error } = useAsyncData(
    // ★ChangeLogPane과 같은 이유로 잘못된 구간이면 요청하지 않는다.
    () => p.error
      ? Promise.resolve({ total: 0, rows: [] })
      : fetchNaverChangeLog({ date_from: p.range.from, date_to: p.range.to, limit: 10, actor: "external" }),
    [p.range.from, p.range.to, p.error],
  );
  const body = () => {
    if (p.error) return <EmptyState reason={p.error} hint="기간을 다시 선택하세요." />;
    if (error) return <EmptyState reason={`불러오지 못했습니다: ${error}`} hint="새로고침하거나 백엔드 상태를 확인하세요." />;
    if (data === null) return <Loading rows={3} />;
    if (data.rows.length === 0) {
      return (
        <EmptyState
          reason={`${p.label}: 감지된 외부 변경이 없습니다.`}
          hint="입찰가·상태·키워드 diff는 매일 07:35 entity_sync에서 잡힙니다."
        />
      );
    }
    return (
      <>
        <Table head={<><Th>시각</Th><Th>대상</Th><Th>변경</Th><Th>근거</Th></>}>
          {data.rows.map((r) => (
            <tr key={r.id}>
              <Td><span className="text-xs text-gray-500">{r.changed_at?.slice(5, 16) ?? NO_DATA}</span></Td>
              <Td><span className="text-xs">{r.entity_type} {r.entity_id}</span></Td>
              <Td><span className="text-xs tabular-nums">{describeChange(r)}</span></Td>
              <Td><span className="text-xs text-gray-600">{r.rationale ?? NO_DATA}</span></Td>
            </tr>
          ))}
        </Table>
        {/* ★잘림을 숨기지 않는다(라이브 검증: 15건인데 10건만 보이고 표식 0). */}
        {data.total > data.rows.length && (
          <p className="px-4 py-2 text-xs text-gray-500 border-t border-gray-100">
            최근 {num(data.rows.length)}건 표시 · 총 {num(data.total)}건
          </p>
        )}
      </>
    );
  };
  return <><PeriodTabs p={p} />{body()}</>;
}

function PendingPane() {
  // ★실행형과 정보성을 **각각 질의한다**(2026-07-17 prod 배포 검증에서 발견).
  //   한 번에 받아 !informational로 거르면 안 된다: 목록이 created_at DESC이고 정보성이
  //   훨씬 자주 생성돼서, prod 실측 당시 pending 107건 중 최신 100건이 **전부 trigger_pacing**
  //   이었다 → 실행형 bid_up 5건이 페이지 밖으로 밀려나 "지금 결정할 제안이 없습니다"가
  //   렌더됐다. 5건이 사람 결정을 기다리는데 없다고 말한 것 — 이 패널이 존재하는 이유가
  //   정확히 그 5건을 보여주는 것이라 치명적이다.
  //   limit을 올리는 건 임시방편(정보성이 더 쌓이면 다시 밀려남) → 서버에 물어본다.
  const { data, error } = useAsyncData(
    () => Promise.all([
      fetchNaverAdProposals({ status: "pending", informational: false, limit: 200 }).then((r) => r.rows),
      // ★limit=1이어도 total은 전체 건수라 정확하다(rows.length를 세면 1이 된다).
      fetchNaverAdProposals({ status: "pending", informational: true, limit: 1 }),
    ]).then(([act, info]) => ({ actionable: act, informationalCount: info.total })),
    [],
  );
  if (error) return <EmptyState reason={`불러오지 못했습니다: ${error}`} hint="새로고침하거나 백엔드 상태를 확인하세요." />;
  if (data === null) return <Loading rows={3} />;
  const actionable = data.actionable;

  return (
    <div>
      {actionable.length === 0 ? (
        <EmptyState reason="지금 결정할 제안이 없습니다." hint="정보성 경보는 아래에 집계됩니다." />
      ) : (
        <Table head={<><Th>유형</Th><Th>대상</Th><Th right>목표</Th><Th>근거</Th></>}>
          {actionable.map((p) => (
            <tr key={p.id}>
              <Td>{PROPOSAL_TYPE_LABEL[p.proposal_type] ?? p.proposal_type}</Td>
              <Td><span className="text-xs">{p.target_id}</span></Td>
              <Td right>{p.target_bid != null ? won(p.target_bid) : p.target_budget != null ? won(p.target_budget) : NO_DATA}</Td>
              <Td><span className="text-xs text-gray-600">{p.rationale}</span></Td>
            </tr>
          ))}
        </Table>
      )}
      {/* ★정보성은 접지 말고 집계 — 저속 경보 98.7%가 진짜였다(스펙 §1-3 정정).
          개별 건을 나열하진 않되, 숨기지도 않는다. */}
      {data.informationalCount > 0 && (
        <p className="px-4 py-2 text-xs text-gray-500 border-t border-gray-100">
          정보성 경보 {num(data.informationalCount)}건 집계됨(개별 나열 안 함) —{" "}
          <Link to="/naver-ad/console" className="text-blue-600 hover:underline">콘솔에서 보기</Link>
        </p>
      )}
    </div>
  );
}
