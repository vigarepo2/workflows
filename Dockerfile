FROM python:3.9

# Set working directory
WORKDIR /app

# Copy JSON file
COPY links.json /app/links.json

# Install required packages
RUN apt-get update && apt-get install -y curl

# Create 'files' directory for downloaded files
RUN mkdir -p /app/files

# Parallel Download Script
RUN python3 -c "
import json, os, subprocess, concurrent.futures;
with open('/app/links.json') as f:
    links = json.load(f);
os.makedirs('/app/files', exist_ok=True);
def download(link):
    filename = f'/app/files/{link[\"name\"]}';
    if not os.path.exists(filename):  # Skip if file already exists
        subprocess.run(['curl', '-s', '-o', filename, link['url']]);
with concurrent.futures.ThreadPoolExecutor() as executor:
    executor.map(download, links);
"

# Install Flask to serve files
RUN pip install flask

# Copy Flask app
COPY app.py /app/app.py

EXPOSE 80
CMD ["python3", "/app/app.py"]
