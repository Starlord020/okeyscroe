import eventlet
eventlet.monkey_patch()

from flask import Flask, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room, disconnect
import json, os, random, string, time

app = Flask(__name__)
app.config['SECRET_KEY'] = 'okey101secret'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

TABLES_FILE = 'tables.json'
DATABASE_URL = os.environ.get('DATABASE_URL')

# Render.com "postgres://" → psycopg2 needs "postgresql://"
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

def _db_conn():
    import psycopg2
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def _db_init():
    """Tablo yoksa oluştur."""
    with _db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS app_data (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

def load_tables():
    if DATABASE_URL:
        try:
            _db_init()
            with _db_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT value FROM app_data WHERE key = 'tables'")
                    row = cur.fetchone()
                    if row:
                        return json.loads(row[0])
        except Exception as e:
            print(f'[DB] load error: {e}')
        return {}
    # Yerel JSON dosyası
    if os.path.exists(TABLES_FILE):
        try:
            with open(TABLES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}

def save_tables():
    if DATABASE_URL:
        try:
            with _db_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO app_data (key, value) VALUES ('tables', %s)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                    """, (json.dumps(tables, ensure_ascii=False),))
        except Exception as e:
            print(f'[DB] save error: {e}')
    else:
        with open(TABLES_FILE, 'w', encoding='utf-8') as f:
            json.dump(tables, f, ensure_ascii=False)

def make_id():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))

tables = load_tables()

SUPER_ADMIN_PASSWORD = "yarakhasan"

# socket_id -> { table_id -> is_admin (bool) }
socket_auth = {}
# sockets that authenticated as super admin
super_admin_sockets = set()

def is_admin(sid, tid):
    """Şifreli masalarda sadece yetkili socket düzenleme yapabilir."""
    if sid in super_admin_sockets:
        return True
    t = tables.get(tid)
    if t is None:
        return False
    # Şifresi olmayan masa: herkes düzenleyebilir
    if not t.get('password'):
        return True
    return socket_auth.get(sid, {}).get(tid, False)

def table_summary(t):
    rounds = t.get('rounds', [])
    t1 = sum(r[0] for r in rounds)
    t2 = sum(r[1] for r in rounds)
    return {
        'id': t['id'],
        'name': t['name'],
        'names': t['names'],
        'round_count': len(rounds),
        'totals': [t1, t2],
        'created_at': t.get('created_at', 0),
        'has_password': bool(t.get('password'))
    }

def emit_table_state(tid):
    if tid in tables:
        socketio.emit('state', tables[tid], to=tid)

def broadcast_lobby():
    socketio.emit('tables_list', [table_summary(t) for t in tables.values()])

# ── ROUTES ─────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('.', 'okeyscore.html')

@app.route('/logo.png')
def logo():
    return send_from_directory('.', 'logo.png')

# ── LIFECYCLE ──────────────────────────────────────────────────────────────────

@socketio.on('connect')
def on_connect():
    emit('tables_list', [table_summary(t) for t in tables.values()])

@socketio.on('disconnect')
def on_disconnect():
    from flask import request as freq
    sid = freq.sid
    socket_auth.pop(sid, None)
    super_admin_sockets.discard(sid)

@socketio.on('super_admin_login')
def on_super_admin_login(data):
    from flask import request as freq
    pw = data.get('password', '').strip()
    if pw == SUPER_ADMIN_PASSWORD:
        super_admin_sockets.add(freq.sid)
        emit('super_admin_ok')
    else:
        emit('error', {'msg': 'Süper admin şifresi yanlış'})

# ── LOBBY ──────────────────────────────────────────────────────────────────────

@socketio.on('create_table')
def on_create_table(data):
    from flask import request as freq
    tid = make_id()
    password = data.get('password', '').strip()
    table = {
        'id': tid,
        'name': data.get('name', 'Yeni Masa'),
        'names': [
            data.get('p1', 'Oyuncu 1'),
            data.get('p2', 'Oyuncu 2')
        ],
        'rounds': [],
        'password': password,
        'created_at': int(time.time())
    }
    tables[tid] = table
    save_tables()

    # Oluşturan kişi otomatik yetkili
    sid = freq.sid
    if sid not in socket_auth:
        socket_auth[sid] = {}
    socket_auth[sid][tid] = True

    join_room(tid)
    broadcast_lobby()
    is_super = sid in super_admin_sockets
    emit('join_ok', {'table': table, 'is_admin': True, 'is_super_admin': is_super})

@socketio.on('join_table')
def on_join_table(data):
    from flask import request as freq
    tid = data.get('id')
    password = data.get('password', '').strip()

    if tid not in tables:
        emit('error', {'msg': 'Masa bulunamadı'})
        return

    t = tables[tid]
    table_password = t.get('password', '')
    sid = freq.sid

    is_super = (sid in super_admin_sockets) or (password == SUPER_ADMIN_PASSWORD)
    if is_super:
        super_admin_sockets.add(sid)
        admin = True
    elif table_password:
        admin = (password == table_password)
    else:
        admin = True  # Şifresiz masa: herkes yönetici

    if sid not in socket_auth:
        socket_auth[sid] = {}
    socket_auth[sid][tid] = admin

    join_room(tid)
    emit('join_ok', {'table': t, 'is_admin': admin, 'is_super_admin': is_super})

@socketio.on('leave_table')
def on_leave_table(data):
    tid = data.get('id')
    if tid:
        leave_room(tid)

@socketio.on('delete_table')
def on_delete_table(data):
    from flask import request as freq
    tid = data.get('id')
    if tid not in tables:
        return
    if not is_admin(freq.sid, tid):
        emit('error', {'msg': 'Bu işlem için yetkiniz yok'})
        return
    del tables[tid]
    save_tables()
    broadcast_lobby()

# ── GAME ───────────────────────────────────────────────────────────────────────

@socketio.on('input_update')
def on_input_update(data):
    tid = data.get('table_id')
    if tid not in tables: return
    socketio.emit('input_update', {
        'v1': data.get('v1', ''),
        'v2': data.get('v2', '')
    }, to=tid, include_self=False)

@socketio.on('add_round')
def on_add_round(data):
    from flask import request as freq
    tid = data.get('table_id')
    if tid not in tables: return
    if not is_admin(freq.sid, tid):
        emit('error', {'msg': 'Düzenleme yetkiniz yok'}); return
    round_data = data['round']
    tables[tid]['rounds'].append(round_data)
    save_tables()
    emit_table_state(tid)
    broadcast_lobby()
    losers = [tables[tid]['names'][i] for i, v in enumerate(round_data) if v == 101]
    if losers:
        socketio.emit('penalty_101', {'names': losers}, to=tid)

@socketio.on('delete_round')
def on_delete_round(data):
    from flask import request as freq
    tid = data.get('table_id')
    if tid not in tables: return
    if not is_admin(freq.sid, tid):
        emit('error', {'msg': 'Düzenleme yetkiniz yok'}); return
    idx = data.get('idx')
    if 0 <= idx < len(tables[tid]['rounds']):
        tables[tid]['rounds'].pop(idx)
        save_tables()
        emit_table_state(tid)
        broadcast_lobby()

@socketio.on('undo_round')
def on_undo_round(data):
    from flask import request as freq
    tid = data.get('table_id')
    if tid not in tables: return
    if not is_admin(freq.sid, tid):
        emit('error', {'msg': 'Düzenleme yetkiniz yok'}); return
    if tables[tid]['rounds']:
        tables[tid]['rounds'].pop()
        save_tables()
        emit_table_state(tid)
        broadcast_lobby()

@socketio.on('edit_round')
def on_edit_round(data):
    from flask import request as freq
    tid = data.get('table_id')
    if tid not in tables: return
    if not is_admin(freq.sid, tid):
        emit('error', {'msg': 'Düzenleme yetkiniz yok'}); return
    idx = data.get('idx')
    player_idx = data.get('playerIdx')
    value = data.get('value')
    if 0 <= idx < len(tables[tid]['rounds']) and player_idx in (0, 1):
        tables[tid]['rounds'][idx][player_idx] = value
        save_tables()
        emit_table_state(tid)
        broadcast_lobby()

@socketio.on('rename_player')
def on_rename_player(data):
    from flask import request as freq
    tid = data.get('table_id')
    if tid not in tables: return
    if not is_admin(freq.sid, tid):
        emit('error', {'msg': 'Düzenleme yetkiniz yok'}); return
    idx = data.get('idx')
    name = data.get('name', '').strip()
    if name and 0 <= idx <= 1:
        tables[tid]['names'][idx] = name
        save_tables()
        emit_table_state(tid)
        broadcast_lobby()

@socketio.on('rename_table')
def on_rename_table(data):
    from flask import request as freq
    tid = data.get('table_id')
    if tid not in tables: return
    if not is_admin(freq.sid, tid):
        emit('error', {'msg': 'Düzenleme yetkiniz yok'}); return
    name = data.get('name', '').strip()
    if name:
        tables[tid]['name'] = name
        save_tables()
        emit_table_state(tid)
        broadcast_lobby()

@socketio.on('reset_game')
def on_reset_game(data):
    from flask import request as freq
    tid = data.get('table_id')
    if tid not in tables: return
    if not is_admin(freq.sid, tid):
        emit('error', {'msg': 'Düzenleme yetkiniz yok'}); return
    tables[tid]['rounds'] = []
    save_tables()
    emit_table_state(tid)
    broadcast_lobby()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)
