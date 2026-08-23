"""Isolated agent-evaluation dataset generator.

This package is not part of the Akrasia_Zero application runtime.
It writes only to the dedicated evaluation database.

Synthetic scenarios may contain internal planted structure that is
not exposed through the production evidence contract. Those facts
remain useful for validating the scenario generator. An agent must
only be evaluated against conclusions supported by the evidence it
actually receives.
"""