import sqlite3
import os
import math
import re
import time
import secrets
from datetime import datetime, timedelta
import json
import urllib.request
import urllib.error
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import chromadb
from chromadb.utils import embedding_functions

# Load .env for local (non-Docker) runs; Docker passes variables via env_file
load_dotenv()

# Defaults live in the project root (/app inside Docker, the repo folder locally).
# Empty values (e.g. "DB_PATH=" in .env) fall back to the defaults too.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.getenv('DB_PATH') or os.path.join(PROJECT_ROOT, 'database.db')
DATA_ROOT = os.getenv('DATA_ROOT') or os.path.join(PROJECT_ROOT, 'data')
CHROMA_PATH = os.getenv('CHROMA_PATH') or os.path.join(PROJECT_ROOT, 'chroma_db')

# Base URL of the Ollama server (docker-compose points this at host.docker.internal)
OLLAMA_HOST = (os.getenv('OLLAMA_HOST') or 'http://127.0.0.1:11434').rstrip('/')
OLLAMA_URL = os.getenv('OLLAMA_URL') or f'{OLLAMA_HOST}/api/generate'
OLLAMA_MODEL = os.getenv('OLLAMA_MODEL') or 'gemma2:2b'
OLLAMA_EMBED_MODEL = os.getenv('OLLAMA_EMBED_MODEL') or 'nomic-embed-text'

client = chromadb.PersistentClient(path=CHROMA_PATH)

# Use Ollama to generate embeddings automatically
ollama_ef = embedding_functions.OllamaEmbeddingFunction(
    url=OLLAMA_HOST,
    model_name=OLLAMA_EMBED_MODEL,
    timeout=300,  # first call loads the model, which can exceed the 60s default on CPU-only machines
)

# Get or create your collection
collection = client.get_or_create_collection(name="documents", embedding_function=ollama_ef)

# Built-in categories in display order; 'document' holds the governance documents
DEFAULT_CATEGORIES = ['document', 'compliance', 'procurement']

# Admins can also use any category they create; see get_allowed_categories()
ROLE_ACCESS = {
    'admin': DEFAULT_CATEGORIES,
    'reporter': ['compliance', 'procurement'],
    'user': ['document'],
}

ROLE_LABELS = {
    'admin': 'All categories',
    'reporter': 'Procurement and compliance',
    'user': 'Governance documents only',
}

# Login accounts come from .env: (username variable, password variable, role)
ACCOUNTS = (
    ('ADMIN_USER', 'ADMIN_PASS', 'admin'),
    ('REPORTER_USER', 'REPORTER_PASS', 'reporter'),
    ('USER_USER', 'USER_PASS', 'user'),
)

RISK_LEVELS = ('Low', 'Medium', 'High', 'Critical')

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_schema():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        '''CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            upload_date TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )'''
    )
    # Databases created by older versions lack upload_date
    columns = {row['name'] for row in cursor.execute('PRAGMA table_info(documents)')}
    if 'upload_date' not in columns:
        cursor.execute('ALTER TABLE documents ADD COLUMN upload_date TEXT')
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
    # Every AI Studio answer: the user's conversation history and the dashboard's AI analytics.
    # "New conversation" only archives rows, so the analytics keep counting them.
    cursor.execute(
        '''CREATE TABLE IF NOT EXISTS ai_queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            question TEXT NOT NULL,
            result TEXT NOT NULL,
            risk_level TEXT,
            confidence INTEGER,
            response_time REAL,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )'''
    )
    conn.commit()
    conn.close()

def _configured_accounts():
    """(username, password, role) for every account set in .env; accounts without a password are skipped."""
    for user_var, pass_var, role in ACCOUNTS:
        username, password = os.getenv(user_var), os.getenv(pass_var)
        if username and password:
            yield username, password, role

def authenticate_user(username, password):
    for account_user, account_pass, role in _configured_accounts():
        if username == account_user and secrets.compare_digest(str(password or '').encode(), account_pass.encode()):
            return {
                'username': username,
                'role': role,
                'allowed_categories': get_allowed_categories(role),
            }
    return None

def list_accounts():
    """The login accounts from .env, without their passwords."""
    return [
        {'username': username, 'role': role, 'access': get_access_label(role)}
        for username, _, role in _configured_accounts()
    ]

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

def normalize_category(name):
    """Folder-safe category name: 'Policy Updates' -> 'policy_updates'. Returns '' when nothing usable is left."""
    return re.sub(r'[^a-z0-9_]', '', (name or '').strip().lower().replace(' ', '_'))

