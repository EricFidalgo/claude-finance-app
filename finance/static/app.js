'use strict';
// The phone UI. Plain JS, no build step. Every screen reads one API endpoint; amounts arrive as integer cents.
// Names from the bank are untrusted text, so everything goes into the DOM through textContent (see h()).

const main = document.getElementById('main');
const toastEl = document.getElementById('toast');
const syncedEl = document.getElementById('synced');
const syncBtn = document.getElementById('sync');

const TITLES = { today: 'Today', spending: 'Spending', bills: 'Bills', money: 'Money', investing: 'Investing', plan: 'Plan' };
const PERIODS = [['today', 'Today'], ['week', 'This week'], ['last_week', 'Last week'], ['month', 'This month'], ['last_month', 'Last month']];
const PERIOD_INFO = {
  today: { title: 'Spent today', prev: 'yesterday' },
  week: { title: 'Spent this week', prev: 'same days last week', series: ['This week', 'Last week'] },
  last_week: { title: 'Spent last week', prev: 'the week before', series: ['Last week', 'Week before'] },
  month: { title: 'Spent this month', prev: 'same days last month', series: ['This month', 'Last month'] },
  last_month: { title: 'Spent last month', prev: 'the month before', series: ['Last month', 'Month before'] },
};
const CATEGORIES = ['FOOD_AND_DRINK', 'GENERAL_MERCHANDISE', 'TRANSPORTATION', 'ENTERTAINMENT', 'RENT_AND_UTILITIES',
  'PERSONAL_CARE', 'MEDICAL', 'TRAVEL', 'HOME_IMPROVEMENT', 'GENERAL_SERVICES', 'LOAN_PAYMENTS', 'BANK_FEES',
  'GOVERNMENT_AND_NON_PROFIT'];
// Color follows the bucket, never its rank (dataviz: fixed categorical order).
const BUCKETS = [['stock', 'Stocks', 's1'], ['etf', 'ETFs', 's2'], ['fund', 'Funds', 's3'], ['other', 'Other', 's4'], ['cash', 'Cash', 's5']];
const STALE_HOURS = 13;

const state = { screen: null, period: stored('period', 'month'), seq: 0, hiddenAt: 0, planDirty: false };

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
};
function icon(name) {
  return s('svg', { viewBox: '0 0 24 24', class: 'ico', 'aria-hidden': 'true' },
    ICONS[name].map(([tag, a]) => s(tag, a)));
}
// Status is never color alone: an icon in the status color plus a text label in ink.
const LEVEL = { urgent: ['critical', 'Urgent'], warn: ['warning', 'Check'], info: ['info', 'FYI'] };
function statusLine(kind, text) {
  return h('span', { class: `status st-${kind}` }, icon(kind === 'good' ? 'check' : kind === 'serious' ? 'warning' : kind), text);
}

