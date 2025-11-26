import socket
import threading
import json
from datetime import datetime, timedelta
import requests
import traceback
import ssl
import os
import http.server
import socketserver
from urllib.parse import unquote
import hashlib 

# --- CONFIGURATION ---
HOST = '192.168.29.114'  # <--- MAKE SURE THIS MATCHES YOUR LOCAL IP
PORT = 5000
HTTP_PORT = 5001
FILE_DIR = "server_files"

TEMP_PASS_FILE = "temporary_passwords.json"
CHAT_FILE = "chat_history.json"
FRIENDS_FILE = "friends_data.json"
USERS_DB_FILE = "users_db.json"

chat_lock = threading.Lock()

# Ensure directories exist
if not os.path.exists(FILE_DIR):
    os.makedirs(FILE_DIR)

# --- HELPER FUNCTIONS ---

def load_json(file_path):
    try:
        if not os.path.exists(file_path):
            return {}
        with open(file_path, "r") as f:
            return json.load(f)
    except:
        return {}

def save_json(file_path, data):
    with chat_lock:
        with open(file_path, "w") as f:
            json.dump(data, f, indent=4)

def load_temp_passwords():
    return load_json(TEMP_PASS_FILE)

def save_temp_passwords(data):
    try:
        tmp = TEMP_PASS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, TEMP_PASS_FILE)
    except Exception as e:
        print("[TEMP PASS] Failed to save:", e)

# Load persistent data initially
chat_history = load_json(CHAT_FILE)
friends_data = load_json(FRIENDS_FILE)
friends = friends_data.get("friends", {})
pending_requests = friends_data.get("pending", {})

# Global State
clients = []
nicknames = []
online_status = {}
unread_messages = {}
offline_queue = {}
auto_sessions = {}

# ---------------- HTTP FILE SERVER ----------------

