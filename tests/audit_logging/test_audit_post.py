"""The #bot-log post of an audit entry: long details are split over several fields within Discord's limits (the
database keeps everything), sanitized like every entry."""

from __future__ import annotations

from shopbot.core.audit import DETAIL_FIELD_TEXT, MAX_DETAIL_FIELDS, REDACTED, detail_chunks
from tests.audit_logging.util import audit_events
from tests.fakes import FakeTextChannel
from tests.helpers import TestEnv, provision_channels

# Token-shaped (built at runtime so the repository secret scan does not flag a fake value).
TOKEN = "A" * 24 + "." + "B" * 6 + "." + "C" * 38


def test_short_details_fit_one_field() -> None:
    assert detail_chunks({"a": 1}) == ['{"a": 1}']


def test_long_details_are_split_and_capped() -> None:
    details = {f"k{index}": "v" * 250 for index in range(30)}  # ~7900 characters of JSON
    chunks = detail_chunks(details)
    assert len(chunks) == MAX_DETAIL_FIELDS
    assert all(len(chunk) <= DETAIL_FIELD_TEXT for chunk in chunks)
    assert chunks[-1].endswith("…")
    medium = detail_chunks({f"k{index}": "v" * 250 for index in range(6)})  # ~1600 characters: two full pieces
    assert len(medium) == 2 and not medium[-1].endswith("…")


async def test_a_long_entry_is_posted_within_discord_limits(tenv: TestEnv) -> None:
    await provision_channels(tenv)
    details = {"channels": [f"channel_{index}: updated (name, topic, overwrites)" for index in range(40)]}
    details["note"] = f"see {TOKEN}"  # type: ignore[assignment]
    await tenv.app.audit.record("setup.run", actor_id=1, target_type="guild", target_id=1, details=details)
    channel = tenv.app.resources.text_channel(tenv.guild, "audit_log")  # type: ignore[arg-type]
    assert isinstance(channel, FakeTextChannel)
    [posted] = channel.messages
    embed = posted.embeds[0]
    detail_fields = [field for field in embed.fields if field.name == tenv.app.op("audit.details")]
    assert len(detail_fields) >= 2
    assert all(len(field.value or "") <= 1024 for field in embed.fields)
    assert len(embed) <= 6000
    assert TOKEN not in posted.text()
    [event] = await audit_events(tenv.app, "setup.run")
    assert len(event.details["channels"]) == 40  # the database keeps every entry
    assert TOKEN not in str(event.details) and REDACTED in event.details["note"]
