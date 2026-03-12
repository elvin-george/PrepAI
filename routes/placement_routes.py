from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, send_file, current_app, Response
from firebase_admin import firestore
from datetime import datetime
import io
import os
import csv
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

placement_bp = Blueprint('placement', __name__)
db = firestore.client()

# --- HELPER: Strict Role Check ---
def check_placement_role():
    if 'user' not in session: return False
    # Added 'csa' so they can also access monitoring tools
    return session['user'].get('role') in ['placement', 'placement_officer', 'admin', 'csa']

# =====================================================
# 1. DASHBOARD
# =====================================================
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
            # Check for 'placed' status OR the boolean flag
            if data.get('placement_status') == 'placed' or data.get('is_placed') == True:
                placed_students += 1
        
        # Calculate Rate
        placement_rate = 0
        if students_count > 0:
            placement_rate = int((placed_students / students_count) * 100)

        # 3. Pending Actions
        pending_actions = 0
        recent_applications = [] 
        
        return render_template('placement/dashboard.html', 
                             user=session['user'], 
                             drives_count=drives_count, 
                             students_count=students_count,
                             placement_rate=placement_rate,
                             placed_students=placed_students,
                             pending_actions=pending_actions,
                             recent_applications=recent_applications)
                             
    except Exception as e:
        print(f"Dashboard Error: {e}")
        return render_template('placement/dashboard.html', user=session['user'], drives_count=0, students_count=0, placement_rate=0, placed_students=0, pending_actions=0)

# =====================================================
# 2. DRIVES MANAGEMENT
# =====================================================
@placement_bp.route('/placements/report', methods=['GET'])
def report_placement():
    """Render the form for students to report their own placement"""
    if 'user' not in session: return redirect(url_for('auth.login'))
    
    # Check for existing placement record
    status = None
    record = None
    
    try:
        query = db.collection('placements').where('student_id', '==', session['user']['uid']).stream()
        for doc in query:
            record = doc.to_dict()
            status = record.get('status')
            break
    except Exception as e:
        print(f"Error checking placement status: {e}")
        
    return render_template('placement/report_placement.html', user=session['user'], existing_record=record, status=status)
