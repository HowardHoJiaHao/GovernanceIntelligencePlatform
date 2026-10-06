# Use a slim Python base image
FROM python:3.11-slim

# Set the working directory to the backend folder
WORKDIR /app/backend

# sqlite3 CLI is handy for inspecting the database inside the container
RUN apt-get update && apt-get install -y --no-install-recommends sqlite3 && rm -rf /var/lib/apt/lists/*

# Copy the requirements file first (for better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your backend code
COPY . .

EXPOSE 5001

# Run the API script directly
CMD ["python3", "api.py"]