def list_categories():
    """Every category: the built-in ones first, then any other folder in DATA_ROOT or category in SQLite."""
    found = set()
    if os.path.isdir(DATA_ROOT):
        found.update(
            name for name in os.listdir(DATA_ROOT)
            if os.path.isdir(os.path.join(DATA_ROOT, name)) and name == normalize_category(name)
        )
    conn = get_db_connection()
    try:
        found.update(row['category'] for row in conn.execute('SELECT DISTINCT category FROM documents'))
    finally:
        conn.close()
    return DEFAULT_CATEGORIES + sorted(found - set(DEFAULT_CATEGORIES))

# Return access based on hardcoded category
def get_allowed_categories(role):
    """Categories a role may read and upload to. Admins get every category, including ones they created."""
    if role == 'admin':
        return list_categories()
    return list(ROLE_ACCESS.get(role, []))


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

def get_documents(role):
    if role == 'admin':
        return get_documents_by_category()

    documents = []
    for category in get_allowed_categories(role):
        documents.extend(get_documents_by_category(category))
    return documents

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

    try:
        collection.delete(ids=[str(document['id'])])
    except Exception as e:
        print(f"⚠️ Vector DB warning: could not remove document {document['id']}: {e}", flush=True)
    return document

def create_category(category_name):
    safe_category = normalize_category(category_name)
    if not safe_category:
        return None
    os.makedirs(os.path.join(DATA_ROOT, safe_category), exist_ok=True)
    return safe_category

def sanitize_text(text):
    if not isinstance(text, str):
        return ""

    # 1. Standardize line endings
    text = text.replace('\r\n', '\n').replace('\r', '\n')

    # 2. Remove non-printable control characters (ASCII 0-31, 127)
    # This removes Null bytes, Bell, Backspace, etc.
    text = re.sub(r'[\x00-\x1F\x7F]', '', text)

    # 3. Remove Byte Order Mark (BOM) if present
    text = text.replace('﻿', '')

    # 4. Normalize whitespace (optional: converts multiple spaces/tabs into a single space)
    text = re.sub(r'[ \t]+', ' ', text)

    return text.strip()

