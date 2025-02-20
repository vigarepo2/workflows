import json
import os
import subprocess
import concurrent.futures

# Load download links from links.json
with open('/app/links.json') as f:
    links = json.load(f)

# Ensure the 'files' directory exists
os.makedirs('/app/files', exist_ok=True)

# Function to download each file
def download(link):
    filename = f"/app/files/{link['name']}"
    if not os.path.exists(filename):  # Skip if already downloaded
        subprocess.run(['curl', '-s', '-o', filename, link['url']])

# Run downloads in parallel
with concurrent.futures.ThreadPoolExecutor() as executor:
    executor.map(download, links)
