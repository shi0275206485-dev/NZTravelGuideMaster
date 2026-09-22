from datetime import date
from app.models import TripRequest
from backend.app.agents.attraction_agent_v1 import search_attractions
req = TripRequest(destination='Rotorua', 
                  start_date=date(2026,8,25), end_date=date(2026,8,27), preferences=['nature','family'], free_text='travelling with a 5-year-old')
for r in search_attractions(req): print(r.rank, r.attraction.name, '—', r.reason)