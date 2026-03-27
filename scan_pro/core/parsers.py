import re
import difflib
import unicodedata

# ─────────────────────────────────────────────────────────────────
# OCR Correction Helpers
# ─────────────────────────────────────────────────────────────────

def correct_numeric_ocr(text):
    text = re.sub(r'[,.\-]', '', text)
    mapping = {
        'O': '0', 'o': '0', 'D': '0', 'Q': '0', 'C': '0',
        'l': '1', 'I': '1', 'i': '1', '|': '1', ']': '1', '[': '1', 't': '1',
        'Z': '2', 'z': '2',
        'A': '4', 'h': '4',
        'S': '5', 's': '5',
        'G': '6', 'b': '6',
        'T': '7',
        'B': '8',
        'g': '9', 'q': '9', 'P': '9',
    }
    for k, v in mapping.items():
        text = text.replace(k, v)
    return text.replace(" ", "")

def correct_hex_ocr(text):
    mapping = {
        'O': '0', 'Q': '0', 'L': '1', 'I': '1', 'Z': '2', 'G': '6', 'T': '7', 'S': '5',
    }
    text = text.upper().replace(" ", "")
    for k, v in mapping.items():
        text = text.replace(k, v)
    text = re.sub(r'[^0-9A-F]', '', text)
    return text

def normalize_datetime(text):
    text = text.strip()
    text = re.sub(r'[,;|]', ':', text)
    text = re.sub(r'(\d{2})[-.](\d{2})[-.](\d{4})', r'\1/\2/\3', text)
    return text

# ─────────────────────────────────────────────────────────────────
# Parsing Logics
# ─────────────────────────────────────────────────────────────────

TYPE1_KEYWORDS = [
    "Thời gian vào trạm", "Thời gian ra trạm",
    "Biển số xe", "Id trạm vào", "Id trạm ra",
    "Trạng thái", "RFID",
    "Mã giao dịch", "Đơn vị"
]

def detect_receipt_type(text):
    text_lower = text.lower()
    if "timemark" in text_lower or re.search(r'\d{1,2}\s*(tháng|thang|thg)\s*\d{1,2}', text_lower):
        return "type3_photo"

    datetime_pattern = re.compile(r'\d{1,2}:\d{2}')
    url_pattern = re.compile(r'https?://|www\.')

    kv_lines = 0
    total_lines = 0
    for l in text.splitlines():
        ls = l.strip()
        if not ls: continue
        total_lines += 1
        if ':' in ls and len(ls) < 80:
            colon_idx = ls.index(':')
            before_colon = ls[:colon_idx].strip()
            if (re.search(r'[a-zA-ZàáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđĐ]', before_colon)
                    and not datetime_pattern.search(before_colon)
                    and not url_pattern.search(ls)
                    and len(before_colon) > 1):
                kv_lines += 1

    if total_lines > 0 and kv_lines / total_lines > 0.35:
        return "type2_vetc"

    type1_score = sum(1 for kw in TYPE1_KEYWORDS if kw.lower() in text_lower)
    if type1_score >= 2:
        return "type1_web"

    return "type2_vetc"


def parse_type3_photo(text):
    data = {}
    plate_pattern = r'\b(\d{2}[A-Z]\d?\s*[-–]\s*\d{3,4}[.\s]?\d{1,3}[A-Z]?)\b'
    for line in text.splitlines():
        m = re.search(plate_pattern, line, re.IGNORECASE)
        if m:
            data["Biển số"] = m.group(1).strip()
            break
            
    date_pattern = r'(\d{1,2})\s*(?:Tháng|Thang|Thg|tháng|thang|thg)\s*(\d{1,2})[,\.\s]*(\d{4})'
    for line in text.splitlines():
        m_date = re.search(date_pattern, line, re.IGNORECASE)
        if m_date:
            day, month, year = m_date.groups()
            data["Thời gian vào"] = f"{year}-{int(month):02d}-{int(day):02d}"
            break
    return data

def parse_type1_web(text):
    data = {}
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    label_map = {
        "mã giao dịch": "Mã giao dịch", "trạng thái": "Trạng thái",
        "biển số xe": "Biển số", "biển số": "Biển số",
        "rfid": "EPC", "epc": "EPC",
        "id trạm vào": "Id trạm vào", "id trạm ra": "Id trạm ra",
        "trạm vào": "Trạm vào", "trạm ra": "Trạm ra",
        "thời gian vào trạm": "Thời gian vào", "thời gian vào": "Thời gian vào",
        "thời gian ra trạm": "Thời gian ra", "thời gian ra": "Thời gian ra",
        "làn vào": "Làn vào", "làn ra": "Làn ra",
        "đơn vị": "Đơn vị", "giá tiền": "Giá tiền", "loại vé": "Loại vé",
    }

    all_label_keys = list(label_map.keys())
    i = 0
    while i < len(lines):
        line = lines[i]
        line_lower = line.lower()
        matched_key = None
        for label_norm, field_name in label_map.items():
            if label_norm in line_lower:
                matched_key = field_name
                break

        if matched_key is None:
            clean = re.sub(r'[^a-z0-9àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ\s]', '', line_lower).strip()
            fuzzy_matches = difflib.get_close_matches(clean, all_label_keys, n=1, cutoff=0.75)
            if fuzzy_matches:
                matched_key = label_map[fuzzy_matches[0]]

        if matched_key and i + 1 < len(lines):
            j = i + 1
            while j < len(lines):
                frag = lines[j].strip()
                frag_lower = frag.lower()
                is_fragment = (
                    len(frag.split()) <= 3
                    and not re.search(r'\d', frag)
                    and len(frag) <= 20
                    and any(frag_lower in lbl for lbl in label_map.keys())
                )
                if is_fragment: j += 1
                else: break

            if j < len(lines):
                value_line = lines[j]
                value_lower = value_line.lower()
                is_value_a_label = any(lbl in value_lower for lbl in label_map.keys())
                if not is_value_a_label and value_line not in data.values():
                    data[matched_key] = value_line
                    i = j + 1
                    continue

        if matched_key:
            inline_match = re.split(r'[:;]\s*', line, maxsplit=1)
            if len(inline_match) == 2 and inline_match[1].strip():
                val = inline_match[1].strip()
                if val.lower() not in label_map:
                    data[matched_key] = val
                    i += 1
                    continue

        i += 1
    return data

