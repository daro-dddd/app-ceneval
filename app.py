from flask import Flask, render_template, request, jsonify, session, send_file
from knowledge_base import get_topics, get_questions_for_round, KNOWLEDGE_BASE
import os
import sys
import io
import webbrowser
import threading
from fpdf import FPDF

# Configuración para que Flask encuentre templates y static cuando se compila en .exe con PyInstaller
if getattr(sys, 'frozen', False):
    template_folder = os.path.join(sys._MEIPASS, 'templates')
    static_folder = os.path.join(sys._MEIPASS, 'static')
    app = Flask(__name__, template_folder=template_folder, static_folder=static_folder)
else:
    app = Flask(__name__)

app.secret_key = os.environ.get('SECRET_KEY', 'clave_super_secreta_ceneval_produccion_12345')

# ---------------------------------------------------------
# SISTEMAS BASADOS EN EL CONOCIMIENTO Y MOTOR DE INFERENCIA
# ---------------------------------------------------------

def initialize_exam():
    """Inicializa las variables de estado del agente para el usuario actual."""
    session['topics'] = get_topics()
    session['current_topic_index'] = 0
    session['current_difficulty'] = 1
    session['questions_asked'] = 0
    session['consecutive_correct'] = 0
    session['global_consecutive_correct'] = 0
    session['perfect_score'] = False
    session['can_perfect_score'] = True
    session['round_correct_total'] = 0
    session['current_round_questions'] = []
    session['current_question_index'] = 0
    
    # Registro de métricas para la retroalimentación final adaptativa
    session['history'] = []
    
    load_next_round()

def load_next_round():
    """Carga las preguntas de la ronda actual según tema y dificultad (Motor de Inferencia)."""
    topics = session['topics']
    
    if session['questions_asked'] < 30:
        idx = session['current_topic_index'] % len(topics)
        session['current_topic_index'] = idx
        
        topic = topics[idx]
        diff = session['current_difficulty']
        
        history = session.get('history', [])
        asked_ids = [h.get('id') for h in history if 'id' in h]
        
        questions = get_questions_for_round(topic, diff, asked_ids)
        
        if not questions:
            # Tema agotado, avanzar forzosamente
            session['current_topic_index'] += 1
            load_next_round()
            return
        
        # Guardamos en sesión SOLO los IDs de las preguntas de esta ronda para ahorrar memoria (límite de cookies)
        session['current_round_questions'] = [q['id'] for q in questions]
        session['current_question_index'] = 0
        session['consecutive_correct'] = 0
        session['round_correct_total'] = 0
    else:
        # Fin del examen (límite de 30 preguntas)
        session['current_round_questions'] = []

def get_current_question():
    """Obtiene la pregunta actual de la ronda."""
    question_ids = session.get('current_round_questions', [])
    idx = session.get('current_question_index', 0)
    
    if idx < len(question_ids) and session['questions_asked'] < 30:
        q_id = question_ids[idx]
        for topic in KNOWLEDGE_BASE:
            for q in KNOWLEDGE_BASE[topic]:
                if q['id'] == q_id:
                    return q
    return None

def advance_topic():
    """Avanza al siguiente tema y reinicia dificultad (Regla de Inferencia)."""
    session['current_topic_index'] += 1
    session['current_difficulty'] = 1
    load_next_round()

def repeat_topic_with_higher_difficulty():
    """Repite el tema aumentando la dificultad si no se alcanzó el puntaje (Regla de Inferencia)."""
    if session['current_difficulty'] < 3:
        session['current_difficulty'] += 1
    # Si ya está en dificultad 3 y no lo logra, por ahora avanza (o podríamos dejar que repruebe el tema)
    else:
        session['current_topic_index'] += 1
        session['current_difficulty'] = 1
        
    load_next_round()

# ---------------------------------------------------------
# RUTAS DE LA APLICACIÓN WEB
# ---------------------------------------------------------

@app.route('/')
def index():
    # Reinicia el estado al cargar la página principal
    initialize_exam()
    return render_template('index.html')

@app.route('/get_state', methods=['GET'])
def get_state():
    """Devuelve el estado actual al frontend para renderizar la UI."""
    q = get_current_question()
    
    if q is None:
        # Evaluamos el resultado final
        score = sum([1 for h in session.get('history', []) if h['is_correct']])
        
        if session.get('perfect_score'):
            total = score
        else:
            total = 30 # Siempre se evalúa sobre 30 preguntas
        
        # Retroalimentación adaptativa final
        diagnosis = "Excelente. Tienes un dominio superior en las competencias evaluadas."
        if score < total * 0.6:
            diagnosis = "Insuficiente. Necesitas repasar los conceptos fundamentales de los temas."
        elif score < total * 0.8:
            diagnosis = "Suficiente. Tienes buenas bases, pero puedes mejorar en temas intermedios."

        return jsonify({
            "status": "finished",
            "score": score,
            "total_questions": total,
            "diagnosis": diagnosis,
            "history": session.get('history', [])
        })

    # Ocultar indicador 'is_correct' antes de enviar al frontend
    safe_options = [{"text": opt["text"], "id": i} for i, opt in enumerate(q["options"])]
    
    return jsonify({
        "status": "active",
        "topic": session['topics'][session['current_topic_index']],
        "difficulty": session['current_difficulty'],
        "question_number": session['questions_asked'] + 1,
        "question": {
            "context": q["context"],
            "text": q["question"],
            "options": safe_options
        }
    })

