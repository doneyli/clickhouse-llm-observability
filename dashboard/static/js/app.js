/**
 * LLM Observatory — Alpine.js application.
 */

function observatory() {
  return {
    // State
    loading: true,
    error: null,
    timeRange: 'all',
    project: 'all',
    view: 'dashboard', // 'dashboard' or 'session'

    // Search
    searchOpen: false,
    searchQuery: '',
    searchResults: [],

    // Data
    kpi: {},
    sessions: [],
    sessionsTotal: 0,
    projects: [],
    heatmap: { days: [], max_count: 0 },
    hourly: { matrix: [], day_totals: [], max_count: 0 },
    topSessions: [],
    topSort: 'traces',
    tools: [],
    scores: [],

    // Session detail
    selectedSessionId: null,
    sessionDetail: null,

    // Charts
    _dayChart: null,
    _costChart: null,
    _scoreChart: null,
    _tokenChart: null,

    get filterParams() {
      var p = {};
      if (this.timeRange !== 'all') {
        var days = { '7d': 7, '30d': 30, '90d': 90, '1y': 365 }[this.timeRange];
        if (days) p.from = daysAgo(days);
      }
      if (this.project && this.project !== 'all') p.project = this.project;
      return p;
    },

    async init() {
      // Keyboard shortcut: Ctrl+K for search
      document.addEventListener('keydown', (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
          e.preventDefault();
          this.openSearch();
        }
        if (e.key === 'Escape' && this.searchOpen) {
          this.closeSearch();
        }
      });
      await this.fetchAll();
    },

    async fetchAll() {
      this.loading = true;
      this.error = null;
      try {
        var qs = buildQuery(this.filterParams);
        var q = qs ? '?' + qs : '';

        var results = await Promise.all([
          fetch('/api/kpi' + q).then(function(r) { return r.json(); }),
          fetch('/api/sessions' + q + (q ? '&' : '?') + 'limit=200').then(function(r) { return r.json(); }),
          fetch('/api/projects' + q).then(function(r) { return r.json(); }),
          fetch('/api/activity/heatmap' + q).then(function(r) { return r.json(); }),
          fetch('/api/activity/by-hour' + q).then(function(r) { return r.json(); }),
          fetch('/api/top-sessions' + q + (q ? '&' : '?') + 'sort=' + this.topSort).then(function(r) { return r.json(); }),
          fetch('/api/tools' + q).then(function(r) { return r.json(); }),
          fetch('/api/scores').then(function(r) { return r.json(); }),
        ]);

        this.kpi = results[0];
        this.sessions = results[1].sessions || [];
        this.sessionsTotal = results[1].total || 0;
        this.projects = results[2].projects || [];
        this.heatmap = results[3];
        this.hourly = results[4];
        this.topSessions = results[5].sessions || [];
        this.tools = results[6].tools || [];
        this.scores = results[7].scores || [];
        this.loading = false;

        this.$nextTick(() => {
          if (this.view === 'dashboard') {
            this.renderHeatmap();
            this.renderHourGrid();
            this.renderCharts();
          }
        });
      } catch (e) {
        this.error = 'Failed to load data: ' + e.message;
        this.loading = false;
      }
    },

    setTimeRange(range) {
      this.timeRange = range;
      this.view = 'dashboard';
      this.selectedSessionId = null;
      this.sessionDetail = null;
      this.fetchAll();
    },

    setProject(proj) {
      this.project = proj;
      this.view = 'dashboard';
      this.selectedSessionId = null;
      this.sessionDetail = null;
      this.fetchAll();
    },

    setTopSort(sort) {
      this.topSort = sort;
      var qs = buildQuery(this.filterParams);
      var q = qs ? '?' + qs + '&' : '?';
      fetch('/api/top-sessions' + q + 'sort=' + sort)
        .then(function(r) { return r.json(); })
        .then((data) => { this.topSessions = data.sessions || []; });
    },

    // Session selection
    async selectSession(sessionId) {
      this.selectedSessionId = sessionId;
      this.sessionDetail = null;
      this.view = 'session';
      try {
        var data = await fetch('/api/sessions/' + encodeURIComponent(sessionId)).then(function(r) { return r.json(); });
        this.sessionDetail = data;
      } catch (e) {
        this.sessionDetail = { error: e.message, traces: [] };
      }
    },

    backToDashboard() {
      this.view = 'dashboard';
      this.selectedSessionId = null;
      this.sessionDetail = null;
      this.$nextTick(() => {
        this.renderHeatmap();
        this.renderHourGrid();
        this.renderCharts();
      });
    },

    // Search
    openSearch() {
      this.searchOpen = true;
      this.searchQuery = '';
      this.searchResults = [];
      this.$nextTick(() => {
        var input = document.getElementById('search-input');
        if (input) input.focus();
      });
    },

    closeSearch() {
      this.searchOpen = false;
      this.searchQuery = '';
      this.searchResults = [];
    },

    async onSearchInput() {
      var q = this.searchQuery.trim();
      if (q.length < 2) {
        this.searchResults = [];
        return;
      }
      try {
        var qs = buildQuery(this.filterParams);
        var url = '/api/sessions' + (qs ? '?' + qs + '&' : '?') + 'limit=20&search=' + encodeURIComponent(q);
        var data = await fetch(url).then(function(r) { return r.json(); });
        this.searchResults = (data.sessions || []).map((s) => {
          return { id: s.id, project: s.project, title: s.id, trace_count: s.trace_count };
        });
      } catch (e) {
        this.searchResults = [];
      }
    },

    searchSelect(sessionId) {
      this.closeSearch();
      this.selectSession(sessionId);
    },

    exportCSV() {
      downloadCSV(this.filterParams);
    },

    // Get a display title for a session (use session ID or first trace info)
    sessionTitle(session) {
      if (!session) return 'Untitled';
      return session.id;
    },

    // Render heatmap
    renderHeatmap() {
      var container = document.getElementById('heatmap');
      if (!container) return;
      container.innerHTML = '';

      var days = this.heatmap.days || [];
      var maxCount = this.heatmap.max_count || 1;

      if (days.length === 0) {
        container.innerHTML = '<div class="empty-state">No activity data</div>';
        return;
      }

      var countMap = {};
      days.forEach(function(d) { countMap[d.date] = d.count; });

      // Determine range: always show at least the selected time range
      var allDates = days.map(function(d) { return new Date(d.date + 'T00:00:00'); });
      var maxDate = new Date(Math.max.apply(null, allDates));
      var minDate;

      if (this.timeRange === 'all' || this.timeRange === '1y') {
        minDate = new Date(maxDate);
        minDate.setFullYear(minDate.getFullYear() - 1);
      } else {
        minDate = new Date(Math.min.apply(null, allDates));
        // Ensure at least 12 weeks
        var twelveWeeks = new Date(maxDate);
        twelveWeeks.setDate(twelveWeeks.getDate() - 84);
        if (minDate > twelveWeeks) minDate = twelveWeeks;
      }

      // Align to week boundaries (Sunday start)
      var startDate = new Date(minDate);
      startDate.setDate(startDate.getDate() - startDate.getDay());
      var endDate = new Date(maxDate);
      endDate.setDate(endDate.getDate() + (6 - endDate.getDay()));

      // Build structure
      var wrapper = document.createElement('div');
      wrapper.className = 'heatmap-wrapper';

      // Day labels (Mon, Wed, Fri)
      var dayLabels = document.createElement('div');
      dayLabels.className = 'heatmap-day-labels';
      var dayNames = ['', 'Mon', '', 'Wed', '', 'Fri', ''];
      for (var i = 0; i < 7; i++) {
        var lbl = document.createElement('div');
        lbl.className = 'heatmap-day-label';
        lbl.textContent = dayNames[i];
        dayLabels.appendChild(lbl);
      }
      wrapper.appendChild(dayLabels);

      // Grid area
      var gridArea = document.createElement('div');
      gridArea.className = 'heatmap-grid-area';

      // Month labels
      var monthsEl = document.createElement('div');
      monthsEl.className = 'heatmap-months';

      var heatmapEl = document.createElement('div');
      heatmapEl.className = 'heatmap';

      var current = new Date(startDate);
      var lastMonth = -1;
      var weekCount = 0;
      var weekEl = null;
      var cellWidth = 16; // 13px cell + 3px gap

      while (current <= endDate) {
        var dayOfWeek = current.getDay();

        if (dayOfWeek === 0) {
          weekEl = document.createElement('div');
          weekEl.className = 'heatmap-week';
          heatmapEl.appendChild(weekEl);

          var month = current.getMonth();
          if (month !== lastMonth) {
            var monthLabel = document.createElement('span');
            monthLabel.className = 'heatmap-month';
            monthLabel.textContent = current.toLocaleDateString('en-US', { month: 'short' });
            monthLabel.style.left = (weekCount * cellWidth) + 'px';
            monthsEl.appendChild(monthLabel);
            lastMonth = month;
          }
          weekCount++;
        }

        var y = current.getFullYear();
        var m = String(current.getMonth() + 1).padStart(2, '0');
        var d = String(current.getDate()).padStart(2, '0');
        var dateStr = y + '-' + m + '-' + d;
        var count = countMap[dateStr] || 0;
        var level = count === 0 ? 0 :
          count <= maxCount * 0.25 ? 1 :
          count <= maxCount * 0.5 ? 2 :
          count <= maxCount * 0.75 ? 3 : 4;

        var cell = document.createElement('div');
        cell.className = 'heatmap-cell';
        cell.setAttribute('data-level', level);
        cell.title = dateStr + ': ' + count + ' traces';

        if (weekEl) weekEl.appendChild(cell);
        current.setDate(current.getDate() + 1);
      }

      gridArea.appendChild(monthsEl);
      gridArea.appendChild(heatmapEl);
      wrapper.appendChild(gridArea);
      container.appendChild(wrapper);

      // Legend
      var legend = document.createElement('div');
      legend.className = 'heatmap-legend';
      legend.innerHTML = 'Less ';
      for (var l = 0; l <= 4; l++) {
        legend.innerHTML += '<div class="heatmap-cell" data-level="' + l + '"></div>';
      }
      legend.innerHTML += ' More';
      container.appendChild(legend);
    },

    // Hour grid
    renderHourGrid() {
      var container = document.getElementById('hour-grid');
      if (!container) return;
      container.innerHTML = '';

      var matrix = this.hourly.matrix || [];
      var maxCount = this.hourly.max_count || 1;
      var dayNames = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

      if (matrix.length === 0) {
        container.innerHTML = '<div class="empty-state">No activity data</div>';
        return;
      }

      var grid = document.createElement('div');
      grid.className = 'hour-grid';

      // Header row
      grid.innerHTML += '<div></div>';
      for (var h = 0; h < 24; h++) {
        if (h % 3 === 0) {
          grid.innerHTML += '<div class="hour-grid-header">' + h + '</div>';
        } else {
          grid.innerHTML += '<div></div>';
        }
      }

      // Data rows
      for (var d = 0; d < 7; d++) {
        grid.innerHTML += '<div class="hour-grid-label">' + dayNames[d] + '</div>';
        for (var h = 0; h < 24; h++) {
          var count = (matrix[d] && matrix[d][h]) || 0;
          var level = count === 0 ? 0 :
            count <= maxCount * 0.25 ? 1 :
            count <= maxCount * 0.5 ? 2 :
            count <= maxCount * 0.75 ? 3 : 4;
          grid.innerHTML += '<div class="hour-grid-cell heatmap-cell" data-level="' + level +
            '" title="' + dayNames[d] + ' ' + h + ':00 — ' + count + ' traces"></div>';
        }
      }

      container.appendChild(grid);
    },

    // Charts
    renderCharts() {
      // Daily activity bar chart (time-series, not day-of-week)
      var dayCtx = document.getElementById('day-chart');
      if (dayCtx && this.heatmap.days && this.heatmap.days.length > 0) {
        this._dayChart = destroyChart(this._dayChart);
        this._dayChart = createDailyBarChart(dayCtx.getContext('2d'), this.heatmap.days);
      }

      // Cost by project
      var costCtx = document.getElementById('cost-chart');
      if (costCtx) {
        this._costChart = destroyChart(this._costChart);
        var projectCosts = {};
        var hasCost = false;
        this.sessions.forEach(function(s) {
          if (s.total_cost > 0) {
            hasCost = true;
            var p = s.project || 'unknown';
            projectCosts[p] = (projectCosts[p] || 0) + s.total_cost;
          }
        });
        if (hasCost) {
          this._costChart = createCostChart(costCtx.getContext('2d'), Object.keys(projectCosts), Object.values(projectCosts));
        }
      }

      // Score chart
      var scoreCtx = document.getElementById('score-chart');
      if (scoreCtx && this.scores.length > 0) {
        this._scoreChart = destroyChart(this._scoreChart);
        this._scoreChart = createScoreChart(scoreCtx.getContext('2d'), this.scores);
      }

      // Token trend
      var tokenCtx = document.getElementById('token-chart');
      if (tokenCtx) {
        this._tokenChart = destroyChart(this._tokenChart);
        var hasTokens = this.sessions.some(function(s) { return s.total_tokens > 0; });
        if (hasTokens) {
          this._tokenChart = createTokenChart(tokenCtx.getContext('2d'), this.sessions);
        }
      }
    },

    maxToolCount() {
      return Math.max.apply(null, this.tools.map(function(t) { return t.count; }).concat([1]));
    },

    // Metric for top sessions display
    topMetricValue(session) {
      if (this.topSort === 'cost') return fmtCost(session.total_cost);
      if (this.topSort === 'duration') return fmtDuration(session.total_duration_ms);
      return session.trace_count;
    },
  };
}
