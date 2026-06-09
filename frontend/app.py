import json
import os
import re
import urllib.error # Send requests to websites
import urllib.request # handle error 
import requests
from dotenv import load_dotenv # Get enviroment variable from .env
from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename


load_dotenv()

app = Flask(__name__)

BACKEND_URL = os.getenv('BACKEND_URL', 'http://backend:5001')
app.secret_key = os.getenv('SECRET_KEY', 'a-very-long-and-random-fallback-key')
PAGE_SIZE = 10


# Tries to grab the username from the current browser session. 
# If the user isn't logged in, it defaults to 'guest'
def current_username():
    return session.get('username', 'guest')

# Grab the role (admin, auditor, user)
def current_role():
    return session.get('role')

# Check if the current role is admin
def is_admin():
    return current_role() == 'admin'

# Log msg
def flash_message(message, category='info'):
    flash(message, category)

# Data cleaning
# lowercase and removing empty value
def parse_categories(values):
    return [value.strip().lower() for value in values if value.strip()]

# Updated to ensure robust data extraction
def get_allowed_categories(role):
    """Fetches allowed categories from the Backend API."""
    try:
        response = requests.get(f"{BACKEND_URL}/api/categories/{role}", timeout=5)
        if response.status_code == 200:
            # Assuming backend returns a list directly or a dict with a 'categories' key
            data = response.json()
            return data.get('categories', data) if isinstance(data, dict) else data
        return []
    except requests.exceptions.RequestException as e:
        print(f"Error fetching categories: {e}")
        return []

def list_audit_logs(limit=5):
    """Fetches audit logs from the Backend API."""
    try:
        response = requests.get(
            f"{BACKEND_URL}/api/audit/logs",
            params={"limit": limit},
            timeout=5
        )
        if response.status_code == 200:
            # Ensure it returns a list
            data = response.json()
            return data.get('logs', data) if isinstance(data, dict) else data
        return []
    except requests.exceptions.RequestException as e:
        print(f"Error fetching audit logs: {e}")
        return []

def get_ai_response(prompt):
    """Requests an AI response from the backend."""
    try:
        response = requests.post(
            f"{BACKEND_URL}/api/generate", 
            json={"prompt": prompt}, 
            timeout=600
        )
        if response.status_code == 200:
            # Explicitly match the backend key 'model_answer'
            return response.json().get('model_answer', 'No answer returned.')
        return f"Error: Backend returned status {response.status_code}"
    except requests.exceptions.RequestException as e:
        return f"Error: Could not connect to AI service: {str(e)}"

def get_RAG_ai_response(prompt):
    """Requests an AI response from the backend."""
    try:
        response = requests.post(
            f"{BACKEND_URL}/api/RAG",
            json={"prompt": prompt}, 
            timeout=600
        )
        if response.status_code == 200:
            # Explicitly match the backend key 'model_answer'
            return response.json().get('RAG_model_answer', 'No answer returned from RAG.')
        return f"Error: Backend returned status {response.status_code}"
    except requests.exceptions.RequestException as e:
        return f"Error: Could not connect to AI service: {str(e)}"

