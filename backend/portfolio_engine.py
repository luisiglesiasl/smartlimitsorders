"""
Motor Cuantitativo Pre-Trade v4.0: Impacto en Cartera & Encaje con el Perfil del Inversor.
- Pesos ponderados por posición y por sector
- Rentabilidad esperada (CAPM / factorial), Volatilidad Markowitz, Beta, Sharpe, VaR 95%, Stress Test -10%
- Sobreescritura en vivo de volatilidad y beta real calculadas desde las velas del activo buscado
- Acoplamiento Cartera <-> ShieldOrder ("Portfolio-Aware Execution"): recomienda automáticamente
  Smart Position Sizing + endurecimiento de reglas y modo óptimo de protección según el perfil.
"""

from __future__ import annotations
import math
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field

RISK_FREE_RATE = 4.25
MARKET_RISK_PREMIUM = 5.8

ASSET_METADATA: Dict[str, Dict[str, Any]] = {
    "ASML": {"name": "ASML Holding N.V.", "sector": "Semiconductors", "beta": 1.38, "vol": 34.5, "exp_ret": 14.2, "style": "GROWTH", "price_fallback": 1336.0},
    "NVDA": {"name": "NVIDIA Corp.", "sector": "Semiconductors", "beta": 1.68, "vol": 46.0, "exp_ret": 16.5, "style": "GROWTH", "price_fallback": 138.0},
    "AMD":  {"name": "AMD Inc.", "sector": "Semiconductors", "beta": 1.62, "vol": 44.0, "exp_ret": 15.2, "style": "GROWTH", "price_fallback": 145.0},
    "INTC": {"name": "Intel Corp.", "sector": "Semiconductors", "beta": 1.15, "vol": 38.0, "exp_ret": 9.8, "style": "VALUE", "price_fallback": 24.0},
    "SMCI": {"name": "Super Micro Computer", "sector": "Technology", "beta": 1.85, "vol": 62.0, "exp_ret": 17.0, "style": "GROWTH", "price_fallback": 42.0},
    "AAPL": {"name": "Apple Inc.", "sector": "Technology", "beta": 1.18, "vol": 24.0, "exp_ret": 11.4, "style": "QUALITY", "price_fallback": 230.0},
    "MSFT": {"name": "Microsoft Corp.", "sector": "Technology", "beta": 1.12, "vol": 23.5, "exp_ret": 11.8, "style": "QUALITY", "price_fallback": 425.0},
    "META": {"name": "Meta Platforms", "sector": "Technology", "beta": 1.35, "vol": 35.0, "exp_ret": 13.8, "style": "GROWTH", "price_fallback": 590.0},
    "GOOGL":{"name": "Alphabet Inc.", "sector": "Technology", "beta": 1.15, "vol": 26.0, "exp_ret": 11.6, "style": "GROWTH", "price_fallback": 175.0},
    "AMZN": {"name": "Amazon.com Inc.", "sector": "Consumer Cyclical", "beta": 1.28, "vol": 31.0, "exp_ret": 13.0, "style": "GROWTH", "price_fallback": 205.0},
    "TSLA": {"name": "Tesla Inc.", "sector": "Consumer Cyclical", "beta": 1.92, "vol": 56.0, "exp_ret": 16.8, "style": "GROWTH", "price_fallback": 385.0},
    "JNJ":  {"name": "Johnson & Johnson", "sector": "Healthcare", "beta": 0.56, "vol": 15.2, "exp_ret": 7.6, "style": "DIVIDEND", "price_fallback": 156.0},
    "UNH":  {"name": "UnitedHealth Group", "sector": "Healthcare", "beta": 0.68, "vol": 19.5, "exp_ret": 9.2, "style": "QUALITY", "price_fallback": 580.0},
    "JPM":  {"name": "JPMorgan Chase", "sector": "Financials", "beta": 1.06, "vol": 22.8, "exp_ret": 10.4, "style": "VALUE", "price_fallback": 240.0},
    "V":    {"name": "Visa Inc.", "sector": "Financials", "beta": 0.94, "vol": 19.8, "exp_ret": 10.8, "style": "QUALITY", "price_fallback": 305.0},
    "PG":   {"name": "Procter & Gamble", "sector": "Consumer Defensive", "beta": 0.48, "vol": 14.5, "exp_ret": 7.2, "style": "DIVIDEND", "price_fallback": 168.0},
    "KO":   {"name": "Coca-Cola Co.", "sector": "Consumer Defensive", "beta": 0.55, "vol": 15.0, "exp_ret": 7.4, "style": "DIVIDEND", "price_fallback": 64.0},
    "XOM":  {"name": "Exxon Mobil", "sector": "Energy", "beta": 0.85, "vol": 24.5, "exp_ret": 9.3, "style": "DIVIDEND", "price_fallback": 118.0},
    "SPY":  {"name": "SPDR S&P 500 ETF", "sector": "Index ETF", "beta": 1.00, "vol": 15.5, "exp_ret": 10.0, "style": "BALANCED", "price_fallback": 595.0},
    "QQQ":  {"name": "Invesco QQQ Trust", "sector": "Index ETF", "beta": 1.18, "vol": 21.0, "exp_ret": 11.8, "style": "GROWTH", "price_fallback": 505.0},
    "BTC-USD": {"name": "Bitcoin USD", "sector": "Crypto", "beta": 1.85, "vol": 58.0, "exp_ret": 19.0, "style": "GROWTH", "price_fallback": 94000.0},
    "ETH-USD": {"name": "Ethereum USD", "sector": "Crypto", "beta": 1.95, "vol": 64.0, "exp_ret": 18.5, "style": "GROWTH", "price_fallback": 3300.0},
    "SOL-USD": {"name": "Solana USD", "sector": "Crypto", "beta": 2.20, "vol": 78.0, "exp_ret": 21.0, "style": "GROWTH", "price_fallback": 215.0},
}


