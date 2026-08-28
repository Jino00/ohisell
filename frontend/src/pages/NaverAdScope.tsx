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
// ★조회 + 스코프 설정만 한다. 이 화면에 **엔진을 켜는 버튼은 없다**(auto_operate는 별도 결정).
import { useState } from "react";
import {
  Card, Table, Th, Td, Badge, Loading, EmptyState, LayerNav,
} from "../components/ui";
import { useAsyncData } from "../lib/useAsyncData";
import { num } from "../lib/format";
import {
  fetchPaoScopeRoster, putPaoScopeAdgroup, deletePaoScopeAdgroup,
  type PaoScopeCampaign, type PaoScopeAdgroup, type PaoScopeRole,
  type PaoScopeDayClassSplit,
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
      켜는 것은 이 화면이 아니라 별도 결정입니다.
    </div>
  );
}

function CampaignBlock({ c, onChanged }: { c: PaoScopeCampaign; onChanged: () => void }) {
  const [open, setOpen] = useState(c.has_scope);
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-gray-50"
      >
        <span className="text-gray-400 text-xs w-3">{open ? "▾" : "▸"}</span>
        <span className="text-sm font-medium text-gray-900 flex-1 min-w-0 truncate">{c.name}</span>
        {c.has_scope ? (
          <Badge tone="owner">스코프 {c.scoped_count}/{c.adgroup_count}</Badge>
        ) : (
          <Badge tone="neutral">전 그룹</Badge>
        )}
        <Badge tone={c.auto_operate && c.optimizer === "ours" ? "good" : "neutral"}>
          {c.optimizer === "ours" ? (c.auto_operate ? "가동" : "우리·정지") : c.optimizer === "mop" ? "MOP" : "수동"}
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
      {open && (
        <div className="pb-3">
          {c.has_scope && (
            <p className="px-4 pb-2 text-xs text-gray-500">
              ★이 캠페인은 «일부 그룹만» 맡긴 상태라 <b>캠페인 예산 조정은 엔진이 하지 않습니다</b> —
              예산은 광고그룹으로 나눌 수 없어서, 열어두면 스코프 밖 그룹의 노출까지 같이 움직입니다.
            </p>
          )}
          <AdgroupTable c={c} onChanged={onChanged} />
        </div>
      )}
    </div>
  );
}

function AdgroupTable({ c, onChanged }: { c: PaoScopeCampaign; onChanged: () => void }) {
  if (c.adgroups.length === 0) {
    return <EmptyState reason="이 창에 집행된 광고그룹이 없습니다." />;
  }
  return (
    <Table
      head={
        <tr>
          <Th>광고그룹</Th>
          <Th>맡김</Th>
          <Th>역할</Th>
          <Th right>광고비</Th>
          <Th right>클릭</Th>
          <Th right>전환매출</Th>
          <Th right>ROAS</Th>
          <Th right>BEP</Th>
          <Th right>총이익<span className="block text-[10px] font-normal text-gray-400">있는 그대로 / 구간</span></Th>
        </tr>
      }
    >
      {c.adgroups.map((g) => (
        <AdgroupRow key={g.adgroup_id} campaignId={c.campaign_id} g={g} onChanged={onChanged} />
      ))}
    </Table>
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
