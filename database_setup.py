import chromadb
from chromadb.utils import embedding_functions
import os

# TODO: load and chunk the raw PubMed abstracts (Week 3 task)

def initialize_vector_store():
    print("Initializing local ChromaDB instance...")
    
    # setting up persistent storage in the project folder
    client = chromadb.PersistentClient(path="./chroma_data")
    
    # loading the lightweight HuggingFace model
    sentence_transformer_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )
    
    # creating the baseline collection
    collection = client.get_or_create_collection(
        name="pubmed_baseline",
        embedding_function=sentence_transformer_ef
    )
    
    print(f"Success! Collection '{collection.name}' is ready for document input.")
    return collection

if __name__ == "__main__":
    initialize_vector_store()