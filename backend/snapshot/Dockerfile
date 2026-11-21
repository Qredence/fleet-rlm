FROM python:3.13-slim

RUN pip install --no-cache-dir uv \
    && uv pip install --system --no-cache \
        'dspy==3.5.0' \
        'numpy>=2.2.0' \
        'pandas>=2.2.3' \
        'pyarrow>=19.0.0' \
        'openpyxl>=3.1.5' \
        'pillow>=11.1.0' \
        'matplotlib>=3.10.0' \
        'pypdf>=5.3.0' \
        'scipy>=1.15.0'

# Build with backend/ as the context.
COPY skills/ /opt/skills/
RUN chmod -R a-w /opt/skills \
    && mkdir -p /workspace/files /workspace/artifacts
WORKDIR /workspace
