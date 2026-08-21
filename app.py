import os
import sqlite3
import json
import uuid
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, abort, send_from_directory
)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'soficomp-secret-2024-change-in-prod')

# Custom Jinja2 filters
@app.template_filter('from_json')
def from_json_filter(value):
    try:
        return json.loads(value) if value else {}
    except Exception:
        return {}
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # 32MB max upload
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'mp4', 'webm'}

DB_PATH = os.path.join(os.path.dirname(__file__), 'soficomp.db')


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.executescript('''
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            email TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            content TEXT,
            meta_description TEXT,
            is_published INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS menus (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            location TEXT UNIQUE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS menu_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_id INTEGER REFERENCES menus(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            url TEXT,
            page_id INTEGER REFERENCES pages(id) ON DELETE SET NULL,
            sort_order INTEGER DEFAULT 0,
            parent_id INTEGER REFERENCES menu_items(id) ON DELETE CASCADE,
            target TEXT DEFAULT '_self'
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS issue_statuses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            color TEXT NOT NULL DEFAULT '#888888',
            is_default INTEGER DEFAULT 0,
            sort_order INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT UNIQUE NOT NULL,
            company_name TEXT NOT NULL,
            location TEXT NOT NULL,
            machine_id TEXT,
            machine_type TEXT,
            description TEXT NOT NULL,
            contact_name TEXT,
            contact_email TEXT,
            contact_phone TEXT,
            status_id INTEGER REFERENCES issue_statuses(id),
            admin_notes TEXT,
            priority TEXT DEFAULT 'normal',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS issue_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id INTEGER REFERENCES issues(id) ON DELETE CASCADE,
            action TEXT NOT NULL,
            note TEXT,
            admin_id INTEGER REFERENCES admins(id),
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS config_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            icon TEXT DEFAULT 'box',
            sort_order INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS config_options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER REFERENCES config_categories(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            description TEXT,
            price_base REAL DEFAULT 0,
            price_add REAL DEFAULT 0,
            image TEXT,
            specs TEXT,
            is_default INTEGER DEFAULT 0,
            sort_order INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS partner_logos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            image TEXT NOT NULL,
            url TEXT,
            section TEXT DEFAULT 'footer',
            sort_order INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            original_name TEXT,
            mime_type TEXT,
            size INTEGER,
            uploaded_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS issue_photo_statuses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            color TEXT DEFAULT '#888888',
            sort_order INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS issue_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id INTEGER REFERENCES issues(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            description TEXT,
            photo_status_id INTEGER REFERENCES issue_photo_statuses(id) ON DELETE SET NULL,
            custom_status TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL,
            contact_name TEXT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            address TEXT,
            notes TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS customer_machines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER REFERENCES customers(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            serial_number TEXT,
            machine_type TEXT DEFAULT 'master',
            is_cooled INTEGER DEFAULT 0,
            connected_to INTEGER REFERENCES customer_machines(id) ON DELETE SET NULL,
            location TEXT,
            shelf_config TEXT DEFAULT '[]',
            status TEXT DEFAULT 'active',
            notes TEXT,
            installed_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS config_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER REFERENCES customers(id) ON DELETE CASCADE,
            machine_id INTEGER REFERENCES customer_machines(id) ON DELETE SET NULL,
            title TEXT,
            config_data TEXT DEFAULT '{}',
            description TEXT,
            status TEXT DEFAULT 'pending',
            admin_note TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
    ''')

    # Seed default data if empty
    admin_exists = c.execute('SELECT id FROM admins LIMIT 1').fetchone()
    if not admin_exists:
        c.execute(
            'INSERT INTO admins (username, password_hash, email) VALUES (?,?,?)',
            ('admin', generate_password_hash('admin123'), 'info@soficomp.eu')
        )

    if not c.execute('SELECT id FROM issue_statuses LIMIT 1').fetchone():
        statuses = [
            ('Nova prijava', '#3b82f6', 1, 0),
            ('V obravnavi', '#f59e0b', 0, 1),
            ('Na terenu', '#8b5cf6', 0, 2),
            ('Čakanje na del', '#f97316', 0, 3),
            ('Rešeno', '#22c55e', 0, 4),
            ('Zaprto', '#6b7280', 0, 5),
        ]
        c.executemany(
            'INSERT INTO issue_statuses (name, color, is_default, sort_order) VALUES (?,?,?,?)',
            statuses
        )

    if not c.execute('SELECT id FROM issue_photo_statuses LIMIT 1').fetchone():
        photo_statuses = [
            ('Slika težave', '#ef4444', 0),
            ('Pred popravilom', '#f59e0b', 1),
            ('Po popravilu', '#22c55e', 2),
            ('Poškodovani del', '#8b5cf6', 3),
            ('Novi del', '#3b82f6', 4),
            ('Naprava v celoti', '#6b7280', 5),
        ]
        c.executemany('INSERT INTO issue_photo_statuses (name, color, sort_order) VALUES (?,?,?)', photo_statuses)

    if not c.execute('SELECT id FROM menus LIMIT 1').fetchone():
        c.execute("INSERT INTO menus (name, location) VALUES ('Glavna navigacija', 'header')")
        c.execute("INSERT INTO menus (name, location) VALUES ('Noga strani', 'footer')")
        header_id = c.lastrowid - 1
        footer_id = c.lastrowid
        header_id = c.execute("SELECT id FROM menus WHERE location='header'").fetchone()['id']
        footer_id = c.execute("SELECT id FROM menus WHERE location='footer'").fetchone()['id']
        nav_items = [
            (header_id, 'O nas', '/o-nas', None, 0),
            (header_id, 'Produkti', '/produkti', None, 1),
            (header_id, 'Konfigurator', '/konfigurator', None, 2),
            (header_id, 'Servis', '/servis', None, 3),
            (header_id, 'Kontakt', '/kontakt', None, 4),
            (footer_id, 'Prijava težave', '/prijava-tezave', None, 0),
            (footer_id, 'Zasebnost', '/zasebnost', None, 1),
        ]
        c.executemany(
            'INSERT INTO menu_items (menu_id, label, url, page_id, sort_order) VALUES (?,?,?,?,?)',
            nav_items
        )

    default_settings = {
        'site_name': 'SOFIComp',
        'site_tagline': 'Avtomati prihodnosti',
        'site_logo': '',
        'footer_text': '© 2024 SOFIComp d.o.o. Vse pravice pridržane.',
        'footer_address': 'Slovenija',
        'footer_phone': '+386 1 234 5678',
        'footer_email': 'info@soficomp.eu',
        'social_facebook': '',
        'social_instagram': '',
        'social_linkedin': '',
        'hero_title': 'Avtomati prihodnosti.',
        'hero_subtitle': 'Pametne rešitve za samopostrežno prodajo. Zanesljivo, moderno, connected.',
        'hero_cta': 'Konfiguriraj avtomat',
        'contact_address': 'Slovenija',
        'contact_phone': '+386 1 234 5678',
        'contact_email': 'info@soficomp.eu',
        'google_maps_embed': '',
        'primary_color': '#3b82f6',
    }
    for key, value in default_settings.items():
        c.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)', (key, value))

    if not c.execute('SELECT id FROM config_categories LIMIT 1').fetchone():
        cats = [
            ('Tip avtomata', 'cpu', 0),
            ('Kapaciteta', 'layers', 1),
            ('Plačilni sistem', 'credit-card', 2),
            ('Povezljivost', 'wifi', 3),
            ('Monitoring', 'activity', 4),
            ('Branding', 'image', 5),
        ]
        c.executemany('INSERT INTO config_categories (name, icon, sort_order) VALUES (?,?,?)', cats)
        cat_ids = {r['name']: r['id'] for r in c.execute('SELECT id, name FROM config_categories').fetchall()}

        options = [
            # Tip avtomata
            (cat_ids['Tip avtomata'], 'Kava & topli napitki', 'Espresso, cappuccino, čaj in še več.', 2490, 0, None, '{"Napitki": "8-12 vrst", "Skodelice/dan": "do 300"}', 1, 0),
            (cat_ids['Tip avtomata'], 'Prigrizki & hrana', 'Čips, čokolada, sendviči, sadni sok.', 2190, 0, None, '{"Sloti": "40+", "Hlajenje": "opcijsko"}', 0, 1),
            (cat_ids['Tip avtomata'], 'Kombiniran avtomat', 'Kava + prigrizki v enem aparatu.', 3290, 0, None, '{"Napitki": "8 vrst", "Prigrizki": "30 slotov"}', 0, 2),
            (cat_ids['Tip avtomata'], 'Hladne pijače', 'Voda, sokovi, energijske pijače.', 1890, 0, None, '{"Kapaciteta": "do 200 stekleničev", "Temp": "+4°C"}', 0, 3),
            # Kapaciteta
            (cat_ids['Kapaciteta'], 'Standard (XLT-10)', 'Idealen za pisarne do 50 oseb.', 0, 0, None, '{"Kapaciteta": "10 vrst", "Dimenzije": "60×80×180 cm"}', 1, 0),
            (cat_ids['Kapaciteta'], 'Veliki (XLT-22)', 'Za srednje in večje lokacije.', 0, 590, None, '{"Kapaciteta": "22 vrst", "Dimenzije": "80×85×185 cm"}', 0, 1),
            (cat_ids['Kapaciteta'], 'Jumbo (XXL0-5)', 'Maximalna kapaciteta za prometna mesta.', 0, 1190, None, '{"Kapaciteta": "50+ slotov", "Dimenzije": "120×90×185 cm"}', 0, 2),
            # Plačilni sistem
            (cat_ids['Plačilni sistem'], 'Gotovina', 'Sprejem bankovcev in kovancev.', 0, 0, None, '{"Kovanci": "0.10–2€", "Bankovci": "5–50€"}', 1, 0),
            (cat_ids['Plačilni sistem'], 'Kartice', 'Bančne kartice (Visa, MC).', 0, 290, None, '{"Standard": "Chip + PIN", "Certifikat": "PCI-DSS"}', 0, 1),
            (cat_ids['Plačilni sistem'], 'Brezstično / NFC', 'Apple Pay, Google Pay, kartice.', 0, 390, None, '{"Protokol": "NFC/RFID", "Čas transakcije": "<2s"}', 0, 2),
            (cat_ids['Plačilni sistem'], 'Vse skupaj', 'Gotovina + kartice + brezstično.', 0, 590, None, '{"Vse": "vključeno", "Priporoča se": "✓"}', 0, 3),
            # Povezljivost
            (cat_ids['Povezljivost'], 'Brez (offline)', 'Lokalno delovanje brez interneta.', 0, 0, None, '{}', 1, 0),
            (cat_ids['Povezljivost'], 'Wi-Fi', 'Priključitev na obstoječe omrežje.', 0, 150, None, '{"Standard": "802.11 b/g/n"}', 0, 1),
            (cat_ids['Povezljivost'], '4G / LTE', 'Vgrajeni SIM, deluje povsod.', 0, 290, None, '{"Podatki": "vključeni 2 leti", "Pokritost": "SI+EU"}', 0, 2),
            # Monitoring
            (cat_ids['Monitoring'], 'Brez monitoringa', 'Ročno upravljanje in polnjenje.', 0, 0, None, '{}', 1, 0),
            (cat_ids['Monitoring'], 'Osnovno opozarjanje', 'SMS/email ob napakah in praznih zalogah.', 0, 190, None, '{"Opozorila": "e-mail + SMS"}', 0, 1),
            (cat_ids['Monitoring'], 'Premium telemetrija', 'Živi dashboard, statistika, napovedovanje.', 0, 490, None, '{"Dashboard": "24/7", "Poročila": "tedensko"}', 0, 2),
            # Branding
            (cat_ids['Branding'], 'Standardni izgled', 'Privzeti izgled SOFIComp.', 0, 0, None, '{}', 1, 0),
            (cat_ids['Branding'], 'Logo nalepka', 'Vaš logotip na aparatu.', 0, 190, None, '{"Material": "vinyl nalepka"}', 0, 1),
            (cat_ids['Branding'], 'Celoten custom wrap', 'Popolnoma prilagojen dizajn (folija).', 0, 590, None, '{"Material": "premium folija", "Garancija": "3 leta"}', 0, 2),
        ]
        c.executemany(
            'INSERT INTO config_options (category_id, name, description, price_base, price_add, image, specs, is_default, sort_order) VALUES (?,?,?,?,?,?,?,?,?)',
            options
        )

    conn.commit()
    conn.close()


