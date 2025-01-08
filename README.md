# Page2API

A powerful tool for converting product data between different formats and specifications.

## Features

- Convert product information from any webpage into structured data
- Support for multiple output formats (JSON, XML, CSV, Custom API)
- Separate workflows for suppliers and distributors
- Easy-to-use interface
- Automatic data synchronization

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/Page2API.git
cd Page2API
```

2. Create a virtual environment and activate it:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Set up environment variables:
```bash
cp .env.example .env
# Edit .env with your configuration
```

5. Run the application:
```bash
flask run
```

## Usage

1. Sign up for an account as either a supplier or distributor
2. For suppliers:
   - Share product data by pasting URLs
   - Choose output format
   - Manage shared products
3. For distributors:
   - Browse available products
   - Convert to your preferred format
   - Import directly into your system

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

## License

[MIT](https://choosealicense.com/licenses/mit/) 