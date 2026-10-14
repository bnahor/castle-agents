from __future__ import annotations

from castle_agents.domain import Addition, Steering


def test_a_closed_or_superseded_inbox_refuses_delivery():
    inbox = Steering(generation=2)
    assert not inbox.push(Addition("m1", "include INTC"), generation=1)
    assert inbox.push(Addition("m2", "include AVGO"), generation=2)
    inbox.close()
    assert not inbox.push(Addition("m3", "include AMD"), generation=2)
    assert inbox.drain() == []
