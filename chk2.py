import sys,io,json,sqlite3
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding="utf-8",errors="replace")
con=sqlite3.connect("origination.db")
cc,nm=con.execute("""SELECT s.corp_code,c.corp_name FROM series s JOIN corp c ON c.corp_code=s.corp_code
                     GROUP BY s.corp_code ORDER BY COUNT(*) DESC LIMIT 1""").fetchone()
print(f"\n샘플: {nm}")
E=lambda v:"–" if v is None else f"{v/1e8:,.0f}"
P=lambda v:"–" if v is None else f"{v*100:.1f}%"
for kind in ("A","Q"):
    print(f"\n[{kind}]")
    for per,pl in con.execute("SELECT period,payload FROM series WHERE corp_code=? AND kind=? ORDER BY period",(cc,kind)):
        d=json.loads(pl)
        print(f"  {per:8s} 매출{E(d.get('revenue')):>8s} 영익{E(d.get('op')):>7s} 마진{P(d.get('margin')):>7s} "
              f"차입금{E(d.get('debt')):>8s} 순차입{E(d.get('net_debt')):>8s} OCF{E(d.get('ocf')):>7s} "
              f"FCF{E(d.get('fcf')):>7s} CCC{'–' if d.get('ccc') is None else format(d['ccc'],'.0f')+'d':>6s}")
