'use strict';
// The phone UI. Plain JS, no build step. Every screen reads one API endpoint; amounts arrive as integer cents.
// Names from the bank are untrusted text, so everything goes into the DOM through textContent (see h()).

const main = document.getElementById('main');
const toastEl = document.getElementById('toast');
const syncedEl = document.getElementById('synced');
const syncBtn = document.getElementById('sync');

const TITLES = { today: 'Today', spending: 'Spending', transactions: 'Transactions', bills: 'Bills', money: 'Money',
  investing: 'Investing', plan: 'Plan' };
const TAB_OF = { transactions: 'spending' };  // a sub-screen lights up the tab it belongs to
// Pay period shows only with a pay schedule in the plan; Custom opens two date fields.
const PERIODS = [['today', 'Today'], ['week', 'This week'], ['pay_period', 'Pay period'], ['month', 'This month'],
  ['last_week', 'Last week'], ['last_month', 'Last month'], ['custom', 'Custom']];
const PERIOD_INFO = {
  today: { title: 'Spent today', prev: 'yesterday' },
  week: { title: 'Spent this week', prev: 'same days last week', series: ['This week', 'Last week'] },
  pay_period: { title: 'Spent since payday', prev: 'same days last pay period', series: ['This pay period', 'Last pay period'] },
  month: { title: 'Spent this month', prev: 'same days last month', series: ['This month', 'Last month'] },
  last_week: { title: 'Spent last week', prev: 'the week before', series: ['Last week', 'Week before'] },
  last_month: { title: 'Spent last month', prev: 'the month before', series: ['Last month', 'Month before'] },
  custom: { title: 'Spent in these dates', prev: 'the same number of days before', series: ['These dates', 'Days before'] },
};
const SPENT_LABEL = { today: 'Today', week: 'This week', pay_period: 'Pay period', month: 'This month' };
const TOP_MERCHANTS = 10;
const MAX_ROWS = 2000;  // the most transactions one request returns
const PAGE = 100;       // rows drawn at a time; Show more adds the next lot
const CATEGORIES = ['FOOD_AND_DRINK', 'GENERAL_MERCHANDISE', 'TRANSPORTATION', 'ENTERTAINMENT', 'RENT_AND_UTILITIES',
  'PERSONAL_CARE', 'MEDICAL', 'TRAVEL', 'HOME_IMPROVEMENT', 'GENERAL_SERVICES', 'LOAN_PAYMENTS', 'BANK_FEES',
  'GOVERNMENT_AND_NON_PROFIT'];
// Color follows the bucket, never its rank (dataviz: fixed categorical order).
const BUCKETS = [['stock', 'Stocks', 's1'], ['etf', 'ETFs', 's2'], ['fund', 'Funds', 's3'], ['other', 'Other', 's4'], ['cash', 'Cash', 's5']];
const STALE_HOURS = 13;

const state = {
  screen: null, from: null, seq: 0, hiddenAt: 0, planDirty: false, attentionOpen: false,
  period: stored('period', 'month'), range: { start: stored('spendStart', ''), end: stored('spendEnd', '') },
  view: null,     // the transactions screen's period, for Ask Claude
  folds: {},      // which folded cards are open, kept while moving around the app
  scroll: {},     // where each screen was scrolled to, restored on the way back
  restore: false, // the next render is a return via Back
};

// --- small helpers

function stored(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch { return fallback; }
}
function store(key, value) {
  try { localStorage.setItem(key, value); } catch { /* private mode: not remembered */ }
}

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  setAttrs(el, attrs);
  for (const kid of kids.flat(Infinity)) {
    if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
  }
  return el;
}
function s(tag, attrs, ...kids) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  setAttrs(el, attrs);
  for (const kid of kids.flat(Infinity)) {
    if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
  }
  return el;
}
function setAttrs(el, attrs) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.setAttribute('class', v);
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
}

const ICONS = {
  check: [['path', { d: 'M5 12.5l4.5 4.5L19 7.5' }]],
  warning: [['path', { d: 'M12 4 2.5 20h19z' }], ['path', { d: 'M12 10v4.5M12 17.5h.01' }]],
  critical: [['circle', { cx: 12, cy: 12, r: 9 }], ['path', { d: 'M12 7.5v5.5M12 16.5h.01' }]],
  info: [['circle', { cx: 12, cy: 12, r: 9 }], ['path', { d: 'M12 11v5.5M12 7.5h.01' }]],
  payday: [['rect', { x: 3, y: 6, width: 18, height: 12, rx: 2 }], ['circle', { cx: 12, cy: 12, r: 2.5 }]],
  repeat: [['path', { d: 'M4 11V9a3 3 0 0 1 3-3h12M16 3l3 3-3 3M20 13v2a3 3 0 0 1-3 3H5M8 21l-3-3 3-3' }]],
  close: [['path', { d: 'M6 6l12 12M18 6 6 18' }]],
  chevron: [['path', { d: 'M6 9.5l6 6 6-6' }]],
  right: [['path', { d: 'M9.5 6l6 6-6 6' }]],
  back: [['path', { d: 'M14.5 6l-6 6 6 6' }]],
  expand: [['path', { d: 'M14 4h6v6M10 20H4v-6M20 4l-6.5 6.5M4 20l6.5-6.5' }]],
};
function icon(name) {
  return s('svg', { viewBox: '0 0 24 24', class: 'ico', 'aria-hidden': 'true' },
    ICONS[name].map(([tag, a]) => s(tag, a)));
}
// Status is never color alone: an icon in the status color plus a text label in ink.
const LEVEL_ICON = { urgent: 'critical', warn: 'warning', info: 'info' };
function statusLine(kind, text) {
  return h('span', { class: `status st-${kind}` }, icon(kind === 'good' ? 'check' : kind === 'serious' ? 'warning' : kind), text);
}

const USD = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
const USD0 = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const minus = (t) => t.replace('-', '−');
const money = (c) => (c == null ? '—' : minus(USD.format(c / 100)));
const money0 = (c) => (c == null ? '—' : minus(USD0.format(Math.round(c / 100))));
const signed = (c) => (c > 0 ? '+' : '') + money(c);
const compact = (c) => (Math.abs(c) >= 100000 ? money0(c) : money(c));  // cents only below $1,000, to fit a tile
function short$(dollars) {
  if (dollars >= 1000) return '$' + (dollars / 1000).toFixed(dollars % 1000 ? 1 : 0).replace(/\.0$/, '') + 'k';
  return '$' + Math.round(dollars);
}
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
const categoryLabel = (c) => { const t = (c || 'OTHER').replace(/_/g, ' ').toLowerCase(); return t[0].toUpperCase() + t.slice(1); };

function parseDay(iso) { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d); }
function todayDate() { const n = new Date(); return new Date(n.getFullYear(), n.getMonth(), n.getDate()); }
const DAY_FMT = new Intl.DateTimeFormat('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
const SHORT_FMT = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' });
const fmtDay = (iso) => DAY_FMT.format(parseDay(iso));
const fmtShort = (iso) => SHORT_FMT.format(parseDay(iso));
const YEAR_FMT = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
function fmtRange(start, end) {
  if (start === end) return fmtDay(start);
  const year = todayDate().getFullYear();
  const f = (iso) => (parseDay(iso).getFullYear() === year ? fmtShort(iso) : YEAR_FMT.format(parseDay(iso)));
  return `${f(start)} – ${f(end)}`;
}
function relDay(iso) {
  const n = Math.round((parseDay(iso) - todayDate()) / 864e5);
  if (n === 0) return 'today';
  if (n === 1) return 'tomorrow';
  if (n === -1) return 'yesterday';
  return n > 0 ? `in ${n} days` : `${-n} days ago`;
}
function ago(iso) {
  const min = Math.round((Date.now() - new Date(iso)) / 6e4);
  if (min < 1) return 'just now';
  if (min < 60) return `${min} min ago`;
  const hrs = Math.round(min / 60);
  if (hrs < 24) return `${hrs} h ago`;
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(iso));
}

async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { 'Content-Type': 'application/json' } });
  const isJson = (r.headers.get('content-type') || '').includes('json');
  const body = isJson ? await r.json() : await r.text();
  if (!r.ok) {
    const msg = (isJson && (body.error || (body.errors || []).join('; '))) || `HTTP ${r.status}`;
    throw Object.assign(new Error(msg), { body, status: r.status });
  }
  return body;
}

let toastTimer;
function toast(text, link) {
  const kids = [h('span', {}, text)];  // unlike h(), replaceChildren prints a missing link as "undefined"
  if (link) kids.push(h('a', { href: link.href, target: '_blank', rel: 'noopener' }, link.text));
  toastEl.replaceChildren(...kids);
  toastEl.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove('on'), link ? 7000 : 3500);
}

function card(title, ...kids) {
  return h('section', { class: 'card' }, title && h('h2', {}, title), kids);
}
function row(title, sub, amount, amountSub) {
  return h('li', { class: 'row' },
    h('div', { class: 'row-main' }, h('div', { class: 'row-title' }, title),
      // adjacent strings would merge into one flex item, so each gets its own span to keep the gap between them
      sub && h('div', { class: 'row-sub' }, [sub].flat().map((x) => (typeof x === 'string' ? h('span', {}, x) : x)))),
    amount != null && h('div', { class: 'row-amt' }, amount, amountSub && h('span', { class: 'sub' }, amountSub)));
}
function tag(text, iconName) {
  return h('span', { class: 'tag' }, iconName && icon(iconName), text);
}

