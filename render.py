# -*- coding: utf-8 -*-
r"""
origination — 최종 렌더러 (A 아코디언)

레이블·판정 없음. 오리지네이션 관점 지표를 연간 6개년 + 최근 분기로 펼치고
자본활동 공시를 붙인다. 정렬 기준만 제공하고 해석은 사용자 몫.
"""
import os, sys, io, json, html, sqlite3, argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "origination.db")
OUT = os.path.join(BASE, "out"); os.makedirs(OUT, exist_ok=True)
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


def dys(v):
    return "–" if v is None else f"{v:.0f}"


def xx(v):
    return "–" if v is None else f"{v:.1f}x"


def spark(vals):
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return ""
    lo, hi = min(v), max(v)
    rng = (hi - lo) or 1
    b = "▁▂▃▄▅▆▇"
    return "".join(b[min(int((x - lo) / rng * 6), 6)] if x is not None else "·" for x in vals)


def load(con, limit=None, order="events"):
    rid = con.execute("SELECT MAX(run_id) FROM tier1").fetchone()[0]
    rows = []
    q = """SELECT t.corp_code,c.corp_name,c.stock_code FROM tier1 t
           JOIN corp c ON c.corp_code=t.corp_code WHERE t.run_id=?"""
    for cc, nm, sk in con.execute(q, (rid,)):
        ser = {}
        for per, kind, pl in con.execute(
                "SELECT period,kind,payload FROM series WHERE corp_code=? ORDER BY period", (cc,)):
            ser.setdefault(kind, {})[per] = json.loads(pl)
        if not ser.get("A"):
            continue
        ev = [dict(zip(["dt", "cat", "title", "amended"], r)) for r in con.execute(
            "SELECT rcept_dt,cat,title,amended FROM event WHERE corp_code=? "
            "ORDER BY rcept_dt DESC LIMIT 10", (cc,))]
        A = ser["A"]; ys = sorted(A)
        last = A[ys[-1]]
        first = A[ys[0]]
        rows.append({
            "code": cc, "name": nm, "stock": sk, "A": A, "Q": ser.get("Q", {}),
            "ys": ys, "ev": ev, "last": last,
            "_ev": len(ev),
            "_rev_g": last.get("rev_growth"),
            "_mgn_d": ((last.get("margin") - first.get("margin"))
                       if (last.get("margin") is not None and first.get("margin") is not None) else None),
            "_debt_g": ((last.get("debt") / first.get("debt") - 1)
                        if (last.get("debt") and first.get("debt")) else None),
            "_fcf": last.get("fcf"),
        })
    keyf = {"events": lambda r: (-r["_ev"], r["name"]),
            "rev": lambda r: (r["_rev_g"] if r["_rev_g"] is not None else 9),
            "margin": lambda r: (r["_mgn_d"] if r["_mgn_d"] is not None else 9),
            "debt": lambda r: (-(r["_debt_g"] or -9)),
            "fcf": lambda r: (r["_fcf"] if r["_fcf"] is not None else 9e15)}
    rows.sort(key=keyf.get(order, keyf["events"]))
    return rows[:limit] if limit else rows


GROUPS = [
    ("성장·수익", [("매출", "revenue", eok), ("성장률", "rev_growth", pct),
                ("영업이익", "op", eok), ("영업마진", "margin", pct)]),
    ("현금창출", [("OCF", "ocf", eok), ("CAPEX", "capex", eok), ("FCF", "fcf", eok)]),
    ("레버리지", [("총차입금", "debt", eok), ("순차입금", "net_debt", eok),
                ("이자비용", "int_cost", eok), ("이자보상배율", "icr", xx),
                ("순차입금÷영익", "nd_op", xx)]),
    ("운전자본", [("AR일수", "ar_days", dys), ("재고일수", "inv_days", dys),
                ("AP일수", "ap_days", dys), ("CCC", "ccc", dys)]),
]


