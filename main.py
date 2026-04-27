from flask import Flask, render_template, request, redirect, url_for, session, flash, Response
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import csv, io, json, requests
from functools import wraps

# ─── SQLAlchemy ─────────────────────────────────────────────
import sqlalchemy as sa
import sqlalchemy.orm as orm
from sqlalchemy.orm import Session

SqlAlchemyBase = orm.declarative_base()
__factory = None


def global_init(db_file):
    """Инициализация движка и фабрики сессий."""
    global __factory
    if __factory: return
    if not db_file:
        print("Нет пути к БД")
        exit(1)
    conn_str = f'sqlite:///{db_file.strip()}'
    engine = sa.create_engine(conn_str, echo=False)
    __factory = orm.sessionmaker(bind=engine)
    SqlAlchemyBase.metadata.create_all(engine)


def get_session() -> Session:
    """Получить новую сессию."""
    return __factory()


# ─── Модели ─────────────────────────────────────────────────────────────────

class User(SqlAlchemyBase):
    __tablename__ = 'users'
    id = sa.Column(sa.Integer, primary_key=True, autoincrement=True)
    username = sa.Column(sa.String, unique=True, nullable=False)
    password = sa.Column(sa.String, nullable=False)
    
    transactions = orm.relationship('Transaction', back_populates='user', 
                                   cascade='all, delete-orphan', lazy='select')


class Transaction(SqlAlchemyBase):
    __tablename__ = 'transactions'
    id = sa.Column(sa.Integer, primary_key=True, autoincrement=True)
    user_id = sa.Column(sa.Integer, sa.ForeignKey('users.id'), nullable=False)
    date = sa.Column(sa.String, nullable=False)
    amount = sa.Column(sa.Float, nullable=False)
    type = sa.Column(sa.String, nullable=False)
    category = sa.Column(sa.String, nullable=False)
    description = sa.Column(sa.String, default='')
    
    user = orm.relationship('User', back_populates='transactions')


# ─── Flask ──────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = "FinTrc_secret_key"
DB = "fintrc.db"
global_init(DB)

INCOME_CATS  = ["Зарплата", "Подарки", "Другое"]
EXPENSE_CATS = ["Продукты", "Транспорт", "Развлечения", "Жильё", "Другое"]
CURRENCIES   = ["USD", "EUR", "RUB", "GBP", "JPY", "CHF", "CNY", "KZT", "BYN", "UAH"]


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def get_summary(user_id):
    """Доходы, расходы, баланс через SQLAlchemy."""
    db = get_session()
    income = db.query(sa.func.sum(Transaction.amount)).filter(
        Transaction.user_id == user_id, Transaction.type == 'income'
    ).scalar() or 0
    expense = db.query(sa.func.sum(Transaction.amount)).filter(
        Transaction.user_id == user_id, Transaction.type == 'expense'
    ).scalar() or 0
    db.close()
    return income, expense, income - expense


def load_exchange_rates():
    try:
        r = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=10)
        return r.json().get("rates", {})
    except:
        return {}


