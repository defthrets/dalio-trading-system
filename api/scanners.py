"""
Dalios -- Market Scanning
Scanner cache, ticker universes, market data fetching (ASX, crypto, commodities),
market summary, live price lookups.
"""

import asyncio
import random
import time
import aiohttp
import numpy as np
from datetime import datetime
from typing import Optional

from loguru import logger

from api.utils import (
    _cache_get, _cache_set, _get_prices, _fmt_vol, _EXECUTOR,
    YF_AVAILABLE, _is_crypto, SOURCE_LIMITER,
)
from api.state import WATCHLIST


# ── Ticker Universes ────────────────────────────────────

ASX_TICKERS = [
    # -- Big 4 Banks --
    "CBA.AX", "WBC.AX", "ANZ.AX", "NAB.AX",
    # -- Other Banks & Financials --
    "MQG.AX", "BEN.AX", "BOQ.AX", "SUN.AX", "QBE.AX", "IAG.AX",
    "AMP.AX", "ASX.AX", "PPT.AX", "CGF.AX", "CPU.AX", "NHF.AX",
    "MPL.AX", "NIB.AX", "AUB.AX",
    # -- Mining & Resources --
    "BHP.AX", "RIO.AX", "FMG.AX", "S32.AX", "MIN.AX", "LYC.AX",
    "IGO.AX", "SFR.AX", "PLS.AX", "ILU.AX", "AWC.AX",
    "LTR.AX", "NIC.AX", "WSA.AX",
    # -- Gold & Precious Metals --
    "NST.AX", "EVN.AX", "SBM.AX", "RRL.AX", "SAR.AX",
    "GOR.AX", "CMM.AX", "RMS.AX", "DEG.AX", "WAF.AX",
    # -- Energy --
    "WDS.AX", "STO.AX", "BPT.AX", "AGL.AX", "ORG.AX",
    "APA.AX", "KAR.AX", "CVN.AX",
    # -- Healthcare & Biotech --
    "CSL.AX", "RMD.AX", "COH.AX", "SHL.AX", "ANN.AX",
    "PME.AX", "EBO.AX", "HLS.AX", "PNV.AX", "RHC.AX",
    "CUV.AX", "NEU.AX", "TLX.AX", "MSB.AX",
    # -- Technology --
    "WTC.AX", "XRO.AX", "ALU.AX", "MP1.AX", "TNE.AX",
    "REA.AX", "APX.AX", "TYR.AX", "SDR.AX", "DTL.AX",
    "ZIP.AX", "EML.AX", "HUB.AX", "NXT.AX",
    # -- Consumer / Retail --
    "WES.AX", "WOW.AX", "COL.AX", "JBH.AX", "TWE.AX",
    "HVN.AX", "DMP.AX", "SUL.AX", "LOV.AX", "KGN.AX",
    "TPW.AX", "MYR.AX", "NCK.AX",
    # -- Consumer Staples & Food --
    "GNC.AX", "NUF.AX", "ELD.AX", "BKL.AX",
    # -- REITs --
    "GMG.AX", "SCG.AX", "GPT.AX", "VCX.AX", "CLW.AX",
    "MGR.AX", "DXS.AX", "CHC.AX", "BWP.AX", "NSR.AX",
    "CQR.AX", "HMC.AX", "ABP.AX", "SCP.AX", "HDN.AX",
    # -- Industrials & Infrastructure --
    "TCL.AX", "QAN.AX", "BXB.AX", "AZJ.AX", "QUB.AX",
    "WOR.AX", "MND.AX", "JHX.AX", "CSR.AX", "BLD.AX",
    "DOW.AX", "SVW.AX",
    # -- Telecom --
    "TLS.AX", "TPG.AX", "SPK.AX",
    # -- Media --
    "NWS.AX", "SEK.AX", "CAR.AX", "REA.AX", "NEC.AX",
    # -- LICs & ETFs --
    "VAS.AX", "VGS.AX", "IOZ.AX", "STW.AX", "NDQ.AX",
    "A200.AX", "GOLD.AX", "ETHI.AX",
    # -- Diversified --
    "AFI.AX", "ARG.AX", "MLT.AX", "WAM.AX",
]

