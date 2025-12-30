from flask import Blueprint, render_template

hod_bp = Blueprint('hod', __name__)

@hod_bp.route('/dashboard')
def dashboard():
    # In the future, we will fetch department stats, staff lists, etc. here
    return render_template('hod/dashboard.html')