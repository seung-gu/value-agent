"""SEC EDGAR adapter -- wraps edgartools for structured financials + segment revenue.

edgartools parses the filing XBRL (handling format drift + the dimensional/segment data that the
raw companyfacts API omits), so financials and product-level segments come back as plain numbers
-- NO regex/HTML parsing, NO LLM. CIK stays inside the adapter.

Callers pass a TICKER (or CIK) -- NOT a company name. Names are hopeless to match (Apple /
Apple Inc. / 애플), so the upstream agent captures each company's ticker and we look up EDGAR by
that. A non-US / unlisted company has no usable ticker here -> lookup returns None and the
orchestrator uses the web fallback (company_agent).
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
    """EdgarClient over edgartools -- deterministic financials + product segments.

    Identified by TICKER or CIK (never a company name). An unknown/foreign ticker raises inside
    edgartools, which every method turns into an empty/None result -> the caller falls back.
    """

    def lookup(self, ticker: str) -> str | None:
        """Return the CIK if `ticker` is a real SEC-registered (US-listed) company, else None."""
        try:
            cik = getattr(Company(ticker.strip()), "cik", None)
            return str(cik) if cik else None
        except Exception:
            return None

    def financials(self, ticker: str) -> list[EdgarFact]:
        """Company-level accounts (revenue / operating_income / net_income / ocf), per fiscal year."""
        try:
            xb = Company(ticker.strip()).get_financials().xb
        except Exception:
            return []
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

    def segments(self, ticker: str) -> list[EdgarFact]:
        """Product-level revenue streams (iPhone / Mac / … / Services), per fiscal year."""
        try:
            xb = Company(ticker.strip()).get_financials().xb
        except Exception:
            return []
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
