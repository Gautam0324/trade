// Institutional Algo Trading Terminal Client Application
let equityChartInstance = null;
let activeView = "overview";
let cachedDashboardData = null;
let currentAccount = new URLSearchParams(window.location.search).get("account") || "gautam";
let dashboardApiToken = "";

function switchAccount(acc) {
  currentAccount = acc || "gautam";
  const url = new URL(window.location);
  url.searchParams.set("account", currentAccount);
  window.history.replaceState({}, "", url);

  const sel = document.getElementById("accountSelector");
  if (sel) sel.value = currentAccount;

  refreshCurrentView();
}

// Helper for authenticated POST requests
async function authFetch(url, options = {}) {
  options.headers = options.headers || {};
  if (dashboardApiToken) {
    options.headers['X-API-Token'] = dashboardApiToken;
  }
  return fetch(url, options);
}

// Clock with exact IST (Asia/Kolkata) formatting
function updateClock() {
  const now = new Date();
  const timeStr = now.toLocaleTimeString('en-GB', { timeZone: 'Asia/Kolkata', hour12: false });
  const clockEl = document.getElementById('istClockDisplay');
  if (clockEl) {
    clockEl.textContent = `${timeStr} IST`;
  }
}
setInterval(updateClock, 1000);
updateClock();

function formatINR(val) {
  if (val === undefined || val === null || isNaN(val)) return "₹0.00";
  const num = Number(val);
  const sign = num < 0 ? "-" : "";
  return `${sign}₹${Math.abs(num).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatSignedINR(val) {
  if (val === undefined || val === null || isNaN(val)) return "₹0.00";
  const num = Number(val);
  const sign = num > 0 ? "+" : (num < 0 ? "-" : "");
  return `${sign}₹${Math.abs(num).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

// Sidebar Navigation Router
function switchView(viewId) {
  activeView = viewId;
  document.querySelectorAll('.view-section').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));

  const targetSection = document.getElementById(`view-${viewId}`);
  if (targetSection) targetSection.classList.add('active');

  // Highlight Nav Item
  const navItems = document.querySelectorAll('.nav-item');
  const viewMap = ['overview', 'marketwatch', 'positions', 'trades', 'strategies', 'risk', 'control', 'logs', 'config', 'health'];
  const idx = viewMap.indexOf(viewId);
  if (idx !== -1 && navItems[idx]) {
    navItems[idx].classList.add('active');
  }

  // Update Page Title
  const titles = {
    overview: "Portfolio Overview",
    marketwatch: "Live Market Watch",
    positions: "Open Positions Management",
    trades: "Trade Execution Journal",
    strategies: "Strategy Performance Attribution",
    risk: "Risk Management Center",
    control: "Bot Process Control",
    logs: "Audit & System Event Logs",
    config: "System Configuration Viewer",
    health: "Subsystem Diagnostics"
  };
  const titleEl = document.getElementById('pageTitle');
  if (titleEl) titleEl.textContent = titles[viewId] || "Trading Terminal";

  refreshCurrentView();
}

// Router dispatcher for active view data fetch
async function refreshCurrentView() {
  switch (activeView) {
    case 'overview':
      await loadOverviewData();
      break;
    case 'marketwatch':
      await loadWatchlistData();
      break;
    case 'positions':
      await loadPositionsData();
      break;
    case 'trades':
      await loadTradesTable();
      break;
    case 'strategies':
      await loadStrategiesData();
      break;
    case 'risk':
      await loadRiskData();
      break;
    case 'control':
      await loadControlData();
      break;
    case 'logs':
      await loadLogsTerminal();
      break;
    case 'config':
      await loadConfigData();
      break;
    case 'health':
      break;
  }
}

