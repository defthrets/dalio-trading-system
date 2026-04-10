"""
Dalios -- Market Scanning
Scanner cache, ticker universes, market data fetching (ASX, commodities),
market summary, live price lookups.
"""

import asyncio
import random
import numpy as np
from datetime import datetime
from typing import Optional

from loguru import logger

from api.utils import (
    _cache_get, _cache_set, _get_prices, _fmt_vol, _EXECUTOR,
    YF_AVAILABLE, SOURCE_LIMITER,
)
from api.state import WATCHLIST


# ── Ticker Universes ────────────────────────────────────

ASX_TICKERS = [
    # -- Big 4 Banks --
    "CBA.AX", "WBC.AX", "ANZ.AX", "NAB.AX",
    # -- Other Banks & Financials --
    "MQG.AX", "BEN.AX", "BOQ.AX", "SUN.AX", "QBE.AX", "IAG.AX",
    "AMP.AX", "ASX.AX", "PPT.AX", "CGF.AX", "CPU.AX", "NHF.AX",
    "MPL.AX", "NIB.AX", "AUB.AX", "HUB.AX", "MFG.AX", "PTM.AX",
    "GQG.AX", "PDL.AX", "EQT.AX", "IFL.AX", "CIP.AX", "AFG.AX",
    "MAB.AX", "SDF.AX",
    # -- Mining & Resources (Majors) --
    "BHP.AX", "RIO.AX", "FMG.AX", "S32.AX", "MIN.AX", "LYC.AX",
    "IGO.AX", "SFR.AX", "PLS.AX", "ILU.AX", "AWC.AX",
    "LTR.AX", "NIC.AX", "WSA.AX", "CIA.AX", "MGX.AX",
    "GRR.AX", "CLQ.AX", "CHN.AX", "TNG.AX",
    # -- Mining Juniors --
    "NHC.AX", "WHC.AX", "CRN.AX", "BCB.AX", "TER.AX",
    "RED.AX", "SLR.AX", "OGC.AX", "BGL.AX", "RXL.AX",
    "MEU.AX", "AIS.AX", "IPT.AX", "GWR.AX", "SBR.AX",
    # -- Gold & Precious Metals --
    "NST.AX", "EVN.AX", "SBM.AX", "RRL.AX", "SAR.AX",
    "GOR.AX", "CMM.AX", "RMS.AX", "DEG.AX", "WAF.AX",
    "MML.AX", "RSG.AX", "PRU.AX", "SDG.AX", "BDC.AX",
    "SKY.AX", "MAU.AX",
    # -- Lithium & Battery Metals --
    "AKE.AX", "SYA.AX", "CXO.AX", "GL1.AX",
    "AVZ.AX", "LKE.AX", "PLL.AX", "EUR.AX",
    "DEL.AX", "NVX.AX", "EV1.AX", "LAT.AX",
    # -- Uranium --
    "PDN.AX", "BOE.AX", "NXE.AX", "ERA.AX",
    "92E.AX", "AGE.AX", "SLX.AX", "PEN.AX",
    # -- Rare Earths --
    "ARU.AX", "VML.AX", "HAS.AX",
    "REE.AX", "NTU.AX", "ASM.AX", "HLX.AX",
    # -- Energy --
    "WDS.AX", "STO.AX", "BPT.AX", "AGL.AX", "ORG.AX",
    "APA.AX", "KAR.AX", "CVN.AX", "STX.AX", "MEL.AX",
    "COE.AX", "NHE.AX", "TAP.AX", "OPT.AX",
    # -- Healthcare & Biotech --
    "CSL.AX", "RMD.AX", "COH.AX", "SHL.AX", "ANN.AX",
    "PME.AX", "EBO.AX", "HLS.AX", "PNV.AX", "RHC.AX",
    "CUV.AX", "NEU.AX", "TLX.AX", "MSB.AX", "AVH.AX",
    "IMM.AX", "PXA.AX", "NAN.AX", "ACL.AX", "RAC.AX",
    "PRO.AX", "MX1.AX", "OSL.AX", "EMV.AX",
    # -- Technology --
    "WTC.AX", "XRO.AX", "ALU.AX", "MP1.AX", "TNE.AX",
    "REA.AX", "APX.AX", "TYR.AX", "SDR.AX", "DTL.AX",
    "NXT.AX", "DUB.AX", "LNW.AX", "STP.AX", "RDY.AX",
    "BVS.AX", "OTW.AX", "AR9.AX",
    # -- Fintech --
    "ZIP.AX", "EML.AX", "APT.AX", "SPT.AX", "MNY.AX",
    "LBL.AX", "MYS.AX", "PGL.AX", "ABA.AX", "FIN.AX",
    # -- Cybersecurity --
    "TNT.AX", "RNO.AX", "PKS.AX",
    # -- Consumer / Retail --
    "WES.AX", "WOW.AX", "COL.AX", "JBH.AX", "TWE.AX",
    "HVN.AX", "DMP.AX", "SUL.AX", "LOV.AX", "KGN.AX",
    "TPW.AX", "MYR.AX", "NCK.AX", "BBN.AX", "UNI.AX",
    "PFP.AX", "BCF.AX", "TRS.AX", "ADH.AX",
    # -- Consumer Staples & Food --
    "GNC.AX", "NUF.AX", "ELD.AX", "BKL.AX",
    "CGC.AX", "TGR.AX", "HUO.AX", "SKC.AX",
    # -- Cannabis --
    "CPH.AX", "BOD.AX", "CAN.AX", "LEGA.AX", "CUP.AX",
    # -- REITs --
    "GMG.AX", "SCG.AX", "GPT.AX", "VCX.AX", "CLW.AX",
    "MGR.AX", "DXS.AX", "CHC.AX", "BWP.AX", "NSR.AX",
    "CQR.AX", "HMC.AX", "ABP.AX", "SCP.AX", "HDN.AX",
    "URW.AX", "AOF.AX", "CNI.AX", "GDI.AX", "PPC.AX",
    "RGN.AX", "ARF.AX", "CRF.AX",
    # -- Industrials & Infrastructure --
    "TCL.AX", "QAN.AX", "BXB.AX", "AZJ.AX", "QUB.AX",
    "WOR.AX", "MND.AX", "JHX.AX", "CSR.AX", "BLD.AX",
    "DOW.AX", "SVW.AX", "ALQ.AX", "NWH.AX", "SXE.AX",
    "CIM.AX", "IDR.AX", "RWC.AX", "LGL.AX",
    # -- Telecom --
    "TLS.AX", "TPG.AX", "SPK.AX", "NXL.AX", "PPS.AX",
    # -- Media --
    "NWS.AX", "SEK.AX", "CAR.AX", "NEC.AX", "SWM.AX",
    "MMS.AX", "OML.AX", "AHG.AX",
    # -- LICs & ASX ETFs --
    "VAS.AX", "VGS.AX", "IOZ.AX", "STW.AX", "NDQ.AX",
    "A200.AX", "GOLD.AX", "ETHI.AX", "IVV.AX", "IAA.AX",
    "VHY.AX", "SFY.AX", "MVW.AX", "TECH.AX", "HACK.AX",
    "SEMI.AX", "RBTZ.AX", "ACDC.AX", "ERTH.AX",
    # -- Diversified / LICs --
    "AFI.AX", "ARG.AX", "MLT.AX", "WAM.AX",
    "WHF.AX", "MIR.AX", "AMH.AX", "PIC.AX",
]