# Crypto -- top liquid pairs from Binance/Coinbase/Kraken in yfinance USD format
CRYPTO_TICKERS = [
    # -- Layer 1 Major --
    "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD",
    "ADA-USD", "AVAX-USD", "DOT-USD", "TRX-USD", "LTC-USD",
    "ATOM-USD", "NEAR-USD", "ALGO-USD", "XLM-USD", "VET-USD",
    "ICP-USD", "HBAR-USD", "FIL-USD", "EOS-USD", "XTZ-USD",
    "NEO-USD", "IOTA-USD", "XMR-USD", "ZEC-USD", "DASH-USD",
    "WAVES-USD", "ICX-USD", "ONT-USD", "QTUM-USD", "ZIL-USD",
    # -- Layer 2 & Scaling --
    "MATIC-USD", "ARB-USD", "OP-USD", "IMX-USD", "LRC-USD",
    "SKL-USD", "METIS-USD",
    # -- New-Gen L1 --
    "APT-USD", "SUI-USD", "INJ-USD", "SEI-USD", "TIA-USD",
    "PYTH-USD", "JUP-USD",
    # -- DeFi -- DEX & Lending --
    "UNI-USD", "AAVE-USD", "MKR-USD", "COMP-USD", "YFI-USD",
    "SUSHI-USD", "1INCH-USD", "CRV-USD", "BAL-USD", "DYDX-USD",
    "GMX-USD", "SNX-USD", "PENDLE-USD", "CAKE-USD",
    "CVX-USD", "FXS-USD",
    # -- DeFi -- Staking --
    "LDO-USD", "RPL-USD", "ANKR-USD",
    # -- Gaming & Metaverse --
    "SAND-USD", "MANA-USD", "ENJ-USD", "AXS-USD", "GALA-USD",
    "FLOW-USD", "BEAM-USD", "RONIN-USD",
    # -- Meme Coins --
    "DOGE-USD", "SHIB-USD", "PEPE-USD", "FLOKI-USD", "BONK-USD",
    "WIF-USD",
    # -- Infrastructure & Oracles --
    "LINK-USD", "BAND-USD", "API3-USD", "TRB-USD",
    # -- Storage & Data --
    "AR-USD", "STORJ-USD",
    # -- Privacy --
    "SCRT-USD", "ROSE-USD",
    # -- Cross-chain & Interop --
    "RUNE-USD", "AXL-USD",
    # -- AI & Data --
    "FET-USD", "AGIX-USD", "OCEAN-USD", "NMR-USD",
    "TAO-USD", "RNDR-USD", "WLD-USD",
    # -- Exchange Tokens --
    "CRO-USD", "KCS-USD",
    # -- Web3 & Social --
    "BAT-USD", "ZRX-USD", "GRT-USD", "LPT-USD",
    # -- RWA --
    "ONDO-USD",
    # -- Misc High-liquidity --
    "THETA-USD", "CHZ-USD", "CELR-USD", "MINA-USD",
    "KAVA-USD", "CFX-USD", "JASMY-USD", "FTM-USD",
    "HOT-USD", "WIN-USD", "REEF-USD", "OMG-USD",
]

COMMODITY_TICKERS = [
    # -- ASX Precious Metal ETFs (not in ASX_TICKERS) --
    "PMGOLD.AX", "QAU.AX",
    # -- ASX Crude Oil ETF --
    "OOO.AX",
    # -- ASX Broad Commodity ETF --
    "QCB.AX",
    # -- ASX Copper --
    "OZL.AX",
    # -- ASX Uranium (not in ASX_TICKERS) --
    "PDN.AX", "BMN.AX", "LOT.AX", "DYL.AX", "PEN.AX",
    # -- ASX Lithium (not in ASX_TICKERS) --
    "AKE.AX", "CXO.AX", "GL1.AX", "SYA.AX",
    # -- ASX Rare Earths (not in ASX_TICKERS) --
    "ARU.AX", "VML.AX", "HAS.AX",
    # -- Global Futures (commodity price exposure) --
    "GC=F", "SI=F", "CL=F", "BZ=F", "NG=F",
]

ALL_TICKERS = ASX_TICKERS + CRYPTO_TICKERS + COMMODITY_TICKERS
CORR_TICKERS = ASX_TICKERS  # Use ASX for correlation heatmap


