from flask import Flask, render_template, request, redirect, make_response
import csv
import io
import re

app = Flask(__name__)

data = []
categories = set()
photographers = set()
services = set()

columns = ["Дата", "Тип", "Услуга", "Часы", "Цена", "Стоимость", "Фотограф"]


def clean_value(value):
    if not value:
        return ""
    return re.sub(r'\s+', ' ', value.strip())


@app.template_filter("format_currency")
def format_currency(value):
    try:
        return f"{int(float(value or 0)):,} руб."
    except:
        return value


def parse_csv(file):
    stream = io.StringIO(file.stream.read().decode("utf-8"))
    rows = []
    for row in csv.DictReader(stream):
        cleaned = {k: clean_value(v) for k, v in row.items()}
        rows.append(cleaned)
    return rows


@app.route("/", methods=["GET", "POST"])
def index():
    global data, categories, photographers, services

    if request.method == "POST":
        action = request.form.get("action")

        if request.files.get("file"):
            file = request.files["file"]
            rows = parse_csv(file)

            for row in rows:
                try:
                    hours = float(row.get("Часы", 0))
                    price = float(row.get("Цена", 0))
                    row["Стоимость"] = hours * price
                except:
                    row["Стоимость"] = 0

                data.append(row)
                categories.add(row.get("Тип", ""))
                photographers.add(row.get("Фотограф", ""))
                services.add(row.get("Услуга", ""))

            return redirect(request.url)

        if action == "add":
            row = {k: clean_value(v) for k, v in request.form.items() if k != "action"}

            if all(row.values()):
                hours = float(row["Часы"])
                price = float(row["Цена"])
                row["Стоимость"] = hours * price

                data.append(row)
                categories.add(row["Тип"])
                photographers.add(row["Фотограф"])
                services.add(row["Услуга"])

            return redirect(request.url)

        if action in ["preview_report", "generate_report"]:
            filters = {
                k: v for k, v in request.form.items()
                if k in ["Дата_от", "Дата_до", "Фотограф", "Услуга", "Тип"] and v
            }

            filtered = filter_data(data, filters)
            

            if action == "generate_report":
                content = generate_csv(filtered)
                resp = make_response(content)
                resp.headers["Content-Disposition"] = "attachment; filename=report.csv"
                return resp

            stats = calculate_photographer_stats(filtered)
            return render_template(
                "index.html",
                data=filtered,
                categories=sorted(categories),
                photographers=sorted(photographers),
                services=sorted(services),
                columns=columns,
                totals=calculate_totals(filtered),
                photographer_stats=stats
            )
    stats = calculate_photographer_stats(data)
    return render_template(
        "index.html",
        data=data,
        categories=sorted(categories),
        photographers=sorted(photographers),
        services=sorted(services),
        columns=columns,
        totals=calculate_totals(data),
        photographer_stats=stats
    )


def filter_data(data, form):
    d_from = form.get("Дата_от", "")
    d_to = form.get("Дата_до", "")

    filters = {f: form[f] for f in ["Фотограф", "Услуга", "Тип"] if form.get(f)}

    return [
        r for r in data
        if (not d_from or r.get("Дата", "") >= d_from)
        and (not d_to or r.get("Дата", "") <= d_to)
        and all(r.get(f) == v for f, v in filters.items())
    ]


def calculate_totals(data):
    return {
        "Часы": sum(float(r.get("Часы", 0)) for r in data),
        "Стоимость": sum(float(r.get("Стоимость", 0)) for r in data)
    }


def generate_csv(data):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerows(data)
    return ("\ufeff" + output.getvalue()).encode("utf-8")

def calculate_photographer_stats(data):
    stats = {}
    for row in data:
        name = row.get("Фотограф", "")
        revenue = float(row.get("Стоимость", 0))

        if name not in stats:
            stats[name] = {
                "Фотограф": name,
                "Выручка": 0,
                "План": 100000
            }
        stats[name]["Выручка"] += revenue

    for s in stats.values():
        plan = s["План"]
        revenue = s["Выручка"]

        s["Выполнение"] = round((revenue / plan * 100) if plan else 0, 2)

    return list(stats.values())


if __name__ == "__main__":
    app.run(debug=True)