def series_table(ser, periods, label_of):
    ths = "".join(f"<th>{label_of(p)}</th>" for p in periods)
    body = []
    for gname, items in GROUPS:
        body.append(f'<tr class="gh"><th colspan="{len(periods)+1}">{gname}</th></tr>')
        for lb, key, fmt in items:
            tds = "".join(f"<td>{fmt(ser.get(p, {}).get(key))}</td>" for p in periods)
            body.append(f"<tr><th>{lb}</th>{tds}</tr>")
    return (f'<table class="tt"><thead><tr><th></th>{ths}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


def ev_html(evs):
    if not evs:
        return '<div class="noev">최근 12개월 자금조달·자산거래 공시 없음</div>'
    out = []
    for e in evs:
        d = e["dt"]
        ds = f"{d[2:4]}.{d[4:6]}.{d[6:8]}" if d and len(d) == 8 else d
        am = '<span class="am">정정</span>' if e["amended"] else ""
        out.append(f'<div class="ev"><span class="evd">{ds}</span>'
                   f'<span class="tag {CAT_COLOR.get(e["cat"],"")}">{html.escape(e["cat"])}</span>'
                   f'<span class="evt">{html.escape(e["title"])}</span>{am}</div>')
    return "".join(out)


CSS = """
*{box-sizing:border-box}
body{margin:0;padding:14px;font:15px/1.55 -apple-system,'Segoe UI',sans-serif;
background:#faf9f7;color:#1a1a1a;max-width:820px;margin:0 auto}
h1{font-size:18px;margin:0 0 3px}
.sub{color:#888;font-size:12px;margin-bottom:12px}
.chips{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:12px;font-size:11px;
position:sticky;top:0;background:#faf9f7;padding:8px 0;z-index:9;border-bottom:1px solid #e6e2dc}
.chips b{color:#888;font-weight:600}
.chip{padding:4px 10px;border:1px solid #d9d4cc;border-radius:14px;background:#fff;color:#555;
cursor:pointer;text-decoration:none}
.chip.on{background:#1f3864;color:#fff;border-color:#1f3864}
.row{background:#fff;border:1px solid #e6e2dc;border-radius:10px;margin-bottom:7px}
.row summary{padding:11px 12px;cursor:pointer;display:flex;flex-wrap:wrap;gap:8px;
align-items:center;list-style:none;font-size:13px}
.row summary::-webkit-details-marker{display:none}
.row summary::before{content:'▸';color:#aaa}
.row[open] summary::before{content:'▾'}
.nm{font-weight:600;font-size:14px} .cd{color:#aaa;font-size:11px}
.sz{color:#777;font-size:11px}
.sk{font-family:ui-monospace,monospace;color:#1f3864;letter-spacing:-1px;font-size:14px}
.mg{font-size:11px;color:#555}
.evn{background:#eef2ff;color:#3730a3;font-size:10px;padding:2px 7px;border-radius:9px;margin-left:auto}
.body{padding:0 12px 12px;border-top:1px solid #f0ede8}
.lbl{font-size:11px;color:#888;margin:14px 0 6px;font-weight:600}
.lbl em{font-style:normal;color:#bbb;font-weight:400}
.wrap{overflow-x:auto}
table.tt{width:100%;border-collapse:collapse;font-size:11px;min-width:430px}
table.tt th,table.tt td{padding:4px 6px;border-bottom:1px solid #f4f1ec;text-align:right;
white-space:nowrap}
table.tt thead th{color:#999;font-weight:600;background:#faf9f7}
table.tt tbody th{text-align:left;color:#555;font-weight:500}
tr.gh th{background:#f2efe9;color:#1f3864;font-weight:700;font-size:10px;text-align:left;
padding:3px 6px}
.ev{display:flex;gap:7px;align-items:baseline;font-size:12px;padding:5px 0;
border-bottom:1px solid #f4f1ec;flex-wrap:wrap}
.evd{color:#999;font-size:11px;font-family:ui-monospace,monospace;min-width:58px}
.tag{font-size:10px;padding:1px 7px;border-radius:9px}
.fu{background:#dbeafe;color:#1e40af} .rc{background:#ede9fe;color:#5b21b6}
.ts{background:#dcfce7;color:#166534} .as{background:#fef3c7;color:#92400e}
.rk{background:#fee2e2;color:#991b1b}
.am{font-size:9px;color:#aaa;border:1px solid #ddd;border-radius:3px;padding:0 3px}
.noev{color:#aaa;font-size:12px;padding:6px 0}
.acts{margin-top:12px;display:flex;gap:7px}
.acts a{font-size:11px;padding:5px 11px;border:1px solid #d9d4cc;border-radius:6px;
text-decoration:none;color:#444;background:#fff}
.ft{color:#999;font-size:11px;margin-top:22px;border-top:1px solid #e6e2dc;padding-top:10px}
@media(prefers-color-scheme:dark){
body{background:#16151a;color:#e8e6e3}
.chips{background:#16151a;border-color:#33313a}
.row,.chip,.acts a{background:#1f1e24;border-color:#33313a;color:#ddd}
.body{border-color:#2a2930} table.tt thead th{background:#1f1e24}
table.tt th,table.tt td,.ev{border-color:#2a2930}
tr.gh th{background:#26252c;color:#93b4e8} .ft{border-color:#33313a}}
"""

ORDERS = [("events", "자본활동 많은순"), ("rev", "매출 역성장"), ("margin", "마진 하락폭"),
          ("debt", "차입금 증가"), ("fcf", "FCF 낮은순")]


def build(rows, path, order, band_label):
    now = datetime.now()
    chips = "".join(
        f'<a class="chip{" on" if k==order else ""}" href="origination_{k}.html">{lb}</a>'
        for k, lb in ORDERS)
    items = []
    for r in rows:
        A, Q, ys = r["A"], r["Q"], r["ys"]
        qs = sorted(Q)[-6:]
        last = r["last"]
        sp = spark([A[y].get("revenue") for y in ys])
        qtbl = (f'<div class="lbl">분기 추이 <em>손익은 누계 · 잔액은 분기말</em></div>'
                f'<div class="wrap">{series_table(Q, qs, lambda p: p[2:4]+" "+p[4:])}</div>'
                if qs else "")
        items.append(f"""
<details class="row"><summary>
 <span class="nm">{html.escape(r['name'])}</span><span class="cd">{r['stock']}</span>
 <span class="sz">매출 {eok(last.get('revenue'))}억 · 마진 {pct(last.get('margin'))} · 차입금 {eok(last.get('debt'))}억</span>
 <span class="sk" title="매출 6개년">{sp}</span>
 {'<span class="evn">공시 '+str(r['_ev'])+'</span>' if r['_ev'] else ''}
</summary>
<div class="body">
 <div class="lbl">연간 추이 <em>단위 억원 · 일수는 일</em></div>
 <div class="wrap">{series_table(A, ys, lambda p: "'"+p[2:])}</div>
 {qtbl}
 <div class="lbl">최근 12개월 자본활동</div>
 {ev_html(r['ev'])}
 <div class="acts">
  <a href="https://dart.fss.or.kr/dsab007/main.do?textCrpNm={html.escape(r['name'])}" target="_blank">DART 공시</a>
  <a href="#" onclick="return false">엑셀 생성</a><a href="#" onclick="return false">노트</a>
 </div>
</div></details>""")
    doc = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Origination {now:%Y-%m-%d}</title><style>{CSS}</style></head><body>
<h1>Origination</h1>
<div class="sub">{now:%Y-%m-%d} · {band_label} · {len(rows):,}사 · DART 공시 기반</div>
<div class="chips"><b>정렬</b>{chips}</div>
{''.join(items)}
<div class="ft">
판정·등급 없이 지표와 공시 사실만 표시함. 해석은 사용자 판단.<br>
차입금은 계정명 합산이라 회사가 기타금융부채로 묶으면 미검출될 수 있음(–로 표시).<br>
분기 손익은 누계 기준, 재무상태표 항목은 해당 분기말 잔액.
</div></body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--band-label", default="자산 3천억-1조")
    a = ap.parse_args()
    con = sqlite3.connect(DB)
    made = []
    for k, lb in ORDERS:
        rows = load(con, a.limit, k)
        p = build(rows, os.path.join(OUT, f"origination_{k}.html"), k, a.band_label)
        made.append((lb, len(rows), p))
    con.close()
    for lb, n, p in made:
        print(f"  {lb:14s} {n:4d}사  {p}")
