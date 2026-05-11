"""SQLite persistence for Spendly."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator, Iterable

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "spendly.db"


def db_path() -> Path:
    raw = os.environ.get("SPENDLY_DB_PATH")
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_DB


def ensure_db_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    path = db_path()
    ensure_db_dir(path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_name TEXT NOT NULL,
                purchased_at TEXT NOT NULL,
                total_amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'RUB',
                member_id INTEGER,
                notes TEXT,
                source TEXT DEFAULT 'manual',
                FOREIGN KEY (member_id) REFERENCES members(id)
            );

            CREATE TABLE IF NOT EXISTS receipt_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_id INTEGER NOT NULL,
                product_name TEXT NOT NULL,
                quantity REAL NOT NULL DEFAULT 1,
                unit_price REAL,
                line_total REAL NOT NULL,
                category_id INTEGER,
                FOREIGN KEY (receipt_id) REFERENCES receipts(id) ON DELETE CASCADE,
                FOREIGN KEY (category_id) REFERENCES categories(id)
            );

            CREATE INDEX IF NOT EXISTS idx_receipts_date ON receipts(purchased_at);
            CREATE INDEX IF NOT EXISTS idx_receipts_store ON receipts(store_name);
            CREATE INDEX IF NOT EXISTS idx_lines_receipt ON receipt_lines(receipt_id);
            CREATE INDEX IF NOT EXISTS idx_lines_product ON receipt_lines(product_name);
            """
        )
        cur = conn.execute("SELECT COUNT(*) AS c FROM members")
        if cur.fetchone()["c"] == 0:
            conn.execute(
                "INSERT INTO members (name) VALUES (?), (?)",
                ("Семья", "Общее"),
            )
        cur = conn.execute("SELECT COUNT(*) AS c FROM categories")
        if cur.fetchone()["c"] == 0:
            defaults = (
                "Продукты",
                "Бытовая химия",
                "Аптека",
                "Одежда",
                "Транспорт",
                "Кафе и рестораны",
                "Другое",
            )
            conn.executemany(
                "INSERT OR IGNORE INTO categories (name) VALUES (?)",
                [(n,) for n in defaults],
            )


def list_members() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name FROM members ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]


def add_member(name: str) -> None:
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO members (name) VALUES (?)", (name.strip(),))


def list_categories() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name FROM categories ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]


def add_category(name: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO categories (name) VALUES (?)",
            (name.strip(),),
        )


def insert_receipt(
    store_name: str,
    purchased_at: datetime,
    total_amount: float,
    currency: str,
    member_id: int | None,
    lines: Iterable[dict[str, Any]],
    notes: str | None = None,
    source: str = "manual",
) -> int:
    iso = purchased_at.replace(microsecond=0).isoformat(sep=" ")
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO receipts
            (store_name, purchased_at, total_amount, currency, member_id, notes, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                store_name.strip(),
                iso,
                float(total_amount),
                currency or "RUB",
                member_id,
                notes,
                source,
            ),
        )
        rid = int(cur.lastrowid)
        for ln in lines:
            conn.execute(
                """
                INSERT INTO receipt_lines
                (receipt_id, product_name, quantity, unit_price, line_total, category_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    rid,
                    str(ln["product_name"]).strip(),
                    float(ln.get("quantity") or 1),
                    ln.get("unit_price"),
                    float(ln["line_total"]),
                    ln.get("category_id"),
                ),
            )
        return rid


def delete_receipt(receipt_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM receipt_lines WHERE receipt_id = ?", (receipt_id,))
        conn.execute("DELETE FROM receipts WHERE id = ?", (receipt_id,))


def fetch_receipts_filtered(
    date_from: str | None,
    date_to: str | None,
    store: str | None,
    category_id: int | None,
    member_id: int | None,
    product_query: str | None,
) -> list[dict[str, Any]]:
    sql = """
        SELECT r.id, r.store_name, r.purchased_at, r.total_amount, r.currency,
               r.member_id, m.name AS member_name, r.notes, r.source
        FROM receipts r
        LEFT JOIN members m ON m.id = r.member_id
        WHERE 1=1
    """
    params: list[Any] = []
    if date_from:
        sql += " AND r.purchased_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND r.purchased_at <= ?"
        params.append(date_to + " 23:59:59")
    if store:
        sql += " AND r.store_name LIKE ?"
        params.append(f"%{store}%")
    if member_id:
        sql += " AND r.member_id = ?"
        params.append(member_id)
    if category_id or product_query:
        sql += " AND EXISTS (SELECT 1 FROM receipt_lines l WHERE l.receipt_id = r.id"
        if category_id:
            sql += " AND l.category_id = ?"
            params.append(category_id)
        if product_query:
            sql += " AND l.product_name LIKE ?"
            params.append(f"%{product_query}%")
        sql += ")"

    sql += " ORDER BY r.purchased_at DESC, r.id DESC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def fetch_lines_for_receipt(receipt_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT l.id, l.product_name, l.quantity, l.unit_price, l.line_total,
                   l.category_id, c.name AS category_name
            FROM receipt_lines l
            LEFT JOIN categories c ON c.id = l.category_id
            WHERE l.receipt_id = ?
            ORDER BY l.id
            """,
            (receipt_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def update_line_category(line_id: int, category_id: int | None) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE receipt_lines SET category_id = ? WHERE id = ?",
            (category_id, line_id),
        )


def report_by_store(
    date_from: str | None,
    date_to: str | None,
    member_id: int | None,
) -> list[dict[str, Any]]:
    sql = """
        SELECT r.store_name AS store,
               SUM(r.total_amount) AS total,
               COUNT(*) AS receipts
        FROM receipts r
        WHERE 1=1
    """
    params: list[Any] = []
    if date_from:
        sql += " AND r.purchased_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND r.purchased_at <= ?"
        params.append(date_to + " 23:59:59")
    if member_id:
        sql += " AND r.member_id = ?"
        params.append(member_id)
    sql += " GROUP BY r.store_name ORDER BY total DESC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def report_by_category(
    date_from: str | None,
    date_to: str | None,
    member_id: int | None,
) -> list[dict[str, Any]]:
    sql = """
        SELECT COALESCE(c.name, '(без категории)') AS category,
               SUM(l.line_total) AS total,
               SUM(l.quantity) AS qty
        FROM receipt_lines l
        JOIN receipts r ON r.id = l.receipt_id
        LEFT JOIN categories c ON c.id = l.category_id
        WHERE 1=1
    """
    params: list[Any] = []
    if date_from:
        sql += " AND r.purchased_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND r.purchased_at <= ?"
        params.append(date_to + " 23:59:59")
    if member_id:
        sql += " AND r.member_id = ?"
        params.append(member_id)
    sql += " GROUP BY c.id, c.name ORDER BY total DESC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def report_by_product(
    date_from: str | None,
    date_to: str | None,
    member_id: int | None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    sql = """
        SELECT l.product_name AS product,
               SUM(l.line_total) AS total,
               SUM(l.quantity) AS qty
        FROM receipt_lines l
        JOIN receipts r ON r.id = l.receipt_id
        WHERE 1=1
    """
    params: list[Any] = []
    if date_from:
        sql += " AND r.purchased_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND r.purchased_at <= ?"
        params.append(date_to + " 23:59:59")
    if member_id:
        sql += " AND r.member_id = ?"
        params.append(member_id)
    sql += """
        GROUP BY l.product_name
        ORDER BY total DESC
        LIMIT ?
    """
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
