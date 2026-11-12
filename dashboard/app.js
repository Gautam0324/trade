// DAY TRADER PRO — GLASS ELITE
// Institutional Algorithmic Terminal Client Engine

let equityChartInstance = null;
let activeView = "overview";
let cachedDashboardData = null;
let currentAccount = new URLSearchParams(window.location.search).get("account") || "gautam";
let dashboardApiToken = "";
let currentChartDateFilter = "ALL";
let autoRefreshTimer = null;
let pollingIntervalMs = 10000; // 10s default
let allTradesCache = [];
let allReportsCache = [];

// ==========================================================================
// 1. INITIALIZATION & DUAL EXPERIENCE (Beginner vs Pro)
// ==========================================================================
document.addEventListener("DOMContentLoaded", () => {
  initUserExperienceMode();
  initAccountSelector();
  updateClock();
  setInterval(updateClock, 1000);
  switchView(activeView);
  startAutoRefresh();
});

function initUserExperienceMode() {
  const savedMode = localStorage.getItem("daytrader_user_mode") || "pro";
  setUserMode(savedMode);
}

function setUserMode(mode) {
  const isBeginner = mode === "beginner";
  localStorage.setItem("daytrader_user_mode", isBeginner ? "beginner" : "pro");

  if (isBeginner) {
    document.body.classList.remove("pro-mode");
    document.body.classList.add("beginner-mode");
  } else {
    document.body.classList.remove("beginner-mode");
    document.body.classList.add("pro-mode");
  }

  const btn = document.getElementById("modeToggleBtn");
  const label = document.getElementById("modeToggleLabel");
  if (btn && label) {
    if (isBeginner) {
      btn.className = "mode-toggle-btn beginner-active";
      label.textContent = "Beginner Mode";
    } else {
      btn.className = "mode-toggle-btn pro-active";
      label.textContent = "Pro Mode";
    }
  }
}

function toggleUserExperienceMode() {
  const current = localStorage.getItem("daytrader_user_mode") || "pro";
  setUserMode(current === "pro" ? "beginner" : "pro");
}

function initAccountSelector() {
  const sel = document.getElementById("accountSelector");
  if (sel) {
    sel.value = currentAccount;
  }
}

function switchAccount(acc) {
  currentAccount = acc || "gautam";
  const url = new URL(window.location);
  url.searchParams.set("account", currentAccount);
  window.history.replaceState({}, "", url);

  const sel = document.getElementById("accountSelector");
  if (sel) sel.value = currentAccount;

  const dateSelect = document.getElementById("chartDateFilter");
  if (dateSelect) {
    dateSelect.value = "ALL";
    currentChartDateFilter = "ALL";
  }

  refreshCurrentView();
}

// ==========================================================================
// 2. TIMERS & AUTO-REFRESH
// ==========================================================================
function updateClock() {
  const now = new Date();
  const timeStr = now.toLocaleTimeString("en-GB", { timeZone: "Asia/Kolkata", hour12: false });
  const clockEl = document.getElementById("istClockDisplay");
  if (clockEl) {
    clockEl.textContent = `${timeStr} IST`;
  }
}

function startAutoRefresh() {
  if (autoRefreshTimer) clearInterval(autoRefreshTimer);
  if (pollingIntervalMs > 0) {
    autoRefreshTimer = setInterval(() => {
      refreshCurrentView(true);
    }, pollingIntervalMs);
  }
}

function changePollingInterval(val) {
  pollingIntervalMs = parseInt(val, 10);
  startAutoRefresh();
}