def save_text_document(category, filename, content, actor, action,
                        previous_category=None, previous_filename=None):
    """Targeted update for individual document operations. Raises ValueError for an unusable category or filename."""
    # 1. Sanitization & Pathing: names end up in file paths, so they must stay inside DATA_ROOT
    category = normalize_category(category)
    filename = secure_filename((filename or '').strip())
    if not category:
        raise ValueError('A valid category is required')
    if not filename:
        raise ValueError('A valid filename is required')
    previous_category = normalize_category(previous_category) or None
    previous_filename = secure_filename(previous_filename or '') or None

    # 2. File System Update
    target_dir = os.path.join(DATA_ROOT, category)
    os.makedirs(target_dir, exist_ok=True)
    file_path = os.path.join(target_dir, filename)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

    # 3. Clean old file if moving/renaming
    if previous_category and previous_filename:
        old_path = os.path.join(DATA_ROOT, previous_category, previous_filename)
        if os.path.exists(old_path) and old_path != file_path:
            os.remove(old_path)

    # 4. Targeted Database & Vector Update
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # Find ID to ensure we update the correct vector
        cursor.execute('SELECT id FROM documents WHERE filename = ? AND category = ?',
                       (previous_filename or filename, previous_category or category))
        row = cursor.fetchone()

        if row:
            doc_id = row['id']
            cursor.execute('UPDATE documents SET content = ?, category = ?, filename = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                           (content, category, filename, doc_id))
        else:
            today = datetime.now().strftime('%Y-%m-%d')
            cursor.execute('''
                INSERT INTO documents (filename, category, content, upload_date, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (filename, category, content, today))
            doc_id = cursor.lastrowid

        conn.commit()
    finally:
        conn.close()

    # 5. RAG Sync: the document is already saved, so a vector failure
    # (e.g. Ollama not running) must not turn the upload into an error.
    # Missing vectors are filled in later by sync_vector_index().
    index_document(doc_id, category, filename, content)

    # 6. Audit
    log_audit(actor=actor, action=action, category=category, filename=filename, details="Updated via API")
    return file_path

def index_document(doc_id, category, filename, content):
    """Upsert one document into the vector DB. Returns True on success."""
    clean_content = sanitize_text(content)
    if not clean_content:
        print(f"⚠️ Vector DB warning: {filename} is empty, skipping embedding.", flush=True)
        return False
    try:
        collection.upsert(
            ids=[str(doc_id)],
            documents=[clean_content],
            metadatas=[{"category": category, "filename": filename}]
        )
        print(f"✅ Successfully upserted {filename} to Vector DB.", flush=True)
        return True
    except Exception as e:
        print(f"⚠️ Vector DB warning: Failed to embed document {filename}. Error: {e}", flush=True)
        return False

def sync_vector_index(force=False):
    """Embed SQLite documents that are missing from the vector DB (all of them if force=True),
    and drop vectors whose document no longer exists so searches can't return it."""
    documents = get_documents_by_category()
    document_ids = {str(doc['id']) for doc in documents}
    vector_ids = set(collection.get(include=[])['ids'])
    stale = sorted(vector_ids - document_ids)
    if stale:
        collection.delete(ids=stale)

    missing = documents if force else [doc for doc in documents if str(doc['id']) not in vector_ids]
    indexed = sum(
        index_document(doc['id'], doc['category'], doc['filename'], doc['content'])
        for doc in missing
    )
    return {'total': len(documents), 'checked': len(missing), 'indexed': indexed, 'removed': len(stale)}

def import_data_folder():
    """Add the .txt files in DATA_ROOT/<category>/ that SQLite doesn't have yet. Returns how many were added.

    A new or deleted database is rebuilt from data/ this way; their vectors follow via sync_vector_index().
    """
    if not os.path.isdir(DATA_ROOT):
        return 0
    conn = get_db_connection()
    try:
        known = {(row['category'], row['filename']) for row in conn.execute('SELECT category, filename FROM documents')}
        today = datetime.now().strftime('%Y-%m-%d')
        imported = []
        for category in sorted(os.listdir(DATA_ROOT)):
            folder = os.path.join(DATA_ROOT, category)
            if not os.path.isdir(folder) or category != normalize_category(category):
                continue
            for filename in sorted(os.listdir(folder)):
                if not filename.lower().endswith('.txt') or (category, filename) in known:
                    continue
                with open(os.path.join(folder, filename), encoding='utf-8', errors='ignore') as f:
                    content = f.read().strip()
                if content:
                    conn.execute(
                        'INSERT INTO documents (filename, category, content, upload_date) VALUES (?, ?, ?, ?)',
                        (filename, category, content, today),
                    )
                    imported.append((category, filename))
        conn.commit()
    finally:
        conn.close()

    for category, filename in imported:
        log_audit('system', 'import', category=category, filename=filename, details='Imported from the data folder')
    return len(imported)

def bootstrap_database():
    ensure_schema()
    imported = import_data_folder()
    if imported:
        print(f"📥 Imported {imported} new document(s) from {DATA_ROOT}", flush=True)

def call_local_model(prompt, response_format=None):
    try:
        body = {
            'model': OLLAMA_MODEL,
            'prompt': prompt,
            'stream': False,
        }
        if response_format:
            body['format'] = response_format
        payload = json.dumps(body).encode('utf-8')

        request = urllib.request.Request(
            OLLAMA_URL, # Ensure this is http://ollama:11434/api/generate
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )

        with urllib.request.urlopen(request, timeout=600) as response:
            response_data = json.loads(response.read().decode('utf-8'))
            return response_data.get('response', '').strip()

    except urllib.error.URLError as e:
        print(f"CRITICAL: Ollama Connection Failed. URL: {OLLAMA_URL}. Error: {e.reason}", flush=True)
        raise e
    except Exception as e:
        print(f"CRITICAL: Unexpected error in call_local_model: {str(e)}", flush=True)
        raise e

def _cosine_similarity(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0

def _keyword_search(user_query, n_results, categories=None):
    """Fallback when the vector DB is empty or Ollama embeddings are unavailable.

    Ranks documents by the query words they contain, counting words that few documents share
    (and words in the filename) for more. Relevance is the share of query words a document contains.
    """
    terms = {t for t in re.findall(r'[a-z0-9]+', user_query.lower()) if len(t) > 2}
    documents = [
        (doc, doc['filename'].lower(), f"{doc['category']} {doc['content']}".lower())
        for doc in get_documents_by_category()
        if categories is None or doc['category'] in categories
    ]
    # Rarer words say more about which document is meant: "supplier" counts for more than "which"
    weights = {}
    for term in terms:
        containing = sum(1 for _, name, text in documents if term in name or term in text)
        if containing:
            weights[term] = 1 + math.log(len(documents) / containing)

    scored = []
    for doc, name, text in documents:
        found = [term for term in weights if term in name or term in text]
        if found:
            score = sum(weights[term] * (2 if term in name else 1) for term in found)
            scored.append((score, len(found), doc))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            'filename': doc['filename'],
            'category': doc['category'],
            'content': doc['content'],
            'relevance': round(100 * hits / len(terms)),
        }
        for _, hits, doc in scored[:n_results]
    ]

def search_documents(user_query, n_results=3, categories=None):
    """Return the most relevant documents as dicts with a 0-100 relevance score.

    categories limits the search to those categories; None searches all of them.
    """
    if categories is not None and not categories:
        return []
    try:
        if collection.count() == 0:
            raise LookupError('vector DB is empty')
        query_embedding = ollama_ef([user_query])[0]
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where={'category': {'$in': list(categories)}} if categories is not None else None,
            include=['documents', 'metadatas', 'embeddings'],
        )
        matches = []
        for doc, meta, emb in zip(results['documents'][0], results['metadatas'][0], results['embeddings'][0]):
            meta = meta or {}
            matches.append({
                'filename': meta.get('filename', 'Unknown Source'),
                'category': meta.get('category', ''),
                'content': doc,
                'relevance': max(0, round(100 * _cosine_similarity(query_embedding, emb))),
            })
        return matches
    except Exception as e:
        print(f"⚠️ Vector search unavailable ({e}); using keyword search.", flush=True)
        return _keyword_search(user_query, n_results, categories)

def _excerpt(content, query, length=220):
    """About `length` characters around the first query word the document contains, cut at whole words."""
    lowered = content.lower()
    start = 0
    for term in re.findall(r'[a-z0-9]+', query.lower()):
        index = lowered.find(term) if len(term) > 2 else -1
        if index != -1:
            start = max(index - 60, 0)
            break
    end = min(start + length, len(content))
    # Don't start or end in the middle of a word
    if start > 0 and not content[start - 1].isspace():
        start = content.find(' ', start) + 1 or start
    if end < len(content):
        cut = content.rfind(' ', start, end)
        end = cut if cut > start else end
    text = ' '.join(content[start:end].split())
    return ('…' if start > 0 else '') + text + ('…' if end < len(content) else '')

def answer_question(question, n_results=4, categories=None):
    """RAG answer in the structured shape AI Studio renders.

    categories limits the search to those categories; None searches all of them.
    """
    started = time.time()
    matches = search_documents(question, n_results, categories)
    if not matches:
        # Nothing to ground an answer on, so don't let the model guess
        return {
            'answer': 'No matching documents were found for your allowed categories.',
            'key_findings': '',
            'reasoning': '',
            'risk_level': 'Low',
            'recommendation': '',
            'confidence': 0,
            'sources': [],
            'response_time': round(time.time() - started, 2),
        }

    context = ''.join(
        f"Source: {m['filename']} (category: {m['category']})\nContent: {m['content'][:2000]}\n\n"
        for m in matches
    ) or "No relevant information found."

    prompt = (
        "You are a governance, compliance and procurement analyst. Answer the question using ONLY "
        "the context below. If the context does not contain the answer, say so.\n"
        "Respond with a JSON object with exactly these keys:\n"
        '  "answer": short direct answer,\n'
        '  "key_findings": the key facts from the context,\n'
        '  "reasoning": how the context supports the answer,\n'
        '  "risk_level": one of "Low", "Medium", "High", "Critical",\n'
        '  "recommendation": recommended next step,\n'
        '  "confidence": integer 0-100.\n\n'
        f"Context:\n{context}\nQuestion: {question}"
    )
    raw = call_local_model(prompt, response_format='json')
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if not isinstance(parsed, dict):
        parsed = {'answer': raw}

    def text(key, default=''):
        value = parsed.get(key, default)
        if isinstance(value, list):
            # The model often returns key findings as a list; show it as bullet lines
            return '\n'.join(f"- {item if isinstance(item, str) else json.dumps(item)}" for item in value)
        return value if isinstance(value, str) else json.dumps(value)

    try:
        confidence = max(0, min(100, int(float(parsed.get('confidence', 0)))))
    except (TypeError, ValueError):
        confidence = 0
    risk_level = text('risk_level', 'Low').capitalize()
    if risk_level not in RISK_LEVELS:
        risk_level = 'Low'

    return {
        'answer': text('answer'),
        'key_findings': text('key_findings'),
        'reasoning': text('reasoning'),
        'risk_level': risk_level,
        'recommendation': text('recommendation'),
        'confidence': confidence,
        'sources': [
            {
                'title': m['filename'],
                'category': m['category'],
                'relevance': m['relevance'],
                'excerpt': _excerpt(m['content'], question),
            }
            for m in matches
        ],
        'response_time': round(time.time() - started, 2),
    }

def record_ai_query(username, question, result):
    """Save an answer to the user's AI Studio conversation (and the dashboard analytics). Returns its id."""
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            'INSERT INTO ai_queries (username, question, result, risk_level, confidence, response_time) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (username, question, json.dumps(result), result['risk_level'], result['confidence'], result['response_time']),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()

def get_ai_history(username, limit=20):
    """The user's current AI Studio conversation, oldest question first."""
    conn = get_db_connection()
    try:
        rows = conn.execute(
            'SELECT id, question, result, created_at FROM ai_queries '
            'WHERE username = ? AND archived = 0 ORDER BY id DESC LIMIT ?',
            (username, limit),
        ).fetchall()
    finally:
        conn.close()

    history = []
    for row in reversed(rows):
        try:
            result = json.loads(row['result'])
        except json.JSONDecodeError:
            continue
        history.append({'id': row['id'], 'question': row['question'], 'created_at': row['created_at'], 'result': result})
    return history

def archive_ai_history(username):
    """Start a new conversation for the user. Returns how many answers were archived."""
    conn = get_db_connection()
    try:
        cursor = conn.execute('UPDATE ai_queries SET archived = 1 WHERE username = ? AND archived = 0', (username,))
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()

def get_dashboard_metrics(categories=None):
    """Dashboard numbers: document counts for the given categories (every category when None)
    and AI usage across all users. Days are counted in the server's local time."""
    if categories is None:
        categories = list_categories()
    week_start = "date('now', 'localtime', '-6 days')"
    conn = get_db_connection()
    try:
        counts = {row['category']: row['count'] for row in conn.execute(
            'SELECT category, COUNT(*) AS count FROM documents GROUP BY category')}
        last_updated = conn.execute(
            f"SELECT MAX(updated_at) FROM documents WHERE category IN ({', '.join('?' * len(categories))})",
            categories,
        ).fetchone()[0] if categories else None
        today = conn.execute(
            'SELECT COUNT(*) AS queries, AVG(confidence) AS confidence, AVG(response_time) AS response_time '
            "FROM ai_queries WHERE date(created_at, 'localtime') = date('now', 'localtime')"
        ).fetchone()
        risks = {row['risk_level']: row['count'] for row in conn.execute(
            'SELECT risk_level, COUNT(*) AS count FROM ai_queries '
            f"WHERE date(created_at, 'localtime') >= {week_start} GROUP BY risk_level")}
        trend = {row['day']: row['count'] for row in conn.execute(
            "SELECT date(created_at, 'localtime') AS day, COUNT(*) AS count FROM ai_queries "
            f"WHERE date(created_at, 'localtime') >= {week_start} GROUP BY day")}
    finally:
        conn.close()

    days = [datetime.now().date() - timedelta(days=n) for n in range(6, -1, -1)]
    return {
        'documents': [{'category': category, 'count': counts.get(category, 0)} for category in categories],
        'totalDocuments': sum(counts.get(category, 0) for category in categories),
        'lastUpdated': last_updated,
        'aiQueriesToday': today['queries'],
        'avgConfidence': round(today['confidence'] or 0),
        'avgResponseTime': round(today['response_time'] or 0, 1),
        'riskLevels': {level: risks.get(level, 0) for level in RISK_LEVELS},
        'trendLabels': [f"{day:%a} {day.day}" for day in days],
        'trendData': [trend.get(day.isoformat(), 0) for day in days],
    }

def _installed_models():
    """Names of the models Ollama has pulled, or None when Ollama can't be reached."""
    try:
        with urllib.request.urlopen(f'{OLLAMA_HOST}/api/tags', timeout=3) as response:
            tags = json.loads(response.read().decode('utf-8'))
    except (OSError, ValueError):
        return None
    return {model.get('name') for model in tags.get('models', [])}

def get_system_status():
    """Health of the AI service, models and vector index, for the admin Settings page."""
    installed = _installed_models()

    def ready(model):
        return installed is not None and (model if ':' in model else f'{model}:latest') in installed

    try:
        indexed = collection.count()
    except Exception:
        indexed = None
    return {
        'ollama_host': OLLAMA_HOST,
        'ollama_online': installed is not None,
        'chat_model': OLLAMA_MODEL,
        'chat_model_ready': ready(OLLAMA_MODEL),
        'embed_model': OLLAMA_EMBED_MODEL,
        'embed_model_ready': ready(OLLAMA_EMBED_MODEL),
        'indexed_documents': indexed,
        'database': DB_PATH,
        'data_folder': DATA_ROOT,
        'vector_store': CHROMA_PATH,
    }
