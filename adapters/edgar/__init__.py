"""SEC EDGAR adapter -- implements the EdgarClient port over data.sec.gov (no key, no paywall)."""

from adapters.edgar.client import SecEdgarClient

__all__ = ["SecEdgarClient"]