def get_setting(key, default=''):
    conn = get_db()
    row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    conn.close()
    return row['value'] if row else default


def get_settings_dict():
    conn = get_db()
    rows = conn.execute('SELECT key, value FROM settings').fetchall()
    conn.close()
    return {r['key']: r['value'] for r in rows}


def get_nav_items(location='header'):
    conn = get_db()
    menu = conn.execute('SELECT id FROM menus WHERE location=?', (location,)).fetchone()
    if not menu:
        conn.close()
        return []
    items = conn.execute(
        'SELECT mi.*, p.slug as page_slug FROM menu_items mi LEFT JOIN pages p ON mi.page_id=p.id '
        'WHERE mi.menu_id=? AND mi.parent_id IS NULL ORDER BY mi.sort_order',
        (menu['id'],)
    ).fetchall()
    conn.close()
    return items


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'admin_id' not in session:
            return redirect(url_for('admin_login', next=request.url))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Context processors
# ---------------------------------------------------------------------------

@app.context_processor
def inject_globals():
    settings = get_settings_dict()
    nav_items = get_nav_items('header')
    footer_items = get_nav_items('footer')
    return dict(
        site=settings,
        nav_items=nav_items,
        footer_items=footer_items,
        current_year=datetime.now().year
    )


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    conn = get_db()
    # Get configurator categories count for homepage
    cats = conn.execute('SELECT * FROM config_categories ORDER BY sort_order').fetchall()
    conn.close()
    return render_template('index.html', cats=cats)


@app.route('/o-nas')
def about():
    return render_template('about.html')


@app.route('/produkti')
def products():
    conn = get_db()
    # Get machine type category options as products
    cat = conn.execute("SELECT id FROM config_categories WHERE name='Tip avtomata'").fetchone()
    products_list = []
    if cat:
        products_list = conn.execute(
            'SELECT * FROM config_options WHERE category_id=? ORDER BY sort_order',
            (cat['id'],)
        ).fetchall()
    conn.close()
    return render_template('products.html', products=products_list)


@app.route('/konfigurator')
def configurator():
    conn = get_db()
    cats = conn.execute('SELECT * FROM config_categories ORDER BY sort_order').fetchall()
    all_options = conn.execute(
        'SELECT * FROM config_options ORDER BY category_id, sort_order'
    ).fetchall()
    conn.close()
    options_by_cat = {}
    for opt in all_options:
        cid = opt['category_id']
        if cid not in options_by_cat:
            options_by_cat[cid] = []
        options_by_cat[cid].append(dict(opt))
    return render_template('configurator.html', cats=cats, options_by_cat=options_by_cat)


@app.route('/servis')
def service():
    return render_template('service.html')


@app.route('/kontakt')
def contact():
    return render_template('contact.html')


