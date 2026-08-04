// CoupangAdChanges.tsx — 「광고 수정 사항」 쿠팡판. 신규·On/Off·내용 변경을 시간순으로.
//
// ★네이버 화면(NaverAdModifications)과 결정적으로 다른 점 하나: **주체 축이 없다.**
//   쿠팡은 우리가 광고를 쓰는 경로가 아예 없어서 모든 변경이 외부다. 네이버처럼
//   "우리 자동화/대행사/Jino"를 가르는 건 근거가 없다 — 없는 걸 지어내지 않는다.
//   (isAgencyManaged는 '현재 관리 주체'지 '이번 변경을 누가 했나'가 아니다.)
//
// ★발생 시각 배지를 반드시 붙인다. `time_basis='src'`는 쿠팡이 준 updatedAt(진짜 발생 시각),
//   `detected`는 우리가 알아챈 시각이다. 네이버에서 감지일로 귀속했다가 07-30 변경을
//   08-03으로 잡은 실사고가 있었다 — 화면이 둘을 뭉치면 같은 오독이 재발한다.
//
// ★조회 전용이다. 이 화면에서 쿠팡 광고를 조작하는 기능은 없다(쿠팡 광고 쓰기 경로 0).
import { useState } from "react";
import {
  Card, Table, Th, Td, Badge, Loading, EmptyState, PeriodTabs,
} from "../components/ui";
import { usePeriod } from "../lib/usePeriod";
import { useAsyncData } from "../lib/useAsyncData";
import {
  fetchCoupangAdChanges,
  type CoupangAdAccount, type CoupangAdChangeOp, type CoupangAdChangeRow,
} from "../lib/api";

const ACCOUNT_LABEL: Record<CoupangAdAccount, string> = {
  ofix: "오픽스 (3P/RG)",
  ohitech: "오하이테크 (1P 로켓)",
};

const OP_LABEL: Record<CoupangAdChangeOp, string> = {
  created: "신규 생성",
  turned_on: "켜짐",
  turned_off: "꺼짐",
  deleted: "삭제됨",
  field_change: "내용 변경",
  ads_changed: "소재 변경",
  ads_added: "소재 추가",
  ads_removed: "소재 제거",
};

// Badge의 tone 어휘는 이 코드베이스에 이미 정해져 있다(dir/judge/owner/neutral/alert).
// 새 색을 만들지 않고 그 의미에 얹는다 — amber(alert)는 "사람이 봐야 한다"는 뜻이라
// 광고가 꺼지거나 사라진 경우에 맞다.
const OP_TONE: Record<CoupangAdChangeOp, "owner" | "alert" | "neutral"> = {
  created: "owner",
  turned_on: "owner",
  turned_off: "alert",
  deleted: "alert",
  field_change: "neutral",
  ads_changed: "neutral",
  ads_added: "neutral",
  ads_removed: "alert",
};

// 쿠팡 원필드명을 사람 말로. ★모르는 필드는 **원문 그대로 보여준다** — 지어내지 않는다.
const FIELD_LABEL: Record<string, string> = {
  name: "이름",
  budget: "일예산",
  capType: "예산 유형",
  budgetRule: "예산 규칙",
  budgetSharingGroupId: "예산 공유 그룹",
  roasTarget: "목표 ROAS",
  targetType: "목표 유형",
  objective: "캠페인 목적",
  goalType: "목표",
  campaignType: "캠페인 종류",
  startDate: "시작일",
  endDate: "종료일",
  isSuspended: "일시중지",
  disabledByCoupang: "쿠팡 차단",
  uneditable: "수정 잠금",
  isAgencyManaged: "대행사 관리",
  promotionScheme: "프로모션",
  bidType: "입찰 방식",
  pricingType: "과금 방식",
  pricing: "입찰가",
  andonRoasTarget: "안돈 목표 ROAS",
  keywordTargeting: "키워드 타게팅",
  budgetType: "예산 유형",
  adSelectionType: "소재 선택",
  smartTargetingPricingType: "스마트 타게팅 과금",
  smartTargetingPricing: "스마트 타게팅 입찰",
  adDiscountOptIn: "광고 할인 참여",
};