class Holding(BaseModel):
    symbol: str
    shares: float = Field(gt=0)
    price: Optional[float] = None
    sector: Optional[str] = None


class InvestorProfile(BaseModel):
    risk_tolerance: Literal["CONSERVATIVE", "MODERATE", "AGGRESSIVE"] = "MODERATE"
    time_horizon: Literal["SHORT", "MEDIUM", "LONG"] = "LONG"
    target_annual_return_pct: float = Field(default=11.0, ge=3.0, le=30.0)
    investment_style: Literal["GROWTH", "VALUE", "DIVIDEND", "BALANCED"] = "BALANCED"
    preferred_sectors: List[str] = Field(
        default_factory=lambda: ["Technology", "Semiconductors", "Healthcare", "Financials"]
    )
    max_single_position_pct: float = Field(default=15.0, ge=5.0, le=50.0)
    max_sector_weight_pct: float = Field(default=35.0, ge=10.0, le=80.0)


class PortfolioState(BaseModel):
    cash_usd: float = 18500.0
    holdings: List[Holding] = Field(default_factory=list)
    profile: InvestorProfile = Field(default_factory=InvestorProfile)


class PreTradeImpactRequest(BaseModel):
    symbol: str
    side: Literal["BUY", "SELL"] = "BUY"
    quantity: float = Field(gt=0)
    limit_price: float = Field(gt=0)
    portfolio: Optional[PortfolioState] = None
    live_asset_vol_pct: Optional[float] = None
    live_asset_beta: Optional[float] = None


def get_default_portfolio() -> PortfolioState:
    """Cartera realista de 8 acciones en Interactive Brokers + liquidez inicial."""
    return PortfolioState(
        cash_usd=18500.0,
        holdings=[
            Holding(symbol="AAPL", shares=55, price=230.0),
            Holding(symbol="MSFT", shares=30, price=425.0),
            Holding(symbol="NVDA", shares=75, price=138.0),
            Holding(symbol="JNJ", shares=70, price=156.0),
            Holding(symbol="JPM", shares=45, price=240.0),
            Holding(symbol="V", shares=35, price=305.0),
            Holding(symbol="PG", shares=55, price=168.0),
            Holding(symbol="XOM", shares=80, price=118.0),
        ],
        profile=InvestorProfile(),
    )


