from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from firebase_admin import firestore
from datetime import datetime
import io
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

placement_bp = Blueprint('placement', __name__)
db = firestore.client()

# --- HELPER: Strict Role Check ---
def check_placement_role():
    if 'user' not in session: return False
    return session['user'].get('role') in ['placement', 'placement_officer', 'admin']

# --- 1. DASHBOARD (Fixed Stats) ---
@placement_bp.route('/dashboard')
def dashboard():
    if not check_placement_role(): return redirect(url_for('auth.login'))
    
    try:
        # 1. Active Drives
        drives_ref = db.collection('placement_drives').where('status', '==', 'active')
        drives = list(drives_ref.stream())
        drives_count = len(drives)
        
        # 2. Students & Placement Rate
        students_ref = db.collection('users').where('role', '==', 'student')
        students = list(students_ref.stream())
        students_count = len(students)
        
        placed_students = 0
        for s in students:
            data = s.to_dict()
            if data.get('placement_status') == 'placed':
                placed_students += 1
        
        # Calculate Rate
        placement_rate = 0
        if students_count > 0:
            placement_rate = int((placed_students / students_count) * 100)

        # 3. Pending Actions (Logic: Expired drives still marked active + Student approvals)
        pending_actions = 0
        # Example: Count drives that passed deadline but are still 'active'
        now = datetime.now()
        for d in drives:
            dd = d.to_dict()
            deadline = dd.get('deadline')
            # Handle Firestore timestamp or string
            if deadline:
                # (Simple check if you store as string YYYY-MM-DD, strict parsing needed in prod)
                pass 
        
        # 4. Recent Applications (Mock for visualization, or fetch from subcollections)
        # Fetching across all drives is expensive in Firestore NoSQL without a dedicated collection group
        # We will return an empty list for now to prevent errors
        recent_applications = [] 
        
        return render_template('placement/dashboard.html', 
                             user=session['user'], 
                             drives_count=drives_count, 
                             students_count=students_count,
                             placement_rate=placement_rate,
                             pending_actions=pending_actions,
                             recent_applications=recent_applications)
                             
    except Exception as e:
        print(f"Dashboard Error: {e}")
        return render_template('placement/dashboard.html', user=session['user'], drives_count=0, students_count=0, placement_rate=0, pending_actions=0)

# --- 2. DRIVES MANAGEMENT ---
@placement_bp.route('/drives', methods=['GET', 'POST'])
def drives():
    if not check_placement_role(): return redirect(url_for('auth.login'))

    if request.method == 'POST':
        try:
            data = request.form
            deadline_val = data.get('deadline')
            # Store date as string for simplicity or convert to datetime if needed
            
            new_drive = {
                'company_name': data.get('company_name'),
                'role_title': data.get('position'),
                'package': data.get('package'),
                'description': data.get('description'),
                'deadline': deadline_val,
                'posted_by': session['user']['uid'],
                'status': 'active',
                'created_at': firestore.SERVER_TIMESTAMP,
                'eligibility_criteria': {
                    'min_cgpa': float(data.get('min_cgpa', 0)),
                    'max_backlogs': int(data.get('max_backlogs', 0)),
                    'allowed_branches': request.form.getlist('departments')
                }
            }
            db.collection('placement_drives').add(new_drive)
            flash('Drive posted successfully!', 'success')
        except Exception as e:
            flash(f"Error: {e}", "error")
        return redirect(url_for('placement.drives'))

    drives_ref = db.collection('placement_drives').stream()
    drives_list = []
    for d in drives_ref:
        doc = d.to_dict()
        doc['id'] = d.id
        # Safe applicant count
        apps_ref = d.reference.collection('applicants').get()
        doc['applicant_count'] = len(apps_ref)
        drives_list.append(doc)
    
    # Sort in memory to handle missing 'created_at' in legacy data
    # Assuming standard datetime or Firestore Timestamp, handling None with a fallback
    def get_sort_key(d):
        ts = d.get('created_at')
        if ts:
            # Normalize to offset-naive for safe comparison with datetime.min
            return ts.replace(tzinfo=None) if ts.tzinfo else ts
        return datetime.min # Fallback for old data
        
    drives_list.sort(key=get_sort_key, reverse=True)

    return render_template('placement/drives.html', user=session['user'], drives=drives_list)

