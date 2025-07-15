FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Copy files
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY components ./components
COPY main.py .

#COPY ./keys ./keys
#COPY backfill_runner.sh .

ENTRYPOINT ["python", "main.py"]
