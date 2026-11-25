"""
Módulo de Inteligencia Artificial con Google Gemini (google-genai SDK) v4.0.
Cumple dos funciones B2B seguras (fuera de la ruta crítica de milisegundos):
1. Copiloto de Órdenes: Traduce instrucciones en lenguaje natural a reglas matemáticas auditables
   (incluyendo Tramos Adaptativos, Suelo Dinámico y Gap Guard).
2. Explicador Post-Evento: Genera la notificación auditable para el cliente final del bróker.
"""

from __future__ import annotations
import json
import os
import re
from typing import Optional, Dict, Any
from pydantic import BaseModel

try:
    from google import genai
    from google.genai import types
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False


class CopilotParsedRules(BaseModel):
    limit_price: Optional[float] = None
    protection_mode: str = "SMART_TRANCHES"
    lookback_minutes: int = 60
    max_drop_pct: float = 4.0
    max_rvol: float = 2.5
    min_rsi: float = 28.0
    max_atr_multiple: float = 2.5
    max_benchmark_divergence_pct: float = 3.5
    max_gap_pct: float = 3.5
    toxicity_threshold: float = 60.0
    assistant_summary: str


def _resolve_api_key(provided_key: Optional[str]) -> Optional[str]:
    if provided_key and provided_key.strip():
        return provided_key.strip()
    env_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    return env_key.strip() if env_key else None


def parse_natural_language_order(
    prompt: str,
    default_limit_price: float = 90.0,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    key = _resolve_api_key(api_key)
    if HAS_GENAI and key:
        try:
            client = genai.Client(api_key=key)
            sys_instruction = (
                "Eres un asistente cuantitativo integrado en un bróker institucional. Convierte la petición del inversor "
                "en parámetros exactos para una Smart Limit Order. "
                "protection_mode solo puede ser 'INVALIDATE' (cancelar si cae brusco), "
                "'SMART_TRANCHES' (dividir en 3 tramos escalonados adaptativos), "
                "'TRAILING_FLOOR' (perseguir la caída con suelo dinámico hasta giro), "
                "'WAIT_FOR_BOUNCE' (esperar rebote) o 'REQUIRE_CONFIRMATION' (pedir confirmación al móvil). "
                "Responde en español sobrio y claro en assistant_summary sin emojis."
            )
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=f"Precio límite actual sugerido: {default_limit_price}. Petición del usuario: {prompt}",
                config=types.GenerateContentConfig(
                    system_instruction=sys_instruction,
                    response_mime_type="application/json",
                    response_schema=CopilotParsedRules,
                    temperature=0.1,
                ),
            )
            parsed = json.loads(response.text)
            if not parsed.get("limit_price"):
                parsed["limit_price"] = default_limit_price
            parsed["engine_used"] = "Google Gemini (gemini-2.5-flash)"
            return parsed
        except Exception as exc:
            fallback = _heuristic_parse(prompt, default_limit_price)
            fallback["engine_used"] = f"Modo Local (Aviso Gemini API: {str(exc)[:80]})"
            return fallback

    res = _heuristic_parse(prompt, default_limit_price)
    res["engine_used"] = "Intérprete Semántico Local (Conecta Gemini API Key para activar Gemini 2.5 Flash)"
    return res


def _heuristic_parse(prompt: str, default_limit_price: float) -> Dict[str, Any]:
    text = prompt.lower()

    price_match = re.search(r"(?:a|en|precio|l[ií]mite)\s*\$?\s*(\d+(?:[.,]\d+)?)", text)
    limit_price = float(price_match.group(1).replace(",", ".")) if price_match else default_limit_price

    pct_match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", text)
    max_drop_pct = float(pct_match.group(1).replace(",", ".")) if pct_match else 4.0

    lookback = 60
    min_match = re.search(r"(\d+)\s*min", text)
    hr_match = re.search(r"(\d+)\s*hora", text)
    if min_match:
        lookback = max(5, min(1440, int(min_match.group(1))))
    elif hr_match:
        lookback = max(15, min(1440, int(hr_match.group(1)) * 60))
    elif "media hora" in text:
        lookback = 30
    elif "una hora" in text:
        lookback = 60

    if any(w in text for w in ["tramo", "escalona", "dca", "partes", "divid"]):
        mode = "SMART_TRANCHES"
        mode_desc = "ejecutar en 3 tramos escalonados adaptativos (25% / 35% / 40%)"
    elif any(w in text for w in ["suelo", "trailing", "perseguir", "dinámico", "dinamico"]):
        mode = "TRAILING_FLOOR"
        mode_desc = "activar Suelo Dinámico persiguiendo la caída hasta confirmar giro"
    elif any(w in text for w in ["rebote", "giro", "espera", "bounce"]):
        mode = "WAIT_FOR_BOUNCE"
        mode_desc = "congelar la caída y comprar solo tras confirmar rebote"
    elif any(w in text for w in ["avisa", "notifica", "confirma", "móvil", "movil", "push", "pregunta"]):
        mode = "REQUIRE_CONFIRMATION"
        mode_desc = "pausar la orden y solicitar confirmación al móvil"
    else:
        mode = "INVALIDATE"
        mode_desc = "invalidar automáticamente la orden para proteger tu liquidez"

    max_rvol = 2.0 if any(w in text for w in ["estricto", "conservador", "mucho cuidado", "poco volumen"]) else 2.5
    min_rsi = 32.0 if "conservador" in text else 28.0
    threshold = 50.0 if "conservador" in text else 60.0

    return {
        "limit_price": limit_price,
        "protection_mode": mode,
        "lookback_minutes": lookback,
        "max_drop_pct": max_drop_pct,
        "max_rvol": max_rvol,
        "min_rsi": min_rsi,
        "max_atr_multiple": 2.5,
        "max_benchmark_divergence_pct": round(max(2.0, max_drop_pct * 0.8), 1),
        "max_gap_pct": round(max(2.0, max_drop_pct * 0.85), 1),
        "toxicity_threshold": threshold,
        "assistant_summary": (
            f"Configurada Smart Limit a ${limit_price:.2f}: si el movimiento supera {max_drop_pct:.1f}% "
            f"en {lookback} min o RVOL >= {max_rvol:.1f}x, el motor va a {mode_desc}."
        ),
    }


