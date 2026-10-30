import hashlib
import hmac
import json
import logging
import os
from decimal import Decimal
from urllib.parse import urlparse

import telebot
from flask import Flask, abort, request
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from .config import settings
from .db import (init_db, available_count, create_order_record, get_order,
                 mark_paid_and_deliver, list_user_orders, add_demo_number, inventory)
from .payments import create_payment, verify_payment, PaymentError

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("demo-number-bot")

bot = telebot.TeleBot(settings.bot_token, parse_mode="HTML", threaded=False)
app = Flask(__name__)

DISCLAIMER = (
    "⚠️ <b>DEMO STORE</b>\n"
    "All numbers in this bot are fictional test data. They are not real SIMs, "
    "cannot receive SMS/OTP, and cannot be used for calls or account verification. "
    "You are paying only for a clearly labelled demo item."
)

def country_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    for label, code in [("🇺🇸 USA", "USA"), ("🇬🇧 UK", "UK"), ("🇮🇳 India", "India")]:
        markup.add(InlineKeyboardButton(label, callback_data=f"country:{code}"))
    return markup

def main_menu():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🛒 Buy demo number", callback_data="shop"),
        InlineKeyboardButton("📦 My orders", callback_data="orders"),
    )
    return markup

def send_delivery(chat_id, order_id, result):
    row, number = result
    if not number:
        bot.send_message(chat_id, f"Payment verified for <code>{order_id}</code>, but demo inventory needs admin attention. Contact support.")
        return
    bot.send_message(
        chat_id,
        f"🎉 <b>Payment verified — demo item delivered</b>\n\n"
        f"Order: <code>{order_id}</code>\n"
        f"Country: {number['country']}\n"
        f"Demo number: <code>{number['phone']}</code>\n\n"
        f"This is fictional test data and does not receive calls or SMS.",
    )

def fulfill_if_paid(order_id):
    order = get_order(order_id)
    if not order:
        return False, "Order not found."
    verified = verify_payment(settings.famgateway_api_key, order_id)
    if verified["paid"]:
        result, state = mark_paid_and_deliver(order_id, verified.get("utr"))
        if state in ("delivered", "already_delivered"):
            if state == "delivered":
                send_delivery(order["chat_id"], order_id, result)
            return True, "Payment verified."
        return False, f"Payment verified, but fulfillment state is {state}."
    if verified["expired"]:
        return False, "Payment session expired. Start a new purchase."
    return False, "Payment is still pending. Tap Check payment again in a moment."

@bot.message_handler(commands=["start", "help"])
def start(message):
    bot.send_message(message.chat.id, DISCLAIMER + "\n\nChoose an option:", reply_markup=main_menu())

@bot.message_handler(commands=["myorders"])
def myorders(message):
    rows = list_user_orders(message.from_user.id)
    if not rows:
        bot.reply_to(message, "No orders yet.")
        return
    lines = ["<b>Your recent orders</b>"]
    for r in rows:
        lines.append(f"• <code>{r['order_id']}</code> — ₹{r['amount']} — {r['status']}")
    bot.send_message(message.chat.id, "\n".join(lines))

@bot.message_handler(commands=["check"])
def check_latest(message):
    rows = list_user_orders(message.from_user.id, 1)
    if not rows:
        bot.reply_to(message, "No order found.")
        return
    ok, note = fulfill_if_paid(rows[0]["order_id"])
    bot.reply_to(message, note)

@bot.callback_query_handler(func=lambda c: c.data == "shop")
def shop_callback(call):
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, DISCLAIMER + "\n\nChoose a demo country:", reply_markup=country_keyboard())