// 1. Overview Loader
async function loadOverviewData() {
  try {
    const res = await fetch('/api/overview?account=' + encodeURIComponent(currentAccount));
    const data = await res.json();
    cachedDashboardData = data;
    if (data.api_token) {
      dashboardApiToken = data.api_token;
    }

    // Header Badges
    const mkt = data.market_status || "UNKNOWN";
    const mktBadge = document.getElementById('marketBadge');
    if (mktBadge) {
      mktBadge.textContent = `Market ${mkt}`;
      mktBadge.className = `status-badge ${mkt === 'OPEN' ? 'open' : 'closed'}`;
    }

    const modeBadge = document.getElementById('modeBadge');
    if (modeBadge && data.trading_mode) {
      modeBadge.textContent = `${data.trading_mode} MODE`;
      modeBadge.className = `status-badge ${data.trading_mode === 'LIVE' ? 'open' : 'paper'}`;
    }

    const botStatus = data.bot_status || {};
    const botBadge = document.getElementById('botStatusBadge');
    if (botBadge) {
      const isRunning = botStatus.status === "RUNNING";
      botBadge.textContent = isRunning ? `Bot Running (PID: ${botStatus.pid})` : "Bot Stopped";
      botBadge.className = `status-badge ${isRunning ? 'running' : 'stopped'}`;
    }

    // KPIs
    const kpis = data.kpis || {};
    document.getElementById('kpiStartingCap').textContent = formatINR(kpis.starting_capital);
    document.getElementById('kpiFreeCash').textContent = formatINR(kpis.free_cash);
    document.getElementById('kpiDeployedCash').textContent = formatINR(kpis.deployed_cash);
    document.getElementById('kpiOpenPositionsSub').textContent = `${kpis.open_positions_count || 0} Active Positions`;
    
    const rPnlEl = document.getElementById('kpiRealizedPnl');
    const todayPnlVal = kpis.today_realized_pnl !== undefined ? kpis.today_realized_pnl : kpis.realized_pnl;
    rPnlEl.textContent = formatSignedINR(todayPnlVal);
    rPnlEl.className = `kpi-value ${todayPnlVal >= 0 ? 'green' : 'red'}`;

    const rPnlSub = document.getElementById('kpiRealizedPnlSub');
    if (rPnlSub) {
      rPnlSub.textContent = `Cumulative: ${formatSignedINR(kpis.realized_pnl)}`;
    }

    const uPnlEl = document.getElementById('kpiUnrealizedPnl');
    uPnlEl.textContent = formatSignedINR(kpis.unrealized_pnl);
    uPnlEl.className = `kpi-value ${kpis.unrealized_pnl >= 0 ? (kpis.unrealized_pnl > 0 ? 'green' : '') : 'red'}`;

    document.getElementById('kpiNetPortfolio').textContent = formatINR(kpis.total_portfolio_value);
    const retEl = document.getElementById('kpiReturnPct');
    retEl.textContent = `${kpis.return_pct >= 0 ? '+' : ''}${kpis.return_pct}% Today`;
    retEl.className = `kpi-sub ${kpis.return_pct >= 0 ? 'green' : 'red'}`;

    document.getElementById('kpiWinRate').textContent = `${kpis.win_rate}%`;
    document.getElementById('kpiWinsLossesSub').textContent = `${kpis.wins_count} W / ${kpis.losses_count} L / ${kpis.breakeven_count} BE`;

    document.getElementById('kpiProfitFactor').textContent = kpis.profit_factor || "1.00";
    document.getElementById('kpiMaxDrawdown').textContent = formatINR(kpis.max_drawdown);
    
    document.getElementById('kpiLossUtil').textContent = `${kpis.daily_loss_utilization_pct}%`;
    document.getElementById('kpiMaxLossSub').textContent = `Limit: ${formatINR(kpis.max_daily_loss)}`;

    const tradeCountEl = document.getElementById('kpiTradeCount');
    const todayTradesVal = kpis.today_trades_count !== undefined ? kpis.today_trades_count : (kpis.total_trades_count || 0);
    tradeCountEl.textContent = `${todayTradesVal}`;

    // Render Equity Chart
    await renderEquityChart();

    // Quick previews
    renderQuickWatchlist();
    renderQuickPositions(kpis.enriched_positions || []);

  } catch (err) {
    console.error("Overview error:", err);
  }
}

