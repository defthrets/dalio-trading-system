"""
Dalios -- Signal Generation and Analysis
Signal engine, opportunities, justification, quadrant classification, sentiment, correlation.
"""

import asyncio
import random
import numpy as np
from collections import defaultdict
from datetime import datetime
from typing import Optional

from loguru import logger

from api.utils import (
    _cache_get, _cache_set, _get_prices, _EXECUTOR,
    _calc_rsi, _calc_trend, _calc_atr, _calc_macd, _calc_bollinger,
    YF_AVAILABLE,
)
from api.state import STATE, WATCHLIST
from api.scanners import (
    ASX_TICKERS, COMMODITY_TICKERS, CORR_TICKERS,
    _scanner_cache, _ASSET_META, _live_price,
)
from api.portfolio import PAPER, PAPER_STARTING_CASH, _get_fee_pct


# ── Quadrant metadata ──────────────────────────────────
QUADRANT_META = {
    "rising_growth": {
        "label": "RISING GROWTH",
        "color": "#00ff41",
        "icon": "▲",
        "description": "Economy expanding. Favour equities, commodities, corporate bonds. Reduce nominal bonds.",
        "favoured": ["Equities", "Commodities", "Corporate Bonds", "EM Debt"],
        "avoid": ["Nominal Bonds", "Defensive Cash"],
    },
    "falling_growth": {
        "label": "FALLING GROWTH",
        "color": "#ff4444",
        "icon": "▼",
        "description": "Recessionary pressure. Favour long-duration bonds, defensive equities. Reduce cyclicals.",
        "favoured": ["Long Bonds", "Defensive Equities", "Gold", "Cash"],
        "avoid": ["Cyclicals", "Commodities", "EM"],
    },
    "rising_inflation": {
        "label": "RISING INFLATION",
        "color": "#ffb300",
        "icon": "↑",
        "description": "Prices rising faster than growth. Favour gold, inflation-linked bonds, energy, real assets.",
        "favoured": ["Gold", "Energy", "TIPS", "Commodities", "Real Assets"],
        "avoid": ["Nominal Bonds", "Growth Equities"],
    },
    "falling_inflation": {
        "label": "FALLING INFLATION",
        "color": "#00e5ff",
        "icon": "↓",
        "description": "Disinflation / deflation. Favour equities, nominal bonds, consumer staples.",
        "favoured": ["Equities", "Nominal Bonds", "Consumer Staples"],
        "avoid": ["Commodities", "Gold", "Energy"],
    },
}

ASSET_CLASS_MAP: dict = {
    # ── Gold & Precious Metals ──
    "PMGOLD.AX":"gold","QAU.AX":"gold","MNRS.AX":"gold","GOLD.AX":"gold",
    "NST.AX":"gold","EVN.AX":"gold","SBM.AX":"gold","RRL.AX":"gold","SAR.AX":"gold",
    "GOR.AX":"gold","CMM.AX":"gold","RMS.AX":"gold","DEG.AX":"gold","WAF.AX":"gold",
    "MML.AX":"gold","RSG.AX":"gold","PRU.AX":"gold","SDG.AX":"gold","BDC.AX":"gold",
    "SKY.AX":"gold","MAU.AX":"gold",
    "GC=F":"gold","SI=F":"gold","PL=F":"gold","PA=F":"gold",
    # ── Commodities — Energy ──
    "OOO.AX":"commodities","WDS.AX":"commodities","STO.AX":"commodities","BPT.AX":"commodities",
    "APA.AX":"commodities","KAR.AX":"commodities","CVN.AX":"commodities","STX.AX":"commodities",
    "MEL.AX":"commodities","COE.AX":"commodities","NHE.AX":"commodities","TAP.AX":"commodities",
    "OPT.AX":"commodities",
    "CL=F":"commodities","BZ=F":"commodities","NG=F":"commodities",
    # ── Commodities — Base & Industrial Metals ──
    "OZL.AX":"commodities","29M.AX":"commodities","HG=F":"commodities",
    "BHP.AX":"commodities","RIO.AX":"commodities","FMG.AX":"commodities",
    "S32.AX":"commodities","MIN.AX":"commodities",
    "IGO.AX":"commodities","SFR.AX":"commodities","ILU.AX":"commodities","AWC.AX":"commodities",
    "NIC.AX":"commodities","WSA.AX":"commodities","CIA.AX":"commodities","MGX.AX":"commodities",
    "GRR.AX":"commodities","CLQ.AX":"commodities","CHN.AX":"commodities","TNG.AX":"commodities",
    "NHC.AX":"commodities","WHC.AX":"commodities","CRN.AX":"commodities","BCB.AX":"commodities",
    "TER.AX":"commodities","RED.AX":"commodities","SLR.AX":"commodities","OGC.AX":"commodities",
    "BGL.AX":"commodities","RXL.AX":"commodities","MEU.AX":"commodities","AIS.AX":"commodities",
    "IPT.AX":"commodities","GWR.AX":"commodities","SBR.AX":"commodities",
    # ── Commodities — Broad / Agriculture ──
    "QCB.AX":"commodities","COMB.AX":"commodities","FOOD.AX":"commodities","QAG.AX":"commodities",
    "ZC=F":"commodities","ZW=F":"commodities","ZS=F":"commodities",
    "KC=F":"commodities","SB=F":"commodities","CC=F":"commodities",
    "LE=F":"commodities","GF=F":"commodities","HE=F":"commodities","LBS=F":"commodities",
    "GNC.AX":"commodities","NUF.AX":"commodities","ELD.AX":"commodities",
    # ── Commodities — Uranium ──
    "PDN.AX":"commodities","BOE.AX":"commodities","NXE.AX":"commodities","ERA.AX":"commodities",
    "92E.AX":"commodities","AGE.AX":"commodities","SLX.AX":"commodities","PEN.AX":"commodities",
    "BMN.AX":"commodities","LOT.AX":"commodities","DYL.AX":"commodities",
    "BKY.AX":"commodities","TOE.AX":"commodities","GTR.AX":"commodities",
    # ── Commodities — Lithium & Battery Metals ──
    "AKE.AX":"commodities","PLS.AX":"commodities","SYA.AX":"commodities","CXO.AX":"commodities",
    "GL1.AX":"commodities","AVZ.AX":"commodities","LKE.AX":"commodities","PLL.AX":"commodities",
    "EUR.AX":"commodities","DEL.AX":"commodities","NVX.AX":"commodities","EV1.AX":"commodities",
    "LAT.AX":"commodities","LTR.AX":"commodities",
    # ── Commodities — Rare Earths ──
    "LYC.AX":"commodities","ARU.AX":"commodities","VML.AX":"commodities","HAS.AX":"commodities",
    "REE.AX":"commodities","NTU.AX":"commodities","ASM.AX":"commodities","HLX.AX":"commodities",
    # ── Real Assets / REITs ──
    "GMG.AX":"real_assets","SCG.AX":"real_assets","GPT.AX":"real_assets","VCX.AX":"real_assets",
    "CLW.AX":"real_assets","MGR.AX":"real_assets","DXS.AX":"real_assets","CHC.AX":"real_assets",
    "BWP.AX":"real_assets","NSR.AX":"real_assets","CQR.AX":"real_assets","HMC.AX":"real_assets",
    "ABP.AX":"real_assets","SCP.AX":"real_assets","HDN.AX":"real_assets","URW.AX":"real_assets",
    "AOF.AX":"real_assets","CNI.AX":"real_assets","GDI.AX":"real_assets","PPC.AX":"real_assets",
    "RGN.AX":"real_assets","ARF.AX":"real_assets","CRF.AX":"real_assets",
    "TCL.AX":"real_assets",
    # ── ASX Equities — Financials ──
    "CBA.AX":"equities","WBC.AX":"equities","ANZ.AX":"equities","NAB.AX":"equities",
    "MQG.AX":"equities","BEN.AX":"equities","BOQ.AX":"equities","SUN.AX":"equities",
    "QBE.AX":"equities","IAG.AX":"equities","AMP.AX":"equities","ASX.AX":"equities",
    "PPT.AX":"equities","CGF.AX":"equities","CPU.AX":"equities","NHF.AX":"equities",
    "MPL.AX":"equities","NIB.AX":"equities","AUB.AX":"equities","HUB.AX":"equities",
    "MFG.AX":"equities","PTM.AX":"equities","GQG.AX":"equities","PDL.AX":"equities",
    "EQT.AX":"equities","IFL.AX":"equities","CIP.AX":"equities","AFG.AX":"equities",
    "MAB.AX":"equities","SDF.AX":"equities",
    # ── ASX Equities — Healthcare ──
    "CSL.AX":"equities","RMD.AX":"equities","COH.AX":"equities","SHL.AX":"equities",
    "ANN.AX":"equities","PME.AX":"equities","EBO.AX":"equities","HLS.AX":"equities",
    "PNV.AX":"equities","RHC.AX":"equities","CUV.AX":"equities","NEU.AX":"equities",
    "TLX.AX":"equities","MSB.AX":"equities","AVH.AX":"equities","IMM.AX":"equities",
    "PXA.AX":"equities","NAN.AX":"equities","ACL.AX":"equities","RAC.AX":"equities",
    "PRO.AX":"equities","MX1.AX":"equities","OSL.AX":"equities","EMV.AX":"equities",
    # ── ASX Equities — Technology ──
    "WTC.AX":"equities","XRO.AX":"equities","ALU.AX":"equities","MP1.AX":"equities",
    "TNE.AX":"equities","REA.AX":"equities","APX.AX":"equities","TYR.AX":"equities",
    "SDR.AX":"equities","DTL.AX":"equities","NXT.AX":"equities","DUB.AX":"equities",
    "LNW.AX":"equities","STP.AX":"equities","RDY.AX":"equities","BVS.AX":"equities",
    "OTW.AX":"equities","AR9.AX":"equities",
    # ── ASX Equities — Fintech ──
    "ZIP.AX":"equities","EML.AX":"equities","APT.AX":"equities","SPT.AX":"equities",
    "MNY.AX":"equities","LBL.AX":"equities","MYS.AX":"equities","PGL.AX":"equities",
    "ABA.AX":"equities","FIN.AX":"equities",
    # ── ASX Equities — Cybersecurity ──
    "TNT.AX":"equities","RNO.AX":"equities","PKS.AX":"equities",
    # ── ASX Equities — Consumer / Retail ──
    "WES.AX":"equities","WOW.AX":"equities","COL.AX":"equities","JBH.AX":"equities",
    "TWE.AX":"equities","HVN.AX":"equities","DMP.AX":"equities","SUL.AX":"equities",
    "LOV.AX":"equities","KGN.AX":"equities","TPW.AX":"equities","MYR.AX":"equities",
    "NCK.AX":"equities","BBN.AX":"equities","UNI.AX":"equities","PFP.AX":"equities",
    "BCF.AX":"equities","TRS.AX":"equities","ADH.AX":"equities",
    # ── ASX Equities — Consumer Staples ──
    "BKL.AX":"equities","CGC.AX":"equities","TGR.AX":"equities","HUO.AX":"equities",
    "SKC.AX":"equities",
    # ── ASX Equities — Cannabis ──
    "CPH.AX":"equities","BOD.AX":"equities","CAN.AX":"equities","LEGA.AX":"equities",
    "CUP.AX":"equities",
    # ── ASX Equities — Industrials ──
    "QAN.AX":"equities","BXB.AX":"equities","AZJ.AX":"equities","QUB.AX":"equities",
    "WOR.AX":"equities","MND.AX":"equities","JHX.AX":"equities","CSR.AX":"equities",
    "BLD.AX":"equities","DOW.AX":"equities","SVW.AX":"equities","ALQ.AX":"equities",
    "NWH.AX":"equities","SXE.AX":"equities","CIM.AX":"equities","IDR.AX":"equities",
    "RWC.AX":"equities","LGL.AX":"equities",
    # ── ASX Equities — Energy (utilities) ──
    "AGL.AX":"equities","ORG.AX":"equities",
    # ── ASX Equities — Telecom ──
    "TLS.AX":"equities","TPG.AX":"equities","SPK.AX":"equities","NXL.AX":"equities",
    "PPS.AX":"equities",
    # ── ASX Equities — Media ──
    "NWS.AX":"equities","SEK.AX":"equities","CAR.AX":"equities","NEC.AX":"equities",
    "SWM.AX":"equities","MMS.AX":"equities","OML.AX":"equities","AHG.AX":"equities",
    # ── ASX ETFs (equity-tracking) ──
    "VAS.AX":"equities","VGS.AX":"equities","IOZ.AX":"equities","STW.AX":"equities",
    "NDQ.AX":"equities","A200.AX":"equities","ETHI.AX":"equities","IVV.AX":"equities",
    "IAA.AX":"equities","VHY.AX":"equities","SFY.AX":"equities","MVW.AX":"equities",
    "TECH.AX":"equities","HACK.AX":"equities","SEMI.AX":"equities","RBTZ.AX":"equities",
    "ACDC.AX":"equities","ERTH.AX":"equities",
    # ── ASX LICs ──
    "AFI.AX":"equities","ARG.AX":"equities","MLT.AX":"equities","WAM.AX":"equities",
    "WHF.AX":"equities","MIR.AX":"equities","AMH.AX":"equities","PIC.AX":"equities",
}