const USD = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
const USD0 = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const minus = (t) => t.replace('-', '−');
const money = (c) => (c == null ? '—' : minus(USD.format(c / 100)));
const money0 = (c) => (c == null ? '—' : minus(USD0.format(Math.round(c / 100))));
const signed = (c) => (c > 0 ? '+' : '') + money(c);
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
  toastEl.replaceChildren(h('span', {}, text), link && h('a', { href: link.href, target: '_blank', rel: 'noopener' }, link.text));
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
      h('p', { class: 'hero-label' }, 'Safe to spend'),
      h('p', { class: 'hero' }, '—'),
      h('p', { class: 'hero-sub' }, `Add your ${sts.missing.join(' and ')} to the plan to see this.`),
      h('p', {}, h('a', { class: 'btn primary', href: '#plan' }, 'Open plan')));
  } else {
    const short = sts.available_cents < 0;
    hero = card(null,
      h('p', { class: 'hero-label' }, 'Safe to spend per day'),
      h('p', { class: 'hero' }, money(short ? 0 : sts.per_day_cents)),
      h('p', { class: 'hero-sub' }, `until payday ${fmtDay(sts.payday)} · ${plural(sts.days, 'day')}`),
      short && h('p', {}, statusLine('critical', `Short ${money(-sts.available_cents)} before payday`)),
      h('details', {},
        h('summary', {}, 'How this is worked out'),
        h('ul', { class: 'calc' },
          sts.lines.map((l) => h('li', {}, h('span', {}, l.label), h('span', {}, signed(l.amount_cents)))),
          h('li', { class: 'total' }, h('span', {}, `Left until payday, ÷ ${plural(sts.days, 'day')}`), h('span', {}, money(sts.available_cents)))),
        sts.bills.length > 0 && h('p', { class: 'sub' }, 'Bills before payday: ' +
          sts.bills.map((b) => `${b.name} ${money(b.amount_cents)} (${fmtShort(b.due)})`).join(', '))));
  }

  const attention = card('Needs attention',
    d.alerts.length === 0
      ? statusLine('good', 'All clear')
      : d.alerts.map((a) => {
        const [kind, label] = LEVEL[a.level] || LEVEL.info;
        return h('div', { class: `alert st-${kind}` }, icon(kind),
          h('div', {}, h('div', { class: 'alert-level' }, label), h('div', { class: 'alert-title' }, a.title),
            h('div', { class: 'alert-detail' }, a.detail)));
      }));

  const spent = card(null, h('ul', { class: 'rows' },
    h('li', { class: 'row' }, h('div', { class: 'row-main' }, h('div', { class: 'row-title' }, 'Spent today')),
      h('div', { class: 'row-amt' }, money(d.spent_today_cents)),
      h('a', { class: 'btn small', href: '#spending', onclick: () => setPeriod('today') }, 'Details'))));

  const next = card('Coming up',
    d.next_bills.length === 0
      ? h('p', { class: 'empty' }, 'No bills in the next 35 days.')
      : h('ul', { class: 'rows' }, d.next_bills.map(billRow)),
    h('p', {}, h('a', { href: '#bills' }, 'All bills')));
  return [hero, attention, spent, next];
}

// --- Spending

function setPeriod(p) { state.period = p; store('period', p); }

async function screenSpending() {
  const p = state.period;
  const d = await api('/api/spending?period=' + p);
  setSynced(d.last_synced_at);
  const info = PERIOD_INFO[p];
  const chips = h('div', { class: 'chips', role: 'group', 'aria-label': 'Period' },
    PERIODS.map(([key, label]) => h('button', {
      class: 'chip', type: 'button', 'aria-pressed': String(key === p),
      onclick: () => { setPeriod(key); render(); },
    }, label)));
  // The row scrolls sideways at phone width; keep the chosen period on screen.
  requestAnimationFrame(() => chips.querySelector('[aria-pressed="true"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' }));

  const change = d.change_cents;
  const hero = card(null,
    h('p', { class: 'hero-label' }, info.title),
    h('p', { class: 'hero' }, money(d.total_cents)),
    h('p', { class: 'hero-sub' }, `${change > 0 ? '↑' : change < 0 ? '↓' : ''} ${money(Math.abs(change))} vs ${money(d.previous.total_cents)} ${info.prev}`.trim()));

  const out = [chips, hero];
  if (info.series) {
    out.push(card('Running total',
      lineChart([
        { label: info.series[0], color: 's1', points: d.trend.current },
        { label: info.series[1], color: 's2', points: d.trend.previous },
      ])));
  }
  const monthly = p === 'month' || p === 'last_month';
  out.push(card('By category', d.by_category.length
    ? categoryBars(d.by_category, monthly ? d.targets_cents : {})
    : h('p', { class: 'empty' }, 'No spending in this period.')));
  if (d.by_merchant.length) {
    out.push(card('Top merchants', h('ul', { class: 'rows' },
      d.by_merchant.slice(0, 8).map((m) => row(m.merchant, plural(m.count, 'charge'), money(m.total_cents))))));
  }
  if (d.unusual.length) {
    out.push(card('Flagged in the last 2 weeks', h('ul', { class: 'rows' },
      d.unusual.map((u) => row(u.merchant, [statusLine('warning', u.detail), fmtShort(u.date)], money(u.amount_cents))))));
  }
  out.push(card('Transactions', transactionList(d.transactions)));
  return out;
}

