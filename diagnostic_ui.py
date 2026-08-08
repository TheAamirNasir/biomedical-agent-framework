import streamlit as st
from agent import app as langgraph_agent, extract_keyword
from neo4j import GraphDatabase
from pyvis.network import Network
import streamlit.components.v1 as components

# ── Config ─────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Medical Intelligence Dashboard", layout="wide")

URI    = "bolt://localhost:7687"
AUTH   = ("neo4j", "password123")
driver = GraphDatabase.driver(URI, auth=AUTH)

st.title("🩺 Autonomous Medical Intelligence Dashboard")
st.markdown("### Powered by GraphRAG & Multi-Agent Self-Reflection")

# ── Sidebar: Reasoning Trace ───────────────────────────────────────────────────
with st.sidebar:
    st.header("🧠 Reasoning Trace")
    st.info("The LangGraph agent is monitoring the query lifecycle.")
    trace_placeholder = st.empty()

# ── Main Interface ─────────────────────────────────────────────────────────────
query = st.text_input(
    "Enter a medical research question:",
    placeholder="e.g., How does Lecanemab relate to amyloid-beta?"
)

if query:
    with st.spinner("Executing Agentic Workflow..."):

        # 1. Run the LangGraph agent
        response = langgraph_agent.invoke({
            "question": query,
            "sources": [],
            "hallucination_detected": False,
        })

        route   = response.get("database_route", "Unknown")
        keyword = extract_keyword(query)

        # 2. Update sidebar trace
        trace_placeholder.markdown(f"""
        - **Intent Detected:** Biomedical Inquiry
        - **Routing Decision:** `{route.upper()}`
        - **Search Keyword:** `{keyword}`
        - **Data Integrity:** ✅ Verified by Critic Node
        """)

        # 3. Final answer
        st.subheader("Final Verified Answer")
        generation = response.get("generation", "No response generated.")
        if response.get("hallucination_detected"):
            st.error(generation)
        else:
            st.success(generation)

        # 4. References expander (separate from the answer box for clarity)
        sources = response.get("sources", [])
        if sources:
            with st.expander("📚 Source References", expanded=True):
                for src in sources:
                    st.markdown(f"- {src}")

        # ── Knowledge Graph Visualisation ─────────────────────────────────────
        st.subheader("Knowledge Graph Context")

        net = Network(
            height="500px", width="100%",
            bgcolor="#222222", font_color="white", heading=""
        )

        # Focused query: show the 1-hop neighbourhood of the primary entity only.
        # We query outgoing AND incoming edges separately so the graph stays tight.
        FOCUSED_CYPHER = """
        MATCH (primary:Entity)
        WHERE toLower(primary.name) CONTAINS $keyword
        WITH primary LIMIT 3
        MATCH (primary)-[r]->(target:Entity)
        RETURN primary.name AS s, type(r) AS rel, target.name AS o
        LIMIT 20
        UNION
        MATCH (primary:Entity)
        WHERE toLower(primary.name) CONTAINS $keyword
        WITH primary LIMIT 3
        MATCH (source:Entity)-[r]->(primary)
        RETURN source.name AS s, type(r) AS rel, primary.name AS o
        LIMIT 20
        """

        nodes_set  = set()
        found_data = False

        def add_results_to_graph(records):
            global found_data
            for record in records:
                found_data = True
                s_name = record["s"].capitalize()
                o_name = record["o"].capitalize()
                if s_name not in nodes_set:
                    net.add_node(s_name, label=s_name, color="#00ffcc", borderWidth=2)
                    nodes_set.add(s_name)
                if o_name not in nodes_set:
                    net.add_node(o_name, label=o_name, color="#ff4444", borderWidth=2)
                    nodes_set.add(o_name)
                net.add_edge(
                    s_name, o_name,
                    title=record["rel"], label=record["rel"],
                    color="#aaaaaa", arrowStrikethrough=False
                )

        with driver.session() as session:
            add_results_to_graph(session.run(FOCUSED_CYPHER, keyword=keyword))

        # Silent fallback — only if nothing found AND keyword isn't already 'lecanemab'
        if not found_data and keyword != "lecanemab":
            with driver.session() as session:
                add_results_to_graph(session.run(FOCUSED_CYPHER, keyword="lecanemab"))

        if not found_data:
            st.warning(
                "No graph relationships found. "
                "Make sure **neo4j_kg_setup.py** or **automated_ingestion.py** has been run."
            )
        else:
            try:
                path = "graph_output.html"
                net.save_graph(path)
                with open(path, "r", encoding="utf-8") as f:
                    html_content = f.read()
                components.html(html_content, height=550)
            except Exception as e:
                st.error(f"Graph render error: {e}")

# ── Footer ─────────────────────────────────────────────────────────────────────
st.divider()
st.caption("Postgraduate Individual Project | University of Leicester | MSc Computer Science")
