/* Worklog web view: list, overview and token management for the signed-in user. No dependencies.
 * All entry text is inserted with textContent, never as HTML. */
'use strict';

// ---------------------------------------------------------------- i18n

const TEXT = {
  en: {
    loginHint: 'You are not signed in through the single sign-on. Alternatively, sign in with a token: with the read scope to read, with read and write also to edit and import.', token: 'Token', signIn: 'Sign in', signOut: 'Sign out',
    badToken: 'This token is not valid.', noRead: 'This token has no read scope.', loginFailed: 'Sign-in failed.',
    entries: 'Entries', overview: 'Overview', search: 'Search …', project: 'Project', agent: 'Agent', source: 'Source',
    period: 'Period', from: 'From', to: 'To', reset: 'Reset filters',
    allProjects: 'All projects', allAgents: 'All agents', allSources: 'All sources',
    rangeAll: 'All time', rangeToday: 'Today', range7: 'Last 7 days', range30: 'Last 30 days',
    rangeMonth: 'This month', rangeYear: 'This year', rangeCustom: 'Custom …',
    nEntries: '{0} entries', oneEntry: '1 entry', shown: 'showing {0}', loading: 'Loading …',
    more: 'Show {0} more', none: 'No entries match these filters.', error: 'Could not load: {0}',
    perProject: 'By project', time: 'Time', asTable: 'Show as table', showAll: 'Show all {0} projects', showLess: 'Show fewer',
    perDay: 'Per day', perWeek: 'Per week', perMonth: 'Per month', weekOf: 'Week of {0}',
    tEntries: 'Entries', tTime: 'Logged time', tProjects: 'Projects', tDays: 'Active days', bucket: 'Period',
    tokens: 'Tokens', newToken: 'New token', newTokenHint: 'The name becomes the agent of every entry written with this token.',
    tokenName: 'Name', scopes: 'Rights', scopeRead: 'read', scopeWrite: 'write', create: 'Create',
    tokenCreated: 'Token created', tokenOnce: 'Copy it now. It is shown only this once and cannot be retrieved later.',
    copy: 'Copy', copied: 'Copied', done: 'Done, hide token', revoke: 'Revoke',
    revokeConfirm: 'Revoke the token "{0}"? Agents using it lose access immediately. The name cannot be reused.',
    colName: 'Name', colScopes: 'Rights', colCreated: 'Created', colUsed: 'Last used', colState: 'Status',
    active: 'active', revokedAt: 'revoked {0}', never: 'never', pickScope: 'Choose at least one right.',
    dupesFound: '{0} possible duplicates: the same work seems to be logged more than once.',
    oneDupe: '1 possible duplicate: the same work seems to be logged more than once.',
    showDupes: 'Review', hideDupes: 'Hide', similar: 'similar ({0} %)', keepThis: 'Keep the selected one and merge',
    notDupe: 'Not a duplicate', mergeHelp: 'Details and tags are combined, the longest duration counts once, the others are deleted.',
    backfilled: 'logged afterwards on {0}', importJson: 'Import',
    imported: 'Import: {0} new, {1} already there, {2} errors.', importFailed: 'Import failed: {0}',
    edit: 'Edit', del: 'Delete', save: 'Save', cancel: 'Cancel', summaryLbl: 'Summary', detailsLbl: 'Details',
    tagsLbl: 'Tags (comma separated)', minutesLbl: 'Minutes', deleteConfirm: 'Delete this entry? This cannot be undone.',
    renameProject: 'Rename project …', renamePrompt: 'New name for the project "{0}":',
    mergeConfirm: 'The project "{0}" already exists. Move the entries of "{2}" ({1}) into it?', export: 'Export:',
    nameTaken: 'This name is already taken (also by revoked tokens).', badName: 'Name: 1–64 characters, letters, digits, - _ or .',
  },
  de: {
    loginHint: 'Du bist nicht über das Single Sign-on angemeldet. Alternativ geht die Anmeldung mit einem Token: mit dem Recht „read“ zum Lesen, mit „read“ und „write“ auch zum Bearbeiten und Importieren.', token: 'Token', signIn: 'Anmelden', signOut: 'Abmelden',
    badToken: 'Dieses Token ist nicht gültig.', noRead: 'Dieses Token hat kein Leserecht.', loginFailed: 'Anmeldung fehlgeschlagen.',
    entries: 'Einträge', overview: 'Übersicht', search: 'Suchen …', project: 'Projekt', agent: 'Agent', source: 'Quelle',
    period: 'Zeitraum', from: 'Von', to: 'Bis', reset: 'Filter zurücksetzen',
    allProjects: 'Alle Projekte', allAgents: 'Alle Agents', allSources: 'Alle Quellen',
    rangeAll: 'Gesamter Zeitraum', rangeToday: 'Heute', range7: 'Letzte 7 Tage', range30: 'Letzte 30 Tage',
    rangeMonth: 'Dieser Monat', rangeYear: 'Dieses Jahr', rangeCustom: 'Benutzerdefiniert …',
    nEntries: '{0} Einträge', oneEntry: '1 Eintrag', shown: '{0} angezeigt', loading: 'Lade …',
    more: '{0} weitere anzeigen', none: 'Keine Einträge für diese Filter.', error: 'Laden fehlgeschlagen: {0}',
    perProject: 'Nach Projekt', time: 'Zeit', asTable: 'Als Tabelle anzeigen', showAll: 'Alle {0} Projekte anzeigen', showLess: 'Weniger anzeigen',
    perDay: 'Pro Tag', perWeek: 'Pro Woche', perMonth: 'Pro Monat', weekOf: 'Woche ab {0}',
    tEntries: 'Einträge', tTime: 'Erfasste Zeit', tProjects: 'Projekte', tDays: 'Aktive Tage', bucket: 'Zeitraum',
    tokens: 'Tokens', newToken: 'Neues Token', newTokenHint: 'Der Name wird zum Agent jedes Eintrags, der mit diesem Token geschrieben wird.',
    tokenName: 'Name', scopes: 'Rechte', scopeRead: 'lesen', scopeWrite: 'schreiben', create: 'Anlegen',
    tokenCreated: 'Token angelegt', tokenOnce: 'Kopiere es jetzt. Es wird nur dieses eine Mal angezeigt und lässt sich später nicht mehr abrufen.',
    copy: 'Kopieren', copied: 'Kopiert', done: 'Fertig, Token ausblenden', revoke: 'Widerrufen',
    revokeConfirm: 'Token „{0}“ widerrufen? Agents, die es nutzen, verlieren sofort den Zugriff. Der Name ist danach nicht wieder verwendbar.',
    colName: 'Name', colScopes: 'Rechte', colCreated: 'Angelegt', colUsed: 'Zuletzt benutzt', colState: 'Status',
    active: 'aktiv', revokedAt: 'widerrufen {0}', never: 'nie', pickScope: 'Wähle mindestens ein Recht.',
    dupesFound: '{0} mögliche Duplikate: Dieselbe Arbeit scheint mehrfach eingetragen zu sein.',
    oneDupe: '1 mögliches Duplikat: Dieselbe Arbeit scheint mehrfach eingetragen zu sein.',
    showDupes: 'Ansehen', hideDupes: 'Ausblenden', similar: 'ähnlich ({0} %)',
    keepThis: 'Ausgewählten behalten und zusammenführen', notDupe: 'Kein Duplikat',
    mergeHelp: 'Details und Tags werden zusammengelegt, die längste Dauer zählt einmal, die anderen werden gelöscht.',
    backfilled: 'nachgetragen am {0}', importJson: 'Import',
    imported: 'Import: {0} neu, {1} schon vorhanden, {2} Fehler.', importFailed: 'Import fehlgeschlagen: {0}',
    edit: 'Bearbeiten', del: 'Löschen', save: 'Speichern', cancel: 'Abbrechen', summaryLbl: 'Zusammenfassung',
    detailsLbl: 'Details', tagsLbl: 'Tags (durch Komma getrennt)', minutesLbl: 'Minuten',
    deleteConfirm: 'Diesen Eintrag löschen? Das lässt sich nicht rückgängig machen.',
    renameProject: 'Projekt umbenennen …', renamePrompt: 'Neuer Name für das Projekt „{0}“:',
    mergeConfirm: 'Das Projekt „{0}“ gibt es schon. Die Einträge von „{2}“ ({1}) dorthin verschieben?', export: 'Export:',
    nameTaken: 'Dieser Name ist schon vergeben (auch widerrufene Tokens zählen).', badName: 'Name: 1–64 Zeichen, Buchstaben, Ziffern, - _ oder .',
  },
};
const lang = (navigator.language || 'en').toLowerCase().startsWith('de') ? 'de' : 'en';
function t(key, ...args) {
  let s = TEXT[lang][key] ?? TEXT.en[key] ?? key;
  args.forEach((a, i) => { s = s.replace('{' + i + '}', a); });
  return s;
}

