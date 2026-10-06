import os
import secrets
from datetime import datetime, timezone
from functools import wraps

import requests
from dotenv import load_dotenv # Get enviroment variable from .env
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename


load_dotenv()

app = Flask(__name__)

BACKEND_URL = os.getenv('BACKEND_URL', 'http://backend:5001')

# Session cookies are signed with SECRET_KEY. A missing key or the public placeholder
# from .env.example would let anyone forge an admin session, so use a random
# per-process key instead (everyone is logged out when the app restarts).
PLACEHOLDER_SECRET_KEYS = {'', 'change-me-to-a-long-random-string'}
secret_key = os.getenv('SECRET_KEY', '').strip()
if secret_key in PLACEHOLDER_SECRET_KEYS:
    print('WARNING: SECRET_KEY is missing or still the .env.example placeholder; '
          'using a random key. Set SECRET_KEY in .env to keep users logged in across restarts.', flush=True)
    secret_key = secrets.token_hex(32)
app.secret_key = secret_key
PAGE_SIZE = 10

# The database stores governance documents under the category 'document'
CATEGORY_LABELS = {'document': 'Governance'}

# AI Studio suggestions, each shown only to roles that can read the category holding its answer
SUGGESTED_QUESTIONS = [
    ('document', 'What is the approval limit for a Project Manager?'),
    ('document', 'How many days of annual leave do I get after 3 years?'),
    ('document', 'What are the ESG targets for 2026?'),
    ('procurement', 'Which supplier best fulfills Project A requirements?'),
    ('procurement', 'What ISO certification does Supplier Alpha have?'),
    ('compliance', 'What is considered a High Risk incident?'),
    ('compliance', 'When is a supplier classified as High Risk?'),
]

# Shown when the backend can't provide dashboard numbers
EMPTY_METRICS = {
    'documents': [], 'totalDocuments': 0, 'lastUpdated': None,
    'aiQueriesToday': 0, 'avgConfidence': 0, 'avgResponseTime': 0,
    'riskLevels': {'Low': 0, 'Medium': 0, 'High': 0, 'Critical': 0},
    'trendLabels': [], 'trendData': [],
}


# Tries to grab the username from the current browser session.
# If the user isn't logged in, it defaults to 'guest'
def current_username():
    return session.get('username', 'guest')

# Grab the role (admin, reporter, user)
def current_role():
    return session.get('role')

# Check if the current role is admin
def is_admin():
    return current_role() == 'admin'

# Log msg
def flash_message(message, category='info'):
    flash(message, category)

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if 'role' not in session:
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapped

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if 'role' not in session:
            return redirect(url_for('login'))
        if not is_admin():
            flash_message('That page is for administrators only.', 'error')
            return redirect(url_for('dashboard'))
        return view(*args, **kwargs)
    return wrapped

@app.template_filter('category_label')
def category_label(category):
    """Display name for a category: 'document' -> 'Governance', 'policy_updates' -> 'Policy Updates'."""
    if not category:
        return ''
    if category in CATEGORY_LABELS:
        return CATEGORY_LABELS[category]
    # Leave names that already have capitals (e.g. 'AI Studio' in old audit records) as they are
    return category.replace('_', ' ').title() if category.islower() else category

@app.template_filter('datetime')
def format_datetime(value, fmt='%d %b %Y, %H:%M'):
    """SQLite timestamps are UTC 'YYYY-MM-DD HH:MM:SS'; show them in this server's local time."""
    try:
        moment = datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return value or ''
    return moment.astimezone().strftime(fmt)

def api_get(path, params=None, default=None, timeout=10):
    """GET a backend endpoint and return its JSON, or `default` if the backend is down or returns an error."""
    try:
        response = requests.get(f"{BACKEND_URL}{path}", params=params, timeout=timeout)
        if response.ok:
            return response.json()
        print(f"Backend returned {response.status_code} for GET {path}", flush=True)
    except (requests.exceptions.RequestException, ValueError) as e:
        print(f"Backend request failed (GET {path}): {e}", flush=True)
    return default