# ─── Маршруты ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return redirect(url_for("overview" if "user_id" in session else "login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        confirm  = request.form["confirm"].strip()

        if not all([username, password, confirm]):
            flash("Заполните все поля.", "danger")
            return render_template("register.html")
        if password != confirm or len(password) < 6:
            flash("Пароли не совпадают или слишком короткий.", "danger")
            return render_template("register.html")

        db = get_session()
        if db.query(User).filter(User.username == username).first():
            db.close()
            flash("Пользователь существует.", "danger")
            return render_template("register.html")

        new_user = User(username=username, password=generate_password_hash(password))
        db.add(new_user)
        db.commit()
        db.close()
        flash("Аккаунт создан! Войдите.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        db = get_session()
        user = db.query(User).filter(User.username == username).first()
        db.close()
        if user and check_password_hash(user.password, password):
            session["user_id"], session["username"] = user.id, user.username
            return redirect(url_for("overview"))
        flash("Неверный логин или пароль.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/overview")
@login_required
def overview():
    uid = session["user_id"]
    income, expense, balance = get_summary(uid)
    db = get_session()
    recent = db.query(Transaction).filter(Transaction.user_id == uid).order_by(
        Transaction.date.desc(), Transaction.id.desc()
    ).limit(5).all()
    db.close()
    return render_template("overview.html", income=income, expense=expense, 
                          balance=balance, recent=recent)


@app.route("/add", methods=["GET", "POST"])
@login_required
def add():
    uid = session["user_id"]
    selected_type = request.args.get("type", "income")
    categories = INCOME_CATS if selected_type == "income" else EXPENSE_CATS
    today = datetime.today().strftime("%Y-%m-%d")

    if request.method == "POST":
        date_str = request.form["date"].strip()
        amount_str = request.form["amount"].strip()
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            amount = float(amount_str)
            if amount <= 0: raise ValueError
        except:
            flash("Проверьте дату и сумму.", "danger")
            return render_template("add.html", selected_type=selected_type, 
                                 categories=categories, today=today)

        db = get_session()
        tx = Transaction(user_id=uid, date=date_str, amount=amount,
                        type=request.form["type"], category=request.form["category"],
                        description=request.form.get("description", "").strip())
        db.add(tx)
        db.commit()
        db.close()
        flash("Транзакция добавлена!", "success")
        return redirect(url_for("add"))

    return render_template("add.html", selected_type=selected_type, 
                          categories=categories, today=today)


@app.route("/history")
@login_required
def history():
    uid = session["user_id"]
    filter_type = request.args.get("filter", "all")
    db = get_session()
    query = db.query(Transaction).filter(Transaction.user_id == uid)
    if filter_type == "income":
        query = query.filter(Transaction.type == "income")
    elif filter_type == "expense":
        query = query.filter(Transaction.type == "expense")
    rows = query.order_by(Transaction.date.desc(), Transaction.id.desc()).all()
    db.close()
    return render_template("history.html", rows=rows, filter_type=filter_type)


@app.route("/edit", methods=["GET", "POST"])
@login_required
def edit():
    uid = session["user_id"]
    db = get_session()
    load_id = request.args.get("id") or request.form.get("load_id")
    transaction = db.query(Transaction).filter(Transaction.id == load_id, 
                                               Transaction.user_id == uid).first() if load_id else None
    if load_id and not transaction:
        flash("Транзакция не найдена.", "danger")

    if request.method == "POST":
        action = request.form.get("action")
        tid = request.form["id"]
        tx = db.query(Transaction).filter(Transaction.id == tid, Transaction.user_id == uid).first()
        if tx and action == "update":
            try:
                datetime.strptime(request.form["date"].strip(), "%Y-%m-%d")
                amount = float(request.form["amount"].strip())
                if amount <= 0: raise ValueError
                tx.date = request.form["date"].strip()
                tx.amount = amount
                tx.type = request.form["type"]
                tx.category = request.form["category"]
                tx.description = request.form.get("description", "").strip()
                db.commit()
                flash(f"Транзакция #{tid} обновлена!", "success")
            except:
                flash("Проверьте дату и сумму.", "danger")
        elif tx and action == "delete":
            db.delete(tx)
            db.commit()
            flash(f"Транзакция #{tid} удалена.", "success")
        db.close()
        return redirect(url_for("history"))

    categories = INCOME_CATS if (transaction and transaction.type == "income") else EXPENSE_CATS
    db.close()
    return render_template("edit.html", transaction=transaction, categories=categories,
                          income_cats=INCOME_CATS, expense_cats=EXPENSE_CATS)


@app.route("/converter", methods=["GET", "POST"])
@login_required
def converter():
    result = error = None
    rates = load_exchange_rates()
    if not rates: error = "Не удалось загрузить курсы."
    amount_str = request.form.get("amount", "")
    from_curr = request.form.get("from_curr", "USD")
    to_curr = request.form.get("to_curr", "RUB")
    if request.method == "POST" and rates:
        try:
            amount = float(amount_str)
            if amount <= 0: raise ValueError
            usd = amount / rates[from_curr] if from_curr != "USD" else amount
            res = usd * rates[to_curr] if to_curr != "USD" else usd
            result = f"{amount:,.2f} {from_curr} = {res:,.2f} {to_curr}"
        except:
            error = "Проверьте сумму."
    return render_template("converter.html", currencies=CURRENCIES,
                          amount=amount_str, from_curr=from_curr, 
                          to_curr=to_curr, result=result, error=error)


@app.route("/export")
@login_required
def export_csv():
    uid = session["user_id"]
    db = get_session()
    rows = db.query(Transaction).filter(Transaction.user_id == uid).order_by(
        Transaction.date.desc()).all()
    db.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Дата", "Сумма", "Тип", "Категория", "Описание"])
    for r in rows:
        writer.writerow([r.date, r.amount, "Доход" if r.type=="income" else "Расход", r.category, r.description])
    return Response(output.getvalue(), mimetype="text/csv",
                   headers={"Content-Disposition": "attachment; filename=fintrc.csv"})


@app.route("/import", methods=["POST"])
@login_required
def import_csv():
    uid = session["user_id"]
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Выберите файл.", "danger")
        return redirect(url_for("history"))
    try:
        content = file.read().decode("utf-8")
        rows = list(csv.reader(io.StringIO(content)))
    except:
        flash("Ошибка чтения файла.", "danger")
        return redirect(url_for("history"))
    db = get_session()
    added = 0
    for row in rows[1:]:
        if len(row) < 4: continue
        try:
            date_str, amount_str, typ = row[0], row[1], row[2].strip().lower()
            category, desc = row[3], row[4] if len(row)>4 else ""
            amount = float(amount_str)
            if amount <= 0: continue
            if typ in ("income","доход"): ttype="income"
            elif typ in ("expense","расход"): ttype="expense"
            else: continue
            datetime.strptime(date_str,"%Y-%m-%d")
            db.add(Transaction(user_id=uid,date=date_str,amount=amount,type=ttype,category=category,description=desc))
            added+=1
        except: continue
    db.commit()
    db.close()
    flash(f"Импортировано {added} записей.", "success")
    return redirect(url_for("history"))


if __name__ == "__main__":
    app.run(debug=True)
