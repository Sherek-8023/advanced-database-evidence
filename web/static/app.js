'use strict';

(() => {
  const $ = (id) => document.getElementById(id);
  const views = {
    customers: { label: 'Customer 360', description: 'Explore customer identities and linked accounts', report: 'customer_360' },
    risk: { label: 'Transaction Risk', description: 'Daily transaction review', report: 'transaction_risk' },
    opportunities: { label: 'Product Opportunities', description: 'Explore suggestions and the rules behind them', report: 'product_opportunity' },
  };
  const actions = [
    { id: 'HOME_OR_PERSONAL_LOAN_REVIEW', label: 'Home or personal loan review', short: 'Home or personal loan review' },
    { id: 'CARD_REPAYMENT_SUPPORT_REVIEW', label: 'Card balance review', short: 'Card balance review' },
    { id: 'CREDIT_CARD_OFFER_REVIEW', label: 'Credit card offer review', short: 'Credit card offer review' },
    { id: 'NO_RULE_BASED_SUGGESTION', label: 'No rule-based suggestion', short: 'No rule-based suggestion' },
  ];
  const state = { data: null, view: 'customers', selected: null, selectedRisk: null, selectedOpportunity: null, initialFilterDefaultsApplied: false, busy: false, exporting: false, accounts: new Map(), accountController: null, accountSequence: 0, toastTimer: null };
  state.pages = { customers: 0, risk: 0, opportunities: 0 };
  state.context = new Map(); state.customerEpoch = 0; state.riskEpoch = 0;
  const moneyFormatter = new Intl.NumberFormat('en-AU', { style: 'currency', currency: 'AUD', minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const integerFormatter = new Intl.NumberFormat('en-AU');
  const dateFormatter = new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' });
  const timeFormatter = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23', timeZone: 'UTC' });

  // Report values always become text nodes; the API never supplies page markup.
  function node(tag, className, value) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (value !== undefined && value !== null) item.textContent = String(value);
    return item;
  }
  function add(parent, ...children) { children.forEach((child) => parent.append(child)); return parent; }
  function icon(name, className = '') {
    const result = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    result.setAttribute('class', `icon ${className}`);
    result.setAttribute('aria-hidden', 'true');
    result.setAttribute('viewBox', '0 0 24 24');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', `/icons.svg#${name}`);
    result.append(use);
    return result;
  }
  function number(value) { return value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? null : Number(value); }
  function money(value) { const parsed = number(value); return parsed === null ? 'Unknown' : moneyFormatter.format(parsed).replace(/^\$/, 'A$'); }
  function count(value) { const parsed = number(value); return parsed === null ? 'Unknown' : integerFormatter.format(parsed); }
  function text(value, fallback = 'Unknown') { return value === null || value === undefined || value === '' ? fallback : String(value); }
  function date(value, withTime = false) {
    if (!value) return 'Not recorded';
    const match = String(value).match(/^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2})(?::\d{2})?)?/);
    if (!match) return 'Unknown';
    const parsed = new Date(`${match[1]}T00:00:00Z`);
    if (Number.isNaN(parsed.getTime())) return 'Unknown';
    // Warehouse event timestamps have no timezone; do not silently convert them.
    return dateFormatter.format(parsed) + (withTime && match[2] ? `, ${match[2]}` : '');
  }
  function initials(name) { return text(name, '?').split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join('').toUpperCase(); }
  function normalized(value) { return String(value || '').toLocaleLowerCase(); }
  function matches(row, query, fields) { return !query || fields.some((field) => normalized(row[field]).includes(query)); }
  function sortCustomer(a, b) { return text(a.customer_name).localeCompare(text(b.customer_name)) || text(a.canonical_customer_id).localeCompare(text(b.canonical_customer_id)); }
  function badge(value, mode) {
    const label = text(value);
    const positive = ['MATCHED', 'ACTIVE', 'OPEN', 'NORMAL', 'MET'].includes(label);
    const warning = ['UNMATCHED', 'AMBIGUOUS', 'PENDING', 'REVIEW'].includes(label);
    const negative = ['WITHDRAWN', 'REVOKED', 'EXPIRED', 'CLOSED'].includes(label);
    return node('span', `badge badge-${mode || (positive ? 'positive' : warning ? 'warning' : negative ? 'negative' : 'neutral')}`, label);
  }
  function empty(title, description) { return add(node('div', 'empty-state'), node('strong', '', title), node('p', '', description)); }
  function booleanFlag(value) { return node('span', `boolean-value ${value === true ? 'yes' : value === false ? 'no' : 'unknown'}`, value === true ? 'Yes' : value === false ? 'No' : 'Unknown'); }
  function cell(primary, secondary, className = '') {
    const item = add(node('td', className), node('span', 'table-primary', primary));
    if (secondary !== undefined && secondary !== null) item.append(node('span', 'table-secondary', secondary));
    return item;
  }
  function table(headers, caption, className) {
    const result = add(node('table', className), node('caption', 'sr-only', caption));
    const header = node('tr');
    headers.forEach((heading) => { const item = node('th', heading.numeric ? 'numeric' : '', heading.label ?? heading); item.scope = 'col'; header.append(item); });
    result.append(add(node('thead'), header));
    const body = node('tbody'); result.append(body);
    return { table: result, body };
  }
  function filteredSummary(id, metrics) {
    const target = $(id);
    target.replaceChildren(node('div', 'summary-heading', 'Filtered results'));
    metrics.forEach(([value, label, isMoney]) => target.append(add(node('div', `summary-metric${isMoney ? ' money-metric' : ''}`), node('strong', 'summary-value', value), node('span', 'summary-label', label))));
  }
  function preserveSelectionFocus(render) {
    const active = document.activeElement;
    const key = active && active.dataset ? active.dataset.selectKey : null;
    render();
    if (key) { const next = [...document.querySelectorAll('[data-select-key]')].find((item) => item.dataset.selectKey === key); if (next) next.focus({ preventScroll: true }); }
  }
  function announceSelection(message) { $('selection-status').textContent = message; }
  function detailHeader(row, label, extra) {
    const header = add(node('div', 'detail-header'), node('div', 'detail-eyebrow', label));
    const title = add(node('div', 'detail-identity'), node('h2', '', text(row.customer_name)));
    if (extra) title.append(extra);
    add(header, title, node('span', 'detail-id', row.canonical_customer_id));
    return header;
  }
  function detailSection(title) { return add(node('section', 'detail-section'), node('h3', '', title)); }
  function infoCallout(message) { return add(node('div', 'info-callout'), icon('info'), node('span', '', message)); }
  function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(state.toastTimer); state.toastTimer = setTimeout(() => { $('toast').hidden = true; }, 5500); }
  function connectionStatus(connected) { $('database-status').className = connected ? '' : 'disconnected'; $('database-status').textContent = connected ? 'Reporting views connected' : 'Reports currently unavailable'; }
  function setBusy(busy) {
    state.busy = busy;
    $('refresh-button').disabled = busy;
    $('export-button').disabled = busy || state.exporting || !state.data;
    $('refresh-button').querySelector('span').textContent = busy ? 'Refreshing…' : 'Refresh';
    $('main').setAttribute('aria-busy', String(busy));
  }
  function showGlobalError(stale) {
    const box = $('global-message');
    box.replaceChildren(node('strong', '', stale ? 'Refresh unavailable · showing previously loaded data' : 'Reports are not available yet'));
    box.append(node('p', '', stale ? 'The database could not be reached or the reporting views are not ready. Last refreshed still shows the previous successful retrieval.' : 'Check that the project database is running and that the reporting views are ready, then try again.'));
    const retry = node('button', 'button button-secondary', 'Try again'); retry.type = 'button'; retry.addEventListener('click', loadReports); box.append(retry); box.hidden = false;
  }
  async function loadReports() {
    if (state.busy) return;
    setBusy(true); $('global-message').hidden = true;
    if (!state.data) $('loading-state').hidden = false;
    try {
      const response = await fetch('/api/reports', { cache: 'no-store', headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(20000) });
      if (!response.ok) throw new Error('Unavailable');
      const data = await response.json();
      if (!data || !['customer_360', 'transaction_risk', 'product_opportunity'].every((key) => Array.isArray(data[key])) || !data.meta) throw new Error('Invalid response');
      state.data = data; state.accounts.clear(); state.context.clear();
      if (!state.initialFilterDefaultsApplied) {
        const period = data.meta.data_period || {};
        if (/^\d{4}-\d{2}-\d{2}$/.test(period.from || '')) $('risk-from').value = period.from;
        if (/^\d{4}-\d{2}-\d{2}$/.test(period.to || '')) $('risk-to').value = period.to;
        state.initialFilterDefaultsApplied = true;
      }
      if (!data.customer_360.some((row) => row.canonical_customer_id === state.selected)) state.selected = ([...data.customer_360].sort((a,b) => String(b.latest_activity || '').localeCompare(String(a.latest_activity || ''))).find((row) => row.identity_status === 'MATCHED' && row.active_product_count >= 2) || data.customer_360[0] || {}).canonical_customer_id || null;
      if (!data.product_opportunity.some((row) => row.canonical_customer_id === state.selectedOpportunity)) state.selectedOpportunity = (data.product_opportunity.find((row) => /\balice\b/i.test(row.customer_name || '')) || data.product_opportunity[0] || {}).canonical_customer_id || null;
      updateMetadata(); updateFilterOptions(); renderCustomers(); renderRisk(); renderOpportunities();
      $('report-content').hidden = false; connectionStatus(true);
    } catch (error) { showGlobalError(Boolean(state.data)); connectionStatus(false); }
    finally { $('loading-state').hidden = true; setBusy(false); }
  }
  function updateMetadata() {
    const meta = state.data.meta;
    $('scenario-label').textContent = meta.demo_label || '';
    $('scenario-label').hidden = !meta.demo_label;
    let retrieval = 'Last refreshed: unavailable';
    if (meta.generated_at && !Number.isNaN(Date.parse(meta.generated_at))) {
      const parsed = new Date(meta.generated_at);
      retrieval = `Last refreshed: ${dateFormatter.format(parsed)}, ${timeFormatter.format(parsed)} UTC`;
    }
    $('snapshot-time').textContent = retrieval;
    const load = meta.last_load;
    $('load-time').textContent = load ? `Data through ${date(load.data_as_of)} · Verified load ${date(load.completed_at,true)} UTC` : 'Baseline quality fixture · load time not recorded';
    const period = meta.data_period || {};
    $('data-period').textContent = period.from && period.to ? `Transaction dates: ${date(period.from)} – ${date(period.to)}` : 'No transaction dates available';
    updateExportScope();
  }
  function updateExportScope() {
    if (!state.data) return;
    const total = state.data[views[state.view].report].length;
    const filtered = state.view === 'customers' ? customerRows() : state.view === 'risk' ? riskRows() : opportunityRows();
    $('export-scope').textContent = `CSV · ${count(filtered.length)} filtered / ${count(total)} total`;
    $('export-button').title = 'Export all rows matching the current filters, across every page.';
  }
  function updateFilterOptions() {
    const identity = $('identity-filter'); const previousIdentity = identity.value;
    identity.replaceChildren(new Option('All statuses', ''));
    [...new Set(state.data.customer_360.map((row) => row.identity_status).filter(Boolean))].sort().forEach((status) => identity.add(new Option(status, status)));
    if ([...identity.options].some((option) => option.value === previousIdentity)) identity.value = previousIdentity;
    const customer = $('risk-customer'); const previousCustomer = customer.value;
    customer.replaceChildren(new Option('All customers', ''));
    [...state.data.customer_360].sort(sortCustomer).forEach((row) => customer.add(new Option(`${text(row.customer_name)} · ${text(row.canonical_customer_id)}`, row.canonical_customer_id)));
    if ([...customer.options].some((option) => option.value === previousCustomer)) customer.value = previousCustomer;
  }
  function activateView() {
    const requested = location.hash.slice(1); state.view = Object.hasOwn(views, requested) ? requested : 'customers';
    document.querySelectorAll('[data-view]').forEach((link) => { if (link.dataset.view === state.view) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current'); });
    Object.keys(views).forEach((key) => { $(`view-${key}`).hidden = key !== state.view; });
    $('page-title').textContent = views[state.view].label; $('page-description').textContent = views[state.view].description;
    $('data-period').hidden = state.view !== 'risk'; document.title = `${views[state.view].label} · Poverty Bank`; updateExportScope();
  }

  function renderCustomers() {
    if (!state.data) return;
    const rows = customerRows();
    // Keep the selected customer on the visible page, including deep links.
    if (state.selected && rows.some((r) => r.canonical_customer_id === state.selected) && !state.pages.customers) state.pages.customers = Math.floor(rows.findIndex((r) => r.canonical_customer_id === state.selected)/20);
    const visible = paginate(rows,'customers',$('customer-list'),renderCustomers);
    if (!visible.some((row) => row.canonical_customer_id === state.selected)) state.selected = visible.length ? visible[0].canonical_customer_id : null;
    const matched = rows.filter((row) => row.identity_status === 'MATCHED').length;
    const unmatched = rows.filter((row) => row.identity_status === 'UNMATCHED').length;
    const ambiguous = rows.filter((row) => row.identity_status === 'AMBIGUOUS').length;
    const other = rows.length - matched - unmatched - ambiguous;
    const metrics = [[count(rows.length), 'customer identities'], [count(matched), 'matched'], [count(unmatched), 'unmatched']];
    if (ambiguous) metrics.push([count(ambiguous), 'ambiguous']);
    if (other) metrics.push([count(other), 'other identity statuses']);
    filteredSummary('customer-kpis', metrics);
    $('customer-result-count').textContent = `${count(rows.length)} of ${count(state.data.customer_360.length)} identities`;
    const list = $('customer-list'); list.replaceChildren();
    visible.forEach((row) => {
      const button = node('button', 'customer-item'); button.type = 'button';
      button.dataset.selectKey = `customer:${row.canonical_customer_id}`;
      button.setAttribute('aria-pressed', String(row.canonical_customer_id === state.selected));
      button.setAttribute('aria-label', `View details for ${text(row.customer_name)}, ${text(row.canonical_customer_id)}, ${text(row.identity_status)} identity`);
      add(button, node('span', 'avatar', initials(row.customer_name)), add(node('span', 'customer-item-text'), node('strong', '', text(row.customer_name)), node('small', '', row.canonical_customer_id)), badge(row.identity_status), icon('chevron-right', 'row-chevron'));
      button.addEventListener('click', () => { state.selected = row.canonical_customer_id; preserveSelectionFocus(renderCustomers); announceSelection(`Customer details updated: ${text(row.customer_name)}.`); }); list.append(button);
    });
    if (!rows.length) list.append(empty('No matching identities', 'Try a different search or identity status.'));
    const portfolio = [['No active accounts',rows.filter(r=>r.active_product_count===0).length],['1 active account',rows.filter(r=>r.active_product_count===1).length],['2 active accounts',rows.filter(r=>r.active_product_count===2).length],['3+ active accounts',rows.filter(r=>r.active_product_count>=3).length]];
    barChart('customer-analysis','Product coverage in filtered identities',portfolio);
    updateExportScope();
    renderCustomerDetail(rows.find((row) => row.canonical_customer_id === state.selected));
  }
  function renderCustomerDetail(row) {
    state.customerEpoch += 1;
    const detail = $('customer-detail'); detail.replaceChildren();
    if (state.accountController) state.accountController.abort(); state.accountSequence += 1;
    if (!row) { detail.append(empty('Select a customer identity', 'Details appear here for the selected entry in the customer directory.')); return; }
    detail.append(detailHeader(row, 'DETAILS FOR SELECTED CUSTOMER', badge(row.identity_status)));
    const summary = detailSection('Account summary'); const metrics = node('div', 'account-summary');
    [['Deposits', money(row.deposit_balance), true], ['Credit & loan balance', money(row.credit_and_loan_balance)], ['Active accounts', count(row.active_product_count)]].forEach(([label, value, accent]) => metrics.append(add(node('div', 'account-metric'), node('div', 'metric-label', label), node('div', `metric-value${accent ? ' accent' : ''}`, value))));
    summary.append(metrics); detail.append(summary);
    const accounts = node('section', 'detail-section'); const accountCount = node('span', 'table-count', 'Loading…');
    accounts.append(add(node('div', 'section-heading'), node('h3', '', 'Account portfolio'), accountCount));
    const container = node('div', 'table-scroll'); container.id = 'account-table'; container.tabIndex = 0; container.setAttribute('aria-label', 'Accounts linked to the selected customer. Scroll horizontally if needed.'); accounts.append(container); detail.append(accounts);
    const contacts = detailSection('Contact & consent'); const grid = node('div', 'contact-grid'); const contact = node('dl', 'detail-list');
    [['Email', row.email], ['Phone', row.phone]].forEach(([label, value]) => contact.append(add(node('div'), node('dt', '', label), node('dd', value ? '' : 'subdued', text(value, 'Not recorded')))));
    const consent = node('dl', 'detail-list consent-list');
    consent.append(add(node('div'), node('dt', '', 'CDR data sharing'), add(node('dd'), badge(row.cdr_consent_status))), add(node('div'), node('dt', '', 'Consent updated'), node('dd', '', date(row.consent_updated_at, true))), add(node('div'), node('dt', '', 'Latest recorded activity'), node('dd', '', date(row.latest_activity, true))));
    add(grid, contact, consent); contacts.append(grid); detail.append(contacts);
    const browse = node('button','text-button','View this customer’s transaction activity'); browse.type='button'; browse.addEventListener('click',()=>openRisk(row.canonical_customer_id)); contacts.append(browse);
    const timeline = detailSection('Recent activity'); timeline.append(node('p','evidence-note','Latest 20 transactions, repayments and CRM interactions. Amounts retain each source’s sign convention.'));
    const events=node('div','activity-list'); timeline.append(events); detail.append(timeline);
    loadPanel(`/api/activity?customer_id=${encodeURIComponent(row.canonical_customer_id)}`,events,'customerEpoch',state.customerEpoch,renderActivity);
    const lineage=node('details','lineage'); lineage.append(node('summary','','Sources & identity matching'));
    const sources=node('div','table-scroll'); lineage.append(sources); detail.append(lineage);
    loadPanel(`/api/identity?customer_id=${encodeURIComponent(row.canonical_customer_id)}`,sources,'customerEpoch',state.customerEpoch,renderLineage);
    loadAccounts(row.canonical_customer_id, container, accountCount);
  }
  async function loadAccounts(id, container, accountCount, force = false) {
    const sequence = state.accountSequence; const cached = state.accounts.get(id);
    if (cached && !force) { renderAccounts(cached, container, accountCount); return; }
    container.replaceChildren(node('div', 'inline-loading', 'Loading account details…'));
    const controller = new AbortController(); state.accountController = controller;
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(`/api/accounts?customer_id=${encodeURIComponent(id)}`, { cache: 'no-store', headers: { Accept: 'application/json' }, signal: controller.signal });
      if (!response.ok) throw new Error('Unavailable'); const data = await response.json();
      if (!data || !Array.isArray(data.accounts)) throw new Error('Invalid response'); if (sequence !== state.accountSequence) return;
      state.accounts.set(id, data.accounts); renderAccounts(data.accounts, container, accountCount);
    } catch (error) {
      if (sequence !== state.accountSequence) return; accountCount.textContent = 'Unavailable';
      const failure = empty('Account details unavailable', 'The customer report is loaded, but the account lookup did not complete.');
      const retry = node('button', 'text-button', 'Retry account lookup'); retry.type = 'button'; retry.addEventListener('click', () => loadAccounts(id, container, accountCount, true)); failure.append(retry); container.replaceChildren(failure);
    } finally { clearTimeout(timeout); }
  }
  function renderAccounts(rows, container, accountCount) {
    accountCount.textContent = `${count(rows.length)} linked account${rows.length === 1 ? '' : 's'}`;
    if (!rows.length) { container.replaceChildren(empty('No linked accounts', 'This identity has no accounts in the current warehouse.')); return; }
    const result = table(['Account', 'Product', { label: 'Balance', numeric: true }, 'Status'], 'Accounts linked to the selected canonical identity', 'account-table');
    rows.forEach((row) => { const account = cell(text(row.source_account_id)); account.title = `Source: ${text(row.source_system)}${row.opened_on ? ` · Opened ${date(row.opened_on)}` : ''}`; result.body.append(add(node('tr'), account, cell(text(row.product_name, text(row.product_type))), cell(money(row.current_balance), null, 'numeric'), add(node('td'), badge(row.account_status)))); });
    container.replaceChildren(result.table);
  }

  function riskKey(row) { return `${row.canonical_customer_id}:${row.calendar_date}`; }
  function renderRisk() {
    if (!state.data) return;
    const customer = $('risk-customer').value; const from = $('risk-from').value; const to = $('risk-to').value; const status = $('risk-status').value;
    const invalid = Boolean(from && to && from > to); $('risk-filter-error').hidden = !invalid;
    const rows = riskRows();
    const visible=paginate(rows,'risk',$('risk-table'),renderRisk);
    if (!visible.some((row) => riskKey(row) === state.selectedRisk)) state.selectedRisk = visible.length ? riskKey(visible[0]) : null;
    const transactions = rows.reduce((sum, row) => sum + (number(row.transaction_count) || 0), 0); const gross = rows.reduce((sum, row) => sum + (number(row.gross_transaction_value) || 0), 0);
    filteredSummary('risk-kpis', [[count(rows.length), status === 'REVIEW' ? 'customer-days for review' : status === 'NORMAL' ? 'normal customer-days' : 'customer-days'], [count(transactions), 'transactions'], [money(gross), 'gross value', true]]);
    $('risk-count').textContent = `${count(rows.length)} of ${count(state.data.transaction_risk.length)} customer-days`;
    if (!rows.length) $('risk-table').replaceChildren(empty(invalid ? 'Check the date range' : 'No matching transaction activity', invalid ? 'Set From date on or before To date.' : 'Adjust the customer, period or review status.'));
    else {
      const result = table(['Customer', 'Date', 'Transactions', { label: 'Gross value', numeric: true }, { label: 'Largest amount', numeric: true }, 'Status', { label: '' }], 'Daily transaction activity. Select a customer-day to see its review evidence.', 'risk-table');
      visible.forEach((row) => {
        const selected = riskKey(row) === state.selectedRisk; const tr = node('tr', `selectable-row${selected ? ' selected' : ''}`);
        const button = node('button', 'selection-button', text(row.customer_name)); button.type = 'button'; button.dataset.selectKey = `risk:${riskKey(row)}`; button.setAttribute('aria-pressed', String(selected)); button.setAttribute('aria-label', `View risk details for ${text(row.customer_name)}, ${date(row.calendar_date)}, ${text(row.review_flag)}`);
        add(tr, add(node('td'), button), cell(date(row.calendar_date), null, 'date-cell'), cell(count(row.transaction_count), null, 'count-cell'), cell(money(row.gross_transaction_value), null, 'numeric'), cell(money(row.largest_transaction), null, 'numeric'), add(node('td'), badge(row.review_flag)), add(node('td', 'row-end'), icon('chevron-right', 'row-chevron')));
        tr.addEventListener('click', () => { state.selectedRisk = riskKey(row); preserveSelectionFocus(renderRisk); announceSelection(`Risk evidence updated: ${text(row.customer_name)}, ${date(row.calendar_date)}.`); }); result.body.append(tr);
      }); $('risk-table').replaceChildren(result.table);
    }
    lineChart(rows); updateExportScope();
    renderRiskDetail(rows.find((row) => riskKey(row) === state.selectedRisk));
  }
  function thresholdEvidence(label, value, threshold, met, countEvidence, primary) {
    if (primary) {
      const card = add(node('div', `evidence-card${met ? '' : ' neutral-evidence'}`), node('h3', '', label));
      let values;
      if (countEvidence) values = add(node('div', 'evidence-count-copy'), node('strong', 'evidence-number count-number', value), add(node('div'), node('strong', '', 'transactions'), node('p', 'evidence-note', threshold)));
      else values = add(node('div'), node('strong', 'evidence-number', value), node('p', 'evidence-note', threshold));
      card.append(add(node('div', 'evidence-row'), values, badge(met ? 'MET' : 'NOT MET'))); return card;
    }
    return add(node('div', 'supporting-evidence'), add(node('div'), node('div', 'supporting-label', label), node('div', 'supporting-value', value), node('p', 'evidence-note', threshold)), badge(met ? 'MET' : 'NOT MET'));
  }
  function renderRiskDetail(row) {
    state.riskEpoch += 1;
    const detail = $('risk-detail'); detail.className = 'detail-panel risk-detail'; detail.replaceChildren();
    if (!row) { detail.append(empty('Select a transaction day', 'Trigger evidence appears here for the selected daily activity row.')); return; }
    const header = detailHeader(row, 'DETAILS FOR SELECTED ROW'); header.append(add(node('div', 'detail-subline'), node('span', '', date(row.calendar_date)), badge(row.review_flag))); detail.append(header);
    const countMet = (number(row.transaction_count) ?? 0) >= 3; const amountMet = (number(row.largest_transaction) ?? 0) >= 1500;
    const evidence = detailSection(row.review_flag === 'REVIEW' ? 'Trigger evidence' : 'Threshold checks');
    if (countMet) {
      evidence.append(thresholdEvidence('Daily transaction threshold reached', count(row.transaction_count), 'Threshold: at least 3', true, true, true), thresholdEvidence('Largest transaction', money(row.largest_transaction), 'Threshold: at least A$1,500', amountMet, false, amountMet));
    } else if (amountMet) {
      evidence.append(thresholdEvidence('Largest transaction threshold reached', money(row.largest_transaction), 'Threshold: at least A$1,500', true, false, true), thresholdEvidence('Daily transaction count', `${count(row.transaction_count)} transactions`, 'Threshold: at least 3', false, true, false));
    } else {
      evidence.append(thresholdEvidence('Daily transaction count', count(row.transaction_count), 'Threshold: at least 3', false, true, true), thresholdEvidence('Largest transaction', money(row.largest_transaction), 'Threshold: at least A$1,500', false, false, false));
    }
    detail.append(evidence);
    const other = detailSection('Other details'); other.append(node('div', 'supporting-label', 'Gross transaction value'), node('div', 'supporting-value', money(row.gross_transaction_value)));
    other.append(infoCallout(countMet && amountMet ? 'Both thresholds are met. Either threshold is sufficient for REVIEW; this is not a finding of fraud.' : row.review_flag === 'REVIEW' ? 'A review flag is not a finding of fraud. Either threshold is sufficient for REVIEW.' : 'Neither review threshold is met for this customer-day. NORMAL is a rule result, not a guarantee of safety.')); detail.append(other);
    profileButton(detail,row.canonical_customer_id);
    const section=detailSection('Transactions behind this result'); const transactions=node('div','table-scroll'); section.append(transactions); detail.append(section);
    loadPanel(`/api/transactions?customer_id=${encodeURIComponent(row.canonical_customer_id)}&date=${row.calendar_date}`,transactions,'riskEpoch',state.riskEpoch,renderTransactions);
  }

  function productRules(row) {
    const ready = !row.review_readiness || row.review_readiness === 'READY';
    const deposits = number(row.total_deposits) ?? 0; const card = number(row.card_balance) ?? 0;
    const missingLoan = row.has_loan === null || row.has_loan === undefined; const missingCard = row.has_credit_card === null || row.has_credit_card === undefined;
    return [
      { label: 'Loan review', condition: 'Active deposits ≥ A$10,000; no active loan recorded.', met: ready && deposits >= 10000 && row.has_loan === false, reason: !ready ? 'Data review required' : deposits < 10000 ? 'Deposits below A$10,000' : row.has_loan === true ? 'Active loan present' : missingLoan ? 'Loan coverage unknown' : 'No active loan recorded' },
      { label: 'Card balance review', condition: 'Active card balance ≥ A$1,000.', met: ready && card >= 1000, reason: !ready ? 'Data review required' : card >= 1000 ? 'Illustrative threshold met' : 'Card balance below A$1,000' },
      { label: 'Credit card review', condition: 'Active deposits ≥ A$5,000; no active card recorded.', met: ready && deposits >= 5000 && row.has_credit_card === false, reason: !ready ? 'Data review required' : deposits < 5000 ? 'Deposits below A$5,000' : row.has_credit_card === true ? 'Active card present' : missingCard ? 'Card coverage unknown' : 'No active card recorded' },
    ];
  }
  function renderOpportunities() {
    if (!state.data) return;
    const all = state.data.product_opportunity; const selectedType = $('opportunity-type').value; const query = normalized($('opportunity-search').value.trim());
    const rows = opportunityRows();
    const visible=paginate(rows,'opportunities',$('opportunity-table'),renderOpportunities);
    if (!visible.some((row) => row.canonical_customer_id === state.selectedOpportunity)) state.selectedOpportunity = visible.length ? visible[0].canonical_customer_id : null;
    const suggestions = rows.filter((row) => actions.slice(0, 3).some((action) => action.id === row.suggested_action)).length;
    const noSuggestion = rows.filter((row) => row.suggested_action === 'NO_RULE_BASED_SUGGESTION').length;
    filteredSummary('opportunity-kpis', [[count(rows.length), 'identities'], [count(suggestions), 'suggested reviews'], [count(noSuggestion), 'with no suggestion']]);
    $('opportunity-count').textContent = `${count(rows.length)} of ${count(all.length)} identities`;
    if (!rows.length) $('opportunity-table').replaceChildren(empty('No matching rule results', 'Try another suggestion type or customer search.'));
    else {
      const result = table(['Customer', 'Suggested review', { label: '' }], 'Product opportunity results. Select a customer to see the ordered rule evaluation.', 'opportunity-table');
      visible.forEach((row) => {
        const selected = row.canonical_customer_id === state.selectedOpportunity; const tr = node('tr', `selectable-row${selected ? ' selected' : ''}`); const action = actions.find((item) => item.id === row.suggested_action);
        const button = add(node('button', 'selection-button'), node('span', 'table-primary', text(row.customer_name)), node('span', 'table-secondary', row.canonical_customer_id)); button.type = 'button'; button.dataset.selectKey = `opportunity:${row.canonical_customer_id}`; button.setAttribute('aria-pressed', String(selected)); button.setAttribute('aria-label', `View opportunity details for ${text(row.customer_name)}, ${text(row.canonical_customer_id)}`);
        add(tr, add(node('td'), button), add(node('td'), node('span', `suggestion-short${row.suggested_action === 'NO_RULE_BASED_SUGGESTION' ? ' suggestion-none' : ''}`, action ? action.short : text(row.suggested_action))), add(node('td', 'row-end'), icon('chevron-right', 'row-chevron')));
        tr.addEventListener('click', () => { state.selectedOpportunity = row.canonical_customer_id; preserveSelectionFocus(renderOpportunities); announceSelection(`Opportunity details updated: ${text(row.customer_name)}.`); }); result.body.append(tr);
      }); $('opportunity-table').replaceChildren(result.table);
    }
    barChart('opportunity-analysis','Review distribution in filtered results',actions.map(action=>[action.short,rows.filter(r=>r.suggested_action===action.id).length])); updateExportScope();
    renderOpportunityDetail(rows.find((row) => row.canonical_customer_id === state.selectedOpportunity));
  }
  function renderOpportunityDetail(row) {
    const detail = $('opportunity-detail'); detail.className = 'detail-panel opportunity-detail'; detail.replaceChildren();
    if (!row) { detail.append(empty('Select an opportunity result', 'The suggestion and its rule evidence appear here for the selected customer.')); return; }
    detail.append(detailHeader(row, 'DETAILS FOR SELECTED CUSTOMER'));
    if (row.rule_reason) detail.append(infoCallout(row.rule_reason));
    if (row.review_readiness && row.review_readiness!=='READY') detail.append(badge(row.review_readiness,'warning'));
    profileButton(detail,row.canonical_customer_id);
    const action = actions.find((item) => item.id === row.suggested_action); const rules = productRules(row); const selectedRule = rules.findIndex((rule) => rule.met);
    const recommendation = node('section', 'detail-section'); recommendation.append(node('div', 'opportunity-label', 'SUGGESTED REVIEW'), node('h3', 'recommendation-title', action ? action.label : text(row.suggested_action)));
    if (row.suggested_action === 'NO_RULE_BASED_SUGGESTION') recommendation.append(add(node('div', 'evidence-card neutral-evidence'), node('strong', '', row.review_readiness !== 'READY' ? 'Data review required first' : 'No matching review rule'), node('p', 'evidence-note', row.review_readiness !== 'READY' ? 'Product suggestions are withheld until identity and account coverage can be confirmed.' : 'None of the three ordered conditions matches the current active-account data.')));
    else if (action) {
      const isCard = row.suggested_action === 'CARD_REPAYMENT_SUPPORT_REVIEW'; const isLoan = row.suggested_action === 'HOME_OR_PERSONAL_LOAN_REVIEW';
      const evidence = node('div', 'evidence-card'); const values = add(node('div'), node('div', 'supporting-label', isCard ? 'Card balance' : 'Deposits'), node('strong', 'evidence-number', money(isCard ? row.card_balance : row.total_deposits)), node('p', 'evidence-note', isCard ? 'Threshold: at least A$1,000' : isLoan ? 'At least A$10,000 and no loan flag' : 'At least A$5,000 and no credit card flag'));
      evidence.append(add(node('div', 'evidence-row'), values, badge('MATCHED'))); recommendation.append(evidence);
    }
    const flags = node('div', 'product-flags');
    const balanceLabel = row.suggested_action === 'CARD_REPAYMENT_SUPPORT_REVIEW' ? 'Deposits' : 'Card balance'; const balanceValue = row.suggested_action === 'CARD_REPAYMENT_SUPPORT_REVIEW' ? row.total_deposits : row.card_balance;
    flags.append(add(node('div', 'product-flag'), node('div', 'product-flag-label', balanceLabel), node('div', 'product-flag-value', money(balanceValue))));
    [['Active card recorded', row.has_credit_card], ['Active loan recorded', row.has_loan]].forEach(([label, value]) => flags.append(add(node('div', 'product-flag'), node('div', 'product-flag-label', label), add(node('div', 'product-flag-value'), booleanFlag(value)))));
    recommendation.append(flags);
    // With no suggestion both balances are useful evidence, not only the card balance.
    if (row.suggested_action === 'NO_RULE_BASED_SUGGESTION') recommendation.append(node('p', 'evidence-note', `Deposits: ${money(row.total_deposits)}`));
    detail.append(recommendation);
    const evaluation = detailSection('Rule evaluation'); const list = node('ol', 'rule-list');
    rules.forEach((rule, index) => {
      const skipped = selectedRule !== -1 && index > selectedRule; const outcome = skipped ? 'NOT EVALUATED' : rule.met ? 'MATCHED' : 'NOT MATCHED';
      const item = add(node('li', 'rule-item'), node('span', 'rule-number', `0${index + 1}`), add(node('div', 'rule-copy'), node('strong', '', rule.label), node('p', '', rule.condition)), add(node('div', 'rule-outcome'), badge(outcome, outcome === 'MATCHED' ? 'positive' : 'neutral'), node('p', '', skipped ? 'Earlier rule matched' : rule.reason))); list.append(item);
    });
    evaluation.append(list, node('p', 'rules-note', 'Rules are checked in order. The first match is returned. Suggestions are review candidates, not eligibility or approval decisions.'));
    if (row.has_credit_card == null || row.has_loan == null) evaluation.append(node('p', 'unknown-note', 'Product coverage is unknown. Missing account data cannot establish that a product is absent.'));
    detail.append(evaluation);
  }

  function customerRows() {
    const query=normalized($('customer-search').value.trim()), status=$('identity-filter').value;
    return state.data.customer_360.filter(r=>matches(r,query,['customer_name','canonical_customer_id','email'])&&(!status||r.identity_status===status)).sort(sortCustomer);
  }
  function riskRows() {
    const customer=$('risk-customer').value, from=$('risk-from').value, to=$('risk-to').value, status=$('risk-status').value;
    if(from&&to&&from>to)return [];
    return state.data.transaction_risk.filter(r=>(!customer||r.canonical_customer_id===customer)&&(!from||r.calendar_date>=from)&&(!to||r.calendar_date<=to)&&(!status||r.review_flag===status)).sort((a,b)=>String(b.calendar_date).localeCompare(String(a.calendar_date))||sortCustomer(a,b));
  }
  function opportunityRows() {
    const query=normalized($('opportunity-search').value.trim()), type=$('opportunity-type').value;
    return state.data.product_opportunity.filter(r=>(!type||r.suggested_action===type)&&matches(r,query,['customer_name','canonical_customer_id'])).sort((a,b)=>actions.findIndex(x=>x.id===a.suggested_action)-actions.findIndex(x=>x.id===b.suggested_action)||sortCustomer(a,b));
  }
  function paginate(rows,key,container,render) {
    const pages=Math.max(1,Math.ceil(rows.length/20)); state.pages[key]=Math.max(0,Math.min(state.pages[key],pages-1));
    let nav=$(`${key}-pager`); if(!nav){nav=node('div','pager');nav.id=`${key}-pager`;container.after(nav);}
    nav.replaceChildren();
    for(const [label,delta] of [['Previous',-1],['Next',1]]){
      const button=node('button','text-button',label);button.type='button';button.disabled=delta<0?state.pages[key]===0:state.pages[key]>=pages-1;
      button.addEventListener('click',()=>{state.pages[key]+=delta;if(key==='customers')state.selected=null;render();nav.querySelector('button:not(:disabled)')?.focus();});
      nav.append(button);if(delta<0)nav.append(node('span','',`Page ${state.pages[key]+1} of ${pages} · 20 per page`));
    }
    return rows.slice(state.pages[key]*20,(state.pages[key]+1)*20);
  }
  function openRisk(id){$('risk-customer').value=id;$('risk-status').value='';$('risk-from').value=state.data.meta.data_period.from||'';$('risk-to').value=state.data.meta.data_period.to||'';state.pages.risk=0;state.selectedRisk=null;renderRisk();location.hash='risk';}
  function profileButton(container,id){const button=node('button','text-button context-link','Open customer profile');button.type='button';button.addEventListener('click',()=>{$('customer-search').value=id;$('identity-filter').value='';state.selected=id;state.pages.customers=0;renderCustomers();location.hash='customers';});container.append(button);}
  async function loadPanel(url,container,scope,epoch,render){
    container.replaceChildren(node('p','inline-loading','Loading…'));
    try{
      let data=state.context.get(url);
      if(!data){const response=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(12000)});if(!response.ok)throw new Error();data=await response.json();if(!Array.isArray(data.rows))throw new Error();state.context.set(url,data);}
      if(state[scope]===epoch)render(data.rows,container);
    }catch(error){if(state[scope]!==epoch)return;const retry=node('button','text-button','Retry detail lookup');retry.type='button';retry.addEventListener('click',()=>loadPanel(url,container,scope,epoch,render));container.replaceChildren(node('p','evidence-note','Details could not be loaded.'),retry);}
  }
  function renderActivity(rows,container){
    container.replaceChildren();if(!rows.length){container.append(empty('No recorded activity','This identity has no transaction, repayment or CRM event.'));return;}
    rows.forEach(row=>{const event=node('article','activity-event');event.append(add(node('div','activity-head'),node('strong','',text(row.description)),node('span','',row.amount==null?'':money(row.amount))),node('p','evidence-note',`${date(row.event_ts,true)} · ${row.event_type} · ${row.channel}`),node('small','',`${row.source_system} · ${row.event_id}${row.account_id?' · '+row.account_id:''}`));container.append(event);});
  }
  function renderLineage(rows,container){
    const result=table(['Source / original ID','Source profile','Matching rule'],'Source records for this canonical identity','context-table');
    rows.forEach(r=>{const profile=cell(r.full_name,text(r.email,'No email recorded'));profile.append(node('span','table-secondary',`DOB: ${date(r.date_of_birth)}`));result.body.append(add(node('tr'),cell(r.source_system,r.source_customer_id),profile,cell(r.match_status,r.match_method)));});container.replaceChildren(result.table);
  }
  function renderTransactions(rows,container){
    if(!rows.length){container.replaceChildren(empty('No transaction details','Refresh the reports and try again.'));return;}
    const total=rows.reduce((sum,r)=>sum+Math.abs(Number(r.amount)),0);
    const summary=node('p','detail-reconcile',`${rows.length} transactions · Gross ${money(total)} · Matches this customer-day’s detail scope`);
    const result=table(['Time / account','Merchant / source','Signed amount'],'Underlying transactions for the selected customer-day','context-table');
    rows.forEach(r=>result.body.append(add(node('tr',Math.abs(r.amount)>=1500?'amount-trigger':''),cell(date(r.transaction_ts,true),r.source_account_id),cell(text(r.merchant,r.transaction_type),`${r.source_system} · ${r.source_transaction_id} · ${r.channel}`),cell(money(r.amount),Math.abs(r.amount)>=1500?'Amount threshold met':r.transaction_type,'numeric'))));container.replaceChildren(summary,result.table);
  }
  function svgNode(tag,attributes={},value){const element=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(attributes).forEach(([key,val])=>element.setAttribute(key,String(val)));if(value!==undefined)element.textContent=String(value);return element;}
  function barChart(id,title,entries){
    const root=$(id);root.replaceChildren(node('h3','',title));const max=Math.max(1,...entries.map(x=>x[1]));
    const chart=svgNode('svg',{viewBox:`0 0 720 ${entries.length*32+8}`,role:'img','aria-label':`${title}. ${entries.map(x=>x.join(': ')).join('; ')}`});
    entries.forEach(([label,value],i)=>{const y=i*32+3;chart.append(svgNode('text',{x:0,y:y+17,class:'chart-label'},label),svgNode('rect',{x:215,y,width:440,height:22,rx:4,class:'chart-track'}),svgNode('rect',{x:215,y,width:440*value/max,height:22,rx:4,class:'chart-fill'}),svgNode('text',{x:670,y:y+17,class:'chart-label'},count(value)));});root.append(chart);
  }
  function lineChart(rows){
    const root=$('risk-analysis');root.replaceChildren(node('h3','','Daily transaction count in filtered results'));
    if(!rows.length){root.append(node('p','evidence-note','No activity in the current filter scope.'));return;}
    const grouped=new Map();rows.forEach(r=>grouped.set(r.calendar_date,(grouped.get(r.calendar_date)||0)+Number(r.transaction_count)));
    const dates=[...grouped.keys()].sort(), start=new Date(dates[0]+'T00:00:00Z'),end=new Date(dates.at(-1)+'T00:00:00Z'), values=[];
    for(let day=new Date(start);day<=end;day.setUTCDate(day.getUTCDate()+1)){const key=day.toISOString().slice(0,10);values.push([key,grouped.get(key)||0]);}
    const max=Math.max(1,...values.map(x=>x[1])), chart=svgNode('svg',{viewBox:'0 0 900 155',role:'img','aria-label':`Daily transactions from ${dates[0]} to ${dates.at(-1)}. ${rows.reduce((s,r)=>s+Number(r.transaction_count),0)} transactions in filtered customer-days.`});
    [0,0.5,1].forEach(r=>{const y=115-r*90;chart.append(svgNode('line',{x1:40,x2:870,y1:y,y2:y,class:'chart-grid'}),svgNode('text',{x:3,y:y+4,class:'chart-label'},Math.round(max*r)));});
    const points=values.map((v,i)=>`${40+i*830/Math.max(1,values.length-1)},${115-v[1]/max*90}`);
    chart.append(svgNode('polyline',{points:points.join(' '),class:'trend-line',fill:'none'}));
    values.forEach((v,i)=>chart.append(add(svgNode('circle',{cx:40+i*830/Math.max(1,values.length-1),cy:115-v[1]/max*90,r:2.4,class:'chart-dot'}),svgNode('title',{},`${v[0]}: ${v[1]} transactions`))));
    chart.append(svgNode('text',{x:40,y:143,class:'chart-label'},date(dates[0])),svgNode('text',{x:870,y:143,'text-anchor':'end',class:'chart-label'},date(dates.at(-1))));root.append(chart,node('p','evidence-note','Counts follow all current filters. Zero means no transactions in the selected customer-day results; this is not a balance or loss chart.'));
  }
  function installEnhancements(){
    const meta=node('span');meta.id='load-time';$('snapshot-time').after(meta);
    for(const key of ['customer','risk','opportunity']){const chart=node('section','analysis-panel');chart.id=`${key}-analysis`;$(`${key}-kpis`).after(chart);}
    const all=node('button','text-button','Export all');all.type='button';all.id='export-all';all.addEventListener('click',()=>exportReport(true));$('export-button').parentElement.append(all);
    $('export-button').querySelector('span').textContent='Export filtered';
    const presets=node('div','period-presets');presets.setAttribute('aria-label','Transaction period shortcuts');
    [['Recent 7 data days',7],['Recent 30 data days',30],['Full history',null]].forEach(([label,days])=>{const button=node('button','text-button',label);button.type='button';button.addEventListener('click',()=>{if(!state.data)return;const period=state.data.meta.data_period;const end=new Date(period.to+'T00:00:00Z');if(days)end.setUTCDate(end.getUTCDate()-days+1);$('risk-from').value=days?end.toISOString().slice(0,10):period.from;$('risk-to').value=period.to;state.pages.risk=0;renderRisk();});presets.append(button);});$('risk-kpis').before(presets);
  }
  async function exportReport(full = false) {
    if (state.exporting || state.busy || !state.data) return;
    const report = views[state.view].report; const button = $('export-button'); state.exporting = true; button.disabled = true;
    try {
      let blob;
      if(full){const response = await fetch(`/api/export?report=${encodeURIComponent(report)}`, { cache: 'no-store', signal: AbortSignal.timeout(20000) });if (!response.ok) throw new Error('Unavailable');blob=await response.blob();}
      else{const rows=state.view==='customers'?customerRows():state.view==='risk'?riskRows():opportunityRows();const columns=Object.keys(state.data[report][0]||{});const escape=value=>{if(value==null)return '';let text=String(value);if(typeof value==='string'&&(/^[\s]*[=+@-]/.test(text)||/^[\t\r\n]/.test(text)))text="'"+text;return '"'+text.replaceAll('"','""')+'"';};blob=new Blob(['\uFEFF'+[columns,...rows.map(row=>columns.map(key=>row[key]))].map(row=>row.map(escape).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'});}
      const url = URL.createObjectURL(blob); const download = document.createElement('a'); download.href = url; download.download = `${report}${full?'':'_filtered'}.csv`; document.body.append(download); download.click(); download.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); toast(full?'Full report CSV downloaded.':'Filtered CSV downloaded, including all matching pages.');
    } catch (error) { toast('CSV export is unavailable. Check the database connection and try again.'); }
    finally { state.exporting = false; button.disabled = state.busy || !state.data; }
  }

  installEnhancements();
  [['customers',['customer-search','identity-filter','customer-clear']],['risk',['risk-customer','risk-from','risk-to','risk-status','risk-clear']],['opportunities',['opportunity-search','opportunity-type','opportunity-clear']]].forEach(([key,ids])=>ids.forEach(id=>{for(const event of ['input','change','click'])$(id).addEventListener(event,()=>{state.pages[key]=0;if(key==='customers')state.selected=null;});}));
  actions.forEach((action) => $('opportunity-type').add(new Option(action.label, action.id)));
  $('customer-search').addEventListener('input', renderCustomers); $('identity-filter').addEventListener('change', renderCustomers);
  $('customer-clear').addEventListener('click', () => { $('customer-search').value = ''; $('identity-filter').value = ''; renderCustomers(); });
  ['risk-customer', 'risk-from', 'risk-to', 'risk-status'].forEach((id) => $(id).addEventListener('change', renderRisk));
  $('risk-clear').addEventListener('click', () => { ['risk-customer', 'risk-from', 'risk-to', 'risk-status'].forEach((id) => { $(id).value = ''; }); renderRisk(); });
  $('opportunity-search').addEventListener('input', renderOpportunities); $('opportunity-type').addEventListener('change', renderOpportunities);
  $('opportunity-clear').addEventListener('click', () => { $('opportunity-search').value = ''; $('opportunity-type').value = ''; renderOpportunities(); });
  $('refresh-button').addEventListener('click', loadReports); $('export-button').addEventListener('click', ()=>exportReport(false));
  document.querySelector('.skip-link').addEventListener('click', (event) => { event.preventDefault(); $('main').focus(); });
  window.addEventListener('hashchange', activateView); activateView(); loadReports();
})();