@app.route('/prijava-tezave', methods=['GET', 'POST'])
def report_issue():
    conn = get_db()
    if request.method == 'POST':
        ticket_id = 'TKT-' + str(uuid.uuid4())[:8].upper()
        default_status = conn.execute(
            'SELECT id FROM issue_statuses WHERE is_default=1 LIMIT 1'
        ).fetchone()
        status_id = default_status['id'] if default_status else None

        conn.execute('''
            INSERT INTO issues
            (ticket_id, company_name, location, machine_id, machine_type,
             description, contact_name, contact_email, contact_phone, status_id, priority)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            ticket_id,
            request.form.get('company_name', '').strip(),
            request.form.get('location', '').strip(),
            request.form.get('machine_id', '').strip(),
            request.form.get('machine_type', '').strip(),
            request.form.get('description', '').strip(),
            request.form.get('contact_name', '').strip(),
            request.form.get('contact_email', '').strip(),
            request.form.get('contact_phone', '').strip(),
            status_id,
            request.form.get('priority', 'normal'),
        ))
        conn.commit()
        conn.close()
        flash(f'Vaša prijava je bila uspešno oddana. Številka prijave: <strong>{ticket_id}</strong>', 'success')
        return redirect(url_for('report_issue'))

    machine_types = ['Kava & topli napitki', 'Prigrizki & hrana', 'Kombiniran avtomat', 'Hladne pijače', 'Drugo']
    conn.close()
    return render_template('prijava_tezave.html', machine_types=machine_types)


@app.route('/stran/<slug>')
def dynamic_page(slug):
    conn = get_db()
    page = conn.execute(
        'SELECT * FROM pages WHERE slug=? AND is_published=1', (slug,)
    ).fetchone()
    conn.close()
    if not page:
        abort(404)
    return render_template('page.html', page=page)


# ---------------------------------------------------------------------------
# Media upload (public endpoint for editor)
# ---------------------------------------------------------------------------

@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    f = request.files['file']
    if f.filename == '':
        return jsonify({'error': 'No filename'}), 400
    if not allowed_file(f.filename):
        return jsonify({'error': 'File type not allowed'}), 400

    ext = f.filename.rsplit('.', 1)[1].lower()
    unique_name = str(uuid.uuid4()) + '.' + ext
    save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
    f.save(save_path)

    conn = get_db()
    conn.execute(
        'INSERT INTO media (filename, original_name, mime_type, size) VALUES (?,?,?,?)',
        (unique_name, secure_filename(f.filename), f.content_type, os.path.getsize(save_path))
    )
    conn.commit()
    conn.close()

    url = url_for('static', filename=f'uploads/{unique_name}')
    return jsonify({'url': url, 'filename': unique_name})


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------

@app.route('/admin')
@login_required
def admin_dashboard():
    conn = get_db()
    stats = {
        'pages': conn.execute('SELECT COUNT(*) as c FROM pages').fetchone()['c'],
        'issues_open': conn.execute(
            "SELECT COUNT(*) as c FROM issues WHERE status_id IN "
            "(SELECT id FROM issue_statuses WHERE name != 'Rešeno' AND name != 'Zaprto')"
        ).fetchone()['c'],
        'issues_total': conn.execute('SELECT COUNT(*) as c FROM issues').fetchone()['c'],
        'issues_today': conn.execute(
            "SELECT COUNT(*) as c FROM issues WHERE date(created_at)=date('now')"
        ).fetchone()['c'],
    }
    recent_issues = conn.execute('''
        SELECT i.*, s.name as status_name, s.color as status_color
        FROM issues i LEFT JOIN issue_statuses s ON i.status_id=s.id
        ORDER BY i.created_at DESC LIMIT 8
    ''').fetchall()
    issues_by_status = conn.execute('''
        SELECT s.name, s.color, COUNT(i.id) as cnt
        FROM issue_statuses s
        LEFT JOIN issues i ON i.status_id=s.id
        GROUP BY s.id ORDER BY s.sort_order
    ''').fetchall()
    conn.close()
    return render_template('admin/dashboard.html',
                           stats=stats,
                           recent_issues=recent_issues,
                           issues_by_status=issues_by_status)


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if 'admin_id' in session:
        return redirect(url_for('admin_dashboard'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        conn = get_db()
        admin = conn.execute('SELECT * FROM admins WHERE username=?', (username,)).fetchone()
        conn.close()
        if admin and check_password_hash(admin['password_hash'], password):
            session['admin_id'] = admin['id']
            session['admin_username'] = admin['username']
            next_url = request.args.get('next')
            return redirect(next_url or url_for('admin_dashboard'))
        error = 'Napačno uporabniško ime ali geslo.'
    return render_template('admin/login.html', error=error)


@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))


# Pages management
@app.route('/admin/strani')
@login_required
def admin_pages():
    conn = get_db()
    pages = conn.execute('SELECT * FROM pages ORDER BY updated_at DESC').fetchall()
    conn.close()
    return render_template('admin/pages_list.html', pages=pages)


@app.route('/admin/strani/nova', methods=['GET', 'POST'])
@login_required
def admin_page_new():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        slug = request.form.get('slug', '').strip().lower().replace(' ', '-')
        content = request.form.get('content', '')
        meta = request.form.get('meta_description', '')
        published = 1 if request.form.get('is_published') else 0
        now = datetime.now().isoformat()

        conn = get_db()
        try:
            conn.execute(
                'INSERT INTO pages (title, slug, content, meta_description, is_published, created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
                (title, slug, content, meta, published, now, now)
            )
            conn.commit()
            flash('Stran uspešno ustvarjena.', 'success')
            return redirect(url_for('admin_pages'))
        except sqlite3.IntegrityError:
            flash('Slug že obstaja. Izberite drugačen URL.', 'error')
        finally:
            conn.close()

    return render_template('admin/page_edit.html', page=None)


@app.route('/admin/strani/<int:page_id>', methods=['GET', 'POST'])
@login_required
def admin_page_edit(page_id):
    conn = get_db()
    page = conn.execute('SELECT * FROM pages WHERE id=?', (page_id,)).fetchone()
    if not page:
        conn.close()
        abort(404)

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        slug = request.form.get('slug', '').strip().lower().replace(' ', '-')
        content = request.form.get('content', '')
        meta = request.form.get('meta_description', '')
        published = 1 if request.form.get('is_published') else 0
        now = datetime.now().isoformat()
        try:
            conn.execute(
                'UPDATE pages SET title=?, slug=?, content=?, meta_description=?, is_published=?, updated_at=? WHERE id=?',
                (title, slug, content, meta, published, now, page_id)
            )
            conn.commit()
            flash('Stran uspešno shranjena.', 'success')
            return redirect(url_for('admin_pages'))
        except sqlite3.IntegrityError:
            flash('Slug že obstaja.', 'error')
        finally:
            conn.close()
        return redirect(url_for('admin_page_edit', page_id=page_id))

    conn.close()
    return render_template('admin/page_edit.html', page=page)


@app.route('/admin/strani/<int:page_id>/delete', methods=['POST'])
@login_required
def admin_page_delete(page_id):
    conn = get_db()
    conn.execute('DELETE FROM pages WHERE id=?', (page_id,))
    conn.commit()
    conn.close()
    flash('Stran izbrisana.', 'info')
    return redirect(url_for('admin_pages'))


# Menu management
@app.route('/admin/meniji')
@login_required
def admin_menus():
    conn = get_db()
    menus = conn.execute('SELECT * FROM menus').fetchall()
    menu_data = {}
    for menu in menus:
        items = conn.execute(
            'SELECT mi.*, p.title as page_title FROM menu_items mi '
            'LEFT JOIN pages p ON mi.page_id=p.id '
            'WHERE mi.menu_id=? ORDER BY mi.sort_order',
            (menu['id'],)
        ).fetchall()
        menu_data[menu['id']] = {'menu': dict(menu), 'menu_items': [dict(i) for i in items]}
    pages = conn.execute('SELECT id, title, slug FROM pages WHERE is_published=1 ORDER BY title').fetchall()
    conn.close()
    return render_template('admin/menus.html', menu_data=menu_data, pages=pages)


@app.route('/admin/meniji/item/add', methods=['POST'])
@login_required
def admin_menu_item_add():
    data = request.json or {}
    conn = get_db()
    max_order = conn.execute(
        'SELECT COALESCE(MAX(sort_order),0) as m FROM menu_items WHERE menu_id=?',
        (data.get('menu_id'),)
    ).fetchone()['m']
    conn.execute(
        'INSERT INTO menu_items (menu_id, label, url, page_id, sort_order, target) VALUES (?,?,?,?,?,?)',
        (data.get('menu_id'), data.get('label'), data.get('url'), data.get('page_id') or None, max_order + 1, data.get('target', '_self'))
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/admin/meniji/item/<int:item_id>/delete', methods=['POST'])
@login_required
def admin_menu_item_delete(item_id):
    conn = get_db()
    conn.execute('DELETE FROM menu_items WHERE id=?', (item_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/admin/meniji/reorder', methods=['POST'])
@login_required
def admin_menu_reorder():
    data = request.json or {}
    # data = [{'id': 1, 'order': 0}, ...]
    conn = get_db()
    for item in data:
        conn.execute('UPDATE menu_items SET sort_order=? WHERE id=?', (item['order'], item['id']))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/admin/meniji/item/<int:item_id>/edit', methods=['POST'])
@login_required
def admin_menu_item_edit(item_id):
    data = request.json or {}
    conn = get_db()
    conn.execute(
        'UPDATE menu_items SET label=?, url=?, target=? WHERE id=?',
        (data.get('label'), data.get('url'), data.get('target', '_self'), item_id)
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


# Settings
@app.route('/admin/nastavitve', methods=['GET', 'POST'])
@login_required
def admin_settings():
    conn = get_db()
    if request.method == 'POST':
        form_type = request.form.get('form_type')

        if form_type == 'general':
            keys = ['site_name', 'site_tagline', 'footer_text', 'footer_address',
                    'footer_phone', 'footer_email', 'social_facebook', 'social_instagram',
                    'social_linkedin', 'contact_address', 'contact_phone', 'contact_email',
                    'google_maps_embed', 'hero_title', 'hero_subtitle', 'hero_cta', 'primary_color']
            for key in keys:
                if key in request.form:
                    conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)',
                                 (key, request.form.get(key, '')))

        elif form_type == 'logo':
            logo_file = request.files.get('site_logo')
            if logo_file and logo_file.filename and allowed_file(logo_file.filename):
                ext = logo_file.filename.rsplit('.', 1)[1].lower()
                unique_name = 'logo_' + str(uuid.uuid4())[:8] + '.' + ext
                logo_file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_name))
                conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)',
                             ('site_logo', unique_name))

        elif form_type == 'partner_logo':
            logo_file = request.files.get('partner_image')
            if logo_file and logo_file.filename and allowed_file(logo_file.filename):
                ext = logo_file.filename.rsplit('.', 1)[1].lower()
                unique_name = 'partner_' + str(uuid.uuid4())[:8] + '.' + ext
                logo_file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_name))
                max_order = conn.execute('SELECT COALESCE(MAX(sort_order),0) as m FROM partner_logos').fetchone()['m']
                conn.execute(
                    'INSERT INTO partner_logos (name, image, url, section, sort_order) VALUES (?,?,?,?,?)',
                    (request.form.get('partner_name', ''), unique_name,
                     request.form.get('partner_url', ''), request.form.get('partner_section', 'footer'),
                     max_order + 1)
                )

        elif form_type == 'delete_partner':
            logo_id = request.form.get('logo_id')
            conn.execute('DELETE FROM partner_logos WHERE id=?', (logo_id,))

        elif form_type == 'password':
            old_pw = request.form.get('old_password', '')
            new_pw = request.form.get('new_password', '')
            admin = conn.execute('SELECT * FROM admins WHERE id=?', (session['admin_id'],)).fetchone()
            if admin and check_password_hash(admin['password_hash'], old_pw):
                conn.execute('UPDATE admins SET password_hash=? WHERE id=?',
                             (generate_password_hash(new_pw), session['admin_id']))
                flash('Geslo uspešno spremenjeno.', 'success')
            else:
                flash('Staro geslo ni pravilno.', 'error')

        conn.commit()
        flash('Nastavitve shranjene.', 'success')
        conn.close()
        return redirect(url_for('admin_settings'))

    settings = get_settings_dict()
    partner_logos = conn.execute('SELECT * FROM partner_logos ORDER BY sort_order').fetchall()
    conn.close()
    return render_template('admin/settings.html', settings=settings, partner_logos=partner_logos)


# Issues management
@app.route('/admin/prijave')
@login_required
def admin_issues():
    conn = get_db()
    status_filter = request.args.get('status', '')
    priority_filter = request.args.get('priority', '')
    search = request.args.get('q', '')

    query = '''
        SELECT i.*, s.name as status_name, s.color as status_color
        FROM issues i LEFT JOIN issue_statuses s ON i.status_id=s.id
        WHERE 1=1
    '''
    params = []
    if status_filter:
        query += ' AND i.status_id=?'
        params.append(status_filter)
    if priority_filter:
        query += ' AND i.priority=?'
        params.append(priority_filter)
    if search:
        query += ' AND (i.company_name LIKE ? OR i.ticket_id LIKE ? OR i.location LIKE ?)'
        params += [f'%{search}%', f'%{search}%', f'%{search}%']
    query += ' ORDER BY i.created_at DESC'

    issues = conn.execute(query, params).fetchall()
    statuses = conn.execute('SELECT * FROM issue_statuses ORDER BY sort_order').fetchall()
    conn.close()
    return render_template('admin/issues.html',
                           issues=issues, statuses=statuses,
                           status_filter=status_filter, priority_filter=priority_filter,
                           search=search)


@app.route('/admin/prijave/<int:issue_id>', methods=['GET', 'POST'])
@login_required
def admin_issue_detail(issue_id):
    conn = get_db()
    issue = conn.execute('''
        SELECT i.*, s.name as status_name, s.color as status_color
        FROM issues i LEFT JOIN issue_statuses s ON i.status_id=s.id
        WHERE i.id=?
    ''', (issue_id,)).fetchone()
    if not issue:
        conn.close()
        abort(404)

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'update_status':
            new_status_id = request.form.get('status_id')
            note = request.form.get('note', '')
            old_status = issue['status_name'] or '—'
            new_status_row = conn.execute('SELECT name FROM issue_statuses WHERE id=?', (new_status_id,)).fetchone()
            new_status = new_status_row['name'] if new_status_row else '—'
            conn.execute('UPDATE issues SET status_id=?, updated_at=? WHERE id=?',
                         (new_status_id, datetime.now().isoformat(), issue_id))
            conn.execute('INSERT INTO issue_history (issue_id, action, note, admin_id) VALUES (?,?,?,?)',
                         (issue_id, f'Status: "{old_status}" → "{new_status}"', note, session['admin_id']))
        elif action == 'update_notes':
            notes = request.form.get('admin_notes', '')
            conn.execute('UPDATE issues SET admin_notes=?, updated_at=? WHERE id=?',
                         (notes, datetime.now().isoformat(), issue_id))
            conn.execute('INSERT INTO issue_history (issue_id, action, note, admin_id) VALUES (?,?,?,?)',
                         (issue_id, 'Opomba posodobljena', notes[:100], session['admin_id']))
        elif action == 'update_priority':
            priority = request.form.get('priority', 'normal')
            conn.execute('UPDATE issues SET priority=?, updated_at=? WHERE id=?',
                         (priority, datetime.now().isoformat(), issue_id))
        conn.commit()
        return redirect(url_for('admin_issue_detail', issue_id=issue_id))

    statuses = conn.execute('SELECT * FROM issue_statuses ORDER BY sort_order').fetchall()
    history = conn.execute('''
        SELECT h.*, a.username FROM issue_history h
        LEFT JOIN admins a ON h.admin_id=a.id
        WHERE h.issue_id=? ORDER BY h.created_at DESC
    ''', (issue_id,)).fetchall()
    photos = conn.execute('''
        SELECT p.*, ps.name as status_name, ps.color as status_color
        FROM issue_photos p LEFT JOIN issue_photo_statuses ps ON p.photo_status_id=ps.id
        WHERE p.issue_id=? ORDER BY p.created_at
    ''', (issue_id,)).fetchall()
    photo_statuses = conn.execute('SELECT * FROM issue_photo_statuses ORDER BY sort_order').fetchall()
    conn.close()
    return render_template('admin/issue_detail.html', issue=issue, statuses=statuses,
                           history=history, photos=photos, photo_statuses=photo_statuses)


@app.route('/admin/prijave/<int:issue_id>/delete', methods=['POST'])
@login_required
def admin_issue_delete(issue_id):
    conn = get_db()
    conn.execute('DELETE FROM issues WHERE id=?', (issue_id,))
    conn.commit()
    conn.close()
    flash('Prijava izbrisana.', 'info')
    return redirect(url_for('admin_issues'))


@app.route('/admin/prijave/export')
@login_required
def admin_issues_export():
    import csv
    import io
    conn = get_db()
    issues = conn.execute('''
        SELECT i.ticket_id, i.company_name, i.location, i.machine_type,
               i.contact_name, i.contact_email, i.contact_phone,
               i.description, i.priority, s.name as status, i.created_at
        FROM issues i LEFT JOIN issue_statuses s ON i.status_id=s.id
        ORDER BY i.created_at DESC
    ''').fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Ticket', 'Podjetje', 'Lokacija', 'Tip avtomata', 'Kontakt', 'Email', 'Telefon',
                     'Opis', 'Prioriteta', 'Status', 'Datum'])
    for row in issues:
        writer.writerow(list(row))

    from flask import Response
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=prijave.csv'}
    )


# Status management
@app.route('/admin/stanja', methods=['GET', 'POST'])
@login_required
def admin_statuses():
    conn = get_db()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            name = request.form.get('name', '').strip()
            color = request.form.get('color', '#888888')
            is_default = 1 if request.form.get('is_default') else 0
            if is_default:
                conn.execute('UPDATE issue_statuses SET is_default=0')
            max_order = conn.execute('SELECT COALESCE(MAX(sort_order),0) as m FROM issue_statuses').fetchone()['m']
            conn.execute('INSERT INTO issue_statuses (name, color, is_default, sort_order) VALUES (?,?,?,?)',
                         (name, color, is_default, max_order + 1))
            flash('Stanje dodano.', 'success')
        elif action == 'edit':
            sid = request.form.get('id')
            name = request.form.get('name', '').strip()
            color = request.form.get('color', '#888888')
            is_default = 1 if request.form.get('is_default') else 0
            if is_default:
                conn.execute('UPDATE issue_statuses SET is_default=0')
            conn.execute('UPDATE issue_statuses SET name=?, color=?, is_default=? WHERE id=?',
                         (name, color, is_default, sid))
            flash('Stanje posodobljeno.', 'success')
        elif action == 'delete':
            sid = request.form.get('id')
            conn.execute('DELETE FROM issue_statuses WHERE id=?', (sid,))
            flash('Stanje izbrisano.', 'info')
        elif action == 'reorder':
            order_data = json.loads(request.form.get('order', '[]'))
            for item in order_data:
                conn.execute('UPDATE issue_statuses SET sort_order=? WHERE id=?', (item['order'], item['id']))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_statuses'))

    statuses = conn.execute('SELECT * FROM issue_statuses ORDER BY sort_order').fetchall()
    conn.close()
    return render_template('admin/statuses.html', statuses=statuses)


# Configurator admin
@app.route('/admin/konfigurator')
@login_required
def admin_configurator():
    conn = get_db()
    cats = conn.execute('SELECT * FROM config_categories ORDER BY sort_order').fetchall()
    options = conn.execute(
        'SELECT o.*, c.name as cat_name FROM config_options o '
        'JOIN config_categories c ON o.category_id=c.id ORDER BY o.category_id, o.sort_order'
    ).fetchall()
    conn.close()
    return render_template('admin/configurator.html', cats=cats, options=options)


@app.route('/admin/konfigurator/option/add', methods=['POST'])
@login_required
def admin_config_option_add():
    image_filename = None
    if 'image' in request.files:
        f = request.files['image']
        if f.filename and allowed_file(f.filename):
            ext = f.filename.rsplit('.', 1)[1].lower()
            image_filename = 'config_' + str(uuid.uuid4())[:8] + '.' + ext
            f.save(os.path.join(app.config['UPLOAD_FOLDER'], image_filename))

    conn = get_db()
    max_order = conn.execute(
        'SELECT COALESCE(MAX(sort_order),0) as m FROM config_options WHERE category_id=?',
        (request.form.get('category_id'),)
    ).fetchone()['m']
    conn.execute(
        'INSERT INTO config_options (category_id, name, description, price_base, price_add, image, is_default, sort_order) VALUES (?,?,?,?,?,?,?,?)',
        (request.form.get('category_id'), request.form.get('name'), request.form.get('description', ''),
         float(request.form.get('price_base', 0)), float(request.form.get('price_add', 0)),
         image_filename, 1 if request.form.get('is_default') else 0, max_order + 1)
    )
    conn.commit()
    conn.close()
    flash('Možnost dodana.', 'success')
    return redirect(url_for('admin_configurator'))


@app.route('/admin/konfigurator/option/<int:opt_id>/delete', methods=['POST'])
@login_required
def admin_config_option_delete(opt_id):
    conn = get_db()
    conn.execute('DELETE FROM config_options WHERE id=?', (opt_id,))
    conn.commit()
    conn.close()
    flash('Možnost izbrisana.', 'info')
    return redirect(url_for('admin_configurator'))


# Media library
@app.route('/admin/mediji')
@login_required
def admin_media():
    conn = get_db()
    files = conn.execute('SELECT * FROM media ORDER BY uploaded_at DESC').fetchall()
    conn.close()
    return render_template('admin/media.html', files=files)


@app.route('/admin/mediji/<int:media_id>/delete', methods=['POST'])
@login_required
def admin_media_delete(media_id):
    conn = get_db()
    f = conn.execute('SELECT filename FROM media WHERE id=?', (media_id,)).fetchone()
    if f:
        try:
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], f['filename']))
        except FileNotFoundError:
            pass
        conn.execute('DELETE FROM media WHERE id=?', (media_id,))
        conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/admin/mediji/list')
@login_required
def admin_media_list():
    conn = get_db()
    files = conn.execute('SELECT * FROM media ORDER BY uploaded_at DESC LIMIT 100').fetchall()
    conn.close()
    result = []
    for f in files:
        result.append({
            'id': f['id'],
            'filename': f['filename'],
            'original_name': f['original_name'],
            'url': url_for('static', filename=f'uploads/{f["filename"]}'),
        })
    return jsonify(result)


# ---------------------------------------------------------------------------
# Issue: manual creation by admin
# ---------------------------------------------------------------------------

@app.route('/admin/prijave/nova', methods=['GET', 'POST'])
@login_required
def admin_issue_new():
    conn = get_db()
    if request.method == 'POST':
        ticket_id = 'TKT-' + str(uuid.uuid4())[:8].upper()
        default_status = conn.execute('SELECT id FROM issue_statuses WHERE is_default=1 LIMIT 1').fetchone()
        status_id = request.form.get('status_id') or (default_status['id'] if default_status else None)
        conn.execute('''
            INSERT INTO issues
            (ticket_id, company_name, location, machine_id, machine_type,
             description, contact_name, contact_email, contact_phone, status_id, priority, admin_notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            ticket_id,
            request.form.get('company_name', '').strip(),
            request.form.get('location', '').strip(),
            request.form.get('machine_id', '').strip(),
            request.form.get('machine_type', '').strip(),
            request.form.get('description', '').strip(),
            request.form.get('contact_name', '').strip(),
            request.form.get('contact_email', '').strip(),
            request.form.get('contact_phone', '').strip(),
            status_id,
            request.form.get('priority', 'normal'),
            request.form.get('admin_notes', '').strip(),
        ))
        issue_id = conn.execute('SELECT id FROM issues WHERE ticket_id=?', (ticket_id,)).fetchone()['id']
        conn.execute('INSERT INTO issue_history (issue_id, action, admin_id) VALUES (?,?,?)',
                     (issue_id, 'Prijava ustvarjena ročno (admin)', session['admin_id']))
        conn.commit()
        conn.close()
        flash(f'Prijava ustvarjena: <strong>{ticket_id}</strong>', 'success')
        return redirect(url_for('admin_issue_detail', issue_id=issue_id))

    statuses = conn.execute('SELECT * FROM issue_statuses ORDER BY sort_order').fetchall()
    customers = conn.execute('SELECT id, company_name, contact_name FROM customers ORDER BY company_name').fetchall()
    machines = conn.execute('SELECT cm.*, c.company_name FROM customer_machines cm JOIN customers c ON cm.customer_id=c.id ORDER BY c.company_name, cm.name').fetchall()
    conn.close()
    return render_template('admin/issue_new.html', statuses=statuses, customers=customers, machines=machines)


