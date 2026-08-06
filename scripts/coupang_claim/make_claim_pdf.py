"""문의 자료 PDF 생성 (2026-08-06 v2 — 「입고 처리 확인 요청」 톤).

살아있는 Chrome(CDP 9225)에 set_content → printToPDF.
근거 사슬: ①박스 도달(한진) ②박스 열림(같은 박스 타 SKU 입고) ③정산은 입고분만(쿠팡 정산명세)
          ④결손이 센터에 붙어 있음(우리 포장 오류 가설과 모순)
"""
import csv, io, json, collections, pathlib
from playwright.sync_api import sync_playwright

SC = pathlib.Path(__file__).parent
rows = list(csv.DictReader(io.open(SC / "claim_v3.csv", encoding="utf-8-sig")))
centers = list(csv.DictReader(open(SC / "center_rates.csv", encoding="utf-8")))

g_lines, g_qty, g_amt = collections.Counter(), collections.Counter(), collections.Counter()
for r in rows:
    g = r["증거등급"]
    g_lines[g] += 1; g_qty[g] += int(r["미정산수량"]); g_amt[g] += int(r["미정산금액"])
A, B, C = "집하+도착+하차", "집하+도착", "추적없음"
po_A = len({r["발주번호"] for r in rows if r["증거등급"] == A})

# 유형 분해
T1 = [r for r in rows if r["계산서번호"]]                                    # 부분 입고
T2 = [r for r in rows if not r["계산서번호"] and "미발행" not in r["정산상태"]]  # 전량 미입고(계산서엔 있음)
T3 = [r for r in rows if "미발행" in r["정산상태"]]                            # 계산서 자체 없음
n_inv = len({i for r in csv.reader(open(SC / "po_meta.csv", encoding="utf-8"))
             for i in json.loads(r[3] or "[]")})
n_settle_lines = sum(1 for _ in csv.DictReader(open(SC / "settle_lines.csv", encoding="utf-8")))
tot_amt = sum(int(r["미정산금액"]) for r in rows)
w = lambda n: f"{n:,}"
def blk(L):
    return len(L), len({r["발주번호"] for r in L}), sum(int(r["미정산금액"]) for r in L)

top = centers[:4]
bot = centers[-3:]
tot_conf = sum(int(c["conf"]) for c in centers)
tot_gap = sum(int(c["gap"]) for c in centers)

center_rows = "".join(
    f'<tr class="hi"><td>{c["center"]}</td><td class="n">{int(c["conf"]):,}</td>'
    f'<td class="n">{int(c["gap"]):,}</td><td class="n"><b>{c["rate"]}%</b></td></tr>'
    for c in top
) + '<tr><td colspan="4" style="text-align:center;color:#777">… (중간 15개 센터 1.5%~4.5%) …</td></tr>' + "".join(
    f'<tr><td>{c["center"]}</td><td class="n">{int(c["conf"]):,}</td>'
    f'<td class="n">{int(c["gap"]):,}</td><td class="n">{c["rate"]}%</td></tr>'
    for c in bot
) + (f'<tr style="background:#eee"><td><b>전체 {len(centers)}개 센터</b></td>'
     f'<td class="n"><b>{tot_conf:,}</b></td><td class="n"><b>{tot_gap:,}</b></td>'
     f'<td class="n"><b>{tot_gap/tot_conf*100:.2f}%</b></td></tr>')

