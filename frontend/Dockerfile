# frontend/Dockerfile
FROM python:3.14-slim

WORKDIR /app

# Install uv
RUN pip install uv

# Copy requirements file
COPY requirements.txt .

# Install dependencies with uv
RUN uv venv && uv pip install -r requirements.txt

# Copy frontend code
COPY app.py .
COPY templates/ ./templates/

EXPOSE 5000

CMD ["uv", "run", "python", "app.py"]