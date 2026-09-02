// NaverAdScope.tsx — 「PAO 스코프」. 어떤 캠페인·광고그룹을 엔진에 맡길지 + 그 성과 (D-NAO-244).
//
// Jino 원문 2026-08-24: *"ohisell에 PAO 메뉴를 만들어서 어떤 캠페인 - 광고그룹 을 돌릴지,
// 그 성과는 어떻게 나오는지 보여주는 대시보드를 같이 만들자"*
//
// ★이 화면이 지키는 정직 규칙 셋 (D-47-h "0이면 0이라고 말하라, 모르면 0이라 말하지 마라"):
//   ①총이익이 null이면 «모름»으로 그린다 — 0원으로 그리면 적자 그룹이 손익분기로 보인다.
//   ②스코프를 지정해도 캠페인이 꺼져 있으면 **아무것도 실행되지 않는다** — 그 사실을 상단에
//     크게 말한다. 「맡겼다」와 「돌고 있다」를 화면이 뭉치면 n=45의 사고가 화면에서 재발한다.
//   ③스코프 «해제»와 «끄기»는 결과가 정반대다(해제=전 그룹 복귀 / 끄기=그 그룹만 제외).
//     확인창이 그 차이를 말한다.
// ★H1(계약 P2, 2026-08-31): **이 화면에 엔진 스위치가 생겼다.** 종전 이 자리엔 「엔진을 켜는
//   버튼은 없다(auto_operate는 별도 결정)」라고 적혀 있었고, 실제로 켜는 API가 저장소에 없어
//   점화가 prod DB 직접 UPDATE였다. 그래서 ①감사 행이 앱 코드 밖에서 생기고 ②끄는 손이 사람에게
//   없어 제외 재개방이 10일째 밀려 있었다(2026-08-31 실측: due 1건).
//   ⚠️★이 스위치는 optimizer 스위치보다 **무겁다** — 제외 재개방 레인은 실행 harness를 안 타서
//   `optimizer='none'`이라도 켜면 네이버 실쓰기가 나간다. 그래서 켜기 전 preflight를 «보여준 뒤»
//   누르게 한다(차단이 아니라 고지 — 켜는 결정은 사람의 것이다).
import { useState } from "react";
import {
  Card, Table, Th, Td, Badge, Loading, EmptyState, LayerNav,
} from "../components/ui";
import { useAsyncData } from "../lib/useAsyncData";
import { num } from "../lib/format";
import {
  fetchPaoScopeRoster, putPaoScopeAdgroup, deletePaoScopeAdgroup,
  putNaverCampaignAutoOperate, fetchNaverCampaignIgnitionPreflight,
  fetchNaverSearchTermExclusions, reopenNaverSearchTermExclusion,
  putPaoScopeCampaignBulk, voidNaverSearchTermExecution,
  type PaoScopeCampaign, type PaoScopeAdgroup, type PaoScopeRole,
  type PaoScopeDayClassSplit, type NaverAdIgnitionPreflight,
  type NaverSearchTermExclusionRow,
} from "../lib/api";

const ROLE_LABEL: Record<PaoScopeRole, string> = {
  accel: "액셀",
  boundary: "경계",
  brake: "브레이크",
};
const ROLE_HINT: Record<PaoScopeRole, string> = {
  accel: "고ROAS·저지출 — 올릴 자리",
  boundary: "BEP 부근 — ROAS는 내려도 총이익이 느는 구간",
  brake: "출혈 — 내리거나 제외할 자리",
};

/** 보정 적용값이 «있는 그대로»를 감싸는가 — 아니면 화면이 그 사실을 말해야 한다. */
function raw_outside(raw: number, low: number, high: number): boolean {
  return raw < Math.min(low, high) || raw > Math.max(low, high);
}

/** 총이익 셀 — ★«있는 그대로» + 구간 병기 (Jino 지시 2026-08-24).
 *
 *  ①null은 «0원»이 아니라 «모름»이다.
 *  ②큰 글씨는 **보정 없는 값**이다 — 네이버가 준 전환매출 그대로.
 *  ③작은 글씨의 [하한 ~ 상한]이 «얼마나 모르는지»다. 보정계수는 「채널 매출 100%가 광고
 *    공」이라는 가정에 기대므로 단일값으로 보이면 그 가정이 사실처럼 읽힌다.
 */
