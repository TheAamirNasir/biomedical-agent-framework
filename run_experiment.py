import json
import csv
from agent import app as langgraph_agent, chroma_col, embed_model, llm
from evaluator import grade_pipeline_response

# load up the test questions
with open('evaluation_dataset.json', 'r') as f:
    eval_data = json.load(f)

results_log = []
print("Starting evaluation... this might take a while.")

for idx, item in enumerate(eval_data):
    question = item['question']
    print(f"\n[{idx+1}/{len(eval_data)}] Testing: {question}")
    
    # --- TEST 1: Baseline Vector Search (Chroma Only) ---
    try:
        # actually querying chroma here instead of using placeholders
        q_vec = embed_model.embed_query(question)
        chroma_res = chroma_col.query(query_embeddings=[q_vec], n_results=3)
        vector_context = " ".join(chroma_res["documents"][0])
        
        # asking the llm to answer using ONLY the vector context
        prompt = f"Answer this using ONLY the context. Context: {vector_context}\nQuestion: {question}"
        vector_answer = llm.invoke(prompt).content
        
        v_score, v_reason = grade_pipeline_response(question, vector_context, vector_answer)
    except Exception as e:
        print(f"Vector baseline failed: {e}")
        vector_answer, v_score, v_reason = "Error", 0, str(e)
    
    # --- TEST 2: LangGraph Framework ---
    try:
        agent_output = langgraph_agent.invoke({"question": question, "hallucination_detected": False})
        graph_context = agent_output.get("context", "Critic Node Intercepted")
        graph_answer = agent_output.get("generation", "Failed to generate answer")
        
        g_score, g_reason = grade_pipeline_response(question, graph_context, graph_answer)
    except Exception as e:
        print(f"Graph pipeline failed: {e}")
        graph_answer, g_score, g_reason = "Error", 0, str(e)
    
    # log everything
    results_log.append({
        "Question": question,
        "Type": item['question_type'],
        "Vector_Answer": vector_answer,
        "Vector_Faithfulness_Score": v_score,
        "Graph_Answer": graph_answer,
        "Graph_Faithfulness_Score": g_score,
        "Judge_Reasoning_Vector": v_reason,
        "Judge_Reasoning_Graph": g_reason
    })

# dump to csv for the thesis charts
csv_file = "final_experiment_results.csv"
with open(csv_file, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=results_log[0].keys())
    writer.writeheader()
    writer.writerows(results_log)

print(f"\nDone! Saved to {csv_file}")