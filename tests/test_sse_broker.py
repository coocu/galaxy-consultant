import asyncio
import threading
import time

from app.main import StoreEventBroker, _format_sse


def test_store_event_broker_publishes_only_to_matching_store():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        broker = StoreEventBroker()
        broker.set_loop(loop)
        matching = broker.subscribe(2)
        other = broker.subscribe(3)

        broker.publish(2, {"type": "ticket_created", "ticket": {"ticket_number": 7}})
        time.sleep(0.05)

        received = asyncio.run_coroutine_threadsafe(matching.get(), loop).result(timeout=1)
        assert received["ticket"]["ticket_number"] == 7
        assert other.empty()

        broker.unsubscribe(2, matching)
        broker.unsubscribe(3, other)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=1)
        loop.close()


def test_sse_format_uses_event_and_json_data_lines():
    message = _format_sse("ticket_created", {"type": "ticket_created", "ticket_number": 1})

    assert message.startswith("event: ticket_created\n")
    assert '"ticket_number": 1' in message
    assert message.endswith("\n\n")
