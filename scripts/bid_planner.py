#!/usr/bin/env python3
"""Compare your own economics with Google Ads exports to see where you can afford to compete.

Usage:
  bid_planner.py --keywords keyword_planner.csv [--auction auction_insights.csv]
                 (--max-cpa N | --value N --margin M) --cvr R [--out DIR] [--no-pdf]

Inputs (all exported from your own Google Ads account):
  --keywords   Keyword Planner "download keyword ideas" CSV (has top-of-page bid ranges)
  --auction    Auction Insights CSV (competitor impression share, overlap, position above)
  --max-cpa    the most you can pay per conversion
  --value/--margin  alternative: conversion value and the share of it you can spend (0-1)
  --cvr        click-to-conversion rate you expect, e.g. 0.04 for 4%

Output is a suggestion list for a person to review. It never changes bids, and the
bid ranges are Google's market estimates, not any competitor's actual bids.
"""
import csv
import datetime
import html as htmllib
import io
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gtm_recon  # noqa: E402  (shares CSS and Chrome lookup with the recon report)


def read_table(path, keywords):
    """Read a Google Ads CSV export: any encoding Ads uses, preamble lines, comma or tab separated."""
    raw = open(path, "rb").read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig", "replace")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        low = line.lower()
        if sum(k in low for k in keywords) >= 2:
            delim = "\t" if line.count("\t") > line.count(",") else ","
            rows = list(csv.reader(io.StringIO("\n".join(lines[i:])), delimiter=delim))
            head = [h.strip().lower() for h in rows[0]]
            out = []
            for r in rows[1:]:
                if not r or not r[0].strip() or r[0].strip().lower().startswith("total"):
                    continue
                out.append(dict(zip(head, [c.strip() for c in r])))
            return out
    sys.exit("Could not find the table header in %s (expected columns like: %s)" % (path, ", ".join(keywords)))


def num(s):
    """'12.5%', '< 10%', '$1,234.50', '--' -> float or None."""
    if s is None:
        return None
    m = re.search(r"-?\d[\d,]*\.?\d*", s.replace(" ", " "))
    return float(m.group(0).replace(",", "")) if m else None


def col(row, *needles, exclude=()):
    for k in row:
        if all(n in k for n in needles) and not any(x in k for x in exclude):
            return row[k]
    return None


def keyword_verdicts(rows, max_cpc):
    out = []
    for r in rows:
        kw = col(r, "keyword", exclude=("count", "stats"))
        low = num(col(r, "top of page bid", "low"))
        high = num(col(r, "top of page bid", "high"))
        if not kw or low is None or high is None:
            continue
        if max_cpc >= high:
            verdict = "Can compete at the top of the estimated range"
        elif max_cpc >= low:
            verdict = "Can compete in the range; the top end may cost more than you can afford"
        else:
            verdict = "Below the estimated range; not viable at these economics"
        out.append({"keyword": kw, "searches": num(col(r, "avg. monthly searches")), "low": low, "high": high,
                    "headroom": round(max_cpc - high, 2), "verdict": verdict,
                    "competition": col(r, "competition", exclude=("index",)) or ""})
    return sorted(out, key=lambda x: -x["headroom"])


def auction_summary(rows):
    out = []
    for r in rows:
        dom = col(r, "domain") or col(r, "display url") or ""
        if not dom or dom.strip().lower() == "you":
            continue
        above = num(col(r, "position above"))
        outrank = num(col(r, "outranking"))
        overlap = num(col(r, "overlap"))
        if above is not None and above >= 50:
            note = "often shown above you"
        elif outrank is not None and outrank >= 50:
            note = "you usually outrank them"
        else:
            note = "mixed"
        out.append({"domain": dom, "overlap": overlap, "above": above, "outranking": outrank, "note": note})
    return sorted(out, key=lambda x: -(x["overlap"] or 0))


def render_md(ctx):
    L = ["# Bid planner", "",
         "Allowable CPA: %.2f · expected conversion rate: %.2f%% · allowable max CPC: %.2f" % (
             ctx["max_cpa"], ctx["cvr"] * 100, ctx["max_cpc"]), ""]
    if ctx["keywords"]:
        L += ["## Keywords (Google's top-of-page bid estimates vs your allowable max CPC)", "",
              "| Keyword | Searches/mo | Est. low | Est. high | Headroom vs high | Verdict |", "|---|---|---|---|---|---|"]
        for k in ctx["keywords"]:
            L.append("| %s | %s | %.2f | %.2f | %+.2f | %s |" % (
                k["keyword"], "-" if k["searches"] is None else int(k["searches"]), k["low"], k["high"], k["headroom"], k["verdict"]))
    if ctx["auction"]:
        L += ["", "## Competitors (Auction Insights)", "", "| Domain | Overlap | Position above you | You outrank | Note |", "|---|---|---|---|---|"]
        for a in ctx["auction"]:
            f = lambda v: "-" if v is None else "%.0f%%" % v
            L.append("| %s | %s | %s | %s | %s |" % (a["domain"], f(a["overlap"]), f(a["above"]), f(a["outranking"]), a["note"]))
    L += ["", ctx["disclaimer"]]
    return "\n".join(L)


