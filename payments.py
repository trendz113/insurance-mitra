"""
payments.py — Razorpay integration for the paid letter-generation feature.

Pricing: generating the Claude-drafted grievance letter for a health claim
costs ₹99 (one-time per case). This is the only step in the app that calls
the Claude API, so it's the only place we charge. Case tracking and the
free score/verdict step cost us nothing and are not gated.

Life/death claim letters use a local template (not Claude) and stay free,
deliberately -- introducing payment friction to a grieving family is a
worse tradeoff than the revenue is worth, and there's no API cost to
recover anyway.

Security note: the signature verification in verify_payment() is the part
that actually matters. Anyone can POST a fake payment_id to our backend --
only Razorpay's HMAC signature (computed with our secret key) proves a
payment genuinely happened. Never mark a case as paid without this check.
"""
import os
import hmac
import hashlib
import logging

logger = logging.getLogger("payments")

LETTER_PRICE_PAISE = {
    "health": 9900,  # ₹99.00
    "life": 0,        # free -- local template, no Claude API cost
}

_client = None


def _get_client():
    global _client
    if _client is None:
        import razorpay
        key_id = os.environ["RAZORPAY_KEY_ID"]
        key_secret = os.environ["RAZORPAY_KEY_SECRET"]
        _client = razorpay.Client(auth=(key_id, key_secret))
    return _client


def price_for(case_type: str) -> int:
    return LETTER_PRICE_PAISE.get(case_type, LETTER_PRICE_PAISE["health"])


def create_order(case_ref: str, case_type: str, amount_paise: int = None):
    """
    Creates a Razorpay order for the letter-generation fee. Returns the
    order dict (includes 'id') on success, or raises on failure -- the
    caller should catch and return a clean error to the user.

    amount_paise lets the caller override the default price_for(case_type)
    lookup if needed; if omitted, the standard price for that case type
    is used.
    """
    amount = amount_paise if amount_paise is not None else price_for(case_type)
    if amount == 0:
        return None  # free tier, no order needed

    client = _get_client()
    order = client.order.create({
        "amount": amount,
        "currency": "INR",
        "receipt": f"letter-{case_ref}",
        "notes": {"case_ref": case_ref, "case_type": case_type},
    })
    return order


def verify_payment(order_id: str, payment_id: str, signature: str) -> bool:
    """
    Verifies the HMAC-SHA256 signature Razorpay sends back after a
    successful checkout. This is the step that actually proves the payment
    happened -- order_id and payment_id alone can be guessed or faked by
    anyone calling our API directly.
    """
    key_secret = os.environ["RAZORPAY_KEY_SECRET"]
    message = f"{order_id}|{payment_id}"
    expected_signature = hmac.new(
        key_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected_signature, signature)