@placement_bp.route('/drives', methods=['GET', 'POST'])
def drives():
    if not check_placement_role(): return redirect(url_for('auth.login'))

    if request.method == 'POST':
        try:
            data = request.form
            deadline_val = data.get('deadline')
            
            deadline_ts = deadline_val
            try:
                if deadline_val:
                    deadline_ts = datetime.strptime(deadline_val, '%Y-%m-%d')
            except:
                pass

            new_drive = {
                'company_name': data.get('company_name'),
                'role_title': data.get('position'),
                'package': data.get('package'),
                'description': data.get('description'),
                'deadline': deadline_ts,
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

    # Fetch Drives
    drives_ref = db.collection('placement_drives').stream()
    drives_list = []
    for d in drives_ref:
        doc = d.to_dict()
        doc['id'] = d.id
        apps_ref = d.reference.collection('applicants').get()
        doc['applicant_count'] = len(apps_ref)
        drives_list.append(doc)
    
    def get_sort_key(d):
        ts = d.get('created_at')
        if ts:
            return ts.replace(tzinfo=None) if ts.tzinfo else ts
        return datetime.min 
        
    drives_list.sort(key=get_sort_key, reverse=True)

    return render_template('placement/drives.html', user=session['user'], drives=drives_list)

@placement_bp.route('/drives/<drive_id>')
def drive_details(drive_id):
    if not check_placement_role(): return redirect(url_for('auth.login'))
    
    drive_ref = db.collection('placement_drives').document(drive_id)
    drive = drive_ref.get().to_dict()
    if not drive:
        flash("Drive not found", "error")
        return redirect(url_for('placement.drives'))
    drive['id'] = drive_id
    
    applicants = []
    apps_ref = drive_ref.collection('applicants').stream()
    for doc in apps_ref:
        app_data = doc.to_dict()
        student_id = doc.id
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
    if not check_placement_role(): return redirect(url_for('auth.login'))
    
    drive_ref = db.collection('placement_drives').document(drive_id)
    
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
    
    deadline_str = request.form.get('deadline')
    if deadline_str:
        try:
           update_data['deadline'] = datetime.strptime(deadline_str, '%Y-%m-%d')
        except ValueError:
           pass

    drive_ref.update(update_data)
    flash('Drive updated successfully.', 'success')
    return redirect(url_for('placement.drives'))

@placement_bp.route('/drives/export/<drive_id>')
def export_drive_pdf(drive_id):
    if not check_placement_role(): return redirect(url_for('auth.login'))

    drive_ref = db.collection('placement_drives').document(drive_id)
    drive = drive_ref.get()
    
    if not drive.exists: return "Drive not found", 404
    drive_data = drive.to_dict()
    
    applicants = []
    apps_ref = drive_ref.reference.collection('applicants').stream()
    for doc in apps_ref:
        data = doc.to_dict()
        student = db.collection('users').document(doc.id).get().to_dict() or {}
        applicants.append({
            'name': student.get('full_name', 'Unknown'),
            'email': student.get('email', 'N/A'),
            'cgpa': student.get('cgpa', 'N/A'),
            'status': data.get('status', 'applied')
        })
    
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, height - 50, f"Drive Report: {drive_data.get('company_name', 'N/A')}")
    p.setFont("Helvetica", 12)
    p.drawString(50, height - 70, f"Role: {drive_data.get('role_title', 'N/A')}")
    p.drawString(50, height - 90, f"Total Applicants: {len(applicants)}")
    
    y = height - 120
    p.setFont("Helvetica-Bold", 10)
    p.drawString(50, y, "Student Name")
    p.drawString(250, y, "CGPA")
    p.drawString(350, y, "Status")
    p.line(40, y - 5, 560, y - 5)
    
    y -= 25
    p.setFont("Helvetica", 10)
    
    for app in applicants:
        if y < 50:
            p.showPage()
            y = height - 50
        p.drawString(50, y, str(app['name']))
        p.drawString(250, y, str(app['cgpa']))
        p.drawString(350, y, str(app['status']).title())
        y -= 20
        
    p.save()
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"{drive_data.get('company_name')}_report.pdf",
        mimetype='application/pdf'
    )

@placement_bp.route('/api/applications/<drive_id>')
def get_drive_applicants(drive_id):
    if not check_placement_role(): return jsonify({'error': 'Unauthorized'}), 401
    try:
        applicants_ref = db.collection('placement_drives').document(drive_id).collection('applicants').stream()
        results = []
        for doc in applicants_ref:
            data = doc.to_dict()
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

# =====================================================
# 3. STUDENT FILTER & EXPORT
# =====================================================
@placement_bp.route('/students', methods=['GET', 'POST'])
def students():
    if not check_placement_role(): return redirect(url_for('auth.login'))
    
    students_list = []
    query = db.collection('users').where('role', '==', 'student')
    
    dept = request.form.get('department')
    if request.method == 'POST' and dept:
        query = query.where('department', '==', dept)
    
    min_cgpa = 0.0
    required_skills = []
    
    if request.method == 'POST':
        try: min_cgpa = float(request.form.get('cgpa_min', 0))
        except: pass
        
        s_in = request.form.get('skills', '')
        if s_in: required_skills = [s.strip().lower() for s in s_in.split(',') if s.strip()]

    docs = query.stream()
    
    for doc in docs:
        s = doc.to_dict()
        s['id'] = doc.id
        
        try: scgpa = float(s.get('cgpa', 0) or 0)
        except: scgpa = 0.0
        
        if scgpa < min_cgpa: continue
        
        if required_skills:
            raw = s.get('skills', [])
            uskills = set()
            if isinstance(raw, list): uskills = {str(k).lower() for k in raw}
            if not all(req in uskills for req in required_skills): continue
            
        students_list.append(s)
        
    return render_template('placement/students.html', user=session['user'], students=students_list)

