// Estado global · ShieldOrder B2B v4.0 (Modo Estándar/Pro + Monte Carlo + Smart Tranches + Consola Ejecutiva B2B)
const state = {
  symbol: "ASML",
  timeframe: "5m",
  caseId: null,
  side: "BUY",
  uiMode: localStorage.getItem("shieldorder_ui_mode") || "standard", // "standard" | "pro"
  protectionEnabled: true,
  protectionMode: "SMART_TRANCHES",
  preset: "AUTO_SIGMA",
  portfolioTightening: 1.0,
  enabledMetrics: {
    velocity: true,
    rvol: true,
    atr: true,
    rsi: true,
    divergence: true,
    gap: true,
    earnings: true,
  },
  portfolio: null,
  lastImpact: null,
  lastAssetStats: null,
  metricLikelihoods: null,
  lastOptimalPrice: null,
  lastAuditLog: [],
  geminiKey: localStorage.getItem("shieldorder_gemini_key") || "",
  realCases: [],
  chart: null,
  candleSeries: null,
  volumeSeries: null,
  limitLine: null,
  livePollTimer: null,
};

document.addEventListener("DOMContentLoaded", async () => {
  applyUiMode(state.uiMode);
  initIBChart();
  bindEvents();
  bindPortfolioEvents();
  bindExecutiveEvents();
  updateGeminiIndicator();
  await loadPortfolioState();
  await loadRealCases();
  await loadMarketAndSimulate(true);
  await refreshBrokerROI();
  startLivePolling();
});

function applyUiMode(mode) {
  state.uiMode = mode === "pro" ? "pro" : "standard";
  localStorage.setItem("shieldorder_ui_mode", state.uiMode);
  const isPro = state.uiMode === "pro";
  document.body.classList.toggle("pro-mode", isPro);
  const btnStd = document.getElementById("btnModeStandard");
  const btnPro = document.getElementById("btnModePro");
  if (btnStd) btnStd.classList.toggle("active", !isPro);
  if (btnPro) btnPro.classList.toggle("active", isPro);

  const titleEl = document.getElementById("metricsCardTitle");
  const descEl = document.getElementById("metricsCardDesc");
  if (titleEl && descEl) {
    if (isPro) {
      titleEl.textContent = "Control Cuantitativo de Métricas (Modo Pro)";
      descEl.textContent = "Edita las 5 métricas + Gap/Earnings Guard. El gradiente muestra la probabilidad de cada umbral en este activo:";
    } else {
      titleEl.textContent = "Control de Métricas ShieldOrder";
      descEl.textContent = "Ajusta cada métrica a tu gusto. El gradiente indica en tiempo real la probabilidad de que ocurra el umbral en este activo.";
    }
  }
  const tDetails = document.getElementById("ticketMetricsDetails");
  if (tDetails && isPro) {
    tDetails.open = true;
  }
}

// ============================================================
// UTILIDADES DE GRADIENTE VERDE-AMARILLO-ROJO POR PROBABILIDAD
// ============================================================
const TICKET_PROB_IDS = {
  velocity: "probTickVelocity",
  rvol: "probTickRvol",
  atr: "probTickAtr",
  rsi: "probTickRsi",
  divergence: "probTickDivergence",
  gap: "probTickGap",
};

function zoneColorHex(zone) {
  if (zone === "green") return "#087f5b";
  if (zone === "amber") return "#d97706";
  return "#c92a2a";
}

function buildProbGradient(meta) {
  if (!meta || !meta.stops) {
    return "linear-gradient(90deg, #10b981 0%, #84cc16 25%, #f59e0b 52%, #ef4444 78%, #991b1b 100%)";
  }
  const [s0, s1, s2] = meta.stops;
  const m01 = ((s0 + s1) / 2).toFixed(1);
  const m12 = ((s1 + s2) / 2).toFixed(1);
  if (meta.reversed) {
    return `linear-gradient(90deg, #991b1b 0%, #dc2626 ${s0}%, #f97316 ${m01}%, #f59e0b ${s1}%, #84cc16 ${m12}%, #10b981 ${s2}%, #059669 100%)`;
  }
  return `linear-gradient(90deg, #059669 0%, #10b981 ${s0}%, #84cc16 ${m01}%, #f59e0b ${s1}%, #f97316 ${m12}%, #dc2626 ${s2}%, #991b1b 100%)`;
}

function evalLiveMetricProb(key, val) {
  const ml = state.metricLikelihoods;
  const meta = ml && ml.metrics ? ml.metrics[key] : null;
  const charVal = meta ? meta.char_val : (key === "rsi" ? 34.0 : 2.5);
  let prob = 50.0;
  if (key === "velocity") {
    prob = Math.exp(-Math.pow(val / Math.max(0.5, charVal), 1.35)) * 100.0;
  } else if (key === "rvol") {
    prob = Math.exp(-Math.pow(Math.max(0.05, val - 0.45) / Math.max(0.5, charVal), 1.35)) * 100.0;
  } else if (key === "atr") {
    prob = Math.exp(-Math.pow(Math.max(0.05, val - 0.35) / Math.max(0.5, charVal), 1.35)) * 100.0;
  } else if (key === "rsi") {
    prob = (1.0 / (1.0 + Math.exp(-(val - charVal) / 8.5))) * 100.0;
  } else if (key === "divergence") {
    prob = Math.exp(-Math.pow(val / Math.max(0.5, charVal), 1.30)) * 100.0;
  } else if (key === "gap") {
    prob = Math.exp(-Math.pow(val / Math.max(0.5, charVal), 1.30)) * 100.0;
  }
  prob = Math.max(2.0, Math.min(98.0, Math.round(prob * 10) / 10));
  const zone = prob >= 52.0 ? "green" : prob >= 20.0 ? "amber" : "red";
  const label = prob >= 52.0 ? "Probable" : prob >= 20.0 ? "Moderado" : "Poco probable";
  return { prob, zone, label, meta };
}