@app.route('/submit_answer', methods=['POST'])
def submit_answer():
    """Recibe la respuesta, evalúa (Motor de Inferencia) y envía retroalimentación (Agente Inteligente)."""
    data = request.json
    selected_option_id = data.get('option_id')
    time_taken = data.get('time_taken', 10.0)
    
    q = get_current_question()
    if not q:
        return jsonify({"error": "No hay pregunta activa."}), 400

    # Evaluamos si fue timeout
    if selected_option_id == -1:
        is_correct = False
        agent_feedback = "⏳ Tiempo agotado. Debes responder antes de 10 segundos. " + q['feedback']['incorrect']
        user_answer = "Sin responder (Tiempo Agotado)"
    else:
        is_correct = q['options'][selected_option_id]['is_correct']
        agent_feedback = q['feedback']['correct'] if is_correct else q['feedback']['incorrect']
        user_answer = q['options'][selected_option_id]['text']

    if not is_correct:
        session['can_perfect_score'] = False

    # Actualizar estado
    session['questions_asked'] += 1
    session['history'].append({
        "id": q.get("id"),
        "is_correct": is_correct,
        "user_answer": user_answer
    })
    
    # Razonamiento e Inferencia para adaptarse al usuario
    action = "continue"
    if is_correct:
        session['consecutive_correct'] += 1
        session['global_consecutive_correct'] += 1
        session['round_correct_total'] += 1
        
        if session['questions_asked'] == 12 and session.get('can_perfect_score', False):
            session['perfect_score'] = True
            session['current_round_questions'] = [] # Forzar fin del examen
            return jsonify({
                "is_correct": is_correct,
                "feedback": agent_feedback + " ¡Felicidades! Has contestado las primeras 12 preguntas correctamente sin ningún error. Has demostrado un dominio total y tu evaluación termina aquí con 100% de calificación.",
                "action": "advanced_topic"
            })
        
        # Regla: 3 aciertos consecutivos -> Avanza de tema
        if session['consecutive_correct'] == 3:
            advance_topic()
            action = "advanced_topic"
            agent_feedback += " ¡Excelente patrón! Tuviste 3 aciertos seguidos, avanzas al siguiente tema."
        else:
            session['current_question_index'] += 1
    else:
        session['consecutive_correct'] = 0
        session['global_consecutive_correct'] = 0
        session['current_question_index'] += 1

    # Verificar si terminó la ronda sin hacer el salto de 3 consecutivos
    if action == "continue" and session['current_question_index'] >= len(session.get('current_round_questions', [])):
        if session['round_correct_total'] >= 3:
            advance_topic()
            action = "advanced_topic"
            agent_feedback += " Ronda terminada. Tuviste suficientes aciertos para avanzar de tema."
        else:
            repeat_topic_with_higher_difficulty()
            action = "repeated_topic"
            agent_feedback += " Ronda terminada. Tuviste pocos aciertos, el Agente ha decidido repetir el tema con mayor dificultad para fortalecer el aprendizaje."
            
    # Límite global (30 preguntas)
    if session['questions_asked'] >= 30:
        session['current_round_questions'] = []
        action = "finished"

    # En caso de que se haya avanzado a la pregunta siguiente sin cambiar de ronda
    if action == "continue":
        pass

    session.modified = True

    return jsonify({
        "is_correct": is_correct,
        "feedback": agent_feedback,
        "action": action
    })

@app.route('/register', methods=['POST'])
def register_user():
    data = request.json
    session['user_name'] = data.get('nombre', '')
    session['user_lastname'] = data.get('apellido', '')
    session['user_email'] = data.get('correo', '')
    return jsonify({"status": "ok"})

