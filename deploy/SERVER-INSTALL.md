# Installing Ubuntu Server, step by step

A beginner's walkthrough for `ubuntu-24.04.5-live-server-amd64.iso`, from the downloaded file to a
server you control from your Windows computer. It expands steps 1 and 2 of `deploy/AI-SERVER.md`.
When you finish, continue there at **step 3**.

**What "headless" means:** the server has no desktop, mouse, or windows, only a text screen. You need
a monitor and keyboard once, for the installation. After that you control it from your Windows
computer over **SSH**, a secure connection that opens the server's terminal inside PowerShell or
VS Code.

```
Your Windows computer                     Server (no monitor)
+----------------------+   home network   +----------------------+
| PowerShell / VS Code | ------ SSH ----> | Ubuntu Server 24.04  |
| you type commands    | <---- output --- | runs them            |
+----------------------+                  +----------------------+
```

Nothing here can harm your Windows computer or the project. If the server goes wrong, reinstalling
takes about 20 minutes.

---

## Part A. Check the downloaded file

**(your Windows computer, PowerShell, in the folder with the ISO)**

```powershell
Get-FileHash .\ubuntu-24.04.5-live-server-amd64.iso -Algorithm SHA256
```

Open `https://releases.ubuntu.com/24.04/SHA256SUMS` and find the line ending in
`ubuntu-24.04.5-live-server-amd64.iso`. Its long code must equal the `Hash` shown by PowerShell
(capital or small letters do not matter). If they differ, the download is damaged: download it again.

---

## Part B. Choose where to install

| | B1. Practice in VirtualBox | B2. The real server machine |
| --- | --- | --- |
| What it is | A pretend computer in a window on your laptop | The computer that will run the system |
| Risk | None; delete it when done | **Erases that computer's whole disk** |
| Good for | Learning steps 1 to 3 before the real one | Testing and the defense |

**Recommended:** do B1 once, then B2. The installer screens (Part C) are the same in both.

### B1. Practice in VirtualBox

1. Install **VirtualBox** (free, virtualbox.org, "Windows hosts"). If it asks to install network
   drivers, allow it.
2. Click **New**:
   - **Name:** `evsu-server`. **ISO Image:** choose the downloaded ISO.
   - Tick **Skip Unattended Installation**. Otherwise VirtualBox installs Ubuntu by itself and you
     will not see the choices in Part C.
   - **Memory:** 4096 MB (8192 MB if your laptop has 16 GB or more and you want to try the AI
     runtime). **Processors:** 2.
   - **Hard disk:** create a new one, 60 GB (it only uses space as it fills).
   - Click **Finish**.
3. Select the machine, then **Settings > Network > Adapter 1 > Attached to: Bridged Adapter**. For
   **Name**, choose the network card you use now (your Wi-Fi or Ethernet). This gives the virtual
   server its own address on your home network, as a real server would have.
4. Click **Start**. The window shows the installer: go to Part C.

Tip: when you click inside the VirtualBox window, it captures your keyboard. The **right Ctrl** key
releases it.

### B2. The real server machine

**You need:** a USB drive of 8 GB or more (it will be erased), a monitor, a keyboard, and an
Ethernet cable to your router. Back up anything on the server machine first: its disk is erased.

**Make the USB drive (your Windows computer):**

1. Download **Rufus** from rufus.ie (the portable version is fine) and run it.
2. **Device:** your USB drive. Check the name and size carefully; the wrong drive gets erased.
3. **Boot selection:** click **SELECT** and choose the ISO.
4. **Partition scheme:** GPT. **Target system:** UEFI (non CSM). Leave the rest.
5. Click **START**. If asked, choose **Write in ISO Image mode (Recommended)**, then OK to the
   erase warning. Wait for **READY**, then close Rufus.

If the server later will not start from this drive, make it again and choose **DD Image mode**.

**Firmware settings on the server machine:**

1. Turn it on and press the setup key right away, repeatedly. Common keys: **F2** or **Del**
   (most desktops), **F10** (HP), **F1** (Lenovo ThinkCentre). The screen at power-on usually says
   which.
2. Look for these (names differ by brand; skip any you cannot find):
   - **Restore on AC Power Loss** / **AC Recovery** / **After Power Failure**: set to **Power On**.
   - **Virtualization** (Intel VT-x or AMD SVM): **Enabled**.
   - **Secure Boot**: can stay on; Ubuntu supports it.