function applyGradientToInput(inputEl, key, val) {
  if (!inputEl) return;
  const info = evalLiveMetricProb(key, val);
  inputEl.style.setProperty("--grad", buildProbGradient(info.meta));
  inputEl.style.setProperty("--thumb-color", zoneColorHex(info.zone));

  const tickTagId = TICKET_PROB_IDS[key];
  const tickTag = tickTagId ? document.getElementById(tickTagId) : null;
  if (tickTag) {
    tickTag.textContent = `${Math.round(info.prob)}% · ${info.label}`;
    tickTag.className = `sl-prob-tag zone-${info.zone}`;
  }

  const inlineChip = document.querySelector(`.m-prob-chip[data-mkey="${key}"]`);
  if (inlineChip) {
    inlineChip.textContent = `Prob. suceso: ${Math.round(info.prob)}% · ${info.label}`;
    inlineChip.className = `m-prob-chip zone-${info.zone}`;
  }
  const inlineRange = document.querySelector(`.m-inline-range[data-mkey="${key}"]`);
  if (inlineRange && inlineRange !== inputEl) {
    inlineRange.style.setProperty("--grad", buildProbGradient(info.meta));
    inlineRange.style.setProperty("--thumb-color", zoneColorHex(info.zone));
  }
}

function refreshAllTicketGradients() {
  const map = [
    ["velocity", "slDrop"],
    ["rvol", "slRvol"],
    ["atr", "slAtr"],
    ["rsi", "slRsi"],
    ["divergence", "slDiv"],
    ["gap", "slGap"],
  ];
  map.forEach(([key, id]) => {
    const el = document.getElementById(id);
    if (el) applyGradientToInput(el, key, parseFloat(el.value));
  });
}

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
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: "#e2e8f0" },
    timeScale: { borderColor: "#e2e8f0", timeVisible: true, secondsVisible: false },
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
  state.volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.80, bottom: 0 } });

  state.chart.subscribeCrosshairMove((param) => {
    if (!param || !param.time) return;
    const bar = param.seriesData.get(state.candleSeries);
    const srcEl = document.getElementById("chartSourceLabel");
    if (bar && srcEl) {
      srcEl.innerHTML = `O <strong>${bar.open}</strong> &nbsp; H <strong>${bar.high}</strong> &nbsp; L <strong>${bar.low}</strong> &nbsp; C <strong>${bar.close}</strong>`;
    }
  });

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
      state.protectionMode = c.recommended_mode || "SMART_TRANCHES";
      state.preset = "AUTO_SIGMA";
      syncModeButtons();
      syncPresetButtons();
      markRibbonActive(null, c.id);
      activateSubtab("viewQuote");
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

function activateSubtab(viewId) {
  document.querySelectorAll(".subtab-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === viewId));
  ["viewQuote", "viewPortfolio", "viewOrders", "viewExecutive", "viewOmsJson"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.classList.toggle("hidden", viewId !== id);
  });
  // Ocultar gráfico cuando estamos en la Consola Ejecutiva B2B para que respire limpia
  const chartCard = document.getElementById("mainChartCard");
  if (chartCard) chartCard.classList.toggle("hidden", viewId === "viewExecutive");
  if (viewId === "viewExecutive") {
    refreshBrokerROI();
    loadAuditLog();
  }
}