def create_pdf_bytes():
    history = session.get('history', [])
    score = sum([1 for h in history if h['is_correct']])
    
    if session.get('perfect_score'):
        percentage = 100.0
        total = score
    else:
        total = 30
        percentage = (score / total) * 100 if total > 0 else 0
        
    nombre = session.get('user_name', '')
    apellido = session.get('user_lastname', '')
    
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", style="B", size=16)
    pdf.cell(0, 10, text="Reporte de Evaluación CENEVAL", align='C', new_x="LMARGIN", new_y="NEXT")
    
    if nombre or apellido:
        pdf.set_font("helvetica", style="I", size=12)
        pdf.cell(0, 10, text=f"Estudiante: {nombre} {apellido}", align='C', new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_font("helvetica", size=12)
    pdf.ln(5)
    pdf.cell(0, 10, text=f"Aciertos Totales: {score} / {total}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 10, text=f"Calificación: {percentage:.1f}%", new_x="LMARGIN", new_y="NEXT")
    
    pdf.ln(10)
    for i, h in enumerate(history):
        q_id = h.get('id')
        
        # Reconstruir la pregunta original desde KNOWLEDGE_BASE para no gastar sesión
        q_original = None
        for t in KNOWLEDGE_BASE:
            for q in KNOWLEDGE_BASE[t]:
                if q['id'] == q_id:
                    q_original = q
                    break
            if q_original: break
            
        if not q_original: continue
            
        topic = q_original['topic']
        difficulty = q_original['difficulty']
        question_text = q_original['question']
        correct_answer = next(opt['text'] for opt in q_original['options'] if opt['is_correct'])
        
        pdf.set_font("helvetica", style="B", size=10)
        pdf.multi_cell(0, 8, text=f"Pregunta {i+1} ({topic} - Nivel {difficulty}): {question_text}", new_x="LMARGIN", new_y="NEXT")
        
        pdf.set_font("helvetica", size=10)
        pdf.multi_cell(0, 8, text=f"Tu respuesta: {h['user_answer']}", new_x="LMARGIN", new_y="NEXT")
        if not h['is_correct']:
            pdf.multi_cell(0, 8, text=f"Respuesta correcta: {correct_answer}", new_x="LMARGIN", new_y="NEXT")
        
        resultado = "CORRECTO" if h['is_correct'] else "INCORRECTO"
        pdf.multi_cell(0, 8, text=f"Resultado: {resultado}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(5)
    
    return bytes(pdf.output())

@app.route('/download_report', methods=['GET'])
def download_report():
    pdf_bytes = create_pdf_bytes()
    
    return send_file(
        io.BytesIO(pdf_bytes),
        as_attachment=True,
        download_name='reporte_ceneval.pdf',
        mimetype='application/pdf'
    )

import smtplib
from email.message import EmailMessage

@app.route('/enviar_correo', methods=['POST'])
def enviar_correo():
    """Ruta para enviar los resultados por correo electrónico al usuario."""
    destinatario = session.get('user_email')
    nombre = session.get('user_name', '')
    
    if not destinatario:
        return jsonify({"error": "Correo no proporcionado en el registro"}), 400
        
    history = session.get('history', [])
    score = sum([1 for h in history if h['is_correct']])
    
    if session.get('perfect_score'):
        percentage = 100.0
        total = score
    else:
        total = 30
        percentage = (score / total) * 100 if total > 0 else 0
    
    # Configuracion del servidor de correo
    correo_remitente = "appceneval@gmail.com" 
    password_remitente = "ihvvqavmvnekoham"
    
    msg = EmailMessage()
    msg['Subject'] = 'Tu Reporte de Evaluación CENEVAL'
    msg['From'] = correo_remitente
    msg['To'] = f"{destinatario}, georgina_mondragon@my.uvm.edu.mx"
    
    mensaje_extra = ""
    if session.get('perfect_score'):
        mensaje_extra = "¡EXCELENTE! ¡Muchas felicidades! Has logrado el 100% perfecto al contestar correctamente desde la pregunta 1 hasta la 12 sin fallar. ¡Un dominio absoluto de los temas!\n"
    else:
        mensaje_extra = "Sigue practicando para mejorar tu dominio en los diferentes temas.\n"

    cuerpo_correo = f"""Hola {nombre},
    
Gracias por completar el Simulador CENEVAL Inteligente.

Tus Resultados:
Aciertos Totales: {score} / {total}
Calificación: {percentage:.1f}%

{mensaje_extra}
Adjunto encontrarás tu reporte detallado en formato PDF.

Saludos,
El Agente Tutor Inteligente
"""
    msg.set_content(cuerpo_correo)
    
    # Adjuntar PDF
    try:
        pdf_bytes = create_pdf_bytes()
        msg.add_attachment(pdf_bytes, maintype='application', subtype='pdf', filename='Reporte_CENEVAL.pdf')
    except Exception as e:
        print("Error generando PDF para adjuntar:", e)
    
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(correo_remitente, password_remitente)
        server.send_message(msg)
        server.quit()
        
        return jsonify({"mensaje": "Correo enviado correctamente"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # Abrir el navegador automáticamente después de un pequeño retraso
    threading.Timer(1.25, lambda: webbrowser.open('http://127.0.0.1:5000')).start()
    app.run(port=5000)
