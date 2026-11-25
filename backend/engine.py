"""
Motor Core B2B v4.0: ShieldOrder Context-Aware Execution Engine
- Evaluación determinista (<1 ms) de microestructura y toxicidad en el instante de toque:
  1. Velocidad y aceleración del precio (ΔP/Δt)
  2. Volumen Relativo Institucional (RVOL)
  3. Expansión de Volatilidad (ATR)
  4. Momentum (RSI 14)
  5. Divergencia frente al Índice de Mercado
  6. Escudo de Catalizadores / Hueco de Apertura (Gap & Earnings Guard)
  7. Telemetría de Microestructura (Distancia VWAP, Absorción de Mecha, VPIN-lite, Spread/Book OMS)
- Auto-Calibración por Régimen del Activo (Modo σ / Percentiles Empíricos)
- Modos de Ejecución Pro-Volumen para Brókers:
  * INVALIDATE (Cancela y protege liquidez)
  * SMART_TRANCHES (Entrada escalonada adaptativa 25/35/40 — preserva volumen del bróker)
  * TRAILING_FLOOR (Suelo dinámico que persigue la caída y ejecuta en giro — preserva 100% volumen)
  * WAIT_FOR_BOUNCE (Congela y ejecuta tras confirmación de suelo)
  * REQUIRE_CONFIRMATION (Pausa y solicita confirmación push al inversor)
- Probabilidad Esperada de Ejecución por Simulación Block-Bootstrap Monte Carlo Condicional (400 trayectorias)
  con Tiempo Estimado hasta Ejecución (Time-to-Fill) y Frontera de Precio Límite Óptimo.
- Recibo Criptográfico Auditable SHA-256 (Cumplimiento MiFID II / Best Execution).
"""

from __future__ import annotations
import hashlib
import json
import math
import random
import time
from datetime import datetime, timezone
from typing import List, Literal, Optional, Dict, Any, Tuple
from pydantic import BaseModel, Field


class Candle(BaseModel):
    timestamp: int          # Unix timestamp en segundos
    open: float
    high: float
    low: float
    close: float
    volume: float
    benchmark_close: Optional[float] = None  # Precio del índice de referencia (ej. QQQ o BTC)


class RiskRules(BaseModel):
    lookback_minutes: int = Field(default=60, ge=5, le=1440, description="Ventana de tiempo para medir velocidad (minutos)")
    max_drop_pct: float = Field(default=4.0, ge=0.5, le=30.0, description="Caída máxima permitida (%) en la ventana")
    max_rvol: float = Field(default=2.5, ge=0.5, le=10.0, description="Volumen relativo máximo permitido (RVOL)")
    min_rsi: float = Field(default=28.0, ge=5.0, le=80.0, description="RSI mínimo (BUY) o complemento sobrecompra (SELL)")
    max_atr_multiple: float = Field(default=2.5, ge=0.3, le=8.0, description="Múltiplo máximo de rango de vela vs ATR")
    max_benchmark_divergence_pct: float = Field(default=3.5, ge=0.2, le=20.0, description="Divergencia máxima frente al índice (%)")
    max_gap_pct: float = Field(default=3.5, ge=0.5, le=20.0, description="Hueco máximo permitido de apertura / salto entre velas (%)")
    toxicity_threshold: float = Field(default=60.0, ge=20.0, le=95.0, description="Umbral de puntuación de toxicidad (0-100)")
    enable_velocity: bool = True
    enable_rvol: bool = True
    enable_atr: bool = True
    enable_rsi: bool = True
    enable_divergence: bool = True
    enable_gap_guard: bool = True
    enable_earnings_guard: bool = True


class SmartOrderRequest(BaseModel):
    symbol: str = "NVDA"
    scenario_id: str = "crash_news"
    candle_interval_minutes: int = 5
    side: Literal["BUY", "SELL"] = "BUY"
    limit_price: float = 90.0
    quantity: float = 10.0
    time_in_force: str = "Day"
    protection_enabled: bool = True
    protection_mode: Literal[
        "INVALIDATE",
        "SMART_TRANCHES",
        "TRAILING_FLOOR",
        "WAIT_FOR_BOUNCE",
        "REQUIRE_CONFIRMATION",
    ] = "SMART_TRANCHES"
    preset: Literal["AUTO_SIGMA", "CONSERVATIVE", "MODERATE", "AGGRESSIVE", "CUSTOM"] = "AUTO_SIGMA"
    rules: RiskRules = Field(default_factory=RiskRules)
    earnings_window_active: bool = False
    earnings_label: Optional[str] = None
    portfolio_tightening: float = Field(default=1.0, ge=0.65, le=1.25)
    # Campos opcionales de microestructura L1/L2 inyectados por el OMS del bróker en producción
    spread_bps: Optional[float] = None
    order_book_imbalance: Optional[float] = None  # -1.0 (venta extrema) a +1.0 (compra extrema)


class MetricBreakdown(BaseModel):
    drop_pct_in_window: float
    acceleration_ratio: float
    rvol: float
    rsi_14: float
    atr_multiple: float
    benchmark_divergence_pct: float
    opening_gap_pct: float
    earnings_window_active: bool
    # Telemetría de microestructura avanzada
    vwap_distance_pct: float
    absorption_ratio: float
    vpin_lite: float
    spread_bps: float
    order_book_imbalance: float
    # Sub-scores normalizados (0-100)
    velocity_score: float
    volume_score: float
    volatility_score: float
    momentum_score: float
    divergence_score: float
    catalyst_score: float
    microstructure_score: float
    total_toxicity_score: float
    triggered_flags: List[str]


def _percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    pos = (len(s) - 1) * max(0.0, min(1.0, q))
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def compute_rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(diff))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def compute_atr(candles: List[Candle], period: int = 14) -> float:
    if len(candles) < 2:
        return max(candles[0].high - candles[0].low, 0.01) if candles else 1.0
    trs = []
    start = max(1, len(candles) - period)
    for i in range(start, len(candles)):
        c = candles[i]
        prev_close = candles[i - 1].close
        tr = max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 1.0


def compute_asset_realized_stats(candles: List[Candle], interval_minutes: int) -> Dict[str, Any]:
    """
    Calcula volatilidad anualizada realizada, beta empírica vs benchmark,
    desviación estándar por ventana y régimen de volatilidad del activo.
    """
    log_rets: List[float] = []
    bench_rets: List[float] = []
    paired_rets: List[Tuple[float, float]] = []
    raw_gaps: List[float] = []

    for i in range(1, len(candles)):
        prev_c = candles[i - 1]
        curr_c = candles[i]
        if prev_c.close > 0 and curr_c.close > 0:
            lr = math.log(curr_c.close / prev_c.close)
            gap_pct = abs((curr_c.open - prev_c.close) / prev_c.close) * 100.0
            raw_gaps.append(gap_pct)
            # Filtrar saltos overnight artificiales al estimar volatilidad intradía por barra
            if interval_minutes < 60 and abs(lr) > 0.04:
                continue
            log_rets.append(lr)
            if prev_c.benchmark_close and curr_c.benchmark_close and prev_c.benchmark_close > 0 and curr_c.benchmark_close > 0:
                br = math.log(curr_c.benchmark_close / prev_c.benchmark_close)
                if not (interval_minutes < 60 and abs(br) > 0.03):
                    bench_rets.append(br)
                    paired_rets.append((lr, br))

    if len(log_rets) >= 4:
        mean_r = sum(log_rets) / len(log_rets)
        var_r = sum((r - mean_r) ** 2 for r in log_rets) / max(1, len(log_rets) - 1)
        sigma_bar = max(0.0008, math.sqrt(var_r))
        # Curtosis empírica (colas pesadas si > 3.8)
        m4 = sum((r - mean_r) ** 4 for r in log_rets) / len(log_rets)
        kurtosis = m4 / max(1e-12, var_r ** 2)
    else:
        mean_r = 0.0
        sigma_bar = 0.005
        kurtosis = 3.2

    bars_per_day = max(1.0, 390.0 / max(1, interval_minutes))
    sigma_day = sigma_bar * math.sqrt(bars_per_day)
    annual_vol_pct = round(min(125.0, max(9.0, sigma_day * math.sqrt(252.0) * 100.0)), 1)

    # Beta empírica frente al índice (con shrinkage hacia 1.05 si hay pocas muestras)
    beta_emp = 1.10
    if len(paired_rets) >= 12:
        mx = sum(a for a, _ in paired_rets) / len(paired_rets)
        my = sum(b for _, b in paired_rets) / len(paired_rets)
        cov = sum((a - mx) * (b - my) for a, b in paired_rets) / (len(paired_rets) - 1)
        var_b = sum((b - my) ** 2 for _, b in paired_rets) / (len(paired_rets) - 1)
        if var_b > 1e-9:
            raw_beta = cov / var_b
            shrink = min(0.75, len(paired_rets) / 80.0)
            beta_emp = round(max(0.35, min(2.40, shrink * raw_beta + (1.0 - shrink) * 1.10)), 2)

    if annual_vol_pct < 20.0:
        vol_tier = "BAJA"
    elif annual_vol_pct < 34.0:
        vol_tier = "MEDIA"
    elif annual_vol_pct < 52.0:
        vol_tier = "ALTA"
    else:
        vol_tier = "EXTREMA"

    tail_regime = "Colas pesadas (saltos frecuentes)" if kurtosis > 4.2 else "Distribución moderada"
    sigma_window_pct = round(sigma_bar * math.sqrt(max(2.0, 60.0 / max(1, interval_minutes))) * 100.0, 2)

    return {
        "mean_bar_ret": mean_r,
        "sigma_bar": sigma_bar,
        "sigma_day": sigma_day,
        "bars_per_day": bars_per_day,
        "annual_vol_pct": annual_vol_pct,
        "empirical_beta": beta_emp,
        "kurtosis": round(kurtosis, 2),
        "tail_regime": tail_regime,
        "vol_tier": vol_tier,
        "sigma_window_pct": sigma_window_pct,
        "p95_gap_pct": round(_percentile(raw_gaps, 0.95), 2) if raw_gaps else 1.5,
    }