// ---------------------------------------------------------------- helpers

const $ = (id) => document.getElementById(id);
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
const SVG = 'http://www.w3.org/2000/svg';
function svg(tag, attrs) {
  const e = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  return e;
}
const num = (n) => Number(n).toLocaleString(lang);
const pad = (n) => String(n).padStart(2, '0');
const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
function parseDate(s) { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); }
function addDays(d, n) { const x = new Date(d); x.setDate(x.getDate() + n); return x; }
function fmtDur(min) {
  if (!min) return '';
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60), m = min % 60;
  return m ? `${num(h)} h ${m} min` : `${num(h)} h`;
}
const fmtHours = (min) => `${(min / 60).toLocaleString(lang, { maximumFractionDigits: 1 })} h`;
const dayFmt = new Intl.DateTimeFormat(lang, { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' });
const shortFmt = new Intl.DateTimeFormat(lang, { day: '2-digit', month: '2-digit' });
const monthFmt = new Intl.DateTimeFormat(lang, { month: 'short', year: '2-digit' });
const monthLongFmt = new Intl.DateTimeFormat(lang, { month: 'long', year: 'numeric' });

// ---------------------------------------------------------------- state

const DEFAULTS = { tab: 'entries', q: '', project: '', agent: '', source: '', range: 'all', since: '', until: '', metric: 'time' };
const RANGES = ['all', 'today', '7d', '30d', 'month', 'year', 'custom'];
const PAGE = 50;
const state = { ...DEFAULTS };
let shown = [];          // entries currently listed
let total = 0;
let seq = 0;             // discards answers of outdated requests
let overview = null;     // last stats answer
let allProjects = false;
let me = null;           // { user, agent, via, manage_tokens, can_edit, logout_url }
let facets = null;       // last /api/facets answer
let dupes = [];          // groups of likely duplicates
let dupesOpen = false;
const TABS = () => (me && me.manage_tokens ? ['entries', 'overview', 'tokens'] : ['entries', 'overview']);

function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  for (const k of Object.keys(DEFAULTS)) state[k] = p.get(k) ?? DEFAULTS[k];
  if (!TABS().includes(state.tab)) state.tab = 'entries';
  if (!RANGES.includes(state.range)) state.range = 'all';
  if (!['time', 'entries'].includes(state.metric)) state.metric = 'time';
}
function writeHash() {
  const p = new URLSearchParams();
  for (const k of Object.keys(DEFAULTS)) if (state[k] !== DEFAULTS[k]) p.set(k, state[k]);
  const h = p.toString();
  history.replaceState(null, '', h ? '#' + h : location.pathname + location.search);
}
function rangeDates() {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const tomorrow = iso(addDays(today, 1));
  switch (state.range) {
    case 'today': return [iso(today), tomorrow];
    case '7d': return [iso(addDays(today, -6)), tomorrow];
    case '30d': return [iso(addDays(today, -29)), tomorrow];
    case 'month': return [iso(new Date(today.getFullYear(), today.getMonth(), 1)), tomorrow];
    case 'year': return [iso(new Date(today.getFullYear(), 0, 1)), tomorrow];
    case 'custom': return [state.since, state.until ? iso(addDays(parseDate(state.until), 1)) : ''];  // "to" is inclusive
    default: return ['', ''];
  }
}
function filterParams() {
  const [since, until] = rangeDates();
  return { q: state.q.trim(), project: state.project, agent: state.agent, source: state.source, since, until };
}

// ---------------------------------------------------------------- api

async function api(path, params) {
  const url = new URL(path, location.href);
  for (const [k, v] of Object.entries(params || {})) if (v !== '' && v != null) url.searchParams.set(k, v);
  const r = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } }).catch(proxyExpired);
  if (r.status === 401) { showLogin(); throw new Error('401'); }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
  return r.json();
}
// Behind a sign-in proxy an expired login answers with a redirect to another
// origin, which fetch reports as a network error: reload to sign in again.
function proxyExpired(err) {
  if (me && me.via === 'proxy' && navigator.onLine) location.reload();
  throw err;
}
async function apiPost(path, body) {
  const r = await fetch(new URL(path, location.href), {
    method: 'POST', credentials: 'same-origin',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
  }).catch(proxyExpired);
  if (r.status === 401) { showLogin(); throw new Error('401'); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

// ---------------------------------------------------------------- login

function showLogin() {
  $('app').hidden = true;
  $('login').hidden = false;
  $('login-token').focus();
}
async function onLogin(ev) {
  ev.preventDefault();
  $('login-error').textContent = '';
  const r = await fetch('login', {
    method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token: $('login-token').value.trim() }),
  }).catch(() => null);
  if (r && r.ok) {
    $('login-token').value = '';
    $('login').hidden = true;
    loadSession();
  } else {
    $('login-error').textContent = t(r && r.status === 401 ? 'badToken' : r && r.status === 403 ? 'noRead' : 'loginFailed');
  }
}
async function onLogout() {
  await fetch('logout', { method: 'POST', credentials: 'same-origin' }).catch(() => null);
  me = null;
  hideNewToken();
  showLogin();
}
function loadSession() {
  fetch('session', { credentials: 'same-origin' })
    .then((r) => (r.ok ? r.json() : null))
    .then((s) => (s ? start(s) : showLogin()))
    .catch(showLogin);
}