def get_asset_info(
    symbol: str,
    fallback_price: float = 100.0,
    live_vol_pct: Optional[float] = None,
    live_beta: Optional[float] = None,
) -> Dict[str, Any]:
    sym = symbol.strip().upper()
    if sym in ASSET_METADATA:
        info = dict(ASSET_METADATA[sym])
        info["symbol"] = sym
        info["data_calibrated"] = "Referencia + Mercado en vivo" if live_vol_pct else "Referencia cuantitativa"
    else:
        is_crypto = sym.endswith("-USD") or sym.endswith("USDT")
        info = {
            "symbol": sym,
            "name": sym,
            "sector": "Crypto" if is_crypto else "Equities / Other",
            "beta": 1.85 if is_crypto else 1.12,
            "vol": 60.0 if is_crypto else 27.5,
            "exp_ret": 17.5 if is_crypto else round(RISK_FREE_RATE + 1.12 * MARKET_RISK_PREMIUM, 1),
            "style": "GROWTH" if is_crypto else "BALANCED",
            "price_fallback": fallback_price,
            "data_calibrated": "Calibrado en vivo (velas reales)" if live_vol_pct else "Estimación sectorial",
        }

    if live_vol_pct and live_vol_pct > 5.0:
        info["vol"] = round(0.55 * float(live_vol_pct) + 0.45 * float(info["vol"]), 1)
    if live_beta and live_beta > 0.2:
        info["beta"] = round(0.50 * float(live_beta) + 0.50 * float(info["beta"]), 2)
        if sym not in ASSET_METADATA:
            info["exp_ret"] = round(RISK_FREE_RATE + info["beta"] * MARKET_RISK_PREMIUM, 1)
    return info


def _pair_correlation(sec_a: str, sec_b: str, sym_a: str, sym_b: str) -> float:
    if sym_a == sym_b:
        return 1.0
    if sec_a == "Cash" or sec_b == "Cash":
        return 0.0
    if sec_a == "Crypto" or sec_b == "Crypto":
        return 0.78 if sec_a == sec_b else 0.22
    tech_cluster = {"Technology", "Semiconductors"}
    if sec_a == sec_b:
        return 0.68
    if sec_a in tech_cluster and sec_b in tech_cluster:
        return 0.62
    defensive = {"Healthcare", "Consumer Defensive"}
    if sec_a in defensive and sec_b in defensive:
        return 0.48
    return 0.36


