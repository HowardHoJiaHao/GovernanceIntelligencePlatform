import os
import sqlite3
from flask import Flask, request, session, jsonify
from dotenv import load_dotenv
import requests # Used to call Ollama API

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')

# Mock Login
@app.route('/login', methods=['POST'])
def login():
    data = request.json
    if data['username'] == os.getenv('ADMIN_USER') and data['password'] == os.getenv('ADMIN_PASS'):
        session['user'] = 'admin'
        return jsonify({"message": "Logged in as Admin"})
    elif data['username'] == os.getenv('USER_USER') and data['password'] == os.getenv('USER_PASS'):
        session['user'] = 'user'
        return jsonify({"message": "Logged in as User"})
    return jsonify({"message": "Unauthorized"}), 401

# AI Chatbot with RBAC
@app.route('/chat', methods=['POST'])
def chat():
    user_role = session.get('user')
    user_query = request.json['query']
    
    # Logic: Filter context based on role
    allowed_categories = ['governance']
    if user_role == 'admin':
        allowed_categories.append('important')
        
    # Fetch content from SQLite based on allowed_categories...
    # (Implementation of vector/text retrieval here)
    
    # Call Ollama
    response = requests.post("http://localhost:11434/api/generate", json={
        "model": "llama3",
        "prompt": f"Context: {context} \n Question: {user_query}"
    })
    return response.json()