// ---------------------------------------------------------------- filters

function fillSelect(id, allLabel, items, value) {
  const s = $(id);
  s.textContent = '';
  s.append(new Option(allLabel, ''));
  for (const i of items) s.append(new Option(`${i.name} (${num(i.entries)})`, i.name));
  if (value && !items.some((i) => i.name === value)) s.append(new Option(value, value));
  s.value = value;
}
function syncControls() {
  $('f-q').value = state.q;
  $('f-project').value = state.project;
  $('f-agent').value = state.agent;
  $('f-source').value = state.source;
  $('f-range').value = state.range;
  $('f-since').value = state.since;
  $('f-until').value = state.until;
  $('f-custom').hidden = state.range !== 'custom';
  const filtered = ['q', 'project', 'agent', 'source', 'range'].some((k) => state[k] !== DEFAULTS[k]);
  $('f-reset').hidden = !filtered;
  for (const b of document.querySelectorAll('[data-tab]')) b.setAttribute('aria-selected', String(b.dataset.tab === state.tab));
  for (const b of document.querySelectorAll('[data-metric]')) b.setAttribute('aria-pressed', String(b.dataset.metric === state.metric));
  $('view-entries').hidden = state.tab !== 'entries';
  $('view-overview').hidden = state.tab !== 'overview';
  $('view-tokens').hidden = state.tab !== 'tokens';
  $('filters').hidden = state.tab === 'tokens';
  $('f-rename').hidden = !(me && me.can_edit && state.project && state.tab !== 'tokens');
  $('export').hidden = state.tab === 'tokens';
  const q = new URLSearchParams(Object.entries(filterParams()).filter(([, v]) => v !== '' && v != null));
  $('export-md').href = `api/export?${q}&format=md`;
  $('export-csv').href = `api/export?${q}&format=csv`;
  $('export-json').href = `api/export?${q}&format=json`;
  $('import-btn').hidden = !(me && me.can_edit);
  if (state.tab !== 'tokens') hideNewToken();   // a new token never lingers on other tabs
}
function changed() {
  writeHash();
  syncControls();
  refresh();
}
function setFilter(key, value) {
  state[key] = value;
  if (key === 'project' && !$('f-project').querySelector(`option[value="${CSS.escape(value)}"]`)) {
    $('f-project').append(new Option(value, value));
  }
  changed();
}

