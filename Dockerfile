FROM python:3.13-slim
LABEL org.opencontainers.image.title="Sal0 Block" \
      org.opencontainers.image.description="Independent web interface for iSponsorBlockTV" \
      org.opencontainers.image.source="https://github.com/Sal0-Apps/Sal0-Block" \
      org.opencontainers.image.licenses="GPL-3.0-only"
WORKDIR /opt/sal0-panel
COPY panel /opt/sal0-panel
ENV iSPBTV_data_dir=/app/data SAL0_PORT=7860 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD ["python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/healthz', timeout=3).read()"]
ENTRYPOINT ["python3", "-u", "/opt/sal0-panel/server.py"]