CRYPTO_TICKERS = []  # Crypto removed -- ASX + commodities only

COMMODITY_TICKERS = [
    # -- ASX Precious Metal ETFs & Producers --
    "PMGOLD.AX", "QAU.AX", "MNRS.AX", "GOLD.AX",
    # -- ASX Crude Oil & Energy ETFs --
    "OOO.AX", "FUEL.AX",
    # -- ASX Broad Commodity ETFs --
    "QCB.AX", "COMB.AX",
    # -- ASX Copper & Base Metals --
    "OZL.AX", "29M.AX", "SFR.AX",
    # -- ASX Iron Ore --
    "BHP.AX", "FMG.AX", "RIO.AX", "MIN.AX", "GRR.AX", "MGX.AX",
    # -- ASX Uranium --
    "BMN.AX", "LOT.AX", "DYL.AX",
    "BKY.AX", "TOE.AX", "GTR.AX", "PDN.AX", "BOE.AX",
    # -- ASX Lithium & Battery --
    "PLS.AX", "LTR.AX", "IGO.AX", "SYA.AX", "CXO.AX", "GL1.AX",
    # -- ASX Rare Earths --
    "LYC.AX", "ARU.AX", "VML.AX", "HAS.AX", "NTU.AX",
    # -- ASX Nickel & Cobalt --
    "NIC.AX", "WSA.AX",
    # -- ASX Agriculture --
    "FOOD.AX", "QAG.AX", "GNC.AX", "NUF.AX", "ELD.AX",
    "AAC.AX", "TGR.AX", "CGC.AX",
    # -- ASX Coal --
    "WHC.AX", "NHC.AX", "CRN.AX",
    # -- ASX Aluminium --
    "AWC.AX", "S32.AX",
]

