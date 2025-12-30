from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
import firebase_admin
from firebase_admin import auth, firestore

auth_bp = Blueprint('auth', __name__)
db = firestore.client()

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
            # This ensures the user actually logged in with Google/Firebase
            decoded_token = auth.verify_id_token(id_token)
            uid = decoded_token['uid']
            email = decoded_token['email']

            # 3. Get User Role from Firestore
            # We assume you have a 'users' collection where document ID is the UID
            user_ref = db.collection('users').document(uid)
            user_doc = user_ref.get()

            role = 'student' # Default fallback
            username = email.split('@')[0] # Default fallback name

            if user_doc.exists:
                user_data = user_doc.to_dict()
                role = user_data.get('role', 'student')
                username = user_data.get('name', username)
            else:
                # If user doesn't exist in Firestore yet (first login), create them
                # Note: Usually registration handles this, but this is a safety net
                db.collection('users').document(uid).set({
                    'email': email,
                    'role': 'student',
                    'created_at': firestore.SERVER_TIMESTAMP
                })

            # 4. Set the Flask Session
            session.permanent = True
            session['user'] = {
                'uid': uid,
                'email': email,
                'role': role,
                'name': username
            }

            # 5. Determine Redirect URL based on Role
            if role == 'csa':
                target_url = url_for('csa.dashboard')
            elif role == 'placement':
                target_url = url_for('placement.dashboard')
            elif role == 'hod':   
                target_url = url_for('hod.dashboard')
            else:
                target_url = url_for('student.dashboard')

            return jsonify({'status': 'success', 'redirect_url': target_url})
        except Exception as e:
            print(f"Login Error: {e}")
            return jsonify({'status': 'error', 'message': 'Invalid credentials'}), 401

    # 2. Handle GET request (Show the Login Page)
    return render_template('login.html')

@auth_bp.route('/register')
def register():
    return render_template('register.html')

@auth_bp.route('/forgot-password')
def forgot_password():
    return render_template('forgot_password.html')

@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))