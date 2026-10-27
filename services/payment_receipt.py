"""Public payment-receipt API.

The renderer lives in payment_receipt_minimal.py so the existing imports remain
stable while the receipt design can evolve independently.
"""

from services.payment_receipt_minimal import build_payment_receipt_pdf, send_payment_receipt

__all__ = ["build_payment_receipt_pdf", "send_payment_receipt"]