// A card that starts folded, for detail most visits skip. It stays as you left it while you move around the app.
function foldCard(key, title, note, ...kids) {
  return h('section', { class: 'card fold' },
    h('details', { open: !!state.folds[key], ontoggle: (e) => { state.folds[key] = e.currentTarget.open; } },
      h('summary', {}, h('h2', {}, title), h('span', { class: 'fold-note' }, note, icon('chevron'))), kids));
}

// A sideways-scrolling chip row keeps the pressed chip on screen without scrolling the page.
function showPressed(chips) {
  requestAnimationFrame(() => {
    const b = chips.querySelector('[aria-pressed="true"]');
    if (!b) return;
    const c = chips.getBoundingClientRect(), r = b.getBoundingClientRect();
    if (r.left < c.left || r.right > c.right) chips.scrollLeft += r.left - c.left - (c.width - r.width) / 2;
  });
}

// --- transactions, shared by Today, Spending and the Transactions screen

// A selection is {period} or {period: 'custom', start, end}, plus optional q, category and merchant filters.
// merchant matches the name exactly, as Top merchants groups; q is a search.
function txnQuery(sel) {
  const p = new URLSearchParams();
  if (sel.period === 'custom') { p.set('start', sel.start); p.set('end', sel.end); } else p.set('period', sel.period);
  for (const key of ['q', 'category', 'merchant']) if (sel[key]) p.set(key, sel[key]);
  return p;
}
const txnHash = (sel) => '#transactions?' + txnQuery(sel);

// Money in shows with a plus. Money out that isn't spending (a transfer, a card payment) says so, so the
// list adds up to the total above it.
function txnRow(t) {
  const out = t.amount_cents > 0;
  const skipped = out && t.spending === false;
  return h('li', { class: `row${skipped ? ' not-spending' : ''}` },
    h('div', { class: 'row-main' },
      h('div', { class: 'row-title' }, t.merchant_name || t.name),
      h('div', { class: 'row-sub' }, h('span', {}, categoryLabel(t.category) + (t.account_mask ? ` · …${t.account_mask}` : '')),
        t.pending && tag('Pending'), skipped && tag('Not spending'))),
    h('div', { class: `row-amt${out ? '' : ' amt-in'}` }, out ? money(t.amount_cents) : '+' + money(-t.amount_cents)));
}
function txnRows(txns) {
  const out = [];
  let day = null;
  for (const t of txns) {
    if (t.date !== day) { day = t.date; out.push(h('li', { class: 'day-head' }, fmtDay(day))); }
    out.push(txnRow(t));
  }
  return out;
}

// Period chips, and Custom, which opens two date fields. onPick gets the new selection's period and dates.
// sel.start and sel.end are the dates on show, so Custom starts from them.
function periodPicker(sel, { hasPay, onPick }) {
  const today = isoOf(todayDate());
  const from = h('input', { type: 'date', name: 'start', value: sel.start || today, max: today });
  const to = h('input', { type: 'date', name: 'end', value: sel.end || today, max: today });
  const note = h('p', { class: 'sub', role: 'status' });
  const dates = h('div', { class: 'range', hidden: sel.period !== 'custom' },
    h('div', { class: 'field-row' }, field('From', from), field('To', to)), note);
  const pickDates = () => {
    const ok = from.value && to.value && from.value <= to.value;
    note.textContent = ok ? '' : 'Pick a From date on or before the To date.';
    if (ok) onPick({ period: 'custom', start: from.value, end: to.value });
  };
  from.addEventListener('change', pickDates);
  to.addEventListener('change', pickDates);
  const chips = h('div', { class: 'chips', role: 'group', 'aria-label': 'Period' },
    PERIODS.filter(([key]) => hasPay || key !== 'pay_period').map(([key, label]) => h('button', {
      class: 'chip', type: 'button', 'aria-pressed': String(key === sel.period), 'data-key': key,
      onclick: () => {
        for (const b of chips.children) b.setAttribute('aria-pressed', String(b.dataset.key === key));
        dates.hidden = key !== 'custom';
        if (key === 'custom') pickDates(); else onPick({ period: key });
      },
    }, label)));
  showPressed(chips);
  return h('div', { class: 'picker' }, chips, dates);
}

// Back to the screen you came from, where you left it. Opened straight from a link, it goes to Spending.
function backButton() {
  const to = state.from && state.from !== state.screen ? state.from : null;
  return h('button', { class: 'back', type: 'button',
    onclick: () => { if (to) { state.restore = true; history.back(); } else location.hash = '#spending'; } },
  icon('back'), TITLES[to || 'spending']);
}

function setSynced(iso) {
  if (!iso) { syncedEl.replaceChildren('Not synced yet'); return; }
  const stale = Date.now() - new Date(iso) > STALE_HOURS * 36e5;
  syncedEl.replaceChildren(stale ? statusLine('warning', `Stale · updated ${ago(iso)}`) : `Updated ${ago(iso)}`);
}

// --- Today

async function screenToday() {
  const d = await api('/api/today');
  setSynced(d.last_synced_at);
  const sts = d.safe_to_spend;
  let hero;
  if (sts.status !== 'ok') {
    hero = card(null,
      h('p', { class: 'hero-label' }, 'Left to spend today'),
      h('p', { class: 'hero' }, '—'),
      h('p', { class: 'hero-sub' }, `Add your ${sts.missing.join(' and ')} to the plan to see this.`),
      h('p', {}, h('a', { class: 'btn primary', href: '#plan' }, 'Open plan')));
  } else {
    hero = allowanceCard(sts);
  }

  const next = card('Coming up',
    d.next_bills.length === 0
      ? h('p', { class: 'empty' }, 'No bills in the next 35 days.')
      : h('ul', { class: 'rows' }, d.next_bills.map(billRow)),
    h('p', {}, h('a', { href: '#bills' }, 'Calendar and all bills')));

  const out = [hero, spentCard(d.spent), attentionCard(d.alerts, d.alerts_dismissed), recentCard(d.recent, d.spent.at(-1), d.date),
    next].filter(Boolean);
  if (d.tips.length) {
    out.push(card('Tips', d.tips.slice(0, 3).map((t) => h('div', { class: 'alert st-info' }, icon('info'),
      h('div', {}, h('div', { class: 'alert-title' }, t.title), h('div', { class: 'alert-detail' }, t.detail))))));
  }
  return out;
}

// Today, this week and the pay period so far (this month without a pay schedule). Each opens its transactions.
function spentCard(spent) {
  return card('Spent', h('div', { class: 'tiles' }, spent.map((p) =>
    h('a', { class: 'tile', href: txnHash({ period: p.period }) },
      h('span', { class: 'tile-label' }, SPENT_LABEL[p.period], icon('right')),
      h('span', { class: 'tile-amt' }, compact(p.total_cents)),
      h('span', { class: 'tile-sub' }, p.period === 'today' ? fmtShort(p.start) : `since ${fmtShort(p.start)}`)))));
}

// All transactions opens the pay period (or month), or, early in one, the dates back to the oldest row shown here,
// so it never lists fewer than this card.
function recentCard(recent, longest, today) {
  const oldest = recent.at(-1)?.date;
  const sel = !oldest || longest.start <= oldest ? { period: longest.period } : { period: 'custom', start: oldest, end: today };
  return card('Recent transactions',
    recent.length ? h('ul', { class: 'rows' }, txnRows(recent)) : h('p', { class: 'empty' }, 'No transactions yet.'),
    h('a', { class: 'link-row', href: txnHash(sel) }, 'All transactions', icon('right')));
}

// A fresh launch, or coming back after a long break, starts folded; it stays as you left it while you move around the
// app. Folded, the row still says how many and whether any is urgent. What's dismissed lives on the server, so the phone and the computer agree.
function attentionCard(alerts, dismissed) {
  let node;
  const change = async (btn, opts) => {
    btn.disabled = true;
    try {
      const r = await api('/api/alerts/dismissed', opts);
      // Bring back shows what came back; an emptied list folds, so the next new alert doesn't arrive open.
      state.attentionOpen = r.alerts.length > 0 && (state.attentionOpen || opts.method === 'DELETE');
      const next = attentionCard(r.alerts, r.dismissed_count);
      if (next) {
        node.replaceWith(next);
        next.querySelector('summary, button')?.focus({ preventScroll: true });
      } else {
        node.remove();
      }
    } catch (e) {
      btn.disabled = false;
      toast('Could not update the list: ' + e.message);
    }
  };
  const restoreButton = (text) => h('button', { class: 'link-btn', type: 'button', onclick: (e) => change(e.currentTarget, { method: 'DELETE' }) }, text);
  if (alerts.length === 0) {
    if (dismissed === 0) return null;
    node = h('p', { class: 'attention-quiet' }, `${plural(dismissed, 'alert')} dismissed`, restoreButton('Bring back'));
    return node;
  }
  const count = (level) => alerts.filter((a) => a.level === level).length;
  const [urgent, warn, info] = ['urgent', 'warn', 'info'].map(count);
  const label = urgent || info
    ? [urgent && `${urgent} urgent`, warn && `${warn} to check`, info && `${info} FYI`].filter(Boolean).join(', ')
    : `${plural(warn, 'thing')} to check`;
  const worst = LEVEL_ICON[alerts[0].level] || 'info';  // the API sorts most urgent first
  node = h('section', { class: 'card attention', 'aria-label': 'Needs attention' },
    h('details', { open: state.attentionOpen, ontoggle: (e) => { state.attentionOpen = e.currentTarget.open; } },
      h('summary', {}, h('span', { class: `attention-label st-${worst}` }, icon(worst), label), icon('chevron')),
      alerts.map((a) => h('div', { class: 'alert' },
        h('div', {}, a.level === 'urgent' && statusLine(LEVEL_ICON.urgent, 'Urgent'), h('div', { class: 'alert-title' }, a.title),
          h('div', { class: 'alert-detail' }, a.detail)),
        h('button', { class: 'alert-x', type: 'button', 'aria-label': `Remove: ${a.title}`,
          onclick: (e) => change(e.currentTarget, { method: 'PUT', body: JSON.stringify({ id: a.id }) }) }, icon('close')))),
      dismissed > 0 && h('p', { class: 'attention-restore' }, restoreButton(`Bring back ${dismissed} dismissed`))));
  return node;
}