// ---------------------------------------------------------------- entries

function refresh() {
  if (state.tab === 'entries') loadEntries(true);
  else if (state.tab === 'overview') loadOverview();
  else loadTokens();
}
async function loadEntries(reset) {
  const my = ++seq;
  if (reset) { shown = []; total = 0; }
  $('status').textContent = t('loading');
  try {
    const data = await api('api/entries', { ...filterParams(), limit: PAGE, offset: shown.length });
    if (my !== seq) return;
    shown = shown.concat(data.entries);
    total = data.total;
    renderEntries();
  } catch (e) {
    if (my === seq && e.message !== '401') $('status').textContent = t('error', e.message);
  }
}
function renderEntries() {
  const box = $('entries');
  box.textContent = '';
  const count = total === 1 ? t('oneEntry') : t('nEntries', num(total));
  $('status').textContent = shown.length < total ? `${count} · ${t('shown', num(shown.length))}` : count;
  if (!shown.length) box.append(el('p', 'empty', t('none')));
  let day = null, list = null;
  for (const e of shown) {
    const d = e.ts.slice(0, 10);
    if (d !== day) {
      day = d;
      const minutes = shown.filter((x) => x.ts.slice(0, 10) === d).reduce((s, x) => s + (x.duration_min || 0), 0);
      const sec = el('section', 'day');
      const head = el('div', 'day-head');
      head.append(el('span', null, dayFmt.format(parseDate(d))), el('span', null, fmtDur(minutes)));
      sec.append(head);
      list = sec;
      box.append(sec);
    }
    list.append(entryRow(e));
  }
  const rest = total - shown.length;
  $('more').hidden = rest <= 0;
  $('more').textContent = t('more', num(Math.min(PAGE, rest)));
}
function entryRow(e) {
  const d = el('details', 'entry');
  const s = el('summary');
  const chip = el('button', 'chip', e.project);
  chip.type = 'button';
  chip.title = e.project;
  chip.addEventListener('click', (ev) => { ev.preventDefault(); ev.stopPropagation(); setFilter('project', e.project); });
  const proj = el('span', 'e-project');
  proj.append(chip);
  s.append(el('span', 'e-time', e.ts.slice(11, 16)), proj, el('span', 'e-summary', e.summary), el('span', 'e-dur', fmtDur(e.duration_min)));
  const body = el('div', 'e-body');
  if (e.details) body.append(el('pre', 'e-details', e.details));
  if (e.tags && e.tags.length) {
    const tags = el('div', 'e-tags');
    for (const tag of e.tags) tags.append(el('span', 'chip', tag));
    body.append(tags);
  }
  const meta = el('div', 'e-actions small');
  // logged much later than the work was done (backfill, import): say so
  const late = e.created_at && new Date(e.created_at) - new Date(e.ts) > 6 * 3600e3;
  const when = late ? ` · ${t('backfilled', dayFmt.format(new Date(e.created_at)))}` : '';
  meta.append(el('span', 'muted', `${e.agent} · ${e.source} · #${e.id}${when}`));
  if (me && me.can_edit) {
    const edit = el('button', 'link', t('edit'));
    edit.type = 'button';
    edit.addEventListener('click', () => body.replaceWith(entryForm(e, d)));
    const del = el('button', 'danger', t('del'));
    del.type = 'button';
    del.addEventListener('click', () => deleteEntry(e));
    meta.append(edit, del);
  }
  body.append(meta);
  d.append(s, body);
  return d;
}

// ---------------------------------------------------------------- JSON import (proxy sign-in)

async function importFile() {
  const file = $('import-file').files[0];
  $('import-file').value = '';
  if (!file) return;
  try {
    const r = await fetch(new URL('api/import', location.href), {
      method: 'POST', credentials: 'same-origin',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: await file.text(),
    }).catch(proxyExpired);
    if (r.status === 401) { showLogin(); return; }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || r.statusText);
    const errors = data.errors.length ? '\n' + data.errors.slice(0, 5).map((e) => `#${e.index}: ${e.error}`).join('\n') : '';
    alert(t('imported', num(data.created), num(data.skipped + data.updated), num(data.errors.length)) + errors);
    await loadFacets();
    refresh();
    loadDupes();
  } catch (err) {
    alert(t('importFailed', err.message));
  }
}

// ---------------------------------------------------------------- duplicates

