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
app.config["DEBUG"] = False
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB uploads

db = DemoSQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"

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
    id_number = db.Column(db.String(60))
    vehicle_reg = db.Column(db.String(40))
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

def ensure_seed():
    db.create_all()
    if not Setting.query.first():
        s = Setting(site_name="Digibook-X", badge_footer="Please return badge at exit.")
        db.session.add(s)
    if not Purpose.query.first():
        for name in ["Delivery","Interview","Maintenance","Meeting","Guest/Family","Vendor"]:
            db.session.add(Purpose(name=name))
    if not Unit.query.first():
        units = [
            ("Gate","Dept","Main entrance"),
            ("Block A","House",""),
            ("Block B","House",""),
            ("Management Office","Office","Admin wing"),
            ("Shops Wing","Shop","Ground floor"),
        ]
        for n,t,l in units:
            db.session.add(Unit(name=n, type=t, location=l))
    if not Host.query.first():
        # Link to existing units by name
        mgmt = Unit.query.filter_by(name="Management Office").first()
        gate = Unit.query.filter_by(name="Gate").first()
        blocka = Unit.query.filter_by(name="Block A").first()
        ict = Unit(name="ICT Office", type="Dept", location="Admin wing"); db.session.add(ict); db.session.flush()
        hosts = [
            ("Estate Manager", None, None, mgmt.id if mgmt else None),
            ("Security Office", None, None, gate.id if gate else None),
            ("Block A – Apt 2C", None, None, blocka.id if blocka else None),
            ("ICT Office", None, None, ict.id),
            ("Shop 12 – Vendor", None, None, Unit.query.filter_by(name="Shops Wing").first().id if Unit.query.filter_by(name="Shops Wing").first() else None),
        ]
        for n,ph,em,uid in hosts:
            db.session.add(Host(name=n, phone=ph, email=em, unit_id=uid, active=True))
    if not User.query.first():
        admin = User(name="Admin", username="admin", role="Admin", active=True); admin.set_password("admin123")
        guard = User(name="Guard", username="guard", role="Guard", active=True); guard.set_password("guard123")
        db.session.add_all([admin, guard])
    if not Visitor.query.first():
        import random
        purposes = Purpose.query.all()
        units = Unit.query.all()
        hosts = Host.query.all()
        names = ["John Doe","Mary W.","Ali K.","Beatrice N.","Kevin O.","Ruth K.","James M.","Zara S.","Peter P.","Nadia T."]
        for i, nm in enumerate(names, start=1):
            p = random.choice(purposes) if purposes else None
            u = random.choice(units) if units else None
            h = random.choice(hosts) if hosts else None
            chk = datetime.utcnow() - timedelta(hours=random.randint(1,72))
            out = None if i % 3 == 0 else chk + timedelta(hours=random.randint(1,6))
            v = Visitor(
                full_name=nm, phone="07%08d"%random.randint(1000000,9999999),
                id_number=str(20000000+random.randint(0,999999)),
                vehicle_reg="K%s %s"%(random.choice(list("BCDFGHJK")),"%03d"%random.randint(1,999)),
                host_id=h.id if h else None, unit_id=u.id if u else None, purpose_id=p.id if p else None,
                badge_no=gen_badge_no(chk), checkin_time=chk, checkout_time=out
            )
            db.session.add(v)
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
def dashboard():
    # Auto-login demo user if in demo mode
    if is_demo() and not current_user.is_authenticated:
        demo_user = User.query.filter_by(username="admin").first()
        if demo_user:
            login_user(demo_user)
    elif not is_demo() and not current_user.is_authenticated:
        return redirect(url_for("login"))
    
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
    today = datetime.utcnow().date()
    start_month = today.replace(day=1)
    today_count = Visitor.query.filter(Visitor.checkin_time >= datetime.combine(today, datetime.min.time())).count()
    inside_count = Visitor.query.filter(Visitor.checkout_time.is_(None)).count()
    month_count = Visitor.query.filter(Visitor.checkin_time >= datetime.combine(start_month, datetime.min.time())).count()
    recent = Visitor.query.order_by(Visitor.checkin_time.desc()).limit(10).all()
    return render_template("dashboard.html", tiles={"today": today_count, "inside": inside_count, "month": month_count}, recent=recent)

# ------ Auth ------
@app.route("/login", methods=["GET","POST"])
def login():
    # Auto-login in demo mode
    if is_demo():
        demo_user = User.query.filter_by(username="admin").first()
        if demo_user and not current_user.is_authenticated:
            login_user(demo_user)
        return redirect(url_for("dashboard"))
    
    if request.method == "POST":
        u = User.query.filter_by(username=request.form["username"]).first()
        if not u or not u.active or not u.check_password(request.form["password"]):
            flash("Invalid credentials or inactive user.", "error")
            return redirect(url_for("login"))
        login_user(u)
        log("login", "User", u.id)
        return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    log("logout", "User", current_user.id)
    logout_user()
    return redirect(url_for("login"))

