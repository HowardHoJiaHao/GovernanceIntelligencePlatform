import os
import sqlite3
from datetime import datetime

def ingest_data_to_sqlite(data_root='data', db_name='database.db'):
    """
    Scans subdirectories in 'data/', uses folder names as categories, 
    and inserts content into a SQLite database.
    """
    # 1. Connect and Setup Database
    # We use a path to ensure it is created in the root directory
    db_path = os.path.join(os.getcwd(), db_name)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Reset table
    cursor.execute('DROP TABLE IF EXISTS documents')
    cursor.execute('''
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            category TEXT,
            content TEXT,
            upload_date TEXT
        )
    ''')
    
    today = datetime.now().strftime('%Y-%m-%d')
    count = 0
    
    # 2. Loop through categories and files
    for category in os.listdir(data_root):
        category_path = os.path.join(data_root, category)
        
        if os.path.isdir(category_path):
            for filename in os.listdir(category_path):
                if filename.endswith(".txt"):
                    file_path = os.path.join(category_path, filename)
                    
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        
                    cursor.execute('''
                        INSERT INTO documents (filename, category, content, upload_date)
                        VALUES (?, ?, ?, ?)
                    ''', (filename, category, content, today))
                    count += 1
                    print(f"Pumped: {filename} under category: {category}")

    # 3. Finalize
    conn.commit()
    conn.close()
    print(f"\nSuccess: {count} documents pumped into {db_name}.")

if __name__ == "__main__":
    ingest_data_to_sqlite()