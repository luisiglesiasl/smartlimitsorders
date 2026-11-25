"""
Proveedor de Datos Reales de Mercado (Yahoo Finance & Binance) para el Portal Estilo Interactive Brokers:
1. Cotizaciones y gráficos en tiempo real de cualquier acción o ETF mundial (ASML, NVDA, TSLA, AAPL, etc.)
   en múltiples temporalidades (1m, 5m, 15m, 1h, 1d) + índice de referencia real (QQQ / BTC-USD).
2. Casos históricos 100% REALES de desplomes bursátiles famosos (ASML Oct 2024, NVDA DeepSeek Ene 2025,
   INTC Ago 2024, SMCI Oct 2024 y corrección sana real de AAPL) descargados directamente de Yahoo Finance.
3. Buscador de tickers global para la barra de búsqueda superior estilo IBKR.
"""

from __future__ import annotations
import math
from typing import List, Dict, Any, Optional
import httpx
from engine import Candle, RiskRules, evaluate_toxicity_at_index

YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

TIMEFRAME_CONFIG: Dict[str, Dict[str, Any]] = {
    "1m": {"interval": "1m", "range": "5d", "minutes": 1, "label": "1m"},
    "5m": {"interval": "5m", "range": "5d", "minutes": 5, "label": "5m"},
    "15m": {"interval": "15m", "range": "1mo", "minutes": 15, "label": "15m"},
    "1h": {"interval": "60m", "range": "3mo", "minutes": 60, "label": "1H"},
    "1d": {"interval": "1d", "range": "1y", "minutes": 390, "label": "1D"},
}

REAL_CRASH_CASES: List[Dict[str, Any]] = [
    {
        "id": "asml_oct2024",
        "symbol": "ASML",
        "title": "ASML · Desplome Resultados Q3 (-16% en 1 día)",
        "date_label": "Octubre 2024",
        "description": "Caso real: ASML publicó resultados por error antes de tiempo con pedidos muy por debajo de lo previsto. El precio se desplomó en vertical atravesando soportes con volumen récord.",
        "interval": "1d",
        "minutes": 390,
        "period1": 1724025600,  # 2024-08-19
        "period2": 1732924800,  # 2024-11-30
        "suggested_limit_price": 780.0,
        "recommended_mode": "INVALIDATE",
        "tag": "Crash por Noticia",
    },
    {
        "id": "nvda_deepseek",
        "symbol": "NVDA",
        "title": "NVDA · Crash DeepSeek (-17% en 1 sesión)",
        "date_label": "Enero 2025",
        "description": "Caso real: El 27 de enero de 2025 NVIDIA perdió casi $600B de capitalización en una sola sesión por la irrupción de DeepSeek. Prueba 'Invalidar' o 'Esperar Rebote'.",
        "interval": "1d",
        "minutes": 390,
        "period1": 1733011200,  # 2024-12-01
        "period2": 1742428800,  # 2025-03-20
        "suggested_limit_price": 130.0,
        "recommended_mode": "WAIT_FOR_BOUNCE",
        "tag": "Shock Sectorial IA",
    },
    {
        "id": "intc_aug2024",
        "symbol": "INTC",
        "title": "INTC · Colapso de Resultados (-26% en 1 día)",
        "date_label": "Agosto 2024",
        "description": "Caso real: Intel anunció despidos masivos y suspensión de dividendo. Las órdenes límite tradicionales compraron el desplome quedando atrapadas meses en pérdidas.",
        "interval": "1d",
        "minutes": 390,
        "period1": 1717200000,  # 2024-06-01
        "period2": 1727654400,  # 2024-09-30
        "suggested_limit_price": 27.5,
        "recommended_mode": "INVALIDATE",
        "tag": "Cuchillo Cayendo",
    },
    {
        "id": "smci_oct2024",
        "symbol": "SMCI",
        "title": "SMCI · Dimisión del Auditor (-33% en 1 día)",
        "date_label": "Octubre 2024",
        "description": "Caso real: Ernst & Young renunció como auditor de Super Micro Computer. Caída vertical con volumen explosivo donde cualquier orden límite ciega sufrió pérdidas severas.",
        "interval": "1d",
        "minutes": 390,
        "period1": 1723420800,  # 2024-08-12
        "period2": 1733788800,  # 2024-12-10
        "suggested_limit_price": 42.0,
        "recommended_mode": "INVALIDATE",
        "tag": "Riesgo Corporativo",
    },
    {
        "id": "aapl_healthy",
        "symbol": "AAPL",
        "title": "AAPL · Corrección Sana Real (Sí Ejecuta)",
        "date_label": "Otoño 2024",
        "description": "Caso real de control: Retroceso técnico ordenado de Apple acompañado del mercado general, sin volumen tóxico. Demuestra que ShieldOrder aprueba y ejecuta buenas compras.",
        "interval": "1d",
        "minutes": 390,
        "period1": 1726444800,  # 2024-09-16
        "period2": 1734652800,  # 2024-12-20
        "suggested_limit_price": 223.5,
        "recommended_mode": "INVALIDATE",
        "tag": "Caída Sana",
    },
]