@placement_bp.route('/students/export', methods=['POST'])
def export_students_pdf():
    # Placeholder: In a real app, reuse the filter logic from 'students' route
    # and generate a PDF/CSV of the filtered list.
    flash("Export feature coming soon.", "info")
    return redirect(url_for('placement.students'))

# =====================================================
# 4. MANUAL PLACEMENT ENTRY & REPORTING (NEW)
# =====================================================

@placement_bp.route('/placements/add', methods=['POST'])
def add_manual_placement():
    """Manually mark a student as placed or submit a request"""
    if 'user' not in session: return redirect(url_for('auth.login'))
    
    try:
        user_role = session['user']['role']
        student_id = request.form.get('student_id')
        company = request.form.get('company')
        role = request.form.get('role')
        ctc = request.form.get('ctc')
        date = request.form.get('date')
        offer_link = request.form.get('offer_link') # New field
        
        # Determine status based on who is adding
        # If student adds it -> pending
        # If staff adds it -> verified
        initial_status = 'verified' if user_role in ['placement_officer', 'admin', 'csa'] else 'pending'
        
        # 1. Add to Placements Collection
        student_ref = db.collection('users').document(student_id)
        student_data = student_ref.get().to_dict()
        
        # check if already exists to prevent duplicates or allow updates
        existing_query = db.collection('placements').where('student_id', '==', student_id).stream()
        existing_doc = None
        for doc in existing_query:
            existing_doc = doc
            break
            
        placement_data = {
            'student_id': student_id,
            'student_name': student_data.get('full_name', 'Unknown'),
            'batch_id': student_data.get('batch_id'),
            'company': company,
            'role': role,
            'ctc': ctc,
            'placed_date': date,
            'offer_link': offer_link,
            'added_by': user_role,
            'status': initial_status,
            'updated_at': firestore.SERVER_TIMESTAMP
        }

        if existing_doc:
            # Update existing manual entry (re-submission)
            if existing_doc.to_dict().get('status') == 'rejected' or user_role in ['placement_officer', 'admin']:
                 db.collection('placements').document(existing_doc.id).update(placement_data)
                 flash("Placement record updated.", "success")
            else:
                 flash("You already have a pending placement record.", "warning")
                 return redirect(url_for('placement.report_placement'))
        else:
            # New entry
            placement_data['created_at'] = firestore.SERVER_TIMESTAMP
            db.collection('placements').add(placement_data)
            flash("Placement record submitted for verification.", "success")
        
        # 2. Update User Profile Status ONLY if verified
        if initial_status == 'verified':
            student_ref.update({
                'placement_status': 'placed',
                'is_placed': True
            })
            flash(f"Successfully marked {student_data.get('full_name')} as placed!", "success")
        
    except Exception as e:
        flash(f"Error adding placement: {e}", "error")
        
    return redirect(url_for('placement.report_placement') if user_role == 'student' else request.referrer)

@placement_bp.route('/placements/approve/<placement_id>')
def approve_placement(placement_id):
    if not check_placement_role() or session['user']['role'] == 'student': return "Unauthorized", 403
    
    try:
        p_ref = db.collection('placements').document(placement_id)
        p_data = p_ref.get().to_dict()
        
        if not p_data: return "Record not found", 404
        
        # Update Placement Record
        p_ref.update({'status': 'verified'})
        
        # Update Student Profile
        db.collection('users').document(p_data['student_id']).update({
            'placement_status': 'placed',
            'is_placed': True
        })
        
        flash("Placement approved successfully.", "success")
    except Exception as e:
        flash(f"Error approving: {e}", "error")
        
    return redirect(request.referrer)

@placement_bp.route('/placements/reject/<placement_id>')
def reject_placement(placement_id):
    if not check_placement_role() or session['user']['role'] == 'student': return "Unauthorized", 403
    
    try:
        db.collection('placements').document(placement_id).update({'status': 'rejected'})
        flash("Placement record rejected.", "info")
    except Exception as e:
        flash(f"Error rejecting: {e}", "error")
        
    return redirect(request.referrer)

