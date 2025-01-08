#!/bin/bash

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create static build directory
mkdir -p build
cp -r static build/
cp -r templates build/

# Copy necessary files
cp app.py build/
cp requirements.txt build/ 