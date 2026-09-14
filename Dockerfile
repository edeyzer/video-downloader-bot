FROM python:3.11-slim

WORKDIR /app

# Tizim paketlarini o'rnatish (ffmpeg video bilan ishlash uchun kerak bo'ladi)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Kutubxonalarni o'rnatish
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Loyiha fayllarini nusxalash
COPY . .

# Botni ishga tushirish
CMD ["python", "app/main.py"]