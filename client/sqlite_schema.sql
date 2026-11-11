-- 客户端 SQLite（私有目录），privacy: 仅本地缓存，无敏感信息
PRAGMA user_version = 1;

CREATE TABLE IF NOT EXISTS reports (
    date         TEXT PRIMARY KEY,           -- YYYY-MM-DD
    generated_at TEXT,
    item_count   INTEGER NOT NULL DEFAULT 0,
    fetched_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id           TEXT PRIMARY KEY,           -- YYYY-MM-DD-NN
    date         TEXT NOT NULL REFERENCES reports(date) ON DELETE CASCADE,
    category     TEXT NOT NULL CHECK (category IN ('民生','经济金融','科技产业','安全国防','政务法治','其他')),
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT '',
    url          TEXT NOT NULL DEFAULT '',
    publish_date TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_date_cat ON items(date, category);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
