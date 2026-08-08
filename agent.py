import chromadb
from neo4j import GraphDatabase
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_huggingface import HuggingFaceEmbeddings
from typing import TypedDict, List
from langgraph.graph import StateGraph, END

# setup connections
embed_model   = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
chroma_client = chromadb.PersistentClient(path="./chroma_local_storage")
chroma_col    = chroma_client.get_or_create_collection(name="pubmed_test")
neo4j_driver  = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "password123"))

llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0,
    google_api_key="GEMINI_API_KEY"
)

# list of words to ignore when extracting entities for neo4j
STOPWORDS = {
    "what", "where", "when", "who", "how", "why", "which", "whose",
    "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "shall", "can",
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or",
    "but", "with", "from", "by", "into", "through", "over", "under",
    "than", "then", "there", "here", "about", "between", "among",
    "within", "across", "along", "toward", "against", "without",
    "this", "that", "these", "those", "its", "it", "their", "they",
    "any", "all", "each", "every", "both", "some", "such",
    "more", "most", "other", "same", "own", "out", "up", "down",
    "not", "no", "so", "if", "while", "although", "because", "since",
    "further", "again", "very", "too", "also", "just", "only", "even",
    "significant", "significantly", "primary", "secondary", "specific",
    "general", "main", "major", "minor", "overall", "total", "direct",
    "associated", "involved", "related", "observed", "detected",
    "reported", "demonstrated", "indicated", "known", "seen", "found",
    "including", "included", "include", "given", "show", "shows",
    "shown", "suggest", "suggests", "suggested",
    "placebo", "drug", "treatment", "therapy", "patients", "patient",
    "group", "groups", "compared", "comparison", "versus", "trial",
    "study", "results", "result", "effect", "effects", "data",
    "phase", "clinical", "baseline", "endpoint", "measure", "measures",
    "rate", "rates", "percent", "change", "changes", "reduction",
    "increase", "decrease", "score", "scores",
}

def extract_keyword(text: str) -> str:
    # grab the most meaningful word from the question for neo4j lookup
    words = text.lower().replace("?", "").replace(",", "").replace(".", "").split()
    candidates = [w for w in words if w not in STOPWORDS and len(w) > 4]
    if not candidates:
        return "lecanemab" # fallback so it doesn't break
    return max(candidates, key=len)

class AgentState(TypedDict):
    question:             str
    database_route:       str
    context:              str
    sources:              List[str]
    generation:           str
    hallucination_detected: bool


def routing_node(state: AgentState):
    question = state["question"].lower()
    complex_keywords = ["relate", "mechanism", "how", "connect", "effect", "impact"]
    # route to graph if it asks a complex question
    if any(w in question for w in complex_keywords):
        return {"database_route": "neo4j"}
    return {"database_route": "chroma"}

def chroma_retriever_node(state: AgentState):
    import sqlite3 as _sqlite3

    query_vector = embed_model.embed_query(state["question"])
    results = chroma_col.query(
        query_embeddings=[query_vector],
        n_results=3,
        include=["documents", "metadatas", "distances"]
    )
    retrieved_text = " ".join(results["documents"][0])

    # get the full citations from sqlite
    sources = []
    seen_pmids = set()
    try:
        conn = _sqlite3.connect("pubmed_articles.db")
        for meta in results.get("metadatas", [[]])[0]:
            pmid = (meta or {}).get("pmid", "")
            if not pmid or pmid in seen_pmids:
                continue
            seen_pmids.add(pmid)
            row = conn.execute("SELECT citation FROM articles WHERE pmid=?", (pmid,)).fetchone()
            sources.append(row[0] if row else f"PMID: {pmid}")
        conn.close()
    except Exception:
        pass

    if not sources:
        sources = ["Source: PubMed"]

    return {"context": retrieved_text, "sources": sources}

def neo4j_retriever_node(state: AgentState):
    keyword = extract_keyword(state["question"])

    with neo4j_driver.session() as session:
        # get 1-hop relationships for the keyword
        cypher = """
        MATCH (n:Entity)-[r]->(m:Entity)
        WHERE toLower(n.name) CONTAINS $keyword OR toLower(m.name) CONTAINS $keyword
        RETURN n.name + ' ' + type(r) + ' ' + m.name AS triplet,
               n.name AS subject, m.name AS object
        LIMIT 10
        """
        result = session.run(cypher, keyword=keyword)
        rows = list(result)
        triplets = [r["triplet"] for r in rows]
        entities = list({r["subject"] for r in rows} | {r["object"] for r in rows})

    context = " | ".join(triplets) if triplets else "No graph relationships found."
    sources = (
        [f"Neo4j Knowledge Graph — entity: '{keyword}', connected nodes: {', '.join(entities[:6])}"]
        if triplets else []
    )
    return {"context": context, "sources": sources}


def generation_node(state: AgentState):
    context = state["context"]
    question = state["question"]
    sources = state.get("sources", [])

    prompt = f"""
    You are an expert Medical Research AI. Transform the provided context into a professional, evidence-based clinical summary.

    ### CONTEXT DATA:
    {context}

    ### USER INQUIRY:
    {question}

    ### MANDATORY FORMATTING RULES:
    1. Start with a header: "🩺 CLINICAL TRIAL SUMMARY: [Topic]"
    2. Section 1: "Key Findings" - A brief 2-3 sentence overview.
    3. Section 2: "Primary Data Points" - Use bullet points for specific metrics.
    4. Section 3: "Mechanism/Safety" - Summarize biology or adverse events.
    5. Section 4: "Clinical Conclusion" - One final sentence on treatment efficacy.
    6. PROHIBITED: Do not include JSON brackets, signatures, or metadata.

    PROFESSIONAL BRIEF:"""

    response = llm.invoke(prompt)

    # clean up the response format
    if isinstance(response.content, list):
        generation = " ".join(block["text"] for block in response.content if isinstance(block, dict) and block.get("type") == "text").strip()
    else:
        generation = response.content.strip()

    # add references at the bottom
    if sources:
        unique_sources = list(dict.fromkeys(sources))
        refs = "\n\n---\n**📚 References**\n" + "\n".join(f"- {s}" for s in unique_sources)
        generation += refs

    return {"generation": generation}


def critic_node(state: AgentState):
    # just a pass-through for now, hallucination logic happens in the evaluator
    return {"hallucination_detected": False}


def route_database(state: AgentState):
    return "neo4j_retriever" if state["database_route"] == "neo4j" else "chroma_retriever"

# build the graph
workflow = StateGraph(AgentState)
workflow.add_node("router", routing_node)
workflow.add_node("chroma_retriever", chroma_retriever_node)
workflow.add_node("neo4j_retriever", neo4j_retriever_node)
workflow.add_node("generator", generation_node)
workflow.add_node("critic", critic_node)

workflow.set_entry_point("router")
workflow.add_conditional_edges("router", route_database)
workflow.add_edge("chroma_retriever", "generator")
workflow.add_edge("neo4j_retriever", "generator")
workflow.add_edge("generator", "critic")
workflow.add_edge("critic", END)

app = workflow.compile()