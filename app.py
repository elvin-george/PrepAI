from flask import Flask, render_template, redirect, url_for, session, request
import firebase_admin
from firebase_admin import credentials
from dotenv import load_dotenv
import os
from flask_apscheduler import APScheduler 

# Import the background task logic
try:
    from tasks import send_lazy_alerts_job 
except ImportError:
    print("Warning: tasks.py not found. Automated alerts will not run.")
    send_lazy_alerts_job = None

# 1. Load Environment Variables
load_dotenv()

app = Flask(__name__)

# 2. Configuration
app.secret_key = os.getenv("SECRET_KEY", "dev_secret_key_123") 
app.config['SESSION_TYPE'] = 'filesystem' 
app.config['PERMANENT_SESSION_LIFETIME'] = 3600 

# --- CRITICAL FIX: Set Debug explicitly here ---
# This ensures the scheduler check below works correctly
app.debug = True 

# --- SCHEDULER CONFIGURATION ---
app.config['JOBS'] = [
    {
        'id': 'lazy_alert_job',
        'func': lambda: send_lazy_alerts_job(app) if send_lazy_alerts_job else None,
        'trigger': 'interval',
        'seconds': 60 
    }
]

# 3. Firebase Initialization
if not firebase_admin._apps:
    try:
        cred = credentials.Certificate("firebase-key.json")
        firebase_admin.initialize_app(cred)
        print("Firebase Initialized Successfully.")
    except Exception as e:
        print(f"Firebase Init Error: {e}")

# 4. Initialize & Start Scheduler (FIXED LOGIC)
scheduler = APScheduler()
scheduler.init_app(app)

# LOGIC EXPLANATION:
# 1. not app.debug -> Returns False (because we set app.debug=True above)
# 2. WERKZEUG_RUN_MAIN -> Returns False in Main Process, True in Child Process
# Result: Scheduler ONLY starts in the Child process.
if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    scheduler.start()
    print("⏰ Background Scheduler Started (Single Instance)...")

# 5. GLOBAL SESSION GUARD
@app.before_request
def require_login():
    allowed_routes = [
        'auth.login', 
        'auth.register', 
        'auth.forgot_password', 
        'static'
    ]

    if request.endpoint and request.endpoint not in allowed_routes:
        if 'user' not in session:
            # Avoid redirect loop for API endpoints
            if request.endpoint and 'api' not in request.endpoint:
                print(f"Unauthorized access to {request.endpoint}. Redirecting.")
                return redirect(url_for('auth.login'))

# 6. Register Blueprints
try:
    from routes.auth_routes import auth_bp
    from routes.student_routes import student_bp
    from routes.csa_routes import csa_bp
    from routes.hod_routes import hod_bp
    from routes.placement_routes import placement_bp
    from routes.interview_routes import interview_bp

    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(student_bp, url_prefix='/student')
    app.register_blueprint(csa_bp, url_prefix='/csa')
    app.register_blueprint(hod_bp, url_prefix='/hod')
    app.register_blueprint(placement_bp, url_prefix='/placement')
    app.register_blueprint(interview_bp, url_prefix='/interview')
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"Warning: Blueprints not fully implemented. Error: {e}")

# 7. Base Routes
@app.route('/')
def index():
    if 'user' in session:
        role = session.get('user', {}).get('role', 'student')
        
        if role == 'student':
            return redirect(url_for('student.dashboard'))
        elif role == 'csa':
            return redirect(url_for('csa.dashboard'))
        elif role == 'placement' or role == 'placement_officer':
            return redirect(url_for('placement.dashboard'))
        elif role == 'hod':
            return redirect(url_for('hod.dashboard'))
            
    return redirect(url_for('auth.login'))

# 8. Prevent Browser Caching
@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

if __name__ == '__main__':
    # debug=True is redundant here since we set it above, but safe to keep
    app.run(debug=True, port=4999)