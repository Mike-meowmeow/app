# Setting up the AI server

This guide turns one computer into a **headless server** (no monitor or desktop, managed over SSH).
That one machine runs everything: both websites, MySQL, the private files, and the AI runtime
(OCR, spaCy, SBERT).

**What this guide covers and what it does not:**

- Steps 1 to 7 prepare the machine and install the AI runtime, then prove it works with
  `deploy/ai_check.py`. You can do all of this now.
- The recruitment system itself is installed with `deploy/DEPLOY.md` (step 4 points there).
- The AI features inside the application are built in **Stage 10**. The first part exists: the
  background worker, document text extraction with OCR, and HR review of the proposed items
  (step 8). Qualification checks and SBERT competency matching come next.

Allow about half a day. Commands run on the server unless marked **(your Windows computer)**.

---

## 0. Before you start

**Hardware**

| | Minimum | Recommended |
| --- | --- | --- |
| Memory | 8 GB | 16 GB |
| Processor | 4 cores, 64-bit | 6 to 8 cores |
| Storage | 128 GB SSD | 256 GB or more SSD |
| Network | Wired Ethernet | Wired Ethernet |
| GPU | Not needed | Optional (NVIDIA only, step 5.9) |

About 4 to 6 GB of memory is used when everything is running. ClamAV takes about 1.2 GB, MySQL
0.5 to 1 GB, both websites about 0.4 GB, and the AI models 1.5 to 2.5 GB.

**You also need:**

- a USB drive of 8 GB or more (it will be erased);
- your Windows computer on the same network, for SSH;
- a decision on how the server reaches the internet (step 9). Rented cloud servers skip steps 1
  and 2;
- a domain with two names (for example `apply.<domain>` and `staff.<domain>`) and an SMTP email
  account, for DEPLOY.md.

**Data rule:** use only made-up test applicants until the school's Data Privacy Officer approves
where real applicant data is stored.

---

## 1. Install Ubuntu Server 24.04 LTS

Use **Ubuntu Server 24.04 LTS** (for example `ubuntu-24.04.5-live-server-amd64.iso`), not the
desktop edition. DEPLOY.md expects its Python 3.12.

**First time with a server?** Follow **`deploy/SERVER-INSTALL.md`** instead of steps 1 and 2. It
covers the same ground screen by screen, including a practice run in VirtualBox, then returns here
at step 3.

1. **(your Windows computer)** Download the Ubuntu Server 24.04 LTS ISO from ubuntu.com. Write it
   to the USB drive with Rufus or balenaEtcher.
2. **Firmware settings** (press F2, F10, or Del at power-on):
   - Turn on **Restore on AC power loss** (also called *Power On after power failure*), so the
     server starts again by itself after an outage.
   - Turn on virtualization if offered (harmless and sometimes needed later).
   - Boot from the USB drive.
3. **Installer choices:**
   - Language English; keyboard as yours.
   - Choose **Ubuntu Server** (not minimized).
   - Network: wired Ethernet with automatic (DHCP) address for now.
   - Storage: use the entire disk. Accept LVM; leave encryption off unless you can type the
     passphrase at every boot.
   - Your name, server name `evsu-server`, a username such as `admin1`, and a strong password.
   - **Install OpenSSH server: yes.** Do not import SSH keys here.
   - Featured snaps: select none.
4. Reboot, remove the USB drive, and sign in at the console once. Find the server's address:
   ```bash
   ip -4 addr show | grep inet
   ```
   Note the address that is not `127.0.0.1`, for example `192.168.1.50`.
5. **Fix that address** in your router: create a *DHCP reservation* (also called *static lease*)
   for the server. It is simpler and safer than editing network files on the server.

From now on you can unplug the monitor and keyboard.

---

## 2. Connect from your Windows computer with SSH keys

**(your Windows computer, PowerShell)**

```powershell
ssh-keygen -t ed25519            # press Enter for the default file; choose a passphrase
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh admin-mike@192.168.1.107 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
ssh admin-mike@192.168.1.107          # should sign in without the account password
```

Replace `admin1` and the address with yours. **On the server**, once key sign-in works, turn off
password sign-in for SSH:

```bash
echo -e "PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no" | sudo tee /etc/ssh/sshd_config.d/10-evsu.conf
sudo systemctl restart ssh
```

