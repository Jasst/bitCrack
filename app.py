from flask import Flask, request, render_template, jsonify
import hashlib
import base58
from coincurve import PrivateKey
import threading
import time
import json
import os
import random
import math

app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), 'templates'),
    static_folder=os.path.join(os.path.dirname(__file__), 'static')
)

PROGRESS_FILE = "progress_state.json"
FOUND_KEYS_FILE = "found_keys.txt"
LOG_FILE = "log.txt"

search_thread = None
stop_flag = False
stop_flag_lock = threading.Lock()
pause_flag = False
pause_flag_lock = threading.Lock()
saved_state = None
log_lines = []
search_finished = True
prefix_length = 8  # default prefix length

# --- Вспомогательные функции ---

def int_from_hex(s):
    return int(s, 16)

def hex_from_int(i):
    return f"{i:064x}"

def pubkey_to_address(pubkey_bytes):
    sha = hashlib.sha256(pubkey_bytes).digest()
    ripemd = hashlib.new('ripemd160', sha).digest()
    prefixed = b'\x00' + ripemd
    chk = hashlib.sha256(hashlib.sha256(prefixed).digest()).digest()[:4]
    return base58.b58encode(prefixed + chk).decode()

def private_key_to_address(priv_hex):
    key_bytes = bytes.fromhex(priv_hex)
    priv = PrivateKey(key_bytes)
    pub = priv.public_key.format(compressed=True)
    return pubkey_to_address(pub)

def log(msg):
    global log_lines
    ts = time.strftime('%H:%M:%S')
    line = f"[{ts}] {msg}"
    log_lines.append(line)
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")
    if len(log_lines) > 1000:
        log_lines = log_lines[-500:]

def save_progress(state):
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(state, f)

def load_progress():
    global saved_state
    try:
        with open(PROGRESS_FILE) as f:
            saved_state = json.load(f)
            return True
    except:
        return False

def remove_progress_file():
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)

def save_found_key(hex_key, prefix):
    with open(FOUND_KEYS_FILE, "a") as f:
        f.write(f"Prefix: {prefix} | Key: {hex_key}\n")

def is_valid_hex(s):
    return len(s) == 64 and all(c in "0123456789abcdefABCDEF" for c in s)

def set_stop_flag(value):
    global stop_flag
    with stop_flag_lock:
        stop_flag = value

def get_stop_flag():
    with stop_flag_lock:
        return stop_flag

def set_pause_flag(value):
    global pause_flag
    with pause_flag_lock:
        pause_flag = value

def get_pause_flag():
    with pause_flag_lock:
        return pause_flag


# --- Поисковая логика ---

def search_keys_range(target_address, start_int, end_int, mode, attempts, prefix_len, chunk_start):
    current = start_int
    checked = 0

    while current <= end_int:
        if get_stop_flag():
            log(f"[Thread-{threading.get_ident()}] Stopping at {hex_from_int(current)}")
            save_progress({
                "current": current,
                "target_address": target_address,
                "end_int": end_int,
                "mode": mode,
                "attempts": attempts,
                "prefix_length": prefix_len
            })
            return

        while get_pause_flag():
            time.sleep(0.1)
            if get_stop_flag():
                log(f"[Thread-{threading.get_ident()}] Stopping during pause at {hex_from_int(current)}")
                save_progress({
                    "current": current,
                    "target_address": target_address,
                    "end_int": end_int,
                    "mode": mode,
                    "attempts": attempts,
                    "prefix_length": prefix_len
                })
                return

        if mode == "sequential":
            priv_int = current
            current += 1
        elif mode == "random":
            if checked >= attempts:
                break
            priv_int = random.randint(start_int, end_int)
            checked += 1
        else:
            log(f"[Thread-{threading.get_ident()}] Unknown mode: {mode}")
            return

        priv_hex = hex_from_int(priv_int)
        addr = private_key_to_address(priv_hex)
        prefix = addr[:prefix_len]

        if prefix_len > 0 and prefix == target_address[:prefix_len]:
            log(f"[Thread-{threading.get_ident()}] Found key! Addr: {addr} Key: {priv_hex}")
            save_found_key(priv_hex, prefix)
            save_progress({
                "current": priv_int,
                "target_address": target_address,
                "end_int": end_int,
                "mode": mode,
                "attempts": attempts,
                "prefix_length": prefix_len,
                "found": True,
            })

        if mode == "random" and checked % 1000 == 0:
            log(f"[Thread-{threading.get_ident()}] Checked (random): {checked}")
        elif mode == "sequential" and (current - chunk_start) % 1000 == 0:
            log(f"[Thread-{threading.get_ident()}] Checked (sequential): {current - chunk_start}")


