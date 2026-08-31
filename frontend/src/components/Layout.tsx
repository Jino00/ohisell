// Layout.tsx — 사이드바 + 메인 영역 레이아웃
// 데스크탑: 고정 사이드바. 모바일(<md): 햄버거 → 슬라이드 드로어.
// 대시보드(전체)를 부모 메뉴로 두고, 채널별 운영(쿠팡·스마트스토어)을 접이식 자식으로 묶음.
import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { PipelineHealthBanner } from "./PipelineHealthBanner";
import SchedulerStatus from "./SchedulerStatus";
import {
  getAdCostCookieStatus,
  getSchedulerHealth,
  getCollectionStatus,
  type AdCostCookieStatus,
  type SchedulerHealth,
  type CollectionStatus,
} from "../lib/api";
import { buildCollectionFreshnessBanner } from "./collectionFreshnessBanner";
import { runStreamsRefresh, describeOutcome, specsForKeys } from "../lib/streamRefresh";

// 전역 헬스 배너 요약 빌더 (순수 함수 — 테스트 대상).
// healthy:false여도 실제로 표시할 문제가 없으면 null 반환(배너 숨김). 규칙:
//  - COUPANG_ADS1 쿠키 항목은 제외(쿠팡 광고비 배너가 전담) → 제외 후 0개면 null.
//  - disabled 버킷은 정상(의도적 비활성)이므로 문제로 세지 않음.
//  - scheduler_running===false는 최우선 표기.
//  - cost_drift(원가 정본 어긋남)는 count>0일 때만 — null/0이면 아무 말도 안 한다.
// 반환: summary=" · "로 이은 한 줄(배너 truncate용), detail=줄바꿈 목록(title 호버용).
export function buildPipelineHealthBanner(
  health: SchedulerHealth,
): { summary: string; detail: string; items: string[] } | null {
  if (health.healthy) return null;
  // ★등급은 **push 지점에서 함께 붙인다** — 완성된 산문을 나중에 정규식으로 되읽지 않는다
  //   (적대 리뷰 P1, 2026-08-19). 초판은 `rank(text)`로 문자열을 재파싱했는데, WING1/WING2
  //   쿠키만 문구가 `RG 정산 수집 중단(…) — 쿠키 재등록 필요`로 하드코딩돼 있어 `쿠키 만료`
  //   토큰에 안 걸렸고, **가장 중요한 쿠키 케이스 둘이 「잡 실패」보다 뒤로** 갔다. 같은 사건의
  //   쌍둥이 신호(`RG 정산비용이 net_profit에서 누락 중`)는 등급 0인데 그 원인이자 유일한
  //   처방이 맨 뒤에 숨는 꼴이었다. 문구는 사람이 읽으라고 바뀌는 것이고 등급은 그걸 따라가면
  //   안 된다 — 새 경고를 추가하는 사람이 등급을 **안 정하면 컴파일이 안 되게** 둔다.
  //   등급 0=돈이 조용히 샌다(화면이 비지 않아 티가 안 난다) · 1=수집/실행이 멈췄다(언젠간 티가
  //   난다) · 2=그 밖. 같은 등급 안에서는 발견 순서를 지킨다(안정 정렬).
  const parts: { r: 0 | 1 | 2; t: string }[] = [];
  const push = (r: 0 | 1 | 2, t: string) => {
    parts.push({ r, t });
  };

  // 1) 스케줄러 자체 정지 — 최우선
  if (health.scheduler_running === false) {
    push(1, "스케줄러 정지");   // 멈춤 — 화면이 비어 티가 난다
  }

  // 2) 쿠키 만료 — COUPANG_ADS1은 광고비 배너 전담이므로 제외
  for (const c of health.cookies_stale ?? []) {
    if (c.account_key === "COUPANG_ADS1") continue;
    const days = c.age_days != null ? ` (${Math.floor(c.age_days)}일째)` : "";
    let label: string;
    if (c.account_key === "COUPANG_WING1") {
      label = "RG 정산 수집 중단(오픽스) — 쿠키 재등록 필요";
    } else if (c.account_key === "COUPANG_WING2") {
      label = "RG 정산 수집 중단(오하이테크) — 쿠키 재등록 필요";
    } else {
      label = `${c.account_key} 쿠키 만료`;
    }
    // ★쿠키 만료는 돈이 새는 쪽이다: WING 쿠키가 죽으면 RG 정산 수집이 멈추고
    //   그 결과가 `net_profit 누락`(등급 0)으로 나타난다. 원인과 결과를 같은 등급에 둔다.
    push(0, label + days);
  }

  // 3) 데이터 나이 — 백엔드 impact 라벨 그대로 노출
  for (const d of health.data_stale ?? []) {
    const days = d.age_days != null ? ` (${Math.floor(d.age_days)}일째)` : "";
    // 백엔드 `impact` 라벨을 그대로 쓴다. `net_profit에서 누락`류는 돈이 새는 것(0),
    // `정체`류는 대조 상대가 낡는 것(1) — 백엔드 룰 8건 전수 대조로 확인(2026-08-19).
    push(/net_profit|누락|합 불일치/.test(d.impact) ? 0 : 1, `${d.impact}${days}`);
  }

  // 4) 원가 정본 드리프트 — `product_master.cost_price`가 «원가표 정본 + 알려진 버퍼»
  //    ★다른 항목과 종류가 다르다: 나머지는 «파이프라인이 멈췄다», 이건 «값이 틀렸다».
  //      멈춤은 화면이 비어서 티가 나지만 이건 **아무 데도 안 뜬다** — 2026-08-10까지
  //      177건이 그렇게 남아 이익을 과소 계상시켰다(전 채널 90일 1,012,405원).
  //    ★건수만 쓰고 «판정 불가»는 안 쓴다 — 배너는 한 줄이라 셋을 다 넣으면 오히려
  //      드리프트가 묻힌다. 셋 다 보려면 API 응답(cost_drift)이나 CLI를 본다.
  if (health.cost_drift && health.cost_drift.count > 0) {
    const d = health.cost_drift;
    // 버퍼 라벨을 많은 순으로 붙인다 — 어느 계열이 되돌아왔는지가 원인 추정의 첫 단서다.
    const which = Object.entries(d.by_buffer)
      .map(([label, n]) => `${label} ${n}건`)
      .join(", ");
    push(0,
      `원가가 정본과 다름 ${d.count}건${which ? ` (${which})` : ""}` +
        " — 옛 매핑 엑셀 업로드 의심",
    );
  }

  // 4-b) 원가 «가드»가 꺼졌다 — 계약 D-CPP-64 §4 S1-③ (2026-08-31).
  //    ★위 4)와 짝이지 중복이 아니다: 위는 «어긋남을 찾았다», 이건 **«찾을 수가 없었다»**.
  //      업로드 경로의 가드는 정본 스냅샷을 못 찾으면 조용히 통과하고(fail-open,
  //      ref 119 §3-2), 그때 `cost_drift`도 null이 되어 **「어긋남 0건」과 화면에서 똑같이
  //      생긴다.** 그 상태로 배너가 침묵하면 「감시 중」이라는 화면의 약속이 통째로 거짓이 된다.
  //    ★등급 0이다 — 가드가 꺼진 채 옛 엑셀이 올라오면 정확히 «돈이 조용히 새는» 경로다.
  if (health.cost_guard && health.cost_guard.active === false) {
    const why = health.cost_guard.reason;
    push(0, `원가 가드 미작동 — 스냅샷 부재${why ? ` (${why})` : ""}`);
  }

  // 5) 디스크 여유 — ★2026-08-10까지 **분기가 아예 없었다**(Jino 승인 후 추가).
  //    백엔드는 `disk_low`로 healthy=false를 만드는데 여기에 분기가 없어서, 디스크만
  //    문제인 상태에선 parts가 비어 배너가 **통째로 숨었다**. 실제로 그 상태였다 —
  //    2026-08-10 prod 실측: 사용률 93.8%(여유 5.9GB)로 healthy=false인데 화면은 조용했다.
  //    ★2026-08-03 ENOSPC 사고(디스크 포화로 서버 3시간 40분 마비, 자동수집 12개 유실)를
  //      막으려고 만든 «유일한 사전 신호»가 화면까지 이어지지 않은 채 있었다.
  //    ★백엔드가 준 impact 라벨을 그대로 쓴다(판정도 문구도 백엔드가 정본).
  for (const d of health.disk_low ?? []) {
    push(1, `디스크 여유 부족 ${d.used_percent.toFixed(1)}% — ${d.impact}`);
  }

  // 6) 판매분석 보존식 어긋남 — Σ옵션 GMV ≠ 요약축 GMV (D-CPP-36).
  //    ★cost_drift와 같은 종류다: 파이프라인은 살아 있는데 «값»이 틀렸다. 화면이 비지 않으니
  //      아무 데도 안 뜬다 — 그래서 백엔드 판정과 **같은 커밋에** 이 분기를 넣는다
  //      (disk_low가 판정에만 있고 표시가 없어 통째로 숨었던 것이 바로 이 실패다).
  //    ★summary_only는 여기서 안 쓴다 — 그건 «옵션 수집이 아직 안 온 날»이고 신선도가 본다.
  //      뭉치면 «아직»이 «틀렸다»로 보인다.
  const mismatches = health.vendor_item_conservation?.mismatch ?? [];
  if (mismatches.length > 0) {
    // 가장 큰 차액 한 건을 붙인다 — 어느 날짜·어느 유형인지가 원인 추정의 첫 단서다.
    const worst = mismatches.reduce((a, b) =>
      Math.abs(b.diff) > Math.abs(a.diff) ? b : a,
    );
    push(0,
      `판매분석 옵션↔요약 합 불일치 ${mismatches.length}건` +
        ` (최대 ${worst.date} ${worst.registration_type} ${worst.diff.toLocaleString()}원)` +
        " — 옵션별 3P 손익을 신뢰할 수 없음",
    );
  }

  // 7) 검색어 제외 조치 생존 — 우리가 네이버 콘솔에 건 제외가 아직 걸려 있는가(D-NAO-173 P1-①).
  //    ★이 분기가 없으면 disk_low와 같은 방식으로 통째로 숨는다: 백엔드는 healthy=false를
  //      만드는데(breached·stale·never_checked 중 하나라도 있으면) 여기에 분기가 없으면 parts가
  //      비어 배너가 뜨지 않는다 — 2026-08-10 disk_low가 정확히 그 상태였다(교훈 #223).
  //    ★대행사가 우리 제외 조치를 되돌린 사례가 2회이고, 그중 1회(2026-08-10 userLock 해제)는
  //      우리 change_log에 흔적이 아예 없었다 — 사건 탐지가 이미 한 번 조용히 실패했으므로
  //      여기서는 "사건"이 아니라 "지금 상태"를 매일 대조한다(exclusion_survival.py 설계 그대로).
  const es = health.exclusion_survival;
  if (es && es.healthy === false) {
    const total = es.breached_total ?? es.breached.length;
    if (total > 0) {
      const b = es.breached[0];
      const stateLabel: Record<string, string> = {
        alive: "걸려 있음", missing: "사라짐", deleted: "삭제됨(delFlag)", unknown: "확인 실패",
      };
      const label = b?.live_state ? (stateLabel[b.live_state] ?? b.live_state) : "확인 실패";
      // ★«사라짐»과 «확인 실패»를 한 문장으로 뭉치지 않는다 — 조회가 실패한 것을 소실로
      //   읽히게 하면 대응(콘솔에서 다시 걸기)이 헛돈다(적대 리뷰 P2-4).
      const allUnknown = es.breached.every((x) => x.live_state === "unknown");
      const head = allUnknown
        ? `우리가 건 검색어 제외 ${total}건을 확인하지 못함`
        : `우리가 건 검색어 제외 ${total}건이 라이브에서 어긋남`;
      push(1, head + (b?.search_term ? ` (예: "${b.search_term}" ${label})` : ""));
    } else if ((es.never_checked_due ?? 0) > 0 && !es.last_checked_at) {
      // ★«아직 안 돌았다»와 «멈췄다»는 다르다 — 마지막 대조 자체가 없으면 멈춘 게 아니라
      //   시작을 안 한 것이다. 이 구분이 NULL을 남긴 이유다(교훈 #123의 형태).
      push(1, `제외 ${es.never_checked_due}건이 아직 한 번도 대조되지 않음`);
    } else if (es.stale) {
      const lastDate = es.last_checked_at ? es.last_checked_at.slice(0, 10) : "없음";
      push(1, `제외 생존 대조가 멈춤 (마지막 대조 ${lastDate})`);
    }
  }

  // 7-b) 제외 슬롯 소진 — 조치는 살아 있는데 «더 걸 칸이 없다»(S6-a, ref 66 §5-2).
  //    ★7)과 반대 방향의 고장이다. 파이프라인도 값도 정상이고 조치도 멀쩡한데, 그룹당 70칸이
  //      다 차면 그 그룹의 음의 레버가 그 순간 소멸한다 — 다른 어떤 감시에도 안 잡힌다.
  //    ★분기를 «판정과 같은 커밋에» 넣는다(교훈 #223): 백엔드가 healthy=false를 만드는데
  //      여기에 분기가 없으면 parts가 비어 배너가 통째로 안 뜬다 — disk_low가 정확히 그랬다.
  //    ★«소진»과 «못 셌다»를 한 문장으로 뭉치지 않는다(7)의 allUnknown과 같은 규율):
  //      조회가 실패한 것을 소진으로 읽히게 하면 대응(칸 회수)이 헛돈다.
  const slots = health.exclusion_slots;
  if (slots && slots.healthy === false) {
    if (slots.exhausted > 0) {
      const worst = slots.rows.find((r) => r.state === "exhausted");
      const where = worst ? ` (예: ${worst.name || worst.adgroup_id})` : "";
      push(1,
        `제외 슬롯이 꽉 찬 광고그룹 ${slots.exhausted}개 — 그 그룹엔 더 걸 브레이크가 없음` +
          `${where} [${slots.cap}/${slots.cap}]`,
      );
    } else if (slots.unknown > 0) {
      push(1, `제외 슬롯 사용량을 확인하지 못한 광고그룹 ${slots.unknown}개 — «0칸»이 아니라 «모름»`);
    } else if (slots.stale > 0) {
      push(1, `제외 슬롯 관측이 멈춘 광고그룹 ${slots.stale}개 — 지금 값이라 말할 수 없음`);
    }
  }

  // 8) 광고비 괴리 — 쿠팡이 정산에서 뗀 광고비가 우리가 뺀 광고비를 넘는다(D-CPP-46).
  //    ★이 분기가 없으면 disk_low와 같은 방식으로 통째로 숨는다(교훈 #223): 백엔드는
  //      healthy=false를 만드는데 화면엔 아무 말이 없다. **판정과 같은 커밋에** 넣는다.
  //    ★막으려는 사고: D-CPP-43으로 정산 ad_sales 차감을 뺀 뒤, PA 수집이 멈추면 `ad_spend`가
  //      조용히 0이 되고 순이익이 그만큼 **과대계상**된다 — 화면이 비지 않으니 티가 안 난다.
  //    ★`insufficient_data`는 여기서 안 쓴다 — 그건 «못 쟀다»이고 healthy도 안 깬다.
  //      뭉치면 «못 쟀다»가 «어긋났다»로 보인다(보존식의 summary_only와 같은 규율).
  const adiv = health.ad_cost_divergence;
  if (adiv && (adiv.verdict === "diverged" || adiv.verdict === "pipe_stopped")) {
    const win = adiv.window?.start
      ? ` [${adiv.window.start}~${adiv.window.end ?? "?"}]`
      : "";
    if (adiv.verdict === "pipe_stopped") {
      push(0,
        `광고비 수집이 비었는데 정산에서는 ${adiv.settled.toLocaleString()}원이 공제됨${win}` +
          " — 순이익이 그만큼 과대계상됨",
      );
    } else {
      // 배율과 «얼마나»를 같이 준다 — 배율만으론 규모를 모르고, 금액만으론 임계를 모른다.
      const gap = adiv.settled - adiv.deducted;
      push(0,
        `광고비 괴리 ${adiv.ratio?.toFixed(3)}배 (정산 ${adiv.settled.toLocaleString()}원 vs` +
          ` 차감 ${adiv.deducted.toLocaleString()}원, 차 ${gap.toLocaleString()}원)${win}` +
          " — PA 수집 누락 의심",
      );
    }
  }

  // 9) 부분수집 — 주문 수집이 «성공»으로 끝났는데 실제로는 덜 들어왔다(D-NAO-202/204).
  //    ★이게 이 배너에 오는 이유: 다른 어떤 감시로도 안 잡힌다. 잡은 돌았고(stale 아님),
  //      상태는 success(failed 아님), 데이터도 «어제 것»이 있어(data_stale 아님) 나이로도
  //      안 걸린다. 2026-08-18에 정확히 그 상태로 주문 23건·상품매출 356,100원이 사라졌고
  //      그날 sync_log 네 회차가 전부 success였다 — **어디에도 신호가 없었다.**
  //    ★그때 신호가 없던 진짜 이유는 백엔드가 안 센 게 아니라 **화면이 안 읽은 것**이다:
  //      수집 수리(D-NAO-202) 후에도 표식은 sync_log와 로그에만 있었고 어떤 API 표면에도
  //      안 나왔다. disk_low가 판정에만 있고 표시가 없어 통째로 숨었던 것과 같은 실패다
  //      (교훈 #223) — 그래서 백엔드 판정과 **같은 커밋에** 이 분기를 넣는다.
  //    ★`detail`은 백엔드 원문 그대로 쓴다. 여기서 요약하면 «어느 날이 덜 들어왔나»가
  //      사라지는데, 그게 재수집 대상을 고르는 유일한 좌표다.
  const partials = health.partial_sync ?? [];
  if (partials.length > 0) {
    // ★`[0]`은 «최악»이 아니라 «최신»이다(백엔드 `started_at.desc()`). 이름을 정확히 쓴다 —
    //   worst라고 부르면 다음 사람이 «가장 큰 유실»로 읽고 우선순위를 잘못 판단한다.
    const latest = partials[0];
    // ★채널명은 잃지 않는다: 「외 N건」으로 접으면 다른 채널이 통째로 묻힌다(적대 리뷰 P2).
    //   같은 채널이 여러 번이면 이름은 하나로 합친다.
    const names = Array.from(new Set(partials.map((p) => p.channel_name)));
    const who = names.length > 1 ? `${names.join("·")} 주문` : `${latest.channel_name} 주문`;
    const more = partials.length > 1 ? ` (${partials.length}건)` : "";
    push(0,
      `${who}이 덜 수집됨${more}` +
        ` — 최근: ${latest.detail}` +
        " (성공으로 기록됐지만 매출이 과소계상된다)",
    );
  }

  // 10) 잡 문제 (disabled 제외 — 정상)
  const jobNames: string[] = [
    ...(health.failed ?? []).map((j) => j.job_name),
    ...(health.stale ?? []).map((j) => j.job_name),
    ...(health.never_succeeded ?? []).map((j) => j.job_name),
    ...(health.missing_jobs ?? []),
  ];
  for (const n of jobNames) {
    push(1, `잡 실패: ${n}`);
  }

  if (parts.length === 0) return null;

  // ★우선순위 정렬(D-NAO-205): 한 줄만 보이던 시절 «매출에 직접 닿는 신호»가 뒤로 밀려 화면 밖으로
  //   사라졌다(2026-08-19 실측: 경고 11건일 때 부분수집 문구가 truncate 뒤). 접기/펼치기가 생겨
  //   전건을 볼 수 있게 됐지만, **접힌 상태에서 무엇이 보이느냐**는 여전히 이 순서가 정한다.
  //   등급은 위 `push(r, …)`에서 이미 붙어 왔다 — 여기서 문자열을 되읽지 않는다.
  const items = parts
    .map((p, i) => ({ ...p, i }))
    .sort((a, b) => a.r - b.r || a.i - b.i)   // 안정 정렬: 같은 등급은 발견 순서
    .map((p) => p.t);

  return { summary: items.join(" · "), detail: items.join("\n"), items };
}

