# -*- coding: utf-8 -*-
r"""핸드폰 접근용 slim 아카이브 — 회사별 JSON을 C:\tmp\dart_archive\ 에 생성.

내용: 회사 메타 + 표준계정 연도 시계열(fin) + 자본활동 이벤트(event).
등급 표기: 이 파일은 파생(3등급). 원본(2등급 API응답)은 origination.db raw 테이블.
재실행 = 전체 재생성 (멱등).
"""
import sys, io, os, json, sqlite3, re
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DB = r"C:\tmp\origination\origination.db"
OUT = r"C:\tmp\dart_archive"
os.makedirs(os.path.join(OUT, "companies"), exist_ok=True)

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

corps = {r["corp_code"]: dict(r) for r in db.execute("select * from filer")}
for r in db.execute("select * from corp"):
    corps.setdefault(r["corp_code"], {}).update(
        {k: r[k] for k in r.keys() if r[k] is not None})

fin = defaultdict(lambda: defaultdict(dict))   # corp -> account -> {fy: {fs_div: amt}}
for r in db.execute("select corp_code, fy, fs_div, account, amount from fin"):
    fin[r["corp_code"]][r["account"]].setdefault(str(r["fy"]), {})[r["fs_div"]] = r["amount"]

events = defaultdict(list)
for r in db.execute("select corp_code, rcept_no, rcept_dt, cat, title from event order by rcept_dt"):
    events[r["corp_code"]].append(
        {"rcept_no": r["rcept_no"], "date": r["rcept_dt"], "cat": r["cat"], "title": r["title"]})

def safe_name(s):
    return re.sub(r'[\\/:*?"<>|\s]+', "_", s or "").strip("_")[:40]

index = []
n = 0
for cc, meta in sorted(corps.items()):
    if cc not in fin and cc not in events:
        continue
    name = meta.get("corp_name", "")
    doc = {
        "_meta": {
            "corp_code": cc, "corp_name": name,
            "stock_code": meta.get("stock_code", ""),
            "corp_cls": meta.get("corp_cls", ""),
            "등급": "파생(3등급) — 표준계정 시계열 재구성",
            "원본": "origination.db raw 테이블(DART 재무 API 응답, 2등급) → fin",
            "생성": "export_slim.py (재실행=재생성)",
        },
        "financials": fin.get(cc, {}),
        "events": events.get(cc, []),
    }
    fn = f"{cc}_{safe_name(name)}.json"
    with open(os.path.join(OUT, "companies", fn), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    index.append({"corp_code": cc, "name": name,
                  "stock_code": meta.get("stock_code", ""), "file": fn})
    n += 1

with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as f:
    json.dump({"_meta": {"companies": n, "source": "origination.db",
                         "용도": "핸드폰 열람용 slim 아카이브"},
               "companies": index}, f, ensure_ascii=False, indent=1)

readme = """# DART Slim Archive

핸드폰 열람용 회사별 재무 아카이브 (origination 파이프라인 파생물).

- `index.json` — 회사 목록 (corp_code·회사명·파일명)
- `companies/{corp_code}_{회사명}.json` — 표준계정 연도 시계열 + 자본활동 이벤트

등급: 파생(3등급). 원본(2등급 DART API 응답)은 로컬 origination.db raw 테이블.
1등급(공시 원문)은 DART rcept_no로 역추적: https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}
"""
with open(os.path.join(OUT, "README.md"), "w", encoding="utf-8") as f:
    f.write(readme)

total_mb = sum(os.path.getsize(os.path.join(OUT, "companies", x))
               for x in os.listdir(os.path.join(OUT, "companies"))) / 1e6
print(f"회사 {n:,}개 JSON 생성 — companies/ 총 {total_mb:.1f}MB")
