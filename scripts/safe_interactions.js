// Safe-interaction probe for gtm-recon.
//
// Run inside the target page (browser console, or the browser tool's javascript step).
// It performs only harmless interactions, records what the page pushes to
// window.dataLayer, and returns JSON you can save and pass to
//   gtm_recon.py <url> --observed events.json
//
// What it does:   scrolls the page in steps; clicks at most MAX_CLICKS buttons/links whose
//                 label matches SAFE_LABEL, once each, skipping anything that looks like a
//                 submit, sign-up, purchase or form control.
// What it never:  submits a form, types into a field, creates an account, follows a link to
//                 another page, or repeats a click.
(async () => {
  const MAX_CLICKS = 6;
  const SAFE_LABEL = /^(show (phone|number|more|all)|view (more|photos|gallery)|read more|see more|click to call|call( agent)?|play|next|expand)\b/i;
  const UNSAFE_LABEL = /submit|send|sign ?up|log ?in|register|subscribe|book|enquir|inquir|contact (agent|seller)|buy|pay|apply|checkout|delete|accept|reject|download/i;
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  window.dataLayer = window.dataLayer || [];
  const start = window.dataLayer.length;
  const clicked = [];

  // 1. scroll in steps (fires scroll-depth style events)
  const h = () => document.documentElement.scrollHeight - innerHeight;
  for (const f of [0.25, 0.5, 0.75, 1]) {
    scrollTo({ top: h() * f });
    await wait(500);
  }
  scrollTo({ top: 0 });

  // 2. a few whitelisted clicks, once each
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden';
  };
  const candidates = [...document.querySelectorAll('button, a, [role=button]')].filter((el) => {
    const label = (el.innerText || el.getAttribute('aria-label') || '').trim();
    if (!label || label.length > 40 || !SAFE_LABEL.test(label) || UNSAFE_LABEL.test(label)) return false;
    if (el.closest('form') || el.getAttribute('type') === 'submit' || !visible(el)) return false;
    const href = el.getAttribute('href');
    return !href || href === '#' || href.startsWith('javascript:');
  });
  const seen = new Set();
  for (const el of candidates) {
    const label = el.innerText.trim().toLowerCase();
    if (clicked.length >= MAX_CLICKS || seen.has(label)) continue;
    seen.add(label);
    el.click();
    clicked.push(label);
    await wait(800);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  }
  await wait(500);

  // 3. report what the page pushed
  const plain = (o) => {
    try { return JSON.parse(JSON.stringify(o)); } catch (e) { return null; }
  };
  const events = [...window.dataLayer]
    .slice(start)
    .map(plain)
    .filter((e) => e && !Array.isArray(e) && e.event)
    .map((e) => { delete e['gtm.uniqueEventId']; return e; });
  const values = events.filter((e) => Object.keys(e).some((k) => /value|price|amount|revenue/i.test(k)));
  return JSON.stringify({ url: location.href, clicked, events, eventsWithValues: values }, null, 2);
})();
