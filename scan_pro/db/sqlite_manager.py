import sqlite3
from datetime import datetime

DB_PATH = 'receipts_pro.db'

def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_time TEXT,
                receipt_type TEXT,
                transaction_code TEXT,
                license_plate TEXT,
                price TEXT,
                status TEXT,
                epc TEXT,
                time_in TEXT,
                station_in TEXT,
                station_in_id TEXT,
                lane_in TEXT,
                time_out TEXT,
                station_out TEXT,
                station_out_id TEXT,
                lane_out TEXT,
                ticket_type TEXT,
                unit TEXT,
                raw_text TEXT
            )
        ''')
        # Migrate columns
        new_columns = [
            ("receipt_type", "TEXT"),
            ("station_out",  "TEXT"),
            ("lane_out",     "TEXT"),
            ("ticket_type",  "TEXT"),
            ("unit",         "TEXT"),
        ]
        existing = {row[1] for row in cursor.execute("PRAGMA table_info(scans)")}
        for col_name, col_type in new_columns:
            if col_name not in existing:
                cursor.execute(f"ALTER TABLE scans ADD COLUMN {col_name} {col_type}")
        conn.commit()
    except Exception as e:
        print(f"DB Init Error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

def save_to_db(data, raw_text, receipt_type="unknown"):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO scans (
                scan_time, receipt_type, transaction_code, license_plate, price, status, epc,
                time_in, station_in, station_in_id, lane_in,
                time_out, station_out, station_out_id, lane_out,
                ticket_type, unit, raw_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            receipt_type,
            data.get('Mã giao dịch', ''),
            data.get('Biển số', ''),
            data.get('Giá tiền', ''),
            data.get('Trạng thái', ''),
            data.get('EPC', data.get('RFID', '')),
            data.get('TG vào', data.get('Thời gian vào', '')),
            data.get('Trạm vào', ''),
            data.get('Id trạm vào', ''),
            data.get('Làn vào', ''),
            data.get('TG ra', data.get('Thời gian ra', '')),
            data.get('Trạm ra', ''),
            data.get('Id trạm ra', ''),
            data.get('Làn ra', ''),
            data.get('Loại vé', ''),
            data.get('Đơn vị', ''),
            raw_text
        ))
        conn.commit()
    except Exception as e:
        print(f"DB Save Error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

def get_recent_scans(limit=100):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM scans ORDER BY scan_time DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        print(f"DB Read Error: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()

def delete_scan(scan_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
        conn.commit()
    except Exception as e:
        print(f"DB Delete Error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

def update_scan(scan_id, plate, price, status):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE scans SET license_plate = ?, price = ?, status = ? WHERE id = ?
        ''', (plate, price, status, scan_id))
        conn.commit()
    except Exception as e:
        print(f"DB Update Error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()
