# Use official Python base image
FROM python:3.9

# Set working directory
WORKDIR /app

# Install necessary packages
RUN apt-get update && apt-get install -y curl ffmpeg

# Create 'files' directory for storing downloaded files
RUN mkdir -p /app/files

# Download file at build time (Permanent storage)
RUN curl -L -o "/app/files/Badass Ravi Kumar 2025 Hindi 1080p HDTS x264 AAC CineVood.mkv" \
    "https://p01--bar--x2zzx9vlgtc4.code.run/download/Badass%20Ravi%20Kumar%202025%20Hindi%201080p%20HDTS%20x264%20AAC%20CineVood.mkv"

# Install Python dependencies
RUN pip install flask flask_cors ffmpeg-python

# Copy application files
COPY app.py /app/app.py

# Expose port
EXPOSE 80

# Run the application
CMD ["python3", "/app/app.py"]
