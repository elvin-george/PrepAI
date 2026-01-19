from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, current_app
from firebase_admin import firestore, auth
from datetime import datetime
from reportlab.pdfgen import canvas
import os

hod_bp = Blueprint('hod', __name__)
db = firestore.client()

# --- HELPER: Strict Role Check ---
def check_hod_role():
    """Ensures only HODs can access these routes."""
    if 'user' not in session: return False
    return session['user'].get('role') == 'hod'

# --- 1. HOD DASHBOARD ---
@hod_bp.route('/dashboard')
def dashboard():
    if not check_hod_role(): return redirect(url_for('auth.login'))
    
    try:
        # 1. Fetch Real-time Stats
        # Fetching streams is okay for small-medium apps. For 10k+ users, use aggregation queries.
        batches = list(db.collection('batches').stream())
        batches_count = len(batches)
        
        staff = list(db.collection('users').where('role', '==', 'csa').stream())
        csa_count = len(staff)
        
        students = list(db.collection('users').where('role', '==', 'student').stream())
        students_count = len(students)
        
        # 2. Get Recent Batches for the table
        # Sort by creation time (descending) and take top 5
        # We handle cases where 'created_at' might be missing
        batches_data = []
        for b in batches:
            d = b.to_dict()
            d['id'] = b.id
            batches_data.append(d)
            
        # Sort logic: Recent first
        recent_batches = sorted(
            batches_data, 
            key=lambda x: x.get('created_at', datetime.min) if x.get('created_at') else datetime.min, 
            reverse=True
        )[:5]

        return render_template('hod/dashboard.html', 
                             user=session['user'],
                             stats={
                                 'batches': batches_count, 
                                 'csa': csa_count, 
                                 'students': students_count
                             },
                             recent_batches=recent_batches)
                             
    except Exception as e:
        print(f"HOD Dashboard Error: {e}")
        # Return empty dashboard on error to prevent crash
        return render_template('hod/dashboard.html', user=session['user'], stats={'batches':0, 'csa':0, 'students':0}, recent_batches=[])

# --- 2. BATCH MANAGEMENT ---
@hod_bp.route('/batches', methods=['GET', 'POST'])
def batches():
    if not check_hod_role(): return redirect(url_for('auth.login'))

    if request.method == 'POST':
        try:
            # 1. Get Form Data
            # 'batch_id' is manual input (e.g., mca_2024) to make it easy to read
            batch_id = request.form.get('batch_id').strip().lower()
            
            batch_data = {
                'batch_name': request.form.get('batch_name'), # "MCA 2024-2026"
                'department': request.form.get('department'),
                'current_semester': request.form.get('semester'),
                'csa_id': request.form.get('csa_id'), # Optional: Link to CSA
                'created_at': firestore.SERVER_TIMESTAMP
            }
            
            # 2. Save Batch to Firestore
            # using .set() with merge=True acts as both Create and Update
            db.collection('batches').document(batch_id).set(batch_data, merge=True)
            
            # 3. If CSA is assigned, update the CSA's profile too
            # This ensures the CSA knows which batches they own
            if batch_data['csa_id']:
                csa_ref = db.collection('users').document(batch_data['csa_id'])
                csa_ref.update({
                    'managed_batch_ids': firestore.ArrayUnion([batch_id])
                })
                
            flash(f'Batch "{batch_data["batch_name"]}" saved successfully.', 'success')
            
        except Exception as e:
            flash(f"Error saving batch: {e}", "error")
        
        return redirect(url_for('hod.batches'))

    # GET Request: Show List
    # 1. Fetch Batches
    batches_ref = db.collection('batches').order_by('created_at', direction=firestore.Query.DESCENDING).stream()
    batches_list = [{'id': b.id, **b.to_dict()} for b in batches_ref]
    
    # 2. Fetch CSAs (for the dropdown selection)
    csa_ref = db.collection('users').where('role', '==', 'csa').stream()
    csa_list = [{'id': u.id, 'name': u.to_dict().get('full_name')} for u in csa_ref]

    return render_template('hod/batches.html', user=session['user'], batches=batches_list, csas=csa_list)

# --- 4. REPORTS MANAGEMENT ---
@hod_bp.route('/reports')
def reports():
    if not check_hod_role(): return redirect(url_for('auth.login'))
    
    # 1. Fetch Batches for the dropdown filter
    batches_ref = db.collection('batches').stream()
    batches_list = [{'id': b.id, 'name': b.to_dict().get('batch_name')} for b in batches_ref]
    
    # 2. Fetch History of Generated Reports
    reports_ref = db.collection('reports')\
                    .where('generated_by', '==', session['user']['uid']).stream()
    
    reports_list = [{'id': r.id, **r.to_dict()} for r in reports_ref]
    # Sort in memory to avoid "Composite Index Required" error
    reports_list.sort(key=lambda x: x.get('created_at') if x.get('created_at') else datetime.min, reverse=True)
    
    return render_template('hod/reports.html', 
                         user=session['user'], 
                         batches=batches_list,
                         reports=reports_list)