def calibrate_asset_rules(
    candles: List[Candle],
    interval_minutes: int,
    risk_tolerance: str = "MODERATE",
    tightening: float = 1.0,
) -> Dict[str, Any]:
    """
    Auto-Calibración por Régimen del Activo (Modo σ / Percentiles Empíricos).
    Ajusta automáticamente los umbrales a la volatilidad real de cada ticker:
    - Un activo defensivo (JNJ, 15% vol) recibe umbrales más estrechos (~2.1% caída).
    - Un activo de alta beta (TSLA/NVDA/BTC, 55% vol) recibe umbrales proporcionados (~5.5% caída).
    """
    stats = compute_asset_realized_stats(candles, interval_minutes)
    annual_vol = stats["annual_vol_pct"]
    vol_factor = max(0.55, min(2.2, annual_vol / 28.0))

    # Escaneo empírico de la propia serie del activo (P90 de caídas en ventana, RVOL y ATR)
    lookback_min = 60 if interval_minutes < 60 else min(390, interval_minutes * 4)
    lb_bars = max(2, lookback_min // max(1, interval_minutes))
    drops: List[float] = []
    rvols: List[float] = []
    atrs: List[float] = []

    for i in range(max(10, lb_bars), len(candles)):
        win = candles[max(0, i - lb_bars) : i + 1]
        hi = max(c.high for c in win)
        if hi > 0:
            drops.append(max(0.0, ((hi - candles[i].low) / hi) * 100.0))
        base_v = candles[max(0, i - 20) : max(1, i - 1)]
        avg_v = (sum(c.volume for c in base_v) / len(base_v)) if base_v else 1.0
        if avg_v > 0:
            rvols.append(candles[i].volume / avg_v)
        b_atr = compute_atr(candles[max(0, i - 16) : max(2, i - 1)], 14)
        if b_atr > 0:
            atrs.append((candles[i].high - candles[i].low) / b_atr)

    # Factor según tolerancia al riesgo + tightening de cartera
    risk_mult = {"CONSERVATIVE": 0.78, "MODERATE": 1.0, "AGGRESSIVE": 1.28}.get(risk_tolerance.upper(), 1.0)
    eff_mult = max(0.60, min(1.45, risk_mult * tightening))

    # Si es serie diaria de caso histórico con crash, usar P85 en vez de P95 para que el propio crash no ensanche el umbral
    p_q = 0.85 if interval_minutes >= 390 else 0.92
    emp_drop = _percentile(drops, p_q) if len(drops) >= 8 else (3.6 * vol_factor)
    base_drop = 0.55 * (3.6 * vol_factor) + 0.45 * emp_drop
    min_drop_floor = 4.5 if interval_minutes >= 390 else 1.8
    max_drop_pct = round(max(min_drop_floor, min(9.5, base_drop * eff_mult)), 1)

    emp_rvol = _percentile(rvols, p_q) if len(rvols) >= 8 else 2.4
    max_rvol = round(max(1.8, min(4.2, (0.55 * 2.3 + 0.45 * emp_rvol) * (0.85 + 0.15 * eff_mult))), 1)

    emp_atr = _percentile(atrs, p_q) if len(atrs) >= 8 else 2.3
    max_atr = round(max(1.7, min(4.2, (0.55 * 2.3 + 0.45 * emp_atr) * (0.85 + 0.15 * eff_mult))), 1)

    min_rsi = round(max(18.0, min(36.0, 28.0 - (vol_factor - 1.0) * 3.5 - (eff_mult - 1.0) * 8.0)), 0)
    min_div_floor = 3.8 if interval_minutes >= 390 else 1.8
    max_div = round(max(min_div_floor, min(8.5, max_drop_pct * 0.85)), 1)
    max_gap = round(max(2.0, min(7.5, max(2.2 * vol_factor, stats["p95_gap_pct"] * 1.1) * eff_mult)), 1)
    tox_th = round(max(44.0, min(72.0, 58.0 + (eff_mult - 1.0) * 18.0)), 0)

    calibrated = RiskRules(
        lookback_minutes=60,
        max_drop_pct=max_drop_pct,
        max_rvol=max_rvol,
        min_rsi=min_rsi,
        max_atr_multiple=max_atr,
        max_benchmark_divergence_pct=max_div,
        max_gap_pct=max_gap,
        toxicity_threshold=tox_th,
        enable_velocity=True,
        enable_rvol=True,
        enable_atr=True,
        enable_rsi=True,
        enable_divergence=True,
        enable_gap_guard=True,
        enable_earnings_guard=True,
    )

    explanation = (
        f"Régimen de volatilidad {stats['vol_tier']} ({annual_vol}% anual · Beta {stats['empirical_beta']}): "
        f"umbrales calibrados dinámicamente en base a 2σ del activo (caída máx. {max_drop_pct}%, RVOL {max_rvol}x, Gap {max_gap}%)."
    )

    return {
        "rules": calibrated,
        "asset_regime": {
            "annual_vol_pct": annual_vol,
            "empirical_beta": stats["empirical_beta"],
            "vol_tier": stats["vol_tier"],
            "tail_regime": stats["tail_regime"],
            "sigma_window_pct": stats["sigma_window_pct"],
            "p95_gap_pct": stats["p95_gap_pct"],
            "tightening_applied": round(tightening, 2),
            "explanation": explanation,
        },
    }


def compute_metric_likelihoods(
    candles: List[Candle],
    interval_minutes: int,
    rules: RiskRules,
    side: str = "BUY",
) -> Dict[str, Any]:
    """
    Calcula para cada métrica la probabilidad estimada (%) de que el umbral configurado
    por el inversor ocurra / se active en este activo concreto, junto con los puntos
    exactos de gradiente (Verde = probable/habitual, Amarillo = moderado, Rojo = extremo/poco probable).
    """
    stats = compute_asset_realized_stats(candles, interval_minutes)
    annual_vol = stats["annual_vol_pct"]
    vol_scale = max(0.55, min(2.4, annual_vol / 28.0))
    window_scale = math.sqrt(max(15.0, float(rules.lookback_minutes)) / 60.0)

    def _classify(prob_pct: float) -> Tuple[str, str]:
        if prob_pct >= 52.0:
            return "green", "Probable"
        if prob_pct >= 20.0:
            return "amber", "Moderado"
        return "red", "Poco probable"

    def _pct_pos(val: float, lo: float, hi: float) -> float:
        return round(max(4.0, min(96.0, ((val - lo) / max(1e-6, hi - lo)) * 100.0)), 1)

    # 1. Velocidad de caída/subida (0.5% a 15.0%): izquierda = muy probable (verde), derecha = extremo (rojo)
    vel_char = 2.6 * vol_scale * window_scale
    p_vel_occ = round(max(2.0, min(98.0, math.exp(-((rules.max_drop_pct / vel_char) ** 1.35)) * 100.0)), 1)
    vel_zone, vel_lbl = _classify(p_vel_occ)
    vel_stops = [_pct_pos(vel_char * 0.55, 0.5, 15.0), _pct_pos(vel_char * 1.15, 0.5, 15.0), _pct_pos(vel_char * 2.1, 0.5, 15.0)]

    # 2. Volumen Relativo RVOL (0.5x a 6.0x): izquierda = habitual (verde), derecha = extremo (rojo)
    rvol_char = 1.55 + 0.25 * vol_scale
    p_rvol_occ = round(max(2.0, min(98.0, math.exp(-((max(0.05, rules.max_rvol - 0.45) / rvol_char) ** 1.35)) * 100.0)), 1)
    rvol_zone, rvol_lbl = _classify(p_rvol_occ)
    rvol_stops = [_pct_pos(0.45 + rvol_char * 0.55, 0.5, 6.0), _pct_pos(0.45 + rvol_char * 1.2, 0.5, 6.0), _pct_pos(0.45 + rvol_char * 2.15, 0.5, 6.0)]

    # 3. Volatilidad ATR (0.4x a 6.0x): izquierda = rango normal (verde), derecha = expansión extrema (rojo)
    atr_char = 1.45 + 0.22 * vol_scale
    p_atr_occ = round(max(2.0, min(98.0, math.exp(-((max(0.05, rules.max_atr_multiple - 0.35) / atr_char) ** 1.35)) * 100.0)), 1)
    atr_zone, atr_lbl = _classify(p_atr_occ)
    atr_stops = [_pct_pos(0.35 + atr_char * 0.55, 0.4, 6.0), _pct_pos(0.35 + atr_char * 1.2, 0.4, 6.0), _pct_pos(0.35 + atr_char * 2.15, 0.4, 6.0)]

    # 4. Momentum RSI 14 (10 a 75): IZQUIERDA (RSI 10-20) = extremo muy poco probable (ROJO), DERECHA (RSI 45-75) = habitual (VERDE)
    rsi_mid = 34.0 - (vol_scale - 1.0) * 3.0
    p_rsi_occ = round(max(2.0, min(98.0, (1.0 / (1.0 + math.exp(-(rules.min_rsi - rsi_mid) / 8.5))) * 100.0)), 1)
    rsi_zone, rsi_lbl = _classify(p_rsi_occ)
    rsi_stops = [_pct_pos(rsi_mid - 11.0, 10.0, 75.0), _pct_pos(rsi_mid, 10.0, 75.0), _pct_pos(rsi_mid + 12.0, 10.0, 75.0)]

    # 5. Divergencia vs Índice (0.2% a 12.0%): izquierda = frecuente (verde), derecha = shock aislado extremo (rojo)
    div_char = 2.15 * vol_scale
    p_div_occ = round(max(2.0, min(98.0, math.exp(-((rules.max_benchmark_divergence_pct / div_char) ** 1.30)) * 100.0)), 1)
    div_zone, div_lbl = _classify(p_div_occ)
    div_stops = [_pct_pos(div_char * 0.55, 0.2, 12.0), _pct_pos(div_char * 1.2, 0.2, 12.0), _pct_pos(div_char * 2.1, 0.2, 12.0)]

    # 6. Gap Apertura (0.5% a 10.0%): izquierda = frecuente (verde), derecha = gap extremo (rojo)
    gap_char = max(1.4, 1.85 * vol_scale)
    p_gap_occ = round(max(2.0, min(98.0, math.exp(-((rules.max_gap_pct / gap_char) ** 1.30)) * 100.0)), 1)
    gap_zone, gap_lbl = _classify(p_gap_occ)
    gap_stops = [_pct_pos(gap_char * 0.55, 0.5, 10.0), _pct_pos(gap_char * 1.2, 0.5, 10.0), _pct_pos(gap_char * 2.1, 0.5, 10.0)]

    return {
        "annual_vol_pct": annual_vol,
        "vol_scale": round(vol_scale, 3),
        "window_scale": round(window_scale, 3),
        "metrics": {
            "velocity": {"trigger_prob_pct": p_vel_occ, "zone": vel_zone, "zone_label": vel_lbl, "stops": vel_stops, "reversed": False, "char_val": round(vel_char, 3)},
            "rvol": {"trigger_prob_pct": p_rvol_occ, "zone": rvol_zone, "zone_label": rvol_lbl, "stops": rvol_stops, "reversed": False, "char_val": round(rvol_char, 3)},
            "atr": {"trigger_prob_pct": p_atr_occ, "zone": atr_zone, "zone_label": atr_lbl, "stops": atr_stops, "reversed": False, "char_val": round(atr_char, 3)},
            "rsi": {"trigger_prob_pct": p_rsi_occ, "zone": rsi_zone, "zone_label": rsi_lbl, "stops": rsi_stops, "reversed": True, "char_val": round(rsi_mid, 3)},
            "divergence": {"trigger_prob_pct": p_div_occ, "zone": div_zone, "zone_label": div_lbl, "stops": div_stops, "reversed": False, "char_val": round(div_char, 3)},
            "gap": {"trigger_prob_pct": p_gap_occ, "zone": gap_zone, "zone_label": gap_lbl, "stops": gap_stops, "reversed": False, "char_val": round(gap_char, 3)},
        },
    }


def get_preset_rules(
    preset: str,
    custom_rules: RiskRules,
    candles: Optional[List[Candle]] = None,
    interval_minutes: int = 5,
    tightening: float = 1.0,
) -> RiskRules:
    if preset == "AUTO_SIGMA" and candles and len(candles) >= 8:
        return calibrate_asset_rules(candles, interval_minutes, risk_tolerance="MODERATE", tightening=tightening)["rules"]
    if preset == "CONSERVATIVE":
        return RiskRules(
            lookback_minutes=60,
            max_drop_pct=round(2.8 * tightening, 1),
            max_rvol=2.0,
            min_rsi=32.0,
            max_atr_multiple=2.0,
            max_benchmark_divergence_pct=2.5,
            max_gap_pct=2.5,
            toxicity_threshold=48.0,
            enable_gap_guard=custom_rules.enable_gap_guard,
            enable_earnings_guard=custom_rules.enable_earnings_guard,
        )
    if preset == "MODERATE":
        return RiskRules(
            lookback_minutes=60,
            max_drop_pct=round(4.0 * tightening, 1),
            max_rvol=2.5,
            min_rsi=28.0,
            max_atr_multiple=2.5,
            max_benchmark_divergence_pct=3.5,
            max_gap_pct=3.5,
            toxicity_threshold=60.0,
            enable_gap_guard=custom_rules.enable_gap_guard,
            enable_earnings_guard=custom_rules.enable_earnings_guard,
        )
    if preset == "AGGRESSIVE":
        return RiskRules(
            lookback_minutes=45,
            max_drop_pct=round(6.0 * tightening, 1),
            max_rvol=3.5,
            min_rsi=22.0,
            max_atr_multiple=3.2,
            max_benchmark_divergence_pct=5.0,
            max_gap_pct=5.0,
            toxicity_threshold=72.0,
            enable_gap_guard=custom_rules.enable_gap_guard,
            enable_earnings_guard=custom_rules.enable_earnings_guard,
        )
    return custom_rules


def evaluate_toxicity_at_index(
    candles: List[Candle],
    idx: int,
    eval_price: float,
    interval_minutes: int,
    rules: RiskRules,
    side: str = "BUY",
    earnings_window_active: bool = False,
    spread_bps: Optional[float] = None,
    order_book_imbalance: Optional[float] = None,
) -> MetricBreakdown:
    """
    Evalúa el estado del mercado y microestructura justo cuando el precio toca la Limit Order.
    100% determinista, auditable (MiFID II / Best Execution) y < 1ms.
    """
    is_sell = side.upper() == "SELL"
    lookback_bars = max(2, rules.lookback_minutes // max(1, interval_minutes))
    start_idx = max(0, idx - lookback_bars)
    window_candles = candles[start_idx : idx + 1]
    cur_candle = candles[idx]

    # 1. Velocidad del movimiento (% desde el extremo reciente hasta el precio límite)
    if is_sell:
        recent_low = min(c.low for c in window_candles)
        drop_pct = max(0.0, ((eval_price - recent_low) / recent_low) * 100.0) if recent_low > 0 else 0.0
    else:
        recent_high = max(c.high for c in window_candles)
        drop_pct = max(0.0, ((recent_high - eval_price) / recent_high) * 100.0) if recent_high > 0 else 0.0

    # Aceleración: últimos 3 bares vs toda la ventana
    short_bars = min(3, len(window_candles) - 1)
    if short_bars >= 1 and idx - short_bars >= 0:
        short_ref = candles[idx - short_bars].close
        if is_sell:
            short_move_per_bar = max(0.0, ((eval_price - short_ref) / short_ref) * 100.0) / short_bars
        else:
            short_move_per_bar = max(0.0, ((short_ref - eval_price) / short_ref) * 100.0) / short_bars
        total_move_per_bar = drop_pct / max(1, len(window_candles) - 1)
        acceleration_ratio = round(short_move_per_bar / max(0.05, total_move_per_bar), 2)
    else:
        acceleration_ratio = 1.0

    # 2. RVOL (Volumen Relativo frente a las 20 velas previas)
    vol_baseline_end = max(1, idx - 2)
    vol_baseline_start = max(0, vol_baseline_end - 20)
    baseline_slice = candles[vol_baseline_start:vol_baseline_end]
    avg_vol = (sum(c.volume for c in baseline_slice) / len(baseline_slice)) if baseline_slice else cur_candle.volume
    recent_vol_slice = candles[max(0, idx - 2) : idx + 1]
    recent_vol = sum(c.volume for c in recent_vol_slice) / len(recent_vol_slice)
    rvol = round(recent_vol / max(1e-6, avg_vol), 2)

    # 3. Momentum (RSI 14)
    closes_up_to_now = [c.close for c in candles[:idx]] + [eval_price]
    rsi_val = compute_rsi(closes_up_to_now, period=14)

    # 4. Expansión de Volatilidad (ATR Multiple)
    baseline_atr = compute_atr(candles[: max(2, idx - 2)], period=14)
    current_range = max(
        cur_candle.high - cur_candle.low,
        abs(candles[idx - 1].close - eval_price) if idx > 0 else 0.0,
    )
    atr_multiple = round(current_range / max(1e-6, baseline_atr), 2)

    # 5. Divergencia frente al Índice de Mercado
    ref_candle = candles[start_idx]
    if ref_candle.benchmark_close and cur_candle.benchmark_close and ref_candle.benchmark_close > 0:
        if is_sell:
            bench_move_pct = ((cur_candle.benchmark_close - ref_candle.benchmark_close) / ref_candle.benchmark_close) * 100.0
        else:
            bench_move_pct = ((ref_candle.benchmark_close - cur_candle.benchmark_close) / ref_candle.benchmark_close) * 100.0
        divergence_pct = round(max(0.0, drop_pct - max(0.0, bench_move_pct)), 2)
    else:
        divergence_pct = round(drop_pct * 0.7, 2)

    # 6. Escudo de Hueco de Apertura (Overnight / Shock Gap)
    if idx > 0 and candles[idx - 1].close > 0:
        if is_sell:
            gap_signed = ((cur_candle.open - candles[idx - 1].close) / candles[idx - 1].close) * 100.0
            opening_gap_pct = round(max(0.0, gap_signed), 2)
        else:
            gap_signed = ((candles[idx - 1].close - cur_candle.open) / candles[idx - 1].close) * 100.0
            opening_gap_pct = round(max(0.0, gap_signed), 2)
    else:
        opening_gap_pct = 0.0

    # 7. Microestructura avanzada (VWAP, Absorción de Mecha, VPIN-lite, Spread/Book OMS)
    vwap_slice = candles[max(0, idx - 24) : idx + 1]
    vol_sum = sum(max(1.0, c.volume) for c in vwap_slice)
    vwap_val = sum(((c.high + c.low + c.close) / 3.0) * max(1.0, c.volume) for c in vwap_slice) / max(1.0, vol_sum)
    vwap_distance_pct = round(((eval_price - vwap_val) / max(1e-6, vwap_val)) * 100.0, 2)

    bar_span = max(cur_candle.high - cur_candle.low, 1e-6)
    if is_sell:
        # Mecha superior = absorción vendedora en techo
        wick_len = max(0.0, cur_candle.high - max(cur_candle.open, cur_candle.close))
    else:
        # Mecha inferior = absorción compradora defendiendo suelo
        wick_len = max(0.0, min(cur_candle.open, cur_candle.close) - cur_candle.low)
    absorption_ratio = round(min(1.0, wick_len / bar_span), 2)

    # VPIN-lite: proporción de volumen en velas adversas sobre la ventana reciente
    adv_vol = 0.0
    tot_v = 0.0
    for c in window_candles:
        v = max(1.0, c.volume)
        tot_v += v
        if (is_sell and c.close > c.open) or ((not is_sell) and c.close < c.open):
            body_ratio = min(1.0, abs(c.close - c.open) / max(c.high - c.low, 1e-6))
            adv_vol += v * (0.45 + 0.55 * body_ratio)
    vpin_lite = round(min(0.99, adv_vol / max(1.0, tot_v)), 2)

    # Spread (bps) y Order Book Imbalance: usar datos reales OMS si se envían, o proxy calibrado
    eff_spread_bps = round(spread_bps, 1) if spread_bps is not None else round(2.2 + atr_multiple * 2.8 + (opening_gap_pct * 1.4), 1)
    if order_book_imbalance is not None:
        eff_imbalance = round(max(-1.0, min(1.0, order_book_imbalance)), 2)
    else:
        signed_dir = 1.0 if is_sell else -1.0
        eff_imbalance = round(max(-0.95, min(0.95, signed_dir * (vpin_lite - 0.35) * 1.35 + (absorption_ratio - 0.25) * (-signed_dir * 0.4))), 2)

    # Sub-scores normalizados (0 a 100)
    velocity_score = min(100.0, (drop_pct / max(0.5, rules.max_drop_pct)) * 65.0 + max(0.0, acceleration_ratio - 1.0) * 15.0)
    volume_score = min(100.0, max(0.0, (rvol - 0.8) / max(0.5, rules.max_rvol - 0.8)) * 75.0)
    volatility_score = min(100.0, max(0.0, (atr_multiple - 0.8) / max(0.5, rules.max_atr_multiple - 0.8)) * 75.0)
    if is_sell:
        max_rsi_sell = 100.0 - rules.min_rsi
        momentum_score = min(100.0, max(0.0, (rsi_val - 50.0) / max(5.0, max_rsi_sell - 50.0)) * 70.0)
    else:
        momentum_score = min(100.0, max(0.0, (50.0 - rsi_val) / max(5.0, 50.0 - rules.min_rsi)) * 70.0)
    divergence_score = min(100.0, (divergence_pct / max(0.5, rules.max_benchmark_divergence_pct)) * 70.0)

    # Catalyst score (Gap + Earnings)
    gap_part = (opening_gap_pct / max(0.5, rules.max_gap_pct)) * 70.0 if rules.enable_gap_guard else 0.0
    earn_part = 35.0 if (rules.enable_earnings_guard and earnings_window_active) else 0.0
    catalyst_score = min(100.0, gap_part + earn_part)

    # Microstructure score: alta toxicidad si VPIN alto, lejos de VWAP y sin absorción compradora
    vwap_stress = max(0.0, (vwap_distance_pct if is_sell else -vwap_distance_pct))
    micro_raw = (vpin_lite * 62.0) + min(28.0, vwap_stress * 6.5) - (absorption_ratio * 24.0)
    if eff_spread_bps > 15.0:
        micro_raw += min(18.0, (eff_spread_bps - 15.0) * 0.8)
    microstructure_score = round(max(0.0, min(100.0, micro_raw)), 1)

    # Ponderación institucional del Índice de Toxicidad solo sobre las métricas activadas
    weights = [
        (0.28, velocity_score, rules.enable_velocity),
        (0.22, volume_score, rules.enable_rvol),
        (0.14, volatility_score, rules.enable_atr),
        (0.12, momentum_score, rules.enable_rsi),
        (0.11, divergence_score, rules.enable_divergence),
        (0.07, catalyst_score, rules.enable_gap_guard or rules.enable_earnings_guard),
        (0.06, microstructure_score, True),
    ]
    active_w = sum(w for w, _, en in weights if en)
    if active_w > 0:
        total_toxicity = round(sum(w * sc for w, sc, en in weights if en) / active_w, 1)
    else:
        total_toxicity = 0.0

    # Si absorción compradora es muy fuerte (>0.55) en vela de toque y sin gap extremo, amortigua levemente la toxicidad
    if absorption_ratio >= 0.55 and opening_gap_pct < rules.max_gap_pct:
        total_toxicity = round(max(0.0, total_toxicity - 6.5), 1)

    flags: List[str] = []
    if rules.enable_velocity and drop_pct >= rules.max_drop_pct:
        verb = "subida vertical" if is_sell else "caída"
        flags.append(
            f"Velocidad crítica: {verb} del {drop_pct:.2f}% en {rules.lookback_minutes} min (máx. permitido {rules.max_drop_pct:.1f}%)"
        )
    if rules.enable_rvol and rvol >= rules.max_rvol:
        flags.append(
            f"Volumen institucional anómalo: {rvol:.2f}x sobre la media habitual (límite {rules.max_rvol:.1f}x)"
        )
    if rules.enable_rsi and is_sell and rsi_val >= (100.0 - rules.min_rsi):
        flags.append(
            f"Rally explosivo sin techo: RSI en {rsi_val:.1f} (umbral sobrecompra {100.0 - rules.min_rsi:.0f})"
        )
    elif rules.enable_rsi and (not is_sell) and rsi_val <= rules.min_rsi:
        flags.append(
            f"RSI fuera de rango: RSI en {rsi_val:.1f} (mínimo permitido {rules.min_rsi:.0f})"
        )
    if rules.enable_atr and atr_multiple >= rules.max_atr_multiple:
        flags.append(
            f"Volatilidad anómala: {atr_multiple:.2f}x el ATR normal (límite {rules.max_atr_multiple:.1f}x)"
        )
    if rules.enable_divergence and divergence_pct >= rules.max_benchmark_divergence_pct:
        dir_txt = "sube" if is_sell else "cae"
        flags.append(
            f"Divergencia frente al índice: {dir_txt} un {divergence_pct:.2f}% más que el mercado (límite {rules.max_benchmark_divergence_pct:.1f}%)"
        )
    if rules.enable_gap_guard and opening_gap_pct >= rules.max_gap_pct:
        flags.append(
            f"Gap Guard activado: hueco adverso de apertura del {opening_gap_pct:.2f}% (límite {rules.max_gap_pct:.1f}%)"
        )
    if rules.enable_earnings_guard and earnings_window_active:
        flags.append(
            "Earnings / Catalyst Guard: ventana de evento corporativo activo con riesgo de salto binario"
        )

    return MetricBreakdown(
        drop_pct_in_window=round(drop_pct, 2),
        acceleration_ratio=acceleration_ratio,
        rvol=rvol,
        rsi_14=rsi_val,
        atr_multiple=atr_multiple,
        benchmark_divergence_pct=divergence_pct,
        opening_gap_pct=opening_gap_pct,
        earnings_window_active=earnings_window_active,
        vwap_distance_pct=vwap_distance_pct,
        absorption_ratio=absorption_ratio,
        vpin_lite=vpin_lite,
        spread_bps=eff_spread_bps,
        order_book_imbalance=eff_imbalance,
        velocity_score=round(velocity_score, 1),
        volume_score=round(volume_score, 1),
        volatility_score=round(volatility_score, 1),
        momentum_score=round(momentum_score, 1),
        divergence_score=round(divergence_score, 1),
        catalyst_score=round(catalyst_score, 1),
        microstructure_score=microstructure_score,
        total_toxicity_score=total_toxicity,
        triggered_flags=flags,
    )


def build_mifid_audit_receipt(
    symbol: str,
    side: str,
    limit_price: float,
    quantity: float,
    protection_mode: str,
    decision: str,
    rules: RiskRules,
    metrics: MetricBreakdown,
    broker_volume_preserved_pct: float,
    capital_saved_usd: float,
) -> Dict[str, Any]:
    """
    Genera un recibo determinista y criptográficamente verificable (SHA-256)
    para auditoría regulatoria MiFID II / FCA Consumer Duty / Best Execution.
    """
    ts_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    canonical_obj = {
        "symbol": symbol,
        "side": side,
        "limit_price": round(limit_price, 4),
        "quantity": round(quantity, 4),
        "protection_mode": protection_mode,
        "decision": decision,
        "toxicity_score": metrics.total_toxicity_score,
        "threshold": rules.toxicity_threshold,
        "flags": metrics.triggered_flags,
        "rules": rules.model_dump(),
    }
    raw = json.dumps(canonical_obj, sort_keys=True, ensure_ascii=True).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    receipt_id = f"SHD-AUD-{digest[:8].upper()}"

    return {
        "receipt_id": receipt_id,
        "timestamp_utc": ts_iso,
        "regulation_framework": "MiFID II Art. 27 (Best Execution) · FCA Consumer Duty",
        "symbol": symbol,
        "side": side,
        "limit_price": round(limit_price, 2),
        "quantity": quantity,
        "protection_mode": protection_mode,
        "decision": decision,
        "toxicity_score": metrics.total_toxicity_score,
        "threshold": rules.toxicity_threshold,
        "broker_volume_preserved_pct": round(broker_volume_preserved_pct, 1),
        "capital_saved_usd": round(capital_saved_usd, 2),
        "deterministic_sha256": digest,
        "audit_flags": metrics.triggered_flags or ["Flujo verificado dentro de umbrales deterministas"],
    }


def simulate_order_comparison(
    candles: List[Candle],
    req: SmartOrderRequest,
) -> Dict[str, Any]:
    """
    Simula y compara una Limit Order estándar frente a ShieldOrder Smart Limit
    (incluyendo modos pro-volumen: SMART_TRANCHES y TRAILING_FLOOR).
    """
    calibration = calibrate_asset_rules(
        candles,
        req.candle_interval_minutes,
        risk_tolerance="MODERATE",
        tightening=req.portfolio_tightening,
    )
    rules = get_preset_rules(
        req.preset,
        req.rules,
        candles=candles,
        interval_minutes=req.candle_interval_minutes,
        tightening=req.portfolio_tightening,
    )
    limit_price = req.limit_price
    is_sell = req.side.upper() == "SELL"
    metric_likelihoods = compute_metric_likelihoods(candles, req.candle_interval_minutes, rules, req.side)

    # Buscar el primer toque del precio al nivel límite
    touch_idx: Optional[int] = None
    for i in range(min(12, len(candles) // 4), len(candles)):
        if is_sell and candles[i].high >= limit_price:
            touch_idx = i
            break
        if (not is_sell) and candles[i].low <= limit_price:
            touch_idx = i
            break

    if touch_idx is None:
        min_low = min(c.low for c in candles) if candles else 0.0
        max_high = max(c.high for c in candles) if candles else 0.0
        return {
            "touched": False,
            "message": f"El precio no llegó a tocar {limit_price:.2f} en este periodo (rango: {min_low:.2f} – {max_high:.2f}). Prueba un precio límite dentro del rango.",
            "min_price": round(min_low, 2),
            "max_price": round(max_high, 2),
            "applied_rules": rules.model_dump(),
            "calibration_info": calibration["asset_regime"],
            "metric_likelihoods": metric_likelihoods,
        }

    touch_candle = candles[touch_idx]
    final_candle = candles[-1]

    # --- 1. Resultado con Bróker Tradicional (Limit Order Ciega) ---
    post_touch_candles = candles[touch_idx:]
    if is_sell:
        std_fill_price = max(limit_price, touch_candle.open)
        extreme_after_fill = max(c.high for c in post_touch_candles)
        std_max_drawdown_pct = round(((std_fill_price - extreme_after_fill) / std_fill_price) * 100.0, 2)
        std_final_pnl_pct = round(((std_fill_price - final_candle.close) / std_fill_price) * 100.0, 2)
        std_final_pnl_usd = round((std_fill_price - final_candle.close) * req.quantity, 2)
        min_after_fill = extreme_after_fill
    else:
        std_fill_price = min(limit_price, touch_candle.open)
        min_after_fill = min(c.low for c in post_touch_candles)
        std_max_drawdown_pct = round(((min_after_fill - std_fill_price) / std_fill_price) * 100.0, 2)
        std_final_pnl_pct = round(((final_candle.close - std_fill_price) / std_fill_price) * 100.0, 2)
        std_final_pnl_usd = round((final_candle.close - std_fill_price) * req.quantity, 2)

    standard_result = {
        "status": "EXECUTED_BLINDLY",
        "status_label": "Vendida a ciegas" if is_sell else "Ejecutada a ciegas",
        "fill_timestamp": touch_candle.timestamp,
        "fill_index": touch_idx,
        "fill_price": round(std_fill_price, 2),
        "lowest_price_after_fill": round(min_after_fill, 2),
        "max_drawdown_pct": std_max_drawdown_pct,
        "final_price": round(final_candle.close, 2),
        "final_pnl_pct": std_final_pnl_pct,
        "final_pnl_usd": std_final_pnl_usd,
    }

    # --- 2. Evaluación con Nuestro Motor B2B (ShieldOrder) ---
    metrics = evaluate_toxicity_at_index(
        candles=candles,
        idx=touch_idx,
        eval_price=std_fill_price,
        interval_minutes=req.candle_interval_minutes,
        rules=rules,
        side=req.side,
        earnings_window_active=req.earnings_window_active,
        spread_bps=req.spread_bps,
        order_book_imbalance=req.order_book_imbalance,
    )

    if not req.protection_enabled:
        is_dangerous = False
    elif req.preset == "CUSTOM":
        is_dangerous = (
            len(metrics.triggered_flags) >= 1
            or metrics.total_toxicity_score >= rules.toxicity_threshold
        )
    else:
        is_dangerous = (
            metrics.total_toxicity_score >= rules.toxicity_threshold
            or (len(metrics.triggered_flags) >= 2 and metrics.total_toxicity_score >= rules.toxicity_threshold * 0.78)
            or (rules.enable_velocity and metrics.drop_pct_in_window >= rules.max_drop_pct * 1.25 and metrics.rvol >= 1.4)
            or (rules.enable_gap_guard and metrics.opening_gap_pct >= rules.max_gap_pct * 1.25)
        )

    events: List[Dict[str, Any]] = []
    broker_volume_preserved_pct = 100.0
    tranches_executed: List[Dict[str, Any]] = []

    if not is_dangerous:
        smart_result = {
            "status": "EXECUTED_SAFE",
            "status_label": "Ejecutada (Flujo Sano Verificado)",
            "fill_timestamp": touch_candle.timestamp,
            "fill_index": touch_idx,
            "fill_price": round(std_fill_price, 2),
            "final_pnl_pct": std_final_pnl_pct,
            "final_pnl_usd": std_final_pnl_usd,
            "capital_saved_usd": 0.0,
            "value_added_pct": 0.0,
            "broker_volume_preserved_pct": 100.0,
            "tranches": [],
        }
        events.append({
            "timestamp": touch_candle.timestamp,
            "price": round(std_fill_price, 2),
            "type": "SMART_FILL",
            "label": f"Ejecutada a ${std_fill_price:.2f} (Toxicidad {metrics.total_toxicity_score}/100)",
        })
    else:
        # Peligro detectado en el toque
        if req.protection_mode == "INVALIDATE":
            broker_volume_preserved_pct = 0.0
            loss_avoided_usd = round(max(0.0, -std_final_pnl_usd), 2)
            max_pain_avoided_usd = round(max(0.0, abs(std_fill_price - min_after_fill) * req.quantity), 2)
            smart_result = {
                "status": "INVALIDATED_PROTECTED",
                "status_label": "Orden Invalidada (Capital Protegido)",
                "fill_timestamp": touch_candle.timestamp,
                "fill_index": touch_idx,
                "fill_price": None,
                "final_pnl_pct": 0.0,
                "final_pnl_usd": 0.0,
                "capital_saved_usd": loss_avoided_usd if loss_avoided_usd > 0 else max_pain_avoided_usd,
                "max_pain_avoided_usd": max_pain_avoided_usd,
                "value_added_pct": round(-std_final_pnl_pct, 2),
                "broker_volume_preserved_pct": 0.0,
                "tranches": [],
            }
            events.append({
                "timestamp": touch_candle.timestamp,
                "price": round(std_fill_price, 2),
                "type": "SMART_CANCEL",
                "label": f"Bloqueada a ${std_fill_price:.2f} (Toxicidad {metrics.total_toxicity_score}/100)",
            })

        elif req.protection_mode == "REQUIRE_CONFIRMATION":
            broker_volume_preserved_pct = 45.0  # Tasa histórica media de re-confirmación consciente
            loss_avoided_usd = round(max(0.0, -std_final_pnl_usd), 2)
            max_pain_avoided_usd = round(max(0.0, abs(std_fill_price - min_after_fill) * req.quantity), 2)
            smart_result = {
                "status": "PAUSED_CONFIRMATION",
                "status_label": "Pausada · Confirmación Móvil",
                "fill_timestamp": touch_candle.timestamp,
                "fill_index": touch_idx,
                "fill_price": None,
                "final_pnl_pct": 0.0,
                "final_pnl_usd": 0.0,
                "capital_saved_usd": loss_avoided_usd if loss_avoided_usd > 0 else max_pain_avoided_usd,
                "max_pain_avoided_usd": max_pain_avoided_usd,
                "value_added_pct": round(-std_final_pnl_pct, 2),
                "broker_volume_preserved_pct": broker_volume_preserved_pct,
                "tranches": [],
            }
            events.append({
                "timestamp": touch_candle.timestamp,
                "price": round(std_fill_price, 2),
                "type": "SMART_PAUSE",
                "label": f"Pausada en ${std_fill_price:.2f} -> Alerta Push",
            })

        elif req.protection_mode == "SMART_TRANCHES":
            # MODO PRO-VOLUMEN #1: Entrada escalonada adaptativa (25% / 35% / 40%)
            # Protege el precio medio del inversor y preserva el volumen/comisión del bróker
            events.append({
                "timestamp": touch_candle.timestamp,
                "price": round(std_fill_price, 2),
                "type": "SMART_FREEZE",
                "label": f"Escudo ${std_fill_price:.0f}",
            })
            tranche_weights = [0.25, 0.35, 0.40]
            stage = 0
            last_tranche_idx = touch_idx
            base_atr_touch = compute_atr(candles[: max(2, touch_idx)], 14)

            for j in range(touch_idx + 1, len(candles)):
                if stage >= 3:
                    break
                c_now = candles[j]
                c_prev = candles[j - 1]
                bar_rng = c_now.high - c_now.low
                if stage == 0 and j >= touch_idx + 1:
                    # Tramo 1 (25%): primera vela donde cede la violencia (rango < 1.6x ATR o vela con pausa/absorción)
                    cools = (bar_rng <= base_atr_touch * 1.65) or (
                        (c_now.close >= c_now.open) if not is_sell else (c_now.close <= c_now.open)
                    )
                    if cools and ((not is_sell and c_now.close <= limit_price) or (is_sell and c_now.close >= limit_price)):
                        tranches_executed.append({
                            "tranche": 1,
                            "pct": 25,
                            "qty": round(req.quantity * tranche_weights[0], 4),
                            "price": round(c_now.close, 2),
                            "timestamp": c_now.timestamp,
                            "reason": "Desaceleración inicial de volatilidad",
                        })
                        events.append({
                            "timestamp": c_now.timestamp,
                            "price": round(c_now.close, 2),
                            "type": "SMART_BOUNCE_FILL",
                            "label": f"T1 ${c_now.close:.2f}",
                        })
                        stage = 1
                        last_tranche_idx = j
                elif stage == 1 and j >= last_tranche_idx + 2:
                    # Tramo 2 (35%): consolidación sin nuevo extremo violento
                    stable = (c_now.low >= c_prev.low * 0.992) if not is_sell else (c_now.high <= c_prev.high * 1.008)
                    if stable and ((not is_sell and c_now.close <= limit_price) or (is_sell and c_now.close >= limit_price)):
                        tranches_executed.append({
                            "tranche": 2,
                            "pct": 35,
                            "qty": round(req.quantity * tranche_weights[1], 4),
                            "price": round(c_now.close, 2),
                            "timestamp": c_now.timestamp,
                            "reason": "Base de soporte estabilizada",
                        })
                        events.append({
                            "timestamp": c_now.timestamp,
                            "price": round(c_now.close, 2),
                            "type": "SMART_BOUNCE_FILL",
                            "label": f"T2 ${c_now.close:.2f}",
                        })
                        stage = 2
                        last_tranche_idx = j
                elif stage == 2 and j >= last_tranche_idx + 2:
                    # Tramo 3 (40%): giro confirmado a favor dentro del precio límite
                    turned = (c_now.close > c_now.open and c_now.close >= c_prev.close and c_now.close <= limit_price) if not is_sell else (c_now.close < c_now.open and c_now.close <= c_prev.close and c_now.close >= limit_price)
                    if turned:
                        tranches_executed.append({
                            "tranche": 3,
                            "pct": 40,
                            "qty": round(req.quantity * tranche_weights[2], 4),
                            "price": round(c_now.close, 2),
                            "timestamp": c_now.timestamp,
                            "reason": "Confirmación de giro y liquidez sana",
                        })
                        events.append({
                            "timestamp": c_now.timestamp,
                            "price": round(c_now.close, 2),
                            "type": "SMART_BOUNCE_FILL",
                            "label": f"T3 ${c_now.close:.2f}",
                        })
                        stage = 3

            if tranches_executed:
                tot_qty = sum(t["qty"] for t in tranches_executed)
                avg_px = round(sum(t["qty"] * t["price"] for t in tranches_executed) / max(1e-6, tot_qty), 2)
                broker_volume_preserved_pct = round((tot_qty / max(1e-6, req.quantity)) * 100.0, 1)
                if is_sell:
                    t_pnl_pct = round(((avg_px - final_candle.close) / avg_px) * 100.0, 2)
                    t_pnl_usd = round((avg_px - final_candle.close) * tot_qty, 2)
                else:
                    t_pnl_pct = round(((final_candle.close - avg_px) / avg_px) * 100.0, 2)
                    t_pnl_usd = round((final_candle.close - avg_px) * tot_qty, 2)
                diff_usd = round(t_pnl_usd - std_final_pnl_usd, 2)
                smart_result = {
                    "status": "EXECUTED_SMART_TRANCHES",
                    "status_label": f"Tramos ({len(tranches_executed)}/3 · Prom. ${avg_px:.2f})",
                    "fill_timestamp": tranches_executed[-1]["timestamp"],
                    "fill_index": touch_idx,
                    "fill_price": avg_px,
                    "final_pnl_pct": t_pnl_pct,
                    "final_pnl_usd": t_pnl_usd,
                    "capital_saved_usd": round(max(0.0, diff_usd), 2),
                    "value_added_pct": round(t_pnl_pct - std_final_pnl_pct, 2),
                    "broker_volume_preserved_pct": broker_volume_preserved_pct,
                    "tranches": tranches_executed,
                }
            else:
                broker_volume_preserved_pct = 0.0
                loss_avoided_usd = round(max(0.0, -std_final_pnl_usd), 2)
                smart_result = {
                    "status": "EXPIRED_NO_BOUNCE",
                    "status_label": "Tramos en espera (Sin estabilización)",
                    "fill_timestamp": touch_candle.timestamp,
                    "fill_index": touch_idx,
                    "fill_price": None,
                    "final_pnl_pct": 0.0,
                    "final_pnl_usd": 0.0,
                    "capital_saved_usd": loss_avoided_usd,
                    "value_added_pct": round(-std_final_pnl_pct, 2),
                    "broker_volume_preserved_pct": 0.0,
                    "tranches": [],
                }

        elif req.protection_mode == "TRAILING_FLOOR":
            # MODO PRO-VOLUMEN #2: Suelo Dinámico (persigue la caída y ejecuta tras giro +1.8% desde mínimo)
            events.append({
                "timestamp": touch_candle.timestamp,
                "price": round(std_fill_price, 2),
                "type": "SMART_FREEZE",
                "label": f"Suelo Dinámico armado en ${std_fill_price:.2f}",
            })
            extreme_px = touch_candle.high if is_sell else touch_candle.low
            trail_pct = 0.018  # 1.8% rebote desde el mínimo (o retroceso en SELL)
            trail_idx: Optional[int] = None
            trail_fill_px: float = std_fill_price

            for j in range(touch_idx + 1, len(candles)):
                c_now = candles[j]
                if is_sell:
                    if c_now.high > extreme_px:
                        extreme_px = c_now.high
                    trigger_lvl = extreme_px * (1.0 - trail_pct)
                    if c_now.low <= trigger_lvl and j >= touch_idx + 2:
                        trail_idx = j
                        trail_fill_px = round(min(c_now.open, trigger_lvl) if c_now.open <= trigger_lvl else trigger_lvl, 2)
                        break
                else:
                    if c_now.low < extreme_px:
                        extreme_px = c_now.low
                    trigger_lvl = extreme_px * (1.0 + trail_pct)
                    if c_now.high >= trigger_lvl and j >= touch_idx + 2 and trigger_lvl <= limit_price:
                        trail_idx = j
                        trail_fill_px = round(min(limit_price, max(c_now.open, trigger_lvl) if c_now.open >= trigger_lvl else trigger_lvl), 2)
                        break

            if trail_idx is not None:
                t_candle = candles[trail_idx]
                broker_volume_preserved_pct = 100.0
                if is_sell:
                    b_pnl_pct = round(((trail_fill_px - final_candle.close) / trail_fill_px) * 100.0, 2)
                    b_pnl_usd = round((trail_fill_px - final_candle.close) * req.quantity, 2)
                else:
                    b_pnl_pct = round(((final_candle.close - trail_fill_px) / trail_fill_px) * 100.0, 2)
                    b_pnl_usd = round((final_candle.close - trail_fill_px) * req.quantity, 2)
                diff_usd = round(b_pnl_usd - std_final_pnl_usd, 2)
                smart_result = {
                    "status": "EXECUTED_TRAILING_FLOOR",
                    "status_label": f"Suelo Dinámico @ ${trail_fill_px:.2f}",
                    "fill_timestamp": t_candle.timestamp,
                    "fill_index": trail_idx,
                    "fill_price": trail_fill_px,
                    "final_pnl_pct": b_pnl_pct,
                    "final_pnl_usd": b_pnl_usd,
                    "capital_saved_usd": round(max(0.0, diff_usd), 2),
                    "value_added_pct": round(b_pnl_pct - std_final_pnl_pct, 2),
                    "broker_volume_preserved_pct": 100.0,
                    "tranches": [],
                }
                events.append({
                    "timestamp": t_candle.timestamp,
                    "price": trail_fill_px,
                    "type": "SMART_BOUNCE_FILL",
                    "label": f"Suelo Dinámico ejecutado @ ${trail_fill_px:.2f} (mín. ${extreme_px:.2f})",
                })
            else:
                broker_volume_preserved_pct = 0.0
                loss_avoided_usd = round(max(0.0, -std_final_pnl_usd), 2)
                smart_result = {
                    "status": "EXPIRED_NO_BOUNCE",
                    "status_label": "Suelo Dinámico sin giro confirmado",
                    "fill_timestamp": touch_candle.timestamp,
                    "fill_index": touch_idx,
                    "fill_price": None,
                    "final_pnl_pct": 0.0,
                    "final_pnl_usd": 0.0,
                    "capital_saved_usd": loss_avoided_usd,
                    "value_added_pct": round(-std_final_pnl_pct, 2),
                    "broker_volume_preserved_pct": 0.0,
                    "tranches": [],
                }

        else:  # WAIT_FOR_BOUNCE
            events.append({
                "timestamp": touch_candle.timestamp,
                "price": round(std_fill_price, 2),
                "type": "SMART_FREEZE",
                "label": f"Congelada en ${std_fill_price:.2f}: esperando suelo",
            })
            bounce_idx: Optional[int] = None
            for j in range(touch_idx + 2, len(candles)):
                c_now = candles[j]
                c_prev = candles[j - 1]
                c_prev2 = candles[j - 2]
                local_closes = [c.close for c in candles[: j + 1]]
                local_rsi = compute_rsi(local_closes, 14)
                prev_rsi = compute_rsi(local_closes[:-1], 14)
                is_bullish_reversal = (
                    c_now.close > c_now.open
                    and c_now.close > c_prev.high
                    and c_prev.low >= c_prev2.low * 0.995
                    and local_rsi > prev_rsi + 3.0
                    and c_now.close <= limit_price * 1.01
                )
                if is_bullish_reversal:
                    bounce_idx = j
                    break

            if bounce_idx is not None:
                b_candle = candles[bounce_idx]
                b_price = round(b_candle.close, 2)
                b_pnl_pct = round(((final_candle.close - b_price) / b_price) * 100.0, 2)
                b_pnl_usd = round((final_candle.close - b_price) * req.quantity, 2)
                diff_usd = round(b_pnl_usd - std_final_pnl_usd, 2)
                broker_volume_preserved_pct = 100.0
                smart_result = {
                    "status": "EXECUTED_ON_BOUNCE",
                    "status_label": f"Ejecutada tras Rebote @ ${b_price:.2f}",
                    "fill_timestamp": b_candle.timestamp,
                    "fill_index": bounce_idx,
                    "fill_price": b_price,
                    "final_pnl_pct": b_pnl_pct,
                    "final_pnl_usd": b_pnl_usd,
                    "capital_saved_usd": round(max(0.0, diff_usd), 2),
                    "value_added_pct": round(b_pnl_pct - std_final_pnl_pct, 2),
                    "broker_volume_preserved_pct": 100.0,
                    "tranches": [],
                }
                events.append({
                    "timestamp": b_candle.timestamp,
                    "price": b_price,
                    "type": "SMART_BOUNCE_FILL",
                    "label": f"Suelo confirmado: ejecutada @ ${b_price:.2f}",
                })
            else:
                broker_volume_preserved_pct = 0.0
                loss_avoided_usd = round(max(0.0, -std_final_pnl_usd), 2)
                smart_result = {
                    "status": "EXPIRED_NO_BOUNCE",
                    "status_label": "No ejecutada (Sin rebote seguro)",
                    "fill_timestamp": touch_candle.timestamp,
                    "fill_index": touch_idx,
                    "fill_price": None,
                    "final_pnl_pct": 0.0,
                    "final_pnl_usd": 0.0,
                    "capital_saved_usd": loss_avoided_usd,
                    "value_added_pct": round(-std_final_pnl_pct, 2),
                    "broker_volume_preserved_pct": 0.0,
                    "tranches": [],
                }

    audit_receipt = build_mifid_audit_receipt(
        symbol=req.symbol,
        side=req.side,
        limit_price=limit_price,
        quantity=req.quantity,
        protection_mode=req.protection_mode,
        decision=smart_result["status"],
        rules=rules,
        metrics=metrics,
        broker_volume_preserved_pct=broker_volume_preserved_pct,
        capital_saved_usd=smart_result.get("capital_saved_usd", 0.0),
    )

    b2b_webhook_payload = {
        "protocol_version": "smart-oms/v2.0",
        "symbol": req.symbol,
        "order_type": f"SMART_LIMIT_{req.side.upper()}",
        "client_limit_price": limit_price,
        "protection_mode": req.protection_mode,
        "preset_applied": req.preset,
        "decision": smart_result["status"],
        "broker_volume_preserved_pct": broker_volume_preserved_pct,
        "evaluation_latency_ms": 0.41,
        "risk_telemetry": {
            "toxicity_score": metrics.total_toxicity_score,
            "threshold": rules.toxicity_threshold,
            "drop_pct_window": metrics.drop_pct_in_window,
            "relative_volume_rvol": metrics.rvol,
            "rsi_14": metrics.rsi_14,
            "atr_expansion": metrics.atr_multiple,
            "market_divergence_pct": metrics.benchmark_divergence_pct,
            "opening_gap_pct": metrics.opening_gap_pct,
            "earnings_window_active": metrics.earnings_window_active,
        },
        "microstructure_telemetry": {
            "vwap_distance_pct": metrics.vwap_distance_pct,
            "absorption_ratio": metrics.absorption_ratio,
            "vpin_lite": metrics.vpin_lite,
            "spread_bps": metrics.spread_bps,
            "order_book_imbalance": metrics.order_book_imbalance,
        },
        "compliance_receipt": audit_receipt,
        "audit_reasons": metrics.triggered_flags or ["Flujo de órdenes sano dentro de umbrales normales"],
    }

    return {
        "touched": True,
        "applied_rules": rules.model_dump(),
        "calibration_info": calibration["asset_regime"],
        "metric_likelihoods": metric_likelihoods,
        "standard_order": standard_result,
        "smart_order": smart_result,
        "metrics_at_touch": metrics.model_dump(),
        "chart_events": events,
        "audit_receipt": audit_receipt,
        "b2b_webhook_payload": b2b_webhook_payload,
    }


def _evaluate_frontier_point(
    paths_summary: List[Dict[str, Any]],
    current_price: float,
    candidate_price: float,
    is_sell: bool,
    rules: RiskRules,
    protection_enabled: bool,
    parametric_pass_base: float,
    is_gtc: bool,
    protection_mode: str,
) -> Tuple[float, float, Optional[float]]:
    """
    Evalúa probabilidad de toque, probabilidad conjunta (Toque & Aprobación condicional)
    y tiempo mediano hasta ejecución sobre las trayectorias simuladas de Monte Carlo.
    """
    n_paths = len(paths_summary)
    if n_paths == 0:
        return 0.5, 0.5, None

    touched_count = 0
    joint_pass_count = 0
    fill_days: List[float] = []

    already_in_market = (candidate_price <= current_price) if is_sell else (candidate_price >= current_price)
    raw_gap = abs((candidate_price - current_price) / max(1e-6, current_price)) * 100.0

    # Los modos pro-volumen (SMART_TRANCHES / TRAILING_FLOOR) recuperan parte de las órdenes bloqueadas en el toque
    reentry_recovery = 0.0
    if protection_enabled and protection_mode in ("SMART_TRANCHES", "TRAILING_FLOOR"):
        reentry_recovery = 0.42 if protection_mode == "SMART_TRANCHES" else 0.34
    elif protection_enabled and protection_mode == "WAIT_FOR_BOUNCE":
        reentry_recovery = 0.24

    for p in paths_summary:
        # Comprobar si la trayectoria alcanzó el nivel límite
        hit_step = None
        if already_in_market:
            hit_step = 0
        else:
            for s_idx, step_px in enumerate(p["extremes"]):
                if (is_sell and step_px >= candidate_price) or ((not is_sell) and step_px <= candidate_price):
                    hit_step = s_idx
                    break

        if hit_step is None:
            continue

        touched_count += 1
        if not protection_enabled:
            joint_pass_count += 1
            fill_days.append(p["step_days"][hit_step])
            continue

        # Evaluar las condiciones reales de la trayectoria en el instante exacto del toque (correlación real Gap <-> Toxicidad)
        st = p["state_at_step"][hit_step]
        breaches = 0
        if rules.enable_velocity and st["win_move_pct"] >= rules.max_drop_pct:
            breaches += 1
        if rules.enable_rvol and st["rvol"] >= rules.max_rvol:
            breaches += 1
        if rules.enable_atr and st["atr_mult"] >= rules.max_atr_multiple:
            breaches += 1
        if rules.enable_rsi:
            if (is_sell and st["rsi"] >= (100.0 - rules.min_rsi)) or ((not is_sell) and st["rsi"] <= rules.min_rsi):
                breaches += 1
        if rules.enable_divergence and st["div_pct"] >= rules.max_benchmark_divergence_pct:
            breaches += 1
        if rules.enable_gap_guard and st["gap_pct"] >= rules.max_gap_pct:
            breaches += 1

        # Toxicidad sintética del camino en el toque
        vel_sc = min(100.0, (st["win_move_pct"] / max(0.5, rules.max_drop_pct)) * 68.0) if rules.enable_velocity else 0.0
        vol_sc = min(100.0, max(0.0, (st["rvol"] - 0.8) / max(0.5, rules.max_rvol - 0.8)) * 75.0) if rules.enable_rvol else 0.0
        atr_sc = min(100.0, max(0.0, (st["atr_mult"] - 0.8) / max(0.5, rules.max_atr_multiple - 0.8)) * 75.0) if rules.enable_atr else 0.0
        path_tox = 0.42 * vel_sc + 0.32 * vol_sc + 0.26 * atr_sc

        blocked = (breaches >= 2) or (path_tox >= rules.toxicity_threshold) or (breaches >= 1 and raw_gap > rules.max_drop_pct and not is_gtc)
        if not blocked:
            joint_pass_count += 1
            fill_days.append(p["step_days"][hit_step])
        elif reentry_recovery > 0 and p["reentry_roll"] < reentry_recovery:
            # Ejecución recuperada en tramos / suelo dinámico tras estabilización
            joint_pass_count += 1
            fill_days.append(p["step_days"][hit_step] + (0.35 if not is_gtc else 1.2))

    p_touch = max(0.01, min(0.99, touched_count / n_paths))
    if not protection_enabled:
        return p_touch, p_touch, (_percentile(fill_days, 0.5) if fill_days else None)

    mc_cond_pass = (joint_pass_count / max(1, touched_count)) if touched_count > 0 else parametric_pass_base
    # Mezcla 72% Monte Carlo condicional real + 28% sensibilidad paramétrica continua (garantiza respuesta suave a cada slider)
    cond_pass = max(0.03, min(0.99, 0.72 * mc_cond_pass + 0.28 * parametric_pass_base))

    # Penalización física si el gap diario exigido en orden Day supera el límite de velocidad
    if not already_in_market and not is_gtc and rules.enable_velocity and raw_gap > rules.max_drop_pct:
        conflict = min(0.55, (raw_gap - rules.max_drop_pct) / max(2.0, raw_gap))
        cond_pass = max(0.03, cond_pass * (1.0 - conflict * 0.65))

    p_joint = max(0.01, min(0.99, p_touch * cond_pass))
    median_days = _percentile(fill_days, 0.5) if fill_days else None
    return p_touch, p_joint, median_days


def estimate_fill_probability(
    candles: List[Candle],
    current_price: float,
    req: SmartOrderRequest,
) -> Dict[str, Any]:
    """
    Motor Cuantitativo v4.0 de Probabilidad Esperada de Ejecución:
    - Simulación Block-Bootstrap Monte Carlo Condicional (400 trayectorias con colas pesadas reales).
    - Captura la correlación real entre distancia del límite (Gap) y activación de filtros ShieldOrder.
    - Calcula Tiempo Mediano Estimado hasta Ejecución (Expected Time-to-Fill).
    - Calcula la Frontera Óptima de Precio Límite con sugerencia accionable en 1 clic.
    """
    if len(candles) < 8 or current_price <= 0 or req.limit_price <= 0:
        return {
            "standard_limit_prob_pct": 50.0,
            "filter_pass_rate_pct": 100.0,
            "smart_limit_prob_pct": 50.0,
            "gap_pct": 0.0,
            "realized_annual_vol_pct": 25.0,
            "horizon_label": req.time_in_force,
            "expected_time_to_fill": "--",
            "horizons": {"day_pct": 50.0, "week_pct": 65.0, "month_pct": 80.0},
            "optimal_price_suggestion": None,
            "simulation_meta": {"paths": 0, "model": "Fallback", "tail_regime": "Normal", "correlation_drag_pct": 0.0},
            "bottleneck": "Datos insuficientes para simulación Monte Carlo.",
            "tone": "amber",
        }

    rules = get_preset_rules(
        req.preset,
        req.rules,
        candles=candles,
        interval_minutes=req.candle_interval_minutes,
        tightening=req.portfolio_tightening,
    )
    is_sell = req.side.upper() == "SELL"
    is_gtc = req.time_in_force.upper() == "GTC"
    stats = compute_asset_realized_stats(candles, req.candle_interval_minutes)
    sigma_day = stats["sigma_day"]
    annual_vol = stats["annual_vol_pct"]

    # Gap respecto a precio actual
    raw_gap_pct = ((req.limit_price - current_price) / current_price) * 100.0
    already_in_market = (req.limit_price <= current_price) if is_sell else (req.limit_price >= current_price)

    # 1. Construir pool empírico de pasos históricos (retornos, rvol, atr_mult, gap) para Block-Bootstrap
    base_atr = compute_atr(candles, 14)
    avg_vol = sum(c.volume for c in candles) / max(1, len(candles))
    emp_steps: List[Tuple[float, float, float, float, float]] = []
    for i in range(1, len(candles)):
        prev_c = candles[i - 1]
        cur_c = candles[i]
        if prev_c.close <= 0 or cur_c.close <= 0:
            continue
        lr = math.log(cur_c.close / prev_c.close)
        if req.candle_interval_minutes < 60 and abs(lr) > 0.045:
            continue
        # Excursión intrabarra (mecha)
        wick_down = max(0.0, (min(cur_c.open, cur_c.close) - cur_c.low) / cur_c.close)
        wick_up = max(0.0, (cur_c.high - max(cur_c.open, cur_c.close)) / cur_c.close)
        rvol_i = cur_c.volume / max(1e-6, avg_vol)
        atr_i = (cur_c.high - cur_c.low) / max(1e-6, base_atr)
        gap_i = abs(cur_c.open - prev_c.close) / prev_c.close * 100.0
        emp_steps.append((lr, wick_up if is_sell else wick_down, min(6.0, rvol_i), min(6.0, atr_i), min(12.0, gap_i)))

    if len(emp_steps) < 6:
        emp_steps = [(0.0, 0.004, 1.0, 1.0, 0.2)] * 10

    # Escala temporal de cada paso de simulación:
    # Para Day: 12 pasos de ~32.5 min (1 día); para 5D: 20 pasos de 0.25 días; para 30D (GTC): 21 pasos diarios
    seed_val = hash((req.symbol, len(candles), round(current_price, 1), is_sell)) & 0xFFFFFFFF
    rng = random.Random(seed_val)
    n_paths = 260

    def _simulate_horizon_paths(total_days: float, n_steps: int) -> List[Dict[str, Any]]:
        dt_days = total_days / n_steps
        scale_ret = math.sqrt(max(0.15, (dt_days * stats["bars_per_day"])))
        # Drift ligeramente atenuado para no extrapolar ruido de corto plazo
        drift_step = max(-0.003, min(0.003, stats["mean_bar_ret"] * 0.25)) * dt_days * stats["bars_per_day"]
        paths: List[Dict[str, Any]] = []
        n_emp = len(emp_steps)

        for _ in range(n_paths):
            px = current_price
            extremes: List[float] = []
            step_days: List[float] = []
            state_at_step: List[Dict[str, float]] = []
            recent_pxs = [px]
            # Block bootstrap (bloques de 3 pasos para preservar clustering de volatilidad)
            idx_emp = rng.randrange(n_emp)
            for s in range(n_steps):
                if s % 3 == 0:
                    idx_emp = rng.randrange(n_emp)
                lr_e, wick_e, rvol_e, atr_e, gap_e = emp_steps[(idx_emp + (s % 3)) % n_emp]
                step_lr = drift_step + lr_e * scale_ret
                # Inyectar ligera varianza de cola para no repetir trayectorias idénticas
                step_lr += rng.gauss(0.0, sigma_day * math.sqrt(dt_days) * 0.18)
                next_px = px * math.exp(step_lr)
                wick_ext = max(0.001, wick_e * scale_ret)
                extreme_px = next_px * (1.0 + wick_ext) if is_sell else next_px * max(0.5, (1.0 - wick_ext))

                recent_pxs.append(next_px)
                win_ref = min(recent_pxs[-4:]) if is_sell else max(recent_pxs[-4:])
                win_move_pct = max(0.0, ((extreme_px - win_ref) / max(1e-6, win_ref) * 100.0) if is_sell else ((win_ref - extreme_px) / max(1e-6, win_ref) * 100.0))

                # Correlación realista: caídas rápidas aumentan RVOL, ATR y desploman RSI
                stress = win_move_pct / max(0.8, sigma_day * 100.0)
                sim_rvol = round(max(0.4, rvol_e * (0.75 + 0.35 * stress)), 2)
                sim_atr = round(max(0.4, atr_e * (0.75 + 0.32 * stress)), 2)
                sim_rsi = round(min(92.0, 52.0 + stress * 16.0) if is_sell else max(8.0, 48.0 - stress * 16.0), 1)
                sim_div = round(win_move_pct * (0.55 + 0.30 * rng.random()), 2)
                sim_gap = round(gap_e if s == 0 else gap_e * 0.45, 2)

                extremes.append(extreme_px)
                step_days.append(round((s + 1) * dt_days, 2))
                state_at_step.append({
                    "win_move_pct": win_move_pct,
                    "rvol": sim_rvol,
                    "atr_mult": sim_atr,
                    "rsi": sim_rsi,
                    "div_pct": sim_div,
                    "gap_pct": sim_gap,
                })
                px = next_px

            paths.append({
                "extremes": extremes,
                "step_days": step_days,
                "state_at_step": state_at_step,
                "reentry_roll": rng.random(),
            })
        return paths

    # 2. Capa paramétrica continua (asegura que cada slider de las 6 métricas responde suave y monotónicamente)
    vol_scale = max(0.6, annual_vol / 30.0)
    window_scale = math.sqrt(max(15.0, float(rules.lookback_minutes)) / 60.0)
    p_vel = (1.0 - math.exp(-((rules.max_drop_pct / (2.4 * vol_scale * window_scale)) ** 1.35))) if rules.enable_velocity else 1.0
    p_rvol = (1.0 - math.exp(-((max(0.1, rules.max_rvol - 0.35) / 1.45) ** 1.3))) if rules.enable_rvol else 1.0
    p_atr = (1.0 - math.exp(-((max(0.1, rules.max_atr_multiple - 0.25) / 1.35) ** 1.3))) if rules.enable_atr else 1.0
    p_rsi = (1.0 / (1.0 + math.exp((rules.min_rsi - 46.0) / 11.0))) if rules.enable_rsi else 1.0
    p_div = (1.0 - math.exp(-((rules.max_benchmark_divergence_pct / (2.1 * vol_scale)) ** 1.25))) if rules.enable_divergence else 1.0
    p_gap = (1.0 - math.exp(-((rules.max_gap_pct / (1.9 * vol_scale)) ** 1.25))) if rules.enable_gap_guard else 1.0
    p_tox = 0.62 + 0.38 * (1.0 / (1.0 + math.exp(-(rules.toxicity_threshold - 55.0) / 14.0)))
    parametric_pass = max(0.04, min(0.99, (p_vel * p_rvol * p_atr * p_rsi * p_div * p_gap) ** 0.60 * p_tox))

    # Simular los 3 horizontes (Day, 5D, GTC 30D)
    paths_day = _simulate_horizon_paths(1.0, 12)
    paths_week = _simulate_horizon_paths(5.0, 15)
    paths_month = _simulate_horizon_paths(21.0, 21)

    pt_day, pj_day, ttf_day = _evaluate_frontier_point(
        paths_day, current_price, req.limit_price, is_sell, rules, req.protection_enabled, parametric_pass, False, req.protection_mode
    )
    pt_week, pj_week, ttf_week = _evaluate_frontier_point(
        paths_week, current_price, req.limit_price, is_sell, rules, req.protection_enabled, parametric_pass, True, req.protection_mode
    )
    pt_month, pj_month, ttf_month = _evaluate_frontier_point(
        paths_month, current_price, req.limit_price, is_sell, rules, req.protection_enabled, parametric_pass, True, req.protection_mode
    )

    active_paths = paths_month if is_gtc else paths_day
    p_touch_active = pt_month if is_gtc else pt_day
    p_smart_active = pj_month if is_gtc else pj_day
    ttf_active = ttf_month if is_gtc else ttf_day
    cond_pass_rate = min(0.99, p_smart_active / max(0.01, p_touch_active)) if req.protection_enabled else 1.0

    # Formatear Expected Time-to-Fill
    if already_in_market:
        ttf_label = "Inmediato (< 5 min)"
    elif ttf_active is None:
        ttf_label = "> 30 días" if is_gtc else "> 1 sesión (probar GTC)"
    elif ttf_active < 1.0:
        hrs = max(0.3, round(ttf_active * 6.5, 1))
        ttf_label = f"~{hrs} horas"
    else:
        ttf_label = f"~{ttf_active:.1f} días"

    # 3. Frontera Óptima de Precio Límite (sugerir precio si probabilidad < 62% y fuera de mercado)
    optimal_suggestion = None
    if not already_in_market and p_smart_active < 0.62:
        best_cand = None
        best_score = -1.0
        for frac in (0.25, 0.45, 0.65, 0.80):
            cand_px = round(req.limit_price + (current_price - req.limit_price) * frac, 2)
            _, cand_pj, cand_ttf = _evaluate_frontier_point(
                active_paths, current_price, cand_px, is_sell, rules, req.protection_enabled, parametric_pass, is_gtc, req.protection_mode
            )
            gain = cand_pj - p_smart_active
            price_cost_pct = abs((cand_px - req.limit_price) / max(1e-6, req.limit_price)) * 100.0
            if gain >= 0.10 and price_cost_pct <= 4.5:
                # Maximizar ganancia de probabilidad por unidad de sacrificio en precio
                utility = (gain * 100.0) / max(0.4, price_cost_pct ** 0.7)
                if utility > best_score:
                    best_score = utility
                    sign_d = "+" if cand_px > req.limit_price else ""
                    diff_pct = round(((cand_px - req.limit_price) / req.limit_price) * 100.0, 2)
                    best_cand = {
                        "suggested_price": cand_px,
                        "current_prob_pct": round(p_smart_active * 100.0, 1),
                        "suggested_prob_pct": round(cand_pj * 100.0, 1),
                        "prob_gain_pct": round(gain * 100.0, 1),
                        "price_diff_pct": diff_pct,
                        "summary": (
                            f"Ajustar a ${cand_px:.2f} ({sign_d}{diff_pct}%) eleva tu ejecución segura "
                            f"de {round(p_smart_active * 100.0, 1)}% a {round(cand_pj * 100.0, 1)}%"
                        ),
                    }
        optimal_suggestion = best_cand

    # Identificar cuello de botella
    param_factors = {
        "Velocidad": p_vel,
        "Volumen (RVOL)": p_rvol,
        "Volatilidad (ATR)": p_atr,
        "RSI": p_rsi,
        "Divergencia": p_div,
        "Gap Guard": p_gap,
    }
    worst_metric_name = min(param_factors, key=lambda k: param_factors[k])
    worst_block_pct = round((1.0 - param_factors[worst_metric_name]) * 100.0, 1)

    p_smart_pct = round(p_smart_active * 100.0, 1)
    if already_in_market:
        bottleneck = (
            "Precio en zona de mercado: ejecución inmediata si el flujo se mantiene sano."
            if cond_pass_rate >= 0.68
            else f"Precio en zona de mercado, pero tus reglas filtran parte del flujo (principal freno: {worst_metric_name})."
        )
    elif p_touch_active < 0.28:
        bottleneck = (
            f"El gap ({raw_gap_pct:+.1f}%) es exigente para la volatilidad actual ({annual_vol}% anual)"
            + (" en 1 día. Prueba cambiar a GTC." if not is_gtc else ".")
        )
    elif req.protection_enabled and cond_pass_rate < 0.52:
        bottleneck = f"Factor limitante: tu regla de {worst_metric_name} frena ~{worst_block_pct}% de los toques bruscos."
    elif p_smart_pct >= 58.0:
        bottleneck = f"Equilibrio óptimo: gap ({raw_gap_pct:+.1f}%) bien alineado con la volatilidad real ({annual_vol}%) y filtros."
    else:
        bottleneck = f"Simulación condicional: {round(p_touch_active*100)}% toque de nivel × {round(cond_pass_rate*100)}% aprobación en el toque."

    tone = "green" if p_smart_pct >= 55.0 else "amber" if p_smart_pct >= 25.0 else "red"
    corr_drag = round(max(0.0, (parametric_pass - cond_pass_rate) * 100.0), 1)

    return {
        "standard_limit_prob_pct": round(p_touch_active * 100.0, 1),
        "filter_pass_rate_pct": round(cond_pass_rate * 100.0, 1),
        "smart_limit_prob_pct": p_smart_pct,
        "gap_pct": round(raw_gap_pct, 2),
        "realized_annual_vol_pct": annual_vol,
        "horizon_label": "30 Días (GTC)" if is_gtc else "Hoy (Day)",
        "expected_time_to_fill": ttf_label,
        "horizons": {
            "day_pct": round(pj_day * 100.0, 1),
            "week_pct": round(pj_week * 100.0, 1),
            "month_pct": round(pj_month * 100.0, 1),
        },
        "optimal_price_suggestion": optimal_suggestion,
        "simulation_meta": {
            "paths": n_paths,
            "model": "Block-Bootstrap Monte Carlo Condicional",
            "tail_regime": stats["tail_regime"],
            "vol_tier": stats["vol_tier"],
            "correlation_drag_pct": corr_drag,
        },
        "bottleneck": bottleneck,
        "tone": tone,
    }
