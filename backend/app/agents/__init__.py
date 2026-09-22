"""Specialised agents.

Each owns one domain decision, returns a structured result, and degrades
on its own rather than raising — the pipeline treats all four alike.

Two are model-driven, because their judgement is semantic and no rule
expresses it: which places suit this traveller, and how a set of places
becomes a sequence of days.

Two are deterministic, because theirs is not. Whether a day is wet is a
threshold; whether a hotel suits is arithmetic over distance, type and
price. Routing those through a model would add latency and a hallucination
surface in exchange for a worse answer.
"""