/** 숫자면 천단위, 아니면 원문. null은 "—"가 아니라 "(없음)" — 빈칸과 구분한다. */
function valueText(v: string | null): string {
  if (v === null || v === "") return "(없음)";
  if (v === "true") return "예";
  if (v === "false") return "아니오";
  const n = Number(v);
  return Number.isFinite(n) && v.trim() !== "" ? n.toLocaleString("ko-KR") : v;
}

function kstText(iso: string): string {
  // 서버가 이미 KST로 환산해 보낸다(전역 원칙 0). 여기서 다시 변환하면 9시간이 더 붙는다.
  return iso.replace("T", " ").slice(0, 19);
}

export default function CoupangAdChanges() {
  const p = usePeriod();   // 기본 = 당일. 변경 이력은 "오늘 뭐가 바뀌었나"가 첫 질문이다.
  const [account, setAccount] = useState<CoupangAdAccount | "">("");

  const { data, error } = useAsyncData(
    () => fetchCoupangAdChanges({
      from: p.range.from, to: p.range.to,
      account: account || undefined,
    }),
    [p.range.from, p.range.to, account],
  );

  return (
    <div className="space-y-4">
      <Card
        title="쿠팡 광고 수정 사항"
        right={
          <div className="flex items-center gap-1">
            <FilterChip label="전체" active={account === ""} onClick={() => setAccount("")} />
            {(Object.keys(ACCOUNT_LABEL) as CoupangAdAccount[]).map((a) => (
              <FilterChip
                key={a}
                label={ACCOUNT_LABEL[a]}
                active={account === a}
                onClick={() => setAccount(a)}
              />
            ))}
          </div>
        }
      >
        <PeriodTabs p={p} />

        {/* ★쿠팡엔 '우리 실집행'이 없다는 사실을 화면이 먼저 말한다 —
            "우리 변경이 왜 안 보이지?"라는 오해를 구조적으로 막는다. */}
        <p className="mt-2 text-xs text-gray-500">
          쿠팡 광고는 sellc에서 조작하지 않습니다. 여기 뜨는 변경은 <b>전부 광고센터에서
          일어난 것</b>입니다(Jino 또는 대행사). 누가 했는지는 쿠팡이 알려주지 않아 표시하지 않습니다.
        </p>

        {p.error ? (
          <EmptyState reason={p.error} />
        ) : error ? (
          <EmptyState reason={`불러오지 못했습니다: ${error}`} />
        ) : data === null ? (
          <Loading />
        ) : (
          <>
            <Freshness at={data.last_observed_at} />
            {data.items.length === 0 ? (
              <EmptyState reason={
                data.last_observed_at
                  ? "이 기간에 잡힌 광고 설정 변경이 없습니다."
                  : "아직 광고 설정을 한 번도 관측하지 않았습니다 — 「광고비 갱신」을 한 번 누르면 기준선이 생깁니다."
              } />
            ) : (
              <ChangeTable rows={data.items} />
            )}
          </>
        )}
      </Card>
    </div>
  );
}

/** 언제 기준의 목록인가. 없으면 "변경 0건"이 "관측을 안 했다"와 구분되지 않는다. */
function Freshness({ at }: { at: string | null }) {
  return (
    <div className="mt-2 text-xs text-gray-500">
      마지막 관측:{" "}
      {at ? (
        <span className="font-medium text-gray-700">{kstText(at)} KST</span>
      ) : (
        <span className="text-amber-600">없음</span>
      )}
      <span className="ml-2 text-gray-400">
        · 설정 수집은 「광고비 갱신」 회차에 함께 돕니다(별도 크론 없음 — 창이 뜨지 않게).
      </span>
    </div>
  );
}

