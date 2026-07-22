FROM python:3.12-slim

WORKDIR /app

# تثبيت الاعتماديات أولاً للاستفادة من طبقات التخزين المؤقت
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# BOT_TOKEN يُمرّر كمتغير بيئة عند التشغيل
CMD ["python", "main.py"]
