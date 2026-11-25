// Estado global del Portal estilo Interactive Brokers + ShieldOrder B2B
const state = {
  symbol: "ASML",
  timeframe: "5m",
  caseId: null, // null = Tiempo Real | string = Caso Histórico Real
  side: "BUY",
  protectionEnabled: true,
  protectionMode: "INVALIDATE",
  preset: "MODERATE",
  enabledMetrics: {
    velocity: true,
    rvol: true,
    atr: true,
    rsi: true,
    divergence: true,
  },
  portfolio: null,
  lastImpact: null,
  geminiKey: localStorage.getItem("shieldorder_gemini_key") || "",
  realCases: [],
  lastCandles: [],
  chart: null,
  candleSeries: null,
  volumeSeries: null,
  limitLine: null,
  livePollTimer: null,
};

document.addEventListener("DOMContentLoaded", async () => {
  initIBChart();
  bindEvents();
  bindPortfolioEvents();
  updateGeminiIndicator();
  await loadPortfolioState();
  await loadRealCases();
  await loadMarketAndSimulate(true);
  startLivePolling();
});

function initIBChart() {
  const el = document.getElementById("ibChart");
  state.chart = LightweightCharts.createChart(el, {
    layout: {
      background: { type: "solid", color: "#ffffff" },
      textColor: "#475569",
      fontFamily: "Roboto, Helvetica Neue, Arial, sans-serif",
    },
    grid: {
      vertLines: { color: "#f1f5f9" },
      horzLines: { color: "#f1f5f9" },
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
    },
    rightPriceScale: {
      borderColor: "#e2e8f0",
    },
    timeScale: {
      borderColor: "#e2e8f0",
      timeVisible: true,
      secondsVisible: false,
    },
  });

  state.candleSeries = state.chart.addCandlestickSeries({
    upColor: "#00894c",
    downColor: "#d92d20",
    borderVisible: false,
    wickUpColor: "#00894c",
    wickDownColor: "#d92d20",
  });

  state.volumeSeries = state.chart.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "",
  });
  state.volumeSeries.priceScale().applyOptions({
    scaleMargins: { top: 0.80, bottom: 0 },
  });

  // Lectura OHLC en vivo al pasar el ratón (estilo IBKR, sin destruir #chartSourceLabel)
  state.chart.subscribeCrosshairMove((param) => {
    if (!param || !param.time) return;
    const bar = param.seriesData.get(state.candleSeries);
    const srcEl = document.getElementById("chartSourceLabel");
    if (bar && srcEl) {
      srcEl.innerHTML =
        `O <strong>${bar.open}</strong> &nbsp; H <strong>${bar.high}</strong> &nbsp; L <strong>${bar.low}</strong> &nbsp; C <strong>${bar.close}</strong>`;
    }
  });

  // Permitir clic directo en el gráfico para fijar el Limit Price
  state.chart.subscribeClick((param) => {
    if (!param || !param.point) return;
    const clickedPrice = state.candleSeries.coordinateToPrice(param.point.y);
    if (clickedPrice && clickedPrice > 0) {
      document.getElementById("inputLimitPrice").value = clickedPrice.toFixed(2);
      updateEstTotal();
      loadMarketAndSimulate(false);
    }
  });

  window.addEventListener("resize", () => {
    state.chart.applyOptions({ width: el.clientWidth });
  });
}

async function loadRealCases() {
  const resp = await fetch("/api/v1/market/real-cases");
  const data = await resp.json();
  state.realCases = data.cases || [];

  const container = document.getElementById("realCasesContainer");
  state.realCases.forEach((c) => {
    const btn = document.createElement("button");
    btn.className = `ribbon-pill ${c.id === "aapl_healthy" ? "case-safe" : "case-pill"}`;
    btn.dataset.caseId = c.id;
    btn.textContent = `${c.symbol} · ${c.date_label}`;
    btn.title = c.title;
    btn.addEventListener("click", async () => {
      state.caseId = c.id;
      state.symbol = c.symbol;
      state.protectionMode = c.recommended_mode;
      syncModeButtons();
      markRibbonActive(null, c.id);
      await loadMarketAndSimulate(true);
    });
    container.appendChild(btn);
  });
}

function markRibbonActive(liveSym, caseId) {
  document.querySelectorAll(".ribbon-pill[data-live]").forEach((b) => {
    b.classList.toggle("active", !caseId && b.dataset.live === liveSym);
  });
  document.querySelectorAll(".ribbon-pill[data-case-id]").forEach((b) => {
    b.classList.toggle("active", Boolean(caseId) && b.dataset.caseId === caseId);
  });
}

