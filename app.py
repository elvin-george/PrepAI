from flask import Flask, render_template, redirect, url_for, session, request
import firebase_admin
from firebase_admin import credentials
from dotenv import load_dotenv
import os

# 1. Load Environment Variables
load_dotenv()

app = Flask(__name__)

# 2. Configuration
app.secret_key = os.getenv("SECRET_KEY", "dev_secret_key_123") 
app.config['SESSION_TYPE'] = 'filesystem' 
app.config['PERMANENT_SESSION_LIFETIME'] = 3600 # Session expires in 1 hour

# 3. Firebase Initialization
if not firebase_admin._apps:
    try:
        cred = credentials.Certificate("firebase-key.json")
        firebase_admin.initialize_app(cred)
        print("Firebase Initialized Successfully.")
    except Exception as e:
        print(f"Firebase Init Error: {e}")

# 4. GLOBAL SESSION GUARD (Security Enforcement)
# This runs before every single request to ensure security
@app.before_request
def require_login():
    # 1. Define routes that are allowed WITHOUT login (Public Access)
    # 'static' allows CSS/JS to load. 'auth.*' allows login/register pages.
    allowed_routes = [
        'auth.login', 
        'auth.register', 
        'auth.forgot_password', 
        'auth.google_login', # If you add Google Auth later
        'static'
    ]

    # 2. Check if the requested endpoint is protected
    # If the endpoint is NOT in the allowed list...
    if request.endpoint and request.endpoint not in allowed_routes:
        # 3. Check if user is logged in
        if 'user' not in session:
            # User is NOT logged in -> Force redirect to Login
            print(f"Unauthorized access attempt to {request.endpoint}. Redirecting to Login.")
            return redirect(url_for('auth.login'))

# 5. Register Blueprints
# Ensure your blueprint files are created in the 'routes' folder
try:
    from routes.auth_routes import auth_bp
    from routes.student_routes import student_bp
    from routes.csa_routes import csa_bp
    from routes.hod_routes import hod_bp
    from routes.placement_routes import placement_bp

    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(student_bp, url_prefix='/student')
    app.register_blueprint(csa_bp, url_prefix='/csa')
    app.register_blueprint(hod_bp, url_prefix='/hod')
    app.register_blueprint(placement_bp, url_prefix='/placement')
except ImportError as e:
    print(f"Warning: Blueprints not fully implemented yet. Error: {e}")

# 6. Base Routes
@app.route('/')
def index():
    if 'user' in session:
        role = session.get('user', {}).get('role', 'student') # Safe get
        
        if role == 'student':
            return redirect(url_for('student.dashboard'))
        elif role == 'csa':
            return redirect(url_for('csa.dashboard'))
        elif role == 'placement':
            return redirect(url_for('placement.dashboard'))
        elif role == 'hod':  # <--- Add this check
            return redirect(url_for('hod.dashboard'))
            
    return redirect(url_for('auth.login'))

# 7. Prevent Browser Caching (Security)
# Ensures that clicking "Back" after logout doesn't show the previous page
@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

if __name__ == '__main__':
    app.run(debug=True, port=4999)