def _compute_portfolio_metrics(positions: List[Dict[str, Any]], cash_usd: float) -> Dict[str, Any]:
    equity_val = sum(p["value_usd"] for p in positions)
    total_nav = max(1.0, equity_val + max(0.0, cash_usd))

    all_nodes = []
    for p in positions:
        w = p["value_usd"] / total_nav
        all_nodes.append({**p, "weight": w})
    if cash_usd > 0:
        all_nodes.append({
            "symbol": "CASH",
            "sector": "Cash",
            "beta": 0.0,
            "vol": 0.5,
            "exp_ret": RISK_FREE_RATE,
            "weight": cash_usd / total_nav,
            "value_usd": cash_usd,
        })

    exp_return = sum(n["weight"] * n["exp_ret"] for n in all_nodes)
    beta = sum(n["weight"] * n["beta"] for n in all_nodes)

    variance = 0.0
    for a in all_nodes:
        for b in all_nodes:
            rho = _pair_correlation(a["sector"], b["sector"], a["symbol"], b["symbol"])
            variance += a["weight"] * b["weight"] * (a["vol"] / 100.0) * (b["vol"] / 100.0) * rho
    vol_pct = math.sqrt(max(0.0, variance)) * 100.0
    sharpe = (exp_return - RISK_FREE_RATE) / max(0.5, vol_pct)

    monthly_vol = (vol_pct / 100.0) / math.sqrt(12.0)
    monthly_mu = (exp_return / 100.0) / 12.0
    var_95_pct = max(0.0, (1.645 * monthly_vol - monthly_mu) * 100.0)
    var_95_usd = (var_95_pct / 100.0) * total_nav

    stress_10_pct = round(-10.0 * beta, 2)
    stress_10_usd = round((stress_10_pct / 100.0) * total_nav, 2)

    inv_weights = [p["value_usd"] / max(1.0, equity_val) for p in positions if p["value_usd"] > 0]
    hhi = sum(w * w for w in inv_weights) if inv_weights else 1.0
    effective_n = round(1.0 / max(1e-6, hhi), 2)

    sectors: Dict[str, float] = {}
    for n in all_nodes:
        sec = n["sector"]
        sectors[sec] = round(sectors.get(sec, 0.0) + n["weight"] * 100.0, 2)

    return {
        "total_nav_usd": round(total_nav, 2),
        "invested_usd": round(equity_val, 2),
        "cash_usd": round(cash_usd, 2),
        "cash_pct": round((cash_usd / total_nav) * 100.0, 2),
        "expected_return_pct": round(exp_return, 2),
        "volatility_pct": round(vol_pct, 2),
        "beta": round(beta, 2),
        "sharpe_ratio": round(sharpe, 2),
        "var_95_monthly_pct": round(var_95_pct, 2),
        "var_95_monthly_usd": round(var_95_usd, 2),
        "stress_mkt_minus10_pct": stress_10_pct,
        "stress_mkt_minus10_usd": stress_10_usd,
        "effective_n": effective_n,
        "sector_weights": sectors,
        "positions": [
            {
                "symbol": p["symbol"],
                "name": p["name"],
                "sector": p["sector"],
                "shares": round(p["shares"], 4),
                "price": round(p["price"], 2),
                "value_usd": round(p["value_usd"], 2),
                "weight_pct": round((p["value_usd"] / total_nav) * 100.0, 2),
                "beta": p["beta"],
                "vol": p["vol"],
                "exp_ret": p["exp_ret"],
            }
            for p in sorted(positions, key=lambda x: x["value_usd"], reverse=True)
        ],
    }


