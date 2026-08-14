# -*- coding: utf-8 -*-
r"""
origination — Tier 0 유니버스 스캐너

fnlttMultiAcnt.json (100사/콜)로 전 상장사 주요계정을 훑어 딜사이즈 밴드로 1차 압축한다.
결과는 SQLite 스냅샷으로 적재해 주차별 변화 추적의 기준선이 된다.

  python universe.py scan [--year 2025] [--limit N]
  python universe.py stats
"""
import os, sys, io, json, time, sqlite3, argparse, winreg, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

if (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "origination.db")
CORP_XML = r"C:\tmp\dart_fs\corpCode.xml"

BATCH = 100                    # fnlttMultiAcnt 상한(200에서 status 021)
DEAL_MIN = 50_000_000_000      # 자산총계 500억
DEAL_MAX = 2_500_000_000_000   # 자산총계 2.5조 (목표 2조 + 여유)


def _key():
    if os.environ.get("DART_API_KEY"):
        return os.environ["DART_API_KEY"]
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
        return winreg.QueryValueEx(k, "DART_API_KEY")[0]


KEY = _key()


def api(ep, **p):
    p["crtfc_key"] = KEY
    qs = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in p.items())
    url = f"https://opendart.fss.or.kr/api/{ep}?{qs}"
    for i in range(3):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if i == 2:
                raise
            time.sleep(2)


def num(s):
    if s in (None, "", "-"):
        return None
    try:
        return float(str(s).replace(",", "").strip())
    except ValueError:
        return None


def listed_corps():
    root = ET.parse(CORP_XML).getroot()
    out = []
    for e in root.iter("list"):
        sc = (e.findtext("stock_code") or "").strip()
        if not sc:
            continue
        out.append({"corp_code": (e.findtext("corp_code") or "").strip(),
                    "corp_name": (e.findtext("corp_name") or "").strip(),
                    "stock_code": sc})
    # corp_code 중복 제거
    seen, uniq = set(), []
    for c in out:
        if c["corp_code"] in seen:
            continue
        seen.add(c["corp_code"])
        uniq.append(c)
    return uniq


