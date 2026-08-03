from sirius_pulse.cli import _get_active_persona_names


def test_active_personas_prefers_the_multi_persona_list_and_deduplicates() -> None:
    assert _get_active_persona_names(
        {"active_persona": "first", "active_personas": ["second", "first", "second", ""]}
    ) == ["second", "first"]


def test_active_personas_falls_back_to_the_legacy_single_persona_setting() -> None:
    assert _get_active_persona_names({"active_persona": "first"}) == ["first"]
