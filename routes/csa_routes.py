from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from firebase_admin import firestore
from datetime import datetime, timedelta
import io
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

csa_bp = Blueprint('csa', __name__)
db = firestore.client()

# --- HELPER: Strict Role Check ---
def check_csa_role():
    if 'user' not in session: return False
    return session['user'].get('role') == 'csa'

# --- 1. DASHBOARD ---
@csa_bp.route('/dashboard')
def dashboard():
    if not check_csa_role(): return redirect(url_for('auth.login'))
    
    try:
        user_id = session['user']['uid']
        csa_doc = db.collection('users').document(user_id).get()
        csa_data = csa_doc.to_dict()
        managed_batch_ids = csa_data.get('managed_batch_ids', [])
        
        my_batches = []
        total_students = 0
        if managed_batch_ids:
            batches_ref = db.collection('batches').where(firestore.FieldPath.document_id(), 'in', managed_batch_ids).stream()
            for b in batches_ref:
                batch = b.to_dict()
                batch['id'] = b.id
                my_batches.append(batch)
                total_students += batch.get('student_count', 0)
        
        threshold = datetime.now() - timedelta(days=7)
        alerts = 0
        if managed_batch_ids:
            lazy = db.collection('users').where('role','==','student')\
                .where('batch_id','in',managed_batch_ids)\
                .where('last_active','<',threshold).stream()
            alerts = len(list(lazy))

        return render_template('csa/dashboard.html', 
                             user=session['user'],
                             batches=my_batches,
                             stats={'batch_count': len(my_batches), 'total_students': total_students, 'alerts': alerts})
    except Exception as e:
        print(f"Error: {e}")
        return render_template('csa/dashboard.html', user=session['user'], batches=[], stats={})

# --- 2. TASK MANAGEMENT ---
@csa_bp.route('/tasks')
def task_manager():
    if not check_csa_role(): return redirect(url_for('auth.login'))
    
    user_id = session['user']['uid']
    csa_doc = db.collection('users').document(user_id).get()
    managed_batch_ids = csa_doc.to_dict().get('managed_batch_ids', [])
    
    my_tasks = []
    if managed_batch_ids:
        tasks_ref = db.collection('assignments').where('assigned_to_batch', 'in', managed_batch_ids).order_by('created_at', direction=firestore.Query.DESCENDING).stream()
        for t in tasks_ref:
            data = t.to_dict()
            data['id'] = t.id
            my_tasks.append(data)

    now = datetime.now()
    drives_ref = db.collection('placement_drives').where('deadline', '>=', now.strftime('%Y-%m-%d')).stream()
    active_drives = [{'id': d.id, **d.to_dict()} for d in drives_ref]
    
    my_batches = []
    if managed_batch_ids:
        batches_ref = db.collection('batches').where(firestore.FieldPath.document_id(), 'in', managed_batch_ids).stream()
        my_batches = [{'id': b.id, 'name': b.to_dict().get('batch_name')} for b in batches_ref]

    return render_template('csa/tasks.html', user=session['user'], tasks=my_tasks, drives=active_drives, batches=my_batches)

# --- 3. CREATE TASK ---
@csa_bp.route('/tasks/create', methods=['POST'])
def create_task():
    if not check_csa_role(): return redirect(url_for('auth.login'))
    
    try:
        data = {
            'title': request.form['title'],
            'description': request.form['description'],
            'deadline': request.form['deadline'],
            'assigned_to_batch': request.form['batch_id'],
            'created_by': session['user']['uid'],
            'created_at': firestore.SERVER_TIMESTAMP,
            'type': 'assignment', 
            'status': 'active'
        }
        db.collection('assignments').add(data)
        flash("Task created successfully!", "success")
    except Exception as e:
        flash(f"Error creating task: {e}", "error")
        
    return redirect(url_for('csa.task_manager'))

# --- 4. REPOST DRIVE ---
@csa_bp.route('/tasks/repost/<drive_id>', methods=['POST'])
def repost_drive(drive_id):
    if not check_csa_role(): return redirect(url_for('auth.login'))
    
    try:
        drive_ref = db.collection('placement_drives').document(drive_id).get()
        drive_data = drive_ref.to_dict()
        target_batch = request.form['batch_id']
        
        data = {
            'title': f"⚠️ APPLY NOW: {drive_data.get('company_name')}",
            'description': f"Mandatory Application. Original Drive Details: {drive_data.get('job_role')}. Please apply via Placement Portal and upload proof here.",
            'deadline': drive_data.get('deadline'),
            'assigned_to_batch': target_batch,
            'created_by': session['user']['uid'],
            'created_at': firestore.SERVER_TIMESTAMP,
            'type': 'repost', 
            'linked_drive_id': drive_id,
            'status': 'active'
        }
        
        db.collection('assignments').add(data)
        flash(f"Drive reposted to batch!", "success")
        
    except Exception as e:
        flash(f"Error reposting drive: {e}", "error")
        
    return redirect(url_for('csa.task_manager'))

