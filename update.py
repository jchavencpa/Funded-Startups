#!/usr/bin/env python3
"""Daily refresh: US seed-stage startups from SEC Form D (main source) + funding news (enrichment). Stdlib only."""
import json, os, re, time, datetime as dt, urllib.request, urllib.parse, xml.etree.ElementTree as ET
from html import unescape
UA = {"User-Agent": "FundedStartupsTracker jordan@tryhaventax.com"}  # EDIT: real email required by the SEC
SEED_MAX_M = 10        # ignore raises bigger than this ($M)
SEED_MIN_M = 0.25      # ignore tiny/placeholder filings
KEEP_DAYS = 365
STATES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split())
SKIP_IND = re.compile(r"Pooled|Real Estate|REIT|Bank|Insurance|Investing|Oil|Gas|Mining", re.I)
SKIP_NAME = re.compile(r"\b(fund|trust|l\.?p\.?|partners|capital|holdings|ventures|spv|series\s+\d+)\b", re.I)
US_HINT = re.compile(r"\b(San Francisco|New York|Austin|Boston|Seattle|Los Angeles|Chicago|Miami|Denver|Atlanta|Palo Alto|Menlo Park|Brooklyn|Boulder|Pittsburgh|Philadelphia|Dallas|Houston|Salt Lake|San Diego|Nashville|Raleigh|Portland|Minneapolis|U\.S\.|US-based|American)\b")
NON_US = re.compile(r"\b(Prague|London|Berlin|Paris|Tel Aviv|Toronto|Vancouver|Bangalore|Singapore|Dublin|Amsterdam|Stockholm|Lagos|Nairobi|Sydney|Zurich|Warsaw|Lisbon|Madrid|Barcelona|Munich|Helsinki|Copenhagen|India|UK|Europe|Canadian|Israeli|Nigeria)\b")
NEWS_QUERIES = ['"seed round" raises', '"pre-seed" raises', '"seed funding" startup announces', 'startup raises "seed" led by']

def get(url, tries=3):
    for i in range(tries):
        try: return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()
        except Exception:
            if i == tries - 1: raise
            time.sleep(2 * (i + 1))

def day_filings(d):
    q = (d.month - 1) // 3 + 1
    try: txt = get(f"https://www.sec.gov/Archives/edgar/daily-index/{d.year}/QTR{q}/form.{d:%Y%m%d}.idx").decode("latin-1")
    except Exception: return []   # weekends/holidays have no index
    out = []
    for ln in txt.splitlines():
        m = re.match(r"^D\s{2,}(.+?)\s{2,}(\d+)\s+\d{8}\s+edgar/data/\d+/(\S+)\.txt\s*$", ln)
        if m: out.append((m[2], m[3]))
    return out

def parse_form_d(cik, acc):
    x = ET.fromstring(get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-','')}/primary_doc.xml"))
    g = lambda p: (x.findtext(p) or "").strip()
    name, state, ind = g(".//primaryIssuer/entityName"), g(".//primaryIssuer/issuerAddress/stateOrCountry"), g(".//industryGroup/industryGroupType")
    if state not in STATES or SKIP_IND.search(ind) or SKIP_NAME.search(name): return None
    if "Partnership" in g(".//primaryIssuer/entityType"): return None
    if g(".//primaryIssuer/yearOfInc/withinFiveYears") != "true" and not (g(".//primaryIssuer/yearOfInc/value") or "0")[:4] >= str(dt.date.today().year - 5): return None
    if g(".//typesOfSecuritiesOffered/isEquityType") != "true": return None
    sold = g(".//offeringSalesAmounts/totalAmountSold")
    if not sold.isdigit() or not (SEED_MIN_M * 1e6 <= int(sold) <= SEED_MAX_M * 1e6): return None
    ppl = []
    for p in x.iter("relatedPersonInfo"):
        if re.search("Executive|Director|Promoter", p.findtext("relatedPersonRelationshipList/relationship") or ""):
            ppl.append(f"{p.findtext('relatedPersonName/firstName','')} {p.findtext('relatedPersonName/lastName','')}".strip())
    city = g(".//primaryIssuer/issuerAddress/city").title()
    return dict(name=name.title(), round="Seed", amt=round(int(sold) / 1e6, 2), date=g(".//dateOfFirstSale/value") or g(".//signatureDate") or dt.date.today().isoformat(),
        inv="", fnd=", ".join(dict.fromkeys(ppl))[:140], sec=ind, hq=f"{city}, {state}", web="", li="", src=f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-','')}/", via="SEC Form D")

RAISE = re.compile(r"^(?P<name>[A-Z0-9][^,:–—]{1,60}?)\s+(?:raises?|raised|secures?|secured|lands?|closes?|closed|bags|nabs|snags|announces?)\s+(?:an?\s+)?(?:\$|US\$)(?P<amt>[\d.,]+)\s*(?P<u>million|billion|M|B|K)?\b", re.I)
LED = re.compile(r"(?:led|co-led) by ([^.;,]+)", re.I)
def news():
    out = []
    for q in NEWS_QUERIES:
        try: root = ET.fromstring(get("https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q=" + urllib.parse.quote(q + " when:7d")))
        except Exception as e: print("news fail", q, e); continue
        for it in root.iter("item"):
            title = re.sub(r"\s+-\s+[^-]+$", "", unescape(it.findtext("title") or "")).strip()
            m = RAISE.match(title)
            if not m or not re.search(r"seed", title, re.I): continue
            try: amt = float(m["amt"].replace(",", "")) * {"b": 1000, "billion": 1000, "k": .001}.get((m["u"] or "M").lower(), 1)
            except ValueError: continue
            if amt > SEED_MAX_M: continue
            led = LED.search(title)
            try: d = dt.datetime.strptime(it.findtext("pubDate")[5:16], "%d %b %Y").date().isoformat()
            except Exception: d = dt.date.today().isoformat()
            out.append(dict(name=m["name"].strip(), round="Seed", amt=round(amt, 2), date=d, inv=led[1].strip() if led else "", fnd="", sec="", hq="", web="", li="",
                src=(it.findtext("link") or "").strip(), via="News", _text=title))
    return out

key = lambda c: re.sub(r"\b(inc|llc|corp|corporation|co|ltd)\b|\W", "", c["name"].lower())
def main():
    db = json.load(open("data.json")) if os.path.exists("data.json") else []
    seen = set(json.load(open("seen.json"))) if os.path.exists("seen.json") else set()
    days = 3 if seen else int(os.environ.get("BACKFILL_DAYS", 14))
    idx = {key(c): c for c in db}
    for i in range(1, days + 1):
        d = dt.date.today() - dt.timedelta(days=i)
        for cik, acc in day_filings(d):
            if acc in seen: continue
            seen.add(acc); time.sleep(.12)   # SEC allows ~10 req/s
            try: c = parse_form_d(cik, acc)
            except Exception: continue
            if c and key(c) not in idx: idx[key(c)] = c
        print("done", d, len(idx)); json.dump(sorted(seen), open("seen.json", "w"))
    for c in news():
        txt = c.pop("_text"); k = key(c)
        if k in idx:
            for f, v in c.items():
                if v and not idx[k].get(f): idx[k][f] = v
        elif US_HINT.search(txt) and not NON_US.search(txt): idx[k] = c
    cutoff = (dt.date.today() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    out = [c for c in idx.values() if c["date"] >= cutoff and c["round"] == "Seed" and (c["amt"] is None or c["amt"] <= SEED_MAX_M)]
    json.dump(sorted(out, key=lambda c: c["date"], reverse=True), open("data.json", "w"), indent=1)
    print(len(out), "companies")
main()