@hod_bp.route('/reports/generate', methods=['POST'])
def generate_report():
    if not check_hod_role(): return redirect(url_for('auth.login'))
    
    try:
        report_type = request.form.get('report_type') 
        target_id = request.form.get('target_id')
        
        # --- PDF GENERATION LOGIC ---
        timestamp = datetime.now()
        timestamp_str = timestamp.strftime('%Y%m%d_%H%M%S')
        report_title = f"{report_type.replace('_', ' ').title()} - {timestamp.strftime('%Y-%m-%d')}"
        filename = f"report_{report_type}_{timestamp_str}.pdf"
        
        # Ensure directory exists using Flask's configured static folder
        reports_dir = os.path.join(current_app.static_folder, 'reports')
        os.makedirs(reports_dir, exist_ok=True)
        
        filepath = os.path.join(reports_dir, filename)
        
        # Generate PDF using ReportLab
        c = canvas.Canvas(filepath)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(50, 800, "PrepAI - Department Report")
        c.setFont("Helvetica", 12)
        c.drawString(50, 770, f"Title: {report_title}")
        c.drawString(50, 750, f"Date: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
        c.drawString(50, 730, f"Type: {report_type}")
        c.drawString(50, 710, f"Target Group ID: {target_id}")
        
        c.drawString(50, 680, "Analysis Summary:")
        c.drawString(50, 660, "This is a generated report placeholer. Actual analytics data to be implemented.")
        c.drawString(50, 640, "No real data queries were executed for this MVP demonstration.")
        
        c.save()
        
        download_url = url_for('static', filename=f'reports/{filename}')

        new_report = {
            'type': report_type,
            'target': target_id,
            'title': report_title,
            'generated_by': session['user']['uid'],
            'status': 'ready',
            'download_url': download_url,
            'created_at': firestore.SERVER_TIMESTAMP
        }
        
        db.collection('reports').add(new_report)
        flash('Report generated successfully!', 'success')
        
    except Exception as e:
        flash(f"Error generating report: {e}", "error")
        print(f"Report Generation Error: {e}")
        
    return redirect(url_for('hod.reports'))



@hod_bp.route('/batches/delete/<batch_id>')
def delete_batch(batch_id):
    if not check_hod_role(): return redirect(url_for('auth.login'))
    
    try:
        db.collection('batches').document(batch_id).delete()
        flash('Batch deleted successfully.', 'success')
    except Exception as e:
        flash(f"Error deleting batch: {e}", "error")
        
    return redirect(url_for('hod.batches'))

# --- 3. STAFF (CSA) MANAGEMENT ---
@hod_bp.route('/staff', methods=['GET', 'POST'])
def staff():
    if not check_hod_role(): return redirect(url_for('auth.login'))

    if request.method == 'POST':
        try:
            # 1. Get Data
            csa_id = request.form.get('csa_id') # Hidden field for updates
            email = request.form.get('email')
            password = request.form.get('password') 
            name = request.form.get('name')
            dept = request.form.get('department')
            
            if csa_id:
                # --- UPDATE EXISTING STAFF ---
                # 1. Update Firestore Profile
                db.collection('users').document(csa_id).update({
                    'full_name': name,
                    'department': dept,
                    # We typically don't update email blindly as it breaks auth link, but for now we'll update metadata
                    'email': email 
                })
                
                # 2. Update Auth Profile (Name & Email)
                try:
                    auth.update_user(csa_id, email=email, display_name=name)
                    # Only update password if a new one is provided and not empty/default placeholder
                    if password and password.strip() and password != "Welcome@123": 
                         auth.update_user(csa_id, password=password)
                except Exception as auth_err:
                    print(f"Auth Update Warning: {auth_err}") # Non-fatal if auth update fails (e.g. email unchanged)

                flash(f'Staff details updated for {name}.', 'success')
                
            else:
                # --- CREATE NEW STAFF ---
                # 1. Create User in Firebase Authentication
                try:
                    user = auth.create_user(email=email, password=password, display_name=name)
                    csa_id = user.uid 
                except Exception as auth_error:
                    flash(f"Error creating login: {auth_error}", "error")
                    return redirect(url_for('hod.staff'))
                
                # 2. Create Profile in Firestore
                db.collection('users').document(csa_id).set({
                    'email': email,
                    'full_name': name,
                    'role': 'csa', 
                    'department': dept,
                    'managed_batch_ids': [],
                    'reports_to_hod': session['user']['uid'], 
                    'created_at': firestore.SERVER_TIMESTAMP
                })
                
                flash(f'CSA Account created for {name}. Share the password with them.', 'success')
            
        except Exception as e:
            flash(f"System Error: {e}", "error")
        
        return redirect(url_for('hod.staff'))

    # GET Request: List all CSAs
    csa_ref = db.collection('users').where('role', '==', 'csa').stream()
    csa_list = [{'id': u.id, **u.to_dict()} for u in csa_ref]
    
    return render_template('hod/staff.html', user=session['user'], staff=csa_list)

@hod_bp.route('/staff/delete/<csa_id>')
def delete_staff(csa_id):
    if not check_hod_role(): return redirect(url_for('auth.login'))
    
    try:
        # 1. Delete from Firestore
        db.collection('users').document(csa_id).delete()
        
        # 2. Delete from Authentication
        try:
            auth.delete_user(csa_id)
        except Exception as auth_err:
             print(f"Auth Delete Warning: {auth_err}") # User might already be deleted in Auth
             
        flash('Staff member deleted successfully.', 'success')
    except Exception as e:
        flash(f"Error deleting staff: {e}", "error")
        
    return redirect(url_for('hod.staff'))