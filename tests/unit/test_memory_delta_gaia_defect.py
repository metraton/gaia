"""A specialist's memory_delta may propose a Gaia defect under the host initiative, the shape the memory skill teaches."""

import pytest

from gaia.store.proposals import refusal_message


def _envelope(**create):
    base = {
        "slug": "feedback_release_rc_reached_main_20261107",
        "type": "feedback",
        "description": "An RC publish to main reached every marketplace user",
        "body": "Symptom, component, evidence and reproduction.",
    }
    return {"memory_delta": {"version": 1, "proposals": [{"operation": "create", "create": {**base, **create}}]}}


@pytest.mark.parametrize("kind", ["defect", "lesson"])
def test_a_gaia_defect_or_lesson_under_gaia_system_is_accepted(kind):
    assert refusal_message(_envelope(kind=kind, initiative="gaia_system", **{"class": "thread"})) is None


def test_the_free_shape_carries_the_initiative_too():
    envelope = {"memory_delta": {"proposals": [{
        "name": "feedback_release_rc_reached_main_20261107", "type": "feedback", "kind": "defect",
        "initiative": "gaia_system", "description": "An RC reached main", "body": "Evidence."}]}}
    assert refusal_message(envelope) is None


def test_a_defect_without_the_host_initiative_still_lands_on_the_project_and_is_refused():
    assert "defect" in refusal_message(_envelope(kind="defect"))


def test_a_project_kind_under_gaia_system_is_refused():
    assert "gaia owns lessons and defects only" in refusal_message(_envelope(kind="pending", initiative="gaia_system"))
