# BigGrin Booking System

A Flask-based dental clinic appointment booking and management system with patient registration, scheduling, and financial reporting features.

## Features

- **User Authentication**: Admin and Doctor roles with secure login
- **Patient Management**: Register patients with PDPA consent and digital signatures
- **Appointment Booking**: Schedule appointments with automatic time slot management
- **Clinic Hours**: Configurable operating hours (closed Mondays)
- **Financial Reports**: Track income, pending payments, and export CSV reports
- **Appointment Types**: Scaling, Consultation, Extraction, Filling, Root Canal, Crown, and more

## Prerequisites

- Python 3.8 or higher
- pip (Python package manager)

## Setup Instructions

### 1. Clone the Repository

```bash
git clone <repository-url>
cd biggrin-booking-system
```

### 2. Create a Virtual Environment (Recommended)

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**macOS/Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the Application

```bash
python app.py
```

The application will start on `http://localhost:5000`

### 5. Access the Application

Open your browser and navigate to: `http://localhost:5000`

## Default Login Credentials

| Role     | Username | Password  |
|----------|----------|-----------|
| Admin    | admin    | admin123  |
| Doctor   | doctor   | doctor123 |

## Project Structure

```
biggrin-booking-system/
├── app.py                 # Main application file
├── requirements.txt       # Python dependencies
├── README.md             # This file
├── static/               # Static assets (CSS, JS, images)
│   └── logo.png
└── templates/            # HTML templates
    ├── base.html
    ├── login.html
    ├── admin_dashboard.html
    ├── book_appointment.html
    └── ...
```

## Database

The application uses SQLite as the database, which is automatically created as `dental_clinic.db` in the project root when you first run the application. The database schema is automatically initialized with default users.

## Configuration

Key settings in `app.py`:

- **Database**: SQLite (`sqlite:///dental_clinic.db`)
- **Secret Key**: Change `SECRET_KEY` in production
- **Clinic Hours**: Modify `CLINIC_HOURS` dictionary in `app.py`
- **Appointment Types**: Configure fees and durations in `APPOINTMENT_TYPES`

## Production Deployment

Before deploying to production:

1. Change the `SECRET_KEY` to a secure random string
2. Use a production-grade database (PostgreSQL/MySQL) instead of SQLite
3. Set `debug=False` in production
4. Use environment variables for sensitive configuration
5. Configure a proper web server (Gunicorn, uWSGI) behind Nginx

## License

This project is for educational and demonstration purposes.