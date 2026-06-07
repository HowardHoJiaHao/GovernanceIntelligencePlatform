import sqlite3
import os
from datetime import datetime
import json
import urllib.request

ROLE_ACCESS = {
    'admin': ['procurement', 'document', 'compliance'],
    'reporter': ['procurement', 'compliance'],
    'user': ['document'],
}

ROLE_LABELS = {
    'admin': 'All categories',
    'reporter': 'Procurement and compliance',
    'user': 'Procurement only',
}

OLLAMA_URL = os.getenv('OLLAMA_URL', 'http://localhost:11434/api/generate')
OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'gemma2:2b')

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
    conn.commit()
    conn.close()

def authenticate_user(username, password):
    # Mapping of usernames to their corresponding environment variables
    # This keeps your lookup logic clean
    creds = {
        os.getenv('ADMIN_USER'): {'user': os.getenv('ADMIN_USER'), 'pass': os.getenv('ADMIN_PASS'), 'role': 'admin'},
        os.getenv('REPORTER_USER'): {'user': os.getenv('REPORTER_USER'), 'pass': os.getenv('REPORTER_PASS'), 'role': 'reporter'},
        os.getenv('USER_USER'): {'user': os.getenv('USER_USER'), 'pass': os.getenv('USER_PASS'), 'role': 'user'},
    }

    # Verify user exists and password matches
    if username in creds and password == creds[username]['pass']:
        role = creds[username]['role']
        return {
            'username': username,
            'role': role,
            'allowed_categories': ROLE_ACCESS.get(role, [])
        }
    return None

def log_audit(actor, action, category=None, filename=None, details=None):
    conn = get_db_connection()
    conn.execute(
        'INSERT INTO audit_logs (actor, action, category, filename, details) VALUES (?, ?, ?, ?, ?)',
        (actor, action, category, filename, details),
    )
    conn.commit()
    conn.close()

def list_audit_logs(limit=50):
    conn = get_db_connection()
    logs = conn.execute(
        'SELECT actor, action, category, filename, details, created_at FROM audit_logs ORDER BY id DESC LIMIT ?',
        (limit,),
    ).fetchall()
    conn.close()
    return logs

def get_document_counts():
    conn = get_db_connection()
    rows = conn.execute(
        'SELECT category, COUNT(*) AS count FROM documents GROUP BY category ORDER BY category'
    ).fetchall()
    conn.close()
    return rows

def get_access_label(role):
    # 1. Use the dictionary to look up the label directly
    # 2. .get(role, ...) provides a safe default if the role isn't in your list
    label = ROLE_LABELS.get(role)
    # If the role is found in your dictionary, return that label
    if label:
        return label
    # Fallback: Logic for roles not in your list
    allowed = get_allowed_categories(role)
    if not allowed:
        return 'No document access'
    # Default behavior for any other roles: Capitalize the list of categories
    return ', '.join(category.replace('_', ' ').title() for category in allowed)

# Return acces based on hardcoded category
def get_allowed_categories(role):
    return ROLE_ACCESS.get(role, [])

def get_documents(role):
    if role == 'admin':
        return get_documents_by_category()

    documents = []
    for category in get_allowed_categories(role):
        documents.extend(get_documents_by_category(category))
    return documents

# Done
def get_documents_by_category(category=None):
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

def create_category(category_name):
    safe_category = category_name.strip().lower().replace(' ', '_')
    if not safe_category:
        return None
    os.makedirs(os.path.join(DATA_ROOT, safe_category), exist_ok=True)
    return safe_category

def save_text_document(category, filename, content, actor, action, previous_category=None, previous_filename=None):
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
    # seed_default_users()
    # ingest_data()
    # get_allowed_categories(current_username())

def call_local_model(prompt):
    payload = json.dumps({
        'model': OLLAMA_MODEL,
        'prompt': prompt,
        'stream': False,
    }).encode('utf-8')

    request = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )

    with urllib.request.urlopen(request, timeout=540) as response:
        response_data = json.loads(response.read().decode('utf-8'))
        
    return response_data.get('response', '').strip()