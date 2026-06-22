"""
payments.py — Razorpay integration for the paid tracking tier.

Pricing: health claims charge ₹199 (one-time) to unlock active tracking
(GRO -> IRDAI -> Ombudsman stage progression). Life/death claims stay free
for now, deliberately, since introducing payment friction to a grieving
family is a worse tradeoff than the revenue is worth at this stage.

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

TRACKING_PRICE_PAISE = {
    "health": 19900,   # ₹199.00
    "life": 0,          # free for now
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
    return TRACKING_PRICE_PAISE.get(case_type, TRACKING_PRICE_PAISE["health"])


def create_order(case_ref: str, case_type: str):
    """
    Creates a Razorpay order for the tracking unlock fee. Returns the order
    dict (includes 'id') on success, or raises on failure -- the caller
    should catch and return a clean error to the user.
    """
    amount = price_for(case_type)
    if amount == 0:
        return None  # free tier, no order needed

    client = _get_client()
    order = client.order.create({
        "amount": amount,
        "currency": "INR",
        "receipt": f"track-{case_ref}",
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
