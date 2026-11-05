#!/usr/bin/env python3
"""Extract publicly visible tracking config (GTM / Google Ads) from a domain.

Usage: gtm_recon.py URL_OR_DOMAIN_OR_LIST.txt [more ...] [--gtm GTM-XXXX] [--observed events.json] [--json] [--no-pdf] [--out DIR]

Prints a markdown report and, by default, writes one PDF per run to
./reports/ (rendered with headless Chrome).

Reads only what every visitor's browser downloads: the homepage HTML, any
gtag.js / gtm.js it references. It does not and cannot see bids or budgets.
"""
import datetime
import html as htmllib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urljoin, urlparse

# Many sites 403 non-browser user agents. Override with GTM_RECON_UA if you prefer to identify yourself.
UA = os.environ.get("GTM_RECON_UA") or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
MAX_BYTES = 8 * 1024 * 1024


def fetch(url):
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("refusing non-http(s) URL: %s" % url)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(MAX_BYTES).decode("utf-8", "replace")


def balanced_object(text, idx):
    """Return the {...} object enclosing position idx (best effort)."""
    start = text.rfind("{", 0, idx)
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return ""


def field(obj, key):
    m = re.search(r'"%s":\s*("([^"]*)"|[-\d.]+|true|false)' % re.escape(key), obj)
    if not m:
        return None
    return m.group(2) if m.group(2) is not None else m.group(1)


def parse_container(js):
    conversions, events = [], []
    for m in re.finditer(r'"function":"__awct"', js):
        obj = balanced_object(js, m.start())
        conversions.append({
            "conversion_id": field(obj, "vtp_conversionId"),
            "label": field(obj, "vtp_conversionLabel"),
            "value": field(obj, "vtp_conversionValue"),
            "currency": field(obj, "vtp_currencyCode"),
        })
    for m in re.finditer(r'"function":"__gaawe"', js):
        obj = balanced_object(js, m.start())
        name = field(obj, "vtp_eventName")
        if name:
            events.append(name)
    # gtag-style conversions: 'send_to': 'AW-123/label'
    for m in re.finditer(r'(AW-\d+)/([\w-]+)', js):
        c = {"conversion_id": m.group(1)[3:], "label": m.group(2), "value": None, "currency": None}
        if not any(x["conversion_id"] == c["conversion_id"] and x["label"] == c["label"] for x in conversions):
            conversions.append(c)
    return conversions, sorted(set(events))


TAG_TYPES = {"__awct": "Google Ads conversion", "__gaawe": "GA4 event", "__googtag": "Google tag",
             "__gclidw": "Conversion linker", "__html": "Custom HTML", "__ua": "Universal Analytics",
             "__sp": "Google Ads remarketing", "__flc": "Floodlight counter", "__fls": "Floodlight sales"}
OPS = {"_eq": "=", "_cn": "contains", "_re": "matches", "_sw": "starts with", "_ew": "ends with",
       "_css": "matches selector", "_gt": ">", "_lt": "<", "_ge": ">=", "_le": "<="}


def extract_resource(js):
    """Parse the container's embedded `"resource": {...}` object (macros, tags, predicates, rules)."""
    i = js.find('"resource"')
    if i < 0:
        return None
    j = js.find("{", i)
    depth, in_str, esc = 0, False, False
    for k in range(j, len(js)):
        c = js[k]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                raw = js[j:k + 1]
                raw = re.sub(r"\\x([0-9a-fA-F]{2})", r"\\u00\1", raw).replace("\\'", "'")
                try:
                    return json.loads(raw)
                except ValueError:
                    return None
    return None


def short(v, n=70):
    v = str(v)
    return v if len(v) <= n else v[:n - 1] + "…"


def describe_macro(macros, i):
    m = macros[i]
    f = m.get("function", "")
    if f == "__e":
        return "event name"
    if f == "__u":
        return "URL " + m.get("vtp_component", "").lower()
    if f in ("__v", "__k", "__c"):
        return "%s %s" % ({"__v": "dataLayer", "__k": "cookie", "__c": "constant"}[f],
                          short(m.get("vtp_name") or m.get("vtp_value") or ""))
    if f == "__jsm":
        return "custom JS"
    if f == "__f":
        return "referrer"
    if f in ("__smm", "__remm"):
        return "lookup table #%d" % i
    return f.strip("_") + " #" + str(i)


def resolve(macros, v):
    if isinstance(v, list) and len(v) == 2 and v[0] == "macro" and isinstance(v[1], int) and v[1] < len(macros):
        return "{%s}" % describe_macro(macros, v[1])
    return None if v is None else str(v)


