FROM python:3.14.4-bookworm

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN pip install uv && uv pip install --system --requirement pyproject.toml

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

# docker compose down --rmi all
# docker builder prune -af
# docker compose build --no-cache
# docker compose up