// Today's share of what's left until payday, and how much of it is gone. Bills, the card and the buffer
// are already taken out, so this is the money for everything else.
function allowanceCard(sts) {
  const short = sts.available_cents < 0;
  const left = sts.left_today_cents;
  const spent = Math.max(sts.spent_today_cents, 0);
  const allowance = Math.max(sts.allowance_cents, 0);
  const over = !short && left < 0;

  const fill = h('div', { class: `gauge-fill${over ? ' over' : ''}` });
  fill.style.width = `${allowance ? Math.min(100, (100 * spent) / allowance) : spent ? 100 : 0}%`;
  const gauge = h('div', { class: 'gauge', role: 'img', 'aria-label': `${money(spent)} spent of ${money(allowance)} today` }, fill);

  return card(null,
    h('p', { class: 'hero-label' }, 'Left to spend today'),
    h('p', { class: 'hero' }, money(short ? 0 : left)),
    !short && [gauge,
      h('div', { class: 'gauge-legend' },
        h('a', { href: txnHash({ period: 'today' }) }, `${money(spent)} spent`),
        h('span', {}, `of ${money(allowance)} today`)),
      // Today's transactions, and the Spent tile, count a bill payment as spending; this share doesn't.
      sts.bills_paid_today_cents > 0 && h('p', { class: 'sub bills-note' },
        `Plus ${money(sts.bills_paid_today_cents)} of bills paid today, already set aside.`)],
    over && h('p', {}, statusLine('warning', `${money(-left)} over today. Tomorrow's share shrinks to cover it.`)),
    short
      ? h('p', {}, statusLine('critical', `Short ${money(-sts.available_cents)} before payday`))
      : h('p', { class: 'hero-sub' }, `${money(sts.allowance_cents)} a day until payday, ${fmtShort(sts.payday)} (${plural(sts.days, 'day')})`),
    h('details', {},
      h('summary', {}, 'How this is worked out'),
      h('p', { class: 'sub calc-note' }, 'Bills due before payday, your card balance and your buffer are already out. This covers everything else: groceries, eating out, gas, fun.'),
      h('ul', { class: 'calc' },
        sts.lines.map((l) => h('li', {}, h('span', {}, l.label), h('span', {}, signed(l.amount_cents)))),
        h('li', { class: 'total' }, h('span', {}, 'Left until payday'), h('span', {}, money(sts.available_cents))),
        spent > 0 && h('li', {}, h('span', {}, 'Plus spent today'), h('span', {}, signed(spent))),
        h('li', { class: 'total' }, h('span', {}, `÷ ${plural(sts.days, 'day')} = today's share`), h('span', {}, money(sts.allowance_cents)))),
      sts.bills.length > 0 && h('p', { class: 'sub' }, 'Bills before payday: ' +
        sts.bills.map((b) => `${b.name} ${money(b.amount_cents)} (${fmtShort(b.due)})`).join(', '))));
}

// --- Spending

// The chosen period is remembered on the phone; Custom remembers its dates too.
function setPeriod(sel) {
  state.period = sel.period; store('period', sel.period);
  if (sel.period === 'custom') {
    state.range = { start: sel.start, end: sel.end };
    store('spendStart', sel.start); store('spendEnd', sel.end);
  }
}

async function screenSpending() {
  const asked = state.period === 'custom' ? { period: 'custom', ...state.range } : { period: state.period };
  let d;
  try {
    d = await api('/api/spending?' + txnQuery(asked));
  } catch (e) {
    if (e.status !== 400) throw e;
    setPeriod({ period: 'month' });  // the pay period lost its pay schedule, or the saved dates are no good
    d = await api('/api/spending?period=month');
  }
  setSynced(d.last_synced_at);
  const p = state.period, info = PERIOD_INFO[p];
  const view = p === 'custom' ? { period: 'custom', start: d.start, end: d.end } : { period: p };
  const picker = periodPicker({ period: p, start: d.start, end: d.end },
    { hasPay: !!d.payday, onPick: (sel) => { setPeriod(sel); render(); } });

  const change = d.change_cents;
  const hero = card(null,
    h('p', { class: 'hero-label' }, info.title),
    h('p', { class: 'hero' }, money(d.total_cents)),
    h('p', { class: 'hero-sub' }, `${change > 0 ? '↑' : change < 0 ? '↓' : ''} ${money(Math.abs(change))} vs ${money(d.previous.total_cents)} ${info.prev}`.trim()),
    p !== 'today' && h('p', { class: 'sub range-note' }, fmtRange(d.start, d.end)),
    h('a', { class: 'link-row', href: txnHash(view) }, 'See transactions', icon('right')));

  const out = [picker, hero];
  if (info.series) {
    out.push(card('Running total',
      lineChart([
        { label: info.series[0], color: 's1', points: d.trend.current },
        { label: info.series[1], color: 's2', points: d.trend.previous },
      ], { title: 'Running total' })));
  }
  const monthly = p === 'month' || p === 'last_month';
  out.push(card('By category', d.by_category.length
    ? categoryBars(d.by_category, monthly ? d.targets_cents : {}, (c) => txnHash({ ...view, category: c.category }))
    : h('p', { class: 'empty' }, 'No spending in this period.')));
  // Detail most visits skip starts folded.
  if (d.by_merchant.length) {
    const n = d.by_merchant.length;
    out.push(foldCard('merchants', 'Top merchants', n > TOP_MERCHANTS ? `Top ${TOP_MERCHANTS} of ${n}` : plural(n, 'merchant'),
      h('ul', { class: 'rows' }, d.by_merchant.slice(0, TOP_MERCHANTS).map((m) =>
        linkRow(txnHash({ ...view, merchant: m.merchant }), m.merchant, plural(m.count, 'charge'), money(m.total_cents)))),
      n > TOP_MERCHANTS && h('a', { class: 'link-row', href: txnHash(view) }, 'All transactions', icon('right'))));
  }
  if (d.unusual.length) {
    out.push(foldCard('flagged', 'Flagged, last 2 weeks', String(d.unusual.length), h('ul', { class: 'rows' },
      d.unusual.map((u) => row(u.merchant, [statusLine('warning', u.detail), fmtShort(u.date)], money(u.amount_cents))))));
  }
  return out;
}

// A row that opens somewhere: the title is the link, and the whole row is its tap target.
function linkRow(href, title, sub, amount) {
  const r = row(h('a', { class: 'stretch', href }, title), sub, amount);
  r.classList.add('row-link');
  r.append(icon('right'));
  return r;
}

// --- Transactions: any period, searchable, and what the spending among them adds up to

function selFrom(params) {
  const start = params.get('start'), end = params.get('end');
  return { ...(start && end ? { period: 'custom', start, end } : { period: params.get('period') || 'month' }),
    q: params.get('q') || '', category: params.get('category') || '', merchant: params.get('merchant') || '' };
}

