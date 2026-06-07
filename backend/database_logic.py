import sqlite3
import os
from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'database.db')
DATA_ROOT = os.path.join(os.path.dirname(__file__), '..', 'data')

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        '''CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,
            filename TEXT NOT NULL,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )'''
    )
    cursor.execute(
        '''CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            category TEXT,
            filename TEXT,
            details TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )'''
    )
    cursor.execute(
        '''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            allowed_categories TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )'''
    )

    existing_document_columns = {
        row['name'] for row in cursor.execute('PRAGMA table_info(documents)').fetchall()
    }
    if 'updated_at' not in existing_document_columns and existing_document_columns:
        cursor.execute('ALTER TABLE documents ADD COLUMN updated_at TEXT')
        cursor.execute('UPDATE documents SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL')

    existing_user_columns = {row['name'] for row in cursor.execute('PRAGMA table_info(users)').fetchall()}
    if 'allowed_categories' not in existing_user_columns and existing_user_columns:
        cursor.execute('ALTER TABLE users ADD COLUMN allowed_categories TEXT')

    conn.commit()
    conn.close()


def seed_default_users():
    ensure_schema()
    conn = get_db_connection()
    cursor = conn.cursor()
    user_count = cursor.execute('SELECT COUNT(*) AS count FROM users').fetchone()['count']
    if user_count == 0:
        default_accounts = [
            (os.getenv('ADMIN_USER', 'admin'), os.getenv('ADMIN_PASS', 'admin123'), 'admin', 'procurement,governance,important,general'),
            (os.getenv('USER_USER', 'staff'), os.getenv('USER_PASS', 'staff123'), 'user', 'procurement'),
        ]
        for username, password, role, allowed_categories in default_accounts:
            cursor.execute(
                'INSERT INTO users (username, password_hash, role, allowed_categories) VALUES (?, ?, ?, ?)',
                (username, generate_password_hash(password), role, allowed_categories),
            )
        conn.commit()
    conn.close()


def authenticate_user(username, password):
    ensure_schema()
    conn = get_db_connection()
    user = conn.execute('SELECT username, password_hash, role, allowed_categories FROM users WHERE username = ?', (username,)).fetchone()
    conn.close()
    if user and check_password_hash(user['password_hash'], password):
        return {
            'username': user['username'],
            'role': user['role'],
            'allowed_categories': user['allowed_categories'],
        }
    return None


def add_user(username, password, role, allowed_categories=''):
    ensure_schema()
    conn = get_db_connection()
    conn.execute(
        'INSERT INTO users (username, password_hash, role, allowed_categories) VALUES (?, ?, ?, ?)',
        (username, generate_password_hash(password), role, allowed_categories),
    )
    conn.commit()
    conn.close()


def list_users():
    ensure_schema()
    conn = get_db_connection()
    users = conn.execute('SELECT id, username, role, allowed_categories, created_at FROM users ORDER BY role, username').fetchall()
    conn.close()
    return users


def get_user_by_id(user_id):
    ensure_schema()
    conn = get_db_connection()
    user = conn.execute(
        'SELECT id, username, role, allowed_categories, created_at FROM users WHERE id = ?',
        (user_id,),
    ).fetchone()
    conn.close()
    return user


def update_user(user_id, username, role, allowed_categories):
    ensure_schema()
    conn = get_db_connection()
    conn.execute(
        'UPDATE users SET username = ?, role = ?, allowed_categories = ? WHERE id = ?',
        (username, role, allowed_categories, user_id),
    )
    conn.commit()
    conn.close()


def delete_user_by_id(user_id):
    ensure_schema()
    conn = get_db_connection()
    conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()


def log_audit(actor, action, category=None, filename=None, details=None):
    ensure_schema()
    conn = get_db_connection()
    conn.execute(
        'INSERT INTO audit_logs (actor, action, category, filename, details) VALUES (?, ?, ?, ?, ?)',
        (actor, action, category, filename, details),
    )
    conn.commit()
    conn.close()


