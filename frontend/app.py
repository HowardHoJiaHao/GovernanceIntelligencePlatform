import json
import os
import re
import urllib.error
import urllib.request

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

from backend.database_logic import (
    add_user,
    authenticate_user,
    bootstrap_database,
    create_category,
    delete_document_by_id,
    delete_user_by_id,
    get_document_by_id,
    get_documents_by_category,
    get_user_by_id,
    list_audit_logs,
    list_categories,
    list_users,
    log_audit,
    update_user,
    save_text_document,
)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key')

OLLAMA_URL = os.getenv('OLLAMA_URL', 'http://localhost:11434/api/generate')
OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'gemma2:2b')
PAGE_SIZE = 10

ROLE_ACCESS = {
    'admin': ['procurement', 'governance', 'important', 'general'],
    'auditor': ['governance', 'important'],
    'user': ['procurement'],
}

ROLE_LABELS = {
    'admin': 'All categories',
    'auditor': 'Governance and important',
    'user': 'Procurement only',
}


bootstrap_database()


def current_username():
    return session.get('username', 'guest')


def current_role():
    return session.get('role')


def is_admin():
    return current_role() == 'admin'


def flash_message(message, category='info'):
    flash(message, category)


def parse_categories(values):
    return [value.strip().lower() for value in values if value.strip()]


def current_allowed_categories():
    raw_categories = session.get('allowed_categories', '')
    if isinstance(raw_categories, list):
        return [value.strip().lower() for value in raw_categories if value.strip()]
    if not raw_categories:
        return []
    return [value.strip().lower() for value in str(raw_categories).split(',') if value.strip()]


def get_allowed_categories(role):
    if role == 'admin':
        categories = list_categories()
        return categories if categories else ROLE_ACCESS['admin']

    session_categories = current_allowed_categories()
    if session_categories:
        return session_categories

    return ROLE_ACCESS.get(role, [])


def get_access_label(role):
    allowed = get_allowed_categories(role)
    if role == 'admin':
        return 'All categories'
    if not allowed:
        return 'No document access'
    return ', '.join(category.replace('_', ' ').title() for category in allowed)


def get_documents(role):
    if role == 'admin':
        return get_documents_by_category()

    documents = []
    for category in get_allowed_categories(role):
        documents.extend(get_documents_by_category(category))
    return documents


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


def paginate_items(items, page):
    total = len(items)
    total_pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = max(1, min(page, total_pages))
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    return items[start:end], page, total_pages, total


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


def get_document_metrics(role):
    documents = get_documents(role)
    category_counts = {}
    for document in documents:
        category_counts[document['category']] = category_counts.get(document['category'], 0) + 1

    ordered_categories = get_allowed_categories(role)
    metrics = [
        {'category': category, 'count': category_counts.get(category, 0)}
        for category in ordered_categories
    ]
    total_documents = sum(metric['count'] for metric in metrics)
    return metrics, total_documents


def rank_documents(role, query_text):
    documents = get_documents(role)
    if not documents:
        return []

    query_terms = {term for term in re.findall(r'[a-z0-9]+', query_text.lower()) if len(term) > 2}
    if not query_terms:
        return documents[:5]

    scored_documents = []
    for document in documents:
        haystack = f"{document['filename']} {document['category']} {document['content']}".lower()
        score = sum(1 for term in query_terms if term in haystack)
        scored_documents.append((score, document))

    scored_documents.sort(key=lambda item: item[0], reverse=True)
    ranked_documents = [document for score, document in scored_documents if score > 0]
    return ranked_documents[:5] if ranked_documents else documents[:5]


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


def generate_summary(role):
    documents = get_documents(role)
    if not documents:
        return 'No documents are available for your role yet.'

    context = build_context(documents)
    prompt = (
        'You are a concise document analyst. Summarize the accessible documents for this role. '
        'Start with a one-line summary, then list 3 to 5 short bullets. '
        'Do not mention documents outside the allowed categories.\n\n'
        f'Role: {role}\n'
        f'Allowed categories: {", ".join(get_allowed_categories(role))}\n\n'
        f'Documents:\n{context}'
    )

    try:
        return call_local_model(prompt)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return 'Gemma is not available right now. Showing the accessible documents instead.'


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
        model_answer = call_local_model(prompt)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        model_answer = 'Gemma is not available right now. Please try again after starting Ollama locally.'

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
        'sources': sources,
        'summary': summary,
    }


