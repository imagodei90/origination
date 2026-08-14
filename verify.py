# -*- coding: utf-8 -*-
"""백필 결과 검증 — 연도별 커버리지 + 표본 대조"""
import sys, io, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

con = sqlite3.connect("origination.db")

print("[연도별 회사 수 — fy 기준]")
for fy, n in con.execute(
        "SELECT bsns_year - CASE WHEN 1=1 THEN 0 END, COUNT(DISTINCT corp_code) "
        "FROM raw WHERE reprt_code='11011' GROUP BY bsns_year ORDER BY bsns_year"):
    print(f"  보고서 {fy}  {n:>6,}사")

print("\n[실제 회계연도 커버리지 — 3개년 창 전개]")
cov = {}
for by, cc in con.execute(
        "SELECT DISTINCT bsns_year, corp_code FROM raw WHERE reprt_code='11011'"):
    for off in (0, 1, 2):
        cov.setdefault(by - off, set()).add(cc)
for fy in sorted(cov):
    print(f"  FY{fy}  {len(cov[fy]):>6,}사")

years = [y for y in cov if 2020 <= y <= 2025]
full = set.intersection(*[cov[y] for y in years]) if len(years) == 6 else set()
print(f"\n  6개년(2020-2025) 전부 보유: {len(full):,}사")

print("\n[표본 검증 — 삼성전자 매출]")
q = """SELECT bsns_year, thstrm, frmtrm, bfefrmtrm FROM raw
       WHERE corp_code='00126380' AND reprt_code='11011' AND fs_div='CFS'
         AND account_id='ifrs-full_Revenue' AND sj_div IN ('IS','CIS')
       ORDER BY bsns_year DESC"""
for by, a, b, c in con.execute(q):
    f = lambda v: "–" if v is None else f"{v/1e12:,.1f}조"
    print(f"  {by} 보고서 → 당기 {f(a)} / 전기 {f(b)} / 전전기 {f(c)}")

print("\n[표준계정 커버리지]")
tot = con.execute("SELECT COUNT(DISTINCT account_id) FROM raw").fetchone()[0]
std = con.execute("SELECT COUNT(DISTINCT account_id) FROM raw "
                  "WHERE account_id LIKE 'ifrs-full%' OR account_id LIKE 'dart%'").fetchone()[0]
nost = con.execute("SELECT COUNT(*) FROM raw WHERE account_id LIKE '%미사용%'").fetchone()[0]
allr = con.execute("SELECT COUNT(*) FROM raw").fetchone()[0]
print(f"  고유 account_id {tot:,}종 (표준 {std:,}종)")
print(f"  표준코드 미사용 행 {nost:,}/{allr:,} = {nost/allr*100:.1f}%")

print("\n[재무제표 구분별 행수]")
for sj, n in con.execute("SELECT sj_div, COUNT(*) FROM raw GROUP BY sj_div ORDER BY 2 DESC"):
    print(f"  {sj:5s} {n:>9,}")
con.close()
