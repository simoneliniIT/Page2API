from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for
from bs4 import BeautifulSoup
import requests
from anthropic import Anthropic, HUMAN_PROMPT, AI_PROMPT
import os
import json
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import traceback
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_migrate import Migrate, upgrade
from functools import wraps
from sqlalchemy import text
import uuid
import secrets

app = Flask(__name__)

# Use PostgreSQL on Render and SQLite locally
print("\n=== Database Configuration ===")
print(f"Environment variables:")
print(f"- RENDER: {os.environ.get('RENDER')}")
print(f"- DATABASE_URL: {'[SET]' if os.environ.get('DATABASE_URL') else '[NOT SET]'}")
if os.environ.get('DATABASE_URL'):
    print(f"- DATABASE_URL value: {os.environ.get('DATABASE_URL').split('@')[1] if '@' in os.environ.get('DATABASE_URL') else '[MALFORMED]'}")

if os.environ.get('RENDER'):
    print("\nRunning on Render.com")
    database_url = os.environ.get('DATABASE_URL')
    if database_url:
        # Fix potential "postgres://" to "postgresql://" for SQLAlchemy 1.4+
        database_url = database_url.replace('postgres://', 'postgresql://')
        app.config['SQLALCHEMY_DATABASE_URI'] = database_url
        print(f"Using PostgreSQL database: {database_url.split('@')[1] if '@' in database_url else '[MALFORMED]'}")
    else:
        print("WARNING: DATABASE_URL not set on Render, using SQLite as fallback")
        print("Please check your Render.com environment variables configuration")
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///your_database.db'
else:
    print("\nRunning locally")
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///your_database.db'
    print("Using SQLite database for local development")

print("=== End Database Configuration ===\n")

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-here')  # Change this to a secure secret key
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,  # Enable connection health checks
    'pool_recycle': 300,    # Recycle connections every 5 minutes
}

db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    company_name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(512), nullable=True)
    user_type = db.Column(db.String(20), nullable=False)  # 'supplier' or 'distributor'
    distributor_id = db.Column(db.String(36), unique=True)  # UUID for distributors
    products = db.relationship('Product', backref=db.backref('owner', lazy='joined'), lazy='dynamic')
    api_rate_limit = db.Column(db.Integer, default=100)  # Requests per hour
    api_keys = db.relationship('APIKey', backref='user', lazy=True)

    def __init__(self, *args, **kwargs):
        # Set default api_rate_limit if not provided
        if 'api_rate_limit' not in kwargs:
            kwargs['api_rate_limit'] = 100
        super(User, self).__init__(*args, **kwargs)

    @property
    def rate_limit(self):
        # Safely get the rate limit, return default if column doesn't exist
        try:
            return self.api_rate_limit if self.api_rate_limit is not None else 100
        except Exception:
            return 100

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def generate_distributor_id(self):
        if self.user_type == 'distributor' and not self.distributor_id:
            self.distributor_id = str(uuid.uuid4())

class APIKey(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_used_at = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)
    requests_count = db.Column(db.Integer, default=0)

    def __init__(self, user_id, name):
        self.user_id = user_id
        self.name = name
        self.key = secrets.token_urlsafe(32)  # Generate a secure random API key

    def increment_requests(self):
        self.requests_count += 1
        self.last_used_at = datetime.utcnow()
        db.session.commit()

    def check_rate_limit(self):
        """Check if the user has exceeded their rate limit in the past hour"""
        if not self.last_used_at:
            return True
            
        user = User.query.get(self.user_id)
        if not user:
            return False
            
        # Get requests in the last hour
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        if self.last_used_at < one_hour_ago:
            self.requests_count = 0
            db.session.commit()
            return True
            
        return self.requests_count < user.rate_limit

@login_manager.user_loader
def load_user(id):
    return User.query.get(int(id))

# Update Product model to include user relationship
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.JSON, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(100), nullable=True)  # New column for product category
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    api_spec = db.Column(db.Text)
    reward_percentage = db.Column(db.Float, default=0.0)  # Affiliate reward percentage

# Add Template model after the Product model
class Template(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Create a directory for storing saved products if it doesn't exist
PRODUCTS_DIR = 'saved_products'
os.makedirs(PRODUCTS_DIR, exist_ok=True)

# Add admin required decorator
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        print(f"\nAdmin access check - User: {current_user.email if current_user.is_authenticated else 'Not authenticated'}")  # Debug log
        if not current_user.is_authenticated:
            print("Access denied - Not authenticated")  # Debug log
            return redirect(url_for('login'))
        if current_user.user_type != 'admin':
            print(f"Access denied - Not admin (type: {current_user.user_type})")  # Debug log
            return 'Access denied', 403
        print("Admin access granted")  # Debug log
        return f(*args, **kwargs)
    return decorated_function

def require_api_key(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        if not api_key:
            return jsonify({'error': 'API key is required'}), 401
            
        api_key_obj = APIKey.query.filter_by(key=api_key, is_active=True).first()
        if not api_key_obj:
            return jsonify({'error': 'Invalid API key'}), 401
            
        if not api_key_obj.check_rate_limit():
            return jsonify({'error': 'Rate limit exceeded'}), 429
            
        api_key_obj.increment_requests()
        return f(*args, **kwargs)
    return decorated_function

[... rest of the file remains unchanged ...]