async function screenTransactions(params) {
  let sel = selFrom(params);
  const fetchSel = (x) => api('/api/transactions?' + txnQuery(x) + '&limit=' + MAX_ROWS);
  let d;
  try {
    d = await fetchSel(sel);
  } catch (e) {
    if (e.status !== 400) throw e;
    toast(`Showing this month: ${e.message}`);  // a link from before the pay schedule went, or hand-typed dates
    sel = { ...sel, period: 'month', start: undefined, end: undefined };
    d = await fetchSel(sel);
    history.replaceState(history.state, '', txnHash(sel));
  }
  setSynced(d.last_synced_at);
  state.view = sel;

  const summary = h('section', { class: 'card' });
  const list = h('ul', { class: 'rows' });
  const more = h('div', {});
  let shown = PAGE, seq = 0, timer;

  function draw() {
    const info = PERIOD_INFO[sel.period];
    const left = d.transactions.length - shown;
    // replaceChildren, unlike h(), would print a skipped `false` as text, hence the filters
    const filters = [
      sel.category && [categoryLabel(sel.category), { category: '' }],
      sel.merchant && [sel.merchant, { merchant: '' }],
    ].filter(Boolean);
    summary.replaceChildren(...[
      filters.length > 0 && h('p', { class: 'filters' }, filters.map(([label, off]) =>
        h('button', { class: 'filter', type: 'button', 'aria-label': `Show all, not just ${label}`,
          onclick: () => load({ ...sel, ...off }) }, h('span', {}, label), icon('close')))),
      h('p', { class: 'hero-label' }, info.title),
      h('p', { class: 'hero' }, money(d.spent_cents)),
      h('p', { class: 'hero-sub' }, `${plural(d.count, 'transaction')} · ${fmtRange(d.start, d.end)}`),
      d.count > d.transactions.length && h('p', { class: 'sub' },
        `Showing the newest ${d.transactions.length.toLocaleString('en-US')}. Pick fewer days to see the rest.`),
    ].filter(Boolean));
    list.replaceChildren(...txnRows(d.transactions.slice(0, shown)));
    more.replaceChildren(...[
      !d.transactions.length && h('p', { class: 'empty' }, sel.q || sel.category || sel.merchant ? 'Nothing matches.' : 'No transactions in these dates.'),
      left > 0 && h('p', {}, h('button', { class: 'btn small', type: 'button', onclick: () => { shown += PAGE; draw(); } },
        `Show more (${left.toLocaleString('en-US')} left)`)),
    ].filter(Boolean));
  }

  // A new period, search or filter refetches just the results and keeps the address in step, so Back,
  // a sync or Ask Claude sees the same selection.
  async function load(next) {
    if (!list.isConnected) return;  // left the screen while a search was waiting
    sel = next;
    state.view = sel;
    history.replaceState(history.state, '', txnHash(sel));
    const mine = ++seq;
    main.classList.add('loading');
    try {
      const r = await fetchSel(sel);
      if (mine !== seq) return;
      d = r; shown = PAGE; draw();
    } catch (e) {
      if (mine === seq) toast('Could not load: ' + e.message);
    } finally {
      if (mine === seq && list.isConnected) main.classList.remove('loading');  // else a newer render owns it
    }
  }

  const search = h('input', { type: 'search', name: 'q', value: sel.q, placeholder: 'Search merchants…',
    'aria-label': 'Search transactions', autocomplete: 'off', enterkeyhint: 'search' });
  search.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => load({ ...sel, q: search.value.trim() }), 250);
  });
  search.addEventListener('keydown', (e) => { if (e.key === 'Enter') search.blur(); });  // puts the keyboard away

  draw();
  return [backButton(),
    periodPicker({ ...sel, start: d.start, end: d.end },
      { hasPay: !!d.payday, onPick: (p) => load({ ...sel, ...p }) }),
    summary,
    h('section', { class: 'card' }, search, list, more)];
}

function categoryBars(cats, targets, linkFor) {
  const max = Math.max(...cats.map((c) => Math.max(c.total_cents, targets[c.category] || 0)), 1);
  return h('ul', { class: 'bars' }, cats.map((c) => {
    const target = targets[c.category];
    const over = target != null && c.total_cents > target;
    const bar = h('div', { class: 'bar' });
    bar.style.width = `${(100 * c.total_cents) / max}%`;
    const track = h('div', { class: 'bar-track' }, bar);
    if (target != null) {
      const mark = h('div', { class: 'bar-target', title: `Target ${money0(target)}` });
      mark.style.left = `calc(${(100 * target) / max}% - 1px)`;
      track.append(mark);
    }
    const kids = [
      h('div', { class: 'bar-head' },
        h('span', { class: 'row-title' }, c.label, linkFor && icon('right')),
        h('span', {}, h('b', {}, money(c.total_cents)), target != null && h('span', { class: 'sub' }, ` of ${money0(target)}`))),
      track,
      over && statusLine('warning', `${money0(c.total_cents - target)} over target`)];
    return h('li', { class: 'bar-row' }, linkFor ? h('a', { class: 'bar-link', href: linkFor(c) }, kids) : kids);
  }));
}

// Line chart: one hairline crosshair snaps to the nearest point; the tooltip lists every series.
// opts: zero (y starts at $0; off for values that never get near it), label (a point's x label),
// head and rowLabel (the table's first column), band ({low, high, label}: cents per point, shaded around
// the first series), legend (false for a single series the card title already names), title (names the
// larger view), and for that view: sheet, width and height.
function lineChart(series, opts = {}) {
  const { zero = true, label = (p) => fmtShort(p.date), head = 'Day', rowLabel = (p, i) => i + 1, band = null,
    legend: showLegend = true, title = 'Chart', sheet = false } = opts;
  const W = Math.round(opts.width || Math.max(260, main.clientWidth - 64)), H = Math.round(opts.height || 170);
  const padR = 40, padT = 8, padB = 22;
  const n = Math.max(...series.map((x) => x.points.length));
  const plotW = W - padR, plotH = H - padT - padB;
  const values = [...series.flatMap((x) => x.points.map((p) => p.total_cents / 100)),
    ...(band ? [...band.low, ...band.high].map((c) => c / 100) : [])];
  const maxV = Math.max(...values, 1);
  const minV = zero ? 0 : Math.min(...values);
  const ticks = Math.max(3, Math.min(6, Math.round(plotH / 70)));  // a taller chart gets more gridlines
  const raw = (maxV - minV || maxV) / ticks, pow = 10 ** Math.floor(Math.log10(raw)), m = raw / pow;
  const step = (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * pow;
  const top = Math.ceil(maxV / step) * step;
  const bottom = Math.min(Math.floor(minV / step) * step, top - step);  // a flat line on a step still gets a band
  const x = (i) => (n === 1 ? plotW / 2 : (i * plotW) / (n - 1));
  const y = (v) => padT + plotH - ((v - bottom) / (top - bottom)) * plotH;

  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', tabindex: '0',
    'aria-label': series.map((x) => `${x.label}: ${money(x.points.at(-1)?.total_cents ?? 0)}`).join('; ') + '. Use arrow keys for each day.' });
  for (let v = bottom; v <= top + 1e-9; v += step) {
    svg.append(s('line', { class: v === bottom ? 'baseline' : 'gridline', x1: 0, x2: plotW, y1: y(v), y2: y(v) }),
      s('text', { x: W, y: y(v) + 4, 'text-anchor': 'end' }, short$(v)));
  }
  const dates = series[0].points;
  if (dates.length) {
    svg.append(s('text', { x: 0, y: H - 4 }, label(dates[0])),
      s('text', { x: x(dates.length - 1), y: H - 4, 'text-anchor': dates.length - 1 > n * 0.8 ? 'end' : 'middle' }, label(dates.at(-1))));
    // The larger view has room for dates in between, kept clear of the two ends.
    const between = sheet ? Math.floor(x(dates.length - 1) / 110) - 1 : 0;
    for (let k = 1; k <= between; k++) {
      const i = Math.round((k * (dates.length - 1)) / (between + 1));
      svg.append(s('text', { x: x(i), y: H - 4, 'text-anchor': 'middle' }, label(dates[i])));
    }
  }
  if (band) {
    const upper = band.high.map((c, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(c / 100).toFixed(1)}`).join('');
    const lower = band.low.map((c, i) => `L${x(i).toFixed(1)},${y(c / 100).toFixed(1)}`).reverse().join('');
    svg.append(s('path', { class: 'band', fill: `var(--${series[0].color})`, d: upper + lower + 'Z' }));
  }
  // Draw the comparison first so the current period sits on top.
  for (const ser of [...series].reverse()) {
    if (!ser.points.length) continue;
    svg.append(s('path', { class: 'series', stroke: `var(--${ser.color})`,
      d: ser.points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.total_cents / 100).toFixed(1)}`).join('') }));
    const last = ser.points.length - 1;
    svg.append(s('circle', { class: 'dot', r: 4, fill: `var(--${ser.color})`, cx: x(last), cy: y(ser.points[last].total_cents / 100) }));
  }
  const cross = s('line', { class: 'cross', y1: padT, y2: padT + plotH, visibility: 'hidden' });
  const dots = series.map((ser) => s('circle', { class: 'dot', r: 4, fill: `var(--${ser.color})`, visibility: 'hidden' }));
  svg.append(cross, ...dots);
  const hit = s('rect', { x: -8, y: 0, width: plotW + 16, height: H, fill: 'transparent' });
  svg.append(hit);

  const tip = h('div', { class: 'tip', 'aria-hidden': 'true' });
  const wrap = h('div', { class: 'chart' }, svg, tip);
  let idx = -1;
  function show(i) {
    idx = Math.max(0, Math.min(n - 1, i));
    cross.setAttribute('x1', x(idx)); cross.setAttribute('x2', x(idx)); cross.setAttribute('visibility', 'visible');
    const rows = [];
    series.forEach((ser, k) => {
      const p = ser.points[idx];
      dots[k].setAttribute('visibility', p ? 'visible' : 'hidden');
      if (!p) return;
      dots[k].setAttribute('cx', x(idx)); dots[k].setAttribute('cy', y(p.total_cents / 100));
      const key = h('span', { class: 'key-line' }); key.style.background = `var(--${ser.color})`;
      rows.push(h('div', {}, key, h('b', {}, money(p.total_cents)), h('span', { class: 'tip-date' }, `${ser.label} · ${label(p)}`)));
    });
    if (band) rows.push(h('div', { class: 'tip-date' }, `${band.label}: ${money0(band.low[idx])} to ${money0(band.high[idx])}`));
    tip.replaceChildren(...rows);
    tip.classList.add('on');
    const scale = wrap.clientWidth / W;
    const left = Math.min(Math.max(x(idx) * scale - tip.offsetWidth / 2, 0), wrap.clientWidth - tip.offsetWidth);
    tip.style.transform = `translate(${left}px, -${tip.offsetHeight + 6}px)`;
  }
  function hide() {
    idx = -1; tip.classList.remove('on'); cross.setAttribute('visibility', 'hidden');
    dots.forEach((dt) => dt.setAttribute('visibility', 'hidden'));
  }
  function fromPointer(e) {
    const r = svg.getBoundingClientRect();
    show(Math.round((((e.clientX - r.left) / r.width) * W / plotW) * (n - 1)));
  }
  // Capture keeps the crosshair on the finger even when it slides off the chart's edge.
  hit.addEventListener('pointerdown', (e) => { svg.setPointerCapture?.(e.pointerId); fromPointer(e); });
  svg.addEventListener('pointermove', (e) => { if (idx >= 0 || e.pointerType === 'mouse') fromPointer(e); });
  if (!sheet) {
    // On the page, a vertical swipe hands the touch to scrolling (pointercancel), and the reading goes with it.
    // In the larger view the reading stays put until the next touch.
    svg.addEventListener('pointerleave', hide);
    svg.addEventListener('pointercancel', hide);
    svg.addEventListener('blur', hide);
  }
  wrap.addEventListener('contextmenu', (e) => e.preventDefault());  // a long press scrubs; it never opens a menu
  svg.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault();
      show(idx < 0 ? n - 1 : idx + (e.key === 'ArrowRight' ? 1 : -1));
    } else if (e.key === 'Escape' && !sheet) hide();
  });

  const legend = showLegend && h('div', { class: 'legend' }, series.map((ser) => {
    const key = h('span', { class: 'key-line' }); key.style.background = `var(--${ser.color})`;
    return h('span', {}, key, ser.label, h('b', {}, money(ser.points.at(-1)?.total_cents ?? 0)));
  }));
  if (sheet) {
    requestAnimationFrame(() => { if (n) show(n - 1); });  // open on the latest reading
    return [legend, wrap].filter(Boolean);
  }
  const expand = h('button', { class: 'icon-btn expand', type: 'button', 'aria-label': `Open ${title} larger`,
    onclick: () => openChartSheet(series, { ...opts, title }, expand) }, icon('expand'));
  const chartHead = h('div', { class: 'chart-head' }, legend || h('span', {}), expand);
  const table = h('details', { class: 'table-view' }, h('summary', {}, 'Show as table'),
    h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, head), series.map((ser) => h('th', {}, ser.label)),
      band && [h('th', {}, 'Low'), h('th', {}, 'High')])),
      h('tbody', {}, Array.from({ length: n }, (_, i) => h('tr', {}, h('td', {}, rowLabel(series[0].points[i], i)),
        series.map((ser) => h('td', {}, ser.points[i] ? money0(ser.points[i].total_cents) : '')),
        band && [h('td', {}, money0(band.low[i])), h('td', {}, money0(band.high[i]))])))));
  return [chartHead, wrap, table];
}