function bindEvents() {
  // Botones rápidos de acciones en vivo
  document.querySelectorAll(".ribbon-pill[data-live]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.caseId = null;
      state.symbol = btn.dataset.live;
      markRibbonActive(state.symbol, null);
      await loadMarketAndSimulate(true);
    });
  });

  document.getElementById("btnReturnLive").addEventListener("click", async () => {
    state.caseId = null;
    markRibbonActive(state.symbol, null);
    await loadMarketAndSimulate(true);
  });

  document.getElementById("navLiveMode").addEventListener("click", async (e) => {
    e.preventDefault();
    state.caseId = null;
    markRibbonActive(state.symbol, null);
    await loadMarketAndSimulate(true);
  });

  document.getElementById("navRealCases").addEventListener("click", async (e) => {
    e.preventDefault();
    if (state.realCases.length > 0) {
      const c = state.realCases[0];
      state.caseId = c.id;
      state.symbol = c.symbol;
      markRibbonActive(null, c.id);
      await loadMarketAndSimulate(true);
    }
  });

  // Selector de temporalidad (1m, 5m, 15m, 1H, 1D)
  document.querySelectorAll("#tfGroup .tf-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.caseId = null;
      state.timeframe = btn.dataset.tf;
      document.querySelectorAll("#tfGroup .tf-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      markRibbonActive(state.symbol, null);
      await loadMarketAndSimulate(true);
    });
  });

  // Buscador global estilo IBKR (Ctrl+K)
  const searchInput = document.getElementById("searchInput");
  const dropdown = document.getElementById("searchDropdown");
  let searchTimer = null;

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      searchInput.focus();
    }
  });

  searchInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      const q = searchInput.value.trim();
      const resp = await fetch(`/api/v1/market/search?q=${encodeURIComponent(q)}`);
      const data = await resp.json();
      const items = data.results || [];
      if (!items.length) {
        dropdown.classList.add("hidden");
        return;
      }
      dropdown.innerHTML = items
        .map(
          (it) => `
          <div class="search-item" data-sym="${it.symbol}">
            <div><strong>${it.symbol}</strong><span>${it.name}</span></div>
            <small>${it.exchange}</small>
          </div>`
        )
        .join("");
      dropdown.classList.remove("hidden");
      dropdown.querySelectorAll(".search-item").forEach((el) => {
        el.addEventListener("mousedown", async (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          state.caseId = null;
          state.symbol = el.dataset.sym;
          searchInput.value = "";
          dropdown.classList.add("hidden");
          markRibbonActive(state.symbol, null);
          await loadMarketAndSimulate(true);
        });
      });
    }, 220);
  });

  searchInput.addEventListener("keydown", async (e) => {
    if (e.key === "Enter" && searchInput.value.trim()) {
      state.caseId = null;
      state.symbol = searchInput.value.trim().toUpperCase();
      searchInput.value = "";
      dropdown.classList.add("hidden");
      markRibbonActive(state.symbol, null);
      await loadMarketAndSimulate(true);
    }
  });

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".ib-search-wrap")) dropdown.classList.add("hidden");
  });

  // Sub-tabs (Quote & Backtest | Cartera & Perfil | Órdenes Activas | OMS JSON)
  const activateSubtab = (viewId) => {
    document.querySelectorAll(".subtab-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === viewId));
    ["viewQuote", "viewPortfolio", "viewOrders", "viewOmsJson"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.classList.toggle("hidden", viewId !== id);
    });
  };
  document.querySelectorAll(".subtab-btn").forEach((btn) => {
    btn.addEventListener("click", () => activateSubtab(btn.dataset.view));
  });
  document.getElementById("navPortfolio").addEventListener("click", (e) => {
    e.preventDefault();
    activateSubtab("viewPortfolio");
  });
  document.getElementById("btnOpenPortfolioTab").addEventListener("click", () => {
    activateSubtab("viewPortfolio");
  });

  // Buy / Sell en Order Ticket
  const setSide = (side) => {
    state.side = side;
    const isBuy = side === "BUY";
    document.getElementById("tabBuy").classList.toggle("active", isBuy);
    document.getElementById("tabSell").classList.toggle("active", !isBuy);
    document.getElementById("quickBuyBtn").classList.toggle("active", isBuy);
    document.getElementById("quickSellBtn").classList.toggle("active", !isBuy);
    document.querySelector(".ticket-blue-header").classList.toggle("sell-mode", !isBuy);
    const cta = document.getElementById("btnPlaceLiveOrder");
    cta.textContent = isBuy ? "Buy Order" : "Sell Order";
    cta.classList.toggle("sell-btn", !isBuy);
    const actLbl = document.getElementById("shieldActionLabel");
    if (actLbl) {
      actLbl.textContent = isBuy
        ? "Si el precio llega en desplome anómalo:"
        : "Si el precio llega en disparo alcista anómalo:";
    }
    loadMarketAndSimulate(false);
  };
  document.getElementById("tabBuy").addEventListener("click", () => setSide("BUY"));
  document.getElementById("tabSell").addEventListener("click", () => setSide("SELL"));
  document.getElementById("quickBuyBtn").addEventListener("click", () => setSide("BUY"));
  document.getElementById("quickSellBtn").addEventListener("click", () => setSide("SELL"));

  // Exportar Informe PDF ejecutivo
  document.getElementById("btnExportPdf").addEventListener("click", () => {
    window.print();
  });

  // Steppers de cantidad y precio
  document.getElementById("qtyMinus").addEventListener("click", () => {
    const el = document.getElementById("inputQty");
    el.value = Math.max(1, parseInt(el.value || "1", 10) - 5);
    updateEstTotal();
    refreshPortfolioImpact();
  });
  document.getElementById("qtyPlus").addEventListener("click", () => {
    const el = document.getElementById("inputQty");
    el.value = parseInt(el.value || "1", 10) + 5;
    updateEstTotal();
    refreshPortfolioImpact();
  });
  document.getElementById("priceMinus").addEventListener("click", () => {
    const el = document.getElementById("inputLimitPrice");
    const v = parseFloat(el.value || "0");
    const step = v > 200 ? 5.0 : v > 50 ? 1.0 : 0.5;
    el.value = Math.max(0.1, v - step).toFixed(2);
    updateEstTotal();
    loadMarketAndSimulate(false);
  });
  document.getElementById("pricePlus").addEventListener("click", () => {
    const el = document.getElementById("inputLimitPrice");
    const v = parseFloat(el.value || "0");
    const step = v > 200 ? 5.0 : v > 50 ? 1.0 : 0.5;
    el.value = (v + step).toFixed(2);
    updateEstTotal();
    loadMarketAndSimulate(false);
  });

  document.getElementById("inputQty").addEventListener("input", () => {
    updateEstTotal();
    scheduleImpactRefresh();
  });
  document.getElementById("inputLimitPrice").addEventListener("input", () => {
    updateEstTotal();
    scheduleImpactRefresh();
  });
  document.getElementById("inputLimitPrice").addEventListener("change", () => {
    loadMarketAndSimulate(false);
  });
  document.getElementById("selectTif").addEventListener("change", () => {
    loadMarketAndSimulate(false);
  });

  // Checkbox ShieldOrder y Order Type sincronizados
  const syncShieldToggle = (enabled) => {
    state.protectionEnabled = enabled;
    document.getElementById("chkShieldEnabled").checked = enabled;
    document.getElementById("selectOrderType").value = enabled ? "SMART_LIMIT" : "LIMIT";
    document.getElementById("shieldControlsBody").classList.toggle("hidden", !enabled);
  };
  document.getElementById("chkShieldEnabled").addEventListener("change", (e) => {
    syncShieldToggle(e.target.checked);
    loadMarketAndSimulate(false);
  });
  document.getElementById("selectOrderType").addEventListener("change", (e) => {
    syncShieldToggle(e.target.value === "SMART_LIMIT");
    loadMarketAndSimulate(false);
  });

  // Modos de acción y Presets
  document.querySelectorAll("#modeSeg .seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.protectionMode = btn.dataset.mode;
      syncModeButtons();
      loadMarketAndSimulate(false);
    });
  });

  document.querySelectorAll("#presetSeg .pseg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.preset = btn.dataset.preset;
      applyPresetToSliders(state.preset);
      syncPresetButtons();
      loadMarketAndSimulate(false);
    });
  });

  // Sliders manuales de las 5 métricas + Umbral (al mover cualquiera pasa automáticamente a CUSTOM)
  const wireSlider = (id, cb) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("input", (e) => {
      state.preset = "CUSTOM";
      syncPresetButtons();
      cb(e);
    });
    el.addEventListener("change", () => loadMarketAndSimulate(false));
  };
  wireSlider("slDrop", (e) => (document.getElementById("lblDrop").textContent = `${parseFloat(e.target.value).toFixed(1)}%`));
  wireSlider("slWin", (e) => {
    document.getElementById("lblWin").textContent = e.target.value;
    document.getElementById("lblWinVal").textContent = `${e.target.value} min`;
  });
  wireSlider("slRvol", (e) => (document.getElementById("lblRvol").textContent = `${parseFloat(e.target.value).toFixed(1)}x`));
  wireSlider("slAtr", (e) => (document.getElementById("lblAtr").textContent = `${parseFloat(e.target.value).toFixed(1)}x`));
  wireSlider("slRsi", (e) => (document.getElementById("lblRsi").textContent = e.target.value));
  wireSlider("slDiv", (e) => (document.getElementById("lblDiv").textContent = `${parseFloat(e.target.value).toFixed(1)}%`));
  wireSlider("slTox", (e) => (document.getElementById("lblTox").textContent = `${e.target.value}/100`));

  // Botones Preview / Backtest y Buy Order Live
  document.getElementById("btnPreviewBacktest").addEventListener("click", () => loadMarketAndSimulate(false));
  document.getElementById("btnPlaceLiveOrder").addEventListener("click", placeLiveOrder);
  document.getElementById("btnRefreshLive").addEventListener("click", refreshLiveQuoteOnly);
  document.getElementById("btnTopTrade").addEventListener("click", () => {
    document.getElementById("inputLimitPrice").focus();
  });

  // Copiloto Gemini
  document.getElementById("btnRunCopilot").addEventListener("click", runCopilot);

  // Modal Gemini
  document.getElementById("btnOpenGemini").addEventListener("click", () => {
    document.getElementById("inputGeminiKey").value = state.geminiKey;
    document.getElementById("geminiModal").classList.remove("hidden");
  });
  document.getElementById("btnCloseGemini").addEventListener("click", () => {
    document.getElementById("geminiModal").classList.add("hidden");
  });
  document.getElementById("btnSaveGemini").addEventListener("click", () => {
    state.geminiKey = document.getElementById("inputGeminiKey").value.trim();
    localStorage.setItem("shieldorder_gemini_key", state.geminiKey);
    updateGeminiIndicator();
    document.getElementById("geminiModal").classList.add("hidden");
  });
}

