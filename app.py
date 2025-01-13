from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for
from bs4 import BeautifulSoup
import requests
from anthropic import Anthropic, HUMAN_PROMPT, AI_PROMPT
import os
import json
from datetime import datetime
from werkzeug.utils import secure_filename
import traceback
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_migrate import Migrate, upgrade
from functools import wraps
from sqlalchemy import text

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
    # Define the relationship with lazy='joined' to avoid N+1 queries
    products = db.relationship('Product', backref=db.backref('owner', lazy='joined'), lazy='dynamic')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

@login_manager.user_loader
def load_user(id):
    return User.query.get(int(id))

# Update Product model to include user relationship
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.JSON, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    api_spec = db.Column(db.Text)

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

Please convert the product data to match the target API specification format.
Return ONLY the converted data in a clear, structured format, with no additional text or explanations."""

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

Please convert the product data to match the format of this template product:
{template.url}

Important guidelines:
1. Match the exact structure and field names from the template
2. Include ONLY factual information found on the product page
3. DO NOT invent or assume any data not explicitly shown
4. Use the same data types as the template for each field

Return ONLY the converted data in the same format as the template, with no additional text or explanations."""

        else:  # structured_json
            # Default structured JSON conversion
            print("Using structured JSON conversion")
            prompt = f"""Given this product URL:
{product_url}

Please create a well-structured JSON representation of the product data found on this page.
Important guidelines:
1. Include ONLY factual information found on the page
2. DO NOT invent or assume any data not explicitly shown
3. Use clear, descriptive field names
4. Organize data hierarchically where appropriate
5. Include all relevant product details (specs, features, pricing, etc.)
6. Format numbers and dates consistently
7. Use proper JSON data types (strings, numbers, booleans, arrays)

Return ONLY the JSON data with no additional text or explanations."""

        print("Calling Claude API...")
        client = Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-3-5-sonnet-latest",
            max_tokens=2048,
            messages=[{
                "role": "user",
                "content": prompt
            }]
        )
        
        formatted_output = message.content[0].text
        print(f"Got response from Claude ({len(formatted_output)} chars)")
        
        # Return the formatted output with the appropriate spec
        spec = data.get('api_spec') if conversion_type == 'custom_api' else (
            f"Template: {template.name}" if conversion_type == 'template' else 'Structured JSON'
        )
        
        return jsonify({
            'content': formatted_output,
            'feed_spec': spec
        })
        
    except Exception as e:
        print(f"Conversion error: {str(e)}")
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
        
        if not content:
            return jsonify({'error': 'No content provided'}), 400
        
        print(f"Content type: {type(content)}")
        print(f"Content: {json.dumps(content)[:100]}...")  # Convert to string for logging
        print(f"API Spec: {api_spec[:100] if api_spec else ''}")
        print(f"Current user: {current_user.email} (ID: {current_user.id})")
        
        # Ask Claude to generate a user-friendly name
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            return jsonify({'error': 'ANTHROPIC_API_KEY environment variable not set'}), 500
            
        client = Anthropic(api_key=api_key)
        prompt = f"""Based on this product data:
{json.dumps(content)}

Please provide a short, user-friendly name for this product (maximum 50 characters).
Return only the name, nothing else. The name should be clear and descriptive."""

        message = client.messages.create(
            model="claude-3-5-haiku-latest",
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": prompt
            }]
        )
        
        product_name = message.content[0].text.strip()
        print(f"Generated product name: {product_name}")
        
        # Create new Product in database
        product = Product(
            content=content,  # SQLAlchemy will handle the JSON conversion
            name=product_name,
            user_id=current_user.id,
            api_spec=api_spec
        )
        
        print("Adding product to database session...")
        db.session.add(product)
        print("Committing to database...")
        db.session.commit()
        print("Product saved successfully!")
            
        return jsonify({
            'message': 'Product saved successfully',
            'name': product_name
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
        # Get result from file
        result_path = os.path.join(PRODUCTS_DIR, f'{result_id}.json')
        if not os.path.exists(result_path):
            return 'Conversion result not found', 404
            
        with open(result_path, 'r') as f:
            result = json.load(f)
            
        return render_template('conversion_result.html', result=result)
    except Exception as e:
        print(f"Error in conversion_result: {str(e)}")
        traceback.print_exc()
        return str(e), 500

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
        db.session.add(user)
        db.session.commit()

        login_user(user)
        # Redirect based on user type after signup
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
        
        # Drop all tables
        print("Dropping all tables...")
        with app.app_context():
            db.session.execute(text('DROP TABLE IF EXISTS alembic_version'))
            db.session.execute(text('DROP TABLE IF EXISTS product'))
            db.session.execute(text('DROP TABLE IF EXISTS template'))
            db.session.execute(text('DROP TABLE IF EXISTS "user"'))
            db.session.commit()
            print("Tables dropped successfully")
            
            # Explicitly alter the column if it exists
            try:
                db.session.execute(text('ALTER TABLE "user" ALTER COLUMN password_hash TYPE varchar(512)'))
                db.session.commit()
                print("Altered password_hash column length")
            except Exception as e:
                print(f"Note: Could not alter column (this is expected if table doesn't exist): {str(e)}")
        
        print("Creating all tables...")
        db.create_all()
        print("Tables created successfully")
        
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

@app.route('/convert-all', methods=['POST'])
@login_required
def convert_all():
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
            
        api_spec = data.get('api_spec')
        if not api_spec:
            return jsonify({'error': 'API specification is required'}), 400

        # Get all products for the current user
        products = Product.query.filter_by(user_id=current_user.id).all()
        if not products:
            return jsonify({'error': 'No products found'}), 404

        # Check if ANTHROPIC_API_KEY is set
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            return jsonify({'error': 'ANTHROPIC_API_KEY environment variable not set'}), 500

        client = Anthropic(api_key=api_key)
        converted_products = []

        for product in products:
            try:
                prompt = f"""Given this product data:
{json.dumps(product.content, indent=2)}

And this target API specification:
{api_spec}

Please convert the product data to match the target API specification format.
Return ONLY the converted data in a clear, structured format, with no additional text or explanations."""

                message = client.messages.create(
                    model="claude-3-5-sonnet-latest",
                    max_tokens=2048,
                    messages=[{
                        "role": "user",
                        "content": prompt
                    }]
                )
                
                converted_products.append({
                    'id': product.id,
                    'original_content': product.content,
                    'converted_content': message.content[0].text,
                    'name': product.name,
                    'timestamp': product.timestamp.isoformat()
                })
            except Exception as e:
                print(f"Error converting product {product.id}: {str(e)}")
                converted_products.append({
                    'id': product.id,
                    'error': str(e),
                    'name': product.name
                })

        # Save results to a file
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'conversion_{timestamp}.json'
        filepath = os.path.join(PRODUCTS_DIR, filename)
        
        with open(filepath, 'w') as f:
            json.dump({
                'timestamp': datetime.now().isoformat(),
                'api_spec': api_spec,
                'products': converted_products
            }, f, indent=2)

        return jsonify({
            'message': 'Conversion completed',
            'result_id': filename,
            'products': converted_products
        })

    except Exception as e:
        print(f"Error during conversion: {str(e)}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/convert-selected', methods=['POST'])
@login_required
def convert_selected():
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
            
        api_spec = data.get('api_spec')
        product_ids = data.get('product_ids', [])
        
        if not api_spec:
            return jsonify({'error': 'API specification is required'}), 400
        if not product_ids:
            return jsonify({'error': 'No products selected'}), 400

        # Get selected products for the current user
        products = Product.query.filter(
            Product.id.in_(product_ids),
            Product.user_id == current_user.id
        ).all()
        
        if not products:
            return jsonify({'error': 'No products found'}), 404

        # Check if ANTHROPIC_API_KEY is set
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            return jsonify({'error': 'ANTHROPIC_API_KEY environment variable not set'}), 500

        client = Anthropic(api_key=api_key)
        converted_products = []

        for product in products:
            try:
                prompt = f"""Given this product data:
{json.dumps(product.content, indent=2)}

And this target API specification:
{api_spec}

Please convert the product data to match the target API specification format.
Return ONLY the converted data in a clear, structured format, with no additional text or explanations."""

                message = client.messages.create(
                    model="claude-3-5-sonnet-latest",
                    max_tokens=2048,
                    messages=[{
                        "role": "user",
                        "content": prompt
                    }]
                )
                
                converted_products.append({
                    'id': product.id,
                    'original_content': product.content,
                    'converted_content': message.content[0].text,
                    'name': product.name,
                    'timestamp': product.timestamp.isoformat()
                })
            except Exception as e:
                print(f"Error converting product {product.id}: {str(e)}")
                converted_products.append({
                    'id': product.id,
                    'error': str(e),
                    'name': product.name
                })

        # Save results to a file
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'conversion_{timestamp}.json'
        filepath = os.path.join(PRODUCTS_DIR, filename)
        
        with open(filepath, 'w') as f:
            json.dump({
                'timestamp': datetime.now().isoformat(),
                'api_spec': api_spec,
                'products': converted_products
            }, f, indent=2)

        return jsonify({
            'message': 'Conversion completed',
            'result_id': filename,
            'products': converted_products
        })

    except Exception as e:
        print(f"Error during conversion: {str(e)}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# Initialize the app only if running directly
if __name__ == '__main__':
    with app.app_context():
        # Run migrations instead of creating tables directly
        from flask_migrate import upgrade as flask_migrate_upgrade
        flask_migrate_upgrade()
        create_admin_user()
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
