"""Optional offline Vedic sidereal coordinates for research timestamps.

Astrological association is NOT evidence of gamma causation or predictability.
Uses Swiss Ephemeris Moshier analytical mode: no remote API or ephemeris download.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
RASHIS = ("Mesha", "Vrishabha", "Mithuna", "Karka", "Simha", "Kanya",
          "Tula", "Vrishchika", "Dhanu", "Makara", "Kumbha", "Meena")
NAKSHATRAS = ("Ashwini", "Bharani", "Krittika", "Rohini", "Mrigashira",
              "Ardra", "Punarvasu", "Pushya", "Ashlesha", "Magha",
              "Purva Phalguni", "Uttara Phalguni", "Hasta", "Chitra",
              "Swati", "Vishakha", "Anuradha", "Jyeshtha", "Mula",
              "Purva Ashadha", "Uttara Ashadha", "Shravana", "Dhanishta",
              "Shatabhisha", "Purva Bhadrapada", "Uttara Bhadrapada",
              "Revati")


def _position(longitude, speed=None):
    value = float(longitude) % 360
    within = value % 30
    deg = int(within)
    minute = int((within - deg) * 60)
    span = 360 / 27
    nakshatra_index = min(26, int(value / span))
    pada = min(4, int((value % span) / (span / 4)) + 1)
    result = {
        "rashi": RASHIS[int(value // 30)],
        "rashi_degree": deg,
        "rashi_arcminute": minute,
        "sidereal_longitude_degrees": round(value, 6),
        "nakshatra": NAKSHATRAS[nakshatra_index],
        "nakshatra_pada": pada,
    }
    if speed is not None:
        result["retrograde"] = float(speed) < 0
    return result


@lru_cache(maxsize=8192)
def _positions_at_ist(iso_minute):
    try:
        import swisseph as swe
    except ImportError as exc:
        raise RuntimeError(
            "Vedic positions require optional pyswisseph: python -m pip install pyswisseph"
        ) from exc
    stamp = datetime.fromisoformat(iso_minute)
    if stamp.tzinfo is None:
        raise ValueError("Expected timezone-aware IST timestamp")
    utc = stamp.astimezone(timezone.utc)
    hour = (utc.hour + utc.minute / 60 + utc.second / 3600
            + utc.microsecond / 3_600_000_000)
    jd = swe.julday(utc.year, utc.month, utc.day, hour)
    swe.set_sid_mode(swe.SIDM_LAHIRI)
    flags = swe.FLG_SIDEREAL | swe.FLG_MOSEPH | swe.FLG_SPEED
    bodies = {
        "Sun": swe.SUN, "Moon": swe.MOON, "Mars": swe.MARS,
        "Mercury": swe.MERCURY, "Jupiter": swe.JUPITER,
        "Venus": swe.VENUS, "Saturn": swe.SATURN,
        "Rahu": swe.MEAN_NODE,
    }
    planets = {}
    for name, body in bodies.items():
        xx, retflags = swe.calc_ut(jd, body, flags)
        if not (retflags & swe.FLG_SIDEREAL):
            raise RuntimeError("Swiss Ephemeris did not return sidereal coordinates")
        planets[name] = _position(xx[0], xx[3])
    rahu = planets["Rahu"]["sidereal_longitude_degrees"]
    planets["Ketu"] = _position(
        rahu + 180, -1.0 if planets["Rahu"].get("retrograde") else 1.0)
    nakshatra_index = NAKSHATRAS.index(planets["Moon"]["nakshatra"])
    pada = planets["Moon"]["nakshatra_pada"]
    return {
        "timestamp_ist": iso_minute,
        "zodiac": "Vedic sidereal Lahiri",
        "calculation": "Swiss Ephemeris Moshier (offline analytical mode)",
        "rahu_ketu_policy": "Mean Rahu; Ketu exactly opposite",
        "moon_nakshatra": NAKSHATRAS[nakshatra_index],
        "moon_nakshatra_pada": pada,
        "planets": planets,
    }


def sidereal_positions(ist_timestamp, *, precision="minutes"):
    """At a completed minute-bar timestamp, return Vedic planet/Rashi positions.

    Accepts pandas.Timestamp or datetime or ISO string. Naive stamps are assumed
    IST because the Dhan parser normalizes UTC epoch candles to IST-naive.
    A minute timestamp cannot reveal a precise second of an intrabar crossing.
    """
    if isinstance(ist_timestamp, str):
        stamp = datetime.fromisoformat(ist_timestamp)
    elif hasattr(ist_timestamp, "to_pydatetime"):
        stamp = ist_timestamp.to_pydatetime()
    else:
        stamp = ist_timestamp
    if not isinstance(stamp, datetime):
        raise ValueError("Expected datetime or ISO timestamp")
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=IST)
    stamp = stamp.astimezone(IST)
    if precision not in ("minutes", "seconds"):
        raise ValueError("precision must be minutes or seconds")
    return _positions_at_ist(stamp.isoformat(timespec=precision))