// Equity Chart Renderer using Chart.js
async function renderEquityChart() {
  try {
    const res = await fetch('/api/equity_curve?account=' + encodeURIComponent(currentAccount));
    const data = await res.json();
    const points = data.points || [];

    const labels = points.map(p => p.time);
    const equities = points.map(p => p.equity);
    const cumulativePnls = points.map(p => p.cumulative_pnl);

    const ctx = document.getElementById('equityChart');
    if (!ctx) return;

    if (equityChartInstance) {
      equityChartInstance.destroy();
    }

    equityChartInstance = new Chart(ctx, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [
          {
            label: 'Net Portfolio Equity (₹)',
            data: equities,
            borderColor: '#10b981',
            backgroundColor: 'rgba(16, 185, 129, 0.08)',
            borderWidth: 2,
            tension: 0.2,
            fill: true,
            yAxisID: 'y'
          },
          {
            label: 'Cumulative P&L (₹)',
            data: cumulativePnls,
            borderColor: '#f59e0b',
            borderWidth: 1.5,
            borderDash: [4, 4],
            tension: 0.2,
            fill: false,
            yAxisID: 'y1'
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            position: 'top',
            labels: { color: '#94a3b8', font: { family: 'Inter', size: 11 } }
          },
          tooltip: {
            backgroundColor: '#0f172a',
            titleColor: '#f8fafc',
            bodyColor: '#94a3b8',
            borderColor: 'rgba(255, 255, 255, 0.1)',
            borderWidth: 1
          }
        },
        scales: {
          x: {
            grid: { color: 'rgba(255, 255, 255, 0.03)' },
            ticks: { color: '#64748b', font: { family: 'JetBrains Mono', size: 10 }, maxTicksLimit: 12 }
          },
          y: {
            position: 'left',
            grid: { color: 'rgba(255, 255, 255, 0.05)' },
            ticks: {
              color: '#10b981',
              font: { family: 'JetBrains Mono', size: 10 },
              callback: (val) => '₹' + val.toLocaleString('en-IN')
            }
          },
          y1: {
            position: 'right',
            grid: { drawOnChartArea: false },
            ticks: {
              color: '#f59e0b',
              font: { family: 'JetBrains Mono', size: 10 },
              callback: (val) => (val >= 0 ? '+' : '') + '₹' + val.toLocaleString('en-IN')
            }
          }
        }
      }
    });
  } catch (err) {
    console.error("Equity chart error:", err);
  }
}

function renderQuickWatchlist() {
  const el = document.getElementById('quickWatchlist');
  if (!el) return;
  fetch('/api/watchlist?account=' + encodeURIComponent(currentAccount)).then(r => r.json()).then(data => {
    const list = data.symbols || [];
    if (!list.length) {
      el.innerHTML = `<span style="color: var(--text-muted); font-size: 0.8rem;">No watchlist data available.</span>`;
      return;
    }
    let html = `<div style="display: flex; flex-direction: column; gap: 0.5rem;">`;
    list.slice(0, 4).forEach(q => {
      const isGreen = q.change >= 0;
      html += `
        <div style="display: flex; justify-content: space-between; align-items: center; padding: 0.4rem 0.5rem; background: rgba(0,0,0,0.2); border-radius: 6px;">
          <div>
            <strong style="font-size: 0.82rem; color: #fff;">${q.symbol}</strong>
            <span style="margin-left: 0.5rem; font-size: 0.7rem; color: var(--text-muted); font-family: var(--font-mono);">EMA5: ${q.ema5}</span>
          </div>
          <div style="text-align: right; font-family: var(--font-mono); font-size: 0.82rem;">
            <div>₹${q.price.toFixed(2)}</div>
            <div style="font-size: 0.7rem;" class="${isGreen ? 'green' : 'red'}">${isGreen ? '+' : ''}${q.change_pct}%</div>
          </div>
        </div>
      `;
    });
    html += `</div>`;
    el.innerHTML = html;
  }).catch(() => {
    el.innerHTML = `<span style="color: var(--red); font-size: 0.8rem;">Quote feed synchronizing...</span>`;
  });
}

