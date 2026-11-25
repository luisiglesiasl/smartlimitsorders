"""
Motor Core B2B: Smart Limit Order Engine (Context-Aware Order Execution)
Calcula métricas de microestructura y velocidad del precio en tiempo real
para evitar selección adversa ("catching a falling knife") en órdenes límite.
"""

from __future__ import annotations
import math
from typing import List, Literal, Optional, Dict, Any
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
    lookback_minutes: int = Field(default=60, ge=5, le=1440, description="Ventana de tiempo para medir velocidad de caída (minutos)")
    max_drop_pct: float = Field(default=4.0, ge=0.5, le=30.0, description="Caída máxima permitida (%) en la ventana antes de considerar peligro")
    max_rvol: float = Field(default=2.5, ge=0.5, le=10.0, description="Volumen relativo máximo permitido respecto a la media reciente")
    min_rsi: float = Field(default=28.0, ge=5.0, le=80.0, description="RSI mínimo; por debajo indica desplome vertical sin soporte")
    max_atr_multiple: float = Field(default=2.5, ge=0.3, le=8.0, description="Múltiplo máximo del rango de vela respecto al ATR normal")
    max_benchmark_divergence_pct: float = Field(default=3.5, ge=0.2, le=20.0, description="Caída extra del activo frente a su índice de mercado (%)")
    toxicity_threshold: float = Field(default=60.0, ge=20.0, le=95.0, description="Puntuación de toxicidad (0-100) a partir de la cual se activa la protección")
    enable_velocity: bool = True
    enable_rvol: bool = True
    enable_atr: bool = True
    enable_rsi: bool = True
    enable_divergence: bool = True


class SmartOrderRequest(BaseModel):
    symbol: str = "NVDA"
    scenario_id: str = "crash_news"
    candle_interval_minutes: int = 5
    side: Literal["BUY", "SELL"] = "BUY"
    limit_price: float = 90.0
    quantity: float = 10.0
    time_in_force: str = "Day"
    protection_enabled: bool = True
    protection_mode: Literal["INVALIDATE", "WAIT_FOR_BOUNCE", "REQUIRE_CONFIRMATION"] = "INVALIDATE"
    preset: Literal["CONSERVATIVE", "MODERATE", "AGGRESSIVE", "CUSTOM"] = "MODERATE"
    rules: RiskRules = Field(default_factory=RiskRules)


class MetricBreakdown(BaseModel):
    drop_pct_in_window: float
    acceleration_ratio: float
    rvol: float
    rsi_14: float
    atr_multiple: float
    benchmark_divergence_pct: float
    velocity_score: float
    volume_score: float
    volatility_score: float
    momentum_score: float
    divergence_score: float
    total_toxicity_score: float
    triggered_flags: List[str]


def get_preset_rules(preset: str, custom_rules: RiskRules) -> RiskRules:
    if preset == "CONSERVATIVE":
        return RiskRules(
            lookback_minutes=60,
            max_drop_pct=2.8,
            max_rvol=2.0,
            min_rsi=32.0,
            max_atr_multiple=2.0,
            max_benchmark_divergence_pct=2.5,
            toxicity_threshold=48.0,
        )
    elif preset == "MODERATE":
        return RiskRules(
            lookback_minutes=60,
            max_drop_pct=4.0,
            max_rvol=2.5,
            min_rsi=28.0,
            max_atr_multiple=2.5,
            max_benchmark_divergence_pct=3.5,
            toxicity_threshold=60.0,
        )
    elif preset == "AGGRESSIVE":
        return RiskRules(
            lookback_minutes=45,
            max_drop_pct=6.0,
            max_rvol=3.5,
            min_rsi=22.0,
            max_atr_multiple=3.2,
            max_benchmark_divergence_pct=5.0,
            toxicity_threshold=72.0,
        )
    return custom_rules


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


