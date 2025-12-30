from flask import Blueprint, render_template

csa_bp = Blueprint('csa', __name__)

@csa_bp.route('/dashboard')
def dashboard():
    return "<h1>CSA Dashboard (Under Construction)</h1>"