function formatPositionSymbol(p) {
  const sym = p.symbol || '';
  const optType = p.option_type || (sym.endsWith('CE') ? 'CE' : (sym.endsWith('PE') ? 'PE' : null));
  if (optType) {
    const isCall = optType === 'CE';
    const badge = isCall 
      ? `<span style="background: rgba(16, 185, 129, 0.2); color: #10B981; border: 1px solid #10B981; padding: 2px 6px; border-radius: 4px; font-weight: 700; font-size: 0.72rem; margin-right: 6px;">🟢 CALL (CE)</span>`
      : `<span style="background: rgba(239, 68, 68, 0.2); color: #EF4444; border: 1px solid #EF4444; padding: 2px 6px; border-radius: 4px; font-weight: 700; font-size: 0.72rem; margin-right: 6px;">🔴 PUT (PE)</span>`;
    const strikeText = p.strike ? `${p.underlying || ''} ${p.strike} ${optType}` : sym;
    return `${badge}<span style="color: #fff; font-weight: 700;">${strikeText}</span> <span style="color: var(--text-muted); font-size: 0.72rem; margin-left: 4px;">(${sym})</span>`;
  }
  return `<span style="color: #fff; font-weight: 700;">${sym}</span>`;
}

function renderQuickPositions(positions) {
  const el = document.getElementById('quickPositions');
  if (!el) return;
  if (!positions.length) {
    el.innerHTML = `<div style="padding: 1rem; text-align: center; color: var(--text-muted); font-size: 0.8rem;">All positions closed. Capital 100% in free cash.</div>`;
    return;
  }
  let html = `<div style="display: flex; flex-direction: column; gap: 0.5rem;">`;
  positions.forEach(p => {
    const isGreen = (p.unrealized_pnl || 0) >= 0;
    const sym = p.symbol || '';
    const optType = p.option_type || (sym.endsWith('CE') ? 'CE' : (sym.endsWith('PE') ? 'PE' : null));
    let badge = '';
    let label = sym;
    if (optType) {
      const isCall = optType === 'CE';
      badge = isCall 
        ? `<span style="background: rgba(16, 185, 129, 0.2); color: #10B981; border: 1px solid #10B981; padding: 1px 6px; border-radius: 4px; font-weight: 700; font-size: 0.7rem; margin-right: 6px;">🟢 CALL (CE)</span>`
        : `<span style="background: rgba(239, 68, 68, 0.2); color: #EF4444; border: 1px solid #EF4444; padding: 1px 6px; border-radius: 4px; font-weight: 700; font-size: 0.7rem; margin-right: 6px;">🔴 PUT (PE)</span>`;
      label = p.strike ? `${p.underlying || ''} ${p.strike} ${optType}` : sym;
    }

    html += `
      <div style="display: flex; justify-content: space-between; align-items: center; padding: 0.5rem 0.6rem; background: rgba(0,0,0,0.25); border: 1px solid rgba(255,255,255,0.05); border-radius: 6px;">
        <div>
          <div style="display: flex; align-items: center; margin-bottom: 2px;">
            ${badge}
            <strong style="color: #fff; font-size: 0.84rem;">${label}</strong>
          </div>
          <div style="font-size: 0.72rem; color: var(--text-secondary);">
            Contract: <code style="color: #94a3b8; font-size: 0.7rem;">${p.symbol}</code> | Qty: <strong>${p.qty}</strong> @ ₹${Number(p.entry_price).toFixed(2)}
          </div>
        </div>
        <div style="text-align: right; font-family: var(--font-mono); font-size: 0.82rem;">
          <div class="${isGreen ? 'green' : 'red'}" style="font-weight: 600;">${isGreen ? '+' : ''}₹${Number(p.unrealized_pnl || 0).toFixed(2)} (${p.unrealized_pnl_pct || 0}%)</div>
          <div style="font-size: 0.7rem; color: var(--text-muted);">SL: ₹${Number(p.sl_price || p.current_stop || (p.entry_price * 0.85)).toFixed(2)} | Tgt: ₹${Number(p.target_price || (p.entry_price * 1.30)).toFixed(2)}</div>
        </div>
      </div>
    `;
  });
  html += `</div>`;
  el.innerHTML = html;
}