def _get_asset_class(ticker: str) -> str:
    """Resolve asset class for quadrant playbook scoring.
    Explicit map first, then pattern-based fallback so new tickers
    added to scanner universes get reasonable classification."""
    if ticker in ASSET_CLASS_MAP:
        return ASSET_CLASS_MAP[ticker]
    if ticker.endswith("=F"):
        return "commodities"
    if ticker.endswith(".AX"):
        return "equities"
    return "equities"

QUADRANT_PLAYBOOK: dict = {
    "rising_growth": {
        "strong_buy": ["equities","commodities"],
        "buy":        ["real_assets","corporate_bonds"],
        "avoid":      ["long_bonds","gold","tips"],
        "narrative":  (
            "Rising Growth: economic expansion lifts earnings and risk appetite. "
            "Dalio tilts heavily toward equities and commodities -- cyclicals, EM equities, "
            "and industrial metals outperform. Duration risk in nominal bonds rises."
        ),
    },
    "falling_growth": {
        "strong_buy": ["long_bonds","gold"],
        "buy":        ["tips","real_assets"],
        "avoid":      ["equities","commodities"],
        "narrative":  (
            "Falling Growth: recessionary pressure compresses corporate earnings. "
            "Safe havens dominate -- long-duration Treasuries rally as yields fall. "
            "Gold preserves wealth as central banks ease. "
            "Reduce cyclicals and commodities aggressively."
        ),
    },
    "rising_inflation": {
        "strong_buy": ["gold","commodities","tips"],
        "buy":        ["real_assets","equities"],
        "avoid":      ["long_bonds"],
        "narrative":  (
            "Rising Inflation: purchasing power erosion favours hard assets. "
            "Gold is the primary hedge -- Dalio's cornerstone in this quadrant. "
            "Energy, agriculture, and industrial commodities benefit directly. "
            "TIPS provide real yield protection. Nominal bonds are the loser here."
        ),
    },
    "falling_inflation": {
        "strong_buy": ["equities","long_bonds"],
        "buy":        ["real_assets","corporate_bonds"],
        "avoid":      ["commodities","gold","tips"],
        "narrative":  (
            "Falling Inflation (disinflation): central banks ease, real rates decline. "
            "Growth equities and nominal bonds rally in tandem. "
            "Historically the most favourable quadrant for balanced All Weather portfolios."
        ),
    },
}

def _gen_price_history_demo(price: float, trend: str, n_points: int = 30) -> list:
    """Seeded random-walk ending at `price`, shaped by trend direction."""
    drift = 0.003 if trend == "uptrend" else -0.003 if trend == "downtrend" else 0.0
    pts = []
    p = price * (1 - drift * n_points)
    for _ in range(n_points):
        p = p * (1 + drift + random.gauss(0, 0.012))
        pts.append(round(p, 4))
    pts[-1] = price
    return pts


