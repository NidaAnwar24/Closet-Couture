from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from PIL import Image
import os, base64, json, urllib.request, io
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'closet-couture-ultra-secret-2024'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///closet.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024

ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
CATEGORIES  = ['Tops', 'Bottoms', 'Dresses', 'Outerwear', 'Shoes', 'Accessories', 'Bags', 'Other']

db = SQLAlchemy(app)

# ── Models ──────────────────────────────────────────────────────────────────

class User(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80),  unique=True, nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    gender        = db.Column(db.String(10))
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    clothes       = db.relationship('ClothingItem', backref='owner', lazy=True, cascade='all,delete')
    outfits       = db.relationship('Outfit',       backref='owner', lazy=True, cascade='all,delete')

class ClothingItem(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name       = db.Column(db.String(100), nullable=False)
    category   = db.Column(db.String(50),  nullable=False)
    color      = db.Column(db.String(50))
    image_path = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Outfit(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name         = db.Column(db.String(100), nullable=False)
    items_json   = db.Column(db.Text)
    canvas_path  = db.Column(db.String(200))
    ai_generated = db.Column(db.Boolean, default=False)
    inspo_text   = db.Column(db.Text)
    style_note   = db.Column(db.Text)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

# ── Helpers ──────────────────────────────────────────────────────────────────

def allowed(fn):
    return '.' in fn and fn.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def save_upload(file, subfolder='clothing'):
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], subfolder), exist_ok=True)
    fn   = secure_filename(f"{session['user_id']}_{int(datetime.now().timestamp()*1000)}_{file.filename}")
    path = os.path.join(app.config['UPLOAD_FOLDER'], subfolder, fn)
    file.save(path)
    return f"{subfolder}/{fn}"

def item_img_url(path):
    if path and os.path.exists(os.path.join('static', 'uploads', path)):
        return url_for('static', filename=f'uploads/{path}')
    return None

# Category heights for outfit canvas (top→bottom order)
CAT_ORDER  = ['Outerwear', 'Dresses', 'Tops', 'Accessories', 'Bottoms', 'Bags', 'Shoes', 'Other']
CAT_HEIGHT = {'Outerwear':300,'Tops':270,'Dresses':480,'Bottoms':260,'Shoes':200,'Accessories':180,'Bags':180,'Other':220}

def render_outfit_image(item_ids, outfit_id, user_id):
    items = ClothingItem.query.filter(
        ClothingItem.id.in_(item_ids), ClothingItem.user_id == user_id).all()

    W, H = 800, 1100
    canvas = Image.new('RGB', (W, H), (252, 250, 255))
    y = 20

    # sort items by category order
    order_map = {c: i for i, c in enumerate(CAT_ORDER)}
    items_sorted = sorted(items, key=lambda x: order_map.get(x.category, 99))

    for item in items_sorted:
        if not item.image_path:
            continue
        src = os.path.join('static', 'uploads', item.image_path)
        if not os.path.exists(src):
            continue
        try:
            img = Image.open(src).convert('RGBA')
        except Exception:
            continue

        tgt_h = CAT_HEIGHT.get(item.category, 220)
        tgt_w = int(img.width * tgt_h / img.height)
        if tgt_w > W - 60:
            tgt_w = W - 60
            tgt_h = int(img.height * tgt_w / img.width)

        img = img.resize((tgt_w, tgt_h), Image.LANCZOS)
        x   = (W - tgt_w) // 2

        # paste with alpha
        bg_patch = canvas.crop((x, y, x + tgt_w, y + tgt_h))
        bg_patch = bg_patch.convert('RGBA')
        bg_patch.paste(img, (0, 0), img)
        canvas.paste(bg_patch.convert('RGB'), (x, y))

        y += tgt_h + 14
        if y > H - 40:
            break

    out_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'outfits')
    os.makedirs(out_dir, exist_ok=True)
    fn  = f"outfit_{user_id}_{outfit_id}_{int(datetime.now().timestamp())}.png"
    out = os.path.join(out_dir, fn)
    canvas.save(out)
    return f"outfits/{fn}"

def call_claude(messages, system="You are a professional fashion stylist."):
    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 800,
        "system": system,
        "messages": messages
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=payload,
        headers={"Content-Type":"application/json","anthropic-version":"2023-06-01"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())['content'][0]['text']
    except Exception as e:
        return f'{{"error":"{e}"}}'

# ── Auth ─────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return redirect(url_for('dashboard') if 'user_id' in session else url_for('landing'))