def api_post(path, payload, timeout=30):
    """POST JSON to the backend. Returns (data, error); error is None on success, otherwise a readable message."""
    try:
        response = requests.post(f"{BACKEND_URL}{path}", json=payload, timeout=timeout)
    except requests.exceptions.Timeout:
        print(f"Backend request timed out (POST {path})", flush=True)
        return {}, 'The backend took too long to respond. Please try again.'
    except requests.exceptions.RequestException as e:
        print(f"Backend request failed (POST {path}): {e}", flush=True)
        return {}, 'The backend service is not responding. Check that it is running, then try again.'
    try:
        data = response.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not response.ok:
        return data, data.get('error') or data.get('message') or f'Backend returned status {response.status_code}'
    return data, None

def get_allowed_categories(role):
    """Fetches allowed categories from the Backend API."""
    data = api_get(f"/api/categories/{role}", default=[])
    return data if isinstance(data, list) else []

def list_audit_logs(limit=5):
    """Fetches audit logs from the Backend API."""
    data = api_get('/api/audit/logs', params={'limit': limit}, default=[])
    return data if isinstance(data, list) else []

def save_text_document(category, filename, content, actor, action,
                       previous_category=None, previous_filename=None):
    """
    Centralized function for document operations (upload, update).
    Callers read the uploaded file themselves and pass filename/content in;
    a request's file stream can only be read once.
    """
    payload = {
        'category': category,
        'filename': filename,
        'content': (content or '').strip(),
        'actor': actor,
        'action': action
    }
    # Only add previous metadata if they exist (used for updates)
    if previous_category: payload['previous_category'] = previous_category
    if previous_filename: payload['previous_filename'] = previous_filename

    data, error = api_post('/api/documents', payload)
    if error:
        return {'success': False, 'error': error}
    return data

def read_text_upload(file):
    """Return (filename, content) of an uploaded .txt file; raises ValueError with a readable message otherwise."""
    if not file or not file.filename:
        raise ValueError('Please choose a .txt file to upload.')
    filename = secure_filename(file.filename)
    if not filename.lower().endswith('.txt'):
        raise ValueError('Only .txt files are supported.')
    content = file.read().decode('utf-8', errors='ignore').strip()
    if not content:
        raise ValueError(f'{filename} is empty.')
    return filename, content

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

def answer_question(role, question):
    """
    Asks the backend AI, limited to the role's categories. The backend also saves the
    answer to the user's AI Studio conversation.
    Returns the structured answer, or {'error': ...} if the backend or model is unavailable.
    """
    data, error = api_post(
        '/api/ai/query',
        {'question': question, 'role': role, 'user': current_username()},
        timeout=600,
    )
    return {'error': error} if error else data

# Redirect
# trigger new session and go to new URL
# render_template
# remain at same URL
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET' and 'role' in session:
        return redirect(url_for('dashboard'))

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
                # Successfully authenticated: start a fresh session for this user
                user_data = response.json().get('user', {})
                session.clear()
                session['username'] = user_data.get('username')
                session['role'] = user_data.get('role')
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
@login_required
def dashboard():
    role = current_role()
    metrics = api_get('/api/dashboard/metrics', params={'role': role})
    if metrics is None:
        flash_message('Dashboard data is unavailable because the backend is not responding.', 'error')
    access = api_get(f"/api/access-label/{role}", default={})

    return render_template(
        'dashboard.html',
        role=role,
        username=current_username(),
        access_label=access.get('label', 'Unknown'),
        metrics=metrics or EMPTY_METRICS,
        # The audit trail is admin-only, like the Audit Log page
        recent_logs=list_audit_logs(limit=6) if is_admin() else [],
    )

