FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY ecm.py rls.py db.py activities.py workflow.py worker.py ./

CMD ["python", "worker.py"]
