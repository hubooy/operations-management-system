# Build with an operator-reviewed digest, e.g. python:3.12-slim@sha256:... .
# Deployment uses the resulting sha256 image ID, never a mutable tag.
ARG PYTHON_BASE
FROM ${PYTHON_BASE}
RUN pip install --no-cache-dir pandas==2.3.3 numpy==2.3.5
COPY backend/pandas_runner/entrypoint.py /opt/entrypoint.py
USER 65532:65532
WORKDIR /work
ENTRYPOINT ["python", "-I", "-B", "/opt/entrypoint.py"]
