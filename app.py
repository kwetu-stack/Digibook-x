import os, csv, io, uuid
from datetime import datetime, date, timedelta
from pathlib import Path

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, send_file, abort
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, login_user, logout_user,
    current_user, login_required, UserMixin
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# optional PDF export (graceful fallback if missing)
try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4, landscape
except Exception:
    canvas = None
    A4 = landscape = None

# -------------------- Demo Mode --------------------
DEMO_MODE = True

def is_demo():
    return DEMO_MODE

class DemoSQLAlchemy(SQLAlchemy):
    """Override SQLAlchemy to prevent commits in demo mode"""
    def commit(self):
        if not is_demo():
            super().commit()
        # In demo mode, changes are not committed

# -------------------- App & DB setup --------------------
load_dotenv()
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
DATA_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

db_url = os.getenv("DATABASE_URL", "sqlite:///" + str(DATA_DIR / "visibook.db"))
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["DEBUG"] = True
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB uploads

db = DemoSQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"


@app.context_processor
def inject_asset_version():
    def asset_version(filename: str) -> int:
        try:
            return int((BASE_DIR / "static" / filename).stat().st_mtime)
        except OSError:
            return 0

    return {"asset_version": asset_version}

# -------------------- Models --------------------
class ActivityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    action = db.Column(db.String(120))
    entity = db.Column(db.String(80))
    entity_id = db.Column(db.Integer, nullable=True)
    at = db.Column(db.DateTime, default=datetime.utcnow)

class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    site_name = db.Column(db.String(120))
    site_logo_path = db.Column(db.String(255))
    badge_footer = db.Column(db.String(255))

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # Admin, Guard
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

class Unit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    type = db.Column(db.String(20), nullable=False)  # Office, House, Shop, Dept, Other
    location = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Purpose(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Host(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(40))
    email = db.Column(db.String(120))
    unit_id = db.Column(db.Integer, db.ForeignKey("unit.id"))
    unit = db.relationship("Unit", lazy="joined")
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Visitor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(40), nullable=False)
    national_id = db.Column(db.String(60))
    id_number = db.Column(db.String(60))
    vehicle_reg = db.Column(db.String(40))
    vehicle_type = db.Column(db.String(40))
    host_id = db.Column(db.Integer, db.ForeignKey("host.id"))
    host = db.relationship("Host", lazy="joined")
    unit_id = db.Column(db.Integer, db.ForeignKey("unit.id"))
    unit = db.relationship("Unit", lazy="joined", foreign_keys=[unit_id])
    purpose_id = db.Column(db.Integer, db.ForeignKey("purpose.id"))
    purpose = db.relationship("Purpose", lazy="joined")
    badge_no = db.Column(db.String(40), index=True)
    checkin_time = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    checkout_time = db.Column(db.DateTime, nullable=True, index=True)
    notes = db.Column(db.String(255))
    photo_path = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def vehicle_plate(self):
        return self.vehicle_reg

    @vehicle_plate.setter
    def vehicle_plate(self, value):
        self.vehicle_reg = value

    @property
    def check_in_time(self):
        return self.checkin_time

    @check_in_time.setter
    def check_in_time(self, value):
        self.checkin_time = value

    @property
    def national_id_value(self):
        return self.national_id or self.id_number

    @property
    def id_number_value(self):
        return self.national_id or self.id_number