# ---------------------------------------------------------------------------
# Issue photo gallery
# ---------------------------------------------------------------------------

@app.route('/admin/prijave/<int:issue_id>/foto/dodaj', methods=['POST'])
@login_required
def admin_issue_photo_add(issue_id):
    if 'photo' not in request.files:
        return jsonify({'error': 'No file'}), 400
    f = request.files['photo']
    if not f.filename or not allowed_file(f.filename):
        return jsonify({'error': 'Invalid file'}), 400

    ext = f.filename.rsplit('.', 1)[1].lower()
    unique_name = 'issue_' + str(uuid.uuid4())[:10] + '.' + ext
    f.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_name))

    conn = get_db()
    photo_status_id = request.form.get('photo_status_id') or None
    custom_status = request.form.get('custom_status', '').strip() or None
    description = request.form.get('description', '').strip()
    conn.execute(
        'INSERT INTO issue_photos (issue_id, filename, description, photo_status_id, custom_status) VALUES (?,?,?,?,?)',
        (issue_id, unique_name, description, photo_status_id, custom_status)
    )
    conn.commit()
    photo_id = conn.execute('SELECT last_insert_rowid() as id').fetchone()['id']
    conn.close()
    return jsonify({'ok': True, 'photo_id': photo_id, 'url': url_for('static', filename=f'uploads/{unique_name}')})


