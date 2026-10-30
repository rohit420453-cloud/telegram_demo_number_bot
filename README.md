# Telegram Demo Number Store (pyTelegramBotAPI + FamGateway)

A Python Telegram bot that sells clearly disclosed **fictional demo-number records**.
It does not provide real SIMs, SMS reception, OTPs, or account-verification services.

## Hosting compatibility

This is a persistent Python web service using Telegram webhooks, intended for Render (or another Python web host).
It is **not a Netlify Python Functions project**. Netlify Functions are event-driven serverless functions; this Python bot uses `pyTelegramBotAPI` and Flask.

## Features

- `/start`, `/myorders`, `/check`
- USA, UK, India demo catalog
- FamGateway hosted checkout, QR and UPI intent links
- Server-side authenticated payment verification
- HMAC-SHA256 verification of FamGateway webhooks
- Database-backed order and demo inventory records
- Admin commands `/admin` and `/addnumber COUNTRY|PHONE`
- Idempotent delivery: an already-delivered order is not delivered twice

## Before deploying

1. Create a Telegram bot with BotFather and copy its token.
2. Create a FamGateway merchant API key from your FamGateway account.
3. Create a Render account and deploy this repository using the included `render.yaml`.
4. Create a PostgreSQL database with a provider you trust, then copy its connection URL. In Render environment variables, set:
   - `BOT_TOKEN`
   - `FAMGATEWAY_API_KEY`
   - `PUBLIC_BASE_URL` to the exact HTTPS Render service URL
   - `DATABASE_URL` to the PostgreSQL connection URL (use `postgresql://...`)
   - `ADMIN_IDS` to your numeric Telegram user ID
5. Deploy. On startup the app creates its tables and registers the Telegram webhook automatically.
6. In the FamGateway dashboard, configure webhook URL:
   `https://YOUR-RENDER-SERVICE.onrender.com/famgateway/webhook`

Use a persistent PostgreSQL database. The app's local SQLite fallback is intentionally not used because ephemeral web-service filesystems can lose data on redeploy/restart.

## Local development

Python 3.11+ recommended.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set real values in your shell or load `.env` using your preferred local environment manager. Do not commit `.env`.

```bash
export $(grep -v '^#' .env | xargs)  # optional for simple local values
gunicorn bot.main:app --bind 0.0.0.0:8000
```

For local testing, expose the Flask port using a trusted HTTPS tunnel and set `PUBLIC_BASE_URL` to that HTTPS URL. Do not use a public tunnel for production secrets.

## Admin commands

- `/admin` — inventory counts
- `/addnumber USA|+1 202-555-0199`
- `/addnumber UK|+44 7700 900199`
- `/addnumber India|+91 00000 00099`

Only Telegram IDs listed in `ADMIN_IDS` can use admin commands.

## Payment flow

1. The bot creates a FamGateway order via `POST /api/create-order`.
2. It stores the provider order and reserves a demo inventory row.
3. The buyer pays using the checkout/UPI link or QR.
4. The buyer taps **Check payment**, or FamGateway calls the webhook.
5. The server verifies the order using `GET /api/verify-order.php` with the merchant API key.
6. Only after the server-side verification reports success is the reserved demo item marked delivered.

The implementation uses the API contract published in FamGateway's developer documentation. Confirm the response schema and merchant webhook settings in your own dashboard before accepting live payments.

## Important production notes

- Demo numbers are fictional examples. They cannot receive SMS or calls.
- The app does not implement real-number sourcing or OTP interception.
- Configure a real customer-support contact and refund policy before taking payments.
- Review local consumer, tax, payment, and platform obligations before launch.
- Render free web services may sleep when idle; use a suitable always-on plan for reliable webhook response times.
- Back up PostgreSQL and monitor Render/FamGateway logs.
- Keep API keys and bot tokens only in host environment variables. Never paste them into source files or commit them to Git.
- A failed payment verification never triggers delivery.