// 2. Watchlist Loader
async function loadWatchlistData() {
  const tbody = document.getElementById('watchlistTableBody');
  const tsEl = document.getElementById('watchlistTimestamp');
  try {
    const res = await fetch('/api/watchlist?account=' + encodeURIComponent(currentAccount));
    const data = await res.json();
    const symbols = data.symbols || [];
    if (tsEl) tsEl.textContent = `Last sync: ${new Date().toLocaleTimeString('en-GB', { timeZone: 'Asia/Kolkata' })} IST`;

    if (!symbols.length) {
      tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; color: var(--text-muted);">No symbols configured in watchlist.</td></tr>`;
      return;
    }

    let rows = "";
    symbols.forEach(q => {
      const isGreen = q.change >= 0;
      rows += `
        <tr>
          <td style="font-weight: 700; color: #fff;">${q.symbol}</td>
          <td>₹${q.price.toFixed(2)}</td>
          <td class="${isGreen ? 'green' : 'red'}">${isGreen ? '+' : ''}${q.change.toFixed(2)} (${q.change_pct}%)</td>
          <td>₹${q.ema5.toFixed(2)}</td>
          <td>₹${q.ema9.toFixed(2)}</td>
          <td>₹${q.ema33_high.toFixed(2)}</td>
          <td>₹${q.ema33_low.toFixed(2)}</td>
          <td><span class="status-badge ${q.signal === 'BUY' ? 'open' : 'closed'}">${q.signal}</span></td>
          <td>${q.last_updated}</td>
        </tr>
      `;
    });
    tbody.innerHTML = rows;
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; color: var(--red);">Error loading watchlist.</td></tr>`;
  }
}

// 3. Open Positions Loader
async function loadPositionsData() {
  const tbody = document.getElementById('positionsTableBody');
  const summaryEl = document.getElementById('positionsSummary');
  try {
    const res = await fetch('/api/positions?account=' + encodeURIComponent(currentAccount));
    const data = await res.json();
    const positions = data.open_positions || [];

    if (summaryEl) {
      summaryEl.textContent = `Deployed: ${formatINR(data.total_deployed_cash)} | Floating P&L: ${formatSignedINR(data.total_unrealized_pnl)}`;
    }

    if (!positions.length) {
      tbody.innerHTML = `<tr><td colspan="11" style="text-align: center; color: var(--text-muted); padding: 2rem;">No active open positions. Cash 100% free.</td></tr>`;
      return;
    }

    let rows = "";
    positions.forEach(p => {
      const isGreen = p.unrealized_pnl >= 0;
      rows += `
        <tr>
          <td>${p.id || 'POS'}</td>
          <td style="font-weight: 700; color: #fff;">${formatPositionSymbol(p)}</td>
          <td>${p.qty}</td>
          <td>₹${Number(p.entry_price || 0).toFixed(2)}</td>
          <td style="font-weight: 600; color: #38bdf8;">₹${Number(p.current_price || p.entry_price || 0).toFixed(2)}</td>
          <td>₹${Number(p.amount || p.invested || (p.qty * p.entry_price) || 0).toFixed(2)}</td>
          <td class="${isGreen ? 'green' : 'red'}">${formatSignedINR(p.unrealized_pnl || 0)}</td>
          <td class="${isGreen ? 'green' : 'red'}">${isGreen ? '+' : ''}${p.unrealized_pnl_pct || 0}%</td>
          <td>₹${Number(p.peak_price || p.current_price || p.entry_price || 0).toFixed(2)}</td>
          <td>₹${Number(p.current_stop || p.sl_price || p.stop_loss || (p.entry_price * 0.95)).toFixed(2)}</td>
          <td>
            <button class="btn btn-danger" style="padding: 2px 8px; font-size: 0.72rem;" onclick="promptClosePosition('${p.symbol}', ${p.qty})">Close</button>
          </td>
        </tr>
      `;
    });
    tbody.innerHTML = rows;
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="11" style="text-align: center; color: var(--red);">Error loading open positions.</td></tr>`;
  }
}

// 4. Trade History Loader
async function loadTradesTable() {
  const tbody = document.getElementById('tradesTableBody');
  const countBadge = document.getElementById('tradesCountBadge');
  const symFilter = document.getElementById('tradeSymbolFilter').value;
  const outcomeFilter = document.getElementById('tradeOutcomeFilter').value;

  try {
    let url = '/api/trades?account=' + encodeURIComponent(currentAccount) + '&';
    if (symFilter) url += `symbol=${encodeURIComponent(symFilter)}&`;
    if (outcomeFilter) url += `outcome=${encodeURIComponent(outcomeFilter)}&`;

    const res = await fetch(url);
    const data = await res.json();
    const trades = data.trades || [];

    if (countBadge) {
      countBadge.textContent = `Showing: ${data.filtered_count} / ${data.total_records} trades`;
    }

    if (!trades.length) {
      tbody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: var(--text-muted); padding: 2rem;">No matching executed trades found.</td></tr>`;
      return;
    }

    let rows = "";
    trades.forEach(t => {
      const pnl = Number(t.pnl || 0);
      const isGreen = pnl > 0;
      const isLoss = pnl < 0;
      rows += `
        <tr>
          <td>${t.id || 'POS'}</td>
          <td style="font-weight: 700; color: #fff;">${formatPositionSymbol(t)}</td>
          <td><span class="status-badge open" style="padding: 1px 6px; font-size: 0.68rem;">BUY</span></td>
          <td>${t.qty}</td>
          <td>₹${Number(t.entry_price).toFixed(2)}</td>
          <td>₹${Number(t.exit_price).toFixed(2)}</td>
          <td>${t.entry_time || '--'}</td>
          <td>${t.exit_time || '--'}</td>
          <td class="${isGreen ? 'green' : (isLoss ? 'red' : '')}">${formatSignedINR(pnl)}</td>
          <td style="max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${t.reason || t.exit_reason || ''}">${t.reason || t.exit_reason || 'N/A'}</td>
        </tr>
      `;
    });
    tbody.innerHTML = rows;
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: var(--red);">Error loading trade history.</td></tr>`;
  }
}