ALL_TICKERS = ASX_TICKERS + COMMODITY_TICKERS
CORR_TICKERS = ASX_TICKERS  # Use ASX for correlation heatmap

# ── Dynamic ASX full universe (~1,900 companies) ──────────
_ASX_FULL_UNIVERSE: list = []  # Populated on startup

async def _fetch_asx_listed_companies() -> list:
    """Fetch the full list of ASX-listed companies from the ASX website.
    Returns list of ticker strings like 'BHP.AX'. Falls back to ASX_TICKERS."""
    global _ASX_FULL_UNIVERSE
    try:
        import aiohttp, csv, io
        url = "https://asx.api.markitdigital.com/asx-research/1.0/companies/directory/file"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    reader = csv.DictReader(io.StringIO(text))
                    tickers = []
                    for row in reader:
                        code = row.get("ASX code", row.get("Code", "")).strip()
                        if code and len(code) <= 5:
                            tickers.append(f"{code}.AX")
                    if len(tickers) > 500:
                        _ASX_FULL_UNIVERSE = sorted(set(tickers))
                        logger.info(f"Loaded {len(_ASX_FULL_UNIVERSE)} ASX companies from ASX directory")
                        return _ASX_FULL_UNIVERSE
    except Exception as e:
        logger.warning(f"Failed to fetch ASX directory: {e}")
    # Fallback: use static list
    _ASX_FULL_UNIVERSE = ASX_TICKERS[:]
    logger.info(f"Using static ASX ticker list ({len(_ASX_FULL_UNIVERSE)} tickers)")
    return _ASX_FULL_UNIVERSE

