from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
import os
import requests
import json
from datetime import datetime

interview_bp = Blueprint('interview', __name__)

# Load API Key
api_key = os.getenv("GEMINI_API_KEY")

# --- SMART MODEL FINDER (Same as Chatbot) ---
def get_working_model():
    """Finds the best available model to avoid 404 errors"""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        response = requests.get(url)
        
        if response.status_code == 200:
            models = response.json().get('models', [])
            model_names = [m['name'] for m in models]
            
            # Priority 1: Gemini 2.5 Flash (What worked yesterday)
            for m in model_names: 
                if 'gemini-2.5-flash' in m: return m.split('/')[-1]

            # Priority 2: Gemini 1.5 Flash
            for m in model_names: 
                if 'gemini-1.5-flash' in m and 'latest' not in m: return m.split('/')[-1]

            # Priority 3: Any generative model
            for m in model_names:
                if 'generateContent' in m.get('supportedGenerationMethods', []):
                    return m.split('/')[-1]
                    
        return "gemini-2.5-flash" # Default fallback
    except:
        return "gemini-2.5-flash"

# Set the model ONCE when app starts
MODEL_NAME = get_working_model()
print(f"🎤 Mock Interview configured to use: {MODEL_NAME}")

def call_gemini(prompt):
    """Helper to call Gemini via Direct HTTP"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    data = { "contents": [{ "parts": [{"text": prompt}] }] }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        else:
            return f"Error: {response.text}"
    except Exception as e:
        return f"System Error: {str(e)}"

@interview_bp.route('/setup')
def setup():
    if 'user' not in session or session['user']['role'] != 'student':
        return redirect(url_for('auth.login'))
    return render_template('student/ai/interview.html')

@interview_bp.route('/generate_question', methods=['POST'])
def generate_question():
    data = request.json
    topic = data.get('topic', 'General HR')
    difficulty = data.get('difficulty', 'Medium')
    
    prompt = f"""
    Act as a strict technical interviewer.
    Topic: {topic}
    Difficulty: {difficulty}
    
    Task: Generate exactly ONE interview question. 
    Do not provide the answer. 
    Do not add introductory text. 
    Just output the question.
    """
    
    question = call_gemini(prompt)
    session['current_question'] = question
    return jsonify({'question': question})

@interview_bp.route('/submit_answer', methods=['POST'])
def submit_answer():
    data = request.json
    user_answer = data.get('answer')
    question = session.get('current_question', 'Unknown Question')
    
    prompt = f"""
    You are an interviewer evaluating a candidate.
    
    Question: "{question}"
    Candidate Answer: "{user_answer}"
    
    Task:
    1. Give a Score out of 10.
    2. Provide constructive feedback (max 2 sentences).
    3. Provide a 'Model Answer' (what they should have said).
    
    Output strictly in JSON format:
    {{
        "score": 8,
        "feedback": "...",
        "model_answer": "..."
    }}
    """
    
    response_text = call_gemini(prompt)
    
    # Clean up JSON
    try:
        clean_json = response_text.replace('```json', '').replace('```', '').strip()
        feedback_data = json.loads(clean_json)
    except:
        feedback_data = {
            "score": "?", 
            "feedback": response_text, 
            "model_answer": "N/A"
        }
        
    return jsonify(feedback_data)