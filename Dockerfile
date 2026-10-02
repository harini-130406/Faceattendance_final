# ==============================================================================
# Smart Attendance System - Production Managed Container
# Compatible with Railway, Render, Fly.io, and any Docker runtime
# ==============================================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install native Linux C++ build tools & linear algebra headers for dlib compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    pkg-config \
    libopenblas-dev \
    liblapack-dev \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir --upgrade pip wheel && \
    pip install --no-cache-dir "setuptools==80.10.2" && \
    pip install --no-cache-dir -r requirements.txt

# Copy project code
COPY . /app/

# Collect static files into staticfiles/
RUN python manage.py collectstatic --noinput

EXPOSE 8080

# Automated migration, persistent volume media seeding, and production Gunicorn startup
CMD ["sh", "-c", "python manage.py migrate && python manage.py sync_media_to_volume && gunicorn Attendence_System.wsgi:application --bind 0.0.0.0:${PORT:-8080} --workers 2 --threads 2 --timeout 60"]