def init_db():
    con = sqlite3.connect(DB)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS corp (
        corp_code TEXT PRIMARY KEY, corp_name TEXT, stock_code TEXT
    );
    CREATE TABLE IF NOT EXISTS fin (
        corp_code TEXT, fy INTEGER, fs_div TEXT, account TEXT, amount REAL,
        PRIMARY KEY (corp_code, fy, fs_div, account)
    );
    CREATE TABLE IF NOT EXISTS snapshot (
        run_id TEXT, corp_code TEXT, fy INTEGER,
        assets REAL, liab REAL, equity REAL, revenue REAL, op REAL, ni REAL,
        in_band INTEGER,
        PRIMARY KEY (run_id, corp_code)
    );
    CREATE TABLE IF NOT EXISTS run (
        run_id TEXT PRIMARY KEY, started TEXT, note TEXT,
        n_corp INTEGER, n_data INTEGER, n_band INTEGER
    );
    CREATE INDEX IF NOT EXISTS ix_fin ON fin(corp_code, account, fy);
    """)
    con.commit()
    return con


# fnlttMultiAcnt 계정명 → 표준 키
ACC = {
    "자산총계": "assets", "부채총계": "liab", "자본총계": "equity",
    "매출액": "revenue", "영업이익": "op", "당기순이익": "ni",
    "유동자산": "cur_assets", "비유동자산": "noncur_assets",
    "유동부채": "cur_liab", "비유동부채": "noncur_liab",
    "자본금": "capital", "이익잉여금": "retained",
}


def scan(year, limit=None, verbose=True):
    con = init_db()
    corps = listed_corps()
    if limit:
        corps = corps[:limit]
    con.executemany("INSERT OR REPLACE INTO corp VALUES (?,?,?)",
                    [(c["corp_code"], c["corp_name"], c["stock_code"]) for c in corps])
    con.commit()

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    total_rows, calls, t0 = 0, 0, time.time()
    got = set()

    for i in range(0, len(corps), BATCH):
        chunk = corps[i:i + BATCH]
        codes = ",".join(c["corp_code"] for c in chunk)
        try:
            d = api("fnlttMultiAcnt.json", corp_code=codes,
                    bsns_year=str(year), reprt_code="11011")
        except Exception as e:
            print(f"  [{i}] ERR {type(e).__name__}")
            continue
        calls += 1
        rows = d.get("list") or []
        buf = []
        for r in rows:
            key = ACC.get((r.get("account_nm") or "").strip())
            if not key:
                continue
            cc, fsd = r["corp_code"], r.get("fs_div", "")
            got.add(cc)
            # 당기/전기/전전기 3개년을 한 번에 적재
            for fld, off in [("thstrm_amount", 0), ("frmtrm_amount", 1), ("bfefrmtrm_amount", 2)]:
                v = num(r.get(fld))
                if v is not None:
                    buf.append((cc, year - off, fsd, key, v))
        if buf:
            con.executemany("INSERT OR REPLACE INTO fin VALUES (?,?,?,?,?)", buf)
            con.commit()
            total_rows += len(buf)
        if verbose and (i // BATCH) % 5 == 0:
            el = time.time() - t0
            print(f"  {i+len(chunk):>5,}/{len(corps):,}사  콜 {calls}  적재 {total_rows:,}행  {el:.0f}s")

    # 스냅샷 — 연결 우선, 없으면 별도
    cur = con.execute("""
        SELECT corp_code, account, fs_div, amount FROM fin WHERE fy=?""", (year,))
    agg = {}
    for cc, acc, fsd, amt in cur:
        slot = agg.setdefault(cc, {})
        if acc not in slot or (fsd == "CFS" and slot[acc][0] != "CFS"):
            slot[acc] = (fsd, amt)

    snap, n_band = [], 0
    for cc, m in agg.items():
        g = lambda k: m.get(k, (None, None))[1]
        a = g("assets")
        band = 1 if (a is not None and DEAL_MIN <= a <= DEAL_MAX) else 0
        n_band += band
        snap.append((run_id, cc, year, a, g("liab"), g("equity"),
                     g("revenue"), g("op"), g("ni"), band))
    con.executemany("INSERT OR REPLACE INTO snapshot VALUES (?,?,?,?,?,?,?,?,?,?)", snap)
    con.execute("INSERT OR REPLACE INTO run VALUES (?,?,?,?,?,?)",
                (run_id, datetime.now().isoformat(timespec="seconds"),
                 f"Tier0 scan FY{year}", len(corps), len(got), n_band))
    con.commit()

    el = time.time() - t0
    print(f"\n완료 {el:.0f}s  |  API 콜 {calls}  |  요청 {len(corps):,}사  "
          f"데이터 확보 {len(got):,}사  |  밴드 내 {n_band:,}사")
    print(f"run_id = {run_id}")
    con.close()
    return run_id


def stats():
    con = sqlite3.connect(DB)
    print("[run 이력]")
    for r in con.execute("SELECT * FROM run ORDER BY started DESC LIMIT 5"):
        print("  ", r)
    rid = con.execute("SELECT run_id FROM run ORDER BY started DESC LIMIT 1").fetchone()
    if not rid:
        return
    rid = rid[0]
    print(f"\n[{rid} 자산 규모 분포]")
    bands = [(0, 5e10, "500억 미만"), (5e10, 3e11, "500억-3천억"),
             (3e11, 1e12, "3천억-1조"), (1e12, 2.5e12, "1조-2.5조"),
             (2.5e12, 9e15, "2.5조 초과")]
    for lo, hi, lb in bands:
        n = con.execute("SELECT COUNT(*) FROM snapshot WHERE run_id=? AND assets>=? AND assets<?",
                        (rid, lo, hi)).fetchone()[0]
        print(f"  {lb:14s} {n:>6,}사")
    n_null = con.execute("SELECT COUNT(*) FROM snapshot WHERE run_id=? AND assets IS NULL",
                         (rid,)).fetchone()[0]
    print(f"  {'자산 미확보':14s} {n_null:>6,}사")
    print(f"\n[밴드 내 상위 10사 — 매출 기준]")
    q = """SELECT c.corp_name, s.assets, s.revenue, s.op FROM snapshot s
           JOIN corp c ON c.corp_code=s.corp_code
           WHERE s.run_id=? AND s.in_band=1 AND s.revenue IS NOT NULL
           ORDER BY s.revenue DESC LIMIT 10"""
    for nm, a, rev, op in con.execute(q, (rid,)):
        print(f"  {nm[:18]:20s} 자산 {a/1e8:>8,.0f}억  매출 {rev/1e8:>8,.0f}억  "
              f"영업이익 {(op or 0)/1e8:>7,.0f}억")
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--year", type=int, default=2025)
    s.add_argument("--limit", type=int, default=None)
    sub.add_parser("stats")
    a = ap.parse_args()
    if a.cmd == "scan":
        scan(a.year, a.limit)
    else:
        stats()
