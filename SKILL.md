---
name: GTM-outbid
description: Given a URL or domain, extract the site's publicly visible Google Tag Manager setup (GTM, Google Ads and GA4 IDs, conversion tags, event triggers, hard-coded values), optionally record safe dataLayer events, and save a PDF report. Use when invoked as /GTM-outbid <url>, or when asked to analyse a competitor's or our own conversions, funnel events or conversion values, or to decide where to compete on keywords. Built by Future Media.
argument-hint: <url> [more urls]
---

# GTM-outbid

Usage: `/GTM-outbid https://example.com` (one or more URLs or domains, or a `.txt` file with one URL per line; blank lines and `#` comments are ignored).

Reads only public data every visitor's browser downloads. It cannot see bids, budgets, Quality Score or bid strategy; never claim it does. The report's bid/budget scan states how many parameters were checked and what was found; quote it if the user asks about bids.

## Steps

1. Run the extractor on every URL the user gave. Strip tracking parameters (`gclid`, `gbraid`, `gad_*`, `utm_*`) first; they add nothing and could register as fake ad clicks. The script prints a markdown report and writes a PDF to `reports/`:
   ```bash
   python3 scripts/gtm_outbid.py <url> [<url> ...]
   ```
   Flags: `--gtm GTM-XXXX` supply a container ID found in the browser, `--observed events.json` add safe-interaction events (one URL only), `--json`, `--no-pdf`, `--out DIR`. The PDF needs Chrome or Chromium (`CHROME_PATH` to override).

2. Consent step. Do this when the script reports no GTM/AW IDs or a fetch error. The script already checks the HTML and linked first-party scripts; many sites still load Google tags only after the cookie banner is accepted. The user invoking this skill authorises accepting the banner (remove this step to disable):
   - Open the plain page URL in the built-in browser.
   - Click the banner's accept button ("Accept all", "Accept", "Allow all", "Agree"). Never click "Reject" or settings-only buttons. If the banner is only an informational notice and tags are already loaded, click nothing.
   - Wait about 5 seconds, then run in the page: `Object.keys(window.google_tag_manager||{}).filter(k=>/^(GTM|G|AW)-/.test(k))`
   - Re-run the script with each ID found: `... gtm_outbid.py <url> --gtm GTM-XXXX`.
   - If there is still no banner or no tags, say so; the site may use server-side tagging.

3. Safe interaction run (optional, when the user wants more than the static config). With the page open in the built-in browser, run the contents of `scripts/safe_interactions.js` in the page. It scrolls and clicks a few allowlisted, non-submitting controls and returns JSON of the `dataLayer` events pushed. Save that JSON to a file and re-run the script with `--observed <file>`.
   - Never submit a form, type into a field, sign up, log in or follow a link to another page, and never perform a real conversion on someone else's site. This would send fabricated leads and pollute their ad data. If the user wants value-bearing events, they perform the action themselves while the listener runs.
   - Click-based discoveries may need a listing or detail page, not a search-results page.

4. The report includes a tag-to-trigger map: for each tag, the event/page conditions that fire it and any exceptions or blockers. Use it to say which event fires each Ads conversion.

5. Reply with a short summary and the PDF path: container IDs, Ads accounts, number of conversion actions, events, any hard-coded values, the bid-scan result.

6. State the caveats when interpreting: values are often dynamic or missing; a conversion value is the advertiser's internal figure, not a bid ceiling; the event list is everything the container can fire, not an ordered funnel; server-side GTM hides most of this.

7. Bid suggestions (only if the user asks). Bids are not in the container, so use the user's own Google Ads exports with `scripts/bid_planner.py`. Ask for: (a) Keyword Planner CSV (Tools, Keyword Planner, download keyword ideas), (b) optionally an Auction Insights CSV (Campaigns/Ad groups/Keywords, Auction insights, Download), (c) their conversion value and margin, or max CPA, and (d) their expected click-to-conversion rate. Then run:
   ```bash
   python3 scripts/bid_planner.py --keywords kp.csv [--auction ai.csv] --value 2000 --margin 0.25 --cvr 0.01
   ```
   Present the keyword verdicts and competitor table. State that ranges are Google's estimates, not competitor bids, and that the result depends on the conversion-rate assumption. Do not guess a conversion rate; ask. Recommend changes for the user to approve and never change live bids.
