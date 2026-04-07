from app import (
    HOSPITAL_LOCATIONS,
    HOSPITAL_PURPOSES,
    HOSPITAL_STAFF,
    Host,
    Purpose,
    Unit,
    Visitor,
    app,
    build_hospital_demo_visitors,
    db,
)


def upsert_locations():
    for name, unit_type, location in HOSPITAL_LOCATIONS:
        existing = Unit.query.filter_by(name=name).first()
        if existing:
            existing.type = unit_type
            existing.location = location
        else:
            db.session.add(Unit(name=name, type=unit_type, location=location))
    db.session.flush()


def upsert_purposes():
    for name in HOSPITAL_PURPOSES:
        if not Purpose.query.filter_by(name=name).first():
            db.session.add(Purpose(name=name))
    db.session.flush()


def upsert_staff():
    units_by_name = {u.name: u for u in Unit.query.all()}
    for name, phone, email, location_name in HOSPITAL_STAFF:
        existing = Host.query.filter_by(name=name).first()
        location = units_by_name.get(location_name)
        if not location:
            continue
        if existing:
            existing.phone = phone
            existing.email = email
            existing.unit_id = location.id
            existing.active = True
        else:
            db.session.add(Host(name=name, phone=phone, email=email, unit_id=location.id, active=True))
    db.session.flush()


def seed_visitors():
    units_by_name = {u.name: u for u in Unit.query.all()}
    hosts_by_name = {h.name: h for h in Host.query.all()}
    purposes_by_name = {p.name: p for p in Purpose.query.all()}

    for visitor in build_hospital_demo_visitors(units_by_name, hosts_by_name, purposes_by_name):
        exists = Visitor.query.filter_by(full_name=visitor.full_name, checkin_time=visitor.checkin_time).first()
        if exists:
            exists.phone = visitor.phone
            exists.id_number = visitor.id_number
            exists.vehicle_reg = visitor.vehicle_reg
            exists.vehicle_type = visitor.vehicle_type
            exists.host_id = visitor.host_id
            exists.unit_id = visitor.unit_id
            exists.purpose_id = visitor.purpose_id
            exists.checkout_time = visitor.checkout_time
            exists.notes = visitor.notes
            exists.badge_no = visitor.badge_no
        else:
            db.session.add(visitor)


def main():
    with app.app_context():
        upsert_locations()
        upsert_purposes()
        upsert_staff()
        seed_visitors()
        db.session.commit()
        print("Hospital demo data seeded successfully.")


if __name__ == "__main__":
    main()
