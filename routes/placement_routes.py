from flask import Blueprint, render_template

placement_bp = Blueprint('placement', __name__)

@placement_bp.route('/dashboard')
def dashboard():
    return "<h1>Placement Dashboard (Under Construction)</h1>"