# ── Asset metadata ──────────────────────────────────────
_ASSET_META = {
    # ASX
    "CBA.AX":  {"name": "Commonwealth Bank",       "cat": "ASX", "sector": "Banking"},
    "ANZ.AX":  {"name": "ANZ Bank",                "cat": "ASX", "sector": "Banking"},
    "NAB.AX":  {"name": "National Australia Bank", "cat": "ASX", "sector": "Banking"},
    "WBC.AX":  {"name": "Westpac Banking",         "cat": "ASX", "sector": "Banking"},
    "BHP.AX":  {"name": "BHP Group",               "cat": "ASX", "sector": "Mining"},
    "RIO.AX":  {"name": "Rio Tinto",               "cat": "ASX", "sector": "Mining"},
    "FMG.AX":  {"name": "Fortescue Metals",        "cat": "ASX", "sector": "Mining"},
    "S32.AX":  {"name": "South32",                 "cat": "ASX", "sector": "Mining"},
    "MIN.AX":  {"name": "Mineral Resources",       "cat": "ASX", "sector": "Mining"},
    "LYC.AX":  {"name": "Lynas Rare Earths",       "cat": "ASX", "sector": "Materials"},
    "WDS.AX":  {"name": "Woodside Energy",         "cat": "ASX", "sector": "Energy"},
    "STO.AX":  {"name": "Santos",                  "cat": "ASX", "sector": "Energy"},
    "BPT.AX":  {"name": "Beach Energy",            "cat": "ASX", "sector": "Energy"},
    "AGL.AX":  {"name": "AGL Energy",              "cat": "ASX", "sector": "Utilities"},
    "ORG.AX":  {"name": "Origin Energy",           "cat": "ASX", "sector": "Energy"},
    "MQG.AX":  {"name": "Macquarie Group",         "cat": "ASX", "sector": "Finance"},
    "SUN.AX":  {"name": "Suncorp Group",           "cat": "ASX", "sector": "Insurance"},
    "QBE.AX":  {"name": "QBE Insurance",           "cat": "ASX", "sector": "Insurance"},
    "AMP.AX":  {"name": "AMP Limited",             "cat": "ASX", "sector": "Finance"},
    "CSL.AX":  {"name": "CSL Limited",             "cat": "ASX", "sector": "Healthcare"},
    "COH.AX":  {"name": "Cochlear",                "cat": "ASX", "sector": "Healthcare"},
    "RMD.AX":  {"name": "ResMed",                  "cat": "ASX", "sector": "Healthcare"},
    "PME.AX":  {"name": "Pro Medicus",             "cat": "ASX", "sector": "Healthcare"},
    "WES.AX":  {"name": "Wesfarmers",              "cat": "ASX", "sector": "Consumer"},
    "WOW.AX":  {"name": "Woolworths Group",        "cat": "ASX", "sector": "Consumer"},
    "COL.AX":  {"name": "Coles Group",             "cat": "ASX", "sector": "Consumer"},
    "JBH.AX":  {"name": "JB Hi-Fi",               "cat": "ASX", "sector": "Consumer"},
    "TWE.AX":  {"name": "Treasury Wine Estates",   "cat": "ASX", "sector": "Consumer"},
    "REA.AX":  {"name": "REA Group",               "cat": "ASX", "sector": "Technology"},
    "XRO.AX":  {"name": "Xero",                    "cat": "ASX", "sector": "Technology"},
    "WTC.AX":  {"name": "WiseTech Global",         "cat": "ASX", "sector": "Technology"},
    "ALU.AX":  {"name": "Altium",                  "cat": "ASX", "sector": "Technology"},
    "GMG.AX":  {"name": "Goodman Group",           "cat": "ASX", "sector": "REIT"},
    "SCG.AX":  {"name": "Scentre Group",           "cat": "ASX", "sector": "REIT"},
    "GPT.AX":  {"name": "GPT Group",               "cat": "ASX", "sector": "REIT"},
    "QAN.AX":  {"name": "Qantas Airways",          "cat": "ASX", "sector": "Transport"},
    "TCL.AX":  {"name": "Transurban Group",        "cat": "ASX", "sector": "Infrastructure"},
    "TLS.AX":  {"name": "Telstra",                 "cat": "ASX", "sector": "Telecom"},
    "NCM.AX":  {"name": "Newcrest Mining",         "cat": "ASX", "sector": "Gold"},
    "EVN.AX":  {"name": "Evolution Mining",        "cat": "ASX", "sector": "Gold"},
    "NST.AX":  {"name": "Northern Star Resources", "cat": "ASX", "sector": "Gold"},
    # Crypto
    "BTC-USD":  {"name": "Bitcoin",         "cat": "Crypto", "sector": "Layer 1"},
    "ETH-USD":  {"name": "Ethereum",        "cat": "Crypto", "sector": "Layer 1"},
    "BNB-USD":  {"name": "BNB",             "cat": "Crypto", "sector": "Exchange"},
    "SOL-USD":  {"name": "Solana",          "cat": "Crypto", "sector": "Layer 1"},
    "XRP-USD":  {"name": "XRP",             "cat": "Crypto", "sector": "Payments"},
    "ADA-USD":  {"name": "Cardano",         "cat": "Crypto", "sector": "Layer 1"},
    "AVAX-USD": {"name": "Avalanche",       "cat": "Crypto", "sector": "Layer 1"},
    "DOT-USD":  {"name": "Polkadot",        "cat": "Crypto", "sector": "Layer 0"},
    "LINK-USD": {"name": "Chainlink",       "cat": "Crypto", "sector": "Oracle"},
    "MATIC-USD":{"name": "Polygon",         "cat": "Crypto", "sector": "Layer 2"},
    "DOGE-USD": {"name": "Dogecoin",        "cat": "Crypto", "sector": "Meme"},
    "LTC-USD":  {"name": "Litecoin",        "cat": "Crypto", "sector": "Payments"},
    "UNI-USD":  {"name": "Uniswap",         "cat": "Crypto", "sector": "DeFi"},
    "ATOM-USD": {"name": "Cosmos",          "cat": "Crypto", "sector": "Interop"},
    "NEAR-USD": {"name": "NEAR Protocol",   "cat": "Crypto", "sector": "Layer 1"},
    "FTM-USD":  {"name": "Fantom",          "cat": "Crypto", "sector": "Layer 1"},
    "ALGO-USD": {"name": "Algorand",        "cat": "Crypto", "sector": "Layer 1"},
    "XLM-USD":  {"name": "Stellar",         "cat": "Crypto", "sector": "Payments"},
    "AAVE-USD": {"name": "Aave",            "cat": "Crypto", "sector": "DeFi"},
    "SNX-USD":  {"name": "Synthetix",       "cat": "Crypto", "sector": "DeFi"},
    # AU Commodities
    "PMGOLD.AX": {"name": "Perth Mint Gold",       "cat": "Commodity", "sector": "Precious Metals"},
    "QAU.AX":    {"name": "BetaShares Gold ETF",   "cat": "Commodity", "sector": "Precious Metals"},
    "GOLD.AX":   {"name": "Gold Bullion ETF",      "cat": "Commodity", "sector": "Precious Metals"},
    "OOO.AX":    {"name": "BetaShares Crude Oil",  "cat": "Commodity", "sector": "Energy"},
    "QCB.AX":    {"name": "BetaShares Commodities","cat": "Commodity", "sector": "Broad"},
    "OZL.AX":    {"name": "OZ Minerals (Copper)",  "cat": "Commodity", "sector": "Base Metals"},
    "PDN.AX":    {"name": "Paladin Energy",        "cat": "Commodity", "sector": "Uranium"},
    "BMN.AX":    {"name": "Bannerman Energy",      "cat": "Commodity", "sector": "Uranium"},
    "LOT.AX":    {"name": "Lotus Resources",       "cat": "Commodity", "sector": "Uranium"},
    "DYL.AX":    {"name": "Deep Yellow",           "cat": "Commodity", "sector": "Uranium"},
    "PEN.AX":    {"name": "Peninsula Energy",      "cat": "Commodity", "sector": "Uranium"},
    "AKE.AX":    {"name": "Allkem (Lithium)",      "cat": "Commodity", "sector": "Lithium"},
    "CXO.AX":    {"name": "Core Lithium",          "cat": "Commodity", "sector": "Lithium"},
    "GL1.AX":    {"name": "Global Lithium",        "cat": "Commodity", "sector": "Lithium"},
    "SYA.AX":    {"name": "Sayona Mining",         "cat": "Commodity", "sector": "Lithium"},
    "ARU.AX":    {"name": "Arafura Rare Earths",   "cat": "Commodity", "sector": "Rare Earths"},
    "VML.AX":    {"name": "Vital Metals",          "cat": "Commodity", "sector": "Rare Earths"},
    "HAS.AX":    {"name": "Hastings Technology",   "cat": "Commodity", "sector": "Rare Earths"},
    "GC=F":      {"name": "Gold Futures",           "cat": "Commodity", "sector": "Precious Metals"},
    "SI=F":      {"name": "Silver Futures",         "cat": "Commodity", "sector": "Precious Metals"},
    "CL=F":      {"name": "Crude Oil Futures",      "cat": "Commodity", "sector": "Energy"},
    "BZ=F":      {"name": "Brent Crude Futures",    "cat": "Commodity", "sector": "Energy"},
    "NG=F":      {"name": "Natural Gas Futures",    "cat": "Commodity", "sector": "Energy"},
}