async function loadDupes() {
  try {
    dupes = (await api('api/duplicates')).groups;
  } catch (e) {
    dupes = [];
  }
  renderDupes();
}
function renderDupes() {
  $('dupes-hint').hidden = !dupes.length;
  if (!dupes.length) dupesOpen = false;
  $('dupes-text').textContent = dupes.length === 1 ? t('oneDupe') : t('dupesFound', num(dupes.length));
  $('dupes-toggle').textContent = t(dupesOpen ? 'hideDupes' : 'showDupes');
  const box = $('dupes');
  box.hidden = !dupesOpen;
  box.textContent = '';
  if (!dupesOpen) return;
  dupes.forEach((g, gi) => {
    const card = el('div', 'dupe');
    const head = el('div', 'dupe-head');
    head.append(el('span', null, g.entries[0].project), el('span', null, t('similar', Math.round(g.score * 100))));
    card.append(head);
    // default: keep the entry with the most details
    const best = g.entries.reduce((a, b) => ((b.details || '').length > (a.details || '').length ? b : a));
    for (const e of g.entries) {
      const label = el('label');
      const radio = el('input');
      radio.type = 'radio';
      radio.name = `keep-${gi}`;
      radio.value = e.id;
      radio.checked = e.id === best.id;
      radio.disabled = !(me && me.can_edit);
      label.append(radio, el('span', null, e.summary),
        el('span', 'meta', `${e.ts.slice(0, 10)} ${e.ts.slice(11, 16)} · ${e.agent} · ${fmtDur(e.duration_min) || '–'} · #${e.id}`));
      if (e.details) label.append(el('span', 'snippet', e.details));
      card.append(label);
    }
    if (me && me.can_edit) {
      const buttons = el('div', 'buttons');
      const merge = el('button', 'primary', t('keepThis'));
      merge.type = 'button';
      merge.addEventListener('click', async () => {
        const keep = Number(card.querySelector('input:checked').value);
        await dupeAction('api/duplicates/merge', { keep, remove: g.entries.map((e) => e.id).filter((id) => id !== keep) });
      });
      const ignore = el('button', 'link', t('notDupe'));
      ignore.type = 'button';
      ignore.addEventListener('click', () => dupeAction('api/duplicates/ignore', { ids: g.entries.map((e) => e.id) }));
      buttons.append(merge, ignore);
      card.append(buttons, el('p', 'muted small', t('mergeHelp')));
    }
    box.append(card);
  });
}
async function dupeAction(path, body) {
  try {
    await apiPost(path, body);
  } catch (err) {
    if (err.message !== '401') alert(t('error', err.message));
    return;
  }
  await loadDupes();
  loadFacets();
  refresh();
}

// ---------------------------------------------------------------- changes (proxy sign-in)

function field(label, input, wide) {
  const l = el('label', 'field' + (wide ? ' wide' : ''));
  l.append(el('span', null, label), input);
  return l;
}
function entryForm(e, details) {
  const form = el('form', 'e-body e-form');
  const project = el('input'); project.value = e.project; project.required = true; project.setAttribute('list', 'project-names');
  const summary = el('input'); summary.value = e.summary; summary.required = true; summary.maxLength = 300;
  const text = el('textarea'); text.value = e.details || '';
  const tags = el('input'); tags.value = (e.tags || []).join(', ');
  const minutes = el('input'); minutes.type = 'number'; minutes.min = '0'; minutes.value = e.duration_min ?? '';
  const error = el('p', 'error');
  const save = el('button', 'primary', t('save')); save.type = 'submit';
  const cancel = el('button', 'link', t('cancel')); cancel.type = 'button';
  cancel.addEventListener('click', () => details.replaceWith(entryRow(e)));
  const buttons = el('div', 'buttons');
  buttons.append(save, cancel);
  form.append(field(t('project'), project), field(t('minutesLbl'), minutes), field(t('summaryLbl'), summary, true),
              field(t('detailsLbl'), text, true), field(t('tagsLbl'), tags, true), buttons, error);
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    error.textContent = '';
    const changes = {
      project: project.value.trim(), summary: summary.value.trim(), details: text.value,
      tags: tags.value.split(',').map((x) => x.trim()).filter(Boolean),
      duration_min: minutes.value === '' ? null : Number(minutes.value),
    };
    try {
      const updated = await apiPost(`api/entries/${e.id}`, changes);
      shown = shown.map((x) => (x.id === e.id ? updated : x));
      const row = entryRow(updated);
      row.open = true;
      details.replaceWith(row);
      loadFacets();
      loadDupes();
    } catch (err) {
      if (err.message !== '401') error.textContent = err.message;
    }
  });
  setTimeout(() => summary.focus(), 0);
  return form;
}
async function deleteEntry(e) {
  if (!confirm(t('deleteConfirm'))) return;
  try {
    await apiPost(`api/entries/${e.id}/delete`);
    shown = shown.filter((x) => x.id !== e.id);
    total -= 1;
    renderEntries();
    loadFacets();
    loadDupes();
  } catch (err) {
    if (err.message !== '401') alert(t('error', err.message));
  }
}
async function renameProject() {
  const from = state.project;
  const to = (prompt(t('renamePrompt', from), from) || '').trim();
  if (!to || to === from) return;
  const target = facets && facets.projects.find((p) => p.name === to);
  const source = facets && facets.projects.find((p) => p.name === from);
  if (target && !confirm(t('mergeConfirm', to, num(source ? source.entries : 0), from))) return;
  try {
    await apiPost('api/projects/rename', { from, to });
    state.project = to;          // before loading the facets, so the old name is not kept as an extra option
    await loadFacets();
    changed();
  } catch (err) {
    if (err.message !== '401') alert(t('error', err.message));
  }
}