// 로켓배송(1P) 전용 화면 묶음 — 채널 아래 **한 겹 더** 접힌다 (2026-08-06, Jino).
//   왜 그룹인가: 셋 다 1P 전용인데 최상위에 흩어져 있었다. 정작 쿠팡 관련 메뉴는 대시보드
//   밑에 모여 있어 축이 어긋났고, 1P 화면이 늘어날수록 최상위가 지저분해졌다.
//   ★쿠팡 운영/광고수정과 **같은 층**에 두되 한 겹 접어, 채널(쿠팡·스마트스토어)과
//     그 안의 판매방식(1P) 그레인이 같은 줄에 섞이지 않게 한다.
type NavLinkItem = { to: string; label: string; icon: string };
type NavGroup = { label: string; icon: string; children: NavLinkItem[] };

const ROCKET_1P_GROUP: NavGroup = {
  label: "쿠팡 로켓배송(1P)",
  icon: "🚀",
  children: [
    { to: "/rocket-recon", label: "발주·정산 대사", icon: "📦" },
    { to: "/rocket-1p-revenue", label: "매출·손익(납품가 축)", icon: "💵" },
    { to: "/rocket-1p-funnel", label: "유입·전환 퍼널", icon: "🔎" },
  ],
};

// 로켓그로스(2P) 전용 화면 묶음 — 1P 그룹과 **같은 층**이다.
//   계약 `docs/contracts/CONTRACT_2p_own_screens.md`(D-CPP-54, Jino 승인 2026-08-23).
//   ★왜 최상위가 아니라 여기인가: 바로 위 2026-08-06 규칙이 「채널(쿠팡·스마트스토어)과 그 안의
//     판매방식 그레인을 같은 줄에 섞지 않는다」이고, 로켓그로스도 **쿠팡 아래 판매방식**이다.
//     최상위에 두면 그 규칙이 깨진다 — 새 축을 발명하지 않고 1P와 동형으로 둔다.
//   ★왜 이 그룹이 생겼나: 실측(2026-08-23) 전용 사이드바 메뉴 1P 4 · 네이버 2 · **RG 0**.
//     Jino 원문 *"2P에 대한 내용이 sellc의 데시보드와 서브메뉴에 전혀 안보이는게 문제"*.
//   ★1P의 4화면을 베끼지 않았다 — 1P 4화면은 1P 고유 업무(발주·납품·계산서)에서 나왔고 2P엔
//     그 업무가 없다. 세 번째 링크는 **신설이 아니라 기존 완비 화면**을 가리킨다(재고 RG 탭은
//     이미 발송관제+청구감사 둘 다 있다 — 그쪽은 오히려 1P가 「준비 중」이다).
const ROCKET_GROWTH_GROUP: NavGroup = {
  label: "쿠팡 로켓그로스(2P)",
  icon: "🌱",
  children: [
    { to: "/rocket-growth", label: "손익(판매일 축)", icon: "💵" },
    { to: "/rocket-growth/settlement", label: "정산·근거", icon: "🧾" },
    { to: "/inventory?tab=rg", label: "재고·발송관제", icon: "🏭" },
  ],
};

