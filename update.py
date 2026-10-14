#!/usr/bin/env python3
"""Daily refresh: merges funding news (RSS) + SEC Form D filings into data.json. Stdlib only."""
import json, re, time, datetime as dt, urllib.request, xml.etree.ElementTree as ET
from html import unescape
UA = {"User-Agent": "FundedStartupsTracker you@example.com"}  # SEC requires a real contact: EDIT THIS
KEEP_DAYS = 180
FEEDS = [
 "https://techcrunch.com/category/venture/feed/",
 "https://news.crunchbase.com/feed/",
 "https://siliconangle.com/category/funding/feed/",
 "https://www.prnewswire.com/rss/financial-services-latest-news/venture-capital-list.rss",
 "https://www.globenewswire.com/RssFeed/subjectcode/18-Venture%20Capital/feedTitle/GlobeNewswire%20-%20Venture%20Capital",
 "https://www.finsmes.com/feed",
 "https://www.eu-startups.com/feed/",  # filtered out later unless US-tagged
]
US_HINT = re.compile(r"\b(San Francisco|New York|Austin|Boston|Seattle|Los Angeles|Chicago|Miami|Denver|Atlanta|Palo Alto|Menlo Park|Cambridge, MA|Brooklyn|Washington|Boulder|Pittsburgh|Philadelphia|Dallas|Houston|Salt Lake|U\.S\.|US-based|American)\b", re.I)
RAISE = re.compile(r"^(?P<name>[A-Z0-9][^,:–—]{1,60}?)\s+(?:raises?|raised|secures?|secured|lands?|closes?|closed|bags|nabs|snags|announces?)\s+(?:an?\s+)?(?:\$|US\$)(?P<amt>[\d.,]+)\s*(?P<u>million|billion|M|B|K)?\b(?P<rest>.*)", re.I)
ROUND = re.compile(r"(pre-seed|seed|series\s+[A-H]\+?)", re.I)
LED = re.compile(r"(?:led|co-led) by ([^.;]+?)(?: with| and participation|,? to |\.|$)", re.I)

def get(url, timeout=30):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()

def mult(u):
    return {"b":1000,"billion":1000,"k":.001}.get((u or "M").lower(), 1)

def norm_round(r):
    r = (r or "Seed").lower()
    if "seed" in r: return "Seed"
    m = re.search(r"series\s+([a-h])", r)
    return "Series A" if not m else ("Series "+m.group(1).upper() if m.group(1) in "abc" else "Series C+")

def news():
    out = []
    for f in FEEDS:
        try: root = ET.fromstring(get(f))
        except Exception as e: print("feed fail", f, e); continue
        for it in root.iter("item"):
            title = unescape(it.findtext("title") or "").strip()
            desc = unescape(re.sub("<[^>]+>", " ", it.findtext("description") or ""))
            m = RAISE.match(title)
            if not m: continue
            text = title + " " + desc
            if "eu-startups" in f and not US_HINT.search(text): continue
            try: amt = round(float(m["amt"].replace(",", "")) * mult(m["u"]), 2)
            except ValueError: continue
            r = ROUND.search(title) or ROUND.search(desc)
            led = LED.search(text)
            try: d = dt.datetime.strptime(it.findtext("pubDate")[5:16], "%d %b %Y").date().isoformat()
            except Exception: d = dt.date.today().isoformat()
            out.append(dict(name=m["name"].strip(), round=norm_round(r and r[0]), amt=amt, date=d,
                inv=led[1].strip() if led else "", fnd="", sec="", web="", li="",
                us=bool(US_HINT.search(text)), src=(it.findtext("link") or "").strip()))
    return out

def formd():
    """Latest SEC Form D filings: gives state, amount, and named executives (founders)."""
    out = []
    atom = ET.fromstring(get("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=D&count=100&output=atom"))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for e in atom.findall("a:entry", ns):
        link = e.find("a:link", ns).get("href")
        try:
            time.sleep(.2)  # SEC rate limit
            x = ET.fromstring(get(re.sub(r"[^/]+-index\.html?$", "primary_doc.xml", link)))
        except Exception: continue
        g = lambda p: (x.findtext(p) or "").strip()
        name, state = g(".//primaryIssuer/entityName"), g(".//primaryIssuer/issuerAddress/stateOrCountry")
        ind = g(".//industryGroup/industryGroupType")
        if re.search(r"fund|trust|\bL\.?P\.?\b|partners|real estate|reit|bank", name + ind, re.I): continue
        if len(state) != 2 or not state.isalpha() or state in ("CA",) and False: continue
        if not g(".//offeringData/typesOfSecuritiesOffered/isEquityType") == "true": continue
        sold = g(".//offeringSalesAmounts/totalAmountSold")
        if not sold.isdigit() or int(sold) < 250_000: continue
        people = [f"{g_('firstName')} {g_('lastName')}" for g_ in [] ]
        ppl = []
        for p in x.iter("relatedPersonInfo"):
            rel = (p.findtext("relatedPersonRelationshipList/relationship") or "")
            if re.search("Executive|Director", rel):
                ppl.append(f"{p.findtext('relatedPersonName/firstName','')} {p.findtext('relatedPersonName/lastName','')}".strip())
        out.append(dict(name=name.title(), round="Seed", amt=round(int(sold)/1e6, 2), date=g(".//signatureDate") or dt.date.today().isoformat(),
            inv="", fnd=", ".join(dict.fromkeys(ppl))[:120], sec=ind, web="", li="", us=True, state=state, src=link, form_d=True))
    return out

def key(c): return re.sub(r"\W", "", c["name"].lower())
def main():
    try: db = json.load(open("data.json"))
    except Exception: db = []
    cutoff = (dt.date.today() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    idx = {key(c): c for c in db}
    for c in news() + formd():
        if not c.get("us"): continue
        k = key(c)
        if k in idx:  # enrich existing record, never overwrite filled fields
            for f, v in c.items():
                if v and not idx[k].get(f): idx[k][f] = v
        else: idx[k] = c; 
    db = sorted((c for c in idx.values() if c["date"] >= cutoff), key=lambda c: c["date"], reverse=True)
    json.dump(db, open("data.json", "w"), indent=1)
    print(len(db), "companies")
main()