// ---------------------------------------------------------------- overview

async function loadOverview() {
  const my = ++seq;
  $('status').textContent = t('loading');
  try {
    const data = await api('api/stats', filterParams());
    if (my !== seq) return;
    overview = data;
    renderOverview();
  } catch (e) {
    if (my === seq && e.message !== '401') $('status').textContent = t('error', e.message);
  }
}
function renderOverview() {
  if (!overview) return;
  const tt = overview.totals;
  $('status').textContent = tt.entries === 1 ? t('oneEntry') : t('nEntries', num(tt.entries));
  const tiles = $('tiles');
  tiles.textContent = '';
  for (const [label, value] of [[t('tEntries'), num(tt.entries)], [t('tTime'), fmtHours(tt.duration_min)],
                                [t('tProjects'), num(tt.projects)], [t('tDays'), num(tt.days)]]) {
    const tile = el('div', 'tile');
    tile.append(el('b', null, value), el('span', null, label));
    tiles.append(tile);
  }
  renderProjects();
  renderTimeline();
}
function renderProjects() {
  const box = $('projects');
  box.textContent = '';
  const byTime = state.metric === 'time';
  const val = (p) => (byTime ? p.duration_min : p.entries);
  const rows = [...overview.projects].sort((a, b) => val(b) - val(a) || a.project.localeCompare(b.project));
  const max = Math.max(1, ...rows.map(val));
  const LIMIT = 12;
  for (const p of allProjects ? rows : rows.slice(0, LIMIT)) {
    const row = el('div', 'bar-row');
    row.title = `${p.project}\n${fmtDur(p.duration_min) || '0 min'} · ${p.entries === 1 ? t('oneEntry') : t('nEntries', num(p.entries))}`;
    const name = el('button', 'bar-name', p.project);
    name.type = 'button';
    name.addEventListener('click', () => { state.tab = 'entries'; setFilter('project', p.project); });
    const track = el('span', 'bar-track');
    const fill = el('span', 'bar-fill');
    fill.style.width = `${(val(p) / max) * 100}%`;
    if (!val(p)) fill.style.visibility = 'hidden';
    track.append(fill);
    row.append(name, track, el('span', 'bar-value', byTime ? (fmtDur(p.duration_min) || '–') : num(p.entries)));
    box.append(row);
  }
  if (rows.length > LIMIT) {
    const more = el('button', 'link bars-more', allProjects ? t('showLess') : t('showAll', num(rows.length)));
    more.type = 'button';
    more.addEventListener('click', () => { allProjects = !allProjects; renderProjects(); });
    box.append(more);
  }
  if (!rows.length) box.append(el('p', 'empty', t('none')));
}

