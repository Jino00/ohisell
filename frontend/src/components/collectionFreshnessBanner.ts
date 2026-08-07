// collectionFreshnessBanner.ts — 전역 수집 신선도 배너의 순수 빌더(표시 로직만, React 무관).
//   fresh는 숨김. in_flight는 **낡지 않았을 때만** 숨김(아래 staleWhileInFlight).
//   warn=🟡, critical/failed/unknown=🔴.
//   stale(안 눌러 낡음) vs failed(눌렀는데 실패) vs unknown(백엔드가 판정 못 함)을 구분한다.
//   ★2026-08-07: 광고비 stale도 여기로 합쳐졌다(Layout의 빨강 광고쿠키 배너에서 stale 갈래
//     제거) — 같은 last_success_at을 두 배너가 각자 판정하던 중복을 없앴다. 이제 "낡음"의
//     단일 표면은 이 배너이고, 빨강 배너는 버튼으로 못 고치는 것(쿠키 만료·크론 꺼짐) 전용.
import type { CollectionStatus, CollectionStreamStatus } from "../lib/api";
import { isLoginRequired } from "../lib/streamRefresh";

export interface FreshnessBannerItem {
  key: string;
  label: string;
  kind: "stale" | "failed" | "unknown";
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

/** 실패 시각 'MM-DD HH:MM'. 백엔드는 naive KST ISO를 준다(utils/kst.py) — 파싱 없이 잘라 쓴다.
 *  ★Date로 파싱하지 않는 이유: naive ISO를 new Date()에 넣으면 브라우저가 로컬 타임존으로
 *    해석해 KST가 아닌 곳에서 시각이 밀린다. 문자열 슬라이스면 그 함정이 없다. */
function errAtText(iso: string | null): string {
  if (!iso || iso.length < 16) return "";
  return `${iso.slice(5, 10)} ${iso.slice(11, 16)}`;
}

/** 실패 사유 꼬리표. ★하드코딩 금지(2026-08-07): 예전엔 failed면 무조건 '로그인 필요'라고
 *  적었는데, 실제 실패는 브라우저 크래시·네트워크 등도 있다(2026-08-07 RG 실측:
 *  'Target page, context or browser has been closed'). 원문에 근거가 있을 때만 붙인다.
 *  ★판정은 streamRefresh.isLoginRequired **한 벌만** 쓴다(적대리뷰 P2). 여기서 따로
 *    `includes("로그인")`을 쓰면 더 넓어져, 로그인이 아닌 크래시 문구
 *    ('브라우저 창이 닫혔습니다 — 로그인 미완료')에도 꼬리표가 붙는다. 사본이 원본보다
 *    관대하면 원본의 결함을 은폐하는 게 아니라 **새 오탐을 만든다**. */
function reasonText(lastError: string | null): string {
  return isLoginRequired(lastError) ? " · 로그인 필요" : "";
}

/** 마지막 성공이 얼마나 전인지. failed 항목에 **반드시** 붙인다(2026-08-07 적대리뷰 P1).
 *  ★없으면 "실패했지만 데이터는 3시간 전 것"과 "실패했고 데이터는 3개월 전 것"이 같은
 *    문구가 된다. 구 빨강 배너가 stale일 때 '(마지막 수집 …)'을 띄워 이 역할을 했는데,
 *    그 갈래를 이 배너로 흡수하면서 경과가 통째로 사라졌다. 실패 시각(언제 깨졌나)과
 *    마지막 성공(데이터가 얼마나 낡았나)은 **다른 질문**이고 둘 다 필요하다. */
function lastSuccessText(hours: number | null): string {
  if (hours == null) return "수집 기록 없음";
  if (hours >= 48) return `마지막 성공 ${Math.floor(hours / 24)}일 전`;
  return `마지막 성공 ${Math.floor(hours)}시간 전`;
}

// ★백엔드 collection_status.py의 WARN_HOURS/CRIT_HOURS와 같은 값이어야 한다.
//   여기서만 쓰인다 — warn/critical 판정은 백엔드가 하고 프론트는 그 state를 그대로 믿는다.
//   아래 in_flight 구제 경로만 예외적으로 경과시간을 직접 본다(백엔드가 그 구간의 state를
//   in_flight로 덮어써서 낡음 정보가 사라지기 때문).
const WARN_HOURS = 24;
const CRIT_HOURS = 48;

/** in_flight인데 이미 낡았나? (age null = 수집 기록 없음도 낡은 것으로 친다) */
function staleWhileInFlight(hours: number | null): boolean {
  return hours == null || hours >= WARN_HOURS;
}

export function buildCollectionFreshnessBanner(
  status: CollectionStatus | null | undefined,
): FreshnessBanner | null {
  if (!status || !Array.isArray(status.streams)) return null;
  const items: FreshnessBannerItem[] = [];
  for (const st of status.streams as CollectionStreamStatus[]) {
    if (st.state === "failed") {
      // ★시각을 반드시 붙인다(2026-08-07 실사고): 33시간 전 실패가 시각 없이 현재형으로 읽혀
      //   "지금 고장 나 있다"로 오인됐다 — 실제론 그냥 누르니 47초 만에 성공했다.
      //   "마지막 시도가 실패했다"와 "지금 망가져 있다"는 다른 말이다.
      const at = errAtText(st.last_error_at);
      items.push({
        key: st.key, label: st.label, kind: "failed",
        text: `${st.label} 갱신 실패${at ? `(${at})` : ""}`
          + ` · ${lastSuccessText(st.age_hours)}${reasonText(st.last_error)}`,
      });
    } else if (st.state === "unknown") {
      // ★백엔드가 판정 자체를 못 한 스트림(getter 예외). 예전엔 이 경우 엔드포인트가 500이라
      //   프론트가 배너를 통째로 안 띄웠다 — 낡아도 아무 데도 안 뜨는 전면 실명이었다.
      //   이제 나머지 스트림은 정상 표시되고, 못 본 스트림은 못 봤다고 말한다.
      items.push({
        key: st.key, label: st.label, kind: "unknown",
        text: `${st.label} 상태 조회 실패 — 낡음 여부 판정 불가`,
      });
    } else if (st.state === "warn" || st.state === "critical") {
      items.push({
        key: st.key, label: st.label, kind: "stale",
        text: `${st.label} ${ageText(st.age_hours)}`,
      });
    } else if (st.state === "in_flight" && staleWhileInFlight(st.age_hours)) {
      // ★멈춘 요청이 낡음을 가리지 못하게 한다 (2026-08-07, 이 PR의 자기리뷰 P1).
      //   백엔드는 requested=true면 state를 in_flight로 덮어쓴다 — 그런데 요청이 소비되지
      //   않고 멈추는 일이 실제로 있다(2026-08-07 403: RG 요청이 07:55~09:31 1시간 반 pending,
      //   Mac이 꺼진 밤이면 더 길다). 예전엔 빨강 광고쿠키 배너가 시간 기준이라 backstop이
      //   됐는데 이 PR이 그걸 뺐으므로, 여기서 직접 구제하지 않으면 **양쪽 다 침묵**한다.
      //   "요청했다"는 "받았다"가 아니다.
      items.push({
        key: st.key, label: st.label, kind: "stale",
        text: `${st.label} ${ageText(st.age_hours)} · 갱신 요청 중(아직 안 받음)`,
      });
    }
  }
  if (items.length === 0) return null;
  const hasRed =
    items.some((i) => i.kind === "failed" || i.kind === "unknown") ||
    status.streams.some(
      (st) =>
        st.state === "critical" ||
        // in_flight 구제분도 48h 넘으면 빨강 — state가 in_flight라 위 조건에 안 걸린다.
        (st.state === "in_flight" &&
          staleWhileInFlight(st.age_hours) &&
          (st.age_hours == null || st.age_hours >= CRIT_HOURS)),
    );
  return { severity: hasRed ? "red" : "yellow", items };
}
