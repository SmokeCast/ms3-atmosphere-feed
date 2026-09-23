FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
ENV HOST=0.0.0.0 PORT=8083
EXPOSE 8083
CMD ["python", "main.py"]