# ------ Visitors ------
from sqlalchemy import and_

def apply_filters(q):
    # shared filter parser for list & reports
    q_from = request.args.get("from") or request.args.get("q_from")
    q_to = request.args.get("to") or request.args.get("q_to")
    q_status = request.args.get("status") or request.args.get("q_status")
    q_host_id = request.args.get("host_id") or request.args.get("q_host_id")
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
    if q_host_id:
        q = q.filter(Visitor.host_id == int(q_host_id))
    if q_unit_id:
        q = q.filter(Visitor.unit_id == int(q_unit_id))
    if q_purpose_id:
        q = q.filter(Visitor.purpose_id == int(q_purpose_id))
    return q

@app.route("/visitors")
def visitors_list():
    if is_demo() and not current_user.is_authenticated:
        return redirect(url_for("login"))
    if not is_demo():
        if not current_user.is_authenticated:
            return redirect(url_for("login"))
    q = Visitor.query.order_by(Visitor.checkin_time.desc())
    q = apply_filters(q)
    items = q.limit(200).all()
    hosts = Host.query.order_by(Host.name).all()
    units = Unit.query.order_by(Unit.name).all()
    purposes = Purpose.query.order_by(Purpose.name).all()
    return render_template("visitors_list.html", items=items,
                           hosts=hosts, units=units, purposes=purposes,
                           q_from=request.args.get("from"), q_to=request.args.get("to"),
                           q_status=request.args.get("status"),
                           q_host_id=request.args.get("host_id"), q_unit_id=request.args.get("unit_id"),
                           q_purpose_id=request.args.get("purpose_id"))

@app.route("/visitors/new", methods=["GET","POST"])
def visitor_new():
    if is_demo() and not current_user.is_authenticated:
        return redirect(url_for("login"))
    if not is_demo() and not current_user.is_authenticated:
        return redirect(url_for("login"))
    hosts = Host.query.filter_by(active=True).order_by(Host.name).all()
    units = Unit.query.order_by(Unit.name).all()
    purposes = Purpose.query.order_by(Purpose.name).all()
    if request.method == "POST":
        if is_demo():
            flash("Demo Mode: Visitor check-in simulated. Changes will not be saved.", "info")
            return redirect(request.referrer or url_for("visitors_list"))
        
        v = Visitor(
            full_name=request.form["full_name"].strip(),
            phone=request.form["phone"].strip(),
            id_number=request.form.get("id_number") or None,
            vehicle_reg=request.form.get("vehicle_reg") or None,
            host_id=int(request.form["host_id"]) if request.form.get("host_id") else None,
            unit_id=int(request.form["unit_id"]) if request.form.get("unit_id") else None,
            purpose_id=int(request.form["purpose_id"]) if request.form.get("purpose_id") else None,
            notes=request.form.get("notes") or None,
            badge_no=gen_badge_no()
        )
        # photo upload
        f = request.files.get("photo")
        if f and f.filename:
            name = secure_filename(f"{uuid.uuid4().hex}_{f.filename}")
            f.save(UPLOAD_DIR / name)
            v.photo_path = name
        db.session.add(v); db.session.commit()
        log("create", "Visitor", v.id)
        flash(f"Visitor checked-in. <a href='{url_for('visitor_badge', id=v.id)}'>Print badge</a>", "ok")
        return redirect(url_for("visitors_list"))
    return render_template("visitor_new.html", hosts=hosts, units=units, purposes=purposes)

@app.route("/visitors/<int:id>")
def visitor_detail(id):
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
    v = Visitor.query.get_or_404(id)
    setting = Setting.query.first()
    return render_template("visitor_detail.html", v=v, setting=setting)

@app.route("/visitors/<int:id>/edit", methods=["GET","POST"])
def visitor_edit(id):
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
    v = Visitor.query.get_or_404(id)
    hosts = Host.query.filter_by(active=True).order_by(Host.name).all()
    units = Unit.query.order_by(Unit.name).all()
    purposes = Purpose.query.order_by(Purpose.name).all()
    if request.method == "POST":
        if is_demo():
            flash("Demo Mode: Changes will not be saved.", "info")
            return redirect(request.referrer or url_for("visitors_list"))
        
        v.full_name = request.form["full_name"].strip()
        v.phone = request.form["phone"].strip()
        v.id_number = request.form.get("id_number") or None
        v.vehicle_reg = request.form.get("vehicle_reg") or None
        v.host_id = int(request.form["host_id"]) if request.form.get("host_id") else None
        v.unit_id = int(request.form["unit_id"]) if request.form.get("unit_id") else None
        v.purpose_id = int(request.form["purpose_id"]) if request.form.get("purpose_id") else None
        v.notes = request.form.get("notes") or None
        f = request.files.get("photo")
        if f and f.filename:
            name = secure_filename(f"{uuid.uuid4().hex}_{f.filename}")
            f.save(UPLOAD_DIR / name)
            v.photo_path = name
        db.session.commit()
        log("update", "Visitor", v.id)
        flash("Saved.", "ok")
        return redirect(url_for("visitor_edit", id=v.id))
    return render_template("visitor_edit.html", v=v, hosts=hosts, units=units, purposes=purposes)