function transactionList(txns) {
  const list = h('div', {});
  let shown = 60;
  const search = h('input', {
    type: 'search', name: 'q', placeholder: 'Search merchants…', 'aria-label': 'Search transactions', autocomplete: 'off',
    oninput: () => { shown = 60; draw(); },
  });
  function draw() {
    const q = search.value.trim().toLowerCase();
    const rows = q ? txns.filter((t) => `${t.merchant_name || ''} ${t.name || ''}`.toLowerCase().includes(q)) : txns;
    const kids = [];
    let day = null;
    for (const t of rows.slice(0, shown)) {
      if (t.date !== day) { day = t.date; kids.push(h('div', { class: 'day-head' }, fmtDay(day))); }
      const out = t.amount_cents > 0;
      kids.push(h('div', { class: 'row' },
        h('div', { class: 'row-main' },
          h('div', { class: 'row-title' }, t.merchant_name || t.name),
          h('div', { class: 'row-sub' }, `${categoryLabel(t.category)} · …${t.account_mask}`, t.pending && tag('Pending'))),
        h('div', { class: `row-amt${out ? '' : ' amt-in'}` }, out ? money(t.amount_cents) : '+' + money(-t.amount_cents))));
    }
    if (!rows.length) kids.push(h('p', { class: 'empty' }, q ? 'No matches.' : 'No transactions.'));
    if (rows.length > shown) {
      kids.push(h('p', {}, h('button', { class: 'btn small', type: 'button', onclick: () => { shown += 100; draw(); } },
        `Show more (${rows.length - shown} left)`)));
    }
    list.replaceChildren(...kids);
  }
  draw();
  return [search, list];
}

function categoryBars(cats, targets) {
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
    return h('li', { class: 'bar-row' },
      h('div', { class: 'bar-head' },
        h('span', { class: 'row-title' }, c.label),
        h('span', {}, h('b', {}, money(c.total_cents)), target != null && h('span', { class: 'sub' }, ` of ${money0(target)}`))),
      track,
      over && statusLine('warning', `${money0(c.total_cents - target)} over target`));
  }));
}

