from flask import Flask, render_template, request, redirect, url_for, session
import os
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.secret_key = "change_this_to_random_secret_key"


# ---------------- DATABASE CONNECTION ----------------
def get_connection():
    DATABASE_URL = os.environ.get("DATABASE_URL")

    conn = psycopg2.connect(
        DATABASE_URL,
        cursor_factory=RealDictCursor
    )
    return conn


# ---------------- DATABASE INITIALIZATION ----------------
def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        phone TEXT NOT NULL,
        company TEXT NOT NULL,
        role TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS projects (
        id SERIAL PRIMARY KEY,
        project_name TEXT NOT NULL,
        company TEXT NOT NULL,
        created_by TEXT NOT NULL,
        total_budget REAL NOT NULL,
        progress_percent REAL NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS owner_funding (
        id SERIAL PRIMARY KEY,
        project_id INTEGER,
        stage_name TEXT NOT NULL,
        amount REAL NOT NULL,
        released_by TEXT NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contractor_expenses (
        id SERIAL PRIMARY KEY,
        project_id INTEGER,
        expense_type TEXT NOT NULL,
        amount REAL NOT NULL,
        added_by TEXT NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id SERIAL PRIMARY KEY,
        project_id INTEGER,
        amount REAL NOT NULL,
        requested_by TEXT NOT NULL,
        role TEXT NOT NULL,
        status TEXT DEFAULT 'Pending'
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS project_members (
        id SERIAL PRIMARY KEY,
        project_id INTEGER,
        user_id INTEGER,
        project_role TEXT NOT NULL
    )
    """)

    conn.commit()
    conn.close()


# 🔥 IMPORTANT: Initialize DB on startup (works for Gunicorn/Render)
try:
    init_db()
except Exception:
    pass


# ---------------- HOME ----------------
@app.route("/")
def home():
    return render_template("home.html")


# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET", "POST"])
def login():

    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id, name, company, role FROM users WHERE email=%s AND password=%s",
            (email, password)
        )

        user = cursor.fetchone()
        conn.close()

        if user:
            session["user_id"] = user["id"]
            session["name"] = user["name"]
            session["company"] = user["company"]
            session["role"] = user["role"]
            return redirect(url_for("dashboard"))
        else:
            return render_template("login.html", error="Invalid Email or Password")

    return render_template("login.html")


# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


# ---------------- REGISTER ----------------
@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        conn = get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO users (name, phone, company, role, email, password)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                request.form["name"],
                request.form["phone"],
                request.form["company"],
                request.form["role"],
                request.form["email"],
                request.form["password"]
            ))

            conn.commit()
            conn.close()
            return redirect(url_for("login"))

        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            conn.close()
            return render_template("register.html", error="Email already exists!")

    return render_template("register.html")


# ---------------- DASHBOARD ----------------
@app.route("/dashboard")
def dashboard():

    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]
    name = session["name"]
    company = session["company"]
    role = session["role"]

    conn = get_connection()
    cursor = conn.cursor()

    if role == "Business Owner / Founder / CXO":
        cursor.execute("SELECT * FROM projects WHERE created_by=%s", (name,))
    else:
        cursor.execute("""
            SELECT p.*
            FROM projects p
            JOIN project_members pm ON p.id = pm.project_id
            WHERE pm.user_id=%s
        """, (user_id,))

    projects = cursor.fetchall()
    conn.close()

    return render_template("dashboard.html",
                           name=name,
                           company=company,
                           role=role,
                           projects=projects)


# ---------------- CREATE PROJECT ----------------
@app.route("/create_project", methods=["GET", "POST"])
def create_project():

    if "user_id" not in session:
        return redirect(url_for("login"))

    name = session["name"]
    company = session["company"]

    conn = get_connection()
    cursor = conn.cursor()

    if request.method == "POST":

        cursor.execute("""
            INSERT INTO projects (project_name, company, created_by,
                                  total_budget, progress_percent)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (
            request.form["project_name"],
            company,
            name,
            float(request.form["total_budget"]),
            float(request.form["progress_percent"])
        ))

        project_id = cursor.fetchone()["id"]

        selected_users = request.form.getlist("project_members")

        for user_id in selected_users:
            role_assigned = request.form.get(f"role_{user_id}")

            cursor.execute("""
                INSERT INTO project_members (project_id, user_id, project_role)
                VALUES (%s, %s, %s)
            """, (project_id, user_id, role_assigned))

        conn.commit()
        conn.close()

        return redirect(url_for("dashboard"))

    cursor.execute("SELECT id, name, role FROM users WHERE role!='Business Owner / Founder / CXO'")
    users = cursor.fetchall()
    conn.close()

    return render_template("create_project.html", users=users)


# ---------------- PROJECT DETAIL ----------------
@app.route("/project/<int:project_id>", methods=["GET", "POST"])
def project_detail(project_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    name = session["name"]
    role = session["role"]

    conn = get_connection()
    cursor = conn.cursor()

    if request.method == "POST" and "fund_amount" in request.form:
        stage_name = request.form.get("stage_name")
        amount = float(request.form.get("fund_amount") or 0)

        if amount > 0:
            cursor.execute("""
                INSERT INTO owner_funding (project_id, stage_name, amount, released_by)
                VALUES (%s, %s, %s, %s)
            """, (project_id, stage_name, amount, name))
            conn.commit()

    if request.method == "POST" and "expense_amount" in request.form:
        expense_type = request.form.get("expense_type")
        amount = float(request.form.get("expense_amount") or 0)

        if amount > 0:
            cursor.execute("""
                INSERT INTO contractor_expenses (project_id, expense_type, amount, added_by)
                VALUES (%s, %s, %s, %s)
            """, (project_id, expense_type, amount, name))
            conn.commit()

    if request.method == "POST" and "request_amount" in request.form:
        amount = float(request.form.get("request_amount") or 0)

        if amount > 0:
            cursor.execute("""
                INSERT INTO payments (project_id, amount, requested_by, role, status)
                VALUES (%s, %s, %s, %s, 'Pending')
            """, (project_id, amount, name, role))
            conn.commit()

    if request.method == "POST" and "update_progress" in request.form:
        if role == "Contractor":
            new_progress = float(request.form.get("update_progress") or 0)

            if 0 <= new_progress <= 100:
                cursor.execute("""
                    UPDATE projects
                    SET progress_percent=%s
                    WHERE id=%s
                """, (new_progress, project_id))
                conn.commit()
                return redirect(url_for("project_detail", project_id=project_id))

    cursor.execute("SELECT * FROM projects WHERE id=%s", (project_id,))
    project = cursor.fetchone()

    cursor.execute("SELECT SUM(amount) as total FROM owner_funding WHERE project_id=%s", (project_id,))
    total_funds = cursor.fetchone()["total"] or 0

    cursor.execute("SELECT SUM(amount) as total FROM contractor_expenses WHERE project_id=%s", (project_id,))
    total_expenses = cursor.fetchone()["total"] or 0

    cursor.execute("""
        SELECT expense_type, SUM(amount) as total
        FROM contractor_expenses
        WHERE project_id=%s
        GROUP BY expense_type
    """, (project_id,))
    breakdown_rows = cursor.fetchall()

    expense_breakdown = {}
    for row in breakdown_rows:
        expense_breakdown[row["expense_type"]] = row["total"]

    cursor.execute("SELECT * FROM payments WHERE project_id=%s AND status='Pending'", (project_id,))
    pending_payments = cursor.fetchall()

    cursor.execute("""
        SELECT stage_name, amount, released_by
        FROM owner_funding
        WHERE project_id=%s
        ORDER BY id DESC
    """, (project_id,))
    funding_history = cursor.fetchall()

    cursor.execute("""
        SELECT u.name, pm.project_role
        FROM project_members pm
        JOIN users u ON pm.user_id = u.id
        WHERE pm.project_id=%s
    """, (project_id,))
    team_members = cursor.fetchall()

    conn.close()

    balance = total_funds - total_expenses
    profit = balance

    if total_funds > 0:
        profit_percentage = (profit / total_funds) * 100
        expense_utilization = (total_expenses / total_funds) * 100
    else:
        profit_percentage = 0
        expense_utilization = 0

    if role == "Business Owner / Founder / CXO":
        return render_template("owner_dashboard.html",
                               project=project,
                               total_funds=total_funds,
                               total_expenses=total_expenses,
                               balance=balance,
                               profit=profit,
                               profit_percentage=profit_percentage,
                               expense_utilization=expense_utilization,
                               expense_breakdown=expense_breakdown,
                               pending_payments=pending_payments,
                               team_members=team_members,
                               funding_history=funding_history)

    elif role == "Contractor":
        return render_template("contractor_dashboard.html",
                               project=project,
                               total_funds=total_funds,
                               total_expenses=total_expenses,
                               balance=balance,
                               profit=profit,
                               profit_percentage=profit_percentage,
                               expense_utilization=expense_utilization,
                               expense_breakdown=expense_breakdown,
                               pending_payments=pending_payments,
                               team_members=team_members,
                               funding_history=funding_history)

    else:
        return render_template("operational_dashboard.html",
                               project=project,
                               team_members=team_members)
# --------- FORCE DATABASE INITIALIZATION ON START ---------
with app.app_context():
    init_db()

# DO NOT manually set port in production
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)