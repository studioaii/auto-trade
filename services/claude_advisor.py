"""
Claude AI trade advisor — second opinion before every entry.
Sends live indicators + last 5 candles to Claude Haiku and asks ENTER or SKIP.
Falls back gracefully (approve trade) on any API error or timeout.
"""
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        from anthropic import Anthropic
        _client = Anthropic()   # reads ANTHROPIC_API_KEY from env automatically
    return _client


def get_trade_advice(
    signal: str,
    reason: str,
    indicators: dict,
    candles: list,
    nifty_spot: float,
    time_str: str,
) -> tuple[bool, str, int]:
    """
    Ask Claude whether to enter this trade.

    Returns:
        (should_enter, reasoning, confidence_1_to_10)

    On any failure returns (True, "Claude unavailable — rule-based signal", 0)
    so the rule-based engine always has a fallback.
    """
    last_5 = candles[-5:] if len(candles) >= 5 else candles
    candle_data = [
        {
            "time":   c.timestamp.strftime("%H:%M"),
            "open":   round(c.open, 2),
            "high":   round(c.high, 2),
            "low":    round(c.low, 2),
            "close":  round(c.close, 2),
            "volume": c.volume,
        }
        for c in last_5
    ]

    vwap    = indicators.get("vwap", 0)
    ema20   = indicators.get("ema20", 0)
    rsi14   = indicators.get("rsi14", 0)
    mstate  = indicators.get("market_state", "UNKNOWN")
    vs_vwap = "ABOVE" if nifty_spot > vwap else "BELOW"
    gap     = abs(nifty_spot - vwap)
    direction = "BULLISH — buy CE (call)" if signal == "BUY_CE" else "BEARISH — buy PE (put)"

    prompt = f"""You are a quantitative intraday options analyst reviewing a Nifty 50 trade signal.

Signal: {signal} — {direction}
Time: {time_str} IST
Rule-engine trigger: {reason}

Indicators (5-min candles on Nifty 50 index):
  Nifty Spot : {nifty_spot:.2f}
  VWAP       : {vwap:.2f}  (spot is {vs_vwap} by {gap:.2f} pts)
  EMA(20)    : {ema20:.2f}
  RSI(14)    : {rsi14:.1f}
  Market     : {mstate}

Last {len(candle_data)} completed 5-min candles:
{json.dumps(candle_data, indent=2)}

Trade parameters: target +35%, hard stop-loss -20%, trailing stop activates at +20% profit (trails 10% below peak).

Assess setup quality and respond ONLY with valid JSON — no markdown, no extra text:
{{"decision": "ENTER" or "SKIP", "confidence": <integer 1-10>, "reasoning": "<max 15 words>"}}

Be selective. SKIP if: weak momentum, thin candle bodies, RSI diverging from price, \
time risk (after 1:30 PM IST), choppy/mixed last 3 candles, low volume on breakout candle, \
price too close to VWAP (<0.15%), or signal looks like a false breakout/overextension."""

    try:
        client = _get_client()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            timeout=8.0,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()

        # Strip markdown code fences if model wraps response
        if "```" in text:
            parts = text.split("```")
            text = parts[1] if len(parts) > 1 else parts[0]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        result = json.loads(text)
        decision   = str(result.get("decision", "ENTER")).upper().strip()
        confidence = max(1, min(10, int(result.get("confidence", 7))))
        reasoning  = str(result.get("reasoning", "")).strip()[:120]

        # Require confidence >= 6 for ENTER
        should_enter = (decision == "ENTER") and (confidence >= 6)

        logger.info(
            "Claude advisor | %s | confidence=%d/10 | %s",
            decision, confidence, reasoning
        )
        return should_enter, reasoning, confidence

    except Exception as e:
        logger.warning("Claude advisor unavailable: %s — falling back to rule-based signal", e)
        return True, "Claude unavailable — rule-based signal", 0
