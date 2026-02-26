import os
import uuid
import mysql.connector
from numpy import roll
import pandas as pd 
import smtplib
import threading
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from mysql.connector import pooling # Pooling Support
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
from groq import Groq
from PyPDF2 import PdfReader

# Environment Variables Load
load_dotenv()

# --- DIRECTORY SETUP ---
UPLOAD_FOLDER = 'static/uploads/id_docs'    # Photos for user ID
KNOWLEDGE_FOLDER = 'static/uploads/ai_docs' # AI Files 
NOTES_FOLDER = 'static/uploads/notes'

if not os.path.exists(NOTES_FOLDER):
    os.makedirs(NOTES_FOLDER)


for folder in [UPLOAD_FOLDER, KNOWLEDGE_FOLDER]:
    if not os.path.exists(folder):
        os.makedirs(folder)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, support_credentials=True)

# --- CONFIGURATION ---
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# --- DATABASE CONNECTION POOL ---
db_config = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD"),
    "database": "college_db"
}

# Connection Pool Creation (20 Connections set)
try:
    db_pool = pooling.MySQLConnectionPool(
        pool_name="coe_pool",
        pool_size=20, 
        pool_reset_session=True,
        **db_config
    )
    print("Database Pool Created Successfully!")
except Exception as e:
    print(f"Error creating DB Pool: {e}")
    db_pool = None

# --- DB CONNECTION HELPER ---
def get_db_connection():
    try:
        if db_pool:
            return db_pool.get_connection()
        else:
            return None
    except Exception as e:
        print(f"Pool Exhausted or Error: {e}")
        return None

# --- AI CONTEXT HELPER ---
def get_full_context():
    conn = None
    try:
        conn = get_db_connection()
        if not conn: return ""
        
        cursor = conn.cursor()
        cursor.execute("SELECT category, content FROM college_info")
        info = cursor.fetchall()
        cursor.execute("SELECT course, year_sem, time_slot, subject, room_no FROM timetable")
        tt = cursor.fetchall()
        
        context = "College Info:\n" + "\n".join([f"{r[0]}: {r[1]}" for r in info])
        context += "\n\nTimetable 2025-26:\n" + "\n".join([f"{r[0]} {r[1]} - {r[3]} at {r[2]} in {r[4]}" for r in tt])
        return context
    except Exception as e:
        print(f"Context Error: {e}")
        return ""
    finally:
        if conn:
            conn.close() # Connection wapas pool mein


MAIL_SENDER = os.getenv("MAIL_SENDER")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")

