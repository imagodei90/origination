# -*- coding: utf-8 -*-
r"""
origination — raw layer + 작업 원장(ledger)

원칙
  1. API 응답은 전 계정 그대로 저장한다. 지표는 나중에 raw에서 파생한다.
  2. 콜 1회 = ledger 1행. "원하는 집합 − 완료 집합 = 할 일"로 백필이 항상 증분이 된다.
  3. 과거는 불변. 한 번 받은 (회사·연도·보고서·연결구분)은 재호출하지 않는다.
"""
import os, sys, io, json, time, sqlite3
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from universe import api, num, DB

if (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

RPT = {"11011": "A", "11013": "1Q", "11012": "2Q", "11014": "3Q"}
QUOTA_STATUS = {"020", "021"}      # 한도 초과 계열


def init(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS raw (
        corp_code TEXT, bsns_year INTEGER, reprt_code TEXT, fs_div TEXT,
        sj_div TEXT, ord INTEGER, account_id TEXT, account_nm TEXT, account_detail TEXT,
        thstrm REAL, thstrm_add REAL, frmtrm REAL, frmtrm_add REAL, bfefrmtrm REAL,
        PRIMARY KEY (corp_code, bsns_year, reprt_code, fs_div, sj_div, ord, account_id, account_nm)
    );
    CREATE INDEX IF NOT EXISTS ix_raw_c  ON raw(corp_code, bsns_year, reprt_code);
    CREATE INDEX IF NOT EXISTS ix_raw_a  ON raw(account_id);

    CREATE TABLE IF NOT EXISTS ledger (
        corp_code TEXT, bsns_year INTEGER, reprt_code TEXT, fs_div TEXT,
        status TEXT, rcept_no TEXT, n_rows INTEGER, fetched_at TEXT,
        PRIMARY KEY (corp_code, bsns_year, reprt_code, fs_div)
    );
    CREATE INDEX IF NOT EXISTS ix_led ON ledger(status);
    """)
    con.commit()


def done_set(con):
    """재호출 불필요한 (회사,연도,보고서) 집합. 성공했거나 '데이터 없음'이 확정된 건."""
    return {(c, y, r) for c, y, r in con.execute(
        "SELECT corp_code,bsns_year,reprt_code FROM ledger WHERE status IN ('ok','empty')")}


def save_rows(con, cc, year, rc, fs, lst, rcept):
    buf = []
    for i, r in enumerate(lst):
        buf.append((cc, year, rc, fs, r.get("sj_div"),
                    int(r.get("ord") or i), r.get("account_id") or "",
                    r.get("account_nm") or "", r.get("account_detail") or "",
                    num(r.get("thstrm_amount")), num(r.get("thstrm_add_amount")),
                    num(r.get("frmtrm_amount")), num(r.get("frmtrm_add_amount")),
                    num(r.get("bfefrmtrm_amount"))))
    con.executemany("INSERT OR REPLACE INTO raw VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", buf)
    con.execute("INSERT OR REPLACE INTO ledger VALUES (?,?,?,?,?,?,?,?)",
                (cc, year, rc, fs, "ok", rcept, len(lst),
                 datetime.now().isoformat(timespec="seconds")))
    return len(buf)


def mark(con, cc, year, rc, fs, status):
    con.execute("INSERT OR REPLACE INTO ledger VALUES (?,?,?,?,?,?,?,?)",
                (cc, year, rc, fs, status, None, 0,
                 datetime.now().isoformat(timespec="seconds")))


class QuotaExceeded(Exception):
    pass


def fetch_one(con, cc, year, rc):
    """연결 우선, 없으면 별도. (콜수, 저장행수) 반환. 한도면 QuotaExceeded."""
    calls = 0
    for fs in ("CFS", "OFS"):
        d = api("fnlttSinglAcntAll.json", corp_code=cc, bsns_year=str(year),
                reprt_code=rc, fs_div=fs)
        calls += 1
        st = d.get("status")
        if st in QUOTA_STATUS:
            raise QuotaExceeded(st)
        lst = d.get("list") or []
        if lst:
            n = save_rows(con, cc, year, rc, fs, lst, lst[0].get("rcept_no"))
            return calls, n
    mark(con, cc, year, rc, "-", "empty")
    return calls, 0


def plan(codes, years, reports):
    """원하는 (회사,연도,보고서) 전량"""
    out = []
    for cc in codes:
        for y in years:
            for rc in reports:
                out.append((cc, y, rc))
    return out


def backfill(codes, years, reports, verbose=True, commit_every=40):
    """원하는 집합 − 완료 집합만 수행. 중단돼도 다음 실행이 이어받음."""
    con = sqlite3.connect(DB)
    init(con)
    done = done_set(con)
    todo = [t for t in plan(codes, years, reports) if t not in done]
    print(f"계획 {len(codes)*len(years)*len(reports):,}건 · 완료 {len(done):,}건 · "
          f"이번에 받을 것 {len(todo):,}건")
    t0, calls, rows, ok, quota = time.time(), 0, 0, 0, False
    for i, (cc, y, rc) in enumerate(todo, 1):
        try:
            c, n = fetch_one(con, cc, y, rc)
            calls += c; rows += n
            if n:
                ok += 1
        except QuotaExceeded as e:
            con.commit()
            print(f"\n!! API 한도 도달 (status {e}) — {i-1}/{len(todo)} 지점에서 중단.")
            print("   다음 실행 시 남은 분부터 자동 재개됨.")
            quota = True
            break
        except Exception:
            mark(con, cc, y, rc, "-", "error")
        if i % commit_every == 0:
            con.commit()
            if verbose:
                el = time.time() - t0
                rate = calls / max(el, 1)
                eta = (len(todo) - i) * (calls / max(i, 1)) / max(rate, 0.1) / 60
                print(f"  {i:>6,}/{len(todo):,}  콜 {calls:>6,}  행 {rows:>9,}  "
                      f"{el/60:4.1f}분  {rate:.1f}콜/s  잔여 약 {eta:.0f}분")
    con.commit()
    el = time.time() - t0
    print(f"\n{'중단' if quota else '완료'} — 콜 {calls:,} / 저장 {rows:,}행 / "
          f"{el/60:.1f}분 / 성공 {ok:,}건")
    con.close()
    return {"calls": calls, "rows": rows, "sec": el, "quota": quota}


def status():
    con = sqlite3.connect(DB)
    init(con)
    print("[ledger 상태]")
    for st, n in con.execute("SELECT status,COUNT(*) FROM ledger GROUP BY status"):
        print(f"  {st:8s} {n:>8,}")
    r = con.execute("SELECT COUNT(*) FROM raw").fetchone()[0]
    c = con.execute("SELECT COUNT(DISTINCT corp_code) FROM raw").fetchone()[0]
    a = con.execute("SELECT COUNT(DISTINCT account_id) FROM raw").fetchone()[0]
    print(f"\n[raw]  {r:,}행 / 회사 {c:,} / 고유 account_id {a:,}")
    print("\n[연도·보고서별 적재]")
    for y, rc, n in con.execute(
            "SELECT bsns_year,reprt_code,COUNT(DISTINCT corp_code) FROM raw "
            "GROUP BY bsns_year,reprt_code ORDER BY bsns_year DESC,reprt_code"):
        print(f"  {y} {RPT.get(rc,rc):3s}  {n:>6,}사")
    con.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    b = sub.add_parser("run")
    b.add_argument("--limit", type=int, default=None, help="회사 수 제한(테스트용)")
    b.add_argument("--universe", default="filer", choices=["filer", "band"],
                   help="filer=정기보고서 제출 법인 전체 / band=딜사이즈 밴드")
    b.add_argument("--band-lo", type=float, default=5e10)
    b.add_argument("--band-hi", type=float, default=2.5e12)
    b.add_argument("--annual", default="2025,2022", help="사업보고서 연도(각 3개년 커버)")
    b.add_argument("--quarters", default="", help="연도:보고서코드 (예 2026:11013)")
    a = ap.parse_args()

    if a.cmd == "status":
        status()
    else:
        con = sqlite3.connect(DB)
        if a.universe == "filer":
            codes = [r[0] for r in con.execute(
                "SELECT corp_code FROM filer ORDER BY corp_cls, corp_code")]
        else:
            rid = con.execute("SELECT run_id FROM run ORDER BY started DESC LIMIT 1").fetchone()[0]
            codes = [r[0] for r in con.execute(
                "SELECT corp_code FROM snapshot WHERE run_id=? AND assets>=? AND assets<? "
                "ORDER BY assets DESC", (rid, a.band_lo, a.band_hi))]
        con.close()
        if a.limit:
            codes = codes[:a.limit]
        ann_years = [int(x) for x in a.annual.split(",") if x]
        print(f"대상 {len(codes):,}사 · 연간 {ann_years} · 분기 {a.quarters}")
        backfill(codes, ann_years, ["11011"])
        for spec in a.quarters.split(","):
            if not spec.strip():
                continue
            y, rc = spec.split(":")
            backfill(codes, [int(y)], [rc])
        status()
