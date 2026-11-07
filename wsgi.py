"""
WSGI entry point for PythonAnywhere deployment.
Conforms to SYSTEM_DESIGN.md Section 12.
"""

import os
import sys

# Ensure project directory is on sys.path
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

os.environ["FLASK_ENV"] = "production"

from app import app as application

