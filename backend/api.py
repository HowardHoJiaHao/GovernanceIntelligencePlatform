# backend/api.py
from flask import Flask, request, jsonify
import os
import threading
from database_logic import (
    authenticate_user,
    bootstrap_database,
    delete_document_by_id,
    get_document_by_id,
    get_documents,
    list_audit_logs,
    get_access_label,
    log_audit,
    save_text_document,
    create_category,
    get_allowed_categories,
    answer_question,
    record_ai_query,
    get_ai_history,
    archive_ai_history,
    get_dashboard_metrics,
    get_system_status,
    list_accounts,
    sync_vector_index,
    OLLAMA_HOST,
)

# Only the web app calls this API (server to server), so there is no CORS:
# other websites open in the user's browser can't call it either.
app = Flask(__name__)

BACKEND_PORT = int(os.environ.get('BACKEND_PORT', 5001))

# ============ BOOTSTRAP ON STARTUP ============
# This runs when the module loads, BEFORE the main block:
# creates missing tables and imports new files from data/
with app.app_context():
    print("🔄 Bootstrapping database on startup...")
    try:
        bootstrap_database()
        print("✅ Database bootstrap complete")
    except Exception as e:
        print(f"⚠️ Bootstrap warning: {e}")

def _sync_vectors_in_background():
    """Embed any documents missing from ChromaDB without blocking startup."""
    try:
        result = sync_vector_index()
        print(f"✅ Vector index sync: {result}", flush=True)
    except Exception as e:
        print(f"⚠️ Vector index sync skipped (is Ollama running at {OLLAMA_HOST}?): {e}", flush=True)

threading.Thread(target=_sync_vectors_in_background, daemon=True).start()

# ============ API ENDPOINTS ============
@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy'}), 200

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password')

    user = authenticate_user(username, password)

    if user:
        return jsonify({"success": True, "user": user}), 200
    return jsonify({"success": False, "message": "Invalid credentials"}), 401

@app.route('/api/delete-document', methods=['POST'])
def api_delete_document():
    data = request.get_json(silent=True) or {}
    document_id = data.get('document_id')
    username = data.get('username') or 'system'

    deleted_document = delete_document_by_id(document_id)

    if deleted_document:
        log_audit(
            username, 'delete',
            category=deleted_document['category'],
            filename=deleted_document['filename'],
            details=f'Deleted {deleted_document["filename"]}',
        )
        return jsonify({"success": True, "filename": deleted_document['filename']}), 200

    return jsonify({"success": False, "message": "Document not found"}), 404

@app.route('/api/document/<document_id>', methods=['GET'])
def api_get_document(document_id):
    document = get_document_by_id(document_id)
    if not document:
        return jsonify({"success": False, "message": "Document not found"}), 404
    return jsonify({"success": True, "document": dict(document)}), 200

@app.route('/api/documents/<role>', methods=['GET'])
def api_get_documents(role):
    try:
        # sqlite3.Row objects aren't JSON serializable, so convert each one to a dict
        documents = [dict(doc) for doc in get_documents(role)]
        return jsonify({"success": True, "documents": documents}), 200
    except Exception as e:
        print(f"ERROR: Backend failed: {str(e)}", flush=True)
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/access-label/<role>', methods=['GET'])
def api_get_access_label(role):
    try:
        label = get_access_label(role)
        return jsonify({"label": label}), 200
    except Exception:
        return jsonify({"label": "Unknown"}), 500

@app.route('/api/categories/<role>', methods=['GET'])
def api_get_categories(role):
    categories = get_allowed_categories(role)
    return jsonify(categories), 200


@app.route('/api/categories', methods=['POST'])
def api_create_category():
    data = request.get_json(silent=True) or {}
    name = data.get('name') or data.get('category')
    if not name:
        return jsonify({'success': False, 'error': 'Category name is required'}), 400

    try:
        safe_name = create_category(name)
        if not safe_name:
            return jsonify({'success': False, 'error': 'Invalid category name'}), 400
        return jsonify({'success': True, 'category': safe_name}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/audit/logs', methods=['GET'])
def get_audit_logs():
    limit = request.args.get('limit', default=100, type=int)
    logs = list_audit_logs(limit=limit)
    # Convert each row to a dict
    serialized_logs = [dict(row) for row in logs]
    return jsonify(serialized_logs)

@app.route('/api/documents', methods=['POST'])
def api_handle_document():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'success': False, 'error': 'No JSON data provided'}), 400

        required = ['category', 'filename', 'content', 'actor', 'action']
        missing = [f for f in required if f not in data]

        if missing:
            return jsonify({
                'success': False,
                'error': f'Missing fields: {", ".join(missing)}'
            }), 400

        file_path = save_text_document(
            category=data['category'],
            filename=data['filename'],
            content=data['content'],
            actor=data['actor'],
            action=data['action'],
            previous_category=data.get('previous_category'),
            previous_filename=data.get('previous_filename')
        )

        return jsonify({
            'success': True,
            'message': f"Document {data['action']} successful",
            'file_path': file_path
        }), 200

    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/bootstrap/run', methods=['POST'])