// The same chart as large as the screen allows, in a sheet that slides up from the bottom and leaves the
// same way: the close button, Escape, a tap above it, or a pull down on its top edge.
function openChartSheet(series, opts, opener) {
  const close = h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Close' }, icon('close'));
  const head = h('div', { class: 'sheet-head' },
    h('span', { class: 'grabber', 'aria-hidden': 'true' }), h('h2', { id: 'sheet-title' }, opts.title), close);
  const body = h('div', { class: 'sheet-body' });
  const sheet = h('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'sheet-title' }, head, body);
  const backdrop = h('div', { class: 'backdrop' });
  const behind = [document.querySelector('header.top'), main, document.querySelector('nav.tabs')].filter(Boolean);
  document.body.append(backdrop, sheet);
  behind.forEach((el) => { el.inert = true; });

  // Fill the sheet: its height less the legend and the reading above the plot. A legend that wraps
  // to more lines than guessed is measured and the chart drawn again to fit.
  function draw(legendH = opts.legend === false ? 0 : 32) {
    const height = Math.max(150, Math.min(body.clientHeight - legendH - 72 - 8, 640));
    body.replaceChildren(...lineChart(series, { ...opts, sheet: true, width: body.clientWidth, height }));
    const legend = body.querySelector('.legend');
    const actual = legend ? legend.offsetHeight + 8 : 0;
    if (actual > legendH) draw(actual);
  }
  let frame = 0;
  const onResize = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(() => draw()); };
  const onKey = (e) => { if (e.key === 'Escape') shut(); };

  let open = true;
  function shut() {
    if (!open) return;
    open = false;
    window.removeEventListener('resize', onResize);
    window.removeEventListener('hashchange', shut);
    document.removeEventListener('keydown', onKey);
    behind.forEach((el) => { el.inert = false; });
    sheet.classList.remove('on');
    backdrop.classList.remove('on');
    let gone = false;
    const remove = () => { if (!gone) { gone = true; sheet.remove(); backdrop.remove(); } };
    sheet.addEventListener('transitionend', (e) => { if (e.target === sheet) remove(); });
    setTimeout(remove, 500);  // no transition runs under reduced motion or in a background tab
    if (opener?.isConnected) opener.focus({ preventScroll: true });
  }

  // Pull down on the top edge: the sheet follows the finger, and a long or quick pull closes it.
  let drag = null;
  head.addEventListener('pointerdown', (e) => {
    if (e.target.closest('button')) return;
    head.setPointerCapture(e.pointerId);
    drag = { y0: e.clientY, dy: 0, trail: [{ y: e.clientY, t: e.timeStamp }] };
    sheet.classList.add('dragging');
    backdrop.classList.add('dragging');
  });
  head.addEventListener('pointermove', (e) => {
    if (!drag) return;
    drag.dy = e.clientY - drag.y0;
    drag.trail.push({ y: e.clientY, t: e.timeStamp });
    if (drag.trail.length > 5) drag.trail.shift();
    const room = sheet.offsetHeight;
    const dy = drag.dy > 0 ? drag.dy : (drag.dy * room * 0.55) / (room + 0.55 * -drag.dy);  // resists going up
    sheet.style.transform = `translateY(${dy}px)`;
    backdrop.style.opacity = String(1 - Math.max(0, dy) / room);
  });
  const endDrag = () => {
    if (!drag) return;
    const a = drag.trail[0], b = drag.trail.at(-1);
    const speed = (b.y - a.y) / Math.max(1, b.t - a.t);  // px per ms, down is positive
    const far = drag.dy;
    drag = null;
    sheet.classList.remove('dragging');
    backdrop.classList.remove('dragging');
    sheet.style.transform = '';
    backdrop.style.opacity = '';
    if (far > 120 || speed > 0.5) shut();
  };
  head.addEventListener('pointerup', endDrag);
  head.addEventListener('pointercancel', endDrag);

  close.addEventListener('click', shut);
  backdrop.addEventListener('click', shut);
  window.addEventListener('resize', onResize);
  window.addEventListener('hashchange', shut);
  document.addEventListener('keydown', onKey);
  draw();
  requestAnimationFrame(() => {
    sheet.classList.add('on');
    backdrop.classList.add('on');
    close.focus({ preventScroll: true });
  });
}

// --- Bills

function billRow(b) {
  const tags = [
    b.paid && statusLine('good', 'Paid'),
    b.covered === false && statusLine('critical', 'Not covered'),
    b.autopay && tag('Autopay'),
    b.estimated && tag('Estimate'),
    b.source === 'detected' && tag('Detected', 'repeat'),
  ];
  return row(b.name, [`${fmtDay(b.due)} · ${relDay(b.due)}`, tags], (b.estimated ? '~' : '') + money(b.amount_cents));
}

async function screenBills() {
  const d = await api('/api/bills');
  setSynced(d.last_synced_at);
  const unpaid = d.bills.filter((b) => !b.paid);
  const hero = card(null,
    h('p', { class: 'hero-label' }, 'Still to pay, next 35 days'),
    h('p', { class: 'hero' }, money(unpaid.reduce((t, b) => t + b.amount_cents, 0))),
    h('p', { class: 'hero-sub' }, `${plural(unpaid.length, 'bill')} through ${fmtDay(d.end)}`),
    d.uncovered > 0 && h('p', {}, statusLine('critical', `${plural(d.uncovered, 'bill')} not covered by checking before the next payday`)));

  const events = [...d.bills.map((b) => ({ date: b.due, bill: b })), ...d.paydays.map((p) => ({ date: p }))]
    .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : a.bill ? 1 : -1));
  const timeline = card('Schedule', events.length === 0
    ? h('p', { class: 'empty' }, 'No bills found. Add them on the Plan screen.')
    : h('ul', { class: 'rows' }, events.map((e) => e.bill ? billRow(e.bill)
      : h('li', { class: 'row' }, h('div', { class: 'row-main' },
        h('div', { class: 'row-title' }, statusLine('good', 'Payday')),
        h('div', { class: 'row-sub' }, `${fmtDay(e.date)} · ${relDay(e.date)}`))))));

  const declared = new Set(d.declared.map((b) => b.name.toLowerCase()));
  const recurring = card('Recurring charges found',
    d.recurring.length === 0
      ? h('p', { class: 'empty' }, 'None found yet. This needs a few months of history.')
      : h('ul', { class: 'rows' }, d.recurring.map((r) => h('li', { class: 'row' },
        h('div', { class: 'row-main' }, h('div', { class: 'row-title' }, r.merchant),
          h('div', { class: 'row-sub' }, `${r.cadence} · next ${fmtShort(r.next_due)}`, !r.fixed && tag('Varies'))),
        h('div', { class: 'row-amt' }, (r.fixed ? '' : '~') + money(r.amount_cents)),
        declared.has(r.merchant.toLowerCase())
          ? tag('In plan', 'check')
          : h('button', { class: 'btn small', type: 'button', 'aria-label': `Add ${r.merchant} to plan`, onclick: (e) => addBill(r, e.currentTarget) }, 'Add')))));

  const out = [hero, calendarCard(d.calendar), timeline, recurring];
  if (d.price_flags.length) {
    out.push(card('Price changes', h('ul', { class: 'rows' },
      d.price_flags.map((u) => row(u.merchant, [statusLine('warning', u.detail), fmtShort(u.date)], money(u.amount_cents))))));
  }
  return out;
}