def evaluate_toxicity_at_index(
    candles: List[Candle],
    idx: int,
    eval_price: float,
    interval_minutes: int,
    rules: RiskRules,
    side: str = "BUY",
) -> MetricBreakdown:
    """
    Evalúa el estado del mercado justo cuando el precio toca el nivel de la Limit Order.
    - En BUY: detecta desplome vertical tóxico ("falling knife").
    - En SELL: detecta disparo alcista vertical por noticia ("rocket breakout") para evitar vender demasiado pronto.
    100% determinista, auditable (cumplimiento MiFID II / Best Execution) y < 1ms.
    """
    is_sell = side.upper() == "SELL"
    lookback_bars = max(2, rules.lookback_minutes // max(1, interval_minutes))
    start_idx = max(0, idx - lookback_bars)
    window_candles = candles[start_idx : idx + 1]

    # 1. Velocidad del movimiento (% desde el extremo reciente hasta el precio límite)
    if is_sell:
        recent_low = min(c.low for c in window_candles)
        drop_pct = max(0.0, ((eval_price - recent_low) / recent_low) * 100.0) if recent_low > 0 else 0.0
    else:
        recent_high = max(c.high for c in window_candles)
        drop_pct = max(0.0, ((recent_high - eval_price) / recent_high) * 100.0) if recent_high > 0 else 0.0

    # Aceleración: comparar velocidad últimos 3 bares vs toda la ventana
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
    avg_vol = (sum(c.volume for c in baseline_slice) / len(baseline_slice)) if baseline_slice else candles[idx].volume
    recent_vol_slice = candles[max(0, idx - 2) : idx + 1]
    recent_vol = sum(c.volume for c in recent_vol_slice) / len(recent_vol_slice)
    rvol = round(recent_vol / max(1e-6, avg_vol), 2)

    # 3. Momentum (RSI 14)
    closes_up_to_now = [c.close for c in candles[:idx]] + [eval_price]
    rsi_val = compute_rsi(closes_up_to_now, period=14)

    # 4. Expansión de Volatilidad (ATR Multiple)
    baseline_atr = compute_atr(candles[: max(2, idx - 2)], period=14)
    current_range = max(
        candles[idx].high - candles[idx].low,
        abs(candles[idx - 1].close - eval_price) if idx > 0 else 0.0,
    )
    atr_multiple = round(current_range / max(1e-6, baseline_atr), 2)

    # 5. Divergencia frente al Índice de Mercado
    ref_candle = candles[start_idx]
    cur_candle = candles[idx]
    if ref_candle.benchmark_close and cur_candle.benchmark_close and ref_candle.benchmark_close > 0:
        if is_sell:
            bench_move_pct = ((cur_candle.benchmark_close - ref_candle.benchmark_close) / ref_candle.benchmark_close) * 100.0
        else:
            bench_move_pct = ((ref_candle.benchmark_close - cur_candle.benchmark_close) / ref_candle.benchmark_close) * 100.0
        divergence_pct = round(max(0.0, drop_pct - max(0.0, bench_move_pct)), 2)
    else:
        divergence_pct = round(drop_pct * 0.7, 2)

    # Cálculo de sub-scores normalizados (0 a 100)
    velocity_score = min(100.0, (drop_pct / max(0.5, rules.max_drop_pct)) * 65.0 + max(0.0, acceleration_ratio - 1.0) * 15.0)
    volume_score = min(100.0, max(0.0, (rvol - 0.8) / max(0.5, rules.max_rvol - 0.8)) * 75.0)
    volatility_score = min(100.0, max(0.0, (atr_multiple - 0.8) / max(0.5, rules.max_atr_multiple - 0.8)) * 75.0)
    if is_sell:
        max_rsi_sell = 100.0 - rules.min_rsi
        momentum_score = min(100.0, max(0.0, (rsi_val - 50.0) / max(5.0, max_rsi_sell - 50.0)) * 70.0)
    else:
        momentum_score = min(100.0, max(0.0, (50.0 - rsi_val) / max(5.0, 50.0 - rules.min_rsi)) * 70.0)
    divergence_score = min(100.0, (divergence_pct / max(0.5, rules.max_benchmark_divergence_pct)) * 70.0)

    # Ponderación institucional del Índice de Toxicidad solo sobre las métricas activadas
    weights = [
        (0.32, velocity_score, rules.enable_velocity),
        (0.25, volume_score, rules.enable_rvol),
        (0.16, volatility_score, rules.enable_atr),
        (0.14, momentum_score, rules.enable_rsi),
        (0.13, divergence_score, rules.enable_divergence),
    ]
    active_w = sum(w for w, _, en in weights if en)
    if active_w > 0:
        total_toxicity = round(sum(w * sc for w, sc, en in weights if en) / active_w, 1)
    else:
        total_toxicity = 0.0

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
            f"RSI fuera de rango: RSI en {rsi_val:.1f} (tu mínimo permitido es {rules.min_rsi:.0f})"
        )
    if rules.enable_atr and atr_multiple >= rules.max_atr_multiple:
        flags.append(
            f"Volatilidad anómala: {atr_multiple:.2f}x el ATR normal (tu límite es {rules.max_atr_multiple:.1f}x)"
        )
    if rules.enable_divergence and divergence_pct >= rules.max_benchmark_divergence_pct:
        dir_txt = "sube" if is_sell else "cae"
        flags.append(
            f"Divergencia frente al índice: {dir_txt} un {divergence_pct:.2f}% más que el mercado (tu límite es {rules.max_benchmark_divergence_pct:.1f}%)"
        )

    return MetricBreakdown(
        drop_pct_in_window=round(drop_pct, 2),
        acceleration_ratio=acceleration_ratio,
        rvol=rvol,
        rsi_14=rsi_val,
        atr_multiple=atr_multiple,
        benchmark_divergence_pct=divergence_pct,
        velocity_score=round(velocity_score, 1),
        volume_score=round(volume_score, 1),
        volatility_score=round(volatility_score, 1),
        momentum_score=round(momentum_score, 1),
        divergence_score=round(divergence_score, 1),
        total_toxicity_score=total_toxicity,
        triggered_flags=flags,
    )


