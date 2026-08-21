import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app import app, init_db

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
init_db()

application = app
