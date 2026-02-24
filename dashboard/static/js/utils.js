/**
 * Utility functions for LLM Observatory dashboard.
 */

function fmtNum(n) {
  if (n == null) return '—';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return n.toLocaleString();
}

function fmtCost(n) {
  if (n == null || n === 0) return '$0.00';
  if (n < 0.01) return '$' + n.toFixed(4);
  return '$' + n.toFixed(2);
}

function fmtDuration(ms) {
  if (ms == null || ms === 0) return '0ms';
  if (ms < 1000) return Math.round(ms) + 'ms';
  if (ms < 60000) return (ms / 1000).toFixed(1) + 's';
  return (ms / 60000).toFixed(1) + 'm';
}

function fmtRelative(isoStr) {
  if (!isoStr) return '';
  var d = new Date(isoStr);
  var now = new Date();
  var mins = Math.floor((now - d) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return mins + 'm ago';
  var hours = Math.floor(mins / 60);
  if (hours < 24) return hours + 'h ago';
  var days = Math.floor(hours / 24);
  if (days < 30) return days + 'd ago';
  return d.toLocaleDateString();
}

function fmtDate(isoStr) {
  if (!isoStr) return '';
  return new Date(isoStr).toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
  });
}

function fmtShortDate(isoStr) {
  if (!isoStr) return '';
  return new Date(isoStr).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function daysAgo(n) {
  var d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString();
}

function buildQuery(params) {
  var p = new URLSearchParams();
  Object.entries(params).forEach(function(e) {
    if (e[1] != null && e[1] !== '' && e[1] !== 'all') p.set(e[0], e[1]);
  });
  return p.toString();
}

function downloadCSV(params) {
  var qs = buildQuery(params);
  window.location.href = '/api/export/csv' + (qs ? '?' + qs : '');
}

/** Truncate text to N chars */
function truncate(str, n) {
  if (!str) return '';
  str = String(str);
  return str.length > n ? str.slice(0, n) + '...' : str;
}

/** Extract a readable title from trace input */
function traceTitle(trace) {
  if (!trace) return 'Untitled';
  // Try input first
  var input = trace.input;
  if (input) {
    if (typeof input === 'string') return truncate(input, 80);
    if (typeof input === 'object') {
      // common patterns
      if (input.query) return truncate(input.query, 80);
      if (input.question) return truncate(input.question, 80);
      if (input.prompt) return truncate(input.prompt, 80);
      if (input.messages && input.messages.length > 0) {
        var last = input.messages[input.messages.length - 1];
        return truncate(last.content || JSON.stringify(last), 80);
      }
      return truncate(JSON.stringify(input), 80);
    }
  }
  return trace.name || 'Untitled';
}

/** Highlight search term in text */
function highlightText(text, term) {
  if (!term || !text) return escapeHtml(text || '');
  var escaped = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  var re = new RegExp('(' + escaped + ')', 'gi');
  return escapeHtml(text).replace(
    new RegExp('(' + escaped + ')', 'gi'),
    '<span class="search-highlight">$1</span>'
  );
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
