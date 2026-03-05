ARG AIRFLOW_VERSION=3.1.7
FROM apache/airflow:${AIRFLOW_VERSION}

# Create airflow group and user matching Bitnami's defaults
USER root

# Airflow 2 doesn't have git lul
RUN apt-get update && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/

# Set working directory
WORKDIR /opt/airflow

# Copy project files
COPY . .
USER airflow
# Install uv and ensure pip is up to date
RUN pip install --upgrade pip \
    && pip install --no-cache-dir uv

# Create a virtual environment using system python3 interpreter
RUN uv venv --python python3 /opt/airflow/venv

# Prepend venv bin to PATH so uv, pip, and python are used
ENV PATH="/opt/airflow/venv/bin:${PATH}"

# Install package into venv
RUN --mount=type=bind,source=.git,target=.git,readonly \
    uv pip install -e . --no-cache
