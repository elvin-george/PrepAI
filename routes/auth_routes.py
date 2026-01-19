from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
import firebase_admin
from firebase_admin import auth, firestore

auth_bp = Blueprint('auth', __name__)
db = firestore.client()

# --- LOGIN ROUTE ---
@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    # 1. Handle the JSON POST request from JavaScript
    if request.method == 'POST':
        try:
            data = request.get_json()
            id_token = data.get('idToken')

            if not id_token:
                return jsonify({'status': 'error', 'message': 'No token provided'}), 400

            # 2. Verify the token with Firebase Admin
            try:
                decoded_token = auth.verify_id_token(id_token)
            except Exception as e:
                print(f"Token Verification Failed: {e}")
                return jsonify({'status': 'error', 'message': 'Invalid authentication token'}), 401
                
            uid = decoded_token['uid']
            email = decoded_token['email']

            # 3. Get User Role from Firestore
            user_ref = db.collection('users').document(uid)
            user_doc = user_ref.get()

            role = 'student' # Default fallback
            full_name = email.split('@')[0] # Default fallback name

            if user_doc.exists:
                # EXISTING USER: Fetch role from DB
                user_data = user_doc.to_dict()
                role = user_data.get('role', 'student') 
                full_name = user_data.get('full_name', full_name)
                print(f"DEBUG: Found existing user: {email} | Role: {role}")
            else:
                # NEW USER (First Login via Google/Auth): Auto-detect role for testing
                if 'officer' in email.lower() or 'admin' in email.lower() or 'placement' in email.lower():
                    role = 'placement_officer'
                elif 'hod' in email.lower():
                    role = 'hod'
                elif 'csa' in email.lower() or 'advisor' in email.lower():
                    role = 'csa'
                else:
                    role = 'student'

                print(f"DEBUG: Creating new user: {email} | Auto-assigned Role: {role}")
                
                # Save to Firestore
                db.collection('users').document(uid).set({
                    'email': email,
                    'role': role,
                    'full_name': full_name,
                    'created_at': firestore.SERVER_TIMESTAMP
                })

            # 4. Set the Flask Session
            session.permanent = True
            session['user'] = {
                'uid': uid,
                'email': email,
                'role': role,
                'name': full_name
            }

            # 5. Determine Redirect URL based on Role
            if role == 'csa':
                target_url = url_for('csa.dashboard')
                
            elif role in ['placement', 'placement_officer', 'admin']: 
                target_url = url_for('placement.dashboard')
                
            elif role == 'hod':
                target_url = url_for('hod.dashboard')
                
            else:
                target_url = url_for('student.dashboard')

            return jsonify({'status': 'success', 'redirect_url': target_url})

        except Exception as e:
            print(f"Login System Error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # 2. Handle GET request (Show the Login Page)
    return render_template('login.html')

# --- REGISTER ROUTE (UPDATED) ---
@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    # POST: Handle Form Submission
    if request.method == 'POST':
        try:
            # 1. Get Form Data
            name = request.form.get('name')
            email = request.form.get('email')
            password = request.form.get('password')
            role = request.form.get('role')
            batch_id = request.form.get('batch_id') # For Students only

            # 2. Create User in Firebase Authentication
            try:
                user = auth.create_user(
                    email=email,
                    password=password,
                    display_name=name
                )
            except Exception as auth_error:
                # Handle "Email already exists"
                return render_template('register.html', error=str(auth_error), batches=[])

            # 3. Create User Document in Firestore
            user_data = {
                'email': email,
                'full_name': name,
                'role': role,
                'created_at': firestore.SERVER_TIMESTAMP
            }

            # If Student, save the Batch ID
            if role == 'student' and batch_id:
                user_data['batch_id'] = batch_id
                user_data['is_approved'] = False # Default to unapproved until CSA checks
                user_data['department'] = 'MCA' # Default or fetch from batch logic
            
            # If HOD, set managed department (Logic simplified)
            if role == 'hod':
                user_data['managed_department'] = 'MCA'

            db.collection('users').document(user.uid).set(user_data)

            # 4. Redirect to Login
            return redirect(url_for('auth.login'))

        except Exception as e:
            print(f"Registration Error: {e}")
            return render_template('register.html', error="An error occurred during registration.")

    # GET: Show Register Page with Batches
    try:
        # Fetch batches for the dropdown
        batches_ref = db.collection('batches').stream()
        batches = [{'id': b.id, 'name': b.to_dict().get('batch_name')} for b in batches_ref]
    except Exception as e:
        print(f"Error fetching batches: {e}")
        batches = []

    return render_template('register.html', batches=batches)

# --- FORGOT PASSWORD ---
@auth_bp.route('/forgot-password')
def forgot_password():
    return render_template('forgot_password.html')

# --- LOGOUT ---
@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))