# ── Scanner cache ──────────────────────────────────────
_scanner_cache: dict = {}   # market -> {"ts": float, "rows": list}
_CACHE_TTL = 90             # seconds


# ── CoinGecko maps ─────────────────────────────────────
_COINGECKO_MAP = {
    "BTC-USD": "bitcoin",      "ETH-USD": "ethereum",       "BNB-USD": "binancecoin",
    "SOL-USD": "solana",       "XRP-USD": "ripple",          "ADA-USD": "cardano",
    "AVAX-USD":"avalanche-2",  "DOT-USD": "polkadot",        "LINK-USD":"chainlink",
    "MATIC-USD":"matic-network","DOGE-USD":"dogecoin",       "LTC-USD": "litecoin",
    "UNI-USD": "uniswap",      "ATOM-USD":"cosmos",          "NEAR-USD":"near",
    "FTM-USD": "fantom",       "ALGO-USD":"algorand",        "XLM-USD": "stellar",
    "AAVE-USD":"aave",         "SNX-USD": "havven",
}

_CG_SYMBOL_MAP: dict = {
    "BTC-USD":"bitcoin","ETH-USD":"ethereum","BNB-USD":"binancecoin",
    "SOL-USD":"solana","XRP-USD":"ripple","ADA-USD":"cardano",
    "AVAX-USD":"avalanche-2","DOT-USD":"polkadot","TRX-USD":"tron",
    "LTC-USD":"litecoin","ATOM-USD":"cosmos","NEAR-USD":"near",
    "ALGO-USD":"algorand","XLM-USD":"stellar","VET-USD":"vechain",
    "ICP-USD":"internet-computer","HBAR-USD":"hedera-hashgraph",
    "FIL-USD":"filecoin","EOS-USD":"eos","XTZ-USD":"tezos",
    "NEO-USD":"neo","IOTA-USD":"iota","XMR-USD":"monero",
    "ZEC-USD":"zcash","DASH-USD":"dash","WAVES-USD":"waves",
    "MATIC-USD":"matic-network","ARB-USD":"arbitrum","OP-USD":"optimism",
    "IMX-USD":"immutable-x","LRC-USD":"loopring","APT-USD":"aptos",
    "SUI-USD":"sui","INJ-USD":"injective-protocol","SEI-USD":"sei-network",
    "TIA-USD":"celestia","PYTH-USD":"pyth-network","JUP-USD":"jupiter-exchange-solana",
    "UNI-USD":"uniswap","AAVE-USD":"aave","MKR-USD":"maker",
    "COMP-USD":"compound-governance-token","YFI-USD":"yearn-finance",
    "SUSHI-USD":"sushi","1INCH-USD":"1inch","CRV-USD":"curve-dao-token",
    "BAL-USD":"balancer","DYDX-USD":"dydx","GMX-USD":"gmx",
    "SNX-USD":"synthetix-network-token","PENDLE-USD":"pendle",
    "CAKE-USD":"pancakeswap-token","CVX-USD":"convex-finance",
    "FXS-USD":"frax-share","LDO-USD":"lido-dao","RPL-USD":"rocket-pool",
    "ANKR-USD":"ankr","SAND-USD":"the-sandbox","MANA-USD":"decentraland",
    "ENJ-USD":"enjincoin","AXS-USD":"axie-infinity","GALA-USD":"gala",
    "FLOW-USD":"flow","BEAM-USD":"beam-2","RONIN-USD":"ronin",
    "DOGE-USD":"dogecoin","SHIB-USD":"shiba-inu","PEPE-USD":"pepe",
    "FLOKI-USD":"floki","BONK-USD":"bonk","WIF-USD":"dogwifcoin",
    "LINK-USD":"chainlink","BAND-USD":"band-protocol","TRB-USD":"tellor",
    "AR-USD":"arweave","STORJ-USD":"storj","SCRT-USD":"secret",
    "ROSE-USD":"oasis-network","RUNE-USD":"thorchain","AXL-USD":"axelar",
    "FET-USD":"fetch-ai","AGIX-USD":"singularitynet","OCEAN-USD":"ocean-protocol",
    "NMR-USD":"numeraire","TAO-USD":"bittensor","RNDR-USD":"render-token",
    "WLD-USD":"worldcoin-wld","CRO-USD":"crypto-com-chain",
    "KCS-USD":"kucoin-shares","BAT-USD":"basic-attention-token",
    "ZRX-USD":"0x","GRT-USD":"the-graph","LPT-USD":"livepeer",
    "ONDO-USD":"ondo-finance","THETA-USD":"theta-token","CHZ-USD":"chiliz",
    "MINA-USD":"mina-protocol","KAVA-USD":"kava","CFX-USD":"conflux-token",
    "FTM-USD":"fantom","OMG-USD":"omisego","METIS-USD":"metis-token",
    "SKL-USD":"skale","ICX-USD":"icon","ONT-USD":"ontology",
    "QTUM-USD":"qtum","ZIL-USD":"zilliqa","VET-USD":"vechain",
    "HOT-USD":"holotoken","WIN-USD":"wink","REEF-USD":"reef",
    "JASMY-USD":"jasmycoin","API3-USD":"api3","CELR-USD":"celer-network",
}