@placement_bp.route('/placements/export_csv')
def export_placement_csv():
    """Export list of verified placed students"""
    if not check_placement_role(): return "Unauthorized", 403
    
    # Logic: CSA sees only their batch, Officer sees all
    # Export ONLY verified records
    query = db.collection('placements').where('status', '==', 'verified')
    
    if session['user']['role'] == 'csa':
        my_batch = session['user'].get('batch_id')
        if my_batch: query = query.where('batch_id', '==', my_batch)
            
    placements = query.stream()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Student Name', 'Company', 'Role', 'CTC', 'Date', 'Offer Link'])
    
    for p in placements:
        d = p.to_dict()
        writer.writerow([d.get('student_name'), d.get('company'), d.get('role'), d.get('ctc'), d.get('placed_date'), d.get('offer_link', 'N/A')])
        
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=placement_report.csv"}
    )

# =====================================================
# 5. TRAINING & TASKS
# =====================================================
@placement_bp.route('/training', methods=['GET', 'POST'])
def training():
    if not check_placement_role(): return redirect(url_for('auth.login'))
    if request.method == 'POST':
        db.collection('training_resources').add({
            'title': request.form.get('title'),
            'description': request.form.get('description'),
            'link': request.form.get('link'),
            'type': request.form.get('type'),
            'uploaded_by': session['user']['uid'],
            'created_at': firestore.SERVER_TIMESTAMP
        })
        flash('Resource added!', 'success')
        return redirect(url_for('placement.training'))
        
    resources = db.collection('training_resources').order_by('created_at', direction=firestore.Query.DESCENDING).stream()
    res_list = [{'id': r.id, **r.to_dict()} for r in resources]
    return render_template('placement/training.html', user=session['user'], materials=res_list)

@placement_bp.route('/training/delete/<res_id>')
def delete_training(res_id):
    if not check_placement_role(): return redirect(url_for('auth.login'))
    db.collection('training_resources').document(res_id).delete()
    return redirect(url_for('placement.training'))

@placement_bp.route('/tasks', methods=['GET', 'POST'])
def tasks():
    if not check_placement_role(): return redirect(url_for('auth.login'))
    
    if request.method == 'POST':
        db.collection('assignments').add({
            'title': request.form.get('title'),
            'description': request.form.get('description'),
            'type': request.form.get('type'),
            'assigned_by': session['user']['uid'],
            'assigned_to_batch': request.form.get('batch_id'),
            'deadline': request.form.get('deadline'),
            'status': 'active',
            'created_at': firestore.SERVER_TIMESTAMP
        })
        flash('Task assigned!', 'success')
        return redirect(url_for('placement.tasks'))

    uid = session['user']['uid']
    tasks_list = []
    
    try:
        tasks_ref = db.collection('assignments')\
            .where('assigned_by', '==', uid)\
            .order_by('created_at', direction=firestore.Query.DESCENDING).stream()
        tasks_list = [{'id': t.id, **t.to_dict()} for t in tasks_ref]
    except Exception:
        # Fallback sorting
        tasks_ref = db.collection('assignments').where('assigned_by', '==', uid).stream()
        tasks_list = [{'id': t.id, **t.to_dict()} for t in tasks_ref]
        tasks_list.sort(key=lambda x: x.get('created_at', datetime.min) if x.get('created_at') else datetime.min, reverse=True)

    batches = []
    for b in db.collection('batches').stream():
        batches.append({'id': b.id, **b.to_dict()})

    return render_template('placement/tasks.html', user=session['user'], tasks=tasks_list, batches=batches)

@placement_bp.route('/tasks/edit/<task_id>', methods=['POST'])
def edit_task(task_id):
    if not check_placement_role(): return redirect(url_for('auth.login'))
    try:
        task_ref = db.collection('assignments').document(task_id)
        task = task_ref.get()
        if not task.exists:
            flash("Task not found.", "error")
            return redirect(url_for('placement.tasks'))
            
        task_data = task.to_dict()
        creator = task_data.get('assigned_by') or task_data.get('created_by')
        if creator != session['user']['uid']:
            flash("You can only edit tasks you created.", "error")
            return redirect(url_for('placement.tasks'))

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
    return redirect(url_for('placement.tasks'))

