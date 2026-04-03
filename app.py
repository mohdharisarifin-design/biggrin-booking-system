from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
import os
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dental-clinic-secret-key-change-this-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///dental_clinic.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file upload

db = SQLAlchemy(app)

# ========== DATABASE MODELS ==========

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'admin' or 'doctor'
    name = db.Column(db.String(100), nullable=False)

class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120), nullable=True)
    # Full info collected when patient arrives
    address = db.Column(db.Text, nullable=True)
    nric = db.Column(db.String(20), nullable=True)
    is_foreign = db.Column(db.Boolean, default=False)
    signature = db.Column(db.Text, nullable=True)  # Base64 encoded signature
    pdpa_consent = db.Column(db.Boolean, default=False)  # PDPA consent for data storage
    pdpa_consent_date = db.Column(db.DateTime, nullable=True)  # When consent was given
    registered_at = db.Column(db.DateTime, default=datetime.now)

class ReminderLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointment.id'), nullable=False)
    reminder_type = db.Column(db.String(20), nullable=False)  # 'email', 'sms'
    sent_at = db.Column(db.DateTime, default=datetime.now)
    status = db.Column(db.String(20), default='sent')  # 'sent', 'failed'
    message = db.Column(db.Text, nullable=True)
    
    appointment = db.relationship('Appointment', backref='reminders')

class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    appointment_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    appointment_type = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), default='scheduled')  # 'scheduled', 'completed', 'cancelled'
    notes = db.Column(db.Text, nullable=True)
    fee = db.Column(db.Integer, nullable=True)  # Fee in RM
    payment_status = db.Column(db.String(20), default='pending')  # 'pending', 'paid', 'refunded'
    payment_method = db.Column(db.String(50), nullable=True)  # 'cash', 'card', 'online'
    
    patient = db.relationship('Patient', backref='appointments')

class PatientFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False)
    file_type = db.Column(db.String(20), nullable=False)  # 'xray', 'document', 'photo', 'other'
    file_path = db.Column(db.String(500), nullable=False)
    file_size = db.Column(db.Integer, nullable=False)  # in bytes
    uploaded_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.now)
    description = db.Column(db.Text, nullable=True)
    
    patient = db.relationship('Patient', backref='files')
    uploader = db.relationship('User', backref='uploaded_files')

class PatientRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    record_type = db.Column(db.String(30), nullable=False)  # 'doctor_note', 'progress', 'treatment_plan', 'diagnosis'
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointment.id'), nullable=True)
    
    patient = db.relationship('Patient', backref='records')
    creator = db.relationship('User', backref='created_records')
    appointment = db.relationship('Appointment', backref='records')

class PatientRecordHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.Integer, db.ForeignKey('patient_record.id'), nullable=False)
    version_number = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    edited_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    edited_at = db.Column(db.DateTime, default=datetime.now)
    change_summary = db.Column(db.String(255), nullable=True)
    
    record = db.relationship('PatientRecord', backref='history')
    editor = db.relationship('User', backref='record_edits')

# ========== APPOINTMENT TYPES & DURATIONS ==========

APPOINTMENT_TYPES = {
    'scaling': {'name': 'Scaling & Cleaning', 'duration': 30, 'fee': 150},
    'consultation': {'name': 'Consultation Only', 'duration': 30, 'fee': 50},
    'extraction': {'name': 'Tooth Extraction', 'duration': 90, 'fee': 300},  # 1.5 hours
    'filling': {'name': 'Filling', 'duration': 60, 'fee': 200},
    'root_canal': {'name': 'Root Canal', 'duration': 120, 'fee': 800},
    'crown': {'name': 'Crown Procedure', 'duration': 120, 'fee': 1200},
    'other': {'name': 'Other Procedure', 'duration': 60, 'fee': 150}
}

CLINIC_HOURS = {
    0: None,           # Monday - Closed
    1: (9, 17),        # Tuesday - 9 AM to 5 PM
    2: (9, 17),        # Wednesday - 9 AM to 5 PM
    3: (9, 17),        # Thursday - 9 AM to 5 PM
    4: (9, 17),        # Friday - 9 AM to 5 PM
    5: (9, 13.5),      # Saturday - 9 AM to 1:30 PM
    6: (9, 13.5),      # Sunday - 9 AM to 1:30 PM
}

# ========== HELPER FUNCTIONS ==========

def init_db():
    with app.app_context():
        db.create_all()
        
        # Migrate: Add email column to Patient table if it doesn't exist
        try:
            from sqlalchemy import text
            db.session.execute(text("SELECT email FROM patient LIMIT 1"))
        except Exception:
            # Column doesn't exist, add it
            db.session.execute(text("ALTER TABLE patient ADD COLUMN email VARCHAR(120)"))
            db.session.commit()
            print("[DB MIGRATION] Added 'email' column to patient table")
        
        # Migrate: Add new columns to Appointment table
        try:
            from sqlalchemy import text
            db.session.execute(text("SELECT fee FROM appointment LIMIT 1"))
        except Exception:
            # fee column doesn't exist, add it
            db.session.execute(text("ALTER TABLE appointment ADD COLUMN fee INTEGER"))
            db.session.commit()
            print("[DB MIGRATION] Added 'fee' column to appointment table")
        
        try:
            from sqlalchemy import text
            db.session.execute(text("SELECT payment_status FROM appointment LIMIT 1"))
        except Exception:
            # payment_status column doesn't exist, add it
            db.session.execute(text("ALTER TABLE appointment ADD COLUMN payment_status VARCHAR(20) DEFAULT 'pending'"))
            db.session.commit()
            print("[DB MIGRATION] Added 'payment_status' column to appointment table")
        
        try:
            from sqlalchemy import text
            db.session.execute(text("SELECT payment_method FROM appointment LIMIT 1"))
        except Exception:
            # payment_method column doesn't exist, add it
            db.session.execute(text("ALTER TABLE appointment ADD COLUMN payment_method VARCHAR(50)"))
            db.session.commit()
            print("[DB MIGRATION] Added 'payment_method' column to appointment table")
        
        try:
            from sqlalchemy import text
            db.session.execute(text("SELECT pdpa_consent FROM patient LIMIT 1"))
        except Exception:
            # pdpa_consent column doesn't exist, add it
            db.session.execute(text("ALTER TABLE patient ADD COLUMN pdpa_consent BOOLEAN DEFAULT 0"))
            db.session.commit()
            print("[DB MIGRATION] Added 'pdpa_consent' column to patient table")
        
        try:
            from sqlalchemy import text
            db.session.execute(text("SELECT pdpa_consent_date FROM patient LIMIT 1"))
        except Exception:
            # pdpa_consent_date column doesn't exist, add it
            db.session.execute(text("ALTER TABLE patient ADD COLUMN pdpa_consent_date DATETIME"))
            db.session.commit()
            print("[DB MIGRATION] Added 'pdpa_consent_date' column to patient table")
        
        # Create default users if they don't exist
        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin',
                password_hash=generate_password_hash('admin123'),
                role='admin',
                name='Administrator'
            )
            db.session.add(admin)
        
        if not User.query.filter_by(username='doctor').first():
            doctor = User(
                username='doctor',
                password_hash=generate_password_hash('doctor123'),
                role='doctor',
                name='Dr. Dentist'
            )
            db.session.add(doctor)
        
        db.session.commit()
        
        # Create patient_folders directory if it doesn't exist
        patient_folders_path = os.path.join('static', 'patient_folders')
        if not os.path.exists(patient_folders_path):
            os.makedirs(patient_folders_path)
            print(f"[INIT] Created patient folders directory: {patient_folders_path}")