function syncModeButtons() {
  document.querySelectorAll("#modeSeg .seg-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.mode === state.protectionMode);
  });
}

function applyPresetToSliders(preset) {
  const presets = {
    CONSERVATIVE: { drop: 2.8, win: 60, rvol: 2.0, atr: 2.0, rsi: 32, div: 2.5, tox: 48 },
    MODERATE: { drop: 4.0, win: 60, rvol: 2.5, atr: 2.5, rsi: 28, div: 3.5, tox: 60 },
    AGGRESSIVE: { drop: 6.0, win: 45, rvol: 3.5, atr: 3.2, rsi: 22, div: 5.0, tox: 72 },
  };
  const p = presets[preset];
  if (!p) return;
  document.getElementById("slDrop").value = p.drop;
  document.getElementById("lblDrop").textContent = `${p.drop.toFixed(1)}%`;
  document.getElementById("slWin").value = p.win;
  document.getElementById("lblWin").textContent = p.win;
  document.getElementById("lblWinVal").textContent = `${p.win} min`;
  document.getElementById("slRvol").value = p.rvol;
  document.getElementById("lblRvol").textContent = `${p.rvol.toFixed(1)}x`;
  document.getElementById("slAtr").value = p.atr;
  document.getElementById("lblAtr").textContent = `${p.atr.toFixed(1)}x`;
  document.getElementById("slRsi").value = p.rsi;
  document.getElementById("lblRsi").textContent = p.rsi;
  document.getElementById("slDiv").value = p.div;
  document.getElementById("lblDiv").textContent = `${p.div.toFixed(1)}%`;
  document.getElementById("slTox").value = p.tox;
  document.getElementById("lblTox").textContent = `${p.tox}/100`;
}

function syncPresetButtons() {
  document.querySelectorAll("#presetSeg .pseg-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.preset === state.preset);
  });
}

function updateGeminiIndicator() {
  document.getElementById("geminiDot").className = state.geminiKey ? "status-dot dot-green" : "status-dot dot-amber";
}

function updateEstTotal() {
  const qty = parseFloat(document.getElementById("inputQty").value || "0");
  const price = parseFloat(document.getElementById("inputLimitPrice").value || "0");
  const total = qty * price;
  document.getElementById("estOrderTotal").textContent = `~ ${total.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD`;
}

function getRulesPayload() {
  return {
    lookback_minutes: parseInt(document.getElementById("slWin").value, 10),
    max_drop_pct: parseFloat(document.getElementById("slDrop").value),
    max_rvol: parseFloat(document.getElementById("slRvol").value),
    min_rsi: parseFloat(document.getElementById("slRsi").value),
    max_atr_multiple: parseFloat(document.getElementById("slAtr").value),
    max_benchmark_divergence_pct: parseFloat(document.getElementById("slDiv").value),
    toxicity_threshold: parseFloat(document.getElementById("slTox").value),
    enable_velocity: state.enabledMetrics.velocity,
    enable_rvol: state.enabledMetrics.rvol,
    enable_atr: state.enabledMetrics.atr,
    enable_rsi: state.enabledMetrics.rsi,
    enable_divergence: state.enabledMetrics.divergence,
  };
}

async function loadMarketAndSimulate(useSuggestedPrice = false) {
  const overlay = document.getElementById("chartLoadingOverlay");
  if (overlay) {
    overlay.textContent = `Cargando velas reales de ${state.symbol} (${state.caseId ? "1D Histórico" : state.timeframe})...`;
    overlay.classList.remove("hidden");
  }
  const limitVal = useSuggestedPrice ? 0 : parseFloat(document.getElementById("inputLimitPrice").value || "0");
  const qtyVal = parseFloat(document.getElementById("inputQty").value || "10");

  try {
    const resp = await fetch("/api/v1/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: state.symbol,
        timeframe: state.timeframe,
        case_id: state.caseId,
        limit_price: limitVal,
        quantity: qtyVal,
        side: state.side,
        time_in_force: document.getElementById("selectTif").value || "Day",
        protection_enabled: state.protectionEnabled,
        protection_mode: state.protectionMode,
        preset: state.preset,
        rules: getRulesPayload(),
        gemini_api_key: state.geminiKey || null,
      }),
    });

    if (!resp.ok) {
      const err = await resp.json();
      alert(err.detail || `Error cargando ${state.symbol}`);
      return;
    }

    const data = await resp.json();
    if (useSuggestedPrice || limitVal <= 0) {
      document.getElementById("inputLimitPrice").value = Number(data.effective_limit_price).toFixed(2);
    }
    updateEstTotal();
    renderAll(data);
    await loadActiveOrders();
    await refreshPortfolioImpact();
  } finally {
    if (overlay) overlay.classList.add("hidden");
  }
}

async function refreshLiveQuoteOnly() {
  if (state.caseId) return; // No sobreescribir si estamos en caso histórico
  try {
    const resp = await fetch(`/api/v1/market/quote?symbol=${encodeURIComponent(state.symbol)}&timeframe=${encodeURIComponent(state.timeframe)}`);
    if (!resp.ok) return;
    const data = await resp.json();
    renderQuoteHeader(data.quote, data.live_toxicity, data.case_info);
    renderOrdersTable(data.active_orders || []);
  } catch (e) {}
}

function startLivePolling() {
  if (state.livePollTimer) clearInterval(state.livePollTimer);
  state.livePollTimer = setInterval(refreshLiveQuoteOnly, 15000);
}

function renderQuoteHeader(q, liveTox, caseInfo) {
  document.getElementById("qSymbol").textContent = q.symbol;
  document.getElementById("ticketSymBadge").textContent = q.symbol;
  document.getElementById("qName").textContent = q.short_name;
  document.getElementById("qExchange").textContent = q.exchange;
  document.getElementById("qPrice").textContent = q.last_price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  document.getElementById("qCurrency").textContent = q.currency;

  const sign = q.change >= 0 ? "+" : "";
  const chEl = document.getElementById("qChange");
  chEl.textContent = `${sign}${q.change.toFixed(2)} (${sign}${q.change_pct.toFixed(2)}%)`;
  chEl.className = `q-change ${q.change >= 0 ? "txt-green" : "txt-red"}`;

  document.getElementById("qAsk").textContent = q.ask.toFixed(2);
  document.getElementById("qBid").textContent = q.bid.toFixed(2);

  const pill = document.getElementById("qModePill");
  if (caseInfo) {
    pill.textContent = `HISTÓRICO · ${caseInfo.date_label.toUpperCase()}`;
    pill.className = "q-live-pill hist";
    document.getElementById("caseBanner").classList.remove("hidden");
    document.getElementById("caseBannerTitle").textContent = caseInfo.title;
    document.getElementById("caseBannerDesc").textContent = caseInfo.description;
  } else {
    pill.textContent = "LIVE · AUTO 15s";
    pill.className = "q-live-pill";
    document.getElementById("caseBanner").classList.add("hidden");
  }

  if (liveTox) {
    const score = liveTox.total_toxicity_score;
    const toxEl = document.getElementById("qLiveToxVal");
    toxEl.textContent = `${score}/100`;
    if (score >= 60) {
      toxEl.style.background = "#fef2f2";
      toxEl.style.color = "#d92d20";
    } else if (score >= 40) {
      toxEl.style.background = "#fffbeb";
      toxEl.style.color = "#d97706";
    } else {
      toxEl.style.background = "#ecfdf5";
      toxEl.style.color = "#00894c";
    }
  }
}

