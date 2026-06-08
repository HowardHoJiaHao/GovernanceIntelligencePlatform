FROM python:3.14.4-bookworm

WORKDIR /app

COPY pyproject.toml ./

RUN pip install fastapi uvicorn

COPY . .

EXPOSE 8000

# CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
CMD ["uvicorn", "database_logic:app", "--host", "0.0.0.0", "--port", "8000"]