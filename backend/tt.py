from datetime import date, timedelta
from app.agents.weather_agent import get_weather, itinerary_notes, coverage
s = date.today() + timedelta(days=14)
w = get_weather('Rotorua', s, s + timedelta(days=4))
print('coverage:', '%d/%d' % coverage(w))
for n in itinerary_notes(w): print(' •', n)