@app.route('/admin/prijave/foto/<int:photo_id>/delete', methods=['POST'])
@login_required
def admin_issue_photo_delete(photo_id):
    conn = get_db()
    photo = conn.execute('SELECT * FROM issue_photos WHERE id=?', (photo_id,)).fetchone()
    if photo:
        try:
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], photo['filename']))
        except FileNotFoundError:
            pass
        conn.execute('DELETE FROM issue_photos WHERE id=?', (photo_id,))
        conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/admin/foto-stanja', methods=['GET', 'POST'])
@login_required
def admin_photo_statuses():
    conn = get_db()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            name = request.form.get('name', '').strip()
            color = request.form.get('color', '#888888')
            max_order = conn.execute('SELECT COALESCE(MAX(sort_order),0) as m FROM issue_photo_statuses').fetchone()['m']
            conn.execute('INSERT INTO issue_photo_statuses (name, color, sort_order) VALUES (?,?,?)',
                         (name, color, max_order + 1))
            flash('Stanje dodano.', 'success')
        elif action == 'edit':
            sid = request.form.get('id')
            conn.execute('UPDATE issue_photo_statuses SET name=?, color=? WHERE id=?',
                         (request.form.get('name', ''), request.form.get('color', '#888888'), sid))
            flash('Stanje posodobljeno.', 'success')
        elif action == 'delete':
            conn.execute('DELETE FROM issue_photo_statuses WHERE id=?', (request.form.get('id'),))
            flash('Stanje izbrisano.', 'info')
        conn.commit()
        conn.close()
        return redirect(url_for('admin_photo_statuses'))
    statuses = conn.execute('SELECT * FROM issue_photo_statuses ORDER BY sort_order').fetchall()
    conn.close()
    return render_template('admin/photo_statuses.html', statuses=statuses)


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

