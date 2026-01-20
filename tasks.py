from firebase_admin import firestore
from datetime import datetime, timedelta
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
import io
import os

# --- EMAIL CONFIG ---
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 587
SENDER_EMAIL = 'prepai.demo@gmail.com' 
SENDER_PASSWORD = 'boot bzmw owsc cjnx' 

def generate_inactive_pdf(inactive_list):
    """Generates PDF 1: Simply lists students who are inactive."""
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, 750, "⚠️ Inactive Students Report (>7 Days)")
    p.setFont("Helvetica", 10)
    p.drawString(50, 735, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    p.line(50, 730, 550, 730)
    
    y = 700
    if not inactive_list:
        p.drawString(50, y, "Great news! No inactive students found.")
    else:
        p.setFont("Helvetica-Bold", 10)
        p.drawString(50, y, "Student Name")
        p.drawString(300, y, "Last Active")
        y -= 20
        p.setFont("Helvetica", 10)
        
        for student in inactive_list:
            p.drawString(50, y, f"{student['name']} ({student['email']})")
            p.drawString(300, y, str(student['last_active']))
            y -= 15
            if y < 50:
                p.showPage()
                y = 750
    p.save()
    buffer.seek(0)
    return buffer

def generate_missed_tasks_pdf(task_data):
    """Generates PDF 2: Groups defaulters by the specific task they missed."""
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, 750, "📋 Missed Assignments Report")
    p.setFont("Helvetica", 10)
    p.drawString(50, 735, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    p.line(50, 730, 550, 730)
    
    y = 700
    if not task_data:
        p.drawString(50, y, "All tasks submitted on time!")
    else:
        for task in task_data:
            # Task Header
            p.setFont("Helvetica-Bold", 12)
            p.setFillColorRGB(0, 0, 0.5) # Dark Blue
            p.drawString(50, y, f"Task: {task['title']}")
            p.setFont("Helvetica", 10)
            p.setFillColorRGB(0, 0, 0)
            p.drawString(50, y-15, f"Deadline: {task['deadline']} | Missing Submissions: {len(task['defaulters'])}")
            y -= 35
            
            # List Students for this task
            p.setFillColorRGB(0.6, 0, 0) # Red
            for student_name in task['defaulters']:
                p.drawString(70, y, f"• {student_name}")
                y -= 15
                if y < 50:
                    p.showPage()
                    y = 750
            
            y -= 15 # Spacing between tasks
            if y < 80:
                p.showPage()
                y = 750

    p.save()
    buffer.seek(0)
    return buffer

def send_lazy_alerts_job(app):
    with app.app_context():
        db = firestore.client()
        
        # --- 1. ANTI-SPAM CHECK ---
        status_ref = db.collection('system_stats').document('lazy_alert_status')
        status_doc = status_ref.get()
        if status_doc.exists:
            last_run = status_doc.to_dict().get('last_run_at')
            if last_run:
                try:
                    last_run_dt = last_run.replace(tzinfo=None)
                    hours_passed = (datetime.now() - last_run_dt).total_seconds() / 3600
                    if hours_passed < 24:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] ⏳ Skipping. Sent {int(hours_passed)}h ago.")
                        return
                except: pass

        print(f"[{datetime.now()}] ⏰ Collecting Data for Reports...")

        # --- 2. DATA COLLECTION ---
        students = list(db.collection('users').where('role', '==', 'student').stream())
        all_tasks = list(db.collection('assignments').stream())
        
        # List 1: Inactive Students
        inactive_list = []
        threshold = datetime.now() - timedelta(days=7)
        
        for s in students:
            data = s.to_dict()
            last_active = data.get('last_active')
            is_inactive = False
            
            if not last_active:
                is_inactive = True
            else:
                try:
                    if hasattr(last_active, 'replace'):
                        if last_active.replace(tzinfo=None) < threshold: is_inactive = True
                    elif isinstance(last_active, str):
                        pass # Handle string parsing if needed
                except: pass
            
            if is_inactive:
                inactive_list.append({
                    'name': data.get('full_name', 'Unknown'),
                    'email': data.get('email', 'N/A'),
                    'last_active': last_active if last_active else "Never"
                })

        # List 2: Missed Tasks
        missed_tasks_report = []
        now = datetime.now()
        
        for t in all_tasks:
            t_data = t.to_dict()
            tid = t.id
            title = t_data.get('title', 'Untitled Task')
            deadline = t_data.get('deadline')
            batch_id = t_data.get('assigned_to_batch')
            
            # Check if deadline passed
            d_date = None
            if deadline:
                try:
                    if isinstance(deadline, str): d_date = datetime.strptime(deadline, '%Y-%m-%d')
                    elif hasattr(deadline, 'replace'): d_date = deadline.replace(tzinfo=None)
                except: pass
            
            if d_date and d_date < now:
                # Find students in this batch who DID NOT submit
                batch_students = [s for s in students if s.to_dict().get('batch_id') == batch_id]
                missing_students = []
                
                for bs in batch_students:
                    sub_ref = db.collection('assignments').document(tid).collection('submissions').document(bs.id).get()
                    if not sub_ref.exists:
                        missing_students.append(bs.to_dict().get('full_name', 'Unknown'))
                
                if missing_students:
                    missed_tasks_report.append({
                        'title': title,
                        'deadline': deadline,
                        'defaulters': missing_students
                    })

        # --- 3. GENERATE TWO PDFS ---
        pdf1 = generate_inactive_pdf(inactive_list)
        pdf2 = generate_missed_tasks_pdf(missed_tasks_report)
        
        # --- 4. SEND EMAIL ---
        if not inactive_list and not missed_tasks_report:
            print("✅ Everyone is active and up to date. No email needed.")
            status_ref.set({'last_run_at': datetime.now()})
            return

        staff_emails = []
        staff_users = db.collection('users').where('role', 'in', ['csa', 'hod']).stream()
        for u in staff_users:
            email = u.to_dict().get('email')
            if email: staff_emails.append(email)
            
        if staff_emails:
            try:
                msg = MIMEMultipart()
                msg['From'] = SENDER_EMAIL
                msg['To'] = ", ".join(staff_emails)
                msg['Subject'] = f"⚠️ PrepAI Daily Alert: {len(inactive_list)} Inactive, {len(missed_tasks_report)} Tasks Missed"
                
                body = (f"Hello Staff,\n\n"
                        f"The system has detected issues requiring your attention.\n"
                        f"1. Inactive Students: {len(inactive_list)}\n"
                        f"2. Tasks with Missing Submissions: {len(missed_tasks_report)}\n\n"
                        f"Please find the TWO separate detailed reports attached.")
                msg.attach(MIMEText(body, 'plain'))
                
                # ATTACHMENT 1
                part1 = MIMEApplication(pdf1.read(), Name="inactive_students.pdf")
                part1['Content-Disposition'] = 'attachment; filename="inactive_students.pdf"'
                msg.attach(part1)

                # ATTACHMENT 2
                part2 = MIMEApplication(pdf2.read(), Name="missed_tasks.pdf")
                part2['Content-Disposition'] = 'attachment; filename="missed_tasks.pdf"'
                msg.attach(part2)
                
                server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
                server.starttls()
                server.login(SENDER_EMAIL, SENDER_PASSWORD)
                server.sendmail(SENDER_EMAIL, staff_emails, msg.as_string())
                server.quit()
                
                print(f"📧 EMAIL SENT SUCCESSFULY to {len(staff_emails)} staff with 2 attachments.")
                status_ref.set({'last_run_at': datetime.now()})
                
            except Exception as e:
                print(f"❌ Email Failed: {e}")
        else:
            print("⚠️ No staff emails found.")
            status_ref.set({'last_run_at': datetime.now()})