function bindEvents() {
  // Selector Modo Estándar / Pro
  document.getElementById("btnModeStandard").addEventListener("click", () => applyUiMode("standard"));
  document.getElementById("btnModePro").addEventListener("click", () => applyUiMode("pro"));
  document.getElementById("btnSwitchProInline").addEventListener("click", () => applyUiMode("pro"));

  // Botones rápidos de acciones en vivo
  document.querySelectorAll(".ribbon-pill[data-live]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.caseId = null;
      state.symbol = btn.dataset.live;
      state.portfolioTightening = 1.0;
      markRibbonActive(state.symbol, null);
      activateSubtab("viewQuote");
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
    activateSubtab("viewQuote");
    await loadMarketAndSimulate(true);
  });

  document.getElementById("navRealCases").addEventListener("click", async (e) => {
    e.preventDefault();
    if (state.realCases.length > 0) {
      const c = state.realCases[0];
      state.caseId = c.id;
      state.symbol = c.symbol;
      state.protectionMode = c.recommended_mode;
      syncModeButtons();
      markRibbonActive(null, c.id);
      activateSubtab("viewQuote");
      await loadMarketAndSimulate(true);
    }
  });

  document.getElementById("navExecutive").addEventListener("click", (e) => {
    e.preventDefault();
    activateSubtab("viewExecutive");
  });

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

  // Buscador global (Ctrl+K)
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
          state.portfolioTightening = 1.0;
          searchInput.value = "";
          dropdown.classList.add("hidden");
          markRibbonActive(state.symbol, null);
          activateSubtab("viewQuote");
          await loadMarketAndSimulate(true);
        });
      });
    }, 220);
  });

  searchInput.addEventListener("keydown", async (e) => {
    if (e.key === "Enter" && searchInput.value.trim()) {
      state.caseId = null;
      state.symbol = searchInput.value.trim().toUpperCase();
      state.portfolioTightening = 1.0;
      searchInput.value = "";
      dropdown.classList.add("hidden");
      markRibbonActive(state.symbol, null);
      activateSubtab("viewQuote");
      await loadMarketAndSimulate(true);
    }
  });

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".ib-search-wrap")) dropdown.classList.add("hidden");
  });

  document.querySelectorAll(".subtab-btn").forEach((btn) => {
    btn.addEventListener("click", () => activateSubtab(btn.dataset.view));
  });
  document.getElementById("navPortfolio").addEventListener("click", (e) => {
    e.preventDefault();
    activateSubtab("viewPortfolio");
  });
  document.getElementById("btnOpenPortfolioTab").addEventListener("click", () => activateSubtab("viewPortfolio"));

  // Buy / Sell
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

  document.getElementById("btnExportPdf").addEventListener("click", () => window.print());

  // Steppers
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
  document.getElementById("inputLimitPrice").addEventListener("change", () => loadMarketAndSimulate(false));
  document.getElementById("selectTif").addEventListener("change", () => loadMarketAndSimulate(false));

  // Botón 1 clic para aplicar el Precio Límite Óptimo sugerido por Monte Carlo
  document.getElementById("btnApplyOptimalPrice").addEventListener("click", () => {
    if (!state.lastOptimalPrice) return;
    document.getElementById("inputLimitPrice").value = Number(state.lastOptimalPrice).toFixed(2);
    updateEstTotal();
    loadMarketAndSimulate(false);
  });

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
      if (state.preset === "CUSTOM") {
        const td = document.getElementById("ticketMetricsDetails");
        if (td) td.open = true;
      } else if (state.preset !== "AUTO_SIGMA") {
        applyPresetToSliders(state.preset);
      }
      syncPresetButtons();
      refreshAllTicketGradients();
      loadMarketAndSimulate(false);
    });
  });

  const wireSlider = (id, mkey, cb) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("input", (e) => {
      state.preset = "CUSTOM";
      syncPresetButtons();
      cb(e);
      if (mkey) {
        const val = parseFloat(e.target.value);
        applyGradientToInput(e.target, mkey, val);
        const inlineRange = document.querySelector(`.m-inline-range[data-mkey="${mkey}"]`);
        const inlineNum = document.querySelector(`.m-inline-num[data-mkey="${mkey}"]`);
        if (inlineRange) inlineRange.value = val;
        if (inlineNum) inlineNum.value = val;
      }
    });
    el.addEventListener("change", () => loadMarketAndSimulate(false));
  };
  wireSlider("slDrop", "velocity", (e) => (document.getElementById("lblDrop").textContent = `${parseFloat(e.target.value).toFixed(1)}%`));
  wireSlider("slWin", null, (e) => {
    document.getElementById("lblWin").textContent = e.target.value;
    document.getElementById("lblWinVal").textContent = `${e.target.value} min`;
  });
  wireSlider("slRvol", "rvol", (e) => (document.getElementById("lblRvol").textContent = `${parseFloat(e.target.value).toFixed(1)}x`));
  wireSlider("slAtr", "atr", (e) => (document.getElementById("lblAtr").textContent = `${parseFloat(e.target.value).toFixed(1)}x`));
  wireSlider("slRsi", "rsi", (e) => (document.getElementById("lblRsi").textContent = e.target.value));
  wireSlider("slDiv", "divergence", (e) => (document.getElementById("lblDiv").textContent = `${parseFloat(e.target.value).toFixed(1)}%`));
  wireSlider("slGap", "gap", (e) => (document.getElementById("lblGap").textContent = `${parseFloat(e.target.value).toFixed(1)}%`));
  wireSlider("slTox", null, (e) => (document.getElementById("lblTox").textContent = `${e.target.value}/100`));

  document.getElementById("chkEarningsGuard").addEventListener("change", (e) => {
    state.enabledMetrics.earnings = e.target.checked;
    state.preset = "CUSTOM";
    syncPresetButtons();
    loadMarketAndSimulate(false);
  });

  document.getElementById("btnPreviewBacktest").addEventListener("click", () => loadMarketAndSimulate(false));
  document.getElementById("btnPlaceLiveOrder").addEventListener("click", placeLiveOrder);
  document.getElementById("btnRefreshLive").addEventListener("click", refreshLiveQuoteOnly);
  document.getElementById("btnRunCopilot").addEventListener("click", runCopilot);

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

function syncSlidersFromRules(r) {
  if (!r) return;
  document.getElementById("slDrop").value = r.max_drop_pct;
  document.getElementById("lblDrop").textContent = `${Number(r.max_drop_pct).toFixed(1)}%`;
  document.getElementById("slWin").value = r.lookback_minutes;
  document.getElementById("lblWin").textContent = r.lookback_minutes;
  document.getElementById("lblWinVal").textContent = `${r.lookback_minutes} min`;
  document.getElementById("slRvol").value = r.max_rvol;
  document.getElementById("lblRvol").textContent = `${Number(r.max_rvol).toFixed(1)}x`;
  document.getElementById("slAtr").value = r.max_atr_multiple;
  document.getElementById("lblAtr").textContent = `${Number(r.max_atr_multiple).toFixed(1)}x`;
  document.getElementById("slRsi").value = r.min_rsi;
  document.getElementById("lblRsi").textContent = Math.round(r.min_rsi);
  document.getElementById("slDiv").value = r.max_benchmark_divergence_pct;
  document.getElementById("lblDiv").textContent = `${Number(r.max_benchmark_divergence_pct).toFixed(1)}%`;
  if (r.max_gap_pct !== undefined) {
    document.getElementById("slGap").value = r.max_gap_pct;
    document.getElementById("lblGap").textContent = `${Number(r.max_gap_pct).toFixed(1)}%`;
  }
  document.getElementById("slTox").value = r.toxicity_threshold;
  document.getElementById("lblTox").textContent = `${Math.round(r.toxicity_threshold)}/100`;
}

function applyPresetToSliders(preset) {
  const presets = {
    CONSERVATIVE: { max_drop_pct: 2.8, lookback_minutes: 60, max_rvol: 2.0, max_atr_multiple: 2.0, min_rsi: 32, max_benchmark_divergence_pct: 2.5, max_gap_pct: 2.5, toxicity_threshold: 48 },
    MODERATE: { max_drop_pct: 4.0, lookback_minutes: 60, max_rvol: 2.5, max_atr_multiple: 2.5, min_rsi: 28, max_benchmark_divergence_pct: 3.5, max_gap_pct: 3.5, toxicity_threshold: 60 },
    AGGRESSIVE: { max_drop_pct: 6.0, lookback_minutes: 45, max_rvol: 3.5, max_atr_multiple: 3.2, min_rsi: 22, max_benchmark_divergence_pct: 5.0, max_gap_pct: 5.0, toxicity_threshold: 72 },
  };
  if (presets[preset]) syncSlidersFromRules(presets[preset]);
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
    max_gap_pct: parseFloat(document.getElementById("slGap").value),
    toxicity_threshold: parseFloat(document.getElementById("slTox").value),
    enable_velocity: state.enabledMetrics.velocity,
    enable_rvol: state.enabledMetrics.rvol,
    enable_atr: state.enabledMetrics.atr,
    enable_rsi: state.enabledMetrics.rsi,
    enable_divergence: state.enabledMetrics.divergence,
    enable_gap_guard: state.enabledMetrics.gap,
    enable_earnings_guard: state.enabledMetrics.earnings,
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
        portfolio_tightening: state.portfolioTightening,
        gemini_api_key: state.geminiKey || null,
      }),
    });

    if (!resp.ok) {
      const err = await resp.json();
      alert(err.detail || `Error cargando ${state.symbol}`);
      return;
    }

    const data = await resp.json();
    state.lastAssetStats = data.asset_stats || null;
    if (useSuggestedPrice || limitVal <= 0) {
      document.getElementById("inputLimitPrice").value = Number(data.effective_limit_price).toFixed(2);
    }
    if (data.simulation && data.simulation.applied_rules && state.preset === "AUTO_SIGMA") {
      syncSlidersFromRules(data.simulation.applied_rules);
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
  if (state.caseId) return;
  try {
    const resp = await fetch(`/api/v1/market/quote?symbol=${encodeURIComponent(state.symbol)}&timeframe=${encodeURIComponent(state.timeframe)}`);
    if (!resp.ok) return;
    const data = await resp.json();
    renderQuoteHeader(data.quote, data.live_toxicity, data.case_info, data.asset_stats, data.earnings_label);
    renderOrdersTable(data.active_orders || []);
  } catch (e) {}
}

