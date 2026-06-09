import chromadb; 
client = chromadb.PersistentClient(path='./chroma_db'); print(f'Total count: {client.get_collection(name="documents").count()}')
