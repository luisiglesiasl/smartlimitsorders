"""
Servidor API B2B v4.0 · ShieldOrder Execution Suite (Estilo Interactive Brokers Client Portal):
- Datos reales en vivo y casos históricos reales con Earnings/Gap Guard
- Simulador Cuantitativo Block-Bootstrap Monte Carlo + Frontera de Precio Óptimo
- Auto-Calibración por Régimen σ + Acoplamiento Cartera <-> Protección
- Modos Pro-Volumen (Smart Tranches, Suelo Dinámico) + Recibo Criptográfico MiFID II SHA-256
- Consola Ejecutiva B2B para Brókers (ROI Simulator & Compliance Audit Log)
"""

from __future__ import annotations
import time
import uuid
from pathlib import Path
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from engine import (
    Candle,
    RiskRules,
    SmartOrderRequest,
    simulate_order_comparison,
    evaluate_toxicity_at_index,
    get_preset_rules,
    estimate_fill_probability,
    build_mifid_audit_receipt,
)
from market_data import (
    REAL_CRASH_CASES,
    POPULAR_TICKERS,
    fetch_real_market_data,
    search_tickers,
)
from ai_copilot import parse_natural_language_order, generate_audit_explanation
from portfolio_engine import (
    PortfolioState,
    PreTradeImpactRequest,
    get_default_portfolio,
    evaluate_pre_trade_impact,
)