def get_time_slots(date, exclude_appointment_id=None):
    """Get available time slots for a given date."""
    # Check if clinic is closed on this day
    day_hours = CLINIC_HOURS.get(date.weekday())
    if day_hours is None:
        return []  # Closed on this day (Monday)
    
    start_hour, end_hour = day_hours
    
    # Get all appointments for this date
    query = Appointment.query.filter_by(appointment_date=date).filter(Appointment.status != 'cancelled')
    if exclude_appointment_id:
        query = query.filter(Appointment.id != exclude_appointment_id)
    
    appointments = query.all()
    
    # Create list of booked time ranges
    booked_slots = []
    for appt in appointments:
        start = datetime.combine(date, appt.start_time)
        end = datetime.combine(date, appt.end_time)
        booked_slots.append((start, end))
    
    # Generate all possible 30-min slots
    available_slots = []
    start_hour_int = int(start_hour)
    start_minute = int((start_hour - start_hour_int) * 60) if start_hour != start_hour_int else 0
    current = datetime.combine(date, datetime.min.time().replace(hour=start_hour_int, minute=start_minute))
    
    end_hour_int = int(end_hour)
    end_minute = int((end_hour - end_hour_int) * 60) if end_hour != end_hour_int else 0
    end_time = datetime.combine(date, datetime.min.time().replace(hour=end_hour_int, minute=end_minute))
    
    while current < end_time:
        slot_end = current + timedelta(minutes=30)
        
        # Check if this slot overlaps with any booked appointment
        is_available = True
        for booked_start, booked_end in booked_slots:
            if current < booked_end and slot_end > booked_start:
                is_available = False
                break
        
        if is_available:
            available_slots.append(current.time())
        
        current = slot_end
    
    return available_slots

def check_slot_available(date, start_time, duration_minutes, exclude_appointment_id=None):
    """Check if a time slot is available for given duration."""
    start_dt = datetime.combine(date, start_time)
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    
    query = Appointment.query.filter_by(appointment_date=date).filter(Appointment.status != 'cancelled')
    if exclude_appointment_id:
        query = query.filter(Appointment.id != exclude_appointment_id)
    
    appointments = query.all()
    
    for appt in appointments:
        appt_start = datetime.combine(date, appt.start_time)
        appt_end = datetime.combine(date, appt.end_time)
        
        # Check for overlap
        if start_dt < appt_end and end_dt > appt_start:
            return False
    
    return True

# ========== ROUTES ==========