# --- PROFESSIONAL HTML EMAIL TEMPLATE ---
EMAIL_HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <style>
        .container {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 600px; margin: auto; border: 1px solid #e0e0e0; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 15px rgba(0,0,0,0.05); }}
        .header {{ background: linear-gradient(135deg, #6a11cb 0%, #2575fc 100%); color: white; padding: 30px; text-align: center; }}
        .body {{ padding: 30px; line-height: 1.6; color: #333; }}
        .btn {{ display: inline-block; padding: 12px 25px; background-color: #6a11cb; color: #ffffff !important; text-decoration: none; border-radius: 6px; font-weight: bold; margin-top: 20px; }}
        .footer {{ background: #f9f9f9; padding: 20px; text-align: center; font-size: 12px; color: #888; border-top: 1px solid #eee; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin:0;">COE Assistant Update</h1>
        </div>
        <div class="body">
            <h3>Hello {name},</h3>
            <p style="font-size: 16px;">{content}</p>
            <center><a href="http://localhost:5000" class="btn">Go to Dashboard</a></center>
            <p>Best Regards,<br><strong>Admin Team, G.C. Sanjauli</strong></p>
        </div>
        <div class="footer">
            &copy; 2026 COE Student Support System. This is an automated email.
        </div>
    </div>
</body>
</html>
"""

def send_bulk_notifications(recipient_data_list, subject, body_content):
    """this function is internal taht sends the mail"""
    sender_email = os.getenv("MAIL_SENDER")
    sender_password = os.getenv("MAIL_PASSWORD")

    if not sender_email or not sender_password:
        print("!!! ERROR: Email credentials missing !!!")
        return

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender_email, sender_password)

        for student in recipient_data_list:
            if not student.get('email'): continue
            
            msg = MIMEMultipart()
            msg['From'] = f"COE Admin <{sender_email}>"
            msg['To'] = student['email']
            msg['Subject'] = subject
            
            final_html = EMAIL_HTML_TEMPLATE.format(name=student['name'], content=body_content)
            msg.attach(MIMEText(final_html, 'html'))
            
            server.send_message(msg)
            
        server.quit()
        print(f"-> SUCCESS: {len(recipient_data_list)} Emails sent in background.")
    except Exception as e:
        print(f"!!! BACKGROUND MAIL ERROR: {str(e)} !!!")

def send_email_async(recipient_data_list, subject, body_content):
    """Is function ko apne routes mein call karein (Background processing)"""
    thread = threading.Thread(target=send_bulk_notifications, args=(recipient_data_list, subject, body_content))
    thread.daemon = True # Taki main process exit hone par ye bhi stop ho jaye
    thread.start()


def send_marks_email_with_delay(recipient_data_list, subject, body_content):
    def delayed_task():
        # 5 Minute (300 seconds) wait karega
        print("Waiting 5 minutes before sending marks update emails...")
        time.sleep(30) 
        send_bulk_notifications(recipient_data_list, subject, body_content)
        print("Delayed emails sent successfully!")

    # Thread start karein taaki server free rahe
    thread = threading.Thread(target=delayed_task)
    thread.daemon = True
    thread.start()

# new chat  AI route with fetching previous chat
@app.route('/ask', methods=['POST'])
def ask_ai():
    conn = None
    try:
        data = request.json
        user_query = data.get("message", "")
        user_email = data.get("email")
        
        college_knowledge = get_full_context()

        conn = get_db_connection()
        past_chats = []
        if conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT user_query, bot_response FROM chat_history WHERE user_email = %s ORDER BY timestamp DESC LIMIT 5", (user_email,))
            rows = cursor.fetchall()
            for r in reversed(rows):
                past_chats.append({"role": "user", "content": r['user_query']})
                past_chats.append({"role": "assistant", "content": r['bot_response']})

        messages = [
            {"role": "system", "content": f"You are the COE Assistant. Use this context: {college_knowledge}"}
        ]
        
        messages.extend(past_chats)
        
        messages.append({"role": "user", "content": user_query})

        chat_completion = client.chat.completions.create(
            messages=messages,
            model="llama-3.3-70b-versatile",
            temperature=0.7,
        )
        answer = chat_completion.choices[0].message.content
        
        if conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO chat_history (user_email, user_query, bot_response) VALUES (%s, %s, %s)", 
                           (user_email, user_query, answer))
            conn.commit()
            
        return jsonify({"answer": answer})
    except Exception as e:
        print(f"AI Error: {e}")
        return jsonify({"answer": "Error occurred ."}), 500
    finally:
        if conn:
            conn.close()

# --- AUTH ROUTES ---

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    # print("data receive")
    # print(data)
    email = data.get('email').strip()
    roll = data.get('roll').strip()
    course = data.get('course')
    gender = data.get('gender')
    if roll.upper() != "GUEST":
        if not (roll.isdigit() and len(roll) == 8):
            return jsonify({"success": False, "message": "ROLL NO. should be 8 digits"}), 400 
        
    conn = None
    try:
        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "Server Busy"}), 503
        
        cursor = conn.cursor(dictionary=True)

        if roll != 'GUEST':
            cursor.execute("SELECT * FROM users WHERE roll=%s AND course=%s", (roll, course))
            if cursor.fetchone():
                return jsonify({"success": False, "message": f"Roll No {roll} is already registered!"})

        dob = data.get('dob')
        password = dob.replace("-", "") if dob else "123456"
        
        cursor.execute('''INSERT INTO users (email, password, name, dob,gender, roll, course, phone) 
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''', 
                       (email, password, data['name'], data['dob'], gender ,data['roll'], data['course'], data['phone']))
        conn.commit()
        return jsonify({"success": True, "userId": email, "password": password})
    
    except mysql.connector.Error as err:
        if err.errno == 1062:
            return jsonify({"success": False, "message": "Email already registered!"})
        return jsonify({"success": False, "message": str(err)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()


ADMIN_EMAIL = os.getenv("ADMIN_EMAIL") 
ADMIN_PASS = os.getenv("ADMIN_PASS")

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    user_id = data.get('userId', '').strip()
    password = data.get('password', '').strip()

    if user_id == ADMIN_EMAIL and password == ADMIN_PASS:
        return jsonify({"success": True, "userName": "Administrator", "isAdmin": True})

    conn = None
    try:
        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "Server Busy"}), 503

        cursor = conn.cursor()
        cursor.execute("SELECT name FROM users WHERE email=%s AND password=%s", (user_id, password))
        user = cursor.fetchone()
        
        if user:
            return jsonify({"success": True, "userName": user[0], "isAdmin": False})
        return jsonify({"success": False, "message": "Invalid Email or Password!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/forgot_password', methods=['POST'])
def forgot_password():
    conn = None
    try:
        data = request.json
        email = data.get('email').strip()
        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "Server Busy"}), 503

        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT password FROM users WHERE email=%s", (email,))
        user = cursor.fetchone()
        
        if user:
            return jsonify({"success": True, "password": user['password']})
        return jsonify({"success": False, "message": "Email not found!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()

@app.route('/forgot_userid', methods=['POST'])
def forgot_userid():
    conn = None
    try:
        data = request.json
        name = data.get('name', '').strip().lower()
        roll = data.get('roll', '').strip().lower()
        course = data.get('course', '').strip().lower()
        
        if not roll or not course:
            return jsonify({"success": False, "message": "Roll No and Course are required!"})

        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "Server Busy"}), 503

        cursor = conn.cursor(dictionary=True)
        
        if name:
            query = "SELECT email FROM users WHERE LOWER(name)=%s AND LOWER(roll)=%s AND LOWER(course)=%s"
            params = (name, roll, course)
        else:
            query = "SELECT email FROM users WHERE LOWER(roll)=%s AND LOWER(course)=%s"
            params = (roll, course)
            
        cursor.execute(query, params)
        user = cursor.fetchone()
        
        if user:
            return jsonify({"success": True, "userId": user['email']})
        return jsonify({"success": False, "message": "No matching record found."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()

# --- USER DATA ROUTES ---

@app.route('/history', methods=['POST'])
def get_user_history():
    conn = None
    try:
        data = request.json
        user_email = data.get('email')
        conn = get_db_connection()
        if not conn: return jsonify([])

        cursor = conn.cursor()
        cursor.execute("SELECT user_query, bot_response, timestamp FROM chat_history WHERE user_email = %s ORDER BY timestamp DESC", (user_email,))
        rows = cursor.fetchall()
        history = [{"user": r[0], "bot": r[1], "time": str(r[2])} for r in rows]
        return jsonify(history)
    except Exception:
        return jsonify([])
    finally:
        if conn: conn.close()

@app.route('/get_profile', methods=['POST'])
def get_profile():
    conn = None
    try:
        data = request.json
        email = data.get('email')
        conn = get_db_connection()
        if not conn: return jsonify({"error": "DB Error"}), 500

        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT name, roll, course, phone, attendance, internal_grade FROM users WHERE email=%s", (email,))
        user = cursor.fetchone()
        
        if user: return jsonify(user)
        return jsonify({"error": "User not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/update_profile', methods=['POST'])
def update_profile():
    conn = None
    try:
        data = request.json
        email = data.get('email')
        name = data.get('name')
        roll = data.get('roll')
        course = data.get('course')
        phone = data.get('phone')

        if not email:
            return jsonify({"success": False, "message": "Email is required!"}), 400

        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "Server Busy"}), 503

        cursor = conn.cursor()
        query = """
            UPDATE users 
            SET name=%s, roll=%s, course=%s, phone=%s 
            WHERE email=%s
        """
        cursor.execute(query, (name, roll, course, phone, email))
        conn.commit()
        
        if cursor.rowcount > 0:
            return jsonify({"success": True, "message": "Profile updated successfully!"})
        else:
            return jsonify({"success": False, "message": "User not found or no changes made."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

# --- ADMIN ROUTES ---

@app.route('/admin/get_users', methods=['GET'])
def admin_get_users():
    conn = None
    try:
        conn = get_db_connection()
        if not conn: return jsonify([])
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT name, email, roll, course, phone, dob,gender, attendance, internal_grade FROM users")
        users = cursor.fetchall()
        return jsonify(users)
    except Exception:
        return jsonify([])
    finally:
        if conn: conn.close()

@app.route('/admin/update_attendance', methods=['POST'])
def update_attendance():
    conn = None
    try:
        data = request.json
        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "DB Busy"})
        
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET attendance=%s, internal_grade=%s WHERE email=%s", 
                       (data['attendance'], data['grade'], data['email']))
        conn.commit()
        return jsonify({"success": True, "message": "Records updated successfully!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()

@app.route('/admin/delete_user', methods=['POST'])
def delete_user():
    conn = None
    try:
        data = request.json
        email = data.get('email')
        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "DB Busy"})
        
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE email=%s", (email,))
        conn.commit()
        return jsonify({"success": True, "message": "User deleted successfully"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()

# --- KNOWLEDGE BASE ROUTES ---

@app.route('/admin/upload_knowledge', methods=['POST'])
def upload_knowledge():
    conn = None
    try:
        if 'file' not in request.files:
            return jsonify({"success": False, "message": "No file part"})
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"success": False, "message": "No selected file"})

        filename = secure_filename(file.filename)
        filepath = os.path.join(KNOWLEDGE_FOLDER, filename)
        file.save(filepath)

        text_content = ""
        if filename.endswith('.pdf'):
            reader = PdfReader(filepath)
            for page in reader.pages:
                text_content += page.extract_text()
        else:
            with open(filepath, 'r', encoding='utf-8') as f:
                text_content = f.read()

        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "DB Busy"})

        cursor = conn.cursor()
        cursor.execute("INSERT INTO college_info (category, content) VALUES (%s, %s)", 
                       (f"Document: {filename}", text_content))
        conn.commit()
        return jsonify({"success": True, "message": "File processed and added to AI knowledge!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()

@app.route('/admin/get_knowledge', methods=['GET'])
def get_knowledge():
    conn = None
    try:
        conn = get_db_connection()
        if not conn: return jsonify([])
        
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, category, created_at, SUBSTRING(content, 1, 100) as preview 
            FROM college_info 
            WHERE category LIKE 'Document:%' 
            ORDER BY id DESC
        """)
        docs = cursor.fetchall()
        
        for doc in docs:
            if doc['created_at']:
                doc['created_at'] = doc['created_at'].strftime("%Y-%m-%d %H:%M:%S")
                
        return jsonify(docs)
    except Exception as e:
        print(f"Error: {e}")
        return jsonify([])
    finally:
        if conn: conn.close()

@app.route('/admin/delete_knowledge', methods=['POST'])
def delete_knowledge():
    conn = None
    try:
        data = request.json
        doc_id = data.get('id')
        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "DB Busy"})
        
        cursor = conn.cursor()
        cursor.execute("DELETE FROM college_info WHERE id=%s", (doc_id,))
        conn.commit()
        return jsonify({"success": True, "message": "Knowledge deleted permanently!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()

# --- TIMETABLE ROUTES ---

@app.route('/admin/add_timetable', methods=['POST'])
def add_timetable():
    conn = None
    try:
        data = request.json
        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "DB Busy"})
        
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO timetable (course, year_sem, time_slot, subject, room_no)
            VALUES (%s, %s, %s, %s, %s)
        ''', (data['course'], data['year'], data['time'], data['subject'], data['room']))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()

@app.route('/admin/delete_timetable', methods=['POST'])
def delete_timetable():
    conn = None
    try:
        data = request.json
        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "DB Busy"})
        
        cursor = conn.cursor()
        cursor.execute("DELETE FROM timetable WHERE id=%s", (data['id'],))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()

@app.route('/get_timetable', methods=['GET'])
def get_timetable():
    conn = None
    try:
        conn = get_db_connection()
        if not conn: return jsonify([])
        
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, course, year_sem as year, time_slot as time, subject, room_no as room FROM timetable")
        timetable = cursor.fetchall()
        return jsonify(timetable)
    except Exception:
        return jsonify([])
    finally:
        if conn: conn.close()

# --- RESULTS & MARKS ROUTES ---

@app.route('/admin/add_bulk_marks', methods=['POST'])
def add_bulk_marks():
    conn = None
    try:
        data = request.json
        email = data.get('email')
        results = data.get('results')

        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "DB Busy"})
        
        cursor = conn.cursor()
        
        cursor.execute("SELECT email FROM users WHERE email=%s", (email,))
        if not cursor.fetchone():
            return jsonify({"success": False, "message": "User not found!"})

        for item in results:
            cursor.execute('''
                INSERT INTO results (email, subject, marks, total_marks)
                VALUES (%s, %s, %s, %s)
            ''', (email, item['subject'], item['marks'], item['total']))
        
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()

@app.route('/get_result', methods=['POST'])
def get_result():
    conn = None
    try:
        data = request.json
        email = data.get('email')
        
        if not email: return jsonify({"success": False, "message": "Email is required"}), 400
            
        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "DB Busy"}), 503
        
        cursor = conn.cursor(dictionary=True) 
        cursor.execute("SELECT subject, marks, total_marks FROM results WHERE email=%s", (email,))
        results = cursor.fetchall()
        
        if results:
            return jsonify({"success": True, "results": results})
        else:
            return jsonify({"success": False, "message": "No results found."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/admin/get_result_by_roll', methods=['POST'])
def get_result_by_roll():
    conn = None
    try:
        data = request.json
        roll = data.get('roll')
        name_filter = data.get('name', '').strip()
        course_filter = data.get('course', '').strip()

        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "DB Busy"}), 503
        
        cursor = conn.cursor(dictionary=True)
        
        sql = "SELECT email, name, course, roll FROM users WHERE roll=%s"
        params = [roll]
        if name_filter:
            sql += " AND name LIKE %s"
            params.append(f"%{name_filter}%")
        if course_filter:
            sql += " AND course=%s"
            params.append(course_filter)

        cursor.execute(sql, tuple(params))
        user = cursor.fetchone()
        
        if not user:
            return jsonify({"success": False, "message": "Student not found!"})

        cursor.execute("SELECT id, subject, marks, total_marks FROM results WHERE email=%s", (user['email'],))
        results = cursor.fetchall()
        
        return jsonify({
            "success": True, 
            "results": results, 
            "name": user['name'], 
            "email": user['email'],
            "course": user['course']
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()

@app.route('/admin/delete_all_marks', methods=['POST'])
def delete_all_marks():
    conn = None
    try:
        data = request.json
        email = data.get('email')
        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "DB Busy"})
        
        cursor = conn.cursor()
        cursor.execute("DELETE FROM results WHERE email=%s", (email,))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()

@app.route('/admin/update_result', methods=['POST'])
def update_result():
    conn = None
    try:
        data = request.json
        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "DB Busy"})
        
        cursor = conn.cursor()
        cursor.execute("UPDATE results SET marks=%s, total_marks=%s WHERE id=%s", 
                       (data['marks'], data['total'], data['id']))
        conn.commit()
        return jsonify({"success": True, "message": "Marks updated successfully"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()

@app.route('/admin/delete_result_entry', methods=['POST'])
def delete_result_entry():
    conn = None
    try:
        data = request.json
        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "DB Busy"})
        
        cursor = conn.cursor()
        cursor.execute("DELETE FROM results WHERE id=%s", (data['id'],))
        conn.commit()
        return jsonify({"success": True, "message": "Entry deleted"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()

# --- ID CARD ROUTES ---

@app.route('/apply_id', methods=['POST'])
def apply_id():
    conn = None
    try:
        email = request.form.get('email')
        name = request.form.get('fullName')
        roll = request.form.get('rollNo')
        dept = request.form.get('dept')
        year = request.form.get('year')
        father = request.form.get('fatherName')
        mother = request.form.get('motherName')
        phone = request.form.get('phone')
        gender = request.form.get('gender')

        if roll and len(roll.strip()) < 8:
            return jsonify({"success": False, "message": "Roll Number is Minimum 8 digits!"}), 400

        photo = request.files.get('photo')
        sign = request.files.get('sign')
        marksheet = request.files.get('marksheet')

        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "DB Busy"}), 503
        
        cursor = conn.cursor(dictionary=True)

        check_query = "SELECT id FROM id_applications WHERE roll_no = %s AND department = %s AND academic_year = %s"
        cursor.execute(check_query, (roll, dept, year))
        if cursor.fetchone():
            return jsonify({"success": False, "message": f"You already applied for {year}"}), 400

        photo_fn = secure_filename(f"photo_{roll}_{photo.filename}")
        sign_fn = secure_filename(f"sign_{roll}_{sign.filename}")
        mark_fn = secure_filename(f"mark_{roll}_{marksheet.filename}")

        photo.save(os.path.join(UPLOAD_FOLDER, photo_fn))
        sign.save(os.path.join(UPLOAD_FOLDER, sign_fn))
        marksheet.save(os.path.join(UPLOAD_FOLDER, mark_fn))

        # Insert DB
        query = """
            INSERT INTO id_applications 
            (email, full_name, roll_no, department, academic_year, father_name, mother_name, phone, gender, photo_path, signature_path, marksheet_path, status) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Pending')
        """
        cursor.execute(query, (email, name, roll, dept, year, father, mother, phone, gender, photo_fn, sign_fn, mark_fn))
        conn.commit()
        return jsonify({"success": True, "message": "Application successfully submitted!"})

    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/admin/get_pending_id_apps', methods=['GET'])
def get_pending_id_apps():
    conn = None
    try:
        conn = get_db_connection()
        if not conn: return jsonify([])
        
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, full_name as fullName, roll_no as rollNo, 
                   department as dept, father_name as fatherName, 
                   mother_name as motherName, photo_path as photo, 
                   marksheet_path as marksheet, academic_year, phone 
            FROM id_applications WHERE status='Pending'
        """)
        apps = cursor.fetchall()
        return jsonify(apps)
    except Exception:
        return jsonify([])
    finally:
        if conn: conn.close()

# id card status

@app.route('/admin/update_id_status', methods=['POST'])
def update_id_status():
    data = request.json
    app_id = data.get('id')
    status = data.get('status')
    
    unique_id = None
    if status == 'Approved':
        unique_id = f"COE-{uuid.uuid4().hex[:6].upper()}" 

    conn = get_db_connection()
    if not conn: return jsonify({"success": False, "message": "Database Error"}), 503
    
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT email, full_name, roll_no FROM id_applications WHERE id = %s", (app_id,))
        student = cursor.fetchone()
        
        if not student:
            return jsonify({"success": False, "message": "Application not found"}), 404
        cursor.execute("UPDATE id_applications SET status = %s, unique_id = %s WHERE id = %s", 
                       (status, unique_id, app_id))
        conn.commit()

        if status == 'Approved':
            subject = "Official Update: Your ID Card is Approved! 🎓"
            
            content = f"""
            Your application for the Digital Student Identity Card has been <b>verified and approved</b>. 
            Your Unique Student ID is: <span style='color:#6a11cb; font-size:18px;'>{unique_id}</span>.
            <br><br>
            You can now download your card from your dashboard.
            """
            
            send_email_async([{'email': student['email'], 'name': student['full_name']}], subject, content)

        return jsonify({"success": True, "message": "Status updated instantly!"})

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"success": False, "message": str(e)})
    finally:
        conn.close()

@app.route('/api/get_verified_id', methods=['GET'])
def get_verified_id():
    conn = None
    try:
        email = request.args.get('email')
        if not email: return jsonify({"success": False, "message": "Email required"}), 400
            
        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "DB Busy"}), 503
        
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM id_applications WHERE email=%s AND status='Approved'", (email,))
        data = cursor.fetchone()
        
        if data:
            data['success'] = True
            return jsonify(data)
        else:
            return jsonify({"success": False, "message": "ID not approved or found"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/admin/full_edit_id_app', methods=['POST'])
def full_edit_id_app():
    conn = None
    try:
        app_id = request.form.get('id')
        name = request.form.get('fullName')
        roll = request.form.get('rollNo')
        father = request.form.get('fatherName')
        mother = request.form.get('motherName')
        dept = request.form.get('dept')
        year = request.form.get('year')
        phone = request.form.get('phone')

        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "DB Busy"}), 503
        
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE id_applications 
            SET full_name=%s, roll_no=%s, father_name=%s, mother_name=%s, department=%s, academic_year=%s, phone=%s
            WHERE id=%s
        """, (name, roll, father, mother, dept, year, phone, app_id))

        photo = request.files.get('photo')
        marksheet = request.files.get('marksheet')

        if photo:
            filename = secure_filename(f"photo_revised_{roll}_{photo.filename}")
            photo.save(os.path.join(UPLOAD_FOLDER, filename))
            cursor.execute("UPDATE id_applications SET photo_path=%s WHERE id=%s", (filename, app_id))

        if marksheet:
            filename = secure_filename(f"mark_revised_{roll}_{marksheet.filename}")
            marksheet.save(os.path.join(UPLOAD_FOLDER, filename))
            cursor.execute("UPDATE id_applications SET marksheet_path=%s WHERE id=%s", (filename, app_id))

        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/admin/get_verified_students', methods=['GET'])
def get_verified_students():
    conn = None
    try:
        conn = get_db_connection()
        if not conn: return jsonify({"error": "DB Busy"}), 503
        
        cursor = conn.cursor(dictionary=True)
        query = "SELECT * FROM id_applications WHERE status = 'Approved' ORDER BY id DESC"
        cursor.execute(query)
        students = cursor.fetchall()
        return jsonify(students)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/get_all_ids', methods=['GET'])
def get_all_ids():
    conn = None
    try:
        email = request.args.get('email')
        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "DB Busy"}), 503
        
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM id_applications WHERE email=%s ORDER BY academic_year DESC", (email,))
        cards = cursor.fetchall()
        return jsonify({"success": True, "cards": cards})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()

# admin bulk import 
@app.route('/admin/import_bulk_marks', methods=['POST'])
def import_bulk_marks():
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "No file uploaded"})
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "No file selected"})

    conn = None
    try:
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file, engine='openpyxl')

        df.columns = [str(col).strip().upper() for col in df.columns]

        required = ['ROLL_NO', 'SUBJECT', 'MARKS', 'TOTAL_MARKS']
        if not all(col in df.columns for col in required):
            return jsonify({
                "success": False, 
                "message": f"Columns missing! Headers must be: {', '.join(required)}"
            })

        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "DB Busy"}), 503
        
        cursor = conn.cursor(dictionary=True)
        count = 0
        notified_students = [] 

        for index, row in df.iterrows():
            roll_raw = str(row['ROLL_NO']).strip()
            roll = roll_raw.split('.')[0] if '.' in roll_raw else roll_raw
            
            cursor.execute("SELECT email, name FROM users WHERE roll = %s", (roll,))
            user = cursor.fetchone()
            
            if user:
                sem = str(row.get('SEMESTER/YEAR', 'N/A'))
                cursor.execute('''
                    INSERT INTO results (email, subject, marks, total_marks, semester)
                    VALUES (%s, %s, %s, %s, %s)
                ''', (user['email'], row['SUBJECT'], row['MARKS'], row['TOTAL_MARKS'], sem))
                
                notified_students.append({'email': user['email'], 'name': user['name']})
                count += 1

        cursor.execute("INSERT INTO college_info (category, content) VALUES (%s, %s)", 
                       (f"Document: {file.filename}", f"Bulk marks successfully imported for {count} students."))
        
        conn.commit()

        if notified_students:
            subject = "Academic Achievement: New Results Published! 📚"
            body_text = """
            We are pleased to inform you that your <b>Academic Results</b> have been officially updated on the student portal. 
            Your hard work and dedication are reflected in your performance.
            <br><br>
            Please log in to your dashboard to view your subject-wise marks and overall performance.
            """
            send_marks_email_with_delay(notified_students, subject, body_text)

        return jsonify({
            "success": True, 
            "count": count, 
            "message": f"Successfully imported {count} records. Emails will be sent in 5 minutes."
        })

    except Exception as e:
        print(f"IMPORT ERROR: {str(e)}")
        return jsonify({"success": False, "message": f"Error: {str(e)}"})
    finally:
        if conn: conn.close()

@app.route('/admin/clear_all_results_database', methods=['POST'])
def clear_all_results_database():
    conn = None
    try:
        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "DB Busy"}), 503
        
        cursor = conn.cursor()
        cursor.execute("DELETE FROM results") 
        conn.commit()
        return jsonify({"success": True, "message": "All marks deleted successfully"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()


# --- NOTES HUB ROUTES ---

@app.route('/admin/upload_notes', methods=['POST'])
def upload_notes():
    """admin and student both use that route"""
    conn = None
    try:
        email = request.form.get('email', 'Admin') 
        subject = request.form.get('subject')
        title = request.form.get('title')
        file = request.files.get('file')

        if not file or file.filename == '':
            return jsonify({"success": False, "message": "No file selected"}), 400

        original_fn = secure_filename(file.filename)
        filename = f"NOTE_{uuid.uuid4().hex[:6]}_{original_fn}"
        
        NOTES_FOLDER = 'static/uploads/notes'
        if not os.path.exists(NOTES_FOLDER):
            os.makedirs(NOTES_FOLDER)
            
        file.save(os.path.join(NOTES_FOLDER, filename))
        
        conn = get_db_connection()
        if not conn: return jsonify({"success": False, "message": "Server Busy"}), 503
        
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO study_notes (subject, title, file_path, uploaded_by) 
            VALUES (%s, %s, %s, %s)
        """, (subject, title, filename, email))
        
        conn.commit()
        return jsonify({"success": True, "message": "Notes uploaded successfully!"})
        
    except Exception as e:
        print(f"Notes Upload Error: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/get_notes', methods=['GET'])
def get_notes():
    """Sare notes fetch karne ke liye (Official aur Community dono)"""
    conn = None
    try:
        conn = get_db_connection()
        if not conn: return jsonify([])
        
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM study_notes ORDER BY created_at DESC")
        notes = cursor.fetchall()
        
        for note in notes:
            if note['created_at']:
                note['created_at'] = note['created_at'].strftime("%Y-%m-%d %H:%M:%S")
                
        return jsonify(notes)
    except Exception as e:
        print(f"Fetch Notes Error: {str(e)}")
        return jsonify([])
    finally:
        if conn: conn.close()

@app.route('/admin/delete_note', methods=['POST'])
def delete_note():
    """Admin kisi galat notes ko delete kar sake"""
    conn = None
    try:
        data = request.json
        note_id = data.get('id')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM study_notes WHERE id=%s", (note_id,))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    finally:
        if conn: conn.close()
       


 # --- COMMUNITY CHAT ROUTES ---

@app.route('/api/community/send', methods=['POST'])
def send_community_msg():
    """Student ka message community_chats table mein save karne ke liye"""
    conn = None
    try:
        data = request.json
        email = data.get('email')
        name = data.get('name')
        message = data.get('message')

        if not message:
            return jsonify({"success": False, "message": "Message cannot be empty"})

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO community_chats (user_email, user_name, message) VALUES (%s, %s, %s)",
                       (email, name, message))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        print(f"Send Community Msg Error: {str(e)}")
        return jsonify({"success": False, "error": str(e)})
    finally:
        if conn: conn.close()

@app.route('/api/community/messages', methods=['GET'])
def get_community_messages():
    """Database se saare community messages fetch karne ke liye"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM community_chats ORDER BY timestamp ASC")
        messages = cursor.fetchall()
        
        for msg in messages:
            if msg['timestamp']:
                msg['timestamp'] = msg['timestamp'].strftime("%Y-%m-%d %H:%M:%S")
                
        return jsonify(messages)
    except Exception as e:
        print(f"Get Community Msgs Error: {str(e)}")
        return jsonify([])
    finally:
        if conn: conn.close()

@app.route('/api/community/delete', methods=['POST'])
def delete_community_msg():
    """Sirf wahi user apna message delete kar sake jisne bheja tha"""
    conn = None
    try:
        data = request.json
        msg_id = data.get('id')
        user_email = data.get('email')

        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM community_chats WHERE id=%s AND user_email=%s", (msg_id, user_email))
        conn.commit()
        
        if cursor.rowcount > 0:
            return jsonify({"success": True, "message": "Message deleted"})
        else:
            return jsonify({"success": False, "message": "Unauthorized or not found"})
            
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})
    finally:
        if conn: conn.close()      


if __name__ == '__main__':
    app.run(port=5000, debug=True)