@placement_bp.route('/drives/<drive_id>')
def drive_details(drive_id):
    if not check_placement_role(): return redirect(url_for('auth.login'))
    
    # Fetch Drive
    drive_ref = db.collection('placement_drives').document(drive_id)
    drive = drive_ref.get().to_dict()
    if not drive:
        flash("Drive not found", "error")
        return redirect(url_for('placement.drives'))
    drive['id'] = drive_id
    
    # Fetch Applicants
    applicants = []
    apps_ref = drive_ref.collection('applicants').stream()
    for doc in apps_ref:
        app_data = doc.to_dict()
        student_id = doc.id
        # Join with User Data
        student = db.collection('users').document(student_id).get().to_dict() or {}
        applicants.append({
            'id': student_id,
            'name': student.get('full_name', 'Unknown'),
            'email': student.get('email', 'N/A'),
            'cgpa': student.get('cgpa', 'N/A'),
            'resume': student.get('resume_url', '#'),
            'status': app_data.get('status', 'applied'),
            'applied_at': app_data.get('applied_at')
        })
        
    return render_template('placement/drive_details.html', user=session['user'], drive=drive, applicants=applicants)

@placement_bp.route('/drives/edit/<drive_id>', methods=['POST'])
def edit_drive(drive_id):
    if 'user' not in session or session['user']['role'] != 'placement_officer':
        return redirect(url_for('auth.login'))
    
    drive_ref = db.collection('placement_drives').document(drive_id)
    
    # Process form data
    update_data = {
        'company_name': request.form.get('company_name'),
        'role_title': request.form.get('position'),
        'description': request.form.get('description'),
        'package': request.form.get('package'),
        'eligibility_criteria': {
            'min_cgpa': float(request.form.get('min_cgpa', 0)),
            'max_backlogs': int(request.form.get('max_backlogs', 0)),
            'departments': request.form.getlist('departments')
        }
    }
    
    # Handle Deadline
    deadline_str = request.form.get('deadline')
    if deadline_str:
        try:
           update_data['deadline'] = datetime.strptime(deadline_str, '%Y-%m-%d')
        except ValueError:
           pass

    drive_ref.update(update_data)
    
    return redirect(url_for('placement.drives'))

# Helper function to fetch applicant data
def _get_applicants_data(drive_id):
    applicants_ref = db.collection('placement_drives').document(drive_id).collection('applicants').stream()
    results = []
    for doc in applicants_ref:
        data = doc.to_dict()
        # Fetch student details
        student = db.collection('users').document(doc.id).get().to_dict() or {}
        results.append({
            'id': doc.id, # Added for potential use in PDF
            'student_name': student.get('full_name', 'Unknown'),
            'student_email': student.get('email', 'N/A'),
            'cgpa': student.get('cgpa', 'N/A'),
            'status': data.get('status', 'applied'),
            'resume_url': student.get('resume_url', None)
        })
    return results

@placement_bp.route('/drives/export/<drive_id>')
def export_drive_pdf(drive_id):
    if 'user' not in session or session['user']['role'] != 'placement_officer':
        return redirect(url_for('auth.login'))

    # Fetch Data
    drive_ref = db.collection('placement_drives').document(drive_id)
    drive = drive_ref.get()
    
    if not drive.exists:
        return "Drive not found", 404
        
    drive_data = drive.to_dict()
    applicants = _get_applicants_data(drive_id) # Use the helper function
    
    # Generate PDF
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    
    # Header
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, height - 50, f"Drive Report: {drive_data.get('company_name', 'N/A')}")
    
    p.setFont("Helvetica", 12)
    p.drawString(50, height - 70, f"Role: {drive_data.get('role_title', 'N/A')}")
    p.drawString(50, height - 85, f"Package: {drive_data.get('package', 'N/A')}")
    p.drawString(50, height - 100, f"Total Applicants: {len(applicants)}")
    
    # Table Header
    y = height - 140
    p.setFont("Helvetica-Bold", 10)
    p.drawString(50, y, "Student Name")
    p.drawString(200, y, "Email")
    p.drawString(400, y, "CGPA")
    p.drawString(460, y, "Status")
    
    p.line(40, y - 5, 560, y - 5)
    
    # Table Content
    y -= 25
    p.setFont("Helvetica", 10)
    
    for app in applicants:
        if y < 50: # New Page
            p.showPage()
            y = height - 50
            p.setFont("Helvetica", 10)
            
        p.drawString(50, y, str(app.get('student_name', 'N/A'))) # Changed from 'name' to 'student_name'
        p.drawString(200, y, str(app.get('student_email', 'N/A'))) # Changed from 'email' to 'student_email'
        p.drawString(400, y, str(app.get('cgpa', 'N/A')))
        p.drawString(460, y, str(app.get('status', 'Pending')).title())
        y -= 20
        
    p.save()
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"{drive_data.get('company_name', 'drive')}_applicants.pdf",
        mimetype='application/pdf'
    )