@app.route('/')
def index():
    if 'user_id' in session:
        if session.get('role') == 'admin':
            return redirect(url_for('admin_dashboard'))
        else:
            return redirect(url_for('doctor_schedule'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            session['username'] = user.username
            session['role'] = user.role
            session['name'] = user.name
            flash(f'Welcome, {user.name}!', 'success')
            
            if user.role == 'admin':
                return redirect(url_for('admin_dashboard'))
            else:
                return redirect(url_for('doctor_schedule'))
        else:
            flash('Invalid username or password', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('login'))

# ========== ADMIN ROUTES ==========

@app.route('/admin')
def admin_dashboard():
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    today = datetime.now().date()
    
    # Get today's appointments
    today_appointments = Appointment.query.filter_by(
        appointment_date=today
    ).filter(Appointment.status != 'cancelled').order_by(Appointment.start_time).all()
    
    # Get upcoming appointments (next 7 days)
    week_end = today + timedelta(days=7)
    upcoming_count = Appointment.query.filter(
        Appointment.appointment_date > today,
        Appointment.appointment_date <= week_end,
        Appointment.status != 'cancelled'
    ).count()
    
    # Get total patients
    total_patients = Patient.query.count()
    
    # Get income for today
    today_income = db.session.query(db.func.sum(Appointment.fee)).filter(
        Appointment.appointment_date == today,
        Appointment.status != 'cancelled',
        Appointment.payment_status == 'paid'
    ).scalar() or 0
    
    # Get pending payments for today
    today_pending = db.session.query(db.func.sum(Appointment.fee)).filter(
        Appointment.appointment_date == today,
        Appointment.status != 'cancelled',
        Appointment.payment_status == 'pending'
    ).scalar() or 0
    
    # Get patient growth over time (last 6 months)
    from collections import defaultdict
    end_date = today
    start_date = end_date - timedelta(days=180)  # 6 months ago
    
    # Get patients registered in last 6 months
    recent_patients = Patient.query.filter(
        Patient.registered_at >= start_date
    ).order_by(Patient.registered_at).all()
    
    # Group by date
    daily_counts = defaultdict(int)
    for patient in recent_patients:
        date_key = patient.registered_at.strftime('%d %b')
        daily_counts[date_key] += 1
    
    # Prepare data for chart (cumulative)
    growth_dates = sorted(daily_counts.keys())
    growth_counts = []
    cumulative_count = 0
    for date in growth_dates:
        cumulative_count += daily_counts[date]
        growth_counts.append(cumulative_count)
    
    return render_template('admin_dashboard.html', 
                         today_appointments=today_appointments,
                         upcoming_count=upcoming_count,
                         total_patients=total_patients,
                         today=today,
                         appointment_types=APPOINTMENT_TYPES,
                         growth_dates=growth_dates,
                         growth_counts=growth_counts,
                         today_income=today_income,
                         today_pending=today_pending)

@app.route('/admin/book', methods=['GET', 'POST'])
def book_appointment():
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        patient_name = request.form['patient_name']
        phone = request.form['phone']
        email = request.form.get('email', '').strip()
        nric = request.form.get('nric', '').strip()
        is_foreign = request.form.get('is_foreign') == 'on'
        appointment_date = datetime.strptime(request.form['appointment_date'], '%Y-%m-%d').date()
        appointment_type = request.form['appointment_type']
        start_time_str = request.form['start_time']
        notes = request.form.get('notes', '')
        
        # Check if Monday (weekday() returns 0=Monday, 1=Tuesday, etc.)
        # Python's weekday(): Monday is 0
        if appointment_date.weekday() == 0:
            flash('Clinic is closed on Mondays. Please select another day.', 'error')
            available_slots = get_time_slots(appointment_date)
            return render_template('book_appointment.html', 
                                 appointment_types=APPOINTMENT_TYPES,
                                 available_slots=available_slots,
                                 selected_date=appointment_date,
                                 today=datetime.now(),
                                 form_data=request.form)
        
        # Parse start time
        start_time = datetime.strptime(start_time_str, '%H:%M').time()
        
        # Calculate end time based on appointment type
        duration = APPOINTMENT_TYPES[appointment_type]['duration']
        start_dt = datetime.combine(appointment_date, start_time)
        end_dt = start_dt + timedelta(minutes=duration)
        end_time = end_dt.time()
        
        # Check if slot is available
        if not check_slot_available(appointment_date, start_time, duration):
            flash('This time slot is no longer available. Please select another time.', 'error')
            available_slots = get_time_slots(appointment_date)
            return render_template('book_appointment.html', 
                                 appointment_types=APPOINTMENT_TYPES,
                                 available_slots=available_slots,
                                 selected_date=appointment_date,
                                 form_data=request.form)
        
        # Create or get patient
        patient_id = request.form.get('patient_id')
        if patient_id:
            # Use existing patient from autocomplete
            patient = Patient.query.get(patient_id)
            if not patient:
                flash('Selected patient not found', 'error')
                available_slots = get_time_slots(appointment_date)
                return render_template('book_appointment.html', 
                                     appointment_types=APPOINTMENT_TYPES,
                                     available_slots=available_slots,
                                     selected_date=appointment_date,
                                     today=datetime.now(),
                                     form_data=request.form)
        else:
            # Look up by phone or create new patient
            patient = Patient.query.filter_by(phone=phone).first()
            if not patient:
                patient = Patient(name=patient_name, phone=phone, email=email or None, nric=nric or None, is_foreign=is_foreign)
                db.session.add(patient)
                db.session.flush()
            else:
                # Update name, email and NRIC if provided
                patient.name = patient_name
                if email:
                    patient.email = email
                if nric:
                    patient.nric = nric
                patient.is_foreign = is_foreign
        
        # Create appointment with fee
        fee = APPOINTMENT_TYPES[appointment_type]['fee']
        appointment = Appointment(
            patient_id=patient.id,
            appointment_date=appointment_date,
            start_time=start_time,
            end_time=end_time,
            appointment_type=appointment_type,
            notes=notes,
            fee=fee
        )
        db.session.add(appointment)
        db.session.commit()
        
        flash(f'Appointment booked successfully for {patient_name} at {start_time_str}', 'success')
        return redirect(url_for('admin_dashboard'))
    
    # GET request - show booking form
    selected_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    selected_date = datetime.strptime(selected_date, '%Y-%m-%d').date()
    available_slots = get_time_slots(selected_date)
    
    return render_template('book_appointment.html',
                         appointment_types=APPOINTMENT_TYPES,
                         available_slots=available_slots,
                         selected_date=selected_date)

@app.route('/admin/appointments')
def view_appointments():
    if 'user_id' not in session or session.get('role') not in ['admin', 'doctor']:
        return redirect(url_for('login'))
    
    # Get date range from query params or default to today + 30 days
    start_date_str = request.args.get('start_date', datetime.now().strftime('%Y-%m-%d'))
    end_date_str = request.args.get('end_date', (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d'))
    
    start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
    
    appointments = Appointment.query.filter(
        Appointment.appointment_date >= start_date,
        Appointment.appointment_date <= end_date
    ).order_by(Appointment.appointment_date, Appointment.start_time).all()
    
    return render_template('view_appointments.html',
                         appointments=appointments,
                         start_date=start_date,
                         end_date=end_date,
                         appointment_types=APPOINTMENT_TYPES)

@app.route('/admin/income')
def income_report():
    """Income report page."""
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    # Get date range from query params or default to current month
    start_date_str = request.args.get('start_date', datetime.now().strftime('%Y-%m-%d'))
    end_date_str = request.args.get('end_date', datetime.now().strftime('%Y-%m-%d'))
    
    start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
    
    # Get appointments in date range
    appointments = Appointment.query.filter(
        Appointment.appointment_date >= start_date,
        Appointment.appointment_date <= end_date
    ).filter(Appointment.status != 'cancelled').all()
    
    # Calculate income stats
    total_expected = sum(appt.fee or APPOINTMENT_TYPES[appt.appointment_type]['fee'] for appt in appointments)
    total_collected = sum(appt.fee or 0 for appt in appointments if appt.payment_status == 'paid')
    total_pending = sum(appt.fee or 0 for appt in appointments if appt.payment_status == 'pending')
    
    return render_template('income_report.html',
                         appointments=appointments,
                         start_date=start_date,
                         end_date=end_date,
                         total_expected=total_expected,
                         total_collected=total_collected,
                         total_pending=total_pending,
                         appointment_types=APPOINTMENT_TYPES)

@app.route('/admin/appointment/<int:id>/payment', methods=['POST'])
def update_payment(id):
    """Update payment status for an appointment."""
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    appointment = Appointment.query.get_or_404(id)
    
    appointment.payment_status = request.form.get('payment_status', 'pending')
    appointment.payment_method = request.form.get('payment_method', None)
    db.session.commit()
    
    flash('Payment status updated', 'success')
    return redirect(request.referrer or url_for('income_report'))

@app.route('/financial-report')
def financial_report():
    """Comprehensive financial report for admin and doctor."""
    if 'user_id' not in session or session.get('role') not in ['admin', 'doctor']:
        return redirect(url_for('login'))
    
    today = datetime.now().date()
    
    # Get date range from query params
    view_type = request.args.get('view', 'daily')  # daily, weekly, monthly
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    
    if start_date_str and end_date_str:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
    else:
        # Default ranges based on view type
        if view_type == 'daily':
            start_date = today
            end_date = today
        elif view_type == 'weekly':
            start_date = today - timedelta(days=today.weekday())  # Start of week (Monday)
            end_date = start_date + timedelta(days=6)
        else:  # monthly
            start_date = today.replace(day=1)
            # Last day of month
            if today.month == 12:
                end_date = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                end_date = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
    
    # Get all appointments in date range (not cancelled)
    appointments = Appointment.query.filter(
        Appointment.appointment_date >= start_date,
        Appointment.appointment_date <= end_date
    ).filter(Appointment.status != 'cancelled').order_by(Appointment.appointment_date).all()
    
    # Calculate daily breakdown
    from collections import defaultdict
    daily_data = defaultdict(lambda: {'collected': 0, 'pending': 0, 'expected': 0, 'count': 0})
    
    for appt in appointments:
        date_key = appt.appointment_date.strftime('%Y-%m-%d')
        fee = appt.fee or APPOINTMENT_TYPES[appt.appointment_type]['fee']
        daily_data[date_key]['expected'] += fee
        daily_data[date_key]['count'] += 1
        if appt.payment_status == 'paid':
            daily_data[date_key]['collected'] += fee
        else:
            daily_data[date_key]['pending'] += fee
    
    # Calculate totals
    total_collected = sum(d['collected'] for d in daily_data.values())
    total_pending = sum(d['pending'] for d in daily_data.values())
    total_expected = sum(d['expected'] for d in daily_data.values())
    total_appointments = sum(d['count'] for d in daily_data.values())
    
    # Payment method breakdown
    payment_methods = defaultdict(int)
    for appt in appointments:
        if appt.payment_status == 'paid' and appt.payment_method:
            payment_methods[appt.payment_method] += appt.fee or APPOINTMENT_TYPES[appt.appointment_type]['fee']
    
    # Appointment type breakdown
    type_breakdown = defaultdict(lambda: {'count': 0, 'revenue': 0})
    for appt in appointments:
        fee = appt.fee or APPOINTMENT_TYPES[appt.appointment_type]['fee']
        type_breakdown[appt.appointment_type]['count'] += 1
        if appt.payment_status == 'paid':
            type_breakdown[appt.appointment_type]['revenue'] += fee
    
    # Handle CSV download
    if request.args.get('download') == 'csv':
        import csv
        import io
        from flask import Response
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Header
        writer.writerow(['Date', 'Patient', 'Type', 'Fee (RM)', 'Payment Status', 'Method'])
        
        # Data rows
        for appt in appointments:
            writer.writerow([
                appt.appointment_date.strftime('%Y-%m-%d'),
                appt.patient.name,
                APPOINTMENT_TYPES[appt.appointment_type]['name'],
                appt.fee or APPOINTMENT_TYPES[appt.appointment_type]['fee'],
                appt.payment_status,
                appt.payment_method or '-'
            ])
        
        # Summary rows
        writer.writerow([])
        writer.writerow(['Summary', '', '', '', '', ''])
        writer.writerow(['Total Collected', f'RM {total_collected}', '', '', '', ''])
        writer.writerow(['Total Pending', f'RM {total_pending}', '', '', '', ''])
        writer.writerow(['Total Expected', f'RM {total_expected}', '', '', '', ''])
        writer.writerow(['Total Appointments', total_appointments, '', '', '', ''])
        
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={
                'Content-Disposition': f'attachment; filename=financial_report_{start_date}_{end_date}.csv'
            }
        )
    
    return render_template('financial_report.html',
                         view_type=view_type,
                         start_date=start_date,
                         end_date=end_date,
                         daily_data=daily_data,
                         total_collected=total_collected,
                         total_pending=total_pending,
                         total_expected=total_expected,
                         total_appointments=total_appointments,
                         payment_methods=dict(payment_methods),
                         type_breakdown=dict(type_breakdown),
                         appointment_types=APPOINTMENT_TYPES)

@app.route('/admin/appointment/<int:id>/cancel', methods=['POST'])
def cancel_appointment(id):
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    appointment = Appointment.query.get_or_404(id)
    appointment.status = 'cancelled'
    db.session.commit()
    
    flash('Appointment cancelled successfully', 'success')
    return redirect(url_for('view_appointments'))

@app.route('/admin/appointment/<int:id>/reschedule', methods=['GET', 'POST'])
def reschedule_appointment(id):
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    appointment = Appointment.query.get_or_404(id)
    
    if request.method == 'POST':
        new_date = datetime.strptime(request.form['new_date'], '%Y-%m-%d').date()
        new_time_str = request.form['new_time']
        new_time = datetime.strptime(new_time_str, '%H:%M').time()
        
        # Check if Monday (weekday() returns 0=Monday)
        if new_date.weekday() == 0:
            flash('Clinic is closed on Mondays. Please select another day.', 'error')
            available_slots = get_time_slots(new_date, exclude_appointment_id=id)
            return render_template('reschedule_appointment.html',
                                 appointment=appointment,
                                 available_slots=available_slots,
                                 selected_date=new_date)
        
        duration = APPOINTMENT_TYPES[appointment.appointment_type]['duration']
        
        if not check_slot_available(new_date, new_time, duration, exclude_appointment_id=id):
            flash('This time slot is not available', 'error')
            available_slots = get_time_slots(new_date, exclude_appointment_id=id)
            return render_template('reschedule_appointment.html',
                                 appointment=appointment,
                                 available_slots=available_slots,
                                 selected_date=new_date)
        
        # Update appointment
        start_dt = datetime.combine(new_date, new_time)
        end_dt = start_dt + timedelta(minutes=duration)
        
        appointment.appointment_date = new_date
        appointment.start_time = new_time
        appointment.end_time = end_dt.time()
        appointment.status = 'scheduled'
        
        db.session.commit()
        flash('Appointment rescheduled successfully', 'success')
        return redirect(url_for('view_appointments'))
    
    # GET request - show reschedule form
    selected_date = request.args.get('date', appointment.appointment_date.strftime('%Y-%m-%d'))
    selected_date = datetime.strptime(selected_date, '%Y-%m-%d').date()
    available_slots = get_time_slots(selected_date, exclude_appointment_id=id)
    
    return render_template('reschedule_appointment.html',
                         appointment=appointment,
                         available_slots=available_slots,
                         selected_date=selected_date)

@app.route('/admin/patient/<int:id>')
def patient_detail(id):
    if 'user_id' not in session or session.get('role') not in ['admin', 'doctor']:
        return redirect(url_for('login'))
    
    patient = Patient.query.get_or_404(id)
    
    return render_template('patient_detail.html', 
                         patient=patient,
                         appointment_types=APPOINTMENT_TYPES)

@app.route('/admin/patient/<int:id>/register', methods=['GET', 'POST'])
def register_patient(id):
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    patient = Patient.query.get_or_404(id)
    
    if request.method == 'POST':
        patient.name = request.form['name']
        patient.phone = request.form['phone']
        patient.email = request.form.get('email', '').strip() or None
        patient.address = request.form['address']
        patient.nric = request.form['nric']
        patient.is_foreign = request.form.get('is_foreign') == 'on'
        patient.signature = request.form.get('signature_data', '')
        
        # Handle PDPA consent
        patient.pdpa_consent = request.form.get('pdpa_consent') == 'on'
        if patient.pdpa_consent and not patient.pdpa_consent_date:
            patient.pdpa_consent_date = datetime.now()
        
        db.session.commit()
        flash('Patient registration completed', 'success')
        return redirect(url_for('admin_dashboard'))
    
    return render_template('register_patient.html', patient=patient)

@app.route('/admin/patients')
def list_patients():
    if 'user_id' not in session or session.get('role') not in ['admin', 'doctor']:
        return redirect(url_for('login'))
    
    search = request.args.get('search', '')
    
    if search:
        patients = Patient.query.filter(
            db.or_(
                Patient.name.ilike(f'%{search}%'),
                Patient.phone.ilike(f'%{search}%')
            )
        ).order_by(Patient.registered_at.desc()).all()
    else:
        patients = Patient.query.order_by(Patient.registered_at.desc()).all()
    
    return render_template('list_patients.html', patients=patients, search=search)

@app.route('/admin/patients/add', methods=['GET', 'POST'])
def add_patient():
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        name = request.form['name']
        phone = request.form['phone']
        email = request.form.get('email', '').strip() or None
        nric = request.form.get('nric', '').strip()
        is_foreign = request.form.get('is_foreign') == 'on'
        address = request.form.get('address', '').strip()
        signature_data = request.form.get('signature_data', '').strip()
        
        pdpa_consent = request.form.get('pdpa_consent') == 'on'
        pdpa_consent_date = datetime.now() if pdpa_consent else None
        
        # Check if patient with this phone already exists
        existing = Patient.query.filter_by(phone=phone).first()
        if existing:
            flash(f'Patient with phone {phone} already exists: {existing.name}', 'error')
            return render_template('add_patient.html', form_data=request.form)
        
        # Create new patient
        patient = Patient(
            name=name,
            phone=phone,
            email=email,
            nric=nric or None,
            is_foreign=is_foreign,
            address=address or None,
            signature=signature_data or None,
            pdpa_consent=pdpa_consent,
            pdpa_consent_date=pdpa_consent_date
        )
        db.session.add(patient)
        db.session.commit()
        
        flash(f'Patient {name} added successfully', 'success')
        return redirect(url_for('list_patients'))
    
    return render_template('add_patient.html')

@app.route('/admin/patients/bulk-upload', methods=['GET', 'POST'])
def bulk_upload_patients():
    """Bulk upload patients from CSV file."""
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    results = None
    
    if request.method == 'POST':
        if 'csv_file' not in request.files:
            flash('No file selected', 'error')
            return redirect(request.url)
        
        file = request.files['csv_file']
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(request.url)
        
        if not file.filename.endswith('.csv'):
            flash('Please upload a CSV file', 'error')
            return redirect(request.url)
        
        import csv
        import io
        
        stream = io.StringIO(file.stream.read().decode("UTF-8"), newline=None)
        csv_reader = csv.DictReader(stream)
        
        success_count = 0
        errors = []
        row_num = 1  # Start at 1 to account for header
        
        for row in csv_reader:
            row_num += 1
            
            try:
                name = row.get('name', '').strip()
                phone = row.get('phone', '').strip()
                email = row.get('email', '').strip() or None
                nric = row.get('nric', '').strip() or None
                address = row.get('address', '').strip() or None
                is_foreign = row.get('is_foreign', '0').strip() in ['1', 'true', 'True', 'yes', 'Yes']
                pdpa_consent = row.get('pdpa_consent', '0').strip() in ['1', 'true', 'True', 'yes', 'Yes']
                
                # Validation
                if not name:
                    errors.append({'row': row_num, 'name': name or '(empty)', 'message': 'Name is required'})
                    continue
                
                if not phone:
                    errors.append({'row': row_num, 'name': name, 'message': 'Phone is required'})
                    continue
                
                # Check for duplicates
                existing = Patient.query.filter_by(phone=phone).first()
                if existing:
                    errors.append({'row': row_num, 'name': name, 'message': f'Phone {phone} already exists for patient: {existing.name}'})
                    continue
                
                # Create patient
                patient = Patient(
                    name=name,
                    phone=phone,
                    email=email,
                    nric=nric,
                    address=address,
                    is_foreign=is_foreign,
                    pdpa_consent=pdpa_consent,
                    pdpa_consent_date=datetime.now() if pdpa_consent else None
                )
                db.session.add(patient)
                success_count += 1
                
            except Exception as e:
                errors.append({'row': row_num, 'name': row.get('name', '(unknown)'), 'message': str(e)})
        
        # Commit all successful entries
        if success_count > 0:
            db.session.commit()
        
        results = {
            'total': row_num - 1,
            'success': success_count,
            'errors': errors
        }
        
        if success_count > 0:
            flash(f'Successfully added {success_count} patient(s)', 'success')
        if errors:
            flash(f'{len(errors)} row(s) had errors', 'error')
    
    return render_template('bulk_upload_patients.html', results=results)

@app.route('/admin/patients/download-template')
def download_csv_template():
    """Download CSV template for bulk patient upload."""
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    import csv
    import io
    from flask import Response
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow(['name', 'phone', 'email', 'nric', 'address', 'is_foreign', 'pdpa_consent'])
    
    # Sample rows
    writer.writerow(['John Doe', '0123456789', 'john@email.com', '900101011234', 'Kuala Lumpur', '0', '1'])
    writer.writerow(['Jane Smith', '0198765432', 'jane@email.com', 'A12345678', 'Petaling Jaya', '1', '1'])
    writer.writerow(['Ahmad Abdullah', '0176543210', '', '880505101234', 'Shah Alam', '0', '1'])
    
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={
            'Content-Disposition': 'attachment; filename=patient_upload_template.csv'
        }
    )

@app.route('/admin/patient/<int:id>/delete', methods=['POST'])
def delete_patient(id):
    """Delete a patient and their appointments."""
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    patient = Patient.query.get_or_404(id)
    patient_name = patient.name
    
    # Delete all appointments first (to avoid foreign key constraint)
    Appointment.query.filter_by(patient_id=id).delete()
    
    # Delete the patient
    db.session.delete(patient)
    db.session.commit()
    
    flash(f'Patient {patient_name} has been deleted', 'success')
    return redirect(url_for('list_patients'))

@app.route('/admin/settings')
def settings():
    """Admin settings page."""
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    user = User.query.get(session['user_id'])
    users = User.query.all()
    
    # Get reminder logs for display
    reminder_logs = ReminderLog.query.order_by(ReminderLog.sent_at.desc()).limit(10).all()
    
    return render_template('settings.html', user=user, users=users, 
                         reminder_config=REMINDER_CONFIG, reminder_logs=reminder_logs)

@app.route('/admin/change-password', methods=['POST'])
def change_password():
    """Handle password change."""
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    user = User.query.get(session['user_id'])
    
    current_password = request.form['current_password']
    new_password = request.form['new_password']
    confirm_password = request.form['confirm_password']
    
    # Verify current password
    if not check_password_hash(user.password_hash, current_password):
        flash('Current password is incorrect', 'error')
        return redirect(url_for('settings'))
    
    # Check new password confirmation
    if new_password != confirm_password:
        flash('New passwords do not match', 'error')
        return redirect(url_for('settings'))
    
    # Update password
    user.password_hash = generate_password_hash(new_password)
    db.session.commit()
    
    flash('Password updated successfully', 'success')
    return redirect(url_for('settings'))

@app.route('/admin/create-user', methods=['POST'])
def create_user():
    """Create a new user account (for locum staff, assistants)."""
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    username = request.form['username'].strip()
    name = request.form['name'].strip()
    password = request.form['password']
    role = request.form['role']
    
    # Validate role - only allow doctor or assistant (not admin)
    if role not in ['doctor', 'assistant']:
        flash('Invalid role selected', 'error')
        return redirect(url_for('settings'))
    
    # Check if username already exists
    existing = User.query.filter_by(username=username).first()
    if existing:
        flash(f'Username "{username}" already exists', 'error')
        return redirect(url_for('settings'))
    
    # Create new user
    new_user = User(
        username=username,
        name=name,
        role=role,
        password_hash=generate_password_hash(password)
    )
    db.session.add(new_user)
    db.session.commit()
    
    flash(f'User "{username}" created successfully as {role}', 'success')
    return redirect(url_for('settings'))

@app.route('/admin/edit-user/<int:id>', methods=['POST'])
def edit_user(id):
    """Edit an existing user account."""
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    user = User.query.get_or_404(id)
    
    username = request.form['username'].strip()
    name = request.form['name'].strip()
    role = request.form['role']
    new_password = request.form.get('password', '').strip()
    
    # Check if username is being changed and if new username already exists
    if username != user.username:
        existing = User.query.filter_by(username=username).first()
        if existing:
            flash(f'Username "{username}" already exists', 'error')
            return redirect(url_for('settings'))
    
    # Prevent changing admin role to something else
    if user.role == 'admin' and role != 'admin':
        flash('Cannot change admin role', 'error')
        return redirect(url_for('settings'))
    
    # Update user fields
    user.username = username
    user.name = name
    
    # Only allow role change for non-admin users
    if user.role != 'admin':
        if role in ['doctor', 'assistant']:
            user.role = role
    
    # Update password if provided
    if new_password:
        user.password_hash = generate_password_hash(new_password)
    
    db.session.commit()
    flash(f'User "{username}" updated successfully', 'success')
    return redirect(url_for('settings'))

@app.route('/admin/delete-user/<int:id>', methods=['POST'])
def delete_user(id):
    """Delete a user account (cannot delete admin)."""
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    user_to_delete = User.query.get_or_404(id)
    
    # Prevent deleting admin users or self
    if user_to_delete.role == 'admin':
        flash('Cannot delete admin accounts', 'error')
        return redirect(url_for('settings'))
    
    if user_to_delete.id == session['user_id']:
        flash('Cannot delete your own account', 'error')
        return redirect(url_for('settings'))
    
    username = user_to_delete.username
    db.session.delete(user_to_delete)
    db.session.commit()
    
    flash(f'User "{username}" has been deleted', 'success')
    return redirect(url_for('settings'))

# ========== DOCTOR ROUTES ==========

@app.route('/doctor/schedule')
def doctor_schedule():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # Get date from query param or use today
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    
    # Get appointments for selected date
    appointments = Appointment.query.filter_by(
        appointment_date=selected_date
    ).filter(Appointment.status != 'cancelled').order_by(Appointment.start_time).all()
    
    # Get previous and next day for navigation
    prev_date = selected_date - timedelta(days=1)
    next_date = selected_date + timedelta(days=1)
    
    return render_template('doctor_schedule.html',
                         appointments=appointments,
                         selected_date=selected_date,
                         prev_date=prev_date,
                         next_date=next_date,
                         appointment_types=APPOINTMENT_TYPES,
                         clinic_hours=CLINIC_HOURS)

@app.route('/api/slots')
def api_available_slots():
    """API endpoint to get available slots for a date (for AJAX)."""
    date_str = request.args.get('date')
    exclude_id = request.args.get('exclude_id', type=int)
    if not date_str:
        return {'error': 'Date required'}, 400
    
    date = datetime.strptime(date_str, '%Y-%m-%d').date()
    slots = get_time_slots(date, exclude_appointment_id=exclude_id)
    
    return {'slots': [s.strftime('%H:%M') for s in slots]}


@app.route('/api/appointment/<int:id>/reschedule', methods=['POST'])
def api_reschedule_appointment(id):
    """API endpoint to reschedule an appointment via AJAX."""
    if 'user_id' not in session or session.get('role') != 'admin':
        return {'error': 'Unauthorized'}, 403
    
    appointment = Appointment.query.get_or_404(id)
    
    data = request.get_json()
    new_date_str = data.get('new_date')
    new_time_str = data.get('new_time')
    
    if not new_date_str or not new_time_str:
        return {'error': 'Date and time required'}, 400
    
    new_date = datetime.strptime(new_date_str, '%Y-%m-%d').date()
    new_time = datetime.strptime(new_time_str, '%H:%M').time()
    
    # Check if Monday (weekday() returns 0=Monday)
    if new_date.weekday() == 0:
        return {'error': 'Clinic is closed on Mondays'}, 400
    
    duration = APPOINTMENT_TYPES[appointment.appointment_type]['duration']
    
    if not check_slot_available(new_date, new_time, duration, exclude_appointment_id=id):
        return {'error': 'This time slot is not available'}, 400
    
    # Update appointment
    start_dt = datetime.combine(new_date, new_time)
    end_dt = start_dt + timedelta(minutes=duration)
    
    appointment.appointment_date = new_date
    appointment.start_time = new_time
    appointment.end_time = end_dt.time()
    appointment.status = 'scheduled'
    
    db.session.commit()
    
    return {
        'success': True,
        'message': 'Appointment rescheduled successfully',
        'new_date': new_date.strftime('%A, %d %B %Y'),
        'new_time': new_time.strftime('%H:%M'),
        'end_time': end_dt.time().strftime('%H:%M')
    }

@app.route('/api/patients/search')
def api_search_patients():
    """API endpoint to search patients for autocomplete."""
    if 'user_id' not in session or session.get('role') != 'admin':
        return {'error': 'Unauthorized'}, 403
    
    query = request.args.get('q', '').strip()
    if not query or len(query) < 2:
        return {'patients': []}
    
    patients = Patient.query.filter(
        db.or_(
            Patient.name.ilike(f'%{query}%'),
            Patient.phone.ilike(f'%{query}%')
        )
    ).limit(10).all()
    
    return {
        'patients': [
            {
                'id': p.id,
                'name': p.name,
                'phone': p.phone,
                'email': p.email or '',
                'nric': p.nric or '',
                'is_foreign': p.is_foreign
            }
            for p in patients
        ]
    }

@app.route('/api/patient/<int:id>')
def api_get_patient(id):
    """API endpoint to get a single patient's details."""
    if 'user_id' not in session or session.get('role') != 'admin':
        return {'error': 'Unauthorized'}, 403
    
    patient = Patient.query.get_or_404(id)
    return {
        'id': patient.id,
        'name': patient.name,
        'phone': patient.phone,
        'email': patient.email or '',
        'nric': patient.nric or '',
        'is_foreign': patient.is_foreign,
        'address': patient.address or ''
    }

# ========== REMINDER FUNCTIONS ==========

REMINDER_CONFIG = {
    'enabled': True,
    'hours_before': 24,  # Send reminder 24 hours before appointment
    'clinic_name': 'Big Grin Dental Clinic',
    'clinic_phone': '+60 3-XXXX XXXX',
    'clinic_address': 'Your Clinic Address Here',
    # SMTP settings for real email sending
    'smtp_server': '',  # e.g., smtp.gmail.com
    'smtp_port': 587,
    'smtp_username': '',  # your email address
    'smtp_password': '',  # your email password or app password
    'smtp_from_email': '',  # sender email address
    'test_mode': True,  # Set to False to send real emails
    # WhatsApp settings (using CallMeBot free API or Twilio)
    'whatsapp_enabled': False,
    'whatsapp_api': 'callmebot',  # Options: 'callmebot' (free), 'twilio' (paid)
    'whatsapp_api_key': '',  # For CallMeBot
    'whatsapp_phone': '',  # Your WhatsApp number with country code
    'twilio_sid': '',  # For Twilio
    'twilio_token': '',  # For Twilio
    'twilio_whatsapp_number': '',  # Twilio WhatsApp sender number
}

def check_and_send_reminders():
    """Check for upcoming appointments and send reminders (email and/or WhatsApp)."""
    if not REMINDER_CONFIG['enabled']:
        return
    
    # Calculate the reminder window
    reminder_time = datetime.now() + timedelta(hours=REMINDER_CONFIG['hours_before'])
    window_start = reminder_time - timedelta(minutes=30)
    window_end = reminder_time + timedelta(minutes=30)
    
    # Find appointments in the reminder window that haven't been reminded yet
    upcoming_appointments = Appointment.query.filter(
        Appointment.appointment_date == reminder_time.date(),
        Appointment.status == 'scheduled'
    ).all()
    
    sent_count = 0
    for appointment in upcoming_appointments:
        patient = appointment.patient
        
        # Send Email Reminder
        if patient.email:
            existing_email = ReminderLog.query.filter_by(
                appointment_id=appointment.id,
                reminder_type='email'
            ).first()
            
            if not existing_email:
                success = send_email_reminder(patient, appointment)
                reminder = ReminderLog(
                    appointment_id=appointment.id,
                    reminder_type='email',
                    status='sent' if success else 'failed',
                    message=f"Email reminder sent to {patient.email}"
                )
                db.session.add(reminder)
                db.session.commit()
                if success:
                    sent_count += 1
        
        # Send WhatsApp Reminder
        if REMINDER_CONFIG.get('whatsapp_enabled') and patient.phone:
            existing_whatsapp = ReminderLog.query.filter_by(
                appointment_id=appointment.id,
                reminder_type='whatsapp'
            ).first()
            
            if not existing_whatsapp:
                success = send_whatsapp_reminder(patient, appointment)
                reminder = ReminderLog(
                    appointment_id=appointment.id,
                    reminder_type='whatsapp',
                    status='sent' if success else 'failed',
                    message=f"WhatsApp reminder sent to {patient.phone}"
                )
                db.session.add(reminder)
                db.session.commit()
                if success:
                    sent_count += 1
    
    return sent_count

def send_email_reminder(patient, appointment):
    """Send an email reminder to a patient via SMTP."""
    appt_type = APPOINTMENT_TYPES.get(appointment.appointment_type, {}).get('name', appointment.appointment_type)
    
    # Build the email message
    subject = f"Appointment Reminder - {REMINDER_CONFIG['clinic_name']}"
    body = f"""Dear {patient.name},

This is a reminder of your upcoming appointment at {REMINDER_CONFIG['clinic_name']}.

Appointment Details:
Date: {appointment.appointment_date.strftime('%A, %d %B %Y')}
Time: {appointment.start_time.strftime('%H:%M')}
Type: {appt_type}

If you need to reschedule or cancel, please contact us at {REMINDER_CONFIG['clinic_phone']}.

Best regards,
{REMINDER_CONFIG['clinic_name']}
{REMINDER_CONFIG['clinic_address']}"""
    
    # Check if SMTP is configured and not in test mode
    smtp_server = REMINDER_CONFIG.get('smtp_server', '')
    smtp_username = REMINDER_CONFIG.get('smtp_username', '')
    smtp_password = REMINDER_CONFIG.get('smtp_password', '')
    from_email = REMINDER_CONFIG.get('smtp_from_email', '') or smtp_username
    
    if REMINDER_CONFIG.get('test_mode', True) or not smtp_server or not smtp_username:
        # Test mode - just log to console
        print(f"[TEST EMAIL - NOT SENT]")
        print(f"To: {patient.email}")
        print(f"Subject: {subject}")
        print(f"Body:\n{body}")
        print("\n[To send real emails, configure SMTP settings in Settings and set test_mode to False]")
        return True
    
    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = from_email
        msg['To'] = patient.email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        
        # Connect to SMTP server and send
        server = smtplib.SMTP(smtp_server, REMINDER_CONFIG.get('smtp_port', 587))
        server.starttls()  # Enable TLS
        server.login(smtp_username, smtp_password)
        server.send_message(msg)
        server.quit()
        
        print(f"[EMAIL SENT] To: {patient.email}")
        return True
        
    except Exception as e:
        print(f"[EMAIL FAILED] To: {patient.email}, Error: {str(e)}")
        return False

def send_whatsapp_reminder(patient, appointment):
    """Send a WhatsApp reminder using CallMeBot or Twilio API."""
    appt_type = APPOINTMENT_TYPES.get(appointment.appointment_type, {}).get('name', appointment.appointment_type)
    
    # Format phone number (remove any non-digit characters)
    phone = ''.join(c for c in patient.phone if c.isdigit())
    # Add country code if missing (assume Malaysia +60 if starts with 0)
    if phone.startswith('0'):
        phone = '60' + phone[1:]
    elif not phone.startswith('60'):
        phone = '60' + phone
    
    # Build the WhatsApp message
    message = f"""*{REMINDER_CONFIG['clinic_name']} - Appointment Reminder*

Dear {patient.name},

This is a reminder of your upcoming appointment:

📅 *Date:* {appointment.appointment_date.strftime('%A, %d %B %Y')}
🕐 *Time:* {appointment.start_time.strftime('%H:%M')}
🦷 *Type:* {appt_type}

If you need to reschedule or cancel, please contact us at {REMINDER_CONFIG['clinic_phone']}.

_{REMINDER_CONFIG['clinic_name']}_
{REMINDER_CONFIG['clinic_address']}"""
    
    # Check if in test mode or API not configured
    if REMINDER_CONFIG.get('test_mode', True):
        print(f"[TEST WHATSAPP - NOT SENT]")
        print(f"To: +{phone}")
        print(f"Message:\n{message}")
        print("\n[To send real WhatsApp messages, configure CallMeBot or Twilio in Settings and set test_mode to False]")
        return True
    
    api_type = REMINDER_CONFIG.get('whatsapp_api', 'callmebot')
    
    try:
        if api_type == 'callmebot':
            # Using CallMeBot free WhatsApp API
            api_key = REMINDER_CONFIG.get('whatsapp_api_key', '')
            if not api_key:
                print(f"[WHATSAPP FAILED] CallMeBot API key not configured")
                return False
            
            url = f"https://api.callmebot.com/whatsapp.php"
            params = {
                'phone': phone,
                'text': message,
                'apikey': api_key
            }
            
            response = requests.get(url, params=params, timeout=30)
            if response.status_code == 200 and 'success' in response.text.lower():
                print(f"[WHATSAPP SENT via CallMeBot] To: +{phone}")
                return True
            else:
                print(f"[WHATSAPP FAILED] CallMeBot: {response.text}")
                return False
                
        elif api_type == 'twilio':
            # Using Twilio WhatsApp API
            account_sid = REMINDER_CONFIG.get('twilio_sid', '')
            auth_token = REMINDER_CONFIG.get('twilio_token', '')
            from_number = REMINDER_CONFIG.get('twilio_whatsapp_number', '')
            
            if not account_sid or not auth_token:
                print(f"[WHATSAPP FAILED] Twilio credentials not configured")
                return False
            
            url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
            data = {
                'From': f"whatsapp:{from_number}" if from_number else "whatsapp:+14155238886",  # Twilio sandbox number
                'To': f"whatsapp:+{phone}",
                'Body': message
            }
            
            response = requests.post(url, data=data, auth=(account_sid, auth_token), timeout=30)
            if response.status_code == 201:
                print(f"[WHATSAPP SENT via Twilio] To: +{phone}")
                return True
            else:
                print(f"[WHATSAPP FAILED] Twilio: {response.text}")
                return False
        else:
            print(f"[WHATSAPP FAILED] Unknown API type: {api_type}")
            return False
            
    except Exception as e:
        print(f"[WHATSAPP FAILED] To: +{phone}, Error: {str(e)}")
        return False

@app.route('/admin/send-reminders', methods=['POST'])
def manual_send_reminders():
    """Manually trigger reminder check and send."""
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    sent_count = check_and_send_reminders()
    
    if sent_count is None:
        flash('Reminders are currently disabled', 'info')
    elif sent_count == 0:
        flash('No reminders needed to be sent at this time', 'info')
    else:
        flash(f'Sent {sent_count} reminder(s) successfully', 'success')
    
    return redirect(url_for('settings'))

@app.route('/admin/update-reminder-config', methods=['POST'])
def update_reminder_config():
    """Update reminder configuration settings."""
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    REMINDER_CONFIG['enabled'] = request.form.get('enabled') == 'on'
    REMINDER_CONFIG['hours_before'] = int(request.form.get('hours_before', 24))
    REMINDER_CONFIG['clinic_phone'] = request.form.get('clinic_phone', '').strip()
    REMINDER_CONFIG['clinic_address'] = request.form.get('clinic_address', '').strip()
    
    # SMTP settings
    REMINDER_CONFIG['smtp_server'] = request.form.get('smtp_server', '').strip()
    REMINDER_CONFIG['smtp_port'] = int(request.form.get('smtp_port', 587))
    REMINDER_CONFIG['smtp_username'] = request.form.get('smtp_username', '').strip()
    REMINDER_CONFIG['smtp_password'] = request.form.get('smtp_password', '').strip()
    REMINDER_CONFIG['smtp_from_email'] = request.form.get('smtp_from_email', '').strip()
    REMINDER_CONFIG['test_mode'] = request.form.get('test_mode') == 'on'
    
    # WhatsApp settings
    REMINDER_CONFIG['whatsapp_enabled'] = request.form.get('whatsapp_enabled') == 'on'
    REMINDER_CONFIG['whatsapp_api'] = request.form.get('whatsapp_api', 'callmebot')
    REMINDER_CONFIG['whatsapp_api_key'] = request.form.get('whatsapp_api_key', '').strip()
    REMINDER_CONFIG['whatsapp_phone'] = request.form.get('whatsapp_phone', '').strip()
    REMINDER_CONFIG['twilio_sid'] = request.form.get('twilio_sid', '').strip()
    REMINDER_CONFIG['twilio_token'] = request.form.get('twilio_token', '').strip()
    REMINDER_CONFIG['twilio_whatsapp_number'] = request.form.get('twilio_whatsapp_number', '').strip()
    
    flash('Reminder settings updated successfully', 'success')
    return redirect(url_for('settings'))

# ========== PATIENT FOLDER ROUTES ==========

import uuid
from werkzeug.utils import secure_filename
from flask import send_from_directory

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf', 'doc', 'docx', 'dcm'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_patient_folder_path(patient_id):
    """Get or create patient folder path."""
    base_path = os.path.join('static', 'patient_folders', str(patient_id))
    if not os.path.exists(base_path):
        os.makedirs(base_path)
        # Create subdirectories
        for subdir in ['xrays', 'documents', 'photos', 'other']:
            os.makedirs(os.path.join(base_path, subdir), exist_ok=True)
    return base_path

@app.route('/patient/<int:id>/files')
def patient_files(id):
    """View all files for a patient."""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    patient = Patient.query.get_or_404(id)
    files = PatientFile.query.filter_by(patient_id=id).order_by(PatientFile.uploaded_at.desc()).all()
    
    # Group files by type
    files_by_type = {'xray': [], 'document': [], 'photo': [], 'other': []}
    for file in files:
        files_by_type[file.file_type].append(file)
    
    return render_template('patient_files.html', 
                         patient=patient, 
                         files_by_type=files_by_type,
                         files=files)

@app.route('/patient/<int:id>/upload', methods=['POST'])
def upload_file(id):
    """Upload file to patient folder."""
    if 'user_id' not in session or session.get('role') not in ['admin', 'doctor']:
        return redirect(url_for('login'))
    
    patient = Patient.query.get_or_404(id)
    
    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('patient_files', id=id))
    
    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('patient_files', id=id))
    
    if not allowed_file(file.filename):
        flash('File type not allowed. Allowed: PNG, JPG, PDF, DOC, DOCX, DCM', 'error')
        return redirect(url_for('patient_files', id=id))
    
    # Get file type from form
    file_type = request.form.get('file_type', 'other')
    description = request.form.get('description', '')
    
    # Generate unique filename
    original_filename = secure_filename(file.filename)
    file_ext = original_filename.rsplit('.', 1)[1].lower()
    stored_filename = f"{uuid.uuid4()}.{file_ext}"
    
    # Get patient folder path
    patient_folder = get_patient_folder_path(id)
    
    # Determine subfolder based on file type
    subfolder = file_type if file_type in ['xrays', 'documents', 'photos'] else 'other'
    upload_path = os.path.join(patient_folder, subfolder)
    
    # Save file
    file_path = os.path.join(upload_path, stored_filename)
    file.save(file_path)
    
    # Save to database
    patient_file = PatientFile(
        patient_id=id,
        filename=original_filename,
        stored_filename=stored_filename,
        file_type=file_type,
        file_path=os.path.join('static', 'patient_folders', str(id), subfolder, stored_filename),
        file_size=os.path.getsize(file_path),
        uploaded_by=session['user_id'],
        description=description
    )
    db.session.add(patient_file)
    db.session.commit()
    
    flash(f'File "{original_filename}" uploaded successfully', 'success')
    return redirect(url_for('patient_files', id=id))

