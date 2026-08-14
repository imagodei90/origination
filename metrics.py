# -*- coding: utf-8 -*-
r"""
origination — 오리지네이션 관점 지표 시계열 수집

연간 6개년(FY2025·FY2022 보고서 2콜) + 분기 최근 8개 시점(4콜) = 회사당 6콜.
일반 FS 나열이 아니라 T-5~T-3 구간 신호에 필요한 지표만 시계열로 만든다.

  성장·수익   매출 / 영업이익 / 영업마진
  현금창출     OCF / CAPEX / FCF
  레버리지     총차입금 / 순차입금 / 이자비용 / 이자보상배율 / 순차입금÷영업이익
  운전자본     AR일수 / 재고일수 / AP일수 / CCC
"""
import os, sys, io, re, json, time, sqlite3, argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from universe import api, num, DB, init_db

if (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

MIN_IE = 1e8   # 이자비용 하한 — 미만이면 이자보상배율 무의미


def _n(s):
    return re.sub(r"[\s()（）ㆍ·,]", "", s or "")


SPEC = [
    ("revenue", ("IS", "CIS"), ["ifrs-full_Revenue", "ifrs-full_RevenueFromContractsWithCustomers"], ["매출액", "영업수익"], False),
    ("op",      ("IS", "CIS"), ["dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"], ["영업이익", "영업손실"], False),
    ("ni",      ("IS", "CIS"), ["ifrs-full_ProfitLoss"], ["당기순이익"], False),
    ("int_exp", ("IS", "CIS"), ["dart_InterestExpenseFinanceExpense"], ["이자비용"], False),
    ("fin_cost", ("IS", "CIS"), ["ifrs-full_FinanceCosts"], ["금융비용", "금융원가"], False),
    ("cogs",    ("IS", "CIS"), ["ifrs-full_CostOfSales"], ["매출원가"], False),
    ("assets",  ("BS",), ["ifrs-full_Assets"], ["자산총계"], False),
    ("equity",  ("BS",), ["ifrs-full_Equity"], ["자본총계"], False),
    ("cash",    ("BS",), ["ifrs-full_CashAndCashEquivalents"], ["현금및현금성자산", "현금및현금등가물"], False),
    ("st_dep",  ("BS",), ["ifrs-full_ShorttermDepositsNotClassifiedAsCashEquivalents"], ["단기금융상품"], False),
    ("ar",      ("BS",), ["ifrs-full_CurrentTradeReceivables", "ifrs-full_TradeAndOtherCurrentReceivables"], ["매출채권"], False),
    ("inv",     ("BS",), ["ifrs-full_Inventories"], ["재고자산"], False),
    ("ap",      ("BS",), ["ifrs-full_TradeAndOtherCurrentPayablesToTradeSuppliers", "ifrs-full_TradeAndOtherCurrentPayables"], ["매입채무"], False),
    ("debt",    ("BS",), [], ["차입금", "사채", "리스부채", "유동성장기부채"], True),
    ("ocf",     ("CF",), ["ifrs-full_CashFlowsFromUsedInOperatingActivities"], ["영업활동"], False),
    ("capex",   ("CF",), ["ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"], ["유형자산의취득"], False),
]
DEBT_EXCL = ["대여금", "선급", "이자", "채권"]

ANNUAL_FIELDS = [("thstrm_amount", 0), ("frmtrm_amount", 1), ("bfefrmtrm_amount", 2)]
QUARTERS = [("11013", "1Q"), ("11012", "2Q"), ("11014", "3Q")]


def _pick(pool, ids, names, fld, is_sum):
    """account_id 단독 신뢰 금지 — 회사가 매출채권을 '-표준계정코드 미사용-'으로 내면서
    ifrs-full_TradeAndOtherCurrentReceivables 태그를 '기타수취채권'에 붙이는 사례가 있다
    (LG화학 2021-22: 6.39조 → 1.05조 오인식). id 매칭 시 계정명 정합을 함께 본다."""
    if is_sum:
        tot, hit = 0.0, False
        for r in pool:
            nm = _n(r.get("account_nm"))
            if not any(_n(p) in nm for p in names):
                continue
            if any(_n(e) in nm for e in DEBT_EXCL):
                continue
            v = num(r.get(fld))
            if v is not None:
                tot += v; hit = True
        return tot if hit else None

    pats = [p for p in (_n(x) for x in names) if p]

    def name_ok(r):
        if not pats:
            return True
        return any(p in _n(r.get("account_nm")) for p in pats)

    # 1) id 일치 + 계정명 정합
    for aid in ids:
        for r in pool:
            if r.get("account_id") == aid and name_ok(r):
                v = num(r.get(fld))
                if v is not None:
                    return v
    # 2) 계정명 완전일치 → 부분일치 ('매출채권'이 '장기매출채권'에 걸리는 것 방지)
    for exact in (True, False):
        for pat in pats:
            for r in pool:
                nm = _n(r.get("account_nm"))
                if (nm == pat) if exact else (pat in nm):
                    v = num(r.get(fld))
                    if v is not None:
                        return v
    # 3) 최후 — 계정명이 어긋나도 id가 맞으면 사용
    for aid in ids:
        for r in pool:
            if r.get("account_id") == aid:
                v = num(r.get(fld))
                if v is not None:
                    return v
    return None


def extract_annual(rows, year):
    out = {year - o: {} for _, o in ANNUAL_FIELDS}
    for key, sjs, ids, names, is_sum in SPEC:
        pool = [r for r in rows if r.get("sj_div") in sjs]
        for fld, off in ANNUAL_FIELDS:
            v = _pick(pool, ids, names, fld, is_sum)
            if v is not None:
                out[year - off][key] = v
    return out


def extract_quarter(rows, year, qlabel):
    """분기 추출.
    BS  : thstrm_amount = 당분기말 잔액. frmtrm_amount는 '전기말'이지 전년동기말이 아니므로
          전년동기 행에는 절대 넣지 않는다(그러면 잔액이 통째로 틀림).
    손익 : 누계는 thstrm_add_amount. 없으면 thstrm_amount로 폴백(분기보고서마다 다름).
    """
    cur, pri = {}, {}
    for key, sjs, ids, names, is_sum in SPEC:
        pool = [r for r in rows if r.get("sj_div") in sjs]
        is_bs = sjs == ("BS",)
        if is_bs:
            c = _pick(pool, ids, names, "thstrm_amount", is_sum)
            if c is not None:
                cur[key] = c
            continue
        c = _pick(pool, ids, names, "thstrm_add_amount", is_sum)
        if c is None:
            c = _pick(pool, ids, names, "thstrm_amount", is_sum)
        p = _pick(pool, ids, names, "frmtrm_add_amount", is_sum)
        if c is not None:
            cur[key] = c
        if p is not None:
            pri[key] = p
    out = {f"{year}{qlabel}": cur}
    if pri:
        out[f"{year-1}{qlabel}"] = pri
    return out


def derive(d, prev=None, annualize=1.0):
    """지표 파생. annualize: 분기 누계를 연율화할 배수(회전일수 계산용)"""
    g = d.get
    o = dict(d)
    rev, op = g("revenue"), g("op")
    o["margin"] = (op / rev) if (rev and op is not None) else None
    cash = (g("cash") or 0) + (g("st_dep") or 0)
    o["cash_total"] = cash if (g("cash") is not None or g("st_dep") is not None) else None
    debt = g("debt")
    o["net_debt"] = (debt - cash) if debt is not None else None
    ocf, capex = g("ocf"), g("capex")
    o["fcf"] = (ocf - abs(capex)) if (ocf is not None and capex is not None) else None
    ie = g("int_exp") or g("fin_cost")
    o["int_cost"] = ie
    o["icr"] = (op / ie) if (ie and ie >= MIN_IE and op is not None and op > 0) else None
    o["nd_op"] = (o["net_debt"] / op) if (o["net_debt"] and op and op > 0) else None
    base = (rev * annualize) if rev else None
    cbase = (g("cogs") * annualize) if g("cogs") else base
    o["ar_days"] = (g("ar") / base * 365) if (g("ar") is not None and base) else None
    o["inv_days"] = (g("inv") / cbase * 365) if (g("inv") is not None and cbase) else None
    o["ap_days"] = (g("ap") / cbase * 365) if (g("ap") is not None and cbase) else None
    if None not in (o["ar_days"], o["inv_days"], o["ap_days"]):
        o["ccc"] = o["ar_days"] + o["inv_days"] - o["ap_days"]
    else:
        o["ccc"] = None
    if prev:
        pr = prev.get("revenue")
        o["rev_growth"] = (rev / pr - 1) if (rev and pr) else None
    return o


def ensure(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS series (
        corp_code TEXT, period TEXT, kind TEXT, payload TEXT,
        PRIMARY KEY (corp_code, period)
    );
    """)
    con.commit()


# ──────────────────────────────────────────────────────────
# raw layer 리더 — API 응답과 같은 모양으로 되돌려 준다
# ──────────────────────────────────────────────────────────
RAW_COLS = ("sj_div", "account_id", "account_nm",
            "thstrm", "thstrm_add", "frmtrm", "frmtrm_add", "bfefrmtrm")
_FIELD_MAP = {"thstrm": "thstrm_amount", "thstrm_add": "thstrm_add_amount",
              "frmtrm": "frmtrm_amount", "frmtrm_add": "frmtrm_add_amount",
              "bfefrmtrm": "bfefrmtrm_amount"}


def load_raw(con, cc, year, rc):
    """raw에서 (rows, fs_div). 연결 우선, 없으면 별도. API 호출 없음."""
    for fs in ("CFS", "OFS"):
        cur = con.execute(
            f"SELECT {','.join(RAW_COLS)} FROM raw "
            "WHERE corp_code=? AND bsns_year=? AND reprt_code=? AND fs_div=? "
            "ORDER BY ord", (cc, year, rc, fs))
        rows = []
        for rec in cur:
            d = {}
            for k, v in zip(RAW_COLS, rec):
                d[_FIELD_MAP.get(k, k)] = v
            rows.append(d)
        if rows:
            return rows, fs
    return [], None


def collect_one(cc, base_year=2025, with_q=True):
    """연간 6개년 + 분기 최근 시점. (annual dict, quarter dict, calls)"""
    calls = 0
    ann = {}
    for y in (base_year, base_year - 3):
        for fs in ("CFS", "OFS"):
            d = api("fnlttSinglAcntAll.json", corp_code=cc, bsns_year=str(y),
                    reprt_code="11011", fs_div=fs)
            calls += 1
            lst = d.get("list") or []
            if lst:
                ann.update(extract_annual(lst, y))
                break
    q = {}
    if with_q:
        now = datetime.now()
        plan = []
        for yy in (now.year, now.year - 1):
            for rc, lb in reversed(QUARTERS):
                plan.append((yy, rc, lb))
        for yy, rc, lb in plan[:5]:
            try:
                d = api("fnlttSinglAcntAll.json", corp_code=cc, bsns_year=str(yy),
                        reprt_code=rc, fs_div="CFS")
                calls += 1
                lst = d.get("list") or []
                if not lst:
                    continue
                # 직접 조회분(BS 포함)이 전년동기 파생분(손익만)보다 우선
                for pk, pv in extract_quarter(lst, yy, lb).items():
                    if pk not in q or len(pv) > len(q[pk]):
                        q[pk] = pv
            except Exception:
                continue
    return ann, q, calls


def collect_from_raw(con, cc, base_year=2025):
    """raw layer만으로 연간·분기 시계열 구성. API 호출 0."""
    ann, q = {}, {}
    for y in (base_year, base_year - 3, base_year - 6, base_year - 9):
        rows, _ = load_raw(con, cc, y, "11011")
        if rows:
            ann.update(extract_annual(rows, y))
    for yy in (base_year + 1, base_year):
        for rc, lb in QUARTERS:
            rows, _ = load_raw(con, cc, yy, rc)
            if not rows:
                continue
            for pk, pv in extract_quarter(rows, yy, lb).items():
                if pk not in q or len(pv) > len(q[pk]):
                    q[pk] = pv
    return ann, q


def run(limit=None, base_year=2025, with_q=True, verbose=True, source="raw"):
    con = init_db(); ensure(con)
    if source == "raw":
        codes = [r[0] for r in con.execute(
            "SELECT DISTINCT corp_code FROM raw WHERE reprt_code='11011' ORDER BY corp_code")]
    else:
        rid = con.execute("SELECT MAX(run_id) FROM tier1").fetchone()[0]
        codes = [r[0] for r in con.execute(
            "SELECT corp_code FROM tier1 WHERE run_id=? ORDER BY score DESC", (rid,))]
    if limit:
        codes = codes[:limit]
    print(f"대상 {len(codes):,}사 · 소스 {source}")
    t0, calls, ok = time.time(), 0, 0
    for i, cc in enumerate(codes, 1):
        try:
            if source == "raw":
                ann, q = collect_from_raw(con, cc, base_year)
            else:
                ann, q, c = collect_one(cc, base_year, with_q)
                calls += c
        except Exception:
            continue
        rows = []
        yrs = sorted(ann)
        for j, y in enumerate(yrs):
            prev = ann.get(yrs[j - 1]) if j else None
            rows.append((cc, str(y), "A", json.dumps(derive(ann[y], prev), ensure_ascii=False)))
        for pk in sorted(q):
            mult = {"1Q": 4.0, "2Q": 2.0, "3Q": 4 / 3}.get(pk[-2:], 1.0)
            rows.append((cc, pk, "Q", json.dumps(derive(q[pk], None, mult), ensure_ascii=False)))
        if rows:
            con.executemany("INSERT OR REPLACE INTO series VALUES (?,?,?,?)", rows)
            ok += 1
        if i % 20 == 0:
            con.commit()
            if verbose:
                el = time.time() - t0
                print(f"  {i}/{len(codes)}  콜 {calls}  성공 {ok}  {el:.0f}s  "
                      f"({calls/max(el,1):.1f} 콜/s, 잔여 약 {(len(codes)-i)*calls/max(i,1)/max(calls/max(el,1),0.1)/60:.0f}분)")
    con.commit(); con.close()
    el = time.time() - t0
    print(f"\n완료 {el:.0f}s | 대상 {len(codes)} | 콜 {calls} | 성공 {ok} | "
          f"회사당 {calls/max(len(codes),1):.1f}콜")
    return calls, el


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-q", action="store_true")
    ap.add_argument("--source", default="raw", choices=["raw", "api"],
                    help="raw=적재분에서 재계산(콜 0) / api=직접 호출")
    a = ap.parse_args()
    run(a.limit, with_q=not a.no_q, source=a.source)