HTML = f"""
<meta charset="utf-8">
<style>
  @page {{ size: A4; margin: 13mm 13mm 11mm; }}
  body {{ font-family:'Apple SD Gothic Neo','AppleGothic',sans-serif; font-size:9.0pt;
         color:#111; line-height:1.42; }}
  h1 {{ font-size:15pt; margin:0 0 2mm; }}
  .meta {{ color:#555; font-size:8.4pt; margin-bottom:3.5mm; }}
  h2 {{ font-size:10.1pt; margin:3.4mm 0 1.2mm; padding-bottom:1mm; border-bottom:1.4px solid #222; }}
  table {{ border-collapse:collapse; width:100%; font-size:8.7pt; margin:1.5mm 0; }}
  th,td {{ border:1px solid #bbb; padding:1.2mm 2mm; }}
  th {{ background:#f1f1f1; font-weight:600; }}
  td.n {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .hi {{ background:#fff6e0; }}
  .note {{ font-size:8pt; color:#555; margin-top:1mm; }}
  table, .box {{ page-break-inside: avoid; }}
  h2 {{ page-break-after: avoid; }}
  .box {{ border:1px solid #999; background:#fafafa; padding:2.5mm 3mm; margin:2mm 0; font-size:8.7pt; }}
  ol {{ margin:1.5mm 0 1.5mm 5mm; padding:0; }}
  li {{ margin-bottom:0.8mm; }}
</style>

<h1>로켓배송(1P) 입고 처리 확인 요청</h1>
<div class="meta">주식회사 오하이테크 &nbsp;·&nbsp; 쿠팡 공급사코드 A01029796 &nbsp;·&nbsp;
작성 2026-08-06 &nbsp;·&nbsp; 대상 기간 2025-07 ~ 2026-08</div>

<h2>1. 요청 사항</h2>
<div>당사가 납품 가능으로 확정하고 발송한 물량 중 <b>귀사 입고 원장에 기록되지 않은 수량</b>이 누적돼
있습니다. 아래 두 가지를 요청드립니다.</div>
<ol>
<li><b>센터별 결손율 편차의 원인 회신</b> — 동일한 출고 절차인데 도착 센터에 따라 결손율이 최대 약 70배 차이납니다(§5).</li>
<li>확인 후 <b>미입고분 {w(g_amt[A]+g_amt[B])}원(부가세 포함)의 정산</b></li>
</ol>
<div class="box"><b>먼저 확인 부탁드립니다 — 「발주리스트」의 계산서 「정산완료」 표시에 대하여.</b><br>
해당 표시는 <b>발행된 계산서의 지급이 완료</b>되었다는 뜻이며, 발주 수량 전체가 정산되었다는 뜻이 아닙니다.
같은 화면의 「수량」 칸에 발주와 입고가 다르게 표시되는 발주가 다수 있습니다. 미입고 수량은 애초에
계산서에 포함되지 않으므로 계산서 상태로는 확인되지 않습니다.</div>

<h2>2. 금액</h2>
<table>
<tr><th>구분</th><th>발주 건</th><th>SKU 라인</th><th>수량</th><th>금액</th></tr>
<tr class="hi"><td>① 집하·센터도착·하차 모두 확인 <b>(우선 확인 요청)</b></td>
    <td class="n">{po_A}</td><td class="n">{g_lines[A]}</td>
    <td class="n">{w(g_qty[A])}개</td><td class="n"><b>{w(g_amt[A])}원</b></td></tr>
<tr><td>② 집하·센터도착까지 확인 (하차 기록 없음)</td>
    <td class="n">–</td><td class="n">{g_lines[B]}</td>
    <td class="n">{w(g_qty[B])}개</td><td class="n">{w(g_amt[B])}원</td></tr>
<tr><td><b>합계 (①+②)</b></td><td class="n">–</td><td class="n"><b>{g_lines[A]+g_lines[B]}</b></td>
    <td class="n"><b>{w(g_qty[A]+g_qty[B])}개</b></td><td class="n"><b>{w(g_amt[A]+g_amt[B])}원</b></td></tr>
</table>
<div class="note">부가세 포함(발주 라인 매입단가 × 미입고 수량). 집하 기록이 없는 {g_lines[C]}건({w(g_amt[C])}원)은
근거가 약해 제외했습니다. 전체 관측치는 {len(rows)}라인 {w(tot_amt)}원입니다.
수량은 <b>발주 수량이 아니라 당사가 납품 가능으로 확인한 수량</b> 기준이며, 납품 불가로 회신한 물량은 전액 제외했습니다.</div>

<h2>3. 미입고의 세 가지 형태 — 귀사 정산명세 대조 결과</h2>
<div>발주별로 <b>정산관리 &gt; 매입정산 &gt; 계산서번호 &gt; 입고상세내역</b>을 전수 조회(계산서 {n_inv}건 ·
정산 라인 {n_settle_lines:,}건)하여 실제 정산 수량과 대조했습니다(2026-08-06 기준).</div>
<table>
<tr><th>형태</th><th>SKU 라인</th><th>발주</th><th>금액</th></tr>
<tr><td>A. <b>부분 입고</b> — 계산서에 해당 SKU가 있으나 수량이 발송보다 적음</td>
    <td class="n">{blk(T1)[0]}</td><td class="n">{blk(T1)[1]}</td><td class="n">{w(blk(T1)[2])}원</td></tr>
<tr><td>B. <b>전량 미입고</b> — 계산서는 발행됐으나 해당 SKU 줄이 명세에 없음</td>
    <td class="n">{blk(T2)[0]}</td><td class="n">{blk(T2)[1]}</td><td class="n">{w(blk(T2)[2])}원</td></tr>
<tr><td>C. <b>계산서 미발행</b> — 발주 전체에 계산서가 발행되지 않음</td>
    <td class="n">{blk(T3)[0]}</td><td class="n">{blk(T3)[1]}</td><td class="n">{w(blk(T3)[2])}원</td></tr>
</table>
<div class="box"><b>A형 예시 — 발주 109786280</b> (2025-08-01, 고양1) · SKU 62922000 발주 602개, 당사 발송 602개.
계산서 <b>26536706</b>의 입고상세내역에는 <b>601개</b>로 기재(지급일 2025-10-02). 발주 전체로도 826개 중
825개만 정산되어 지급예정금액이 발주금액 9,620,800원이 아닌 <b>9,608,800원</b>입니다.<br>
<b>B형 예시 — 발주 114114697</b> (2025-09-24, 창원1) · 쉽먼트 39838659(1박스, 송장 460489871240)에
발주 2건이 동봉되었습니다. <b>같은 박스의 발주 114212322 14개는 전량 입고</b>되었으나,
본 발주 3개는 입고 0으로 처리되었습니다. 박스는 집하(10-01)·센터도착(10-02)·하차(10-04)가 모두 기록되어 있습니다.</div>

<h2>4. 발송 사실 근거</h2>
<table>
<tr><th>확인 사항</th><th>근거</th><th>결과</th></tr>
<tr><td>운송장 발급</td><td>한진택배 송장번호</td><td class="n">243박스 전량 (100%)</td></tr>
<tr><td>택배사 집하 / 센터 도착</td><td>한진택배 배송추적</td><td class="n">241박스 (99.2%)</td></tr>
<tr><td>귀사 센터 하차</td><td>귀사 하차 기록</td><td class="n">229박스 (94.2%)</td></tr>
<tr><td>발송 내용물 명세</td><td>귀사 발행 「내역서」 PDF 104장</td><td class="n">쉽먼트 상세와 17,818개 전량 일치</td></tr>
<tr class="hi"><td><b>박스가 열려 처리된 정황</b></td><td>귀사 입고 기록</td>
    <td class="n">대상 104박스 중 <b>95박스는 동봉된 다른 SKU가 정상 입고</b>(17,477개 중 16,234개)</td></tr>
</table>

<h2>5. ★센터별 결손율 — 동일 출고 절차, 도착지에 따라 약 70배 차이</h2>
<div>전 기간 발주를 도착 센터별로 「당사 납품확인 수량 대비 실제 입고」로 집계했습니다(누적 1,000개 이상 {len(centers)}개 센터).</div>
<table>
<tr><th>센터</th><th>납품확인 수량</th><th>미입고</th><th>결손율</th></tr>
{center_rows}
</table>
<div>당사의 포장·출고는 센터와 무관하게 동일한 인력·절차로 이루어집니다. 따라서 <b>도착 센터에 따라
결손율이 {bot[-1]['rate']}%에서 {top[0]['rate']}%까지 벌어지는 현상은 출고 측 원인으로 설명되지 않습니다.</b>
특히 <b>{top[0]['center']}·{top[1]['center']}·{top[2]['center']}</b> 3개 센터의 입고 처리 절차를 우선 확인해 주시기 바랍니다.</div>

<h2>6. 첨부</h2>
<div class="note" style="font-size:8.6pt;color:#111">
<b>01_미입고내역_정산명세대조_20260806.csv</b> — {len(rows)}라인 전체(발주번호·물류센터·SKU·발송수량·<b>쿠팡정산수량</b>·계산서번호·지급일·송장번호·집하/도착/하차 시각) &nbsp;|&nbsp;
<b>02_센터별_결손율_20260806.csv</b> &nbsp;|&nbsp;
<b>A_하차확인_*.pdf / B_*.pdf</b> — 쉽먼트별 내역서·라벨 원본</div>
"""

with sync_playwright() as p:
    br = p.chromium.connect_over_cdp("http://localhost:9225")
    page = br.contexts[0].new_page()
    try:
        page.set_content(HTML, wait_until="load")
        page.emulate_media(media="print")
        page.pdf(path=str(SC / "00_claim_20260806.pdf"), format="A4", print_background=True)
    finally:
        page.close(); br.close()
print("PDF 생성 완료")
print(f"  A형 {blk(T1)} / B형 {blk(T2)} / C형 {blk(T3)}")
print(f"  ①+② {g_amt[A]+g_amt[B]:,}원 · 전체 {tot_amt:,}원")