Keep your current SSH window open and test in a second window before closing it. If you lock
yourself out, sign in at the console and delete that file.

---

## 3. Base system

```bash
# Updates, and automatic security updates
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades      # answer Yes

# Time zone and clock synchronization
sudo timedatectl set-timezone Asia/Manila
timedatectl                                          # must show: System clock synchronized: yes
```

**The clock matters:** staff two-factor codes are based on time. If the server clock is off by more
than about 30 seconds, every code is refused. If `synchronized` says `no`, run
`sudo systemctl restart systemd-timesyncd` and check again.

```bash
# Never sleep (a server has no one to wake it)
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target

# Laptop used as a server only: keep running with the lid closed
sudo sed -i 's/^#\?HandleLidSwitch=.*/HandleLidSwitch=ignore/' /etc/systemd/logind.conf
sudo sed -i 's/^#\?HandleLidSwitchExternalPower=.*/HandleLidSwitchExternalPower=ignore/' /etc/systemd/logind.conf
sudo systemctl restart systemd-logind

# Swap: a safety margin if memory runs short (4 GB)
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/90-evsu.conf && sudo sysctl --system

# Firewall: SSH and web only
sudo ufw default deny incoming
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable

# Block repeated SSH password guessing
sudo apt install -y fail2ban
```

Check: `free -h` shows the swap, and `sudo ufw status` shows the three rules.

---

## 4. Install the recruitment system

Follow **`deploy/DEPLOY.md` sections 1 to 6**. Its firewall commands repeat step 3 and are
harmless. Production settings to check in `.env`:

- `DJANGO_DEBUG=False`;
- `STAFF_TWO_FACTOR_REQUIRED=True` (the default in production);
- `PUBLIC_SITE_URL` and `STAFF_SITE_URL` with your two addresses;
- your SMTP account.

If the server is at home behind a router, read step 9 first, because certificates need the sites to
be reachable from the internet.

Then **limit MySQL's memory** so it leaves room for the AI models:

```bash
printf '[mysqld]\ninnodb_buffer_pool_size = 1G\nmax_connections = 60\n' | sudo tee /etc/mysql/mysql.conf.d/zz-evsu.cnf
sudo systemctl restart mysql
```

Before continuing, finish DEPLOY.md section 7 (the checks) so you know the non-AI system works.

---

## 5. Install the AI runtime

Everything below runs as the `evsu` service account inside the project's virtual environment, so
the AI code (Stage 10) can use the application's database and files directly.

### 5.1 System packages

```bash
sudo apt install -y tesseract-ocr tesseract-ocr-eng fonts-dejavu-core
tesseract --version          # 5.x
```

Optional: `tesseract-ocr-fil` adds Filipino, for documents written in Filipino.

### 5.2 Folder for the models

```bash
sudo mkdir -p /srv/evsu-models
sudo chown evsu:evsu /srv/evsu-models
sudo chmod 750 /srv/evsu-models
```

### 5.3 PyTorch, CPU-only

```bash
cd /srv/evsu-recruitment
sudo -u evsu .venv/bin/pip install torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu
```

The CPU-only build is a fraction of the size of the default GPU build. With an NVIDIA GPU, use step
5.9 instead.

### 5.4 The AI packages

```bash
sudo -u evsu .venv/bin/pip install -r requirements-ai.txt
sudo -u evsu .venv/bin/python -m spacy download en_core_web_sm-3.8.0 --direct
sudo -u evsu .venv/bin/python -m pip check        # must say: No broken requirements found.
```

### 5.5 Download the SBERT model once

```bash
sudo -u evsu env HF_HOME=/srv/evsu-models .venv/bin/python -c \
  "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"
du -sh /srv/evsu-models          # roughly 100 MB
```

`all-MiniLM-L6-v2` is small and fast, a good start for a CPU server. Choosing the final model is a
Stage 10 task, measured against HR's judgments. Alternatives to compare then:
`all-mpnet-base-v2` (more accurate, slower) and `paraphrase-multilingual-MiniLM-L12-v2` (handles
Filipino and English).

### 5.6 Settings: run offline

Add to `/srv/evsu-recruitment/.env`:

```ini
# AI runtime (deploy/AI-SERVER.md)
HF_HOME=/srv/evsu-models
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
SBERT_MODEL=sentence-transformers/all-MiniLM-L6-v2
SPACY_MODEL=en_core_web_sm
```

The offline settings make the models load only from the server's disk. Applicant text is never sent
anywhere, and the AI keeps working without internet. To download another model later, run step 5.5
again; `env` there overrides the offline setting for that one command.

### 5.7 Restart the sites

The sites do not use the AI packages, but restart them so they read the new `.env`:

```bash
sudo systemctl restart evsu-recruitment@public evsu-recruitment@staff
```

### 5.8 Record and pin the versions

After step 6 passes, list the exact versions:

```bash
sudo -u evsu .venv/bin/pip freeze | grep -i -E '^(torch|pytesseract|pdfplumber|pypdfium2|pdfminer|spacy|en-core-web-sm|sentence-transformers|transformers|tokenizers|huggingface-hub|numpy|scikit-learn|scipy)=='
tesseract --version | head -1
```

Send the output to the project, so `requirements-ai.txt` gets exact versions (`package==x.y.z`).
Also record them in the Development Logs. Every later install then uses the same tested set.

### 5.9 Optional: NVIDIA GPU

Only with an NVIDIA graphics card. The planned models run well on a CPU, so skip this if unsure.

```bash
sudo ubuntu-drivers install
sudo reboot
nvidia-smi                          # shows the card and driver
cd /srv/evsu-recruitment
sudo -u evsu .venv/bin/pip install --force-reinstall torch     # default build includes CUDA
sudo -u evsu .venv/bin/python -c "import torch; print(torch.cuda.is_available())"   # True
```

---

## 6. Check that the AI runtime works

```bash
cd /srv/evsu-recruitment
sudo -u evsu .venv/bin/python deploy/ai_check.py
```

Expected output (times and numbers vary):

```
PASS  Python version - 3.12.x
PASS  Tesseract OCR on an image - Tesseract 5.x
PASS  PDF text extraction with page positions - ... words with coordinates
PASS  Scanned PDF: render the page, then OCR - page rendered at 300 dpi (...)
PASS  spaCy English model - en_core_web_sm ...; entities: ...
PASS  SBERT sentence similarity - ... on cpu; related 0.5x > unrelated 0.0x
PASS  Models load without internet (offline mode) - models in /srv/evsu-models
PASS  Memory used by this check - peak ... MB

8 of 8 checks passed.
```

The check uses only made-up sample text, never the database or uploaded files. Note the peak memory
line. Together with `free -h` while the sites are running, it shows how much room is left for
Stage 10.

---

## 7. Keep the server healthy

| Task | Command |
| --- | --- |
| Status of all services | `systemctl status evsu-recruitment@public evsu-recruitment@staff mysql nginx clamav-daemon` |
| Recent errors of a site | `journalctl -u evsu-recruitment@staff -n 100 --no-pager` |
| Memory and swap | `free -h` |
| Disk space | `df -h /` (keep at least 20% free) |
| Clock synchronized (two-factor codes) | `timedatectl` |
| Scheduled backups and certificates | `systemctl list-timers` |
| Updates (monthly, after a backup) | `sudo apt update && sudo apt full-upgrade -y`, then `sudo reboot` if asked |

**Backups off the machine.** The nightly backups in `/srv/evsu-recruitment-backups` are on the
same disk. Copy the latest set to another computer or a USB drive weekly
**(your Windows computer)**:

```powershell
scp -r admin1@192.168.1.50:/srv/evsu-recruitment-backups/<latest set> D:\evsu-backups\
```

This needs read permission: add your user with `sudo usermod -aG evsu admin1` and sign in again.
Keep the copies private: they contain account data and the two-factor keys. `deploy/RECOVERY.md`
explains restoring.

---

## 8. The AI worker

The AI runs as **one more background service** on this machine. It reads queued jobs from the
database, reads verified documents (digital text, or OCR for scanned pages), and stores proposed
items for HR review. It never runs inside a web request, so a slow OCR job never slows a page.
Results are advisory: HR accepts, corrects, links, or rejects each item, and nothing changes an
application's status, shortlist, or roster by itself.

```bash
cd /srv/evsu-recruitment
sudo install -m 644 deploy/systemd/evsu-ai-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now evsu-ai-worker
journalctl -u evsu-ai-worker -n 20 --no-pager      # "AI worker ... started."
```

