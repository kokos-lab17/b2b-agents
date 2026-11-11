"""Persistent supplier memory and transaction storage using SQLite (Feature 5)."""
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any
from backend.config import settings

_db_lock = threading.Lock()


def _get_connection(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or settings.DATABASE_PATH
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str | None = None) -> None:
    """Initialize the SQLite schema."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS suppliers (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT UNIQUE NOT NULL,
                        archetype TEXT NOT NULL,
                        style TEXT NOT NULL,
                        specialty TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS procurement_sessions (
                        id TEXT PRIMARY KEY,
                        product TEXT NOT NULL,
                        qty INTEGER NOT NULL,
                        budget REAL NOT NULL,
                        status TEXT NOT NULL,
                        selected_supplier TEXT,
                        total_cost REAL,
                        is_demo INTEGER DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS negotiation_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        supplier_name TEXT NOT NULL,
                        rounds INTEGER NOT NULL,
                        outcome TEXT NOT NULL,
                        final_unit_price REAL,
                        final_total_price REAL,
                        created_at TEXT NOT NULL
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS transactions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        order_id TEXT UNIQUE NOT NULL,
                        supplier_name TEXT NOT NULL,
                        amount REAL NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        captured_at TEXT
                    );
                """)
        finally:
            conn.close()


def ensure_supplier_profile(
    name: str, archetype: str, style: str, specialty: str, db_path: str | None = None
) -> None:
    """Ensure supplier exists in registry."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute("""
                    INSERT INTO suppliers (name, archetype, style, specialty, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        archetype=excluded.archetype,
                        style=excluded.style,
                        specialty=excluded.specialty;
                """, (name, archetype, style, specialty, now))
        finally:
            conn.close()


def record_session(
    session_id: str,
    product: str,
    qty: int,
    budget: float,
    status: str = "running",
    is_demo: bool = False,
    db_path: str | None = None,
) -> None:
    """Create or update procurement session record."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute("""
                    INSERT INTO procurement_sessions (id, product, qty, budget, status, is_demo, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status=excluded.status,
                        updated_at=excluded.updated_at;
                """, (session_id, product, qty, budget, status, 1 if is_demo else 0, now, now))
        finally:
            conn.close()


def record_negotiation_attempt(
    session_id: str,
    supplier_name: str,
    rounds: int,
    outcome: str,
    final_unit_price: float | None = None,
    final_total_price: float | None = None,
    db_path: str | None = None,
) -> None:
    """Record an attempted negotiation (distinct from accepted deal or payment)."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute("""
                    INSERT INTO negotiation_records (session_id, supplier_name, rounds, outcome, final_unit_price, final_total_price, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                """, (session_id, supplier_name, rounds, outcome, final_unit_price, final_total_price, now))
        finally:
            conn.close()


def record_deal_accepted(
    session_id: str,
    order_id: str,
    supplier_name: str,
    amount: float,
    db_path: str | None = None,
) -> None:
    """Record an accepted deal awaiting payment."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute("""
                    INSERT INTO transactions (session_id, order_id, supplier_name, amount, status, created_at)
                    VALUES (?, ?, ?, ?, 'awaiting_approval', ?)
                    ON CONFLICT(order_id) DO UPDATE SET
                        amount=excluded.amount,
                        status='awaiting_approval';
                """, (session_id, order_id, supplier_name, amount, now))
                conn.execute("""
                    UPDATE procurement_sessions
                    SET selected_supplier = ?, total_cost = ?, status = 'deal_selected', updated_at = ?
                    WHERE id = ?;
                """, (supplier_name, amount, now, session_id))
        finally:
            conn.close()


def get_transaction(
    order_id: str, db_path: str | None = None
) -> dict[str, Any] | None:
    """Retrieve transaction details by order_id."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                cur = conn.execute(
                    "SELECT session_id, order_id, supplier_name, amount, status, created_at, captured_at FROM transactions WHERE order_id = ?;",
                    (order_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()


def record_payment_captured(
    order_id: str, db_path: str | None = None
) -> bool:
    """Record payment capture. Only verified PayPal captures reach here."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                now = datetime.now(timezone.utc).isoformat()
                cur = conn.execute("""
                    UPDATE transactions
                    SET status = 'captured', captured_at = COALESCE(captured_at, ?)
                    WHERE order_id = ?;
                """, (now, order_id))
                if cur.rowcount > 0:
                    conn.execute("""
                        UPDATE procurement_sessions
                        SET status = 'completed', updated_at = ?
                        WHERE id = (SELECT session_id FROM transactions WHERE order_id = ?);
                    """, (now, order_id))
                    return True
                return False
        finally:
            conn.close()


def get_supplier_metrics(supplier_name: str, db_path: str | None = None) -> dict[str, Any]:
    """Retrieve historical metrics for a supplier.
    Negotiation attempts, accepted deals, and captured payments are strictly tracked separately.
    """
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                # Count attempted negotiations
                cur = conn.execute("""
                    SELECT COUNT(*) as attempts FROM negotiation_records
                    WHERE supplier_name = ?;
                """, (supplier_name,))
                attempts = cur.fetchone()["attempts"]

                # Count accepted deals
                cur = conn.execute("""
                    SELECT COUNT(*) as accepted FROM transactions
                    WHERE supplier_name = ?;
                """, (supplier_name,))
                accepted = cur.fetchone()["accepted"]

                # Count captured payments
                cur = conn.execute("""
                    SELECT COUNT(*) as captured FROM transactions
                    WHERE supplier_name = ? AND status = 'captured';
                """, (supplier_name,))
                captured = cur.fetchone()["captured"]

                # Calculate reliability score (0.0 to 1.0)
                # Base is 0.85 if no history; successful captures boost it; unfulfilled/failed reduce it
                if attempts == 0:
                    reliability = 0.90
                else:
                    conversion = accepted / attempts if attempts > 0 else 0.5
                    capture_rate = captured / accepted if accepted > 0 else 1.0
                    reliability = round(min(1.0, max(0.5, 0.70 + 0.15 * conversion + 0.15 * capture_rate)), 2)

                return {
                    "supplier_name": supplier_name,
                    "attempted_deals": attempts,
                    "accepted_deals": accepted,
                    "captured_payments": captured,
                    "reliability_score": reliability,
                }
        finally:
            conn.close()


# Initialize database automatically on import
init_db()