function renderAll(data) {
  renderQuoteHeader(data.quote, data.live_toxicity, data.case_info);
  renderFillProbability(data.fill_probability);
  const srcEl = document.getElementById("chartSourceLabel");
  if (srcEl) srcEl.textContent = data.data_source;

  // 1. Gráfico de Velas y Volumen (estrictamente ordenado y sin timestamps duplicados)
  const dedupMap = new Map();
  (data.candles || []).forEach((c) => {
    dedupMap.set(Number(c.timestamp), c);
  });
  const sortedRaw = Array.from(dedupMap.values()).sort((a, b) => a.timestamp - b.timestamp);

  const candles = sortedRaw.map((c) => ({
    time: Number(c.timestamp),
    open: Number(c.open),
    high: Number(c.high),
    low: Number(c.low),
    close: Number(c.close),
  }));
  const volumes = sortedRaw.map((c) => ({
    time: Number(c.timestamp),
    value: Number(c.volume || 0),
    color: c.close >= c.open ? "rgba(0, 137, 76, 0.28)" : "rgba(217, 45, 32, 0.30)",
  }));

  state.chart.timeScale().applyOptions({
    timeVisible: data.timeframe !== "1d",
    secondsVisible: false,
  });
  state.candleSeries.setMarkers([]);
  state.candleSeries.setData(candles);
  state.volumeSeries.setData(volumes);
  state.chart.priceScale("right").applyOptions({ autoScale: true });
  state.chart.timeScale().fitContent();

  if (state.limitLine) state.candleSeries.removePriceLine(state.limitLine);
  state.limitLine = state.candleSeries.createPriceLine({
    price: data.effective_limit_price,
    color: "#0052cc",
    lineWidth: 2,
    lineStyle: LightweightCharts.LineStyle.Dashed,
    axisLabelVisible: true,
    title: `Limit $${data.effective_limit_price}`,
  });

  const sim = data.simulation;
  const untouched = document.getElementById("untouchedNotice");

  if (!sim.touched) {
    untouched.textContent = `${sim.message} Haz clic en cualquier nivel del gráfico para fijar tu Limit Price dentro del rango.`;
    untouched.classList.remove("hidden");
    state.candleSeries.setMarkers([]);
    state.chart.timeScale().fitContent();
    return;
  }
  untouched.classList.add("hidden");

  // 2. Marcadores
  const markers = [
    {
      time: sim.standard_order.fill_timestamp,
      position: "aboveBar",
      color: "#d92d20",
      shape: "arrowDown",
      text: `Limit Normal compra $${sim.standard_order.fill_price}`,
    },
  ];
  (sim.chart_events || []).forEach((ev) => {
    const goodFill = ev.type === "SMART_FILL" || ev.type === "SMART_BOUNCE_FILL";
    markers.push({
      time: ev.timestamp,
      position: "belowBar",
      color: goodFill ? "#00894c" : "#0052cc",
      shape: goodFill ? "arrowUp" : "circle",
      text: ev.label,
    });
  });
  markers.sort((a, b) => a.time - b.time);
  state.candleSeries.setMarkers(markers);
  state.chart.timeScale().fitContent();

  // 3. Tarjetas Comparativas
  const std = sim.standard_order;
  const smart = sim.smart_order;
  const m = sim.metrics_at_touch;

  const sSign = std.final_pnl_usd >= 0 ? "+" : "";
  const stdMain = document.getElementById("stdMainVal");
  stdMain.textContent = `${sSign}$${std.final_pnl_usd.toFixed(2)} (${sSign}${std.final_pnl_pct}%)`;
  stdMain.className = `comp-number ${std.final_pnl_usd >= 0 ? "txt-green" : "txt-red"}`;
  document.getElementById("stdPill").textContent = std.status_label;
  document.getElementById("stdPill").className = `pill ${std.final_pnl_usd >= 0 ? "pill-grey" : "pill-red"}`;
  document.getElementById("stdSubVal").textContent =
    `Ejecutó a $${std.fill_price} · Mínimo tras compra: $${std.lowest_price_after_fill} (${std.max_drawdown_pct}%)`;

  const smartMain = document.getElementById("smartMainVal");
  const smartPill = document.getElementById("smartPill");
  smartPill.textContent = smart.status_label;

  if (["INVALIDATED_PROTECTED", "PAUSED_CONFIRMATION", "EXPIRED_NO_BOUNCE"].includes(smart.status)) {
    smartMain.textContent = `+$${smart.capital_saved_usd.toFixed(2)} salvados`;
    smartMain.className = "comp-number txt-green";
    smartPill.className = "pill pill-green";
    document.getElementById("smartSubVal").textContent =
      `Caída tóxica bloqueada · Capital protegido ($0 pérdida sufrida)`;
  } else {
    const mSign = smart.final_pnl_usd >= 0 ? "+" : "";
    smartMain.textContent = `${mSign}$${smart.final_pnl_usd.toFixed(2)} (${mSign}${smart.final_pnl_pct}%)`;
    smartMain.className = `comp-number ${smart.final_pnl_usd >= 0 ? "txt-green" : "txt-red"}`;
    smartPill.className = "pill pill-green";
    document.getElementById("smartSubVal").textContent =
      `Compra ejecutada a $${smart.fill_price}${smart.value_added_pct > 0 ? ` · Mejora +${smart.value_added_pct}%` : " · Caída sana verificada"}`;
  }

  // 4. Toxicidad
  const tox = m.total_toxicity_score;
  const th = sim.applied_rules.toxicity_threshold;
  const danger = tox >= th || m.triggered_flags.length >= 2;
  const toxVal = document.getElementById("toxVal");
  toxVal.textContent = `${tox}/100`;
  toxVal.className = `comp-number ${danger ? "txt-red" : "txt-green"}`;
  const toxBar = document.getElementById("toxBar");
  toxBar.style.width = `${Math.min(100, tox)}%`;
  toxBar.style.background = danger ? "#d92d20" : "#00894c";
  document.getElementById("toxSub").textContent = danger
    ? `Flujo tóxico detectado (supera umbral ${th}/100)`
    : `Flujo sano (por debajo de umbral ${th}/100)`;

  // 5. Barras INTERACTIVAS de las 5 métricas (editables directamente aquí por el usuario)
  const applied = sim.applied_rules;
  const isSell = state.side === "SELL";
  const items = [
    {
      key: "velocity",
      sliderId: "slDrop",
      lblId: "lblDrop",
      suffix: "%",
      name: isSell ? "1. Velocidad de Subida (ΔP/Δt)" : "1. Velocidad de Caída (ΔP/Δt)",
      val: `${isSell ? "+" : "-"}${m.drop_pct_in_window}% (Acel. ${m.acceleration_ratio}x)`,
      score: m.velocity_score,
      ruleVal: applied.max_drop_pct,
      min: 0.5,
      max: 15.0,
      step: 0.5,
      rulePrefix: isSell ? "Bloquear si sube ≥" : "Bloquear si cae ≥",
      breached: state.enabledMetrics.velocity && m.drop_pct_in_window >= applied.max_drop_pct,
    },
    {
      key: "rvol",
      sliderId: "slRvol",
      lblId: "lblRvol",
      suffix: "x",
      name: "2. Volumen Relativo (RVOL)",
      val: `${m.rvol}x media normal`,
      score: m.volume_score,
      ruleVal: applied.max_rvol,
      min: 0.5,
      max: 6.0,
      step: 0.1,
      rulePrefix: "Bloquear si volumen ≥",
      breached: state.enabledMetrics.rvol && m.rvol >= applied.max_rvol,
    },
    {
      key: "atr",
      sliderId: "slAtr",
      lblId: "lblAtr",
      suffix: "x",
      name: "3. Expansión de Volatilidad (ATR)",
      val: `${m.atr_multiple}x rango habitual`,
      score: m.volatility_score,
      ruleVal: applied.max_atr_multiple,
      min: 0.4,
      max: 6.0,
      step: 0.1,
      rulePrefix: "Bloquear si volatilidad ≥",
      breached: state.enabledMetrics.atr && m.atr_multiple >= applied.max_atr_multiple,
    },
    {
      key: "rsi",
      sliderId: "slRsi",
      lblId: "lblRsi",
      suffix: "",
      name: "4. Momentum RSI (14)",
      val: `RSI ${m.rsi_14}`,
      score: m.momentum_score,
      ruleVal: applied.min_rsi,
      min: 10,
      max: 75,
      step: 1,
      rulePrefix: isSell ? "Bloquear si RSI ≥ (100 - X):" : "Bloquear si RSI ≤",
      breached: state.enabledMetrics.rsi && (isSell ? m.rsi_14 >= 100 - applied.min_rsi : m.rsi_14 <= applied.min_rsi),
    },
    {
      key: "divergence",
      sliderId: "slDiv",
      lblId: "lblDiv",
      suffix: "%",
      name: "5. Divergencia vs Índice (QQQ/BTC)",
      val: `${isSell ? "+" : "-"}${m.benchmark_divergence_pct}% extra`,
      score: m.divergence_score,
      ruleVal: applied.max_benchmark_divergence_pct,
      min: 0.2,
      max: 12.0,
      step: 0.2,
      rulePrefix: "Bloquear si diverge ≥",
      breached: state.enabledMetrics.divergence && m.benchmark_divergence_pct >= applied.max_benchmark_divergence_pct,
    },
  ];

  const metricsEl = document.getElementById("metricsList");
  metricsEl.innerHTML = items
    .map((it) => {
      const enabled = state.enabledMetrics[it.key];
      const col = !enabled ? "#94a3b8" : it.breached || it.score >= 65 ? "#d92d20" : it.score >= 40 ? "#d97706" : "#00894c";
      const cls = `metric-card-row ${it.breached ? "breached" : ""} ${!enabled ? "disabled" : ""}`;
      return `
        <div class="${cls}">
          <div class="m-head">
            <label class="m-check-label">
              <input type="checkbox" class="m-toggle" data-key="${it.key}" ${enabled ? "checked" : ""} />
              <span>${it.name}</span>
            </label>
            <strong>${it.val}</strong>
          </div>
          <div class="m-bar-bg">
            <div class="m-bar-fill" style="width:${Math.min(100, Math.max(4, it.score))}%;background:${col}"></div>
          </div>
          <div class="m-editor-row">
            <span>${it.rulePrefix}</span>
            <input type="range" class="m-inline-range" data-slider="${it.sliderId}" data-lbl="${it.lblId}" data-suffix="${it.suffix}" min="${it.min}" max="${it.max}" step="${it.step}" value="${it.ruleVal}" ${!enabled ? "disabled" : ""} />
            <input type="number" class="m-num-input m-inline-num" data-slider="${it.sliderId}" data-lbl="${it.lblId}" data-suffix="${it.suffix}" min="${it.min}" max="${it.max}" step="${it.step}" value="${it.ruleVal}" ${!enabled ? "disabled" : ""} />
            <span>${it.suffix}</span>
          </div>
        </div>`;
    })
    .join("");

  // Enlazar checkboxes, sliders y cajas numéricas de las 5 barras
  metricsEl.querySelectorAll(".m-toggle").forEach((chk) => {
    chk.addEventListener("change", (e) => {
      state.enabledMetrics[e.target.dataset.key] = e.target.checked;
      state.preset = "CUSTOM";
      syncPresetButtons();
      loadMarketAndSimulate(false);
    });
  });

  const syncInlineMetric = (el, runSim) => {
    const val = parseFloat(el.value);
    if (isNaN(val)) return;
    const sliderId = el.dataset.slider;
    const lblId = el.dataset.lbl;
    const suffix = el.dataset.suffix || "";
    const ticketSlider = document.getElementById(sliderId);
    const ticketLbl = document.getElementById(lblId);
    if (ticketSlider) ticketSlider.value = val;
    if (ticketLbl) ticketLbl.textContent = `${suffix ? val.toFixed(1) : Math.round(val)}${suffix}`;

    // Sincronizar el par slider <-> input numérico dentro de la misma tarjeta
    const parent = el.closest(".m-editor-row");
    if (parent) {
      const sibling = el.type === "range" ? parent.querySelector(".m-inline-num") : parent.querySelector(".m-inline-range");
      if (sibling) sibling.value = val;
    }

    state.preset = "CUSTOM";
    syncPresetButtons();
    if (runSim) loadMarketAndSimulate(false);
  };

  metricsEl.querySelectorAll(".m-inline-range").forEach((rng) => {
    rng.addEventListener("input", (e) => syncInlineMetric(e.target, false));
    rng.addEventListener("change", (e) => syncInlineMetric(e.target, true));
  });
  metricsEl.querySelectorAll(".m-inline-num").forEach((num) => {
    num.addEventListener("change", (e) => syncInlineMetric(e.target, true));
  });

  const fl = document.getElementById("flagsList");
  fl.innerHTML = m.triggered_flags.length
    ? m.triggered_flags.map((f) => `<li><strong>ALERTA:</strong> ${f}</li>`).join("")
    : `<li class="ok-flag"><strong>OK:</strong> Sin alertas de toxicidad. Descenso ordenado apto para ejecutar la Limit Order.</li>`;

  // 6. Notificación y OMS JSON
  document.getElementById("notifTitle").textContent = data.client_notification.headline;
  document.getElementById("notifBody").textContent = data.client_notification.body;
  document.getElementById("notifEngine").textContent = `Motor: ${data.client_notification.generated_by}`;
  document.getElementById("notifConfirmRow").classList.toggle("hidden", smart.status !== "PAUSED_CONFIRMATION");
  document.getElementById("omsJsonPre").textContent = JSON.stringify(sim.b2b_webhook_payload, null, 2);
}