function startLivePolling() {
  if (state.livePollTimer) clearInterval(state.livePollTimer);
  state.livePollTimer = setInterval(refreshLiveQuoteOnly, 15000);
}

function renderQuoteHeader(q, liveTox, caseInfo, assetStats, earningsLabel) {
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

  const catPill = document.getElementById("qCatalystPill");
  if (catPill) {
    if (earningsLabel) {
      catPill.textContent = earningsLabel.toUpperCase();
      catPill.classList.remove("hidden");
    } else {
      catPill.classList.add("hidden");
    }
  }

  if (assetStats) {
    const regEl = document.getElementById("qRegimeVal");
    if (regEl) regEl.textContent = `${assetStats.vol_tier} (${assetStats.annual_vol_pct}% · β ${assetStats.empirical_beta})`;
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
  renderQuoteHeader(data.quote, data.live_toxicity, data.case_info, data.asset_stats, data.earnings_label);
  renderFillProbability(data.fill_probability);
  const srcEl = document.getElementById("chartSourceLabel");
  if (srcEl) srcEl.textContent = data.data_source;

  // Auto-sigma hint en el Order Ticket
  const cal = data.simulation && data.simulation.calibration_info;
  const sigmaHint = document.getElementById("autoSigmaHint");
  if (sigmaHint && cal) {
    if (state.preset === "AUTO_SIGMA") {
      sigmaHint.innerHTML = `Auto-calibrado a volatilidad <strong>${cal.vol_tier}</strong> (${cal.annual_vol_pct}% anual)${state.portfolioTightening < 0.98 ? ` · <strong>Refuerzo Cartera activo</strong>` : ""}.`;
    } else {
      sigmaHint.innerHTML = `Perfil <strong>${state.preset}</strong> activo sobre ${data.symbol} (Vol. real ${cal.annual_vol_pct}% anual).`;
    }
  }

  // 1. Gráfico de Velas y Volumen
  const dedupMap = new Map();
  (data.candles || []).forEach((c) => dedupMap.set(Number(c.timestamp), c));
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

  state.chart.timeScale().applyOptions({ timeVisible: data.timeframe !== "1d", secondsVisible: false });
  state.candleSeries.setMarkers([]);
  state.candleSeries.setData(candles);
  state.volumeSeries.setData(volumes);
  state.chart.priceScale("right").applyOptions({ autoScale: true });
  state.chart.timeScale().fitContent();

  if (state.limitLine) state.candleSeries.removePriceLine(state.limitLine);
  state.limitLine = state.candleSeries.createPriceLine({
    price: data.effective_limit_price,
    color: "#1d59c9",
    lineWidth: 2,
    lineStyle: LightweightCharts.LineStyle.Dashed,
    axisLabelVisible: true,
    title: `Limit $${data.effective_limit_price}`,
  });

  const sim = data.simulation;
  if (sim && sim.metric_likelihoods) {
    state.metricLikelihoods = sim.metric_likelihoods;
    refreshAllTicketGradients();
  }
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
      color: "#c92a2a",
      shape: "arrowDown",
      text: `Limit Normal $${sim.standard_order.fill_price}`,
    },
  ];
  (sim.chart_events || []).forEach((ev) => {
    const goodFill = ev.type === "SMART_FILL" || ev.type === "SMART_BOUNCE_FILL";
    markers.push({
      time: ev.timestamp,
      position: "belowBar",
      color: goodFill ? "#087f5b" : "#1d59c9",
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
    `Ejecutó a $${std.fill_price} · Extremo tras toque: $${std.lowest_price_after_fill} (${std.max_drawdown_pct}%)`;

  const smartMain = document.getElementById("smartMainVal");
  const smartPill = document.getElementById("smartPill");
  smartPill.textContent = smart.status_label;

  if (["INVALIDATED_PROTECTED", "PAUSED_CONFIRMATION", "EXPIRED_NO_BOUNCE"].includes(smart.status)) {
    smartMain.textContent = `+$${smart.capital_saved_usd.toFixed(2)} protegidos`;
    smartMain.className = "comp-number txt-green";
    smartPill.className = "pill pill-green";
    document.getElementById("smartSubVal").textContent = `Caída tóxica bloqueada · $0 pérdida sufrida`;
  } else {
    const mSign = smart.final_pnl_usd >= 0 ? "+" : "";
    smartMain.textContent = `${mSign}$${smart.final_pnl_usd.toFixed(2)} (${mSign}${smart.final_pnl_pct}%)`;
    smartMain.className = `comp-number ${smart.final_pnl_usd >= 0 ? "txt-green" : "txt-red"}`;
    smartPill.className = "pill pill-green";
    const diffTxt = smart.capital_saved_usd > 0 ? ` · Mejora +$${smart.capital_saved_usd.toFixed(2)} vs Limit ciega` : " · Flujo sano verificado";
    document.getElementById("smartSubVal").textContent = `Precio medio $${smart.fill_price}${diffTxt}`;
  }

  const volRow = document.getElementById("volPreservedRow");
  const volVal = document.getElementById("volPreservedVal");
  if (volRow && volVal) {
    volVal.textContent = `${smart.broker_volume_preserved_pct !== undefined ? smart.broker_volume_preserved_pct : 100}%`;
    volRow.classList.remove("hidden");
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
  toxBar.style.background = danger ? "#c92a2a" : "#087f5b";
  document.getElementById("toxSub").textContent = danger
    ? `Flujo tóxico detectado (umbral ${th}/100)`
    : `Flujo sano (por debajo de umbral ${th}/100)`;

  // 4b. Telemetría de Microestructura (Modo Pro)
  document.getElementById("mcVwap").textContent = `${m.vwap_distance_pct >= 0 ? "+" : ""}${m.vwap_distance_pct}%`;
  document.getElementById("mcAbsorb").textContent = `${Math.round((m.absorption_ratio || 0) * 100)}% mecha`;
  document.getElementById("mcVpin").textContent = `${Math.round((m.vpin_lite || 0) * 100)}%`;
  document.getElementById("mcGap").textContent = `${m.opening_gap_pct}%`;
  document.getElementById("mcSpread").textContent = `${m.spread_bps} bps`;
  document.getElementById("mcImbal").textContent = `${m.order_book_imbalance >= 0 ? "+" : ""}${m.order_book_imbalance}`;

  // 5a. Resumen limpio para Modo Estándar
  if (cal) {
    document.getElementById("stdRegimeBanner").textContent = cal.explanation;
  }
  const tranchesBox = document.getElementById("stdTranchesBox");
  if (smart.tranches && smart.tranches.length) {
    tranchesBox.innerHTML = `
      <strong>Ejecución en Tramos Adaptativos (Precio medio $${smart.fill_price} · ${smart.broker_volume_preserved_pct}% volumen ejecutado):</strong>
      <ul>
        ${smart.tranches.map((t) => `<li><strong>Tramo ${t.tranche} (${t.pct}% · ${t.qty} acc.):</strong> $${t.price.toFixed(2)} — ${t.reason}</li>`).join("")}
      </ul>`;
    tranchesBox.classList.remove("hidden");
  } else {
    tranchesBox.classList.add("hidden");
  }

  // 5b. Barras INTERACTIVAS de las 6 métricas (Modo Pro) + Pills resumen (Modo Estándar)
  const applied = sim.applied_rules;
  const isSell = state.side === "SELL";
  const items = [
    {
      key: "velocity", sliderId: "slDrop", lblId: "lblDrop", suffix: "%",
      name: isSell ? "1. Velocidad Subida (ΔP/Δt)" : "1. Velocidad Caída (ΔP/Δt)",
      short: `Velocidad ${m.drop_pct_in_window}%`,
      val: `${isSell ? "+" : "-"}${m.drop_pct_in_window}% (Acel. ${m.acceleration_ratio}x)`,
      score: m.velocity_score, ruleVal: applied.max_drop_pct, min: 0.5, max: 15.0, step: 0.5,
      rulePrefix: isSell ? "Bloquear si sube >=" : "Bloquear si cae >=",
      breached: state.enabledMetrics.velocity && m.drop_pct_in_window >= applied.max_drop_pct,
    },
    {
      key: "rvol", sliderId: "slRvol", lblId: "lblRvol", suffix: "x",
      name: "2. Volumen Relativo (RVOL)", short: `RVOL ${m.rvol}x`,
      val: `${m.rvol}x media normal`, score: m.volume_score, ruleVal: applied.max_rvol,
      min: 0.5, max: 6.0, step: 0.1, rulePrefix: "Bloquear si RVOL >=",
      breached: state.enabledMetrics.rvol && m.rvol >= applied.max_rvol,
    },
    {
      key: "atr", sliderId: "slAtr", lblId: "lblAtr", suffix: "x",
      name: "3. Expansión Volatilidad (ATR)", short: `ATR ${m.atr_multiple}x`,
      val: `${m.atr_multiple}x rango habitual`, score: m.volatility_score, ruleVal: applied.max_atr_multiple,
      min: 0.4, max: 6.0, step: 0.1, rulePrefix: "Bloquear si ATR >=",
      breached: state.enabledMetrics.atr && m.atr_multiple >= applied.max_atr_multiple,
    },
    {
      key: "rsi", sliderId: "slRsi", lblId: "lblRsi", suffix: "",
      name: "4. Momentum RSI (14)", short: `RSI ${m.rsi_14}`,
      val: `RSI ${m.rsi_14}`, score: m.momentum_score, ruleVal: applied.min_rsi,
      min: 10, max: 75, step: 1, rulePrefix: isSell ? "Bloquear si RSI >=" : "Bloquear si RSI <=",
      breached: state.enabledMetrics.rsi && (isSell ? m.rsi_14 >= 100 - applied.min_rsi : m.rsi_14 <= applied.min_rsi),
    },
    {
      key: "divergence", sliderId: "slDiv", lblId: "lblDiv", suffix: "%",
      name: "5. Divergencia vs Índice", short: `Diverg. ${m.benchmark_divergence_pct}%`,
      val: `${isSell ? "+" : "-"}${m.benchmark_divergence_pct}% extra`, score: m.divergence_score, ruleVal: applied.max_benchmark_divergence_pct,
      min: 0.2, max: 12.0, step: 0.2, rulePrefix: "Bloquear si diverge >=",
      breached: state.enabledMetrics.divergence && m.benchmark_divergence_pct >= applied.max_benchmark_divergence_pct,
    },
    {
      key: "gap", sliderId: "slGap", lblId: "lblGap", suffix: "%",
      name: "6. Gap / Catalyst Guard", short: `Gap ${m.opening_gap_pct}%`,
      val: `Gap ${m.opening_gap_pct}%${m.earnings_window_active ? " · EVENTO ACTIVO" : ""}`,
      score: m.catalyst_score, ruleVal: applied.max_gap_pct,
      min: 0.5, max: 10.0, step: 0.5, rulePrefix: "Bloquear si Gap >=",
      breached: (state.enabledMetrics.gap && m.opening_gap_pct >= applied.max_gap_pct) || (state.enabledMetrics.earnings && m.earnings_window_active),
    },
  ];

  const metricsEl = document.getElementById("metricsList");
  metricsEl.innerHTML = items
    .map((it) => {
      const enabled = state.enabledMetrics[it.key];
      const info = evalLiveMetricProb(it.key, it.ruleVal);
      const gradCss = buildProbGradient(info.meta);
      const thumbCol = zoneColorHex(info.zone);
      const col = !enabled ? "#94a3b8" : it.breached || it.score >= 65 ? "#c92a2a" : it.score >= 40 ? "#b45309" : "#087f5b";
      const proCls = it.key === "gap" ? "pro-only" : "";
      const cls = `metric-card-row ${it.breached ? "breached" : ""} ${!enabled ? "disabled" : ""} ${proCls}`;
      return `
        <div class="${cls}">
          <div class="m-head">
            <label class="m-check-label">
              <input type="checkbox" class="m-toggle" data-key="${it.key}" ${enabled ? "checked" : ""} />
              <span>${it.name}</span>
            </label>
            <div class="m-head-right">
              <strong>${it.val}</strong>
            </div>
          </div>
          <div class="m-bar-bg">
            <div class="m-bar-fill" style="width:${Math.min(100, Math.max(4, it.score))}%;background:${col}"></div>
          </div>
          <div class="m-editor-top-meta">
            <span>${it.rulePrefix} <strong>${it.ruleVal}${it.suffix}</strong></span>
            <span class="m-prob-chip zone-${info.zone}" data-mkey="${it.key}">Prob. suceso: ${Math.round(info.prob)}% · ${info.label}</span>
          </div>
          <div class="m-editor-row">
            <input type="range" class="prob-range m-inline-range" data-mkey="${it.key}" data-slider="${it.sliderId}" data-lbl="${it.lblId}" data-suffix="${it.suffix}" min="${it.min}" max="${it.max}" step="${it.step}" value="${it.ruleVal}" style="--grad:${gradCss};--thumb-color:${thumbCol};" ${!enabled ? "disabled" : ""} />
            <input type="number" class="m-num-input m-inline-num" data-mkey="${it.key}" data-slider="${it.sliderId}" data-lbl="${it.lblId}" data-suffix="${it.suffix}" min="${it.min}" max="${it.max}" step="${it.step}" value="${it.ruleVal}" ${!enabled ? "disabled" : ""} />
            <span>${it.suffix}</span>
          </div>
        </div>`;
    })
    .join("");

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
    const mkey = el.dataset.mkey;
    const sliderId = el.dataset.slider;
    const lblId = el.dataset.lbl;
    const suffix = el.dataset.suffix || "";
    const ticketSlider = document.getElementById(sliderId);
    const ticketLbl = document.getElementById(lblId);
    if (ticketSlider) ticketSlider.value = val;
    if (ticketLbl) ticketLbl.textContent = `${suffix ? val.toFixed(1) : Math.round(val)}${suffix}`;

    const card = el.closest(".metric-card-row");
    if (card) {
      const sibling = el.type === "range" ? card.querySelector(".m-inline-num") : card.querySelector(".m-inline-range");
      if (sibling) sibling.value = val;
      const strongLbl = card.querySelector(".m-editor-top-meta strong");
      if (strongLbl) strongLbl.textContent = `${suffix ? val.toFixed(1) : Math.round(val)}${suffix}`;
    }

    if (mkey) {
      const rng = card ? card.querySelector(".m-inline-range") : el;
      applyGradientToInput(rng, mkey, val);
      if (ticketSlider) applyGradientToInput(ticketSlider, mkey, val);
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
    : `<li class="ok-flag"><strong>OK:</strong> Sin alertas de toxicidad. Movimiento ordenado apto para ejecutar la Limit Order.</li>`;

  // 6. Notificación, Recibo MiFID II y OMS JSON
  document.getElementById("notifTitle").textContent = data.client_notification.headline;
  document.getElementById("notifBody").textContent = data.client_notification.body;
  document.getElementById("notifEngine").textContent = `Motor: ${data.client_notification.generated_by}`;
  document.getElementById("notifConfirmRow").classList.toggle("hidden", smart.status !== "PAUSED_CONFIRMATION");
  document.getElementById("omsJsonPre").textContent = JSON.stringify(sim.b2b_webhook_payload, null, 2);

  if (sim.audit_receipt) {
    document.getElementById("receiptIdBadge").textContent = sim.audit_receipt.receipt_id;
    document.getElementById("receiptHashLine").textContent = `MiFID II SHA-256: ${sim.audit_receipt.deterministic_sha256.slice(0, 32)}...`;
  }
}

function renderFillProbability(fp) {
  if (!fp) return;
  const pct = Number(fp.smart_limit_prob_pct || 0);
  const pillCls = fp.tone === "green" ? "pill-green" : fp.tone === "amber" ? "pill-amber" : "pill-red";
  const barCol = fp.tone === "green" ? "#087f5b" : fp.tone === "amber" ? "#b45309" : "#c92a2a";

  const mainEl = document.getElementById("fpMainPct");
  mainEl.textContent = `${pct.toFixed(1)}% · ${fp.horizon_label}`;
  mainEl.className = `pill ${pillCls}`;

  const bar = document.getElementById("fpBarFill");
  bar.style.width = `${Math.min(100, Math.max(3, pct))}%`;
  bar.style.background = barCol;

  document.getElementById("fpTtfVal").textContent = fp.expected_time_to_fill || "--";
  const gSign = fp.gap_pct > 0 ? "+" : "";
  document.getElementById("fpGapVolLbl").textContent = `Gap ${gSign}${fp.gap_pct}% · Vol ${fp.realized_annual_vol_pct}%`;
  document.getElementById("fpTouchPct").textContent = `${fp.standard_limit_prob_pct}%`;
  document.getElementById("fpPassPct").textContent = `${fp.filter_pass_rate_pct}%`;

  if (fp.horizons) {
    document.getElementById("fpDayPct").textContent = `${fp.horizons.day_pct}%`;
    document.getElementById("fpWeekPct").textContent = `${fp.horizons.week_pct}%`;
    document.getElementById("fpMonthPct").textContent = `${fp.horizons.month_pct}%`;
  }

  const optBox = document.getElementById("fpOptimalBox");
  const opt = fp.optimal_price_suggestion;
  if (opt && opt.suggested_price) {
    state.lastOptimalPrice = opt.suggested_price;
    document.getElementById("fpOptimalMsg").textContent = opt.summary;
    document.getElementById("btnApplyOptimalPrice").textContent = `Usar $${Number(opt.suggested_price).toFixed(2)}`;
    optBox.classList.remove("hidden");
  } else {
    state.lastOptimalPrice = null;
    optBox.classList.add("hidden");
  }

  document.getElementById("fpBottleneck").textContent = fp.bottleneck || "";
  if (fp.simulation_meta) {
    const sm = fp.simulation_meta;
    document.getElementById("fpMcMeta").textContent = `${sm.model} (${sm.paths} trayectorias) · ${sm.tail_regime} · Arrastre correlación: -${sm.correlation_drag_pct}%`;
  }
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
  toast.textContent = `Orden ${data.order.order_id} enviada: ${data.order.side} ${data.order.quantity} ${data.order.symbol} @ $${data.order.limit_price} (${data.order.protection_mode}).`;
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
          <th>ID</th><th>Activo</th><th>Tipo</th><th>Cant.</th><th>Limit Price</th><th>Modo ShieldOrder</th><th>Estado en Vivo</th><th></th>
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
    state.protectionMode = parsed.protection_mode || "SMART_TRANCHES";
    state.preset = "CUSTOM";
    syncModeButtons();
    syncPresetButtons();
    syncSlidersFromRules(parsed);

    const msg = document.getElementById("copilotMsg");
    msg.textContent = parsed.assistant_summary;
    msg.classList.remove("hidden");
    updateEstTotal();
    await loadMarketAndSimulate(false);
  } finally {
    btn.textContent = "Traducir a parámetros";
    btn.disabled = false;
  }
}

// ============================================================
// MÓDULO PRE-TRADE: CARTERA & PERFIL + ACOPLAMIENTO PROTECCIÓN
// ============================================================
const ALL_SECTORS = ["Technology", "Semiconductors", "Healthcare", "Financials", "Consumer Defensive", "Consumer Cyclical", "Energy", "Crypto"];

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
      state.portfolio.profile.preferred_sectors = list.includes(sec) ? list.filter((s) => s !== sec) : [...list, sec];
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

  ["pfRisk", "pfHorizon", "pfStyle"].forEach((id) => document.getElementById(id).addEventListener("change", onProfileChange));
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

  // Botón combo 1 clic: aplica Smart Sizing + Protección calibrada al perfil de cartera
  const applyPortfolioCombo = () => {
    if (!state.lastImpact) return;
    const rp = state.lastImpact.recommended_protection;
    if (state.lastImpact.suggested_optimal_qty > 0 && state.lastImpact.suggested_optimal_qty < state.lastImpact.quantity) {
      document.getElementById("inputQty").value = state.lastImpact.suggested_optimal_qty;
    }
    if (rp) {
      state.protectionMode = rp.recommended_mode || state.protectionMode;
      state.preset = rp.recommended_preset || "AUTO_SIGMA";
      state.portfolioTightening = rp.tightening_factor || 1.0;
      syncModeButtons();
      syncPresetButtons();
    }
    updateEstTotal();
    loadMarketAndSimulate(false);
  };
  document.getElementById("btnApplyOptimalTicket").addEventListener("click", applyPortfolioCombo);
  document.getElementById("btnApplyOptimalFull").addEventListener("click", applyPortfolioCombo);
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
        live_asset_vol_pct: state.lastAssetStats ? state.lastAssetStats.annual_vol_pct : null,
        live_asset_beta: state.lastAssetStats ? state.lastAssetStats.empirical_beta : null,
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
  const rp = imp.recommended_protection;

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

  const showCombo = imp.side === "BUY" && rp && rp.needs_adjustment;
  const sizingRow = document.getElementById("tiSizingRow");
  if (showCombo) {
    document.getElementById("tiSizingMsg").textContent = rp.summary;
    document.getElementById("btnApplyOptimalTicket").textContent = "Aplicar Combo";
    sizingRow.classList.remove("hidden");
  } else {
    sizingRow.classList.add("hidden");
  }

  const ring = document.getElementById("pfScoreRing");
  ring.textContent = imp.fit_score;
  ring.className = `pf-score-ring ${imp.fit_tone === "green" ? "" : imp.fit_tone}`;
  document.getElementById("pfFitLabel").textContent = `${imp.fit_label} (${imp.fit_score}/100)`;
  document.getElementById("pfAssetMetaBadge").textContent = `${imp.symbol} · ${imp.asset_sector} · β ${imp.asset_beta} · Vol ${imp.asset_vol}% (${imp.asset_calibration_source})`;
  document.getElementById("pfSizingAdvice").textContent = imp.sizing_advice;
  document.getElementById("pfProtectionLinkText").textContent = rp ? rp.summary : "";

  const optFullBtn = document.getElementById("btnApplyOptimalFull");
  if (showCombo) {
    optFullBtn.textContent = `Aplicar Sizing + ${rp.recommended_mode_label}`;
    optFullBtn.classList.remove("hidden");
  } else {
    optFullBtn.classList.add("hidden");
  }

  document.getElementById("pfOrderSummaryLbl").textContent =
    `${imp.side} ${imp.quantity} ${imp.symbol} @ $${imp.limit_price.toFixed(2)} ($${imp.order_value_usd.toLocaleString("en-US")})`;

  document.getElementById("pfWarningsList").innerHTML = imp.warnings.map((w) => `<li><strong>RIESGO:</strong> ${w}</li>`).join("");
  document.getElementById("pfPositivesList").innerHTML = imp.positives.map((p) => `<li class="ok-flag"><strong>OK:</strong> ${p}</li>`).join("");

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

  const beforeWeightMap = {};
  b.positions.forEach((p) => (beforeWeightMap[p.symbol] = p.weight_pct));

  document.getElementById("pfHoldingsTableWrap").innerHTML = `
    <table class="orders-table">
      <thead>
        <tr><th>Ticker</th><th>Sector</th><th>Acciones</th><th>Precio Ref.</th><th>Peso Actual</th><th>Nuevo Peso</th><th>Beta</th><th></th></tr>
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
                <td>${holdingObj ? `<input type="number" min="1" step="1" value="${holdingObj.shares}" class="m-num-input pf-sh-edit" data-sym="${pos.symbol}" />` : `<span>0 → <strong>${pos.shares}</strong></span>`}</td>
                <td>$${pos.price.toFixed(2)}</td>
                <td>${bw.toFixed(1)}%</td>
                <td><strong>${pos.weight_pct.toFixed(1)}%</strong></td>
                <td>${pos.beta.toFixed(2)}</td>
                <td>${holdingObj ? `<button class="ib-btn-outline-sm pf-del-btn" data-sym="${pos.symbol}">X</button>` : ""}</td>
              </tr>`;
          })
          .join("")}
      </tbody>
    </table>`;

  document.querySelectorAll(".pf-sh-edit").forEach((inp) => {
    inp.addEventListener("change", (e) => {
      const sym = e.target.dataset.sym;
      const h = state.portfolio.holdings.find((x) => x.symbol.toUpperCase() === sym);
      if (h) {
        h.shares = Math.max(1, parseFloat(e.target.value || "1"));
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

// ============================================================
// CONSOLA EJECUTIVA B2B (ROI BRÓKER & AUDITORÍA MiFID II)
// ============================================================
function bindExecutiveEvents() {
  ["roiAccounts", "roiVolMusd", "roiFeeBps"].forEach((id) => {
    document.getElementById(id).addEventListener("change", refreshBrokerROI);
  });
  ["roiProPct", "roiPremPct", "roiMarginPct"].forEach((id) => {
    const el = document.getElementById(id);
    el.addEventListener("input", () => {
      document.getElementById("lblRoiProPct").textContent = `${document.getElementById("roiProPct").value}%`;
      document.getElementById("lblRoiPremPct").textContent = `${parseFloat(document.getElementById("roiPremPct").value).toFixed(1)}%`;
      document.getElementById("lblRoiMarginPct").textContent = `${document.getElementById("roiMarginPct").value}%`;
    });
    el.addEventListener("change", refreshBrokerROI);
  });

  document.getElementById("btnExportAuditJson").addEventListener("click", () => {
    const blob = new Blob([JSON.stringify(state.lastAuditLog, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `shieldorder-mifid2-audit-${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  });
}

