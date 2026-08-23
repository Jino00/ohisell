// RgSettlementCard.tsx — RG(로켓그로스 2P) 계정별 정산 내역 카드.
//
// ★왜 여기로 나왔나 (계약 `docs/contracts/CONTRACT_2p_own_screens.md` §1-A-3):
//   계약이 요구한 것은 *"기존 `RgSettlementCard`(CommandCenter) 컴포넌트를 재사용해 주기별
//   정산 내역을 함께 싣는다"*였다. n=6은 화면 B에 **자체 요약 카드**만 두고 이 항목을
//   이행하지 않았다(완료 QA가 「미이행 1건」으로 남겼다). 재사용하려면 정의가 페이지 안에
//   갇혀 있으면 안 되므로 공용 모듈로 «정의만» 옮긴다.
//
// ★«이사»가 아니라 «재사용»이다 (계약 §3 금지선): 종합 조망(`CommandCenter.tsx`)의 렌더
//   자리는 그대로다. 같은 props로 같은 것을 그린다 — 옮긴 것은 함수의 주소일 뿐 표면이 아니다.
//
// ★두 번째 소비처는 갱신 버튼을 안 쓴다: 「RG 정산 갱신」은 Mac Wing 데몬을 깨우는 실동작이라
//   두 화면이 같은 데몬을 경쟁적으로 부르면 서로의 폴링을 인질로 잡는다(CommandCenter
//   `refreshRgSettlementNow`의 주석이 기록한 실사고와 같은 모양). 그래서 갱신 props를
//   **선택**으로 만들고, 없으면 버튼을 그리지 않는다. 기존 호출부는 종전대로 넘기므로 불변이다.
import type { OverviewResponse, RgSettlementByAccount } from "../lib/api";

function won(s: string | null | undefined): string {
  if (s == null) return "—";
  return `${Math.round(Number(s)).toLocaleString("ko-KR")}원`;
}

export function RgSettlementCard({
  data,
  onRefresh,
  refreshing,
  msg,
}: {
  data: OverviewResponse;
  /** 생략하면 갱신 버튼을 그리지 않는다(읽기 전용 소비처 — 위 주석 참조). */
  onRefresh?: () => void;
  refreshing?: boolean;
  msg?: string | null;
}) {
  const rg = data.rg_settlement;
  const RefreshButton = onRefresh ? (
    <button
      onClick={onRefresh}
      disabled={refreshing}
      className="flex items-center gap-1.5 px-2.5 py-1 text-xs bg-orange-600 text-white rounded-md hover:bg-orange-700 disabled:opacity-50"
    >
      <span className={refreshing ? "animate-spin" : ""}>🔄</span>
      {refreshing ? "갱신 중…" : "RG 정산 갱신"}
    </button>
  ) : null;
  if (!rg) return null;
  if (!rg.summary.has_data) {
    return (
      <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mb-4 flex items-center justify-between">
        <span className="text-sm text-amber-700">🚧 RG 정산 비용(미반영) — 데이터 없음</span>
        {RefreshButton}
      </div>
    );
  }
  return (
    <div className="bg-orange-50 border border-orange-200 rounded-lg p-4 mb-4">
      {msg && (
        <div className="text-xs text-orange-700 bg-orange-100 rounded px-2 py-1 mb-2">{msg}</div>
      )}
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-semibold text-orange-800">✅ RG 정산 비용 — 순이익 반영됨 (계정 단위, <span className="underline decoration-dotted">광고 제외</span> 차감)</span>
        <div className="flex items-center gap-2">
          {RefreshButton}
          <span className="text-right">
          {/* 헤드라인 = 실제 순이익 차감액 = 정산 총액 − 광고비(D-CPP-43). 부호 인식(Codex): 양수=차감(−), 음수=환급(+). */}
          {(() => {
            const d = Number(rg.summary.deducted ?? rg.summary.total);
            const sign = d < 0 ? "+" : "−";
            return <span className="text-base font-bold text-orange-900">{sign}{won(String(Math.abs(d)))}{d < 0 ? " (환급)" : ""}</span>;
          })()}
          <span className="block text-xs text-orange-500">정산총액 {won(rg.summary.total)} 중 광고 {won(rg.summary.ad_settlement ?? '0')}는 <b>차감 제외</b></span>
          {/* ★이 배너가 «어느 축»의 차감인지 말한다(적대 리뷰 3R P2). 헤드라인은 실제 차감액
              (판매일 축일 수 있다)인데 아래 계정 카드는 정산 원장 축 그대로라, 축을 안 밝히면
              한 배너가 서로 다른 두 숫자를 근거 없이 나란히 보인다. */}
          <span className="block text-xs text-orange-500">
            {rg.summary.axis === "sales_date"
              ? "헤드라인은 «판매일 축»(그 창에 판 것에 붙는 비용) · 아래 계정 카드는 «정산 원장 축»이라 값이 다를 수 있다"
              : "«정산 인식일 축» — 정산 주기 통짜라 그 주기를 덮는 어느 하루를 물어도 같은 값이다"}
          </span>
          </span>
        </div>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
        {rg.by_account.map((a: RgSettlementByAccount) => (
          <div key={a.account_key} className="bg-white rounded border border-orange-100 p-2 text-xs">
            <div className="font-medium text-gray-700 mb-1">{a.account_key}</div>
            <div className="flex justify-between"><span className="text-gray-500">판매수수료</span><span>{won(a.sale_fee)}</span></div>
            <div className="flex justify-between"><span className="text-gray-500">풀필먼트(배송·입출고·보관)</span><span>{won(a.fulfillment)}</span></div>
            <div className="flex justify-between"><span className="text-gray-500">반품비</span><span>{won(a.return_fee)}</span></div>
            <div className="flex justify-between text-gray-400"><span>광고비<span className="text-orange-400">*</span> <span className="text-[10px]">(차감 안 함)</span></span><span className="line-through">{won(a.ad_sales)}</span></div>
            {Number(a.other) !== 0 && (
              <div className="flex justify-between text-red-600"><span>기타(미매핑)</span><span>{won(a.other)}</span></div>
            )}
            <div className="flex justify-between border-t border-orange-100 mt-1 pt-1 text-gray-400"><span>정산 총액</span><span>{won(a.total)}</span></div>
            <div className="flex justify-between font-semibold"><span>순이익 차감액</span><span>{won(String(Number(a.total) - Number(a.ad_sales)))}</span></div>
          </div>
        ))}
      </div>
      <div className="text-xs text-orange-700 mt-2 bg-orange-100 rounded px-2 py-1">
        정산주기 기준(부분 윈도우도 주기 전액). <b>RG 광고비 {won(rg.summary.ad_settlement ?? '0')}는 차감하지 않는다</b> — 광고센터 PA 광고비를 정산에서 «공제»하는 것이라 PA(광고비 항목)에서 이미 빠졌다(D-CPP-43).
      </div>
      <p className="text-xs text-orange-600 mt-2">
        ✅ 순이익에 반영됨(계정 단위, RG 정산 총액 − 광고비, D-14/D-CPP-43).
        <span className="text-orange-400"> *</span>1차 출처: 윙 &gt; 정산 &gt; 로켓그로스 정산현황 &gt; 「광고비 내역」 — 광고유형이 전부 <b>PA</b>이고 캠페인 이름이 광고센터와 같다. 미공제분은 다음 정산으로 이월된다.
      </p>
    </div>
  );
}

export default RgSettlementCard;