def pred_text(res, i):
    p = res["predicates"][i]
    arg0 = resolve(res["macros"], p.get("arg0")) or "?"
    return "%s %s %s" % (arg0, OPS.get(p.get("function"), p.get("function", "?").strip("_")),
                         short(resolve(res["macros"], p.get("arg1")) or ""))


def tag_details(res, t):
    macros, f = res["macros"], t.get("function")
    if f == "__awct":
        d = "AW-%s / %s" % (resolve(macros, t.get("vtp_conversionId")), resolve(macros, t.get("vtp_conversionLabel")))
        val = resolve(macros, t.get("vtp_conversionValue"))
        return d + (" · value " + val if val else "")
    if f == "__gaawe":
        return "event " + (resolve(macros, t.get("vtp_eventName")) or "?")
    if f == "__googtag":
        return "id " + (resolve(macros, t.get("vtp_tagId")) or "?")
    return ""


def parse_tag_map(res):
    """Map each tag to the trigger conditions that fire / block it (rules: OR of AND-ed predicates)."""
    if not res or not all(k in res for k in ("tags", "macros", "predicates", "rules")):
        return []
    fire = {i: [] for i in range(len(res["tags"]))}
    block = {i: [] for i in range(len(res["tags"]))}
    for rule in res["rules"]:
        ifs = [c for cl in rule if cl[0] == "if" for c in cl[1:]]
        unless = [c for cl in rule if cl[0] == "unless" for c in cl[1:]]
        cond = {"when": " AND ".join(pred_text(res, i) for i in ifs) or "always",
                "except": " OR ".join(pred_text(res, i) for i in unless)}
        for cl in rule:
            if cl[0] == "add":
                for t in cl[1:]:
                    fire[t].append(cond)
            elif cl[0] == "block":
                for t in cl[1:]:
                    block[t].append(cond["when"])
    out = []
    for i, t in enumerate(res["tags"]):
        out.append({"type": TAG_TYPES.get(t.get("function"), (t.get("function") or "?").strip("_")),
                    "function": t.get("function"), "details": tag_details(res, t),
                    "fires": fire[i], "blocked_by": block[i]})
    return out


BID_KEY = re.compile(r"bid|cpc|cpa|roas|budget", re.I)


def bid_scan(res):
    """Look for bid/budget-like parameter names in container tags and variables. Returns (checked, hits)."""
    checked, hits = 0, set()
    for group in ("tags", "macros"):
        for item in (res or {}).get(group, []):
            for k in item:
                checked += 1
                if BID_KEY.search(k):
                    hits.add("%s: %s" % (item.get("function", "?"), k))
    return checked, sorted(hits)