async function refreshBrokerROI() {
  try {
    const resp = await fetch("/api/v1/b2b/roi-estimate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        active_retail_accounts: parseInt(document.getElementById("roiAccounts").value || "250000", 10),
        monthly_limit_volume_musd: parseFloat(document.getElementById("roiVolMusd").value || "850"),
        avg_fee_bps: parseFloat(document.getElementById("roiFeeBps").value || "6.5"),
        pro_volume_mode_adoption_pct: parseFloat(document.getElementById("roiProPct").value || "68"),
        premium_tier_adoption_pct: parseFloat(document.getElementById("roiPremPct").value || "6.5"),
        margin_account_share_pct: parseFloat(document.getElementById("roiMarginPct").value || "22"),
      }),
    });
    if (!resp.ok) return;
    const data = await resp.json();
    const k = data.kpis;
    document.getElementById("kpiNetImpact").textContent = `+$${k.total_broker_net_impact_musd.toFixed(2)}M/año`;
    document.getElementById("kpiVolPreserved").textContent = `${k.overall_volume_preserved_pct}%`;
    document.getElementById("kpiFeeRecovered").textContent = `+$${k.fee_recovered_by_pro_modes_kusd.toFixed(0)}K comisiones recuperadas vs cancelación ciega`;
    document.getElementById("kpiClientSaved").textContent = `$${k.client_capital_protected_musd.toFixed(1)}M`;
    document.getElementById("kpiAccountsSaved").textContent = `~${k.accounts_retained_yearly.toLocaleString("en-US")} cuentas retenidas/año (+$${k.retained_ltv_revenue_musd}M LTV)`;
    document.getElementById("kpiArrAndMargin").textContent = `$${(k.premium_arr_musd + k.margin_bad_debt_avoided_kusd / 1000).toFixed(2)}M`;
    document.getElementById("kpiPremSubs").textContent = `${k.premium_subscribers.toLocaleString("en-US")} suscriptores ($${k.premium_arr_musd}M ARR) + $${k.margin_bad_debt_avoided_kusd}K margen`;
    document.getElementById("execThesisBox").textContent = data.executive_thesis;
  } catch (e) {}
}

