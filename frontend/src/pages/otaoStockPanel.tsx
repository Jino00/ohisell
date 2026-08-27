// otaoStockPanel.tsx — S4 「자사 현재고 (파생)」. 계약 §4 S4의 표면.
//
// 합격기준 원문: *"같은 메뉴에서 **파생 현재고**(초기 실사 + 픽업 입고 − 판매)가 보이고,
//   실사 표본 **10 SKU 대조 오차**가 화면에 표시된다."*
//
// ★★한 문장 안에 요구가 둘이고 **둘의 막힘이 다르다.** 이 화면의 설계 전부가 여기서 나온다:
//     ① 파생 현재고  → 「판매」 항이 막혔다. 발주·픽업은 OTAO 품목코드(GAPIP…) 축이고
//                      판매는 우리 SKU(OHI-…) 축인데 둘을 잇는 표가 없다(교집합 0).
//                      ⇒ **0으로 그리지 않는다.** 0으로 그리면 재고가 부풀고, 부푼 재고는
//                        「발주하지 마라」로 읽힌다 — 이 화면이 낼 수 있는 가장 비싼 거짓말이다.
//     ② 실사 대조 오차 → **안 막혔다.** 판매 축을 안 타기 때문이다. 그래서 이쪽은 끝까지 그린다.
//
// ★창고를 합치지 않는다(계약 §1 창고 5개 표). 같은 1,008개라도 「본사에 있는 것」과 「이미
//   쿠팡 제트배송에 나가 있는 것」은 발주 판단에서 정반대 의미다 — 초판 실측이 전 창고 합계를
//   내서 틀린 자리다.
//
// ★읽기 전용이다. 실사값을 여기서 «입력»받지 않는다 — 재고를 ohisell에 쓰기 시작하는 것이
//   계약 §3-1 「재고 정본 이원화」의 입구다. 적재는 사람이 실행하는 스크립트가 한다.
import { Card, Table, Th, Td, Badge, EmptyState } from "../components/ui";
import { num } from "../lib/format";
import type { OtaoStock, OtaoStockRow } from "../lib/api";

/** 역할 코드 → 사람 말. 계약 §1 표가 정본이다. */
const ROLE_LABEL: Record<string, string> = {
  own: "본사",
  material: "본사-포장(부자재)",
  channel: "쿠팡 제트배송",
  excluded: "미사용 창고",
  unknown: "역할 미상",
};

/** 「모른다」를 «모른다»로 그린다. ★`?? 0`을 쓰지 않는 것이 이 컴포넌트의 전부다. */
function Unknown({ why }: { why: string }) {
  return (
    <span className="text-gray-400" title={why}>
      —
    </span>
  );
}

/** 파생 현재고 셀 — 값이 없으면 «왜 없는지»를 말한다. 빈칸으로 두지 않는다. */
function DerivedCell({ row, reason }: { row: OtaoStockRow; reason: string | null }) {
  if (row.derived_quantity !== null) {
    return <span className="font-medium">{num(row.derived_quantity)}</span>;
  }
  if (row.derived_blocked_by === "baseline") {
    return (
      <span className="text-gray-500" title="이 상품코드가 재고 스냅샷에 없습니다 — 「재고 0」이 아닙니다.">
        기준 없음
      </span>
    );
  }
  return (
    <span
      className="text-amber-700"
      title={reason ?? "판매를 이 축에 붙일 근거가 없습니다."}
    >
      산출 불가
    </span>
  );
}

/** 대조 오차 셀 — 실사가 없으면 «미실시»다. 0이 아니다. */
function VarianceCell({ row }: { row: OtaoStockRow }) {
  if (row.counted_quantity === null) {
    return <Unknown why="실사 미실시 — 오차가 0이어서 비어 있는 것이 아닙니다." />;
  }
  if (row.variance_vs_snapshot === null) {
    return (
      <span className="text-gray-500" title="실사값은 있는데 스냅샷에 이 코드가 없어 대조가 성립하지 않았습니다.">
        대조 불가
      </span>
    );
  }
  const v = row.variance_vs_snapshot;
  const tone = v === 0 ? "text-emerald-700" : Math.abs(v) > 0 ? "text-amber-700" : "";
  return (
    <span className={`font-medium ${tone}`} title="ECOUNT가 말한 값 − 사람이 센 값">
      {v > 0 ? "+" : ""}
      {num(v)}
      {row.variance_pct !== null && (
        <span className="ml-1 text-xs text-gray-400">({row.variance_pct.toFixed(1)}%)</span>
      )}
    </span>
  );
}