def save_text_document(category, filename, content, actor, action, 
                       previous_category=None, previous_filename=None):
    """
    Centralized function for document operations (upload, update, delete).
    """
    try:
        # Build the dynamic payload
        # file = request.files.get('file')
        # filename = secure_filename(file.filename) if file else ""
        # content = file.read().decode('utf-8', errors='ignore') if file else request.form.get('content', '')
        file = request.files.get('file')

        if file and file.filename:
            filename = secure_filename(file.filename)
            # Read the file content once into a variable
            content = file.read().decode('utf-8', errors='ignore')
        else:
            # If no file, use the manual content entry
            filename = request.form.get('filename', 'default.txt')
            content = request.form.get('content', '')

        # CRITICAL: Strip the content here before it goes anywhere else
        content = content.strip()
        payload = {
            'category': category,
            'filename': filename,
            'content': content,
            'actor': actor,
            'action': action
        }
        
        # Only add previous metadata if they exist (used for updates)
        if previous_category: payload['previous_category'] = previous_category
        if previous_filename: payload['previous_filename'] = previous_filename
        
        # Perform the POST request
        response = requests.post(
            f"{BACKEND_URL}/api/documents",
            json=payload,
            timeout=30
        )
        
        # Handle API response based on status code
        if response.status_code == 200:
            return response.json()
        elif response.status_code >= 400:
            # Try to get error message from backend, fallback to text
            error_data = response.json() if 'application/json' in response.headers.get('Content-Type', '') else {}
            return {'success': False, 'error': error_data.get('error', f"HTTP Error {response.status_code}")}
            
    except requests.exceptions.RequestException as e:
        # Catch connection, timeout, and DNS errors
        return {'success': False, 'error': f"Connection failed: {str(e)}"}
    except Exception as e:
        # Catch unexpected errors
        return {'success': False, 'error': f"Unexpected error: {str(e)}"}

# For filter use
def filter_documents(documents, category_filter='', query_filter=''):
    filtered_documents = list(documents)
    if category_filter:
        filtered_documents = [document for document in filtered_documents if document['category'] == category_filter]
    if query_filter:
        query = query_filter.lower()
        filtered_documents = [
            document
            for document in filtered_documents
            if query in document['filename'].lower() or query in document['content'].lower()
        ]
    return filtered_documents

# for pagination
def paginate_items(items, page):
    total = len(items)
    total_pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = max(1, min(page, total_pages))
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    return items[start:end], page, total_pages, total

# write it into sentences filename, category, content
def build_context(documents, max_documents=6):
    snippets = []
    for document in documents[:max_documents]:
        content = str(document['content'])[:1200]
        snippets.append(
            f"Document: {document['filename']}\n"
            f"Category: {document['category']}\n"
            f"Content: {content}"
        )
    return '\n\n'.join(snippets)

