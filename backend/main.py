"""
Servidor API B2B para Brókers (Estilo Interactive Brokers Client Portal + ShieldOrder Plugin).
Soporta:
- Cotizaciones y gráficos en tiempo real de cualquier acción (Yahoo Finance)
- Casos históricos reales de desplomes bursátiles (ASML, NVDA, INTC, SMCI, AAPL)
- Simulador / Backtester instantáneo comparando Limit Order estándar vs ShieldOrder Smart Limit
- Monitor de Órdenes Activas en vivo (Paper Trading)
- Copiloto y Notificaciones con Google Gemini (google-genai)
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
    title="ShieldOrder B2B · Interactive Brokers Integration Demo & OMS API",
    version="2.0.0",
    description=(
        "Infraestructura B2B integrable en el Order Ticket de brókers (estilo Interactive Brokers) "
        "para proteger órdenes límite mediante métricas de velocidad, volumen relativo, ATR, RSI y divergencia."
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

# Almacén en memoria de Órdenes Activas (Paper Live Monitor) y Cartera del Inversor
ACTIVE_ORDERS: List[Dict[str, Any]] = []
USER_PORTFOLIO: PortfolioState = get_default_portfolio()


class SimulationAPIRequest(BaseModel):
    symbol: str = "ASML"
    timeframe: str = "5m"
    case_id: Optional[str] = None
    limit_price: float = 0.0
    quantity: float = 10.0
    side: str = "BUY"
    time_in_force: str = "Day"
    protection_enabled: bool = True
    protection_mode: str = "INVALIDATE"
    preset: str = "MODERATE"
    rules: RiskRules = Field(default_factory=RiskRules)
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
    protection_mode: str = "INVALIDATE"
    preset: str = "MODERATE"
    rules: RiskRules = Field(default_factory=RiskRules)


class CopilotAPIRequest(BaseModel):
    prompt: str
    default_limit_price: float = 90.0
    gemini_api_key: Optional[str] = None


class BrokerLiveCheckRequest(BaseModel):
    broker_order_id: str = "IBKR-994812"
    symbol: str = "ASML"
    limit_price: float = 1280.0
    candle_interval_minutes: int = 5
    preset: str = "MODERATE"
    protection_mode: str = "INVALIDATE"
    recent_candles: List[Candle]


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

    # Actualizar vigilancia en tiempo real de órdenes activas de este ticker
    last_price = bundle["quote"]["last_price"]
    tox_score = bundle["live_toxicity"]["total_toxicity_score"]
    for order in ACTIVE_ORDERS:
        if order["symbol"] == bundle["symbol"] and order["status"] in ("WORKING_MONITORING", "ARMED_SHIELD"):
            order["last_seen_price"] = last_price
            order["current_toxicity"] = tox_score
            dist_pct = round(((last_price - order["limit_price"]) / order["limit_price"]) * 100.0, 2)
            order["distance_pct"] = dist_pct
            threshold = order["rules"]["toxicity_threshold"]
            if last_price <= order["limit_price"] and order["side"] == "BUY":
                if order["protection_enabled"] and tox_score >= threshold:
                    order["status"] = "BLOCKED_BY_SHIELD"
                    order["status_note"] = f"Bloqueada en vivo a ${last_price} (Toxicidad {tox_score}/100)"
                else:
                    order["status"] = "FILLED_LIVE"
                    order["status_note"] = f"Ejecutada en vivo a ${last_price} (Flujo sano {tox_score}/100)"
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

    sim_req = SmartOrderRequest(
        symbol=bundle["symbol"],
        scenario_id=req.case_id or "live_market",
        candle_interval_minutes=bundle["interval_minutes"],
        side="SELL" if req.side.upper() == "SELL" else "BUY",
        limit_price=effective_limit,
        quantity=req.quantity,
        time_in_force=req.time_in_force,
        protection_enabled=req.protection_enabled,
        protection_mode=req.protection_mode,
        preset=req.preset,
        rules=req.rules,
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

    return {
        "symbol": bundle["symbol"],
        "timeframe": bundle["timeframe"],
        "data_source": bundle["source"],
        "case_info": bundle["case_info"],
        "quote": bundle["quote"],
        "suggested_limit_price": bundle["suggested_limit_price"],
        "effective_limit_price": effective_limit,
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

    rules = get_preset_rules(req.preset, RiskRules())
    metrics = evaluate_toxicity_at_index(
        candles=req.recent_candles,
        idx=len(req.recent_candles) - 1,
        eval_price=req.limit_price,
        interval_minutes=req.candle_interval_minutes,
        rules=rules,
    )
    is_dangerous = metrics.total_toxicity_score >= rules.toxicity_threshold or len(metrics.triggered_flags) >= 2
    action = "ROUTE_TO_EXCHANGE" if not is_dangerous else req.protection_mode

    return {
        "broker_order_id": req.broker_order_id,
        "symbol": req.symbol,
        "oms_action": action,
        "toxicity_score": metrics.total_toxicity_score,
        "threshold": rules.toxicity_threshold,
        "metrics": metrics.model_dump(),
    }


@app.get("/api/v1/portfolio", tags=["6. Cartera & Perfil del Inversor"])
async def get_portfolio():
    """Devuelve la cartera actual del usuario (8 acciones + cash) y su perfil de inversor."""
    return {"portfolio": USER_PORTFOLIO.model_dump()}


@app.put("/api/v1/portfolio", tags=["6. Cartera & Perfil del Inversor"])
async def update_portfolio(portfolio: PortfolioState):
    """Guarda los cambios en las posiciones, liquidez o perfil del inversor."""
    global USER_PORTFOLIO
    USER_PORTFOLIO = portfolio
    return {"portfolio": USER_PORTFOLIO.model_dump()}


@app.post("/api/v1/portfolio/impact", tags=["6. Cartera & Perfil del Inversor"])
async def compute_pre_trade_portfolio_impact(req: PreTradeImpactRequest):
    """
    Simula el impacto Pre-Trade en la cartera del inversor si su Smart Limit Order se ejecuta:
    nuevos pesos ponderados, rentabilidad esperada, volatilidad Markowitz, Beta, Sharpe, VaR 95%,
    encaje con el perfil (Fit Score 0-100) y tamaño óptimo sugerido (Smart Sizing).
    """
    if req.portfolio is None:
        req.portfolio = USER_PORTFOLIO
    return evaluate_pre_trade_impact(req)


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
