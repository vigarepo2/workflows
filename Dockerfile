# Use an official Python 3.9 image as base
FROM python:3.9

# Set the working directory in the container
WORKDIR /app

# Install git and other necessary dependencies
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# Clone the repository
RUN git clone https://github.com/vigarepo2/heroku-bypass /app

# Change working directory to the cloned repository
WORKDIR /app

# Install required Python packages
RUN pip install --no-cache-dir -r requirements.txt

# Expose the application port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
