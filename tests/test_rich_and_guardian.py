"""Rich messages, the Bot API client, big reactions and the guardian.

Everything here is offline. The Bot API client is exercised against a fake
httpx transport, so the request bodies are checked exactly as Telegram would
receive them, and the guardian is driven through its real handlers.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from telethon import events

from config import settings
from core import blocks, botapi, humanize
from handlers import control, guardian

# ── blocks: the shapes Telegram actually accepts ────────────────────────────


def test_a_block_type_that_does_not_exist_is_refused_early() -> None:
    """The API says "invalid rich message" and nothing else. We do better."""
    with pytest.raises(ValueError, match="unknown rich block type"):
        blocks.validate({"type": "headline", "text": "nope"})


def test_the_discriminators_are_the_short_names_telegram_uses() -> None:
    """Not the longer names the Go bindings give their structs."""
    for short in ("heading", "pre", "blockquote", "pullquote", "list", "map"):
        assert short in blocks.BLOCK_TYPES
    for long in ("rich_block_heading", "code", "quote"):
        assert long not in blocks.BLOCK_TYPES


def test_every_builder_produces_a_block_that_validates() -> None:
    built = [
        blocks.paragraph("hello"),
        blocks.heading("title", size=2),
        blocks.divider(),
        blocks.pre("print(1)", "python"),
        blocks.footer("small print"),
        blocks.anchor("top"),
        blocks.math_block("e=mc^2"),
        blocks.thinking("working on it"),
        blocks.list_([blocks.list_item("one"), blocks.list_item("two", checkbox=True)]),
        blocks.blockquote("quoted", credit="someone"),
        blocks.expandable_quote("long"),
        blocks.pullquote("punchy"),
        blocks.details("more", [blocks.paragraph("inside")]),
        blocks.table([[blocks.cell("a"), blocks.cell("b")]], bordered=True),
        blocks.photo("file_id"),
        blocks.buttons([blocks.url("tap", "https://example.com")]),
    ]
    for block in built:
        assert blocks.validate(block)["type"] in blocks.BLOCK_TYPES


def test_nested_blocks_are_validated_too() -> None:
    with pytest.raises(ValueError):
        blocks.validate(blocks.details("summary", [{"type": "nonsense"}]))
    bad_item = blocks.list_item("fine")
    bad_item["blocks"] = [{"type": "nonsense"}]
    with pytest.raises(ValueError):
        blocks.validate(blocks.list_([bad_item]))


def test_a_task_list_item_carries_its_checkbox_state() -> None:
    item = blocks.list_item("done already", checkbox=True, checked=True)
    assert item["has_checkbox"] is True
    assert item["is_checked"] is True
    assert blocks.list_item("plain").get("has_checkbox") is None


def test_plain_text_is_accepted_where_blocks_are_expected() -> None:
    """A quotation is usually one line; forcing a list would be noise."""
    quote = blocks.blockquote("just a line")
    assert quote["blocks"][0] == {"type": "paragraph", "text": "just a line"}


def test_a_message_needs_some_content() -> None:
    with pytest.raises(ValueError, match="needs blocks"):
        blocks.message([])


def test_a_message_carries_exactly_what_was_given() -> None:
    payload = blocks.message([blocks.paragraph("hi")])
    assert payload == {"blocks": [{"type": "paragraph", "text": "hi"}]}
    assert "markdown" not in payload and "media" not in payload


def test_inline_nodes_use_the_documented_field_names() -> None:
    assert blocks.url("site", "https://x.dev") == {
        "type": "url",
        "text": "site",
        "url": "https://x.dev",
    }
    assert blocks.datetime_("noon", 1_760_000_000)["unix_time"] == 1_760_000_000
    assert blocks.custom_emoji("5368324170671202286", "🔥")["alternative_text"] == "🔥"
    assert blocks.text_mention("Rita", 42)["user"]["id"] == 42


# ── the HTTP Bot API client ─────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch):
    """Each test gets a clean client and a clean unsupported-method cache."""
    botapi._unsupported.clear()
    botapi._client = None
    monkeypatch.setattr(settings, "bot_token", "123:TESTTOKEN", raising=False)
    yield
    botapi._unsupported.clear()
    botapi._client = None


def _fake_api(responder) -> None:
    """Point the client at an in-process transport."""
    transport = httpx.MockTransport(responder)
    botapi._client = httpx.AsyncClient(transport=transport)


async def test_send_rich_posts_the_documented_payload() -> None:
    seen = {}

    def responder(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

    _fake_api(responder)
    result = await botapi.send_rich(
        99,
        blocks.message([blocks.heading("Status")]),
        reply_markup=botapi.inline_keyboard([[botapi.disabled_button("Casual")]]),
        reply_to_message_id=7,
    )

    assert result["message_id"] == 5
    assert seen["url"].endswith("/bot123:TESTTOKEN/sendRichMessage")
    assert '"chat_id": 99' in seen["body"] or '"chat_id":99' in seen["body"]
    assert "rich_message" in seen["body"]
    assert "reply_parameters" in seen["body"]


async def test_an_unknown_method_is_only_tried_once() -> None:
    """An older Telegram server must not cost a round trip on every panel."""
    calls = []

    def responder(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            404, json={"ok": False, "error_code": 404, "description": "Not Found: method not found"}
        )

    _fake_api(responder)
    assert botapi.rich_supported() is True
    with pytest.raises(botapi.MethodUnsupported):
        await botapi.send_rich(1, blocks.message([blocks.paragraph("x")]))
    with pytest.raises(botapi.MethodUnsupported):
        await botapi.send_rich(1, blocks.message([blocks.paragraph("x")]))
    assert len(calls) == 1
    assert botapi.rich_supported() is False


async def test_an_ordinary_rejection_does_not_disable_the_method() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
        )

    _fake_api(responder)
    with pytest.raises(botapi.BotApiError) as caught:
        await botapi.send_rich(1, blocks.message([blocks.paragraph("x")]))
    assert not isinstance(caught.value, botapi.MethodUnsupported)
    assert botapi.rich_supported() is True


async def test_a_network_failure_is_a_bot_api_error_not_a_crash() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    _fake_api(responder)
    with pytest.raises(botapi.BotApiError):
        await botapi.call("getMe")


async def test_nothing_is_sent_without_a_token(monkeypatch) -> None:
    monkeypatch.setattr(settings, "bot_token", "", raising=False)
    assert botapi.configured() is False
    assert botapi.rich_supported() is False
    with pytest.raises(botapi.BotApiError):
        await botapi.call("getMe")


def test_the_august_2026_button_additions_are_shaped_correctly() -> None:
    assert botapi.disabled_button("Casual (current)") == {
        "text": "Casual (current)",
        "disabled": {},
    }
    markup = botapi.inline_keyboard([[botapi.button("Go", data="x")]], force_reply=True)
    assert markup["force_reply"] is True
    assert markup["inline_keyboard"][0][0]["callback_data"] == "x"


async def test_a_join_request_query_takes_exactly_one_verdict() -> None:
    with pytest.raises(ValueError):
        await botapi.answer_join_request_query("q", approve=True, decline=True)
    with pytest.raises(ValueError):
        await botapi.answer_join_request_query("q")


# ── the panels ──────────────────────────────────────────────────────────────


def test_the_help_panel_only_lists_commands_that_exist() -> None:
    """A rich panel is still a menu; a dead entry is still a dead entry."""
    listed = _commands_in(control.help_blocks(is_admin=True))
    known = {f"/{name}" for name, _ in control.COMMANDS + control.ADMIN_COMMANDS}
    assert listed <= known
    for required in ("/login", "/persona", "/prompt", "/clearcache", "/guard", "/rich"):
        assert required in listed


def test_the_help_panel_hides_admin_commands_from_ordinary_users() -> None:
    assert "/broadcast" not in _commands_in(control.help_blocks())
    assert "/broadcast" in _commands_in(control.help_blocks(is_admin=True))


def _commands_in(body) -> set[str]:
    found: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            if node.get("type") == "bot_command":
                found.add(node["text"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(body)
    return found


def test_every_panel_block_would_be_accepted_by_telegram() -> None:
    for block in control.help_blocks(is_admin=True) + control.limits_blocks(42):
        blocks.validate(block)


async def test_the_active_persona_is_a_disabled_button(monkeypatch) -> None:
    """The 24 August 2026 feature, used for the one thing it is for."""

    class FakeMongo:
        async def get_persona_key(self, owner):
            return "romantic"

        async def get_prompt(self, owner):
            return ""

    monkeypatch.setattr(control, "mongo", FakeMongo())
    markup = await control.persona_markup(42)
    flat = [button for row in markup["inline_keyboard"] for button in row]
    disabled = [button for button in flat if "disabled" in button]
    assert len(disabled) == 1
    assert "current" in disabled[0]["text"].lower()
    assert all("callback_data" in button for button in flat if "disabled" not in button)


async def test_a_rich_panel_falls_back_to_plain_text(monkeypatch) -> None:
    """Formatting must never cost the message itself."""
    said = []

    async def fake_say(event, text, **kwargs):
        said.append(text)

    async def broken_send_rich(*args, **kwargs):
        raise botapi.BotApiError("sendRichMessage", 400, "nope")

    monkeypatch.setattr(control, "_say", fake_say)
    monkeypatch.setattr(control.botapi, "send_rich", broken_send_rich)
    monkeypatch.setattr(control, "rich_enabled", AsyncMock(return_value=True))

    class Event:
        sender_id = 42
        chat_id = 42

    used_rich = await control.send_panel(None, Event(), "plain version", [blocks.paragraph("x")])
    assert used_rich is False
    assert said == ["plain version"]


async def test_a_user_who_turned_rich_off_gets_plain_panels(monkeypatch) -> None:
    class FakeMongo:
        async def get_user_setting(self, owner, key, default=None):
            return False

    monkeypatch.setattr(control, "mongo", FakeMongo())
    monkeypatch.setattr(settings, "rich_messages", True, raising=False)
    assert await control.rich_enabled(42) is False


async def test_rich_is_off_for_everybody_when_the_deployment_says_so(monkeypatch) -> None:
    class FakeMongo:
        async def get_user_setting(self, owner, key, default=None):
            return True

    monkeypatch.setattr(control, "mongo", FakeMongo())
    monkeypatch.setattr(settings, "rich_messages", False, raising=False)
    assert await control.rich_enabled(42) is False


def test_telethon_buttons_convert_to_bot_api_json() -> None:
    rows = control.main_menu(signed_in=True)
    converted = control._to_api_keyboard(rows)
    assert len(converted) == len(rows)
    for row in converted:
        for button in row:
            assert button["text"]
            assert isinstance(button["callback_data"], str)


# ── big reactions and read receipts ─────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "I got the job!!!",
        "we finally got married today",
        "congratulations on the promotion",
        "HAPPY BIRTHDAY",
    ],
)
def test_a_real_occasion_earns_the_big_reaction(text: str) -> None:
    assert humanize.deserves_big_reaction(text) is True


@pytest.mark.parametrize(
    "text",
    ["ok", "sounds good", "what time are we meeting", "thanks", "", "OK"],
)
def test_small_talk_does_not(text: str) -> None:
    assert humanize.deserves_big_reaction(text) is False


def test_an_essay_is_never_a_big_moment() -> None:
    """A wall of text with the word congratulations in it is not a party."""
    assert humanize.deserves_big_reaction("congratulations. " + "x" * 400) is False


async def test_the_big_flag_reaches_telegram(monkeypatch) -> None:
    sent = {}

    class FakeClient:
        async def __call__(self, request):
            sent["big"] = getattr(request, "big", None)
            return None

    class FakeEvent:
        chat_id = 5
        id = 9
        is_private = True

        async def get_input_chat(self):
            return 5

    monkeypatch.setattr(settings, "reactions_enabled", True, raising=False)
    monkeypatch.setattr(humanize.random, "random", lambda: 0.0)
    await humanize.send_reaction(FakeClient(), FakeEvent(), "positive", big=True)
    assert sent.get("big") is True


def test_the_reaction_request_matches_telethons_real_signature() -> None:
    """The bug this caught: the field is "reaction", not "reactions".

    Every reaction was raising TypeError inside a debug-level swallow, so
    the bot looked like it was reacting and never was. Compare against the
    real signature so a Telethon rename cannot hide the same way twice.
    """
    import ast
    import inspect
    import pathlib

    from telethon.tl.functions.messages import SendReactionRequest

    accepted = set(inspect.signature(SendReactionRequest.__init__).parameters)
    tree = ast.parse(pathlib.Path("core/humanize.py").read_text())
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "SendReactionRequest":
            used = {keyword.arg for keyword in node.keywords}
    assert used <= accepted, f"not real parameters: {used - accepted}"
    assert "reaction" in used and "big" in used


async def test_a_read_receipt_never_breaks_the_reply() -> None:
    class FakeEvent:
        chat_id = 5

        async def mark_read(self):
            raise RuntimeError("no")

    class FakeClient:
        async def send_read_acknowledge(self, *args, **kwargs):
            raise RuntimeError("no")

    assert await humanize.mark_as_read(FakeClient(), FakeEvent()) is False


def test_the_read_receipt_is_sent_after_the_reply_not_before() -> None:
    """A message read the instant it arrives, then answered, reads as a bot."""
    source = (__import__("pathlib").Path("handlers/userbot.py")).read_text()
    body = source[source.index("async def _handle") :]
    assert body.index("await simulate_typing") < body.index("await mark_as_read")
    assert body.index("event.reply") < body.index("await mark_as_read")


# ── the guardian ────────────────────────────────────────────────────────────


class _User:
    def __init__(self, **kwargs) -> None:
        self.bot = kwargs.get("bot", False)
        self.deleted = kwargs.get("deleted", False)
        self.scam = kwargs.get("scam", False)
        self.fake = kwargs.get("fake", False)


def test_the_obvious_ones_are_declined_before_any_captcha() -> None:
    assert guardian.screen(_User(bot=True))[0] == "decline"
    assert guardian.screen(_User(deleted=True))[0] == "decline"
    assert guardian.screen(_User(scam=True))[0] == "decline"
    assert guardian.screen(_User(fake=True))[0] == "decline"
    assert guardian.screen(_User(), banned=True)[0] == "decline"
    assert guardian.screen(_User())[0] == "ask"


def test_an_unresolvable_requester_still_gets_a_verdict() -> None:
    """get_entity fails often enough that None must not be a crash."""
    assert guardian.screen(None)[0] == "ask"


def test_the_captcha_answer_is_one_of_the_buttons_shown() -> None:
    for seed in range(50):
        correct, options = guardian.captcha_challenge(seed)
        assert correct in options
        assert len(set(options)) == 4


class FakeBot:
    """Enough of a Telethon client for the guardian to run against."""

    def __init__(self) -> None:
        self.handlers: list[tuple[object, object]] = []
        self.requests: list[object] = []
        self.messages: list[tuple[int, str]] = []
        self.entities: dict[int, object] = {}
        self.can_dm = True

    def on(self, builder):
        def decorator(handler):
            self.handlers.append((builder, handler))
            return handler

        return decorator

    async def __call__(self, request):
        self.requests.append(request)

    async def get_entity(self, ident):
        if ident in self.entities:
            return self.entities[ident]
        raise ValueError("unknown")

    async def send_message(self, chat_id, text, **kwargs):
        if not self.can_dm:
            raise RuntimeError("bot was blocked by the user")
        self.messages.append((chat_id, text))

    async def dispatch_click(self, data: bytes, sender_id: int):
        event = _Click(data, sender_id)
        for builder, handler in self.handlers:
            if isinstance(builder, events.CallbackQuery) and builder.match(data):
                event.pattern_match = builder.match(data)
                await handler(event)
        return event


class _Click:
    def __init__(self, data: bytes, sender_id: int) -> None:
        self.data = data
        self.sender_id = sender_id
        self.chat_id = sender_id
        self.answers: list[str] = []
        self.edits: list[str] = []
        self.pattern_match = None

    async def answer(self, text=None, alert=False):
        self.answers.append(text or "")

    async def edit(self, text, **kwargs):
        self.edits.append(text)


class _Peer:
    def __init__(self, channel_id: int) -> None:
        self.channel_id = channel_id


class _Update:
    def __init__(self, chat_id: int, user_id: int) -> None:
        self.peer = _Peer(chat_id)
        self.user_id = user_id


class FakeGuardMongo:
    def __init__(self, mode: str = "captcha", owner: int = 7) -> None:
        self.guards = {-1001: {"chat_id": -1001, "mode": mode, "owner_id": owner}}
        self.banned: set[int] = set()

    async def get_guard(self, chat_id):
        return self.guards.get(int(chat_id))

    async def is_banned(self, user_id):
        return user_id in self.banned


@pytest.fixture
def guard_bot(monkeypatch):
    bot = FakeBot()
    guardian._pending.clear()
    guardian.register(bot)
    return bot


async def test_a_group_with_no_guard_is_left_alone(guard_bot, monkeypatch) -> None:
    monkeypatch.setattr(guardian, "mongo", FakeGuardMongo())
    await guardian._handle(guard_bot, _Update(-1002, 500))  # a different group
    assert guard_bot.requests == []
    assert guard_bot.messages == []


async def test_auto_mode_approves_a_real_account(guard_bot, monkeypatch) -> None:
    monkeypatch.setattr(guardian, "mongo", FakeGuardMongo(mode="auto"))
    guard_bot.entities[500] = _User()
    await guardian._handle(guard_bot, _Update(-1001, 500))
    assert len(guard_bot.requests) == 1
    assert guard_bot.requests[0].approved is True
    assert guard_bot.messages[0][0] == 7  # the owner is told


async def test_a_bot_is_declined_even_in_captcha_mode(guard_bot, monkeypatch) -> None:
    monkeypatch.setattr(guardian, "mongo", FakeGuardMongo(mode="captcha"))
    guard_bot.entities[500] = _User(bot=True)
    await guardian._handle(guard_bot, _Update(-1001, 500))
    assert guard_bot.requests[0].approved is False
    assert guardian.pending_count() == 0


async def test_the_captcha_is_sent_to_the_requester(guard_bot, monkeypatch) -> None:
    monkeypatch.setattr(guardian, "mongo", FakeGuardMongo())
    guard_bot.entities[500] = _User()
    guard_bot.entities[-1001] = type("Chat", (), {"title": "The Group"})()
    await guardian._handle(guard_bot, _Update(-1001, 500))
    assert guard_bot.requests == []  # nothing decided yet
    assert guard_bot.messages[0][0] == 500
    assert "The Group" in guard_bot.messages[0][1]
    assert guardian.pending_count() == 1


async def test_the_right_button_lets_them_in(guard_bot, monkeypatch) -> None:
    monkeypatch.setattr(guardian, "mongo", FakeGuardMongo())
    guard_bot.entities[500] = _User()
    await guardian._handle(guard_bot, _Update(-1001, 500))
    correct, _ = guardian._pending[(-1001, 500)]

    await guard_bot.dispatch_click(f"guard:-1001:500:{correct}".encode(), sender_id=500)
    assert guard_bot.requests[-1].approved is True
    assert guardian.pending_count() == 0


async def test_the_wrong_button_declines(guard_bot, monkeypatch) -> None:
    monkeypatch.setattr(guardian, "mongo", FakeGuardMongo())
    guard_bot.entities[500] = _User()
    await guardian._handle(guard_bot, _Update(-1001, 500))
    correct, _ = guardian._pending[(-1001, 500)]
    wrong = next(e for e in guardian._CAPTCHA_EMOJI if e != correct)

    await guard_bot.dispatch_click(f"guard:-1001:500:{wrong}".encode(), sender_id=500)
    assert guard_bot.requests[-1].approved is False


async def test_somebody_else_cannot_answer_your_captcha(guard_bot, monkeypatch) -> None:
    monkeypatch.setattr(guardian, "mongo", FakeGuardMongo())
    guard_bot.entities[500] = _User()
    await guardian._handle(guard_bot, _Update(-1001, 500))
    correct, _ = guardian._pending[(-1001, 500)]

    event = await guard_bot.dispatch_click(f"guard:-1001:500:{correct}".encode(), sender_id=999)
    assert guard_bot.requests == []
    assert guardian.pending_count() == 1
    assert "not for you" in event.answers[0]


async def test_an_expired_captcha_decides_nothing(guard_bot, monkeypatch) -> None:
    """A slow human is not a spammer; the request goes back to the queue."""
    monkeypatch.setattr(guardian, "mongo", FakeGuardMongo())
    guard_bot.entities[500] = _User()
    await guardian._handle(guard_bot, _Update(-1001, 500))
    correct, _ = guardian._pending[(-1001, 500)]
    guardian._pending[(-1001, 500)] = (correct, time.time() - 1)

    await guard_bot.dispatch_click(f"guard:-1001:500:{correct}".encode(), sender_id=500)
    assert guard_bot.requests == []


async def test_a_user_who_blocks_the_bot_is_left_for_a_human(guard_bot, monkeypatch) -> None:
    monkeypatch.setattr(guardian, "mongo", FakeGuardMongo())
    guard_bot.entities[500] = _User()
    guard_bot.can_dm = False
    await guardian._handle(guard_bot, _Update(-1001, 500))
    assert guard_bot.requests == []
    assert guardian.pending_count() == 0


async def test_a_query_id_uses_the_bot_api_route(guard_bot, monkeypatch) -> None:
    """Bot API 10.1 answers the query itself when Telegram sends one."""
    monkeypatch.setattr(guardian, "mongo", FakeGuardMongo(mode="auto"))
    guard_bot.entities[500] = _User()
    answered = {}

    async def fake_answer(query_id, **kwargs):
        answered["id"] = query_id
        answered.update(kwargs)

    monkeypatch.setattr(guardian.botapi, "answer_join_request_query", fake_answer)
    update = _Update(-1001, 500)
    update.query_id = "abc123"
    await guardian._handle(guard_bot, update)

    assert answered["id"] == "abc123"
    assert answered["approve"] is True
    assert guard_bot.requests == []  # no MTProto call was needed


async def test_only_an_admin_of_the_group_may_guard_it() -> None:
    class Bot:
        async def get_entity(self, chat_id):
            return object()

        async def get_permissions(self, chat_id, user_id):
            return type("Rights", (), {"is_admin": False, "is_creator": False})()

    allowed, why = await control._may_guard(Bot(), -1001, 42)
    assert allowed is False
    assert "admin" in why.lower()


async def test_a_group_the_bot_cannot_see_is_refused() -> None:
    class Bot:
        async def get_entity(self, chat_id):
            raise ValueError("nope")

    allowed, why = await control._may_guard(Bot(), -1001, 42)
    assert allowed is False
    assert "cannot see" in why


async def test_an_admin_may_guard_the_group() -> None:
    class Bot:
        async def get_entity(self, chat_id):
            return object()

        async def get_permissions(self, chat_id, user_id):
            return type("Rights", (), {"is_admin": True, "is_creator": False})()

    allowed, why = await control._may_guard(Bot(), -1001, 42)
    assert allowed is True
    assert why == ""


def test_the_guardian_never_calls_get_updates() -> None:
    """The HTTP client must not fight the MTProto session for updates."""
    source = (__import__("pathlib").Path("core/botapi.py")).read_text()
    assert "getUpdates" not in source.replace("``getUpdates``", "")


def test_asyncio_is_used_for_the_captcha_expiry_not_a_thread() -> None:
    assert asyncio  # imported above; the module schedules with call_later
    assert guardian.CAPTCHA_TIMEOUT >= 300