@app.route('/patient/file/<int:file_id>/download')
def download_file(file_id):
    """Download a patient file."""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    patient_file = PatientFile.query.get_or_404(file_id)
    patient_folder = get_patient_folder_path(patient_file.patient_id)
    
    # Determine subfolder
    subfolder = patient_file.file_type if patient_file.file_type in ['xrays', 'documents', 'photos'] else 'other'
    file_path = os.path.join(patient_folder, subfolder, patient_file.stored_filename)
    
    return send_from_directory(os.path.dirname(file_path), 
                              patient_file.stored_filename,
                              as_attachment=True,
                              download_name=patient_file.filename)

@app.route('/patient/file/<int:file_id>/delete', methods=['POST'])
def delete_file(file_id):
    """Delete a patient file."""
    if 'user_id' not in session or session.get('role') not in ['admin', 'doctor']:
        return redirect(url_for('login'))
    
    patient_file = PatientFile.query.get_or_404(file_id)
    patient_id = patient_file.patient_id
    
    # Delete physical file
    patient_folder = get_patient_folder_path(patient_id)
    subfolder = patient_file.file_type if patient_file.file_type in ['xrays', 'documents', 'photos'] else 'other'
    file_path = os.path.join(patient_folder, subfolder, patient_file.stored_filename)
    
    if os.path.exists(file_path):
        os.remove(file_path)
    
    # Delete database record
    db.session.delete(patient_file)
    db.session.commit()
    
    flash('File deleted successfully', 'success')
    return redirect(url_for('patient_files', id=patient_id))