Then set `AI_EXTRACTION_ENABLED=True` in `.env` and restart the sites. From then on, verifying a
document queues it; HR sees the result in the **AI** column of the application's documents.

The service runs with `Nice=10` (the websites get the processor first) and `MemoryMax=3G` (it is
restarted rather than crowding out MySQL). It processes one job at a time; a job is retried up to
`AI_MAX_ATTEMPTS` times, and a run whose worker stopped is picked up again after
`AI_JOB_STALE_SECONDS`.

**Try it with made-up documents:**

```bash
sudo -u evsu .venv/bin/python manage.py make_ai_samples /tmp/samples
sudo -u evsu .venv/bin/python manage.py ai_extract_file /tmp/samples/TOR_transcript.pdf --type TOR
```

`ai_extract_file` prints what the AI would propose for one file, without touching the database.
Upload the same files as a test applicant to try the whole flow.

---

## 9. Reaching the internet

The applicant site must be public, and both sites need HTTPS certificates. Choose one:

| Option | Works when | Notes |
| --- | --- | --- |
| **A. Rented cloud server** | Always | Most reliable for the defense; no router needed. Skip steps 1 and 2 |
| **B. Router port forwarding** | Your internet has a real public IP | Forward ports 80 and 443 to the server's address. Many home connections in the Philippines use CGNAT (shared addresses), where this cannot work |
| **C. Cloudflare Tunnel** | Behind CGNAT too | Free; needs your domain on Cloudflare. No router changes; the server connects out to Cloudflare |

**Check for CGNAT (option B):**
- Compare the WAN address on your router's status page with the address shown by a "what is my IP"
  website.
- If they differ, or the router's address starts with `100.64` to `100.127`, you are behind CGNAT.
  Use option A or C, or ask your provider for a public IP.

**Option C needs changes.** With Cloudflare Tunnel, visitors' addresses arrive through Cloudflare.
The Nginx files and `TRUSTED_PROXIES` must then take the real address from Cloudflare's header, or
sign-in limits and the audit trail would see one address for everyone. Ask for the Nginx files to be
adapted before you start.

**Power and internet at home:** a small UPS keeps the server running through short outages, and
"Restore on AC power loss" (step 1) brings it back after long ones. For the defense, also keep the
offline backup laptop ready.

---

## 10. Troubleshooting

| Problem | What to do |
| --- | --- |
| `ssh: connect ... timed out` | Server off or address changed: check the router's DHCP reservation (step 1.5); `sudo ufw status` must allow OpenSSH |
| `Permission denied (publickey)` | The key was not copied: repeat step 2 while password sign-in is still on, or fix it at the console |
| Every two-factor code is refused | Server clock: `timedatectl` must show `synchronized: yes` (step 3); also check the phone's automatic time |
| `apt` downloads crawl, stall, or fail with `Unable to connect to ph.archive.ubuntu.com` | The country mirror chosen by the installer is unreliable. Switch to the main one: `sudo sed -i 's#http://ph.archive.ubuntu.com/ubuntu/#http://archive.ubuntu.com/ubuntu/#' /etc/apt/sources.list.d/ubuntu.sources`, then `sudo apt update` |
| `pip` is killed or the server freezes while installing | Not enough memory: confirm the swap from step 3 is on (`free -h`), then retry |
| `TesseractNotFoundError` | `sudo apt install tesseract-ocr tesseract-ocr-eng` |
| `Can't find model 'en_core_web_sm'` | Run the `spacy download` command in step 5.4 as `evsu` |
| SBERT check fails with a network or `offline` error | The model was not downloaded to `/srv/evsu-models`: repeat step 5.5 |
| OCR check misses words | Install `fonts-dejavu-core` (step 5.1) and run again |
| Sites slow while the AI runs (after Stage 10) | `free -h` and `top`; lower worker concurrency, keep `MemoryMax`, or add memory |
| Server off after a power cut | Turn on "Restore on AC power loss" in the firmware (step 1.2) |

When you finish, record in the Development Logs:
- the hardware;
- the Ubuntu, MySQL, and Tesseract versions;
- the pinned versions from step 5.8;
- the `ai_check.py` output.
