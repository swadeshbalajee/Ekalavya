"""The planner model decides what a chat turn means; code only validates the decision."""

from scholarmotion.agents.lesson_preparation import fallback_preparation, prepare_lesson

SCENES = [
    {"id": "S01", "start": 0, "end": 30, "title": "Forces come in pairs"},
    {"id": "S02", "start": 30, "end": 61, "title": "Two skaters"},
]


class Planner:
    name = "test"

    def __init__(self, **decision):
        self.decision = decision
        self.prompt = ""

    async def generate_structured(self, prompt, schema, *, temperature=None):
        self.prompt = prompt
        return schema(**self.decision)


async def _plan(planner, **kwargs):
    defaults = {
        "message": "anything",
        "project_request": "Explain Newton's third law.",
        "conversation": "",
        "account_text_language": "English",
        "account_voice_language": "English",
        "project_text_language": "English",
        "project_voice_language": "English",
    }
    return await prepare_lesson(planner, **{**defaults, **kwargs})


def test_an_outage_answers_in_text_with_saved_languages_and_never_starts_a_video():
    result = fallback_preparation(
        message="make me a video about torque in Tamil",
        project_request="",
        text_language="Hindi",
        voice_language="Hindi",
    )
    assert result.action == "answer"
    assert (result.text_language, result.voice_language) == ("Hindi", "Hindi")
    assert result.planned is False


async def test_the_planners_language_choice_is_kept():
    result = await _plan(
        Planner(action="answer", text_language="Tamil", voice_language="Tamil"),
        message="இதை தமிழில் விளக்குங்கள்",
    )
    assert (result.text_language, result.voice_language) == ("Tamil", "Tamil")


async def test_an_unsupported_language_falls_back_to_the_defaults():
    result = await _plan(
        Planner(action="answer", text_language="Klingon", voice_language="Klingon"),
        account_text_language="Hindi",
        account_voice_language="Hindi",
    )
    assert (result.text_language, result.voice_language) == ("Hindi", "Hindi")


async def test_scene_targets_must_exist_in_the_video():
    planner = Planner(action="localize_video", target_scenes=["S02", "S09"], text_language="Tamil")
    result = await _plan(planner, video_scenes=SCENES, message="change only scene 2 to tamil")
    assert result.action == "localize_video"
    assert result.target_scenes == ["S02"]
    assert "id S02: scene 2, 30.0s to 61.0s" in planner.prompt


async def test_a_video_edit_without_a_finished_video_becomes_an_answer():
    result = await _plan(Planner(action="edit_video", target_scenes=["S01"]), video_scenes=[])
    assert result.action == "answer"


async def test_a_whole_paper_lecture_is_always_a_video_and_needs_a_paper():
    with_paper = await _plan(
        Planner(action="answer", lesson_scope="whole_paper"), attached_papers=["A2T"]
    )
    assert (with_paper.action, with_paper.lesson_scope) == ("generate_video", "whole_paper")
    without = await _plan(Planner(action="generate_video", lesson_scope="whole_paper"))
    assert without.lesson_scope == "topic"


async def test_fixing_one_scene_of_a_paper_lecture_stays_an_edit():
    # Seen live: "Scene 7 has h3,h2,h1 overlapping" in a paper chat became a new video.
    edit = {"op": "change", "scene": "S02", "instruction": "Separate the labels."}
    result = await _plan(
        Planner(
            action="edit_video", lesson_scope="whole_paper", target_scenes=["S02"], edits=[edit]
        ),
        attached_papers=["A2T"],
        video_scenes=SCENES,
    )
    assert result.action == "edit_video"
    assert [item.scene for item in result.edits] == ["S02"]


async def test_requested_duration_is_kept_within_the_limit():
    result = await _plan(Planner(action="generate_video", duration_minutes=90))
    assert result.duration_minutes == 30


async def test_the_chat_language_changes_only_to_a_supported_one_the_planner_named():
    asked = await _plan(Planner(action="answer", chat_language="tamil"))
    assert asked.chat_language == "Tamil"
    assert (await _plan(Planner(action="answer", chat_language="Klingon"))).chat_language == ""
    assert (await _plan(Planner(action="answer"))).chat_language == ""