def generate_audit_explanation(
    symbol: str,
    sim_result: Dict[str, Any],
    api_key: Optional[str] = None,
) -> Dict[str, str]:
    if not sim_result.get("touched"):
        return {
            "headline": "Orden en vigilancia activa",
            "body": sim_result.get("message", "El precio aún no ha alcanzado el nivel límite."),
            "generated_by": "Motor Determinista",
        }

    smart = sim_result["smart_order"]
    std = sim_result["standard_order"]
    m = sim_result["metrics_at_touch"]
    status = smart["status"]

    key = _resolve_api_key(api_key)
    if HAS_GENAI and key:
        try:
            client = genai.Client(api_key=key)
            prompt = (
                f"Redacta una notificación institucional breve (máximo 3 frases, sin emojis) "
                f"para un cliente de bróker sobre su Smart Limit Order en {symbol}. "
                f"Decisión: {status} ({smart['status_label']}). "
                f"Precio límite: ${std['fill_price']}. "
                f"Métricas en toque: variación {m['drop_pct_in_window']}%, RVOL {m['rvol']}x, "
                f"RSI {m['rsi_14']}, Toxicidad {m['total_toxicity_score']}/100. "
                f"Limit estándar: ${std['final_pnl_usd']} ({std['final_pnl_pct']}%); "
                f"Smart Limit: ${smart['final_pnl_usd']} (ahorro/mejora: ${smart['capital_saved_usd']})."
            )
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.2),
            )
            return {
                "headline": f"Smart Limit · {smart['status_label']}",
                "body": resp.text.strip(),
                "generated_by": "Google Gemini (gemini-2.5-flash)",
            }
        except Exception:
            pass

    if status == "EXECUTED_SAFE":
        body = (
            f"Tu orden en {symbol} a ${smart['fill_price']:.2f} se ejecutó con normalidad. "
            f"El motor verificó un movimiento ordenado ({m['drop_pct_in_window']}% en ventana, RVOL {m['rvol']}x, "
            f"absorción {int(m.get('absorption_ratio', 0)*100)}%) con toxicidad baja de {m['total_toxicity_score']}/100."
        )
    elif status == "EXECUTED_SMART_TRANCHES":
        n_tr = len(smart.get("tranches", []))
        vol_pct = smart.get("broker_volume_preserved_pct", 100.0)
        body = (
            f"Al detectar toxicidad ({m['total_toxicity_score']}/100) en ${std['fill_price']:.2f}, "
            f"ShieldOrder evitó la entrada ciega y ejecutó {n_tr}/3 tramos adaptativos a un precio medio de ${smart['fill_price']:.2f} "
            f"(mejora de {smart['value_added_pct']:+.2f}% frente a una Limit estándar, preservando el {vol_pct:.0f}% del volumen operado)."
        )
    elif status == "EXECUTED_TRAILING_FLOOR":
        body = (
            f"Detectada caída tóxica en ${std['fill_price']:.2f} (Toxicidad {m['total_toxicity_score']}/100). "
            f"El Suelo Dinámico acompañó el desplome sin ejecutar hasta confirmar giro, logrando entrada en ${smart['fill_price']:.2f} "
            f"({smart['value_added_pct']:+.2f}% mejor que una orden límite tradicional, 100% volumen preservado)."
        )
    elif status == "INVALIDATED_PROTECTED":
        body = (
            f"Liquidez protegida: orden en {symbol} a ${std['fill_price']:.2f} invalidada al detectarse "
            f"variación brusca del {m['drop_pct_in_window']}% con RVOL {m['rvol']}x (Toxicidad {m['total_toxicity_score']}/100). "
            f"Una orden límite tradicional habría entrado en pérdidas inmediatas (ahorro estimado: ${smart['capital_saved_usd']:.2f})."
        )
    elif status == "PAUSED_CONFIRMATION":
        body = (
            f"{symbol} alcanzó ${std['fill_price']:.2f} con velocidad anómala ({m['drop_pct_in_window']}%, RVOL {m['rvol']}x, "
            f"Toxicidad {m['total_toxicity_score']}/100). Orden pausada a la espera de confirmación explícita del titular."
        )
    elif status == "EXECUTED_ON_BOUNCE":
        body = (
            f"Orden retenida durante la fase de pánico en ${std['fill_price']:.2f} (Toxicidad {m['total_toxicity_score']}/100) "
            f"y ejecutada tras confirmación de suelo a ${smart['fill_price']:.2f} (mejora de +{smart['value_added_pct']:.2f}%)."
        )
    else:
        body = (
            f"Orden congelada al detectarse caída severa en ${std['fill_price']:.2f} "
            f"(Toxicidad {m['total_toxicity_score']}/100). Expiró sin estabilización segura, evitando una pérdida de ${smart['capital_saved_usd']:.2f}."
        )

    return {
        "headline": f"Smart Limit · {smart['status_label']}",
        "body": body,
        "generated_by": "Motor Auditable MiFID II · ShieldOrder B2B",
    }