def recon(domain, extra_gtm=()):
    base = domain if domain.startswith("http") else "https://" + domain
    domain = urlparse(base).netloc or domain
    out = {"domain": domain, "url": base, "gtm_ids": [], "aw_ids": [], "ga4_ids": [],
           "conversions": [], "events": [], "tag_map": [], "bid_scan": {"checked": 0, "hits": []}, "observed": [], "notes": []}
    try:
        html = fetch(base)
    except Exception as e:
        out["notes"].append("homepage fetch failed: %s (retry through Chrome)" % e)
        if not extra_gtm:
            return out
        html = ""

    out["gtm_ids"] = sorted(set(re.findall(r'GTM-[A-Z0-9]{4,8}', html)))
    scripts = {html}
    if not out["gtm_ids"]:
        # consent-gated sites often keep the GTM loader in a first-party bundle, not in the HTML
        skip = ("recaptcha", "gstatic.com", "jquery", "bootstrap", "cloudflare", "facebook", "twimg")
        srcs = [u for u in re.findall(r'<script[^>]+src=["\']([^"\']+)', html) if not any(k in u for k in skip)]
        for u in srcs[:12]:
            try:
                found = re.findall(r'GTM-[A-Z0-9]{4,8}', fetch(urljoin(base, u)))
            except Exception:
                continue
            if found:
                out["gtm_ids"] = sorted(set(found))
                out["notes"].append("GTM ID found in linked script %s (not in the HTML)" % short(u, 80))
                break
    out["gtm_ids"] = sorted(set(out["gtm_ids"]) | set(extra_gtm))
    for gid in out["gtm_ids"]:
        try:
            scripts.add(fetch("https://www.googletagmanager.com/gtm.js?id=" + gid))
        except Exception as e:
            out["notes"].append("gtm.js fetch failed for %s: %s" % (gid, e))
    for m in re.finditer(r'googletagmanager\.com/gtag/js\?id=([\w-]+)', html):
        try:
            scripts.add(fetch("https://www.googletagmanager.com/gtag/js?id=" + m.group(1)))
        except Exception as e:
            out["notes"].append("gtag.js fetch failed for %s: %s" % (m.group(1), e))

    blob = "\n".join(scripts)
    out["aw_ids"] = sorted(set(re.findall(r'AW-\d{6,}', blob)))
    out["ga4_ids"] = sorted(set(re.findall(r'G-[A-Z0-9]{8,12}', blob)))
    for s in scripts:
        conv, ev = parse_container(s)
        out["conversions"] += [c for c in conv if c not in out["conversions"]]
        out["events"] = sorted(set(out["events"]) | set(ev))

    out["aw_ids"] = sorted(set(out["aw_ids"]) | {"AW-" + c["conversion_id"] for c in out["conversions"] if c["conversion_id"]})

    for sc in scripts:
        res = extract_resource(sc)
        try:
            tm = parse_tag_map(res)
        except (KeyError, IndexError, TypeError):
            tm = []
            out["notes"].append("container rules were in an unexpected shape; tag/trigger map skipped")
        out["tag_map"] += tm
        n, hits = bid_scan(res)
        out["bid_scan"]["checked"] += n
        out["bid_scan"]["hits"] = sorted(set(out["bid_scan"]["hits"]) | set(hits))
        for t in (res or {}).get("tags", []):
            if t.get("function") == "__awct":
                cid, lab = resolve(res["macros"], t.get("vtp_conversionId")), resolve(res["macros"], t.get("vtp_conversionLabel"))
                val = resolve(res["macros"], t.get("vtp_conversionValue"))
                for c in out["conversions"]:
                    if c["conversion_id"] == cid and c["label"] == lab and not c["value"] and val:
                        c["value"] = val
    if not out["tag_map"] and out["gtm_ids"]:
        out["notes"].append("could not parse tag/trigger rules from the container (unusual format or server-side)")

    if not out["gtm_ids"] and not out["aw_ids"]:
        out["notes"].append("no GTM/AW IDs in static HTML; tags may be injected by JS, server-side, or consent-gated (retry through Chrome)")
    if out["conversions"] and not any(c["value"] for c in out["conversions"]):
        out["notes"].append("no hard-coded conversion values; values are likely dynamic (dataLayer) or unset")
    return out


EVENT_NAMES = {"gtm.js": "page load", "gtm.dom": "DOM ready", "gtm.load": "window load", "gtm.linkClick": "link click",
               "gtm.click": "click", "gtm.timer": "timer", "gtm.formSubmit": "form submit", "gtm.scrollDepth": "scroll depth",
               "gtm.historyChange": "history change", "gtm.init_consent": "consent init", "gtm.elementVisibility": "element visible"}
KEY_TYPES = ("Google Ads conversion", "GA4 event", "Floodlight counter", "Floodlight sales")


def humanize(w):
    w = re.sub(r"\s*AND \{dataLayer gtm\.triggers\} matches \S+( \S+)*?(?= AND |$)", "", w)
    w = re.sub(r"\{event name\} = (\S+)", lambda m: "event: " + EVENT_NAMES.get(m.group(1), m.group(1)), w)
    return w


def fire_text(t):
    parts = []
    for f in t["fires"]:
        x = humanize(f["when"])
        if f["except"]:
            x += " (not if " + humanize(f["except"]) + ")"
        parts.append(x)
    return " | ".join(parts) or "never (no trigger)"


def split_tags(r):
    key = [t for t in r["tag_map"] if t["type"] in KEY_TYPES]
    other = {}
    for t in r["tag_map"]:
        if t["type"] not in KEY_TYPES:
            other[t["type"]] = other.get(t["type"], 0) + 1
    return key, other


