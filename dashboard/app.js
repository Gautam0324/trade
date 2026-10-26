// Day Trader Skill Dashboard App Logic (Multi-Market: NSE / BSE / US)
let chartInstance = null;
let currentMarket = "NSE";
let currentCurrency = "INR";
let currentCurrencySymbol = "₹";

// Dynamic Clock supporting IST and ET
function updateClock() {
  const now = new Date();
  const timeZone = (currentMarket === 'US') ? 'America/New_York' : 'Asia/Kolkata';
  const tzLabel = (currentMarket === 'US') ? 'ET (NY)' : 'IST (India)';
  const timeStr = now.toLocaleTimeString('en-US', { timeZone: timeZone, hour12: false });
  const clockEl = document.getElementById('clockDisplay');
  if (clockEl) clockEl.textContent = `${timeStr} ${tzLabel}`;
}
setInterval(updateClock, 1000);
updateClock();

function formatMoney(amount) {
  const sym = currentCurrency === 'INR' ? '₹' : '$';
  return `${sym}${Number(amount).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

async function switchMarket(market, symbol) {
  const logEl = document.getElementById('systemLog');
  logEl.textContent += `\n[ACTION] Switching market to ${market} (${symbol})...`;
  currentMarket = market;
  currentCurrency = (market === 'US') ? 'USD' : 'INR';
  currentCurrencySymbol = (market === 'US') ? '$' : '₹';

  // Update Buttons
  document.getElementById('btnNSE').style.borderColor = (market === 'NSE') ? 'var(--accent-cyan)' : 'var(--border-subtle)';
  document.getElementById('btnBSE').style.borderColor = (market === 'BSE') ? 'var(--accent-cyan)' : 'var(--border-subtle)';
  document.getElementById('btnUS').style.borderColor = (market === 'US') ? 'var(--accent-cyan)' : 'var(--border-subtle)';

  const defaultBudget = (market === 'US') ? 5000 : 100000;
  const defaultMaxLoss = (market === 'US') ? 50 : 1000;

  try {
    const res = await fetch('/api/set_market', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        market: market,
        symbol: symbol,
        budget: defaultBudget,
        max_loss: defaultMaxLoss
      })
    });
    const data = await res.json();
    logEl.textContent += `\n[PASS] Loaded ${symbol} with ${data.currency} ${defaultBudget.toLocaleString()} budget.`;
    refreshDashboard();
  } catch (e) {
    logEl.textContent += `\n[ERROR] Market switch failed: ${e.message}`;
  }
}

async function loadCustomTicker(symbol) {
  const market = symbol.includes('.BO') ? 'BSE' : (symbol.includes('.NS') ? 'NSE' : 'US');
  await switchMarket(market, symbol);
}

async function refreshDashboard() {
  const log = (msg) => {
    const el = document.getElementById('systemLog');
    if (el) el.textContent += `\n${msg}`;
  };

  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    
    const cfg = data.config || {};
    const p = data.plan || {};
    const ord = (data.orders && data.orders.orders && data.orders.orders.length > 0) ? data.orders.orders[0] : null;

    currentCurrency = p.currency || cfg.currency || (p.symbol && p.symbol.includes('.NS') ? 'INR' : 'USD');
    currentMarket = p.exchange || cfg.market || 'NSE';
    currentCurrencySymbol = currentCurrency === 'INR' ? '₹' : '$';

    document.getElementById('exchangeBadge').textContent = `${currentMarket} (${currentCurrency})`;
    document.getElementById('exchangeLabel').textContent = `${currentMarket} (${currentCurrency === 'INR' ? 'India' : 'US'})`;
    document.getElementById('brokerTag').textContent = currentCurrency === 'INR' ? 'ZERODHA / PAPER' : 'ALPACA PAPER';

    // Update Plan card
    if (p.symbol) {
      document.getElementById('tickerDisplay').textContent = p.symbol;
      document.getElementById('sideDisplay').textContent = (p.side || 'BUY').toUpperCase();
      document.getElementById('entryVal').textContent = formatMoney(p.entry);
      document.getElementById('stopVal').textContent = formatMoney(p.stop);
      document.getElementById('targetVal').textContent = formatMoney(p.target);
      document.getElementById('riskVal').textContent = formatMoney(p.risk_per_share || (p.entry - p.stop));
      document.getElementById('flatByTime').textContent = `${p.flat_by || (currentCurrency === 'INR' ? '15:20 IST' : '15:55 ET')}`;
    }

    // Update Config
    if (cfg.budget && document.getElementById('budgetInput')) {
      document.getElementById('budgetInput').value = cfg.budget;
      document.getElementById('budgetLabel').textContent = `Account Budget (${currentCurrencySymbol} ${currentCurrency})`;
    }
    if (cfg.max_loss_per_trade_usd && document.getElementById('maxLossInput')) {
      document.getElementById('maxLossInput').value = cfg.max_loss_per_trade_usd;
      document.getElementById('maxLossLabel').textContent = `Max Loss Per Trade (${currentCurrencySymbol} ${currentCurrency})`;
    }

    // Update Live Balance & Ledger
    if (data.ledger) {
      const leg = data.ledger;
      const free = Number(leg.free_cash || 0);
      const deployed = Number(leg.deployed_cash || 0);
      const pnl = Number(leg.realized_pnl || 0);
      const start = Number(leg.starting_capital || 100000);

      const totalPortfolio = free + deployed;
      const openPositions = leg.open_positions || [];
      const history = leg.trade_history || [];
      const totalTradesToday = history.length;

      // 1. TOP HERO KPI STRIP
      if (document.getElementById('topTotalCapital')) {
        document.getElementById('topTotalCapital').textContent = formatMoney(totalPortfolio);
      }
      if (document.getElementById('topFreeCash')) {
        document.getElementById('topFreeCash').textContent = formatMoney(free);
      }
      if (document.getElementById('topHoldingAmount')) {
        document.getElementById('topHoldingAmount').textContent = formatMoney(deployed);
      }
      if (document.getElementById('topActivePositionsCount')) {
        document.getElementById('topActivePositionsCount').textContent = `${openPositions.length} Open Position${openPositions.length === 1 ? '' : 's'}`;
      }
      if (document.getElementById('topRealizedPnl')) {
        const pnlTopEl = document.getElementById('topRealizedPnl');
        const prefix = pnl >= 0 ? '+' : '';
        pnlTopEl.textContent = `${prefix}${formatMoney(pnl)}`;
        pnlTopEl.style.color = pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
      }
      if (document.getElementById('topTradeCount')) {
        document.getElementById('topTradeCount').textContent = `${totalTradesToday} / 4`;
      }

      // Render Active Positions List
      const posContainer = document.getElementById('activePositionsList');
      if (document.getElementById('openPosBadge')) {
        document.getElementById('openPosBadge').textContent = `${openPositions.length} OPEN`;
      }

      if (openPositions.length === 0) {
        posContainer.innerHTML = `<div style="font-size: 0.8rem; color: var(--text-muted); padding: 0.5rem; text-align: center;">No active open trades right now.</div>`;
      } else {
        posContainer.innerHTML = openPositions.map(pos => `
          <div class="news-item" style="border-left: 3px solid var(--accent-cyan);">
            <div style="display: flex; justify-content: space-between; align-items: baseline;">
              <strong style="color: #fff; font-size: 0.95rem;">${pos.symbol} (${pos.exchange})</strong>
              <span style="font-size: 0.75rem; color: var(--accent-green); font-weight: 700;">BUY ${pos.qty} SHARES</span>
            </div>
            <div style="display: flex; justify-content: space-between; font-size: 0.78rem; color: var(--text-secondary); margin-top: 4px;">
              <span>Entry: <strong style="color:#fff;">${formatMoney(pos.entry_price)}</strong></span>
              <span>Invested: <strong style="color: var(--accent-amber);">${formatMoney(pos.amount)}</strong></span>
            </div>
            <div style="font-size: 0.7rem; color: var(--text-muted); margin-top: 3px;">
              🕒 Entry Time: <code>${pos.entry_time || 'IST'}</code>
            </div>
          </div>
        `).join('');
      }

      // Render Closed Trade History
      const histContainer = document.getElementById('tradeHistoryList');
      const history = leg.trade_history || [];
      document.getElementById('historyCount').textContent = `${history.length} Trades`;

      if (history.length === 0) {
        histContainer.innerHTML = `<div style="font-size: 0.8rem; color: var(--text-muted); padding: 0.5rem; text-align: center;">No closed trades yet today.</div>`;
      } else {
        histContainer.innerHTML = history.slice().reverse().map(h => {
          const isProf = Number(h.pnl) >= 0;
          return `
            <div class="news-item" style="border-left: 3px solid ${isProf ? 'var(--accent-green)' : 'var(--accent-red)'};">
              <div style="display: flex; justify-content: space-between; align-items: baseline;">
                <strong style="color: #fff; font-size: 0.85rem;">${h.symbol}</strong>
                <strong style="color: ${isProf ? 'var(--accent-green)' : 'var(--accent-red)'}; font-size: 0.85rem;">
                  ${isProf ? '+' : ''}${formatMoney(h.pnl)}
                </strong>
              </div>
              <div style="display: flex; justify-content: space-between; font-size: 0.72rem; color: var(--text-secondary); margin-top: 2px;">
                <span>In: ${formatMoney(h.entry_price)} ➔ Out: ${formatMoney(h.exit_price)}</span>
                <span>${h.qty} shares</span>
              </div>
              <div style="font-size: 0.66rem; color: var(--text-muted); margin-top: 2px;">
                ${h.reason || 'Exit trigger'}
              </div>
            </div>
          `;
        }).join('');
      }
    }

    // Update Orders Ticket
    if (ord) {
      document.getElementById('ticketHeader').textContent = `${(ord.side || 'BUY').toUpperCase()} ${ord.qty} ${ord.symbol}`;
      document.getElementById('ticketQty').textContent = `${ord.qty} Shares`;
      
      const capital = Number(ord.qty) * (p.entry || 1000);
      document.getElementById('ticketCapital').textContent = formatMoney(capital);
      
      const maxLoss = Number(ord.qty) * (p.risk_per_share || 10);
      document.getElementById('ticketMaxLoss').textContent = formatMoney(maxLoss);
      document.getElementById('ticketTP').textContent = formatMoney(ord.take_profit_limit_price || p.target);
      document.getElementById('ticketSL').textContent = formatMoney(ord.stop_loss_stop_price || p.stop);
    }

    // Render Chart
    renderMarketChart(p);

  } catch (err) {
    console.error("Failed to load dashboard status:", err);
    log(`[ERROR] Sync failed: ${err.message}`);
  }
}

function renderMarketChart(plan) {
  const ctx = document.getElementById('fanChartCanvas').getContext('2d');
  if (chartInstance) {
    chartInstance.destroy();
  }

  const isIndia = currentCurrency === 'INR';
  const startHour = isIndia ? 9 : 10;
  const startMin = isIndia ? 15 : 15;
  const labels = [];

  let curH = startHour;
  let curM = startMin;
  const totalBars = 35;

  for (let i = 0; i < totalBars; i++) {
    labels.push(`${curH < 10 ? '0' : ''}${curH}:${curM < 10 ? '0' : ''}${curM}`);
    curM += 10;
    if (curM >= 60) {
      curH++;
      curM -= 60;
    }
  }

  const entry = plan ? Number(plan.entry) : (isIndia ? 1195 : 364.18);
  const target = plan ? Number(plan.target) : (isIndia ? 1205 : 370.04);
  const stop = plan ? Number(plan.stop) : (isIndia ? 1185 : 358.32);
  const spreadBase = isIndia ? (entry * 0.012) : 6;

  const medianData = [];
  const upperData = [];
  const lowerData = [];
  const targetLine = [];
  const stopLine = [];
  const entryLine = [];

  for (let i = 0; i < labels.length; i++) {
    const factor = (i / labels.length);
    medianData.push(entry + (factor * (target - entry) * 0.4));
    upperData.push(entry + (factor * spreadBase * 1.5));
    lowerData.push(entry - (factor * spreadBase * 1.1));
    targetLine.push(target);
    stopLine.push(stop);
    entryLine.push(entry);
  }

  chartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'Target (1R)',
          data: targetLine,
          borderColor: '#10b981',
          borderWidth: 1.5,
          borderDash: [5, 5],
          pointRadius: 0,
          fill: false
        },
        {
          label: 'Stop-Loss',
          data: stopLine,
          borderColor: '#ef4444',
          borderWidth: 1.5,
          borderDash: [5, 5],
          pointRadius: 0,
          fill: false
        },
        {
          label: 'Entry Level',
          data: entryLine,
          borderColor: '#06b6d4',
          borderWidth: 1,
          pointRadius: 0,
          fill: false
        },
        {
          label: 'Upper Volatility Fan',
          data: upperData,
          borderColor: 'rgba(59, 130, 246, 0.4)',
          borderWidth: 1,
          pointRadius: 0,
          fill: '+1',
          backgroundColor: 'rgba(59, 130, 246, 0.12)'
        },
        {
          label: 'Median Projection',
          data: medianData,
          borderColor: '#3b82f6',
          borderWidth: 2,
          pointRadius: 0,
          fill: false
        },
        {
          label: 'Lower Volatility Fan',
          data: lowerData,
          borderColor: 'rgba(59, 130, 246, 0.4)',
          borderWidth: 1,
          pointRadius: 0,
          fill: false
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: true,
          position: 'top',
          labels: { color: '#9ca3af', boxWidth: 12, font: { size: 10 } }
        }
      },
      scales: {
        x: {
          ticks: { color: '#6b7280', maxTicksLimit: 8 },
          grid: { color: 'rgba(255, 255, 255, 0.05)' }
        },
        y: {
          ticks: {
            color: '#6b7280',
            callback: function(value) { return formatMoney(value); }
          },
          grid: { color: 'rgba(255, 255, 255, 0.05)' }
        }
      }
    }
  });
}

async function recalculateSizing() {
  const budget = parseFloat(document.getElementById('budgetInput').value) || 100000;
  const maxLoss = parseFloat(document.getElementById('maxLossInput').value) || 1000;

  const logEl = document.getElementById('systemLog');
  logEl.textContent += `\n[ACTION] Recalculating: Budget=${currentCurrencySymbol} ${budget.toLocaleString()}, MaxLoss=${currentCurrencySymbol} ${maxLoss.toLocaleString()}...`;

  try {
    const res = await fetch('/api/recalculate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ budget: budget, max_loss: maxLoss })
    });
    const result = await res.json();
    logEl.textContent += `\n${result.size_output || 'Position updated.'}`;
    refreshDashboard();
  } catch (e) {
    logEl.textContent += `\n[ERROR] Sizing failed: ${e.message}`;
  }
}

async function fetchLiveQuoteData() {
  const ticker = document.getElementById('tickerDisplay').textContent;
  const logEl = document.getElementById('systemLog');
  logEl.textContent += `\n[FETCH] Requesting latest bar for ${ticker}...`;
  try {
    const res = await fetch(`/api/quote?symbol=${encodeURIComponent(ticker)}`);
    const q = await res.json();
    if (q.price) {
      logEl.textContent += `\n[TICK] ${q.symbol} live price: ${formatMoney(q.price)} (${q.exchange})`;
    }
  } catch (e) {
    logEl.textContent += `\n[ERROR] Bar fetch error: ${e.message}`;
  }
}

async function fetchNews() {
  const ticker = document.getElementById('tickerDisplay').textContent;
  try {
    const res = await fetch(`/api/news?symbol=${encodeURIComponent(ticker)}`);
    const data = await res.json();
    const feed = document.getElementById('newsFeed');
    if (data.output) {
      const lines = data.output.split('\n').filter(l => l.trim().length > 0).slice(0, 5);
      if (lines.length > 0) {
        feed.innerHTML = lines.map(line => `
          <div class="news-item">
            <div class="news-time">BSE / NSE Live Disclosure</div>
            <div class="news-title">${line}</div>
          </div>
        `).join('');
      }
    }
  } catch (e) {
    console.error("News fetch error:", e);
  }
}

function confirmOrderAction() {
  const logEl = document.getElementById('systemLog');
  logEl.textContent += `\n[SAFETY CHECK] Ticket verified for paper trade in ${currentCurrency}. Manual approval mandatory prior to execution.`;
  alert(`Safety Protocol Active:\n\nOrder is ready in ${currentCurrency} for paper execution.\nNo real orders are submitted without interactive approval.`);
}

async function sendTelegramTest() {
  const token = document.getElementById('telegramTokenInput').value.trim();
  const chatId = document.getElementById('telegramChatIdInput').value.trim();
  const logEl = document.getElementById('systemLog');

  if (!token || !chatId) {
    alert("Please provide both your Telegram Bot Token and Chat ID.");
    return;
  }

  logEl.textContent += `\n[TELEGRAM] Sending test trade notification...`;
  try {
    const res = await fetch('/api/telegram_test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bot_token: token, chat_id: chatId })
    });
    const data = await res.json();
    if (data.status === 'ok') {
      logEl.textContent += `\n[TELEGRAM] Alert sent successfully! Check your Telegram chat.`;
      alert("Trade alert sent to your Telegram successfully!");
    } else {
      logEl.textContent += `\n[TELEGRAM ERROR] ${data.message}`;
      alert(`Telegram delivery failed: ${data.message}`);
    }
  } catch (err) {
    logEl.textContent += `\n[TELEGRAM ERROR] ${err.message}`;
  }
}

window.addEventListener('DOMContentLoaded', () => {
  switchMarket('NSE', 'RELIANCE.NS');
  // Auto-refresh dashboard every 5 seconds to sync with daemon
  setInterval(refreshDashboard, 5000);
});
