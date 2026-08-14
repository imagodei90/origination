# -*- coding: utf-8 -*-
import sys, io, sqlite3
from collections import Counter
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import report as R

con = sqlite3.connect("origination.db")
rid, pid, rows = R.load(con)

print("스코어 분포")
for lo, hi, lb in [(80, 101, "80+"), (70, 80, "70-79"), (60, 70, "60-69"),
                   (40, 60, "40-59"), (20, 40, "20-39"), (0, 20, "0-19")]:
    n = sum(1 for r in rows if lo <= r["score"] < hi)
    print(f"  {lb:6s} {n:4d}사")

print("\n태그 빈도")
c = Counter(t for r in rows for t in r["tags"])
for t, n in c.most_common(10):
    print(f"  {t:14s} {n:4d}")

print("\n상위 12사")
for r in rows[:12]:
    icr = "영업적자" if r.get("op_loss") else ("n.a." if r.get("icr") is None else f"{r['icr']:.1f}x")
    print(f"  [{r['score']:3d}] {r['name'][:14]:16s} {r['stock']:7s} "
          f"자산{R.eok(r.get('assets')):>8s} 매출{R.eok(r.get('revenue')):>9s} "
          f"ICR {icr:>8s} FCF {R.eok(r.get('fcf')):>8s}")
    print(f"        {','.join(r['tags'])}")