def findings(r):
    out = []
    ads = [t for t in r["tag_map"] if t["type"] == "Google Ads conversion"]
    ga4 = [t for t in r["tag_map"] if t["type"] == "GA4 event"]
    out.append("%d Google Ads conversion tag(s) and %d GA4 event tag(s) in the container." % (len(ads), len(ga4)))
    for t in ads:
        when = fire_text(t)
        line = "Ads conversion %s fires on %s." % (t["details"].split(" · ")[0], when)
        if "consent" in when.lower():
            line += " Consent trigger: likely consent/remarketing plumbing, not a lead conversion."
        out.append(line)
        out.append("  Value sent: %s." % t["details"].split(" · value ")[1] if " · value " in t["details"]
                   else "  No conversion value is sent.")
    if not ads:
        out.append("No Google Ads conversion tag found; Ads conversions may be GA4 imports or server-side.")
    groups = {}
    for t in ga4:
        name = t["details"].replace("event ", "", 1) or "?"
        groups.setdefault(name, []).append(fire_text(t))
    for name, whens in groups.items():
        extra = "" if len(whens) == 1 else " (+%d more triggers)" % (len(whens) - 1)
        out.append("GA4 event %s: %d trigger(s). e.g. %s%s" % (name, len(whens), short(whens[0], 110), extra))
    return out


def render(r):
    lines = ["## %s" % r["domain"]]
    lines.append("- GTM containers: %s" % (", ".join(r["gtm_ids"]) or "none"))
    lines.append("- Google Ads IDs: %s" % (", ".join(r["aw_ids"]) or "none"))
    lines.append("- GA4 IDs: %s" % (", ".join(r["ga4_ids"]) or "none"))
    if r["conversions"]:
        lines.append("\n| AW id | label | value | currency |\n|---|---|---|---|")
        for c in r["conversions"]:
            lines.append("| %s | %s | %s | %s |" % (c["conversion_id"], c["label"] or "-", c["value"] or "-", c["currency"] or "-"))
    if r["events"]:
        lines.append("\nGA4 events: " + ", ".join(r["events"]))
    if r["tag_map"]:
        lines.append("\nKey findings:")
        lines += ["- " + f for f in findings(r)]
        key, other = split_tags(r)
        lines.append("\nTag -> trigger map (conversion and event tags):")
        for t in key:
            lines.append("- [%s] %s: %s%s" % (t["type"], t["details"] or "-", fire_text(t),
                         "; BLOCKED when " + " | ".join(t["blocked_by"]) if t["blocked_by"] else ""))
        if other:
            lines.append("- Other tags (support/infrastructure): " + ", ".join("%d %s" % (n, k) for k, n in sorted(other.items())))
    if r["bid_scan"]["checked"]:
        lines.append("\nBid/budget scan: checked %d tag and variable parameters; %s." % (
            r["bid_scan"]["checked"], "found " + ", ".join(r["bid_scan"]["hits"]) if r["bid_scan"]["hits"] else "no bid, CPC, CPA, ROAS or budget fields found"))
    if r["observed"]:
        lines.append("\nObserved dataLayer events (safe interaction run):")
        for ev in r["observed"]:
            lines.append("- %s" % short(json.dumps(ev, ensure_ascii=False), 160))
    for n in r["notes"]:
        lines.append("- note: " + n)
    return "\n".join(lines)


CSS = """body{font:13px/1.5 -apple-system,Helvetica,Arial,sans-serif;color:#111;margin:32px}
h1{font-size:20px}h2{font-size:16px;margin-top:28px;border-bottom:1px solid #ccc;padding-bottom:4px}
table{border-collapse:collapse;width:100%;margin:8px 0}td,th{border:1px solid #ddd;padding:4px 8px;text-align:left;font-size:12px}
th{background:#f3f3f3}code{font-size:12px}.note{color:#8a4b00}.foot{margin-top:32px;color:#666;font-size:11px}"""

FOOT = ("Source: publicly downloaded page and GTM/gtag scripts only. Bids, budgets, Quality Score and bid "
        "strategy are not exposed there. A conversion value is the advertiser's own figure, not a bid ceiling.")


