// InventoryPage.tsx — 재고 관리 (멀티채널 재고 관제 허브, D-19)
// 채널 탭 [로켓그로스(RG) | 로켓배송 1P | Wing]. RG = 발송관제 | 청구 감사(S8, D-17) 서브탭.
// 1P/Wing은 향후 입주.
import { useState, useEffect } from "react";
import { fetchReplenishmentPlan, fetchRgFeeAudit, type ReplenishmentPlan, type RgFeeAudit } from "../lib/api";
import RgReplenishmentTable from "../components/RgReplenishmentTable";
import RgFeeAuditView from "../components/RgFeeAuditView";

const COMPANIES = [
  { value: "ALL", label: "전체" },
  { value: "오픽스", label: "오픽스" },
  { value: "오하이테크", label: "오하이테크" },
];

const CHANNELS = ["로켓그로스", "로켓배송 1P", "Wing"] as const;
type Channel = (typeof CHANNELS)[number];

const RG_SUBTABS = ["발송관제", "청구 감사"] as const;
type RgSubTab = (typeof RG_SUBTABS)[number];

export default function InventoryPage() {
  const [company, setCompany] = useState("ALL");
  const [channel, setChannel] = useState<Channel>("로켓그로스");
  const [rgSubTab, setRgSubTab] = useState<RgSubTab>("발송관제");

  // RG 발송관제 플랜
  const [rgPlan, setRgPlan] = useState<ReplenishmentPlan | null>(null);
  const [rgLoading, setRgLoading] = useState(false);
  const [rgError, setRgError] = useState<string | null>(null);

  // RG 청구 감사(S8, D-17) — 읽기 전용 스크리닝. 화면 소비자가 없어 curl로만 보이던 것을 노출.
  const [feeAudit, setFeeAudit] = useState<RgFeeAudit | null>(null);
  const [feeAuditLoading, setFeeAuditLoading] = useState(false);
  const [feeAuditError, setFeeAuditError] = useState<string | null>(null);

  // 로켓그로스 탭 선택 시 발송관제 플랜 로드 (회사 필터 반영).
  useEffect(() => {
    if (channel !== "로켓그로스" || rgSubTab !== "발송관제") return;
    let cancelled = false;  // 회사 빠른 전환 시 옛 응답이 새 응답 덮어쓰는 레이스 방지
    setRgLoading(true);
    setRgError(null);
    fetchReplenishmentPlan(company, 7)
      .then((p) => { if (!cancelled) setRgPlan(p); })
      .catch((e: Error) => { if (!cancelled) setRgError(e.message); })
      .finally(() => { if (!cancelled) setRgLoading(false); });
    return () => { cancelled = true; };
  }, [channel, company, rgSubTab]);

  // 로켓그로스 > 청구 감사 서브탭 선택 시 감사 결과 로드(회사 필터 반영).
  useEffect(() => {
    if (channel !== "로켓그로스" || rgSubTab !== "청구 감사") return;
    let cancelled = false;
    setFeeAuditLoading(true);
    setFeeAuditError(null);
    fetchRgFeeAudit(company)
      .then((a) => { if (!cancelled) setFeeAudit(a); })
      .catch((e: Error) => { if (!cancelled) setFeeAuditError(e.message); })
      .finally(() => { if (!cancelled) setFeeAuditLoading(false); });
    return () => { cancelled = true; };
  }, [channel, company, rgSubTab]);

  return (
    <div>
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <h2 className="text-2xl font-bold text-gray-900">🏭 재고 관리</h2>
        {/* 회사 필터 */}
        <div className="flex gap-1">
          {COMPANIES.map((c) => (
            <button
              key={c.value}
              onClick={() => setCompany(c.value)}
              className={`px-2.5 py-1 rounded text-xs font-medium ${
                company === c.value ? "bg-gray-800 text-white" : "bg-white border border-gray-300 text-gray-600 hover:bg-gray-100"
              }`}
            >
              {c.label}
            </button>
          ))}
        </div>
      </div>

      {/* 채널 탭 */}
      <div className="flex gap-1 mb-4 border-b border-gray-200">
        {CHANNELS.map((ch) => (
          <button
            key={ch}
            onClick={() => setChannel(ch)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${
              channel === ch
                ? "border-orange-500 text-orange-700"
                : "border-transparent text-gray-500 hover:text-gray-800"
            }`}
          >
            {ch}
          </button>
        ))}
      </div>

      {channel === "로켓그로스" && (
        <>
          {/* RG 서브탭 [발송관제 | 청구 감사] */}
          <div className="flex gap-1 mb-3">
            {RG_SUBTABS.map((t) => (
              <button
                key={t}
                onClick={() => setRgSubTab(t)}
                className={`px-3 py-1.5 rounded text-xs font-medium ${
                  rgSubTab === t
                    ? "bg-orange-100 text-orange-800"
                    : "bg-white border border-gray-300 text-gray-600 hover:bg-gray-100"
                }`}
              >
                {t}
              </button>
            ))}
          </div>

          {rgSubTab === "발송관제" && (
            <RgReplenishmentTable plan={rgPlan} loading={rgLoading} error={rgError} />
          )}
          {rgSubTab === "청구 감사" && (
            <RgFeeAuditView audit={feeAudit} loading={feeAuditLoading} error={feeAuditError} />
          )}
        </>
      )}

      {channel === "로켓배송 1P" && (
        <div className="bg-white border border-gray-200 rounded-lg px-4 py-12 text-center text-gray-400 text-sm">
          🚚 로켓배송 1P 재고 — 준비 중
        </div>
      )}

      {channel === "Wing" && (
        <div className="bg-white border border-gray-200 rounded-lg px-4 py-12 text-center text-gray-400 text-sm">
          🛫 Wing 재고 — 준비 중
        </div>
      )}
    </div>
  );
}