@bot.callback_query_handler(func=lambda c: c.data.startswith("country:"))
def country_callback(call):
    bot.answer_callback_query(call.id)
    country = call.data.split(":", 1)[1]
    count = available_count(country)
    if count < 1:
        bot.send_message(call.message.chat.id, f"No {country} demo stock available right now.")
        return
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(
        f"Pay ₹{settings.price_inr} — get demo item",
        callback_data=f"buy:{country}"
    ))
    bot.send_message(
        call.message.chat.id,
        f"<b>{country} demo number</b>\nPrice: ₹{settings.price_inr}\n"
        f"Available demo stock: {count}\n\n"
        "The delivered number is fictional and cannot receive calls or SMS.",
        reply_markup=markup,
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("buy:"))
def buy_callback(call):
    bot.answer_callback_query(call.id)
    country = call.data.split(":", 1)[1]
    if country not in {"USA", "UK", "India"}:
        return
    if available_count(country) < 1:
        bot.send_message(call.message.chat.id, "Sorry, this demo stock is sold out.")
        return
    # Create provider order first. DB reserves the first available demo item.
    try:
        payment = create_payment(
            settings.famgateway_api_key,
            Decimal(settings.price_inr),
            call.from_user.first_name or f"Telegram {call.from_user.id}",
        )
        # Reserve only a matching country item; DB method below uses first available.
        # To keep the reservation country-specific, verify availability and select matching
        # inventory in a dedicated transaction.
        from .db import create_order_record_for_country
        create_order_record_for_country(payment, call.from_user.id, call.message.chat.id,
                                         Decimal(settings.price_inr), country)
    except Exception as exc:
        log.exception("Order creation failed")
        bot.send_message(call.message.chat.id, "Could not create your order. Please try again later.")
        return

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("🌐 Open UPI checkout", url=payment["checkout_url"]))
    if payment.get("upi_intent"):
        markup.add(InlineKeyboardButton("📲 Open UPI app", url=payment["upi_intent"]))
    markup.add(InlineKeyboardButton("🔄 Check payment", callback_data=f"check:{payment['order_id']}"))
    text = (
        f"🧾 <b>Demo order created</b>\nOrder: <code>{payment['order_id']}</code>\n"
        f"Amount: ₹{settings.price_inr}\n\n"
        "Pay using the checkout link or QR. After payment, tap Check payment. "
        "Delivery happens only after server-side verification."
    )
    if payment.get("qr_url"):
        bot.send_photo(call.message.chat.id, payment["qr_url"], caption=text, reply_markup=markup)
    else:
        bot.send_message(call.message.chat.id, text, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("check:"))
def check_callback(call):
    bot.answer_callback_query(call.id, "Checking payment…")
    order_id = call.data.split(":", 1)[1]
    order = get_order(order_id)
    if not order or order["telegram_user_id"] != call.from_user.id:
        bot.send_message(call.message.chat.id, "Order not found for your account.")
        return
    try:
        ok, note = fulfill_if_paid(order_id)
        if not ok:
            bot.send_message(call.message.chat.id, note)
        elif order["status"] == "delivered":
            bot.send_message(call.message.chat.id, "This order has already been delivered.")
    except PaymentError:
        log.exception("Payment verification error")
        bot.send_message(call.message.chat.id, "Payment provider is temporarily unavailable. Please try again.")

@bot.message_handler(commands=["admin"])
def admin(message):
    if message.from_user.id not in settings.admin_ids:
        return
    rows = inventory()
    lines = ["<b>Inventory</b>"]
    for r in rows:
        lines.append(f"{r['country']}: available {r['available']}, reserved {r['reserved']}, delivered {r['delivered']}")
    bot.send_message(message.chat.id, "\n".join(lines) + "\n\nAdd stock: /addnumber COUNTRY|PHONE")

@bot.message_handler(commands=["addnumber"])
def addnumber(message):
    if message.from_user.id not in settings.admin_ids:
        return
    try:
        payload = message.text.split(" ", 1)[1]
        country, phone = [x.strip() for x in payload.split("|", 1)]
        if country not in {"USA", "UK", "India"} or not phone:
            raise ValueError()
        add_demo_number(country, phone)
        bot.reply_to(message, "Demo inventory item added.")
    except Exception:
        bot.reply_to(message, "Usage: /addnumber USA|+1 202-555-0199")

@app.get("/health")
def health():
    return {"ok": True, "service": "telegram-demo-number-bot"}

@app.post(f"/telegram/{settings.telegram_webhook_secret}")
def telegram_webhook():
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(supplied, settings.telegram_webhook_secret):
        abort(403)
    update = telebot.types.Update.de_json(request.get_data(as_text=True))
    bot.process_new_updates([update])
    return "OK", 200

@app.post("/famgateway/webhook")
def famgateway_webhook():
    raw = request.get_data()
    signature = request.headers.get("X-FamGateway-Signature", "")
    expected = hmac.new(
        settings.famgateway_api_key.encode("utf-8"), raw, hashlib.sha256
    ).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature.lower()):
        abort(401)
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        abort(400)
    if event.get("event") != "payment.success" or event.get("status") != "success":
        return {"status": "ignored"}, 200
    order_id = event.get("order_id")
    if not order_id:
        abort(400)
    # Do not trust the webhook alone: verify the order directly with FamGateway.
    try:
        ok, note = fulfill_if_paid(order_id)
        log.info("Webhook for %s: %s %s", order_id, ok, note)
    except Exception:
        log.exception("Webhook fulfillment failed")
        return {"status": "retry"}, 500
    return {"status": "processed" if ok else "received"}, 200

def register_webhook():
    webhook_url = f"{settings.public_base_url}/telegram/{settings.telegram_webhook_secret}"
    bot.remove_webhook()
    bot.set_webhook(url=webhook_url, secret_token=settings.telegram_webhook_secret,
                    allowed_updates=["message", "callback_query"])
    log.info("Telegram webhook registered.")

init_db()
register_webhook()