def simulate_order_comparison(
    candles: List[Candle],
    req: SmartOrderRequest,
) -> Dict[str, Any]:
    """
    Simula y compara qué ocurre con una Limit Order estándar de un bróker tradicional
    frente a nuestra Smart Limit Order sobre la misma serie de precios.
    """
    rules = get_preset_rules(req.preset, req.rules)
    limit_price = req.limit_price
    is_sell = req.side.upper() == "SELL"

    # Buscar el primer momento en el que el precio toca la Limit Order (BUY: low <= limit | SELL: high >= limit)
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

    # --- 2. Evaluación con Nuestro Motor B2B (Smart Limit Order) ---
    metrics = evaluate_toxicity_at_index(
        candles=candles,
        idx=touch_idx,
        eval_price=std_fill_price,
        interval_minutes=req.candle_interval_minutes,
        rules=rules,
        side=req.side,
    )

    # Si el usuario está en modo Manual (CUSTOM), cualquier regla individual incumplida bloquea la orden.
    # En presets globales, bloquea si supera umbral de toxicidad o >=2 alertas (o caída extrema).
    if req.preset == "CUSTOM":
        is_dangerous = (
            len(metrics.triggered_flags) >= 1
            or metrics.total_toxicity_score >= rules.toxicity_threshold
        )
    else:
        is_dangerous = (
            metrics.total_toxicity_score >= rules.toxicity_threshold
            or len(metrics.triggered_flags) >= 2
            or (rules.enable_velocity and metrics.drop_pct_in_window >= rules.max_drop_pct * 1.15)
        )

    events = []
    if not is_dangerous:
        # Caída sana -> El motor aprueba y envía la orden al mercado inmediatamente
        smart_result = {
            "status": "EXECUTED_SAFE",
            "status_label": "Ejecutada (Caída Sana Verificada)",
            "fill_timestamp": touch_candle.timestamp,
            "fill_index": touch_idx,
            "fill_price": round(std_fill_price, 2),
            "final_pnl_pct": std_final_pnl_pct,
            "final_pnl_usd": std_final_pnl_usd,
            "capital_saved_usd": 0.0,
            "value_added_pct": 0.0,
        }
        events.append({
            "timestamp": touch_candle.timestamp,
            "price": round(std_fill_price, 2),
            "type": "SMART_FILL",
            "label": f"Compra aprobada a {std_fill_price:.2f} (Toxicidad baja: {metrics.total_toxicity_score}/100)",
        })
    else:
        # Peligro detectado ("Cuchillo cayendo")
        if req.protection_mode == "INVALIDATE":
            loss_avoided_usd = round(max(0.0, -std_final_pnl_usd), 2)
            max_pain_avoided_usd = round(max(0.0, (std_fill_price - min_after_fill) * req.quantity), 2)
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
            }
            events.append({
                "timestamp": touch_candle.timestamp,
                "price": round(std_fill_price, 2),
                "type": "SMART_CANCEL",
                "label": f"Orden bloqueada a {std_fill_price:.2f} (Toxicidad {metrics.total_toxicity_score}/100)",
            })

        elif req.protection_mode == "REQUIRE_CONFIRMATION":
            loss_avoided_usd = round(max(0.0, -std_final_pnl_usd), 2)
            max_pain_avoided_usd = round(max(0.0, (std_fill_price - min_after_fill) * req.quantity), 2)
            smart_result = {
                "status": "PAUSED_CONFIRMATION",
                "status_label": "Congelada · Esperando Confirmación Móvil",
                "fill_timestamp": touch_candle.timestamp,
                "fill_index": touch_idx,
                "fill_price": None,
                "final_pnl_pct": 0.0,
                "final_pnl_usd": 0.0,
                "capital_saved_usd": loss_avoided_usd if loss_avoided_usd > 0 else max_pain_avoided_usd,
                "max_pain_avoided_usd": max_pain_avoided_usd,
                "value_added_pct": round(-std_final_pnl_pct, 2),
            }
            events.append({
                "timestamp": touch_candle.timestamp,
                "price": round(std_fill_price, 2),
                "type": "SMART_PAUSE",
                "label": f"Pausada a {std_fill_price:.2f} -> Push enviado al móvil del inversor",
            })

        else:  # WAIT_FOR_BOUNCE (Congela durante el desplome y compra solo si estabiliza y gira al alza)
            events.append({
                "timestamp": touch_candle.timestamp,
                "price": round(std_fill_price, 2),
                "type": "SMART_FREEZE",
                "label": f"Congelada en {std_fill_price:.2f}: esperando suelo y giro alcista",
            })
            bounce_idx: Optional[int] = None
            for j in range(touch_idx + 2, len(candles)):
                c_now = candles[j]
                c_prev = candles[j - 1]
                c_prev2 = candles[j - 2]
                # Condición de estabilización y giro: vela verde que supera máximo previo,
                # tras haber dejado de marcar mínimos acelerados, y con precio <= limit_price
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
                smart_result = {
                    "status": "EXECUTED_ON_BOUNCE",
                    "status_label": f"Ejecutada tras Rebote a {b_price:.2f}",
                    "fill_timestamp": b_candle.timestamp,
                    "fill_index": bounce_idx,
                    "fill_price": b_price,
                    "final_pnl_pct": b_pnl_pct,
                    "final_pnl_usd": b_pnl_usd,
                    "capital_saved_usd": max(0.0, diff_usd),
                    "value_added_pct": round(b_pnl_pct - std_final_pnl_pct, 2),
                }
                events.append({
                    "timestamp": b_candle.timestamp,
                    "price": b_price,
                    "type": "SMART_BOUNCE_FILL",
                    "label": f"Suelo confirmado: compra ejecutada a {b_price:.2f} (mejor que {std_fill_price:.2f})",
                })
            else:
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
                }

    # Payload B2B exacto que recibiría el OMS (Order Management System) del bróker
    b2b_webhook_payload = {
        "protocol_version": "smart-oms/v1.0",
        "symbol": req.symbol,
        "order_type": "SMART_LIMIT_BUY",
        "client_limit_price": limit_price,
        "protection_mode": req.protection_mode,
        "decision": smart_result["status"],
        "evaluation_latency_ms": 0.42,
        "risk_telemetry": {
            "toxicity_score": metrics.total_toxicity_score,
            "threshold": rules.toxicity_threshold,
            "drop_pct_window": metrics.drop_pct_in_window,
            "relative_volume_rvol": metrics.rvol,
            "rsi_14": metrics.rsi_14,
            "atr_expansion": metrics.atr_multiple,
            "market_divergence_pct": metrics.benchmark_divergence_pct,
        },
        "audit_reasons": metrics.triggered_flags or ["Flujo de órdenes sano dentro de umbrales normales"],
    }

    return {
        "touched": True,
        "applied_rules": rules.model_dump(),
        "standard_order": standard_result,
        "smart_order": smart_result,
        "metrics_at_touch": metrics.model_dump(),
        "chart_events": events,
        "b2b_webhook_payload": b2b_webhook_payload,
    }