// 5. Strategy Analytics Loader
async function loadStrategiesData() {
  const tbody = document.getElementById('strategiesTableBody');
  try {
    const res = await fetch('/api/strategies');
    const data = await res.json();
    const strategies = data.strategies || [];

    if (!strategies.length) {
      tbody.innerHTML = `<tr><td colspan="9" style="text-align: center;">No strategy data recorded.</td></tr>`;
      return;
    }

    let rows = "";
    strategies.forEach(s => {
      rows += `
        <tr>
          <td style="font-weight: 700; color: #fff;">${s.strategy}</td>
          <td>${s.trades}</td>
          <td class="green">${s.wins}</td>
          <td class="red">${s.losses}</td>
          <td>${s.win_rate}%</td>
          <td class="green">₹${s.gross_profit.toFixed(2)}</td>
          <td class="red">₹${s.gross_loss.toFixed(2)}</td>
          <td>${s.profit_factor}</td>
          <td class="${s.net_pnl >= 0 ? 'green' : 'red'}">${formatSignedINR(s.net_pnl)}</td>
        </tr>
      `;
    });
    tbody.innerHTML = rows;
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; color: var(--red);">Error loading strategy stats.</td></tr>`;
  }
}

// 6. Risk Center Loader
async function loadRiskData() {
  try {
    const res = await fetch('/api/risk');
    const data = await res.json();

    document.getElementById('riskMaxDailyLoss').textContent = formatINR(data.max_daily_loss);
    document.getElementById('riskCurrentDailyLoss').textContent = formatINR(data.realized_daily_loss);
    document.getElementById('riskDailyTrades').textContent = `${data.current_trades_count} / ${data.max_trades_per_day} Day`;
    document.getElementById('riskSquareoffTime').textContent = `${data.flat_by_time} IST`;

    const container = document.getElementById('riskAlertsContainer');
    const alerts = data.alerts || [];
    if (!alerts.length) {
      container.innerHTML = `<div style="font-size: 0.8rem; color: var(--emerald); padding: 0.5rem 0;">✓ All risk metrics compliant. Zero limit breaches.</div>`;
    } else {
      let html = "";
      alerts.forEach(a => {
        const isCrit = a.severity === "CRITICAL";
        html += `
          <div style="background: rgba(${isCrit ? '239, 68, 68' : '245, 158, 11'}, 0.1); border-left: 3px solid ${isCrit ? 'var(--red)' : 'var(--gold-light)'}; padding: 0.6rem 0.85rem; border-radius: 4px; font-size: 0.8rem; display: flex; justify-content: space-between;">
            <div>
              <strong style="color: ${isCrit ? 'var(--red)' : 'var(--gold-light)'};">[${a.severity}]</strong>
              <span style="color: var(--text-primary); margin-left: 0.4rem;">${a.message}</span>
            </div>
            <span style="color: var(--text-muted); font-size: 0.72rem;">Active Rule</span>
          </div>
        `;
      });
      container.innerHTML = html;
    }
  } catch (err) {
    console.error("Risk load error:", err);
  }
}

