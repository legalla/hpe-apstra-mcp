FROM python:3.12-slim

# Non-root user for security
RUN useradd -m -u 1000 mcp
WORKDIR /app

# Dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Source code
COPY apstra_client.py apstra_auth.py apstra_token_manager.py server.py ./

# Mount point of the secrets volume, owned by the non-root user.
RUN mkdir -p /app/secrets && chown -R mcp:mcp /app/secrets

USER mcp

EXPOSE 8000

ENTRYPOINT ["python", "server.py"]