// 대시보드 하위 채널별 운영 패널 (접이식). 항목은 **링크이거나 그룹**이다.
//   순서가 곧 화면 순서다 — 1P·2P 그룹은 쿠팡 것들 바로 뒤, 스마트스토어 앞.
const DASHBOARD_CHILDREN: (NavLinkItem | NavGroup)[] = [
  { to: "/coupang-ops", label: "쿠팡 운영", icon: "🔧" },
  // 쿠팡 광고 설정 변경 이력(트랙 coupang-ad-change-log). 조회 전용 — 여기서 광고를 만지지 않는다.
  { to: "/coupang-ad-changes", label: "쿠팡 광고 수정", icon: "📝" },
  ROCKET_1P_GROUP,
  ROCKET_GROWTH_GROUP,
  { to: "/naver-ops", label: "스마트스토어", icon: "🛒" },
];

/** 링크 항목인가(그룹이 아니라). 그룹은 `to`가 없고 `children`을 갖는다. */
function isLink(c: NavLinkItem | NavGroup): c is NavLinkItem {
  return "to" in c;
}

// 대시보드 그룹 다음에 오는 최상위 메뉴들
const NAV_ITEMS = [
  { to: "/command-center", label: "종합 조망", icon: "🎯" },
  { to: "/orders", label: "주문 관리", icon: "📋" },
  { to: "/products", label: "상품 관리", icon: "📦" },
  { to: "/product-connection-map", label: "상품 연결맵", icon: "🔗" },
  // 로켓1P 화면 3개는 ROCKET_1P_GROUP(대시보드 하위)으로 옮겼다 — 최상위에서 제거.
  { to: "/inventory", label: "재고 관리", icon: "🏭" },
  // 발주(OTAO) — SKU별 발주 누계·픽업 누계·예약 잔량 3칸(계약 §4 S1 · D-INV-1~4).
  //   ★재고 «바로 뒤»다: 같은 물류 축이고 「창고에 무엇이 있나」 다음 질문이
  //   「OTAO에 무엇을 시켜 뒀나」이기 때문이다.
  { to: "/otao-po", label: "발주 (OTAO)", icon: "📦" },
  { to: "/import-cost", label: "수입건 원장", icon: "📥" },
  // 원가 메뉴 — D-CPP-53 / 계약 `docs/PLAN_cost-menu-standard-cost.md`.
  // 수입건 원장 바로 뒤다: 단가가 원장에서 흘러오는 순서가 곧 메뉴 순서다.
  { to: "/cost", label: "원가", icon: "💰" },
  { to: "/settlements", label: "정산 관리", icon: "💰" },
  { to: "/ad-report", label: "광고 리포트", icon: "📈" },
  { to: "/naver-ad", label: "네이버 광고", icon: "🟢" },
  // PAO 스코프 — 「어떤 캠페인·광고그룹을 엔진에 맡길지 + 그 성과」(D-NAO-244).
  //   Jino 원문 2026-08-24: *"ohisell에 PAO 메뉴를 만들어서 어떤 캠페인 - 광고그룹 을 돌릴지,
  //   그 성과는 어떻게 나오는지 보여주는 대시보드를 같이 만들자"*.
  //   ★네이버 광고 «바로 뒤»다 — 같은 채널의 화면이고, 무엇을 맡길지 고른 뒤 성과를 보는
  //     순서가 곧 메뉴 순서다. (네이버 광고 하위 8화면이 사이드바에 없는 문제는 별건 —
  //     그 그룹화는 이 계약 범위가 아니다.)
  { to: "/naver-ad/scope", label: "PAO 스코프", icon: "🎛️" },
  { to: "/settings", label: "설정", icon: "⚙️" },
];