def evaluate_pre_trade_impact(req: PreTradeImpactRequest) -> Dict[str, Any]:
    pf = req.portfolio or get_default_portfolio()
    profile = pf.profile
    target_sym = req.symbol.strip().upper()
    target_meta = get_asset_info(
        target_sym,
        fallback_price=req.limit_price,
        live_vol_pct=req.live_asset_vol_pct,
        live_beta=req.live_asset_beta,
    )
    order_value = round(req.quantity * req.limit_price, 2)

    # 1. Estado ANTES
    before_pos_map: Dict[str, Dict[str, Any]] = {}
    for h in pf.holdings:
        sym = h.symbol.strip().upper()
        is_target = sym == target_sym
        meta = get_asset_info(
            sym,
            fallback_price=h.price or 100.0,
            live_vol_pct=req.live_asset_vol_pct if is_target else None,
            live_beta=req.live_asset_beta if is_target else None,
        )
        px = float(h.price) if h.price and h.price > 0 else float(meta["price_fallback"])
        if is_target:
            px = req.limit_price
        before_pos_map[sym] = {
            "symbol": sym,
            "name": meta["name"],
            "sector": h.sector or meta["sector"],
            "shares": float(h.shares),
            "price": px,
            "value_usd": float(h.shares) * px,
            "beta": meta["beta"],
            "vol": meta["vol"],
            "exp_ret": meta["exp_ret"],
            "style": meta["style"],
        }

    before_metrics = _compute_portfolio_metrics(list(before_pos_map.values()), pf.cash_usd)

    # 2. Estado DESPUÉS
    after_pos_map = {k: dict(v) for k, v in before_pos_map.items()}
    if req.side == "BUY":
        after_cash = pf.cash_usd - order_value
        if target_sym in after_pos_map:
            after_pos_map[target_sym]["shares"] += req.quantity
            after_pos_map[target_sym]["price"] = req.limit_price
            after_pos_map[target_sym]["value_usd"] = after_pos_map[target_sym]["shares"] * req.limit_price
        else:
            after_pos_map[target_sym] = {
                "symbol": target_sym,
                "name": target_meta["name"],
                "sector": target_meta["sector"],
                "shares": req.quantity,
                "price": req.limit_price,
                "value_usd": order_value,
                "beta": target_meta["beta"],
                "vol": target_meta["vol"],
                "exp_ret": target_meta["exp_ret"],
                "style": target_meta["style"],
            }
    else:
        after_cash = pf.cash_usd + order_value
        if target_sym in after_pos_map:
            new_sh = max(0.0, after_pos_map[target_sym]["shares"] - req.quantity)
            if new_sh <= 0.0001:
                del after_pos_map[target_sym]
            else:
                after_pos_map[target_sym]["shares"] = new_sh
                after_pos_map[target_sym]["value_usd"] = new_sh * req.limit_price

    after_metrics = _compute_portfolio_metrics(list(after_pos_map.values()), after_cash)

    before_sym_weight = next((p["weight_pct"] for p in before_metrics["positions"] if p["symbol"] == target_sym), 0.0)
    after_sym_weight = next((p["weight_pct"] for p in after_metrics["positions"] if p["symbol"] == target_sym), 0.0)

    target_sector = target_meta["sector"]
    before_sec_weight = before_metrics["sector_weights"].get(target_sector, 0.0)
    after_sec_weight = after_metrics["sector_weights"].get(target_sector, 0.0)

    fit_score = 92
    warnings: List[str] = []
    positives: List[str] = []

    risk_caps = {
        "CONSERVATIVE": {"max_beta": 0.90, "max_vol": 14.5},
        "MODERATE": {"max_beta": 1.18, "max_vol": 19.5},
        "AGGRESSIVE": {"max_beta": 1.55, "max_vol": 28.0},
    }[profile.risk_tolerance]

    over_single = req.side == "BUY" and after_sym_weight > profile.max_single_position_pct
    over_sector = req.side == "BUY" and after_sec_weight > profile.max_sector_weight_pct
    over_beta = after_metrics["beta"] > risk_caps["max_beta"]
    over_vol = after_metrics["volatility_pct"] > risk_caps["max_vol"]

    if over_single:
        excess = round(after_sym_weight - profile.max_single_position_pct, 1)
        fit_score -= min(28, int(excess * 2.5 + 8))
        warnings.append(
            f"Sobreconcentración en {target_sym}: pasaría a pesar un {after_sym_weight:.1f}% de tu cartera (límite {profile.max_single_position_pct:.0f}%)."
        )
    elif req.side == "BUY":
        positives.append(
            f"Peso controlado en {target_sym} ({after_sym_weight:.1f}%), dentro de tu límite máximo del {profile.max_single_position_pct:.0f}%."
        )

    if over_sector:
        excess_s = round(after_sec_weight - profile.max_sector_weight_pct, 1)
        fit_score -= min(20, int(excess_s * 1.5 + 6))
        warnings.append(
            f"Exposición elevada al sector {target_sector}: sube de {before_sec_weight:.1f}% a {after_sec_weight:.1f}% (límite {profile.max_sector_weight_pct:.0f}%)."
        )

    if over_beta:
        fit_score -= 16
        warnings.append(
            f"Riesgo de mercado alto para perfil {profile.risk_tolerance}: la Beta sube a {after_metrics['beta']:.2f} (máx. recomendado {risk_caps['max_beta']:.2f})."
        )
    if over_vol:
        fit_score -= 14
        warnings.append(
            f"Volatilidad proyectada ({after_metrics['volatility_pct']:.1f}%) por encima del rango ideal (< {risk_caps['max_vol']:.1f}%)."
        )

    if req.side == "BUY" and profile.time_horizon == "SHORT" and target_meta["vol"] > 28.0:
        fit_score -= 15
        warnings.append(
            f"Desajuste temporal: {target_sym} tiene alta volatilidad ({target_meta['vol']:.0f}% anual) para horizonte corto plazo."
        )
    elif req.side == "BUY" and profile.time_horizon == "LONG" and target_meta["exp_ret"] >= profile.target_annual_return_pct:
        positives.append(
            f"Buen encaje con horizonte largo plazo: impulsa la rentabilidad hacia tu meta del {profile.target_annual_return_pct:.1f}%."
        )

    if req.side == "BUY" and profile.preferred_sectors and target_sector not in profile.preferred_sectors:
        fit_score -= 8
        warnings.append(
            f"El sector {target_sector} no figura entre tus sectores preferidos ({', '.join(profile.preferred_sectors[:3])})."
        )
    elif req.side == "BUY" and target_sector in profile.preferred_sectors:
        positives.append(f"Alineada con tu sector preferido ({target_sector}).")

    if after_cash < 0:
        fit_score -= 18
        warnings.append(
            f"Liquidez insuficiente: esta orden requiere ${abs(after_cash):,.2f} en margen (cash actual ${pf.cash_usd:,.2f})."
        )

    delta_ret = round(after_metrics["expected_return_pct"] - before_metrics["expected_return_pct"], 2)
    delta_vol = round(after_metrics["volatility_pct"] - before_metrics["volatility_pct"], 2)
    delta_beta = round(after_metrics["beta"] - before_metrics["beta"], 2)
    delta_sharpe = round(after_metrics["sharpe_ratio"] - before_metrics["sharpe_ratio"], 2)

    if delta_sharpe > 0:
        positives.append(f"Mejora la eficiencia riesgo/retorno de la cartera (Ratio de Sharpe +{delta_sharpe:.2f}).")
    if after_metrics["expected_return_pct"] >= profile.target_annual_return_pct:
        positives.append(
            f"Rentabilidad proyectada ({after_metrics['expected_return_pct']:.2f}%) cumple tu meta anual ({profile.target_annual_return_pct:.1f}%)."
        )

    fit_score = max(15, min(99, fit_score))
    if fit_score >= 78:
        fit_label = "Encaje Óptimo"
        fit_tone = "green"
    elif fit_score >= 55:
        fit_label = "Encaje Moderado (Revisar alertas)"
        fit_tone = "amber"
    else:
        fit_label = "Desalineado con tu Perfil"
        fit_tone = "red"

    # Smart Position Sizing
    nav_base = before_metrics["total_nav_usd"]
    current_sym_val = before_pos_map[target_sym]["value_usd"] if target_sym in before_pos_map else 0.0
    room_by_single = max(0.0, (profile.max_single_position_pct / 100.0) * nav_base - current_sym_val)
    room_by_sector = max(0.0, (profile.max_sector_weight_pct / 100.0) * nav_base - (before_sec_weight / 100.0) * nav_base)
    safe_budget = min(room_by_single, room_by_sector, max(0.0, pf.cash_usd))
    suggested_qty = max(1, int(safe_budget // req.limit_price)) if safe_budget >= req.limit_price else 0

    if req.side == "BUY" and suggested_qty > 0 and suggested_qty < req.quantity:
        sizing_advice = (
            f"Smart Sizing: reducir a {suggested_qty} acc. (${suggested_qty * req.limit_price:,.0f}) "
            f"mantiene {target_sym} y {target_sector} dentro de tus límites sin usar margen."
        )
    elif req.side == "BUY" and suggested_qty == 0 and (over_single or after_cash < 0):
        sizing_advice = (
            f"Smart Sizing: ya estás en tu límite de liquidez o exposición en {target_sector}; considera reducir otra posición."
        )
    else:
        suggested_qty = int(req.quantity)
        sizing_advice = f"Tamaño adecuado: {int(req.quantity)} acciones encajan dentro de tu presupuesto y límites."

    # ACOPLAMIENTO CARTERA <-> SHIELDORDER (Portfolio-Aware Execution)
    tightening_factor = 1.0
    rec_preset = "AUTO_SIGMA"
    rec_mode = "SMART_TRANCHES"
    rec_reasons: List[str] = []

    if profile.risk_tolerance == "CONSERVATIVE":
        tightening_factor *= 0.80
        rec_preset = "CONSERVATIVE"
        rec_mode = "INVALIDATE"
        rec_reasons.append("perfil Conservador")
    elif profile.risk_tolerance == "AGGRESSIVE":
        tightening_factor *= 1.10
        rec_mode = "TRAILING_FLOOR"
        rec_reasons.append("perfil Agresivo")

    if over_single or over_sector or over_beta or after_cash < 0:
        tightening_factor = round(max(0.70, tightening_factor * 0.82), 2)
        if profile.risk_tolerance != "CONSERVATIVE":
            rec_mode = "SMART_TRANCHES"
        rec_reasons.append("sobreexposición en cartera")

    needs_action = (suggested_qty > 0 and suggested_qty < req.quantity) or (tightening_factor < 0.95)
    mode_labels = {
        "INVALIDATE": "Invalidar",
        "SMART_TRANCHES": "Tramos Adaptativos",
        "TRAILING_FLOOR": "Suelo Dinámico",
        "REQUIRE_CONFIRMATION": "Confirmación Móvil",
    }
    if needs_action:
        reason_txt = (
            f"Por {', '.join(rec_reasons) or 'encaje de cartera'}: recomendamos "
            f"{f'{suggested_qty} acc. + ' if suggested_qty < req.quantity else ''}"
            f"modo {mode_labels.get(rec_mode, rec_mode)} con umbrales reforzados ({int(round((1 - tightening_factor) * 100))}% más estrictos)."
        )
    else:
        reason_txt = (
            f"Cartera equilibrada: Auto-Calibración σ + {mode_labels.get(rec_mode, rec_mode)} es óptimo para {target_sym}."
        )

    recommended_protection = {
        "needs_adjustment": needs_action,
        "recommended_qty": suggested_qty,
        "recommended_preset": rec_preset,
        "recommended_mode": rec_mode,
        "recommended_mode_label": mode_labels.get(rec_mode, rec_mode),
        "tightening_factor": round(tightening_factor, 2),
        "summary": reason_txt,
    }

    all_sec_keys = sorted(set(list(before_metrics["sector_weights"].keys()) + list(after_metrics["sector_weights"].keys())))
    sector_comparison = [
        {
            "sector": sec,
            "before_pct": before_metrics["sector_weights"].get(sec, 0.0),
            "after_pct": after_metrics["sector_weights"].get(sec, 0.0),
            "delta_pct": round(after_metrics["sector_weights"].get(sec, 0.0) - before_metrics["sector_weights"].get(sec, 0.0), 2),
            "is_target_sector": sec == target_sector,
        }
        for sec in all_sec_keys
    ]
    sector_comparison.sort(key=lambda x: x["after_pct"], reverse=True)

    return {
        "symbol": target_sym,
        "asset_sector": target_sector,
        "asset_beta": target_meta["beta"],
        "asset_vol": target_meta["vol"],
        "asset_exp_ret": target_meta["exp_ret"],
        "asset_calibration_source": target_meta.get("data_calibrated", "Referencia"),
        "side": req.side,
        "quantity": req.quantity,
        "limit_price": req.limit_price,
        "order_value_usd": order_value,
        "before": before_metrics,
        "after": after_metrics,
        "deltas": {
            "symbol_weight_before_pct": before_sym_weight,
            "symbol_weight_after_pct": after_sym_weight,
            "symbol_weight_delta_pct": round(after_sym_weight - before_sym_weight, 2),
            "sector_weight_before_pct": before_sec_weight,
            "sector_weight_after_pct": after_sec_weight,
            "sector_weight_delta_pct": round(after_sec_weight - before_sec_weight, 2),
            "expected_return_delta_pct": delta_ret,
            "volatility_delta_pct": delta_vol,
            "beta_delta": delta_beta,
            "sharpe_delta": delta_sharpe,
            "var_95_delta_usd": round(after_metrics["var_95_monthly_usd"] - before_metrics["var_95_monthly_usd"], 2),
        },
        "sector_comparison": sector_comparison,
        "fit_score": fit_score,
        "fit_label": fit_label,
        "fit_tone": fit_tone,
        "warnings": warnings,
        "positives": positives,
        "suggested_optimal_qty": suggested_qty,
        "sizing_advice": sizing_advice,
        "recommended_protection": recommended_protection,
        "profile_used": profile.model_dump(),
    }
