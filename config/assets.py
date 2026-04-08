"""
Asset universe definitions for ASX equities and global commodities.
Organized by Dalio's All Weather quadrants.
"""

# ASX Top Equities (Blue Chips + Growth)
ASX_EQUITIES = {
    "BHP.AX": {"name": "BHP Group", "sector": "Materials", "quadrant_bias": "rising_growth"},
    "CBA.AX": {"name": "Commonwealth Bank", "sector": "Financials", "quadrant_bias": "rising_growth"},
    "CSL.AX": {"name": "CSL Limited", "sector": "Healthcare", "quadrant_bias": "falling_inflation"},
    "NAB.AX": {"name": "National Australia Bank", "sector": "Financials", "quadrant_bias": "rising_growth"},
    "WBC.AX": {"name": "Westpac", "sector": "Financials", "quadrant_bias": "rising_growth"},
    "ANZ.AX": {"name": "ANZ Group", "sector": "Financials", "quadrant_bias": "rising_growth"},
    "WES.AX": {"name": "Wesfarmers", "sector": "Consumer Staples", "quadrant_bias": "falling_inflation"},
    "WOW.AX": {"name": "Woolworths", "sector": "Consumer Staples", "quadrant_bias": "falling_inflation"},
    "MQG.AX": {"name": "Macquarie Group", "sector": "Financials", "quadrant_bias": "rising_growth"},
    "FMG.AX": {"name": "Fortescue Metals", "sector": "Materials", "quadrant_bias": "rising_inflation"},
    "RIO.AX": {"name": "Rio Tinto", "sector": "Materials", "quadrant_bias": "rising_inflation"},
    "TLS.AX": {"name": "Telstra", "sector": "Telecom", "quadrant_bias": "falling_growth"},
    "WDS.AX": {"name": "Woodside Energy", "sector": "Energy", "quadrant_bias": "rising_inflation"},
    "ALL.AX": {"name": "Aristocrat Leisure", "sector": "Consumer Discretionary", "quadrant_bias": "rising_growth"},
    "STO.AX": {"name": "Santos", "sector": "Energy", "quadrant_bias": "rising_inflation"},
    "GMG.AX": {"name": "Goodman Group", "sector": "Real Estate", "quadrant_bias": "rising_growth"},
    "TCL.AX": {"name": "Transurban", "sector": "Industrials", "quadrant_bias": "falling_growth"},
    "COL.AX": {"name": "Coles Group", "sector": "Consumer Staples", "quadrant_bias": "falling_inflation"},
    "REA.AX": {"name": "REA Group", "sector": "Technology", "quadrant_bias": "rising_growth"},
    "JHX.AX": {"name": "James Hardie", "sector": "Materials", "quadrant_bias": "rising_growth"},
}

# Global Commodities
COMMODITIES = {
    "GC=F": {"name": "Gold Futures", "type": "precious_metal", "quadrant_bias": "rising_inflation"},
    "SI=F": {"name": "Silver Futures", "type": "precious_metal", "quadrant_bias": "rising_inflation"},
    "CL=F": {"name": "Crude Oil WTI", "type": "energy", "quadrant_bias": "rising_inflation"},
    "BZ=F": {"name": "Brent Crude Oil", "type": "energy", "quadrant_bias": "rising_inflation"},
    "NG=F": {"name": "Natural Gas", "type": "energy", "quadrant_bias": "rising_inflation"},
    "HG=F": {"name": "Copper Futures", "type": "industrial_metal", "quadrant_bias": "rising_growth"},
    "ZW=F": {"name": "Wheat Futures", "type": "agriculture", "quadrant_bias": "rising_inflation"},
    "ZC=F": {"name": "Corn Futures", "type": "agriculture", "quadrant_bias": "rising_inflation"},
    "ZS=F": {"name": "Soybean Futures", "type": "agriculture", "quadrant_bias": "rising_inflation"},
    "PL=F": {"name": "Platinum Futures", "type": "precious_metal", "quadrant_bias": "rising_inflation"},
    "PA=F": {"name": "Palladium Futures", "type": "precious_metal", "quadrant_bias": "rising_growth"},
    "CT=F": {"name": "Cotton Futures", "type": "agriculture", "quadrant_bias": "rising_inflation"},
    "KC=F": {"name": "Coffee Futures", "type": "agriculture", "quadrant_bias": "rising_inflation"},
    "SB=F": {"name": "Sugar Futures", "type": "agriculture", "quadrant_bias": "rising_inflation"},
    "LBS=F": {"name": "Lumber Futures", "type": "industrial", "quadrant_bias": "rising_growth"},
}