async def _get_crypto_coingecko() -> Optional[dict]:
    """Fetch real crypto prices from CoinGecko free API (no API key needed).
    Returns both USD and AUD prices for CoinSpot trade execution."""
    from api.utils import _to_trade_ticker
    key = "coingecko_prices"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    ids = ",".join(_COINGECKO_MAP.values())
    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        f"?ids={ids}&vs_currencies=usd,aud&include_24hr_change=true"
    )
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=8),
            headers={"User-Agent": "DALIOS/1.0"},
        ) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        rev = {v: k for k, v in _COINGECKO_MAP.items()}
        result = {}
        for cid, vals in data.items():
            if cid not in rev:
                continue
            usd_ticker = rev[cid]
            result[usd_ticker] = {
                "price":      vals.get("usd"),
                "price_aud":  vals.get("aud"),
                "change_pct": round(vals.get("usd_24h_change") or 0, 2),
                "source":     "CoinGecko",
            }
            # Also store under -AUD ticker for CoinSpot lookups
            aud_ticker = _to_trade_ticker(usd_ticker)
            result[aud_ticker] = {
                "price":      vals.get("aud"),
                "change_pct": round(vals.get("usd_24h_change") or 0, 2),
                "source":     "CoinGecko",
            }
        if result:
            _cache_set(key, result)
        return result or None
    except Exception as exc:
        logger.warning(f"CoinGecko error: {exc}")
        return None


# ── Binance price feed ──────────────────────────────────
_BINANCE_PRICE_CACHE: dict = {}
_BINANCE_PRICE_TS: float = 0.0
_BINANCE_PRICE_TTL: float = 20.0   # 20-second cache


