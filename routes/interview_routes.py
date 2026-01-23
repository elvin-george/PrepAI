from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
import google.generativeai as genai
import os

interview_bp = Blueprint('interview', __name__)

# Configure AI
api_key = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=api_key)
model = genai.GenerativeModel('gemini-1.5-flash')

def check_student():
    return 'user' in session and session['user']['role'] == 'student'

@interview_bp.route('/setup')
def setup_interview():
    if not check_student(): return redirect(url_for('auth.login'))
    return render_template('student/interview_setup.html')

@interview_bp.route('/start', methods=['POST'])
def start_interview():
    if not check_student(): return jsonify({'error': 'Unauthorized'}), 401
    
    topic = request.json.get('topic', 'General HR')
    difficulty = request.json.get('difficulty', 'Medium')
    
    # 1. Ask AI to generate a question
    prompt = f"""
    Act as a strict technical interviewer. 
    Generate a single, unique {difficulty}-level interview question about {topic}.
    Do not provide the answer. Just the question.
    """
    response = model.generate_content(prompt)
    
    # Store context in session
    session['current_question'] = response.text
    session['current_topic'] = topic
    
    return jsonify({'question': response.text})

@interview_bp.route('/submit_answer', methods=['POST'])
def submit_answer():
    if not check_student(): return jsonify({'error': 'Unauthorized'}), 401
    
    user_answer = request.json.get('answer')
    question = session.get('current_question')
    
    if not question: return jsonify({'error': 'No active session'}), 400
    
    # 2. Ask AI to grade the answer
    prompt = f"""
    You are an interviewer. 
    Question: "{question}"
    Student Answer: "{user_answer}"
    
    Task:
    1. Rate the answer out of 10.
    2. Provide 2 lines of specific feedback on how to improve.
    3. Suggest a "Model Answer" (the perfect response).
    
    Format the output as JSON:
    {{
        "score": 7,
        "feedback": "Good attempt, but you missed...",
        "model_answer": "A better way to say this is..."
    }}
    """
    # Force JSON response mode (Gemini 1.5 Feature)
    response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
    
    return jsonify({'result': response.text})