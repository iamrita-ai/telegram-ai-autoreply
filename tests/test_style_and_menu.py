"""House style, the command menu, and the multi-message prompt.

The tests here drive the **real** handlers: ``control.register`` is called
with a fake bot that records every handler it registers, and messages are
dispatched through the same patterns Telethon would use. Nothing about the
prompt flow is asserted from source text.
"""

from __future__ import annotations

import ast
import pathlib
import re
from unittest.mock import AsyncMock

import pytest
from telethon import events

from core import personas, style
from handlers import ai, control

# ── house style ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("it works \u2014 mostly", "it works, mostly"),
        ("wait \u2013 what", "wait, what"),
        ("10\u201420", "10-20"),
        ("state\u2014of\u2014the\u2014art", "state-of-the-art"),
        ("done \u2014 .", "done."),
        ("nothing to change here", "nothing to change here"),
    ],
)
def test_em_dashes_become_punctuation_people_type(raw: str, expected: str) -> None:
    assert style.strip_em_dashes(raw) == expected


def test_only_the_first_emoji_survives() -> None:
    assert style.limit_emoji("hey \U0001f604 how are u \U0001f525 doing \U0001f4af") == (
        "hey \U0001f604 how are u doing"
    )


def test_emoji_can_be_banned_entirely() -> None:
    assert style.limit_emoji("sure \u2705 thing \u2728", 0) == "sure thing"


def test_a_flag_a_family_and_a_keycap_each_count_once() -> None:
    assert style.count_emoji("\U0001f468\u200d\U0001f469\u200d\U0001f467") == 1
    assert style.count_emoji("\U0001f1ee\U0001f1f3") == 1
    assert style.count_emoji("1\ufe0f\u20e3") == 1


def test_panel_layout_characters_are_not_treated_as_emoji() -> None:
    """The control bot draws with these; stripping them would wreck it."""
    layout = "\u2501\u2501\u2501 \u00b7 | > \u2022"
    assert style.count_emoji(layout) == 0
    assert style.clean(layout) == layout


def test_the_ai_tidier_applies_the_house_style() -> None:
    """Every model reply goes through _tidy, so this is the choke point."""
    cleaned = ai._tidy("Sure \U0001f604 I can do that \u2014 later tonight \U0001f525\U0001f4af")
    assert "\u2014" not in cleaned
    assert style.count_emoji(cleaned) <= 1


@pytest.mark.parametrize("key", ["professional", "casual", "romantic"])
def test_every_persona_bans_dashes_and_rations_emoji(key: str) -> None:
    prompt = personas.get_persona(key).prompt
    assert "em dashes" in prompt
    assert "no emoji at all" in prompt


def _source_files() -> list[pathlib.Path]:
    return [
        path
        for path in pathlib.Path(".").rglob("*.py")
        if ".git" not in path.parts and "tests" not in path.parts and path.name != "style.py"
    ]


@pytest.mark.parametrize("path", _source_files(), ids=str)
def test_no_source_file_contains_an_em_dash(path: pathlib.Path) -> None:
    """Including comments and docs: the habit is what leaks into messages."""
    text = path.read_text(encoding="utf-8")
    assert "\u2014" not in text and "\u2013" not in text, f"{path} still contains a dash"


