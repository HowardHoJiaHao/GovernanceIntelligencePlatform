import sqlite3
import os
from datetime import datetime
import json
import urllib.request
import urllib.error
from werkzeug.utils import secure_filename
import chromadb
from chromadb.utils import embedding_functions


client = chromadb.PersistentClient(path="/app/chroma_db")

# Use Ollama to generate embeddings automatically
ollama_ef = embedding_functions.OllamaEmbeddingFunction(
    # CHANGE THIS: Point to the embeddings endpoint
    url="http://host.docker.internal:11434/api/embeddings", 
    model_name="nomic-embed-text" 
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
    'user': 'Procurement only',
}

OLLAMA_URL = os.getenv('OLLAMA_URL', 'http://host.docker.internal:11434/api/generate')

OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'gemma2:2b')

DB_PATH = os.getenv('DB_PATH', '/app/database.db')
DATA_ROOT = os.getenv('DATA_ROOT', '/app/data')

def get_db_connection():
    # 1. Debugging check
    print(f"DEBUG: Checking file at {DB_PATH}", flush=True)
    
    if os.path.exists(DB_PATH):
        # Perform the "Sanity Check"
        temp_conn = sqlite3.connect(DB_PATH, timeout=30)
        count = temp_conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        print(f"DEBUG: Database file exists. Document count in THIS file is: {count}", flush=True)
        temp_conn.close()
    else:
        print(f"ERROR: Database file NOT FOUND at {DB_PATH}", flush=True)

    # 2. Actual connection logic
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
    return document

def create_category(category_name):
    safe_category = category_name.strip().lower().replace(' ', '_')
    if not safe_category:
        return None
    os.makedirs(os.path.join(DATA_ROOT, safe_category), exist_ok=True)
    return safe_category

import re

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
            cursor.execute('UPDATE documents SET content = ?, category = ?, filename = ? WHERE id = ?', 
                           (content, category, filename, doc_id))
        else:
            today = datetime.now().strftime('%Y-%m-%d')
            cursor.execute('''
                INSERT INTO documents (filename, category, content, upload_date, updated_at) 
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (filename, category, content, today))
            doc_id = cursor.lastrowid
        
        conn.commit()
        
        # try:
        # # 5. RAG Sync: Upsert only the changed document
        #     safe_content = str(content).strip() if content else ""
        
        #     if len(safe_content) > 0:
        #         print(f"DEBUG: Upserting content of length {len(safe_content)} to Vector DB.")
        #         collection.upsert(
        #             ids=[str(doc_id)],
        #             documents=[safe_content],
        #             metadatas=[{"category": category, "filename": filename}]
        #         )
        #     else:
        #         print(f"❌ RAG ERROR: Content for {filename} (ID: {doc_id}) is empty! Vector sync aborted.")
        # except Exception as e:
        #     import traceback
        #     print(f"❌ CRITICAL ERROR: {traceback.format_exc()}")
        #     raise
        try:
            # First, check if the embedding function will actually accept this
            # We perform a dummy check or simply wrap the upsert in a try-except block
            clean_content = sanitize_text(content)

            # LOG the difference
            print(f"DEBUG: Original length: {len(content)}, Cleaned length: {len(clean_content)}")
            collection.upsert(
                ids=[str(doc_id)],
                documents=[clean_content],
                metadatas=[{"category": category, "filename": filename}]
            )
            print(f"✅ Successfully upserted {filename} to Vector DB.")
        except ValueError as ve:
            print(f"⚠️ Vector DB warning: Failed to embed document {filename}. Error: {ve}")
    finally:
        conn.close()

    # 6. Audit
    log_audit(actor=actor, action=action, category=category, filename=filename, details="Updated via API")




def bootstrap_database():
    ensure_schema()
    # seed_default_users()
    # ingest_data()
    # get_allowed_categories(current_username())

def call_local_model(prompt):
    try:
        payload = json.dumps({
            'model': OLLAMA_MODEL,
            'prompt': prompt,
            'stream': False,
        }).encode('utf-8')

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

def get_relevant_context(user_query, n_results=3):
    # Query ChromaDB
    results = collection.query(
        query_texts=[user_query],
        n_results=n_results
    )
    
    # 1. Safety Check: Ensure results exist and contain data
    if not results['documents'] or not results['documents'][0]:
        return "No relevant information found."

    # 2. Combine the results safely
    context_text = ""
    # We use zip() here to make the loop much cleaner and more "Pythonic"
    documents = results['documents'][0]
    metadatas = results['metadatas'][0]
    
    for doc, meta in zip(documents, metadatas):
        # Handle cases where metadata might be None
        filename = meta.get('filename', 'Unknown Source') if meta else 'Unknown Source'
        context_text += f"Source: {filename}\nContent: {doc}\n\n"
        
    return context_text
