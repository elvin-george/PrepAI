import firebase_admin
from firebase_admin import credentials, firestore, auth
from datetime import datetime, timedelta, timezone

# 1. Initialize Firebase
if not firebase_admin._apps:
    cred = credentials.Certificate("firebase-key.json")
    firebase_admin.initialize_app(cred)

db = firestore.client()
print("🔥 Connected to Firebase for Full Initialization (Auth + DB)...")

def create_auth_and_db_user(uid, email, password, name, role, extra_data):
    """
    Creates a user in Firebase Auth AND Firestore with the SAME UID.
    """
    # A. Create in Firebase Authentication (The Login)
    try:
        auth.get_user(uid)
        print(f"   - Auth User '{email}' already exists. Skipping Auth creation.")
    except auth.UserNotFoundError:
        print(f"   - Creating Auth User '{email}' with password '{password}'...")
        auth.create_user(
            uid=uid,
            email=email,
            password=password,
            display_name=name
        )

    # B. Create in Firestore Database (The Profile)
    # Base data every user has
    user_data = {
        'email': email,
        'role': role,
        'full_name': name,
        'profile_image': "",
        'phone_number': "9876543210",
        'created_at': firestore.SERVER_TIMESTAMP
    }
    
    # Merge with role-specific extra data
    user_data.update(extra_data)
    
    # Save to Firestore
    db.collection('users').document(uid).set(user_data)
    print(f"   - Firestore Profile created for {role}: {name}")

def init_database():
    # ==========================================
    # 1. ESTABLISH HIERARCHY (Batches)
    # ==========================================
    batch_id = "mca_2024_2026"
    print(f"1. Setting up Batch: {batch_id}...")
    db.collection('batches').document(batch_id).set({
        'batch_name': "MCA 2024-2026",
        'department': "MCA",
        'current_semester': "S3",
        'csa_id': "demo_csa_1", 
        'student_count': 1,
        'created_at': firestore.SERVER_TIMESTAMP
    })

    # ==========================================
    # 2. CREATE REAL USERS (Auth + DB)
    # ==========================================
    print("2. Creating Users (Password for all: 'password123')...")

    # --- Student ---
    create_auth_and_db_user(
        uid="demo_student_1",
        email="student@prepai.com",
        password="password123",
        name="Elvin Student",
        role="student",
        extra_data={
            'batch_id': batch_id,
            'department': "MCA",
            'current_semester': "S3",
            'is_approved': True,
            'cgpa': 7.5,
            'backlogs': 0,
            'skills': ["Python", "Flask", "React"],
            'resume_url': "https://drive.google.com/dummy-link",
            'resume_text_content': "ELVIN STUDENT\nPython Developer\nSkills: Flask, AI...",
            'placement_status': "seeking",
            'last_active': firestore.SERVER_TIMESTAMP
        }
    )

    # --- CSA (Staff) ---
    create_auth_and_db_user(
        uid="demo_csa_1",
        email="csa@prepai.com",
        password="password123",
        name="Prof. Staff Advisor",
        role="csa",
        extra_data={
            'managed_batch_ids': [batch_id],
            'department': "MCA"
        }
    )

    # --- HOD ---
    create_auth_and_db_user(
        uid="demo_hod_1",
        email="hod@prepai.com",
        password="password123",
        name="Dr. Head of Dept",
        role="hod",
        extra_data={
            'managed_department': "MCA"
        }
    )

    # --- Placement Officer ---
    create_auth_and_db_user(
        uid="demo_po_1",
        email="po@prepai.com",
        password="password123",
        name="Placement Admin",
        role="placement_officer",
        extra_data={
            'access_level': "admin"
        }
    )

    # ==========================================
    # 3. PLACEMENT DRIVES
    # ==========================================
    print("3. creating Placement Drive...")
    drive_id = "drive_ust_global"
    drive_ref = db.collection('placement_drives').document(drive_id)
    drive_ref.set({
        'company_name': "UST Global",
        'role_title': "Software Developer",
        'package': "6 LPA",
        'description': "Hiring for Python/Java developers.",
        'deadline': datetime.now(timezone.utc) + timedelta(days=10),
        'posted_by': "demo_po_1",
        'status': "active",
        'eligibility_criteria': {
            'min_cgpa': 6.0,
            'max_backlogs': 0,
            'allowed_branches': ["MCA", "BTech CS"]
        }
    })
    
    # Applicant
    drive_ref.collection('applicants').document('demo_student_1').set({
        'applied_at': firestore.SERVER_TIMESTAMP,
        'status': "applied",
        'resume_snapshot_url': "https://drive.google.com/dummy-link"
    })

    # ==========================================
    # 4. LAZY ALERT ENGINE (Assignments)
    # ==========================================
    print("4. Creating Lazy Alert Assignments...")
    assignment_id = "task_resume_update"
    assign_ref = db.collection('assignments').document(assignment_id)
    assign_ref.set({
        'title': "Update Resume for UST",
        'description': "Please update your resume text for the upcoming drive.",
        'assigned_by': "demo_csa_1",
        'assigned_to_batch': batch_id,
        'type': "internal_task",
        'reference_id': drive_id,
        'deadline': datetime.now(timezone.utc) + timedelta(days=2),
        'created_at': firestore.SERVER_TIMESTAMP
    })

    # Submission
    assign_ref.collection('submissions').document('demo_student_1').set({
        'status': "submitted",
        'submitted_at': firestore.SERVER_TIMESTAMP,
        'submission_text': "Updated my resume section."
    })

    # ==========================================
    # 5. AI FEATURES
    # ==========================================
    print("5. Seeding AI History...")
    session_ref = db.collection('ai_sessions').document('demo_session_1')
    session_ref.set({
        'user_id': "demo_student_1",
        'module_type': "chatbot",
        'title': "Python Doubts",
        'last_message_preview': "How do lists work?",
        'updated_at': firestore.SERVER_TIMESTAMP
    })
    
    session_ref.collection('messages').add({
        'sender': "user",
        'content': "How do lists work in Python?",
        'timestamp': firestore.SERVER_TIMESTAMP
    })
    session_ref.collection('messages').add({
        'sender': "ai",
        'content': "Lists are mutable sequences...",
        'timestamp': firestore.SERVER_TIMESTAMP
    })

    print("\n✅ Initialization Complete!")
    print("   You can now login with:")
    print("   - student@prepai.com / password123")
    print("   - csa@prepai.com / password123")
    print("   - hod@prepai.com / password123")
    print("   - po@prepai.com / password123")

if __name__ == "__main__":
    init_database()