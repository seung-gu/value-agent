"""SEC EDGAR adapter -- wraps edgartools for structured financials + segment revenue.

edgartools parses the filing XBRL (handling format drift + the dimensional/segment data that the
raw companyfacts API omits), so financials and product-level segments come back as plain numbers
-- NO regex/HTML parsing, NO LLM. CIK stays inside the adapter; callers pass ticker/name.
"""

from __future__ import annotations

from edgar import Company, set_identity
from pydantic import BaseModel

# SEC requires a descriptive identity (User-Agent) for all requests.
set_identity("value-agent research admin@value-agent.local")

# us-gaap concepts pulled as company-level financial accounts (absolute USD).
_ACCOUNTS = {
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
    "OperatingIncomeLoss": "operating_income",
    "NetIncomeLoss": "net_income",
    "NetCashProvidedByUsedInOperatingActivities": "operating_cash_flow",
}
_REVENUE_CONCEPT = "RevenueFromContractWithCustomerExcludingAssessedTax"
_PRODUCT_AXIS = "srt:ProductOrServiceAxis"  # product disaggregation (vs the geographic segments)


class EdgarFact(BaseModel):
    """One (key, period, amount USD) fact -- key is an account or a revenue stream."""

    key: str
    period: str        # "FY2025"
    amount: float
    source: str = "edgar"


def _fy(row) -> str | None:
    # period_end ("2025-09-27") is reliable; fiscal_year can be wrong on dimensioned rows.
    end = row.get("period_end")
    if end:
        return f"FY{str(end)[:4]}"
    fy = row.get("fiscal_year")
    return f"FY{int(fy)}" if fy else None


def _val(row) -> float | None:
    v = row.get("numeric_value")
    if v is None:
        v = row.get("value")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class SecEdgarClient:
    """EdgarClient over edgartools -- deterministic financials + product segments."""

    def lookup(self, name_or_ticker: str) -> str | None:
        """Return the CIK if the company is SEC-registered (US-listed), else None."""
        try:
            cik = getattr(Company(name_or_ticker.strip()), "cik", None)
            return str(cik) if cik else None
        except Exception:
            return None

    def financials(self, ident: str) -> list[EdgarFact]:
        """Company-level accounts (revenue / operating_income / net_income / ocf), per fiscal year."""
        xb = Company(ident.strip()).get_financials().xb
        out: list[EdgarFact] = []
        for concept, account in _ACCOUNTS.items():
            df = xb.query().by_concept(concept).to_dataframe()
            for _, row in df.iterrows():
                if row.get("is_dimensioned"):     # keep only the undimensioned company total
                    continue
                period, amount = _fy(row), _val(row)
                if period and amount is not None:
                    out.append(EdgarFact(key=account, period=period, amount=amount))
        return out

    def segments(self, ident: str) -> list[EdgarFact]:
        """Product-level revenue streams (iPhone / Mac / … / Services), per fiscal year."""
        xb = Company(ident.strip()).get_financials().xb
        df = (
            xb.query()
            .by_concept(_REVENUE_CONCEPT)
            .by_dimension(_PRODUCT_AXIS)
            .to_dataframe()
        )
        out: list[EdgarFact] = []
        for _, row in df.iterrows():
            stream = str(row.get("label", "")).strip()
            if not stream or stream == "Products":   # 'Products' is the iPhone+Mac+… subtotal
                continue
            period, amount = _fy(row), _val(row)
            if period and amount is not None:
                out.append(EdgarFact(key=stream, period=period, amount=amount))
        return out
