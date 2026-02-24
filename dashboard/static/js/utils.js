/**
 * Utility functions for LLM Observatory dashboard.
 */

/** Format a number with comma separators */
function fmtNum(n) {
  if (n == null) return '—';
  return n.toLocaleString();
}

/** Format cost in dollars */
function fmtCost(n) {
  if (n == null || n === 0) return '$0.00';
  if (n < 0.01) return '$' + n.toFixed(4);
  return '$' + n.toFixed(2);
}

/** Format duration in ms to human readable */
function fmtDuration(ms) {
  if (ms == null) return '—';
  if (ms < 1000) return Math.round(ms) + 'ms';
  if (ms < 60000) return (ms / 1000).toFixed(1) + 's';
  return (ms / 60000).toFixed(1) + 'm';
}

/** Format ISO timestamp to relative time (e.g. "2h ago") */
function fmtRelative(isoStr) {
  if (!isoStr) return '—';
  const d = new Date(isoStr);
  const now = new Date();
  const diffMs = now - d;
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return mins + 'm ago';
  const hours = Math.floor(mins / 60);
  if (hours < 24) return hours + 'h ago';
  const days = Math.floor(hours / 24);
  if (days < 30) return days + 'd ago';
  return d.toLocaleDateString();
}

/** Format ISO timestamp to short date */
function fmtDate(isoStr) {
  if (!isoStr) return '—';
  return new Date(isoStr).toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
  });
}

/** Get ISO string for N days ago */
function daysAgo(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString();
}

/** Build query string from filter params */
function buildQuery(params) {
  const p = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v != null && v !== '' && v !== 'all') p.set(k, v);
  });
  return p.toString();
}

/** Trigger CSV download */
function downloadCSV(params) {
  const qs = buildQuery(params);
  window.location.href = '/api/export/csv' + (qs ? '?' + qs : '');
}