def render_html(results):
    e = htmllib.escape
    parts = ["<h1>Tracking recon</h1><p>Generated %s</p>" % datetime.date.today().isoformat()]
    for r in results:
        parts.append("<h2>%s</h2><p><b>URL:</b> %s<br><b>GTM containers:</b> %s<br><b>Google Ads IDs:</b> %s<br><b>GA4 IDs:</b> %s</p>" % (
            e(r["domain"]), e(r["url"]), e(", ".join(r["gtm_ids"]) or "none"),
            e(", ".join(r["aw_ids"]) or "none"), e(", ".join(r["ga4_ids"]) or "none")))
        if r["conversions"]:
            rows = "".join("<tr><td>%s</td><td><code>%s</code></td><td>%s</td><td>%s</td></tr>" % (
                e(c["conversion_id"] or "-"), e(c["label"] or "-"), e(c["value"] or "-"), e(c["currency"] or "-"))
                for c in r["conversions"])
            parts.append("<table><tr><th>AW id</th><th>Label</th><th>Value</th><th>Currency</th></tr>%s</table>" % rows)
        if r["events"]:
            parts.append("<p><b>GA4 events (%d):</b> %s</p>" % (len(r["events"]), e(", ".join(r["events"]))))
        if r["tag_map"]:
            parts.append("<h3>Key findings</h3><ul>%s</ul>" % "".join("<li>%s</li>" % e(f) for f in findings(r)))
            key, other = split_tags(r)
            rows = "".join("<tr><td>%s</td><td>%s</td><td>%s%s</td></tr>" % (
                e(t["type"]), e(t["details"] or "-"), e(fire_text(t)),
                "<br><b>Blocked when:</b> " + e(" | ".join(t["blocked_by"])) if t["blocked_by"] else "") for t in key)
            parts.append("<h3>Conversion and event tags</h3><table><tr><th>Tag</th><th>Details</th><th>Fires when</th></tr>%s</table>" % rows)
            if other:
                parts.append("<p>Other tags (support/infrastructure): %s</p>" % e(", ".join("%d %s" % (n, k) for k, n in sorted(other.items()))))
        if r["bid_scan"]["checked"]:
            parts.append("<p><b>Bid/budget scan:</b> checked %d tag and variable parameters; %s.</p>" % (
                r["bid_scan"]["checked"], e("found " + ", ".join(r["bid_scan"]["hits"])) if r["bid_scan"]["hits"] else "no bid, CPC, CPA, ROAS or budget fields found"))
        if r["observed"]:
            rows = "".join("<tr><td>%s</td></tr>" % e(short(json.dumps(ev, ensure_ascii=False), 160)) for ev in r["observed"])
            parts.append("<h3>Observed dataLayer events (safe interaction run)</h3><table>%s</table>" % rows)
        parts += ['<p class="note">%s</p>' % e(n) for n in r["notes"]]
    parts.append('<p class="foot">%s</p>' % e(FOOT))
    return "<!doctype html><meta charset=utf-8><style>%s</style>%s" % (CSS, "".join(parts))


def find_chrome():
    for c in (os.environ.get("CHROME_PATH"), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if c and os.path.exists(c):
            return c
    for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        if shutil.which(n):
            return shutil.which(n)
    return None


def write_pdf(results, outdir):
    chrome = find_chrome()
    if not chrome:
        print("PDF skipped: Chrome/Chromium not found (set CHROME_PATH)", file=sys.stderr)
        return None
    os.makedirs(outdir, exist_ok=True)
    name = "gtm-recon-%s-%s.pdf" % ("_".join(re.sub(r"[^\w.-]", "", r["domain"]) for r in results)[:80],
                                   datetime.date.today().isoformat())
    pdf = os.path.abspath(os.path.join(outdir, name))
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "report.html")
        open(src, "w", encoding="utf-8").write(render_html(results))
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        "--user-data-dir=" + os.path.join(d, "profile"),
                        "--print-to-pdf=" + pdf, "file://" + src],
                       check=True, capture_output=True, timeout=60)
    return pdf


if __name__ == "__main__":
    argv = sys.argv[1:]
    outdir = "reports"
    if "--out" in argv:
        k = argv.index("--out")
        outdir = argv[k + 1]
        del argv[k:k + 2]
    observed = []
    if "--observed" in argv:
        k = argv.index("--observed")
        data = json.load(open(argv[k + 1], encoding="utf-8"))
        observed = data.get("events", []) if isinstance(data, dict) else data
        del argv[k:k + 2]
    extra_gtm = []
    while "--gtm" in argv:
        k = argv.index("--gtm")
        extra_gtm.append(argv[k + 1])
        del argv[k:k + 2]
    args = []
    for a in (a for a in argv if not a.startswith("--")):
        if a.lower().endswith(".txt") and os.path.isfile(a):
            args += [l.strip() for l in open(a, encoding="utf-8") if l.strip() and not l.lstrip().startswith("#")]
        else:
            args.append(a)
    if not args:
        sys.exit(__doc__)
    if observed and len(args) != 1:
        sys.exit("--observed needs exactly one URL")
    results = []
    for n, d in enumerate(args):
        if n:
            time.sleep(1)  # be polite when given a list
        results.append(recon(d, extra_gtm))
    if observed:
        results[0]["observed"] = observed
    if "--json" in argv:
        print(json.dumps(results, indent=2))
    else:
        print("\n\n".join(render(r) for r in results))
    if "--no-pdf" not in argv:
        pdf = write_pdf(results, outdir)
        if pdf:
            print("\nPDF: " + pdf)