def hydrate_session(user):
    session['username'] = user['username']
    session['role'] = user['role']
    session['allowed_categories'] = user.get('allowed_categories') or ''


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = authenticate_user(request.form['username'].strip(), request.form['password'])
        if user:
            hydrate_session(user)
            return redirect(url_for('dashboard'))
        flash_message('Invalid credentials', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash_message('You have been signed out.', 'info')
    return redirect(url_for('login'))


@app.route('/')
def dashboard():
    if 'role' not in session:
        return redirect(url_for('login'))

    role = current_role()
    documents = get_documents(role)
    category_filter = request.args.get('category', '').strip().lower()
    query_filter = request.args.get('q', '').strip()
    filtered_documents = filter_documents(documents, category_filter, query_filter)
    page = request.args.get('page', 1, type=int)
    paginated_documents, page, total_pages, total_filtered = paginate_items(filtered_documents, page)

    access_label = get_access_label(role)
    metrics, total_documents = get_document_metrics(role)
    summary = generate_summary(role)
    recent_logs = list_audit_logs(limit=5)

    return render_template(
        'dashboard.html',
        docs=paginated_documents,
        role=role,
        username=current_username(),
        access_label=access_label,
        allowed_categories=get_allowed_categories(role),
        summary=summary,
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
    if 'role' not in session:
        return redirect(url_for('login'))

    role = current_role()
    allowed_categories = get_allowed_categories(role)

    if request.method == 'POST':
        file = request.files.get('file')
        category = request.form.get('category', '').strip().lower()

        if not file or not file.filename:
            flash_message('Please choose a text file before uploading.', 'error')
            return redirect(url_for('upload'))

        if category not in allowed_categories and not is_admin():
            flash_message('You do not have permission to upload to that category.', 'error')
            return redirect(url_for('upload'))

        filename = secure_filename(file.filename)
        content = file.read().decode('utf-8', errors='ignore')
        save_text_document(
            category=category,
            filename=filename,
            content=content,
            actor=current_username(),
            action='upload',
        )
        flash_message('Success! Your file has been saved to the database and synced to the text files.', 'success')
        return redirect(url_for('upload'))

    return render_template(
        'upload.html',
        allowed_categories=allowed_categories,
        role=role,
        access_label=get_access_label(role),
        categories=list_categories(),
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
        access_label=get_access_label(role),
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

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'create_category':
            category_name = request.form.get('category_name', '').strip()
            created_category = create_category(category_name)
            if created_category:
                log_audit(current_username(), 'create_category', category=created_category, details=f'Created category {created_category}')
                flash_message(f'Category {created_category} created successfully.', 'success')
            else:
                flash_message('Please provide a valid category name.', 'error')
            return redirect(url_for('admin_files'))

        if action == 'delete_document':
            document_id = request.form.get('document_id', '').strip()
            deleted_document = delete_document_by_id(document_id)
            if deleted_document:
                log_audit(
                    current_username(),
                    'delete',
                    category=deleted_document['category'],
                    filename=deleted_document['filename'],
                    details=f'Deleted {deleted_document["filename"]}',
                )
                flash_message(f'Document {deleted_document["filename"]} deleted successfully.', 'success')
            else:
                flash_message('Document could not be deleted because it was not found.', 'error')
            return redirect(url_for('admin_files'))

        if action == 'upload_document':
            category = request.form.get('new_category', '').strip().lower() or request.form.get('category', '').strip().lower()
            file = request.files.get('file')

            if not file or not file.filename:
                flash_message('Choose a file before uploading.', 'error')
                return redirect(url_for('admin_files'))

            if not category:
                flash_message('Choose a category for the upload.', 'error')
                return redirect(url_for('admin_files'))

            if category not in list_categories():
                created_category = create_category(category)
                if created_category:
                    log_audit(current_username(), 'create_category', category=created_category, details=f'Auto-created category {created_category} during upload')
                    category = created_category

            filename = secure_filename(file.filename)
            content = file.read().decode('utf-8', errors='ignore')
            save_text_document(
                category=category,
                filename=filename,
                content=content,
                actor=current_username(),
                action='upload',
            )
            flash_message('Upload complete. Database and TXT folder were updated.', 'success')
            return redirect(url_for('admin_files'))

        if action == 'update_document':
            document_id = request.form.get('document_id', '').strip()
            new_category = request.form.get('category', '').strip().lower()
            new_filename = secure_filename(request.form.get('filename', '').strip())
            new_content = request.form.get('content', '').strip()

            document = get_document_by_id(document_id)
            if not document:
                flash_message('Document not found.', 'error')
                return redirect(url_for('admin_files'))

            if not new_category or not new_filename or not new_content:
                flash_message('Category, filename, and content are required.', 'error')
                return redirect(url_for('admin_files'))

            if new_category not in list_categories():
                create_category(new_category)
                log_audit(current_username(), 'create_category', category=new_category, details=f'Auto-created category {new_category} during update')

            save_text_document(
                category=new_category,
                filename=new_filename,
                content=new_content,
                actor=current_username(),
                action='update',
                previous_category=document['category'],
                previous_filename=document['filename'],
            )
            flash_message('Document updated, database refreshed, and audit log saved.', 'success')
            return redirect(url_for('admin_files'))

    documents = get_documents('admin')
    category_filter = request.args.get('category', '').strip().lower()
    query_filter = request.args.get('q', '').strip()
    filtered_documents = filter_documents(documents, category_filter, query_filter)
    page = request.args.get('page', 1, type=int)
    paginated_documents, page, total_pages, total_filtered = paginate_items(filtered_documents, page)

    edit_document = None
    edit_document_id = request.args.get('edit_id')
    if edit_document_id:
        edit_document = get_document_by_id(edit_document_id)
    upload_categories = list_categories()

    return render_template(
        'admin_files.html',
        documents=paginated_documents,
        username=current_username(),
        edit_document=edit_document,
        page=page,
        total_pages=total_pages,
        total_filtered=total_filtered,
        category_filter=category_filter,
        query_filter=query_filter,
        upload_categories=upload_categories,
    )


@app.route('/admin/users', methods=['GET', 'POST'])
def admin_users():
    if not is_admin():
        return redirect(url_for('dashboard'))

    all_categories = list_categories() or ROLE_ACCESS['admin']

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        role = request.form.get('role', '').strip()
        allowed_categories = parse_categories(request.form.getlist('allowed_categories'))
        action = request.form.get('action', 'create_user')

        if action == 'delete_user':
            user_id = request.form.get('user_id', '').strip()
            user = get_user_by_id(user_id)
            if not user:
                flash_message('User not found.', 'error')
                return redirect(url_for('admin_users'))
            delete_user_by_id(user_id)
            log_audit(current_username(), 'delete_user', details=f'Deleted user {user["username"]}')
            flash_message(f'User {user["username"]} deleted successfully.', 'success')
            return redirect(url_for('admin_users'))

        if action == 'update_user':
            user_id = request.form.get('user_id', '').strip()
            user = get_user_by_id(user_id)
            if not user:
                flash_message('User not found.', 'error')
                return redirect(url_for('admin_users'))

            username = request.form.get('username', '').strip()
            role = request.form.get('role', '').strip()
            allowed_categories = parse_categories(request.form.getlist('allowed_categories'))

            if not username or not role:
                flash_message('Username and role are required.', 'error')
                return redirect(url_for('admin_users', edit_id=user_id))

            if role == 'admin':
                allowed_categories = list_categories() or ROLE_ACCESS['admin']
            elif not allowed_categories:
                flash_message('Select at least one category for the user.', 'error')
                return redirect(url_for('admin_users', edit_id=user_id))

            update_user(user_id, username, role, ','.join(allowed_categories))
            log_audit(current_username(), 'update_user', details=f'Updated user {username} with role {role} and categories {", ".join(allowed_categories)}')
            flash_message(f'User {username} updated successfully.', 'success')
            return redirect(url_for('admin_users'))

        if action == 'create_user':
            if not username or not password or not role:
                flash_message('Username, password, and role are required.', 'error')
                return redirect(url_for('admin_users'))

            if role == 'admin':
                allowed_categories = list_categories() or ROLE_ACCESS['admin']
            elif not allowed_categories:
                flash_message('Select at least one category for the user.', 'error')
                return redirect(url_for('admin_users'))

            try:
                add_user(username, password, role, ','.join(allowed_categories))
                log_audit(
                    actor=current_username(),
                    action='create_user',
                    details=f'Created user {username} with role {role} and categories {", ".join(allowed_categories)}',
                )
                flash_message(f'User {username} created successfully.', 'success')
            except Exception:
                flash_message('That user could not be created. It may already exist.', 'error')

            return redirect(url_for('admin_users'))

        flash_message('Unknown admin user action.', 'error')
        return redirect(url_for('admin_users'))

    users = list_users()
    edit_user = None
    edit_user_id = request.args.get('edit_id')
    if edit_user_id:
        edit_user = get_user_by_id(edit_user_id)
    return render_template(
        'admin_users.html',
        users=users,
        username=current_username(),
        all_categories=all_categories,
        edit_user=edit_user,
    )


@app.route('/admin/audit')
def admin_audit():
    if not is_admin():
        return redirect(url_for('dashboard'))
    logs = list_audit_logs(limit=200)
    return render_template('admin_audit.html', logs=logs, username=current_username())


if __name__ == '__main__':
    app.run(port=5001, debug=True)