@pytest.mark.parametrize("name", ["handlers/control.py", "handlers/userbot.py", "main.py"])
def test_no_message_carries_more_than_one_emoji(name: str) -> None:
    """One emoji reads as a person. Three reads as an advert."""
    tree = ast.parse(pathlib.Path(name).read_text())
    docstrings = {
        node.body[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node not in docstrings
        ):
            assert style.count_emoji(node.value) <= 1, (
                f"{name}:{node.lineno} has {style.count_emoji(node.value)} emoji"
            )


# ── the command menu ────────────────────────────────────────────────────────


def test_every_menu_command_is_valid_for_telegram() -> None:
    seen = set()
    for name, description in control.COMMANDS + control.ADMIN_COMMANDS:
        assert re.fullmatch(r"[a-z0-9_]{1,32}", name), name
        assert 1 <= len(description) <= 256, name
        assert name not in seen, f"{name} listed twice"
        seen.add(name)


def test_every_menu_command_actually_exists() -> None:
    """A menu entry with no handler is a button that does nothing."""
    source = pathlib.Path("handlers/control.py").read_text()
    for name, _ in control.COMMANDS + control.ADMIN_COMMANDS:
        assert re.search(rf'pattern=r"\^/{name}\b', source), f"/{name} is in the menu but unhandled"


def test_the_important_commands_are_in_the_menu() -> None:
    names = {name for name, _ in control.COMMANDS}
    for required in ("start", "help", "login", "logout", "prompt", "clearcache", "persona"):
        assert required in names


async def test_publish_commands_registers_the_default_and_admin_scopes() -> None:
    from telethon.tl.types import BotCommandScopeDefault, BotCommandScopePeer

    sent = []

    class FakeBot:
        async def __call__(self, request):
            sent.append(request)

        async def get_input_entity(self, owner):
            return f"peer:{owner}"

    from config import settings

    original = settings.owner_ids
    object.__setattr__(settings, "owner_ids", (111,))
    try:
        await control.publish_commands(FakeBot())
    finally:
        object.__setattr__(settings, "owner_ids", original)

    assert len(sent) == 2
    assert isinstance(sent[0].scope, BotCommandScopeDefault)
    assert isinstance(sent[1].scope, BotCommandScopePeer)
    assert len(sent[1].commands) > len(sent[0].commands)  # admins see more
    assert {c.command for c in sent[0].commands} == {n for n, _ in control.COMMANDS}


async def test_a_failed_menu_publish_does_not_raise() -> None:
    """The bot must still start if Telegram rejects the command list."""

    class BrokenBot:
        async def __call__(self, request):
            raise RuntimeError("nope")

    await control.publish_commands(BrokenBot())  # must not raise


def test_commands_are_written_so_telegram_makes_them_tappable() -> None:
    """A command in code formatting is copyable but not clickable.

    Bare commands must be plain text. Forms that take an argument may stay
    in backticks, because those have to be edited before sending.
    """
    text = control.help_text()
    for command in ("/login", "/logout", "/status", "/persona", "/prompt", "/clearcache"):
        assert f"`{command}`" not in text
        assert command in text


def _callbacks(rows) -> set[str]:
    return {button.data.decode() for row in rows for button in row}


def test_the_menu_keyboard_changes_with_the_login_state() -> None:
    connected = _callbacks(control.main_menu(signed_in=True))
    disconnected = _callbacks(control.main_menu(signed_in=False))
    assert "menu:logout" in connected and "menu:login" not in connected
    assert "menu:login" in disconnected and "menu:logout" not in disconnected
    for both in ("menu:status", "menu:prompt", "menu:clear", "menu:help"):
        assert both in connected and both in disconnected


# ── driving the real handlers ───────────────────────────────────────────────


class FakeMongo:
    """A stand-in for the database, with just enough behaviour to be real."""

    def __init__(self) -> None:
        self.settings: dict[tuple[int, str], object] = {}
        self.login_state: dict[int, object] = {}
        self.registered: list[int] = []

    async def is_banned(self, owner):
        return False

    async def register_user(self, owner, **kwargs):
        self.registered.append(owner)

    async def get_user_setting(self, owner, key, default=None):
        return self.settings.get((owner, key), default)

    async def set_user_setting(self, owner, key, value):
        self.settings[(owner, key)] = value

    async def get_prompt(self, owner):
        return self.settings.get((owner, "prompt"))

    async def get_persona_key(self, owner):
        return "casual"

    async def is_locked(self, owner):
        return bool(self.settings.get((owner, "locked")))

    async def get_dnd(self, owner):
        return ""

    async def get_stat(self, owner, key):
        return 7

    async def get_today_count(self, owner):
        return 2

    async def load_session(self, owner):
        return ""

    async def get_login_state(self, owner):
        return self.login_state.get(owner)

    async def set_login_state(self, owner, state):
        self.login_state[owner] = state

    async def clear_all_history(self, owner):
        return 12

    async def clear_all_pending(self, owner):
        return 3

    async def delete_session(self, owner):
        return True


class FakeEvent:
    def __init__(self, text: str, sender_id: int = 42) -> None:
        self.text = text
        self.sender_id = sender_id
        self.chat_id = sender_id
        self.replies: list[str] = []
        self.pattern_match = None

    async def get_sender(self):
        return None

    async def respond(self, text, **kwargs):
        self.replies.append(text)


class FakeCallback:
    """A button press, as Telethon delivers it."""

    def __init__(self, data: bytes, sender_id: int = 42) -> None:
        self.data = data
        self.sender_id = sender_id
        self.chat_id = sender_id
        self.replies: list[str] = []
        self.answered = False
        self.pattern_match = None

    async def get_sender(self):
        return None

    async def answer(self, text=None, alert=False):
        self.answered = True

    async def respond(self, text, **kwargs):
        self.replies.append(text)


class Harness:
    """Registers the real handlers and dispatches messages the way Telethon does."""

    def __init__(self, monkeypatch) -> None:
        self.message_handlers: list[tuple[object, object]] = []
        self.mongo = FakeMongo()
        monkeypatch.setattr(control, "mongo", self.mongo)
        control.register(self, AsyncMock())

    # -- the bits control.register() uses of a bot ------------------------
    def on(self, event_builder):
        def decorator(handler):
            self.message_handlers.append((event_builder, handler))
            return handler

        return decorator

    async def click(self, data: str, sender_id: int = 42) -> FakeCallback:
        event = FakeCallback(data.encode(), sender_id)
        for builder, handler in self.message_handlers:
            if not isinstance(builder, events.CallbackQuery):
                continue
            match = builder.match(event.data)
            if match:
                event.pattern_match = match
                await handler(event)
        return event

    async def send(self, text: str, sender_id: int = 42) -> FakeEvent:
        event = FakeEvent(text, sender_id)
        for builder, handler in self.message_handlers:
            if not isinstance(builder, events.NewMessage):
                continue
            if builder.pattern is None:
                event.pattern_match = None
                await handler(event)
                continue
            match = builder.pattern(text)
            if match:
                event.pattern_match = match
                await handler(event)
        return event


@pytest.fixture
def bot(monkeypatch) -> Harness:
    return Harness(monkeypatch)


async def test_a_prompt_can_be_written_across_several_messages(bot: Harness) -> None:
    """The whole point: /prompt, keep talking, say done."""
    await bot.send("/prompt You are Serena, a smart, confident, witty bot.")
    await bot.send("Speak like a real Gen-Z internet user, ngl, tbh, fr.")
    await bot.send("Never be a yes-man. If the user is wrong, say so.")
    final = await bot.send("done")

    saved = bot.mongo.settings[(42, "prompt")]
    assert saved.startswith("You are Serena")
    assert "Gen-Z" in saved
    assert "yes-man" in saved
    assert saved.count("\n") == 2, "each message should be its own line, in order"
    assert bot.mongo.settings[(42, "prompt_draft")] is None
    assert "Prompt saved" in final.replies[-1]


async def test_the_draft_survives_until_it_is_finished(bot: Harness) -> None:
    await bot.send("/prompt part one")
    await bot.send("part two")
    assert (42, "prompt") not in bot.mongo.settings, "nothing is saved until you say done"
    assert bot.mongo.settings[(42, "prompt_draft")] == ["part one", "part two"]


async def test_slash_done_also_finishes_it(bot: Harness) -> None:
    await bot.send("/prompt hello")
    await bot.send("/done")
    assert bot.mongo.settings[(42, "prompt")] == "hello"


async def test_cancel_throws_the_draft_away(bot: Harness) -> None:
    await bot.send("/prompt something I regret")
    event = await bot.send("/cancel")
    assert bot.mongo.settings[(42, "prompt_draft")] is None
    assert (42, "prompt") not in bot.mongo.settings
    assert "discarded" in event.replies[-1].lower()


async def test_ordinary_chat_is_not_collected_as_a_prompt(bot: Harness) -> None:
    """No draft in progress means plain text does nothing at all."""
    event = await bot.send("just saying hello")
    assert event.replies == []
    assert (42, "prompt_draft") not in bot.mongo.settings


async def test_two_users_write_prompts_without_colliding(bot: Harness) -> None:
    await bot.send("/prompt mine", sender_id=1)
    await bot.send("/prompt yours", sender_id=2)
    await bot.send("and more of mine", sender_id=1)
    await bot.send("done", sender_id=1)
    await bot.send("done", sender_id=2)
    assert bot.mongo.settings[(1, "prompt")] == "mine\nand more of mine"
    assert bot.mongo.settings[(2, "prompt")] == "yours"


async def test_a_prompt_is_capped_rather_than_growing_forever(bot: Harness) -> None:
    await bot.send("/prompt " + "x" * (control.PROMPT_MAX_CHARS - 10))
    await bot.send("y" * 100)
    saved = bot.mongo.settings[(42, "prompt")]
    assert len(saved) == control.PROMPT_MAX_CHARS


async def test_clearcache_clears_all_three_stores(bot: Harness) -> None:
    event = await bot.send("/clearcache")
    body = event.replies[-1]
    assert "12" in body and "3" in body  # history and held fragments
    assert "counters" in body.lower()
    assert "untouched" in body.lower()  # settings and login are not touched


async def test_clear_cache_resets_the_rate_limiter(monkeypatch) -> None:
    """The in-memory counters are the third thing people mean by "cache"."""
    from core.safety import limiter_for

    monkeypatch.setattr(control, "mongo", FakeMongo())
    limiter = limiter_for(4242)
    limiter.record(1, text="hello")
    await control.clear_cache(4242)
    assert limiter_for(4242) is not limiter  # a fresh limiter, no history
    assert limiter_for(4242).snapshot()["replies_last_hour"] == 0


# ── the buttons do what they say ────────────────────────────────────────────


async def test_the_status_button_returns_the_status_panel(bot: Harness) -> None:
    event = await bot.click("menu:status")
    assert event.answered, "an unanswered callback leaves a spinner on the button"
    assert "Status" in event.replies[-1]


async def test_the_prompt_button_starts_a_draft(bot: Harness) -> None:
    event = await bot.click("menu:prompt")
    assert bot.mongo.settings[(42, "prompt_draft")] == []
    assert "done" in event.replies[-1].lower()
    # and the collector is now live
    await bot.send("written from the button")
    await bot.send("done")
    assert bot.mongo.settings[(42, "prompt")] == "written from the button"


async def test_the_save_button_finishes_a_draft(bot: Harness) -> None:
    await bot.send("/prompt half a personality")
    event = await bot.click("prompt:save")
    assert bot.mongo.settings[(42, "prompt")] == "half a personality"
    assert "Prompt saved" in event.replies[-1]


async def test_the_cancel_button_discards_a_draft(bot: Harness) -> None:
    await bot.send("/prompt regrettable")
    await bot.click("prompt:cancel")
    assert bot.mongo.settings[(42, "prompt_draft")] is None
    assert (42, "prompt") not in bot.mongo.settings


async def test_destructive_buttons_ask_first(bot: Harness) -> None:
    """One tap must never wipe anything."""
    clear = await bot.click("menu:clear")
    assert "?" in clear.replies[-1]
    logout = await bot.click("menu:logout")
    assert "?" in logout.replies[-1]

    confirmed = await bot.click("menu:clear_yes")
    assert "Cache cleared" in confirmed.replies[-1]


async def test_the_logout_button_signs_out_once_confirmed(bot: Harness) -> None:
    event = await bot.click("menu:logout_yes")
    assert "Signed out" in event.replies[-1]


async def test_pause_and_resume_buttons_flip_the_switch(bot: Harness) -> None:
    await bot.click("menu:pause")
    assert bot.mongo.settings[(42, "locked")] is True
    await bot.click("menu:resume")
    assert bot.mongo.settings[(42, "locked")] is False
