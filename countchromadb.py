import chromadb
from pathlib import Path

# Use pathlib to get the absolute path to the chroma_db folder
db_path = Path(__file__).parent / "chroma_db"

print(f"Attempting to connect to database at: {db_path.absolute()}")

try:
    # Initialize client with the absolute path
    client = chromadb.PersistentClient(path=str(db_path))
    
    # List collections to confirm connection
    collections = client.list_collections()
    print(f"Collections found: {[c.name for c in collections]}")
    
    if "documents" in [c.name for c in collections]:
        collection = client.get_collection(name="documents")
        print(f"✅ Success! Total documents in ChromaDB: {collection.count()}")
    else:
        print("❌ Collection 'documents' not found.")
        
except Exception as e:
    print(f"❌ An error occurred: {e}")