function buckets() {
  const days = overview.days;
  if (!days.length) return { unit: 'day', items: [] };
  const [since, until] = rangeDates();
  const first = parseDate(days[0].date), last = parseDate(days[days.length - 1].date);
  let start = since ? parseDate(since) : first;
  let end = until ? addDays(parseDate(until), -1) : last;
  if (start > first) start = first;   // server and browser may disagree on "today" by a time zone
  if (end < last) end = last;
  const span = Math.round((end - start) / 864e5) + 1;
  const unit = span <= 45 ? 'day' : span <= 420 ? 'week' : 'month';
  const align = (d) => {
    if (unit === 'week') return addDays(d, -((d.getDay() + 6) % 7));      // Monday
    if (unit === 'month') return new Date(d.getFullYear(), d.getMonth(), 1);
    return d;
  };
  const next = (d) => (unit === 'month' ? new Date(d.getFullYear(), d.getMonth() + 1, 1) : addDays(d, unit === 'week' ? 7 : 1));
  const map = new Map();
  for (let d = align(start); d <= end; d = next(d)) map.set(iso(d), { start: d, entries: 0, minutes: 0 });
  for (const x of days) {
    const b = map.get(iso(align(parseDate(x.date))));
    if (b) { b.entries += x.entries; b.minutes += x.duration_min; }
  }
  return { unit, items: [...map.values()] };
}
function bucketLabel(b, unit, long) {
  if (unit === 'month') return (long ? monthLongFmt : monthFmt).format(b.start);
  if (unit === 'week') return long ? t('weekOf', dayFmt.format(b.start)) : shortFmt.format(b.start);
  return long ? dayFmt.format(b.start) : shortFmt.format(b.start);
}
function niceScale(maxVal, whole) {
  if (maxVal <= 0) return { max: 1, step: 1 };
  const raw = maxVal / 3, p = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / p;
  let step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  if (whole) step = Math.max(1, Math.round(step));
  return { max: Math.ceil(maxVal / step - 1e-9) * step, step };
}
function renderTimeline() {
  const box = $('timeline');
  const { unit, items } = buckets();
  const byTime = state.metric === 'time';
  $('timeline-title').textContent = t(unit === 'day' ? 'perDay' : unit === 'week' ? 'perWeek' : 'perMonth');
  box.textContent = '';

  // table view of the same numbers
  const table = $('timeline-table');
  table.textContent = '';
  const head = table.createTHead().insertRow();
  for (const [label, cls] of [[t('bucket'), ''], [t('tTime'), 'num'], [t('tEntries'), 'num']]) {
    const th = el('th', cls, label);
    head.append(th);
  }
  const tbody = table.createTBody();
  for (const b of items) {
    const tr = tbody.insertRow();
    tr.append(el('td', null, bucketLabel(b, unit, true)), el('td', 'num', fmtDur(b.minutes) || '–'), el('td', 'num', num(b.entries)));
  }
  if (!items.length) { box.append(el('p', 'empty', t('none'))); return; }

  const W = Math.max(280, Math.floor(box.clientWidth)), H = 190;
  const m = { l: 46, r: 8, t: 10, b: 24 };
  const plotW = W - m.l - m.r, plotH = H - m.t - m.b, y0 = m.t + plotH;
  const values = items.map((b) => (byTime ? b.minutes / 60 : b.entries));
  const scale = niceScale(Math.max(...values), !byTime);
  const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img', 'aria-label': $('timeline-title').textContent });

  for (let v = 0; v <= scale.max + 1e-9; v += scale.step) {
    const y = y0 - (v / scale.max) * plotH;
    root.append(svg('line', { x1: m.l, x2: W - m.r, y1: y, y2: y, class: v === 0 ? 'axis' : 'grid' }));
    const label = svg('text', { x: m.l - 6, y: y + 4, 'text-anchor': 'end' });
    label.textContent = byTime ? `${v.toLocaleString(lang, { maximumFractionDigits: 1 })} h` : num(v);
    root.append(label);
  }

  const band = plotW / items.length;
  const w = Math.min(24, Math.max(1, band - 2));
  const every = Math.max(1, Math.ceil(items.length / Math.max(1, Math.floor(plotW / 64))));
  items.forEach((b, i) => {
    const x = m.l + i * band + (band - w) / 2;
    const h = (values[i] / scale.max) * plotH;
    if (h > 0) {
      const y = y0 - h, r = Math.min(4, w / 2, h);
      root.append(svg('path', { class: 'col',
        d: `M${x},${y0}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y0}Z` }));
    }
    if (i % every === 0) {
      const label = svg('text', { x: m.l + i * band + band / 2, y: H - 7, 'text-anchor': 'middle' });
      label.textContent = bucketLabel(b, unit, false);
      root.append(label);
    }
    const hit = svg('rect', { class: 'hit', x: m.l + i * band, y: m.t, width: band, height: plotH });
    const text = `${bucketLabel(b, unit, true)}\n${fmtDur(b.minutes) || '0 min'} · ${b.entries === 1 ? t('oneEntry') : t('nEntries', num(b.entries))}`;
    hit.addEventListener('pointermove', (ev) => showTip(text, ev.clientX, ev.clientY));
    hit.addEventListener('pointerleave', hideTip);
    root.append(hit);
  });
  box.append(root);
}

function showTip(text, x, y) {
  const tip = $('tip');
  tip.textContent = text;
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  tip.style.left = `${Math.max(4, Math.min(x + 12, window.innerWidth - r.width - 4))}px`;
  tip.style.top = `${Math.max(4, y - r.height - 10)}px`;
}
function hideTip() { $('tip').hidden = true; }

// ---------------------------------------------------------------- own tokens (proxy sign-in)

const dateTimeFmt = new Intl.DateTimeFormat(lang, { dateStyle: 'medium', timeStyle: 'short' });
const fmtWhen = (s) => (s ? dateTimeFmt.format(new Date(s)) : t('never'));