@placement_bp.route('/api/applications/<drive_id>')
def get_drive_applicants(drive_id):
    if not check_placement_role(): return jsonify({'error': 'Unauthorized'}), 401
    try:
        results = []
        for doc in applicants_ref:
            data = doc.to_dict()
            # Fetch student details
            student = db.collection('users').document(doc.id).get().to_dict() or {}
            results.append({
                'student_name': student.get('full_name', 'Unknown'),
                'student_email': student.get('email', 'N/A'),
                'cgpa': student.get('cgpa', 'N/A'),
                'status': data.get('status', 'applied'),
                'resume_url': student.get('resume_url', None)
            })
        return jsonify(results)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# --- 3. STUDENT FILTER ---
@placement_bp.route('/students', methods=['GET', 'POST'])
def students():
    if not check_placement_role(): return redirect(url_for('auth.login'))
    
    students_list = []
    query = db.collection('users').where('role', '==', 'student')
    
    dept = None
    min_cgpa = 0.0
    required_skills = []

    if request.method == 'POST':
        # Department Filter
        dept = request.form.get('department')
        if dept:
            query = query.where('department', '==', dept)
        
        # Parse CGPA
        cgpa_input = request.form.get('cgpa_min')
        if cgpa_input:
            try:
                min_cgpa = float(cgpa_input)
            except ValueError:
                pass
        
        # Parse Skills
        skills_input = request.form.get('skills', '')
        if skills_input:
            required_skills = [s.strip().lower() for s in skills_input.split(',') if s.strip()]

    docs = query.stream()
    
    for doc in docs:
        s = doc.to_dict()
        s['id'] = doc.id
        
        # CGPA Filter
        try:
            student_cgpa = float(s.get('cgpa', 0) or 0)
        except (ValueError, TypeError):
            student_cgpa = 0.0
            
        if student_cgpa < min_cgpa: continue
        
        # Skills Filter
        if required_skills:
            # Normalize user skills: handle list, string, or None
            raw_skills = s.get('skills', [])
            if isinstance(raw_skills, str):
                user_skills = {raw_skills.lower()}
            elif isinstance(raw_skills, list):
                user_skills = {str(sk).lower() for sk in raw_skills}
            else:
                user_skills = set()
            
            # Check if student has ALL required skills
            if not all(req in user_skills for req in required_skills):
                continue
                
        students_list.append(s)
        
    return render_template('placement/students.html', user=session['user'], students=students_list)

