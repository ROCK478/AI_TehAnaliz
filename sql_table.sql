CREATE TABLE stock_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    old_price REAL,
    new_price REAL,
    change_value REAL,
    percent_change REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE orders (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    status      VARCHAR(50) NOT NULL DEFAULT 'pending',
    total_price NUMERIC(10, 2) NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);