async function loadTokens() {
  const my = ++seq;
  $('status').textContent = t('loading');
  try {
    const data = await api('api/tokens');
    if (my !== seq) return;
    $('status').textContent = '';
    renderTokens(data.tokens);
  } catch (e) {
    if (my === seq && e.message !== '401') $('status').textContent = t('error', e.message);
  }
}
function renderTokens(tokens) {
  const table = $('token-table');
  table.textContent = '';
  const head = table.createTHead().insertRow();
  for (const key of ['colName', 'colScopes', 'colCreated', 'colUsed', 'colState', '']) head.append(el('th', null, key ? t(key) : ''));
  const body = table.createTBody();
  // active first, then by name
  const rows = [...tokens].sort((a, b) => (!!a.revoked_at - !!b.revoked_at) || a.name.localeCompare(b.name));
  for (const k of rows) {
    const tr = body.insertRow();
    if (k.revoked_at) tr.className = 'revoked';
    tr.append(el('td', null, k.name),
      el('td', null, k.scopes.map((s) => t(s === 'read' ? 'scopeRead' : s === 'write' ? 'scopeWrite' : s) || s).join(', ')),
      el('td', null, fmtWhen(k.created_at)), el('td', null, fmtWhen(k.last_used_at)),
      el('td', null, k.revoked_at ? t('revokedAt', fmtWhen(k.revoked_at)) : t('active')));
    const cell = el('td', 'actions');
    if (!k.revoked_at) {
      const b = el('button', 'danger', t('revoke'));
      b.type = 'button';
      b.addEventListener('click', () => revokeToken(k.name));
      cell.append(b);
    }
    tr.append(cell);
  }
  if (!rows.length) body.insertRow().append(el('td', 'muted', '–'));
}
async function revokeToken(name) {
  if (!confirm(t('revokeConfirm', name))) return;
  try {
    await apiPost(`api/tokens/${encodeURIComponent(name)}/revoke`);
  } catch (e) {
    if (e.message !== '401') alert(t('error', e.message));
  }
  loadTokens();
}
async function onCreateToken(ev) {
  ev.preventDefault();
  $('token-error').textContent = '';
  const form = $('token-form');
  const scopes = [...form.querySelectorAll('input[name=scope]:checked')].map((i) => i.value);
  if (!scopes.length) { $('token-error').textContent = t('pickScope'); return; }
  try {
    const data = await apiPost('api/tokens', { name: $('token-name').value.trim(), scopes });
    $('token-value').textContent = data.token;
    $('token-copy').textContent = t('copy');
    $('token-new').hidden = false;
    form.reset();
    $('token-copy').focus();
    loadTokens();
  } catch (e) {
    if (e.message === '401') return;
    $('token-error').textContent = /already exists/.test(e.message) ? t('nameTaken') : /^name:/.test(e.message) ? t('badName') : e.message;
  }
}
function hideNewToken() {
  $('token-value').textContent = '';
  $('token-new').hidden = true;
}
async function copyToken() {
  try {
    await navigator.clipboard.writeText($('token-value').textContent);
    $('token-copy').textContent = t('copied');
  } catch {
    getSelection().selectAllChildren($('token-value'));   // fallback: select for Ctrl+C
  }
}

// ---------------------------------------------------------------- start

async function start(session) {
  me = session;
  $('app').hidden = false;
  // token sign-in shows which token is used: "laptop · axute"
  $('who').textContent = session.agent ? `${session.agent} · ${session.user}` : session.user;
  $('tab-tokens').hidden = !session.manage_tokens;
  $('logout').hidden = session.via !== 'session';
  $('logout-link').hidden = !session.logout_url;
  if (session.logout_url) $('logout-link').href = session.logout_url;
  readHash();
  try { await loadFacets(); } catch (e) { if (e.message === '401') return; }
  syncControls();
  refresh();
  loadDupes();
}
async function loadFacets() {
  const f = await api('api/facets');
  facets = f;
  fillSelect('f-project', t('allProjects'), f.projects, state.project);
  fillSelect('f-agent', t('allAgents'), f.agents, state.agent);
  fillSelect('f-source', t('allSources'), f.sources, state.source);
  const list = $('project-names');
  list.textContent = '';
  for (const p of f.projects) list.append(new Option(p.name));
}

function init() {
  document.documentElement.lang = lang;
  for (const e of document.querySelectorAll('[data-t]')) e.textContent = t(e.dataset.t);
  for (const e of document.querySelectorAll('[data-tp]')) { e.placeholder = t(e.dataset.tp); e.setAttribute('aria-label', t(e.dataset.tp)); }
  for (const e of document.querySelectorAll('[data-ta]')) e.setAttribute('aria-label', t(e.dataset.ta));

  $('login').addEventListener('submit', onLogin);
  $('logout').addEventListener('click', onLogout);
  let timer = null;
  $('f-q').addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => { state.q = $('f-q').value; changed(); }, 250);
  });
  for (const [id, key] of [['f-project', 'project'], ['f-agent', 'agent'], ['f-source', 'source'], ['f-range', 'range'],
                           ['f-since', 'since'], ['f-until', 'until']]) {
    $(id).addEventListener('change', () => { state[key] = $(id).value; changed(); });
  }
  $('f-reset').addEventListener('click', () => {
    Object.assign(state, { ...DEFAULTS, tab: state.tab, metric: state.metric });
    changed();
  });
  for (const b of document.querySelectorAll('[data-tab]')) b.addEventListener('click', () => { state.tab = b.dataset.tab; changed(); });
  for (const b of document.querySelectorAll('[data-metric]')) {
    b.addEventListener('click', () => {
      state.metric = b.dataset.metric;
      writeHash();
      syncControls();
      if (overview) { renderProjects(); renderTimeline(); }
    });
  }
  $('more').addEventListener('click', () => loadEntries(false));
  window.addEventListener('hashchange', () => { readHash(); syncControls(); refresh(); });
  let raf = 0;
  new ResizeObserver(() => {
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => { if (overview && state.tab === 'overview') renderTimeline(); });
  }).observe($('timeline'));

  $('token-form').addEventListener('submit', onCreateToken);
  $('f-rename').addEventListener('click', renameProject);
  $('import-btn').addEventListener('click', () => $('import-file').click());
  $('import-file').addEventListener('change', importFile);
  $('dupes-toggle').addEventListener('click', () => { dupesOpen = !dupesOpen; renderDupes(); });
  $('token-copy').addEventListener('click', copyToken);
  $('token-done').addEventListener('click', hideNewToken);
  loadSession();
}
init();