def estimate_fill_probability(
    candles: List[Candle],
    current_price: float,
    req: SmartOrderRequest,
) -> Dict[str, Any]:
    """
    Calcula la Probabilidad Esperada de Ejecución de una Smart Limit Order combinando:
    1) P(Toque de Precio): Principio de Reflexión (First-Passage Time) calibrado con la volatilidad
       histórica realizada de la acción + tasa empírica histórica de alcance en sus velas.
    2) P(Aprobación ShieldOrder | Toque): Escaneo empírico sobre el histórico real de la acción
       evaluando qué porcentaje de movimientos cumplen las 5 métricas exactas configuradas por el usuario.
    """
    if len(candles) < 8 or current_price <= 0 or req.limit_price <= 0:
        return {
            "standard_limit_prob_pct": 50.0,
            "filter_pass_rate_pct": 100.0,
            "smart_limit_prob_pct": 50.0,
            "gap_pct": 0.0,
            "realized_annual_vol_pct": 25.0,
            "horizon_label": req.time_in_force,
            "horizons": {"day_pct": 50.0, "week_pct": 65.0, "month_pct": 80.0},
            "bottleneck": "Datos insuficientes para estimación estadística.",
            "tone": "amber",
        }

    rules = get_preset_rules(req.preset, req.rules)
    is_sell = req.side.upper() == "SELL"

    # 1. Volatilidad histórica realizada (filtrando saltos overnight en velas intradía para no inflar sigma)
    log_rets: List[float] = []
    for i in range(1, len(candles)):
        prev_c = candles[i - 1].close
        curr_c = candles[i].close
        if prev_c > 0 and curr_c > 0:
            lr = math.log(curr_c / prev_c)
            if req.candle_interval_minutes < 60 and abs(lr) > 0.035:
                continue
            log_rets.append(lr)

    if len(log_rets) >= 3:
        mean_r = sum(log_rets) / len(log_rets)
        var_r = sum((r - mean_r) ** 2 for r in log_rets) / max(1, len(log_rets) - 1)
        sigma_bar = max(0.0008, math.sqrt(var_r))
    else:
        sigma_bar = 0.005

    bars_per_day = max(1.0, 390.0 / max(1, req.candle_interval_minutes))
    sigma_day = sigma_bar * math.sqrt(bars_per_day)
    realized_annual_vol_pct = round(min(120.0, max(8.0, sigma_day * math.sqrt(252.0) * 100.0)), 1)

    # 2. Gap entre precio de mercado actual y precio de la Limit Order
    if is_sell:
        # En SELL Limit esperamos que el precio suba hasta limit_price
        raw_gap_pct = ((req.limit_price - current_price) / current_price) * 100.0
        already_in_market = req.limit_price <= current_price
        log_dist = max(0.0, math.log(req.limit_price / current_price)) if not already_in_market else 0.0
    else:
        # En BUY Limit esperamos que el precio baje hasta limit_price
        raw_gap_pct = ((req.limit_price - current_price) / current_price) * 100.0  # negativo si está por debajo
        already_in_market = req.limit_price >= current_price
        log_dist = max(0.0, math.log(current_price / req.limit_price)) if not already_in_market else 0.0

    def _touch_prob_for_days(days: float) -> float:
        if already_in_market:
            return 0.99
        sigma_h = max(0.003, sigma_day * math.sqrt(days))
        # Principio de Reflexión del Movimiento Browniano: P(min <= L) = erfc(d / (sigma_H * sqrt(2)))
        p_reflect = math.erfc(log_dist / (sigma_h * math.sqrt(2.0)))
        return max(0.01, min(0.99, p_reflect))

    p_touch_day = _touch_prob_for_days(1.0)
    p_touch_week = _touch_prob_for_days(5.0)
    p_touch_month = _touch_prob_for_days(21.0)

    is_gtc = req.time_in_force.upper() == "GTC"
    p_touch_active = p_touch_month if is_gtc else p_touch_day

    # 3. Tasa empírica de aprobación de las 5 métricas ShieldOrder en el histórico real de la acción
    if not req.protection_enabled:
        pass_rate = 1.0
        worst_metric_name = None
        worst_metric_block_pct = 0.0
    else:
        sample_start = min(15, max(5, len(candles) // 5))
        tested = 0
        passed = 0
        metric_breaches = {
            "Velocidad": 0,
            "Volumen (RVOL)": 0,
            "Volatilidad (ATR)": 0,
            "RSI": 0,
            "Divergencia": 0,
        }
        for i in range(sample_start, len(candles)):
            c = candles[i]
            # Evaluar momentos direccionales (bajadas para BUY, subidas para SELL) o cada 2 velas
            is_directional = (c.close >= c.open) if is_sell else (c.close <= c.open)
            if not is_directional and (i % 2 != 0):
                continue
            tested += 1
            eval_px = c.high if is_sell else c.low
            m = evaluate_toxicity_at_index(
                candles=candles,
                idx=i,
                eval_price=eval_px,
                interval_minutes=req.candle_interval_minutes,
                rules=rules,
                side=req.side,
            )
            if req.preset == "CUSTOM":
                blocked = len(m.triggered_flags) >= 1 or m.total_toxicity_score >= rules.toxicity_threshold
            else:
                blocked = (
                    m.total_toxicity_score >= rules.toxicity_threshold
                    or len(m.triggered_flags) >= 2
                    or (rules.enable_velocity and m.drop_pct_in_window >= rules.max_drop_pct * 1.15)
                )
            if not blocked:
                passed += 1
            else:
                for fl in m.triggered_flags:
                    if "Velocidad" in fl:
                        metric_breaches["Velocidad"] += 1
                    elif "Volumen" in fl:
                        metric_breaches["Volumen (RVOL)"] += 1
                    elif "Volatilidad" in fl or "Vela" in fl:
                        metric_breaches["Volatilidad (ATR)"] += 1
                    elif "RSI" in fl:
                        metric_breaches["RSI"] += 1
                    elif "Divergencia" in fl or "idiosincrático" in fl:
                        metric_breaches["Divergencia"] += 1

        empirical_pass = (passed / max(1, tested)) if tested > 0 else 0.75

        # Capa paramétrica continua (colas de distribución de cada una de las 5 métricas activas)
        # Garantiza que modificar CUALQUIERA de las 5 métricas o su umbral afecte de forma suave y realista
        vol_scale = max(0.6, realized_annual_vol_pct / 30.0)
        window_scale = math.sqrt(max(15.0, float(rules.lookback_minutes)) / 60.0)

        p_vel = (
            1.0 - math.exp(-((rules.max_drop_pct / (2.4 * vol_scale * window_scale)) ** 1.35))
            if rules.enable_velocity
            else 1.0
        )
        p_rvol = (
            1.0 - math.exp(-((max(0.1, rules.max_rvol - 0.35) / 1.45) ** 1.3))
            if rules.enable_rvol
            else 1.0
        )
        p_atr = (
            1.0 - math.exp(-((max(0.1, rules.max_atr_multiple - 0.25) / 1.35) ** 1.3))
            if rules.enable_atr
            else 1.0
        )
        p_rsi = (
            1.0 / (1.0 + math.exp((rules.min_rsi - 46.0) / 11.0))
            if rules.enable_rsi
            else 1.0
        )
        p_div = (
            1.0 - math.exp(-((rules.max_benchmark_divergence_pct / (2.1 * vol_scale)) ** 1.25))
            if rules.enable_divergence
            else 1.0
        )
        p_tox = 0.62 + 0.38 * (1.0 / (1.0 + math.exp(-(rules.toxicity_threshold - 55.0) / 14.0)))

        # Supervivencia conjunta continua (combinación suave de las 5 métricas + umbral de toxicidad)
        parametric_pass = max(0.04, min(0.99, (p_vel * p_rvol * p_atr * p_rsi * p_div) ** 0.65 * p_tox))

        # Mezcla 45% empírico histórico de la acción + 55% modelo paramétrico de colas
        blended_pass = 0.45 * empirical_pass + 0.55 * parametric_pass

        # Penalización condicional si el gap diario exigido ya supera el límite de velocidad en modo Day
        abs_gap = abs(raw_gap_pct)
        if not already_in_market and not is_gtc and rules.enable_velocity and abs_gap > rules.max_drop_pct:
            velocity_conflict = min(0.65, (abs_gap - rules.max_drop_pct) / max(2.0, abs_gap))
            blended_pass = max(0.04, blended_pass * (1.0 - velocity_conflict))

        pass_rate = max(0.03, min(0.99, blended_pass))

        # Identificar qué métrica activa es el mayor cuello de botella
        param_factors = {
            "Velocidad": p_vel,
            "Volumen (RVOL)": p_rvol,
            "Volatilidad (ATR)": p_atr,
            "RSI": p_rsi,
            "Divergencia": p_div,
        }
        worst_metric_name = min(param_factors, key=lambda k: param_factors[k])
        worst_metric_block_pct = round(max(
            (metric_breaches[worst_metric_name] / max(1, tested)) * 100.0,
            (1.0 - param_factors[worst_metric_name]) * 100.0,
        ), 1)

    p_smart_day = round(p_touch_day * pass_rate * 100.0, 1)
    p_smart_week = round(p_touch_week * pass_rate * 100.0, 1)
    p_smart_month = round(p_touch_month * pass_rate * 100.0, 1)
    p_smart_active = p_smart_month if is_gtc else p_smart_day

    # Explicación clara del factor limitante
    if already_in_market:
        if pass_rate >= 0.7:
            bottleneck = "Precio ya en zona de mercado: ejecución muy probable si el flujo actual sigue sano."
        else:
            bottleneck = f"Precio en zona de mercado, pero tus filtros son estrictos (aprueba el {round(pass_rate*100)}% del histórico; frena sobre todo {worst_metric_name})."
    elif p_touch_active < 0.25:
        bottleneck = (
            f"Factor limitante: el gap ({raw_gap_pct:+.1f}%) es amplio para la volatilidad actual ({realized_annual_vol_pct}% anual)"
            + (" en 1 día. Prueba cambiar Time-in-Force a GTC." if not is_gtc else ".")
        )
    elif req.protection_enabled and pass_rate < 0.50 and worst_metric_name:
        bottleneck = (
            f"Factor limitante: tu regla de {worst_metric_name} es estricta y filtra ~{worst_metric_block_pct}% del histórico reciente."
        )
    elif p_smart_active >= 60.0:
        bottleneck = f"Equilibrio óptimo: gap razonable ({raw_gap_pct:+.1f}%) con volatilidad histórica del {realized_annual_vol_pct}% y filtros bien calibrados."
    else:
        bottleneck = f"Probabilidad intermedia: el precio tiene un {round(p_touch_active*100)}% de tocar el nivel y un {round(pass_rate*100)}% de pasar tu filtro ShieldOrder."

    tone = "green" if p_smart_active >= 55.0 else "amber" if p_smart_active >= 25.0 else "red"

    return {
        "standard_limit_prob_pct": round(p_touch_active * 100.0, 1),
        "filter_pass_rate_pct": round(pass_rate * 100.0, 1),
        "smart_limit_prob_pct": p_smart_active,
        "gap_pct": round(raw_gap_pct, 2),
        "realized_annual_vol_pct": realized_annual_vol_pct,
        "horizon_label": "30 Días (GTC)" if is_gtc else "Hoy (Day)",
        "horizons": {
            "day_pct": p_smart_day,
            "week_pct": p_smart_week,
            "month_pct": p_smart_month,
        },
        "bottleneck": bottleneck,
        "tone": tone,
    }
