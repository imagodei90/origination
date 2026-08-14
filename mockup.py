# -*- coding: utf-8 -*-
r"""디자인 시안 — 같은 실데이터에 3개 레이아웃을 얹어 상단 토글로 비교"""
import os, sys, io, json, html, sqlite3
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "origination.db")
OUT = os.path.join(BASE, "out"); os.makedirs(OUT, exist_ok=True)
YEARS = [2020, 2021, 2022, 2023, 2024, 2025]
CAT_COLOR = {"자금조달": "fu", "자본개편": "rc", "자기주식": "ts", "자산거래": "as", "위험신호": "rk"}


def eok(v):
    if v is None:
        return "–"
    a = v / 1e8
    if abs(a) >= 10000:
        return f"{a/10000:,.1f}조"
    return f"{a:,.0f}"


def pct(v, d=1):
    return "–" if v is None else f"{v*100:.{d}f}%"


def trend_of(con, cc):
    """fin 테이블에서 연도별 core 지표"""
    out = {y: {} for y in YEARS}
    q = "SELECT fy,account,fs_div,amount FROM fin WHERE corp_code=? AND fy>=? AND fy<=?"
    for fy, acc, fsd, amt in con.execute(q, (cc, YEARS[0], YEARS[-1])):
        slot = out.setdefault(fy, {})
        if acc not in slot or (fsd == "CFS" and slot[acc][0] != "CFS"):
            slot[acc] = (fsd, amt)
    res = {}
    for y in YEARS:
        m = out.get(y, {})
        g = lambda k: m.get(k, (None, None))[1]
        rev, op = g("revenue"), g("op")
        res[y] = {"revenue": rev, "op": op, "assets": g("assets"), "equity": g("equity"),
                  "liab": g("liab"), "ni": g("ni"),
                  "margin": (op / rev) if (rev and op is not None) else None}
    return res


def spark(vals):
    """▁▂▃▅▆▇ 스파크라인"""
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return ""
    lo, hi = min(v), max(v)
    rng = (hi - lo) or 1
    bars = "▁▂▃▄▅▆▇"
    return "".join(bars[min(int((x - lo) / rng * 6), 6)] if x is not None else " " for x in vals)


def load(con, n=12):
    rid = con.execute("SELECT MAX(run_id) FROM tier1").fetchone()[0]
    q = """SELECT t.corp_code,c.corp_name,c.stock_code,t.score,t.payload
           FROM tier1 t JOIN corp c ON c.corp_code=t.corp_code
           WHERE t.run_id=? ORDER BY t.score DESC LIMIT ?"""
    rows = []
    for cc, nm, sk, sc, pl in con.execute(q, (rid, n)):
        p = json.loads(pl)
        ev = [dict(zip(["dt", "cat", "title", "amended"], r)) for r in con.execute(
            "SELECT rcept_dt,cat,title,amended FROM event WHERE corp_code=? ORDER BY rcept_dt DESC LIMIT 8",
            (cc,))]
        rows.append({"code": cc, "name": nm, "stock": sk, "score": sc,
                     "p": p, "tr": trend_of(con, cc), "ev": ev})
    return rows


def ev_html(evs, compact=False):
    if not evs:
        return '<div class="noev">최근 12개월 자금조달·자산거래 공시 없음</div>'
    out = []
    for e in evs[:8 if not compact else 3]:
        d = e["dt"]
        ds = f"{d[2:4]}.{d[4:6]}.{d[6:8]}" if d and len(d) == 8 else d
        am = '<span class="am">정정</span>' if e["amended"] else ""
        out.append(f'<div class="ev"><span class="evd">{ds}</span>'
                   f'<span class="tag {CAT_COLOR.get(e["cat"],"")}">{html.escape(e["cat"])}</span>'
                   f'<span class="evt">{html.escape(e["title"])}</span>{am}</div>')
    return "".join(out)


def trend_table(tr):
    ths = "".join(f"<th>'{str(y)[2:]}</th>" for y in YEARS)
    def row(lb, key, fmt):
        tds = "".join(f"<td>{fmt(tr[y].get(key))}</td>" for y in YEARS)
        return f"<tr><th>{lb}</th>{tds}</tr>"
    return f"""<table class="tt"><thead><tr><th></th>{ths}</tr></thead><tbody>
{row('매출', 'revenue', eok)}{row('영업이익', 'op', eok)}{row('영업마진', 'margin', pct)}
{row('자산', 'assets', eok)}{row('부채', 'liab', eok)}{row('자본', 'equity', eok)}
</tbody></table>"""