@app.route('/dashboard/data')
@login_required
def dashboard_data():
    """Fresh dashboard numbers for the page's auto-refresh."""
    metrics = api_get('/api/dashboard/metrics', params={'role': current_role()})
    if metrics is None:
        return jsonify({'error': 'Backend unavailable'}), 503
    return jsonify(metrics)

@app.route('/folders')
@login_required
def folders():
    role = current_role()
    categories = get_allowed_categories(role)
    active = request.args.get('category', '').strip().lower()
    if active not in categories:
        active = categories[0] if categories else ''

    data = api_get(f"/api/documents/{role}", timeout=20)
    if data is None:
        flash_message('Documents are unavailable because the backend is not responding.', 'error')
    documents = (data or {}).get('documents', [])

    counts = {category: 0 for category in categories}
    for document in documents:
        if document['category'] in counts:
            counts[document['category']] += 1

    query_filter = request.args.get('q', '').strip()
    filtered = filter_documents(documents, active, query_filter) if active else []
    page_documents, page, total_pages, total_filtered = paginate_items(filtered, request.args.get('page', 1, type=int))

    return render_template(
        'folders.html',
        categories=categories,
        counts=counts,
        active=active,
        documents=page_documents,
        page=page,
        total_pages=total_pages,
        total_filtered=total_filtered,
        query_filter=query_filter,
    )

@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    """Uploads from the Folders page; the old standalone upload page now lives there."""
    if request.method == 'GET':
        return redirect(url_for('folders'))

    category = request.form.get('category', '').strip().lower()
    allowed_categories = api_get(f"/api/categories/{current_role()}")
    if allowed_categories is None:
        flash_message('The upload failed because the backend is not responding.', 'error')
        return redirect(url_for('folders'))
    if category not in allowed_categories:
        flash_message('You do not have permission to upload to that category.', 'error')
        return redirect(url_for('folders'))

    try:
        filename, content = read_text_upload(request.files.get('file'))
    except ValueError as e:
        flash_message(str(e), 'error')
    else:
        result = save_text_document(category, filename, content, current_username(), 'upload')
        if result.get('success'):
            flash_message(f'{filename} was uploaded to {category_label(category)}.', 'success')
        else:
            flash_message(f"Upload failed: {result.get('error', 'Server error')}", 'error')
    return redirect(url_for('folders', category=category))

@app.route('/chat', methods=['GET', 'POST'])
@login_required
def chat():
    role = current_role()
    if request.method == 'POST':
        question = request.form.get('query', '').strip()
        if not question:
            flash_message('Please type a question first.', 'error')
        else:
            result = answer_question(role, question)
            if 'error' in result:
                flash_message(result['error'], 'error')
                # Put the question back in the box so it can be retried
                session['unanswered_question'] = question
        # Post/Redirect/Get: refreshing the page shows the answer instead of asking again
        return redirect(url_for('chat'))

    allowed_categories = get_allowed_categories(role)
    data = api_get('/api/ai/history', params={'user': current_username()})
    if data is None:
        flash_message('Your conversation could not be loaded because the backend is not responding.', 'error')
    history = (data or {}).get('history', [])

    return render_template(
        'chat.html',
        role=role,
        allowed_categories=allowed_categories,
        history=history,
        latest=history[-1]['result'] if history else None,
        suggestions=[question for category, question in SUGGESTED_QUESTIONS if category in allowed_categories],
        pending_question=session.pop('unanswered_question', ''),
    )

@app.route('/chat/clear', methods=['POST'])
@login_required
def chat_clear():
    _, error = api_post('/api/ai/history/clear', {'user': current_username()})
    if error:
        flash_message(f'Could not start a new conversation: {error}', 'error')
    return redirect(url_for('chat'))

@app.route('/admin')
@admin_required
def admin_home():
    return redirect(url_for('admin_files'))