// Cumulative line chart: one hairline crosshair snaps to the nearest day; the tooltip lists every series.
function lineChart(series) {
  const W = Math.max(260, main.clientWidth - 64), H = 170, padR = 40, padT = 8, padB = 22;
  const n = Math.max(...series.map((x) => x.points.length));
  const plotW = W - padR, plotH = H - padT - padB;
  const maxV = Math.max(...series.flatMap((x) => x.points.map((p) => p.total_cents / 100)), 1);
  const raw = maxV / 3, pow = 10 ** Math.floor(Math.log10(raw)), m = raw / pow;
  const step = (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * pow;
  const top = Math.ceil(maxV / step) * step;
  const x = (i) => (n === 1 ? plotW / 2 : (i * plotW) / (n - 1));
  const y = (v) => padT + plotH - (v / top) * plotH;

  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', tabindex: '0',
    'aria-label': series.map((x) => `${x.label}: ${money(x.points.at(-1)?.total_cents ?? 0)}`).join('; ') + '. Use arrow keys for each day.' });
  for (let v = 0; v <= top + 1e-9; v += step) {
    svg.append(s('line', { class: v === 0 ? 'baseline' : 'gridline', x1: 0, x2: plotW, y1: y(v), y2: y(v) }),
      s('text', { x: W, y: y(v) + 4, 'text-anchor': 'end' }, short$(v)));
  }
  const dates = series[0].points;
  if (dates.length) {
    svg.append(s('text', { x: 0, y: H - 4 }, fmtShort(dates[0].date)),
      s('text', { x: x(dates.length - 1), y: H - 4, 'text-anchor': dates.length - 1 > n * 0.8 ? 'end' : 'middle' }, fmtShort(dates.at(-1).date)));
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
      rows.push(h('div', {}, key, h('b', {}, money(p.total_cents)), h('span', { class: 'tip-date' }, `${ser.label} · ${fmtShort(p.date)}`)));
    });
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
  hit.addEventListener('pointerdown', fromPointer);
  hit.addEventListener('pointermove', fromPointer);
  svg.addEventListener('pointerleave', hide);
  svg.addEventListener('blur', hide);
  svg.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault();
      show(idx < 0 ? n - 1 : idx + (e.key === 'ArrowRight' ? 1 : -1));
    } else if (e.key === 'Escape') hide();
  });

  const legend = h('div', { class: 'legend' }, series.map((ser) => {
    const key = h('span', { class: 'key-line' }); key.style.background = `var(--${ser.color})`;
    return h('span', {}, key, ser.label, h('b', {}, money(ser.points.at(-1)?.total_cents ?? 0)));
  }));
  const table = h('details', { class: 'table-view' }, h('summary', {}, 'Show as table'),
    h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, 'Day'), series.map((ser) => h('th', {}, ser.label)))),
      h('tbody', {}, Array.from({ length: n }, (_, i) => h('tr', {}, h('td', {}, i + 1),
        series.map((ser) => h('td', {}, ser.points[i] ? money0(ser.points[i].total_cents) : '')))))));
  return [legend, wrap, table];
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

  const out = [hero, timeline, recurring];
  if (d.price_flags.length) {
    out.push(card('Price changes', h('ul', { class: 'rows' },
      d.price_flags.map((u) => row(u.merchant, [statusLine('warning', u.detail), fmtShort(u.date)], money(u.amount_cents))))));
  }
  return out;
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
    const title = `${a.name} …${a.mask}`;
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
        c && `re-link by ${fmtShort(c.expires)}${c.estimated ? ' (estimate)' : ''}`],
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
    return row(x.ticker || x.name, `${x.name}${x.quantity ? ` · ${+x.quantity.toFixed(4)} sh` : ''}`,
      money0(x.value_cents), gain != null ? `${signed(gain).replace(/\.\d\d$/, '')}${pct}` : null);
  })));
  return [hero, allocation, left, holdings];
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
  const plan = await api('/api/plan');
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

const SCREENS = { today: screenToday, spending: screenSpending, bills: screenBills, money: screenMoney, investing: screenInvesting, plan: screenPlan };

async function render() {
  const name = SCREENS[location.hash.slice(1)] ? location.hash.slice(1) : 'today';
  if (state.screen === 'plan' && name !== 'plan' && state.planDirty) {
    if (!confirm('Leave without saving your plan changes?')) { history.replaceState(null, '', '#plan'); return; }
    state.planDirty = false;
  } else if (name === 'plan' && state.screen === 'plan' && state.planDirty) {
    return;  // a sync or a return to the app must not wipe edits in progress
  }
  const changed = name !== state.screen;
  state.screen = name;
  document.getElementById('title').textContent = TITLES[name];
  document.title = `${TITLES[name]} · Money`;
  document.querySelectorAll('.tabs a').forEach((a) => {
    if (a.dataset.tab === name) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  const seq = ++state.seq;
  main.classList.add('loading');  // keep the previous frame, dimmed, until the new data arrives
  try {
    const nodes = await SCREENS[name]();
    if (seq !== state.seq) return;
    main.replaceChildren(...nodes);
  } catch (e) {
    if (seq !== state.seq) return;
    main.replaceChildren(card(null, h('p', {}, 'Could not load this screen: ' + e.message),
      h('button', { class: 'btn', type: 'button', onclick: render }, 'Try again')));
  } finally {
    if (seq === state.seq) main.classList.remove('loading');
  }
  if (changed) window.scrollTo(0, 0);
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
  const screen = state.screen === 'spending' ? state.period : state.screen === 'plan' ? '' : state.screen;
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
  // Coming back to the app after a while: show fresh numbers.
  if (document.hidden) state.hiddenAt = Date.now();
  else if (state.hiddenAt && Date.now() - state.hiddenAt > 60e3) render();
});
render();