app = FastAPI(
    title="ShieldOrder B2B v4.0 · Institutional Smart Limit & Pre-Trade OMS API",
    version="4.0.0",
    description=(
        "Infraestructura B2B integrable en el Order Ticket y OMS de brókers: "
        "Smart Limit Orders con auto-calibración σ, modos pro-volumen (Tramos Adaptativos y Suelo Dinámico), "
        "Monte Carlo condicional, inteligencia pre-trade de cartera y auditoría criptográfica MiFID II."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def disable_static_cache(request, call_next):
    response = await call_next(request)
    if request.url.path in ("/", "/index.html", "/app.js", "/styles.css"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


ACTIVE_ORDERS: List[Dict[str, Any]] = []
USER_PORTFOLIO: PortfolioState = get_default_portfolio()
AUDIT_RECEIPTS_LOG: List[Dict[str, Any]] = []


def _record_audit_receipt(receipt: Optional[Dict[str, Any]]) -> None:
    if not receipt:
        return
    rid = receipt.get("receipt_id")
    if any(r.get("receipt_id") == rid for r in AUDIT_RECEIPTS_LOG):
        return
    AUDIT_RECEIPTS_LOG.insert(0, receipt)
    if len(AUDIT_RECEIPTS_LOG) > 40:
        del AUDIT_RECEIPTS_LOG[40:]


class SimulationAPIRequest(BaseModel):
    symbol: str = "ASML"
    timeframe: str = "5m"
    case_id: Optional[str] = None
    limit_price: float = 0.0
    quantity: float = 10.0
    side: str = "BUY"
    time_in_force: str = "Day"
    protection_enabled: bool = True
    protection_mode: str = "SMART_TRANCHES"
    preset: str = "AUTO_SIGMA"
    rules: RiskRules = Field(default_factory=RiskRules)
    portfolio_tightening: float = Field(default=1.0, ge=0.65, le=1.25)
    earnings_window_override: Optional[bool] = None
    gemini_api_key: Optional[str] = None


class PlaceLiveOrderRequest(BaseModel):
    symbol: str
    side: str = "BUY"
    quantity: float = 10.0
    order_type: str = "SMART_LIMIT"
    limit_price: float
    time_in_force: str = "Day"
    outside_rth: bool = False
    protection_enabled: bool = True
    protection_mode: str = "SMART_TRANCHES"
    preset: str = "AUTO_SIGMA"
    rules: RiskRules = Field(default_factory=RiskRules)


class CopilotAPIRequest(BaseModel):
    prompt: str
    default_limit_price: float = 90.0
    gemini_api_key: Optional[str] = None


class BrokerLiveCheckRequest(BaseModel):
    broker_order_id: str = "IBKR-994812"
    symbol: str = "ASML"
    side: str = "BUY"
    limit_price: float = 1280.0
    quantity: float = 10.0
    candle_interval_minutes: int = 5
    preset: str = "AUTO_SIGMA"
    protection_mode: str = "SMART_TRANCHES"
    earnings_window_active: bool = False
    bid: Optional[float] = None
    ask: Optional[float] = None
    spread_bps: Optional[float] = None
    order_book_imbalance: Optional[float] = None
    recent_candles: List[Candle]


class BrokerROISimulationRequest(BaseModel):
    active_retail_accounts: int = Field(default=250_000, ge=1_000, le=50_000_000)
    monthly_limit_volume_musd: float = Field(default=850.0, ge=1.0, le=500_000.0)
    avg_fee_bps: float = Field(default=6.5, ge=0.5, le=100.0)
    pro_volume_mode_adoption_pct: float = Field(default=68.0, ge=0.0, le=100.0)
    premium_tier_adoption_pct: float = Field(default=6.5, ge=0.0, le=40.0)
    premium_monthly_price_usd: float = Field(default=7.99, ge=1.0, le=50.0)
    margin_account_share_pct: float = Field(default=22.0, ge=0.0, le=100.0)


@app.get("/api/v1/market/search", tags=["1. Mercado Real"])
async def api_search_tickers(q: str = Query(default="")):
    return {"results": await search_tickers(q), "popular": POPULAR_TICKERS}


@app.get("/api/v1/market/real-cases", tags=["1. Mercado Real"])
async def api_real_cases():
    return {"cases": REAL_CRASH_CASES}


@app.get("/api/v1/market/quote", tags=["1. Mercado Real"])
async def api_market_quote(
    symbol: str = Query(default="ASML"),
    timeframe: str = Query(default="5m"),
    case_id: Optional[str] = Query(default=None),
):
    try:
        bundle = await fetch_real_market_data(symbol=symbol, timeframe=timeframe, case_id=case_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo cargar {symbol}: {exc}")

    last_price = bundle["quote"]["last_price"]
    tox_score = bundle["live_toxicity"]["total_toxicity_score"]
    for order in ACTIVE_ORDERS:
        if order["symbol"] == bundle["symbol"] and order["status"] in ("WORKING_MONITORING", "ARMED_SHIELD"):
            order["last_seen_price"] = last_price
            order["current_toxicity"] = tox_score
            dist_pct = round(((last_price - order["limit_price"]) / order["limit_price"]) * 100.0, 2)
            order["distance_pct"] = dist_pct
            threshold = order["rules"]["toxicity_threshold"]
            touched = (last_price <= order["limit_price"]) if order["side"] == "BUY" else (last_price >= order["limit_price"])
            if touched:
                if order["protection_enabled"] and tox_score >= threshold:
                    order["status"] = "BLOCKED_BY_SHIELD"
                    order["status_note"] = f"Protegida en vivo @ ${last_price} ({order['protection_mode']} · Tox {tox_score})"
                else:
                    order["status"] = "FILLED_LIVE"
                    order["status_note"] = f"Ejecutada en vivo @ ${last_price} (Flujo sano {tox_score}/100)"
            elif order["protection_enabled"] and tox_score >= threshold:
                order["status"] = "ARMED_SHIELD"
                order["status_note"] = f"Escudo armado: toxicidad actual {tox_score}/100"
            else:
                order["status"] = "WORKING_MONITORING"
                order["status_note"] = f"Vigilando precio (a {dist_pct}% del límite)"

    return {
        "symbol": bundle["symbol"],
        "timeframe": bundle["timeframe"],
        "interval_minutes": bundle["interval_minutes"],
        "data_source": bundle["source"],
        "case_info": bundle["case_info"],
        "quote": bundle["quote"],
        "suggested_limit_price": bundle["suggested_limit_price"],
        "asset_stats": bundle["asset_stats"],
        "earnings_window_active": bundle["earnings_window_active"],
        "earnings_label": bundle["earnings_label"],
        "live_toxicity": bundle["live_toxicity"],
        "candles": [c.model_dump() for c in bundle["candles"]],
        "active_orders": ACTIVE_ORDERS,
    }


@app.post("/api/v1/simulate", tags=["2. Simulación & Backtest Real"])
async def run_simulation(req: SimulationAPIRequest):
    try:
        bundle = await fetch_real_market_data(
            symbol=req.symbol,
            timeframe=req.timeframe,
            case_id=req.case_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error obteniendo datos reales de {req.symbol}: {exc}")

    candles = bundle["candles"]
    effective_limit = req.limit_price if req.limit_price > 0 else bundle["suggested_limit_price"]
    earnings_active = (
        req.earnings_window_override
        if req.earnings_window_override is not None
        else bundle["earnings_window_active"]
    )

    valid_modes = {"INVALIDATE", "SMART_TRANCHES", "TRAILING_FLOOR", "WAIT_FOR_BOUNCE", "REQUIRE_CONFIRMATION"}
    safe_mode = req.protection_mode if req.protection_mode in valid_modes else "SMART_TRANCHES"
    valid_presets = {"AUTO_SIGMA", "CONSERVATIVE", "MODERATE", "AGGRESSIVE", "CUSTOM"}
    safe_preset = req.preset if req.preset in valid_presets else "AUTO_SIGMA"

    sim_req = SmartOrderRequest(
        symbol=bundle["symbol"],
        scenario_id=req.case_id or "live_market",
        candle_interval_minutes=bundle["interval_minutes"],
        side="SELL" if req.side.upper() == "SELL" else "BUY",
        limit_price=effective_limit,
        quantity=req.quantity,
        time_in_force=req.time_in_force,
        protection_enabled=req.protection_enabled,
        protection_mode=safe_mode,
        preset=safe_preset,
        rules=req.rules,
        earnings_window_active=earnings_active,
        earnings_label=bundle["earnings_label"],
        portfolio_tightening=req.portfolio_tightening,
    )

    comparison = simulate_order_comparison(candles, sim_req)
    fill_prob = estimate_fill_probability(
        candles=candles,
        current_price=bundle["quote"]["last_price"],
        req=sim_req,
    )
    notification = generate_audit_explanation(
        symbol=bundle["symbol"],
        sim_result=comparison,
        api_key=req.gemini_api_key,
    )
    if comparison.get("audit_receipt"):
        _record_audit_receipt(comparison["audit_receipt"])

    return {
        "symbol": bundle["symbol"],
        "timeframe": bundle["timeframe"],
        "data_source": bundle["source"],
        "case_info": bundle["case_info"],
        "quote": bundle["quote"],
        "suggested_limit_price": bundle["suggested_limit_price"],
        "effective_limit_price": effective_limit,
        "asset_stats": bundle["asset_stats"],
        "earnings_window_active": earnings_active,
        "earnings_label": bundle["earnings_label"],
        "live_toxicity": bundle["live_toxicity"],
        "fill_probability": fill_prob,
        "candles": [c.model_dump() for c in candles],
        "simulation": comparison,
        "client_notification": notification,
    }


@app.get("/api/v1/orders", tags=["3. Órdenes Activas (Paper Live)"])
async def list_orders():
    return {"orders": ACTIVE_ORDERS}


@app.post("/api/v1/orders", tags=["3. Órdenes Activas (Paper Live)"])
async def place_live_order(req: PlaceLiveOrderRequest):
    rules = get_preset_rules(req.preset, req.rules)
    order_id = f"SHD-{str(uuid.uuid4())[:6].upper()}"
    order = {
        "order_id": order_id,
        "created_at": int(time.time()),
        "symbol": req.symbol.upper(),
        "side": req.side.upper(),
        "quantity": req.quantity,
        "order_type": req.order_type,
        "limit_price": round(req.limit_price, 2),
        "estimated_usd": round(req.limit_price * req.quantity, 2),
        "time_in_force": req.time_in_force,
        "outside_rth": req.outside_rth,
        "protection_enabled": req.protection_enabled,
        "protection_mode": req.protection_mode,
        "preset": req.preset,
        "rules": rules.model_dump(),
        "status": "WORKING_MONITORING",
        "status_note": "Orden sintética en vigilancia en tiempo real",
        "current_toxicity": 0.0,
        "distance_pct": 0.0,
    }
    ACTIVE_ORDERS.insert(0, order)
    return {"order": order, "orders": ACTIVE_ORDERS}


@app.delete("/api/v1/orders/{order_id}", tags=["3. Órdenes Activas (Paper Live)"])
async def cancel_order(order_id: str):
    global ACTIVE_ORDERS
    ACTIVE_ORDERS = [o for o in ACTIVE_ORDERS if o["order_id"] != order_id]
    return {"orders": ACTIVE_ORDERS}


@app.post("/api/v1/ai/copilot", tags=["4. Google Gemini Copilot"])
async def ai_order_copilot(req: CopilotAPIRequest):
    return parse_natural_language_order(
        prompt=req.prompt,
        default_limit_price=req.default_limit_price,
        api_key=req.gemini_api_key,
    )


@app.post("/api/v1/b2b/evaluate-order", tags=["5. Integración OMS Bróker (Producción)"])
async def broker_oms_evaluate(req: BrokerLiveCheckRequest):
    if len(req.recent_candles) < 5:
        raise HTTPException(status_code=400, detail="Se requieren al menos 5 velas recientes.")

    eff_spread = req.spread_bps
    if eff_spread is None and req.bid and req.ask and req.ask > req.bid and req.bid > 0:
        mid = (req.ask + req.bid) / 2.0
        eff_spread = round(((req.ask - req.bid) / mid) * 10_000.0, 1)

    rules = get_preset_rules(req.preset, RiskRules(), candles=req.recent_candles, interval_minutes=req.candle_interval_minutes)
    metrics = evaluate_toxicity_at_index(
        candles=req.recent_candles,
        idx=len(req.recent_candles) - 1,
        eval_price=req.limit_price,
        interval_minutes=req.candle_interval_minutes,
        rules=rules,
        side=req.side,
        earnings_window_active=req.earnings_window_active,
        spread_bps=eff_spread,
        order_book_imbalance=req.order_book_imbalance,
    )
    is_dangerous = metrics.total_toxicity_score >= rules.toxicity_threshold or len(metrics.triggered_flags) >= 2
    action = "ROUTE_TO_EXCHANGE" if not is_dangerous else req.protection_mode
    vol_pres = 100.0 if not is_dangerous or action in ("SMART_TRANCHES", "TRAILING_FLOOR") else 0.0

    receipt = build_mifid_audit_receipt(
        symbol=req.symbol,
        side=req.side,
        limit_price=req.limit_price,
        quantity=req.quantity,
        protection_mode=req.protection_mode,
        decision=action,
        rules=rules,
        metrics=metrics,
        broker_volume_preserved_pct=vol_pres,
        capital_saved_usd=0.0,
    )
    _record_audit_receipt(receipt)

    return {
        "broker_order_id": req.broker_order_id,
        "symbol": req.symbol,
        "oms_action": action,
        "broker_volume_preserved_pct": vol_pres,
        "toxicity_score": metrics.total_toxicity_score,
        "threshold": rules.toxicity_threshold,
        "metrics": metrics.model_dump(),
        "compliance_receipt": receipt,
    }


@app.post("/api/v1/b2b/roi-estimate", tags=["5. Integración OMS Bróker (Producción)"])
async def broker_executive_roi(req: BrokerROISimulationRequest):
    """
    Simulador Financiero B2B para CEO/CPO/CFO de un bróker:
    cuantifica el volumen preservado (resolviendo la objeción de pérdida de comisiones),
    capital protegido a clientes, retención de AUM/LTV, ahorro en bad-debt de margen
    e ingresos recurrentes por tier Premium white-label.
    """
    annual_limit_vol_usd = req.monthly_limit_volume_musd * 12.0 * 1_000_000.0
    annual_limit_rev_usd = annual_limit_vol_usd * (req.avg_fee_bps / 10_000.0)

    # Aproximadamente el 9.5% del volumen en limit orders toca durante micro-eventos tóxicos / gaps adversos
    toxic_touch_share = 0.095
    toxic_vol_usd = annual_limit_vol_usd * toxic_touch_share

    # Capital protegido a clientes (caída adversa media evitada del 6.8% en eventos tóxicos)
    client_capital_protected_usd = toxic_vol_usd * 0.068

    # Resolución de objeción de volumen:
    # - Modos pro-volumen (Tramos / Suelo Dinámico) preservan ~91% del volumen tóxico mediante re-entrada segura
    # - Modos de pausa/invalidación preservan ~38% por re-colocación consciente posterior
    pro_share = req.pro_volume_mode_adoption_pct / 100.0
    toxic_volume_retained_ratio = pro_share * 0.91 + (1.0 - pro_share) * 0.38
    overall_volume_preserved_pct = round(((1.0 - toxic_touch_share) + toxic_touch_share * toxic_volume_retained_ratio) * 100.0, 1)

    fee_drag_if_cancel_only_usd = annual_limit_rev_usd * toxic_touch_share * 0.62
    fee_recovered_by_pro_modes_usd = annual_limit_rev_usd * toxic_touch_share * (toxic_volume_retained_ratio - 0.38)

    # Reducción de Churn y Retención de LTV (clientes que no abandonan tras sufrir pérdidas súbitas)
    avg_annual_rev_per_account = max(35.0, (annual_limit_rev_usd / max(1, req.active_retail_accounts)) * 2.4)
    accounts_saved_from_blowup = int(req.active_retail_accounts * 0.022)
    retained_ltv_revenue_usd = accounts_saved_from_blowup * avg_annual_rev_per_account * 1.65

    # Ahorro en Margin Bad-Debt / Liquidaciones negativas
    margin_bad_debt_avoided_usd = client_capital_protected_usd * (req.margin_account_share_pct / 100.0) * 0.045

    # Upsell Tier Premium White-Label (ej. suscripción tipo Robinhood Gold / Ultra)
    premium_subscribers = int(req.active_retail_accounts * (req.premium_tier_adoption_pct / 100.0))
    premium_arr_usd = premium_subscribers * req.premium_monthly_price_usd * 12.0

    # Impacto neto total anual en P&L del bróker
    residual_cancel_fee_cost_usd = annual_limit_rev_usd * toxic_touch_share * (1.0 - toxic_volume_retained_ratio)
    total_broker_net_impact_usd = (
        retained_ltv_revenue_usd
        + margin_bad_debt_avoided_usd
        + premium_arr_usd
        - residual_cancel_fee_cost_usd
    )

    return {
        "inputs": req.model_dump(),
        "kpis": {
            "client_capital_protected_musd": round(client_capital_protected_usd / 1_000_000.0, 2),
            "overall_volume_preserved_pct": overall_volume_preserved_pct,
            "fee_recovered_by_pro_modes_kusd": round(fee_recovered_by_pro_modes_usd / 1_000.0, 1),
            "residual_cancel_fee_cost_kusd": round(residual_cancel_fee_cost_usd / 1_000.0, 1),
            "accounts_retained_yearly": accounts_saved_from_blowup,
            "retained_ltv_revenue_musd": round(retained_ltv_revenue_usd / 1_000_000.0, 2),
            "margin_bad_debt_avoided_kusd": round(margin_bad_debt_avoided_usd / 1_000.0, 1),
            "premium_subscribers": premium_subscribers,
            "premium_arr_musd": round(premium_arr_usd / 1_000_000.0, 2),
            "total_broker_net_impact_musd": round(total_broker_net_impact_usd / 1_000_000.0, 2),
            "fee_drag_avoided_vs_naive_cancel_kusd": round(fee_drag_if_cancel_only_usd / 1_000.0, 1),
        },
        "executive_thesis": (
            f"Con un {req.pro_volume_mode_adoption_pct:.0f}% de uso en modos pro-volumen (Tramos Adaptativos y Suelo Dinámico), "
            f"el bróker preserva el {overall_volume_preserved_pct}% de su volumen total en órdenes límite, "
            f"protege ${round(client_capital_protected_usd / 1_000_000.0, 1)}M/año de capital de clientes y genera "
            f"+${round(total_broker_net_impact_usd / 1_000_000.0, 2)}M/año de impacto neto en P&L entre retención de LTV, "
            f"ahorro en bad-debt de margen y monetización de tier Premium."
        ),
    }


@app.get("/api/v1/b2b/audit-log", tags=["5. Integración OMS Bróker (Producción)"])
async def get_compliance_audit_log():
    """Devuelve los últimos recibos criptográficos deterministas SHA-256 (MiFID II / Best Execution)."""
    return {"count": len(AUDIT_RECEIPTS_LOG), "receipts": AUDIT_RECEIPTS_LOG}


@app.get("/api/v1/portfolio", tags=["6. Cartera & Perfil del Inversor"])
async def get_portfolio():
    return {"portfolio": USER_PORTFOLIO.model_dump()}


@app.put("/api/v1/portfolio", tags=["6. Cartera & Perfil del Inversor"])
async def update_portfolio(portfolio: PortfolioState):
    global USER_PORTFOLIO
    USER_PORTFOLIO = portfolio
    return {"portfolio": USER_PORTFOLIO.model_dump()}


@app.post("/api/v1/portfolio/impact", tags=["6. Cartera & Perfil del Inversor"])
async def compute_pre_trade_portfolio_impact(req: PreTradeImpactRequest):
    if req.portfolio is None:
        req.portfolio = USER_PORTFOLIO
    return evaluate_pre_trade_impact(req)


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
