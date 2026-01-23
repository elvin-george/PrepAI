import os
import requests
import json
from langchain_community.vectorstores import FAISS
# Handle imports safely
try:
    from langchain_huggingface import HuggingFaceEmbeddings
except ImportError:
    from langchain_community.embeddings import HuggingFaceEmbeddings
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

# 1. Setup Local Embeddings
local_embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

def get_optimized_model_name():
    """Finds the requested Flash model to avoid Quota/404 errors"""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        response = requests.get(url)
        
        if response.status_code == 200:
            models = response.json().get('models', [])
            model_names = [m['name'] for m in models]
            
            # --- PRIORITY LIST (Updated per your request) ---
            
            # 1. Gemini 2.5 Flash (User Requested)
            for m in model_names: 
                if 'gemini-2.5-flash' in m: return m.split('/')[-1]

            # 2. Gemini 1.5 Flash (Standard Fallback)
            for m in model_names: 
                if 'gemini-1.5-flash' in m and 'latest' not in m: return m.split('/')[-1]

            # 3. Gemini 2.0 Flash (Experimental)
            for m in model_names: 
                if 'gemini-2.0-flash' in m: return m.split('/')[-1]

            # 4. Last Resort: Any available generative model
            for m in model_names:
                if 'generateContent' in m.get('supportedGenerationMethods', []):
                    return m.split('/')[-1]

        # If auto-discovery fails, force the requested one
        return "gemini-2.5-flash" 
    except:
        return "gemini-2.5-flash"

# Initialize Model Selection
CURRENT_MODEL = get_optimized_model_name()
print(f"🤖 AI Chat configured to use: {CURRENT_MODEL}")

def get_rag_response(user_question):
    try:
        # --- PART 1: RETRIEVAL ---
        try:
            vector_store = FAISS.load_local(
                "faiss_index", 
                local_embeddings, 
                allow_dangerous_deserialization=True
            )
            docs = vector_store.similarity_search(user_question, k=4)
        except Exception:
            return "⚠️ System Error: Index not found. Please run 'python utils/build_rag_index.py' first."
        
        if not docs:
            return "I couldn't find any information about that in the provided documents."

        # --- PART 2: GENERATION ---
        context_text = "\n\n".join([d.page_content for d in docs])
        
        final_prompt = f"""
        You are an intelligent assistant for Saintgits College.
        Answer based strictly on the context below.
        
        [CONTEXT]:
        {context_text}
        
        [QUESTION]:
        {user_question}
        
        [ANSWER]:
        """
        
        # API Call
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{CURRENT_MODEL}:generateContent?key={api_key}"
        
        headers = {'Content-Type': 'application/json'}
        data = { "contents": [{ "parts": [{"text": final_prompt}] }] }
        
        response = requests.post(url, headers=headers, json=data)
        
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        elif response.status_code == 429:
             return "⚠️ Traffic Limit: The AI is busy (Quota Exceeded). Please wait a moment."
        else:
            # Fallback: If 2.5 fails, try 1.5-flash immediately
            if CURRENT_MODEL == "gemini-2.5-flash":
                fallback_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
                response = requests.post(fallback_url, headers=headers, json=data)
                if response.status_code == 200:
                     return response.json()['candidates'][0]['content']['parts'][0]['text']
            
            return f"Google API Error ({CURRENT_MODEL}): {response.text}"

    except Exception as e:
        return f"System Error: {str(e)}"