# -------------------- Auth --------------------
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def log(action, entity="", entity_id=None):
    if is_demo():
        return  # Skip logging in demo mode to avoid demo data pollution
    try:
        db.session.add(ActivityLog(
            user_id=current_user.id if current_user.is_authenticated else None,
            action=action, entity=entity, entity_id=entity_id
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()

# -------------------- Utilities --------------------
from functools import wraps

def demo_protect(f):
    """Decorator to protect routes in demo mode"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if is_demo():
            flash("Demo Mode: This action is disabled.", "warning")
            return redirect(request.referrer or url_for("dashboard"))
        return f(*args, **kwargs)
    return wrapper

def role_required(role):
    def deco(f):
        @wraps(f)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.role != role:
                flash("You do not have permission for this action.", "error")
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)
        return wrapper
    return deco


def get_safe_next_url():
    next_url = request.args.get("next")
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return None


def ensure_authenticated():
    if current_user.is_authenticated:
        return None
    if is_demo():
        demo_user = User.query.filter_by(username="admin").first()
        if demo_user:
            login_user(demo_user)
            return None
    return redirect(url_for("login", next=request.path))

HOSPITAL_LOCATIONS = [
    ("Reception", "Office", "Ground floor lobby"),
    ("Emergency (ER)", "Dept", "Emergency wing"),
    ("ICU", "Dept", "Critical care unit"),
    ("Ward A", "Dept", "Inpatient wing"),
    ("Pharmacy", "Dept", "Ground floor"),
    ("Lab", "Dept", "Diagnostics wing"),
    ("Admin Office", "Office", "Administration block"),
]

HOSPITAL_PURPOSES = [
    "Visit Patient",
    "Consultation",
    "Emergency",
    "Delivery",
    "Purchase Medicine",
]

HOSPITAL_STAFF = [
    ("Reception Desk", "0712001001", "reception@digibookx.demo", "Reception"),
    ("ER Triage Desk", "0712001002", "er@digibookx.demo", "Emergency (ER)"),
    ("ICU Desk", "0712001003", "icu@digibookx.demo", "ICU"),
    ("Ward A Desk", "0712001004", "warda@digibookx.demo", "Ward A"),
    ("Pharmacy Desk", "0712001005", "pharmacy@digibookx.demo", "Pharmacy"),
    ("Lab Desk", "0712001006", "lab@digibookx.demo", "Lab"),
    ("Admin Desk", "0712001007", "admin@digibookx.demo", "Admin Office"),
]

HOSPITAL_VISITOR_SCENARIOS = [
    ("Wanjiku Njeri", "0712345601", "25678901", "Ward A", "Ward A Desk", "Visit Patient", "KDA 234A", "Car", 6, 9, 15, True, 70),
    ("Brian Otieno", "0722457801", "31245678", "Reception", "Reception Desk", "Consultation", "", "", 6, 11, 20, True, 45),
    ("Joseph Mwangi", "0733568901", "27890123", "Emergency (ER)", "ER Triage Desk", "Emergency", "KCY 118Q", "SUV", 6, 2, 5, False, 0),
    ("Mercy Chebet", "0701122301", "30124567", "Pharmacy", "Pharmacy Desk", "Purchase Medicine", "", "", 6, 8, 10, True, 35),
    ("Peter Kiptoo", "0798765401", "22113456", "Lab", "Lab Desk", "Consultation", "KDL 541M", "Motorbike", 5, 13, 5, True, 25),
    ("Alice Atieno", "0711123402", "33456789", "ICU", "ICU Desk", "Visit Patient", "", "", 5, 15, 0, True, 60),
    ("Samuel Kariuki", "0722234502", "28765432", "Admin Office", "Admin Desk", "Delivery", "KDM 902R", "Pickup", 5, 10, 45, True, 40),
    ("Faith Wangari", "0733345602", "24567890", "Reception", "Reception Desk", "Consultation", "", "", 5, 14, 30, True, 50),
    ("George Odhiambo", "0744456702", "29876543", "ICU", "ICU Desk", "Visit Patient", "KCU 445P", "Car", 4, 19, 20, False, 0),
    ("Janet Wairimu", "0755567802", "27654321", "Admin Office", "Admin Desk", "Delivery", "KDD 781B", "Van", 4, 7, 55, True, 20),
    ("David Kimani", "0766678902", "24321098", "Emergency (ER)", "ER Triage Desk", "Emergency", "", "", 4, 1, 40, True, 180),
    ("Lilian Akinyi", "0777789002", "31987654", "Reception", "Reception Desk", "Consultation", "KBT 334H", "SUV", 3, 9, 25, True, 55),
    ("Mohamed Noor", "0788890102", "22654319", "Ward A", "Ward A Desk", "Visit Patient", "", "", 3, 12, 15, True, 20),
    ("Lucy Muthoni", "0709901202", "29012345", "Pharmacy", "Pharmacy Desk", "Purchase Medicine", "", "", 3, 17, 40, True, 15),
    ("Dennis Kipchumba", "0710012303", "25556667", "Lab", "Lab Desk", "Consultation", "KDG 200T", "Car", 2, 20, 10, False, 0),
    ("Caroline Jepkosgei", "0721123403", "27889900", "Ward A", "Ward A Desk", "Visit Patient", "", "", 2, 8, 35, True, 30),
    ("Martin Ochieng", "0732234503", "23334445", "Lab", "Lab Desk", "Consultation", "KCK 712E", "SUV", 2, 16, 5, True, 45),
    ("Stella Naliaka", "0743345603", "29991111", "ICU", "ICU Desk", "Visit Patient", "", "", 1, 11, 50, True, 80),
    ("Paul Mutiso", "0754456703", "24445556", "Admin Office", "Admin Desk", "Delivery", "KDV 850U", "Truck", 1, 6, 20, True, 15),
    ("Naomi Wambui", "0765567803", "27778889", "Emergency (ER)", "ER Triage Desk", "Emergency", "", "", 1, 2, 15, True, 210),
    ("Kevin Maina", "0776678903", "32223334", "Reception", "Reception Desk", "Consultation", "KDA 900C", "Car", 0, 10, 10, True, 50),
    ("Purity Achieng", "0787789003", "28887776", "Pharmacy", "Pharmacy Desk", "Purchase Medicine", "", "", 0, 13, 25, True, 25),
    ("Victor Ombui", "0798890103", "26665554", "Ward A", "Ward A Desk", "Visit Patient", "KBX 412L", "Motorbike", 0, 18, 15, False, 0),
]

def build_hospital_demo_visitors(units_by_name, hosts_by_name, purposes_by_name):
    visitors = []
    base_date = datetime.utcnow().date() - timedelta(days=6)
    for i, row in enumerate(HOSPITAL_VISITOR_SCENARIOS, start=1):
        name, phone, national_id, location_name, staff_name, purpose_name, plate, vehicle_type, day_offset, hour, minute, checked_out, stay_minutes = row
        checkin_time = datetime.combine(base_date + timedelta(days=day_offset), datetime.min.time()) + timedelta(hours=hour, minutes=minute)
        checkout_time = checkin_time + timedelta(minutes=stay_minutes) if checked_out else None
        location = units_by_name.get(location_name)
        staff = hosts_by_name.get(staff_name) if staff_name else None
        purpose = purposes_by_name.get(purpose_name)
        visitors.append(
            Visitor(
                full_name=name,
                phone=phone,
                national_id=national_id,
                id_number=national_id,
                vehicle_reg=plate or None,
                vehicle_type=vehicle_type or None,
                host_id=staff.id if staff else None,
                unit_id=location.id if location else None,
                purpose_id=purpose.id if purpose else None,
                badge_no=gen_badge_no(checkin_time),
                checkin_time=checkin_time,
                checkout_time=checkout_time,
                notes="Hospital demo record",
            )
        )
    return visitors


LEGACY_DEMO_UNIT_NAMES = {
    "Gate",
    "Block A",
    "Block B",
    "Management Office",
    "Shops Wing",
    "ICT Office",
}

EXPECTED_DEMO_UNIT_NAMES = {name for name, _, _ in HOSPITAL_LOCATIONS}


def demo_dataset_needs_refresh():
    existing_units = {name for (name,) in db.session.query(Unit.name).all()}
    if not existing_units:
        return False
    if existing_units & LEGACY_DEMO_UNIT_NAMES:
        return True
    if existing_units != EXPECTED_DEMO_UNIT_NAMES:
        return True
    first_visitor = Visitor.query.order_by(Visitor.id.asc()).first()
    return bool(first_visitor and not (first_visitor.national_id or first_visitor.id_number))


def reset_demo_dataset():
    Visitor.query.delete()
    Host.query.delete()
    Unit.query.delete()
    Purpose.query.delete()

    setting = Setting.query.first()
    if setting:
        setting.site_name = "Digibook-X Hospital"
        setting.badge_footer = "Please return visitor badge at the hospital exit."
        setting.site_logo_path = None

def ensure_seed():
    db.create_all()
    if is_demo() and demo_dataset_needs_refresh():
        reset_demo_dataset()
    if not Setting.query.first():
        s = Setting(site_name="Digibook-X Hospital", badge_footer="Please return visitor badge at the hospital exit.")
        db.session.add(s)
    if not Purpose.query.first():
        for name in HOSPITAL_PURPOSES:
            db.session.add(Purpose(name=name))
    if not Unit.query.first():
        for n, t, l in HOSPITAL_LOCATIONS:
            db.session.add(Unit(name=n, type=t, location=l))
    if not Host.query.first():
        units_by_name = {u.name: u for u in Unit.query.all()}
        for name, phone, email, location_name in HOSPITAL_STAFF:
            location = units_by_name.get(location_name)
            if location:
                db.session.add(Host(name=name, phone=phone, email=email, unit_id=location.id, active=True))
    if not User.query.first():
        admin = User(name="Admin", username="admin", role="Admin", active=True); admin.set_password("admin123")
        guard = User(name="Guard", username="guard", role="Guard", active=True); guard.set_password("guard123")
        db.session.add_all([admin, guard])
    if not Visitor.query.first():
        units_by_name = {u.name: u for u in Unit.query.all()}
        hosts_by_name = {h.name: h for h in Host.query.all()}
        purposes_by_name = {p.name: p for p in Purpose.query.all()}
        for visitor in build_hospital_demo_visitors(units_by_name, hosts_by_name, purposes_by_name):
            db.session.add(visitor)
    db.session.commit()

def gen_badge_no(ref_time=None):
    ref = ref_time or datetime.utcnow()
    return f"KWT-{ref.strftime('%y%m')}-{str(uuid.uuid4())[:4].upper()}"

# --------------- Context & error pages ---------------
@app.context_processor
def inject_now():
    return {"now": datetime.utcnow, "is_demo": is_demo}

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404

# -------------------- Routes --------------------
@app.route("/health")
def health():
    return "OK"

@app.route("/")
@app.route("/dashboard")
def dashboard():
    auth_redirect = ensure_authenticated()
    if auth_redirect:
        return auth_redirect
    today = datetime.utcnow().date()
    start_month = today.replace(day=1)
    today_count = Visitor.query.filter(Visitor.checkin_time >= datetime.combine(today, datetime.min.time())).count()
    inside_count = Visitor.query.filter(Visitor.checkout_time.is_(None)).count()
    month_count = Visitor.query.filter(Visitor.checkin_time >= datetime.combine(start_month, datetime.min.time())).count()
    return render_template("dashboard.html", tiles={"today": today_count, "inside": inside_count, "month": month_count})

# ------ Auth ------
@app.route("/login", methods=["GET","POST"])
def login():
    # Auto-login in demo mode
    if is_demo():
        demo_user = User.query.filter_by(username="admin").first()
        if demo_user and not current_user.is_authenticated:
            login_user(demo_user)
        return redirect(get_safe_next_url() or url_for("dashboard"))
    
    if request.method == "POST":
        u = User.query.filter_by(username=request.form["username"]).first()
        if not u or not u.active or not u.check_password(request.form["password"]):
            flash("Invalid credentials or inactive user.", "error")
            return redirect(url_for("login"))
        login_user(u)
        log("login", "User", u.id)
        return redirect(get_safe_next_url() or url_for("dashboard"))
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    log("logout", "User", current_user.id)
    logout_user()
    return redirect(url_for("login"))

# ------ Visitors ------
from sqlalchemy import and_, inspect, text

def ensure_schema():
    inspector = inspect(db.engine)
    visitor_columns = {col["name"] for col in inspector.get_columns("visitor")} if inspector.has_table("visitor") else set()

    if "checkout_time" not in visitor_columns:
        ddl_type = "DATETIME" if db.engine.dialect.name == "sqlite" else "TIMESTAMP"
        with db.engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE visitor ADD COLUMN checkout_time {ddl_type} NULL"))
    if "vehicle_type" not in visitor_columns:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE visitor ADD COLUMN vehicle_type VARCHAR(40) NULL"))
    if "national_id" not in visitor_columns:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE visitor ADD COLUMN national_id VARCHAR(60) NULL"))
    with db.engine.begin() as conn:
        conn.execute(text("UPDATE visitor SET national_id = id_number WHERE national_id IS NULL AND id_number IS NOT NULL"))

def parse_optional_int(value):
    return int(value) if value else None

def validate_visit_destination(unit_id):
    if unit_id is None:
        return "Office/Department to Visit is required."
    return None

def apply_filters(q):
    # shared filter parser for list & reports
    q_from = request.args.get("from") or request.args.get("q_from")
    q_to = request.args.get("to") or request.args.get("q_to")
    q_status = request.args.get("status") or request.args.get("q_status")
    q_unit_id = request.args.get("unit_id") or request.args.get("q_unit_id")
    q_purpose_id = request.args.get("purpose_id") or request.args.get("q_purpose_id")

    if q_from:
        q = q.filter(Visitor.checkin_time >= datetime.strptime(q_from, "%Y-%m-%d"))
    if q_to:
        end = datetime.strptime(q_to, "%Y-%m-%d") + timedelta(days=1)
        q = q.filter(Visitor.checkin_time < end)
    if q_status == "inside":
        q = q.filter(Visitor.checkout_time.is_(None))
    elif q_status == "out":
        q = q.filter(Visitor.checkout_time.is_not(None))
    if q_unit_id:
        q = q.filter(Visitor.unit_id == int(q_unit_id))
    if q_purpose_id:
        q = q.filter(Visitor.purpose_id == int(q_purpose_id))
    return q

@app.route("/visitors")
def visitors_list():
    auth_redirect = ensure_authenticated()
    if auth_redirect:
        return auth_redirect
    q = Visitor.query.order_by(Visitor.checkin_time.desc())
    q = apply_filters(q)
    items = q.limit(200).all()
    units = Unit.query.order_by(Unit.name).all()
    purposes = Purpose.query.order_by(Purpose.name).all()
    return render_template("visitors_list.html", items=items,
                           units=units, purposes=purposes,
                           q_from=request.args.get("from"), q_to=request.args.get("to"),
                           q_status=request.args.get("status"),
                           q_unit_id=request.args.get("unit_id"),
                           q_purpose_id=request.args.get("purpose_id"))

@app.route("/visitors/new", methods=["GET","POST"])
def visitor_new():
    auth_redirect = ensure_authenticated()
    if auth_redirect:
        return auth_redirect
    units = Unit.query.order_by(Unit.name).all()
    purposes = Purpose.query.order_by(Purpose.name).all()
    if request.method == "POST":
        if is_demo():
            flash("Demo Mode: Visitor check-in simulated. Changes will not be saved.", "info")
            return redirect(request.referrer or url_for("visitors_list"))
        
        unit_id = parse_optional_int(request.form.get("unit_id"))
        purpose_id = parse_optional_int(request.form.get("purpose_id"))
        validation_error = validate_visit_destination(unit_id)
        if validation_error:
            flash(validation_error, "error")
            return redirect(url_for("visitor_new"))

        v = Visitor(
            full_name=request.form["full_name"].strip(),
            phone=request.form["phone"].strip(),
            national_id=request.form["national_id"].strip(),
            id_number=request.form["national_id"].strip(),
            vehicle_reg=request.form.get("vehicle_reg") or None,
            vehicle_type=request.form.get("vehicle_type") or None,
            host_id=None,
            unit_id=unit_id,
            purpose_id=purpose_id,
            badge_no=gen_badge_no()
        )
        db.session.add(v); db.session.commit()
        log("create", "Visitor", v.id)
        flash(f"Visitor checked-in. <a href='{url_for('visitor_badge', id=v.id)}'>Print badge</a>", "ok")
        return redirect(url_for("visitors_list"))
    return render_template("visitor_new.html", units=units, purposes=purposes)

@app.route("/visitors/<int:id>")
def visitor_detail(id):
    auth_redirect = ensure_authenticated()
    if auth_redirect:
        return auth_redirect
    v = Visitor.query.get_or_404(id)
    setting = Setting.query.first()
    return render_template("visitor_detail.html", v=v, setting=setting)

@app.route("/visitors/<int:id>/edit", methods=["GET","POST"])
def visitor_edit(id):
    auth_redirect = ensure_authenticated()
    if auth_redirect:
        return auth_redirect
    v = Visitor.query.get_or_404(id)
    units = Unit.query.order_by(Unit.name).all()
    purposes = Purpose.query.order_by(Purpose.name).all()
    if request.method == "POST":
        if is_demo():
            flash("Demo Mode: Changes will not be saved.", "info")
            return redirect(request.referrer or url_for("visitors_list"))
        
        unit_id = parse_optional_int(request.form.get("unit_id"))
        purpose_id = parse_optional_int(request.form.get("purpose_id"))
        validation_error = validate_visit_destination(unit_id)
        if validation_error:
            flash(validation_error, "error")
            return redirect(url_for("visitor_edit", id=v.id))

        v.full_name = request.form["full_name"].strip()
        v.phone = request.form["phone"].strip()
        v.national_id = request.form["national_id"].strip()
        v.id_number = request.form["national_id"].strip()
        v.vehicle_reg = request.form.get("vehicle_reg") or None
        v.vehicle_type = request.form.get("vehicle_type") or None
        v.host_id = None
        v.unit_id = unit_id
        v.purpose_id = purpose_id
        db.session.commit()
        log("update", "Visitor", v.id)
        flash("Saved.", "ok")
        return redirect(url_for("visitor_edit", id=v.id))
    return render_template("visitor_edit.html", v=v, units=units, purposes=purposes)

@app.route("/visitors/<int:id>/checkout", methods=["POST"])
def visitor_checkout(id):
    auth_redirect = ensure_authenticated()
    if auth_redirect:
        return auth_redirect
    
    v = Visitor.query.get_or_404(id)
    if is_demo():
        flash("Demo Mode: Check-out simulated. Changes will not be saved.", "info")
        return redirect(url_for("visitor_edit", id=id))
    
    if not v.checkout_time:
        v.checkout_time = datetime.utcnow()
        db.session.commit()
        log("checkout", "Visitor", v.id)
        flash("Visitor checked-out.", "ok")
    return redirect(url_for("visitor_edit", id=id))

@app.route("/visitors/<int:id>/delete", methods=["POST"])
def visitor_delete(id):
    auth_redirect = ensure_authenticated()
    if auth_redirect:
        return auth_redirect
    
    if is_demo():
        flash("Demo Mode: Delete action is disabled.", "warning")
        return redirect(url_for("visitors_list"))
    
    v = Visitor.query.get_or_404(id)
    db.session.delete(v); db.session.commit()
    log("delete", "Visitor", id)
    flash("Deleted.", "ok")
    return redirect(url_for("visitors_list"))

@app.route("/visitors/<int:id>/badge")
def visitor_badge(id):
    auth_redirect = ensure_authenticated()
    if auth_redirect:
        return auth_redirect
    v = Visitor.query.get_or_404(id)
    setting = Setting.query.first() or Setting(site_name="Site")
    return render_template("visitor_badge.html", v=v, setting=setting)

# ------ Hosts CRUD ------
@app.route("/hosts")
def hosts_list():
    auth_redirect = ensure_authenticated()
    if auth_redirect:
        return auth_redirect
    items = Host.query.order_by(Host.name).all()
    return render_template("hosts_list.html", items=items)

@app.route("/hosts/new", methods=["GET","POST"])
@role_required("Admin")
def host_new():
    units = Unit.query.order_by(Unit.name).all()
    if request.method == "POST":
        if is_demo():
            flash("Demo Mode: Contact creation simulated. Changes will not be saved.", "info")
            return redirect(url_for("hosts_list"))
        
        unit_id = parse_optional_int(request.form.get("unit_id"))
        if unit_id is None:
            flash("Office/Department assignment is required.", "error")
            return redirect(url_for("host_new"))

        h = Host(
            name=request.form["name"].strip(),
            phone=request.form.get("phone") or None,
            email=request.form.get("email") or None,
            unit_id=unit_id,
            active=(request.form.get("active","1")=="1")
        )
        db.session.add(h); db.session.commit()
        log("create","Host",h.id)
        return redirect(url_for("hosts_list"))
    return render_template("host_form.html", h=None, units=units)

@app.route("/hosts/<int:id>/edit", methods=["GET","POST"])
@role_required("Admin")
def host_edit(id):
    h = Host.query.get_or_404(id)
    units = Unit.query.order_by(Unit.name).all()
    if request.method == "POST":
        if is_demo():
            flash("Demo Mode: Changes will not be saved.", "info")
            return redirect(url_for("hosts_list"))
        
        unit_id = parse_optional_int(request.form.get("unit_id"))
        if unit_id is None:
            flash("Office/Department assignment is required.", "error")
            return redirect(url_for("host_edit", id=h.id))

        h.name = request.form["name"].strip()
        h.phone = request.form.get("phone") or None
        h.email = request.form.get("email") or None
        h.unit_id = unit_id
        h.active = (request.form.get("active","1")=="1")
        db.session.commit()
        log("update","Host",h.id)
        flash("Saved.","ok")
        return redirect(url_for("host_edit", id=h.id))
    return render_template("host_form.html", h=h, units=units)

@app.route("/hosts/<int:id>/delete", methods=["POST"])
@role_required("Admin")
def host_delete(id):
    if is_demo():
        flash("Demo Mode: Delete action is disabled.", "warning")
        return redirect(url_for("hosts_list"))
    
    h = Host.query.get_or_404(id)
    db.session.delete(h); db.session.commit()
    log("delete","Host",id)
    return redirect(url_for("hosts_list"))

# ------ Units CRUD ------
@app.route("/units")
@app.route("/locations")
def units_list():
    auth_redirect = ensure_authenticated()
    if auth_redirect:
        return auth_redirect
    items = Unit.query.order_by(Unit.name).all()
    return render_template("units_list.html", items=items)

@app.route("/units/new", methods=["GET","POST"])
@role_required("Admin")
def unit_new():
    if request.method == "POST":
        if is_demo():
            flash("Demo Mode: Office/Department creation simulated. Changes will not be saved.", "info")
            return redirect(url_for("units_list"))
        
        u = Unit(name=request.form["name"].strip(), type=request.form["type"], location=request.form.get("location") or None)
        db.session.add(u); db.session.commit()
        log("create","Unit",u.id)
        return redirect(url_for("units_list"))
    return render_template("unit_form.html", u=None)

@app.route("/units/<int:id>/edit", methods=["GET","POST"])
@role_required("Admin")
def unit_edit(id):
    u = Unit.query.get_or_404(id)
    if request.method == "POST":
        if is_demo():
            flash("Demo Mode: Changes will not be saved.", "info")
            return redirect(url_for("units_list"))
        
        u.name = request.form["name"].strip()
        u.type = request.form["type"]
        u.location = request.form.get("location") or None
        db.session.commit()
        log("update","Unit",u.id)
        flash("Saved.","ok")
        return redirect(url_for("unit_edit", id=u.id))
    return render_template("unit_form.html", u=u)

@app.route("/units/<int:id>/delete", methods=["POST"])
@role_required("Admin")
def unit_delete(id):
    if is_demo():
        flash("Demo Mode: Delete action is disabled.", "warning")
        return redirect(url_for("units_list"))
    
    u = Unit.query.get_or_404(id)
    db.session.delete(u); db.session.commit()
    log("delete","Unit",id)
    return redirect(url_for("units_list"))

# ------ Purposes CRUD ------
@app.route("/purposes")
def purposes_list():
    auth_redirect = ensure_authenticated()
    if auth_redirect:
        return auth_redirect
    items = Purpose.query.order_by(Purpose.name).all()
    return render_template("purposes_list.html", items=items)

@app.route("/purposes/new", methods=["GET","POST"])
@role_required("Admin")
def purpose_new():
    if request.method == "POST":
        if is_demo():
            flash("Demo Mode: Purpose creation simulated. Changes will not be saved.", "info")
            return redirect(url_for("purposes_list"))
        
        p = Purpose(name=request.form["name"].strip())
        db.session.add(p); db.session.commit()
        log("create","Purpose",p.id)
        return redirect(url_for("purposes_list"))
    return render_template("purpose_form.html", p=None)

@app.route("/purposes/<int:id>/edit", methods=["GET","POST"])
@role_required("Admin")
def purpose_edit(id):
    p = Purpose.query.get_or_404(id)
    if request.method == "POST":
        if is_demo():
            flash("Demo Mode: Changes will not be saved.", "info")
            return redirect(url_for("purposes_list"))
        
        p.name = request.form["name"].strip()
        db.session.commit()
        log("update","Purpose",p.id)
        flash("Saved.","ok")
        return redirect(url_for("purpose_edit", id=p.id))
    return render_template("purpose_form.html", p=p)

@app.route("/purposes/<int:id>/delete", methods=["POST"])
@role_required("Admin")
def purpose_delete(id):
    if is_demo():
        flash("Demo Mode: Delete action is disabled.", "warning")
        return redirect(url_for("purposes_list"))
    
    p = Purpose.query.get_or_404(id)
    db.session.delete(p); db.session.commit()
    log("delete","Purpose",id)
    return redirect(url_for("purposes_list"))

# ------ Users (Admin only) ------
@app.route("/users")
@role_required("Admin")
def users_list():
    if is_demo():
        flash("Demo Mode: User management is restricted.", "warning")
        return redirect(url_for("dashboard"))
    items = User.query.order_by(User.name).all()
    return render_template("users_list.html", items=items)

@app.route("/users/new", methods=["GET","POST"])
@role_required("Admin")
def user_new():
    if is_demo():
        flash("Demo Mode: User creation is disabled.", "warning")
        return redirect(url_for("dashboard"))
    
    if request.method == "POST":
        u = User(
            name=request.form["name"].strip(),
            username=request.form["username"].strip(),
            role=request.form["role"],
            active=(request.form.get("active","1")=="1")
        )
        pwd = request.form.get("password")
        if not pwd:
            flash("Password required.", "error")
            return redirect(url_for("user_new"))
        u.set_password(pwd)
        db.session.add(u); db.session.commit()
        log("create","User",u.id)
        return redirect(url_for("users_list"))
    return render_template("user_form.html", u=None)

@app.route("/users/<int:id>/edit", methods=["GET","POST"])
@role_required("Admin")
def user_edit(id):
    if is_demo():
        flash("Demo Mode: User editing is disabled.", "warning")
        return redirect(url_for("dashboard"))
    
    u = User.query.get_or_404(id)
    if request.method == "POST":
        u.name = request.form["name"].strip()
        u.username = request.form["username"].strip()
        u.role = request.form["role"]
        u.active = (request.form.get("active","1")=="1")
        pwd = request.form.get("password")
        if pwd:
            u.set_password(pwd)
        db.session.commit()
        log("update","User",u.id)
        flash("Saved.","ok")
        return redirect(url_for("user_edit", id=u.id))
    return render_template("user_form.html", u=u)

@app.route("/users/<int:id>/delete", methods=["POST"])
@role_required("Admin")
def user_delete(id):
    if is_demo():
        flash("Demo Mode: Delete action is disabled.", "warning")
        return redirect(url_for("dashboard"))
    
    u = User.query.get_or_404(id)
    db.session.delete(u); db.session.commit()
    log("delete","User",id)
    return redirect(url_for("users_list"))

# ------ Reports & Export ------
@app.route("/reports")
def reports():
    auth_redirect = ensure_authenticated()
    if auth_redirect:
        return auth_redirect
    q = Visitor.query.order_by(Visitor.checkin_time.desc())
    q = apply_filters(q)
    items = q.limit(500).all()
    units = Unit.query.order_by(Unit.name).all()
    purposes = Purpose.query.order_by(Purpose.name).all()
    return render_template("reports.html", items=items,
                           units=units, purposes=purposes,
                           q_from=request.args.get("from"), q_to=request.args.get("to"),
                           q_status=request.args.get("status"),
                           q_unit_id=request.args.get("unit_id"),
                           q_purpose_id=request.args.get("purpose_id"))

def _export_rows():
    q = apply_filters(Visitor.query.order_by(Visitor.checkin_time.desc()))
    return q.all()

@app.route("/export/csv")
def export_csv():
    auth_redirect = ensure_authenticated()
    if auth_redirect:
        return auth_redirect
    rows = _export_rows()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["check_in","check_out","visitor","phone","national_id","office_or_department_to_visit","purpose","vehicle","status","badge_no"])
    for v in rows:
        status = "Active" if not v.checkout_time else "Checked-out"
        vehicle = v.vehicle_reg or ""
        if vehicle and v.vehicle_type:
            vehicle = f"{vehicle} ({v.vehicle_type})"
        w.writerow([
            v.checkin_time.strftime("%Y-%m-%d %H:%M"),
            v.checkout_time.strftime("%Y-%m-%d %H:%M") if v.checkout_time else "Active",
            v.full_name, v.phone, v.national_id or v.id_number or "", v.unit.name if v.unit else "", v.purpose.name if v.purpose else "",
            vehicle, status,
            v.badge_no or ""
        ])
    return send_file(io.BytesIO(buf.getvalue().encode("utf-8")), as_attachment=True,
                     download_name=f"digibookx_export_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv",
                     mimetype="text/csv")

@app.route("/export/pdf")
def export_pdf():
    auth_redirect = ensure_authenticated()
    if auth_redirect:
        return auth_redirect
    rows = _export_rows()
    if not canvas:
        flash("PDF engine not available. Install reportlab or use CSV export.", "error")
        return redirect(request.referrer or url_for("reports"))
    # simple landscape table
    from reportlab.lib.units import cm
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=landscape(A4))
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2*cm, 20*cm, "Digibook-X Visitors Export")
    c.setFont("Helvetica", 10)
    y = 19*cm
    headers = ["Check-in","Check-out","Visitor","ID Number","Office/Department","Purpose","Vehicle","Status"]
    c.drawString(2*cm, y, " | ".join(headers)); y -= 0.8*cm
    for v in rows[:200]:
        status = "Active" if not v.checkout_time else "Checked-out"
        vehicle = v.vehicle_reg or "-"
        if v.vehicle_reg and v.vehicle_type:
            vehicle = f"{v.vehicle_reg} ({v.vehicle_type})"
        checkout_display = v.checkout_time.strftime("%Y-%m-%d %H:%M") if v.checkout_time else "Active"
        line = f"{v.checkin_time:%Y-%m-%d %H:%M} | {checkout_display} | {v.full_name} | {v.national_id or v.id_number or ''} | {v.unit.name if v.unit else ''} | {v.purpose.name if v.purpose else ''} | {vehicle} | {status}"
        c.drawString(2*cm, y, line[:180])
        y -= 0.6*cm
        if y < 2*cm:
            c.showPage()
            y = 20*cm
            c.setFont("Helvetica", 10)
    c.showPage(); c.save()
    buf.seek(0)
    fname = f"digibookx_export_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.pdf"
    return send_file(buf, as_attachment=True, download_name=fname, mimetype="application/pdf")

# ------ Settings ------
@app.route("/settings", methods=["GET","POST"])
@role_required("Admin")
def settings_page():
    if is_demo():
        flash("Demo Mode: Settings editing is disabled.", "warning")
        s = Setting.query.first()
        return render_template("settings.html", s=s)
    
    s = Setting.query.first()
    if request.method == "POST":
        if not s:
            s = Setting(); db.session.add(s)
        s.site_name = request.form.get("site_name") or None
        s.badge_footer = request.form.get("badge_footer") or None
        f = request.files.get("logo")
        if f and f.filename:
            name = secure_filename(f"logo_{uuid.uuid4().hex}_{f.filename}")
            f.save(UPLOAD_DIR / name)
            s.site_logo_path = name
        db.session.commit()
        flash("Saved.", "ok")
        return redirect(url_for("settings_page"))
    return render_template("settings.html", s=s)

# -------------------- Main --------------------

# Run startup seed safely (works on Flask 3.0.x and 3.1+)
try:
    with app.app_context():
        ensure_schema()
        ensure_seed()
except Exception:
    pass

if __name__ == "__main__":
    with app.app_context():
        ensure_schema()
        ensure_seed()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
