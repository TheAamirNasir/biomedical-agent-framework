from neo4j import GraphDatabase

# local neo4j desktop connection
URI = "bolt://localhost:7687"
AUTH = ("neo4j", "password123") 

# hardcoding this for now just to make sure the cypher logic works before adding LangGraph
triplets = [
    {"subject": "lecanemab", "relation": "TREATS", "object": "early Alzheimer's disease"},
    {"subject": "lecanemab", "relation": "REDUCES", "object": "brain amyloid burden"},
    {"subject": "lecanemab", "relation": "BINDS_TO", "object": "Aβ soluble protofibrils"},
    {"subject": "Aβ soluble protofibrils", "relation": "PLAYS_ROLE_IN", "object": "early Alzheimer's disease"}
]

print("connecting...")
driver = GraphDatabase.driver(URI, auth=AUTH)
driver.verify_connectivity()

with driver.session() as session:
    # clear old stuff so it doesn't duplicate
    session.run("MATCH (n) DETACH DELETE n")
    
    print("inserting nodes...")
    for t in triplets:
        # cypher query to merge nodes and relationships
        query = f"""
        MERGE (s:Entity {{name: $subject}})
        MERGE (o:Entity {{name: $object}})
        MERGE (s)-[r:{t['relation']}]->(o)
        """
        session.run(query, subject=t["subject"], object=t["object"])
        print(f"linked {t['subject']} -> {t['object']}")

    print("\ntesting retrieval:")
    # find what the drug connects to
    res = session.run("MATCH (d:Entity {name: 'lecanemab'})-[r]->(target) RETURN type(r) as rel, target.name as name")
    for r in res:
        print(f"lecanemab {r['rel']} {r['name']}")