def get_asx_universe() -> list:
    """Return the full ASX universe (fetched or static fallback)."""
    return _ASX_FULL_UNIVERSE if _ASX_FULL_UNIVERSE else ASX_TICKERS


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
    # -- ASX Commodity Producers (additional) --
    "SFR.AX":    {"name": "Sandfire Resources",     "cat": "Commodity", "sector": "Copper"},
    "NIC.AX":    {"name": "Nickel Industries",      "cat": "Commodity", "sector": "Nickel"},
    "WSA.AX":    {"name": "Western Areas (Nickel)", "cat": "Commodity", "sector": "Nickel"},
    "AWC.AX":    {"name": "Alumina Limited",        "cat": "Commodity", "sector": "Aluminium"},
    "WHC.AX":    {"name": "Whitehaven Coal",        "cat": "Commodity", "sector": "Coal"},
    "NHC.AX":    {"name": "New Hope Coal",          "cat": "Commodity", "sector": "Coal"},
    "CRN.AX":    {"name": "Coronado Global Coal",   "cat": "Commodity", "sector": "Coal"},
    "GRR.AX":    {"name": "Grange Resources",       "cat": "Commodity", "sector": "Iron Ore"},
    "MGX.AX":    {"name": "Mount Gibson Iron",      "cat": "Commodity", "sector": "Iron Ore"},
    "PLS.AX":    {"name": "Pilbara Minerals",       "cat": "Commodity", "sector": "Lithium"},
    "LTR.AX":    {"name": "Liontown Resources",     "cat": "Commodity", "sector": "Lithium"},
    "IGO.AX":    {"name": "IGO Limited",            "cat": "Commodity", "sector": "Lithium/Nickel"},
    "NTU.AX":    {"name": "Northern Minerals",      "cat": "Commodity", "sector": "Rare Earths"},
    "BOE.AX":    {"name": "Boss Energy",            "cat": "Commodity", "sector": "Uranium"},
    "AAC.AX":    {"name": "Australian Agri",        "cat": "Commodity", "sector": "Agriculture"},
    "GNC.AX":    {"name": "GrainCorp",              "cat": "Commodity", "sector": "Agriculture"},
    "NUF.AX":    {"name": "Nufarm (Agri-Chem)",     "cat": "Commodity", "sector": "Agriculture"},
    "ELD.AX":    {"name": "Elders (Farming)",       "cat": "Commodity", "sector": "Agriculture"},
    "TGR.AX":    {"name": "Tassal (Salmon)",        "cat": "Commodity", "sector": "Agriculture"},
    "CGC.AX":    {"name": "Costa Group (Produce)",   "cat": "Commodity", "sector": "Agriculture"},
}


# ── Scanner cache ──────────────────────────────────────
_scanner_cache: dict = {}   # market -> {"ts": float, "rows": list}
_CACHE_TTL = 300            # 5 minutes — prices don't change that fast


async def _live_price(ticker: str) -> Optional[float]:
    """Get the most recent price for a ticker.
    Priority: scanner cache -> yfinance -> demo seed.
    """
    # 1. Scanner cache (fastest -- already in memory)
    cached_ms = _cache_get("market_summary")
    if cached_ms:
        for item in cached_ms:
            if item.get("ticker") == ticker and item.get("price") is not None:
                return float(item["price"])

    # 2. yfinance fallback
    prices = await _get_prices([ticker], "5d")
    if prices and ticker in prices and prices[ticker]:
        return float(prices[ticker][-1])

    # 3. Demo seed (never None -- prevents order failure on unknown tickers)
    seed = abs(hash(ticker)) % 10000
    rng = random.Random(seed)
    return round(rng.uniform(10, 300), 2)