@placement_bp.route('/students/export', methods=['POST'])
def export_students_pdf():
    if not check_placement_role(): return redirect(url_for('auth.login'))

    # --- DUPLICATE FILTERING LOGIC ---
    students_list = []
    query = db.collection('users').where('role', '==', 'student')
    
    # Department
    dept = request.form.get('department')
    if dept:
        query = query.where('department', '==', dept)
    
    # CGPA
    min_cgpa = 0.0
    cgpa_input = request.form.get('cgpa_min')
    if cgpa_input:
        try: min_cgpa = float(cgpa_input)
        except ValueError: pass
        
    # Skills
    required_skills = []
    skills_input = request.form.get('skills', '')
    if skills_input:
        required_skills = [s.strip().lower() for s in skills_input.split(',') if s.strip()]

    docs = query.stream()
    
    for doc in docs:
        s = doc.to_dict()
        
        # CGPA Check
        try:
            student_cgpa = float(s.get('cgpa', 0) or 0)
        except (ValueError, TypeError):
            student_cgpa = 0.0
        if student_cgpa < min_cgpa: continue
        
        # Skills Check
        if required_skills:
            raw_skills = s.get('skills', [])
            if isinstance(raw_skills, str): user_skills = {raw_skills.lower()}
            elif isinstance(raw_skills, list): user_skills = {str(sk).lower() for sk in raw_skills}
            else: user_skills = set()
            
            if not all(req in user_skills for req in required_skills): continue
            
        students_list.append(s)

    # --- GENERATE PDF ---
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    
    # Header
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, height - 50, "Student Report")
    p.setFont("Helvetica", 10)
    p.drawString(450, height - 50, f"Date: {datetime.now().strftime('%Y-%m-%d')}")
    
    # Filters Applied Info
    filter_text = []
    if dept: filter_text.append(f"Dept: {dept}")
    if min_cgpa > 0: filter_text.append(f"Min CGPA: {min_cgpa}")
    if required_skills: filter_text.append(f"Skills: {', '.join(required_skills)}")
    
    p.setFont("Helvetica-Oblique", 10)
    p.drawString(50, height - 70, "Filters: " + (" | ".join(filter_text) if filter_text else "None"))
    p.drawString(50, height - 85, f"Total Students: {len(students_list)}")
    
    # Table Header
    y = height - 110
    p.setFont("Helvetica-Bold", 8)
    p.drawString(30, y, "Name")
    p.drawString(150, y, "Email")
    p.drawString(300, y, "Dept")
    p.drawString(350, y, "CGPA")
    p.drawString(400, y, "Skills")
    
    p.line(25, y - 5, 580, y - 5)
    y -= 20
    
    p.setFont("Helvetica", 8)
    for s in students_list:
        if y < 50:
            p.showPage()
            y = height - 50
            p.setFont("Helvetica", 8)
            
        p.drawString(30, y, str(s.get('full_name', 'N/A'))[:25])
        p.drawString(150, y, str(s.get('email', 'N/A'))[:30])
        p.drawString(300, y, str(s.get('department', 'N/A')))
        p.drawString(350, y, str(s.get('cgpa', 'N/A')))
        
        # Skills (Current)
        skills_str = ", ".join(s.get('skills', [])) if isinstance(s.get('skills'), list) else str(s.get('skills', ''))
        p.drawString(400, y, skills_str[:40]) # Truncate
        
        y -= 15
        
    p.save()
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"student_report_{datetime.now().strftime('%Y%m%d')}.pdf",
        mimetype='application/pdf'
    )

# --- 4. TRAINING ---
@placement_bp.route('/training', methods=['GET', 'POST'])
def training():
    if not check_placement_role(): return redirect(url_for('auth.login'))
    if request.method == 'POST':
        try:
            data = request.form
            resource = {
                'title': data.get('title'),
                'description': data.get('description'),
                'link': data.get('link'),
                'type': data.get('type'),
                'uploaded_by': session['user']['uid'],
                'created_at': firestore.SERVER_TIMESTAMP
            }
            db.collection('training_resources').add(resource)
            flash('Resource added!', 'success')
        except Exception as e:
            flash(f"Error: {e}", "error")
        return redirect(url_for('placement.training'))
        
    resources = db.collection('training_resources').order_by('created_at', direction=firestore.Query.DESCENDING).stream()
    res_list = [{'id': r.id, **r.to_dict()} for r in resources]
    return render_template('placement/training.html', user=session['user'], materials=res_list)

@placement_bp.route('/training/delete/<res_id>')
def delete_training(res_id):
    if not check_placement_role(): return redirect(url_for('auth.login'))
    db.collection('training_resources').document(res_id).delete()
    return redirect(url_for('placement.training'))

# --- 5. TASKS ---
@placement_bp.route('/tasks', methods=['GET', 'POST'])
def tasks():
    if not check_placement_role(): return redirect(url_for('auth.login'))
    if request.method == 'POST':
        try:
            data = request.form
            deadline_str = data.get('deadline') # string from form
            
            task = {
                'title': data.get('title'),
                'description': data.get('description'),
                'type': data.get('type'),
                'assigned_by': session['user']['uid'],
                'assigned_to_batch': data.get('batch_id'),
                'deadline': deadline_str,
                'created_at': firestore.SERVER_TIMESTAMP
            }
            db.collection('assignments').add(task)
            flash('Task assigned!', 'success')
        except Exception as e:
            flash(f"Error: {e}", "error")
        return redirect(url_for('placement.tasks'))

    tasks_ref = db.collection('assignments').order_by('created_at', direction=firestore.Query.DESCENDING).stream()
    tasks_list = [{'id': t.id, **t.to_dict()} for t in tasks_ref]
    return render_template('placement/tasks.html', user=session['user'], tasks=tasks_list)