def list_audit_logs(limit=50):
    ensure_schema()
    conn = get_db_connection()
    logs = conn.execute(
        'SELECT actor, action, category, filename, details, created_at FROM audit_logs ORDER BY id DESC LIMIT ?',
        (limit,),
    ).fetchall()
    conn.close()
    return logs


def get_document_counts():
    ensure_schema()
    conn = get_db_connection()
    rows = conn.execute(
        'SELECT category, COUNT(*) AS count FROM documents GROUP BY category ORDER BY category'
    ).fetchall()
    conn.close()
    return rows


def get_documents_by_category(category=None):
    ensure_schema()
    conn = get_db_connection()
    if category:
        rows = conn.execute(
            'SELECT id, filename, category, content, updated_at FROM documents WHERE category = ? ORDER BY filename',
            (category,),
        ).fetchall()
    else:
        rows = conn.execute(
            'SELECT id, filename, category, content, updated_at FROM documents ORDER BY category, filename'
        ).fetchall()
    conn.close()
    return rows


def get_document_by_id(document_id):
    ensure_schema()
    conn = get_db_connection()
    row = conn.execute(
        'SELECT id, filename, category, content, updated_at FROM documents WHERE id = ?',
        (document_id,),
    ).fetchone()
    conn.close()
    return row


def delete_document_by_id(document_id):
    document = get_document_by_id(document_id)
    if not document:
        return None

    file_path = os.path.join(DATA_ROOT, document['category'], document['filename'])
    if os.path.exists(file_path):
        os.remove(file_path)

    conn = get_db_connection()
    conn.execute('DELETE FROM documents WHERE id = ?', (document_id,))
    conn.commit()
    conn.close()
    return document


def list_categories():
    categories = []
    if os.path.isdir(DATA_ROOT):
        for entry in sorted(os.listdir(DATA_ROOT)):
            entry_path = os.path.join(DATA_ROOT, entry)
            if os.path.isdir(entry_path):
                categories.append(entry)
    return categories


def create_category(category_name):
    ensure_schema()
    safe_category = category_name.strip().lower().replace(' ', '_')
    if not safe_category:
        return None
    os.makedirs(os.path.join(DATA_ROOT, safe_category), exist_ok=True)
    return safe_category


def save_text_document(category, filename, content, actor, action, previous_category=None, previous_filename=None):
    ensure_schema()
    os.makedirs(os.path.join(DATA_ROOT, category), exist_ok=True)
    file_path = os.path.join(DATA_ROOT, category, filename)
    with open(file_path, 'w', encoding='utf-8') as file_handle:
        file_handle.write(content)

    if previous_category and previous_filename and (
        previous_category != category or previous_filename != filename
    ):
        old_path = os.path.join(DATA_ROOT, previous_category, previous_filename)
        if os.path.exists(old_path) and old_path != file_path:
            os.remove(old_path)

    ingest_data()
    log_audit(
        actor=actor,
        action=action,
        category=category,
        filename=filename,
        details=f'{action} by {actor} at {datetime.utcnow().isoformat()}Z',
    )
    return file_path

def ingest_data():
    ensure_schema()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY, filename TEXT NOT NULL, category TEXT NOT NULL, content TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    cursor.execute('DELETE FROM documents')
    for category in os.listdir(DATA_ROOT):
        cat_path = os.path.join(DATA_ROOT, category)
        if os.path.isdir(cat_path):
            for filename in os.listdir(cat_path):
                if filename.endswith('.txt'):
                    with open(os.path.join(cat_path, filename), 'r', encoding='utf-8') as f:
                        cursor.execute(
                            'INSERT INTO documents (filename, category, content, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)',
                            (filename, category, f.read()),
                        )
    conn.commit()
    conn.close()


def bootstrap_database():
    ensure_schema()
    seed_default_users()
    ingest_data()