@app.route('/admin/files', methods=['GET', 'POST'])
@admin_required
def admin_files():
    # 1. Handle POST Actions (Admin Data Mutations)
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'create_category':
            data, error = api_post('/api/categories', {"name": request.form.get('category_name', '').strip()})
            if error:
                flash_message(f'Could not create the category: {error}', 'error')
            else:
                flash_message(f"Category {category_label(data['category'])} created.", 'success')

        elif action == 'delete_document':
            data, error = api_post(
                '/api/delete-document',
                {"document_id": request.form.get('document_id', '').strip(), "username": current_username()},
            )
            if error:
                flash_message(f'Delete failed: {error}', 'error')
            else:
                flash_message(f"{data.get('filename', 'Document')} was deleted.", 'success')

        elif action == 'upload_document' or action == 'update_document':
            # Upload: read the chosen file once. Update: use the editor's filename/content fields.
            if action == 'upload_document':
                try:
                    filename, content = read_text_upload(request.files.get('file'))
                except ValueError as e:
                    flash_message(str(e), 'error')
                    return redirect(url_for('admin_files'))
            else:
                filename = request.form.get('filename', '')
                content = request.form.get('content', '')

            res = save_text_document(
                category=request.form.get('category', ''),
                filename=filename,
                content=content,
                actor=current_username(),
                action='upload' if action == 'upload_document' else 'update',
                previous_category=request.form.get('old_category'),
                previous_filename=request.form.get('old_filename'),
            )
            if res.get('success'):
                flash_message(f"{filename} was {'uploaded' if action == 'upload_document' else 'saved'}.", 'success')
            else:
                flash_message(f"Error: {res.get('error')}", 'error')

        return redirect(url_for('admin_files'))

    # 2. Handle GET (Rendering)
    data = api_get('/api/documents/admin', timeout=20)
    if data is None:
        flash_message('Documents are unavailable because the backend is not responding.', 'error')
    documents = (data or {}).get('documents', [])

    edit_document = None
    edit_id = request.args.get('edit_id')
    if edit_id:
        edit_document = (api_get(f"/api/document/{edit_id}", default={}) or {}).get('document')

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
        upload_categories=get_allowed_categories('admin'),
        category_filter=cat_filter,
        query_filter=q_filter,
    )

@app.route('/admin/audit')
@admin_required
def admin_audit():
    """Renders the audit log dashboard for administrators."""
    logs = list_audit_logs(limit=200)

    return render_template(
        'admin_audit.html',
        logs=logs,
        username=current_username()
    )

@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    """System health, knowledge base, accounts and recent activity."""
    if request.method == 'POST':
        if request.form.get('action') == 'reindex':
            # Re-embedding every document can take a few minutes on CPU-only machines
            data, error = api_post('/api/reindex', {}, timeout=600)
            if error:
                flash_message(f'Re-indexing failed: {error}', 'error')
            elif data.get('indexed', 0) < data.get('total', 0):
                flash_message(
                    f"Indexed {data.get('indexed', 0)} of {data.get('total', 0)} documents. "
                    'Check that Ollama is running and the embedding model is pulled.', 'error')
            else:
                flash_message(f"All {data.get('total', 0)} documents were re-indexed.", 'success')
        return redirect(url_for('admin_settings'))

    status = api_get('/api/system/status')
    if status is None:
        flash_message('System status is unavailable because the backend is not responding.', 'error')

    return render_template(
        'admin_settings.html',
        status=status,
        metrics=api_get('/api/dashboard/metrics') or EMPTY_METRICS,
        accounts=(api_get('/api/accounts', default={}) or {}).get('accounts', []),
        recent_logs=list_audit_logs(limit=8),
    )

if __name__ == '__main__':
    # Start local Flask development server on port 5000 (the backend uses 5001)
    # debug=True is for Auto-Reload and Interactive Debug
    app.run(host='0.0.0.0', port=int(os.getenv('FRONTEND_PORT', 5000)), debug=True)