@placement_bp.route('/tasks/submissions/<task_id>')
def view_submissions(task_id):
    if not check_placement_role(): return redirect(url_for('auth.login'))
    try:
        task_ref = db.collection('assignments').document(task_id).get()
        if not task_ref.exists: return redirect(url_for('placement.tasks'))
        task_data = task_ref.to_dict()
        
        students_ref = db.collection('users').where('batch_id', '==', task_data.get('assigned_to_batch')).where('role', '==', 'student').stream()
        students_data = []
        
        for s in students_ref:
            s_dict = s.to_dict()
            sub_ref = db.collection('assignments').document(task_id).collection('submissions').document(s.id).get()
            
            status = 'pending'
            file_url = '#'
            submitted_at = None
            if sub_ref.exists:
                sub_data = sub_ref.to_dict()
                status = 'submitted'
                file_url = sub_data.get('file_url') or sub_data.get('link') or sub_data.get('submission_link') or '#'
                submitted_at = sub_data.get('submitted_at')

            students_data.append({
                'name': s_dict.get('full_name', 'Unknown'),
                'email': s_dict.get('email', 'N/A'),
                'status': status,
                'file_url': file_url,
                'submitted_at': submitted_at
            })
        return render_template('placement/task_submissions.html', user=session['user'], task=task_data, students=students_data)
    except Exception as e:
        flash("An error occurred loading submissions.", "error")
        return redirect(url_for('placement.tasks'))

# =====================================================
# 6. MESSAGES (Universal)
# =====================================================
@placement_bp.route('/messages')
def messages():
    if not check_placement_role(): return redirect(url_for('auth.login'))
    current_uid = session['user']['uid']
    all_users = db.collection('users').stream()
    users_list = []
    for u in all_users:
        if u.id != current_uid:
            d = u.to_dict()
            users_list.append({
                'id': u.id,
                'name': d.get('full_name', d.get('email')),
                'user_type': d.get('role', 'user').replace('_', ' ').title()
            })
    users_list.sort(key=lambda x: x['name'])
    return render_template('placement/messages.html', user=session['user'], users=users_list)

@placement_bp.route('/api/messages/<target_user_id>')
def get_messages_api(target_user_id):
    if not check_placement_role(): return jsonify({'error': 'Unauthorized'}), 401
    try:
        sender_id = session['user']['uid']
        participants = sorted([sender_id, target_user_id])
        conv_id = f"{participants[0]}_{participants[1]}"
        
        msgs = db.collection('conversations').document(conv_id).collection('messages')\
                 .order_by('timestamp').limit(50).stream()
        
        data = [{'sender_id': m.to_dict().get('sender_id'), 
                 'message': m.to_dict().get('content'), 
                 'timestamp': m.to_dict().get('timestamp')} for m in msgs]
        return jsonify(data)
    except: return jsonify([])

@placement_bp.route('/api/messages', methods=['POST'])
def send_message():
    if not check_placement_role(): return jsonify({'error': 'Unauthorized'}), 401
    data = request.json
    receiver_id = data.get('receiver_id')
    sender_id = session['user']['uid']
    participants = sorted([sender_id, receiver_id])
    conv_id = f"{participants[0]}_{participants[1]}"
    
    conv_ref = db.collection('conversations').document(conv_id)
    if not conv_ref.get().exists:
        conv_ref.set({'participants': participants, 'updated_at': firestore.SERVER_TIMESTAMP})
        
    conv_ref.collection('messages').add({
        'sender_id': sender_id, 'content': data.get('message'), 'timestamp': firestore.SERVER_TIMESTAMP
    })
    return jsonify({'status': 'sent'})

@placement_bp.route('/api/broadcast', methods=['POST'])
def send_broadcast():
    # Placeholder for broadcast functionality
    return jsonify({'status': 'success', 'count': 0})

