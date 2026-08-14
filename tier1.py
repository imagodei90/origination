# -*- coding: utf-8 -*-
r"""
origination — Tier 1 조기경보 지표 추출

Tier 0 밴드에서 고른 회사에 fnlttSinglAcntAll를 1회씩 호출(3개년 동시 수집)해
조기경보 4축 지표를 계산한다.

  python tier1.py run --band 3000e-1t [--limit N]
  python tier1.py cost            # 비용 실측만
"""
import os, sys, io, json, time, sqlite3, argparse, re
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from universe import api, num, DB, init_db

if (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BANDS = {
    "500e-3000e": (5e10, 3e11),
    "3000e-1t":   (3e11, 1e12),
    "1t-2.5t":    (1e12, 2.5e12),
}


def _norm(s):
    return re.sub(r"[\s()（）ㆍ·,]", "", s or "")


# (키, sj, account_id 후보, 계정명 후보, 합산여부)
SPEC = [
    ("revenue", ("IS", "CIS"), ["ifrs-full_Revenue", "ifrs-full_RevenueFromContractsWithCustomers"], ["매출액", "영업수익"], False),
    ("ebit",    ("IS", "CIS"), ["dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"], ["영업이익", "영업손실"], False),
    ("ni",      ("IS", "CIS"), ["ifrs-full_ProfitLoss"], ["당기순이익"], False),
    ("int_exp", ("IS", "CIS"), ["dart_InterestExpenseFinanceExpense"], ["이자비용"], False),
    ("fin_cost", ("IS", "CIS"), ["ifrs-full_FinanceCosts"], ["금융비용", "금융원가"], False),
    ("assets",  ("BS",), ["ifrs-full_Assets"], ["자산총계"], False),
    ("equity",  ("BS",), ["ifrs-full_Equity"], ["자본총계"], False),
    ("cash",    ("BS",), ["ifrs-full_CashAndCashEquivalents"], ["현금및현금성자산", "현금및현금등가물"], False),
    ("st_dep",  ("BS",), ["ifrs-full_ShorttermDepositsNotClassifiedAsCashEquivalents"], ["단기금융상품"], False),
    ("ar",      ("BS",), ["ifrs-full_CurrentTradeReceivables", "ifrs-full_TradeAndOtherCurrentReceivables"], ["매출채권"], False),
    ("inv",     ("BS",), ["ifrs-full_Inventories"], ["재고자산"], False),
    ("ap",      ("BS",), ["ifrs-full_TradeAndOtherCurrentPayablesToTradeSuppliers", "ifrs-full_TradeAndOtherCurrentPayables"], ["매입채무"], False),
    ("debt",    ("BS",), [], ["차입금", "사채", "리스부채", "유동성장기부채"], True),   # 합산
    ("ocf",     ("CF",), ["ifrs-full_CashFlowsFromUsedInOperatingActivities"], ["영업활동"], False),
    ("capex",   ("CF",), ["ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"], ["유형자산의취득"], False),
]
DEBT_EXCL = ["대여금", "선급", "이자", "채권"]

FIELDS = [("thstrm_amount", 0), ("frmtrm_amount", 1), ("bfefrmtrm_amount", 2)]


def extract(rows, year):
    """{fy: {key: value}}"""
    out = {year - o: {} for _, o in FIELDS}
    for key, sjs, ids, names, is_sum in SPEC:
        pool = [r for r in rows if r.get("sj_div") in sjs]
        for fld, off in FIELDS:
            fy = year - off
            val = None
            if is_sum:
                tot, hit = 0.0, False
                for r in pool:
                    nm = _norm(r.get("account_nm"))
                    if not any(_norm(p) in nm for p in names):
                        continue
                    if any(_norm(e) in nm for e in DEBT_EXCL):
                        continue
                    v = num(r.get(fld))
                    if v is not None:
                        tot += v
                        hit = True
                val = tot if hit else None
            else:
                for aid in ids:
                    for r in pool:
                        if r.get("account_id") == aid:
                            v = num(r.get(fld))
                            if v is not None:
                                val = v
                                break
                    if val is not None:
                        break
                if val is None:
                    for pat in names:
                        np_ = _norm(pat)
                        for r in pool:
                            if np_ and np_ in _norm(r.get("account_nm")):
                                v = num(r.get(fld))
                                if v is not None:
                                    val = v
                                    break
                        if val is not None:
                            break
            if val is not None:
                out[fy][key] = val
    return out


def signals(hist):
    """조기경보 4축. hist = {fy: {key: val}}. 최신연도 기준 지표 + YoY 변화"""
    yrs = sorted(hist, reverse=True)
    if len(yrs) < 2:
        return None
    c, p = hist[yrs[0]], hist[yrs[1]]
    g = lambda d, k: d.get(k)
    s = {"fy": yrs[0]}

    # ① 이자보상배율
    # 이자비용이 미미하면 배율이 무의미하게 폭발(한국철강 -2757배)하므로 하한을 둔다.
    # 영업적자면 배율 대신 '영업적자'로 다루고 배율은 내지 않는다.
    MIN_IE = 1e8   # 이자비용 1억
    def _icr(d):
        ie = d.get("int_exp") or d.get("fin_cost")
        eb = d.get("ebit")
        if ie is None or ie < MIN_IE or eb is None or eb < 0:
            return None
        return eb / ie
    s["icr"], s["icr_prev"] = _icr(c), _icr(p)
    s["op_loss"] = (g(c, "ebit") is not None and g(c, "ebit") < 0)

    # ② 순차입금 / FCF 갭
    cash = (g(c, "cash") or 0) + (g(c, "st_dep") or 0)
    debt = g(c, "debt")
    # 차입금은 계정명 합산이라 회사가 '기타금융부채'로 묶으면 못 잡음.
    # 미검출이면 순차입금을 만들지 않고 신뢰도 플래그로 남긴다.
    s["debt_found"] = debt is not None
    s["net_debt"] = (debt - cash) if debt is not None else None
    s["cash"] = cash
    cash_p = (g(p, "cash") or 0) + (g(p, "st_dep") or 0)
    nd_p = (g(p, "debt") - cash_p) if g(p, "debt") is not None else None
    s["net_debt_chg"] = (s["net_debt"] - nd_p) if (s["net_debt"] is not None and nd_p is not None) else None
    ocf, capex = g(c, "ocf"), g(c, "capex")
    s["fcf"] = (ocf - abs(capex)) if (ocf is not None and capex is not None) else ocf
    s["ocf"] = ocf
    s["nd_ebit"] = (s["net_debt"] / g(c, "ebit")) if (s["net_debt"] and g(c, "ebit") and g(c, "ebit") > 0) else None

    # ③ 영업레버리지 역전 — 매출 역성장 + 마진 하락
    rev, rev_p = g(c, "revenue"), g(p, "revenue")
    s["rev_growth"] = (rev / rev_p - 1) if (rev and rev_p) else None
    m = (g(c, "ebit") / rev) if (rev and g(c, "ebit") is not None) else None
    m_p = (g(p, "ebit") / rev_p) if (rev_p and g(p, "ebit") is not None) else None
    s["margin"], s["margin_chg"] = m, ((m - m_p) if (m is not None and m_p is not None) else None)

    # ④ 운전자본 회전일수 변화
    def days(d, k, base):
        v, b = d.get(k), d.get(base)
        return (v / b * 365) if (v is not None and b) else None
    s["ar_days"] = days(c, "ar", "revenue")
    s["ar_days_chg"] = (s["ar_days"] - days(p, "ar", "revenue")) if (s["ar_days"] and days(p, "ar", "revenue")) else None
    s["inv_days"] = days(c, "inv", "revenue")
    s["inv_days_chg"] = (s["inv_days"] - days(p, "inv", "revenue")) if (s["inv_days"] and days(p, "inv", "revenue")) else None

    s["equity"], s["assets"], s["revenue"], s["ebit"] = g(c, "equity"), g(c, "assets"), rev, g(c, "ebit")
    s["debt"] = debt
    return s


def score(s):
    """0-100. 높을수록 스트레스. 근거 태그 동반"""
    pts, tags = 0, []
    if s.get("op_loss"):
        pts += 30; tags.append("영업적자")
    elif s.get("icr") is not None:
        if s["icr"] < 1:
            pts += 30; tags.append("이자보상<1")
        elif s["icr"] < 2:
            pts += 15; tags.append("이자보상<2")
        if s.get("icr_prev") is not None and s["icr"] < s["icr_prev"] and s["icr"] < 3:
            pts += 5; tags.append("이자보상악화")
    if s.get("fcf") is not None and s["fcf"] < 0:
        pts += 15; tags.append("FCF음수")
    if s.get("ocf") is not None and s["ocf"] < 0:
        pts += 15; tags.append("OCF음수")
    if s.get("net_debt_chg") and s.get("equity") and s["net_debt_chg"] > 0.1 * s["equity"]:
        pts += 15; tags.append("순차입금급증")
    if s.get("rev_growth") is not None and s["rev_growth"] < -0.05:
        pts += 10; tags.append("매출역성장")
    if s.get("margin_chg") is not None and s["margin_chg"] < -0.02:
        pts += 10; tags.append("마진하락")
    if not s.get("debt_found"):
        tags.append("차입금미확보")   # 감점 아님 — 데이터 한계 표시
    if s.get("ar_days_chg") and s["ar_days_chg"] > 15:
        pts += 5; tags.append("AR회전악화")
    if s.get("inv_days_chg") and s["inv_days_chg"] > 20:
        pts += 5; tags.append("재고회전악화")
    if s.get("equity") is not None and s["equity"] < 0:
        pts += 20; tags.append("자본잠식")
    return min(pts, 100), tags


def ensure_tables(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS tier1 (
        run_id TEXT, corp_code TEXT, fy INTEGER, score INTEGER, tags TEXT,
        payload TEXT, PRIMARY KEY (run_id, corp_code)
    );
    """)
    con.commit()


def run(band, limit=None, year=2025, verbose=True):
    lo, hi = BANDS[band]
    con = init_db()
    ensure_tables(con)
    rid0 = con.execute("SELECT run_id FROM run ORDER BY started DESC LIMIT 1").fetchone()[0]
    q = """SELECT s.corp_code, c.corp_name FROM snapshot s JOIN corp c ON c.corp_code=s.corp_code
           WHERE s.run_id=? AND s.assets>=? AND s.assets<? ORDER BY s.assets DESC"""
    targets = con.execute(q, (rid0, lo, hi)).fetchall()
    if limit:
        targets = targets[:limit]

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_t1"
    t0, calls, ok, fail = time.time(), 0, 0, 0
    rows_out = []
    for i, (cc, nm) in enumerate(targets, 1):
        try:
            d = api("fnlttSinglAcntAll.json", corp_code=cc, bsns_year=str(year),
                    reprt_code="11011", fs_div="CFS")
            calls += 1
            lst = d.get("list") or []
            if not lst:
                d = api("fnlttSinglAcntAll.json", corp_code=cc, bsns_year=str(year),
                        reprt_code="11011", fs_div="OFS")
                calls += 1
                lst = d.get("list") or []
            if d.get("status") == "020":
                print("!! API 일일 한도 초과 (status 020). 중단."); break
            if not lst:
                fail += 1
                continue
            s = signals(extract(lst, year))
            if not s:
                fail += 1
                continue
            sc, tags = score(s)
            rows_out.append((run_id, cc, s["fy"], sc, ",".join(tags), json.dumps(s, ensure_ascii=False)))
            ok += 1
        except Exception:
            fail += 1
        if verbose and i % 50 == 0:
            el = time.time() - t0
            print(f"  {i:>4}/{len(targets)}  콜 {calls}  성공 {ok}  실패 {fail}  "
                  f"{el:.0f}s  ({calls/max(el,1):.1f} 콜/s)")
    if rows_out:
        con.executemany("INSERT OR REPLACE INTO tier1 VALUES (?,?,?,?,?,?)", rows_out)
        con.commit()
    el = time.time() - t0
    print(f"\nTier1 완료 {el:.0f}s | 대상 {len(targets)} | 콜 {calls} | 성공 {ok} | 실패 {fail}")
    print(f"  콜당 평균 {el/max(calls,1):.2f}s, 처리율 {calls/max(el,1):.1f} 콜/s")
    print(f"  run_id = {run_id}")
    con.close()
    return run_id, {"targets": len(targets), "calls": calls, "ok": ok,
                    "fail": fail, "sec": el}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--band", default="3000e-1t", choices=list(BANDS))
    r.add_argument("--limit", type=int, default=None)
    r.add_argument("--year", type=int, default=2025)
    a = ap.parse_args()
    run(a.band, a.limit, a.year)
