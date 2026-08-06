"""문의 자료 A4 1장 PDF 생성 (2026-08-06판 — 귀사 정산명세 대조 근거 추가).

살아있는 Chrome(CDP 9225)에 set_content → printToPDF. 한글 폰트는 시스템 폰트 사용.
"""
import csv, io, collections, pathlib
from playwright.sync_api import sync_playwright

SC = pathlib.Path(__file__).parent
rows = list(csv.DictReader(io.open(SC / "claim_v3.csv", encoding="utf-8-sig")))

g_lines, g_qty, g_amt = collections.Counter(), collections.Counter(), collections.Counter()
for r in rows:
    g = r["증거등급"]
    g_lines[g] += 1
    g_qty[g] += int(r["미정산수량"])
    g_amt[g] += int(r["미정산금액"])

n_inv = len({i for r in csv.reader(open(SC/'po_meta.csv',encoding='utf-8')) for i in __import__('json').loads(r[3] or '[]')})
n_settle_lines = sum(1 for _ in csv.DictReader(open(SC/'settle_lines.csv',encoding='utf-8')))
A, B, C = "집하+도착+하차", "집하+도착", "추적없음"
po_A = len({r["발주번호"] for r in rows if r["증거등급"] == A})
noinv = [r for r in rows if "미발행" in r["정산상태"]]
ech = [r for r in rows if r["물류센터"] == "이천4"]
tot_amt = sum(int(r["미정산금액"]) for r in rows)
w = lambda n: f"{n:,}"

