'use strict';
let csrf = '';
const $ = id => document.getElementById(id);
const tell = text => { $('message').textContent = text; };
const amount = (value, currency = 'USD') => value == null ? 'Unknown' : new Intl.NumberFormat('en', { style: 'currency', currency, maximumFractionDigits: 2 }).format(Number(value));
const when = value => value ? new Date(value).toLocaleString('en-GB', { timeZone: 'Asia/Riyadh', dateStyle: 'medium', timeStyle: 'short' }) + ' Saudi' : 'Unknown';
function textNode(tag, text, className) { const node = document.createElement(tag); node.textContent = text; if (className) node.className = className; return node; }
function card(id, title, lines) { const node = document.createElement('article'); node.className = 'card'; node.append(textNode('strong', title)); for (const line of lines) node.append(textNode('p', line)); $(id).append(node); return node; }
function clear(id, empty) { $(id).replaceChildren(); if (empty) $(id).append(textNode('p', 'No verified records available.')); }
async function api(path, body) {
  const response = await fetch(path, { method: body === undefined ? 'GET' : 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Request rejected');
  return data;
}
async function refresh() {
  try {
    const data = await api('/api/dashboard');
    $('workspace').hidden = false; $('login').hidden = true;
    $('health').textContent = `Report: ${data.system_health} · ${data.session} session · Eligible quote data: ${data.provider_status ?? 'unavailable'} · ${when(data.as_of_riyadh)}`;
    $('cash').textContent = `${data.reconciliation}. Confirmed: ${amount(data.confirmed_cash_sar, 'SAR')} · Estimated: ${amount(data.estimated_cash_sar, 'SAR')} · Buying power: ${amount(data.buying_power_sar, 'SAR')}`;
    const stocks = data.report?.stocks ?? [];
    clear('candidates', !stocks.length);
    for (const stock of stocks) {
      const lines = [`${stock.status} · Technical checks ${stock.score_0_to_5 ?? '—'}/5`, stock.quote ? `Indicative ${amount(stock.quote.price)} · ${stock.quote.source} · Observed ${when(stock.quote.observed_at_utc)}` : 'Eligible current quote unavailable.'];
      if (stock.allocation?.eligible && stock.status === 'CONDITIONAL ENTRY REVIEW') lines.push(`Provisional allocation up to ${amount(stock.allocation.allocation_sar, 'SAR')}. Awaed preview required.`);
      const node = card('candidates', stock.ticker, lines);
      const details = document.createElement('details'); details.append(textNode('summary', 'Evidence & missing requirements'));
      for (const reason of stock.reasons ?? []) details.append(textNode('p', reason));
      node.append(details);
    }
    clear('positions', !data.positions.length);
    for (const position of data.positions) card('positions', position.ticker, [`${position.quantity} shares · Cost basis ${amount(position.cost_basis_usd)}`, `Market value ${amount(position.market_value_usd)} · Unrealized P/L ${amount(position.unrealized_pnl_usd)}`, `Realized P/L ${amount(position.realized_pnl_usd)} · Broker reconciliation required`]);
    clear('opening-drafts', false);
    for (const draft of data.opening_drafts ?? []) {
      const holding = draft.snapshot;
      const node = card('opening-drafts', `${holding.ticker} · Confirm opening snapshot`, [`${holding.quantity} shares · Total cost basis ${amount(holding.total_cost_usd)}`, `Checked ${when(holding.as_of_utc)}. No acquisition date inferred.`]);
      const button = textNode('button', 'Confirm current holding');
      button.onclick = async () => { try { await api(`/api/opening-holdings/${draft.id}/confirm`, { confirm_current_snapshot: true }); await refresh(); } catch (error) { tell(error.message); } };
      node.append(button);
    }
    clear('alerts', !data.recent_alerts.length);
    for (const event of data.recent_alerts) card('alerts', `Notification: ${event.payload.state}`, [when(event.payload.at_utc), event.payload.error_code ? 'Delivery needs operator review.' : 'API submission is not a read receipt.']);
    clear('predictions', !data.predictions.length);
    for (const event of data.predictions) card('predictions', event.payload.candidate?.ticker ?? 'Research record', [when(event.payload.logged_at_utc), 'Outcome not yet evaluated. No accuracy claim.']);
    clear('submissions', !data.submissions.length);
    for (const submission of data.submissions) {
      const fill = submission.fill;
      const node = card('submissions', `${fill.ticker} · ${fill.side.toUpperCase()} · ${submission.state.replaceAll('_', ' ')}`, [`${fill.quantity} shares at ${amount(fill.price)} · Fees ${amount(fill.fees)}`, `Executed ${when(fill.executed_at_utc)}`, fill.reference ? `Reference: ${fill.reference}` : 'No transaction reference supplied.']);
      if (submission.state !== 'CONFIRMED') {
        const target = submission.state === 'DRAFT' ? 'PENDING_CONFIRMATION' : 'CONFIRMED';
        const button = textNode('button', target === 'CONFIRMED' ? 'Confirm actual fill' : 'Review submission');
        button.onclick = async () => { try { await api(`/api/transactions/${submission.id}/${target}`, {}); await refresh(); } catch (error) { tell(error.message); } };
        node.append(button);
      }
    }
  } catch (error) { tell(error.message); }
}
$('login-form').onsubmit = async event => { event.preventDefault(); try { const data = await api('/api/login', { key: $('key').value }); csrf = data.csrf; $('key').value = ''; tell(''); await refresh(); } catch (error) { tell(error.message); } };
$('fill').onsubmit = async event => {
  event.preventDefault();
  try { const fill = Object.fromEntries(new FormData(event.target)); fill.currency = 'USD'; fill.executed_at_utc = new Date(fill.executed_at_utc + '+03:00').toISOString(); await api('/api/transactions', fill); tell('Draft saved. Review and confirm the actual fill below.'); await refresh(); } catch (error) { tell(error.message); }
};
$('account').onsubmit = async event => {
  event.preventDefault();
  try { const data = Object.fromEntries(new FormData(event.target)); data.observed_at_utc = new Date(data.observed_at_utc + '+03:00').toISOString(); data.user_verified = $('account-verified').checked; await api('/api/account-snapshot', data); tell('Snapshot recorded. Reconcile again after any confirmed fill.'); await refresh(); } catch (error) { tell(error.message); }
};
$('refresh').onclick = refresh;
$('scan').onclick = async () => { tell('Running research scan…'); try { const result = await api('/api/scan', {}); tell(`Scan: ${result.status}`); await refresh(); } catch (error) { tell(error.message); } };
$('test').onclick = async () => { try { const result = await api('/api/notifications/test', {}); tell(`Test notification: ${result.state.replaceAll('_', ' ')}. Read receipt unavailable.`); } catch (error) { tell(error.message); } };
$('logout').onclick = async () => { await api('/api/logout', {}); location.reload(); };
api('/api/session').then(data => { csrf = data.csrf; refresh(); }).catch(() => {});

$('opening').onsubmit = async event => { event.preventDefault(); try { const data = Object.fromEntries(new FormData(event.target)); data.as_of_utc = new Date(data.as_of_utc + '+03:00').toISOString(); data.broker_reconciled = $('opening-verified').checked; await api('/api/opening-holdings', data); tell('Review the current holding and confirm it below.'); await refresh(); } catch (error) { tell(error.message); } };

$('basis').onsubmit = async event => { event.preventDefault(); try { const data = Object.fromEntries(new FormData(event.target)); data.verified_cost_basis = $('basis-verified').checked; await api('/api/opening-basis', data); tell('Verified historical basis recorded with an audit trail.'); await refresh(); } catch (error) { tell(error.message); } };
