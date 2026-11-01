# Inference image: the reconcile package and nothing else. Build with `src/` as
# the context (see run_inference.sh --build), so nothing outside it can be copied
# in. The package has no runtime dependencies. The preparation adapters (key
# handling, upstream readers) are deleted: inference must not need them.
FROM python:3.12-slim
COPY reconcile /app/reconcile
RUN rm -rf /app/reconcile/adapters \
 && python -m compileall -q /app/reconcile
ENV PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
USER 65534:65534
ENTRYPOINT ["python", "-m", "reconcile.runs.batch"]
