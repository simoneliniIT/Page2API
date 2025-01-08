import sys
import os

# Add your project directory to the sys.path
path = '/home/YOUR_PYTHONANYWHERE_USERNAME/Page2API'
if path not in sys.path:
    sys.path.append(path)

# Import your Flask app
from app import app as application

# Set environment variables if needed
os.environ['FLASK_ENV'] = 'production' 