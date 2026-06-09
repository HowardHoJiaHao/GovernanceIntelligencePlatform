# Use a slim Python base image
FROM python:3.11-slim

# Set the working directory to the backend folder
WORKDIR /app/backend

# Copy the requirements file first (for better caching)
COPY requirements.txt .

# Install dependencies directly using pip
RUN apt-get update && apt-get install -y sqlite3 && rm -rf /var/lib/apt/lists/* gcc g++
# Install dependencies (This is the missing step!)
RUN pip install --no-cache-dir -r requirements.txt
# Copy the rest of your backend code
COPY . .

# Run the API script directly
CMD ["python3", "api.py"]