// NaverAdModifications.tsx — 「수정 사항」. 그날 광고에 일어난 수정 전건 + 누가 했나.
//
// ★존재 이유: 대행사 조작을 찾으려면 라이브 `editTm`을 손으로 대조해야 했다(2026-07-30
//   03 캠페인 3건을 그렇게 찾았다). 그걸 날짜 하나 고르면 보이게 한다.
// ★두 원천을 합쳐 보여준다(naver_change_log ∪ naver_agency_op). 대행사 조작은 grain에 따라
//   다른 테이블에 들어가므로 한쪽만 보면 "그날 아무도 안 만졌다"는 거짓 안심을 준다.
// ★★화면에 'MOP'라는 말을 쓰지 않는다 — 코드베이스의 optimizer='mop'은 "제3자 소유"라는
//   뜻이라 Jino가 말하는 "MOP=우리 시스템"과 정반대다. 라벨은 우리 자동화/대행사/Jino 셋뿐.
// ★조회 전용이다. 이 화면에서 광고를 조작하는 기능은 없다(정정은 우리 DB의 주석일 뿐,
//   네이버 API 쓰기 0).
import { useCallback, useState } from "react";
import {
  Card, Table, Th, Td, Badge, Pager, Loading, EmptyState, LayerNav,
} from "../components/ui";
import { PeriodRangeBar, type PeriodPreset } from "../components/PeriodRangeBar";
import { presetWindowKeepingToday } from "../lib/periodRange";
import { usePeriodRange } from "../lib/usePeriod";
import { useAsyncData } from "../lib/useAsyncData";
import { num } from "../lib/format";
import {
  ACTOR_OPTIONS, actorLabel, actorTone, correctionNote, timeSuffix, valueText,
} from "../lib/modificationActor";
import {
  fetchNaverModifications, putNaverModificationActor,
  type NaverModificationActor, type NaverModificationFeedReapply, type NaverModificationRow,
} from "../lib/api";

const PAGE = 50;

/** 종전 `PeriodTabs`의 넷(당일·어제·7일·30일)을 그대로 옮긴다. 이 API 상한은 365일이라
 *  90일·1년까지 붙여도 프론트 검증(`customRangeError` 기본 상한 = MAX_SPAN_DAYS)과 어긋나지 않는다. */
const MODIFICATION_PERIOD_PRESETS: PeriodPreset[] = ["today", "yesterday", "7d", "30d", "90d", "1y"];

export default function NaverAdModifications() {
  // ★기본 창은 종전 `usePeriod()`의 기본값(당일)과 같다 — 이 화면은 「그날 누가 만졌나」가
  //   존재 이유라 열자마자 오늘을 봐야 한다.
  const p = usePeriodRange();
  const { from, to, setFrom, setTo } = p;
  const [actor, setActor] = useState<NaverModificationActor | "">("");
  // 정정 후 목록을 다시 불러오기 위한 버전 카운터. ★로컬 상태로 낙관적 갱신하지 않는다 —
  // 저장이 실패해도 화면만 바뀌면 "고쳤다"고 착각한다(서버가 진실이다).
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion((v) => v + 1), []);

  // ★기간·주체가 바뀌면 페이지를 처음으로 되돌린다. 안 되돌리면 3페이지를 보던 중 '어제'로
  //   바꿨을 때 offset=100이 그대로 나가 **결과가 있는데도 빈 화면**이 뜬다("수정 없음"으로 읽힌다).
  //   effect로 setState 하지 않고 **파생**한다 — 그러면 리셋 전 한 프레임이 잘못된 offset으로
  //   조회를 날리는 창(cascading render)이 애초에 없다.
  // D-NAO-139 — 네이버가 상품 피드를 재적용하면 그 상품의 소재가 **전부** 같은 초로 움직여
  // 한 사건이 N줄로 늘어선다. 기본은 접어서 1줄로 보여주고, 이 스위치를 켜면 아예 뺀다
  // (대행사가 실제로 만진 것만 보고 싶을 때). 무엇을 얼마나 뺐는지는 항상 표시한다.
  const [hideFeed, setHideFeed] = useState(false);
  const queryKey = `${p.range.from}|${p.range.to}|${actor}|${hideFeed}`;
  const [page, setPage] = useState({ key: queryKey, offset: 0 });
  const offset = page.key === queryKey ? page.offset : 0;
  const setOffset = (n: number) => setPage({ key: queryKey, offset: n });

  return (
    <div className="space-y-4">
      <LayerNav />
      <Card
        title="수정 사항"
        right={
          <div className="flex items-center gap-1">
            <FilterChip label="전체" active={actor === ""} onClick={() => setActor("")} />
            {ACTOR_OPTIONS.map((a) => (
              <FilterChip
                key={a}
                label={actorLabel(a)}
                active={actor === a}
                onClick={() => setActor(a)}
              />
            ))}
            <span className="mx-1 h-3 w-px bg-gray-200" />
            <FilterChip
              label="피드 재적용 숨기기"
              active={hideFeed}
              onClick={() => setHideFeed((v) => !v)}
            />
          </div>
        }
      >
        {/* 기간 바 — PAO 화면 공통(Jino 2026-09-03). ★「당일」은 당일 그대로다 —
            이 화면의 존재 이유가 「오늘 누가 만졌나」라 오늘을 못 고르면 기능이 사라진다. */}
        <PeriodRangeBar
          label="수정이 일어난 날"
          from={from} to={to} onFrom={setFrom} onTo={setTo}
          presets={MODIFICATION_PERIOD_PRESETS}
          windowFor={presetWindowKeepingToday}
          note="두 원천(우리 기록 ∪ 대행사 감지)을 합친 날짜입니다. 대행사 조작은 다음날 아침 감지되는 것이 있어 그 행은 감지 시각이 함께 붙습니다."
        />
        {p.error ? (
          <EmptyState reason={p.error} hint="기간을 다시 선택하세요." />
        ) : (
          <ModificationTable
            from={p.range.from}
            to={p.range.to}
            label={p.label}
            actor={actor}
            hideFeed={hideFeed}
            offset={offset}
            onOffset={setOffset}
            version={version}
            onCorrected={reload}
          />
        )}
      </Card>
    </div>
  );
}

