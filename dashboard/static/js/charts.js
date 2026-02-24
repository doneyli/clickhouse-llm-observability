/**
 * Chart.js factory functions for LLM Observatory.
 */

function destroyChart(chartRef) {
  if (chartRef) chartRef.destroy();
  return null;
}

/** Daily activity bar chart (time series of daily trace counts) */
function createDailyBarChart(ctx, heatmapDays) {
  var days = (heatmapDays || []).slice();
  days.sort(function(a, b) { return a.date.localeCompare(b.date); });

  var labels = days.map(function(d) {
    return new Date(d.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  });
  var counts = days.map(function(d) { return d.count; });

  return new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        data: counts,
        backgroundColor: 'rgba(99, 102, 241, 0.5)',
        borderRadius: 3,
        barPercentage: 0.8,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: '#f0f0f0' } },
        x: { grid: { display: false }, ticks: { maxTicksLimit: 10, font: { size: 11 } } }
      }
    }
  });
}

/** Cost-by-project doughnut chart */
function createCostChart(ctx, labels, values) {
  var colors = ['#6366f1', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#06b6d4', '#84cc16'];
  return new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: labels,
      datasets: [{ data: values, backgroundColor: colors.slice(0, labels.length) }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom', labels: { font: { size: 11 } } } }
    }
  });
}

/** Score distribution bar chart */
function createScoreChart(ctx, scores) {
  var labels = scores.map(function(s) { return s.name; });
  var avgs = scores.map(function(s) { return s.avg; });
  var colors = ['#6366f1', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'];

  return new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        data: avgs,
        backgroundColor: labels.map(function(_, i) { return colors[i % colors.length] + '99'; }),
        borderColor: labels.map(function(_, i) { return colors[i % colors.length]; }),
        borderWidth: 1,
        borderRadius: 4,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, max: 1.0, grid: { color: '#f0f0f0' } },
        x: { grid: { display: false } }
      }
    }
  });
}

/** Token usage trend line chart */
function createTokenChart(ctx, sessions) {
  var sorted = sessions.filter(function(s) { return s.first_trace && s.total_tokens > 0; })
    .sort(function(a, b) { return (a.first_trace || '').localeCompare(b.first_trace || ''); });

  if (sorted.length === 0) return null;

  var labels = sorted.map(function(s) {
    return new Date(s.first_trace).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  });
  var tokens = sorted.map(function(s) { return s.total_tokens || 0; });

  return new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [{
        data: tokens,
        borderColor: '#6366f1',
        backgroundColor: 'rgba(99, 102, 241, 0.1)',
        fill: true,
        tension: 0.3,
        pointRadius: 2,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, grid: { color: '#f0f0f0' } },
        x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } }
      }
    }
  });
}
