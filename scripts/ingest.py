import os
import sqlite3
from datetime import datetime
import chromadb
from chromadb.utils import embedding_functions

def ingest_data_unified(data_root='data', db_name='database.db'):
    # 1. Setup SQLite
    conn = sqlite3.connect(db_name)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS documents (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        filename TEXT,
                        category TEXT,
                        content TEXT, 
                        upload_date TEXT, 
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # 2. Setup ChromaDB
    client = chromadb.PersistentClient(path="./chroma_db")
    ollama_ef = embedding_functions.OllamaEmbeddingFunction(
        url="http://localhost:11434", # Use localhost if running this script on host
        model_name="nomic-embed-text"
    )
    collection = client.get_or_create_collection(name="documents", embedding_function=ollama_ef)

    # 3. Process files
    today = datetime.now().strftime('%Y-%m-%d')
    for category in os.listdir(data_root):
        cat_path = os.path.join(data_root, category)
        if os.path.isdir(cat_path):
            for filename in os.listdir(cat_path):
                if filename.endswith(".txt"):
                    with open(os.path.join(cat_path, filename), 'r', encoding='utf-8') as f:
                        content = f.read()
                        
                        # A. Save to SQLite
                        cursor.execute('''
                            INSERT INTO documents (filename, category, content, upload_date, updated_at) 
                            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ''', (filename, category, content, today))
                        doc_id = str(cursor.lastrowid) # Capture the auto-incremented ID
                        
                        # B. Save to ChromaDB (using the same ID!)
                        collection.upsert(
                            ids=[doc_id],
                            documents=[content],
                            metadatas=[{"filename": filename, "category": category}]
                        )
                        print(f"Pumped {filename} into SQL (ID: {doc_id}) and ChromaDB.")

    conn.commit()
    conn.close()
    print("Ingestion complete.")

if __name__ == "__main__":
    ingest_data_unified()