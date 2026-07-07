FROM python:3.12-slim

WORKDIR /app

# Instalar dependencias del sistema mínimas
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copiar e instalar dependencias primero (cache de capas)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código de la app
COPY . .

# Crear directorio de datos (montado como volumen en producción)
RUN mkdir -p /app/data

EXPOSE 8700

# Nota: el CLI de claude no vive en el contenedor.
# El coach IA degrada automáticamente al fallback si CLAUDE_CLI no está disponible.
# Para activar el coach: montar/instalar claude y setear CLAUDE_CLI en .env.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8700"]
