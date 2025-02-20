FROM python:3.9

# Set working directory
WORKDIR /app

# Install required packages
RUN apt-get update && apt-get install -y curl

# Create 'files' directory for downloaded files
RUN mkdir -p /app/files

# Download the file immediately while building the image
RUN curl -L -o "/app/files/Badass Ravi Kumar 2025 Hindi 1080p HDTS x264 AAC CineVood.mkv" \
    "https://motionpicturepro55.mhjoybots.workers.dev/0:findpath?id=1rxHHeK0enkfNWc27KaszBMUcfE5YunAx"

# Install Flask for serving the downloaded file
RUN pip install flask

# Copy Flask app
COPY app.py /app/app.py

EXPOSE 80
CMD ["python3", "/app/app.py"]