def generate_random_password(length=10):
    import random, string
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))


@app.route('/admin/stranke')
@login_required
def admin_customers():
    conn = get_db()
    customers = conn.execute('''
        SELECT c.*,
               COUNT(DISTINCT cm.id) as machine_count,
               COUNT(DISTINCT i.id) as issue_count
        FROM customers c
        LEFT JOIN customer_machines cm ON cm.customer_id=c.id
        LEFT JOIN issues i ON i.contact_email=c.email
        GROUP BY c.id ORDER BY c.company_name
    ''').fetchall()
    conn.close()
    return render_template('admin/customers.html', customers=customers)


@app.route('/admin/stranke/nova', methods=['GET', 'POST'])
@login_required
def admin_customer_new():
    if request.method == 'POST':
        raw_pw = request.form.get('password') or generate_random_password()
        username = request.form.get('username', '').strip().lower()
        conn = get_db()
        try:
            conn.execute('''
                INSERT INTO customers (company_name, contact_name, username, password_hash, email, phone, address, notes)
                VALUES (?,?,?,?,?,?,?,?)
            ''', (
                request.form.get('company_name', '').strip(),
                request.form.get('contact_name', '').strip(),
                username,
                generate_password_hash(raw_pw),
                request.form.get('email', '').strip(),
                request.form.get('phone', '').strip(),
                request.form.get('address', '').strip(),
                request.form.get('notes', '').strip(),
            ))
            conn.commit()
            cid = conn.execute('SELECT id FROM customers WHERE username=?', (username,)).fetchone()['id']
            conn.close()
            flash(f'Stranka dodana. Geslo: <strong>{raw_pw}</strong> — shranite ga!', 'success')
            return redirect(url_for('admin_customer_detail', customer_id=cid))
        except Exception as e:
            conn.close()
            flash(f'Napaka: {e}', 'error')
    return render_template('admin/customer_edit.html', customer=None)


@app.route('/admin/stranke/<int:customer_id>', methods=['GET', 'POST'])
@login_required
def admin_customer_detail(customer_id):
    conn = get_db()
    customer = conn.execute('SELECT * FROM customers WHERE id=?', (customer_id,)).fetchone()
    if not customer:
        conn.close()
        abort(404)

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'update':
            conn.execute('''
                UPDATE customers SET company_name=?, contact_name=?, email=?, phone=?, address=?, notes=?, is_active=?
                WHERE id=?
            ''', (
                request.form.get('company_name', '').strip(),
                request.form.get('contact_name', '').strip(),
                request.form.get('email', '').strip(),
                request.form.get('phone', '').strip(),
                request.form.get('address', '').strip(),
                request.form.get('notes', '').strip(),
                1 if request.form.get('is_active') else 0,
                customer_id
            ))
            flash('Stranka posodobljena.', 'success')
        elif action == 'reset_password':
            new_pw = generate_random_password()
            conn.execute('UPDATE customers SET password_hash=? WHERE id=?',
                         (generate_password_hash(new_pw), customer_id))
            flash(f'Novo geslo: <strong>{new_pw}</strong> — shranite ga!', 'success')
        conn.commit()
        conn.close()
        return redirect(url_for('admin_customer_detail', customer_id=customer_id))

    machines = conn.execute(
        'SELECT * FROM customer_machines WHERE customer_id=? ORDER BY name', (customer_id,)
    ).fetchall()
    config_requests = conn.execute('''
        SELECT cr.*, cm.name as machine_name FROM config_requests cr
        LEFT JOIN customer_machines cm ON cr.machine_id=cm.id
        WHERE cr.customer_id=? ORDER BY cr.created_at DESC LIMIT 10
    ''', (customer_id,)).fetchall()
    conn.close()
    return render_template('admin/customer_detail.html',
                           customer=customer, machines=machines, config_requests=config_requests)


