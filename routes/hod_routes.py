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
        batches = list(db.collection('batches').stream())
        batches_count = len(batches)
        
        staff = list(db.collection('users').where('role', '==', 'csa').stream())
        csa_count = len(staff)
        
        students = list(db.collection('users').where('role', '==', 'student').stream())
        students_count = len(students)
        
        # 2. Get Recent Batches for the table
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
        return render_template('hod/dashboard.html', user=session['user'], stats={'batches':0, 'csa':0, 'students':0}, recent_batches=[])

# --- 2. BATCH MANAGEMENT ---
@hod_bp.route('/batches', methods=['GET', 'POST'])
def batches():
    if not check_hod_role(): return redirect(url_for('auth.login'))

    if request.method == 'POST':
        try:
            # 1. Get Form Data
            batch_id = request.form.get('batch_id').strip().lower()
            
            batch_data = {
                'batch_name': request.form.get('batch_name'),
                'department': request.form.get('department'),
                'current_semester': request.form.get('semester'),
                'csa_id': request.form.get('csa_id'),
                'created_at': firestore.SERVER_TIMESTAMP
            }
            
            # 2. Save Batch to Firestore (Merge allows update)
            db.collection('batches').document(batch_id).set(batch_data, merge=True)
            
            # 3. If CSA is assigned, update the CSA's profile
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
    batches_ref = db.collection('batches').order_by('created_at', direction=firestore.Query.DESCENDING).stream()
    batches_list = [{'id': b.id, **b.to_dict()} for b in batches_ref]
    
    # Fetch CSAs for the dropdown
    csa_ref = db.collection('users').where('role', '==', 'csa').stream()
    csa_list = [{'id': u.id, 'name': u.to_dict().get('full_name')} for u in csa_ref]

    return render_template('hod/batches.html', user=session['user'], batches=batches_list, csas=csa_list)

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
            # Get Data
            csa_id = request.form.get('csa_id') # If present, it's an Update
            email = request.form.get('email')
            password = request.form.get('password') 
            name = request.form.get('name')
            dept = request.form.get('department')
            
            if csa_id:
                # --- UPDATE EXISTING STAFF ---
                # 1. Update Firestore
                db.collection('users').document(csa_id).update({
                    'full_name': name,
                    'department': dept,
                    'email': email 
                })
                
                # 2. Update Auth (Name & Email & Password if provided)
                try:
                    auth.update_user(csa_id, email=email, display_name=name)
                    if password and password.strip() and password != "Welcome@123": 
                         auth.update_user(csa_id, password=password)
                except Exception as auth_err:
                    print(f"Auth Update Warning: {auth_err}")

                flash(f'Staff details updated for {name}.', 'success')
                
            else:
                # --- CREATE NEW STAFF ---
                # 1. Create Auth User
                try:
                    user = auth.create_user(email=email, password=password, display_name=name)
                    csa_id = user.uid 
                except Exception as auth_error:
                    flash(f"Error creating login: {auth_error}", "error")
                    return redirect(url_for('hod.staff'))
                
                # 2. Create Firestore Profile
                db.collection('users').document(csa_id).set({
                    'email': email,
                    'full_name': name,
                    'role': 'csa', 
                    'department': dept,
                    'managed_batch_ids': [],
                    'reports_to_hod': session['user']['uid'], 
                    'created_at': firestore.SERVER_TIMESTAMP
                })
                flash(f'CSA Account created for {name}. Share the password.', 'success')
            
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
        db.collection('users').document(csa_id).delete()
        try:
            auth.delete_user(csa_id)
        except Exception as auth_err:
             print(f"Auth Delete Warning: {auth_err}")
        flash('Staff member deleted successfully.', 'success')
    except Exception as e:
        flash(f"Error deleting staff: {e}", "error")
    return redirect(url_for('hod.staff'))

