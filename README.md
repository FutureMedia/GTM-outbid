# GTM-outbid

## Competitor GTM tracking analysis

Read a website's **publicly visible Google Tag Manager setup** and turn it into a short report (markdown and PDF): which Google Ads conversions it fires, which GA4 events exist, what triggers each one, and whether any conversion value is hard-coded.

Built by Future Media as a Claude Code skill, and usable as a plain Python command-line tool.

> **How it helps in bidding.** It shows what a competitor measures and values, so you can decide where to raise bids on your own account. See [Outbidding](#outbidding).
> **What it is not.** It does **not** directly reveal competitor bids, budgets, Quality Score or bid strategy. Those live inside Google's ad systems and are never sent to a visitor's browser. See [What it can and cannot tell you](#what-it-can-and-cannot-tell-you).

## Why it exists

Every visitor's browser downloads a site's GTM container (`googletagmanager.com/gtm.js?id=GTM-XXXX`). It is public, and it describes how the site tracks visitors: tags, triggers, variables and Google Ads conversion IDs. Reading it shows how a business measures success, which is useful when planning your own tracking, conversion setup and PPC strategy. Doing it by hand means digging through minified JavaScript. This tool does it in one command and turns trigger rules into plain language.

The idea of reading a competitor's container comes from a public X post: <https://x.com/googlewaqas/status/2117613798370545971>. This project is an independent implementation with a tag-to-trigger parser, bid-signal scan, consent handling and PDF reports.

## What you get

For each URL:

- GTM container ID(s), Google Ads account IDs, GA4 property IDs
- Every Google Ads conversion tag: account, label, currency, and the value if it is hard-coded or read from a variable
- GA4 event names and the **trigger conditions** that fire each tag (page, event, URL rules, exceptions)
- **Key findings** in plain language, with support and infrastructure tags collapsed
- A **bid/budget scan** that checks every tag and variable parameter for bid, CPC, CPA, ROAS or budget fields and states plainly when none exist
- Optional **observed dataLayer events** from a safe interaction run
- A PDF report in `reports/`

## What it can and cannot tell you

| Can                                                 | Cannot                                               |
| --------------------------------------------------- | ---------------------------------------------------- |
| Which conversions a site tracks and what fires them | What the advertiser bids or spends                   |
| Event names, often in the order the funnel suggests | Quality Score, Ad Rank, bid strategy                 |
| Hard-coded conversion values, when they exist       | Dynamic values (these come from the page at runtime) |
| Which tags are consent-gated                        | Anything behind server-side GTM                      |

A conversion value is the advertiser's own figure sent to Google after a conversion. It is **not** a bid ceiling. With Smart Bidding (tCPA, tROAS) real cost per click depends on bid strategy, Quality Score and margin, none of which appear in the container. When the tool runs, the bid/budget scan prints how many parameters it checked and what it found. On every container we have tried it found none.

## Outbidding

The tool does not show anyone's bids. It shows what a competitor measures and values, so you can decide where to raise bids on your own account:

1. **Find their primary conversion.** Of all the conversions a competitor tracks, see which one the setup is built around and what value, if any, they assigned to it.
2. **Compare it with your own economics.** If a lead is worth $2,000 to you at a 25% margin and about $800 to them, you can afford a higher cost per acquisition on shared keywords.
3. **Choose where to compete.** Raise bids or targets only on keywords where you hold that advantage, and back off where you don't.
4. **Check it against your own data.** The [bid planner](#bid-planner-compare-your-economics-with-your-own-google-ads-exports) combines your Keyword Planner and Auction Insights exports with your max CPA and conversion rate, marks each keyword as can compete at the top of the range, within the range, or not viable, and lists the competitors you overlap with most.

Outbidding is not just a higher bid. Position is set by Ad Rank (bid × expected CTR × ad relevance × landing page experience), and you pay only the minimum needed to beat the advertiser below you. Treat what you learn about a competitor as a hypothesis, confirm it against your own Auction Insights and CPCs, and approve every bid change yourself. The tool never changes bids.

## Install

Requires Python 3.8+ (standard library only). Google Chrome or Chromium is optional and only needed for PDF output.

**As a Claude Code skill:**

```bash
git clone https://github.com/FutureMedia/GTM-outbid ~/.claude/skills/GTM-outbid
```

Then in Claude Code: `/GTM-outbid https://example.com`

**As a command-line tool:**

```bash
python3 scripts/gtm_outbid.py https://example.com
```

## Usage

```bash
# one or more URLs or domains
python3 scripts/gtm_outbid.py https://example.com another.com

# a text file with one URL per line (blank lines and # comments are ignored)
python3 scripts/gtm_outbid.py sites.txt

# skip the PDF, or choose where it goes
python3 scripts/gtm_outbid.py example.com --no-pdf
python3 scripts/gtm_outbid.py example.com --out ./my-reports

# structured output
python3 scripts/gtm_outbid.py example.com --json

# supply a container ID found in a browser (for consent-gated or blocked sites)
python3 scripts/gtm_outbid.py example.com --gtm GTM-XXXX

# add events captured by a safe interaction run (single URL)
python3 scripts/gtm_outbid.py example.com --gtm GTM-XXXX --observed events.json
```

Set `CHROME_PATH` if Chrome is in a non-standard location. Set `GTM_OUTBID_UA` to use your own User-Agent string. The default is a browser-like one because many sites return 403 to scripts.

Tracking parameters such as `gclid`, `gbraid` and `gad_*` add nothing. Drop them from the URL so the tool does not register fake ad clicks on someone else's account.

## Bid planner: compare your economics with your own Google Ads exports

Bids are not in the container, but your own Google Ads account can export useful estimates. [`scripts/bid_planner.py`](scripts/bid_planner.py) reads two CSV exports and your own numbers and tells you where you can afford to compete:

- **Keyword Planner** (Tools, Keyword Planner, download keyword ideas): estimated top-of-page bid range per keyword.
- **Auction Insights** (Campaigns, Ad groups or Keywords, Auction insights, Download): which competitors you overlap with and how often they are shown above you.
- **Your economics:** the most you can pay per conversion (`--max-cpa`, or `--value` with `--margin`) and your expected click-to-conversion rate (`--cvr`).

```bash
python3 scripts/bid_planner.py \
  --keywords keyword_planner.csv --auction auction_insights.csv \
  --value 2000 --margin 0.25 --cvr 0.01
```

Your allowable max CPC is `allowable CPA x conversion rate`. Each keyword is then compared with Google's estimated range and marked: can compete at the top of the range, can compete within the range, or not viable. Competitors are listed by overlap with a note on who is usually shown above you. The output is a markdown table and a PDF. Try it with the synthetic files in [`examples/`](examples/).

Only read-only access to your Google Ads account is needed. No login or API credentials are passed to this tool. The ranges are Google's market estimates, not any competitor's actual bids, and the result is only as good as your conversion-rate assumption. It never changes bids.

## Consent-gated sites

Some sites load their Google tags only after the cookie banner is accepted. The script already looks in the HTML and in linked first-party scripts for the GTM ID, which is enough for many consent-gated sites. When that still returns nothing, the skill's instructions have the agent open the page in a browser, accept the banner, read the IDs from `window.google_tag_manager`, and re-run with `--gtm`. If you do not want banners accepted, remove step 2 from `SKILL.md`.

## Safe interaction mode

Some data is only pushed to the page's `dataLayer` when a visitor interacts with it. [`scripts/safe_interactions.js`](scripts/safe_interactions.js) runs inside the page and:

- scrolls the page in steps;
- clicks at most six buttons or links whose labels match a short allowlist (for example "Show all photos", "View more", "Read more");
- records everything the page pushes to `dataLayer` and returns it as JSON for `--observed`.

It never submits a form, types into a field, creates an account, follows a link to another page or repeats a click. Anything inside a `<form>` or labelled like submit, sign up, log in, book, enquire, buy or pay is skipped.

This tool deliberately does **not** complete conversions: no fake enquiries, sign-ups or purchases. That would send fabricated leads to real people and feed junk conversions into someone else's ad account. To see value-bearing events, perform the action yourself with your own details, in a browser where the same snippet is listening.

## Responsible use

- It reads only what every visitor's browser already downloads. It does not log in, bypass protections or access private data.
- Respect each site's terms of service and robots rules, and keep request volume low. A one-second pause between URLs is built in.
- Use it for competitive research, audits and your own site, not for tampering with anyone's analytics or ad account.
- The output describes tracking configuration. Treat any inference about a competitor's strategy as a hypothesis.

## Limitations

- Server-side GTM, heavily obfuscated containers and unusual rule formats may parse partially. The report says when it could not parse.
- Variables show as types (for example `{lookup table #4}` or `{custom JS}`), not their contents.
- The event list is everything the container can fire, not an ordered funnel.
- Only Google tags are covered. Other trackers (Meta, LinkedIn, Bing, Criteo and so on) are not parsed.
- When several containers load, their tags are listed together without a per-container label.

## Repository layout

```
SKILL.md                   instructions Claude follows when the skill runs
scripts/gtm_outbid.py       extractor, parser, report and PDF writer
scripts/safe_interactions.js   in-page safe interaction probe
scripts/bid_planner.py     Keyword Planner and Auction Insights CSVs vs your economics
examples/                  synthetic sample CSVs for bid_planner.py
LICENSE
```

## Credits

Created by **Future Media**. Inspired by a public X post by the author of the thread linked above.

## License

MIT. See [LICENSE](LICENSE). © Future Media.
