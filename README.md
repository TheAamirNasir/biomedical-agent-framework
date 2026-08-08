# Autonomous Multi-Agent Framework for Complex Domain-Specific Reasoning

This repository contains my final MSc project (University of Leicester, CO7201). It is an autonomous, multi-agent Retrieval-Augmented Generation (RAG) framework designed to tackle complex medical literature analysis.

## The Problem
Standard RAG pipelines rely on vector similarity, which struggles with multi-hop logical questions in high-stakes domains like medicine. If a system cannot connect "Drug A" to "Mechanism B" to "Side Effect C", it risks hallucinating clinical outcomes.

## The Solution
I engineered an autonomous LLM reasoning engine utilizing a LangGraph state machine to dynamically route biomedical queries. 

*   **Hybrid Storage:** Utilizes both a vector database (ChromaDB) for semantic retrieval and a local knowledge graph (Neo4j) for relationship mapping.
*   **Automated Ingestion:** Features a PyTorch-optimized extraction pipeline using Bio.Entrez and a local REBEL model to ingest PubMed abstracts and build a dense Knowledge Graph.
*   **Safety Intercepts:** Implements a 'Critic Node' that audits generated responses. If an output cannot be strictly grounded in the database, the agent triggers a safety intercept, rejecting the query rather than hallucinating.

## Empirical Results
Evaluated using the RAGAS framework against a baseline Vector RAG system:
*   **Faithfulness:** Maintained 100% factual accuracy by successfully intercepting adversarial/out-of-domain queries.
*   **Context Recall:** The Graph-Augmented framework achieved a 25% increase in data recall compared to the baseline vector search.