class FileUploadHandler(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(self.headers['Content-Length'])
            # SECURITY: Limit upload size to 50MB to prevent DoS
            if content_length > 50 * 1024 * 1024:
                self.send_error(413, "File too large")
                return
                
            filename = unquote(self.headers.get('X-Filename', 'unknown_file'))
            filename = os.path.basename(filename)
            file_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
            file_path = os.path.join(FILE_DIR, file_id)

            with open(file_path, 'wb') as f:
                f.write(self.rfile.read(content_length))

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()

            response_data = {
                "success": True,
                "file_id": file_id,
                "filename": filename,
                "url": f"http://{HOST}:{HTTP_PORT}/files/{file_id}"
            }
            self.wfile.write(json.dumps(response_data).encode('utf-8'))
            print(f"[FILE SERVER] Received file: {file_id}")

        except Exception as e:
            print(f"[FILE SERVER] Upload error: {e}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode('utf-8'))

    def do_GET(self):
        if self.path.startswith('/files/'):
            try:
                filename = os.path.basename(unquote(self.path[len('/files/'):]))
                file_path = os.path.join(FILE_DIR, filename)
                if not os.path.exists(file_path) or not os.path.isfile(file_path):
                    self.send_error(404, "File not found")
                    return
                with open(file_path, 'rb') as f:
                    self.send_response(200)
                    self.send_header("Content-type", self.guess_type(file_path))
                    fs = os.fstat(f.fileno())
                    self.send_header("Content-Length", str(fs[6]))
                    self.send_header("Last-Modified", self.date_time_string(fs.st_mtime))
                    self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                    self.end_headers()
                    self.copyfile(f, self.wfile)
            except Exception as e:
                self.send_error(404, "File not found")
        else:
            self.send_error(404, "Not found")

def start_file_server():
    # Pass directory explicitly to fix 404s
    handler_with_args = lambda *args, **kwargs: FileUploadHandler(*args, directory=FILE_DIR, **kwargs)
    with socketserver.TCPServer(("", HTTP_PORT), handler_with_args) as httpd:
        print(f"[FILE SERVER] Serving on port {HTTP_PORT} from {FILE_DIR}")
        httpd.serve_forever()

threading.Thread(target=start_file_server, daemon=True).start()

# ---------------- CHAT LOGIC ----------------

def send_message(msg, client):
    try:
        client.send(msg.encode('utf-8'))
    except:
        pass 

def send_private(sender, recipient, msg, ai_generated=False):
    """Deliver private messages with proper AI handling and persistence."""
    
    # 1. Deliver to recipient if online
    if recipient in nicknames:
        try:
            idx = nicknames.index(recipient)
            send_message(f"{sender}|{msg}", clients[idx])
        except Exception:
            pass
    else:
        offline_queue.setdefault(recipient, {}).setdefault(sender, []).append(msg)

    # 2. Mark unread (only humans)
    if not ai_generated:
        unread_messages.setdefault(recipient, {}).setdefault(sender, []).append(msg)

    # 3. Save History
    for a, b in [(sender, recipient), (recipient, sender)]:
        chat_history.setdefault(a, {}).setdefault(b, [])
        chat_history[a][b].append({
            "sender": sender,
            "message": msg,
            "timestamp": datetime.now().isoformat(),
            "ai": ai_generated
        })
    save_json(CHAT_FILE, chat_history)

    # 4. AutoAI Logic
    if ai_generated:
        return

    active_session = None
    activator = None
    target = None
    
    # Find active session
    for key, session in auto_sessions.items():
        if not session["active"] or datetime.now() >= session["expires"]:
            continue
        u1, u2 = key.split(":")
        if (sender == u2 and recipient == u1) or (sender == u1 and recipient == u2):
            activator, target = u1, u2
            active_session = session
            break
            
    if not active_session:
        return

    # Trigger AutoAI Webhook
    payload = {
        "sender": activator,
        "recipient": target,
        "latest_message": msg,
        "recent_messages": chat_history.get(activator, {}).get(target, [])[-20:]
    }

    def call_n8n_webhook():
        try:
            response = requests.post(
                "webhookurl",
                json=payload,
                timeout=60
            )
            try:
                data = response.json()
            except Exception:
                data = {}

            ai_reply_text = data.get("reply") or "(No response)"
            send_private(activator, target, ai_reply_text, ai_generated=True)
        except Exception:
            error_msg = traceback.format_exc()
            send_private(activator, target, f"(AutoAI error: {error_msg})", ai_generated=True)

    threading.Thread(target=call_n8n_webhook).start()

# ---------------- CORE CLIENT HANDLER ----------------

def handle_client(client):
    """
    Handles Authentication -> Session Setup -> Main Chat Loop
    """
    authenticated_user = None
    
    try:
        # === PHASE 1: AUTHENTICATION ===
        while True:
            try:
                msg = client.recv(1024).decode('utf-8')
                if not msg:
                    client.close()
                    return
            except:
                client.close()
                return

            if msg.startswith("LOGIN|"):
                try:
                    _, username, password = msg.split("|", 2)
                    
                    # FIX: Reload DB to see new Admin creations immediately
                    users_db = load_json(USERS_DB_FILE)
                    
                    if username not in users_db:
                        send_message("LOGIN_FAIL|Invalid credentials", client)
                        continue 

                    # Check Temp Password
                    temp_db = load_temp_passwords()
                    login_success = False
                    first_login = False

                    if username in temp_db:
                        if hashlib.sha256(password.encode()).hexdigest() == temp_db[username]["temp_password_hash"]:
                            login_success = True
                            first_login = True
                    
                    # Check Real Password
                    if not login_success:
                        stored_hash = users_db[username].get("password_hash")
                        if stored_hash and hashlib.sha256(password.encode()).hexdigest() == stored_hash:
                            login_success = True
                    
                    if login_success:
                        if first_login or users_db[username].get("force_password_change", False):
                            send_message("FIRST_LOGIN|OK", client)
                            # Stay in loop to wait for CHANGE_PASS
                        else:
                            send_message("LOGIN_OK", client)
                            authenticated_user = username
                            break # Go to Chat Phase
                    else:
                        send_message("LOGIN_FAIL|Invalid credentials", client)

                except Exception as e:
                    print(f"Login error: {e}")
                    send_message("LOGIN_FAIL|Error", client)

            elif msg.startswith("CHANGE_PASS|"):
                try:
                    _, username, old_pass, new_pass = msg.split("|", 3)
                    users_db = load_json(USERS_DB_FILE)
                    temp_db = load_temp_passwords()
                    
                    valid_old = False
                    if username in temp_db:
                        if hashlib.sha256(old_pass.encode()).hexdigest() == temp_db[username]["temp_password_hash"]:
                            valid_old = True
                    
                    if valid_old:
                        users_db[username]["password_hash"] = hashlib.sha256(new_pass.encode()).hexdigest()
                        users_db[username]["force_password_change"] = False
                        save_json(USERS_DB_FILE, users_db)
                        
                        if username in temp_db:
                            del temp_db[username]
                            save_temp_passwords(temp_db)
                            
                        send_message("CHANGE_OK", client)
                    else:
                        send_message("CHANGE_FAIL|Incorrect temp password", client)
                except:
                    send_message("CHANGE_FAIL|Format error", client)
            
            # Reject other commands before login
            else:
                pass

        # === PHASE 2: SESSION SETUP ===
        print(f"[NEW SESSION] {authenticated_user} logged in.")
        
        nicknames.append(authenticated_user)
        clients.append(client)
        online_status[authenticated_user] = True
        
        friends.setdefault(authenticated_user, [])
        pending_requests.setdefault(authenticated_user, [])
        unread_messages.setdefault(authenticated_user, {})
        chat_history.setdefault(authenticated_user, {})
        save_json(CHAT_FILE, chat_history)

        send_message(f"Welcome {authenticated_user}!\nConnected. Type /help for commands.", client)
        
        # Deliver Offline Messages
        if authenticated_user in offline_queue:
            for sender, msgs in offline_queue[authenticated_user].items():
                for m in msgs:
                    send_message(f"{sender}|{m}", client)
            del offline_queue[authenticated_user]

        # === PHASE 3: MAIN CHAT LOOP ===
        # Alias 'nickname' to 'authenticated_user' to keep original logic working
        nickname = authenticated_user
        pending_session = False # For friend requests logic

        while True:
            try:
                msg = client.recv(1024).decode('utf-8')
                if not msg:
                    break

                # ---------------- AUTO MODE ----------------
                if msg.startswith("/Auto"):
                    parts = msg.split()
                    if len(parts) < 2:
                        send_message("Usage: /Auto <friendName> [<time_in_minutes>]", client)
                        continue
                    target = parts[1]
                    duration = 0
                    if len(parts) >= 3:
                        p = parts[2]
                        if p.endswith("m"):
                            try:
                                duration = int(p[:-1])
                            except:
                                duration = 0
                        else:
                            try:
                                duration = int(p)
                            except:
                                duration = 0
                    if target not in friends.get(nickname, []):
                        send_message(f"{target} is not your friend.", client)
                        continue
                    expires = datetime.now() + timedelta(minutes=duration if duration > 0 else 9999)
                    key = f"{nickname}:{target}"
                    auto_sessions[key] = {"active": True, "expires": expires}
                    send_message(f"✅ AutoAI enabled for {target} {'for '+parts[2] if duration else '(until turned off)'}", client)
                    continue

                elif msg.startswith("/noAuto"):
                    parts = msg.split()
                    if len(parts) < 2:
                        send_message("Usage: /noAuto <friendName>", client)
                        continue
                    target = parts[1]
                    key = f"{nickname}:{target}"
                    if key in auto_sessions:
                        auto_sessions[key]["active"] = False
                        auto_sessions.pop(key, None)
                        send_message(f"❌ AutoAI disabled for {target}.", client)
                    else:
                        send_message(f"No active AutoAI session with {target}.", client)
                    continue

                elif msg.startswith("/clearunread"):
                    parts = msg.split()
                    if len(parts) < 2:
                        continue
                    friend_name = parts[1].strip()
                    if nickname in unread_messages and friend_name in unread_messages[nickname]:
                        unread_messages[nickname][friend_name] = []
                    continue

                elif msg.startswith("/summarize"):
                    parts = msg.split()
                    if len(parts) < 2:
                        send_message("Usage: /summarize <friend>", client)
                        continue
                    target = parts[1]

                    recent_msgs = chat_history.get(nickname, {}).get(target, [])[-50:]

                    if not recent_msgs:
                        send_message(f"No recent messages with {target} to summarize.", client)
                        continue

                    payload = {
                        "sender": nickname,
                        "recipient": target,
                        "recent_messages": recent_msgs
                    }

                    try:
                        r = requests.post(
                            "webhookurl",
                            json=payload,
                            timeout=90
                        )
                        result = r.json()
                        summary = result.get("summary", None) or result.get("reply", "(No summary received)")
                        send_message(f"🧾 Summary of chat with {target}:\n\n{summary}", client)
                    except Exception as e:
                        send_message(f"⚠️ Error generating summary: {e}", client)
                    continue  

                elif msg.startswith("/helper"):
                    parts = msg.split(" ", 2)
                    if len(parts) < 3:
                        send_message("Usage: /helper <friend> <prompt>", client)
                        continue
                    target = parts[1].strip()
                    prompt = parts[2].strip()

                    if target not in friends.get(nickname, []):
                        send_message(f"{target} is not your friend.", client)
                        continue

                    recent_msgs = chat_history.get(nickname, {}).get(target, [])[-50:]

                    payload = {
                        "requester": nickname,
                        "target_friend": target,
                        "prompt": prompt,
                        "recent_messages": recent_msgs
                    }
                    send_message("🔍 Processing helper request, please wait...", client)

                    def call_helper_webhook():
                        try:
                            r = requests.post(
                                "webhookurl",
                                json=payload,
                                timeout=90
                            )
                            r.raise_for_status()
                            result = r.json()
                            helper_response = result.get("response", None) or result.get("reply", "(No response received)")
                            send_message(f"🧠 Helper Response for {target} on '{prompt[:20]}...':\n\n{helper_response}", client)
                        except Exception as e:
                            send_message(f"⚠️ Helper Error: {e}", client)

                    threading.Thread(target=call_helper_webhook, daemon=True).start()
                    continue

                elif msg.strip().lower().startswith("/playbook"):
                    parts = msg.split()
                    if len(parts) < 2:
                        send_message("Usage: /playbook <friend>", client)
                        continue
                    target = parts[1].strip()

                    recent_msgs = chat_history.get(nickname, {}).get(target, [])[-50:]
                    if not recent_msgs:
                        send_message(f"No recent messages with {target} to include in playbook.", client)
                        continue

                    payload = {
                        "sender": nickname,
                        "recipient": target,
                        "recent_messages": recent_msgs
                    }

                    def send_playbook():
                        try:
                            r = requests.post(
                                "webhookurl",
                                json=payload,
                                timeout=120
                            )
                            if r.status_code == 200:
                                send_message("Playbook generated successfully! Check your Drive.\n", client)
                            else:
                                send_message(f"Workflow error (HTTP {r.status_code}).\n", client)
                        except Exception as e:
                            send_message(f"Error calling PlayBook webhook: {e}\n", client)

                    threading.Thread(target=send_playbook, daemon=True).start()
                    continue

                # ---------------- FRIEND REQUEST ----------------
                elif msg.startswith("/addfriend"):
                    parts = msg.split(" ", 1)
                    if len(parts) < 2:
                        send_message("Usage: /addfriend <nickname>", client)
                        continue
                    target = parts[1].strip()
                    if target not in nicknames:
                        send_message(f"{target} is not online currently.", client)
                        continue
                    if target not in pending_requests:
                        pending_requests[target] = []
                    if nickname not in pending_requests[target] and nickname not in friends.get(target, []):
                        pending_requests[target].append(nickname)
                        send_message(f"Friend request sent to {target}.", client)
                        # Notify target
                        try:
                            t_idx = nicknames.index(target)
                            send_message(f"{nickname} wants to be your friend.", clients[t_idx])
                        except:
                            pass
                        save_json(FRIENDS_FILE, {"friends": friends, "pending": pending_requests})
                    continue

                # ---------------- PENDING REQUESTS ----------------
                elif msg.startswith("/pending"):
                    pending = pending_requests.get(nickname, [])
                    if not pending:
                        send_message("No pending friend requests.", client)
                        continue
                    display = "Pending friend requests:\n"
                    for i, p in enumerate(pending):
                        display += f"{i+1}. {p}\n"
                    send_message(display.strip(), client)
                    pending_session = True
                    continue

                # ---------------- FRIEND RESPONSE ----------------
                elif pending_session and (msg.lower().startswith("yes") or msg.lower().startswith("no")):
                    parts = msg.split(" ", 1)
                    if len(parts) != 2:
                        send_message("Usage: yes <num> or no <num>", client)
                        continue
                    action = parts[0].lower()
                    try:
                        idx = int(parts[1]) - 1
                    except:
                        send_message("Invalid index.", client)
                        continue
                    pending = pending_requests.get(nickname, [])
                    if idx < 0 or idx >= len(pending):
                        send_message("Index out of range.", client)
                        continue
                    requester = pending.pop(idx)
                    if action == "yes":
                        friends.setdefault(nickname, [])
                        friends.setdefault(requester, [])
                        if requester not in friends[nickname]:
                            friends[nickname].append(requester)
                        if nickname not in friends[requester]:
                            friends[requester].append(nickname)
                        send_message(f"You are now friends with {requester}.", client)
                        try:
                            r_idx = nicknames.index(requester)
                            send_message(f"{nickname} accepted your friend request.", clients[r_idx])
                        except:
                            pass
                    else:
                        send_message(f"You rejected {requester}'s friend request.", client)
                    
                    pending_session = False
                    save_json(FRIENDS_FILE, {"friends": friends, "pending": pending_requests})
                    continue

                # ---------------- FRIEND LIST ----------------
                elif msg.startswith("/friends"):
                    flist = friends.get(nickname, [])
                    display = "Your friends:\n"
                    for f in flist:
                        status = "🔥" if online_status.get(f, False) else ""
                        offline_msg = " 🗣️" if unread_messages.get(nickname, {}).get(f) else ""
                        display += f"{f} {status}{offline_msg}\n"
                    send_message(display, client)
                    continue

                # ---------------- FILEMANIA ----------------
                elif msg.startswith("/FILEMANIA|"):
                    try:
                        _, action, file_url = msg.split("|", 2)
                        print(f"[FILEMANIA] Received action '{action}' for user '{nickname}'")

                        NGROK_BASE = "https://cd9037313da9.ngrok-free.app" 
                        file_url = file_url.replace(f"http://{HOST}:{HTTP_PORT}", NGROK_BASE)

                        payload = {
                            "sender": nickname,
                            "action": action,
                            "file_url": file_url
                        }

                        def call_filemania_webhook():
                            try:
                                response = requests.post(
                                    "webhookurl", 
                                    json=payload,
                                    timeout=180 
                                )
                                response.raise_for_status()
                                data = response.json()
                                ai_reply_text = data.get("reply") or f"(File analysis: {action} returned no reply.)"
                                send_message(f"🧠 FileMania Result:\n\n{ai_reply_text}", client)
                            except Exception:
                                error_msg = traceback.format_exc()
                                send_message(f"🤖 FileMania ({action}) Error:\n{error_msg}", client)

                        threading.Thread(target=call_filemania_webhook, daemon=True).start()

                    except ValueError:
                        send_message("Invalid FileMania command format.", client)
                    continue

                # ---------------- PRIVATE MESSAGE ----------------
                elif msg.startswith("PRIVATE|"):
                    try:
                        _, recipient, message_text = msg.split("|", 2)
                        if recipient not in friends.get(nickname, []):
                            send_message(f"{recipient} is not your friend.", client)
                            continue
                        send_private(nickname, recipient, message_text)
                    except:
                        send_message("Invalid private message format.", client)
                    continue

                # ---------------- CLEAR CHAT ----------------
                elif msg.startswith("/clear"):
                    parts = msg.split()
                    if len(parts) < 2:
                        send_message("Usage: /clear <friendName>", client)
                        continue
                    target = parts[1].strip()
                    if target not in friends.get(nickname, []):
                        send_message(f"{target} is not your friend.", client)
                        continue
                    if nickname in chat_history and target in chat_history[nickname]:
                        chat_history[nickname][target] = []
                    if target in chat_history and nickname in chat_history[target]:
                        chat_history[target][nickname] = []  
                    save_json(CHAT_FILE, chat_history)
                    send_message(f"✅ Chat with {target} cleared for both sides.", client)
                    continue                

                # ---------------- HELP ----------------
                elif msg.startswith("/help"):
                    commands = (
                        "/addfriend <name>    → send friend request\n"
                        "/pending             → view pending requests\n"
                        "yes <num>/no <num>   → respond to pending request\n"
                        "/friends             → view friends list\n"
                        "/msg <friend>        → start private chat\n"
                        "/helper <f> <prompt> → AI assistance based on chat context\n"
                        "/Auto <friend> [15m] → enable AutoAI chat\n"
                        "/noAuto <friend>     → disable AutoAI chat\n"
                        "/exit                → exit current chat\n"
                    )
                    send_message(commands, client)
                    continue

                else:
                    send_message("Unknown command. Type /help for available commands.", client)
            
            except Exception as e:
                print(f"Loop Error: {e}")
                break

    except Exception as e:
        print(f"Client handler error: {e}")
    finally:
        # === PHASE 4: CLEANUP ===
        if client in clients:
            clients.remove(client)
        if authenticated_user and authenticated_user in nicknames:
            nicknames.remove(authenticated_user)
            online_status[authenticated_user] = False
            print(f"[DISCONNECT] {authenticated_user}")
        client.close()

# ---------------- SERVER STARTUP ----------------
if __name__ == "__main__":
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((HOST, PORT))

    # TLS Setup
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile="server.crt", keyfile="server.key")
    server = context.wrap_socket(server, server_side=True)

    server.listen()
    print(f"[SECURE SERVER] Listening on {HOST}:{PORT}")

    try:
        while True:
            client, addr = server.accept()
            print(f"[CONNECTION] New connection from {addr}")
            
            # Start thread immediately without asking for NICK
            thread = threading.Thread(target=handle_client, args=(client,))
            thread.start()
    except KeyboardInterrupt:
        server.close()
        print("\n[SERVER SHUTDOWN]")