3. Save and exit (usually **F10**).
4. Plug in the USB drive, restart, and press the **boot menu key**: **F12** (Dell, Lenovo, Acer),
   **F9** (HP), **F8** (ASUS), **F11** (MSI, ASRock), or **Esc**. Choose the USB drive, preferably
   the entry that starts with **UEFI**.

---

## Part C. The installer, screen by screen

You will first see a black menu: choose **Try or Install Ubuntu Server** (press Enter, or wait).
After a minute the installer appears.

**Keys in the installer:** arrow keys or **Tab** move, **Space** ticks a box `[X]`, **Enter**
presses the highlighted button, **Esc** goes back. The mouse does not work.

| # | Screen | Choose |
| --- | --- | --- |
| 1 | **Willkommen! / Welcome!** (language) | **English** |
| 2 | **Installer update available** (only if online) | **Update to the new installer**, or **Continue without updating**; either is fine |
| 3 | **Keyboard configuration** | Layout **English (US)**, unless your keyboard differs. **Done** |
| 4 | **Choose the type of installation** | **(X) Ubuntu Server**, not *minimized*. Leave *Search for third-party drivers* unticked. **Done** |
| 5 | **Network configuration** | The Ethernet card (e.g. `enp3s0`) should show **DHCPv4** and an address like `192.168.1.50/24`. **Write that address down.** **Done** |
| 6 | **Proxy configuration** | Leave empty. **Done** |
| 7 | **Ubuntu archive mirror configuration** | Wait until it says the mirror passed the test. **Done** |
| 8 | **Guided storage configuration** | **(X) Use an entire disk**, the right disk selected; **[X] Set up this disk as an LVM group**; **[ ] Encrypt** left unticked. **Done** |
| 9 | **Storage configuration** (summary) | **Done**. The root volume may look smaller than the disk; Part E fixes that |
| 10 | **Confirm destructive action** | **Continue**. This is the point where the disk is erased |
| 11 | **Profile configuration** | **Your name:** anything. **Your server's name:** `evsu-server`. **Pick a username:** e.g. `admin1` (small letters). **Password:** a strong one you will remember; you need it for every `sudo`. **Done** |
| 12 | **Upgrade to Ubuntu Pro** | **(X) Skip for now**. **Continue** |
| 13 | **SSH configuration** | **[X] Install OpenSSH server** (press Space to tick it). **Import SSH key:** leave as *from: (none)*. **Done** |
| 14 | **Featured server snaps** | Tick nothing. **Done** |
| 15 | **Installing system** | Wait 5 to 20 minutes. When **Reboot Now** appears at the bottom, select it |

**Screen 5 without an address?** The cable is unplugged or the card has no driver. On a laptop
without Ethernet, a Wi-Fi network can be chosen here if the card is recognized, but a cable is far
more reliable for a server. In VirtualBox, check that the adapter is **Bridged** (Part B1, step 3).

**After Reboot Now:** when it says *Please remove the installation medium*, pull out the USB drive
(VirtualBox usually removes the ISO by itself) and press Enter.

---

## Part D. First sign-in at the server's screen

Wait for the text `evsu-server login:`. Lines of messages may appear after it; that is normal.
Press Enter for a clean prompt.

1. Type your username, Enter, then your password, Enter. **Nothing appears while you type the
   password.** That is normal.
2. You now see a prompt like `admin1@evsu-server:~$`. Type:

   ```bash
   ip -4 addr show | grep inet
   ```

   Note the address that is not `127.0.0.1`, for example `192.168.1.50`. This is the address you
   connect to.
3. **Keep that address from changing:** sign in to your router's web page (often `192.168.1.1` or
   `192.168.0.1`; the login is usually on a sticker under the router). Find **DHCP reservation**,
   **Static lease**, or **Address reservation**, choose `evsu-server` from the device list, and save.
   If you cannot find it, continue; you will just need to check the address again after restarts.

---

## Part E. Use the whole disk

The installer often gives the system only part of the disk (at most 100 GB). Check:

```bash
df -h /
```

If **Size** is much smaller than your disk, give it all the space:

```bash
sudo lvextend -r -l +100%FREE /dev/ubuntu-vg/ubuntu-lv
df -h /
```

`sudo` runs a command as administrator and asks for your password. **Size** should now be close to
the disk size. If it reports that no free space is left, the disk was already fully used.

---

## Part F. Connect from your Windows computer

From here on you can leave the server alone (in VirtualBox, you can minimize its window but must
keep it running).

**(your Windows computer, PowerShell)**

