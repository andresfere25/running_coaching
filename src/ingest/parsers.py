import re
from typing import Any, Optional, Tuple, List

def norm_str(x: Any) -> str:
    if x is None:
        return ""
    s = str(x).strip()
    return re.sub(r"\s+", " ", s)

def parse_yes_no(x: Any) -> Optional[bool]:
    s = norm_str(x).lower()
    if s in ("sí", "si", "s", "yes", "y", "true", "1"):
        return True
    if s in ("no", "n", "false", "0"):
        return False
    if s in ("no sé", "no se", "ns", ""):
        return None
    return None

def parse_int(x: Any) -> Optional[int]:
    s = norm_str(x)
    if s == "" or s.lower() in ("no sé", "no se"):
        return None
    try:
        return int(float(s))
    except:
        return None

def parse_float(x: Any) -> Optional[float]:
    s = norm_str(x).replace(",", ".")
    if s == "" or s.lower() in ("no sé", "no se"):
        return None
    m = re.search(r"-?\d+(\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except:
        return None
def parse_range_km_week(x: Any) -> Tuple[Optional[float], Optional[float]]:
    """
    Soporta:
      - '10–20', '10-20', '10 a 20', '10/20', '10 20'
      - '10' (devuelve 10,10)
      - '10–20 km', '10-20kms'
      - 'No sé' / vacío
    """
    s = norm_str(x).replace("–", "-").lower()
    if s in ("", "no sé", "no se"):
        return (None, None)

    # Extraer números sin grupos capturantes (clave)
    nums = re.findall(r"\d+(?:[.,]\d+)?", s)

    # Convertir coma a punto si aparece
    nums = [n.replace(",", ".") for n in nums if n and n.strip()]

    if len(nums) >= 2:
        a, b = float(nums[0]), float(nums[1])
        return (min(a, b), max(a, b))
    if len(nums) == 1:
        v = float(nums[0])
        return (v, v)
    return (None, None)



def parse_time_to_seconds(x: Any) -> Optional[int]:
    s = norm_str(x)
    if s == "" or s.lower() in ("no sé", "no se", "ns"):
        return None
    parts = s.split(":")
    try:
        parts = [int(p) for p in parts]
    except:
        return None
    if len(parts) == 3:
        h, m, sec = parts
        return h * 3600 + m * 60 + sec
    if len(parts) == 2:
        mm, ss = parts
        return mm * 60 + ss
    if len(parts) == 1:
        return parts[0] * 60
    return None

def parse_minutes_range(x: Any) -> Tuple[Optional[int], Optional[int]]:
    s = norm_str(x).replace("–", "-").lower()
    if s in ("", "no sé", "no se"):
        return (None, None)
    nums = re.findall(r"\d+", s)
    if len(nums) >= 2:
        a, b = int(nums[0]), int(nums[1])
        return (min(a, b), max(a, b))
    if len(nums) == 1:
        v = int(nums[0])
        return (v, v)
    return (None, None)

def parse_multi_select(x: Any) -> List[str]:
    s = norm_str(x)
    if s == "" or s.lower() in ("no sé", "no se"):
        return []
    return [p.strip() for p in s.split(",") if p.strip()]