async function placeLiveOrder() {
  const limitPrice = parseFloat(document.getElementById("inputLimitPrice").value || "0");
  const qty = parseFloat(document.getElementById("inputQty").value || "10");
  if (limitPrice <= 0) return;

  const resp = await fetch("/api/v1/orders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      symbol: state.symbol,
      side: state.side,
      quantity: qty,
      order_type: state.protectionEnabled ? "SMART_LIMIT" : "LIMIT",
      limit_price: limitPrice,
      time_in_force: document.getElementById("selectTif").value,
      outside_rth: document.getElementById("selectRth").value === "Yes",
      protection_enabled: state.protectionEnabled,
      protection_mode: state.protectionMode,
      preset: state.preset,
      rules: getRulesPayload(),
    }),
  });
  const data = await resp.json();
  renderOrdersTable(data.orders || []);

  const toast = document.getElementById("orderConfirmToast");
  toast.textContent = `Orden ${data.order.order_id} enviada: ${data.order.side} ${data.order.quantity} ${data.order.symbol} @ $${data.order.limit_price} (${data.order.order_type}). Ver en Órdenes Activas.`;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), 5000);
}

async function loadActiveOrders() {
  const resp = await fetch("/api/v1/orders");
  const data = await resp.json();
  renderOrdersTable(data.orders || []);
}

