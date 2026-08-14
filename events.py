# -*- coding: utf-8 -*-
r"""
origination — 자본활동 공시 수집

list.json에서 주요사항보고(B)·발행공시(C)를 받아 자금조달·자본개편·자산거래·위험신호만 남긴다.
회사당 2콜.
"""
import os, sys, io, re, time, json, sqlite3
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from universe import api, DB, init_db

if (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 분류 → (라벨, 매칭 키워드)
CATS = [
    ("자금조달", ["유상증자", "전환사채", "신주인수권부사채", "교환사채", "회사채",
                "증권신고서", "사채권발행", "무상증자", "조건부자본증권"]),
    ("자본개편", ["감자", "합병", "분할", "주식교환", "주식이전", "포괄적"]),
    ("자기주식", ["자기주식"]),
    ("자산거래", ["유형자산", "타법인주식", "영업양수", "영업양도", "자산양수", "자산양도",
                "출자", "주식및출자증권"]),
    ("위험신호", ["회생절차", "파산", "채권은행", "관리절차", "부도", "감사의견",
                "상장폐지", "거래정지", "소송"]),
]
DROP = ["철회"]   # 철회신고서는 별도 표시용으로 남기되 분류는 자금조달


def classify(name):
    n = re.sub(r"\[[^\]]*\]", "", name or "")
    for label, kws in CATS:
        if any(k in n for k in kws):
            return label
    return None


def clean_title(name):
    """주요사항보고서(유상증자결정) → 유상증자결정"""
    n = (name or "").strip()
    amended = "[기재정정]" in n or "[첨부정정]" in n
    n = re.sub(r"\[[^\]]*\]", "", n).strip()
    m = re.match(r"주요사항보고서\((.+)\)", n)
    if m:
        n = m.group(1)
    return n, amended


def ensure(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS event (
        corp_code TEXT, rcept_no TEXT, rcept_dt TEXT, cat TEXT,
        title TEXT, amended INTEGER, raw TEXT,
        PRIMARY KEY (corp_code, rcept_no)
    );
    CREATE INDEX IF NOT EXISTS ix_ev ON event(corp_code, rcept_dt DESC);
    """)
    con.commit()


def fetch_for(corp_code, months=12):
    end = datetime.now()
    bgn = end - timedelta(days=30 * months)
    out = []
    for ty in ("B", "C"):
        try:
            d = api("list.json", corp_code=corp_code,
                    bgn_de=bgn.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"),
                    pblntf_ty=ty, page_no=1, page_count=100)
        except Exception:
            continue
        if d.get("status") != "000":
            continue
        for r in (d.get("list") or []):
            nm = r.get("report_nm") or ""
            cat = classify(nm)
            if not cat:
                continue
            title, amended = clean_title(nm)
            out.append({"corp_code": corp_code, "rcept_no": r.get("rcept_no"),
                        "rcept_dt": r.get("rcept_dt"), "cat": cat,
                        "title": title, "amended": int(amended), "raw": nm})
    # 같은 건의 기재정정 중복 제거 — 같은 날짜+제목이면 최신 접수번호만
    seen, uniq = {}, []
    for e in sorted(out, key=lambda x: x["rcept_no"], reverse=True):
        k = (e["rcept_dt"], e["title"])
        if k in seen:
            continue
        seen[k] = 1
        uniq.append(e)
    return sorted(uniq, key=lambda x: x["rcept_dt"], reverse=True)


def collect(corp_codes, months=12, verbose=True):
    con = init_db()
    ensure(con)
    t0, n = time.time(), 0
    for i, cc in enumerate(corp_codes, 1):
        evs = fetch_for(cc, months)
        if evs:
            con.executemany(
                "INSERT OR REPLACE INTO event VALUES (?,?,?,?,?,?,?)",
                [(e["corp_code"], e["rcept_no"], e["rcept_dt"], e["cat"],
                  e["title"], e["amended"], e["raw"]) for e in evs])
            n += len(evs)
        if i % 25 == 0:
            con.commit()
            if verbose:
                el = time.time() - t0
                print(f"  {i}/{len(corp_codes)}  이벤트 {n}건  {el:.0f}s")
    con.commit()
    con.close()
    print(f"자본활동 수집 완료 — {len(corp_codes)}사 / 이벤트 {n}건 / {time.time()-t0:.0f}s")
    return n


def load_events(con, corp_code, limit=8):
    return [dict(zip(["dt", "cat", "title", "amended", "rcept"], r)) for r in con.execute(
        "SELECT rcept_dt,cat,title,amended,rcept_no FROM event WHERE corp_code=? "
        "ORDER BY rcept_dt DESC LIMIT ?", (corp_code, limit))]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    con = sqlite3.connect(DB)
    rid = con.execute("SELECT MAX(run_id) FROM tier1").fetchone()[0]
    codes = [r[0] for r in con.execute(
        "SELECT corp_code FROM tier1 WHERE run_id=? ORDER BY score DESC", (rid,))]
    con.close()
    if a.limit:
        codes = codes[:a.limit]
    collect(codes)
