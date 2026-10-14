from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, abort
import sqlite3
import hashlib
import json
import os
from datetime import datetime
from io import BytesIO
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
import qrcode
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "local-development-key-change-before-deploy")
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

DB = os.path.join(BASE_DIR, "certificates.db")
CHAIN_FILE = os.path.join(BASE_DIR, "blockchain.json")


def connect():
    return sqlite3.connect(DB)


def init_db():
    with connect() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS certificates(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_name TEXT NOT NULL,
                course TEXT NOT NULL,
                certificate_hash TEXT NOT NULL UNIQUE,
                issue_date TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL
            )
        """)


def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login first.")
            return redirect(url_for("login"))
        return function(*args, **kwargs)
    return wrapper


def load_chain():
    if os.path.exists(CHAIN_FILE):
        with open(CHAIN_FILE, "r") as f:
            return json.load(f)

    return [{
        "index": 0,
        "timestamp": str(datetime.now()),
        "data": "Genesis Block",
        "previous_hash": "0",
        "hash": hashlib.sha256(b"Genesis Block").hexdigest()
    }]


def save_chain(chain):
    with open(CHAIN_FILE, "w") as f:
        json.dump(chain, f, indent=4)


def add_block(data):
    chain = load_chain()
    previous = chain[-1]
    block_data = json.dumps(data, sort_keys=True)

    block_hash = hashlib.sha256(
        (block_data + previous["hash"]).encode()
    ).hexdigest()

    chain.append({
        "index": len(chain),
        "timestamp": str(datetime.now()),
        "data": data,
        "previous_hash": previous["hash"],
        "hash": block_hash
    })

    save_chain(chain)


@app.route("/")
def home():
    return render_template("home.html")


# REGISTER
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            flash("Please fill all fields.")
            return redirect(url_for("register"))

        if len(password) < 8:
            flash("Password must contain at least 8 characters.")
            return redirect(url_for("register"))

        try:
            with connect() as con:
                con.execute(
                    """INSERT INTO users(full_name, email, password)
                    VALUES(?,?,?)""",
                    (name, email, generate_password_hash(password))
                )

            flash("Registration successful. Please login.")
            return redirect(url_for("login"))

        except sqlite3.IntegrityError:
            flash("This email is already registered.")

    return render_template("register.html")


# LOGIN
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        with connect() as con:
            user = con.execute(
                """SELECT id, full_name, password
                FROM users WHERE email=?""",
                (email,)
            ).fetchone()

        if user and check_password_hash(user[2], password):
            session.clear()
            session["user_id"] = user[0]
            session["user_name"] = user[1]
            return redirect(url_for("home"))

        flash("Invalid email or password.")

    return render_template("login.html")


# LOGOUT
@app.route("/logout")
def logout():
    session.clear()
    flash("You have logged out.")
    return redirect(url_for("home"))


# QR SCANNER
@app.route("/scan")
def scan():
    return render_template("scan.html")


# ISSUE CERTIFICATE
@app.route("/issue", methods=["GET", "POST"])
@login_required
def issue():
    if request.method == "POST":
        name = request.form.get("student_name", "").strip()
        course = request.form.get("course", "").strip()

        if not name or not course:
            flash("Please fill all fields.")
            return redirect(url_for("issue"))

        issue_date = str(datetime.now())
        raw = f"{name}|{course}|{issue_date}"
        cert_hash = hashlib.sha256(raw.encode()).hexdigest()

        try:
            with connect() as con:
                con.execute(
                    """INSERT INTO certificates
                    (student_name, course, certificate_hash, issue_date)
                    VALUES(?,?,?,?)""",
                    (name, course, cert_hash, issue_date)
                )

            add_block({
                "student_name": name,
                "course": course,
                "certificate_hash": cert_hash,
                "issue_date": issue_date
            })

            return render_template(
                "result.html",
                name=name,
                course=course,
                cert_hash=cert_hash,
                issue_date=issue_date
            )

        except sqlite3.IntegrityError:
            flash("Certificate already exists.")

    return render_template("issue.html")


# DOWNLOAD CERTIFICATE AS PDF
@app.route("/certificate/<cert_hash>/pdf")
@login_required
def certificate_pdf(cert_hash):
    with connect() as con:
        row = con.execute(
            """SELECT student_name, course, certificate_hash, issue_date
            FROM certificates WHERE certificate_hash=?""",
            (cert_hash,)
        ).fetchone()

    if not row:
        abort(404)

    name, course, saved_hash, issue_date = row
    verify_url = request.url_root.rstrip("/") + url_for("verify", hash=saved_hash)

    qr_img = qrcode.make(verify_url)
    qr_buffer = BytesIO()
    qr_img.save(qr_buffer, format="PNG")
    qr_buffer.seek(0)

    output = BytesIO()
    page = landscape(A4)
    pdf = canvas.Canvas(output, pagesize=page)
    width, height = page

    # Elegant certificate border
    pdf.setFillColor(colors.HexColor("#F8FAFC"))
    pdf.rect(0, 0, width, height, fill=1, stroke=0)
    pdf.setStrokeColor(colors.HexColor("#0F766E"))
    pdf.setLineWidth(4)
    pdf.rect(12*mm, 12*mm, width-24*mm, height-24*mm, fill=0, stroke=1)
    pdf.setStrokeColor(colors.HexColor("#D4AF37"))
    pdf.setLineWidth(1.2)
    pdf.rect(16*mm, 16*mm, width-32*mm, height-32*mm, fill=0, stroke=1)

    pdf.setFillColor(colors.HexColor("#0F766E"))
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawCentredString(width/2, height-35*mm, "SECURE DIGITAL CERTIFICATE VERIFICATION")
    pdf.setFillColor(colors.HexColor("#1E293B"))
    pdf.setFont("Helvetica-Bold", 27)
    pdf.drawCentredString(width/2, height-53*mm, "CERTIFICATE OF COMPLETION")
    pdf.setFont("Helvetica", 12)
    pdf.setFillColor(colors.HexColor("#475569"))
    pdf.drawCentredString(width/2, height-68*mm, "This demo certificate is presented to")
    pdf.setFillColor(colors.HexColor("#0F172A"))
    pdf.setFont("Helvetica-Bold", 24)
    pdf.drawCentredString(width/2, height-84*mm, name)
    pdf.setFont("Helvetica", 13)
    pdf.setFillColor(colors.HexColor("#475569"))
    pdf.drawCentredString(width/2, height-96*mm, "for the course")
    pdf.setFillColor(colors.HexColor("#0F766E"))
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawCentredString(width/2, height-108*mm, course)

    pdf.setFillColor(colors.HexColor("#334155"))
    pdf.setFont("Helvetica", 10)
    pdf.drawString(27*mm, 37*mm, "Certificate ID: " + saved_hash[:12].upper())
    pdf.drawString(27*mm, 29*mm, "Issue date: " + issue_date[:10])
    pdf.drawString(27*mm, 21*mm, "SHA-256: " + saved_hash[:40] + "...")
    pdf.drawImage(qr_buffer, width-49*mm, 23*mm, width=25*mm, height=25*mm, preserveAspectRatio=True, mask="auto")
    pdf.setFont("Helvetica", 8)
    pdf.drawRightString(width-22*mm, 18*mm, "Scan QR to verify online | DEMO")
    pdf.showPage()
    pdf.save()
    output.seek(0)
    return send_file(output, mimetype="application/pdf", as_attachment=True,
                     download_name="Certificate_" + saved_hash[:12] + ".pdf")


# VERIFY CERTIFICATE
@app.route("/verify", methods=["GET", "POST"])
def verify():
    result = None
    cert_hash = ""

    if request.method == "POST":
        cert_hash = request.form.get("certificate_hash", "").strip()
    else:
        cert_hash = request.args.get("hash", "").strip()

    if cert_hash:
        with connect() as con:
            row = con.execute(
                """SELECT student_name, course, certificate_hash, issue_date
                FROM certificates WHERE certificate_hash=?""",
                (cert_hash,)
            ).fetchone()

        chain = load_chain()
        recorded = any(
            isinstance(b.get("data"), dict)
            and b["data"].get("certificate_hash") == cert_hash
            for b in chain
        )
        result = {"valid": bool(row and recorded), "certificate": row}

    return render_template("verify.html", result=result, cert_hash=cert_hash)


# CERTIFICATE LIST
@app.route("/certificates")
@login_required
def certificates():
    with connect() as con:
        rows = con.execute(
            """SELECT student_name, course, certificate_hash, issue_date
            FROM certificates ORDER BY id DESC"""
        ).fetchall()

    return render_template("certificates.html", rows=rows)


# BLOCKCHAIN
@app.route("/blockchain")
@login_required
def blockchain():
    return render_template("blockchain.html", chain=load_chain())


# INTEGRITY CHECK
@app.route("/integrity")
@login_required
def integrity():
    chain = load_chain()
    valid = True

    for i in range(1, len(chain)):
        block = chain[i]

        expected = hashlib.sha256(
            (json.dumps(block["data"], sort_keys=True)
             + block["previous_hash"]).encode()
        ).hexdigest()

        if (
            block["previous_hash"] != chain[i - 1]["hash"]
            or block["hash"] != expected
        ):
            valid = False
            break

    return render_template("integrity.html", valid=valid)


# Initialize tables when running locally or under Gunicorn.
init_db()

if __name__ == "__main__":
    app.run(debug=True)
