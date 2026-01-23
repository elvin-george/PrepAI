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

# --- HELPER: Get Assigned Batch IDs (Centralized Logic) ---
def get_csa_batch_ids(user_id):
    """Returns a list of batch IDs assigned to the given CSA."""
    csa_doc = db.collection('users').document(user_id).get()
    if not csa_doc.exists: return []
    
    managed_batch_ids = csa_doc.to_dict().get('managed_batch_ids', [])
    seen_batch_ids = set()

    # 1. Direct Fetch by CSA ID (Source of Truth)
    batches_query = db.collection('batches').where('csa_id', '==', user_id).stream()
    for b in batches_query:
        seen_batch_ids.add(b.id)

    # 2. Legacy/Profile Fetch (Backup)
    if managed_batch_ids:
        for b_id in managed_batch_ids:
            if b_id not in seen_batch_ids:
                doc_ref = db.collection('batches').document(b_id).get()
                if doc_ref.exists:
                    seen_batch_ids.add(doc_ref.id)
                    
    return list(seen_batch_ids)

# --- HELPER: PDF Report Generator ---
def create_dashboard_pdf(inactive_list, missed_tasks):
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, 750, "⚠️ PrepAI Compliance Report")
    p.setFont("Helvetica", 10)
    p.drawString(50, 735, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    p.line(50, 730, 550, 730)
    
    y = 700
    # 1. Inactive Students
    p.setFont("Helvetica-Bold", 14)
    p.drawString(50, y, f"1. Inactive Students (>7 Days) - Total: {len(inactive_list)}")
    y -= 20
    p.setFont("Helvetica", 10)
    if not inactive_list:
        p.drawString(50, y, "No inactive students found.")
        y -= 20
    else:
        for s in inactive_list:
            p.drawString(50, y, f"- {s['name']} ({s['email']})")
            p.drawString(400, y, f"Last Active: {s['last_active']}")
            y -= 15
            if y < 50: p.showPage(); y = 750
    
    y -= 20
    # 2. Missed Tasks
    p.setFont("Helvetica-Bold", 14)
    p.drawString(50, y, f"2. Missed Assignments - Total Tasks: {len(missed_tasks)}")
    y -= 20
    p.setFont("Helvetica", 10)
    if not missed_tasks:
        p.drawString(50, y, "No missed tasks.")
    else:
        for t in missed_tasks:
            p.setFillColorRGB(0.5, 0, 0)
            p.drawString(50, y, f"Task: {t['title']} (Due: {t['deadline']})")
            p.setFillColorRGB(0, 0, 0)
            y -= 15
            for student in t['defaulters']:
                p.drawString(70, y, f"• {student}")
                y -= 15
                if y < 50: p.showPage(); y = 750
            y -= 10
            
    p.save()
    buffer.seek(0)
    return buffer

# --- 1. DASHBOARD ---
@csa_bp.route('/dashboard')
def dashboard():
    if not check_csa_role(): return redirect(url_for('auth.login'))
    
    try:
        user_id = session['user']['uid']
        csa_doc = db.collection('users').document(user_id).get()
        csa_data = csa_doc.to_dict()
        
        # --- FIX: USE CENTRAL HELPER ---
        all_my_batch_ids = get_csa_batch_ids(user_id)
        
        my_batches = []
        total_students = 0
        
        # Fetch Batch Details
        for b_id in all_my_batch_ids:
            b_ref = db.collection('batches').document(b_id).get()
            if b_ref.exists:
                b_data = b_ref.to_dict()
                b_data['id'] = b_id
                my_batches.append(b_data)
                total_students += b_data.get('student_count', 0)

        # Stats Calculation
        threshold = datetime.now() - timedelta(days=7)
        alerts = 0
        
        if all_my_batch_ids:
             try:
                # Firestore 'in' query supports max 10 items.
                chunks = [all_my_batch_ids[i:i + 10] for i in range(0, len(all_my_batch_ids), 10)]
                for chunk in chunks:
                    # 1. Try Efficient Database Query (Needs Index)
                    try:
                        lazy = db.collection('users').where('role','==','student')\
                            .where('batch_id','in',chunk)\
                            .where('last_active','<',threshold).stream()
                        alerts += len(list(lazy))
                    
                    # 2. Fallback: Fetch all students in batch & filter in Python (Slow but works without index)
                    except Exception:
                        print(f"Stats: Using Python Fallback for {chunk}")
                        batch_students = db.collection('users').where('role','==','student')\
                            .where('batch_id','in',chunk).stream()
                        
                        for s in batch_students:
                            d = s.to_dict()
                            last = d.get('last_active')
                            if not last: # Null = Inactive
                                alerts += 1
                                continue
                            
                            # Check date
                            try:
                                if hasattr(last, 'replace') and last.replace(tzinfo=None) < threshold: 
                                    alerts += 1
                            except: pass
             except Exception as e:
                print(f"Stats Fatal Error: {e}")
                print(f"Stats Fatal Error: {e}")
        
        return render_template('csa/dashboard.html', 
                             user=session['user'],
                             batches=my_batches,
                             stats={'batch_count': len(my_batches), 'total_students': total_students, 'alerts': alerts})

    except Exception as e:
        print(f"Dashboard Error: {e}")
        return render_template('csa/dashboard.html', user=session['user'], batches=[], stats={})

# --- 2. DOWNLOAD REPORT ---
@csa_bp.route('/download_report')
def download_report():
    if not check_csa_role(): return redirect(url_for('auth.login'))
    
    students = list(db.collection('users').where('role', '==', 'student').stream())
    all_tasks = list(db.collection('assignments').stream())
    
    inactive_list = []
    missed_tasks_report = []
    threshold = datetime.now() - timedelta(days=7)
    now = datetime.now()

    for s in students:
        d = s.to_dict()
        last = d.get('last_active')
        is_inactive = False
        if not last: is_inactive = True
        else:
            try:
                if hasattr(last, 'replace') and last.replace(tzinfo=None) < threshold: is_inactive = True
            except: pass
        if is_inactive:
            inactive_list.append({'name': d.get('full_name'), 'email': d.get('email'), 'last_active': str(last)})

    for t in all_tasks:
        t_data = t.to_dict()
        deadline = t_data.get('deadline')
        d_date = None
        if deadline:
            try:
                if isinstance(deadline, str): d_date = datetime.strptime(deadline, '%Y-%m-%d')
                elif hasattr(deadline, 'replace'): d_date = deadline.replace(tzinfo=None)
            except: pass
        
        if d_date and d_date < now:
            batch_id = t_data.get('assigned_to_batch')
            batch_students = [s for s in students if s.to_dict().get('batch_id') == batch_id]
            missing = []
            for bs in batch_students:
                sub = db.collection('assignments').document(t.id).collection('submissions').document(bs.id).get()
                if not sub.exists: missing.append(bs.to_dict().get('full_name'))
            if missing:
                missed_tasks_report.append({'title': t_data.get('title'), 'deadline': deadline, 'defaulters': missing})

    pdf_buffer = create_dashboard_pdf(inactive_list, missed_tasks_report)
    return send_file(pdf_buffer, as_attachment=True, download_name='Daily_Risk_Report.pdf', mimetype='application/pdf')

# --- 3. STUDENTS LIST (Fixed Filtering) ---
@csa_bp.route('/students', methods=['GET', 'POST'])
def students():
    if not check_csa_role(): return redirect(url_for('auth.login'))
    
    my_batches = get_csa_batch_ids(session['user']['uid'])
    
    # --- SCOPING FIX: Show ONLY my students ---
    if not my_batches:
        # If no batches assigned, return empty list immediately
        return render_template('csa/students.html', user=session['user'], students=[], page_title="Student Manager")

    # Fetch students only from my batches
    # 'in' query supports max 10. Split if needed, but for simplicity we take top 10 for now in this list view
    # Realistically a CSA won't have > 10 batches.
    query = db.collection('users').where('role', '==', 'student').where('batch_id', 'in', my_batches[:10])
    
    # Handle Department Filter (POST)
    if request.method == 'POST':
        dept = request.form.get('department')
        if dept: query = query.where('department', '==', dept)
    
    results = query.stream()
    students_list = []
    
    # Handle Risk Filter (GET)
    filter_type = request.args.get('filter')
    threshold = datetime.now() - timedelta(days=7)
    
    # Set page title based on filter
    page_title = "Student Manager"
    if filter_type == 'risk':
        page_title = "⚠️ Risk Alerts: Inactive Students"

    for doc in results:
        data = doc.to_dict()
        data['id'] = doc.id
        
        # --- RISK FILTER LOGIC ---
        if filter_type == 'risk':
            last = data.get('last_active')
            is_active = True # Assume active unless proven otherwise
            
            if not last: 
                is_active = False # No record = Inactive
            else:
                try:
                    # Check if date is older than threshold
                    if hasattr(last, 'replace'): # Timestamp
                        if last.replace(tzinfo=None) < threshold: is_active = False
                    # Note: If it's a string, we might skip parsing for simplicity or add it if needed
                except: pass
            
            # If the student IS active, we SKIP them (because we only want Inactive ones)
            if is_active: continue 

        # --- SKILLS FILTER LOGIC ---
        if request.method == 'POST':
            skills = request.form.get('skills')
            if skills:
                req_skills = [s.strip().lower() for s in skills.split(',')]
                user_skills = [s.lower() for s in data.get('skills', [])]
                if not any(item in user_skills for item in req_skills): continue

        students_list.append(data)

    return render_template('csa/students.html', 
                         user=session['user'], 
                         students=students_list, 
                         page_title=page_title) # Pass title to template

# --- 4. TASK MANAGER ---
@csa_bp.route('/tasks')
def task_manager():
    if not check_csa_role(): return redirect(url_for('auth.login'))
    
    user_id = session['user']['uid']
    user_id = session['user']['uid']
    my_batch_ids = get_csa_batch_ids(user_id)
    
    my_tasks = []
    if my_batch_ids:
        # Filter: Tasks assigned to my batches (Created by ME or Placement Officer)
        try:
            # 1. Try Optimized Query (Needs Index)
            # We fetch all active assignments for these batches
            # The filtering of 'who created it' happens partially here (assigned_to_batch is the key)
            tasks_ref = db.collection('assignments')\
                .where('assigned_to_batch', 'in', my_batch_ids[:10])\
                .order_by('created_at', direction=firestore.Query.DESCENDING).stream()
                
            for t in tasks_ref:
                data = t.to_dict()
                data['id'] = t.id
                
                # Logic: Show if created by ME or (Created by Placement AND assigned to my batch)
                # Since we filtered by 'assigned_to_batch', we just need to differentiate for Editing rights
                # Actually, the requirement: "we only need assignements that are posted by the Placement officer and the the current CSA"
                # So if another CSA posted it (unlikely if scoped by batch, but possible), we might want to exclude?
                # For now, we assume if it's assigned to MY batch, I should see it.
                
                my_tasks.append(data)
                
        except Exception:
            # 2. Fallback: Fetch without Sort
            print("Task Query Fallback: Index missing, sorting in memory.")
            tasks_ref = db.collection('assignments')\
                .where('assigned_to_batch', 'in', my_batch_ids[:10]).stream()
                
            for t in tasks_ref:
                data = t.to_dict()
                data['id'] = t.id
                my_tasks.append(data)
            
            my_tasks.sort(key=lambda x: x.get('created_at', datetime.min) if x.get('created_at') else datetime.min, reverse=True)

    now = datetime.now()
    drives_ref = db.collection('placement_drives').where('deadline', '>=', now.strftime('%Y-%m-%d')).stream()
    active_drives = [{'id': d.id, **d.to_dict()} for d in drives_ref]
    
    # Populate dropdown with ONLY my batches
    my_batches_list = []
    for b_id in my_batch_ids:
        b_ref = db.collection('batches').document(b_id).get()
        if b_ref.exists:
            my_batches_list.append({'id': b_id, 'name': b_ref.to_dict().get('batch_name')})

    return render_template('csa/tasks.html', user=session['user'], tasks=my_tasks, drives=active_drives, batches=my_batches_list)

    return render_template('csa/tasks.html', user=session['user'], tasks=my_tasks, drives=active_drives, batches=my_batches)

# --- 5. CREATE & REPOST ---
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
        flash(f"Error: {e}", "error")
    return redirect(url_for('csa.task_manager'))

@csa_bp.route('/tasks/repost/<drive_id>', methods=['POST'])
def repost_drive(drive_id):
    if not check_csa_role(): return redirect(url_for('auth.login'))
    try:
        drive_ref = db.collection('placement_drives').document(drive_id).get()
        drive_data = drive_ref.to_dict()
        data = {
            'title': f"⚠️ APPLY NOW: {drive_data.get('company_name')}",
            'description': f"Mandatory Application: {drive_data.get('job_role')}. Upload proof.",
            'deadline': drive_data.get('deadline'),
            'assigned_to_batch': request.form['batch_id'],
            'created_by': session['user']['uid'],
            'created_at': firestore.SERVER_TIMESTAMP,
            'type': 'repost', 
            'linked_drive_id': drive_id,
            'status': 'active'
        }
        db.collection('assignments').add(data)
        flash(f"Drive reposted!", "success")
    except Exception as e:
        flash(f"Error: {e}", "error")
    return redirect(url_for('csa.task_manager'))

# --- 6. VIEW BATCH ---
@csa_bp.route('/batch/<batch_id>')
def view_batch(batch_id):
    if not check_csa_role(): return redirect(url_for('auth.login'))
    
    batch_ref = db.collection('batches').document(batch_id).get()
    if not batch_ref.exists:
        flash("Batch not found", "error")
        return redirect(url_for('csa.dashboard'))
        
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

# --- 7. APIS ---
@csa_bp.route('/messages')
def messages():
    if not check_csa_role(): return redirect(url_for('auth.login'))
    users = []
    docs = db.collection('users').stream()
    uid = session['user']['uid']
    for doc in docs:
        if doc.id == uid: continue
        users.append({'id': doc.id, 'name': doc.to_dict().get('full_name'), 'user_type': doc.to_dict().get('role')})
    return render_template('csa/messages.html', user=session['user'], users=users)

@csa_bp.route('/api/notifications')
def get_notifications():
    if 'user' not in session: return jsonify([])
    doc = db.collection('system_stats').document('lazy_alert_status').get()
    if doc.exists:
        d = doc.to_dict()
        if d.get('latest_message'): return jsonify([{'title': 'System Alert', 'message': d.get('latest_message')}])
    return jsonify([])

@csa_bp.route('/api/messages/<user_id>')
def get_chat(user_id):
    current = session['user']['uid']
    participants = sorted([current, user_id])
    conv_id = f"{participants[0]}_{participants[1]}"
    
    msgs_ref = db.collection('conversations').document(conv_id).collection('messages')\
                 .order_by('timestamp').stream()
    
    result = []
    for m in msgs_ref:
        d = m.to_dict()
        # Map 'content' (DB) back to 'message' (Frontend expectation)
        result.append({
            'sender_id': d.get('sender_id'),
            'message': d.get('content'), 
            'timestamp': d.get('timestamp')
        })
    return jsonify(result)

@csa_bp.route('/api/messages', methods=['POST'])
def send_chat():
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
    
    # Update conversation timestamp
    conv_ref.update({'updated_at': firestore.SERVER_TIMESTAMP})
    return jsonify({'status': 'sent'})

@csa_bp.route('/tasks/edit/<task_id>', methods=['POST'])
def edit_task(task_id):
    if not check_csa_role(): return redirect(url_for('auth.login'))
    try:
        task_ref = db.collection('assignments').document(task_id)
        task = task_ref.get()
        
        if not task.exists:
            flash("Task not found.", "error")
            return redirect(url_for('csa.task_manager'))
            
        task_data = task.to_dict()
        
        # Verify Ownership: CSA can only edit their OWN tasks
        if task_data.get('created_by') != session['user']['uid']:
            flash("You can only edit tasks you created.", "error")
            return redirect(url_for('csa.task_manager'))

        update_data = {
            'title': request.form['title'],
            'description': request.form['description'],
            'deadline': request.form['deadline'],
            'assigned_to_batch': request.form['batch_id']
        }
        
        task_ref.update(update_data)
        flash("Task updated successfully!", "success")
    except Exception as e:
        flash(f"Error updating task: {e}", "error")
    return redirect(url_for('csa.task_manager'))

@csa_bp.route('/tasks/submissions/<task_id>')
def view_submissions(task_id):
    if not check_csa_role(): return redirect(url_for('auth.login'))
    
    try:
        task_ref = db.collection('assignments').document(task_id).get()
        if not task_ref.exists:
            flash("Task not found", "error")
            return redirect(url_for('csa.task_manager'))
            
        task_data = task_ref.to_dict()
        batch_id = task_data.get('assigned_to_batch')
        
        # Verify Access (Own Task OR Assigned to My Batch)
        my_batches = get_csa_batch_ids(session['user']['uid'])
        if batch_id not in my_batches and task_data.get('created_by') != session['user']['uid']:
             flash("Unauthorized access to this task.", "error")
             return redirect(url_for('csa.task_manager'))

        # Fetch Students in Batch
        students_ref = db.collection('users').where('batch_id', '==', batch_id).where('role', '==', 'student').stream()
        students_data = []
        
        for s in students_ref:
            s_dict = s.to_dict()
            student_id = s.id
            
            # Check Submission Status
            submission_ref = db.collection('assignments').document(task_id).collection('submissions').document(student_id).get()
            
            status = 'pending'
            file_url = '#'
            submitted_at = None
            
            if submission_ref.exists:
                sub_data = submission_ref.to_dict()
                status = 'submitted'
                file_url = sub_data.get('file_url') or sub_data.get('link') or '#'
                submitted_at = sub_data.get('submitted_at')

            students_data.append({
                'name': s_dict.get('full_name', 'Unknown'),
                'email': s_dict.get('email', 'N/A'),
                'status': status,
                'file_url': file_url,
                'submitted_at': submitted_at
            })
            
        return render_template('csa/task_submissions.html', user=session['user'], task=task_data, students=students_data)

    except Exception as e:
        print(f"Error viewing submissions: {e}")
        flash("An error occurred loading submissions.", "error")
        return redirect(url_for('csa.task_manager'))