FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/btc
COPY service/requirements.txt /opt/btc/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY service/btc_node /opt/btc/btc_node
ENTRYPOINT ["python", "-m", "btc_node"]
