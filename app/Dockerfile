FROM python:3.11-slim

# Install curl for healthcheck
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first — Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all app code
COPY . .

# Create required directories
RUN mkdir -p /data /events /config /dashboard

EXPOSE 8000

# main:app because WORKDIR is /app, not app.main
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]