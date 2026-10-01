ARG ANALYSIS_BASE=python:3.12.11-slim-bookworm
FROM ${ANALYSIS_BASE}
WORKDIR /repo
COPY requirements-analysis.txt /tmp/requirements-analysis.txt
RUN python3 -m pip install --no-cache-dir --target /opt/analysis-deps -r /tmp/requirements-analysis.txt
ENV PYTHONPATH=/opt/analysis-deps
