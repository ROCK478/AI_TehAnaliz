CREATE TABLE stock_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    old_price REAL,
    new_price REAL,
    change_value REAL,
    percent_change REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);