function ProfitCell({
  value, low, high, status,
}: { value: number | null; low?: number | null; high?: number | null; status: string }) {
  if (value === null) {
    // ★D-NAO-267 (계약 §4-C S2-④): 램프업은 «모름»과 다른 사유다. 둘 다 값이 없지만
    //   bep_unknown은 「우리가 못 잰다」이고 ramp_up은 「이 그룹엔 아직 잴 체질이 없다」다.
    //   같은 「모름」으로 그리면 신규 그룹의 초기 잡음이 상품 원가 미연결과 한 칸에 뭉개진다.
    if (status === "ramp_up") {
      return (
        <span
          className="text-sky-600"
          title="램프업 — 창 안에 평시(평일·비공휴일) 관측이 0일입니다. 평시 체질이 없어 밴드 확정값을 내지 않습니다(ref 63 §10 교란축 X9)."
        >
          램프업
        </span>
      );
    }
    return (
      <span className="text-gray-400" title={status === "bep_unknown" ? "BEP를 해석하지 못했습니다(상품 원가 미연결)" : status}>
        모름
      </span>
    );
  }
  const hasBand = low !== null && low !== undefined && high !== null && high !== undefined;
  // ★적대 리뷰 P2-7 채택 — 「구간」이 「있는 그대로」를 «감싸지 않는» 경우가 드물지 않다.
  //   보정계수 구간은 low=min(floor, point)·high=max(floor, point)인데 floor=0.827이고 점추정도
  //   1 미만일 수 있어(실측 스프레드 0.8289~0.8862) high<1 → high<raw가 된다. 그때 큰 글씨가
  //   자기 괄호 밖에 놓여 「구간이 감싼다」는 잘못된 직관을 준다 — 그 사실을 명시적으로 말한다.
  const outside = hasBand && (raw_outside(value, low!, high!));
  const band = hasBand ? (
    <span
      className={`block text-[10px] leading-tight ${outside ? "text-amber-600" : "text-gray-400"}`}
      title={
        outside
          ? "⚠️ 보정 적용값이 «있는 그대로»의 한쪽에만 있습니다 — 구간이 위 숫자를 감싸지 않습니다(보정계수 양끝이 둘 다 1보다 작거나 큰 경우)."
          : "보정 적용 범위. 하한=유입경로 라벨 근거 · 상한=채널 매출 전액을 광고 공으로 돌린 가정"
      }
    >
      {outside ? "⚠ " : ""}{num(low!)} ~ {num(high!)}
    </span>
  ) : null;
  return (
    <span>
      <span className={value < 0 ? "text-red-600" : "text-emerald-700"}>{num(value)}</span>
      {band}
    </span>
  );
}

export default function NaverAdScope() {
  const [days, setDays] = useState(21);
  const [reloadKey, setReloadKey] = useState(0);
  const { data, error } = useAsyncData(() => fetchPaoScopeRoster({ days }), [days, reloadKey]);

  return (
    <div className="space-y-4">
      <LayerNav />
      <Card
        title="PAO 스코프 — 무엇을 엔진에 맡길까"
        right={
          <div className="flex items-center gap-1">
            {[7, 21, 51].map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setDays(d)}
                className={`px-2 py-0.5 text-xs rounded-full ${
                  days === d ? "bg-blue-50 text-blue-700 font-semibold" : "text-gray-600 hover:bg-gray-100"
                }`}
              >
                {d}일
              </button>
            ))}
          </div>
        }
      >
        {error ? (
          <EmptyState reason={`불러오지 못했습니다: ${error}`} hint="새로고침하거나 서버 로그를 확인하세요." />
        ) : data === null ? (
          <Loading />
        ) : data.campaigns.length === 0 ? (
          <EmptyState
            reason="이 창에 집행된 광고가 없습니다."
            hint="기간을 넓히거나 수집 상태를 확인하세요."
          />
        ) : (
          <>
            <EngineStateNotice campaigns={data.campaigns} />
            <div className="px-4 pb-2 text-xs text-gray-500">
              창 {data.window.date_from} ~ {data.window.date_to} ({data.window.days}일, 오늘 제외)
              {" · "}
              <span title="큰 숫자는 보정 없는 «있는 그대로»입니다. 작은 [a ~ b]는 보정계수 구간 양끝을 적용한 값입니다.">
                총이익 = <b>있는 그대로</b> + 구간[×{data.correction_factor.low.toFixed(3)} ~ ×
                {data.correction_factor.high.toFixed(3)}]
              </span>
              <span className="text-gray-400">
                {" "}({data.correction_factor.source ?? "출처 미상"})
              </span>
            </div>
            <DayClassStrip split={data.weekend_holiday} />
            <div className="divide-y divide-gray-100">
              {data.campaigns.map((c) => (
                <CampaignBlock key={c.campaign_id} c={c} onChanged={() => setReloadKey((k) => k + 1)} />
              ))}
            </div>
          </>
        )}
      </Card>
    </div>
  );
}

/** ★평시/주말/공휴일 분리 표기 (D-NAO-267 · ref 65 S2-ⓐ).
 *
 *  ref 63 §4-1이 확정한 것: 주말 −8,020,470원 · 공휴일 −915,912원(둘 다 홀드아웃 통과).
 *  이 날들을 평시와 섞어서 재면 **평시 성과가 확정된 음의 효과에 눌려 과소평가된다.**
 *
 *  ★보정이 아니라 «분리»다 — 빼거나 곱하지 않는다. 세 칸을 나눠 놓기만 하고, 얼마나
 *    다른지는 보는 사람이 읽는다. 계수를 곱하는 순간 그 숫자는 「모형이 만든 값」이 된다.
 *  ★칸별 총이익은 안 낸다(BEP가 그룹마다 다르고 날짜 grain엔 그 조인이 없다) — 대신
 *    ROAS를 낸다. 지어내느니 안 낸다.
 */