function FilterChip({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-2 py-0.5 text-xs rounded-full ${
        active ? "bg-blue-50 text-blue-700 font-semibold" : "text-gray-600 hover:bg-gray-100"
      }`}
    >
      {label}
    </button>
  );
}

function ModificationTable({
  from, to, label, actor, hideFeed, offset, onOffset, version, onCorrected,
}: {
  from: string; to: string; label: string; actor: NaverModificationActor | ""; hideFeed: boolean;
  offset: number; onOffset: (n: number) => void; version: number; onCorrected: () => void;
}) {
  const { data, error } = useAsyncData(
    () => fetchNaverModifications({
      date_from: from, date_to: to, actor: actor || undefined, limit: PAGE, offset,
      include_feed_reapply: !hideFeed,
    }),
    [from, to, actor, hideFeed, offset, version],
  );

  if (error) {
    return <EmptyState reason={`불러오지 못했습니다: ${error}`} hint="새로고침하거나 백엔드 상태를 확인하세요." />;
  }
  if (data === null) return <Loading rows={5} />;
  if (data.rows.length === 0) {
    // ★0을 찍고 침묵하지 않는다(D-47-h). 왜 비었는지, 어디를 더 볼 수 있는지 말한다.
    return (
      <EmptyState
        reason={`${label}: 이날 기록된 수정 사항이 없습니다.`}
        hint={
          actor
            ? "주체 필터를 '전체'로 바꿔 보세요."
            : "대행사 조작은 하루 1회 스냅샷 대조(07:35)와 소재 수정 시각 대조로 잡힙니다 — 아직 안 잡혔을 수 있습니다."
        }
      />
    );
  }

  return (
    <>
      <ActorSummary byActor={data.by_actor} label={label} />
      <ReclaimedOursNote count={data.reclaimed_ours} />
      <FeedReapplyNote info={data.feed_reapply} />
      <Table
        head={
          <>
            <Th>시각</Th><Th>주체</Th><Th>대상</Th><Th>무엇을</Th><Th>이전 → 이후</Th><Th>출처</Th>
          </>
        }
      >
        {data.rows.map((r) => (
          <tr key={r.key}>
            <Td><TimeCell row={r} /></Td>
            <Td><ActorCell row={r} onCorrected={onCorrected} /></Td>
            <Td><TargetCell row={r} /></Td>
            <Td>
              <span className="text-xs">{r.op_label}</span>
              <FeedVerdictBadge row={r} />
            </Td>
            <Td><ValueCell row={r} /></Td>
            <Td><SourceCell row={r} /></Td>
          </tr>
        ))}
      </Table>
      <Pager total={data.total} offset={offset} pageSize={PAGE} onOffset={onOffset} />
    </>
  );
}

/** 주체 분포 — 필터를 걸어도 **구간 전체**를 보여준다(필터 때문에 그림이 반쪽이 되지 않게). */
function ActorSummary({ byActor, label }: { byActor: Record<string, number>; label: string }) {
  return (
    <p className="px-4 py-2 text-xs text-gray-500 border-b border-gray-100">
      {label} ·{" "}
      {ACTOR_OPTIONS.map((a, i) => (
        <span key={a}>
          {i > 0 && " · "}
          <span className="text-gray-700">{actorLabel(a)}</span> {num(byActor[a] ?? 0)}건
        </span>
      ))}
    </p>
  );
}

/** D-NAO-163 — 「외부 변경」으로 감지됐지만 우리 실집행과 대조돼 되찾은 건수.
 *
 *  ★같은 이유로 조용히 바꾸지 않는다: 주체는 사실 주장이라, 자동 규칙이 몇 건을 뒤집었는지
 *  모르면 사람이 그 규칙을 검증할 방법이 없다. 행마다의 근거는 주체 칸에 함께 붙는다. */
function ReclaimedOursNote({ count }: { count: number }) {
  if (!count) return null;
  return (
    <p className="px-4 py-2 text-xs text-gray-500 border-b border-gray-100">
      「외부 변경」으로 감지됐지만 우리 실집행과 대조돼 되찾음{" "}
      <span className="text-emerald-700">{num(count)}건</span> — 탐지가 하루 1회라 우리가 낮에 한
      일이 다음날 아침 외부 변경처럼 보입니다. 근거는 각 행의 주체 칸에 있습니다.
    </p>
  );
}

/** D-NAO-139 — 피드 재적용을 얼마나 접고 숨겼는지. **판정 행이 있으면 항상 말한다.**
 *
 *  ★조용히 접거나 숨기면 안 되는 이유: 이 화면은 "그날 대행사가 뭘 만졌나"에 답하는 화면이고,
 *  줄이 사라진 걸 모르면 줄어든 목록이 곧 "조용한 하루"로 읽힌다 — 이 화면이 없애려던
 *  거짓 안심이 형태만 바꿔 돌아온다. */
function FeedReapplyNote({ info }: { info: NaverModificationFeedReapply }) {
  if (!info || info.feed_rows === 0) return null;
  return (
    <p className="px-4 py-2 text-xs text-gray-500 border-b border-gray-100">
      네이버 상품 피드 재적용{" "}
      <span className="text-gray-700">{num(info.feed_rows)}건</span>
      {info.hidden > 0
        ? ` — 목록에서 숨김(${num(info.hidden)}건). 대행사가 실제로 만진 것만 보고 있습니다.`
        : info.collapsed_into > 0
          ? ` — 같은 상품이 같은 초에 움직인 ${num(info.collapsed_into)}줄을 접었습니다(한 사건 = 한 줄).`
          : " — 접힌 줄 없음."}
    </p>
  );
}

/** 판정 배지 — **주체는 안 바꾼다**(자동 판정 '대행사'는 그대로 둔다, D-NAO-138 ①).
 *  여기서 말하는 건 "그 편집이 사람 손인가"이지 "누구 소유인가"가 아니다. */
function FeedVerdictBadge({ row }: { row: NaverModificationRow }) {
  if (!row.feed_verdict) return null;
  // 실조작만 눈에 띄게 한다 — 이 화면의 목적이 "사람이 만진 것"을 찾는 것이라,
  // 피드 재적용(절대다수)이 강조되면 신호가 다시 잡음에 묻힌다.
  const tone = row.feed_verdict === "real" ? "alert" : "neutral";
  return (
    <div className="mt-0.5 space-y-0.5" title={row.feed_evidence ?? undefined}>
      <Badge tone={tone}>{row.feed_verdict_label}</Badge>
      {row.feed_group_size > 1 && (
        <div className="text-[11px] text-gray-500">소재 {num(row.feed_group_size)}개 동시</div>
      )}
    </div>
  );
}

/** ★발생 시각이 없는 건은 그 사실을 표시한다(계약). 감지 시각을 발생 시각처럼 쓰면
 *  "언제 손댔나"에 거짓으로 답하게 된다 — 이 화면이 없애려는 문제가 바로 그것이다. */
function TimeCell({ row }: { row: NaverModificationRow }) {
  const suffix = timeSuffix(row);
  return (
    <div className="text-xs leading-tight" title={row.time_note}>
      <div className="text-gray-700 tabular-nums">
        {row.occurred_at ? row.occurred_at.slice(5, 16).replace("T", " ") : "시각 미상"}
      </div>
      {suffix && <div className="text-amber-600">{suffix}</div>}
    </div>
  );
}

/** 주체 배지 + 정정 드롭다운. **최소로**: 셀렉트 하나, 고르면 즉시 저장·재조회.
 *  저장 실패는 삼키지 않는다(조용히 실패하면 사용자는 고쳐졌다고 믿는다). */
function ActorCell({ row, onCorrected }: { row: NaverModificationRow; onCorrected: () => void }) {
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const note = correctionNote(row);

  async function change(value: string) {
    setSaving(true);
    setFailed(null);
    try {
      await putNaverModificationActor(
        row.source, row.source_id, value === "auto" ? null : (value as NaverModificationActor),
      );
      onCorrected();
    } catch (e) {
      setFailed(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-0.5">
      <Badge tone={actorTone(row.actor)}>{row.actor_label}</Badge>
      <select
        className="block text-xs border border-gray-200 rounded px-1 py-0.5 text-gray-600"
        value={row.corrected ? row.actor : "auto"}
        disabled={saving}
        onChange={(e) => void change(e.target.value)}
        aria-label="수정 주체 정정"
      >
        <option value="auto">자동 판정</option>
        {ACTOR_OPTIONS.map((a) => (
          <option key={a} value={a}>{actorLabel(a)}</option>
        ))}
      </select>
      {note && <div className="text-[11px] text-gray-400">{note}</div>}
      {/* 규칙 ⑤ — 「외부 변경」으로 감지됐는데 우리 것으로 되찾은 행. 근거 없이 뒤집으면
          다음 사람이 못 믿으므로, 무엇과 대조했는지를 행에 그대로 적는다.
          ★사람이 ④로 「대행사」라고 정정한 행에는 안 보인다 — 배지는 대행사인데 밑에
          "우리 실집행과 대조됨"이 남아 있으면 한 셀이 상충하는 두 말을 한다. */}
      {row.actor_evidence && row.actor === "ours" && (
        <div className="text-[11px] text-emerald-700">{row.actor_evidence}</div>
      )}
      {row.correction_note && <div className="text-[11px] text-gray-500">{row.correction_note}</div>}
      {failed && <div className="text-[11px] text-red-600">저장 실패: {failed}</div>}
    </div>
  );
}

function TargetCell({ row }: { row: NaverModificationRow }) {
  return (
    <div className="text-xs leading-tight" title={`${row.entity_type} ${row.entity_id}`}>
      <div className={row.entity_name ? "font-medium text-gray-800" : "text-gray-500"}>
        {row.entity_name ?? row.entity_type_label}
        <span className="font-normal text-gray-400"> · {row.entity_type_label}</span>
      </div>
      {row.campaign_name && <div className="text-gray-500">{row.campaign_name}</div>}
    </div>
  );
}

/** ★"이전값 불명(관측 공백)"을 그대로 쓴다 — 빈칸이면 데이터 결손으로 읽히고 0이면 거짓이다
 *  (백필 36건 중 31건이 이전값 없음). */
function ValueCell({ row }: { row: NaverModificationRow }) {
  if (row.execution_state === "blocked") return <Badge tone="neutral">🚫 차단됨(안 바뀜)</Badge>;
  if (row.execution_state === "unknown") return <Badge tone="neutral">⚠️ 반영 불확실</Badge>;
  const before = valueText(row.before, row.before_unknown);
  const after = valueText(row.after, row.after_unknown);
  return (
    <span className="text-xs tabular-nums" title={row.summary ?? undefined}>
      <span className={row.before == null ? "text-gray-400" : ""}>{before}</span>
      {" → "}
      <span className={row.after == null ? "text-gray-400" : ""}>{after}</span>
    </span>
  );
}

/** 원천 + 소급 백필 표시. ★백필은 정규 탐지가 아니라 신뢰도가 다르다 — 숨기면 안 된다. */
function SourceCell({ row }: { row: NaverModificationRow }) {
  return (
    <div className="text-xs leading-tight">
      <div className="text-gray-500">{row.source_label}</div>
      {row.backfilled && <div className="text-amber-600">소급 백필</div>}
    </div>
  );
}