// 7. Bot Control Loader
async function loadControlData() {
  try {
    const res = await fetch('/api/overview?account=' + encodeURIComponent(currentAccount));
    const data = await res.json();
    const bot = data.bot_status || {};
    const isRunning = bot.status === "RUNNING";

    const stateEl = document.getElementById('controlProcessState');
    const pidEl = document.getElementById('controlPidSub');
    const btnStart = document.getElementById('btnStartBot');
    const btnStop = document.getElementById('btnStopBot');

    if (stateEl) {
      stateEl.textContent = isRunning ? "RUNNING" : "STOPPED";
      stateEl.className = `kpi-value ${isRunning ? 'green' : 'red'}`;
    }
    if (pidEl) {
      pidEl.textContent = isRunning ? `Process ID: ${bot.pid}` : "Process Idle";
    }
    if (btnStart) btnStart.disabled = isRunning;
    if (btnStop) btnStop.disabled = !isRunning;
  } catch (err) {
    console.error("Control load error:", err);
  }
}

async function startBotDaemon() {
  if (!confirm("Confirm launching the automated day trading bot daemon?")) return;
  try {
    const res = await authFetch('/api/bot/start', { method: 'POST' });
    const data = await res.json();
    alert(data.message);
    loadControlData();
    loadOverviewData();
  } catch (err) {
    alert("Error starting bot: " + err.message);
  }
}

async function stopBotDaemon() {
  if (!confirm("WARNING: Stop the trading daemon? Open positions will NOT be automatically closed unless instructed.")) return;
  try {
    const res = await authFetch('/api/bot/stop', { method: 'POST' });
    const data = await res.json();
    alert(data.message);
    loadControlData();
    loadOverviewData();
  } catch (err) {
    alert("Error stopping bot: " + err.message);
  }
}

// 8. Logs Terminal Loader
async function loadLogsTerminal() {
  const terminal = document.getElementById('logsTerminal');
  const lvlFilter = document.getElementById('logLevelFilter').value;
  try {
    let url = '/api/logs?';
    if (lvlFilter) url += `level=${lvlFilter}`;
    const res = await fetch(url);
    const data = await res.json();
    const logs = data.logs || [];

    if (!logs.length) {
      terminal.innerHTML = `<div style="color: var(--text-muted); text-align: center; padding: 2rem;">No system log events recorded.</div>`;
      return;
    }

    let html = "";
    logs.forEach(l => {
      html += `
        <div class="log-entry">
          <span class="log-time">[${l.timestamp || '--'}]</span>
          <span class="log-badge ${l.level}">${l.level}</span>
          <span style="color: var(--text-primary);">${l.message}</span>
        </div>
      `;
    });
    terminal.innerHTML = html;
  } catch (err) {
    terminal.innerHTML = `<div style="color: var(--red);">Error reading system logs.</div>`;
  }
}

