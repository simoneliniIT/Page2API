from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for
from bs4 import BeautifulSoup
import requests
from anthropic import Anthropic
import os
import json
from datetime import datetime
from werkzeug.utils import secure_filename
import traceback
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_migrate import Migrate
from functools import wraps

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///your_database.db'
app.config['SECRET_KEY'] = 'your-secret-key-here'  # Change this to a secure secret key
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
    password_hash = db.Column(db.String(128))
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
        data = request.get_json()
        product_url = data['product_url']
        conversion_type = data['conversion_type']
        
        if conversion_type == 'custom_api':
            api_spec = data['api_spec']
            if not api_spec:
                return jsonify({'error': 'API specification is required for custom API conversion'}), 400
            
            # Get Claude's response for custom API
            anthropic = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
            prompt = f"""Given this product URL:
{product_url}

And this target API specification:
{api_spec}

Please convert the product data to match the target API specification format.
Return ONLY the converted data in a clear, structured format, with no additional text or explanations."""

        else:  # structured_json
            # Get Claude's response for structured JSON
            anthropic = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
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

        message = anthropic.messages.create(
            model="claude-3-sonnet-20240229",
            max_tokens=2048,
            messages=[{
                "role": "user",
                "content": prompt
            }]
        )
        
        formatted_output = message.content[0].text.strip()
        print(f"Initial conversion output: {formatted_output}")  # Debug print
        
        # Return the formatted output directly
        return jsonify({
            'content': formatted_output,
            'feed_spec': data.get('api_spec', 'Structured JSON')
        })
        
    except Exception as e:
        print(f"Conversion error: {str(e)}")  # Debug print
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
        anthropic = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
        prompt = f"""Based on this product data:
{json.dumps(content)}

Please provide a short, user-friendly name for this product (maximum 50 characters).
Return only the name, nothing else. The name should be clear and descriptive."""

        message = anthropic.messages.create(
            model="claude-3-sonnet-20240229",
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

@app.route('/convert-all', methods=['POST'])
def convert_all():
    try:
        # 1. Get API spec from request
        api_spec = request.json.get('api_spec')
        if not api_spec:
            print("No API spec provided")
            return jsonify({'error': 'API specification is required'}), 400
        
        # 2. Get products
        products = []
        for filename in os.listdir(PRODUCTS_DIR):
            if filename.endswith('.json'):
                filepath = os.path.join(PRODUCTS_DIR, filename)
                with open(filepath, 'r') as f:
                    data = json.load(f)
                    if 'converted_content' not in data:  # Skip conversion results
                        products.append(data['content'])
        
        if not products:
            print("No products found to convert")
            return jsonify({'error': 'No products found to convert'}), 400

        # 3. Prepare prompt
        products_str = "\n---\n".join(products)
        prompt = f"""You are a product data conversion expert. Convert these products to match the target API specification.

Source products:
{products_str}

Target API specification:
{api_spec}

Instructions:
1. Convert each product to match the target specification exactly
2. Maintain all relevant data from the source
3. Return ONLY the converted data
4. Do not include any explanations or additional text

Return the converted products in valid format."""

        print("\n=== Sending Prompt to Claude ===")
        print(f"Number of products: {len(products)}")
        print(f"API Spec: {api_spec}")
        print("=== End Prompt ===\n")

        # 4. Call Claude API
        try:
            anthropic = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
            if not anthropic.api_key:
                raise ValueError("ANTHROPIC_API_KEY not set")

            message = anthropic.messages.create(
                model="claude-3-sonnet-20240229",
                max_tokens=4096,
                messages=[{
                    "role": "user",
                    "content": prompt
                }]
            )
            
            converted_content = message.content[0].text.strip()
            print("\n=== Claude Response ===")
            print(converted_content)
            print("=== End Response ===\n")

        except Exception as claude_error:
            print(f"Claude API error: {str(claude_error)}")
            return jsonify({'error': f'Claude API error: {str(claude_error)}'}), 500

        # 5. Save result
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        result_id = f'conversion_{timestamp}'
        
        result_data = {
            'converted_content': converted_content,
            'original_spec': api_spec,
            'timestamp': timestamp,
            'num_products': len(products)
        }
        
        filepath = os.path.join(PRODUCTS_DIR, f'{result_id}.json')
        with open(filepath, 'w') as f:
            json.dump(result_data, f, indent=2)
            print(f"Saved conversion result to {filepath}")

        return jsonify({'id': result_id})

    except Exception as e:
        print(f"Convert all error: {str(e)}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

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
    
    print(f"Share route - Found {len(products)} products")
    
    return render_template('share.html', products=products)

@app.route('/convert-selected', methods=['POST'])
@login_required
def convert_selected():
    try:
        print("Starting convert-selected...")  # Debug log
        data = request.get_json()
        print(f"Received data: {data}")  # Debug log
        
        api_spec = data.get('api_spec')
        product_ids = data.get('product_ids', [])
        
        print(f"API Spec: {api_spec}")  # Debug log
        print(f"Product IDs: {product_ids}")  # Debug log
        
        if not api_spec:
            return jsonify({'error': 'API specification is required'}), 400
        
        if not product_ids:
            return jsonify({'error': 'No products selected'}), 400

        # Get selected products from database
        products = Product.query.filter(Product.id.in_(product_ids)).all()
        print(f"Found {len(products)} products")  # Debug log
        
        if not products:
            return jsonify({'error': 'No products found'}), 400

        # Prepare products data for conversion
        products_data = []
        for product in products:
            if product.content:
                products_data.append(product.content)
            else:
                print(f"Warning: Product {product.id} has no content")
        
        if not products_data:
            return jsonify({'error': 'No valid product data found'}), 400

        products_str = "\n---\n".join(json.dumps(p, indent=2) for p in products_data)

        # Prepare prompt for Claude
        prompt = f"""Given these products in their current formats:

{products_str}

Please convert them to match this target API specification:
{api_spec}

Return ONLY the converted data in the target format, with no additional text or explanations.
The response should be valid JSON."""

        print("Calling Claude API...")  # Debug log
        # Get Claude's response
        anthropic = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
        message = anthropic.messages.create(
            model="claude-3-sonnet-20240229",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": prompt
            }]
        )

        converted_content = message.content[0].text.strip()
        print("Got response from Claude")  # Debug log

        # Validate and format the converted content as JSON
        try:
            # Parse and re-format with proper indentation
            parsed_json = json.loads(converted_content)
            converted_content = json.dumps(parsed_json, indent=2)
        except json.JSONDecodeError:
            print("Invalid JSON in conversion result")  # Debug log
            return jsonify({'error': 'Invalid conversion result format'}), 500

        # Save the conversion result
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        result_id = f'conversion_{timestamp}'
        
        # Create a new conversion result record
        conversion_result = {
            'id': result_id,
            'converted_content': converted_content,
            'original_spec': api_spec,
            'timestamp': timestamp,
            'product_ids': product_ids,
            'num_products': len(products)
        }
        
        # Save to file instead of session
        result_path = os.path.join(PRODUCTS_DIR, f'{result_id}.json')
        with open(result_path, 'w') as f:
            json.dump(conversion_result, f, indent=2)
        
        print(f"Saved conversion result to {result_path}")  # Debug log
        return jsonify({'id': result_id, 'message': 'Conversion successful'})

    except Exception as e:
        print(f"Convert selected error: {str(e)}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

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
    print("\nAdmin dashboard accessed")  # Debug log
    
    # Clear any existing session
    db.session.remove()
    
    # Get all users with a fresh query
    users = User.query.order_by(User.id).all()
    templates = Template.query.order_by(Template.created_at.desc()).all()
    
    # Debug logging
    print(f"Found {len(users)} users and {len(templates)} templates")
    for user in users:
        print(f"User: {user.email} (ID: {user.id}, Type: {user.user_type}, Name: {user.first_name} {user.last_name})")
    
    return render_template('admin/dashboard.html', users=users, templates=templates)

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
        # Create admin user
        admin_user = User.query.filter_by(email="admin@admin.com").first()
        if not admin_user:
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
        else:
            # Update existing admin user to ensure correct type
            admin_user.user_type = "admin"
            db.session.commit()
            print("Existing admin user updated")

        # Create test user if doesn't exist
        test_user = User.query.filter_by(email="simone.lini@gmail.com").first()
        if not test_user:
            test_user = User(
                email="simone.lini@gmail.com",
                first_name="Simone",
                last_name="Lini",
                company_name="Page2API",
                user_type="supplier"
            )
            test_user.set_password("password")  # Set a default password
            db.session.add(test_user)
            db.session.commit()
            print("Test user created successfully")
        else:
            print("Test user already exists")

    except Exception as e:
        print(f"Error creating/updating users: {str(e)}")
        db.session.rollback()

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

# Call this after db initialization
with app.app_context():
    db.create_all()  # Make sure tables are created
    create_admin_user()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
