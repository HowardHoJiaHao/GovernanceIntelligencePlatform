import sqlite3
import os
import math
import re
import time
from datetime import datetime
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
OLLAMA_HOST = (os.getenv('OLLAMA_HOST') or 'http://localhost:11434').rstrip('/')
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

ROLE_ACCESS = {
    'admin': ['procurement', 'document', 'compliance'],
    'reporter': ['procurement', 'compliance'],
    'user': ['document'],
}

ROLE_LABELS = {
    'admin': 'All categories',
    'reporter': 'Procurement and compliance',
    'user': 'Governance documents only',
}

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
    # This will now correctly see the ROLE_ACCESS dictionary
    categories = ROLE_ACCESS.get(role, [])
    print(f"DEBUG: Role '{role}' mapped to categories: {categories}", flush=True)
    return categories


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
    safe_category = category_name.strip().lower().replace(' ', '_')
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
    text = text.replace('\ufeff', '')
    
    # 4. Normalize whitespace (optional: converts multiple spaces/tabs into a single space)
    text = re.sub(r'[ \t]+', ' ', text)
    
    return text.strip()

def save_text_document(category, filename, content, actor, action, 
                        previous_category=None, previous_filename=None):
    """Targeted update for individual document operations."""
    # 1. Sanitization & Pathing
    category = category.strip()
    filename = secure_filename(filename.strip())
    
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
    """Embed SQLite documents that are missing from the vector DB (all of them if force=True)."""
    documents = get_documents_by_category()
    if force:
        missing = documents
    else:
        existing = set(collection.get(ids=[str(doc['id']) for doc in documents], include=[])['ids']) if documents else set()
        missing = [doc for doc in documents if str(doc['id']) not in existing]

    indexed = sum(
        index_document(doc['id'], doc['category'], doc['filename'], doc['content'])
        for doc in missing
    )
    return {'total': len(documents), 'checked': len(missing), 'indexed': indexed}

def bootstrap_database():
    ensure_schema()

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
    """Fallback when the vector DB is empty or Ollama embeddings are unavailable."""
    terms = {t for t in re.findall(r'[a-z0-9]+', user_query.lower()) if len(t) > 2}
    scored = []
    for doc in get_documents_by_category():
        if categories is not None and doc['category'] not in categories:
            continue
        haystack = f"{doc['filename']} {doc['category']} {doc['content']}".lower()
        hits = sum(1 for t in terms if t in haystack)
        if hits:
            scored.append((hits, doc))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            'filename': doc['filename'],
            'category': doc['category'],
            'content': doc['content'],
            'relevance': round(100 * hits / len(terms)),
        }
        for hits, doc in scored[:n_results]
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

def get_relevant_context(user_query, n_results=3):
    matches = search_documents(user_query, n_results)
    if not matches:
        return "No relevant information found."
    return ''.join(f"Source: {m['filename']}\nContent: {m['content']}\n\n" for m in matches)

def _excerpt(content, query, length=160):
    """The part of a document around the first query word it contains."""
    lowered = content.lower()
    for term in re.findall(r'[a-z0-9]+', query.lower()):
        index = lowered.find(term) if len(term) > 2 else -1
        if index != -1:
            return content[max(index - 60, 0):index + length].strip()
    return content[:length].strip()

def answer_question(question, n_results=4, categories=None):
    """RAG answer in the structured shape the EGIP dashboard and the AI Chatbot page render.

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
    if risk_level not in ('Low', 'Medium', 'High', 'Critical'):
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

AI_QUERY_DETAILS = re.compile(r'confidence (\d+)% \| ([\d.]+)s$')

def get_dashboard_metrics():
    """Real counts for the EGIP dashboard. Fields without a data source are left out."""
    conn = get_db_connection()
    try:
        counts = {row['category']: row['count'] for row in conn.execute(
            'SELECT category, COUNT(*) AS count FROM documents GROUP BY category')}
        today_queries = conn.execute(
            "SELECT details FROM audit_logs WHERE action = 'ai_query' AND date(created_at) = date('now')"
        ).fetchall()
        trend_rows = {row['day']: row['count'] for row in conn.execute(
            "SELECT date(created_at) AS day, COUNT(*) AS count FROM audit_logs "
            "WHERE action = 'ai_query' AND date(created_at) >= date('now', '-6 days') GROUP BY day")}
        trend_days = [row[0] for row in conn.execute(
            "SELECT date('now', '-' || n || ' days') FROM "
            "(SELECT 6 AS n UNION SELECT 5 UNION SELECT 4 UNION SELECT 3 UNION SELECT 2 UNION SELECT 1 UNION SELECT 0) ORDER BY n DESC")]
    finally:
        conn.close()

    stats = [AI_QUERY_DETAILS.search(row['details'] or '') for row in today_queries]
    stats = [(int(m.group(1)), float(m.group(2))) for m in stats if m]

    compliance = counts.pop('compliance', 0)
    procurement = counts.pop('procurement', 0)
    return {
        'governanceDocs': sum(counts.values()),  # 'document' and any custom categories
        'complianceDocs': compliance,
        'procurementDocs': procurement,
        'aiQueriesToday': len(today_queries),
        'avgConfidence': round(sum(c for c, _ in stats) / len(stats)) if stats else 0,
        'avgResponseTime': round(sum(t for _, t in stats) / len(stats), 1) if stats else 0,
        'trendLabels': [datetime.strptime(day, '%Y-%m-%d').strftime('%a') for day in trend_days],
        'trendData': [trend_rows.get(day, 0) for day in trend_days],
    }