def run_bootstrap():
    try:
        bootstrap_database()
        return jsonify({'success': True, 'message': 'Database bootstrapped successfully'}), 200
    except Exception as e:
        return jsonify({'success': False, 'message': f'Bootstrap failed: {str(e)}'}), 500

@app.route('/api/reindex', methods=['POST'])
def api_reindex():
    """Re-embed every document into ChromaDB (needs Ollama + the embedding model)."""
    try:
        result = sync_vector_index(force=True)
        return jsonify({'success': True, **result}), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ============ DASHBOARD, AI STUDIO AND SETTINGS ============
@app.route('/api/dashboard/metrics', methods=['GET'])
def api_dashboard_metrics():
    # With ?role=, document counts only cover that role's categories
    role = request.args.get('role')
    try:
        return jsonify(get_dashboard_metrics(get_allowed_categories(role) if role else None)), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ai/query', methods=['POST'])
def api_ai_query():
    data = request.get_json(silent=True) or {}
    question = (data.get('question') or '').strip()
    if not question:
        return jsonify({'error': 'Question is required'}), 400

    # With a role, only that role's categories are searched; without one, every category is
    role = data.get('role')
    categories = get_allowed_categories(role) if role else None
    user = data.get('user') or 'api'

    try:
        result = answer_question(question, categories=categories)
    except Exception as e:
        print(f"DEBUG: AI query failed: {e}", flush=True)
        return jsonify({'error': f'AI model unavailable: {e}'}), 503

    result['id'] = record_ai_query(user, question, result)
    log_audit(
        user, 'ai_query',
        details=f"{question[:120]} | confidence {result['confidence']}% | {result['response_time']}s",
    )
    return jsonify(result), 200

@app.route('/api/ai/history', methods=['GET'])
def api_ai_history():
    user = (request.args.get('user') or '').strip()
    if not user:
        return jsonify({'error': 'user is required'}), 400
    limit = max(1, min(request.args.get('limit', default=20, type=int), 100))
    return jsonify({'history': get_ai_history(user, limit)}), 200

@app.route('/api/ai/history/clear', methods=['POST'])
def api_clear_ai_history():
    user = ((request.get_json(silent=True) or {}).get('user') or '').strip()
    if not user:
        return jsonify({'error': 'user is required'}), 400
    return jsonify({'success': True, 'archived': archive_ai_history(user)}), 200

@app.route('/api/system/status', methods=['GET'])
def api_system_status():
    return jsonify(get_system_status()), 200

@app.route('/api/accounts', methods=['GET'])
def api_accounts():
    return jsonify({'accounts': list_accounts()}), 200

# ============ MAIN BLOCK - SERVER STARTS HERE ============
if __name__ == '__main__':
    print("=" * 50)
    print("🚀 Starting Backend API Server")
    print("=" * 50)
    print(f"📍 Running on: http://0.0.0.0:{BACKEND_PORT}")
    print(f"📁 Data directory: {os.environ.get('DATA_ROOT', './data')}")
    print("=" * 50)
    print("📋 Available endpoints:")
    for rule in sorted(app.url_map.iter_rules(), key=lambda rule: rule.rule):
        if rule.endpoint != 'static':
            methods = ', '.join(sorted(rule.methods - {'HEAD', 'OPTIONS'}))
            print(f"  {methods:<7} {rule.rule}")
    print("=" * 50)

    # Start the Flask development server
    app.run(
        host='0.0.0.0',      # Allow external connections (Docker needs this)
        port=BACKEND_PORT,    # Use the configured port
        debug=False,          # Set to True for development only
        threaded=True         # Handle multiple requests
    )
