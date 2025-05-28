import os
import random
from flask import Flask, render_template, session, request, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room, disconnect
from flask_session import Session
from uuid import uuid4

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', str(uuid4()))
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)
socketio = SocketIO(app, manage_session=False)

# In-memory waiting and active chat management
waiting_users = []
active_chats = {}

def find_match(user):
    for i, stranger in enumerate(waiting_users):
        # Each must match other's requirements
        if (
            (user['gender_pref'] == 'Any' or stranger['gender'] == user['gender_pref'])
            and (stranger['gender_pref'] == 'Any' or user['gender'] == stranger['gender_pref'])
            and (stranger['age_min'] <= user['age'] <= stranger['age_max'])
            and (user['age_min'] <= stranger['age'] <= user['age_max'])
            and (stranger['sid'] != user['sid'])
        ):
            return i, stranger
    return None, None

@app.route('/', methods=['GET'])
def index():
    return render_template('setup.html')

@app.route('/connecting', methods=['POST'])
def connecting():
    # Get preferences
    username = request.form.get('username', '').strip() or 'Stranger'
    gender = request.form.get('gender', 'Prefer not to say')
    gender_pref = request.form.get('gender_pref', 'Any')
    age = int(request.form.get('age', 18))
    age_min = int(request.form.get('age_min', 18))
    age_max = int(request.form.get('age_max', 60))

    session['user'] = {
        'username': username,
        'gender': gender,
        'gender_pref': gender_pref,
        'age': age,
        'age_min': age_min,
        'age_max': age_max,
        'sid': None
    }
    return render_template('connecting.html',
                           username=username,
                           gender=gender,
                           gender_pref=gender_pref,
                           age=age,
                           age_min=age_min,
                           age_max=age_max
                           )

@app.route('/chat')
def chat():
    if 'user' not in session:
        return redirect(url_for('index'))
    return render_template('chat.html')

@socketio.on('connect')
def handle_connect():
    if 'user' not in session:
        disconnect()
        return
    session['user']['sid'] = request.sid

@socketio.on('find_partner')
def find_partner(data):
    user = session.get('user')
    if not user:
        emit('match_failed')
        return
    user['sid'] = request.sid

    idx, stranger = find_match(user)
    if stranger:
        # Found a match, start chat
        room = str(uuid4())
        stranger_sid = stranger['sid']
        waiting_users.pop(idx)
        active_chats[room] = {'users': [user, stranger]}
        join_room(room, sid=user['sid'])
        join_room(room, sid=stranger_sid)
        # Send each user the other's info, but never the real username if blank
        emit('matched', {
            'room': room,
            'you': {
                'username': user['username'] if user['username'] else "Stranger",
                'gender': user['gender'],
                'age': user['age']
            },
            'stranger': {
                'username': stranger['username'] if stranger['username'] else "Stranger",
                'gender': stranger['gender'],
                'age': stranger['age']
            }
        }, room=user['sid'])
        emit('matched', {
            'room': room,
            'you': {
                'username': stranger['username'] if stranger['username'] else "Stranger",
                'gender': stranger['gender'],
                'age': stranger['age']
            },
            'stranger': {
                'username': user['username'] if user['username'] else "Stranger",
                'gender': user['gender'],
                'age': user['age']
            }
        }, room=stranger_sid)
    else:
        waiting_users.append(user)

@socketio.on('send_message')
def handle_message(data):
    room = data.get('room')
    msg = data.get('msg')
    if not room or room not in active_chats:
        return
    user = session.get('user')
    # Add timestamp
    from datetime import datetime
    timestamp = datetime.utcnow().strftime('%H:%M')
    # Send to sender
    emit('receive_message', {
        'sender': 'You',
        'msg': msg,
        'username': user['username'] if user['username'] else 'Stranger',
        'time': timestamp,
    }, room=request.sid)
    # Send to stranger
    for u in active_chats[room]['users']:
        if u['sid'] != request.sid:
            emit('receive_message', {
                'sender': 'Stranger',
                'msg': msg,
                'username': user['username'] if user['username'] else 'Stranger',
                'time': timestamp,
            }, room=u['sid'])

@socketio.on('typing')
def handle_typing(data):
    room = data.get('room')
    if not room or room not in active_chats:
        return
    for u in active_chats[room]['users']:
        if u['sid'] != request.sid:
            emit('stranger_typing', {}, room=u['sid'])

@socketio.on('end_chat')
def handle_end_chat(data):
    room = data.get('room')
    user = session.get('user')
    if room in active_chats:
        other_user = None
        for u in active_chats[room]['users']:
            if u['sid'] != request.sid:
                other_user = u
                break
        if other_user:
            emit('chat_ended', {'username': user['username'] if user['username'] else "Stranger"}, room=other_user['sid'])
        del active_chats[room]
    leave_room(room)

@socketio.on('next_chat')
def handle_next_chat(data):
    handle_end_chat(data)
    emit('go_to_connecting', {})

@socketio.on('cancel_search')
def handle_cancel_search():
    sid = request.sid
    global waiting_users
    waiting_users = [u for u in waiting_users if u['sid'] != sid]

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    global waiting_users
    waiting_users = [u for u in waiting_users if u['sid'] != sid]
    # End active chat if any
    for room, chat in list(active_chats.items()):
        for user in chat['users']:
            if user['sid'] == sid:
                for other in chat['users']:
                    if other['sid'] != sid:
                        emit('chat_ended', {'username': user['username'] if user['username'] else "Stranger"}, room=other['sid'])
                del active_chats[room]
                break

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    socketio.run(app, host='0.0.0.0', port=port)