@app.route('/landing')
def landing():
    return render_template('landing.html')

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        u, e, p = request.form['username'], request.form['email'], request.form['password']
        if User.query.filter_by(email=e).first():
            return render_template('auth.html', mode='register', error='Email already registered.')
        if User.query.filter_by(username=u).first():
            return render_template('auth.html', mode='register', error='Username taken.')
        user = User(username=u, email=e, password_hash=generate_password_hash(p))
        db.session.add(user); db.session.commit()
        session['user_id'] = user.id; session['username'] = user.username
        return redirect(url_for('gender_select'))
    return render_template('auth.html', mode='register')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        e, p = request.form['email'], request.form['password']
        user = User.query.filter_by(email=e).first()
        if user and check_password_hash(user.password_hash, p):
            session.update({'user_id': user.id, 'username': user.username, 'gender': user.gender})
            return redirect(url_for('gender_select') if not user.gender else url_for('dashboard'))
        return render_template('auth.html', mode='login', error='Invalid credentials.')
    return render_template('auth.html', mode='login')

@app.route('/gender', methods=['GET','POST'])
def gender_select():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        g    = request.form['gender']
        user = db.session.get(User, session['user_id'])
        user.gender = g; db.session.commit()
        session['gender'] = g
        return redirect(url_for('dashboard'))
    return render_template('gender_select.html', username=session.get('username',''))

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('landing'))

# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    user    = db.session.get(User, session['user_id'])
    clothes = ClothingItem.query.filter_by(user_id=user.id).all()
    outfits = Outfit.query.filter_by(user_id=user.id).order_by(Outfit.created_at.desc()).limit(6).all()
    counts  = {c: sum(1 for x in clothes if x.category==c) for c in CATEGORIES}
    return render_template('dashboard.html', user=user, clothes=clothes,
                           outfits=outfits, counts=counts, categories=CATEGORIES,
                           gender=user.gender, item_img_url=item_img_url)

# ── Wardrobe ──────────────────────────────────────────────────────────────────

@app.route('/wardrobe')
def wardrobe():
    if 'user_id' not in session: return redirect(url_for('login'))
    user    = db.session.get(User, session['user_id'])
    clothes = ClothingItem.query.filter_by(user_id=user.id).order_by(
              ClothingItem.category, ClothingItem.created_at.desc()).all()
    by_cat  = {c: [x for x in clothes if x.category==c] for c in CATEGORIES}
    return render_template('wardrobe.html', user=user, by_cat=by_cat,
                           categories=CATEGORIES, gender=user.gender, item_img_url=item_img_url)

@app.route('/upload-item', methods=['POST'])
def upload_item():
    if 'user_id' not in session: return jsonify(error='Not logged in'), 401
    name     = (request.form.get('name','') or 'My Item').strip()
    category = request.form.get('category','Other')
    color    = request.form.get('color','').strip()
    img_path = None
    if 'image' in request.files:
        f = request.files['image']
        if f and f.filename and allowed(f.filename):
            img_path = save_upload(f, 'clothing')
    item = ClothingItem(user_id=session['user_id'], name=name,
                        category=category, color=color, image_path=img_path)
    db.session.add(item); db.session.commit()
    return jsonify(success=True, id=item.id, name=item.name, category=item.category,
                   color=item.color, img_url=item_img_url(img_path) or '')

@app.route('/delete-item/<int:iid>', methods=['DELETE'])
def delete_item(iid):
    if 'user_id' not in session: return jsonify(error='Not logged in'), 401
    item = ClothingItem.query.filter_by(id=iid, user_id=session['user_id']).first_or_404()
    if item.image_path:
        try: os.remove(os.path.join('static','uploads',item.image_path))
        except: pass
    db.session.delete(item); db.session.commit()
    return jsonify(success=True)

# ── Outfit Builder ────────────────────────────────────────────────────────────

@app.route('/builder')
def builder():
    if 'user_id' not in session: return redirect(url_for('login'))
    user    = db.session.get(User, session['user_id'])
    clothes = ClothingItem.query.filter_by(user_id=user.id).order_by(ClothingItem.category).all()
    by_cat  = {c: [x for x in clothes if x.category==c] for c in CATEGORIES}
    return render_template('builder.html', user=user, by_cat=by_cat,
                           categories=CATEGORIES, gender=user.gender, item_img_url=item_img_url)

@app.route('/save-outfit', methods=['POST'])
def save_outfit():
    if 'user_id' not in session: return jsonify(error='Not logged in'), 401
    data     = request.json
    name     = (data.get('name','') or 'My Outfit').strip()
    item_ids = [int(i) for i in data.get('items',[])]
    if not item_ids: return jsonify(error='Select at least one item'), 400

    outfit = Outfit(user_id=session['user_id'], name=name,
                    items_json=json.dumps(item_ids), ai_generated=False)
    db.session.add(outfit); db.session.flush()
    outfit.canvas_path = render_outfit_image(item_ids, outfit.id, session['user_id'])
    db.session.commit()

    return jsonify(success=True, id=outfit.id,
                   canvas_url=url_for('static', filename=f'uploads/{outfit.canvas_path}'))

