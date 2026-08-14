# -*- coding: utf-8 -*-
r"""
origination — 정기보고서 제출 법인 유니버스 확정

list.json(정기공시 A)에서 사업/반기/분기보고서를 실제로 제출한 법인만 모은다.
corpCode.xml의 stock_code 보유 목록에는 오래전 상장폐지된 회사가 다수 섞여 있어
그대로 쓰면 빈 응답만 잔뜩 받게 되므로, 실제 제출 이력을 유니버스의 기준으로 삼는다.

  python filers.py build      # 유니버스 수집 → filer 테이블
  python filers.py stats
"""
import os, sys, io, time, sqlite3, argparse
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from universe import api, DB, init_db

if (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

CLS = [("Y", "유가증권"), ("K", "코스닥"), ("N", "코넥스"), ("E", "기타(비상장)")]
KINDS = ("사업보고서", "분기보고서", "반기보고서")


def ensure(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS filer (
        corp_code TEXT PRIMARY KEY, corp_name TEXT, corp_cls TEXT,
        last_report TEXT, last_dt TEXT
    );
    """)
    con.commit()


def windows(months=18):
    """list.json은 조회 기간 상한이 있어 3개월씩 끊어서 훑는다"""
    end = datetime.now()
    out = []
    cur = end - timedelta(days=30 * months)
    while cur < end:
        nxt = min(cur + timedelta(days=90), end)
        out.append((cur.strftime("%Y%m%d"), nxt.strftime("%Y%m%d")))
        cur = nxt + timedelta(days=1)
    return out


def build(months=18, verbose=True):
    con = init_db(); ensure(con)
    t0, calls, found = time.time(), 0, {}
    for cls, label in CLS:
        n0 = len(found)
        for bgn, end in windows(months):
            page = 1
            while page <= 60:
                try:
                    d = api("list.json", bgn_de=bgn, end_de=end, pblntf_ty="A",
                            corp_cls=cls, page_no=page, page_count=100)
                except Exception:
                    break
                calls += 1
                if d.get("status") != "000":
                    break
                for r in d.get("list") or []:
                    rn = r.get("report_nm") or ""
                    if not any(k in rn for k in KINDS):
                        continue
                    cc = r["corp_code"]
                    prev = found.get(cc)
                    if not prev or r.get("rcept_dt", "") > prev[3]:
                        found[cc] = (r.get("corp_name"), cls, rn, r.get("rcept_dt", ""))
                tp = d.get("total_page") or 1
                if page >= tp:
                    break
                page += 1
        if verbose:
            print(f"  {label:12s} 누적 {len(found):>6,}사 (+{len(found)-n0:,})  콜 {calls}")
    con.executemany("INSERT OR REPLACE INTO filer VALUES (?,?,?,?,?)",
                    [(cc, v[0], v[1], v[2], v[3]) for cc, v in found.items()])
    con.commit()
    print(f"\n유니버스 확정 — {len(found):,}사 / 콜 {calls} / {time.time()-t0:.0f}s")
    con.close()
    return len(found)


def stats():
    con = sqlite3.connect(DB); ensure(con)
    print("[법인 구분별]")
    for cls, label in CLS:
        n = con.execute("SELECT COUNT(*) FROM filer WHERE corp_cls=?", (cls,)).fetchone()[0]
        print(f"  {label:12s} {n:>6,}")
    tot = con.execute("SELECT COUNT(*) FROM filer").fetchone()[0]
    print(f"  {'합계':12s} {tot:>6,}")
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--months", type=int, default=18)
    sub.add_parser("stats")
    a = ap.parse_args()
    build(a.months) if a.cmd == "build" else stats()
