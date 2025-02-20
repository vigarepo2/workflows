# Use official Python base image
FROM python:3.9

# Set working directory
WORKDIR /app

# Install necessary packages
RUN apt-get update && apt-get install -y curl ffmpeg

# Create 'files' directory for storing downloaded files
RUN mkdir -p /app/files

# Download file while building the image (PERMANENT STORAGE)
RUN curl -L -o "/app/files/Badass Ravi Kumar 2025 Hindi 1080p HDTS x264 AAC CineVood.mkv" \
    "https://motionpicturepro55.mhjoybots.workers.dev/0:findpath?id=1rxHHeK0enkfNWc27KaszBMUcfE5YunAx"

# Install Python dependencies
RUN pip install flask flask_cors ffmpeg-python

# Copy application files
COPY app.py /app/app.py
COPY templates /app/templates
COPY static /app/static

# Expose port
EXPOSE 80

# Run the application
CMD ["python3", "/app/app.py"]