# --- VIEW PLACED STUDENTS (Universal Route for CSA & Officer) ---
@placement_bp.route('/placements/view')
def view_placements():
    if not check_placement_role(): return redirect(url_for('auth.login'))
    
    role = session['user']['role']
    query = db.collection('placements')
    
    # 1. Filter Logic
    # 1. Filter Logic
    if role == 'csa':
        # CSA only sees their batch(es)
        user_id = session['user']['uid']
        # Fetch batches from DB to be sure (reusing logic from csa_routes conceptually)
        batch_ids = []
        
        # 1. Direct Fetch
        batches_query = db.collection('batches').where('csa_id', '==', user_id).stream()
        for b in batches_query: batch_ids.append(b.id)
        
        # 2. Managed IDs from profile
        if not batch_ids:
             csa_doc = db.collection('users').document(user_id).get()
             if csa_doc.exists:
                 batch_ids = csa_doc.to_dict().get('managed_batch_ids', [])
        
        if batch_ids:
            # Firestore 'in' query supports max 10
            # proper way is to use 'in' for chunks or just filter in python if list is small
            # For reported issue, we assume 1 or small number of batches
            query = query.where('batch_id', 'in', batch_ids[:10])
        else:
            # No batches assigned? View empty
            return render_template('staff/placement_list.html', user=session['user'], placements=[], role=role)
            
    # Placement Officer sees ALL (no filter added)
    
    # 2. Fetch Data
    placements_ref = query.stream()
    placements_list = []
    
    for p in placements_ref:
        data = p.to_dict()
        data['id'] = p.id  # Critical for approve/reject links
        placements_list.append(data)
        
    # 3. Render the SHARED template
    return render_template('staff/placement_list.html', placements=placements_list, role=role)

# =====================================================
# 7. REPORTS & ANALYTICS
# =====================================================
@placement_bp.route('/reports')
def reports():
    if not check_placement_role(): return redirect(url_for('auth.login'))
    reports_ref = db.collection('reports').where('generated_by', '==', session['user']['uid']).stream()
    reports_list = [{'id': r.id, **r.to_dict()} for r in reports_ref]
    reports_list.sort(key=lambda x: x.get('created_at') if x.get('created_at') else datetime.min, reverse=True)
    return render_template('placement/reports.html', user=session['user'], reports=reports_list)

@placement_bp.route('/generate_report', methods=['POST'])
def generate_report():
    if not check_placement_role(): return redirect(url_for('auth.login'))
    try:
        report_type = request.form.get('type')
        timestamp = datetime.now()
        filename = f"report_{report_type}_{timestamp.strftime('%Y%m%d_%H%M%S')}.pdf"
        reports_dir = os.path.join(current_app.static_folder, 'reports')
        os.makedirs(reports_dir, exist_ok=True)
        filepath = os.path.join(reports_dir, filename)
        
        c = canvas.Canvas(filepath)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(50, 800, "PrepAI - Analytics Report")
        c.setFont("Helvetica", 12)
        c.drawString(50, 770, f"Title: {report_type.replace('_', ' ').title()}")
        c.drawString(50, 750, f"Date: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
        
        c.drawString(50, 680, "Summary:")
        if report_type == 'placement_stats':
            c.drawString(50, 660, "Active Drives: " + str(len(list(db.collection('placement_drives').where('status', '==', 'active').stream()))))
            c.drawString(50, 640, "Total Students: " + str(len(list(db.collection('users').where('role', '==', 'student').stream()))))
        elif report_type == 'task_completion':
            c.drawString(50, 660, "Active Tasks: " + str(len(list(db.collection('assignments').stream()))))
            
        c.save()
        
        db.collection('reports').add({
            'type': report_type,
            'title': f"{report_type.replace('_', ' ').title()} - {timestamp.strftime('%Y-%m-%d')}",
            'generated_by': session['user']['uid'],
            'status': 'ready',
            'download_url': url_for('static', filename=f'reports/{filename}'),
            'created_at': firestore.SERVER_TIMESTAMP
        })
        flash('Report generated successfully!', 'success')
    except Exception as e:
        flash(f"Error generating report: {e}", "error")
        
    return redirect(url_for('placement.reports'))