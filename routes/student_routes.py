from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from firebase_admin import firestore
from datetime import datetime
from utils.ai_helper import get_rag_response
import os
import requests
import json
import io
from pypdf import PdfReader  # Make sure to run: pip install pypdf

student_bp = Blueprint('student', __name__)
db = firestore.client()
api_key = os.getenv("GEMINI_API_KEY")

# --- HELPER: Strict Role Check ---
def check_student_role():
    if 'user' not in session: return False
    return session['user'].get('role') == 'student'

# --- HELPER: Direct Gemini Call (Reliable for Tools) ---
def call_gemini_api(prompt):
    """
    Directly calls Gemini API for tools (Roadmap, Resume, etc.).
    Prioritizes Gemini 2.5 Flash as requested, then falls back to 1.5 Flash and Pro.
    """
    if not api_key:
        print("Error: GEMINI_API_KEY not found in environment variables.")
        return None

    # Prioritize Gemini 2.5 Flash as requested, then fallbacks
    models_to_try = ["gemini-2.5-flash", "gemini-1.5-flash", "gemini-1.5-pro"]
    
    headers = {'Content-Type': 'application/json'}
    data = { "contents": [{ "parts": [{"text": prompt}] }] }

    for model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        try:
            response = requests.post(url, headers=headers, json=data)
            if response.status_code == 200:
                return response.json()['candidates'][0]['content']['parts'][0]['text']
            elif response.status_code == 429: # Quota limit
                print(f"Quota exceeded for {model}, trying next...")
                continue
            else:
                print(f"Gemini API Error ({model}): {response.text}")
        except Exception as e:
            print(f"System Error ({model}): {e}")
            
    return None

# ==========================================
#  1. DASHBOARD & CORE ROUTES
# ==========================================

# --- HELPER: Fetch Active Tasks ---
def get_student_tasks(user_id, batch_id):
    if not batch_id: return []
    
    tasks = []
    try:
        tasks_ref = db.collection('assignments')\
            .where('assigned_to_batch', '==', batch_id)\
            .stream()
            
        for t in tasks_ref:
            t_data = t.to_dict()
            t_data['id'] = t.id
            
            if t_data.get('status', 'active') != 'active': continue

            # Check submission
            sub_ref = db.collection('assignments').document(t.id).collection('submissions').document(user_id).get()
            t_data['is_submitted'] = sub_ref.exists
            tasks.append(t_data)
            
        tasks.sort(key=lambda x: x.get('deadline', ''))
    except Exception as e:
        print(f"Error fetching tasks: {e}")
        
    return tasks

# --- HELPER: Fetch Active Drives (Robust) ---
def get_active_drives(user_id):
    drives = []
    # Debug entry to check if template renders list at all
    # drives.append({'id': 'debug', 'company': 'Debug Tech', 'role': 'Test Role', 'ctc': '10LPA', 'date': '2025-12-31', 'has_applied': False})
    
    try:
        drives_ref = db.collection('placement_drives').stream()
        current_time_str = datetime.now().strftime('%Y-%m-%d')

        for d in drives_ref:
            try:
                doc = d.to_dict()
                doc['id'] = d.id
                
                # 1. Expiry Check (Handle Strings and Timestamps)
                deadline = doc.get('deadline')
                is_expired = False
                
                if deadline:
                    deadline_str = ""
                    # Convert Timestamp/Datetime to YYYY-MM-DD String
                    if hasattr(deadline, 'strftime'):
                        deadline_str = deadline.strftime('%Y-%m-%d')
                    else:
                        deadline_str = str(deadline)
                        
                    if deadline_str < current_time_str:
                        is_expired = True

                if is_expired: continue

                # 2. Applied Status
                has_applied = False
                try:
                    # Check sub-collection
                    app_ref = d.reference.collection('applicants').document(user_id).get()
                    if app_ref.exists: has_applied = True
                except: pass

                # 3. Robust Field Mapping
                role = doc.get('role_title') or doc.get('role') or doc.get('job_role') or doc.get('title') or 'Open Role'
                
                drives.append({
                    'company': doc.get('company_name', 'Unknown Company'),
                    'role': role,
                    'ctc': doc.get('package', 'Not Disclosed'),
                    'date': str(deadline) if deadline else 'Open',
                    'id': d.id,
                    'description': doc.get('description', ''),
                    'created_at': doc.get('created_at'),
                    'has_applied': has_applied
                })
            except Exception as e:
                print(f"Skipping corrupt drive doc {d.id}: {e}")
                continue
            
        drives.sort(key=lambda x: x.get('created_at') if x.get('created_at') else datetime.min, reverse=True)
        
    except Exception as e:
        print(f"Error fetching drives: {e}")
        # Return empty list on fatal error
        
    return drives

