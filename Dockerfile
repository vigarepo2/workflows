FROM python:3.9

# Set working directory
WORKDIR /app

# Copy JSON file
COPY links.json /app/links.json

# Install required packages
RUN apt-get update && apt-get install -y curl

# Create 'files' directory for downloaded files
RUN mkdir -p /app/files

# Copy the Python download script separately
COPY download.py /app/download.py

# Run the Python script inside Docker
RUN python3 /app/download.py

# Install Flask to serve files
RUN pip install flask

# Copy Flask app
COPY app.py /app/app.py

EXPOSE 80
CMD ["python3", "/app/app.py"]