# Dashboard numbers
def get_document_metrics(role):
    """
    Fetches documents and calculates metrics. 
    Consider moving the count logic to the backend for better performance!
    """
    try:
        response = requests.get(f"{BACKEND_URL}/api/documents/{role}", timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            documents = data.get('documents', [])
        else:
            flash(f"Could not retrieve documents (Status: {response.status_code}).", "error")
            documents = [] 
            
    except requests.exceptions.RequestException as e:
        flash("Backend service is currently unreachable.", "error")
        documents = []
    
    # Calculate counts using a dictionary comprehension for speed
    # This counts occurrences of each category present in the document list
    category_counts = {}
    for doc in documents:
        cat = doc.get('category', 'Uncategorized')
        category_counts[cat] = category_counts.get(cat, 0) + 1

    # Get allowed categories to ensure the dashboard only shows what the user can see
    allowed_categories = get_allowed_categories(role)
    
    # Map the counts to the allowed categories
    metrics = [
        {'category': cat, 'count': category_counts.get(cat, 0)}
        for cat in allowed_categories
    ]
    
    total_documents = sum(m['count'] for m in metrics)
    return metrics, total_documents

def rank_documents(role, query_text):
    """
    Ranks documents based on keyword matching (TF-like scoring).
    """
    # 1. Fetch documents
    try:
        response = requests.get(f"{BACKEND_URL}/api/documents/{role}", timeout=5)
        documents = response.json().get('documents', []) if response.status_code == 200 else []
    except requests.exceptions.RequestException:
        flash("Backend service is currently unreachable.", "error")
        return []

    if not documents:
        return []

    # 2. Clean query: Lowercase, alphanumeric, length > 2
    query_terms = {term for term in re.findall(r'[a-z0-9]+', query_text.lower()) if len(term) > 2}
    if not query_terms:
        return documents[:5]

    # 3. Calculate scores
    scored_documents = []
    for doc in documents:
        # Use .get() to avoid KeyError if data is missing
        haystack = f"{doc.get('filename', '')} {doc.get('category', '')} {doc.get('content', '')}".lower()
        
        # Scoring: Count unique query terms present
        score = sum(1 for term in query_terms if term in haystack)
        
        if score > 0:
            scored_documents.append((score, doc))

    # 4. Sort and return
    # Sort by score (descending)
    scored_documents.sort(key=lambda item: item[0], reverse=True)
    
    ranked = [doc for score, doc in scored_documents]
    return ranked[:5] if ranked else documents[:5]


# Find parts fo the document that match
def build_excerpt(content, query_text, length=160):
    if not content:
        return 'No excerpt available.'

    lowered = content.lower()
    query_terms = [term for term in re.findall(r'[a-z0-9]+', query_text.lower()) if len(term) > 2]
    for term in query_terms:
        index = lowered.find(term)
        if index != -1:
            start = max(index - 60, 0)
            end = min(index + length, len(content))
            return content[start:end].strip()

    return content[:length].strip()


def answer_question(role, question):
    documents = rank_documents(role, question)
    if not documents:
        return {
            'answer': 'No matching documents were found for your allowed categories.',
            'sources': [],
            'summary': 'No matching documents found.',
        }

    context = build_context(documents)
    prompt = (
        'You are a helpful assistant answering questions using only the provided document context. '
        'Return a short answer, mention which documents were used, and include a concise findings summary. '
        'If the answer is not present in the context, say so clearly.\n\n'
        f'Role: {role}\n'
        f'Allowed categories: {", ".join(get_allowed_categories(role))}\n\n'
        f'Document context:\n{context}\n\n'
        f'Question: {question}'
    )

    try:
        model_answer = get_ai_response(prompt)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        model_answer = 'Gemma is not available right now. Please try again after starting Ollama locally.'
    
    # try:
    #     RAG_model_answer = get_RAG_ai_response(prompt)
    # except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
    #     RAG_model_answer = 'RAG is not available right now. Please try again after starting Ollama locally.'

    sources = [
        {
            'filename': document['filename'],
            'category': document['category'],
            'excerpt': build_excerpt(document['content'], question),
        }
        for document in documents
    ]

    summary = 'Matched {} document(s) in {}.'.format(
        len(sources),
        ', '.join(sorted({source['category'] for source in sources})),
    )

    return {
        'answer': model_answer,
        # 'answer': 'hi',
        # 'RAG_answer': RAG_model_answer,
        'sources': sources,
        'summary': summary,
    }

# Set session cookie
def hydrate_session(user):
    session['username'] = user['username']
    session['role'] = user['role']
    session['allowed_categories'] = user.get('allowed_categories', [])

# Redirect
# trigger new session and go to new URL
# render_template
# remain at same URL 
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        try:
            # Send credentials to the backend
            # Ensure BACKEND_URL is set in your environment
            response = requests.post(
                f"{BACKEND_URL}/api/login", 
                json={"username": username, "password": password},
                timeout=5
            )
            
            if response.status_code == 200:
                # Successfully authenticated
                user_data = response.json().get('user', {})
                session['username'] = user_data.get('username')
                session['role'] = user_data.get('role')
                # Optional: Store allowed categories if returned by backend
                session['allowed_categories'] = user_data.get('allowed_categories', [])
                
                return redirect(url_for('dashboard'))
            
            # Handle non-200 responses
            flash('Invalid username or password.', 'error')
            
        except requests.exceptions.ConnectionError:
            flash('Backend service is unreachable. Please try again later.', 'error')
        except requests.exceptions.Timeout:
            flash('Login request timed out.', 'error')
        except Exception as e:
            flash(f'An unexpected error occurred: {str(e)}', 'error')
        
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash_message('You have been signed out.', 'info')
    return redirect(url_for('login'))

# After entering the main it will come here (consider the root)
@app.route('/')
def dashboard():
    # 1. Session Authentication
    if 'role' not in session:
        return redirect(url_for('login'))

    role = current_role()
    documents = []
    access_label = "Guest"
    # 2. Data Retrieval with Error Handling
    # We use a try-except block to ensure one failing API call doesn't crash the page
    try:
        resp_docs = requests.get(f"{BACKEND_URL}/api/documents/{role}", timeout=20)
    
        # --- INSPECTION BLOCK ---
        if resp_docs.status_code == 200:
            data = resp_docs.json()
            documents = data.get('documents', [])
            print(f"DEBUG: Frontend received {len(documents)} items.")
            print(f"DEBUG: Sample item: {documents[0] if documents else 'Empty List'}")
        else:
            print(f"DEBUG: API Error: {resp_docs.status_code}")
            documents = []
    # ------------------------
    except requests.exceptions.RequestException:
        flash("Some dashboard components are currently unavailable.", "warning")
        documents, access_label = [], "Guest"

    # 3. Filtering and Pagination
    # Extraction of URL parameters
    category_filter = request.args.get('category', '').strip().lower()
    query_filter = request.args.get('q', '').strip()
    
    # Apply filters to the document list
    filtered_docs = filter_documents(documents, category_filter, query_filter)
    
    # Calculate pagination logic
    page = request.args.get('page', 1, type=int)
    paginated_docs, page, total_pages, total_filtered = paginate_items(filtered_docs, page)

    # 4. Auxiliary Data Gathering
    # Metrics and logs are fetched separately to modularize backend load
    metrics, total_documents = get_document_metrics(role)
    recent_logs = list_audit_logs(limit=5)

    # 5. Template Rendering
    return render_template(
        'dashboard.html',
        docs=paginated_docs,
        role=role,
        username=current_username(),
        access_label=access_label,
        allowed_categories=get_allowed_categories(role),
        metrics=metrics,
        total_documents=total_documents,
        recent_logs=recent_logs,
        page=page,
        total_pages=total_pages,
        total_filtered=total_filtered,
        category_filter=category_filter,
        query_filter=query_filter,
    )

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    # 1. Authorization Check
    if 'role' not in session:
        return redirect(url_for('login'))

    role = current_role()
    allowed_categories = get_allowed_categories(role)

    # 2. Handle POST Request
    if request.method == 'POST':
        file = request.files.get('file')
        category = request.form.get('category', '').strip().lower()

        # Validation: Check for file presence
        if not file or not file.filename:
            flash_message('Please choose a text file before uploading.', 'error')
            return redirect(url_for('upload'))

        # Validation: Role-based access control
        if not is_admin() and category not in allowed_categories:
            flash_message('You do not have permission to upload to that category.', 'error')
            return redirect(url_for('upload'))

        # Processing: Read and sanitize
        try:
            filename = secure_filename(file.filename)
            content = file.read().decode('utf-8', errors='ignore')
            
            # API Communication
            result = save_text_document(
                category=category,
                filename=filename,
                content=content,
                actor=current_username(),
                action='upload'
            )
            
            # DEBUG: Print the raw result
            print(f"DEBUG: API result received: {result}", flush=True)

            # Response Handling
            if result.get('success'):
                flash_message('Success! Your file has been saved to the database.', 'success')
            else:
                flash_message(f"Upload failed: {result.get('error', 'Server error')}", 'error')
                
        except Exception as e:
            flash_message(f"Critical error during file processing: {str(e)}", 'error')
            
        return redirect(url_for('upload'))

    # 3. Handle GET Request: Load interface data
    access_label = "Guest"
    try:
        response = requests.get(f"{BACKEND_URL}/api/access-label/{role}", timeout=2)
        if response.status_code == 200:
            access_label = response.json().get('label', 'Guest')
    except requests.exceptions.RequestException:
        pass # Graceful degradation if label service is down

    return render_template(
        'upload.html',
        allowed_categories=allowed_categories,
        role=role,
        access_label=access_label
    )

@app.route('/chat', methods=['GET', 'POST'])
def chat():
    if 'role' not in session:
        return redirect(url_for('login'))

    role = current_role()
    answer = None
    if request.method == 'POST':
        answer = answer_question(role, request.form.get('query', '').strip())

    return render_template(
        'chat.html',
        role=role,
        allowed_categories=get_allowed_categories(role),
        answer=answer,
    )

@app.route('/admin')
def admin_home():
    if not is_admin():
        return redirect(url_for('dashboard'))
    return redirect(url_for('admin_files'))

@app.route('/admin/files', methods=['GET', 'POST'])
def admin_files():
    if not is_admin():
        return redirect(url_for('dashboard'))

    # 1. Handle POST Actions (Admin Data Mutations)
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'create_category':
            category_name = request.form.get('category_name', '').strip()
            # API CALL: POST /api/categories
            res = requests.post(f"{BACKEND_URL}/api/categories", json={"name": category_name})
            if res.status_code == 200:
                flash_message(f'Category {category_name} created.', 'success')
            else:
                flash_message('Could not create category.', 'error')

        elif action == 'delete_document':
            document_id = request.form.get('document_id', '').strip()
            # API CALL: POST /api/delete-document
            res = requests.post(f"{BACKEND_URL}/api/delete-document", json={"document_id": document_id})
            if res.status_code == 200:
                flash_message('Document deleted.', 'success')
            else:
                flash_message('Delete failed.', 'error')

        elif action == 'upload_document' or action == 'update_document':
            file = request.files.get('file')
                
            # Logic: If a new file is uploaded, use its name. 
            # Otherwise, fall back to the text input field named 'filename'.
            filename = secure_filename(file.filename) if file and file.filename else request.form.get('filename')
            
            payload = {
                'category': request.form.get('category'),
                'filename': filename, # <--- Now this will be 'Document.txt' instead of ''
                'content': request.form.get('content'),
                'actor': current_username(),
                'action': 'update',
                'previous_category': request.form.get('old_category'), # Ensure these are in your form
                'previous_filename': request.form.get('old_filename')
            }
            res = save_text_document(**payload)

            if res.get('success'):
                flash_message('Document operation successful.', 'success')
            else:
                flash_message(f"Error: {res.get('error')}", 'error')

        return redirect(url_for('admin_files'))

    # 2. Handle GET (Rendering)
    try:
        # Fetch data from API
        res_docs = requests.get(f"{BACKEND_URL}/api/documents/admin", timeout=5)
        documents = res_docs.json().get('documents', []) if res_docs.status_code == 200 else []
        
        edit_document = None
        edit_id = request.args.get('edit_id')
        if edit_id:
            res_edit = requests.get(f"{BACKEND_URL}/api/document/{edit_id}")
            if res_edit.status_code == 200:
                edit_document = res_edit.json().get('document')
    except requests.exceptions.RequestException:
        documents, edit_document = [], None
        flash_message("Backend unreachable.", "error")

    # Pagination/Filtering
    cat_filter = request.args.get('category', '').strip().lower()
    q_filter = request.args.get('q', '').strip()
    filtered = filter_documents(documents, cat_filter, q_filter)
    paginated, page, pages, total = paginate_items(filtered, request.args.get('page', 1, type=int))

    return render_template(
        'admin_files.html',
        documents=paginated,
        edit_document=edit_document,
        page=page,
        total_pages=pages,
        total_filtered=total,
        upload_categories=get_allowed_categories('admin')
    )
    
@app.route('/admin/audit')
def admin_audit():
    """Renders the audit log dashboard for administrators."""
    if not is_admin():
        return redirect(url_for('dashboard'))
    # Fetch logs from the backend API
    # Ensure list_audit_logs is imported or defined in this file
    logs = list_audit_logs(limit=200)

    return render_template(
        'admin_audit.html', 
        logs=logs, 
        username=current_username()
    )

if __name__ == '__main__':
    # Strat local Flast development server
    # specify port 5001, Default 5000
    # debug=True is for Auto-Reload and Interactive Debug
    # app.run(port=5001, debug=True)
    app.run(host='0.0.0.0', port=5000, debug=True)
    bootstrap_database()