# ==========================================
#  1. DASHBOARD & CORE ROUTES
# ==========================================

@student_bp.route('/dashboard')
def dashboard():
    if not check_student_role(): return redirect(url_for('auth.login'))
    
    user_id = session['user']['uid']
    
    # 1. Fetch User Profile
    user_ref = db.collection('users').document(user_id)
    user_doc = user_ref.get()
    user_data = user_doc.to_dict()
    batch_id = user_data.get('batch_id')

    # 2. Fetch Data using Helpers
    active_tasks = get_student_tasks(user_id, batch_id)
    drives = get_active_drives(user_id)

    # 3. Calculate Stats
    pending_count = sum(1 for t in active_tasks if not t['is_submitted'])
    completed_count = sum(1 for t in active_tasks if t['is_submitted'])
    
    # 4. Notifications (For Template)
    notifications = []
    for t in active_tasks[:2]:
        if not t.get('is_submitted'):
            notifications.append({'text': f'Pending: {t.get("title")}', 'time': 'Action Required', 'new': True})
            
    for d in drives[:2]:
        if not d['has_applied']:
            notifications.append({'text': f'New Drive: {d["company"]}', 'time': 'Apply Now', 'new': True})

    today = datetime.now().strftime("%B %d, %Y")

    return render_template('student/dashboard.html', 
                           user=user_data, 
                           tasks=active_tasks,
                           drives=drives,
                           today_date=today,
                           notifications=notifications,
                           stats={
                               'pending': pending_count,
                               'completed': completed_count,
                               'attendance': user_data.get('attendance', 85)
                           })

# ==========================================
#  2. MESSAGES & PROFILE
# ==========================================

@student_bp.route('/messages')
def messages():
    if not check_student_role(): return redirect(url_for('auth.login'))
    
    # Fetch Staff to chat with (CSA, HOD, Placement)
    # efficient query or python filter
    users = []
    docs = db.collection('users').stream() 
    uid = session['user']['uid']
    
    # Allowed roles for students to message
    allowed_roles = ['csa', 'hod', 'placement_officer', 'admin']
    
    for doc in docs:
        if doc.id == uid: continue
        d = doc.to_dict()
        if d.get('role') in allowed_roles:
             users.append({'id': doc.id, 'name': d.get('full_name'), 'user_type': d.get('role')})
             
    return render_template('student/messages.html', users=users)

@student_bp.route('/api/messages/<user_id>')
def get_chat(user_id):
    if not check_student_role(): return jsonify({"error": "Unauthorized"}), 403
    current = session['user']['uid']
    participants = sorted([current, user_id])
    conv_id = f"{participants[0]}_{participants[1]}"
    
    msgs_ref = db.collection('conversations').document(conv_id).collection('messages')\
                 .order_by('timestamp').stream()
    
    result = []
    for m in msgs_ref:
        d = m.to_dict()
        result.append({
            'sender_id': d.get('sender_id'),
            'message': d.get('content'), 
            'timestamp': d.get('timestamp')
        })
    return jsonify(result)

@student_bp.route('/api/messages', methods=['POST'])
def send_chat():
    if not check_student_role(): return jsonify({"error": "Unauthorized"}), 403
    data = request.json
    receiver_id = data['receiver_id']
    content = data['message']
    sender_id = session['user']['uid']
    
    participants = sorted([sender_id, receiver_id])
    conv_id = f"{participants[0]}_{participants[1]}"
    
    conv_ref = db.collection('conversations').document(conv_id)
    if not conv_ref.get().exists:
        conv_ref.set({'participants': participants, 'updated_at': firestore.SERVER_TIMESTAMP})
        
    conv_ref.collection('messages').add({
        'sender_id': sender_id,
        'content': content,
        'timestamp': firestore.SERVER_TIMESTAMP
    })
    
    conv_ref.update({'updated_at': firestore.SERVER_TIMESTAMP})
    return jsonify({'status': 'sent'})

