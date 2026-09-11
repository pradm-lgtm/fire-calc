# The approval page only. The submitter stays on your Mac, where the
# logged-in browser session lives, and polls this over HTTPS.
FROM python:3.12-slim

WORKDIR /app
COPY webapp.py store.py cloud_auth.py ./

# Proposals live in a mounted volume so they survive a redeploy.
ENV FANTASY_DB=/data/fantasy.db
VOLUME /data
EXPOSE 8777

# No dependencies to install: the page is standard library only.
CMD ["python3", "webapp.py", "--host", "0.0.0.0", "--port", "8777", \
     "--db", "/data/fantasy.db"]
