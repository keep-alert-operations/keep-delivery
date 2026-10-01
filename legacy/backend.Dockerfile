FROM python:3.13.5-slim
RUN pip install --no-cache-dir uv==0.10.8
WORKDIR /opt/appchi
COPY backend/pyproject.toml backend/uv.lock ./backend/
COPY backend/appchi ./backend/appchi
COPY migrations ./migrations
RUN uv sync --project backend --frozen --no-dev && chmod -R g=u /opt/appchi
ENV PATH="/opt/appchi/backend/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 1001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "appchi.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
