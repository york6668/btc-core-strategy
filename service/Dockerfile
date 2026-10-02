FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/btc
COPY service/requirements.txt /opt/btc/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY service/btc_node /opt/btc/btc_node
ENTRYPOINT ["python", "-m", "btc_node"]
