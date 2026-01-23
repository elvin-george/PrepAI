from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from firebase_admin import firestore
from datetime import datetime
from utils.ai_helper import get_rag_response
from flask import jsonify

student_bp = Blueprint('student', __name__)
db = firestore.client()

# --- HELPER: Strict Role Check ---
def check_student_role():
    if 'user' not in session: return False
    return session['user'].get('role') == 'student'

# --- 1. DASHBOARD ---
@student_bp.route('/dashboard')
def dashboard():
    if not check_student_role(): return redirect(url_for('auth.login'))
    
    user_id = session['user']['uid']
    
    # 1. Fetch User Profile (for Resume URL & Batch ID)
    user_ref = db.collection('users').document(user_id)
    user_doc = user_ref.get()
    user_data = user_doc.to_dict()
    batch_id = user_data.get('batch_id')

    # 2. Fetch Active Assignments (for this batch)
    active_tasks = []
    if batch_id:
        tasks_ref = db.collection('assignments')\
            .where('assigned_to_batch', '==', batch_id)\
            .where('status', '==', 'active')\
            .stream()
            
        for t in tasks_ref:
            t_data = t.to_dict()
            t_data['id'] = t.id
            # Check if already submitted
            sub_ref = db.collection('assignments').document(t.id).collection('submissions').document(user_id).get()
            t_data['is_submitted'] = sub_ref.exists
            active_tasks.append(t_data)
        
        # Sort by deadline manually
        active_tasks.sort(key=lambda x: x.get('deadline'))

    # 3. Fetch Recent Notifications (Global or Role specific)
    # For simplicity, we just fetch the latest system alert if strictly needed, 
    # but the base.html handles the dropdown. We will pass a few "Recent Activities" here.
    
    # Calculate Stats
    pending_count = sum(1 for t in active_tasks if not t['is_submitted'])
    completed_count = sum(1 for t in active_tasks if t['is_submitted'])
    
    return render_template('student/dashboard.html', 
                         user=user_data, 
                         tasks=active_tasks,
                         stats={
                             'pending': pending_count,
                             'completed': completed_count,
                             'attendance': user_data.get('attendance', 85) # Placeholder default
                         })

# --- 2. UPDATE PROFILE (Resume URL) ---
@student_bp.route('/update_profile', methods=['POST'])
def update_profile():
    if not check_student_role(): return redirect(url_for('auth.login'))
    
    try:
        resume_url = request.form.get('resume_url')
        skills = request.form.get('skills') # Optional: Comma separated
        
        data = {
            'resume_url': resume_url,
            'last_updated': firestore.SERVER_TIMESTAMP
        }
        
        if skills:
            data['skills'] = [s.strip() for s in skills.split(',')]

        db.collection('users').document(session['user']['uid']).update(data)
        
        # Update Session Data to reflect changes immediately if needed
        session['user']['resume_url'] = resume_url
        
        flash("Profile updated successfully!", "success")
    except Exception as e:
        flash(f"Error updating profile: {e}", "error")
        
    return redirect(url_for('student.dashboard'))

# --- 3. SUBMIT ASSIGNMENT ---
@student_bp.route('/submit_task/<task_id>', methods=['POST'])
def submit_task(task_id):
    if not check_student_role(): return redirect(url_for('auth.login'))
    
    try:
        submission_link = request.form.get('submission_link')
        
        # Save submission
        sub_data = {
            'student_id': session['user']['uid'],
            'student_name': session['user']['full_name'],
            'submission_link': submission_link,
            'submitted_at': firestore.SERVER_TIMESTAMP,
            'status': 'submitted'
        }
        
        # Add to sub-collection of the assignment
        db.collection('assignments').document(task_id).collection('submissions').document(session['user']['uid']).set(sub_data)
        
        flash("Assignment submitted successfully!", "success")
    except Exception as e:
        flash(f"Error submitting task: {e}", "error")
        
    return redirect(url_for('student.dashboard'))

# --- 4. CHAT INTERFACE PAGE ---
@student_bp.route('/chat')
def chat_interface():
    if not check_student_role(): return redirect(url_for('auth.login'))
    user_data = session.get('user', {})
    return render_template('student/chat.html', user=user_data)

# --- 5. API TO HANDLE MESSAGES ---
@student_bp.route('/api/chat', methods=['POST'])
def chat_api():
    if not check_student_role(): return jsonify({"error": "Unauthorized"}), 403
    
    data = request.json
    user_message = data.get('message', '')
    
    if not user_message:
        return jsonify({"response": "Please say something!"})

    # Call our RAG Helper
    ai_reply = get_rag_response(user_message)
    
    return jsonify({"response": ai_reply})