// A month grid: paydays are squares, bills are dots (hollow once paid), so shape carries the meaning and color
// only repeats it. Tapping a day lists what lands on it.
const MONTH_FMT = new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric' });
const WEEKDAYS = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];
const isoOf = (dt) => `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;

function calendarCard(events) {
  const byDay = new Map();
  for (const e of events) byDay.set(e.date, [...(byDay.get(e.date) || []), e]);
  const today = todayDate();
  const todayIso = isoOf(today);
  let offset = 0;  // 0 = this month, 1 = next
  let picked = todayIso;
  const title = h('h2', { class: 'cal-title' });
  const prev = h('button', { class: 'icon-btn cal-nav', type: 'button', 'aria-label': 'This month', onclick: () => { offset = 0; picked = todayIso; draw(); } }, '‹');
  const next = h('button', { class: 'icon-btn cal-nav', type: 'button', 'aria-label': 'Next month', onclick: () => { offset = 1; picked = null; draw(); } }, '›');
  const grid = h('div', { class: 'cal-grid' });
  const detail = h('div', { class: 'cal-detail', 'aria-live': 'polite' });

  function marker(e) {
    const cls = e.kind === 'payday' ? 'm-pay' : e.paid ? 'm-paid' : 'm-bill';
    return h('span', { class: `mark ${cls}` });
  }
  function describe(e) {
    return `${e.kind === 'payday' ? 'Payday' : e.name} ${money0(e.amount_cents)}${e.kind === 'bill' && e.paid ? ' paid' : ''}`;
  }
  function draw() {
    const first = new Date(today.getFullYear(), today.getMonth() + offset, 1);
    const days = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
    title.textContent = MONTH_FMT.format(first);
    prev.disabled = offset === 0;
    next.disabled = offset === 1;
    const cells = WEEKDAYS.map((w) => h('span', { class: 'cal-wd', 'aria-hidden': 'true' }, w));
    for (let i = 0; i < first.getDay(); i++) cells.push(h('span', {}));
    for (let day = 1; day <= days; day++) {
      const iso = isoOf(new Date(first.getFullYear(), first.getMonth(), day));
      const evs = byDay.get(iso) || [];
      const label = `${fmtDay(iso)}${evs.length ? ': ' + evs.map(describe).join(', ') : ''}`;
      cells.push(h('button', {
        class: `cal-day${iso === todayIso ? ' today' : ''}${iso < todayIso ? ' past' : ''}`, type: 'button',
        'aria-label': label, 'aria-pressed': String(iso === picked),
        onclick: () => { picked = picked === iso ? null : iso; draw(); },
      }, h('span', { class: 'cal-num' }, day), h('span', { class: 'cal-marks' }, evs.slice(0, 3).map(marker))));
    }
    grid.replaceChildren(...cells);

    // The picked day, or the whole month when nothing is picked
    const monthIso = isoOf(first).slice(0, 7);
    const shown = picked ? byDay.get(picked) || [] : events.filter((e) => e.date.startsWith(monthIso));
    const head = picked ? fmtDay(picked) : `All of ${MONTH_FMT.format(first).split(' ')[0]}`;
    detail.replaceChildren(h('div', { class: 'day-head' }, head), shown.length === 0
      ? h('p', { class: 'empty' }, picked === todayIso ? 'Nothing due today.' : 'Nothing due.')
      : h('ul', { class: 'rows' }, shown.map((e) => {
        const sub = [!picked && fmtShort(e.date), e.kind === 'bill' && e.paid && statusLine('good', 'Paid')].filter(Boolean);
        return row(h('span', { class: 'cal-row-title' }, marker(e), e.name), sub.length ? sub : null,
          (e.kind === 'payday' ? '+' : '') + money(e.amount_cents));
      })));
  }
  draw();
  const legend = h('div', { class: 'legend cal-legend' },
    h('span', {}, h('span', { class: 'mark m-pay' }), 'Payday'),
    h('span', {}, h('span', { class: 'mark m-bill' }), 'Bill due'),
    h('span', {}, h('span', { class: 'mark m-paid' }), 'Paid'));
  return h('section', { class: 'card' }, h('div', { class: 'cal-head' }, prev, title, next), legend, grid, detail);
}

async function addBill(r, btn) {
  btn.disabled = true;
  try {
    const plan = await api('/api/plan');
    plan.bills.push({ name: r.merchant, amount: r.amount_cents / 100, due_day: parseDay(r.next_due).getDate(),
      category: r.category, fixed: r.fixed });
    await api('/api/plan', { method: 'PUT', body: JSON.stringify(plan) });
    toast(`Added ${r.merchant} to your plan.`);
    render();
  } catch (e) {
    btn.disabled = false;
    toast('Could not add: ' + e.message);
  }
}

// --- Money

async function screenMoney() {
  const [d, st] = await Promise.all([api('/api/accounts'), api('/api/status')]);
  setSynced(d.last_synced_at);
  const nw = d.net_worth;
  const hero = card(null,
    h('p', { class: 'hero-label' }, 'Net worth'),
    h('p', { class: 'hero' }, money0(nw.net_cents)),
    h('p', { class: 'hero-sub' }, `${money0(nw.assets_cents)} in accounts − ${money0(nw.liabilities_cents)} owed on cards`));

  const accounts = card('Accounts', h('ul', { class: 'rows' }, d.accounts.map((a) => {
    const title = a.mask ? `${a.name} …${a.mask}` : a.name;  // accounts typed into data/manual.json have no mask
    if (a.type === 'credit') {
      const used = a.limit_cents ? Math.round((100 * a.owed_cents) / a.limit_cents) : null;
      return row(title, `${a.institution} · credit card`, money(a.owed_cents),
        used != null ? `owed · ${used}% of ${money0(a.limit_cents)}` : 'owed');
    }
    if (a.available_cents != null && a.available_cents !== a.current_cents) {
      return row(title, `${a.institution} · ${a.subtype || a.type}`, money(a.available_cents), `available · ${money(a.current_cents)} current`);
    }
    return row(title, `${a.institution} · ${a.subtype || a.type}`, money(a.current_cents));
  })));

  const r = d.runway;
  const runway = card('Runway',
    r.typical_monthly_cents
      ? [h('p', { class: 'hero' }, `${r.months} `, h('small', {}, r.months === 1 ? 'month' : 'months')),
        h('p', { class: 'hero-sub' }, `Cash on hand ${money0(r.cash_cents)} at a typical ${money0(r.typical_monthly_cents)} a month of spending.`)]
      : h('p', { class: 'empty' }, 'Needs a full month of spending history.'));

  const links = card('Connections', h('ul', { class: 'rows' }, st.items.map((it) => {
    const ok = it.status === 'ok';
    const c = it.consent;
    return row(it.institution,
      [ok ? statusLine('good', 'Connected') : statusLine('critical', it.error_code || it.status),
        it.last_synced_at ? `synced ${ago(it.last_synced_at)}` : 'never synced',
        c && `re-link by ${fmtShort(c.expires)} (${c.estimated ? 'about ' : ''}${c.days_left} ${c.days_left === 1 ? 'day' : 'days'} left)`],
      null);
  })), h('p', { class: 'sub' }, 'To fix a login or re-link, run ./run.sh link on the computer.'));
  return [hero, accounts, runway, links];
}

// --- Investing

async function screenInvesting() {
  const d = await api('/api/holdings');
  setSynced(d.last_synced_at);
  const a = d.allocation;
  const hero = card(null,
    h('p', { class: 'hero-label' }, 'Investments'),
    h('p', { class: 'hero' }, money0(a.total_cents)),
    h('p', { class: 'hero-sub' }, `${money0(a.invested_cents)} invested · ${money0(a.idle_cash_cents)} in cash`));

  const byBucket = Object.fromEntries(a.buckets.map((b) => [b.bucket, b]));
  const present = BUCKETS.filter(([k]) => byBucket[k]);
  const stack = h('div', { class: 'stack', role: 'img',
    'aria-label': present.map(([k, label]) => `${label} ${byBucket[k].pct}%`).join(', ') },
  present.map(([k, label, color]) => {
    const seg = h('span', { title: `${label} ${money0(byBucket[k].value_cents)} (${byBucket[k].pct}%)` });
    seg.style.flex = `${byBucket[k].value_cents} 0 0`;
    seg.style.background = `var(--${color})`;
    return seg;
  }));
  const allocation = card('Allocation', a.total_cents ? [stack, h('ul', { class: 'rows' }, present.map(([k, label, color]) => {
    const key = h('span', { class: 'key-rect' }); key.style.background = `var(--${color})`;
    return h('li', { class: 'row' }, key, h('div', { class: 'row-main' }, label),
      h('div', { class: 'row-amt' }, money0(byBucket[k].value_cents), h('span', { class: 'sub' }, `${byBucket[k].pct}%`)));
  })), a.largest && h('p', { class: 'sub' }, `Largest position: ${a.largest.ticker || a.largest.name}, ${a.largest.pct_of_invested}% of invested.`)]
    : h('p', { class: 'empty' }, 'No holdings yet.'));

  const li = d.left_to_invest;
  const left = card('Ready to invest',
    h('p', { class: 'hero' }, money0(li.total_cents)),
    h('p', { class: 'hero-sub' }, 'Idle cash plus checking beyond bills, card balance and buffer.'),
    li.missing.length > 0 && h('p', {}, statusLine('info', `Add ${li.missing.join(' and ')} to the plan for a truer number.`)),
    h('details', {}, h('summary', {}, 'How this is worked out'),
      h('ul', { class: 'calc' }, li.lines.map((l) => h('li', {}, h('span', {}, l.label), h('span', {}, signed(l.amount_cents)))))),
    h('ul', { class: 'rows' }, li.options.map((o) => row(o.label, null, money0(o.amount_cents)))));

  const holdings = card('Holdings', h('ul', { class: 'rows' }, d.holdings.map((x) => {
    const gain = x.cost_basis_cents != null ? x.value_cents - x.cost_basis_cents : null;
    const pct = gain != null && x.cost_basis_cents ? ` (${((100 * gain) / x.cost_basis_cents).toFixed(1)}%)` : '';
    // a manual holding is worth its last close, so say which day that was
    const priced = x.account_key.startsWith('manual:') && (x.updated_at ? `price of ${fmtShort(x.updated_at)}` : 'no price yet');
    const what = [x.ticker && x.name, x.quantity && `${+x.quantity.toFixed(4)} sh`].filter(Boolean).join(' · ');
    return row(x.ticker || x.name, [what, priced],
      money0(x.value_cents), gain != null ? `${signed(gain).replace(/\.\d\d$/, '')}${pct}` : null);
  })));
  return [hero, historyCard(d.history), projectionCard(a.invested_cents, d.contributions_per_year_cents), allocation, left, holdings];
}

const RANGES = [['1M', 1], ['3M', 3], ['6M', 6], ['1Y', 12]];
const RANGE_WORDS = { 1: 'the last month', 3: 'the last 3 months', 6: 'the last 6 months', 12: 'the last year' };

// What the shares held today were worth on each past day. It isn't a record of the account (deposits and
// sales aren't in it), so the card says so.
function historyCard(hist) {
  if (hist.points.length < 2) {
    return card('Value over time', h('p', { class: 'empty' }, 'Needs a few days of prices. They load on the next sync.'));
  }
  let months = +stored('range', 3) || 3;
  const chips = h('div', { class: 'chips', role: 'group', 'aria-label': 'Range' });
  const body = h('div', {});
  function draw() {
    chips.replaceChildren(...RANGES.map(([text, m]) => h('button', {
      class: 'chip', type: 'button', 'aria-pressed': String(m === months),
      onclick: () => { months = m; store('range', m); draw(); },
    }, text)));
    const from = todayDate();
    from.setMonth(from.getMonth() - months);
    const pts = hist.points.filter((p) => p.date >= isoOf(from));
    const inRange = pts.length >= 2;
    const shown = inRange ? pts : hist.points.slice(-2);
    const first = shown[0].total_cents, last = shown.at(-1).total_cents;
    const change = last - first;
    const pct = first ? (100 * change) / first : 0;
    const arrow = change > 0 ? '↑' : change < 0 ? '↓' : '';
    body.replaceChildren(
      h('p', { class: 'change' },
        h('b', {}, `${arrow} ${signed(change).replace(/\.\d\d$/, '')} (${pct > 0 ? '+' : ''}${minus(pct.toFixed(1))}%)`.trim()),
        h('span', { class: 'sub' }, inRange ? ` over ${RANGE_WORDS[months]}` : ` since ${fmtShort(shown[0].date)}`)),
      ...lineChart([{ label: 'Value', color: 's1', points: shown }],
        { zero: false, legend: false, head: 'Date', rowLabel: (p) => fmtShort(p.date), title: 'Value over time' }));
  }
  draw();
  return card('Value over time', chips, body,
    h('p', { class: 'sub' }, 'The shares you hold now, at each day’s closing price. Money you added or took out along the way isn’t in it.'),
    hist.unpriced.length > 0 && h('p', { class: 'sub' }, `Left out (no price history): ${hist.unpriced.join(', ')}.`));
}

// Compounding once a year: the value grows by the rate, then the year's contributions go in. 7% is roughly the
// long-run return of US stocks after inflation, so results read in today's dollars.
const RATES = [0.04, 0.07, 0.10];

function project(start, perYear, years, rate) {
  const out = [start];
  for (let k = 1; k <= years; k++) out.push(out[k - 1] * (1 + rate) + perYear);
  return out.map(Math.round);
}

function projectionCard(startCents, contribCents) {
  const yearsIn = h('input', { type: 'range', min: 1, max: 40, step: 1, value: +stored('years', 10) || 10, name: 'years', 'aria-label': 'Years ahead' });
  const extraIn = num(stored('extra', ''), { name: 'extra-monthly', placeholder: '0' });
  const yearsOut = h('b', {});
  const body = h('div', {});
  const yearLabel = (p) => (p.year === 0 ? 'Now' : `Year ${p.year}`);
  function draw() {
    const years = +yearsIn.value;
    yearsOut.textContent = plural(years, 'year');
    const extra = Math.max(readNum(extraIn) || 0, 0);
    const perYear = contribCents + Math.round(extra * 100) * 12;
    const [low, mid, high] = RATES.map((r) => project(startCents, perYear, years, r));
    const putIn = startCents + perYear * years;
    body.replaceChildren(
      h('p', { class: 'hero' }, money0(mid.at(-1))),
      h('p', { class: 'hero-sub' }, `in ${plural(years, 'year')} at 7% a year`),
      h('p', { class: 'sub' }, `${money0(low.at(-1))} at 4% · ${money0(high.at(-1))} at 10%`),
      h('p', { class: 'sub' }, `${money0(putIn)} of it is money in: today’s ${money0(startCents)}` +
        (perYear ? ` plus ${money0(perYear)} a year` : '') + `. The other ${money0(Math.max(mid.at(-1) - putIn, 0))} is growth.`),
      ...lineChart([{ label: 'At 7%', color: 's1', points: mid.map((c, i) => ({ year: i, total_cents: c })) }],
        { legend: false, label: yearLabel, head: 'Year', rowLabel: yearLabel, band: { low, high, label: '4% to 10%' },
          title: 'Where it could grow' }));
  }
  yearsIn.addEventListener('input', () => { store('years', yearsIn.value); draw(); });
  extraIn.addEventListener('input', () => { store('extra', extraIn.value); draw(); });
  draw();
  return card('Where it could grow',
    h('label', { class: 'field' }, h('span', {}, 'Years ahead: ', yearsOut), yearsIn),
    field('Extra each month ($)', extraIn),
    contribCents > 0 && h('p', { class: 'sub' }, `Already counts your ${money0(contribCents)} a year of paycheck contributions.`),
    body,
    h('p', { class: 'sub' }, 'Starts from what’s invested; cash isn’t counted. Not a forecast. 7% is about the long-run average for US stocks after inflation, so these are in today’s dollars. Real years swing well above and below it.'));
}

// --- Plan

function field(label, input) { return h('label', { class: 'field' }, h('span', {}, label), input); }
function num(value, attrs = {}) {
  return h('input', { type: 'text', inputmode: 'decimal', autocomplete: 'off', value: value ?? '', ...attrs });
}
const readNum = (input) => {
  const v = input.value.replace(/[$,\s]/g, '');
  return v === '' ? null : Number(v);
};

async function screenPlan() {
  const [plan, status] = await Promise.all([api('/api/plan'), api('/api/status')]);
  setSynced(status.last_synced_at);
  const inc = plan.income || {};
  const f = {
    amount: num(inc.amount, { name: 'income-amount', placeholder: 'e.g. 2450…' }),
    schedule: h('select', { name: 'schedule' }, ['weekly', 'biweekly', 'semimonthly', 'monthly'].map((x) =>
      h('option', { value: x, selected: (inc.schedule || 'biweekly') === x }, x[0].toUpperCase() + x.slice(1)))),
    payday: h('input', { type: 'date', name: 'next-payday', value: inc.next_payday || '' }),
    day1: num(inc.days?.[0] ?? 15, { inputmode: 'numeric', name: 'pay-day-1' }),
    day2: num(inc.days?.[1] ?? 31, { inputmode: 'numeric', name: 'pay-day-2' }),
    buffer: num(plan.buffer, { name: 'buffer', placeholder: 'e.g. 200…' }),
  };
  const days = h('div', { class: 'field-row' }, field('First payday of month', f.day1), field('Second (31 = last day)', f.day2));
  const syncDays = () => { days.hidden = f.schedule.value !== 'semimonthly'; };
  f.schedule.addEventListener('change', syncDays);
  syncDays();

  const bills = repeater(plan.bills, 'Add bill', (b = {}) => {
    const el = {
      name: h('input', { type: 'text', name: 'bill-name', value: b.name || '', autocomplete: 'off', placeholder: 'Rent…' }),
      amount: num(b.amount, { name: 'bill-amount', placeholder: '0' }),
      due: num(b.due_day, { inputmode: 'numeric', name: 'bill-due-day', placeholder: '1' }),
      autopay: h('input', { type: 'checkbox', name: 'bill-autopay', checked: !!b.autopay }),
      varies: h('input', { type: 'checkbox', name: 'bill-varies', checked: b.fixed === false }),
    };
    return {
      node: [field('Name', el.name), h('div', { class: 'field-row' }, field('Amount ($)', el.amount), field('Due day (1–31)', el.due)),
        h('label', { class: 'check' }, el.autopay, 'Paid automatically'),
        h('label', { class: 'check' }, el.varies, 'Amount changes month to month')],
      read: () => ({ ...b, name: el.name.value.trim(), amount: readNum(el.amount), due_day: readNum(el.due),
        autopay: el.autopay.checked, fixed: !el.varies.checked }),
    };
  });

  const targets = repeater(Object.entries(plan.monthly_spend || {}), 'Add target', ([cat, amount] = []) => {
    const el = {
      cat: h('select', { name: 'target-category' }, CATEGORIES.map((c) => h('option', { value: c, selected: c === cat }, categoryLabel(c)))),
      amount: num(amount, { name: 'target-amount', placeholder: '0' }),
    };
    return { node: h('div', { class: 'field-row' }, field('Category', el.cat), field('Per month ($)', el.amount)),
      read: () => [el.cat.value, readNum(el.amount)] };
  });

  const goals = repeater(plan.goals, 'Add goal', (g = {}) => {
    const el = {
      name: h('input', { type: 'text', name: 'goal-name', value: g.name || '', autocomplete: 'off', placeholder: 'Emergency fund…' }),
      target: num(g.target, { name: 'goal-target', placeholder: '0' }),
      by: h('input', { type: 'date', name: 'goal-by', value: g.by || '' }),
    };
    return { node: [field('Name', el.name), h('div', { class: 'field-row' }, field('Target ($)', el.target), field('By (optional)', el.by))],
      read: () => ({ ...g, name: el.name.value.trim(), target: readNum(el.target), by: el.by.value || null }) };
  });

  const errors = h('div', { role: 'alert' });
  const save = h('button', { class: 'btn primary', type: 'submit' }, 'Save plan');
  state.planDirty = false;
  const form = h('form', { novalidate: true, oninput: () => { state.planDirty = true; }, onchange: () => { state.planDirty = true; }, onsubmit: async (e) => {
    e.preventDefault();
    const amount = readNum(f.amount);
    const next = {
      ...plan,
      income: amount == null && !f.payday.value ? null : {
        amount, schedule: f.schedule.value, next_payday: f.payday.value || null,
        ...(f.schedule.value === 'semimonthly' ? { days: [readNum(f.day1), readNum(f.day2)] } : {}),
      },
      buffer: readNum(f.buffer),
      bills: bills.read(),
      monthly_spend: Object.fromEntries(targets.read().filter(([, v]) => v != null)),
      goals: goals.read(),
    };
    save.disabled = true;
    save.textContent = 'Saving…';
    try {
      await api('/api/plan', { method: 'PUT', body: JSON.stringify(next) });
      state.planDirty = false;
      errors.replaceChildren();
      toast('Plan saved.');
    } catch (err) {
      const list = err.body?.errors || [err.message];
      errors.replaceChildren(h('div', { class: 'errors' }, h('b', {}, 'Not saved. Fix these:'), h('ul', {}, list.map((x) => h('li', {}, x)))));
      errors.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } finally {
      save.disabled = false;
      save.textContent = 'Save plan';
    }
  } },
  errors,
  card('Income', h('p', { class: 'sub' }, 'Your take-home pay. Safe-to-spend counts down to the next payday.'),
    h('div', { class: 'field-row' }, field('Each paycheck ($)', f.amount), field('Paid', f.schedule)),
    field('Next payday', f.payday), days),
  card('Bills', h('p', { class: 'sub' }, 'Fixed bills you pay each month. Bills tab also finds recurring charges you can add.'), bills.node),
  card('Buffer', field('Keep this much in checking ($)', f.buffer)),
  card('Monthly spending targets', targets.node),
  card('Goals', goals.node),
  h('div', { class: 'save-bar' }, save));
  return [form];
}

function repeater(items, addLabel, make) {
  const entries = [];
  const list = h('div', {});
  function add(item) {
    const entry = make(item);
    const box = h('div', { class: 'repeat' },
      h('div', { class: 'repeat-head' }, h('span', {}),
        h('button', { class: 'link-btn', type: 'button', onclick: () => { entries.splice(entries.indexOf(entry), 1); box.remove(); list.dispatchEvent(new Event('input', { bubbles: true })); } }, 'Remove')),
      entry.node);
    entries.push(entry);
    list.append(box);
    return box;
  }
  (items || []).forEach(add);
  const addBtn = h('button', { class: 'btn small', type: 'button',
    onclick: () => { add().querySelector('input, select')?.focus(); list.dispatchEvent(new Event('input', { bubbles: true })); } }, addLabel);
  return { node: [list, addBtn], read: () => entries.map((e) => e.read()) };
}

// --- shell: routing, sync, Ask Claude

const SCREENS = { today: screenToday, spending: screenSpending, transactions: screenTransactions, bills: screenBills,
  money: screenMoney, investing: screenInvesting, plan: screenPlan };

// The address is #screen, plus ?query for a screen that takes one (#transactions?period=week&q=coffee).
async function render() {
  const [path, query = ''] = location.hash.slice(1).split('?');
  const name = Object.hasOwn(SCREENS, path) ? path : 'today';
  if (state.screen === 'plan' && name !== 'plan' && state.planDirty) {
    if (!confirm('Leave without saving your plan changes?')) { history.replaceState({ from: state.from }, '', '#plan'); return; }
    state.planDirty = false;
  } else if (name === 'plan' && state.screen === 'plan' && state.planDirty) {
    return;  // a sync or a return to the app must not wipe edits in progress
  }
  const changed = name !== state.screen;
  if (changed && state.screen) state.scroll[state.screen] = main.scrollTop;  // Back returns to the same spot
  // Each history entry keeps the screen it was opened from, so the Back button names where history.back() goes,
  // even after the browser's own Back or Forward.
  if (!history.state) history.replaceState({ from: changed ? state.screen || null : null }, '', location.hash);
  state.from = history.state.from;
  const restore = state.restore;
  state.restore = false;
  state.screen = name;
  const tab = TAB_OF[name] || name;
  document.getElementById('title').textContent = TITLES[name];
  document.title = `${TITLES[name]} · Money`;
  document.querySelectorAll('.tabs a').forEach((a) => {
    if (a.dataset.tab === tab) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  const seq = ++state.seq;
  main.classList.add('loading');  // keep the previous frame, dimmed, until the new data arrives
  try {
    const nodes = await SCREENS[name](new URLSearchParams(query));
    if (seq !== state.seq) return;
    main.replaceChildren(...nodes);
  } catch (e) {
    if (seq !== state.seq) return;
    main.replaceChildren(card(null, h('p', {}, 'Could not load this screen: ' + e.message),
      h('button', { class: 'btn', type: 'button', onclick: render }, 'Try again')));
  } finally {
    if (seq === state.seq) main.classList.remove('loading');
  }
  if (changed) main.scrollTo(0, restore ? state.scroll[name] || 0 : 0);
}

async function syncNow() {
  syncBtn.disabled = true;
  syncBtn.textContent = 'Syncing…';
  try {
    const r = await api('/api/sync', { method: 'POST', body: '{}' });
    if (r.status === 'skipped') toast(`Already synced ${ago(r.last_attempt_at)}.`);
    else if (r.status === 'running') toast('A sync is already running.');
    else toast(r.results.every((x) => x.status === 'ok') ? 'Synced.' : 'Synced, with problems. See Today.');
    await render();
  } catch (e) {
    toast('Sync failed: ' + e.message);
  } finally {
    syncBtn.disabled = false;
    syncBtn.textContent = 'Sync now';
  }
}

// Copies a plain-text snapshot of the finances (plus this screen's detail) for pasting into Claude.
async function askClaude() {
  // The snapshot's screen detail takes a named period; a custom range gets the month's.
  const named = (period) => (period && period !== 'custom' ? period : 'spending');
  const screen = { spending: () => named(state.period), transactions: () => named(state.view?.period), plan: () => '' }[state.screen]?.()
    ?? state.screen;
  const url = '/api/snapshot?screen=' + encodeURIComponent(screen);
  const text = () => fetch(url).then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.text();
  });
  try {
    if (window.ClipboardItem && navigator.clipboard?.write) {
      // Safari needs the clipboard write to start inside the tap, so hand it a promise.
      await navigator.clipboard.write([new ClipboardItem({ 'text/plain': text().then((t) => new Blob([t], { type: 'text/plain' })) })]);
    } else {
      await navigator.clipboard.writeText(await text());
    }
    toast('Snapshot copied. Paste it into Claude and ask.', { href: 'https://claude.ai/new', text: 'Open Claude' });
  } catch (e) {
    toast('Could not copy: ' + e.message);
  }
}

syncBtn.addEventListener('click', syncNow);
document.getElementById('ask').addEventListener('click', askClaude);
window.addEventListener('hashchange', render);
window.addEventListener('beforeunload', (e) => { if (state.screen === 'plan' && state.planDirty) e.preventDefault(); });
document.addEventListener('visibilitychange', () => {
  // Coming back to the app after a while: show fresh numbers. A long break counts as a fresh launch, because a phone
  // can keep this page alive for days. Checking a charge in the bank's app and coming back leaves the card open.
  if (document.hidden) { state.hiddenAt = Date.now(); return; }
  const away = state.hiddenAt ? Date.now() - state.hiddenAt : 0;
  if (away > 30 * 60e3) state.attentionOpen = false;
  if (away > 60e3) render();
});
render();
