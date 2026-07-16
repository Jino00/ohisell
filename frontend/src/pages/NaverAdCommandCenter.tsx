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
import { Card, Stat, EmptyState, Loading, CoverageBar, Table, Th, Td, Badge, LayerNav } from "../components/ui";
import { num, won, pctFromFraction, NO_DATA } from "../lib/format";
import { useAsyncData } from "../lib/useAsyncData";
import {
  getNaverDashboardOverview, fetchNaverChangeLog, fetchNaverCampaignSettings,
  fetchNaverRetroScorecard, fetchNaverAdProposals,
  type NaverDashboardOverview, type NaverAdCampaignSettings,
  type NaverRetroScorecard, type NaverChangeLogRow, type NaverAdProposal,
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
                <Stat
                  label="광고비"
                  value={won(cov.ours_cost)}
                  isEmpty={cov.ours_cost === 0}
                  reason="아직 우리 MOP에 넘긴 캠페인이 없습니다."
                  tone={cov.ours_cost === 0 ? "idle" : "neutral"}
                  sub={cov.total_cost ? `전체의 ${pctFromFraction(cov.ours_cost / cov.total_cost)}` : undefined}
                />
              </div>
            </div>
            <div className="rounded border border-gray-200 p-3">
              <Badge>원본 MOP</Badge>
              <div className="mt-2">
                <Stat
                  label="광고비"
                  value={won(cov.mop_cost)}
                  isEmpty={cov.mop_cost === 0}
                  // ★D-47-g: 03을 optimizer='mop'으로 태깅해야 이 열이 채워진다.
                  reason="원본 MOP가 돌리는 캠페인이 optimizer='mop'으로 태깅되지 않았습니다(D-47-g)."
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
        title="우리 MOP가 돌리는 캠페인"
        right={<Link to="/naver-ad/console" className="text-xs text-blue-600 hover:underline">캠페인 넘기기 →</Link>}
      >
        <OursCampaignList />
      </Card>

      {/* ③ 성적표 두 겹 — 중복 아니라 상보 */}
      <div className="grid grid-cols-2 gap-4">
        <Card title="우리 조언이 맞았나 (방향 정밀도)">
          <RetroScorecardPane />
        </Card>
        <Card title="우리가 한 일의 결과 (인과)">
          <ChangeLogPane />
        </Card>
      </div>

      {/* ④ 나를 기다리는 것 */}
      <Card title="나를 기다리는 것">
        <PendingPane />
      </Card>
    </div>
  );
}

function OursCampaignList() {
  const [rows, setRows] = useState<NaverAdCampaignSettings[] | null>(null);
  // ★캠페인별 조작 횟수(codex[P2] R2·R3). 계정 전체 합계를 모든 행에 똑같이 찍으면 캠페인이
  //   늘었을 때 A의 조작이 B 행에 표시된다 — **D-47-c(N=1→N=여럿 동일 컴포넌트) 정면 위반**.
  //   오늘 04 하나뿐이라 우연히 맞아 보일 뿐, 카나리를 확대하는 순간 틀린다(그 확대가 이
  //   화면의 존재 이유다).
  //   ★행을 받아 클라이언트에서 묶지 않는다(codex[P2] R3): 한 페이지(500건)만 받아 세면
  //   그 창을 넘는 순간 **조용히 적게 센다**(옛 실집행만 있는 캠페인이 "0회"로 보인다).
  //   캠페인당 limit=1로 서버의 `total`을 읽으면 페이지네이션과 무관하게 **정확**하다.
  //   요청 수는 ours 캠페인 수(설계상 카나리라 소수)라 감당된다.
  //   ★실패 시 {}로 만들지 않는다(codex[P2] R3): 모르는 걸 "0회"라고 단언하면 D-47-h의
  //   0-vs-불명 계약을 깬다. null로 두어 "—"가 뜨게 한다.
  const [countByCampaign, setCountByCampaign] = useState<Record<string, number> | null>(null);
  const [countError, setCountError] = useState(false);
  const [listError, setListError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      let ours: NaverAdCampaignSettings[];
      try {
        const r = await fetchNaverCampaignSettings();
        ours = r.rows.filter((c) => c.optimizer === "ours");
      } catch (e) {
        // ★실패를 빈 목록으로 위장하지 않는다 — setRows([])로 두면 화면이 "우리 MOP에 넘긴
        //   캠페인이 아직 없습니다"라고 **단언**한다. 넘긴 캠페인이 있는데 조회만 실패했을
        //   수도 있다. 모르면 모른다고 한다(D-47-h · useAsyncData 헤더의 그 실수와 동일 계열).
        if (alive) setListError(e instanceof Error ? e.message : String(e));
        return;
      }
      if (!alive) return;
      setRows(ours);

      try {
        const counts = await Promise.all(
          ours.map((c) =>
            fetchNaverChangeLog({ days: 30, limit: 1, actor: "ours", campaign_id: c.campaign_id })
              .then((r) => [c.campaign_id, r.total] as const),
          ),
        );
        if (!alive) return;
        setCountByCampaign(Object.fromEntries(counts));
      } catch {
        if (alive) setCountError(true);  // 불명 유지 — 0이라고 단언하지 않는다
      }
    })();
    return () => { alive = false; };
  }, []);

  if (listError) {
    return <EmptyState reason={`캠페인 목록을 불러오지 못했습니다: ${listError}`} hint="새로고침하거나 백엔드 상태를 확인하세요." />;
  }
  if (rows === null) return <Loading rows={2} />;
  if (rows.length === 0) {
    return (
      <EmptyState
        reason="우리 MOP에 넘긴 캠페인이 아직 없습니다."
        hint="최적화 콘솔에서 캠페인의 관리 주체를 '우리'로 바꾸면 여기에 나타납니다."
      />
    );
  }

  return (
    <Table head={<>
      <Th>캠페인</Th>
      <Th right>우리 조작</Th>
      <Th>상태</Th>
    </>}>
      {rows.map((c) => {
        const changeCount = countByCampaign === null ? null : (countByCampaign[c.campaign_id] ?? 0);
        return (
        <tr key={c.campaign_id}>
          {/* ★campaign_name은 백엔드 /campaign-settings가 주지 않는다(_serialize_settings 실측) —
              추측으로 필드를 만들지 않고 campaign_id만 표시한다. */}
          <Td><span className="text-gray-900">{c.campaign_id}</span></Td>
          {/* ★"우리 조작 N회" — 프로그램이 일하는지 매일 보이는 자리.
              0은 회색(judge-idle)이다. 빨강이면 고장난 것처럼 보이고 초록이면 거짓말이다. */}
          <Td right>
            <span className={changeCount === 0 ? "text-judge-idle" : "text-gray-900"}>
              {changeCount == null ? NO_DATA : `${num(changeCount)}회`}
            </span>
          </Td>
          <Td>
            {/* ★0-vs-불명을 가른다(D-47-h): 0은 "0회"+이유(회색), **모르면 이유를 지어내지
                않는다**. 조회 실패를 "승인된 실행이 없습니다"라고 쓰면 사실이 아닌 걸 단언하는
                것이다(codex[P2] R3). */}
            {changeCount == null ? (
              <span className="text-xs text-gray-400">
                {countError ? "조작 횟수를 불러오지 못했습니다." : "확인 중…"}
              </span>
            ) : changeCount === 0 ? (
              <span className="text-xs text-gray-500">
                제안은 나오지만 승인된 실행이 없습니다(사람 승인 게이트 대기).
              </span>
            ) : <Badge tone="owner">가동 중</Badge>}
          </Td>
        </tr>
        );
      })}
    </Table>
  );
}

