import os
import psycopg
from psycopg.rows import dict_row

SCHEMA = """
CREATE TABLE IF NOT EXISTS demo_numbers (
    id BIGSERIAL PRIMARY KEY,
    country TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'available'
      CHECK (status IN ('available','reserved','delivered')),
    reserved_order_id TEXT UNIQUE,
    delivered_to BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    chat_id BIGINT NOT NULL,
    amount NUMERIC(10,2) NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
      CHECK (status IN ('pending','paid','expired','delivered','delivery_failed')),
    checkout_url TEXT NOT NULL,
    qr_url TEXT,
    upi_intent TEXT,
    utr TEXT,
    demo_number_id BIGINT REFERENCES demo_numbers(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    paid_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS orders_user_idx ON orders(telegram_user_id, created_at DESC);
"""

def connect():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)

def init_db():
    with connect() as conn:
        conn.execute(SCHEMA)
        existing = conn.execute("SELECT COUNT(*) AS n FROM demo_numbers").fetchone()["n"]
        if existing == 0:
            demo_rows = [
                ("USA", "+1 202-555-0101"),
                ("USA", "+1 202-555-0102"),
                ("USA", "+1 202-555-0103"),
                ("UK", "+44 7700 900101"),
                ("UK", "+44 7700 900102"),
                ("UK", "+44 7700 900103"),
                ("India", "+91 00000 00001"),
                ("India", "+91 00000 00002"),
                ("India", "+91 00000 00003"),
            ]
            conn.executemany(
                "INSERT INTO demo_numbers(country, phone) VALUES (%s, %s) ON CONFLICT(phone) DO NOTHING",
                demo_rows,
            )

def available_count(country=None):
    with connect() as conn:
        if country:
            return conn.execute(
                "SELECT COUNT(*) AS n FROM demo_numbers WHERE status='available' AND country=%s",
                (country,),
            ).fetchone()["n"]
        return conn.execute(
            "SELECT COUNT(*) AS n FROM demo_numbers WHERE status='available'"
        ).fetchone()["n"]

def create_order_record(order, user_id, chat_id, amount):
    with connect() as conn:
        # Lock and reserve one demo number before accepting the order.
        number = conn.execute(
            """SELECT id, country, phone FROM demo_numbers
               WHERE status='available' ORDER BY id
               FOR UPDATE SKIP LOCKED LIMIT 1"""
        ).fetchone()
        if not number:
            raise RuntimeError("No demo inventory is currently available.")
        conn.execute(
            """INSERT INTO orders(order_id, telegram_user_id, chat_id, amount,
                                  checkout_url, qr_url, upi_intent, demo_number_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (order["order_id"], user_id, chat_id, amount, order["checkout_url"],
             order.get("qr_url"), order.get("upi_intent"), number["id"]),
        )
        conn.execute(
            """UPDATE demo_numbers SET status='reserved', reserved_order_id=%s
               WHERE id=%s""",
            (order["order_id"], number["id"]),
        )
        return number

def get_order(order_id):
    with connect() as conn:
        return conn.execute(
            """SELECT o.*, d.phone, d.country FROM orders o
               LEFT JOIN demo_numbers d ON d.id=o.demo_number_id
               WHERE o.order_id=%s""", (order_id,)
        ).fetchone()

def mark_paid_and_deliver(order_id, utr=None):
    """Idempotently mark paid and deliver the already-reserved demo item."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE order_id=%s FOR UPDATE", (order_id,)
        ).fetchone()
        if not row:
            return None, "not_found"
        if row["status"] == "delivered":
            number = conn.execute(
                "SELECT phone,country FROM demo_numbers WHERE id=%s",
                (row["demo_number_id"],)
            ).fetchone()
            return (row, number), "already_delivered"
        if row["status"] not in ("pending", "paid", "delivery_failed"):
            return row, "not_pending"
        conn.execute(
            """UPDATE orders SET status='paid', utr=COALESCE(%s,utr),
               paid_at=COALESCE(paid_at,NOW()) WHERE order_id=%s""",
            (utr, order_id),
        )
        number = conn.execute(
            "SELECT * FROM demo_numbers WHERE id=%s FOR UPDATE",
            (row["demo_number_id"],)
        ).fetchone()
        if not number:
            conn.execute("UPDATE orders SET status='delivery_failed' WHERE order_id=%s", (order_id,))
            return row, "no_reserved_number"
        conn.execute(
            """UPDATE demo_numbers SET status='delivered', delivered_to=%s
               WHERE id=%s""", (row["telegram_user_id"], number["id"])
        )
        conn.execute(
            "UPDATE orders SET status='delivered', delivered_at=NOW() WHERE order_id=%s",
            (order_id,)
        )
        row["status"] = "delivered"
        return (row, number), "delivered"

def list_user_orders(user_id, limit=10):
    with connect() as conn:
        return conn.execute(
            """SELECT order_id,amount,status,created_at,utr FROM orders
               WHERE telegram_user_id=%s ORDER BY created_at DESC LIMIT %s""",
            (user_id, limit)
        ).fetchall()

def add_demo_number(country, phone):
    with connect() as conn:
        conn.execute(
            "INSERT INTO demo_numbers(country,phone) VALUES (%s,%s)", (country, phone)
        )

def inventory():
    with connect() as conn:
        return conn.execute(
            """SELECT country,
               COUNT(*) FILTER (WHERE status='available') AS available,
               COUNT(*) FILTER (WHERE status='reserved') AS reserved,
               COUNT(*) FILTER (WHERE status='delivered') AS delivered
               FROM demo_numbers GROUP BY country ORDER BY country"""
        ).fetchall()


def create_order_record_for_country(order, user_id, chat_id, amount, country):
    with connect() as conn:
        number = conn.execute(
            """SELECT id, country, phone FROM demo_numbers
               WHERE status='available' AND country=%s
               ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1""",
            (country,)
        ).fetchone()
        if not number:
            raise RuntimeError("No demo inventory is currently available for this country.")
        conn.execute(
            """INSERT INTO orders(order_id, telegram_user_id, chat_id, amount,
                                  checkout_url, qr_url, upi_intent, demo_number_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (order["order_id"], user_id, chat_id, amount, order["checkout_url"],
             order.get("qr_url"), order.get("upi_intent"), number["id"]),
        )
        conn.execute(
            """UPDATE demo_numbers SET status='reserved', reserved_order_id=%s
               WHERE id=%s""",
            (order["order_id"], number["id"]),
        )
        return number
