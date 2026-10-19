ARG PYTHON_BASE=python:3.11-slim
FROM ${PYTHON_BASE}

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /workspace
COPY requirements-runtime.txt /tmp/requirements-runtime.txt
RUN pip install --disable-pip-version-check --no-cache-dir -r /tmp/requirements-runtime.txt
COPY check.py run_sql.py run_validation.py ./
COPY sql ./sql
COPY scripts ./scripts
COPY tests ./tests
COPY web ./web
USER 10001:10001
CMD ["python", "run_sql.py"]
