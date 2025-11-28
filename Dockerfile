FROM python:3.11-slim-bookworm

# Répertoire de travail
WORKDIR /app

# Copie des fichiers requirements d'abord (pour le cache Docker)
COPY requirements.txt .

# Installation des dépendances Python
RUN pip install --no-cache-dir -r requirements.txt

# Copie de l'application
COPY app.py .

# Variables d'environnement
ENV PORT=5000

# Exposition du port
EXPOSE 5000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

# Démarrage avec Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120", "app:app"]