@app.route('/admin/stranke/<int:customer_id>/delete', methods=['POST'])
@login_required
def admin_customer_delete(customer_id):
    conn = get_db()
    conn.execute('DELETE FROM customers WHERE id=?', (customer_id,))
    conn.commit()
    conn.close()
    flash('Stranka izbrisana.', 'info')
    return redirect(url_for('admin_customers'))


# ---------------------------------------------------------------------------
# Customer machines (XY-style configurator)
# ---------------------------------------------------------------------------

@app.route('/admin/stranke/<int:customer_id>/avtomat/nov', methods=['GET', 'POST'])
@login_required
def admin_machine_new(customer_id):
    conn = get_db()
    customer = conn.execute('SELECT * FROM customers WHERE id=?', (customer_id,)).fetchone()
    if not customer:
        conn.close()
        abort(404)
    if request.method == 'POST':
        shelf_config = request.form.get('shelf_config', '[]')
        connected_to = request.form.get('connected_to') or None
        conn.execute('''
            INSERT INTO customer_machines
            (customer_id, name, serial_number, machine_type, is_cooled, connected_to, location, shelf_config, status, notes, installed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            customer_id,
            request.form.get('name', '').strip(),
            request.form.get('serial_number', '').strip(),
            request.form.get('machine_type', 'master'),
            1 if request.form.get('is_cooled') else 0,
            connected_to,
            request.form.get('location', '').strip(),
            shelf_config,
            request.form.get('status', 'active'),
            request.form.get('notes', '').strip(),
            request.form.get('installed_at', '').strip() or None,
        ))
        conn.commit()
        conn.close()
        flash('Avtomat dodan.', 'success')
        return redirect(url_for('admin_customer_detail', customer_id=customer_id))
    masters = conn.execute(
        "SELECT * FROM customer_machines WHERE customer_id=? AND machine_type='master'", (customer_id,)
    ).fetchall()
    conn.close()
    return render_template('admin/machine_edit.html', customer=customer, machine=None, masters=masters)


@app.route('/admin/avtomat/<int:machine_id>', methods=['GET', 'POST'])
@login_required
def admin_machine_edit(machine_id):
    conn = get_db()
    machine = conn.execute('SELECT * FROM customer_machines WHERE id=?', (machine_id,)).fetchone()
    if not machine:
        conn.close()
        abort(404)
    customer = conn.execute('SELECT * FROM customers WHERE id=?', (machine['customer_id'],)).fetchone()

    if request.method == 'POST':
        shelf_config = request.form.get('shelf_config', machine['shelf_config'])
        connected_to = request.form.get('connected_to') or None
        conn.execute('''
            UPDATE customer_machines
            SET name=?, serial_number=?, machine_type=?, is_cooled=?, connected_to=?,
                location=?, shelf_config=?, status=?, notes=?, installed_at=?, updated_at=?
            WHERE id=?
        ''', (
            request.form.get('name', '').strip(),
            request.form.get('serial_number', '').strip(),
            request.form.get('machine_type', 'master'),
            1 if request.form.get('is_cooled') else 0,
            connected_to,
            request.form.get('location', '').strip(),
            shelf_config,
            request.form.get('status', 'active'),
            request.form.get('notes', '').strip(),
            request.form.get('installed_at', '').strip() or None,
            datetime.now().isoformat(),
            machine_id,
        ))
        conn.commit()
        conn.close()
        flash('Avtomat posodobljen.', 'success')
        return redirect(url_for('admin_customer_detail', customer_id=customer['id']))

    masters = conn.execute(
        "SELECT * FROM customer_machines WHERE customer_id=? AND machine_type='master' AND id!=?",
        (machine['customer_id'], machine_id)
    ).fetchall()
    conn.close()
    return render_template('admin/machine_edit.html', customer=customer, machine=machine, masters=masters)


@app.route('/admin/avtomat/<int:machine_id>/delete', methods=['POST'])
@login_required
def admin_machine_delete(machine_id):
    conn = get_db()
    m = conn.execute('SELECT customer_id FROM customer_machines WHERE id=?', (machine_id,)).fetchone()
    cid = m['customer_id'] if m else None
    conn.execute('DELETE FROM customer_machines WHERE id=?', (machine_id,))
    conn.commit()
    conn.close()
    flash('Avtomat izbrisan.', 'info')
    return redirect(url_for('admin_customer_detail', customer_id=cid) if cid else url_for('admin_customers'))


# ---------------------------------------------------------------------------
# Config requests (admin side)
# ---------------------------------------------------------------------------

@app.route('/admin/zahtevki')
@login_required
def admin_config_requests():
    conn = get_db()
    status_filter = request.args.get('status', '')
    q = '''
        SELECT cr.*, c.company_name, cm.name as machine_name
        FROM config_requests cr
        JOIN customers c ON cr.customer_id=c.id
        LEFT JOIN customer_machines cm ON cr.machine_id=cm.id
        WHERE 1=1
    '''
    params = []
    if status_filter:
        q += ' AND cr.status=?'
        params.append(status_filter)
    q += ' ORDER BY cr.created_at DESC'
    requests_list = conn.execute(q, params).fetchall()
    conn.close()
    return render_template('admin/config_requests.html', requests=requests_list, status_filter=status_filter)


@app.route('/admin/zahtevki/<int:req_id>', methods=['GET', 'POST'])
@login_required
def admin_config_request_detail(req_id):
    conn = get_db()
    req = conn.execute('''
        SELECT cr.*, c.company_name, c.email as customer_email, cm.name as machine_name, cm.shelf_config as current_config
        FROM config_requests cr
        JOIN customers c ON cr.customer_id=c.id
        LEFT JOIN customer_machines cm ON cr.machine_id=cm.id
        WHERE cr.id=?
    ''', (req_id,)).fetchone()
    if not req:
        conn.close()
        abort(404)

    if request.method == 'POST':
        action = request.form.get('action')
        admin_note = request.form.get('admin_note', req['admin_note'] or '')
        valid_statuses = ('pending', 'reviewing', 'pre_install', 'approved', 'rejected', 'applied')

        if action == 'apply' and req['machine_id']:
            conn.execute('UPDATE config_requests SET status=?, admin_note=?, updated_at=? WHERE id=?',
                         ('applied', admin_note, datetime.now().isoformat(), req_id))
            conn.execute('UPDATE customer_machines SET shelf_config=?, updated_at=? WHERE id=?',
                         (req['config_data'], datetime.now().isoformat(), req['machine_id']))
            conn.commit()
            flash('Konfiguracija aplicirana na avtomat.', 'success')
        elif action == 'set_status':
            new_status = request.form.get('status')
            if new_status in valid_statuses:
                conn.execute('UPDATE config_requests SET status=?, admin_note=?, updated_at=? WHERE id=?',
                             (new_status, admin_note, datetime.now().isoformat(), req_id))
                conn.commit()
                status_labels = {'pending':'Čaka','reviewing':'V pregledu','pre_install':'Potrjeno pred namestitvijo',
                                 'approved':'Odobren','rejected':'Zavrnjen','applied':'Apliciran'}
                flash(f'Status: {status_labels.get(new_status, new_status)}', 'success')
        elif action == 'save_note':
            conn.execute('UPDATE config_requests SET admin_note=?, updated_at=? WHERE id=?',
                         (admin_note, datetime.now().isoformat(), req_id))
            conn.commit()
            flash('Opomba shranjena.', 'success')
        conn.close()
        return redirect(url_for('admin_config_request_detail', req_id=req_id))

    conn.close()
    return render_template('admin/config_request_detail.html', req=req)


# ---------------------------------------------------------------------------
# Customer portal
# ---------------------------------------------------------------------------

def portal_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'customer_id' not in session:
            return redirect(url_for('portal_login', next=request.url))
        return f(*args, **kwargs)
    return decorated


def get_customer():
    if 'customer_id' not in session:
        return None
    conn = get_db()
    c = conn.execute('SELECT * FROM customers WHERE id=? AND is_active=1', (session['customer_id'],)).fetchone()
    conn.close()
    return c


@app.context_processor
def inject_customer():
    return dict(current_customer=get_customer())


@app.route('/portal')
def portal_index():
    if 'customer_id' in session:
        return redirect(url_for('portal_dashboard'))
    return redirect(url_for('portal_login'))


@app.route('/portal/prijava', methods=['GET', 'POST'])
def portal_login():
    if 'customer_id' in session:
        return redirect(url_for('portal_dashboard'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        conn = get_db()
        customer = conn.execute('SELECT * FROM customers WHERE username=? AND is_active=1', (username,)).fetchone()
        conn.close()
        if customer and check_password_hash(customer['password_hash'], password):
            session['customer_id'] = customer['id']
            session['customer_name'] = customer['company_name']
            return redirect(url_for('portal_dashboard'))
        error = 'Napačno uporabniško ime ali geslo.'
    return render_template('portal/login.html', error=error)


@app.route('/portal/odjava')
def portal_logout():
    session.pop('customer_id', None)
    session.pop('customer_name', None)
    return redirect(url_for('portal_login'))


@app.route('/portal/dashboard')
@portal_login_required
def portal_dashboard():
    conn = get_db()
    cid = session['customer_id']
    machines = conn.execute('SELECT * FROM customer_machines WHERE customer_id=? ORDER BY name', (cid,)).fetchall()
    issues = conn.execute('''
        SELECT i.*, s.name as status_name, s.color as status_color
        FROM issues i LEFT JOIN issue_statuses s ON i.status_id=s.id
        WHERE i.contact_email=(SELECT email FROM customers WHERE id=?)
        ORDER BY i.created_at DESC LIMIT 5
    ''', (cid,)).fetchall()
    open_requests = conn.execute(
        "SELECT COUNT(*) as c FROM config_requests WHERE customer_id=? AND status NOT IN ('approved','rejected','applied')",
        (cid,)
    ).fetchone()['c']
    conn.close()
    return render_template('portal/dashboard.html', machines=machines, issues=issues, open_requests=open_requests)


@app.route('/portal/prijava-tezave', methods=['GET', 'POST'])
@portal_login_required
def portal_report_issue():
    conn = get_db()
    cid = session['customer_id']
    customer = conn.execute('SELECT * FROM customers WHERE id=?', (cid,)).fetchone()
    machines = conn.execute('SELECT * FROM customer_machines WHERE customer_id=? ORDER BY name', (cid,)).fetchall()

    if request.method == 'POST':
        ticket_id = 'TKT-' + str(uuid.uuid4())[:8].upper()
        default_status = conn.execute('SELECT id FROM issue_statuses WHERE is_default=1 LIMIT 1').fetchone()
        status_id = default_status['id'] if default_status else None
        machine_id = request.form.get('machine_id', '').strip()
        machine_name = ''
        if machine_id:
            m = conn.execute('SELECT name, location FROM customer_machines WHERE id=? AND customer_id=?', (machine_id, cid)).fetchone()
            if m:
                machine_name = m['name']
        conn.execute('''
            INSERT INTO issues
            (ticket_id, company_name, location, machine_id, machine_type, description,
             contact_name, contact_email, contact_phone, status_id, priority)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            ticket_id,
            customer['company_name'],
            request.form.get('location', customer.get('address', '')).strip(),
            machine_id,
            machine_name,
            request.form.get('description', '').strip(),
            customer['contact_name'],
            customer['email'],
            customer['phone'],
            status_id,
            request.form.get('priority', 'normal'),
        ))
        conn.commit()
        conn.close()
        flash(f'Prijava oddana! Številka: <strong>{ticket_id}</strong>', 'success')
        return redirect(url_for('portal_issues'))

    conn.close()
    return render_template('portal/report_issue.html', customer=customer, machines=machines)