POPULAR_TICKERS = [
    {"symbol": "ASML", "name": "ASML Holding N.V.", "exchange": "NASDAQ"},
    {"symbol": "NVDA", "name": "NVIDIA Corporation", "exchange": "NASDAQ"},
    {"symbol": "TSLA", "name": "Tesla, Inc.", "exchange": "NASDAQ"},
    {"symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ"},
    {"symbol": "MSFT", "name": "Microsoft Corporation", "exchange": "NASDAQ"},
    {"symbol": "META", "name": "Meta Platforms, Inc.", "exchange": "NASDAQ"},
    {"symbol": "AMZN", "name": "Amazon.com, Inc.", "exchange": "NASDAQ"},
    {"symbol": "AMD", "name": "Advanced Micro Devices", "exchange": "NASDAQ"},
    {"symbol": "INTC", "name": "Intel Corporation", "exchange": "NASDAQ"},
    {"symbol": "SMCI", "name": "Super Micro Computer", "exchange": "NASDAQ"},
    {"symbol": "BTC-USD", "name": "Bitcoin USD", "exchange": "CRYPTO"},
    {"symbol": "ETH-USD", "name": "Ethereum USD", "exchange": "CRYPTO"},
]


def _extract_candles_from_yahoo(data: Dict[str, Any], bench_map: Dict[int, float]) -> List[Candle]:
    timestamps = data.get("timestamp") or []
    quote = data["indicators"]["quote"][0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    vols = quote.get("volume") or []

    candles: List[Candle] = []
    seen_ts: Dict[int, int] = {}
    last_bench: Optional[float] = None
    for i, ts in enumerate(timestamps):
        o = opens[i] if i < len(opens) else None
        h = highs[i] if i < len(highs) else None
        l = lows[i] if i < len(lows) else None
        c = closes[i] if i < len(closes) else None
        v = vols[i] if i < len(vols) else 0
        if None in (o, h, l, c):
            continue
        ts_int = int(ts)
        # Emparejar benchmark por timestamp exacto o día (para velas diarias)
        b_val = bench_map.get(ts_int) or bench_map.get(ts_int // 86400) or last_bench
        if b_val is not None:
            last_bench = b_val
        candle_obj = Candle(
            timestamp=ts_int,
            open=round(float(o), 2),
            high=round(float(h), 2),
            low=round(float(l), 2),
            close=round(float(c), 2),
            volume=float(v or 0),
            benchmark_close=b_val,
        )
        # Deduplicar timestamps idénticos (evita que TradingView LightweightCharts rechace los datos)
        if ts_int in seen_ts:
            candles[seen_ts[ts_int]] = candle_obj
        else:
            seen_ts[ts_int] = len(candles)
            candles.append(candle_obj)

    candles.sort(key=lambda item: item.timestamp)
    return candles


def _build_quote_header(symbol: str, meta: Dict[str, Any], candles: List[Candle], is_historical: bool = False) -> Dict[str, Any]:
    last_price = float(meta.get("regularMarketPrice") or (candles[-1].close if candles else 0.0))
    if is_historical and candles:
        last_price = candles[-1].close
        prev_close = candles[-2].close if len(candles) >= 2 else candles[0].open
    else:
        prev_close = float(meta.get("chartPreviousClose") or meta.get("previousClose") or (candles[0].open if candles else last_price))

    change = round(last_price - prev_close, 2)
    change_pct = round((change / prev_close) * 100.0, 2) if prev_close else 0.0

    # Spread realista Bid/Ask estilo Interactive Brokers
    half_spread = max(0.01, round(last_price * 0.0004, 2))
    bid = round(last_price - half_spread, 2)
    ask = round(last_price + half_spread, 2)

    exch_raw = meta.get("fullExchangeName") or meta.get("exchangeName") or "NASDAQ"
    if exch_raw in ("NMS", "NGM", "NCM"):
        exch_raw = "NASDAQ"
    elif exch_raw == "NYQ":
        exch_raw = "NYSE"

    return {
        "symbol": symbol.upper(),
        "short_name": meta.get("longName") or meta.get("shortName") or symbol.upper(),
        "exchange": exch_raw,
        "currency": meta.get("currency") or "USD",
        "last_price": round(last_price, 2),
        "previous_close": round(prev_close, 2),
        "change": change,
        "change_pct": change_pct,
        "bid": bid,
        "ask": ask,
        "bid_size": 17,
        "ask_size": 24,
        "day_volume": int(meta.get("regularMarketVolume") or (candles[-1].volume if candles else 0)),
        "day_high": round(float(meta.get("regularMarketDayHigh") or (max(c.high for c in candles[-20:]) if candles else last_price)), 2),
        "day_low": round(float(meta.get("regularMarketDayLow") or (min(c.low for c in candles[-20:]) if candles else last_price)), 2),
        "fifty_two_week_high": round(float(meta.get("fiftyTwoWeekHigh") or 0.0), 2),
        "fifty_two_week_low": round(float(meta.get("fiftyTwoWeekLow") or 0.0), 2),
        "is_historical": is_historical,
    }


async def fetch_real_market_data(
    symbol: str = "ASML",
    timeframe: str = "5m",
    case_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Descarga datos reales de Yahoo Finance para cualquier ticker en tiempo real
    o para uno de los casos históricos reales curados.
    """
    case_cfg = next((c for c in REAL_CRASH_CASES if c["id"] == case_id), None) if case_id else None

    if case_cfg:
        clean_sym = case_cfg["symbol"]
        interval = case_cfg["interval"]
        interval_minutes = case_cfg["minutes"]
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{clean_sym}"
            f"?interval={interval}&period1={case_cfg['period1']}&period2={case_cfg['period2']}"
        )
        bench_sym = "QQQ"
        bench_url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{bench_sym}"
            f"?interval={interval}&period1={case_cfg['period1']}&period2={case_cfg['period2']}"
        )
        source_label = f"Yahoo Finance · Caso Histórico Real ({case_cfg['date_label']})"
    else:
        clean_sym = symbol.strip().upper()
        tf = TIMEFRAME_CONFIG.get(timeframe, TIMEFRAME_CONFIG["5m"])
        interval = tf["interval"]
        interval_minutes = tf["minutes"]
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{clean_sym}?interval={interval}&range={tf['range']}"
        bench_sym = "BTC-USD" if clean_sym.endswith("-USD") and clean_sym != "BTC-USD" else "QQQ"
        bench_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{bench_sym}?interval={interval}&range={tf['range']}"
        source_label = f"Yahoo Finance Real-Time ({tf['label']} · Benchmark {bench_sym})"

    async with httpx.AsyncClient(timeout=10.0, headers=YAHOO_HEADERS) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        payload = resp.json()
        results = payload.get("chart", {}).get("result")
        if not results:
            raise ValueError(f"No se encontraron datos de mercado para el ticker '{clean_sym}'.")
        main_res = results[0]
        meta = main_res.get("meta", {})

        # Benchmark para divergencia real
        bench_map: Dict[int, float] = {}
        try:
            b_resp = await client.get(bench_url)
            if b_resp.status_code == 200:
                b_results = b_resp.json().get("chart", {}).get("result")
                if b_results:
                    b_res = b_results[0]
                    b_ts = b_res.get("timestamp") or []
                    b_closes = b_res["indicators"]["quote"][0].get("close") or []
                    for t, bc in zip(b_ts, b_closes):
                        if bc is not None:
                            bench_map[int(t)] = float(bc)
                            bench_map[int(t) // 86400] = float(bc)
        except Exception:
            pass

    candles = _extract_candles_from_yahoo(main_res, bench_map)
    if not case_cfg and len(candles) > 180:
        candles = candles[-180:]

    if len(candles) < 5:
        raise ValueError(f"Datos insuficientes para {clean_sym} en temporalidad {interval}.")

    quote = _build_quote_header(clean_sym, meta, candles, is_historical=bool(case_cfg))

    if case_cfg:
        suggested_limit = case_cfg["suggested_limit_price"]
    else:
        # Por defecto sugerimos una limit order ~1.5% debajo del último precio (o dentro del rango reciente para backtest)
        recent_slice = candles[max(5, len(candles) // 3):]
        hi = max(c.high for c in recent_slice)
        lo = min(c.low for c in recent_slice)
        suggested_limit = round(lo + (hi - lo) * 0.42, 2)

    # Calcular telemetría de toxicidad actual en tiempo real sobre la última vela
    live_metrics = evaluate_toxicity_at_index(
        candles=candles,
        idx=len(candles) - 1,
        eval_price=candles[-1].close,
        interval_minutes=interval_minutes,
        rules=RiskRules(),
    )

    return {
        "symbol": clean_sym,
        "timeframe": interval,
        "interval_minutes": interval_minutes,
        "source": source_label,
        "case_info": case_cfg,
        "quote": quote,
        "candles": candles,
        "suggested_limit_price": suggested_limit,
        "live_toxicity": live_metrics.model_dump(),
    }


async def search_tickers(query: str) -> List[Dict[str, str]]:
    q = query.strip()
    if not q:
        return POPULAR_TICKERS[:8]
    q_up = q.upper()
    local_matches = [
        t for t in POPULAR_TICKERS if q_up in t["symbol"] or q_up in t["name"].upper()
    ]
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={q}&quotesCount=8&newsCount=0"
        async with httpx.AsyncClient(timeout=4.0, headers=YAHOO_HEADERS) as client:
            r = await client.get(url)
            if r.status_code == 200:
                quotes = r.json().get("quotes", [])
                remote = []
                seen = set()
                for item in quotes:
                    sym = item.get("symbol")
                    if not sym or sym in seen:
                        continue
                    qtype = item.get("quoteType", "")
                    if qtype not in ("EQUITY", "ETF", "CRYPTOCURRENCY", "INDEX"):
                        continue
                    seen.add(sym)
                    remote.append({
                        "symbol": sym,
                        "name": item.get("shortname") or item.get("longname") or sym,
                        "exchange": item.get("exchDisp") or item.get("exchange") or "US",
                    })
                if remote:
                    return remote[:8]
    except Exception:
        pass
    return local_matches[:8]