@student_bp.route('/update_profile', methods=['POST'])
def update_profile():
    if not check_student_role(): return redirect(url_for('auth.login'))
    try:
        resume_url = request.form.get('resume_url')
        skills = request.form.get('skills')
        data = {'resume_url': resume_url, 'last_updated': firestore.SERVER_TIMESTAMP}
        if skills: data['skills'] = [s.strip() for s in skills.split(',')]
        
        db.collection('users').document(session['user']['uid']).update(data)
        session['user']['resume_url'] = resume_url
        flash("Profile updated successfully!", "success")
    except Exception as e:
        flash(f"Error: {e}", "error")
    return redirect(url_for('student.dashboard'))

@student_bp.route('/submit_task/<task_id>', methods=['POST'])
def submit_task(task_id):
    if not check_student_role(): return redirect(url_for('auth.login'))
    try:
        submission_link = request.form.get('submission_link')
        sub_data = {
            'student_id': session['user']['uid'],
            'student_name': session['user']['full_name'],
            'submission_link': submission_link,
            'submitted_at': firestore.SERVER_TIMESTAMP,
            'status': 'submitted'
        }
        db.collection('assignments').document(task_id).collection('submissions').document(session['user']['uid']).set(sub_data)
        flash("Submitted successfully!", "success")
    except Exception as e:
        flash(f"Error: {e}", "error")
    return redirect(url_for('student.dashboard'))

@student_bp.route('/apply_drive/<drive_id>', methods=['POST'])
def apply_drive(drive_id):
    if not check_student_role(): return redirect(url_for('auth.login'))
    try:
        user_id = session['user']['uid']
        user_name = session['user']['full_name']
        drive_ref = db.collection('placement_drives').document(drive_id)
        applicant_ref = drive_ref.collection('applicants').document(user_id)
        
        if applicant_ref.get().exists:
            flash("Already applied.", "info")
        else:
            applicant_ref.set({
                'student_id': user_id, 'name': user_name,
                'applied_at': firestore.SERVER_TIMESTAMP, 'status': 'applied'
            })
            flash("Applied successfully!", "success")
    except Exception as e:
        flash(f"Error: {e}", "error")
    return redirect(url_for('student.dashboard'))

# ==========================================
#  3. CHATBOT (RAG HYBRID)
# ==========================================

@student_bp.route('/chat')
def chat_interface():
    if not check_student_role(): return redirect(url_for('auth.login'))
    return render_template('student/chat.html', user=session.get('user', {}))

@student_bp.route('/api/chat', methods=['POST'])
def chat_api():
    if not check_student_role(): return jsonify({"error": "Unauthorized"}), 403
    data = request.json
    user_message = data.get('message', '')
    if not user_message: return jsonify({"response": "Please say something!"})
    
    # Uses the hybrid RAG helper we fixed earlier
    ai_reply = get_rag_response(user_message)
    return jsonify({"response": ai_reply})

@student_bp.route('/api/notifications')
def get_notifications_api():
    if not check_student_role(): return jsonify([])
    
    user_id = session['user']['uid']
    # We need batch_id to get tasks
    user_ref = db.collection('users').document(user_id).get()
    batch_id = user_ref.to_dict().get('batch_id')
    
    active_tasks = get_student_tasks(user_id, batch_id)
    drives = get_active_drives(user_id)
    
    alerts = []
    
    # 1. Unsubmitted Tasks
    for t in active_tasks:
        if not t.get('is_submitted'):
             alerts.append({
                'title': 'Pending Task',
                'message': f"Due: {t.get('title')}",
                'type': 'task'
            })
             
    # 2. New Drives
    for d in drives:
        if not d['has_applied']:
            alerts.append({
                'title': 'New Placement Drive',
                'message': f"{d['company']} ({d['role']})",
                'type': 'drive'
            })
            
    # Return top 5
    return jsonify(alerts[:5])

# ==========================================
#  4. NEW AI TOOLS (Direct API)
# ==========================================

# --- A. ROADMAP GENERATOR ---
@student_bp.route('/ai/roadmap')
def roadmap():
    if not check_student_role(): return redirect(url_for('auth.login'))
    return render_template('student/ai/roadmap.html')

