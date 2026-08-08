import sys
import time
import json
import zlib
import sqlite3
import argparse
from pathlib import Path
from Bio import Entrez
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel
from typing import List
import chromadb
from tqdm import tqdm

# setup api keys and limits
Entrez.email   = "your.email@leicester.ac.uk"
Entrez.api_key = "GEMINI_API_KEY"
NEO4J_DAILY_LIMIT = 200
FETCH_BATCH_SIZE  = 500
CHUNK_SIZE        = 300
CHUNK_OVERLAP     = 50
DB_PATH           = "pubmed_articles.db"
PROGRESS_FILE     = "ingestion_progress.json"

# sticking to alzheimer's for the thesis so the graph is dense
# the numbers are the max papers to grab per search
QUERIES = [
    ("lecanemab alzheimer phase 3", 1500),
    ("donanemab clinical trial amyloid", 1000),
    ("amyloid beta clearance mechanism", 2000),
    ("tau protein tangles alzheimers progression", 2000),
    ("apoe4 gene risk factor dementia", 1500),
    ("pet scan amyloid plaque detection", 1000),
    ("aria-e edema side effect lecanemab", 800), # saw this side effect mentioned a lot
    ("cdr-sb cognitive decline score", 1000),
    ("bace1 inhibitor trial fail", 500) # good to have negative examples too
]

# model connections
embed_model   = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
chroma_client = chromadb.PersistentClient(path="./chroma_local_storage")
chroma_col    = chroma_client.get_or_create_collection(name="pubmed_test")

extractor_llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key="GEMINI_API_KEY"
)

text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP, length_function=len
)