def detail_grid(p):
    def g(lb, v):
        return f'<div><i>{lb}</i><b>{v}</b></div>'
    icr = "영업적자" if p.get("op_loss") else ("–" if p.get("icr") is None else f'{p["icr"]:.1f}x')
    return ('<div class="dg">' +
            g("총차입금", eok(p.get("debt")) if p.get("debt_found") else "미확보") +
            g("순차입금", eok(p.get("net_debt"))) + g("현금성", eok(p.get("cash"))) +
            g("OCF", eok(p.get("ocf"))) + g("FCF", eok(p.get("fcf"))) +
            g("이자보상", icr) +
            g("AR일수", "–" if p.get("ar_days") is None else f'{p["ar_days"]:.0f}일') +
            g("재고일수", "–" if p.get("inv_days") is None else f'{p["inv_days"]:.0f}일') +
            g("매출성장", pct(p.get("rev_growth"))) + '</div>')


def build(rows, path):
    def layoutA():
        items = []
        for r in rows:
            tr, p = r["tr"], r["p"]
            sp = spark([tr[y].get("revenue") for y in YEARS])
            spm = spark([tr[y].get("margin") for y in YEARS])
            nev = len(r["ev"])
            items.append(f"""
<details class="row"><summary>
  <span class="nm">{html.escape(r['name'])}</span><span class="cd">{r['stock']}</span>
  <span class="sz">자산 {eok(tr[2025].get('assets') or p.get('assets'))}억 · 매출 {eok(tr[2025].get('revenue'))}억</span>
  <span class="sk">{sp}</span>
  <span class="mg">마진 {pct(tr[2025].get('margin'))}</span>
  {'<span class="evn">공시 '+str(nev)+'</span>' if nev else ''}
</summary>
<div class="body">
  <div class="lbl">재무 추이 <em>단위 억원</em></div>
  {trend_table(tr)}
  <div class="lbl">최근 지표 (FY{p.get('fy')})</div>
  {detail_grid(p)}
  <div class="lbl">최근 12개월 자본활동</div>
  {ev_html(r['ev'])}
  <div class="acts"><a href="https://dart.fss.or.kr/dsab007/main.do?textCrpNm={html.escape(r['name'])}" target="_blank">DART</a>
  <a href="#">엑셀 생성</a><a href="#">노트</a></div>
</div></details>""")
        return f"""<div class="chips"><b>정렬</b>
<span class="chip on">자본활동 많은순</span><span class="chip">매출 역성장</span>
<span class="chip">마진 하락폭</span><span class="chip">차입금 증가</span><span class="chip">FCF</span></div>
{''.join(items)}"""

    def layoutB():
        evs = []
        for r in rows:
            for e in r["ev"]:
                evs.append((e["dt"], r["name"], r["stock"], e["cat"], e["title"]))
        evs.sort(reverse=True)
        tl = "".join(
            f'<div class="ev2"><span class="evd">{d[2:4]}.{d[4:6]}.{d[6:8]}</span>'
            f'<span class="nm2">{html.escape(n)}</span>'
            f'<span class="tag {CAT_COLOR.get(c,"")}">{html.escape(c)}</span>'
            f'<span class="evt">{html.escape(t)}</span></div>' for d, n, s, c, t in evs[:24])
        tb = "".join(
            f"<tr><td class='l'>{html.escape(r['name'])}</td><td>{r['stock']}</td>"
            f"<td>{eok(r['tr'][2025].get('revenue'))}</td>"
            f"<td class='sk'>{spark([r['tr'][y].get('revenue') for y in YEARS])}</td>"
            f"<td>{pct(r['tr'][2020].get('margin'))} → {pct(r['tr'][2025].get('margin'))}</td>"
            f"<td>{eok(r['p'].get('fcf'))}</td></tr>" for r in rows)
        return f"""<div class="tabs"><span class="tb on">자본활동</span><span class="tb">재무추이</span><span class="tb">전체목록</span></div>
<div class="lbl">최근 자본활동 · 날짜순</div>{tl}
<div class="lbl" style="margin-top:20px">재무추이 탭 (같은 화면에 있진 않음)</div>
<table class="tt2"><thead><tr><th class="l">회사</th><th>코드</th><th>매출</th><th>추이</th><th>마진 변화</th><th>FCF</th></tr></thead><tbody>{tb}</tbody></table>"""

    def layoutC():
        tb = "".join(
            f"<tr><td class='l'>{html.escape(r['name'])}</td><td>{r['stock']}</td>"
            f"<td>{eok(r['tr'][2025].get('assets'))}</td><td>{eok(r['tr'][2025].get('revenue'))}</td>"
            f"<td>{pct(r['tr'][2025].get('margin'))}</td><td>{len(r['ev'])}</td><td>›</td></tr>"
            for r in rows)
        r0 = rows[0]
        return f"""<div class="lbl">목록 화면</div>
<table class="tt2"><thead><tr><th class="l">회사</th><th>코드</th><th>자산</th><th>매출</th><th>마진</th><th>공시</th><th></th></tr></thead><tbody>{tb}</tbody></table>
<div class="lbl" style="margin-top:22px">↓ 회사를 누르면 화면 전환</div>
<div class="detail"><div class="dh">‹ 뒤로 &nbsp;|&nbsp; <b>{html.escape(r0['name'])}</b> {r0['stock']}</div>
{trend_table(r0['tr'])}{detail_grid(r0['p'])}{ev_html(r0['ev'])}</div>"""

    css = """
*{box-sizing:border-box}
body{margin:0;padding:14px;font:15px/1.55 -apple-system,'Segoe UI',sans-serif;
background:#faf9f7;color:#1a1a1a;max-width:780px;margin:0 auto}
h1{font-size:18px;margin:0 0 3px}
.sub{color:#888;font-size:12px;margin-bottom:14px}
.switch{display:flex;gap:6px;margin-bottom:18px;position:sticky;top:0;background:#faf9f7;
padding:8px 0;z-index:9;border-bottom:1px solid #e6e2dc}
.switch button{flex:1;padding:9px;border:1px solid #d9d4cc;background:#fff;border-radius:8px;
font-size:12px;font-weight:600;cursor:pointer;color:#555}
.switch button.on{background:#1f3864;color:#fff;border-color:#1f3864}
.chips{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:12px;font-size:11px}
.chips b{color:#888;font-weight:600;margin-right:2px}
.chip{padding:4px 10px;border:1px solid #d9d4cc;border-radius:14px;background:#fff;color:#555}
.chip.on{background:#1f3864;color:#fff;border-color:#1f3864}
.row{background:#fff;border:1px solid #e6e2dc;border-radius:10px;margin-bottom:7px}
.row summary{padding:11px 12px;cursor:pointer;display:flex;flex-wrap:wrap;gap:7px;align-items:center;
list-style:none;font-size:13px}
.row summary::-webkit-details-marker{display:none}
.row summary::before{content:'▸';color:#aaa;margin-right:2px}
.row[open] summary::before{content:'▾'}
.nm{font-weight:600;font-size:14px} .cd{color:#aaa;font-size:11px}
.sz{color:#777;font-size:11px} .mg{font-size:11px;color:#555}
.sk{font-family:ui-monospace,monospace;color:#1f3864;letter-spacing:-1px}
.evn{background:#eef2ff;color:#3730a3;font-size:10px;padding:2px 7px;border-radius:9px;margin-left:auto}
.body{padding:0 12px 12px;border-top:1px solid #f0ede8}
.lbl{font-size:11px;color:#888;margin:13px 0 6px;font-weight:600}
.lbl em{font-style:normal;color:#bbb;font-weight:400}
table.tt{width:100%;border-collapse:collapse;font-size:11px}
table.tt th,table.tt td{padding:4px 5px;border-bottom:1px solid #f0ede8;text-align:right}
table.tt thead th{color:#999;font-weight:600}
table.tt tbody th{text-align:left;color:#666;font-weight:600;white-space:nowrap}
.dg{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}
.dg div{background:#f7f5f2;border-radius:6px;padding:6px 8px}
.dg i{display:block;font-style:normal;font-size:10px;color:#999}
.dg b{font-size:12px}
.ev,.ev2{display:flex;gap:7px;align-items:baseline;font-size:12px;padding:5px 0;
border-bottom:1px solid #f4f1ec;flex-wrap:wrap}
.evd{color:#999;font-size:11px;font-family:ui-monospace,monospace;min-width:58px}
.nm2{font-weight:600;min-width:96px}
.tag{font-size:10px;padding:1px 7px;border-radius:9px}
.fu{background:#dbeafe;color:#1e40af} .rc{background:#ede9fe;color:#5b21b6}
.ts{background:#dcfce7;color:#166534} .as{background:#fef3c7;color:#92400e}
.rk{background:#fee2e2;color:#991b1b}
.am{font-size:9px;color:#aaa;border:1px solid #ddd;border-radius:3px;padding:0 3px}
.noev{color:#aaa;font-size:12px;padding:6px 0}
.acts{margin-top:12px;display:flex;gap:7px}
.acts a{font-size:11px;padding:5px 11px;border:1px solid #d9d4cc;border-radius:6px;
text-decoration:none;color:#444;background:#fff}
.tabs{display:flex;gap:6px;margin-bottom:14px;border-bottom:1px solid #e6e2dc}
.tb{padding:8px 14px;font-size:12px;color:#888;border-bottom:2px solid transparent}
.tb.on{color:#1f3864;font-weight:700;border-color:#1f3864}
table.tt2{width:100%;border-collapse:collapse;font-size:12px;background:#fff}
table.tt2 th,table.tt2 td{padding:7px 6px;border-bottom:1px solid #f0ede8;text-align:right}
table.tt2 th{color:#999;font-size:11px} .l{text-align:left!important}
.detail{background:#fff;border:1px solid #e6e2dc;border-radius:10px;padding:12px;margin-top:8px}
.dh{font-size:13px;color:#666;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid #f0ede8}
.pane{display:none} .pane.on{display:block}
.note{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:10px;
font-size:12px;color:#78350f;margin-bottom:16px}
@media(prefers-color-scheme:dark){
body{background:#16151a;color:#e8e6e3}
.switch{background:#16151a;border-color:#33313a}
.switch button{background:#1f1e24;border-color:#3a3842;color:#bbb}
.row,.chip,table.tt2,.detail,.acts a{background:#1f1e24;border-color:#33313a;color:#ddd}
.dg div{background:#26252c} .body{border-color:#2a2930}
table.tt th,table.tt td,.ev,.ev2,table.tt2 th,table.tt2 td,.dh{border-color:#2a2930}
.note{background:#2a2410;border-color:#5a4a1a;color:#fcd34d}}
"""
    doc = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Origination 디자인 시안</title><style>{css}</style></head><body>
