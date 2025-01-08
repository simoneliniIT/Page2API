#!/bin/bash

# Create public directory
mkdir -p public

# Copy static assets
cp -r static/* public/

# Copy HTML files directly
cp templates/index.html public/index.html
cp templates/login.html public/login.html
cp templates/signup.html public/signup.html
cp templates/share.html public/share.html
cp templates/consumer.html public/consumer.html

# Copy JavaScript and CSS
mkdir -p public/js
mkdir -p public/css
cp static/js/app.js public/js/
cp static/css/base.css public/css/

# Create _redirects file
echo "/* /index.html 200" > public/_redirects 