HTML = f"""
<meta charset="utf-8">
<style>
  @page {{ size: A4; margin: 14mm 14mm 12mm; }}
  body {{ font-family: 'Apple SD Gothic Neo','AppleGothic',sans-serif; font-size: 9.0pt;
         color:#111; line-height:1.42; }}
  h1 {{ font-size: 15pt; margin:0 0 2mm; }}
  .meta {{ color:#555; font-size:8.4pt; margin-bottom:4mm; }}
  h2 {{ font-size:10.1pt; margin:3.2mm 0 1.2mm; padding-bottom:1mm;
       border-bottom:1.4px solid #222; }}
  table {{ border-collapse:collapse; width:100%; font-size:8.7pt; margin:1.5mm 0; }}
  th,td {{ border:1px solid #bbb; padding:1.2mm 2mm; }}
  th {{ background:#f1f1f1; font-weight:600; }}
  td.n {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .hi {{ background:#fff6e0; font-weight:600; }}
  .note {{ font-size:8pt; color:#555; margin-top:1mm; }}
  .box {{ border:1px solid #999; background:#fafafa; padding:2.5mm 3mm; margin:2mm 0; font-size:8.7pt; }}
</style>

<h1>로켓배송(1P) 미정산 건 — 문의 자료</h1>
<div class="meta">주식회사 오하이테크 &nbsp;·&nbsp; 쿠팡 공급사코드 A01029796 &nbsp;·&nbsp;
작성 2026-08-06 &nbsp;·&nbsp; 대상 기간 2025-07 ~ 2026-08</div>

<h2>1. 문의 내용</h2>
<div>귀사 발주에 대해 당사가 납품을 확정하고 실제로 발송했으나, 귀사 입고 원장에 입고가 기록되지 않아
<b>귀사 정산명세(입고상세내역)에도 포함되지 않은</b> 수량이 누적돼 있습니다. 귀사는 입고 수량을 기준으로
세금계산서를 발행하므로, 입고가 잡히지 않으면 대금 청구 자체가 발생하지 않습니다.
확인 및 정산을 요청드립니다.</div>

<h2>2. 금액</h2>
<table>
<tr><th>구분</th><th>발주 건</th><th>SKU 라인</th><th>수량</th><th>금액</th></tr>
<tr class="hi"><td>① 집하·센터도착·하차 모두 확인 <b>(1차 청구 권장)</b></td>
    <td class="n">{po_A}</td><td class="n">{g_lines[A]}</td>
    <td class="n">{w(g_qty[A])}개</td><td class="n">{w(g_amt[A])}원</td></tr>
<tr><td>② 집하·센터도착까지 확인 (하차 기록 없음)</td>
    <td class="n">–</td><td class="n">{g_lines[B]}</td>
    <td class="n">{w(g_qty[B])}개</td><td class="n">{w(g_amt[B])}원</td></tr>
<tr><td><b>합계 (①+②)</b></td><td class="n">–</td>
    <td class="n"><b>{g_lines[A]+g_lines[B]}</b></td>
    <td class="n"><b>{w(g_qty[A]+g_qty[B])}개</b></td>
    <td class="n"><b>{w(g_amt[A]+g_amt[B])}원</b></td></tr>
</table>
<div class="note">금액은 부가세 포함(발주 라인 매입단가 × 미정산 수량). 집하 기록이 없는 {g_lines[C]}건
({w(g_amt[C])}원)은 근거가 약해 청구 대상에서 제외했습니다. 전체 관측치는 {len(rows)}라인 {w(tot_amt)}원입니다.</div>

<h2>3. 근거 ― 귀사 정산명세와 라인 단위 대조</h2>
<div>각 발주에 대해 <b>정산관리 &gt; 매입정산 &gt; 계산서번호 &gt; 입고상세내역</b>을 전수 조회하여
귀사가 실제로 정산한 수량과 당사 발송 수량을 SKU 라인 단위로 대조했습니다(2026-08-06 조회).</div>
<table>
<tr><th>확인 사항</th><th>결과</th></tr>
<tr><td>조회한 계산서</td><td class="n">{n_inv}건 · 정산 라인 {n_settle_lines:,}건 전수</td></tr>
<tr><td>정산 수량 &lt; 발송 수량인 라인</td><td class="n">{len(rows)}라인 · 차이 {w(sum(int(r['미정산수량']) for r in rows))}개</td></tr>
<tr class="hi"><td>계산서가 발행되지 않은 발주</td>
    <td class="n">{len({r['발주번호'] for r in noinv})}건 · {len(noinv)}라인 · {w(sum(int(r['미정산금액']) for r in noinv))}원</td></tr>
</table>
<div class="box"><b>예시 — 발주 109786280</b> (2025-08-01 발주, 고양1)<br>
SKU 62922000(Z폴드7 필름) 발주 602개, 당사 발송 602개. 계산서 <b>26536706</b>의 입고상세내역에는
<b>601개 7,212,000원</b>으로 기재(지급일 2025-10-02). 발주 전체로는 826개 중 825개만 정산되어
지급예정금액이 발주금액 9,620,800원이 아닌 <b>9,608,800원</b>입니다. 차액 <b>1개 12,000원</b>이 미정산입니다.</div>

<h2>4. 발송 사실 근거 (택배사·귀사 기록)</h2>
<table>
<tr><th>확인 사항</th><th>근거</th><th>결과</th></tr>
<tr><td>운송장 발급</td><td>한진택배 송장번호</td><td class="n">243박스 전량 (100%)</td></tr>
<tr><td>택배사 집하 / 센터 도착</td><td>한진택배 배송추적</td><td class="n">241박스 (99.2%)</td></tr>
<tr><td>쿠팡 센터 하차</td><td>귀사 하차 기록</td><td class="n">229박스 (94.2%)</td></tr>
<tr><td>발송 내용물 명세</td><td>귀사 발행 「내역서」 PDF 104장</td><td class="n">쉽먼트 상세와 17,818개 전량 일치</td></tr>
</table>

<h2>5. 특이사항 ― 이천4 센터 편중</h2>
<div>미정산 {w(tot_amt)}원 중 <b>이천4 센터가 {len({r['발주번호'] for r in ech})}개 발주 {len(ech)}라인
{w(sum(int(r['미정산금액']) for r in ech))}원({sum(int(r['미정산금액']) for r in ech)/tot_amt*100:.1f}%)</b>을 차지합니다.
해당 센터의 미입고율은 7.82%로 2위 센터(1.59%)의 약 5배이며, 부분 손실이 아니라 <b>입고 처리 자체가
누락된 건</b>의 비중이 현저히 높습니다. 2025-07-31 ~ 2026-06-01에 걸쳐 산발적으로 계속되고 있어
개별 건이 아닌 센터 단위 확인을 요청드립니다.</div>

<h2>6. 첨부</h2>
<div class="note" style="font-size:8.6pt;color:#111">
<b>01_미정산내역_정산명세대조_20260806.csv</b> — {len(rows)}라인 전체(발주번호·물류센터·SKU·발송수량·<b>쿠팡정산수량</b>·계산서번호·지급일·송장번호·집하/도착/하차 시각) &nbsp;|&nbsp;
<b>02_이천4_별건_20260806.csv</b> — 이천4 {len(ech)}라인 &nbsp;|&nbsp;
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
print("PDF 생성 완료:", SC / "00_claim_20260806.pdf")