async def _fetch_binance_prices() -> dict:
    """Fetch all USDT spot prices from Binance public REST -- no API key needed."""
    global _BINANCE_PRICE_CACHE, _BINANCE_PRICE_TS
    now = time.time()
    if now - _BINANCE_PRICE_TS < _BINANCE_PRICE_TTL and _BINANCE_PRICE_CACHE:
        return _BINANCE_PRICE_CACHE

    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get("https://api.binance.com/api/v3/ticker/price") as resp:
                if resp.status != 200:
                    return _BINANCE_PRICE_CACHE
                data = await resp.json(content_type=None)
        result = {}
        for item in data:
            sym = item["symbol"]  # e.g. "BTCUSDT"
            if sym.endswith("USDT"):
                base = sym[:-4]   # "BTC"
                result[f"{base}-USD"] = float(item["price"])
        _BINANCE_PRICE_CACHE = result
        _BINANCE_PRICE_TS = now
        logger.debug(f"Binance price feed: {len(result)} USDT pairs cached")
        return result
    except Exception as e:
        logger.warning(f"Binance price feed failed: {e}")
        return _BINANCE_PRICE_CACHE


async def _live_price(ticker: str) -> Optional[float]:
    """Get the most recent price for a ticker.
    Priority: scanner cache -> Binance (crypto) -> yfinance -> demo seed.
    """
    # 1. Scanner cache (fastest -- already in memory)
    cached_ms = _cache_get("market_summary")
    if cached_ms:
        for item in cached_ms:
            if item.get("ticker") == ticker and item.get("price") is not None:
                return float(item["price"])

    # 2. Binance public API for crypto (fast, real-time, no auth needed)
    if ticker.endswith("-USD"):
        bn_prices = await _fetch_binance_prices()
        if ticker in bn_prices:
            return bn_prices[ticker]

    # 3. yfinance fallback
    prices = await _get_prices([ticker], "5d")
    if prices and ticker in prices and prices[ticker]:
        return float(prices[ticker][-1])

    # 4. Demo seed (never None -- prevents order failure on unknown tickers)
    seed = abs(hash(ticker)) % 10000
    rng = random.Random(seed)
    return round(rng.uniform(10, 300), 2)


async def _prices_for_positions(tickers: list) -> dict:
    """Return {ticker: price} for all open position tickers."""
    if not tickers:
        return {}
    result = {}

    # 1. Batch-fetch crypto prices from Binance
    crypto_tickers = [t for t in tickers if t.endswith("-USD") or t.endswith("-AUD")]
    if crypto_tickers:
        bn_prices = await _fetch_binance_prices()
        for t in crypto_tickers:
            lookup = t.replace("-AUD", "-USD") if t.endswith("-AUD") else t
            if lookup in bn_prices:
                result[t] = bn_prices[lookup]

    # 2. Batch-fetch remaining tickers via yfinance download
    remaining = [t for t in tickers if t not in result]
    if remaining and YF_AVAILABLE:
        try:
            loop = asyncio.get_running_loop()

            def _batch_yf():
                import yfinance as _yf_batch
                try:
                    raw = _yf_batch.download(
                        remaining if len(remaining) > 1 else remaining[0],
                        period="5d", auto_adjust=True, progress=False,
                        threads=True, timeout=10,
                    )
                    if raw is None or raw.empty:
                        return {}
                    import pandas as _pd_batch
                    if isinstance(raw.columns, _pd_batch.MultiIndex):
                        close = raw["Close"] if "Close" in raw.columns.get_level_values(0) else None
                    elif "Close" in raw.columns:
                        close = _pd_batch.DataFrame({remaining[0]: raw["Close"]})
                    else:
                        return {}
                    if close is None or close.empty:
                        return {}
                    prices = {}
                    for t in remaining:
                        if t in close.columns:
                            col = close[t].dropna()
                            if not col.empty:
                                prices[t] = float(col.iloc[-1])
                    return prices
                except Exception:
                    return {}

            batch_prices = await asyncio.wait_for(
                loop.run_in_executor(_EXECUTOR, _batch_yf), timeout=12.0)
            result.update(batch_prices)
        except (asyncio.TimeoutError, Exception):
            pass

    # 3. Individual fallback for any tickers still missing
    still_missing = [t for t in tickers if t not in result]
    for t in still_missing:
        p = await _live_price(t)
        if p is not None:
            result[t] = p
    return result


# ── Scanner functions ───────────────────────────────────

async def _scan_yfinance(tickers: list, market: str) -> list:
    """Fetch OHLCV for non-crypto markets."""
    if not YF_AVAILABLE:
        return []
    await SOURCE_LIMITER.acquire("yfinance")
    try:
        return await _scan_yfinance_inner(tickers, market)
    finally:
        SOURCE_LIMITER.release("yfinance")