function renderOrdersTable(orders) {
  document.getElementById("ordersCountBadge").textContent = orders.length;
  const wrap = document.getElementById("ordersTableWrap");
  if (!orders.length) {
    wrap.innerHTML = `<p class="empty-state">Aún no has enviado ninguna orden en vivo. Configura el Order Ticket a la derecha y pulsa <strong>Buy Order</strong>.</p>`;
    return;
  }
  wrap.innerHTML = `
    <table class="orders-table">
      <thead>
        <tr>
          <th>ID</th>
          <th>Activo</th>
          <th>Tipo</th>
          <th>Cant.</th>
          <th>Limit Price</th>
          <th>Modo ShieldOrder</th>
          <th>Estado en Vivo</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        ${orders
          .map(
            (o) => `
          <tr>
            <td><code>${o.order_id}</code></td>
            <td><strong>${o.symbol}</strong> (${o.side})</td>
            <td>${o.order_type}</td>
            <td>${o.quantity}</td>
            <td><strong>$${Number(o.limit_price).toFixed(2)}</strong></td>
            <td>${o.protection_enabled ? `${o.protection_mode} (${o.preset})` : "Sin protección"}</td>
            <td><span class="pill ${o.status === "BLOCKED_BY_SHIELD" ? "pill-red" : "pill-green"}">${o.status_note}</span></td>
            <td><button class="ib-btn-outline-sm" onclick="cancelLiveOrder('${o.order_id}')">Cancelar</button></td>
          </tr>`
          )
          .join("")}
      </tbody>
    </table>`;
}

window.cancelLiveOrder = async function (orderId) {
  const resp = await fetch(`/api/v1/orders/${orderId}`, { method: "DELETE" });
  const data = await resp.json();
  renderOrdersTable(data.orders || []);
};

async function runCopilot() {
  const prompt = document.getElementById("txtCopilot").value.trim();
  if (!prompt) return;
  const btn = document.getElementById("btnRunCopilot");
  btn.textContent = "Analizando...";
  btn.disabled = true;

  try {
    const resp = await fetch("/api/v1/ai/copilot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt,
        default_limit_price: parseFloat(document.getElementById("inputLimitPrice").value || "0"),
        gemini_api_key: state.geminiKey || null,
      }),
    });
    const parsed = await resp.json();
    if (parsed.limit_price) document.getElementById("inputLimitPrice").value = Number(parsed.limit_price).toFixed(2);
    state.protectionMode = parsed.protection_mode || "INVALIDATE";
    state.preset = "CUSTOM";
    syncModeButtons();
    syncPresetButtons();

    document.getElementById("slDrop").value = parsed.max_drop_pct;
    document.getElementById("lblDrop").textContent = `${Number(parsed.max_drop_pct).toFixed(1)}%`;
    document.getElementById("slWin").value = parsed.lookback_minutes;
    document.getElementById("lblWin").textContent = parsed.lookback_minutes;
    document.getElementById("lblWinVal").textContent = `${parsed.lookback_minutes} min`;
    document.getElementById("slRvol").value = parsed.max_rvol;
    document.getElementById("lblRvol").textContent = `${Number(parsed.max_rvol).toFixed(1)}x`;
    document.getElementById("slRsi").value = parsed.min_rsi;
    document.getElementById("lblRsi").textContent = parsed.min_rsi;
    document.getElementById("slTox").value = parsed.toxicity_threshold;
    document.getElementById("lblTox").textContent = `${parsed.toxicity_threshold}/100`;

    const msg = document.getElementById("copilotMsg");
    msg.textContent = parsed.assistant_summary;
    msg.classList.remove("hidden");
    updateEstTotal();
    await loadMarketAndSimulate(false);
  } finally {
    btn.textContent = "Aplicar regla con IA";
    btn.disabled = false;
  }
}

// ============================================================
// MÓDULO PRE-TRADE: CARTERA (8+ ACCIONES) Y PERFIL DEL INVERSOR
// ============================================================
const ALL_SECTORS = [
  "Technology",
  "Semiconductors",
  "Healthcare",
  "Financials",
  "Consumer Defensive",
  "Consumer Cyclical",
  "Energy",
  "Crypto",
];

let impactDebounceTimer = null;
function scheduleImpactRefresh() {
  clearTimeout(impactDebounceTimer);
  impactDebounceTimer = setTimeout(refreshPortfolioImpact, 220);
}

async function loadPortfolioState() {
  const resp = await fetch("/api/v1/portfolio");
  const data = await resp.json();
  state.portfolio = data.portfolio;
  syncProfileControlsFromState();
}

async function savePortfolioAndRefresh() {
  if (!state.portfolio) return;
  await fetch("/api/v1/portfolio", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state.portfolio),
  });
  await refreshPortfolioImpact();
}

function syncProfileControlsFromState() {
  if (!state.portfolio) return;
  const p = state.portfolio.profile;
  document.getElementById("pfRisk").value = p.risk_tolerance;
  document.getElementById("pfHorizon").value = p.time_horizon;
  document.getElementById("pfStyle").value = p.investment_style;
  document.getElementById("pfTargetRet").value = p.target_annual_return_pct;
  document.getElementById("lblPfTargetRet").textContent = `${Number(p.target_annual_return_pct).toFixed(1)}%`;
  document.getElementById("pfMaxPos").value = p.max_single_position_pct;
  document.getElementById("lblPfMaxPos").textContent = `${Math.round(p.max_single_position_pct)}%`;
  document.getElementById("pfMaxSec").value = p.max_sector_weight_pct;
  document.getElementById("lblPfMaxSec").textContent = `${Math.round(p.max_sector_weight_pct)}%`;
  document.getElementById("pfCashInput").value = Math.round(state.portfolio.cash_usd);

  const chipsWrap = document.getElementById("pfSectorsChips");
  chipsWrap.innerHTML = ALL_SECTORS.map((sec) => {
    const active = (p.preferred_sectors || []).includes(sec);
    return `<button type="button" class="sec-chip ${active ? "active" : ""}" data-sec="${sec}">${sec}</button>`;
  }).join("");

  chipsWrap.querySelectorAll(".sec-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      const sec = btn.dataset.sec;
      const list = state.portfolio.profile.preferred_sectors || [];
      if (list.includes(sec)) {
        state.portfolio.profile.preferred_sectors = list.filter((s) => s !== sec);
      } else {
        state.portfolio.profile.preferred_sectors = [...list, sec];
      }
      btn.classList.toggle("active");
      savePortfolioAndRefresh();
    });
  });
}