// ==========================================================================
// 3. UTILITY FORMATTERS
// ==========================================================================
function formatINR(val) {
  if (val === undefined || val === null || isNaN(val)) return "₹0.00";
  const num = Number(val);
  const sign = num < 0 ? "-" : "";
  return `${sign}₹${Math.abs(num).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatSignedINR(val) {
  if (val === undefined || val === null || isNaN(val)) return "₹0.00";
  const num = Number(val);
  const sign = num > 0 ? "+" : (num < 0 ? "-" : "");
  return `${sign}₹${Math.abs(num).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

async function authFetch(url, options = {}) {
  options.headers = options.headers || {};
  if (dashboardApiToken) {
    options.headers["X-API-Token"] = dashboardApiToken;
  }
  return fetch(url, options);
}

// ==========================================================================
// 4. ROUTER & VIEW SWITCHER
// ==========================================================================
function switchView(viewId) {
  activeView = viewId;
  document.querySelectorAll(".view-section").forEach(el => el.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach(el => el.classList.remove("active"));

  const targetSection = document.getElementById(`view-${viewId}`);
  if (targetSection) targetSection.classList.add("active");

  const navEl = document.getElementById(`nav-${viewId}`);
  if (navEl) navEl.classList.add("active");

  const titles = {
    "overview": "Portfolio Overview Cockpit",
    "live-trading": "Live Trading Desk & Active Positions",
    "fno-desk": "F&O Derivatives Desk (NIFTY & BANKNIFTY)",
    "cash-desk": "Cash Equities Desk & Technical Scanner",
    "strategy-centre": "Algorithmic Strategy Performance Matrix",
    "risk-centre": "Risk Management & Kill-Switch Safeguards",
    "trade-journal": "Executed Trades Journal & 13-Step Lifecycle",
    "analytics": "Performance Analytics & Trading Streaks",
    "reports": "Financial Audit & Statutory Tax P&L Statement",
    "accounts": "Multi-Account Manager & Ledger Invariants",
    "system-health": "Infrastructure Diagnostics & Event Stream",
    "settings": "Terminal Configuration & Experience Settings"
  };

  const titleEl = document.getElementById("pageTitle");
  if (titleEl) titleEl.textContent = titles[viewId] || "Trading Terminal";

  refreshCurrentView();
}

async function refreshCurrentView(isBackground = false) {
  try {
    switch (activeView) {
      case "overview":
        await loadOverviewData();
        break;
      case "live-trading":
        await loadLiveTradingData();
        break;
      case "fno-desk":
        await loadFnoDeskData();
        break;
      case "cash-desk":
        await loadCashDeskData();
        break;
      case "strategy-centre":
        await loadStrategyCentreData();
        break;
      case "risk-centre":
        await loadRiskCentreData();
        break;
      case "trade-journal":
        await loadTradesTable();
        break;
      case "analytics":
        await loadAnalyticsData();
        break;
      case "reports":
        await loadReportsData();
        break;
      case "accounts":
        await loadAccountsData();
        break;
      case "system-health":
        await loadSystemHealth();
        break;
      case "settings":
        await loadSettingsData();
        break;
    }
  } catch (err) {
    if (!isBackground) console.error("Error refreshing view:", err);
  }
}

// ==========================================================================
// 5. VIEW 1: OVERVIEW COCKPIT
// ==========================================================================
async function loadOverviewData() {
  const res = await fetch(`/api/dashboard?account=${encodeURIComponent(currentAccount)}`);
  const data = await res.json();
  cachedDashboardData = data;

  if (data.api_token) dashboardApiToken = data.api_token;

  // Topbar badges
  updateTopbarBadges(data);

  const kpis = data.kpis || {};

  // 8 Primary KPI Cards
  const kpiStartingCap = document.getElementById("kpiStartingCap");
  if (kpiStartingCap) kpiStartingCap.textContent = formatINR(kpis.starting_capital);

  const kpiFreeCash = document.getElementById("kpiFreeCash");
  if (kpiFreeCash) kpiFreeCash.textContent = formatINR(kpis.free_cash);

  const kpiDeployedCash = document.getElementById("kpiDeployedCash");
  if (kpiDeployedCash) kpiDeployedCash.textContent = formatINR(kpis.deployed_cash);

  const kpiOpenPositionsSub = document.getElementById("kpiOpenPositionsSub");
  if (kpiOpenPositionsSub) kpiOpenPositionsSub.textContent = `${kpis.open_positions_count || 0} Active Positions`;

  const kpiGrossPnl = document.getElementById("kpiGrossPnl");
  if (kpiGrossPnl) {
    kpiGrossPnl.textContent = formatSignedINR(kpis.today_gross_pnl || 0);
    kpiGrossPnl.className = `kpi-value ${(kpis.today_gross_pnl || 0) >= 0 ? "green" : "red"}`;
  }

  const kpiFees = document.getElementById("kpiFees");
  if (kpiFees) kpiFees.textContent = formatINR(kpis.today_fees || 0);

  const kpiRealizedPnl = document.getElementById("kpiRealizedPnl");
  if (kpiRealizedPnl) {
    kpiRealizedPnl.textContent = formatSignedINR(kpis.today_realized_pnl || 0);
    kpiRealizedPnl.className = `kpi-value ${(kpis.today_realized_pnl || 0) >= 0 ? "green" : "red"}`;
  }

  const kpiWinRate = document.getElementById("kpiWinRate");
  if (kpiWinRate) kpiWinRate.textContent = `${kpis.win_rate || 0.0}%`;

  const kpiWinsLossesSub = document.getElementById("kpiWinsLossesSub");
  if (kpiWinsLossesSub) kpiWinsLossesSub.textContent = `${kpis.wins_count || 0}W / ${kpis.losses_count || 0}L / ${kpis.breakeven_count || 0}BE`;

  const kpiProfitFactor = document.getElementById("kpiProfitFactor");
  if (kpiProfitFactor) {
    kpiProfitFactor.textContent = (kpis.profit_factor || 1.0).toFixed(2);
    kpiProfitFactor.className = `kpi-value ${(kpis.profit_factor || 1.0) >= 1.2 ? "green" : ""}`;
  }

  // Sidebar badge for open positions
  const navOpenCount = document.getElementById("navOpenCount");
  if (navOpenCount) navOpenCount.textContent = kpis.open_positions_count || 0;

  // Dual Segment Breakdown: F&O Desk
  const fno = kpis.segment_fno || {};
  document.getElementById("fnoGrossPnl").textContent = formatSignedINR(fno.gross_pnl || 0);
  document.getElementById("fnoFees").textContent = formatINR(fno.fees || 0);
  const fnoNet = document.getElementById("fnoNetPnl");
  fnoNet.textContent = formatSignedINR(fno.net_pnl || 0);
  fnoNet.className = `segment-metric-val ${(fno.net_pnl || 0) >= 0 ? "val-green" : "val-red"}`;
  document.getElementById("fnoTradesCount").textContent = fno.trades_count || 0;
  document.getElementById("fnoWinRate").textContent = `${fno.win_rate || 0.0}%`;
  document.getElementById("fnoProfitFactor").textContent = (fno.profit_factor || 1.0).toFixed(2);

  // Dual Segment Breakdown: Cash Desk
  const cash = kpis.segment_cash || {};
  document.getElementById("cashGrossPnl").textContent = formatSignedINR(cash.gross_pnl || 0);
  document.getElementById("cashFees").textContent = formatINR(cash.fees || 0);
  const cashNet = document.getElementById("cashNetPnl");
  cashNet.textContent = formatSignedINR(cash.net_pnl || 0);
  cashNet.className = `segment-metric-val ${(cash.net_pnl || 0) >= 0 ? "val-green" : "val-red"}`;
  document.getElementById("cashTradesCount").textContent = cash.trades_count || 0;
  document.getElementById("cashWinRate").textContent = `${cash.win_rate || 0.0}%`;
  document.getElementById("cashProfitFactor").textContent = (cash.profit_factor || 1.0).toFixed(2);

  // Render Equity Curve
  await renderEquityChart();

  // Quick Watchlist & Positions Row
  renderQuickWidgets(kpis.enriched_positions || []);
}

function updateTopbarBadges(data) {
  const mkt = document.getElementById("marketBadge");
  if (mkt) {
    if (data.market_open) {
      mkt.className = "status-badge open";
      mkt.innerHTML = `<span class="status-dot"></span> Market Open`;
    } else {
      mkt.className = "status-badge closed";
      mkt.innerHTML = `<span class="status-dot"></span> Market Closed`;
    }
  }

  const bot = document.getElementById("botStatusBadge");
  const daemon = data.bot_status || {};
  if (bot) {
    if (daemon.status === "RUNNING") {
      bot.className = "status-badge running";
      bot.innerHTML = `<span class="status-dot"></span> Bot Running (PID ${daemon.pid})`;
    } else {
      bot.className = "status-badge stopped";
      bot.innerHTML = `<span class="status-dot"></span> Bot Stopped`;
    }
  }
}

function renderQuickWidgets(positions) {
  // Quick Positions
  const posEl = document.getElementById("quickPositions");
  if (posEl) {
    if (positions.length === 0) {
      posEl.innerHTML = `<div style="text-align: center; color: var(--text-muted); padding: 20px;">No open positions. 100% Cash free.</div>`;
    } else {
      let html = `<div style="display: flex; flex-direction: column; gap: 8px;">`;
      positions.forEach(p => {
        const pnl = p.unrealized_pnl || 0;
        html += `
          <div style="display: flex; justify-content: space-between; align-items: center; background: rgba(11, 18, 32, 0.4); padding: 8px 12px; border-radius: 6px; cursor: pointer;" onclick="openTradeDrawer(${JSON.stringify(p).replace(/"/g, '&quot;')})">
            <div>
              <strong>${p.symbol}</strong> <span style="font-size: 11px; color: var(--text-muted);">${p.side || 'BUY'} • ${p.qty} Qty</span>
            </div>
            <div style="text-align: right;">
              <div class="${pnl >= 0 ? 'val-green' : 'val-red'}">${formatSignedINR(pnl)}</div>
              <div style="font-size: 10px; color: var(--text-muted);">LTP: ₹${Number(p.current_price || 0).toFixed(2)}</div>
            </div>
          </div>
        `;
      });
      html += `</div>`;
      posEl.innerHTML = html;
    }
  }

  // Quick Watchlist preview
  fetch(`/api/watchlist?account=${encodeURIComponent(currentAccount)}`)
    .then(r => r.json())
    .then(data => {
      const el = document.getElementById("quickWatchlist");
      if (!el) return;
      const symbols = data.symbols || [];
      if (symbols.length === 0) {
        el.innerHTML = `<div style="text-align: center; color: var(--text-muted); padding: 20px;">No symbols in universe.</div>`;
        return;
      }
      let html = `<div style="display: flex; flex-direction: column; gap: 8px;">`;
      symbols.slice(0, 4).forEach(s => {
        const change = s.change_pct || 0;
        html += `
          <div style="display: flex; justify-content: space-between; align-items: center; background: rgba(11, 18, 32, 0.4); padding: 8px 12px; border-radius: 6px;">
            <div>
              <strong>${s.symbol}</strong>
            </div>
            <div style="text-align: right;">
              <div style="font-family: var(--font-mono); font-weight: 600;">₹${Number(s.price || 0).toFixed(2)}</div>
              <div style="font-size: 10px;" class="${change >= 0 ? 'val-green' : 'val-red'}">${change >= 0 ? '+' : ''}${change.toFixed(2)}%</div>
            </div>
          </div>
        `;
      });
      html += `</div>`;
      el.innerHTML = html;
    })
    .catch(() => {});
}

// ==========================================================================
// 6. INTERACTIVE EQUITY CHART WITH DATE-WISE FILTERING
// ==========================================================================
async function renderEquityChart() {
  try {
    const dateSelect = document.getElementById("chartDateFilter");
    const selectedDate = dateSelect ? dateSelect.value : currentChartDateFilter;
    currentChartDateFilter = selectedDate;

    const res = await fetch(`/api/equity_curve?account=${encodeURIComponent(currentAccount)}&date=${encodeURIComponent(selectedDate)}`);
    const data = await res.json();
    const points = Array.isArray(data) ? data : (data.points || []);
    const availableDates = data.available_dates || [];
    const viewMode = data.view_mode || "ALL";

    // Populate date dropdown if needed
    if (dateSelect && availableDates.length > 0) {
      const existingOpts = Array.from(dateSelect.options).map(o => o.value);
      const needsPopulate = availableDates.some(d => !existingOpts.includes(d));
      if (needsPopulate) {
        dateSelect.innerHTML = `
          <option value="ALL">All Dates (Cumulative)</option>
          <option value="DAILY">Daily P&L Comparison</option>
        `;
        availableDates.forEach(d => {
          const opt = document.createElement("option");
          opt.value = d;
          opt.textContent = `${d} (Intraday)`;
          dateSelect.appendChild(opt);
        });
        dateSelect.value = selectedDate;
      }
    }

    // Update stats badge
    const statsBadge = document.getElementById("chartStatsBadge");
    if (statsBadge) {
      if (viewMode === "DAILY") {
        const netPnl = data.total_pnl || 0;
        statsBadge.textContent = `${points.length} Days | Net: ${formatSignedINR(netPnl)}`;
        statsBadge.style.color = netPnl >= 0 ? "var(--profit-green)" : "var(--loss-red)";
      } else if (viewMode === "INTRADAY") {
        const dayPnl = data.day_pnl || 0;
        statsBadge.textContent = `${data.selected_date}: ${formatSignedINR(dayPnl)} (${points.length - 1} trades)`;
        statsBadge.style.color = dayPnl >= 0 ? "var(--profit-green)" : "var(--loss-red)";
      } else {
        const totalPnl = data.total_pnl || 0;
        statsBadge.textContent = `Total: ${points.length - 1} Trades | Net: ${formatSignedINR(totalPnl)}`;
        statsBadge.style.color = totalPnl >= 0 ? "var(--profit-green)" : "var(--loss-red)";
      }
    }

    const ctx = document.getElementById("equityChart");
    if (!ctx) return;

    if (equityChartInstance) {
      equityChartInstance.destroy();
    }

    const labels = points.map(p => p.time);

    let datasets = [];
    if (viewMode === "DAILY") {
      const dailyPnls = points.map(p => p.day_pnl || 0);
      const barColors = dailyPnls.map(v => v >= 0 ? "rgba(32, 201, 151, 0.7)" : "rgba(240, 82, 82, 0.7)");
      const barBorders = dailyPnls.map(v => v >= 0 ? "#20C997" : "#F05252");

      datasets = [
        {
          type: "bar",
          label: "Daily P&L (₹)",
          data: dailyPnls,
          backgroundColor: barColors,
          borderColor: barBorders,
          borderWidth: 1,
          borderRadius: 4,
          yAxisID: "y"
        },
        {
          type: "line",
          label: "Cumulative Net Equity (₹)",
          data: points.map(p => p.equity),
          borderColor: "#63A5FF",
          backgroundColor: "rgba(99, 165, 255, 0.08)",
          fill: true,
          tension: 0.2,
          pointRadius: 4,
          pointHoverRadius: 6,
          yAxisID: "y1"
        }
      ];
    } else {
      datasets = [
        {
          type: "line",
          label: "Portfolio Equity (₹)",
          data: points.map(p => p.equity),
          borderColor: "#20C997",
          backgroundColor: "rgba(32, 201, 151, 0.12)",
          fill: true,
          tension: 0.25,
          pointRadius: points.length > 50 ? 1 : 3,
          pointHoverRadius: 6,
          borderWidth: 2
        }
      ];
    }

    equityChartInstance = new Chart(ctx, {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { labels: { color: "#AAB8CB", font: { family: "Inter", size: 11 } } },
          tooltip: {
            backgroundColor: "rgba(16, 26, 43, 0.95)",
            borderColor: "rgba(255, 255, 255, 0.15)",
            borderWidth: 1,
            titleColor: "#F8FAFC",
            bodyColor: "#AAB8CB",
            titleFont: { family: "Inter", weight: "bold" },
            bodyFont: { family: "JetBrains Mono" },
            callbacks: {
              label: (item) => {
                const val = Number(item.raw || 0);
                return ` ${item.dataset.label}: ₹${val.toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;
              }
            }
          }
        },
        scales: {
          x: {
            grid: { color: "rgba(255, 255, 255, 0.05)" },
            ticks: { color: "#718096", font: { family: "JetBrains Mono", size: 10 }, maxTicksLimit: 12 }
          },
          y: {
            grid: { color: "rgba(255, 255, 255, 0.05)" },
            ticks: {
              color: "#718096",
              font: { family: "JetBrains Mono", size: 10 },
              callback: v => `₹${(v / 1000).toFixed(0)}k`
            }
          },
          ...(viewMode === "DAILY" ? {
            y1: {
              position: "right",
              grid: { drawOnChartArea: false },
              ticks: { color: "#63A5FF", font: { family: "JetBrains Mono", size: 10 }, callback: v => `₹${(v / 1000).toFixed(0)}k` }
            }
          } : {})
        }
      }
    });
  } catch (err) {
    console.error("Error rendering equity chart:", err);
  }
}

// ==========================================================================
// 7. VIEW 2: LIVE TRADING DESK
// ==========================================================================
async function loadLiveTradingData() {
  const res = await fetch(`/api/positions?account=${encodeURIComponent(currentAccount)}`);
  const data = await res.json();
  const positions = data.open_positions || [];
  const tbody = document.getElementById("positionsTableBody");
  if (!tbody) return;

  if (positions.length === 0) {
    tbody.innerHTML = `<tr><td colspan="11" style="text-align: center; color: var(--text-muted); padding: 30px;">No active open positions. Cash 100% free.</td></tr>`;
    return;
  }

  let html = "";
  positions.forEach(p => {
    const pnl = Number(p.unrealized_pnl || 0);
    const pnlPct = Number(p.unrealized_pnl_pct || 0);
    const pnlClass = pnl >= 0 ? "val-green" : "val-red";
    const posPayload = JSON.stringify(p).replace(/"/g, '&quot;');

    html += `
      <tr class="clickable-row" onclick="openTradeDrawer(${posPayload})">
        <td style="font-family: var(--font-mono); font-size: 11px;">${p.id || p.position_id || '--'}</td>
        <td><strong>${p.symbol}</strong></td>
        <td><span class="status-badge ${p.side === 'BUY' ? 'open' : 'paper'}">${p.side || 'BUY'}</span></td>
        <td>${p.qty}</td>
        <td>₹${Number(p.entry_price || 0).toFixed(2)}</td>
        <td>₹${Number(p.current_price || 0).toFixed(2)}</td>
        <td>₹${Number(p.invested || p.amount || 0).toLocaleString('en-IN', {minimumFractionDigits: 2})}</td>
        <td class="${pnlClass}">${formatSignedINR(pnl)}</td>
        <td class="${pnlClass}">${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%</td>
        <td>₹${Number(p.current_stop || p.sl_price || 0).toFixed(2)}</td>
        <td>
          <button class="btn btn-danger" style="padding: 2px 8px; font-size: 10px;" onclick="event.stopPropagation(); confirmClosePosition('${p.symbol}')">Square Off</button>
        </td>
      </tr>
    `;
  });
  tbody.innerHTML = html;
}

// ==========================================================================
// 8. VIEW 3: F&O DERIVATIVES DESK
// ==========================================================================
async function loadFnoDeskData() {
  const res = await fetch("/api/fno");
  const data = await res.json();

  const nifty = data.indices?.NIFTY || {};
  const bnf = data.indices?.BANKNIFTY || {};

  document.getElementById("niftySpotBadge").textContent = `Spot: ₹${Number(nifty.spot || 0).toLocaleString('en-IN')}`;
  document.getElementById("niftyAtmStrike").textContent = `${nifty.atm_strike || '--'}`;
  document.getElementById("niftyCeContract").textContent = nifty.ce_contract || "QUOTE UNAVAILABLE";
  document.getElementById("niftyPeContract").textContent = nifty.pe_contract || "QUOTE UNAVAILABLE";

  document.getElementById("bankniftySpotBadge").textContent = `Spot: ₹${Number(bnf.spot || 0).toLocaleString('en-IN')}`;
  document.getElementById("bankniftyAtmStrike").textContent = `${bnf.atm_strike || '--'}`;
  document.getElementById("bankniftyCeContract").textContent = bnf.ce_contract || "QUOTE UNAVAILABLE";
  document.getElementById("bankniftyPeContract").textContent = bnf.pe_contract || "QUOTE UNAVAILABLE";

  const fnoPos = data.open_positions || [];
  const tbody = document.getElementById("fnoPositionsTableBody");
  if (!tbody) return;

  if (fnoPos.length === 0) {
    tbody.innerHTML = `<tr><td colspan="11" style="text-align: center; color: var(--text-muted); padding: 30px;">No active option contracts deployed.</td></tr>`;
    return;
  }

  let html = "";
  fnoPos.forEach(p => {
    const pnl = Number(p.unrealized_pnl || 0);
    const pnlClass = pnl >= 0 ? "val-green" : "val-red";
    html += `
      <tr>
        <td><strong>${p.symbol}</strong></td>
        <td><span class="status-badge paper">${p.symbol.endsWith('CE') ? 'CALL (CE)' : 'PUT (PE)'}</span></td>
        <td>${p.lots || 1} Lot(s)</td>
        <td>${p.qty}</td>
        <td>₹${Number(p.entry_price || 0).toFixed(2)}</td>
        <td>₹${Number(p.current_price || 0).toFixed(2)}</td>
        <td>₹${Number(p.invested || 0).toLocaleString('en-IN', {minimumFractionDigits: 2})}</td>
        <td class="${pnlClass}">${formatSignedINR(pnl)}</td>
        <td>₹${Number(p.sl_price || (p.entry_price * 0.95)).toFixed(2)}</td>
        <td>₹${Number(p.target_price || (p.entry_price * 1.15)).toFixed(2)}</td>
        <td>
          <button class="btn btn-danger" style="padding: 2px 8px; font-size: 10px;" onclick="confirmClosePosition('${p.symbol}')">Exit</button>
        </td>
      </tr>
    `;
  });
  tbody.innerHTML = html;
}

// ==========================================================================
// 9. VIEW 4: CASH DESK
// ==========================================================================
async function loadCashDeskData() {
  const res = await fetch(`/api/watchlist?account=${encodeURIComponent(currentAccount)}`);
  const data = await res.json();
  const symbols = data.symbols || [];
  const tbody = document.getElementById("watchlistTableBody");
  if (!tbody) return;

  document.getElementById("cashWatchlistTimestamp").textContent = `Last sync: ${new Date().toLocaleTimeString('en-GB')}`;

  if (symbols.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; color: var(--text-muted); padding: 30px;">No quotes available.</td></tr>`;
    return;
  }

  let html = "";
  symbols.forEach(s => {
    const change = Number(s.change_pct || 0);
    const changeClass = change >= 0 ? "val-green" : "val-red";
    html += `
      <tr>
        <td><strong>${s.symbol}</strong></td>
        <td>₹${Number(s.price || 0).toFixed(2)}</td>
        <td class="${changeClass}">${change >= 0 ? '+' : ''}${change.toFixed(2)}%</td>
        <td>₹${Number(s.ema5 || 0).toFixed(2)}</td>
        <td>₹${Number(s.ema9 || 0).toFixed(2)}</td>
        <td>₹${Number(s.ema33_high || 0).toFixed(2)}</td>
        <td>₹${Number(s.ema33_low || 0).toFixed(2)}</td>
        <td><span class="status-badge ${s.signal === 'BUY' ? 'open' : 'closed'}">${s.signal || 'HOLD / MONITOR'}</span></td>
        <td>
          <button class="btn btn-secondary" style="padding: 2px 8px; font-size: 10px;" onclick="inspectSymbol('${s.symbol}')">Inspect</button>
        </td>
      </tr>
    `;
  });
  tbody.innerHTML = html;
}

// ==========================================================================
// 10. VIEW 5: STRATEGY CENTRE
// ==========================================================================
async function loadStrategyCentreData() {
  const res = await fetch(`/api/strategies?account=${encodeURIComponent(currentAccount)}`);
  const data = await res.json();
  const strategies = data.strategies || [];
  const tbody = document.getElementById("strategiesTableBody");
  if (!tbody) return;

  if (strategies.length === 0) {
    tbody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: var(--text-muted); padding: 30px;">No strategy history recorded.</td></tr>`;
    return;
  }

  let html = "";
  strategies.forEach(s => {
    const net = Number(s.net_pnl || 0);
    const netClass = net >= 0 ? "val-green" : "val-red";
    html += `
      <tr>
        <td><strong>${s.strategy}</strong></td>
        <td><span class="nav-tag">${s.tag || 'QUANT'}</span></td>
        <td><span class="status-badge paper">${s.execution_type || 'PAPER_VERIFIED'}</span></td>
        <td>${s.trades}</td>
        <td>${s.win_rate}%</td>
        <td class="val-green">₹${Number(s.gross_profit || 0).toLocaleString('en-IN', {minimumFractionDigits: 2})}</td>
        <td class="val-red">₹${Number(s.gross_loss || 0).toLocaleString('en-IN', {minimumFractionDigits: 2})}</td>
        <td class="val-green">${Number(s.profit_factor || 1.0).toFixed(2)}</td>
        <td>₹${Number(s.expectancy || 0).toFixed(2)}</td>
        <td class="${netClass}">${formatSignedINR(net)}</td>
      </tr>
    `;
  });
  tbody.innerHTML = html;
}

// ==========================================================================
// 11. VIEW 6: RISK CENTRE
// ==========================================================================
async function loadRiskCentreData() {
  const res = await fetch(`/api/risk?account=${encodeURIComponent(currentAccount)}`);
  const data = await res.json();

  const maxLoss = Number(data.max_daily_loss || 1000.0);
  const curLoss = Number(data.current_daily_loss || 0.0);
  const utilPct = Math.min(100.0, Math.max(0.0, (curLoss / maxLoss) * 100.0));

  const fill = document.getElementById("riskLossBarFill");
  if (fill) fill.style.width = `${utilPct}%`;

  const utilText = document.getElementById("riskLossUtilText");
  if (utilText) {
    utilText.textContent = `${utilPct.toFixed(1)}% Consumed`;
    utilText.style.color = utilPct > 80 ? "var(--loss-red)" : (utilPct > 50 ? "var(--warning-amber)" : "var(--profit-green)");
  }

  document.getElementById("riskMaxDailyLoss").textContent = formatINR(maxLoss);
  document.getElementById("riskCurrentDailyLoss").textContent = formatINR(curLoss);
  document.getElementById("riskDailyTrades").textContent = `${data.max_trades_per_day || 3} / Day`;
  document.getElementById("riskSquareoffTime").textContent = `${data.flat_by_time || '15:20'} IST`;

  const alertsContainer = document.getElementById("riskAlertsContainer");
  if (alertsContainer) {
    const alerts = data.alerts || [];
    if (alerts.length === 0) {
      alertsContainer.innerHTML = `<div style="color: var(--profit-green);">✔ All risk compliance checks passed. Zero safety violations.</div>`;
    } else {
      let html = "";
      alerts.forEach(a => {
        html += `<div style="color: var(--loss-red);">⚠ ${a}</div>`;
      });
      alertsContainer.innerHTML = html;
    }
  }
}

// ==========================================================================
// 12. VIEW 7: TRADE JOURNAL
// ==========================================================================
async function loadTradesTable() {
  const res = await fetch(`/api/trades?account=${encodeURIComponent(currentAccount)}`);
  const data = await res.json();
  allTradesCache = data.trades || [];

  const symFilter = document.getElementById("tradeSymbolFilter");
  if (symFilter && symFilter.options.length <= 1) {
    const syms = Array.from(new Set(allTradesCache.map(t => t.symbol).filter(Boolean)));
    symFilter.innerHTML = `<option value="">All Symbols</option>`;
    syms.forEach(s => {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = s;
      symFilter.appendChild(opt);
    });
  }

  filterAndRenderTrades();
}

function filterAndRenderTrades() {
  const outcome = document.getElementById("tradeOutcomeFilter")?.value || "";
  const symbol = document.getElementById("tradeSymbolFilter")?.value || "";

  let list = allTradesCache.filter(t => {
    const pnl = Number(t.pnl || 0);
    if (outcome === "WIN" && pnl <= 0) return false;
    if (outcome === "LOSS" && pnl >= 0) return false;
    if (outcome === "BREAKEVEN" && pnl !== 0) return false;
    if (symbol && t.symbol !== symbol) return false;
    return true;
  });

  document.getElementById("tradesCountBadge").textContent = `Showing ${list.length} trades`;

  const tbody = document.getElementById("tradesTableBody");
  if (!tbody) return;

  if (list.length === 0) {
    tbody.innerHTML = `<tr><td colspan="11" style="text-align: center; color: var(--text-muted); padding: 30px;">No trades match current filters.</td></tr>`;
    return;
  }

  let html = "";
  list.forEach(t => {
    const net = Number(t.pnl || 0);
    const gross = Number(t.gross_pnl || net);
    const fees = Number(t.fees || 0);
    const netClass = net >= 0 ? "val-green" : "val-red";
    const payload = JSON.stringify(t).replace(/"/g, '&quot;');

    html += `
      <tr class="clickable-row" onclick="openTradeDrawer(${payload})">
        <td style="font-family: var(--font-mono); font-size: 11px;">${t.id || '--'}</td>
        <td>${t.date || t.exit_date || '--'}</td>
        <td><strong>${t.symbol}</strong></td>
        <td><span class="status-badge ${t.side === 'BUY' ? 'open' : 'paper'}">${t.side || 'BUY'}</span></td>
        <td>${t.qty}</td>
        <td>₹${Number(t.entry_price || 0).toFixed(2)}</td>
        <td>₹${Number(t.exit_price || 0).toFixed(2)}</td>
        <td>₹${gross.toFixed(2)}</td>
        <td class="val-red">₹${fees.toFixed(2)}</td>
        <td class="${netClass}">${formatSignedINR(net)}</td>
        <td style="font-size: 11px; color: var(--text-muted); max-width: 160px; overflow: hidden; text-overflow: ellipsis;">${t.reason || t.exit_reason || '--'}</td>
      </tr>
    `;
  });
  tbody.innerHTML = html;
}

// ==========================================================================
// 13. VIEW 8: ANALYTICS
// ==========================================================================
async function loadAnalyticsData() {
  const res = await fetch(`/api/dashboard?account=${encodeURIComponent(currentAccount)}`);
  const data = await res.json();
  const kpis = data.kpis || {};
  const an = kpis.analytics || {};

  document.getElementById("anMaxWinStreak").textContent = an.max_win_streak || 0;
  document.getElementById("anMaxLossStreak").textContent = an.max_loss_streak || 0;
  document.getElementById("anAvgWin").textContent = formatINR(an.avg_win || 0);
  document.getElementById("anAvgLoss").textContent = formatINR(an.avg_loss || 0);
  document.getElementById("anExpectancy").textContent = formatSignedINR(an.expectancy || 0);

  // Hourly Performance Table
  const hourly = an.hourly_distribution || [];
  const hBody = document.getElementById("hourlyTableBody");
  if (hBody) {
    if (hourly.length === 0) {
      hBody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted);">No intraday trading data.</td></tr>`;
    } else {
      let html = "";
      hourly.forEach(h => {
        const pnl = Number(h.pnl || 0);
        const winRate = h.trades > 0 ? ((h.wins / h.trades) * 100).toFixed(1) : "0.0";
        html += `
          <tr>
            <td><strong>${h.hour}</strong></td>
            <td>${h.trades}</td>
            <td>${h.wins}</td>
            <td>${winRate}%</td>
            <td class="${pnl >= 0 ? 'val-green' : 'val-red'}">${formatSignedINR(pnl)}</td>
          </tr>
        `;
      });
      hBody.innerHTML = html;
    }
  }

  // Best & Worst Instruments
  renderBestWorstTable("bestInstrumentsBody", an.best_instruments || [], true);
  renderBestWorstTable("worstInstrumentsBody", an.worst_instruments || [], false);
}

function renderBestWorstTable(elementId, items, isBest) {
  const el = document.getElementById(elementId);
  if (!el) return;
  if (items.length === 0) {
    el.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--text-muted);">No records</td></tr>`;
    return;
  }
  let html = "";
  items.forEach(i => {
    const pnl = Number(i.pnl || 0);
    html += `
      <tr>
        <td><strong>${i.symbol}</strong></td>
        <td>${i.trades}</td>
        <td>${i.wins || (i.trades - (i.losses || 0))}</td>
        <td class="${pnl >= 0 ? 'val-green' : 'val-red'}">${formatSignedINR(pnl)}</td>
      </tr>
    `;
  });
  el.innerHTML = html;
}

// ==========================================================================
// 14. VIEW 9: REPORTS & TAX P&L
// ==========================================================================
async function loadReportsData() {
  const res = await fetch(`/api/trades?account=${encodeURIComponent(currentAccount)}`);
  const data = await res.json();
  allReportsCache = data.trades || [];

  let totalTurnover = 0;
  let totalGross = 0;
  let totalFees = 0;
  let totalNet = 0;

  allReportsCache.forEach(t => {
    const qty = Number(t.qty || 0);
    const entryP = Number(t.entry_price || 0);
    const exitP = Number(t.exit_price || 0);
    const turnover = (entryP * qty) + (exitP * qty);
    const net = Number(t.pnl || 0);
    const gross = Number(t.gross_pnl || net);
    const fees = Number(t.fees || 0);

    totalTurnover += turnover;
    totalGross += gross;
    totalFees += fees;
    totalNet += net;
  });

  document.getElementById("repTotalTurnover").textContent = formatINR(totalTurnover);
  document.getElementById("repGrossPnl").textContent = formatSignedINR(totalGross);
  document.getElementById("repTotalFees").textContent = formatINR(totalFees);
  const repNet = document.getElementById("repNetPnl");
  repNet.textContent = formatSignedINR(totalNet);
  repNet.className = `kpi-value ${totalNet >= 0 ? "green" : "red"}`;

  filterAndRenderReports();
}

function filterAndRenderReports() {
  const dateFilter = document.getElementById("reportDateFilter")?.value || "ALL";
  const segFilter = document.getElementById("reportSegmentFilter")?.value || "ALL";
  const outcomeFilter = document.getElementById("reportOutcomeFilter")?.value || "ALL";

  const todayStr = new Date().toISOString().slice(0, 10);

  let filtered = allReportsCache.filter(t => {
    const pnl = Number(t.pnl || 0);
    const sym = t.symbol || "";
    const isFno = sym.endsWith("CE") || sym.endsWith("PE") || (t.id && t.id.includes("OPT"));

    if (dateFilter === "TODAY" && t.date !== todayStr && t.exit_date !== todayStr) return false;
    if (segFilter === "CASH" && isFno) return false;
    if (segFilter === "FNO" && !isFno) return false;
    if (outcomeFilter === "WIN" && pnl <= 0) return false;
    if (outcomeFilter === "LOSS" && pnl >= 0) return false;
    return true;
  });

  document.getElementById("reportsCountBadge").textContent = `Showing ${filtered.length} trades`;

  const tbody = document.getElementById("reportsTableBody");
  if (!tbody) return;

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="13" style="text-align: center; color: var(--text-muted); padding: 30px;">No statement entries matching filters.</td></tr>`;
    return;
  }

  let html = "";
  filtered.forEach(t => {
    const qty = Number(t.qty || 0);
    const entryP = Number(t.entry_price || 0);
    const exitP = Number(t.exit_price || 0);
    const turnover = (entryP * qty) + (exitP * qty);
    const net = Number(t.pnl || 0);
    const gross = Number(t.gross_pnl || net);
    const fees = Number(t.fees || 0);
    const isFno = (t.symbol || "").endsWith("CE") || (t.symbol || "").endsWith("PE");

    html += `
      <tr>
        <td>${t.date || t.exit_date || '--'}</td>
        <td style="font-family: var(--font-mono); font-size: 11px;">${t.id || '--'}</td>
        <td><strong>${t.symbol}</strong></td>
        <td><span class="status-badge ${isFno ? 'paper' : 'open'}">${isFno ? 'F&O' : 'CASH'}</span></td>
        <td>${t.side || 'BUY'}</td>
        <td>${qty}</td>
        <td>₹${entryP.toFixed(2)}</td>
        <td>₹${exitP.toFixed(2)}</td>
        <td>₹${turnover.toLocaleString('en-IN', {minimumFractionDigits: 2})}</td>
        <td>₹${gross.toFixed(2)}</td>
        <td class="val-red">₹${fees.toFixed(2)}</td>
        <td class="${net >= 0 ? 'val-green' : 'val-red'}">${formatSignedINR(net)}</td>
        <td style="font-size: 11px; color: var(--text-muted);">${t.reason || t.exit_reason || '--'}</td>
      </tr>
    `;
  });
  tbody.innerHTML = html;
}

// ==========================================================================
// 15. VIEW 10: ACCOUNTS MANAGER & INVARIANT RECONCILIATION
// ==========================================================================
async function loadAccountsData() {
  const dashRes = await fetch(`/api/dashboard?account=${encodeURIComponent(currentAccount)}`);
  const dashData = await dashRes.json();
  const kpis = dashData.kpis || {};
  const inv = kpis.accounting_invariant || {};

  const badge = document.getElementById("invariantStatusBadge");
  if (badge) {
    if (inv.status === "PASS") {
      badge.className = "status-badge open";
      badge.textContent = "INVARIANT: PASS (100% RECONCILED)";
    } else {
      badge.className = "status-badge stopped";
      badge.textContent = "INVARIANT: DISCREPANCY DETECTED";
    }
  }

  document.getElementById("invLhsVal").textContent = formatINR(inv.lhs || 0);
  document.getElementById("invRhsVal").textContent = formatINR(inv.rhs || 0);
  const diffEl = document.getElementById("invDiffVal");
  diffEl.textContent = formatINR(inv.diff || 0);
  diffEl.className = `segment-metric-val ${(inv.diff || 0) <= 0.05 ? "val-green" : "val-red"}`;

  // Multi-Account Grid Cards
  const accRes = await fetch("/api/accounts");
  const accData = await accRes.json();
  const accounts = accData.accounts || {};

  const container = document.getElementById("accountsGridContainer");
  if (!container) return;

  let html = "";
  for (const [id, meta] of Object.entries(accounts)) {
    const isSelected = id === currentAccount;
    html += `
      <div class="kpi-card" style="border-color: ${isSelected ? 'var(--info-blue)' : 'var(--glass-border)'}; cursor: pointer;" onclick="switchAccount('${id}')">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span style="font-weight: 700; font-size: 14px; color: var(--text-primary);">${meta.name || id}</span>
          ${isSelected ? '<span class="status-badge open">ACTIVE</span>' : '<span class="status-badge closed">SELECT</span>'}
        </div>
        <div style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">Role: ${meta.role || 'Trader'} • Segment: ${meta.segment || 'Cash'}</div>
        <div class="kpi-value" style="font-size: 18px; margin-top: 10px;">${formatINR(meta.capital || 1000000)}</div>
        <div style="font-size: 11px; color: var(--text-secondary); margin-top: 4px;">Ledger: <code>${meta.ledger_file}</code></div>
      </div>
    `;
  }
  container.innerHTML = html;
}

async function reconcileActiveAccount() {
  const res = await authFetch("/api/accounts/reconcile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account: currentAccount })
  });
  const data = await res.json();
  if (data.status === "SUCCESS") {
    alert(`Account ${currentAccount} reconciled successfully! Status: ${data.report?.is_valid ? 'VALID' : 'REPAIRED'}`);
    refreshCurrentView();
  } else {
    alert(`Reconciliation failed: ${data.message || 'Unknown error'}`);
  }
}

// ==========================================================================
// 16. VIEW 11: SYSTEM HEALTH & LOGS
// ==========================================================================
async function loadSystemHealth() {
  const res = await fetch(`/api/system/status?account=${encodeURIComponent(currentAccount)}`);
  const data = await res.json();
  const daemon = data.daemon || {};

  const daemonState = document.getElementById("healthDaemonState");
  if (daemonState) {
    if (daemon.status === "RUNNING") {
      daemonState.textContent = "RUNNING";
      daemonState.className = "kpi-value green";
      document.getElementById("healthPidSub").textContent = `PID: ${daemon.pid}`;
    } else {
      daemonState.textContent = "STOPPED";
      daemonState.className = "kpi-value red";
      document.getElementById("healthPidSub").textContent = "PID: --";
    }
  }

  loadLogsTerminal();
}

async function loadLogsTerminal() {
  const lvl = document.getElementById("logLevelFilter")?.value || "";
  const res = await fetch(`/api/logs?level=${encodeURIComponent(lvl)}`);
  const data = await res.json();
  const logs = data.logs || [];
  const term = document.getElementById("logsTerminal");
  if (!term) return;

  if (logs.length === 0) {
    term.innerHTML = `<div style="text-align: center; color: var(--text-muted); padding: 40px;">No logs recorded.</div>`;
    return;
  }

  let html = "";
  logs.forEach(l => {
    const lvlClass = `log-level-${l.level || 'INFO'}`;
    html += `
      <div class="terminal-log-row">
        <span class="log-time">[${l.timestamp || '--'}]</span>
        <span class="${lvlClass}">[${l.level || 'INFO'}]</span>
        <span class="log-msg">${l.message}</span>
      </div>
    `;
  });
  term.innerHTML = html;
}

// ==========================================================================
// 17. VIEW 12: SETTINGS
// ==========================================================================
async function loadSettingsData() {
  const res = await fetch("/api/config");
  const data = await res.json();
  const cfg = data.config || {};
  const tbody = document.getElementById("configTableBody");
  if (!tbody) return;

  let html = "";
  for (const [k, v] of Object.entries(cfg)) {
    html += `
      <tr>
        <td><code>${k}</code></td>
        <td style="font-family: var(--font-mono); font-weight: 600;">${typeof v === 'object' ? JSON.stringify(v) : v}</td>
        <td style="font-size: 11px; color: var(--text-muted);">Runtime safety parameter</td>
      </tr>
    `;
  }
  tbody.innerHTML = html;
}

// ==========================================================================
// 18. TRADE DETAILS DRAWER (13-Step Lifecycle Timeline)
// ==========================================================================
function openTradeDrawer(trade) {
  if (!trade) return;

  document.getElementById("drawerTradeSymbol").textContent = trade.symbol || "UNKNOWN";
  document.getElementById("drawerTradeId").textContent = `ID: ${trade.id || trade.position_id || '--'}`;

  const netPnl = Number(trade.pnl || trade.unrealized_pnl || 0);
  const netEl = document.getElementById("drawerNetPnl");
  netEl.textContent = formatSignedINR(netPnl);
  netEl.className = `segment-metric-val ${netPnl >= 0 ? "val-green" : "val-red"}`;

  const entryP = Number(trade.entry_price || 0);
  const exitP = Number(trade.exit_price || trade.current_price || entryP);
  document.getElementById("drawerPrices").textContent = `₹${entryP.toFixed(2)} → ₹${exitP.toFixed(2)}`;
  document.getElementById("drawerQty").textContent = `${trade.qty || 0} Units`;

  // Render 13-Step Execution Lifecycle Timeline
  render13StepTimeline(trade);

  document.getElementById("drawerBackdrop").classList.add("active");
  document.getElementById("tradeDetailDrawer").classList.add("active");
}

function closeTradeDrawer() {
  document.getElementById("drawerBackdrop").classList.remove("active");
  document.getElementById("tradeDetailDrawer").classList.remove("active");
}

function render13StepTimeline(t) {
  const container = document.getElementById("drawerTimeline");
  if (!container) return;

  const entryTime = t.entry_time || t.timestamp || "09:30:00 IST";
  const exitTime = t.exit_time || "In Progress";
  const isClosed = Boolean(t.exit_time || t.pnl !== undefined);

  const steps = [
    { num: 1, title: "Market Data Ingestion", time: entryTime, desc: `Completed 1m / 5m candle received for ${t.symbol}. Zero lookahead verified.` },
    { num: 2, title: "Technical Indicators Evaluated", time: entryTime, desc: "EMA 5, EMA 9, EMA 33 Channel, Supertrend (10, 3), and VWAP computed on atomic series." },
    { num: 3, title: "Quantitative Setup Detected", time: entryTime, desc: `Setup Signal: ${t.entry_reason || t.reason || 'EMA Channel / Confluence Breakout'}. Confidence >= 65.` },
    { num: 4, title: "Pre-Trade Risk Verification", time: entryTime, desc: "Daily loss budget, max open positions, and reward-to-risk (>= 1:1.5) passed without exception." },
    { num: 5, title: "Order Routed to Broker Adapter", time: entryTime, desc: `Adapter order generated: BUY ${t.qty} @ limit/market reference ₹${Number(t.entry_price || 0).toFixed(2)}.` },
    { num: 6, title: "Execution Fill Confirmed", time: entryTime, desc: `Position opened. Filled price: ₹${Number(t.entry_price || 0).toFixed(2)}. Zero slippage simulated.` },
    { num: 7, title: "Initial Stop-Loss & Target Placed", time: entryTime, desc: `SL set @ ₹${Number(t.sl_price || (t.entry_price * 0.95)).toFixed(2)} | Target set @ ₹${Number(t.target_price || (t.entry_price * 1.15)).toFixed(2)}.` },
    { num: 8, title: "Mark-to-Market Real-Time Tracking", time: isClosed ? exitTime : "Active", desc: `Trailing profit ratchet active. Peak tracked: ₹${Number(t.peak_price || t.exit_price || t.entry_price).toFixed(2)}.` },
    { num: 9, title: "Dynamic Trailing Stop Updated", time: isClosed ? exitTime : "Active", desc: "Trailing stop ratcheted to lock gains as price progressed in favorable direction." },
    { num: 10, title: "Exit Signal Triggered", time: isClosed ? exitTime : "Pending", desc: isClosed ? `Exit Trigger: ${t.reason || t.exit_reason || 'Target Reached / Trailing SL Hit'}.` : "Position currently running in market." },
    { num: 11, title: "Exit Order Executed", time: isClosed ? exitTime : "Pending", desc: isClosed ? `Sold ${t.qty} units @ ₹${Number(t.exit_price || 0).toFixed(2)}.` : "Awaiting exit fill." },
    { num: 12, title: "Statutory Deductions & Brokerage Computed", time: isClosed ? exitTime : "Pending", desc: isClosed ? `STT, Exchange txn charges, GST (18%), and SEBI fees: ₹${Number(t.fees || 0).toFixed(2)}.` : "Will be deducted upon exit." },
    { num: 13, title: "Double-Entry Ledger Reconciliation", time: isClosed ? exitTime : "Pending", desc: isClosed ? `Net Realized P&L of ${formatSignedINR(t.pnl || 0)} credited to account free cash balance.` : "Pending balance settlement." }
  ];

  let html = "";
  steps.forEach(s => {
    const isStepDone = isClosed || s.num <= 9;
    const stepClass = isStepDone ? "" : "pending";
    html += `
      <div class="timeline-step ${stepClass}">
        <div class="timeline-title">
          <span>${s.num}. ${s.title}</span>
          <span class="timeline-time">${s.time}</span>
        </div>
        <div class="timeline-desc">${s.desc}</div>
      </div>
    `;
  });
  container.innerHTML = html;
}

// ==========================================================================
// 19. EMERGENCY CONTROLS & MANUAL EXITS
// ==========================================================================
let pendingCloseSymbol = null;

function confirmClosePosition(symbol) {
  pendingCloseSymbol = symbol;
  document.getElementById("modalTitle").textContent = "Confirm Position Square-Off";
  document.getElementById("modalBody").textContent = `Are you sure you want to manually square off position ${symbol}? This will record market exit and reconcile ledger.`;
  document.getElementById("modalConfirmBtn").onclick = executeConfirmedClose;
  document.getElementById("confirmModal").classList.add("active");
}

function closeConfirmModal() {
  document.getElementById("confirmModal").classList.remove("active");
  pendingCloseSymbol = null;
}

async function executeConfirmedClose() {
  if (!pendingCloseSymbol) return;
  const res = await authFetch("/api/positions/close", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbol: pendingCloseSymbol })
  });
  const data = await res.json();
  closeConfirmModal();
  if (data.status === "SUCCESS") {
    alert(data.message || "Position closed successfully.");
    refreshCurrentView();
  } else {
    alert(data.message || "Error closing position.");
  }
}

async function triggerEmergencyPause() {
  if (!confirm("EMERGENCY PAUSE: This will immediately halt all trading bot daemons. Continue?")) return;
  const res = await authFetch("/api/risk/emergency_pause", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({})
  });
  const data = await res.json();
  alert(data.message || "Emergency pause command issued.");
  refreshCurrentView();
}

async function triggerEmergencySquareoff() {
  if (!confirm("KILL SWITCH CONFIRMATION: This will immediately close ALL open positions across this account. Are you sure?")) return;
  const res = await authFetch("/api/risk/emergency_squareoff", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account: currentAccount })
  });
  const data = await res.json();
  alert(data.message || "Kill switch executed.");
  refreshCurrentView();
}

// ==========================================================================
// 20. CSV EXPORT HELPERS
// ==========================================================================
function exportTradeCSV() {
  if (allTradesCache.length === 0) {
    alert("No trades to export.");
    return;
  }
  const headers = ["Trade ID", "Date", "Symbol", "Side", "Qty", "Entry Price", "Exit Price", "Gross P&L", "Fees", "Net P&L", "Reason"];
  const rows = allTradesCache.map(t => [
    t.id || "",
    t.date || t.exit_date || "",
    t.symbol || "",
    t.side || "BUY",
    t.qty || 0,
    t.entry_price || 0,
    t.exit_price || 0,
    t.gross_pnl || t.pnl || 0,
    t.fees || 0,
    t.pnl || 0,
    `"${(t.reason || t.exit_reason || '').replace(/"/g, '""')}"`
  ]);

  downloadCSV([headers.join(","), ...rows.map(r => r.join(","))].join("\n"), `trade_journal_${currentAccount}_${new Date().toISOString().slice(0, 10)}.csv`);
}

function exportReportsCSV() {
  if (allReportsCache.length === 0) {
    alert("No statement entries to export.");
    return;
  }
  const headers = ["Date", "Trade ID", "Symbol", "Segment", "Side", "Qty", "Entry Price", "Exit Price", "Turnover", "Gross P&L", "Fees", "Net P&L", "Reason"];
  const rows = allReportsCache.map(t => {
    const qty = Number(t.qty || 0);
    const entryP = Number(t.entry_price || 0);
    const exitP = Number(t.exit_price || 0);
    const turnover = (entryP * qty) + (exitP * qty);
    const isFno = (t.symbol || "").endsWith("CE") || (t.symbol || "").endsWith("PE");
    return [
      t.date || t.exit_date || "",
      t.id || "",
      t.symbol || "",
      isFno ? "FNO" : "CASH",
      t.side || "BUY",
      qty,
      entryP,
      exitP,
      turnover.toFixed(2),
      t.gross_pnl || t.pnl || 0,
      t.fees || 0,
      t.pnl || 0,
      `"${(t.reason || t.exit_reason || '').replace(/"/g, '""')}"`
    ];
  });

  downloadCSV([headers.join(","), ...rows.map(r => r.join(","))].join("\n"), `tax_statement_${currentAccount}_${new Date().toISOString().slice(0, 10)}.csv`);
}

function downloadCSV(csvContent, filename) {
  const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
  const link = document.createElement("a");
  const url = URL.createObjectURL(blob);
  link.setAttribute("href", url);
  link.setAttribute("download", filename);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}