DISCLAIMER = ("Bid ranges are Google's estimates for the market, not any competitor's actual bids. Max CPC = allowable CPA x "
              "expected conversion rate, so it is only as good as that rate. This is a suggestion list for a person to review; "
              "it never changes bids.")


def render_html(ctx):
    e = htmllib.escape
    p = ["<h1>Bid planner</h1><p>Generated %s</p>" % datetime.date.today().isoformat(),
         "<p>Allowable CPA <b>%.2f</b> · expected conversion rate <b>%.2f%%</b> · allowable max CPC <b>%.2f</b></p>" % (
             ctx["max_cpa"], ctx["cvr"] * 100, ctx["max_cpc"])]
    if ctx["keywords"]:
        rows = "".join("<tr><td>%s</td><td>%s</td><td>%.2f</td><td>%.2f</td><td>%+.2f</td><td>%s</td></tr>" % (
            e(k["keyword"]), "-" if k["searches"] is None else int(k["searches"]), k["low"], k["high"], k["headroom"], e(k["verdict"]))
            for k in ctx["keywords"])
        p.append("<h2>Keywords</h2><table><tr><th>Keyword</th><th>Searches/mo</th><th>Est. low</th><th>Est. high</th>"
                 "<th>Headroom vs high</th><th>Verdict</th></tr>%s</table>" % rows)
    if ctx["auction"]:
        f = lambda v: "-" if v is None else "%.0f%%" % v
        rows = "".join("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            e(a["domain"]), f(a["overlap"]), f(a["above"]), f(a["outranking"]), e(a["note"])) for a in ctx["auction"])
        p.append("<h2>Competitors</h2><table><tr><th>Domain</th><th>Overlap</th><th>Position above you</th>"
                 "<th>You outrank</th><th>Note</th></tr>%s</table>" % rows)
    p.append('<p class="foot">%s</p>' % e(ctx["disclaimer"]))
    return "<!doctype html><meta charset=utf-8><style>%s</style>%s" % (gtm_recon.CSS, "".join(p))


def write_pdf(ctx, outdir):
    chrome = gtm_recon.find_chrome()
    if not chrome:
        print("PDF skipped: Chrome/Chromium not found (set CHROME_PATH)", file=sys.stderr)
        return None
    os.makedirs(outdir, exist_ok=True)
    pdf = os.path.abspath(os.path.join(outdir, "bid-planner-%s.pdf" % datetime.date.today().isoformat()))
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "report.html")
        open(src, "w", encoding="utf-8").write(render_html(ctx))
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        "--user-data-dir=" + os.path.join(d, "profile"), "--print-to-pdf=" + pdf, "file://" + src],
                       check=True, capture_output=True, timeout=60)
    return pdf


def arg(argv, name):
    if name in argv:
        k = argv.index(name)
        if k + 1 >= len(argv):
            sys.exit("%s needs a value" % name)
        v = argv[k + 1]
        del argv[k:k + 2]
        return v
    return None


def main(argv):
    if not argv or "-h" in argv or "--help" in argv:
        sys.exit(__doc__)
    outdir = arg(argv, "--out") or "reports"
    kw_path, au_path = arg(argv, "--keywords"), arg(argv, "--auction")
    max_cpa, value, margin, cvr = (arg(argv, n) for n in ("--max-cpa", "--value", "--margin", "--cvr"))
    if not kw_path and not au_path:
        sys.exit("Give at least --keywords or --auction")
    try:
        if max_cpa is None and value is not None and margin is not None:
            max_cpa = float(value) * float(margin)
            if not 0 < float(margin) <= 1:
                sys.exit("--margin must be between 0 and 1")
        max_cpa = float(max_cpa) if max_cpa is not None else None
        cvr = float(cvr) if cvr is not None else None
    except ValueError:
        sys.exit("--max-cpa, --value, --margin and --cvr must be numbers")
    if kw_path and (max_cpa is None or cvr is None):
        sys.exit("Keyword verdicts need --cvr and either --max-cpa or --value with --margin")
    if cvr is not None and not 0 < cvr <= 1:
        sys.exit("--cvr must be between 0 and 1 (for example 0.04 for 4%)")

    max_cpc = round(max_cpa * cvr, 2) if kw_path else 0.0
    ctx = {"max_cpa": max_cpa or 0.0, "cvr": cvr or 0.0, "max_cpc": max_cpc, "disclaimer": DISCLAIMER,
           "keywords": keyword_verdicts(read_table(kw_path, ("keyword", "bid", "searches")), max_cpc) if kw_path else [],
           "auction": auction_summary(read_table(au_path, ("domain", "overlap", "share", "outranking", "position"))) if au_path else []}
    print(render_md(ctx))
    if "--no-pdf" not in argv:
        pdf = write_pdf(ctx, outdir)
        if pdf:
            print("\nPDF: " + pdf)


if __name__ == "__main__":
    main(sys.argv[1:])
