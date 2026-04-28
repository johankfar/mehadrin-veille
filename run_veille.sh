#!/bin/bash
# Mehadrin Veille - Cron wrapper
cd /opt/mehadrin-veille
source .env

# Pull latest code (force reset if diverged)
git fetch origin 2>/dev/null
git reset --hard origin/main 2>/dev/null

# Run the veille generator
python3 veille_generator.py >> /var/log/mehadrin-veille.log 2>&1

# Commit and push results
git add veille_live.json veille_data.json 2>/dev/null
if ! git diff --cached --quiet 2>/dev/null; then
    git commit -m "veille: update $(date -u +'%Y-%m-%d %H:%M UTC')" 2>/dev/null
    git push origin main 2>/dev/null || echo "$(date): push failed" >> /var/log/mehadrin-veille.log
fi