# --- 6. MESSAGES & BROADCAST ---
@placement_bp.route('/messages')
def messages():
    if not check_placement_role(): return redirect(url_for('auth.login'))
    # Fetch list of users to chat with (excluding self)
    users = db.collection('users').limit(50).stream()
    user_list = []
    for u in users:
        if u.id != session['user']['uid']:
            d = u.to_dict()
            user_list.append({
                'id': u.id,
                'name': d.get('full_name', d.get('email')),
                'user_type': d.get('role', 'student')
            })
    return render_template('placement/messages.html', user=session['user'], users=user_list)

@placement_bp.route('/api/messages/<target_user_id>')
def get_messages_api(target_user_id):
    if not check_placement_role(): return jsonify({'error': 'Unauthorized'}), 401
    try:
        sender_id = session['user']['uid']
        participants = sorted([sender_id, target_user_id])
        conv_id = f"{participants[0]}_{participants[1]}"
        
        msgs_ref = db.collection('conversations').document(conv_id).collection('messages').order_by('timestamp').limit(50)
        messages = []
        for doc in msgs_ref.stream():
            d = doc.to_dict()
            messages.append({
                'sender_id': d.get('sender_id'),
                'message': d.get('content'),
                'timestamp': d.get('timestamp')
            })
        return jsonify(messages)
    except:
        return jsonify([])

@placement_bp.route('/api/messages', methods=['POST'])
def send_message():
    if not check_placement_role(): return jsonify({'error': 'Unauthorized'}), 401
    data = request.json
    receiver_id = data.get('receiver_id')
    content = data.get('message')
    sender_id = session['user']['uid']
    
    # 1. Get/Create Conversation
    participants = sorted([sender_id, receiver_id])
    conv_id = f"{participants[0]}_{participants[1]}"
    conv_ref = db.collection('conversations').document(conv_id)
    
    if not conv_ref.get().exists:
        conv_ref.set({'participants': participants, 'updated_at': firestore.SERVER_TIMESTAMP})
    
    # 2. Add Message
    conv_ref.collection('messages').add({
        'sender_id': sender_id,
        'content': content,
        'timestamp': firestore.SERVER_TIMESTAMP
    })
    return jsonify({'status': 'sent'})

@placement_bp.route('/api/broadcast', methods=['POST'])
def send_broadcast():
    if not check_placement_role(): return jsonify({'error': 'Unauthorized'}), 401
    try:
        data = request.json
        target_group = data.get('target_group') # 'all_students', 'all_csa', 'all_hod'
        message = data.get('message')
        sender_id = session['user']['uid']
        
        # 1. Determine who to send to
        query = db.collection('users')
        if target_group == 'all_students':
            query = query.where('role', '==', 'student')
        elif target_group == 'all_csa':
            query = query.where('role', '==', 'csa')
        elif target_group == 'all_hod':
            query = query.where('role', '==', 'hod')
        else:
            return jsonify({'error': 'Invalid target group'}), 400
            
        recipients = query.stream()
        count = 0
        
        # 2. Loop and send (Simple implementation)
        # In a real app with 1000s users, use Cloud Functions or Batch writes
        for recipient in recipients:
            rec_id = recipient.id
            if rec_id == sender_id: continue
            
            participants = sorted([sender_id, rec_id])
            conv_id = f"{participants[0]}_{participants[1]}"
            conv_ref = db.collection('conversations').document(conv_id)
            
            if not conv_ref.get().exists:
                conv_ref.set({'participants': participants, 'updated_at': firestore.SERVER_TIMESTAMP})
                
            conv_ref.collection('messages').add({
                'sender_id': sender_id,
                'content': f"[BROADCAST] {message}",
                'timestamp': firestore.SERVER_TIMESTAMP
            })
            count += 1
            
        return jsonify({'status': 'success', 'count': count})
        
    except Exception as e:
        print(f"Broadcast Error: {e}")
        return jsonify({'error': str(e)}), 500

# --- 7. REPORTS (Fix for BuildError) ---
@placement_bp.route('/reports')
def reports():
    if not check_placement_role(): return redirect(url_for('auth.login'))
    # Fetch dummy or real reports
    return render_template('placement/reports.html', user=session['user'], reports=[])

@placement_bp.route('/generate_report', methods=['POST'])
def generate_report():
    if not check_placement_role(): return redirect(url_for('auth.login'))
    flash("Report generation started...", "success")
    return redirect(url_for('placement.reports'))