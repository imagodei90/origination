# -*- coding: utf-8 -*-
"""raw 기반 재계산 검증 — API 직접 호출분과 값이 같은지"""
import sys, io, json, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

con = sqlite3.connect("origination.db")
E = lambda v: "  n.a." if v is None else f"{v/1e12:6.2f}"
P = lambda v: "  n.a." if v is None else f"{v*100:5.1f}%"
D = lambda v: "  n.a." if v is None else f"{v:5.0f}d"

for cc, nm in [("00356361", "LG화학"), ("00126380", "삼성전자")]:
    rows = [(p, json.loads(x)) for p, x in con.execute(
        "SELECT period,payload FROM series WHERE corp_code=? AND kind='A' ORDER BY period", (cc,))]
    if not rows:
        print(f"{nm}: series 없음"); continue
    print(f"\n### {nm}   (단위 조원)")
    print("기간      " + "".join(f"{p:>9s}" for p, _ in rows))
    for k, lb, f in [("revenue", "매출액", E), ("op", "영업이익", E), ("margin", "영업마진", P),
                     ("ar", "매출채권", E), ("inv", "재고자산", E), ("ap", "매입채무", E),
                     ("debt", "총차입금", E), ("net_debt", "순차입금", E),
                     ("ocf", "OCF", E), ("fcf", "FCF", E)]:
        print(f"{lb:9s}" + "".join(f"{f(d.get(k)):>9s}" for _, d in rows))
    print("AR일수   " + "".join(f"{D(d.get('ar_days')):>9s}" for _, d in rows))
    print("CCC      " + "".join(f"{D(d.get('ccc')):>9s}" for _, d in rows))

n = con.execute("SELECT COUNT(DISTINCT corp_code) FROM series").fetchone()[0]
tot = con.execute("SELECT COUNT(*) FROM series").fetchone()[0]
ann = con.execute("SELECT COUNT(*) FROM series WHERE kind='A'").fetchone()[0]
print(f"\nseries — {n:,}사 / {tot:,}행 (연간 {ann:,} · 분기 {tot-ann:,})")

print("\n[연도별 지표 확보율 — 연간]")
for per, c1, c2, c3 in con.execute("""
    SELECT period,
           SUM(CASE WHEN json_extract(payload,'$.revenue') IS NOT NULL THEN 1 ELSE 0 END),
           SUM(CASE WHEN json_extract(payload,'$.op')      IS NOT NULL THEN 1 ELSE 0 END),
           SUM(CASE WHEN json_extract(payload,'$.debt')    IS NOT NULL THEN 1 ELSE 0 END)
    FROM series WHERE kind='A' GROUP BY period ORDER BY period"""):
    tot_p = con.execute("SELECT COUNT(*) FROM series WHERE kind='A' AND period=?", (per,)).fetchone()[0]
    print(f"  {per}  대상 {tot_p:>5,}  매출 {c1:>5,}  영업이익 {c2:>5,}  차입금 {c3:>5,}")
con.close()
