import requests
from decimal import Decimal

BASE = "https://famgateway.in"

class PaymentError(Exception):
    pass

def create_payment(api_key, amount, customer_name):
    try:
        response = requests.post(
            f"{BASE}/api/create-order",
            headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
            json={"amount": float(Decimal(str(amount))),
                  "customer_name": customer_name[:80]},
            timeout=(5, 20),
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise PaymentError(f"Could not create payment order: {exc}") from exc

    data = payload.get("data", {})
    if payload.get("status") != "success" or not data.get("order_id") or not data.get("checkout_url"):
        raise PaymentError("Payment provider returned an unexpected response.")
    return {
        "order_id": str(data["order_id"]),
        "checkout_url": data["checkout_url"],
        "qr_url": data.get("qr_url"),
        "upi_intent": data.get("upi_intent"),
        "amount": data.get("payable_amount", data.get("amount", amount)),
    }

def verify_payment(api_key, order_id):
    try:
        response = requests.get(
            f"{BASE}/api/verify-order.php",
            headers={"X-Api-Key": api_key, "Accept": "application/json"},
            params={"order_id": order_id},
            timeout=(5, 20),
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise PaymentError(f"Could not verify payment: {exc}") from exc

    # Accommodate either a top-level status or a nested data object.
    result = data.get("data") if isinstance(data.get("data"), dict) else data
    status = str(result.get("status", "")).lower()
    paid = status in {"success", "paid", "completed"} or result.get("is_paid") is True
    expired = status == "expired" or result.get("is_expired") is True
    return {"paid": paid, "expired": expired, "utr": result.get("utr")}