async def _scan_yfinance_inner(tickers: list, market: str) -> list:
    import yfinance as yf
    loop = asyncio.get_running_loop()
    results: dict = {}

    def _bulk():
        try:
            raw = yf.download(
                tickers, period="5d", interval="1d",
                group_by="ticker", auto_adjust=True,
                progress=False, threads=True,
            )
            return raw
        except Exception as exc:
            logger.warning(f"yfinance bulk failed [{market}]: {exc}")
            return None

    raw = await loop.run_in_executor(None, _bulk)

    if raw is not None and not raw.empty:
        multi = len(tickers) > 1
        for ticker in tickers:
            try:
                if multi:
                    if hasattr(raw.columns, "levels"):
                        if ticker in raw.columns.get_level_values(1):
                            df = raw.xs(ticker, axis=1, level=1).dropna(subset=["Close"])
                        elif ticker in raw.columns.get_level_values(0):
                            df = raw[ticker].dropna(subset=["Close"])
                        else:
                            continue
                    else:
                        df = raw[ticker].dropna(subset=["Close"])
                else:
                    df = raw.dropna(subset=["Close"])

                if len(df) < 2:
                    continue
                price = float(df["Close"].iloc[-1])
                prev = float(df["Close"].iloc[-2])
                chg_pct = (price - prev) / prev * 100 if prev else 0
                vol = float(df["Volume"].iloc[-1]) if "Volume" in df.columns else 0
                results[ticker] = (price, chg_pct, vol)
            except Exception as exc:
                logger.debug(f"Bulk parse [{ticker}]: {exc}")

    # Individual fallback for tickers bulk missed
    missing = [t for t in tickers if t not in results]
    if missing:
        logger.info(f"[{market}] individual fallback for {len(missing)} tickers")

        def _fetch_one(tkr):
            try:
                hist = yf.Ticker(tkr).history(period="5d", interval="1d", auto_adjust=True)
                hist = hist.dropna(subset=["Close"])
                if len(hist) < 2:
                    return None
                price = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2])
                chg_pct = (price - prev) / prev * 100 if prev else 0
                vol = float(hist["Volume"].iloc[-1]) if "Volume" in hist.columns else 0
                return (price, chg_pct, vol)
            except Exception:
                return None

        futures = [loop.run_in_executor(None, _fetch_one, t) for t in missing]
        ind_results = await asyncio.gather(*futures)
        for ticker, val in zip(missing, ind_results):
            if val is not None:
                results[ticker] = val

    # Build rows
    rows = []
    for ticker in tickers:
        meta = _ASSET_META.get(ticker, {"name": ticker, "sector": "--"})
        if ticker not in results:
            continue
        price, chg_pct, vol = results[ticker]
        rows.append({
            "ticker":       ticker,
            "name":         meta.get("name", ticker),
            "sector":       meta.get("sector", "--"),
            "price":        round(price, 4),
            "change":       round(price * chg_pct / 100, 4),
            "change_pct":   round(chg_pct, 2),
            "volume_fmt":   _fmt_vol(vol),
            "volume":       int(vol),
            "in_watchlist": ticker in WATCHLIST,
        })

    logger.info(f"yfinance [{market}]: {len(rows)}/{len(tickers)} tickers")
    return rows


async def _scan_coingecko(tickers: list) -> list:
    """Fetch crypto prices: CoinGecko free API first, yfinance fallback."""
    await SOURCE_LIMITER.acquire("coingecko")
    try:
        return await _scan_coingecko_inner(tickers)
    finally:
        SOURCE_LIMITER.release("coingecko")


async def _scan_coingecko_inner(tickers: list) -> list:
    cg_ids: list = []
    id_to_ticker: dict = {}
    for t in tickers:
        cg_id = _CG_SYMBOL_MAP.get(t)
        if cg_id:
            cg_ids.append(cg_id)
            id_to_ticker[cg_id] = t

    all_coin_data: dict = {}

    if cg_ids:
        try:
            connector = aiohttp.TCPConnector(ssl=True, limit=5)
            timeout = aiohttp.ClientTimeout(total=20)
            headers = {"User-Agent": "DALIOS/1.0", "Accept": "application/json"}
            async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
                chunk_size = 100
                for i in range(0, len(cg_ids), chunk_size):
                    chunk = cg_ids[i:i + chunk_size]
                    url = (
                        "https://api.coingecko.com/api/v3/coins/markets"
                        f"?vs_currency=usd&ids={','.join(chunk)}"
                        "&order=market_cap_desc&per_page=250&page=1"
                        "&price_change_percentage=24h&sparkline=false"
                    )
                    try:
                        async with session.get(url) as resp:
                            if resp.status == 200:
                                data = await resp.json(content_type=None)
                                for coin in data:
                                    all_coin_data[coin["id"]] = coin
                            elif resp.status == 429:
                                logger.warning("CoinGecko rate-limited (429) -- using yfinance fallback")
                                break
                            else:
                                logger.warning(f"CoinGecko HTTP {resp.status}")
                    except Exception as exc:
                        logger.warning(f"CoinGecko chunk error: {exc}")
        except Exception as exc:
            logger.warning(f"CoinGecko session error: {exc}")

    rows = []
    found_tickers: set = set()

    for cg_id, ticker in id_to_ticker.items():
        coin = all_coin_data.get(cg_id, {})
        price = float(coin.get("current_price") or 0)
        if price <= 0:
            continue
        found_tickers.add(ticker)
        chg_pct = float(coin.get("price_change_percentage_24h") or 0)
        vol = float(coin.get("total_volume") or 0)
        mkt_cap = float(coin.get("market_cap") or 0)
        rows.append({
            "ticker":          ticker,
            "name":            coin.get("name", ticker.replace("-USD", "")),
            "sector":          "Crypto",
            "price":           round(price, 6) if price < 1 else round(price, 2),
            "change":          round(price * chg_pct / 100, 6),
            "change_pct":      round(chg_pct, 2),
            "volume_fmt":      _fmt_vol(vol),
            "volume":          int(vol),
            "market_cap_fmt":  _fmt_vol(mkt_cap),
            "in_watchlist":    ticker in WATCHLIST,
        })

    # yfinance fallback for tickers CoinGecko missed
    missing = [t for t in tickers if t not in found_tickers]
    if missing:
        logger.info(f"CoinGecko missed {len(missing)} tickers -- yfinance fallback")
        yf_rows = await _scan_yfinance(missing, "crypto")
        for r in yf_rows:
            r.setdefault("market_cap_fmt", "--")
            r["sector"] = "Crypto"
            rows.append(r)

    logger.info(f"Crypto scan: {len(found_tickers)} CoinGecko + {len(missing)} yfinance = {len(rows)} rows")
    return rows