def parse_type2_vetc(text):
    data = {}
    heuristics = {
        "Mã giao dịch":  [r"M[ãa\*]?[ \t]*giao[ \t]*d[ịi]ch", r"M[ãa][ \t]*GD", r"M[ãa][ \t]*v[eé]"],
        "Trạng thái":    [r"Tr[ạa]ng[ \t]*th[áa]i", r"T[ìi]nh[ \t]*tr[ạa]ng"],
        "Biển số":       [
            r"Bi[eêếềệểễ][nń][ \t]*s[oôốồổỗộ][ \t]*xe?",
            r"Bi[eê]n[ \t]*so", r"\bBKS\b", r"\bBSX\b", r"Bi[ểe]n[ \t]*ki[eê]m",
        ],
        "EPC":           [r"\bEPC\b", r"\bRFID\b", r"M[ãa][ \t]*th[ẻe]"],
        "TG vào":        [r"TG[ \t]*v[àa]o", r"Gi[ờo][ \t]*v[àa]o", r"Th[ờo]i[ \t]*gian[ \t]*v[àa]o"],
        "Id trạm vào":   [r"Id[ \t]*tr[ạa]m[ \t]*v[àa]o"],
        "Trạm vào":      [r"Tr[ạa]m[ \t]*v[àa]o"],
        "Làn vào":       [r"L[àa]n[ \t]*v[àa]o"],
        "TG ra":         [r"TG[ \t]*[Rr]a\b", r"Gi[ờo][ \t]*[Rr]a\b", r"Th[ờo]i[ \t]*gian[ \t]*[Rr]a\b"],
        "Id trạm ra":    [r"Id[ \t]*tr[ạa]m[ \t]*ra"],
        "Trạm ra":       [r"Tr[ạa]m[ \t]*ra"],
        "Làn ra":        [r"L[àa]n[ \t]*ra\b"],
        "Loại vé":       [r"Lo[ạa]i[ \t]*v[eé]"],
        "Giá tiền":      [r"Gi[áa][ \t]*ti[ềe]n", r"S[ốo][ \t]*ti[ềe]n", r"T[ổo]ng[ \t]*c[ộo]ng"],
        "Đơn vị":        [r"[ĐDd][ơo]n[ \t]*v[ịi]", r"\bBoo\b"],
    }
    delimiter = r"[\s]*[:;.\-][\s]*"
    for field_name, patterns in heuristics.items():
        for pattern in patterns:
            full_pattern = r"^[^\w\n]*" + pattern + delimiter + r"(.+)"
            match = re.search(full_pattern, text, re.IGNORECASE | re.MULTILINE | re.UNICODE)
            if match:
                val = match.group(1).strip()
                val = re.sub(r'\s*[|\\].*$', '', val).strip()
                if val:
                    data[field_name] = val
                    break
    return data

def post_process_data(data, receipt_type):
    numeric_fields = ["Mã giao dịch", "Id trạm vào", "Id trạm ra", "Giá tiền", "Làn vào", "Làn ra", "Loại vé"]
    for field in numeric_fields:
        if field in data and data[field]:
            data[field] = correct_numeric_ocr(data[field])

    for epc_field in ["EPC", "RFID"]:
        if epc_field in data and data[epc_field]:
            data[epc_field] = correct_hex_ocr(data[epc_field])

    if "Biển số" in data:
        bs = data["Biển số"].strip()
        if len(bs) >= 3:
            prefix = correct_numeric_ocr(bs[:2])
            data["Biển số"] = prefix + bs[2:]

    for time_field in ["TG vào", "TG ra", "Thời gian vào", "Thời gian ra"]:
        if time_field in data:
            data[time_field] = normalize_datetime(data[time_field])

    return data

def parse_receipt(text):
    text = unicodedata.normalize('NFC', text)
    receipt_type = detect_receipt_type(text)

    if receipt_type == "type1_web":
        data = parse_type1_web(text)
    elif receipt_type == "type3_photo":
        data = parse_type3_photo(text)
    else:
        data = parse_type2_vetc(text)

    if len(data) < 3 and receipt_type != "type3_photo":
        if receipt_type == "type1_web":
            data_alt = parse_type2_vetc(text)
            if len(data_alt) > len(data):
                data = data_alt
                receipt_type = "type2_vetc"
        else:
            data_alt = parse_type1_web(text)
            if len(data_alt) > len(data):
                data = data_alt
                receipt_type = "type1_web"

    if "Biển số" not in data:
        plate_pattern = r'\b(\d{2}[A-Z]\d?\s*[-–]\s*\d{3,4}[.\s]?\d{1,3}[A-Z]?)\b'
        for line in text.splitlines():
            m = re.search(plate_pattern, line, re.IGNORECASE)
            if m:
                data["Biển số"] = m.group(1).strip()
                break

    data = post_process_data(data, receipt_type)
    return data, receipt_type
