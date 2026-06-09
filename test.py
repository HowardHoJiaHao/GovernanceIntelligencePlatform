import chromadb
import sys
from chromadb.utils import embedding_functions

# 1. Setup connection
client = chromadb.PersistentClient(path="./chroma_db")
ollama_ef = embedding_functions.OllamaEmbeddingFunction(
    url="http://localhost:11434/api/embeddings", 
    model_name="nomic-embed-text" 
)

# 2. Get list of collections correctly
all_collections = client.list_collections()
collection_names = [c.name for c in all_collections]

if "documents" in collection_names:
    print("Success: Collection 'documents' found!")
    collection = client.get_collection(name="documents", embedding_function=ollama_ef)
    
    # 3. Perform the search
    query_text = sys.argv[1] if len(sys.argv) > 1 else "project alpha"
    print(f"\nSearching for: '{query_text}'")
    
    results = collection.query(query_texts=[query_text], n_results=3)
    
    docs = results.get('documents', [[]])[0]
    metas = results.get('metadatas', [[]])[0]
    
    if not docs:
        print("No matches found for that query.")
    else:
        for i, (doc, meta) in enumerate(zip(docs, metas)):
            filename = meta.get('filename', 'Unknown') if meta else 'Unknown'
            print(f"\n--- Match {i+1} (File: {filename}) ---")
            print(f"Snippet: {doc}")
else:
    print(f"Collection 'documents' not found. Available: {collection_names}")