function ChangeTable({ rows }: { rows: CoupangAdChangeRow[] }) {
  return (
    <div className="mt-3">
      <Table
        head={
          <>
            <Th>발생 시각 (KST)</Th>
            <Th>계정</Th>
            <Th>대상</Th>
            <Th>무엇이</Th>
            <Th>전</Th>
            <Th>후</Th>
          </>
        }
      >
        {rows.map((r) => (
            <tr key={r.id} className="border-t border-gray-100">
              <Td>
                <div className="whitespace-nowrap">{kstText(r.occurred_at)}</div>
                {/* ★진짜 발생 시각인지, 우리가 알아챈 시각인지 — 뭉치면 안 된다. */}
                {r.time_basis === "detected" && (
                  <span className="text-[11px] text-amber-600">감지 시각(쿠팡 미제공)</span>
                )}
              </Td>
              <Td>
                <span className="text-xs text-gray-600">{ACCOUNT_LABEL[r.account]}</span>
              </Td>
              <Td>
                <div className="max-w-[22rem] truncate" title={r.entity_name}>
                  {r.entity_name || "(이름 없음)"}
                </div>
                <span className="text-[11px] text-gray-400">
                  {r.entity_type === "adgroup" ? "광고그룹"
                    : r.entity_type === "ad" ? "소재" : "캠페인"} · {r.entity_id}
                </span>
              </Td>
              <Td>
                <Badge tone={OP_TONE[r.op]}>{OP_LABEL[r.op] ?? r.op}</Badge>
                {r.field && (
                  <span className="ml-1 text-xs text-gray-700">
                    {FIELD_LABEL[r.field] ?? r.field}
                  </span>
                )}
              </Td>
              <Td>
                {r.op === "field_change" && (
                  <span className="text-gray-500">{valueText(r.before_value)}</span>
                )}
              </Td>
              <Td>
                {r.op === "field_change" ? (
                  <span className="font-medium">{valueText(r.after_value)}</span>
                ) : (
                  <AdsSummary r={r} />
                )}
              </Td>
            </tr>
        ))}
      </Table>
    </div>
  );
}

/**
 * 소재 변경 요약 + 옵션ID 펼치기.
 * ★목록이 없을 수 있다 — 쿠팡 변경 이력은 **개수만** 주고, 우리 소재 스냅샷은 2026-08-04부터
 *   쌓이기 시작했다. 그래서 그 이전 사건은 영원히 개수만 남는다. 그 사실을 숨기지 않는다
 *   (목록 없음을 빈칸으로 두면 "소재가 안 바뀐 것"으로 읽힌다).
 */
function AdsSummary({ r }: { r: CoupangAdChangeRow }) {
  const [open, setOpen] = useState(false);
  const d = r.detail;
  const isAds = r.op === "ads_changed" || r.op === "ads_added" || r.op === "ads_removed";
  if (!isAds) return null;

  const counts = d
    ? [d.added ? `${d.added}개 추가` : null, d.removed ? `${d.removed}개 제거` : null]
        .filter(Boolean).join(" · ")
    : "";
  const opts = d?.options ?? [];

  return (
    <div className="text-sm">
      <div className="flex items-center gap-2">
        {r.before_value !== null && r.after_value !== null && (
          <span className="text-gray-600">{r.before_value}개 → <b>{r.after_value}개</b></span>
        )}
        {counts && <span className="text-gray-500">{counts}</span>}
        {opts.length > 0 ? (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="text-xs text-blue-600 hover:underline"
          >
            {open ? "접기" : `옵션 ${opts.length}개 보기`}
          </button>
        ) : (
          <span className="text-[11px] text-gray-400">옵션 목록 없음(개수만 기록됨)</span>
        )}
      </div>
      {open && (
        <ul className="mt-1 max-h-56 overflow-y-auto rounded bg-gray-50 p-2 text-xs">
          {opts.map((o, i) => (
            <li key={`${o.vendor_item_id}-${i}`} className="py-0.5">
              <span className="font-mono text-gray-700">{o.vendor_item_id ?? "(ID 없음)"}</span>
              <span className="ml-2 text-gray-500">{o.item_name}</span>
            </li>
          ))}
          {d?.truncated && (
            <li className="pt-1 text-amber-600">…목록이 잘렸습니다(200개 상한)</li>
          )}
        </ul>
      )}
    </div>
  );
}

function FilterChip({ label, active, onClick }: {
  label: string; active: boolean; onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full px-2.5 py-1 text-xs transition ${
        active ? "bg-gray-900 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
      }`}
    >
      {label}
    </button>
  );
}