// D-NAO-45(상설 소급 채점) 라이브 — 실행이 0이어도 이건 채워진다. "우리 조작 0회"만
// 있으면 초라하지만, 그 옆에 방향 정밀도가 붙으면 신뢰의 근거가 된다.
// 정직 경계(D-NAO-45 docstring): "방향 정확도 계기판이지 인과 성과 검증이 아니다 —
// 인과 승격은 카나리 몫". 그 카나리가 바로 이 화면의 1층이다.
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
    default:
      // 알 수 없는 액션은 지어내지 않는다 — action 원문을 그대로 보여준다.
      return row.action;
  }
}

function ChangeLogPane() {
  // ★이 패널은 "우리가 한 일의 결과"(인과)다 — 외부가 바꾼 걸 감지한 행이 섞이면
  //   남의 조작을 우리 성과로 보여주게 된다(codex[P2] 2026-07-17). 외부 변경은
  //   별도 관심사(3열 대조의 MOP 열·이상 피드)라 여기 섞지 않는다.
  const { data, error } = useAsyncData(() => fetchNaverChangeLog({ days: 30, limit: 10, actor: "ours" }), []);
  if (error) return <EmptyState reason={`불러오지 못했습니다: ${error}`} hint="새로고침하거나 백엔드 상태를 확인하세요." />;
  if (data === null) return <Loading rows={3} />;
  if (data.rows.length === 0) {
    return (
      <EmptyState
        reason="최근 30일 우리가 집행한 변경이 없습니다."
        hint="제안은 생성되지만 사람 승인 게이트를 통과한 실행이 아직 없습니다. 승인하면 여기에 '무엇을 왜 바꿨는지'가 쌓입니다."
      />
    );
  }
  return (
    <Table head={<><Th>시각</Th><Th>대상</Th><Th>변경</Th><Th>근거</Th></>}>
      {data.rows.map((r) => (
        <tr key={r.id}>
          <Td><span className="text-xs text-gray-500">{r.changed_at?.slice(5, 16) ?? NO_DATA}</span></Td>
          <Td><span className="text-xs">{r.entity_type} {r.entity_id}</span></Td>
          {/* ★"무엇을 왜 바꿨는지" — MOP에 0개인 컬럼(ref24). 우리가 이길 자리이므로
              지원하는 실행 액션 4종을 전부 제대로 그린다(codex[P2] R3). */}
          <Td>
            <span className="text-xs tabular-nums">{describeChange(r)}</span>
          </Td>
          <Td><span className="text-xs text-gray-600">{r.rationale ?? NO_DATA}</span></Td>
        </tr>
      ))}
    </Table>
  );
}

function PendingPane() {
  const { data: rows, error } = useAsyncData<NaverAdProposal[]>(
    () => fetchNaverAdProposals({ status: "pending", limit: 100 }).then((r) => r.rows),
    [],
  );
  if (error) return <EmptyState reason={`불러오지 못했습니다: ${error}`} hint="새로고침하거나 백엔드 상태를 확인하세요." />;
  if (rows === null) return <Loading rows={3} />;

  // ★백엔드가 준 informational 플래그로 가른다 — 프론트에서 유형 문자열을 하드코딩해
  //   재분류하면 백엔드에 유형이 추가될 때 조용히 드리프트한다.
  const actionable = rows.filter((p) => !p.informational);
  const informational = rows.filter((p) => p.informational);

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
      {informational.length > 0 && (
        <p className="px-4 py-2 text-xs text-gray-500 border-t border-gray-100">
          정보성 경보 {num(informational.length)}건 집계됨(개별 나열 안 함) —{" "}
          <Link to="/naver-ad/console" className="text-blue-600 hover:underline">콘솔에서 보기</Link>
        </p>
      )}
    </div>
  );
}