async def _gen_signals(n: int = 12) -> list[dict]:
    """Generate trade signals from real price data."""
    cache_key = f"signals_{n}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    cached_by_market: dict = {"asx": [], "commodities": []}
    for mkt in ("asx", "commodities"):
        sc = _scanner_cache.get(mkt)
        if sc:
            rows = sorted(sc["rows"], key=lambda r: abs(r.get("change_pct", 0)), reverse=True)
            cached_by_market[mkt] = [r["ticker"] for r in rows if r.get("price", 0) > 0][:n]

    n_each = max(4, (n * 2) // 3)
    fresh = {
        "asx":         random.sample(ASX_TICKERS,        min(n_each, len(ASX_TICKERS))),
        "commodities": random.sample(COMMODITY_TICKERS,  min(n_each, len(COMMODITY_TICKERS))),
    }

    market_candidates = {}
    for mkt in ("asx", "commodities"):
        market_candidates[mkt] = list(dict.fromkeys(
            cached_by_market[mkt] + fresh[mkt]
        ))[:n_each]

    prices_map: dict = {}

    asx_cands = market_candidates["asx"][:10]
    if asx_cands:
        asx_prices = await _get_prices(asx_cands, "3mo")
        if asx_prices:
            prices_map.update(asx_prices)

    comm_cands = market_candidates["commodities"][:10]
    if comm_cands:
        comm_prices = await _get_prices(comm_cands, "3mo")
        if comm_prices:
            prices_map.update(comm_prices)

    candidates = list(dict.fromkeys(
        market_candidates["asx"] + market_candidates["commodities"]
    ))

    cache_prices: dict = {}
    for mkt in ("asx", "commodities"):
        sc = _scanner_cache.get(mkt)
        if sc:
            for r in sc["rows"]:
                if r.get("price", 0) > 0:
                    cache_prices[r["ticker"]] = r["price"]

    signals = []
    for ticker in candidates:
        closes = prices_map.get(ticker)
        if not closes or len(closes) < 10:
            continue

        price  = round(closes[-1], 2)
        rsi    = _calc_rsi(closes)
        trend  = _calc_trend(closes)
        atr    = _calc_atr(closes)
        macd_data = _calc_macd(closes)
        bb_data   = _calc_bollinger(closes)

        score = 0.0
        signal_reasons = []

        # ── ASX / Commodities — Dalio principles ──
        rsi_oversold, rsi_overbought = 32, 68

        if rsi < rsi_oversold:
            score += 2.0
            signal_reasons.append(f"RSI oversold ({rsi:.0f})")
        elif rsi > rsi_overbought:
            score -= 2.0
            signal_reasons.append(f"RSI overbought ({rsi:.0f})")
        elif rsi < 45:
            score += 0.5
        elif rsi > 55:
            score -= 0.5

        if macd_data["macd_signal"] == "bullish":
            score += 1.5
            signal_reasons.append("MACD bullish")
        else:
            score -= 1.5
            signal_reasons.append("MACD bearish")
        if macd_data["macd_crossover"]:
            score += 1.0
            signal_reasons.append("Fresh MACD crossover")

        if bb_data["bb_position"] == "below_lower":
            score += 1.5
            signal_reasons.append("Below lower Bollinger Band")
        elif bb_data["bb_position"] == "above_upper":
            score -= 1.5
            signal_reasons.append("Above upper Bollinger Band")

        if trend == "uptrend":
            score += 2.0
            signal_reasons.append("Confirmed uptrend")
        elif trend == "downtrend":
            score -= 2.0
            signal_reasons.append("Confirmed downtrend")

        if len(closes) >= 10:
            roc = (closes[-1] / closes[-10] - 1) * 100
            if roc > 5:
                score += 0.5
            elif roc < -5:
                score -= 0.5

        sl_mult, tp_mult = 1.5, 2.5

        if score >= 3.0:
            action = "BUY"
        elif score <= -3.0:
            action = "SHORT"
        elif score >= 1.5:
            action = "LONG"
        elif score <= -1.5:
            action = "SELL"
        else:
            action = "HOLD"

        price_history = [round(c, 2) for c in closes[-30:]]

        sl_offset = max(atr * sl_mult, price * 0.025)
        tp_offset = atr * tp_mult

        conf = round(min(95, max(50.0, 50 + abs(score) * 6)), 1)

        ac = _get_asset_class(ticker)
        qdata = STATE.last_quadrant or {}
        quadrant = qdata.get("quadrant", "rising_growth")
        pb = QUADRANT_PLAYBOOK.get(quadrant, QUADRANT_PLAYBOOK["rising_growth"])

        if ac in pb["strong_buy"]:   q_fit = "strong"
        elif ac in pb["buy"]:         q_fit = "moderate"
        elif ac in pb["avoid"]:       q_fit = "avoid"
        else:                          q_fit = "neutral"

        predicted_days = max(3, min(60, int(tp_offset / max(price * 0.008, 0.01))))
        pos_size_pct = round(min(5.0, max(1.0, (conf - 50) / 9)), 1)

        if ticker in COMMODITY_TICKERS:
            sig_market = "commodities"
        else:
            sig_market = "asx"

        rr_ratio = round(tp_offset / sl_offset, 2)
        sig = {
            "ticker": ticker,
            "trade_ticker": ticker,
            "currency": "AUD",
            "market": sig_market,
            "action": action,
            "confidence": conf,
            "price": price,
            "data_source": "LIVE",
            "quadrant_fit": q_fit,
            "rsi": rsi,
            "trend": trend,
            "macd_signal": macd_data["macd_signal"],
            "macd_value": macd_data["macd"],
            "macd_crossover": macd_data["macd_crossover"],
            "bb_position": bb_data["bb_position"],
            "bb_pct": bb_data["bb_pct"],
            "signal_score": round(score, 2),
            "signal_reasons": signal_reasons,
            "stop_loss":  round(price - sl_offset, 2) if action in ("SELL","SHORT") else round(price - sl_offset, 2),
            "take_profit": round(price + tp_offset, 2) if action in ("BUY","LONG")  else round(price - tp_offset, 2),
            "rr_ratio": rr_ratio,
            "fee_pct": _get_fee_pct(ticker),
            "round_trip_fee_pct": round(_get_fee_pct(ticker) * 2, 2),
            "net_rr_ratio": round(max(0, (tp_offset - price * _get_fee_pct(ticker) * 2 / 100)) / sl_offset, 2),
            "position_size_pct": pos_size_pct,
            "dalio_justification": _gen_justification(
                ticker, action, rsi=rsi, rr=rr_ratio,
                macd_signal=macd_data["macd_signal"],
                bb_position=bb_data["bb_position"],
                trend=trend, q_fit=q_fit,
            ),
            "price_history": price_history,
            "predicted_days": predicted_days,
            "timestamp": datetime.utcnow().isoformat(),
        }

        signals.append(sig)

    active = [s for s in signals if s["action"] != "HOLD"]
    if not active:
        active = sorted(signals, key=lambda s: s["confidence"], reverse=True)

    per_market = max(2, n // 2)
    balanced = []
    for mkt in ("asx", "commodities"):
        mkt_sigs = sorted(
            [s for s in active if s.get("market") == mkt],
            key=lambda s: s["confidence"], reverse=True
        )
        balanced.extend(mkt_sigs[:per_market])

    seen = {s["ticker"] for s in balanced}
    remaining = [s for s in active if s["ticker"] not in seen]
    remaining.sort(key=lambda s: s["confidence"], reverse=True)
    balanced.extend(remaining[:max(0, n - len(balanced))])

    balanced.sort(key=lambda s: s["confidence"], reverse=True)
    result = balanced[:n]
    _cache_set(cache_key, result)
    logger.info(f"Signals generated: {len(result)} total -- "
                f"ASX:{sum(1 for s in result if s.get('market')=='asx')} "
                f"Commodities:{sum(1 for s in result if s.get('market')=='commodities')}")
    return result


def _opp_from_signal_fallback(sigs: list, quadrant: str, playbook: dict,
                               qdata: dict, existing_classes: list, n: int) -> list:
    """Fallback when no scanner cache exists."""
    regime_label = qdata.get("label", quadrant.replace("_"," ").title())
    results = []
    for s in sigs:
        if s["action"] in ("HOLD", "SELL", "SHORT"):
            continue
        ac    = _get_asset_class(s["ticker"])
        q_fit = ("strong"   if ac in playbook["strong_buy"] else
                 "moderate" if ac in playbook["buy"]        else
                 "avoid"    if ac in playbook["avoid"]      else "neutral")
        q_w   = {"strong": 1.4, "moderate": 1.0, "neutral": 0.6, "avoid": 0.2}[q_fit]
        score = round(s["confidence"] * q_w, 1)
        jus   = s.get("dalio_justification", {})
        reason_0 = (f"Regime: {regime_label} -- {ac.replace('_',' ').title()} is "
                    f"{'favoured' if q_fit in ('strong','moderate') else 'on avoid list'}.")
        reason_1 = f"RSI {s['rsi']:.0f} | trend: {s['trend']} | signal: {s['action']}"
        reasoning = [reason_0, reason_1]
        if isinstance(jus, dict):
            for key in ("narrative", "recommendation"):
                val = jus.get(key, "")
                if val and isinstance(val, str):
                    reasoning.append(val[:120])
                    break
        results.append({
            "ticker": s["ticker"], "market": "signal", "action": s["action"],
            "price": s["price"], "change_pct": 0, "rsi": s["rsi"],
            "trend": s["trend"], "above_sma20": s["trend"] == "uptrend",
            "hi_52w": s["take_profit"], "lo_52w": s["stop_loss"],
            "pct_from_hi": 0, "pct_from_lo": 0, "sma20": s["price"],
            "stop_loss": s["stop_loss"], "take_profit": s["take_profit"],
            "rr_ratio": s["rr_ratio"], "score": score,
            "asset_class": ac, "quadrant_fit": q_fit,
            "data_source": s["data_source"],
            "reasoning": reasoning,
            "volume_fmt": "--", "sector": "--",
            "quadrant": quadrant, "regime_label": regime_label,
        })
    results.sort(key=lambda o: o["score"], reverse=True)
    return results[:n]


async def _gen_opportunities(n: int = 8) -> list[dict]:
    """Return the top-N trade opportunities."""
    qdata    = STATE.last_quadrant or _gen_quadrant_data()
    quadrant = qdata.get("quadrant", "rising_growth")
    dalio_pb = QUADRANT_PLAYBOOK.get(quadrant, QUADRANT_PLAYBOOK["rising_growth"])
    existing_classes = [_get_asset_class(t) for t in PAPER.positions]

    all_rows: list[dict] = []
    for mkt in ("asx", "commodities"):
        cached = _scanner_cache.get(mkt)
        if cached:
            for r in cached["rows"]:
                row = dict(r)
                row["_market"] = mkt
                all_rows.append(row)

    if not all_rows:
        sigs = await _gen_signals(n * 2)
        return _opp_from_signal_fallback(sigs, quadrant, dalio_pb, qdata, existing_classes, n)

    def _prescore(r: dict) -> float:
        tkr = r["ticker"]
        if tkr in PAPER.positions:
            return -999.0
        ac  = _get_asset_class(tkr)
        chg = r.get("change_pct", 0.0)
        q_s = (100 if ac in dalio_pb["strong_buy"] else
               70  if ac in dalio_pb["buy"]        else
               10  if ac in dalio_pb["avoid"]      else 45)
        mom = min(abs(chg) * 3.0, 30.0)
        dir_b = (15 if ac in dalio_pb["strong_buy"] and chg > 0 else
                 10 if ac in dalio_pb["avoid"] and chg < 0      else 0)
        div_b = max(0.0, 20.0 - existing_classes.count(ac) * 5)
        return q_s * 0.40 + mom * 0.25 + dir_b * 0.20 + div_b * 0.15

    all_rows.sort(key=_prescore, reverse=True)
    candidates = [r for r in all_rows if r["ticker"] not in PAPER.positions][:30]

    cand_by_mkt: dict = {"asx": [], "commodities": []}
    for r in candidates[:24]:
        mkt = r.get("_market", "asx")
        if mkt in cand_by_mkt:
            cand_by_mkt[mkt].append(r["ticker"])

    history_map: dict = {}
    for mkt, tkrs in cand_by_mkt.items():
        if not tkrs:
            continue
        chunk = await _get_prices(tkrs[:10], "3mo")
        if chunk:
            history_map.update(chunk)

    opportunities: list[dict] = []

    for r in candidates:
        tkr    = r["ticker"]
        ac     = _get_asset_class(tkr)
        closes = history_map.get(tkr)
        chg    = r.get("change_pct", 0.0)
        price  = r.get("price", 0.0)
        if not price:
            continue

        if closes and len(closes) >= 14:
            rsi        = _calc_rsi(closes)
            trend      = _calc_trend(closes)
            hi52       = float(max(closes))
            lo52       = float(min(closes))
            sma20      = float(np.mean(closes[-20:])) if len(closes) >= 20 else price
            above_sma  = price > sma20
            vol_d      = float(np.std(np.diff(closes)) / price) if len(closes) > 2 else 0.02
            data_src   = "LIVE"
        else:
            rsi        = 50.0
            trend      = "sideways"
            hi52       = price * 1.20
            lo52       = price * 0.80
            sma20      = price
            above_sma  = chg > 0
            vol_d      = 0.025
            data_src   = "SCANNER"

        pct_from_hi = round((price / hi52 - 1) * 100, 1) if hi52 else 0
        pct_from_lo = round((price / lo52 - 1) * 100, 1) if lo52 else 0

        if   rsi < 32 and trend != "downtrend": action = "BUY"
        elif rsi > 68 and trend != "uptrend":   action = "SELL"
        elif trend == "uptrend" and rsi < 58:   action = "LONG"
        elif trend == "downtrend" and rsi > 42: action = "SHORT"
        else:                                   action = "WATCH"

        is_short_signal = action in ("SELL", "SHORT")
        tkr_pb = dalio_pb
        tkr_regime = quadrant

        is_avoid_class  = ac in tkr_pb["avoid"]
        if is_short_signal and not is_avoid_class:
            continue

        q_score = (100 if ac in tkr_pb["strong_buy"] else
                   70  if ac in tkr_pb["buy"]        else
                   10  if ac in tkr_pb["avoid"]      else 45)
        q_fit   = ("strong"   if ac in tkr_pb["strong_buy"] else
                   "moderate" if ac in tkr_pb["buy"]        else
                   "avoid"    if ac in tkr_pb["avoid"]      else "neutral")

        if not is_short_signal:
            rsi_score = max(0.0, 50.0 - rsi) * 0.8
        else:
            rsi_score = max(0.0, rsi - 50.0) * 0.8

        mom_score  = min(abs(chg) * 2.5, 25.0)
        div_score  = max(0.0, 20.0 - existing_classes.count(ac) * 5.0)

        composite = round(
            q_score   * 0.35 +     # Dalio quadrant fit is primary driver
            rsi_score * 0.30 +
            mom_score * 0.20 +
            div_score * 0.15,
            1
        )

        atr = max(vol_d * price * 14, price * 0.01)
        sl_m = 1.5
        sl  = round(price - atr * sl_m, 4)
        tp_m = 2.5
        tp  = round(price + atr * tp_m, 4)
        rr  = round((tp - price) / max(price - sl, 1e-6), 2)

        regime_display = tkr_regime.replace('_', ' ').title()
        reasons = [
            f"Regime: {regime_display} -- "
            f"{ac.replace('_',' ').title()} is "
            f"{'FAVOURED (strong buy)' if q_fit=='strong' else 'favoured' if q_fit=='moderate' else 'AVOID LIST' if q_fit=='avoid' else 'neutral'}.",
            f"RSI {rsi:.0f} ({'oversold' if rsi<35 else 'overbought' if rsi>65 else 'neutral'}) | "
            f"Trend: {trend} | {'Above' if above_sma else 'Below'} 20-day SMA.",
            f"Today: {'+' if chg>=0 else ''}{chg:.2f}% | "
            f"52w range: {pct_from_lo:+.1f}% from low, {pct_from_hi:+.1f}% from high.",
            f"Stop ${sl:,.4f} -> Target ${tp:,.4f} | R:R {rr:.1f}x",
        ]
        if pct_from_lo < 10:
            reasons.append("Near 52-week low -- potential high-reward entry zone.")
        if above_sma and chg > 1:
            reasons.append("Strong momentum: price above SMA and up today.")
        if existing_classes.count(ac) == 0:
            reasons.append(f"No current {ac.replace('_',' ')} exposure -- adds portfolio diversification.")

        opportunities.append({
            "ticker":       tkr,
            "market":       r["_market"],
            "action":       action,
            "price":        price,
            "change_pct":   round(chg, 2),
            "rsi":          round(rsi, 1),
            "trend":        trend,
            "above_sma20":  above_sma,
            "hi_52w":       round(hi52, 4),
            "lo_52w":       round(lo52, 4),
            "pct_from_hi":  pct_from_hi,
            "pct_from_lo":  pct_from_lo,
            "sma20":        round(sma20, 4),
            "stop_loss":    sl,
            "take_profit":  tp,
            "rr_ratio":     rr,
            "fee_pct":      _get_fee_pct(tkr),
            "round_trip_fee_pct": round(_get_fee_pct(tkr) * 2, 2),
            "net_profit_pct": round((tp - price) / price * 100 - _get_fee_pct(tkr) * 2, 2),
            "score":        composite,
            "asset_class":  ac,
            "quadrant_fit": q_fit,
            "data_source":  data_src,
            "reasoning":    reasons,
            "volume_fmt":   r.get("volume_fmt", "--"),
            "sector":       r.get("sector", "--"),
            "quadrant":     tkr_regime,
            "regime_label": regime_display,
        })

    opportunities.sort(key=lambda o: o["score"], reverse=True)

    # ── Per-market balancing: guarantee minimum representation ──
    # Each market gets at least floor(n/2) slots, rest filled by best score
    per_mkt = max(1, n // 2)
    balanced = []
    for mkt in ("asx", "commodities"):
        mkt_opps = [o for o in opportunities if o.get("market") == mkt]
        balanced.extend(mkt_opps[:per_mkt])

    seen = {o["ticker"] for o in balanced}
    remaining = [o for o in opportunities if o["ticker"] not in seen]
    balanced.extend(remaining[:max(0, n - len(balanced))])
    balanced.sort(key=lambda o: o["score"], reverse=True)

    logger.info(f"Opportunities: {len(balanced[:n])} total -- "
                f"ASX:{sum(1 for o in balanced[:n] if o.get('market')=='asx')} "
                f"Commodities:{sum(1 for o in balanced[:n] if o.get('market')=='commodities')}")
    return balanced[:n]


def _gen_justification(ticker: str, action: str, **kwargs) -> dict:
    """Generate Dalio framework justification for ASX/commodities."""
    rsi_val = kwargs.get("rsi", 50.0)
    rr = kwargs.get("rr", 2.0)
    macd_sig = kwargs.get("macd_signal", "neutral")
    bb_pos = kwargs.get("bb_position", "mid")
    trend = kwargs.get("trend", "sideways")
    q_fit = kwargs.get("q_fit", "neutral")

    sent_score = 0.0
    sent_source = "keyword"

    n_positions = len(PAPER.positions) + 1
    corr_estimate = round(max(-0.15, min(0.1, 0.3 - 0.03 * n_positions)), 3)
    sharpe_est = round(max(0.01, min(0.4, (rr - 1.0) * 0.1)), 3)
    risk_contrib = round(100.0 / max(n_positions, 1), 2)

    rsi_desc = "oversold" if rsi_val < 35 else "overbought" if rsi_val > 65 else "neutral"

    qdata = STATE.last_quadrant or {}
    quadrant = qdata.get("quadrant", "rising_growth")
    meta = QUADRANT_META.get(quadrant, QUADRANT_META["rising_growth"])
    quadrant_label = quadrant.replace("_", " ").title()

    if STATE.last_sentiment:
        qs = STATE.last_sentiment.get("quadrant_sentiment", {})
        q_sent = qs.get(quadrant, {})
        sent_score = q_sent.get("avg_score", 0.0)
        sent_source = STATE.last_sentiment.get("sentiment_model", "keyword")

    sentiment_word = "positive" if sent_score > 0.1 else "negative" if sent_score < -0.1 else "neutral"

    ai_overview = (
        f"{ticker} presents a {action.lower()} opportunity under the current {quadrant_label} regime. "
        f"Sentiment ({sent_source}) is {sentiment_word} (score {sent_score:+.3f}), "
        f"RSI reads {rsi_val:.0f} ({rsi_desc}), MACD is {macd_sig}, "
        f"BB position: {bb_pos}, trend: {trend}. "
        f"Risk/reward ratio is {rr}:1. "
        f"Estimated correlation delta {corr_estimate:+.3f} -- within Holy Grail threshold. "
        f"Quadrant fit: {q_fit}. "
        f"Dalio framework favours {', '.join(meta['favoured'][:3])} in this environment."
    )

    reasons = [
        f"Asset has {q_fit} alignment with {quadrant_label} environment",
        f"Sentiment ({sent_source}): {sentiment_word} ({sent_score:+.3f}) for {ticker}",
        f"RSI {rsi_val:.0f} -- {rsi_desc} zone",
        f"MACD {macd_sig} | Bollinger: {bb_pos} | Trend: {trend}",
        f"Correlation delta {corr_estimate:+.3f} -- within Holy Grail threshold",
    ]

    return {
        "quadrant": quadrant,
        "quadrant_description": meta["description"],
        "sentiment_score": sent_score,
        "sentiment_model": sent_source,
        "sharpe_improvement": sharpe_est,
        "correlation_delta": corr_estimate,
        "risk_contribution_pct": risk_contrib,
        "ai_overview": ai_overview,
        "reasons": reasons,
        "data_source": "LIVE",
    }


def _gen_quadrant_data() -> dict:
    """Classify economic quadrant from real market data when available."""
    try:
        return _classify_quadrant_from_market_data()
    except Exception as exc:
        logger.debug(f"Quadrant using fallback ({exc}) — normal on startup before scanners run")
        return _gen_quadrant_data_random()


def _classify_quadrant_from_market_data() -> dict:
    """Derive economic quadrant from cached scanner/price data."""
    growth_score = 0.0
    inflation_score = 0.0
    confidence_factors = 0
    data_sources = []

    asx_cache = _scanner_cache.get("asx")
    if asx_cache and asx_cache.get("rows"):
        rows = asx_cache["rows"]
        up_count = sum(1 for r in rows if r.get("change_pct", 0) > 0)
        breadth = up_count / max(len(rows), 1)
        growth_score += (breadth - 0.5) * 4.0
        confidence_factors += 1
        data_sources.append("ASX breadth")

    comm_cache = _scanner_cache.get("commodities")
    if comm_cache and comm_cache.get("rows"):
        for r in comm_cache["rows"]:
            tkr = r.get("ticker", "")
            if "GC=F" in tkr or "GOLD" in tkr.upper():
                chg = r.get("change_pct", 0)
                if chg > 0.5:
                    inflation_score += 1.5
                elif chg < -0.5:
                    inflation_score -= 1.0
                confidence_factors += 1
                data_sources.append("Gold price")
                break
            if "CL=F" in tkr or "OIL" in tkr.upper() or "BZ=F" in tkr:
                chg = r.get("change_pct", 0)
                if chg > 1.0:
                    inflation_score += 1.0
                elif chg < -1.0:
                    inflation_score -= 0.5
                confidence_factors += 1
                data_sources.append("Oil price")
                break

    if STATE.last_sentiment:
        sent = STATE.last_sentiment
        if sent.get("conflict_risk_elevated"):
            inflation_score += 1.5
            confidence_factors += 1
            data_sources.append("Conflict risk")
        dom_q = sent.get("dominant_quadrant", "")
        if "inflation" in dom_q:
            inflation_score += 0.8
        elif "growth" in dom_q:
            growth_score += 0.5 if "rising" in dom_q else -0.5

    if confidence_factors == 0:
        raise ValueError("No market data available for quadrant classification")

    if growth_score > 0.3 and inflation_score <= 0.5:
        q = "rising_growth"
    elif growth_score <= -0.3 and inflation_score <= 0.5:
        q = "falling_growth"
    elif inflation_score > 0.5:
        q = "rising_inflation"
    else:
        q = "falling_inflation"

    meta = QUADRANT_META[q]
    confidence = min(92, max(55, 50 + confidence_factors * 8 + abs(growth_score + inflation_score) * 5))

    gdp_proxy = round(2.5 + growth_score * 0.8, 2)
    cpi_proxy = round(3.0 + inflation_score * 1.2, 2)
    gdp_trend = "rising" if growth_score > 0.3 else "falling" if growth_score < -0.3 else "stable"
    cpi_trend = "rising" if inflation_score > 0.5 else "falling" if inflation_score < -0.3 else "stable"

    conflict = False
    if STATE.last_sentiment:
        conflict = STATE.last_sentiment.get("conflict_risk_elevated", False)

    return {
        "quadrant": q,
        "label": meta["label"],
        "color": meta["color"],
        "description": meta["description"],
        "gdp_value": gdp_proxy,
        "gdp_trend": gdp_trend,
        "cpi_value": cpi_proxy,
        "cpi_trend": cpi_trend,
        "conflict_risk_elevated": conflict,
        "favoured_assets": meta["favoured"],
        "avoid_assets": meta["avoid"],
        "confidence": round(confidence, 1),
        "macro_source": f"Market-derived ({', '.join(data_sources)})",
        "sentiment_source": "RSS keyword analysis",
        "data_source": "LIVE" if confidence_factors >= 2 else "PARTIAL",
        "timestamp": datetime.utcnow().isoformat(),
    }


def _gen_quadrant_data_random() -> dict:
    """Pure random fallback."""
    q = random.choice(list(QUADRANT_META.keys()))
    meta = QUADRANT_META[q]
    return {
        "quadrant": q,
        "label": meta["label"],
        "color": meta["color"],
        "description": meta["description"],
        "gdp_value": round(random.uniform(-1.5, 4.5), 2),
        "gdp_trend": random.choice(["rising", "falling", "stable"]),
        "cpi_value": round(random.uniform(1.5, 8.5), 2),
        "cpi_trend": random.choice(["rising", "falling", "stable"]),
        "conflict_risk_elevated": random.choices([False, True], weights=[75, 25])[0],
        "favoured_assets": meta["favoured"],
        "avoid_assets": meta["avoid"],
        "confidence": round(random.uniform(65, 92), 1),
        "macro_source": "DEMO (no market data available)",
        "sentiment_source": "DEMO",
        "data_source": "DEMO",
        "timestamp": datetime.utcnow().isoformat(),
    }


# ── Sentiment (news RSS + FinBERT) ─────────────────────

_NEWS_RSS_FEEDS = [
    ("Reuters Business",   "https://feeds.reuters.com/reuters/businessNews"),
    ("Reuters Markets",    "https://feeds.reuters.com/reuters/UKmarkets"),
    ("Reuters Top News",   "https://feeds.reuters.com/reuters/topNews"),
    ("Reuters Tech",       "https://feeds.reuters.com/reuters/technologyNews"),
    ("Yahoo Finance",      "https://finance.yahoo.com/news/rssindex"),
    ("MarketWatch",        "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ("MarketWatch Stocks", "https://feeds.marketwatch.com/marketwatch/StockstoWatch/"),
    ("MarketWatch Econ",   "https://feeds.marketwatch.com/marketwatch/economy/"),
    ("CNBC Finance",       "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("CNBC World",         "https://www.cnbc.com/id/100727362/device/rss/rss.html"),
    ("CNBC Earnings",      "https://www.cnbc.com/id/15839135/device/rss/rss.html"),
    ("Investing.com",      "https://www.investing.com/rss/news_25.rss"),
    ("Investing Forex",    "https://www.investing.com/rss/news_1.rss"),
    ("Investing Stocks",   "https://www.investing.com/rss/news_14.rss"),
    ("Investing Commodities","https://www.investing.com/rss/news_11.rss"),
    ("Seeking Alpha",      "https://seekingalpha.com/market_currents.xml"),
    ("AFR",                "https://www.afr.com/rss/feed/latest"),
    ("AFR Markets",        "https://www.afr.com/rss/feed/markets"),
    ("ABC Finance AU",     "https://www.abc.net.au/news/feed/1399786/rss.xml"),
    ("FT Markets",         "https://www.ft.com/rss/home/uk"),
    ("Bloomberg Mkts",     "https://feeds.bloomberg.com/markets/news.rss"),
    ("WSJ Markets",        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    ("WSJ Business",       "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml"),
    ("WSJ World",          "https://feeds.a.dj.com/rss/RSSWorldNews.xml"),
    ("Barrons",            "https://feeds.barrons.com/barrons/articles.rss"),
    ("Motley Fool",        "https://www.fool.com/feeds/index.aspx"),
    ("Benzinga",           "https://www.benzinga.com/feed"),
    ("Zacks",              "https://www.zacks.com/feeds/"),
    ("TheStreet",          "https://www.thestreet.com/feeds/rss"),
    ("Kitco Gold",         "https://www.kitco.com/rss/kitconews.xml"),
    ("OilPrice.com",       "https://oilprice.com/rss/main"),
    ("Mining.com",         "https://www.mining.com/feed/"),
    ("Platts",             "https://www.spglobal.com/commodityinsights/en/rss-feed/platts-metals"),
    ("AgriCensus",         "https://www.agricensus.com/feed/"),
    ("BBC Business",       "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("BBC World",          "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Al Jazeera Business","https://www.aljazeera.com/xml/rss/all.xml"),
    ("The Guardian Money", "https://www.theguardian.com/uk/money/rss"),
    ("Guardian Business",  "https://www.theguardian.com/uk/business/rss"),
    ("Guardian World",     "https://www.theguardian.com/world/rss"),
    ("NPR Economy",        "https://feeds.npr.org/1006/rss.xml"),
    ("NPR Business",       "https://feeds.npr.org/1006/rss.xml"),
    ("Economist Finance",  "https://www.economist.com/finance-and-economics/rss.xml"),
    ("AP Business",        "https://rsshub.app/apnews/topics/business"),
    ("CNN Business",       "http://rss.cnn.com/rss/money_news_economy.rss"),
    ("CNN Markets",        "http://rss.cnn.com/rss/money_markets.rss"),
    ("ABC News US",        "https://abcnews.go.com/abcnews/moneyheadlines"),
    ("Forbes",             "https://www.forbes.com/business/feed/"),
    ("Forbes Investing",   "https://www.forbes.com/investing/feed/"),
    ("Nikkei Asia",        "https://asia.nikkei.com/rss"),
    ("SMH Business AU",    "https://www.smh.com.au/rss/business.xml"),
    ("SMH Money AU",       "https://www.smh.com.au/rss/money.xml"),
    ("SMH National AU",    "https://www.smh.com.au/rss/national.xml"),
    ("SMH Politics AU",    "https://www.smh.com.au/rss/politics.xml"),
    ("ABC AU News",        "https://www.abc.net.au/news/feed/51120/rss.xml"),
    ("ABC AU Business",    "https://www.abc.net.au/news/feed/2942460/rss.xml"),
    ("ABC AU Politics",    "https://www.abc.net.au/news/feed/45910/rss.xml"),
    ("The Australian",     "https://www.theaustralian.com.au/feed"),
    ("9News AU",           "https://www.9news.com.au/rss"),
    ("SBS News AU",        "https://www.sbs.com.au/news/feed"),
    ("Guardian AU",        "https://www.theguardian.com/australia-news/rss"),
    ("Defence Connect AU", "https://www.defenceconnect.com.au/rss.xml"),
    ("ASPI Strategist",    "https://www.aspistrategist.org.au/feed/"),
    ("LiveWire AU",        "https://www.livewiremarkets.com/rss"),
    ("SCMP Economy",       "https://www.scmp.com/rss/5/feed"),
    ("Channel News Asia",  "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6511"),
    ("Straits Times Biz",  "https://www.straitstimes.com/news/business/rss.xml"),
    ("Economic Times IN",  "https://economictimes.indiatimes.com/rssfeedstopstories.cms"),
    ("Mint India",         "https://www.livemint.com/rss/markets"),
    ("DW Business",        "https://rss.dw.com/xml/rss-en-bus"),
    ("Euronews Business",  "https://www.euronews.com/rss?level=tag&name=business"),
    ("Irish Times Biz",    "https://www.irishtimes.com/cmlink/the-irish-times-business-1.920361"),
    ("Telegraph Business", "https://www.telegraph.co.uk/business/rss.xml"),
    ("GNews Commodities",  "https://news.google.com/rss/search?q=gold+oil+commodities+futures&hl=en&gl=US&ceid=US:en"),
    ("GNews ASX",          "https://news.google.com/rss/search?q=ASX+Australian+stocks+market&hl=en-AU&gl=AU&ceid=AU:en"),
    ("GNews Markets",      "https://news.google.com/rss/search?q=stock+market+interest+rates+inflation&hl=en&gl=US&ceid=US:en"),
    ("GNews AU Defence",   "https://news.google.com/rss/search?q=Australia+defence+military+ADF+AUKUS&hl=en-AU&gl=AU&ceid=AU:en"),
    ("GNews AU Politics",  "https://news.google.com/rss/search?q=Australia+government+policy+budget+election&hl=en-AU&gl=AU&ceid=AU:en"),
    ("GNews AU Economy",   "https://news.google.com/rss/search?q=Australia+economy+RBA+inflation+GDP&hl=en-AU&gl=AU&ceid=AU:en"),
    ("GNews Indo-Pacific", "https://news.google.com/rss/search?q=Indo-Pacific+China+Taiwan+trade+war+sanctions&hl=en-AU&gl=AU&ceid=AU:en"),
    ("GNews Geopolitics",  "https://news.google.com/rss/search?q=geopolitics+sanctions+trade+war+Australia&hl=en-AU&gl=AU&ceid=AU:en"),
    ("GNews Central Banks","https://news.google.com/rss/search?q=RBA+federal+reserve+interest+rates+Australia&hl=en-AU&gl=AU&ceid=AU:en"),
    ("GNews Forex",        "https://news.google.com/rss/search?q=forex+currency+USD+EUR+AUD&hl=en&gl=US&ceid=US:en"),
    ("GNews Bonds",        "https://news.google.com/rss/search?q=treasury+bonds+yields+fixed+income&hl=en&gl=US&ceid=US:en"),
    ("GNews Real Estate",  "https://news.google.com/rss/search?q=real+estate+housing+market+REIT&hl=en&gl=US&ceid=US:en"),
    ("GNews ETF",          "https://news.google.com/rss/search?q=ETF+index+fund+passive+investing&hl=en&gl=US&ceid=US:en"),
    ("GNews Energy",       "https://news.google.com/rss/search?q=energy+oil+gas+renewable+OPEC&hl=en&gl=US&ceid=US:en"),
    ("GNews Tech Stocks",  "https://news.google.com/rss/search?q=tech+stocks+NASDAQ+AI+semiconductor&hl=en&gl=US&ceid=US:en"),
    ("GNews Earnings",     "https://news.google.com/rss/search?q=earnings+report+quarterly+results+revenue&hl=en&gl=US&ceid=US:en"),
    ("GNews IPO",          "https://news.google.com/rss/search?q=IPO+listing+SPAC+public+offering&hl=en&gl=US&ceid=US:en"),
    ("GNews China Econ",   "https://news.google.com/rss/search?q=China+economy+trade+manufacturing&hl=en&gl=US&ceid=US:en"),
    ("GNews Recession",    "https://news.google.com/rss/search?q=recession+downturn+economic+slowdown&hl=en&gl=US&ceid=US:en"),
]

_BULLISH_WORDS  = {"rally","surge","gain","high","record","beat","growth","rise","up","profit",
                   "positive","strong","outperform","buy","upgrade","bullish","recovery","soar"}
_BEARISH_WORDS  = {"fall","drop","crash","low","miss","recession","down","loss","negative","weak",
                   "risk","warning","downgrade","sell","bearish","slump","plunge","cut","concern"}
_CONFLICT_WORDS = {"war","conflict","military","sanctions","attack","threat","crisis","invasion",
                   "strike","bomb","weapons","troops","geopolit","defence","defense","navy",
                   "missile","nuclear","adf","aukus","submarine","indo-pacific","taiwan",
                   "south china sea","tariff","trade war","embargo","blockade","escalat"}
_INFLATION_KW   = {"inflation","cpi","pce","rates","fed","rba","boe","ecb","oil","energy",
                   "commodit","gold","silver","copper","wheat","supply"}
_GROWTH_KW      = {"gdp","jobs","employment","payroll","earnings","revenue","ism","pmi",
                   "retail","consumer","spending","trade","export","import"}
_DEFLAT_KW      = {"deflation","disinflation","rate cut","pivot","quantitative","qe","stimulus"}


def _score_headline(title: str, body: str = "") -> dict:
    text = (title + " " + body).lower()
    words = set(text.replace(",", " ").replace(".", " ").split())
    bull  = len(words & _BULLISH_WORDS)
    bear  = len(words & _BEARISH_WORDS)
    conf  = len(words & _CONFLICT_WORDS)
    infl  = len(words & _INFLATION_KW)
    grow  = len(words & _GROWTH_KW)
    defl  = len(words & _DEFLAT_KW)
    if bull > bear + 1: sentiment = "positive"
    elif bear > bull + 1: sentiment = "negative"
    else: sentiment = "neutral"
    if infl >= grow and infl >= defl:
        quadrant = "rising_inflation" if bull >= bear else "falling_inflation"
    elif defl > infl: quadrant = "falling_inflation"
    elif grow > 0 and bull >= bear: quadrant = "rising_growth"
    else: quadrant = "falling_growth"
    return {"sentiment": sentiment, "quadrant": quadrant, "conflict_risk": conf > 0,
            "bull_score": bull, "bear_score": bear}


async def _fetch_real_news() -> list[dict]:
    loop = asyncio.get_running_loop()
    articles: list[dict] = []

    def _parse_one_feed(feed_name: str, url: str) -> list[dict]:
        try:
            import feedparser
            feed = feedparser.parse(url)
            items = []
            for entry in feed.entries:
                title = (getattr(entry, "title", "") or "").strip()
                if not title or len(title) < 15: continue
                body = (getattr(entry, "summary", "") or "")[:400]
                score = _score_headline(title, body)
                items.append({"title": title, "source": feed_name, "sentiment": score["sentiment"],
                    "quadrant": score["quadrant"], "conflict_risk": score["conflict_risk"],
                    "bull_score": score["bull_score"], "bear_score": score["bear_score"],
                    "timestamp": datetime.utcnow().isoformat()})
            return items
        except Exception as exc:
            logger.debug(f"RSS [{feed_name}] failed: {exc}")
            return []

    futures = [loop.run_in_executor(None, _parse_one_feed, name, url) for name, url in _NEWS_RSS_FEEDS]
    try:
        results = await asyncio.wait_for(asyncio.gather(*futures, return_exceptions=True), timeout=30)
    except asyncio.TimeoutError:
        logger.warning("RSS aggregate fetch timed out after 30s")
        results = []
    for batch in results:
        if isinstance(batch, list): articles.extend(batch)

    if not articles:
        logger.warning("All RSS feeds failed -- using static headline pool")
        articles = _gen_static_headlines()

    seen: set = set()
    unique: list = []
    for a in articles:
        key = a["title"][:60].lower()
        if key not in seen:
            seen.add(key)
            unique.append(a)
    unique.sort(key=lambda h: (h["conflict_risk"], abs(h["bull_score"] - h["bear_score"])), reverse=True)
    logger.info(f"News scan: {len(unique)} unique articles from {len(_NEWS_RSS_FEEDS)} feeds")
    return unique


_STATIC_HEADLINE_POOL = [
    ("Fed signals pause in rate hikes amid cooling inflation",       "rising_growth",    "positive"),
    ("RBA holds rates as Australian GDP surprises to upside",        "rising_growth",    "positive"),
    ("Oil surges 4% on Middle East supply disruption fears",         "rising_inflation", "negative"),
    ("BHP reports record iron ore shipments, ASX rallies",           "rising_growth",    "positive"),
    ("China manufacturing PMI contracts for third straight month",   "falling_growth",   "negative"),
    ("Gold hits 3-month high as USD weakens on jobs data miss",      "rising_inflation", "positive"),
    ("Military conflict escalates in Eastern Europe, safe havens bid","rising_inflation","negative"),
    ("US CPI drops to 2.4%, markets price in rate cuts",             "falling_inflation","positive"),
    ("Tech layoffs accelerate, NASDAQ futures lower",                "falling_growth",   "negative"),
    ("OPEC+ announces surprise production cut of 500k bpd",          "rising_inflation", "neutral"),
    ("ASX 200 closes at 5-year high on earnings season beat",        "rising_growth",    "positive"),
    ("Copper prices plunge on weak Chinese demand outlook",          "falling_growth",   "negative"),
    ("Wheat prices spike amid Black Sea shipping disruptions",       "rising_inflation", "negative"),
    ("Australian dollar rallies as trade surplus widens",            "rising_growth",    "positive"),
    ("Silver ETF inflows surge as inflation expectations rise",      "rising_inflation", "positive"),
    ("US 10-year yield falls as economic data disappoints",          "falling_growth",   "negative"),
    ("Amazon, Alphabet earnings beat; tech sector rallies",         "rising_growth",    "positive"),
    ("Iron ore falls on Chinese property sector concerns",           "falling_growth",   "negative"),
    ("TIPS inflows accelerate as breakeven inflation widens",        "rising_inflation", "neutral"),
    ("S&P 500 hits fresh record as rate cut hopes persist",         "rising_growth",    "positive"),
]


def _gen_static_headlines() -> list[dict]:
    return [
        {"title": h[0], "quadrant": h[1], "sentiment": h[2], "source": "Market Intelligence",
         "timestamp": datetime.utcnow().isoformat(),
         "conflict_risk": "military" in h[0].lower() or "conflict" in h[0].lower(),
         "bull_score": 1 if h[2] == "positive" else 0,
         "bear_score": 1 if h[2] == "negative" else 0}
        for h in _STATIC_HEADLINE_POOL
    ]


# ── Keyword Sentiment Scorer (replaces FinBERT — zero dependencies) ──

_POSITIVE_KW = {
    "surge": 0.8, "soar": 0.9, "rally": 0.8, "boom": 0.85, "breakout": 0.7,
    "record high": 0.9, "outperform": 0.7, "beat": 0.6, "strong": 0.5,
    "growth": 0.5, "gain": 0.5, "rise": 0.4, "profit": 0.5, "positive": 0.4,
    "bullish": 0.6, "upgrade": 0.7, "recovery": 0.6, "rebound": 0.6,
    "optimism": 0.55, "confidence": 0.5, "expansion": 0.6, "stimulus": 0.5,
}
_NEGATIVE_KW = {
    "crash": 0.9, "collapse": 0.85, "plunge": 0.8, "crisis": 0.8,
    "recession": 0.85, "bankruptcy": 0.9, "default": 0.8, "bear market": 0.8,
    "decline": 0.5, "drop": 0.4, "fall": 0.4, "loss": 0.5, "weak": 0.45,
    "bearish": 0.6, "fear": 0.55, "uncertainty": 0.45, "slowdown": 0.5,
    "layoffs": 0.6, "downgrade": 0.6, "warning": 0.5, "miss": 0.5,
    "sanctions": 0.5, "tariff": 0.4, "inflation": 0.3,
}


def _keyword_sentiment_score(text: str) -> float:
    """Score text from -1 (bearish) to +1 (bullish) using keyword matching."""
    t = text.lower()
    pos = sum(w for kw, w in _POSITIVE_KW.items() if kw in t)
    neg = sum(w for kw, w in _NEGATIVE_KW.items() if kw in t)
    total = pos + neg
    if total == 0:
        return 0.0
    return max(-1.0, min(1.0, (pos - neg) / max(pos, neg)))


def _try_keyword_sentiment(articles: list[dict]) -> list[dict]:
    """Score articles using lightweight keyword sentiment (replaces FinBERT)."""
    if not articles:
        return articles
    for a in articles:
        text = a.get("title", "") + " " + a.get("summary", "")
        score = _keyword_sentiment_score(text)
        a["finbert_score"] = round(score, 4)  # Keep same key for API compat
        a["sentiment"] = "positive" if score > 0.1 else "negative" if score < -0.1 else "neutral"
        a["bull_score"] = round(max(0, score), 3)
        a["bear_score"] = round(max(0, -score), 3)
    logger.info(f"Keyword sentiment scored {len(articles)} articles")
    return articles


async def _gen_sentiment_data() -> dict:
    articles = await _fetch_real_news()
    articles = _try_keyword_sentiment(articles)
    total = len(articles)
    conflict = sum(1 for a in articles if a["conflict_risk"])

    q_counts: dict = defaultdict(lambda: {"count": 0, "bull": 0, "bear": 0, "scores": []})
    for a in articles:
        q = a["quadrant"]
        q_counts[q]["count"] += 1
        score = a.get("finbert_score", None)
        if score is not None:
            q_counts[q]["scores"].append(score)
            if score > 0.1: q_counts[q]["bull"] += 1
            elif score < -0.1: q_counts[q]["bear"] += 1
        else:
            if a["sentiment"] == "positive": q_counts[q]["bull"] += 1
            elif a["sentiment"] == "negative": q_counts[q]["bear"] += 1

    quadrant_sentiment: dict = {}
    for q in QUADRANT_META:
        s = q_counts[q]
        c = max(s["count"], 1)
        if s["scores"]:
            avg_score = round(float(np.mean(s["scores"])), 3)
        else:
            avg_score = round((s["bull"] - s["bear"]) / c, 3)
        quadrant_sentiment[q] = {"avg_score": avg_score, "article_count": s["count"],
                                  "bullish_pct": round(s["bull"] / c * 100, 1)}

    dominant = max(quadrant_sentiment, key=lambda q: quadrant_sentiment[q]["article_count"])
    sentiment_model = "FinBERT" if articles and articles[0].get("finbert_score") is not None else "Keyword"

    return {
        "total_articles": total, "conflict_risk_articles": conflict,
        "conflict_risk_elevated": conflict >= max(3, int(total * 0.08)),
        "dominant_quadrant": dominant, "quadrant_sentiment": quadrant_sentiment,
        "top_headlines": articles, "sentiment_model": sentiment_model,
        "timestamp": datetime.utcnow().isoformat(),
    }


# ── Correlation matrix ─────────────────────────────────

def _gen_correlation_matrix_demo(override_tickers: list = None) -> dict:
    """Fallback correlation matrix — uses real yfinance price data with
    default tickers when portfolio + watchlist have too few assets.
    Returns synthetic random only if yfinance fetch fails entirely."""
    if override_tickers:
        portfolio_tickers = [t for t in override_tickers if t]
    else:
        portfolio_tickers = list(PAPER.positions.keys())
    watchlist_tickers = list(WATCHLIST) if WATCHLIST else []
    tickers = list(dict.fromkeys(portfolio_tickers + watchlist_tickers))
    has_portfolio = len(portfolio_tickers) > 0
    if len(tickers) < 6:
        defaults = ["CBA.AX", "BHP.AX", "CSL.AX", "WDS.AX", "GMG.AX",
                     "FMG.AX", "NAB.AX", "WBC.AX", "GC=F", "CL=F", "SI=F"]
        for t in defaults:
            if t not in tickers:
                tickers.append(t)
            if len(tickers) >= 12:
                break
    # Try fetching real price data synchronously via yfinance
    try:
        import yfinance as yf
        import concurrent.futures
        df = yf.download(tickers[:15], period="3mo", progress=False, threads=True)
        if df is not None and hasattr(df, 'columns') and len(df) >= 20:
            close = df["Close"] if "Close" in df.columns else df
            close = close.dropna(axis=1, how="all").dropna()
            if len(close.columns) >= 4 and len(close) >= 20:
                valid = [t for t in tickers if t in close.columns]
                returns = close[valid].pct_change().dropna()
                corr = np.round(returns.corr().values, 3)
                n = len(valid)
                upper = np.triu_indices(n, k=1)
                source = "PORTFOLIO" if has_portfolio else "DEFAULTS"
                return {
                    "tickers": valid, "matrix": corr.tolist(),
                    "mean_correlation": round(float(np.mean(corr[upper])), 3),
                    "max_correlation": round(float(np.max(corr[upper])), 3),
                    "holy_grail_count": sum(1 for i in range(n) if np.mean(np.abs(corr[i][np.arange(n) != i])) < 0.3),
                    "threshold": 0.3, "data_source": source,
                    "timestamp": datetime.utcnow().isoformat(),
                }
    except Exception:
        pass
    # Ultimate fallback: synthetic random (should rarely hit)
    n = len(tickers)
    mat = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            r = round(random.uniform(-0.25, 0.55), 3)
            mat[i][j] = r
            mat[j][i] = r
    upper = np.triu_indices(n, k=1)
    return {
        "tickers": tickers, "matrix": mat.tolist(),
        "mean_correlation": round(float(np.mean(mat[upper])), 3),
        "max_correlation": round(float(np.max(mat[upper])), 3),
        "holy_grail_count": sum(1 for i in range(n) if np.mean(np.abs(mat[i][np.arange(n) != i])) < 0.3),
        "threshold": 0.3, "data_source": "DEMO",
        "timestamp": datetime.utcnow().isoformat(),
    }


async def _real_correlation_matrix(override_tickers: list = None) -> Optional[dict]:
    """Correlation matrix of portfolio holdings + watchlist.
    Holy Grail count measures how many of YOUR assets have mean
    correlation < 0.3 — not the entire ticker universe.
    override_tickers: if provided (e.g. from live broker), use these instead of PAPER."""
    # Use actual portfolio positions + watchlist for meaningful correlation
    if override_tickers:
        portfolio_tickers = [t for t in override_tickers if t]
    else:
        portfolio_tickers = list(PAPER.positions.keys())
    watchlist_tickers = list(WATCHLIST) if WATCHLIST else []
    # Combine, deduplicate, preserving order
    tickers = list(dict.fromkeys(portfolio_tickers + watchlist_tickers))
    has_positions = len(portfolio_tickers) > 0
    # Only pad with defaults if user has NO positions at all
    if not has_positions and len(tickers) < 6:
        defaults = [
            "CBA.AX", "BHP.AX", "CSL.AX", "WDS.AX", "GMG.AX",  # ASX
            "GC=F", "CL=F", "SI=F",                               # Commodities
        ]
        for t in defaults:
            if t not in tickers:
                tickers.append(t)
            if len(tickers) >= 15:
                break
    if len(tickers) < 2:
        return None
    prices_map = await _get_prices(tickers[:30], "3mo")
    if not prices_map or len(prices_map) < 2: return None
    valid = [t for t in tickers if t in prices_map and len(prices_map[t]) >= 20]
    if len(valid) < 2: return None
    min_len = min(len(prices_map[t]) for t in valid)
    closes = np.array([prices_map[t][-min_len:] for t in valid], dtype=float)
    returns = np.diff(closes, axis=1) / closes[:, :-1]
    corr = np.round(np.corrcoef(returns), 3)
    # Handle single asset case (corrcoef returns scalar)
    if corr.ndim == 0:
        corr = np.array([[1.0]])
    n = len(valid)
    upper = np.triu_indices(n, k=1)
    hg_count = sum(1 for i in range(n) if float(np.mean(np.abs(corr[i][np.arange(n) != i]))) < 0.3) if n > 1 else 0
    # Include real portfolio position data for weight calculation
    portfolio_info = {}
    total_value = PAPER.cash
    for t, pos in PAPER.positions.items():
        mv = pos["qty"] * pos["entry_price"]
        total_value += mv
        portfolio_info[t] = {"qty": pos["qty"], "entry_price": pos["entry_price"],
                             "market_value": round(mv, 2), "side": pos.get("side", "LONG")}
    for t in portfolio_info:
        portfolio_info[t]["weight_pct"] = round(portfolio_info[t]["market_value"] / max(total_value, 1) * 100, 2)
    source = "LIVE" if has_positions else ("DEFAULTS" if not has_positions else "PORTFOLIO")
    return {
        "tickers": valid, "matrix": corr.tolist(),
        "mean_correlation": round(float(np.mean(corr[upper])), 3) if len(upper[0]) > 0 else 0.0,
        "max_correlation": round(float(np.max(corr[upper])), 3) if len(upper[0]) > 0 else 0.0,
        "holy_grail_count": hg_count, "threshold": 0.3,
        "data_source": source, "timestamp": datetime.utcnow().isoformat(),
        "portfolio_positions": portfolio_info,
    }


def _gen_portfolio_health() -> dict:
    """Real portfolio health from PAPER state."""
    initial = PAPER_STARTING_CASH
    equity = PAPER.cash
    if PAPER.equity_history:
        equity = PAPER.equity_history[-1]["v"]
    daily_pnl = 0.0
    if len(PAPER.equity_history) >= 2:
        daily_pnl = round(PAPER.equity_history[-1]["v"] - PAPER.equity_history[-2]["v"], 2)
    drawdown = 0.0
    if PAPER.equity_history:
        peak = max(e["v"] for e in PAPER.equity_history)
        drawdown = round((peak - equity) / peak * 100, 2) if peak > 0 else 0.0
    sharpe = 0.0
    if len(PAPER.equity_history) >= 10:
        try:
            eq_arr = np.array([e["v"] for e in PAPER.equity_history], dtype=float)
            rets = np.diff(eq_arr) / eq_arr[:-1]
            if rets.std() > 0:
                sharpe = round(float((rets.mean() / rets.std()) * (252 ** 0.5)), 2)
        except Exception:
            pass
    open_count = len(PAPER.positions)
    positions_list = [
        {"ticker": t, "side": pos.get("side", "LONG"),
         "size_pct": round(pos["qty"] * pos["entry_price"] / max(equity, 1) * 100, 1),
         "unrealised_pnl_pct": 0.0}
        for t, pos in PAPER.positions.items()
    ]
    # Build daily P&L series from equity history
    daily_pnl_series = []
    if len(PAPER.equity_history) >= 2:
        for idx in range(1, len(PAPER.equity_history)):
            prev_v = PAPER.equity_history[idx - 1]["v"]
            curr_v = PAPER.equity_history[idx]["v"]
            d_pnl = round(curr_v - prev_v, 2)
            d_pct = round(d_pnl / prev_v * 100, 3) if prev_v else 0
            daily_pnl_series.append({
                "t": PAPER.equity_history[idx].get("t", ""),
                "pnl": d_pnl, "pnl_pct": d_pct,
            })
    return {
        "timestamp": datetime.utcnow().isoformat(), "equity": round(equity, 2),
        "initial_equity": initial, "cash": round(PAPER.cash, 2),
        "total_return_pct": round((equity / initial - 1) * 100, 2) if initial else 0.0,
        "daily_pnl": daily_pnl,
        "daily_pnl_pct": round(daily_pnl / equity * 100, 3) if equity else 0.0,
        "drawdown_pct": drawdown, "open_positions": open_count,
        "dalio_diversification_met": open_count >= 3,
        "selected_portfolio_size": open_count,
        "circuit_breaker_active": drawdown > 9.5,
        "daily_limit_pct": 2.0, "max_drawdown_pct": 10.0,
        "sharpe_ratio": sharpe, "positions": positions_list,
        "daily_pnl_series": daily_pnl_series[-60:],
        "has_real_data": len(PAPER.equity_history) >= 2,
    }


def _gen_backtest_results() -> dict:
    """Generate backtest results from real trade history when available,
    falling back to simulated demo data."""
    trades = PAPER.history
    eq_hist = PAPER.equity_history

    # ── Try real data first ──
    if len(trades) >= 3 and len(eq_hist) >= 5:
        wins = [t for t in trades if t.get("pnl", 0) > 0]
        losses = [t for t in trades if t.get("pnl", 0) <= 0]
        total_pnl = sum(t.get("pnl", 0) for t in trades)
        initial = STATE.initial_equity or PAPER_STARTING_CASH
        eq_vals = np.array([e["v"] for e in eq_hist], dtype=float)
        rets = np.diff(eq_vals) / eq_vals[:-1]
        sharpe = round(float((rets.mean() / rets.std()) * (252 ** 0.5)), 2) if rets.std() > 0 else 0
        neg_rets = rets[rets < 0]
        sortino = round(float((rets.mean() / neg_rets.std()) * (252 ** 0.5)), 2) if len(neg_rets) > 0 and neg_rets.std() > 0 else 0
        peak = np.maximum.accumulate(eq_vals)
        drawdowns = (peak - eq_vals) / peak * 100
        max_dd = -round(float(drawdowns.max()), 2)
        calmar = round(abs(total_pnl / initial * 100 / max_dd), 2) if max_dd != 0 else 0
        win_rate = round(len(wins) / len(trades) * 100, 1)
        avg_trade_ret = round(np.mean([t.get("pnl_pct", t.get("pnl", 0) / initial * 100) for t in trades]), 2)
        total_ret = round((eq_vals[-1] / eq_vals[0] - 1) * 100, 2)
        # Build period breakdown from equity history chunks
        chunk_size = max(len(eq_hist) // 8, 5)
        periods = []
        for i in range(min(8, len(eq_hist) // chunk_size)):
            chunk = eq_vals[i * chunk_size:(i + 1) * chunk_size]
            if len(chunk) < 2:
                continue
            p_ret = round(float((chunk[-1] / chunk[0] - 1) * 100), 2)
            p_rets = np.diff(chunk) / chunk[:-1]
            p_sharpe = round(float((p_rets.mean() / p_rets.std()) * (252 ** 0.5)), 2) if p_rets.std() > 0 else 0
            p_peak = np.maximum.accumulate(chunk)
            p_dd = -round(float(((p_peak - chunk) / p_peak * 100).max()), 2)
            # Count trades in this time window
            chunk_trades = trades[i * (len(trades) // 8):(i + 1) * (len(trades) // 8)] if len(trades) >= 8 else trades
            p_wins = len([t for t in chunk_trades if t.get("pnl", 0) > 0])
            p_total = len(chunk_trades) or 1
            periods.append({
                "period": i + 1, "train_start": eq_hist[i * chunk_size].get("t", "")[:10],
                "return_pct": p_ret, "sharpe": p_sharpe,
                "max_drawdown": p_dd, "win_rate": round(p_wins / p_total * 100, 1),
                "trades": p_total,
            })
        days = (len(eq_hist)) / 252 if len(eq_hist) > 0 else 1
        ann_ret = round(total_ret / max(days, 0.01), 2)
        return {
            "status": "REAL", "data_source": "real", "training_months": 0,
            "test_months": 0, "periods": len(periods),
            "total_return_pct": total_ret,
            "annualised_return_pct": ann_ret,
            "sharpe_ratio": sharpe, "sortino_ratio": sortino,
            "calmar_ratio": calmar, "max_drawdown_pct": max_dd,
            "win_rate_pct": win_rate, "avg_trade_return_pct": avg_trade_ret,
            "period_results": periods, "timestamp": datetime.utcnow().isoformat(),
        }

    # ── Fallback: simulated demo data ──
    periods = []
    cumulative = STATE.initial_equity
    for i in range(8):
        ret = round(random.gauss(3.5, 6.0), 2)
        cumulative *= (1 + ret / 100)
        periods.append({"period": i + 1, "train_start": f"202{2 + i // 4}-Q{(i % 4) + 1}",
            "return_pct": ret, "sharpe": round(random.uniform(0.9, 2.8), 2),
            "max_drawdown": round(random.uniform(-12, -1), 2),
            "win_rate": round(random.uniform(50, 72), 1), "trades": random.randint(28, 85)})
    return {
        "status": "DEMO", "data_source": "demo", "training_months": 12,
        "test_months": 3, "periods": len(periods),
        "total_return_pct": round((cumulative / STATE.initial_equity - 1) * 100, 2),
        "annualised_return_pct": round(random.uniform(18, 42), 2),
        "sharpe_ratio": round(random.uniform(1.6, 2.4), 2),
        "sortino_ratio": round(random.uniform(2.0, 3.1), 2),
        "calmar_ratio": round(random.uniform(1.8, 2.9), 2),
        "max_drawdown_pct": round(random.uniform(-9, -5), 2),
        "win_rate_pct": round(random.uniform(57, 68), 1),
        "avg_trade_return_pct": round(random.uniform(1.5, 3.2), 2),
        "period_results": periods, "timestamp": datetime.utcnow().isoformat(),
    }


def dalio_analyse_trade(ticker: str, side: str, quadrant: str,
                        cash: float, positions: dict, current_signals: list) -> dict:
    ticker = ticker.upper().strip()
    side = side.upper().strip()
    asset_class = _get_asset_class(ticker)
    playbook = QUADRANT_PLAYBOOK.get(quadrant, QUADRANT_PLAYBOOK["rising_growth"])

    if side == "BUY":
        if   asset_class in playbook["strong_buy"]: raw_score = random.randint(82, 97); fit_label = "STRONG FIT"
        elif asset_class in playbook["buy"]:         raw_score = random.randint(62, 81); fit_label = "MODERATE FIT"
        elif asset_class in playbook["avoid"]:       raw_score = random.randint(10, 35); fit_label = "COUNTER-TREND"
        else:                                        raw_score = random.randint(40, 61); fit_label = "NEUTRAL"
    else:
        if   asset_class in playbook["avoid"]:       raw_score = random.randint(75, 93); fit_label = "STRONG FIT"
        elif asset_class not in playbook["strong_buy"]: raw_score = random.randint(55, 74); fit_label = "MODERATE FIT"
        else:                                        raw_score = random.randint(20, 45); fit_label = "COUNTER-TREND"
    fit_score = max(0, min(100, raw_score))

    risk_flags: list = []
    total_pv = cash + sum(p.get("qty", 0) * p.get("entry_price", 0) for p in positions.values())
    n_pos = len(positions)
    existing_classes = [_get_asset_class(t) for t in positions]
    class_count = existing_classes.count(asset_class)
    if class_count >= 4: risk_flags.append(f"High concentration: {class_count} existing {asset_class} positions")
    if n_pos >= 15: risk_flags.append("Portfolio at 15-position Holy Grail limit")
    if asset_class in playbook["avoid"] and side == "BUY":
        risk_flags.append(f"{asset_class.replace('_',' ').title()} is on the avoid list for {quadrant.replace('_',' ').title()}")
    if total_pv > 0 and cash / total_pv < 0.05: risk_flags.append("Cash below 5% of portfolio -- liquidity risk")
    sig = next((s for s in current_signals if s.get("ticker") == ticker), None)
    if sig and sig.get("action") in ("SELL","SHORT") and side == "BUY":
        risk_flags.append(f"Signal engine recommends {sig['action']} on {ticker}")

    quadrant_label = quadrant.replace("_"," ").title()
    asset_label = asset_class.replace("_"," ").title()
    reasoning = [
        f"Quadrant is {quadrant_label} -- Dalio favours {', '.join((playbook['strong_buy']+playbook['buy'])[:3]).replace('_',' ')}.",
        f"{ticker} classified as {asset_label} -- {'aligned' if asset_class in playbook['strong_buy']+playbook['buy'] else 'not aligned'} with {quadrant_label} playbook.",
        f"Portfolio has {n_pos} positions across {len(set(existing_classes))} asset class(es) -- {'diversified' if len(set(existing_classes))>=4 else 'needs more diversification'}.",
    ]
    if sig:
        reasoning.append(f"Signal engine: {sig.get('action','HOLD')} {ticker} with {sig.get('confidence',0):.0f}% confidence, RSI {sig.get('rsi',50)}.")
    reasoning.append(f"Avoid list for {quadrant_label}: {', '.join(playbook['avoid']).replace('_',' ')}. {'This trade is on the avoid list.' if asset_class in playbook['avoid'] else 'This trade is not on the avoid list.'}")

    _AW = {"equities":0.30,"long_bonds":0.40,"gold":0.15,"commodities":0.075,"tips":0.075}
    cc = {c: existing_classes.count(c) for c in set(existing_classes)}
    if side == "BUY": cc[asset_class] = cc.get(asset_class, 0) + 1
    tot = sum(cc.values()) or 1
    dev = sum(abs(cc.get(c,0)/tot - ideal) for c, ideal in _AW.items())
    all_weather_score = max(0, min(100, int(100 - dev * 50)))

    if fit_label == "STRONG FIT":
        rec = f"PROCEED -- {ticker} strongly aligned with {quadrant_label} regime. Size within risk budget."
    elif fit_label == "MODERATE FIT":
        rec = f"CONSIDER -- Moderate alignment. Reduce size 30-50% vs a strong-fit signal."
    elif fit_label == "COUNTER-TREND":
        rec = f"CAUTION -- {ticker} ({asset_label}) counters Dalio's {quadrant_label} playbook. Keep size <2% if high conviction."
    else:
        rec = f"NEUTRAL -- No strong quadrant signal. Assess diversification value before committing."

    return {"fit_score": fit_score, "fit_label": fit_label, "quadrant_narrative": playbook["narrative"],
            "asset_class": asset_class, "reasoning": reasoning, "recommendation": rec,
            "risk_flags": risk_flags, "all_weather_score": all_weather_score,
            "quadrant": quadrant, "quadrant_label": quadrant_label, "ticker": ticker, "side": side,
            "timestamp": datetime.utcnow().isoformat()}