# --- 5. VIEW BATCH ---
@csa_bp.route('/batch/<batch_id>')
def view_batch(batch_id):
    if not check_csa_role(): return redirect(url_for('auth.login'))
    
    batch_ref = db.collection('batches').document(batch_id).get()
    batch_data = batch_ref.to_dict()
    batch_data['id'] = batch_id
    
    students_ref = db.collection('users').where('batch_id', '==', batch_id).stream()
    students = []
    threshold = datetime.now() - timedelta(days=7)
    
    for s in students_ref:
        d = s.to_dict()
        d['id'] = s.id
        last = d.get('last_active')
        d['is_lazy'] = False
        if not last: d['is_lazy'] = True
        else:
            try:
                if hasattr(last, 'replace') and last.replace(tzinfo=None) < threshold: d['is_lazy'] = True
            except: pass
        students.append(d)
        
    return render_template('csa/batch_view.html', user=session['user'], batch=batch_data, students=students)

# --- 6. STUDENT FILTER PAGE ---
@csa_bp.route('/students', methods=['GET', 'POST'])
def students():
    if not check_csa_role(): return redirect(url_for('auth.login'))
    
    query = db.collection('users').where('role', '==', 'student')
    if request.method == 'POST':
        dept = request.form.get('department')
        skills = request.form.get('skills')
        if dept: query = query.where('department', '==', dept)
            
    results = query.stream()
    students_list = []
    
    for doc in results:
        data = doc.to_dict()
        data['id'] = doc.id
        if request.method == 'POST' and skills:
            req_skills = [s.strip().lower() for s in skills.split(',')]
            user_skills = [s.lower() for s in data.get('skills', [])]
            if not any(item in user_skills for item in req_skills): continue
        students_list.append(data)

    return render_template('csa/students.html', user=session['user'], students=students_list)

# --- 7. MESSAGES PAGE ---
@csa_bp.route('/messages')
def messages():
    if not check_csa_role(): return redirect(url_for('auth.login'))
    
    users = []
    docs = db.collection('users').stream()
    current_uid = session['user']['uid']
    for doc in docs:
        if doc.id == current_uid: continue 
        d = doc.to_dict()
        users.append({'id': doc.id, 'name': d.get('full_name', 'Unknown'), 'user_type': d.get('role', 'student').upper()})
        
    return render_template('csa/messages.html', user=session['user'], users=users)

# --- 8. API: SINGLE DOCUMENT NOTIFICATION (Free Tier Friendly) ---
@csa_bp.route('/api/notifications')
def get_notifications():
    if 'user' not in session: return jsonify([])
    
    # Read the SINGLE status document
    doc = db.collection('system_stats').document('lazy_alert_status').get()
    
    if doc.exists:
        data = doc.to_dict()
        msg = data.get('latest_message')
        msg_date = data.get('message_date')
        
        # Only show if message exists and is from today/recent
        if msg and msg_date:
            # Simple check: is it from last 24h?
            try:
                msg_dt = msg_date.replace(tzinfo=None)
                if (datetime.now() - msg_dt).total_seconds() < 86400:
                    return jsonify([{
                        'title': 'Daily Compliance Alert',
                        'message': msg
                    }])
            except: pass
            
    return jsonify([])

# --- 9. API: CHAT ---
@csa_bp.route('/api/messages/<user_id>')
def get_chat(user_id):
    current = session['user']['uid']
    msgs = db.collection('messages').where('participants', 'array_contains', current).order_by('timestamp').stream()
    result = []
    for m in msgs:
        d = m.to_dict()
        if user_id in d.get('participants', []): result.append(d)
    return jsonify(result)

@csa_bp.route('/api/messages', methods=['POST'])
def send_chat():
    data = request.json
    db.collection('messages').add({
        'sender_id': session['user']['uid'],
        'receiver_id': data['receiver_id'],
        'message': data['message'],
        'participants': [session['user']['uid'], data['receiver_id']],
        'timestamp': firestore.SERVER_TIMESTAMP
    })
    return jsonify({'status': 'sent'})

@csa_bp.route('/batch/<batch_id>/defaulters_report')
def generate_defaulters_report(batch_id):
    return redirect(url_for('csa.dashboard'))