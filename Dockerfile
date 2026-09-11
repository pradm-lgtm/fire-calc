# The approval page, and the weekly job that fills it. The submitter stays on
# your Mac, where the logged-in browser session lives, and polls this over
# HTTPS - so the Mac is never reached from outside.
FROM python:3.12-slim

WORKDIR /app

# The page
COPY webapp.py store.py cloud_auth.py ./
# The weekly job and everything it reaches for
COPY run_weekly.py sleeper_client.py source_discovery.py expert_extract.py \
     expert_waivers.py waiver_analyzer.py claim_safety.py cloud_client.py ./
COPY sources.json selectors.json never_drops.json ./

# Proposals live on a mounted volume so a redeploy does not lose them.
ENV FANTASY_DB=/data/fantasy.db
VOLUME /data
EXPOSE 8777

# Fail at build time rather than in production if a file was forgotten.
RUN python3 -c "import webapp, run_weekly, expert_waivers, source_discovery"

# No dependencies to install: page and job are standard library only.
CMD ["python3", "webapp.py", "--host", "0.0.0.0", "--port", "8777", \
     "--db", "/data/fantasy.db"]
