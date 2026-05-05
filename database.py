import os
import sqlite3
from contextlib import contextmanager
from typing import Optional

_DATA_DIR = os.getenv('DATA_DIR', '.')
DB_PATH = os.path.join(_DATA_DIR, 'expenses.db')


@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                user_id   INTEGER PRIMARY KEY,
                username  TEXT NOT NULL,
                joined_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS expenses (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                username    TEXT NOT NULL,
                amount      REAL NOT NULL,
                category    TEXT NOT NULL,
                subcategory TEXT DEFAULT '',
                description TEXT DEFAULT '',
                created_at  TEXT DEFAULT (datetime('now'))
            );
        ''')
        try:
            conn.execute("ALTER TABLE expenses ADD COLUMN subcategory TEXT DEFAULT ''")
        except Exception:
            pass


def upsert_user(user_id: int, username: str):
    with _db() as conn:
        conn.execute(
            'INSERT OR REPLACE INTO users (user_id, username) VALUES (?, ?)',
            (user_id, username),
        )


def get_all_user_ids() -> list[int]:
    with _db() as conn:
        rows = conn.execute('SELECT user_id FROM users').fetchall()
        return [r['user_id'] for r in rows]


def add_expense(user_id: int, username: str, amount: float, category: str, subcategory: str, description: str):
    with _db() as conn:
        conn.execute(
            'INSERT INTO expenses (user_id, username, amount, category, subcategory, description) VALUES (?,?,?,?,?,?)',
            (user_id, username, amount, category, subcategory, description),
        )


def delete_expense(expense_id: int):
    with _db() as conn:
        conn.execute('DELETE FROM expenses WHERE id = ?', (expense_id,))


def get_expense_by_id(expense_id: int) -> Optional[dict]:
    with _db() as conn:
        row = conn.execute('SELECT * FROM expenses WHERE id = ?', (expense_id,)).fetchone()
        return dict(row) if row else None


def get_monthly_expenses(month: int, year: int) -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            '''SELECT * FROM expenses
               WHERE strftime('%m', created_at) = ?
                 AND strftime('%Y', created_at) = ?
               ORDER BY created_at DESC''',
            (f'{month:02d}', str(year)),
        ).fetchall()
        return [dict(r) for r in rows]


def clear_expenses():
    with _db() as conn:
        conn.execute('DELETE FROM expenses')


def get_today_total() -> float:
    with _db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) as total FROM expenses WHERE date(created_at) = date('now')"
        ).fetchone()
        return row['total']
