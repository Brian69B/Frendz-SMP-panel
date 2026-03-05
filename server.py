import os, subprocess, time, gzip, glob, re, json, secrets
import psutil
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from flask import (Flask, Response, render_template, jsonify, request,
                   stream_with_context, session, redirect, url_for)

app = Flask(__name__)
app.secret_key = os.environ.get("PANEL_SECRET", "friendzsmp-panel-secret-2025-xK9mP")
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = 86400 * 7  # 7 days

# ── credentials (change password here) ───────────────────────────────────────
USERS = {
    "admin": generate_password_hash("friendzsmp2025"),
}

SERVER_DIR  = "/home/ubuntu/friendzsmp/friendzsmp"
LOG_PATH    = os.path.join(SERVER_DIR, "logs", "latest.log")
JAVA_BIN    = "/opt/java21/bin/java"
SCREEN_NAME = "minecraft"

# ── auth helpers ──────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "msg": "Unauthorized"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated

# ── server helpers ────────────────────────────────────────────────────────────

def is_running():
    r = subprocess.run(["systemctl", "is-active", "friendzsmp-mc"],
                       capture_output=True, text=True)
    if r.stdout.strip() == "active":
        return True
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if "java" in (p.info["name"] or "") and \
               any("fabric" in c for c in (p.info["cmdline"] or [])):
                return True
        except Exception:
            pass
    r2 = subprocess.run(["screen", "-ls"], capture_output=True, text=True)
    return SCREEN_NAME in r2.stdout

def java_pid():
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if "java" in p.info["name"] and \
               any("fabric" in c for c in (p.info["cmdline"] or [])):
                return p.info["pid"]
        except Exception:
            pass
    return None

def read_log_tail(n=500):
    try:
        with open(LOG_PATH, "r", errors="replace") as f:
            lines = f.readlines()
        return [l.rstrip("\n") for l in lines[-n:]]
    except FileNotFoundError:
        return []

def list_archived_logs():
    files = sorted(glob.glob(os.path.join(SERVER_DIR, "logs", "*.log.gz")), reverse=True)
    return [os.path.basename(f) for f in files]

def parse_players_from_log():
    online = set()
    try:
        with open(LOG_PATH, "r", errors="replace") as f:
            for line in f:
                m = re.search(r"(\w+) joined the game", line)
                if m:
                    online.add(m.group(1))
                m2 = re.search(r"(\w+) left the game", line)
                if m2:
                    online.discard(m2.group(1))
    except Exception:
        pass
    return list(online)

def get_server_props():
    path = os.path.join(SERVER_DIR, "server.properties")
    props = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    props[k.strip()] = v.strip()
    except Exception:
        pass
    return props

def save_server_props(updates: dict):
    path = os.path.join(SERVER_DIR, "server.properties")
    lines = []
    try:
        with open(path) as f:
            lines = f.readlines()
    except Exception:
        pass
    result = []
    changed = set()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in updates:
                result.append(f"{k}={updates[k]}\n")
                changed.add(k)
                continue
        result.append(line)
    for k, v in updates.items():
        if k not in changed:
            result.append(f"{k}={v}\n")
    with open(path, "w") as f:
        f.writelines(result)

def get_mods():
    mods_dir = os.path.join(SERVER_DIR, "mods")
    try:
        return sorted([f for f in os.listdir(mods_dir) if f.endswith(".jar")])
    except Exception:
        return []

def system_stats():
    pid = java_pid()
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(SERVER_DIR)
    proc_cpu = proc_mem = None
    if pid:
        try:
            p = psutil.Process(pid)
            proc_cpu = p.cpu_percent(interval=0.1)
            proc_mem = p.memory_info().rss
        except Exception:
            pass
    return {
        "cpu_total": cpu,
        "mem_used": mem.used,
        "mem_total": mem.total,
        "mem_percent": mem.percent,
        "disk_used": disk.used,
        "disk_total": disk.total,
        "disk_percent": disk.percent,
        "proc_cpu": proc_cpu,
        "proc_mem": proc_mem,
    }

# ── auth routes ───────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET"])
def login_page():
    if session.get("logged_in"):
        return redirect(url_for("index"))
    return render_template("login.html")

@app.route("/login", methods=["POST"])
def login_post():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    hashed = USERS.get(username)
    if hashed and check_password_hash(hashed, password):
        session["logged_in"] = True
        session["username"] = username
        session.permanent = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "msg": "Invalid username or password"}), 401

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))

# ── main app route ────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template("index.html", username=session.get("username", "admin"))

# ── API routes (all protected) ────────────────────────────────────────────────

