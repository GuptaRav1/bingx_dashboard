/**
 * BingX Trade Terminal / Trade Dashboard Frontend Application
 * Conforms to PRD.md Section 11, 12, 13 and SYSTEM_DESIGN.md Section 9.
 */

(function () {
  const CUTOFF_DATE = "2026-09-14";

  // Application State (SYSTEM_DESIGN.md Section 9)
  const state = {
    from: CUTOFF_DATE,
    to: getIstToday(),
    allPositions: [],
    symbolFilter: "",
    sideFilter: "ALL", // "ALL", "LONG", "SHORT"
    balance: null,
    meta: null,
    derived: null,
    syncPolling: null,
    isSyncing: false,
    chartInstance: null,
    tradeChartInstance: null,
    calendarYear: null,
    calendarMonth: null, // 1-indexed (1=Jan, 12=Dec)
  };

  // ---------------------------------------------------------------------------
  // Date & Formatting Helpers
  // ---------------------------------------------------------------------------

  /** Returns today's date formatted as YYYY-MM-DD in IST (UTC+05:30) */
  function getIstToday() {
    const istTime = new Date(Date.now() + 5.5 * 3600 * 1000);
    return istTime.toISOString().slice(0, 10);
  }

  /** Formats epoch milliseconds to YYYY-MM-DD in IST (Internal dictionary key) */
  function fmtIstDateOnly(epochMs) {
    if (!epochMs) return null;
    try {
      const parts = new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Kolkata",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      }).formatToParts(new Date(epochMs));

      const map = {};
      parts.forEach((p) => {
        map[p.type] = p.value;
      });
      return `${map.year}-${map.month}-${map.day}`;
    } catch (e) {
      const istDate = new Date(epochMs + 5.5 * 3600 * 1000);
      return istDate.toISOString().slice(0, 10);
    }
  }

  /** Formats epoch milliseconds to DD/MM/YY HH:mm:ss IST */
  function fmtIst(epochMs) {
    if (!epochMs) return "—";
    try {
      const parts = new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Kolkata",
        year: "2-digit",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      }).formatToParts(new Date(epochMs));

      const map = {};
      parts.forEach((p) => {
        map[p.type] = p.value;
      });
      return `${map.day}/${map.month}/${map.year} ${map.hour}:${map.minute}:${map.second}`;
    } catch (e) {
      const istDate = new Date(epochMs + 5.5 * 3600 * 1000);
      const iso = istDate.toISOString();
      return `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(2, 4)} ${iso.slice(11, 19)}`;
    }
  }

  /** Formats epoch milliseconds to DD/MM/YY HH:mm IST */
  function fmtIstShort(epochMs) {
    if (!epochMs) return "Never";
    try {
      const parts = new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Kolkata",
        year: "2-digit",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).formatToParts(new Date(epochMs));

      const map = {};
      parts.forEach((p) => {
        map[p.type] = p.value;
      });
      return `${map.day}/${map.month}/${map.year} ${map.hour}:${map.minute} IST`;
    } catch (e) {
      const istDate = new Date(epochMs + 5.5 * 3600 * 1000);
      const iso = istDate.toISOString();
      return `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(2, 4)} ${iso.slice(11, 16)} IST`;
    }
  }

  /** Formats number with thousand separators and maximum decimal digits */
  function fmtNum(val, maxDecimals = 4) {
    if (val === null || val === undefined || isNaN(val)) return "—";
    return Number(val).toLocaleString("en-US", {
      minimumFractionDigits: 0,
      maximumFractionDigits: maxDecimals,
    });
  }

  // ---------------------------------------------------------------------------
  // Metric Computations (PRD Section 13)
  // ---------------------------------------------------------------------------

  /** Win rate: wins / total × 100 (breakevens count as non-wins) */
  function computeWinRate(positions) {
    if (!positions || positions.length === 0) return null;
    let wins = 0;
    for (const p of positions) {
      if (p.net_pnl > 0) wins++;
    }
    return ((wins / positions.length) * 100).toFixed(2) + "%";
  }

  /** Profit factor: Σ gains / |Σ losses| */
  function computeProfitFactor(positions) {
    if (!positions || positions.length === 0) return null;
    let gains = 0;
    let losses = 0;
    for (const p of positions) {
      if (p.net_pnl > 0) gains += p.net_pnl;
      else if (p.net_pnl < 0) losses += Math.abs(p.net_pnl);
    }
    if (losses === 0 && gains > 0) return "∞";
    if (losses === 0 && gains === 0) return null;
    const pf = gains / losses;
    return pf.toFixed(2).replace(/\.?0+$/, "");
  }

  /** Average PnL per trade: sum(net_pnl) / count(trades) */
  function computeAvgPnl(positions) {
    if (!positions || positions.length === 0) return null;
    const totalPnl = positions.reduce((acc, p) => acc + (p.net_pnl || 0), 0);
    return totalPnl / positions.length;
  }

  /** Longest win and loss streaks (PRD Section 13.4) */
  function computeLongestStreaks(positions) {
    if (!positions || positions.length === 0) {
      return { win: null, loss: null };
    }

    // Sort ascending by close_time
    const sorted = [...positions].sort((a, b) => (a.close_time || 0) - (b.close_time || 0));

    let maxWin = 0;
    let maxLoss = 0;
    let curWin = 0;
    let curLoss = 0;

    for (const t of sorted) {
      const pnl = t.net_pnl || 0;
      if (pnl > 0) {
        curWin += 1;
        curLoss = 0;
      } else if (pnl < 0) {
        curLoss += 1;
        curWin = 0;
      }
      // Breakeven (pnl === 0) is skipped without resetting streaks
      if (curWin > maxWin) maxWin = curWin;
      if (curLoss > maxLoss) maxLoss = curLoss;
    }

    return { win: maxWin, loss: maxLoss };
  }

  /** Cumulative PnL points (PRD Section 13.5) */
  function computeCumulativePoints(positions) {
    if (!positions || positions.length === 0) {
      return [{ x: 0, y: 0, trade: null }];
    }

    const sorted = [...positions].sort((a, b) => (a.close_time || 0) - (b.close_time || 0));

    let running = 0;
    const points = [{ x: 0, y: 0, trade: null }];

    sorted.forEach((t, idx) => {
      running += t.net_pnl || 0;
      points.push({
        x: idx + 1,
        y: running,
        trade: t,
      });
    });

    return points;
  }

  /** Calendar daily PnL mapping { "YYYY-MM-DD": { netPnl: number, count: number } } (PRD §13.6) */
  function computeCalendarData(positions) {
    const map = {};
    if (!positions) return map;

    for (const p of positions) {
      if (!p.close_time) continue;
      const dateStr = fmtIstDateOnly(p.close_time);
      if (!dateStr) continue;

      if (!map[dateStr]) {
        map[dateStr] = { netPnl: 0, count: 0 };
      }
      map[dateStr].netPnl += p.net_pnl || 0;
      map[dateStr].count += 1;
    }
    return map;
  }

  /** Max Drawdown (MDD) in USDT (Peak-to-Trough) */
  function computeMaxDrawdown(positions) {
    if (!positions || positions.length === 0) return { val: null, peak: 0 };

    const sorted = [...positions].sort((a, b) => (a.close_time || 0) - (b.close_time || 0));
    let running = 0;
    let peak = 0;
    let mdd = 0;

    for (const t of sorted) {
      running += t.net_pnl || 0;
      if (running > peak) {
        peak = running;
      }
      const dd = peak - running;
      if (dd > mdd) {
        mdd = dd;
      }
    }

    return { val: mdd, peak: peak };
  }

  /** Total Fees Paid and Fee Drag % */
  function computeFeeStats(positions) {
    if (!positions || positions.length === 0) {
      return { totalFees: null, grossProfit: null, feeDrag: null };
    }

    let totalFees = 0;
    let grossProfit = 0;

    for (const p of positions) {
      totalFees += p.fees || 0;
      if (p.gross_pnl > 0) {
        grossProfit += p.gross_pnl;
      }
    }

    const feeDrag = grossProfit > 0 ? (totalFees / grossProfit) * 100 : null;

    return { totalFees, grossProfit, feeDrag };
  }

  /** Formats duration in milliseconds into a concise readable string */
  function fmtDuration(ms) {
    if (ms === null || ms === undefined || isNaN(ms)) return "—";
    const totalSec = Math.round(ms / 1000);
    if (totalSec < 60) return `${totalSec}s`;
    const days = Math.floor(totalSec / 86400);
    const hours = Math.floor((totalSec % 86400) / 3600);
    const minutes = Math.floor((totalSec % 3600) / 60);
    const seconds = totalSec % 60;

    if (days > 0) {
      return hours > 0 ? `${days}d ${hours}h` : `${days}d`;
    }
    if (hours > 0) {
      return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
    }
    return seconds > 0 ? `${minutes}m ${seconds}s` : `${minutes}m`;
  }

  /** Average Trade Duration (Holding Time) across all, winning, and losing trades */
  function computeDurationStats(positions) {
    if (!positions || positions.length === 0) {
      return { avg: null, winAvg: null, lossAvg: null };
    }

    const valid = positions.filter((p) => p.open_time && p.close_time && p.close_time >= p.open_time);
    if (valid.length === 0) {
      return { avg: null, winAvg: null, lossAvg: null };
    }

    const totalDur = valid.reduce((acc, p) => acc + (p.close_time - p.open_time), 0);
    const avg = totalDur / valid.length;

    const wins = valid.filter((p) => p.net_pnl > 0);
    const winAvg = wins.length > 0 ? wins.reduce((acc, p) => acc + (p.close_time - p.open_time), 0) / wins.length : null;

    const losses = valid.filter((p) => p.net_pnl < 0);
    const lossAvg = losses.length > 0 ? losses.reduce((acc, p) => acc + (p.close_time - p.open_time), 0) / losses.length : null;

    return { avg, winAvg, lossAvg };
  }

  /** Master computation pipeline (SYSTEM_DESIGN.md Section 9) */
  function computeAll(positions) {
    return {
      metrics: {
        winRate: computeWinRate(positions),
        profitFactor: computeProfitFactor(positions),
        avgPnl: computeAvgPnl(positions),
        longestWin: computeLongestStreaks(positions).win,
        longestLoss: computeLongestStreaks(positions).loss,
        mdd: computeMaxDrawdown(positions),
        fees: computeFeeStats(positions),
        duration: computeDurationStats(positions),
      },
      chartPoints: computeCumulativePoints(positions),
      calendar: computeCalendarData(positions),
    };
  }

  // ---------------------------------------------------------------------------
  // UI Renderers
  // ---------------------------------------------------------------------------

  function renderBalance(bal) {
    const balEl = document.getElementById("metric-balance");
    const subEl = document.getElementById("metric-balance-subtitle");
    if (!balEl || !subEl) return;

    if (!bal || bal.balance === null) {
      balEl.textContent = "—";
      subEl.textContent = "not synced yet";
      return;
    }

    balEl.textContent = fmtNum(bal.balance, 4);
    subEl.textContent = `as of ${fmtIstShort(bal.ts)}`;
  }

  function renderMetrics(metrics, positions) {
    const posList = positions || state.allPositions || [];
    const totalCount = posList.length;

    // Win Rate & Radial indicator
    const wrEl = document.getElementById("metric-win-rate");
    const totalEl = document.getElementById("metric-total-trades");
    const subEl = document.getElementById("metric-win-rate-subtitle");
    const circleEl = document.getElementById("metric-win-rate-circle");

    if (wrEl) wrEl.textContent = metrics.winRate !== null ? metrics.winRate : "—";
    if (totalEl) totalEl.textContent = `${totalCount} total`;

    let wins = 0;
    let losses = 0;
    posList.forEach((p) => {
      if (p.net_pnl > 0) wins++;
      else if (p.net_pnl < 0) losses++;
    });

    const winPct = totalCount > 0 ? ((wins / totalCount) * 100).toFixed(1) : "0";
    if (circleEl) {
      circleEl.setAttribute("stroke-dasharray", `${winPct}, 100`);
    }

    if (subEl) {
      if (totalCount > 0) {
        subEl.innerHTML = `
          <span class="text-emerald-400 font-medium">${wins}W</span>
          <span class="text-neutral-600">/</span>
          <span class="text-rose-400 font-medium">${losses}L</span>
          <span class="text-neutral-400 font-mono">(${winPct}%)</span>
        `;
      } else {
        subEl.innerHTML = `<span class="text-neutral-500">wins / total trades</span>`;
      }
    }

    // Profit Factor
    const pfEl = document.getElementById("metric-profit-factor");
    const pfBadge = document.getElementById("metric-profit-factor-badge");
    if (pfEl) pfEl.textContent = metrics.profitFactor !== null ? metrics.profitFactor : "—";
    if (pfBadge) {
      const numPf = parseFloat(metrics.profitFactor);
      if (metrics.profitFactor === "∞" || (!isNaN(numPf) && numPf >= 1.5)) {
        pfBadge.textContent = "> 1.5 Healthy";
        pfBadge.className = "px-1.5 py-0.5 rounded text-[10px] bg-emerald-500/10 text-emerald-400 font-medium border border-emerald-500/20";
      } else {
        pfBadge.textContent = "Normal";
        pfBadge.className = "px-1.5 py-0.5 rounded text-[10px] bg-neutral-900 text-neutral-400 font-medium border border-neutral-800";
      }
    }

    // Avg PnL
    const avgEl = document.getElementById("metric-avg-pnl");
    if (avgEl) {
      if (metrics.avgPnl === null) {
        avgEl.textContent = "—";
        avgEl.className = "text-2xl font-bold tracking-tight text-white font-mono tabular-nums";
      } else {
        const valStr = `${metrics.avgPnl >= 0 ? "+" : ""}${fmtNum(metrics.avgPnl, 4)}`;
        avgEl.textContent = valStr;
        avgEl.className = `text-2xl font-bold tracking-tight font-mono tabular-nums ${metrics.avgPnl >= 0 ? "text-emerald-400" : "text-rose-400"}`;
      }
    }

    // Streaks
    const winStrkEl = document.getElementById("metric-win-streak");
    if (winStrkEl) {
      winStrkEl.textContent = metrics.longestWin !== null ? metrics.longestWin : "—";
    }

    const lossStrkEl = document.getElementById("metric-loss-streak");
    if (lossStrkEl) {
      lossStrkEl.textContent = metrics.longestLoss !== null ? metrics.longestLoss : "—";
    }

    // Max Drawdown (MDD)
    const mddEl = document.getElementById("metric-mdd");
    const mddSub = document.getElementById("metric-mdd-subtitle");
    if (mddEl) {
      if (metrics.mdd && metrics.mdd.val !== null) {
        if (metrics.mdd.val > 0) {
          mddEl.textContent = `-${fmtNum(metrics.mdd.val, 4)}`;
          mddEl.className = "text-2xl font-bold tracking-tight text-rose-400 font-mono tabular-nums";
        } else {
          mddEl.textContent = "0.0000";
          mddEl.className = "text-2xl font-bold tracking-tight text-neutral-400 font-mono tabular-nums";
        }
      } else {
        mddEl.textContent = "—";
        mddEl.className = "text-2xl font-bold tracking-tight text-white font-mono tabular-nums";
      }
    }
    if (mddSub && metrics.mdd && metrics.mdd.val !== null) {
      mddSub.textContent = metrics.mdd.val > 0 ? "Peak-to-trough decline from ATH" : "Zero drawdown in period";
    }

    // Total Fees Paid & Fee Drag %
    const feeEl = document.getElementById("metric-total-fees");
    const feeBadge = document.getElementById("metric-fee-drag-badge");
    const feeSub = document.getElementById("metric-fee-subtitle");
    if (feeEl) {
      feeEl.textContent = metrics.fees && metrics.fees.totalFees !== null ? fmtNum(metrics.fees.totalFees, 4) : "—";
    }
    if (feeBadge) {
      if (metrics.fees && metrics.fees.feeDrag !== null) {
        feeBadge.textContent = `FEE DRAG: ${metrics.fees.feeDrag.toFixed(1)}%`;
        feeBadge.className = "px-1.5 py-0.5 rounded text-[10px] bg-amber-500/10 text-amber-400 font-mono font-medium border border-amber-500/20";
      } else {
        feeBadge.textContent = "FEE DRAG: —";
        feeBadge.className = "px-1.5 py-0.5 rounded text-[10px] bg-neutral-900 text-neutral-400 font-mono font-medium border border-neutral-800";
      }
    }
    if (feeSub) {
      if (metrics.fees && metrics.fees.feeDrag !== null) {
        feeSub.textContent = `${metrics.fees.feeDrag.toFixed(1)}% of gross gains eaten by fees`;
      } else {
        feeSub.textContent = "Total commission paid to broker";
      }
    }

    // Average Trade Duration (Holding Time)
    const durEl = document.getElementById("metric-avg-duration");
    const durSub = document.getElementById("metric-duration-subtitle");
    if (durEl) {
      durEl.textContent = metrics.duration && metrics.duration.avg !== null ? fmtDuration(metrics.duration.avg) : "—";
    }
    if (durSub) {
      if (metrics.duration && (metrics.duration.winAvg !== null || metrics.duration.lossAvg !== null)) {
        durSub.innerHTML = `
          <span class="text-emerald-400">Win: ${fmtDuration(metrics.duration.winAvg)}</span>
          <span class="text-neutral-600">|</span>
          <span class="text-rose-400">Loss: ${fmtDuration(metrics.duration.lossAvg)}</span>
        `;
      } else {
        durSub.textContent = "Win: — | Loss: —";
      }
    }
  }

  function renderChart(chartPoints) {
    const canvas = document.getElementById("pnl-chart");
    const badge = document.getElementById("chart-total-pnl");
    if (!canvas || typeof Chart === "undefined") return;

    const finalPnl = chartPoints.length > 0 ? chartPoints[chartPoints.length - 1].y : 0;
    const isPositive = finalPnl >= 0;

    // Update Header Badge
    if (badge) {
      const pnlFormatted = `${finalPnl >= 0 ? "+" : ""}${fmtNum(finalPnl, 4)} USDT`;
      badge.innerHTML = `<span>${pnlFormatted}</span>`;
      badge.className = `flex items-center gap-1.5 px-3.5 py-1.5 rounded-full font-mono text-xs font-semibold shrink-0 ${finalPnl >= 0
        ? "bg-emerald-500/10 border border-emerald-500/30 text-emerald-400"
        : "bg-rose-500/10 border border-rose-500/30 text-rose-400"
        }`;
    }

    const labels = chartPoints.map((p) => (p.x === 0 ? "0" : `#${p.x}`));
    const dataValues = chartPoints.map((p) => p.y);

    const strokeColor = isPositive ? "#00F0A8" : "#FF4D6D";

    if (state.chartInstance) {
      state.chartInstance.destroy();
      state.chartInstance = null;
    }

    const ctx = canvas.getContext("2d");
    const gradient = ctx.createLinearGradient(0, 0, 0, 360);
    if (isPositive) {
      gradient.addColorStop(0, "rgba(0, 240, 168, 0.32)");
      gradient.addColorStop(0.5, "rgba(0, 240, 168, 0.08)");
      gradient.addColorStop(1, "rgba(0, 240, 168, 0.0)");
    } else {
      gradient.addColorStop(0, "rgba(255, 77, 109, 0.32)");
      gradient.addColorStop(0.5, "rgba(255, 77, 109, 0.08)");
      gradient.addColorStop(1, "rgba(255, 77, 109, 0.0)");
    }

    state.chartInstance = new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Cumulative PnL",
            data: dataValues,
            borderColor: strokeColor,
            backgroundColor: gradient,
            borderWidth: 2.5,
            fill: true,
            tension: 0,
            pointRadius: chartPoints.length > 60 ? 0 : 3,
            pointHoverRadius: 6,
            pointBackgroundColor: strokeColor,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        scales: {
          x: {
            grid: { color: "rgba(255, 255, 255, 0.05)" },
            ticks: {
              color: "#737373",
              font: { family: 'JetBrains Mono, monospace', size: 11 },
              maxTicksLimit: 15,
            },
            title: {
              display: true,
              text: "Trade #",
              color: "#A3A3A3",
              font: { size: 11, weight: "bold" },
            },
          },
          y: {
            grid: { color: "rgba(255, 255, 255, 0.05)" },
            ticks: {
              color: "#737373",
              font: { family: 'JetBrains Mono, monospace', size: 11 },
              callback: (val) => Number(val).toLocaleString() + " USDT",
            },
            title: {
              display: true,
              text: "Cumulative PnL",
              color: "#A3A3A3",
              font: { size: 11, weight: "bold" },
            },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "#121212",
            titleColor: "#E5E5E5",
            bodyColor: isPositive ? "#00F0A8" : "#FF4D6D",
            borderColor: "rgba(255, 255, 255, 0.12)",
            borderWidth: 1,
            padding: 12,
            displayColors: false,
            callbacks: {
              title: function (items) {
                const idx = items[0].dataIndex;
                const pt = chartPoints[idx];
                if (!pt || !pt.trade) return "Baseline (0 USDT)";
                return `Trade #${pt.x} (${pt.trade.symbol} ${pt.trade.direction})`;
              },
              label: function (item) {
                const idx = item.dataIndex;
                const pt = chartPoints[idx];
                if (!pt || !pt.trade) return "Cum PnL: 0.0000 USDT";
                const cumStr = `${pt.y >= 0 ? "+" : ""}${fmtNum(pt.y, 4)} USDT`;
                const trdStr = `${pt.trade.net_pnl >= 0 ? "+" : ""}${fmtNum(pt.trade.net_pnl, 4)} USDT`;
                return [
                  `Cumulative PnL: ${cumStr}`,
                  `Trade Net PnL: ${trdStr}`,
                  `Closed: ${fmtIst(pt.trade.close_time)}`,
                ];
              },
            },
          },
        },
      },
    });
  }

  function renderTradeBarChart(positions) {
    const canvas = document.getElementById("trade-pnl-chart");
    const summaryBadge = document.getElementById("chart-trade-pnl-summary");
    if (!canvas || typeof Chart === "undefined") return;

    const list = positions || [];
    // Sort ascending by close_time (chronological execution order)
    const sorted = [...list].sort((a, b) => (a.close_time || 0) - (b.close_time || 0));

    let wins = 0;
    let losses = 0;
    sorted.forEach((p) => {
      if ((p.net_pnl || 0) > 0) wins++;
      else if ((p.net_pnl || 0) < 0) losses++;
    });

    if (summaryBadge) {
      if (sorted.length > 0) {
        summaryBadge.innerHTML = `
          <span>${sorted.length} Trades</span>
          <span class="text-neutral-500 font-sans font-normal">|</span>
          <span class="text-emerald-400 font-bold">${wins}W</span>
          <span class="text-neutral-500 font-sans font-normal">/</span>
          <span class="text-rose-400 font-bold">${losses}L</span>
        `;
        summaryBadge.className = "flex items-center gap-1.5 px-3 py-1 rounded-full bg-neutral-900 border border-neutral-800 text-neutral-300 font-mono text-xs font-semibold shrink-0";
      } else {
        summaryBadge.innerHTML = `<span>0 Trades</span>`;
        summaryBadge.className = "flex items-center gap-1.5 px-3 py-1 rounded-full bg-neutral-900 border border-neutral-800 text-neutral-400 font-mono text-xs font-semibold shrink-0";
      }
    }

    if (state.tradeChartInstance) {
      state.tradeChartInstance.destroy();
      state.tradeChartInstance = null;
    }

    const labels = sorted.map((_, idx) => `#${idx + 1}`);
    const dataValues = sorted.map((p) => p.net_pnl || 0);
    const bgColors = dataValues.map((val) =>
      val >= 0 ? "rgba(0, 240, 168, 0.85)" : "rgba(255, 77, 109, 0.85)"
    );
    const borderColors = dataValues.map((val) => (val >= 0 ? "#00F0A8" : "#FF4D6D"));

    const ctx = canvas.getContext("2d");
    state.tradeChartInstance = new Chart(ctx, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Net PnL",
            data: dataValues,
            backgroundColor: bgColors,
            borderColor: borderColors,
            borderWidth: 1,
            borderRadius: 4,
            maxBarThickness: 32,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        scales: {
          x: {
            grid: { color: "rgba(255, 255, 255, 0.05)" },
            ticks: {
              color: "#737373",
              font: { family: 'JetBrains Mono, monospace', size: 11 },
              maxTicksLimit: 15,
            },
            title: {
              display: true,
              text: "Trade #",
              color: "#A3A3A3",
              font: { size: 11, weight: "bold" },
            },
          },
          y: {
            grid: { color: "rgba(255, 255, 255, 0.05)" },
            ticks: {
              color: "#737373",
              font: { family: 'JetBrains Mono, monospace', size: 11 },
              callback: (val) => Number(val).toLocaleString() + " USDT",
            },
            title: {
              display: true,
              text: "Net PnL",
              color: "#A3A3A3",
              font: { size: 11, weight: "bold" },
            },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "#121212",
            titleColor: "#E5E5E5",
            bodyColor: "#A3A3A3",
            borderColor: "rgba(255, 255, 255, 0.12)",
            borderWidth: 1,
            padding: 12,
            displayColors: false,
            callbacks: {
              title: function (items) {
                const idx = items[0].dataIndex;
                const t = sorted[idx];
                if (!t) return "";
                return `Trade #${idx + 1} (${t.symbol} ${t.direction})`;
              },
              label: function (item) {
                const idx = item.dataIndex;
                const t = sorted[idx];
                if (!t) return "";
                const netSign = t.net_pnl >= 0 ? "+" : "";
                const grossSign = t.gross_pnl >= 0 ? "+" : "";
                return [
                  `Net PnL: ${netSign}${fmtNum(t.net_pnl, 4)} USDT`,
                  `Gross PnL: ${grossSign}${fmtNum(t.gross_pnl, 4)} USDT`,
                  `Fee: ${fmtNum(t.fees, 4)} USDT`,
                  `Closed: ${fmtIst(t.close_time)}`,
                ];
              },
            },
          },
        },
      },
    });
  }

  // ---------------------------------------------------------------------------
  // Calendar Grid Renderer (PRD §11.5 & §13.6)
  // ---------------------------------------------------------------------------

  function renderCalendar(calendarMap) {
    const tbody = document.getElementById("calendar-body");
    const labelEl = document.getElementById("cal-month-label");
    const totalEl = document.getElementById("cal-month-total");
    if (!tbody || !labelEl) return;

    const year = state.calendarYear;
    const month = state.calendarMonth; // 1-indexed

    // Format Month Label
    const monthDate = new Date(Date.UTC(year, month - 1, 1));
    const monthName = monthDate.toLocaleString("en-US", { month: "long", timeZone: "UTC" });
    labelEl.textContent = `${monthName} ${year}`;

    // Month Boundaries
    const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
    const firstDayDow = new Date(Date.UTC(year, month - 1, 1)).getUTCDay(); // 0=Sun, 6=Sat
    const daysInPrevMonth = new Date(Date.UTC(year, month - 1, 0)).getUTCDate();

    let currentDay = 1;
    let nextMonthDay = 1;
    let monthTotal = 0;
    let monthHasTrades = false;

    const rows = [];
    let done = false;
    let weekNumber = 1;

    while (!done) {
      let weekHtml = "";
      let weekTotal = 0;
      let weekTradesCount = 0;
      let weekHasTrades = false;

      for (let dow = 0; dow < 7; dow++) {
        // 1. Trailing days of previous month
        if (rows.length === 0 && dow < firstDayDow) {
          const prevD = daysInPrevMonth - firstDayDow + dow + 1;
          weekHtml += `
            <div class="cal-cell cal-cell-adjacent">
              <span class="text-xs font-mono text-neutral-600">${prevD}</span>
              <div></div>
            </div>
          `;
        }
        // 2. Days of current month
        else if (currentDay <= daysInMonth) {
          const dayStr = `${year}-${String(month).padStart(2, "0")}-${String(currentDay).padStart(2, "0")}`;
          const isWithinFilter = dayStr >= state.from && dayStr <= state.to;
          const dayData = isWithinFilter && calendarMap ? calendarMap[dayStr] : null;

          if (dayData && dayData.count > 0) {
            const val = Number(dayData.netPnl.toFixed(4));
            const isPos = val >= 0;
            const sign = isPos ? "+" : "";
            const cellClass = isPos ? "cal-cell cal-cell-pos" : "cal-cell cal-cell-neg";
            const textClass = isPos ? "text-emerald-400" : "text-rose-400";
            const dotClass = isPos ? "bg-emerald-400" : "bg-rose-500";

            weekHtml += `
              <div class="${cellClass}">
                <div class="flex justify-between items-center">
                  <span class="text-xs font-mono text-white font-semibold">${currentDay}</span>
                  <span class="w-2 h-2 rounded-full ${dotClass}"></span>
                </div>
                <div class="flex justify-between items-baseline">
                  <span class="text-xs text-neutral-500 font-mono font-medium">${dayData.count}T</span>
                  <span class="font-mono text-xs font-bold ${textClass}">${sign}${fmtNum(val, 2)}</span>
                </div>
              </div>
            `;
            weekTotal += val;
            weekTradesCount += dayData.count;
            weekHasTrades = true;
            monthTotal += val;
            monthHasTrades = true;
          } else {
            weekHtml += `
              <div class="cal-cell cal-cell-empty">
                <span class="text-xs font-mono text-neutral-500">${currentDay}</span>
                <div></div>
              </div>
            `;
          }
          currentDay++;
        }
        // 3. Leading days of next month
        else {
          weekHtml += `
            <div class="cal-cell cal-cell-adjacent">
              <span class="text-xs font-mono text-neutral-600">${nextMonthDay}</span>
              <div></div>
            </div>
          `;
          nextMonthDay++;
        }
      }

      // Week Total Column (Column 8, PRD §11.5)
      let weekTotalContent = "";
      if (weekHasTrades) {
        const wVal = Number(weekTotal.toFixed(4));
        const wCls = wVal >= 0 ? "text-emerald-400" : "text-rose-400";
        const wSign = wVal >= 0 ? "+" : "";
        weekTotalContent = `
          <div class="flex justify-between items-baseline w-full">
            <span class="text-xs text-neutral-500 font-mono font-medium">${weekTradesCount}T</span>
            <span class="font-mono text-xs font-bold ${wCls}">${wSign}${fmtNum(wVal, 2)}</span>
          </div>
        `;
      } else {
        weekTotalContent = `
          <div class="flex justify-between items-baseline w-full">
            <span class="text-xs text-neutral-600 font-mono">0T</span>
            <span class="text-xs font-mono text-neutral-500">0.00</span>
          </div>
        `;
      }

      weekHtml += `
        <div class="cal-cell cal-cell-total flex flex-col justify-between">
          <div class="flex justify-between items-center">
            <span class="text-[10px] text-neutral-400 uppercase font-mono tracking-wider font-semibold">W${weekNumber}</span>
          </div>
          ${weekTotalContent}
        </div>
      `;

      rows.push(`<div class="grid grid-cols-8 gap-2 mb-2">${weekHtml}</div>`);
      weekNumber++;

      if (currentDay > daysInMonth) {
        done = true;
      }
    }

    tbody.innerHTML = `<tr><td colspan="8">${rows.join("")}</td></tr>`;

    // Update Month Total Summary
    if (totalEl) {
      if (monthHasTrades) {
        const mSign = monthTotal >= 0 ? "+" : "";
        const mCls = monthTotal >= 0 ? "text-emerald-400" : "text-rose-400";
        totalEl.innerHTML = `
          <span class="text-neutral-400 font-sans font-medium">Month Total:</span>
          <span class="font-bold text-sm font-mono ${mCls}">${mSign}${fmtNum(monthTotal, 2)}</span>
        `;
      } else {
        totalEl.innerHTML = `
          <span class="text-neutral-400 font-sans font-medium">Month Total:</span>
          <span class="text-neutral-500 font-mono font-bold text-sm">0.00</span>
        `;
      }
    }
  }

  function setupCalendarNav() {
    const prevBtn = document.getElementById("cal-prev-btn");
    const nextBtn = document.getElementById("cal-next-btn");

    if (prevBtn) {
      prevBtn.addEventListener("click", () => {
        state.calendarMonth--;
        if (state.calendarMonth < 1) {
          state.calendarMonth = 12;
          state.calendarYear--;
        }
        if (state.derived) {
          renderCalendar(state.derived.calendar);
        }
      });
    }

    if (nextBtn) {
      nextBtn.addEventListener("click", () => {
        state.calendarMonth++;
        if (state.calendarMonth > 12) {
          state.calendarMonth = 1;
          state.calendarYear++;
        }
        if (state.derived) {
          renderCalendar(state.derived.calendar);
        }
      });
    }
  }

  // ---------------------------------------------------------------------------
  // Table Renderer (PRD §11.6 - Order ID and Leverage Removed)
  // ---------------------------------------------------------------------------

  function renderTable(positions) {
    const tbody = document.getElementById("positions-body");
    const tableCountBadge = document.getElementById("table-count-badge");
    const showingCount = document.getElementById("table-showing-count");
    if (!tbody) return;

    let filtered = positions || [];

    // Filter by Symbol
    if (state.symbolFilter) {
      const q = state.symbolFilter.trim().toLowerCase();
      filtered = filtered.filter((p) => (p.symbol || "").toLowerCase().includes(q));
    }

    // Filter by Side (Direction)
    if (state.sideFilter && state.sideFilter !== "ALL") {
      filtered = filtered.filter((p) => (p.direction || "").toUpperCase() === state.sideFilter);
    }

    if (tableCountBadge) {
      tableCountBadge.textContent = `${filtered.length} Execution${filtered.length === 1 ? "" : "s"}`;
    }
    if (showingCount) {
      showingCount.textContent = filtered.length;
    }

    if (filtered.length === 0) {
      tbody.innerHTML = `<tr><td colspan="10" class="empty-cell text-center py-8 text-neutral-400">No trades in this range.</td></tr>`;
      return;
    }

    const rows = filtered.map((p) => {
      const isLong = (p.direction || "").toUpperCase() === "LONG";
      const badgeClass = isLong ? "badge-long" : "badge-short";
      const directionBadge = `<span class="${badgeClass}">${p.direction || "—"}</span>`;

      const netClass = p.net_pnl > 0 ? "text-emerald-400" : p.net_pnl < 0 ? "text-rose-400" : "text-neutral-400";
      const netSign = p.net_pnl > 0 ? "+" : "";

      const flaggedClass = p.note ? "flagged" : "";
      const noteHtml = p.note ? `<span class="note-cell">${p.note}</span>` : '<span class="text-neutral-600">—</span>';

      return `
        <tr class="trade-row hover:bg-neutral-900/60 transition-colors group ${flaggedClass}" data-pos-id="${p.position_id}">
          <td class="py-3 px-4 font-sans font-semibold text-white col-symbol">
            <div class="flex items-center gap-1.5">
              <span>${p.symbol}</span>
              <span class="accordion-toggle" title="Toggle details">▾</span>
            </div>
          </td>
          <td class="py-3 px-4 col-direction">${directionBadge}</td>
          <td class="py-3 px-4 text-right font-medium col-quantity">${fmtNum(p.quantity, 6)}</td>
          <td class="py-3 px-4 text-neutral-400 col-desktop col-opentime">${fmtIst(p.open_time)}</td>
          <td class="py-3 px-4 text-neutral-400 col-closetime">${fmtIst(p.close_time)}</td>
          <td class="py-3 px-4 text-right text-neutral-300 col-desktop col-entryprice">${p.entry_price !== null ? fmtNum(p.entry_price, 8) : "—"}</td>
          <td class="py-3 px-4 text-right text-neutral-300 col-desktop col-exitprice">${fmtNum(p.exit_price, 8)}</td>
          <td class="py-3 px-4 text-right text-neutral-400 col-desktop col-fee">${fmtNum(p.fees, 4)}</td>
          <td class="py-3 px-4 text-right font-bold ${netClass} col-netpnl">${netSign}${fmtNum(p.net_pnl, 4)}</td>
          <td class="py-3 px-4 col-desktop col-note">${noteHtml}</td>
        </tr>
        <tr class="trade-detail-row hidden ${flaggedClass}" id="detail-${p.position_id}">
          <td colspan="10">
            <div class="trade-detail-content">
              <div class="detail-grid">
                <div class="detail-field">
                  <span class="detail-label">Instrument</span>
                  <span class="detail-val font-semibold text-white">${p.symbol}</span>
                </div>
                <div class="detail-field">
                  <span class="detail-label">Side</span>
                  <span class="detail-val">${directionBadge}</span>
                </div>
                <div class="detail-field">
                  <span class="detail-label">Quantity</span>
                  <span class="detail-val font-mono">${fmtNum(p.quantity, 6)}</span>
                </div>
                <div class="detail-field">
                  <span class="detail-label">Entry Price</span>
                  <span class="detail-val font-mono">${p.entry_price !== null ? fmtNum(p.entry_price, 8) : "—"}</span>
                </div>
                <div class="detail-field">
                  <span class="detail-label">Exit Price</span>
                  <span class="detail-val font-mono">${fmtNum(p.exit_price, 8)}</span>
                </div>
                <div class="detail-field">
                  <span class="detail-label">Fee</span>
                  <span class="detail-val font-mono">${fmtNum(p.fees, 4)}</span>
                </div>
                <div class="detail-field">
                  <span class="detail-label">Gross PnL</span>
                  <span class="detail-val font-mono">${fmtNum(p.gross_pnl, 4)}</span>
                </div>
                <div class="detail-field">
                  <span class="detail-label">Net PnL</span>
                  <span class="detail-val font-mono font-bold ${netClass}">${netSign}${fmtNum(p.net_pnl, 4)}</span>
                </div>
                <div class="detail-field">
                  <span class="detail-label">Entry Time (IST)</span>
                  <span class="detail-val font-mono">${fmtIst(p.open_time)}</span>
                </div>
                <div class="detail-field">
                  <span class="detail-label">Exit Time (IST)</span>
                  <span class="detail-val font-mono">${fmtIst(p.close_time)}</span>
                </div>
                ${p.note ? `
                <div class="detail-field detail-field-full">
                  <span class="detail-label">Note / Flag</span>
                  <span class="detail-val note-cell">${p.note}</span>
                </div>` : ""}
              </div>
            </div>
          </td>
        </tr>
      `;
    });

    tbody.innerHTML = rows.join("");
  }

  function setupTableAccordion() {
    const tbody = document.getElementById("positions-body");
    if (!tbody) return;

    tbody.addEventListener("click", (e) => {
      // Accordion expansion is active on screens < 1024px (PRD §11.6)
      if (window.innerWidth >= 1024) return;

      const row = e.target.closest(".trade-row");
      if (!row) return;

      const posId = row.getAttribute("data-pos-id");
      if (!posId) return;

      const detailRow = document.getElementById(`detail-${posId}`);
      if (!detailRow) return;

      const isExpanded = !detailRow.classList.contains("hidden");
      if (isExpanded) {
        detailRow.classList.add("hidden");
        row.classList.remove("expanded");
      } else {
        detailRow.classList.remove("hidden");
        row.classList.add("expanded");
      }
    });
  }

  function setupTableControls() {
    const searchInput = document.getElementById("symbol-search-input");
    const btnAll = document.getElementById("filter-side-all");
    const btnLong = document.getElementById("filter-side-long");
    const btnShort = document.getElementById("filter-side-short");
    const exportBtn = document.getElementById("export-csv-btn");

    if (searchInput) {
      searchInput.addEventListener("input", (e) => {
        state.symbolFilter = e.target.value;
        renderTable(state.allPositions);
      });
    }

    function updateSideFilter(side) {
      state.sideFilter = side;
      const btns = [
        { el: btnAll, side: "ALL" },
        { el: btnLong, side: "LONG" },
        { el: btnShort, side: "SHORT" },
      ];
      btns.forEach(({ el, side: s }) => {
        if (!el) return;
        if (s === side) {
          el.className = "px-2.5 py-1 bg-neutral-800 text-white rounded shadow-sm transition";
        } else {
          el.className = `px-2.5 py-1 ${s === "LONG" ? "hover:text-emerald-400" : s === "SHORT" ? "hover:text-rose-400" : "hover:text-white"} text-neutral-400 transition-colors`;
        }
      });
      renderTable(state.allPositions);
    }

    if (btnAll) btnAll.addEventListener("click", () => updateSideFilter("ALL"));
    if (btnLong) btnLong.addEventListener("click", () => updateSideFilter("LONG"));
    if (btnShort) btnShort.addEventListener("click", () => updateSideFilter("SHORT"));

    if (exportBtn) {
      exportBtn.addEventListener("click", exportTradesToCsv);
    }
  }

  function exportTradesToCsv() {
    const positions = state.allPositions || [];
    if (positions.length === 0) {
      showToast("No trades to export.");
      return;
    }

    const headers = [
      "Position ID",
      "Symbol",
      "Direction",
      "Quantity",
      "Entry Time (IST)",
      "Exit Time (IST)",
      "Entry Price",
      "Exit Price",
      "Fee",
      "Gross PnL",
      "Net PnL",
      "Note",
    ];

    const rows = positions.map((p) => [
      `"${p.position_id}"`,
      `"${p.symbol}"`,
      `"${p.direction}"`,
      p.quantity,
      `"${fmtIst(p.open_time)}"`,
      `"${fmtIst(p.close_time)}"`,
      p.entry_price !== null ? p.entry_price : "",
      p.exit_price,
      p.fees,
      p.gross_pnl,
      p.net_pnl,
      `"${p.note || ""}"`,
    ]);

    const csvContent = [headers.join(","), ...rows.map((r) => r.join(","))].join("\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", `trades_${state.from}_to_${state.to}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  function showToast(message, duration = 3000) {
    const toast = document.getElementById("toast");
    if (!toast) return;
    toast.textContent = message;
    toast.classList.remove("hidden");
    toast.style.opacity = "1";
    setTimeout(() => {
      toast.style.opacity = "0";
      setTimeout(() => toast.classList.add("hidden"), 300);
    }, duration);
  }

  function setBanner(type, htmlContent, autoDismissMs = 0) {
    const banner = document.getElementById("sync-banner");
    if (!banner) return;

    if (!type) {
      banner.className = "banner hidden";
      banner.innerHTML = "";
      return;
    }

    banner.className = `banner banner-${type}`;
    banner.innerHTML = htmlContent;
    banner.classList.remove("hidden");
    banner.style.opacity = "1";

    if (autoDismissMs > 0) {
      setTimeout(() => {
        banner.style.opacity = "0";
        setTimeout(() => banner.classList.add("hidden"), 300);
      }, autoDismissMs);
    }
  }

  function updateSyncIndicator(lastSyncedAt) {
    const dot = document.getElementById("sync-dot");
    const ping = document.getElementById("sync-ping");
    const text = document.getElementById("sync-time-text");
    if (!dot || !text) return;

    if (!lastSyncedAt) {
      dot.className = "relative inline-flex rounded-full h-2.5 w-2.5 bg-rose-500";
      if (ping) ping.className = "hidden";
      text.textContent = "Never";
      text.className = "font-mono text-rose-400 font-semibold";
      return;
    }

    const ageMs = Date.now() - Number(lastSyncedAt);
    const dayMs = 24 * 3600 * 1000;

    if (ageMs < dayMs) {
      dot.className = "relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500";
      if (ping) ping.className = "animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75";
      text.className = "font-mono text-emerald-400 font-semibold";
    } else if (ageMs <= 7 * dayMs) {
      dot.className = "relative inline-flex rounded-full h-2.5 w-2.5 bg-yellow-500";
      if (ping) ping.className = "animate-ping absolute inline-flex h-full w-full rounded-full bg-yellow-400 opacity-75";
      text.className = "font-mono text-yellow-400 font-semibold";
    } else {
      dot.className = "relative inline-flex rounded-full h-2.5 w-2.5 bg-rose-500";
      if (ping) ping.className = "hidden";
      text.className = "font-mono text-rose-400 font-semibold";
    }

    text.textContent = fmtIstShort(lastSyncedAt);
  }

  // ---------------------------------------------------------------------------
  // Data Fetching & State Pipeline
  // ---------------------------------------------------------------------------

  async function fetchMeta() {
    try {
      const res = await fetch("/api/meta");
      if (res.ok) {
        state.meta = await res.json();
        updateSyncIndicator(state.meta.last_synced_at);
        return state.meta;
      }
    } catch (e) {
      console.warn("Failed to fetch meta:", e);
    }
    return null;
  }

  async function fetchBalance() {
    try {
      const res = await fetch("/api/balance");
      if (res.ok) {
        state.balance = await res.json();
        renderBalance(state.balance);
        return state.balance;
      }
    } catch (e) {
      console.warn("Failed to fetch balance:", e);
    }
    return null;
  }

  async function fetchPositions() {
    const tbody = document.getElementById("positions-body");
    const countBadge = document.getElementById("trade-count-badge");

    try {
      const res = await fetch(`/api/positions?from=${encodeURIComponent(state.from)}&to=${encodeURIComponent(state.to)}`);

      if (!res.ok) {
        const errData = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
        throw new Error(errData.error || "Failed to fetch positions");
      }

      state.allPositions = await res.json();

      // Compute derived metrics, chart points, & calendar map
      state.derived = computeAll(state.allPositions);

      // Render all widgets (SYSTEM_DESIGN.md Section 9)
      renderMetrics(state.derived.metrics, state.allPositions);
      renderChart(state.derived.chartPoints);
      renderTradeBarChart(state.allPositions);
      renderCalendar(state.derived.calendar);
      renderTable(state.allPositions);

      if (countBadge) {
        countBadge.textContent = `${state.allPositions.length} Trade${state.allPositions.length === 1 ? "" : "s"}`;
      }
    } catch (err) {
      console.error("Error fetching positions:", err);
      if (tbody) {
        tbody.innerHTML = `<tr><td colspan="10" class="empty-cell text-center py-8 text-rose-400">Error: ${err.message}</td></tr>`;
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Sync Orchestration & Polling (PRD §11.7 & §11.8)
  // ---------------------------------------------------------------------------

  async function triggerSync(isAutoFirstSync = false) {
    const resyncBtn = document.getElementById("resync-btn");
    const spinner = document.getElementById("btn-spinner");
    const icon = document.getElementById("btn-icon");
    const label = document.getElementById("btn-label");

    if (state.isSyncing) {
      showToast("Sync already in progress.");
      return;
    }

    try {
      resyncBtn.disabled = true;
      if (icon) icon.classList.add("hidden");
      if (spinner) spinner.classList.remove("hidden");
      if (label) label.textContent = "Syncing...";

      const res = await fetch("/api/sync", { method: "POST" });

      if (res.status === 409) {
        showToast("Sync already in progress.");
        startPollingSyncStatus();
        return;
      }

      if (!res.ok) {
        throw new Error(`Sync trigger failed with status ${res.status}`);
      }

      state.isSyncing = true;
      if (isAutoFirstSync) {
        setBanner("active", `<div style="display:flex;align-items:center;gap:10px;"><span class="spinner"></span><span>First sync — this takes ~20 s</span></div>`);
      } else {
        setBanner("active", `<div style="display:flex;align-items:center;gap:10px;"><span class="spinner"></span><span>Syncing from BingX...</span></div>`);
      }

      startPollingSyncStatus();
    } catch (e) {
      console.error("Failed to trigger sync:", e);
      setBanner("error", `<span>Failed to trigger sync: ${e.message}</span>`);
      resyncBtn.disabled = false;
      if (spinner) spinner.classList.add("hidden");
      if (icon) icon.classList.remove("hidden");
      if (label) label.textContent = "Resync";
      state.isSyncing = false;
    }
  }

  function startPollingSyncStatus() {
    if (state.syncPolling) clearInterval(state.syncPolling);

    state.syncPolling = setInterval(async () => {
      try {
        const res = await fetch("/api/sync/status");
        if (!res.ok) return;

        const data = await res.json();

        if (data.running) {
          state.isSyncing = true;
          const total = data.window_total || 13;
          const current = data.window_index || 1;
          setBanner(
            "active",
            `<div style="display:flex;align-items:center;gap:10px;">
              <span class="spinner"></span>
              <span>Syncing… window ${current} of ${total}</span>
            </div>`
          );
        } else {
          // Sync completed
          clearInterval(state.syncPolling);
          state.syncPolling = null;
          state.isSyncing = false;

          const resyncBtn = document.getElementById("resync-btn");
          const spinner = document.getElementById("btn-spinner");
          const icon = document.getElementById("btn-icon");
          const label = document.getElementById("btn-label");
          if (resyncBtn) resyncBtn.disabled = false;
          if (spinner) spinner.classList.add("hidden");
          if (icon) icon.classList.remove("hidden");
          if (label) label.textContent = "Resync";

          handleSyncCompleted(data.last_result);

          // Refresh metadata, balance, and positions
          await fetchMeta();
          await fetchBalance();
          await fetchPositions();
        }
      } catch (err) {
        console.warn("Error polling sync status:", err);
      }
    }, 500);
  }

  function handleSyncCompleted(result) {
    if (!result) {
      setBanner(null);
      return;
    }

    const errors = result.errors || [];
    const inserted = result.inserted_orders || 0;
    const newPos = result.new_positions || 0;

    if (result.fatal) {
      setBanner(
        "error",
        `<span>Sync failed: ${result.fatal}. Showing last known data.</span>
         <button class="px-2 py-1 bg-blue-600 hover:bg-blue-500 rounded text-xs text-white font-semibold ml-3" onclick="window.triggerManualSync()">Retry</button>`
      );
    } else if (errors.length > 0) {
      setBanner(
        "warning",
        `<span>Sync completed with errors — ${errors.length} window(s) failed. Some trades may be missing.</span>
         <button class="ml-auto text-amber-300 font-bold px-2" onclick="document.getElementById('sync-banner').classList.add('hidden')">×</button>`
      );
    } else {
      let msg = "";
      if (inserted === 0 && newPos === 0) {
        msg = "Already up to date.";
      } else {
        msg = `Synced — ${inserted} new order${inserted === 1 ? "" : "s"}, ${newPos} new position${newPos === 1 ? "" : "s"}.`;
      }
      setBanner("success", `<span>${msg}</span>`, 2000);
    }
  }

  // ---------------------------------------------------------------------------
  // Date Filter Bar Setup (PRD §11.2)
  // ---------------------------------------------------------------------------

  function setupFilterBar() {
    const fromInput = document.getElementById("filter-from");
    const toInput = document.getElementById("filter-to");
    const errorEl = document.getElementById("filter-error");

    const today = getIstToday();

    fromInput.min = CUTOFF_DATE;
    fromInput.max = today;
    toInput.min = CUTOFF_DATE;
    toInput.max = today;

    fromInput.value = state.from;
    toInput.value = state.to;

    function handleDateChange() {
      const fromVal = fromInput.value;
      const toVal = toInput.value;

      if (!fromVal || !toVal) return;

      if (fromVal > toVal) {
        errorEl.textContent = "From must be on or before To";
        errorEl.classList.remove("hidden");
        return;
      }

      errorEl.classList.add("hidden");
      state.from = fromVal;
      state.to = toVal;
      fetchPositions();
    }

    fromInput.addEventListener("change", handleDateChange);
    toInput.addEventListener("change", handleDateChange);
  }

  // ---------------------------------------------------------------------------
  // App Boot Sequence (PRD §11.8)
  // ---------------------------------------------------------------------------

  async function init() {
    // Initial calendar month from state.to (or current IST date)
    const toDateParts = state.to.split("-");
    state.calendarYear = parseInt(toDateParts[0], 10);
    state.calendarMonth = parseInt(toDateParts[1], 10);

    setupFilterBar();
    setupCalendarNav();
    setupTableAccordion();
    setupTableControls();

    const resyncBtn = document.getElementById("resync-btn");
    if (resyncBtn) {
      resyncBtn.addEventListener("click", () => triggerSync(false));
    }

    window.triggerManualSync = () => triggerSync(false);

    // Initial parallel load: meta, balance, & positions
    const [meta] = await Promise.all([
      fetchMeta(),
      fetchBalance(),
      fetchPositions(),
    ]);

    // Auto-sync on first visit if never synced or position_count == 0
    if (!meta || meta.last_synced_at === null || meta.position_count === 0) {
      console.log("Empty DB detected: triggering initial auto-sync...");
      triggerSync(true);
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
