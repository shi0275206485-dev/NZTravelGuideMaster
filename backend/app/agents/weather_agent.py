"""WeatherQueryAgent.

Fetches the daily forecast for a trip and turns it into per-day guidance
the planner can act on.

No LLM is involved. The useful judgement here — is this a day to be
indoors? — is a threshold on a precipitation probability, and a rule states
that more reliably and more cheaply than a model would. The agent's value
is in the degradation path, not in reasoning.

Open-Meteo publishes a 16-day forecast window (confirmed in Phase 1
validation). Trips beyond it are planned without weather adjustment, and
the itinerary says so rather than quietly presenting an unweighted plan as
a weather-aware one.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx

from ..cache import Cache, cached_fetch
from ..config import get_settings
from ..models import DESTINATION_CONFIG, WeatherInfo

logger = logging.getLogger(__name__)

WEATHER_NAMESPACE = "weather"

# Open-Meteo's published horizon. Requests beyond it return fewer days
# rather than an error, so the boundary is enforced here.
FORECAST_WINDOW_DAYS = 16

# Above this chance of rain a day is treated as wet, which shifts the
# planner toward indoor options. Set from what the number means to a
# traveller rather than from the data: below roughly half, an outdoor day
# is still the better bet and a forecast that hedges should not rewrite
# someone's trip.
WET_DAY_THRESHOLD = 50

# ...but probability alone contradicted itself in testing: a day forecast as
# "rain showers" at 19% was reported dry. A code that names rain is a
# statement that rain is expected, so a lower bar applies when one is
# present — showers at 30% still argue for keeping an indoor option in hand.
WET_CODE_THRESHOLD = 30
RAIN_CODES = frozenset(
    list(range(51, 68)) + list(range(80, 83)) + list(range(95, 100))
)

# Cached for six hours: long enough that repeated planning of the same trip
# costs one call, short enough that a forecast does not go stale within a
# session.
WEATHER_TTL_S = 6 * 3600

# WMO weather interpretation codes, condensed to what a traveller needs.
WEATHER_CODES: dict[int, str] = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Rain showers", 81: "Rain showers", 82: "Heavy rain showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail",
    99: "Thunderstorm with hail",
}


def describe_code(code: int | None) -> str:
    if code is None:
        return "Unknown"
    return WEATHER_CODES.get(code, "Mixed conditions")


def within_forecast_window(start: date, today: date | None = None) -> bool:
    reference = today or date.today()
    return (start - reference).days <= FORECAST_WINDOW_DAYS


def _fetch_forecast(lat: float, lon: float) -> dict:
    """Fetch the whole forecast window for a location.

    Asks for a window length rather than a date range. Requesting an
    explicit start_date/end_date more than about a week out is rejected
    with a 400, and the two parameter styles cannot be combined — which is
    how weather silently vanished for every trip booked more than a week
    ahead, the normal case for trip planning.

    Fetching the full window and filtering locally also means one cached
    response serves every trip to that destination whatever its dates.
    """
    settings = get_settings()
    response = httpx.get(
        settings.open_meteo_url,
        params={
            "latitude": lat,
            "longitude": lon,
            "daily": ("weathercode,temperature_2m_max,temperature_2m_min,"
                      "precipitation_probability_max"),
            "timezone": "Pacific/Auckland",
            "forecast_days": FORECAST_WINDOW_DAYS,
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def _unavailable(reason: str) -> WeatherInfo:
    """A day with no forecast, carrying the reason for the user."""
    return WeatherInfo(available=False, summary=reason, is_wet=False)


def get_weather(
    destination: str,
    start_date: date,
    end_date: date,
    cache: Cache | None = None,
) -> dict[int, WeatherInfo]:
    """Per-day forecast, keyed by day number (1-based).

    Always returns an entry for every day of the trip. Days outside the
    forecast window, or every day if the service is unreachable, come back
    marked unavailable rather than missing, so the planner never has to
    distinguish "no rain expected" from "no data".
    """
    num_days = (end_date - start_date).days + 1
    config = DESTINATION_CONFIG.get(destination)
    if config is None:
        return {d: _unavailable("Unsupported destination") for d in range(1, num_days + 1)}

    if not within_forecast_window(start_date):
        logger.info("trip to %s starts beyond the forecast window", destination)
        return {
            d: _unavailable(
                f"Beyond the {FORECAST_WINDOW_DAYS}-day forecast window — "
                "planned without weather adjustment"
            )
            for d in range(1, num_days + 1)
        }

    store = cache or Cache(get_settings().cache_db_path)
    # Keyed on destination alone: the response covers the whole window, so
    # every trip to the same place shares one cached forecast.
    key = destination

    try:
        payload = cached_fetch(
            store,
            WEATHER_NAMESPACE,
            key,
            lambda: _fetch_forecast(config["lat"], config["lon"]),
            ttl_s=WEATHER_TTL_S,
        )
    except Exception as exc:
        # A missing forecast degrades the itinerary; it does not invalidate
        # it. Everything else about the trip is still plannable.
        logger.warning("weather lookup failed for %s: %s", destination, exc)
        return {
            d: _unavailable("Forecast unavailable — planned without weather adjustment")
            for d in range(1, num_days + 1)
        }

    return _parse_daily(payload, start_date, num_days)


def _parse_daily(payload: dict, start_date: date, num_days: int) -> dict[int, WeatherInfo]:
    daily = payload.get("daily") or {}
    dates: list[str] = daily.get("time") or []
    by_date = {d: i for i, d in enumerate(dates)}

    result: dict[int, WeatherInfo] = {}
    for offset in range(num_days):
        day_number = offset + 1
        iso = (start_date + timedelta(days=offset)).isoformat()
        index = by_date.get(iso)

        if index is None:
            # The window ended partway through the trip.
            result[day_number] = _unavailable(
                "Beyond the forecast window — planned without weather adjustment"
            )
            continue

        precipitation = _at(daily.get("precipitation_probability_max"), index)
        code = _at(daily.get("weathercode"), index)
        is_wet = _is_wet_day(code, precipitation)

        result[day_number] = WeatherInfo(
            available=True,
            temp_min_c=_at(daily.get("temperature_2m_min"), index),
            temp_max_c=_at(daily.get("temperature_2m_max"), index),
            precipitation_probability=precipitation,
            summary=_summarise(code, precipitation, is_wet),
            is_wet=is_wet,
        )
    return result


def _is_wet_day(code: int | None, precipitation: int | None) -> bool:
    """Whether to steer the day indoors, from both signals together."""
    if precipitation is None:
        return code in RAIN_CODES if code is not None else False
    if precipitation >= WET_DAY_THRESHOLD:
        return True
    return code in RAIN_CODES and precipitation >= WET_CODE_THRESHOLD


def _at(values: list | None, index: int):
    if not values or index >= len(values):
        return None
    return values[index]


def _summarise(code: int | None, precipitation: int | None, is_wet: bool) -> str:
    description = describe_code(code)
    if precipitation is None:
        return description
    if is_wet:
        return f"{description}, {precipitation}% chance of rain — favour indoor options"
    return f"{description}, {precipitation}% chance of rain"


def coverage(weather: dict[int, WeatherInfo]) -> tuple[int, int]:
    """(days with a forecast, days in the trip)."""
    return sum(1 for w in weather.values() if w.available), len(weather)


def coverage_notice(weather: dict[int, WeatherInfo]) -> str:
    """Tell the traveller which days the weather actually informed.

    Partial coverage is the normal case, not an edge case: the forecast
    runs 16 days out and people plan further ahead than that, so a
    five-day trip booked three weeks early gets weather on none of its
    days, and one booked a fortnight early gets it on the first two.

    Saying so matters. A plan that silently mixes weather-adjusted days
    with unadjusted ones looks identical to one that considered the
    forecast throughout, and a traveller who assumes the latter will pack
    for the wrong trip. This string is surfaced in the itinerary rather
    than logged.
    """
    if not weather:
        return ""

    available, total = coverage(weather)
    if available == total:
        return ""

    if available == 0:
        return (
            f"These dates are beyond the {FORECAST_WINDOW_DAYS}-day forecast "
            "window, so this itinerary was planned without weather adjustment."
        )

    covered = sorted(d for d, w in weather.items() if w.available)
    uncovered = sorted(d for d, w in weather.items() if not w.available)
    return (
        f"Forecast covers {_day_phrase(covered)} only. "
        f"{_day_phrase(uncovered, capitalise=True)} "
        f"{'was' if len(uncovered) == 1 else 'were'} planned without weather "
        "adjustment, being beyond the "
        f"{FORECAST_WINDOW_DAYS}-day forecast window."
    )


def _day_phrase(days: list[int], capitalise: bool = False) -> str:
    """Render day numbers as "day 3", "days 1 and 2", or "days 3-5"."""
    if not days:
        return ""
    if len(days) == 1:
        phrase = f"day {days[0]}"
    elif days == list(range(days[0], days[-1] + 1)) and len(days) > 2:
        phrase = f"days {days[0]}–{days[-1]}"
    elif len(days) == 2:
        phrase = f"days {days[0]} and {days[1]}"
    else:
        phrase = "days " + ", ".join(str(d) for d in days[:-1]) + f" and {days[-1]}"
    return phrase[0].upper() + phrase[1:] if capitalise else phrase


def itinerary_notes(weather: dict[int, WeatherInfo]) -> list[str]:
    """Weather lines for TripPlan.notes, in the order they should be shown.

    Coverage first: whether the forecast applied at all frames how much
    weight to give what follows.
    """
    notes = []
    notice = coverage_notice(weather)
    if notice:
        notes.append(notice)
    summary = summarise_trip(weather)
    if summary:
        notes.append(summary)
    return notes


def summarise_trip(weather: dict[int, WeatherInfo]) -> str:
    """One line on what the forecast means for the trip, where there is one."""
    if not weather or not any(w.available for w in weather.values()):
        return ""
    wet_days = sorted(d for d, w in weather.items() if w.is_wet)
    if not wet_days:
        # "Dry" is a strong claim; soften it if any day mentions rain at all,
        # even below the threshold that would reshape the itinerary.
        showery = any(
            w.available and (w.precipitation_probability or 0) >= 15
            for w in weather.values()
        )
        return (
            "Some showers possible, but no day looks wet enough to plan around."
            if showery
            else "Forecast is dry across the trip."
        )
    return (f"Rain likely on {_day_phrase(wet_days)} — "
            "indoor options favoured there.")