async function loadAuditLog() {
  try {
    const resp = await fetch("/api/v1/b2b/audit-log");
    if (!resp.ok) return;
    const data = await resp.json();
    state.lastAuditLog = data.receipts || [];
    const wrap = document.getElementById("auditLogTableWrap");
    if (!state.lastAuditLog.length) {
      wrap.innerHTML = `<p class="empty-state">Ejecuta una simulación para registrar recibos MiFID II.</p>`;
      return;
    }
    wrap.innerHTML = `
      <table class="orders-table">
        <thead>
          <tr><th>Receipt ID</th><th>UTC</th><th>Activo</th><th>Modo</th><th>Decisión</th><th>Toxicidad</th><th>Vol. Preservado</th><th>SHA-256</th></tr>
        </thead>
        <tbody>
          ${state.lastAuditLog
            .map(
              (r) => `
            <tr>
              <td><code>${r.receipt_id}</code></td>
              <td>${r.timestamp_utc.slice(11, 19)}</td>
              <td><strong>${r.symbol}</strong> (${r.side})</td>
              <td>${r.protection_mode}</td>
              <td><span class="pill ${r.decision.includes("INVALIDATED") ? "pill-amber" : "pill-green"}">${r.decision}</span></td>
              <td>${r.toxicity_score}/100</td>
              <td><strong>${r.broker_volume_preserved_pct}%</strong></td>
              <td><code>${r.deterministic_sha256.slice(0, 14)}...</code></td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  } catch (e) {}
}
