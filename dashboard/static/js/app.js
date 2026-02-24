/**
 * LLM Observatory — Alpine.js application.
 */

function observatory() {
  return {
    // State
    loading: true,
    error: null,
    timeRange: '30d',
    project: 'all',
    search: '',
    searchTimeout: null,

    // Data
    kpi: {},
    sessions: [],
    sessionsTotal: 0,
    sessionsPage: 1,
    projects: [],
    heatmap: { days: [], max_count: 0 },
    hourly: { matrix: [], day_totals: [], max_count: 0 },
    topSessions: [],
    topSort: 'traces',
    tools: [],
    scores: [],
    selectedSession: null,
    sessionDetail: null,

    // Charts
    _dayChart: null,
    _costChart: null,
    _scoreChart: null,
    _tokenChart: null,

    // Computed filters
    get filterParams() {
      const p = {};
      if (this.timeRange !== 'all') {
        const days = { '7d': 7, '30d': 30, '90d': 90, '1y': 365 }[this.timeRange];
        if (days) p.from = daysAgo(days);
      }
      if (this.project && this.project !== 'all') p.project = this.project;
      return p;
    },

    // Init
    async init() {
      await this.fetchAll();
    },

    // Fetch all data
    async fetchAll() {
      this.loading = true;
      this.error = null;
      try {
        const qs = buildQuery(this.filterParams);
        const q = qs ? '?' + qs : '';

        const [kpiRes, sessRes, projRes, heatRes, hourRes, topRes, toolRes, scoreRes] =
          await Promise.all([
            fetch('/api/kpi' + q).then(r => r.json()),
            fetch('/api/sessions' + q + (q ? '&' : '?') + 'limit=200' +
              (this.search ? '&search=' + encodeURIComponent(this.search) : '')
            ).then(r => r.json()),
            fetch('/api/projects' + q).then(r => r.json()),
            fetch('/api/activity/heatmap' + q).then(r => r.json()),
            fetch('/api/activity/by-hour' + q).then(r => r.json()),
            fetch('/api/top-sessions' + q + (q ? '&' : '?') + 'sort=' + this.topSort).then(r => r.json()),
            fetch('/api/tools' + q).then(r => r.json()),
            fetch('/api/scores').then(r => r.json()),
          ]);

        this.kpi = kpiRes;
        this.sessions = sessRes.sessions || [];
        this.sessionsTotal = sessRes.total || 0;
        this.projects = projRes.projects || [];
        this.heatmap = heatRes;
        this.hourly = hourRes;
        this.topSessions = topRes.sessions || [];
        this.tools = toolRes.tools || [];
        this.scores = scoreRes.scores || [];

        this.loading = false;

        // Render charts after DOM update
        this.$nextTick(() => {
          this.renderHeatmap();
          this.renderHourGrid();
          this.renderCharts();
        });
      } catch (e) {
        this.error = 'Failed to load data: ' + e.message;
        this.loading = false;
      }
    },

    // Time range change
    setTimeRange(range) {
      this.timeRange = range;
      this.selectedSession = null;
      this.sessionDetail = null;
      this.fetchAll();
    },

    // Project filter change
    setProject(proj) {
      this.project = proj;
      this.selectedSession = null;
      this.sessionDetail = null;
      this.fetchAll();
    },

    // Search
    onSearch() {
      clearTimeout(this.searchTimeout);
      this.searchTimeout = setTimeout(() => this.fetchAll(), 300);
    },

    // Top sessions sort
    setTopSort(sort) {
      this.topSort = sort;
      const qs = buildQuery(this.filterParams);
      const q = qs ? '?' + qs + '&' : '?';
      fetch('/api/top-sessions' + q + 'sort=' + sort)
        .then(r => r.json())
        .then(data => { this.topSessions = data.sessions || []; });
    },

    // Session click
    async selectSession(sessionId) {
      if (this.selectedSession === sessionId) {
        this.selectedSession = null;
        this.sessionDetail = null;
        return;
      }
      this.selectedSession = sessionId;
      this.sessionDetail = null;
      try {
        const data = await fetch('/api/sessions/' + encodeURIComponent(sessionId)).then(r => r.json());
        this.sessionDetail = data;
      } catch (e) {
        this.sessionDetail = { error: e.message };
      }
    },

    // Export
    exportCSV() {
      downloadCSV(this.filterParams);
    },

    // Render GitHub-style heatmap
    renderHeatmap() {
      const container = document.getElementById('heatmap');
      if (!container) return;
      container.innerHTML = '';

      const days = this.heatmap.days || [];
      const maxCount = this.heatmap.max_count || 1;

      if (days.length === 0) {
        container.innerHTML = '<div class="empty-state">No activity data</div>';
        return;
      }

      // Build date->count map
      const countMap = {};
      days.forEach(d => { countMap[d.date] = d.count; });

      // Find range
      const allDates = days.map(d => new Date(d.date));
      const minDate = new Date(Math.min(...allDates));
      const maxDate = new Date(Math.max(...allDates));

      // Extend to full weeks
      const startDate = new Date(minDate);
      startDate.setDate(startDate.getDate() - startDate.getDay()); // Start on Sunday
      const endDate = new Date(maxDate);
      endDate.setDate(endDate.getDate() + (6 - endDate.getDay())); // End on Saturday

      // Build weeks
      const heatmapEl = document.createElement('div');
      heatmapEl.className = 'heatmap';

      // Months header
      const monthsEl = document.createElement('div');
      monthsEl.className = 'heatmap-months';

      let current = new Date(startDate);
      let weekEl = null;
      let lastMonth = -1;
      let weekCount = 0;

      while (current <= endDate) {
        const dayOfWeek = current.getDay();

        if (dayOfWeek === 0) {
          weekEl = document.createElement('div');
          weekEl.className = 'heatmap-week';
          heatmapEl.appendChild(weekEl);
          weekCount++;

          // Track month labels
          const month = current.getMonth();
          if (month !== lastMonth) {
            const label = document.createElement('span');
            label.className = 'heatmap-month';
            label.textContent = current.toLocaleDateString('en-US', { month: 'short' });
            label.style.marginLeft = ((weekCount - 1) * 16) + 'px';
            label.style.position = 'absolute';
            monthsEl.appendChild(label);
            lastMonth = month;
          }
        }

        const dateStr = current.toISOString().slice(0, 10);
        const count = countMap[dateStr] || 0;
        const level = count === 0 ? 0 :
          count <= maxCount * 0.25 ? 1 :
          count <= maxCount * 0.5 ? 2 :
          count <= maxCount * 0.75 ? 3 : 4;

        const cell = document.createElement('div');
        cell.className = 'heatmap-cell';
        cell.setAttribute('data-level', level);
        cell.title = dateStr + ': ' + count + ' traces';

        if (weekEl) weekEl.appendChild(cell);

        current.setDate(current.getDate() + 1);
      }

      monthsEl.style.position = 'relative';
      monthsEl.style.height = '16px';
      container.appendChild(monthsEl);
      container.appendChild(heatmapEl);

      // Legend
      const legend = document.createElement('div');
      legend.className = 'heatmap-legend';
      legend.innerHTML = 'Less ';
      for (let i = 0; i <= 4; i++) {
        legend.innerHTML += '<div class="heatmap-cell" data-level="' + i + '"></div>';
      }
      legend.innerHTML += ' More';
      container.appendChild(legend);
    },

    // Render hour x day grid
    renderHourGrid() {
      const container = document.getElementById('hour-grid');
      if (!container) return;
      container.innerHTML = '';

      const matrix = this.hourly.matrix || [];
      const maxCount = this.hourly.max_count || 1;
      const dayNames = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

      if (matrix.length === 0) {
        container.innerHTML = '<div class="empty-state">No activity data</div>';
        return;
      }

      const grid = document.createElement('div');
      grid.className = 'hour-grid';

      // Header row
      grid.innerHTML += '<div></div>';
      for (let h = 0; h < 24; h++) {
        if (h % 3 === 0) {
          grid.innerHTML += '<div class="hour-grid-header">' + h + '</div>';
        } else {
          grid.innerHTML += '<div></div>';
        }
      }

      // Data rows
      for (let d = 0; d < 7; d++) {
        grid.innerHTML += '<div class="hour-grid-label">' + dayNames[d] + '</div>';
        for (let h = 0; h < 24; h++) {
          const count = (matrix[d] && matrix[d][h]) || 0;
          const level = count === 0 ? 0 :
            count <= maxCount * 0.25 ? 1 :
            count <= maxCount * 0.5 ? 2 :
            count <= maxCount * 0.75 ? 3 : 4;
          grid.innerHTML += '<div class="hour-grid-cell heatmap-cell" data-level="' + level +
            '" title="' + dayNames[d] + ' ' + h + ':00 — ' + count + ' traces"></div>';
        }
      }

      container.appendChild(grid);
    },

    // Render Chart.js charts
    renderCharts() {
      // Day totals bar chart
      const dayCtx = document.getElementById('day-chart');
      if (dayCtx && this.hourly.day_totals) {
        this._dayChart = destroyChart(this._dayChart);
        this._dayChart = createDayBarChart(dayCtx.getContext('2d'), this.hourly.day_totals);
      }

      // Cost by project doughnut
      const costCtx = document.getElementById('cost-chart');
      if (costCtx && this.sessions.length > 0) {
        this._costChart = destroyChart(this._costChart);
        const projectCosts = {};
        this.sessions.forEach(s => {
          const p = s.project || 'unknown';
          projectCosts[p] = (projectCosts[p] || 0) + (s.total_cost || 0);
        });
        const labels = Object.keys(projectCosts);
        const values = Object.values(projectCosts);
        if (labels.length > 0) {
          this._costChart = createCostChart(costCtx.getContext('2d'), labels, values);
        }
      }

      // Score distribution chart
      const scoreCtx = document.getElementById('score-chart');
      if (scoreCtx && this.scores.length > 0) {
        this._scoreChart = destroyChart(this._scoreChart);
        this._scoreChart = createScoreChart(scoreCtx.getContext('2d'), this.scores);
      }

      // Token trend chart
      const tokenCtx = document.getElementById('token-chart');
      if (tokenCtx && this.sessions.length > 0) {
        this._tokenChart = destroyChart(this._tokenChart);
        this._tokenChart = createTokenChart(tokenCtx.getContext('2d'), this.sessions);
      }
    },

    // Helper: max tool count for bar width
    maxToolCount() {
      return Math.max(...this.tools.map(t => t.count), 1);
    },
  };
}
