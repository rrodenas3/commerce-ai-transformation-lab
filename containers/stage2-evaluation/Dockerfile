FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a

WORKDIR /app
COPY scripts /app/scripts

USER 65532:65532
CMD []
ENTRYPOINT ["/usr/bin/env", "-i", "PYTHONPATH=/app", "TMPDIR=/work", "/usr/local/bin/python", "/app/scripts/run_stage2_isolated.py", "--inner"]
