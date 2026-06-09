# backend/api.py
from flask import Flask, request, jsonify
from flask_cors import CORS
import os
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
    call_local_model,
    get_relevant_context,
)

app = Flask(__name__)
CORS(app)

BACKEND_PORT = int(os.environ.get('BACKEND_PORT', 5001))

# ============ BOOTSTRAP ON STARTUP ============
# This runs when the module loads, BEFORE the main block
# Useful for initialization
with app.app_context():
    print("🔄 Bootstrapping database on startup...")
    try:
        bootstrap_database()
        print("✅ Database bootstrap complete")
    except Exception as e:
        print(f"⚠️ Bootstrap warning: {e}")

# ============ API ENDPOINTS ============
@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy'}), 200

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password')
    
    user = authenticate_user(username, password)
    
    if user:
        return jsonify({"success": True, "user": user}), 200
    return jsonify({"success": False, "message": "Invalid credentials"}), 401

@app.route('/api/delete-document', methods=['POST'])
def api_delete_document():
    data = request.json
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
    print(f"CANARY: Backend API hit for role: {role}", flush=True)
    try:
        documents = get_documents(role) # This returns a list of sqlite3.Row
        
        # FIX: Convert each Row object into a standard dictionary
        serialized_documents = [dict(doc) for doc in documents]
        
        print(f"DEBUG: Successfully serialized {len(serialized_documents)} documents", flush=True)
        return jsonify({"success": True, "documents": serialized_documents}), 200
        
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
    data = request.get_json() or {}
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

@app.route('/api/generate', methods=['POST'])
def api_generate():
    data = request.json
    prompt = data.get('prompt')
    
    if not prompt:
        return jsonify({"error": "Prompt is required"}), 400
        
    try:
        result = call_local_model(prompt)
        return jsonify({"model_answer": result}), 200
    except Exception as e:
        return jsonify({"error": "Model failed to generate response"}), 500

# @app.route('/api/RAG', methods=['POST'])
# def generate():
#     data = request.json()
#     query = data.get('prompt')
    
#     # 1. Get relevant context (instead of reading all files)
#     context = get_relevant_context(query)
    
#     # 2. Construct a professional RAG prompt
#     prompt = f"""You are a helpful assistant. Use the provided context to answer the question.
#     Cite the source filenames provided in the context.
    
#     Context:
#     {context}
    
#     Question: {query}
#     """
    
#     # 3. Call your model
#     try:
#         result = call_local_model(prompt)
#         return jsonify({"model_answer": result}), 200
#     except Exception as e:
#         return jsonify({"error": "Model failed to generate response"}), 500

@app.route('/api/RAG', methods=['POST'])
def generate():
    # FIX: Use get_json()
    data = request.get_json()
    query = data.get('prompt')
    
    try:
        # Get context and add a safety check
        context = get_relevant_context(query)
        if not context:
            context = "No relevant information found in the database."

        prompt = f"..." 

        result = call_local_model(prompt)
        return jsonify({"model_answer": result}), 200
        
    except Exception as e:
        # LOG THE REAL ERROR
        print(f"DEBUG: RAG Pipeline Error: {str(e)}")
        import traceback
        traceback.print_exc() # This will show you exactly which line failed
        return jsonify({"error": str(e)}), 500

@app.route('/api/audit/logs', methods=['GET'])
def get_audit_logs():
    limit = request.args.get('limit', default=100, type=int)
    logs = list_audit_logs(limit=limit)
    # Convert each row to a dict
    serialized_logs = [dict(row) for row in logs]
    return jsonify(serialized_logs)


@app.route('/api/audit-logs', methods=['GET'])
def get_audit_logs_alias():
    """Compatibility alias: older frontend code calls /api/audit-logs."""
    return get_audit_logs()

@app.route('/api/documents', methods=['POST'])
def api_handle_document():
    try:
        data = request.get_json()
        print(f"DEBUG: Data received in API: {data}", flush=True)
        if not data:
            return jsonify({'success': False, 'error': 'No JSON data provided'}), 400
        
        required = ['category', 'filename', 'content', 'actor', 'action']
        missing = [f for f in required if f not in data]
        
        if missing:
            return jsonify({
                'success': False,
                'error': f'Missing fields: {", ".join(missing)}'
            }), 400
        category = data['category'].strip()
        filename = data['filename'].strip()

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
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/bootstrap/run', methods=['POST'])
def run_bootstrap():
    try:
        bootstrap_database()
        return jsonify({'success': True, 'message': 'Database bootstrapped successfully'}), 200
    except Exception as e:
        return jsonify({'success': False, 'message': f'Bootstrap failed: {str(e)}'}), 500

# ============ MAIN BLOCK - SERVER STARTS HERE ============
if __name__ == '__main__':
    print("=" * 50)
    print("🚀 Starting Backend API Server")
    print("=" * 50)
    print(f"📍 Running on: http://0.0.0.0:{BACKEND_PORT}")
    print(f"📁 Data directory: {os.environ.get('DATA_ROOT', './data')}")
    print("=" * 50)
    print("📋 Available endpoints:")
    print("  POST   /api/login")
    print("  GET    /api/health")
    print("  GET    /api/documents/<role>")
    print("  GET    /api/document/<id>")
    print("  POST   /api/documents")
    print("  POST   /api/delete-document")
    print("  GET    /api/categories/<role>")
    print("  GET    /api/access-label/<role>")
    print("  POST   /api/generate")
    print("  GET    /api/audit/logs")
    print("  POST   /api/bootstrap/run")
    print("=" * 50)
    
    # Start the Flask development server
    app.run(
        host='0.0.0.0',      # Allow external connections (Docker needs this)
        port=BACKEND_PORT,    # Use the configured port
        debug=False,          # Set to True for development only
        threaded=True         # Handle multiple requests
    )