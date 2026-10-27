from scholarmotion.agents.manager import Manager, RepairAction


def test_simplified_fallback_receives_a_final_render_attempt() -> None:
    manager = Manager()

    assert manager.repair_action(4) is RepairAction.SIMPLIFY
    assert manager.max_repair_attempts >= 5
    assert manager.repair_action(5) is RepairAction.BLOCK
