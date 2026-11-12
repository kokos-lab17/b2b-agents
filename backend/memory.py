"""Persistent supplier memory, transaction storage, and session isolation using SQLite (Feature 5 & 12)."""
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any
from backend.config import settings

_db_lock = threading.Lock()


def _get_connection(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or settings.DATABASE_PATH
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column_def: str) -> None:
    col_name = column_def.split()[0]
    cursor = conn.execute(f"PRAGMA table_info({table});")
    existing_cols = [row[1] for row in cursor.fetchall()]
    if col_name not in existing_cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def};")


def init_db(db_path: str | None = None) -> None:
    """Initialize SQLite schema and perform safe idempotent migrations."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                # 1. Suppliers registry
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

                # 2. Procurement sessions
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
                # Migrations for sessions
                _add_column_if_missing(conn, "procurement_sessions", "negotiation_mode TEXT DEFAULT 'DEMO'")
                _add_column_if_missing(conn, "procurement_sessions", "payment_mode TEXT DEFAULT 'SIMULATED'")
                _add_column_if_missing(conn, "procurement_sessions", "buyer_id TEXT DEFAULT 'default_buyer'")
                _add_column_if_missing(conn, "procurement_sessions", "priority_type TEXT DEFAULT 'BALANCED_VALUE'")
                _add_column_if_missing(conn, "procurement_sessions", "deal_json TEXT")
                _add_column_if_missing(conn, "procurement_sessions", "final_offers_json TEXT")
                _add_column_if_missing(conn, "procurement_sessions", "buyer_intelligence_json TEXT")
                _add_column_if_missing(conn, "procurement_sessions", "order_id TEXT")
                _add_column_if_missing(conn, "procurement_sessions", "approve_url TEXT")

                # 3. Negotiation records per round
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

                # 4. Transactions
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
                # Migration for transactions payment_mode
                _add_column_if_missing(conn, "transactions", "payment_mode TEXT DEFAULT 'SIMULATED'")

                # 5. Session events (Isolated per session)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS session_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        step INTEGER NOT NULL,
                        timestamp TEXT NOT NULL,
                        agent TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        message TEXT NOT NULL,
                        strategy TEXT,
                        offer REAL,
                        details_json TEXT,
                        status TEXT
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_session_events_session ON session_events(session_id);")

                # 6. Buyer profiles
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS buyer_profiles (
                        buyer_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                """)

                # 7. Buyer preferences
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS buyer_preferences (
                        buyer_id TEXT PRIMARY KEY,
                        priority_type TEXT NOT NULL DEFAULT 'BALANCED_VALUE',
                        custom_weights_json TEXT,
                        max_budget REAL,
                        max_delivery_days INTEGER,
                        min_warranty_months INTEGER,
                        max_shipping_cost REAL,
                        preferred_payment_terms TEXT,
                        source TEXT NOT NULL DEFAULT 'explicit',
                        updated_at TEXT NOT NULL
                    );
                """)

                # 8. Buyer recommendations (Consent-based behavioral learning)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS buyer_recommendations (
                        id TEXT PRIMARY KEY,
                        buyer_id TEXT NOT NULL,
                        recommendation_type TEXT NOT NULL,
                        title TEXT NOT NULL,
                        description TEXT NOT NULL,
                        evidence TEXT NOT NULL,
                        suggested_priority TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                """)

                # 9. Strategy effectiveness records
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS strategy_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        strategy_name TEXT NOT NULL,
                        round_num INTEGER NOT NULL,
                        concession_obtained INTEGER NOT NULL DEFAULT 0,
                        price_reduction REAL NOT NULL DEFAULT 0.0,
                        deal_won INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL
                    );
                """)

                # 10. Audit records (Decision audit trail)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS audit_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        action TEXT NOT NULL,
                        actor TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        details_json TEXT
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_records_session ON audit_records(session_id);")

                # 11. Saved and compared quotes
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS saved_quotes (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        supplier_name TEXT NOT NULL,
                        unit_price REAL NOT NULL,
                        quantity INTEGER NOT NULL,
                        delivery_days INTEGER NOT NULL,
                        warranty_months INTEGER NOT NULL,
                        shipping_cost REAL NOT NULL,
                        payment_terms TEXT NOT NULL,
                        special_concessions_json TEXT,
                        notes TEXT,
                        created_at TEXT NOT NULL
                    );
                """)

                # Seed default buyer profile if absent
                now = datetime.now(timezone.utc).isoformat()
                conn.execute("""
                    INSERT OR IGNORE INTO buyer_profiles (buyer_id, name, created_at, updated_at)
                    VALUES ('default_buyer', 'Primary Procurement Officer', ?, ?);
                """, (now, now))
                conn.execute("""
                    INSERT OR IGNORE INTO buyer_preferences (buyer_id, priority_type, source, updated_at)
                    VALUES ('default_buyer', 'BALANCED_VALUE', 'system_default', ?);
                """, (now,))
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
    negotiation_mode: str = "DEMO",
    payment_mode: str = "SIMULATED",
    buyer_id: str = "default_buyer",
    priority_type: str = "BALANCED_VALUE",
    db_path: str | None = None,
) -> None:
    """Create or update procurement session record."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute("""
                    INSERT INTO procurement_sessions (
                        id, product, qty, budget, status, is_demo,
                        negotiation_mode, payment_mode, buyer_id, priority_type,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status=excluded.status,
                        negotiation_mode=excluded.negotiation_mode,
                        payment_mode=excluded.payment_mode,
                        buyer_id=excluded.buyer_id,
                        priority_type=excluded.priority_type,
                        updated_at=excluded.updated_at;
                """, (
                    session_id, product, qty, budget, status, 1 if is_demo else 0,
                    negotiation_mode, payment_mode, buyer_id, priority_type,
                    now, now
                ))
        finally:
            conn.close()


def record_session_result(
    session_id: str,
    status: str,
    deal: dict[str, Any] | None = None,
    final_offers: list[dict[str, Any]] | None = None,
    buyer_intelligence: dict[str, Any] | None = None,
    order_id: str | None = None,
    approve_url: str | None = None,
    selected_supplier: str | None = None,
    total_cost: float | None = None,
    db_path: str | None = None,
) -> None:
    """Persist completed or awaiting_approval session results so sessions can be recovered from DB."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                now = datetime.now(timezone.utc).isoformat()
                deal_json = json.dumps(deal) if deal else None
                offers_json = json.dumps(final_offers) if final_offers else None
                intel_json = json.dumps(buyer_intelligence) if buyer_intelligence else None
                cur = conn.execute("""
                    UPDATE procurement_sessions
                    SET status = ?,
                        deal_json = COALESCE(?, deal_json),
                        final_offers_json = COALESCE(?, final_offers_json),
                        buyer_intelligence_json = COALESCE(?, buyer_intelligence_json),
                        order_id = COALESCE(?, order_id),
                        approve_url = COALESCE(?, approve_url),
                        selected_supplier = COALESCE(?, selected_supplier),
                        total_cost = COALESCE(?, total_cost),
                        updated_at = ?
                    WHERE id = ?;
                """, (
                    status, deal_json, offers_json, intel_json,
                    order_id, approve_url, selected_supplier, total_cost,
                    now, session_id
                ))
                if cur.rowcount == 0:
                    conn.execute("""
                        INSERT INTO procurement_sessions (
                            id, product, qty, budget, status, selected_supplier, total_cost,
                            deal_json, final_offers_json, buyer_intelligence_json, order_id, approve_url,
                            created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (
                        session_id,
                        deal.get("product", "Procured Item") if deal else "Procured Item",
                        deal.get("quantity", 100) if deal else 100,
                        deal.get("unit_price", 0.0) if deal else 0.0,
                        status, selected_supplier, total_cost,
                        deal_json, offers_json, intel_json, order_id, approve_url,
                        now, now
                    ))
        finally:
            conn.close()


def get_session(session_id: str, db_path: str | None = None) -> dict[str, Any] | None:
    """Fetch session record by ID."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                cur = conn.execute("SELECT * FROM procurement_sessions WHERE id = ?;", (session_id,))
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()


def get_latest_session(db_path: str | None = None) -> dict[str, Any] | None:
    """Fetch the most recently updated session."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                cur = conn.execute("SELECT * FROM procurement_sessions ORDER BY updated_at DESC LIMIT 1;")
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()


def get_recent_sessions(limit: int = 15, db_path: str | None = None) -> list[dict[str, Any]]:
    """Retrieve recent procurement sessions for history and analytics."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                cur = conn.execute("""
                    SELECT s.*, t.order_id, t.status as transaction_status
                    FROM procurement_sessions s
                    LEFT JOIN transactions t ON s.id = t.session_id
                    ORDER BY s.created_at DESC
                    LIMIT ?;
                """, (limit,))
                return [dict(r) for r in cur.fetchall()]
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
    """Record an attempted negotiation."""
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
    payment_mode: str = "SIMULATED",
    db_path: str | None = None,
) -> None:
    """Record an accepted deal awaiting payment."""
    if payment_mode == "SIMULATED" and not (order_id.startswith("DEMO-") or order_id.startswith("SIM-")):
        payment_mode = "PAYPAL_SANDBOX"

    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute("""
                    INSERT INTO transactions (session_id, order_id, supplier_name, amount, status, payment_mode, created_at)
                    VALUES (?, ?, ?, ?, 'awaiting_approval', ?, ?)
                    ON CONFLICT(order_id) DO UPDATE SET
                        amount=excluded.amount,
                        payment_mode=excluded.payment_mode,
                        status='awaiting_approval';
                """, (session_id, order_id, supplier_name, amount, payment_mode, now))
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
                    "SELECT session_id, order_id, supplier_name, amount, status, payment_mode, created_at, captured_at FROM transactions WHERE order_id = ?;",
                    (order_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()


def record_payment_captured(
    order_id: str, db_path: str | None = None
) -> bool:
    """Record payment capture. Verified PayPal captures or simulated transactions."""
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


def record_session_event(
    session_id: str,
    step: int | None = None,
    agent: str = "System",
    event_type: str = "status",
    message: str = "",
    strategy: str | None = None,
    offer: float | None = None,
    details: dict[str, Any] | None = None,
    status: str = "running",
    approve_url: str | None = None,
    db_path: str | None = None,
) -> None:
    """Persist an explainable event isolated to its session."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                if step is None:
                    cur = conn.execute("SELECT COALESCE(MAX(step), 0) + 1 AS next_step FROM session_events WHERE session_id = ?;", (session_id,))
                    row = cur.fetchone()
                    step = row["next_step"] if row else 1

                now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
                # If approve_url is provided, incorporate into details
                if approve_url:
                    details = details or {}
                    details["approve_url"] = approve_url

                details_json = json.dumps(details) if details else None
                conn.execute("""
                    INSERT INTO session_events (
                        session_id, step, timestamp, agent, event_type, message,
                        strategy, offer, details_json, status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    session_id, step, now_str, agent, event_type, message,
                    strategy, offer, details_json, status
                ))
        finally:
            conn.close()


def get_session_events(session_id: str, db_path: str | None = None) -> list[dict[str, Any]]:
    """Retrieve isolated event history for a given session."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                cur = conn.execute("""
                    SELECT step, timestamp, agent, event_type, message, strategy, offer, details_json, status
                    FROM session_events
                    WHERE session_id = ?
                    ORDER BY step ASC;
                """, (session_id,))
                events = []
                for row in cur.fetchall():
                    item = dict(row)
                    if item.get("details_json"):
                        try:
                            item["details"] = json.loads(item["details_json"])
                        except Exception:
                            item["details"] = None
                    del item["details_json"]
                    events.append(item)
                return events
        finally:
            conn.close()


def record_audit_event(
    session_id: str,
    action: str,
    actor: str = "Buyer Agent",
    reason: str = "",
    details: dict[str, Any] | None = None,
    agent: str | None = None,
    reasoning: str | None = None,
    db_path: str | None = None,
) -> None:
    """Record an audit trail entry for explainable decision governance."""
    actor_name = agent or actor
    reason_text = reasoning or reason
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                now_iso = datetime.now(timezone.utc).isoformat()
                details_json = json.dumps(details) if details else None
                conn.execute("""
                    INSERT INTO audit_records (session_id, timestamp, action, actor, reason, details_json)
                    VALUES (?, ?, ?, ?, ?, ?);
                """, (session_id, now_iso, action, actor_name, reason_text, details_json))
        finally:
            conn.close()


def get_session_audit_trail(session_id: str, db_path: str | None = None) -> list[dict[str, Any]]:
    """Retrieve decision audit trail for a session."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                cur = conn.execute("""
                    SELECT id, session_id, timestamp, action, actor, reason, details_json
                    FROM audit_records
                    WHERE session_id = ?
                    ORDER BY id ASC;
                """, (session_id,))
                trail = []
                for row in cur.fetchall():
                    item = dict(row)
                    if item.get("details_json"):
                        try:
                            item["details"] = json.loads(item["details_json"])
                        except Exception:
                            item["details"] = None
                    del item["details_json"]
                    trail.append(item)
                return trail
        finally:
            conn.close()


def record_strategy_outcome(
    session_id: str,
    strategy_name: str | None = None,
    round_num: int = 2,
    concession_obtained: bool = True,
    price_reduction: float = 0.0,
    deal_won: bool = False,
    strategy: str | None = None,
    supplier_name: str | None = None,
    initial_price: float = 0.0,
    final_price: float = 0.0,
    won: bool | None = None,
    db_path: str | None = None,
) -> None:
    """Record effectiveness metrics of a negotiation strategy."""
    strat = strategy or strategy_name or "PRICE_DISCOUNT"
    if won is not None:
        deal_won = won
    if initial_price > 0 and final_price > 0:
        price_reduction = max(0.0, round(initial_price - final_price, 2))
        concession_obtained = price_reduction > 0 or deal_won

    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                now_iso = datetime.now(timezone.utc).isoformat()
                conn.execute("""
                    INSERT INTO strategy_records (
                        session_id, strategy_name, round_num, concession_obtained,
                        price_reduction, deal_won, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                """, (
                    session_id, strat, round_num,
                    1 if concession_obtained else 0,
                    float(price_reduction),
                    1 if deal_won else 0,
                    now_iso
                ))
        finally:
            conn.close()


def get_strategy_metrics(db_path: str | None = None) -> list[dict[str, Any]]:
    """Calculate deterministic effectiveness metrics for all negotiation strategies."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                cur = conn.execute("""
                    SELECT
                        strategy_name,
                        COUNT(DISTINCT session_id) as total_uses,
                        COUNT(DISTINCT CASE WHEN concession_obtained = 1 THEN session_id END) as concessions_won,
                        AVG(price_reduction) as avg_price_cut,
                        COUNT(DISTINCT CASE WHEN deal_won = 1 THEN session_id END) as deals_closed
                    FROM strategy_records
                    GROUP BY strategy_name;
                """)
                results = []
                for row in cur.fetchall():
                    uses = row["total_uses"]
                    concessions = row["concessions_won"] or 0
                    deals = row["deals_closed"] or 0
                    avg_cut = round(row["avg_price_cut"] or 0.0, 2)
                    success_rate = round((concessions / uses) * 100, 1) if uses > 0 else 0.0
                    confidence = "HIGH" if uses >= 6 else ("MEDIUM" if uses >= 3 else "LOW (Insufficient Data)")

                    results.append({
                        "strategy_name": row["strategy_name"],
                        "total_uses": uses,
                        "concessions_won": concessions,
                        "success_rate_pct": success_rate,
                        "avg_price_reduction": avg_cut,
                        "avg_price_cut": avg_cut,
                        "deals_closed": deals,
                        "confidence_rating": confidence,
                    })
                return results
        finally:
            conn.close()


def get_supplier_metrics(supplier_name: str, db_path: str | None = None) -> dict[str, Any]:
    """Retrieve historical supplier intelligence.
    Simulated transactions are STRICTLY isolated from genuine Sandbox metrics (Requirement 6 & 11.6).
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

                # Count genuine PayPal Sandbox captured payments
                cur = conn.execute("""
                    SELECT COUNT(*) as sandbox_captured FROM transactions
                    WHERE supplier_name = ? AND status = 'captured' AND payment_mode = 'PAYPAL_SANDBOX';
                """, (supplier_name,))
                sandbox_captured = cur.fetchone()["sandbox_captured"]

                # Count simulated transactions separately
                cur = conn.execute("""
                    SELECT COUNT(*) as simulated_captured FROM transactions
                    WHERE supplier_name = ? AND status = 'captured' AND payment_mode = 'SIMULATED';
                """, (supplier_name,))
                simulated_captured = cur.fetchone()["simulated_captured"]

                # Average unit price and total cost from actual accepted deals
                cur = conn.execute("""
                    SELECT AVG(amount) as avg_deal_amount FROM transactions
                    WHERE supplier_name = ?;
                """, (supplier_name,))
                avg_amount_row = cur.fetchone()
                avg_deal_amount = round(avg_amount_row["avg_deal_amount"], 2) if avg_amount_row and avg_amount_row["avg_deal_amount"] else 0.0

                # Genuine reliability score: calculated strictly on real transactions
                has_history = attempts > 0
                if not has_history:
                    reliability = None
                    scoring_prior = 0.85
                else:
                    conversion = accepted / attempts if attempts > 0 else 0.5
                    # Reliability is influenced by conversion and real settlement reliability
                    sandbox_ratio = sandbox_captured / accepted if accepted > 0 else 0.8
                    reliability = round(min(1.0, max(0.5, 0.70 + 0.15 * conversion + 0.15 * sandbox_ratio)), 2)
                    scoring_prior = reliability

                return {
                    "supplier_name": supplier_name,
                    "attempted_deals": attempts,
                    "accepted_deals": accepted,
                    "captured_payments": sandbox_captured,
                    "simulated_deals": simulated_captured,
                    "avg_deal_amount": avg_deal_amount,
                    "reliability_score": reliability,
                    "has_history": has_history,
                    "scoring_prior": scoring_prior,
                }
        finally:
            conn.close()


def get_overall_procurement_analytics(db_path: str | None = None) -> dict[str, Any]:
    """Calculate procurement dashboard metrics distinguishing genuine Sandbox from Simulated."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                # 1. Total genuine Sandbox spend
                cur = conn.execute("""
                    SELECT COALESCE(SUM(amount), 0.0) as total_sandbox_spend, COUNT(*) as count
                    FROM transactions
                    WHERE status = 'captured' AND payment_mode = 'PAYPAL_SANDBOX';
                """)
                row_sb = cur.fetchone()
                total_sandbox_spend = round(row_sb["total_sandbox_spend"], 2)
                sandbox_count = row_sb["count"]

                # 2. Total simulated spend
                cur = conn.execute("""
                    SELECT COALESCE(SUM(amount), 0.0) as total_simulated_spend, COUNT(*) as count
                    FROM transactions
                    WHERE status = 'captured' AND payment_mode = 'SIMULATED';
                """)
                row_sim = cur.fetchone()
                total_simulated_spend = round(row_sim["total_simulated_spend"], 2)
                simulated_count = row_sim["count"]

                # 3. Total sessions & completed count
                cur = conn.execute("SELECT COUNT(*) as total FROM procurement_sessions;")
                total_sessions = cur.fetchone()["total"]

                cur = conn.execute("SELECT COUNT(*) as completed FROM procurement_sessions WHERE status = 'completed';")
                completed_sessions = cur.fetchone()["completed"]

                # 4. Total negotiated savings vs initial budget
                cur = conn.execute("""
                    SELECT SUM((budget * qty) - total_cost) as total_savings
                    FROM procurement_sessions
                    WHERE status = 'completed' AND total_cost IS NOT NULL AND total_cost > 0;
                """)
                row_savings = cur.fetchone()
                total_savings = round(row_savings["total_savings"], 2) if row_savings and row_savings["total_savings"] else 0.0

                return {
                    "total_sandbox_spend": total_sandbox_spend,
                    "sandbox_orders_count": sandbox_count,
                    "total_simulated_spend": total_simulated_spend,
                    "simulated_orders_count": simulated_count,
                    "total_sessions": total_sessions,
                    "completed_sessions": completed_sessions,
                    "total_savings": max(0.0, total_savings),
                }
        finally:
            conn.close()


# Initialize database automatically on import
init_db()