export default function OtaoStockPanel({ data }: { data: OtaoStock }) {
  // ★자백 ⓪ — 「찍은 적 없다」를 0으로 그리지 않는다.
  if (data.snapshot_empty) {
    return (
      <Card title="자사 재고 스냅샷이 아직 없습니다">
        <EmptyState
          reason="ECOUNT 창고별 재고를 한 번도 적재하지 않았습니다 — 「재고 0」이 아니라 «찍은 적 없음»입니다."
          hint="허용목록에 등록된 IP에서 scripts/ecount_stock_export.py 로 받고, 서버에서 scripts/otao_stock_import.py 로 넣습니다. 이 표는 그 순간 섭니다."
        />
        {data.notes.length > 0 && (
          <ul className="mt-3 space-y-1">
            {data.notes.map((n) => (
              <li key={n} className="text-xs leading-relaxed text-gray-500">
                {n}
              </li>
            ))}
          </ul>
        )}
      </Card>
    );
  }

  const t = data.totals;
  const countedWithout = (t.counted_without_snapshot as string[] | undefined) ?? [];

  return (
    <div className="space-y-4">
      {/* ★자백 ①②③④ — 백엔드가 준 문장을 그대로 싣는다. 화면이 지어내지 않는다. */}
      {data.notes.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
          <ul className="space-y-1">
            {data.notes.map((n) => (
              <li key={n} className="text-xs leading-relaxed text-amber-900 whitespace-pre-line">
                {n}
              </li>
            ))}
          </ul>
        </div>
      )}

      <Card
        title="SKU별 파생 현재고"
        right={
          <span className="text-xs text-gray-400">
            스냅샷 {num(data.snapshot_count)}개
            {data.latest_at && <> · 최신 {data.latest_at.replace("T", " ").slice(0, 16)}</>}
            {" · "}본사 합계 {num(t.latest_own as number | null)}
          </span>
        }
      >
        {/* ★세 항을 «갈라서» 낸다. 합산 단일 숫자를 만들지 않는다(§3-9와 같은 결). */}
        <Table
          head={
            <>
              <Th>상품코드</Th>
              <Th right>기준 재고(본사)</Th>
              <Th right>+ 픽업 입고</Th>
              <Th right>− 판매</Th>
              <Th right>= 파생 현재고</Th>
              <Th right>판매 미차감 상한</Th>
              <Th right>최신 스냅샷</Th>
              <Th right>실사</Th>
              <Th right>대조 오차</Th>
            </>
          }
        >
          {data.rows.map((r) => (
            <tr key={r.product_code}>
              <Td>{r.product_code}</Td>
              <Td right>
                {r.baseline_quantity === null ? (
                  <Unknown why="이 코드가 기준 스냅샷에 없습니다 — 「재고 0」이 아닙니다." />
                ) : (
                  num(r.baseline_quantity)
                )}
              </Td>
              <Td right>{num(r.inbound_quantity)}</Td>
              <Td right>
                {/* ★이 칸이 이 화면의 급소다. 절대 0으로 그리지 않는다. */}
                <span
                  className="text-amber-700"
                  title={data.sold_unavailable_reason ?? undefined}
                >
                  근거 없음
                </span>
              </Td>
              <Td right>
                <DerivedCell row={r} reason={data.sold_unavailable_reason} />
              </Td>
              <Td right>
                {r.upper_bound_if_no_sales === null ? (
                  <Unknown why="기준 재고가 없어 상한도 못 냅니다." />
                ) : (
                  <span
                    className="text-gray-500"
                    title="판매를 «빼지 않은» 값입니다 — 현재고가 아니라 그 상한입니다."
                  >
                    ≤ {num(r.upper_bound_if_no_sales)}
                  </span>
                )}
              </Td>
              <Td right>
                {r.latest_snapshot_quantity === null ? (
                  <Unknown why="최신 스냅샷의 본사 창고에 이 코드가 없습니다." />
                ) : (
                  num(r.latest_snapshot_quantity)
                )}
              </Td>
              <Td right>
                {r.counted_quantity === null ? (
                  <Unknown why="실사 미실시" />
                ) : (
                  num(r.counted_quantity)
                )}
              </Td>
              <Td right>
                <VarianceCell row={r} />
              </Td>
            </tr>
          ))}
        </Table>
      </Card>

      {/* ★창고 역할별 분해 — 합치면 발주 판단이 화면에서 사라진다(계약 §1). */}
      <Card
        title="창고 역할별 기준 재고"
        right={
          <Badge tone={data.unknown_warehouses.length > 0 ? "alert" : "good"}>
            {data.unknown_warehouses.length > 0
              ? `역할 미상 ${data.unknown_warehouses.length}곳`
              : "역할 전건 확인"}
          </Badge>
        }
      >
        <p className="mb-3 text-xs leading-relaxed text-gray-500">
          「본사에 있는 것」과 「이미 쿠팡 제트배송에 나가 있는 것」은 발주 판단에서 정반대
          의미입니다. 파생 현재고의 기준이 되는 것은 <b>본사</b> 하나뿐이고, 나머지는 갈라서만
          보입니다.
        </p>
        <Table
          head={
            <>
              <Th>상품코드</Th>
              {Object.keys(ROLE_LABEL).map((k) => (
                <Th key={k} right>
                  {ROLE_LABEL[k]}
                </Th>
              ))}
            </>
          }
        >
          {data.rows.map((r) => (
            <tr key={r.product_code}>
              <Td>{r.product_code}</Td>
              {Object.keys(ROLE_LABEL).map((k) => {
                const v = r.baseline_by_role?.[k];
                return (
                  <Td key={k} right>
                    {v === undefined || v === null ? (
                      <span className="text-gray-300">—</span>
                    ) : (
                      <span className={k === "own" ? "font-medium" : "text-gray-500"}>
                        {num(v)}
                      </span>
                    )}
                  </Td>
                );
              })}
            </tr>
          ))}
        </Table>
        {data.unknown_warehouses.length > 0 && (
          <p className="mt-3 text-xs leading-relaxed text-amber-800">
            ⚠ 계약 §1 창고 표에 없는 이름이 있습니다 —{" "}
            {data.unknown_warehouses
              .map((w) => `${w.warehouse}(${num(w.quantity)})`)
              .join(", ")}
            . 본사 재고에 합치지 않았습니다.
          </p>
        )}
      </Card>

      {countedWithout.length > 0 && (
        <Card title="실사값은 있는데 스냅샷에 없는 코드">
          <p className="text-xs leading-relaxed text-amber-800">
            {countedWithout.join(", ")} — 대조가 «성립하지 않은» 것이지 오차가 0인 것이 아닙니다.
          </p>
        </Card>
      )}

      <p className="text-xs leading-relaxed text-gray-500">
        ★「− 판매」 칸이 <b>근거 없음</b>인 이유: {data.sold_unavailable_reason}
      </p>
    </div>
  );
}
