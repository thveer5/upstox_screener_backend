# Deploying Algo Upstox to Production

**Stack:** Oracle Cloud OR Google Cloud (backend) + Vercel (frontend).
Cost: ₹0/month forever.

Pick one provider for Section A, then continue with sections B–F (same for both):
- [Section A1: Oracle Cloud](#a-oracle-cloud--provision-the-backend-vm) — better specs, Mumbai region, signup can be flaky
- [Section A2: Google Cloud](#a2-google-cloud-free-tier--alternative-to-oracle) — easier signup, but US-only for always-free

---

## A. Oracle Cloud — Provision the backend VM

### A.1 Sign up
1. Create Oracle Cloud account at https://www.oracle.com/cloud/free/
2. Choose **Home Region: Mumbai (ap-mumbai-1)** or **Hyderabad (ap-hyderabad-1)**
   - This is permanent — pick India for lowest Upstox API latency
3. Credit-card verification (no charge — just identity)
4. Wait ~5 min for account activation

### A.2 Create the Always-Free Ampere VM
1. Console → **Compute → Instances → Create instance**
2. **Name:** `algo-upstox`
3. **Image and shape:** Click *Edit* → Change shape → pick **Ampere → VM.Standard.A1.Flex**
   - Set **OCPU = 2, Memory = 12 GB** (within Always Free limits)
   - Image: **Canonical Ubuntu 22.04**
4. **Networking:** keep defaults (public subnet)
5. **SSH keys:** generate a new keypair → **download the private key** (you'll need it)
6. **Boot volume:** keep default 50 GB
7. Click **Create**. Wait ~2 minutes for state = RUNNING.
8. Note the **Public IP address** shown on the instance page — this is your permanent static IP.

### A.3 Open ports 80 + 443
By default Oracle's VCN firewall blocks all inbound except port 22.
1. Console → **Networking → Virtual Cloud Networks** → your VCN → **Public Subnet** → **Default Security List**
2. **Add Ingress Rules** for:
   - Source CIDR `0.0.0.0/0`, TCP, **Destination Port 80**
   - Source CIDR `0.0.0.0/0`, TCP, **Destination Port 443**

### A.4 Also open at the Ubuntu firewall level
SSH in (use the downloaded key):
```bash
ssh -i <path-to-private-key> ubuntu@<your-vm-public-ip>
```
Then:
```bash
sudo iptables -I INPUT -p tcp --dport 80  -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

Skip to **Section B** to continue.

---

## A2. Google Cloud Free Tier — alternative to Oracle

### A2.1 Sign up
1. https://cloud.google.com/free → **Get started for free**
2. Sign in with a Google account
3. Add a card for verification (no charge — Google explicitly says "We won't charge you unless you upgrade to a paid account")
4. You'll get **$300 trial credit** + always-free tier

### A2.2 Pick a free-tier region — **CRITICAL**
The always-free e2-micro is **only free in these 3 US regions**:
- `us-west1` (Oregon) — best latency for India among the three
- `us-central1` (Iowa)
- `us-east1` (South Carolina)

**Picking any other region = you get billed.** Always-free does not exist in asia-south1 (Mumbai).

### A2.3 Create the e2-micro VM
1. Console → **Compute Engine → VM instances → Create instance**
2. **Name:** `algo-upstox`
3. **Region:** `us-west1` (Oregon)
4. **Zone:** `us-west1-a`
5. **Machine type:** **Series E2 → e2-micro** (1 vCPU shared, 1 GB RAM) ← must be exactly this
6. **Boot disk:** Change → **Ubuntu 22.04 LTS, 30 GB Standard persistent disk** (free tier ceiling is 30 GB)
7. **Firewall:** check both ☑ **Allow HTTP traffic** and ☑ **Allow HTTPS traffic**
8. Click **Create**. Wait ~30 sec for state = RUNNING.

### A2.4 Reserve a static external IP (free)
By default the VM gets an ephemeral IP that changes on stop/start.
1. Console → **VPC network → IP addresses**
2. Find your VM's external IP in the list → click **Promote to static**
3. Give it any name → Reserve
4. ✅ Free as long as it stays attached to the running VM

### A2.5 SSH in
Easiest: click the **SSH** button next to your VM in the console — opens an in-browser terminal.
Or use `gcloud compute ssh algo-upstox --zone us-west1-a` from local with gcloud CLI installed.

Skip to **Section B**.

---

## B. Backend deployment

### B.1 Install dependencies
```bash
sudo apt update && sudo apt -y install python3-venv python3-pip git curl
```

### B.2 Install Caddy (for HTTPS reverse proxy)
```bash
sudo apt -y install debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt -y install caddy
```

### B.3 Clone the repo + install Python deps
```bash
cd /home/ubuntu
git clone <your-repo-url> algo_upstox
# or: rsync from your laptop
cd algo_upstox/backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### B.4 Configure the backend env
```bash
cp ../deploy/.env.production.example .env
nano .env
```
Set:
- `UPSTOX_API_KEY` + `UPSTOX_API_SECRET` (from Upstox Developer Console)
- `UPSTOX_REDIRECT_URI` — use your VM IP via `sslip.io` for instant HTTPS without a domain:
  - Example for VM IP `203.0.113.45`: `https://203-0-113-45.sslip.io/auth/callback`
  - (`sslip.io` resolves `203-0-113-45.sslip.io` to `203.0.113.45` automatically)
- `FRONTEND_ORIGIN` — leave as Vercel placeholder for now, you'll fill in after deploying frontend

### B.5 Hostname for HTTPS
You have three options for the HTTPS hostname:

| Option | Setup time | Cost | Hostname looks like |
|--------|-----------|------|---------------------|
| **sslip.io (Recommended)** | 0 min | Free | `203-0-113-45.sslip.io` |
| DuckDNS | 5 min, signup | Free | `myapp.duckdns.org` |
| Real domain | 10 min, ~₹800/yr | Paid | `api.mydomain.com` |

Going with **sslip.io** — no signup, just works. Replace `<YOUR-HOSTNAME>` in the Caddyfile.

### B.6 Set up Caddy
```bash
sudo cp ~/algo_upstox/deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile
# Replace <YOUR-HOSTNAME> with e.g. 203-0-113-45.sslip.io
sudo systemctl restart caddy
sudo systemctl enable caddy
```
First start takes ~30 sec while Caddy fetches a Let's Encrypt cert. Check:
```bash
sudo journalctl -u caddy -n 30 --no-pager
```
You should see *"certificate obtained successfully"*.

### B.7 Set up the systemd service for uvicorn
```bash
sudo cp ~/algo_upstox/deploy/algo-upstox.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now algo-upstox
```
Verify:
```bash
sudo systemctl status algo-upstox
curl -s https://<YOUR-HOSTNAME>/
```
The second command should return the Algo Upstox landing HTML.

---

## C. Update Upstox app settings

In https://account.upstox.com/developer/apps → your app:
1. **Static IPs** dialog → set **Primary IP** to your **Oracle VM public IP**
2. **Redirect URL** → `https://<YOUR-HOSTNAME>/auth/callback`
   (must EXACTLY match `UPSTOX_REDIRECT_URI` in `.env`)
3. Save

> Reminder: changing app settings invalidates any existing access token — you'll need to log in fresh after deploy.

---

## D. Vercel — deploy the frontend

### D.1 Push the repo to GitHub
If not already on GitHub:
```bash
# on your laptop
cd d:\algo_upstox
git init
git add .
git commit -m "Initial commit"
gh repo create algo-upstox --private --source=. --push
```

### D.2 Import to Vercel
1. https://vercel.com/new → **Import Git Repository** → select `algo-upstox`
2. **Root Directory:** `frontend`
3. **Framework Preset:** Vite (auto-detected)
4. **Build Command:** `npm run build` (default)
5. **Output Directory:** `dist` (default)
6. **Environment Variables:**
   - `VITE_API_BASE` = `https://<YOUR-BACKEND-HOSTNAME>` (e.g. `https://203-0-113-45.sslip.io`)
7. Click **Deploy**. Wait ~1 minute.
8. Note your Vercel URL — looks like `algo-upstox.vercel.app`

### D.3 Tell the backend about the Vercel URL
SSH back into the VM:
```bash
nano /home/ubuntu/algo_upstox/backend/.env
# Update:
FRONTEND_ORIGIN=https://algo-upstox.vercel.app,http://localhost:5173
sudo systemctl restart algo-upstox
```

---

## E. First-time sanity check

1. Open `https://algo-upstox.vercel.app` — should see the dashboard
2. Sidebar shows OAuth status. Click **Login** → completes OAuth on the backend → redirects back to the Vercel app
3. Visit `https://<YOUR-BACKEND-HOSTNAME>/me` directly → should return your Upstox profile JSON
4. Bootstrap the tv session: in the dashboard, click **Bootstrap** → paste a fresh cookie from tv.upstox.com → Save
5. Holdings page should populate
6. Market Watch should load

---

## F. Operational notes

- **OAuth re-login required daily** — Upstox tokens reset at 3:30 AM IST. Just click Login in the sidebar.
- **tv_session cookie re-bootstrap every ~24h** — when refresh_token expires (sidebar pill shows time remaining). Capture fresh cookie from tv.upstox.com browser tab → Bootstrap.
- **Static IP is permanent** — Oracle/GCP VM IP doesn't change. No more UDAPI1154.
- **GCP free tier egress limit** — only **1 GB outbound to internet per month**. Each screener call returns ~50 KB → ~20,000 calls/month before billing kicks in (~₹10/GB after). Should be plenty for personal use, but watch the GCP billing dashboard the first month to see your usage.
- **GCP latency** — ~250 ms from US to Upstox in India. Fine for the dashboard; not for HFT.
- **Backups** — `tokens.json` + `tv_session.json` live in `~/algo_upstox/backend/`. Worth backing up if you want zero-downtime token continuity across VM rebuilds, but a fresh login regenerates both in <1 min.
- **Updates** — to deploy new code: `cd ~/algo_upstox && git pull && cd backend && .venv/bin/pip install -r requirements.txt && sudo systemctl restart algo-upstox`. Frontend redeploys automatically when you push to GitHub.
- **Logs**: `sudo journalctl -u algo-upstox -f` (backend), `sudo journalctl -u caddy -f` (caddy).
