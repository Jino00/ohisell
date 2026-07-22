// collectionFreshnessBanner.ts — 전역 수집 신선도 배너의 순수 빌더(표시 로직만, React 무관).
//   fresh/in_flight는 숨김. warn=🟡, critical/failed=🔴.
//   stale(안 눌러 낡음) vs failed(눌렀는데 로그인 깨짐)를 구분해 문구를 만든다.
import type { CollectionStatus, CollectionStreamStatus } from "../lib/api";

export interface FreshnessBannerItem {
  key: string;
  label: string;
  kind: "stale" | "failed";
  text: string;
}
export interface FreshnessBanner {
  severity: "yellow" | "red";
  items: FreshnessBannerItem[];
}

function ageText(hours: number | null): string {
  // 일별 데이터라 48h 미만은 시간 단위가 더 정밀(예: '30시간 지남'), 이후는 일 단위.
  if (hours == null) return "수집 기록 없음";
  if (hours >= 48) return `${Math.floor(hours / 24)}일 지남`;
  return `${Math.floor(hours)}시간 지남`;
}

export function buildCollectionFreshnessBanner(
  status: CollectionStatus | null | undefined,
): FreshnessBanner | null {
  if (!status || !Array.isArray(status.streams)) return null;
  const items: FreshnessBannerItem[] = [];
  for (const st of status.streams as CollectionStreamStatus[]) {
    if (st.state === "failed") {
      items.push({
        key: st.key, label: st.label, kind: "failed",
        text: `${st.label} 갱신 실패 · 로그인 필요`,
      });
    } else if (st.state === "warn" || st.state === "critical") {
      items.push({
        key: st.key, label: st.label, kind: "stale",
        text: `${st.label} ${ageText(st.age_hours)}`,
      });
    }
  }
  if (items.length === 0) return null;
  const hasRed =
    items.some((i) => i.kind === "failed") ||
    status.streams.some((st) => st.state === "critical");
  return { severity: hasRed ? "red" : "yellow", items };
}
