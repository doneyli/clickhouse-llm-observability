/**
 * Chart.js factory functions for LLM Observatory.
 */

/** Destroy chart if it exists */
function destroyChart(chartRef) {
  if (chartRef) chartRef.destroy();
  return null;
}

/** Create activity-by-day bar chart */
function createDayBarChart(ctx, dayTotals) {
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  return new Chart(ctx, {
    type: 'bar',
    data: {
      labels: days,
      datasets: [{
        data: dayTotals,
        backgroundColor: 'rgba(59, 130, 246, 0.6)',
        borderRadius: 4,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, ticks: { precision: 0 } },
        x: { grid: { display: false } }
      }
    }
  });
}

/** Create cost-by-project doughnut chart */
function createCostChart(ctx, labels, values) {
  const colors = [
    '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
    '#ec4899', '#06b6d4', '#84cc16'
  ];
  return new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: labels,
      datasets: [{
        data: values,
        backgroundColor: colors.slice(0, labels.length),
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { font: { size: 11 } } }
      }
    }
  });
}

/** Create score distribution bar chart */
function createScoreChart(ctx, scores) {
  const labels = scores.map(s => s.name);
  const avgs = scores.map(s => s.avg);
  const colors = labels.map((_, i) => {
    const palette = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'];
    return palette[i % palette.length];
  });

  return new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        label: 'Average Score',
        data: avgs,
        backgroundColor: colors.map(c => c + '99'),
        borderColor: colors,
        borderWidth: 1,
        borderRadius: 4,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, max: 1.0 },
        x: { grid: { display: false } }
      }
    }
  });
}

/** Create token usage trend line chart */
function createTokenChart(ctx, sessions) {
  // Show token usage per session (ordered by time)
  const sorted = [...sessions]
    .filter(s => s.first_trace)
    .sort((a, b) => (a.first_trace || '').localeCompare(b.first_trace || ''));

  const labels = sorted.map(s => {
    if (!s.first_trace) return '';
    return new Date(s.first_trace).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  });
  const tokens = sorted.map(s => s.total_tokens || 0);

  return new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [{
        label: 'Tokens',
        data: tokens,
        borderColor: '#3b82f6',
        backgroundColor: 'rgba(59, 130, 246, 0.1)',
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
        y: { beginAtZero: true },
        x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } }
      }
    }
  });
}