# setup sqlite to compress text and save space
def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")   
    conn.execute("PRAGMA synchronous=NORMAL") 
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            pmid     TEXT PRIMARY KEY,
            title    TEXT,
            authors  TEXT,
            journal  TEXT,
            year     TEXT,
            citation TEXT,
            abstract BLOB
        );
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            pmid     TEXT,
            chunk_idx INTEGER,
            text     BLOB,
            FOREIGN KEY (pmid) REFERENCES articles(pmid)
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_pmid ON chunks(pmid);
    """)
    conn.commit()
    return conn

def compress(text: str) -> bytes:
    return zlib.compress(text.encode("utf-8"), level=6)

def decompress(data: bytes) -> str:
    return zlib.decompress(data).decode("utf-8")

def article_in_db(conn: sqlite3.Connection, pmid: str) -> bool:
    return conn.execute("SELECT 1 FROM articles WHERE pmid=?", (pmid,)).fetchone() is not None

def store_article(conn: sqlite3.Connection, article: dict, chunks: list):
    citation = f"{article['authors']} ({article['year']}). {article['title']}. {article['journal']}. PMID: {article['pmid']}"
    conn.execute(
        "INSERT OR IGNORE INTO articles VALUES (?,?,?,?,?,?,?)",
        (article["pmid"], article["title"], article["authors"], article["journal"], article["year"], citation, compress(article["abstract"]))
    )
    for i, chunk_text in enumerate(chunks):
        chunk_id = f"pmid_{article['pmid']}_chunk_{i}"
        conn.execute("INSERT OR IGNORE INTO chunks VALUES (?,?,?,?)", (chunk_id, article["pmid"], i, compress(chunk_text)))
    conn.commit()

def get_article_text(conn: sqlite3.Connection, pmid: str) -> str:
    row = conn.execute("SELECT abstract FROM articles WHERE pmid=?", (pmid,)).fetchone()
    return decompress(row[0]) if row else ""

# track progress so we can stop and resume without losing data
def load_progress() -> dict:
    if Path(PROGRESS_FILE).exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"chroma_done": [], "neo4j_done": []}

def save_progress(progress: dict):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)

def search_pubmed(query: str, retmax: int) -> list:
    handle = Entrez.esearch(db="pubmed", term=query, retmax=retmax, usehistory="y")
    record = Entrez.read(handle)
    handle.close()
    return record["IdList"]

def fetch_articles_batch(pmids: list) -> list:
    articles = []
    print(f"  -> Starting download of {len(pmids):,} papers from PubMed...") 
    
    for start in range(0, len(pmids), FETCH_BATCH_SIZE):
        batch = pmids[start: start + FETCH_BATCH_SIZE]
        print(f"    -> Downloading batch {start} to {start+len(batch)}...") 
        try:
            handle = Entrez.efetch(db="pubmed", id=",".join(batch), rettype="abstract", retmode="xml")
            records = Entrez.read(handle)
            handle.close()
        except Exception as e:
            print(f"  Fetch error (batch {start}): {e}")
            time.sleep(2)
            continue

        for article in records.get("PubmedArticle", []):
            try:
                medline = article["MedlineCitation"]
                art = medline["Article"]
                pmid = str(medline["PMID"])
                title = str(art.get("ArticleTitle", "Unknown Title"))
                abstract_obj = art.get("Abstract", {}).get("AbstractText", [])
                abstract = " ".join(str(t) for t in abstract_obj) if isinstance(abstract_obj, list) else str(abstract_obj)
                
                if not abstract.strip():
                    continue

                author_list = art.get("AuthorList", [])
                names = [f"{a.get('LastName','')} {a.get('Initials','')}".strip() for a in author_list[:3] if a.get("LastName")]
                authors = ", ".join(names) + (" et al." if len(author_list) > 3 else "")
                journal = str(art.get("Journal", {}).get("Title", "Unknown Journal"))
                year = str(medline.get("DateCompleted", {}).get("Year", "") or art.get("Journal", {}).get("JournalIssue", {}).get("PubDate", {}).get("Year", "n.d."))
                
                articles.append({
                    "pmid": pmid, "title": title, "abstract": abstract,
                    "authors": authors, "journal": journal, "year": year,
                })
            except (KeyError, IndexError):
                continue
        time.sleep(0.4 if not Entrez.api_key else 0.12)
    return articles

def ingest_article(conn: sqlite3.Connection, article: dict) -> bool:
    if article_in_db(conn, article["pmid"]):
        return False
    chunks = text_splitter.create_documents([article["abstract"]])
    chunk_texts = [c.page_content for c in chunks]
    ids = [f"pmid_{article['pmid']}_chunk_{i}" for i in range(len(chunks))]
    existing = set(chroma_col.get(ids=ids)["ids"])
    new_ids = [i for i in ids if i not in existing]

    store_article(conn, article, chunk_texts)

    if new_ids:
        embeddings = embed_model.embed_documents(chunk_texts)
        idx_map = {id_: i for i, id_ in enumerate(ids)}
        new_idx = [idx_map[i] for i in new_ids]
        chroma_col.add(
            documents=[chunk_texts[i] for i in new_idx],
            embeddings=[embeddings[i] for i in new_idx],
            metadatas=[{"pmid": article["pmid"]} for _ in new_idx],
            ids=new_ids,
        )
    return True

# neo4j setup
try:
    from neo4j import GraphDatabase
    neo4j_driver = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "password123"))
    NEO4J_AVAILABLE = True
except Exception:
    NEO4J_AVAILABLE = False

class Triplet(BaseModel):
    subject: str
    relation: str
    object: str

class TripletList(BaseModel):
    triplets: List[Triplet]

# using local REBEL model so we don't hit API limits
def load_rebel():
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"  Loading REBEL model ({device.upper()})... (This takes ~1.6GB of space)")
    tokenizer = AutoTokenizer.from_pretrained("Babelscape/rebel-large")
    model = AutoModelForSeq2SeqLM.from_pretrained("Babelscape/rebel-large").to(device)
    return {"model": model, "tokenizer": tokenizer, "device": device}

def parse_rebel_output(text: str) -> List[Triplet]:
    triplets = []
    subject, relation, object_ = '', '', ''
    current = 'x'
    tokens = text.replace("<s>", "").replace("<pad>", "").replace("</s>", "").split()
    for token in tokens:
        if token == "<triplet>":
            if subject and relation and object_:
                triplets.append(Triplet(subject=subject.strip(), relation=relation.strip().replace(" ", "_").upper(), object=object_.strip()))
            subject, relation, object_ = '', '', ''
            current = 't'
        elif token == "<subj>":
            if subject and relation and object_:
                triplets.append(Triplet(subject=subject.strip(), relation=relation.strip().replace(" ", "_").upper(), object=object_.strip()))
            object_, relation = '', ''
            current = 's'
        elif token == "<obj>":
            relation = ''
            current = 'o'
        else:
            if current == 't': subject += ' ' + token
            elif current == 's': object_ += ' ' + token
            elif current == 'o': relation += ' ' + token
    if subject and relation and object_:
        triplets.append(Triplet(subject=subject.strip(), relation=relation.strip().replace(" ", "_").upper(), object=object_.strip()))
    return triplets

def extract_triplets_rebel_batch(pipe, abstracts: List[str]) -> List[List[Triplet]]:
    try:
        model, tokenizer, device = pipe["model"], pipe["tokenizer"], pipe["device"]
        inputs = tokenizer(abstracts, padding=True, return_tensors="pt", max_length=512, truncation=True)
        inputs = {k: v.to(device) for k, v in inputs.items()}
        
        # num_beams=1 is super important here so the GPU doesn't choke
        generated_tokens = model.generate(**inputs, max_length=256, length_penalty=0, num_beams=1, num_return_sequences=1)
        decoded_batch = tokenizer.batch_decode(generated_tokens, skip_special_tokens=False)
        return [parse_rebel_output(decoded) for decoded in decoded_batch]
    except Exception as e:
        print(f"    REBEL batch parse error: {e}")
        return [[] for _ in abstracts]

# fallback gemini extractor just in case
triplet_llm = extractor_llm.with_structured_output(TripletList)
def extract_triplets_gemini(abstract: str) -> List[Triplet]:
    result = triplet_llm.invoke(f"Extract medical relationship triplets from this text: {abstract}")
    return result.triplets

def ingest_to_neo4j(triplets: List[Triplet]):
    with neo4j_driver.session() as session:
        for t in triplets:
            rel = t.relation.strip().replace(" ", "_").replace("-", "_").upper()
            try:
                session.run(
                    f"MERGE (s:Entity {{name: $s}}) MERGE (o:Entity {{name: $o}}) MERGE (s)-[r:{rel}]->(o)",
                    s=t.subject.lower(), o=t.object.lower()
                )
            except Exception:
                pass

def print_stats(conn: sqlite3.Connection):
    n_articles = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    n_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    db_size = Path(DB_PATH).stat().st_size / (1024**2) if Path(DB_PATH).exists() else 0
    chroma_size = sum(f.stat().st_size for f in Path("./chroma_local_storage").rglob("*") if f.is_file()) / (1024**2) if Path("./chroma_local_storage").exists() else 0

    print("\n-- Storage Stats --")
    print(f"Articles in SQLite: {n_articles}")
    print(f"Chunks in SQLite: {n_chunks}")
    print(f"SQLite DB size: {db_size:.1f} MB")
    print(f"ChromaDB size: {chroma_size:.1f} MB")
    print()

def run_chroma_phase(progress: dict, conn: sqlite3.Connection):
    print("\nRunning Phase 1: PubMed Fetch & ChromaDB Embeddings")
    chroma_done = set(progress["chroma_done"])
    all_pmids = []
    
    for query, retmax in QUERIES:
        try:
            pmids = search_pubmed(query, retmax)
            all_pmids.extend(pmids)
            print(f"Found {len(pmids)} for '{query}'")
            time.sleep(0.4 if not Entrez.api_key else 0.12)
        except Exception as e:
            print(f"Search failed for '{query}': {e}")

    unique_pmids = list(dict.fromkeys(all_pmids))
    new_pmids = [p for p in unique_pmids if p not in chroma_done]

    if not new_pmids:
        print("Nothing new to ingest.")
        return

    articles = fetch_articles_batch(new_pmids)
    ingested = 0

    for article in tqdm(articles, desc="Saving chunks", unit="paper"):
        if ingest_article(conn, article):
            ingested += 1
            progress["chroma_done"].append(article["pmid"])
            if ingested % 100 == 0:
                save_progress(progress)

    save_progress(progress)
    print(f"Phase 1 done. {ingested} papers stored.")
    print_stats(conn)

def run_neo4j_phase(progress: dict, conn: sqlite3.Connection, extractor: str = "rebel"):
    if not NEO4J_AVAILABLE:
        print("Neo4j isn't running. Skipping phase 2.")
        return

    print(f"\nRunning Phase 2: Neo4j Extraction using {extractor.upper()}")
    neo4j_done = set(progress["neo4j_done"])
    chroma_done = set(progress["chroma_done"])
    pending = [p for p in chroma_done if p not in neo4j_done]

    if extractor == "gemini":
        to_process = pending[:NEO4J_DAILY_LIMIT]
    else:
        to_process = pending

    if not to_process:
        print("Graph is up to date.")
        return

    pipe = load_rebel() if extractor == "rebel" else None
    extracted = 0
    
    # IMPORTANT: Keep this low (like 4 or 8) while testing on the laptop to avoid crashing it
    BATCH_SIZE = 32 

    for i in tqdm(range(0, len(to_process), BATCH_SIZE), desc="Extracting Triplets", unit="batch"):
        batch_pmids = to_process[i : i + BATCH_SIZE]
        batch_abstracts = []
        valid_pmids = []
        
        for pmid in batch_pmids:
            abstract = get_article_text(conn, pmid)
            if abstract:
                batch_abstracts.append(abstract)
                valid_pmids.append(pmid)
                
        if not batch_abstracts:
            continue

        try:
            if extractor == "rebel":
                batch_triplets = extract_triplets_rebel_batch(pipe, batch_abstracts)
                for pmid, triplets in zip(valid_pmids, batch_triplets):
                    ingest_to_neo4j(triplets)
                    progress["neo4j_done"].append(pmid)
                    extracted += 1
            else:
                for pmid, abstract in zip(valid_pmids, batch_abstracts):
                    triplets = extract_triplets_gemini(abstract)
                    ingest_to_neo4j(triplets)
                    progress["neo4j_done"].append(pmid)
                    extracted += 1
                    time.sleep(0.3)

            save_progress(progress)

        except Exception as e:
            print(f"\nBatch failed: {e}")
            if extractor == "gemini":
                time.sleep(2)

    save_progress(progress)
    print(f"Phase 2 done. Added {extracted} papers to Neo4j.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--chroma", action="store_true")
    parser.add_argument("--neo4j", action="store_true")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--extractor", choices=["rebel", "gemini"], default="rebel")
    args = parser.parse_args()

    conn = init_db()
    progress = load_progress()

    if args.stats:
        print_stats(conn)
        sys.exit(0)

    run_both = not args.chroma and not args.neo4j
    if args.chroma or run_both:
        run_chroma_phase(progress, conn)
    if args.neo4j or run_both:
        run_neo4j_phase(progress, conn, extractor=args.extractor)
    conn.close()