@app.route("/api/status")
@login_required
def api_status():
    running = is_running()
    players = parse_players_from_log() if running else []
    stats   = system_stats()
    props   = get_server_props()
    return jsonify({
        "running": running,
        "players": players,
        "player_count": len(players),
        "max_players": props.get("max-players", "20"),
        "motd": props.get("motd", "A Minecraft Server"),
        "version": "1.21.11",
        "port": props.get("server-port", "25565"),
        "online_mode": props.get("online-mode", "true"),
        **stats,
    })

@app.route("/api/console/stream")
@login_required
def api_console_stream():
    def generate():
        try:
            with open(LOG_PATH, "r", errors="replace") as f:
                lines = f.readlines()
                for line in lines[-150:]:
                    yield f"data: {json.dumps(line.rstrip())}\n\n"
                while True:
                    line = f.readline()
                    if line:
                        yield f"data: {json.dumps(line.rstrip())}\n\n"
                    else:
                        time.sleep(0.25)
                        yield ": ping\n\n"
        except GeneratorExit:
            pass
        except Exception as e:
            yield f"data: {json.dumps(f'[stream error: {e}]')}\n\n"
    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/api/console/send", methods=["POST"])
@login_required
def api_console_send():
    cmd = (request.get_json(silent=True) or {}).get("command", "").strip()
    if not cmd:
        return jsonify({"ok": False, "msg": "Empty command"})
    if not is_running():
        return jsonify({"ok": False, "msg": "Server not running"})
    subprocess.run(["screen", "-S", SCREEN_NAME, "-X", "stuff", cmd + "\n"])
    return jsonify({"ok": True, "msg": f"Sent: {cmd}"})

@app.route("/api/server/<action>", methods=["POST"])
@login_required
def api_server_action(action):
    if action == "start":
        if is_running():
            return jsonify({"ok": False, "msg": "Already running"})
        subprocess.run(["sudo", "systemctl", "start", "friendzsmp-mc"])
        return jsonify({"ok": True, "msg": "Server starting…"})
    elif action == "stop":
        if not is_running():
            return jsonify({"ok": False, "msg": "Not running"})
        subprocess.run(["sudo", "systemctl", "stop", "friendzsmp-mc"])
        return jsonify({"ok": True, "msg": "Stop command sent"})
    elif action == "restart":
        subprocess.run(["sudo", "systemctl", "restart", "friendzsmp-mc"])
        return jsonify({"ok": True, "msg": "Restart initiated"})
    return jsonify({"ok": False, "msg": "Unknown action"}), 400

@app.route("/api/player/<action>", methods=["POST"])
@login_required
def api_player_action(action):
    data = request.get_json(silent=True) or {}
    player = data.get("player", "").strip()
    if not player:
        return jsonify({"ok": False, "msg": "No player specified"})
    if not is_running():
        return jsonify({"ok": False, "msg": "Server not running"})
    cmds = {
        "kick":     f"kick {player} Kicked by admin",
        "ban":      f"ban {player}",
        "op":       f"op {player}",
        "deop":     f"deop {player}",
        "tp_spawn": f"tp {player} 0 64 0",
    }
    cmd = cmds.get(action)
    if not cmd:
        return jsonify({"ok": False, "msg": "Unknown action"})
    subprocess.run(["screen", "-S", SCREEN_NAME, "-X", "stuff", cmd + "\n"])
    return jsonify({"ok": True, "msg": f"Ran: {cmd}"})

@app.route("/api/logs/list")
@login_required
def api_logs_list():
    return jsonify({"files": list_archived_logs()})

@app.route("/api/logs/archived/<filename>")
@login_required
def api_logs_archived(filename):
    if not filename.endswith(".log.gz") or "/" in filename:
        return jsonify({"error": "Invalid"}), 400
    path = os.path.join(SERVER_DIR, "logs", filename)
    try:
        with gzip.open(path, "rt", errors="replace") as f:
            lines = [l.rstrip("\n") for l in f.readlines()]
        return jsonify({"lines": lines})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/mods")
@login_required
def api_mods():
    return jsonify({"mods": get_mods()})

@app.route("/api/props", methods=["GET"])
@login_required
def api_props_get():
    return jsonify(get_server_props())

@app.route("/api/props", methods=["POST"])
@login_required
def api_props_set():
    updates = request.get_json(silent=True) or {}
    if not updates:
        return jsonify({"ok": False, "msg": "No data"})
    save_server_props(updates)
    return jsonify({"ok": True, "msg": "Properties saved (restart required)"})

@app.route("/api/list/<name>")
@login_required
def api_list(name):
    allowed = {"whitelist": "whitelist.json", "ops": "ops.json",
               "banned-players": "banned-players.json", "banned-ips": "banned-ips.json"}
    if name not in allowed:
        return jsonify({"error": "Unknown list"}), 400
    path = os.path.join(SERVER_DIR, allowed[name])
    try:
        with open(path) as f:
            data = json.load(f)
        return jsonify({"entries": data})
    except Exception as e:
        return jsonify({"entries": [], "error": str(e)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, threaded=True)
