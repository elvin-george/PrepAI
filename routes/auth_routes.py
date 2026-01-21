from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash
import firebase_admin
from firebase_admin import auth, firestore
import requests
import os

auth_bp = Blueprint('auth', __name__)
db = firestore.client()

# --- CONFIGURATION (SECURE) ---
# Reads from your .env file.
# Make sure your .env has: FIREBASE_API_KEY=AIzaSy...
FIREBASE_WEB_API_KEY = os.getenv("FIREBASE_API_KEY")

if not FIREBASE_WEB_API_KEY:
    print("⚠️ WARNING: FIREBASE_API_KEY not found in environment variables. Login will fail.")

# --- LOGIN ROUTE ---
@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    # Handle GET request (Show Page)
    if request.method == 'GET':
        return render_template('login.html')

    # Handle POST request (Login Logic)
    try:
        email = None
        uid = None
        role = 'student' # Default
        full_name = 'User'

        # --- PATH A: FORM LOGIN (Email & Password) ---
        if request.form.get('email') and request.form.get('password'):
            email = request.form.get('email')
            password = request.form.get('password')

            # 1. Verify Password via Google Identity REST API
            request_url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_WEB_API_KEY}"
            payload = {"email": email, "password": password, "returnSecureToken": True}
            
            r = requests.post(request_url, json=payload)
            data = r.json()

            # Handle Errors
            if 'error' in data:
                error_msg = data['error']['message']
                if error_msg == "INVALID_PASSWORD":
                    flash("Incorrect password.", "error")
                elif error_msg == "EMAIL_NOT_FOUND":
                    flash("No account found with this email.", "error")
                else:
                    flash(f"Login failed: {error_msg}", "error")
                return redirect(url_for('auth.login'))

            # Success! Get UID
            uid = data['localId']

        # --- PATH B: JSON/TOKEN LOGIN (Google Sign-In) ---
        elif request.is_json:
            data = request.get_json()
            id_token = data.get('idToken')
            if not id_token:
                return jsonify({'status': 'error', 'message': 'No token provided'}), 400
            
            decoded_token = auth.verify_id_token(id_token)
            uid = decoded_token['uid']
            email = decoded_token['email']

        else:
            return render_template('login.html')

        # --- FETCH USER ROLE FROM FIRESTORE ---
        user_ref = db.collection('users').document(uid)
        user_doc = user_ref.get()

        if user_doc.exists:
            user_data = user_doc.to_dict()
            role = user_data.get('role', 'student')
            full_name = user_data.get('full_name', email.split('@')[0])
        else:
            full_name = email.split('@')[0]

        # --- SAVE SESSION ---
        session.permanent = True
        session['user'] = {
            'uid': uid,
            'email': email,
            'role': role,
            'full_name': full_name
        }

        # --- REDIRECT BASED ON ROLE ---
        if request.is_json:
            target_url = url_for('student.dashboard')
            if role == 'csa': target_url = url_for('csa.dashboard')
            elif role == 'hod': target_url = url_for('hod.dashboard')
            elif role in ['placement', 'placement_officer']: target_url = url_for('placement.dashboard')
            return jsonify({'status': 'success', 'redirect_url': target_url})
        else:
            if role == 'csa': return redirect(url_for('csa.dashboard'))
            elif role == 'hod': return redirect(url_for('hod.dashboard'))
            elif role in ['placement', 'placement_officer']: return redirect(url_for('placement.dashboard'))
            else: return redirect(url_for('student.dashboard'))

    except Exception as e:
        print(f"Login System Error: {e}")
        if request.is_json:
            return jsonify({'status': 'error', 'message': str(e)}), 500
        else:
            flash(f"System Error: {e}", "error")
            return redirect(url_for('auth.login'))

# --- REGISTER ROUTE ---
@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        try:
            name = request.form.get('name')
            email = request.form.get('email')
            password = request.form.get('password')
            role = request.form.get('role')
            batch_id = request.form.get('batch_id')

            # Create User in Auth
            try:
                user = auth.create_user(
                    email=email,
                    password=password,
                    display_name=name
                )
            except Exception as auth_error:
                return render_template('register.html', error=str(auth_error), batches=[])

            # Create User in Firestore
            user_data = {
                'email': email,
                'full_name': name,
                'role': role,
                'created_at': firestore.SERVER_TIMESTAMP
            }

            if role == 'student' and batch_id:
                user_data['batch_id'] = batch_id
                user_data['is_approved'] = False
                user_data['department'] = 'MCA'
            
            if role == 'hod':
                user_data['managed_department'] = 'MCA'

            db.collection('users').document(user.uid).set(user_data)

            flash("Registration successful! Please login.", "success")
            return redirect(url_for('auth.login'))

        except Exception as e:
            print(f"Registration Error: {e}")
            return render_template('register.html', error="An error occurred.")

    try:
        batches_ref = db.collection('batches').stream()
        batches = [{'id': b.id, 'name': b.to_dict().get('batch_name')} for b in batches_ref]
    except:
        batches = []

    return render_template('register.html', batches=batches)

# --- FORGOT PASSWORD ROUTE ---
@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        try:
            # Generate password reset link via Firebase Admin SDK
            link = auth.generate_password_reset_link(email)
            
            # In a real production app, you would send this link via email using smtplib
            # For this demo/development, we will print it to the console so you can click it.
            print(f"🔗 RESET LINK FOR {email}: {link}")
            
            flash(f"Password reset link sent to {email}. (Check server console for link)", "success")
            return redirect(url_for('auth.login'))
            
        except Exception as e:
            flash(f"Error: {str(e)}", "error")
            
    return render_template('forgot_password.html')

# --- LOGOUT ---
@auth_bp.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for('auth.login'))