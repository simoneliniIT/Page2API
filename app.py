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
            
        return self.requests_count < user.api_rate_limit

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

@app.route('/')
def index():
    if not current_user.is_authenticated:
        return render_template('index.html')
    
    # Redirect based on user type
    if current_user.user_type == 'admin':
        return redirect(url_for('admin'))
    elif current_user.user_type == 'supplier':
        return redirect(url_for('share'))
    elif current_user.user_type == 'distributor':
        return redirect(url_for('consumer'))
    else:
        # Fallback in case of unknown user type
        return redirect(url_for('login'))

@app.route('/convert', methods=['POST'])
def convert():
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
            
        product_url = data.get('product_url')
        conversion_type = data.get('conversion_type', 'structured_json')
        
        if not product_url:
            return jsonify({'error': 'No product URL provided'}), 400
            
        print(f"Converting URL: {product_url}")
        print(f"Conversion type: {conversion_type}")
        
        # Check if ANTHROPIC_API_KEY is set
        if not os.environ.get('ANTHROPIC_API_KEY'):
            return jsonify({'error': 'ANTHROPIC_API_KEY environment variable not set'}), 500

        print(f"\nStarting conversion - Type: {conversion_type}")
        print(f"Product URL: {product_url}")
        
        # Get Claude's response based on conversion type
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            return jsonify({'error': 'ANTHROPIC_API_KEY environment variable not set'}), 500
            
        client = Anthropic(api_key=api_key)
        
        if conversion_type == 'custom_api':
            # Custom API conversion
            api_spec = data.get('api_spec')
            if not api_spec:
                return jsonify({'error': 'API specification is required for custom API conversion'}), 400
                
            print(f"Using custom API spec: {api_spec[:100]}...")
            prompt = f"""Given this product URL:
{product_url}

And this target API specification:
{api_spec}

IMPORTANT: This URL might contain multiple products. Please analyze the page and convert ALL products found.
For each product, create a separate entry in the output array.

Please convert ALL product data to match the target API specification format.
Return ONLY an array of converted products in a clear, structured format, with no additional text or explanations.
Each item in the array should follow the API specification format."""

        elif conversion_type == 'template':
            # Template-based conversion
            template_id = data.get('template_id')
            if not template_id:
                return jsonify({'error': 'Template ID is required when using a template'}), 400
                
            # Get template URL
            template = Template.query.get(template_id)
            if not template:
                return jsonify({'error': 'Selected template not found'}), 404
                
            print(f"Using template: {template.name} ({template.url})")
            prompt = f"""Given this product URL:
{product_url}

Please convert ALL products found on this page to match the format of this template product:
{template.url}

IMPORTANT: This URL might contain multiple products. Please analyze the page and convert ALL products found.
For each product, create a separate entry in the output array.

Important guidelines:
1. Match the exact structure and field names from the template
2. Include ONLY factual information found on the product page
3. DO NOT invent or assume any data not explicitly shown
4. Use the same data types as the template for each field

Return ONLY an array of converted products in the same format as the template, with no additional text or explanations."""

        else:  # structured_json
            # Default structured JSON conversion
            print("Using structured JSON conversion")
            prompt = f"""Given this product URL:
{product_url}

IMPORTANT: This URL might contain multiple products. Please analyze the page and convert ALL products found.
For each product, create a separate entry in the output array.

Please create a well-structured JSON representation of ALL product data found on this page.
Important guidelines:
1. Include ONLY factual information found on the page
2. DO NOT invent or assume any data not explicitly shown
3. Use clear, descriptive field names
4. Organize data hierarchically where appropriate
5. Include all relevant product details (specs, features, pricing, etc.)
6. Format numbers and dates consistently
7. Use proper JSON data types (strings, numbers, booleans, arrays)

Return ONLY an array of JSON objects, with no additional text or explanations.
Each object in the array should represent one product."""

        message = client.messages.create(
            model="claude-3-5-sonnet-latest",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": prompt
            }]
        )
        
        response_content = message.content[0].text.strip()
        
        try:
            # Parse the response as JSON
            products = json.loads(response_content)
            
            # Ensure the response is an array
            if not isinstance(products, list):
                products = [products]
                
            # Save results to a file
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'conversion_{timestamp}.json'
            filepath = os.path.join(PRODUCTS_DIR, filename)
            
            os.makedirs(PRODUCTS_DIR, exist_ok=True)
            
            with open(filepath, 'w') as f:
                json.dump({
                    'timestamp': datetime.now().isoformat(),
                    'api_spec': data.get('api_spec', ''),
                    'products': products,
                    'has_errors': False
                }, f, indent=2)
            
            return jsonify({
                'content': json.dumps(products),
                'feed_spec': data.get('api_spec', ''),
                'result_id': filename,
                'product_count': len(products)
            })
            
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON response: {str(e)}")
            return jsonify({'error': 'Invalid JSON response from conversion'}), 500
            
    except Exception as e:
        print(f"Error during conversion: {str(e)}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/save-product', methods=['POST'])
@login_required
def save_product():
    try:
        print("Starting save_product...")
        print(f"Request JSON: {request.json}")
        
        if not request.json:
            return jsonify({'error': 'No JSON data received'}), 400
            
        content = request.json.get('content')
        api_spec = request.json.get('api_spec', '')
        reward_percentage = float(request.json.get('reward_percentage', 0))
        
        # Validate reward percentage
        if not 0 <= reward_percentage <= 100:
            return jsonify({'error': 'Reward percentage must be between 0 and 100'}), 400
        
        if not content:
            return jsonify({'error': 'No content provided'}), 400
        
        print(f"Content type: {type(content)}")
        print(f"Content: {json.dumps(content)[:100]}...")  # Convert to string for logging
        print(f"API Spec: {api_spec[:100] if api_spec else ''}")
        print(f"Current user: {current_user.email} (ID: {current_user.id})")
        
        # Ask Claude to generate a user-friendly name and category
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            return jsonify({'error': 'ANTHROPIC_API_KEY environment variable not set'}), 500
            
        client = Anthropic(api_key=api_key)
        prompt = f"""Based on this product data:
{json.dumps(content)}

Please provide two things:
1. A short, user-friendly name for this product (maximum 50 characters)
2. A category that best describes this product, keeping in mind this is for travel. Pick only one option between "Tours & Activities", "Accommodation", "Transportation", "Events & Shows", "Car Rental", "Vacations", "Airport Transfers". If none of these makes sense, use "Other"

Return your response in this exact format:
Name: [product name]
Category: [category]"""

        message = client.messages.create(
            model="claude-3-5-haiku-latest",
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": prompt
            }]
        )
        
        response = message.content[0].text.strip()
        name_line = [line for line in response.split('\n') if line.startswith('Name:')][0]
        category_line = [line for line in response.split('\n') if line.startswith('Category:')][0]
        
        product_name = name_line.replace('Name:', '').strip()
        product_category = category_line.replace('Category:', '').strip()
        
        print(f"Generated product name: {product_name}")
        print(f"Generated category: {product_category}")
        
        # Create new Product in database
        product = Product(
            content=content,
            name=product_name,
            category=product_category,
            user_id=current_user.id,
            api_spec=api_spec,
            reward_percentage=reward_percentage
        )
        
        print("Adding product to database session...")
        db.session.add(product)
        print("Committing to database...")
        db.session.commit()
        print("Product saved successfully!")
            
        return jsonify({
            'message': 'Product saved successfully',
            'name': product_name,
            'category': product_category
        }), 200
    except Exception as e:
        print(f"Error saving product: {str(e)}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/saved-products')
@login_required
def saved_products():
    try:
        if current_user.user_type == 'supplier':
            # Show only user's products
            products = Product.query.filter_by(user_id=current_user.id).order_by(Product.timestamp.desc()).all()
            print(f"Found {len(products)} products for supplier {current_user.email}")
        else:
            # Show all products for distributors
            products = Product.query.order_by(Product.timestamp.desc()).all()
            print(f"Found {len(products)} products for distributor {current_user.email}")
        
        # Debug print each product
        for product in products:
            print(f"Product: {product.name}, User: {product.user_id}, Content: {type(product.content)}")
            
        return render_template('saved_products.html', products=products)
    except Exception as e:
        print(f"Error in saved_products route: {str(e)}")
        traceback.print_exc()
        return render_template('saved_products.html', products=[])

@app.route('/download-product/<product_id>')
def download_product(product_id):
    try:
        filepath = os.path.join(PRODUCTS_DIR, secure_filename(f'{product_id}.json'))
        return send_file(filepath, as_attachment=True)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download-all')
def download_all():
    try:
        # Create a zip file containing all products
        import zipfile
        from io import BytesIO
        
        memory_file = BytesIO()
        with zipfile.ZipFile(memory_file, 'w') as zf:
            for filename in os.listdir(PRODUCTS_DIR):
                if filename.endswith('.json'):
                    filepath = os.path.join(PRODUCTS_DIR, filename)
                    zf.write(filepath, filename)
        
        memory_file.seek(0)
        return send_file(
            memory_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name='all_products.zip'
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/delete-product/<product_id>', methods=['DELETE'])
def delete_product(product_id):
    try:
        filepath = os.path.join(PRODUCTS_DIR, secure_filename(f'{product_id}.json'))
        os.remove(filepath)
        return jsonify({'message': 'Product deleted successfully'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/consumer')
@login_required
def consumer():
    if current_user.user_type not in ['distributor', 'admin']:
        return 'Access denied', 403
    
    # Get all products from all suppliers with owners preloaded
    products = Product.query.join(User).order_by(Product.timestamp.desc()).all()
    print(f"Consumer route - Found {len(products)} total products")
    
    return render_template('consumer.html', products=products)

@app.route('/conversion-result/<result_id>')
@login_required
def conversion_result(result_id):
    try:
        # Ensure the products directory exists
        os.makedirs(PRODUCTS_DIR, exist_ok=True)
        
        # Get result from file
        result_path = os.path.join(PRODUCTS_DIR, secure_filename(result_id))
        print(f"Looking for conversion result at: {result_path}")
        
        if not os.path.exists(result_path):
            print(f"Result file not found: {result_path}")
            # Check if it's an AJAX request
            if request.headers.get('Accept') == 'application/json':
                return jsonify({'error': 'Conversion result not found'}), 404
            return render_template('conversion_result.html', error='Conversion result not found')
            
        with open(result_path, 'r') as f:
            result = json.load(f)
            
        print(f"Successfully loaded conversion result with {len(result.get('products', []))} products")
        
        # If it's an AJAX request, return JSON
        if request.headers.get('Accept') == 'application/json':
            return jsonify(result)
            
        return render_template('conversion_result.html', 
                             result=result,
                             timestamp=result.get('timestamp'),
                             api_spec=result.get('api_spec'),
                             products=result.get('products', []),
                             has_errors=result.get('has_errors', False))
                             
    except Exception as e:
        print(f"Error in conversion_result: {str(e)}")
        traceback.print_exc()
        # If it's an AJAX request, return JSON error
        if request.headers.get('Accept') == 'application/json':
            return jsonify({'error': str(e)}), 500
        return render_template('conversion_result.html', error=str(e))

@app.route('/download-selected', methods=['POST'])
def download_selected():
    try:
        product_ids = request.json['product_ids']
        
        # Get selected products
        products = []
        for product_id in product_ids:
            filepath = os.path.join(PRODUCTS_DIR, f'{product_id}.json')
            if os.path.exists(filepath):
                with open(filepath, 'r') as f:
                    product = json.load(f)
                    products.append(product)

        if not products:
            return 'No products found', 404

        # Create a JSON file with selected products
        return jsonify(products)

    except Exception as e:
        return str(e), 500

@app.route('/share')
@login_required
def share():
    if current_user.user_type not in ['supplier', 'admin']:
        return 'Access denied', 403
    
    # Get user's products
    if current_user.user_type == 'admin':
        products = Product.query.order_by(Product.timestamp.desc()).all()
    else:
        products = Product.query.filter_by(user_id=current_user.id).order_by(Product.timestamp.desc()).all()
    
    # Get all templates
    templates = Template.query.order_by(Template.created_at.desc()).all()
    
    print(f"Share route - Found {len(products)} products and {len(templates)} templates")
    
    return render_template('share.html', products=products, templates=templates)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        first_name = request.form['first_name']
        last_name = request.form['last_name']
        company_name = request.form['company_name']
        user_type = request.form['user_type']

        if User.query.filter_by(email=email).first():
            return 'Email already registered'

        user = User(
            email=email,
            first_name=first_name,
            last_name=last_name,
            company_name=company_name,
            user_type=user_type
        )
        user.set_password(password)
        
        # Generate distributor ID for distributors
        if user_type == 'distributor':
            user.generate_distributor_id()
            
        db.session.add(user)
        db.session.commit()

        login_user(user)
        if user.user_type == 'supplier':
            return redirect(url_for('share'))
        else:
            return redirect(url_for('consumer'))

    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        print(f"\nLogin attempt - Email: {email}")  # Debug log
        
        user = User.query.filter_by(email=email).first()
        print(f"User found: {bool(user)}")  # Debug log
        if user:
            print(f"User type: {user.user_type}")  # Debug log
        
        if user and user.check_password(password):
            login_user(user)
            print(f"Login successful - User: {user.email}, Type: {user.user_type}")  # Debug log
            
            # Redirect based on user type
            if user.user_type == 'admin':
                print("Redirecting to admin dashboard")  # Debug log
                return redirect(url_for('admin'))
            elif user.user_type == 'supplier':
                print("Redirecting to share page")  # Debug log
                return redirect(url_for('share'))
            elif user.user_type == 'distributor':
                print("Redirecting to consumer page")  # Debug log
                return redirect(url_for('consumer'))
            else:
                print(f"Unknown user type: {user.user_type}")  # Debug log
                return redirect(url_for('index'))
        else:
            print("Login failed - Invalid credentials")  # Debug log
        
        return 'Invalid email or password'

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.user_type == 'supplier':
        # Show only user's products
        products = Product.query.filter_by(user_id=current_user.id).all()
    else:
        # Show all products for distributors
        products = Product.query.all()
    
    return render_template('dashboard.html', products=products)

@app.route('/debug-products')
@login_required
def debug_products():
    try:
        products = Product.query.all()
        result = []
        for p in products:
            result.append({
                'id': p.id,
                'name': p.name,
                'user_id': p.user_id,
                'timestamp': p.timestamp.isoformat(),
                'content_type': type(p.content).__name__,
                'content_preview': str(p.content)[:100]
            })
        return jsonify(result)
    except Exception as e:
        print(f"Debug error: {str(e)}")
        traceback.print_exc()
        return jsonify({'error': str(e)})

@app.route('/check-db')
@login_required
def check_db():
    try:
        # Get all users
        users = User.query.all()
        user_data = [{'id': u.id, 'email': u.email, 'type': u.user_type} for u in users]
        
        # Get all products
        products = Product.query.all()
        product_data = [{
            'id': p.id,
            'name': p.name,
            'user_id': p.user_id,
            'timestamp': p.timestamp.isoformat(),
            'has_content': bool(p.content),
            'has_api_spec': bool(p.api_spec)
        } for p in products]
        
        return jsonify({
            'users': user_data,
            'products': product_data,
            'current_user': {
                'id': current_user.id,
                'email': current_user.email,
                'type': current_user.user_type
            }
        })
    except Exception as e:
        print(f"Database check error: {str(e)}")
        traceback.print_exc()
        return jsonify({'error': str(e)})

@app.route('/delete-selected', methods=['POST'])
@login_required
def delete_selected():
    try:
        product_ids = request.json.get('product_ids', [])
        if not product_ids:
            return jsonify({'error': 'No products selected'}), 400

        # Delete selected products
        Product.query.filter(
            Product.id.in_(product_ids),
            Product.user_id == current_user.id  # Ensure user can only delete their own products
        ).delete(synchronize_session=False)
        
        db.session.commit()
        return jsonify({'message': 'Products deleted successfully'})
    except Exception as e:
        print(f"Error deleting products: {str(e)}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# Add admin route
@app.route('/admin')
@login_required
@admin_required
def admin():
    try:
        print("\n=== Admin Dashboard Access ===")
        print(f"Current user: {current_user.email} (ID: {current_user.id}, Type: {current_user.user_type})")
        
        # Clear any existing session and create a new one
        db.session.remove()
        db.session.begin()
        
        # Get all users with a fresh query
        users = User.query.order_by(User.id).all()
        templates = Template.query.order_by(Template.created_at.desc()).all()
        
        # Debug logging
        print(f"\nFound {len(users)} users:")
        for user in users:
            print(f"- User: {user.email} (ID: {user.id}, Type: {user.user_type}, Name: {user.first_name} {user.last_name})")
        
        print(f"\nFound {len(templates)} templates")
        print("=== End Admin Dashboard Access ===\n")
        
        # Ensure we have a valid database connection
        if not db.session.is_active:
            db.session.begin()
        
        return render_template('admin/dashboard.html', users=users, templates=templates)
        
    except Exception as e:
        print(f"Error in admin route: {str(e)}")
        traceback.print_exc()
        db.session.rollback()
        return f"Error loading admin dashboard: {str(e)}", 500

# Add template management routes before the admin route
@app.route('/admin/templates/add', methods=['POST'])
@login_required
@admin_required
def add_template():
    try:
        name = request.form['name']
        url = request.form['url']
        
        template = Template(name=name, url=url)
        db.session.add(template)
        db.session.commit()
        
        return redirect(url_for('admin'))
    except Exception as e:
        print(f"Error adding template: {str(e)}")
        return str(e), 500

@app.route('/admin/templates/delete/<int:template_id>', methods=['POST'])
@login_required
@admin_required
def delete_template(template_id):
    try:
        template = Template.query.get_or_404(template_id)
        db.session.delete(template)
        db.session.commit()
        return redirect(url_for('admin'))
    except Exception as e:
        print(f"Error deleting template: {str(e)}")
        return str(e), 500

@app.route('/api/templates')
@login_required
def get_templates():
    templates = Template.query.all()
    return jsonify([{
        'id': t.id,
        'name': t.name,
        'url': t.url
    } for t in templates])

# Add this to your initialization code to create admin user
def create_admin_user():
    try:
        print("\n=== Creating/Updating Users ===")
        
        # Create admin user
        admin_user = User.query.filter_by(email="admin@admin.com").first()
        print(f"Checking for admin user: {'Found' if admin_user else 'Not found'}")
        
        if not admin_user:
            print("Creating new admin user...")
            admin_user = User(
                email="admin@admin.com",
                first_name="Admin",
                last_name="User",
                company_name="Page2API",
                user_type="admin"
            )
            admin_user.set_password("admin")
            db.session.add(admin_user)
            db.session.commit()
            print("Admin user created successfully")
            
            # Verify admin was created
            admin_check = User.query.filter_by(email="admin@admin.com").first()
            print(f"Admin user verification: {'Success' if admin_check else 'Failed'}")
        else:
            print("Updating existing admin user...")
            admin_user.user_type = "admin"
            admin_user.first_name = "Admin"  # Ensure all fields are set
            admin_user.last_name = "User"
            admin_user.company_name = "Page2API"
            admin_user.set_password("admin")  # Reset password to default
            db.session.commit()
            print("Existing admin user updated")

        # Create test user if doesn't exist
        test_user = User.query.filter_by(email="simone.lini@gmail.com").first()
        print(f"Checking for test user: {'Found' if test_user else 'Not found'}")
        
        if not test_user:
            print("Creating new test user...")
            test_user = User(
                email="simone.lini@gmail.com",
                first_name="Simone",
                last_name="Lini",
                company_name="Page2API",
                user_type="supplier"
            )
            test_user.set_password("password")
            db.session.add(test_user)
            db.session.commit()
            print("Test user created successfully")
        else:
            print("Test user already exists")

        print("=== User Creation/Update Complete ===\n")

    except Exception as e:
        print(f"Error creating/updating users: {str(e)}")
        traceback.print_exc()
        db.session.rollback()
        raise  # Re-raise the exception to handle it in the calling function

# Add debug route to check users
@app.route('/debug-users')
def debug_users():
    try:
        users = User.query.all()
        return jsonify([{
            'id': u.id,
            'email': u.email,
            'type': u.user_type,
            'name': f"{u.first_name} {u.last_name}"
        } for u in users])
    except Exception as e:
        return jsonify({'error': str(e)})

# Add route to force database initialization
@app.route('/init-db')
def init_db():
    try:
        print("\n=== Initializing Database ===")
        database_url = os.environ.get('DATABASE_URL')
        print(f"Database URL: {'[SET]' if database_url else '[NOT SET]'}")
        
        with app.app_context():
            # Check if tables exist first
            print("Checking database tables...")
            inspector = db.inspect(db.engine)
            existing_tables = inspector.get_table_names()
            print(f"Existing tables: {existing_tables}")
            
            if 'user' not in existing_tables:
                print("Creating user table...")
                User.__table__.create(db.engine)
            
            if 'product' not in existing_tables:
                print("Creating product table...")
                Product.__table__.create(db.engine)
            
            if 'template' not in existing_tables:
                print("Creating template table...")
                Template.__table__.create(db.engine)
            
            # Add new columns if they don't exist
            try:
                print("Checking and adding new columns if needed...")
                # Check if distributor_id exists before adding
                result = db.session.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='user' AND column_name='distributor_id'"))
                if not result.fetchone():
                    print("Adding distributor_id column...")
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN distributor_id VARCHAR(36) UNIQUE'))
                
                # Check if reward_percentage exists before adding
                result = db.session.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='product' AND column_name='reward_percentage'"))
                if not result.fetchone():
                    print("Adding reward_percentage column...")
                    db.session.execute(text('ALTER TABLE product ADD COLUMN reward_percentage FLOAT DEFAULT 0.0'))
                
                db.session.commit()
                print("Column checks completed")
            except Exception as e:
                print(f"Error checking/adding columns: {str(e)}")
                db.session.rollback()
            
            # Create admin and test users
            print("\nCreating/updating users...")
            create_admin_user()
            print("Users created/updated successfully")
            
            return jsonify({
                'database_url_set': bool(database_url),
                'message': 'Database initialized successfully'
            })
    except Exception as e:
        print(f"Error initializing database: {str(e)}")
        return jsonify({
            'database_url_set': bool(os.environ.get('DATABASE_URL')),
            'error': str(e)
        }), 500

@app.route('/convert-selected', methods=['POST'])
@login_required
def convert_selected():
    try:
        print("\n=== Starting convert_selected ===")
        # Check if user is a distributor or admin
        if current_user.user_type not in ['distributor', 'admin']:
            print("Access denied: User type not distributor/admin")
            return jsonify({'error': 'Access denied. Only distributors can convert products.'}), 403

        data = request.json
        if not data:
            print("No data provided in request")
            return jsonify({'error': 'No data provided'}), 400
            
        api_spec = data.get('api_spec')
        product_ids = data.get('product_ids', [])
        
        if not api_spec:
            print("No API specification provided")
            return jsonify({'error': 'API specification is required'}), 400
            
        if not product_ids:
            print("No product IDs provided")
            return jsonify({'error': 'No products selected'}), 400

        # Get distributor ID
        distributor_id = None
        if current_user.user_type == 'distributor':
            distributor_id = current_user.distributor_id
            print(f"Using distributor ID: {distributor_id}")

        # Get selected products
        products = Product.query.filter(Product.id.in_(product_ids)).all()
        if not products:
            print("No products found")
            return jsonify({'error': 'No products found'}), 404

        print(f"Found {len(products)} products to convert")

        # Check if ANTHROPIC_API_KEY is set
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            print("ANTHROPIC_API_KEY not set")
            return jsonify({'error': 'ANTHROPIC_API_KEY environment variable not set'}), 500

        client = Anthropic(api_key=api_key)
        converted_products = []
        has_errors = False

        # Prepare selected products for conversion
        selected_products_data = []
        for product in products:
            selected_products_data.append({
                'id': product.id,
                'content': product.content
            })

        # Convert selected products in a single request
        prompt = f"""Given these products:
{json.dumps(selected_products_data, indent=2)}

And this target API specification:
{api_spec}

Please convert each product to match the target API specification format.
For each product, preserve its ID and convert its content.

IMPORTANT: For any URLs in the converted content, append "?ref={distributor_id}" if a distributor ID is provided.
Current distributor ID: {distributor_id if distributor_id else 'None'}

Return ONLY an array of objects, where each object has:
1. id: The original product ID
2. content: The converted content matching the API specification, with distributor ID appended to URLs if provided

Return the array in valid JSON format, with no additional text or explanations."""

        message = client.messages.create(
            model="claude-3-5-sonnet-latest",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": prompt
            }]
        )
        
        converted_content = message.content[0].text.strip()
        
        try:
            # Parse the response as JSON
            converted_results = json.loads(converted_content)
            
            # Ensure we have an array
            if not isinstance(converted_results, list):
                converted_results = [converted_results]
                
            # Save results to a file
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'conversion_{timestamp}.json'
            filepath = os.path.join(PRODUCTS_DIR, filename)
            
            os.makedirs(PRODUCTS_DIR, exist_ok=True)
            
            # Process each converted product
            for result in converted_results:
                try:
                    product = next(p for p in products if str(p.id) == str(result['id']))
                    converted_products.append({
                        'id': product.id,
                        'original_content': product.content,
                        'converted_content': result['content'],
                        'name': product.name,
                        'timestamp': product.timestamp.isoformat()
                    })
                except Exception as e:
                    has_errors = True
                    print(f"Error processing product {result.get('id')}: {str(e)}")
                    converted_products.append({
                        'id': result.get('id'),
                        'error': str(e),
                        'name': next((p.name for p in products if str(p.id) == str(result.get('id'))), 'Unknown')
                    })
            
            # Save the final results
            with open(filepath, 'w') as f:
                json.dump({
                    'timestamp': datetime.now().isoformat(),
                    'api_spec': api_spec,
                    'products': converted_products,
                    'has_errors': has_errors,
                    'distributor_id': distributor_id
                }, f, indent=2)
            
            return jsonify({
                'message': 'Conversion completed',
                'result_id': filename,
                'products': converted_products,
                'has_errors': has_errors,
                'distributor_id': distributor_id
            })
            
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON response: {str(e)}")
            return jsonify({'error': 'Invalid JSON response from conversion'}), 500
            
    except Exception as e:
        print(f"\nError during conversion: {str(e)}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/convert-all', methods=['POST'])
@login_required
def convert_all():
    try:
        print("\n=== Starting convert_all ===")
        # Check if user is a distributor or admin
        if current_user.user_type not in ['distributor', 'admin']:
            print("Access denied: User type not distributor/admin")
            return jsonify({'error': 'Access denied. Only distributors can convert products.'}), 403

        data = request.json
        if not data:
            print("No data provided in request")
            return jsonify({'error': 'No data provided'}), 400
            
        api_spec = data.get('api_spec')
        if not api_spec:
            print("No API specification provided")
            return jsonify({'error': 'API specification is required'}), 400

        # Get distributor ID
        distributor_id = None
        if current_user.user_type == 'distributor':
            distributor_id = current_user.distributor_id
            print(f"Using distributor ID: {distributor_id}")

        # Get selected products or all products
        if 'product_ids' in data:
            print(f"Converting selected products: {data['product_ids']}")
            products = Product.query.filter(Product.id.in_(data['product_ids'])).all()
        else:
            print("Converting all products")
            products = Product.query.all()

        if not products:
            print("No products found")
            return jsonify({'error': 'No products found'}), 404

        print(f"Found {len(products)} products to convert")

        # Check if ANTHROPIC_API_KEY is set
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            print("ANTHROPIC_API_KEY not set")
            return jsonify({'error': 'ANTHROPIC_API_KEY environment variable not set'}), 500

        client = Anthropic(api_key=api_key)
        converted_products = []
        has_errors = False

        # Prepare all products for a single conversion
        all_products_data = []
        for product in products:
            all_products_data.append({
                'id': product.id,
                'content': product.content
            })

        # Convert all products in a single request
        prompt = f"""Given these products:
{json.dumps(all_products_data, indent=2)}

And this target API specification:
{api_spec}

Please convert each product to match the target API specification format.
For each product, preserve its ID and convert its content.

IMPORTANT: For any URLs in the converted content, append "?ref={distributor_id}" if a distributor ID is provided.
Current distributor ID: {distributor_id if distributor_id else 'None'}

Return ONLY an array of objects, where each object has:
1. id: The original product ID
2. content: The converted content matching the API specification, with distributor ID appended to URLs if provided

Return the array in valid JSON format, with no additional text or explanations."""

        message = client.messages.create(
            model="claude-3-5-sonnet-latest",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": prompt
            }]
        )
        
        converted_content = message.content[0].text.strip()
        
        try:
            # Parse the response as JSON
            converted_results = json.loads(converted_content)
            
            # Ensure we have an array
            if not isinstance(converted_results, list):
                converted_results = [converted_results]
                
            # Save results to a file
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'conversion_{timestamp}.json'
            filepath = os.path.join(PRODUCTS_DIR, filename)
            
            os.makedirs(PRODUCTS_DIR, exist_ok=True)
            
            # Process each converted product
            for result in converted_results:
                try:
                    product = next(p for p in products if str(p.id) == str(result['id']))
                    converted_products.append({
                        'id': product.id,
                        'original_content': product.content,
                        'converted_content': result['content'],
                        'name': product.name,
                        'timestamp': product.timestamp.isoformat()
                    })
                except Exception as e:
                    has_errors = True
                    print(f"Error processing product {result.get('id')}: {str(e)}")
                    converted_products.append({
                        'id': result.get('id'),
                        'error': str(e),
                        'name': next((p.name for p in products if str(p.id) == str(result.get('id'))), 'Unknown')
                    })
            
            # Save the final results
            with open(filepath, 'w') as f:
                json.dump({
                    'timestamp': datetime.now().isoformat(),
                    'api_spec': api_spec,
                    'products': converted_products,
                    'has_errors': has_errors,
                    'distributor_id': distributor_id
                }, f, indent=2)
            
            return jsonify({
                'message': 'Conversion completed',
                'result_id': filename,
                'products': converted_products,
                'has_errors': has_errors,
                'distributor_id': distributor_id
            })
            
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON response: {str(e)}")
            return jsonify({'error': 'Invalid JSON response from conversion'}), 500
            
    except Exception as e:
        print(f"\nError during conversion: {str(e)}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/keys', methods=['GET'])
@login_required
def list_api_keys():
    if current_user.user_type not in ['distributor', 'admin']:
        return jsonify({'error': 'Access denied'}), 403
        
    keys = APIKey.query.filter_by(user_id=current_user.id).all()
    return jsonify([{
        'id': k.id,
        'name': k.name,
        'created_at': k.created_at.isoformat(),
        'last_used_at': k.last_used_at.isoformat() if k.last_used_at else None,
        'is_active': k.is_active,
        'requests_count': k.requests_count
    } for k in keys])

@app.route('/api/keys', methods=['POST'])
@login_required
def create_api_key():
    if current_user.user_type not in ['distributor', 'admin']:
        return jsonify({'error': 'Access denied'}), 403
        
    name = request.json.get('name')
    if not name:
        return jsonify({'error': 'Name is required'}), 400
        
    api_key = APIKey(user_id=current_user.id, name=name)
    db.session.add(api_key)
    db.session.commit()
    
    return jsonify({
        'id': api_key.id,
        'key': api_key.key,  # Only shown once upon creation
        'name': api_key.name,
        'created_at': api_key.created_at.isoformat()
    })

@app.route('/api/keys/<int:key_id>', methods=['DELETE'])
@login_required
def delete_api_key(key_id):
    if current_user.user_type not in ['distributor', 'admin']:
        return jsonify({'error': 'Access denied'}), 403
        
    api_key = APIKey.query.filter_by(id=key_id, user_id=current_user.id).first()
    if not api_key:
        return jsonify({'error': 'API key not found'}), 404
        
    db.session.delete(api_key)
    db.session.commit()
    
    return jsonify({'message': 'API key deleted successfully'})

@app.route('/api/keys/<int:key_id>/deactivate', methods=['POST'])
@login_required
def deactivate_api_key(key_id):
    if current_user.user_type not in ['distributor', 'admin']:
        return jsonify({'error': 'Access denied'}), 403
        
    api_key = APIKey.query.filter_by(id=key_id, user_id=current_user.id).first()
    if not api_key:
        return jsonify({'error': 'API key not found'}), 404
        
    api_key.is_active = False
    db.session.commit()
    
    return jsonify({'message': 'API key deactivated successfully'})

@app.route('/api/v1/convert', methods=['POST'])
@require_api_key
def api_convert():
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
            
        query = data.get('query')
        format_url = data.get('format_url')
        distributor_id = data.get('distributor_id')
        
        if not all([query, format_url, distributor_id]):
            return jsonify({'error': 'Missing required fields: query, format_url, distributor_id'}), 400
            
        # Verify distributor ID
        user = User.query.filter_by(distributor_id=distributor_id, user_type='distributor').first()
        if not user:
            return jsonify({'error': 'Invalid distributor ID'}), 401
            
        # Get format specification from URL
        try:
            response = requests.get(format_url)
            if not response.ok:
                return jsonify({'error': 'Failed to fetch format specification'}), 400
            format_spec = response.text
        except Exception as e:
            return jsonify({'error': f'Error fetching format specification: {str(e)}'}), 400
            
        # Convert natural language query to database query
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            return jsonify({'error': 'ANTHROPIC_API_KEY environment variable not set'}), 500
            
        client = Anthropic(api_key=api_key)
        
        # Convert query to database filter
        query_prompt = f"""Given this natural language query for travel products:
{query}

Convert it into a set of database filters. Consider these product categories:
- Tours & Activities
- Accommodation
- Transportation
- Events & Shows
- Car Rental
- Vacations
- Airport Transfers

Return ONLY a JSON object with these fields:
1. categories: array of relevant categories
2. keywords: array of important keywords to match
3. min_price: minimum price (if specified)
4. max_price: maximum price (if specified)
5. location: location info (if specified)

Example:
{{"categories": ["Tours & Activities"], "keywords": ["walking", "food"], "min_price": null, "max_price": 100, "location": "Rome"}}"""

        message = client.messages.create(
            model="claude-3-5-haiku-latest",
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": query_prompt
            }]
        )
        
        try:
            query_params = json.loads(message.content[0].text.strip())
        except json.JSONDecodeError:
            return jsonify({'error': 'Failed to parse query parameters'}), 500
            
        # Build database query
        products_query = Product.query
        
        if query_params.get('categories'):
            products_query = products_query.filter(Product.category.in_(query_params['categories']))
            
        # Get matching products
        products = products_query.all()
        if not products:
            return jsonify({'error': 'No matching products found'}), 404
            
        # Convert products to target format
        conversion_prompt = f"""Given these products:
{json.dumps([p.content for p in products], indent=2)}

And this target format specification:
{format_spec}

Please convert each product to match the target format specification.
IMPORTANT: For any URLs in the converted content, append "?ref={distributor_id}"

Return ONLY an array of converted products in the target format, with no additional text or explanations."""

        message = client.messages.create(
            model="claude-3-5-sonnet-latest",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": conversion_prompt
            }]
        )
        
        try:
            converted_products = json.loads(message.content[0].text.strip())
            
            # Save conversion result
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'conversion_{timestamp}.json'
            filepath = os.path.join(PRODUCTS_DIR, filename)
            
            with open(filepath, 'w') as f:
                json.dump({
                    'timestamp': datetime.now().isoformat(),
                    'query': query,
                    'format_url': format_url,
                    'distributor_id': distributor_id,
                    'products': converted_products
                }, f, indent=2)
            
            return jsonify({
                'result_id': filename,
                'products': converted_products
            })
            
        except json.JSONDecodeError:
            return jsonify({'error': 'Failed to parse converted products'}), 500
            
    except Exception as e:
        print(f"Error in API conversion: {str(e)}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/v1/conversion/<result_id>', methods=['GET'])
@require_api_key
def api_get_conversion(result_id):
    try:
        filepath = os.path.join(PRODUCTS_DIR, secure_filename(result_id))
        if not os.path.exists(filepath):
            return jsonify({'error': 'Conversion result not found'}), 404
            
        with open(filepath, 'r') as f:
            result = json.load(f)
            
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/docs')
@login_required
def api_docs():
    if current_user.user_type not in ['distributor', 'admin']:
        return redirect(url_for('index'))
        
    openapi_spec = {
        "openapi": "3.0.0",
        "info": {
            "title": "Page2API Product Conversion API",
            "version": "1.0.0",
            "description": "API for converting travel products to your desired format"
        },
        "servers": [
            {
                "url": request.host_url.rstrip('/'),
                "description": "Current server"
            }
        ],
        "components": {
            "securitySchemes": {
                "ApiKeyAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-API-Key"
                }
            }
        },
        "security": [
            {
                "ApiKeyAuth": []
            }
        ],
        "paths": {
            "/api/v1/convert": {
                "post": {
                    "summary": "Convert products based on query",
                    "description": "Convert travel products matching the query to the specified format",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["query", "format_url", "distributor_id"],
                                    "properties": {
                                        "query": {
                                            "type": "string",
                                            "description": "Natural language query describing the products to find",
                                            "example": "all walking tours in Rome under $100"
                                        },
                                        "format_url": {
                                            "type": "string",
                                            "description": "URL containing the target format specification",
                                            "example": "https://example.com/product-format.json"
                                        },
                                        "distributor_id": {
                                            "type": "string",
                                            "description": "Your distributor ID",
                                            "example": "abc123-def456"
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Successful conversion",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "result_id": {
                                                "type": "string",
                                                "description": "ID of the conversion result"
                                            },
                                            "products": {
                                                "type": "array",
                                                "description": "Array of converted products",
                                                "items": {
                                                    "type": "object"
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        },
                        "400": {
                            "description": "Invalid request",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "error": {
                                                "type": "string"
                                            }
                                        }
                                    }
                                }
                            }
                        },
                        "401": {
                            "description": "Invalid API key or distributor ID"
                        },
                        "429": {
                            "description": "Rate limit exceeded"
                        }
                    }
                }
            },
            "/api/v1/conversion/{result_id}": {
                "get": {
                    "summary": "Get conversion result",
                    "description": "Get the result of a previous conversion",
                    "parameters": [
                        {
                            "name": "result_id",
                            "in": "path",
                            "required": True,
                            "schema": {
                                "type": "string"
                            },
                            "description": "ID of the conversion result"
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Conversion result",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "timestamp": {
                                                "type": "string",
                                                "format": "date-time"
                                            },
                                            "query": {
                                                "type": "string"
                                            },
                                            "format_url": {
                                                "type": "string"
                                            },
                                            "distributor_id": {
                                                "type": "string"
                                            },
                                            "products": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object"
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        },
                        "404": {
                            "description": "Conversion result not found"
                        }
                    }
                }
            }
        }
    }
    
    return render_template('api_docs.html', openapi_spec=openapi_spec)

# Initialize the app only if running directly
if __name__ == '__main__':
    with app.app_context():
        # Run migrations instead of creating tables directly
        from flask_migrate import upgrade as flask_migrate_upgrade
        flask_migrate_upgrade()
        create_admin_user()
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