function DayClassStrip({ split }: { split: PaoScopeDayClassSplit }) {
  const CELLS: { key: "weekday" | "weekend" | "holiday"; label: string }[] = [
    { key: "weekday", label: "평시" },
    { key: "weekend", label: "주말" },
    { key: "holiday", label: "공휴일" },
  ];
  return (
    <div className="px-4 pb-3">
      <div className="flex items-center gap-2 text-[11px] text-gray-500 mb-1">
        <span title={split.reference}>평시·주말·공휴일 분리</span>
        <span className="text-gray-400" title={split.basis}>({split.basis})</span>
        {/* ★검산이 깨지면 숨기지 않고 말한다 — 한 날짜가 두 칸에 들어갔다는 뜻이다 */}
        {!split.identity.ok && (
          <span className="text-red-600" title={split.identity.note}>
            ⚠️ 항등식 불일치 — 세 칸의 합이 전체와 다릅니다
          </span>
        )}
      </div>
      <div className="grid grid-cols-3 gap-2">
        {CELLS.map(({ key, label }) => {
          const c = split[key];
          return (
            <div key={key} className="border border-gray-200 rounded px-2 py-1.5">
              <div className="text-xs text-gray-600">
                {label} <span className="text-gray-400">{c.days}일</span>
              </div>
              <div className="text-xs tabular-nums text-gray-900">{num(c.cost)}원</div>
              <div className="text-[11px] tabular-nums text-gray-500">
                ROAS{" "}
                {c.roas === null ? (
                  <span className="text-gray-400" title="이 칸에 집행이 없습니다 — 0이 아니라 «없음»입니다">—</span>
                ) : (
                  c.roas.toFixed(2)
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** ★「맡겼다」 ≠ 「돌고 있다」 — 스코프가 있는데 엔진이 꺼져 있으면 크게 말한다. */
function EngineStateNotice({ campaigns }: { campaigns: PaoScopeCampaign[] }) {
  const scoped = campaigns.filter((c) => c.has_scope);
  if (scoped.length === 0) return null;
  const running = scoped.filter((c) => c.auto_operate && c.optimizer === "ours");
  if (running.length === scoped.length) return null;
  const stopped = scoped.filter((c) => !(c.auto_operate && c.optimizer === "ours"));
  return (
    <div className="mx-4 mt-3 mb-1 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">
      <b>스코프는 지정돼 있지만 엔진은 이 캠페인에서 돌지 않습니다</b> —{" "}
      {stopped.map((c) => c.name).join(", ")}. 스코프는 캠페인 스위치{" "}
      <span className="font-mono">auto_operate</span> «아래»의 축이라, 캠페인이 꺼져 있으면 실행은 0입니다.
      {/* ★H1: 종전 문구는 「켜는 것은 이 화면이 아니라 별도 결정입니다」였다. 이제 이 화면에
          스위치가 있으므로 그 문장은 거짓이 됐다 — 화면이 자기 기능을 부정하면 사람은 버튼을
          보고도 안 누른다. 「어디서 켜는가」를 실제 자리로 바꾼다. */}
      {" "}각 캠페인 줄의 <b>엔진 스위치</b>로 켜고 끕니다.
    </div>
  );
}

/** 킬스위치 토글 (H1 · 계약 P2) — 이 저장소 최초의 `auto_operate` 쓰기 손.
 *
 *  ⚠️★**끄기는 즉시, 켜기는 고지 후.** 비대칭인 이유: 끄는 것은 언제나 안전 방향이라 마찰을
 *  두면 급할 때 못 끈다(킬스위치의 존재 이유). 켜는 것은 그 순간 예약된 네이버 실쓰기를
 *  풀 수 있으므로 preflight를 «보여준 뒤» 한 번 더 누르게 한다.
 *  ★차단이 아니다 — 경고가 있어도 누를 수 있다(전역 §1: 새 게이트를 세우지 않는다). */
function EngineSwitch({ c, onChanged }: { c: PaoScopeCampaign; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [preflight, setPreflight] = useState<NaverAdIgnitionPreflight | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function apply(next: boolean) {
    setBusy(true);
    setErr(null);
    try {
      const res = await putNaverCampaignAutoOperate({ campaignId: c.campaign_id, autoOperate: next });
      // ★켜는 요청에만 preflight가 실린다. 경고가 있으면 «누른 뒤에도» 계속 보여준다 —
      //   끄기 전까지 그 캠페인이 무엇을 열어 뒀는지가 화면에 남아 있어야 한다.
      setPreflight(next ? (res.ignition_preflight ?? null) : null);
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  // 켜기 확인 단계: preflight를 먼저 받아 보여주고, 그 화면에서 한 번 더 눌러야 실제로 켜진다.
  const [confirming, setConfirming] = useState(false);

  return (
    /* ★`stopPropagation`을 쓰지 않는다 — 이 스위치는 접기 버튼의 «형제»이지 자식이 아니다
       (CampaignBlock 주석 참조). 전파를 막아야 한다면 그건 구조가 틀렸다는 신호다. */
    <span className="inline-flex flex-col items-end gap-1">
      <button
        type="button"
        disabled={busy}
        onClick={() => (c.auto_operate ? apply(false) : setConfirming(true))}
        className={
          "rounded px-2 py-0.5 text-xs font-medium border disabled:opacity-50 " +
          (c.auto_operate
            ? "border-red-300 text-red-700 hover:bg-red-50"
            : "border-emerald-300 text-emerald-700 hover:bg-emerald-50")
        }
        title={c.auto_operate
          ? "엔진을 끕니다 — 이 캠페인의 자동 조치·제외 재개방이 즉시 멈춥니다"
          : "엔진을 켭니다 — 무엇이 열리는지 먼저 보여드립니다"}
      >
        {busy ? "…" : c.auto_operate ? "엔진 끄기" : "엔진 켜기"}
      </button>

      {confirming && !c.auto_operate && (
        <IgnitionConfirm
          campaignId={c.campaign_id}
          onCancel={() => setConfirming(false)}
          onConfirm={() => { setConfirming(false); void apply(true); }}
        />
      )}
      {err && <span className="text-[11px] text-red-600">{err}</span>}
      {preflight && preflight.warnings.length > 0 && (
        <span className="text-[11px] text-amber-700">
          켜짐 — 경고 {preflight.warnings.length}건
        </span>
      )}
    </span>
  );
}

/** 켜기 확인창 — preflight를 «먼저 보여주고» 그 위에서 한 번 더 누르게 한다.
 *
 *  ★경고 0건이어도 이 창을 띄운다(교훈 #123 — 「검사를 안 했다」와 「검사했는데 깨끗하다」가
 *  같아 보이면 안 된다). `safe_to_ignite`는 «경고가 없다»는 뜻이지 «켜도 좋다»는 승인이 아니다.
 *  ★차단하지 않는다 — 경고가 몇 건이든 「그래도 켠다」를 누를 수 있다. */
function IgnitionConfirm({
  campaignId, onCancel, onConfirm,
}: { campaignId: string; onCancel: () => void; onConfirm: () => void }) {
  const { data, error } = useAsyncData(
    () => fetchNaverCampaignIgnitionPreflight(campaignId),
    [campaignId],
  );
  // ★이 훅은 일부러 `loading`을 안 준다 — 3상태(로딩/실패/데이터)를 «구조적으로» 가르게 하려고
  //   그렇게 만들어졌다(모듈 머리주석). 그러니 여기서도 셋을 따로 그린다: 실패를 «경고 0건»으로
  //   위장하지 않는 것이 이 확인창의 존재 이유다.
  const loading = data === null && error === null;
  return (
    <div className="w-96 rounded-md border border-amber-300 bg-amber-50 p-3 text-left text-xs text-amber-900 shadow-sm">
      <b className="block mb-1">엔진을 켜면 무엇이 열리는가</b>
      {loading && <span className="text-gray-600">검사 중…</span>}
      {/* ★검사가 실패하면 «깨끗하다»고 그리지 않는다 — 실패를 실패로 말하고 켜기는 계속 허용한다. */}
      {error && <span className="text-red-700">선행 검사 실패: {String(error)} — 검사 결과 없이 켭니다.</span>}
      {data && data.warnings.length === 0 && (
        <span className="text-emerald-800">검사했고 경고 0건입니다(«안 했다»가 아니라 «깨끗하다»).</span>
      )}
      {data && data.warnings.length > 0 && (
        <ul className="list-disc pl-4 space-y-1">
          {data.warnings.map((w) => (
            <li key={w.code}>{w.message}</li>
          ))}
        </ul>
      )}
      <div className="mt-2 flex gap-2 justify-end">
        <button type="button" onClick={onCancel}
          className="rounded border border-gray-300 bg-white px-2 py-0.5 hover:bg-gray-50">
          취소
        </button>
        <button type="button" onClick={onConfirm} disabled={loading}
          className="rounded border border-emerald-400 bg-emerald-600 px-2 py-0.5 text-white hover:bg-emerald-700 disabled:opacity-50">
          그래도 켠다
        </button>
      </div>
    </div>
  );
}

function CampaignBlock({ c, onChanged }: { c: PaoScopeCampaign; onChanged: () => void }) {
  const [open, setOpen] = useState(c.has_scope);
  return (
    <div>
      {/* ★H1: 종전엔 이 행 «전체»가 하나의 `<button>`(접기/펴기)이었다. 스위치를 그 안에 넣으면
          `<button>` 안의 `<button>`이라 **유효하지 않은 HTML**이고, jsdom은 이 규칙을 강제하지
          않아 테스트는 초록인 채로 통과한다(내 초판이 실제로 그랬다). 그래서 행을 flex 컨테이너로
          바꾸고 접기 버튼과 스위치를 **형제**로 둔다 — `stopPropagation`으로 덮는 것은 증상 처치다. */}
      <div className="w-full flex items-center gap-3 px-4 py-3 hover:bg-gray-50">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-3 flex-1 min-w-0 text-left"
        >
          <span className="text-gray-400 text-xs w-3">{open ? "▾" : "▸"}</span>
          <span className="text-sm font-medium text-gray-900 flex-1 min-w-0 truncate">{c.name}</span>
          {c.has_scope ? (
            <Badge tone="owner">스코프 {c.scoped_count}/{c.adgroup_count}</Badge>
          ) : (
            <Badge tone="neutral">전 그룹</Badge>
          )}
          <Badge tone={c.auto_operate && c.optimizer === "ours" ? "good" : "neutral"}>
            {c.optimizer === "ours" ? (c.auto_operate ? "PAO 가동" : "PAO 정지") : c.optimizer === "mop" ? "제3자(대행사)" : "수동"}
          </Badge>
          {/* ★D-NAO-267: 램프업 그룹은 총이익 합산에서 빠진다 — 몇 개가 빠졌는지 «여기서»
              말하지 않으면 옆의 총이익이 「그냥 그만큼인 값」으로 읽힌다. */}
          {c.ramp_up_count > 0 && (
            <Badge tone="neutral">램프업 {c.ramp_up_count} 제외</Badge>
          )}
          <span className="text-xs text-gray-500 tabular-nums w-24 text-right">{num(c.cost)}원</span>
          <span className="text-xs tabular-nums w-24 text-right">
            <ProfitCell value={c.gross_profit} low={c.gross_profit_low} high={c.gross_profit_high} status="ok" />
          </span>
        </button>
        {/* ★배지 «옆»에 둔다 — 배지는 상태를 말하고 스위치는 그 상태를 바꾼다. 읽는 자리와
            누르는 자리가 떨어져 있으면 누른 뒤 무엇이 바뀌었는지 확인이 갈라진다. */}
        <EngineSwitch c={c} onChanged={onChanged} />
      </div>
      {open && (
        <div className="pb-3">
          {c.has_scope && (
            <p className="px-4 pb-2 text-xs text-gray-500">
              ★이 캠페인은 «일부 그룹만» 맡긴 상태라 <b>캠페인 예산 조정은 엔진이 하지 않습니다</b> —
              예산은 광고그룹으로 나눌 수 없어서, 열어두면 스코프 밖 그룹의 노출까지 같이 움직입니다.
            </p>
          )}
          <AdgroupTable c={c} onChanged={onChanged} />
          <ReopenPanel campaignId={c.campaign_id} />
        </div>
      )}
    </div>
  );
}

/** 제외 재개방 «손»(계약 P2 넷째). 이 캠페인이 우리가 걸어 둔 제외 중 **지금 열 수 있는/못 여는**
 *  것을 보여 주고, 각 행에 해제 버튼을 준다.
 *
 *  ★**왜 여기 있나**: 재개방의 유형별 dispatch는 D-NAO-271로 이미 구현돼 있었지만 **자동 레인
 *    안에서만** 돌았다. 레인은 `auto_operate=1`인 캠페인만 훑으므로, 스위치가 꺼진 캠페인의 제외는
 *    재심사일이 지나도 아무도 못 열었다 — 2026-08-31 실측: due 1건이 **10일째** 밀려 있었다.
 *    기능은 있는데 «손»이 없던 자리다. 그래서 손을 스위치 바로 아래에 둔다(켜는 자리와 여는
 *    자리가 붙어 있어야 「켠 뒤 재개방」이라는 사유가 행동으로 이어진다).
 *
 *  ★**전체 제외 드릴다운(요약·페이징·4종 GET 렌더)은 여기 만들지 않는다** — 그건 계약 P3의
 *    체크박스다. 여기 있는 것은 P2의 손이 서 있을 만큼의 표면뿐이다. */
function ReopenPanel({ campaignId }: { campaignId: string }) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  // ★`useAsyncData`를 쓴다(이 페이지의 관례). 손으로 `catch → setRows([])`를 쓰면 **조회 실패가
  //   「제외 0건」으로 렌더된다** — 그 훅의 머리주석이 정확히 그 병을 막으려고 만들어졌고,
  //   내 초판이 그 실수를 그대로 반복했다(「없다」와 「못 읽었다」는 다른 사실이다).
  // ★`console_import`(대행사·수동 편입분)는 애초에 재개방 대상이 아니다 — 계약 §5 금지선.
  //   ★★거르는 «자리»가 결함이었다(적대 리뷰 1R P1-1): 화면에서 거르면 `limit` 뒤라 페이지가
  //   편입분으로 차서 정작 열 수 있는 due 행이 응답에 아예 안 온다. 그래서 SQL로 내렸다.
  const { data, error } = useAsyncData(
    () => fetchNaverSearchTermExclusions({
      campaignId, status: "excluded", limit: 50, excludeConsoleImport: true,
    }),
    [campaignId, reloadKey],
  );
  // 화면 필터는 이중 방어로 남긴다 — 서버가 파라미터를 무시해도 손이 잘못 열리지 않는다.
  const rows = data?.rows.filter((r) => r.source !== "console_import") ?? null;

  async function reopen(row: NaverSearchTermExclusionRow) {
    setBusyId(row.id);
    setMsg(null);
    try {
      const res = await reopenNaverSearchTermExclusion(row.id);
      // ★막힌 것도 «정상 응답»이다 — 사유를 그대로 보여 준다. 조용히 아무 일도 안 일어나면
      //   사람은 버튼이 고장 났다고 읽는다.
      setMsg(res.ok ? `열었습니다 — 「${row.search_term}」 관찰 시작(${res.probation_until}까지)` : res.reason);
      setReloadKey((k) => k + 1);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "재개방 실패");
    } finally {
      setBusyId(null);
    }
  }

  if (error) {
    return <p className="px-4 pt-3 text-xs text-amber-700">제외 목록을 읽지 못했습니다 — {error}</p>;
  }
  if (rows === null) return null;
  if (rows.length === 0) {
    return (
      <p className="px-4 pt-3 text-xs text-gray-500">
        우리가 건 검색어 제외가 없습니다 (대행사·콘솔 편입분은 재개방 대상이 아닙니다).
      </p>
    );
  }
  return (
    <div className="px-4 pt-3">
      <div className="text-xs font-medium text-gray-700 mb-1">검색어 제외 재개방</div>
      {msg && <p className="text-xs text-gray-600 mb-1">{msg}</p>}
      <ul className="space-y-1">
        {rows.map((r) => (
          <li key={r.id} className="flex items-center gap-2 text-xs">
            <span className="font-medium text-gray-900">{r.search_term}</span>
            <span className="text-gray-500">재심사 {r.next_review_at ?? "미정"} · {r.cycle}회차</span>
            <button
              type="button"
              disabled={r.reopen_block_reason !== null || busyId === r.id}
              onClick={() => void reopen(r)}
              className="px-2 py-0.5 rounded border border-gray-300 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-gray-50"
            >
              {busyId === r.id ? "여는 중…" : "지금 재개방"}
            </button>
            {/* ★비활성 사유를 «항상» 옆에 적는다 — 회색 버튼만 있으면 「왜」가 사라진다.
                문장은 백엔드가 준 것을 그대로 쓴다(화면이 다시 쓰면 두 벌이 갈라진다). */}
            {r.reopen_block_reason && (
              <span className="text-gray-500">— {r.reopen_block_reason}</span>
            )}
            <VoidButton row={r} onDone={(m) => { setMsg(m); setReloadKey((k) => k + 1); }} />
          </li>
        ))}
      </ul>
    </div>
  );
}

/** 원장 무효화(void) — 계약 P2 「원장 무효화(void) 버튼」의 손.
 *
 *  ★**재개방과 다른 일이다.** 재개방은 계정에 걸린 제외를 «푸는» 네이버 실쓰기이고, 무효화는
 *    「이 행이 애초에 잘못 들어왔다」고 **우리 장부만** 고치는 것이다(네이버 쓰기 0). 둘을
 *    나란히 두되 말로 갈라 놓는다 — 섞이면 사람이 계정을 건드릴 생각으로 장부를 지운다.
 *
 *  ★사유가 **필수**다(백엔드가 강제한다). 왜 지웠는지 없는 삭제는 감사 불가라서다.
 *  ★★결과의 `wisdom_may_have_counted`는 **3상**이라 셋을 다르게 말한다 — 특히 `null`을
 *    「아니오」로 접으면 «확인 안 함»이 «안 셌음»으로 둔갑한다(교훈 #123). */
function VoidButton({
  row, onDone,
}: { row: NaverSearchTermExclusionRow; onDone: (msg: string) => void }) {
  const [busy, setBusy] = useState(false);

  async function run() {
    const reason = window.prompt(
      `「${row.search_term}」 원장 행을 무효화합니다.\n\n` +
        "· 네이버 계정은 건드리지 않습니다(우리 장부만 고칩니다).\n" +
        "· 행은 감사용으로 남고, 성적표·생존 감시·학습 사슬에서 빠집니다.\n\n" +
        "사유를 적어 주세요(필수):",
      "",
    );
    if (reason === null) return;               // 취소
    if (!reason.trim()) {                      // 빈 사유는 보내지 않는다(백엔드도 422로 막는다)
      onDone("사유가 비어 무효화하지 않았습니다.");
      return;
    }
    setBusy(true);
    try {
      const res = await voidNaverSearchTermExecution(row.id, reason.trim());
      const wisdom =
        res.wisdom_may_have_counted === true
          ? " ⚠️ 이미 학습에 셈이 들어갔을 수 있습니다(그건 되돌리지 못합니다)."
          : res.wisdom_may_have_counted === false
            ? " 학습에는 아직 안 들어갔습니다."
            : " ⚠️ 학습 반영 여부는 **확인하지 못했습니다**(«아니오»가 아닙니다).";
      const head = res.result === "already_void" ? "이미 무효화된 행입니다." : "무효화했습니다.";
      onDone(`${head} 일기 ${res.diary_voided}건 중화.${wisdom}`);
    } catch (e) {
      onDone(e instanceof Error ? `무효화 실패: ${e.message}` : "무효화 실패");
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      disabled={busy}
      onClick={() => void run()}
      className="px-2 py-0.5 rounded border border-gray-300 text-gray-600 hover:bg-red-50 hover:text-red-700 disabled:opacity-40"
      title="이 행이 잘못 들어왔을 때 — 우리 장부만 고칩니다(네이버 쓰기 없음)"
    >
      {busy ? "…" : "무효화"}
    </button>
  );
}

function AdgroupTable({ c, onChanged }: { c: PaoScopeCampaign; onChanged: () => void }) {
  if (c.adgroups.length === 0) {
    return <EmptyState reason="이 창에 집행된 광고그룹이 없습니다." />;
  }
  return (
    <>
    <BulkScopeBar campaignId={c.campaign_id} adgroups={c.adgroups}
                  hasScope={c.has_scope} adgroupCount={c.adgroup_count} onChanged={onChanged} />
    <Table
      head={
        // ★`<tr>`로 감싸지 않는다 — `Table`이 이미 `<thead><tr>{head}</tr></thead>`로 감싼다
        //   (`components/ui/Table.tsx:28`). 감싸면 `<tr><tr>…</tr></tr>` 중첩이 된다.
        //
        //   ★왜 깨지나 (적대 리뷰가 내 초판 설명을 정정했다 — 2026-08-29):
        //   HTML **파서**의 fixup이 아니다. React는 `createElement`+`appendChild`로 DOM을 만들지
        //   HTML 문자열 파서를 타지 않아서 그 경로는 애초에 안 걸린다(리뷰어 실측: 스크립트로 만든
        //   중첩 `<tr>`은 `outerHTML`에 **그대로 보존**된다). 진짜 원인은 **CSS 익명 테이블 박스
        //   생성 규칙**이다 — `table-row`의 자식이 `table-cell`이 아니면 익명 셀로 감싸이므로,
        //   안쪽 `<tr>`의 `<th>`들이 **본문과 같은 열 그리드에 참여하지 못한다.** 이 규칙은 DOM이
        //   파싱으로 만들어졌든 스크립트로 만들어졌든 동일하게 적용된다(리뷰어가 실제 Chromium
        //   렌더로 증상 재현·수정본 정렬 확인).
        //   증상: 헤더는 왼쪽에 몰리고 본문 숫자는 오른쪽으로 밀린다
        //   (2026-08-29 Jino 지적 「이거 칸 안맞잖아」). 호출부 39곳 중 여기만 어긋나 있었다.
        <>
          <Th>광고그룹</Th>
          <Th>맡김</Th>
          <Th>역할</Th>
          <Th right>광고비</Th>
          <Th right>클릭</Th>
          <Th right>전환매출</Th>
          <Th right>ROAS</Th>
          <Th right>BEP</Th>
          <Th right>총이익<span className="block text-[10px] font-normal text-gray-400">있는 그대로 / 구간</span></Th>
        </>
      }
    >
      {c.adgroups.map((g) => (
        <AdgroupRow key={g.adgroup_id} campaignId={c.campaign_id} g={g} onChanged={onChanged} />
      ))}
    </Table>
    </>
  );
}

/** H5 — 캠페인 단위 일괄 지정(계약 P2). 표 «위»에 둔다: 사람이 아래 목록을 보고 나서
 *  「이거 전부」라고 누르는 순서이기 때문이다.
 *
 *  ★**보이는 목록을 그대로 보낸다** — 「이 캠페인의 전부」라는 뜻으로 서버에 맡기지 않는다.
 *    그러면 화면의 창(기간 탭)이 거른 목록과 서버가 손댄 집합이 조용히 어긋난다.
 *  ★**엔진을 켜지 않는다** — 스코프는 `auto_operate` «아래»의 축이다. 그 사실을 버튼 옆에
 *    적어 둔다(누르는 사람이 「이걸로 돌기 시작한다」고 읽으면 안 된다). */
function BulkScopeBar({
  campaignId, adgroups, hasScope, adgroupCount, onChanged,
}: {
  campaignId: string; adgroups: PaoScopeAdgroup[]; hasScope: boolean;
  adgroupCount: number; onChanged: () => void;
}) {
  // ★기본이 **「역할 유지」**다(적대 리뷰 P1-1). 초판은 기본이 「역할 없음」이라 사람이
  //   역할 칸을 손대지 않고 「전부 끄기」만 눌러도 붙여 둔 역할이 N건 지워졌고, 확인
  //   문구는 「행은 남고 꺼지기만 합니다」라며 그 반대를 단언했다.
  const [role, setRole] = useState<PaoScopeRole | "__keep__" | "__clear__">("__keep__");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const ids = adgroups.map((g) => g.adgroup_id);
  // 창 밖 그룹 수 — 로스터는 「창 안 집행분 ∪ 기존 스코프 행」만 싣는다(적대 리뷰 P2-1).
  const outsideWindow = Math.max(0, adgroupCount - ids.length);

  async function apply(enabled: boolean) {
    const verb = enabled ? "맡깁니다" : "끕니다";
    const roleLine =
      role === "__keep__"
        ? "· 역할·메모는 그대로 둡니다(이 버튼은 맡김 여부만 바꿉니다).\n"
        : role === "__clear__"
          ? "⚠️ 역할을 «없음»으로 **지웁니다**(이 목록의 그룹 전부).\n"
          : `· 역할을 「${ROLE_LABEL[role]}」로 함께 바꿉니다.\n`;
    // ★창 밖 그룹 경고는 「스코프가 아직 없는 캠페인」에서만 뜻이 있다 — 첫 행이 생기는
    //   순간 진리표가 「목록에 없는 그룹 = OFF」로 바뀌기 때문이다(D-NAO-244).
    const outsideLine =
      enabled && !hasScope && outsideWindow > 0
        ? `\n⚠️⚠️ 이 캠페인은 아직 스코프가 없습니다. 지금 ${ids.length}개만 맡기면 ` +
          `**이 창에 안 보이는 ${outsideWindow}개는 «맡김 밖»으로 넘어갑니다**(원장에 줄도 남지 않습니다).\n`
        : "";
    const ok = window.confirm(
      `보이는 광고그룹 ${ids.length}개를 한 번에 ${verb}.\n\n` +
        roleLine +
        (enabled
          ? "⚠️ 이 캠페인이 「일부 그룹만 맡긴 상태」가 되면 캠페인 레벨 액션(예산)이 hold됩니다.\n"
          : "· 행은 남고 꺼지기만 합니다(스코프에서 빼는 «해제»와 결과가 다릅니다).\n") +
        outsideLine +
        "\n이 동작은 엔진을 켜지 않습니다 — 켜는 것은 별도 스위치입니다.",
    );
    if (!ok) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await putPaoScopeCampaignBulk({
        campaign_id: campaignId,
        adgroup_ids: ids,
        enabled,
        // ★「유지」면 키 자체를 **안 보낸다** — 백엔드가 «안 보냄»과 «null»을 가른다.
        ...(role === "__keep__" ? {} : { role: role === "__clear__" ? null : role }),
      });
      // ★`requested`가 아니라 `changed`를 말한다 — 이미 같은 값이던 행까지 「했다」고 세면
      //   화면의 숫자가 감사 원장의 줄 수와 어긋난다.
      setMsg(
        `${r.changed}건 바뀜` +
          (r.counts.unchanged > 0 ? ` · ${r.counts.unchanged}건은 이미 같은 값` : ""),
      );
      onChanged();
    } catch (e) {
      setMsg(e instanceof Error ? `실패: ${e.message}` : "실패");
    } finally {
      setBusy(false);
    }
  }

  if (ids.length === 0) return null;
  return (
    <div className="px-4 py-2 flex flex-wrap items-center gap-2 text-xs border-b border-gray-100">
      <span className="text-gray-600">보이는 {ids.length}개 일괄</span>
      <select
        className="text-xs border border-gray-200 rounded px-1 py-0.5"
        value={role}
        disabled={busy}
        onChange={(e) => setRole(e.target.value as PaoScopeRole | "__keep__" | "__clear__")}
        aria-label="일괄 역할"
      >
        {/* ★기본이 「유지」다 — 「없음」이 기본이면 역할 칸을 안 건드린 사람이 라벨을 지운다 */}
        <option value="__keep__">역할 유지</option>
        <option value="accel">액셀</option>
        <option value="boundary">경계</option>
        <option value="brake">브레이크</option>
        <option value="__clear__">역할 지우기</option>
      </select>
      <button
        type="button"
        disabled={busy}
        onClick={() => void apply(true)}
        className="px-2 py-0.5 rounded border border-gray-300 hover:bg-gray-50 disabled:opacity-40"
      >
        {busy ? "…" : "전부 맡김"}
      </button>
      <button
        type="button"
        disabled={busy}
        onClick={() => void apply(false)}
        className="px-2 py-0.5 rounded border border-gray-300 hover:bg-gray-50 disabled:opacity-40"
      >
        전부 끄기
      </button>
      <span className="text-gray-400">엔진을 켜지는 않습니다</span>
      {msg && <span className="text-gray-600">— {msg}</span>}
    </div>
  );
}

function AdgroupRow({
  campaignId, g, onChanged,
}: { campaignId: string; g: PaoScopeAdgroup; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);

  async function setScope(role: PaoScopeRole | null, enabled: boolean) {
    setBusy(true);
    try {
      await putPaoScopeAdgroup({ campaign_id: campaignId, adgroup_id: g.adgroup_id, role, enabled });
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function removeScope() {
    // ★해제와 끄기는 결과가 정반대다 — 마지막 행을 지우면 캠페인이 «전 그룹 대상»으로 돌아간다.
    const ok = window.confirm(
      `「${g.name}」을 스코프에서 완전히 뺍니다.\n\n` +
        "⚠️ 이 캠페인의 마지막 스코프 행이면, 캠페인이 «전 그룹 대상»으로 돌아갑니다\n" +
        "(엔진이 켜지면 이 캠페인의 모든 광고그룹이 대상이 됩니다).\n\n" +
        "그 그룹만 쉬게 하려면 «해제»가 아니라 «끄기»를 쓰세요.",
    );
    if (!ok) return;
    setBusy(true);
    try {
      const r = await deletePaoScopeAdgroup(campaignId, g.adgroup_id);
      if (r.campaign_now_unrestricted) {
        window.alert("이 캠페인의 스코프가 모두 사라져 «전 그룹 대상»으로 돌아갔습니다.");
      }
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  const scoped = g.scope_enabled !== null && g.scope_enabled !== undefined;

  return (
    <tr className={g.in_scope ? "bg-blue-50/40" : undefined}>
      <Td>
        <span className="font-medium text-gray-900">{g.name}</span>
        {g.status && g.status !== "on" && (
          <span className="ml-2 text-xs text-gray-400">{g.status}</span>
        )}
      </Td>
      <Td>
        <div className="flex items-center gap-1">
          <button
            type="button"
            disabled={busy}
            onClick={() => (g.in_scope ? setScope(g.scope_role, false) : setScope(g.scope_role, true))}
            className={`px-2 py-0.5 text-xs rounded-full ${
              g.in_scope ? "bg-blue-100 text-blue-800 font-semibold" : "bg-gray-100 text-gray-600"
            } disabled:opacity-40`}
            title={g.in_scope ? "이 그룹을 끕니다(행은 남습니다)" : "이 그룹을 엔진에 맡깁니다"}
          >
            {g.in_scope ? "맡김" : scoped ? "꺼짐" : "안 맡김"}
          </button>
          {scoped && (
            <button
              type="button"
              disabled={busy}
              onClick={removeScope}
              className="px-1 text-xs text-gray-400 hover:text-red-600 disabled:opacity-40"
              title="스코프에서 완전히 뺍니다(끄기와 결과가 다릅니다)"
            >
              ✕
            </button>
          )}
        </div>
      </Td>
      <Td>
        <select
          disabled={busy}
          value={g.scope_role ?? ""}
          onChange={(e) => setScope((e.target.value || null) as PaoScopeRole | null, g.scope_enabled ?? true)}
          className="text-xs border border-gray-200 rounded px-1 py-0.5 disabled:opacity-40"
          title={g.scope_role ? ROLE_HINT[g.scope_role] : "역할을 정하면 판정과 가드가 같은 것을 가리킵니다"}
        >
          <option value="">—</option>
          {(Object.keys(ROLE_LABEL) as PaoScopeRole[]).map((r) => (
            <option key={r} value={r}>{ROLE_LABEL[r]}</option>
          ))}
        </select>
      </Td>
      <Td right>{num(g.cost)}</Td>
      <Td right>{num(g.clk)}</Td>
      <Td right>{num(g.conv_amt)}</Td>
      <Td right>{g.roas === null ? <span className="text-gray-400">—</span> : g.roas.toFixed(2)}</Td>
      <Td right>{g.bep_roas === null ? <span className="text-gray-400">모름</span> : g.bep_roas.toFixed(3)}</Td>
      <Td right>
        <ProfitCell
          value={g.gross_profit} low={g.gross_profit_low} high={g.gross_profit_high}
          status={g.profit_status}
        />
      </Td>
    </tr>
  );
}
