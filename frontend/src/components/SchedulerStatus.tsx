// SchedulerStatus.tsx — 사이드바 하단 스케줄러 상태 컴포넌트 (Sprint 3)
import { useEffect, useState } from "react";
import { fetchApi, type SchedulerStatus as SchedulerStatusType } from "../lib/api";

export default function SchedulerStatus() {
  const [status, setStatus] = useState<SchedulerStatusType | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [triggering, setTriggering] = useState<string | null>(null);
  const [toggling, setToggling] = useState<string | null>(null);
  // 「지금 실행」 결과 한 줄 — 잡이 "실행했지만 아무것도 안 했다"를 말할 수 있어야 한다.
  // (예: 시간별 스냅샷은 같은 시각 슬롯이면 건너뛴다 — 다시 찍으면 페이싱 증분이 왜곡되므로.)
  // 응답을 버리면 그 구분이 화면에서 사라지고, 그건 가드가 없는 것과 같아 보인다.
  const [triggerMsg, setTriggerMsg] = useState<{ jobId: string; text: string; skipped: boolean } | null>(null);

  async function fetchStatus() {
    try {
      const data = await fetchApi<SchedulerStatusType>("/api/scheduler/status");
      setStatus(data);
    } catch {
      setStatus(null);
    }
  }

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 60000);
    return () => clearInterval(interval);
  }, []);

  async function handleTrigger(jobId: string) {
    setTriggering(jobId);
    setTriggerMsg(null);
    try {
      const res = await fetchApi<{ detail?: string; skipped?: boolean }>(
        `/api/scheduler/trigger/${jobId}`, { method: "POST", body: JSON.stringify({}) });
      setTriggerMsg({ jobId, text: res?.detail || "실행 완료", skipped: !!res?.skipped });
      await fetchStatus();
    } catch (e: any) {
      // 종전에는 조용히 삼켰다 — 실패한 수동 실행이 성공과 구분되지 않았다.
      setTriggerMsg({ jobId, text: e?.message || "실행 실패", skipped: false });
    } finally {
      setTriggering(null);
    }
  }

  async function handleToggle(jobId: string, enabled: boolean) {
    setToggling(jobId);
    try {
      await fetchApi(`/api/scheduler/toggle/${jobId}?enabled=${enabled}`, { method: "PUT" });
      await fetchStatus();
    } catch {
      // ignore
    } finally {
      setToggling(null);
    }
  }

  function formatNextRun(isoStr: string | null): string {
    if (!isoStr) return "--";
    const d = new Date(isoStr);
    const now = Date.now();
    const diffMin = Math.round((d.getTime() - now) / 60000);
    if (diffMin <= 0) return "곧 실행";
    if (diffMin < 60) return `${diffMin}분 후`;
    const diffHr = Math.floor(diffMin / 60);
    return `${diffHr}시간 ${diffMin % 60}분 후`;
  }

  if (!status) return null;

  const nextJob = status.jobs.find((j) => j.is_enabled && j.next_run_time);
  const nextRunText = nextJob ? formatNextRun(nextJob.next_run_time) : "--";

  return (
    <div className="border-t border-gray-200 p-3">
      {/* Compact bar */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 text-xs text-gray-600 hover:text-gray-900"
      >
        <span className={`w-2 h-2 rounded-full ${status.is_running ? "bg-green-500" : "bg-gray-400"}`} />
        <span className="font-medium">자동 동기화</span>
        <span className="text-gray-400 ml-auto">{nextRunText}</span>
        <span className={`transition-transform ${expanded ? "rotate-180" : ""}`}>&#9662;</span>
      </button>

      {/* Expanded panel */}
      {expanded && (
        <div className="mt-2 space-y-2">
          {status.jobs.length === 0 && (
            <div className="text-xs text-gray-400">등록된 작업이 없습니다</div>
          )}
          {status.jobs.map((job) => (
            <div key={job.id}>
            <div className="flex items-center gap-2 text-xs">
              {/* Toggle switch */}
              <button
                onClick={() => handleToggle(job.id, !job.is_enabled)}
                disabled={toggling === job.id}
                className={`relative w-8 h-4 rounded-full transition-colors ${
                  job.is_enabled ? "bg-green-500" : "bg-gray-300"
                }`}
              >
                <span
                  className={`absolute top-0.5 w-3 h-3 bg-white rounded-full shadow transition-transform ${
                    job.is_enabled ? "left-4" : "left-0.5"
                  }`}
                />
              </button>
              <span className="flex-1 text-gray-700 truncate" title={job.name}>
                {job.name}
              </span>
              <button
                onClick={() => handleTrigger(job.id)}
                disabled={triggering === job.id}
                className="px-1.5 py-0.5 bg-blue-50 text-blue-600 rounded hover:bg-blue-100 disabled:opacity-50 whitespace-nowrap"
              >
                {triggering === job.id ? "..." : "지금 실행"}
              </button>
            </div>
            {triggerMsg?.jobId === job.id && (
              <div className={`mt-0.5 ml-10 text-[11px] leading-snug ${
                triggerMsg.skipped ? "text-amber-600" : "text-gray-500"}`}>
                {triggerMsg.skipped ? "⏭ " : ""}{triggerMsg.text}
              </div>
            )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
