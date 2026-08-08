import json
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

# 1. Define the strict mathematical output we want from the Judge
class FaithfulnessScore(BaseModel):
    reasoning: str = Field(description="Step-by-step logic checking if the answer is derived ONLY from the context.")
    is_faithful: int = Field(description="Score 1 if the answer is completely supported by the context, 0 if it contains hallucinations.")

# 2. Set up the Judge LLM (Swapped to free Gemini API)
judge_llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0).with_structured_output(FaithfulnessScore)

# 3. The Strict Grading Prompt
grading_prompt = ChatPromptTemplate.from_messages([
    ("system", """You are a strict medical data grader. 
    Your job is to check if the 'Generated Answer' is faithfully derived ONLY from the 'Retrieved Context'.
    If the answer includes ANY medical claims, drugs, or diseases not present in the context, it is a hallucination. Score it 0.
    If the answer correctly states that the context does not contain the information, score it 1."""),
    ("human", "Question: {question}\n\nRetrieved Context: {context}\n\nGenerated Answer: {answer}")
])

grading_chain = grading_prompt | judge_llm

# Example function to grade a single pipeline run
def grade_pipeline_response(question, retrieved_context, generated_answer):
    score = grading_chain.invoke({
        "question": question,
        "context": retrieved_context,
        "answer": generated_answer
    })
    return score.is_faithful, score.reasoning