# ── Market summary demo data ───────────────────────────
_MARKET_DEMO = [
    ("BTC-USD",  "Bitcoin",       "crypto",     95_420.0,   2.14),
    ("ETH-USD",  "Ethereum",      "crypto",      3_512.5,   1.87),
    ("SOL-USD",  "Solana",        "crypto",       178.40,   4.31),
    ("BNB-USD",  "BNB",           "crypto",       612.00,   1.05),
    ("XRP-USD",  "XRP",           "crypto",         0.62,   3.20),
    ("ADA-USD",  "Cardano",       "crypto",         0.45,  -1.10),
    ("DOGE-USD", "Dogecoin",      "crypto",         0.082,  5.40),
    ("AVAX-USD", "Avalanche",     "crypto",        38.50,   2.80),
    ("DOT-USD",  "Polkadot",      "crypto",         7.20,  -0.95),
    ("LINK-USD", "Chainlink",     "crypto",        15.80,   1.65),
    ("MATIC-USD","Polygon",       "crypto",         0.72,  -1.40),
    ("ATOM-USD", "Cosmos",        "crypto",         9.10,   0.85),
    ("LTC-USD",  "Litecoin",      "crypto",        85.40,   0.52),
    ("UNI-USD",  "Uniswap",       "crypto",         7.90,   2.10),
    ("NEAR-USD", "NEAR",          "crypto",         5.60,   3.15),
    ("^AXJO",    "ASX 200",       "index",       7_985.0,   0.42),
    ("CBA.AX",   "CommBank",      "asx",          145.20,   0.72),
    ("BHP.AX",   "BHP Group",     "asx",           42.80,  -0.33),
    ("CSL.AX",   "CSL Ltd",       "asx",          285.60,   1.15),
    ("NAB.AX",   "NAB",           "asx",           38.50,   0.45),
    ("WBC.AX",   "Westpac",       "asx",           28.90,  -0.18),
    ("ANZ.AX",   "ANZ Bank",      "asx",           30.15,   0.62),
    ("FMG.AX",   "Fortescue",     "asx",           18.40,  -1.80),
    ("RIO.AX",   "Rio Tinto",     "asx",          115.30,  -0.55),
    ("WDS.AX",   "Woodside",      "asx",           26.70,   0.90),
    ("WES.AX",   "Wesfarmers",    "asx",           72.40,   0.35),
    ("MQG.AX",   "Macquarie",     "asx",          198.50,   1.20),
    ("TLS.AX",   "Telstra",       "asx",            3.95,  -0.25),
    ("^GSPC",    "S&P 500",       "index",       5_674.0,  -0.31),
    ("^DJI",     "Dow Jones",     "index",      42_150.0,   0.18),
    ("^IXIC",    "Nasdaq",        "index",      18_320.0,  -0.45),
    ("^N225",    "Nikkei 225",    "index",      38_750.0,   0.55),
    ("^FTSE",    "FTSE 100",      "index",       8_210.0,  -0.22),
    ("^VIX",     "VIX Fear",      "index",         18.4,   -3.20),
    ("AUD=X",    "AUD/USD",       "fx",            0.6312,  0.18),
    ("EURUSD=X", "EUR/USD",       "fx",            1.0845,  0.12),
    ("GC=F",     "Gold Futures",  "commodity",   2_380.0,   0.48),
    ("SI=F",     "Silver Futures","commodity",      28.60,   0.92),
    ("CL=F",     "Crude Oil WTI", "commodity",     78.50,  -0.65),
    ("NG=F",     "Natural Gas",   "commodity",      2.15,  -2.30),
    ("GLD",      "Gold ETF",      "commodity",    241.30,   0.65),
    ("SLV",      "Silver ETF",    "commodity",     28.40,   0.88),
    ("USO",      "Oil ETF",       "commodity",     74.85,  -0.55),
    ("PPLT",     "Platinum ETF",  "commodity",     92.30,   0.40),
    ("COPX",     "Copper Miners", "commodity",     38.70,   1.10),
    ("URA",      "Uranium ETF",   "commodity",     28.90,   2.35),
    ("WEAT",     "Wheat ETF",     "commodity",      5.80,  -0.70),
    ("DBA",      "Agriculture ETF","commodity",    25.40,   0.15),
]