def search_keys_parallel(target_address, start_int, end_int, mode, attempts, prefix_len, num_workers=4):
    global search_finished, log_lines
    search_finished = False
    log_lines.clear()
    log(f"Search started in parallel mode with {num_workers} workers")

    total_range = end_int - start_int + 1
    chunk_size = math.ceil(total_range / num_workers)
    threads = []

    for i in range(num_workers):
        chunk_start = start_int + i * chunk_size
        chunk_end = min(chunk_start + chunk_size - 1, end_int)
        t = threading.Thread(
            target=search_keys_range,
            args=(target_address, chunk_start, chunk_end, mode, attempts, prefix_len, chunk_start)
        )
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    search_finished = True
    log("Parallel search finished.")
    remove_progress_file()


# --- Маршруты Flask ---

@app.route("/", methods=["GET", "POST"])
def index():
    global search_thread, prefix_length

    if request.method == "POST":
        if search_thread and search_thread.is_alive():
            return jsonify({"error": "Search already in progress."})

        target_address = request.form.get("target_address", "").strip()
        start = request.form.get("start", "").strip()
        end = request.form.get("end", "").strip()
        mode = request.form.get("mode", "sequential")
        attempts = int(request.form.get("attempts", 1000))
        prefix_length = int(request.form.get("prefix_length", 8))

        if not is_valid_hex(start) or not is_valid_hex(end):
            return jsonify({"error": "Start and End keys must be 64 hex chars."})

        start_int = int_from_hex(start)
        end_int = int_from_hex(end)

        if start_int > end_int:
            return jsonify({"error": "Start must be <= End."})

        attempts = max(1, attempts)

        set_stop_flag(False)
        set_pause_flag(False)
        log_lines.clear()

        search_thread = threading.Thread(target=search_keys_parallel, args=(target_address, start_int, end_int, mode, attempts, prefix_length, 4))
        search_thread.start()

        return jsonify({"result": "Search started."})

    saved = load_progress()
    context = {
        "target_address": saved_state.get("target_address") if saved else "",
        "start": hex_from_int(saved_state.get("current")) if saved else "",
        "end": hex_from_int(saved_state.get("end_int")) if saved else "",
        "mode": saved_state.get("mode") if saved else "sequential",
        "attempts": saved_state.get("attempts") if saved else 1000,
        "prefix_length": saved_state.get("prefix_length") if saved else 8,
        "result": "\n".join(log_lines),
        "saved_state_exists": saved
    }
    return render_template("index.html", **context)

@app.route("/pause", methods=["POST"])
def pause_search():
    set_pause_flag(True)
    return jsonify({"result": "Paused"})

@app.route("/resume", methods=["POST"])
def resume_search():
    set_pause_flag(False)
    return jsonify({"result": "Resumed"})

@app.route("/stop", methods=["POST"])
def stop_search():
    set_stop_flag(True)
    set_pause_flag(False)
    remove_progress_file()
    return jsonify({"result": "Stopped and progress cleared"})

@app.route("/progress")
def progress():
    return jsonify({
        "result": "\n".join(log_lines[-50:]),
        "finished": search_finished,
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route("/clear_log")
def clear_log():
    global log_lines
    log_lines.clear()
    open(LOG_FILE, "w").close()
    return jsonify({"result": "Logs cleared."})

if __name__ == "__main__":
    app.run(debug=False, host='127.0.0.1', port=5000)