function bindPortfolioEvents() {
  const onProfileChange = () => {
    if (!state.portfolio) return;
    const p = state.portfolio.profile;
    p.risk_tolerance = document.getElementById("pfRisk").value;
    p.time_horizon = document.getElementById("pfHorizon").value;
    p.investment_style = document.getElementById("pfStyle").value;
    p.target_annual_return_pct = parseFloat(document.getElementById("pfTargetRet").value);
    p.max_single_position_pct = parseFloat(document.getElementById("pfMaxPos").value);
    p.max_sector_weight_pct = parseFloat(document.getElementById("pfMaxSec").value);
    document.getElementById("lblPfTargetRet").textContent = `${p.target_annual_return_pct.toFixed(1)}%`;
    document.getElementById("lblPfMaxPos").textContent = `${Math.round(p.max_single_position_pct)}%`;
    document.getElementById("lblPfMaxSec").textContent = `${Math.round(p.max_sector_weight_pct)}%`;
    savePortfolioAndRefresh();
  };

  ["pfRisk", "pfHorizon", "pfStyle"].forEach((id) => {
    document.getElementById(id).addEventListener("change", onProfileChange);
  });
  ["pfTargetRet", "pfMaxPos", "pfMaxSec"].forEach((id) => {
    document.getElementById(id).addEventListener("input", () => {
      document.getElementById("lblPfTargetRet").textContent = `${parseFloat(document.getElementById("pfTargetRet").value).toFixed(1)}%`;
      document.getElementById("lblPfMaxPos").textContent = `${Math.round(parseFloat(document.getElementById("pfMaxPos").value))}%`;
      document.getElementById("lblPfMaxSec").textContent = `${Math.round(parseFloat(document.getElementById("pfMaxSec").value))}%`;
    });
    document.getElementById(id).addEventListener("change", onProfileChange);
  });

  document.getElementById("pfCashInput").addEventListener("change", (e) => {
    if (!state.portfolio) return;
    state.portfolio.cash_usd = Math.max(0, parseFloat(e.target.value || "0"));
    savePortfolioAndRefresh();
  });

  document.getElementById("btnAddHolding").addEventListener("click", () => {
    if (!state.portfolio) return;
    const sym = document.getElementById("addHoldSym").value.trim().toUpperCase();
    const shares = parseFloat(document.getElementById("addHoldShares").value || "0");
    const price = parseFloat(document.getElementById("addHoldPrice").value || "0");
    if (!sym || shares <= 0 || price <= 0) return;
    const existing = state.portfolio.holdings.find((h) => h.symbol.toUpperCase() === sym);
    if (existing) {
      existing.shares += shares;
      existing.price = price;
    } else {
      state.portfolio.holdings.push({ symbol: sym, shares, price });
    }
    document.getElementById("addHoldSym").value = "";
    savePortfolioAndRefresh();
  });

  const applyOptimalQty = () => {
    if (!state.lastImpact || !state.lastImpact.suggested_optimal_qty) return;
    document.getElementById("inputQty").value = state.lastImpact.suggested_optimal_qty;
    updateEstTotal();
    loadMarketAndSimulate(false);
  };
  document.getElementById("btnApplyOptimalTicket").addEventListener("click", applyOptimalQty);
  document.getElementById("btnApplyOptimalFull").addEventListener("click", applyOptimalQty);
}

async function refreshPortfolioImpact() {
  const limitPrice = parseFloat(document.getElementById("inputLimitPrice").value || "0");
  const qty = parseFloat(document.getElementById("inputQty").value || "1");
  if (limitPrice <= 0 || qty <= 0) return;

  try {
    const resp = await fetch("/api/v1/portfolio/impact", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: state.symbol,
        side: state.side,
        quantity: qty,
        limit_price: limitPrice,
        portfolio: state.portfolio,
      }),
    });
    if (!resp.ok) return;
    const imp = await resp.json();
    state.lastImpact = imp;
    renderPortfolioImpact(imp);
  } catch (e) {}
}