@app.route('/portal/prijave')
@portal_login_required
def portal_issues():
    conn = get_db()
    cid = session['customer_id']
    customer = conn.execute('SELECT email FROM customers WHERE id=?', (cid,)).fetchone()
    issues = conn.execute('''
        SELECT i.*, s.name as status_name, s.color as status_color
        FROM issues i LEFT JOIN issue_statuses s ON i.status_id=s.id
        WHERE i.contact_email=?
        ORDER BY i.created_at DESC
    ''', (customer['email'],)).fetchall()
    conn.close()
    return render_template('portal/issues.html', issues=issues)


@app.route('/portal/avtomati')
@portal_login_required
def portal_machines():
    conn = get_db()
    cid = session['customer_id']
    machines = conn.execute('SELECT * FROM customer_machines WHERE customer_id=? ORDER BY name', (cid,)).fetchall()
    conn.close()
    return render_template('portal/machines.html', machines=machines)


@app.route('/portal/avtomat/<int:machine_id>')
@portal_login_required
def portal_machine_detail(machine_id):
    conn = get_db()
    cid = session['customer_id']
    machine = conn.execute('SELECT * FROM customer_machines WHERE id=? AND customer_id=?', (machine_id, cid)).fetchone()
    if not machine:
        conn.close()
        abort(404)
    requests_list = conn.execute(
        'SELECT * FROM config_requests WHERE machine_id=? ORDER BY created_at DESC', (machine_id,)
    ).fetchall()
    conn.close()
    return render_template('portal/machine_detail.html', machine=machine, requests=requests_list)


@app.route('/portal/avtomat/<int:machine_id>/konfiguracija', methods=['GET', 'POST'])
@portal_login_required
def portal_config_request(machine_id):
    conn = get_db()
    cid = session['customer_id']
    machine = conn.execute('SELECT * FROM customer_machines WHERE id=? AND customer_id=?', (machine_id, cid)).fetchone()
    if not machine:
        conn.close()
        abort(404)

    if request.method == 'POST':
        config_data = request.form.get('config_data', '{}')
        description = request.form.get('description', '').strip()
        title = request.form.get('title', 'Nova konfiguracija').strip()
        conn.execute('''
            INSERT INTO config_requests (customer_id, machine_id, title, config_data, description, status)
            VALUES (?,?,?,?,?,'pending')
        ''', (cid, machine_id, title, config_data, description))
        conn.commit()
        conn.close()
        flash('Zahteva za konfiguracijo je bila poslana.', 'success')
        return redirect(url_for('portal_machine_detail', machine_id=machine_id))

    conn.close()
    return render_template('portal/config_request.html', machine=machine)


@app.route('/portal/nastavitve', methods=['GET', 'POST'])
@portal_login_required
def portal_settings():
    conn = get_db()
    cid = session['customer_id']
    customer = conn.execute('SELECT * FROM customers WHERE id=?', (cid,)).fetchone()

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'change_password':
            old_pw = request.form.get('old_password', '')
            new_pw = request.form.get('new_password', '')
            if customer and check_password_hash(customer['password_hash'], old_pw):
                conn.execute('UPDATE customers SET password_hash=? WHERE id=?',
                             (generate_password_hash(new_pw), cid))
                conn.commit()
                flash('Geslo uspešno spremenjeno.', 'success')
            else:
                flash('Staro geslo ni pravilno.', 'error')
        conn.close()
        return redirect(url_for('portal_settings'))

    conn.close()
    return render_template('portal/settings.html', customer=customer)


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    init_db()
    app.run(debug=True, host='0.0.0.0', port=8080)