# --- 4. REPORTS MANAGEMENT ---
@hod_bp.route('/reports')
def reports():
    if not check_hod_role(): return redirect(url_for('auth.login'))
    
    # 1. Fetch Batches
    batches_ref = db.collection('batches').stream()
    batches_list = [{'id': b.id, 'name': b.to_dict().get('batch_name')} for b in batches_ref]
    
    # 2. Fetch History
    reports_ref = db.collection('reports').where('generated_by', '==', session['user']['uid']).stream()
    reports_list = [{'id': r.id, **r.to_dict()} for r in reports_ref]
    
    # Sort in memory
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
        
        # --- PDF GENERATION ---
        timestamp = datetime.now()
        timestamp_str = timestamp.strftime('%Y%m%d_%H%M%S')
        report_title = f"{report_type.replace('_', ' ').title()} - {timestamp.strftime('%Y-%m-%d')}"
        filename = f"report_{report_type}_{timestamp_str}.pdf"
        
        # Ensure static/reports directory exists
        reports_dir = os.path.join(current_app.static_folder, 'reports')
        os.makedirs(reports_dir, exist_ok=True)
        
        filepath = os.path.join(reports_dir, filename)
        
        # Create PDF
        c = canvas.Canvas(filepath)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(50, 800, "PrepAI - Department Report")
        c.setFont("Helvetica", 12)
        c.drawString(50, 770, f"Title: {report_title}")
        c.drawString(50, 750, f"Date: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
        c.drawString(50, 730, f"Type: {report_type}")
        c.drawString(50, 710, f"Target: {target_id}")
        c.drawString(50, 680, "Analysis Summary (Placeholder):")
        c.drawString(50, 660, "This document confirms the generation of the requested analytics report.")
        c.save()
        
        download_url = url_for('static', filename=f'reports/{filename}')

        # Save to Firestore
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

# --- 5. MESSAGES & BROADCAST ---
@hod_bp.route('/messages')
def messages():
    if not check_hod_role(): return redirect(url_for('auth.login'))
    
    current_uid = session['user']['uid']
    
    # Fetch ALL users to enable universal messaging (HOD -> PO/Staff/Students)
    all_users = db.collection('users').stream()
    
    users_list = []
    for u in all_users:
        if u.id != current_uid: # Exclude self
            d = u.to_dict()
            raw_role = d.get('role', 'user')
            
            users_list.append({
                'id': u.id,
                'name': d.get('full_name', d.get('email')),
                'user_type': raw_role.replace('_', ' ').title()
            })
            
    users_list.sort(key=lambda x: x['name'])
    
    return render_template('hod/messages.html', user=session['user'], users=users_list)

@hod_bp.route('/api/messages/<target_user_id>')
def get_messages_api(target_user_id):
    if not check_hod_role(): return jsonify({'error': 'Unauthorized'}), 401
    try:
        sender_id = session['user']['uid']
        participants = sorted([sender_id, target_user_id])
        conv_id = f"{participants[0]}_{participants[1]}"
        
        msgs_ref = db.collection('conversations').document(conv_id).collection('messages')\
                     .order_by('timestamp').limit(50)
        
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

@hod_bp.route('/api/messages', methods=['POST'])
def send_message():
    if not check_hod_role(): return jsonify({'error': 'Unauthorized'}), 401
    data = request.json
    receiver_id = data.get('receiver_id')
    content = data.get('message')
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
    return jsonify({'status': 'sent'})

@hod_bp.route('/api/broadcast', methods=['POST'])
def send_broadcast():
    if not check_hod_role(): return jsonify({'error': 'Unauthorized'}), 401
    try:
        data = request.json
        target_group = data.get('target_group') 
        message = data.get('message')
        sender_id = session['user']['uid']
        
        query = db.collection('users')
        if target_group == 'dept_students':
            query = query.where('role', '==', 'student')
        elif target_group == 'dept_csas':
            query = query.where('role', '==', 'csa')
        else:
            return jsonify({'error': 'Invalid target'}), 400
            
        recipients = query.stream()
        count = 0
        
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
                'content': f"[DEPT NOTICE] {message}",
                'timestamp': firestore.SERVER_TIMESTAMP
            })
            count += 1
            
        return jsonify({'status': 'success', 'count': count})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500