# ── Couture Buddy ─────────────────────────────────────────────────────────────

@app.route('/couture-buddy')
def couture_buddy():
    if 'user_id' not in session: return redirect(url_for('login'))
    user    = db.session.get(User, session['user_id'])
    clothes = ClothingItem.query.filter_by(user_id=user.id).all()
    outfits = Outfit.query.filter_by(user_id=user.id, ai_generated=True)\
                    .order_by(Outfit.created_at.desc()).limit(8).all()
    return render_template('couture_buddy.html', user=user, clothes=clothes,
                           outfits=outfits, gender=user.gender, item_img_url=item_img_url)

@app.route('/ai-create-outfit', methods=['POST'])
def ai_create_outfit():
    if 'user_id' not in session: return jsonify(error='Not logged in'), 401
    data       = request.json
    inspo      = (data.get('inspo','') or '').strip()
    occasion   = (data.get('occasion','') or '').strip()
    if not inspo: return jsonify(error='Describe your inspo first!'), 400

    user    = db.session.get(User, session['user_id'])
    clothes = ClothingItem.query.filter_by(user_id=user.id).all()
    if not clothes: return jsonify(error='Your wardrobe is empty — upload some clothes first!'), 400

    wardrobe_str = "\n".join(
        f"ID:{c.id} | {c.name} | {c.category} | color:{c.color or '?'}" for c in clothes)

    prompt = f"""Style a {user.gender or 'person'} using ONLY these wardrobe items:
{wardrobe_str}

Inspo: {inspo}
{f'Occasion: {occasion}' if occasion else ''}

Pick 2-5 complementary items. Return ONLY valid JSON (no markdown):
{{"outfit_name":"...","item_ids":[...],"style_note":"one vivid sentence about the look"}}"""

    raw = call_claude([{"role":"user","content":prompt}],
                      "Elite fashion stylist. Return only valid JSON, no extra text.")
    try:
        clean = raw.strip().lstrip('```json').lstrip('```').rstrip('```').strip()
        ai    = json.loads(clean)
        ids   = [int(i) for i in ai.get('item_ids',[])]
        oname = ai.get('outfit_name','Couture Buddy Pick')
        note  = ai.get('style_note','')
    except Exception:
        return jsonify(error='AI response parse failed. Try again!'), 500

    valid = {c.id for c in clothes}
    ids   = [i for i in ids if i in valid]
    if not ids: return jsonify(error='AI could not match items. Describe your inspo differently.'), 400

    outfit = Outfit(user_id=session['user_id'], name=oname,
                    items_json=json.dumps(ids), ai_generated=True,
                    inspo_text=inspo, style_note=note)
    db.session.add(outfit); db.session.flush()
    outfit.canvas_path = render_outfit_image(ids, outfit.id, session['user_id'])
    db.session.commit()

    sel   = [c for c in clothes if c.id in ids]
    items = [{"id":c.id,"name":c.name,"category":c.category,
              "img_url":item_img_url(c.image_path) or ''} for c in sel]

    return jsonify(success=True, outfit_id=outfit.id, outfit_name=oname,
                   style_note=note, items=items,
                   canvas_url=url_for('static', filename=f'uploads/{outfit.canvas_path}'))

# ── Outfits gallery ───────────────────────────────────────────────────────────

@app.route('/outfits')
def outfits_gallery():
    if 'user_id' not in session: return redirect(url_for('login'))
    user    = db.session.get(User, session['user_id'])
    all_out = Outfit.query.filter_by(user_id=user.id).order_by(Outfit.created_at.desc()).all()
    return render_template('outfits.html', user=user, outfits=all_out,
                           gender=user.gender, item_img_url=item_img_url)

@app.route('/delete-outfit/<int:oid>', methods=['DELETE'])
def delete_outfit(oid):
    if 'user_id' not in session: return jsonify(error='Not logged in'), 401
    o = Outfit.query.filter_by(id=oid, user_id=session['user_id']).first_or_404()
    if o.canvas_path:
        try: os.remove(os.path.join('static','uploads',o.canvas_path))
        except: pass
    db.session.delete(o); db.session.commit()
    return jsonify(success=True)

# ─────────────────────────────────────────────────────────────────────────────

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    os.makedirs(os.path.join('static','uploads','clothing'), exist_ok=True)
    os.makedirs(os.path.join('static','uploads','outfits'),  exist_ok=True)
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
