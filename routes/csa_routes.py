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
        
        # 1. Fetch CSA Profile to get assigned batches
        csa_doc = db.collection('users').document(user_id).get()
        csa_data = csa_doc.to_dict()
        managed_batch_ids = csa_data.get('managed_batch_ids', [])
        
        # 2. Fetch Details for these Batches
        my_batches = []
        total_students = 0
        
        if managed_batch_ids:
            # Firestore 'in' query supports up to 10 items
            batches_ref = db.collection('batches').where(firestore.FieldPath.document_id(), 'in', managed_batch_ids).stream()
            for b in batches_ref:
                batch = b.to_dict()
                batch['id'] = b.id
                my_batches.append(batch)
                total_students += batch.get('student_count', 0)
        
        # 3. Calculate "Lazy Alerts" (Inactive > 7 Days) for Dashboard Stats
        threshold_date = datetime.now() - timedelta(days=7)
        pending_alerts = 0
        
        if managed_batch_ids:
            lazy_students = db.collection('users')\
                .where('role', '==', 'student')\
                .where('batch_id', 'in', managed_batch_ids)\
                .where('last_active', '<', threshold_date)\
                .stream()
            pending_alerts = len(list(lazy_students))

        return render_template('csa/dashboard.html', 
                             user=session['user'],
                             batches=my_batches,
                             stats={
                                 'batch_count': len(my_batches),
                                 'total_students': total_students,
                                 'alerts': pending_alerts
                             })
                             
    except Exception as e:
        print(f"CSA Dashboard Error: {e}")
        return render_template('csa/dashboard.html', user=session['user'], batches=[], stats={'batch_count':0, 'total_students':0, 'alerts':0})

# --- 2. VIEW BATCH STUDENTS ---
@csa_bp.route('/batch/<batch_id>')
def view_batch(batch_id):
    if not check_csa_role(): return redirect(url_for('auth.login'))
    
    # 1. Fetch Batch Info
    batch_ref = db.collection('batches').document(batch_id).get()
    batch_data = batch_ref.to_dict()
    batch_data['id'] = batch_id
    
    # 2. Fetch Students
    students_ref = db.collection('users').where('batch_id', '==', batch_id).stream()
    students = []
    
    threshold = datetime.now() - timedelta(days=7)
    
    for s in students_ref:
        data = s.to_dict()
        data['id'] = s.id
        
        # Calculate Lazy Status
        last_active = data.get('last_active')
        data['is_lazy'] = False
        if last_active:
            try:
                # Compare timezone-naive datetimes
                if last_active.replace(tzinfo=None) < threshold:
                    data['is_lazy'] = True
            except: pass
        else:
            data['is_lazy'] = True # Never logged in
            
        students.append(data)
    
    return render_template('csa/batch_view.html', user=session['user'], batch=batch_data, students=students)

# --- 3. GENERATE DEFAULTERS REPORT (PDF) ---
@csa_bp.route('/batch/<batch_id>/defaulters_report')
def generate_defaulters_report(batch_id):
    if not check_csa_role(): return redirect(url_for('auth.login'))
    
    # Logic: Find students inactive > 7 days
    threshold = datetime.now() - timedelta(days=7)
    lazy_students = db.collection('users')\
        .where('batch_id', '==', batch_id)\
        .where('last_active', '<', threshold).stream()
        
    defaulters = [{'name': s.to_dict().get('full_name'), 'email': s.to_dict().get('email')} for s in lazy_students]

    # Generate PDF
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, 750, f"Defaulters Report - Batch {batch_id}")
    p.setFont("Helvetica", 10)
    p.drawString(50, 735, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    
    y = 700
    p.setFont("Helvetica-Bold", 10)
    p.drawString(50, y, "Student Name")
    p.drawString(300, y, "Email")
    y -= 20
    
    p.setFont("Helvetica", 10)
    if not defaulters:
        p.drawString(50, y, "No inactive students found.")
    else:
        for d in defaulters:
            p.drawString(50, y, d['name'])
            p.drawString(300, y, d['email'])
            y -= 20
            if y < 50:
                p.showPage()
                y = 750
            
    p.save()
    buffer.seek(0)
    
    return send_file(buffer, as_attachment=True, download_name=f'defaulters_{batch_id}.pdf', mimetype='application/pdf')