@student_bp.route('/api/generate_roadmap', methods=['POST'])
def api_roadmap():
    data = request.json
    topic = data.get('topic')
    duration = data.get('duration')
    level = data.get('level')

    prompt = f"""
    Create a structured study roadmap for '{topic}'.
    Duration: {duration}
    Level: {level}
    
    Format the output strictly as a JSON list of weeks.
    Example:
    [
        {{"week": "Week 1", "title": "Basics", "topics": ["Topic A", "Topic B"]}},
        {{"week": "Week 2", "title": "Advanced", "topics": ["Topic C", "Topic D"]}}
    ]
    Do not add markdown formatting.
    """
    
    response = call_gemini_api(prompt)
    if not response:
        return jsonify({"error": "AI Service unavailable. Please try again later."}), 503

    try:
        cleaned_response = response.replace('```json', '').replace('```', '').strip()
        roadmap_data = json.loads(cleaned_response)
        return jsonify({"roadmap": roadmap_data})
    except:
        return jsonify({"error": "Failed to generate roadmap"}), 500

# --- B. NOTE SUMMARIZER ---
@student_bp.route('/ai/summarizer')
def summarizer():
    if not check_student_role(): return redirect(url_for('auth.login'))
    return render_template('student/ai/summarizer.html')

@student_bp.route('/api/summarize', methods=['POST'])
def api_summarize():
    data = request.json
    text = data.get('text')
    
    prompt = f"""
    Summarize the following study notes. 
    Use bullet points for key concepts. 
    Make it concise but comprehensive for exam revision.
    
    Notes:
    {text[:5000]}
    """
    
    summary = call_gemini_api(prompt)
    if not summary:
        return jsonify({"error": "AI Service unavailable. Please try again later."}), 503
        
    return jsonify({"summary": summary})

# --- C. QUIZ GENERATOR ---
@student_bp.route('/ai/quiz')
def quiz():
    if not check_student_role(): return redirect(url_for('auth.login'))
    return render_template('student/ai/quiz.html')

@student_bp.route('/api/quizgen', methods=['POST'])
def api_quizgen():
    data = request.json
    text = data.get('text')
    difficulty = data.get('difficulty', 'Medium')
    count = data.get('question_count', 5)
    
    prompt = f"""
    Generate {count} {difficulty} multiple-choice questions based on this text:
    "{text[:3000]}"
    
    Format strictly as a JSON array:
    [
        {{
            "question": "Question text?",
            "options": ["A", "B", "C", "D"],
            "answer": "Correct Option"
        }}
    ]
    Do not add markdown formatting.
    """
    
    response = call_gemini_api(prompt)
    if not response:
        return jsonify({"error": "AI Service unavailable. Please try again later."}), 503

    try:
        cleaned_response = response.replace('```json', '').replace('```', '').strip()
        quiz_data = json.loads(cleaned_response)
        return jsonify({"quiz": quiz_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- D. RESUME ANALYZER (PDF) ---
@student_bp.route('/resume_analysis')
def resume_analysis():
    if not check_student_role(): return redirect(url_for('auth.login'))
    return render_template('student/ai/resume_analysis.html')

@student_bp.route('/api/analyze_resume', methods=['POST'])
def api_analyze_resume():
    if 'resume' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['resume']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400

    try:
        # 1. Read PDF in Memory
        pdf_reader = PdfReader(io.BytesIO(file.read()))
        resume_text = ""
        for page in pdf_reader.pages:
            resume_text += page.extract_text() + "\n"
            
        # 2. Send to Gemini
        prompt = f"""
        Act as a strict HR manager. Analyze this resume text for a Software Engineer role.
        Resume Text:
        {resume_text[:4000]}
        
        Output strict JSON:
        {{
            "score": 75,
            "summary": "One sentence summary.",
            "strengths": ["Strength 1", "Strength 2"],
            "weaknesses": ["Weakness 1", "Weakness 2"],
            "suggestions": ["Tip 1", "Tip 2"]
        }}
        """
        
        
        response = call_gemini_api(prompt)
        if not response:
            return jsonify({"error": "AI Service unavailable."}), 503

        cleaned_response = response.replace('```json', '').replace('```', '').strip()
        analysis = json.loads(cleaned_response)
        
        return jsonify(analysis)
        
    except Exception as e:
        print(e)
        return jsonify({"error": "Could not analyze PDF. Ensure it is text-based."}), 500