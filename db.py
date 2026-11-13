# db.py : SQLite（bot.db）。表はここで全部作る
import sqlite3

import config

con = sqlite3.connect(config.DB_PATH)
con.row_factory = sqlite3.Row
con.executescript("""
create table if not exists seen(id text primary key);
-- 📌=1 / 👎=0。あとで好みの学習に使う
create table if not exists feedback(id text primary key, label int, title text, abstract text,
                                    at text default current_timestamp);
create table if not exists papers(page text primary key, pid text, thread_id int, status text default '積読');
create table if not exists reminders(id integer primary key, user_id int, channel_id int, text text,
                                     due text, repeat text, done int default 0);
create table if not exists tasks(id integer primary key, text text, day text, done_day text,
                                 deleted int default 0, created text default current_timestamp);
create table if not exists habits(id integer primary key, name text unique, days text default '0123456',
                                  active int default 1);
create table if not exists habit_log(habit_id int, day text, primary key(habit_id, day));
create table if not exists boards(day text primary key, channel_id int, message_id int);
create table if not exists expenses(id integer primary key, day text, amount int, category text, store text,
                                    memo text, image text, ocr text, created text default current_timestamp);
create table if not exists store_cat(store text primary key, category text);
create table if not exists cosense_meta(title text primary key, updated int);
create table if not exists url_titles(url text primary key, title text);
-- 全文検索。trigram なので日本語も部分一致で探せる（3文字未満の語は LIKE で探す）
create virtual table if not exists docs using fts5(title, body, source unindexed, ref unindexed,
                                                   url unindexed, at unindexed, tokenize='trigram');
""")
con.commit()


def q(sql, *args):
    return con.execute(sql, args).fetchall()


def one(sql, *args):
    return con.execute(sql, args).fetchone()


def run(sql, *args):
    cur = con.execute(sql, args)
    con.commit()
    return cur.lastrowid
