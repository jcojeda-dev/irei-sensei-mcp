# Imagen base con Python 3.12 sobre Debian (necesario para instalar las
# librerías de sistema que WeasyPrint requiere: Pango, Cairo, GDK-Pixbuf).
FROM python:3.12-slim

# Librerías de sistema para WeasyPrint (generación de PDF).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway/Render inyectan la variable PORT en tiempo de ejecución.
CMD ["sh", "-c", "uvicorn http_api:app --host 0.0.0.0 --port ${PORT:-8000}"]
