# Deploying TravelGuideMaster to AWS EC2

One Ubuntu instance in the Auckland region running two containers:

```
browser ──HTTPS──▶ web (Caddy)  ── serves the built front end
                     │           ── terminates TLS (Let's Encrypt, automatic)
                     └──/api/*──▶ api (FastAPI + agent pipeline) ──▶ LLM, Open-Meteo, OSRM
                                   │
                                   └── backend/data/cache.sqlite  (POIs, limits, caches)
```

The API has no published port; only Caddy can reach it. Expect about an
hour end to end the first time, most of it waiting for AWS and DNS.

---

## 0. Before you start

- [ ] **Rotate the Model Studio key.** The old one was committed to
      `config.py` as a default. Generate a new one in the Alibaba Cloud
      console and revoke the old.
- [ ] Confirm no key is tracked — this should print nothing (the pattern
      matches the old key's shape, not every word containing "sk-"):
      `git grep -nE "sk-[A-Za-z0-9_.-]{20,}"`
- [ ] Have `backend/data/cache.sqlite` on your machine. It is **not** in git
      (`backend/.gitignore` excludes `data/`) and the app cannot plan
      without it.

## 1. Enable the Auckland region

Console → account menu (top right) → **Account** → **AWS Regions** →
*Asia Pacific (New Zealand)* `ap-southeast-6` → **Enable**. Newer regions
are opt-in; it can take several minutes to become usable. Then switch the
console to that region.

## 2. Launch the instance

EC2 → **Launch instance**:

| Setting | Value |
|---|---|
| Name | `travelguidemaster` |
| AMI | **Ubuntu Server 24.04 LTS** (x86_64, or arm64 if you choose a `t4g` type) |
| Type | `t3.small` (2 GiB). `t3.micro` works with the swap in step 5. |
| Key pair | Create one, download the `.pem` |
| Storage | 20 GiB gp3 |

**Security group** (create new):

| Type | Port | Source | Why |
|---|---|---|---|
| SSH | 22 | **My IP** | administration only |
| HTTP | 80 | 0.0.0.0/0 | certificate issuance, redirect to HTTPS |
| HTTPS | 443 | 0.0.0.0/0 | the site |
| Custom UDP | 443 | 0.0.0.0/0 | HTTP/3 (optional) |

Do **not** open 8000.

## 3. Give it a fixed address

EC2 → **Elastic IPs** → **Allocate** → **Associate** with the instance.
Without this the public IP changes on every stop/start and the DNS name
goes stale.

## 4. Point a domain at it

Add an **A record** for the name you will use, with the **Elastic IP** as
its value — not the instance's current public IP, which changes on every
stop/start.

**Alibaba Cloud domain** (the one in use): Console → **Domains** → check
the domain's status is *Normal*. A domain registered on the China site must
pass real-name verification before it resolves at all; until then it sits
in *serverHold* and records have no effect. No ICP filing is needed — that
applies only to sites hosted in mainland China. Then **Alibaba Cloud DNS**
→ the domain → **Add record**: type `A`, host `travel` (for
`travel.<domain>`) or `@` for the bare domain, value the Elastic IP, TTL
10 minutes.

**DuckDNS** (free alternative): create a subdomain at
<https://www.duckdns.org> and set its IP to the Elastic IP.

Check from your machine:

```powershell
Resolve-DnsName travel.<domain> -Type A   # must return the Elastic IP
```

Do not continue until it does — Caddy's first certificate request fails
otherwise, and repeated failures are rate-limited by Let's Encrypt.

## 5. Prepare the server

```powershell
ssh -i path\to\key.pem ubuntu@<elastic-ip>
```

```bash
# Docker Engine + buildx + Compose, from Docker's own installer
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu && newgrp docker
docker compose version            # v2 or later (Compose jumped from v2 to v5)
docker buildx version             # needed by --build

# 2 GiB of swap: the front-end build is the memory peak, and on 1 GiB it
# can be killed partway. Harmless on larger instances.
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## 6. Get the code and the data onto it

```bash
git clone https://github.com/shi0275206485-dev/NZTravelGuideMaster.git
cd NZTravelGuideMaster && git checkout prod
mkdir -p backend/data          # as ubuntu (uid 1000), so the container can write it
```

A private repository needs a GitHub fine-grained token as the password,
or copy the project with `scp` instead.

From **your machine**, copy the POI cache:

```powershell
scp -i path\to\key.pem backend\data\cache.sqlite ubuntu@<elastic-ip>:~/NZTravelGuideMaster/backend/data/
```

## 7. Configure

```bash
cd ~/NZTravelGuideMaster/deploy
cp .env.example .env && chmod 600 .env
nano .env
```

Set `DOMAIN`, `LLM_API_KEY` (the **new** key), `LLM_BASE_URL` (your
workspace endpoint) and `DEMO_ACCESS_CODE` — several random words, not a
PIN. Leave the limits as they are unless you have a reason.

## 8. Start

```bash
docker compose up -d --build
docker compose logs -f web      # watch for "certificate obtained successfully"
```

The first build takes a few minutes. Ctrl-C leaves the containers running.

## 9. Verify

```bash
DOMAIN=$(grep '^DOMAIN=' .env | cut -d= -f2)
curl -sI https://$DOMAIN | head -5           # 200, strict-transport-security present
curl -s  https://$DOMAIN/api/health; echo    # "poi":6 — anything else, see §12
```

Then in a browser: open the site, generate a plan, enter the access code
when asked. A healthy plan has day-by-day grouping and weather. Check the
API saw no degradation:

```bash
docker compose logs api | grep -Ei "model call failed|falling back|weather lookup failed|routing failed" \
  || echo "no degradations"
```

Finally, confirm the per-address limit sees real visitor addresses, not
Caddy's:

```bash
docker compose exec api python -c "import sqlite3; print(sqlite3.connect('data/cache.sqlite').execute('select bucket,key,count from quota').fetchall())"
```

The keys should be your own public IP. If they are all a `172.x` address,
forwarding is broken and every visitor is sharing one limit.

## 10. Handing out trial codes

`DEMO_ACCESS_CODE` in `.env` is the operator's own code and the switch that
turns access control on — keep it set, and keep it to yourself. Everyone
else gets a code of their own, issued from the server:

```bash
cd ~/NZTravelGuideMaster/deploy
docker compose exec api python -m app.access_codes add "LinkedIn — Jane Doe" --days 14
```

It prints the code once (only a hash is stored) along with a short id.
Send the code; keep the id, which is what revokes it:

```bash
docker compose exec api python -m app.access_codes list
docker compose exec api python -m app.access_codes revoke 3f9a21
```

`list` shows each code's label, status, how many times it has been used and
when it was last used — enough to tell a code being shared around from one
being used as intended. Codes take effect immediately; nothing restarts.
Omit `--days` for a code that does not expire.

Expired and revoked codes stay in the listing rather than disappearing, so
it still says who was given what.

## 11. Operating it

| Task | Command (from `deploy/`) |
|---|---|
| Logs | `docker compose logs -f api` |
| Deploy a change | `git pull && docker compose up -d --build` |
| Restart | `docker compose restart` |
| Stop the site | `docker compose down` — **never** `down -v`, which deletes the certificates |
| Back up data | `scp` `backend/data/cache.sqlite` off the server |

Stopping the EC2 instance when you are not demonstrating saves most of the
cost; the Elastic IP keeps the address. AWS bills for public IPv4
addresses and for Elastic IPs, so check current pricing and release the
address once the course is over.

## 12. When something is wrong

| Symptom | Likely cause | Fix |
|---|---|---|
| Caddy log: certificate / ACME errors | DNS not pointing at the IP, or port 80 closed | Recheck §3–4 and the security group |
| `/api/health` shows no `poi`, plans fail "No cached attractions" | `cache.sqlite` not copied | §6 `scp` |
| 500 with "readonly database" in the API log | `backend/data` owned by root | `sudo chown -R 1000:1000 ~/NZTravelGuideMaster/backend/data` |
| Plans say places are "listed in recommended order" | LLM key or endpoint wrong | `docker compose logs api \| grep "model call failed"` |
| "The weather forecast could not be retrieved" | Open-Meteo unreachable from the instance | outbound network / security group egress |
| Everyone gets 429 after three plans | Client address not forwarded | §9 quota check |

## Known limits of this deployment

- **One instance, no redundancy.** Acceptable for a course demo; a
  restart is a short outage.
- **OSRM's public demo server** provides routing. Its usage policy asks for
  under one request a second — fine at demo volume, not beyond it.
- **`/api/recompute` is not rate-limited.** It costs no model calls, but
  each edit makes an OSRM request, so heavy abuse could get the instance's
  address blocked by the demo server.
- **Request logs have no retention policy.** They stay in Docker's log
  files until removed — worth stating in the COMPX565 risk register.