```powershell
ssh admin1@192.168.1.50
```

Use your username and address. The first time, it asks *Are you sure you want to continue
connecting?*: type `yes`. Then enter the server password. You now have the server's prompt inside
PowerShell. Type `exit` to come back to Windows.

**Now do step 2 of `deploy/AI-SERVER.md`** (sign in with a key instead of a password). Follow the
safety rule there: keep one SSH window open while you test in a second one.

### Optional: work in VS Code

1. In VS Code, install the extension **Remote - SSH** (by Microsoft).
2. Press **F1**, choose **Remote-SSH: Connect to Host...**, and type `admin1@192.168.1.50`.
   Choose **Linux** if asked.
3. A new VS Code window opens connected to the server:
   - **Terminal > New Terminal** runs commands on the server;
   - **File > Open Folder** browses the server's files, and you can drag files from Windows into it.

Files under `/etc` and `/srv` belong to the administrator or the `evsu` account, so VS Code cannot
save them directly. Edit those with `sudo nano <file>` in the terminal: **Ctrl+O** then Enter saves,
**Ctrl+X** exits.

---

## Part G. The basics you will use

| Command | What it does |
| --- | --- |
| `pwd` | Show which folder you are in |
| `ls -la` | List the files here, including hidden ones |
| `cd /srv/evsu-recruitment` | Go to a folder; `cd ..` goes up, `cd ~` goes home |
| `cat <file>` | Show a file |
| `sudo nano <file>` | Edit a file as administrator |
| `sudo systemctl status <service>` | Is a service running? Press **q** to leave |
| `sudo systemctl restart <service>` | Restart a service after changing its settings |
| `journalctl -u <service> -n 50 --no-pager` | The last 50 log lines of a service, where errors show |
| `free -h` / `df -h /` | Memory / disk space |
| `sudo reboot` / `sudo poweroff` | Restart / shut down (SSH disconnects; reconnect after a minute) |
| `exit` | Leave SSH and return to Windows |

**Terminal habits:**

- **Paste** with right-click or **Ctrl+Shift+V**. **Ctrl+C** stops a running command; it does not
  copy.
- **Tab** completes names; the **Up arrow** brings back earlier commands.
- Copy commands exactly, but replace example values first: `admin1`, `192.168.1.50`, `<domain>`.
- A command that prints nothing usually worked. Read anything with `error` or `failed`.
- When something fails, copy the command and its last 20 lines of output and ask for help.
- Take care with `rm -rf`, firewall (`ufw`) changes, and the SSH settings in step 2.

**Shutting down properly:** always use `sudo poweroff` rather than holding the power button, so the
database closes cleanly.

---

## Part H. Continue

Go back to **`deploy/AI-SERVER.md` step 3** (base system), then step 4 (the recruitment system,
through `deploy/DEPLOY.md`) and step 5 (the AI runtime), and finish with `deploy/ai_check.py`.

**Suggested sessions:**

1. VirtualBox: Parts A to F, then AI-SERVER.md step 3. Delete the practice machine afterwards, or
   keep it to rehearse.
2. Real machine: Parts B2 to F, then step 3.
3. Steps 4 to 6 on the real machine.

**Record for the Development Logs:** the machine (brand, processor, memory, disk), the Ubuntu
version (`lsb_release -d`), and whether it is a VirtualBox practice machine or the real server.

## Common problems

| Problem | What to do |
| --- | --- |
| The computer starts Windows instead of the USB drive | Use the boot menu key (Part B2), not the setup key; try the other USB port or the **UEFI:** entry |
| "Secure Boot violation" or the USB drive is not listed | Remake the drive in Rufus with **DD Image mode**, or turn off Secure Boot |
| The installer stops at the mirror test (screen 7) | No internet: check the cable; **Done** anyway installs without updates, then run updates later |
| VirtualBox: the installer starts again after reboot | Close the machine, **Settings > Storage**, remove the ISO from the optical drive, start again |
| VirtualBox: no address on screen 5 | **Settings > Network**: Bridged Adapter, and the network card you are actually using |
| `ssh: connect to host ... timed out` | Wrong address, server off, or a different network: check `ip -4 addr` on the server |
| `ssh` says *REMOTE HOST IDENTIFICATION HAS CHANGED* after a reinstall | Expected after reinstalling: run `ssh-keygen -R 192.168.1.50` on Windows, then connect again |
| Forgot the password | Easiest at this stage: reinstall (Part C) |