async def _prices_for_positions(tickers: list) -> dict:
    """Return {ticker: price} for all open position tickers."""
    if not tickers:
        return {}
    result = {}

    # 1. Batch-fetch tickers via yfinance download
    remaining = [t for t in tickers if t not in result]
    if remaining and YF_AVAILABLE:
        try:
            loop = asyncio.get_running_loop()

            def _batch_yf():
                import yfinance as _yf_batch
                import pandas as _pd_batch
                try:
                    single = len(remaining) == 1
                    raw = _yf_batch.download(
                        remaining if not single else remaining[0],
                        period="5d", auto_adjust=True, progress=False,
                        threads=True, timeout=10,
                    )
                    if raw is None or raw.empty:
                        return {}
                    prices = {}
                    if single:
                        # Single ticker: flat columns
                        if "Close" in raw.columns:
                            col = raw["Close"].dropna()
                            if not col.empty:
                                prices[remaining[0]] = float(col.iloc[-1])
                    elif isinstance(raw.columns, _pd_batch.MultiIndex):
                        # Multi-ticker: level 0 = Price, level 1 = Ticker
                        if "Close" in raw.columns.get_level_values(0):
                            close = raw["Close"]
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
    """Fetch OHLCV for ASX and commodity markets via yfinance."""
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

    # Batch large ticker lists — run batches concurrently for speed
    BATCH_SIZE = 100
    batches = [tickers[i:i+BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
    if len(batches) > 1:
        logger.info(f"[{market}] Scanning {len(tickers)} tickers in {len(batches)} concurrent batches")

    def _bulk(batch_tickers, idx):
        try:
            raw = yf.download(
                batch_tickers, period="5d", interval="1d",
                auto_adjust=True, progress=False, threads=True,
            )
            return (batch_tickers, raw)
        except Exception as exc:
            logger.warning(f"yfinance bulk failed [{market}] batch {idx}: {exc}")
            return (batch_tickers, None)

    # Run all batches concurrently
    batch_futures = [
        loop.run_in_executor(None, _bulk, batch, i)
        for i, batch in enumerate(batches)
    ]
    batch_results = await asyncio.gather(*batch_futures)
    all_raw = [(b, r) for b, r in batch_results if r is not None]

    import pandas as _pd

    for batch, raw in all_raw:
        if raw is None or raw.empty:
            continue
        single = len(batch) == 1
        for ticker in batch:
            try:
                if single:
                    # Single ticker: columns are flat (Close, Open, High, Low, Volume)
                    df = raw.dropna(subset=["Close"])
                    if len(df) < 2:
                        continue
                    price = float(df["Close"].iloc[-1])
                    prev = float(df["Close"].iloc[-2])
                    vol = float(df["Volume"].iloc[-1]) if "Volume" in df.columns else 0
                else:
                    # Multi-ticker: default grouping = level 0 is Price, level 1 is Ticker
                    # Access via raw["Close"][ticker], raw["Volume"][ticker]
                    if not isinstance(raw.columns, _pd.MultiIndex):
                        continue
                    close_df = raw["Close"] if "Close" in raw.columns.get_level_values(0) else None
                    if close_df is None:
                        continue
                    if ticker not in close_df.columns:
                        continue
                    col = close_df[ticker].dropna()
                    if len(col) < 2:
                        continue
                    price = float(col.iloc[-1])
                    prev = float(col.iloc[-2])
                    vol_df = raw["Volume"] if "Volume" in raw.columns.get_level_values(0) else None
                    vol = float(vol_df[ticker].dropna().iloc[-1]) if vol_df is not None and ticker in vol_df.columns else 0

                chg_pct = (price - prev) / prev * 100 if prev else 0
                results[ticker] = (price, chg_pct, vol)
            except Exception as exc:
                logger.debug(f"Bulk parse [{ticker}]: {exc}")

    # Individual fallback for tickers bulk missed — cap at 30 to avoid slowdowns
    missing = [t for t in tickers if t not in results]
    if missing:
        logger.info(f"[{market}] individual fallback for {min(len(missing), 30)}/{len(missing)} tickers")
        missing = missing[:30]  # Cap to avoid massive individual fetches

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


# ── Market summary demo data ───────────────────────────
_MARKET_DEMO = [
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
    ("PMGOLD.AX","Perth Mint Gold","commodity",     24.50,   0.48),
    ("PLS.AX",   "Pilbara Lithium","commodity",      3.80,  -1.20),
    ("LYC.AX",   "Lynas Rare Earth","commodity",     7.40,   0.92),
    ("PDN.AX",   "Paladin Uranium","commodity",     12.30,   2.35),
    ("WHC.AX",   "Whitehaven Coal","commodity",      7.90,  -0.65),
    ("OOO.AX",   "Oil ETF (ASX)", "commodity",      5.60,  -0.55),
    ("S32.AX",   "South32",       "commodity",       3.20,   0.40),
]