@app.route('/patient/<int:id>/records')
def patient_records(id):
    """View all medical records for a patient."""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    patient = Patient.query.get_or_404(id)
    records = PatientRecord.query.filter_by(patient_id=id).order_by(PatientRecord.created_at.desc()).all()
    
    return render_template('patient_records.html', patient=patient, records=records)

@app.route('/patient/<int:id>/record', methods=['POST'])
def add_record(id):
    """Add a medical record for a patient."""
    if 'user_id' not in session or session.get('role') not in ['admin', 'doctor']:
        return redirect(url_for('login'))
    
    patient = Patient.query.get_or_404(id)
    
    record_type = request.form.get('record_type', 'doctor_note')
    title = request.form.get('title', '').strip()
    content = request.form.get('content', '').strip()
    appointment_id = request.form.get('appointment_id') or None
    
    if not title or not content:
        flash('Title and content are required', 'error')
        return redirect(url_for('patient_records', id=id))
    
    record = PatientRecord(
        patient_id=id,
        record_type=record_type,
        title=title,
        content=content,
        created_by=session['user_id'],
        appointment_id=appointment_id
    )
    db.session.add(record)
    db.session.commit()
    
    flash('Medical record added successfully', 'success')
    return redirect(url_for('patient_records', id=id))

@app.route('/patient/record/<int:record_id>/edit', methods=['POST'])
def edit_record(record_id):
    """Edit a medical record with version history."""
    if 'user_id' not in session or session.get('role') not in ['admin', 'doctor']:
        return redirect(url_for('login'))
    
    record = PatientRecord.query.get_or_404(record_id)
    
    new_title = request.form.get('title', '').strip()
    new_content = request.form.get('content', '').strip()
    change_summary = request.form.get('change_summary', 'Updated record').strip()
    
    if not new_title or not new_content:
        flash('Title and content are required', 'error')
        return redirect(url_for('patient_records', id=record.patient_id))
    
    # Save current version to history
    version_count = PatientRecordHistory.query.filter_by(record_id=record_id).count()
    history_entry = PatientRecordHistory(
        record_id=record_id,
        version_number=version_count + 1,
        title=record.title,
        content=record.content,
        edited_by=session['user_id'],
        change_summary=change_summary
    )
    db.session.add(history_entry)
    
    # Update record
    record.title = new_title
    record.content = new_content
    record.updated_at = datetime.now()
    
    db.session.commit()
    
    flash('Medical record updated successfully', 'success')
    return redirect(url_for('patient_records', id=record.patient_id))

@app.route('/patient/record/<int:record_id>/delete', methods=['POST'])
def delete_record(record_id):
    """Delete a medical record."""
    if 'user_id' not in session or session.get('role') not in ['admin', 'doctor']:
        return redirect(url_for('login'))
    
    record = PatientRecord.query.get_or_404(record_id)
    patient_id = record.patient_id
    
    # Delete history entries first
    PatientRecordHistory.query.filter_by(record_id=record_id).delete()
    
    # Delete record
    db.session.delete(record)
    db.session.commit()
    
    flash('Medical record deleted successfully', 'success')
    return redirect(url_for('patient_records', id=patient_id))

@app.route('/patient/record/<int:record_id>/history')
def view_record_history(record_id):
    """View version history of a medical record."""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    record = PatientRecord.query.get_or_404(record_id)
    history = PatientRecordHistory.query.filter_by(record_id=record_id).order_by(PatientRecordHistory.version_number.desc()).all()
    
    return render_template('record_history.html', record=record, history=history)

# ========== MAIN ==========

if __name__ == '__main__':
    init_db()
    # Run on all interfaces so doctor can access from phone on same network
    app.run(host='0.0.0.0', port=5000, debug=True)