<h1>Origination — 디자인 시안</h1>
<div class="sub">실제 데이터 12사 · {datetime.now():%Y-%m-%d} · 세 안을 눌러 비교</div>
<div class="note">레이블(고위험/관찰)은 전부 뺐음. 정렬 기준만 제공하고 판단은 열어둠.
숫자 단위는 억원. 스파크라인은 6개년 추이.</div>
<div class="switch">
  <button class="on" onclick="sw(0,this)">A 아코디언</button>
  <button onclick="sw(1,this)">B 탭 분리</button>
  <button onclick="sw(2,this)">C 2단계</button>
</div>
<div class="pane on">{layoutA()}</div>
<div class="pane">{layoutB()}</div>
<div class="pane">{layoutC()}</div>
<script>
function sw(i,b){{
  document.querySelectorAll('.pane').forEach((p,j)=>p.classList.toggle('on',i===j));
  document.querySelectorAll('.switch button').forEach(x=>x.classList.remove('on'));
  b.classList.add('on');window.scrollTo(0,0);
}}
</script></body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    return path


if __name__ == "__main__":
    con = sqlite3.connect(DB)
    rows = load(con, 12)
    con.close()
    p = build(rows, os.path.join(OUT, "design_mockup.html"))
    print("시안:", p)
    for r in rows[:5]:
        print(f"  {r['name']:14s} 이벤트 {len(r['ev'])}건  "
              f"매출추이 {[eok(r['tr'][y].get('revenue')) for y in YEARS]}")