function linkClass({ isActive }: { isActive: boolean }) {
  return `flex items-center gap-2 px-3 py-2 rounded-md text-sm mb-1 ${
    isActive ? "bg-blue-50 text-blue-700 font-medium" : "text-gray-600 hover:bg-gray-100"
  }`;
}

export default function Layout() {
  const location = useLocation();
  // ★1P 하위그룹도 "채널 메뉴 안"이다 — 여기에 안 넣으면 /rocket-1p-funnel로 딥링크했을 때
  //   메뉴가 접힌 채 열려 현재 위치를 알 수 없다(2026-08-06 그룹 신설 시 같이 잡음).
  const rocketActive = ROCKET_1P_GROUP.children.some((c) => location.pathname === c.to);
  const childActive =
    DASHBOARD_CHILDREN.some((c) => isLink(c) && location.pathname === c.to) || rocketActive;
  const [open, setOpen] = useState(childActive || location.pathname === "/");
  const [rocketOpen, setRocketOpen] = useState(rocketActive);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [adCookie, setAdCookie] = useState<AdCostCookieStatus | null>(null);
  const [health, setHealth] = useState<SchedulerHealth | null>(null);
  const [collection, setCollection] = useState<CollectionStatus | null>(null);
  // 수집 신선도 배너 '지금 갱신' 상태 — 2026-08-03까지 이 자리는 링크였고, 눌러도 페이지만
  // 바뀌어 "아무 일도 안 일어난다"로 보였다(Jino 보고). 이제 실제 갱신을 돌린다.
  const [collRefreshing, setCollRefreshing] = useState(false);
  const [collRefreshMsg, setCollRefreshMsg] = useState<string | null>(null);
  // 갱신은 최대 215초 걸린다 — 그 사이 언마운트되거나 사용자가 다시 누를 수 있다. 마운트
  // 플래그 + 실행 세대로 늦은 콜백의 setState를 막고, 소거 타이머도 정리한다(codex R1[P2]).
  const collMounted = useRef(true);
  const collRunSeq = useRef(0);
  const collClearTimer = useRef<number | null>(null);
  useEffect(() => {
    collMounted.current = true;
    return () => {
      collMounted.current = false;
      if (collClearTimer.current !== null) window.clearTimeout(collClearTimer.current);
    };
  }, []);

  // 채널 페이지로 직접 진입하면 그룹을 자동으로 펼침
  useEffect(() => {
    if (childActive) setOpen(true);
  }, [childActive]);

  // 광고쿠키 만료 감지 — 페이지 진입/이동마다 재확인.
  // 접속 시 realtime sync가 만료(302)를 감지해 status를 red로 바꾸므로, 6초 뒤 한 번 더 확인.
  useEffect(() => {
    let cancelled = false;
    const fetchStatus = () => {
      getAdCostCookieStatus()
        .then((s) => { if (!cancelled) setAdCookie(s); })
        .catch(() => { /* 조용히 실패 — 배너만 미표시 */ });
    };
    fetchStatus();
    const t = setTimeout(fetchStatus, 6000);
    return () => { cancelled = true; clearTimeout(t); };
  }, [location.pathname]);

  // 파이프라인 헬스 — 전역 5분 폴링(경로와 무관하게 상주).
  // 실패 시 조용히 무시 → 네트워크 오류로 배너를 잘못 띄우지 않음(오탐 금지).
  useEffect(() => {
    let cancelled = false;
    const fetchHealth = () => {
      getSchedulerHealth()
        .then((h) => { if (!cancelled) setHealth(h); })
        .catch(() => { /* 조용히 실패 — 배너 미표시 */ });
    };
    fetchHealth();
    const t = setInterval(fetchHealth, 5 * 60 * 1000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  // 쿠팡 수집 신선도 — 전역 60초 폴링. 자동 트리거 제거 후 '낡음/실패'를 여기서만 가시화.
  // 실패 시 조용히 무시(fail-safe) → 네트워크 오류로 배너를 잘못 띄우지 않고 앱도 안 죽는다.
  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      getCollectionStatus()
        .then((c) => { if (!cancelled) setCollection(c); })
        .catch(() => { /* 조용히 실패 — 배너 미표시 */ });
    };
    tick();
    const t = setInterval(tick, 60 * 1000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  // 배너 '지금 갱신' — 낡은/실패한 스트림 전부를 한 번에 갱신한다.
  //  ★계정별 개별 선택은 커맨드센터가 담당한다(같은 UI를 배너에 복제하지 않는다 — 한쪽만
  //    고쳐지는 사고를 만들지 않기 위해). 배너의 일은 "배너를 없애는 것"이므로 전건 갱신이다.
  //  ★로그인이 필요한 스트림은 계정명과 함께 남긴다 — 로그인은 버튼이 대신할 수 없고,
  //    어느 계정인지 모르면 창을 찾지 못한다(2026-08-03 실측: 4개 중 2개가 rc=3 로그인 필요).
  async function handleCollectionRefresh(keys: string[]) {
    const { specs, unknown } = specsForKeys(keys);
    // ★못 알아본 key를 침묵시키지 않는다(codex R1[P2]): 백엔드가 스트림을 추가·개명하면
    //   매칭이 0건이 되는데, 조용히 return하면 버튼이 다시 "눌러도 아무 일 없는" 물건이 된다.
    const unknownNote = unknown.length ? `⚠️ 미지원 항목 ${unknown.join(", ")}(갱신 불가)` : "";
    if (specs.length === 0) {
      setCollRefreshMsg(unknownNote || "⚠️ 갱신할 수 있는 항목이 없습니다");
      return;
    }
    // 실행 세대 — 이전 실행의 늦은 콜백이 새 실행의 문구를 덮어쓰는 것을 막는다(codex R1[P2]).
    const gen = ++collRunSeq.current;
    const alive = () => collMounted.current && gen === collRunSeq.current;
    const withNote = (body: string) => [body, unknownNote].filter(Boolean).join(" · ");

    setCollRefreshing(true);
    setCollRefreshMsg(withNote(specs.map((s) => describeOutcome(s, null)).join(" · ")));
    try {
      const results = await runStreamsRefresh(specs, (_spec, _outcome, all) => {
        // 한 건이 정착할 때마다 문구를 갱신 — 느린 스트림이 빠른 스트림을 인질로 잡지 않는다.
        if (!alive()) return;
        setCollRefreshMsg(
          withNote(specs.map((s) => describeOutcome(s, all.get(s.key) ?? null)).join(" · ")),
        );
      });
      if (!alive()) return;
      const allDone = specs.every((s) => results.get(s.key)?.state === "done") && !unknown.length;
      // 완료분을 배너에서 즉시 걷어내기 위해 신선도를 다시 읽는다(60초 폴링을 기다리지 않음).
      // ★재조회 실패를 삼키지 않는다(codex R1[P2]): 삼키면 문구만 사라지고 낡은 배너가 이유
      //   없이 되돌아온다 — 갱신이 실패한 것처럼 보인다. 실패하면 문구를 남기고 이유를 적는다.
      getCollectionStatus()
        .then((c) => {
          if (!alive()) return;
          setCollection(c);
          // 전건 성공일 때만 문구 자동 소거 — 실패/로그인 필요는 남겨서 Jino가 보게 한다.
          if (allDone) {
            collClearTimer.current = window.setTimeout(() => {
              if (alive()) setCollRefreshMsg(null);
            }, 4000);
          }
        })
        .catch(() => {
          if (!alive()) return;
          setCollRefreshMsg((prev) => `${prev ?? ""} · ⚠️ 상태 재조회 실패(잠시 후 자동 갱신)`);
        });
    } catch (e: any) {
      if (alive()) setCollRefreshMsg("❌ 갱신 요청 실패: " + (e?.message || ""));
    } finally {
      if (alive()) setCollRefreshing(false);
    }
  }

  // 경로 이동 시 모바일 드로어 닫기
  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  const healthBanner = health ? buildPipelineHealthBanner(health) : null;
  const collectionBanner = buildCollectionFreshnessBanner(collection);

  return (
    <div className="flex h-screen bg-gray-50">
      {/* 모바일 드로어 배경 */}
      {mobileOpen && (
        <div
          className="fixed inset-0 bg-black/30 z-30 md:hidden"
          onClick={() => setMobileOpen(false)}
          aria-hidden
        />
      )}

      {/* 사이드바: 데스크탑 고정 / 모바일 슬라이드 드로어 */}
      <aside
        className={`fixed inset-y-0 left-0 z-40 w-64 bg-white border-r border-gray-200 flex flex-col
          transform transition-transform duration-200 md:static md:w-56 md:translate-x-0 md:z-auto
          ${mobileOpen ? "translate-x-0" : "-translate-x-full"}`}
      >
        <div className="p-4 border-b border-gray-200 flex items-center justify-between">
          <div>
            <h1 className="text-lg font-bold text-gray-900">ohisell</h1>
            <p className="text-xs text-gray-500">오픈쇼핑몰 실적 관리</p>
          </div>
          <button
            type="button"
            onClick={() => setMobileOpen(false)}
            className="md:hidden text-gray-400 hover:text-gray-700 text-xl leading-none px-1"
            aria-label="메뉴 닫기"
          >
            ✕
          </button>
        </div>
        <nav className="flex-1 p-2 overflow-y-auto">
          {/* 대시보드(전체) — 클릭=전체 종합, ▾로 채널별 운영 펼침/접기 */}
          <div className="flex items-center mb-1">
            <NavLink
              to="/"
              end
              className={({ isActive }) =>
                `flex-1 flex items-center gap-2 px-3 py-2 rounded-md text-sm ${
                  isActive ? "bg-blue-50 text-blue-700 font-medium" : "text-gray-600 hover:bg-gray-100"
                }`
              }
            >
              <span>📊</span>
              대시보드
            </NavLink>
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              aria-label={open ? "채널 메뉴 접기" : "채널 메뉴 펼치기"}
              aria-expanded={open}
              className="px-2 py-2 text-gray-400 hover:text-gray-700 text-xs"
            >
              {open ? "▾" : "▸"}
            </button>
          </div>
          {open && (
            <div className="ml-3 border-l border-gray-200 pl-2 mb-1">
              {DASHBOARD_CHILDREN.map((c) =>
                isLink(c) ? (
                  <NavLink key={c.to} to={c.to} className={linkClass}>
                    <span>{c.icon}</span>
                    {c.label}
                  </NavLink>
                ) : (
                  // 로켓배송(1P) — 채널과 같은 층에 두되 한 겹 더 접는다.
                  // ★그룹 머리는 링크가 아니라 **토글**이다: 셋 중 무엇을 대표 화면으로 삼을지
                  //   근거가 없고, 임의로 하나를 골라 링크하면 나머지 둘이 이등 시민이 된다.
                  <div key={c.label}>
                    <button
                      type="button"
                      onClick={() => setRocketOpen((o) => !o)}
                      aria-expanded={rocketOpen}
                      aria-label={rocketOpen ? "로켓배송 메뉴 접기" : "로켓배송 메뉴 펼치기"}
                      className={`w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm mb-1 ${
                        rocketActive ? "text-blue-700 font-medium" : "text-gray-600 hover:bg-gray-100"
                      }`}
                    >
                      <span>{c.icon}</span>
                      <span className="flex-1 text-left">{c.label}</span>
                      <span className="text-xs text-gray-400">{rocketOpen ? "▾" : "▸"}</span>
                    </button>
                    {rocketOpen && (
                      <div className="ml-3 border-l border-gray-200 pl-2">
                        {c.children.map((sub) => (
                          <NavLink key={sub.to} to={sub.to} className={linkClass}>
                            <span>{sub.icon}</span>
                            {sub.label}
                          </NavLink>
                        ))}
                      </div>
                    )}
                  </div>
                ),
              )}
            </div>
          )}

          {/* 나머지 최상위 메뉴 */}
          {NAV_ITEMS.map((item) => (
            <NavLink key={item.to} to={item.to} className={linkClass}>
              <span>{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <SchedulerStatus />
      </aside>

      {/* 메인 영역 */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* 모바일 상단바 (햄버거) */}
        <header className="md:hidden flex items-center gap-3 bg-white border-b border-gray-200 px-4 py-3 sticky top-0 z-20">
          <button
            type="button"
            onClick={() => setMobileOpen(true)}
            className="text-gray-700 text-2xl leading-none"
            aria-label="메뉴 열기"
          >
            ☰
          </button>
          <span className="text-base font-bold text-gray-900">ohisell</span>
        </header>

        {/* 광고쿠키 배너 — ★"버튼으로 안 되는 것"만 담는다 (2026-08-07).
            여기 있던 stale 갈래(페처 지연 → '지금 갱신')를 들어냈다. 이유: stale은
            `cookie_status.last_success_at`로 판정하는데, 아래 수집 신선도 배너의 `ofix_ad`도
            **같은 행·같은 필드**(ad_cost_sync._cookie_row(db).last_success_at)를 읽는다 —
            같은 사실을 두 배너가 각자 판정해 같은 화면에 두 줄로 떴다(2026-08-07 실측:
            양쪽 last_success_at가 `2026-08-07T07:53:18.036504`로 완전 동일).
            ★가시성 구멍 없음: 신선도 배너가 24h(warn)에 먼저 뜨고 여기 stale은 26h였다.
              단 26~48h 구간의 색은 빨강 → 노랑으로 내려간다(신선도 배너의 균일 규칙을 따름).
            남은 두 갈래(크론 꺼짐·쿠키 만료)는 갱신 버튼으로 해결되지 않고 각자 다른 처방이
            필요하다 → 이 배너는 '이동' 전용이 되고, '액션'은 신선도 배너 한 곳에만 있다. */}
        {/* 크론 꺼짐 > red 우선순위: 크론이 꺼져 있으면 쿠키가 멀쩡해도 push가 안 와 재설정은 헛수고 */}
        {(adCookie?.refresh_cron_enabled === false || adCookie?.status === "red") && (
          <div className="flex items-center gap-3 bg-red-600 text-white px-4 py-2 text-sm">
            <span className="font-semibold shrink-0">🔴 쿠팡 광고비 수집 중단</span>
            {adCookie?.refresh_cron_enabled === false ? (
              <>
                <span className="text-red-100 min-w-0 truncate">
                  갱신 크론 꺼짐(스케줄러에서 재개 필요)
                  {adCookie.last_success_at && ` (마지막 수집 ${adCookie.last_success_at.slice(0, 10)})`}.
                </span>
                <Link
                  to="/coupang-ops"
                  className="ml-auto shrink-0 bg-white text-red-700 font-medium px-3 py-1 rounded hover:bg-red-50"
                >
                  스케줄러 관리 →
                </Link>
              </>
            ) : (
              // 쿠키 만료 → 재설정 폼으로 (Mac이 fetch해도 인증 실패하므로 갱신 요청은 무의미)
              <>
                <span className="text-red-100 min-w-0 truncate">
                  광고비 수집이 멈췄습니다 — 쿠키 만료(재설정 필요)
                  {adCookie?.last_success_at && ` (마지막 수집 ${adCookie.last_success_at.slice(0, 10)})`}.
                </span>
                <Link
                  to="/coupang-ops?adcookie=open"
                  className="ml-auto shrink-0 bg-white text-red-700 font-medium px-3 py-1 rounded hover:bg-red-50"
                >
                  쿠키 다시 설정 →
                </Link>
              </>
            )}
          </div>
        )}

        {/* 파이프라인 헬스 전역 경고 — /api/scheduler/health의 healthy:false 표면화.
            광고비 배너(위)와 별개·동시 표시 가능. COUPANG_ADS1 쿠키는 위 배너 전담이라 제외됨. */}
        {healthBanner && <PipelineHealthBanner items={healthBanner.items} />}

        {/* 쿠팡 수집 신선도 전역 배너 — 자동 트리거 제거(순수 on-demand) 후 '낡음/실패'를 표면화.
            빨강=48h↑ 낡음 or 갱신 실패(로그인 필요), 노랑=24~48h 낡음. 항목 클릭 → 종합조망에서 갱신. */}
        {collectionBanner && (
          <div
            className={`flex items-center gap-3 px-4 py-2 text-sm text-white ${
              collectionBanner.severity === "red" ? "bg-rose-600" : "bg-amber-500"
            }`}
          >
            <span className="font-semibold shrink-0">🕒 수집 신선도</span>
            <span className="min-w-0 truncate" title={collRefreshMsg ?? undefined}>
              {collRefreshMsg ?? collectionBanner.items.map((it, i) => (
                <span key={it.key}>
                  {i > 0 && " · "}
                  {it.text}
                </span>
              ))}
            </span>
            <button
              onClick={() => handleCollectionRefresh(collectionBanner.items.map((it) => it.key))}
              disabled={collRefreshing}
              className={`ml-auto shrink-0 bg-white font-medium px-3 py-1 rounded hover:bg-gray-50 disabled:opacity-60 ${
                collectionBanner.severity === "red" ? "text-rose-700" : "text-amber-700"
              }`}
            >
              {collRefreshing ? "갱신 중…" : "지금 갱신 →"}
            </button>
            <Link
              to="/command-center"
              className="shrink-0 underline decoration-white/50 hover:decoration-white text-xs opacity-90"
            >
              개별 갱신
            </Link>
          </div>
        )}

        <main className="flex-1 overflow-auto p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