function renderPortfolioImpact(imp) {
  const b = imp.before;
  const a = imp.after;
  const d = imp.deltas;

  // 1. Tarjeta compacta dentro del ORDER TICKET derecho
  const pillCls = imp.fit_tone === "green" ? "pill-green" : imp.fit_tone === "amber" ? "pill-amber" : "pill-red";
  const tiFit = document.getElementById("tiFitPill");
  tiFit.textContent = `Fit ${imp.fit_score}/100`;
  tiFit.className = `pill ${pillCls}`;

  const miniBadge = document.getElementById("fitMiniBadge");
  if (miniBadge) miniBadge.textContent = `${imp.fit_score}`;

  document.getElementById("tiSymLbl").textContent = imp.symbol;
  document.getElementById("tiWeightVal").textContent = `${d.symbol_weight_before_pct.toFixed(1)}% → ${d.symbol_weight_after_pct.toFixed(1)}%`;
  const rSign = d.expected_return_delta_pct >= 0 ? "+" : "";
  document.getElementById("tiRetVal").textContent = `${b.expected_return_pct}% → ${a.expected_return_pct}% (${rSign}${d.expected_return_delta_pct}%)`;
  document.getElementById("tiBetaVal").textContent = `${b.beta.toFixed(2)} → ${a.beta.toFixed(2)}`;
  document.getElementById("tiVolVal").textContent = `${b.volatility_pct}% → ${a.volatility_pct}%`;

  const showOpt = imp.side === "BUY" && imp.suggested_optimal_qty > 0 && imp.suggested_optimal_qty < imp.quantity;
  const sizingRow = document.getElementById("tiSizingRow");
  if (showOpt) {
    document.getElementById("tiSizingMsg").textContent = `Óptimo para tu perfil: ${imp.suggested_optimal_qty} acc.`;
    document.getElementById("btnApplyOptimalTicket").textContent = `Usar ${imp.suggested_optimal_qty} acc.`;
    sizingRow.classList.remove("hidden");
  } else {
    sizingRow.classList.add("hidden");
  }

  // 2. Vista Completa "Cartera & Perfil"
  const ring = document.getElementById("pfScoreRing");
  ring.textContent = imp.fit_score;
  ring.className = `pf-score-ring ${imp.fit_tone === "green" ? "" : imp.fit_tone}`;
  document.getElementById("pfFitLabel").textContent = `${imp.fit_label} (${imp.fit_score}/100)`;
  document.getElementById("pfAssetMetaBadge").textContent = `${imp.symbol} · ${imp.asset_sector} · Beta ${imp.asset_beta} · Vol ${imp.asset_vol}%`;
  document.getElementById("pfSizingAdvice").textContent = imp.sizing_advice;

  const optFullBtn = document.getElementById("btnApplyOptimalFull");
  if (showOpt) {
    optFullBtn.textContent = `Aplicar ${imp.suggested_optimal_qty} acciones óptimas`;
    optFullBtn.classList.remove("hidden");
  } else {
    optFullBtn.classList.add("hidden");
  }

  document.getElementById("pfOrderSummaryLbl").textContent =
    `${imp.side} ${imp.quantity} ${imp.symbol} @ $${imp.limit_price.toFixed(2)} ($${imp.order_value_usd.toLocaleString("en-US")})`;

  // Alertas y puntos fuertes
  document.getElementById("pfWarningsList").innerHTML = imp.warnings.map((w) => `<li><strong>RIESGO:</strong> ${w}</li>`).join("");
  document.getElementById("pfPositivesList").innerHTML = imp.positives.map((p) => `<li class="ok-flag"><strong>OK:</strong> ${p}</li>`).join("");

  // Tabla comparativa Antes -> Después
  const fmtD = (val, unit = "", invertGood = false) => {
    const s = val >= 0 ? "+" : "";
    const good = invertGood ? val <= 0 : val >= 0;
    const cls = Math.abs(val) < 0.005 ? "" : good ? "txt-green" : "txt-red";
    return `<strong class="${cls}">${s}${val.toFixed(2)}${unit}</strong>`;
  };

  const rows = [
    ["Peso de " + imp.symbol, `${d.symbol_weight_before_pct}%`, `${d.symbol_weight_after_pct}%`, fmtD(d.symbol_weight_delta_pct, "%", d.symbol_weight_after_pct > imp.profile_used.max_single_position_pct)],
    ["Rentabilidad Anual Esperada", `${b.expected_return_pct}%`, `${a.expected_return_pct}%`, fmtD(d.expected_return_delta_pct, "%", false)],
    ["Volatilidad Anual (Markowitz)", `${b.volatility_pct}%`, `${a.volatility_pct}%`, fmtD(d.volatility_delta_pct, "%", true)],
    ["Beta frente al Mercado (S&P 500)", `${b.beta.toFixed(2)}`, `${a.beta.toFixed(2)}`, fmtD(d.beta_delta, "", true)],
    ["Ratio de Sharpe (Eficiencia)", `${b.sharpe_ratio.toFixed(2)}`, `${a.sharpe_ratio.toFixed(2)}`, fmtD(d.sharpe_delta, "", false)],
    ["VaR 95% Mensual (Riesgo)", `-$${b.var_95_monthly_usd.toLocaleString("en-US")}`, `-$${a.var_95_monthly_usd.toLocaleString("en-US")}`, fmtD(d.var_95_delta_usd, "$", true)],
    ["Stress Test (-10% Mercado)", `${b.stress_mkt_minus10_pct}%`, `${a.stress_mkt_minus10_pct}%`, fmtD(Number((a.stress_mkt_minus10_pct - b.stress_mkt_minus10_pct).toFixed(2)), "%", false)],
    ["Liquidez Disponible (Cash)", `$${b.cash_usd.toLocaleString("en-US")}`, `$${a.cash_usd.toLocaleString("en-US")}`, `<strong class="${a.cash_usd < 0 ? "txt-red" : ""}">${a.cash_pct}% NAV</strong>`],
    ["Diversificación (Effective N)", `${b.effective_n}`, `${a.effective_n}`, fmtD(Number((a.effective_n - b.effective_n).toFixed(2)), "", false)],
  ];
  document.getElementById("pfImpactTableBody").innerHTML = rows
    .map((r) => `<tr><td>${r[0]}</td><td>${r[1]}</td><td><strong>${r[2]}</strong></td><td>${r[3]}</td></tr>`)
    .join("");

  // Barras sectoriales Antes vs Después
  document.getElementById("pfSectorBars").innerHTML = imp.sector_comparison
    .map((s) => {
      const ds = s.delta_pct >= 0 ? `+${s.delta_pct}%` : `${s.delta_pct}%`;
      return `
        <div class="pf-sec-row">
          <div class="pf-sec-head">
            <span><strong>${s.sector}</strong>${s.is_target_sector ? " (Orden)" : ""}</span>
            <span>${s.before_pct}% → <strong>${s.after_pct}%</strong> (${ds})</span>
          </div>
          <div class="pf-sec-track">
            <div class="pf-sec-fill ${s.is_target_sector ? "target" : ""}" style="width:${Math.min(100, s.after_pct)}%"></div>
          </div>
        </div>`;
    })
    .join("");

  // Tabla editable de posiciones (Antes vs Nuevo Peso)
  const beforeWeightMap = {};
  b.positions.forEach((p) => (beforeWeightMap[p.symbol] = p.weight_pct));

  document.getElementById("pfHoldingsTableWrap").innerHTML = `
    <table class="orders-table">
      <thead>
        <tr>
          <th>Ticker</th>
          <th>Sector</th>
          <th>Acciones</th>
          <th>Precio Ref.</th>
          <th>Peso Actual</th>
          <th>Nuevo Peso (Tras Orden)</th>
          <th>Beta</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        ${a.positions
          .map((pos) => {
            const bw = beforeWeightMap[pos.symbol] || 0;
            const isTarget = pos.symbol === imp.symbol;
            const holdingObj = state.portfolio ? state.portfolio.holdings.find((h) => h.symbol.toUpperCase() === pos.symbol) : null;
            return `
              <tr style="${isTarget ? "background:#eff6ff;" : ""}">
                <td><strong>${pos.symbol}</strong> ${isTarget ? '<span class="pill pill-green">Orden</span>' : ""}</td>
                <td>${pos.sector}</td>
                <td>
                  ${
                    holdingObj
                      ? `<input type="number" min="1" step="1" value="${holdingObj.shares}" class="m-num-input pf-sh-edit" data-sym="${pos.symbol}" />`
                      : `<span>0 → <strong>${pos.shares}</strong></span>`
                  }
                </td>
                <td>$${pos.price.toFixed(2)}</td>
                <td>${bw.toFixed(1)}%</td>
                <td><strong>${pos.weight_pct.toFixed(1)}%</strong></td>
                <td>${pos.beta.toFixed(2)}</td>
                <td>${holdingObj ? `<button class="ib-btn-outline-sm pf-del-btn" data-sym="${pos.symbol}">✕</button>` : ""}</td>
              </tr>`;
          })
          .join("")}
      </tbody>
    </table>`;

  document.querySelectorAll(".pf-sh-edit").forEach((inp) => {
    inp.addEventListener("change", (e) => {
      const sym = e.target.dataset.sym;
      const val = Math.max(1, parseFloat(e.target.value || "1"));
      const h = state.portfolio.holdings.find((x) => x.symbol.toUpperCase() === sym);
      if (h) {
        h.shares = val;
        savePortfolioAndRefresh();
      }
    });
  });

  document.querySelectorAll(".pf-del-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const sym = e.target.dataset.sym;
      state.portfolio.holdings = state.portfolio.holdings.filter((x) => x.symbol.toUpperCase() !== sym);
      savePortfolioAndRefresh();
    });
  });
}


function renderFillProbability(fp) {
  if (!fp) return;
  const pct = Number(fp.smart_limit_prob_pct || 0);
  const pillCls = fp.tone === "green" ? "pill-green" : fp.tone === "amber" ? "pill-amber" : "pill-red";
  const barCol = fp.tone === "green" ? "#00894c" : fp.tone === "amber" ? "#d97706" : "#d92d20";

  const mainEl = document.getElementById("fpMainPct");
  mainEl.textContent = `${pct.toFixed(1)}% · ${fp.horizon_label}`;
  mainEl.className = `pill ${pillCls}`;

  const bar = document.getElementById("fpBarFill");
  bar.style.width = `${Math.min(100, Math.max(3, pct))}%`;
  bar.style.background = barCol;

  const gSign = fp.gap_pct > 0 ? "+" : "";
  document.getElementById("fpGapVolLbl").textContent = `Gap ${gSign}${fp.gap_pct}% · Vol ${fp.realized_annual_vol_pct}%`;
  document.getElementById("fpTouchPct").textContent = `${fp.standard_limit_prob_pct}%`;
  document.getElementById("fpPassPct").textContent = `${fp.filter_pass_rate_pct}%`;

  if (fp.horizons) {
    document.getElementById("fpDayPct").textContent = `${fp.horizons.day_pct}%`;
    document.getElementById("fpWeekPct").textContent = `${fp.horizons.week_pct}%`;
    document.getElementById("fpMonthPct").textContent = `${fp.horizons.month_pct}%`;
  }
  document.getElementById("fpBottleneck").textContent = fp.bottleneck || "";
}