# Defensive / Inflation Hedges (ETFs for diversification)
DEFENSIVE_ETFS = {
    "TLT": {"name": "iShares 20+ Year Treasury Bond", "type": "bonds", "quadrant_bias": "falling_growth"},
    "IEF": {"name": "iShares 7-10 Year Treasury Bond", "type": "bonds", "quadrant_bias": "falling_growth"},
    "TIP": {"name": "iShares TIPS Bond", "type": "inflation_linked", "quadrant_bias": "rising_inflation"},
    "GLD": {"name": "SPDR Gold Shares", "type": "gold_etf", "quadrant_bias": "rising_inflation"},
    "DBC": {"name": "Invesco DB Commodity Index", "type": "commodity_basket", "quadrant_bias": "rising_inflation"},
    "VAS.AX": {"name": "Vanguard Australian Shares", "type": "equity_etf", "quadrant_bias": "rising_growth"},
}

def get_all_assets() -> dict:
    """Return the curated asset universe (equities + commodities + ETFs).
    For the full ASX universe (~1,900 companies), use api.scanners.get_asx_universe()."""
    all_assets = {}
    all_assets.update(ASX_EQUITIES)
    all_assets.update(COMMODITIES)
    all_assets.update(DEFENSIVE_ETFS)
    return all_assets


# Core subset (~13 assets) for low-memory systems (< 8GB RAM).
# Covers all quadrants with the most liquid instruments.
CORE_TICKERS = [
    # ASX blue chips (5)
    "BHP.AX", "CBA.AX", "CSL.AX", "FMG.AX", "WDS.AX",
    # Commodities (5)
    "GC=F", "CL=F", "SI=F", "HG=F", "NG=F",
    # Defensive ETFs (3)
    "TLT", "GLD", "TIP",
]


def get_core_assets() -> dict:
    """Return a reduced ~20 asset universe for memory-constrained systems."""
    full = get_all_assets()
    return {t: full[t] for t in CORE_TICKERS if t in full}


def get_assets_by_quadrant(quadrant: str) -> dict:
    """Filter assets by Dalio economic quadrant."""
    return {
        ticker: info
        for ticker, info in get_all_assets().items()
        if info.get("quadrant_bias") == quadrant
    }


# Dalio's 4 Economic Quadrants
QUADRANTS = {
    "rising_growth": {
        "description": "Economy expanding, corporate earnings rising",
        "favored": ["equities", "commodities", "corporate_bonds", "emerging_markets"],
        "avoid": ["nominal_bonds", "gold"],
    },
    "falling_growth": {
        "description": "Economy contracting, recessionary pressure",
        "favored": ["nominal_bonds", "treasury_bonds", "defensive_equities"],
        "avoid": ["cyclical_equities", "commodities"],
    },
    "rising_inflation": {
        "description": "Prices rising, currency devaluation pressure",
        "favored": ["commodities", "gold", "inflation_linked_bonds", "energy"],
        "avoid": ["nominal_bonds", "growth_equities"],
    },
    "falling_inflation": {
        "description": "Disinflation or deflation, stable purchasing power",
        "favored": ["equities", "nominal_bonds", "consumer_staples"],
        "avoid": ["commodities", "gold", "energy"],
    },
}