@app.route("/visitors/<int:id>/checkout", methods=["POST"])
def visitor_checkout(id):
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
    
    v = Visitor.query.get_or_404(id)
    if is_demo():
        flash("Demo Mode: Checkout simulated. Changes will not be saved.", "info")
        return redirect(url_for("visitor_edit", id=id))
    
    if not v.checkout_time:
        v.checkout_time = datetime.utcnow()
        db.session.commit()
        log("checkout", "Visitor", v.id)
        flash("Visitor checked-out.", "ok")
    return redirect(url_for("visitor_edit", id=id))

@app.route("/visitors/<int:id>/delete", methods=["POST"])
def visitor_delete(id):
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
    
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
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
    v = Visitor.query.get_or_404(id)
    setting = Setting.query.first() or Setting(site_name="Site")
    return render_template("visitor_badge.html", v=v, setting=setting)

# ------ Hosts CRUD ------
@app.route("/hosts")
def hosts_list():
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
    items = Host.query.order_by(Host.name).all()
    return render_template("hosts_list.html", items=items)

@app.route("/hosts/new", methods=["GET","POST"])
@role_required("Admin")
def host_new():
    units = Unit.query.order_by(Unit.name).all()
    if request.method == "POST":
        if is_demo():
            flash("Demo Mode: Host creation simulated. Changes will not be saved.", "info")
            return redirect(url_for("hosts_list"))
        
        h = Host(
            name=request.form["name"].strip(),
            phone=request.form.get("phone") or None,
            email=request.form.get("email") or None,
            unit_id=int(request.form["unit_id"]) if request.form.get("unit_id") else None,
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
        
        h.name = request.form["name"].strip()
        h.phone = request.form.get("phone") or None
        h.email = request.form.get("email") or None
        h.unit_id = int(request.form["unit_id"]) if request.form.get("unit_id") else None
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
def units_list():
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
    items = Unit.query.order_by(Unit.name).all()
    return render_template("units_list.html", items=items)

@app.route("/units/new", methods=["GET","POST"])
@role_required("Admin")
def unit_new():
    if request.method == "POST":
        if is_demo():
            flash("Demo Mode: Unit creation simulated. Changes will not be saved.", "info")
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
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
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
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
    q = Visitor.query.order_by(Visitor.checkin_time.desc())
    q = apply_filters(q)
    items = q.limit(500).all()
    hosts = Host.query.order_by(Host.name).all()
    units = Unit.query.order_by(Unit.name).all()
    purposes = Purpose.query.order_by(Purpose.name).all()
    return render_template("reports.html", items=items,
                           hosts=hosts, units=units, purposes=purposes,
                           q_from=request.args.get("from"), q_to=request.args.get("to"),
                           q_status=request.args.get("status"),
                           q_host_id=request.args.get("host_id"), q_unit_id=request.args.get("unit_id"),
                           q_purpose_id=request.args.get("purpose_id"))

def _export_rows():
    q = apply_filters(Visitor.query.order_by(Visitor.checkin_time.desc()))
    return q.all()

@app.route("/export/csv")
def export_csv():
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
    rows = _export_rows()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["checkin_time","checkout_time","full_name","phone","id_number","vehicle_reg","host","unit","purpose","badge_no"])
    for v in rows:
        w.writerow([
            v.checkin_time.strftime("%Y-%m-%d %H:%M"),
            v.checkout_time.strftime("%Y-%m-%d %H:%M") if v.checkout_time else "",
            v.full_name, v.phone, v.id_number or "", v.vehicle_reg or "",
            v.host.name if v.host else "", v.unit.name if v.unit else "", v.purpose.name if v.purpose else "",
            v.badge_no or ""
        ])
    return send_file(io.BytesIO(buf.getvalue().encode("utf-8")), as_attachment=True,
                     download_name=f"digibookx_export_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv",
                     mimetype="text/csv")

@app.route("/export/pdf")
def export_pdf():
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
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
    headers = ["Date","Name","Host","Unit","Purpose","Status","Badge"]
    c.drawString(2*cm, y, " | ".join(headers)); y -= 0.8*cm
    for v in rows[:200]:
        status = "Inside" if not v.checkout_time else "Checked-out"
        line = f"{v.checkin_time:%Y-%m-%d %H:%M} | {v.full_name} | {v.host.name if v.host else ''} | {v.unit.name if v.unit else ''} | {v.purpose.name if v.purpose else ''} | {status} | {v.badge_no}"
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
        ensure_seed()
except Exception:
    pass

if __name__ == "__main__":
    with app.app_context():
        ensure_seed()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