// 9. Config Loader
async function loadConfigData() {
  const tbody = document.getElementById('configTableBody');
  try {
    const res = await fetch('/api/config');
    const data = await res.json();
    const cfg = data.config || {};

    const descriptions = {
      budget: "Starting account trading budget capital in INR",
      market: "Target exchange (NSE / BSE)",
      currency: "Account base currency",
      candle_timeframe: "Primary chart candle interval (5m)",
      cooldown_minutes: "Minimum waiting delay before re-entering same symbol",
      max_trades_per_day: "Daily over-trading quota guard",
      max_daily_loss_usd: "Hard kill-switch stop loss for the day",
      max_loss_per_trade_usd: "Max permissible risk per single order",
      max_open_positions: "Max simultaneous open positions",
      no_entries_after: "Intraday cutoff time after which no new trades are taken",
      flat_by: "Mandatory end-of-day market square-off time",
      strategy_mode: "Active algorithmic strategy engine mode",
      broker: "Active execution adapter"
    };

    let rows = "";
    Object.keys(cfg).forEach(k => {
      rows += `
        <tr>
          <td style="font-weight: 600; color: #fff;">${k}</td>
          <td style="color: var(--emerald);">${JSON.stringify(cfg[k])}</td>
          <td style="color: var(--text-secondary);">${descriptions[k] || "Strategy engine parameter"}</td>
        </tr>
      `;
    });
    tbody.innerHTML = rows;
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="3" style="text-align: center; color: var(--red);">Error loading configuration.</td></tr>`;
  }
}

// Position Close Modal
let pendingCloseSymbol = null;
function promptClosePosition(symbol, qty) {
  pendingCloseSymbol = symbol;
  const modal = document.getElementById('confirmModal');
  const body = document.getElementById('modalBody');
  body.textContent = `Are you sure you want to execute immediate market exit for position ${symbol} (${qty} shares)?`;
  modal.style.display = 'flex';

  document.getElementById('modalConfirmBtn').onclick = async () => {
    try {
      const res = await authFetch('/api/positions/close', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: pendingCloseSymbol })
      });
      const data = await res.json();
      alert(data.message);
      closeConfirmModal();
      refreshCurrentView();
    } catch (err) {
      alert("Failed to close position: " + err.message);
    }
  };
}

function closeConfirmModal() {
  document.getElementById('confirmModal').style.display = 'none';
  pendingCloseSymbol = null;
}

function exportTradeCSV() {
  window.open('/api/trades', '_blank');
}

// Auto-Refresh Controller (Default: PAUSED - WebSocket / Webhook Event Driven)
let autoRefreshEnabled = false;
let autoRefreshTimer = null;
let refreshCountdown = 5;

function toggleAutoRefresh() {
  autoRefreshEnabled = !autoRefreshEnabled;
  const badge = document.getElementById('liveSyncBadge');
  if (badge) {
    if (autoRefreshEnabled) {
      badge.className = "status-badge running";
      badge.innerHTML = `<span class="status-dot"></span> LIVE AUTO-UPDATE (<span id="autoRefreshCountdown">${refreshCountdown}s</span>)`;
      startAutoRefreshLoop();
    } else {
      badge.className = "status-badge closed";
      badge.innerHTML = `<span class="status-dot"></span> PAUSED (Click to Resume)`;
      if (autoRefreshTimer) clearInterval(autoRefreshTimer);
    }
  }
}

function startAutoRefreshLoop() {
  if (autoRefreshTimer) clearInterval(autoRefreshTimer);
  refreshCountdown = 5;

  autoRefreshTimer = setInterval(async () => {
    if (!autoRefreshEnabled) return;
    refreshCountdown--;

    const countdownEl = document.getElementById('autoRefreshCountdown');
    if (countdownEl) countdownEl.textContent = `${refreshCountdown}s`;

    if (refreshCountdown <= 0) {
      refreshCountdown = 5;
      await refreshCurrentView();
    }
  }, 1000);
}

// Initial Boot
window.addEventListener('DOMContentLoaded', () => {
  const sel = document.getElementById('accountSelector');
  if (sel) sel.value = currentAccount;
  loadOverviewData();
  const badge = document.getElementById('liveSyncBadge');
  if (badge) {
    badge.className = "status-badge closed";
    badge.innerHTML = `<span class="status-dot"></span> PAUSED (Click to Resume)`;
  }
});
